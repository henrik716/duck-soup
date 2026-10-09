"""The version badge / update check (duck_soup/version.py, GET /api/version)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from duck_soup import cli, version
from duck_soup.web.app import app


@pytest.mark.parametrize("latest, current, newer", [
    ("1.3.0", "1.2.2", True),
    ("1.2.10", "1.2.9", True),
    ("1.2.2", "1.2.2", False),
    ("1.2.1", "1.2.2", False),
    (None, "1.2.2", False),
    ("1.3.0rc1", "1.2.2", False),
    ("1.3.0", "0+unknown", False),
])
def test_is_newer(latest, current, newer):
    assert version.is_newer(latest, current) is newer


def test_disabled_check_never_contacts_pypi(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("contacted PyPI")
    monkeypatch.setattr("requests.get", boom)
    assert version.latest_version(force=True) is None


def test_failed_check_means_no_update(monkeypatch):
    monkeypatch.delenv("DUCK_SOUP_NO_UPDATE_CHECK")
    def offline(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr("requests.get", offline)
    assert version.latest_version(force=True) is None


def test_version_endpoint_reports_update(monkeypatch):
    monkeypatch.setattr(version, "latest_version", lambda force=False: "999.0.0")
    body = TestClient(app).get("/api/version").json()
    assert body["version"] == version.__version__
    assert body["latest"] == "999.0.0"
    assert body["update_available"] is True
    assert body["upgrade"]


def test_cli_version_flag(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert version.__version__ in capsys.readouterr().out
