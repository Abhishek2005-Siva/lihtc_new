from backend.pipeline.cypher_validator import CypherValidationError, CypherValidator
from backend.graph.ontology import GraphOntology


def test_validator_rejects_unknown_label():
    ontology = GraphOntology(labels=["CensusTract"], relationship_types=[], properties_by_label={})
    validator = CypherValidator(ontology)

    try:
        validator.validate("MATCH (n:MissingLabel) RETURN n")
    except CypherValidationError:
        return

    raise AssertionError("Expected CypherValidationError")

