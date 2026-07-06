"""Scalar UDFs registered into DuckDB so the engine can stay mostly-SQL.

MGRS conversion isn't available in DuckDB's spatial extension, so we register a
small Python UDF backed by the `mgrs` library. If that library isn't installed,
the UDF returns NULL rather than failing the whole run.
"""
from __future__ import annotations

import threading
import warnings

import duckdb

# DuckDB's spatial extension has known native-crash issues on Windows when
# loaded/used concurrently from multiple threads (see duckdb-spatial#289,
# duckdb#13208, duckdb#17971). Serialize all engine work through this lock so
# only one thread ever touches the extension at a time.
DUCKDB_LOCK = threading.Lock()

try:
    import mgrs as _mgrs_lib  # type: ignore

    _converter = _mgrs_lib.MGRS()
    _HAVE_MGRS = True
except Exception:  # pragma: no cover - depends on optional dep
    _converter = None
    _HAVE_MGRS = False


_PRECISION = 5  # 5 = 1 m MGRS precision


def _to_mgrs(lon: float | None, lat: float | None) -> str | None:
    if lon is None or lat is None or _converter is None:
        return None
    try:
        return _converter.toMGRS(lat, lon, MGRSPrecision=_PRECISION)
    except Exception:
        return None


def register_udfs(con) -> None:
    """Register UDFs on a DuckDB connection (idempotent)."""
    if not _HAVE_MGRS:
        warnings.warn(
            "python 'mgrs' package not installed; func:mgrs will yield NULL. "
            "Install with: pip install mgrs"
        )
    try:
        con.create_function(
            "to_mgrs", _to_mgrs, ["DOUBLE", "DOUBLE"], "VARCHAR",
            null_handling="special",
        )
    except (duckdb.CatalogException, duckdb.NotImplementedException):
        pass  # already registered on this connection
