"""Executor — orchestrates the full pipeline for one user question.

Pipeline stages:
  1. Normalize     — deterministic + optional LLM term expansion
  2. PlanPaths     — Agent 1: PathPlanner (reads the raw question directly)
  3. ExecutePaths  — for each path, for each step:
       Step 1: build_cypher  — Agent 2: CypherBuilder (extracts values from the
                                question itself and writes Cypher in one call)
       Step 2: type_check    — deterministic type fixes
       Step 3: execute_query — Neo4j run_read (retries feed error back to builder)
       Step 4: validate_output — Agent: OutputValidator scores relevance post-execution
  4. Synthesize    — Agent 3: Synthesizer

There is no separate parameter-extraction stage. PathPlanner decides tool
sequencing from the raw question; CypherBuilder independently re-reads the
question for every tool call to extract the values it needs. This means the
tool cache can only be checked once CypherBuilder has produced its own
params dict, not before — see `_execute_step`.
"""
from __future__ import annotations

from typing import Any

from backend.cache.tool_cache import ToolCache
from backend.pipeline.steps import build_cypher, execute_query, type_check
from backend.pipeline.steps.build_cypher import InsufficientParamsError
from backend.agents.cypher_builder import CypherBuilder
from backend.agents.cypher_validator_agent import CypherValidatorAgent
from backend.agents.path_planner import ExecutionPath, PathPlanner, Plan
from backend.agents.synthesizer import Synthesizer, SynthesisResult
from backend.registry.tool_registry import TOOLS
from backend.utils.normalizer import Normalizer
from backend.utils.scratchpad import Scratchpad, StepRecord

_MAX_RETRIES = 3

# Row keys that identify a geographic entity, propagated from a step's result
# into a dependent step's question context (see _extract_geo).
_GEO_KEYS = ("county_fips", "cbsa_code", "state_fips", "is_metro_tract")


