from neo4j import GraphDatabase
d = GraphDatabase.driver("neo4j://127.0.0.1:7687", auth=("neo4j", "Letmein2272"))
with d.session() as s:
    labels = ["State","County","CensusTract","MetroArea",
              "QCTDesignation","SDDADesignation","NMDDADesignation",
              "Section8AMILimit","StateAMILimit","LenderBehaviorRisk","SpecialProgramLimit"]
    print(f"{'Label':<25} {'Total':>10} {'Standalone':>12} {'%':>6}")
    print("-" * 58)
    grand_total, grand_standalone = 0, 0
    for label in labels:
        total = s.run(f"MATCH (n:{label}) RETURN count(n) AS c").single()["c"]
        standalone = s.run(
            f"MATCH (n:{label}) WHERE NOT (n)--() RETURN count(n) AS c"
        ).single()["c"]
        pct = f"{standalone/total*100:.1f}%" if total > 0 else "n/a"
        grand_total += total
        grand_standalone += standalone
        flag = " <-- ISSUE" if standalone > 0 else ""
        print(f"  {label:<25} {total:>10,} {standalone:>12,} {pct:>6}{flag}")
    print("-" * 58)
    print(f"  {'TOTAL':<25} {grand_total:>10,} {grand_standalone:>12,}")
d.close()
