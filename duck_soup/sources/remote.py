"""Remote file sources: downloading an http(s) uri to a local file, and the progress of
slow source work (downloads, conversions) for the editor."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Callable

import requests

from ..config import Source
from . import settings
from .formats import DOWNLOADED

# Remote file sources (an http(s) uri on a gpkg/geojson/csv/... source) are downloaded once
# to a local file instead of being handed to ST_Read as a URL. GDAL reads URLs through
# /vsicurl/, which does a HEAD and then many small Range requests — fine against a static
# file host, but an export endpoint that builds the file per request (no Content-Length, no
# Range support, e.g. NVDB's segmentert.csv) answers every one of those with the whole
# body, so a single ST_Read turns into dozens of full downloads and times out.
#
# Downloads are kept in the temp dir until replaced. Previews (the editor's inspect /
# preview / counts) read the newest download of a URL on disk, whichever process made it and
# across editor restarts, until the user refreshes it (prepare_source(refresh=True), the
# source card's "refresh" link): a dataset rarely changes between two previews, and fetching
# it again costs as long as the download takes. A run (run_config: `duck-soup run`, the
# editor's Run) downloads fresh instead, see fresh_downloads, so its output reflects the
# current data. Where they go: settings.REMOTE_CACHE_DIR.

# One lock per URL: two reads of the same URL share one download (the second waits for the
# first, then finds it), while different URLs download in parallel. A single lock for all of
# them made every other download queue behind the slowest, even an abandoned one.
_URL_LOCKS: dict[str, threading.Lock] = {}
_URL_LOCKS_GUARD = threading.Lock()


def _url_lock(uri: str) -> threading.Lock:
    with _URL_LOCKS_GUARD:
        return _URL_LOCKS.setdefault(uri, threading.Lock())

# How old a superseded download (or its Parquet copy) must be before it's deleted. Every
# read looks up the newest download, but a query already running against an older one (a
# long run in another process) re-opens its file as it goes, so it mustn't disappear at once.
_SUPERSEDED_GRACE = 3600.0  # seconds

# Set by fresh_downloads() for the duration of a run: when it started (a download finished
# before then is fetched again), and the downloads made during it, {uri: path}.
_FRESH_AFTER: ContextVar[float | None] = ContextVar("duck_soup_fresh_after", default=None)
_RUN_DOWNLOADS: ContextVar[dict[str, str] | None] = ContextVar("duck_soup_run_downloads", default=None)


@contextmanager
def fresh_downloads(reuse: dict[str, str] | None = None):
    """Within this block (a run), remote file sources are downloaded again instead of reusing
    an earlier download, once per URL: a download made inside the block is reused, so a URL
    read by several pipelines is still only fetched once.

    Yields the block's downloads, {uri: path}. `reuse` seeds it with downloads made for this
    run elsewhere: the editor downloads a run's files itself, then hands them to its engine
    worker (worker.py). Kept by path rather than compared by time, since Windows' clock
    ticks too coarsely to order a download against the start of a run."""
    done = dict(reuse or {})
    tokens = (_FRESH_AFTER.set(time.time()), _RUN_DOWNLOADS.set(done))
    try:
        yield done
    finally:
        _FRESH_AFTER.reset(tokens[0])
        _RUN_DOWNLOADS.reset(tokens[1])


def _superseded_long_enough(path: Path) -> bool:
    try:
        return time.time() - path.stat().st_mtime > _SUPERSEDED_GRACE
    except OSError:
        return False


# What a slow source is busy with right now (downloading, converting to Parquet), keyed by
# the source's uri as configured. Reported back by prepare_source, which the editor's
# endpoints call before reading. Each stage removes its own entry when it ends.
_PROGRESS: dict[str, dict] = {}
_PROGRESS_LOCK = threading.Lock()


def _set_progress(key: str | None, stage: str, **fields) -> None:
    if not key:
        return
    with _PROGRESS_LOCK:
        prev = _PROGRESS.get(key)
        started = prev["_started"] if prev and prev["stage"] == stage else time.monotonic()
        _PROGRESS[key] = {"stage": stage, "_started": started, **fields}


def _clear_progress(key: str | None, stage: str) -> None:
    with _PROGRESS_LOCK:
        if key and _PROGRESS.get(key, {}).get("stage") == stage:
            del _PROGRESS[key]


def _clear_all_progress(key: str) -> None:
    with _PROGRESS_LOCK:
        _PROGRESS.pop(key, None)


def source_progress(key: str) -> dict | None:
    """The current slow stage for a source uri, or None: {"stage": "download", "bytes": n,
    "total": n or None (no Content-Length), "elapsed": s} or {"stage": "convert", ...}."""
    with _PROGRESS_LOCK:
        p = _PROGRESS.get(key)
        if p is None:
            return None
        out = {k: v for k, v in p.items() if not k.startswith("_")}
        out["elapsed"] = round(time.monotonic() - p["_started"], 1)
        return out


def is_remote_uri(uri: str) -> bool:
    return uri.lower().startswith(("http://", "https://"))


def should_download(uri: str, fmt: str) -> bool:
    """Whether read_expr downloads this remote uri first (fetch_remote_file). Not a bare
    .shp, whose .shx/.dbf sidecars only GDAL's own /vsicurl/ reader fetches alongside it."""
    from urllib.parse import urlparse

    if not is_remote_uri(uri):
        return False
    return not (fmt == "shp" and urlparse(uri).path.lower().endswith(".shp"))


