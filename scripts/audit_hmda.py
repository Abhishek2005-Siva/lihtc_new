from neo4j import GraphDatabase
d = GraphDatabase.driver("neo4j://127.0.0.1:7687", auth=("neo4j","Letmein2272"))
with d.session() as s:
    total = s.run("MATCH (n:LenderBehaviorRisk) RETURN count(n) AS c").single()["c"]
    out = s.run("MATCH (n:LenderBehaviorRisk)-[r]->() RETURN count(r) AS c").single()["c"]
    by_year = s.run("MATCH (n:LenderBehaviorRisk) RETURN n.assessment_year AS yr, count(n) AS c ORDER BY yr").data()
    print(f"Total LenderBehaviorRisk: {total:,}")
    print(f"Total APPLIES_TO edges:   {out:,}")
    print("By year:")
    for row in by_year:
        print(f"  {row['yr']}: {row['c']:,}")
d.close()
