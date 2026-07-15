"""Small Neo4j client wrapper with connection pooling."""
from __future__ import annotations

from typing import Any

from neo4j import GraphDatabase, Driver


# Module-level driver cache keyed by (uri, user) — reused across Streamlit reruns.
# The Neo4j Python driver maintains an internal connection pool, so sharing one
# driver instance avoids TCP handshake overhead on every question.
_driver_cache: dict[tuple[str, str], Driver] = {}


def _get_driver(uri: str, user: str, password: str) -> Driver:
    key = (uri, user)
    if key not in _driver_cache:
        _driver_cache[key] = GraphDatabase.driver(uri, auth=(user, password))
    return _driver_cache[key]


class Neo4jClient:
    def __init__(self, uri: str, user: str, password: str) -> None:
        self.driver = _get_driver(uri, user, password)

    def run_read(self, cypher: str, params: dict[str, Any] | None = None, timeout: int | None = None) -> list[dict[str, Any]]:
        with self.driver.session() as session:
            return [dict(record) for record in session.run(cypher, parameters=params or {}, timeout=timeout)]

    def close(self) -> None:
        # No-op: driver is pooled at module level; caller should not close it.
        pass

