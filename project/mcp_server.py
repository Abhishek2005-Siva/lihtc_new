"""MCP server entrypoint — wires dependencies and registers all tools.

Architecture:
  - The CALLING LLM (whatever's on the other end of the MCP connection) decides
    WHICH tool to call and WHAT parameters to pass — this replaces Orchestrator's
    job for MCP-driven calls.
  - Every tool call still runs through CypherBuilder + every deterministic
    guardrail built for this pipeline (backend.pipeline.tool_call.run_tool_call)
    to safely turn those parameters into Cypher and execute it. Nothing here
    lets the calling model write or see raw Cypher.
  - Tool definitions live one-per-file under backend/mcp/tools/.

Run:
    python mcp_server.py

Requires NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD / NVIDIA_API_KEY to be set
(via .env or real environment variables) — same as the Streamlit app.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP

from backend.agents.cypher_builder import CypherBuilder
from backend.cache.tool_cache import ToolCache
from backend.graph.neo4j_client import Neo4jClient
from backend.graph.ontology import GraphOntology
from backend.llm.client import NvidiaLLMClient
from backend.mcp.context import ToolContext
from backend.mcp.tools import register_all
from backend.utils.config import load_settings

_ONTOLOGY_PATH = _PROJECT_ROOT / "data" / "ontology" / "lihtc_ontology.json"


def _load_ontology() -> GraphOntology:
    data = json.loads(_ONTOLOGY_PATH.read_text(encoding="utf-8"))
    known = set(GraphOntology.__dataclass_fields__)
    return GraphOntology(**{k: v for k, v in data.items() if k in known})


def build_context() -> ToolContext:
    settings = load_settings()
    if not settings.nvidia_api_key:
        raise RuntimeError(
            "NVIDIA_API_KEY is not set. Add it to .env or your environment before "
            "starting the MCP server — CypherBuilder needs it to write Cypher."
        )
    neo4j = Neo4jClient(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
    llm = NvidiaLLMClient(api_key=settings.nvidia_api_key, model=settings.fast_model)
    return ToolContext(
        cypher_builder=CypherBuilder(llm),
        neo4j_client=neo4j,
        tool_cache=ToolCache(),
        ontology=_load_ontology(),
    )


def create_server() -> FastMCP:
    mcp = FastMCP(
        "lihtc-graph",
        instructions=(
            "Query a LIHTC (Low-Income Housing Tax Credit) knowledge graph covering "
            "QCT/DDA designations, Section 8 AMI income limits, and HMDA fair lending "
            "risk data for U.S. census tracts. Pick the most specific tool for the "
            "question — use custom_query only as a last resort."
        ),
    )
    register_all(mcp, build_context())
    return mcp


if __name__ == "__main__":
    create_server().run(transport="stdio")
