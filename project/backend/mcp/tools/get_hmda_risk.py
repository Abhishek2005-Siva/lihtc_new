"""MCP tool: get_hmda_risk"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from backend.mcp.context import ToolContext


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool()
    def get_hmda_risk(fips_code: str, year: int) -> dict:
        """Get the fair lending risk profile (HMDA) for the metro area containing a
        tract, for a single year. Returns risk_tier, denial rates, and disparity ratio.
        fips_code is the 11-digit census tract FIPS code.
        """
        return ctx.call("get_hmda_risk", f"What is the HMDA fair lending risk for census tract {fips_code} in {year}?")
