"""Cypher Validator — deterministic code, no LLM.

Checks:
  Layer 1 — Structural / safety (write clauses, unknown labels/rels/props)
  Layer 2 — Quirk rules (type rules, graph topology rules, Cypher structure rules)
  Layer 4 — Dry-run against Neo4j (syntax + unknown properties at runtime)
"""
from __future__ import annotations

import re
from typing import Any


class CypherValidationError(ValueError):
    pass


class CypherValidator:
    def __init__(self, ontology) -> None:
        self.ontology = ontology

    # ── Public API ────────────────────────────────────────────────────────────

    def validate(self, cypher: str) -> None:
        """Run Layer 1 + Layer 2. Raises CypherValidationError on failure."""
        self._layer1_structural(cypher)
        self._layer2_quirks(cypher)

    def dry_run(self, cypher: str, params: dict[str, Any], neo4j_client) -> str | None:
        """Layer 4 — LIMIT 0 parse check. Returns error string or None."""
        limited = _append_limit0(cypher)
        try:
            neo4j_client.run_read(limited, params, timeout=5)
            return None
        except Exception as exc:
            return str(exc)

    def validate_and_dry_run(
        self,
        cypher: str,
        params: dict[str, Any],
        neo4j_client,
    ) -> list[str]:
        """Run all layers. Returns list of error strings (empty = valid)."""
        errors: list[str] = []
        try:
            self.validate(cypher)
        except CypherValidationError as exc:
            errors.append(str(exc))
            return errors   # don't dry-run if structurally invalid

        dry_err = self.dry_run(cypher, params, neo4j_client)
        if dry_err:
            errors.append(f"Dry-run: {dry_err}")
        return errors

    # ── Layer 1 ───────────────────────────────────────────────────────────────

    def _layer1_structural(self, cypher: str) -> None:
        self._reject_write_clauses(cypher)
        self._validate_labels(cypher)
        self._validate_relationships(cypher)
        self._validate_properties(cypher)

    def _reject_write_clauses(self, cypher: str) -> None:
        if re.search(r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP)\b", cypher, re.IGNORECASE):
            raise CypherValidationError("Cypher contains a write clause.")

    def _validate_labels(self, cypher: str) -> None:
        labels = set(re.findall(r":([A-Za-z_][A-Za-z0-9_]*)", cypher))
        rel_types = set(re.findall(r"\[:([A-Za-z_][A-Za-z0-9_]*)\]", cypher))
        node_labels = labels - rel_types
        missing = node_labels - set(self.ontology.labels)
        if missing:
            raise CypherValidationError(f"Unknown label(s): {sorted(missing)}")

    def _validate_relationships(self, cypher: str) -> None:
        rels = set(re.findall(r"\[:([A-Za-z_][A-Za-z0-9_]*)\]", cypher))
        missing = rels - set(self.ontology.relationship_types)
        if missing:
            raise CypherValidationError(f"Unknown relationship type(s): {sorted(missing)}")

    def _validate_properties(self, cypher: str) -> None:
        alias_to_label: dict[str, str] = {}
        for alias, label in re.findall(
            r"\(([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([A-Za-z_][A-Za-z0-9_]*)",
            cypher,
        ):
            alias_to_label[alias] = label

        unknown: list[str] = []

        for alias, prop in re.findall(
            r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b", cypher
        ):
            label = alias_to_label.get(alias)
            if not label:
                continue
            allowed = set(self.ontology.properties_by_label.get(label, []))
            if allowed and prop not in allowed:
                unknown.append(f"{label}.{prop}")

        # Inline map properties: (alias:Label {prop: value})
        for label, map_body in re.findall(
            r"\([A-Za-z_][A-Za-z0-9_]*\s*:\s*([A-Za-z_][A-Za-z0-9_]*)\s*\{([^}]*)\}",
            cypher,
        ):
            allowed = set(self.ontology.properties_by_label.get(label, []))
            if not allowed:
                continue
            for prop in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*:", map_body):
                if prop not in allowed:
                    unknown.append(f"{label}.{prop} (inline map)")

        if unknown:
            raise CypherValidationError(f"Unknown property reference(s): {sorted(set(unknown))}")

    # ── Layer 2 ───────────────────────────────────────────────────────────────

    def _layer2_quirks(self, cypher: str) -> None:
        errors: list[str] = []
        self._type_quirks(cypher, errors)
        self._graph_quirks(cypher, errors)
        self._cypher_quirks(cypher, errors)
        if errors:
            raise CypherValidationError(
                "Quirk rule violation(s):\n" + "\n".join(f"  - {e}" for e in errors)
            )

    def _type_quirks(self, cypher: str, errors: list[str]) -> None:
        boolean_fields = {
            "is_designated", "is_territory", "is_high_disparity",
            "is_multifamily_constrained", "income_estimate_reliable",
            "split_tr_flag", "is_metro_tract",
        }
        for f in boolean_fields:
            if f in cypher:
                if re.search(rf"{f}\s*=\s*[01]", cypher) or \
                   re.search(rf"COALESCE\([^)]*{f}", cypher):
                    errors.append(
                        f"{f} is BOOLEAN — use = true/false, never = 0/1 or COALESCE."
                    )

        for pattern in ["designation_year", r"(?<!\w)year(?!\w)", "basis_boost_pct", "ami_pct"]:
            if re.search(rf'{pattern}\s*[=:]\s*["\']', cypher):
                errors.append(
                    f"{pattern.strip(r'(?<!w)(?!w)')} is INTEGER — remove quotes."
                )

        if "assessment_year" in cypher:
            if not re.search(r'assessment_year\s*=\s*["\']|toString\s*\(', cypher):
                errors.append(
                    "assessment_year is STRING — use toString($year) or '2025'."
                )

    def _graph_quirks(self, cypher: str, errors: list[str]) -> None:
        if "QCTDesignation" in cypher:
            if re.search(r"County.*QCTDesignation|State.*QCTDesignation", cypher, re.IGNORECASE):
                errors.append(
                    "QCTDesignation → CensusTract via APPLIES_TO directly. "
                    "Never route through County or State."
                )
            if re.search(
                r"QCTDesignation[^)]*\{[^}]*(tract_fips|fips_code|county_fips)[^}]*\}",
                cypher, re.IGNORECASE,
            ):
                errors.append(
                    "QCTDesignation has no fips_code/tract_fips property. "
                    "Use -[:APPLIES_TO]->(ct:CensusTract {fips_code: $fips_code})."
                )

        if "SDDADesignation" in cypher:
            if re.search(
                r"SDDADesignation[^)]*\{[^}]*(county_fips|fips_code|tract_fips)[^}]*\}",
                cypher, re.IGNORECASE,
            ):
                errors.append(
                    "SDDADesignation has no county_fips/fips_code. "
                    "It connects to MetroArea via APPLIES_TO."
                )

        if "NMDDADesignation" in cypher:
            if re.search(
                r"NMDDADesignation[^)]*\{[^}]*fips_code[^}]*\}", cypher, re.IGNORECASE,
            ):
                errors.append(
                    "NMDDADesignation has no fips_code. "
                    "Traverse via APPLIES_TO to County."
                )

        if "Section8AMILimit" in cypher:
            if re.search(r"APPLIES_TO.*Section8AMILimit|Section8AMILimit.*APPLIES_TO", cypher):
                errors.append(
                    "Section8AMILimit has no APPLIES_TO edge. "
                    "Reach it via County-[:HAS_MSA_AMI]->Section8AMILimit."
                )

        if re.search(r"\bct\.cbsa_code\b", cypher):
            errors.append(
                "CensusTract.cbsa_code has a float artifact. "
                "Use (ct)-[:IN_METRO]->(ma:MetroArea) instead."
            )

    def _cypher_quirks(self, cypher: str, errors: list[str]) -> None:
        designation_labels = {"QCTDesignation", "SDDADesignation", "NMDDADesignation"}
        if any(lbl in cypher for lbl in designation_labels):
            if "OPTIONAL MATCH" not in cypher:
                errors.append(
                    "Designation queries must use OPTIONAL MATCH — plain MATCH returns "
                    "nothing when the designation is absent."
                )

        has_sdda  = "SDDADesignation"  in cypher
        has_nmdda = "NMDDADesignation" in cypher
        if (has_sdda or has_nmdda) and not (has_sdda and has_nmdda) \
                and "QCTDesignation" not in cypher:
            missing = "SDDADesignation" if has_nmdda else "NMDDADesignation"
            errors.append(
                f"DDA queries must check both SDDADesignation and NMDDADesignation. Missing: {missing}."
            )

        if re.search(r"(Section8AMILimit|StateAMILimit)", cypher) and "bedroom_count" in cypher:
            errors.append(
                "AMI limit tables are wide-format — bedroom_count does not exist. "
                "Use limit_1person through limit_8person."
            )

        if re.search(r"poverty_rate|income_criterion_ratio", cypher) and \
                re.search(r"is_designated", cypher, re.IGNORECASE) is None and \
                re.search(r"QCTDesignation|SDDADesignation|NMDDADesignation", cypher):
            errors.append(
                "Never re-derive is_designated from poverty_rate or income_criterion_ratio. "
                "Filter with is_designated = true."
            )


def _append_limit0(cypher: str) -> str:
    stripped = cypher.rstrip().rstrip(";").rstrip()
    if re.search(r"\bLIMIT\s+\d+", stripped, re.IGNORECASE):
        return stripped
    return stripped + "\nLIMIT 0"
