"""HTTP lookups: the `http_lookup` step, one GET per distinct URL.

The step's SQL (steps.http_lookup_sql) is an ordinary left join of the running rows to a
TEMP TABLE `http_<i>` (url, status, doc) of responses, which Engine._http_responses fills
before creating the step's view. Responses are kept in a SQLite cache shared by the editor's
server and its workers (settings.HTTP_CACHE_DIR), keyed by URL and headers, so a URL is only
ever requested again when a run finds its response older than the step's `max_age_hours`.

How missing responses get fetched depends on who's asking (Engine's `http` mode):

- "fetch" (runs, the CLI): fetch_responses, right there, with progress in the log.
- "pending" (the editor's previews and row counts): never on the network in the engine
  worker. The missing URLs are reported (HttpPending) instead, the server fetches them in a
  background job (start_job) and answers "pending" with its progress, and the editor asks
  again once it's done, the way it waits for a source that's still downloading
  (sources.prepare). Previews look up only the first `preview_limit` rows (see the engine).
- "cached" (the editor's live checks): missing responses are just missing (NULL fields).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import requests

from .config import HttpLookup, _URL_PLACEHOLDER
from .sources import settings
from .sql_util import quote_ident as _ident, quote_literal as _lit

# A failed request (no response, 429, 5xx) is worth trying again: a run always does, a
# preview once its failure is this old (seconds), so a dead API isn't asked on every edit.
_RETRY_FAILED_AFTER = 300.0
# Attempts per request before it's stored as failed, and the longest wait between them.
_ATTEMPTS = 3
_MAX_BACKOFF = 30.0
# SQLite's limit on bound parameters is 999 in older builds.
_LOOKUP_CHUNK = 500


class HttpPending(Exception):
    """Raised in "pending" mode when step `step` needs responses that aren't cached: `job`
    (see job_spec) says what to fetch."""

    def __init__(self, step: int, job: dict):
        super().__init__(f"step {step}: {len(job['urls'])} HTTP requests to make")
        self.step = step
        self.job = job


# ---- SQL --------------------------------------------------------------------

def request_sql(step: HttpLookup) -> str:
    """SQL expression for a row's request URL, over the running row's (unqualified) columns.
    NULL when a placeholder or parameter is NULL: that row makes no request."""
    if step.url_column:
        return f"CAST({_ident(step.url_column.strip())} AS VARCHAR)"
    url = step.url.strip()
    parts: list[str] = []
    literal: list[str] = []
    pos = 0
    for m in _URL_PLACEHOLDER.finditer(url):
        literal.append(url[pos:m.start()])
        pos = m.end()
        if m.group(1) is None:  # {{ or }}
            literal.append(m.group(0)[0])
            continue
        if literal:
            parts.append(_lit("".join(literal)))
            literal = []
        parts.append(f"url_encode(CAST({_ident(m.group(1).strip())} AS VARCHAR))")
    literal.append(url[pos:])
    if "".join(literal):
        parts.append(_lit("".join(literal)))
    if step.params:
        sep = "&" if "?" in url else "?"
        for name, expr in step.params.items():
            parts.append(_lit(f"{sep}{name}="))
            parts.append(f"url_encode(CAST(({expr}) AS VARCHAR))")
            sep = "&"
    return " || ".join(parts) or "''"


def json_path(*dot_paths: str | None) -> str:
    """A DuckDB JSON path for dot paths joined together: `adresser.0.navn` →
    `$."adresser"[0]."navn"` (a number is a list index)."""
    out = "$"
    for dot in dot_paths:
        for seg in (dot or "").split("."):
            seg = seg.strip()
            if not seg:
                continue
            out += f"[{seg}]" if seg.isdigit() else '."' + seg.replace('"', '\\"') + '"'
    return out


def hit_path(step: HttpLookup, path: str = "") -> str:
    """Path to `path` inside the hit: the first of `records`, or the whole response."""
    if (step.records or "").strip():
        return json_path(step.records, "0", path)
    return json_path(path)


def field_sql(step: HttpLookup, path: str, doc: str = "r.doc") -> str:
    """A field's value, as text (NULL when the hit hasn't got it)."""
    return f"json_extract_string({doc}, {_lit(hit_path(step, path))})"


def hit_sql(step: HttpLookup, alias: str = "r") -> str:
    """Whether a row got a hit: a 2xx JSON response, with a first record if `records` is set."""
    ok = f"{alias}.status BETWEEN 200 AND 299 AND {alias}.doc IS NOT NULL"
    if (step.records or "").strip():
        ok += f" AND json_extract({alias}.doc, {_lit(hit_path(step))}) IS NOT NULL"
    return f"coalesce({ok}, false)"


# ---- cache ------------------------------------------------------------------

def cache_key(url: str, headers: dict[str, str]) -> str:
    """By the headers as written (with ${VAR}s unexpanded): the cache never holds a secret."""
    raw = url + "\n" + json.dumps(sorted(headers.items()))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _connect() -> sqlite3.Connection:
    folder = settings.HTTP_CACHE_DIR
    folder.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(folder / "responses.sqlite", timeout=60, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(
        "CREATE TABLE IF NOT EXISTS responses ("
        "key TEXT PRIMARY KEY, url TEXT, status INTEGER, body TEXT, fetched REAL)"
    )
    return con


def _failed(status: int) -> bool:
    return status == 0 or status == 429 or status >= 500


def lookup(
    urls: list[str], headers: dict[str, str], max_age_hours: float | None, preview: bool,
) -> tuple[dict[str, tuple[int, str]], list[str]]:
    """({url: (status, body)} of usable cached responses, [urls missing]). A run (`preview`
    False) doesn't use responses older than `max_age_hours`, nor failed ones."""
    now = time.time()
    found: dict[str, tuple[int, str]] = {}
    keys = {cache_key(u, headers): u for u in urls}
    con = _connect()
    try:
        items = list(keys)
        for start in range(0, len(items), _LOOKUP_CHUNK):
            chunk = items[start:start + _LOOKUP_CHUNK]
            rows = con.execute(
                f"SELECT key, status, body, fetched FROM responses WHERE key IN ({','.join('?' * len(chunk))})",
                chunk,
            ).fetchall()
            for key, status, body, fetched in rows:
                age = now - fetched
                if _failed(status):
                    if not preview or age > _RETRY_FAILED_AFTER:
                        continue
                elif not preview and max_age_hours is not None and age > max_age_hours * 3600:
                    continue
                found[keys[key]] = (status, body)
    finally:
        con.close()
    return found, [u for u in urls if u not in found]


