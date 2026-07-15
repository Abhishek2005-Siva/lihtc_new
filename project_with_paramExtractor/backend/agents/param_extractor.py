"""Agent 1 — Parameter Extractor.

Reads the raw user question and extracts only the parameters explicitly present.
Returns a sparse JSON — keys with no value are omitted entirely.

The extractable parameter list is loaded at import time from data/param_reference.json
so it stays in sync with the schema without hardcoding anything here.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.llm.parser import parse_json_object

_THIS_YEAR = datetime.now().year

# ---------------------------------------------------------------------------
# Build system prompt from data/param_reference.json
# ---------------------------------------------------------------------------

def _build_param_block() -> str:
    ref_path = Path(__file__).resolve().parents[3] / "data" / "param_reference.json"
    try:
        ref = json.loads(ref_path.read_text(encoding="utf-8"))
    except Exception:
        return "(parameter reference unavailable)"

    lines = []
    for p in ref["parameters"]:
        name = p["name"]
        typ  = p["type"]
        node = p.get("node", "")
        desc = p.get("description", "")
        vals = p.get("values")
        ex   = p.get("example", "")

        detail = desc
        if vals:
            detail += f" Values: {vals}."
        if ex != "":
            detail += f" e.g. {ex!r}"

        node_tag = f" [{node}]" if node else ""
        lines.append(f"  {name:<40} {typ}{node_tag} -- {detail}")

    return "\n".join(lines)


_PARAM_BLOCK = _build_param_block()

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

def _build_system(param_block: str) -> str:
    return f"""\
You are a parameter extractor for a LIHTC underwriting assistant.
Read the user question and extract ONLY values explicitly stated or clearly implied.
Do NOT answer the question.

EXTRACTABLE PARAMETERS:
{param_block}

Output a FLAT JSON object. No nested objects. No null values. No extra keys.
Always include year, end_goal, query_mode.

Example: "Show QCT tracts in Dallas County (48113) with poverty rate above 25%"
{{"year":2025,"county_fips":"48113","min_poverty_rate_at_designation":0.25,"is_qct_designated":true,"end_goal":"site_selection","query_mode":"search"}}
"""


# Static prompt with the full catalog — used when no semantic selector is supplied.
_SYSTEM = _build_system(_PARAM_BLOCK)


# ---------------------------------------------------------------------------
# Data class + extractor
# ---------------------------------------------------------------------------

_STRUCTURAL_KEYS = {
    "fips_code", "county_fips", "state_fips", "cbsa_code",
    "year", "start_year", "end_year",
    "program_type", "household_size", "limit",
    "end_goal", "query_mode", "filters",
    "clarification_needed", "clarification_question",
}


@dataclass
class ExtractedParams:
    # -- Fixed pipeline fields ------------------------------------------------
    fips_code: str | None = None
    county_fips: str | None = None
    state_fips: str | None = None
    cbsa_code: str | None = None
    year: int = _THIS_YEAR
    start_year: int | None = None
    end_year: int | None = None
    program_type: str | None = None
    household_size: int | None = None
    limit: int | None = None
    end_goal: str = "full_profile"
    query_mode: str = "lookup"
    filters: dict[str, Any] = field(default_factory=dict)
    clarification_needed: bool = False
    clarification_question: str | None = None
    # -- All graph attributes the LLM extracts (fully dynamic) ----------------
    extra: dict[str, Any] = field(default_factory=dict)
    # -- Keys the LLM returned as null (stripped before next stage) -----------
    null_keys: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "ExtractedParams":
        # Flatten nested node-label dicts, track all null keys
        flat: dict = {}
        null_keys: list[str] = []
        for k, v in d.items():
            if isinstance(v, dict):
                for sub_k, sub_v in v.items():
                    if sub_v is None:
                        null_keys.append(sub_k)
                    elif sub_k not in flat:
                        flat[sub_k] = sub_v
            elif v is None:
                null_keys.append(k)
            else:
                flat[k] = v
        d = flat

        extra = {k: v for k, v in d.items() if k not in _STRUCTURAL_KEYS}
        return cls(
            fips_code=d.get("fips_code"),
            county_fips=str(d["county_fips"]) if d.get("county_fips") is not None else None,
            state_fips=str(d["state_fips"]) if d.get("state_fips") is not None else None,
            cbsa_code=d.get("cbsa_code"),
            year=int(d.get("year") or _THIS_YEAR),
            start_year=int(d["start_year"]) if d.get("start_year") else None,
            end_year=int(d["end_year"]) if d.get("end_year") else None,
            program_type=d.get("program_type"),
            household_size=int(d["household_size"]) if d.get("household_size") else None,
            limit=int(d["limit"]) if d.get("limit") else None,
            end_goal=d.get("end_goal") or "full_profile",
            query_mode=d.get("query_mode") or "lookup",
            filters=d.get("filters") or {},
            clarification_needed=bool(d.get("clarification_needed")),
            clarification_question=d.get("clarification_question"),
            extra=extra,
            null_keys=list(set(null_keys)),
        )

    def to_dict(self) -> dict[str, Any]:
        """Sparse output -- structural fields first, then all dynamic extras."""
        d: dict[str, Any] = {
            "year":       self.year,
            "end_goal":   self.end_goal,
            "query_mode": self.query_mode,
        }
        if self.fips_code:              d["fips_code"]              = self.fips_code
        if self.county_fips:            d["county_fips"]            = self.county_fips
        if self.state_fips:             d["state_fips"]             = self.state_fips
        if self.cbsa_code:              d["cbsa_code"]              = self.cbsa_code
        if self.start_year:             d["start_year"]             = self.start_year
        if self.end_year:               d["end_year"]               = self.end_year
        if self.program_type:           d["program_type"]           = self.program_type
        if self.household_size:         d["household_size"]         = self.household_size
        if self.limit is not None:      d["limit"]                  = self.limit
        if self.filters:                d["filters"]                = self.filters
        if self.clarification_needed:   d["clarification_needed"]   = self.clarification_needed
        if self.clarification_question: d["clarification_question"] = self.clarification_question
        d.update(self.extra)
        return {k: v for k, v in d.items() if v is not None}


class ParamExtractor:
    def __init__(self, llm_client, selector=None) -> None:
        """selector: optional ParamSelector. When provided, only the parameters
        semantically relevant to the question are put in the prompt. When None,
        the full static catalog (_SYSTEM) is used."""
        self.llm = llm_client
        self.selector = selector

    def extract(
        self,
        question: str,
        history: list[dict] | None = None,
    ) -> ExtractedParams:
        user_content = question
        if history:
            history_text = "\n".join(
                f"{m['role'].upper()}: {m.get('question') or m.get('answer') or ''}"
                for m in history[-6:]
            )
            user_content = f"RECENT CONVERSATION:\n{history_text}\n\nCURRENT QUESTION:\n{question}"

        # Narrow the parameter catalog semantically when a selector is available.
        if self.selector is not None:
            system_prompt = _build_system(self.selector.build_block(question))
        else:
            system_prompt = _SYSTEM

        raw = self.llm.complete(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_content},
            ],
            label="ParamExtractor",
        )
        data = parse_json_object(raw)
        return ExtractedParams.from_dict(data)
