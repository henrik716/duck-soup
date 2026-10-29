"""The engine worker (duck_soup/worker.py): a crash in the engine's process must not take the
server down, must be reported as such, and must leave a working engine behind."""
import json

import pytest
from fastapi.testclient import TestClient

from duck_soup.web.app import app
from duck_soup.worker import EngineCrashed, EngineTaskError, EngineWorker

# DuckDB spatial aborts the process on this (ST_LineLocatePoint on a line with Z).
_CRASHING_EXPR = (
    "ST_LineLocatePoint(ST_GeomFromText('LINESTRING Z (0 0 0, 10 0 5)'), ST_Point(5, 0))"
)


def _config(tmp_path, expr: str = "upper(name)") -> dict:
    path = tmp_path / "pts.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": "a"},
         "geometry": {"type": "Point", "coordinates": [500000, 6600000]}},
    ]}))
    return {"name": "t", "output": str(tmp_path / "out.gpkg"), "pipelines": [{
        "name": "t", "working_crs": "EPSG:25833", "base": "pts", "steps": [],
        "sources": [{"id": "pts", "format": "geojson", "uri": str(path), "crs": "EPSG:25833"}],
        "mapping": [{"to": "v", "expr": expr}], "layers": [{"layer": "out"}],
    }]}


def test_dead_worker_raises_engine_crashed_and_is_replaced(tmp_path):
    worker = EngineWorker()
    try:
        cfg = _config(tmp_path)
        # Kill the worker as soon as it reports progress, i.e. in the middle of the call.
        with pytest.raises(EngineCrashed) as info:
            worker.call("preview", cfg, log=lambda line: worker._proc.kill(), limit=5)
        assert info.value.log and info.value.log[0] in str(info.value)
        assert "crashed" in str(info.value)

        assert [r["v"] for r in worker.call("preview", cfg, limit=5)] == ["A"]
    finally:
        worker.close()


def test_task_errors_carry_the_workers_traceback(tmp_path):
    worker = EngineWorker()
    try:
        with pytest.raises(EngineTaskError) as info:
            worker.call("preview", _config(tmp_path, expr="no_such_function(name)"), limit=5)
        assert "no_such_function" in str(info.value)
        assert "duck_soup" in info.value.trace and "worker.py" in info.value.trace
    finally:
        worker.close()


def test_server_survives_a_native_crash_in_duckdb(tmp_path):
    client = TestClient(app)
    body = client.post("/api/preview", json={"config": _config(tmp_path, _CRASHING_EXPR), "limit": 5}).json()
    if body["ok"]:
        pytest.skip("this DuckDB no longer crashes on ST_LineLocatePoint with Z: pick a new trigger")
    assert body["crashed"] is True and "crashed" in body["error"]

    # Same server, fresh engine.
    body = client.post("/api/preview", json={"config": _config(tmp_path), "limit": 5}).json()
    assert body["ok"] is True and [r["v"] for r in body["rows"]] == ["A"]

    run = client.post("/api/run", json={"config": _config(tmp_path, _CRASHING_EXPR), "name": "t"}).json()
    assert run["ok"] is False and run["crashed"] is True
    assert run["log"]  # the progress made before the crash is kept
    runs = client.get("/api/runs/t").json()["runs"]
    assert runs[0]["ok"] is False and "crashed" in runs[0]["error"]


def test_run_downloads_in_the_server_and_the_worker_reuses_them(tmp_path, monkeypatch):
    import duck_soup.sources as sources_mod

    monkeypatch.setattr(sources_mod.settings, "REMOTE_CACHE_DIR", tmp_path / "remote")
    calls = []

    class _Resp:
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def raise_for_status(self): pass
        def iter_content(self, chunk_size=1):
            yield b"id,name\n1,A\n"

    # Only the server's requests is faked: a download in the worker would go out for real.
    monkeypatch.setattr("requests.get", lambda url, **kw: calls.append(url) or _Resp())
    src = {"id": "r", "format": "csv", "uri": "https://example.com/r.csv", "geometry": False}
    cfg = {"name": "t", "output": str(tmp_path / "out.gpkg"), "pipelines": [{
        "name": "t", "sources": [src], "base": "r", "steps": [],
        "mapping": [{"to": "name", "from": "name"}], "layers": [{"layer": "out"}],
    }]}
    body = TestClient(app).post("/api/run", json={"config": cfg, "name": "t"}).json()
    assert body["ok"] is True, body.get("error")
    assert calls == ["https://example.com/r.csv"]
    assert [layer["rows"] for layer in body["layers"]] == [1]


