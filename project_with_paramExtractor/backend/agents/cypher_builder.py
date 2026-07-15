"""Agent 3 — Cypher Builder.

Translates ONE tool call (name + params) into a valid Neo4j Cypher query.
Receives a dynamically assembled prompt — only the schema and rules
relevant to the specific tool being called.
"""
from __future__ import annotations

import json
import re
from typing import Any

from backend.registry.tool_registry import TOOLS, build_schema_context
from backend.llm.parser import parse_json_object
from backend.pipeline.rule_library import get_rules_for


class InsufficientParamsError(Exception):
    """Raised when CypherBuilder cannot build a query with the provided params."""
    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        super().__init__(f"Insufficient params — need: {', '.join(missing)}")

_SYSTEM = """\
Generate a read-only Neo4j Cypher query for the LIHTC knowledge graph.

CYPHER STRUCTURE — follow this clause order every time:
  1. MATCH / OPTIONAL MATCH   Find nodes and relationships.
                               Use OPTIONAL MATCH when a pattern may not exist
                               (e.g. a designation that may be absent).
                               Never use comma-joined MATCH for optional patterns.
  2. WHERE                    Filter conditions. Always comes immediately after the
                               MATCH it applies to, not after a later WITH.
  3. WITH                     Pass variables between clauses or aggregate.
                               WITH is never the final clause — RETURN must follow.
  4. RETURN                   Output columns. Always use AS aliases.
  5. ORDER BY                 Sort. Must come after RETURN, before LIMIT.
  6. LIMIT                    Cap result count. Always after ORDER BY if both present.

CYPHER DON'TS:
  - Never end a query with WITH, WHERE, or LIMIT alone — RETURN must be the concluding clause.
  - Never put LIMIT before ORDER BY.
  - Never use a ternary operator (x ? a : b) — Cypher has none. Use CASE WHEN x THEN a ELSE b END.
  - Never reference a variable outside the scope it was introduced in.
  - Never use bare identifiers as map values — {key: value} must be {key: $value} for params.
  - Never invent property names — use only names listed in SCHEMA.
  - Never use $param for ANY key that is not listed in AGENT PARAMS — not even optional params.
    If "limit" is not in AGENT PARAMS, do NOT write LIMIT $limit. Omit the LIMIT clause entirely.
    If "state_fips" is not in AGENT PARAMS, do NOT write $state_fips. Omit that condition entirely.
    Only the exact keys present in AGENT PARAMS may be referenced as $param in the Cypher.
  - Never write (expression) = $param to compare a boolean predicate to a parameter.
    This is invalid Cypher. Inline the condition directly in WHERE instead.
    WRONG: WHERE (q IS NOT NULL) = $is_qct_designated
    RIGHT: WHERE q IS NOT NULL
  - Never use a map literal like {min: ..., max: ...} as a property value inside a
    node/relationship pattern. Cypher has no range-map syntax. min_<attr> and max_<attr>
    are ALWAYS separate WHERE comparisons, never part of the {...} property map in MATCH.
    WRONG: MATCH (q:QCTDesignation {poverty_rate_at_designation: {min: $min_poverty_rate_at_designation}})
    RIGHT: MATCH (q:QCTDesignation) WHERE q.poverty_rate_at_designation >= $min_poverty_rate_at_designation
  - Never add a MATCH, OPTIONAL MATCH, or WHERE condition for an attribute the user
    did not ask about. Only translate keys that are ACTUALLY PRESENT in AGENT PARAMS
    into query conditions. If AGENT PARAMS has no "program_type" key, do NOT filter or
    branch on program_type at all — just omit that condition entirely. If AGENT PARAMS
    has no "max_ranking_ratio" key, do NOT add a ranking_ratio filter. Every filter
    condition in your Cypher must trace back to a key that literally appears in
    AGENT PARAMS below — never add "helpful" extra filters for attributes with no value.
  - Never re-declare the same variable in a second MATCH/OPTIONAL MATCH with a NEW
    label or property map once it is already bound (e.g. matching (q:QCTDesignation)
    once, then later writing another OPTIONAL MATCH (q:QCTDesignation) again). Bind
    each variable exactly once. If you need another condition on it, add a WHERE
    clause referencing the existing variable — do not re-MATCH it.

FILTER HINT PARAMS — translate these AGENT PARAMS into WHERE conditions, not inline values:
  is_qct_designated: true  → use plain MATCH (not OPTIONAL MATCH) for QCTDesignation;
                              adds implicit filter to designated tracts only.
  is_dda_designated: true  → same for SDDADesignation / NMDDADesignation.
  min_<attr>               → separate WHERE clause: WHERE node.<attr> >= $min_<attr>
                              Never put min_<attr>/max_<attr> inside the {...} property map.
  max_<attr>               → separate WHERE clause: WHERE node.<attr> <= $max_<attr>
  Example: min_poverty_rate_at_designation → WHERE q.poverty_rate_at_designation >= $min_poverty_rate_at_designation
  Example: max_income_criterion_ratio      → WHERE q.income_criterion_ratio <= $max_income_criterion_ratio
  These params ARE in AGENT PARAMS and MUST be referenced as $param in the Cypher.
  This list shows the SHAPE of the translation — it does NOT mean these specific
  attributes (ranking_ratio, income_criterion_ratio, program_type, etc.) must always
  be filtered. Only apply min_/max_ translation to keys that literally exist in
  AGENT PARAMS for THIS call.

USING THE USER QUESTION (when given) TO PICK THE RIGHT OPERATOR:
  min_<attr>/max_<attr> default to inclusive (>=, <=), but the question's exact wording
  decides strictness — check it every time a min_/max_ param is present:
    "above X" / "more than X" / "greater than X"        → strict:    > $min_<attr>
    "at least X" / "X or more" / "minimum X"             → inclusive: >= $min_<attr>
    "below X" / "less than X" / "under X"                → strict:    < $max_<attr>
    "at most X" / "X or less" / "no more than X"         → inclusive: <= $max_<attr>
    "between X and Y"                                    → both min_ and max_ present;
                                                            combine with AND, both inclusive
                                                            unless the question says otherwise.
    "exactly X" / "equal to X"                            → use = instead of >=/<=, even if
                                                            the param is named min_<attr>.
  If no question is given, or the wording is ambiguous, default to inclusive (>=, <=).
  Never use the question to invent a value or a param that isn't in AGENT PARAMS —
  it is context for HOW to compare, never a source of WHAT to compare.

INSUFFICIENT PARAMS:
  Only declare insufficient_params if a param listed under REQUIRED PARAMS in the TOOL section
  is completely absent from AGENT PARAMS. Optional params being absent is normal — omit them.
  county_fips is a valid geographic anchor for search_tracts — do NOT require fips_code for it.
  If truly insufficient, return:
  {"cypher": "", "params": {}, "explanation": "", "insufficient_params": ["missing_param"]}
  Do NOT set insufficient_params just because you think more data would help.

OUTPUT FORMAT — read carefully:
  - Output ONLY the single corrected JSON object. No markdown. No ``` fences. No text before or after.
  - Do NOT repeat or quote the previous failed JSON. Do NOT explain what changed. Just output the fix.
  - The "cypher" value MUST be a single JSON string on one logical line.
  - Use \\n (backslash-n, two characters) for newlines in Cypher — NOT actual line breaks.
  - NEVER split the cypher string across multiple lines using Python-style concatenation:
      WRONG: "cypher": "MATCH ct\n"
                        "WHERE ct.x = 1"
      RIGHT: "cypher": "MATCH ct\\nWHERE ct.x = 1"
  {"cypher": "MATCH ... RETURN ...", "params": {"param": value}, "explanation": "one sentence"}
"""

