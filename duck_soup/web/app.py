"""Web backend for the Duck Soup pipeline editor.

Run: uvicorn duck_soup.web.app:app --reload
Then open http://127.0.0.1:8000
"""
from __future__ import annotations

import asyncio
import io
import os
import tempfile
import time
import traceback
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
import shutil

import duckdb
import yaml
from fastapi import FastAPI, Form, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..config import (
    JOIN_PREDICATES,
    MAP_FUNCS,
    SOURCE_FORMATS,
    Source,
    dump_config_yaml,
    load_config,
    load_config_dict,
)
from .. import history
from ..engine import (
    count_config_pipeline, plan_config_pipeline, preview_config_pipeline, run_config,
)
from ..sources import (
    cached_read_note,
    download_info,
    crs_extent_warning,
    fetch_remote_file,
    large_file_reader_note,
    prepare_source,
    remote_file_name,
    should_download,
    list_arcgis_rest_layers,
    list_gdal_layers,
    list_oapif_collections,
    list_postgres_tables,
    list_wfs_layers,
    parquet_geometry_info,
    read_expr,
)
from ..derive import DUCKDB_LOCK, init_duckdb, load_extensions

ROOT = Path(os.environ.get("DUCK_SOUP_ROOT", Path.home() / "duck-soup")).resolve()
PIPELINE_DIR = ROOT / "pipelines"
STATIC_DIR = Path(__file__).resolve().parent / "static"
# Run history lives in <RUNS_ROOT>/runs/ (see history.py): the same place `duck-soup run`
# records scheduled runs when run from this project folder or with DUCK_SOUP_ROOT set.
RUNS_ROOT = ROOT

PIPELINE_DIR.mkdir(parents=True, exist_ok=True)
print(f"Using {ROOT} for pipelines/data/output (override with DUCK_SOUP_ROOT)")

app = FastAPI(title="Duck Soup editor")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/meta")
def meta() -> dict:
    """Vocabulary for the editor's dropdowns."""
    return {
        "formats": SOURCE_FORMATS,
        "predicates": JOIN_PREDICATES,
        "funcs": MAP_FUNCS,
        "step_types": ["spatial_join", "attribute_join", "nearest_neighbor", "buffer", "centroid", "clip", "erase", "dissolve", "intersect_overlay", "line_overlay", "filter", "merge", "snapshot"],
    }


@app.get("/api/pipelines")
def list_pipelines() -> list[str]:
    return sorted(p.stem for p in PIPELINE_DIR.glob("*.yaml"))


@app.get("/api/pipelines/{name}")
def get_pipeline(name: str) -> dict:
    path = PIPELINE_DIR / f"{name}.yaml"
    if not path.exists():
        raise HTTPException(404, f"pipeline '{name}' not found")
    text = path.read_text(encoding="utf-8")
    try:
        cfg = load_config(path)
        config_dict = cfg.model_dump(by_alias=True, exclude_none=True)
        warning = None
    except Exception as e:
        # Saved YAML can go stale/invalid between edits (e.g. a mapping row left
        # mid-edit with no source picked yet). Load it raw so the editor can still
        # open it and let the user fix it there, instead of refusing to load at all.
        config_dict = yaml.safe_load(text) or {}
        warning = str(e)
    return {
        "name": name,
        "config": config_dict,
        "yaml": text,
        "warning": warning,
    }


class SaveRequest(BaseModel):
    config: dict


@app.post("/api/pipelines/{name}")
def save_pipeline(name: str, req: SaveRequest) -> dict:
    try:
        cfg = load_config_dict(req.config)
    except Exception as e:
        raise HTTPException(422, f"invalid config: {e}")
    text = dump_config_yaml(cfg)
    (PIPELINE_DIR / f"{name}.yaml").write_text(text, encoding="utf-8")
    return {"ok": True, "yaml": text}


class InspectRequest(BaseModel):
    source: dict


class RefreshSourceRequest(BaseModel):
    source: dict


