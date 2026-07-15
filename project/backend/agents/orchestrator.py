"""Agent 1 — Orchestrator.

Reads the user's raw question directly and plans ONE ordered sequence of tool
calls — no alternative/fallback strategies. Knows graph structure and tool
registry. Never writes Cypher and never extracts structured parameters — each
CypherBuilder call re-reads the question itself. Runs once per question.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.llm.parser import parse_json_object
from backend.registry.tool_registry import tools_prompt_block


_RELATIONSHIPS = """\
KEY RELATIONSHIPS (from → to):
  CensusTract    -[:IN_COUNTY]->   County
  CensusTract    -[:IN_METRO]->    MetroArea
  County         -[:IN_STATE]->    State
  County         -[:HAS_MSA_AMI]-> Section8AMILimit
  QCTDesignation   -[:APPLIES_TO]-> CensusTract
  SDDADesignation  -[:APPLIES_TO]-> MetroArea
  NMDDADesignation -[:APPLIES_TO]-> County
  LenderBehaviorRisk -[:APPLIES_TO]-> MetroArea
"""

_TOOLS_BLOCK = tools_prompt_block()

_SYSTEM = """\
You plan tool execution for a LIHTC knowledge graph agent, reading the user's
raw question directly. Produce ONE sequence of tool calls — the most direct
path to answering the question. Do not propose alternative or fallback
strategies; pick the single best one.

You do NOT extract parameters or write Cypher — you only decide WHICH tools
to call and in WHAT order. Each tool call will independently re-read the
question and extract whatever values it needs.

""" + _RELATIONSHIPS + """
AVAILABLE TOOLS:
""" + _TOOLS_BLOCK + """

RULES:
  - The first step should be the single most direct tool for the question
    (fewest hops, most specific — e.g. check_qct for a named tract's QCT
    status, not a broader search_tracts call).
  - Add a second (or later) step ONLY when the question genuinely requires a
    prior step's result first (e.g. resolving a metro area before checking its
    HMDA risk) — do not add exploratory or "just in case" steps.
  - depends_on: "step_1", "step_2", etc. (1-based index of prior step) or null.
    Use this when a later step needs a value (e.g. cbsa_code) discovered by an
    earlier step's result, rather than something stated directly in the question.
  - MISSING-VALUE CHECK (do this before picking the first tool): does the real
    tool you need require a county_fips/state_fips/cbsa_code, but the question
    only NAMES that place (e.g. "Cook County IL") rather than stating its digit
    code directly? A code can NEVER be reliably guessed from a name — it must
    come from the graph. If so, plan resolve_geography as step_1 to look up the
    code, then the real tool as step_2 with depends_on: "step_1" — do not send
    the real tool straight at a named-but-unresolved place. This does NOT apply
    when the question already gives the digit code, or names a specific tract
    (fips_code always comes from the question text itself, never resolved).
  - Every "tool" value in every step MUST be copied EXACTLY from the name of one
    of the tools in the AVAILABLE TOOLS list above — do not invent a new tool name
    (e.g. filter_by_county, filter_tracts, get_county_info do NOT exist).
  - Most tools (check_qct, check_dda, get_ami_limits, get_hmda_risk) work for ONE
    year at a time. If the question asks about MULTIPLE discrete years for the
    same entity (e.g. "in 2025 and 2024", "in 2025 but not 2024"), add ONE step
    PER YEAR using that tool — do not try to answer a multi-year question with a
    single call to a single-year tool. (get_hmda_trend is the exception — it
    natively takes a start_year/end_year range for trend questions.)
  - clarification_needed: true ONLY if a specific tract/county/metro is REQUIRED
    to answer the question but is completely absent from the question itself
    (e.g. "is this tract eligible?" with no tract identifier anywhere in it).
    Do not ask for clarification just because a year or optional detail is
    missing — default those sensibly instead.

IMPORTANT: Output ONLY the JSON object below. No prose, no markdown, no explanation.
{
  "end_goal": "one short phrase describing what the underwriter wants to determine",
  "clarification_needed": false,
  "clarification_question": "",
  "steps": [{"tool": "tool_name", "depends_on": null}]
}
"""


@dataclass
class PlanStep:
    tool: str
    depends_on: str | None = None


@dataclass
class Plan:
    end_goal: str
    steps: list[PlanStep]
    clarification_needed: bool = False
    clarification_question: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Plan":
        return cls(
            end_goal=d.get("end_goal", ""),
            clarification_needed=bool(d.get("clarification_needed")),
            clarification_question=d.get("clarification_question", ""),
            steps=[
                PlanStep(tool=s["tool"], depends_on=s.get("depends_on"))
                for s in d.get("steps", [])
            ],
        )


class Orchestrator:
    def __init__(self, llm_client) -> None:
        self.llm = llm_client

    def plan(self, question: str) -> Plan:
        """Plan paths for this question alone — no conversation history is
        used, so each question is planned independently and can't get
        cross-contaminated with an earlier turn's location/intent."""
        user_content = f"QUESTION:\n{question}"

        raw = self.llm.complete(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": user_content},
            ],
            label="Orchestrator",
        )
        data = parse_json_object(raw)
        return Plan.from_dict(data)
