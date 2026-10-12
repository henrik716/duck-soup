"""Files read with ST_Read (GDAL): the ST_Read expression, layer listing, and the two fixes
applied before engine.py sees a file: curve geometry linearized, and the geometry column
aliased to `geom`."""
from __future__ import annotations

import hashlib
import os
import shutil
import warnings
from pathlib import Path
from typing import Callable

import duckdb

from ..sql_util import quote_ident as _sql_ident, quote_literal as _sql_str
from . import settings
from .crs import _normalize_crs

# GDAL geometry-type labels that DuckDB's spatial extension cannot parse: the ISO
# SQL/MM curve types (CircularString, CompoundCurve, CurvePolygon, MultiCurve,
# MultiSurface). DuckDB's GEOMETRY only supports the OGC simple-feature linear
# types, so these need to be linearized (approximated with line segments) before
# ST_Read can touch them.
_CURVE_MARKERS = ("curve", "surface")


def _st_read(uri: str, layer: str | None = None, extra: str = "") -> str:
    args = [_sql_str(uri)]
    if layer:
        args.append(f"layer={_sql_str(layer)}")
    if extra:
        args.append(extra)
    return f"ST_Read({', '.join(args)})"


def list_gdal_layers(
    con: duckdb.DuckDBPyConnection, target: str
) -> tuple[list[str], str | None]:
    """Layer names + first layer's CRS for anything GDAL can open, via ST_Read_Meta."""
    layers: list[str] = []
    default_crs: str | None = None
    res = con.execute(f"SELECT layers FROM ST_Read_Meta({_sql_str(target)})").fetchall()
    if res and len(res) > 0 and res[0][0]:
        for layer in res[0][0]:
            layer_name = layer.get("name")
            if layer_name:
                layers.append(layer_name)

            geom_fields = layer.get("geometry_fields")
            if geom_fields and isinstance(geom_fields, list) and len(geom_fields) > 0:
                crs_info = geom_fields[0].get("crs")
                if crs_info and isinstance(crs_info, dict):
                    auth_name = crs_info.get("auth_name")
                    auth_code = crs_info.get("auth_code")
                    if auth_name and auth_code and not default_crs:
                        default_crs = _normalize_crs(f"{auth_name}:{auth_code}")
    return layers, default_crs


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
    return settings.LINEARIZE_CACHE_DIR / f"{digest}.gpkg"


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
    settings.LINEARIZE_CACHE_DIR) since this is redone on every preview request otherwise.
    """
    out_layer = layer or source_id
    cache_path = _linearize_cache_path(uri, layer)
    if cache_path and cache_path.exists():
        return str(cache_path), out_layer

    import pyogrio

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
        settings.LINEARIZE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
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


def _geometry_column_via_describe(con: duckdb.DuckDBPyConnection, base: str) -> str | None:
    """Find a table expression's GEOMETRY-typed column by DESCRIBEing it, rather than via
    ST_Read_Meta. Used when ST_Read_Meta can't be trusted to report the field (see callers).
    """
    rows = con.execute(f"DESCRIBE SELECT * FROM {base}").fetchall()
    return next((r[0] for r in rows if str(r[1]).upper().startswith("GEOMETRY")), None)


def _read_expr_normalized(
    uri: str,
    layer: str | None,
    con: duckdb.DuckDBPyConnection | None,
    extra: str = "",
) -> str:
    """ST_Read(...) for this file, with its geometry field (if any) aliased to `geom`.

    engine.py hard-codes the geometry column as `geom` everywhere (source views, joins,
    the final mapped output) — a reasonable assumption for GeoJSON/most GDAL output, but
    plenty of real GeoPackages name it whatever the authoring tool used (Esri's `shape`
    is common; some of the Bane NOR layers in this repo use it). Without this rename,
    `SELECT * EXCLUDE (geom), ... FROM ST_Read(...)` fails at the first source view with
    "Column geom in EXCLUDE list not found in FROM clause" for any such source.

    `extra` (e.g. an `open_options=[...]` clause) only ever affects field *types* here, not
    whether a geometry field exists — none of this function's callers use it to conjure a
    geometry field into existence (see `_apply_tabular_geometry` for xlsx/csv geometry-from-
    columns, which is deliberately built in SQL afterwards instead, precisely to avoid that).

    ST_Read_Meta isn't reliable for every *format* GDAL itself opens fine, though — confirmed
    empirically for a File Geodatabase (an Esri "NVE" gdb export): `ST_Read_Meta(path)`
    returned zero rows outright, even though `ST_Read(path)` opened its layer and read rows
    without complaint. When that happens, `_layer_geometry_field` comes back empty and this
    used to silently skip the rename, so engine.py's hard-coded `EXCLUDE (geom)` blew up on
    the real column name (e.g. Esri's `SHAPE`) with a binder error. Falling back to the same
    DESCRIBE-based detection `parquet_geometry_info` already needs for Parquet covers this —
    it's slightly more work than the metadata lookup, but only for the formats where the
    metadata lookup already came back empty-handed.
    """
    base = _st_read(uri, layer=layer, extra=extra)
    if con is None:
        return base
    gfield = _layer_geometry_field(con, uri, layer)
    name = gfield["name"] if gfield else _geometry_column_via_describe(con, base)
    if not name or name == "geom":
        return base
    return f"(SELECT * EXCLUDE ({_sql_ident(name)}), {_sql_ident(name)} AS geom FROM {base})"
