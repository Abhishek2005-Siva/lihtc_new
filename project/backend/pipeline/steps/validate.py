"""Step 3 — Validate Cypher.

Two layers:
  Layer 1 — LLM semantic review (CypherValidatorAgent): checks $param references,
             write clauses, missing RETURN, scope errors. Understands Cypher
             contextually — no false positives from static pattern matching.
  Layer 2 — Neo4j EXPLAIN: final ground-truth syntax check.

Returns a list of error strings. Empty list means the query is valid.
"""
from __future__ import annotations

from typing import Any

from backend.agents.cypher_validator_agent import CypherValidatorAgent


def run(
    validator: CypherValidatorAgent,
    cypher: str,
    params: dict[str, Any],
    neo4j_client,
    tool_name: str = "",
) -> list[str]:
    """Run both validation layers. Returns [] if valid, list of errors otherwise."""
    return validator.validate_and_explain(cypher, params, neo4j_client, tool_name)
