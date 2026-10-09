"""DuckDB work for the editor's server, in child processes.

DuckDB and its spatial extension occasionally crash in native code (ST_LineLocatePoint on a
line with Z used to, for one). In-process, that kills the editor's server with no error at
all. Here it only kills a worker: the call raises EngineCrashed with the exit code and the
last log lines, and the next call starts a fresh worker. The server itself never opens a
DuckDB connection.

Two long-lived workers, each taking one call at a time: ENGINE for the editor's interactive
work (inspect, preview, row counts) and RUNNER for runs, so a long run doesn't hold up
previews, and cancelling one (EngineWorker.cancel) only kills the run. Converting a large
csv/xlsx to Parquet gets a worker of its own per file (sources._prepare_job): it takes long
enough that it would hold up ENGINE too, and starting a process is cheap next to it.

Calls go over a pipe as (task, payload, context, kwargs); the worker answers with any
number of ("log", line) messages and then ("ok", result) or ("error", message, trace,
partial). `context` carries the server's working folder and cache settings, so the worker
reads the same downloads and Parquet copies the server prepared (see sources.py).
"""
from __future__ import annotations

import faulthandler
import io
import multiprocessing as mp
import os
import threading
import traceback
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Callable

# How many of the last log lines an EngineCrashed message quotes.
_CRASH_LOG_LINES = 3
# How often a waiting call checks that the worker is still alive (seconds). A dead worker
# normally shows as a closed pipe at once, but not always (one that dies while starting up).
_POLL_INTERVAL = 0.5
# How long a new worker may take to start (import the engine, load nothing yet).
_START_TIMEOUT = 60.0


class EngineStartFailed(RuntimeError):
    """The worker process died or hung before it was ready to take a call."""

    def __init__(self, exitcode: int | None):
        self.exitcode = exitcode
        super().__init__(
            f"The engine worker failed to start (exit code {_format_exitcode(exitcode)});"
            " see the server's console for the error."
        )


class EngineCrashed(RuntimeError):
    """The worker process died mid-call, i.e. a native crash in DuckDB or an extension."""

    def __init__(self, exitcode: int | None, log: list[str]):
        self.exitcode = exitcode
        self.log = log
        msg = f"The DuckDB engine crashed (exit code {_format_exitcode(exitcode)})"
        if log:
            msg += " after: " + " · ".join(log[-_CRASH_LOG_LINES:])
        msg += (
            ". This is a native crash inside DuckDB or its spatial extension, usually set off"
            " by something in the data; the editor itself is fine and starts a new engine for"
            " the next request."
        )
        super().__init__(msg)


class EngineCancelled(RuntimeError):
    """The call was cancelled (EngineWorker.cancel), which stops its worker."""

    def __init__(self, log: list[str] | None = None):
        self.log = log or []
        super().__init__("Cancelled.")


class EngineTaskError(RuntimeError):
    """A task raised an ordinary exception in the worker. `trace` is the worker's traceback,
    `partial` whatever the task had produced so far (a run: the layers written)."""

    def __init__(self, message: str, trace: str, partial: dict):
        super().__init__(message)
        self.trace = trace
        self.partial = partial


def _format_exitcode(code: int | None) -> str:
    if code is None:
        return "unknown"
    if code > 255:  # a Windows NTSTATUS, e.g. 0xC0000005 (access violation)
        return f"0x{code:08X}"
    if code < 0:  # POSIX: killed by a signal
        return f"signal {-code}"
    return str(code)


# -- worker side --------------------------------------------------------------
# Every task takes (payload, log, partial, **kwargs): `partial` is a dict it fills with
# whatever an error report should still carry.

def _task_preview(config: dict, log: Callable[[str], None], partial: dict, **kwargs) -> Any:
    from .config import load_config_dict
    from .engine import preview_config_pipeline

    return preview_config_pipeline(load_config_dict(config), log=log, **kwargs)


def _task_counts(config: dict, log: Callable[[str], None], partial: dict, **kwargs) -> Any:
    from .config import load_config_dict
    from .engine import count_config_pipeline

    return count_config_pipeline(load_config_dict(config), log=log, **kwargs)


