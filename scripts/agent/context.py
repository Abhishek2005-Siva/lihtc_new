"""Conversation context — carries state across multiple turns.

A single Context instance lives for the duration of a user session.
It stores the message history and extracted facts that persist across
questions (e.g. if the user already established which tract they are
asking about, the next question can skip get_tract_context).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Message:
    role: str        # "user" | "assistant"
    content: str
    synthesis: dict[str, Any] | None = None   # structured answer dict if assistant


class ConversationContext:
    """Carries message history and persistent facts across turns."""

    def __init__(self) -> None:
        self.messages: list[Message] = []
        # Facts that persist across turns — e.g. once we know a tract's
        # county_fips and cbsa_code, we don't re-fetch them next question.
        self.persistent_facts: dict[str, Any] = {}

    # ── Message management ────────────────────────────────────────────────────

    def add_user(self, text: str) -> None:
        self.messages.append(Message(role="user", content=text))

    def add_assistant(self, text: str, synthesis: dict[str, Any] | None = None) -> None:
        self.messages.append(Message(role="assistant", content=text, synthesis=synthesis))

    def last_user_message(self) -> str | None:
        for msg in reversed(self.messages):
            if msg.role == "user":
                return msg.content
        return None

    def history_for_prompt(self, max_turns: int = 3) -> list[dict]:
        """Return the last N turns as a list of {role, content} dicts."""
        recent = [m for m in self.messages if m.role in ("user", "assistant")]
        trimmed = recent[-(max_turns * 2):]
        return [{"role": m.role, "content": m.content} for m in trimmed]

    # ── Fact persistence ──────────────────────────────────────────────────────

    def update_facts(self, new_facts: dict[str, Any]) -> None:
        """Merge new facts — used after each successful tool call."""
        self.persistent_facts.update({k: v for k, v in new_facts.items() if v is not None})

    def seed_facts_for(self, question: str) -> dict[str, Any]:
        """Return persistent facts relevant to seed a new scratchpad.

        If the user asked about the same FIPS code as before, we can skip
        get_tract_context — the geographic facts are already known.
        """
        import re
        fips_in_q = re.search(r"\b(\d{11})\b", question)
        if fips_in_q:
            known_fips = self.persistent_facts.get("fips_code")
            if known_fips == fips_in_q.group(1):
                return dict(self.persistent_facts)
        # Different or no FIPS — start fresh
        return {}

    def clear(self) -> None:
        self.messages.clear()
        self.persistent_facts.clear()
