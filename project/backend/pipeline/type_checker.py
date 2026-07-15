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

    `allowed` is the set of keys the LLM itself declared in its own "params" JSON
    for this call. CypherBuilder extracts values directly from the question and
    self-declares them — there's no external ground truth to validate against
    anymore. This check instead catches internal self-inconsistency: the LLM
    referenced $foo in the Cypher but forgot to include "foo" in its own params
    dict. Neo4j would receive no binding for it; catching it here gives a precise
    fix instead of a generic Neo4j "missing parameter" error.
    """
    used = {m.group(1) for m in _PARAM_REF_RE.finditer(cypher)}
    unauthorized = sorted(used - allowed)
    if not unauthorized:
        return None
    return (
        f"INVALID PARAMS: the Cypher references {', '.join('$' + p for p in unauthorized)} "
        f"which {'is' if len(unauthorized) == 1 else 'are'} NOT declared in your own "
        f"'params' output. You may ONLY reference $names that are keys in 'params': "
        f"{sorted(allowed)}. Either add the missing key(s) with a real value re-read "
        f"from the question, or remove the condition(s) using "
        f"{', '.join('$' + p for p in unauthorized)} entirely."
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


# Catches a hardcoded literal used in a property map {key: "value"} or {key: 123}
# instead of a $param. `true`/`false`/`null` are never matched here since they're
# bareword identifiers, not a quoted string or digit sequence — no lookahead needed
# to exclude them (an earlier version used one and it back-tracked across newlines
# into unrelated following text, corrupting the matched number).
# Matches a key preceded by either "{" (first key) or "," (subsequent key).
_MAP_LITERAL_RE = re.compile(
    r"[{,]\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*"
    r"(\"(?:[^\"\\]|\\.)*\"|\d+(?:\.\d+)?)"
)

# Catches a hardcoded literal in a WHERE comparison: identifier.prop OP literal
# instead of identifier.prop OP $param.
_WHERE_LITERAL_RE = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9_]*\.([A-Za-z_][A-Za-z0-9_]*)\s*"
    r"(=|>=|<=|>|<)\s*"
    r"(\"(?:[^\"\\]|\\.)*\"|\d+(?:\.\d+)?)"
)


def _parse_literal(raw: str):
    if raw.startswith('"'):
        return raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if "." in raw:
        return float(raw)
    return int(raw)


def fix_hardcoded_literals(cypher: str, params: dict) -> str:
    """Auto-parameterize hardcoded literals the LLM embedded directly in the
    Cypher instead of writing $param and declaring it in "params". Mutates
    `params` in place to add the values the LLM should have declared itself.

    Small models have repeatedly failed to apply this exact fix on retry even
    when told precisely which literal is wrong (verbatim, every attempt), so
    it's corrected mechanically instead of relying on the retry loop.
    """
    def _param_name(base: str) -> str:
        if base not in params:
            return base
        i = 2
        while f"{base}_{i}" in params:
            i += 1
        return f"{base}_{i}"

    def _fix_map(m: re.Match) -> str:
        prefix, key, raw = m.group(0)[0], m.group(1), m.group(2)
        if raw in ("true", "false", "null"):
            return m.group(0)
        name = _param_name(key)
        params[name] = _parse_literal(raw)
        return f"{prefix}{key}: ${name}"

    cypher = _MAP_LITERAL_RE.sub(_fix_map, cypher)

    def _fix_where(m: re.Match) -> str:
        prop, raw = m.group(1), m.group(3)
        if raw in ("true", "false", "null"):
            return m.group(0)
        name = _param_name(prop)
        params[name] = _parse_literal(raw)
        offset = m.start(3) - m.start(0)
        return f"{m.group(0)[:offset]}${name}"

    cypher = _WHERE_LITERAL_RE.sub(_fix_where, cypher)
    return cypher


def find_hardcoded_literals(cypher: str) -> str | None:
    """Return an actionable error if a value that should be a $param is instead
    hardcoded as a literal string/number in the Cypher.

    CypherBuilder now extracts values from the question and writes the Cypher in
    the same call — it has a tendency to embed the value it just read directly
    (e.g. {county_fips: "48113"}, designation_year: 2025, > 0.25) instead of
    writing $county_fips / $year / $min_poverty_rate_at_designation and declaring
    them in "params". This bypasses the unauthorized-param and missing-return-
    column checks entirely (both only scan for $param usage), so it needs its
    own dedicated check.
    """
    found: list[str] = []

    for m in _MAP_LITERAL_RE.finditer(cypher):
        key, val = m.group(1), m.group(2)
        if val in ("true", "false", "null"):
            continue
        found.append(f"{{{key}: {val}}}")

    for m in _WHERE_LITERAL_RE.finditer(cypher):
        prop, op, val = m.group(1), m.group(2), m.group(3)
        found.append(f".{prop} {op} {val}")

    if not found:
        return None
    return (
        f"HARDCODED LITERAL: found {', '.join(found)} using a literal value directly "
        f"instead of a $param. Every value read from the question MUST be written as "
        f"$paramname in the Cypher and declared with its value in your 'params' JSON — "
        f"never embed the literal string/number/decimal directly in the query. "
        f"Fix: replace each literal above with $<descriptive_name> and add that name "
        f"to 'params' with the same value."
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
        f"(include only the min_/max_ clause that actually applies to the question)."
    )


# Detects the "OPTIONAL MATCH + WHERE" trap: a WHERE clause directly attached to
# an OPTIONAL MATCH only decides which candidate nodes may bind to that pattern —
# it does NOT drop the outer row when nothing satisfies it. The row still comes
# back with every column from that optional variable as NULL. This was the root
# cause behind several "all columns NULL" reports even after other bugs were fixed.
_OPTIONAL_MATCH_LINE_RE = re.compile(r"^\s*OPTIONAL\s+MATCH\s+(.*)$", re.IGNORECASE)
_WHERE_LINE_RE = re.compile(r"^\s*WHERE\s+(.*)$", re.IGNORECASE)
_NODE_ALIAS_RE = re.compile(r"\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*[:)]")


def fix_optional_match_where_trap(cypher: str) -> str:
    """Auto-correct the OPTIONAL MATCH + WHERE trap by changing 'OPTIONAL MATCH'
    to plain 'MATCH' wherever the immediately-following WHERE filters a property
    of a variable that OPTIONAL MATCH just introduced.

    This is a deterministic fix rather than a retry-and-hope error because small
    models have proven unable to reliably apply this one-word correction even
    when told exactly what's wrong, verbatim, across every retry attempt. The
    fix is mechanical and safe: a required-looking property filter right after
    OPTIONAL MATCH is a near-certain sign the author wanted a required match —
    converting to MATCH is exactly what makes the query behave as intended.
    """
    lines = cypher.splitlines()
    for i, line in enumerate(lines):
        om_match = _OPTIONAL_MATCH_LINE_RE.match(line)
        if not om_match:
            continue
        aliases = set(_NODE_ALIAS_RE.findall(om_match.group(1)))
        if not aliases:
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines):
            continue
        where_match = _WHERE_LINE_RE.match(lines[j])
        if not where_match:
            continue
        where_body = where_match.group(1)
        if any(
            re.search(
                rf"\b{re.escape(alias)}\.\w+\s*(?:=|>=|<=|>|<)\s*(?!NULL\b)",
                where_body, re.IGNORECASE,
            )
            for alias in aliases
        ):
            lines[i] = re.sub(
                r"OPTIONAL\s+MATCH", "MATCH", line, count=1, flags=re.IGNORECASE,
            )
    return "\n".join(lines)


def find_optional_match_where_trap(cypher: str) -> str | None:
    """Return an actionable error if a WHERE directly after OPTIONAL MATCH filters
    a property (not just an IS NULL/IS NOT NULL check) of a variable that OPTIONAL
    MATCH just introduced — a near-certain sign the filter was meant to be required.
    """
    lines = cypher.splitlines()
    for i, line in enumerate(lines):
        om_match = _OPTIONAL_MATCH_LINE_RE.match(line)
        if not om_match:
            continue
        aliases = set(_NODE_ALIAS_RE.findall(om_match.group(1)))
        if not aliases:
            continue
        # Find the next non-blank line.
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines):
            continue
        where_match = _WHERE_LINE_RE.match(lines[j])
        if not where_match:
            continue
        where_body = where_match.group(1)
        for alias in aliases:
            if re.search(
                rf"\b{re.escape(alias)}\.\w+\s*(?:=|>=|<=|>|<)\s*(?!NULL\b)",
                where_body, re.IGNORECASE,
            ):
                return (
                    f"OPTIONAL MATCH + WHERE TRAP: the WHERE clause right after "
                    f"'OPTIONAL MATCH {om_match.group(1).strip()}' filters a property of "
                    f"'{alias}', but a WHERE directly attached to OPTIONAL MATCH only "
                    f"controls what CAN bind — it does NOT remove the row when nothing "
                    f"satisfies the condition. The row still comes back with '{alias}' "
                    f"and all its columns NULL instead of being excluded. "
                    f"If this condition is REQUIRED (the question wants only qualifying "
                    f"rows), change 'OPTIONAL MATCH' to plain 'MATCH' for this pattern. "
                    f"Only keep OPTIONAL MATCH if rows without a match should still be "
                    f"returned (with nulls) — in that case, remove the property filter "
                    f"from this WHERE entirely."
                )
    return None


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
