"""Cypher Validator — deterministic, no LLM.

Layer 1 — string rules (instant): write clauses, label/rel validity, bedroom_count
Layer 2 — Neo4j EXPLAIN (10ms): full syntax check with exact error location
"""
from __future__ import annotations

import re
from typing import Any


class CypherValidationError(ValueError):
    pass


class CypherValidator:
    def __init__(self, ontology) -> None:
        self.ontology = ontology

    def validate(self, cypher: str, params: dict[str, Any] | None = None) -> list[str]:
        """Layer 1 string checks. Returns list of error strings."""
        errors: list[str] = []
        self._check_write_clauses(cypher, errors)
        self._check_labels(cypher, errors)
        self._check_relationships(cypher, errors)
        self._check_properties(cypher, errors)
        self._check_structural(cypher, errors)
        if params is not None:
            self._check_fips_formats(params, errors)
            self._check_missing_params(cypher, params, errors)
        return errors

    def explain(self, cypher: str, params: dict[str, Any], neo4j_client) -> str | None:
        """Layer 2 — EXPLAIN against Neo4j. Returns error string or None."""
        try:
            neo4j_client.run_read("EXPLAIN " + cypher.lstrip(), params, timeout=5)
            return None
        except Exception as exc:
            return str(exc)

    def validate_and_explain(
        self, cypher: str, params: dict[str, Any], neo4j_client
    ) -> list[str]:
        """Run both layers. Returns empty list if valid."""
        errors = self.validate(cypher, params)
        if errors:
            return errors
        err = self.explain(cypher, params, neo4j_client)
        if err:
            return [f"EXPLAIN: {err}"]
        return []

    # ── Layer 1 checks ────────────────────────────────────────────────────────

    def _check_write_clauses(self, cypher: str, errors: list[str]) -> None:
        if re.search(r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP)\b", cypher, re.IGNORECASE):
            errors.append("Cypher contains a write clause.")

    def _check_labels(self, cypher: str, errors: list[str]) -> None:
        labels    = set(re.findall(r":([A-Za-z_][A-Za-z0-9_]*)", cypher))
        rel_types = set(re.findall(r"\[:([A-Za-z_][A-Za-z0-9_]*)\]", cypher))
        unknown   = (labels - rel_types) - set(self.ontology.labels)
        if unknown:
            errors.append(f"Unknown label(s): {sorted(unknown)}")

    def _check_relationships(self, cypher: str, errors: list[str]) -> None:
        rels    = set(re.findall(r"\[:([A-Za-z_][A-Za-z0-9_]*)\]", cypher))
        unknown = rels - set(self.ontology.relationship_types)
        if unknown:
            errors.append(f"Unknown relationship type(s): {sorted(unknown)}")

    def _check_properties(self, cypher: str, errors: list[str]) -> None:
        alias_to_label: dict[str, str] = {}
        for alias, label in re.findall(
            r"\(([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([A-Za-z_][A-Za-z0-9_]*)", cypher
        ):
            alias_to_label[alias] = label

        unknown: list[str] = []
        for alias, prop in re.findall(
            r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b", cypher
        ):
            label   = alias_to_label.get(alias)
            allowed = set(self.ontology.properties_by_label.get(label, [])) if label else set()
            if allowed and prop not in allowed:
                unknown.append(f"{label}.{prop}")

        for label, map_body in re.findall(
            r"\([A-Za-z_][A-Za-z0-9_]*\s*:\s*([A-Za-z_][A-Za-z0-9_]*)\s*\{([^}]*)\}", cypher
        ):
            allowed = set(self.ontology.properties_by_label.get(label, []))
            if not allowed:
                continue
            for prop in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*:", map_body):
                if prop not in allowed:
                    unknown.append(f"{label}.{prop} (inline map)")

        if unknown:
            errors.append(f"Unknown property reference(s): {sorted(set(unknown))}")

    def _check_structural(self, cypher: str, errors: list[str]) -> None:
        if "bedroom_count" in cypher:
            errors.append(
                "bedroom_count does not exist. AMI limits are wide-format: "
                "use limit_1person through limit_8person."
            )

        if re.search(r":County\b.*QCTDesignation|:State\b.*QCTDesignation", cypher, re.IGNORECASE):
            errors.append(
                "QCTDesignation links directly to CensusTract via APPLIES_TO. "
                "Never route through County or State."
            )

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

        designation_labels = {"QCTDesignation", "SDDADesignation", "NMDDADesignation"}
        if any(lbl in cypher for lbl in designation_labels):
            has_optional          = "OPTIONAL MATCH" in cypher
            has_designated_filter = bool(re.search(r"is_designated\s*=\s*true", cypher, re.IGNORECASE))
            if not has_optional and not has_designated_filter:
                errors.append(
                    "Designation queries must use OPTIONAL MATCH when returning tracts "
                    "regardless of designation. Use plain MATCH only when filtering "
                    "to designated rows only (with is_designated = true)."
                )

        has_sdda  = "SDDADesignation"  in cypher
        has_nmdda = "NMDDADesignation" in cypher
        if (has_sdda or has_nmdda) and not (has_sdda and has_nmdda) \
                and "QCTDesignation" not in cypher:
            missing = "SDDADesignation" if has_nmdda else "NMDDADesignation"
            errors.append(
                f"DDA queries must check both SDDADesignation and NMDDADesignation. "
                f"Missing: {missing}."
            )

        if re.search(r"\?\s*\w", cypher):
            errors.append("Cypher has no ternary operator. Use CASE WHEN ... THEN ... ELSE ... END.")

        for m in re.finditer(
            r"\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?!\$)([A-Za-z_][A-Za-z0-9_]*)\s*\}", cypher
        ):
            key, val = m.group(1), m.group(2)
            if val not in ("true", "false", "null"):
                errors.append(
                    f"Inline map value '{key}: {val}' looks like an unparameterised identifier. "
                    f"Use ${val} if it is a parameter."
                )

    def _check_fips_formats(self, params: dict[str, Any], errors: list[str]) -> None:
        """Catch obviously wrong FIPS values before they reach Neo4j."""
        fips = params.get("fips_code")
        if fips is not None and (not str(fips).isdigit() or len(str(fips)) != 11):
            errors.append(
                f"fips_code must be an 11-digit numeric string, got: '{fips}'. "
                "Do not pass county FIPS or invented values as fips_code."
            )
        county = params.get("county_fips")
        if county is not None and (not str(county).isdigit() or len(str(county)) != 5):
            errors.append(
                f"county_fips must be a 5-digit numeric string, got: '{county}'."
            )

    def _check_missing_params(
        self, cypher: str, params: dict[str, Any], errors: list[str]
    ) -> None:
        """Catch $param references that have no matching key in exec_params.
        These cause Neo4j ParameterMissing at execution time, not at EXPLAIN time.
        """
        referenced = {m.group(1) for m in re.finditer(r"\$([A-Za-z_][A-Za-z0-9_]*)", cypher)}
        missing = referenced - set(params.keys())
        if missing:
            errors.append(
                f"Cypher references undefined parameter(s): "
                f"{', '.join(f'${p}' for p in sorted(missing))}. "
                f"Remove these conditions or add the parameter values."
            )
