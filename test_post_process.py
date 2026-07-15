import sys
sys.path.insert(0, "project")
from backend.agents.cypher_builder import _post_process

broken = (
    "MATCH (ct:CensusTract {county_fips: $county_fips})\n"
    "OPTIONAL MATCH (q:QCTDesignation)-[:APPLIES_TO]->(ct)\n"
    "WHERE q.is_designated = true AND q.designation_year = $year\n"
    "OPTIONAL MATCH (s:SDDADesignation)-[:APPLIES_TO]->(ct)\n"
    "WITH ct, q, s WHERE ($is_qct_designated IS NULL OR (q IS NOT NULL) = $is_qct_designated) "
    "AND ($is_dda_designated IS NULL OR (s IS NOT NULL) = $is_dda_designated)\n"
    "RETURN ct.fips_code AS fips_code, ct.poverty_rate AS poverty_rate"
)

params = {"county_fips": "48113", "year": 2025, "min_poverty_rate": 0.25}

result = _post_process(broken, params)
print("RESULT:")
print(result)
print()
print("Invented params removed?", "$is_qct_designated" not in result and "$is_dda_designated" not in result)
