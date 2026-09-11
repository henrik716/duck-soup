"""Pydantic schema validation: valid fixture configs, legacy upgrade, and
the cross-reference / shape checks that should reject a bad pipeline.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from duck_soup.config import (
    CodeCase,
    CodeList,
    Config,
    MapItem,
    Source,
    load_config,
    load_config_dict,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PIPELINES_DIR = REPO_ROOT / "pipelines"


def test_load_single_pipeline_fixture():
    cfg = load_config(PIPELINES_DIR / "test.yaml")
    assert cfg.name == "test"
    assert len(cfg.pipelines) == 1
    pdef = cfg.pipelines[0]
    assert pdef.base == "places"
    assert {s.id for s in pdef.sources} == {"places", "fylke"}
    assert len(pdef.layers) == 1
    assert pdef.layers[0].layer == "output"


def test_load_multi_layer_fixture():
    cfg = load_config(PIPELINES_DIR / "test_multi_layer.yaml")
    assert len(cfg.pipelines) == 1
    layers = {l.layer for l in cfg.pipelines[0].layers}
    assert layers == {"matched", "unmatched"}


def test_load_multi_pipeline_shared_output_fixture():
    cfg = load_config(PIPELINES_DIR / "test_multi_pipeline_shared_output.yaml")
    assert len(cfg.pipelines) == 2
    names = {p.name for p in cfg.pipelines}
    assert names == {"places_layer", "fylke_layer"}


def test_legacy_single_pipeline_format_upgrades_to_config():
    legacy = {
        "name": "legacy_test",
        "sources": [
            {"id": "places", "format": "geojson", "uri": "data/test.geojson", "layer": "test", "crs": "EPSG:4326"},
        ],
        "base": "places",
        "mapping": [{"to": "name", "from": "name"}],
        "output": {"path": "output/legacy.gpkg", "layer": "output", "crs": "EPSG:25833"},
    }
    cfg = load_config_dict(legacy)
    assert isinstance(cfg, Config)
    assert cfg.output == "output/legacy.gpkg"
    assert len(cfg.pipelines) == 1
    assert cfg.pipelines[0].name == "legacy_test"
    assert cfg.pipelines[0].layers[0].layer == "output"


def test_invalid_crs_format_rejected():
    with pytest.raises(ValidationError, match="invalid CRS"):
        Source(id="a", format="geojson", uri="x.geojson", crs="not-a-crs")


def test_unknown_base_reference_rejected():
    bad = {
        "name": "bad",
        "output": "output/bad.gpkg",
        "pipelines": [{
            "name": "p1",
            "sources": [{"id": "a", "format": "geojson", "uri": "x.geojson"}],
            "base": "does_not_exist",
            "mapping": [{"to": "name", "from": "name"}],
            "layers": [{"layer": "out", "crs": "EPSG:25833"}],
        }],
    }
    with pytest.raises(ValidationError, match="not among sources"):
        load_config_dict(bad)


def test_step_referencing_unknown_branch_rejected():
    bad = {
        "name": "bad",
        "output": "output/bad.gpkg",
        "pipelines": [{
            "name": "p1",
            "sources": [{"id": "a", "format": "geojson", "uri": "x.geojson"}],
            "base": "a",
            "steps": [{"type": "buffer", "distance": 1, "branch": "nope"}],
            "mapping": [{"to": "name", "from": "name"}],
            "layers": [{"layer": "out", "crs": "EPSG:25833"}],
        }],
    }
    with pytest.raises(ValidationError, match="unknown branch"):
        load_config_dict(bad)


def test_codelist_requires_exactly_one_of_cases_or_file():
    with pytest.raises(ValidationError, match="cases.*or.*file|either inline"):
        CodeList(source="category")


def test_codelist_rejects_both_cases_and_file():
    with pytest.raises(ValidationError):
        CodeList(
            source="category",
            cases=[{"match": "x", "value": "y"}],
            file="lookup.csv",
            file_match_col="a",
            file_value_col="b",
        )


def test_codelist_with_only_cases_is_valid():
    cl = CodeList(source="category", cases=[{"match": "x", "value": "y"}])
    assert cl.cases[0].value == "y"


def test_mapitem_requires_exactly_one_source():
    with pytest.raises(ValidationError, match="exactly one of"):
        MapItem(to="col")


def test_mapitem_rejects_multiple_sources():
    with pytest.raises(ValidationError, match="exactly one of"):
        MapItem(to="col", **{"from": "a"}, const="b")


def test_mapitem_from_alias_accepted():
    m = MapItem(to="col", **{"from": "source_col"})
    assert m.from_ == "source_col"


def test_mapitem_blank_from_treated_as_unset():
    # The editor writes `from: ""` (not omitted) for a row still mid-edit. That must
    # surface as the normal "needs exactly one of" validation error, not silently pass
    # through as a real (empty) column reference that then breaks SQL generation.
    with pytest.raises(ValidationError, match="exactly one of"):
        MapItem(to="origin", **{"from": ""})


def test_mapitem_blank_from_does_not_shadow_func():
    m = MapItem(to="origin", **{"from": "", "func": "mgrs"})
    assert m.from_ is None
    assert m.func == "mgrs"


def test_mapitem_blank_expr_treated_as_unset():
    with pytest.raises(ValidationError, match="exactly one of"):
        MapItem(to="origin", expr="")


def test_codecase_is_blank_alone_is_valid():
    c = CodeCase(is_blank=True, value="Unknown")
    assert c.is_blank is True
    assert c.match is None


def test_codecase_rejects_is_blank_with_other_pattern():
    with pytest.raises(ValidationError, match="exactly one of"):
        CodeCase(is_blank=True, match="x", value="Unknown")


def test_codecase_rejects_no_pattern():
    with pytest.raises(ValidationError, match="exactly one of"):
        CodeCase(value="Unknown")
