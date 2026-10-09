# Development

This page is for people changing duck soup itself. To use it, see
[Getting started](getting-started/install.md).

## Setup

```bash
git clone https://github.com/henrik716/duck-soup.git
cd duck-soup
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[test,docs]"
pytest -v
```

## Code layout

```text
duck_soup/
  config.py      Pydantic schema for the YAML (the source of truth for every option)
  sources.py     one reader per format → a SQL table expression (mostly ST_Read)
  engine.py      builds the src_<id> → step_N → mapped view chain and writes the output
  derive.py      DuckDB bootstrap (extensions) + Python UDFs (to_mgrs)
  sql_util.py    identifier / literal quoting
  worker.py      the editor's engine process: preview / counts / run, crash-isolated
  cli.py         check / run / serve / tutorial
  tutorial.py    copies the bundled tutorial (tutorial/) into a project folder
  web/
    app.py       FastAPI backend (/api/inspect, /api/preview, /api/run, …)
    ui/          TypeScript + Vite + MapLibre editor source
    static/      built editor (committed)
pipelines/       example pipelines
data/            test fixtures
tests/           pytest suite
docs/            this site (MkDocs Material)
```

### Engine in brief

1. Each source becomes a `src_<id>` view, reprojected to the working CRS (`sources.py`).
2. Each step becomes `step_N` on top of the previous view (`engine.py`). Spatial joins, clip,
   erase and nearest-neighbour use a plain `JOIN` on the spatial predicate, which DuckDB plans
   as an R-tree `SPATIAL_JOIN`, then aggregate per base row. Avoid `LATERAL` subqueries for
   spatial predicates: they compare every pair.
3. The mapping compiles to a final `SELECT`, written with
   `COPY … (FORMAT GDAL, DRIVER 'GPKG')`, or `COPY … (FORMAT PARQUET)` for a `.parquet`
   output (GeoParquet, with the geometry cast to `GEOMETRY('<crs>')` so the CRS is embedded).

### The engine worker

The editor's server never opens a DuckDB connection itself: all DuckDB work happens in
child processes (`worker.py`), so a native crash in DuckDB or its spatial extension kills only
that process. The request answers with `crashed: true` and an error naming the exit code and
the last log lines, and the next request starts a new worker.

- **`ENGINE`** takes the interactive work: inspecting sources and listing their layers,
  previews, row counts. One call at a time.
- **`RUNNER`** takes runs, so a long run doesn't hold up previews. **cancel**
  (`/api/run/cancel`) kills it; the next run starts a new one.
- Converting a large CSV/Excel file to Parquet gets a **one-off worker** per file, since it
  can take a minute and would otherwise hold up `ENGINE`.

The server keeps everything that reports progress: it downloads remote files (for a run too,
before handing it over) and tracks conversions, and the workers read the results from the
shared cache folders. A worker exits by itself when the server process goes away. The CLI
runs the engine in its own process, where a crash simply ends the run with a non-zero exit
code.

## Frontend

```bash
cd duck_soup/web/ui
npm install
npm run dev     # Vite on :5173, proxies /api to uvicorn on :8000
npm run build   # tsc + vite build → ../static/ (commit the output)
```

Run `uvicorn duck_soup.web.app:app --reload` alongside it. `src/types.ts` mirrors the Pydantic
models in `config.py`, so keep them in sync when adding fields.

## Docs

```bash
pip install -e ".[docs]"
mkdocs serve           # live preview at http://127.0.0.1:8000
mkdocs build --strict  # what CI runs; fails on broken links
```

Pages live in `docs/`, and navigation is in `mkdocs.yml`. When you add or change an option in
`config.py`, update the matching reference page. The site deploys to GitHub Pages
automatically on every push to `main` that touches `docs/` or `mkdocs.yml`.

The tutorial pages include tables, YAML and diagrams from `docs/tutorials/generated/`, which
`scripts/build_tutorial.py` produces by running every example through the engine. It also
writes the sample data and `pondsworth.yaml` into `duck_soup/tutorial/`, which ships inside
the package so `duck-soup tutorial` (`duck_soup/tutorial.py`) can copy it into a user's project
folder. Re-run it after changing the engine or the examples, and commit the output:

```bash
python scripts/build_tutorial.py
```

## CI

`.github/workflows/ci.yml` runs `pytest` and the frontend build on every push and PR. On
`v*` tags it also publishes the package to PyPI (`duck-soup-etl`) and the Docker image to
`ghcr.io/henrik716/duck-soup`.

To release: bump `version` in `pyproject.toml`, commit and push, then push a matching tag
(`git tag v0.1.15 && git push origin v0.1.15`). PyPI rejects a version that's already been
published.
`.github/workflows/docs.yml` builds this site and deploys it.
