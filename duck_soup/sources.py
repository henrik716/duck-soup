"""Turn a Source config into something DuckDB can read.

Almost everything goes through DuckDB's ST_Read (which uses GDAL), so adding a
new vector format is usually just a new branch that builds the right GDAL
connection string. ArcGIS REST and OGC API - Features (oapif) are the exception:
both are fetched with pagination to a temporary GeoJSON file, which is then read
with ST_Read like anything else. Parquet is the other exception: the GDAL build
bundled with DuckDB's spatial extension has no Parquet/Arrow driver, so it's read
with DuckDB's own native read_parquet() instead (see read_expr's `parquet` branch).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time
import warnings
from pathlib import Path
from typing import Callable

import duckdb
import requests

from .config import Source

# GDAL geometry-type labels that DuckDB's spatial extension cannot parse: the ISO
# SQL/MM curve types (CircularString, CompoundCurve, CurvePolygon, MultiCurve,
# MultiSurface). DuckDB's GEOMETRY only supports the OGC simple-feature linear
# types, so these need to be linearized (approximated with line segments) before
# ST_Read can touch them.
_CURVE_MARKERS = ("curve", "surface")

# Linearizing a curve layer (see _linearize_curves) round-trips the whole layer
# through GDAL — cheap for a one-off run, but the web editor re-previews on every
# edit and every map pan (bbox mode), so redoing it from scratch each time is the
# difference between an instant preview and a multi-hundred-ms-per-request one.
# Cached here (outside the per-call `workdir`, which is deleted at the end of
# every single preview/run) and keyed by the source file's mtime+size, so an
# edited source file invalidates its own cache entry automatically.
_LINEARIZE_CACHE_DIR = Path(tempfile.gettempdir()) / "duck_soup_linearize_cache"

# Short-lived in-memory cache for fetch_oapif, keyed by (root url, collection, bbox,
# page_size). The web editor's schema-inspection sample and its live preview are two
# independent network round trips against the same collection fired moments apart
# (selecting a collection triggers a sample fetch, which immediately triggers a preview
# fetch), and every further edit/pan re-previews again — each of those would otherwise
# re-page the OAPIF server from scratch even though nothing upstream changed. Entry value
# is (written_at, features fetched so far, whether paging ran to completion i.e. no more
# `next` link) so a later call needing fewer rows (or the same/fewer) can be served
# straight from memory instead of hitting the network again.
_OAPIF_FETCH_CACHE: dict[tuple, tuple[float, list[dict], bool]] = {}
_OAPIF_CACHE_LOCK = threading.Lock()
_OAPIF_CACHE_TTL = 20.0  # seconds


def _sql_str(value: str) -> str:
    """Single-quote a value for inlining into SQL."""
    return "'" + value.replace("'", "''") + "'"


def _sql_ident(name: str) -> str:
    """Double-quote a SQL identifier."""
    return '"' + name.replace('"', '""') + '"'


def _st_read(uri: str, layer: str | None = None, extra: str = "") -> str:
    args = [_sql_str(uri)]
    if layer:
        args.append(f"layer={_sql_str(layer)}")
    if extra:
        args.append(extra)
    return f"ST_Read({', '.join(args)})"


def fetch_wfs(src: Source, workdir: Path) -> str:
    """Fetch a WFS typename via GetFeature (GML) and write a temp .gml file.

    WFS returns GML/XML. Fetches all features in a single request and lets
    GDAL/ST_Read parse the GML file. For very large layers consider the
    'oapif' format instead (see fetch_oapif), which paginates and natively
    returns GeoJSON.

    Without an explicit SRSNAME, a WFS server picks its own default response CRS —
    which may not match `src.crs` (the CRS engine.py's reprojection assumes the fetched
    geometry is already in). That mismatch doesn't just pick the wrong CRS, it silently
    corrupts the geometry: e.g. requesting no SRS gets back EPSG:4258 lon/lat degrees,
    but if `src.crs` says EPSG:25833, those small degree values get treated as UTM33N
    easting/northing metres and reprojected into nonsense (typically landing the data
    near the UTM zone's equator/central-meridian origin, off in the Gulf of Guinea).
    Pinning SRSNAME to `src.crs` (when set) makes the server actually return data in
    the CRS the rest of the pipeline assumes.
    """
    from urllib.parse import urlparse, urlencode, parse_qsl

    base = src.uri
    if base.upper().startswith("WFS:"):
        base = base[4:]

    p = urlparse(base)
    qs_base = [(k, v) for k, v in parse_qsl(p.query)
               if k.lower() not in ("request", "service", "version", "typenames", "typename",
                                    "count", "startindex", "outputformat", "srsname")]

    params = qs_base + [
        ("SERVICE", "WFS"), ("VERSION", "2.0.0"), ("REQUEST", "GetFeature"),
        ("TYPENAMES", src.layer or ""),
    ]
    if src.crs:
        authority, code = src.crs.split(":", 1)
        params.append(("SRSNAME", f"urn:ogc:def:crs:{authority.upper()}::{code}"))
    url = p._replace(query=urlencode(params)).geturl()
    resp = requests.get(url, timeout=300)
    resp.raise_for_status()
    out_path = workdir / f"{src.id}.gml"
    out_path.write_bytes(resp.content)
    return str(out_path)


def fetch_arcgis_rest(src: Source, workdir: Path, sample: bool = False) -> str:
    """Page through an ArcGIS REST query endpoint and write a GeoJSON file.

    `src.uri` is normally the service root, e.g.
    https://host/arcgis/rest/services/Foo/MapServer, with `src.layer` set to
    the sublayer id (e.g. "0"). For back-compat, `uri` may instead already be
    the full layer endpoint (.../MapServer/0) with `layer` left unset.
    Returns the path to the written GeoJSON file (CRS is forced to EPSG:4326).

    `sample`, if true, returns after the first page — for callers that just
    need a representative page of features (e.g. schema inspection), not the
    whole layer. Without it, a layer with more rows than fit on one page can
    make something as cheap as a schema preview take as long as a full run.
    """
    root = src.uri.rstrip("/")
    base_url = f"{root}/{src.layer}" if src.layer else root
    query_url = base_url + "/query"
    out_path = workdir / f"{src.id}.geojson"

    features: list[dict] = []
    offset = 0
    while True:
        params = {
            "where": src.where,
            "outFields": "*",
            "f": "geojson",
            "outSR": 4326,
            "resultOffset": offset,
            "resultRecordCount": src.page_size,
            "returnGeometry": "true",
        }
        resp = requests.get(query_url, params=params, timeout=120)
        resp.raise_for_status()
        payload = resp.json()
        batch = payload.get("features", [])
        features.extend(batch)
        if sample:
            break
        if len(batch) < src.page_size or payload.get("exceededTransferLimit") is False:
            break
        if not batch:
            break
        offset += len(batch)

    fc = {"type": "FeatureCollection", "features": features}
    out_path.write_text(json.dumps(fc), encoding="utf-8")
    return str(out_path)


def fetch_oapif(
    src: Source,
    workdir: Path,
    bbox: tuple[float, float, float, float] | None = None,
    sample: bool = False,
    max_features: int | None = None,
) -> str:
    """Page through an OGC API - Features collection's /items endpoint and write a GeoJSON file.

    `src.uri` should be the API root (landing page), e.g.
    https://host/ogcapi — not an /items URL. `src.layer` is the collection id.
    Always fetches the default CRS84 (EPSG:4326) response; see Source._check_oapif_crs.

    `bbox` (west, south, east, north in WGS84, matching CRS84) is pushed down as the OGC API
    `bbox` query param on the first request, so the server does the filtering instead of us
    downloading out-of-viewport features and throwing them away locally afterwards. The
    server's `next` link already carries it forward on subsequent pages.

    `sample`, if true, returns after the first page (does not follow `next`) — for callers
    that just need a representative page of features (e.g. schema inspection), not the whole
    collection.

    `max_features`, if set, stops paging as soon as at least that many features have been
    collected — for callers (e.g. preview) that only need a bounded number of rows and would
    otherwise page through an entire large collection only to discard most of it locally.

    A short-lived in-memory cache (see _OAPIF_FETCH_CACHE) serves repeat calls for the same
    collection/bbox/page_size that need no more rows than an earlier call already fetched,
    without hitting the network again.
    """
    root = src.uri.rstrip("/")
    needed = None if (max_features is None and not sample) else (max_features or src.page_size)
    cache_key = (root, src.layer, bbox, src.page_size)

    now = time.time()
    with _OAPIF_CACHE_LOCK:
        cached = _OAPIF_FETCH_CACHE.get(cache_key)
        if cached and now - cached[0] < _OAPIF_CACHE_TTL:
            cached_at, cached_features, complete = cached
            if complete or (needed is not None and len(cached_features) >= needed):
                features = cached_features if needed is None else cached_features[:needed]
                out_path = workdir / f"{src.id}.geojson"
                out_path.write_text(
                    json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8"
                )
                return str(out_path)

    url = f"{root}/collections/{src.layer}/items"
    params: dict | None = {"limit": src.page_size}
    if bbox:
        west, south, east, north = bbox
        params["bbox"] = f"{west},{south},{east},{north}"
    headers = {"Accept": "application/json"}
    out_path = workdir / f"{src.id}.geojson"

    features = []
    complete = False
    with requests.Session() as session:
        while url:
            resp = session.get(url, params=params, headers=headers, timeout=120)
            resp.raise_for_status()
            payload = resp.json()
            features.extend(payload.get("features", []))
            if sample:
                break
            if max_features is not None and len(features) >= max_features:
                break
            url = next(
                (link["href"] for link in payload.get("links", []) if link.get("rel") == "next"),
                None,
            )
            params = None  # the `next` link already carries its own query params
        else:
            complete = True

    with _OAPIF_CACHE_LOCK:
        # Prune expired entries opportunistically so the cache doesn't grow unbounded
        # over a long editing session that pans across many distinct bboxes.
        for k, v in list(_OAPIF_FETCH_CACHE.items()):
            if now - v[0] >= _OAPIF_CACHE_TTL:
                del _OAPIF_FETCH_CACHE[k]
        _OAPIF_FETCH_CACHE[cache_key] = (now, features, complete)

    fc = {"type": "FeatureCollection", "features": features}
    out_path.write_text(json.dumps(fc), encoding="utf-8")
    return str(out_path)


# Matches config.py's CRSStr format (AUTHORITY:NUMERIC_CODE, e.g. "EPSG:4326") — mirrored
# here (rather than importing config._CRS_RE) so a CRS string this module hands back is
# guaranteed to pass that validation, never surfaced as a raw pydantic error in the editor.
_CRS_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*:[0-9]+$")


def parquet_geometry_info(con: duckdb.DuckDBPyConnection, uri: str) -> tuple[str | None, str | None]:
    """(geometry_column_name, embedded_crs) for a (Geo)Parquet file's native schema.

    DuckDB reads Parquet directly via read_parquet(), not through GDAL (see read_expr's
    `parquet` branch for why), and the spatial extension recognizes GeoParquet's `geo`
    key-value metadata and types the geometry column as GEOMETRY — or, when the file's
    metadata names a CRS, as GEOMETRY('EPSG:25833'), with the CRS string embedded right
    in the DuckDB type. Returns (None, None) if there's no geometry column, the file has
    no CRS in its metadata, or the file can't be read at all.

    GeoParquet files that omit an explicit CRS default to OGC:CRS84 per the spec — very
    common (e.g. any file written without deliberately setting a CRS) — which DuckDB
    reports verbatim as GEOMETRY('OGC:CRS84'). That's WGS84 lon/lat, the same CRS this
    codebase already treats as EPSG:4326 everywhere else CRS84 shows up (see fetch_oapif,
    Source._check_oapif_crs), just under a non-numeric authority:code the app's CRS field
    can't accept (config.CRSStr requires a numeric code) — so it's normalized here. Any
    other CRS identifier that doesn't fit that AUTHORITY:NUMERIC_CODE shape is dropped
    (None) rather than handed back to the editor as a value it will reject.
    """
    try:
        rows = con.execute(f"DESCRIBE SELECT * FROM read_parquet({_sql_str(uri)})").fetchall()
    except Exception:
        return None, None
    for name, col_type, *_ in rows:
        col_type = str(col_type)
        if col_type.upper().startswith("GEOMETRY"):
            m = re.match(r"GEOMETRY\('([^']+)'\)", col_type, re.IGNORECASE)
            crs = m.group(1) if m else None
            if crs and crs.upper() == "OGC:CRS84":
                crs = "EPSG:4326"
            if crs and not _CRS_RE.match(crs):
                crs = None
            return name, crs
    return None, None


def _layer_geometry_field(con: duckdb.DuckDBPyConnection, uri: str, layer: str | None) -> dict | None:
    """Look up a layer's native geometry field (name + type) via ST_Read_Meta, without reading features."""
    try:
        (layers,) = con.execute(f"SELECT layers FROM ST_Read_Meta({_sql_str(uri)})").fetchone()
    except Exception:
        return None
    for lyr in layers or []:
        if layer and lyr["name"] != layer:
            continue
        for gf in lyr["geometry_fields"]:
            return gf
    return None


def _linearize_cache_path(uri: str, layer: str | None) -> Path | None:
    """Cache path for this (file, layer), or None if `uri` isn't a local file we can stat."""
    try:
        st = os.stat(uri)
    except OSError:
        return None
    key = f"{Path(uri).resolve()}|{layer or ''}|{st.st_mtime_ns}|{st.st_size}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return _LINEARIZE_CACHE_DIR / f"{digest}.gpkg"


def _linearize_curves(
    uri: str,
    layer: str | None,
    workdir: Path,
    source_id: str,
    log: Callable[[str], None] | None,
) -> tuple[str, str]:
    """Read a curve-geometry layer and rewrite it as linear geometry in a GeoPackage.

    DuckDB's spatial extension can't parse WKB for the ISO curve types (see
    _CURVE_MARKERS) — GDAL reads them fine, it's DuckDB's own WKB import that rejects
    them. Round-tripping through pyogrio (a separate, self-contained GDAL build) forces
    the curves to their linear approximation on the way out, the same thing
    `ogr2ogr -nlt CONVERT_TO_LINEAR` does. The result is cached (see
    _LINEARIZE_CACHE_DIR) since this is redone on every preview request otherwise.
    """
    out_layer = layer or source_id
    cache_path = _linearize_cache_path(uri, layer)
    if cache_path and cache_path.exists():
        return str(cache_path), out_layer

    try:
        import pyogrio
    except ImportError as e:
        raise RuntimeError(
            f"source '{source_id}' has curve geometry (circular arcs / compound curves) "
            "that DuckDB's spatial engine can't read directly. Install the optional "
            '"curves" extra to enable automatic linearization: pip install -e ".[curves]"'
        ) from e

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        meta, _fids, geometry, field_data = pyogrio.raw.read(uri, layer=layer)

    # Write to workdir first and move into place, so a request that reads the cache
    # concurrently with one still writing it never sees a partially-written file.
    tmp_out = workdir / f"{source_id}_linearized.gpkg"
    pyogrio.raw.write(
        str(tmp_out), geometry, field_data, fields=meta["fields"],
        layer=out_layer, crs=meta["crs"], geometry_type=meta["geometry_type"],
        encoding=meta["encoding"],
    )
    if log:
        msg = f"source '{source_id}': linearized curve geometry -> {meta['geometry_type']}"
        if any("measured" in str(w.message).lower() for w in caught):
            msg += " (M/measure dimension dropped, not supported downstream)"
        log(msg)

    if cache_path:
        _LINEARIZE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp_out), str(cache_path))
        return str(cache_path), out_layer
    return str(tmp_out), out_layer