def _post_in_background(client, url, body):
    import threading

    out = {}
    thread = threading.Thread(target=lambda: out.update(client.post(url, json=body).json()))
    thread.start()
    return thread, out


def _wait_until(cond, timeout=30.0):
    import time

    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.05)


def test_cancel_stops_a_run_in_progress(tmp_path):
    import time

    from duck_soup.worker import RUNNER

    client = TestClient(app)
    # A mapping that keeps DuckDB busy for far longer than the test waits.
    slow = "(SELECT sum(a.range * b.range) FROM range(100000000) a, range(100000) b)"
    thread, body = _post_in_background(client, "/api/run", {"config": _config(tmp_path, slow), "name": "t"})
    _wait_until(lambda: RUNNER._busy)
    started = time.monotonic()
    assert client.post("/api/run/cancel").json()["cancelled"] == 1
    thread.join(30)
    assert time.monotonic() - started < 10
    assert body["ok"] is False and body["cancelled"] is True and body["crashed"] is False
    assert body["error"].startswith("Cancelled.")
    assert client.get("/api/runs/t").json()["runs"][0]["error"].startswith("Cancelled.")

    # The next run gets a new worker.
    body = client.post("/api/run", json={"config": _config(tmp_path), "name": "t"}).json()
    assert body["ok"] is True, body.get("error")


def test_cancel_during_downloads_answers_at_once(tmp_path, monkeypatch):
    import threading
    import time

    import duck_soup.sources as sources_mod

    monkeypatch.setattr(sources_mod.settings, "REMOTE_CACHE_DIR", tmp_path / "remote")
    release = threading.Event()

    class _Resp:
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def raise_for_status(self): pass
        def iter_content(self, chunk_size=1):
            release.wait(30)
            yield b"id,name\n1,A\n"

    monkeypatch.setattr("requests.get", lambda url, **kw: _Resp())
    src = {"id": "r", "format": "csv", "uri": "https://example.com/slow-run.csv", "geometry": False}
    cfg = {"name": "t", "output": str(tmp_path / "out.gpkg"), "pipelines": [{
        "name": "t", "sources": [src], "base": "r", "steps": [],
        "mapping": [{"to": "name", "from": "name"}], "layers": [{"layer": "out"}],
    }]}
    # Called directly rather than through TestClient, which shuts down each request's event
    # loop afterwards and so waits for the download thread; uvicorn's loop keeps running.
    import asyncio

    import duck_soup.web.app as web_app

    async def main():
        job = asyncio.ensure_future(web_app.run(web_app.RunRequest(config=cfg, name="t")))
        try:
            while sources_mod.source_progress(src["uri"]) is None:
                await asyncio.sleep(0.05)
            started = time.monotonic()
            web_app.cancel_run()
            body = await asyncio.wait_for(job, 5)
            return body, time.monotonic() - started
        finally:
            release.set()

    body, elapsed = asyncio.run(main())
    assert elapsed < 2
    assert body["cancelled"] is True and body["error"] == "Cancelled."


_ENDLESS = "(SELECT count(*) FROM range(100000000) a, range(100000) b WHERE a.range + b.range > 0)"


