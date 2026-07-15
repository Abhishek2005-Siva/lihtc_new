"""Agent 1 — Path Planner.

Reads the user's raw question directly and plans 3 ordered execution paths.
Knows graph structure and tool registry. Never writes Cypher and never
extracts structured parameters — each CypherBuilder call re-reads the question
itself. Runs once per question.
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
You plan execution paths for a LIHTC knowledge graph agent, reading the user's
raw question directly. Produce exactly 3 DISTINCT paths.
Each path is a sequence of tool calls that traverses the graph differently.

You do NOT extract parameters or write Cypher — you only decide WHICH tools
to call and in WHAT order. Each tool call will independently re-read the
question and extract whatever values it needs.

""" + _RELATIONSHIPS + """
AVAILABLE TOOLS:
""" + _TOOLS_BLOCK + """

WHAT "DIFFERENT PATHS" MEANS:
  Paths must differ in graph traversal strategy, not just wording.
  Examples of genuinely different paths for the same goal:
    path_1: search_tracts — filter directly on CensusTract by county
    path_2: search_tracts — broader search at state level, filter results
    path_3: check_qct — if a specific tract is already named in the question
  NEVER produce paths that only differ by limit value. That is NOT a different path.

RULES:
  - path_1: most direct given the question (fewest hops, most specific)
  - path_2: different entry point or different set of tools for the same goal
  - path_3: broadest fallback (e.g. state-level search, different node type as anchor)
  - depends_on: "step_1", "step_2", etc. (1-based index of prior step) or null.
    Use this when a later step needs a value (e.g. cbsa_code) discovered by an
    earlier step's result, rather than something stated directly in the question.
  - Every "tool" value in every step MUST be copied EXACTLY from the AVAILABLE TOOLS
    list above — one of: check_qct, check_dda, get_ami_limits, get_hmda_risk,
    get_hmda_trend, search_tracts, search_dda_areas, search_hmda_risk.
    Do NOT invent a new tool name (e.g. filter_by_county, filter_tracts,
    get_county_info do NOT exist).
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
  "paths": [
    {
      "id": "path_1",
      "description": "one sentence describing the traversal strategy",
      "steps": [{"tool": "tool_name", "depends_on": null}],
      "feasibility": "high"
    },
    {
      "id": "path_2",
      "description": "...",
      "steps": [{"tool": "tool_name", "depends_on": null}],
      "feasibility": "medium"
    },
    {
      "id": "path_3",
      "description": "...",
      "steps": [{"tool": "tool_name", "depends_on": null}],
      "feasibility": "low"
    }
  ],
  "selected_path": "path_1",
  "selection_reason": "one sentence"
}
"""


@dataclass
class PlanStep:
    tool: str
    depends_on: str | None = None


@dataclass
class ExecutionPath:
    id: str
    description: str
    steps: list[PlanStep]
    feasibility: str = "high"

    @classmethod
    def from_dict(cls, d: dict) -> "ExecutionPath":
        return cls(
            id=d["id"],
            description=d.get("description", ""),
            feasibility=d.get("feasibility", "high"),
            steps=[
                PlanStep(tool=s["tool"], depends_on=s.get("depends_on"))
                for s in d.get("steps", [])
            ],
        )


@dataclass
class Plan:
    end_goal: str
    paths: list[ExecutionPath]
    selected_path: str
    selection_reason: str
    clarification_needed: bool = False
    clarification_question: str = ""

    def ordered_paths(self) -> list[ExecutionPath]:
        """Return paths with selected_path first, then the rest."""
        selected = [p for p in self.paths if p.id == self.selected_path]
        others   = [p for p in self.paths if p.id != self.selected_path]
        return selected + others

    @classmethod
    def from_dict(cls, d: dict) -> "Plan":
        return cls(
            end_goal=d.get("end_goal", ""),
            selected_path=d.get("selected_path", "path_1"),
            selection_reason=d.get("selection_reason", ""),
            clarification_needed=bool(d.get("clarification_needed")),
            clarification_question=d.get("clarification_question", ""),
            paths=[ExecutionPath.from_dict(p) for p in d.get("paths", [])],
        )


class PathPlanner:
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
            label="PathPlanner",
        )
        data = parse_json_object(raw)
        return Plan.from_dict(data)
