"""MCP tool: search_hmda_risk"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from backend.mcp.context import ToolContext


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool()
    def search_hmda_risk(year: int, risk_tier: str | None = None, state_fips: str | None = None) -> dict:
        """Find metro areas by HMDA fair lending risk for a given year. Optionally
        filter by risk_tier ('low', 'moderate', 'elevated', 'high') or state_fips
        (2-digit).
        """
        q = f"Show me metro areas by HMDA fair lending risk for {year}"
        if risk_tier:
            q += f" with risk tier {risk_tier}"
        if state_fips:
            q += f" in state FIPS {state_fips}"
        return ctx.call("search_hmda_risk", q + ".")