def _maybe_linearize(
    uri: str,
    layer: str | None,
    workdir: Path,
    source_id: str,
    con: duckdb.DuckDBPyConnection | None,
    log: Callable[[str], None] | None,
) -> tuple[str, str | None]:
    if con is None:
        return uri, layer
    gfield = _layer_geometry_field(con, uri, layer)
    gtype = gfield["type"] if gfield else None
    if gtype and any(marker in gtype.lower() for marker in _CURVE_MARKERS):
        return _linearize_curves(uri, layer, workdir, source_id, log)
    return uri, layer


def _geometry_column_via_describe(con: duckdb.DuckDBPyConnection, base: str) -> str | None:
    """Find a table expression's GEOMETRY-typed column by DESCRIBEing it, rather than via
    ST_Read_Meta. Used when ST_Read_Meta can't be trusted to report the field (see callers).
    """
    rows = con.execute(f"DESCRIBE SELECT * FROM {base}").fetchall()
    return next((r[0] for r in rows if str(r[1]).upper().startswith("GEOMETRY")), None)


def _read_expr_normalized(
    uri: str,
    layer: str | None,
    con: duckdb.DuckDBPyConnection | None,
    extra: str = "",
) -> str:
    """ST_Read(...) for this file, with its geometry field (if any) aliased to `geom`.

    engine.py hard-codes the geometry column as `geom` everywhere (source views, joins,
    the final mapped output) — a reasonable assumption for GeoJSON/most GDAL output, but
    plenty of real GeoPackages name it whatever the authoring tool used (Esri's `shape`
    is common; some of the Bane NOR layers in this repo use it). Without this rename,
    `SELECT * EXCLUDE (geom), ... FROM ST_Read(...)` fails at the first source view with
    "Column geom in EXCLUDE list not found in FROM clause" for any such source.

    `extra` (e.g. an `open_options=[...]` clause) only ever affects field *types* here, not
    whether a geometry field exists — ST_Read_Meta (which the geometry-field lookup below
    goes through) has no way to receive it and always opens the file with GDAL's bare
    defaults, but that's fine for every caller of this function, none of which use `extra`
    to conjure a geometry field into existence. See `_read_csv_geometry` for the one case
    (csv X_POSSIBLE_NAMES/GEOM_POSSIBLE_NAMES) where the open options themselves are what
    create the geometry field, which needs a different detection path entirely.

    ST_Read_Meta also isn't reliable for every *format* GDAL itself opens fine — confirmed
    empirically for a File Geodatabase (an Esri "NVE" gdb export): `ST_Read_Meta(path)`
    returned zero rows outright, even though `ST_Read(path)` opened its layer and read rows
    without complaint. When that happens, `_layer_geometry_field` comes back empty and this
    used to silently skip the rename, so engine.py's hard-coded `EXCLUDE (geom)` blew up on
    the real column name (e.g. Esri's `SHAPE`) with a binder error. Falling back to the same
    DESCRIBE-based detection `_read_csv_geometry`/`parquet_geometry_info` already use covers
    this — it's slightly more work than the metadata lookup, but only for the formats where
    the metadata lookup already came back empty-handed.
    """
    base = _st_read(uri, layer=layer, extra=extra)
    if con is None:
        return base
    gfield = _layer_geometry_field(con, uri, layer)
    name = gfield["name"] if gfield else _geometry_column_via_describe(con, base)
    if not name or name == "geom":
        return base
    return f"(SELECT * EXCLUDE ({_sql_ident(name)}), {_sql_ident(name)} AS geom FROM {base})"


