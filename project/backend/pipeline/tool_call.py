"""Shared tool-call execution — build Cypher, run every deterministic
guardrail, execute against Neo4j, retry on failure.

This is the single place that runs one tool call end-to-end. Both the
Executor (multi-path question answering) and the MCP server (direct
structured tool calls from an external LLM client) call this same function,
so every guardrail built for this pipeline (pre-flight checks, hardcoded-
literal auto-fix, OPTIONAL MATCH trap auto-fix, relationship-direction
auto-fix, etc.) applies identically no matter which caller is asking.
"""
from __future__ import annotations

from typing import Any

from backend.agents.cypher_builder import CypherBuilder
from backend.cache.tool_cache import ToolCache
from backend.pipeline.geo_verify import verify_county_resolution
from backend.pipeline.steps import build_cypher, execute_query, type_check
from backend.pipeline.steps.build_cypher import InsufficientParamsError
from backend.pipeline.type_checker import fix_fabricated_tract_anchor

_MAX_RETRIES = 3


def run_tool_call(
    cypher_builder: CypherBuilder,
    neo4j_client,
    tool_cache: ToolCache,
    tool_name: str,
    question: str,
    ontology,
    on_attempt=None,
) -> tuple[list[dict] | None, str, str | None, dict[str, Any]]:
    """Build + execute one tool call with retries. Returns (rows, cypher, error, exec_params).

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
                cypher_builder, tool_name, question, ontology,
                error=error if attempt > 1 else None,
                previous_cypher=failed_cypher if attempt > 1 else None,
            )
        except InsufficientParamsError as exc:
            _attach_exec_result(cypher_builder, cypher, None, str(exc))
            if on_attempt:
                on_attempt(tool_name, attempt, cypher, None, str(exc))
            return None, cypher, str(exc), {}
        except Exception as exc:
            gen_error = f"Cypher generation failed: {exc}"
            _attach_exec_result(cypher_builder, cypher, None, gen_error)
            if on_attempt:
                on_attempt(tool_name, attempt, cypher, None, gen_error)
            return None, cypher, gen_error, {}

        # Tool cache check — only possible now that CypherBuilder has told us
        # its own params. Skips a repeat Neo4j round-trip (not the LLM call).
        cached = tool_cache.get(tool_name, exec_params)
        if cached is not None:
            _attach_exec_result(cypher_builder, cypher, cached, None)
            if on_attempt:
                on_attempt(tool_name, attempt, cypher, cached, None)
            return cached, cypher, None, exec_params

        # Step 2 — Type check (deterministic fixes)
        cypher = type_check.run(cypher)

        # Step 2a — Auto-correct the fabricated-tract-anchor anti-pattern (see
        # fix_fabricated_tract_anchor docstring) before the pre-flight check —
        # small models have proven unable to reliably stop doing this across
        # retries even when told exactly what's wrong, so it's fixed mechanically.
        cypher, exec_params = fix_fabricated_tract_anchor(cypher, exec_params, question)

        # Step 2b — Pre-flight: catch known-bad patterns before hitting Neo4j.
        # Gives the LLM a precise, actionable fix instead of a raw Cypher
        # syntax/parameter error it has repeatedly failed to self-correct from.
        preflight_error = type_check.preflight_check(cypher, exec_params, question)
        if preflight_error:
            _attach_exec_result(cypher_builder, cypher, None, preflight_error)
            if on_attempt:
                on_attempt(tool_name, attempt, cypher, None, preflight_error)
            error = preflight_error
            failed_cypher = cypher
            continue

        # Step 2c — Verify a resolved county_fips against the graph (needs a
        # live Neo4j round-trip, so it can't live in the pure-function preflight
        # checks above). Confirmed necessary via a real trace: the model
        # resolved "Cook County IL" to '48113' (Dallas County TX's code) from
        # memory, producing a plausible-looking but entirely wrong answer.
        geo_error = verify_county_resolution(cypher, exec_params, question, neo4j_client)
        if geo_error:
            _attach_exec_result(cypher_builder, cypher, None, geo_error)
            if on_attempt:
                on_attempt(tool_name, attempt, cypher, None, geo_error)
            error = geo_error
            failed_cypher = cypher
            continue

        # Step 3 — Execute; any error feeds back into next attempt
        rows, exc_str = execute_query.run(neo4j_client, cypher, exec_params)
        _attach_exec_result(cypher_builder, cypher, rows, exc_str)
        if on_attempt:
            on_attempt(tool_name, attempt, cypher, rows, exc_str)
        if exc_str:
            error = exc_str
            failed_cypher = cypher
            continue

        return rows, cypher, None, exec_params

    return None, cypher, f"Failed after {_MAX_RETRIES} attempts: {error}", exec_params


def _attach_exec_result(
    cypher_builder: CypherBuilder, cypher: str, rows: list[dict] | None, error: str | None,
) -> None:
    """Attach the execution outcome to the CypherBuilder's most recent LLMCall
    record, so the LLM call log can show what running the generated Cypher
    actually did — right below that call's RESPONSE."""
    call_log = getattr(cypher_builder.llm, "call_log", None)
    if call_log:
        call_log[-1].exec_result = {
            "cypher": cypher,
            "row_count": len(rows) if rows is not None else 0,
            "sample": (rows or [])[:5],
            "error": error,
        }
