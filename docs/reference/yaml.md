# Pipeline YAML

This page describes the complete structure of a pipeline file. The schema is defined by the
Pydantic models in
[`duck_soup/config.py`](https://github.com/henrik716/duck-soup/blob/main/duck_soup/config.py).
`duck-soup check <file>` validates a file against it.

## Full example

```yaml
name: ducks
description: "Ranking every duck in the neighbourhood pond by sass level"
output: output/Ducks.gpkg       # one shared GeoPackage for every pipeline below (.parquet → GeoParquet)
overwrite: true
metadata:
  abstract: "Duck sightings enriched with pond gossip and species drama"
  gdpr: "No personal data (ducks were not available for consent)"

pipelines:
  - name: ducks
    working_crs: EPSG:25833

    sources:
      - {id: ducks, format: gpkg, uri: data/Ducks.gpkg, layer: Ducks, crs: EPSG:4326}
      - {id: ponds, format: geojson, uri: data/Ponds.geojson, crs: EPSG:25833}
      - {id: species_codes, format: csv, uri: data/species_codes.csv, geometry: false}

    derived_sources:
      - {id: ponds_chill, from: ponds, where: "vibe = 'chill'"}

    base: ducks
    steps:
      - type: spatial_join
        source: ponds
        predicate: intersects
        fields: {s_pond_id: pond_id, s_pond_name: name, s_pond_type: type}
      - type: attribute_join
        source: species_codes
        left: species_code
        right: code
        fields: {s_species_name: common_name}

    mapping:
      - {to: name,        from: nickname}
      - {to: species,     from: s_species_name}
      - {to: pondId,      from: s_pond_id, cast: INTEGER}
      - {to: longitude,   func: lon}
      - {to: latitude,    func: lat}
      - {to: spottedDate, func: today}
      - {to: pond_label,  expr: "upper(s_pond_name)"}

    layers:
      - {layer: Ducks, crs: EPSG:25833}
      - {layer: DucksChillPonds, crs: EPSG:25833, filter: "s_pond_type = 'no_drama'"}
```

## Top level (config)

| Key | Type | Default | Meaning |
|---|---|---|---|
| `name` | string | **required** | Config name. |
| `description` | string | `""` | Free text. |
| `output` | path | **required** | The file every pipeline writes into. `.gpkg` writes a GeoPackage; `.parquet` or `.geoparquet` writes GeoParquet (see [GeoParquet output](#geoparquet-output)). Folders are created as needed. |
| `overwrite` | bool | `true` | Delete the output file before writing. With `false`, layers are appended to an existing file. |
| `metadata` | object | — | Optional dataset metadata, see below. |
| `pipelines` | list | **required** | One or more pipelines, see below. |

### `metadata`

All fields are optional strings: `name`, `abstract`, `origin`, `update_frequency`,
`geometric_quality`, `attribute_quality`, `access_method_source`, `gdpr`.

They're stored in the config file, where the editor's **Dataset Metadata** section edits them.

## Pipeline

| Key | Type | Default | Meaning |
|---|---|---|---|
| `name` | string | **required** | Pipeline name. |
| `description` | string | `""` | Free text. |
| `working_crs` | CRS | base source CRS | CRS for all joins and geoprocessing. See [working CRS](../concepts.md#the-working-crs). |
| `sources` | list | **required** | See [Source formats](sources.md). |
| `derived_sources` | list | `[]` | See [below](#derived_sources). |
| `base` | source id | **required** | The source whose features become output rows. |
| `steps` | list | `[]` | See [Step types](steps.md). |
| `mapping` | list | **required** (may be `[]`) | See [Mapping](mapping.md). Empty = write every column. |
| `layers` | list | **required** | At least one output layer, see below. |

A CRS is always written as `AUTHORITY:CODE`, e.g. `EPSG:25833`.

### `derived_sources`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `id` | string | **required** | New id, usable as `source:` in any step. |
| `from` | source id | **required** | A source or an earlier derived source. |
| `where` | SQL | — | Keep only rows where this is true. |
| `buffer` | number | — | Buffer distance in working-CRS units. |
| `make_valid` | bool | `true` | Repair geometry after deriving. |

### `layers`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `layer` | string | **required** | Table name in the GeoPackage, or file name for a multi-layer GeoParquet output. |
| `crs` | CRS | `EPSG:25833` | Output CRS. |
| `filter` | SQL | — | Only rows where this is true go into this layer. Uses pre-mapping column names. |
| `mapping` | list | — | Replaces the pipeline `mapping` for this layer only. |

## Paths

`uri`, codelist `file` and `output` may be absolute or relative. Relative paths resolve against
the **current working directory** of the process: where you ran `duck-soup run`, where you
started the server, or `/data` in Docker. Your data doesn't need to live in the repository.

## GeoParquet output

An `output` path ending in `.parquet` or `.geoparquet` is written as
[GeoParquet](https://geoparquet.org/) instead of a GeoPackage:

```yaml
output: output/pond_survey.parquet
```

A GeoParquet file holds exactly one table, so the layout depends on how many layers the
config has **in total**, across all pipelines:

| Layers | Written to |
|---|---|
| 1 | `output/pond_survey.parquet` |
| 2 or more | `output/pond_survey/<layer>.parquet`, one file per layer |

- Each layer's `crs` is embedded in the file's GeoParquet metadata, so GIS tools read the
  coordinates in the right CRS.
- Layer names become file names, so they must be unique (ignoring case) and can't contain
  `< > : " / \ | ? *`.
- `overwrite: true` deletes the file, or the `*.parquet` files in the folder, before writing.
  Other files in the folder are left alone.
- Writing uses DuckDB's own Parquet writer (ZSTD-compressed), not GDAL.

## Validation rules

Besides types, `check` enforces:

- `base`, every step's `source`, and every derived source's `from` must name a known id.
- A `snapshot`'s `id` can't collide with a source id, and a step's `branch` must name a snapshot
  defined **earlier** in the list.
- Every mapping item has exactly one of `from` / `const` / `expr` / `func` / `codelist`.
- `geom` is reserved for the output geometry and can't be a mapping target.
- `arcgis_rest` and `oapif` sources must be EPSG:4326 (or leave `crs` out).
- For a GeoParquet `output`, layer names must be unique, valid file names.
- At runtime: buffer, nearest-neighbour distances and `area`/`length` require a projected
  working CRS.

## Shorthand forms

Two legacy shorthands are still accepted and upgraded automatically on load. The editor always
saves the full form.

**Single layer**: write `layer:` / `crs:` / `filter:` directly on the pipeline instead of a
`layers:` list:

```yaml
pipelines:
  - name: test
    # …
    layer: output
    crs: EPSG:25833
```

**Single pipeline**: leave out the `pipelines:` wrapper and put `sources` / `base` / `steps` /
`mapping` at the top level, with an `output:` (or `outputs:`) block holding the path and layer:

```yaml
name: my_pipeline
working_crs: EPSG:25833
sources: [ … ]
base: places
steps: [ … ]
mapping: [ … ]
output:
  path: output/result.gpkg
  layer: result_layer
  crs: EPSG:25833
```
