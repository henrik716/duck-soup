"""CRS strings: normalizing what GDAL/PROJ report, and a sanity check of a source's CRS
against its coordinates."""
from __future__ import annotations

import math

import duckdb

from ..sql_util import quote_literal
# config.py's CRSStr format (AUTHORITY:NUMERIC_CODE, e.g. "EPSG:4326"): a CRS string this
# module hands back must pass that validation, never surfaced as a raw pydantic error in the
# editor.
from ..config import _CRS_RE

# Aliases GDAL/PROJ use for WGS84-lon/lat that aren't the AUTHORITY:NUMERIC_CODE shape
# config.py's CRS field requires: "OGC:CRS84" is GeoParquet/GeoJSON's default per-spec CRS
# (see parquet_geometry_info), "CRS:84" is the older OGC WMS 1.3 CRS-namespace spelling of
# the same thing, sometimes reported by GDAL's ST_Read_Meta for other formats (see
# list_gdal_layers). Every transform in this codebase runs with always_xy := true, so the
# axis-order distinction these aliases formally encode never matters here — normalizing to
# EPSG:4326 is safe and keeps the editor's auto-detected CRS field showing a value it (and
# the user) recognizes, rather than a technically-equivalent but unfamiliar alias.
_CRS84_ALIASES = {"OGC:CRS84", "CRS:84"}


def _normalize_crs(crs: str | None) -> str | None:
    """A GDAL/PROJ-reported CRS string, normalized to config.py's AUTHORITY:NUMERIC_CODE
    shape: CRS84 aliases become "EPSG:4326", anything else non-conforming is dropped."""
    if not crs:
        return None
    if crs.upper() in _CRS84_ALIASES:
        return "EPSG:4326"
    return crs if _CRS_RE.match(crs) else None


# Geographic CRSs (coordinates in degrees) common enough to expect as a working_crs — mostly
# because working_crs defaults to the base source's CRS, and GeoJSON / ArcGIS REST / OGC API
# sources are lon/lat. crs_extent_warning uses this list as is (it has no connection);
# with one, probe_crs answers for any CRS PROJ knows.
GEOGRAPHIC_CRS = {
    "EPSG:4326",  # WGS 84
    "EPSG:4258",  # ETRS89
    "EPSG:4269",  # NAD83
    "EPSG:4267",  # NAD27
    "EPSG:4230",  # ED50
    "EPSG:4283",  # GDA94
    "EPSG:4167",  # NZGD2000
    "EPSG:4674",  # SIRGAS 2000
}


# probe_crs's answers, by CRS string: PROJ's database doesn't change while the process runs.
_PROBED: dict[str, bool | None] = {}


def probe_crs(con: duckdb.DuckDBPyConnection, crs: str) -> bool | None:
    """True if `crs` is geographic (degrees), False if projected, None if PROJ (DuckDB
    spatial's bundled database) doesn't know it.

    There's no "is this geographic?" function in DuckDB, so this measures one: a 0.001°
    step east of 10°E 45°N, reprojected into `crs`, is ~0.001 units long in a lon/lat CRS
    (0.0011 in grads) and ~80 in metres, ~260 in feet. A point PROJ can't reproject into
    `crs` (outside a projection's domain) counts as projected: any lon/lat is valid in a
    geographic CRS."""
    key = crs.upper()
    if key in GEOGRAPHIC_CRS:
        return True
    if key not in _PROBED:
        to = f"ST_Transform(ST_Point({{}}, 45), 'EPSG:4326', {quote_literal(crs)}, always_xy := true)"
        try:
            step = con.execute(f"SELECT ST_Distance({to.format(10)}, {to.format(10.001)})").fetchone()[0]
        except duckdb.Error as e:
            if "Could not create projection" in str(e):
                _PROBED[key] = None
                return None
            step = None
        _PROBED[key] = step is not None and math.isfinite(step) and step < 0.01
    return _PROBED[key]


# Loose bounds for "this looks like WGS84 lon/lat degrees" — a bit past the true [-180,180]
# x [-90,90] range to tolerate antimeridian-crossing extents and floating-point slop.
_LONLAT_BOUND_X = 180.5
_LONLAT_BOUND_Y = 90.5


def crs_extent_warning(
    crs: str | None,
    xmin: float | None,
    xmax: float | None,
    ymin: float | None,
    ymax: float | None,
) -> str | None:
    """A human-readable nudge if a source's declared `crs` looks inconsistent with the
    coordinate magnitudes actually sampled from it, or None if there's nothing to flag.

    This is deliberately a magnitude heuristic, not real CRS validation — there's no CRS
    authority database in this codebase to check "is EPSG:25833 really a projected CRS"
    against (see _normalize_crs), so "geographic" means one of the common lon/lat CRSs in
    GEOGRAPHIC_CRS (EPSG:4326, ETRS89 EPSG:4258, NAD83, ...). The mismatch it catches is the
    single most common real-world CRS mistake: a source labeled with a geographic CRS whose
    values are clearly meters (UTM/State Plane/etc.), or the reverse — a source labeled with
    some other CRS whose values are clearly lon/lat degrees. A real EPSG:4326 dataset with
    an oddly tiny extent, or a real projected dataset that happens to have small-magnitude
    coordinates (rare, but not impossible near a projection's false origin), can still slip
    past or trip this — it's a hint to double-check, not a hard error.
    """
    if xmin is None or xmax is None or ymin is None or ymax is None or not crs:
        return None
    looks_lonlat = (
        -_LONLAT_BOUND_X <= xmin <= _LONLAT_BOUND_X
        and -_LONLAT_BOUND_X <= xmax <= _LONLAT_BOUND_X
        and -_LONLAT_BOUND_Y <= ymin <= _LONLAT_BOUND_Y
        and -_LONLAT_BOUND_Y <= ymax <= _LONLAT_BOUND_Y
    )
    geographic = crs.upper() in GEOGRAPHIC_CRS
    extent = f"x:[{xmin:.1f}, {xmax:.1f}] y:[{ymin:.1f}, {ymax:.1f}]"
    if geographic and not looks_lonlat:
        return (
            f"Coordinates range over {extent} — too large to be lon/lat degrees, but this "
            f"source's CRS is set to {crs}. It's probably a projected CRS instead "
            "(e.g. a UTM zone) — check the source data's real CRS."
        )
    if not geographic and looks_lonlat:
        return (
            f"Coordinates range over {extent} — that looks like lon/lat degrees, but this "
            f"source's CRS is set to {crs}. Did you mean EPSG:4326?"
        )
    return None
