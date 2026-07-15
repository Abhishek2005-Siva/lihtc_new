"""MCP tool: search_dda_areas"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from backend.mcp.context import ToolContext


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool()
    def search_dda_areas(year: int, state_fips: str | None = None) -> dict:
        """Find all DDA areas (metro SDDAs and non-metro NMDDAs) for a given year,
        optionally filtered by state_fips (2-digit). Returns area names and
        basis_boost_pct.
        """
        q = f"Show me all DDA areas for {year}"
        if state_fips:
            q += f" in state FIPS {state_fips}"
        return ctx.call("search_dda_areas", q + ".")
