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
Plain (non-GeoJSON) JSON is read with DuckDB's native read_json() — see `_json_read_expr`.
Finally, on Windows a local file of 2 GiB or more is read through pyogrio instead of
ST_Read, which segfaults on such files there — see `_read_via_pyogrio`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import warnings
from contextlib import contextmanager
from contextvars import ContextVar
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

# Large csv/xlsx files are read from a cached Parquet copy — see `_tabular_parquet_cache`.
# Below this size GDAL opens them fast enough that the copy isn't worth it.
_TABULAR_CACHE_DIR = Path(tempfile.gettempdir()) / "duck_soup_tabular_cache"
_TABULAR_CACHE_MIN_BYTES = 5 * 2**20

# On Windows, DuckDB's spatial extension (confirmed through duckdb 1.5.6) segfaults —
# taking the whole process down, web server included — once ST_Read gets past the 2 GiB
# mark of a file. Small reads (a LIMITed preview, a schema sample) finish before reaching
# that point, which is why such a file inspects fine and then crashes on a full scan (a
# join against it, or an "in view" preview of features stored late in the file). Files at
# or above this size are read through pyogrio instead (see `_read_via_pyogrio`). None
# disables the fallback; elsewhere ST_Read handles large files fine.
_ST_READ_MAX_BYTES: int | None = 2**31 if sys.platform == "win32" else None

# Rows read for a schema-inspection sample (read_expr's `sample=True`) on the pyogrio path —
# enough for /api/inspect's CRS sanity check, which looks at up to 500 features.
_PYOGRIO_SAMPLE_ROWS = 500

# Formats the pyogrio fallback covers: plain GDAL vector files. xlsx/csv are left on ST_Read,
# since their header-row open options and SQL-built geometry are ST_Read-specific.
_PYOGRIO_FORMATS = ("gpkg", "fgdb", "shp", "geojson", "gml", "flatgeobuf")

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


# Remote file sources (an http(s) uri on a gpkg/geojson/csv/... source) are downloaded once
# to a local file instead of being handed to ST_Read as a URL. GDAL reads URLs through
# /vsicurl/, which does a HEAD and then many small Range requests — fine against a static
# file host, but an export endpoint that builds the file per request (no Content-Length, no
# Range support, e.g. NVDB's segmentert.csv) answers every one of those with the whole
# body, so a single ST_Read turns into dozens of full downloads and times out.
#
# Downloads are kept in the temp dir until replaced. Previews (the editor's inspect /
# preview / counts) read the newest download of a URL on disk, whichever process made it and
# across editor restarts, until the user refreshes it (prepare_source(refresh=True), the
# source card's "refresh" link): a dataset rarely changes between two previews, and fetching
# it again costs as long as the download takes. A run (run_config: `duck-soup run`, the
# editor's Run) downloads fresh instead, see fresh_downloads, so its output reflects the
# current data.
_REMOTE_CACHE_DIR = Path(tempfile.gettempdir()) / "duck_soup_remote_cache"
_REMOTE_CACHE_LOCK = threading.Lock()

# How old a superseded download (or its Parquet copy) must be before it's deleted. Every
# read looks up the newest download, but a query already running against an older one (a
# long run in another process) re-opens its file as it goes, so it mustn't disappear at once.
_SUPERSEDED_GRACE = 3600.0  # seconds

# Set by fresh_downloads() for the duration of a run: a download made before this time is
# fetched again.
_FRESH_AFTER: ContextVar[float | None] = ContextVar("duck_soup_fresh_after", default=None)


@contextmanager
def fresh_downloads():
    """Within this block (a run), remote file sources are downloaded again instead of reusing
    an earlier download, once per URL: a download made inside the block is reused, so a URL
    read by several pipelines is still only fetched once."""
    token = _FRESH_AFTER.set(time.time())
    try:
        yield
    finally:
        _FRESH_AFTER.reset(token)


def _superseded_long_enough(path: Path) -> bool:
    try:
        return time.time() - path.stat().st_mtime > _SUPERSEDED_GRACE
    except OSError:
        return False


# What a slow source is busy with right now (downloading, converting to Parquet), keyed by
# the source's uri as configured. Reported back by prepare_source, which the editor's
# endpoints call before reading. Each stage removes its own entry when it ends.
_PROGRESS: dict[str, dict] = {}
_PROGRESS_LOCK = threading.Lock()


