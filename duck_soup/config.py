"""Pipeline configuration: the YAML schema, validated with pydantic.

A pipeline is: a set of sources, one base source whose features flow through,
an ordered list of join steps, an ordered attribute mapping, and one output.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal, Optional, Union

import yaml
from pydantic import AfterValidator, BaseModel, Field, model_validator

# Formats we know how to read. Everything except arcgis_rest goes through
# DuckDB's ST_Read (GDAL); arcgis_rest is fetched (paginated) to a temp file first.
SOURCE_FORMATS = [
    "gpkg",       # GeoPackage
    "geojson",    # GeoJSON file or URL
    "gml",        # GML / INSPIRE
    "fgdb",       # Esri File Geodatabase (.gdb folder)
    "wfs",        # OGC WFS endpoint
    "arcgis_rest",  # ArcGIS REST FeatureServer/MapServer query endpoint
    "parquet",    # (Geo)Parquet
    "shp",        # Shapefile
    "xlsx",       # Excel sheet (tabular, no geometry)
    "csv",        # CSV (tabular, no geometry)
]

# Formats that carry geometry by default.
SPATIAL_FORMATS = {"gpkg", "geojson", "gml", "fgdb", "wfs", "arcgis_rest", "parquet", "shp"}

JOIN_PREDICATES = ["intersects", "contains", "within"]

# Mapping value functions computed by the engine.
MAP_FUNCS = ["uuid", "now", "today", "lon", "lat", "mgrs", "wkb", "area", "length"]

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
    uri: str = Field(..., description="File path, folder (.gdb), or service URL")
    layer: Optional[str] = Field(None, description="Layer / typename / sheet name")
    crs: OptionalCRSStr = Field(None, description="CRS of the source, e.g. EPSG:4326")
    geometry: Optional[bool] = Field(
        None, description="Override whether this source has geometry"
    )
    make_valid: bool = Field(
        True, description="Repair invalid geometry (ST_MakeValid) when loading"
    )
    # ArcGIS REST tuning
    where: str = Field("1=1", description="ArcGIS REST 'where' filter")
    page_size: int = Field(2000, description="ArcGIS REST records per request")

    @property
    def has_geometry(self) -> bool:
        if self.geometry is not None:
            return self.geometry
        return self.format in SPATIAL_FORMATS

    @model_validator(mode="after")
    def _check_arcgis_rest_crs(self):
        if self.format == "arcgis_rest" and self.crs not in (None, "EPSG:4326"):
            raise ValueError(
                f"source '{self.id}': arcgis_rest is always fetched as EPSG:4326; "
                f"remove 'crs: {self.crs}' or set it to EPSG:4326"
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


class SpatialJoin(StepBase):
    type: Literal["spatial_join"] = "spatial_join"
    source: str = Field(..., description="id of the source to join against")
    predicate: Literal[tuple(JOIN_PREDICATES)] = "intersects"  # type: ignore[valid-type]
    match: Literal["first", "all"] = Field(
        "first",
        description="'first': keep only the first match (current behaviour). "
        "'all': one output row per match, base row repeated for each",
    )
    on_multiple: Literal["first", "largest_overlap"] = Field(
        "first",
        description="Only applies when match='first'. 'first': deterministic tie-break "
        "on the join source's original row order. 'largest_overlap': pick the match with "
        "the largest ST_Intersection area (ties broken by original row order).",
    )
    fields: dict[str, str] = Field(
        default_factory=dict,
        description="{output_name: source_column} fields to pull from the match",
    )


class AttributeJoin(StepBase):
    type: Literal["attribute_join"] = "attribute_join"
    source: str
    left: str = Field(..., description="SQL expression on the base row (or a literal)")
    right: str = Field(..., description="Column on the joined source to match on")
    fields: dict[str, str] = Field(default_factory=dict)


class NearestNeighbor(StepBase):
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


class Buffer(StepBase):
    type: Literal["buffer"] = "buffer"
    distance: float = Field(..., description="Buffer distance in working CRS units")


class Centroid(StepBase):
    type: Literal["centroid"] = "centroid"


class Clip(StepBase):
    type: Literal["clip"] = "clip"
    source: str = Field(..., description="id of the mask source")
    predicate: Literal[tuple(JOIN_PREDICATES)] = "intersects"  # type: ignore[valid-type]


class Erase(StepBase):
    type: Literal["erase"] = "erase"
    source: str = Field(..., description="id of the eraser source")
    predicate: Literal[tuple(JOIN_PREDICATES)] = "intersects"  # type: ignore[valid-type]


class Dissolve(StepBase):
    type: Literal["dissolve"] = "dissolve"
    by: list[str] = Field(default_factory=list, description="Columns to group by; empty = dissolve all")


class IntersectOverlay(StepBase):
    type: Literal["intersect_overlay"] = "intersect_overlay"
    source: str = Field(..., description="id of the overlay source")
    fields: dict[str, str] = Field(default_factory=dict)


class Filter(StepBase):
    type: Literal["filter"] = "filter"
    where: str = Field(
        ..., description="SQL boolean expression; rows where this is false are dropped"
    )


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


Step = Annotated[
    Union[
        SpatialJoin, AttributeJoin, NearestNeighbor, Buffer, Centroid, Clip, Erase,
        Dissolve, IntersectOverlay, Filter, Merge, Snapshot,
    ],
    Field(discriminator='type'),
]


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


class OutputLayer(BaseModel):
    layer: str
    crs: CRSStr = "EPSG:25833"
    mapping: list[MapItem] = Field(default_factory=list)
    filter: Optional[str] = None


class Output(BaseModel):
    path: str
    overwrite: bool = True
    layers: list[OutputLayer] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalise_layers(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        if "layer" in data:
            layer_data: dict = {"layer": data.pop("layer")}
            if "crs" in data:
                layer_data["crs"] = data.pop("crs")
            if "mapping" in data:
                layer_data["mapping"] = data.pop("mapping")
            if "filter" in data:
                layer_data["filter"] = data.pop("filter")
            data["layers"] = [layer_data]
        return data

    @model_validator(mode="after")
    def _check_layers(self):
        if not self.layers:
            raise ValueError("at least one layer must be defined under 'layers'")
        return self


class Pipeline(BaseModel):
    name: str
    description: str = ""
    working_crs: OptionalCRSStr = Field(
        None, description="Internal CRS for joins; defaults to the base source CRS"
    )
    sources: list[Source]
    derived_sources: list[DerivedSource] = Field(default_factory=list)
    base: str
    steps: list[Step] = Field(default_factory=list)
    mapping: list[MapItem]
    # Accept either singular `output` (legacy) or plural `outputs`. The
    # validator below normalises both into `outputs`.
    output: Optional[Output] = Field(None, exclude=True)
    outputs: list[Output] = Field(default_factory=list)

    @model_validator(mode="after")
    def _normalise_outputs(self):
        if self.output is not None and self.outputs:
            raise ValueError("specify either 'output' or 'outputs', not both")
        if self.output is not None:
            object.__setattr__(self, "outputs", [self.output])
            object.__setattr__(self, "output", None)
        if not self.outputs:
            raise ValueError("at least one output must be defined under 'outputs'")
        return self

    @model_validator(mode="after")
    def _check_refs(self):
        ids = {s.id for s in self.sources}
        if self.base and self.base not in ids:
            raise ValueError(f"base '{self.base}' is not among sources {sorted(ids)}")
        for ds in self.derived_sources:
            if ds.from_ not in ids:
                raise ValueError(f"derived_source '{ds.id}' references unknown source '{ds.from_}'")
            ids.add(ds.id)
        # Snapshots only become referenceable by steps *after* them (as a
        # source: or a branch:), so branches grows as we walk the list in order.
        branches: set[str] = set()
        for st in self.steps:
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
        return self

    def source(self, sid: str) -> Source:
        return next(s for s in self.sources if s.id == sid)

    @property
    def base_source(self) -> Source:
        return self.source(self.base)

    @property
    def effective_working_crs(self) -> str:
        return self.working_crs or self.base_source.crs or self.outputs[0].layers[0].crs


def load_pipeline(path: str | Path) -> Pipeline:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Pipeline.model_validate(data)


def load_pipeline_dict(data: dict) -> Pipeline:
    return Pipeline.model_validate(data)


def dump_pipeline_yaml(pipeline: Pipeline) -> str:
    data = pipeline.model_dump(by_alias=True, exclude_none=True)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


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
        if "layer" in data:
            layer_data: dict = {"layer": data.pop("layer")}
            if "crs" in data:
                layer_data["crs"] = data.pop("crs")
            if "filter" in data:
                layer_data["filter"] = data.pop("filter")
            data.setdefault("layers", [layer_data])
        return data

    @model_validator(mode="after")
    def _check_layers(self):
        if not self.layers:
            raise ValueError("at least one layer must be defined under 'layers'")
        return self

    @model_validator(mode="after")
    def _check_refs(self):
        ids = {s.id for s in self.sources}
        if self.base and self.base not in ids:
            raise ValueError(f"base '{self.base}' is not among sources {sorted(ids)}")
        for ds in self.derived_sources:
            if ds.from_ not in ids:
                raise ValueError(f"derived_source '{ds.id}' references unknown source '{ds.from_}'")
            ids.add(ds.id)
        # Snapshots only become referenceable by steps *after* them (as a
        # source: or a branch:), so branches grows as we walk the list in order.
        branches: set[str] = set()
        for st in self.steps:
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
        return self

    def to_pipeline(self, output_path: str) -> Pipeline:
        """Convert to a single-output Pipeline for the existing engine."""
        return Pipeline(
            name=self.name,
            description=self.description,
            working_crs=self.working_crs,
            sources=self.sources,
            derived_sources=self.derived_sources,
            base=self.base,
            steps=self.steps,
            mapping=self.mapping,
            outputs=[Output(path=output_path, overwrite=False, layers=self.layers)],
        )


class DatasetMetadata(BaseModel):
    """Dataset-level metadata for the GeoPackage output."""

    name: Optional[str] = None
    abstract: Optional[str] = None
    origin: Optional[str] = None
    update_frequency: Optional[str] = None
    geometric_quality: Optional[str] = None
    attribute_quality: Optional[str] = None
    access_method_source: Optional[str] = None
    gdpr: Optional[str] = None


class Config(BaseModel):
    """Top-level config: one shared output GeoPackage + one or more pipelines."""

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
        return self


def _pipeline_to_config(p: Pipeline) -> Config:
    """Upgrade a legacy single-pipeline YAML to Config format."""
    out = p.outputs[0]
    pdef = PipelineDef(
        name=p.name,
        description=p.description,
        working_crs=p.working_crs,
        sources=p.sources,
        derived_sources=p.derived_sources,
        base=p.base,
        steps=p.steps,
        mapping=p.mapping,
        layers=out.layers,
    )
    return Config(
        name=p.name,
        description=p.description,
        output=out.path,
        overwrite=out.overwrite,
        pipelines=[pdef],
    )


def load_config(path: str | Path) -> Config:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return load_config_dict(data)


def load_config_dict(data: dict) -> Config:
    if "pipelines" in data:
        return Config.model_validate(data)
    # Legacy single-pipeline format — auto-upgrade
    return _pipeline_to_config(Pipeline.model_validate(data))


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
