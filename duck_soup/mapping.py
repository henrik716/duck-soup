"""The SQL of an output layer: the mapping compiled to columns, and the final SELECT.

Every mapping item (`from` / `const` / `expr` / `func` / `codelist`, optionally `cast`)
becomes one `<expr> AS "<to>"` column of the SELECT written for a layer (final_select).
"""
from __future__ import annotations

from .config import CodeCase, CodeList, MapItem, OutputLayer
from .sql_util import quote_ident as _ident, quote_literal as _lit, st_transform

# The SQL of each `func`, over the columns final_select provides: `geom` (working CRS),
# `__geom4326` (the full geometry in lon/lat) and `__centroid4326` (its centroid, taken in
# the working CRS, in lon/lat). config.MAP_FUNCS lists the same names (a test checks).
FUNC_SQL = {
    "uuid": "uuid()::VARCHAR",
    "seq": "row_number() OVER ()",
    "now": "current_timestamp",
    "today": "current_date",
    "lon": "ROUND(ST_X(__centroid4326), 7)",
    "lat": "ROUND(ST_Y(__centroid4326), 7)",
    "x": "ROUND(ST_X(ST_Centroid(geom)), 3)",
    "y": "ROUND(ST_Y(ST_Centroid(geom)), 3)",
    "mgrs": "to_mgrs(ST_X(__centroid4326), ST_Y(__centroid4326))",
    "geohash": "to_geohash(ST_X(__centroid4326), ST_Y(__centroid4326))",
    "wkb": "ST_AsHEXWKB(__geom4326)",
    "wkt": "ST_AsText(__geom4326)",
    "geom_type": "ST_GeometryType(geom)::VARCHAR",
    "area": "ST_Area(geom)",
    # ST_Length only measures lines (0 for polygons) and ST_Perimeter only polygons (0 for
    # lines), so their sum is the length of a line, the perimeter of a polygon, and the
    # total of both for a mixed collection.
    "length": "(ST_Length(geom) + ST_Perimeter(geom))",
}

# The funcs that read the geometry: an error on a base source without one.
GEOMETRY_FUNCS = frozenset(FUNC_SQL) - {"uuid", "seq", "now", "today"}

# The funcs measured in working CRS units: meaningless in degrees (see
# Engine._check_working_crs_units).
MEASURE_FUNCS = frozenset({"area", "length"})

# The funcs that read __geom4326 / __centroid4326 (see with_geo_helpers).
GEO4326_FUNCS = frozenset({"lon", "lat", "mgrs", "geohash", "wkb", "wkt"})


def check_geometry_funcs(items: list[MapItem], has_geom: bool, base: str) -> None:
    """Refuse geometry funcs in `items` when the base source has no geometry."""
    used = {m.func for m in items if m.func}
    if not has_geom and (used & GEOMETRY_FUNCS):
        raise ValueError(
            f"mapping uses {sorted(used & GEOMETRY_FUNCS)} but base source "
            f"'{base}' has no geometry"
        )


def with_geo_helpers(source: str, working_crs: str) -> str:
    """SELECT * from `source` (a table expression with a working-CRS `geom`) plus
    __geom4326 (the full geometry in lon/lat, for wkb/wkt) and __centroid4326 (its centroid,
    for lon/lat/mgrs/geohash on lines and polygons too).

    __geom4326 is transformed from `geom`, which is already in working_crs (see
    Engine._create_source_views): one chained reprojection (source CRS -> working_crs ->
    EPSG:4326) per feature, not a second independent transform from the source CRS. This
    keeps lon/lat/mgrs consistent with the geometry actually used for joins/steps and avoids
    double rounding/drift from reprojecting the same feature twice. __centroid4326 is the
    centroid taken in working_crs, then reprojected: a centroid computed on lon/lat degrees
    is distorted (~100 km off for a line spanning Norway)."""
    return f"""
        SELECT *,
            {st_transform('ST_Centroid(geom)', working_crs, 'EPSG:4326')} AS __centroid4326
        FROM (
            SELECT *, {st_transform('geom', working_crs, 'EPSG:4326')} AS __geom4326
            FROM {source}
        )"""


def map_expr(item: MapItem) -> str:
    if item.from_ is not None:
        expr = _ident(item.from_)
    elif item.const is not None:
        if isinstance(item.const, str):
            expr = _lit(item.const)
        else:
            expr = str(item.const)
    elif item.expr is not None:
        expr = f"({item.expr})"
    elif item.func is not None:
        expr = FUNC_SQL[item.func]
    elif item.codelist is not None:
        expr = codelist_expr(item.codelist)
    else:  # pragma: no cover - validated upstream
        raise ValueError(f"mapping '{item.to}' has no source")
    if item.cast:
        expr = cast_expr(expr, item.cast)
    return f"{expr} AS {_ident(item.to)}"