def _task_run(config: dict, log: Callable[[str], None], partial: dict, downloads: dict | None = None) -> Any:
    from .config import load_config_dict
    from .engine import run_config

    written = partial.setdefault("layers", [])
    buf = io.StringIO()
    with redirect_stdout(buf):
        out = run_config(load_config_dict(config), log, written, downloads=downloads)
    return {"output": out, "layers": written, "stdout": buf.getvalue()}


def _task_inspect(source: dict, log: Callable[[str], None], partial: dict) -> dict:
    """A source's columns, and a warning when its coordinates don't fit its CRS."""
    import tempfile

    import duckdb

    from .config import Source
    from .derive import init_duckdb
    from .sources import crs_extent_warning, read_expr

    src = Source.model_validate(source)
    con = duckdb.connect()
    try:
        init_duckdb(con)
        with tempfile.TemporaryDirectory() as tmp:
            read = read_expr(src, Path(tmp), con=con, sample=True)
            res = con.execute(f"DESCRIBE SELECT * FROM {read}").fetchall()
            columns = [{"name": r[0], "type": str(r[1])} for r in res]
            crs_warning = None
            has_geom = any(
                c["name"] == "geom" and c["type"].upper().startswith("GEOMETRY") for c in columns
            )
            if src.crs and has_geom:
                # Bounded by LIMIT so this sanity check stays cheap even against a huge
                # source — a few hundred features are plenty to tell "this is clearly in
                # meters, not degrees" from actual data, no need to scan the whole file.
                # Best-effort: a source this query can't handle (e.g. an odd geometry type
                # ST_XMin/ST_XMax choke on) just skips the hint rather than failing the inspect.
                try:
                    ext = con.execute(
                        f"SELECT MIN(ST_XMin(geom)), MAX(ST_XMax(geom)), "
                        f"MIN(ST_YMin(geom)), MAX(ST_YMax(geom)) "
                        f"FROM (SELECT geom FROM {read} WHERE geom IS NOT NULL LIMIT 500) t"
                    ).fetchone()
                    if ext:
                        crs_warning = crs_extent_warning(src.crs, *ext)
                except Exception:
                    pass
            return {"columns": columns, "crs_warning": crs_warning}
    finally:
        con.close()


def _task_layers(target: dict, log: Callable[[str], None], partial: dict) -> tuple[list, str | None]:
    """(layers, default CRS) of a file or database, by `target["kind"]`: `gdal` (any file
    GDAL reads), `parquet` (layers is empty) or `postgres` (a DSN)."""
    import duckdb

    from .derive import load_extensions
    from .sources import list_gdal_layers, list_postgres_tables, parquet_geometry_info

    con = duckdb.connect()
    try:
        load_extensions(con)
        kind, path = target["kind"], target["path"]
        if kind == "postgres":
            return list_postgres_tables(con, path)
        if kind == "parquet":
            return [], parquet_geometry_info(con, path)[1]
        return list_gdal_layers(con, path)
    finally:
        con.close()


def _task_convert(target: dict, log: Callable[[str], None], partial: dict) -> None:
    """Write the cached Parquet copy of a large local csv/xlsx (sources._tabular_parquet_cache)."""
    import duckdb

    from .derive import load_extensions
    from .sources import _tabular_parquet_cache

    con = duckdb.connect()
    try:
        load_extensions(con)
        _tabular_parquet_cache(con, target["path"], target["layer"], target["extra"], log)
    finally:
        con.close()


_TASKS = {
    "preview": _task_preview, "counts": _task_counts, "run": _task_run,
    "inspect": _task_inspect, "layers": _task_layers, "convert": _task_convert,
}


def _apply_context(ctx: dict) -> None:
    from . import sources

    os.chdir(ctx["cwd"])
    for name, value in ctx["sources"].items():
        setattr(sources, name, value)


def _serve(conn) -> None:
    """The worker's loop: one call at a time until the server closes the pipe."""
    faulthandler.enable()  # a native crash prints a Python traceback to the server's console
    # A worker busy in a long query only notices a closed pipe once it's done: when the
    # server is gone (killed, crashed), stop at once rather than keep computing for nobody.
    server = mp.parent_process()
    if server is not None:
        threading.Thread(target=lambda: (server.join(), os._exit(1)), daemon=True).start()
    conn.send(("ready",))
    while True:
        try:
            task, payload, ctx, kwargs = conn.recv()
        except (EOFError, OSError):
            return
        partial: dict = {}
        try:
            _apply_context(ctx)
            result = _TASKS[task](payload, lambda line: conn.send(("log", line)), partial, **kwargs)
            conn.send(("ok", result))
        except Exception as e:
            conn.send(("error", str(e), traceback.format_exc(), partial))


