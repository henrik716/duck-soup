"""The http_lookup step: one GET per distinct URL, cached on disk, and previews that never
wait on the network in the engine (see duck_soup/http_lookup.py)."""
import json
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import duckdb
import pytest

from duck_soup import http_lookup
from duck_soup.config import Config, HttpLookup
from duck_soup.engine import Engine, run_config
from duck_soup.sources import settings


class _Api:
    """A local address-search API: /sok?sok=<text> answers {"adresser": [...]}, with one hit
    unless the text is "nowhere"; /fail answers 500. Records every request it got."""

    def __init__(self):
        self.requests: list[str] = []
        self.headers: list[dict] = []
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                api.requests.append(self.path)
                api.headers.append(dict(self.headers))
                url = urlparse(self.path)
                if url.path == "/fail":
                    self.send_response(500)
                    self.end_headers()
                    self.wfile.write(b"boom")
                    return
                if url.path == "/html":
                    body = b"<html>not json</html>"
                else:
                    text = parse_qs(url.query).get("sok", [""])[0]
                    hits = [] if text == "nowhere" else [
                        {"adressetekst": text.upper(), "punkt": {"lat": 59.9, "lon": 10.7}}
                    ]
                    body = json.dumps({"adresser": hits}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def api():
    server = _Api()
    yield server
    server.server.shutdown()


@pytest.fixture(autouse=True)
def _cache(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "HTTP_CACHE_DIR", tmp_path / "http_cache")
    monkeypatch.setattr(http_lookup, "_MAX_BACKOFF", 0.0)


def _config(tmp_path: Path, step: dict, names=("Storgata 1", "Storgata 1", "nowhere", "Åsveien 2")) -> Config:
    csv = tmp_path / "addresses.csv"
    csv.write_text("id,adresse\n" + "".join(f"{i},{n}\n" for i, n in enumerate(names)), encoding="utf-8")
    fields = list(step.get("fields", {})) + ([step["status_field"]] if step.get("status_field") else [])
    return Config.model_validate({"name": "t", "output": str(tmp_path / "out.gpkg"), "pipelines": [{
        "name": "t", "working_crs": "EPSG:25833", "base": "addr",
        "sources": [{"id": "addr", "format": "csv", "uri": str(csv).replace("\\", "/")}],
        "steps": [{"type": "http_lookup", **step}],
        "mapping": [{"to": c, "from": c} for c in ["id", "adresse", *fields]],
        "layers": [{"layer": "out"}],
    }]})


_GEOCODE = {
    "records": "adresser",
    "fields": {"tekst": "adressetekst", "lat": "punkt.lat"},
    "status_field": "http_status",
}


def test_url_template_encodes_column_values():
    step = HttpLookup(url="https://x/sok?sok={adresse}&n={{1}}", params={"kommune": "kn"})
    con = duckdb.connect()
    url = con.execute(
        f"SELECT {http_lookup.request_sql(step)} FROM (SELECT 'Storgata 1, Øvre & Co' AS adresse, 301 AS kn)"
    ).fetchone()[0]
    assert url == "https://x/sok?sok=Storgata%201%2C%20%C3%98vre%20%26%20Co&n={1}&kommune=301"


def test_null_placeholder_makes_no_request():
    step = HttpLookup(url="https://x/{a}")
    con = duckdb.connect()
    assert con.execute(f"SELECT {http_lookup.request_sql(step)} FROM (SELECT NULL AS a)").fetchone()[0] is None


@pytest.mark.parametrize("bad", [{}, {"url": "u", "url_column": "c"}, {"url": "https://x/{}"}])
def test_config_needs_one_url(bad):
    with pytest.raises(ValueError):
        HttpLookup(**bad)


def test_run_fetches_each_distinct_url_once_and_reads_fields(tmp_path, api):
    cfg = _config(tmp_path, {"url": api.url + "/sok?sok={adresse}", **_GEOCODE})
    run_config(cfg)
    assert len(api.requests) == 3  # "Storgata 1" twice in the data, asked once
    con = duckdb.connect()
    con.execute("LOAD spatial")
    rows = con.execute(
        f"SELECT id, tekst, lat, http_status FROM ST_Read('{cfg.output}', layer='out') ORDER BY id"
    ).fetchall()
    assert rows == [
        ("0", "STORGATA 1", "59.9", 200), ("1", "STORGATA 1", "59.9", 200), ("2", None, None, 200),
        ("3", "ÅSVEIEN 2", "59.9", 200),
    ]


def test_rows_without_a_hit_are_rejects(tmp_path, api):
    cfg = _config(tmp_path, {"url": api.url + "/sok?sok={adresse}", **_GEOCODE, "rejects": "not_found"})
    pipeline = cfg.pipelines[0]
    with pytest.raises(http_lookup.HttpPending) as pending:
        Engine(pipeline, http="pending").preview()
    http_lookup.fetch(pending.value.job)
    rejects = Engine(pipeline, http="pending").preview(preview_until_step=1, rejects=True)
    assert [r["adresse"] for r in rejects] == ["nowhere"]
    passed = Engine(pipeline, http="pending").preview(preview_until_step=1)
    assert len(passed) == 3


def test_second_run_reads_the_cache(tmp_path, api):
    cfg = _config(tmp_path, {"url": api.url + "/sok?sok={adresse}", **_GEOCODE})
    run_config(cfg)
    run_config(cfg)
    assert len(api.requests) == 3


def test_run_requests_responses_older_than_max_age_again(tmp_path, api):
    cfg = _config(tmp_path, {"url": api.url + "/sok?sok={adresse}", **_GEOCODE, "max_age_hours": 1})
    run_config(cfg)
    con = sqlite3.connect(settings.HTTP_CACHE_DIR / "responses.sqlite")
    con.execute("UPDATE responses SET fetched = ?", (time.time() - 2 * 3600,))
    con.commit()
    con.close()
    run_config(cfg)
    assert len(api.requests) == 6


def test_failed_requests_are_retried_then_reported(tmp_path, api):
    cfg = _config(tmp_path, {"url": api.url + "/fail?x={adresse}", **_GEOCODE}, names=("a",))
    run_config(cfg)
    assert len(api.requests) == http_lookup._ATTEMPTS
    rows = Engine(cfg.pipelines[0], http="cached").preview()
    assert rows[0]["http_status"] == 500 and rows[0]["tekst"] is None


def test_a_response_that_is_not_json_is_no_hit(tmp_path, api):
    cfg = _config(tmp_path, {"url": api.url + "/html?x={adresse}", **_GEOCODE, "rejects": "r"}, names=("a",))
    http_lookup.fetch(http_lookup.job_spec(cfg.pipelines[0].steps[0], [api.url + "/html?x=a"]))
    rows = Engine(cfg.pipelines[0], http="cached").preview(preview_until_step=1, rejects=True)
    assert [r["http_status"] for r in rows] == [200]


def test_preview_never_requests_but_says_what_is_missing(tmp_path, api):
    names = [f"gate {i}" for i in range(30)]
    cfg = _config(tmp_path, {"url": api.url + "/sok?sok={adresse}", **_GEOCODE, "preview_limit": 10}, names=names)
    with pytest.raises(http_lookup.HttpPending) as pending:
        Engine(cfg.pipelines[0], http="pending").preview()
    assert len(pending.value.job["urls"]) == 10
    assert api.requests == []

    http_lookup.fetch(pending.value.job)
    assert len(api.requests) == 10
    rows = Engine(cfg.pipelines[0], http="pending").preview()
    assert len(rows) == 10 and all(r["tekst"] == r["adresse"].upper() for r in rows)
    assert len(api.requests) == 10


def test_live_checks_go_without_missing_responses(tmp_path, api):
    cfg = _config(tmp_path, {"url": api.url + "/sok?sok={adresse}", **_GEOCODE})
    rows = Engine(cfg.pipelines[0], http="cached").preview()
    assert [r["tekst"] for r in rows] == [None] * 4
    assert api.requests == []


def test_headers_read_env_vars_and_the_cache_keeps_no_secret(tmp_path, api, monkeypatch):
    monkeypatch.setenv("DUCK_SOUP_TEST_TOKEN", "s3cret")
    cfg = _config(
        tmp_path, {"url": api.url + "/sok?sok={adresse}", **_GEOCODE, "headers": {"X-Key": "${DUCK_SOUP_TEST_TOKEN}"}},
        names=("a",),
    )
    run_config(cfg)
    assert api.headers[0]["X-Key"] == "s3cret"
    assert b"s3cret" not in (settings.HTTP_CACHE_DIR / "responses.sqlite").read_bytes()


def test_missing_env_var_is_an_error(tmp_path, api):
    cfg = _config(tmp_path, {"url": api.url + "/sok?sok={adresse}", **_GEOCODE, "headers": {"X": "${NOPE_NOT_SET_9}"}})
    with pytest.raises(ValueError, match="NOPE_NOT_SET_9"):
        run_config(cfg)


def test_unknown_column_in_url_names_the_step(tmp_path, api):
    cfg = _config(tmp_path, {"url": api.url + "/sok?sok={nope}", **_GEOCODE})
    with pytest.raises(ValueError, match=r"pipelines\.0\.steps\.0\.url"):
        Engine(cfg.pipelines[0], http="pending").preview()


def test_url_column_is_sent_as_is(tmp_path, api):
    cfg = _config(tmp_path, {"url_column": "adresse", "fields": {"t": "adresser.0.adressetekst"}}, names=(api.url + "/sok?sok=x%20y",))
    run_config(cfg)
    assert api.requests == ["/sok?sok=x%20y"]


def test_sql_plan_reads_nothing(tmp_path, api):
    cfg = _config(tmp_path, {"url": api.url + "/sok?sok={adresse}", **_GEOCODE})
    plan = Engine(cfg.pipelines[0]).sql_plan()
    assert any('"http_1"' in v["sql"] for v in plan)
    assert api.requests == []


def test_editor_preview_answers_pending_then_rows(tmp_path, api, monkeypatch):
    from fastapi.testclient import TestClient

    from duck_soup.web.app import app

    cfg = _config(tmp_path, {"url": api.url + "/sok?sok={adresse}", **_GEOCODE})
    body = {"config": cfg.model_dump(by_alias=True, exclude_none=True), "client": "t"}
    client = TestClient(app)
    first = client.post("/api/preview", json=body).json()
    assert first["pending"] and first["progress"]["stage"] == "http"
    for _ in range(100):
        d = client.post("/api/preview", json=body).json()
        if not d.get("pending"):
            break
        time.sleep(0.1)
    assert d["ok"], d
    assert [r["tekst"] for r in d["rows"]] == ["STORGATA 1", "STORGATA 1", None, "ÅSVEIEN 2"]
    assert len(api.requests) == 3

    # A live check never fetches.
    other = _config(tmp_path, {"url": api.url + "/sok?sok=new{adresse}", **_GEOCODE})
    check = client.post("/api/preview", json={
        "config": other.model_dump(by_alias=True, exclude_none=True), "client": "c", "cached_http": True,
    }).json()
    assert check["ok"] and len(api.requests) == 3


def test_editor_test_request_shows_the_first_response(tmp_path, api):
    from fastapi.testclient import TestClient

    from duck_soup.web.app import app

    cfg = _config(tmp_path, {"url": api.url + "/sok?sok={adresse}", **_GEOCODE})
    d = TestClient(app).post("/api/http_sample", json={
        "config": cfg.model_dump(by_alias=True, exclude_none=True), "step": 1,
    }).json()
    assert d["ok"], d
    assert d["url"] == api.url + "/sok?sok=Storgata%201" and d["status"] == 200
    assert json.loads(d["body"])["adresser"][0]["adressetekst"] == "STORGATA 1"
    assert len(api.requests) == 1
