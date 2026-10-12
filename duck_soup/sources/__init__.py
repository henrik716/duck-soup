"""Turn a Source config into something DuckDB can read.

Almost everything goes through DuckDB's ST_Read (which uses GDAL), so adding a
new vector format is usually just a new branch that builds the right GDAL
connection string. ArcGIS REST and OGC API - Features (oapif) are the exception:
both are fetched with pagination to a temporary GeoJSON file, which is then read
with ST_Read like anything else. Parquet is the other exception: the GDAL build
bundled with DuckDB's spatial extension has no Parquet/Arrow driver, so it's read
with DuckDB's own native read_parquet() instead (see read_expr's `parquet` branch).
postgres is a third exception, for the same reason: it's read via DuckDB's own
postgres extension (ATTACH ... TYPE postgres) rather than GDAL's PG driver, which
isn't part of the bundled spatial-extension GDAL build either.
Plain (non-GeoJSON) JSON is read with DuckDB's native read_json() — see `_json_read_expr`.
Finally, on Windows a local file of 2 GiB or more is read through pyogrio instead of
ST_Read, which segfaults on such files there — see `_read_via_pyogrio`.

The package splits that by concern: `remote` (downloads), `services` (WFS, ArcGIS REST,
OAPIF), `gdal` (ST_Read), `parquet`, `postgres`, `tabular` (xlsx/csv/json),
`large_files` (pyogrio), `crs`, `prepare` (the editor's background preparation) and
`settings` (cache folders and size thresholds). `read_expr` here picks between them.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import duckdb

from ..config import Source
from ..sql_util import quote_ident as _sql_ident, quote_literal as _sql_str
from . import settings
from .crs import GEOGRAPHIC_CRS, crs_extent_warning
from .formats import (
    GDAL_FILE,
    GDAL_TABULAR,
    GDAL_VECTOR,
    MAY_HAVE_CURVES,
    alias_geom,
)
from .gdal import _maybe_linearize, _read_expr_normalized, _st_read, list_gdal_layers
from .large_files import (
    _read_via_pyogrio,
    large_file_reader_note,
    needs_pyogrio_reader,
)
from .parquet import parquet_geometry_info
from .postgres import (
    _split_pg_layer,
    attach_postgres,
    list_postgres_tables,
    postgres_geometry_info,
)
from .prepare import cached_read_note, download_info, prepare_source
from .remote import (
    download_remote_sources,
    download_time,
    fetch_remote_file,
    fresh_downloads,
    is_remote_uri,
    latest_download,
    remote_file_name,
    should_download,
    source_progress,
)
from .services import (
    fetch_arcgis_rest,
    fetch_oapif,
    fetch_wfs,
    list_arcgis_rest_layers,
    list_oapif_collections,
    list_wfs_layers,
)
from .tabular import (
    _apply_tabular_geometry,
    _json_read_expr,
    _tabular_open_extra,
    _tabular_parquet_cache,
)

__all__ = [
    "GEOGRAPHIC_CRS", "attach_postgres", "cached_read_note", "crs_extent_warning",
    "download_info", "download_remote_sources", "download_time", "fetch_arcgis_rest",
    "fetch_oapif", "fetch_remote_file", "fetch_wfs", "fresh_downloads", "is_remote_uri",
    "large_file_reader_note", "latest_download", "list_arcgis_rest_layers",
    "list_gdal_layers", "list_oapif_collections", "list_postgres_tables", "list_wfs_layers",
    "needs_pyogrio_reader", "parquet_geometry_info", "postgres_geometry_info",
    "prepare_source", "read_expr", "remote_file_name", "settings", "should_download",
    "source_progress",
]


def read_expr(
    src: Source,
    workdir: Path,
    con: duckdb.DuckDBPyConnection | None = None,
    log: Callable[[str], None] | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    sample: bool = False,
    max_features: int | None = None,
) -> str:
    """Return a SQL table expression (ST_Read(...)) for this source.

    When `con` (a DuckDB connection with the spatial extension loaded) is given,
    file-based sources are checked for ISO curve geometry (which DuckDB can't parse)
    and transparently linearized first — see `_maybe_linearize` — and their geometry
    field is aliased to `geom` if it isn't already — see `_read_expr_normalized`.

    `bbox` and `max_features` are only honored for `oapif` sources — see `fetch_oapif` — and
    for files read via pyogrio instead of ST_Read — see `_read_via_pyogrio`.
    `sample` is honored by `oapif`, `arcgis_rest` and the pyogrio path.
    """
    fmt = src.format

    if fmt == "arcgis_rest":
        path = fetch_arcgis_rest(src, workdir, sample=sample)
        return _st_read(path)

    if fmt == "oapif":
        path = fetch_oapif(src, workdir, bbox=bbox, sample=sample, max_features=max_features)
        return _st_read(path)

    if fmt == "wfs":
        path = fetch_wfs(src, workdir)
        uri, layer = _maybe_linearize(path, None, workdir, src.id, con, log)
        return _read_expr_normalized(uri, layer, con)

    if fmt == "parquet":
        # No layer concept for a single flat Parquet file, and no GDAL curve types to
        # worry about — this path never touches GDAL at all (see module docstring).
        base = f"read_parquet({_sql_str(src.uri)})"
        if con is None:
            return base
        name, _crs = parquet_geometry_info(con, src.uri)
        return alias_geom(base, name)

    if fmt == "postgres":
        # No GDAL involved at all here either — see module docstring.
        if con is None:
            raise ValueError(f"source '{src.id}': postgres sources require a live DuckDB connection")
        schema, table = _split_pg_layer(src.layer)
        alias = attach_postgres(con, src.uri, src.id)
        base = f"{_sql_ident(alias)}.{_sql_ident(schema)}.{_sql_ident(table)}"
        geom_col, _crs = postgres_geometry_info(con, alias, schema, table)
        if not geom_col:
            return base
        g = _sql_ident(geom_col)
        # The postgres extension doesn't recognize PostGIS's `geometry` type, so it comes
        # through as VARCHAR hex-EWKB text (PostGIS's default text output for that column) —
        # ST_GeomFromHEXWKB parses that directly, same as the xlsx/csv geom_field branch below.
        geom = f"CASE WHEN {g} IS NULL THEN NULL ELSE TRY(ST_GeomFromHEXWKB({g})) END"
        return alias_geom(base, geom_col, geom)

    if fmt == "json":
        # Native read_json(), not GDAL: its GeoJSON driver only takes FeatureCollections.
        uri = src.uri
        if con is not None and should_download(uri, fmt):
            uri = fetch_remote_file(uri, fmt, log, progress_key=src.uri)
        base = _json_read_expr(uri, src.records)
        if not (src.x_field or src.geom_field):
            return base
        column_types = None
        if con is not None:
            column_types = {
                r[0]: str(r[1]) for r in con.execute(f"DESCRIBE SELECT * FROM {base}").fetchall()
            }
        return _apply_tabular_geometry(base, src, column_types)

    if fmt in GDAL_FILE:
        # GDAL picks the driver from the path/extension; layer/sheet is optional.
        uri, layer = src.uri, src.layer
        if con is not None and should_download(uri, fmt):
            uri = fetch_remote_file(uri, fmt, log, progress_key=src.uri)
        if fmt in MAY_HAVE_CURVES:
            uri, layer = _maybe_linearize(uri, layer, workdir, src.id, con, log)
        if con is not None and fmt in GDAL_VECTOR and needs_pyogrio_reader(uri):
            limit = settings.PYOGRIO_SAMPLE_ROWS if sample and max_features is None else max_features
            return _read_via_pyogrio(con, uri, layer, src.id, bbox, limit, log)
        extra = _tabular_open_extra(src)
        base = None
        if con is not None and fmt in GDAL_TABULAR:
            base = _tabular_parquet_cache(con, uri, layer, extra, log, progress_key=src.uri)
        if base is None:
            base = _read_expr_normalized(uri, layer, con, extra=extra)
        if fmt in GDAL_TABULAR and (src.x_field or src.geom_field):
            base = _apply_tabular_geometry(base, src)
        return base

    raise ValueError(f"unsupported source format: {fmt}")