def test_interrupt_stops_a_tagged_call_and_keeps_the_worker(tmp_path):
    import threading
    import time

    from duck_soup.worker import EngineSuperseded

    worker = EngineWorker()
    try:
        assert [r["v"] for r in worker.call("preview", _config(tmp_path), limit=5)] == ["A"]
        pid = worker._proc.pid
        threading.Timer(1.5, lambda: worker.interrupt(("preview", "tab"))).start()
        started = time.monotonic()
        with pytest.raises(EngineSuperseded):
            worker.call("preview", _config(tmp_path, _ENDLESS), limit=5, tag=("preview", "tab"))
        assert time.monotonic() - started < 6
        assert worker._proc.pid == pid  # interrupted, not restarted
        assert not worker.interrupt(("preview", "tab"))  # nothing running any more

        # A call already stale once the worker is free doesn't run at all.
        with pytest.raises(EngineSuperseded):
            worker.call("preview", _config(tmp_path, _ENDLESS), limit=5, stale=lambda: True)
        assert [r["v"] for r in worker.call("preview", _config(tmp_path), limit=5)] == ["A"]
    finally:
        worker.close()


def test_a_newer_preview_from_the_same_tab_drops_the_older_ones(tmp_path):
    import time

    client = TestClient(app)
    slow = {"config": _config(tmp_path, _ENDLESS), "limit": 5, "client": "tab-1"}
    started = time.monotonic()
    first, a = _post_in_background(client, "/api/preview", slow)
    time.sleep(1.5)  # running by now
    second, b = _post_in_background(client, "/api/preview", slow)
    time.sleep(0.5)  # waiting behind the first
    body = client.post("/api/preview", json={"config": _config(tmp_path), "limit": 5, "client": "tab-1"}).json()
    first.join(10)
    second.join(10)
    assert a["stale"] is True and b["stale"] is True
    assert body["ok"] is True and [r["v"] for r in body["rows"]] == ["A"]
    assert time.monotonic() - started < 15


def test_progress_is_tracked_where_duckdb_starts_with_it_off():
    """Linux without a terminal (CI, a server run as a service) starts DuckDB with its
    progress bar off, and query_progress then never reports. load_extensions turns it on,
    without printing it."""
    import duckdb

    from duck_soup.derive import load_extensions

    class StartsOff:  # a connection as DuckDB opens it there
        def __init__(self):
            self.con = duckdb.connect()
            self.con.execute("SET enable_progress_bar = false")

        def execute(self, sql):
            return self.con.execute(sql)

    off = StartsOff()
    load_extensions(off)
    assert off.con.execute(
        "SELECT current_setting('enable_progress_bar'), current_setting('enable_progress_bar_print')"
    ).fetchone() == (True, False)


def test_a_slow_preview_reports_its_progress(tmp_path):
    from duck_soup.worker import ENGINE

    client = TestClient(app)
    thread, body = _post_in_background(
        client, "/api/preview", {"config": _config(tmp_path, _ENDLESS), "limit": 5, "client": "tab-p"},
    )
    try:
        # DuckDB only reports a query's progress once it has run for a moment (2 s).
        _wait_until(lambda: client.get("/api/progress", params={"client": "tab-p"}).json()["preview"] is not None, 20)
        got = client.get("/api/progress", params={"client": "tab-p"}).json()
        assert 0 <= got["preview"] <= 100 and got["run"] is None
        # Another tab's preview isn't this one.
        assert client.get("/api/progress", params={"client": "tab-q"}).json()["preview"] is None
    finally:
        ENGINE.interrupt(("preview", "tab-p"))
        thread.join(10)
    assert body["stale"] is True
    assert client.get("/api/progress", params={"client": "tab-p"}).json()["preview"] is None


def test_an_urgent_call_goes_before_the_waiting_ones():
    # A preview mustn't queue behind a just-loaded config's inspects (EngineWorker._turn).
    import threading
    import time

    worker = EngineWorker()
    order: list[str] = []

    def wait_turn(name: str, urgent: bool) -> threading.Thread:
        def go() -> None:
            with worker._turn(urgent):
                order.append(name)
        t = threading.Thread(target=go)
        t.start()
        time.sleep(0.1)  # waiting by now
        return t

    with worker._turn():  # a call running
        waiting = [wait_turn("inspect 1", False), wait_turn("inspect 2", False), wait_turn("preview", True)]
    for t in waiting:
        t.join(5)
    assert order[0] == "preview"
    assert sorted(order[1:]) == ["inspect 1", "inspect 2"]
