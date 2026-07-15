# LIHTC Neo4j Ontology

This ontology is for dynamic Cypher generation. The agent should use only the labels, relationships, and properties listed here.

## Anchor Nodes

- `State(state_fips, state_abbr, state_name)`
- `County(county_fips, state_fips, county_name)`
- `CensusTract(fips_code, state_fips, county_fips, cbsa_code, fmr_area_code, is_metro)`
- `MetroArea(cbsa_code, fmr_area_code, state_fips, area_population, cbsa_title)`

## Data Nodes

- `QCTDesignation`: tract-year QCT status.
- `SDDADesignation`: metro-year small DDA status.
- `NMDDADesignation`: county-year non-metro DDA status.
- `Section8AMILimit`: HUD area income limits by year and program type.
- `StateAMILimit`: state-level fallback income limits.
- `SpecialProgramLimit`: Section 221/235/236 limits, intentionally disconnected.
- `LenderBehaviorRisk`: HMDA-derived county/metro fair lending risk by year.

## Relationship Patterns

- `(c:County)-[:IN_STATE]->(s:State)`
- `(m:MetroArea)-[:IN_STATE]->(s:State)`
- `(t:CensusTract)-[:IN_STATE]->(s:State)`
- `(t:CensusTract)-[:IN_COUNTY]->(c:County)`
- `(t:CensusTract)-[:IN_METRO]->(m:MetroArea)`
- `(c:County)-[:IN_METRO]->(m:MetroArea)`
- `(q:QCTDesignation)-[:APPLIES_TO]->(t:CensusTract)`
- `(sd:SDDADesignation)-[:APPLIES_TO]->(m:MetroArea)`
- `(nd:NMDDADesignation)-[:APPLIES_TO]->(c:County)`
- `(a:StateAMILimit)-[:APPLIES_TO]->(s:State)`
- `(r:LenderBehaviorRisk)-[:APPLIES_TO]->(m:MetroArea)`
- `(r:LenderBehaviorRisk)-[:APPLIES_TO]->(c:County)`
- `(c:County)-[:HAS_MSA_AMI]->(a:Section8AMILimit)`
- `(c:County)-[:HAS_STATE_AMI]->(a:StateAMILimit)`

## Important Query Rules

- Use `CensusTract.fips_code` for 11-digit tract lookup.
- Use `QCTDesignation.designation_year`, `SDDADesignation.designation_year`, and `NMDDADesignation.designation_year` for designation-year filters.
- Use `Section8AMILimit.year` and `StateAMILimit.year` for AMI-year filters.
- `LenderBehaviorRisk.assessment_year` is an INTEGER (e.g. `2025`), not a string.
- `County.county_name` exists (e.g. `'Cook County'`) — use it to resolve a NAMED county instead of guessing county_fips from memory; only use a bare county_fips param when the question states the digit code directly.
- Do not use `MetroArea.metro_name`; use `cbsa_title`.
- For AMI, try county `HAS_MSA_AMI` first and `HAS_STATE_AMI` as fallback.
- For HMDA, metro tracts can connect through `IN_METRO`; non-metro risk can connect through `IN_COUNTY`.

