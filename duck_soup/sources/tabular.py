"""Tabular sources (xlsx, csv, plain json): header-row options, geometry built from
columns, and the cached Parquet copy of a large csv/xlsx."""
from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path
from typing import Callable

import duckdb

from ..config import Source
from ..sql_util import quote_ident as _sql_ident, quote_literal as _sql_str
from . import settings
from .formats import GDAL_TABULAR, alias_geom
from .gdal import _geometry_column_via_describe, _st_read
from .large_files import _local_file_size
from .remote import _clear_progress, _is_download, _set_progress, _superseded_long_enough


def _read_as_parquet(src: Source, local: str) -> bool:
    return (
        src.format in GDAL_TABULAR
        and _local_file_size(local) >= settings.TABULAR_CACHE_MIN_BYTES
        and not Path(local).is_dir()
    )


def _open_options_arg(opts: list[str]) -> str:
    """`open_options=['...', ...]` fragment for `_st_read`'s `extra` param, or "" if `opts` is empty."""
    if not opts:
        return ""
    return f"open_options=[{', '.join(_sql_str(o) for o in opts)}]"


def _header_open_option(fmt: str, header_row: bool) -> str:
    """GDAL open option forcing header-row detection on/off for xlsx/csv (Source.header_row).

    Left at GDAL's own AUTO default (Source.header_row = None) whenever it's not set — AUTO
    guesses from the first row's apparent field types, which is wrong often enough to want an
    override: a header row that happens to look like the data below it, or an all-numeric
    first data row (e.g. postal codes) mistaken for headers.
    """
    if fmt == "xlsx":
        return "HEADERS=FORCE" if header_row else "HEADERS=DISABLE"
    return "HEADERS=YES" if header_row else "HEADERS=NO"


def _apply_tabular_geometry(
    base: str, src: Source, column_types: dict[str, str] | None = None
) -> str:
    """Wrap a plain tabular read (xlsx/csv/json, no native geometry) with a computed `geom`
    column, for a source naming x/y or geometry columns via Source.x_field/y_field/geom_field.

    This is deliberately *not* done via GDAL open options (X_POSSIBLE_NAMES/Y_POSSIBLE_NAMES/
    GEOM_POSSIBLE_NAMES): those only exist on the CSV driver — XLSX's driver has no
    geometry support at all, open options or otherwise — and even for csv they came with
    real sharp edges: ST_Read_Meta has no `open_options` parameter, so it's blind to a field
    that only exists because of them (needing a separate DESCRIBE-based detection path), and
    GDAL's KEEP_GEOM_COLUMNS default duplicates the matched column under the same name as
    the geometry field it derives, which DuckDB then refuses to `SELECT *` at all
    ("duplicate column name") — confirmed empirically. Building the geometry directly in SQL
    sidesteps all of that and works identically for every tabular format.

    `column_types` (column name → DuckDB type, from a DESCRIBE of `base`) matters for json:
    read_json() types a field holding mixed values (`10.7` in one record, `"5.3"` in the
    next) as JSON, and a nested GeoJSON geometry object as a STRUCT or JSON — neither of
    which the plain text/number handling below can take.
    """
    types = {k: v.upper() for k, v in (column_types or {}).items()}

    def _number(col: str) -> str:
        c = _sql_ident(col)
        if types.get(col) == "JSON":
            c = f"json_extract_string({c}, '$')"
        return f"TRY_CAST({c} AS DOUBLE)"

    if src.x_field and src.y_field:
        geom = f"ST_Point({_number(src.x_field)}, {_number(src.y_field)})"
        consumed = [src.x_field, src.y_field]
    elif src.geom_field:
        g = _sql_ident(src.geom_field)
        gtype = types.get(src.geom_field, "VARCHAR")
        if gtype == "JSON" or gtype.startswith(("STRUCT", "MAP")):
            # A GeoJSON geometry object nested in the record (json sources).
            geom = f"CASE WHEN {g} IS NULL THEN NULL ELSE TRY(ST_GeomFromGeoJSON(to_json({g}))) END"
        else:
            # Hex WKB (e.g. PostGIS's extended WKB), GeoJSON text and WKT look nothing alike,
            # so branch on the text: pure hex digits → WKB, a leading `{` → GeoJSON, else WKT
            # — the same distinctions GDAL's own GEOM_POSSIBLE_NAMES auto-detection makes.
            # Each parse is wrapped in TRY: a malformed value gives the row a NULL geometry
            # instead of failing the whole read, like a non-numeric x/y does above.
            geom = (
                f"CASE WHEN {g} IS NULL OR trim({g}) = '' THEN NULL "
                f"WHEN regexp_matches({g}, '^[0-9A-Fa-f]+$') THEN TRY(ST_GeomFromHEXWKB({g})) "
                f"WHEN starts_with(ltrim({g}), '{{') THEN TRY(ST_GeomFromGeoJSON({g})) "
                f"ELSE TRY(CAST({g} AS GEOMETRY)) END"
            )
        consumed = [src.geom_field]
    else:
        return base
    # The source columns are consumed into geom, and so is any `geom` the read already has:
    # GDAL's CSV driver turns a column named WKT (any case) into a geometry field of its own,
    # named geom, which would otherwise collide with this one. COLUMNS(lambda) rather than
    # EXCLUDE, since that `geom` may not exist; the inner __geom keeps the filtered column
    # list from coming out empty (a file holding nothing but its geometry column).
    drop = ", ".join(_sql_str(c.lower()) for c in [*consumed, "geom"])
    return (
        f"(SELECT * EXCLUDE (__geom), __geom AS geom FROM ("
        f"SELECT COLUMNS(lambda c: lower(c) NOT IN ({drop})) FROM ("
        f"SELECT *, {geom} AS __geom FROM {base})))"
    )


