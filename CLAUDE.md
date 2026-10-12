# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Backend**
```bash
pip install -e .                                   # install duck_soup + deps
python -m duck_soup.cli check pipelines/test.yaml  # validate pipeline YAML
python -m duck_soup.cli run pipelines/test.yaml    # execute → GeoPackage (or GeoParquet)
uvicorn duck_soup.web.app:app --reload              # dev server at :8000
```

**Frontend** (source: `duck_soup/web/ui/`, output: `duck_soup/web/static/`)
```bash
cd duck_soup/web/ui
npm install
npm run dev        # Vite dev server with /api proxy → :8000
npm run build      # compile TypeScript → ../static/ (commit the output)
npm run build:fast # skip tsc type-check
npm test           # Vitest unit tests (src/*.test.ts); npm run test:watch to rerun on save
```

**Docs** (GitHub Pages, MkDocs Material; source: `docs/`, config: `mkdocs.yml`)
```bash
pip install -e ".[docs]"
mkdocs serve           # live preview
mkdocs build --strict  # what the Docs workflow runs; fails on broken links
```
`.github/workflows/docs.yml` deploys to https://henrik716.github.io/duck-soup/ on pushes to `main`
touching `docs/` or `mkdocs.yml`. When a `config.py` option changes, update the matching
`docs/reference/*.md` page (and `docs/editor/*.md` if the editor UI changes).
The tutorial pages (`docs/tutorials/`) include generated tables/diagrams from
`docs/tutorials/generated/`: re-run `python scripts/build_tutorial.py` after an engine change
that affects step or mapping output, and commit the regenerated files. The tutorial data and
config ship in the package (`duck_soup/tutorial/`, listed in `pyproject.toml` package-data);
`duck-soup tutorial` (`duck_soup/tutorial.py`) copies them into a project folder as
`data/tutorial/` + `pipelines/pondsworth.yaml`.
The editor screenshots (`docs/assets/screenshots/*-light/dark.png`) come from
`python scripts/build_screenshots.py [shot ...]` (`pip install -e ".[screenshots]"`). It drives
the installed Edge (`--browser chrome` for Chrome), never Playwright's bundled Chromium, which
is too slow with MapLibre and times out. Retake the affected shots after a UI change; the top
bar is in editor-overview, resized-panels and flow-editor.

**Tests**
```bash
pip install -e ".[test]"                            # install with pytest + httpx
pytest -v                                           # backend test suite (tests/)
```

GitHub Actions (`.github/workflows/ci.yml`) runs on every push/PR: a `backend` job (`pytest`)
and a `frontend` job (`npm test`, then `npm run build`, i.e. `tsc --noEmit` + `vite build`).
Frontend unit tests are Vitest files next to their module (`src/<module>.test.ts`, run in Node,
no DOM), so they suit pure modules like `expr-format.ts` and `expr-highlight.ts`; `tsc` type-checks
them too, and Vite's build never includes them since nothing imports them.

## Architecture

The tool replaces FME workspaces. A pipeline YAML describes sources, spatial/attribute join steps, output column mapping, and an output GeoPackage (or GeoParquet, for a `.parquet` path). All processing runs inside a single DuckDB in-memory session using the spatial extension (GDAL-backed `ST_Read`/`ST_Write`).

### Python modules

**`config.py`** — Pydantic schemas for the entire YAML structure: `Config` (one output file, `pipelines`), `PipelineDef` (sources, base, steps, mapping, `layers`), `Source`, the `Step` union, `MapItem`, `CodeList`, `OutputLayer`. Validates cross-references and CRS. `PipelineDef` is the one pipeline model: the engine runs it directly. `load_config_dict` upgrades the legacy single-pipeline shorthand at the dict level (`_upgrade_single_pipeline`) before validating.

