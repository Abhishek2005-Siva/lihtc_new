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
    "assessment_year":  "INTEGER",
    # STRING (these should never be unquoted bare integers)
    "fips_code":        "STRING",
    "county_fips":      "STRING",
    "state_fips":       "STRING",
    "cbsa_code":        "STRING",
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


# Fixed-width, zero-padded geographic identifiers per the schema (see ontology
# quirks: zfill(11)/zfill(5)/zfill(2)). A wrong length is a reliable signature
# of a broader geography's code being reused for a narrower field it doesn't
# belong in (e.g. a 5-digit county_fips passed as an 11-digit tract fips_code
# when the question only names a county, no specific tract) — confirmed via a
# real trace where this silently returned zero rows with no error at all.
_FIXED_WIDTH_ID_PARAMS = {
    "fips_code": 11,
    "county_fips": 5,
    "state_fips": 2,
}


def find_wrong_length_identifier(cypher: str, exec_params: dict) -> str | None:
    """Return an actionable error if a fixed-width geographic identifier param
    has the wrong length for what it's supposed to be.

    Only fires when the Cypher actually compares a property of the SAME name
    to the param (e.g. "fips_code: $fips_code" or ".fips_code = $fips_code") —
    a param merely NAMED "fips_code" that the model bound to some unrelated
    property (e.g. "state: $fips_code") isn't this bug, just a misleading
    param name with a perfectly valid value; flagging it there was a real
    false positive caught in testing (a SpecialProgramLimit.state filter using
    a 2-digit state code, correctly used, rejected only because of its name).
    """
    for param, expected_len in _FIXED_WIDTH_ID_PARAMS.items():
        value = exec_params.get(param)
        if not isinstance(value, str) or len(value) == expected_len:
            continue
        if not re.search(rf"\b{param}\s*(?::|=)\s*\${param}\b", cypher):
            continue
        example = {11: "01001020700", 5: "48113", 2: "48"}[expected_len]
        return (
            f"WRONG-LENGTH IDENTIFIER: params['{param}'] = {value!r} is "
            f"{len(value)} character(s), but {param} must be exactly "
            f"{expected_len} zero-padded digits (e.g. '{example}'). This usually means a "
            f"broader geography's code (e.g. county_fips) was reused for a narrower field "
            f"it doesn't belong in, because the question only names that broader geography "
            f"with no specific tract given. Do NOT invent a tract-level identifier — remove "
            f"'{param}' from params/Cypher entirely and anchor the query on whichever entity "
            f"the question actually names instead (e.g. MATCH County directly, not CensusTract)."
        )
    return None


def find_fabricated_fips_code(cypher: str, exec_params: dict, question: str) -> str | None:
    """Return an actionable error if a fips_code param was declared but that
    exact 11-digit string never appears in the user's question.

    Unlike a county/metro NAME (which the model may legitimately resolve to a
    FIPS code from general knowledge — "Dallas TX" -> "48113"), a specific
    tract's fips_code is only knowable if the question states it explicitly;
    there is no name-based lookup for an individual tract. A fips_code that
    isn't in the question at all is fabricated — confirmed via real traces
    where the model dodged the plain wrong-length check above by padding a
    county_fips with zeros (e.g. '17031' -> '17031000000') to fake the right
    length, or by matching some unrelated real tract entirely by accident.

    Only fires when the Cypher actually compares CensusTract.fips_code to this
    param (same false-positive guard as find_wrong_length_identifier — a param
    merely NAMED "fips_code" bound to an unrelated property isn't this bug).
    """
    value = exec_params.get("fips_code")
    if not isinstance(value, str) or len(value) != 11:
        return None  # wrong-length case already caught by find_wrong_length_identifier
    if not re.search(r"\bfips_code\s*(?::|=)\s*\$fips_code\b", cypher):
        return None
    if value in question:
        return None
    return (
        f"FABRICATED TRACT IDENTIFIER: params['fips_code'] = '{value}' does not appear "
        f"anywhere in the user's question. A tract-level fips_code can ONLY be used if "
        f"the question states that exact 11-digit number explicitly — it cannot be "
        f"derived or guessed from a county/metro name the way county_fips can. Remove "
        f"'fips_code' and the CensusTract match entirely, and anchor the query on "
        f"whichever geography the question actually names instead (e.g. MATCH County "
        f"directly using county_fips)."
    )