# Strip ALL non-ASCII chars — zero-width chars have no valid use in Cypher.
_UNICODE_RE = re.compile(r"[^\x00-\x7F]")

_BOOLEAN_FIELDS = (
    "is_designated", "is_high_disparity", "is_metro_tract", "is_metro",
    "is_territory", "is_multifamily_constrained", "split_tr_flag",
)
_INTEGER_FIELDS = ("designation_year", "basis_boost_pct", "ami_pct")
_BARE_MAP_RE = re.compile(
    r"\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?!\$)(?!true\b)(?!false\b)(?!null\b)"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*\}"
)
_TERNARY_RE = re.compile(r"(\([^()]+\))\s*\?\s*([^\s:][^:]*?)\s*:\s*([^\s,)]+)")
# Catches: (expr) = $param  e.g.  (q IS NOT NULL) = $is_qct_designated
# The whole clause is a NULL guard — if the param isn't in exec_params, drop it entirely.
_BOOL_COMPARE_RE = re.compile(
    r"\(\s*([^()]+?)\s*\)\s*=\s*(\$[A-Za-z_][A-Za-z0-9_]*)"
)


def _post_process(cypher: str, params: dict[str, Any]) -> str:
    """Deterministic fixes applied after LLM generation."""
    cypher = _UNICODE_RE.sub("", cypher)

    # If LLM generated $limit but limit was not in params, remove the LIMIT clause entirely.
    if "limit" not in params:
        cypher = re.sub(r"\s*LIMIT\s+\$limit\b", "", cypher, flags=re.IGNORECASE)

    for f in _BOOLEAN_FIELDS:
        cypher = re.sub(rf"\b{f}\s*=\s*0\b", f"{f} = false", cypher, flags=re.IGNORECASE)
        cypher = re.sub(rf"\b{f}\s*=\s*1\b", f"{f} = true",  cypher, flags=re.IGNORECASE)

    for f in _INTEGER_FIELDS:
        cypher = re.sub(rf"({f}\s*=\s*)['\"](\d+)['\"]", r"\g<1>\2", cypher)

    cypher = re.sub(
        r"assessment_year\s*=\s*\$year(?![_A-Za-z0-9])",
        "assessment_year = toString($year)", cypher,
    )

    known = set(params.keys())
    def _fix_bare(m: re.Match) -> str:
        key, val = m.group(1), m.group(2)
        return f"{{{key}: ${val}}}" if val in known else m.group(0)
    cypher = _BARE_MAP_RE.sub(_fix_bare, cypher)

    def _fix_ternary(m: re.Match) -> str:
        return f"CASE WHEN {m.group(1)} THEN {m.group(2).strip()} ELSE {m.group(3).strip()} END"
    cypher = _TERNARY_RE.sub(_fix_ternary, cypher)

    # Remove patterns like ($invented_param IS NULL OR (inner_expr) = $invented_param)
    # which the LLM generates for optional boolean filters. Keep only inner_expr.
    def _fix_bool_compare(m: re.Match) -> str:
        param_name = m.group(2).lstrip("$")
        inner = m.group(1).strip()
        if param_name not in params:
            # Invented param — replace whole sub-expression with just the inner predicate
            return inner
        # Param exists — rewrite as CASE WHEN inner THEN true ELSE false END = $param
        return f"CASE WHEN {inner} THEN true ELSE false END = {m.group(2)}"
    # Also strip the NULL-guard wrapper: ($p IS NULL OR <bool_compare>)
    _null_guard_re = re.compile(
        r"\(\s*\$([A-Za-z_][A-Za-z0-9_]*)\s+IS\s+NULL\s+OR\s+(.+?)\s*=\s*\$\1\s*\)",
        re.IGNORECASE,
    )
    def _fix_null_guard(m: re.Match) -> str:
        param_name = m.group(1)
        expr = m.group(2).strip()
        if param_name not in params:
            return expr  # invented param — keep only the inner expression
        return m.group(0)  # real param — leave as-is
    cypher = _null_guard_re.sub(_fix_null_guard, cypher)
    cypher = _BOOL_COMPARE_RE.sub(_fix_bool_compare, cypher)

    alias_to_label: dict[str, str] = {}
    for alias, label in re.findall(
        r"\(([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([A-Za-z_][A-Za-z0-9_]*)", cypher
    ):
        alias_to_label[alias] = label
    ct_aliases  = {a for a, l in alias_to_label.items() if l == "CensusTract"}
    bad_aliases = {a for a, l in alias_to_label.items()
                   if l not in ("CensusTract",) and l in ("MetroArea", "County", "State")}
    ct_alias = next(iter(ct_aliases), "ct")
    for alias in bad_aliases:
        for prop in ("is_metro_tract", "is_metro"):
            cypher = re.sub(rf"\b{alias}\.{prop}\b", f"{ct_alias}.{prop}", cypher)

    cypher = _fix_clause_order(cypher)
    return cypher


