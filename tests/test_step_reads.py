"""Every step reads the view before it once. Views are expanded wherever they're used, so a
step reading its input twice computes the whole chain before it twice, and two such steps in
a row four times: five chained line_overlays read the base source 32 times."""
import pytest

import duck_soup.engine as engine_mod
import duck_soup.sources as sources_mod
from duck_soup.config import Config
from duck_soup.derive import init_duckdb
from duck_soup.engine import Engine

_STEPS = {
    "spatial_join": {"fields": {"f": "name"}},
    "spatial_join_all": {"type": "spatial_join", "match": "all", "fields": {"f": "name"}},
    "spatial_join_rejects": {"type": "spatial_join", "fields": {"f": "name"}, "rejects": "r"},
    "attribute_join": {"left": "name", "right": "name", "fields": {"f": "name"}},
    "nearest_neighbor": {"fields": {"f": "name"}},
    "buffer": {"distance": 1},
    "centroid": {},
    "clip": {},
    "erase": {},
    "dissolve": {"by": ["name"]},
    "intersect_overlay": {"fields": {"f": "name"}},
    "line_overlay": {"fields": {"f": "name"}},
    "filter": {"where": "name IS NOT NULL"},
    "merge": {},
}
_SOURCELESS = {"buffer", "centroid", "dissolve", "filter"}


@pytest.fixture
def reads(monkeypatch):
    """Each source's read is one row through a counting UDF: how often a source is read."""
    counts: dict[str, int] = {}

    def tick(sid, name):
        counts[sid] = counts.get(sid, 0) + 1
        return name

    def init(con):
        init_duckdb(con)
        con.create_function("tick", tick, ["VARCHAR", "VARCHAR"], "VARCHAR", side_effects=True)

    monkeypatch.setattr(engine_mod, "init_duckdb", init)
    monkeypatch.setattr(sources_mod, "read_expr", lambda src, *a, **k: (
        f"(SELECT tick('{src.id}', 'x') AS name, "
        f"ST_GeomFromText('LINESTRING (0 0, 10 0)') AS geom)"
    ))
    return counts


def _pipeline(tmp_path, kind: str, n_steps: int, n_layers: int = 1):
    spec = dict(_STEPS[kind])
    step_type = spec.pop("type", kind)
    steps = []
    for i in range(n_steps):
        step = {"type": step_type, **spec}
        if step_type not in _SOURCELESS:
            step["source"] = f"s{i}"
        if "fields" in step:
            step["fields"] = {f"f{i}": "name"}
        if "rejects" in step:
            step["rejects"] = f"rejects_{i}"
        steps.append(step)
    cfg = Config.model_validate({"name": "t", "output": str(tmp_path / "out.gpkg"), "pipelines": [{
        "name": "t", "working_crs": "EPSG:25833", "base": "base", "steps": steps,
        "sources": [
            {"id": sid, "format": "geojson", "uri": "unused.geojson", "crs": "EPSG:25833"}
            for sid in ["base", *(f"s{i}" for i in range(n_steps))]
        ],
        "mapping": [{"to": "name", "from": "name"}],
        "layers": [{"layer": f"out{i}"} for i in range(n_layers)],
    }]})
    return cfg.pipelines[0].to_pipeline(cfg.output)


@pytest.mark.parametrize("kind", _STEPS)
def test_chained_steps_read_each_source_once_in_preview(tmp_path, reads, kind):
    Engine(_pipeline(tmp_path, kind, n_steps=3)).preview()
    assert reads["base"] == 1, reads
    assert all(n == 1 for n in reads.values()), reads


def test_run_reads_each_source_once_for_every_layer(tmp_path, reads):
    # Two layers plus a rejects layer: three COPYs over one chain.
    pipeline = _pipeline(tmp_path, "spatial_join_rejects", n_steps=2, n_layers=2)
    Engine(pipeline).run()
    assert reads == {"base": 1, "s0": 1, "s1": 1}


# A step that reads a snapshot reads the chain up to it a second time (see
# Engine._build_step_views); each pattern is repeated, so a doubling would compound.
_SNAPSHOT_PATTERNS = {
    "join_snapshot": lambda i: [
        {"type": "snapshot", "id": f"snap{i}"},
        {"type": "spatial_join", "source": f"snap{i}", "fields": {f"f{i}": "name"}},
    ],
    "merge_snapshot": lambda i: [
        {"type": "snapshot", "id": f"snap{i}"},
        {"type": "merge", "source": f"snap{i}"},
    ],
    "branch_then_join_back": lambda i: [
        {"type": "snapshot", "id": f"snap{i}"},
        {"type": "buffer", "distance": 1, "branch": f"snap{i}"},
        {"type": "spatial_join", "source": f"snap{i}", "fields": {f"f{i}": "name"}},
    ],
}


def _snapshot_pipeline(tmp_path, steps):
    cfg = Config.model_validate({"name": "t", "output": str(tmp_path / "out.gpkg"), "pipelines": [{
        "name": "t", "working_crs": "EPSG:25833", "base": "base", "steps": steps,
        "sources": [{"id": "base", "format": "geojson", "uri": "unused.geojson", "crs": "EPSG:25833"}],
        "mapping": [{"to": "name", "from": "name"}], "layers": [{"layer": "out"}],
    }]})
    return cfg.pipelines[0].to_pipeline(cfg.output)


@pytest.mark.parametrize("pattern", _SNAPSHOT_PATTERNS)
def test_snapshots_read_again_later_are_computed_once(tmp_path, reads, pattern):
    steps = [s for i in range(3) for s in _SNAPSHOT_PATTERNS[pattern](i)]
    Engine(_snapshot_pipeline(tmp_path, steps)).preview()
    assert reads == {"base": 1}


def test_snapshot_nothing_reads_stays_a_view(tmp_path, reads):
    # Stored as a table only when read again: otherwise the preview's LIMIT can still cut
    # the chain short.
    steps = [{"type": "snapshot", "id": "snap"}, {"type": "buffer", "distance": 1}]
    engine = Engine(_snapshot_pipeline(tmp_path, steps))
    engine.preview()
    assert [e["view"] for e in engine.plan if e["kind"] == "step"] == ["step_2"]
