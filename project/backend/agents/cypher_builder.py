"""Agent 2 — Cypher Builder.

Translates ONE tool call into a valid Neo4j Cypher query, reading the user's
raw question directly. There is no separate parameter-extraction stage: this
agent extracts whatever values it needs (geography, year, thresholds, filters)
from the question itself, in the same call that writes the Cypher, and
declares them in its own "params" output. That output is the sole source of
truth for execution — nothing here is pre-validated against an external dict.
"""
from __future__ import annotations

import re
from typing import Any

from backend.registry.tool_registry import TOOLS, build_schema_context
from backend.llm.parser import parse_json_object
from backend.pipeline.rule_library import get_rules_for
from backend.pipeline.type_checker import fix_hardcoded_literals


class InsufficientParamsError(Exception):
    """Raised when CypherBuilder cannot build a query from the question given."""
    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        super().__init__(f"Insufficient information — need: {', '.join(missing)}")

_SYSTEM = """\
Generate a read-only Neo4j Cypher query for the LIHTC knowledge graph, reading the
values you need directly from the user's question. There is no pre-extracted
parameter dict — you decide what values exist and declare them yourself in "params".

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
                               ONLY include a LIMIT clause if the question EXPLICITLY
                               asks for a capped count ("top 10", "first 5", "limit to 20",
                               "show 3"). If the question says "show me all", "how many",
                               or gives no count at all, DO NOT add any LIMIT — omit the
                               clause entirely so every matching row is returned. Never
                               add a default/arbitrary LIMIT (e.g. LIMIT 50) "just in case".


CYPHER DON'TS:
  - Never end a query with WITH, WHERE, or LIMIT alone — RETURN must be the concluding clause.
  - Never put LIMIT before ORDER BY.
  - Never use a ternary operator (x ? a : b) — Cypher has none. Use CASE WHEN x THEN a ELSE b END.
  - Never reference a variable outside the scope it was introduced in.
  - Never use bare identifiers as map values — {key: value} must be {key: $value} for params.
  - Never invent property names — use only names listed in SCHEMA.
  - Never reference a $param in the Cypher that isn't a key in your own "params" output.
  - Never write (expression) = $param to compare a boolean predicate to a parameter.
    This is invalid Cypher. Inline the condition directly in WHERE instead.
    WRONG: WHERE (q IS NOT NULL) = $is_qct_designated
    RIGHT: WHERE q IS NOT NULL
  - Never use a map literal like {min: ..., max: ...} as a property value inside a
    node/relationship pattern. Cypher has no range-map syntax. min_<attr> and max_<attr>
    are ALWAYS separate WHERE comparisons, never part of the {...} property map in MATCH.
    WRONG: MATCH (q:QCTDesignation {poverty_rate_at_designation: {min: $min_poverty_rate_at_designation}})
    RIGHT: MATCH (q:QCTDesignation) WHERE q.poverty_rate_at_designation >= $min_poverty_rate_at_designation
  - Never add a MATCH, OPTIONAL MATCH, or WHERE condition for an attribute the question
    never mentions. Every filter condition must trace back to something the question
    actually asked about — no "helpful" extra filters.
  - Never re-declare the same variable in a second MATCH/OPTIONAL MATCH with a NEW
    label or property map once it is already bound (e.g. matching (q:QCTDesignation)
    once, then later writing another OPTIONAL MATCH (q:QCTDesignation) again). Bind
    each variable exactly once. If you need another condition on it, add a WHERE
    clause referencing the existing variable — do not re-MATCH it.

CRITICAL: OPTIONAL MATCH + WHERE DOES NOT FILTER OUT ROWS.
  A WHERE clause written directly after OPTIONAL MATCH only decides which candidate
  nodes are ALLOWED to bind to that optional pattern — it does NOT remove the outer
  row when no candidate satisfies it. The row is still returned, just with every
  column from that optional variable as NULL. This is the single most common cause
  of "all columns are NULL" bugs in this system.
    WRONG (silently returns every tract, with poverty_rate always NULL for any tract
    that doesn't have a q satisfying the filter, INSTEAD of excluding that tract):
      OPTIONAL MATCH (q:QCTDesignation)-[:APPLIES_TO]->(ct)
      WHERE q IS NOT NULL AND q.poverty_rate_at_designation >= $min_poverty_rate_at_designation
    RIGHT (actually excludes tracts that don't qualify, because MATCH is required —
    a failed match removes the whole row, which is what "show me tracts where X" means):
      MATCH (q:QCTDesignation)-[:APPLIES_TO]->(ct)
      WHERE q.poverty_rate_at_designation >= $min_poverty_rate_at_designation
  RULE: if the question asks to see ONLY things that meet a condition (a required
  filter), use plain MATCH for that node, never OPTIONAL MATCH. Only use OPTIONAL
  MATCH when the question wants a value "if it exists" while still showing every
  row regardless (e.g. "show all tracts and their QCT status, designated or not").

  Also: never put simple equality filters (designation_year, is_designated, etc.)
  inline in a node's {...} property map. Match the node bare and put ALL filters —
  fixed-value and threshold alike — in WHERE. This keeps behavior consistent and
  avoids cramming multiple different filter kinds into one map.
    WRONG: OPTIONAL MATCH (q:QCTDesignation {designation_year: $year, is_designated: true})
    RIGHT: OPTIONAL MATCH (q:QCTDesignation) WHERE q.designation_year = $year AND q.is_designated = true
    (and per the rule above: if this condition is REQUIRED, not optional, use MATCH not OPTIONAL MATCH)

PICKING THE RIGHT OPERATOR FROM THE QUESTION'S WORDING:
    "above X" / "more than X" / "greater than X"        → strict:    > (name param min_<attr>)
    "at least X" / "X or more" / "minimum X"             → inclusive: >= (name param min_<attr>)
    "below X" / "less than X" / "under X"                → strict:    < (name param max_<attr>)
    "at most X" / "X or less" / "no more than X"         → inclusive: <= (name param max_<attr>)
    "between X and Y"                                    → both a min_<attr> and max_<attr>
                                                            param, combined with AND, inclusive
                                                            unless the question says otherwise.
    "exactly X" / "equal to X"                            → use = instead of >=/<=.
  If wording is ambiguous, default to inclusive (>=, <=).

INSUFFICIENT INFORMATION:
  Only declare insufficient_params if the question genuinely lacks a value this tool
  cannot work without (e.g. no county, tract, or metro area given at all for a
  geography-anchored tool). Do NOT declare it just because you think more data would help.
  If truly insufficient, return:
  {"cypher": "", "params": {}, "explanation": "", "insufficient_params": ["what's missing"]}

OUTPUT FORMAT — read carefully:
  - Output ONLY the single corrected JSON object. No markdown. No ``` fences. No text before or after.
  - Do NOT repeat or quote the previous failed JSON. Do NOT explain what changed. Just output the fix.
  - The "cypher" value MUST be a single JSON string on one logical line.
  - Use \\n (backslash-n, two characters) for newlines in Cypher — NOT actual line breaks.
  - NEVER split the cypher string across multiple lines using Python-style concatenation:
      WRONG: "cypher": "MATCH ct\n"
                        "WHERE ct.x = 1"
      RIGHT: "cypher": "MATCH ct\\nWHERE ct.x = 1"
  - "params" must contain a value for EVERY $name referenced in "cypher" — this is the
    only source of truth for execution.
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

# Node pattern: (alias:Label {...}) — label and property map both optional.
_NODE_RE = re.compile(
    r"\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*([A-Za-z_][A-Za-z0-9_]*))?[^()]*\)"
)
# A relationship arrow with its type, either direction: -[:REL]-> or <-[:REL]-
# NOTE: alternation order matters here — "->" must come before "-" or the regex
# engine matches the bare "-" first and leaves a dangling ">" unconsumed.
_REL_ARROW_RE = re.compile(
    r"(<-|-)\s*\[\s*(?:[A-Za-z_][A-Za-z0-9_]*)?\s*:\s*([A-Za-z_][A-Za-z0-9_]*)[^\]]*\]\s*(->|-)"
)


def _fix_relationship_direction(
    cypher: str, alias_to_label: dict[str, str], canonical: set[tuple[str, str, str]],
) -> str:
    """Flip a relationship arrow if it was written backwards relative to the
    ontology's declared (from, type, to) direction — e.g. writing
    (ct:CensusTract)-[:APPLIES_TO]->(q:QCTDesignation) when the true direction
    is (q:QCTDesignation)-[:APPLIES_TO]->(ct:CensusTract).

    This matters more than a syntax error: OPTIONAL MATCH on a reversed
    relationship silently matches zero rows. The query still "succeeds" —
    every column pulled from the far node is just NULL, with no error at all.
    """
    if not canonical:
        return cypher

    nodes = list(_NODE_RE.finditer(cypher))
    replacements: list[tuple[int, int, str]] = []

    for rel_m in _REL_ARROW_RE.finditer(cypher):
        arrow_in, rel_type, arrow_out = rel_m.group(1), rel_m.group(2), rel_m.group(3)

        left_node = None
        for n in nodes:
            if n.end() <= rel_m.start() and cypher[n.end():rel_m.start()].strip() == "":
                left_node = n
        right_node = None
        for n in nodes:
            if n.start() >= rel_m.end() and cypher[rel_m.end():n.start()].strip() == "":
                if right_node is None or n.start() < right_node.start():
                    right_node = n
        if not left_node or not right_node:
            continue

        left_label = left_node.group(2) or alias_to_label.get(left_node.group(1))
        right_label = right_node.group(2) or alias_to_label.get(right_node.group(1))
        if not left_label or not right_label:
            continue

        if arrow_in == "-" and arrow_out == "->":
            current, flipped = (left_label, rel_type, right_label), (right_label, rel_type, left_label)
        elif arrow_in == "<-" and arrow_out == "-":
            current, flipped = (right_label, rel_type, left_label), (left_label, rel_type, right_label)
        else:
            continue

        if current in canonical or flipped not in canonical:
            continue  # already correct, or not a recognized relationship — leave alone

        new_text = f"<-[:{rel_type}]-" if arrow_in == "-" else f"-[:{rel_type}]->"
        replacements.append((rel_m.start(), rel_m.end(), new_text))

    if not replacements:
        return cypher

    result = cypher
    for start, end, new_text in sorted(replacements, key=lambda r: r[0], reverse=True):
        result = result[:start] + new_text + result[end:]
    return result


def _post_process(cypher: str, params: dict[str, Any], ontology=None) -> str:
    """Deterministic fixes applied after LLM generation."""
    cypher = _UNICODE_RE.sub("", cypher)

    # Strip ANY LIMIT clause — parameterized ($limit) or a hardcoded literal
    # (LIMIT 50) — unless the user explicitly asked for a capped count. Without
    # this, a default LIMIT silently truncates results and the Synthesizer never
    # learns the true total (e.g. reporting "10 tracts" when 47 actually matched).
    if "limit" not in params:
        cypher = re.sub(r"\s*LIMIT\s+(?:\$limit\b|\d+\b)", "", cypher, flags=re.IGNORECASE)

    for f in _BOOLEAN_FIELDS:
        cypher = re.sub(rf"\b{f}\s*=\s*0\b", f"{f} = false", cypher, flags=re.IGNORECASE)
        cypher = re.sub(rf"\b{f}\s*=\s*1\b", f"{f} = true",  cypher, flags=re.IGNORECASE)

    for f in _INTEGER_FIELDS:
        cypher = re.sub(rf"({f}\s*=\s*)['\"](\d+)['\"]", r"\g<1>\2", cypher)

    cypher = re.sub(
        r"assessment_year\s*=\s*\$year(?![_A-Za-z0-9])",
        "assessment_year = toString($year)", cypher,
    )

    # Auto-parameterize any literal the LLM embedded directly instead of using
    # $param (e.g. {basis_boost_pct: 30}). Runs after the boolean/integer quote
    # coercion above so a wrongly-quoted integer literal is unquoted first and
    # gets the correct type when it's turned into a param. Mutates `params`.
    cypher = fix_hardcoded_literals(cypher, params)

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
    # NOTE: is_metro_tract belongs to QCTDesignation, not CensusTract — redirecting
    # it to ct_alias here would just move the bug to another wrong node. Only
    # is_metro genuinely lives on CensusTract, so only that one gets corrected.
    ct_alias = next(iter(ct_aliases), "ct")
    for alias in bad_aliases:
        cypher = re.sub(rf"\b{alias}\.is_metro\b", f"{ct_alias}.is_metro", cypher)

    if ontology is not None:
        canonical = {
            (rp["from"], rp["type"], rp["to"])
            for rp in getattr(ontology, "relationship_patterns", [])
        }
        cypher = _fix_relationship_direction(cypher, alias_to_label, canonical)

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


def _build_prompt(tool_name: str, question: str, ontology,
                  error: str | None, previous_cypher: str | None = None) -> str:
    tool = TOOLS[tool_name]
    schema_ctx = build_schema_context(tool_name, ontology)
    rules = get_rules_for(tool_name, tool.node_labels)
    rules_text = "\n".join(f"  {i+1}. {r}" for i, r in enumerate(rules))

    parts = [
        schema_ctx,
        "",
        f"USER QUESTION:\n{question}",
        "",
        f"RULES FOR THIS CALL:\n{rules_text}",
    ]
    if error:
        parts += ["", f"PREVIOUS ATTEMPT FAILED WITH:\n{error}"]
        if previous_cypher:
            parts += ["", f"PREVIOUS CYPHER THAT FAILED:\n{previous_cypher}"]
        if error.startswith("INVALID PARAMS"):
            # A $param in the cypher wasn't declared in the LLM's own params output —
            # patching in place just accumulates more broken structure (seen
            # repeatedly: bolting on another OPTIONAL MATCH + filter instead of
            # removing the bad one). Force a clean rewrite instead.
            parts += [
                "",
                "The previous Cypher referenced a $param that was missing from your own "
                "'params' output. Do NOT patch the previous Cypher — throw it away and write "
                "a NEW, SIMPLER query from scratch, re-reading the USER QUESTION above. "
                "Every $param in the new Cypher MUST have a matching key in 'params'. "
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
        question: str,
        ontology,
        error: str | None = None,
        previous_cypher: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Generate Cypher for one tool call, extracting values directly from
        `question`. Returns (cypher, exec_params) — exec_params is entirely
        self-declared by the LLM in this same call; there is no external dict
        to validate it against.

        previous_cypher: the Cypher from the failed attempt, sent alongside `error`
        on retries so the LLM patches the specific query instead of regenerating blind.
        """
        user_content = _build_prompt(tool_name, question, ontology, error, previous_cypher)
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

        exec_params: dict[str, Any] = {
            k: v for k, v in data.get("params", {}).items() if v is not None
        }

        cypher = _post_process(data.get("cypher", "").strip(), exec_params, ontology)
        return cypher, exec_params
