# Coming from FME

duck soup covers the most common kind of FME workspace: read a few datasets, join and
geoprocess them, reshape the attributes to a target schema, and write the result. This page
maps the FME ideas and transformers you already know to their duck soup equivalents.

## How the model differs

| FME | duck soup |
|---|---|
| A workspace (`.fmw`) | A config (a YAML file), built in the [editor](../editor/overview.md) or by hand |
| Readers | [Sources](../reference/sources.md) |
| Writers | The config's `output` file and each pipeline's [output layers](../editor/output-layers.md) |
| A canvas where features flow along many connections | A **pipeline**: one [base](../concepts.md#the-base) source flowing through an ordered list of steps |
| Several unrelated flows in one workspace | Several pipelines in one config, writing into the same output |
| Routing features to different writers with a Tester | One [layer filter](../editor/output-layers.md) per output layer |
| A side branch that feeds back in later | A [snapshot branch](../concepts.md#branches-and-derived-sources) |
| Inspector / feature caching | The editor's live [preview](../editor/preview-and-run.md), on every step |
| Feature counts on the connections | **count rows** in the [pipeline flow](../editor/overview.md#row-counts), on a sample or all the data |
| Passed / Failed (Unmatched, Outside…) output ports | [`rejects`](../reference/steps.md#rejects) on joins, `clip` and `filter`: the failed rows go to a layer of their own |
| The canvas, and a transformer's parameters opened from it | The [pipeline flow](../editor/overview.md#ducks-in-a-row-the-pipeline-flow) ("ducks in a row"): click a node and its card opens in a panel beside it |
| Coordinate system on each reader and writer | One [working CRS](../concepts.md#the-working-crs) for the processing, plus a CRS per source and per layer |

Every row in a duck soup pipeline starts as one feature of the base source. Other sources are
reference data that steps join against, so a "merge these two readers" flow becomes a
[`merge`](../reference/steps.md#merge) step, and a "match readers A and B" flow becomes a join
with A as the base.

## Transformers

### Joins and overlays

| FME transformer | duck soup |
|---|---|
| PointOnAreaOverlayer, SpatialRelator | [`spatial_join`](../reference/steps.md#spatial_join) (`predicate`: intersects / contains / within) |
| SpatialFilter | `spatial_join` with [`rejects`](../reference/steps.md#rejects): matches go on as *Passed*, the rest to the rejects layer as *Failed* |
| FeatureMerger, DatabaseJoiner, Joiner | [`attribute_join`](../reference/steps.md#attribute_join) |
| NeighborFinder | [`nearest_neighbor`](../reference/steps.md#nearest_neighbor) |
| AreaOnAreaOverlayer, LineOnAreaOverlayer | [`intersect_overlay`](../reference/steps.md#intersect_overlay) |
| LineOnLineOverlayer | [`line_overlay`](../reference/steps.md#line_overlay), one step per overlay dataset |

A spatial join keeps base features that match nothing, like a FeatureMerger's *unmerged*
output joined back in. To route them to their own layer instead, like the *Unmerged* port,
set `rejects:` on the join. To drop them, follow the join with a `filter` step.

### Geometry

| FME transformer | duck soup |
|---|---|
| Bufferer | [`buffer`](../reference/steps.md#buffer) step, or a [derived source](../concepts.md#branches-and-derived-sources) with `buffer:` |
| CenterPointReplacer | [`centroid`](../reference/steps.md#centroid) |
| Clipper (*inside* output) | [`clip`](../reference/steps.md#clip); its `rejects` layer is the features entirely *outside* |
| Clipper (*outside* output) | [`erase`](../reference/steps.md#erase) |
| Dissolver, Aggregator | [`dissolve`](../reference/steps.md#dissolve) |
| GeometryValidator (repair) | `make_valid`, on by default for every source |
| 2DForcer | [`force_2d: true`](../reference/sources.md#3d-geometry) on the source |
| Reprojector | Automatic: sources are reprojected into the working CRS, and layers into their own `crs` |

### Rows

| FME transformer | duck soup |
|---|---|
| Tester, TestFilter | [`filter`](../reference/steps.md#filter) step (drops rows, or with `rejects` writes them to a *Failed* layer), or a layer `filter` (splits rows across layers) |
| Several readers into one stream | [`merge`](../reference/steps.md#merge) |

### Attributes

Attribute work happens in the [mapping](../reference/mapping.md), which lists every output
column in order. Anything not mapped isn't written, which replaces most AttributeKeeper and
AttributeRemover use.

| FME transformer | duck soup mapping |
|---|---|
| AttributeRenamer, AttributeKeeper | `{to: new_name, from: old_name}` |
| AttributeCreator (fixed value) | `{to: source, const: "Survey 2026"}` |
| AttributeManager, ExpressionEvaluator, StringConcatenator, StringCaseChanger, StringReplacer | `expr` with a [DuckDB SQL expression](../tutorials/mapping.md#expr-a-sql-expression) |
| AttributeValueMapper, ValueMapper | [`codelist`](../reference/mapping.md#codelists), with rules or a CSV lookup |
| AttributeTypeConverter | `cast` |
| UUIDGenerator | `func: uuid` |
| TimeStamper, DateTimeStamper | `func: now` / `func: today` |
| CoordinateExtractor (of a centre point) | `func: lon` / `func: lat`, or `func: mgrs` |
| AreaCalculator, LengthCalculator | `func: area` / `func: length` |
| GeometryExtractor (WKB) | `func: wkb` |

Different attribute sets for different writers become a
[per-layer mapping](../editor/output-layers.md#per-layer-mapping).

## Running and scheduling

| FME | duck soup |
|---|---|
| Running from FME Workbench | **run** in the editor, or `duck-soup run config.yaml` |
| `fme.exe workspace.fmw` in a batch file | `duck-soup run config.yaml`. See [Command line](../reference/cli.md). |
| FME Flow (Server) schedules | cron, systemd, Task Scheduler, Docker or CI. See [Scheduling runs](../scheduling.md). |
| FME Flow job history | The editor's [History tab](../editor/preview-and-run.md#run-history), which lists scheduled runs too |
| Viewing what a transformer does under the hood | The editor's [SQL tab](../editor/preview-and-run.md#sql-tab) |

## What duck soup doesn't do

- **Output formats**: only GeoPackage and GeoParquet. There are no writers for Shapefile,
  databases or web services.
- **Published parameters**: the YAML has no variables or parameters. Keep one config per
  variant, or generate the YAML with a script.
- **PythonCaller and custom transformers**: there's no scripting hook. Most attribute logic
  fits in a DuckDB SQL `expr`.
- **Feature-by-feature control flow**: loops, looping custom transformers and
  feature-level routing beyond filters aren't available. A pipeline is a fixed chain of steps.
- **Raster and topology tools**: duck soup works with vector features and tables.

See also [Known limitations](../troubleshooting.md#known-limitations).
