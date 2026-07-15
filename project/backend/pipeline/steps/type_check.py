"""Step 2 — Type check.

Applies deterministic property-type fixes to the generated Cypher before
it reaches the validator. No LLM involved — pure string transforms.

Fixes include:
  - Boolean fields stored as 0/1 → true/false
  - Integer fields quoted as strings → unquoted (includes assessment_year)
"""
from __future__ import annotations

from backend.pipeline.type_checker import (
    check_and_fix,
    find_clause_inside_where,
    find_disconnected_match,
    find_fabricated_fips_code,
    find_hardcoded_literals,
    find_map_range_filter,
    find_missing_filter_columns,
    find_optional_match_where_trap,
    find_unauthorized_params,
    find_wrong_length_identifier,
    fix_double_where,
    fix_optional_match_where_trap,
)


def run(cypher: str) -> str:
    """Apply all type-fix rules to cypher. Returns the corrected Cypher string."""
    cypher = check_and_fix(cypher).cypher
    cypher = fix_double_where(cypher)  # deterministic, purely syntactic — safe to auto-fix
    # Auto-correct OPTIONAL MATCH + WHERE trap — small models have proven unable to
    # reliably apply this one-word fix even when told exactly what's wrong on every
    # retry, so it's corrected mechanically instead of relying on retries to converge.
    cypher = fix_optional_match_where_trap(cypher)
    return cypher


def preflight_check(cypher: str, exec_params: dict, question: str = "") -> str | None:
    """Deterministic checks for Cypher patterns that are always invalid.

    Returns an actionable error message if a known-bad pattern is found, else None.
    Run this before sending to Neo4j to catch anti-patterns with a precise fix
    instead of a generic Neo4j syntax/parameter error the LLM can't self-correct from.
    """
    allowed_params = set(exec_params.keys())
    return (
        find_disconnected_match(cypher)
        or find_clause_inside_where(cypher)
        or find_map_range_filter(cypher)
        or find_hardcoded_literals(cypher)
        or find_optional_match_where_trap(cypher)
        or find_unauthorized_params(cypher, allowed_params)
        or find_missing_filter_columns(cypher, allowed_params)
        or find_wrong_length_identifier(cypher, exec_params)
        or find_fabricated_fips_code(cypher, exec_params, question)
    )
