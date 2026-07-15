"""Step 4 — Execute query against Neo4j.

Runs a validated Cypher query and returns the result rows.
Returns (rows, error_string). On success error_string is None.
"""
from __future__ import annotations

from typing import Any

_DEFAULT_TIMEOUT = 10  # seconds


def run(
    neo4j_client,
    cypher: str,
    params: dict[str, Any],
    timeout: int = _DEFAULT_TIMEOUT,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Execute a Cypher read query.

    Returns:
        (rows, None)       on success.
        (None, error_str)  on failure — caller decides whether to retry.
    """
    try:
        rows = neo4j_client.run_read(cypher, params, timeout=timeout)
        return rows, None
    except Exception as exc:
        return None, str(exc)
