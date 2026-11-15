"""The text-to-SQL agent loop.

    question
      -> schema linking (pick relevant tables)
      -> prompt (schema DDL + sample rows + business glossary + few-shot)
      -> LLM generates SQL
      -> guard (single read-only SELECT?)        -- fail -> feed error back, retry
      -> execute (read-only, timeout, row cap)   -- fail -> feed error back, retry
      -> empty result? ask the model to double-check once
      -> answer
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from . import prompts
from .executor import Executor, QueryResult
from .guard import GuardError, extract_sql, validate
from .llm import LLMClient
from .schema import GlossaryTerm, introspect, link_tables, render, render_glossary


@dataclass
class AgentConfig:
    name: str = "full"
    sample_rows: bool = True       # show sample rows in schema
    glossary: bool = True          # include business definitions
    few_shot: bool = True          # include few-shot examples
    schema_linking: bool = True    # prune schema to relevant tables
    max_repairs: int = 3           # self-correction attempts on errors
    check_empty: bool = True       # one extra look when the result is empty


PRESETS = {
    "baseline": AgentConfig("baseline", sample_rows=False, glossary=False, few_shot=False,
                            schema_linking=False, max_repairs=0, check_empty=False),
    "context": AgentConfig("context", few_shot=False, schema_linking=False, max_repairs=0, check_empty=False),
    "fewshot": AgentConfig("fewshot", schema_linking=False, max_repairs=0, check_empty=False),
    "full": AgentConfig("full"),
}


@dataclass
class Step:
    kind: str          # "generate" | "guard_error" | "exec_error" | "empty" | "ok"
    sql: str = ""
    detail: str = ""


@dataclass
class AgentResult:
    question: str
    sql: str | None
    result: QueryResult | None
    steps: list[Step] = field(default_factory=list)
    linked_tables: list[str] = field(default_factory=list)
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.result is not None and self.result.ok


class SQLAgent:
    def __init__(self, db_path: str | Path, llm: LLMClient, config: AgentConfig | None = None,
                 glossary: list[GlossaryTerm] | None = None, executor: Executor | None = None):
        self.llm = llm
        self.config = config or AgentConfig()
        self.glossary = glossary or []
        self.executor = executor or Executor(db_path)
        conn = self.executor.connect()
        try:
            self.tables = introspect(conn, sample_rows=3)
        finally:
            conn.close()

    def _ask(self, messages: list[dict], res: AgentResult) -> str:
        r = self.llm.complete(messages)
        res.llm_calls += 1
        res.prompt_tokens += r.prompt_tokens
        res.completion_tokens += r.completion_tokens
        messages.append({"role": "assistant", "content": r.text})
        return extract_sql(r.text)

    def run(self, question: str) -> AgentResult:
        cfg = self.config
        start = time.perf_counter()
        res = AgentResult(question=question, sql=None, result=None)

        names = (link_tables(question, self.tables, self.glossary) if cfg.schema_linking
                 else list(self.tables))
        res.linked_tables = names
        schema_txt = render({n: self.tables[n] for n in names}, include_samples=cfg.sample_rows)
        gloss_txt = render_glossary(self.glossary) if cfg.glossary else ""

        messages = [{"role": "system", "content": prompts.SYSTEM}]
        if cfg.few_shot:
            messages += prompts.few_shot_messages()
        messages.append({"role": "user", "content": prompts.user_prompt(schema_txt, question, gloss_txt)})

        sql = self._ask(messages, res)
        res.steps.append(Step("generate", sql))
        repairs, empty_checked = 0, False

        while True:
            res.sql = sql
            error = None
            try:
                validate(sql)
            except GuardError as e:
                error = str(e)
                res.steps.append(Step("guard_error", sql, error))
                res.result = QueryResult(error=error)
            if error is None:
                qr = self.executor.run(sql)
                res.result = qr
                if not qr.ok:
                    error = qr.error
                    res.steps.append(Step("exec_error", sql, error))

            if error is not None:
                if repairs >= cfg.max_repairs:
                    break
                repairs += 1
                messages.append({"role": "user", "content": prompts.repair_prompt(error)})
                sql = self._ask(messages, res)
                res.steps.append(Step("generate", sql, f"repair {repairs}"))
                continue

            if cfg.check_empty and not qr.rows and not empty_checked:
                empty_checked = True
                res.steps.append(Step("empty", sql, "0 rows; asking model to double-check"))
                messages.append({"role": "user", "content": prompts.empty_check_prompt()})
                new_sql = self._ask(messages, res)
                if new_sql.strip() != sql.strip():
                    sql = new_sql
                    res.steps.append(Step("generate", sql, "after empty check"))
                    continue
            res.steps.append(Step("ok", sql, f"{len(qr.rows)} rows"))
            break

        res.latency_s = time.perf_counter() - start
        return res
