"""fetch_oapif()/fetch_arcgis_rest(): paginated fetches, mocked over HTTP.

Also covers read_expr()'s `parquet` branch (see test_read_expr_parquet_*): Parquet is read
with DuckDB's native read_parquet(), not ST_Read/GDAL (no Parquet driver in the bundled GDAL
build), so it has its own geometry-column-detection path worth testing directly.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import duckdb
import pytest

import duck_soup.sources as sources_mod
from duck_soup.config import Source
from duck_soup.sources import fetch_arcgis_rest, fetch_oapif, fetch_wfs, read_expr

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _repo_root_cwd(monkeypatch):
    # data/test.parquet is referenced by relative path, resolved by DuckDB against the
    # process cwd (same reasoning as test_engine.py's fixture of the same name).
    monkeypatch.chdir(REPO_ROOT)


@pytest.fixture()
def spatial_con():
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    try:
        yield con
    finally:
        con.close()


def test_read_expr_parquet_aliases_geometry_column_to_geom(spatial_con):
    # data/test.parquet mirrors data/test.geojson (same 5 features) but, like real-world
    # GeoParquet (e.g. GeoPandas output), names its geometry column "geometry" rather than
    # "geom" — exercising the same EXCLUDE/alias rename ST_Read-based formats get from
    # _read_expr_normalized, just built on read_parquet() instead.
    src = Source(id="pts", format="parquet", uri="data/test.parquet", crs="EPSG:4326")
    read = read_expr(src, REPO_ROOT, con=spatial_con)

    cols = [c[0] for c in spatial_con.execute(f"DESCRIBE SELECT * FROM {read}").fetchall()]
    assert "geom" in cols
    assert "geometry" not in cols

    rows = spatial_con.execute(f"SELECT name, category FROM {read} ORDER BY name").fetchall()
    assert len(rows) == 5
    assert ("Operahuset", "culture") in rows


def test_read_expr_flatgeobuf_reads_expected_rows(spatial_con):
    # data/test.fgb mirrors data/test.geojson (same 5 features). Unlike parquet, FlatGeobuf
    # goes through the ordinary ST_Read/_read_expr_normalized path (GDAL's FlatGeobuf driver
    # is bundled with the spatial extension) — this just confirms it's wired into read_expr's
    # generic GDAL-format branch and returns a usable `geom` column, same as any other format.
    src = Source(id="pts", format="flatgeobuf", uri="data/test.fgb", crs="EPSG:4326")
    read = read_expr(src, REPO_ROOT, con=spatial_con)

    cols = [c[0] for c in spatial_con.execute(f"DESCRIBE SELECT * FROM {read}").fetchall()]
    assert "geom" in cols

    rows = spatial_con.execute(f"SELECT name, category FROM {read} ORDER BY name").fetchall()
    assert len(rows) == 5
    assert ("Operahuset", "culture") in rows


def test_read_expr_csv_x_y_fields_build_point_geometry(spatial_con):
    src = Source(id="pts", format="csv", uri="data/test_xy.csv", crs="EPSG:4326", x_field="lon", y_field="lat")
    read = read_expr(src, REPO_ROOT, con=spatial_con)

    cols = [c[0] for c in spatial_con.execute(f"DESCRIBE SELECT * FROM {read}").fetchall()]
    assert "geom" in cols
    # x_field/y_field are consumed into geom, not duplicated as attributes (_apply_tabular_geometry).
    assert "lon" not in cols and "lat" not in cols

    rows = spatial_con.execute(f"SELECT name, ST_AsText(geom) FROM {read} ORDER BY name").fetchall()
    assert ("Operahuset", "POINT (10.7527 59.9075)") in rows


def test_read_expr_xlsx_x_y_fields_build_point_geometry(spatial_con):
    # Geometry-from-columns isn't a GDAL open option here (the XLSX driver has none at all) —
    # _apply_tabular_geometry builds it in SQL, so it works identically for xlsx as for csv.
    src = Source(id="pts", format="xlsx", uri="data/test_xy.xlsx", crs="EPSG:4326", x_field="lon", y_field="lat")
    read = read_expr(src, REPO_ROOT, con=spatial_con)

    cols = [c[0] for c in spatial_con.execute(f"DESCRIBE SELECT * FROM {read}").fetchall()]
    assert "geom" in cols
    assert "lon" not in cols and "lat" not in cols

    # xlsx round-trips coordinates through IEEE754 binary floats (unlike csv's plain text),
    # so compare numerically rather than the exact WKT string.
    rows = spatial_con.execute(
        f"SELECT name, round(ST_X(geom), 4), round(ST_Y(geom), 4) FROM {read} ORDER BY name"
    ).fetchall()
    assert ("Operahuset", 10.7527, 59.9075) in rows


def test_read_expr_csv_geom_field_builds_geometry_from_wkt(spatial_con):
    src = Source(id="pts", format="csv", uri="data/test_wkt.csv", crs="EPSG:4326", geom_field="geom_col")
    read = read_expr(src, REPO_ROOT, con=spatial_con)

    cols = [c[0] for c in spatial_con.execute(f"DESCRIBE SELECT * FROM {read}").fetchall()]
    assert "geom" in cols
    assert "geom_col" not in cols

    rows = spatial_con.execute(f"SELECT name, ST_AsText(geom) FROM {read} ORDER BY name").fetchall()
    assert ("Operahuset", "POINT (10.7527 59.9075)") in rows


def test_read_expr_xlsx_geom_field_builds_geometry_from_wkt(spatial_con):
    # data/test_wkt.xlsx has only string columns ("name", "geom_col"), which is exactly the
    # case GDAL's own AUTO header heuristic gets wrong for xlsx (a header row of plain text
    # looks structurally identical to a data row of plain text) — confirmed empirically: read
    # without header_row=True, GDAL treats the header itself as the first data row instead.
    # So this exercises header_row and geom_field together, the way a real file would need.
    src = Source(
        id="pts", format="xlsx", uri="data/test_wkt.xlsx", crs="EPSG:4326",
        header_row=True, geom_field="geom_col",
    )
    read = read_expr(src, REPO_ROOT, con=spatial_con)

    cols = [c[0] for c in spatial_con.execute(f"DESCRIBE SELECT * FROM {read}").fetchall()]
    assert "geom" in cols
    assert "geom_col" not in cols

    rows = spatial_con.execute(f"SELECT name, ST_AsText(geom) FROM {read} ORDER BY name").fetchall()
    assert ("Operahuset", "POINT (10.7527 59.9075)") in rows


def test_read_expr_csv_header_row_false_treats_first_row_as_data(spatial_con):
    src = Source(id="pts", format="csv", uri="data/test_noheader.csv", header_row=False)
    read = read_expr(src, REPO_ROOT, con=spatial_con)

    rows = spatial_con.execute(f"SELECT * FROM {read} ORDER BY field_1").fetchall()
    assert rows == [(1, "1", "apple"), (2, "2", "banana"), (3, "3", "cherry")]


def test_read_expr_xlsx_header_row_false_treats_first_row_as_data(spatial_con):
    # data/test_xy.xlsx has a genuine header row ("name", "lon", "lat") that GDAL's AUTO
    # heuristic correctly picks up on its own (see the "none" test below) — header_row=False
    # forces it to be read as a literal data row instead, proving the override actually
    # takes effect rather than just matching what AUTO would have done anyway.
    src = Source(id="pts", format="xlsx", uri="data/test_xy.xlsx", header_row=False)
    read = read_expr(src, REPO_ROOT, con=spatial_con)

    rows = {r[0] for r in spatial_con.execute(f"SELECT Field1 FROM {read}").fetchall()}
    # The real header row ("name", "lon", "lat") shows up as an ordinary data value now,
    # alongside the two actual data rows — proving the override actually took effect rather
    # than just matching what AUTO would have picked anyway.
    assert rows == {"name", "Operahuset", "Akershus Festning"}


def test_read_expr_csv_header_row_none_leaves_gdal_auto_detection(spatial_con):
    # Baseline: without an explicit override, GDAL's own AUTO heuristic decides — for this
    # file (a genuine header row followed by non-numeric-looking data) it picks up "name" as
    # a real header rather than a data value.
    src = Source(id="pts", format="csv", uri="data/test_xy.csv", crs="EPSG:4326")
    read = read_expr(src, REPO_ROOT, con=spatial_con)
    cols = [c[0] for c in spatial_con.execute(f"DESCRIBE SELECT * FROM {read}").fetchall()]
    assert "name" in cols


def test_read_expr_xlsx_header_row_none_leaves_gdal_auto_detection(spatial_con):
    src = Source(id="pts", format="xlsx", uri="data/test_xy.xlsx", crs="EPSG:4326")
    read = read_expr(src, REPO_ROOT, con=spatial_con)
    cols = [c[0] for c in spatial_con.execute(f"DESCRIBE SELECT * FROM {read}").fetchall()]
    assert "name" in cols


def test_read_expr_falls_back_to_describe_when_st_read_meta_finds_no_geometry_field(spatial_con, tmp_path, monkeypatch):
    # Regression test: ST_Read_Meta isn't reliable for every format ST_Read itself opens fine
    # — confirmed empirically against a real File Geodatabase export, where ST_Read_Meta
    # returned zero rows outright even though ST_Read opened its layer and read rows without
    # complaint. When that happened, _layer_geometry_field came back empty and the rename used
    # to be silently skipped, so engine.py's hard-coded `EXCLUDE (geom)` blew up on the real
    # column name (Esri's `SHAPE`) with a binder error. Reproduced here by forcing
    # _layer_geometry_field to come back empty (rather than depending on an actual gdb
    # fixture) against a gpkg whose geometry column is deliberately not named "geom".
    gpkg_path = tmp_path / "shape_col.gpkg"
    spatial_con.execute("CREATE TABLE t AS SELECT 1 AS id, ST_Point(10.75, 59.90) AS shape")
    spatial_con.execute(
        f"COPY t TO '{gpkg_path}' (FORMAT GDAL, DRIVER 'GPKG', LAYER_CREATION_OPTIONS 'GEOMETRY_NAME=shape')"
    )
    monkeypatch.setattr(sources_mod, "_layer_geometry_field", lambda con, uri, layer: None)

    src = Source(id="pts", format="gpkg", uri=str(gpkg_path))
    read = read_expr(src, tmp_path, con=spatial_con)

    cols = [c[0] for c in spatial_con.execute(f"DESCRIBE SELECT * FROM {read}").fetchall()]
    assert "geom" in cols
    assert "shape" not in cols


def test_read_expr_parquet_without_connection_returns_bare_read_parquet():
    # Mirrors _read_expr_normalized's con=None short-circuit for GDAL formats: without a
    # connection there's no way to inspect the schema, so no renaming is attempted.
    src = Source(id="pts", format="parquet", uri="data/test.parquet", crs="EPSG:4326")
    assert read_expr(src, REPO_ROOT) == "read_parquet('data/test.parquet')"


def test_parquet_geometry_info_reports_column_name_and_embedded_crs(spatial_con):
    name, crs = sources_mod.parquet_geometry_info(spatial_con, "data/test.parquet")
    assert name == "geometry"
    assert crs == "EPSG:4326"


def test_parquet_geometry_info_normalizes_ogc_crs84_to_epsg_4326(spatial_con):
    # Regression test: GeoParquet files that omit an explicit CRS default to OGC:CRS84 per
    # the spec (data/test_crs84.parquet has no CRS set on its geometry column at all), and
    # DuckDB reports that verbatim as GEOMETRY('OGC:CRS84'). Handing "OGC:CRS84" straight
    # to the editor as default_crs used to save straight into the pipeline YAML's `crs`
    # field, which config.CRSStr rejects (it requires a numeric EPSG-style code) — crashing
    # pipeline validation with a raw pydantic error. It's WGS84 lon/lat, so EPSG:4326.
    name, crs = sources_mod.parquet_geometry_info(spatial_con, "data/test_crs84.parquet")
    assert name == "geometry"
    assert crs == "EPSG:4326"


def test_read_expr_postgres_without_connection_raises():
    # Unlike parquet, postgres has no con=None fallback — ATTACHing a database and reading
    # its catalog both require a live DuckDB connection, so this should fail clearly rather
    # than produce a SQL string that can never actually be executed.
    src = Source(id="parcels", format="postgres", uri="postgresql://user:pass@host/db", layer="parcels")
    with pytest.raises(ValueError, match="live DuckDB connection"):
        read_expr(src, REPO_ROOT)


# The tests below need a real PostGIS database to ATTACH to — there's no local-fixture-file
# equivalent for a running database. Point DUCK_SOUP_TEST_PG_DSN at a libpq connection string
# for a PostGIS instance with a table `duck_soup_test_pts(name text, geom geometry(Point, 4326))`
# containing a row named 'Operahuset' to exercise them; they're skipped otherwise.
_PG_DSN = os.environ.get("DUCK_SOUP_TEST_PG_DSN")
_pg_skip = pytest.mark.skipif(not _PG_DSN, reason="set DUCK_SOUP_TEST_PG_DSN to run postgres source tests")


@pytest.fixture()
def postgres_con():
    con = duckdb.connect()
    con.execute("INSTALL postgres; LOAD postgres;")
    try:
        yield con
    finally:
        con.close()


@_pg_skip
def test_read_expr_postgres_reads_expected_rows_and_geometry(postgres_con):
    src = Source(id="pts", format="postgres", uri=_PG_DSN, layer="duck_soup_test_pts", crs="EPSG:4326")
    read = read_expr(src, REPO_ROOT, con=postgres_con)

    cols = [c[0] for c in postgres_con.execute(f"DESCRIBE SELECT * FROM {read}").fetchall()]
    assert "geom" in cols

    rows = postgres_con.execute(f"SELECT name FROM {read} WHERE name = 'Operahuset'").fetchall()
    assert len(rows) == 1


@_pg_skip
def test_postgres_geometry_info_reports_column_and_srid(postgres_con):
    alias = sources_mod.attach_postgres(postgres_con, _PG_DSN, "pts")
    name, crs = sources_mod.postgres_geometry_info(postgres_con, alias, "public", "duck_soup_test_pts")
    assert name == "geom"
    assert crs == "EPSG:4326"


@_pg_skip
def test_read_expr_postgres_schema_qualified_layer_matches_unqualified(postgres_con):
    # "public.duck_soup_test_pts" should resolve the same table as bare "duck_soup_test_pts".
    src_qualified = Source(id="pts", format="postgres", uri=_PG_DSN, layer="public.duck_soup_test_pts")
    src_bare = Source(id="pts2", format="postgres", uri=_PG_DSN, layer="duck_soup_test_pts")
    read_q = read_expr(src_qualified, REPO_ROOT, con=postgres_con)
    read_b = read_expr(src_bare, REPO_ROOT, con=postgres_con)
    assert (
        postgres_con.execute(f"SELECT count(*) FROM {read_q}").fetchone()
        == postgres_con.execute(f"SELECT count(*) FROM {read_b}").fetchone()
    )


@pytest.fixture(autouse=True)
def _clear_oapif_fetch_cache():
    # fetch_oapif's short-lived cache (_OAPIF_FETCH_CACHE) is a module-level global keyed by
    # (uri, layer, bbox, page_size) — several tests here share the same fake uri/layer/page_size
    # and would otherwise leak cached results (and skipped HTTP calls) across each other.
    sources_mod._OAPIF_FETCH_CACHE.clear()
    yield
    sources_mod._OAPIF_FETCH_CACHE.clear()


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeSession:
    """Records calls and hands back canned pages, standing in for requests.Session()."""

    def __init__(self, pages: list[dict]):
        self._pages = pages
        self.calls: list[tuple[str, dict | None]] = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params))
        return _FakeResponse(self._pages[len(self.calls) - 1])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_session(monkeypatch, pages: list[dict]) -> _FakeSession:
    session = _FakeSession(pages)
    monkeypatch.setattr("duck_soup.sources.requests.Session", lambda: session)
    return session


def test_fetch_oapif_follows_next_link_until_absent(tmp_path: Path, monkeypatch):
    pages = [
        {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {"nr": "03"}, "geometry": None}],
            "links": [{"rel": "next", "href": "https://host/ogcapi/collections/fylker/items?offset=1&limit=1"}],
        },
        {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {"nr": "11"}, "geometry": None}],
            "links": [],
        },
    ]
    session = _patch_session(monkeypatch, pages)

    src = Source(id="fylker", format="oapif", uri="https://host/ogcapi", layer="fylker", page_size=1)
    out_path = fetch_oapif(src, tmp_path)

    data = json.loads(Path(out_path).read_text(encoding="utf-8"))
    assert [f["properties"]["nr"] for f in data["features"]] == ["03", "11"]
    assert len(session.calls) == 2
    # First request hits the constructed items URL with our own params; the second
    # follows the server's `next` link verbatim (no params re-added).
    assert session.calls[0][0] == "https://host/ogcapi/collections/fylker/items"
    assert session.calls[0][1] == {"limit": 1}
    assert session.calls[1][0] == "https://host/ogcapi/collections/fylker/items?offset=1&limit=1"
    assert session.calls[1][1] is None


def test_fetch_oapif_pushes_bbox_down_on_first_request_only(tmp_path: Path, monkeypatch):
    pages = [
        {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {"nr": "03"}, "geometry": None}],
            "links": [{"rel": "next", "href": "https://host/ogcapi/collections/fylker/items?offset=1&limit=1"}],
        },
        {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {"nr": "11"}, "geometry": None}],
            "links": [],
        },
    ]
    session = _patch_session(monkeypatch, pages)

    src = Source(id="fylker", format="oapif", uri="https://host/ogcapi", layer="fylker", page_size=1)
    fetch_oapif(src, tmp_path, bbox=(4.0, 58.0, 6.0, 60.0))

    assert session.calls[0][1] == {"limit": 1, "bbox": "4.0,58.0,6.0,60.0"}
    # next link already encodes the bbox server-side, so it isn't re-added locally.
    assert session.calls[1][1] is None


class _FakeGetResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeWfsResponse:
    content = b"<wfs:FeatureCollection/>"

    def raise_for_status(self):
        pass


def test_fetch_wfs_requests_srsname_matching_source_crs(tmp_path: Path, monkeypatch):
    # Regression test: without an explicit SRSNAME, the server picks its own default
    # response CRS, which may not match `src.crs` — engine.py then reprojects the fetched
    # geometry assuming it's already in `src.crs`, silently corrupting coordinates when the
    # two disagree (e.g. treating raw lon/lat degrees as UTM33N metres).
    calls: list[str] = []

    def fake_get(url, timeout=None):
        calls.append(url)
        return _FakeWfsResponse()

    monkeypatch.setattr("duck_soup.sources.requests.get", fake_get)

    src = Source(id="a", format="wfs", uri="https://host/wfs", layer="app:Fylke", crs="EPSG:25833")
    fetch_wfs(src, tmp_path)

    assert "SRSNAME=urn%3Aogc%3Adef%3Acrs%3AEPSG%3A%3A25833" in calls[0]


def test_fetch_wfs_omits_srsname_when_source_crs_unset(tmp_path: Path, monkeypatch):
    calls: list[str] = []

    def fake_get(url, timeout=None):
        calls.append(url)
        return _FakeWfsResponse()

    monkeypatch.setattr("duck_soup.sources.requests.get", fake_get)

    src = Source(id="a", format="wfs", uri="https://host/wfs", layer="app:Fylke")
    fetch_wfs(src, tmp_path)

    assert "SRSNAME" not in calls[0]


def test_fetch_arcgis_rest_joins_service_root_and_layer(tmp_path: Path, monkeypatch):
    calls: list[tuple[str, dict]] = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, params))
        return _FakeGetResponse({"features": []})

    monkeypatch.setattr("duck_soup.sources.requests.get", fake_get)

    src = Source(id="a", format="arcgis_rest", uri="https://host/arcgis/rest/services/Foo/MapServer", layer="0")
    fetch_arcgis_rest(src, tmp_path)

    assert calls[0][0] == "https://host/arcgis/rest/services/Foo/MapServer/0/query"


def test_fetch_arcgis_rest_full_endpoint_without_layer_unchanged(tmp_path: Path, monkeypatch):
    calls: list[tuple[str, dict]] = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, params))
        return _FakeGetResponse({"features": []})

    monkeypatch.setattr("duck_soup.sources.requests.get", fake_get)

    src = Source(id="a", format="arcgis_rest", uri="https://host/arcgis/rest/services/Foo/MapServer/0")
    fetch_arcgis_rest(src, tmp_path)

    assert calls[0][0] == "https://host/arcgis/rest/services/Foo/MapServer/0/query"


def test_fetch_arcgis_rest_pages_until_short_batch(tmp_path: Path, monkeypatch):
    pages = [
        {
            "features": [
                {"type": "Feature", "properties": {"nr": "03"}, "geometry": None},
                {"type": "Feature", "properties": {"nr": "07"}, "geometry": None},
            ],
            "exceededTransferLimit": True,
        },
        {"features": [{"type": "Feature", "properties": {"nr": "11"}, "geometry": None}]},
    ]
    calls: list[dict] = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params)
        return _FakeGetResponse(pages[len(calls) - 1])

    monkeypatch.setattr("duck_soup.sources.requests.get", fake_get)

    src = Source(id="a", format="arcgis_rest", uri="https://host/arcgis/rest/services/Foo/MapServer", layer="0", page_size=2)
    out_path = fetch_arcgis_rest(src, tmp_path)

    data = json.loads(Path(out_path).read_text(encoding="utf-8"))
    assert [f["properties"]["nr"] for f in data["features"]] == ["03", "07", "11"]
    assert len(calls) == 2
    assert calls[0]["resultOffset"] == 0
    assert calls[1]["resultOffset"] == 2


def test_fetch_arcgis_rest_sample_stops_after_first_page(tmp_path: Path, monkeypatch):
    # A layer with far more rows than fit on one page: without `sample`, fetch_arcgis_rest
    # would keep paging (exceededTransferLimit True) until it downloaded everything, which
    # makes something as cheap as a schema preview take as long as a full run.
    pages = [
        {
            "features": [
                {"type": "Feature", "properties": {"nr": "03"}, "geometry": None},
                {"type": "Feature", "properties": {"nr": "07"}, "geometry": None},
            ],
            "exceededTransferLimit": True,
        },
        {"features": [{"type": "Feature", "properties": {"nr": "11"}, "geometry": None}]},
    ]
    calls: list[dict] = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params)
        return _FakeGetResponse(pages[len(calls) - 1])

    monkeypatch.setattr("duck_soup.sources.requests.get", fake_get)

    src = Source(id="a", format="arcgis_rest", uri="https://host/arcgis/rest/services/Foo/MapServer", layer="0", page_size=2)
    out_path = fetch_arcgis_rest(src, tmp_path, sample=True)

    data = json.loads(Path(out_path).read_text(encoding="utf-8"))
    assert [f["properties"]["nr"] for f in data["features"]] == ["03", "07"]
    assert len(calls) == 1


def test_fetch_oapif_sample_stops_after_first_page(tmp_path: Path, monkeypatch):
    pages = [
        {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {"nr": "03"}, "geometry": None}],
            "links": [{"rel": "next", "href": "https://host/ogcapi/collections/fylker/items?offset=1&limit=1"}],
        },
    ]
    session = _patch_session(monkeypatch, pages)

    src = Source(id="fylker", format="oapif", uri="https://host/ogcapi", layer="fylker", page_size=1)
    out_path = fetch_oapif(src, tmp_path, sample=True)

    data = json.loads(Path(out_path).read_text(encoding="utf-8"))
    assert [f["properties"]["nr"] for f in data["features"]] == ["03"]
    assert len(session.calls) == 1


def test_fetch_oapif_max_features_stops_paging_once_reached(tmp_path: Path, monkeypatch):
    # Three pages available, but a preview only needs the first two features worth of pages —
    # without max_features, fetch_oapif would keep following `next` until page 3.
    pages = [
        {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {"nr": "03"}, "geometry": None}],
            "links": [{"rel": "next", "href": "https://host/ogcapi/collections/fylker/items?offset=1&limit=1"}],
        },
        {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {"nr": "11"}, "geometry": None}],
            "links": [{"rel": "next", "href": "https://host/ogcapi/collections/fylker/items?offset=2&limit=1"}],
        },
        {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {"nr": "15"}, "geometry": None}],
            "links": [],
        },
    ]
    session = _patch_session(monkeypatch, pages)

    src = Source(id="fylker", format="oapif", uri="https://host/ogcapi", layer="fylker", page_size=1)
    out_path = fetch_oapif(src, tmp_path, max_features=2)

    data = json.loads(Path(out_path).read_text(encoding="utf-8"))
    assert [f["properties"]["nr"] for f in data["features"]] == ["03", "11"]
    assert len(session.calls) == 2


def test_fetch_oapif_second_call_reuses_cached_fetch(tmp_path: Path, monkeypatch):
    # Mirrors the web editor's real sequence: selecting a collection triggers a sample
    # fetch (schema inspection), which immediately triggers a live preview fetch for the
    # same collection/bbox/page_size. The second call shouldn't need the network at all —
    # the first call's page already covers what the second one needs.
    pages = [
        {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {"nr": "03"}, "geometry": None},
                {"type": "Feature", "properties": {"nr": "07"}, "geometry": None},
            ],
            "links": [{"rel": "next", "href": "https://host/ogcapi/collections/fylker/items?offset=2&limit=2"}],
        },
    ]
    session = _patch_session(monkeypatch, pages)

    src = Source(id="fylker", format="oapif", uri="https://host/ogcapi", layer="fylker", page_size=2)
    fetch_oapif(src, tmp_path, sample=True)  # schema inspection sample
    assert len(session.calls) == 1

    out_path = fetch_oapif(src, tmp_path, max_features=1)  # immediate preview, needs <= what's cached
    data = json.loads(Path(out_path).read_text(encoding="utf-8"))
    assert [f["properties"]["nr"] for f in data["features"]] == ["03"]
    assert len(session.calls) == 1  # no second HTTP call


def test_fetch_oapif_cache_expires_after_ttl(tmp_path: Path, monkeypatch):
    pages = [
        {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {"nr": "03"}, "geometry": None}],
            "links": [],
        },
        {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {"nr": "99"}, "geometry": None}],
            "links": [],
        },
    ]
    session = _patch_session(monkeypatch, pages)

    fake_now = [1_000_000.0]
    monkeypatch.setattr(sources_mod.time, "time", lambda: fake_now[0])

    src = Source(id="fylker", format="oapif", uri="https://host/ogcapi", layer="fylker", page_size=1)
    fetch_oapif(src, tmp_path, sample=True)
    assert len(session.calls) == 1

    fake_now[0] += sources_mod._OAPIF_CACHE_TTL + 1
    out_path = fetch_oapif(src, tmp_path, sample=True)
    data = json.loads(Path(out_path).read_text(encoding="utf-8"))
    assert [f["properties"]["nr"] for f in data["features"]] == ["99"]
    assert len(session.calls) == 2
