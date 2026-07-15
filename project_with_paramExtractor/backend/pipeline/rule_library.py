"""Rule library — tagged Cypher rules for dynamic prompt assembly.

Rules are tagged by: tool name, param name, node label, or "universal".
The Cypher Builder receives only the rules relevant to its current tool call.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Rule:
    text: str
    tags: list[str]   # e.g. ["universal"], ["tool:check_dda"], ["label:QCTDesignation"]


RULES: list[Rule] = [
    # ── Universal ────────────────────────────────────────────────────────────
    Rule(
        "No write clauses: CREATE, MERGE, SET, DELETE, DROP, REMOVE.",
        ["universal"],
    ),
    Rule(
        "Parameterise ALL user-supplied values with $. Write {fips_code: $fips_code}, "
        "never {fips_code: fips_code}.",
        ["universal"],
    ),
    Rule(
        "Valid node labels: CensusTract, County, State, MetroArea, QCTDesignation, "
        "SDDADesignation, NMDDADesignation, Section8AMILimit, LenderBehaviorRisk. "
        "Never use abbreviations (MSA, QCT, DDA, SDDA, NMDDA) as labels.",
        ["universal"],
    ),

    # ── Param: fips_code ─────────────────────────────────────────────────────
    Rule(
        "fips_code is an 11-digit STRING (e.g. '01001020700'). Never confuse with "
        "county_fips (5-digit).",
        ["param:fips_code"],
    ),

    # ── Param: county_fips ───────────────────────────────────────────────────
    Rule(
        "county_fips is a 5-digit STRING (e.g. '48113'). Filter tracts with: "
        "MATCH (ct:CensusTract {county_fips: $county_fips}). "
        "Do NOT traverse through a County node just to filter by county.",
        ["param:county_fips"],
    ),

    # ── Param: year ──────────────────────────────────────────────────────────
    Rule(
        "designation_year, year (AMI), and basis_boost_pct are INTEGER — never quote them. "
        "Write: q.designation_year = $year  (not = '2025').",
        ["param:year"],
    ),

    # ── Label: QCTDesignation ────────────────────────────────────────────────
    Rule(
        "QCTDesignation links DIRECTLY to CensusTract via APPLIES_TO: "
        "(q:QCTDesignation)-[:APPLIES_TO]->(ct:CensusTract). "
        "Never route through County or State.",
        ["label:QCTDesignation"],
    ),
    Rule(
        "The graph contains BOTH is_designated=true AND is_designated=false rows. "
        "Always filter: q.is_designated = true",
        ["label:QCTDesignation", "label:SDDADesignation", "label:NMDDADesignation"],
    ),
    Rule(
        "Use OPTIONAL MATCH for designation lookups when you need to return the "
        "tract regardless of whether a designation exists (e.g. 'is this tract designated?'). "
        "Use plain MATCH when filtering to only designated entities "
        "(e.g. 'show all designated tracts').",
        ["label:QCTDesignation", "label:SDDADesignation", "label:NMDDADesignation"],
    ),
    Rule(
        "poverty_rate_at_designation is a FLOAT stored as decimal 0.0–1.0. "
        "'above 25%' means > 0.25, not > 25.",
        ["label:QCTDesignation"],
    ),
    Rule(
        "When min_poverty_rate_at_designation is in AGENT PARAMS, add: "
        "AND q.poverty_rate_at_designation >= $min_poverty_rate_at_designation in the WHERE clause. "
        "When max_poverty_rate_at_designation is in AGENT PARAMS, add: "
        "AND q.poverty_rate_at_designation <= $max_poverty_rate_at_designation in the WHERE clause.",
        ["param:min_poverty_rate_at_designation", "param:max_poverty_rate_at_designation"],
    ),

    # ── Label: SDDADesignation / NMDDADesignation ────────────────────────────
    Rule(
        "SDDADesignation connects to MetroArea via APPLIES_TO: "
        "(ct)-[:IN_METRO]->(ma:MetroArea)<-[:APPLIES_TO]-(s:SDDADesignation).",
        ["label:SDDADesignation"],
    ),
    Rule(
        "NMDDADesignation connects to County via APPLIES_TO: "
        "(ct)-[:IN_COUNTY]->(co:County)<-[:APPLIES_TO]-(n:NMDDADesignation).",
        ["label:NMDDADesignation"],
    ),

    # ── Tool: check_dda ──────────────────────────────────────────────────────
    Rule(
        "DDA means BOTH SDDADesignation (metro) AND NMDDADesignation (non-metro). "
        "Always check both with OPTIONAL MATCH for each. "
        "Return: (s IS NOT NULL OR n IS NOT NULL) AS is_dda_designated.",
        ["tool:check_dda"],
    ),

    # ── Label: Section8AMILimit ───────────────────────────────────────────────
    Rule(
        "Section8AMILimit is reached via County-[:HAS_MSA_AMI]->Section8AMILimit. "
        "Never use APPLIES_TO to reach it.",
        ["label:Section8AMILimit"],
    ),
    Rule(
        "AMI limits are WIDE FORMAT: limit_1person through limit_8person are columns "
        "on a single row. bedroom_count does not exist. max_rent is a direct column.",
        ["label:Section8AMILimit"],
    ),
    Rule(
        "program_type values: 'VLI' (50% AMI), 'ELI' (30% AMI), 'LI' (80% AMI).",
        ["label:Section8AMILimit"],
    ),

    # ── Label: LenderBehaviorRisk ─────────────────────────────────────────────
    Rule(
        "LenderBehaviorRisk.assessment_year is a STRING, not integer. "
        "Compare with: r.assessment_year = toString($year).",
        ["label:LenderBehaviorRisk"],
    ),
    Rule(
        "Some MetroArea nodes have multiple LenderBehaviorRisk records. "
        "Deduplicate: ORDER BY r.total_applications DESC LIMIT 1.",
        ["label:LenderBehaviorRisk"],
    ),

    # ── Tool: get_hmda_trend ──────────────────────────────────────────────────
    Rule(
        "For trend queries match LenderBehaviorRisk via MetroArea directly: "
        "MATCH (ma:MetroArea {cbsa_code: $cbsa_code})<-[:APPLIES_TO]-(r:LenderBehaviorRisk). "
        "Filter: toInteger(r.assessment_year) >= $start_year AND <= $end_year.",
        ["tool:get_hmda_trend"],
    ),

    # ── General Cypher ────────────────────────────────────────────────────────
    Rule(
        "ORDER BY must come before LIMIT. Correct: RETURN ... ORDER BY x LIMIT 50.",
        ["universal"],
    ),
    Rule(
        "Cypher has no ternary operator. Never write (x ? a : b). "
        "Use: CASE WHEN x THEN a ELSE b END.",
        ["universal"],
    ),
    Rule(
        "is_metro and is_metro_tract are properties on CensusTract only. "
        "MetroArea has no is_metro property.",
        ["label:CensusTract", "label:MetroArea"],
    ),
    Rule(
        "For point-in-time designation checks use designation_year = $year (equality). "
        "Do NOT use designation_year <= $year.",
        ["label:QCTDesignation", "label:SDDADesignation", "label:NMDDADesignation"],
    ),
]


def get_rules_for(
    tool_name: str,
    params: dict,
    node_labels: list[str],
) -> list[str]:
    """Return deduplicated rule texts relevant to this tool call."""
    active_tags = (
        {"universal"}
        | {f"tool:{tool_name}"}
        | {f"param:{k}" for k in params}
        | {f"label:{lbl}" for lbl in node_labels}
    )
    seen: set[str] = set()
    result: list[str] = []
    for rule in RULES:
        if any(tag in active_tags for tag in rule.tags):
            if rule.text not in seen:
                seen.add(rule.text)
                result.append(rule.text)
    return result
