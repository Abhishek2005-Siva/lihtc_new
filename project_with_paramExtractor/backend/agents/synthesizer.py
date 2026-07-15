"""Agent 4 — Synthesizer.

Reads all observations from the executor and produces a structured answer.
Applies LIHTC regulatory rules to interpret the data.
Model: 70B, temperature 0.1.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.llm.parser import parse_json_object

if TYPE_CHECKING:
    from backend.utils.scratchpad import Scratchpad

_SYSTEM = """\
You synthesize Neo4j graph query results into a structured answer for a LIHTC underwriter.

You receive:
- question:     the original user question
- end_goal:     what the underwriter is trying to determine
- observations: keyed by tool name — the actual data returned from Neo4j
- gaps:         data that could not be retrieved and why
- steps_taken:  summary of what was executed
- recent_history: (optional) last 2-3 Q&A turns for conversational continuity

RULES:
- Reason ONLY from the data in observations. Do not invent facts.
- Always cite specific field values (e.g. "is_designated = true for year 2025").
- Never say Yes/No without citing the observation that supports it.
- If observations are empty or gaps exist, acknowledge what is missing.
- If a gap reason starts with "Insufficient params", tell the user exactly what additional
  information they need to provide (e.g. "Please provide the census tract FIPS code (11 digits)").
  Do not guess or fabricate the missing value.
- Use recent_history only for conversational continuity ("As we saw for tract X...").
  Never use prior history as a source of facts — only current observations are authoritative.
- For basis_boost_eligibility: the 30% boost applies if QCT OR DDA is designated.
  It applies ONCE even if both are designated — never additive.

Return ONLY a JSON object:
{
  "direct_answer":    "YES/NO with data citation, or the key value",
  "evidence":         "bullet list: field = value -- what this means",
  "regulatory_basis": "IRC section or HUD regulation. N/A if purely factual.",
  "caveats":          "limitations or things to verify. None. if none.",
  "conclusion":       "one action-oriented sentence for the underwriter"
}
"""


def _coerce_str(value) -> str:
    if isinstance(value, list):
        return "\n".join(str(i).strip("- ").strip() for i in value if i)
    return str(value) if value is not None else ""


@dataclass
class SynthesisResult:
    direct_answer: str
    evidence: str
    regulatory_basis: str
    caveats: str
    conclusion: str


class Synthesizer:
    def __init__(self, llm_client) -> None:
        self.llm = llm_client

    def synthesize(
        self,
        scratchpad: "Scratchpad",
        history: list[dict] | None = None,
    ) -> SynthesisResult:
        payload = scratchpad.to_synthesis_dict()
        if history:
            payload["recent_history"] = history[-6:]
        raw = self.llm.complete(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": json.dumps(payload, indent=2, default=str)},
            ],
            temperature=0.1,
            label="Synthesizer",
        )
        data = parse_json_object(raw)
        return SynthesisResult(
            direct_answer   =_coerce_str(data.get("direct_answer", "")),
            evidence        =_coerce_str(data.get("evidence", "")),
            regulatory_basis=_coerce_str(data.get("regulatory_basis", "")),
            caveats         =_coerce_str(data.get("caveats", "")),
            conclusion      =_coerce_str(data.get("conclusion", "")),
        )
