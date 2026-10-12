"""Writing a pipeline's layers to its output file: GeoPackage via GDAL, with every
layer after the first spliced in at the SQLite level, or GeoParquet via DuckDB's own
writer, one file per layer when there are several."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Callable

import duckdb

from .config import OutputLayer
from .sql_util import quote_ident as _ident, quote_literal as _lit


def parquet_output_root(out_path: Path, multi: bool) -> Path:
    """Where a GeoParquet output lands: the file itself for a single layer, otherwise
    the folder `<out_path without suffix>/` holding one `<layer>.parquet` per layer."""
    return out_path.with_suffix("") if multi else out_path


def parquet_layer_path(out_path: Path, layer_name: str, multi: bool) -> Path:
    if not multi:
        return out_path
    return parquet_output_root(out_path, multi) / f"{layer_name}.parquet"


def clear_parquet_output(out_path: Path, multi: bool) -> None:
    """Overwrite for a GeoParquet output: remove the file, or the layer files in its
    folder. Only `*.parquet` files directly in the folder go; anything else is left."""
    root = parquet_output_root(out_path, multi)
    if not multi:
        if root.is_file():
            root.unlink()
    elif root.is_dir():
        for f in root.glob("*.parquet"):
            f.unlink()


# Column types GDAL's GeoPackage writer crashes on (a native crash, no error; DuckDB spatial
# through 1.5.x), at any depth inside a LIST/STRUCT/MAP: 128-bit integers, and the DECIMALs
# wider than 18 digits that are stored as them. sum() of any integer or DECIMAL column
# returns one, so a mapping expr can easily produce them.
_GPKG_HUGE_INT = re.compile(r"\bU?HUGEINT\b")
_GPKG_WIDE_DECIMAL = re.compile(r"\bDECIMAL\((\d+),\s*\d+\)")
_INT64_MIN, _INT64_MAX = -(2**63), 2**63 - 1


def gpkg_safe_type(dtype: str) -> str:
    """`dtype` with those types swapped for what a GeoPackage stores anyway: a 64-bit
    INTEGER and a REAL (it has no exact decimals: a DECIMAL(18, 2) is written as REAL too)."""
    dtype = _GPKG_HUGE_INT.sub("BIGINT", dtype)
    return _GPKG_WIDE_DECIMAL.sub(lambda m: "DOUBLE" if int(m[1]) > 18 else m[0], dtype)


def gpkg_safe_select(
    con: duckdb.DuckDBPyConnection, select: str, layer: str, log: Callable[[str], None],
) -> str:
    """`select` with every column GeoPackage can't take cast by gpkg_safe_type. A top-level
    integer beyond 64 bits fails the write with an error naming the column."""
    replace = []
    for name, dtype, *_ in con.execute(f"DESCRIBE SELECT * FROM ({select})").fetchall():
        dtype = str(dtype)
        safe = gpkg_safe_type(dtype)
        if safe == dtype:
            continue
        col = _ident(name)
        if safe == "BIGINT":
            too_big = _lit(
                f"column '{name}' of layer '{layer}' has a value too large for a GeoPackage "
                f"integer (64 bits); cast it to DOUBLE in the mapping to write it"
            )
            expr = (
                f"CASE WHEN {col} IS NULL THEN NULL "
                f"WHEN {col} BETWEEN {_INT64_MIN} AND {_INT64_MAX} THEN {col}::BIGINT "
                f"ELSE error({too_big}) END"
            )
        else:
            expr = f"{col}::{safe}"
        replace.append(f"{expr} AS {col}")
        log(f"  column '{name}': {dtype} written as {safe} (GeoPackage has no {dtype})")
    if not replace:
        return select
    return f"SELECT * REPLACE ({', '.join(replace)}) FROM ({select})"


def merge_gpkg_layer(main_path: Path, temp_path: Path) -> None:
    """Splice the single layer written to temp_path into main_path.

    DuckDB's `COPY ... TO x.gpkg (FORMAT GDAL, ...)` recreates the whole file
    on every call (no working append/update mode as of duckdb 1.5.4), so
    writing N layers into one GeoPackage means writing each to its own
    single-layer file and merging all but the first in at the SQLite level —
    a GeoPackage is just a SQLite database. The R-tree spatial index isn't
    recreated for merged-in layers: it's an optional GeoPackage extension
    (gpkg_extensions), not required to read the layer, just to accelerate
    spatial queries against it directly in a GIS tool.
    """
    con = sqlite3.connect(str(main_path))
    try:
        con.execute("ATTACH DATABASE ? AS src", (str(temp_path),))
        (table_name,) = con.execute("SELECT table_name FROM src.gpkg_contents LIMIT 1").fetchone()
        (create_sql,) = con.execute(
            "SELECT sql FROM src.sqlite_master WHERE type = 'table' AND name = ?", (table_name,)
        ).fetchone()
        con.execute(create_sql)
        con.execute(f'INSERT INTO "{table_name}" SELECT * FROM src."{table_name}"')
        con.execute(
            "INSERT INTO gpkg_contents SELECT * FROM src.gpkg_contents WHERE table_name = ?", (table_name,)
        )
        con.execute(
            "INSERT INTO gpkg_geometry_columns SELECT * FROM src.gpkg_geometry_columns WHERE table_name = ?",
            (table_name,),
        )
        con.execute("INSERT OR IGNORE INTO gpkg_spatial_ref_sys SELECT * FROM src.gpkg_spatial_ref_sys")
        has_ogr_contents = con.execute(
            "SELECT 1 FROM src.sqlite_master WHERE type = 'table' AND name = 'gpkg_ogr_contents'"
        ).fetchone()
        if has_ogr_contents:
            con.execute(
                "INSERT INTO gpkg_ogr_contents SELECT * FROM src.gpkg_ogr_contents WHERE table_name = ?",
                (table_name,),
            )
        con.commit()
    finally:
        con.execute("DETACH DATABASE src")
        con.close()


def write_gpkg_layer(
    con: duckdb.DuckDBPyConnection,
    select: str,
    layer: OutputLayer,
    out_path: Path,
    scratch: Path,
    log: Callable[[str], None],
) -> int:
    """Add `select`'s rows to the GeoPackage `out_path` as `layer`; returns the rows written.

    The GDAL writer recreates the file it writes on every call, so only the very first
    layer written to a not-yet-existing file can go straight there; every layer after that
    (of this pipeline, or an earlier one sharing the output) is written to `scratch` and
    spliced into out_path at the SQLite level (see merge_gpkg_layer)."""
    write_direct = not out_path.exists()
    target = out_path if write_direct else scratch
    log(f"writing {out_path} layer '{layer.layer}' ({layer.crs})")
    select = gpkg_safe_select(con, select, layer.layer, log)
    (rows,) = con.execute(
        f"COPY ({select}) TO {_lit(str(target))} "
        f"(FORMAT GDAL, DRIVER 'GPKG', LAYER_NAME {_lit(layer.layer)}, SRS {_lit(layer.crs)})"
    ).fetchone()
    if not write_direct:
        merge_gpkg_layer(out_path, target)
    return rows


def write_parquet_layer(
    con: duckdb.DuckDBPyConnection,
    select: str,
    layer: OutputLayer,
    target: Path,
    has_geometry: bool,
    log: Callable[[str], None],
) -> int:
    """Write one layer as GeoParquet via DuckDB's native Parquet writer.

    The spatial extension adds GeoParquet's `geo` metadata for any GEOMETRY column,
    but only names a CRS when the column's *type* carries one. A plain GEOMETRY gets
    no `crs` entry, which per the GeoParquet spec means OGC:CRS84, so a UTM layer
    would be read back as lon/lat. Hence the cast to GEOMETRY('<layer.crs>'). The
    intermediate ::GEOMETRY drops any CRS the column already has (e.g. from a typed
    GeoParquet source), since DuckDB refuses to cast between two different CRSs; the
    engine tracks the actual CRS itself, and `geom` is in layer.crs at this point.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    sql = select
    if has_geometry:
        sql = (
            f"SELECT * REPLACE (geom::GEOMETRY::GEOMETRY({_lit(layer.crs)}) AS geom) "
            f"FROM ({select})"
        )
    log(f"writing {target} ({layer.crs})")
    (rows,) = con.execute(
        f"COPY ({sql}) TO {_lit(str(target))} (FORMAT PARQUET, COMPRESSION ZSTD)"
    ).fetchone()
    return rows
