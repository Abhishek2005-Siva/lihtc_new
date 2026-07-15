"""Every MCP tool module — one file per tool in backend.registry.tool_registry.TOOLS.

Each module exposes register(mcp, ctx) which defines and decorates its tool
function as an MCP tool, closing over the shared ToolContext.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from backend.mcp.context import ToolContext
from backend.mcp.tools import (
    check_dda,
    check_qct,
    custom_query,
    get_ami_limits,
    get_hmda_risk,
    get_hmda_trend,
    get_special_program_limits,
    get_state_ami_limits,
    search_dda_areas,
    search_hmda_risk,
    search_tracts,
)

_MODULES = (
    check_qct,
    check_dda,
    get_ami_limits,
    get_hmda_risk,
    get_hmda_trend,
    search_tracts,
    search_dda_areas,
    search_hmda_risk,
    get_state_ami_limits,
    get_special_program_limits,
    custom_query,
)


def register_all(mcp: FastMCP, ctx: ToolContext) -> None:
    for module in _MODULES:
        module.register(mcp, ctx)
