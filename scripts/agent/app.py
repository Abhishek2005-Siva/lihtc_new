"""LIHTC Graph Agent — Streamlit UI."""
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

# Add repo root to path so `scripts` package is importable
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.agent.agents.cypher_builder import CypherBuilder
from scripts.agent.agents.cypher_validator import CypherValidator
from scripts.agent.agents.react_agent import ReactAgent
from scripts.agent.agents.synthesizer import Synthesizer, SynthesisResult
from scripts.agent.context import ConversationContext
from scripts.agent.normalizer import Normalizer
from scripts.agent.react_loop import ReactLoop
from scripts.agent.shared.config import load_settings
from scripts.agent.shared.llm_client import NvidiaLLMClient
from scripts.agent.shared.neo4j_client import Neo4jClient
from scripts.agent.shared.ontology import GraphOntology

_ONTOLOGY_PATH = _REPO_ROOT / "project" / "data" / "ontology" / "lihtc_ontology.json"


# ---------------------------------------------------------------------------
# Cached resources
# ---------------------------------------------------------------------------

@st.cache_resource(show_spinner=False, ttl=0)
def _load_ontology() -> GraphOntology:
    data = json.loads(_ONTOLOGY_PATH.read_text(encoding="utf-8"))
    known = {f for f in GraphOntology.__dataclass_fields__}
    return GraphOntology(**{k: v for k, v in data.items() if k in known})


@st.cache_resource(show_spinner=False)
def _get_neo4j(uri: str, user: str, pwd: str) -> Neo4jClient:
    return Neo4jClient(uri, user, pwd)


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def render() -> None:
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
            st.session_state.pop("ctx", None)
            st.session_state.pop("messages", None)
            st.rerun()

        st.divider()
        st.caption("Neo4j: " + load_settings().neo4j_uri)


