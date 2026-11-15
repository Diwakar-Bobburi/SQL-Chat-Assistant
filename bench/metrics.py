"""Execution accuracy (EX): a prediction is correct if it returns the same result as the gold query.

Comparison rules (documented so the number means something):
- Numbers are compared after rounding to 2 decimals (ints and floats are treated alike).
- Column order does not matter (values inside each row are compared as a sorted tuple),
  but the number of columns does: extra or missing columns count as wrong.
- Row order matters only for questions flagged `order_matters`; otherwise rows are
  compared as a multiset (duplicates count).
"""
from __future__ import annotations

from collections import Counter


def _norm_value(v):
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return round(float(v), 2) + 0.0  # +0.0 turns -0.0 into 0.0
    if v is None:
        return None
    return str(v).strip()


def _norm_row(row) -> tuple:
    vals = [_norm_value(v) for v in row]
    return tuple(sorted(vals, key=lambda x: (x is None, type(x).__name__, str(x))))


def results_match(gold_rows: list[tuple], pred_rows: list[tuple], order_matters: bool = False) -> bool:
    if gold_rows and pred_rows and len(gold_rows[0]) != len(pred_rows[0]):
        return False
    g = [_norm_row(r) for r in gold_rows]
    p = [_norm_row(r) for r in pred_rows]
    if order_matters:
        return g == p
    return Counter(g) == Counter(p)
