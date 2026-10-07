# Quickstart

This page walks you through one small pipeline twice: once from the command line, then
again in the editor. Each pass takes about five minutes.

## The task

You have a GeoJSON file of points (`places`) in WGS84 and a polygon layer of Norwegian
counties (`fylke`) in UTM 33N. You want a GeoPackage layer where every point carries the name
of the county it falls in, plus lat/lon, an MGRS grid reference and a UUID.

That's exactly what [`pipelines/test.yaml`](https://github.com/henrik716/duck-soup/blob/main/pipelines/test.yaml)
in the repository does. Its data is committed under `data/`.

## From the command line

From a clone of the repository:

```bash
duck-soup check pipelines/test.yaml   # validate only
duck-soup run   pipelines/test.yaml   # run → output/test.gpkg
```

`check` prints a one-line summary if the file is valid, or a precise error pointing at the
offending field. `run` logs each stage with timings and writes `output/test.gpkg`. Open it in
QGIS (or any GIS) to look at the result.

Here is the pipeline, trimmed to the essentials:

```yaml
name: test
output: output/test.gpkg
pipelines:
- name: test
  sources:
  - id: places
    format: geojson
    uri: data/test.geojson
    crs: EPSG:4326
  - id: fylke
    format: geojson
    uri: data/Basisdata_0000_Norge_25833_Fylke_GeoJSON.geojson
    layer: Fylke
    crs: EPSG:25833
  base: places                 # (1)!
  steps:
  - type: spatial_join         # (2)!
    source: fylke
    predicate: intersects
    fields:
      county: fylkesnavn
  mapping:                     # (3)!
  - {to: name,      from: name}
  - {to: category,  from: category}
  - {to: latitude,  func: lat}
  - {to: longitude, func: lon}
  - {to: mgrs,      func: mgrs}
  - {to: uuid,      func: uuid}
  - {to: county,    from: county}
  layer: output                # (4)!
  crs: EPSG:25833
```

1. Every output row starts life as one feature of the **base** source.
2. For each place, find the county it intersects and copy its `fylkesnavn` column in as `county`.
3. The **mapping** decides exactly which columns come out, in what order.
4. A single-layer shorthand: write layer `output` in EPSG:25833.

No `working_crs` is set, so joins run in the base source's CRS, which is EPSG:4326 here.
That's fine for a point-in-polygon join. If you add a buffer or measure area, set
`working_crs` to a projected CRS in metres; duck soup refuses to run distance operations in
degrees. See [Core concepts](../concepts.md#the-working-crs).

## In the editor

1. Start the editor (`duck-soup serve`, or the Docker command) and open
   <http://localhost:8000>.
2. Click **new** in the top bar. You get an empty config with one pipeline.
3. In the pipeline's **sources** tab, click the **GeoJSON** template button twice to add two
   source cards. Fill them in:
    - `id` = `places`, uri = `data/test.geojson`. Click the folder icon to browse instead
      of typing.
    - `id` = `fylke`, uri = `data/Basisdata_0000_Norge_25833_Fylke_GeoJSON.geojson`.

    The editor reads each file as soon as the path is set, then fills in the layer and CRS
    and lists the columns.
4. Click **★ base** on the `places` card.
5. Open the **steps** tab, click **add step** and choose **Spatial Join**. Set the source
   to `fylke` and pull `fylkesnavn` in as `county`.
6. Open the **mapping** tab and click the ✨ (auto-map) button to map every available
   column. Then delete the ones you don't want, and add `func` rows for `lat`, `lon`,
   `mgrs` and `uuid`.
7. Open **output layers** and check the layer name and CRS. Set the **output geopackage**
   path at the top of the page.

The map and the **Data Table** tab update as you go. Then:

- <kbd>Ctrl</kbd>+<kbd>S</kbd> (**save**) stores the config as `pipelines/<name>.yaml`.
- <kbd>Ctrl</kbd>+<kbd>Enter</kbd> (**run**) runs it and switches to **Run Logs**.

The **YAML Config** tab shows the exact YAML the editor generated. It's the same format you'd
write by hand, so you can commit it and run it from the CLI later.

## Next steps

- [Core concepts](../concepts.md) explains what's happening under the hood.
- The [Tour of the editor](../editor/overview.md) covers everything else on the screen.
- The [Step types](../reference/steps.md) reference lists every step you can add.
