"""Shared dependencies every MCP tool needs.

Built once at server startup (see mcp_server.py) and passed into each tool
module's register() function, so every tool shares the same CypherBuilder,
Neo4j connection, tool cache, and ontology — and every tool call goes through
the exact same guardrail pipeline (backend.pipeline.tool_call.run_tool_call)
that the Streamlit Executor uses.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.agents.cypher_builder import CypherBuilder
from backend.cache.tool_cache import ToolCache
from backend.pipeline.tool_call import run_tool_call


@dataclass
class ToolContext:
    cypher_builder: CypherBuilder
    neo4j_client: Any
    tool_cache: ToolCache
    ontology: Any

    def call(self, tool_name: str, question: str) -> dict[str, Any]:
        """Run one tool call through the shared guardrail pipeline and shape
        the result for an MCP response."""
        rows, cypher, error, exec_params = run_tool_call(
            self.cypher_builder, self.neo4j_client, self.tool_cache,
            tool_name, question, self.ontology,
        )
        if error:
            return {"error": error}
        return {"rows": rows, "row_count": len(rows or [])}
