"""The DuckDB execution engine.

Strategy
--------
Read every source once, reproject geometry to a single working CRS, then build a
chain of SQL views: base -> step1 -> step2 -> ... -> mapped. DuckDB plans the
whole chain as one query, so joins and projections stay fast and stream from
GDAL where possible. The final view is written straight to GeoPackage via
COPY ... (FORMAT GDAL, DRIVER 'GPKG'), or, for a .parquet/.geoparquet output path,
to GeoParquet via DuckDB's native COPY ... (FORMAT PARQUET) (see _write_parquet_layer).

Spatial joins, nearest-neighbor joins and clips keep a single best match per base
feature (see Engine._best_match_select), so each base feature stays a single row
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
    OutputLayer, Pipeline, Snapshot, SpatialJoin, is_parquet_path,
)
from .derive import DUCKDB_LOCK, init_duckdb
from .sql_util import quote_ident as _ident, quote_literal as _lit

_PREDICATE_SQL = {
    "intersects": "ST_Intersects",
    "contains": "ST_Contains",
    "within": "ST_Within",
}


# Geographic CRSs (coordinates in degrees) common enough to expect as a working_crs — mostly
# because working_crs defaults to the base source's CRS, and GeoJSON / ArcGIS REST / OGC API
# sources are lon/lat. There's no CRS database in this codebase to ask "is this geographic?"
# in general (see sources.crs_extent_warning), so this is a list, not a lookup.
_GEOGRAPHIC_CRS = {
    "EPSG:4326",  # WGS 84
    "EPSG:4258",  # ETRS89
    "EPSG:4269",  # NAD83
    "EPSG:4267",  # NAD27
    "EPSG:4230",  # ED50
    "EPSG:4283",  # GDA94
    "EPSG:4167",  # NZGD2000
    "EPSG:4674",  # SIRGAS 2000
}


def _same_dimension(result: str, like: str) -> str:
    """`result` (an overlay result such as ST_Intersection) reduced to the parts with the
    same dimension as `like`: areas stay areas, lines stay lines, points stay points.

    "intersects" also matches features that only share an edge or a corner, and the
    intersection of two such polygons is a line or a point. Without this, clipping a
    polygon layer by neighbouring polygons (counties, districts, ...) wrote those slivers
    into the output as extra line/point features. A feature left with nothing of its own
    dimension comes out empty;
    callers drop those rows (see _drop_empty).
    """
    return f"ST_CollectionExtract({result}, ST_Dimension({like}) + 1)"


def _drop_empty(select: str) -> str:
    """`select` minus rows whose geometry ended up empty (nothing left after clip/erase)."""
    return f"SELECT * FROM ({select}) WHERE NOT ST_IsEmpty(geom)"


def _transform(col: str, from_crs: str, to_crs: str) -> str:
    if from_crs == to_crs:
        return col
    return f"ST_Transform({col}, {_lit(from_crs)}, {_lit(to_crs)}, always_xy := true)"


def parquet_output_root(out_path: Path, multi: bool) -> Path:
    """Where a GeoParquet output lands: the file itself for a single layer, otherwise
    the folder `<out_path without suffix>/` holding one `<layer>.parquet` per layer."""
    return out_path.with_suffix("") if multi else out_path


def parquet_layer_path(out_path: Path, layer_name: str, multi: bool) -> Path:
    if not multi:
        return out_path
    return parquet_output_root(out_path, multi) / f"{layer_name}.parquet"


def _clear_parquet_output(out_path: Path, multi: bool) -> None:
    """Overwrite for a GeoParquet output: remove the file, or the layer files in its
    folder. Only `*.parquet` files directly in the folder go; anything else is left."""
    root = parquet_output_root(out_path, multi)
    if not multi:
        if root.is_file():
            root.unlink()
    elif root.is_dir():
        for f in root.glob("*.parquet"):
            f.unlink()


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
    def __init__(
        self,
        pipeline: Pipeline,
        log: Callable[[str], None] | None = None,
        parquet_multi: bool | None = None,
    ):
        """`parquet_multi` says whether a GeoParquet output gets one file per layer
        (see parquet_layer_path). run_config sets it from the layer count across *all*
        its pipelines, since they share one output; left None, it's derived from this
        pipeline's own layers."""
        self.p = pipeline
        self.working_crs = pipeline.effective_working_crs
        self.log = log or (lambda m: None)
        self.parquet_multi = parquet_multi

    # -- source views -------------------------------------------------------
    def _create_source_views(
        self,
        con: duckdb.DuckDBPyConnection,
        workdir: Path,
        bbox: tuple[float, float, float, float] | None = None,
        max_features: int | None = None,
    ) -> None:
        for s in self.p.sources:
            # The bbox is only ever applied (via _bbox_filter) to the base source's rows, so
            # only push it down to the base source's own fetch — a join/nearest-neighbor
            # partner just outside the viewport can still be a valid match and must still be
            # fetched in full. Same reasoning for max_features: only the base source's row
            # count is bounded by the preview limit, a join partner still needs its full data.
            src_bbox = bbox if s.id == self.p.base else None
            src_max_features = max_features if s.id == self.p.base else None
            read = src_readers.read_expr(
                s, workdir, con=con, log=self.log, bbox=src_bbox, max_features=src_max_features
            )
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

        fields = {out: f"b.{_ident(col)}" for out, col in step.fields.items()}
        # Deterministic match selection: __src_row is a stable row_number() assigned to
        # each join-source row when its view is created (see _create_source_views), so
        # ranking by it makes "first match" reproducible instead of depending on
        # unspecified query-plan order. "largest_overlap" ranks by intersection area first
        # (negated, since the best match is the lowest rank), falling back to __src_row.
        if step.on_multiple == "largest_overlap":
            rank = ["-ST_Area(ST_Intersection(a.geom, b.geom))", "b.__src_row"]
        else:
            rank = ["b.__src_row"]
        select = self._best_match_select(prev, src_view, f"{pred}(a.geom, b.geom)", rank, fields)
        sql = f"CREATE OR REPLACE TEMP VIEW {_ident(out_view)} AS {select}"
        self.log(f"step {idx}: spatial_join {step.predicate} {step.source} (on_multiple={step.on_multiple})")
        return sql, out_view

    def _apply_nearest_neighbor(self, prev: str, step: NearestNeighbor, idx: int) -> str:
        src_view = _ident(f"src_{step.source}")
        dist_expr = "ST_Distance(a.geom, b.geom)"
        fields = {out: f"b.{_ident(col)}" for out, col in step.fields.items()}
        if step.distance_field:
            fields[step.distance_field] = dist_expr
        # With max_distance, ST_DWithin lets DuckDB use its spatial join (an R-tree over the
        # source). Without it every base row has to be compared against every source row —
        # inherently a full scan per base row, so a large source wants a max_distance.
        on = (
            f"ST_DWithin(a.geom, b.geom, {step.max_distance})"
            if step.max_distance is not None else "true"
        )
        out_view = f"step_{idx}"
        select = self._best_match_select(prev, src_view, on, [dist_expr, "b.__src_row"], fields)
        sql = f"CREATE OR REPLACE TEMP VIEW {_ident(out_view)} AS {select}"
        suffix = f" (max_distance={step.max_distance})" if step.max_distance is not None else ""
        self.log(f"step {idx}: nearest_neighbor {step.source}{suffix}")
        return sql, out_view

    @staticmethod
    def _per_row_match_select(
        prev: str,
        src_view: str,
        on: str,
        agg: str,
        columns: str,
        inner: bool = False,
    ) -> str:
        """SELECT `columns` over every `prev` row (`a`) joined to `m.__agg`: the aggregate
        `agg` over that row's matches in `src_view` (`b`), i.e. the source rows satisfying
        `on`. Unmatched rows get a NULL `m.__agg`, or are dropped when `inner`.

        This replaces per-row `LATERAL (... WHERE <predicate> ...)` subqueries: DuckDB only
        plans a spatial predicate in a plain JOIN as its SPATIAL_JOIN operator (an R-tree
        over one side), while the LATERAL form compared every base row against every source
        row. Against a 1.1M-feature source that's ~2 s instead of not finishing in 10
        minutes for 50 base rows.

        Base rows get a fresh __a_row number to group by — their own __src_row isn't
        unique once a match=all join or a merge has run. The numbered base is MATERIALIZED
        because it's read twice, and row_number() OVER () may number differently each time.
        """
        return f"""
        WITH a AS MATERIALIZED (
            SELECT *, row_number() OVER () AS __a_row FROM {_ident(prev)}
        ),
        m AS (
            SELECT a.__a_row, {agg} AS __agg
            FROM a JOIN {src_view} b ON {on}
            GROUP BY a.__a_row
        )
        SELECT {columns}
        FROM a {'INNER' if inner else 'LEFT'} JOIN m ON m.__a_row = a.__a_row
        ORDER BY a.__a_row
        """

    @classmethod
    def _best_match_select(
        cls,
        prev: str,
        src_view: str,
        on: str,
        rank: list[str],
        fields: dict[str, str],
    ) -> str:
        """SELECT of every `prev` row (`a`) plus `fields` taken from its single best-ranked
        match in `src_view` (`b`): the match with the lowest `rank` tuple among those
        satisfying `on`, or NULL fields when nothing matches. arg_min keeps one running
        best per base row, so memory stays bounded even when `on` is `true`
        (nearest_neighbor without max_distance).
        """
        if not fields:
            return f"SELECT * FROM {_ident(prev)}"
        best = ", ".join(f"{_lit(out)}: {expr}" for out, expr in fields.items())
        pulled = ", ".join(
            f"struct_extract(m.__agg, {_lit(out)}) AS {_ident(out)}" for out in fields
        )
        return cls._per_row_match_select(
            prev, src_view, on,
            agg=f"arg_min({{{best}}}, row({', '.join(rank)}))",
            columns=f"a.* EXCLUDE (__a_row), {pulled}",
        )

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
        # Clipped by the first match (in source row order); unmatched features, and ones
        # that only touch the mask, are dropped.
        clipped = _same_dimension("ST_Intersection(a.geom, m.__agg)", "a.geom")
        select = _drop_empty(self._per_row_match_select(
            prev, src_view, f"{pred}(a.geom, b.geom)",
            agg="arg_min(b.geom, b.__src_row)",
            columns=f"a.* EXCLUDE (__a_row, geom), {clipped} AS geom",
            inner=True,
        ))
        sql = f"CREATE OR REPLACE TEMP VIEW {_ident(out_view)} AS {select}"
        self.log(f"step {idx}: clip {step.predicate} {step.source}")
        return sql, out_view

    def _apply_erase(self, prev: str, step: Erase, idx: int) -> tuple[str, str]:
        pred = _PREDICATE_SQL[step.predicate]
        src_view = _ident(f"src_{step.source}")
        out_view = f"step_{idx}"
        # Each feature minus the union of everything it matches; unmatched ones pass
        # through, and ones erased completely are dropped rather than kept as empty shapes.
        select = _drop_empty(self._per_row_match_select(
            prev, src_view, f"{pred}(a.geom, b.geom)",
            agg="ST_Union_Agg(b.geom)",
            columns=(
                "a.* EXCLUDE (__a_row, geom), "
                "COALESCE(ST_Difference(a.geom, m.__agg), a.geom) AS geom"
            ),
        ))
        sql = f"CREATE OR REPLACE TEMP VIEW {_ident(out_view)} AS {select}"
        self.log(f"step {idx}: erase {step.predicate} {step.source}")
        return sql, out_view

    def _apply_dissolve(self, prev: str, step: Dissolve, idx: int) -> tuple[str, str]:
        out_view = f"step_{idx}"
        # Dissolved features are new rows, so they get a fresh __src_row: later steps and
        # the final SELECT expect every feature to carry one (match ranking when this branch
        # is a join source, and the default "all columns" output, which excludes it).
        if step.by:
            by_cols = ", ".join(_ident(c) for c in step.by)
            sql = f"""
            CREATE OR REPLACE TEMP VIEW {_ident(out_view)} AS
            SELECT *, row_number() OVER () AS __src_row FROM (
                SELECT {by_cols}, ST_Union_Agg(geom) AS geom
                FROM {_ident(prev)}
                GROUP BY {by_cols}
            )
            """
        else:
            sql = f"""
            CREATE OR REPLACE TEMP VIEW {_ident(out_view)} AS
            SELECT ST_Union_Agg(geom) AS geom, 1 AS __src_row
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
        # Only the overlapping area itself: pairs that merely touch would otherwise add
        # line/point slivers to a polygon layer (see _same_dimension).
        overlaid = _same_dimension("ST_Intersection(a.geom, b.geom)", "a.geom")
        pairs = f"""
        SELECT a.* EXCLUDE (geom){(', ' + b_fields) if b_fields else ''},
               {overlaid} AS geom
        FROM {_ident(prev)} a
        JOIN {src_view} b ON ST_Intersects(a.geom, b.geom)
        """
        sql = f"CREATE OR REPLACE TEMP VIEW {_ident(out_view)} AS {_drop_empty(pairs)}"
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
        """Build the step views; returns the main chain's final view.

        With `limit_steps` (previewing up to a given step) it instead returns the view of
        the branch that step ran on, so previewing a step on a snapshot branch (e.g. a buffer
        of the snapshot) shows that step's output rather than the untouched main chain.
        """
        chains: dict[str, str] = {self._MAIN_BRANCH: prev}
        last_branch = self._MAIN_BRANCH

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
                last_branch = step.id
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
            last_branch = target

        return chains[last_branch if limit_steps is not None else self._MAIN_BRANCH]

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
            # ST_Length only measures lines (0 for polygons) and ST_Perimeter only polygons
            # (0 for lines), so their sum is the length of a line, the perimeter of a polygon,
            # and the total of both for a mixed collection.
            return "(ST_Length(geom) + ST_Perimeter(geom))"
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
        # __centroid4326 is the centroid taken in working_crs, then reprojected: a centroid
        # computed on lon/lat degrees is distorted (~100 km off for a line spanning Norway).
        select_cols = (
            f"{cols},\n            " if cols
            else "* EXCLUDE (geom, __geom4326, __centroid4326, __src_row),\n            "
        )
        # With no mapping, every upstream column is written — but not GDAL's OGC_FID: ST_Read
        # adds it for GeoJSON (and other FID-less) sources, and the GeoPackage writer rejects a
        # field by that name, since it's GDAL's default name for the feature ID itself.
        # COLUMNS(lambda) rather than EXCLUDE, which errors when the column isn't there (most
        # sources have none); it can't come up empty here because `geom` always survives.
        if not cols:
            source = f"(SELECT COLUMNS(lambda c: lower(c) <> 'ogc_fid') FROM {source})"
        return f"""
        WITH base4326 AS (
            SELECT *,
                {_transform('geom', self.working_crs, 'EPSG:4326')} AS __geom4326
            FROM {source}
        ),
        enriched AS (
            SELECT *,
                {_transform('ST_Centroid(geom)', self.working_crs, 'EPSG:4326')} AS __centroid4326
            FROM base4326
        )
        SELECT
            {select_cols}{geom_out} AS geom
        FROM enriched
        """

    def _check_working_crs_units(self) -> None:
        """Refuse to run distance/area operations in a geographic working CRS.

        Buffer distances, nearest_neighbor's max_distance/distance_field and the area/length
        mapping funcs are all in working_crs units. In a degrees CRS a `buffer: 500` meant as
        metres becomes 500 degrees and Oslo–Bergen measures 5.45 — silently wrong rather
        than an error, which is why this is checked up front.
        """
        if self.working_crs.upper() not in _GEOGRAPHIC_CRS:
            return
        uses: list[str] = []
        for i, step in enumerate(self.p.steps, start=1):
            if isinstance(step, Buffer):
                uses.append(f"step {i} (buffer)")
            elif isinstance(step, NearestNeighbor) and (
                step.max_distance is not None or step.distance_field
            ):
                uses.append(f"step {i} (nearest_neighbor distance)")
        uses += [f"derived source '{ds.id}' (buffer)" for ds in self.p.derived_sources if ds.buffer is not None]
        mappings = [self.p.mapping] + [l.mapping for o in self.p.outputs for l in o.layers if l.mapping]
        funcs = sorted({m.func for mp in mappings for m in mp if m.func in ("area", "length")})
        uses += [f"mapping func '{f}'" for f in funcs]
        if not uses:
            return
        origin = (
            "" if self.p.working_crs
            else f" (inherited from base source '{self.p.base}', since working_crs isn't set)"
        )
        raise ValueError(
            f"working CRS {self.working_crs}{origin} is in degrees, but {', '.join(uses)} "
            f"measure distance or area in working CRS units, so they'd be in degrees too. "
            f"Set working_crs to a projected CRS in metres, e.g. EPSG:25833 for Norway."
        )

    # -- run ----------------------------------------------------------------
    def _prepare(
        self,
        con,
        workdir: Path,
        bbox: tuple[float, float, float, float] | None = None,
        max_features: int | None = None,
        limit_steps: int | None = None,
    ) -> str:
        """Build the source → step_0 → step_N view chain; returns the final view name.

        When `bbox` is given the base view is pre-filtered to it before any steps run,
        not just at the very end. Steps (spatial joins especially) otherwise run over the
        entire base table only to have everything but the visible viewport thrown away
        afterwards — for a big base table with real join steps that's the difference
        between an instant "in view" preview and a very slow one. Callers that filter by
        bbox must still re-apply it to the final SELECT, since a geometry-mutating step
        (buffer, dissolve, ...) can move a feature relative to it.
        """
        self._check_working_crs_units()
        self._create_source_views(con, workdir, bbox=bbox, max_features=max_features)
        self._create_derived_source_views(con)
        con.execute(
            f"CREATE OR REPLACE TEMP VIEW {_ident('step_0')} AS "
            f"SELECT * FROM {_ident('src_' + self.p.base)} "
            f"{self._bbox_filter(bbox, self.working_crs) if self.p.base_source.has_geometry else ''}"
        )
        return self._build_step_views(con, "step_0", limit_steps=limit_steps)

    def run(self) -> str:
        with DUCKDB_LOCK:
            con = duckdb.connect()
            init_duckdb(con)

            with tempfile.TemporaryDirectory() as tmp:
                workdir = Path(tmp)
                prev = self._prepare(con, workdir)

                deleted_paths: set[Path] = set()
                out_paths: list[str] = []
                for out in self.p.outputs:
                    out_path = Path(out.path)
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    if is_parquet_path(out_path):
                        multi = self._parquet_multi(out_path)
                        if out.overwrite and out_path not in deleted_paths:
                            _clear_parquet_output(out_path, multi)
                            deleted_paths.add(out_path)
                        for layer in out.layers:
                            self._write_parquet_layer(
                                con, self._final_select(prev, layer), layer,
                                parquet_layer_path(out_path, layer.layer, multi),
                            )
                        root = str(parquet_output_root(out_path, multi))
                        if root not in out_paths:
                            out_paths.append(root)
                        continue
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

    def _parquet_multi(self, out_path: Path) -> bool:
        if self.parquet_multi is not None:
            return self.parquet_multi
        return sum(len(o.layers) for o in self.p.outputs if Path(o.path) == out_path) > 1

    def _write_parquet_layer(self, con, final_sql: str, layer: OutputLayer, target: Path) -> None:
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
        sql = final_sql
        if self.p.base_source.has_geometry:
            sql = (
                f"SELECT * REPLACE (geom::GEOMETRY::GEOMETRY({_lit(layer.crs)}) AS geom) "
                f"FROM ({final_sql})"
            )
        self.log(f"writing {target} ({layer.crs})")
        con.execute(f"COPY ({sql}) TO {_lit(str(target))} (FORMAT PARQUET, COMPRESSION ZSTD)")

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

    def _preview_rows_sql(
        self,
        source_expr: str,
        crs: str,
        has_geom: bool,
        bbox: tuple[float, float, float, float] | None,
        limit: int,
    ) -> str:
        """Row-preview SELECT over `source_expr` (a view name or a parenthesised subquery)."""
        if not has_geom:
            return f"SELECT * FROM {source_expr} LIMIT {limit}"
        return (
            f"SELECT * EXCLUDE (geom), "
            f"ST_AsGeoJSON(ST_Transform(geom, {_lit(crs)}, 'EPSG:4326', always_xy := true)) AS __geojson "
            f"FROM {source_expr} {self._bbox_filter(bbox, crs)}LIMIT {limit}"
        )

    def preview(
        self,
        limit: int = 50,
        preview_until_step: int | None = None,
        bbox: tuple[float, float, float, float] | None = None,
    ) -> list[dict]:
        with DUCKDB_LOCK:
            con = duckdb.connect()
            init_duckdb(con)

            with tempfile.TemporaryDirectory() as tmp:
                workdir = Path(tmp)
                prev = self._prepare(
                    con, workdir, bbox=bbox, max_features=limit,
                    limit_steps=preview_until_step,
                )

                if preview_until_step is not None:
                    try:
                        con.execute(f"DESCRIBE SELECT * FROM {_ident(prev)}")
                        cols_desc = con.fetchall()
                        has_geom = any(col[0] == "geom" for col in cols_desc)
                    except Exception:
                        has_geom = False
                    preview_sql = self._preview_rows_sql(
                        _ident(prev), self.working_crs, has_geom, bbox, limit
                    )
                else:
                    layer = self.p.outputs[0].layers[0]
                    final_sql = self._final_select(prev, layer)
                    preview_sql = self._preview_rows_sql(
                        f"({final_sql})", layer.crs,
                        self.p.base_source.has_geometry, bbox, limit,
                    )

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
    """Run all pipelines in a Config, appending each one's layers to the shared output.

    Returns the GeoPackage path, or for GeoParquet the file (one layer in total) or the
    folder of per-layer files (several).
    """
    out_path = Path(config.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    multi = None
    if is_parquet_path(out_path):
        multi = config.layer_count > 1
        if config.overwrite:
            _clear_parquet_output(out_path, multi)
    elif config.overwrite and out_path.exists():
        out_path.unlink()
    for pdef in config.pipelines:
        Engine(pdef.to_pipeline(config.output), log=log, parquet_multi=multi).run()
    if multi is not None:
        return str(parquet_output_root(out_path, multi))
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




