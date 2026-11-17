"""Dashboards: a JSON list of SQL tiles, each drawn with an automatic (or fixed) chart.

Schema:
{
  "title": "...", "description": "...",
  "filters": {"year": {"label": "Year", "options_sql": "SELECT DISTINCT ..."}},   # optional
  "tiles": [{"title": "...", "sql": "...", "chart": null | {"kind": ...},
             "width": "full" | "half", "caption": "...", "compare_previous": false}]
}

Tile SQL may use the named parameter :year (NULL = all years). Every tile goes through
the same guard as model-written SQL, so an imported dashboard can't run writes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .executor import Executor
from .guard import GuardError, validate
from .viz import ChartSpec, suggest_chart

MAX_TILES = 30


@dataclass
class Tile:
    title: str
    sql: str
    chart: ChartSpec | None = None      # None = choose automatically from the result
    width: str = "half"
    caption: str = ""
    compare_previous: bool = False      # KPI tiles: show change vs the previous year

    def to_dict(self) -> dict:
        d = {"title": self.title, "sql": self.sql, "chart": self.chart.to_dict() if self.chart else None,
             "width": self.width}
        if self.caption:
            d["caption"] = self.caption
        if self.compare_previous:
            d["compare_previous"] = True
        return d


@dataclass
class Dashboard:
    title: str
    tiles: list[Tile] = field(default_factory=list)
    description: str = ""
    filters: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({"title": self.title, "description": self.description, "filters": self.filters,
                           "tiles": [t.to_dict() for t in self.tiles]}, indent=2)


@dataclass
class TileResult:
    df: pd.DataFrame | None
    chart: ChartSpec | None
    error: str | None = None
    previous: pd.DataFrame | None = None


def parse_dashboard(data: dict | str) -> Dashboard:
    """Build a Dashboard from JSON, rejecting anything malformed or unsafe."""
    if isinstance(data, str):
        data = json.loads(data)
    if not isinstance(data, dict) or not isinstance(data.get("tiles"), list):
        raise ValueError("A dashboard needs a 'tiles' list.")
    if len(data["tiles"]) > MAX_TILES:
        raise ValueError(f"At most {MAX_TILES} tiles are allowed.")
    tiles = []
    for i, t in enumerate(data["tiles"], 1):
        if not isinstance(t, dict) or not t.get("sql"):
            raise ValueError(f"Tile {i} has no SQL.")
        try:
            validate(t["sql"])
        except GuardError as e:
            raise ValueError(f"Tile {i} ('{t.get('title', '')}') was rejected: {e}") from e
        chart = t.get("chart")
        if chart is not None and (not isinstance(chart, dict) or chart.get("kind") not in {"kpi", "line", "bar", "scatter"}):
            raise ValueError(f"Tile {i} has an unknown chart type.")
        tiles.append(Tile(title=str(t.get("title", f"Tile {i}")), sql=t["sql"], chart=ChartSpec.from_dict(chart),
                          width="full" if t.get("width") == "full" else "half", caption=str(t.get("caption", "")),
                          compare_previous=bool(t.get("compare_previous"))))
    filters = data.get("filters") or {}
    for f in filters.values():
        validate(f["options_sql"])
    return Dashboard(title=str(data.get("title", "Dashboard")), tiles=tiles,
                     description=str(data.get("description", "")), filters=filters)


def load_dashboard(path: str | Path) -> Dashboard:
    return parse_dashboard(Path(path).read_text(encoding="utf-8"))


def _df(executor: Executor, sql: str, params: dict):
    r = executor.run(sql, params=params)
    if not r.ok:
        return None, r.error
    return pd.DataFrame(r.rows, columns=r.columns), None


def run_tile(executor: Executor, tile: Tile, params: dict | None = None) -> TileResult:
    params = dict(params or {})
    try:
        validate(tile.sql)  # again: tiles can be edited after loading
    except GuardError as e:
        return TileResult(None, None, str(e))
    needed = {k: params.get(k) for k in ("year",) if f":{k}" in tile.sql}
    df, err = _df(executor, tile.sql, needed)
    if err:
        return TileResult(None, None, err)
    chart = tile.chart or suggest_chart(df)
    prev = None
    if tile.compare_previous and needed.get("year"):
        prev, _ = _df(executor, tile.sql, {**needed, "year": str(int(needed["year"]) - 1)})
    return TileResult(df, chart, None, prev)


def filter_options(executor: Executor, dashboard: Dashboard, name: str) -> list[str]:
    f = dashboard.filters.get(name)
    if not f:
        return []
    r = executor.run(f["options_sql"])
    return [str(row[0]) for row in r.rows if row[0] is not None] if r.ok else []