# Matches the fabricated-anchor shape as the CYPHER'S FIRST LINE: a bare
# CensusTract match keyed purely on fips_code, with nothing else in its
# property map (a real per-tract query would have no other reason to look
# different from this — the anti-pattern is specifically this exact shape used
# when only a county was named).
_FIRST_MATCH_CT_RE = re.compile(
    r"^\s*MATCH\s*\(\s*ct\s*:\s*CensusTract\s*\{\s*fips_code\s*:\s*\$fips_code\s*\}\s*\)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
# The very next hop from ct to County via IN_COUNTY — this is what should
# become the new anchor once the fabricated ct match is dropped.
_CT_TO_COUNTY_HOP_RE = re.compile(
    r"^\s*(?:OPTIONAL\s+)?MATCH\s*\(\s*ct\s*\)\s*-\s*\[\s*:\s*IN_COUNTY\s*\]\s*->\s*"
    r"(\(\s*co\s*:\s*County\s*\{[^}]*\}\s*\))\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def fix_fabricated_tract_anchor(
    cypher: str, exec_params: dict, question: str,
) -> tuple[str, dict]:
    """Mechanically rewrite the fabricated-tract-anchor anti-pattern: a MATCH
    on CensusTract keyed by a fips_code that's either the wrong length or not
    actually in the question (see find_wrong_length_identifier /
    find_fabricated_fips_code above), immediately followed by a hop to County
    via IN_COUNTY — when all the query actually needed was the County itself.

    Confirmed via repeated real traces that prompt guidance alone does not
    reliably stop the model from anchoring on CensusTract even when the
    question only names a county (it kept re-fabricating a fips_code across
    retries — sometimes by padding a county_fips with zeros to dodge the
    length check), so this corrects it mechanically instead of relying on the
    retry loop to converge.

    Only fires when the rewrite is unambiguous: a fabricated/wrong-length
    fips_code, a real county_fips already declared, a bare single-key
    CensusTract match as the cypher's first line, and a clean hop to County
    right after. Leaves the query untouched in every other case — it's safer
    to fall through to the retry loop than risk a partial/bad rewrite.
    """
    fips = exec_params.get("fips_code")
    county_fips = exec_params.get("county_fips")
    if not isinstance(fips, str) or not isinstance(county_fips, str):
        return cypher, exec_params
    fabricated = len(fips) != 11 or fips not in question
    if not fabricated:
        return cypher, exec_params

    lines = cypher.splitlines()
    if not lines or not _FIRST_MATCH_CT_RE.match(lines[0]):
        return cypher, exec_params
    if not _CT_TO_COUNTY_HOP_RE.search(cypher):
        return cypher, exec_params

    new_cypher = "\n".join(lines[1:])  # drop the fabricated CensusTract match
    new_cypher = _CT_TO_COUNTY_HOP_RE.sub(
        lambda m: f"MATCH {m.group(1)}", new_cypher, count=1,
    )
    # Any remaining (ct)-[:IN_METRO]-> must redirect through co instead, since
    # ct no longer exists — County has its own IN_METRO relationship too.
    new_cypher = re.sub(
        r"\(\s*ct\s*\)(\s*-\s*\[\s*:\s*IN_METRO\s*\])", r"(co)\1",
        new_cypher, flags=re.IGNORECASE,
    )

    # Bail out entirely if `ct` is still referenced anywhere else — the shape
    # wasn't as clean as expected; a partial rewrite is worse than none.
    if re.search(r"\bct\b", new_cypher):
        return cypher, exec_params

    new_params = {k: v for k, v in exec_params.items() if k != "fips_code"}
    return new_cypher, new_params


# Extracts the RETURN clause body (up to ORDER BY / LIMIT / end of string).
_RETURN_CLAUSE_RE = re.compile(
    r"\bRETURN\s+(.+?)(?=\r?\n\s*(?:ORDER\s+BY|LIMIT)\b|$)", re.IGNORECASE | re.DOTALL,
)

# Matches alias.attr <op> $param inside WHERE — the general "filtered but not
# returned" pattern, not just min_/max_-prefixed ones. Anchor/identity fields
# are excluded (see _ANCHOR_ATTRS) since those are normally used purely to
# scope the query, not as the thing the user is asking about.
_WHERE_PARAM_FILTER_RE = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9_]*\.([A-Za-z_][A-Za-z0-9_]*)\s*(?:=|>=|<=|>|<)\s*\$[A-Za-z_][A-Za-z0-9_]*"
)
_ANCHOR_ATTRS = {
    "fips_code", "county_fips", "state_fips", "cbsa_code",
    "year", "designation_year", "assessment_year", "start_year", "end_year",
    "is_designated",
}


