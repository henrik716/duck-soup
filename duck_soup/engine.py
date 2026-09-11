"""The DuckDB execution engine.

Strategy
--------
Read every source once, reproject geometry to a single working CRS, then build a
chain of SQL views: base -> step1 -> step2 -> ... -> mapped. DuckDB plans the
whole chain as one query, so joins and projections stay fast and stream from
GDAL where possible. The final view is written straight to GeoPackage via
COPY ... (FORMAT GDAL, DRIVER 'GPKG').

Spatial joins use LATERAL ... LIMIT 1 so each base feature stays a single row
even when it matches several polygons (this mirrors what the FME SpatialFilter
+ first-match behaviour did).
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from typing import Callable

import duckdb

from . import sources as src_readers
from .config import (
    AttributeJoin, Buffer, Centroid, Clip, CodeCase, CodeList, Config,
    Dissolve, Erase, Filter, IntersectOverlay, MapItem, Merge, NearestNeighbor,
    OutputLayer, Pipeline, Snapshot, SpatialJoin,
)
from .derive import DUCKDB_LOCK, register_udfs

_PREDICATE_SQL = {
    "intersects": "ST_Intersects",
    "contains": "ST_Contains",
    "within": "ST_Within",
}


def _ident(name: str) -> str:
    """Quote a SQL identifier."""
    return '"' + name.replace('"', '""') + '"'


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _transform(col: str, from_crs: str, to_crs: str) -> str:
    if from_crs == to_crs:
        return col
    return f"ST_Transform({col}, {_lit(from_crs)}, {_lit(to_crs)}, always_xy := true)"


def _merge_gpkg_layer(main_path: Path, temp_path: Path) -> None:
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


class Engine:
    def __init__(self, pipeline: Pipeline, log: Callable[[str], None] | None = None):
        self.p = pipeline
        self.working_crs = pipeline.effective_working_crs
        self.log = log or (lambda m: None)

    # -- source views -------------------------------------------------------
    def _create_source_views(self, con: duckdb.DuckDBPyConnection, workdir: Path) -> None:
        for s in self.p.sources:
            read = src_readers.read_expr(s, workdir, con=con, log=self.log)
            view = _ident(f"src_{s.id}")
            if s.has_geometry:
                crs = "EPSG:4326" if s.format == "arcgis_rest" else (s.crs or self.working_crs)
                # Source geometry is reprojected to working_crs exactly once, here. Every
                # later CRS-dependent value (lon/lat/mgrs, output CRS, etc.) is derived from
                # this same working_crs geometry rather than re-transforming from the source
                # CRS, so there's a single reprojection chain per feature (no double rounding).
                geom = _transform("geom", crs, self.working_crs)
                if s.make_valid:
                    geom = f"ST_MakeValid({geom})"
                sql = (
                    f"CREATE OR REPLACE TEMP VIEW {view} AS "
                    f"SELECT * EXCLUDE (geom), {geom} AS geom, "
                    f"row_number() OVER () AS __src_row FROM {read}"
                )
            else:
                sql = f"CREATE OR REPLACE TEMP VIEW {view} AS SELECT * FROM {read}"
            self.log(f"source '{s.id}' ({s.format}) -> {view}")
            con.execute(sql)

    def _resolve_has_geometry(self, sid: str) -> bool:
        for ds in self.p.derived_sources:
            if ds.id == sid:
                return self._resolve_has_geometry(ds.from_)
        return self.p.source(sid).has_geometry

    def _create_derived_source_views(self, con: duckdb.DuckDBPyConnection) -> None:
        for ds in self.p.derived_sources:
            from_view = _ident(f"src_{ds.from_}")
            view = _ident(f"src_{ds.id}")
            where_clause = f"WHERE {ds.where}" if ds.where else ""
            if self._resolve_has_geometry(ds.from_):
                geom = "geom"
                if ds.buffer is not None:
                    geom = f"ST_Buffer({geom}, {ds.buffer})"
                if ds.make_valid:
                    geom = f"ST_MakeValid({geom})"
                sql = (
                    f"CREATE OR REPLACE TEMP VIEW {view} AS "
                    f"SELECT * EXCLUDE (geom), {geom} AS geom FROM {from_view} {where_clause}"
                )
            else:
                sql = f"CREATE OR REPLACE TEMP VIEW {view} AS SELECT * FROM {from_view} {where_clause}"
            self.log(f"derived_source '{ds.id}' <- {ds.from_}")
            con.execute(sql)

    # -- steps --------------------------------------------------------------
    def _apply_spatial_join(self, prev: str, step: SpatialJoin, idx: int) -> str:
        pred = _PREDICATE_SQL[step.predicate]
        src_view = _ident(f"src_{step.source}")
        out_view = f"step_{idx}"

        if step.match == "all":
            b_fields = ", ".join(
                f"b.{_ident(col)} AS {_ident(out)}" for out, col in step.fields.items()
            )
            sql = f"""
            CREATE OR REPLACE TEMP VIEW {_ident(out_view)} AS
            SELECT a.*{(', ' + b_fields) if b_fields else ''}
            FROM {_ident(prev)} a
            LEFT JOIN {src_view} b ON {pred}(a.geom, b.geom)
            """
            self.log(f"step {idx}: spatial_join {step.predicate} {step.source} (match=all)")
            return sql, out_view

        pulled = ", ".join(
            f"j.{_ident(out)}" for out in step.fields
        )
        inner = ", ".join(
            f"b.{_ident(col)} AS {_ident(out)}" for out, col in step.fields.items()
        )
        # Deterministic match selection: __src_row is a stable row_number() assigned to
        # each join-source row when its view is created (see _create_source_views), so
        # ordering by it makes "first match" reproducible instead of depending on
        # unspecified query-plan order. "largest_overlap" ranks by intersection area first,
        # falling back to __src_row to break ties.
        if step.on_multiple == "largest_overlap":
            order_by = "ORDER BY ST_Area(ST_Intersection(a.geom, b.geom)) DESC, b.__src_row ASC"
        else:
            order_by = "ORDER BY b.__src_row ASC"
        sql = f"""
        CREATE OR REPLACE TEMP VIEW {_ident(out_view)} AS
        SELECT a.*{(', ' + pulled) if pulled else ''}
        FROM {_ident(prev)} a
        LEFT JOIN LATERAL (
            SELECT {inner if inner else '1'}
            FROM {src_view} b
            WHERE {pred}(a.geom, b.geom)
            {order_by}
            LIMIT 1
        ) j ON true
        """
        self.log(f"step {idx}: spatial_join {step.predicate} {step.source} (on_multiple={step.on_multiple})")
        return sql, out_view

    def _apply_nearest_neighbor(self, prev: str, step: NearestNeighbor, idx: int) -> str:
        src_view = _ident(f"src_{step.source}")
        dist_expr = "ST_Distance(a.geom, b.geom)"
        pulled = ", ".join(f"j.{_ident(out)}" for out in step.fields)
        if step.distance_field:
            pulled += (", " if pulled else "") + f"j.{_ident(step.distance_field)}"
        inner = ", ".join(
            f"b.{_ident(col)} AS {_ident(out)}" for out, col in step.fields.items()
        )
        if step.distance_field:
            inner += (", " if inner else "") + f"{dist_expr} AS {_ident(step.distance_field)}"
        where_clause = (
            f"WHERE ST_DWithin(a.geom, b.geom, {step.max_distance})"
            if step.max_distance is not None else ""
        )
        out_view = f"step_{idx}"
        sql = f"""
        CREATE OR REPLACE TEMP VIEW {_ident(out_view)} AS
        SELECT a.*{(', ' + pulled) if pulled else ''}
        FROM {_ident(prev)} a
        LEFT JOIN LATERAL (
            SELECT {inner if inner else '1'}
            FROM {src_view} b
            {where_clause}
            ORDER BY {dist_expr}
            LIMIT 1
        ) j ON true
        """
        suffix = f" (max_distance={step.max_distance})" if step.max_distance is not None else ""
        self.log(f"step {idx}: nearest_neighbor {step.source}{suffix}")
        return sql, out_view

    def _apply_attribute_join(self, prev: str, step: AttributeJoin, idx: int) -> str:
        src_view = _ident(f"src_{step.source}")
        pulled = ", ".join(f"j.{_ident(out)}" for out in step.fields)
        inner = ", ".join(
            f"b.{_ident(col)} AS {_ident(out)}" for out, col in step.fields.items()
        )
        out_view = f"step_{idx}"
        sql = f"""
        CREATE OR REPLACE TEMP VIEW {_ident(out_view)} AS
        SELECT a.*{(', ' + pulled) if pulled else ''}
        FROM {_ident(prev)} a
        LEFT JOIN LATERAL (
            SELECT {inner if inner else '1'}
            FROM {src_view} b
            WHERE b.{_ident(step.right)} = ({step.left})
            LIMIT 1
        ) j ON true
        """
        self.log(f"step {idx}: attribute_join {step.source} on {step.right}")
        return sql, out_view

    def _apply_buffer(self, prev: str, step: Buffer, idx: int) -> tuple[str, str]:
        out_view = f"step_{idx}"
        sql = f"""
        CREATE OR REPLACE TEMP VIEW {_ident(out_view)} AS
        SELECT * EXCLUDE (geom), ST_Buffer(geom, {step.distance}) AS geom
        FROM {_ident(prev)}
        """
        self.log(f"step {idx}: buffer {step.distance}")
        return sql, out_view

    def _apply_centroid(self, prev: str, _step: Centroid, idx: int) -> tuple[str, str]:
        out_view = f"step_{idx}"
        sql = f"""
        CREATE OR REPLACE TEMP VIEW {_ident(out_view)} AS
        SELECT * EXCLUDE (geom), ST_Centroid(geom) AS geom
        FROM {_ident(prev)}
        """
        self.log(f"step {idx}: centroid")
        return sql, out_view

    def _apply_clip(self, prev: str, step: Clip, idx: int) -> tuple[str, str]:
        pred = _PREDICATE_SQL[step.predicate]
        src_view = _ident(f"src_{step.source}")
        out_view = f"step_{idx}"
        sql = f"""
        CREATE OR REPLACE TEMP VIEW {_ident(out_view)} AS
        SELECT a.* EXCLUDE (geom), ST_Intersection(a.geom, j.clip_geom) AS geom
        FROM {_ident(prev)} a
        INNER JOIN LATERAL (
            SELECT b.geom AS clip_geom
            FROM {src_view} b
            WHERE {pred}(a.geom, b.geom)
            LIMIT 1
        ) j ON true
        """
        self.log(f"step {idx}: clip {step.predicate} {step.source}")
        return sql, out_view

    def _apply_erase(self, prev: str, step: Erase, idx: int) -> tuple[str, str]:
        pred = _PREDICATE_SQL[step.predicate]
        src_view = _ident(f"src_{step.source}")
        out_view = f"step_{idx}"
        sql = f"""
        CREATE OR REPLACE TEMP VIEW {_ident(out_view)} AS
        SELECT a.* EXCLUDE (geom),
               COALESCE(ST_Difference(a.geom, j.union_geom), a.geom) AS geom
        FROM {_ident(prev)} a
        LEFT JOIN LATERAL (
            SELECT ST_Union_Agg(b.geom) AS union_geom
            FROM {src_view} b
            WHERE {pred}(a.geom, b.geom)
        ) j ON true
        """
        self.log(f"step {idx}: erase {step.predicate} {step.source}")
        return sql, out_view

    def _apply_dissolve(self, prev: str, step: Dissolve, idx: int) -> tuple[str, str]:
        out_view = f"step_{idx}"
        if step.by:
            by_cols = ", ".join(_ident(c) for c in step.by)
            sql = f"""
            CREATE OR REPLACE TEMP VIEW {_ident(out_view)} AS
            SELECT {by_cols}, ST_Union_Agg(geom) AS geom
            FROM {_ident(prev)}
            GROUP BY {by_cols}
            """
        else:
            sql = f"""
            CREATE OR REPLACE TEMP VIEW {_ident(out_view)} AS
            SELECT ST_Union_Agg(geom) AS geom
            FROM {_ident(prev)}
            """
        self.log(f"step {idx}: dissolve by {step.by or '(all)'}")
        return sql, out_view

    def _apply_intersect_overlay(self, prev: str, step: IntersectOverlay, idx: int) -> tuple[str, str]:
        src_view = _ident(f"src_{step.source}")
        b_fields = ", ".join(
            f"b.{_ident(col)} AS {_ident(out)}" for out, col in step.fields.items()
        )
        out_view = f"step_{idx}"
        sql = f"""
        CREATE OR REPLACE TEMP VIEW {_ident(out_view)} AS
        SELECT a.* EXCLUDE (geom){(', ' + b_fields) if b_fields else ''},
               ST_Intersection(a.geom, b.geom) AS geom
        FROM {_ident(prev)} a
        JOIN {src_view} b ON ST_Intersects(a.geom, b.geom)
        """
        self.log(f"step {idx}: intersect_overlay {step.source}")
        return sql, out_view

    def _apply_filter(self, prev: str, step: Filter, idx: int) -> tuple[str, str]:
        out_view = f"step_{idx}"
        sql = f"""
        CREATE OR REPLACE TEMP VIEW {_ident(out_view)} AS
        SELECT * FROM {_ident(prev)}
        WHERE {step.where}
        """
        self.log(f"step {idx}: filter {step.where}")
        return sql, out_view

    def _apply_merge(self, prev: str, step: Merge, idx: int) -> tuple[str, str]:
        src_view = _ident(f"src_{step.source}")
        out_view = f"step_{idx}"
        sql = f"""
        CREATE OR REPLACE TEMP VIEW {_ident(out_view)} AS
        SELECT * FROM {_ident(prev)}
        UNION ALL BY NAME
        SELECT * FROM {src_view}
        """
        self.log(f"step {idx}: merge {step.source}")
        return sql, out_view

    _MAIN_BRANCH = "__main__"

    def _sync_branch_view(self, con: duckdb.DuckDBPyConnection, chains: dict[str, str], name: str) -> None:
        # Any branch a later step's source: might reference needs a src_<id>
        # view kept up to date; the main chain isn't referenced that way.
        if name == self._MAIN_BRANCH:
            return
        con.execute(
            f"CREATE OR REPLACE TEMP VIEW {_ident(f'src_{name}')} AS "
            f"SELECT * FROM {_ident(chains[name])}"
        )

    def _build_step_views(self, con: duckdb.DuckDBPyConnection, prev: str, limit_steps: int | None = None) -> str:
        chains: dict[str, str] = {self._MAIN_BRANCH: prev}

        for i, step in enumerate(self.p.steps, start=1):
            if limit_steps is not None and i > limit_steps:
                break
            if isinstance(step, Snapshot):
                # `branch` here means "snapshot from this branch", not
                # "operate on this branch" — no new view needed, just an alias.
                source_branch = step.branch or self._MAIN_BRANCH
                chains[step.id] = chains[source_branch]
                self._sync_branch_view(con, chains, step.id)
                self.log(f"step {i}: snapshot '{step.id}' <- {source_branch}")
                continue

            target = step.branch or self._MAIN_BRANCH
            cur = chains[target]
            if isinstance(step, SpatialJoin):
                sql, new_view = self._apply_spatial_join(cur, step, i)
            elif isinstance(step, AttributeJoin):
                sql, new_view = self._apply_attribute_join(cur, step, i)
            elif isinstance(step, NearestNeighbor):
                sql, new_view = self._apply_nearest_neighbor(cur, step, i)
            elif isinstance(step, Buffer):
                sql, new_view = self._apply_buffer(cur, step, i)
            elif isinstance(step, Centroid):
                sql, new_view = self._apply_centroid(cur, step, i)
            elif isinstance(step, Clip):
                sql, new_view = self._apply_clip(cur, step, i)
            elif isinstance(step, Erase):
                sql, new_view = self._apply_erase(cur, step, i)
            elif isinstance(step, Dissolve):
                sql, new_view = self._apply_dissolve(cur, step, i)
            elif isinstance(step, IntersectOverlay):
                sql, new_view = self._apply_intersect_overlay(cur, step, i)
            elif isinstance(step, Filter):
                sql, new_view = self._apply_filter(cur, step, i)
            elif isinstance(step, Merge):
                sql, new_view = self._apply_merge(cur, step, i)
            else:  # pragma: no cover
                raise ValueError(f"unknown step type: {step}")
            con.execute(sql)
            chains[target] = new_view
            self._sync_branch_view(con, chains, target)

        return chains[self._MAIN_BRANCH]

    # -- mapping ------------------------------------------------------------
    def _map_expr(self, item: MapItem) -> str:
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
            expr = self._func_expr(item.func)
        elif item.codelist is not None:
            expr = self._codelist_expr(item.codelist)
        else:  # pragma: no cover - validated upstream
            raise ValueError(f"mapping '{item.to}' has no source")
        if item.cast:
            expr = self._cast_expr(expr, item.cast)
        return f"{expr} AS {_ident(item.to)}"

    @staticmethod
    def _cast_expr(expr: str, cast_type: str) -> str:
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

    def _codelist_expr(self, cl: CodeList) -> str:
        col = _ident(cl.source)
        if cl.cases:
            arms = " ".join(
                f"WHEN {self._case_condition(col, c, cl.case_insensitive)} "
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

    @staticmethod
    def _case_condition(col: str, c: CodeCase, case_insensitive: bool) -> str:
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

    def _func_expr(self, func: str) -> str:
        # geometry-derived funcs read __geom4326 (full geometry) or
        # __centroid4326 (point representation), provided by the final CTE
        if func == "uuid":
            return "uuid()::VARCHAR"
        if func == "now":
            return "current_timestamp"
        if func == "today":
            return "current_date"
        if func == "lon":
            return "ROUND(ST_X(__centroid4326), 7)"
        if func == "lat":
            return "ROUND(ST_Y(__centroid4326), 7)"
        if func == "mgrs":
            return "to_mgrs(ST_X(__centroid4326), ST_Y(__centroid4326))"
        if func == "wkb":
            return "ST_AsHEXWKB(__geom4326)"
        if func == "area":
            return "ST_Area(geom)"
        if func == "length":
            return "ST_Length(geom)"
        raise ValueError(f"unknown func: {func}")

    def _final_select(self, prev: str, layer: OutputLayer) -> str:
        has_geom = self.p.base_source.has_geometry
        mapping = layer.mapping or self.p.mapping
        geom_funcs = {"lon", "lat", "mgrs", "wkb", "area", "length"}
        used = {m.func for m in mapping if m.func}
        if not has_geom and (used & geom_funcs):
            raise ValueError(
                f"mapping uses {sorted(used & geom_funcs)} but base source "
                f"'{self.p.base}' has no geometry"
            )

        cols = ",\n            ".join(self._map_expr(m) for m in mapping)
        source = f"(SELECT * FROM {_ident(prev)} WHERE {layer.filter})" if layer.filter else _ident(prev)

        if not has_geom:
            return f"SELECT\n            {cols if cols else '*'}\n        FROM {source}"

        geom_out = _transform("geom", self.working_crs, layer.crs)
        # geom (working_crs, for area/length in projected units), __geom4326
        # (full geometry, for wkb) and __centroid4326 (point, for lon/lat/mgrs
        # on lines and polygons too) are all available here; geometry written as 'geom'.
        # __geom4326 is transformed from `geom`, which is already in working_crs (see
        # _create_source_views) — i.e. one chained reprojection (source CRS -> working_crs
        # -> EPSG:4326) per feature, not a second independent transform from the source CRS.
        # This keeps lon/lat/mgrs consistent with the geometry actually used for joins/steps
        # and avoids double rounding/drift from reprojecting the same feature twice.
        select_cols = (
            f"{cols},\n            " if cols
            else "* EXCLUDE (geom, __geom4326, __centroid4326, __src_row),\n            "
        )
        return f"""
        WITH base4326 AS (
            SELECT *,
                {_transform('geom', self.working_crs, 'EPSG:4326')} AS __geom4326
            FROM {source}
        ),
        enriched AS (
            SELECT *, ST_Centroid(__geom4326) AS __centroid4326
            FROM base4326
        )
        SELECT
            {select_cols}{geom_out} AS geom
        FROM enriched
        """

    # -- run ----------------------------------------------------------------
    def run(self) -> str:
        with DUCKDB_LOCK:
            con = duckdb.connect()
            # `INSTALL spatial` fetches the extension build matching this connection's
            # DuckDB core version, so the spatial extension version is pinned indirectly
            # via the `duckdb` dependency pin in pyproject.toml (see the tested version
            # noted there and in README.md) — bump both together when upgrading.
            con.execute("INSTALL spatial; LOAD spatial;")
            register_udfs(con)

            with tempfile.TemporaryDirectory() as tmp:
                workdir = Path(tmp)
                self._create_source_views(con, workdir)
                self._create_derived_source_views(con)

                # base
                con.execute(
                    f"CREATE OR REPLACE TEMP VIEW {_ident('step_0')} AS "
                    f"SELECT * FROM {_ident('src_' + self.p.base)}"
                )
                prev = "step_0"
                prev = self._build_step_views(con, prev)

                deleted_paths: set[Path] = set()
                out_paths: list[str] = []
                for out in self.p.outputs:
                    out_path = Path(out.path)
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    if out.overwrite and out_path not in deleted_paths:
                        if out_path.exists():
                            out_path.unlink()
                        deleted_paths.add(out_path)
                    for i, layer in enumerate(out.layers):
                        final_sql = self._final_select(prev, layer)
                        # The GDAL writer recreates out_path on every call, so
                        # only the very first layer written to a not-yet-existing
                        # file can go straight there; every layer after that
                        # (in this pipeline, or an earlier pipeline sharing the
                        # same output path) is written to its own temp file and
                        # spliced into out_path at the SQLite level.
                        write_direct = not out_path.exists()
                        target = out_path if write_direct else (workdir / f"__layer_{i}.gpkg")
                        copy_sql = (
                            f"COPY ({final_sql}) TO {_lit(str(target))} "
                            f"(FORMAT GDAL, DRIVER 'GPKG', LAYER_NAME {_lit(layer.layer)}, "
                            f"SRS {_lit(layer.crs)})"
                        )
                        self.log(f"writing {out_path} layer '{layer.layer}' ({layer.crs})")
                        con.execute(copy_sql)
                        if not write_direct:
                            _merge_gpkg_layer(out_path, target)
                    if str(out_path) not in out_paths:
                        out_paths.append(str(out_path))
                return out_paths

    @staticmethod
    def _bbox_filter(bbox: tuple[float, float, float, float] | None, crs: str) -> str:
        """WHERE clause restricting `geom` (in `crs`) to a WGS84 bbox (west, south, east, north).

        The bbox itself — just 4 corner points — is transformed once into `crs`, rather than
        transforming every row's geometry to WGS84 to compare; ST_Intersects then runs natively
        against the source CRS.
        """
        if not bbox:
            return ""
        west, south, east, north = bbox
        envelope = f"ST_MakeEnvelope({west}, {south}, {east}, {north})"
        if crs != "EPSG:4326":
            envelope = f"ST_Transform({envelope}, 'EPSG:4326', {_lit(crs)}, always_xy := true)"
        return f"WHERE ST_Intersects(geom, {envelope}) "

    def preview(
        self,
        limit: int = 50,
        preview_until_step: int | None = None,
        bbox: tuple[float, float, float, float] | None = None,
    ) -> list[dict]:
        with DUCKDB_LOCK:
            con = duckdb.connect()
            # `INSTALL spatial` fetches the extension build matching this connection's
            # DuckDB core version, so the spatial extension version is pinned indirectly
            # via the `duckdb` dependency pin in pyproject.toml (see the tested version
            # noted there and in README.md) — bump both together when upgrading.
            con.execute("INSTALL spatial; LOAD spatial;")
            register_udfs(con)

            with tempfile.TemporaryDirectory() as tmp:
                workdir = Path(tmp)
                self._create_source_views(con, workdir)
                self._create_derived_source_views(con)

                # base — pre-filtered to the bbox (if given) before any steps run, not just
                # at the very end. Steps (spatial joins especially) otherwise run over the
                # entire base table only to have everything but the visible viewport thrown
                # away afterwards — for a big base table with real join steps that's the
                # difference between an instant "in view" preview and a very slow one. The
                # final SELECT below still re-applies the bbox filter, since a geometry-
                # mutating step (buffer, dissolve, ...) can move a feature relative to it.
                con.execute(
                    f"CREATE OR REPLACE TEMP VIEW {_ident('step_0')} AS "
                    f"SELECT * FROM {_ident('src_' + self.p.base)} "
                    f"{self._bbox_filter(bbox, self.working_crs) if self.p.base_source.has_geometry else ''}"
                )
                prev = "step_0"
                prev = self._build_step_views(con, prev, limit_steps=preview_until_step)

                if preview_until_step is not None:
                    try:
                        con.execute(f"DESCRIBE SELECT * FROM {_ident(prev)}")
                        cols_desc = con.fetchall()
                        has_geom = any(col[0] == "geom" for col in cols_desc)
                    except Exception:
                        has_geom = False

                    if has_geom:
                        preview_sql = (
                            f"SELECT * EXCLUDE (geom), "
                            f"ST_AsGeoJSON(ST_Transform(geom, {_lit(self.working_crs)}, 'EPSG:4326', always_xy := true)) AS __geojson "
                            f"FROM {_ident(prev)} {self._bbox_filter(bbox, self.working_crs)}LIMIT {limit}"
                        )
                    else:
                        preview_sql = f"SELECT * FROM {_ident(prev)} LIMIT {limit}"
                else:
                    layer = self.p.outputs[0].layers[0]
                    final_sql = self._final_select(prev, layer)
                    has_geom = self.p.base_source.has_geometry
                    if has_geom:
                        preview_sql = (
                            f"SELECT * EXCLUDE (geom), "
                            f"ST_AsGeoJSON(ST_Transform(geom, {_lit(layer.crs)}, 'EPSG:4326', always_xy := true)) AS __geojson "
                            f"FROM ({final_sql}) {self._bbox_filter(bbox, layer.crs)}LIMIT {limit}"
                        )
                    else:
                        preview_sql = f"SELECT * FROM ({final_sql}) LIMIT {limit}"

                res = con.execute(preview_sql)
                cols = [desc[0] for desc in res.description]
                rows = res.fetchall()
                return [dict(zip(cols, r)) for r in rows]


def run_pipeline(pipeline: Pipeline, log: Callable[[str], None] | None = None) -> list[str]:
    engine = Engine(pipeline, log=log)
    return engine.run()


def preview_pipeline(
    pipeline: Pipeline,
    limit: int = 50,
    preview_until_step: int | None = None,
    bbox: tuple[float, float, float, float] | None = None,
) -> list[dict]:
    engine = Engine(pipeline)
    return engine.preview(limit=limit, preview_until_step=preview_until_step, bbox=bbox)


def run_config(config: Config, log: Callable[[str], None] | None = None) -> str:
    """Run all pipelines in a Config, appending each as a separate layer to one gpkg."""
    out_path = Path(config.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if config.overwrite and out_path.exists():
        out_path.unlink()
    for pdef in config.pipelines:
        Engine(pdef.to_pipeline(config.output), log=log).run()
    return str(out_path)


def preview_config_pipeline(
    config: Config,
    pipeline_idx: int = 0,
    limit: int = 50,
    preview_until_step: int | None = None,
    bbox: tuple[float, float, float, float] | None = None,
) -> list[dict]:
    """Preview one pipeline from a Config (defaults to the first)."""
    if pipeline_idx >= len(config.pipelines):
        raise ValueError(f"no pipeline at index {pipeline_idx}")
    return preview_pipeline(
        config.pipelines[pipeline_idx].to_pipeline(config.output),
        limit=limit, preview_until_step=preview_until_step, bbox=bbox,
    )




