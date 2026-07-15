"""Scratchpad — carries state across all ReAct loop iterations."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Step:
    thought: str
    action: str
    params: dict[str, Any]
    cypher: str
    observation: dict[str, Any]   # {"rows": [...]} or {"error": "..."}


@dataclass
class Scratchpad:
    question: str
    intent: str | None
    # Facts extracted from observations — keyed by field name for fast lookup.
    # Orchestrator can read these without scanning all step observations.
    known_facts: dict[str, Any] = field(default_factory=dict)
    steps_taken: list[Step] = field(default_factory=list)
    termination_reason: str = ""   # "agent_finished" | "max_steps" | "loop_detected" | "consecutive_errors"

    # ── Helpers ──────────────────────────────────────────────────────────────

    def add_step(self, step: Step) -> None:
        self.steps_taken.append(step)

    def update_facts(self, new_facts: dict[str, Any]) -> None:
        self.known_facts.update({k: v for k, v in new_facts.items() if v is not None})

    def last_error(self) -> str | None:
        """Return the error from the most recent step, or None."""
        if self.steps_taken:
            return self.steps_taken[-1].observation.get("error")
        return None

    def consecutive_errors(self) -> int:
        """Count how many of the last N steps returned errors."""
        count = 0
        for step in reversed(self.steps_taken):
            if "error" in step.observation:
                count += 1
            else:
                break
        return count

    def seen_actions(self) -> set[str]:
        return {json.dumps({"action": s.action, "params": s.params}, sort_keys=True)
                for s in self.steps_taken}

    def to_prompt_dict(self) -> dict:
        """Compact representation for the Orchestrator prompt."""
        return {
            "question": self.question,
            "intent": self.intent,
            "known_facts": self.known_facts,
            "steps_taken": [
                {
                    "thought": s.thought,
                    "action": s.action,
                    "params": s.params,
                    "observation": s.observation,
                }
                for s in self.steps_taken
            ],
        }

    def to_synthesis_dict(self) -> dict:
        """Full representation for the Synthesizer prompt."""
        return {
            "question": self.question,
            "intent": self.intent,
            "known_facts": self.known_facts,
            "steps_taken": [
                {
                    "thought": s.thought,
                    "action": s.action,
                    "params": s.params,
                    "cypher": s.cypher,
                    "observation": s.observation,
                }
                for s in self.steps_taken
                if s.action not in ("FINISH", "[unparseable]")
            ],
            "termination_reason": self.termination_reason,
        }
