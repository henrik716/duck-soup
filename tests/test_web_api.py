"""FastAPI endpoint tests for the pipeline editor backend (duck_soup/web/app.py)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

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


def test_preview_returns_rows_for_valid_config(client):
    r = client.post("/api/preview", json={"config": _test_config_dict(), "pipeline_idx": 0, "limit": 10})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert len(body["rows"]) == 5


def test_inspect_reads_source_schema(client):
    source = {"id": "places", "format": "geojson", "uri": "data/test.geojson", "layer": "test", "crs": "EPSG:4326"}
    r = client.post("/api/inspect", json={"source": source})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    col_names = {c["name"] for c in body["columns"]}
    assert {"name", "category"} <= col_names


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
