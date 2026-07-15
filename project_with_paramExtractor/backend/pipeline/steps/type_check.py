"""Step 2 — Type check.

Applies deterministic property-type fixes to the generated Cypher before
it reaches the validator. No LLM involved — pure string transforms.

Fixes include:
  - Boolean fields stored as 0/1 → true/false
  - Integer fields quoted as strings → unquoted
  - assessment_year integer → toString($year) coercion
"""
from __future__ import annotations

from backend.pipeline.type_checker import (
    check_and_fix,
    find_map_range_filter,
    find_missing_filter_columns,
    find_unauthorized_params,
    fix_double_where,
)


def run(cypher: str) -> str:
    """Apply all type-fix rules to cypher. Returns the corrected Cypher string."""
    cypher = check_and_fix(cypher).cypher
    cypher = fix_double_where(cypher)  # deterministic, purely syntactic — safe to auto-fix
    return cypher


def preflight_check(cypher: str, allowed_params: set[str]) -> str | None:
    """Deterministic checks for Cypher patterns that are always invalid.

    Returns an actionable error message if a known-bad pattern is found, else None.
    Run this before sending to Neo4j to catch anti-patterns with a precise fix
    instead of a generic Neo4j syntax/parameter error the LLM can't self-correct from.
    """
    return (
        find_map_range_filter(cypher)
        or find_unauthorized_params(cypher, allowed_params)
        or find_missing_filter_columns(cypher, allowed_params)
    )
