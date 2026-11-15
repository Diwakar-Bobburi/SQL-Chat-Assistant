"""Static safety check on generated SQL, before it ever reaches the database.

Defense in depth: the executor also opens SQLite read-only, so even if this check
were bypassed, writes would fail.
"""
from __future__ import annotations

import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

FORBIDDEN = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Drop, exp.Create, exp.Alter,
    exp.Pragma, exp.Command, exp.Transaction, exp.Attach, exp.Detach, exp.Set, exp.Use,
)


class GuardError(ValueError):
    pass


def extract_sql(text: str) -> str:
    """Pull the SQL out of a model reply (```sql fenced block preferred)."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)  # reasoning models (qwen3)
    blocks = re.findall(r"```(?:sql|sqlite)?\s*(.*?)```", text, flags=re.S | re.I)
    m = blocks[-1] if blocks else None
    sql = m if m is not None else text
    return sql.strip().rstrip(";").strip()


def validate(sql: str, dialect: str = "sqlite") -> str:
    """Return the SQL unchanged if it is a single read-only query, else raise GuardError."""
    if not sql:
        raise GuardError("Empty query.")
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except ParseError as e:
        raise GuardError(f"SQL does not parse: {str(e).splitlines()[0]}") from e
    if len(statements) != 1:
        raise GuardError(f"Expected exactly one statement, got {len(statements)}.")
    stmt = statements[0]
    if not isinstance(stmt, exp.Query):
        raise GuardError(f"Only SELECT queries are allowed (got {stmt.key.upper()}).")
    bad = next((n for n in stmt.walk() if isinstance(n, FORBIDDEN)), None)
    if bad is not None:
        raise GuardError(f"Forbidden operation inside query: {bad.key.upper()}.")
    return sql
