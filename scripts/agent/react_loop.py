"""ReAct loop — connects all components.

Step 1  Normalizer runs on raw question
Step 2  Orchestrator THINKs and picks a tool + params
Step 3  Cypher Builder translates tool call → Cypher
Step 4  Cypher Validator checks the Cypher
        → if invalid: error returned as observation, continue from Step 2
Step 5  Neo4j executes the Cypher
        → if empty: Orchestrator observes and decides retry or soft gap
        → if data: stored in scratchpad, facts extracted
Step 6  Orchestrator reads new observation + full scratchpad → next action
Step 7  Repeat Steps 2-6 until FINISH or termination condition
Step 8  Synthesizer reads complete scratchpad → structured answer
"""
from __future__ import annotations

import json
from typing import Any

from scripts.agent.agents.cypher_builder import CypherBuilder
from scripts.agent.agents.cypher_validator import CypherValidator, CypherValidationError
from scripts.agent.agents.react_agent import ReactAgent
from scripts.agent.agents.synthesizer import Synthesizer, SynthesisResult
from scripts.agent.normalizer import Normalizer, NormalizeResult
from scripts.agent.shared.scratchpad import Scratchpad, Step
from scripts.agent.shared.tool_registry import TOOLS, extract_facts


# ---------------------------------------------------------------------------
# Max steps per intent
# ---------------------------------------------------------------------------

_MAX_STEPS: dict[str | None, int] = {
    "qct_status":              4,
    "dda_status":              5,
    "basis_boost_eligibility": 5,
    "ami_limits":              4,
    "max_rent":                4,
    "lending_risk":            4,
    "full_profile":            8,
    "market_viability":        6,
}
_DEFAULT_MAX_STEPS = 6


# ---------------------------------------------------------------------------
# Default parameters for tools with optional params
# ---------------------------------------------------------------------------

def _default_params(tool_name: str, params: dict) -> dict:
    out = dict(params)
    if tool_name == "get_ami_limits":
        out.setdefault("program_type", "VLI")
    if tool_name in ("search_tracts",):
        out.setdefault("county_fips", None)
        out.setdefault("state_fips", None)
        out.setdefault("is_qct_designated", None)
        out.setdefault("is_dda_designated", None)
        out.setdefault("limit", 50)
    if tool_name in ("search_dda_areas", "search_hmda_risk"):
        out.setdefault("state_fips", None)
        out.setdefault("limit", 50)
    if tool_name == "search_hmda_risk":
        out.setdefault("risk_tier", None)
    return out


# ---------------------------------------------------------------------------
# Termination checks
# ---------------------------------------------------------------------------

def _termination_reason(scratchpad: Scratchpad, step: int, max_steps: int) -> str | None:
    if step >= max_steps:
        return "max_steps"

    # Same tool + same params called twice = stuck loop
    seen: set[str] = set()
    for s in scratchpad.steps_taken:
        key = json.dumps({"a": s.action, "p": s.params}, sort_keys=True)
        if key in seen:
            return "loop_detected"
        seen.add(key)

    # Three consecutive errors
    if scratchpad.consecutive_errors() >= 3:
        return "consecutive_errors"

    return None


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

class ReactLoop:
    """Wires all components together and drives the ReAct loop."""

    def __init__(
        self,
        orchestrator: ReactAgent,
        cypher_builder: CypherBuilder,
        validator: CypherValidator,
        synthesizer: Synthesizer,
        neo4j_client,
        normalizer: Normalizer,
        ontology,
    ) -> None:
        self.orchestrator   = orchestrator
        self.cypher_builder = cypher_builder
        self.validator      = validator
        self.synthesizer    = synthesizer
        self.neo4j          = neo4j_client
        self.normalizer     = normalizer
        self.ontology       = ontology

    def run(
        self,
        question: str,
        intent: str | None = None,
        seed_facts: dict[str, Any] | None = None,
    ) -> tuple[NormalizeResult, Scratchpad, SynthesisResult]:
        """Run the full pipeline. Returns (norm_result, scratchpad, synthesis)."""

        # Step 1 — Normalise
        norm = self.normalizer.normalize(question)

        # Seed known_facts from normalizer + caller-provided facts
        known_facts: dict[str, Any] = dict(seed_facts or {})
        if norm.fips_in_text:
            known_facts.setdefault("fips_code", norm.fips_in_text)
        if norm.year_in_text:
            known_facts.setdefault("year", norm.year_in_text)

        scratchpad = Scratchpad(
            question=norm.text,
            intent=intent,
            known_facts=known_facts,
        )

        max_steps = _MAX_STEPS.get(intent, _DEFAULT_MAX_STEPS)

        # Steps 2-7 — ReAct loop
        for step_num in range(1, max_steps + 1):
            reason = _termination_reason(scratchpad, step_num - 1, max_steps)
            if reason:
                scratchpad.termination_reason = reason
                break

            # Step 2 — Orchestrator THINK
            try:
                thought, action, params = self.orchestrator.think(
                    scratchpad, step_num, max_steps
                )
            except Exception as exc:
                scratchpad.termination_reason = f"llm_error: {exc}"
                break

            # Step 7 — FINISH
            if action.upper() == "FINISH":
                scratchpad.add_step(Step(
                    thought=thought, action="FINISH", params={},
                    cypher="", observation={"status": "agent declared finish"},
                ))
                scratchpad.termination_reason = "agent_finished"
                break

            # Unknown tool
            if action not in TOOLS:
                scratchpad.add_step(Step(
                    thought=thought, action=action, params=params, cypher="",
                    observation={"error": f"Unknown tool '{action}'. Available: {list(TOOLS)}"},
                ))
                continue

            filled_params = _default_params(action, params)

            # Step 3 — Cypher Builder
            try:
                cypher, exec_params = self.cypher_builder.build(
                    action, filled_params, self.ontology
                )
            except Exception as exc:
                scratchpad.add_step(Step(
                    thought=thought, action=action, params=filled_params, cypher="",
                    observation={"error": f"Cypher generation failed: {exc}"},
                ))
                continue

            if not cypher:
                scratchpad.add_step(Step(
                    thought=thought, action=action, params=filled_params, cypher="",
                    observation={"error": "Cypher Builder returned empty query"},
                ))
                continue

            # Step 4 — Validator
            errors = self.validator.validate_and_dry_run(cypher, exec_params, self.neo4j)
            if errors:
                scratchpad.add_step(Step(
                    thought=thought, action=action, params=filled_params, cypher=cypher,
                    observation={"error": " | ".join(errors)},
                ))
                continue

            # Step 5 — Execute
            try:
                rows = self.neo4j.run_read(cypher, exec_params, timeout=10)
            except Exception as exc:
                scratchpad.add_step(Step(
                    thought=thought, action=action, params=filled_params, cypher=cypher,
                    observation={"error": str(exc)},
                ))
                continue

            # Step 6 — Update scratchpad
            if rows:
                observation: dict[str, Any] = {"rows": rows[:20]}
                scratchpad.update_facts(extract_facts(action, rows))
            else:
                observation = {"rows": [], "note": "No data found for these parameters."}

            scratchpad.add_step(Step(
                thought=thought, action=action, params=filled_params,
                cypher=cypher, observation=observation,
            ))

        else:
            scratchpad.termination_reason = "max_steps"

        # Step 8 — Synthesize
        synthesis = self.synthesizer.synthesize(scratchpad)
        return norm, scratchpad, synthesis
