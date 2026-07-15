"""Executor — orchestrates the full pipeline for one user question.

Pipeline stages:
  1. Normalize     — deterministic + optional LLM term expansion
  2. ExtractParams — Agent 1: ParamExtractor
  3. PlanPaths     — Agent 2: PathPlanner
  4. ExecutePaths  — for each path, for each step:
       Step 1: build_cypher  — Agent 3: CypherBuilder
       Step 2: type_check    — deterministic type fixes
       Step 3: execute_query — Neo4j run_read (retries feed error back to builder)
       Step 4: validate_output — Agent: OutputValidator scores relevance post-execution
  5. Synthesize    — Agent 4: Synthesizer
"""
from __future__ import annotations

from typing import Any

from backend.cache.geo_cache import GeoCache
from backend.cache.tool_cache import ToolCache
from backend.pipeline.steps import build_cypher, execute_query, type_check
from backend.pipeline.steps.build_cypher import InsufficientParamsError
from backend.agents.cypher_builder import CypherBuilder
from backend.agents.cypher_validator_agent import CypherValidatorAgent
from backend.agents.param_extractor import ExtractedParams, ParamExtractor
from backend.agents.path_planner import ExecutionPath, PathPlanner, Plan
from backend.agents.synthesizer import Synthesizer, SynthesisResult
from backend.registry.tool_registry import TOOLS, extract_facts
from backend.utils.normalizer import Normalizer
from backend.utils.scratchpad import Scratchpad, StepRecord

_MAX_RETRIES = 3

# Keys from ExtractedParams that are pipeline metadata, not graph query params.
_PIPELINE_META = frozenset({
    "end_goal", "query_mode", "clarification_needed", "clarification_question", "filters",
})


