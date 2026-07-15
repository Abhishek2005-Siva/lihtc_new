"""Geo cache — cross-turn geographic fact cache.

Stores geographic context (county_fips, cbsa_code, is_metro, state_fips) keyed
by fips_code. Seeded into scratchpad known_facts when the same tract reappears.
"""
from __future__ import annotations

from typing import Any

_GEO_KEYS = {"is_metro_tract", "is_metro", "county_fips", "cbsa_code", "state_fips",
             "county_name", "state_name", "metro_name"}


class GeoCache:
    def __init__(self) -> None:
        self._cache: dict[str, dict[str, Any]] = {}

    def store(self, fips_code: str, facts: dict[str, Any]) -> None:
        geo = {k: v for k, v in facts.items() if k in _GEO_KEYS and v is not None}
        if geo:
            self._cache[fips_code] = geo

    def get(self, fips_code: str) -> dict[str, Any]:
        return dict(self._cache.get(fips_code, {}))

    def clear(self) -> None:
        self._cache.clear()
