"""MCP tool: get_special_program_limits"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from backend.mcp.context import ToolContext


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool()
    def get_special_program_limits(
        year: int,
        hud_fmr_area_code: str | None = None,
        area_name: str | None = None,
        state: str | None = None,
    ) -> dict:
        """Get special HUD program income limits (e.g. Section 221 BMIR, Section 235,
        Section 236) — NOT the standard Section 8 VLI/ELI/LI tiers. This node has no
        graph relationship to CensusTract/County — identify the area directly via
        hud_fmr_area_code, area_name, or state (2-digit FIPS).
        """
        parts = [f"Show me special HUD program income limits for {year}"]
        if hud_fmr_area_code:
            parts.append(f"for HUD FMR area {hud_fmr_area_code}")
        if area_name:
            parts.append(f"for area {area_name}")
        if state:
            parts.append(f"in state FIPS {state}")
        return ctx.call("get_special_program_limits", " ".join(parts) + ".")
