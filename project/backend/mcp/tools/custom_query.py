"""MCP tool: custom_query"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from backend.mcp.context import ToolContext


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool()
    def custom_query(question: str) -> dict:
        """LAST RESORT fallback — use ONLY when no other tool's description matches
        the question. Gives the query-builder access to the full graph schema (every
        node label and relationship) instead of one tool's narrow slice. Pass the
        question in plain English.
        """
        return ctx.call("custom_query", question)
