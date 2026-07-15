"""Loads and caches the Neo4j ontology from the static lihtc_ontology.json file."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Canonical path to the static ontology file.
_ONTOLOGY_FILE = Path(__file__).resolve().parents[2] / "data" / "ontology" / "lihtc_ontology.json"


@dataclass
class GraphOntology:
    labels: list[str] = field(default_factory=list)
    relationship_types: list[str] = field(default_factory=list)
    properties_by_label: dict[str, list[str]] = field(default_factory=dict)
    relationship_patterns: list[dict[str, Any]] = field(default_factory=list)
    primary_keys: dict[str, str] = field(default_factory=dict)
    data_year_ranges: dict[str, str] = field(default_factory=dict)
    node_counts: dict[str, int] = field(default_factory=dict)
    property_schemas: dict[str, dict[str, Any]] = field(default_factory=dict)
    normalisation_rules: dict[str, Any] = field(default_factory=dict)
    quirks: list[str] = field(default_factory=list)
    # Legacy fields kept for backwards-compat
    indexes: list[dict[str, str]] = field(default_factory=list)
    concepts: dict[str, Any] = field(default_factory=dict)

    def to_prompt_context(self) -> str:
        """Return a structured prompt string the LLM can use to write Cypher.

        Sections (in order of usefulness for generation):
          1. Node labels + primary keys + counts
          2. Relationship patterns
          3. Properties per label (names only — types in section 4)
          4. Property type constraints (neo4j_type, values, nullable, quirks)
          5. Data year ranges
          6. Normalisation rules (padding, sentinels, type exceptions)
          7. Quirks (plain-English rules for the generator)
        """
        lines: list[str] = []

        # ── 1. Labels ─────────────────────────────────────────────────────────
        lines.append("=== NODE LABELS ===")
        for label in self.labels:
            pk = self.primary_keys.get(label, "?")
            count = self.node_counts.get(label, "?")
            lines.append(f"  {label}  pk={pk}  ~{count} nodes")

        # ── 2. Relationship patterns ──────────────────────────────────────────
        lines.append("\n=== RELATIONSHIP PATTERNS ===")
        for rp in self.relationship_patterns:
            via = f"  via={rp.get('via_field') or rp.get('via_crosswalk', '')}"
            lines.append(f"  ({rp['from']})-[:{rp['type']}]->({rp['to']}){via}")

        # ── 3. Properties per label ───────────────────────────────────────────
        lines.append("\n=== PROPERTIES BY LABEL ===")
        for label, props in self.properties_by_label.items():
            lines.append(f"  {label}: {', '.join(props)}")

        # ── 4. Property type constraints ──────────────────────────────────────
        lines.append("\n=== PROPERTY TYPE CONSTRAINTS ===")
        for label, schema in self.property_schemas.items():
            lines.append(f"  [{label}]")
            for prop, meta in schema.items():
                parts = [f"neo4j_type={meta.get('neo4j_type', '?')}"]
                if meta.get("values"):
                    parts.append(f"values={meta['values']}")
                if meta.get("nullable"):
                    parts.append("nullable")
                if meta.get("range"):
                    parts.append(f"range={meta['range']}")
                if meta.get("comparison"):
                    parts.append(f"COMPARISON: {meta['comparison']}")
                if meta.get("quirk"):
                    parts.append(f"QUIRK: {meta['quirk']}")
                lines.append(f"    {prop}: {' | '.join(parts)}")

        # ── 5. Data year ranges ───────────────────────────────────────────────
        lines.append("\n=== DATA YEAR RANGES ===")
        for field_path, rng in self.data_year_ranges.items():
            lines.append(f"  {field_path}: {rng}")

        # ── 6. Normalisation rules ────────────────────────────────────────────
        if self.normalisation_rules:
            lines.append("\n=== NORMALISATION RULES ===")
            pad = self.normalisation_rules.get("zero_padding", {})
            if pad:
                lines.append("  Zero-padding:")
                for f, rule in pad.items():
                    lines.append(f"    {f}: {rule}")
            neo4j_map = self.normalisation_rules.get("neo4j_type_map", {})
            exc = neo4j_map.get("STRING_as_INTEGER_exception", {})
            if exc:
                lines.append("  STRING stored as year (exception):")
                for f, note in exc.items():
                    lines.append(f"    {f}: {note}")
            exc2 = neo4j_map.get("INTEGER_not_BOOLEAN_exception", {})
            if exc2:
                lines.append("  INTEGER stored instead of BOOLEAN (exception):")
                for f, note in exc2.items():
                    lines.append(f"    {f}: {note}")

        # ── 7. Quirks ─────────────────────────────────────────────────────────
        lines.append("\n=== CYPHER GENERATION RULES (QUIRKS) ===")
        for i, quirk in enumerate(self.quirks, 1):
            lines.append(f"  {i}. {quirk}")

        return "\n".join(lines)


class OntologyLoader:
    """Loads GraphOntology from the static lihtc_ontology.json file.

    Falls back to live Neo4j introspection if the static file is not found,
    but the static file is authoritative and preferred — it contains type
    constraints, year ranges, and quirks that Neo4j introspection cannot
    provide.
    """

    def __init__(
        self,
        neo4j_client=None,
        ontology_path: str | Path | None = None,
        cache_path: str | Path = "backend/graph/schema_cache.json",
    ) -> None:
        self.neo4j_client = neo4j_client
        self.ontology_path = Path(ontology_path) if ontology_path else _ONTOLOGY_FILE
        self.cache_path = Path(cache_path)

    def load(self, refresh: bool = False) -> GraphOntology:
        """Load ontology. Static file takes precedence over Neo4j introspection."""
        if self.ontology_path.exists():
            return self._load_from_file(self.ontology_path)

        # Fallback: cache or live Neo4j (static file missing)
        if self.cache_path.exists() and not refresh:
            return self._load_from_file(self.cache_path)

        if self.neo4j_client is None:
            raise FileNotFoundError(
                f"Ontology file not found at {self.ontology_path} and no neo4j_client provided."
            )
        ontology = self._load_from_neo4j()
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(asdict(ontology), indent=2), encoding="utf-8")
        return ontology

    def _load_from_file(self, path: Path) -> GraphOntology:
        data = json.loads(path.read_text(encoding="utf-8"))
        # Strip keys not in the dataclass
        known = {f.name for f in GraphOntology.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return GraphOntology(**filtered)

    def _load_from_neo4j(self) -> GraphOntology:
        labels = [
            row["label"]
            for row in self.neo4j_client.run_read(
                "CALL db.labels() YIELD label RETURN label ORDER BY label"
            )
        ]
        rels = [
            row["relationshipType"]
            for row in self.neo4j_client.run_read(
                "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType ORDER BY relationshipType"
            )
        ]
        props = self._load_properties_by_label(labels)
        return GraphOntology(labels=labels, relationship_types=rels, properties_by_label=props)

    def _load_properties_by_label(self, labels: list[str]) -> dict[str, list[str]]:
        properties: dict[str, list[str]] = {}
        for label in labels:
            query = f"MATCH (n:`{label}`) UNWIND keys(n) AS key RETURN DISTINCT key ORDER BY key"
            properties[label] = [row["key"] for row in self.neo4j_client.run_read(query)]
        return properties
