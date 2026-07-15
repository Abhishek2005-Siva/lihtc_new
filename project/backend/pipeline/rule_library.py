"""Rule library — tagged Cypher rules for dynamic prompt assembly.

Rules are tagged by: tool name, param name, node label, or "universal".
The Cypher Builder receives only the rules relevant to its current tool call.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

_THIS_YEAR = datetime.now().year


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

    # ── Geographic identifiers ───────────────────────────────────────────────
    Rule(
        "fips_code is an 11-digit STRING (e.g. '01001020700'). Never confuse with "
        "county_fips (5-digit).",
        ["universal"],
    ),
    Rule(
        "county_fips is a 5-digit STRING (e.g. '48113'). If the question STATES the "
        "5-digit code directly, filter tracts with: "
        "MATCH (ct:CensusTract {county_fips: $county_fips}) -- no County traversal needed. "
        "If the question instead NAMES a county (e.g. 'Cook County IL', 'Dallas County TX'), "
        "do NOT guess its FIPS code from memory — a misremembered code silently matches the "
        "wrong county with no error. Resolve it via the graph instead: County has a "
        "county_name property (e.g. 'Cook County') and its own state_fips property, so "
        "MATCH (co:County) WHERE toLower(co.county_name) CONTAINS toLower($county_name) "
        "AND co.state_fips = $state_fips, then use co.county_fips or traverse from co "
        "directly. Resolve $state_fips the same way if the state is only named/abbreviated "
        "(not given as a digit code) — MATCH (st:State) WHERE st.state_abbr = $state_abbr "
        "OR toLower(st.state_name) = toLower($state_name), then use st.state_fips.",
        ["universal"],
    ),
    Rule(
        f"designation_year, year (AMI), and basis_boost_pct are INTEGER — never quote them. "
        f"Write: q.designation_year = $year  (not = '2025'). If the question doesn't state a "
        f"year, default $year to {_THIS_YEAR} (the current year).",
        ["universal"],
    ),

    # ── Tool: resolve_geography ──────────────────────────────────────────────
    Rule(
        "resolve_geography: match the named entity's text property directly and "
        "return its code(s) — never invent a code yourself here either, only find "
        "it via a real property match. "
        "For a county WITH a state named too (the usual case — 'Cook County IL'), "
        "County and State MUST be joined via the real IN_STATE relationship in "
        "ONE connected MATCH — do NOT match County and State independently and "
        "compare their state_fips values, that is a disconnected-match/cartesian "
        "bug. Correct shape: "
        "MATCH (co:County)-[:IN_STATE]->(st:State) "
        "WHERE toLower(co.county_name) CONTAINS toLower($county_name) "
        "AND (st.state_abbr = $state_abbr OR toLower(st.state_name) = toLower($state_name)) "
        "RETURN co.county_fips AS county_fips, co.state_fips AS state_fips, "
        "co.county_name AS county_name. "
        "For a county with NO state named at all (rare/ambiguous — county names can "
        "repeat across states): MATCH (co:County) WHERE toLower(co.county_name) "
        "CONTAINS toLower($county_name) RETURN co.county_fips AS county_fips, "
        "co.state_fips AS state_fips, co.county_name AS county_name. "
        "For a state alone (no county named): "
        "MATCH (st:State) WHERE st.state_abbr = $state_abbr OR toLower(st.state_name) "
        "= toLower($state_name) RETURN st.state_fips AS state_fips. "
        "For a metro area: MATCH (m:MetroArea) WHERE toLower(m.metro_name) CONTAINS "
        "toLower($metro_name) RETURN m.cbsa_code AS cbsa_code, m.metro_name AS metro_name.",
        ["tool:resolve_geography"],
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
        "If the question implies a poverty-rate threshold (e.g. 'above 25%'), add: "
        "AND q.poverty_rate_at_designation >= $min_poverty_rate_at_designation in the WHERE clause "
        "(name the param min_poverty_rate_at_designation / max_poverty_rate_at_designation).",
        ["label:QCTDesignation"],
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
    Rule(
        "If the question names ONLY a county (e.g. 'Cook County IL') with no "
        "specific tract, do NOT match CensusTract at all and do NOT invent a "
        "fake tract fips_code (e.g. reusing the 5-digit county_fips as an "
        "11-digit fips_code — these are different fields, never equal). Anchor "
        "directly on County instead: "
        "MATCH (co:County {county_fips: $county_fips}) "
        "OPTIONAL MATCH (co)<-[:APPLIES_TO]-(n:NMDDADesignation) WHERE n.designation_year = $year AND n.is_designated = true "
        "OPTIONAL MATCH (co)-[:IN_METRO]->(ma:MetroArea)<-[:APPLIES_TO]-(s:SDDADesignation) WHERE s.designation_year = $year AND s.is_designated = true. "
        "Only match CensusTract when the question actually gives a specific "
        "11-digit tract fips_code.",
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
        "LenderBehaviorRisk.assessment_year is an INTEGER (e.g. 2025), not a "
        "string. Compare directly: r.assessment_year = $year — never wrap it "
        "in toString().",
        ["label:LenderBehaviorRisk"],
    ),
    Rule(
        "Some MetroArea nodes have multiple LenderBehaviorRisk records. "
        "Deduplicate: ORDER BY r.total_applications DESC LIMIT 1.",
        ["label:LenderBehaviorRisk"],
    ),

    # ── Tool: get_hmda_trend ──────────────────────────────────────────────────
    Rule(
        "For trend queries, reach the MetroArea via the named entity in the "
        "question (a tract or county) and traverse IN_METRO — do NOT filter "
        "MetroArea by a guessed cbsa_code, since that opaque HUD code cannot be "
        "derived from a place name. E.g. MATCH (ct:CensusTract {fips_code: "
        "$fips_code})-[:IN_METRO]->(ma:MetroArea)<-[:APPLIES_TO]-(r:LenderBehaviorRisk). "
        "Filter: r.assessment_year >= $start_year AND r.assessment_year <= $end_year "
        "(assessment_year is an INTEGER — compare directly, no toInteger()/toString()).",
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
        "is_metro is a property on CensusTract (ct.is_metro). is_metro_tract is a "
        "DIFFERENT property that exists ONLY on QCTDesignation (q.is_metro_tract) — "
        "CensusTract has NO is_metro_tract property. MetroArea has no is_metro property either.",
        ["label:CensusTract", "label:MetroArea", "label:QCTDesignation"],
    ),
    Rule(
        "For point-in-time designation checks use designation_year = $year (equality). "
        "Do NOT use designation_year <= $year.",
        ["label:QCTDesignation", "label:SDDADesignation", "label:NMDDADesignation"],
    ),
]


def get_rules_for(
    tool_name: str,
    node_labels: list[str],
) -> list[str]:
    """Return deduplicated rule texts relevant to this tool call.

    No longer filters by param presence — CypherBuilder extracts its own values
    from the raw question, so there's no pre-known params dict to filter against.
    Rules are selected purely by tool name and the node labels the tool touches.
    """
    active_tags = (
        {"universal"}
        | {f"tool:{tool_name}"}
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