def _set_progress(key: str | None, stage: str, **fields) -> None:
    if not key:
        return
    with _PROGRESS_LOCK:
        prev = _PROGRESS.get(key)
        started = prev["_started"] if prev and prev["stage"] == stage else time.monotonic()
        _PROGRESS[key] = {"stage": stage, "_started": started, **fields}


def _clear_progress(key: str | None, stage: str) -> None:
    with _PROGRESS_LOCK:
        if key and _PROGRESS.get(key, {}).get("stage") == stage:
            del _PROGRESS[key]


def _clear_all_progress(key: str) -> None:
    with _PROGRESS_LOCK:
        _PROGRESS.pop(key, None)


def source_progress(key: str) -> dict | None:
    """The current slow stage for a source uri, or None: {"stage": "download", "bytes": n,
    "total": n or None (no Content-Length), "elapsed": s} or {"stage": "convert", ...}."""
    with _PROGRESS_LOCK:
        p = _PROGRESS.get(key)
        if p is None:
            return None
        out = {k: v for k, v in p.items() if not k.startswith("_")}
        out["elapsed"] = round(time.monotonic() - p["_started"], 1)
        return out


def is_remote_uri(uri: str) -> bool:
    return uri.lower().startswith(("http://", "https://"))


def should_download(uri: str, fmt: str) -> bool:
    """Whether read_expr downloads this remote uri first (fetch_remote_file). Not a bare
    .shp, whose .shx/.dbf sidecars only GDAL's own /vsicurl/ reader fetches alongside it."""
    from urllib.parse import urlparse

    if not is_remote_uri(uri):
        return False
    return not (fmt == "shp" and urlparse(uri).path.lower().endswith(".shp"))


def remote_file_name(uri: str, fmt: str) -> str:
    """The local file name fetch_remote_file gives a download of `uri`: the URL's own name
    (query string dropped), plus the format's extension when it has none."""
    from urllib.parse import unquote, urlparse

    name = re.sub(r'[<>:"/\\|?*]', "_", unquote(Path(urlparse(uri).path).name)) or fmt
    return name if Path(name).suffix else f"{name}.{fmt}"


def _url_dir(uri: str) -> Path:
    return _REMOTE_CACHE_DIR / hashlib.sha1(uri.encode()).hexdigest()[:16]


def latest_download(uri: str, fmt: str) -> Path | None:
    """The newest complete download of `uri` on disk (by any process), or None. Downloads sit
    at <url hash>/<download time, ns>/<name>; one still in progress has no <name> yet."""
    name = remote_file_name(uri, fmt)
    try:
        folders = [f for f in _url_dir(uri).iterdir() if f.is_dir() and f.name.isdigit()]
    except OSError:
        return None
    for folder in sorted(folders, key=lambda f: int(f.name), reverse=True):
        if (folder / name).is_file():
            return folder / name
    return None


def download_time(path: Path) -> float:
    """When a download (a latest_download path) was made, as a Unix timestamp."""
    return int(Path(path).parent.name) / 1e9


def fetch_remote_file(
    uri: str,
    fmt: str,
    log: Callable[[str], None] | None = None,
    progress_key: str | None = None,
    fresh: bool = False,
) -> str:
    """Download a remote file source to a local cache file (see _REMOTE_CACHE_DIR) and return
    its path, or the newest earlier download of it unless `fresh` or inside fresh_downloads(). The file keeps the URL's own name, since GDAL derives a csv/geojson layer name
    from it and picks drivers from compound suffixes like `.gdb.zip`; a name without an
    extension gets the format's (`fmt`).

    Each download goes into its own folder, `<url hash>/<download time>/<name>`, rather than
    overwriting the previous one: on Windows a file another process still has open (the
    editor server while a CLI run re-downloads, or the reverse) can't be replaced. Older
    downloads of the same URL are removed after _SUPERSEDED_GRACE.

    Progress is published under `progress_key` (default: `uri`), see source_progress."""
    with _REMOTE_CACHE_LOCK:
        latest = latest_download(uri, fmt)
        fresh_after = _FRESH_AFTER.get()
        if latest and not fresh and (fresh_after is None or download_time(latest) >= fresh_after):
            return str(latest)

        name = remote_file_name(uri, fmt)
        url_dir = _url_dir(uri)
        # The download time names the folder (latest_download picks the highest). Windows'
        # clock ticks every few ms, so step past a name another download already took.
        url_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.time_ns()
        while True:
            folder = url_dir / str(stamp)
            try:
                folder.mkdir()
                break
            except FileExistsError:
                stamp += 1
        out = folder / name
        tmp = out.with_name(out.name + ".part")
        if log:
            log(f"Downloading {uri}")
        start = time.monotonic()
        key = progress_key or uri
        _set_progress(key, "download", bytes=0, total=None)
        try:
            # (connect, read) timeouts: the read timeout is per socket read, not the whole
            # body, so a slow-to-start export that then streams steadily still gets through.
            with requests.get(uri, stream=True, timeout=(30, 300)) as resp:
                resp.raise_for_status()
                length = getattr(resp, "headers", {}).get("Content-Length")
                # A per-request export (chunked, or a bogus `Content-Length: 0`) has no size.
                total = int(length) if length and length.isdigit() and int(length) > 0 else None
                done = 0
                with open(tmp, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1 << 18):
                        f.write(chunk)
                        done += len(chunk)
                        _set_progress(key, "download", bytes=done, total=total)
        finally:
            _clear_progress(key, "download")
        os.replace(tmp, out)
        if log:
            log(f"Downloaded {out.stat().st_size / 2**20:.1f} MiB in {time.monotonic() - start:.1f}s")
        for old in url_dir.iterdir():
            if old == folder or not _superseded_long_enough(old):
                continue
            try:  # still open elsewhere (Windows) → left for the next download to remove
                shutil.rmtree(old) if old.is_dir() else old.unlink()
            except OSError:
                pass
        return str(out)


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

