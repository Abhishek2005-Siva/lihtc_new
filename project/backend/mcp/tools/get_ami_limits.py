"""MCP tool: get_ami_limits"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from backend.mcp.context import ToolContext


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool()
    def get_ami_limits(fips_code: str, year: int, program_type: str = "VLI") -> dict:
        """Get Section 8 AMI income limits for the county containing a tract.
        Returns limit_1person..limit_8person and max_rent.
        program_type: VLI (50% AMI, default), ELI (30% AMI), or LI (80% AMI).
        fips_code is the 11-digit census tract FIPS code.
        """
        return ctx.call(
            "get_ami_limits",
            f"What are the {program_type} AMI income limits for census tract {fips_code} in {year}?",
        )
