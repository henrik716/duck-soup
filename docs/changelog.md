# Changelog

What changed in each release, newest first. Releases are grouped by minor version, with
the patch release that brought each change in brackets. Changes to how an existing pipeline
loads or what it writes are listed under **Upgrading** at the top of each section.

To upgrade, see [Install](getting-started/install.md); `duck-soup --version` and the badge
next to the editor's name show the version you're running.

## 1.5 — 2026-10-13

**Docs**

- This changelog. (1.5.0)
- Clearer wording across the editor, reference and guide pages. The `rejects` docs now name
  every step type that takes it, and the concepts page says how previews and runs compute
  the chain. (1.5.0)

## 1.4 — 2026-10-12

!!! warning "Upgrading"
    - The Python API that was never documented (`Pipeline`, `load_pipeline`, `run_pipeline`,
      `preview_pipeline`, …) is gone; use the config-based functions in `duck_soup`. (1.4.0)
    - In the single-pipeline shorthand, an `outputs:` list with more than one entry is now an
      error. Before, only the first was written. (1.4.0)
    - `attribute_join` no longer matches every row to the first source row when the `left`
      column also exists on the join source. Pipelines that hit this now get the right
      values. (1.4.2)

**Pipeline YAML**

- New [`attributes`](reference/steps.md#attributes) step, shown as **Update Attributes** in
  the editor: sets columns partway through the pipeline using the mapping's rules
  (`from`/`const`/`expr`/`func`/`codelist`, plus `cast`), in order, and drops columns. Every
  column it doesn't list is kept. (1.4.2, renamed in the editor in 1.4.3)
- [`dissolve`](reference/steps.md#dissolve): a `by` key can be a named SQL expression, and
  `aggregates` adds columns per group (`sum`, `count`, `string_agg`, …). (1.4.2)
- A bad column, key or aggregate in a step names the field that failed, and the Problems
  tab links to it. (1.4.2)

**Editor**

- The working CRS and each output layer's CRS are searchable dropdowns, like a source's
  CRS. (1.4.0)
- The upload spinner on the sources dropzone shows again. (1.4.0)
- The step gallery is grouped like the [step reference](reference/steps.md). (1.4.3)
- The Update Attributes card lists the columns that reach it: click one to set it, or its
  × to drop it. (1.4.5)
- Step cards' column dropdowns, checks and the expression builder offer only the columns
  that reach that step, not columns added by later steps. (1.4.5)

**Docs**

- [Keep or drop?](reference/steps.md#keep-or-drop): when dropping columns is worth it. (1.4.6)

## 1.3 — 2026-10-09 to 2026-10-12

**Mapping**

- New [`func`](reference/mapping.md#func) values: `seq` (row number per layer), `x` / `y`
  (the centroid in the working CRS), `geohash`, `wkt` and `geom_type`. (1.3.4)

**Editor**

- The version shows next to the editor's name. When a newer release is on PyPI the badge
  reads "update to x.y.z", and clicking it copies the upgrade command. `duck-soup serve`
  prints the same hint. Set `DUCK_SOUP_NO_UPDATE_CHECK=1` to turn the check off. (1.3.0)
- [Expression builder](editor/mapping.md#expression-builder): columns grouped by the step
  they come from, Tab / Shift+Tab indent, more snippets (1.3.0), and a Format button
  (<kbd>Shift</kbd>+<kbd>Alt</kbd>+<kbd>F</kbd>) that pretty-prints the expression (1.3.5).
- A mapping row's column dropdown is grouped by origin, like the field pool. (1.3.0)
- Help links from the editor to these docs. (1.3.4)

**Fixes**

- ETRS89 (EPSG:4258), NAD83 and other common geographic CRSs no longer trigger the CRS
  extent warning. (1.3.0)
- The field pool's group headings no longer collapse once the list scrolls. (1.3.5)

**Command line**

- `duck-soup --version`. (1.3.0)
- The install docs recommend uv first, then pipx. (1.3.2)

## 1.2 — 2026-10-09

!!! warning "Upgrading"
    - GeoPackage output casts HUGEINT columns to BIGINT and DECIMALs wider than 18 digits to
      DOUBLE, since GDAL's writer crashes on them (a `sum()` in a mapping `expr` can produce
      one). A value too large for 64 bits fails with an error naming the column. (1.2.0)

**Engine**

- DuckDB runs in separate worker processes, so a crash in DuckDB or its spatial extension
  no longer takes the editor's server down; the request reports the crash and the next one
  starts a fresh worker. See [Crashes](troubleshooting.md#crashes). (1.2.0)
- Runs store each step once, so a pipeline with several layers or rejects layers no longer
  recomputes the whole chain for each one. (1.2.0)
- Chained [`line_overlay`](reference/steps.md#line_overlay) steps no longer double the
  work of every step before them (five in a row went from minutes to seconds). (1.2.0)
- A [`snapshot`](reference/steps.md#snapshot) that a later step reads is computed once in
  previews instead of once per reader. (1.2.1)
- A mapping SQL error names the mapping row that caused it, and the Problems tab links to
  it. (1.2.2)

**Editor**

- A cancel button next to Run. (1.2.0)
- A new preview stops the one still running, so typing in a slow pipeline no longer queues
  a full preview per pause. The status counts the seconds of a slow preview. (1.2.2)

## 1.1 — 2026-10-08 to 2026-10-09

!!! warning "Upgrading"
    - File sources with an `http(s)` `uri` are downloaded to a local file before reading,
      rather than read in place. See [Remote files](reference/sources.md#remote-files).
      (1.1.0)

**Pipeline YAML**

- Joins, `clip` and `filter` take [`rejects: <layer>`](reference/steps.md#rejects): the rows
  the step rejects are written to that extra layer. (1.1.0)
- New [`json`](reference/sources.md#json) source format: plain JSON records, with geometry
  from `x_field`/`y_field` or `geom_field`. (1.1.0)
- New `force_2d` source option drops Z and M values on load. See
  [3D geometry](reference/sources.md#3d-geometry). (1.1.3)

**Sources**

- Previews reuse the newest download until you refresh it; runs download fresh, once per
  URL. (1.1.0)
- CSV and Excel files of 5 MB or more are read from a cached Parquet copy, which is much
  faster on every preview. (1.1.0)
- Remote sources download in parallel, and a failed download stays visible instead of
  being retried by every request. (1.1.2)
- A malformed WKT/WKB/GeoJSON value gives that row an empty geometry instead of failing
  the whole read. (1.1.1)

**Editor**

- The [pipeline flow](editor/overview.md#ducks-in-a-row-the-pipeline-flow) is an editor:
  click a node to edit its card, "+" on a connection to insert a step, drag or
  <kbd>Alt</kbd>+arrow to reorder. It shows [row counts](editor/overview.md#row-counts)
  per connection and the rejects layers. (1.1.0)
- New [SQL tab](editor/preview-and-run.md#sql-tab) and
  [History tab](editor/preview-and-run.md#run-history). (1.1.0)
- Download and conversion progress on the source card and in the header. (1.1.0, every
  pending source listed in 1.1.2)
- Export .py moved to the top bar. (1.1.3)

**Command line**

- `duck-soup run` records each run in the [run history](scheduling.md#run-history)
  (`--history-dir`, `--no-history`). (1.1.0)

**Fixes**

- A CSV column named `WKT` no longer leaves the source with two geometry columns. (1.1.1)
- `line_overlay` no longer crashes on lines with Z or M values. (1.1.3)

## 1.0 — 2026-10-08

**Pipeline YAML**

- New [`line_overlay`](reference/steps.md#line_overlay) step: cuts lines where they run
  along another line dataset and gives the shared stretches that dataset's fields. (1.0.0)

**Editor**

- [Resizable panels](editor/overview.md#resizing-the-panels) and a button to collapse the
  map. (1.0.0)
- The file browser is a full open/save dialog: places, breadcrumbs, sortable columns,
  search and type-ahead. (1.0.0)
- `lat` comes before `lon` in the function dropdown. (1.0.1)

**Docs**

- New [Coming from FME](guides/fme.md) guide and [Output layers](editor/output-layers.md)
  page. (1.0.0)

## 0.1 — 2026-09-17 to 2026-10-08

The first published releases. 0.1.0 put duck soup on PyPI (`duck-soup-etl`) and
`ghcr.io/henrik716/duck-soup`, with `duck-soup serve` and these sources: GeoPackage,
GeoJSON, GML, File Geodatabase, Shapefile, FlatGeobuf, Parquet, CSV, Excel, WFS,
ArcGIS REST, OGC API - Features and PostgreSQL.

!!! warning "Upgrading"
    - The editor keeps its files in `~/duck-soup` unless `DUCK_SOUP_ROOT` says otherwise,
      instead of the folder it was started from. (0.1.2)
    - `buffer`, nearest-neighbor distances and the `area`/`length` functions refuse to run
      in a geographic working CRS, where they used to measure in degrees. (0.1.9)
    - `lon`, `lat` and `mgrs` use the centroid taken in the working CRS, not one computed
      on degrees (which could be ~100 km off for long lines). (0.1.9)
    - Spatial join, nearest-neighbor, clip and erase keep the base rows in order, and ties
      go to the earliest source row. (0.1.9)
    - `erase` drops features it erases completely instead of keeping empty
      geometries. (0.1.9)
    - `func: length` gives a polygon's perimeter instead of 0. (0.1.13)

**Pipeline YAML**

- [GeoParquet output](reference/yaml.md#geoparquet-output): an output path ending in
  `.parquet` or `.geoparquet`. (0.1.10)
- `mgrs` and curved geometry work without installing extras. (0.1.5)

**Engine**

- Spatial join, nearest-neighbor, clip and erase use DuckDB's spatial index; a 50-row
  preview against a 1.1M-feature layer went from over 10 minutes to seconds. (0.1.9)
- `clip` and `intersect_overlay` no longer leave line or point slivers where features only
  touch. (0.1.9)
- On Windows, files of 2 GiB or more are read with pyogrio, avoiding a crash in DuckDB's
  spatial extension. (0.1.8)
- A pipeline without a mapping can write a GeoPackage from a GeoJSON source. (0.1.13)

**Editor**

- Searchable CRS picker for sources, and a warning when a source's coordinates don't fit
  its declared CRS. (0.1.7)
- Light mode, following the OS setting until you pick one. (0.1.11)
- Previewing a step on a snapshot branch shows that branch. (0.1.12)
- An eye button on each source card previews that source on its own. (0.1.13)
- `on_multiple` for spatial joins and [per-layer mapping](editor/output-layers.md#per-layer-mapping)
  in the editor, which also no longer drops them when re-saving a config. (0.1.15)

**Command line**

- [`duck-soup tutorial`](reference/cli.md#duck-soup-tutorial) copies the Pondsworth
  sample data into a project folder. (0.1.14)

**Docs**

- This site (0.1.10), the [tutorials](tutorials/index.md) (0.1.13) and
  [Scheduling runs](scheduling.md) (0.1.15).