@app.post("/api/refresh_source")
def refresh_source(req: RefreshSourceRequest) -> dict:
    """Download a remote file source again (previews otherwise keep reusing its download, see
    sources.py's _REMOTE_CACHE_DIR). Runs in the background like any download: answers
    pending, and the editor re-inspects until it's done."""
    try:
        src = Source.model_validate(req.source)
    except Exception as e:
        return {"ok": False, "error": f"Invalid source config: {e}"}
    prog = prepare_source(src, refresh=True)
    if prog is None:
        return {"ok": True}  # not a downloaded source: nothing to refresh
    if prog["stage"] == "error":
        return {"ok": False, "error": prog["error"]}
    return {"ok": False, "pending": True, "progress": {**prog, "source": src.id}}


def _pending_response(sources: list[Source]) -> dict | None:
    """A response for when one of `sources` still needs downloading / converting to Parquet
    (see sources.prepare_source): `{"ok": False, "pending": True, "progress": {...}}`, which
    the editor shows and then retries. Starts every source that needs it, not just the first.
    None when all of them can be read right away."""
    first = None
    for src in sources:
        prog = prepare_source(src)
        if prog is None:
            continue
        if prog["stage"] == "error":
            return {"ok": False, "error": f"source '{src.id}': {prog['error']}"}
        first = first or {**prog, "source": src.id}
    return {"ok": False, "pending": True, "progress": first} if first else None


@app.post("/api/inspect")
def inspect_source(req: InspectRequest) -> dict:
    try:
        src = Source.model_validate(req.source)
    except Exception as e:
        return {"ok": False, "error": f"Invalid source config: {e}"}

    pending = _pending_response([src])
    if pending:
        return pending
    try:
        with DUCKDB_LOCK:
            con = duckdb.connect()
            try:
                init_duckdb(con)
                with tempfile.TemporaryDirectory() as tmp:
                    workdir = Path(tmp)
                    read = read_expr(src, workdir, con=con, sample=True)
                    res = con.execute(f"DESCRIBE SELECT * FROM {read}").fetchall()
                    columns = [{"name": r[0], "type": str(r[1])} for r in res]
                    crs_warning = None
                    has_geom = any(
                        c["name"] == "geom" and c["type"].upper().startswith("GEOMETRY")
                        for c in columns
                    )
                    if src.crs and has_geom:
                        # Bounded by LIMIT so this sanity check stays cheap even against a
                        # huge source — a few hundred features are plenty to tell "this is
                        # clearly in meters, not degrees" from actual data, no need to scan
                        # the whole file. Best-effort: a source this query can't handle (e.g.
                        # an odd geometry type ST_XMin/ST_XMax choke on) just skips the hint
                        # rather than failing the whole inspect.
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
                    return {
                        "ok": True, "columns": columns, "crs_warning": crs_warning,
                        "reader_note": large_file_reader_note(src) or cached_read_note(src),
                        "download": download_info(src),
                    }
            finally:
                con.close()
    except Exception as e:
        return {"ok": False, "error": str(e)}


class ParseYamlRequest(BaseModel):
    yaml: str


@app.post("/api/parse_yaml")
def parse_yaml(req: ParseYamlRequest) -> dict:
    """Paste-to-import: same parse/validate path `/api/pipelines/{name}` GET already
    uses for a file on disk, just against pasted text instead of a saved YAML file."""
    try:
        raw = yaml.safe_load(req.yaml)
    except yaml.YAMLError as e:
        return {"ok": False, "error": f"invalid YAML: {e}"}
    if not isinstance(raw, dict):
        return {"ok": False, "error": "YAML must decode to a mapping (a pipeline config)"}
    try:
        cfg = load_config_dict(raw)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "config": cfg.model_dump(by_alias=True, exclude_none=True)}


class ValidateRequest(BaseModel):
    config: dict


