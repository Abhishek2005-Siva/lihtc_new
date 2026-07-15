"""Debug script — run one question through the pipeline and print detailed trace."""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent / "project"
sys.path.insert(0, str(_ROOT.parent))
sys.path.insert(0, str(_ROOT))

from backend.agent.geo_cache import GeoCache
from backend.agent.normalizer import Normalizer
from backend.agent.react_loop import Executor
from backend.agent.tool_cache import ToolCache
from backend.agents.cypher_builder import CypherBuilder, _post_process, _UNICODE_RE
from backend.agents.param_extractor import ParamExtractor
from backend.agents.path_planner import PathPlanner
from backend.agents.synthesizer import Synthesizer
from backend.graph.neo4j_client import Neo4jClient
from backend.graph.ontology import GraphOntology
from backend.llm.client import NvidiaLLMClient
from backend.pipeline.cypher_validator import CypherValidator
from backend.utils.config import load_settings

QUESTION = (
    "Show me all QCT designated tracts in Dallas County (county FIPS 48113) "
    "for 2025 where the poverty rate is above 25%."
)

ONTOLOGY_PATH = Path(__file__).parent / "project" / "data" / "ontology" / "lihtc_ontology.json"


def main():
    import os
    settings = load_settings()
    # Allow passing API key as CLI arg: python debug_query.py <api_key>
    if len(sys.argv) > 1:
        settings.nvidia_api_key = sys.argv[1]
    if not settings.nvidia_api_key:
        settings.nvidia_api_key = os.getenv("NVIDIA_API_KEY", "")
    if not settings.nvidia_api_key:
        print("ERROR: Pass NVIDIA_API_KEY as first argument: python debug_query.py nvapi-xxx")
        sys.exit(1)

    data = json.loads(ONTOLOGY_PATH.read_text(encoding="utf-8"))
    known = set(GraphOntology.__dataclass_fields__)
    ontology = GraphOntology(**{k: v for k, v in data.items() if k in known})

    neo4j = Neo4jClient(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)

    fast_llm  = NvidiaLLMClient(api_key=settings.nvidia_api_key, model=settings.fast_model)
    synth_llm = NvidiaLLMClient(api_key=settings.nvidia_api_key, model=settings.synth_model)

    # Monkey-patch CypherBuilder.build to print raw LLM output before post-processing
    original_build = CypherBuilder.build

    def patched_build(self, tool_name, params, ontology, error=None):
        from backend.llm.parser import parse_json_object
        from backend.agent.tool_registry import TOOLS, build_schema_context
        from backend.pipeline.rule_library import get_rules_for
        import json as _json

        tool = TOOLS[tool_name]
        schema_ctx = build_schema_context(tool_name, ontology)
        rules = get_rules_for(tool_name, params, tool.node_labels)
        rules_text = "\n".join(f"  {i+1}. {r}" for i, r in enumerate(rules))
        parts = [
            schema_ctx, "",
            f"AGENT PARAMS (authoritative):\n{_json.dumps(params, default=str)}", "",
            f"RULES FOR THIS CALL:\n{rules_text}",
        ]
        if error:
            parts += ["", f"PREVIOUS ATTEMPT FAILED WITH:\n{error}", "Fix ONLY the reported error."]
        user_content = "\n".join(parts)

        from backend.agents.cypher_builder import _SYSTEM
        raw = self.llm.complete(
            [{"role": "system", "content": _SYSTEM},
             {"role": "user",   "content": user_content}],
            label=f"CypherBuilder:{tool_name}" + (" [retry]" if error else ""),
        )
        print(f"\n{'='*60}")
        print(f"[CypherBuilder] tool={tool_name} error={bool(error)}")
        print(f"--- RAW LLM RESPONSE ---\n{raw}\n")

        # Show non-ASCII chars
        non_ascii = [(i, c, ord(c)) for i, c in enumerate(raw) if ord(c) > 127]
        if non_ascii:
            print(f"[!] {len(non_ascii)} non-ASCII chars in raw response:")
            for idx, ch, cp in non_ascii[:20]:
                print(f"    pos={idx} char=U+{cp:04X}")
        else:
            print("[✓] No non-ASCII chars in raw response")

        parsed = parse_json_object(raw)
        raw_cypher = parsed.get("cypher", "").strip()
        print(f"--- CYPHER (pre-post-process) ---\n{raw_cypher}\n")

        # Show non-ASCII in cypher
        non_ascii_c = [(i, c, ord(c)) for i, c in enumerate(raw_cypher) if ord(c) > 127]
        if non_ascii_c:
            print(f"[!] {len(non_ascii_c)} non-ASCII chars in parsed cypher:")
            for idx, ch, cp in non_ascii_c[:30]:
                print(f"    pos={idx} char=U+{cp:04X} context=...{repr(raw_cypher[max(0,idx-5):idx+5])}...")
        else:
            print("[✓] No non-ASCII chars in parsed cypher")

        exec_params = dict(params)
        exec_params.update({k: v for k, v in parsed.get("params", {}).items() if k not in params})
        cypher = _post_process(raw_cypher, exec_params)

        non_ascii_after = [(i, c, ord(c)) for i, c in enumerate(cypher) if ord(c) > 127]
        if non_ascii_after:
            print(f"[!] {len(non_ascii_after)} non-ASCII chars STILL in cypher after post_process!")
        else:
            print("[✓] Clean after post_process")

        print(f"--- CYPHER (post-post-process) ---\n{cypher}\n")
        return cypher, exec_params

    CypherBuilder.build = patched_build

    executor = Executor(
        param_extractor=ParamExtractor(fast_llm),
        path_planner=PathPlanner(fast_llm),
        cypher_builder=CypherBuilder(fast_llm),
        validator=CypherValidator(ontology),
        synthesizer=Synthesizer(synth_llm),
        neo4j_client=neo4j,
        normalizer=Normalizer(fast_llm),
        ontology=ontology,
        geo_cache=GeoCache(),
        tool_cache=ToolCache(),
    )

    print(f"QUESTION: {QUESTION}\n")
    params, plan, scratchpad, synthesis = executor.run(QUESTION)

    print("\n" + "="*60)
    print("EXTRACTED PARAMS:", json.dumps(params.to_dict(), indent=2))
    print("\nPLAN:", plan.selected_path)
    for p in plan.paths:
        marker = "✓" if p.id == plan.selected_path else " "
        print(f"  [{marker}] {p.id}: {p.description}")
        for s in p.steps:
            print(f"       {s.tool}({s.params})")

    print("\nSTEPS TAKEN:")
    for s in scratchpad.steps_taken:
        status = "ERROR" if s.error else f"{s.row_count} rows"
        print(f"  {s.tool} [{status}]")
        if s.error:
            print(f"    ERROR: {s.error}")

    print("\nGAPS:", scratchpad.gaps)
    print("\nSYNTHESIS:", synthesis.direct_answer)


if __name__ == "__main__":
    main()
