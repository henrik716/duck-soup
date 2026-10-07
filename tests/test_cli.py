"""`python -m duck_soup.cli check|run` against the fixture pipelines."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from duck_soup import cli

REPO_ROOT = Path(__file__).resolve().parents[1]
PIPELINES_DIR = REPO_ROOT / "pipelines"


@pytest.fixture(autouse=True)
def _repo_root_cwd(monkeypatch):
    monkeypatch.chdir(REPO_ROOT)


def test_check_valid_pipeline_prints_ok(capsys):
    rc = cli.main(["check", str(PIPELINES_DIR / "test.yaml")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "OK:" in out
    assert "'test'" in out


def test_check_invalid_pipeline_raises():
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
    bad_path = Path.cwd() / "bad_pipeline.yaml"
    bad_path.write_text(yaml.safe_dump(bad), encoding="utf-8")
    try:
        with pytest.raises(ValidationError):
            cli.main(["check", str(bad_path)])
    finally:
        bad_path.unlink()


def test_run_writes_output_file(tmp_path):
    cfg_dict = yaml.safe_load((PIPELINES_DIR / "test.yaml").read_text(encoding="utf-8"))
    cfg_dict["output"] = str(tmp_path / "cli_run.gpkg")
    tmp_yaml = tmp_path / "pipeline.yaml"
    tmp_yaml.write_text(yaml.safe_dump(cfg_dict), encoding="utf-8")

    rc = cli.main(["run", str(tmp_yaml)])

    assert rc == 0
    assert (tmp_path / "cli_run.gpkg").exists()


def test_tutorial_copies_data_and_config(tmp_path, capsys):
    rc = cli.main(["tutorial", str(tmp_path)])

    assert rc == 0
    assert (tmp_path / "pipelines" / "pondsworth.yaml").is_file()
    data = {p.name for p in (tmp_path / "data" / "tutorial").iterdir()}
    assert {"places.geojson", "districts.geojson", "categories.csv"} <= data
    assert "pondsworth.yaml" not in data
    assert "Pondsworth is ready" in capsys.readouterr().out
    # The copied config validates against the copied data's relative paths.
    assert cli.main(["check", str(tmp_path / "pipelines" / "pondsworth.yaml")]) == 0


def test_tutorial_refuses_to_overwrite_without_force(tmp_path, capsys):
    assert cli.main(["tutorial", str(tmp_path)]) == 0
    config = tmp_path / "pipelines" / "pondsworth.yaml"
    config.write_text("# my edits", encoding="utf-8")

    assert cli.main(["tutorial", str(tmp_path)]) == 1
    assert "already exists" in capsys.readouterr().err
    assert config.read_text(encoding="utf-8") == "# my edits"

    assert cli.main(["tutorial", str(tmp_path), "--force"]) == 0
    assert config.read_text(encoding="utf-8") != "# my edits"
