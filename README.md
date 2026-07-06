# Duck Soup <img src="duck_soup/web/static/favicon.png" width="32" height="32" align="absmiddle">

[![DuckDB](https://img.shields.io/badge/Powered%20by-DuckDB-orange.svg)](https://duckdb.org/)
[![GeoPackage](https://img.shields.io/badge/Output-GeoPackage-blue.svg)](https://www.geopackage.org/)

**Duck Soup** makes config-driven geodata ETL on **DuckDB** as easy as... well, duck soup!

It serves as a lightweight, lightning-fast replacement for building heavy, complex workspaces in tools like FME (Safe Software Feature Manipulation Engine) or writing custom, error-prone Python scripts. You describe your dataset as a simple YAML file (sources → base → join steps → attribute mapping → output), or build it visually using the interactive web editor.

Under the hood, everything runs inside DuckDB using the powerful **spatial** extension. It compiles your entire pipeline of spatial joins, attribute joins, nearest-neighbor searches, and field mappings into a single, high-performance SQL query that streams directly to an output **GeoPackage**.

---

## Key Features

- **YAML-driven pipelines:** Describe inputs, join steps, schema mapping, and output layers in one neat configuration file.
- **Web Editor:** A visual, browser-based pipeline builder with live YAML preview, syntax validation, data previewing, and interactive execution logs.
- **Powered by DuckDB Spatial:** Blistering speed using DuckDB's columnar execution engine and GDAL-backed `ST_Read`/`ST_Write` operations.
- **Flexible Joins:** Supports spatial joins (intersects, contains, within), traditional attribute joins, and nearest-neighbor (distance-constrained) searches out of the box.
- **Rich Attribute Mapping:** Translate, rename, compute coordinates, generate UUIDs/timestamps, or apply CSV-based codelist lookups on the fly.

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

Each source is read once and its geometry reprojected to a single **working CRS**.
The engine then builds a chain of SQL views — `base → step_1 → step_2 → … → mapped`
— and DuckDB plans the whole thing as one query, so joins and projections stream
and stay fast. The final view is written straight to GeoPackage via
`COPY … (FORMAT GDAL, DRIVER 'GPKG')`. lon/lat and MGRS are always derived from a
single `EPSG:4326` transform of the geometry.

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

The editor lets you add sources, pick the base, build join steps, define the output
mapping, and Validate / Save / Run — with a live YAML preview and run log.

## Pipeline YAML

```yaml
name: embassies
working_crs: EPSG:25833        # CRS used for joins; defaults to base source CRS

sources:
  - id: ambassader             # unique handle
    format: gpkg               # gpkg|geojson|gml|fgdb|shp|wfs|arcgis_rest|parquet|xlsx|csv
    uri: data/Ambassader.gpkg  # path, .gdb folder, or service URL
    layer: Ambassader          # layer / WFS typename / sheet name
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
    geometry: false            # tabular source

base: ambassader               # features flow from here

steps:                         # ordered; each adds columns to the base row
  - type: spatial_join
    source: postnummer
    predicate: intersects      # intersects|contains|within
    on_multiple: first         # first|largest_overlap (see "Spatial join match resolution" below)
    fields: { s_postnummer: postnummer, s_poststed: poststed }
  - type: attribute_join
    source: dgif
    left: "'Embassies'"        # SQL expression / literal evaluated on the base row
    right: dataset             # column on the joined source
    fields: { dgifCCode: dgifCCode }

mapping:                       # ordered output columns; one of from/const/expr/func
  - { to: name,           from: "name:en" }
  - { to: type,           const: "Embassy" }
  - { to: postalCode,     from: "s_postnummer", cast: INTEGER }
  - { to: longitude,      func: lon }      # ST_X of EPSG:4326 geometry
  - { to: latitude,       func: lat }      # ST_Y of EPSG:4326 geometry
  - { to: mgrs,           func: mgrs }     # via python mgrs lib
  - { to: updateDate,     func: today }    # also: now, uuid
  - { to: area_label,     expr: "upper(s_poststed)" }   # raw SQL on the row

output:
  path: output/Embassies.gpkg
  layer: Embassies
  crs: EPSG:25833
```

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
| `func`     | `lon`, `lat`, `mgrs`, `uuid`, `now`, `today`                     |
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

## Not yet (first-draft scope)

- One base + first-match joins (1:1) by default. `spatial_join` with `match: first`
  keeps only a single joined feature per base row (see "Spatial join match resolution"
  above for how it's chosen) — if a base feature overlaps more than one join-source
  feature, the others are silently dropped, not merged or reported. For example, a point
  sitting exactly on the boundary between two postal-code polygons will only pick up one
  polygon's `postnummer`/`poststed` values; the other polygon's data never appears in the
  output. If that matters for your data, either clean up overlapping join-source polygons
  upstream, or use `match: all` instead (one output row per match, base row repeated) and
  deduplicate downstream. Many-to-many fan-out and unioning multiple bases are the
  obvious next steps.
- `func` set is small on purpose; add new ones in `engine.py:_func_expr` (SQL) or
  `derive.py` (python UDF).
- The editor builds/validates/saves and runs synchronously; for very large jobs
  you'd want a job queue and streamed logs.
