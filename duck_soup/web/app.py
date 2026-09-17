"""Web backend for the Duck Soup pipeline editor.

Run: uvicorn duck_soup.web.app:app --reload
Then open http://127.0.0.1:8000
"""
from __future__ import annotations

import asyncio
import io
import os
import tempfile
import traceback
from contextlib import redirect_stdout
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
from ..engine import run_config, preview_config_pipeline
from ..sources import read_expr, parquet_geometry_info, attach_postgres, _sql_ident
from ..derive import DUCKDB_LOCK, register_udfs

ROOT = Path(os.environ.get("DUCK_SOUP_ROOT", Path.cwd()))
PIPELINE_DIR = ROOT / "pipelines"
STATIC_DIR = Path(__file__).resolve().parent / "static"

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
        "step_types": ["spatial_join", "attribute_join", "nearest_neighbor", "buffer", "centroid", "clip", "erase", "dissolve", "intersect_overlay", "filter", "merge", "snapshot"],
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


@app.post("/api/inspect")
def inspect_source(req: InspectRequest) -> dict:
    try:
        src = Source.model_validate(req.source)
    except Exception as e:
        return {"ok": False, "error": f"Invalid source config: {e}"}

    try:
        with DUCKDB_LOCK:
            con = duckdb.connect()
            try:
                con.execute("INSTALL spatial; LOAD spatial;")
                con.execute("INSTALL postgres; LOAD postgres;")
                register_udfs(con)
                with tempfile.TemporaryDirectory() as tmp:
                    workdir = Path(tmp)
                    read = read_expr(src, workdir, con=con, sample=True)
                    res = con.execute(f"DESCRIBE SELECT * FROM {read}").fetchall()
                    columns = [{"name": r[0], "type": str(r[1])} for r in res]
                    return {"ok": True, "columns": columns}
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


@app.post("/api/run")
async def run(req: RunRequest) -> dict:
    try:
        cfg = load_config_dict(req.config)
    except Exception as e:
        raise HTTPException(422, f"invalid config: {e}")

    log_lines: list[str] = []
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            out_path = await asyncio.to_thread(run_config, cfg, log_lines.append)
        return {
            "ok": True,
            "output": out_path,
            "log": log_lines,
            "stdout": buf.getvalue(),
        }
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "log": log_lines,
            "trace": traceback.format_exc(),
        }


class ExportScriptRequest(BaseModel):
    config: dict
    name: str = "pipeline"


