"""Cypher Builder — translates a tool call into a Neo4j Cypher query.

Input:  tool_name + params + ontology (exact property names via build_schema_context)
Output: {cypher: str, params: dict}

This agent never generates Cypher from a raw question. It only translates
a structured tool call (action + params) using the exact schema extracted
from the ontology. Property names come from the ontology — never guessed.
"""
from __future__ import annotations

import json
from typing import Any

from scripts.agent.shared.tool_registry import build_schema_context
from scripts.agent.shared.parser import parse_json_object


_SYSTEM = """\
Generate a read-only Neo4j Cypher query for the LIHTC knowledge graph.

You receive:
- TOOL: which operation to perform
- PURPOSE: what the query must answer
- QUERY PATTERN: the traversal and return structure to follow
- EXACT SCHEMA: the ONLY property names you may use
- RELEVANT RELATIONSHIPS: valid edges between the node labels
- TYPE CONSTRAINTS: neo4j_type and comparison rules per property

STRICT RULES — all mandatory:
1. Use ONLY property names listed under EXACT SCHEMA. Never invent or guess names.
   If the schema lists "cbsa_title" you must write cbsa_title, not "metro_name".
2. BOOLEAN fields (is_designated, is_high_disparity, is_metro_tract, is_territory,
   is_multifamily_constrained, split_tr_flag): compare with = true / = false.
   NEVER use = 0/1 or COALESCE(...) = 1.
3. INTEGER fields (designation_year, year, basis_boost_pct, ami_pct): NEVER quote them.
   Write WHERE q.designation_year = $year (integer), not = '2025'.
4. assessment_year on LenderBehaviorRisk is STRING: use r.assessment_year = toString($year).
5. Parameterise ALL user-supplied values ($fips_code, $year, etc.).
6. Use OPTIONAL MATCH for all designation lookups (QCTDesignation, SDDADesignation,
   NMDDADesignation) — a plain MATCH returns nothing when the designation is absent.
7. DDA queries must check BOTH SDDADesignation (metro) AND NMDDADesignation (non-metro).
8. Section8AMILimit is reached via County-[:HAS_MSA_AMI], never via APPLIES_TO.
9. No write clauses: CREATE, MERGE, SET, DELETE, DROP, REMOVE.
10. fips_code is STRING zero-padded to 11 digits. county_fips zero-padded to 5.

Return ONLY a JSON object — no markdown, no explanation outside the JSON:
{"cypher": "MATCH ... RETURN ...", "params": {"param_name": value}}
"""


class CypherBuilder:
    """Cypher Builder LLM — converts a tool call to a validated Cypher query."""

    def __init__(self, llm_client) -> None:
        self.llm = llm_client

    def build(
        self,
        tool_name: str,
        params: dict[str, Any],
        ontology,
    ) -> tuple[str, dict[str, Any]]:
        """Generate Cypher for a tool call. Returns (cypher, exec_params).

        exec_params merges agent-provided params with any extras the LLM added.
        Agent params are authoritative — LLM cannot override them.
        """
        schema_ctx = build_schema_context(tool_name, ontology)
        messages = [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": (
                    f"{schema_ctx}\n\n"
                    f"Agent-provided params: {json.dumps(params, default=str)}"
                ),
            },
        ]
        raw = self.llm.complete(messages, label=f"Cypher Builder -- {tool_name}")
        data = parse_json_object(raw)

        cypher = data.get("cypher", "").strip()
        # Start with agent params as source of truth; add LLM extras (defaults, etc.)
        exec_params: dict[str, Any] = dict(params)
        exec_params.update({k: v for k, v in data.get("params", {}).items()
                            if k not in params})

        return cypher, exec_params
