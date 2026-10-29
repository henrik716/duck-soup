# Changelog

What changed in each release, newest first. Releases are grouped by minor version, with
the patch release that brought each change in brackets. Changes to how an existing pipeline
loads or what it writes are listed under **Upgrading** at the top of each section.

To upgrade, see [Install](getting-started/install.md); `duck-soup --version` and the badge
next to the editor's name show the version you're running.

## 1.8 — 2026-10-29

**Editor**

- A slow preview says how far it is: `previewing… 42% · 8s`, in a chip at the top of the
  preview panel, and the bar above the tabs fills up to match instead of sweeping. The
  percentage is DuckDB's, for the query running now, and shows once the query has run for a
  couple of seconds. A download of known size and an `http_lookup` step's requests fill the
  bar the same way. See [Preview & run](editor/preview-and-run.md). (1.8.0)
- A run shows the same (`running… query 42% · 63s`), also on the **Run Logs** tab's
  `running…` line. Each step and each layer is a query of its own, and the percentage starts
  over for each. (1.8.0)
- The status pill's tooltip no longer flickers under the mouse during a slow preview. It was
  rewritten every second (`previewing… 3s`, `4s`, …). (1.8.0)
- The percentage also shows when the editor's server runs on Linux without a terminal, as in
  the Docker image or as a service. Before, only the seconds showed there. (1.8.1)
- A dissolve or combine lines key renamed in **as** shows as grouped by in **columns reaching
  this step**. Before, the column showed as dropped, and its **by** (or **keep all**) added it
  a second time. (1.8.2; released as 1.8.3, since 1.8.2's tests failed on a check of the
  percentage that relied on DuckDB tracking every query)

## 1.7 — 2026-10-13 to 2026-10-27

!!! warning "Upgrading"
    - `cast: DATE` and `cast: TIMESTAMP` also read a day.month.year date followed by a time
      (`14.03.2024 08:30`, with or without seconds). Before, such values became NULL. (1.7.6)

**Pipeline YAML**

