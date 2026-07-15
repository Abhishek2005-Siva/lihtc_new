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
from backend.cache.geo_cache import GeoCache
from backend.utils.normalizer import Normalizer
from backend.pipeline.executor import Executor
from backend.cache.tool_cache import ToolCache
from backend.agents.cypher_builder import CypherBuilder
from backend.agents.param_extractor import ParamExtractor
from backend.agents.param_selector import ParamSelector
from backend.agents.path_planner import PathPlanner
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

def main() -> None:
    st.set_page_config(page_title="LIHTC Agent", page_icon="🏠", layout="wide")
    _render_sidebar()
    _render_chat()


def _render_sidebar() -> None:
    with st.sidebar:
        st.title("LIHTC Agent")
        st.divider()

        api_key = st.text_input("NVIDIA API Key", type="password",
                                value=st.session_state.get("api_key", ""))
        if api_key:
            st.session_state["api_key"] = api_key

        st.session_state["synth_model"] = st.selectbox(
            "Synthesizer model",
            ["meta/llama-3.1-70b-instruct", "meta/llama-3.1-8b-instruct",
             "nvidia/llama-3.1-nemotron-70b-instruct"],
            index=0,
        )

        if st.button("Clear conversation"):
            for key in ("messages", "ctx", "geo_cache", "tool_cache"):
                st.session_state.pop(key, None)
            st.rerun()

        st.divider()
        st.caption(f"Neo4j: {load_settings().neo4j_uri}")


def _render_chat() -> None:
    if "messages"   not in st.session_state: st.session_state.messages   = []
    if "ctx"        not in st.session_state: st.session_state.ctx        = ConversationContext()
    if "geo_cache"  not in st.session_state: st.session_state.geo_cache  = GeoCache()
    if "tool_cache" not in st.session_state: st.session_state.tool_cache = ToolCache()

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
        st.toast("Add your NVIDIA API key in the sidebar first.")
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
        if cols[i % 2].button(ex, use_container_width=True, key=f"ex_{i}"):
            st.session_state.messages.append({"role": "user", "content": ex})
            st.rerun()


def _run_pipeline(question: str, ctx: ConversationContext) -> None:
    api_key     = st.session_state["api_key"]
    synth_model = st.session_state.get("synth_model", "meta/llama-3.1-70b-instruct")
    settings    = load_settings()
    ontology    = _load_ontology()
    neo4j       = _get_neo4j(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)

    fast_llm  = NvidiaLLMClient(api_key=api_key, model="meta/llama-3.1-8b-instruct")
    synth_llm = NvidiaLLMClient(api_key=api_key, model=synth_model)

    executor = Executor(
        param_extractor = ParamExtractor(fast_llm, selector=ParamSelector(fast_llm)),
        path_planner    = PathPlanner(fast_llm),
        cypher_builder  = CypherBuilder(fast_llm),
        validator       = CypherValidatorAgent(fast_llm, ontology),
        synthesizer     = Synthesizer(synth_llm),
        neo4j_client    = neo4j,
        normalizer      = Normalizer(fast_llm),
        ontology        = ontology,
        geo_cache       = st.session_state.geo_cache,
        tool_cache      = st.session_state.tool_cache,
    )

    history = [
        {"role": m.role, "question" if m.role == "user" else "answer": m.content}
        for m in ctx.messages[-6:]
    ]

    try:
        params_placeholder = st.empty()
        plan_placeholder   = st.empty()
        trace_container    = st.container()
        synth_placeholder  = st.empty()

        stage_status = st.status("Running pipeline...", expanded=True)

        def on_stage(stage: str, data):
            with stage_status:
                if stage == "normalize":
                    st.write("Normalizing question...")
                elif stage == "extract":
                    st.write("Extracting parameters...")
                elif stage == "extracted":
                    st.write(f"Parameters extracted — goal: **{data.end_goal}**, mode: **{data.query_mode}**")
                    with params_placeholder.expander("Extracted parameters", expanded=False):
                        st.json(data.to_dict())
                        if data.null_keys:
                            st.caption(
                                "Stripped null keys: "
                                + ", ".join(f"`{k}`" for k in sorted(data.null_keys))
                            )
                elif stage == "plan":
                    st.write("Planning execution paths...")
                elif stage == "planned":
                    st.write(f"Plan ready — {len(data.paths)} path(s), selected: **{data.selected_path}**")
                    with plan_placeholder.expander(
                        f"Execution plan — {len(data.paths)} paths · selected: {data.selected_path}",
                        expanded=False,
                    ):
                        for path in data.paths:
                            label = "✓ " if path.id == data.selected_path else "  "
                            st.markdown(f"**{label}{path.id}** — {path.description}")
                            for j, step in enumerate(path.steps, 1):
                                st.caption(f"  Step {j}: {step.tool}({step.params})")
                elif stage == "synthesize":
                    st.write("Synthesizing answer...")

        def on_attempt(tool_name, attempt_num, cypher, rows, error):
            with trace_container:
                if error:
                    st.caption(f"↳ {tool_name} attempt {attempt_num}: ❌ {error}")
                else:
                    st.caption(f"↳ {tool_name} attempt {attempt_num}: ✓ {len(rows or [])} row(s)")
                if cypher:
                    with st.expander(f"Cypher (attempt {attempt_num})", expanded=False):
                        st.code(cypher, language="cypher")

        def on_step(record):
            badge_color = "#c0392b" if record.error else "#1a7f3c"
            badge_text  = "ERROR" if record.error else f"{record.row_count} row(s)"
            cached      = " [cached]" if record.cypher == "[cached]" else ""
            with trace_container:
                st.markdown(
                    f"**{record.tool}**{cached} "
                    f"<span style='background:{badge_color}20;color:{badge_color};"
                    f"padding:2px 8px;border-radius:4px;font-size:0.8rem'>{badge_text}</span>",
                    unsafe_allow_html=True,
                )
                col1, col2 = st.columns([1, 2])
                with col1:
                    st.json(record.params)
                if record.cypher and record.cypher != "[cached]":
                    with col2:
                        st.code(_inline_params(record.cypher, record.params), language="cypher")
                if record.error:
                    st.error(record.error)
                elif record.rows:
                    st.json(record.rows[:5])
                    if record.validation_score is not None:
                        score = record.validation_score
                        color = "#1a7f3c" if score >= 0.7 else "#c0392b"
                        st.markdown(
                            f"<span style='color:{color};font-size:0.8rem'>"
                            f"Relevance score: {score:.0%} — {record.validation_reason}</span>",
                            unsafe_allow_html=True,
                        )
                else:
                    st.warning("No rows returned.")
                st.divider()
            with stage_status:
                st.write(f"✓ {record.tool} — {badge_text}{cached}")

        params, plan, scratchpad, synthesis = executor.run(
            question, history=history or None,
            on_step=on_step, on_stage=on_stage, on_attempt=on_attempt,
        )
        stage_status.update(
            label=f"Done — {len(scratchpad.steps_taken)} step(s), {len(scratchpad.gaps)} gap(s)",
            state="complete", expanded=False,
        )

        if scratchpad.gaps:
            with trace_container:
                st.markdown("**Gaps:**")
                for g in scratchpad.gaps:
                    st.caption(f"• {g['tool']}: {g['reason']}")

        # ── Synthesis ──────────────────────────────────────────────────────
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
