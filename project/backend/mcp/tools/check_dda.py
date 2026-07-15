"""MCP tool: check_dda"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from backend.mcp.context import ToolContext


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool()
    def check_dda(fips_code: str, year: int) -> dict:
        """Check DDA (Difficult Development Area) status for a specific census tract
        and year. Checks both metro (SDDA) and non-metro (NMDDA) designation.
        Returns is_dda_designated, basis_boost_pct, sdda_area_name, nmdda_area_name.
        fips_code is the 11-digit census tract FIPS code.
        """
        return ctx.call("check_dda", f"Is census tract {fips_code} a Difficult Development Area in {year}?")
