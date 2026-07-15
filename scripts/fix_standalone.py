"""Fix all standalone nodes with resolvable relationships."""
from neo4j import GraphDatabase
import time

NEO4J_IMPORT = (
    r"C:\Users\Abhishek\.Neo4jDesktop2\Data\dbmss"
    r"\dbms-739a327b-c575-4606-92db-b2355c89faf3\import"
)
d = GraphDatabase.driver("neo4j://127.0.0.1:7687", auth=("neo4j", "Letmein2272"))


def run(s, q, **params):
    return s.run(q, **params)


# ══════════════════════════════════════════════════════════════════
# FIX 1: CensusTract orphans — add IN_COUNTY + IN_STATE
# ══════════════════════════════════════════════════════════════════
print("=== FIX 1: CensusTract orphans ===")
t0 = time.time()
with d.session() as s:
    # IN_COUNTY (only where County node exists)
    r = s.run("""
        MATCH (n:CensusTract) WHERE NOT (n)--()
        MATCH (c:County {county_fips: n.county_fips})
        MERGE (n)-[:IN_COUNTY]->(c)
        RETURN count(*) AS c
    """).single()
    print(f"  IN_COUNTY edges created: {r['c']:,}")

    # IN_STATE
    r = s.run("""
        MATCH (n:CensusTract) WHERE NOT (n)-[:IN_STATE]-()
        MATCH (st:State {state_fips: n.state_fips})
        MERGE (n)-[:IN_STATE]->(st)
        RETURN count(*) AS c
    """).single()
    print(f"  IN_STATE edges created:  {r['c']:,}")

    remaining = s.run("MATCH (n:CensusTract) WHERE NOT (n)--() RETURN count(n) AS c").single()["c"]
    print(f"  Remaining orphans: {remaining} ({time.time()-t0:.1f}s)")


# ══════════════════════════════════════════════════════════════════
# FIX 2: Section8AMILimit NCNTY → County (HAS_MSA_AMI)
# ══════════════════════════════════════════════════════════════════
print("\n=== FIX 2: Section8AMILimit NCNTY -> County ===")
t0 = time.time()
with d.session() as s:
    # NCNTY{state2}{county3}N{state2}{county3} -> county_fips = positions 5-9
    r = s.run("""
        MATCH (n:Section8AMILimit) WHERE NOT (n)--() AND n.area_code STARTS WITH 'NCNTY'
        WITH n, substring(n.area_code, 5, 5) AS county_fips
        MATCH (c:County {county_fips: county_fips})
        MERGE (c)-[:HAS_MSA_AMI]->(n)
        RETURN count(*) AS c
    """).single()
    print(f"  HAS_MSA_AMI edges created: {r['c']:,}")

    remaining = s.run(
        "MATCH (n:Section8AMILimit) WHERE NOT (n)--() RETURN count(n) AS c"
    ).single()["c"]
    total = s.run("MATCH (n:Section8AMILimit) RETURN count(n) AS c").single()["c"]
    print(f"  Remaining standalone: {remaining:,} / {total:,} ({remaining/total*100:.1f}%) ({time.time()-t0:.1f}s)")


# ══════════════════════════════════════════════════════════════════
# FIX 3: LenderBehaviorRisk (non-metro) → County via msa_code
# ══════════════════════════════════════════════════════════════════
print("\n=== FIX 3: LenderBehaviorRisk → County (non-metro) ===")
t0 = time.time()
with d.session() as s:
    r = s.run("""
        MATCH (n:LenderBehaviorRisk) WHERE NOT (n)--()
          AND n.msa_code IS NOT NULL AND n.msa_code <> 'NA'
        MATCH (c:County {county_fips: n.msa_code})
        MERGE (n)-[:APPLIES_TO]->(c)
        RETURN count(*) AS c
    """).single()
    print(f"  APPLIES_TO County edges created: {r['c']:,}")

    remaining = s.run(
        "MATCH (n:LenderBehaviorRisk) WHERE NOT (n)--() RETURN count(n) AS c"
    ).single()["c"]
    total = s.run("MATCH (n:LenderBehaviorRisk) RETURN count(n) AS c").single()["c"]
    print(f"  Remaining standalone: {remaining:,} / {total:,} ({remaining/total*100:.1f}%) ({time.time()-t0:.1f}s)")


# ══════════════════════════════════════════════════════════════════
# FINAL AUDIT
# ══════════════════════════════════════════════════════════════════
print("\n=== FINAL STANDALONE AUDIT ===")
labels = [
    "State", "County", "CensusTract", "MetroArea",
    "QCTDesignation", "SDDADesignation", "NMDDADesignation",
    "Section8AMILimit", "StateAMILimit", "LenderBehaviorRisk", "SpecialProgramLimit",
]
with d.session() as s:
    print(f"  {'Label':<25} {'Total':>10} {'Standalone':>12} {'%':>7}")
    print("  " + "-" * 58)
    for label in labels:
        total = s.run(f"MATCH (n:{label}) RETURN count(n) AS c").single()["c"]
        standalone = s.run(
            f"MATCH (n:{label}) WHERE NOT (n)--() RETURN count(n) AS c"
        ).single()["c"]
        pct = f"{standalone/total*100:.1f}%" if total > 0 else "n/a"
        flag = " *" if standalone > 0 else ""
        print(f"  {label:<25} {total:>10,} {standalone:>12,} {pct:>7}{flag}")

    print()
    for rel in ["APPLIES_TO", "HAS_MSA_AMI", "HAS_STATE_AMI", "IN_STATE", "IN_METRO", "IN_COUNTY"]:
        c = s.run(f"MATCH ()-[r:{rel}]->() RETURN count(r) AS c").single()["c"]
        print(f"  :{rel:<24} {c:>12,}")

d.close()
print("\nAll fixes applied.")
