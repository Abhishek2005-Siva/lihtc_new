"""LLM provider wrapper -- NVIDIA-hosted models via OpenAI-compatible API."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterator

from openai import APITimeoutError, OpenAI, RateLimitError


@dataclass
class LLMCall:
    label: str
    messages: list[dict]
    response: str = ""
    streamed: bool = False
    # Populated after the fact for CypherBuilder calls: the outcome of running
    # the generated Cypher against Neo4j (rows/error), shown in the LLM call log.
    exec_result: dict | None = None


class LLMClient:
    call_log: list[LLMCall]

    def complete(self, messages: list[dict], *, temperature: float = 0.0, label: str = "LLM call") -> str:
        raise NotImplementedError

    def stream(self, messages: list[dict], *, temperature: float = 0.1, label: str = "LLM call") -> Iterator[str]:
        raise NotImplementedError


class NvidiaLLMClient(LLMClient):
    _BASE_URL = "https://integrate.api.nvidia.com/v1"

    _TIMEOUT = 300  # seconds -- NVIDIA shared infra can be slow under load

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
                    max_tokens=4096,
                )
                content = resp.choices[0].message.content or ""
                if not content.strip():
                    # Empty response — retry (model sometimes returns nothing on first attempt)
                    if attempt < 3:
                        time.sleep(wait)
                        wait *= 2
                        continue
                    raise ValueError(
                        f"[{label}] Model returned empty response after {attempt+1} attempts."
                    )
                entry.response = content
                self.call_log.append(entry)
                return content
            except (RateLimitError, APITimeoutError):
                if attempt == 3:
                    raise
                time.sleep(wait)
                wait *= 2  # 5 → 10 → 20 → give up
        raise RuntimeError(f"[{label}] All retry attempts exhausted.")

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
