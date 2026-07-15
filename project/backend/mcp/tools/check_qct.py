"""MCP tool: check_qct"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from backend.mcp.context import ToolContext


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool()
    def check_qct(fips_code: str, year: int) -> dict:
        """Check QCT designation status for a specific census tract and a single year.
        Returns is_designated, basis_boost_pct, qct_trigger_criterion,
        poverty_rate_at_designation, and income_criterion_ratio.
        fips_code is the 11-digit census tract FIPS code.
        """
        return ctx.call("check_qct", f"Is census tract {fips_code} QCT designated in {year}?")
