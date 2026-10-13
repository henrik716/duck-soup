"""Pipeline configuration: the YAML schema, validated with pydantic.

A config is one output file and one or more pipelines, each writing its own layers to it.
A pipeline is: a set of sources, one base source whose features flow through, an ordered
list of steps, an ordered attribute mapping, and the output layers it writes.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal, Optional, Union, get_args

import yaml
from pydantic import (
    AfterValidator, BaseModel, Field, field_serializer, model_serializer, model_validator,
)

# Formats we know how to read. Most go through DuckDB's ST_Read (GDAL); see the
# duck_soup.sources docstring for the ones that don't.
SOURCE_FORMATS = [
    "gpkg",       # GeoPackage
    "oapif",      # OGC API - Features endpoint
    "geojson",    # GeoJSON file or URL
    "wfs",        # OGC WFS endpoint
    "gml",        # GML / INSPIRE
    "flatgeobuf", # FlatGeobuf
    "parquet",    # (Geo)Parquet
    "postgres",   # PostgreSQL/PostGIS table, read via DuckDB's postgres extension
    "csv",        # CSV (tabular; geometry from x_field/y_field/geom_field)
    "xlsx",       # Excel sheet (tabular; geometry from x_field/y_field/geom_field)
    "json",       # plain JSON records via DuckDB's read_json (tabular, as csv)
    "fgdb",       # Esri File Geodatabase (.gdb folder)
    "shp",        # Shapefile
    "arcgis_rest",  # ArcGIS REST FeatureServer/MapServer query endpoint
]

# Formats that carry geometry by default.
SPATIAL_FORMATS = {
    "gpkg", "geojson", "gml", "fgdb", "wfs", "arcgis_rest", "oapif", "parquet", "flatgeobuf", "shp",
    "postgres",
}

# Tabular formats whose geometry can be built from columns (x_field/y_field/geom_field).
TABULAR_GEOM_FORMATS = ("xlsx", "csv", "json")

# Formats that take `header_row`.
HEADER_ROW_FORMATS = ("xlsx", "csv")

JOIN_PREDICATES = ["intersects", "contains", "within"]

# Mapping value functions computed by the engine.
MAP_FUNCS = [
    "uuid", "seq", "now", "today", "lat", "lon", "x", "y", "mgrs", "geohash",
    "wkb", "wkt", "geom_type", "area", "length",
]

# The ways a dissolve/combine_lines aggregate can combine a column without SQL (`how`); the
# SQL of each is steps.AGGREGATE_SQL, which must list the same names.
AGGREGATE_HOWS = [
    "any", "most_common", "from_largest", "all", "sum", "average", "min", "max", "count",
]
# The ones that only work on numbers.
NUMERIC_AGGREGATE_HOWS = ["sum", "average"]

CASE_MATCH_TYPES = ["match", "like", "regex"]

_CRS_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*:[0-9]+$")


def _check_crs_format(v: Optional[str]) -> Optional[str]:
    if v is not None and not _CRS_RE.match(v):
        raise ValueError(f"invalid CRS '{v}'; expected format like 'EPSG:4326'")
    return v


CRSStr = Annotated[str, AfterValidator(_check_crs_format)]
OptionalCRSStr = Annotated[Optional[str], AfterValidator(_check_crs_format)]


class Source(BaseModel):
    id: str = Field(..., description="Unique handle used to reference this source")
    format: Literal[tuple(SOURCE_FORMATS)]  # type: ignore[valid-type]
    uri: str = Field(..., description="File path, folder (.gdb), service URL, or postgres connection string")
    layer: Optional[str] = Field(
        None,
        description="Layer / typename / sheet / collection name / ArcGIS sublayer id / postgres table (optionally schema.table)",
    )
    crs: OptionalCRSStr = Field(None, description="CRS of the source, e.g. EPSG:4326")
    geometry: Optional[bool] = Field(
        None, description="Override whether this source has geometry"
    )
    make_valid: bool = Field(
        True, description="Repair invalid geometry (ST_MakeValid) when loading"
    )
    force_2d: bool = Field(
        False, description="Drop Z and M values from the geometry (ST_Force2D) when loading"
    )
    # ArcGIS REST tuning
    where: str = Field("1=1", description="ArcGIS REST 'where' filter")
    page_size: int = Field(2000, description="Records per request (ArcGIS REST) or per page (OGC API - Features)")
    # xlsx/csv tuning
    header_row: Optional[bool] = Field(
        None, description="Treat the first row as column headers (xlsx/csv only); omit to auto-detect"
    )
    # json-only: where the records sit in the document
    records: Optional[str] = Field(
        None,
        description="Dot path to the array of records inside a JSON document, e.g. data.items (json only); omit when the file is the array itself",
    )
    # xlsx/csv/json-only: build geometry out of otherwise-plain columns
    x_field: Optional[str] = Field(None, description="Column holding X / longitude (xlsx/csv/json only, builds point geometry)")
    y_field: Optional[str] = Field(None, description="Column holding Y / latitude (xlsx/csv/json only, builds point geometry)")
    geom_field: Optional[str] = Field(
        None, description="Column holding WKT/WKB/GeoJSON geometry (xlsx/csv/json only)"
    )

    @property
    def has_geometry(self) -> bool:
        if self.geometry is not None:
            return self.geometry
        if (self.x_field and self.y_field) or self.geom_field:
            return True
        return self.format in SPATIAL_FORMATS

    @model_validator(mode="after")
    def _check_arcgis_rest_crs(self):
        if self.format == "arcgis_rest" and self.crs not in (None, "EPSG:4326"):
            raise ValueError(
                f"source '{self.id}': arcgis_rest is always fetched as EPSG:4326; "
                f"remove 'crs: {self.crs}' or set it to EPSG:4326"
            )
        return self

    @model_validator(mode="after")
    def _check_arcgis_rest_layer_uri(self):
        if self.format == "arcgis_rest" and self.layer:
            from urllib.parse import urlparse
            last = urlparse(self.uri).path.rstrip("/").rsplit("/", 1)[-1]
            if last.isdigit():
                raise ValueError(
                    f"source '{self.id}': uri already ends in a sublayer id "
                    f"('.../{last}') and 'layer' is also set ('{self.layer}') — "
                    "remove 'layer' (uri is the full layer endpoint) or strip "
                    "the trailing id from uri (uri is the service root)"
                )
        return self

    @model_validator(mode="after")
    def _check_oapif_crs(self):
        if self.format == "oapif" and self.crs not in (None, "EPSG:4326"):
            raise ValueError(
                f"source '{self.id}': oapif is always fetched in the default CRS84 "
                f"(EPSG:4326) response; remove 'crs: {self.crs}' or set it to EPSG:4326"
            )
        return self

    @model_validator(mode="after")
    def _check_records_format(self):
        if self.records is not None and self.format != "json":
            raise ValueError(f"source '{self.id}': 'records' only applies to json sources")
        if self.records is not None and not all(self.records.split(".")):
            raise ValueError(
                f"source '{self.id}': 'records' must be a dot path like data.items, got '{self.records}'"
            )
        return self

    @model_validator(mode="after")
    def _check_header_row_format(self):
        if self.header_row is not None and self.format not in HEADER_ROW_FORMATS:
            raise ValueError(
                f"source '{self.id}': 'header_row' only applies to xlsx/csv sources"
            )
        return self

    @model_validator(mode="after")
    def _check_geometry_fields_format(self):
        if (self.x_field or self.y_field or self.geom_field) and self.format not in TABULAR_GEOM_FORMATS:
            raise ValueError(
                f"source '{self.id}': 'x_field'/'y_field'/'geom_field' only apply to xlsx/csv/json sources"
            )
        if (self.x_field is None) != (self.y_field is None):
            raise ValueError(
                f"source '{self.id}': 'x_field' and 'y_field' must be set together"
            )
        if self.geom_field and (self.x_field or self.y_field):
            raise ValueError(
                f"source '{self.id}': set either 'geom_field' or 'x_field'/'y_field', not both"
            )
        return self


class StepBase(BaseModel):
    """Shared by every step type.

    `branch` lets a step operate on a named branch (created earlier by a
    `snapshot` step) instead of the main chain — e.g. filter or buffer only
    that branch while the main chain continues unmodified. For `snapshot`
    itself, `branch` means "snapshot from this branch" instead of "operate on
    this branch"; default (None) is always the main chain either way.
    """

    branch: Optional[str] = Field(
        None,
        description="Name of a branch (from an earlier snapshot step) to apply this step to instead of the main chain",
    )


def _rejects_field(what: str):
    return Field(
        None,
        description=f"Output layer to write {what} to. Those rows then leave the chain: only "
        "the rest continue to the next step",
    )


class RejectsMixin(BaseModel):
    """For the steps with a `rejects` field: ones that can split their rows into passed and
    rejected, like an FME transformer's Passed/Failed ports. Without `rejects`, nothing
    changes: a join keeps unmatched rows (with NULL fields) and a filter or clip drops its
    rejects. Each step declares the field itself (after its own fields, so it's written last
    in the YAML); this only treats the editor's blank value as "not set"."""

    @model_validator(mode="before")
    @classmethod
    def _blank_rejects_to_none(cls, data):
        if isinstance(data, dict) and isinstance(data.get("rejects"), str) and not data["rejects"].strip():
            data = {**data, "rejects": None}
        return data


