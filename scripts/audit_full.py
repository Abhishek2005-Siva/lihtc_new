from neo4j import GraphDatabase
d = GraphDatabase.driver("neo4j://127.0.0.1:7687", auth=("neo4j", "Letmein2272"))
with d.session() as s:
    print("=" * 60)
    print("NODE AUDIT")
    print("=" * 60)
    for label in ["State","County","CensusTract","MetroArea",
                  "QCTDesignation","SDDADesignation","NMDDADesignation",
                  "Section8AMILimit","StateAMILimit","LenderBehaviorRisk","SpecialProgramLimit"]:
        total = s.run(f"MATCH (n:{label}) RETURN count(n) AS c").single()["c"]
        out = s.run(f"MATCH (n:{label})-[r]->() RETURN count(r) AS c").single()["c"]
        inc = s.run(f"MATCH ()-[r]->(n:{label}) RETURN count(r) AS c").single()["c"]
        pct = f"{out/total*100:.0f}%" if total > 0 else "n/a"
        print(f"  {label:<25} {total:>10,}  out={out:>10,}  in={inc:>10,}  out%={pct}")

    print()
    print("=" * 60)
    print("RELATIONSHIP AUDIT")
    print("=" * 60)
    for rel in ["APPLIES_TO","HAS_MSA_AMI","HAS_STATE_AMI","IN_STATE","IN_METRO","IN_COUNTY"]:
        c = s.run(f"MATCH ()-[r:{rel}]->() RETURN count(r) AS c").single()["c"]
        print(f"  :{rel:<24} {c:>12,}")

    print()
    print("=" * 60)
    print("HMDA COVERAGE BY YEAR")
    print("=" * 60)
    rows = s.run("MATCH (n:LenderBehaviorRisk) RETURN n.assessment_year AS yr, count(n) AS c ORDER BY yr").data()
    for r in rows:
        print(f"  {r['yr']}: {r['c']:,} nodes")

    print()
    print("=" * 60)
    print("KNOWN GAPS (documented, not fixable)")
    print("=" * 60)
    sdda_miss = s.run("MATCH (s:SDDADesignation) WHERE NOT (s)-[:APPLIES_TO]->() RETURN count(s) AS c").single()["c"]
    nmdd_miss = s.run("MATCH (n:NMDDADesignation) WHERE NOT (n)-[:APPLIES_TO]->() RETURN count(n) AS c").single()["c"]
    print(f"  SDDA unconnected:  {sdda_miss} (PR territories + county-level HUD FMR areas)")
    print(f"  NMDDA unconnected: {nmdd_miss} (HUD source gap 2017-2020, county_fips not published)")
    print(f"  HMDA pre-2018:     not ingested (pre-reform schema, different pipeline needed)")
d.close()