def _fix_clause_order(cypher: str) -> str:
    order_m = re.search(r"\bORDER\s+BY\s+.+?(?=\s*(?:SKIP|LIMIT|$))", cypher,
                        re.IGNORECASE | re.DOTALL)
    skip_m  = re.search(r"\bSKIP\s+\S+",  cypher, re.IGNORECASE)
    limit_m = re.search(r"\bLIMIT\s+\S+", cypher, re.IGNORECASE)
    if not (order_m or skip_m or limit_m):
        return cypher
    base = cypher
    for m in sorted([m for m in [order_m, skip_m, limit_m] if m],
                    key=lambda x: x.start(), reverse=True):
        base = base[:m.start()].rstrip() + base[m.end():]
    parts = [
        order_m.group(0).strip() if order_m else "",
        skip_m.group(0).strip()  if skip_m  else "",
        limit_m.group(0).strip() if limit_m else "",
    ]
    suffix = " ".join(p for p in parts if p)
    return base.rstrip() + ("\n" + suffix if suffix else "")


def _build_prompt(tool_name: str, active_params: dict[str, Any], ontology,
                  error: str | None, previous_cypher: str | None = None,
                  question: str = "") -> str:
    tool = TOOLS[tool_name]
    schema_ctx = build_schema_context(tool_name, ontology)
    rules = get_rules_for(tool_name, active_params, tool.node_labels)
    rules_text = "\n".join(f"  {i+1}. {r}" for i, r in enumerate(rules))

    parts = [
        schema_ctx,
        "",
        f"AGENT PARAMS (authoritative values — use these, never invent your own):\n"
        f"{json.dumps(active_params, default=str)}",
    ]
    if question:
        parts += [
            "",
            f"USER QUESTION (context ONLY for comparison intent — e.g. 'between X and Y', "
            f"'exactly N', 'above', 'below', 'at least', 'not equal to'. Do NOT extract new "
            f"values from this text; every value you use must already be in AGENT PARAMS above):\n"
            f"{question}",
        ]
    parts += [
        "",
        f"RULES FOR THIS CALL:\n{rules_text}",
    ]
    if error:
        parts += ["", f"PREVIOUS ATTEMPT FAILED WITH:\n{error}"]
        if previous_cypher:
            parts += ["", f"PREVIOUS CYPHER THAT FAILED:\n{previous_cypher}"]
        if error.startswith("INVALID PARAMS"):
            # Invented/unauthorized $param — patching in place just accumulates more
            # broken structure (seen repeatedly: the model bolts on yet another
            # OPTIONAL MATCH + invented filter instead of removing the bad one).
            # Force a clean rewrite from just the schema + AGENT PARAMS instead.
            parts += [
                "",
                "The previous Cypher used a parameter that does not exist in AGENT PARAMS. "
                "Do NOT patch the previous Cypher — throw it away and write a NEW, SIMPLER "
                "query from scratch using ONLY the keys listed in AGENT PARAMS above. "
                "Do not add any MATCH/WHERE clause for an attribute that has no key in AGENT PARAMS. "
                "Output ONLY the corrected JSON — do NOT repeat the old JSON or include any explanation text.",
            ]
        else:
            parts += [
                "",
                "Fix ONLY the reported error in the previous Cypher above. "
                "Do NOT regenerate the query from scratch — patch the specific issue. "
                "Output ONLY the corrected JSON — do NOT repeat the old JSON or include any explanation text.",
            ]
    return "\n".join(parts)


