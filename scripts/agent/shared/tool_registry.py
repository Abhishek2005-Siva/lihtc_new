"""Tool registry — auto-generated from the ontology at startup.

Level 2 abstract tools are defined here (what the Orchestrator picks from).
Property names are NEVER hardcoded — build_schema_context() extracts them
from ontology.properties_by_label at call time, so the Cypher Builder always
sees the ground-truth property list.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Tool:
    name: str
    description: str
    params: list[str]
    optional_params: list[str]
    node_labels: list[str]     # labels this tool touches — used for schema extraction
    query_hint: str            # traversal + WHERE logic + return aliases for Cypher Builder
                               # describes STRUCTURE only — property names left to schema


TOOLS: dict[str, Tool] = {

    "get_tract_context": Tool(
        name="get_tract_context",
        description=(
            "Get geographic context for a census tract: metro/rural status, county, "
            "state, and CBSA/metro area. Always call this first when the question involves "
            "a census tract — it populates county_fips and cbsa_code that other tools need."
        ),
        params=["fips_code"],
        optional_params=[],
        node_labels=["CensusTract", "County", "State", "MetroArea"],
        query_hint=(
            "MATCH (ct:CensusTract) by fips_code.\n"
            "OPTIONAL MATCH (ct)-[:IN_COUNTY]->(co:County)-[:IN_STATE]->(st:State).\n"
            "OPTIONAL MATCH (ct)-[:IN_METRO]->(ma:MetroArea).\n"
            "RETURN the metro/rural boolean flag AS is_metro_tract, "
            "ct.county_fips AS county_fips, ct.fips_code AS fips_code, "
            "the county name property AS county_name, "
            "st.state_fips AS state_fips, the state name property AS state_name, "
            "ma.cbsa_code AS cbsa_code, the metro name/title property AS metro_name."
        ),
    ),

    "check_qct": Tool(
        name="check_qct",
        description=(
            "Check QCT (Qualified Census Tract) designation status for a tract and year. "
            "Returns is_designated flag, basis_boost_pct, and trigger criterion."
        ),
        params=["fips_code", "year"],
        optional_params=[],
        node_labels=["CensusTract", "QCTDesignation"],
        query_hint=(
            "MATCH (ct:CensusTract) by fips_code.\n"
            "OPTIONAL MATCH (q:QCTDesignation)-[:APPLIES_TO]->(ct) "
            "WHERE q.is_designated = true AND q.designation_year = $year.\n"
            "RETURN q IS NOT NULL AS is_designated, "
            "q.basis_boost_pct AS basis_boost_pct, "
            "the trigger criterion field AS trigger_criterion, "
            "q.poverty_rate_at_designation AS poverty_rate_at_designation, "
            "q.income_criterion_ratio AS income_criterion_ratio."
        ),
    ),

    "check_dda": Tool(
        name="check_dda",
        description=(
            "Check DDA (Difficult Development Area) status for a tract and year. "
            "Checks BOTH metro DDA (SDDADesignation via MetroArea) and non-metro DDA "
            "(NMDDADesignation via County). Use fips_code — traverses internally."
        ),
        params=["fips_code", "year"],
        optional_params=[],
        node_labels=["CensusTract", "MetroArea", "County", "SDDADesignation", "NMDDADesignation"],
        query_hint=(
            "MATCH (ct:CensusTract) by fips_code.\n"
            "OPTIONAL MATCH (ct)-[:IN_METRO]->(ma:MetroArea)<-[:APPLIES_TO]-(s:SDDADesignation) "
            "WHERE s.is_designated = true AND s.designation_year = $year.\n"
            "OPTIONAL MATCH (ct)-[:IN_COUNTY]->(co:County)<-[:APPLIES_TO]-(n:NMDDADesignation) "
            "WHERE n.is_designated = true AND n.designation_year = $year.\n"
            "RETURN s IS NOT NULL AS sdda_designated, n IS NOT NULL AS nmdda_designated, "
            "(s IS NOT NULL OR n IS NOT NULL) AS is_dda_designated, "
            "COALESCE(s.basis_boost_pct, n.basis_boost_pct) AS basis_boost_pct, "
            "the SDDA area name AS sdda_area_name, the NMDDA area name AS nmdda_area_name."
        ),
    ),

    "get_ami_limits": Tool(
        name="get_ami_limits",
        description=(
            "Get Section 8 AMI income limits for the area containing a tract. "
            "Returns all 8 household-size limits (limit_1person through limit_8person) and max_rent. "
            "program_type: 'VLI' (50% AMI, default), 'ELI' (30% AMI), 'LI' (80% AMI)."
        ),
        params=["fips_code", "year"],
        optional_params=["program_type"],
        node_labels=["CensusTract", "County", "Section8AMILimit"],
        query_hint=(
            "MATCH (ct:CensusTract {fips_code: $fips_code})-[:IN_COUNTY]->(co:County).\n"
            "MATCH (co)-[:HAS_MSA_AMI]->(a:Section8AMILimit) "
            "WHERE a.year = $year AND a.program_type = $program_type.\n"
            "RETURN all limit_Nperson columns AS limit_1person ... limit_8person, "
            "a.max_rent AS max_rent, the area name AS area_name, "
            "a.program_type AS program_type, the MFI/median income field AS mfi_value."
        ),
    ),

    "get_hmda_risk": Tool(
        name="get_hmda_risk",
        description=(
            "Get fair lending risk profile for the metro area containing a tract "
            "(single year). Pass fips_code — traverses to MetroArea internally. "
            "Returns risk_tier, denial rates, and disparity ratio."
        ),
        params=["fips_code", "year"],
        optional_params=[],
        node_labels=["CensusTract", "MetroArea", "LenderBehaviorRisk"],
        query_hint=(
            "MATCH (ct:CensusTract {fips_code: $fips_code})-[:IN_METRO]->(ma:MetroArea)"
            "<-[:APPLIES_TO]-(r:LenderBehaviorRisk) "
            "WHERE r.assessment_year = toString($year).\n"
            "RETURN r.risk_tier AS risk_tier, denial_rate_disparity_ratio, denial_rate_overall, "
            "denial_rate_minority, denial_rate_white, multifamily_denial_rate, "
            "is_high_disparity, fair_lending_risk_score.\n"
            "Dedup: ORDER BY total_applications DESC LIMIT 1."
        ),
    ),

    "get_hmda_trend": Tool(
        name="get_hmda_trend",
        description=(
            "Get fair lending risk trend across multiple years for a tract's metro area. "
            "Pass fips_code — traverses to MetroArea internally."
        ),
        params=["fips_code", "start_year", "end_year"],
        optional_params=[],
        node_labels=["CensusTract", "MetroArea", "LenderBehaviorRisk"],
        query_hint=(
            "MATCH (ct:CensusTract {fips_code: $fips_code})-[:IN_METRO]->(ma:MetroArea)"
            "<-[:APPLIES_TO]-(r:LenderBehaviorRisk) "
            "WHERE toInteger(r.assessment_year) >= $start_year "
            "AND toInteger(r.assessment_year) <= $end_year.\n"
            "RETURN r.assessment_year AS year, risk_tier, denial_rate_overall, "
            "denial_rate_disparity_ratio, denial_rate_minority.\n"
            "ORDER BY year ASC."
        ),
    ),

    "search_tracts": Tool(
        name="search_tracts",
        description=(
            "Search for census tracts matching geographic and designation filters. "
            "Filter by county_fips OR state_fips (not both). "
            "is_qct_designated and is_dda_designated are optional boolean filters."
        ),
        params=["year"],
        optional_params=["county_fips", "state_fips", "is_qct_designated",
                         "is_dda_designated", "limit"],
        node_labels=["CensusTract", "QCTDesignation", "SDDADesignation",
                     "NMDDADesignation", "MetroArea", "County"],
        query_hint=(
            "MATCH (ct:CensusTract) "
            "WHERE ($county_fips IS NULL OR ct.county_fips = $county_fips) "
            "AND ($state_fips IS NULL OR ct.state_fips = $state_fips).\n"
            "OPTIONAL MATCH (q:QCTDesignation)-[:APPLIES_TO]->(ct) "
            "WHERE q.is_designated = true AND q.designation_year = $year.\n"
            "OPTIONAL MATCH (ct)-[:IN_METRO]->(ma)<-[:APPLIES_TO]-(s:SDDADesignation) "
            "WHERE s.is_designated = true AND s.designation_year = $year.\n"
            "OPTIONAL MATCH (ct)-[:IN_COUNTY]->(co)<-[:APPLIES_TO]-(n:NMDDADesignation) "
            "WHERE n.is_designated = true AND n.designation_year = $year.\n"
            "WITH ct, q, s, n WHERE ($is_qct_designated IS NULL OR (q IS NOT NULL) = $is_qct_designated) "
            "AND ($is_dda_designated IS NULL OR (s IS NOT NULL OR n IS NOT NULL) = $is_dda_designated).\n"
            "RETURN ct.fips_code AS fips_code, q IS NOT NULL AS is_qct, "
            "(s IS NOT NULL OR n IS NOT NULL) AS is_dda, "
            "COALESCE(q.basis_boost_pct, s.basis_boost_pct, n.basis_boost_pct) AS basis_boost_pct.\n"
            "LIMIT $limit."
        ),
    ),

    "search_dda_areas": Tool(
        name="search_dda_areas",
        description=(
            "Find all DDA areas (metro SDDAs and non-metro NMDDAs) for a given year, "
            "optionally filtered by state. Returns area names and basis boost percentages."
        ),
        params=["year"],
        optional_params=["state_fips", "limit"],
        node_labels=["SDDADesignation", "NMDDADesignation", "MetroArea", "County", "State"],
        query_hint=(
            "MATCH (s:SDDADesignation)-[:APPLIES_TO]->(ma:MetroArea) "
            "WHERE s.is_designated = true AND s.designation_year = $year.\n"
            "OPTIONAL MATCH (ma)-[:IN_STATE]->(st:State) "
            "WHERE $state_fips IS NULL OR st.state_fips = $state_fips.\n"
            "RETURN 'metro' AS dda_type, ma.cbsa_code AS area_id, "
            "the metro area name AS area_name, s.basis_boost_pct AS basis_boost_pct.\n"
            "UNION\n"
            "MATCH (n:NMDDADesignation)-[:APPLIES_TO]->(co:County)-[:IN_STATE]->(st:State) "
            "WHERE n.is_designated = true AND n.designation_year = $year "
            "AND ($state_fips IS NULL OR st.state_fips = $state_fips).\n"
            "RETURN 'non_metro' AS dda_type, co.county_fips AS area_id, "
            "co.county_name AS area_name, n.basis_boost_pct AS basis_boost_pct.\n"
            "LIMIT $limit."
        ),
    ),

    "search_hmda_risk": Tool(
        name="search_hmda_risk",
        description=(
            "Find metro areas with high HMDA fair lending risk for a given year. "
            "Optionally filter by risk_tier ('HIGH', 'ELEVATED', 'MODERATE', 'LOW') or state_fips."
        ),
        params=["year"],
        optional_params=["risk_tier", "state_fips", "limit"],
        node_labels=["LenderBehaviorRisk", "MetroArea", "State"],
        query_hint=(
            "MATCH (ma:MetroArea)<-[:APPLIES_TO]-(r:LenderBehaviorRisk) "
            "WHERE r.assessment_year = toString($year) "
            "AND ($risk_tier IS NULL OR r.risk_tier = $risk_tier).\n"
            "OPTIONAL MATCH (ma)-[:IN_STATE]->(st:State) "
            "WHERE $state_fips IS NULL OR st.state_fips = $state_fips.\n"
            "RETURN ma.cbsa_code AS cbsa_code, the metro name AS metro_name, "
            "r.risk_tier AS risk_tier, r.denial_rate_disparity_ratio AS disparity_ratio, "
            "r.denial_rate_overall AS denial_rate_overall, r.is_high_disparity AS is_high_disparity.\n"
            "ORDER BY r.denial_rate_disparity_ratio DESC.\n"
            "LIMIT $limit."
        ),
    ),
}


# ---------------------------------------------------------------------------
# Schema context builder — used by Cypher Builder
# ---------------------------------------------------------------------------

def build_schema_context(tool_name: str, ontology) -> str:
    """Extract exact schema info for a tool from the ontology.

    The Cypher Builder LLM receives this — property names come directly from
    ontology.properties_by_label, never from hardcoded strings.
    """
    tool = TOOLS[tool_name]
    lines: list[str] = [
        f"TOOL: {tool_name}",
        f"PURPOSE: {tool.description}",
        "",
        "QUERY PATTERN (follow this traversal and return structure):",
        tool.query_hint,
        "",
        "EXACT SCHEMA — use ONLY these property names, do not invent others:",
    ]

    tool_labels = set(tool.node_labels)
    for label in tool.node_labels:
        pk = ontology.primary_keys.get(label)
        props = ontology.properties_by_label.get(label, [])
        pk_note = f"  pk={pk}" if pk else ""
        lines.append(f"  {label}{pk_note}: {props}")

    relevant_rels = [
        f"  (:{r['from']})-[:{r['type']}]->(:{r['to']})"
        for r in ontology.relationship_patterns
        if r["from"] in tool_labels and r["to"] in tool_labels
    ]
    if relevant_rels:
        lines.append("")
        lines.append("RELEVANT RELATIONSHIPS:")
        lines.extend(relevant_rels)

    type_lines: list[str] = []
    for label in tool.node_labels:
        schema = ontology.property_schemas.get(label, {})
        for prop, meta in schema.items():
            neo4j_type = meta.get("neo4j_type")
            comparison = meta.get("comparison")
            if neo4j_type and neo4j_type != "STRING":
                note = f"neo4j_type={neo4j_type}"
                if comparison:
                    note += f" | {comparison}"
                type_lines.append(f"  {label}.{prop}: {note}")
    if type_lines:
        lines.append("")
        lines.append("TYPE CONSTRAINTS (critical for correct WHERE clauses):")
        lines.extend(type_lines)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Fact extraction — what goes into Scratchpad.known_facts after each tool call
# ---------------------------------------------------------------------------

def extract_facts(tool_name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Pull the most useful scalar facts from a tool result."""
    if not rows:
        return {}
    row = rows[0]
    facts: dict[str, Any] = {}

    if tool_name == "get_tract_context":
        for k in ["is_metro_tract", "county_fips", "cbsa_code",
                  "state_fips", "county_name", "state_name", "metro_name", "fips_code"]:
            if row.get(k) is not None:
                facts[k] = row[k]

    elif tool_name == "check_qct":
        facts["qct_is_designated"]     = row.get("is_designated", False)
        facts["qct_basis_boost_pct"]   = row.get("basis_boost_pct")
        facts["qct_trigger_criterion"] = row.get("trigger_criterion")

    elif tool_name == "check_dda":
        facts["dda_is_designated"]   = row.get("is_dda_designated", False)
        facts["sdda_designated"]     = row.get("sdda_designated", False)
        facts["nmdda_designated"]    = row.get("nmdda_designated", False)
        facts["dda_basis_boost_pct"] = row.get("basis_boost_pct")

    elif tool_name == "get_ami_limits":
        facts["ami_limit_4person"] = row.get("limit_4person")
        facts["ami_max_rent"]      = row.get("max_rent")
        facts["ami_area_name"]     = row.get("area_name")

    elif tool_name == "get_hmda_risk":
        facts["hmda_risk_tier"]         = row.get("risk_tier")
        facts["hmda_disparity_ratio"]   = row.get("denial_rate_disparity_ratio")
        facts["hmda_is_high_disparity"] = row.get("is_high_disparity")

    return facts


# ---------------------------------------------------------------------------
# Prompt block — what the Orchestrator sees in its tool list
# ---------------------------------------------------------------------------

def tools_prompt_block() -> str:
    lines = []
    for tool in TOOLS.values():
        params = ", ".join(tool.params + [f"[{p}]" for p in tool.optional_params])
        lines.append(f"  {tool.name}({params}): {tool.description}")
    return "\n".join(lines)