class Aggregate(BaseModel):
    """One value per group (or per chain) for a dissolve or combine_lines step: `how` to
    combine the `from` column's values (no SQL needed), or a SQL aggregate in `expr`.
    `to` defaults to the `from` column's name."""

    to: str = Field("", description="Output column name (default: the `from` column)")
    from_: Optional[str] = Field(None, alias="from", description="The column to combine")
    how: Optional[Literal[tuple(AGGREGATE_HOWS)]] = Field(  # type: ignore[valid-type]
        None, description="How to combine its values: " + ", ".join(AGGREGATE_HOWS)
    )
    expr: Optional[str] = Field(None, description="A SQL aggregate, instead of from/how")

    model_config = {"populate_by_name": True}

    @model_validator(mode="before")
    @classmethod
    def _strip(cls, data):
        # Trim only, like MapItem.expr; a blank field (the editor mid-edit) counts as unset.
        if isinstance(data, dict):
            data = {k: v.strip() if k in ("to", "from", "from_", "expr") and isinstance(v, str) else v
                    for k, v in data.items()}
            data = {k: v for k, v in data.items() if not (k != "to" and v == "")}
        return data

    @model_serializer(mode="wrap")
    def _no_default_name(self, handler):
        # `to` is the `from` column's name unless given: written only when it isn't.
        data = handler(self)
        if self.from_ is not None and self.to == self.from_:
            data.pop("to", None)
        return data

    @model_validator(mode="after")
    def _complete(self):
        if not self.to:
            self.to = self.from_ or ""
        if not self.to:
            raise ValueError("an aggregate needs a name ('to')")
        if self.expr is not None:
            if self.how is not None or self.from_ is not None:
                raise ValueError(f"aggregate '{self.to}': give either expr, or from and how, not both")
        elif self.how is None:
            raise ValueError(f"aggregate '{self.to}' needs 'how' (or a SQL 'expr')")
        elif self.from_ is None and self.how != "count":
            raise ValueError(f"aggregate '{self.to}' needs a column ('from') for how: {self.how}")
        return self


