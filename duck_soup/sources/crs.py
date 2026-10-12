"""CRS strings: normalizing what GDAL/PROJ report, and a sanity check of a source's CRS
against its coordinates."""
from __future__ import annotations

import re


# Matches config.py's CRSStr format (AUTHORITY:NUMERIC_CODE, e.g. "EPSG:4326") — mirrored
# here (rather than importing config._CRS_RE) so a CRS string this module hands back is
# guaranteed to pass that validation, never surfaced as a raw pydantic error in the editor.
_CRS_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*:[0-9]+$")

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
# sources are lon/lat. There's no CRS database in this codebase to ask "is this geographic?"
# in general, so this is a list, not a lookup. Used by engine.py (working_crs) and
# crs_extent_warning.
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