def find_missing_filter_columns(cypher: str, param_keys) -> str | None:
    """If the query filters on an attribute, that attribute must appear in the
    RETURN clause — otherwise the user asked about a value they can never see
    in the result. This was a real bug: a query filtered on basis_boost_pct
    (the actual thing the question asked about) but only returned an unrelated
    field, so the Synthesizer never saw basis_boost_pct and had to guess at the
    answer from other data, producing a wrong conclusion. Returns an actionable
    error if an attribute is missing.
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

    for m2 in _WHERE_PARAM_FILTER_RE.finditer(cypher):
        attr = m2.group(1)
        if attr in _ANCHOR_ATTRS:
            continue
        if not re.search(rf"\b{re.escape(attr)}\b", return_body):
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


# Detects a MATCH/OPTIONAL MATCH clause that introduces a brand-new node with NO
# relationship arrow connecting it to anything matched earlier — a standalone
# "(alias:Label {...})" pattern with no -[...]-> or <-[...]- anywhere in it.
# Two independently-matched node sets with no join condition between them is a
# cartesian product: Neo4j pairs EVERY row from one set with EVERY row from the
# other, completely ignoring which specific entity the first MATCH anchored on.
# This has occurred repeatedly and is a much worse failure than a syntax error —
# the query runs "successfully" and returns large amounts of unrelated data.
_MATCH_LINE_RE = re.compile(r"^\s*(?:OPTIONAL\s+)?MATCH\s+(.*)$", re.IGNORECASE)
_REL_ARROW_ANYWHERE_RE = re.compile(r"-\s*\[|\]\s*-|<-|->")


def find_disconnected_match(cypher: str) -> str | None:
    """Return an actionable error if a MATCH/OPTIONAL MATCH clause (after the
    first) introduces a new node with no relationship connecting it to a
    previously matched variable — the cartesian-product anti-pattern.
    """
    lines = cypher.splitlines()
    match_lines = [
        (i, m.group(1)) for i, line in enumerate(lines)
        if (m := _MATCH_LINE_RE.match(line))
    ]
    if len(match_lines) < 2:
        return None

    for i, (line_no, body) in enumerate(match_lines):
        if i == 0:
            continue  # the first MATCH always anchors the query — nothing to connect to yet
        if not _REL_ARROW_ANYWHERE_RE.search(body):
            alias_m = _NODE_ALIAS_RE.search(body)
            alias = alias_m.group(1) if alias_m else "?"
            return (
                f"DISCONNECTED MATCH: the clause 'MATCH {body.strip()}' introduces "
                f"'{alias}' with NO relationship arrow (-[:REL]-> or <-[:REL]-) connecting "
                f"it to any previously matched node. Two separately-matched node sets with "
                f"no join between them produce a CARTESIAN PRODUCT — Neo4j pairs every row "
                f"from one set with every row from the other, returning data for entities "
                f"completely unrelated to what the earlier MATCH anchored on. Add a "
                f"relationship path connecting '{alias}' to an already-matched variable "
                f"(e.g. via APPLIES_TO), or if '{alias}' is meant to filter by a shared "
                f"property, join through the graph relationship instead of matching it "
                f"independently."
            )
    return None


_OPTIONAL_MATCH_LINE_RE = re.compile(r"^\s*OPTIONAL\s+MATCH\s+(.*)$", re.IGNORECASE)
_WHERE_LINE_RE = re.compile(r"^\s*WHERE\s+(.*)$", re.IGNORECASE)
_NODE_ALIAS_RE = re.compile(r"\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*[:)]")

# Detects the LLM writing a WHERE condition with an unclosed "(" and then
# starting the NEXT clause (WITH/RETURN/MATCH/OPTIONAL MATCH) before closing
# it — e.g. "WHERE (a = $x\nWITH a) AND (b)" — a structurally invalid attempt to
# combine two conditions that involve a second MATCH. Confirmed via diagnostic
# testing as a recurring failure the model does not reliably avoid even when
# told about it directly in the system prompt, so it's caught deterministically
# here instead, the same way the OPTIONAL MATCH + WHERE trap is.
_WHERE_START_RE = re.compile(r"^\s*WHERE\b", re.IGNORECASE)
_CLAUSE_KEYWORD_LINE_RE = re.compile(r"^\s*(WITH|RETURN|OPTIONAL\s+MATCH|MATCH)\b", re.IGNORECASE)


def find_clause_inside_where(cypher: str) -> str | None:
    """Return an actionable error if a WITH/RETURN/MATCH/OPTIONAL MATCH clause
    keyword starts on the line right after a WHERE whose parentheses are still
    unclosed — i.e. a new clause was nested inside a WHERE condition instead of
    the WHERE being closed first.
    """
    lines = cypher.splitlines()
    for i, line in enumerate(lines):
        if not _WHERE_START_RE.match(line):
            continue
        if line.count("(") <= line.count(")"):
            continue  # this WHERE line's parens are already balanced
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines):
            continue
        m = _CLAUSE_KEYWORD_LINE_RE.match(lines[j])
        if not m:
            continue
        keyword = m.group(1).upper()
        return (
            f"CLAUSE NESTED INSIDE WHERE: the WHERE clause opens a '(' that is still "
            f"unclosed when '{keyword}' starts on the next line. WITH/RETURN/MATCH/"
            f"OPTIONAL MATCH must NEVER appear inside a WHERE condition's parentheses — "
            f"they are always separate, top-level clauses. Close every '(' the WHERE "
            f"opened BEFORE this point, and move '{keyword}' out to its own line as a "
            f"normal top-level clause. If you need a second pattern to check a second "
            f"condition, write it as its own OPTIONAL MATCH clause placed BEFORE the "
            f"WHERE, then reference both variables together in one WHERE ... AND ... ."
        )
    return None


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

    # assessment_year is an INTEGER (confirmed against live data — it was
    # incorrectly documented as STRING before). Unwrap any toString(...) the
    # model still wraps it in out of training habit — comparing an INTEGER
    # property to a STRING value silently matches zero rows, no error at all.
    new = re.sub(
        r"assessment_year\s*=\s*toString\(([^()]+)\)",
        r"assessment_year = \1", cypher, flags=re.IGNORECASE,
    )
    if new != cypher:
        fixes.append("assessment_year: removed toString() wrapper (INTEGER)")
        cypher = new

    # ORDER BY / LIMIT ordering — LIMIT must come after ORDER BY
    # \b...\b around SKIP/LIMIT is required: without it, "LIMIT" matches
    # case-insensitively as a bare substring of property names like
    # "limit_4person" (very common in this schema — AMI limit fields), truncating
    # the ORDER BY capture right after the first such reference (e.g. "ORDER BY s.").
    order_m = re.search(r"\bORDER\s+BY\s+.+?(?=\s*(?:\bSKIP\b|\bLIMIT\b|$))", cypher,
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