JoinFields = dict[str, Union[str, Aggregate]]


class _SummarizingJoin(BaseModel):
    """`fields` for a join that can summarize its matches (`match: summarize`): each field is
    then an aggregate over the matches, `{output: {from: col, how: ...}}` (or `{expr: ...}`),
    instead of `{output: source_column}`."""

    @model_validator(mode="before")
    @classmethod
    def _name_field_aggregates(cls, data):
        # An aggregate's name is its key in `fields`.
        if isinstance(data, dict) and isinstance(data.get("fields"), dict):
            data = {**data, "fields": {
                out: {**spec, "to": out} if isinstance(spec, dict) else spec
                for out, spec in data["fields"].items()
            }}
        return data

    @model_validator(mode="after")
    def _fields_match_mode(self):
        summarize = self.match == "summarize"
        for out, spec in self.fields.items():
            if summarize and isinstance(spec, str):
                raise ValueError(
                    f"{self.type}: with match: summarize, say how to combine field '{out}', "
                    f"e.g. {{from: {spec}, how: all}}"
                )
            if not summarize and not isinstance(spec, str):
                raise ValueError(
                    f"{self.type}: field '{out}' says how to combine matches, which needs "
                    "match: summarize"
                )
        return self

    @field_serializer("fields", check_fields=False)
    def _fields_without_names(self, fields):
        # The key already names each aggregate.
        return {
            out: spec if isinstance(spec, str)
            else {k: v for k, v in spec.model_dump(by_alias=True, exclude_none=True).items() if k != "to"}
            for out, spec in fields.items()
        }


class SpatialJoin(StepBase, RejectsMixin, _SummarizingJoin):
    type: Literal["spatial_join"] = "spatial_join"
    source: str = Field(..., description="id of the source to join against")
    predicate: Literal[tuple(JOIN_PREDICATES)] = "intersects"  # type: ignore[valid-type]
    match: Literal["first", "all", "summarize"] = Field(
        "first",
        description="'first': keep only the first match (current behaviour). "
        "'all': one output row per match, base row repeated for each. "
        "'summarize': one row per feature, each field an aggregate over its matches",
    )
    on_multiple: Literal["first", "largest_overlap"] = Field(
        "first",
        description="Only applies when match='first'. 'first': deterministic tie-break "
        "on the join source's original row order. 'largest_overlap': pick the match with "
        "the largest ST_Intersection area (ties broken by original row order).",
    )
    fields: JoinFields = Field(
        default_factory=dict,
        description="{output_name: source_column} fields to pull from the match; with match: "
        "summarize, {output_name: {from: source_column, how: ...}}",
    )
    rejects: Optional[str] = _rejects_field("the features with no match")


class AttributeJoin(StepBase, RejectsMixin, _SummarizingJoin):
    type: Literal["attribute_join"] = "attribute_join"
    source: str
    left: str = Field(..., description="SQL expression on the base row (or a literal)")
    right: str = Field(..., description="Column on the joined source to match on")
    match: Literal["first", "summarize"] = Field(
        "first",
        description="'first': the first matching row, in the source's row order. "
        "'summarize': each field an aggregate over all the matching rows",
    )
    fields: JoinFields = Field(
        default_factory=dict,
        description="{output_name: source_column}; with match: summarize, "
        "{output_name: {from: source_column, how: ...}}",
    )
    rejects: Optional[str] = _rejects_field("the rows with no match")


class NearestNeighbor(StepBase, RejectsMixin):
    type: Literal["nearest_neighbor"] = "nearest_neighbor"
    source: str = Field(..., description="id of the source to search for the nearest feature in")
    max_distance: Optional[float] = Field(
        None, description="Max search radius in working CRS units; features with no match within range get NULL fields"
    )
    distance_field: Optional[str] = Field(
        None, description="Output column name to store the distance to the matched neighbour"
    )
    fields: dict[str, str] = Field(
        default_factory=dict,
        description="{output_name: source_column} fields to pull from the nearest match",
    )
    rejects: Optional[str] = _rejects_field("the features with no neighbour (within max_distance)")


class Buffer(StepBase):
    type: Literal["buffer"] = "buffer"
    distance: float = Field(..., description="Buffer distance in working CRS units")