def _open_options_arg(opts: list[str]) -> str:
    """`open_options=['...', ...]` fragment for `_st_read`'s `extra` param, or "" if `opts` is empty."""
    if not opts:
        return ""
    return f"open_options=[{', '.join(_sql_str(o) for o in opts)}]"


def _header_open_option(fmt: str, header_row: bool) -> str:
    """GDAL open option forcing header-row detection on/off for xlsx/csv (Source.header_row).

    Left at GDAL's own AUTO default (Source.header_row = None) whenever it's not set — AUTO
    guesses from the first row's apparent field types, which is wrong often enough to want an
    override: a header row that happens to look like the data below it, or an all-numeric
    first data row (e.g. postal codes) mistaken for headers.
    """
    if fmt == "xlsx":
        return "HEADERS=FORCE" if header_row else "HEADERS=DISABLE"
    return "HEADERS=YES" if header_row else "HEADERS=NO"


def _csv_geometry_open_options(src: Source) -> list[str]:
    """GDAL CSV driver open options that turn plain columns into a geometry field.

    XLSX has no equivalent — its driver is purely tabular, with no geometry-related open
    options at all — so this is csv-only (see config.Source._check_geometry_fields_format).
    """
    opts = []
    if src.x_field and src.y_field:
        opts.append(f"X_POSSIBLE_NAMES={src.x_field}")
        opts.append(f"Y_POSSIBLE_NAMES={src.y_field}")
    if src.geom_field:
        opts.append(f"GEOM_POSSIBLE_NAMES={src.geom_field}")
    if opts:
        # Without this (GDAL's KEEP_GEOM_COLUMNS default is YES), GEOM_POSSIBLE_NAMES keeps
        # the matched column as a regular attribute field *with the same name* as the
        # geometry field GDAL derives from it, and DuckDB refuses to `SELECT *` a layer with
        # two identically-named columns ("duplicate column name") — confirmed empirically.
        # Dropping the raw column is also the more sensible default for X_POSSIBLE_NAMES/
        # Y_POSSIBLE_NAMES: the source columns are consumed into `geom`, not duplicated —
        # lon/lat can always be recovered downstream via the `lon`/`lat` mapping funcs
        # (engine.py), which derive from geometry anyway.
        opts.append("KEEP_GEOM_COLUMNS=NO")
    return opts