# Aliases GDAL/PROJ use for WGS84-lon/lat that aren't the AUTHORITY:NUMERIC_CODE shape
# config.py's CRS field requires: "OGC:CRS84" is GeoParquet/GeoJSON's default per-spec CRS
# (see parquet_geometry_info), "CRS:84" is the older OGC WMS 1.3 CRS-namespace spelling of
# the same thing, sometimes reported by GDAL's ST_Read_Meta for other formats (see
# list_gdal_layers). Every transform in this codebase runs with always_xy := true, so the
# axis-order distinction these aliases formally encode never matters here — normalizing to
# EPSG:4326 is safe and keeps the editor's auto-detected CRS field showing a value it (and
# the user) recognizes, rather than a technically-equivalent but unfamiliar alias.
_CRS84_ALIASES = {"OGC:CRS84", "CRS:84"}


def _normalize_crs(crs: str | None) -> str | None:
    """A GDAL/PROJ-reported CRS string, normalized to config.py's AUTHORITY:NUMERIC_CODE
    shape: CRS84 aliases become "EPSG:4326", anything else non-conforming is dropped."""
    if not crs:
        return None
    if crs.upper() in _CRS84_ALIASES:
        return "EPSG:4326"
    return crs if _CRS_RE.match(crs) else None


# Loose bounds for "this looks like WGS84 lon/lat degrees" — a bit past the true [-180,180]
# x [-90,90] range to tolerate antimeridian-crossing extents and floating-point slop.
_LONLAT_BOUND_X = 180.5
_LONLAT_BOUND_Y = 90.5


def crs_extent_warning(
    crs: str | None,
    xmin: float | None,
    xmax: float | None,
    ymin: float | None,
    ymax: float | None,
) -> str | None:
    """A human-readable nudge if a source's declared `crs` looks inconsistent with the
    coordinate magnitudes actually sampled from it, or None if there's nothing to flag.

    This is deliberately a magnitude heuristic, not real CRS validation — there's no CRS
    authority database in this codebase to check "is EPSG:25833 really a projected CRS"
    against (see _normalize_crs), so it only ever compares against EPSG:4326, the one
    geographic CRS this codebase already special-cases everywhere. The mismatch it catches
    is the single most common real-world CRS mistake: a source labeled EPSG:4326 whose
    values are clearly meters (UTM/State Plane/etc.), or the reverse — a source labeled with
    some other CRS whose values are clearly lon/lat degrees. A real EPSG:4326 dataset with
    an oddly tiny extent, or a real projected dataset that happens to have small-magnitude
    coordinates (rare, but not impossible near a projection's false origin), can still slip
    past or trip this — it's a hint to double-check, not a hard error.
    """
    if xmin is None or xmax is None or ymin is None or ymax is None or not crs:
        return None
    looks_lonlat = (
        -_LONLAT_BOUND_X <= xmin <= _LONLAT_BOUND_X
        and -_LONLAT_BOUND_X <= xmax <= _LONLAT_BOUND_X
        and -_LONLAT_BOUND_Y <= ymin <= _LONLAT_BOUND_Y
        and -_LONLAT_BOUND_Y <= ymax <= _LONLAT_BOUND_Y
    )
    is_4326 = crs.upper() == "EPSG:4326"
    extent = f"x:[{xmin:.1f}, {xmax:.1f}] y:[{ymin:.1f}, {ymax:.1f}]"
    if is_4326 and not looks_lonlat:
        return (
            f"Coordinates range over {extent} — too large to be lon/lat degrees, but this "
            "source's CRS is set to EPSG:4326. It's probably a projected CRS instead "
            "(e.g. a UTM zone) — check the source data's real CRS."
        )
    if not is_4326 and looks_lonlat:
        return (
            f"Coordinates range over {extent} — that looks like lon/lat degrees, but this "
            f"source's CRS is set to {crs}. Did you mean EPSG:4326?"
        )
    return None


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
            crs = _normalize_crs(m.group(1) if m else None)
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
                        default_crs = _normalize_crs(f"{auth_name}:{auth_code}")
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

    import pyogrio

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


