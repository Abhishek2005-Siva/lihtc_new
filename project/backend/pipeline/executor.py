"""Executor — orchestrates the full pipeline for one user question.

Pipeline stages:
  1. Normalize   — deterministic + optional LLM term expansion
  2. Plan        — Agent 1: Orchestrator (reads the raw question directly,
                    produces ONE sequence of tool calls — no fallback paths)
  3. Execute     — for each step:
       Step 1: build_cypher  — Agent 2: CypherBuilder (extracts values from the
                                question itself and writes Cypher in one call)
       Step 2: type_check    — deterministic type fixes
       Step 3: execute_query — Neo4j run_read (retries feed error back to builder)
       Step 4: validate_output — Agent: OutputValidator scores relevance post-execution
  4. Synthesize  — Agent 3: Synthesizer

There is no separate parameter-extraction stage. Orchestrator decides tool
sequencing from the raw question; CypherBuilder independently re-reads the
question for every tool call to extract the values it needs. This means the
tool cache can only be checked once CypherBuilder has produced its own
params dict, not before — see `run_tool_call`.
"""
from __future__ import annotations

from typing import Any

from backend.cache.tool_cache import ToolCache
from backend.pipeline.tool_call import run_tool_call
from backend.agents.cypher_builder import CypherBuilder
from backend.agents.cypher_validator_agent import CypherValidatorAgent
from backend.agents.orchestrator import Orchestrator, Plan, PlanStep
from backend.agents.synthesizer import Synthesizer, SynthesisResult
from backend.registry.tool_registry import TOOLS
from backend.utils.normalizer import Normalizer
from backend.utils.scratchpad import Scratchpad, StepRecord

# Row keys that identify a geographic entity, propagated from a step's result
# into a dependent step's question context (see _extract_geo).
_GEO_KEYS = ("county_fips", "cbsa_code", "state_fips", "is_metro_tract")


