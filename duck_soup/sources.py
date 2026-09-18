"""Turn a Source config into something DuckDB can read.

Almost everything goes through DuckDB's ST_Read (which uses GDAL), so adding a
new vector format is usually just a new branch that builds the right GDAL
connection string. ArcGIS REST and OGC API - Features (oapif) are the exception:
both are fetched with pagination to a temporary GeoJSON file, which is then read
with ST_Read like anything else. Parquet is the other exception: the GDAL build
bundled with DuckDB's spatial extension has no Parquet/Arrow driver, so it's read
with DuckDB's own native read_parquet() instead (see read_expr's `parquet` branch).
postgres is a third exception, for the same reason: it's read via DuckDB's own
postgres extension (ATTACH ... TYPE postgres) rather than GDAL's PG driver, which
isn't part of the bundled spatial-extension GDAL build either.
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
from .sql_util import quote_ident as _sql_ident, quote_literal as _sql_str

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


def _split_pg_layer(layer: str | None) -> tuple[str, str]:
    """(schema, table) from a Source.layer value, defaulting schema to 'public'."""
    if not layer:
        raise ValueError("postgres sources require 'layer' to name a table (optionally 'schema.table')")
    if "." in layer:
        schema, _, table = layer.partition(".")
        return schema, table
    return "public", layer


def attach_postgres(con: duckdb.DuckDBPyConnection, dsn: str, source_id: str) -> str:
    """ATTACH a postgres connection string under a stable per-source alias, idempotently.

    Aliased by source id (not by dsn) so re-running a pipeline against the same source
    twice on one connection doesn't try to ATTACH the same alias twice, while different
    postgres sources in one pipeline still get distinct catalogs.
    """
    alias = "pg_" + re.sub(r"\W", "_", source_id)
    attached = {row[0] for row in con.execute("SELECT database_name FROM duckdb_databases()").fetchall()}
    if alias not in attached:
        con.execute(f"ATTACH {_sql_str(dsn)} AS {_sql_ident(alias)} (TYPE postgres)")
    return alias


def postgres_geometry_info(
    con: duckdb.DuckDBPyConnection, alias: str, schema: str, table: str
) -> tuple[str | None, str | None]:
    """(geometry_column_name, EPSG:<srid>) for a table in an attached postgres catalog.

    PostGIS registers every spatial table's geometry column in the `geometry_columns`
    view; queried the same way as any other view through the attached catalog. Returns
    (None, None) if the table has no geometry column (or isn't PostGIS-enabled at all).
    """
    try:
        rows = con.execute(
            f"SELECT f_geometry_column, srid FROM {_sql_ident(alias)}.public.geometry_columns "
            f"WHERE f_table_schema = {_sql_str(schema)} AND f_table_name = {_sql_str(table)}"
        ).fetchall()
    except Exception:
        return None, None
    if not rows:
        return None, None
    name, srid = rows[0]
    crs = f"EPSG:{srid}" if srid else None
    return name, crs


# ---------------------------------------------------------------------------
# Layer discovery — "what can I pick for this source's `layer`?", per format.
# Each returns the layer list (and the default CRS where the format exposes one).
# ---------------------------------------------------------------------------

def list_postgres_tables(
    con: duckdb.DuckDBPyConnection, dsn: str
) -> tuple[list[str], str | None]:
    """PostGIS-enabled tables (from geometry_columns) plus plain tables.

    Plain tables are included because they're usable as attribute-join lookup sources.
    Each is "schema.table" unless schema is the default 'public'. Default CRS is the
    first spatial table's SRID.
    """
    alias = attach_postgres(con, dsn, "inspect")
    layers: list[str] = []
    default_crs: str | None = None
    geom_rows = con.execute(
        f"SELECT f_table_schema, f_table_name, srid FROM {_sql_ident(alias)}.public.geometry_columns "
        "ORDER BY f_table_schema, f_table_name"
    ).fetchall()
    seen: set[str] = set()
    for schema, table, srid in geom_rows:
        full = table if schema == "public" else f"{schema}.{table}"
        seen.add(full)
        layers.append(full)
        if default_crs is None and srid:
            default_crs = f"EPSG:{srid}"
    table_rows = con.execute(
        f"SELECT table_schema, table_name FROM {_sql_ident(alias)}.information_schema.tables "
        "WHERE table_type = 'BASE TABLE' ORDER BY table_schema, table_name"
    ).fetchall()
    for schema, table in table_rows:
        full = table if schema == "public" else f"{schema}.{table}"
        if full not in seen:
            seen.add(full)
            layers.append(full)
    return layers, default_crs


def list_wfs_layers(uri: str) -> tuple[list[str], str | None]:
    """WFS feature types via GetCapabilities — more reliable than GDAL's WFS driver."""
    from urllib.parse import urlparse, urlencode, parse_qsl
    from xml.etree import ElementTree

    layers: list[str] = []
    default_crs: str | None = None
    raw = uri[4:] if uri.upper().startswith("WFS:") else uri
    p = urlparse(raw)
    qs_clean = [(k, v) for k, v in parse_qsl(p.query)
                if k.lower() not in ("request", "service", "version")]
    qs_caps = urlencode(qs_clean + [("SERVICE", "WFS"), ("REQUEST", "GetCapabilities")])
    caps_url = p._replace(query=qs_caps).geturl()
    resp = requests.get(caps_url, timeout=30)
    resp.raise_for_status()
    root = ElementTree.fromstring(resp.content)
    for ns in ("http://www.opengis.net/wfs/2.0", "http://www.opengis.net/wfs"):
        for ft in root.iter(f"{{{ns}}}FeatureType"):
            name_el = ft.find(f"{{{ns}}}Name")
            if name_el is not None and name_el.text:
                layers.append(name_el.text.strip())
            if not default_crs:
                # NB: `find(...) or find(...)` is wrong here — Element.__bool__ is
                # based on child-element count, not identity, so a real match on a
                # leaf element like <DefaultCRS>EPSG::4258</DefaultCRS> (no children)
                # is falsy and silently discarded in favor of the second find().
                crs_el = ft.find(f"{{{ns}}}DefaultCRS")
                if crs_el is None:
                    crs_el = ft.find(f"{{{ns}}}DefaultSRS")
                if crs_el is not None and crs_el.text:
                    raw_crs = crs_el.text.strip()
                    # Normalise urn:ogc:def:crs:EPSG::4258 → EPSG:4258
                    m = re.search(r"EPSG[:_]+([\w]+)$", raw_crs, re.IGNORECASE)
                    if m:
                        default_crs = f"EPSG:{m.group(1)}"
        if layers:
            break
    return layers, default_crs


def list_oapif_collections(uri: str) -> list[str]:
    """Collection ids from an OGC API - Features endpoint.

    The plain URL without an Accept header returns the server's HTML front-end, not data.
    """
    resp = requests.get(
        f"{uri.rstrip('/')}/collections",
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return [c["id"] for c in data.get("collections", []) if c.get("id")]


def list_arcgis_rest_layers(uri: str) -> list[dict]:
    """Sublayers (and standalone tables) from ArcGIS REST's service-info endpoint."""
    resp = requests.get(f"{uri.rstrip('/')}?f=json", timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        # ArcGIS REST returns HTTP 200 with {"error": {...}} for a bad url.
        raise RuntimeError(data["error"].get("message", "ArcGIS REST service error"))
    layers = []
    for entry in (data.get("layers") or []) + (data.get("tables") or []):
        lid = entry.get("id")
        if lid is None:
            continue
        name = entry.get("name")
        layers.append({"value": str(lid), "label": f"{lid} - {name}" if name else str(lid)})
    return layers


def list_gdal_layers(
    con: duckdb.DuckDBPyConnection, target: str
) -> tuple[list[str], str | None]:
    """Layer names + first layer's CRS for anything GDAL can open, via ST_Read_Meta."""
    layers: list[str] = []
    default_crs: str | None = None
    res = con.execute(f"SELECT layers FROM ST_Read_Meta({_sql_str(target)})").fetchall()
    if res and len(res) > 0 and res[0][0]:
        for layer in res[0][0]:
            layer_name = layer.get("name")
            if layer_name:
                layers.append(layer_name)

            geom_fields = layer.get("geometry_fields")
            if geom_fields and isinstance(geom_fields, list) and len(geom_fields) > 0:
                crs_info = geom_fields[0].get("crs")
                if crs_info and isinstance(crs_info, dict):
                    auth_name = crs_info.get("auth_name")
                    auth_code = crs_info.get("auth_code")
                    if auth_name and auth_code and not default_crs:
                        default_crs = f"{auth_name}:{auth_code}"
    return layers, default_crs


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
    whether a geometry field exists — none of this function's callers use it to conjure a
    geometry field into existence (see `_apply_tabular_geometry` for xlsx/csv geometry-from-
    columns, which is deliberately built in SQL afterwards instead, precisely to avoid that).

    ST_Read_Meta isn't reliable for every *format* GDAL itself opens fine, though — confirmed
    empirically for a File Geodatabase (an Esri "NVE" gdb export): `ST_Read_Meta(path)`
    returned zero rows outright, even though `ST_Read(path)` opened its layer and read rows
    without complaint. When that happens, `_layer_geometry_field` comes back empty and this
    used to silently skip the rename, so engine.py's hard-coded `EXCLUDE (geom)` blew up on
    the real column name (e.g. Esri's `SHAPE`) with a binder error. Falling back to the same
    DESCRIBE-based detection `parquet_geometry_info` already needs for Parquet covers this —
    it's slightly more work than the metadata lookup, but only for the formats where the
    metadata lookup already came back empty-handed.
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


def _apply_tabular_geometry(base: str, src: Source) -> str:
    """Wrap a plain tabular read (xlsx/csv, no native geometry) with a computed `geom`
    column, for a source naming x/y or WKT/WKB columns via Source.x_field/y_field/geom_field.

    This is deliberately *not* done via GDAL open options (X_POSSIBLE_NAMES/Y_POSSIBLE_NAMES/
    GEOM_POSSIBLE_NAMES): those only exist on the CSV driver — XLSX's driver has no
    geometry support at all, open options or otherwise — and even for csv they came with
    real sharp edges: ST_Read_Meta has no `open_options` parameter, so it's blind to a field
    that only exists because of them (needing a separate DESCRIBE-based detection path), and
    GDAL's KEEP_GEOM_COLUMNS default duplicates the matched column under the same name as
    the geometry field it derives, which DuckDB then refuses to `SELECT *` at all
    ("duplicate column name") — confirmed empirically. Building the geometry directly in SQL
    sidesteps all of that and works identically for both formats.
    """
    if src.x_field and src.y_field:
        x, y = _sql_ident(src.x_field), _sql_ident(src.y_field)
        geom = f"ST_Point(TRY_CAST({x} AS DOUBLE), TRY_CAST({y} AS DOUBLE))"
        exclude = f"{x}, {y}"
    elif src.geom_field:
        g = _sql_ident(src.geom_field)
        # A hex-WKB string (e.g. PostGIS's extended WKB) and WKT text look nothing alike, so
        # branch on whether it's pure hex digits — mirrors what GDAL's own GEOM_POSSIBLE_NAMES
        # auto-detection distinguishes between (minus its GeoJSON-text branch: a single "one
        # geometry column" xlsx/csv export is WKT or WKB in practice, not GeoJSON).
        geom = (
            f"CASE WHEN {g} IS NULL OR trim({g}) = '' THEN NULL "
            f"WHEN regexp_matches({g}, '^[0-9A-Fa-f]+$') THEN ST_GeomFromHEXWKB({g}) "
            f"ELSE CAST({g} AS GEOMETRY) END"
        )
        exclude = g
    else:
        return base
    return f"(SELECT * EXCLUDE ({exclude}), {geom} AS geom FROM {base})"


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

    if fmt == "postgres":
        # No GDAL involved at all here either — see module docstring.
        if con is None:
            raise ValueError(f"source '{src.id}': postgres sources require a live DuckDB connection")
        schema, table = _split_pg_layer(src.layer)
        alias = attach_postgres(con, src.uri, src.id)
        base = f"{_sql_ident(alias)}.{_sql_ident(schema)}.{_sql_ident(table)}"
        geom_col, _crs = postgres_geometry_info(con, alias, schema, table)
        if not geom_col:
            return base
        g = _sql_ident(geom_col)
        # The postgres extension doesn't recognize PostGIS's `geometry` type, so it comes
        # through as VARCHAR hex-EWKB text (PostGIS's default text output for that column) —
        # ST_GeomFromHEXWKB parses that directly, same as the xlsx/csv geom_field branch below.
        geom = f"CASE WHEN {g} IS NULL THEN NULL ELSE ST_GeomFromHEXWKB({g}) END"
        return f"(SELECT * EXCLUDE ({g}), {geom} AS geom FROM {base})"

    if fmt in ("gpkg", "fgdb", "shp", "geojson", "gml", "flatgeobuf", "xlsx", "csv"):
        # GDAL picks the driver from the path/extension; layer/sheet is optional.
        uri, layer = src.uri, src.layer
        if fmt in ("gpkg", "fgdb", "gml"):
            uri, layer = _maybe_linearize(uri, layer, workdir, src.id, con, log)
        extra = ""
        if fmt in ("xlsx", "csv") and src.header_row is not None:
            extra = _open_options_arg([_header_open_option(fmt, src.header_row)])
        base = _read_expr_normalized(uri, layer, con, extra=extra)
        if fmt in ("xlsx", "csv") and (src.x_field or src.geom_field):
            base = _apply_tabular_geometry(base, src)
        return base

    raise ValueError(f"unsupported source format: {fmt}")