class Centroid(StepBase):
    type: Literal["centroid"] = "centroid"


class Simplify(StepBase):
    """Fewer vertices: every line and polygon edge within `tolerance` of the original.
    With `shared_boundaries`, the polygons are simplified together as one coverage, so an
    edge two neighbours share is simplified once and stays shared: no gaps or overlaps open
    up between them."""

    type: Literal["simplify"] = "simplify"
    tolerance: float = Field(
        ..., gt=0, description="How far (working CRS units) the simplified shape may stray from the original"
    )
    shared_boundaries: bool = Field(
        False,
        description="Simplify the polygons together, keeping the edges neighbours share "
        "shared (needs polygons that don't overlap); lines and points are still simplified "
        "one by one",
    )
    keep_outer_boundary: bool = Field(
        False,
        description="With shared_boundaries: leave the outer edge of the coverage as it is "
        "(a border or a coastline) and simplify only the edges inside it",
    )

    @model_validator(mode="after")
    def _outer_needs_shared(self):
        if self.keep_outer_boundary and not self.shared_boundaries:
            raise ValueError("simplify: keep_outer_boundary needs shared_boundaries: true")
        return self


class SnapToGrid(StepBase):
    """Every vertex rounded to a grid of `grid_size` (working CRS units), keeping the
    geometry valid: polygons narrower than a grid cell collapse to an empty geometry."""

    type: Literal["snap_to_grid"] = "snap_to_grid"
    grid_size: float = Field(..., gt=0, description="Grid cell size in working CRS units, e.g. 0.01 for centimetres")


class CleanCoverage(StepBase):
    """Polygons that should fit together edge to edge (a coverage) made to: edges within
    `snap_distance` of each other snapped together, overlaps given to one of the
    polygons, and enclosed gaps narrower than `max_gap_width` filled. A polygon left
    with nothing (it lay wholly under another) comes out empty."""

    type: Literal["clean_coverage"] = "clean_coverage"
    snap_distance: Optional[float] = Field(
        None, gt=0,
        description="How close (working CRS units) edges must be to snap together; "
        "default: a tiny distance derived from the data's extent",
    )
    max_gap_width: Optional[float] = Field(
        None, gt=0,
        description="Fill gaps between the polygons narrower than this (working CRS units); "
        "default: gaps are left open",
    )


class Explode(StepBase):
    """Multipart → singleparts: one row per part of each multi-geometry (or geometry
    collection), each with the feature's attributes."""

    type: Literal["explode"] = "explode"
    part_field: Optional[str] = Field(
        None, description="Output column to number the parts in (1, 2, …, per feature)"
    )

    @model_validator(mode="after")
    def _part_field_not_reserved(self):
        # The editor writes a blank field as "": no part column.
        self.part_field = (self.part_field or "").strip() or None
        if self.part_field and self.part_field.lower() in ("geom", "__src_row"):
            raise ValueError(f"explode: '{self.part_field}' is reserved; give the part column another name")
        return self


class Clip(StepBase, RejectsMixin):
    type: Literal["clip"] = "clip"
    source: str = Field(..., description="id of the mask source")
    predicate: Literal[tuple(JOIN_PREDICATES)] = "intersects"  # type: ignore[valid-type]
    rejects: Optional[str] = _rejects_field(
        "the features clip would drop (outside the mask, or only touching it), unclipped"
    )


class Erase(StepBase):
    type: Literal["erase"] = "erase"
    source: str = Field(..., description="id of the eraser source")
    predicate: Literal[tuple(JOIN_PREDICATES)] = "intersects"  # type: ignore[valid-type]


class ExprColumn(BaseModel):
    """A named SQL expression: a dissolve or combine_lines group key."""

    to: str = Field(..., description="Output column name")
    expr: str = Field(..., description="SQL expression")

    @model_validator(mode="before")
    @classmethod
    def _strip(cls, data):
        # Trim only, like MapItem.expr: a hand-formatted multi-line expression stays as written.
        if isinstance(data, dict):
            data = {k: v.strip() if k in ("to", "expr") and isinstance(v, str) else v for k, v in data.items()}
        return data

    @model_validator(mode="after")
    def _not_blank(self):
        if not self.to:
            raise ValueError("an expression column needs a name ('to')")
        if not self.expr:
            raise ValueError(f"column '{self.to}' needs an expression")
        return self


class _GroupedStep(StepBase):
    """Group keys and per-group aggregates, shared by dissolve and combine_lines."""

    # Declared here only so it comes first: a field keeps the position it's first declared
    # at, and the subclasses' `type` would otherwise follow `by` and `aggregates` in a dump.
    type: str

    by: list[Union[str, ExprColumn]] = Field(
        default_factory=list,
        description="Group keys: a column name, or {to, expr} to group by a SQL expression; "
        "empty = one group",
    )
    aggregates: list[Aggregate] = Field(
        default_factory=list,
        description="Columns computed per group, e.g. {from: population, how: sum} or "
        "{to: total, expr: 'sum(population)'}",
    )

    @model_validator(mode="after")
    def _unique_names(self):
        seen: set[str] = set()
        for name in self.output_names:
            if name.lower() in ("geom", "__src_row"):
                raise ValueError(f"{self.type}: '{name}' is reserved; give the column another name")
            if name.lower() in seen:
                raise ValueError(f"{self.type}: column '{name}' is produced twice")
            seen.add(name.lower())
        return self

    @property
    def output_names(self) -> list[str]:
        """The columns the step writes besides geom: its keys, then its aggregates."""
        keys = [k if isinstance(k, str) else k.to for k in self.by]
        return keys + [a.to for a in self.aggregates]