def _local_file_size(uri: str) -> int:
    """Size in bytes of a local file, or of the largest file inside a directory source (a
    .gdb is a folder, and GDAL opens each of its files separately). 0 if `uri` isn't a
    local path we can stat (a URL, a /vsi path, ...)."""
    p = Path(uri)
    try:
        if p.is_dir():
            return max((f.stat().st_size for f in p.iterdir() if f.is_file()), default=0)
        return p.stat().st_size
    except OSError:
        return 0


def needs_pyogrio_reader(uri: str) -> bool:
    """True when `uri` is too large for ST_Read on this platform (see _ST_READ_MAX_BYTES)."""
    return _ST_READ_MAX_BYTES is not None and _local_file_size(uri) >= _ST_READ_MAX_BYTES


def _mb(n: int) -> str:
    return f"{n / 1e6:.1f} MB"


def _read_as_parquet(src: Source, local: str) -> bool:
    return (
        src.format in ("csv", "xlsx")
        and _local_file_size(local) >= _TABULAR_CACHE_MIN_BYTES
        and not Path(local).is_dir()
    )


def download_info(src: Source) -> dict | None:
    """For a remote file source previews read from a download: {"bytes", "age" (seconds since
    it was downloaded), "parquet" (also read via a Parquet copy)}, for the source card's
    "downloaded 2 h ago · refresh" note. None when it isn't downloaded (yet)."""
    if not should_download(src.uri, src.format):
        return None
    latest = latest_download(src.uri, src.format)
    if latest is None:
        return None
    return {
        "bytes": latest.stat().st_size,
        "age": max(0.0, round(time.time() - download_time(latest), 1)),
        "parquet": _read_as_parquet(src, str(latest)),
    }


def cached_read_note(src: Source) -> str | None:
    """User-facing note when a local `src` is read from a Parquet copy (see
    _tabular_parquet_cache). Remote sources get download_info instead."""
    if is_remote_uri(src.uri) or not _read_as_parquet(src, src.uri):
        return None
    return (
        f"Large {src.format} file ({_mb(_local_file_size(src.uri))}): read from a Parquet "
        f"copy for fast previews, rebuilt when the file changes."
    )


def large_file_reader_note(src: Source) -> str | None:
    """User-facing explanation when `src` will be read via pyogrio, else None."""
    if src.format not in _PYOGRIO_FORMATS or not needs_pyogrio_reader(src.uri):
        return None
    size_gib = _local_file_size(src.uri) / 2**30
    return (
        f"Large file ({size_gib:.1f} GiB): read via pyogrio instead of DuckDB's reader, which "
        f"crashes on files over 2 GiB on Windows. Join steps against this source load the "
        f"whole layer into memory."
    )


def _bbox_in_crs(
    con: duckdb.DuckDBPyConnection,
    bbox: tuple[float, float, float, float],
    crs: str,
) -> tuple[float, float, float, float]:
    """A WGS84 (west, south, east, north) bbox as the extent of the same envelope in `crs`.

    Transforms the same 4-corner envelope engine._bbox_filter does, so its extent always
    covers the polygon that filter later tests against.
    """
    west, south, east, north = bbox
    if crs == "EPSG:4326":
        return bbox
    row = con.execute(
        f"SELECT ST_XMin(e), ST_YMin(e), ST_XMax(e), ST_YMax(e) FROM (SELECT ST_Transform("
        f"ST_MakeEnvelope({west}, {south}, {east}, {north}), 'EPSG:4326', {_sql_str(crs)}, "
        f"always_xy := true) AS e)"
    ).fetchone()
    return tuple(row)


