"""FastAPI endpoint tests for the pipeline editor backend (duck_soup/web/app.py)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from duck_soup.config import Source
from duck_soup.web.app import app

REPO_ROOT = Path(__file__).resolve().parents[1]
PIPELINES_DIR = REPO_ROOT / "pipelines"


@pytest.fixture(autouse=True)
def _repo_root_cwd(monkeypatch):
    # Relative source `uri`s in configs are resolved by DuckDB against the
    # process cwd, same as the CLI's documented usage from the repo root.
    monkeypatch.chdir(REPO_ROOT)


@pytest.fixture()
def client():
    return TestClient(app)


def _test_config_dict() -> dict:
    return yaml.safe_load((PIPELINES_DIR / "test.yaml").read_text(encoding="utf-8"))


def test_meta_lists_formats_predicates_and_funcs(client):
    r = client.get("/api/meta")
    assert r.status_code == 200
    body = r.json()
    assert "gpkg" in body["formats"]
    assert "intersects" in body["predicates"]
    assert "mgrs" in body["funcs"]


def test_list_pipelines_includes_fixtures(client):
    r = client.get("/api/pipelines")
    assert r.status_code == 200
    names = r.json()
    assert "test" in names
    assert "test_multi_layer" in names


def test_get_pipeline_returns_config(client):
    r = client.get("/api/pipelines/test")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "test"
    assert body["config"]["pipelines"][0]["base"] == "places"


def test_get_pipeline_404_for_unknown_name(client):
    r = client.get("/api/pipelines/does_not_exist")
    assert r.status_code == 404


def test_validate_accepts_valid_config(client):
    r = client.post("/api/validate", json={"config": _test_config_dict()})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "yaml" in body


def test_validate_rejects_config_with_unknown_base(client):
    cfg = _test_config_dict()
    cfg["pipelines"][0]["base"] = "does_not_exist"
    r = client.post("/api/validate", json={"config": cfg})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "not among sources" in body["error"]


def test_parse_yaml_accepts_valid_pipeline_yaml(client):
    text = (PIPELINES_DIR / "test.yaml").read_text(encoding="utf-8")
    r = client.post("/api/parse_yaml", json={"yaml": text})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["config"]["pipelines"][0]["base"] == "places"


def test_parse_yaml_rejects_malformed_yaml(client):
    r = client.post("/api/parse_yaml", json={"yaml": "sources: [this is not: valid: yaml"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "invalid YAML" in body["error"]


def test_parse_yaml_rejects_non_mapping_yaml(client):
    r = client.post("/api/parse_yaml", json={"yaml": "- just\n- a\n- list\n"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "mapping" in body["error"]


def test_parse_yaml_rejects_semantically_invalid_config(client):
    cfg = _test_config_dict()
    cfg["pipelines"][0]["base"] = "does_not_exist"
    r = client.post("/api/parse_yaml", json={"yaml": yaml.dump(cfg)})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "not among sources" in body["error"]


def test_preview_returns_rows_for_valid_config(client):
    r = client.post("/api/preview", json={"config": _test_config_dict(), "pipeline_idx": 0, "limit": 10})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert len(body["rows"]) == 5


def test_preview_with_blank_base_returns_readable_error(client):
    # `base` is intentionally allowed to be blank through config validation (a pipeline
    # can be saved mid-edit before a source is marked as base — see Pipeline._check_refs),
    # but actually previewing it must fail with a readable message, not a bare
    # StopIteration (which stringifies to "") and a raw traceback.
    cfg = _test_config_dict()
    cfg["pipelines"][0]["base"] = ""
    r = client.post("/api/preview", json={"config": cfg, "pipeline_idx": 0, "limit": 10})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["error"] == "no base source selected"


def test_inspect_reads_source_schema(client):
    source = {"id": "places", "format": "geojson", "uri": "data/test.geojson", "layer": "test", "crs": "EPSG:4326"}
    r = client.post("/api/inspect", json={"source": source})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    col_names = {c["name"] for c in body["columns"]}
    assert {"name", "category"} <= col_names


def test_inspect_file_flatgeobuf_reads_layer_and_crs(client):
    # FlatGeobuf goes through the same generic ST_Read_Meta path as gpkg/shp (GDAL's
    # FlatGeobuf driver is bundled with the spatial extension) — no format-specific
    # branch needed, unlike parquet.
    r = client.post("/api/inspect_file", json={"uri": "data/test.fgb", "format": "flatgeobuf"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["layers"] == ["test"]
    assert body["default_crs"] == "EPSG:4326"


def test_inspect_reads_flatgeobuf_source_schema(client):
    source = {"id": "pts", "format": "flatgeobuf", "uri": "data/test.fgb", "crs": "EPSG:4326"}
    r = client.post("/api/inspect", json={"source": source})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    col_names = {c["name"] for c in body["columns"]}
    assert {"name", "category", "geom"} <= col_names


_WFS_CAPABILITIES_XML = """<?xml version="1.0" encoding="UTF-8"?>
<wfs:WFS_Capabilities xmlns:wfs="http://www.opengis.net/wfs/2.0" xmlns:app="http://example.com/app">
  <wfs:FeatureTypeList>
    <wfs:FeatureType>
      <wfs:Name>app:Fylke</wfs:Name>
      <wfs:Title>Fylke</wfs:Title>
      <wfs:DefaultCRS>urn:ogc:def:crs:EPSG::4258</wfs:DefaultCRS>
      <wfs:OtherCRS>urn:ogc:def:crs:EPSG::25833</wfs:OtherCRS>
    </wfs:FeatureType>
  </wfs:FeatureTypeList>
