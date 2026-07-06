"""End-to-end pipeline execution against the checked-in fixture YAMLs +
data/ files, asserting on the GeoPackage(s) actually written to disk.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from duck_soup.config import load_config
from duck_soup.engine import preview_config_pipeline, run_config

REPO_ROOT = Path(__file__).resolve().parents[1]
PIPELINES_DIR = REPO_ROOT / "pipelines"


def _gpkg_layers(path: Path) -> dict[str, int]:
    """{layer_name: row_count} for every feature layer in a GeoPackage."""
    con = sqlite3.connect(str(path))
    try:
        names = [r[0] for r in con.execute("SELECT table_name FROM gpkg_contents").fetchall()]
        return {name: con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in names}
    finally:
        con.close()


@pytest.fixture(autouse=True)
def _repo_root_cwd(monkeypatch):
    # Source `uri`s in the fixture YAMLs are relative (e.g. data/test.geojson),
    # resolved by DuckDB against the process cwd.
    monkeypatch.chdir(REPO_ROOT)


def test_run_single_pipeline_writes_expected_columns(tmp_path):
    cfg = load_config(PIPELINES_DIR / "test.yaml")
    cfg.output = str(tmp_path / "test.gpkg")

    out_path = run_config(cfg)

    assert Path(out_path) == tmp_path / "test.gpkg"
    layers = _gpkg_layers(Path(out_path))
    assert layers == {"output": 5}

    con = sqlite3.connect(out_path)
    try:
        cols = {r[1] for r in con.execute('PRAGMA table_info("output")').fetchall()}
    finally:
        con.close()
    assert {"name", "category", "latitude", "longitude", "mgrs", "uuid", "county"} <= cols


def test_run_multi_layer_pipeline_splits_matched_and_unmatched(tmp_path):
    cfg = load_config(PIPELINES_DIR / "test_multi_layer.yaml")
    cfg.output = str(tmp_path / "multi_layer.gpkg")

    out_path = run_config(cfg)

    layers = _gpkg_layers(Path(out_path))
    assert set(layers) == {"matched", "unmatched"}
    # every base feature ends up in exactly one of the two filtered layers
    assert sum(layers.values()) == 5


def test_run_multi_pipeline_shared_output(tmp_path):
    cfg = load_config(PIPELINES_DIR / "test_multi_pipeline_shared_output.yaml")
    cfg.output = str(tmp_path / "shared.gpkg")

    out_path = run_config(cfg)

    layers = _gpkg_layers(Path(out_path))
    assert set(layers) == {"places_out", "fylke_out"}
    assert layers["places_out"] == 5
    assert layers["fylke_out"] > 0


def test_preview_pipeline_returns_rows_without_writing_output(tmp_path):
    cfg = load_config(PIPELINES_DIR / "test.yaml")
    cfg.output = str(tmp_path / "unused.gpkg")

    rows = preview_config_pipeline(cfg, pipeline_idx=0, limit=10)

    assert len(rows) == 5
    assert not (tmp_path / "unused.gpkg").exists()
    assert {"name", "category", "county"} <= rows[0].keys()
