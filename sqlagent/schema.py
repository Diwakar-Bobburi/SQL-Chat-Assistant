"""Schema introspection, prompt rendering and schema linking.

Schema linking picks only the tables a question needs (plus the join tables that
connect them). On a 7-table demo DB it mostly saves tokens; on a 100-table
warehouse it is what keeps the prompt usable.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Column:
    name: str
    type: str
    pk: bool = False


@dataclass
class ForeignKey:
    column: str
    ref_table: str
    ref_column: str


@dataclass
class Table:
    name: str
    columns: list[Column]
    fks: list[ForeignKey] = field(default_factory=list)
    samples: list[tuple] = field(default_factory=list)
    row_count: int = 0


@dataclass
class GlossaryTerm:
    term: str
    definition: str
    tables: list[str] = field(default_factory=list)


def load_glossary(path: str | Path | None) -> list[GlossaryTerm]:
    if not path or not Path(path).exists():
        return []
    return [GlossaryTerm(**t) for t in json.loads(Path(path).read_text(encoding="utf-8"))]


def introspect(conn: sqlite3.Connection, sample_rows: int = 3) -> dict[str, Table]:
    tables: dict[str, Table] = {}
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    for t in names:
        cols = [Column(r[1], r[2] or "TEXT", bool(r[5])) for r in conn.execute(f'PRAGMA table_info("{t}")')]
        fks = [ForeignKey(r[3], r[2], r[4]) for r in conn.execute(f'PRAGMA foreign_key_list("{t}")')]
        samples = [tuple(r) for r in conn.execute(f'SELECT * FROM "{t}" LIMIT {sample_rows}')] if sample_rows else []
        count = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        tables[t] = Table(t, cols, fks, samples, count)
    return tables


def render(tables: dict[str, Table], include_samples: bool = True) -> str:
    """Render tables as CREATE TABLE statements (the format LLMs read best), with sample rows."""
    out = []
    for t in tables.values():
        lines = [f"  {c.name} {c.type}{' PRIMARY KEY' if c.pk else ''}" for c in t.columns]
        lines += [f"  FOREIGN KEY ({fk.column}) REFERENCES {fk.ref_table}({fk.ref_column})" for fk in t.fks]
        block = f"CREATE TABLE {t.name} (\n" + ",\n".join(lines) + "\n);"
        if include_samples and t.samples:
            header = ", ".join(c.name for c in t.columns)
            rows = "\n".join("  " + ", ".join(repr(v) for v in r) for r in t.samples)
            block += f"\n/* {t.row_count} rows. Sample:\n  {header}\n{rows}\n*/"
        out.append(block)
    return "\n\n".join(out)


def render_glossary(terms: list[GlossaryTerm]) -> str:
    return "\n".join(f"- {t.term}: {t.definition}" for t in terms)


# ---------------------------------------------------------------- schema linking

_STOP = set("a an the of in on for to by with and or is are was were be what which who how many much "
            "list show give find each per all any that this from their its as at than top most "
            "me do does did has have had".split())


def _norm(word: str) -> str:
    w = word.lower()
    for suf in ("ies", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)] + ("y" if suf == "ies" else "")
    return w


def _tokens(text: str) -> set[str]:
    parts = re.findall(r"[A-Za-z]+", text.replace("_", " "))
    return {_norm(p) for p in parts if p.lower() not in _STOP and len(p) > 1}


def _fk_graph(tables: dict[str, Table]) -> dict[str, set[str]]:
    g: dict[str, set[str]] = {t: set() for t in tables}
    for t in tables.values():
        for fk in t.fks:
            if fk.ref_table in g:
                g[t.name].add(fk.ref_table)
                g[fk.ref_table].add(t.name)
    return g


def _path(g: dict[str, set[str]], a: str, b: str) -> list[str]:
    prev, q = {a: None}, deque([a])
    while q:
        n = q.popleft()
        if n == b:
            path = []
            while n is not None:
                path.append(n)
                n = prev[n]
            return path
        for m in g[n]:
            if m not in prev:
                prev[m] = n
                q.append(m)
    return []


def link_tables(question: str, tables: dict[str, Table], glossary: list[GlossaryTerm] = (),
                max_tables: int = 6) -> list[str]:
    """Score tables by lexical overlap with the question, then add FK bridge tables."""
    q = _tokens(question)
    scores: dict[str, float] = {t: 0.0 for t in tables}
    for t in tables.values():
        if _tokens(t.name) & q:
            scores[t.name] += 3
        for c in t.columns:
            if not c.pk and _tokens(c.name) & q:
                scores[t.name] += 1
        for row in t.samples:  # value matching, e.g. "Electronics" -> categories
            for v in row:
                if isinstance(v, str) and len(v) > 2 and _norm(v) in q:
                    scores[t.name] += 2
    for term in glossary:
        if _tokens(term.term) <= q or term.term.lower() in question.lower():
            for t in term.tables:
                if t in scores:
                    scores[t] += 3

    picked = [t for t, s in sorted(scores.items(), key=lambda kv: -kv[1]) if s > 0][:max_tables]
    if not picked:
        return list(tables)

    g = _fk_graph(tables)
    linked = set(picked)
    for i, a in enumerate(picked):
        for b in picked[i + 1:]:
            linked.update(_path(g, a, b))
    return [t for t in tables if t in linked]  # keep stable order