- Geometry clean-up and generalization steps, shown in the editor's gallery under
  **Shaping up**:
    - [`simplify`](reference/steps.md#simplify): fewer vertices within a `tolerance`, one
      feature at a time, or with `shared_boundaries: true` the polygons together, so the
      edges neighbours share stay shared and no gaps or slivers open up between them.
      (1.7.0)
    - [`snap_to_grid`](reference/steps.md#snap_to_grid): every vertex rounded to a grid,
      keeping the geometry valid. (1.7.0)
    - [`clean_coverage`](reference/steps.md#clean_coverage): polygons that should fit edge to
      edge made to: overlaps removed, nearly matching edges snapped, small gaps filled. (1.7.0)
    - [`fill_holes`](reference/steps.md#fill_holes): the holes in polygons removed, all of
      them or those under `max_area`. (1.7.0)
    - [`eliminate_slivers`](reference/steps.md#eliminate_slivers): polygons under `max_area`
      (and/or matching `where`) merged into the neighbour they share the longest border with,
      or the largest. (1.7.0)
    - [`split_lines`](reference/steps.md#split_lines): lines cut where they cross or touch
      each other, or with `source` where another source's lines or polygon edges cross them.
      `tolerance` also cuts where a line ends just short of another. (1.7.0)
    - [`explode`](reference/steps.md#explode): multipart to singleparts, a row per part, with
      an optional part number. (1.7.0)
- [`remove_duplicates`](reference/steps.md#remove_duplicates): the first row of each set
  with the same `by` values and/or the same geometry, with `rejects` for the duplicates.
  (1.7.0)
- [`http_lookup`](reference/steps.md#http_lookup): an HTTP GET per row, e.g. an address
  search, with values from the JSON response as columns. One request per distinct URL,
  cached on disk, at a polite rate. (1.7.0)
- [`make_points`](reference/steps.md#make_points): point geometry from two coordinate
  columns, so a table without geometry (geocoded with `http_lookup`, say) has it from that
  step on. (1.7.0)
- A CRS can be any `AUTHORITY:CODE` in the PROJ database, including codes with letters such
  as `IGNF:LAMB93`. Before, the code had to be a number. A code PROJ doesn't know now stops
  the preview or run before anything is read, naming the field (*unknown CRS*). Before, it
  failed partway with *Could not create projection*. See [YAML](reference/yaml.md). (1.7.9)

**Editor**

- The step gallery has a search box. It finds a step by its name, its description, or the
  FME transformer or QGIS tool it replaces (`Deaggregator`, `DonutHoleExtractor`,
  `Split with lines`, …); <kbd>Enter</kbd> adds the first match. (1.7.0)
- The search box's hint names QGIS first, and its examples are tool names only the search
  knows: `Delete holes` (QGIS) and `Deaggregator` (FME). (1.7.1)
- Replacing the geometry is easier to find: Update Attributes lists **geom** among the
  columns reaching it, and clicking it opens the expression builder, whose new
  **Geometry (new shape)** group has the conversions: outline, hulls, bounding box, point on
  surface, line midpoint, and more. See [`attributes`](reference/steps.md#attributes). (1.7.0)
- HTTP Lookup's **Test request** shows the first row's response; click a value in it to add
  it as a field. (1.7.0)
- The selected map feature is drawn in indigo instead of teal: Persian indigo in the light
  theme, a lighter shade in the dark one. (1.7.2)
- **Refresh all** in the Sources tab reads every source of the pipeline again: downloaded
  files are downloaded again, and the rest have their columns and layers read again, for a
  local file that has been replaced. Then the preview re-runs. See
  [Refreshing all sources](editor/sources.md#refreshing-all-sources). (1.7.4)
- A mapping column's cast can be cleared by picking **— no cast —** at the top of its list,
  and the list offers `BIGINT` for whole numbers over about 2.1 billion. Each type says what
  it's for. (1.7.6)
- The expression builder's new **Convert** snippets do what a cast can't: numbers with a
  decimal comma (`1 234,5`), `ja`/`nei` as true/false, and ArcGIS REST dates (milliseconds since 1970). `CAST` and `TRY_CAST` moved there from
  **Numeric**. (1.7.6)
- With several output layers, the flow diagram runs their lines into one trunk with a single
  arrow into the output file, instead of a curve per layer piling up in front of it. (1.7.9)
- Placeholder text in empty fields comes from the tutorial's town of Pondsworth
  (`district_name = 'Old Puddleton'`, `vegetation = 'sedge'`, …) instead of unrelated
  examples. (1.7.9)
- The name in the top bar ends in a violet ring, a 。, instead of a full stop: "duck soup。".
  (1.7.10)
- The ring after the name in the top bar is smaller, closer to the size of a full stop.
  (1.7.11)
- Mapping and step errors about a missing column say what's wrong in words, instead of
  DuckDB's *Referenced column "width" not found in FROM clause!*: the step that dropped the
  column (*step 8 (combine_lines) drops 'width': it keeps only the columns it groups by and
  its aggregates*), the closest column name there is (*Did you mean 'nearest_stop'?*), or
  that a column name with a dot in it, such as `NET.TYPEVEG`, needs double quotes. (1.7.12)
- The **Problems** tab names each problem in the editor's words (*step 8 · aggregate 1*,
  *mapping row 2*) instead of by its config path, and says *click to go to error*. The card
  or mapping row a problem is about stays outlined in red until it's fixed. (1.7.12)

**Fixes**

- A layer without geometry and without a mapping, such as a rejects layer from a CSV
  source, failed to write to a GeoPackage. (1.7.0)
- A config with many pipelines sharing their sources, such as the tutorial's, took minutes
  to load in the editor, with previews waiting until it was done. Source cards with the
  same source now share one inspect, and a burst of edits syncs the editor once. The
  tutorial config loads in seconds. (1.7.0)
- Loading another config made the preview wait until the previous config's sources had all
  been inspected. Loading a config now cancels the previous one's inspects, and previews go
  ahead of any inspects still waiting. (1.7.2)
- Switching away from a config whose preview takes a while, such as one with several remote
  CSV sources, still waited for that preview: its sources' inspects held every connection
  the browser allows to the server, so the next config couldn't even be fetched. Loading a
  config now cancels the open one's inspects before fetching it. (1.7.3)
- On narrow screens the top bar jumped about while a config loaded, as the status pill's
  text changed width. Its width now comes from the room in the bar, not from its text: a
  long message is cut short, and hovering the pill shows all of it. (1.7.2)
- A CSV or Excel source loaded from a config with no geometry got one in the editor anyway,
  when it had columns such as `lon`/`lat`, and those columns were gone from its rows. In the
  tutorial, previewing or counting the `make_points` pipeline failed with *Referenced column
  "lon" not found*. The editor now only suggests geometry columns for a source you're adding.
  If you saved the tutorial config in the editor, set `sightings`' geometry back to *none*
  in the `make_points` pipeline, or run `duck-soup tutorial --force`. (1.7.8)
- Buffers, distances and areas in a geographic working CRS that isn't one of a few common
  ones, such as SWEREF99 (EPSG:4619), ran in degrees instead of being refused. Any lon/lat
  CRS is now recognized. (1.7.9)

**Docs**

- The new steps in the step reference, the editor's steps page and the
  [FME guide](guides/fme.md), with the FME transformers each one stands in for. (1.7.0)
- [Steps by example](tutorials/steps.md) has an example for each new step but
  `http_lookup`, which needs a web API. Two new sample datasets come with them: `reedbeds`,
  three marsh zones that don't quite fit together, and `sightings`, a CSV with text
  coordinates. Run `duck-soup tutorial --force` to get them; it replaces
  `pipelines/pondsworth.yaml` too, so save any changes you made to it under another name
  first. (1.7.0)
- The diagrams in [Steps by example](tutorials/steps.md) show more of what each step does,
  and each has a caption saying what to look at. The line steps draw every output row as its
  own stretch, so you can see where `line_overlay` and `split_lines` cut and where
  `combine_lines` joins (`combine_lines` now starts from the pieces it actually gets).
  `simplify` shows its vertices, `on_multiple` the district each option picks, and
  `spatial_join` the district names. (1.7.5)
- How to update the tutorial after upgrading: run `duck-soup tutorial --force`, which
  replaces the sample data and `pipelines/pondsworth.yaml`. See
  [After upgrading](getting-started/install.md#after-upgrading). `duck-soup tutorial`'s
  `--force` help and its message when the files already exist say so too. (1.7.5)
- Each pipeline in the tutorial's `pipelines/pondsworth.yaml` declares only the sources it
  uses, instead of all twelve, so the editor shows only those. Run `duck-soup tutorial --force`
  to get the new file. (1.7.7)
- [Troubleshooting](troubleshooting.md) has an entry for the *unknown CRS* error. (1.7.9)
- The docs site's header and the preview image shown when a docs link is shared end the name
  in the same ring as the editor. (1.7.10)
- The title on the docs home page ends in the ring too, instead of a full stop. (1.7.11)
- [Troubleshooting](troubleshooting.md) has an entry for column names with dots in them,
  which need double quotes in SQL. (1.7.12)

## 1.6 — 2026-10-13

!!! warning "Upgrading"
    - An `attribute_join` whose `right` column has duplicate values now takes the first
      matching row in the source's row order. Before, it took an arbitrary one, so a pipeline
      with duplicate keys may now write different values. Use `match: summarize` to combine
      them instead. (1.6.0)

**Pipeline YAML**

- Aggregates without SQL: a [`dissolve`](reference/steps.md#dissolve) or
  [`combine_lines`](reference/steps.md#combine_lines) aggregate can be `{from: column, how: …}`
  as well as a SQL `expr`. `how` is `any`, `most_common`, `from_largest`, `all`, `sum`,
  `average`, `min`, `max` or `count`, and `to` defaults to the column's name. See
  [Keeping other columns](reference/steps.md#keeping-other-columns). (1.6.0)
- `spatial_join` and `attribute_join` take `match: summarize`: one row per feature, with
  each field combining the values of all its matches, e.g. a count of trees per district or a
  list of the streets crossing a parcel. See
  [Summarizing matches](reference/steps.md#summarizing-matches). (1.6.0)
- An aggregate that can't work on its column (a sum of text, a column that isn't there) is an
  error that says so before anything runs, and the Problems tab links to it. (1.6.0)

**Editor**

- Dissolve and Combine Lines cards list the columns reaching the step, marked as grouped by,
  kept or dropped. Click one to keep it, or **by** to group by it. Each kept column is a row:
  how to combine its values, from a list, with a switch to SQL. **+ keep all** and **+ count**
  add rows in one click. (1.6.0)
- A join that keeps the first match warns when features in the sample data match several. (1.6.0)
- Conditions without SQL: the filter step, output layer filters and derived sources' `where`
  are rows of *column · operator · value* (is, greater than, contains, is empty, is one of, …),
  with a switch to SQL. The YAML still holds the SQL. (1.6.0)
- Map preview: hover a feature to highlight it, and click it to select it. A selected feature
  is drawn on top, its attributes open in a popup, and its row is highlighted in the Data
  Table. Where features overlap, the popup steps through them (*1 of 3 here*). Clicking a
  table row selects its feature on the map. (1.6.0)
- The selected map feature is drawn in teal, the editor's colour for spatial things, instead
  of amber, which didn't fit the rest of the palette. (1.6.1)
- The live checks (expression builder, field checks, the join warning) run on the first
  1000 base features, so a check after a dissolve no longer computes over all the data and
  holds up the preview. (1.6.0)

**Fixes**

- Dissolve and Combine Lines steps are saved with `type` first again (1.5.1 wrote it last).
  (1.6.0)

**Docs**

- [Keeping other columns](reference/steps.md#keeping-other-columns) and
  [Summarizing matches](reference/steps.md#summarizing-matches) in the step reference, the
  tutorial's dissolve and combine_lines examples without SQL, and the editor pages for the new
  controls. (1.6.0)

## 1.5 — 2026-10-13

**Pipeline YAML**

- New [`combine_lines`](reference/steps.md#combine_lines) step, shown as **Combine Lines** in
  the editor: joins lines that meet end to end into longer lines, one feature per connected
  chain with the same `by` values. `aggregates` are computed per chain, and
  `preserve_direction` (on by default) only joins lines running the same way. (1.5.1)

**Docs**

- A [`combine_lines`](tutorials/steps.md#combine_lines) example in Steps by example. (1.5.1)
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
