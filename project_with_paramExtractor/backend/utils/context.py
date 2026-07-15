"""Conversation context — carries state across multiple turns.

Stores message history and geographic facts that persist between questions.
If the user asks two questions about the same census tract, the second question
can skip get_tract_context because county_fips and cbsa_code are already known.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Message:
    role: str        # "user" | "assistant"
    content: str
    synthesis: dict[str, Any] | None = None


class ConversationContext:
    def __init__(self) -> None:
        self.messages: list[Message] = []
        self.persistent_facts: dict[str, Any] = {}

    def add_user(self, text: str) -> None:
        self.messages.append(Message(role="user", content=text))

    def add_assistant(self, text: str, synthesis: dict[str, Any] | None = None) -> None:
        self.messages.append(Message(role="assistant", content=text, synthesis=synthesis))

    def update_facts(self, new_facts: dict[str, Any]) -> None:
        self.persistent_facts.update({k: v for k, v in new_facts.items() if v is not None})

    def seed_facts_for(self, question: str) -> dict[str, Any]:
        """Return persistent facts to seed a new scratchpad.

        If the new question references the same FIPS as before, carry over
        geographic facts so the agent skips a redundant get_tract_context call.
        """
        fips_m = re.search(r"\b(\d{11})\b", question)
        if fips_m and fips_m.group(1) == self.persistent_facts.get("fips_code"):
            return dict(self.persistent_facts)
        return {}

    def clear(self) -> None:
        self.messages.clear()
        self.persistent_facts.clear()
