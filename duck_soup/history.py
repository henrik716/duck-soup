"""Run history: one JSON file per run, under `<root>/runs/<config name>/`.

Both the CLI (`duck-soup run`, i.e. scheduled runs) and the editor's run button write here,
so the editor's Runs tab can answer "did last night's run work, and how many rows did it
write?". Plain files, one per run, so they're as easy to read, diff, back up or delete as
the pipelines themselves. Each config keeps its newest MAX_RUNS runs.

Recording is best effort: a run that worked must never fail because its history couldn't be
written (a read-only folder, a full disk), so record_run() only warns.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

RUNS_DIR = "runs"
MAX_RUNS = 200

_UNSAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_RUN_ID_RE = re.compile(r"^\d{8}-\d{6}-\d{6}$")

# What the editor's run list shows per run; the rest (log, yaml) is only in the full record.
_SUMMARY_KEYS = (
    "id", "config", "trigger", "started_at", "duration_s", "ok", "error", "output",
    "layers", "config_hash",
)


def default_root() -> Path:
    """Where the CLI keeps history when not told: the editor's project folder if
    DUCK_SOUP_ROOT says where that is (so the editor sees scheduled runs), else the current
    folder, which is the project folder in the scheduling recipes."""
    env = os.environ.get("DUCK_SOUP_ROOT")
    return Path(env).resolve() if env else Path.cwd()


def config_dir(root: Path, name: str) -> Path:
    """`<root>/runs/<name>/`, with `name` reduced to characters safe in a folder name."""
    safe = _UNSAFE_RE.sub("_", name).strip("._") or "config"
    return Path(root) / RUNS_DIR / safe


def config_hash(yaml_text: str) -> str:
    """Short fingerprint of the config that ran, to tell runs of different versions apart."""
    return hashlib.sha256(yaml_text.encode("utf-8")).hexdigest()[:12]


def record_run(
    root: Path,
    name: str,
    *,
    trigger: str,
    started: datetime,
    finished: datetime,
    ok: bool,
    output: str | None = None,
    error: str | None = None,
    layers: list[dict] | None = None,
    log: list[str] | None = None,
    yaml_text: str = "",
) -> Path | None:
    """Write one run's record; returns its path, or None (with a warning) if it couldn't."""
    record = {
        "id": started.astimezone(timezone.utc).strftime("%Y%m%d-%H%M%S-%f"),
        "config": name,
        "trigger": trigger,
        "started_at": started.astimezone(timezone.utc).isoformat(),
        "finished_at": finished.astimezone(timezone.utc).isoformat(),
        "duration_s": round((finished - started).total_seconds(), 3),
        "ok": ok,
        "error": error,
        "output": output,
        "layers": layers or [],
        "config_hash": config_hash(yaml_text) if yaml_text else None,
        "log": log or [],
        "yaml": yaml_text,
    }
    folder = config_dir(root, name)
    path = folder / f"{record['id']}.json"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        _prune(folder)
    except OSError as e:
        print(f"warning: couldn't record this run in {folder}: {e}", file=sys.stderr)
        return None
    return path


def _run_files(folder: Path) -> list[Path]:
    """The folder's run records, newest first (ids sort chronologically)."""
    if not folder.is_dir():
        return []
    files = [p for p in folder.glob("*.json") if _RUN_ID_RE.match(p.stem)]
    return sorted(files, key=lambda p: p.stem, reverse=True)


def _prune(folder: Path) -> None:
    for old in _run_files(folder)[MAX_RUNS:]:
        old.unlink(missing_ok=True)


def list_runs(root: Path, name: str, limit: int = 50) -> list[dict]:
    """Summaries of a config's newest runs, newest first. Unreadable files are skipped."""
    runs = []
    for path in _run_files(config_dir(root, name))[:limit]:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        runs.append({k: record.get(k) for k in _SUMMARY_KEYS})
    return runs


def get_run(root: Path, name: str, run_id: str) -> dict | None:
    """One run's full record, or None. `run_id` must look like an id, so it can't name a
    path outside the config's folder."""
    if not _RUN_ID_RE.match(run_id):
        return None
    path = config_dir(root, name) / f"{run_id}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
