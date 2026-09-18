# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Backend**
```bash
pip install -e .                                   # install duck_soup + deps
pip install -e ".[mgrs]"                           # optional MGRS support
python -m duck_soup.cli check pipelines/test.yaml  # validate pipeline YAML
python -m duck_soup.cli run pipelines/test.yaml    # execute → GeoPackage
uvicorn duck_soup.web.app:app --reload              # dev server at :8000
```

**Frontend** (source: `duck_soup/web/ui/`, output: `duck_soup/web/static/`)
```bash
cd duck_soup/web/ui
npm install
npm run dev        # Vite dev server with /api proxy → :8000
npm run build      # compile TypeScript → ../static/ (commit the output)
npm run build:fast # skip tsc type-check
```

**Tests**
```bash
pip install -e ".[mgrs,test]"                      # install with pytest + httpx
pytest -v                                           # backend test suite (tests/)
```

GitHub Actions (`.github/workflows/ci.yml`) runs on every push/PR: a `backend` job (`pytest`)
and a `frontend` job (`npm run build`, i.e. `tsc --noEmit` + `vite build`). There's no
frontend unit-test framework yet — the frontend job is a type-check/build gate only.

## Architecture

The tool replaces FME workspaces. A pipeline YAML describes sources, spatial/attribute join steps, output column mapping, and an output GeoPackage. All processing runs inside a single DuckDB in-memory session using the spatial extension (GDAL-backed `ST_Read`/`ST_Write`).

### Python modules

**`config.py`** — Pydantic schemas for the entire YAML structure. `Source`, `Step` (SpatialJoin / AttributeJoin / NearestNeighbor), `MapItem`, `CodeList`, `Pipeline`, `Config`. Validates cross-references and CRS. Auto-upgrades the legacy single-output format to the multi-output/multi-layer format on load.

**`sources.py`** — Translates a `Source` config into a SQL table expression. Most formats produce `ST_Read(...)` strings via `_st_read()`. Exceptions:
- `arcgis_rest`: paged HTTP → temp GeoJSON → `ST_Read`
- `wfs`: HTTP GetFeature (GML/3.2) → temp `.gml` → `ST_Read`
- `parquet`: DuckDB's native `read_parquet()`, not `ST_Read` — the GDAL build bundled with the `spatial` extension has no Parquet/Arrow driver

**`engine.py`** — Core SQL builder and executor. Builds a view chain: `src_<id>` views (reprojected to `working_crs`) → `step_0`, `step_1`, … → `mapped`. Spatial joins use `LATERAL … LIMIT 1` (first match). Attribute joins are 1:1 left joins. Nearest-neighbor joins use `LATERAL … ORDER BY ST_Distance(...) LIMIT 1`, optionally filtered by `ST_DWithin` when `max_distance` is set. `MapItem` rules (`from`/`const`/`expr`/`func`/`codelist`) are compiled to SQL column expressions. Output is written via DuckDB's `COPY … (FORMAT GDAL, DRIVER 'GPKG')`.

**`derive.py`** — Registers Python UDFs into DuckDB (`to_mgrs`). Gracefully no-ops if the optional `mgrs` package is absent. Also owns connection bootstrap: `load_extensions()` (spatial + postgres) and `init_duckdb()` (extensions + UDFs) — every DuckDB connection in the codebase goes through one of these.

**`sql_util.py`** — `quote_ident()` / `quote_literal()`, shared by `engine.py` and `sources.py` (imported there under their local `_ident`/`_lit` and `_sql_ident`/`_sql_str` names).

**`cli.py`** — Thin argparse wrapper around `check` (print schema summary) and `run`.

**`web/app.py`** — FastAPI backend. Key endpoints:
- `POST /api/inspect` — `DESCRIBE SELECT * FROM <read_expr>` to get column schema
- `POST /api/inspect_file` — WFS uses HTTP GetCapabilities + XML parse, OGC API - Features (`oapif`) lists `/collections` as JSON; everything else uses `ST_Read_Meta()`
- `POST /api/preview` — runs the pipeline up to the mapped view, returns 50 rows (no geometry)
- `POST /api/run` — executes the full pipeline async via `asyncio.to_thread`

### Frontend

Single-page app (TypeScript + Vite + MapLibre GL + Lucide). Source/step/mapping card rendering and config serialisation are split across `cards/*.ts` (one file per card type, plus `collectPipelineDef`) and `config-io.ts` (`collectConfig`/`hydrate`), with shared helpers in `dom.ts`, `state.ts`, `combo.ts`, `schema.ts`, `toast.ts`, `table.ts`, `metadata.ts`, `file-explorer.ts`, `expr-drawer.ts`, and `step-gallery.ts`. `main.ts` orchestrates tabs, validation, preview, and run. `types.ts` mirrors the Python Pydantic schemas exactly — keep them in sync when adding fields. Built assets are committed to `duck_soup/web/static/` and served by FastAPI at `/static/`.

### Pipeline YAML

```yaml
name: my_pipeline
working_crs: EPSG:25833      # all joins happen in this CRS
sources:
  - id: places
    format: geojson           # gpkg | geojson | gml | fgdb | wfs | arcgis_rest | oapif | parquet | flatgeobuf | shp | xlsx | csv | postgres
    uri: data/places.geojson
    layer: places             # layer / typename / sheet name
    crs: EPSG:4326
base: places                  # source features flow from here
steps:
  - type: spatial_join
    source: regions
    predicate: intersects     # intersects | contains | within
    fields: {region_name: name}
  - type: attribute_join
    source: lookup
    left: category            # column on the running row
    right: code               # column on the lookup source
    fields: {label: display_label}
  - type: nearest_neighbor
    source: stations
    max_distance: 5000        # optional search radius, in working_crs units
    distance_field: distance_m # optional output column for the computed distance
    fields: {nearest_station: name}
mapping:
  - {to: name,     from: name}
  - {to: category, const: "Embassy"}
  - {to: uuid,     func: uuid}     # uuid | now | today | lon | lat | mgrs
  - to: region
    codelist:
      source: region_name
      cases:
        - {like: "%oslo%", value: "Capital"}
      default: "Other"
outputs:
  - path: output/result.gpkg
    layers:
      - name: result_layer
        crs: EPSG:25833
```

`codelist` can also reference a CSV file: `{file: data/lookup.csv, key: code, value: label}`.

A source's `uri` (and a `codelist`'s `file`) can be any absolute path on disk, not just a path
under `data/` — `sources.py` passes it straight through to `ST_Read`/`read_parquet` with no
normalization. `data/` is only a convenience location for the committed test fixtures; personal
datasets don't need to be copied into the repo to be used in a pipeline.

A `postgres` source's `uri` is a full libpq/DSN connection string (e.g.
`postgresql://user:pass@host:5432/dbname`), read via DuckDB's `postgres` extension
(`ATTACH ... TYPE postgres`) rather than GDAL/`ST_Read` — see `sources.py`'s module docstring.
`layer` names the table, optionally schema-qualified (`schema.table`, defaulting to `public`).
