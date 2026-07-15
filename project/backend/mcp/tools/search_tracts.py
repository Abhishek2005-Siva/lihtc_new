"""MCP tool: search_tracts"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from backend.mcp.context import ToolContext


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool()
    def search_tracts(
        year: int,
        county_fips: str | None = None,
        state_fips: str | None = None,
        is_qct_designated: bool | None = None,
        is_dda_designated: bool | None = None,
    ) -> dict:
        """Search census tracts by geography and designation filters. Provide EITHER
        county_fips (5-digit) OR state_fips (2-digit), not both. Set is_qct_designated
        or is_dda_designated to true to filter to only designated tracts; omit both to
        get every tract with its designation status.
        """
        parts = [f"Show me census tracts for year {year}"]
        if county_fips:
            parts.append(f"in county FIPS {county_fips}")
        if state_fips:
            parts.append(f"in state FIPS {state_fips}")
        if is_qct_designated:
            parts.append("that are QCT designated")
        if is_dda_designated:
            parts.append("that are DDA designated")
        return ctx.call("search_tracts", " ".join(parts) + ".")
