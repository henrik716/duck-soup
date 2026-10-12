"""Which formats each reader handles, and the `geom` column every reader hands engine.py."""
from __future__ import annotations

from ..sql_util import quote_ident as _sql_ident

# Vector files GDAL opens with ST_Read, and pyogrio for a file too large for it (see
# large_files.py).
GDAL_VECTOR = ("gpkg", "fgdb", "shp", "geojson", "gml", "flatgeobuf")

# Spreadsheets GDAL opens with ST_Read. Unlike the vector files: header-row open options,
# geometry built from columns in SQL, and a Parquet copy once large (see tabular.py). Left
# on ST_Read for any size, since all three are ST_Read-specific.
GDAL_TABULAR = ("xlsx", "csv")

# Files read with ST_Read, from a local copy when the uri is remote.
GDAL_FILE = GDAL_VECTOR + GDAL_TABULAR

# Downloaded first when their uri is remote (see remote.should_download).
DOWNLOADED = GDAL_FILE + ("json",)

# Can hold ISO curve geometry, linearized before DuckDB reads it (see gdal._maybe_linearize).
MAY_HAVE_CURVES = ("gpkg", "fgdb", "gml")


def alias_geom(base: str, column: str | None, expr: str | None = None) -> str:
    """`base` with its geometry column `column` renamed to `geom`, the name engine.py reads
    it by everywhere, or replaced by `expr` (SQL computing the geometry from that column).
    `base` itself when it has no geometry column, or it's `geom` already with nothing to
    compute."""
    if not column or (column == "geom" and expr is None):
        return base
    col = _sql_ident(column)
    return f"(SELECT * EXCLUDE ({col}), {expr or col} AS geom FROM {base})"
