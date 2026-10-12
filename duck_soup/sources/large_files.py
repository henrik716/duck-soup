"""Files too large for ST_Read on this platform, read through pyogrio instead."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import duckdb

from ..config import Source
from ..sql_util import quote_ident as _sql_ident, quote_literal as _sql_str
from . import settings
from .crs import _normalize_crs
from .formats import GDAL_VECTOR
from .gdal import _geometry_column_via_describe

def _local_file_size(uri: str) -> int:
    """Size in bytes of a local file, or of the largest file inside a directory source (a
    .gdb is a folder, and GDAL opens each of its files separately). 0 if `uri` isn't a
    local path we can stat (a URL, a /vsi path, ...)."""
    p = Path(uri)
    try:
        if p.is_dir():
            return max((f.stat().st_size for f in p.iterdir() if f.is_file()), default=0)
        return p.stat().st_size
    except OSError:
        return 0


def needs_pyogrio_reader(uri: str) -> bool:
    """True when `uri` is too large for ST_Read on this platform (see settings.ST_READ_MAX_BYTES)."""
    return settings.ST_READ_MAX_BYTES is not None and _local_file_size(uri) >= settings.ST_READ_MAX_BYTES


def large_file_reader_note(src: Source) -> str | None:
    """User-facing explanation when `src` will be read via pyogrio, else None."""
    if src.format not in GDAL_VECTOR or not needs_pyogrio_reader(src.uri):
        return None
    size_gib = _local_file_size(src.uri) / 2**30
    return (
        f"Large file ({size_gib:.1f} GiB): read via pyogrio instead of DuckDB's reader, which "
        f"crashes on files over 2 GiB on Windows. Join steps against this source load the "
        f"whole layer into memory."
    )


def _bbox_in_crs(
    con: duckdb.DuckDBPyConnection,
    bbox: tuple[float, float, float, float],
    crs: str,
) -> tuple[float, float, float, float]:
    """A WGS84 (west, south, east, north) bbox as the extent of the same envelope in `crs`.

    Transforms the same 4-corner envelope engine._bbox_filter does, so its extent always
    covers the polygon that filter later tests against.
    """
    west, south, east, north = bbox
    if crs == "EPSG:4326":
        return bbox
    row = con.execute(
        f"SELECT ST_XMin(e), ST_YMin(e), ST_XMax(e), ST_YMax(e) FROM (SELECT ST_Transform("
        f"ST_MakeEnvelope({west}, {south}, {east}, {north}), 'EPSG:4326', {_sql_str(crs)}, "
        f"always_xy := true) AS e)"
    ).fetchone()
    return tuple(row)


def _read_via_pyogrio(
    con: duckdb.DuckDBPyConnection,
    uri: str,
    layer: str | None,
    source_id: str,
    bbox: tuple[float, float, float, float] | None,
    max_features: int | None,
    log: Callable[[str], None] | None,
) -> str:
    """Load a layer into a DuckDB temp table via pyogrio's Arrow stream; returns the table name.

    The ST_Read-free path for files ST_Read can't handle (see settings.ST_READ_MAX_BYTES). pyogrio
    bundles its own GDAL with ordinary file I/O, and streams Arrow batches straight into
    DuckDB — which turns the GeoArrow WKB column into GEOMETRY on its own. A full 1.1M-feature
    layer of a 2.2 GiB GeoPackage loads in ~3 s. The result matches ST_Read's columns: the
    FID column is included under its own name unless it's already a regular field (GeoJSON's
    `id`), and the geometry column is renamed to `geom` (see _read_expr_normalized).

    `bbox` (WGS84) is pushed down as a GDAL spatial filter in the layer's own CRS, and
    `max_features` stops pulling batches once that many rows are in — the same bounded reads
    fetch_oapif does for a preview. Both are best-effort narrowing; the engine still applies
    its exact bbox filter and LIMIT afterwards.
    """
    import pyogrio

    info = pyogrio.read_info(uri, layer=layer)
    # A driver without a named FID column (FlatGeobuf) still gets one from ST_Read, as OGC_FID.
    return_fids = info.get("fid_column") not in list(info.get("fields", []))

    layer_bbox = None
    if bbox and info.get("crs"):
        try:
            layer_bbox = _bbox_in_crs(con, bbox, _normalize_crs(info["crs"]) or info["crs"])
        except Exception:
            layer_bbox = None  # unknown CRS to PROJ etc. — read unfiltered, engine still filters

    table = _sql_ident(f"__pyogrio_{source_id}")
    stream = f"__pyogrio_stream_{source_id}"
    limit = f" LIMIT {int(max_features)}" if max_features is not None else ""
    kwargs = {"batch_size": min(max_features, 65536)} if max_features else {}
    with pyogrio.raw.open_arrow(
        uri, layer=layer, bbox=layer_bbox, return_fids=return_fids, **kwargs
    ) as (_meta, reader):
        con.register(stream, reader)
        try:
            con.execute(
                f"CREATE OR REPLACE TEMP TABLE {table} AS "
                f"SELECT * FROM {_sql_ident(stream)}{limit}"
            )
        finally:
            con.unregister(stream)

    geom_col = _geometry_column_via_describe(con, table)
    if geom_col and geom_col != "geom":
        con.execute(f"ALTER TABLE {table} RENAME COLUMN {_sql_ident(geom_col)} TO geom")
    if log:
        log(f"source '{source_id}': file >= 2 GiB, read via pyogrio instead of ST_Read")
    return table
