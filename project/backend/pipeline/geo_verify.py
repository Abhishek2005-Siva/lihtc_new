"""Geo-resolution verification — confirms a resolved county FIPS code against
the graph itself.

The small model has proven unable to reliably resolve a place NAME to its
numeric code from memory even when explicitly told to use the graph instead
(confirmed via a real trace: it declared county_fips='48113' — Dallas County
TX's code — for a question about "Cook County IL", producing a plausible-
looking but entirely wrong answer with no error at all). Unlike the tract-
level fabrication checks in type_checker.py, this needs a live Neo4j round-
trip to ground-truth the code against the actual name on record, so it lives
separately from the pure string-only preflight checks.
"""
from __future__ import annotations

import re

_COUNTY_USAGE_RE = re.compile(r"\bcounty_fips\s*(?::|=)\s*\$county_fips\b")


def verify_county_resolution(
    cypher: str, exec_params: dict, question: str, neo4j_client,
) -> str | None:
    """Return an actionable error if a county_fips param was resolved to the
    WRONG county — i.e. it doesn't correspond to any name mentioned in the
    question — by checking it against the graph directly.
    """
    value = exec_params.get("county_fips")
    if not isinstance(value, str) or len(value) != 5:
        return None  # wrong-length case is caught elsewhere
    if value in question:
        return None  # the question stated this exact code directly — nothing to verify
    if not _COUNTY_USAGE_RE.search(cypher):
        return None  # not actually used as a county_fips filter

    rows = neo4j_client.run_read(
        "MATCH (co:County {county_fips: $v}) RETURN co.county_name AS name",
        {"v": value},
    )
    if not rows:
        return (
            f"UNVERIFIED COUNTY CODE: params['county_fips'] = '{value}' does not match any "
            f"County in the graph. Do not guess a county_fips from memory — resolve it via "
            f"the graph instead: MATCH (co:County) WHERE toLower(co.county_name) CONTAINS "
            f"toLower($county_name) AND co.state_fips = $state_fips."
        )

    resolved_name = (rows[0].get("name") or "").strip()
    name_tokens = [t for t in re.findall(r"[A-Za-z]+", resolved_name) if t.lower() != "county"]
    if any(tok and tok.lower() in question.lower() for tok in name_tokens):
        return None  # plausible — the resolved county's name is mentioned in the question

    return (
        f"WRONG COUNTY CODE: params['county_fips'] = '{value}' resolves to "
        f"'{resolved_name}' in the graph, but the question never mentions that county — "
        f"you guessed the WRONG FIPS code from memory. NEVER resolve a county name to its "
        f"code from memory. Resolve it via the graph instead: MATCH (co:County) WHERE "
        f"toLower(co.county_name) CONTAINS toLower($county_name) AND co.state_fips = "
        f"$state_fips (resolve $state_fips via State.state_abbr/state_name first if the "
        f"state was only named/abbreviated), then use co.county_fips or traverse from co "
        f"directly instead of hardcoding a value you cannot verify."
    )