def _read_via_pyogrio(
    con: duckdb.DuckDBPyConnection,
    uri: str,
    layer: str | None,
    source_id: str,
    bbox: tuple[float, float, float, float] | None,
    max_features: int | None,
    log: Callable[[str], None] | None,
) -> str:
    """Load a layer into a DuckDB temp table via pyogrio's Arrow stream; returns the table name.

    The ST_Read-free path for files ST_Read can't handle (see _ST_READ_MAX_BYTES). pyogrio
    bundles its own GDAL with ordinary file I/O, and streams Arrow batches straight into
    DuckDB — which turns the GeoArrow WKB column into GEOMETRY on its own. A full 1.1M-feature
    layer of a 2.2 GiB GeoPackage loads in ~3 s. The result matches ST_Read's columns: the
    FID column is included under its own name unless it's already a regular field (GeoJSON's
    `id`), and the geometry column is renamed to `geom` (see _read_expr_normalized).

    `bbox` (WGS84) is pushed down as a GDAL spatial filter in the layer's own CRS, and
    `max_features` stops pulling batches once that many rows are in — the same bounded reads
    fetch_oapif does for a preview. Both are best-effort narrowing; the engine still applies
    its exact bbox filter and LIMIT afterwards.
    """
    import pyogrio

    info = pyogrio.read_info(uri, layer=layer)
    # A driver without a named FID column (FlatGeobuf) still gets one from ST_Read, as OGC_FID.
    return_fids = info.get("fid_column") not in list(info.get("fields", []))

    layer_bbox = None
    if bbox and info.get("crs"):
        try:
            layer_bbox = _bbox_in_crs(con, bbox, _normalize_crs(info["crs"]) or info["crs"])
        except Exception:
            layer_bbox = None  # unknown CRS to PROJ etc. — read unfiltered, engine still filters

    table = _sql_ident(f"__pyogrio_{source_id}")
    stream = f"__pyogrio_stream_{source_id}"
    limit = f" LIMIT {int(max_features)}" if max_features is not None else ""
    kwargs = {"batch_size": min(max_features, 65536)} if max_features else {}
    with pyogrio.raw.open_arrow(
        uri, layer=layer, bbox=layer_bbox, return_fids=return_fids, **kwargs
    ) as (_meta, reader):
        con.register(stream, reader)
        try:
            con.execute(
                f"CREATE OR REPLACE TEMP TABLE {table} AS "
                f"SELECT * FROM {_sql_ident(stream)}{limit}"
            )
        finally:
            con.unregister(stream)

    geom_col = _geometry_column_via_describe(con, table)
    if geom_col and geom_col != "geom":
        con.execute(f"ALTER TABLE {table} RENAME COLUMN {_sql_ident(geom_col)} TO geom")
    if log:
        log(f"source '{source_id}': file >= 2 GiB, read via pyogrio instead of ST_Read")
    return table


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