class Executor:
    def __init__(
        self,
        param_extractor: ParamExtractor,
        path_planner: PathPlanner,
        cypher_builder: CypherBuilder,
        validator: CypherValidatorAgent,
        synthesizer: Synthesizer,
        neo4j_client,
        normalizer: Normalizer,
        ontology,
        geo_cache: GeoCache | None = None,
        tool_cache: ToolCache | None = None,
    ) -> None:
        self.param_extractor = param_extractor
        self.path_planner    = path_planner
        self.cypher_builder  = cypher_builder
        self.validator       = validator
        self.synthesizer     = synthesizer
        self.neo4j           = neo4j_client
        self.normalizer      = normalizer
        self.ontology        = ontology
        self.geo_cache       = geo_cache or GeoCache()
        self.tool_cache      = tool_cache or ToolCache()

    def run(
        self,
        question: str,
        history: list[dict] | None = None,
        on_step=None,
        on_stage=None,
        on_attempt=None,
    ) -> tuple[ExtractedParams, Plan, Scratchpad, SynthesisResult]:
        # Stage 1 — Normalize
        if on_stage: on_stage("normalize", None)
        norm = self.normalizer.normalize(question)

        # Stage 2 — Extract parameters
        if on_stage: on_stage("extract", None)
        params = self.param_extractor.extract(norm.text, history=history)
        if on_stage: on_stage("extracted", params)

        if params.clarification_needed:
            scratchpad = Scratchpad(question=norm.text, end_goal=params.end_goal)
            synthesis  = SynthesisResult(
                direct_answer   =params.clarification_question or "Could you clarify your question?",
                evidence        ="",
                regulatory_basis="N/A",
                caveats         ="Clarification required before proceeding.",
                conclusion      =params.clarification_question or "",
            )
            return params, _empty_plan(params.end_goal), scratchpad, synthesis

        # Seed geo facts from cache if same tract seen before
        known_facts: dict[str, Any] = {}
        if params.fips_code:
            known_facts = self.geo_cache.get(params.fips_code)

        # Stage 3 — Plan paths
        if on_stage: on_stage("plan", None)
        plan = self.path_planner.plan(params.to_dict(), known_facts=known_facts)
        if on_stage: on_stage("planned", plan)

        # Stage 4 — Execute ALL paths, not just until one succeeds. Each path's
        # steps are built and run independently; Scratchpad.record_observation()
        # keeps only the highest-scoring passing result per tool across all of
        # them, so a weaker path can never silently overwrite a better one and
        # every path's attempt is visible in the trace/gaps.
        scratchpad = Scratchpad(question=norm.text, end_goal=params.end_goal)
        for path in plan.ordered_paths():
            self._run_path(
                path, scratchpad, params,
                on_step=on_step, on_attempt=on_attempt, question=norm.text,
            )

        # Update geo cache from observations
        if params.fips_code:
            all_facts: dict[str, Any] = {}
            for step in scratchpad.steps_taken:
                all_facts.update(extract_facts(step.tool, step.rows))
            self.geo_cache.store(params.fips_code, all_facts)

        # Stage 5 — Synthesize
        if on_stage: on_stage("synthesize", None)
        synthesis = self.synthesizer.synthesize(scratchpad, history=history)
        return params, plan, scratchpad, synthesis

    # ── Path execution ───────────────────────────────────────────────────────

    def _run_path(
        self, path: ExecutionPath, scratchpad: Scratchpad, params: ExtractedParams,
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

            # Resolve params — step params first, then all structural + extra params from ExtractedParams
            resolved = {k: v for k, v in step.params.items() if v is not None}
            for k, v in params.to_dict().items():
                if k not in _PIPELINE_META and v is not None and k not in resolved:
                    resolved[k] = v

            # PathPlanner sometimes mislabels county_fips as fips_code (5-digit value).
            if "fips_code" in resolved and "county_fips" not in resolved:
                val = str(resolved["fips_code"])
                if len(val) == 5:
                    resolved["county_fips"] = val
                    del resolved["fips_code"]

            if step.depends_on and step.depends_on.startswith("step_"):
                try:
                    dep_idx = int(step.depends_on.replace("step_", "")) - 1
                    dep_step = path.steps[dep_idx]
                    prior = step_results.get(dep_step.tool, [])
                    if prior:
                        resolved.update(_extract_geo(prior[0]))
                except (ValueError, IndexError):
                    pass

            # Tool cache check — cached results already passed validation once.
            cached = self.tool_cache.get(step.tool, resolved)
            if cached is not None:
                record = StepRecord(
                    tool=step.tool, params=resolved, cypher="[cached]",
                    rows=cached, row_count=len(cached), path_id=path.id,
                    validation_score=1.0,
                )
                scratchpad.add_step(record)
                scratchpad.record_observation(step.tool, cached, 1.0)
                step_results[step.tool] = cached
                if on_step: on_step(record)
                continue

            # Run all 4 execution steps with retries
            rows, cypher, error = self._execute_step(
                step.tool, resolved, on_attempt=on_attempt, question=question,
            )
            record = StepRecord(
                tool=step.tool, params=resolved, cypher=cypher,
                rows=rows or [], row_count=len(rows or []),
                error=error, path_id=path.id,
            )
            scratchpad.add_step(record)
            if on_step: on_step(record)

            if error:
                scratchpad.add_gap(step.tool, resolved, error)
            elif not rows:
                scratchpad.add_gap(step.tool, resolved, "No data returned.")
            else:
                # Validate that the output actually answers the question
                validation = self.validator.validate_output(question, step.tool, rows)
                record.validation_score = validation.score
                record.validation_reason = validation.reason
                if on_step: on_step(record)  # re-fire so UI picks up validation
                if validation.passed:
                    step_results[step.tool] = rows
                    self.tool_cache.store(step.tool, resolved, rows)
                    scratchpad.record_observation(step.tool, rows, validation.score)
                else:
                    scratchpad.add_gap(
                        step.tool, resolved,
                        f"Output mismatch (score {validation.score:.2f}): {validation.reason}",
                    )

        return bool(scratchpad.observations)

    def _execute_step(
        self, tool_name: str, params: dict[str, Any], on_attempt=None, question: str = "",
    ) -> tuple[list[dict] | None, str, str | None]:
        """Build + execute with retry loop. Returns (rows, cypher, error).

        on_attempt, if given, fires after each build+execute attempt (before deciding
        whether to retry) so the caller can show intermediate progress.
        """
        cypher = ""
        error: str | None = None
        failed_cypher: str | None = None   # the Cypher from the previous failed attempt

        for attempt in range(1, _MAX_RETRIES + 1):
            # Step 1 — Build Cypher (feeds previous error + previous Cypher back on retry
            # so the LLM patches the specific query instead of regenerating blind)
            try:
                cypher, exec_params = build_cypher.run(
                    self.cypher_builder, tool_name, params, self.ontology,
                    error=error if attempt > 1 else None,
                    previous_cypher=failed_cypher if attempt > 1 else None,
                    question=question,
                )
            except InsufficientParamsError as exc:
                self._attach_exec_result(cypher, None, str(exc))
                if on_attempt:
                    on_attempt(tool_name, attempt, cypher, None, str(exc))
                return None, cypher, str(exc)
            except Exception as exc:
                gen_error = f"Cypher generation failed: {exc}"
                self._attach_exec_result(cypher, None, gen_error)
                if on_attempt:
                    on_attempt(tool_name, attempt, cypher, None, gen_error)
                return None, cypher, gen_error

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

            return rows, cypher, None

        return None, cypher, f"Failed after {_MAX_RETRIES} attempts: {error}"

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
    geo_keys = ("county_fips", "cbsa_code", "state_fips", "is_metro_tract")
    return {k: v for k, v in row.items() if k in geo_keys and v is not None}


def _empty_plan(end_goal: str) -> Plan:
    from backend.agents.path_planner import Plan
    return Plan(end_goal=end_goal, paths=[], selected_path="", selection_reason="")
