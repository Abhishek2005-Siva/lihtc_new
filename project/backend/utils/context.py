"""Conversation context — chat message log for the UI only.

Each question is answered independently by the pipeline; nothing here feeds
back into ParamExtractor/PathPlanner/CypherBuilder. This exists purely so the
Streamlit chat window can redraw prior turns.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Message:
    role: str        # "user" | "assistant"
    content: str
    synthesis: dict[str, Any] | None = None


class ConversationContext:
    def __init__(self) -> None:
        self.messages: list[Message] = []

    def add_user(self, text: str) -> None:
        self.messages.append(Message(role="user", content=text))

    def add_assistant(self, text: str, synthesis: dict[str, Any] | None = None) -> None:
        self.messages.append(Message(role="assistant", content=text, synthesis=synthesis))

    def clear(self) -> None:
        self.messages.clear()
