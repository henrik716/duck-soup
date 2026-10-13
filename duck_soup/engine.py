"""The DuckDB execution engine.

Strategy
--------
Read every source once, reproject geometry to a single working CRS, then build a
chain of SQL views: base -> step1 -> step2 -> ... -> mapped. DuckDB plans the
whole chain as one query, so joins and projections stay fast and stream from
GDAL where possible. The final view is written straight to GeoPackage via
COPY ... (FORMAT GDAL, DRIVER 'GPKG'), or, for a .parquet/.geoparquet output path,
to GeoParquet via DuckDB's native COPY ... (FORMAT PARQUET) (see output.write_parquet_layer).

Spatial joins, nearest-neighbor joins and clips keep a single best match per base
feature (see steps._best_match_select), so each base feature stays a single row
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

import tempfile
import textwrap
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

import duckdb

from . import sources as src_readers
from . import mapping, steps
from .config import (
    Attributes, Buffer, CombineLines, Config, Dissolve, NearestNeighbor, OutputLayer, PipelineDef,
    Snapshot, NUMERIC_AGGREGATE_HOWS, is_parquet_path, reject_layer_names,
)
from .derive import DUCKDB_LOCK, init_duckdb
from .output import (
    clear_parquet_output,
    parquet_layer_path,
    parquet_output_root,
    write_gpkg_layer,
    write_parquet_layer,
)
from .sql_util import quote_ident as _ident, quote_literal as _lit, st_transform

class MappingError(ValueError):
    """A mapping item, or a column an attributes/dissolve/combine_lines step sets, that doesn't parse or
    bind (see Engine._check_mapping and Engine._check_step).

    The message starts with a line naming the item, for the editor's status, followed by
    the item's config path and the error on the next line: the shape of a validation error,
    which the editor's Problems tab turns into a link to the mapping row or step card.
    `item` is a MapItem or ExprColumn, or a bare column name (a dissolve or combine_lines key); `label` says
    what it is ("mapping", "step 3 column", ...)."""

    def __init__(self, path: str, item, error: Exception, label: str = "mapping"):
        detail = str(error).splitlines()[0].strip()
        if isinstance(item, str):
            name, kind = item, "column"
        elif getattr(item, "how", None) is not None:  # an aggregate without SQL
            name, kind = item.to, item.how
        else:
            name = item.to
            kind = next(
                k.rstrip("_") for k in ("expr", "from_", "func", "codelist", "const")
                if getattr(item, k, None) is not None
            )
        head = f"{label} '{name}' ({kind}): {detail}"
        super().__init__("\n".join([head, path, f"  {detail}"]))
        self.path = path


_NUMERIC_TYPES = (
    "TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT",
    "UINTEGER", "UBIGINT", "UHUGEINT", "FLOAT", "DOUBLE", "DECIMAL",
)


def _is_numeric(typ: str) -> bool:
    return typ.upper().startswith(_NUMERIC_TYPES)


def _type_in_words(typ: str) -> str:
    """A DuckDB column type as an error message can say it: "text", "a date", ..."""
    t = typ.upper()
    if t == "VARCHAR":
        return "text"
    if t == "BOOLEAN":
        return "true/false"
    if t == "DATE":
        return "a date"
    if t.startswith("TIMESTAMP"):
        return "a date and time"
    if t.startswith("GEOMETRY"):
        return "a geometry"
    return f"of type {typ}"


def _parse_error_in_expr(con: duckdb.DuckDBPyConnection, item, error: Exception) -> Exception:
    """For a parser error in an expression item, the error of parsing that expression on its
    own (bracketed, as it's compiled), so the message points into the expression rather than
    at the generated `AS "<to>"` after it."""
    expr = getattr(item, "expr", None)
    if expr is not None and isinstance(error, duckdb.ParserException):
        try:
            con.extract_statements(f"SELECT ({expr})")
        except duckdb.ParserException as own_error:
            return own_error
    return error


class Engine:
    def __init__(
        self,
        pipeline: PipelineDef,
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
        # Set by _prepare for the chain it builds: steps as tables, each computed once, for a
        # run and counts() (see _emit).
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
            schema, table = src_readers.postgres._split_pg_layer(s.layer)
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
                geom = st_transform("geom", crs, self.working_crs)
                if s.force_2d:
                    geom = f"ST_Force2D({geom})"
                if s.make_valid:
                    geom = f"ST_MakeValid({geom})"
                select = (
                    f"SELECT * EXCLUDE (geom), {geom} AS geom, "
                    f"row_number() OVER () AS __src_row FROM {read}"
                )
            else:
                # Numbered too, so "first match" in an attribute_join means the first row.
                select = f"SELECT *, row_number() OVER () AS __src_row FROM {read}"
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
        drop = ", ".join(_ident(c) for c in ["__matched", *steps.pulled_columns(step)])
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
            build = steps.STEP_SQL.get(type(step))
            if build is None:  # pragma: no cover
                raise ValueError(f"unknown step type: {step}")
            sql = build(cur, step, lambda m, i=i: self.log(f"step {i}: {m}"), self._step_context())
            if con is not None and getattr(step, "match", None) == "summarize":
                self._check_aggregate_columns(
                    con, i, [(f"fields.{out}", agg) for out, agg in step.fields.items()],
                    f"src_{step.source}", "field", f"in '{step.source}'",
                )
            if con is not None and isinstance(step, (Attributes, Dissolve, CombineLines)):
                self._check_step(con, i, step, cur)
            new_view = f"step_{i}"
            self._emit_step(con, step, i, sql, new_view)
            chains[target] = new_view
            self.step_views[i] = new_view
            self._sync_branch_view(con, chains, target, i)
            last_branch = target

        return chains[last_branch if limit_steps is not None else self._MAIN_BRANCH]

    def _step_context(self) -> steps.StepContext:
        return steps.StepContext(
            working_crs=self.working_crs,
            has_geom=self.p.base_source.has_geometry,
            base=self.p.base,
        )

    def _check_step(self, con: duckdb.DuckDBPyConnection, i: int, step, prev: str) -> None:
        """Plan (not run) step `i`, an attributes, dissolve or combine_lines step, over the view `prev`; if
        one of the columns it sets doesn't parse or bind, raise MappingError naming it, with
        its `pipelines.<p>.steps.<i-1>.<field>.<j>` path. Like _check_mapping, but the
        attribute columns are tried as growing prefixes, since each may use the ones before
        it."""
        build = steps.STEP_SQL[type(step)]
        ctx = self._step_context()

        def plan_error(variant) -> Exception | None:
            try:
                con.execute(f"DESCRIBE {build(prev, variant, lambda m: None, ctx)}")
            except (duckdb.ParserException, duckdb.BinderException) as e:
                return e
            return None

        if not isinstance(step, Attributes):
            self._check_aggregate_columns(
                con, i, [(f"aggregates.{j}", agg) for j, agg in enumerate(step.aggregates)], prev,
            )
        failure = plan_error(step)
        if failure is None:
            return
        if isinstance(step, Attributes):
            candidates = [
                ("columns", j, item, "column", step.model_copy(update={"columns": step.columns[:j + 1], "drop": []}))
                for j, item in enumerate(step.columns)
            ]
        else:
            candidates = [
                ("by", j, key, "group key", step.model_copy(update={"by": step.by[:j + 1], "aggregates": []}))
                for j, key in enumerate(step.by)
            ] + [
                ("aggregates", j, agg, "aggregate", step.model_copy(update={"aggregates": [agg]}))
                for j, agg in enumerate(step.aggregates)
            ]
        for field, j, item, what, variant in candidates:
            error = plan_error(variant)
            if error is None:
                continue
            raise MappingError(
                f"pipelines.{self.pipeline_index}.steps.{i - 1}.{field}.{j}", item,
                _parse_error_in_expr(con, item, error), label=f"step {i} {what}",
            )
        raise failure

    def _check_aggregate_columns(
        self, con: duckdb.DuckDBPyConnection, i: int, aggregates: list, view: str,
        what: str = "aggregate", where: str = "at this step",
    ) -> None:
        """Raise MappingError for an aggregate whose `how` can't work on its `from` column (a
        missing column, a sum of text, ...), in words rather than DuckDB's binder error.
        `aggregates` are (path within the step, Aggregate) pairs over the rows of `view`."""
        types = {
            name.lower(): (name, typ)
            for name, typ, *_ in con.execute(f"DESCRIBE SELECT * FROM {_ident(view)}").fetchall()
        }
        for field, agg in aggregates:
            if agg.how is None:
                continue
            problem = None
            if agg.how == "from_largest" and "geom" not in types:
                problem = "from the largest needs geometry, and these rows have none"
            elif agg.from_ is not None and agg.from_.lower() not in types:
                problem = f"there's no column '{agg.from_}' {where}"
            elif agg.from_ is not None and agg.how in NUMERIC_AGGREGATE_HOWS:
                name, typ = types[agg.from_.lower()]
                if not _is_numeric(typ):
                    problem = (
                        f"{name} is {_type_in_words(typ)}, and {agg.how} only works on numbers. "
                        "Use one value, most common or all of them instead"
                    )
            if problem:
                raise MappingError(
                    f"pipelines.{self.pipeline_index}.steps.{i - 1}.{field}", agg,
                    ValueError(problem), label=f"step {i} {what}",
                )

    # -- mapping ------------------------------------------------------------
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
        items = layer.mapping if own else self.p.mapping
        layer_idx = self.p.layers.index(layer) if own and layer in self.p.layers else 0
        for j, item in enumerate(items):
            try:
                con.execute(f"DESCRIBE {self._final_select(prev, layer.model_copy(update={'mapping': [item]}))}")
                continue
            except (duckdb.ParserException, duckdb.BinderException) as e:
                error = e
            error = _parse_error_in_expr(con, item, error)
            where = f"layers.{layer_idx}.mapping.{j}" if own else f"mapping.{j}"
            raise MappingError(f"pipelines.{self.pipeline_index}.{where}", item, error)
        raise failure

    def _final_select(self, prev: str, layer: OutputLayer, raw: bool = False) -> str:
        """The SELECT written for `layer` (see mapping.final_select). `raw` skips the mapping
        (every column as-is), for a rejects layer: its rows leave the chain mid-way, before
        columns the mapping may use exist."""
        return mapping.final_select(
            prev, layer, [] if raw else (layer.mapping or self.p.mapping),
            working_crs=self.working_crs, has_geom=self.p.base_source.has_geometry,
            base=self.p.base,
        )

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
        mappings = [self.p.mapping] + [layer.mapping for layer in self.p.layers if layer.mapping]
        mappings += [step.columns for step in self.p.steps if isinstance(step, Attributes)]
        funcs = sorted({m.func for items in mappings for m in items if m.func in mapping.MEASURE_FUNCS})
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
    @contextmanager
    def _session(self) -> Iterator[tuple[duckdb.DuckDBPyConnection, Path]]:
        """A connection set up for the engine (derive.init_duckdb) and a temp folder for
        this call's downloads and spills, both gone afterwards. One call at a time."""
        with DUCKDB_LOCK, tempfile.TemporaryDirectory() as tmp:
            con = duckdb.connect()
            try:
                init_duckdb(con)
                yield con, Path(tmp)
            finally:
                con.close()

    def _prepare(
        self,
        con,
        workdir: Path,
        bbox: tuple[float, float, float, float] | None = None,
        max_features: int | None = None,
        limit_steps: int | None = None,
        base_limit: int | None = None,
        materialize: bool = False,
    ) -> str:
        """Build the source → step_0 → step_N view chain; returns the final view name.

        When `bbox` is given the base view is pre-filtered to it before any steps run,
        not just at the very end. Steps (spatial joins especially) otherwise run over the
        entire base table only to have everything but the visible viewport thrown away
        afterwards — for a big base table with real join steps that's the difference
        between an instant "in view" preview and a very slow one. Callers that filter by
        bbox must still re-apply it to the final SELECT, since a geometry-mutating step
        (buffer, dissolve, ...) can move a feature relative to it.

        `base_limit` keeps only the first N base features (counts() on a sample).
        `materialize` makes the steps TEMP TABLEs (see _emit). With no `con` (plan()),
        nothing is read or executed.
        """
        self.plan, self.step_views, self.reject_views = [], {}, {}
        self._materialize = materialize
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

    def _layer_writes(self, prev: str) -> list[tuple[OutputLayer, str, str]]:
        """(layer, SELECT, kind) for each layer written: the pipeline's layers, then the
        steps' rejects layers, in the first layer's CRS."""
        writes = [(layer, self._final_select(prev, layer), "layer") for layer in self.p.layers]
        crs = self.p.layers[0].crs
        for i, view in sorted(self.reject_views.items()):
            layer = OutputLayer(layer=self.p.steps[i - 1].rejects, crs=crs)
            writes.append((layer, self._final_select(view, layer, raw=True), "rejects"))
        return writes

    def run(self, out_path: str) -> str:
        """Write the pipeline's layers to `out_path`, alongside whatever layers it already
        holds (run_config clears it first when the config says `overwrite`). Returns the
        GeoPackage, or for GeoParquet the file or folder written (see parquet_output_root)."""
        out_path = Path(out_path)
        with self._session() as (con, workdir):
            # Steps as TEMP TABLEs (see _emit): a run computes all of them anyway, and as
            # views every layer's COPY (and every rejects layer's) would compute the whole
            # chain again. What doesn't fit in memory spills to the run's temp folder.
            con.execute(f"SET temp_directory = {_lit(str(workdir / 'spill'))}")
            prev = self._prepare(con, workdir, materialize=True)
            # Before writing anything, so a mapping error names its row (MappingError)
            # instead of failing halfway through the output.
            for layer in self.p.layers:
                self._check_mapping(con, prev, layer)

            out_path.parent.mkdir(parents=True, exist_ok=True)
            writes = self._layer_writes(prev)
            self.written = []
            if is_parquet_path(out_path):
                multi = self._parquet_multi()
                for layer, final_sql, kind in writes:
                    target = parquet_layer_path(out_path, layer.layer, multi)
                    rows = write_parquet_layer(
                        con, final_sql, layer, target, self.p.base_source.has_geometry, self.log,
                    )
                    self.written.append({"layer": layer.layer, "rows": rows, "kind": kind, "path": str(target)})
                return str(parquet_output_root(out_path, multi))

            for i, (layer, final_sql, kind) in enumerate(writes):
                rows = write_gpkg_layer(
                    con, final_sql, layer, out_path, workdir / f"__layer_{i}.gpkg", self.log,
                )
                self.written.append({"layer": layer.layer, "rows": rows, "kind": kind, "path": str(out_path)})
            return str(out_path)

    def _parquet_multi(self) -> bool:
        """Whether a GeoParquet output gets one file per layer: run_config's say (it counts
        every pipeline's layers) or, left to this pipeline alone, more than one layer."""
        if self.parquet_multi is not None:
            return self.parquet_multi
        return len(self.p.layers) + len(reject_layer_names(self.p.steps)) > 1

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
        sample: int | None = None,
    ) -> list[dict]:
        """Up to `limit` rows of the output's first layer, or of the step
        `preview_until_step` (0: the base source), or with `rejects` of that step's rejects.

        With `sample`, the chain runs on the first `sample` base features only. Without it a
        step that needs all its rows before it gives any (a dissolve, a summarize) computes
        over all the data, however small `limit` is: the editor's live checks pass one."""
        with self._session() as (con, workdir):
            prev = self._prepare(
                con, workdir, bbox=bbox, max_features=sample or limit, limit_steps=preview_until_step,
                base_limit=sample,
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
                layer = self.p.layers[0]
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
        for layer in self.p.layers:
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
        with self._session() as (con, workdir):
            prev = self._prepare(
                con, workdir, bbox=bbox, max_features=limit, base_limit=limit, materialize=True,
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
                    for layer in self.p.layers
                ],
                "sources": {},
            }
            if limit is None:
                ids = [s.id for s in self.p.sources] + [ds.id for ds in self.p.derived_sources]
                result["sources"] = {sid: count(_ident(f"src_{sid}")) for sid in ids}
            return result


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
            clear_parquet_output(out_path, multi)
    elif config.overwrite and out_path.exists():
        out_path.unlink()
    # A run reads current data: remote file sources are downloaded again (once per URL)
    # rather than reusing a preview's download — see sources.fresh_downloads.
    with src_readers.fresh_downloads(reuse=downloads):
        for i, pdef in enumerate(config.pipelines):
            engine = Engine(pdef, log=log, parquet_multi=multi, pipeline_index=i)
            engine.run(config.output)
            if written is not None:
                written.extend({"pipeline": pdef.name, **w} for w in engine.written)
    if multi is not None:
        return str(parquet_output_root(out_path, multi))
    return str(out_path)


def _config_pipeline(config: Config, pipeline_idx: int) -> PipelineDef:
    if not 0 <= pipeline_idx < len(config.pipelines):
        raise ValueError(f"no pipeline at index {pipeline_idx}")
    return config.pipelines[pipeline_idx]


def preview_config_pipeline(
    config: Config,
    pipeline_idx: int = 0,
    limit: int = 50,
    preview_until_step: int | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    rejects: bool = False,
    log: Callable[[str], None] | None = None,
    sample: int | None = None,
) -> list[dict]:
    """Preview one pipeline from a Config (defaults to the first)."""
    engine = Engine(_config_pipeline(config, pipeline_idx), log=log, pipeline_index=pipeline_idx)
    return engine.preview(
        limit=limit, preview_until_step=preview_until_step, bbox=bbox, rejects=rejects,
        sample=sample,
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

