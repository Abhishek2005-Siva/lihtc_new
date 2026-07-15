"""Fix 1-3: OMB crosswalk, HAS_STATE_AMI, SDDA missing edges."""
import pandas as pd, time, urllib.request, warnings
from pathlib import Path
from neo4j import GraphDatabase
warnings.filterwarnings("ignore")

OMB_URL = "https://www2.census.gov/programs-surveys/metro-micro/geographies/reference-files/2023/delineation-files/list1_2023.xlsx"
OMB_PATH = Path("bronze_files/omb/list1_2023.xlsx")
NEO4J_IMPORT = Path(
    r"C:\Users\Abhishek\.Neo4jDesktop2\Data\dbmss"
    r"\dbms-739a327b-c575-4606-92db-b2355c89faf3\import"
)
SILVER = Path("silver")

d = GraphDatabase.driver("neo4j://127.0.0.1:7687", auth=("neo4j", "Letmein2272"))


def cypher(session, query, csv_name=None):
    """Run LOAD CSV query, clean up CSV after."""
    session.run(query)
    if csv_name:
        (NEO4J_IMPORT / csv_name).unlink(missing_ok=True)


# ══════════════════════════════════════════════════════════════════════
# FIX 1: OMB Crosswalk
# ══════════════════════════════════════════════════════════════════════
print("=== FIX 1: OMB Crosswalk ===", flush=True)

Path("bronze_files/omb").mkdir(parents=True, exist_ok=True)
if not OMB_PATH.exists():
    print("  Downloading OMB List1 2023...", flush=True)
    urllib.request.urlretrieve(OMB_URL, OMB_PATH)
    print(f"  Downloaded: {OMB_PATH.stat().st_size / 1024:.0f} KB", flush=True)

omb = pd.read_excel(OMB_PATH, header=2, dtype=str)
omb.columns = omb.columns.str.strip()
print(f"  Columns: {list(omb.columns)}", flush=True)

state_col = [c for c in omb.columns if "State" in c and "FIPS" in c][0]
county_col = [c for c in omb.columns if "County" in c and "FIPS" in c][0]
cbsa_col = [c for c in omb.columns if "CBSA" in c and "Code" in c][0]
cbsa_title = [c for c in omb.columns if "CBSA" in c and "Title" in c][0]

omb["county_fips"] = omb[state_col].str.zfill(2) + omb[county_col].str.zfill(3)
omb["cbsa_code"] = omb[cbsa_col].str.strip()

crosswalk = omb[["county_fips", "cbsa_code"]].dropna().drop_duplicates()
crosswalk.to_csv("bronze_files/omb/cbsa_crosswalk.csv", index=False)
print(f"  Crosswalk: {len(crosswalk):,} county->CBSA mappings", flush=True)

# Update MetroArea names
cbsa_names = omb[[cbsa_col, cbsa_title]].dropna().drop_duplicates(cbsa_col)
cbsa_names.columns = ["cbsa_code", "metro_name"]
cbsa_names.to_csv(NEO4J_IMPORT / "cbsa_names.csv", index=False)
with d.session() as s:
    cypher(s, """
        LOAD CSV WITH HEADERS FROM 'file:///cbsa_names.csv' AS row
        CALL { WITH row
            MATCH (m:MetroArea {cbsa_code: row.cbsa_code})
            SET m.metro_name = row.metro_name
        } IN TRANSACTIONS OF 5000 ROWS
    """, "cbsa_names.csv")
print("  MetroArea names updated", flush=True)

# Delete wrong HMDA->County edges
with d.session() as s:
    r = s.run(
        "MATCH (r:LenderBehaviorRisk)-[e:APPLIES_TO]->(c:County) "
        "DELETE e RETURN count(e) AS c"
    ).single()
    print(f"  Deleted {r['c']:,} wrong HMDA->County edges", flush=True)

# Create correct HMDA->MetroArea edges
county_to_cbsa = dict(zip(crosswalk["county_fips"], crosswalk["cbsa_code"]))

for year in [2022, 2023, 2024, 2025]:
    p = SILVER / "hmda" / str(year) / f"silver_lender_behavior_risk_{year}.parquet"
    if not p.exists():
        continue
    df = pd.read_parquet(p, columns=["risk_id", "msa_code"])
    df = df[df["msa_code"].notna()].copy()
    df["county_fips"] = df["msa_code"].astype(str).str.zfill(5)
    df["cbsa_code"] = df["county_fips"].map(county_to_cbsa)
    matched = df[df["cbsa_code"].notna()][["risk_id", "cbsa_code"]].drop_duplicates()

    csv = f"hmda_metro_{year}.csv"
    matched.to_csv(NEO4J_IMPORT / csv, index=False)
    t0 = time.time()
    with d.session() as s:
        cypher(s, f"""
            LOAD CSV WITH HEADERS FROM 'file:///{csv}' AS row
            CALL {{ WITH row
                MATCH (r:LenderBehaviorRisk {{risk_id: row.risk_id}})
                MATCH (m:MetroArea {{cbsa_code: row.cbsa_code}})
                MERGE (r)-[:APPLIES_TO]->(m)
            }} IN TRANSACTIONS OF 5000 ROWS
        """, csv)
    unmatched = len(df) - len(matched)
    print(f"  HMDA {year}: {len(matched):,} -> MetroArea"
          f" ({unmatched} non-metro) - {time.time()-t0:.1f}s", flush=True)

    # Also set cbsa_code property on nodes
    csv2 = f"hmda_cbsa_{year}.csv"
    matched.to_csv(NEO4J_IMPORT / csv2, index=False)
    with d.session() as s:
        cypher(s, f"""
            LOAD CSV WITH HEADERS FROM 'file:///{csv2}' AS row
            CALL {{ WITH row
                MATCH (r:LenderBehaviorRisk {{risk_id: row.risk_id}})
                SET r.cbsa_code = row.cbsa_code
            }} IN TRANSACTIONS OF 5000 ROWS
        """, csv2)

