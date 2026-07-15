"""Tool cache — cross-turn Neo4j result cache.

Keyed by "tool_name|param1=val1|param2=val2". Avoids redundant Neo4j calls
for identical tool+param combinations within a session.
Search tools are never cached (filters may vary with same params).
"""
from __future__ import annotations

import json
from typing import Any

_NEVER_CACHE = {"search_tracts", "search_dda_areas", "search_hmda_risk"}


def _cache_key(tool_name: str, params: dict[str, Any]) -> str:
    sorted_params = json.dumps(params, sort_keys=True, default=str)
    return f"{tool_name}|{sorted_params}"


class ToolCache:
    def __init__(self) -> None:
        self._cache: dict[str, list[dict[str, Any]]] = {}

    def get(self, tool_name: str, params: dict[str, Any]) -> list[dict[str, Any]] | None:
        if tool_name in _NEVER_CACHE:
            return None
        return self._cache.get(_cache_key(tool_name, params))

    def store(self, tool_name: str, params: dict[str, Any], rows: list[dict[str, Any]]) -> None:
        if tool_name not in _NEVER_CACHE:
            self._cache[_cache_key(tool_name, params)] = rows

    def invalidate_for_fips(self, fips_code: str) -> None:
        self._cache = {
            k: v for k, v in self._cache.items()
            if fips_code not in k
        }

    def clear(self) -> None:
        self._cache.clear()