class Dissolve(_GroupedStep):
    type: Literal["dissolve"] = "dissolve"


class CombineLines(_GroupedStep):
    """Joins lines end to end into longer lines: one feature per connected chain of lines
    that share the `by` values, with `aggregates` computed over the features that make up
    each chain. Features that aren't lines are kept one per feature."""

    type: Literal["combine_lines"] = "combine_lines"
    preserve_direction: bool = Field(
        True,
        description="Only join lines that run the same way (end to start); false lets a "
        "line be reversed to fit",
    )


class IntersectOverlay(StepBase):
    type: Literal["intersect_overlay"] = "intersect_overlay"
    source: str = Field(..., description="id of the overlay source")
    fields: dict[str, str] = Field(default_factory=dict)


class LineOverlay(StepBase):
    """Line-on-line overlay ("identity"): the running lines are cut where they lie on the
    `source` lines. Shared stretches get `fields` from the source, the rest pass through
    with NULL fields, so the output is still the complete network."""

    type: Literal["line_overlay"] = "line_overlay"
    source: str = Field(..., description="id of the overlay line source")
    fields: dict[str, str] = Field(default_factory=dict)
    tolerance: Optional[float] = Field(
        None,
        gt=0,
        description="How far apart (working CRS units) two lines may be and still count as "
        "lying on each other; without it, they must match to within 0.000001 units",
    )


class Filter(StepBase, RejectsMixin):
    type: Literal["filter"] = "filter"
    where: str = Field(
        ..., description="SQL boolean expression; rows where this is false are dropped"
    )
    rejects: Optional[str] = _rejects_field("the rows where `where` isn't true")


class RemoveDuplicates(StepBase, RejectsMixin):
    """One row per distinct value of the `by` columns (and/or the geometry): the first, in
    row order. Rows without a geometry count as having the same one, as NULLs in a `by`
    column count as the same value."""

    type: Literal["remove_duplicates"] = "remove_duplicates"
    by: list[str] = Field(
        default_factory=list, description="Columns whose values together make a row a duplicate"
    )
    geometry: bool = Field(
        False,
        description="Also compare the geometry: equal shapes count as duplicates, whatever "
        "vertex they start at or way they run",
    )
    rejects: Optional[str] = _rejects_field("the duplicates")

    @model_validator(mode="after")
    def _something_to_compare(self):
        self.by = [c.strip() for c in self.by if c.strip()]
        if not self.by and not self.geometry:
            raise ValueError("remove_duplicates: list the columns to compare ('by'), or set geometry: true")
        return self


class Merge(StepBase):
    type: Literal["merge"] = "merge"
    source: str = Field(
        ..., description="id of the source (or derived_source) to append via UNION ALL BY NAME"
    )


class Snapshot(StepBase):
    """Names the running chain's current state (mid-pipeline, after whatever
    steps ran before it) so a later step's `source:` can join back against it
    — forking off a source *after* some processing, not just a raw source.

    Does not itself change the running chain; it only registers a side view.
    """

    type: Literal["snapshot"] = "snapshot"
    id: str = Field(..., description="Name for this step's current state; usable as source: in a later step")


class DerivedSource(BaseModel):
    """A filtered/buffered view of an existing source or derived_source.

    Registered in the same `src_<id>` namespace as ordinary sources, so any
    join step's `source:` can target it exactly like a real source.
    """

    id: str = Field(..., description="Unique handle used to reference this derived source")
    from_: str = Field(..., alias="from", description="id of a source or derived_source to build from")
    where: Optional[str] = Field(None, description="SQL filter applied before use")
    buffer: Optional[float] = Field(None, description="Buffer distance (working CRS units) applied before use")
    make_valid: bool = Field(
        True, description="Repair invalid geometry (ST_MakeValid) when deriving"
    )

    model_config = {"populate_by_name": True}


class CodeCase(BaseModel):
    """One rule in a codelist: 'if <pattern matches source>, output value'."""

    value: str = Field(..., description="Output value when this case matches")
    match: Optional[str] = Field(None, description="Exact-match pattern")
    like: Optional[str] = Field(None, description="SQL LIKE pattern, e.g. '%potato%'")
    regex: Optional[str] = Field(None, description="Regex pattern")
    is_blank: bool = Field(False, description="Match when the source value is NULL or ''")

    @model_validator(mode="after")
    def _one_pattern(self):
        n = sum(x is not None for x in (self.match, self.like, self.regex)) + (
            1 if self.is_blank else 0
        )
        if n != 1:
            raise ValueError("a codelist case needs exactly one of match/like/regex/is_blank")
        return self


