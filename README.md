# LIHTC Agent

**Live site:** [lihtc-new.vercel.app](https://lihtc-new.vercel.app) · **Source:** [`/web`](web)

> The live site is a Vite + React landing page that explains the project. The application itself needs a Neo4j database and an NVIDIA API key, so it runs locally. Follow the setup steps below.

An agent that answers plain-English questions about Low-Income Housing Tax Credit (LIHTC) data. It reasons over a Neo4j knowledge graph by generating Cypher from the live ontology, instead of calling hardcoded, business-specific queries.

## Streamlit app

The agent UI. It needs a reachable Neo4j database: set `NEO4J_URI`, `NEO4J_USER` and `NEO4J_PASSWORD` (in a `.env` file locally, or in the app's Secrets on Streamlit Cloud). Paste your NVIDIA API key in the sidebar.

```bash
pip install -r requirements.txt
streamlit run project/frontend/app.py
```

**Deploy on Streamlit Community Cloud:** at [share.streamlit.io](https://share.streamlit.io) choose this repo, branch `main` and main file `project/frontend/app.py`.

## How it works

1. Understand the user's intent, constraints, filters and expected output.
2. Build the minimum execution plan.
3. Generate dynamic information goals only when one query cannot answer the request.
4. Generate Cypher from the live ontology.
5. Validate labels, relationships and properties before execution.
6. Retry with ontology context when Neo4j reports a schema error.
7. Combine query outputs and let the LLM compare, analyse trends or summarize.

## The graph

The Neo4j graph holds roughly 2.05M nodes and 2.15M relationships (June 2026 audit): states, counties, metro areas, census tracts, QCT/DDA designations, Section 8 and state AMI limits, and HMDA lender-behavior risk. See [docs/graph_schema.md](docs/graph_schema.md) for node types, relationships, traversal patterns and known gaps.

## Repository layout

```
project/
  frontend/     Streamlit UI (app.py)
  backend/      agents, graph (Neo4j + ontology), LLM client, pipeline, cache, MCP
  data/         ontology files
  mcp_server.py MCP server entry point
config/         schema reference and silver-layer config
docs/           graph schema
ingest_scripts/ data ingestion scripts
web/            landing page (Vite + React), deployed on Vercel
```

## Run it locally

Requires Python, a running Neo4j instance loaded with the LIHTC data, and an NVIDIA API key.

```bash
git clone https://github.com/Abhishek2005-Siva/lihtc_new
cd lihtc_new/project
pip install -r requirements.txt

export NEO4J_URI=neo4j://127.0.0.1:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD=<your password>
export NVIDIA_API_KEY=<your key>

streamlit run frontend/app.py
```

Optional: `FAST_MODEL` and `SYNTH_MODEL` choose the planning and synthesis models (defaults: `meta/llama-3.1-8b-instruct` and `meta/llama-3.1-70b-instruct`).

## Landing page

```bash
cd web
npm install
npm run dev
```
