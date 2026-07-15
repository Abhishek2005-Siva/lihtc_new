# Dynamic Neo4j Ontology Agent

This scaffold is for an agent that reasons over a Neo4j ontology dynamically instead of selecting hardcoded business-specific Cypher tools.

## Core Flow

1. Understand the user's intent, constraints, filters, and expected output.
2. Build the minimum execution plan.
3. Generate dynamic information goals only when the request cannot be answered with one query.
4. Generate Cypher from the live ontology.
5. Validate labels, relationships, and properties before execution.
6. Retry with ontology context when Neo4j reports schema errors.
7. Combine query outputs and let the LLM perform reasoning, comparison, trend analysis, or summarization.

## Layout

- `frontend/` contains Streamlit UI entry points and reusable UI components.
- `backend/agent/` handles intent planning, execution, validation, and synthesis.
- `backend/graph/` handles Neo4j access, ontology loading, schema caching, and Cypher generation.
- `backend/llm/` wraps model calls and structured parsing.
- `backend/tools/` is reserved for non-graph generic tools such as web search, SEC API, or Python execution.
- `data/ontology/` and `data/cache/` store generated/cache artifacts.
- `tests/` holds unit and integration tests.