class CodeList(BaseModel):
    """Translate one column's value via rules, or via a CSV lookup table.

    Either give `cases` (small, inline, evaluated top-to-bottom, first match
    wins) or `file` (a CSV with a key column and a value column, for large
    code tables). Not both.
    """

    source: str = Field(..., description="Column whose value is being translated")
    case_insensitive: bool = True
    cases: list[CodeCase] = Field(default_factory=list)
    default: Optional[str] = Field(None, description="Value when nothing matches")
    file: Optional[str] = Field(None, description="CSV file for a lookup-table codelist")
    file_match_col: Optional[str] = Field(None, description="Key column in the CSV")
    file_value_col: Optional[str] = Field(None, description="Output column in the CSV")

    @model_validator(mode="after")
    def _cases_xor_file(self):
        if bool(self.cases) == bool(self.file):
            raise ValueError(
                "codelist needs either inline 'cases' or a 'file' lookup, not both/neither"
            )
        if self.file and not (self.file_match_col and self.file_value_col):
            raise ValueError("file-based codelist needs file_match_col and file_value_col")
        return self


class MapItem(BaseModel):
    to: str = Field(..., description="Output column name")
    # exactly one of: from_ / const / expr / func
    from_: Optional[str] = Field(None, alias="from")
    const: Optional[object] = None
    expr: Optional[str] = None
    func: Optional[Literal[tuple(MAP_FUNCS)]] = None  # type: ignore[valid-type]
    codelist: Optional[CodeList] = None
    cast: Optional[str] = Field(None, description="Cast result to this SQL type")

    model_config = {"populate_by_name": True}

    @model_validator(mode="before")
    @classmethod
    def _blank_strings_to_none(cls, data):
        # The editor writes an empty string (not the field at all) for a `from`/`expr`
        # picker that hasn't been given a value yet — e.g. a mapping row switched to
        # `func` still carrying a blank leftover `from: ""` from its `from` days, or a
        # freshly-added row still mid-edit. `_ident("")` on that empty string builds
        # `"" AS ...`, an empty quoted identifier DuckDB's parser rejects outright — so
        # treat blank as "not provided", same as the row never had it, before that can
        # reach SQL generation.
        if isinstance(data, dict):
            for key in ("from", "expr"):
                if data.get(key) == "":
                    data = {**data, key: None}
            expr = data.get("expr")
            if isinstance(expr, str) and expr != expr.strip():
                # Trim only — a hand-formatted multi-line expression (CASE/WHEN on their
                # own indented lines) is legitimate and dump_config_yaml() now writes it
                # back as a YAML literal block (`|`) so it round-trips exactly. Collapsing
                # internal whitespace here would undo that formatting on every load.
                data = {**data, "expr": expr.strip()}
        return data

    @model_validator(mode="after")
    def _exactly_one_source(self):
        provided = [
            v for v in (self.from_, self.const, self.expr, self.func, self.codelist)
            if v is not None
        ]
        if len(provided) != 1:
            raise ValueError(
                f"mapping '{self.to}' needs exactly one of from/const/expr/func/codelist"
            )
        return self


class Attributes(StepBase):
    """Sets and removes attributes mid-pipeline: each of
    `columns` (the mapping's rules) adds a column or overwrites one of the same name, in
    order, so a column can use the ones set before it; then `drop` removes columns. Every
    other column passes through."""

    type: Literal["attributes"] = "attributes"
    columns: list[MapItem] = Field(
        default_factory=list,
        description="Columns to add or overwrite, with the same rules as the mapping",
    )
    drop: list[str] = Field(default_factory=list, description="Columns to remove")

    @model_validator(mode="after")
    def _keep_geom(self):
        if any(c.lower() == "geom" for c in self.drop):
            raise ValueError("attributes: 'geom' can't be dropped")
        return self


Step = Annotated[
    Union[
        SpatialJoin, AttributeJoin, NearestNeighbor, Buffer, Centroid, Clip, Erase,
        Dissolve, CombineLines, Simplify, SnapToGrid, CleanCoverage, Explode,
        IntersectOverlay, LineOverlay, Filter, RemoveDuplicates, Merge, Snapshot,
        Attributes,
    ],
    Field(discriminator='type'),
]

# Every step `type`, in the order of the Step union.
STEP_TYPES = [cls.model_fields["type"].default for cls in get_args(get_args(Step)[0])]


class OutputLayer(BaseModel):
    layer: str
    crs: CRSStr = "EPSG:25833"
    mapping: list[MapItem] = Field(default_factory=list)
    filter: Optional[str] = None


# ---------------------------------------------------------------------------
# Validator bodies shared by PipelineDef, Config and the single-pipeline upgrade
# ---------------------------------------------------------------------------

def _normalise_layer_shorthand(
    data: dict, extra_keys: tuple[str, ...], overwrite: bool
) -> dict:
    """Fold the legacy singular `layer:` shorthand into a one-entry `layers:` list.

    `extra_keys` and `overwrite` differ per caller and are deliberately not unified: the
    single-pipeline shorthand's `output:` block also absorbs a sibling `mapping:` key (an
    output has no mapping of its own, so there `mapping:` can only mean the layer's
    override) and clobbers any existing `layers:`, while `PipelineDef` leaves an explicit
    `layers:` list winning.
    """
    if "layer" not in data:
        return data
    layer_data: dict = {"layer": data.pop("layer")}
    for key in extra_keys:
        if key in data:
            layer_data[key] = data.pop(key)
    if overwrite:
        data["layers"] = [layer_data]
    else:
        data.setdefault("layers", [layer_data])
    return data


