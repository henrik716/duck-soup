"""Scalar UDFs registered into DuckDB so the engine can stay mostly-SQL.

MGRS and geohash conversion aren't available in DuckDB's spatial extension, so we
register small Python UDFs: `to_mgrs` backed by the `mgrs` library, `to_geohash`
self-contained.
"""
from __future__ import annotations

import threading
import weakref

import duckdb
import mgrs as _mgrs_lib

# DuckDB's spatial extension has known native-crash issues on Windows when
# loaded/used concurrently from multiple threads (see duckdb-spatial#289,
# duckdb#13208, duckdb#17971). Serialize all engine work through this lock so
# only one thread ever touches the extension at a time.
DUCKDB_LOCK = threading.Lock()

# Every connection set up by load_extensions, so a query in progress can be stopped from
# another thread (interrupt_all, used by worker.py) without the engine handing its
# connections around. Weak: a closed and dropped connection leaves by itself.
_CONNECTIONS: weakref.WeakSet = weakref.WeakSet()

_converter = _mgrs_lib.MGRS()
_PRECISION = 5  # 5 = 1 m MGRS precision


def _to_mgrs(lon: float | None, lat: float | None) -> str | None:
    if lon is None or lat is None:
        return None
    try:
        return _converter.toMGRS(lat, lon, MGRSPrecision=_PRECISION)
    except Exception:
        return None


_GEOHASH_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"
_GEOHASH_LENGTH = 12  # ~4 cm cells


def _to_geohash(lon: float | None, lat: float | None) -> str | None:
    if lon is None or lat is None or not (-180 <= lon <= 180 and -90 <= lat <= 90):
        return None
    lon_lo, lon_hi, lat_lo, lat_hi = -180.0, 180.0, -90.0, 90.0
    chars, bits, value, even = [], 0, 0, True  # bits interleave lon, lat, lon, …
    while len(chars) < _GEOHASH_LENGTH:
        if even:
            mid = (lon_lo + lon_hi) / 2
            value = value * 2 + (lon >= mid)
            lon_lo, lon_hi = (mid, lon_hi) if lon >= mid else (lon_lo, mid)
        else:
            mid = (lat_lo + lat_hi) / 2
            value = value * 2 + (lat >= mid)
            lat_lo, lat_hi = (mid, lat_hi) if lat >= mid else (lat_lo, mid)
        even, bits = not even, bits + 1
        if bits == 5:
            chars.append(_GEOHASH_BASE32[value])
            bits, value = 0, 0
    return "".join(chars)


def register_udfs(con) -> None:
    """Register UDFs on a DuckDB connection (idempotent)."""
    try:
        con.create_function(
            "to_mgrs", _to_mgrs, ["DOUBLE", "DOUBLE"], "VARCHAR",
            null_handling="special",
        )
        con.create_function(
            "to_geohash", _to_geohash, ["DOUBLE", "DOUBLE"], "VARCHAR",
            null_handling="special",
        )
    except (duckdb.CatalogException, duckdb.NotImplementedException):
        pass  # already registered on this connection


def load_extensions(con) -> None:
    """Load the DuckDB extensions duck_soup reads through.

    `INSTALL spatial` fetches the extension build matching this connection's DuckDB
    core version, so the spatial extension version is pinned indirectly via the
    `duckdb` dependency pin in pyproject.toml (see the tested version noted there and
    in README.md) — bump both together when upgrading.
    """
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute("INSTALL postgres; LOAD postgres;")
    _CONNECTIONS.add(con)


def interrupt_all() -> None:
    """Interrupt the query running on every connection: it fails with an InterruptException,
    and the connection stays usable."""
    for con in list(_CONNECTIONS):
        try:
            con.interrupt()
        except Exception:
            pass  # closed meanwhile


def init_duckdb(con) -> None:
    """Full setup for a connection that will evaluate mapping expressions."""
    load_extensions(con)
    register_udfs(con)
