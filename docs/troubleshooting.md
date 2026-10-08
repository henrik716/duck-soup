# Troubleshooting

## Installation and startup

??? failure "`duck-soup: command not found` after `pip install`"
    The script folder isn't on your `PATH`. Use `pipx install duck-soup-etl` instead, or run
    `python -m duck_soup.cli serve`. See [Install](getting-started/install.md#option-1-pipx-or-uv-recommended).

??? failure "Error loading the `spatial` extension on first run"
    DuckDB downloads `spatial` from `extensions.duckdb.org` the first time. Check internet
    access and any proxy or firewall. On an offline machine, install the extension once on a
    connected machine with the **same DuckDB version** and copy `~/.duckdb/extensions/` across.

??? failure "The editor can't find my files / relative paths point to the wrong place"
    Relative paths resolve against the folder the **server process was started from**, while
    saved configs and the file browser use `DUCK_SOUP_ROOT` (default `~/duck-soup`). Start the
    server from your project folder with `DUCK_SOUP_ROOT` set to the same folder, or use
    absolute paths. In Docker both are `/data`, the folder you mounted.

## Remote sources and databases

??? failure "\"unable to get local issuer certificate\" / SSL errors on WFS, GML or HTTPS sources"
    GDAL uses its own TLS stack, separate from Python's. duck soup points it at the
    certificate bundle from `certifi`, which covers public services. Behind a corporate proxy
    that re-signs HTTPS traffic, or for a server with an internal certificate, that bundle
    doesn't include your organisation's root certificate. Point duck soup at a bundle that does
    (ask IT for it, as a `.pem` file) before starting it:

    === "macOS / Linux"

        ```bash
        export GDAL_CACERT=/path/to/company-ca.pem
        export CURL_CA_BUNDLE=$GDAL_CACERT
        export REQUESTS_CA_BUNDLE=$GDAL_CACERT
        duck-soup serve
        ```

    === "Windows (PowerShell)"

        ```powershell
        $env:GDAL_CACERT = "C:\certs\company-ca.pem"
        $env:CURL_CA_BUNDLE = $env:GDAL_CACERT
        $env:REQUESTS_CA_BUNDLE = $env:GDAL_CACERT
        duck-soup serve
        ```

    `REQUESTS_CA_BUNDLE` covers the sources fetched by duck soup itself (WFS, OGC API -
    Features, ArcGIS REST); the other two cover GDAL. If you need an HTTP proxy, set
    `HTTPS_PROXY` the same way.

??? failure "A WFS, OGC API or ArcGIS REST source fails or returns nothing"
    1. Open the service URL in a browser. An error page, a login prompt or a timeout there
       means the problem is the service, not the pipeline. duck soup can't send credentials,
       so services that need a login or token won't work.
    2. Check `layer`: the feature type (WFS), collection id (OGC API) or sublayer id (ArcGIS)
       must match exactly. The editor's layer dropdown lists the valid ones.
    3. For ArcGIS REST, test the `where` clause in the service's own query page. An invalid
       one makes the server return an error.
    4. A WFS layer that comes back with suspiciously round numbers of features (1 000, 10 000)
       has probably hit the server's response limit. See [WFS](reference/sources.md#wfs).

??? failure "Postgres: connection refused or authentication failed"
    - Test the same connection string with `psql "postgresql://…"` from the same machine. If
      that fails too, the problem is the network, firewall or credentials.
    - In Docker, `localhost` is the container itself. Use `host.docker.internal` (Docker
      Desktop) or the database server's real host name.
    - Leave the password out of the YAML and set `PGPASSWORD` instead. See
      [Credentials](scheduling.md#credentials).
    - `layer` is `schema.table`. A plain `table` means `public.table`.

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
    A step is fanning out: `spatial_join` with `match: all`, `intersect_overlay`, `line_overlay` (one row per piece), or `merge`.
    Preview step by step to find which.

??? question "Columns disappeared after a step"
    `dissolve` keeps only its `by` columns. Pull the attributes you need back in afterwards
    with a join, or dissolve on more columns.

??? question "A column I cast is full of NULLs"
    `cast` uses `TRY_CAST`, so values that can't be converted become NULL instead of failing
    the run: `unknown` as `INTEGER`, or `March 2024` as `DATE`. Preview the column without the
    cast to see the raw values, then clean them with an `expr` (for example
    `strptime(inspected, '%d/%m/%Y')`) before casting. See
    [`cast`](tutorials/mapping.md#cast-set-the-column-type).

??? question "Dates before 1970 are missing or wrong in the GeoPackage"
    GeoPackage output can't store `DATE` values before 1970. Cast the column to `TIMESTAMP`
    instead.

??? question "`lon`/`lat`/`area` fail with \"base source has no geometry\""
    The base is tabular (CSV/Excel/JSON). Give it geometry with `x_field`/`y_field` or
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
- **No authentication for web services.** WFS, OGC API - Features and ArcGIS REST sources
  are fetched without credentials. A WFS `uri` can carry an API key as a query parameter.
- **WFS isn't paged.** A feature type is fetched in one request, so a server's response limit
  can cut it short. Prefer `oapif` where the service offers it.
- **Output is GeoPackage or GeoParquet only.** GeoParquet has no layers, so a multi-layer
  config writes a folder with one `.parquet` file per layer.
- **Small `func` set** (`uuid`, `now`, `today`, `lon`, `lat`, `mgrs`, `wkb`, `area`,
  `length`). Anything else can be done with `expr` and DuckDB SQL.

Found a bug? [Open an issue](https://github.com/henrik716/duck-soup/issues).
