from neo4j import GraphDatabase
d = GraphDatabase.driver("neo4j://127.0.0.1:7687", auth=("neo4j","Letmein2272"))
with d.session() as s:
    # Check if Dayton (19380), Prescott (39140), Poughkeepsie (35620) exist as MetroArea
    print("=== MSA-level SDDA: MetroArea lookup ===")
    for cbsa in ["19380","39140","35620"]:
        r = s.run("MATCH (m:MetroArea {cbsa_code: $c}) RETURN m.cbsa_code, m.metro_name", c=cbsa).data()
        print(f"  cbsa={cbsa}: {r}")

    print()
    print("=== SDDA missing breakdown ===")
    total_missing = s.run("MATCH (s:SDDADesignation) WHERE NOT (s)-[:APPLIES_TO]->() RETURN count(s) AS c").single()["c"]
    print(f"  Total missing: {total_missing}")

    rows = s.run("MATCH (s:SDDADesignation) WHERE NOT (s)-[:APPLIES_TO]->() RETURN s.fmr_area_code AS fmr, s.area_name AS name ORDER BY s.fmr_area_code").data()
    pr_rows = [r for r in rows if "PR" in (r["name"] or "") or "Municipio" in (r["name"] or "")]
    county_rows = [r for r in rows if "HUD Metro FMR Area" in (r["name"] or "")]
    msa_rows = [r for r in rows if "MSA" in (r["name"] or "")]
    print(f"  Puerto Rico territories: {len(pr_rows)}")
    print(f"  County-level HUD FMR areas: {len(county_rows)}")
    print(f"  MSA-level (should map): {len(msa_rows)}")
    for r in msa_rows:
        print(f"    {r['fmr']} | {r['name']}")

    print()
    print("=== StateAMILimit out-edge type ===")
    rows2 = s.run("MATCH (s:StateAMILimit)-[r]->(t) RETURN type(r) AS rel, labels(t) AS target, count(r) AS c").data()
    for r in rows2:
        print(f"  {r['rel']} -> {r['target']}: {r['c']:,}")
d.close()
