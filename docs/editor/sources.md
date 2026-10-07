# Sources

The **sources** tab of a pipeline card is where you tell duck soup what data to read, and which
of it the pipeline starts from.

## Adding a source

There are three ways to add one:

=== "Drag and drop"

    Drop files onto the **Drag & drop spatial files here** area (or click it to pick files).
    The files are uploaded to the server's temp folder and a source card is created for each,
    with the format detected from the extension.

    - A **Shapefile** is uploaded together with its sidecar files (`.shx`, `.dbf`, `.prj`, …)
      and becomes one source.
    - A **File Geodatabase**: drop the whole `.gdb` folder. It's uploaded file by file and
      becomes one `fgdb` source.

=== "Template buttons"

    Click a format button (**GPKG**, **OAPIF**, **GeoJSON**, **WFS**, **FlatGeobuf**,
    **Parquet**, **Postgres**, **CSV**, **Excel**, **FileGDB**, **Shapefile**,
    **ArcGIS REST**) to add an empty card preset to that format, then fill in the path or URL.

=== "add"

    The **add** button adds a blank source card.

!!! tip "Uploading vs. pointing at files"
    Dropped files are copied to a temporary folder, which is fine for exploring. For a pipeline
    you'll run again, point the `uri` at the file where it lives instead (type the path or use
    the folder button). A source can point at any absolute path on disk.

## The source card

![The sources tab with an expanded source card](../assets/screenshots/sources-tab.png){ .screenshot loading=lazy }

| Field | Notes |
|---|---|
| **id** | Your handle for the source, used by `base`, steps and derived sources. |
| **format** | Detected from the file extension when possible (marked *from extension*). |
| **uri / path / url** | A file path, a `.gdb` folder, a service URL or a Postgres connection string. The folder button opens a file browser rooted at the project folder. |
| **layer / typename / sheet / collection** | Filled with the layers, feature types, sheets, collections or tables found in the source. Pick one. |
| **crs** | Detected from the data where possible (marked *detected*). Searchable list of common EPSG codes. |
| **repair invalid geometry** | `make_valid`: on by default. |

Once the uri and layer are set, the editor **inspects** the source. It shows a badge with the
column count and lists the columns and their types at the bottom of the card. Those columns
then appear in the step and mapping pickers.

The card also shows warnings when something looks off:

- **CRS sanity check**: if the sampled coordinates don't fit the CRS you picked (for example,
  UTM-sized numbers declared as EPSG:4326), the card says so. A wrong CRS is the most common
  cause of empty spatial joins.
- **Large-file note**: on Windows, files over 2 GiB are read through `pyogrio` instead of
  DuckDB's `ST_Read` to avoid a crash. The card tells you when that happens.
- **Incomplete card**: a card with no `id` or `uri` is left out of the YAML, and the warning
  says why.

### Service and database sources

- **ArcGIS REST**: set the uri to the service root (`…/MapServer` or `…/FeatureServer`) and pick
  the sublayer. If you paste a URL ending in a sublayer id (`…/FeatureServer/3`), the editor
  splits it for you.
- **OGC API - Features**: set the uri to the API root. If you paste a full
  `/collections/{id}/items` URL, the editor trims it and fills in the collection.
- **WFS**: the editor reads `GetCapabilities` and lists the feature types.
- **Postgres**: set the uri to a connection string
  (`postgresql://user:pass@host:5432/dbname`) and pick the table.

### CSV and Excel

Tabular sources get two extra controls:

- **header row**: auto-detect, *first row is headers*, or *no header row*.
- **geometry**: *none (tabular only)*, *point from X / Y columns*, or *WKT / WKB column*. When a
  column has an obvious name (`wkt`, `geom`, `lon`/`lat`, `x`/`y` …) the editor pre-selects it
  and marks it *detected*.

## Choosing the base

Click **★ base** on the source the pipeline should start from. Its features are the rows that
flow through every step to the output. Exactly one source per pipeline is the base. The eye
button in the tab's toolbar previews the base source before any steps.

## The working CRS

The **working CRS** field at the top of the tab sets the CRS that every join, buffer and
area/length calculation runs in. Leave it blank to use the base source's CRS. If you buffer,
measure or use nearest-neighbour distances, set it to a projected CRS in metres. See
[Core concepts](../concepts.md#the-working-crs).

## Derived sources

The **derived sources** tab holds filtered or buffered views of an existing source. Each card
has:

- **id**: the new name.
- **from**: the source (or another derived source) to start from.
- **where**: an optional SQL filter, e.g. `facility_type = 'hospital'`.
- **buffer distance**: optional, in working-CRS units.
- **repair invalid geometry**: on by default.

A derived source can be used anywhere a step asks for a `source`. Use one when you want to join
against only part of a big reference layer, or against a buffered version of it, without adding
steps to the main chain.

## Previewing a source on its own

The **view** dropdown in the map toolbar lists every source. Pick one to see its raw features
(no steps, no mapping) on the map and in the table. Pick *pipeline output* to go back.
