"""The installed version, and whether PyPI has a newer one.

The update check is best effort: one small HTTP request to PyPI's JSON API, cached for the
process (and its result, ok or not, for `_CACHE_SECONDS`), with a short timeout. Any failure
(offline, firewalled, PyPI down) just means "no update known". Set
`DUCK_SOUP_NO_UPDATE_CHECK=1` to never contact PyPI.
"""
from __future__ import annotations

import os
import threading
import time
from importlib.metadata import PackageNotFoundError, version as _dist_version
from pathlib import Path

DIST_NAME = "duck-soup-etl"
PYPI_URL = f"https://pypi.org/pypi/{DIST_NAME}/json"
_CACHE_SECONDS = 6 * 3600
_TIMEOUT = 4

def _installed_version() -> str:
    # A source checkout reads pyproject.toml: an editable install's metadata only records
    # the version it had when `pip install -e .` last ran.
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    try:
        import tomllib
        project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
        if project["name"] == DIST_NAME:
            return str(project["version"])
    except (OSError, KeyError, ValueError):
        pass
    try:
        return _dist_version(DIST_NAME)
    except PackageNotFoundError:
        return "0+unknown"


__version__ = _installed_version()

_lock = threading.Lock()
_cached: tuple[float, str | None] | None = None


def _parse(v: str) -> tuple[int, ...] | None:
    """`1.2.3` → (1, 2, 3); None for anything that isn't plain dotted numbers (pre-releases,
    local builds), which then never counts as newer or older."""
    try:
        return tuple(int(p) for p in v.split("."))
    except ValueError:
        return None


def is_newer(latest: str | None, current: str | None = None) -> bool:
    a, b = _parse(latest or ""), _parse(current or __version__)
    return a is not None and b is not None and a > b


def update_check_disabled() -> bool:
    return os.environ.get("DUCK_SOUP_NO_UPDATE_CHECK", "").strip().lower() not in ("", "0", "false", "no")


def latest_version(force: bool = False) -> str | None:
    """The newest release on PyPI, or None when unknown (check disabled, or it failed)."""
    global _cached
    if update_check_disabled():
        return None
    with _lock:
        if not force and _cached and time.monotonic() - _cached[0] < _CACHE_SECONDS:
            return _cached[1]
        latest: str | None = None
        try:
            import requests
            r = requests.get(PYPI_URL, timeout=_TIMEOUT)
            r.raise_for_status()
            latest = str(r.json()["info"]["version"])
        except Exception:
            latest = None
        _cached = (time.monotonic(), latest)
        return latest


def in_docker() -> bool:
    return Path("/.dockerenv").exists()


def upgrade_commands() -> list[str]:
    """How to upgrade, the most likely install method first (README recommends uv)."""
    if in_docker():
        return ["docker pull ghcr.io/henrik716/duck-soup"]
    return [f"uv tool upgrade {DIST_NAME}", f"pipx upgrade {DIST_NAME}", f"pip install -U {DIST_NAME}"]


def upgrade_hint() -> str:
    first, *rest = upgrade_commands()
    return f"{first}  (or: {' / '.join(rest)})" if rest else first


def version_info() -> dict:
    latest = latest_version()
    return {
        "version": __version__,
        "latest": latest,
        "update_available": is_newer(latest),
        "upgrade": upgrade_commands(),
        "release_notes": "https://github.com/henrik716/duck-soup/releases",
    }