def _render_chat() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "ctx" not in st.session_state:
        st.session_state.ctx = ConversationContext()

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
        "<p style='color:#888'>Ask about QCT designations, AMI limits, DDA areas, or lending risk.</p>"
        "</div>",
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
    api_key = st.session_state["api_key"]
    synth_model = st.session_state.get("synth_model", "meta/llama-3.1-70b-instruct")
    settings = load_settings()

    fast_llm  = NvidiaLLMClient(api_key=api_key, model="meta/llama-3.1-8b-instruct")
    synth_llm = NvidiaLLMClient(api_key=api_key, model=synth_model)
    ontology  = _load_ontology()
    neo4j     = _get_neo4j(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)

    loop = ReactLoop(
        orchestrator   = ReactAgent(fast_llm),
        cypher_builder = CypherBuilder(fast_llm),
        validator      = CypherValidator(ontology),
        synthesizer    = Synthesizer(synth_llm),
        neo4j_client   = neo4j,
        normalizer     = Normalizer(fast_llm),
        ontology       = ontology,
    )

    try:
        seed_facts = ctx.seed_facts_for(question)
        with st.spinner("Running ReAct loop..."):
            norm, scratchpad, synthesis = loop.run(
                question=question,
                seed_facts=seed_facts,
            )

        # ── Normalisation ──────────────────────────────────────────────────
        if norm.changes:
            with st.expander(f"Normalisation ({len(norm.changes)} substitution(s))", expanded=False):
                for c in norm.changes:
                    st.caption(f"• {c}")

        # ── ReAct trace ────────────────────────────────────────────────────
        tool_steps = [s for s in scratchpad.steps_taken if s.action != "[unparseable]"]
        data_steps = [s for s in tool_steps if s.action != "FINISH"]
        term_label = {
            "agent_finished":     "agent finished",
            "max_steps":          "max steps reached",
            "loop_detected":      "loop detected",
            "consecutive_errors": "consecutive errors",
        }.get(scratchpad.termination_reason, scratchpad.termination_reason)

        with st.expander(
            f"ReAct trace — {len(data_steps)} tool call(s) · {term_label}",
            expanded=True,
        ):
            for i, step in enumerate(tool_steps, 1):
                is_finish = step.action == "FINISH"
                rows = step.observation.get("rows", [])
                err  = step.observation.get("error")

                st.markdown("---")
                if is_finish:
                    st.markdown("#### FINISH")
                else:
                    badge_color = "#c0392b" if err else "#1a7f3c"
                    badge_text  = "ERROR" if err else f"{len(rows)} row(s)"
                    st.markdown(
                        f"#### Step {i} &nbsp; `{step.action}` "
                        f"<span style='background:{badge_color}20;color:{badge_color};"
                        f"padding:2px 8px;border-radius:4px;font-size:0.8rem'>{badge_text}</span>",
                        unsafe_allow_html=True,
                    )

                st.markdown(
                    f"<div style='background:#f8f9fa;border-left:3px solid #6c757d;"
                    f"padding:0.5rem 0.75rem;border-radius:4px;margin:0.4rem 0;"
                    f"font-style:italic;color:#444'>"
                    f"<b>Thought:</b> {step.thought}</div>",
                    unsafe_allow_html=True,
                )

                if is_finish:
                    continue

                col1, col2 = st.columns([1, 2])
                with col1:
                    st.markdown("**Params**")
                    st.json(step.params)
                if step.cypher:
                    with col2:
                        st.markdown("**Cypher**")
                        st.code(_inline_params(step.cypher, step.params), language="cypher")

                if err:
                    st.error(f"**Observation:** {err}")
                elif rows:
                    st.markdown(f"**Observation** — {len(rows)} row(s):")
                    st.json(rows)
                else:
                    st.warning("**Observation:** No rows returned.")

        # ── Synthesis ──────────────────────────────────────────────────────
        _render_synthesis(synthesis.__dict__)

        # ── LLM call log ──────────────────────────────────────────────────
        all_calls = fast_llm.call_log + synth_llm.call_log
        if all_calls:
            with st.expander(f"LLM call log ({len(all_calls)} calls)", expanded=False):
                for i, call in enumerate(all_calls, 1):
                    st.markdown(f"**Call {i} — {call.label}**")
                    for msg in call.messages:
                        st.markdown(f"*{msg['role'].upper()}*")
                        st.code(msg["content"], language="markdown")
                    st.markdown("*RESPONSE*")
                    st.code(call.response, language="markdown")
                    if i < len(all_calls):
                        st.divider()

        # Persist facts for next turn
        ctx.update_facts(scratchpad.known_facts)
        ctx.add_assistant(synthesis.direct_answer, synthesis.__dict__)
        st.session_state.messages.append({
            "role": "assistant",
            "content": synthesis.direct_answer,
            "synthesis": synthesis.__dict__,
        })

    except Exception as exc:
        import traceback
        st.error(f"**Agent error:** {exc}")
        st.caption(traceback.format_exc())
        st.session_state.messages.append({"role": "assistant", "content": f"Error: {exc}"})


def _render_synthesis(s: dict) -> None:
    direct = s.get("direct_answer", "")
    icon   = "yes" if direct.lower().startswith("yes") else (
             "no"  if direct.lower().startswith("no")  else "info")
    color  = {"yes": "#1a7f3c", "no": "#c0392b", "info": "#2c6fad"}.get(icon, "#2c6fad")
    marker = {"yes": "YES",     "no": "NO",      "info": "ANSWER"}.get(icon, "ANSWER")

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


def _inline_params(cypher: str, params: dict) -> str:
    result = cypher
    for key, val in sorted(params.items(), key=lambda x: -len(x[0])):
        replacement = f'"{val}"' if isinstance(val, str) else \
                      str(val).lower() if isinstance(val, bool) else str(val)
        result = result.replace(f"${key}", replacement)
    return result


if __name__ == "__main__":
    render()