def _require_layers(layers: list) -> None:
    if not layers:
        raise ValueError("at least one layer must be defined under 'layers'")


# Output files with these suffixes are written as GeoParquet instead of GeoPackage.
PARQUET_SUFFIXES = (".parquet", ".geoparquet")

# Characters Windows (the strictest of the platforms we run on) won't allow in a filename.
_UNSAFE_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def is_parquet_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in PARQUET_SUFFIXES


def _check_parquet_layer_names(path: str, names: list[str]) -> None:
    """GeoParquet has no layers: each layer becomes its own `<layer>.parquet` file
    (see output.parquet_layer_path), so layer names must be valid, distinct filenames.
    In a GeoPackage a duplicate name is just a second table; here it would silently
    overwrite the first layer's file."""
    if not is_parquet_path(path):
        return
    seen: set[str] = set()
    for name in names:
        if not name.strip() or name.strip(".") == "" or _UNSAFE_FILENAME_RE.search(name):
            raise ValueError(
                f"layer name '{name}' can't be used as a file name, which a GeoParquet "
                f"output ('{path}') needs; avoid < > : \" / \\ | ? * and blank names"
            )
        # Case-insensitive: Windows and macOS filesystems would map both to one file.
        if name.lower() in seen:
            raise ValueError(
                f"layer name '{name}' is used twice; a GeoParquet output ('{path}') "
                f"writes one file per layer, so layer names must be unique"
            )
        seen.add(name.lower())


def reject_layer_names(steps: list) -> list[str]:
    """Names of the output layers the steps' `rejects` write, in step order."""
    return [st.rejects for st in steps if getattr(st, "rejects", None)]


def _check_reject_names(layer_names: list[str], reject_names: list[str]) -> None:
    """A rejects layer is an extra layer in the output, so its name can't also be an
    output layer's or another step's rejects layer: the second write would collide with
    the first."""
    seen = {n.lower() for n in layer_names}
    for name in reject_names:
        if name.lower() in seen:
            raise ValueError(
                f"rejects layer '{name}' is already used as an output layer or by another "
                f"step's rejects; give it a name of its own"
            )
        seen.add(name.lower())


def _validate_refs(
    sources: list[Source],
    derived_sources: list[DerivedSource],
    base: str,
    steps: list,
) -> None:
    """Check that base/derived_source/step references all resolve."""
    ids = {s.id for s in sources}
    if base and base not in ids:
        raise ValueError(f"base '{base}' is not among sources {sorted(ids)}")
    for ds in derived_sources:
        if ds.from_ not in ids:
            raise ValueError(f"derived_source '{ds.id}' references unknown source '{ds.from_}'")
        ids.add(ds.id)
    # Snapshots only become referenceable by steps *after* them (as a
    # source: or a branch:), so branches grows as we walk the list in order.
    branches: set[str] = set()
    for st in steps:
        if isinstance(st, Snapshot):
            if st.id in ids:
                raise ValueError(f"snapshot id '{st.id}' collides with an existing source/derived_source/snapshot id")
            if st.branch and st.branch not in branches:
                raise ValueError(f"snapshot '{st.id}' references unknown branch '{st.branch}'")
            ids.add(st.id)
            branches.add(st.id)
            continue
        if st.branch and st.branch not in branches:
            raise ValueError(f"step references unknown branch '{st.branch}'")
        # Skip empty-string source — step is still being configured in the editor.
        if hasattr(st, 'source') and st.source and st.source not in ids:
            raise ValueError(f"step references unknown source '{st.source}'")


def _validate_no_geom_mapping(
    sources: list[Source], base: str, mappings: list[list[MapItem]]
) -> None:
    base_src = next((s for s in sources if s.id == base), None)
    if base_src is None or not base_src.has_geometry:
        return
    if any(m.to == "geom" for mapping in mappings for m in mapping):
        raise ValueError(
            "mapping target 'geom' is reserved for the output geometry "
            "column (added automatically); rename this mapping entry"
        )


# ---------------------------------------------------------------------------
# Multi-pipeline Config: one output file, N independent pipelines/layers
# ---------------------------------------------------------------------------