class Executor:
    def __init__(
        self,
        path_planner: PathPlanner,
        cypher_builder: CypherBuilder,
        validator: CypherValidatorAgent,
        synthesizer: Synthesizer,
        neo4j_client,
        normalizer: Normalizer,
        ontology,
        tool_cache: ToolCache | None = None,
    ) -> None:
        self.path_planner    = path_planner
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

        # Stage 2 — Plan paths (PathPlanner reads the raw question directly)
        if on_stage: on_stage("plan", None)
        plan = self.path_planner.plan(norm.text)
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

        # Stage 3 — Execute paths in order, stopping as soon as one produces a
        # passing (validated, confident) observation — no need to burn more LLM
        # calls and Neo4j round-trips on fallback paths once we already have a
        # good answer. record_observation() still only accepts passing results
        # (score >= threshold), so within the path(s) actually run, a weaker
        # step can never overwrite a better one.
        scratchpad = Scratchpad(question=norm.text, end_goal=plan.end_goal)
        for path in plan.ordered_paths():
            success = self._run_path(
                path, scratchpad,
                on_step=on_step, on_attempt=on_attempt, question=norm.text,
            )
            if success:
                break

        # Stage 4 — Synthesize
        if on_stage: on_stage("synthesize", None)
        synthesis = self.synthesizer.synthesize(scratchpad)
        return plan, scratchpad, synthesis

    # ── Path execution ───────────────────────────────────────────────────────

    def _run_path(
        self, path: ExecutionPath, scratchpad: Scratchpad,
        on_step=None, on_attempt=None, question: str = "",
    ) -> bool:
        """Execute one path. Returns True if it produced useful observations."""
        step_results: dict[str, list[dict]] = {}

        for i, step in enumerate(path.steps):
            # Skip steps for tools that don't exist in the registry
            if step.tool not in TOOLS:
                record = StepRecord(
                    tool=step.tool, params={}, cypher="", rows=[], row_count=0,
                    error=f"Unknown tool '{step.tool}'", path_id=path.id,
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
            if step.depends_on and step.depends_on.startswith("step_"):
                try:
                    dep_idx = int(step.depends_on.replace("step_", "")) - 1
                    dep_step = path.steps[dep_idx]
                    prior = step_results.get(dep_step.tool, [])
                    if prior:
                        geo = _extract_geo(prior[0])
                        if geo:
                            step_question += (
                                "\n\n[Context from a prior step in this plan — "
                                f"treat these as authoritative extracted values: {geo}]"
                            )
                except (ValueError, IndexError):
                    pass

            # Run all execution steps with retries. Tool-cache lookup happens
            # INSIDE this call, once CypherBuilder has produced its own params
            # dict — there's no pre-known params dict to check before the LLM call.
            rows, cypher, error, exec_params = self._execute_step(
                step.tool, step_question, on_attempt=on_attempt,
            )
            record = StepRecord(
                tool=step.tool, params=exec_params, cypher=cypher,
                rows=rows or [], row_count=len(rows or []),
                error=error, path_id=path.id,
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
                    # Stop this path here — no point running further steps once we
                    # already have a confident answer — unless a later step in this
                    # same path depends on this one's result (a genuine chain, not
                    # a redundant alternative attempt).
                    this_step_id = f"step_{i + 1}"
                    depended_on = any(
                        s.depends_on == this_step_id for s in path.steps[i + 1:]
                    )
                    if not depended_on:
                        break
                else:
                    scratchpad.add_gap(
                        step.tool, exec_params,
                        f"Output mismatch (score {validation.score:.2f}): {validation.reason}",
                    )

        return bool(scratchpad.observations)

    def _execute_step(
        self, tool_name: str, question: str, on_attempt=None,
    ) -> tuple[list[dict] | None, str, str | None, dict[str, Any]]:
        """Build + execute with retry loop. Returns (rows, cypher, error, exec_params).

        on_attempt, if given, fires after each build+execute attempt (before deciding
        whether to retry) so the caller can show intermediate progress.
        """
        cypher = ""
        exec_params: dict[str, Any] = {}
        error: str | None = None
        failed_cypher: str | None = None   # the Cypher from the previous failed attempt

        for attempt in range(1, _MAX_RETRIES + 1):
            # Step 1 — Build Cypher (feeds previous error + previous Cypher back on retry
            # so the LLM patches the specific query instead of regenerating blind)
            try:
                cypher, exec_params = build_cypher.run(
                    self.cypher_builder, tool_name, question, self.ontology,
                    error=error if attempt > 1 else None,
                    previous_cypher=failed_cypher if attempt > 1 else None,
                )
            except InsufficientParamsError as exc:
                self._attach_exec_result(cypher, None, str(exc))
                if on_attempt:
                    on_attempt(tool_name, attempt, cypher, None, str(exc))
                return None, cypher, str(exc), {}
            except Exception as exc:
                gen_error = f"Cypher generation failed: {exc}"
                self._attach_exec_result(cypher, None, gen_error)
                if on_attempt:
                    on_attempt(tool_name, attempt, cypher, None, gen_error)
                return None, cypher, gen_error, {}

            # Tool cache check — only possible now that CypherBuilder has told us
            # its own params. Skips a repeat Neo4j round-trip (not the LLM call).
            cached = self.tool_cache.get(tool_name, exec_params)
            if cached is not None:
                self._attach_exec_result(cypher, cached, None)
                if on_attempt:
                    on_attempt(tool_name, attempt, cypher, cached, None)
                return cached, cypher, None, exec_params

            # Step 2 — Type check (deterministic fixes)
            cypher = type_check.run(cypher)

            # Step 2b — Pre-flight: catch known-bad patterns before hitting Neo4j.
            # Gives the LLM a precise, actionable fix instead of a raw Cypher
            # syntax/parameter error it has repeatedly failed to self-correct from.
            # allowed_params = exec_params keys — the ONLY $params the Cypher may use.
            preflight_error = type_check.preflight_check(cypher, set(exec_params.keys()))
            if preflight_error:
                self._attach_exec_result(cypher, None, preflight_error)
                if on_attempt:
                    on_attempt(tool_name, attempt, cypher, None, preflight_error)
                error = preflight_error
                failed_cypher = cypher
                continue

            # Step 3 — Execute; any error feeds back into next attempt
            rows, exc_str = execute_query.run(self.neo4j, cypher, exec_params)
            self._attach_exec_result(cypher, rows, exc_str)
            if on_attempt:
                on_attempt(tool_name, attempt, cypher, rows, exc_str)
            if exc_str:
                error = exc_str
                failed_cypher = cypher
                continue

            return rows, cypher, None, exec_params

        return None, cypher, f"Failed after {_MAX_RETRIES} attempts: {error}", exec_params

    def _attach_exec_result(
        self, cypher: str, rows: list[dict] | None, error: str | None,
    ) -> None:
        """Attach the execution outcome to the CypherBuilder's most recent LLMCall
        record, so the LLM call log can show what running the generated Cypher
        actually did — right below that call's RESPONSE."""
        call_log = getattr(self.cypher_builder.llm, "call_log", None)
        if call_log:
            call_log[-1].exec_result = {
                "cypher": cypher,
                "row_count": len(rows) if rows is not None else 0,
                "sample": (rows or [])[:5],
                "error": error,
            }


def _extract_geo(row: dict) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k in _GEO_KEYS and v is not None}
