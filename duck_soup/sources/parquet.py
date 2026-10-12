"""(Geo)Parquet sources, read with DuckDB's native read_parquet() rather than GDAL."""
from __future__ import annotations

import re

import duckdb

from ..sql_util import quote_literal as _sql_str
from .crs import _normalize_crs


def parquet_geometry_info(con: duckdb.DuckDBPyConnection, uri: str) -> tuple[str | None, str | None]:
    """(geometry_column_name, embedded_crs) for a (Geo)Parquet file's native schema.

    DuckDB reads Parquet directly via read_parquet(), not through GDAL (see read_expr's
    `parquet` branch for why), and the spatial extension recognizes GeoParquet's `geo`
    key-value metadata and types the geometry column as GEOMETRY — or, when the file's
    metadata names a CRS, as GEOMETRY('EPSG:25833'), with the CRS string embedded right
    in the DuckDB type. Returns (None, None) if there's no geometry column, the file has
    no CRS in its metadata, or the file can't be read at all.

    GeoParquet files that omit an explicit CRS default to OGC:CRS84 per the spec — very
    common (e.g. any file written without deliberately setting a CRS) — which DuckDB
    reports verbatim as GEOMETRY('OGC:CRS84'). That's WGS84 lon/lat, the same CRS this
    codebase already treats as EPSG:4326 everywhere else CRS84 shows up (see fetch_oapif,
    Source._check_oapif_crs), just under a non-numeric authority:code the app's CRS field
    can't accept (config.CRSStr requires a numeric code) — so it's normalized here. Any
    other CRS identifier that doesn't fit that AUTHORITY:NUMERIC_CODE shape is dropped
    (None) rather than handed back to the editor as a value it will reject.
    """
    try:
        rows = con.execute(f"DESCRIBE SELECT * FROM read_parquet({_sql_str(uri)})").fetchall()
    except Exception:
        return None, None
    for name, col_type, *_ in rows:
        col_type = str(col_type)
        if col_type.upper().startswith("GEOMETRY"):
            m = re.match(r"GEOMETRY\('([^']+)'\)", col_type, re.IGNORECASE)
            crs = _normalize_crs(m.group(1) if m else None)
            return name, crs
    return None, None