</wfs:WFS_Capabilities>"""


class _FakeCapabilitiesResponse:
    content = _WFS_CAPABILITIES_XML.encode("utf-8")

    def raise_for_status(self):
        pass


def test_inspect_file_wfs_reads_default_crs_from_leaf_element(client, monkeypatch):
    # Regression test: `find(...) or find(...)` on a leaf element like <DefaultCRS> (no
    # child elements) is always falsy under ElementTree's __bool__, so the old code fell
    # through to <DefaultSRS> (absent on any WFS 2.0 server) and always returned None here.
    monkeypatch.setattr("requests.get", lambda *a, **k: _FakeCapabilitiesResponse())

    r = client.post("/api/inspect_file", json={
        "uri": "https://host/wfs?service=WFS",
        "format": "wfs",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["layers"] == ["app:Fylke"]
    assert body["default_crs"] == "EPSG:4258"


def test_inspect_file_parquet_reads_embedded_crs_without_gdal(client):
    # Regression test: /api/inspect_file used to fall through to GDAL's ST_Read_Meta for
    # every format, which has no Parquet driver in the bundled GDAL build and always
    # errored for parquet sources (surfaced as a permanent "could not read this file"
    # badge on the source card). data/test.parquet is GeoParquet with its CRS embedded.
    r = client.post("/api/inspect_file", json={"uri": "data/test.parquet", "format": "parquet"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["layers"] == []
    assert body["default_crs"] == "EPSG:4326"


def test_inspect_file_parquet_normalizes_crs84_to_a_valid_crs(client):
    # Regression test: a GeoParquet file with no explicit CRS (defaults to OGC:CRS84 per
    # the spec) used to hand "OGC:CRS84" back as default_crs, which the editor writes
    # straight into the source's `crs` field — a value config.CRSStr rejects outright,
    # crashing pipeline validation. Must come back as something Source(...) accepts.
    r = client.post("/api/inspect_file", json={"uri": "data/test_crs84.parquet", "format": "parquet"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["default_crs"] == "EPSG:4326"
    Source.model_validate({"id": "pts", "format": "parquet", "uri": "data/test_crs84.parquet", "crs": body["default_crs"]})


def test_inspect_reads_parquet_source_schema(client):
    source = {"id": "pts", "format": "parquet", "uri": "data/test.parquet", "crs": "EPSG:4326"}
    r = client.post("/api/inspect", json={"source": source})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    col_names = {c["name"] for c in body["columns"]}
    assert {"name", "category", "geom"} <= col_names


def test_run_endpoint_writes_geopackage(client, tmp_path):
    cfg = _test_config_dict()
    cfg["output"] = str(tmp_path / "web_run.gpkg")
    r = client.post("/api/run", json={"config": cfg})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert Path(body["output"]) == tmp_path / "web_run.gpkg"
    assert (tmp_path / "web_run.gpkg").exists()


def test_files_endpoint_lists_data_directory(client):
    r = client.get("/api/files", params={"subpath": "data"})
    assert r.status_code == 200
    body = r.json()
    names = {e["name"] for e in body["entries"]}
    assert "test.geojson" in names
