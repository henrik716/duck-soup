"""postgres sources, read through DuckDB's postgres extension (ATTACH ... TYPE postgres)
rather than GDAL."""
from __future__ import annotations

import re

import duckdb

from ..sql_util import quote_ident as _sql_ident, quote_literal as _sql_str


def _split_pg_layer(layer: str | None) -> tuple[str, str]:
    """(schema, table) from a Source.layer value, defaulting schema to 'public'."""
    if not layer:
        raise ValueError("postgres sources require 'layer' to name a table (optionally 'schema.table')")
    if "." in layer:
        schema, _, table = layer.partition(".")
        return schema, table
    return "public", layer


def attach_postgres(con: duckdb.DuckDBPyConnection, dsn: str, source_id: str) -> str:
    """ATTACH a postgres connection string under a stable per-source alias, idempotently.

    Aliased by source id (not by dsn) so re-running a pipeline against the same source
    twice on one connection doesn't try to ATTACH the same alias twice, while different
    postgres sources in one pipeline still get distinct catalogs.
    """
    alias = "pg_" + re.sub(r"\W", "_", source_id)
    attached = {row[0] for row in con.execute("SELECT database_name FROM duckdb_databases()").fetchall()}
    if alias not in attached:
        con.execute(f"ATTACH {_sql_str(dsn)} AS {_sql_ident(alias)} (TYPE postgres)")
    return alias


def postgres_geometry_info(
    con: duckdb.DuckDBPyConnection, alias: str, schema: str, table: str
) -> tuple[str | None, str | None]:
    """(geometry_column_name, EPSG:<srid>) for a table in an attached postgres catalog.

    PostGIS registers every spatial table's geometry column in the `geometry_columns`
    view; queried the same way as any other view through the attached catalog. Returns
    (None, None) if the table has no geometry column (or isn't PostGIS-enabled at all).
    """
    try:
        rows = con.execute(
            f"SELECT f_geometry_column, srid FROM {_sql_ident(alias)}.public.geometry_columns "
            f"WHERE f_table_schema = {_sql_str(schema)} AND f_table_name = {_sql_str(table)}"
        ).fetchall()
    except Exception:
        return None, None
    if not rows:
        return None, None
    name, srid = rows[0]
    crs = f"EPSG:{srid}" if srid else None
    return name, crs


def list_postgres_tables(
    con: duckdb.DuckDBPyConnection, dsn: str
) -> tuple[list[str], str | None]:
    """PostGIS-enabled tables (from geometry_columns) plus plain tables.

    Plain tables are included because they're usable as attribute-join lookup sources.
    Each is "schema.table" unless schema is the default 'public'. Default CRS is the
    first spatial table's SRID.
    """
    alias = attach_postgres(con, dsn, "inspect")
    layers: list[str] = []
    default_crs: str | None = None
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
    return layers, default_crs