def _tabular_open_extra(src: Source) -> str:
    """The ST_Read open-options clause for a csv/xlsx source (just header_row today)."""
    if src.format in GDAL_TABULAR and src.header_row is not None:
        return _open_options_arg([_header_open_option(src.format, src.header_row)])
    return ""


def _tabular_cache_path(uri: str, layer: str | None, extra: str) -> tuple[Path, str] | None:
    """(Parquet copy path, per-file key) for a csv/xlsx file big enough to be read via a
    copy (see _tabular_parquet_cache), else None. Doesn't create anything."""
    if _local_file_size(uri) < settings.TABULAR_CACHE_MIN_BYTES or Path(uri).is_dir():
        return None
    p = Path(uri).resolve()
    st = p.stat()
    # A download (fetch_remote_file) sits at <url hash>/<download time>/<name>: key on the
    # url hash so a re-download replaces that URL's copy instead of orphaning it.
    try:
        key_path = p.parent.parent if p.is_relative_to(settings.REMOTE_CACHE_DIR.resolve()) else p
    except OSError:
        key_path = p
    path_key = hashlib.sha1(str(key_path).encode()).hexdigest()[:16]
    # A csv has exactly one layer, so picking it (empty → "segmentert") changes nothing read
    # and mustn't mean a new conversion. xlsx sheets do differ.
    if p.suffix.lower() == ".csv":
        layer = None
    version = hashlib.sha1(
        f"{st.st_mtime_ns}|{st.st_size}|{layer}|{extra}".encode()
    ).hexdigest()[:12]
    return settings.TABULAR_CACHE_DIR / f"{path_key}_{version}.parquet", path_key


def _tabular_parquet_cache(
    con: duckdb.DuckDBPyConnection,
    uri: str,
    layer: str | None,
    extra: str,
    log: Callable[[str], None] | None,
    progress_key: str | None = None,
) -> str | None:
    """read_parquet() over a cached Parquet copy of a large local csv/xlsx file, or None to
    read it with ST_Read as usual.

    GDAL's CSV driver scans the whole file every time it opens one — a 44 MB csv costs ~8.5s
    per open, for a LIMIT 50 or a DESCRIBE just the same — and the editor opens a source many
    times per edit (inspect, preview, counts, plus the ST_Read_Meta inside
    _read_expr_normalized). Converting once to Parquet (same columns and types, it's just
    ST_Read's output) makes every later read near-instant. Keyed by path + mtime + size +
    layer + open options, so an edited file or a changed header_row gets a fresh copy; a
    file's older copies are deleted when a new one is written."""
    target = _tabular_cache_path(uri, layer, extra)
    if target is None:
        return None
    out, path_key = target
    st = Path(uri).stat()
    if not out.exists():
        settings.TABULAR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if log:
            log(f"Caching {Path(uri).name} as Parquet for faster reads")
        tmp = out.with_name(f"{out.stem}.{threading.get_ident()}.part")
        # Raw ST_Read, not _read_expr_normalized: its ST_Read_Meta lookup is one more full
        # GDAL scan of the file. The geometry column is found from the Parquet copy below.
        read = _st_read(uri, layer=layer, extra=extra)
        start = time.monotonic()
        _set_progress(progress_key, "convert", bytes=st.st_size)
        try:
            con.execute(f"COPY (SELECT * FROM {read}) TO {_sql_str(str(tmp))} (FORMAT PARQUET)")
        finally:
            _clear_progress(progress_key, "convert")
        os.replace(tmp, out)
        if log:
            log(f"Converted to Parquet in {time.monotonic() - start:.1f}s")
        # A local file's older copies are simply superseded (every process derives the same
        # new version from the file itself), but a download's copies can be another process's
        # current one: only remove those once stale for everyone.
        remote = _is_download(Path(uri))
        for old in settings.TABULAR_CACHE_DIR.glob(f"{path_key}_*.parquet"):
            if old == out or (remote and not _superseded_long_enough(old)):
                continue
            try:  # still open elsewhere (Windows) → left for the next rewrite to remove
                old.unlink()
            except OSError:
                pass
    base = f"read_parquet({_sql_str(str(out).replace(chr(92), '/'))})"
    name = _geometry_column_via_describe(con, base)
    return alias_geom(base, name)


def _json_read_expr(path: str, records: str | None) -> str:
    """read_json() over a plain JSON file: an array of objects, newline-delimited objects,
    or (with `records`, a dot path like `data.items`) one document holding that array.

    `sample_size = -1` types each column from the whole file rather than the first ~20k
    records, so a field that only turns mixed-type further down can't fail the read halfway.
    `maximum_object_size` lifts the 16 MB per-object cap, which a `records` document (the
    whole file is one object) passes easily."""
    base = f"read_json({_sql_str(path)}, sample_size = -1, maximum_object_size = 2147483647)"
    if not records:
        return base
    path_expr = ".".join(_sql_ident(part) for part in records.split("."))
    # Inner unnest: the list into one row per record; outer: the record's fields into columns.
    return (
        f"(SELECT unnest(__rec, max_depth := 1) FROM "
        f"(SELECT unnest({path_expr}) AS __rec FROM {base}))"
    )
