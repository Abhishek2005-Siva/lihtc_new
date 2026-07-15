"""Step 1 — Build Cypher.

Calls CypherBuilder to translate a tool name + the raw question into a Cypher
query. Returns (cypher, exec_params). Raises InsufficientParamsError when the
LLM signals it cannot build a query from the question given.
"""
from __future__ import annotations

from typing import Any

from backend.agents.cypher_builder import CypherBuilder, InsufficientParamsError  # noqa: F401 (re-exported)


def run(
    builder: CypherBuilder,
    tool_name: str,
    question: str,
    ontology,
    error: str | None = None,
    previous_cypher: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Generate Cypher for one tool call.

    Args:
        builder:         CypherBuilder instance.
        tool_name:       Name of the abstract tool (e.g. "search_tracts").
        question:        The user's question (plus any prior-step context appended)
                         — the ONLY source CypherBuilder reads values from.
        ontology:        GraphOntology used for schema injection.
        error:           Error string from the previous attempt (triggers retry prompt).
        previous_cypher: The Cypher that failed, sent alongside `error` so the LLM
                         patches it instead of regenerating from scratch.

    Returns:
        (cypher, exec_params) — the generated Cypher string and the final
        parameter dict that should be passed to Neo4j.

    Raises:
        InsufficientParamsError: LLM cannot build a query from the question.
        Exception: Any other LLM or parse failure.
    """
    return builder.build(
        tool_name, question, ontology,
        error=error, previous_cypher=previous_cypher,
    )