**`sources/`** — Translates a `Source` config into a SQL table expression (`read_expr` in `__init__.py`, which dispatches on format). One module per concern: `remote` (downloads, progress), `services` (WFS / ArcGIS REST / OAPIF fetchers and layer listing), `gdal` (`ST_Read`, layer listing, curve linearization, aliasing the geometry column to `geom`), `parquet`, `postgres`, `tabular` (xlsx/csv/json), `large_files` (pyogrio), `crs`, `prepare` (the editor's background preparation and source-card notes). `formats.py` holds the format sets each reader handles (`GDAL_VECTOR`, `GDAL_TABULAR`, `DOWNLOADED`, …) and `alias_geom`; add a format there rather than as a literal tuple. Cache folders and size thresholds live in `settings.py`, read as `settings.X` at call time (never `from .settings import X`), so tests patch them there and `worker._SHARED_SETTINGS` hands them to the workers. Most formats produce `ST_Read(...)` strings via `gdal._st_read()`. Exceptions:
- `arcgis_rest`: paged HTTP → temp GeoJSON → `ST_Read`
- `wfs`: HTTP GetFeature (GML/3.2) → temp `.gml` → `ST_Read`
- `parquet`: DuckDB's native `read_parquet()`, not `ST_Read` — the GDAL build bundled with the `spatial` extension has no Parquet/Arrow driver
- `json`: DuckDB's native `read_json()` (plain records; `records` dot path, geometry via `x_field`/`y_field`/`geom_field`)
- any file format with an `http(s)` uri: downloaded to a local file first (`fetch_remote_file`), since GDAL's `/vsicurl/` range reads fail against per-request export endpoints. Previews reuse the newest download on disk (`latest_download`) until refreshed (`/api/refresh_source`); runs download fresh, once per URL (`run_config` wraps itself in `fresh_downloads()`)
- the editor never blocks a request on that work: `prepare_source` runs the download/conversion in a background thread and the endpoints answer `{"pending": true, "progress": …}` (`_pending_response` in `web/app.py`) until it's ready; the frontend retries (`awaitPrepared` in `main.ts`, the source card's inspect)
- `csv`/`xlsx` of 5 MB+: read from a cached Parquet copy (`_tabular_parquet_cache`, keyed by path + mtime + size + options), since GDAL's CSV driver scans the whole file on every open

**`engine.py`** — Core SQL builder and executor. Builds a view chain: `src_<id>` views (reprojected to `working_crs`) → `step_0`, `step_1`, … → `mapped`. Each step's SELECT comes from **`steps.py`** (`<type>_sql(prev, step, log, ctx)`, pure functions picked by step type from `steps.STEP_SQL`; register a new step type there; `ctx` is a `StepContext` with the working CRS and whether the base has geometry); every view goes through `Engine._emit`, which also records it in `Engine.plan`, so the same chain serves previews (views, so a `LIMIT` can cut steps short), `sql_plan()` (no connection, nothing read: the editor's SQL tab), and runs and `counts()` (steps as TEMP TABLEs, each computed once: otherwise every layer's `COPY` / every count recomputes the chain). A step builder must read the previous view only once (pull it into a `MATERIALIZED` CTE if it needs it twice): views are expanded where they're used, so a second read doubles the work of the whole chain before it, compounding across chained steps (`tests/test_step_reads.py`). For the same reason a `snapshot` that a later step reads (as `source` or `branch`) is stored once as a TEMP TABLE `step_<i>` in previews too (`_snapshot_used`). A step with `rejects` (joins, `clip`, `filter`) gets a `__matched` column and is split into `step_<i>__split` → `step_<i>` (passed) + `step_<i>__rejects`, which `run()` writes as an extra layer after the pipeline's own (unmapped, in the first layer's CRS); `Config.layer_count` counts them. An `Engine` runs one `PipelineDef`, and `run(out_path)` adds its layers to that one file; `run_config` clears the output first (when `overwrite`) and runs each pipeline into it. `run_config(..., written=[])` reports the rows written per layer (from `COPY`'s returned count). Spatial joins (first match or largest overlap), nearest-neighbor joins, clip and erase all go through `steps._per_row_match_select`: a plain `JOIN` on the spatial predicate (so DuckDB plans it as its R-tree `SPATIAL_JOIN`), aggregated per base row — `arg_min` for the single best match, `ST_Union_Agg` for erase. Avoid `LATERAL` subqueries for spatial predicates: they compare every base row against every source row. Nearest-neighbor uses `ST_DWithin` as the join condition when `max_distance` is set; without it, every pair is compared. Attribute joins are 1:1 left joins. `MapItem` rules (`from`/`const`/`expr`/`func`/`codelist`) are compiled to SQL column expressions by **`mapping.py`** (`final_select` builds a layer's SELECT; each `func`'s SQL is in `mapping.FUNC_SQL`, which must list the same names as `config.MAP_FUNCS`, with `GEOMETRY_FUNCS` / `MEASURE_FUNCS` next to it). Before a preview or a run's writes, `_check_mapping` plans (`DESCRIBE`, nothing computed) each layer's final SELECT; on a parser/binder error it plans each item alone and raises `MappingError` naming the first failing one, with its `pipelines.<i>.mapping.<j>` path on the second line (the shape the editor's Problems tab links to a card), so `Engine` takes a `pipeline_index`. The layers are written by `output.py`: GeoPackage via DuckDB's `COPY … (FORMAT GDAL, DRIVER 'GPKG')` (`write_gpkg_layer`; every layer after the first is written to a temp file and spliced in at the SQLite level, `merge_gpkg_layer`, since GDAL's COPY recreates the file), or, when the output path ends in `.parquet`/`.geoparquet`, via native `COPY … (FORMAT PARQUET)` as GeoParquet (`write_parquet_layer`). The geometry is cast to `GEOMETRY('<layer crs>')` there, since without a CRS-typed column DuckDB omits `crs` from the `geo` metadata and readers assume OGC:CRS84. GeoParquet has no layers: 1 layer in total → that file, more → `<stem>/<layer>.parquet` (`parquet_layer_path`).

