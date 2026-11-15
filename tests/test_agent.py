import json
import sqlite3
from pathlib import Path

import pytest

from bench.build_db import build
from bench.metrics import results_match
from sqlagent import PRESETS, Executor, GuardError, SQLAgent, ScriptedClient, validate
from sqlagent.guard import extract_sql
from sqlagent.schema import introspect, link_tables, load_glossary

BENCH = Path(__file__).resolve().parent.parent / "bench"


@pytest.fixture(scope="session")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "retail.db"
    build(path)
    return path


# ---------------------------------------------------------------- guard
@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "WITH x AS (SELECT 1 AS a) SELECT a FROM x",
    "SELECT name FROM products UNION SELECT name FROM categories",
])
def test_guard_allows_reads(sql):
    assert validate(sql) == sql


@pytest.mark.parametrize("sql", [
    "DELETE FROM orders",
    "DROP TABLE customers",
    "UPDATE products SET price = 0",
    "INSERT INTO categories VALUES (99, 'x')",
    "SELECT 1; DROP TABLE orders",
    "PRAGMA table_info(orders)",
    "ATTACH DATABASE 'x.db' AS x",
    "",
])
def test_guard_blocks_writes_and_multi_statements(sql):
    with pytest.raises(GuardError):
        validate(sql)


def test_extract_sql_from_fenced_reply():
    assert extract_sql("Sure!\n```sql\nSELECT 1;\n```\nDone") == "SELECT 1"
    assert extract_sql("SELECT 2") == "SELECT 2"


# ---------------------------------------------------------------- executor
def test_executor_is_read_only(db):
    r = Executor(db).run("DELETE FROM orders")  # bypassing the guard on purpose
    assert not r.ok
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM orders").fetchone()[0] > 0


def test_executor_timeout(db):
    slow = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c"
    r = Executor(db, timeout_s=0.5).run(slow)
    assert not r.ok and "timeout" in r.error


def test_executor_row_cap(db):
    r = Executor(db, max_rows=10).run("SELECT * FROM orders")
    assert len(r.rows) == 10 and r.truncated


# ---------------------------------------------------------------- metric
def test_metric_rules():
    assert results_match([(1, "a")], [("a", 1.0)])                          # column order + int/float
    assert results_match([(1.234,)], [(1.2344,)])                           # 2-decimal rounding
    assert not results_match([(1, "a")], [(1, "a", "extra")])               # extra column
    assert results_match([(1,), (2,)], [(2,), (1,)])                        # unordered by default
    assert not results_match([(1,), (2,)], [(2,), (1,)], order_matters=True)
    assert not results_match([(1,), (1,)], [(1,)])                          # duplicates count


def test_gold_queries_all_run(db):
    ex = Executor(db)
    for q in json.loads((BENCH / "questions.json").read_text(encoding="utf-8")):
        r = ex.run(q["gold_sql"])
        assert r.ok and r.rows, q["id"]


# ---------------------------------------------------------------- schema linking
def test_schema_linking_covers_gold_tables(db):
    import sqlglot
    from sqlglot import exp

    conn = sqlite3.connect(db)
    tables = introspect(conn)
    gloss = load_glossary(BENCH / "glossary.json")
    for q in json.loads((BENCH / "questions.json").read_text(encoding="utf-8")):
        tree = sqlglot.parse_one(q["gold_sql"], read="sqlite")
        ctes = {c.alias for c in tree.find_all(exp.CTE)}
        gold = {t.name for t in tree.find_all(exp.Table) if t.name not in ctes}
        assert gold <= set(link_tables(q["question"], tables, gloss)), q["id"]


# ---------------------------------------------------------------- agent loop
def test_agent_self_corrects_after_bad_column(db):
    replies = iter(["```sql\nSELECT COUNT(*) FROM orders WHERE order_status = 'cancelled'\n```",
                    "```sql\nSELECT COUNT(*) FROM orders WHERE status = 'cancelled'\n```"])
    agent = SQLAgent(db, ScriptedClient(lambda m, i: next(replies)), PRESETS["full"])
    res = agent.run("How many orders were cancelled?")
    assert res.ok and res.result.rows[0][0] > 0
    assert [s.kind for s in res.steps] == ["generate", "exec_error", "generate", "ok"]
    assert "order_status" in res.steps[1].sql


