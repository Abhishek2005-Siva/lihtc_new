"""Agent 2 — Path Planner.

Given extracted parameters and end goal, plans 3 ordered execution paths.
Knows graph structure and tool registry. Never writes Cypher.
Runs once per question after Agent 1.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

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
You plan execution paths for a LIHTC knowledge graph agent.

Given extracted parameters and an end goal, produce exactly 3 DISTINCT paths.
Each path is a sequence of tool calls that traverses the graph differently.

""" + _RELATIONSHIPS + """
AVAILABLE TOOLS (* = required param, [...] = optional):
""" + _TOOLS_BLOCK + """

WHAT "DIFFERENT PATHS" MEANS:
  Paths must differ in graph traversal strategy, not just parameter values or limits.
  Examples of genuinely different paths for the same goal:
    path_1: search_tracts(county_fips) — filter directly on CensusTract by county
    path_2: search_tracts(state_fips) — broader search at state level, filter results
    path_3: check_qct(fips_code) — if a specific tract is already known from context
  NEVER produce paths that only differ by limit value. That is NOT a different path.

RULES:
  - path_1: most direct given the available params (fewest hops, most specific)
  - path_2: different entry point or different set of tools for the same goal
  - path_3: broadest fallback (e.g. state-level search, different node type as anchor)
  - 5-digit FIPS → county_fips. 11-digit FIPS → fips_code. Never mix them.
  - Never pass null as a param value. Omit params that have no value.
  - depends_on: "step_1", "step_2", etc. (1-based index of prior step) or null.
  - Every "tool" value in every step MUST be copied EXACTLY from the AVAILABLE TOOLS
    list above — one of: check_qct, check_dda, get_ami_limits, get_hmda_risk,
    get_hmda_trend, search_tracts, search_dda_areas, search_hmda_risk.
    Do NOT invent a new tool name (e.g. filter_by_county, filter_tracts,
    get_county_info do NOT exist). If you need a filter, put it in the params of
    an existing tool call instead — do not add an extra step with a made-up tool.

IMPORTANT: Output ONLY the JSON object below. No prose, no markdown, no explanation.
{
  "end_goal": "...",
  "paths": [
    {
      "id": "path_1",
      "description": "one sentence describing the traversal strategy",
      "steps": [{"tool": "tool_name", "params": {"param": "value"}, "depends_on": null}],
      "feasibility": "high"
    },
    {
      "id": "path_2",
      "description": "...",
      "steps": [{"tool": "tool_name", "params": {"param": "value"}, "depends_on": null}],
      "feasibility": "medium"
    },
    {
      "id": "path_3",
      "description": "...",
      "steps": [{"tool": "tool_name", "params": {"param": "value"}, "depends_on": null}],
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
    params: dict[str, Any]
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
                PlanStep(
                    tool=s["tool"],
                    params=s.get("params", {}),
                    depends_on=s.get("depends_on"),
                )
                for s in d.get("steps", [])
            ],
        )


@dataclass
class Plan:
    end_goal: str
    paths: list[ExecutionPath]
    selected_path: str
    selection_reason: str

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
            paths=[ExecutionPath.from_dict(p) for p in d.get("paths", [])],
        )


class PathPlanner:
    def __init__(self, llm_client) -> None:
        self.llm = llm_client

    def plan(
        self,
        params: dict[str, Any],
        known_facts: dict[str, Any] | None = None,
    ) -> Plan:
        user_content = f"EXTRACTED PARAMETERS:\n{json.dumps(params, indent=2)}"
        if known_facts:
            user_content += f"\n\nKNOWN FACTS FROM CACHE:\n{json.dumps(known_facts, indent=2)}"

        raw = self.llm.complete(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": user_content},
            ],
            label="PathPlanner",
        )
        data = parse_json_object(raw)
        return Plan.from_dict(data)
