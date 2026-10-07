---
hide:
  - navigation
---

<div class="ds-hero" markdown>

![duck soup logo](assets/favicon.png)

# duck soup

<p class="tagline">Config-driven geodata ETL on DuckDB — as easy as… well, duck soup.</p>

[Get started](getting-started/install.md){ .md-button .md-button--primary }
[Take the editor tour](editor/overview.md){ .md-button }

</div>

**duck soup** is a lightweight replacement for heavyweight ETL workspaces (think FME) and
one-off Python scripts. You describe a pipeline — where the data comes from, how it should
be joined and reshaped, which columns come out the other end — either as a short YAML file
or visually in a browser-based editor. duck soup runs it inside **DuckDB** with the
**spatial** extension and writes the result to a **GeoPackage** or **GeoParquet**.

## What you can do with it

<div class="grid cards" markdown>

-   :material-database-import: **Read almost anything**

    GeoPackage, GeoJSON, Shapefile, FlatGeobuf, GML, File Geodatabase, (Geo)Parquet,
    CSV/Excel, PostGIS, WFS, OGC API - Features and ArcGIS REST services.
    [Source formats →](reference/sources.md)

-   :material-vector-intersection: **Join and geoprocess**

    Spatial joins, attribute joins, nearest-neighbour search, buffer, centroid, clip,
    erase, dissolve, overlay, filter and merge, chained in any order.
    [Step types →](reference/steps.md)

-   :material-table-column: **Shape the output schema**

    Rename and cast columns, compute coordinates, MGRS, area and length, generate UUIDs
    and timestamps, translate codes with rule- or CSV-based codelists.
    [Mapping →](reference/mapping.md)

-   :material-map-search: **See it before you run it**

    The editor previews every source, every intermediate step and the final output on a
    map and in a table, re-validating as you type.
    [Editor tour →](editor/overview.md)

</div>

## A pipeline in 30 seconds

This pipeline takes a set of points, finds which county each one falls in, and writes a
tidy layer with coordinates and an ID:

```yaml
name: places_by_county
output: output/places.gpkg
pipelines:
  - name: places
    working_crs: EPSG:25833          # all joins happen in this (metric) CRS
    sources:
      - {id: places, format: geojson, uri: data/places.geojson, crs: EPSG:4326}
      - {id: county, format: gpkg,    uri: data/counties.gpkg, layer: county, crs: EPSG:25833}
    base: places                     # features flow from here…
    steps:
      - type: spatial_join           # …and pick up the county they fall in
        source: county
        predicate: intersects
        fields: {county_name: name}
    mapping:                         # …and come out with exactly these columns
      - {to: name,      from: name}
      - {to: county,    from: county_name}
      - {to: latitude,  func: lat}
      - {to: longitude, func: lon}
      - {to: id,        func: uuid}
    layers:
      - {layer: places, crs: EPSG:25833}
```

```bash
duck-soup run places_by_county.yaml
```

In the editor, the same pipeline is a handful of cards (sources, steps, a mapping grid,
output layers) next to a live map and table of the result. Here it is with a larger example
loaded:

![The duck soup editor](assets/screenshots/editor-overview-light.png#only-light){ .screenshot loading=lazy }
![The duck soup editor](assets/screenshots/editor-overview-dark.png#only-dark){ .screenshot loading=lazy }

## How the guide is organised

| If you want to… | Read |
|---|---|
| Install duck soup and run your first pipeline | [Install](getting-started/install.md), [Quickstart](getting-started/quickstart.md) |
| Understand sources, base, steps, mapping and layers | [Core concepts](concepts.md) |
| Build pipelines by clicking rather than typing | [Web editor](editor/overview.md) |
| Look up every YAML option | [Reference](reference/yaml.md) |
| Fix something that went wrong | [Troubleshooting](troubleshooting.md) |
