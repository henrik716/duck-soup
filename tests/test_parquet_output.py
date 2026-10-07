"""GeoParquet output: a .parquet/.geoparquet output path is written with DuckDB's native
Parquet writer, one file per layer when there's more than one."""
from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from duck_soup.config import load_config, load_config_dict
from duck_soup.derive import init_duckdb
from duck_soup.engine import run_config
from duck_soup.sources import parquet_geometry_info

REPO_ROOT = Path(__file__).resolve().parents[1]
PIPELINES_DIR = REPO_ROOT / "pipelines"


@pytest.fixture(autouse=True)
def _repo_root_cwd(monkeypatch):
    # Source `uri`s in the fixture YAMLs are relative (e.g. data/test.geojson).
    monkeypatch.chdir(REPO_ROOT)


@pytest.fixture
def con():
    c = duckdb.connect()
    init_duckdb(c)
    yield c
    c.close()


def _count(con, path: Path) -> int:
    return con.execute(f"SELECT COUNT(*) FROM read_parquet('{path.as_posix()}')").fetchone()[0]


def test_single_layer_writes_geoparquet_with_layer_crs(tmp_path, con):
    cfg = load_config(PIPELINES_DIR / "test.yaml")
    cfg.output = str(tmp_path / "test.parquet")
    layer_crs = cfg.pipelines[0].layers[0].crs

    out_path = run_config(cfg)

    assert Path(out_path) == tmp_path / "test.parquet"
    assert _count(con, Path(out_path)) == 5
    cols = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{Path(out_path).as_posix()}')").fetchall()}
    assert {"name", "category", "county", "geom"} <= cols
    # Without an explicit CRS in the `geo` metadata, readers would assume OGC:CRS84.
    assert parquet_geometry_info(con, out_path) == ("geom", layer_crs)


def test_multi_layer_writes_one_file_per_layer(tmp_path, con):
    cfg = load_config(PIPELINES_DIR / "test_multi_layer.yaml")
    cfg.output = str(tmp_path / "multi.parquet")

    out_path = run_config(cfg)

    assert Path(out_path) == tmp_path / "multi"
    files = {p.name for p in Path(out_path).glob("*.parquet")}
    assert files == {"matched.parquet", "unmatched.parquet"}
    # every base feature ends up in exactly one of the two filtered layers
    assert sum(_count(con, Path(out_path) / f) for f in files) == 5


def test_multi_pipeline_shared_output_writes_one_file_per_layer(tmp_path):
    cfg = load_config(PIPELINES_DIR / "test_multi_pipeline_shared_output.yaml")
    cfg.output = str(tmp_path / "shared.parquet")

    out_path = run_config(cfg)

    assert {p.name for p in Path(out_path).glob("*.parquet")} == {"places_out.parquet", "fylke_out.parquet"}


def test_overwrite_clears_stale_layer_files(tmp_path):
    folder = tmp_path / "multi"
    folder.mkdir()
    (folder / "old_layer.parquet").write_bytes(b"stale")
    (folder / "notes.txt").write_text("keep me")
    cfg = load_config(PIPELINES_DIR / "test_multi_layer.yaml")
    cfg.output = str(tmp_path / "multi.parquet")

    run_config(cfg)

    assert not (folder / "old_layer.parquet").exists()
    assert (folder / "notes.txt").exists()


def test_rerun_replaces_single_file(tmp_path, con):
    cfg = load_config(PIPELINES_DIR / "test.yaml")
    cfg.output = str(tmp_path / "test.geoparquet")

    run_config(cfg)
    out_path = run_config(cfg)

    assert _count(con, Path(out_path)) == 5


def test_non_geometry_base_writes_plain_parquet(tmp_path, con):
    csv = tmp_path / "lookup.csv"
    csv.write_text("code,label\n1,one\n2,two\n", encoding="utf-8")
    cfg = load_config_dict({
        "name": "t",
        "sources": [{"id": "lookup", "format": "csv", "uri": str(csv)}],
        "base": "lookup",
        "mapping": [{"to": "code", "from": "code"}, {"to": "label", "from": "label"}],
        "output": {"path": str(tmp_path / "plain.parquet"), "layer": "out"},
    })

    out_path = run_config(cfg)

    assert con.execute(f"SELECT label FROM read_parquet('{Path(out_path).as_posix()}') ORDER BY code").fetchall() == [
        ("one",), ("two",)
    ]


@pytest.mark.parametrize("names,msg", [
    (["a", "A"], "used twice"),
    (["a/b"], "file name"),
    (["  "], "file name"),
])
def test_parquet_layer_names_must_be_distinct_filenames(names, msg):
    pipelines = [{
        "name": f"p{i}", "sources": [{"id": "s", "format": "geojson", "uri": "x.geojson", "crs": "EPSG:4326"}],
        "base": "s", "mapping": [], "layers": [{"layer": n}],
    } for i, n in enumerate(names)]
    with pytest.raises(ValueError, match=msg):
        load_config_dict({"name": "t", "output": "out.parquet", "pipelines": pipelines})
    # The same names are fine in a GeoPackage, where layers are tables, not files.
    if msg == "used twice":
        load_config_dict({"name": "t", "output": "out.gpkg", "pipelines": pipelines})
