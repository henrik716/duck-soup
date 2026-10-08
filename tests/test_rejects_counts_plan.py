"""Step rejects (pass/reject split), row counts per step, the SQL plan, and run history."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from duck_soup import cli, history
from duck_soup.config import load_config_dict
from duck_soup.engine import (
    count_config_pipeline, plan_config_pipeline, preview_config_pipeline, run_config,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PIPELINES_DIR = REPO_ROOT / "pipelines"


@pytest.fixture(autouse=True)
def _repo_root_cwd(monkeypatch):
    monkeypatch.chdir(REPO_ROOT)


def _square(x0, y0, x1, y1) -> dict:
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


def _point(x, y) -> dict:
    return {"type": "Point", "coordinates": [x, y]}


def _geojson(path: Path, features: list[tuple[dict, dict]]) -> str:
    path.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": p, "geometry": g} for p, g in features],
    }), encoding="utf-8")
    return str(path)


# zone A covers x 0..2, zone B x 1..3 (both y 0..1).
_ZONES = [({"zone": "A", "code": 1}, _square(0, 0, 2, 1)), ({"zone": "B", "code": 2}, _square(1, 0, 3, 1))]
# p2 lies in both zones, p3 in neither, p4 only in B. kind: p1/p2 "x", p3 "y", p4 NULL.
_POINTS = [
    ({"name": "p1", "kind": "x"}, _point(0.5, 0.5)),
    ({"name": "p2", "kind": "x"}, _point(1.5, 0.5)),
    ({"name": "p3", "kind": "y"}, _point(10, 10)),
    ({"name": "p4", "kind": None}, _point(2.5, 0.5)),
]


def _cfg(tmp_path: Path, steps: list[dict], mapping=None, layers=None, output="out.gpkg", **extra) -> object:
    sources = [
        {"id": "base", "format": "geojson", "uri": _geojson(tmp_path / "base.geojson", _POINTS), "crs": "EPSG:25833"},
        {"id": "zones", "format": "geojson", "uri": _geojson(tmp_path / "zones.geojson", _ZONES), "crs": "EPSG:25833"},
    ]
    return load_config_dict({
        "name": "t", "output": str(tmp_path / output),
        "pipelines": [{
            "name": "p", "working_crs": "EPSG:25833", "sources": sources, "base": "base",
            "steps": steps, "mapping": mapping or [],
            "layers": layers or [{"layer": "out", "crs": "EPSG:25833"}], **extra,
        }],
    })


def _layer_rows(path: Path, layer: str, cols: str) -> list[tuple]:
    con = sqlite3.connect(str(path))
    try:
        return con.execute(f'SELECT {cols} FROM "{layer}" ORDER BY 1').fetchall()
    finally:
        con.close()


def _layer_columns(path: Path, layer: str) -> list[str]:
    con = sqlite3.connect(str(path))
    try:
        return [r[1] for r in con.execute(f'PRAGMA table_info("{layer}")').fetchall()]
    finally:
        con.close()


# ---- rejects ---------------------------------------------------------------

def test_filter_rejects_get_their_own_layer_without_the_mapping(tmp_path):
    cfg = _cfg(
        tmp_path, [{"type": "filter", "where": "kind = 'x'", "rejects": "not_x"}],
        mapping=[{"to": "label", "expr": "upper(name)"}],
    )
    written: list[dict] = []
    out = Path(run_config(cfg, written=written))

    assert _layer_rows(out, "out", "label") == [("P1",), ("P2",)]
    # NULL isn't true either, so p4 is a reject too. The mapping isn't applied to rejects.
    assert _layer_rows(out, "not_x", "name, kind") == [("p3", "y"), ("p4", None)]
    assert [(w["layer"], w["rows"], w["kind"]) for w in written] == [("out", 2, "layer"), ("not_x", 2, "rejects")]


@pytest.mark.parametrize("match", ["first", "all"])
def test_spatial_join_rejects_take_unmatched_rows_out_of_the_chain(tmp_path, match):
    cfg = _cfg(tmp_path, [{
        "type": "spatial_join", "source": "zones", "match": match,
        "fields": {"zone": "zone"}, "rejects": "no_zone",
    }])
    out = Path(run_config(cfg))

    passed = _layer_rows(out, "out", "name, zone")
    assert ("p3", None) not in passed
    assert {r[0] for r in passed} == {"p1", "p2", "p4"}
    assert _layer_rows(out, "no_zone", "name") == [("p3",)]
    # The pulled field is always NULL on a reject, so it isn't written there.
    assert "zone" not in _layer_columns(out, "no_zone")


def test_unparseable_geometry_text_goes_to_spatial_join_rejects(tmp_path):
    # A malformed WKT value gives the row a NULL geometry instead of failing the run; it
    # can't match a spatial join, so it ends up among the step's rejects. The column is named
    # WKT, which GDAL's CSV driver turns into a geometry field of its own (see
    # _apply_tabular_geometry): the output still gets exactly one geometry column.
    csv = tmp_path / "pts.csv"
    csv.write_text("name,WKT\np1,POINT(0.5 0.5)\nbroken,POINT(1 x\n", encoding="utf-8")
    cfg = load_config_dict({
        "name": "t", "output": str(tmp_path / "out.gpkg"),
        "pipelines": [{
            "name": "p", "working_crs": "EPSG:25833", "base": "base",
            "sources": [
                {"id": "base", "format": "csv", "uri": str(csv).replace("\\", "/"),
                 "geom_field": "WKT", "crs": "EPSG:25833"},
                {"id": "zones", "format": "geojson", "uri": _geojson(tmp_path / "zones.geojson", _ZONES),
                 "crs": "EPSG:25833"},
            ],
            "steps": [{
                "type": "spatial_join", "source": "zones", "fields": {"zone": "zone"}, "rejects": "no_zone",
            }],
            "mapping": [], "layers": [{"layer": "out", "crs": "EPSG:25833"}],
        }],
    })
    out = Path(run_config(cfg))

    assert _layer_rows(out, "out", "name, zone") == [("p1", "A")]
    assert _layer_rows(out, "no_zone", "name") == [("broken",)]


def test_join_without_fields_still_splits_on_rejects(tmp_path):
    cfg = _cfg(tmp_path, [{"type": "spatial_join", "source": "zones", "rejects": "no_zone"}])
    assert [r["name"] for r in preview_config_pipeline(cfg, limit=10)] == ["p1", "p2", "p4"]
    assert [r["name"] for r in preview_config_pipeline(cfg, limit=10, preview_until_step=1, rejects=True)] == ["p3"]


def test_attribute_join_rejects(tmp_path):
    cfg = _cfg(tmp_path, [{
        "type": "attribute_join", "source": "zones", "left": "'A'", "right": "zone",
        "fields": {"code": "code"},
    }, {
        "type": "attribute_join", "source": "zones", "left": "kind", "right": "zone",
        "fields": {"code2": "code"}, "rejects": "no_kind_zone",
    }])
    # No row's `kind` is a zone name: everything is rejected by step 2.
    assert preview_config_pipeline(cfg, limit=10, preview_until_step=2) == []
    rejected = preview_config_pipeline(cfg, limit=10, preview_until_step=2, rejects=True)
    assert [(r["name"], r["code"]) for r in rejected] == [("p1", 1), ("p2", 1), ("p3", 1), ("p4", 1)]
    assert "code2" not in rejected[0]


def test_nearest_neighbor_rejects_rows_out_of_range_and_drops_distance_field(tmp_path):
    cfg = _cfg(tmp_path, [{
        "type": "nearest_neighbor", "source": "zones", "max_distance": 1,
        "distance_field": "dist", "fields": {"zone": "zone"}, "rejects": "far",
    }])
    assert [r["name"] for r in preview_config_pipeline(cfg, limit=10, preview_until_step=1)] == ["p1", "p2", "p4"]
    (far,) = preview_config_pipeline(cfg, limit=10, preview_until_step=1, rejects=True)
    assert far["name"] == "p3" and "dist" not in far and "zone" not in far


def test_clip_rejects_keep_their_unclipped_geometry(tmp_path):
    base = [
        ({"name": "inside"}, _square(0.5, 0.2, 1.5, 0.8)),
        ({"name": "touching"}, _square(3, 0, 4, 1)),  # shares only B's right edge
        ({"name": "outside"}, _square(10, 10, 11, 11)),
    ]
    cfg = load_config_dict({
        "name": "t", "output": str(tmp_path / "out.gpkg"),
        "pipelines": [{
            "name": "p", "working_crs": "EPSG:25833", "base": "base", "mapping": [],
            "sources": [
                {"id": "base", "format": "geojson", "uri": _geojson(tmp_path / "b.geojson", base), "crs": "EPSG:25833"},
                {"id": "zones", "format": "geojson", "uri": _geojson(tmp_path / "z.geojson", _ZONES), "crs": "EPSG:25833"},
            ],
            "steps": [{"type": "clip", "source": "zones", "rejects": "unclipped"}],
            "layers": [{"layer": "out", "crs": "EPSG:25833"}],
        }],
    })
    assert [r["name"] for r in preview_config_pipeline(cfg, limit=10, preview_until_step=1)] == ["inside"]
    rejected = preview_config_pipeline(cfg, limit=10, preview_until_step=1, rejects=True)
    assert [r["name"] for r in rejected] == ["touching", "outside"]
    outside = json.loads(rejected[1]["__geojson"])
    assert outside["type"] == "Polygon"


def test_steps_without_rejects_behave_as_before(tmp_path):
    cfg = _cfg(tmp_path, [{"type": "spatial_join", "source": "zones", "fields": {"zone": "zone"}}])
    rows = preview_config_pipeline(cfg, limit=10, preview_until_step=1)
    assert [(r["name"], r["zone"]) for r in rows] == [("p1", "A"), ("p2", "A"), ("p3", None), ("p4", "B")]
    with pytest.raises(ValueError, match="no rejects layer"):
        preview_config_pipeline(cfg, limit=10, preview_until_step=1, rejects=True)


def test_rejects_name_must_not_collide_with_a_layer_or_other_rejects(tmp_path):
    with pytest.raises(ValidationError, match="already used"):
        _cfg(tmp_path, [{"type": "filter", "where": "true", "rejects": "OUT"}])
    with pytest.raises(ValidationError, match="already used"):
        _cfg(tmp_path, [
            {"type": "filter", "where": "true", "rejects": "r"},
            {"type": "filter", "where": "true", "rejects": "r"},
        ])
    # The editor sends a blank field for "not set".
    cfg = _cfg(tmp_path, [{"type": "filter", "where": "true", "rejects": "  "}])
    assert cfg.pipelines[0].steps[0].rejects is None


def test_rejects_count_as_layers_for_geoparquet(tmp_path):
    cfg = _cfg(tmp_path, [{"type": "filter", "where": "kind = 'x'", "rejects": "rest"}], output="out.parquet")
    assert cfg.layer_count == 2
    root = Path(run_config(cfg))
    assert root == tmp_path / "out"
    assert {p.name for p in root.iterdir()} == {"out.parquet", "rest.parquet"}


def test_rejects_written_last_in_yaml():
    from duck_soup.config import dump_config_yaml

    cfg = load_config_dict({
        "name": "t", "output": "o.gpkg", "pipelines": [{
            "name": "p", "sources": [{"id": "b", "format": "geojson", "uri": "b.geojson"}],
            "base": "b", "mapping": [], "layers": [{"layer": "l"}],
            "steps": [{"type": "filter", "where": "true", "rejects": "r"}],
        }],
    })
    step = yaml.safe_load(dump_config_yaml(cfg))["pipelines"][0]["steps"][0]
    assert list(step) == ["type", "where", "rejects"]


# ---- counts ----------------------------------------------------------------

def test_counts_per_step_rejects_and_layer(tmp_path):
    cfg = _cfg(
        tmp_path,
        [
            {"type": "spatial_join", "source": "zones", "match": "all", "fields": {"zone": "zone"}},
            {"type": "filter", "where": "kind = 'x'", "rejects": "not_x"},
        ],
        layers=[{"layer": "out", "crs": "EPSG:25833"}, {"layer": "in_a", "crs": "EPSG:25833", "filter": "zone = 'A'"}],
    )
    full = count_config_pipeline(cfg, limit=None)
    # match=all: p2 lies in A and B, so 5 rows; 3 of them (p1, p2 twice) have kind x.
    assert full["base"] == 4
    assert full["steps"] == {1: 5, 2: 3}
    assert full["rejects"] == {2: 2}
    assert full["layers"] == [3, 2]
    assert full["sources"] == {"base": 4, "zones": 2}

    sample = count_config_pipeline(cfg, limit=2)
    assert sample["limit"] == 2 and sample["base"] == 2
    assert sample["steps"] == {1: 3, 2: 3} and sample["sources"] == {}


def test_counts_cover_snapshot_branches(tmp_path):
    cfg = _cfg(tmp_path, [
        {"type": "snapshot", "id": "snap"},
        {"type": "filter", "branch": "snap", "where": "kind = 'y'"},
        {"type": "filter", "where": "kind = 'x'"},
    ])
    assert count_config_pipeline(cfg, limit=None)["steps"] == {1: 4, 2: 1, 3: 2}


# ---- plan ------------------------------------------------------------------

def test_plan_lists_every_view_without_reading_data(tmp_path):
    cfg = load_config_dict({
        "name": "t", "output": "o.gpkg", "pipelines": [{
            "name": "p", "working_crs": "EPSG:25833", "base": "places",
            # None of these exist or are reachable: the plan mustn't touch them.
            "sources": [
                {"id": "places", "format": "geojson", "uri": str(tmp_path / "missing.geojson"), "crs": "EPSG:4326"},
                {"id": "zones", "format": "wfs", "uri": "https://example.invalid/wfs", "layer": "z"},
                {"id": "db", "format": "postgres", "uri": "postgresql://me:s3cret@db.invalid/gis", "layer": "x.t"},
            ],
            "derived_sources": [{"id": "big", "from": "zones", "buffer": 10}],
            "steps": [
                {"type": "spatial_join", "source": "big", "fields": {"z": "name"}, "rejects": "nowhere"},
                {"type": "attribute_join", "source": "db", "left": "z", "right": "k", "fields": {"v": "v"}},
            ],
            "mapping": [{"to": "z", "from": "z"}],
            "layers": [{"layer": "out", "crs": "EPSG:25833"}],
        }],
    })
    plan = plan_config_pipeline(cfg)
    assert [(e["kind"], e.get("step")) for e in plan] == [
        ("source", None), ("source", None), ("source", None), ("derived", None), ("base", None),
        ("split", 1), ("step", 1), ("rejects", 1), ("step", 2), ("layer", None),
    ]
    sql = "\n".join(e["sql"] for e in plan)
    assert "missing.geojson" in sql and "ST_Transform" in sql
    assert "s3cret" not in sql
    assert plan[-1]["sql"].startswith("WITH base4326")
    assert "WHERE NOT __matched" in plan[7]["sql"]


# ---- run history -----------------------------------------------------------

def _record(root: Path, name: str, started: datetime, ok: bool = True) -> Path:
    return history.record_run(
        root, name, trigger="cli", started=started, finished=started + timedelta(seconds=2),
        ok=ok, output="o.gpkg", layers=[{"layer": "l", "rows": 3}], log=["hi"], yaml_text="name: x\n",
    )


def test_history_records_lists_and_reads_runs(tmp_path):
    t0 = datetime(2026, 10, 8, 2, 0, tzinfo=timezone.utc)
    _record(tmp_path, "nightly", t0)
    path = _record(tmp_path, "nightly", t0 + timedelta(days=1), ok=False)
    assert path.parent == tmp_path / "runs" / "nightly"

    runs = history.list_runs(tmp_path, "nightly")
    assert [r["ok"] for r in runs] == [False, True]  # newest first
    assert runs[0]["duration_s"] == 2 and runs[0]["layers"] == [{"layer": "l", "rows": 3}]
    assert "log" not in runs[0] and "yaml" not in runs[0]

    full = history.get_run(tmp_path, "nightly", runs[0]["id"])
    assert full["log"] == ["hi"] and full["yaml"] == "name: x\n"
    assert history.get_run(tmp_path, "nightly", "../../etc/passwd") is None
    assert history.list_runs(tmp_path, "other") == []


def test_history_keeps_the_newest_runs_and_safe_folder_names(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "MAX_RUNS", 3)
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(5):
        _record(tmp_path, "a/../b c", t0 + timedelta(minutes=i))
    folder = history.config_dir(tmp_path, "a/../b c")
    assert folder.parent == tmp_path / "runs"
    runs = history.list_runs(tmp_path, "a/../b c")
    assert len(runs) == 3 and runs[0]["started_at"].startswith("2026-01-01T00:04")


def test_cli_run_records_history(tmp_path):
    cfg = yaml.safe_load((PIPELINES_DIR / "test.yaml").read_text(encoding="utf-8"))
    cfg["output"] = str(tmp_path / "out.gpkg")
    pipeline = tmp_path / "nightly.yaml"
    pipeline.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    assert cli.main(["run", str(pipeline), "--history-dir", str(tmp_path)]) == 0
    (run,) = history.list_runs(tmp_path, "nightly")
    assert run["ok"] and run["trigger"] == "cli"
    assert [(w["layer"], w["rows"]) for w in run["layers"]] == [("output", 5)]

    cfg["pipelines"][0]["sources"][0]["uri"] = str(tmp_path / "gone.geojson")
    pipeline.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    with pytest.raises(Exception):
        cli.main(["run", str(pipeline), "--history-dir", str(tmp_path)])
    failed = history.list_runs(tmp_path, "nightly")[0]
    assert failed["ok"] is False and failed["error"]


def test_cli_run_defaults_to_duck_soup_root(tmp_path, _isolated_run_history):
    cfg = yaml.safe_load((PIPELINES_DIR / "test.yaml").read_text(encoding="utf-8"))
    cfg["output"] = str(tmp_path / "out.gpkg")
    pipeline = tmp_path / "p.yaml"
    pipeline.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    assert cli.main(["run", str(pipeline)]) == 0
    assert len(history.list_runs(_isolated_run_history, "p")) == 1
    assert cli.main(["run", str(pipeline), "--no-history"]) == 0
    assert len(history.list_runs(_isolated_run_history, "p")) == 1


# ---- web API ---------------------------------------------------------------

@pytest.fixture()
def client():
    from duck_soup.web.app import app

    return TestClient(app)


def test_api_plan_counts_and_rejects_preview(client, tmp_path):
    cfg = _cfg(tmp_path, [{"type": "filter", "where": "kind = 'x'", "rejects": "rest"}])
    config = cfg.model_dump(by_alias=True, exclude_none=True)

    plan = client.post("/api/plan", json={"config": config}).json()
    assert plan["ok"] and [e["kind"] for e in plan["plan"]][-4:] == ["split", "step", "rejects", "layer"]

    counts = client.post("/api/counts", json={"config": config, "limit": None}).json()
    assert counts["ok"] and counts["steps"] == {"1": 2} and counts["rejects"] == {"1": 2}

    rows = client.post("/api/preview", json={
        "config": config, "preview_until_step": 1, "rejects": True,
    }).json()["rows"]
    assert [r["name"] for r in rows] == ["p3", "p4"]

    bad = client.post("/api/plan", json={"config": config, "pipeline_idx": 3}).json()
    assert bad["ok"] is False


def test_api_run_records_history_under_the_saved_name(client, tmp_path, _isolated_run_history):
    cfg = _cfg(tmp_path, [{"type": "filter", "where": "kind = 'x'", "rejects": "rest"}])
    config = cfg.model_dump(by_alias=True, exclude_none=True)

    body = client.post("/api/run", json={"config": config, "name": "my config"}).json()
    assert body["ok"]
    assert [(w["layer"], w["rows"], w["kind"]) for w in body["layers"]] == [("out", 2, "layer"), ("rest", 2, "rejects")]

    runs = client.get("/api/runs/my config").json()["runs"]
    assert len(runs) == 1 and runs[0]["trigger"] == "editor" and runs[0]["ok"]
    full = client.get(f"/api/runs/my config/{runs[0]['id']}").json()
    assert any("filter" in line for line in full["log"])
    assert client.get("/api/runs/my config/20260101-000000-000000").status_code == 404
    assert (_isolated_run_history / "runs" / "my_config").is_dir()
