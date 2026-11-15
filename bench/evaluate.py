"""Run the text-to-SQL benchmark and write a report.

    python -m bench.evaluate                                  # all 4 configs, Groq
    python -m bench.evaluate --configs baseline,full --limit 10
    python -m bench.evaluate --llm oracle                     # harness sanity check, no API key
    python -m bench.evaluate --llm noisy                      # checks the self-correction path offline
    python -m bench.evaluate --resume bench/results/<run>     # finish a run cut short by API limits

Outputs (in --out, default bench/results/<run-name>/):
    report.md          summary tables (overall, by difficulty, cost/latency)
    predictions.jsonl  every question x config: SQL, correctness, trace
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bench.metrics import results_match  # noqa: E402
from sqlagent import PRESETS, Executor, GroqClient, SQLAgent, ScriptedClient, load_glossary  # noqa: E402

BENCH = ROOT / "bench"
# Errors that would hit every question (bad key, unknown model): abort instead of logging 100% "fails".
FATAL_ERRORS = {"AuthenticationError", "NotFoundError", "PermissionDeniedError", "BadRequestError"}
# Errors caused by the API, not the model: recorded but NOT counted in accuracy, and re-run by --resume.
INFRA_ERRORS = {"RateLimitError", "APIConnectionError", "APITimeoutError", "InternalServerError"}


def gold_tables(sql: str) -> set[str]:
    import sqlglot
    from sqlglot import exp
    tree = sqlglot.parse_one(sql, read="sqlite")
    ctes = {c.alias for c in tree.find_all(exp.CTE)}
    return {t.name for t in tree.find_all(exp.Table) if t.name and t.name not in ctes}


# ------------------------------------------------------------ offline "models" for harness checks

def _current_question(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m["role"] == "user" and "Question:" in m["content"]:
            return m["content"].rsplit("Question:", 1)[1].strip()
    return ""


def make_offline_client(kind: str, questions: list[dict]) -> ScriptedClient:
    gold = {q["question"]: q["gold_sql"] for q in questions}

    def corrupt(sql: str) -> str:
        # Simulate a typical LLM mistake: a hallucinated column name.
        for a, b in [("order_date", "order_dt"), ("customer_id", "cust_id"), ("price", "unit_cost"),
                     ("rating", "stars"), ("status", "order_status"), ("name", "title")]:
            if re.search(rf"\b{a}\b", sql):
                return re.sub(rf"\b{a}\b", b, sql, count=1)
        return sql

    def responder(messages, _i):
        sql = gold.get(_current_question(messages), "SELECT 1")
        if kind == "noisy":
            is_first = messages[-1]["content"].startswith("Schema:")
            if is_first:
                sql = corrupt(sql)
        return f"```sql\n{sql}\n```"

    return ScriptedClient(responder, name=f"offline-{kind}")


# ------------------------------------------------------------------------------------ runner

def evaluate(questions, configs, llm, db_default, glossary, log=print, skip=frozenset()):
    """Returns (records, stop_reason). stop_reason is set when the API's daily quota runs out."""
    executors: dict[str, Executor] = {}
    gold_cache: dict[str, list] = {}
    records = []
    for cfg_name in configs:
        cfg = PRESETS[cfg_name]
        agents: dict[str, SQLAgent] = {}
        for i, q in enumerate(questions, 1):
            if (cfg_name, q["id"]) in skip:
                continue
            db = str(q.get("db") or db_default)
            if db not in executors:
                executors[db] = Executor(db, timeout_s=15)
            if db not in agents:
                use_gloss = glossary if Path(db).resolve() == Path(db_default).resolve() else []
                agents[db] = SQLAgent(db, llm, cfg, glossary=use_gloss, executor=executors[db])
            if q["id"] not in gold_cache:
                g = executors[db].run(q["gold_sql"], max_rows=100_000)
                if not g.ok:
                    raise RuntimeError(f"Gold SQL for {q['id']} fails: {g.error}")
                gold_cache[q["id"]] = g.rows

            try:
                res = agents[db].run(q["question"])
                pred_rows = res.result.rows if res.ok else []
                if res.ok and res.result.truncated:  # re-run uncapped for a fair comparison
                    pred_rows = executors[db].run(res.sql, max_rows=100_000).rows
                correct = res.ok and results_match(gold_cache[q["id"]], pred_rows, q.get("order_matters", False))
                rec = dict(
                    config=cfg_name, id=q["id"], difficulty=q.get("difficulty", "n/a"), question=q["question"],
                    gold_sql=q["gold_sql"], pred_sql=res.sql, correct=correct, executed=res.ok,
                    error=None if res.ok else (res.result.error if res.result else "no result"),
                    llm_calls=res.llm_calls, prompt_tokens=res.prompt_tokens,
                    completion_tokens=res.completion_tokens, latency_s=round(res.latency_s, 3),
                    repaired=any(s.kind in ("exec_error", "guard_error") for s in res.steps) and res.ok,
                    linked_tables=res.linked_tables,
                    linking_recall=gold_tables(q["gold_sql"]) <= set(res.linked_tables),
                    steps=[{"kind": s.kind, "sql": s.sql, "detail": s.detail} for s in res.steps],
                    infra_error=False,
                )
            except Exception as e:  # an API failure should not kill a long benchmark run...
                name = type(e).__name__
                if name in FATAL_ERRORS:  # ...unless it will fail every question
                    raise SystemExit(f"Stopping: {name}: {e}\nNo results were saved. "
                                     "Check GROQ_API_KEY and --model (list models: see README).")
                rec = dict(config=cfg_name, id=q["id"], difficulty=q.get("difficulty", "n/a"),
                           question=q["question"], gold_sql=q["gold_sql"], pred_sql=None, correct=False,
                           executed=False, error=f"{name}: {e}", llm_calls=0, prompt_tokens=0,
                           completion_tokens=0, latency_s=0, repaired=False, linked_tables=[],
                           linking_recall=False, steps=[], infra_error=name in INFRA_ERRORS)
            records.append(rec)
            status = "PASS" if rec["correct"] else "API ERROR (not scored)" if rec["infra_error"] else "fail"
            log(f"[{cfg_name:8}] {i:>3}/{len(questions)} {q['id']:>5} {status}")
            if rec["infra_error"] and "per day" in rec["error"]:
                return records, "The API's daily token limit was reached."
    return records, None