@app.post("/api/validate")
def validate(req: ValidateRequest) -> dict:
    try:
        cfg = load_config_dict(req.config)
        return {"ok": True, "yaml": dump_config_yaml(cfg)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class RunRequest(BaseModel):
    config: dict
    # The saved config's name, which keys its run history; defaults to the config's own name.
    name: str | None = None


@app.post("/api/run")
async def run(req: RunRequest) -> dict:
    try:
        cfg = load_config_dict(req.config)
    except Exception as e:
        raise HTTPException(422, f"invalid config: {e}")

    log_lines: list[str] = []
    written: list[dict] = []
    buf = io.StringIO()
    started = datetime.now(timezone.utc)

    def record(**result) -> None:
        history.record_run(
            RUNS_ROOT, (req.name or "").strip() or cfg.name, trigger="editor",
            started=started, finished=datetime.now(timezone.utc),
            layers=written, log=log_lines, yaml_text=dump_config_yaml(cfg), **result,
        )

    try:
        with redirect_stdout(buf):
            out_path = await asyncio.to_thread(run_config, cfg, log_lines.append, written)
        record(ok=True, output=out_path)
        return {
            "ok": True,
            "output": out_path,
            "log": log_lines,
            "layers": written,
            "stdout": buf.getvalue(),
        }
    except Exception as e:
        record(ok=False, error=str(e))
        return {
            "ok": False,
            "error": str(e),
            "log": log_lines,
            "layers": written,
            "trace": traceback.format_exc(),
        }


@app.get("/api/runs/{name}")
def list_runs(name: str, limit: int = 50) -> dict:
    """The newest runs of a config, newest first: editor runs and scheduled CLI runs."""
    return {"runs": history.list_runs(RUNS_ROOT, name, limit=max(1, min(limit, history.MAX_RUNS)))}


@app.get("/api/runs/{name}/{run_id}")
def get_run(name: str, run_id: str) -> dict:
    record = history.get_run(RUNS_ROOT, name, run_id)
    if record is None:
        raise HTTPException(404, f"no run '{run_id}' for '{name}'")
    return record


class ExportScriptRequest(BaseModel):
    config: dict
    name: str = "pipeline"


def _render_export_script(name: str, yaml_text: str) -> str:
    """A standalone .py file that runs `yaml_text` via duck_soup, no UI/server needed."""
    embedded = yaml_text.replace('"""', '\\"\\"\\"')
    return f'''#!/usr/bin/env python3
"""{name} — Duck Soup pipeline, exported as a standalone script.

Run with:
    pip install duck-soup-etl   # or: pip install -e . from a duck_soup checkout
    python {name}.py

Relative source/output paths in the pipeline are resolved against the current working
directory when this script runs (not against this file's location) — same as
`duck_soup.cli run`.
"""
from __future__ import annotations

import sys

import yaml
from duck_soup.config import load_config_dict
from duck_soup.engine import run_config

PIPELINE_YAML = """\\
{embedded}
"""


def main() -> int:
    cfg = load_config_dict(yaml.safe_load(PIPELINE_YAML))
    out_path = run_config(cfg, log=lambda m: print(f"  {{m}}", flush=True))
    print(f"\\nWrote {{out_path}}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


@app.post("/api/export_script")
def export_script(req: ExportScriptRequest) -> dict:
    try:
        cfg = load_config_dict(req.config)
    except Exception as e:
        raise HTTPException(422, f"invalid config: {e}")
    yaml_text = dump_config_yaml(cfg)
    script = _render_export_script(req.name, yaml_text)
    return {"ok": True, "script": script, "filename": f"{req.name}.py"}


class PreviewRequest(BaseModel):
    config: dict
    pipeline_idx: int = 0
    # No upper bound: the editor warns above 20 000 features that a big GeoJSON
    # payload may make the browser slow or unresponsive, but lets the user go ahead.
    limit: int = Field(default=1000, ge=1)
    preview_until_step: int | None = None
    # Optional WGS84 bbox (west, south, east, north) — e.g. the current map viewport —
    # to restrict the preview to, instead of an arbitrary first-N-rows slice.
    bbox: tuple[float, float, float, float] | None = None
    # With preview_until_step: show that step's rejects instead of the rows it passes on.
    rejects: bool = False


@app.post("/api/preview")
def preview(req: PreviewRequest) -> dict:
    try:
        cfg = load_config_dict(req.config)
    except Exception as e:
        raise HTTPException(422, f"invalid config: {e}")

    pending = _pending_response(cfg.pipelines[min(req.pipeline_idx, len(cfg.pipelines) - 1)].sources)
    if pending:
        return pending
    try:
        rows = preview_config_pipeline(
            cfg, pipeline_idx=req.pipeline_idx, limit=req.limit,
            preview_until_step=req.preview_until_step, bbox=req.bbox, rejects=req.rejects,
        )
        # Sanitize values to ensure JSON serializability (handles non-UTF-8 strings, bytes, etc.)
        safe_rows = []
        for row in rows:
            safe_row = {}
            for k, v in row.items():
                if isinstance(v, (bytes, bytearray, memoryview)):
                    safe_row[k] = v.hex() if isinstance(v, (bytes, bytearray)) else bytes(v).hex()
                elif isinstance(v, str):
                    safe_row[k] = v.encode("utf-8", errors="replace").decode("utf-8")
                else:
                    safe_row[k] = v
            safe_rows.append(safe_row)
        return {"ok": True, "rows": safe_rows}
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "trace": traceback.format_exc(),
        }


class PlanRequest(BaseModel):
    config: dict
    pipeline_idx: int = 0


@app.post("/api/plan")
def plan(req: PlanRequest) -> dict:
    """The SQL behind each view of one pipeline, for the editor's SQL tab. Reads no data."""
    try:
        cfg = load_config_dict(req.config)
        return {"ok": True, "plan": plan_config_pipeline(cfg, pipeline_idx=req.pipeline_idx)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class CountsRequest(BaseModel):
    config: dict
    pipeline_idx: int = 0
    # Count over the first N base features only; None counts all the data (about a run's cost).
    limit: int | None = Field(default=1000, ge=1, le=1_000_000)
    bbox: tuple[float, float, float, float] | None = None


@app.post("/api/counts")
def counts(req: CountsRequest) -> dict:
    """Row counts after each step of one pipeline, for the lineage diagram's connections."""
    try:
        cfg = load_config_dict(req.config)
    except Exception as e:
        raise HTTPException(422, f"invalid config: {e}")
    pending = _pending_response(cfg.pipelines[min(req.pipeline_idx, len(cfg.pipelines) - 1)].sources)
    if pending:
        return pending
    try:
        return {"ok": True, **count_config_pipeline(
            cfg, pipeline_idx=req.pipeline_idx, limit=req.limit, bbox=req.bbox,
        )}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---- file browser ----
# Paths inside ROOT are reported relative to it (so pipelines stay portable); anything
# outside is reported absolute. All paths use forward slashes.
_HIDDEN_DIRS = {".git", ".venv", "__pycache__", ".gemini", ".idea", ".vscode", "node_modules"}


def _display_path(p: Path) -> str:
    if p.is_relative_to(ROOT):
        rel = str(p.relative_to(ROOT)).replace("\\", "/")
        return "" if rel == "." else rel
    return str(p).replace("\\", "/")


def _resolve_browse_dir(subpath: str) -> Path:
    if not subpath:
        return ROOT
    p = Path(subpath)
    return (p if p.is_absolute() else ROOT / subpath).resolve()


def _file_entry(p: Path, is_dir: bool | None = None) -> dict:
    if is_dir is None:
        is_dir = p.is_dir()
    size = modified = None
    try:
        st = p.stat()
        modified = st.st_mtime
        if not is_dir:
            size = st.st_size
    except OSError:
        pass
    return {"name": p.name, "path": _display_path(p), "is_dir": is_dir, "size": size, "modified": modified}


def _crumbs(target: Path) -> list[dict]:
    # Breadcrumb trail: inside ROOT it starts at ROOT itself; outside, at the filesystem anchor.
    chain = []
    for d in [target, *target.parents]:
        chain.append(d)
        if d == ROOT:
            break
    out = []
    for d in reversed(chain):
        name = d.name or str(d).replace("\\", "/").rstrip("/") or "/"
        out.append({"name": name, "path": _display_path(d)})
    return out


@app.get("/api/files")
def list_files(subpath: str = "") -> dict:
    target_dir = _resolve_browse_dir(subpath)
    if not target_dir.exists() or not target_dir.is_dir():
        raise HTTPException(404, "Directory not found")
    entries = []
    try:
        for p in target_dir.iterdir():
            if p.name in _HIDDEN_DIRS:
                continue
            try:
                is_dir = p.is_dir()
            except OSError:
                continue
            entries.append(_file_entry(p, is_dir))
    except PermissionError:
        raise HTTPException(403, "Permission denied")
    except Exception as e:
        raise HTTPException(500, str(e))
    entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))

    parent_dir = target_dir.parent
    parent = None if parent_dir == target_dir else _display_path(parent_dir)  # None at e.g. C:\
    return {
        "current": _display_path(target_dir),
        "parent": parent,
        "crumbs": _crumbs(target_dir),
        "entries": entries,
    }


@app.get("/api/files/places")
def file_places() -> dict:
    """Quick-access sidebar: the project folder and its usual subfolders, home, and drives."""
    places = [{"name": ROOT.name or str(ROOT), "path": "", "kind": "project"}]
    for sub in ("data", "pipelines", "output"):
        if (ROOT / sub).is_dir():
            places.append({"name": sub, "path": sub, "kind": sub})
    home = Path.home()
    if home != ROOT:
        places.append({"name": "home", "path": _display_path(home), "kind": "home"})
    if os.name == "nt":
        drives = [f"{c}:/" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.exists(f"{c}:\\")]
    else:
        drives = ["/"]
    places += [{"name": d.rstrip("/") or "/", "path": d, "kind": "drive"} for d in drives]
    return {"places": places}


@app.get("/api/files/search")
def search_files(q: str, subpath: str = "", limit: int = 200) -> dict:
    """Case-insensitive name search below `subpath`, breadth-first so nearby matches come
    first. Bounded by entry count and time, so searching from e.g. a drive root returns
    partial results (`truncated`) instead of hanging."""
    needle = q.strip().lower()
    root = _resolve_browse_dir(subpath)
    if not needle or not root.is_dir():
        return {"results": [], "truncated": False}
    deadline = time.monotonic() + 3.0
    scanned, max_scan = 0, 50_000
    results: list[dict] = []
    queue = [root]
    truncated = False
    while queue:
        d = queue.pop(0)
        try:
            with os.scandir(d) as it:
                children = sorted(it, key=lambda e: e.name.lower())
        except OSError:
            continue
        for e in children:
            if e.name in _HIDDEN_DIRS:
                continue
            scanned += 1
            try:
                is_dir = e.is_dir()
            except OSError:
                continue
            if needle in e.name.lower():
                entry = _file_entry(Path(e.path), is_dir)
                entry["folder"] = _display_path(Path(e.path).parent)
                results.append(entry)
            # A File Geodatabase is a directory, but a leaf data source â€” not worth descending.
            if is_dir and not e.name.lower().endswith(".gdb") and not e.is_symlink():
                queue.append(Path(e.path))
        if len(results) >= limit or scanned >= max_scan or time.monotonic() > deadline:
            truncated = bool(queue) or len(results) >= limit
            break
    return {"results": results[:limit], "truncated": truncated}


_UPLOAD_DIR = Path(tempfile.gettempdir()) / "duck_soup_uploads"


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...), relpath: str = Form("")) -> dict:
    try:
        upload_dir = _UPLOAD_DIR
        upload_dir.mkdir(parents=True, exist_ok=True)
        # `relpath` lets a multi-file source (e.g. a File Geodatabase folder, uploaded as
        # many internal files by the frontend's directory-drop handler) land together under
        # one subdirectory instead of flat in the upload dir. It's client-supplied, so it's
        # resolved and checked against `upload_dir` rather than trusted outright — otherwise a
        # crafted relpath like "../../../etc/passwd" could write outside the upload directory.
        rel = Path(relpath.strip() or file.filename)
        if rel.is_absolute() or ".." in rel.parts:
            return {"ok": False, "error": "invalid relative path"}
        target_path = (upload_dir / rel).resolve()
        if not target_path.is_relative_to(upload_dir.resolve()):
            return {"ok": False, "error": "invalid relative path"}
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with target_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        return {"ok": True, "path": str(target_path)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
class InspectFileRequest(BaseModel):
    uri: str
    format: str


@app.post("/api/inspect_file")
def inspect_file(req: InspectFileRequest) -> dict:
    uri = req.uri.strip()
    fmt = req.format.strip()
    if not uri:
        return {"ok": True, "layers": [], "default_crs": None}

    # Resolve local paths (postgres sources carry a libpq DSN, not a filesystem path)
    resolved_path = Path(uri) if fmt != "postgres" else None
    if fmt != "postgres" and not resolved_path.is_absolute() and not (
        uri.startswith("http://") or uri.startswith("https://") or uri.upper().startswith("WFS:")
    ):
        resolved_path = (ROOT / uri).resolve()

    with DUCKDB_LOCK:
        con = duckdb.connect()
        try:
            load_extensions(con)

            if fmt == "postgres":
                layers, default_crs = list_postgres_tables(con, uri)
                return {"ok": True, "layers": layers, "default_crs": default_crs}

            fwd_path = str(resolved_path).replace("\\", "/")
            if fmt == "csv":
                # A csv has exactly one layer, named after the file; skip ST_Read_Meta, which
                # makes GDAL scan the whole file (seconds for a large one).
                # A remote one is named after its download (see sources.remote_file_name).
                local = remote_file_name(uri, fmt) if should_download(uri, fmt) else fwd_path
                return {"ok": True, "layers": [Path(local).stem], "default_crs": None}
            if fmt == "json":
                # Read with DuckDB's read_json (see sources.py), not GDAL: no layers, no CRS.
                return {"ok": True, "layers": [], "default_crs": None}
            if fmt == "parquet":
                # Native read_parquet(), not GDAL's ST_Read_Meta (no Parquet driver in the
                # bundled GDAL build — see sources.py's module docstring). No layer concept
                # for a single flat Parquet file, so just report the CRS if one is embedded.
                _name, crs = parquet_geometry_info(con, fwd_path)
                return {"ok": True, "layers": [], "default_crs": crs}
            elif fmt == "wfs" or uri.upper().startswith("WFS:"):
                layers, default_crs = list_wfs_layers(uri)
                return {"ok": True, "layers": layers, "default_crs": default_crs}
            elif fmt == "oapif":
                # fetch_oapif() always requests the default CRS84 response.
                return {"ok": True, "layers": list_oapif_collections(uri), "default_crs": "EPSG:4326"}
            elif fmt == "arcgis_rest":
                # fetch_arcgis_rest() always forces outSR=4326.
                return {"ok": True, "layers": list_arcgis_rest_layers(uri), "default_crs": "EPSG:4326"}

            if should_download(uri, fmt):
                pending = _pending_response([Source(id="inspect", format=fmt, uri=uri)])
                if pending:
                    return {**pending, "layers": [], "default_crs": None}
                target = fetch_remote_file(uri, fmt)  # ready: a cache hit, no download
            elif uri.startswith(("http://", "https://")):
                target = uri
            else:
                target = fwd_path
            layers, default_crs = list_gdal_layers(con, target)
            return {"ok": True, "layers": layers, "default_crs": default_crs}
        except Exception as e:
            return {"ok": False, "error": str(e), "layers": [], "default_crs": None}
        finally:
            con.close()



app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
