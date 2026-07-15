from neo4j import GraphDatabase
d = GraphDatabase.driver("neo4j://127.0.0.1:7687", auth=("neo4j", "Letmein2272"))
with d.session() as s:
    print("=== CensusTract orphan properties ===")
    rows = s.run("MATCH (n:CensusTract) WHERE NOT (n)--() RETURN properties(n) AS p LIMIT 5").data()
    for r in rows: print(f"  {r['p']}")

    print("\n=== NCNTY area_code parsing (county_fips extraction) ===")
    # NCNTY01003N01003 -> county_fips = positions 5-9 = "01003"
    rows = s.run("""MATCH (n:Section8AMILimit) WHERE NOT (n)--() AND n.area_code STARTS WITH 'NCNTY'
        WITH n, substring(n.area_code, 5, 5) AS county_fips
        MATCH (c:County {county_fips: county_fips})
        RETURN count(n) AS matchable""").data()
    print(f"  NCNTY matchable to County: {rows[0]['matchable']:,}")

    print("\n=== LenderBehaviorRisk non-NA standalone county match ===")
    rows = s.run("""MATCH (n:LenderBehaviorRisk) WHERE NOT (n)--() AND n.msa_code <> 'NA' AND n.msa_code IS NOT NULL
        WITH n, n.msa_code AS fips
        MATCH (c:County {county_fips: fips})
        RETURN count(n) AS matchable""").data()
    print(f"  LenderBehaviorRisk matchable to County: {rows[0]['matchable']:,}")
d.close()
