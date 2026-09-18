# <img src="duck_soup/web/static/favicon.png" width="32" height="32" align="absmiddle"> duck soup

[![DuckDB](https://img.shields.io/badge/Powered%20by-DuckDB-orange.svg)](https://duckdb.org/)
[![GeoPackage](https://img.shields.io/badge/Output-GeoPackage-blue.svg)](https://www.geopackage.org/)

**duck soup** makes config-driven geodata ETL on **DuckDB** as easy as... well, duck soup!

It's a lightweight, lightning-fast replacement for building heavy workspaces in tools like FME (Safe Software Feature Manipulation Engine), or writing custom, error-prone Python scripts. Describe your pipeline as a simple YAML file — sources, a base, join/geoprocessing steps, attribute mapping, one or more output layers — or build it visually in the interactive web editor.

Under the hood, everything runs inside DuckDB using the powerful **spatial** extension: joins, geoprocessing (buffers, clips, overlays, dissolves, ...), and field mappings all compile down to a chain of SQL views, written straight to an output **GeoPackage**. A single YAML file can define several independent pipelines, each fanning out to multiple layers, all written into one shared GeoPackage.

---

## Run it

No repo clone needed — pick one:

```bash
# Docker (no Python needed): mounts the current directory as /data,
# so pipelines/, data/, output/ etc. are read from and written to it
docker run -p 8000:8000 -v "$PWD":/data ghcr.io/henrik716/duck-soup

# or: pipx (needs Python 3.11+)
pipx install duck-soup-etl
duck-soup serve
```

Then open http://localhost:8000. Internet access is needed on first run so DuckDB
can download its `spatial` extension. See "Install" below for a from-source setup
(needed if you want to modify the code or frontend).

[pipx](https://pipx.pypa.io/) installs Python command-line apps (as opposed to
`pip`, which installs Python *libraries*, typically into a project's virtual
environment). It keeps each app in its own isolated environment and puts its
command on your `PATH` automatically, so `duck-soup serve` just works afterward
with no extra setup. If it's not already on your machine:

```bash
python -m pip install --user pipx
python -m pipx ensurepath
# then open a new terminal so the PATH change takes effect
```

If you use `pip install duck-soup-etl` instead and `duck-soup serve` isn't found
afterwards, it's a PATH issue, not a broken install: plain `pip` installs the
console script into a per-user (or venv) `Scripts`/`bin` directory that isn't
always on `PATH` by default (Windows per-user installs especially — `pipx` avoids
this entirely by managing PATH for you). Either add that directory to `PATH`
(`pip` prints its location as a warning when this happens), or sidestep it by
running the module directly, which always works regardless of `PATH`:

```bash
python -m duck_soup.cli serve
```

## Key Features

- **YAML-driven pipelines:** Describe inputs, join/geoprocessing steps, schema mapping, and output layers in one neat configuration file.
- **Multi-pipeline / multi-layer output:** One file can define several independent pipelines, each writing one or more layers, all into a single shared GeoPackage with optional dataset-level metadata.
- **Web Editor:** A visual, browser-based pipeline builder with live YAML preview, syntax validation, data previewing, and interactive execution logs. Drag a File Geodatabase folder straight onto the sources panel and it's uploaded and wired up as a source automatically.
- **Interactive map preview:** A MapLibre GL-powered map with a light/dark basemap toggle previews source and result geometry directly in the browser, with hover/click inspection of feature attributes.
- **Powered by DuckDB Spatial:** Blistering speed using DuckDB's columnar execution engine and GDAL-backed `ST_Read`/`ST_Write` operations.
- **Flexible Joins:** Spatial joins (intersects/contains/within, keeping the first match or fanning out to all), attribute joins, and nearest-neighbor (distance-constrained) searches.
- **Geoprocessing steps:** Buffer, centroid, clip, erase, dissolve, intersect overlay, filter, and merge (union) — chainable like any join step, with `snapshot` to fork the chain into named branches.
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
source). A `snapshot` step names the chain's current state so a later step can fork
off a named branch instead of following the main chain, and a `derived_source` wraps
an existing source with a filter/buffer for reuse as a join source — so a pipeline
isn't limited to one strictly linear sequence of steps. DuckDB plans each layer's
view chain as one query, so joins, geoprocessing, and projections stream and stay
fast.

Once the chain is built, each output layer applies its own attribute `mapping` (and
optional `filter`) and is written straight to GeoPackage via
`COPY … (FORMAT GDAL, DRIVER 'GPKG')`. A pipeline can write several layers this way,
and a Config file can run several pipelines, all appended into the same GeoPackage.
lon/lat/mgrs/wkb/area/length are always derived from the same working-CRS geometry
used for the joins, so there's no extra reprojection.

Most formats go through DuckDB's `ST_Read` (which uses GDAL), so adding a format is
usually one branch in `sources.py`. **ArcGIS REST** and **OGC API - Features (oapif)**
are paged over HTTP into a temporary GeoJSON file first, then read like any other file;
**WFS** is likewise fetched (GetFeature/GML) into a temporary `.gml` file first. **Parquet**
and **Postgres** skip GDAL entirely, using DuckDB's native `read_parquet()` and `postgres`
extension respectively. See "Source formats" below for the full picture.

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

## Install (from source)

Create a virtual environment, activate it, and install the package with dependencies:

```bash
python -m venv .venv

# Activate on Windows:
.venv\Scripts\activate
# Activate on macOS/Linux:
source .venv/bin/activate

pip install -e .           # installs duck_soup + all required deps
# optional: pip install -e ".[mgrs]"     adds MGRS grid conversion support
# optional: pip install -e ".[curves]"   adds automatic linearization of curve geometry
```

DuckDB downloads its **spatial** extension on first run (needs internet once, or
pre-install it offline). The `mgrs` package is optional — without it, `func: mgrs`
yields NULL instead of failing. The `pyogrio` package (`curves` extra) is also
optional — without it, sources whose geometry is stored as an ISO curve type
(CircularString, CompoundCurve, CurvePolygon, MultiCurve, MultiSurface — DuckDB's
spatial extension can't parse these, only plain OGC linear geometry) fail with a
clear error telling you to install it; with it, such sources are automatically
read and re-linearized into ordinary line/polygon geometry on the fly.

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

A pipeline file is a **Config**: one shared output GeoPackage, optional dataset
metadata, and a list of independent `pipelines`. Each pipeline has its own
sources/base/steps/mapping, and can fan out to one or more output `layers` that all
get appended into that same GeoPackage:

```yaml
name: ducks
description: "Ranking every duck in the neighbourhood pond by sass level"
output: output/Ducks.gpkg       # one shared GeoPackage for every pipeline below
overwrite: true
metadata:                       # optional GeoPackage-level dataset metadata
  abstract: "Duck sightings enriched with pond gossip and species drama"
  gdpr: "No personal data (ducks were not available for consent)"

pipelines:
  - name: ducks                  # first (here, only) pipeline in the file
    working_crs: EPSG:25833      # CRS used for joins; defaults to base source CRS

    sources:
      - id: ducks                # unique handle
        format: gpkg              # gpkg|geojson|gml|fgdb|shp|wfs|arcgis_rest|oapif|parquet|flatgeobuf|xlsx|csv|postgres
        uri: data/Ducks.gpkg      # path, .gdb folder, service URL, or postgres connection string
        layer: Ducks              # layer / WFS typename / sheet name
        crs: EPSG:4326
      - id: ponds
        format: geojson
        uri: data/Ponds.geojson
        layer: ponds
        crs: EPSG:25833
      - id: species_codes
        format: csv
        uri: data/species_codes.csv
        geometry: false          # tabular source

    derived_sources:             # optional: filtered/buffered view of a source, usable as `source:` below
      - id: ponds_chill
        from: ponds
        where: "vibe = 'chill'"  # only the drama-free ponds

    base: ducks                   # features flow from here
    steps:                        # ordered; each reshapes or adds columns to the row
      - type: spatial_join
        source: ponds
        predicate: intersects     # intersects|contains|within
        on_multiple: first        # first|largest_overlap (see "Spatial join match resolution" below)
        fields: { s_pond_id: pond_id, s_pond_name: name, s_pond_type: type }
      - type: attribute_join
        source: species_codes
        left: species_code        # SQL expression / literal evaluated on the row
        right: code                # column on the joined source
        fields: { s_species_name: common_name }

    mapping:                      # ordered output columns; one of from/const/expr/func/codelist
      - { to: name,           from: "nickname" }        # e.g. "Sir Quacksalot"
      - { to: species,        from: "s_species_name" }
      - { to: pondId,         from: "s_pond_id", cast: INTEGER }
      - { to: longitude,      func: lon }      # ST_X of EPSG:4326 geometry
      - { to: latitude,       func: lat }      # ST_Y of EPSG:4326 geometry
      - { to: mgrs,           func: mgrs }     # for ducks who need a proper grid reference
      - { to: spottedDate,    func: today }    # also: now, uuid, wkb, area, length
      - { to: pond_label,     expr: "upper(s_pond_name)" }   # raw SQL on the row, e.g. "MURKY LAGOON"

    layers:                       # one or more output layers from the same chain above
      - layer: Ducks
        crs: EPSG:25833
      - layer: DucksChillPonds     # a second layer, filtered from the same pipeline
        crs: EPSG:25833
        filter: "s_pond_type = 'no_drama'"
```

A single-layer pipeline can write `layer:`/`crs:`/`filter:` directly instead of a
`layers:` list (see `pipelines/test.yaml` for a minimal working example) — and a
single-pipeline file can skip the `pipelines:` wrapper entirely and write
`sources`/`base`/`steps`/`mapping`/`output` directly at the top level. Both are
legacy shorthands, auto-upgraded to the Config shape above on load.

### Source formats

| `format` | read path | notes |
|----------|-----------|-------|
| `gpkg`, `geojson`, `gml`, `shp`, `flatgeobuf` | `ST_Read` | curve geometry (CircularString, CompoundCurve, ...) needs the `curves` extra to linearize |
| `fgdb` | `ST_Read` | points at a `.gdb` folder; its geometry column is detected via a `DESCRIBE` fallback since `ST_Read_Meta` is unreliable on File Geodatabases |
| `wfs` | GetFeature (GML) → temp `.gml` → `ST_Read` | `SRSNAME` is pinned to the source `crs` to avoid silent geometry corruption |
| `arcgis_rest` | paged JSON → temp GeoJSON → `ST_Read` | always EPSG:4326; `page_size` caps the page size, `where` pushes a filter server-side |
| `oapif` (OGC API - Features) | paged `/collections/{layer}/items` → temp GeoJSON → `ST_Read` | always EPSG:4326; supports `bbox`, `max_features`, and `page_size` |
| `parquet` | DuckDB's native `read_parquet()` | bypasses GDAL entirely — the bundled GDAL build has no Parquet/Arrow driver |
| `postgres` | DuckDB's `postgres` extension (`ATTACH ... TYPE postgres`) | not GDAL's PG driver; geometry comes back as hex-EWKB, parsed via `ST_GeomFromHEXWKB` |
| `xlsx`, `csv` | `ST_Read` (tabular) | optional geometry from `x_field`+`y_field` (→ `ST_Point`) or `geom_field` (WKT or hex-WKB, auto-detected); `header_row` controls header detection |

Every source also accepts an optional `make_valid: true` to repair invalid geometry via
`ST_MakeValid` before it flows into any joins.

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
run against instead of the main chain, letting a pipeline maintain several parallel
chains that each keep evolving on their own.

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

For "if value LIKE mall then Mallard" style remapping, use `codelist` instead
of hand-writing a `CASE WHEN` in `expr`. Rules are evaluated top-to-bottom, first
match wins:

```yaml
- to: species
  codelist:
    source: raw_species        # column being translated
    case_insensitive: true      # default true
    cases:
      - { match: "mall",     value: "Mallard (reigning pond champion)" }  # exact match
      - { like: "%gadwall%", value: "Gadwall (quietly judging everyone)" } # SQL LIKE pattern
      - { regex: "^teal",    value: "Green-winged Teal (chaotic good)" }   # regex
      - { is_blank: true,    value: "Mallard (reigning pond champion)" }   # NULL or ''
    default: "Mystery duck, do not approach"  # used when nothing matches (omit for NULL)
```

A case can also match `is_blank: true` instead of `match`/`like`/`regex`, matching a
NULL or empty-string source value. That's how you route a specific value *and*
NULL/blank to the same output (as with "mall"/Mallard above) — give both cases the
same `value`. Unlike `is_blank`, an unmatched value falling through to `default`
also catches NULL, but only as a single catch-all — `is_blank` lets a blank source
value take a distinct case, evaluated in order alongside the others.

For large code tables (hundreds+ of codes), point at a CSV instead of listing
rules — this becomes a correlated lookup against `read_csv`, not a giant `CASE`:

```yaml
- to: species_name
  codelist:
    source: species_code
    file: codelists/duck_species.csv   # two (or more) columns
    file_match_col: code                 # key column in the CSV
    file_value_col: common_name          # output column in the CSV
    default: "Unidentified duck"
```

If the lookup is itself a real table with several columns you need (not just a
single translated value), model it as an `attribute_join` step instead — that's
a proper join, not a per-row correlated subquery, and is the better fit for
things like the species lookup table in the example above.

The editor's mapping rows support `codelist` directly: choosing it opens a panel
with a toggle between **rules** (match/like/regex/is blank → value, plus a
default) and **file lookup** (csv path + key/value columns).

## Known limitations

- **One `base` per pipeline.** Each `pipelines` entry starts from a single source's
  features. `merge` can append another source's rows mid-chain via `UNION ALL BY
  NAME`, but those rows only pass through the steps *after* the merge, not the ones
  before it — so there's no way to run one identical step chain over two starting
  datasets at once. Write separate `pipelines` entries instead (each can write to
  its own layer in the same shared output).
- **`match: first` is the default and is easy to reach for by accident.** A base
  feature that overlaps more than one join-source feature — e.g. a duck paddling
  exactly on the boundary between two overlapping ponds — silently keeps only one
  match's fields unless you deliberately opt into `match: all` (see "Spatial join
  match resolution" above).
- **Synchronous run.** `POST /api/run` blocks on the whole pipeline and returns
  once it's done — no job queue, cancellation, or streamed logs for long-running
  jobs. `check`/`run` in the CLI are likewise blocking, single-shot commands.
- **Small `func` set on purpose** (`lon`, `lat`, `mgrs`, `wkb`, `area`, `length`,
  `uuid`, `now`, `today`); add new ones in `engine.py:_func_expr` (SQL) or
  `derive.py` (python UDF).