class CypherBuilder:
    def __init__(self, llm_client) -> None:
        self.llm = llm_client

    def build(
        self,
        tool_name: str,
        params: dict[str, Any],
        ontology,
        error: str | None = None,
        previous_cypher: str | None = None,
        question: str = "",
    ) -> tuple[str, dict[str, Any]]:
        """Generate Cypher for one tool call. Returns (cypher, exec_params).

        previous_cypher: the Cypher from the failed attempt, sent alongside `error`
        on retries so the LLM patches the specific query instead of regenerating blind.
        question: the user's original question, given as context only — for judging
        comparison intent (between/exactly/above/below) that a bare param name can't
        express. The LLM must never pull new values from it; AGENT PARAMS is authoritative.
        """
        # Strip nulls here so both the prompt and exec_params start clean.
        active_params = {k: v for k, v in params.items() if v is not None}
        user_content = _build_prompt(
            tool_name, active_params, ontology, error, previous_cypher, question,
        )
        raw = self.llm.complete(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": user_content},
            ],
            label=f"CypherBuilder:{tool_name}" + (" [retry]" if error else ""),
        )
        data = parse_json_object(raw)

        missing = data.get("insufficient_params")
        if missing:
            raise InsufficientParamsError(missing)

        # exec_params comes ONLY from active_params (the extractor's real values).
        # Do NOT merge in whatever the LLM puts in its own "params" field — that
        # field can contain invented keys/values (e.g. a fabricated fips_code) that
        # would silently execute as if they were real, returning wrong or empty
        # results with no error. The LLM may only reference $keys that exist here.
        exec_params: dict[str, Any] = dict(active_params)

        cypher = _post_process(data.get("cypher", "").strip(), exec_params)
        return cypher, exec_params