def cast_expr(expr: str, cast_type: str) -> str:
    normalized = cast_type.strip().upper()
    if normalized in ("DATE", "TIMESTAMP"):
        # TRY_CAST(... AS DATE/TIMESTAMP) only understands ISO-ish text
        # (YYYY-MM-DD); it silently returns NULL for the dd.mm.yyyy format this
        # data's source fields actually use (e.g. Bane NOR's '01.01.1111'
        # placeholder date, or real 'gyldigfra' values) — not because the date
        # itself is invalid, but because the format doesn't match. Fall back to
        # parsing that format explicitly before giving up; a value that fails
        # both is still NULL. (DATE specifically also can't hold a pre-1970 value
        # when written to GeoPackage — a DuckDB/GDAL limitation, not this parsing
        # — which is why some mappings use `cast: TIMESTAMP` instead.)
        return (
            f"COALESCE(TRY_CAST({expr} AS {normalized}), "
            f"TRY_CAST(TRY_STRPTIME(CAST({expr} AS VARCHAR), '%d.%m.%Y') AS {normalized}))"
        )
    return f"TRY_CAST({expr} AS {cast_type})"


def codelist_expr(cl: CodeList) -> str:
    col = _ident(cl.source)
    if cl.cases:
        arms = " ".join(
            f"WHEN {case_condition(col, c, cl.case_insensitive)} "
            f"THEN {_lit(c.value)}"
            for c in cl.cases
        )
        default = _lit(cl.default) if cl.default is not None else "NULL"
        return f"CASE {arms} ELSE {default} END"

    # File-backed lookup table: correlated scalar subquery against read_csv.
    match_col = _ident(cl.file_match_col)
    value_col = _ident(cl.file_value_col)
    lookup = f"read_csv({_lit(cl.file)}, header=true, all_varchar=true)"
    cmp = (
        f"lower(cl.{match_col}) = lower({col})"
        if cl.case_insensitive
        else f"cl.{match_col} = {col}"
    )
    sub = f"(SELECT cl.{value_col} FROM {lookup} AS cl WHERE {cmp} LIMIT 1)"
    if cl.default is not None:
        return f"COALESCE({sub}, {_lit(cl.default)})"
    return sub


def case_condition(col: str, c: CodeCase, case_insensitive: bool) -> str:
    if c.is_blank:
        return f"({col} IS NULL OR {col} = '')"
    if c.match is not None:
        return (
            f"lower({col}) = lower({_lit(c.match)})"
            if case_insensitive
            else f"{col} = {_lit(c.match)}"
        )
    if c.like is not None:
        target = f"lower({col})" if case_insensitive else col
        pattern = c.like.lower() if case_insensitive else c.like
        return f"{target} LIKE {_lit(pattern)}"
    # regex
    flag = ", 'i'" if case_insensitive else ""
    return f"regexp_matches({col}, {_lit(c.regex)}{flag})"


def final_select(
    prev: str,
    layer: OutputLayer,
    mapping: list[MapItem],
    *,
    working_crs: str,
    has_geom: bool,
    base: str,
) -> str:
    """The SELECT written for `layer` from the view `prev`: `mapping`'s columns (every
    column as-is when it's empty), the layer's `filter`, and `geom` in the layer's CRS.
    `base` names the base source in the error for a geometry func without geometry."""
    check_geometry_funcs(mapping, has_geom, base)

    exprs = [map_expr(m) for m in mapping]
    source = f"(SELECT * FROM {_ident(prev)} WHERE {layer.filter})" if layer.filter else _ident(prev)

    if not has_geom:
        cols = ",\n            ".join(exprs) or "*"
        return f"SELECT\n            {cols}\n        FROM {source}"

    geom_out = st_transform("geom", working_crs, layer.crs)
    # geom (working_crs, for area/length/x/y/geom_type) and the lon/lat helper columns
    # (see with_geo_helpers) are all available here; geometry written as 'geom'.
    cols = ",\n        ".join(exprs)
    select_cols = (
        f"{cols},\n        " if cols
        else "* EXCLUDE (geom, __geom4326, __centroid4326, __src_row),\n        "
    )
    # With no mapping, every upstream column is written — but not GDAL's OGC_FID: ST_Read
    # adds it for GeoJSON (and other FID-less) sources, and the GeoPackage writer rejects a
    # field by that name, since it's GDAL's default name for the feature ID itself.
    # COLUMNS(lambda) rather than EXCLUDE, which errors when the column isn't there (most
    # sources have none); it can't come up empty here because `geom` always survives.
    if not cols:
        source = f"(SELECT COLUMNS(lambda c: lower(c) <> 'ogc_fid') FROM {source})"
    return f"""
    WITH enriched AS ({with_geo_helpers(source, working_crs)}
    )
    SELECT
        {select_cols}{geom_out} AS geom
    FROM enriched
    """