def _store(con: sqlite3.Connection, lock: threading.Lock, url: str, headers: dict, status: int, body: str) -> None:
    with lock:
        con.execute(
            "INSERT OR REPLACE INTO responses VALUES (?, ?, ?, ?, ?)",
            (cache_key(url, headers), url, status, body, time.time()),
        )
        con.commit()


# ---- fetching ---------------------------------------------------------------

_ENV_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def resolve_headers(headers: dict[str, str]) -> dict[str, str]:
    def env(m: re.Match) -> str:
        if m.group(1) not in os.environ:
            raise ValueError(f"http_lookup header uses ${{{m.group(1)}}}, which isn't set in the environment")
        return os.environ[m.group(1)]

    return {k: _ENV_VAR.sub(env, v) for k, v in headers.items()}


def job_spec(step: HttpLookup, urls: list[str]) -> dict:
    """What a fetch needs, as plain data (it crosses the worker pipe)."""
    return {
        "urls": urls, "headers": dict(step.headers), "max_rate": step.max_rate,
        "concurrency": step.concurrency, "timeout": step.timeout,
    }


class _RateLimit:
    def __init__(self, per_second: float):
        self.interval = 1.0 / per_second
        self.next = time.monotonic()
        self.lock = threading.Lock()

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            at = max(now, self.next)
            self.next = at + self.interval
        if at > now:
            time.sleep(at - now)


def _get(url: str, headers: dict, timeout: float, limit: _RateLimit, cancel: threading.Event | None) -> tuple[int, str]:
    """(status, body); (0, error message) when no response came."""
    result: tuple[int, str] = (0, "not requested")
    for attempt in range(_ATTEMPTS):
        if cancel is not None and cancel.is_set():
            return result
        limit.wait()
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            result = (resp.status_code, resp.text)
            if not _failed(resp.status_code):
                return result
            retry_after = resp.headers.get("Retry-After", "")
            wait = float(retry_after) if retry_after.isdigit() else 2.0 ** attempt
        except requests.RequestException as e:
            result = (0, str(e) or type(e).__name__)
            wait = 2.0 ** attempt
        if attempt + 1 < _ATTEMPTS:
            time.sleep(min(wait, _MAX_BACKOFF))
    return result


