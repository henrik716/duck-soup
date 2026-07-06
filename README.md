# Duck Soup <img src="duck_soup/web/static/favicon.png" width="32" height="32" align="absmiddle">

[![DuckDB](https://img.shields.io/badge/Powered%20by-DuckDB-orange.svg)](https://duckdb.org/)
[![GeoPackage](https://img.shields.io/badge/Output-GeoPackage-blue.svg)](https://www.geopackage.org/)

**Duck Soup** makes config-driven geodata ETL on **DuckDB** as easy as... well, duck soup!

It serves as a lightweight, lightning-fast replacement for building heavy, complex workspaces in tools like FME (Safe Software Feature Manipulation Engine) or writing custom, error-prone Python scripts. You describe your dataset as a simple YAML file (sources → base → join/geoprocessing steps → attribute mapping → one or more output layers), or build it visually using the interactive web editor.

Under the hood, everything runs inside DuckDB using the powerful **spatial** extension. It compiles your pipeline of spatial joins, attribute joins, nearest-neighbor searches, buffers, clips, overlays, dissolves, and field mappings into a chain of SQL views that DuckDB streams straight to an output **GeoPackage**. A single YAML file can define several independent pipelines that each fan out to multiple layers, all written into one shared GeoPackage.

---

## Key Features

- **YAML-driven pipelines:** Describe inputs, join/geoprocessing steps, schema mapping, and output layers in one neat configuration file.
- **Multi-pipeline / multi-layer output:** One file can define several independent pipelines, each writing one or more layers, all into a single shared GeoPackage with optional dataset-level metadata.
- **Web Editor:** A visual, browser-based pipeline builder with live YAML preview, syntax validation, data previewing, and interactive execution logs.
- **Powered by DuckDB Spatial:** Blistering speed using DuckDB's columnar execution engine and GDAL-backed `ST_Read`/`ST_Write` operations.
- **Flexible Joins:** Spatial joins (intersects/contains/within, first-match or fan-out-all), attribute joins, and nearest-neighbor (distance-constrained) searches.
- **Geoprocessing steps:** Buffer, centroid, clip, erase, dissolve, intersect overlay, filter, and merge (union) — chainable like any join step, and forkable into named branches via `snapshot`.
- **Derived sources:** Build a filtered/buffered view of any source and reuse it as a join source, without a dedicated step.
- **Rich Attribute Mapping:** Translate, rename, compute coordinates/area/length, generate UUIDs/timestamps, or apply rule-based/CSV-based codelist lookups on the fly.

---

```
duck_soup/           # Python package directory
  config.py          # YAML schema (pydantic) + load/save
  sources.py         # one reader per format (mostly DuckDB ST_Read / GDAL)
  derive.py          # python UDFs (MGRS) registered into DuckDB
  engine.py          # builds a chain of SQL views, writes GeoPackage
  cli.py             # python -m duck_soup.cli run pipelines/test.yaml
  web/
    app.py           # FastAPI backend
    static/          # compiled editor frontend assets served by FastAPI
    ui/              # TypeScript + Vite editor frontend source
pipelines/           # your dataset definitions (*.yaml)
data/  output/       # inputs / outputs
```

## How it works

Each source is read once into a `src_<id>` view, with its geometry reprojected to a
single **working CRS**. The engine then walks the pipeline's `steps` in order, each
one creating the next `step_N` view on top of the last (`step_0` is the `base`
source). A `snapshot` step can fork a named branch off the chain at that point — a
later step's `source:` can target a branch, or a `derived_source` can wrap an
existing source with a filter/buffer — so steps don't have to run strictly linearly.
DuckDB plans each layer's view chain as one query, so joins, geoprocessing, and
projections stream and stay fast.

Once the chain is built, each output layer applies its own attribute `mapping` (and
optional `filter`) and is written straight to GeoPackage via
`COPY … (FORMAT GDAL, DRIVER 'GPKG')`. A pipeline can write several layers this way,
and a Config file can run several pipelines, all appended into the same GeoPackage.
lon/lat/mgrs/wkb/area/length are always derived from the same working-CRS geometry
used for joins — no extra reprojection.

Almost every format goes through DuckDB's `ST_Read` (which uses GDAL), so adding a
format is usually one branch in `sources.py`. **ArcGIS REST** is the exception: it's
paged to a temporary GeoJSON file first, then read like any other file.

## Prerequisites

Before setting up the project, make sure you have the following installed:

1. **Python**: Python `3.11` or newer.
2. **Node.js & npm** (optional): Only needed if you plan to modify or rebuild the frontend editor. Node.js `18+` is recommended.
3. **Internet Access**: Required on the first run so DuckDB can automatically download and install its `spatial` extension.

Tested with DuckDB `1.5.4` (spatial extension core build `28db190`). `pyproject.toml`
pins `duckdb` to `>=1.5.4,<1.6` because `INSTALL spatial` always fetches the extension
build matching the running DuckDB core version — pinning DuckDB is what keeps the
spatial extension version reproducible. Bump the pin (and this note) together when
upgrading.

## Install

Create a virtual environment, activate it, and install the package with dependencies:

```bash
python -m venv .venv

# Activate on Windows:
.venv\Scripts\activate
# Activate on macOS/Linux:
source .venv/bin/activate

pip install -e .           # installs duck_soup + all required deps
# optional: pip install -e ".[mgrs]"   adds MGRS grid conversion support
```

DuckDB downloads its **spatial** extension on first run (needs internet once, or
pre-install it offline). The `mgrs` package is optional — without it, `func: mgrs`
yields NULL instead of failing.

## Quickstart

Run the self-contained test pipeline (no external data or services needed):

```bash
python -m duck_soup.cli check pipelines/test.yaml   # validate
python -m duck_soup.cli run   pipelines/test.yaml   # writes output/test.gpkg
```

## Running the Application Locally

### CLI

Run commands in your virtual environment:

```bash
python -m duck_soup.cli check pipelines/test.yaml   # validate only
python -m duck_soup.cli run   pipelines/test.yaml   # build the GeoPackage
```

### Editor (Web App)

The editor consists of a FastAPI backend and a TypeScript + Vite frontend.

#### Option 1: Quick Start (Pre-built Frontend)

If you just want to run the app using the pre-compiled static files, simply start the FastAPI backend:

```bash
uvicorn duck_soup.web.app:app --reload
```
Then open http://127.0.0.1:8000 in your browser.

#### Option 2: Development Setup (Modifying Frontend)

If you want to modify or develop the editor's frontend, run both the backend and frontend dev server (with hot reloading):

1. **Start the FastAPI backend** (runs on port 8000):
   ```bash
   uvicorn duck_soup.web.app:app --reload
   ```

2. **Start the Vite dev server** (runs on port 5173 and proxies `/api` to port 8000):
   ```bash
   cd duck_soup/web/ui
   npm install
   npm run dev
   ```
   Then open http://localhost:5173 in your browser.

#### Option 3: Building the Frontend

If you have made frontend changes in `duck_soup/web/ui` and want to compile them to static files served by FastAPI:

```bash
cd duck_soup/web/ui
npm run build      # compiles TypeScript and copies assets to ../static/
```

The editor lets you add sources and derived sources, pick the base, build join and
geoprocessing steps, define one or more output layers and their mapping, add more
pipelines to the same file, and Validate / Save / Run — with a live YAML preview
and run log.

## Pipeline YAML

A file is a **Config**: one shared output GeoPackage, optional dataset metadata, and a
list of independent `pipelines`. Each pipeline has its own sources/base/steps/mapping
and can fan out to one or more output `layers`, all appended into the same GeoPackage:

```yaml
name: embassies
description: "Foreign missions in Norway"
output: output/Embassies.gpkg   # one shared GeoPackage for every pipeline below
overwrite: true
metadata:                       # optional GeoPackage-level dataset metadata
  abstract: "Embassy locations enriched with postal area and MGRS"
  gdpr: "No personal data"

pipelines:
  - name: embassies              # first (here, only) pipeline in the file
    working_crs: EPSG:25833      # CRS used for joins; defaults to base source CRS

    sources:
      - id: ambassader           # unique handle
        format: gpkg             # gpkg|geojson|gml|fgdb|shp|wfs|arcgis_rest|parquet|xlsx|csv
        uri: data/Ambassader.gpkg  # path, .gdb folder, or service URL
        layer: Ambassader        # layer / WFS typename / sheet name
        crs: EPSG:4326
      - id: postnummer
        format: wfs
        uri: https://wfs.geonorge.no/skwms1/wfs.postnummeromrader
        layer: Postnummeromrade
        crs: EPSG:25833
      - id: dgif
        format: xlsx
        uri: data/Mappingtabell_NGF-DGIF.xlsx
        layer: NGF-DGIF
        geometry: false          # tabular source

    derived_sources:             # optional: filtered/buffered view of a source, usable as `source:` below
      - id: postnummer_oslo
        from: postnummer
        where: "poststed = 'OSLO'"

    base: ambassader              # features flow from here
    steps:                        # ordered; each reshapes or adds columns to the row
      - type: spatial_join
        source: postnummer
        predicate: intersects     # intersects|contains|within
        on_multiple: first        # first|largest_overlap (see "Spatial join match resolution" below)
        fields: { s_postnummer: postnummer, s_poststed: poststed }
      - type: attribute_join
        source: dgif
        left: "'Embassies'"       # SQL expression / literal evaluated on the row
        right: dataset             # column on the joined source
        fields: { dgifCCode: dgifCCode }

    mapping:                      # ordered output columns; one of from/const/expr/func/codelist
      - { to: name,           from: "name:en" }
      - { to: type,           const: "Embassy" }
      - { to: postalCode,     from: "s_postnummer", cast: INTEGER }
      - { to: longitude,      func: lon }      # ST_X of EPSG:4326 geometry
      - { to: latitude,       func: lat }      # ST_Y of EPSG:4326 geometry
      - { to: mgrs,           func: mgrs }     # via python mgrs lib
      - { to: updateDate,     func: today }    # also: now, uuid, wkb, area, length
      - { to: area_label,     expr: "upper(s_poststed)" }   # raw SQL on the row

    layers:                       # one or more output layers from the same chain above
      - layer: Embassies
        crs: EPSG:25833
      - layer: EmbassiesOslo       # a second layer, filtered from the same pipeline
        crs: EPSG:25833
        filter: "s_poststed = 'OSLO'"
```

A single-layer pipeline can write `layer:`/`crs:`/`filter:` directly instead of a
`layers:` list (see `pipelines/test.yaml` for a minimal working example) — and a
single-pipeline file can skip the `pipelines:` wrapper entirely and write
`sources`/`base`/`steps`/`mapping`/`output` directly at the top level. Both are
legacy shorthands, auto-upgraded to the Config shape above on load.

### Step types

| `type`              | what it does |
|---------------------|--------------|
| `spatial_join`       | join by `predicate` (intersects/contains/within); `match: first` (default, one row per base feature) or `match: all` (fan out one row per match) |
| `attribute_join`     | 1:1 left join: `left` (SQL expression/literal on the row) = `right` (column on the joined source) |
| `nearest_neighbor`   | join the closest feature by distance; optional `max_distance` cap and `distance_field` output |
| `buffer`             | `ST_Buffer(geom, distance)` |
| `centroid`           | `ST_Centroid(geom)` |
| `clip`               | keep only the geometry intersecting a matching feature from `source` (`ST_Intersection`) |
| `erase`              | subtract the union of all matching `source` features from the geometry (`ST_Difference`) |
| `dissolve`           | `ST_Union_Agg(geom)` grouped `by` a list of columns (omit for one feature total) |
| `intersect_overlay`  | inner join + `ST_Intersection`, one output row per overlapping pair |
| `filter`             | drop rows where `where` (SQL boolean) is false |
| `merge`              | append another source's rows via `UNION ALL BY NAME` |
| `snapshot`           | name the chain's current state (`id:`) so a later step can join back against it or fork a branch |

Every step also accepts an optional `branch:` — the name of an earlier `snapshot` to
run against instead of the main chain, letting a pipeline maintain several parallel,
diverging chains that each keep evolving independently.

### Derived sources

A `derived_sources` entry wraps an existing `source` (or another derived source) with
a `where` filter and/or a `buffer`, registered under its own `id` — usable as
`source:` in any step exactly like a real source. Handy for joining against only a
subset of a large reference layer, or a buffered version of it, without adding a
dedicated step to the main chain.

### Spatial join match resolution

`spatial_join` with `match: first` (the default) keeps exactly one matching feature per
base row even when several join-source features satisfy the predicate. Which one is kept
is controlled by `on_multiple`:

| `on_multiple`     | meaning                                                                 |
|-------------------|--------------------------------------------------------------------------|
| `first` (default) | deterministic — picks the join-source feature with the lowest original row order (its position in the source file/table), not an arbitrary query-plan order |
| `largest_overlap` | picks the feature with the largest `ST_Intersection` area with the base geometry; ties fall back to original row order |

`match: all` is unaffected by `on_multiple` — it fans out one output row per match instead
of picking a single one.

### Mapping value kinds

| kind       | meaning                                                       |
|------------|----------------------------------------------------------------|
| `from`     | copy a column from the (joined) row                            |
| `const`    | a literal value                                                 |
| `expr`     | raw SQL expression evaluated against the row                    |
| `func`     | `lon`, `lat`, `mgrs`, `wkb`, `area`, `length`, `uuid`, `now`, `today` |
| `codelist` | translate a column's value via rules or a CSV lookup table      |
| `cast`     | optional; wraps the result in `TRY_CAST(… AS TYPE)`              |

### Codelists

For "if value LIKE potato then Vegetable" style remapping, use `codelist` instead
of hand-writing a `CASE WHEN` in `expr`. Rules are evaluated top-to-bottom, first
match wins:

```yaml
- to: category
  codelist:
    source: raw_name          # column being translated
    case_insensitive: true     # default true
    cases:
      - { match: "potato",  value: "Vegetable" }   # exact match
      - { like: "%apple%",  value: "Fruit" }        # SQL LIKE pattern
      - { regex: "^carro",  value: "Vegetable" }    # regex
    default: "Unknown"          # used when nothing matches (omit for NULL)
```

For large code tables (hundreds+ of codes), point at a CSV instead of listing
rules — this becomes a correlated lookup against `read_csv`, not a giant `CASE`:

```yaml
- to: nato_code
  codelist:
    source: raw_code
    file: codelists/dgif_codes.csv   # two (or more) columns
    file_match_col: code              # key column in the CSV
    file_value_col: label              # output column in the CSV
    default: "Unmapped"
```

If the lookup is itself a real table with several columns you need (not just a
single translated value), model it as an `attribute_join` step instead — that's
a proper join, not a per-row correlated subquery, and is the better fit for
things like the NGF→DGIF mapping table in the example above.

The editor's mapping rows support `codelist` directly: choosing it opens a panel
with a toggle between **rules** (match/like/regex → value, plus a default) and
**file lookup** (csv path + key/value columns).

## Known limitations

- **One `base` per pipeline.** Each `pipelines` entry starts from a single source's
  features. `merge` can append another source's rows mid-chain via `UNION ALL BY
  NAME`, but those rows only pass through the steps *after* the merge, not the ones
  before it — there's no way to run one identical step chain over two starting
  datasets at once. If you need that, write separate `pipelines` entries (each can
  write to its own layer in the same shared output).
- **`match: first` is the default and is easy to reach for by accident.** A base
  feature that overlaps more than one join-source feature — e.g. a point sitting
  exactly on the boundary between two postal-code polygons — silently keeps only
  one match's fields unless you deliberately opt into `match: all` (see "Spatial
  join match resolution" above).
- **Synchronous run.** `POST /api/run` blocks on the whole pipeline and returns
  once it's done — no job queue, cancellation, or streamed logs for long-running
  jobs. `check`/`run` in the CLI are likewise blocking, single-shot commands.
- **Small `func` set on purpose** (`lon`, `lat`, `mgrs`, `wkb`, `area`, `length`,
  `uuid`, `now`, `today`); add new ones in `engine.py:_func_expr` (SQL) or
  `derive.py` (python UDF).
