"""Orchestrator — drives the ReAct loop.

Receives the current scratchpad and outputs one of:
  THOUGHT: ...
  ACTION: <tool_name>
  PARAMS: {...}

or:
  THOUGHT: ...
  ACTION: FINISH
  PARAMS: {}

This agent reasons; it does NOT write Cypher.
"""
from __future__ import annotations

import json
import re
from typing import Any

from scripts.agent.shared.scratchpad import Scratchpad
from scripts.agent.shared.tool_registry import tools_prompt_block
from scripts.agent.shared.parser import parse_json_object


_SYSTEM = """\
You are a LIHTC underwriting agent driving a ReAct loop over a Neo4j knowledge graph.
Your job: decide what information is still needed and which tool to call next.
A separate system handles Cypher generation — you only pick tools and parameters.

RULES:
- Reason strictly from the scratchpad. Do not assume facts not already observed.
- If a fact is already in known_facts, do NOT fetch it again.
- Call get_tract_context first whenever a new fips_code appears — it populates
  county_fips, cbsa_code, and is_metro_tract that other tools need.
- FINISH only when you have enough data to answer the question fully.

AVAILABLE TOOLS:
{tool_list}

Respond in EXACTLY this format — no markdown, no extra text:

THOUGHT: [one paragraph: what you know, what is still missing, why you pick this tool]
ACTION: [tool_name]
PARAMS: {{"param": "value"}}

To finish:

THOUGHT: [why you have enough to answer]
ACTION: FINISH
PARAMS: {{}}
"""

_USER = "SCRATCHPAD:\n{scratchpad_json}"

_THOUGHT_RE = re.compile(r"THOUGHT\s*:\s*(.+?)(?=ACTION\s*:)", re.DOTALL | re.IGNORECASE)
_ACTION_RE  = re.compile(r"ACTION\s*:\s*([A-Za-z_]+)", re.IGNORECASE)
_PARAMS_RE  = re.compile(r"PARAMS\s*:\s*(\{.*\})", re.DOTALL | re.IGNORECASE)


def _build_messages(scratchpad: Scratchpad) -> list[dict]:
    system = _SYSTEM.format(tool_list=tools_prompt_block())
    user = _USER.format(
        scratchpad_json=json.dumps(scratchpad.to_prompt_dict(), indent=2, default=str)
    )
    return [
        {"role": "system", "content": system},
        {"role": "user",   "content": user},
    ]


def parse_response(text: str) -> tuple[str, str, dict]:
    """Parse THOUGHT / ACTION / PARAMS from the LLM response.

    Returns (thought, action, params). Raises ValueError if no ACTION found.
    """
    thought_m = _THOUGHT_RE.search(text)
    action_m  = _ACTION_RE.search(text)
    params_m  = _PARAMS_RE.search(text)

    if not action_m:
        raise ValueError(f"No ACTION in orchestrator response: {text[:300]}")

    thought = thought_m.group(1).strip() if thought_m else ""
    action  = action_m.group(1).strip()
    params: dict[str, Any] = {}
    if params_m:
        try:
            params = parse_json_object(params_m.group(1))
        except Exception:
            params = {}

    return thought, action, params


class ReactAgent:
    """Orchestrator LLM — drives the THOUGHT → ACTION → PARAMS step."""

    def __init__(self, llm_client) -> None:
        self.llm = llm_client

    def think(self, scratchpad: Scratchpad, step_num: int, max_steps: int) -> tuple[str, str, dict]:
        """Ask the LLM what to do next. Returns (thought, action, params)."""
        messages = _build_messages(scratchpad)
        raw = self.llm.complete(
            messages,
            label=f"Orchestrator THOUGHT {step_num}/{max_steps}",
        )
        return parse_response(raw)
