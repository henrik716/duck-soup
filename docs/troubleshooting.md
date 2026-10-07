# Troubleshooting

## Installation and startup

??? failure "`duck-soup: command not found` after `pip install`"
    The script folder isn't on your `PATH`. Use `pipx install duck-soup-etl` instead, or run
    `python -m duck_soup.cli serve`. See [Install](getting-started/install.md#option-2-pipx-python-311).

??? failure "Error loading the `spatial` extension on first run"
    DuckDB downloads `spatial` from `extensions.duckdb.org` the first time. Check internet
    access and any proxy or firewall. On an offline machine, install the extension once on a
    connected machine with the **same DuckDB version** and copy `~/.duckdb/extensions/` across.

??? failure "The editor can't find my files / relative paths point to the wrong place"
    Relative paths resolve against the folder the **server process was started from**, while
    saved configs and the file browser use `DUCK_SOUP_ROOT` (default `~/duck-soup`). Start the
    server from your project folder with `DUCK_SOUP_ROOT` set to the same folder, or use
    absolute paths. In Docker both are `/data`, the folder you mounted.

## Empty or wrong results

??? question "My spatial join matches nothing (all NULL)"
    This is almost always a CRS problem: the data's real coordinates don't match the `crs`
    declared on the source. Check:

    1. The source card's CRS warning in the editor. It compares the sampled coordinates with
       the declared CRS.
    2. Preview each source on its own (map **view** dropdown). Both should appear in the same
       place on the map.
    3. The predicate direction. `within` means *the base feature is within the source
       feature*. For points in polygons, use `intersects` or `within`, not `contains`.

??? question "A feature that overlaps two polygons only got one of them"
    That's `match: first`, the default. Use `on_multiple: largest_overlap` to pick the
    polygon it's mostly in, or `match: all` to get one row per match. See
    [`spatial_join`](reference/steps.md#spatial_join).

??? question "\"working CRS … is in degrees\" error"
    You're buffering, measuring distances or computing `area`/`length` in a geographic CRS
    (like EPSG:4326, often inherited from the base source). Set `working_crs` to a projected
    CRS in metres: the UTM zone covering your data (EPSG:326xx in the northern hemisphere,
    EPSG:327xx in the southern, e.g. EPSG:32633), your national grid, or EPSG:3857 as a
    rough fallback (its distances are only accurate near the equator).

??? question "My output has more rows than the base source"
    A step is fanning out: `spatial_join` with `match: all`, `intersect_overlay`, or `merge`.
    Preview step by step to find which.

??? question "Columns disappeared after a step"
    `dissolve` keeps only its `by` columns. Pull the attributes you need back in afterwards
    with a join, or dissolve on more columns.

??? question "`lon`/`lat`/`area` fail with \"base source has no geometry\""
    The base is tabular (CSV/Excel). Give it geometry with `x_field`/`y_field` or
    `geom_field`, or set `geometry: true` if it really is spatial.

## Performance

??? tip "Nearest-neighbour is slow"
    Without `max_distance`, every base feature is compared to every source feature. Add a
    realistic search radius.

??? tip "The editor preview feels slow on a huge dataset"
    Lower the **limit** in the map toolbar, or turn on **in view** and zoom to the area you
    care about. Remote sources (WFS, ArcGIS REST, OGC API) are fetched over HTTP on each
    preview, so filter them server-side where possible (`where` for ArcGIS REST).

## Known limitations

- **One base per pipeline.** `merge` can append rows mid-chain, but those rows skip the
  earlier steps. To run the same steps over two datasets, write two pipelines.
- **Runs are synchronous.** The editor's run is one request: no live log, no cancellation.
  Use the CLI for long jobs.
- **Output is GeoPackage or GeoParquet only.** GeoParquet has no layers, so a multi-layer
  config writes a folder with one `.parquet` file per layer.
- **Small `func` set** (`uuid`, `now`, `today`, `lon`, `lat`, `mgrs`, `wkb`, `area`,
  `length`). Anything else can be done with `expr` and DuckDB SQL.
- **A few YAML options aren't in the editor yet**: `on_multiple` and per-layer `mapping`.
  The editor doesn't show them.

Found a bug? [Open an issue](https://github.com/henrik716/duck-soup/issues).