def _read_csv_geometry(uri: str, con: duckdb.DuckDBPyConnection | None, extra: str) -> str:
    """Like `_read_expr_normalized`, but for a csv whose geometry field only exists because
    of `extra`'s open options (X_POSSIBLE_NAMES/Y_POSSIBLE_NAMES or GEOM_POSSIBLE_NAMES).

    `_read_expr_normalized` finds the geometry field via ST_Read_Meta, but ST_Read_Meta has
    no `open_options` parameter at all (verified against duckdb-spatial's source) — it always
    opens the file with GDAL's bare defaults, so it can never see a field that only appears
    because of these options. DESCRIBEing the actual ST_Read(...) call instead and looking for
    the GEOMETRY-typed column is the only way to find out what GDAL really produced — the same
    workaround `parquet_geometry_info` already needs for Parquet, which has the identical
    blind spot.
    """
    base = _st_read(uri, extra=extra)
    if con is None:
        return base
    name = _geometry_column_via_describe(con, base)
    if not name or name == "geom":
        return base
    return f"(SELECT * EXCLUDE ({_sql_ident(name)}), {_sql_ident(name)} AS geom FROM {base})"


def read_expr(
    src: Source,
    workdir: Path,
    con: duckdb.DuckDBPyConnection | None = None,
    log: Callable[[str], None] | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    sample: bool = False,
    max_features: int | None = None,
) -> str:
    """Return a SQL table expression (ST_Read(...)) for this source.

    When `con` (a DuckDB connection with the spatial extension loaded) is given,
    file-based sources are checked for ISO curve geometry (which DuckDB can't parse)
    and transparently linearized first — see `_maybe_linearize` — and their geometry
    field is aliased to `geom` if it isn't already — see `_read_expr_normalized`.

    `bbox` and `max_features` are only honored for `oapif` sources — see `fetch_oapif`.
    `sample` is honored by both `oapif` and `arcgis_rest`.
    """
    fmt = src.format

    if fmt == "arcgis_rest":
        path = fetch_arcgis_rest(src, workdir, sample=sample)
        return _st_read(path)

    if fmt == "oapif":
        path = fetch_oapif(src, workdir, bbox=bbox, sample=sample, max_features=max_features)
        return _st_read(path)

    if fmt == "wfs":
        path = fetch_wfs(src, workdir)
        uri, layer = _maybe_linearize(path, None, workdir, src.id, con, log)
        return _read_expr_normalized(uri, layer, con)

    if fmt == "parquet":
        # No layer concept for a single flat Parquet file, and no GDAL curve types to
        # worry about — this path never touches GDAL at all (see module docstring).
        base = f"read_parquet({_sql_str(src.uri)})"
        if con is None:
            return base
        name, _crs = parquet_geometry_info(con, src.uri)
        if not name or name == "geom":
            return base
        return f"(SELECT * EXCLUDE ({_sql_ident(name)}), {_sql_ident(name)} AS geom FROM {base})"

    if fmt in ("gpkg", "fgdb", "shp", "geojson", "gml", "flatgeobuf", "xlsx", "csv"):
        # GDAL picks the driver from the path/extension; layer/sheet is optional.
        uri, layer = src.uri, src.layer
        if fmt in ("gpkg", "fgdb", "gml"):
            uri, layer = _maybe_linearize(uri, layer, workdir, src.id, con, log)
        open_opts: list[str] = []
        if fmt in ("xlsx", "csv") and src.header_row is not None:
            open_opts.append(_header_open_option(fmt, src.header_row))
        csv_geom_opts = _csv_geometry_open_options(src) if fmt == "csv" else []
        open_opts += csv_geom_opts
        extra = _open_options_arg(open_opts)
        if csv_geom_opts:
            return _read_csv_geometry(uri, con, extra)
        return _read_expr_normalized(uri, layer, con, extra=extra)

    raise ValueError(f"unsupported source format: {fmt}")
