"""Read-only SQLite execution with a wall-clock timeout and a row cap."""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class QueryResult:
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    truncated: bool = False
    error: str | None = None
    elapsed_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None


class Executor:
    def __init__(self, db_path: str | Path, timeout_s: float = 10.0, max_rows: int = 1000):
        self.db_path = Path(db_path).resolve()
        if not self.db_path.exists():
            raise FileNotFoundError(self.db_path)
        self.timeout_s = timeout_s
        self.max_rows = max_rows

    def connect(self) -> sqlite3.Connection:
        # mode=ro: the connection physically cannot write, whatever the SQL says.
        # as_uri() gives file:///D:/... on Windows; a raw backslash path breaks SQLite URIs.
        conn = sqlite3.connect(f"{self.db_path.as_uri()}?mode=ro", uri=True, check_same_thread=False)
        conn.execute("PRAGMA query_only = ON")
        return conn

    def run(self, sql: str, max_rows: int | None = None, params: dict | None = None) -> QueryResult:
        cap = self.max_rows if max_rows is None else max_rows
        start = time.perf_counter()
        deadline = start + self.timeout_s
        conn = self.connect()
        # Returning non-zero from the progress handler aborts the running statement.
        conn.set_progress_handler(lambda: int(time.perf_counter() > deadline), 10_000)
        try:
            cur = conn.execute(sql, params or {})
            columns = [d[0] for d in cur.description or []]
            rows = cur.fetchmany(cap + 1)
            truncated = len(rows) > cap
            return QueryResult(columns, [tuple(r) for r in rows[:cap]], truncated,
                               elapsed_s=time.perf_counter() - start)
        except sqlite3.OperationalError as e:
            msg = str(e)
            if msg == "interrupted":
                msg = f"Query exceeded the {self.timeout_s:.0f}s timeout."
            return QueryResult(error=msg, elapsed_s=time.perf_counter() - start)
        except sqlite3.Error as e:
            return QueryResult(error=f"{type(e).__name__}: {e}", elapsed_s=time.perf_counter() - start)
        finally:
            conn.close()
