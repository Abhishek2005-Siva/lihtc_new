"""Scratchpad — per-turn working memory. Resets every question."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StepRecord:
    tool: str
    params: dict[str, Any]
    cypher: str
    rows: list[dict[str, Any]]
    row_count: int
    error: str | None = None
    type_fixes: list[str] = field(default_factory=list)
    path_id: str = ""
    validation_score: float | None = None
    validation_reason: str = ""


@dataclass
class Scratchpad:
    question: str
    end_goal: str
    steps_taken: list[StepRecord] = field(default_factory=list)
    observations: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    gaps: list[dict[str, Any]] = field(default_factory=list)
    _best_score: dict[str, float] = field(default_factory=dict, repr=False)

    def add_step(self, record: StepRecord) -> None:
        """Log the step. Does NOT touch observations — call record_observation()
        once validation score is known, so multiple paths competing for the same
        tool don't just let the last one processed silently win."""
        self.steps_taken.append(record)

    def record_observation(self, tool: str, rows: list[dict[str, Any]], score: float) -> None:
        """Keep only the highest-scoring passing result per tool across all paths."""
        if not rows:
            return
        if tool not in self._best_score or score > self._best_score[tool]:
            self._best_score[tool] = score
            self.observations[tool] = rows

    def add_gap(self, tool: str, params: dict, reason: str) -> None:
        self.gaps.append({"tool": tool, "params": params, "reason": reason})

    def to_synthesis_dict(self) -> dict[str, Any]:
        return {
            "question":     self.question,
            "end_goal":     self.end_goal,
            "observations": {
                tool: rows[:10]
                for tool, rows in self.observations.items()
            },
            "gaps":         self.gaps,
            "steps_taken": [
                {
                    "tool":       s.tool,
                    "params":     s.params,
                    "row_count":  s.row_count,
                    "sample":     s.rows[:3],
                    "error":      s.error,
                    "path_id":    s.path_id,
                }
                for s in self.steps_taken
            ],
        }
