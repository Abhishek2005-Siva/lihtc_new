"""Type Checker — deterministic property type enforcement.

Scans generated Cypher for property comparisons and fixes type mismatches
using a lookup table. No LLM. Pure string manipulation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


# Maps property name → expected neo4j type
_PROP_TYPES: dict[str, str] = {
    # INTEGER
    "designation_year": "INTEGER",
    "basis_boost_pct":  "INTEGER",
    "ami_pct":          "INTEGER",
    "limit_1person":    "INTEGER",
    "limit_2person":    "INTEGER",
    "limit_3person":    "INTEGER",
    "limit_4person":    "INTEGER",
    "limit_5person":    "INTEGER",
    "limit_6person":    "INTEGER",
    "limit_7person":    "INTEGER",
    "limit_8person":    "INTEGER",
    "max_rent":         "INTEGER",
    # STRING (these should never be unquoted bare integers)
    "fips_code":        "STRING",
    "county_fips":      "STRING",
    "state_fips":       "STRING",
    "cbsa_code":        "STRING",
    "assessment_year":  "STRING",   # special — must use toString($year)
    # BOOLEAN
    "is_designated":              "BOOLEAN",
    "is_high_disparity":          "BOOLEAN",
    "is_metro_tract":             "BOOLEAN",
    "is_metro":                   "BOOLEAN",
    "is_territory":               "BOOLEAN",
    "is_multifamily_constrained": "BOOLEAN",
    "split_tr_flag":              "BOOLEAN",
}

_BOOLEAN_FIELDS = {k for k, v in _PROP_TYPES.items() if v == "BOOLEAN"}
_INTEGER_FIELDS = {k for k, v in _PROP_TYPES.items() if v == "INTEGER"}
_STRING_FIELDS  = {k for k, v in _PROP_TYPES.items() if v == "STRING"}


@dataclass
class TypeCheckResult:
    cypher: str
    fixes: list[str] = field(default_factory=list)


# Catches the anti-pattern: attr: {min: ..., max: ...} used as a node/rel property
# value in a MATCH pattern. Cypher has no such syntax — min_/max_ filters must be
# separate WHERE comparisons. The raw Neo4j syntax error for this is a generic
# "expected an expression" message that doesn't tell the LLM what's actually wrong,
# so retries kept regenerating the same broken shape. This gives a precise fix.
_MAP_RANGE_RE = re.compile(r"(\w+)\s*:\s*\{\s*(min|max)\s*:", re.IGNORECASE)


# Extracts every $param reference in generated Cypher.
_PARAM_REF_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


def find_unauthorized_params(cypher: str, allowed: set[str]) -> str | None:
    """Return an actionable error if the Cypher references a $param not in `allowed`.

    `allowed` must be exactly the extractor's real AGENT PARAMS keys — exec_params
    is built only from those, so any $param outside this set is either a typo or
    a value the LLM invented (e.g. a fabricated fips_code). Either way Neo4j will
    receive no binding for it; catching it here gives a precise fix instead of a
    generic Neo4j "missing parameter" error.
    """
    used = {m.group(1) for m in _PARAM_REF_RE.finditer(cypher)}
    unauthorized = sorted(used - allowed)
    if not unauthorized:
        return None
    return (
        f"INVALID PARAMS: the Cypher references {', '.join('$' + p for p in unauthorized)} "
        f"which {'is' if len(unauthorized) == 1 else 'are'} NOT in AGENT PARAMS. "
        f"You may ONLY reference these exact keys as $param: {sorted(allowed)}. "
        f"Do not invent new param names or values — remove the condition(s) using "
        f"{', '.join('$' + p for p in unauthorized)} entirely, or replace with a key "
        f"that is actually present in AGENT PARAMS."
    )


# Extracts the RETURN clause body (up to ORDER BY / LIMIT / end of string).
_RETURN_CLAUSE_RE = re.compile(
    r"\bRETURN\s+(.+?)(?=\r?\n\s*(?:ORDER\s+BY|LIMIT)\b|$)", re.IGNORECASE | re.DOTALL,
)


def find_missing_filter_columns(cypher: str, param_keys) -> str | None:
    """If the query filters on min_<attr>/max_<attr>, that attribute must appear
    in the RETURN clause — otherwise the user asked to filter on a value they can
    never see in the result, which validators have been scoring as "relevant"
    even though the answer is incomplete (e.g. filtering poverty_rate but never
    returning it). Returns an actionable error if an attribute is missing.
    """
    m = _RETURN_CLAUSE_RE.search(cypher)
    return_body = m.group(1) if m else ""

    missing: list[str] = []
    for key in param_keys:
        attr = None
        if key.startswith("min_"):
            attr = key[len("min_"):]
        elif key.startswith("max_"):
            attr = key[len("max_"):]
        if attr and not re.search(rf"\b{re.escape(attr)}\b", return_body):
            missing.append(attr)

    if not missing:
        return None
    uniq = sorted(set(missing))
    return (
        f"MISSING RETURN COLUMN: the query filters on {', '.join(uniq)} but does NOT "
        f"return {'it' if len(uniq) == 1 else 'them'} in the RETURN clause. "
        f"The user asked to filter by {'this' if len(uniq) == 1 else 'these'} value(s), "
        f"so {'it' if len(uniq) == 1 else 'they'} must be visible in the output. "
        f"Add {', '.join(f'<alias>.{a} AS {a}' for a in uniq)} to the RETURN clause."
    )


# Catches two consecutive WHERE clauses (invalid Cypher) — merges them with AND.
# The LLM does this when patching a retry: it appends a new WHERE instead of
# combining with the existing one.
_DOUBLE_WHERE_RE = re.compile(
    r"\bWHERE\s+(.+?)\r?\n\s*WHERE\s+(.+?)(?=\r?\n\s*(?:WITH|RETURN|OPTIONAL\s+MATCH|MATCH|ORDER\s+BY|LIMIT)\b|$)",
    re.IGNORECASE | re.DOTALL,
)


def fix_double_where(cypher: str) -> str:
    def _merge(m: re.Match) -> str:
        return f"WHERE ({m.group(1).strip()}) AND ({m.group(2).strip()})"
    return _DOUBLE_WHERE_RE.sub(_merge, cypher)


def find_map_range_filter(cypher: str) -> str | None:
    """Return an actionable error message if the map-literal range anti-pattern
    is present, else None."""
    m = _MAP_RANGE_RE.search(cypher)
    if not m:
        return None
    attr = m.group(1)
    return (
        f"INVALID CYPHER SYNTAX: '{attr}: {{min: ..., max: ...}}' is not valid Cypher — "
        f"a map literal cannot be used as a node/relationship property value for a range filter. "
        f"Remove '{attr}: {{...}}' entirely from the {{...}} property pattern in MATCH. "
        f"Instead add separate WHERE conditions after the MATCH: "
        f"WHERE node.{attr} >= $min_{attr} AND node.{attr} <= $max_{attr} "
        f"(include only the min_/max_ clause whose param is actually present in AGENT PARAMS)."
    )


def check_and_fix(cypher: str) -> TypeCheckResult:
    fixes: list[str] = []

    # INTEGER fields — remove quotes
    for f in _INTEGER_FIELDS:
        new = re.sub(rf"({f}\s*=\s*)['\"](\d+)['\"]", r"\g<1>\2", cypher)
        if new != cypher:
            fixes.append(f"{f}: removed quotes (INTEGER)")
            cypher = new

    # BOOLEAN = 0/1 → true/false
    for f in _BOOLEAN_FIELDS:
        new = re.sub(rf"\b{f}\s*=\s*0\b", f"{f} = false", cypher, flags=re.IGNORECASE)
        if new != cypher:
            fixes.append(f"{f}: 0 → false (BOOLEAN)")
            cypher = new
        new = re.sub(rf"\b{f}\s*=\s*1\b", f"{f} = true", cypher, flags=re.IGNORECASE)
        if new != cypher:
            fixes.append(f"{f}: 1 → true (BOOLEAN)")
            cypher = new

    # STRING fips fields — add quotes around bare integers
    for f in ("fips_code", "county_fips", "state_fips", "cbsa_code"):
        new = re.sub(rf"({f}\s*=\s*)(\d+)(?!\s*['\"])", r"\g<1>'\g<2>'", cypher)
        if new != cypher:
            fixes.append(f"{f}: added quotes (STRING)")
            cypher = new

    # assessment_year — must use toString($year) not bare $year
    new = re.sub(
        r"assessment_year\s*=\s*\$year(?![_A-Za-z0-9])",
        "assessment_year = toString($year)", cypher,
    )
    if new != cypher:
        fixes.append("assessment_year: wrapped in toString() (STRING)")
        cypher = new

    # assessment_year — bare integer (not param) → quoted string
    new = re.sub(r"(assessment_year\s*=\s*)(\d{4})(?!\s*['\"])", r"\g<1>'\g<2>'", cypher)
    if new != cypher:
        fixes.append("assessment_year: added quotes (STRING)")
        cypher = new

    # ORDER BY / LIMIT ordering — LIMIT must come after ORDER BY
    order_m = re.search(r"\bORDER\s+BY\s+.+?(?=\s*(?:SKIP|LIMIT|$))", cypher,
                        re.IGNORECASE | re.DOTALL)
    skip_m  = re.search(r"\bSKIP\s+\S+",  cypher, re.IGNORECASE)
    limit_m = re.search(r"\bLIMIT\s+\S+", cypher, re.IGNORECASE)

    if limit_m and order_m and limit_m.start() < order_m.start():
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
        cypher = base.rstrip() + ("\n" + suffix if suffix else "")
        fixes.append("Reordered ORDER BY before LIMIT")

    return TypeCheckResult(cypher=cypher, fixes=fixes)
