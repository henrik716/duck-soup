# <img src="duck_soup/web/static/favicon.png" width="32" height="32" align="absmiddle"> duck soup

[![DuckDB](https://img.shields.io/badge/Powered%20by-DuckDB-orange.svg)](https://duckdb.org/)
[![Output](https://img.shields.io/badge/Output-GeoPackage%20%7C%20GeoParquet-blue.svg)](https://www.geopackage.org/)
[![Docs](https://img.shields.io/badge/docs-user%20guide-9d85ff.svg)](https://henrik716.github.io/duck-soup/)

📖 **Full user guide: https://henrik716.github.io/duck-soup/**

**duck soup** makes config-driven geodata ETL on **DuckDB** as easy as... well, duck soup!

It's a lightweight, lightning-fast replacement for building heavy workspaces in tools like FME (Safe Software Feature Manipulation Engine), or writing custom, error-prone Python scripts. Describe your pipeline as a simple YAML file — sources, a base, join/geoprocessing steps, attribute mapping, one or more output layers — or build it visually in the interactive web editor.

Under the hood, everything runs inside DuckDB using the powerful **spatial** extension: joins, geoprocessing (buffers, clips, overlays, dissolves, ...), and field mappings all compile down to a chain of SQL views, written straight to an output **GeoPackage** or **GeoParquet**. A single YAML file can define several independent pipelines, each fanning out to multiple layers, all written into one shared output.

---

## Run it

Install the `duck-soup` command with **uv** (recommended) or **pipx**. Both give it its own
isolated environment and need no admin rights:

```bash
# uv: works with or without Python installed; it downloads a matching one itself
uv tool install duck-soup-etl

# pipx: if you already have Python 3.11+ and prefer pipx
pipx install duck-soup-etl
```

Then start the editor and open http://localhost:8000:

```bash
duck-soup serve
```

Internet access is needed on first run so DuckDB can download its `spatial` extension.

Don't have uv or pipx yet?

```bash
# uv, on Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# uv, on macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# pipx
python -m pip install --user pipx
python -m pipx ensurepath          # then open a new terminal
```

To upgrade: `uv tool upgrade duck-soup-etl` or `pipx upgrade duck-soup-etl`.

If you used plain `pip install duck-soup-etl` and `duck-soup` isn't found, that's a
`PATH` issue, not a broken install. `python -m duck_soup.cli serve` always works.

### Docker

For servers, scheduled jobs, or machines where you can't install Python:

```bash
docker run -p 8000:8000 -v "$PWD":/data ghcr.io/henrik716/duck-soup
```

This mounts the current directory as `/data` (also the container's working directory),
so `pipelines/`, `data/` and `output/` are read from and written to it. Folders outside
it, such as another drive or a network share, need their own `-v` mount. To upgrade:
`docker pull ghcr.io/henrik716/duck-soup`.

## Try it

`duck-soup tutorial` copies a small sample town (Pondsworth) and a config with an
example of every step and mapping option into a folder:

```bash
mkdir -p ~/duck-soup && cd ~/duck-soup   # the editor's default project folder
duck-soup tutorial
duck-soup serve                          # then pick "pondsworth" in the editor's load list
```

The [tutorials](https://henrik716.github.io/duck-soup/tutorials/) walk through it.

## Command line

| Command | What it does |
|---------|--------------|
| `duck-soup check <file>` | validate a pipeline YAML without reading any data |
| `duck-soup run <file>` | run every pipeline in the file and write the output |
| `duck-soup serve [--host] [--port]` | start the web editor (default `127.0.0.1:8000`); `DUCK_SOUP_ROOT` sets the project folder (default `~/duck-soup`) |
| `duck-soup tutorial [folder] [--force]` | copy the tutorial data and config into a project folder |

`check` and `run` exit non-zero on any failure, so they slot into scripts and CI.
Relative paths in a pipeline resolve against the folder you run the command from.

The editor has no authentication and can read files and run SQL on the machine it runs
on. Keep it on `127.0.0.1` or behind something that controls access.

### Scheduling

A run is one command, so cron, systemd timers, Windows Task Scheduler, Docker and CI can
all schedule it:

```bash
# crontab: rebuild every night at 02:00
0 2 * * * cd /srv/gis && /home/gis/.local/bin/duck-soup run pipelines/nightly.yaml >> logs/nightly.log 2>&1
```

With `overwrite: true` the old output is deleted *before* the sources are read, so a
failed run leaves no file. The [scheduling guide](https://henrik716.github.io/duck-soup/scheduling/)
has a recipe per scheduler and shows how to swap in the new file only after a good run.

## Key Features

- **YAML-driven pipelines:** Describe inputs, join/geoprocessing steps, schema mapping, and output layers in one configuration file you can commit, diff, and review.
- **Multi-pipeline / multi-layer output:** One file can define several independent pipelines, each writing one or more layers, all into a single shared GeoPackage (or, for GeoParquet, one file per layer) with optional dataset-level metadata. Each layer can have its own filter and its own column mapping.
- **Web editor:** A visual, browser-based pipeline builder. Everything you can write in YAML can be built and edited there:
  - live validation as you type, with a **Problems** tab that jumps to the card at fault
  - a preview of any source, any intermediate step, or the final output, on a map and in a sortable table
  - **ducks in a row**, an interactive flow of sources → steps → layers: click a node to edit it in a side panel, insert steps with the **+** on a connection, drag steps to reorder them, and count the rows on every connection (on a sample or all the data)
  - a **SQL** tab with the SQL behind every source, step and output layer
  - a **History** tab with every past run, editor and scheduled CLI runs alike, and the rows each layer got
  - runs you can cancel, and previews that keep working while a run goes on
  - DuckDB runs in separate processes, so a crash inside DuckDB is reported as an error instead of taking the editor down
  - a SQL expression builder with live checks against sample data
  - import a pasted YAML, or export a pipeline as a standalone `.py` script
  - undo/redo, dark and light mode
  - drag a File Geodatabase folder or a Shapefile onto the sources panel to upload it and add it as a source
- **Powered by DuckDB Spatial:** Fast columnar execution, GDAL-backed `ST_Read`/`ST_Write`, and R-tree spatial joins.
- **Many sources:** GeoPackage, GeoJSON, Shapefile, FlatGeobuf, GML, File Geodatabase, (Geo)Parquet, CSV/Excel/JSON, PostGIS, WFS, OGC API - Features and ArcGIS REST.
- **Flexible joins:** Spatial joins (intersects/contains/within; keep the first match, the largest overlap, or all matches), attribute joins, and nearest-neighbour searches with an optional distance cap.
- **Geoprocessing steps:** Buffer, centroid, clip, erase, dissolve, intersect overlay, filter, and merge (union), chainable like any join step, with `snapshot` to fork the chain into named branches.
- **Derived sources:** Build a filtered/buffered view of any source and reuse it as a join source, without a dedicated step.
- **Rich attribute mapping:** Rename, cast, compute coordinates/MGRS/area/length, generate UUIDs/timestamps, write SQL expressions, or apply rule-based/CSV-based codelist lookups.

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

Once the chain is built, each output layer applies its optional `filter`, then the
attribute mapping (the pipeline's `mapping`, or the layer's own if it has one), and is
written straight to GeoPackage via `COPY … (FORMAT GDAL, DRIVER 'GPKG')`. A pipeline
can write several layers this way, and a Config file can run several pipelines, all
appended into the same GeoPackage.
An `output` ending in `.parquet` (or `.geoparquet`) is written as GeoParquet instead,
via DuckDB's native `COPY … (FORMAT PARQUET)` with the layer CRS embedded in the `geo`
metadata. GeoParquet has no layers, so a single layer is written to that file and
several go to a folder of `<layer>.parquet` files named after it.
lon/lat/mgrs/wkb/area/length are always derived from the same working-CRS geometry
used for the joins, so there's no extra reprojection.

Most formats go through DuckDB's `ST_Read` (which uses GDAL), so adding a format is
usually one branch in `sources.py`. **ArcGIS REST** and **OGC API - Features (oapif)**
are paged over HTTP into a temporary GeoJSON file first, then read like any other file;
**WFS** is likewise fetched (GetFeature/GML) into a temporary `.gml` file first. **Parquet**
and **Postgres** skip GDAL entirely, using DuckDB's native `read_parquet()` and `postgres`
extension respectively. See "Source formats" below for the full picture.

## Pipeline YAML

A pipeline file is a **Config**: one shared output (GeoPackage, or GeoParquet for a
`.parquet` path), optional dataset metadata, and a list of independent `pipelines`.
Each pipeline has its own sources/base/steps/mapping, and can fan out to one or more
output `layers` that all get appended into that same output:

```yaml
name: ducks
description: "Ranking every duck in the neighbourhood pond by sass level"
output: output/Ducks.gpkg       # one shared GeoPackage for every pipeline below (.parquet → GeoParquet)
overwrite: true
metadata:                       # optional dataset-level metadata
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
      - layer: DucksPublic         # a third layer with its own, slimmer columns
        crs: EPSG:4326
        mapping:                   # replaces the pipeline mapping for this layer only
          - { to: name,    from: "nickname" }
          - { to: species, from: "s_species_name" }
```

A layer `filter` runs before the mapping, so it uses the upstream column names
(`s_pond_type`), not the output names.

A single-layer pipeline can write `layer:`/`crs:`/`filter:` directly instead of a
`layers:` list (see `pipelines/test.yaml` for a minimal working example) — and a
single-pipeline file can skip the `pipelines:` wrapper entirely and write
`sources`/`base`/`steps`/`mapping`/`output` directly at the top level. Both are
legacy shorthands, auto-upgraded to the Config shape above on load.

Every option below can be set in the editor as well as in YAML. The full reference is in
the [user guide](https://henrik716.github.io/duck-soup/reference/yaml/).

### Source formats

| `format` | read path | notes |
|----------|-----------|-------|
| `gpkg`, `geojson`, `gml`, `shp`, `flatgeobuf` | `ST_Read` | curve geometry (CircularString, CompoundCurve, ...) is automatically linearized via `pyogrio` |
| `fgdb` | `ST_Read` | points at a `.gdb` folder; its geometry column is detected via a `DESCRIBE` fallback since `ST_Read_Meta` is unreliable on File Geodatabases |
| `wfs` | GetFeature (GML) → temp `.gml` → `ST_Read` | `SRSNAME` is pinned to the source `crs` to avoid silent geometry corruption |
| `arcgis_rest` | paged JSON → temp GeoJSON → `ST_Read` | always EPSG:4326; `page_size` caps the page size, `where` pushes a filter server-side |
| `oapif` (OGC API - Features) | paged `/collections/{layer}/items` → temp GeoJSON → `ST_Read` | always EPSG:4326; `page_size` sets the features per request |
| `parquet` | DuckDB's native `read_parquet()` | bypasses GDAL entirely — the bundled GDAL build has no Parquet/Arrow driver |
| `postgres` | DuckDB's `postgres` extension (`ATTACH ... TYPE postgres`) | not GDAL's PG driver; geometry comes back as hex-EWKB, parsed via `ST_GeomFromHEXWKB` |
| `xlsx`, `csv` | `ST_Read` (tabular) | optional geometry from `x_field`+`y_field` (→ `ST_Point`) or `geom_field` (WKT or hex-WKB, auto-detected); `header_row` controls header detection |

Every source also accepts an optional `make_valid: true` to repair invalid geometry via
`ST_MakeValid` before it flows into any joins, and `force_2d: true` to drop Z/M values
(`ST_Force2D`) on load.

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
| `line_overlay`       | line-on-line overlay: cut lines where `source` lines lie on them, shared pieces get `fields`, the rest pass through; optional `tolerance` for lines that are only nearly on top of each other |
| `filter`             | drop rows where `where` (SQL boolean) is false |
| `merge`              | append another source's rows via `UNION ALL BY NAME` |
| `snapshot`           | name the chain's current state (`id:`) so a later step can join back against it or fork a branch |

Joins, `clip` and `filter` also accept `rejects: <layer>`, which, like an FME transformer's
Failed port, writes the rows the step rejects (no match, outside the mask, condition not true)
to a layer of their own instead of passing them on or discarding them.

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
is controlled by `on_multiple` (**when several match** in the editor):

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
  match's fields unless you deliberately opt into `on_multiple: largest_overlap` or
  `match: all` (see "Spatial join match resolution" above).
- **No streamed run log.** `POST /api/run` returns once the run is done, with the
  whole log; there's no job queue. A run can be cancelled (`POST /api/run/cancel`, the
  editor's **cancel** button). `check`/`run` in the CLI are blocking, single-shot commands.
- **Output is GeoPackage or GeoParquet only.**
- **No variable substitution in the YAML.** Connection strings and URLs are stored as
  written. For PostgreSQL, leave the password out and set `PGPASSWORD` instead.
- **Small `func` set on purpose** (`lon`, `lat`, `mgrs`, `wkb`, `area`, `length`,
  `uuid`, `now`, `today`); anything else can be an `expr`. New ones go in
  `engine.py:_func_expr` (SQL) or `derive.py` (python UDF).

---

## Development

Only needed to change duck soup itself. You need Python 3.11+, and Node.js 18+ only if
you change the editor frontend. More detail in the
[development guide](https://henrik716.github.io/duck-soup/development/).

```bash
git clone https://github.com/henrik716/duck-soup.git
cd duck-soup
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[test,docs]"      # package + pytest/httpx + mkdocs

pytest -v                                            # test suite
python -m duck_soup.cli check pipelines/test.yaml    # validate the self-contained test pipeline
python -m duck_soup.cli run   pipelines/test.yaml    # writes output/test.gpkg
uvicorn duck_soup.web.app:app --reload               # editor backend at http://127.0.0.1:8000
```

The built editor is committed in `duck_soup/web/static/`, so the backend serves it
without Node. To change the frontend, run the Vite dev server alongside the backend for
hot reloading (it proxies `/api` to port 8000), then build and commit the output:

```bash
cd duck_soup/web/ui
npm install
npm run dev        # http://localhost:5173
npm run build      # type-checks and writes ../static/ (commit it)
```

The user guide is MkDocs Material, in `docs/`. `mkdocs serve` previews it, and
`mkdocs build --strict` is what the Docs workflow runs before deploying to GitHub Pages.

```
duck_soup/           # Python package
  config.py          # YAML schema (pydantic) + load/save
  sources.py         # one reader per format (mostly DuckDB ST_Read / GDAL)
  engine.py          # builds a chain of SQL views, writes GeoPackage/GeoParquet
  derive.py          # DuckDB bootstrap (extensions) + python UDFs (MGRS)
  sql_util.py        # identifier / literal quoting
  cli.py             # duck-soup check / run / serve / tutorial
  tutorial.py        # copies tutorial/ (sample data + config) into a project folder
  web/
    app.py           # FastAPI backend
    static/          # compiled editor frontend, served by FastAPI
    ui/              # TypeScript + Vite + MapLibre editor source
pipelines/           # example pipelines
data/                # test fixtures
tests/               # pytest suite
docs/                # user guide (MkDocs Material)
```

`pyproject.toml` pins `duckdb` to `>=1.5.4,<1.6` because `INSTALL spatial` always
fetches the extension build matching the running DuckDB core version — pinning DuckDB
is what keeps the spatial extension version reproducible. Bump the pin (and this note)
together when upgrading.

**Releasing:** bump `version` in `pyproject.toml`, commit and push, then push a `v*` tag
(e.g. `git tag v0.1.16 && git push origin v0.1.16`). CI then publishes to PyPI and
`ghcr.io/henrik716/duck-soup`.
