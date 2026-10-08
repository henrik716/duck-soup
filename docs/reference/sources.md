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
| `x_field`, `y_field` | `csv`, `xlsx` | — | Build point geometry from two numeric columns. |
| `geom_field` | `csv`, `xlsx` | — | Build geometry from a WKT or hex-WKB column. |
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
| `geom_field` | Build geometry from a WKT or hex-WKB column (auto-detected). |

`x_field`/`y_field` and `geom_field` are mutually exclusive, and need a `crs`:

```yaml
- id: sightings
  format: csv
  uri: data/duck_sightings.csv
  x_field: lon
  y_field: lat
  crs: EPSG:4326
```

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
