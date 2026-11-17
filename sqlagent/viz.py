"""Pick a chart from the shape of a query result, and draw it.

Rules (first match wins):
    1 row, numbers (+ at most 1 label col)  -> "kpi"      metric tiles
    first column looks like a date/period   -> "line"     trend over time
    one text column + numbers, ≤ 40 rows    -> "bar"      ranked horizontal bars (grouped if several)
    Several numeric series are only drawn together when they share a scale (revenue next to
    a growth % would flatten one of them); otherwise only the first is plotted.
    exactly two numeric columns, ≥ 3 rows   -> "scatter"
    anything else                           -> None       table only
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

import pandas as pd

_PERIOD = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")  # 2024 | 2024-03 | 2024-03-15


@dataclass
class ChartSpec:
    kind: str                 # kpi | line | bar | scatter
    x: str | None = None
    y: list[str] | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "ChartSpec | None":
        return cls(**d) if d else None


def _numeric_cols(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])]


def _is_period(s: pd.Series) -> bool:
    vals = s.dropna().astype(str)
    return len(vals) > 0 and vals.map(lambda v: bool(_PERIOD.match(v))).all()


def _same_scale(df: pd.DataFrame, cols: list[str]) -> list[str]:
    """Keep the leading columns whose typical magnitude is within 10x of the first."""
    def mag(c):
        m = df[c].abs().median()
        return float(m) if pd.notna(m) and m else 0.0
    base = mag(cols[0])
    keep = [cols[0]]
    for c in cols[1:]:
        m = mag(c)
        if base and m and max(base, m) / min(base, m) <= 10:
            keep.append(c)
    return keep


def suggest_chart(df: pd.DataFrame) -> ChartSpec | None:
    if df is None or df.empty or df.columns.duplicated().any():
        return None
    nums = _numeric_cols(df)
    others = [c for c in df.columns if c not in nums]

    if len(df) == 1 and nums and len(nums) <= 4 and len(others) <= 1:
        return ChartSpec("kpi", x=others[0] if others else None, y=nums)

    first = df.columns[0]
    if len(df) >= 2 and _is_period(df[first]):
        ys = [c for c in nums if c != first]
        if ys:
            return ChartSpec("line", x=first, y=_same_scale(df, ys)[:3])

    if len(others) == 1 and nums and 2 <= len(df) <= 40 and df[others[0]].is_unique:
        return ChartSpec("bar", x=others[0], y=_same_scale(df, nums)[:3])

    if not others and len(nums) == 2 and len(df) >= 3:
        return ChartSpec("scatter", x=nums[0], y=[nums[1]])
    return None


def build_chart(spec: ChartSpec, df: pd.DataFrame, height: int = 280):
    """Altair chart for line/bar/scatter. KPIs are drawn by the caller with st.metric."""
    import altair as alt

    if spec.kind == "line":
        data = df[[spec.x, *spec.y]].copy()
        data[spec.x] = data[spec.x].astype(str)
        long = data.melt(spec.x, var_name="series", value_name="value")
        enc = dict(x=alt.X(f"{spec.x}:O", title=None, axis=alt.Axis(labelAngle=-45)),
                   y=alt.Y("value:Q", title=spec.y[0] if len(spec.y) == 1 else None),
                   tooltip=[spec.x, "series", alt.Tooltip("value:Q", format=",.2f")])
        if len(spec.y) > 1:
            enc["color"] = alt.Color("series:N", title=None)
        return alt.Chart(long).mark_line(point=True).encode(**enc).properties(height=height)

    if spec.kind == "bar":
        if len(spec.y) == 1:
            y = spec.y[0]
            return (alt.Chart(df).mark_bar()
                    .encode(y=alt.Y(f"{spec.x}:N", sort="-x", title=None),
                            x=alt.X(f"{y}:Q", title=y),
                            tooltip=[spec.x, alt.Tooltip(f"{y}:Q", format=",.2f")])
                    .properties(height=alt.Step(28)))  # fixed space per bar
        order = df.sort_values(spec.y[0], ascending=False)[spec.x].astype(str).tolist()
        long = df[[spec.x, *spec.y]].melt(spec.x, var_name="series", value_name="value")
        return (alt.Chart(long).mark_bar()
                .encode(y=alt.Y(f"{spec.x}:N", sort=order, title=None),
                        x=alt.X("value:Q", title=None),
                        yOffset="series:N", color=alt.Color("series:N", title=None),
                        tooltip=[spec.x, "series", alt.Tooltip("value:Q", format=",.2f")])
                .properties(height=alt.Step(14 * len(spec.y) + 8)))

    if spec.kind == "scatter":
        y = spec.y[0]
        return (alt.Chart(df).mark_circle(size=60)
                .encode(x=alt.X(f"{spec.x}:Q"), y=alt.Y(f"{y}:Q"), tooltip=[spec.x, y])
                .properties(height=height))
    raise ValueError(f"no Altair chart for kind={spec.kind!r}")


def fmt_number(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    if isinstance(v, float) and not v.is_integer():
        return f"{v:,.2f}"
    return f"{int(v):,}"