def _apply_tabular_geometry(
    base: str, src: Source, column_types: dict[str, str] | None = None
) -> str:
    """Wrap a plain tabular read (xlsx/csv/json, no native geometry) with a computed `geom`
    column, for a source naming x/y or geometry columns via Source.x_field/y_field/geom_field.

    This is deliberately *not* done via GDAL open options (X_POSSIBLE_NAMES/Y_POSSIBLE_NAMES/
    GEOM_POSSIBLE_NAMES): those only exist on the CSV driver — XLSX's driver has no
    geometry support at all, open options or otherwise — and even for csv they came with
    real sharp edges: ST_Read_Meta has no `open_options` parameter, so it's blind to a field
    that only exists because of them (needing a separate DESCRIBE-based detection path), and
    GDAL's KEEP_GEOM_COLUMNS default duplicates the matched column under the same name as
    the geometry field it derives, which DuckDB then refuses to `SELECT *` at all
    ("duplicate column name") — confirmed empirically. Building the geometry directly in SQL
    sidesteps all of that and works identically for every tabular format.

    `column_types` (column name → DuckDB type, from a DESCRIBE of `base`) matters for json:
    read_json() types a field holding mixed values (`10.7` in one record, `"5.3"` in the
    next) as JSON, and a nested GeoJSON geometry object as a STRUCT or JSON — neither of
    which the plain text/number handling below can take.
    """
    types = {k: v.upper() for k, v in (column_types or {}).items()}

    def _number(col: str) -> str:
        c = _sql_ident(col)
        if types.get(col) == "JSON":
            c = f"json_extract_string({c}, '$')"
        return f"TRY_CAST({c} AS DOUBLE)"

    if src.x_field and src.y_field:
        geom = f"ST_Point({_number(src.x_field)}, {_number(src.y_field)})"
        consumed = [src.x_field, src.y_field]
    elif src.geom_field:
        g = _sql_ident(src.geom_field)
        gtype = types.get(src.geom_field, "VARCHAR")
        if gtype == "JSON" or gtype.startswith(("STRUCT", "MAP")):
            # A GeoJSON geometry object nested in the record (json sources).
            geom = f"CASE WHEN {g} IS NULL THEN NULL ELSE TRY(ST_GeomFromGeoJSON(to_json({g}))) END"
        else:
            # Hex WKB (e.g. PostGIS's extended WKB), GeoJSON text and WKT look nothing alike,
            # so branch on the text: pure hex digits → WKB, a leading `{` → GeoJSON, else WKT
            # — the same distinctions GDAL's own GEOM_POSSIBLE_NAMES auto-detection makes.
            # Each parse is wrapped in TRY: a malformed value gives the row a NULL geometry
            # instead of failing the whole read, like a non-numeric x/y does above.
            geom = (
                f"CASE WHEN {g} IS NULL OR trim({g}) = '' THEN NULL "
                f"WHEN regexp_matches({g}, '^[0-9A-Fa-f]+$') THEN TRY(ST_GeomFromHEXWKB({g})) "
                f"WHEN starts_with(ltrim({g}), '{{') THEN TRY(ST_GeomFromGeoJSON({g})) "
                f"ELSE TRY(CAST({g} AS GEOMETRY)) END"
            )
        consumed = [src.geom_field]
    else:
        return base
    # The source columns are consumed into geom, and so is any `geom` the read already has:
    # GDAL's CSV driver turns a column named WKT (any case) into a geometry field of its own,
    # named geom, which would otherwise collide with this one. COLUMNS(lambda) rather than
    # EXCLUDE, since that `geom` may not exist; the inner __geom keeps the filtered column
    # list from coming out empty (a file holding nothing but its geometry column).
    drop = ", ".join(_sql_str(c.lower()) for c in [*consumed, "geom"])
    return (
        f"(SELECT * EXCLUDE (__geom), __geom AS geom FROM ("
        f"SELECT COLUMNS(lambda c: lower(c) NOT IN ({drop})) FROM ("
        f"SELECT *, {geom} AS __geom FROM {base})))"
    )


def _tabular_open_extra(src: Source) -> str:
    """The ST_Read open-options clause for a csv/xlsx source (just header_row today)."""
    if src.format in ("xlsx", "csv") and src.header_row is not None:
        return _open_options_arg([_header_open_option(src.format, src.header_row)])
    return ""


def _tabular_cache_path(uri: str, layer: str | None, extra: str) -> tuple[Path, str] | None:
    """(Parquet copy path, per-file key) for a csv/xlsx file big enough to be read via a
    copy (see _tabular_parquet_cache), else None. Doesn't create anything."""
    if _local_file_size(uri) < _TABULAR_CACHE_MIN_BYTES or Path(uri).is_dir():
        return None
    p = Path(uri).resolve()
    st = p.stat()
    # A download (fetch_remote_file) sits at <url hash>/<download time>/<name>: key on the
    # url hash so a re-download replaces that URL's copy instead of orphaning it.
    try:
        key_path = p.parent.parent if p.is_relative_to(_REMOTE_CACHE_DIR.resolve()) else p
    except OSError:
        key_path = p
    path_key = hashlib.sha1(str(key_path).encode()).hexdigest()[:16]
    # A csv has exactly one layer, so picking it (empty → "segmentert") changes nothing read
    # and mustn't mean a new conversion. xlsx sheets do differ.
    if p.suffix.lower() == ".csv":
        layer = None
    version = hashlib.sha1(
        f"{st.st_mtime_ns}|{st.st_size}|{layer}|{extra}".encode()
    ).hexdigest()[:12]
    return _TABULAR_CACHE_DIR / f"{path_key}_{version}.parquet", path_key


def _is_download(path: Path) -> bool:
    try:
        return Path(path).resolve().is_relative_to(_REMOTE_CACHE_DIR.resolve())
    except OSError:
        return False


# ---- background preparation (editor) ----
# The editor must not block a request on a download or a Parquet conversion: browsers keep
# only ~6 connections open per server, and inspect + layer listing + preview + counts all
# waiting on one slow source starve everything else (validation, other sources). So its
# endpoints call prepare_source first, which does that work in a background thread and
# answers "not ready yet, here's the progress" straight away. The CLI never calls it:
# read_expr does the same work inline, sharing the same caches.
_PREPARE_JOBS: dict[str, threading.Thread] = {}
_PREPARE_ERRORS: dict[str, str] = {}
_PREPARE_LOCK = threading.Lock()


