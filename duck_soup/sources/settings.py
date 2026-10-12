"""Where sources keeps its caches, and the size thresholds that pick a reader.

Module attributes, read at call time as `settings.X` (never imported by name), so a test
or the editor's server can change one in a single place: worker.py hands the server's
values to its worker processes (see worker._SHARED_SETTINGS)."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# Linearizing a curve layer (see _linearize_curves) round-trips the whole layer
# through GDAL — cheap for a one-off run, but the web editor re-previews on every
# edit and every map pan (bbox mode), so redoing it from scratch each time is the
# difference between an instant preview and a multi-hundred-ms-per-request one.
# Cached here (outside the per-call `workdir`, which is deleted at the end of
# every single preview/run) and keyed by the source file's mtime+size, so an
# edited source file invalidates its own cache entry automatically.
LINEARIZE_CACHE_DIR = Path(tempfile.gettempdir()) / "duck_soup_linearize_cache"


# Large csv/xlsx files are read from a cached Parquet copy — see `_tabular_parquet_cache`.
# Below this size GDAL opens them fast enough that the copy isn't worth it.
TABULAR_CACHE_DIR = Path(tempfile.gettempdir()) / "duck_soup_tabular_cache"
TABULAR_CACHE_MIN_BYTES = 5 * 2**20


# On Windows, DuckDB's spatial extension (confirmed through duckdb 1.5.6) segfaults —
# taking the whole process down, web server included — once ST_Read gets past the 2 GiB
# mark of a file. Small reads (a LIMITed preview, a schema sample) finish before reaching
# that point, which is why such a file inspects fine and then crashes on a full scan (a
# join against it, or an "in view" preview of features stored late in the file). Files at
# or above this size are read through pyogrio instead (see `_read_via_pyogrio`). None
# disables the fallback; elsewhere ST_Read handles large files fine.
ST_READ_MAX_BYTES: int | None = 2**31 if sys.platform == "win32" else None


# Rows read for a schema-inspection sample (read_expr's `sample=True`) on the pyogrio path —
# enough for /api/inspect's CRS sanity check, which looks at up to 500 features.
PYOGRIO_SAMPLE_ROWS = 500

# Remote file sources are downloaded here — see remote.py.
REMOTE_CACHE_DIR = Path(tempfile.gettempdir()) / "duck_soup_remote_cache"
