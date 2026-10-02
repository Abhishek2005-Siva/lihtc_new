export default {
  name: 'LIHTC Agent',
  repo: 'https://github.com/Abhishek2005-Siva/lihtc_new',
  eyebrow: 'Neo4j + LLM agent',
  tagline: 'Ask plain-English questions of a housing tax credit knowledge graph.',
  description:
    'An agent that reasons over a Low-Income Housing Tax Credit (LIHTC) graph in Neo4j. It generates Cypher from the live ontology instead of hardcoded queries, validates it, runs it and synthesizes an answer.',
  stack: ['Python', 'Neo4j', 'Streamlit', 'LLM agents', 'MCP'],
  notice:
    'This is a showcase page. The Streamlit app needs a running Neo4j database and an NVIDIA API key, so it runs locally from the repository.',
  steps: [
    { title: 'Understand intent', text: 'Parses the question, filters and the output the user expects.' },
    { title: 'Plan', text: 'Builds the minimum plan, and splits into information goals only when one query is not enough.' },
    { title: 'Generate and validate Cypher', text: 'Writes Cypher from the live ontology and checks labels, relationships and properties first.' },
    { title: 'Execute and synthesize', text: 'Retries on schema errors, then combines results into a reasoned answer.' },
  ],
  stats: {
    title: 'The graph',
    items: [
      { value: '~2.05M', label: 'Nodes' },
      { value: '~2.15M', label: 'Relationships' },
      { value: '56', label: 'States and territories' },
      { value: '111,603', label: 'Census tracts' },
    ],
    note: 'Figures from the repository graph schema document (June 2026 audit).',
  },
  features: [
    { title: 'Ontology-driven', text: 'No business-specific query tools. The agent reads the schema and writes its own Cypher.' },
    { title: 'Validation and retry', text: 'Cypher is checked before execution, and schema errors trigger a retry with ontology context.' },
    { title: 'Step-by-step trace', text: 'The Streamlit UI shows each tool call, attempt and reason.' },
    { title: 'MCP server', text: 'Also exposes the capabilities through an MCP server entry point.' },
  ],
  startNote: 'Needs Python, a running Neo4j instance loaded with the LIHTC data, and an NVIDIA API key (set NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NVIDIA_API_KEY). See the project README.',
  quickstart: `git clone https://github.com/Abhishek2005-Siva/lihtc_new
cd lihtc_new/project
pip install -r requirements.txt

streamlit run frontend/app.py`,
}
