"""What the editor asks about a source before reading it: whether it's ready (downloaded,
converted) or being prepared in the background, and the notes its card shows."""
from __future__ import annotations

import threading
import time

from ..config import Source
from .formats import DOWNLOADED, GDAL_TABULAR
from .large_files import _local_file_size
from .remote import (
    _clear_all_progress,
    _set_progress,
    download_time,
    fetch_remote_file,
    is_remote_uri,
    latest_download,
    should_download,
    source_progress,
)
from .tabular import _read_as_parquet, _tabular_cache_path, _tabular_open_extra


def _mb(n: int) -> str:
    return f"{n / 1e6:.1f} MB"


def download_info(src: Source) -> dict | None:
    """For a remote file source previews read from a download: {"bytes", "age" (seconds since
    it was downloaded), "parquet" (also read via a Parquet copy)}, for the source card's
    "downloaded 2 h ago · refresh" note. None when it isn't downloaded (yet)."""
    if not should_download(src.uri, src.format):
        return None
    latest = latest_download(src.uri, src.format)
    if latest is None:
        return None
    return {
        "bytes": latest.stat().st_size,
        "age": max(0.0, round(time.time() - download_time(latest), 1)),
        "parquet": _read_as_parquet(src, str(latest)),
    }


def cached_read_note(src: Source) -> str | None:
    """User-facing note when a local `src` is read from a Parquet copy (see
    _tabular_parquet_cache). Remote sources get download_info instead."""
    if is_remote_uri(src.uri) or not _read_as_parquet(src, src.uri):
        return None
    return (
        f"Large {src.format} file ({_mb(_local_file_size(src.uri))}): read from a Parquet "
        f"copy for fast previews, rebuilt when the file changes."
    )


# ---- background preparation (editor) ----
# The editor must not block a request on a download or a Parquet conversion: browsers keep
# only ~6 connections open per server, and inspect + layer listing + preview + counts all
# waiting on one slow source starve everything else (validation, other sources). So its
# endpoints call prepare_source first, which does that work in a background thread and
# answers "not ready yet, here's the progress" straight away. The CLI never calls it:
# read_expr does the same work inline, sharing the same caches.
_PREPARE_JOBS: dict[str, threading.Thread] = {}
_PREPARE_LOCK = threading.Lock()

# The last failed preparation per uri: (what was prepared, when it failed, the error). Every
# request asking about the same thing gets that error until it's _PREPARE_ERROR_TTL old,
# the source's settings change, or the user refreshes. Handing it to only the first request
# made the others (inspect, preview and counts all ask) start new attempts: a dead URL got
# tried several times over.
_PREPARE_ERRORS: dict[str, tuple[tuple, float, str]] = {}
_PREPARE_ERROR_TTL = 60.0  # seconds


def _prepare_signature(src: Source) -> tuple:
    """What preparing `src` depends on: a change to any of these is worth another attempt."""
    return (src.uri, src.format, src.layer, _tabular_open_extra(src))


def _needs_preparing(src: Source) -> bool:
    """Whether reading `src` right now would mean a download or a Parquet conversion."""
    if src.format not in DOWNLOADED:
        return False
    local = src.uri
    if should_download(src.uri, src.format):
        latest = latest_download(src.uri, src.format)
        if latest is None:
            return True
        local = str(latest)
    if src.format in GDAL_TABULAR:
        target = _tabular_cache_path(local, src.layer, _tabular_open_extra(src))
        if target is not None and not target[0].exists():
            return True
    return False


def _prepare_job(src: Source, refresh: bool) -> None:
    from ..worker import run_once

    key = src.uri
    try:
        local = src.uri
        if should_download(src.uri, src.format):
            local = fetch_remote_file(src.uri, src.format, progress_key=key, fresh=refresh)
        if src.format in GDAL_TABULAR:
            extra = _tabular_open_extra(src)
            target = _tabular_cache_path(local, src.layer, extra)
            if target is not None and not target[0].exists():
                _set_progress(key, "convert", bytes=_local_file_size(local))
                # In a worker process of its own (see worker.py): GDAL reads the whole file
                # here, and a native crash on some odd row mustn't take the editor down.
                run_once("convert", {"path": local, "layer": src.layer, "extra": extra})
    except Exception as e:
        message = str(e) or type(e).__name__
        with _PREPARE_LOCK:
            _PREPARE_ERRORS[key] = (_prepare_signature(src), time.monotonic(), message)
    finally:
        _clear_all_progress(key)
        with _PREPARE_LOCK:
            _PREPARE_JOBS.pop(key, None)


def prepare_source(src: Source, refresh: bool = False) -> dict | None:
    """None when `src` can be read right away. Otherwise makes sure a background job is
    downloading / converting it (one per uri) and returns what it's doing — see
    source_progress — or {"stage": "error", "error": ...} while the last attempt's failure
    still applies (see _PREPARE_ERRORS). `refresh` downloads a remote file source again
    even when it's already downloaded, and retries one that failed."""
    key = src.uri
    with _PREPARE_LOCK:
        failed = _PREPARE_ERRORS.get(key)
        if failed is not None:
            signature, at, message = failed
            if (not refresh and signature == _prepare_signature(src)
                    and time.monotonic() - at < _PREPARE_ERROR_TTL):
                return {"stage": "error", "error": message}
            del _PREPARE_ERRORS[key]
        if key not in _PREPARE_JOBS:
            refresh = refresh and should_download(src.uri, src.format)
            if not refresh and not _needs_preparing(src):
                return None
            job = threading.Thread(target=_prepare_job, args=(src, refresh), daemon=True)
            _PREPARE_JOBS[key] = job
            job.start()
    return source_progress(key) or {"stage": "queued", "elapsed": 0.0}
