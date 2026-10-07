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
  cli.py         check / run / serve
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
writes the sample data and `data/tutorial/pondsworth.yaml`. Re-run it after changing the
engine or the examples, and commit the output:

```bash
python scripts/build_tutorial.py
```

## CI

`.github/workflows/ci.yml` runs `pytest` and the frontend build on every push and PR, and on
`v*` tags publishes the Docker image to `ghcr.io/henrik716/duck-soup`.
`.github/workflows/docs.yml` builds this site and deploys it.
