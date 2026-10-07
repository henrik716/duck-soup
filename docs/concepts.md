# Core concepts

A duck soup pipeline answers five questions, always in the same order:

1. **Sources**: where does the data come from?
2. **Base**: which source's features become the output rows?
3. **Steps**: how do those rows get enriched, filtered or reshaped?
4. **Mapping**: which columns come out, named what?
5. **Layers**: where is the result written, in which CRS?

```mermaid
flowchart LR
    S1[(source A)] --> B
    S2[(source B)] -.join.-> ST
    S3[(source C)] -.join.-> ST
    B[base<br/>= source A] --> ST[steps<br/>1 → 2 → 3]
    ST --> M[mapping]
    M --> L1[layer 1]
    M --> L2[layer 2<br/>filtered]
    L1 --> G[(output.gpkg<br/>or .parquet)]
    L2 --> G
```

## Config, pipelines and the shared output

A YAML file is a **config**. It names one output (a **GeoPackage**, or **GeoParquet** when the
path ends in `.parquet`) and holds one or more
**pipelines**:

```yaml
name: transport
output: output/transport.gpkg   # every pipeline writes into this one file
overwrite: true                 # delete the file before writing (default)
pipelines:
  - name: stations
    # sources / base / steps / mapping / layers …
  - name: bridges
    # … a completely independent chain
```

Pipelines don't share anything except the output file. Each has its own sources, base, steps
and mapping, and writes its own layers. Use several pipelines when you want several unrelated
layers in one deliverable.

## Sources

A source is a dataset with an `id` you refer to elsewhere. It has a `format`, a `uri` (file
path, folder, URL or connection string) and usually a `layer` and `crs`:

```yaml
sources:
  - id: roads
    format: gpkg
    uri: data/roads.gpkg
    layer: roads
    crs: EPSG:25833
```

Every source is read once. Invalid geometry is repaired on load (`make_valid`, on by default)
and reprojected into the pipeline's working CRS. Tabular sources (CSV, Excel, or anything with
`geometry: false`) can still be used for attribute joins and codelists. See
[Source formats](reference/sources.md) for every format.

## The base

`base` names the source whose features flow through the pipeline. **One base feature in = one
output row out**, unless a step deliberately changes the row count (a `filter`, a `merge`, a
`dissolve`, a `spatial_join` with `match: all`, an `intersect_overlay`).

Every other source is reference data. Steps join it onto the base rows, but it never becomes
output rows on its own.

## Steps

Steps run in order. Each step takes the rows produced so far and returns a new set of rows:

- **Joins** add columns: `spatial_join`, `attribute_join`, `nearest_neighbor`, `intersect_overlay`.
- **Geoprocessing** changes the geometry: `buffer`, `centroid`, `clip`, `erase`, `dissolve`.
- **Row operations** change which rows exist: `filter`, `merge`.
- **`snapshot`** names the current state so later steps can refer back to it.

Each join step pulls specific columns from the joined source through `fields`, written as
`{new_name: source_column}`. Only those columns are added.

See [Step types](reference/steps.md) for the full list.

## Mapping

The mapping is the output schema: an ordered list of output columns, each filled from exactly
one of these:

| kind | example | meaning |
|---|---|---|
| `from` | `{to: name, from: navn}` | copy a column |
| `const` | `{to: source, const: "NVDB"}` | a fixed value |
| `expr` | `{to: label, expr: "upper(navn)"}` | any DuckDB SQL expression |
| `func` | `{to: id, func: uuid}` | a built-in: `uuid`, `now`, `today`, `lon`, `lat`, `mgrs`, `wkb`, `area`, `length` |
| `codelist` | see [Codelists](reference/mapping.md#codelists) | translate codes via rules or a CSV |

Any of them can add `cast: INTEGER` (or another SQL type). The geometry column is always
written automatically as `geom`, so don't map it yourself. **With no mapping at all, every
column is written as-is.**

## Layers

A pipeline writes one or more **layers** into the shared output. Every layer gets the same
rows, and each layer can:

- reproject to its own `crs`,
- keep only some rows with a `filter` (a SQL condition),
- override the pipeline mapping with its own `mapping`.

```yaml
layers:
  - {layer: stations,          crs: EPSG:25833}
  - {layer: stations_unmatched, crs: EPSG:25833, filter: "county IS NULL"}
```

## The working CRS

All joins and geoprocessing happen in one CRS, the **working CRS**. Every source is
reprojected into it when read, and each layer is reprojected out of it when written.

- If you don't set `working_crs`, it defaults to the base source's CRS.
- Buffer distances, `max_distance`, `distance_field`, and the `area`/`length` functions are all
  in working-CRS units. duck soup **refuses to run** these in a geographic (degree) CRS such
  as EPSG:4326, because "buffer 500" would mean 500 degrees. Pick a projected CRS in metres
  (for example EPSG:25833 for Norway).
- `lon`, `lat` and `mgrs` are always computed in EPSG:4326 from the centroid, whatever the
  working CRS is.

## How it runs

Under the hood, a pipeline compiles to a chain of DuckDB SQL views:

```mermaid
flowchart LR
    A["src_places<br/>(read + reproject)"] --> S0[step_0<br/>= base]
    B["src_fylke"] --> S1
    S0 --> S1[step_1<br/>spatial_join]
    S1 --> S2[step_2<br/>filter]
    S2 --> M[mapped<br/>mapping applied]
    M --> W["COPY … TO output.gpkg / .parquet"]
```

DuckDB plans the whole chain as one query, so data streams through without intermediate
files. Spatial joins use DuckDB's R-tree spatial join, which keeps large point-in-polygon
joins fast.

## Branches and derived sources

A pipeline isn't strictly linear. There are two ways to fork it.

**Derived sources** are filtered or buffered views of a source, which you can join against
like any other source:

```yaml
derived_sources:
  - id: big_lakes
    from: lakes
    where: "area_km2 > 10"
    buffer: 100            # optional, working-CRS units
```

**Snapshots** name the chain's state partway through, so a later step can join against
the processed rows (`source: my_snapshot`). Steps can also keep working on that branch
(`branch: my_snapshot`) while the main chain carries on unchanged. Output layers are always
written from the **main** chain, so a branch is only useful as the `source:` of a later step.

This example finds, for every hospital, the other hospitals within 5 km:

```yaml
base: hospitals
steps:
  - {type: snapshot, id: zones}                        # copy the current rows to a branch
  - {type: buffer, distance: 5000, branch: zones}      # buffer the copy, not the main chain
  - type: spatial_join                                 # main chain: still hospital points
    source: zones
    predicate: within
    match: all                                         # one row per zone the hospital is in
    fields: {nearby_hospital: name}                    # (each hospital also matches its own zone)
```
