import sys; sys.path.insert(0, "project")
from backend.llm.parser import parse_json_object

bad = (
    '{"cypher": "MATCH (ct:CensusTract {county_fips: $county_fips})\\n"\n'
    '             "OPTIONAL MATCH (q:QCTDesignation)-[:APPLIES_TO]->(ct)\\n"\n'
    '             "RETURN ct.fips_code AS fips_code",\n'
    ' "params": {"county_fips": "48113"}, "explanation": "ok"}'
)
r = parse_json_object(bad)
print("cypher:", repr(r["cypher"]))
print("PASS" if "MATCH" in r["cypher"] else "FAIL")
