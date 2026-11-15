"""Convert Spider's dev set into this harness's question format, so the same agent can be
scored on a public benchmark as well as the custom retail one.

1. Download Spider (https://yale-lily.github.io/spider) and unzip it, e.g. to ./spider
2. python -m bench.spider_adapter --spider ./spider --n 200
3. python -m bench.evaluate --questions bench/questions_spider.json --configs baseline,full

Spider spans ~20 dev databases, so each question carries its own `db` path. The retail
glossary is only applied to retail.db. Spider has no difficulty field here; questions are
bucketed by a rough SQL-complexity heuristic so the report still splits them.
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path


def bucket(sql: str) -> str:
    s = sql.upper()
    score = s.count(" JOIN ") + 2 * s.count("SELECT", 1) + s.count("GROUP BY") + s.count("HAVING") \
        + s.count(" INTERSECT ") + s.count(" UNION ") + s.count(" EXCEPT ")
    return "easy" if score == 0 else "medium" if score <= 2 else "hard"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spider", required=True, help="unzipped Spider folder (has dev.json and database/)")
    ap.add_argument("--n", type=int, default=200, help="sample size (full dev set is 1034)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=str(Path(__file__).parent / "questions_spider.json"))
    args = ap.parse_args()

    root = Path(args.spider).resolve()
    dev = json.loads((root / "dev.json").read_text(encoding="utf-8"))
    random.Random(args.seed).shuffle(dev)
    out = []
    for i, ex in enumerate(dev[: args.n]):
        db = root / "database" / ex["db_id"] / f"{ex['db_id']}.sqlite"
        out.append({"id": f"s{i:04d}", "difficulty": bucket(ex["query"]), "question": ex["question"],
                    "gold_sql": ex["query"], "db": str(db),
                    "order_matters": "ORDER BY" in ex["query"].upper()})
    Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {len(out)} Spider questions to {args.out}")


if __name__ == "__main__":
    main()
