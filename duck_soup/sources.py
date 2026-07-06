"""Turn a Source config into something DuckDB can read.

Almost everything goes through DuckDB's ST_Read (which uses GDAL), so adding a
new vector format is usually just a new branch that builds the right GDAL
connection string. ArcGIS REST is the exception: it is fetched with pagination
to a temporary GeoJSON file, which is then read with ST_Read like anything else.
"""
from __future__ import annotations

import json
from pathlib import Path

import requests

from .config import Source


def _sql_str(value: str) -> str:
    """Single-quote a value for inlining into SQL."""
    return "'" + value.replace("'", "''") + "'"


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
    OGC API Features format instead, which natively returns GeoJSON.
    """
    from urllib.parse import urlparse, urlencode, parse_qsl

    base = src.uri
    if base.upper().startswith("WFS:"):
        base = base[4:]

    p = urlparse(base)
    qs_base = [(k, v) for k, v in parse_qsl(p.query)
               if k.lower() not in ("request", "service", "version", "typenames", "typename",
                                    "count", "startindex", "outputformat")]

    params = qs_base + [
        ("SERVICE", "WFS"), ("VERSION", "2.0.0"), ("REQUEST", "GetFeature"),
        ("TYPENAMES", src.layer or ""),
    ]
    url = p._replace(query=urlencode(params)).geturl()
    resp = requests.get(url, timeout=300)
    resp.raise_for_status()
    out_path = workdir / f"{src.id}.gml"
    out_path.write_bytes(resp.content)
    return str(out_path)


def fetch_arcgis_rest(src: Source, workdir: Path) -> str:
    """Page through an ArcGIS REST query endpoint and write a GeoJSON file.

    `src.uri` should be the layer endpoint, e.g.
    https://host/arcgis/rest/services/Foo/FeatureServer/0
    Returns the path to the written GeoJSON file (CRS is forced to EPSG:4326).
    """
    base_url = src.uri.rstrip("/")
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
        if len(batch) < src.page_size or payload.get("exceededTransferLimit") is False:
            break
        if not batch:
            break
        offset += len(batch)

    fc = {"type": "FeatureCollection", "features": features}
    out_path.write_text(json.dumps(fc), encoding="utf-8")
    return str(out_path)


def read_expr(src: Source, workdir: Path) -> str:
    """Return a SQL table expression (ST_Read(...)) for this source."""
    fmt = src.format

    if fmt == "arcgis_rest":
        path = fetch_arcgis_rest(src, workdir)
        return _st_read(path)

    if fmt == "wfs":
        path = fetch_wfs(src, workdir)
        return _st_read(path)

    if fmt in ("gpkg", "fgdb", "shp", "geojson", "gml", "parquet", "xlsx", "csv"):
        # GDAL picks the driver from the path/extension; layer/sheet is optional.
        return _st_read(src.uri, layer=src.layer)

    raise ValueError(f"unsupported source format: {fmt}")
