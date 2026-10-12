"""Web services fetched over HTTP to a local file ST_Read can open (WFS, ArcGIS REST,
OGC API - Features), and listing the layers each one offers."""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import requests

from ..config import Source

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