# -- server side --------------------------------------------------------------

# sources.py settings the server decides for its workers: where the downloads and Parquet
# copies are, and which files get a Parquet copy.
_SHARED_SETTINGS = (
    "_REMOTE_CACHE_DIR", "_TABULAR_CACHE_DIR", "_LINEARIZE_CACHE_DIR", "_TABULAR_CACHE_MIN_BYTES",
)


def _context() -> dict:
    from . import sources

    return {"cwd": os.getcwd(), "sources": {n: getattr(sources, n) for n in _SHARED_SETTINGS}}


class EngineWorker:
    def __init__(self, name: str = "duck-soup-engine") -> None:
        self.name = name
        self._lock = threading.Lock()
        self._proc = None
        self._conn = None
        self._busy = False
        self._cancelled = False

    def _start(self) -> None:
        ctx = mp.get_context("spawn")
        ours, theirs = ctx.Pipe()
        proc = ctx.Process(target=_serve, args=(theirs,), daemon=True, name=self.name)
        proc.start()
        # Only the worker may hold its end: closed here, so a dead worker reads as EOF.
        theirs.close()
        self._proc, self._conn = proc, ours
        try:
            msg = self._recv(timeout=_START_TIMEOUT)
        except (EOFError, OSError, TimeoutError):
            msg = None
        if msg != ("ready",):
            raise EngineStartFailed(self._discard())

    def _recv(self, timeout: float | None = None):
        """The worker's next message. Raises EOFError once the worker is gone (and nothing it
        sent is left unread), TimeoutError after `timeout` seconds."""
        waited = 0.0
        while not self._conn.poll(_POLL_INTERVAL):
            if not self._proc.is_alive():
                if self._conn.poll(0):  # sent just before it exited
                    break
                raise EOFError("engine worker exited")
            waited += _POLL_INTERVAL
            if timeout is not None and waited >= timeout:
                raise TimeoutError
        return self._conn.recv()

    def _discard(self) -> int | None:
        proc, conn = self._proc, self._conn
        self._proc = self._conn = None
        if conn is not None:
            conn.close()
        if proc is None:
            return None
        proc.join(timeout=10)
        if proc.is_alive():
            proc.kill()
            proc.join()
        return proc.exitcode

    def call(self, task: str, payload: Any, log: Callable[[str], None] | None = None, **kwargs) -> Any:
        """Run `task` on `payload` (a config dict, a source dict, ...) in the worker and
        return its result. Raises EngineTaskError when the task fails, EngineCrashed when the
        worker dies, EngineCancelled when cancel() stopped it."""
        with self._lock:
            self._cancelled = False
            if self._proc is None or not self._proc.is_alive():
                self._discard()
                self._start()
            lines: list[str] = []
            self._busy = True
            try:
                self._conn.send((task, payload, _context(), kwargs))
                while True:
                    msg = self._recv()
                    if msg[0] == "log":
                        lines.append(msg[1])
                        if log:
                            log(msg[1])
                    elif msg[0] == "ok":
                        return msg[1]
                    else:
                        raise EngineTaskError(*msg[1:])
            except (EOFError, OSError):
                exitcode = self._discard()
                if self._cancelled:
                    raise EngineCancelled(lines) from None
                raise EngineCrashed(exitcode, lines) from None
            finally:
                self._busy = False

    def cancel(self) -> bool:
        """Stop the call in progress by killing the worker (the next call starts a new one).
        True when there was one to stop."""
        proc = self._proc
        if not self._busy or proc is None:
            return False
        self._cancelled = True
        proc.kill()
        return True

    def close(self) -> None:
        with self._lock:
            self._discard()


def run_once(task: str, payload: Any, log: Callable[[str], None] | None = None, **kwargs) -> Any:
    """Run one task in a worker of its own, stopped afterwards."""
    worker = EngineWorker("duck-soup-convert")
    try:
        return worker.call(task, payload, log=log, **kwargs)
    finally:
        worker.close()


# The server's workers, each started on first use.
ENGINE = EngineWorker("duck-soup-engine")
RUNNER = EngineWorker("duck-soup-runner")