def download_remote_sources(sources: list[Source], log: Callable[[str], None] | None = None) -> None:
    """Download every remote file among `sources`, in parallel, the way read_expr would:
    fresh inside fresh_downloads(), once per URL. The editor does this for a run before
    handing it to its engine worker (worker.py), so the downloads keep showing their
    progress and are shared with the editor's own reads of the same URLs."""
    from concurrent.futures import ThreadPoolExecutor
    from contextvars import copy_context

    todo = {(s.uri, s.format) for s in sources
            if s.format in DOWNLOADED and should_download(s.uri, s.format)}
    if not todo:
        return
    # copy_context: each thread sees this run's fresh_downloads() setting.
    with ThreadPoolExecutor(max_workers=min(len(todo), 4)) as pool:
        futures = [
            pool.submit(copy_context().run, fetch_remote_file, uri, fmt, log, uri)
            for uri, fmt in sorted(todo)
        ]
        for f in futures:
            f.result()


def remote_file_name(uri: str, fmt: str) -> str:
    """The local file name fetch_remote_file gives a download of `uri`: the URL's own name
    (query string dropped), plus the format's extension when it has none."""
    from urllib.parse import unquote, urlparse

    name = re.sub(r'[<>:"/\\|?*]', "_", unquote(Path(urlparse(uri).path).name)) or fmt
    return name if Path(name).suffix else f"{name}.{fmt}"


def _url_dir(uri: str) -> Path:
    return settings.REMOTE_CACHE_DIR / hashlib.sha1(uri.encode()).hexdigest()[:16]


def latest_download(uri: str, fmt: str) -> Path | None:
    """The newest complete download of `uri` on disk (by any process), or None. Downloads sit
    at <url hash>/<download time, ns>/<name>; one still in progress has no <name> yet."""
    name = remote_file_name(uri, fmt)
    try:
        folders = [f for f in _url_dir(uri).iterdir() if f.is_dir() and f.name.isdigit()]
    except OSError:
        return None
    for folder in sorted(folders, key=lambda f: int(f.name), reverse=True):
        if (folder / name).is_file():
            return folder / name
    return None


def download_time(path: Path) -> float:
    """When a download (a latest_download path) finished, as a Unix timestamp."""
    return Path(path).stat().st_mtime


def fetch_remote_file(
    uri: str,
    fmt: str,
    log: Callable[[str], None] | None = None,
    progress_key: str | None = None,
    fresh: bool = False,
) -> str:
    """Download a remote file source to a local cache file (see settings.REMOTE_CACHE_DIR) and return
    its path, or the newest earlier download of it unless `fresh` or inside fresh_downloads().
    The file keeps the URL's own name, since GDAL derives a csv/geojson layer name from it
    and picks drivers from compound suffixes like `.gdb.zip`; a name without an extension
    gets the format's (`fmt`).

    A read that arrives while the same URL is already downloading (in this process) waits
    for it and uses it. That includes a run: a download that finished after the run started
    counts as fresh, so Run during a preview's download doesn't fetch the file twice.

    Each download goes into its own folder, `<url hash>/<download time>/<name>`, rather than
    overwriting the previous one: on Windows a file another process still has open (the
    editor server while a CLI run re-downloads, or the reverse) can't be replaced. Older
    downloads of the same URL are removed after _SUPERSEDED_GRACE.

    Progress is published under `progress_key` (default: `uri`), see source_progress."""
    with _url_lock(uri):
        run = _RUN_DOWNLOADS.get()
        if run is not None and not fresh and uri in run and Path(run[uri]).is_file():
            return run[uri]
        latest = latest_download(uri, fmt)
        fresh_after = _FRESH_AFTER.get()
        if latest and not fresh and (fresh_after is None or download_time(latest) > fresh_after):
            if run is not None:
                run[uri] = str(latest)
            return str(latest)

        name = remote_file_name(uri, fmt)
        url_dir = _url_dir(uri)
        # The download time names the folder (latest_download picks the highest). Windows'
        # clock ticks every few ms, so step past a name another download already took.
        url_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.time_ns()
        while True:
            folder = url_dir / str(stamp)
            try:
                folder.mkdir()
                break
            except FileExistsError:
                stamp += 1
        out = folder / name
        tmp = out.with_name(out.name + ".part")
        if log:
            log(f"Downloading {uri}")
        start = time.monotonic()
        key = progress_key or uri
        _set_progress(key, "download", bytes=0, total=None)
        try:
            # (connect, read) timeouts: the read timeout is per socket read, not the whole
            # body, so a slow-to-start export that then streams steadily still gets through.
            with requests.get(uri, stream=True, timeout=(30, 300)) as resp:
                resp.raise_for_status()
                length = getattr(resp, "headers", {}).get("Content-Length")
                # A per-request export (chunked, or a bogus `Content-Length: 0`) has no size.
                total = int(length) if length and length.isdigit() and int(length) > 0 else None
                done = 0
                with open(tmp, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1 << 18):
                        f.write(chunk)
                        done += len(chunk)
                        _set_progress(key, "download", bytes=done, total=total)
        finally:
            _clear_progress(key, "download")
        os.replace(tmp, out)
        if log:
            log(f"Downloaded {out.stat().st_size / 2**20:.1f} MiB in {time.monotonic() - start:.1f}s")
        for old in url_dir.iterdir():
            if old == folder or not _superseded_long_enough(old):
                continue
            try:  # still open elsewhere (Windows) → left for the next download to remove
                shutil.rmtree(old) if old.is_dir() else old.unlink()
            except OSError:
                pass
        if run is not None:
            run[uri] = str(out)
        return str(out)


def _is_download(path: Path) -> bool:
    try:
        return Path(path).resolve().is_relative_to(settings.REMOTE_CACHE_DIR.resolve())
    except OSError:
        return False
