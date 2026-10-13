"""The SQL of each pipeline step.

Each `<type>_sql(prev, step, log, ctx)` returns the SELECT computing the step from the view
`prev`; Engine._build_step_views creates it as the view `step_<i>`. Source `x` is read
from its view `src_x`. With `step.rejects` set, the SELECT carries a boolean `__matched`
column that Engine._emit_step splits on (see the engine module docstring).

A builder must read `prev` only once (pull it into a MATERIALIZED CTE to use it twice):
views are expanded where they're used, so a second read doubles the work of the whole
chain before it (tests/test_step_reads.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import mapping
from .config import (
    Aggregate, AttributeJoin, Attributes, Buffer, Centroid, Clip, CombineLines, Dissolve, Erase,
    ExprColumn, Filter, IntersectOverlay, LineOverlay, Merge, NearestNeighbor, SpatialJoin,
)
from .sql_util import quote_ident as _ident, quote_literal as _lit

Log = Callable[[str], None]


@dataclass(frozen=True)
class StepContext:
    """What a builder may need to know about the pipeline besides its own step."""

    working_crs: str
    has_geom: bool  # whether the base source has geometry
    base: str  # the base source's id, for error messages

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


def _src(sid: str) -> str:
    """The view a step reads source (or derived source, or snapshot) `sid` from."""
    return _ident(f"src_{sid}")


def spatial_join_sql(prev: str, step: SpatialJoin, log: Log, _ctx: StepContext) -> str:
    pred = _PREDICATE_SQL[step.predicate]
    src_view = _src(step.source)

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
        log(f"spatial_join {step.predicate} {step.source} (match=all)")
        return sql

    if step.match == "summarize":
        log(f"spatial_join {step.predicate} {step.source} (summarize {', '.join(step.fields)})")
        return _summarize_select(
            prev, src_view, f"{pred}(a.geom, b.geom)", step.fields, flag=bool(step.rejects)
        )
    fields = {out: f"b.{_ident(col)}" for out, col in step.fields.items()}
    # Deterministic match selection: __src_row is a stable row_number() assigned to
    # each join-source row when its view is created (see Engine._create_source_views), so
    # ranking by it makes "first match" reproducible instead of depending on
    # unspecified query-plan order. "largest_overlap" ranks by intersection area first
    # (negated, since the best match is the lowest rank), falling back to __src_row.
    if step.on_multiple == "largest_overlap":
        rank = ["-ST_Area(ST_Intersection(a.geom, b.geom))", "b.__src_row"]
    else:
        rank = ["b.__src_row"]
    select = _best_match_select(
        prev, src_view, f"{pred}(a.geom, b.geom)", rank, fields, flag=bool(step.rejects)
    )
    log(f"spatial_join {step.predicate} {step.source} (on_multiple={step.on_multiple})")
    return select

def nearest_neighbor_sql(prev: str, step: NearestNeighbor, log: Log, _ctx: StepContext) -> str:
    src_view = _src(step.source)
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
    select = _best_match_select(
        prev, src_view, on, [dist_expr, "b.__src_row"], fields, flag=bool(step.rejects)
    )
    suffix = f" (max_distance={step.max_distance})" if step.max_distance is not None else ""
    log(f"nearest_neighbor {step.source}{suffix}")
    return select

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

def _summarize_select(
    prev: str, src_view: str, on: str, fields: dict, flag: bool, left: str | None = None,
) -> str:
    """Every `prev` row (`a`), with each of `fields` ({output: Aggregate}) computed over its
    matches in `src_view` (`b`): the source rows satisfying `on`. A row with no matches
    gets NULLs, and 0 for a count. `left` is an expression computed on `a` first as
    `__left` (attribute_join's), for `on` to use.

    The aggregates see only the source's columns (their SQL names them bare, as written),
    so they run over a subquery of the matched source rows, numbered by base row. The plain
    JOIN lets DuckDB plan a spatial `on` as its SPATIAL_JOIN, as in _per_row_match_select.
    """
    aggs = ", ".join(_expr_column(agg) for agg in fields.values())
    outs = "".join(
        f", coalesce(m.{_ident(out)}, 0) AS {_ident(out)}" if agg.how == "count"
        else f", m.{_ident(out)}"
        for out, agg in fields.items()
    )
    matched = ", m.__a_row IS NOT NULL AS __matched" if flag else ""
    extra = f", ({left}) AS __left" if left is not None else ""
    drop = "__a_row, __left" if left is not None else "__a_row"
    return f"""
    WITH a AS MATERIALIZED (
        SELECT *{extra}, row_number() OVER () AS __a_row FROM {_ident(prev)}
    ),
    m AS (
        SELECT __a_row{(', ' + aggs) if aggs else ''}
        FROM (SELECT b.*, a.__a_row FROM a JOIN {src_view} b ON {on})
        GROUP BY __a_row
    )
    SELECT a.* EXCLUDE ({drop}){outs}{matched}
    FROM a LEFT JOIN m ON m.__a_row = a.__a_row
    ORDER BY a.__a_row
    """


def _best_match_select(
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
    return _per_row_match_select(
        prev, src_view, on, agg=agg,
        columns=f"a.* EXCLUDE (__a_row){pulled}{matched}",
    )

def attribute_join_sql(prev: str, step: AttributeJoin, log: Log, _ctx: StepContext) -> str:
    src_view = _src(step.source)
    if step.match == "summarize":
        log(f"attribute_join {step.source} on {step.right} (summarize {', '.join(step.fields)})")
        return _summarize_select(
            prev, src_view, f"b.{_ident(step.right)} = a.__left", step.fields,
            flag=bool(step.rejects), left=step.left,
        )
    pulled = ", ".join(f"j.{_ident(out)}" for out in step.fields)
    inner = [f"b.{_ident(col)} AS {_ident(out)}" for out, col in step.fields.items()]
    matched = ""
    if step.rejects:
        inner.append("1 AS __hit")
        matched = ", j.__hit IS NOT NULL AS __matched"
    # `left` is computed on the running row before the join: written inside the subquery, an
    # unqualified column in it would resolve to the join source's column of that name
    # whenever there is one, matching every row against itself.
    sql = f"""
    SELECT a.* EXCLUDE (__left){(', ' + pulled) if pulled else ''}{matched}
    FROM (SELECT *, ({step.left}) AS __left FROM {_ident(prev)}) a
    LEFT JOIN LATERAL (
        SELECT {', '.join(inner) if inner else '1'}
        FROM {src_view} b
        WHERE b.{_ident(step.right)} = a.__left
        ORDER BY b.__src_row
        LIMIT 1
    ) j ON true
    """
    log(f"attribute_join {step.source} on {step.right}")
    return sql

def buffer_sql(prev: str, step: Buffer, log: Log, _ctx: StepContext) -> str:
    sql = f"""
    SELECT * EXCLUDE (geom), ST_Buffer(geom, {step.distance}) AS geom
    FROM {_ident(prev)}
    """
    log(f"buffer {step.distance}")
    return sql

def centroid_sql(prev: str, _step: Centroid, log: Log, _ctx: StepContext) -> str:
    sql = f"""
    SELECT * EXCLUDE (geom), ST_Centroid(geom) AS geom
    FROM {_ident(prev)}
    """
    log("centroid")
    return sql

def clip_sql(prev: str, step: Clip, log: Log, _ctx: StepContext) -> str:
    pred = _PREDICATE_SQL[step.predicate]
    src_view = _src(step.source)
    on = f"{pred}(a.geom, b.geom)"
    # Clipped by the first match (in source row order); unmatched features, and ones
    # that only touch the mask, are dropped.
    clipped = _same_dimension("ST_Intersection(a.geom, m.__agg)", "a.geom")
    agg = "arg_min(b.geom, b.__src_row)"
    log(f"clip {step.predicate} {step.source}")
    if not step.rejects:
        select = _drop_empty(_per_row_match_select(
            prev, src_view, on, agg=agg,
            columns=f"a.* EXCLUDE (__a_row, geom), {clipped} AS geom",
            inner=True,
        ))
        return select
    # The same rows as above pass; every other feature is a reject and keeps its
    # unclipped geometry.
    per_row = _per_row_match_select(
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
    return select

def erase_sql(prev: str, step: Erase, log: Log, _ctx: StepContext) -> str:
    pred = _PREDICATE_SQL[step.predicate]
    src_view = _src(step.source)
    select = _erase_select(prev, src_view, f"{pred}(a.geom, b.geom)")
    log(f"erase {step.predicate} {step.source}")
    return select

def _erase_select(prev: str, src_view: str, on: str) -> str:
    """Each `prev` feature minus the union of everything it matches in `src_view`;
    unmatched ones pass through, and ones erased completely are dropped rather than
    kept as empty shapes."""
    return _drop_empty(_per_row_match_select(
        prev, src_view, on,
        agg="ST_Union_Agg(b.geom)",
        columns=(
            "a.* EXCLUDE (__a_row, geom), "
            "COALESCE(ST_Difference(a.geom, m.__agg), a.geom) AS geom"
        ),
    ))

# The SQL of each aggregate `how` (config.AGGREGATE_HOWS), with {col} for the quoted column.
# from_largest takes the value of the longest line or the largest area: a polygon's length
# and a line's area are 0, so the larger of the two measures whichever the feature is.
AGGREGATE_SQL = {
    "any": "any_value({col})",
    "most_common": "mode({col})",
    "from_largest": "arg_max({col}, greatest(ST_Area(geom), ST_Length(geom)))",
    "all": "string_agg(DISTINCT CAST({col} AS VARCHAR), ', ' ORDER BY CAST({col} AS VARCHAR))",
    "sum": "sum({col})",
    "average": "avg({col})",
    "min": "min({col})",
    "max": "max({col})",
    "count": "count({col})",
}


def aggregate_sql(agg: Aggregate) -> str:
    """The SQL of an aggregate: its `expr`, or what its `how` does to its `from` column."""
    if agg.expr is not None:
        return agg.expr
    return AGGREGATE_SQL[agg.how].format(col=_ident(agg.from_) if agg.from_ else "*")


def _expr_column(col: ExprColumn | Aggregate) -> str:
    """`col` as a SELECT item. Bracketed, like mapping.map_expr, so an expression with a
    top-level operator stays one expression."""
    sql = aggregate_sql(col) if isinstance(col, Aggregate) else col.expr
    return f"({sql}) AS {_ident(col.to)}"


def dissolve_sql(prev: str, step: Dissolve, log: Log, _ctx: StepContext) -> str:
    # Dissolved features are new rows, so they get a fresh __src_row: later steps and
    # the final SELECT expect every feature to carry one (match ranking when this branch
    # is a join source, and the default "all columns" output, which excludes it).
    keys = [_ident(k) if isinstance(k, str) else _expr_column(k) for k in step.by]
    aggs = "".join(f", {_expr_column(a)}" for a in step.aggregates)
    if keys:
        # Grouped by position, so an expression key is written once (with its name).
        positions = ", ".join(str(n) for n in range(1, len(keys) + 1))
        sql = f"""
        SELECT *, row_number() OVER () AS __src_row FROM (
            SELECT {', '.join(keys)}, ST_Union_Agg(geom) AS geom{aggs}
            FROM {_ident(prev)}
            GROUP BY {positions}
        )
        """
    else:
        sql = f"""
        SELECT ST_Union_Agg(geom) AS geom{aggs}, 1 AS __src_row
        FROM {_ident(prev)}
        """
    names = [k if isinstance(k, str) else f"{k.to} = {k.expr}" for k in step.by]
    suffix = f" with {', '.join(a.to for a in step.aggregates)}" if step.aggregates else ""
    log(f"dissolve by {names or '(all)'}{suffix}")
    return sql


def combine_lines_sql(prev: str, step: CombineLines, log: Log, ctx: StepContext) -> str:
    """Lines joined end to end, one feature per connected chain of lines that share the
    `by` values.

    The lines of a group are collected (not unioned: a union would also cut them where
    they cross without meeting, at a bridge) and merged at shared end points by
    ST_LineMerge, which stops where three or more lines meet. Each line part is then
    matched back to the chain that covers it, so the aggregates run over the features that
    make up each chain, not over the whole group. A feature whose parts end up in several
    chains counts once in each. Features that aren't lines are kept, one per feature.
    """
    if not ctx.has_geom:
        raise ValueError(f"combine_lines needs geometry, and the base source '{ctx.base}' has none")
    names = [k if isinstance(k, str) else k.to for k in step.by]
    keys = ", ".join(
        f"{_ident(k) if isinstance(k, str) else '(' + k.expr + ')'} AS __k{j}"
        for j, k in enumerate(step.by)
    )
    key_cols = [f"__k{j}" for j in range(len(step.by))]
    grp = f"dense_rank() OVER (ORDER BY {', '.join(key_cols)})" if key_cols else "1"
    aggs = "".join(f", {_expr_column(a)}" for a in step.aggregates)
    out_keys = "".join(f"c.__k{j} AS {_ident(n)}, " for j, n in enumerate(names))
    kc = "".join(f", {k}" for k in key_cols)
    carried = "".join(f", any_value({k}) AS {k}" for k in key_cols)
    direction = "true" if step.preserve_direction else "false"
    is_line = "ST_GeometryType(geom) IN ('LINESTRING', 'MULTILINESTRING')"
    sql = f"""
    WITH p AS MATERIALIZED (
        -- the running rows, read once, with their group keys and a row id
        SELECT *, {grp} AS __grp, row_number() OVER () AS __seg
        FROM (SELECT *{', ' + keys if keys else ''} FROM {_ident(prev)})
    ),
    part AS MATERIALIZED (
        -- one row per line part
        SELECT __seg, __grp{kc}, unnest(ST_Dump(geom)).geom AS geom FROM p WHERE {is_line}
    ),
    chain AS MATERIALIZED (
        -- each group's lines merged where they meet, one row per resulting chain
        SELECT * EXCLUDE (__part), __part.geom AS geom, row_number() OVER () AS __chain
        FROM (
            SELECT __grp{carried}, unnest(ST_Dump(ST_LineMerge(ST_Collect(list(geom)), {direction}))) AS __part
            FROM part GROUP BY __grp
        )
    ),
    member AS (
        -- each line part to the chain it lies on (the lowest-numbered, should two overlap)
        SELECT DISTINCT __seg, __chain FROM (
            SELECT part.__seg, min(c.__chain) AS __chain
            FROM (SELECT *, row_number() OVER () AS __part_row FROM part) part
            -- The group is compared as a difference, not an equality: DuckDB would hash join
            -- on an equality and compare every pair within a group, rather than plan its
            -- R-tree SPATIAL_JOIN on the predicate.
            JOIN chain c ON ST_Covers(c.geom, part.geom) AND c.__grp - part.__grp = 0
            GROUP BY part.__part_row, part.__seg
        )
        UNION ALL
        -- anything that isn't a line is a chain of its own
        SELECT __seg, -__seg FROM p WHERE geom IS NULL OR NOT {is_line}
    ),
    shape AS (
        SELECT __chain, geom{kc} FROM chain
        UNION ALL
        SELECT -__seg, geom{kc} FROM p WHERE geom IS NULL OR NOT {is_line}
    ),
    agg AS (
        SELECT __chain{aggs}
        FROM (SELECT p.*, m.__chain FROM p JOIN member m USING (__seg))
        GROUP BY __chain
    )
    SELECT {out_keys}c.geom{''.join(', a.' + _ident(a.to) for a in step.aggregates)},
           row_number() OVER (ORDER BY c.__chain < 0, abs(c.__chain)) AS __src_row
    FROM shape c LEFT JOIN agg a USING (__chain)
    ORDER BY __src_row
    """
    labels = [k if isinstance(k, str) else f"{k.to} = {k.expr}" for k in step.by]
    suffix = f" with {', '.join(a.to for a in step.aggregates)}" if step.aggregates else ""
    reversible = "" if step.preserve_direction else " (lines may be reversed)"
    log(f"combine_lines by {labels or '(all)'}{suffix}{reversible}")
    return sql


def attributes_sql(prev: str, step: Attributes, log: Log, ctx: StepContext) -> str:
    """Every column of `prev`, with step.columns set in order (each one added, or replacing
    the column of that name) and step.drop removed.

    One SELECT per column, each over the one before it, so a column can use the ones set
    before it. They're nested subqueries over one read of `prev`, which DuckDB folds into a
    single projection. The column being set is left out
    of the SELECT * with COLUMNS(lambda), not EXCLUDE, which errors when the column isn't
    there yet; a replaced column therefore moves to the end of the row. `__keep` rides along
    so that lambda never matches nothing (COLUMNS errors on an empty set), e.g. when the only
    column of a table without geometry is replaced.
    """
    mapping.check_geometry_funcs(step.columns, ctx.has_geom, ctx.base)
    helpers = ctx.has_geom and any(c.func in mapping.GEO4326_FUNCS for c in step.columns)
    sql = f"SELECT *, NULL AS __keep FROM {_ident(prev)}"
    if helpers:
        sql = mapping.with_geo_helpers(f"({sql})", ctx.working_crs)
    for col in step.columns:
        sql = f"""
        SELECT COLUMNS(lambda c: lower(c) <> {_lit(col.to.lower())}), {mapping.map_expr(col)}
        FROM ({sql})"""
    hidden = [c.lower() for c in step.drop] + ["__keep"]
    hidden += ["__geom4326", "__centroid4326"] if helpers else []
    listed = ", ".join(_lit(c) for c in hidden)
    sql = f"""
        SELECT COLUMNS(lambda c: lower(c) NOT IN ({listed}))
        FROM ({sql})"""
    parts = [f"set {', '.join(c.to for c in step.columns)}"] if step.columns else []
    parts += [f"drop {', '.join(step.drop)}"] if step.drop else []
    log(f"attributes: {'; '.join(parts) or '(nothing)'}")
    return sql

def intersect_overlay_sql(prev: str, step: IntersectOverlay, log: Log, _ctx: StepContext) -> str:
    src_view = _src(step.source)
    b_fields = ", ".join(
        f"b.{_ident(col)} AS {_ident(out)}" for out, col in step.fields.items()
    )
    # Only the overlapping area itself: pairs that merely touch would otherwise add
    # line/point slivers to a polygon layer (see _same_dimension).
    overlaid = _same_dimension("ST_Intersection(a.geom, b.geom)", "a.geom")
    pairs = f"""
    SELECT a.* EXCLUDE (geom){(', ' + b_fields) if b_fields else ''},
           {overlaid} AS geom
    FROM {_ident(prev)} a
    JOIN {src_view} b ON ST_Intersects(a.geom, b.geom)
    """
    log(f"intersect_overlay {step.source}")
    return _drop_empty(pairs)

# line_overlay without a `tolerance`: lines must lie on each other to within this many
# working-CRS units (a micrometre in a metric CRS), which is "exactly" for real data.
_LINE_OVERLAY_EXACT = 1e-6
# A stretch only counts as shared when the source line runs along it: its end points'
# closest points on the source line must be at least this fraction of the stretch's own
# length apart (the cosine of the angle between the lines, ~18°). Lines that only cross
# otherwise leave a stretch of 2 x tolerance around the crossing.
_LINE_OVERLAY_MIN_ALIGNMENT = 0.95

def line_overlay_sql(prev: str, step: LineOverlay, log: Log, _ctx: StepContext) -> str:
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
    tol = step.tolerance if step.tolerance is not None else _LINE_OVERLAY_EXACT
    src_view = _src(step.source)
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
              >= {_LINE_OVERLAY_MIN_ALIGNMENT} * ST_Distance(ST_StartPoint(__s), ST_EndPoint(__s))
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
    log(f"line_overlay {step.source}{suffix}")
    return select

def filter_sql(prev: str, step: Filter, log: Log, _ctx: StepContext) -> str:
    if step.rejects:
        sql = f"SELECT *, ({step.where}) IS TRUE AS __matched FROM {_ident(prev)}"
    else:
        sql = f"""
        SELECT * FROM {_ident(prev)}
        WHERE {step.where}
        """
    log(f"filter {step.where}")
    return sql

def merge_sql(prev: str, step: Merge, log: Log, _ctx: StepContext) -> str:
    src_view = _src(step.source)
    sql = f"""
    SELECT * FROM {_ident(prev)}
    UNION ALL BY NAME
    SELECT * FROM {src_view}
    """
    log(f"merge {step.source}")
    return sql


def pulled_columns(step) -> list[str]:
    """Columns a join step adds, which are always NULL on its rejects."""
    cols = list(getattr(step, "fields", {}) or {})
    if isinstance(step, NearestNeighbor) and step.distance_field:
        cols.append(step.distance_field)
    return cols


# The SELECT of each step type (a snapshot only names a view, see
# Engine._build_step_views).
STEP_SQL: dict[type, Callable[..., str]] = {
    SpatialJoin: spatial_join_sql,
    AttributeJoin: attribute_join_sql,
    NearestNeighbor: nearest_neighbor_sql,
    Buffer: buffer_sql,
    Centroid: centroid_sql,
    Clip: clip_sql,
    Erase: erase_sql,
    Dissolve: dissolve_sql,
    CombineLines: combine_lines_sql,
    IntersectOverlay: intersect_overlay_sql,
    LineOverlay: line_overlay_sql,
    Filter: filter_sql,
    Merge: merge_sql,
    Attributes: attributes_sql,
}
