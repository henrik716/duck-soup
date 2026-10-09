# Source formats

## Common fields

| Key | Type | Default | Meaning |
|---|---|---|---|
| `id` | string | **required** | Unique handle for this source. |
| `format` | enum | **required** | One of the formats below. |
| `uri` | string | **required** | File path, `.gdb` folder, service URL or Postgres connection string. |
| `layer` | string | — | Layer / WFS typename / sheet / collection / ArcGIS sublayer id / Postgres table. |
| `crs` | CRS | — | The source's CRS, e.g. `EPSG:4326`. |
| `geometry` | bool | by format | Force the source to be treated as spatial (`true`) or tabular (`false`). |
| `make_valid` | bool | `true` | Repair invalid geometry with `ST_MakeValid` when loading. |

## Format-specific fields

| Key | Formats | Default | Meaning |
|---|---|---|---|
| `header_row` | `csv`, `xlsx` | auto-detect | `true` = first row is headers, `false` = no header row. See [CSV and Excel](#csv-and-excel). |
| `records` | `json` | — | Dot path to the array of records inside the document, e.g. `data.items`. See [JSON](#json). |
| `x_field`, `y_field` | `csv`, `xlsx`, `json` | — | Build point geometry from two numeric columns. |
| `geom_field` | `csv`, `xlsx`, `json` | — | Build geometry from a WKT, hex-WKB or GeoJSON column. |
| `where` | `arcgis_rest` | `1=1` | Filter evaluated by the ArcGIS server. See [ArcGIS REST](#arcgis-rest). |
| `page_size` | `arcgis_rest`, `oapif` | `2000` | Features per request while paging. |

Other formats ignore these keys.

## Formats at a glance

| `format` | Reads | How |
|---|---|---|
| `gpkg` | GeoPackage | GDAL via DuckDB `ST_Read` |
| `geojson` | GeoJSON file or URL | `ST_Read` |
| `shp` | Shapefile | `ST_Read` |
| `flatgeobuf` | FlatGeobuf | `ST_Read` |
| `gml` | GML / INSPIRE GML | `ST_Read` |
| `fgdb` | Esri File Geodatabase (`.gdb` folder) | `ST_Read` (OpenFileGDB) |
| `parquet` | Parquet / GeoParquet | DuckDB `read_parquet()` |
| `postgres` | PostgreSQL / PostGIS table | DuckDB `postgres` extension |
| `csv` | CSV | `ST_Read`, tabular (optional geometry from columns) |
| `xlsx` | Excel sheet | `ST_Read`, tabular (optional geometry from columns) |
| `json` | Plain JSON records (not GeoJSON) | DuckDB `read_json()`, tabular (optional geometry from columns) |
| `wfs` | OGC WFS | HTTP GetFeature → temp GML → `ST_Read` |
| `oapif` | OGC API - Features | paged HTTP → temp GeoJSON → `ST_Read` |
| `arcgis_rest` | ArcGIS MapServer/FeatureServer layer | paged HTTP → temp GeoJSON → `ST_Read` |

## Files

### GeoPackage, GeoJSON, Shapefile, FlatGeobuf, GML

```yaml
- id: ponds
  format: gpkg
  uri: data/ponds.gpkg
  layer: ponds          # needed when the file holds several layers
  crs: EPSG:32631
```

Curved geometry types (CircularString, CompoundCurve, CurvePolygon, MultiCurve, MultiSurface),
which DuckDB can't parse, are linearized automatically with `pyogrio`. The result is cached, so
repeated previews stay fast.

On Windows, files of 2 GiB or more are read through `pyogrio` instead of `ST_Read`, which
crashes on them there. This happens automatically.

### File Geodatabase

```yaml
- id: nests
  format: fgdb
  uri: C:/pond-survey/nesting.gdb   # the .gdb folder itself
  layer: nests
  crs: EPSG:32631
```

### Parquet / GeoParquet

```yaml
- id: duck_tracks
  format: parquet
  uri: data/duck_tracks.parquet   # GPS pings from tagged ducks
  crs: EPSG:4326
```

Read with DuckDB's native `read_parquet()`, because the GDAL bundled with DuckDB has no Parquet
driver. The geometry column is detected from GeoParquet metadata.

### CSV and Excel

Tabular by default, so they're usable in attribute joins without any geometry. Extra fields:

| Key | Meaning |
|---|---|
| `layer` | Sheet name (Excel). |
| `header_row` | `true` = first row is headers, `false` = no header row, omit = auto-detect. |
| `x_field` + `y_field` | Build point geometry from two numeric columns. Set both together. |
| `geom_field` | Build geometry from a WKT, hex-WKB or GeoJSON-text column (auto-detected). |

CSV and Excel files of 5 MB or more are converted to Parquet the first time they're read (in the
system temp folder) and read from that copy afterwards, with the same columns and types. GDAL
re-scans a whole CSV every time it opens one, so without the copy every preview of a large file
takes seconds. The copy is rebuilt when the file changes.

`x_field`/`y_field` and `geom_field` are mutually exclusive, and need a `crs`:

```yaml
- id: sightings
  format: csv
  uri: data/duck_sightings.csv
  x_field: lon
  y_field: lat
  crs: EPSG:4326
```

A value that can't be parsed (a non-numeric coordinate, or malformed WKT, WKB or GeoJSON) gives
that row an empty geometry instead of failing the read. Such a row never matches a spatial step,
so it ends up in that step's rejects layer if it has one.

### JSON

For plain JSON records, such as an API response, rather than GeoJSON. A GeoJSON
FeatureCollection, whatever its file extension, belongs in `geojson`.

```yaml
- id: feeders
  format: json
  uri: https://api.example.org/feeders?pond=12
  records: data.items        # where the array of records sits in the document
  geom_field: geometry       # or x_field + y_field
  crs: EPSG:4326
```

Read with DuckDB's native `read_json()`, not GDAL. Tabular by default, like CSV and Excel.

| Key | Meaning |
|---|---|
| `records` | Dot path to the array of records, e.g. `results` or `data.items`. Omit when the file is the array itself (`[{…}, {…}]`) or has one object per line (newline-delimited JSON). |
| `x_field` + `y_field` | Build point geometry from two numeric columns. Numbers stored as strings (`"10.7"`) work too. |
| `geom_field` | Build geometry from a column holding a GeoJSON geometry object (`{"type": "Point", "coordinates": […]}`), or GeoJSON, WKT or hex-WKB text. |

- **Nested objects** become struct columns. Reach into them with an `expr` mapping, e.g.
  `{to: city, expr: "address.city"}`.
- **Column types** are worked out from the whole file, so a field that holds a number in one
  record and a string in another doesn't fail the read.
- **One request, no paging.** If an API splits its results over several pages, only the page
  the `uri` points at is read.

### Remote files

A file-based source (`gpkg`, `geojson`, `shp`, `flatgeobuf`, `gml`, `fgdb`, `csv`, `xlsx`,
`json`) can have an `http(s)://` URL as its `uri`:

```yaml
- id: roads
  format: csv
  uri: https://nvdb-eksport.atlas.vegvesen.no/vegnett/veglenkesekvenser/segmentert.csv?fylke=56
  geom_field: GEO.WKT
  crs: EPSG:5973
```

- The file is **downloaded** into the system temp folder and then read locally. That also
  makes export endpoints usable, the ones that build the file on every request and don't
  support partial reads; reading them directly through GDAL times out.
- **Previews reuse the download.** Inspecting, previewing and counting in the editor read
  the newest download until you click **refresh** on the source card, including after
  restarting the editor. Datasets rarely change between two previews, and fetching them
  again takes as long as the download does.
- **Runs download fresh data.** `duck-soup run` and the editor's **Run** always download the
  file again, once per run even if several pipelines read it, so the output reflects the
  current data.
- **Layer names** come from the file name in the URL, as they would for a local file
  (`…/segmentert.csv?fylke=56` → layer `segmentert`).
- **A plain `.shp` URL** is the one exception: it's read directly, so GDAL can also fetch the
  `.shx`/`.dbf` next to it. A zipped shapefile (`.shp.zip`/`.zip`) is downloaded.
- **In the editor**, the download (and, for a large CSV or Excel file, the conversion to
  Parquet) runs in the background. The source card and the status at the top show what's
  happening, e.g. *downloading… 23.4 MB · 4.1 MB/s*, then *converting to Parquet for fast
  previews… 12 s*. The rest of the editor stays usable meanwhile.
- Several remote sources download at the same time, each URL once even if several sources
  use it. A run started while the editor is downloading a file waits for that download and
  uses it.
- If a download fails, the read fails. There's no fallback to an older copy. In the editor
  the error stays until you change the source's settings or click **refresh**, or for a
  minute, instead of being retried with every preview.
- Downloads and Parquet copies are kept in the system temp folder. A copy replaced by a
  newer download is removed an hour later, so a run that's still reading it, in the editor
  or in another `duck-soup run`, isn't disrupted.
- `parquet` URLs are read directly by DuckDB, which handles partial reads itself.

## Databases

### PostgreSQL / PostGIS

```yaml
- id: ponds
  format: postgres
  uri: postgresql://user:pass@dbhost:5432/pond_registry
  layer: wetlands.ponds           # schema.table; plain "table" means public.table
  crs: EPSG:32631
```

Read via DuckDB's `postgres` extension (`ATTACH … TYPE postgres`), not GDAL. Geometry comes back
as hex EWKB and is parsed automatically.

!!! warning
    The connection string, password included, is stored in the YAML. Don't commit pipelines
    containing real credentials.

## Web services

### WFS

```yaml
- id: wetlands
  format: wfs
  uri: https://wfs.example.org/wfs
  layer: ns:ProtectedWetland     # feature type name
  crs: EPSG:32631
```

duck soup sends `GetFeature` (GML 3.2) with `SRSNAME` set to the source's `crs`, so you get
coordinates in the CRS you declared, never in silently swapped axes.

- The whole feature type is fetched in **one** `GetFeature` request, with no paging. Many
  servers cap the number of features per response, so a large layer can come back
  incomplete. If the service also offers OGC API - Features, use `oapif`, which pages.
- Extra query parameters on the `uri` (an API key, for example) are kept and sent with the
  request. duck soup sets `SERVICE`, `VERSION`, `REQUEST`, `TYPENAMES` and `SRSNAME` itself.

### OGC API - Features

```yaml
- id: ponds
  format: oapif
  uri: https://api.example.org/features     # API root, not /items
  layer: ponds                               # collection id
  page_size: 2000
```

Always fetched as CRS84 (EPSG:4326), so leave `crs` out or set it to EPSG:4326. Pages through
`/collections/{layer}/items` until done.

Services that need authentication aren't supported.

### ArcGIS REST

```yaml
- id: bread_stalls
  format: arcgis_rest
  uri: https://services.example.com/arcgis/rest/services/Parks/FeatureServer
  layer: "3"                 # sublayer id
  where: "status = 'open'"   # evaluated server-side, default 1=1
  page_size: 2000
```

Always fetched as EPSG:4326. `uri` can be the service root (with `layer` set) **or** the full
`…/FeatureServer/3` URL (with no `layer`), but not both.

Services that need a login or token aren't supported: requests are sent without
authentication.
