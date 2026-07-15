"""Agent — Output Validator.

Checks whether the rows returned by Neo4j actually answer the user's question.
Called AFTER a successful query execution — not used to validate Cypher syntax.

Returns a score (0.0-1.0) and a short explanation.
Score >= 0.7 = results are relevant and useful.
Score <  0.7 = results don't match what was asked (mismatched tool/query).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from backend.llm.parser import parse_json_object

_SYSTEM = """\
You are an output relevance validator for a LIHTC knowledge graph assistant.

You receive:
- QUESTION: the original user question
- TOOL: the graph tool that was called
- ROWS: the first few rows of data returned from Neo4j (may be empty)

Your job: decide if the returned data actually answers the question.

Score 0.0 to 1.0:
  1.0 = results directly and completely answer the question
  0.7 = results are relevant, partially answer the question
  0.5 = results are tangentially related but miss the key ask
  0.0 = results are completely unrelated or empty when data was expected

Return ONLY this JSON:
{"score": 0.85, "reason": "one sentence explanation"}

No markdown. No extra keys.
"""


@dataclass
class ValidationResult:
    score: float
    reason: str

    @property
    def passed(self) -> bool:
        return self.score >= 0.7


class CypherValidatorAgent:
    def __init__(self, llm_client, ontology) -> None:
        self.llm = llm_client
        self.ontology = ontology

    def validate_output(
        self,
        question: str,
        tool_name: str,
        rows: list[dict],
    ) -> ValidationResult:
        """Score how well the returned rows answer the question."""
        sample = rows[:5] if rows else []
        user_content = "\n".join([
            f"QUESTION: {question}",
            f"TOOL: {tool_name}",
            f"ROWS: {json.dumps(sample, default=str)}",
        ])
        try:
            raw = self.llm.complete(
                [
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user",   "content": user_content},
                ],
                label=f"OutputValidator:{tool_name}",
            )
            data = parse_json_object(raw)
            return ValidationResult(
                score=float(data.get("score", 1.0)),
                reason=data.get("reason", ""),
            )
        except Exception:
            # Fail open — don't block good results due to validator error
            return ValidationResult(score=1.0, reason="Validator unavailable.")
