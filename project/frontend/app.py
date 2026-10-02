"""LIHTC Graph Agent — Streamlit UI."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backend.utils.context import ConversationContext
from backend.utils.normalizer import Normalizer
from backend.pipeline.executor import Executor
from backend.cache.tool_cache import ToolCache
from backend.agents.cypher_builder import CypherBuilder
from backend.agents.orchestrator import Orchestrator
from backend.agents.synthesizer import Synthesizer
from backend.graph.neo4j_client import Neo4jClient
from backend.graph.ontology import GraphOntology
from backend.llm.client import NvidiaLLMClient
from backend.agents.cypher_validator_agent import CypherValidatorAgent
from backend.utils.config import load_settings

_ONTOLOGY_PATH = _PROJECT_ROOT / "data" / "ontology" / "lihtc_ontology.json"


@st.cache_resource(show_spinner=False, ttl=0)
def _load_ontology() -> GraphOntology:
    data = json.loads(_ONTOLOGY_PATH.read_text(encoding="utf-8"))
    known = set(GraphOntology.__dataclass_fields__)
    return GraphOntology(**{k: v for k, v in data.items() if k in known})


@st.cache_resource(show_spinner=False)
def _get_neo4j(uri: str, user: str, pwd: str) -> Neo4jClient:
    return Neo4jClient(uri, user, pwd)


# ---------------------------------------------------------------------------

PROVIDERS = {
    "NVIDIA (free)": {
        "base_url": None,  # NvidiaLLMClient default
        "fast": "nvidia/llama-3.1-nemotron-70b-instruct",
        "synth": ["nvidia/llama-3.1-nemotron-70b-instruct", "nvidia/nemotron-3-super-120b-a12b",
                  "mistralai/mistral-large-2-instruct"],
        "hint": "nvapi-…  (free key at build.nvidia.com)",
    },
    "OpenAI": {
        "base_url": "https://api.openai.com/v1",
        "fast": "gpt-4o-mini",
        "synth": ["gpt-4o", "gpt-4o-mini", "gpt-4.1-mini"],
        "hint": "sk-…",
    },
}


@st.cache_data(ttl=60, show_spinner=False)
def _neo4j_status(uri: str, user: str, password: str) -> tuple[bool, str]:
    """Quick connectivity check so a missing database is obvious before the first question."""
    try:
        from neo4j import GraphDatabase

        with GraphDatabase.driver(uri, auth=(user, password), connection_timeout=3) as driver:
            driver.verify_connectivity()
        return True, ""
    except Exception as exc:  # noqa: BLE001 - any failure means "not connected"
        return False, str(exc)[:160]


def main() -> None:
    st.set_page_config(page_title="LIHTC Agent", page_icon="🏠", layout="wide")
    _render_sidebar()
    _render_chat()


def _render_sidebar() -> None:
    with st.sidebar:
        st.title("LIHTC Agent")
        st.divider()

        provider = st.selectbox("LLM provider", list(PROVIDERS), key="provider")
        cfg = PROVIDERS[provider]

        if "api_key" not in st.session_state:
            st.session_state["api_key"] = load_settings().nvidia_api_key or ""

        api_key = st.text_input("API key", type="password",
                                value=st.session_state["api_key"],
                                placeholder=cfg["hint"],
                                help="Used only in this browser session.")
        st.session_state["api_key"] = api_key

        st.session_state["synth_model"] = st.selectbox(
            "Synthesizer model", cfg["synth"], key=f"synth_{provider}",
        )

        if st.button("Clear conversation"):
            for key in ("messages", "ctx"):
                st.session_state.pop(key, None)
            st.rerun()

        st.divider()
        settings = load_settings()
        ok, detail = _neo4j_status(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
        st.caption(f"Neo4j: {settings.neo4j_uri} — {'connected' if ok else 'not connected'}")
        if not ok:
            st.warning(
                "No Neo4j database is reachable, so questions cannot be answered yet. "
                "Set NEO4J_URI, NEO4J_USER and NEO4J_PASSWORD (in a .env file locally, or in the "
                "app's Secrets on Streamlit Cloud) and reload.",
                icon="⚠️",
            )
            st.caption(detail)


def _render_chat() -> None:
    if "messages" not in st.session_state: st.session_state.messages = []
    if "ctx"      not in st.session_state: st.session_state.ctx      = ConversationContext()

    ctx: ConversationContext = st.session_state.ctx

    if not st.session_state.messages:
        _render_examples()

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            if msg["role"] == "assistant" and msg.get("synthesis"):
                _render_synthesis(msg["synthesis"])
            else:
                st.markdown(msg["content"])

    question = st.chat_input("Ask about QCT, DDA, AMI limits, or fair lending risk...")
    if not question:
        return
    if not st.session_state.get("api_key"):
        st.toast("Add your API key (OpenAI or NVIDIA) in the sidebar first.")
        return

    st.session_state.messages.append({"role": "user", "content": question})
    ctx.add_user(question)
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        _run_pipeline(question, ctx)


def _render_examples() -> None:
    st.markdown(
        "<div style='text-align:center;padding:2rem 1rem 1rem'>"
        "<h2>LIHTC Graph Agent</h2>"
        "<p style='color:#888'>Ask about QCT designations, AMI limits, DDA areas, "
        "or fair lending risk.</p></div>",
        unsafe_allow_html=True,
    )
    examples = [
        "Is census tract 01001020700 eligible for the 30% basis boost in 2025?",
        "What is the AMI limit for a 4-person household in Travis County TX for 2025?",
        "Is Cook County IL a Difficult Development Area in 2025?",
        "Compare HMDA lending risk in Dallas and Houston for 2024",
    ]
    cols = st.columns(2)
    for i, ex in enumerate(examples):
        if cols[i % 2].button(ex, width="stretch", key=f"ex_{i}"):
            st.session_state.messages.append({"role": "user", "content": ex})
            st.rerun()


def _run_pipeline(question: str, ctx: ConversationContext) -> None:
    api_key     = st.session_state["api_key"]
    provider    = PROVIDERS[st.session_state.get("provider", "NVIDIA (free)")]
    synth_model = st.session_state.get("synth_model", provider["synth"][0])
    settings    = load_settings()
    ontology    = _load_ontology()
    neo4j       = _get_neo4j(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)

    fast_llm  = NvidiaLLMClient(api_key=api_key, model=provider["fast"], base_url=provider["base_url"])
    synth_llm = NvidiaLLMClient(api_key=api_key, model=synth_model, base_url=provider["base_url"])

    # Fresh ToolCache every question — no caching carries over between turns,
    # so one question's results can never leak into another's.
    executor = Executor(
        orchestrator    = Orchestrator(fast_llm),
        cypher_builder  = CypherBuilder(fast_llm),
        validator       = CypherValidatorAgent(fast_llm, ontology),
        synthesizer     = Synthesizer(synth_llm),
        neo4j_client    = neo4j,
        normalizer      = Normalizer(fast_llm),
        ontology        = ontology,
        tool_cache      = ToolCache(),
    )

    try:
        plan_placeholder = st.empty()
        steps_container  = st.container()

        # Steps re-fire (once on execution, once again after validation) for the
        # SAME StepRecord object — key placeholders on object identity so each
        # step renders as one card that updates in place, not duplicates.
        step_placeholders: dict[int, tuple[int, "st.delta_generator.DeltaGenerator", list]] = {}
        step_counter = {"n": 0}
        pending_attempts: list[dict] = []

        def on_stage(stage: str, data):
            if stage == "planned":
                with plan_placeholder.expander(
                    f"Plan — goal: {data.end_goal} · {len(data.steps)} step(s)",
                    expanded=False,
                ):
                    for j, step in enumerate(data.steps, 1):
                        dep = f" (depends on {step.depends_on})" if step.depends_on else ""
                        st.caption(f"Step {j}: {step.tool}{dep}")

        def on_attempt(tool_name, attempt_num, cypher, rows, error):
            pending_attempts.append({
                "attempt": attempt_num, "cypher": cypher, "rows": rows, "error": error,
            })

        def on_step(record):
            key = id(record)
            if key not in step_placeholders:
                step_counter["n"] += 1
                attempts_snapshot = list(pending_attempts)
                pending_attempts.clear()
                with steps_container:
                    placeholder = st.empty()
                step_placeholders[key] = (step_counter["n"], placeholder, attempts_snapshot)
            n, placeholder, attempts_snapshot = step_placeholders[key]
            with placeholder.container():
                _render_step_card(n, record, attempts_snapshot)

        plan, scratchpad, synthesis = executor.run(
            question,
            on_step=on_step, on_stage=on_stage, on_attempt=on_attempt,
        )

        if scratchpad.gaps:
            with st.expander(f"Gaps ({len(scratchpad.gaps)})", expanded=False):
                for g in scratchpad.gaps:
                    st.caption(f"• {g['tool']}: {g['reason']}")

        # ── Synthesis ──────────────────────────────────────────────────────
        st.divider()
        st.markdown("#### Final answer")
        _render_synthesis(synthesis.__dict__)

        # ── LLM call log ──────────────────────────────────────────────────
        all_calls = fast_llm.call_log + synth_llm.call_log
        if all_calls:
            with st.expander(f"LLM calls ({len(all_calls)})", expanded=False):
                for i, call in enumerate(all_calls, 1):
                    st.markdown(f"**{i}. {call.label}**")
                    for msg in call.messages:
                        st.markdown(f"*{msg['role'].upper()}*")
                        st.code(msg["content"], language="markdown")
                    st.markdown("*RESPONSE*")
                    st.code(call.response, language="markdown")
                    if call.exec_result is not None:
                        er = call.exec_result
                        st.markdown("*CYPHER EXECUTION RESULT*")
                        if er["error"]:
                            st.error(er["error"])
                        else:
                            st.success(f"{er['row_count']} row(s)")
                            if er["sample"]:
                                st.json(er["sample"])
                    if i < len(all_calls):
                        st.divider()

        ctx.add_assistant(synthesis.direct_answer, synthesis.__dict__)
        st.session_state.messages.append({
            "role": "assistant",
            "content": synthesis.direct_answer,
            "synthesis": synthesis.__dict__,
        })

    except Exception as exc:
        import traceback
        st.error(f"**Pipeline error:** {exc}")
        st.caption(traceback.format_exc())
        st.session_state.messages.append({"role": "assistant", "content": f"Error: {exc}"})


def _inline_params(cypher: str, params: dict) -> str:
    """Replace $param placeholders with their actual values for display only."""
    import re
    def _fmt(v) -> str:
        if isinstance(v, str):
            return f'"{v}"'
        if isinstance(v, bool):
            return "true" if v else "false"
        return str(v)
    for key in sorted(params, key=len, reverse=True):  # longest first avoids partial matches
        cypher = re.sub(rf"\${re.escape(key)}\b", _fmt(params[key]), cypher)
    # Mark any remaining $params that had no value as ⚠$param so they're visible
    cypher = re.sub(r"(\$[A-Za-z_][A-Za-z0-9_]*)", r"⚠\1", cypher)
    return cypher


def _render_step_card(n: int, record, attempts: list[dict]) -> None:
    """One step of the pipeline, in the order the user actually wants to read it:
    1. which tool was picked and with what parameters
    2. the Cypher that was built from those parameters and executed
    3. the reasoning (relevance check) and the resulting rows
    """
    cached = record.cypher == "[cached]"
    ok     = not record.error

    with st.container(border=True):
        header = f"Step {n} · `{record.tool}`"
        if cached:
            header += "  🗂️ *served from cache*"
        st.markdown(f"##### {header}")

        st.markdown("**1 · Tool selected & parameters sent**")
        st.json(record.params or {}, expanded=True)

        st.markdown("**2 · Query built & executed**")
        if len(attempts) > 1:
            with st.expander(f"{len(attempts)} attempt(s) — showing retries before success/failure", expanded=False):
                for a in attempts[:-1]:
                    status = f"❌ {a['error']}" if a["error"] else f"✓ {len(a['rows'] or [])} row(s)"
                    st.caption(f"Attempt {a['attempt']}: {status}")
                    if a["cypher"]:
                        st.code(a["cypher"], language="cypher")
        if cached:
            st.caption("Identical (tool, params) seen earlier this turn — no new query was run.")
        elif record.cypher:
            st.code(_inline_params(record.cypher, record.params), language="cypher")
        else:
            st.caption("No query was executed for this step.")

        st.markdown("**3 · Reasoning & results**")
        if record.error:
            st.error(record.error)
        elif not record.rows:
            st.warning("No rows returned.")
        else:
            st.success(f"{record.row_count} row(s) returned")
            if record.validation_score is not None:
                score = record.validation_score
                color = "#1a7f3c" if score >= 0.7 else "#c0392b"
                st.markdown(
                    f"<span style='color:{color};font-size:0.85rem'>"
                    f"Relevance check: {score:.0%} — {record.validation_reason}</span>",
                    unsafe_allow_html=True,
                )
            st.dataframe(record.rows[:10], width="stretch")


def _render_synthesis(s: dict) -> None:
    direct = s.get("direct_answer", "")
    icon   = "yes" if direct.lower().startswith("yes") else (
             "no"  if direct.lower().startswith("no")  else "info")
    color  = {"yes": "#1a7f3c", "no": "#c0392b", "info": "#2c6fad"}.get(icon, "#2c6fad")
    marker = {"yes": "YES", "no": "NO", "info": "ANSWER"}.get(icon, "ANSWER")

    st.markdown(
        f'<div style="background:{color}18;border-left:4px solid {color};'
        f'padding:0.75rem 1rem;border-radius:4px;margin-bottom:0.75rem">'
        f'<span style="color:{color};font-weight:700">{marker}</span>&nbsp;&nbsp;{direct}</div>',
        unsafe_allow_html=True,
    )
    if s.get("evidence"):
        with st.expander("Evidence", expanded=True):
            for line in s["evidence"].split("\n"):
                line = line.strip().lstrip("-").strip()
                if line:
                    st.markdown(f"- {line}")
    if s.get("regulatory_basis") and s["regulatory_basis"].upper() != "N/A":
        with st.expander("Regulatory basis", expanded=False):
            st.markdown(s["regulatory_basis"])
    if s.get("caveats") and s["caveats"].lower() != "none.":
        with st.expander("Caveats", expanded=False):
            st.markdown(s["caveats"])
    if s.get("conclusion"):
        st.info(s["conclusion"])


if __name__ == "__main__":
    main()
