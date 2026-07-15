"""LLM provider wrapper -- NVIDIA-hosted models via OpenAI-compatible API."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterator

from openai import OpenAI, RateLimitError


@dataclass
class LLMCall:
    label: str
    messages: list[dict]
    response: str = ""
    streamed: bool = False


class LLMClient:
    call_log: list[LLMCall]

    def complete(self, messages: list[dict], *, temperature: float = 0.0, label: str = "LLM call") -> str:
        raise NotImplementedError

    def stream(self, messages: list[dict], *, temperature: float = 0.1, label: str = "LLM call") -> Iterator[str]:
        raise NotImplementedError


class NvidiaLLMClient(LLMClient):
    _BASE_URL = "https://integrate.api.nvidia.com/v1"

    _TIMEOUT = 120  # seconds -- prevents indefinite hang on NVIDIA API

    def __init__(self, api_key: str, model: str = "meta/llama-3.1-70b-instruct") -> None:
        self._client = OpenAI(
            base_url=self._BASE_URL,
            api_key=api_key,
            timeout=self._TIMEOUT,
        )
        self.model = model
        self.call_log: list[LLMCall] = []

    def complete(self, messages: list[dict], *, temperature: float = 0.0, label: str = "LLM call") -> str:
        entry = LLMCall(label=label, messages=messages)
        wait = 5  # initial backoff seconds
        for attempt in range(4):
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=2048,
                )
                entry.response = resp.choices[0].message.content or ""
                self.call_log.append(entry)
                return entry.response
            except RateLimitError:
                if attempt == 3:
                    raise
                time.sleep(wait)
                wait *= 2  # 5 → 10 → 20 → give up

    def stream(self, messages: list[dict], *, temperature: float = 0.1, label: str = "LLM call") -> Iterator[str]:
        entry = LLMCall(label=label, messages=messages, streamed=True)
        chunks: list[str] = []
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=2048,
            stream=True,
        )
        for chunk in resp:
            delta = chunk.choices[0].delta.content
            if delta:
                chunks.append(delta)
                yield delta
        entry.response = "".join(chunks)
        self.call_log.append(entry)