**`derive.py`** — Registers Python UDFs into DuckDB (`to_mgrs`, backed by the `mgrs` package, a required dependency; `to_geohash`, self-contained, since the spatial extension has no `ST_GeoHash`). Also owns connection bootstrap: `load_extensions()` (spatial + postgres) and `init_duckdb()` (extensions + UDFs) — every DuckDB connection in the codebase goes through one of these.

**`sql_util.py`** — `quote_ident()` / `quote_literal()` (and `st_transform()`), shared by `engine.py` and `sources/` (imported there under their local `_ident`/`_lit` and `_sql_ident`/`_sql_str` names).

**`worker.py`** — Every DuckDB connection the editor's server needs lives in a spawned child process, so a native DuckDB crash can't take the server down. `EngineWorker.call(task, payload, …)` runs a task (`inspect`, `layers`, `preview`, `counts`, `run`, `convert`) in its worker, one call at a time, streaming log lines back over a pipe. Two long-lived workers: `ENGINE` (inspect / layer listing / preview / counts) and `RUNNER` (runs; `RUNNER.cancel()` kills it, behind `POST /api/run/cancel`); `run_once` gives the Parquet conversion in `sources._prepare_job` a one-off worker. `/api/preview` takes a `client` id (one per editor tab, `CLIENT_ID` in `api.ts`; each live check in `validation.ts` uses its own, so checks and the main preview don't stop each other): `_preview_ticket` makes each preview that client's newest, `ENGINE.interrupt(("preview", client))` stops its running one (a DuckDB interrupt via `derive.interrupt_all`, matched by call id so a late interrupt can't hit the next call; the worker stays up), and `call(..., stale=...)` skips one that's been superseded by the time the worker is free; both raise `EngineSuperseded`, answered as `stale: true`. A crash raises `EngineCrashed` (exit code + last log lines; the endpoints answer `crashed: true`), a cancel `EngineCancelled`, and the next call starts a new worker; ordinary exceptions come back as `EngineTaskError` with the worker's traceback. A worker exits when the server process dies (a watchdog thread on `mp.parent_process()`). Each call passes the server's cwd and the `sources` settings in `_SHARED_SETTINGS` (cache folders, `_TABULAR_CACHE_MIN_BYTES`), so a worker reads what the server prepared: downloads stay in the server (for a run, `download_remote_sources` first, then `run_config(downloads={uri: path})` in `RUNNER`). Anything else a test monkeypatches in the server process is invisible to the workers.

**`cli.py`** — Thin argparse wrapper around `check` (print schema summary) and `run` (which records the run in the history; `--history-dir`, `--no-history`).

**`history.py`** — Run history: one JSON file per run in `<root>/runs/<config name>/` (newest 200 kept), written by both `duck-soup run` (root: `--history-dir`, else `$DUCK_SOUP_ROOT`, else cwd) and the editor's `/api/run` (`web.app.RUNS_ROOT`). Recording is best effort and never fails a run. Tests redirect both roots to a temp folder (`tests/conftest.py`).

**`web/app.py`** — FastAPI backend. Key endpoints:
- `POST /api/inspect` — `DESCRIBE SELECT * FROM <read_expr>` (in the `ENGINE` worker) to get column schema
- `POST /api/inspect_file` — WFS uses HTTP GetCapabilities + XML parse, OGC API - Features (`oapif`) lists `/collections` as JSON; everything else uses `ST_Read_Meta()`
- `POST /api/preview` — runs the pipeline up to the mapped view, returns 50 rows (no geometry)
- `POST /api/run` — downloads remote sources, then executes the full pipeline in the `RUNNER` worker (`worker.py`; `POST /api/run/cancel` stops it), returns the rows written per layer, and records the run in the history under the request's `name`
- `POST /api/plan` — the SQL of every view of one pipeline (`Engine.sql_plan`), reads no data
- `POST /api/counts` — row counts after each step of one pipeline (`Engine.counts`), over the first `limit` base features or (`limit: null`) all the data
- `GET /api/runs/{name}`, `GET /api/runs/{name}/{id}` — run history summaries / one full record
- `POST /api/preview` also takes `rejects: true` (with `preview_until_step`) to preview a step's rejects

### Frontend

Single-page app (TypeScript + Vite + MapLibre GL + Lucide). Source/step/mapping card rendering and config serialisation are split across `cards/*.ts` (one file per card type, plus `collectPipelineDef`; `cards/source-upload.ts` is the sources dropzone, which adds cards through the pipeline card's `addSource`) and `config-io.ts` (`collectConfig`/`hydrate`), with shared helpers in `dom.ts`, `state.ts`, `combo.ts`, `schema.ts`, `toast.ts`, `table.ts`, `metadata.ts`, `file-explorer.ts`, `expr-drawer.ts`, `expr-field.ts` (the inline SQL field with the expression-builder icon), `field-preview.ts` (the throwaway preview Config a field's live check runs, up to its step), and `step-gallery.ts`. The `attributes` step card reuses `mapRow` for its columns; `schema.stepOutputColumns` is the one place that knows which columns a step card adds. `main.ts` orchestrates tabs, validation, preview (every kind goes through `showPreview`), run, row counts, the SQL plan and run history. `lineage.ts` draws the pipeline flow diagram (titled "ducks in a row · pipeline flow" in the editor; the code keeps the `lineage` name) (and only redraws when what it draws changes — see `drawnSignature`) and is an editor too: node clicks open the card in `flow-editor.ts` (the card itself, displayed as a fixed panel while it stays in its list in the DOM), "+" on a connection inserts a step via the pipeline card's `_insertStep`, step nodes drag/Alt+arrow to reorder via `_moveStep`. `sql-plan.ts` renders the SQL tab, `run-history.ts` the History tab. Icons are `<i data-lucide="name">` placeholders rendered by `renderIcons(root)` / swapped by `setIcon(el, name)` from `icons.ts` (never lucide's `createIcons`, which re-renders and warns about every icon on the page); a new icon goes in its `ICONS` registry, and `icons.test.ts` fails on any name used in the markup but missing there. Format and step vocabularies come from `/api/meta` (`META` in `state.ts`: formats, funcs, step types, `tabular_geom_formats`, `header_row_formats`, all from `config.py`); don't hard-code them in a card. The step gallery's cards (`STEP_GALLERY` in `step-gallery.ts`, grouped like the step reference, each group with a duck name) are the exception: a new step type needs a card there, in its group. `types.ts` mirrors the Python Pydantic schemas exactly — keep them in sync when adding fields. Built assets are committed to `duck_soup/web/static/` and served by FastAPI at `/static/`.

### Pipeline YAML

```yaml
name: my_config
output: output/result.gpkg      # .parquet / .geoparquet → GeoParquet
pipelines:
  - name: my_pipeline
    working_crs: EPSG:25833     # all joins happen in this CRS
    sources:
      - id: places
        format: geojson         # gpkg | geojson | gml | fgdb | wfs | arcgis_rest | oapif | parquet | flatgeobuf | shp | xlsx | csv | json | postgres
        uri: data/places.geojson
        layer: places           # layer / typename / sheet name
        crs: EPSG:4326
    base: places                # source features flow from here
    steps:
      - type: spatial_join
        source: regions
        predicate: intersects   # intersects | contains | within
        fields: {region_name: name}
      - type: attribute_join
        source: lookup
        left: category          # column on the running row
        right: code             # column on the lookup source
        fields: {label: display_label}
      - type: nearest_neighbor
        source: stations
        max_distance: 5000      # optional search radius, in working_crs units
        distance_field: distance_m  # optional output column for the computed distance
        fields: {nearest_station: name}
    mapping:
      - {to: name,     from: name}
      - {to: category, const: "Embassy"}
      - {to: uuid,     func: uuid}   # see config.MAP_FUNCS
      - to: region
        codelist:
          source: region_name
          cases:
            - {like: "%oslo%", value: "Capital"}
          default: "Other"
    layers:
      - layer: result_layer
        crs: EPSG:25833
```

`codelist` can also reference a CSV file:
`{source: code, file: data/lookup.csv, file_match_col: code, file_value_col: label}`.

The single-pipeline shorthand (no `pipelines:` wrapper; one pipeline's keys at the top level
plus an `output:` block with `path` and `layer`) still loads: `config._upgrade_single_pipeline`
turns it into the form above before validation.

A source's `uri` (and a `codelist`'s `file`) can be any absolute path on disk, not just a path
under `data/` — `sources/` passes it straight through to `ST_Read`/`read_parquet` with no
normalization. `data/` is only a convenience location for the committed test fixtures; personal
datasets don't need to be copied into the repo to be used in a pipeline.

A `postgres` source's `uri` is a full libpq/DSN connection string (e.g.
`postgresql://user:pass@host:5432/dbname`), read via DuckDB's `postgres` extension
(`ATTACH ... TYPE postgres`) rather than GDAL/`ST_Read` — see `sources/__init__.py`'s module docstring.
`layer` names the table, optionally schema-qualified (`schema.table`, defaulting to `public`).
