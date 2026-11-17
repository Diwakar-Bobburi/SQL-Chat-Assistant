"""Streamlit UI: ask questions in plain English, see the SQL, the result and the agent's trace.

    streamlit run app.py
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from sqlagent import PRESETS, Executor, GroqClient, SQLAgent, load_glossary
from sqlagent.dashboard import Dashboard, Tile, filter_options, load_dashboard, parse_dashboard, run_tile
from sqlagent.viz import ChartSpec, build_chart, fmt_number, suggest_chart

ROOT = Path(__file__).parent
DEMO_DB = ROOT / "bench" / "retail.db"
GLOSSARY = ROOT / "bench" / "glossary.json"
OVERVIEW = ROOT / "dashboards" / "overview.json"

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


if "history" not in st.session_state:
    st.session_state.history = []
if "pins" not in st.session_state:
    st.session_state.pins: list[Tile] = []


def draw_chart(df: pd.DataFrame, spec: ChartSpec | None, previous: pd.DataFrame | None = None, key: str = ""):
    """KPI tiles, a chart, or nothing (the caller shows the table)."""
    if spec is None:
        return False
    if spec.kind == "kpi":
        row = df.iloc[0]
        prev = previous.iloc[0] if previous is not None and not previous.empty else None
        cols = st.columns(len(spec.y))
        for col, name in zip(cols, spec.y):
            delta = None
            if prev is not None and pd.notna(prev.get(name)) and prev.get(name):
                delta = f"{100 * (row[name] - prev[name]) / abs(prev[name]):+.1f}% vs previous year"
            label = f"{name} · {row[spec.x]}" if spec.x else name
            col.metric(label, fmt_number(row[name]), delta)
        return True
    st.altair_chart(build_chart(spec, df), width="stretch", key=key or None)
    return True


def draw_tile(tile: Tile, executor: Executor, params: dict | None, key: str, on_remove=None):
    with st.container(border=True):
        head, tools = st.columns([6, 1])
        head.markdown(f"**{tile.title}**")
        if on_remove and tools.button("✕", key=f"rm_{key}", help="Remove from dashboard"):
            on_remove()
            st.rerun()
        res = run_tile(executor, tile, params)
        if res.error:
            st.error(res.error)
        elif res.df.empty:
            st.info("No data for this selection.")
        elif not draw_chart(res.df, res.chart, res.previous, key=f"chart_{key}"):
            st.dataframe(res.df, width="stretch", hide_index=True)
        if tile.caption:
            st.caption(tile.caption)
        with st.expander("SQL"):
            st.code(tile.sql, language="sql")


def draw_dashboard(dash: Dashboard, executor: Executor, params: dict | None, key: str, removable: bool = False):
    i, tiles = 0, dash.tiles
    while i < len(tiles):  # full-width tiles get a row; half tiles are paired
        row = [tiles[i]] if tiles[i].width == "full" or i + 1 >= len(tiles) or tiles[i + 1].width == "full" \
            else [tiles[i], tiles[i + 1]]
        cols = st.columns(len(row)) if len(row) > 1 else [st.container()]
        for j, (col, tile) in enumerate(zip(cols, row)):
            idx = i + j
            with col:
                draw_tile(tile, executor, params, key=f"{key}_{idx}",
                          on_remove=(lambda idx=idx: st.session_state.pins.pop(idx)) if removable else None)
        i += len(row)


chat_tab, dash_tab, schema_tab, bench_tab = st.tabs(["Ask", "Dashboard", "Schema", "Benchmark"])

# ------------------------------------------------------------------ chat
with chat_tab:
    st.title("SQL Chat Assistant v2")
    st.caption("Plain-English questions → validated, read-only SQL → results. Every step is shown.")

    def show(res, idx: int | None = None):
        if res.ok:
            df = pd.DataFrame(res.result.rows, columns=res.result.columns)
            spec = suggest_chart(df)
            if draw_chart(df, spec, key=f"ask_{idx}"):
                with st.expander(f"Table · {len(df):,} rows"):
                    st.dataframe(df, width="stretch", hide_index=True)
            else:
                st.dataframe(df, width="stretch", hide_index=True)
            if res.result.truncated:
                st.caption("Showing the first 1,000 rows.")
            if idx is not None:
                pinned = any(p.sql == res.sql for p in st.session_state.pins)
                if st.button("📌 Pinned" if pinned else "📌 Pin to dashboard", key=f"pin_{idx}", disabled=pinned):
                    st.session_state.pins.append(Tile(title=res.question, sql=res.sql,
                                                      chart=spec, width="full" if spec and spec.kind == "line" else "half"))
                    st.toast("Added to My dashboard")
                    st.rerun()
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

    for i, (q, res) in enumerate(st.session_state.history):
        with st.chat_message("user"):
            st.write(q)
        with st.chat_message("assistant"):
            show(res, i)

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
                                   "Meanwhile, the Dashboard tab works without the model.")
                    else:
                        st.error(f"The model API returned an error ({type(e).__name__}). Please try again.")
                if res is not None:
                    show(res)
            if res is not None:
                st.session_state.history.append((question, res))
                st.rerun()  # refresh the "questions left" counter

# ------------------------------------------------------------------ dashboard
with dash_tab:
    executor = Executor(db_path)
    views = (["Business overview"] if db_path == DEMO_DB else []) + [f"My dashboard ({len(st.session_state.pins)})"]
    view = st.radio("View", views, horizontal=True, label_visibility="collapsed")

    if view == "Business overview":
        overview = load_dashboard(OVERVIEW)
        top, pick = st.columns([4, 1])
        top.subheader(overview.title)
        years = filter_options(executor, overview, "year")
        year = pick.selectbox("Year", ["All years", *years], index=len(years))  # latest year by default
        st.caption(overview.description)
        draw_dashboard(overview, executor, {"year": None if year == "All years" else year}, key="ov")
    else:
        st.subheader("My dashboard")
        pins = st.session_state.pins
        if not pins:
            st.info("Ask a question, then click **📌 Pin to dashboard** under the answer. Your charts collect here.")
        else:
            draw_dashboard(Dashboard("My dashboard", pins), executor, None, key="my", removable=True)
        st.divider()
        c1, c2, c3 = st.columns(3)
        c1.download_button("⬇ Export as JSON", Dashboard("My dashboard", pins).to_json(),
                           file_name="dashboard.json", mime="application/json", disabled=not pins)
        if c2.button("Clear all", disabled=not pins):
            st.session_state.pins = []
            st.rerun()
        imported = c3.file_uploader("Import a dashboard JSON", type=["json"], label_visibility="collapsed")
        if imported is not None and st.session_state.get("_imported") != imported.file_id:
            try:
                st.session_state.pins = parse_dashboard(imported.getvalue().decode("utf-8")).tiles
                st.session_state._imported = imported.file_id
                st.rerun()
            except (ValueError, UnicodeDecodeError, KeyError) as e:
                st.error(f"Could not import: {e}")

# ------------------------------------------------------------------ schema
with schema_tab:
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
