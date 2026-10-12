"""SQL string-quoting helpers shared by the engine and the source readers."""
from __future__ import annotations


def quote_ident(name: str) -> str:
    """Double-quote a SQL identifier."""
    return '"' + name.replace('"', '""') + '"'


def quote_literal(value: str) -> str:
    """Single-quote a value for inlining into SQL."""
    return "'" + value.replace("'", "''") + "'"


def st_transform(col: str, from_crs: str, to_crs: str) -> str:
    """`col` reprojected from `from_crs` to `to_crs` (itself when they're the same)."""
    if from_crs == to_crs:
        return col
    return f"ST_Transform({col}, {quote_literal(from_crs)}, {quote_literal(to_crs)}, always_xy := true)"
