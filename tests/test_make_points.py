"""The make_points step: point geometry from coordinate columns, so a pipeline whose base
source has no geometry has it from that step on (config.geometry_before_steps)."""
from pathlib import Path

import duckdb
import pytest

from duck_soup.config import Config, geometry_before_steps
from duck_soup.engine import Engine, run_config


def _config(tmp_path: Path, steps: list[dict], mapping=None, output="out.gpkg") -> Config:
    csv = tmp_path / "places.csv"
    csv.write_text("id,lon,lat\n1,10.75,59.91\n2,,\n3,east,1\n", encoding="utf-8")
    return Config.model_validate({"name": "t", "output": str(tmp_path / output), "pipelines": [{
        "name": "t", "working_crs": "EPSG:25833", "base": "places",
        "sources": [{"id": "places", "format": "csv", "uri": str(csv).replace("\\", "/")}],
        "steps": steps,
        "mapping": mapping if mapping is not None else [{"to": "id", "from": "id"}],
        "layers": [{"layer": "out"}],
    }]})


def _read(path, layer):
    con = duckdb.connect()
    con.execute("LOAD spatial")
    return con.execute(
        f"SELECT * EXCLUDE (geom), ST_AsText(geom) AS wkt FROM ST_Read('{path}', layer='{layer}') ORDER BY 1"
    ).fetchall()


def test_points_are_made_and_reprojected_to_the_layer(tmp_path):
    cfg = _config(tmp_path, [{"type": "make_points", "x": "lon", "y": "lat"}])
    run_config(cfg)
    rows = _read(cfg.output, "out")
    assert [r[1] for r in rows] == ["1", "2", "3"]
    x, y = map(float, rows[0][-1].removeprefix("POINT (").rstrip(")").split())
    assert 262_000 < x < 263_000 and 6_649_000 < y < 6_650_000  # Oslo in EPSG:25833
    assert rows[1][-1] is None and rows[2][-1] is None  # no number, no point


def test_rows_without_coordinates_go_to_rejects_with_their_columns(tmp_path):
    cfg = _config(tmp_path, [{"type": "make_points", "x": "lon", "y": "lat", "rejects": "no_coords"}])
    run_config(cfg)
    assert [r[1] for r in _read(cfg.output, "out")] == ["1"]
    rejected = _read(cfg.output, "no_coords")
    assert [(r[1], r[2]) for r in rejected] == [("2", ""), ("3", "east")]


def test_geometry_steps_and_funcs_work_after_it(tmp_path):
    cfg = _config(
        tmp_path,
        [{"type": "make_points", "x": "lon", "y": "lat", "rejects": "r"}, {"type": "buffer", "distance": 10}],
        mapping=[{"to": "id", "from": "id"}, {"to": "lon2", "func": "lon"}, {"to": "a", "func": "area"}],
    )
    rows = Engine(cfg.pipelines[0]).preview()
    assert rows[0]["lon2"] == pytest.approx(10.75) and rows[0]["a"] == pytest.approx(314, rel=0.01)
    assert rows[0]["__geojson"].startswith('{"type":"Polygon"')


def test_geometry_step_before_it_names_make_points(tmp_path):
    cfg = _config(tmp_path, [{"type": "combine_lines"}, {"type": "make_points", "x": "lon", "y": "lat"}])
    with pytest.raises(ValueError, match="put a make_points step before it"):
        Engine(cfg.pipelines[0]).preview()


def test_geometry_follows_the_chain_it_was_made_on():
    steps = Config.model_validate({"name": "t", "output": "o.gpkg", "pipelines": [{
        "name": "t", "base": "b", "sources": [{"id": "b", "format": "csv", "uri": "b.csv"}],
        "steps": [
            {"type": "snapshot", "id": "raw"},
            {"type": "make_points", "x": "x", "y": "y"},
            {"type": "filter", "where": "true", "branch": "raw"},
            {"type": "filter", "where": "true"},
        ],
        "mapping": [], "layers": [{"layer": "o"}],
    }]}).pipelines[0].steps
    before, end = geometry_before_steps(False, steps)
    assert before == [False, False, False, True] and end is True
    assert geometry_before_steps(False, steps[:1]) == ([False], False)


def test_no_geometry_csv_rejects_layer_is_written(tmp_path):
    # GDAL's OGC_FID used to go into a geometry-less layer without a mapping, which the
    # GeoPackage writer refuses.
    cfg = _config(tmp_path, [{"type": "filter", "where": "lon <> ''", "rejects": "r"}])
    run_config(cfg)
    assert [r[1] for r in _read(cfg.output, "r")] == ["2"]