print("Fix 1 complete.\n", flush=True)


# ══════════════════════════════════════════════════════════════════════
# FIX 2: HAS_STATE_AMI
# ══════════════════════════════════════════════════════════════════════
print("=== FIX 2: HAS_STATE_AMI ===", flush=True)

for year in range(2010, 2026):
    xwalk_p = SILVER / "ami" / str(year) / f"silver_county_area_crosswalk_{year}.parquet"
    if not xwalk_p.exists():
        continue
    xwalk = pd.read_parquet(xwalk_p)
    xwalk["county_fips"] = xwalk["fips"].astype(str).str[:5]
    nonmetro = xwalk[xwalk["metro"] == 0][["county_fips"]].drop_duplicates().copy()
    nonmetro["state_fips"] = nonmetro["county_fips"].str[:2]
    nonmetro["year"] = year

    csv = f"state_ami_{year}.csv"
    nonmetro.to_csv(NEO4J_IMPORT / csv, index=False)
    t0 = time.time()
    with d.session() as s:
        cypher(s, f"""
            LOAD CSV WITH HEADERS FROM 'file:///{csv}' AS row
            CALL {{ WITH row
                MATCH (c:County {{county_fips: row.county_fips}})
                MATCH (a:StateAMILimit)
                    WHERE a.state = row.state_fips
                    AND a.year = toInteger(row.year)
                MERGE (c)-[:HAS_STATE_AMI]->(a)
            }} IN TRANSACTIONS OF 2000 ROWS
        """, csv)
    print(f"  {year}: {len(nonmetro)} counties - {time.time()-t0:.1f}s", flush=True)

print("Fix 2 complete.\n", flush=True)


# ══════════════════════════════════════════════════════════════════════
# FIX 3: SDDA missing edges
# ══════════════════════════════════════════════════════════════════════
print("=== FIX 3: SDDA missing edges ===", flush=True)

qct = pd.read_parquet(
    SILVER / "qct" / "silver_qct_2025.parquet",
    columns=["fmr_area_code", "cbsa_code"],
)
fmr_to_cbsa = (
    qct[qct["cbsa_code"].notna()]
    .drop_duplicates("fmr_area_code")[["fmr_area_code", "cbsa_code"]]
)
print(f"  FMR->CBSA crosswalk: {len(fmr_to_cbsa)} mappings", flush=True)

with d.session() as s:
    missing = s.run(
        "MATCH (s:SDDADesignation) "
        "WHERE NOT (s)-[:APPLIES_TO]->() "
        "RETURN s.designation_id AS did, s.fmr_area_code AS fmr"
    ).data()
print(f"  SDDA nodes without edges: {len(missing)}", flush=True)

if missing:
    miss_df = pd.DataFrame(missing)
    miss_df = miss_df.merge(
        fmr_to_cbsa, left_on="fmr", right_on="fmr_area_code", how="left"
    )
    resolvable = miss_df[miss_df["cbsa_code"].notna()]
    print(f"  Resolvable: {len(resolvable)}", flush=True)

    if not resolvable.empty:
        csv = "sdda_fix.csv"
        resolvable[["did", "cbsa_code"]].to_csv(NEO4J_IMPORT / csv, index=False)
        with d.session() as s:
            cypher(s, f"""
                LOAD CSV WITH HEADERS FROM 'file:///{csv}' AS row
                CALL {{ WITH row
                    MATCH (s:SDDADesignation {{designation_id: row.did}})
                    MATCH (m:MetroArea {{cbsa_code: row.cbsa_code}})
                    MERGE (s)-[:APPLIES_TO]->(m)
                }} IN TRANSACTIONS OF 5000 ROWS
            """, csv)
            print(f"  Created {len(resolvable)} new edges", flush=True)

print("Fix 3 complete.\n", flush=True)


# ══════════════════════════════════════════════════════════════════════
# FINAL AUDIT
# ══════════════════════════════════════════════════════════════════════
print("=== FINAL AUDIT ===", flush=True)
with d.session() as s:
    for label in [
        "QCTDesignation", "SDDADesignation", "NMDDADesignation",
        "Section8AMILimit", "SpecialProgramLimit", "StateAMILimit",
        "LenderBehaviorRisk",
    ]:
        total = s.run(f"MATCH (n:{label}) RETURN count(n) AS c").single()["c"]
        out = s.run(
            f"MATCH (n:{label})-[r]->() RETURN count(r) AS c"
        ).single()["c"]
        inc = s.run(
            f"MATCH ()-[r]->(n:{label}) RETURN count(r) AS c"
        ).single()["c"]
        print(f"  {label:<25} {total:>10,} nodes  "
              f"out={out:>10,}  in={inc:>10,}", flush=True)

    print(flush=True)
    for rel in [
        "APPLIES_TO", "HAS_MSA_AMI", "HAS_STATE_AMI",
        "IN_STATE", "IN_METRO", "IN_COUNTY",
    ]:
        r = s.run(f"MATCH ()-[r:{rel}]->() RETURN count(r) AS c").single()
        print(f"  :{rel:<24} {r['c']:>12,}", flush=True)

d.close()
print("\nAll 3 fixes applied.", flush=True)
