"""Streamlit UI: ask questions in plain English, see the SQL, the result and the agent's trace.

    streamlit run app.py
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from sqlagent import PRESETS, GroqClient, SQLAgent, load_glossary

ROOT = Path(__file__).parent
DEMO_DB = ROOT / "bench" / "retail.db"
GLOSSARY = ROOT / "bench" / "glossary.json"

st.set_page_config(page_title="SQL Chat Assistant v2", page_icon="🗄️", layout="wide")

if not DEMO_DB.exists():
    from bench.build_db import build
    build(DEMO_DB)

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("Settings")
    upload = st.file_uploader("SQLite database (optional)", type=["db", "sqlite", "sqlite3"])
    if upload:
        tmp = Path(tempfile.gettempdir()) / f"upload_{upload.name}"
        tmp.write_bytes(upload.getvalue())
        db_path, glossary = tmp, []
        st.caption(f"Using uploaded `{upload.name}`")
    else:
        db_path, glossary = DEMO_DB, load_glossary(GLOSSARY)
        st.caption("Using the demo retail database")
    preset = st.selectbox("Agent config", list(PRESETS), index=list(PRESETS).index("full"),
                          help="Same presets the benchmark compares")
    model = st.text_input("Groq model", os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"))
    if not os.getenv("GROQ_API_KEY"):
        try:  # Streamlit Cloud: key comes from the app's Secrets
            os.environ["GROQ_API_KEY"] = st.secrets["GROQ_API_KEY"]
        except Exception:  # no secrets file locally
            pass
    if not os.getenv("GROQ_API_KEY"):
        key = st.text_input("GROQ_API_KEY", type="password")
        if key:
            os.environ["GROQ_API_KEY"] = key


@st.cache_resource(show_spinner=False)
def get_agent(db: str, preset: str, model: str, use_glossary: bool) -> SQLAgent:
    return SQLAgent(db, GroqClient(model=model), PRESETS[preset],
                    glossary=load_glossary(GLOSSARY) if use_glossary else [])


chat_tab, schema_tab, bench_tab = st.tabs(["Ask", "Schema", "Benchmark"])

# ------------------------------------------------------------------ chat
with chat_tab:
    st.title("SQL Chat Assistant v2")
    st.caption("Plain-English questions → validated, read-only SQL → results. Every step is shown.")

    if "history" not in st.session_state:
        st.session_state.history = []

    def show(res):
        if res.ok:
            df = pd.DataFrame(res.result.rows, columns=res.result.columns)
            st.dataframe(df, width="stretch", hide_index=True)
            if res.result.truncated:
                st.caption("Showing the first 1,000 rows.")
            num = df.select_dtypes("number").columns
            if len(df.columns) == 2 and len(num) == 1 and 1 < len(df) <= 50 and df.columns[0] not in num:
                st.bar_chart(df.set_index(df.columns[0]))
        else:
            st.error(f"Could not answer: {res.result.error if res.result else 'unknown error'}")
        st.code(res.sql or "", language="sql")
        with st.expander(f"Agent trace · {res.llm_calls} LLM call(s) · "
                         f"{res.prompt_tokens + res.completion_tokens:,} tokens · {res.latency_s:.1f}s"):
            st.write("**Tables sent to the model:** " + ", ".join(res.linked_tables))
            for i, s in enumerate(res.steps, 1):
                icon = {"generate": "✍️", "guard_error": "🛡️", "exec_error": "⚠️", "empty": "🔍", "ok": "✅"}[s.kind]
                st.markdown(f"{i}. {icon} **{s.kind}** {s.detail}")
                if s.kind == "generate":
                    st.code(s.sql, language="sql")

    for q, res in st.session_state.history:
        with st.chat_message("user"):
            st.write(q)
        with st.chat_message("assistant"):
            show(res)

    if not st.session_state.history and db_path == DEMO_DB:
        st.markdown("**Try:** _Which 5 customers placed the most orders?_ · "
                    "_What was the revenue for each month of 2024?_ · "
                    "_Which sales rep generated the most revenue in each region?_")

    # Public demo guard: set DEMO_MAX_QUESTIONS (e.g. in Streamlit secrets) to cap questions per visitor,
    # so one visitor can't spend the whole daily Groq quota.
    max_q = int(os.getenv("DEMO_MAX_QUESTIONS", "0") or 0)
    used = len(st.session_state.history)
    if max_q:
        st.caption(f"Demo limit: {max_q - used} of {max_q} questions left in this session.")
    question = st.chat_input("Ask a question about the data", disabled=bool(max_q) and used >= max_q)
    if question:
        if not os.getenv("GROQ_API_KEY"):
            st.warning("Add a GROQ_API_KEY in the sidebar first.")
        else:
            agent = get_agent(str(db_path), preset, model, bool(glossary))
            with st.chat_message("user"):
                st.write(question)
            with st.chat_message("assistant"), st.spinner("Thinking…"):
                try:
                    res = agent.run(question)
                except Exception as e:  # API trouble shouldn't show a stack trace to visitors
                    res = None
                    if type(e).__name__ == "RateLimitError":
                        st.warning("The demo's free model quota is used up for now. Please try again later. "
                                   "Meanwhile, the Benchmark tab shows how the agent scores.")
                    else:
                        st.error(f"The model API returned an error ({type(e).__name__}). Please try again.")
                if res is not None:
                    show(res)
            if res is not None:
                st.session_state.history.append((question, res))
                st.rerun()  # refresh the "questions left" counter

# ------------------------------------------------------------------ schema
with schema_tab:
    from sqlagent.executor import Executor
    from sqlagent.schema import introspect, render, render_glossary

    conn = Executor(db_path).connect()
    tables = introspect(conn)
    conn.close()
    st.code(render(tables), language="sql")
    if glossary:
        st.subheader("Business glossary")
        st.markdown(render_glossary(glossary))

# ------------------------------------------------------------------ benchmark
with bench_tab:
    runs = sorted((ROOT / "bench" / "results").glob("*/report.md"), reverse=True)
    if not runs:
        st.info("No benchmark runs yet. Run `python -m bench.evaluate` to create one.")
    else:
        pick = st.selectbox("Run", runs, format_func=lambda p: p.parent.name)
        st.markdown(pick.read_text(encoding="utf-8"))
