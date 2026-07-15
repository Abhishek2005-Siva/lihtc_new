"""Tool registry — definitions for all tools in the LIHTC knowledge graph.

Each Tool carries only what's needed for schema extraction and Orchestrator routing.
The CypherBuilder LLM derives the query pattern from the schema, relationships,
type constraints, and the general rules in its system prompt — reading values
directly from the user's question rather than a pre-extracted params dict.
No hardcoded Cypher templates or required/optional param lists here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Tool:
    name: str
    description: str
    node_labels: list[str]  # labels this tool touches — used for schema extraction only


TOOLS: dict[str, Tool] = {

    "resolve_geography": Tool(
        name="resolve_geography",
        description=(
            "Resolve a NAMED place (county name, state name/abbreviation, or metro "
            "area name) to its FIPS/CBSA code by looking it up in the graph — never "
            "guess a code from memory. Use this as a FIRST step, with a later step's "
            "depends_on pointing at it, whenever the question names a county, state, "
            "or metro area WITHOUT giving its digit code directly, and the real tool "
            "needed to answer the question requires that code as a parameter. Returns "
            "county_fips and/or state_fips and/or cbsa_code plus the matched name, so "
            "the next step can use the resolved value instead of guessing. Do NOT use "
            "this if the question already states a digit FIPS code directly, or if "
            "the question names a specific tract (11-digit fips_code) — tract-level "
            "identifiers must come from the question text itself, never resolved "
            "this way."
        ),
        node_labels=["County", "State", "MetroArea"],
    ),

    "check_qct": Tool(
        name="check_qct",
        description=(
            "Check QCT designation status for a specific tract and a SINGLE year. "
            "Returns is_designated flag, basis_boost_pct, trigger criterion, "
            "poverty_rate_at_designation, and income_criterion_ratio. "
            "This checks exactly ONE year — it does not accept a year range. If the "
            "question asks about multiple discrete years for the same tract (e.g. "
            "'in 2025 and 2024', 'in 2025 but not 2024'), use ONE check_qct step PER "
            "YEAR, not a single call — do not try to widen the year filter to cover both."
        ),
        node_labels=["CensusTract", "QCTDesignation"],
    ),

    "check_dda": Tool(
        name="check_dda",
        description=(
            "Check DDA status for a year, anchored on whichever geography the "
            "question actually gives a CODE for — a specific tract (11-digit "
            "fips_code, always taken from the question text itself), OR a county "
            "(5-digit county_fips), OR nothing tract-specific at all (anchor "
            "directly on County — no CensusTract needed). REQUIRES county_fips "
            "already resolved to its digit code — if the question only NAMES a "
            "county (e.g. 'Cook County IL') without stating that code, call "
            "resolve_geography FIRST and depends_on it; do not guess the code "
            "yourself. Checks BOTH metro DDA (SDDADesignation via MetroArea) and "
            "non-metro DDA (NMDDADesignation via County). Returns is_dda_designated, "
            "basis_boost_pct, sdda_area_name, nmdda_area_name."
        ),
        node_labels=["CensusTract", "MetroArea", "County", "SDDADesignation", "NMDDADesignation"],
    ),

    "get_ami_limits": Tool(
        name="get_ami_limits",
        description=(
            "Get Section 8 AMI income limits for the county containing a tract, or "
            "for a county directly. Returns all 8 household-size limits "
            "(limit_1person..limit_8person) and max_rent. program_type: VLI (50% AMI, "
            "default), ELI (30% AMI), LI (80% AMI). REQUIRES county_fips already "
            "resolved — if the question only NAMES a county without a digit code, "
            "call resolve_geography FIRST and depends_on it."
        ),
        node_labels=["CensusTract", "County", "Section8AMILimit"],
    ),

    "get_hmda_risk": Tool(
        name="get_hmda_risk",
        description=(
            "Get fair lending risk profile for the metro area containing a tract (single year). "
            "Returns risk_tier, denial rates, and disparity ratio."
        ),
        node_labels=["CensusTract", "MetroArea", "LenderBehaviorRisk"],
    ),

    "get_hmda_trend": Tool(
        name="get_hmda_trend",
        description=(
            "Get fair lending risk trend across multiple years for a tract's metro area. "
            "Use start_year and end_year to define the range."
        ),
        node_labels=["CensusTract", "MetroArea", "LenderBehaviorRisk"],
    ),

    "search_tracts": Tool(
        name="search_tracts",
        description=(
            "Search census tracts by geography (county_fips or state_fips) and designation filters. "
            "is_qct_designated=true returns only QCT-designated tracts. "
            "is_dda_designated=true returns only DDA-designated tracts. "
            "Omitting both returns all tracts with their designation status. "
            "REQUIRES county_fips/state_fips already resolved to digit codes — if "
            "the question only NAMES a county or state, call resolve_geography "
            "FIRST and depends_on it."
        ),
        node_labels=["CensusTract", "QCTDesignation", "SDDADesignation",
                     "NMDDADesignation", "MetroArea", "County"],
    ),

    "search_dda_areas": Tool(
        name="search_dda_areas",
        description=(
            "Find all DDA areas (metro SDDAs and non-metro NMDDAs) for a given year, "
            "optionally filtered by state. Returns area names and basis boost percentages. "
            "REQUIRES state_fips already resolved — if the question only NAMES/"
            "abbreviates a state, call resolve_geography FIRST and depends_on it."
        ),
        node_labels=["SDDADesignation", "NMDDADesignation", "MetroArea", "County", "State"],
    ),

    "search_hmda_risk": Tool(
        name="search_hmda_risk",
        description=(
            "Find metro areas by HMDA fair lending risk for a given year. "
            "Optionally filter by risk_tier or state_fips. REQUIRES state_fips "
            "already resolved — if the question only NAMES/abbreviates a state, "
            "call resolve_geography FIRST and depends_on it."
        ),
        node_labels=["LenderBehaviorRisk", "MetroArea", "State"],
    ),

    "get_state_ami_limits": Tool(
        name="get_state_ami_limits",
        description=(
            "Get STATE-level (not county/metro-level) Section 8 AMI income limits, "
            "reached via State rather than a specific tract's county. Use when the "
            "question explicitly asks for a state's AMI limit, or wants a statewide "
            "figure to compare against a local one. Returns all 8 household-size "
            "limits (limit_1person..limit_8person), max_rent, and program_type "
            "(ELI=30%, VLI=50%, LI=80% AMI). REQUIRES state_fips already resolved — "
            "if the question only NAMES/abbreviates a state, call resolve_geography "
            "FIRST and depends_on it."
        ),
        node_labels=["State", "County", "CensusTract", "StateAMILimit"],
    ),

    "get_special_program_limits": Tool(
        name="get_special_program_limits",
        description=(
            "Get special HUD program income limits (e.g. Section 221 BMIR, Section 235, "
            "Section 236) — NOT the standard Section 8 VLI/ELI/LI tiers. This node type "
            "has NO relationship to CensusTract/County in the graph — match it directly "
            "by its own hud_fmr_area_code, area_name, or state property, not via a tract "
            "traversal. Returns all 8 household-size limits and max_income. If filtering "
            "by state, REQUIRES the 2-digit state_fips already resolved — if the question "
            "only NAMES/abbreviates a state, call resolve_geography FIRST and depends_on it."
        ),
        node_labels=["SpecialProgramLimit"],
    ),

    "custom_query": Tool(
        name="custom_query",
        description=(
            "LAST RESORT fallback — use ONLY when the question genuinely does not match "
            "any other tool's description above. Gives access to the full graph schema "
            "(every node label and relationship) instead of one tool's narrow slice, so "
            "it can answer questions about node types or combinations the other tools "
            "don't specifically cover. Do not use this for anything a more specific tool "
            "already handles — prefer the specific tool whenever one applies."
        ),
        node_labels=[],  # sentinel: build_schema_context() falls back to ALL ontology labels
    ),
}


# ---------------------------------------------------------------------------
# Schema context — used by CypherBuilder
# ---------------------------------------------------------------------------

def build_schema_context(tool_name: str, ontology) -> str:
    """Build the schema section of the CypherBuilder user prompt.

    Contains: tool name + purpose, exact property lists, relationships,
    type constraints, and graph quirks. No Cypher templates or param lists.
    The LLM derives the query pattern from this plus the user's raw question.
    """
    tool = TOOLS[tool_name]
    # custom_query has no fixed node_labels (empty list = sentinel) — it's the
    # generic fallback, so show the FULL graph schema instead of one narrow slice.
    target_labels = tool.node_labels or list(ontology.labels)

    lines: list[str] = [
        f"TOOL: {tool_name}",
        f"PURPOSE: {tool.description}",
        "",
        "EXACT SCHEMA — use ONLY these property names, do not invent others:",
    ]

    for label in target_labels:
        pk = ontology.primary_keys.get(label)
        props = ontology.properties_by_label.get(label, [])
        pk_note = f"  pk={pk}" if pk else ""
        lines.append(f"  {label}{pk_note}: {props}")

    tool_labels = set(target_labels)
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
    for label in target_labels:
        schema = ontology.property_schemas.get(label, {})
        for prop, meta in schema.items():
            neo4j_type = meta.get("neo4j_type")
            comparison = meta.get("comparison")
            padding    = meta.get("padding")
            quirk      = meta.get("quirk")
            if neo4j_type and neo4j_type != "STRING":
                note = f"neo4j_type={neo4j_type}"
                if comparison: note += f" | {comparison}"
                if padding:    note += f" | {padding}"
                if quirk:      note += f" | NOTE: {quirk}"
                type_lines.append(f"  {label}.{prop}: {note}")
            elif quirk or padding:
                note = "neo4j_type=STRING"
                if padding: note += f" | {padding}"
                if quirk:   note += f" | NOTE: {quirk}"
                type_lines.append(f"  {label}.{prop}: {note}")
    if type_lines:
        lines.append("")
        lines.append("TYPE CONSTRAINTS (critical for correct WHERE clauses):")
        lines.extend(type_lines)

    all_quirks: list[str] = ontology.quirks if hasattr(ontology, "quirks") else []
    relevant_quirks = [
        q for q in all_quirks
        if any(label in q for label in target_labels)
    ]
    if relevant_quirks:
        lines.append("")
        lines.append("GRAPH QUIRKS (read carefully):")
        for q in relevant_quirks:
            lines.append(f"  - {q}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Prompt block shown to Orchestrator — tool names + descriptions only
# ---------------------------------------------------------------------------

def tools_prompt_block() -> str:
    return "\n".join(
        f"  {tool.name}: {tool.description}"
        for tool in TOOLS.values()
    )


# ---------------------------------------------------------------------------
# Fact extraction — keys populated in Scratchpad after each tool call
# ---------------------------------------------------------------------------

def extract_facts(tool_name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    row = rows[0]
    facts: dict[str, Any] = {}

    if tool_name == "check_qct":
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