class Executor:
    def __init__(
        self,
        orchestrator: Orchestrator,
        cypher_builder: CypherBuilder,
        validator: CypherValidatorAgent,
        synthesizer: Synthesizer,
        neo4j_client,
        normalizer: Normalizer,
        ontology,
        tool_cache: ToolCache | None = None,
    ) -> None:
        self.orchestrator    = orchestrator
        self.cypher_builder  = cypher_builder
        self.validator       = validator
        self.synthesizer     = synthesizer
        self.neo4j           = neo4j_client
        self.normalizer      = normalizer
        self.ontology        = ontology
        self.tool_cache      = tool_cache or ToolCache()

    def run(
        self,
        question: str,
        on_step=None,
        on_stage=None,
        on_attempt=None,
    ) -> tuple[Plan, Scratchpad, SynthesisResult]:
        """Answer one question, independently of any other turn — no history
        is read or written anywhere in this pipeline."""
        # Stage 1 — Normalize
        if on_stage: on_stage("normalize", None)
        norm = self.normalizer.normalize(question)

        # Stage 2 — Plan (Orchestrator reads the raw question directly)
        if on_stage: on_stage("plan", None)
        plan = self.orchestrator.plan(norm.text)
        if on_stage: on_stage("planned", plan)

        if plan.clarification_needed:
            scratchpad = Scratchpad(question=norm.text, end_goal=plan.end_goal)
            synthesis  = SynthesisResult(
                direct_answer   =plan.clarification_question or "Could you clarify your question?",
                evidence        ="",
                regulatory_basis="N/A",
                caveats         ="Clarification required before proceeding.",
                conclusion      =plan.clarification_question or "",
            )
            return plan, scratchpad, synthesis

        # Stage 3 — Execute the planned steps in order
        scratchpad = Scratchpad(question=norm.text, end_goal=plan.end_goal)
        self._run_steps(
            plan.steps, scratchpad,
            on_step=on_step, on_attempt=on_attempt, question=norm.text,
        )

        # Stage 4 — Synthesize
        if on_stage: on_stage("synthesize", None)
        synthesis = self.synthesizer.synthesize(scratchpad)
        return plan, scratchpad, synthesis

    # ── Step execution ───────────────────────────────────────────────────────

    def _run_steps(
        self, steps: list[PlanStep], scratchpad: Scratchpad,
        on_step=None, on_attempt=None, question: str = "",
    ) -> None:
        """Execute the planned steps in order."""
        step_results: dict[str, list[dict]] = {}

        for i, step in enumerate(steps):
            # Skip steps for tools that don't exist in the registry
            if step.tool not in TOOLS:
                record = StepRecord(
                    tool=step.tool, params={}, cypher="", rows=[], row_count=0,
                    error=f"Unknown tool '{step.tool}'",
                )
                scratchpad.add_step(record)
                scratchpad.add_gap(step.tool, {}, f"Unknown tool '{step.tool}'")
                if on_step: on_step(record)
                continue

            # If this step depends on a prior step's result, append the geo facts
            # discovered there to the question as extra context — CypherBuilder
            # reads the raw question directly, so this is the only channel left
            # for passing a value from one step's output into the next step's input.
            step_question = question
            dep_step = None
            if step.depends_on:
                if step.depends_on.startswith("step_"):
                    try:
                        dep_idx = int(step.depends_on.replace("step_", "")) - 1
                        dep_step = steps[dep_idx]
                    except (ValueError, IndexError):
                        dep_step = None
                else:
                    # Model sometimes writes the dependency as a tool NAME instead
                    # of "step_N" (e.g. "resolve_geography") — fall back to matching
                    # by tool name among the earlier steps rather than silently
                    # dropping the whole context hand-off.
                    for earlier in steps[:i]:
                        if earlier.tool == step.depends_on:
                            dep_step = earlier
                            break
            if dep_step is not None:
                prior = step_results.get(dep_step.tool, [])
                if prior:
                    geo = _extract_geo(prior[0])
                    if geo:
                        step_question += (
                            "\n\n[Context from a prior step in this plan — "
                            f"treat these as authoritative extracted values: {geo}]"
                        )

            # Run all execution steps with retries. Tool-cache lookup happens
            # INSIDE this call, once CypherBuilder has produced its own params
            # dict — there's no pre-known params dict to check before the LLM call.
            rows, cypher, error, exec_params = run_tool_call(
                self.cypher_builder, self.neo4j, self.tool_cache,
                step.tool, step_question, self.ontology, on_attempt=on_attempt,
            )
            record = StepRecord(
                tool=step.tool, params=exec_params, cypher=cypher,
                rows=rows or [], row_count=len(rows or []),
                error=error,
            )
            scratchpad.add_step(record)
            if on_step: on_step(record)

            if error:
                scratchpad.add_gap(step.tool, exec_params, error)
            elif not rows:
                scratchpad.add_gap(step.tool, exec_params, "No data returned.")
            else:
                # Validate that the output actually answers the question
                validation = self.validator.validate_output(question, step.tool, rows)
                record.validation_score = validation.score
                record.validation_reason = validation.reason
                if on_step: on_step(record)  # re-fire so UI picks up validation
                if validation.passed:
                    step_results[step.tool] = rows
                    self.tool_cache.store(step.tool, exec_params, rows)
                    scratchpad.record_observation(step.tool, rows, validation.score)
                    # Stop here — no point running further steps once we already
                    # have a confident answer — unless a later step depends on
                    # this one's result (a genuine chain, not a redundant retry).
                    # A later step may reference this one either as "step_N" or,
                    # if the model got the format wrong, by this step's tool name.
                    this_step_id = f"step_{i + 1}"
                    depended_on = any(
                        s.depends_on in (this_step_id, step.tool) for s in steps[i + 1:]
                    )
                    if not depended_on:
                        break
                else:
                    scratchpad.add_gap(
                        step.tool, exec_params,
                        f"Output mismatch (score {validation.score:.2f}): {validation.reason}",
                    )


def _extract_geo(row: dict) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k in _GEO_KEYS and v is not None}
