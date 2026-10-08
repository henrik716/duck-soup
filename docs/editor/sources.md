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
    **Parquet**, **Postgres**, **CSV**, **Excel**, **JSON**, **FileGDB**, **Shapefile**,
    **ArcGIS REST**) to add an empty card preset to that format, then fill in the path or URL.

=== "add"

    The **add** button adds a blank source card.

!!! tip "Uploading vs. pointing at files"
    Dropped files are copied to a temporary folder, which is fine for exploring. For a pipeline
    you'll run again, point the `uri` at the file where it lives instead (type the path or use
    the folder button). A source can point at any absolute path on disk.

## The file browser

The folder button next to a source's **uri** (and next to the **output file** and a codelist's
CSV file) opens a file browser on the server's file system:

- **Places** on the left jump to the project folder, its `data/`, `pipelines/` and `output/`
  subfolders, your home folder, and each drive.
- The **path bar** shows where you are. Click a folder in it to go up, or click the bar (or
  press <kbd>Ctrl</kbd>+<kbd>L</kbd>) to type or paste a path.
- **Search** (<kbd>Ctrl</kbd>+<kbd>F</kbd>) filters the current folder as you type. From two
  letters on, it also searches every subfolder below it.
- The **filter** shows only data files by default. Switch it to *all files* to see
  everything. Click a column header to sort by name, type, size or date.
- Click to select, double-click (or <kbd>Enter</kbd>) to open a folder or pick a file. A
  `.gdb` folder is picked like a file. <kbd>Alt</kbd>+<kbd>←</kbd>/<kbd>→</kbd> go back and
  forward.

The browser remembers the last folder you used. It shows the machine the **server** runs on:
in Docker, that's the container, so only mounted folders are visible.

## The source card

![The sources tab with an expanded source card](../assets/screenshots/sources-tab-light.png#only-light){ .screenshot loading=lazy }
![The sources tab with an expanded source card](../assets/screenshots/sources-tab-dark.png#only-dark){ .screenshot loading=lazy }

| Field | Notes |
|---|---|
| **id** | Your handle for the source, used by `base`, steps and derived sources. |
| **format** | Detected from the file extension when possible (marked *from extension*). |
| **uri / path / url** | A file path, a `.gdb` folder, a service URL or a Postgres connection string. The folder button opens a file browser at the current file's folder (or the project folder): search the folder and its subfolders, sort by name/type/size/date, jump to project folders, home or drives from the sidebar, or click the path bar (Ctrl+L) to paste a path. Double-click or Enter picks a file; by default only supported data files are shown. |
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

### CSV, Excel and JSON

Tabular sources get extra controls:

- **header row** (CSV and Excel): auto-detect, *first row is headers*, or *no header row*.
- **records path** (JSON): where the array of records sits in the document, e.g.
  `data.items`. Leave it empty when the file is the array itself.
- **geometry**: *none (tabular only)*, *point from X / Y columns*, or *WKT / WKB / GeoJSON
  column*. When a column has an obvious name (`wkt`, `geom`, `geometry`, `lon`/`lat`, `x`/`y`
  …) the editor pre-selects it and marks it *detected*.

A dropped `.json` file is set up as GeoJSON. Switch the format to **json** if it holds plain
records instead. See [JSON](../reference/sources.md#json).

### Downloads and large files

A source with an `http(s)://` URL is downloaded first, and a CSV or Excel file of 5 MB or more
is converted to Parquet once so previews stay fast. Both run in the background. While they do,
the card's badge shows the progress in place of *inspecting…*:

- *downloading… 23.4 MB · 4.1 MB/s*, or with a percentage when the server sends the file
  size.
- *converting to Parquet for fast previews… 12 s*.

The status at the top of the editor shows the same while a preview waits on the source.

Once it's done, the card shows when the file was downloaded, e.g. *Downloaded 2 h ago ·
44.6 MB · read via Parquet*. Previews keep using that download, even after you restart the
editor, until you click **refresh** next to it, which downloads the file again in the
background and then re-runs the preview. **Run** always downloads fresh data. See
[Remote files](../reference/sources.md#remote-files).

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
- **where**: an optional SQL filter, e.g. `vibe = 'chill'`.
- **buffer distance**: optional, in working-CRS units.
- **repair invalid geometry**: on by default.

A derived source can be used anywhere a step asks for a `source`. Use one when you want to join
against only part of a big reference layer, or against a buffered version of it, without adding
steps to the main chain.

## Previewing a source on its own

Click the eye button on a source card to see that source's raw features (no steps, no
mapping) on the map and in the table. The card is highlighted while you preview it. The
**view** dropdown in the map toolbar does the same, and lists every source. Pick *pipeline
output* there, or click **Show pipeline output** above the table, to go back.
