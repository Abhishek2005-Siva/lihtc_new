"""Synthesizer — turns the complete scratchpad into a structured answer.

Model: 70B, temperature 0.1
Input:  original question + full scratchpad (thoughts, actions, observations)
Output: {direct_answer, evidence, regulatory_basis, caveats, conclusion}
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from scripts.agent.shared.scratchpad import Scratchpad
from scripts.agent.shared.parser import parse_json_object


_SYSTEM = """\
You synthesize Neo4j graph query results into a structured answer for an underwriter.

You receive a JSON payload with:
- question: the original user question
- intent: the classification of what was asked
- known_facts: key-value facts extracted during the ReAct loop
- steps_taken: list of {thought, action, params, cypher, observation}
- termination_reason: why the agent stopped

RULES:
- Reason ONLY from the data returned in observations. Do not apply domain knowledge
  that contradicts or supplements the query results.
- Always cite specific field values from the results in direct_answer.
- Never say "Yes" or "No" without citing the data that supports it.
- If the data is insufficient to answer fully, say so in caveats.

Return EXACTLY these five fields as a JSON object (no markdown fence):

direct_answer:
  Lead with YES/NO for eligibility questions, or the key value for lookups.
  Always cite the specific values from the results.

evidence:
  Bullet list of result fields that support the answer.
  Format: "field_name = value -- what this means"
  Include ALL relevant fields, not just the decisive one.

regulatory_basis:
  The relevant rule, statute, or program definition. Write "N/A" if purely factual.

caveats:
  Data limitations, year assumptions, or anything to verify. "None." if no caveats.

conclusion:
  One action-oriented sentence: what the underwriter should do next.

Return ONLY JSON:
{
  "direct_answer": "...",
  "evidence": "...",
  "regulatory_basis": "...",
  "caveats": "...",
  "conclusion": "..."
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
    """Synthesizer LLM — reads scratchpad, produces structured answer."""

    def __init__(self, llm_client) -> None:
        self.llm = llm_client

    def synthesize(self, scratchpad: Scratchpad) -> SynthesisResult:
        payload = json.dumps(scratchpad.to_synthesis_dict(), indent=2, default=str)
        messages = [
            {"role": "system", "content": _SYSTEM},
            {"role": "user",   "content": payload},
        ]
        raw = self.llm.complete(messages, temperature=0.1, label="Synthesizer")
        data = parse_json_object(raw)
        return SynthesisResult(
            direct_answer=_coerce_str(data.get("direct_answer", "")),
            evidence=_coerce_str(data.get("evidence", "")),
            regulatory_basis=_coerce_str(data.get("regulatory_basis", "")),
            caveats=_coerce_str(data.get("caveats", "")),
            conclusion=_coerce_str(data.get("conclusion", "")),
        )
