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

A step with `rejects` splits its rows like an FME transformer's Passed/Failed ports: its
SELECT carries an extra `__matched` column, a `step_<i>__split` view holds both halves,
`step_<i>` the passed rows and `step_<i>__rejects` the rest, which run() writes as an extra
output layer.

Every view goes through Engine._emit, which also records it in `Engine.plan`, so the same
chain serves three callers: run/preview (views), plan() (no connection: the SQL only, for
the editor's SQL tab) and counts() (tables, so each step is computed once and counted).
"""
from __future__ import annotations

import re
import sqlite3
import tempfile
import textwrap
from pathlib import Path
from typing import Callable

import duckdb

from . import sources as src_readers
from .config import (
    AttributeJoin, Buffer, Centroid, Clip, CodeCase, CodeList, Config,
    Dissolve, Erase, Filter, IntersectOverlay, LineOverlay, MapItem, Merge, NearestNeighbor,
    OutputLayer, Pipeline, Snapshot, SpatialJoin, is_parquet_path, reject_layer_names,
)
from .derive import DUCKDB_LOCK, init_duckdb
from .sql_util import quote_ident as _ident, quote_literal as _lit

_PREDICATE_SQL = {
    "intersects": "ST_Intersects",
    "contains": "ST_Contains",
    "within": "ST_Within",
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


# Column types GDAL's GeoPackage writer crashes on (a native crash, no error; DuckDB spatial
# through 1.5.x), at any depth inside a LIST/STRUCT/MAP: 128-bit integers, and the DECIMALs
# wider than 18 digits that are stored as them. sum() of any integer or DECIMAL column
# returns one, so a mapping expr can easily produce them.
_GPKG_HUGE_INT = re.compile(r"\bU?HUGEINT\b")
_GPKG_WIDE_DECIMAL = re.compile(r"\bDECIMAL\((\d+),\s*\d+\)")
_INT64_MIN, _INT64_MAX = -(2**63), 2**63 - 1


class MappingError(ValueError):
    """A mapping item that doesn't parse or bind (see Engine._check_mapping).

    The message starts with a line naming the item, for the editor's status, followed by
    the item's config path and the error on the next line: the shape of a validation error,
    which the editor's Problems tab turns into a link to the mapping row."""

    def __init__(self, path: str, item, error: Exception):
        detail = str(error).splitlines()[0].strip()
        kind = next(
            k.rstrip("_") for k in ("expr", "from_", "func", "codelist", "const")
            if getattr(item, k, None) is not None
        )
        head = f"mapping '{item.to}' ({kind}): {detail}"
        super().__init__("\n".join([head, path, f"  {detail}"]))
        self.path = path


def _gpkg_safe_type(dtype: str) -> str:
    """`dtype` with those types swapped for what a GeoPackage stores anyway: a 64-bit
    INTEGER and a REAL (it has no exact decimals: a DECIMAL(18, 2) is written as REAL too)."""
    dtype = _GPKG_HUGE_INT.sub("BIGINT", dtype)
    return _GPKG_WIDE_DECIMAL.sub(lambda m: "DOUBLE" if int(m[1]) > 18 else m[0], dtype)


def _gpkg_safe_select(
    con: duckdb.DuckDBPyConnection, select: str, layer: str, log: Callable[[str], None],
) -> str:
    """`select` with every column GeoPackage can't take cast by _gpkg_safe_type. A top-level
    integer beyond 64 bits fails the write with an error naming the column."""
    replace = []
    for name, dtype, *_ in con.execute(f"DESCRIBE SELECT * FROM ({select})").fetchall():
        dtype = str(dtype)
        safe = _gpkg_safe_type(dtype)
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
        pipeline_index: int = 0,
    ):
        """`parquet_multi` says whether a GeoParquet output gets one file per layer
        (see parquet_layer_path). run_config sets it from the layer count across *all*
        its pipelines, since they share one output; left None, it's derived from this
        pipeline's own layers."""
        self.p = pipeline
        # Its position in the config, for the `pipelines.<i>.mapping.<j>` paths of MappingError.
        self.pipeline_index = pipeline_index
        self.working_crs = pipeline.effective_working_crs
        self.log = log or (lambda m: None)
        self.parquet_multi = parquet_multi
        # Filled by _prepare: every view it created, in order (see _emit), and the view
        # holding each step's output and rejects, keyed by 1-based step number.
        self.plan: list[dict] = []
        self.step_views: dict[int, str] = {}
        self.reject_views: dict[int, str] = {}
        # Filled by run(): one {"layer", "rows", "kind", "path"} per layer written.
        self.written: list[dict] = []
        # counts() sets this so steps become tables, each computed once (see _emit).
        self._materialize = False

    def _emit(
        self, con: duckdb.DuckDBPyConnection | None, view: str, select: str, kind: str,
        table: bool = False, **meta,
    ) -> None:
        """CREATE `view` AS `select` and record it in self.plan.

        Steps are TEMP VIEWs, so DuckDB plans the whole chain as one query, except in runs
        and while counting rows (counts()), where they're TEMP TABLEs: writing N layers or
        counting N views would compute the chain up to each of them again, N times in total.
        `table` makes a TEMP TABLE regardless (a snapshot read again later, see
        _build_step_views). Source views always stay views, since a table would read every
        source in full. Without a connection (plan()) the view is only recorded.
        """
        self.plan.append({"kind": kind, "view": view, "sql": textwrap.dedent(select).strip(), **meta})
        if con is None:
            return
        steps_as_tables = self._materialize and kind not in ("source", "derived", "branch")
        obj = "TABLE" if table or steps_as_tables else "VIEW"
        con.execute(f"CREATE OR REPLACE TEMP {obj} {_ident(view)} AS {select}")

    # -- source views -------------------------------------------------------
    def _plan_read_expr(self, s) -> str:
        """read_expr for plan(), which mustn't fetch anything: services and databases get a
        placeholder instead of being downloaded or attached just to show the SQL."""
        if s.format in ("arcgis_rest", "oapif", "wfs"):
            what = f"{s.uri} ({s.layer})" if s.layer else s.uri
            return f"ST_Read('<{s.format}: {what}, downloaded to a temp file at run time>')"
        if s.format == "postgres":
            # Not the uri: a connection string usually holds the password.
            schema, table = src_readers._split_pg_layer(s.layer)
            return f"{_ident(s.id)}.{_ident(schema)}.{_ident(table)}"
        return src_readers.read_expr(s, Path(tempfile.gettempdir()), con=None)

    def _create_source_views(
        self,
        con: duckdb.DuckDBPyConnection | None,
        workdir: Path,
        bbox: tuple[float, float, float, float] | None = None,
        max_features: int | None = None,
    ) -> None:
        for s in self.p.sources:
            if con is None:
                read = self._plan_read_expr(s)
            else:
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
            view = f"src_{s.id}"
            if s.has_geometry:
                crs = "EPSG:4326" if s.format == "arcgis_rest" else (s.crs or self.working_crs)
                # Source geometry is reprojected to working_crs exactly once, here. Every
                # later CRS-dependent value (lon/lat/mgrs, output CRS, etc.) is derived from
                # this same working_crs geometry rather than re-transforming from the source
                # CRS, so there's a single reprojection chain per feature (no double rounding).
                geom = _transform("geom", crs, self.working_crs)
                if s.force_2d:
                    geom = f"ST_Force2D({geom})"
                if s.make_valid:
                    geom = f"ST_MakeValid({geom})"
                select = (
                    f"SELECT * EXCLUDE (geom), {geom} AS geom, "
                    f"row_number() OVER () AS __src_row FROM {read}"
                )
            else:
                select = f"SELECT * FROM {read}"
            self.log(f"source '{s.id}' ({s.format}) -> {_ident(view)}")
            self._emit(con, view, select, "source", id=s.id, title=f"source '{s.id}' ({s.format})")

    def _resolve_has_geometry(self, sid: str) -> bool:
        for ds in self.p.derived_sources:
            if ds.id == sid:
                return self._resolve_has_geometry(ds.from_)
        return self.p.source(sid).has_geometry

    def _create_derived_source_views(self, con: duckdb.DuckDBPyConnection | None) -> None:
        for ds in self.p.derived_sources:
            from_view = _ident(f"src_{ds.from_}")
            where_clause = f"WHERE {ds.where}" if ds.where else ""
            if self._resolve_has_geometry(ds.from_):
                geom = "geom"
                if ds.buffer is not None:
                    geom = f"ST_Buffer({geom}, {ds.buffer})"
                if ds.make_valid:
                    geom = f"ST_MakeValid({geom})"
                select = f"SELECT * EXCLUDE (geom), {geom} AS geom FROM {from_view} {where_clause}"
            else:
                select = f"SELECT * FROM {from_view} {where_clause}"
            self.log(f"derived_source '{ds.id}' <- {ds.from_}")
            self._emit(
                con, f"src_{ds.id}", select, "derived",
                id=ds.id, title=f"derived source '{ds.id}' (from '{ds.from_}')",
            )

    # -- steps --------------------------------------------------------------
    # Each _apply_* returns (SELECT, view name) for its step; _build_step_views creates the
    # view. With `step.rejects` set, the SELECT carries a boolean `__matched` column that
    # _build_step_views splits on (see the module docstring).
    def _apply_spatial_join(self, prev: str, step: SpatialJoin, idx: int) -> tuple[str, str]:
        pred = _PREDICATE_SQL[step.predicate]
        src_view = _ident(f"src_{step.source}")
        out_view = f"step_{idx}"

        if step.match == "all":
            b_fields = ", ".join(
                f"b.{_ident(col)} AS {_ident(out)}" for out, col in step.fields.items()
            )
            # A matching source row always has a geometry, an unmatched base row gets NULL.
            matched = ", b.geom IS NOT NULL AS __matched" if step.rejects else ""
            sql = f"""
            SELECT a.*{(', ' + b_fields) if b_fields else ''}{matched}
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
        select = self._best_match_select(
            prev, src_view, f"{pred}(a.geom, b.geom)", rank, fields, flag=bool(step.rejects)
        )
        self.log(f"step {idx}: spatial_join {step.predicate} {step.source} (on_multiple={step.on_multiple})")
        return select, out_view

    def _apply_nearest_neighbor(self, prev: str, step: NearestNeighbor, idx: int) -> tuple[str, str]:
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
        select = self._best_match_select(
            prev, src_view, on, [dist_expr, "b.__src_row"], fields, flag=bool(step.rejects)
        )
        suffix = f" (max_distance={step.max_distance})" if step.max_distance is not None else ""
        self.log(f"step {idx}: nearest_neighbor {step.source}{suffix}")
        return select, out_view

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
        flag: bool = False,
    ) -> str:
        """SELECT of every `prev` row (`a`) plus `fields` taken from its single best-ranked
        match in `src_view` (`b`): the match with the lowest `rank` tuple among those
        satisfying `on`, or NULL fields when nothing matches. arg_min keeps one running
        best per base row, so memory stays bounded even when `on` is `true`
        (nearest_neighbor without max_distance). `flag` adds `__matched`.
        """
        if not fields and not flag:
            return f"SELECT * FROM {_ident(prev)}"
        if fields:
            best = ", ".join(f"{_lit(out)}: {expr}" for out, expr in fields.items())
            agg = f"arg_min({{{best}}}, row({', '.join(rank)}))"
            pulled = "".join(
                f", struct_extract(m.__agg, {_lit(out)}) AS {_ident(out)}" for out in fields
            )
        else:
            # Nothing to pull: only whether there's a match matters.
            agg, pulled = "count(*)", ""
        matched = ", m.__a_row IS NOT NULL AS __matched" if flag else ""
        return cls._per_row_match_select(
            prev, src_view, on, agg=agg,
            columns=f"a.* EXCLUDE (__a_row){pulled}{matched}",
        )

    def _apply_attribute_join(self, prev: str, step: AttributeJoin, idx: int) -> tuple[str, str]:
        src_view = _ident(f"src_{step.source}")
        pulled = ", ".join(f"j.{_ident(out)}" for out in step.fields)
        inner = [f"b.{_ident(col)} AS {_ident(out)}" for out, col in step.fields.items()]
        matched = ""
        if step.rejects:
            inner.append("1 AS __hit")
            matched = ", j.__hit IS NOT NULL AS __matched"
        out_view = f"step_{idx}"
        sql = f"""
        SELECT a.*{(', ' + pulled) if pulled else ''}{matched}
        FROM {_ident(prev)} a
        LEFT JOIN LATERAL (
            SELECT {', '.join(inner) if inner else '1'}
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
        SELECT * EXCLUDE (geom), ST_Buffer(geom, {step.distance}) AS geom
        FROM {_ident(prev)}
        """
        self.log(f"step {idx}: buffer {step.distance}")
        return sql, out_view

    def _apply_centroid(self, prev: str, _step: Centroid, idx: int) -> tuple[str, str]:
        out_view = f"step_{idx}"
        sql = f"""
        SELECT * EXCLUDE (geom), ST_Centroid(geom) AS geom
        FROM {_ident(prev)}
        """
        self.log(f"step {idx}: centroid")
        return sql, out_view

    def _apply_clip(self, prev: str, step: Clip, idx: int) -> tuple[str, str]:
        pred = _PREDICATE_SQL[step.predicate]
        src_view = _ident(f"src_{step.source}")
        out_view = f"step_{idx}"
        on = f"{pred}(a.geom, b.geom)"
        # Clipped by the first match (in source row order); unmatched features, and ones
        # that only touch the mask, are dropped.
        clipped = _same_dimension("ST_Intersection(a.geom, m.__agg)", "a.geom")
        agg = "arg_min(b.geom, b.__src_row)"
        self.log(f"step {idx}: clip {step.predicate} {step.source}")
        if not step.rejects:
            select = _drop_empty(self._per_row_match_select(
                prev, src_view, on, agg=agg,
                columns=f"a.* EXCLUDE (__a_row, geom), {clipped} AS geom",
                inner=True,
            ))
            return select, out_view
        # The same rows as above pass; every other feature is a reject and keeps its
        # unclipped geometry.
        per_row = self._per_row_match_select(
            prev, src_view, on, agg=agg,
            columns=f"a.* EXCLUDE (__a_row), {clipped} AS __clipped, m.__agg IS NOT NULL AS __hit",
        )
        select = f"""
        SELECT * EXCLUDE (geom, __clipped, __hit, __matched),
               CASE WHEN __matched THEN __clipped ELSE geom END AS geom, __matched
        FROM (
            SELECT *, COALESCE(__hit AND NOT ST_IsEmpty(__clipped), false) AS __matched
            FROM ({per_row})
        )
        """
        return select, out_view

    def _apply_erase(self, prev: str, step: Erase, idx: int) -> tuple[str, str]:
        pred = _PREDICATE_SQL[step.predicate]
        src_view = _ident(f"src_{step.source}")
        out_view = f"step_{idx}"
        select = self._erase_select(prev, src_view, f"{pred}(a.geom, b.geom)")
        self.log(f"step {idx}: erase {step.predicate} {step.source}")
        return select, out_view

    @classmethod
    def _erase_select(cls, prev: str, src_view: str, on: str) -> str:
        """Each `prev` feature minus the union of everything it matches in `src_view`;
        unmatched ones pass through, and ones erased completely are dropped rather than
        kept as empty shapes."""
        return _drop_empty(cls._per_row_match_select(
            prev, src_view, on,
            agg="ST_Union_Agg(b.geom)",
            columns=(
                "a.* EXCLUDE (__a_row, geom), "
                "COALESCE(ST_Difference(a.geom, m.__agg), a.geom) AS geom"
            ),
        ))

    def _apply_dissolve(self, prev: str, step: Dissolve, idx: int) -> tuple[str, str]:
        out_view = f"step_{idx}"
        # Dissolved features are new rows, so they get a fresh __src_row: later steps and
        # the final SELECT expect every feature to carry one (match ranking when this branch
        # is a join source, and the default "all columns" output, which excludes it).
        if step.by:
            by_cols = ", ".join(_ident(c) for c in step.by)
            sql = f"""
            SELECT *, row_number() OVER () AS __src_row FROM (
                SELECT {by_cols}, ST_Union_Agg(geom) AS geom
                FROM {_ident(prev)}
                GROUP BY {by_cols}
            )
            """
        else:
            sql = f"""
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
        self.log(f"step {idx}: intersect_overlay {step.source}")
        return _drop_empty(pairs), out_view

    # line_overlay without a `tolerance`: lines must lie on each other to within this many
    # working-CRS units (a micrometre in a metric CRS), which is "exactly" for real data.
    _LINE_OVERLAY_EXACT = 1e-6
    # A stretch only counts as shared when the source line runs along it: its end points'
    # closest points on the source line must be at least this fraction of the stretch's own
    # length apart (the cosine of the angle between the lines, ~18°). Lines that only cross
    # otherwise leave a stretch of 2 x tolerance around the crossing.
    _LINE_OVERLAY_MIN_ALIGNMENT = 0.95

    def _apply_line_overlay(self, prev: str, step: LineOverlay, idx: int) -> tuple[str, str]:
        """Each running line cut where it lies within `tolerance` of a source line; every
        piece is one row, with `fields` from the source line it lies on (the first one, in
        source row order, where several do) or NULL.

        Overlaying the lines directly (ST_Intersection) only finds the shared stretches when
        the coordinates match to the last bit; lines that are equal to within a centimetre
        come back as points. Instead, each line is matched against the source lines buffered
        by the tolerance, which yields the stretches as positions along the line itself
        (ST_LineLocatePoint). The line is cut at all of them with ST_LineSubstring, so
        neighbouring pieces share their end points exactly and the geometry stays the
        running line's own, not the source's.
        """
        out_view = f"step_{idx}"
        tol = step.tolerance if step.tolerance is not None else self._LINE_OVERLAY_EXACT
        src_view = _ident(f"src_{step.source}")
        f_struct = ", ".join(
            [f"{_lit(out)}: b.{_ident(col)}" for out, col in step.fields.items()]
            + ["'__b_row': b.__src_row"]
        )
        pulled = "".join(
            f", struct_extract(g.__agg, {_lit(out)}) AS {_ident(out)}" for out in step.fields
        )
        is_line = "ST_GeometryType(geom) IN ('LINESTRING', 'MULTILINESTRING')"
        select = f"""
        WITH p AS MATERIALIZED (
            -- the running rows, read once: the lines below and the pass-through at the end
            -- both come from here, since reading the previous view twice would compute it
            -- (and every step before it) twice, doubling with each chained line_overlay
            SELECT * FROM {_ident(prev)}
        ),
        a AS MATERIALIZED (
            -- one row per line part, so positions along it are well defined
            SELECT * EXCLUDE (__part), __part.geom AS geom, row_number() OVER () AS __a_row
            FROM (
                SELECT * EXCLUDE (geom), unnest(ST_Dump(geom)) AS __part
                FROM p WHERE {is_line}
            )
        ),
        b AS (
            SELECT *, ST_Buffer(geom, {tol}, 8, 'CAP_FLAT', 'JOIN_ROUND', 1.0) AS __buf
            FROM {src_view}
        ),
        stretch AS (
            -- Positions are found on a 2D copy of the line: ST_LineLocatePoint crashes the
            -- process on lines with Z or M (NVDB exports are 3D). ST_LineSubstring measures
            -- along the line in 2D too, so the cut below still keeps the line's own Z.
            SELECT a.__a_row, ST_Force2D(a.geom) AS __a, b.geom AS __b, {{{f_struct}}} AS __f,
                   unnest(ST_Dump(ST_CollectionExtract(ST_Intersection(ST_Force2D(a.geom), b.__buf), 2))).geom AS __s
            FROM a JOIN b ON ST_Intersects(a.geom, b.__buf)
        ),
        span AS (
            SELECT __a_row, __f,
                   least(ST_LineLocatePoint(__a, ST_StartPoint(__s)),
                         ST_LineLocatePoint(__a, ST_EndPoint(__s))) AS lo,
                   greatest(ST_LineLocatePoint(__a, ST_StartPoint(__s)),
                            ST_LineLocatePoint(__a, ST_EndPoint(__s))) AS hi
            FROM stretch
            WHERE ST_Distance(ST_ClosestPoint(__b, ST_StartPoint(__s)), ST_ClosestPoint(__b, ST_EndPoint(__s)))
                  >= {self._LINE_OVERLAY_MIN_ALIGNMENT} * ST_Distance(ST_StartPoint(__s), ST_EndPoint(__s))
        ),
        cut AS (
            SELECT __a_row, f AS lo, lead(f) OVER (PARTITION BY __a_row ORDER BY f) AS hi
            FROM (
                SELECT c.__a_row, c.f, lag(c.f) OVER (PARTITION BY c.__a_row ORDER BY c.f) AS prev_f,
                       {tol} / nullif(ST_Length(a.geom), 0) AS near
                FROM (
                    SELECT __a_row, 0.0 AS f FROM a UNION SELECT __a_row, 1.0 FROM a
                    UNION SELECT __a_row, lo FROM span UNION SELECT __a_row, hi FROM span
                ) c JOIN a ON a.__a_row = c.__a_row
            )
            -- Positions within the tolerance of each other are one cut, and the line's own
            -- ends stay where they are: a source line stopping 2 cm short of where the path
            -- ends (or overshooting it) shouldn't leave a 2 cm piece behind.
            WHERE f = 0 OR f = 1 OR (f - prev_f > near AND f < 1 - near)
        ),
        piece AS (
            -- the first source line (in source row order) covering each piece's middle
            SELECT c.__a_row, c.lo, c.hi, arg_min(s.__f, s.__f.__b_row) AS __agg
            FROM cut c
            LEFT JOIN span s ON s.__a_row = c.__a_row AND (c.lo + c.hi) / 2 BETWEEN s.lo AND s.hi
            WHERE c.hi > c.lo
            GROUP BY ALL
        ),
        run AS (
            -- consecutive pieces on the same source line (cut where two of its stretches
            -- meet, or where another line's stretch began and ended inside it) become one
            SELECT *, sum(CASE WHEN __agg.__b_row IS NOT DISTINCT FROM prev_b THEN 0 ELSE 1 END)
                          OVER (PARTITION BY __a_row ORDER BY lo) AS __run
            FROM (SELECT *, lag(__agg.__b_row) OVER (PARTITION BY __a_row ORDER BY lo) AS prev_b FROM piece)
        ),
        g AS (
            SELECT __a_row, min(lo) AS lo, max(hi) AS hi, any_value(__agg) AS __agg
            FROM run GROUP BY __a_row, __run
        )
        SELECT a.* EXCLUDE (__a_row, geom){pulled},
               ST_LineSubstring(a.geom, g.lo, g.hi) AS geom
        FROM g JOIN a ON a.__a_row = g.__a_row
        UNION ALL BY NAME
        -- anything that isn't a line (or has no geometry) passes through untouched
        SELECT * FROM p WHERE geom IS NULL OR NOT {is_line}
        """
        suffix = f" (tolerance={step.tolerance})" if step.tolerance is not None else ""
        self.log(f"step {idx}: line_overlay {step.source}{suffix}")
        return select, out_view

    def _apply_filter(self, prev: str, step: Filter, idx: int) -> tuple[str, str]:
        out_view = f"step_{idx}"
        if step.rejects:
            sql = f"SELECT *, ({step.where}) IS TRUE AS __matched FROM {_ident(prev)}"
        else:
            sql = f"""
            SELECT * FROM {_ident(prev)}
            WHERE {step.where}
            """
        self.log(f"step {idx}: filter {step.where}")
        return sql, out_view

    def _apply_merge(self, prev: str, step: Merge, idx: int) -> tuple[str, str]:
        src_view = _ident(f"src_{step.source}")
        out_view = f"step_{idx}"
        sql = f"""
        SELECT * FROM {_ident(prev)}
        UNION ALL BY NAME
        SELECT * FROM {src_view}
        """
        self.log(f"step {idx}: merge {step.source}")
        return sql, out_view

    _MAIN_BRANCH = "__main__"

    def _sync_branch_view(
        self, con: duckdb.DuckDBPyConnection | None, chains: dict[str, str], name: str, step: int,
    ) -> None:
        # Any branch a later step's source: might reference needs a src_<id>
        # view kept up to date; the main chain isn't referenced that way.
        if name == self._MAIN_BRANCH:
            return
        self._emit(
            con, f"src_{name}", f"SELECT * FROM {_ident(chains[name])}", "branch",
            id=name, step=step, title=f"branch '{name}' after step {step}",
        )

    @staticmethod
    def _pulled_columns(step) -> list[str]:
        """Columns a join step adds, which are always NULL on its rejects."""
        cols = list(getattr(step, "fields", {}) or {})
        if isinstance(step, NearestNeighbor) and step.distance_field:
            cols.append(step.distance_field)
        return cols

    def _emit_step(self, con: duckdb.DuckDBPyConnection | None, step, i: int, select: str, view: str) -> None:
        """Create step `i`'s view; with `rejects`, split it into passed and rejected rows."""
        title = f"step {i}: {step.type}"
        if not getattr(step, "rejects", None):
            self._emit(con, view, select, "step", step=i, title=title)
            return
        split = f"{view}__split"
        self._emit(con, split, select, "split", step=i, title=f"{title} (passed and rejected rows)")
        self._emit(
            con, view, f"SELECT * EXCLUDE (__matched) FROM {_ident(split)} WHERE __matched",
            "step", step=i, title=title,
        )
        drop = ", ".join(_ident(c) for c in ["__matched", *self._pulled_columns(step)])
        rejects = f"{view}__rejects"
        self._emit(
            con, rejects, f"SELECT * EXCLUDE ({drop}) FROM {_ident(split)} WHERE NOT __matched",
            "rejects", step=i, layer=step.rejects, title=f"step {i}: rejects → layer '{step.rejects}'",
        )
        self.reject_views[i] = rejects

    def _snapshot_used(self, sid: str, idx: int, limit_steps: int | None) -> bool:
        """Whether a step after step `idx` (up to `limit_steps`) reads snapshot `sid`."""
        later = self.p.steps[idx:limit_steps]
        return any(getattr(s, "source", None) == sid or s.branch == sid for s in later)

    def _build_step_views(self, con: duckdb.DuckDBPyConnection | None, prev: str, limit_steps: int | None = None) -> str:
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
                if not self._materialize and self._snapshot_used(step.id, i, limit_steps):
                    # ...except when a later step reads the snapshot: the chain so far is then
                    # read twice (by the branch it was taken from, and through the snapshot),
                    # and as a view, computed twice, doubling with every snapshot used this
                    # way. Stored once as a table, both read that. Nothing is lost: a step that
                    # reads a snapshot needs all of its rows, so a preview's LIMIT couldn't cut
                    # it short anyway. (Runs and counts() store every step already.)
                    view = f"step_{i}"
                    self._emit(
                        con, view, f"SELECT * FROM {_ident(chains[source_branch])}", "step",
                        table=True, step=i,
                        title=f"step {i}: snapshot '{step.id}' (stored once, read again later)",
                    )
                    chains[source_branch] = view
                    self._sync_branch_view(con, chains, source_branch, i)
                chains[step.id] = chains[source_branch]
                self._sync_branch_view(con, chains, step.id, i)
                self.step_views[i] = chains[step.id]
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
            elif isinstance(step, LineOverlay):
                sql, new_view = self._apply_line_overlay(cur, step, i)
            elif isinstance(step, Filter):
                sql, new_view = self._apply_filter(cur, step, i)
            elif isinstance(step, Merge):
                sql, new_view = self._apply_merge(cur, step, i)
            else:  # pragma: no cover
                raise ValueError(f"unknown step type: {step}")
            self._emit_step(con, step, i, sql, new_view)
            chains[target] = new_view
            self.step_views[i] = new_view
            self._sync_branch_view(con, chains, target, i)
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
        if func == "seq":
            return "row_number() OVER ()"
        if func == "now":
            return "current_timestamp"
        if func == "today":
            return "current_date"
        if func == "lon":
            return "ROUND(ST_X(__centroid4326), 7)"
        if func == "lat":
            return "ROUND(ST_Y(__centroid4326), 7)"
        if func == "x":
            return "ROUND(ST_X(ST_Centroid(geom)), 3)"
        if func == "y":
            return "ROUND(ST_Y(ST_Centroid(geom)), 3)"
        if func == "mgrs":
            return "to_mgrs(ST_X(__centroid4326), ST_Y(__centroid4326))"
        if func == "geohash":
            return "to_geohash(ST_X(__centroid4326), ST_Y(__centroid4326))"
        if func == "wkb":
            return "ST_AsHEXWKB(__geom4326)"
        if func == "wkt":
            return "ST_AsText(__geom4326)"
        if func == "geom_type":
            return "ST_GeometryType(geom)::VARCHAR"
        if func == "area":
            return "ST_Area(geom)"
        if func == "length":
            # ST_Length only measures lines (0 for polygons) and ST_Perimeter only polygons
            # (0 for lines), so their sum is the length of a line, the perimeter of a polygon,
            # and the total of both for a mixed collection.
            return "(ST_Length(geom) + ST_Perimeter(geom))"
        raise ValueError(f"unknown func: {func}")

    def _check_mapping(self, con: duckdb.DuckDBPyConnection, prev: str, layer: OutputLayer) -> None:
        """Plan (not run) the SELECT written for `layer`; if a mapping item doesn't parse or
        refers to a column that isn't there, raise MappingError naming that item.

        Otherwise such an error only names generated SQL ("syntax error at or near AS", for an
        expr missing its closing bracket) and not the mapping row, and a run reports it only
        after computing every step. Planning is cheap: nothing is read or computed."""
        try:
            con.execute(f"DESCRIBE {self._final_select(prev, layer)}")
            return
        except (duckdb.ParserException, duckdb.BinderException) as whole:
            failure = whole
        own = bool(layer.mapping)
        mapping = layer.mapping if own else self.p.mapping
        layer_idx = self.p.outputs[0].layers.index(layer) if own and layer in self.p.outputs[0].layers else 0
        for j, item in enumerate(mapping):
            try:
                con.execute(f"DESCRIBE {self._final_select(prev, layer.model_copy(update={'mapping': [item]}))}")
                continue
            except (duckdb.ParserException, duckdb.BinderException) as e:
                error = e
            if item.expr is not None and isinstance(error, duckdb.ParserException):
                # Parsed on its own (bracketed, as _map_expr does), so the message points
                # into the expression rather than at the generated `AS "<to>"` after it.
                try:
                    con.extract_statements(f"SELECT ({item.expr})")
                except duckdb.ParserException as own_error:
                    error = own_error
            where = f"layers.{layer_idx}.mapping.{j}" if own else f"mapping.{j}"
            raise MappingError(f"pipelines.{self.pipeline_index}.{where}", item, error)
        raise failure

    def _final_select(self, prev: str, layer: OutputLayer, raw: bool = False) -> str:
        """The SELECT written for `layer`. `raw` skips the mapping (every column as-is), for
        a rejects layer: its rows leave the chain mid-way, before columns the mapping may
        use exist."""
        has_geom = self.p.base_source.has_geometry
        mapping = [] if raw else (layer.mapping or self.p.mapping)
        geom_funcs = {
            "lon", "lat", "x", "y", "mgrs", "geohash", "wkb", "wkt", "geom_type", "area", "length",
        }
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
        # geom (working_crs, for area/length/x/y/geom_type), __geom4326 (full geometry,
        # for wkb/wkt) and __centroid4326 (point, for lon/lat/mgrs/geohash on lines and
        # polygons too) are all available here; geometry written as 'geom'.
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
        if self.working_crs.upper() not in src_readers.GEOGRAPHIC_CRS:
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
        base_limit: int | None = None,
    ) -> str:
        """Build the source → step_0 → step_N view chain; returns the final view name.

        When `bbox` is given the base view is pre-filtered to it before any steps run,
        not just at the very end. Steps (spatial joins especially) otherwise run over the
        entire base table only to have everything but the visible viewport thrown away
        afterwards — for a big base table with real join steps that's the difference
        between an instant "in view" preview and a very slow one. Callers that filter by
        bbox must still re-apply it to the final SELECT, since a geometry-mutating step
        (buffer, dissolve, ...) can move a feature relative to it.

        `base_limit` keeps only the first N base features (counts() on a sample). With no
        `con` (plan()), nothing is read or executed.
        """
        self.plan, self.step_views, self.reject_views = [], {}, {}
        if con is not None:
            self._check_working_crs_units()
        self._create_source_views(con, workdir, bbox=bbox, max_features=max_features)
        self._create_derived_source_views(con)
        where = self._bbox_filter(bbox, self.working_crs) if self.p.base_source.has_geometry else ""
        limit = f"LIMIT {int(base_limit)}" if base_limit is not None else ""
        self._emit(
            con, "step_0", f"SELECT * FROM {_ident('src_' + self.p.base)} {where}{limit}",
            "base", id=self.p.base, title=f"base: '{self.p.base}'",
        )
        return self._build_step_views(con, "step_0", limit_steps=limit_steps)

    def _layer_writes(self, prev: str, out_index: int) -> list[tuple[OutputLayer, str, str]]:
        """(layer, SELECT, kind) for each layer written to output `out_index`. The steps'
        rejects layers go into the first output, in the first layer's CRS."""
        out = self.p.outputs[out_index]
        writes = [(layer, self._final_select(prev, layer), "layer") for layer in out.layers]
        if out_index == 0:
            crs = out.layers[0].crs
            for i, view in sorted(self.reject_views.items()):
                layer = OutputLayer(layer=self.p.steps[i - 1].rejects, crs=crs)
                writes.append((layer, self._final_select(view, layer, raw=True), "rejects"))
        return writes

    def run(self) -> str:
        with DUCKDB_LOCK:
            con = duckdb.connect()
            init_duckdb(con)

            with tempfile.TemporaryDirectory() as tmp:
                workdir = Path(tmp)
                # Steps as TEMP TABLEs (see _emit): a run computes all of them anyway, and as
                # views every layer's COPY (and every rejects layer's) would compute the whole
                # chain again. What doesn't fit in memory spills to the run's temp folder.
                con.execute(f"SET temp_directory = {_lit(str(workdir / 'spill'))}")
                self._materialize = True
                try:
                    prev = self._prepare(con, workdir)
                finally:
                    self._materialize = False
                # Before writing anything, so a mapping error names its row (MappingError)
                # instead of failing halfway through the output.
                for out in self.p.outputs:
                    for layer in out.layers:
                        self._check_mapping(con, prev, layer)

                deleted_paths: set[Path] = set()
                out_paths: list[str] = []
                self.written = []
                for out_index, out in enumerate(self.p.outputs):
                    out_path = Path(out.path)
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    writes = self._layer_writes(prev, out_index)
                    if is_parquet_path(out_path):
                        multi = self._parquet_multi(out_path)
                        if out.overwrite and out_path not in deleted_paths:
                            _clear_parquet_output(out_path, multi)
                            deleted_paths.add(out_path)
                        for layer, final_sql, kind in writes:
                            target = parquet_layer_path(out_path, layer.layer, multi)
                            rows = self._write_parquet_layer(con, final_sql, layer, target)
                            self.written.append({"layer": layer.layer, "rows": rows, "kind": kind, "path": str(target)})
                        root = str(parquet_output_root(out_path, multi))
                        if root not in out_paths:
                            out_paths.append(root)
                        continue
                    if out.overwrite and out_path not in deleted_paths:
                        if out_path.exists():
                            out_path.unlink()
                        deleted_paths.add(out_path)
                    for i, (layer, final_sql, kind) in enumerate(writes):
                        # The GDAL writer recreates out_path on every call, so
                        # only the very first layer written to a not-yet-existing
                        # file can go straight there; every layer after that
                        # (in this pipeline, or an earlier pipeline sharing the
                        # same output path) is written to its own temp file and
                        # spliced into out_path at the SQLite level.
                        write_direct = not out_path.exists()
                        target = out_path if write_direct else (workdir / f"__layer_{i}.gpkg")
                        self.log(f"writing {out_path} layer '{layer.layer}' ({layer.crs})")
                        final_sql = _gpkg_safe_select(con, final_sql, layer.layer, self.log)
                        copy_sql = (
                            f"COPY ({final_sql}) TO {_lit(str(target))} "
                            f"(FORMAT GDAL, DRIVER 'GPKG', LAYER_NAME {_lit(layer.layer)}, "
                            f"SRS {_lit(layer.crs)})"
                        )
                        (rows,) = con.execute(copy_sql).fetchone()
                        self.written.append({"layer": layer.layer, "rows": rows, "kind": kind, "path": str(out_path)})
                        if not write_direct:
                            _merge_gpkg_layer(out_path, target)
                    if str(out_path) not in out_paths:
                        out_paths.append(str(out_path))
                return out_paths

    def _parquet_multi(self, out_path: Path) -> bool:
        if self.parquet_multi is not None:
            return self.parquet_multi
        n = sum(len(o.layers) for o in self.p.outputs if Path(o.path) == out_path)
        if Path(self.p.outputs[0].path) == out_path:
            n += len(reject_layer_names(self.p.steps))
        return n > 1

    def _write_parquet_layer(self, con, final_sql: str, layer: OutputLayer, target: Path) -> int:
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
        (rows,) = con.execute(
            f"COPY ({sql}) TO {_lit(str(target))} (FORMAT PARQUET, COMPRESSION ZSTD)"
        ).fetchone()
        return rows

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
        rejects: bool = False,
    ) -> list[dict]:
        """Up to `limit` rows of the output's first layer, or of the step
        `preview_until_step` (0: the base source), or with `rejects` of that step's rejects."""
        with DUCKDB_LOCK:
            con = duckdb.connect()
            init_duckdb(con)

            with tempfile.TemporaryDirectory() as tmp:
                workdir = Path(tmp)
                prev = self._prepare(
                    con, workdir, bbox=bbox, max_features=limit,
                    limit_steps=preview_until_step,
                )
                if rejects:
                    if preview_until_step not in self.reject_views:
                        raise ValueError(f"step {preview_until_step} has no rejects layer")
                    prev = self.reject_views[preview_until_step]

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
                    self._check_mapping(con, prev, layer)
                    final_sql = self._final_select(prev, layer)
                    preview_sql = self._preview_rows_sql(
                        f"({final_sql})", layer.crs,
                        self.p.base_source.has_geometry, bbox, limit,
                    )

                res = con.execute(preview_sql)
                cols = [desc[0] for desc in res.description]
                rows = res.fetchall()
                return [dict(zip(cols, r)) for r in rows]

    def sql_plan(self) -> list[dict]:
        """Every view the pipeline builds, in order, with its SQL, plus the SELECT written
        for each output layer, without reading any data (see _plan_read_expr)."""
        prev = self._prepare(None, Path(tempfile.gettempdir()))
        plan = list(self.plan)
        for layer in self.p.outputs[0].layers:
            plan.append({
                "kind": "layer", "layer": layer.layer,
                "title": f"layer '{layer.layer}' ({layer.crs})",
                "view": None, "sql": textwrap.dedent(self._final_select(prev, layer)).strip(),
            })
        return plan

    def counts(
        self,
        limit: int | None = None,
        bbox: tuple[float, float, float, float] | None = None,
    ) -> dict:
        """Row counts after every step, like the feature counts FME shows on its connections.

        With `limit`, the chain runs on the first `limit` base features only (a sample, quick
        to count); without it, on all the data, which costs about as much as a run. Each step
        is materialized once (see _emit), so counting N steps doesn't compute the chain N
        times. Source counts are only part of the full count: on a sample they'd still mean
        reading every source in full.
        """
        self._materialize = True
        with DUCKDB_LOCK:
            con = duckdb.connect()
            try:
                init_duckdb(con)
                with tempfile.TemporaryDirectory() as tmp:
                    prev = self._prepare(
                        con, Path(tmp), bbox=bbox, max_features=limit, base_limit=limit,
                    )

                    def count(rel: str) -> int:
                        return con.execute(f"SELECT count(*) FROM {rel}").fetchone()[0]

                    result = {
                        "limit": limit,
                        "base": count(_ident("step_0")),
                        "steps": {i: count(_ident(v)) for i, v in self.step_views.items()},
                        "rejects": {i: count(_ident(v)) for i, v in self.reject_views.items()},
                        "layers": [
                            count(f"({self._final_select(prev, layer)})")
                            for layer in self.p.outputs[0].layers
                        ],
                        "sources": {},
                    }
                    if limit is None:
                        ids = [s.id for s in self.p.sources] + [ds.id for ds in self.p.derived_sources]
                        result["sources"] = {sid: count(_ident(f"src_{sid}")) for sid in ids}
                    return result
            finally:
                con.close()
                self._materialize = False