def _needs_preparing(src: Source) -> bool:
    """Whether reading `src` right now would mean a download or a Parquet conversion."""
    if src.format not in ("gpkg", "fgdb", "shp", "geojson", "gml", "flatgeobuf", "xlsx", "csv", "json"):
        return False
    local = src.uri
    if should_download(src.uri, src.format):
        latest = latest_download(src.uri, src.format)
        if latest is None:
            return True
        local = str(latest)
    if src.format in ("csv", "xlsx"):
        target = _tabular_cache_path(local, src.layer, _tabular_open_extra(src))
        if target is not None and not target[0].exists():
            return True
    return False


def _prepare_job(src: Source, refresh: bool) -> None:
    from .derive import DUCKDB_LOCK, load_extensions

    key = src.uri
    try:
        local = src.uri
        if should_download(src.uri, src.format):
            local = fetch_remote_file(src.uri, src.format, progress_key=key, fresh=refresh)
        if src.format in ("csv", "xlsx"):
            extra = _tabular_open_extra(src)
            target = _tabular_cache_path(local, src.layer, extra)
            if target is not None and not target[0].exists():
                # Shown while waiting for the lock too: it's the next thing that happens.
                _set_progress(key, "convert", bytes=_local_file_size(local))
                with DUCKDB_LOCK:
                    con = duckdb.connect()
                    try:
                        load_extensions(con)
                        _tabular_parquet_cache(con, local, src.layer, extra, None, progress_key=key)
                    finally:
                        con.close()
    except Exception as e:
        with _PREPARE_LOCK:
            _PREPARE_ERRORS[key] = f"{type(e).__name__}: {e}" if not str(e) else str(e)
    finally:
        _clear_all_progress(key)
        with _PREPARE_LOCK:
            _PREPARE_JOBS.pop(key, None)


def prepare_source(src: Source, refresh: bool = False) -> dict | None:
    """None when `src` can be read right away. Otherwise makes sure a background job is
    downloading / converting it (one per uri) and returns what it's doing — see
    source_progress — or {"stage": "error", "error": ...} once if the last job failed.
    `refresh` downloads a remote file source again even when it's already downloaded."""
    key = src.uri
    with _PREPARE_LOCK:
        error = _PREPARE_ERRORS.pop(key, None)
        if error is not None:
            return {"stage": "error", "error": error}
        if key not in _PREPARE_JOBS:
            refresh = refresh and should_download(src.uri, src.format)
            if not refresh and not _needs_preparing(src):
                return None
            job = threading.Thread(target=_prepare_job, args=(src, refresh), daemon=True)
            _PREPARE_JOBS[key] = job
            job.start()
    return source_progress(key) or {"stage": "queued", "elapsed": 0.0}


def _tabular_parquet_cache(
    con: duckdb.DuckDBPyConnection,
    uri: str,
    layer: str | None,
    extra: str,
    log: Callable[[str], None] | None,
    progress_key: str | None = None,
) -> str | None:
    """read_parquet() over a cached Parquet copy of a large local csv/xlsx file, or None to
    read it with ST_Read as usual.

    GDAL's CSV driver scans the whole file every time it opens one — a 44 MB csv costs ~8.5s
    per open, for a LIMIT 50 or a DESCRIBE just the same — and the editor opens a source many
    times per edit (inspect, preview, counts, plus the ST_Read_Meta inside
    _read_expr_normalized). Converting once to Parquet (same columns and types, it's just
    ST_Read's output) makes every later read near-instant. Keyed by path + mtime + size +
    layer + open options, so an edited file or a changed header_row gets a fresh copy; a
    file's older copies are deleted when a new one is written."""
    target = _tabular_cache_path(uri, layer, extra)
    if target is None:
        return None
    out, path_key = target
    st = Path(uri).stat()
    if not out.exists():
        _TABULAR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if log:
            log(f"Caching {Path(uri).name} as Parquet for faster reads")
        tmp = out.with_name(f"{out.stem}.{threading.get_ident()}.part")
        # Raw ST_Read, not _read_expr_normalized: its ST_Read_Meta lookup is one more full
        # GDAL scan of the file. The geometry column is found from the Parquet copy below.
        read = _st_read(uri, layer=layer, extra=extra)
        start = time.monotonic()
        _set_progress(progress_key, "convert", bytes=st.st_size)
        try:
            con.execute(f"COPY (SELECT * FROM {read}) TO {_sql_str(str(tmp))} (FORMAT PARQUET)")
        finally:
            _clear_progress(progress_key, "convert")
        os.replace(tmp, out)
        if log:
            log(f"Converted to Parquet in {time.monotonic() - start:.1f}s")
        # A local file's older copies are simply superseded (every process derives the same
        # new version from the file itself), but a download's copies can be another process's
        # current one: only remove those once stale for everyone.
        remote = _is_download(Path(uri))
        for old in _TABULAR_CACHE_DIR.glob(f"{path_key}_*.parquet"):
            if old == out or (remote and not _superseded_long_enough(old)):
                continue
            try:  # still open elsewhere (Windows) → left for the next rewrite to remove
                old.unlink()
            except OSError:
                pass
    base = f"read_parquet({_sql_str(str(out).replace(chr(92), '/'))})"
    name = _geometry_column_via_describe(con, base)
    if not name or name == "geom":
        return base
    return f"(SELECT * EXCLUDE ({_sql_ident(name)}), {_sql_ident(name)} AS geom FROM {base})"