def fetch(
    job: dict,
    on_progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> None:
    """Request every URL of `job` (see job_spec), storing each response in the cache as it
    comes. `on_progress(done, total)` after each one; `cancel` stops before the next."""
    urls, headers = job["urls"], job["headers"]
    sent = resolve_headers(headers)
    limit = _RateLimit(job["max_rate"])
    con, lock = _connect(), threading.Lock()
    done = [0]

    def one(url: str) -> None:
        if cancel is not None and cancel.is_set():
            return
        status, body = _get(url, sent, job["timeout"], limit, cancel)
        if cancel is not None and cancel.is_set() and status == 0:
            return  # cut short, not failed: leave it for next time
        _store(con, lock, url, headers, status, body)
        with lock:
            done[0] += 1
            n = done[0]
        if on_progress:
            on_progress(n, len(urls))

    try:
        with ThreadPoolExecutor(max_workers=job["concurrency"]) as pool:
            list(pool.map(one, urls))
    finally:
        con.close()


def fetch_responses(job: dict, log: Callable[[str], None], label: str) -> None:
    """fetch, logging progress now and then (a run)."""
    total = len(job["urls"])
    log(f"{label}: {total} HTTP requests to make")
    started = time.monotonic()
    last = [started]

    def progress(done: int, total: int) -> None:
        now = time.monotonic()
        if done == total or now - last[0] >= 5:
            last[0] = now
            log(f"{label}: {done} / {total} requests ({now - started:.0f} s)")

    fetch(job, progress)


# ---- the editor's background jobs ---------------------------------------------
# One job per client (an editor tab's preview, row counts): a newer job of the same client
# cancels the older one, whose URLs the edit may no longer need. Each job records the
# request that started it, so while it runs, that same request (the editor asking again) is
# answered with its progress without recomputing the pipeline in the engine worker.

_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
# A job that failed outright (a missing ${VAR}): its error, for the requests asking next.
_JOB_ERRORS: dict[str, tuple[str, str, float]] = {}
_JOB_ERROR_TTL = 60.0


def request_signature(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _progress(job: dict) -> dict:
    return {
        "stage": "http", "source": job["label"], "done": job["done"], "total": job["total"],
        "elapsed": round(time.monotonic() - job["started"], 1),
    }


def job_progress(client: str, signature: str) -> dict | None:
    """The progress of `client`'s running job when it was started by this same request (see
    start_job), or {"stage": "error", ...} when that job failed; None otherwise."""
    with _JOBS_LOCK:
        job = _JOBS.get(client)
        if job is not None and job["signature"] == signature:
            return _progress(job)
        failed = _JOB_ERRORS.get(client)
        if failed is not None and failed[0] == signature and time.monotonic() - failed[2] < _JOB_ERROR_TTL:
            return {"stage": "error", "error": failed[1]}
    return None


def start_job(client: str, signature: str, spec: dict, label: str) -> dict:
    """Fetch `spec` (see job_spec) in a background thread for `client`, cancelling the
    client's previous job. Returns its progress."""
    with _JOBS_LOCK:
        old = _JOBS.get(client)
        if old is not None:
            if set(spec["urls"]) <= old["urls"]:
                # An edit that needs nothing new (a later step, the mapping): the running job
                # already fetches it all, and now answers this request too.
                old["signature"] = signature
                return _progress(old)
            old["cancel"].set()
        _JOB_ERRORS.pop(client, None)
        job = {
            "signature": signature, "label": label, "urls": set(spec["urls"]),
            "done": 0, "total": len(spec["urls"]),
            "started": time.monotonic(), "cancel": threading.Event(),
        }
        _JOBS[client] = job

    def progress(done: int, total: int) -> None:
        job["done"] = done

    def work() -> None:
        try:
            fetch(spec, progress, job["cancel"])
        except Exception as e:
            with _JOBS_LOCK:
                _JOB_ERRORS[client] = (signature, str(e) or type(e).__name__, time.monotonic())
        finally:
            with _JOBS_LOCK:
                if _JOBS.get(client) is job:
                    del _JOBS[client]

    threading.Thread(target=work, daemon=True, name=f"http-lookup-{client}").start()
    return _progress(job)