def run_pipeline(pipeline: Pipeline, log: Callable[[str], None] | None = None) -> list[str]:
    engine = Engine(pipeline, log=log)
    return engine.run()


def preview_pipeline(
    pipeline: Pipeline,
    limit: int = 50,
    preview_until_step: int | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    rejects: bool = False,
    log: Callable[[str], None] | None = None,
    pipeline_index: int = 0,
) -> list[dict]:
    engine = Engine(pipeline, log=log, pipeline_index=pipeline_index)
    return engine.preview(
        limit=limit, preview_until_step=preview_until_step, bbox=bbox, rejects=rejects,
    )


def run_config(
    config: Config,
    log: Callable[[str], None] | None = None,
    written: list[dict] | None = None,
    downloads: dict[str, str] | None = None,
) -> str:
    """Run all pipelines in a Config, appending each one's layers to the shared output.

    Returns the GeoPackage path, or for GeoParquet the file (one layer in total) or the
    folder of per-layer files (several). When `written` is given, one entry per layer
    written is appended to it: {"pipeline", "layer", "rows", "kind", "path"}, `kind` being
    "layer" or "rejects".

    `downloads` ({uri: path}) are remote files already downloaded for this run, used
    rather than fetched again: the editor downloads a run's files itself, then hands the
    run to its engine worker (see worker.py and sources.download_remote_sources).
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
    # A run reads current data: remote file sources are downloaded again (once per URL)
    # rather than reusing a preview's download — see sources.fresh_downloads.
    with src_readers.fresh_downloads(reuse=downloads):
        for i, pdef in enumerate(config.pipelines):
            engine = Engine(
                pdef.to_pipeline(config.output), log=log, parquet_multi=multi, pipeline_index=i,
            )
            engine.run()
            if written is not None:
                written.extend({"pipeline": pdef.name, **w} for w in engine.written)
    if multi is not None:
        return str(parquet_output_root(out_path, multi))
    return str(out_path)


def _config_pipeline(config: Config, pipeline_idx: int) -> Pipeline:
    if not 0 <= pipeline_idx < len(config.pipelines):
        raise ValueError(f"no pipeline at index {pipeline_idx}")
    return config.pipelines[pipeline_idx].to_pipeline(config.output)


def preview_config_pipeline(
    config: Config,
    pipeline_idx: int = 0,
    limit: int = 50,
    preview_until_step: int | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    rejects: bool = False,
    log: Callable[[str], None] | None = None,
) -> list[dict]:
    """Preview one pipeline from a Config (defaults to the first)."""
    return preview_pipeline(
        _config_pipeline(config, pipeline_idx),
        limit=limit, preview_until_step=preview_until_step, bbox=bbox, rejects=rejects, log=log,
        pipeline_index=pipeline_idx,
    )


def plan_config_pipeline(config: Config, pipeline_idx: int = 0) -> list[dict]:
    """The SQL of one pipeline's views (see Engine.sql_plan)."""
    return Engine(_config_pipeline(config, pipeline_idx)).sql_plan()


def count_config_pipeline(
    config: Config,
    pipeline_idx: int = 0,
    limit: int | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    log: Callable[[str], None] | None = None,
) -> dict:
    """Row counts after each step of one pipeline (see Engine.counts)."""
    return Engine(_config_pipeline(config, pipeline_idx), log=log).counts(limit=limit, bbox=bbox)

