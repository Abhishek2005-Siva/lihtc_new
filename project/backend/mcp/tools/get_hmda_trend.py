"""MCP tool: get_hmda_trend"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from backend.mcp.context import ToolContext


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool()
    def get_hmda_trend(fips_code: str, start_year: int, end_year: int) -> dict:
        """Get the fair lending risk (HMDA) trend across multiple years for a
        tract's metro area. fips_code is the 11-digit census tract FIPS code.
        """
        return ctx.call(
            "get_hmda_trend",
            f"What is the HMDA fair lending risk trend for census tract {fips_code} from {start_year} to {end_year}?",
        )
