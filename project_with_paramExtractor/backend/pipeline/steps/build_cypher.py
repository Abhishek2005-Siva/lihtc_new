"""Step 1 — Build Cypher.

Calls CypherBuilder to translate a tool name + params into a Cypher query.
Returns (cypher, exec_params). Raises InsufficientParamsError when the LLM
signals it cannot build a query with the provided parameters.
"""
from __future__ import annotations

from typing import Any

from backend.agents.cypher_builder import CypherBuilder, InsufficientParamsError  # noqa: F401 (re-exported)


def run(
    builder: CypherBuilder,
    tool_name: str,
    params: dict[str, Any],
    ontology,
    error: str | None = None,
    previous_cypher: str | None = None,
    question: str = "",
) -> tuple[str, dict[str, Any]]:
    """Generate Cypher for one tool call.

    Args:
        builder:         CypherBuilder instance.
        tool_name:       Name of the abstract tool (e.g. "search_tracts").
        params:          Resolved parameters for this step (nulls already stripped).
        ontology:        GraphOntology used for schema injection.
        error:           Error string from the previous attempt (triggers retry prompt).
        previous_cypher: The Cypher that failed, sent alongside `error` so the LLM
                         patches it instead of regenerating from scratch.
        question:        The user's original question, given as context so the LLM
                         can judge comparison intent (between/exactly/above/below)
                         beyond what the min_/max_ param naming convention can express.

    Returns:
        (cypher, exec_params) — the generated Cypher string and the final
        parameter dict that should be passed to Neo4j.

    Raises:
        InsufficientParamsError: LLM cannot build a query with these params.
        Exception: Any other LLM or parse failure.
    """
    return builder.build(
        tool_name, params, ontology,
        error=error, previous_cypher=previous_cypher, question=question,
    )
