"""SQL string-quoting helpers shared by the engine and the source readers."""
from __future__ import annotations


def quote_ident(name: str) -> str:
    """Double-quote a SQL identifier."""
    return '"' + name.replace('"', '""') + '"'


def quote_literal(value: str) -> str:
    """Single-quote a value for inlining into SQL."""
    return "'" + value.replace("'", "''") + "'"