def test_agent_feeds_guard_error_back(db):
    replies = iter(["```sql\nDROP TABLE orders\n```", "```sql\nSELECT COUNT(*) FROM orders\n```"])
    llm = ScriptedClient(lambda m, i: next(replies))
    res = SQLAgent(db, llm, PRESETS["full"]).run("How many orders?")
    assert res.ok and res.steps[1].kind == "guard_error"


def test_baseline_does_not_retry(db):
    llm = ScriptedClient(lambda m, i: "SELECT nope FROM orders")
    res = SQLAgent(db, llm, PRESETS["baseline"]).run("x")
    assert not res.ok and llm.calls == 1


def test_gives_up_after_max_repairs(db):
    llm = ScriptedClient(lambda m, i: "SELECT nope FROM orders")
    res = SQLAgent(db, llm, PRESETS["full"]).run("x")
    assert not res.ok and llm.calls == 1 + PRESETS["full"].max_repairs


def test_empty_result_double_check(db):
    replies = iter(["SELECT * FROM customers WHERE country = 'germany'",
                    "SELECT * FROM customers WHERE country = 'Germany'"])
    res = SQLAgent(db, ScriptedClient(lambda m, i: next(replies)), PRESETS["full"]).run("German customers?")
    assert res.ok and len(res.result.rows) > 0
    assert "empty" in [s.kind for s in res.steps]


def test_offline_harness_end_to_end(tmp_path):
    from bench.evaluate import main
    main(["--llm", "noisy", "--configs", "baseline,full", "--out", str(tmp_path), "--limit", "10"])
    lines = [json.loads(l) for l in (tmp_path / "predictions.jsonl").read_text(encoding="utf-8").splitlines()]
    full = [r for r in lines if r["config"] == "full"]
    assert all(r["correct"] for r in full)
    assert (tmp_path / "report.md").exists()


# ---------------------------------------------------------------- API errors and --resume
def test_retry_after_parsing():
    from sqlagent.llm import retry_after_seconds
    assert retry_after_seconds("Please try again in 10m47.136s.") == pytest.approx(647.136)
    assert retry_after_seconds("Please try again in 2.5s.") == pytest.approx(2.5)
    assert retry_after_seconds("Please try again in 450ms.") == pytest.approx(0.45)
    assert retry_after_seconds("no hint") is None


def test_daily_limit_stops_run_and_resume_finishes_it(tmp_path, monkeypatch):
    import bench.evaluate as ev

    class RateLimitError(Exception):  # same class name as the Groq SDK's
        pass

    questions = json.loads((BENCH / "questions.json").read_text(encoding="utf-8"))
    gold = {q["question"]: q["gold_sql"] for q in questions}
    budget = {"calls": 3}  # the "daily quota" runs out after 3 questions

    def responder(messages, i):
        if budget["calls"] == 0:
            raise RateLimitError("Rate limit reached on tokens per day (TPD). Please try again in 10m47s.")
        budget["calls"] -= 1
        return f"```sql\n{gold[ev._current_question(messages)]}\n```"

    monkeypatch.setattr(ev, "GroqClient", lambda model=None: ScriptedClient(responder, "fake"))
    args = ["--configs", "baseline", "--limit", "6", "--out", str(tmp_path)]
    ev.main(args)
    recs = [json.loads(l) for l in (tmp_path / "predictions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(recs) == 4 and sum(r["correct"] for r in recs) == 3     # stopped right after the error
    assert recs[-1]["infra_error"] is True
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "**100.0%**" in report and "3/6" in report and "Incomplete run" in report

    budget["calls"] = 100                                               # quota resets
    ev.main(["--resume", str(tmp_path), "--configs", "baseline", "--limit", "6"])
    recs = [json.loads(l) for l in (tmp_path / "predictions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(recs) == 6 and all(r["correct"] for r in recs)
    assert budget["calls"] == 97                                        # only the 3 missing ones re-ran
    assert "Incomplete" not in (tmp_path / "report.md").read_text(encoding="utf-8")
