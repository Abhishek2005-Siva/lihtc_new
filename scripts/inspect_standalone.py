from neo4j import GraphDatabase
d = GraphDatabase.driver("neo4j://127.0.0.1:7687", auth=("neo4j", "Letmein2272"))
with d.session() as s:
    print("=== 138 orphan CensusTract (sample) ===")
    rows = s.run("MATCH (n:CensusTract) WHERE NOT (n)--() RETURN n.tract_geoid AS geoid LIMIT 10").data()
    for r in rows: print(f"  {r['geoid']}")

    print("\n=== Section8AMILimit standalone sample ===")
    rows = s.run("MATCH (n:Section8AMILimit) WHERE NOT (n)--() RETURN n.area_code AS ac, n.area_name AS name, n.year AS yr LIMIT 10").data()
    for r in rows: print(f"  {r['ac']} | {r['name']} | {r['yr']}")

    print("\n=== Section8AMILimit area_code patterns (distinct prefixes) ===")
    rows = s.run("""MATCH (n:Section8AMILimit) WHERE NOT (n)--()
        RETURN substring(n.area_code, 0, 5) AS prefix, count(n) AS c
        ORDER BY c DESC LIMIT 10""").data()
    for r in rows: print(f"  prefix={r['prefix']} count={r['c']:,}")

    print("\n=== LenderBehaviorRisk standalone sample ===")
    rows = s.run("MATCH (n:LenderBehaviorRisk) WHERE NOT (n)--() RETURN n.risk_id, n.msa_code, n.assessment_year LIMIT 10").data()
    for r in rows: print(f"  {r['n.risk_id']} | msa_code={r['n.msa_code']} | year={r['n.assessment_year']}")
d.close()
