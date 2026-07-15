from neo4j import GraphDatabase
d = GraphDatabase.driver("neo4j://127.0.0.1:7687", auth=("neo4j", "Letmein2272"))

checks = [
    # (description, cypher)
    # Geographic hierarchy
    ("IN_STATE  : County -> State",         "MATCH (a:County)-[:IN_STATE]->(b:State) RETURN count(*) AS c"),
    ("IN_STATE  : MetroArea -> State",       "MATCH (a:MetroArea)-[:IN_STATE]->(b:State) RETURN count(*) AS c"),
    ("IN_COUNTY : CensusTract -> County",   "MATCH (a:CensusTract)-[:IN_COUNTY]->(b:County) RETURN count(*) AS c"),
    ("IN_METRO  : CensusTract -> MetroArea","MATCH (a:CensusTract)-[:IN_METRO]->(b:MetroArea) RETURN count(*) AS c"),
    ("IN_METRO  : County -> MetroArea",     "MATCH (a:County)-[:IN_METRO]->(b:MetroArea) RETURN count(*) AS c"),
    # Designation edges
    ("APPLIES_TO: QCTDesignation -> CensusTract",     "MATCH (a:QCTDesignation)-[:APPLIES_TO]->(b:CensusTract) RETURN count(*) AS c"),
    ("APPLIES_TO: SDDADesignation -> MetroArea",       "MATCH (a:SDDADesignation)-[:APPLIES_TO]->(b:MetroArea) RETURN count(*) AS c"),
    ("APPLIES_TO: NMDDADesignation -> County",         "MATCH (a:NMDDADesignation)-[:APPLIES_TO]->(b:County) RETURN count(*) AS c"),
    ("APPLIES_TO: StateAMILimit -> State",             "MATCH (a:StateAMILimit)-[:APPLIES_TO]->(b:State) RETURN count(*) AS c"),
    ("APPLIES_TO: LenderBehaviorRisk -> MetroArea",    "MATCH (a:LenderBehaviorRisk)-[:APPLIES_TO]->(b:MetroArea) RETURN count(*) AS c"),
    ("APPLIES_TO: LenderBehaviorRisk -> County",       "MATCH (a:LenderBehaviorRisk)-[:APPLIES_TO]->(b:County) RETURN count(*) AS c"),
    # AMI edges
    ("HAS_MSA_AMI  : County -> Section8AMILimit",  "MATCH (a:County)-[:HAS_MSA_AMI]->(b:Section8AMILimit) RETURN count(*) AS c"),
    ("HAS_STATE_AMI: County -> StateAMILimit",      "MATCH (a:County)-[:HAS_STATE_AMI]->(b:StateAMILimit) RETURN count(*) AS c"),
]

print(f"{'Relationship Check':<48} {'Count':>10}  Status")
print("-" * 70)
with d.session() as s:
    for desc, q in checks:
        c = s.run(q).single()["c"]
        status = "OK" if c > 0 else "MISSING"
        flag = " <-- MISSING" if c == 0 else ""
        print(f"  {desc:<46} {c:>10,}  {status}{flag}")

d.close()
