"""MCP tool: get_state_ami_limits"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from backend.mcp.context import ToolContext


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool()
    def get_state_ami_limits(state_fips: str, year: int, program_type: str = "VLI") -> dict:
        """Get STATE-level (not county/metro) Section 8 AMI income limits.
        state_fips is the 2-digit state FIPS code. program_type: VLI (50% AMI,
        default), ELI (30% AMI), or LI (80% AMI).
        """
        return ctx.call(
            "get_state_ami_limits",
            f"What are the {program_type} state-level AMI income limits for state FIPS {state_fips} in {year}?",
        )