def summarize(records, configs, n_questions: int) -> str:
    scored = [r for r in records if not r.get("infra_error")]
    by_cfg = defaultdict(list)
    for r in scored:
        by_cfg[r["config"]].append(r)
    diffs = sorted({r["difficulty"] for r in records}, key=lambda d: {"easy": 0, "medium": 1, "hard": 2}.get(d, 9))
    n_diff = {d: len({r["id"] for r in records if r["difficulty"] == d}) for d in diffs}

    def pct(rs):
        return f"{100 * sum(r['correct'] for r in rs) / len(rs):.1f}%" if rs else "-"

    lines = ["## Execution accuracy", "",
             "Questions that hit an API error (rate limit, outage) are not scored; see the last column.", "",
             "| Config | Overall | " + " | ".join(f"{d} (n={n_diff[d]})" for d in diffs) + " | Scored |",
             "|---|---|" + "---|" * len(diffs) + "---|"]
    for c in configs:
        rs = by_cfg[c]
        lines.append(f"| {c} | **{pct(rs)}** | " + " | ".join(pct([r for r in rs if r['difficulty'] == d]) for d in diffs)
                     + f" | {len(rs)}/{n_questions} |")

    lines += ["", "## Reliability and cost (per scored question)", "",
              "| Config | Executes | Fixed by self-correction | Schema-linking recall | Avg tables in prompt | Avg LLM calls | Avg prompt tokens | Median latency (s) |",
              "|---|---|---|---|---|---|---|---|"]
    for c in configs:
        rs = by_cfg[c]
        if not rs:
            lines.append(f"| {c} |" + " - |" * 7)
            continue
        lines.append(
            f"| {c} | {100 * sum(r['executed'] for r in rs) / len(rs):.1f}% | {sum(r['repaired'] for r in rs)} | "
            f"{100 * sum(r['linking_recall'] for r in rs) / len(rs):.0f}% | "
            f"{statistics.mean(len(r['linked_tables']) for r in rs):.1f} | "
            f"{statistics.mean(r['llm_calls'] for r in rs):.2f} | {statistics.mean(r['prompt_tokens'] for r in rs):,.0f} | "
            f"{statistics.median(r['latency_s'] for r in rs):.2f} |")

    if len(configs) > 1:
        first, last = configs[0], configs[-1]
        a = {r["id"]: r["correct"] for r in by_cfg[first]}
        b = {r["id"]: r["correct"] for r in by_cfg[last]}
        won = sorted(i for i in a if b.get(i) and not a[i])
        lost = sorted(i for i in a if a[i] and not b.get(i))
        lines += ["", f"## {first} → {last}", "",
                  f"- Newly solved ({len(won)}): {', '.join(won) or '—'}",
                  f"- Regressions ({len(lost)}): {', '.join(lost) or '—'}"]

    fails = [r for r in by_cfg[configs[-1]] if not r["correct"]]
    if fails:
        lines += ["", f"## Failures in `{configs[-1]}`", ""]
        for r in fails:
            why = (r["error"] or "ran, but wrong result").splitlines()[0][:300]
            lines.append(f"- **{r['id']}** ({r['difficulty']}) {r['question']}  \n  _{why}_")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--questions", default=str(BENCH / "questions.json"))
    ap.add_argument("--db", default=str(BENCH / "retail.db"))
    ap.add_argument("--glossary", default=str(BENCH / "glossary.json"))
    ap.add_argument("--configs", default="baseline,context,fewshot,full")
    ap.add_argument("--llm", default="groq", choices=["groq", "oracle", "noisy"])
    ap.add_argument("--model", default=None, help="Groq model id (default: $GROQ_MODEL or openai/gpt-oss-120b)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--ids", default=None, help="comma-separated question ids")
    ap.add_argument("--out", default=None)
    ap.add_argument("--resume", default=None, metavar="RUN_DIR",
                    help="continue a previous run: keeps its scored results, re-runs API errors and unfinished questions")
    args = ap.parse_args(argv)

    if not Path(args.db).exists():
        from bench.build_db import build
        build(Path(args.db))

    questions = json.loads(Path(args.questions).read_text(encoding="utf-8"))
    if args.ids:
        wanted = set(args.ids.split(","))
        questions = [q for q in questions if q["id"] in wanted]
    if args.limit:
        questions = questions[: args.limit]
    configs = [c.strip() for c in args.configs.split(",")]
    unknown = [c for c in configs if c not in PRESETS]
    if unknown:
        ap.error(f"unknown config(s) {unknown}; choose from {list(PRESETS)}")

    llm = GroqClient(model=args.model) if args.llm == "groq" else make_offline_client(args.llm, questions)
    glossary = load_glossary(args.glossary)

    run_name = f"{datetime.now():%Y%m%d-%H%M}_{llm.name.replace('/', '_')}"
    out = Path(args.resume or args.out or BENCH / "results" / run_name)

    kept: list[dict] = []
    if args.resume:
        prev = out / "predictions.jsonl"
        if not prev.exists():
            ap.error(f"nothing to resume: {prev} not found")
        kept = [r for r in (json.loads(l) for l in prev.read_text(encoding="utf-8").splitlines() if l.strip())
                if not r.get("infra_error")]
        print(f"Resuming {out.name}: keeping {len(kept)} scored results.")
    skip = {(r["config"], r["id"]) for r in kept}

    t0 = time.time()
    new, stop_reason = evaluate(questions, configs, llm, args.db, glossary, skip=skip)
    order = {(c, q["id"]): i for i, (c, q) in enumerate((c, q) for c in configs for q in questions)}
    records = sorted(kept + new, key=lambda r: order.get((r["config"], r["id"]), 10**9))
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "predictions.jsonl", "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, default=str) + "\n")

    expected = len(configs) * len(questions)
    n_scored = sum(1 for r in records if not r.get("infra_error") and (r["config"], r["id"]) in order)
    resume_cmd = f"python -m bench.evaluate --resume {out}" + "".join(
        f" --{k} {v}" for k, v in [("configs", args.configs), ("ids", args.ids), ("limit", args.limit),
                                   ("model", args.model)] if v and not (k == "configs" and v == ap.get_default("configs")))
    status = "" if n_scored == expected else (
        f"\n> **Incomplete run: {n_scored}/{expected} scored.** {stop_reason or 'Some questions hit API errors.'}\n"
        f"> Finish it later with: `{resume_cmd}`\n")

    header = (f"# Text-to-SQL benchmark report\n\n"
              f"- Model: `{llm.name}`\n- Questions: {len(questions)} (`{Path(args.questions).name}`)\n"
              f"- Database: `{Path(args.db).name}`\n- Run time: {time.time() - t0:.0f}s\n"
              f"- Metric: execution accuracy (see `bench/metrics.py` for comparison rules)\n{status}\n")
    report = header + summarize(records, configs, len(questions)) + "\n"
    (out / "report.md").write_text(report, encoding="utf-8")
    print("\n" + report)
    print(f"Saved to {out}")


if __name__ == "__main__":
    main()
