from neo4j import GraphDatabase
d = GraphDatabase.driver("neo4j://127.0.0.1:7687", auth=("neo4j", "Letmein2272"))
with d.session() as s:
    print("Fix 1 - cbsa_code float artifact:")
    n = s.run("MATCH (q:QCTDesignation) WHERE q.cbsa_code ENDS WITH '.0' RETURN count(q) AS n").single()["n"]
    print(f"  QCT nodes with .0 cbsa_code: {n} (should be 0)")

    print("Fix 2 - is_designated boolean:")
    n = s.run("MATCH (q:QCTDesignation) WHERE q.is_designated = true RETURN count(q) AS n").single()["n"]
    print(f"  QCT WHERE is_designated = true:  {n:,} (should be >0)")
    n = s.run("MATCH (q:QCTDesignation) WHERE q.is_designated = 1 RETURN count(q) AS n").single()["n"]
    print(f"  QCT WHERE is_designated = 1:     {n} (should be 0)")

    print("Fix 3 - assessment_year integer:")
    r = s.run("MATCH (l:LenderBehaviorRisk) RETURN l.assessment_year AS y LIMIT 1").single()
    print(f"  LBR assessment_year: {r['y']}  type={type(r['y']).__name__} (should be int)")

    print("Fix 4 - county_name:")
    n = s.run("MATCH (c:County) WHERE c.county_name IS NOT NULL RETURN count(c) AS n").single()["n"]
    print(f"  Counties with county_name: {n} of 3235")
    r = s.run("MATCH (c:County) RETURN c.county_fips, c.county_name LIMIT 3").data()
    for row in r:
        print(f"    {row['c.county_fips']} -> {row['c.county_name']}")

    print("Fix 5 - LBR one row per CBSA:")
    n = s.run("MATCH (l:LenderBehaviorRisk) WHERE l.cbsa_code IS NOT NULL WITH l.cbsa_code AS c, l.assessment_year AS y, count(l) AS cnt WHERE cnt > 1 RETURN count(*) AS dupes").single()["dupes"]
    print(f"  CBSA+year combos with >1 LBR node: {n} (should be 0)")

    print("Fix 7 - hud_fmr_area_code:")
    n = s.run("MATCH (a:Section8AMILimit) WHERE a.hud_fmr_area_code IS NOT NULL RETURN count(a) AS n").single()["n"]
    print(f"  Section8AMILimit with hud_fmr_area_code: {n:,}")
    n = s.run("MATCH (a:Section8AMILimit) WHERE a.area_code IS NOT NULL RETURN count(a) AS n").single()["n"]
    print(f"  Section8AMILimit with old area_code:     {n} (should be 0)")

    print("Fix 8 - cbsa_title:")
    n = s.run("MATCH (m:MetroArea) WHERE m.cbsa_title IS NOT NULL RETURN count(m) AS n").single()["n"]
    print(f"  MetroAreas with cbsa_title: {n} of 443")
    r = s.run("MATCH (m:MetroArea) WHERE m.cbsa_title IS NOT NULL RETURN m.cbsa_code, m.cbsa_title LIMIT 3").data()
    for row in r:
        print(f"    {row['m.cbsa_code']} -> {row['m.cbsa_title']}")

d.close()