def _json_read_expr(path: str, records: str | None) -> str:
    """read_json() over a plain JSON file: an array of objects, newline-delimited objects,
    or (with `records`, a dot path like `data.items`) one document holding that array.

    `sample_size = -1` types each column from the whole file rather than the first ~20k
    records, so a field that only turns mixed-type further down can't fail the read halfway.
    `maximum_object_size` lifts the 16 MB per-object cap, which a `records` document (the
    whole file is one object) passes easily."""
    base = f"read_json({_sql_str(path)}, sample_size = -1, maximum_object_size = 2147483647)"
    if not records:
        return base
    path_expr = ".".join(_sql_ident(part) for part in records.split("."))
    # Inner unnest: the list into one row per record; outer: the record's fields into columns.
    return (
        f"(SELECT unnest(__rec, max_depth := 1) FROM "
        f"(SELECT unnest({path_expr}) AS __rec FROM {base}))"
    )


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

    `bbox` and `max_features` are only honored for `oapif` sources — see `fetch_oapif` — and
    for files read via pyogrio instead of ST_Read — see `_read_via_pyogrio`.
    `sample` is honored by `oapif`, `arcgis_rest` and the pyogrio path.
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
        geom = f"CASE WHEN {g} IS NULL THEN NULL ELSE TRY(ST_GeomFromHEXWKB({g})) END"
        return f"(SELECT * EXCLUDE ({g}), {geom} AS geom FROM {base})"

    if fmt == "json":
        # Native read_json(), not GDAL: its GeoJSON driver only takes FeatureCollections.
        uri = src.uri
        if con is not None and should_download(uri, fmt):
            uri = fetch_remote_file(uri, fmt, log, progress_key=src.uri)
        base = _json_read_expr(uri, src.records)
        if not (src.x_field or src.geom_field):
            return base
        column_types = None
        if con is not None:
            column_types = {
                r[0]: str(r[1]) for r in con.execute(f"DESCRIBE SELECT * FROM {base}").fetchall()
            }
        return _apply_tabular_geometry(base, src, column_types)

    if fmt in ("gpkg", "fgdb", "shp", "geojson", "gml", "flatgeobuf", "xlsx", "csv"):
        # GDAL picks the driver from the path/extension; layer/sheet is optional.
        uri, layer = src.uri, src.layer
        if con is not None and should_download(uri, fmt):
            uri = fetch_remote_file(uri, fmt, log, progress_key=src.uri)
        if fmt in ("gpkg", "fgdb", "gml"):
            uri, layer = _maybe_linearize(uri, layer, workdir, src.id, con, log)
        if con is not None and fmt in _PYOGRIO_FORMATS and needs_pyogrio_reader(uri):
            limit = _PYOGRIO_SAMPLE_ROWS if sample and max_features is None else max_features
            return _read_via_pyogrio(con, uri, layer, src.id, bbox, limit, log)
        extra = _tabular_open_extra(src)
        base = None
        if con is not None and fmt in ("xlsx", "csv"):
            base = _tabular_parquet_cache(con, uri, layer, extra, log, progress_key=src.uri)
        if base is None:
            base = _read_expr_normalized(uri, layer, con, extra=extra)
        if fmt in ("xlsx", "csv") and (src.x_field or src.geom_field):
            base = _apply_tabular_geometry(base, src)
        return base

    raise ValueError(f"unsupported source format: {fmt}")