def _render_export_script(name: str, yaml_text: str) -> str:
    """A standalone .py file that runs `yaml_text` via duck_soup, no UI/server needed."""
    embedded = yaml_text.replace('"""', '\\"\\"\\"')
    return f'''#!/usr/bin/env python3
"""{name} — Duck Soup pipeline, exported as a standalone script.

Run with:
    pip install duck_soup   # or: pip install -e . from a duck_soup checkout
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
    # Capped server-side (not just the frontend default) so a source with millions
    # of features can't be asked to ship an unbounded GeoJSON payload to the browser.
    limit: int = Field(default=1000, ge=1, le=20000)
    preview_until_step: int | None = None
    # Optional WGS84 bbox (west, south, east, north) — e.g. the current map viewport —
    # to restrict the preview to, instead of an arbitrary first-N-rows slice.
    bbox: tuple[float, float, float, float] | None = None


@app.post("/api/preview")
def preview(req: PreviewRequest) -> dict:
    try:
        cfg = load_config_dict(req.config)
    except Exception as e:
        raise HTTPException(422, f"invalid config: {e}")

    try:
        rows = preview_config_pipeline(
            cfg, pipeline_idx=req.pipeline_idx, limit=req.limit,
            preview_until_step=req.preview_until_step, bbox=req.bbox,
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




@app.get("/api/files")
def list_files(subpath: str = "") -> dict:
    if not subpath:
        target_dir = ROOT
    else:
        p = Path(subpath)
        target_dir = p if p.is_absolute() else (ROOT / subpath).resolve()

    if not target_dir.exists() or not target_dir.is_dir():
        raise HTTPException(404, "Directory not found")
    entries = []
    try:
        for p in target_dir.iterdir():
            if p.name in {".git", ".venv", "__pycache__", ".gemini", ".idea", ".vscode"}:
                continue
            inside_root = p.is_relative_to(ROOT)
            path_str = (
                str(p.relative_to(ROOT)).replace("\\", "/")
                if inside_root
                else str(p).replace("\\", "/")
            )
            entries.append({"name": p.name, "path": path_str, "is_dir": p.is_dir()})
    except Exception as e:
        raise HTTPException(500, str(e))
    entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))

    parent_dir = target_dir.parent
    if parent_dir == target_dir:  # filesystem root (e.g. C:\)
        parent = None
    elif parent_dir.is_relative_to(ROOT):
        parent = str(parent_dir.relative_to(ROOT)).replace("\\", "/")
    else:
        parent = str(parent_dir).replace("\\", "/")

    if target_dir.is_relative_to(ROOT):
        current = str(target_dir.relative_to(ROOT)).replace("\\", "/")
    else:
        current = str(target_dir).replace("\\", "/")

    return {"current": current, "parent": parent, "entries": entries}


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
            con.execute("INSTALL spatial; LOAD spatial;")
            con.execute("INSTALL postgres; LOAD postgres;")
            layers = []
            default_crs = None

            if fmt == "postgres":
                # List PostGIS-enabled tables (from geometry_columns) plus plain tables
                # (usable as attribute-join lookup sources), each as "schema.table" unless
                # schema is the default 'public'. Default CRS is the first spatial table's SRID.
                alias = attach_postgres(con, uri, "inspect")
                geom_rows = con.execute(
                    f"SELECT f_table_schema, f_table_name, srid FROM {_sql_ident(alias)}.public.geometry_columns "
                    "ORDER BY f_table_schema, f_table_name"
                ).fetchall()
                seen: set[str] = set()
                for schema, table, srid in geom_rows:
                    full = table if schema == "public" else f"{schema}.{table}"
                    seen.add(full)
                    layers.append(full)
                    if default_crs is None and srid:
                        default_crs = f"EPSG:{srid}"
                table_rows = con.execute(
                    f"SELECT table_schema, table_name FROM {_sql_ident(alias)}.information_schema.tables "
                    "WHERE table_type = 'BASE TABLE' ORDER BY table_schema, table_name"
                ).fetchall()
                for schema, table in table_rows:
                    full = table if schema == "public" else f"{schema}.{table}"
                    if full not in seen:
                        seen.add(full)
                        layers.append(full)
                return {"ok": True, "layers": layers, "default_crs": default_crs}

            fwd_path = str(resolved_path).replace("\\", "/")
            sql_uri = "'" + fwd_path.replace("'", "''") + "'"
            if fmt == "parquet":
                # Native read_parquet(), not GDAL's ST_Read_Meta (no Parquet driver in the
                # bundled GDAL build — see sources.py's module docstring). No layer concept
                # for a single flat Parquet file, so just report the CRS if one is embedded.
                _name, crs = parquet_geometry_info(con, fwd_path)
                return {"ok": True, "layers": [], "default_crs": crs}
            elif fmt == "wfs" or uri.upper().startswith("WFS:"):
                # Fetch WFS layer list via GetCapabilities — more reliable than GDAL's WFS driver
                import requests as _requests
                from urllib.parse import urlparse, urlencode, parse_qsl
                from xml.etree import ElementTree
                raw = uri[4:] if uri.upper().startswith("WFS:") else uri
                p = urlparse(raw)
                qs_clean = [(k, v) for k, v in parse_qsl(p.query)
                            if k.lower() not in ("request", "service", "version")]
                qs_caps = urlencode(qs_clean + [("SERVICE", "WFS"), ("REQUEST", "GetCapabilities")])
                caps_url = p._replace(query=qs_caps).geturl()
                resp = _requests.get(caps_url, timeout=30)
                resp.raise_for_status()
                root = ElementTree.fromstring(resp.content)
                for ns in ("http://www.opengis.net/wfs/2.0", "http://www.opengis.net/wfs"):
                    for ft in root.iter(f"{{{ns}}}FeatureType"):
                        name_el = ft.find(f"{{{ns}}}Name")
                        if name_el is not None and name_el.text:
                            layers.append(name_el.text.strip())
                        if not default_crs:
                            # NB: `find(...) or find(...)` is wrong here — Element.__bool__ is
                            # based on child-element count, not identity, so a real match on a
                            # leaf element like <DefaultCRS>EPSG::4258</DefaultCRS> (no children)
                            # is falsy and silently discarded in favor of the second find().
                            crs_el = ft.find(f"{{{ns}}}DefaultCRS")
                            if crs_el is None:
                                crs_el = ft.find(f"{{{ns}}}DefaultSRS")
                            if crs_el is not None and crs_el.text:
                                raw_crs = crs_el.text.strip()
                                # Normalise urn:ogc:def:crs:EPSG::4258 → EPSG:4258
                                import re as _re
                                m = _re.search(r"EPSG[:_]+([\w]+)$", raw_crs, _re.IGNORECASE)
                                if m:
                                    default_crs = f"EPSG:{m.group(1)}"
                    if layers:
                        break
                return {"ok": True, "layers": layers, "default_crs": default_crs}
            elif fmt == "oapif":
                # List collections via the OGC API - Features JSON endpoint — the plain
                # URL without an Accept header returns the server's HTML front-end, not data.
                import requests as _requests
                resp = _requests.get(
                    f"{uri.rstrip('/')}/collections",
                    headers={"Accept": "application/json"},
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                layers = [c["id"] for c in data.get("collections", []) if c.get("id")]
                # fetch_oapif() always requests the default CRS84 response.
                return {"ok": True, "layers": layers, "default_crs": "EPSG:4326"}
            elif fmt == "arcgis_rest":
                # ArcGIS REST's service-info endpoint (?f=json) lists sublayers (and
                # standalone tables) directly.
                import requests as _requests
                resp = _requests.get(f"{uri.rstrip('/')}?f=json", timeout=30)
                resp.raise_for_status()
                data = resp.json()
                if "error" in data:
                    # ArcGIS REST returns HTTP 200 with {"error": {...}} for a bad url.
                    raise RuntimeError(data["error"].get("message", "ArcGIS REST service error"))
                for entry in (data.get("layers") or []) + (data.get("tables") or []):
                    lid = entry.get("id")
                    if lid is None:
                        continue
                    name = entry.get("name")
                    layers.append({"value": str(lid), "label": f"{lid} - {name}" if name else str(lid)})
                # fetch_arcgis_rest() always forces outSR=4326.
                return {"ok": True, "layers": layers, "default_crs": "EPSG:4326"}
            elif uri.startswith("http://") or uri.startswith("https://"):
                sql_uri = "'" + uri.replace("'", "''") + "'"

            res = con.execute(f"SELECT layers FROM ST_Read_Meta({sql_uri})").fetchall()
            if res and len(res) > 0 and res[0][0]:
                layers_list = res[0][0]
                for layer in layers_list:
                    layer_name = layer.get("name")
                    if layer_name:
                        layers.append(layer_name)

                    # Extract CRS of first layer if available
                    geom_fields = layer.get("geometry_fields")
                    if geom_fields and isinstance(geom_fields, list) and len(geom_fields) > 0:
                        crs_info = geom_fields[0].get("crs")
                        if crs_info and isinstance(crs_info, dict):
                            auth_name = crs_info.get("auth_name")
                            auth_code = crs_info.get("auth_code")
                            if auth_name and auth_code:
                                crs_str = f"{auth_name}:{auth_code}"
                                if not default_crs:
                                    default_crs = crs_str
            return {"ok": True, "layers": layers, "default_crs": default_crs}
        except Exception as e:
            return {"ok": False, "error": str(e), "layers": [], "default_crs": None}
        finally:
            con.close()



app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
