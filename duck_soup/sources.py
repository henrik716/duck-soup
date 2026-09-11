"""Turn a Source config into something DuckDB can read.

Almost everything goes through DuckDB's ST_Read (which uses GDAL), so adding a
new vector format is usually just a new branch that builds the right GDAL
connection string. ArcGIS REST is the exception: it is fetched with pagination
to a temporary GeoJSON file, which is then read with ST_Read like anything else.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import warnings
from pathlib import Path
from typing import Callable

import duckdb
import requests

from .config import Source

# GDAL geometry-type labels that DuckDB's spatial extension cannot parse: the ISO
# SQL/MM curve types (CircularString, CompoundCurve, CurvePolygon, MultiCurve,
# MultiSurface). DuckDB's GEOMETRY only supports the OGC simple-feature linear
# types, so these need to be linearized (approximated with line segments) before
# ST_Read can touch them.
_CURVE_MARKERS = ("curve", "surface")

# Linearizing a curve layer (see _linearize_curves) round-trips the whole layer
# through GDAL — cheap for a one-off run, but the web editor re-previews on every
# edit and every map pan (bbox mode), so redoing it from scratch each time is the
# difference between an instant preview and a multi-hundred-ms-per-request one.
# Cached here (outside the per-call `workdir`, which is deleted at the end of
# every single preview/run) and keyed by the source file's mtime+size, so an
# edited source file invalidates its own cache entry automatically.
_LINEARIZE_CACHE_DIR = Path(tempfile.gettempdir()) / "duck_soup_linearize_cache"


def _sql_str(value: str) -> str:
    """Single-quote a value for inlining into SQL."""
    return "'" + value.replace("'", "''") + "'"


def _sql_ident(name: str) -> str:
    """Double-quote a SQL identifier."""
    return '"' + name.replace('"', '""') + '"'


def _st_read(uri: str, layer: str | None = None, extra: str = "") -> str:
    args = [_sql_str(uri)]
    if layer:
        args.append(f"layer={_sql_str(layer)}")
    if extra:
        args.append(extra)
    return f"ST_Read({', '.join(args)})"


def fetch_wfs(src: Source, workdir: Path) -> str:
    """Fetch a WFS typename via GetFeature (GML) and write a temp .gml file.

    WFS returns GML/XML. Fetches all features in a single request and lets
    GDAL/ST_Read parse the GML file. For very large layers consider the
    OGC API Features format instead, which natively returns GeoJSON.
    """
    from urllib.parse import urlparse, urlencode, parse_qsl

    base = src.uri
    if base.upper().startswith("WFS:"):
        base = base[4:]

    p = urlparse(base)
    qs_base = [(k, v) for k, v in parse_qsl(p.query)
               if k.lower() not in ("request", "service", "version", "typenames", "typename",
                                    "count", "startindex", "outputformat")]

    params = qs_base + [
        ("SERVICE", "WFS"), ("VERSION", "2.0.0"), ("REQUEST", "GetFeature"),
        ("TYPENAMES", src.layer or ""),
    ]
    url = p._replace(query=urlencode(params)).geturl()
    resp = requests.get(url, timeout=300)
    resp.raise_for_status()
    out_path = workdir / f"{src.id}.gml"
    out_path.write_bytes(resp.content)
    return str(out_path)


def fetch_arcgis_rest(src: Source, workdir: Path) -> str:
    """Page through an ArcGIS REST query endpoint and write a GeoJSON file.

    `src.uri` should be the layer endpoint, e.g.
    https://host/arcgis/rest/services/Foo/FeatureServer/0
    Returns the path to the written GeoJSON file (CRS is forced to EPSG:4326).
    """
    base_url = src.uri.rstrip("/")
    query_url = base_url + "/query"
    out_path = workdir / f"{src.id}.geojson"

    features: list[dict] = []
    offset = 0
    while True:
        params = {
            "where": src.where,
            "outFields": "*",
            "f": "geojson",
            "outSR": 4326,
            "resultOffset": offset,
            "resultRecordCount": src.page_size,
            "returnGeometry": "true",
        }
        resp = requests.get(query_url, params=params, timeout=120)
        resp.raise_for_status()
        payload = resp.json()
        batch = payload.get("features", [])
        features.extend(batch)
        if len(batch) < src.page_size or payload.get("exceededTransferLimit") is False:
            break
        if not batch:
            break
        offset += len(batch)

    fc = {"type": "FeatureCollection", "features": features}
    out_path.write_text(json.dumps(fc), encoding="utf-8")
    return str(out_path)


def _layer_geometry_field(con: duckdb.DuckDBPyConnection, uri: str, layer: str | None) -> dict | None:
    """Look up a layer's native geometry field (name + type) via ST_Read_Meta, without reading features."""
    try:
        (layers,) = con.execute(f"SELECT layers FROM ST_Read_Meta({_sql_str(uri)})").fetchone()
    except Exception:
        return None
    for lyr in layers or []:
        if layer and lyr["name"] != layer:
            continue
        for gf in lyr["geometry_fields"]:
            return gf
    return None


def _linearize_cache_path(uri: str, layer: str | None) -> Path | None:
    """Cache path for this (file, layer), or None if `uri` isn't a local file we can stat."""
    try:
        st = os.stat(uri)
    except OSError:
        return None
    key = f"{Path(uri).resolve()}|{layer or ''}|{st.st_mtime_ns}|{st.st_size}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return _LINEARIZE_CACHE_DIR / f"{digest}.gpkg"