class PipelineDef(BaseModel):
    """One pipeline within a Config — its chain can fan out to multiple output layers."""

    name: str
    description: str = ""
    working_crs: OptionalCRSStr = None
    sources: list[Source]
    derived_sources: list[DerivedSource] = Field(default_factory=list)
    base: str
    steps: list[Step] = Field(default_factory=list)
    mapping: list[MapItem]
    layers: list[OutputLayer] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalise_layers(cls, data: object) -> object:
        # Accept the old singular layer/crs/filter shorthand (one pipeline,
        # one layer) alongside the new plural `layers:` list.
        if not isinstance(data, dict):
            return data
        return _normalise_layer_shorthand(data, ("crs", "filter"), overwrite=False)

    @model_validator(mode="after")
    def _check_layers(self):
        _require_layers(self.layers)
        _check_reject_names([layer.layer for layer in self.layers], reject_layer_names(self.steps))
        return self

    @model_validator(mode="after")
    def _check_refs(self):
        _validate_refs(self.sources, self.derived_sources, self.base, self.steps)
        return self

    @model_validator(mode="after")
    def _check_no_geom_mapping(self):
        mappings = [self.mapping] + [layer.mapping for layer in self.layers if layer.mapping]
        _validate_no_geom_mapping(self.sources, self.base, mappings)
        return self

    def source(self, sid: str) -> Source:
        src = next((s for s in self.sources if s.id == sid), None)
        if src is None:
            # `base` (and derived_source `from`) are allowed to be blank/stale while a
            # pipeline is still being edited — see _check_refs's `self.base and` guard —
            # so this is only caught here, once something actually tries to run with it.
            raise ValueError(f"unknown source '{sid}'" if sid else "no base source selected")
        return src

    @property
    def base_source(self) -> Source:
        return self.source(self.base)

    @property
    def effective_working_crs(self) -> str:
        return self.working_crs or self.base_source.crs or self.layers[0].crs


class DatasetMetadata(BaseModel):
    """Dataset-level metadata for the config's output."""

    name: Optional[str] = None
    abstract: Optional[str] = None
    origin: Optional[str] = None
    update_frequency: Optional[str] = None
    geometric_quality: Optional[str] = None
    attribute_quality: Optional[str] = None
    access_method_source: Optional[str] = None
    gdpr: Optional[str] = None


class Config(BaseModel):
    """Top-level config: one shared output + one or more pipelines.

    `output` ending in .parquet/.geoparquet is written as GeoParquet, anything else as
    GeoPackage. GeoParquet has no layers, so a single layer in total is written to
    `output` itself, and several to `<output without suffix>/<layer>.parquet`.
    """

    name: str
    description: str = ""
    output: str
    overwrite: bool = True
    metadata: Optional[DatasetMetadata] = None
    pipelines: list[PipelineDef]

    @model_validator(mode="after")
    def _check(self):
        if not self.pipelines:
            raise ValueError("a config needs at least one pipeline")
        layers = [layer.layer for p in self.pipelines for layer in p.layers]
        rejects = [name for p in self.pipelines for name in reject_layer_names(p.steps)]
        _check_reject_names(layers, rejects)
        _check_parquet_layer_names(self.output, layers + rejects)
        return self

    @property
    def layer_count(self) -> int:
        """Layers written to the output, including the steps' rejects layers."""
        return sum(len(p.layers) + len(reject_layer_names(p.steps)) for p in self.pipelines)


# The keys a single-pipeline file carries over to its one pipeline.
_PIPELINE_KEYS = ("name", "description", "working_crs", "sources", "derived_sources", "base", "steps", "mapping")


def _upgrade_single_pipeline(data: dict) -> dict:
    """The legacy single-pipeline shorthand (no `pipelines:` wrapper; one pipeline's keys at
    the top level, plus an `output:` block, or an `outputs:` list of one, holding the path,
    `overwrite` and the layers) as a Config dict."""
    if "output" in data and "outputs" in data:
        raise ValueError("specify either 'output' or 'outputs', not both")
    if "outputs" in data:
        outputs = data["outputs"] or []
        if len(outputs) != 1:
            raise ValueError(
                f"a single-pipeline file writes one output, but 'outputs' lists {len(outputs)}; "
                "use the full form (output: + pipelines:) to write several layers"
            )
        out = outputs[0]
    elif data.get("output") is not None:
        out = data["output"]
    else:
        raise ValueError("at least one output must be defined under 'output'")
    if not isinstance(out, dict) or "path" not in out:
        raise ValueError("the 'output' block needs a 'path'")
    out = _normalise_layer_shorthand(dict(out), ("crs", "mapping", "filter"), overwrite=True)
    pipeline = {k: data[k] for k in _PIPELINE_KEYS if k in data}
    pipeline["layers"] = out.get("layers", [])
    upgraded = {k: data[k] for k in ("name", "description") if k in data}
    return {
        **upgraded, "output": out["path"], "overwrite": out.get("overwrite", True),
        "pipelines": [pipeline],
    }


def load_config(path: str | Path) -> Config:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return load_config_dict(data)


def load_config_dict(data: dict) -> Config:
    if "pipelines" not in data:
        data = _upgrade_single_pipeline(data)
    return Config.model_validate(data)


class _ConfigDumper(yaml.SafeDumper):
    """SafeDumper that writes multi-line strings (e.g. a hand-formatted `expr`) as a
    literal block (`|`) instead of PyYAML's default plain-scalar folding.

    Folding only replaces each line break with a single space — it does *not* strip a
    line's own leading indentation, so a nicely indented multi-line SQL expression comes
    back mangled (e.g. "COALESCE(\\n  NULLIF(" turns into "COALESCE(  NULLIF(") the next
    time the file is loaded and re-wrapped. Block style preserves the string byte-for-byte.
    """


def _str_representer(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_ConfigDumper.add_representer(str, _str_representer)


def dump_config_yaml(config: Config) -> str:
    data = config.model_dump(by_alias=True, exclude_none=True)
    return yaml.dump(data, Dumper=_ConfigDumper, sort_keys=False, allow_unicode=True)