def _linearize_curves(
    uri: str,
    layer: str | None,
    workdir: Path,
    source_id: str,
    log: Callable[[str], None] | None,
) -> tuple[str, str]:
    """Read a curve-geometry layer and rewrite it as linear geometry in a GeoPackage.

    DuckDB's spatial extension can't parse WKB for the ISO curve types (see
    _CURVE_MARKERS) — GDAL reads them fine, it's DuckDB's own WKB import that rejects
    them. Round-tripping through pyogrio (a separate, self-contained GDAL build) forces
    the curves to their linear approximation on the way out, the same thing
    `ogr2ogr -nlt CONVERT_TO_LINEAR` does. The result is cached (see
    _LINEARIZE_CACHE_DIR) since this is redone on every preview request otherwise.
    """
    out_layer = layer or source_id
    cache_path = _linearize_cache_path(uri, layer)
    if cache_path and cache_path.exists():
        return str(cache_path), out_layer

    try:
        import pyogrio
    except ImportError as e:
        raise RuntimeError(
            f"source '{source_id}' has curve geometry (circular arcs / compound curves) "
            "that DuckDB's spatial engine can't read directly. Install the optional "
            '"curves" extra to enable automatic linearization: pip install -e ".[curves]"'
        ) from e

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        meta, _fids, geometry, field_data = pyogrio.raw.read(uri, layer=layer)

    # Write to workdir first and move into place, so a request that reads the cache
    # concurrently with one still writing it never sees a partially-written file.
    tmp_out = workdir / f"{source_id}_linearized.gpkg"
    pyogrio.raw.write(
        str(tmp_out), geometry, field_data, fields=meta["fields"],
        layer=out_layer, crs=meta["crs"], geometry_type=meta["geometry_type"],
        encoding=meta["encoding"],
    )
    if log:
        msg = f"source '{source_id}': linearized curve geometry -> {meta['geometry_type']}"
        if any("measured" in str(w.message).lower() for w in caught):
            msg += " (M/measure dimension dropped, not supported downstream)"
        log(msg)

    if cache_path:
        _LINEARIZE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp_out), str(cache_path))
        return str(cache_path), out_layer
    return str(tmp_out), out_layer


def _maybe_linearize(
    uri: str,
    layer: str | None,
    workdir: Path,
    source_id: str,
    con: duckdb.DuckDBPyConnection | None,
    log: Callable[[str], None] | None,
) -> tuple[str, str | None]:
    if con is None:
        return uri, layer
    gfield = _layer_geometry_field(con, uri, layer)
    gtype = gfield["type"] if gfield else None
    if gtype and any(marker in gtype.lower() for marker in _CURVE_MARKERS):
        return _linearize_curves(uri, layer, workdir, source_id, log)
    return uri, layer


def _read_expr_normalized(
    uri: str,
    layer: str | None,
    con: duckdb.DuckDBPyConnection | None,
) -> str:
    """ST_Read(...) for this file, with its geometry field (if any) aliased to `geom`.

    engine.py hard-codes the geometry column as `geom` everywhere (source views, joins,
    the final mapped output) — a reasonable assumption for GeoJSON/most GDAL output, but
    plenty of real GeoPackages name it whatever the authoring tool used (Esri's `shape`
    is common; some of the Bane NOR layers in this repo use it). Without this rename,
    `SELECT * EXCLUDE (geom), ... FROM ST_Read(...)` fails at the first source view with
    "Column geom in EXCLUDE list not found in FROM clause" for any such source.
    """
    base = _st_read(uri, layer=layer)
    if con is None:
        return base
    gfield = _layer_geometry_field(con, uri, layer)
    name = gfield["name"] if gfield else None
    if not name or name == "geom":
        return base
    return f"(SELECT * EXCLUDE ({_sql_ident(name)}), {_sql_ident(name)} AS geom FROM {base})"


def read_expr(
    src: Source,
    workdir: Path,
    con: duckdb.DuckDBPyConnection | None = None,
    log: Callable[[str], None] | None = None,
) -> str:
    """Return a SQL table expression (ST_Read(...)) for this source.

    When `con` (a DuckDB connection with the spatial extension loaded) is given,
    file-based sources are checked for ISO curve geometry (which DuckDB can't parse)
    and transparently linearized first — see `_maybe_linearize` — and their geometry
    field is aliased to `geom` if it isn't already — see `_read_expr_normalized`.
    """
    fmt = src.format

    if fmt == "arcgis_rest":
        path = fetch_arcgis_rest(src, workdir)
        return _st_read(path)

    if fmt == "wfs":
        path = fetch_wfs(src, workdir)
        uri, layer = _maybe_linearize(path, None, workdir, src.id, con, log)
        return _read_expr_normalized(uri, layer, con)

    if fmt in ("gpkg", "fgdb", "shp", "geojson", "gml", "parquet", "xlsx", "csv"):
        # GDAL picks the driver from the path/extension; layer/sheet is optional.
        uri, layer = src.uri, src.layer
        if fmt in ("gpkg", "fgdb", "gml"):
            uri, layer = _maybe_linearize(uri, layer, workdir, src.id, con, log)
        return _read_expr_normalized(uri, layer, con)

    raise ValueError(f"unsupported source format: {fmt}")
