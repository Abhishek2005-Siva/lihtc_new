"""
bootstrap_geography.py

Creates geographic anchor nodes and edges in Neo4j in strict dependency order:
  Step 0 — Constraints
  Step 1 — State        (hardcoded lookup, 51 rows)
  Step 2 — County       (derived from TIGER tract file)
  Step 3 — CensusTract  Phase 1: identity only from TIGER
  Step 4 — MetroArea    (from QCT Silver -- unique cbsa_codes)
  Step 5 — CensusTract  Phase 2: enrich cbsa_code / fmr_area_code / is_metro
  Step 6 — Edges        (IN_STATE, IN_METRO, IN_COUNTY)
  Step 7 — Fix cbsa_code float artifact on CensusTract (e.g. '33860.0' -> '33860')
  Step 8 — OMB crosswalk: enrich MetroArea with metro_name from OMB List1

Sources:
  TIGER: bronze_files/Geographic/census_tract_reference_2025.csv
  QCT:   silver/qct/silver_qct_2025.parquet
  OMB:   bronze_files/omb/list1_2023.xlsx  (downloaded if missing)

Usage:
    python scripts/bootstrap/bootstrap_geography.py
    python scripts/bootstrap/bootstrap_geography.py --dry-run
    python scripts/bootstrap/bootstrap_geography.py --step 7 8
"""

from __future__ import annotations

import argparse
import os
import urllib.request
from pathlib import Path

import pandas as pd
from neo4j import GraphDatabase

# -- Paths --------------------------------------------------------------------
ROOT       = Path(__file__).resolve().parent.parent.parent
TIGER_PATH = ROOT / "bronze_files" / "Geographic" / "census_tract_reference_2025.csv"
QCT_PATH   = ROOT / "silver" / "qct" / "silver_qct_2025.parquet"
OMB_PATH   = ROOT / "bronze_files" / "omb" / "list1_2023.xlsx"
OMB_URL    = (
    "https://www2.census.gov/programs-surveys/metro-micro/geographies/"
    "reference-files/2023/delineation-files/list1_2023.xlsx"
)

# -- Neo4j connection ---------------------------------------------------------
NEO4J_URI      = os.getenv("NEO4J_URI", "neo4j://127.0.0.1:7687")
NEO4J_USER     = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")

BATCH = 5_000


# -- State lookup (50 states + DC + 5 territories) ----------------------------
STATES = [
    ("01","AL","Alabama"),       ("02","AK","Alaska"),
    ("04","AZ","Arizona"),       ("05","AR","Arkansas"),
    ("06","CA","California"),    ("08","CO","Colorado"),
    ("09","CT","Connecticut"),   ("10","DE","Delaware"),
    ("11","DC","District of Columbia"),
    ("12","FL","Florida"),       ("13","GA","Georgia"),
    ("15","HI","Hawaii"),        ("16","ID","Idaho"),
    ("17","IL","Illinois"),      ("18","IN","Indiana"),
    ("19","IA","Iowa"),          ("20","KS","Kansas"),
    ("21","KY","Kentucky"),      ("22","LA","Louisiana"),
    ("23","ME","Maine"),         ("24","MD","Maryland"),
    ("25","MA","Massachusetts"), ("26","MI","Michigan"),
    ("27","MN","Minnesota"),     ("28","MS","Mississippi"),
    ("29","MO","Missouri"),      ("30","MT","Montana"),
    ("31","NE","Nebraska"),      ("32","NV","Nevada"),
    ("33","NH","New Hampshire"), ("34","NJ","New Jersey"),
    ("35","NM","New Mexico"),    ("36","NY","New York"),
    ("37","NC","North Carolina"),("38","ND","North Dakota"),
    ("39","OH","Ohio"),          ("40","OK","Oklahoma"),
    ("41","OR","Oregon"),        ("42","PA","Pennsylvania"),
    ("44","RI","Rhode Island"),  ("45","SC","South Carolina"),
    ("46","SD","South Dakota"),  ("47","TN","Tennessee"),
    ("48","TX","Texas"),         ("49","UT","Utah"),
    ("50","VT","Vermont"),       ("51","VA","Virginia"),
    ("53","WA","Washington"),    ("54","WV","West Virginia"),
    ("55","WI","Wisconsin"),     ("56","WY","Wyoming"),
    ("60","AS","American Samoa"),
    ("66","GU","Guam"),
    ("69","MP","Northern Mariana Islands"),
    ("72","PR","Puerto Rico"),
    ("78","VI","U.S. Virgin Islands"),
]


# -- Helpers ------------------------------------------------------------------
def _batched_run(session, query: str, rows: list[dict], label: str) -> None:
    total = 0
    for i in range(0, len(rows), BATCH):
        session.run(query, rows=rows[i : i + BATCH])
        total += min(BATCH, len(rows) - i)
    print(f"  {label}: {len(rows):,} rows ({total // BATCH + 1} batches)")


def _load_tiger() -> pd.DataFrame:
    df = pd.read_csv(TIGER_PATH, dtype=str)
    df.columns = df.columns.str.upper()
    df["state_fips"]  = df["STATEFP"].str.zfill(2)
    df["county_fips"] = df["state_fips"] + df["COUNTYFP"].str.zfill(3)
    df["fips_code"]   = df["GEOID"].str.zfill(11)
    print(f"  TIGER loaded: {len(df):,} tracts")
    return df


def _load_qct() -> pd.DataFrame:
    df = pd.read_parquet(QCT_PATH)
    df["cbsa_code"]  = df["cbsa_code"].astype(str).where(
        df["cbsa_code"].notna() & (df["cbsa_code"].astype(str) != "100000"), None
    )
    df["fmr_area_code"] = df["fmr_area_code"].astype(str)
    df["state_fips"]    = df["state_fips"].astype(str).str.zfill(2)
    df["county_fips"]   = df["county_fips"].astype(str).str.zfill(5)
    df["tract_fips"]    = df["tract_fips"].astype(str).str.zfill(11)
    print(f"  QCT loaded: {len(df):,} tracts")
    return df


def _load_omb() -> pd.DataFrame:
    """Download OMB List1 crosswalk if not already cached."""
    if not OMB_PATH.exists():
        OMB_PATH.parent.mkdir(parents=True, exist_ok=True)
        print(f"  Downloading OMB List1 from Census Bureau...", flush=True)
        urllib.request.urlretrieve(OMB_URL, OMB_PATH)
        print(f"  Saved: {OMB_PATH} ({OMB_PATH.stat().st_size // 1024:,} KB)")
    df = pd.read_excel(OMB_PATH, header=2, dtype=str)
    df.columns = df.columns.str.strip()
    print(f"  OMB loaded: {len(df):,} rows, columns: {list(df.columns)}")
    return df


# -- Step 0: Constraints ------------------------------------------------------
def step0_constraints(session) -> None:
    stmts = [
        "CREATE CONSTRAINT IF NOT EXISTS FOR (n:State)       REQUIRE n.state_fips  IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (n:County)      REQUIRE n.county_fips IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (n:CensusTract) REQUIRE n.fips_code   IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (n:MetroArea)   REQUIRE n.cbsa_code   IS UNIQUE",
    ]
    for s in stmts:
        session.run(s)
    print("  Constraints created/verified (4 total)")


# -- Step 1: State ------------------------------------------------------------
def step1_states(session) -> None:
    rows = [{"state_fips": f, "state_abbr": a, "state_name": n} for f, a, n in STATES]
    _batched_run(session, """
        UNWIND $rows AS row
        MERGE (s:State {state_fips: row.state_fips})
        SET s.state_abbr = row.state_abbr,
            s.state_name = row.state_name
    """, rows, "State nodes")


# -- Step 2: County -----------------------------------------------------------
_COUNTY_REF = ROOT / "bronze_files" / "Geographic" / "national_county.txt"


def _load_county_names() -> dict[str, str]:
    """
    Fix 4: Load county_fips → county_name from Census ANSI county codes file.
    Returns {fips5: "Dallas County"} etc.
    """
    if not _COUNTY_REF.exists():
        print("  national_county.txt not found — county_name will be null")
        return {}
    nc = pd.read_csv(_COUNTY_REF, sep="|", dtype=str)
    result = {}
    for _, row in nc.iterrows():
        fips5 = row["STATEFP"].zfill(2) + row["COUNTYFP"].zfill(3)
        result[fips5] = row["COUNTYNAME"].strip()
    print(f"  County name lookup loaded: {len(result):,} entries")
    return result


def step2_counties(session, tiger: pd.DataFrame) -> None:
    counties = (
        tiger[["county_fips", "state_fips"]]
        .drop_duplicates("county_fips")
        .copy()
    )
    # Fix 4: populate county_name from Census ANSI codes reference
    name_map = _load_county_names()
    counties["county_name"] = counties["county_fips"].map(name_map)
    rows = counties.to_dict("records")
    _batched_run(session, """
        UNWIND $rows AS row
        MERGE (c:County {county_fips: row.county_fips})
        SET c.state_fips  = row.state_fips,
            c.county_name = row.county_name
    """, rows, "County nodes")


# -- Step 3: CensusTract Phase 1 ----------------------------------------------
def step3_tracts_phase1(session, tiger: pd.DataFrame) -> None:
    tracts = tiger[["fips_code", "state_fips", "county_fips"]].drop_duplicates("fips_code")
    rows = tracts.to_dict("records")
    _batched_run(session, """
        UNWIND $rows AS row
        MERGE (t:CensusTract {fips_code: row.fips_code})
        SET t.state_fips  = row.state_fips,
            t.county_fips = row.county_fips
    """, rows, "CensusTract Phase 1 nodes")


# -- Step 4: MetroArea --------------------------------------------------------
def step4_metro_areas(session, qct: pd.DataFrame) -> None:
    metro = (
        qct[qct["cbsa_code"].notna()]
        .drop_duplicates("cbsa_code")
        [["cbsa_code", "fmr_area_code", "state_fips", "area_population"]]
        .copy()
    )
    metro["metro_name"]      = None
    metro["area_population"] = pd.to_numeric(metro["area_population"], errors="coerce")
    rows = metro.to_dict("records")
    _batched_run(session, """
        UNWIND $rows AS row
        MERGE (m:MetroArea {cbsa_code: row.cbsa_code})
        SET m.fmr_area_code   = row.fmr_area_code,
            m.state_fips      = row.state_fips,
            m.area_population = row.area_population,
            m.metro_name      = row.metro_name
    """, rows, "MetroArea nodes")


# -- Step 5: CensusTract Phase 2 ----------------------------------------------
def step5_tracts_phase2(session, qct: pd.DataFrame) -> None:
    enrichment = (
        qct[["tract_fips", "cbsa_code", "fmr_area_code", "is_metro_tract"]]
        .drop_duplicates("tract_fips")
        .rename(columns={"tract_fips": "fips_code", "is_metro_tract": "is_metro"})
        .copy()
    )
    enrichment["is_metro"] = enrichment["is_metro"].apply(
        lambda x: bool(x) if pd.notna(x) else None
    )
    # Strip any float artifact from cbsa_code in the silver parquet before writing
    enrichment["cbsa_code"] = enrichment["cbsa_code"].apply(_clean_cbsa_code)
    rows = enrichment.to_dict("records")
    _batched_run(session, """
        UNWIND $rows AS row
        MATCH (t:CensusTract {fips_code: row.fips_code})
        SET t.cbsa_code     = row.cbsa_code,
            t.fmr_area_code = row.fmr_area_code,
            t.is_metro      = row.is_metro
    """, rows, "CensusTract Phase 2 enrichment")


# -- Step 6: Edges ------------------------------------------------------------
def step6_edges(session, tiger: pd.DataFrame, qct: pd.DataFrame) -> None:
    county_state = (
        tiger[["county_fips", "state_fips"]]
        .drop_duplicates()
        .to_dict("records")
    )
    _batched_run(session, """
        UNWIND $rows AS row
        MATCH (c:County {county_fips: row.county_fips})
        MATCH (s:State  {state_fips:  row.state_fips})
        MERGE (c)-[:IN_STATE]->(s)
    """, county_state, "County->State edges")

    county_metro = (
        qct[qct["cbsa_code"].notna()]
        [["county_fips", "cbsa_code"]]
        .drop_duplicates()
        .to_dict("records")
    )
    _batched_run(session, """
        UNWIND $rows AS row
        MATCH (c:County    {county_fips: row.county_fips})
        MATCH (m:MetroArea {cbsa_code:   row.cbsa_code})
        MERGE (c)-[:IN_METRO]->(m)
    """, county_metro, "County->MetroArea edges")

    metro_state = (
        qct[qct["cbsa_code"].notna()]
        [["cbsa_code", "state_fips"]]
        .drop_duplicates("cbsa_code")
        .to_dict("records")
    )
    _batched_run(session, """
        UNWIND $rows AS row
        MATCH (m:MetroArea {cbsa_code:  row.cbsa_code})
        MATCH (s:State     {state_fips: row.state_fips})
        MERGE (m)-[:IN_STATE]->(s)
    """, metro_state, "MetroArea->State edges")

    tract_county = (
        qct[["tract_fips", "county_fips"]]
        .drop_duplicates("tract_fips")
        .rename(columns={"tract_fips": "fips_code"})
        .to_dict("records")
    )
    _batched_run(session, """
        UNWIND $rows AS row
        MATCH (t:CensusTract {fips_code:   row.fips_code})
        MATCH (c:County      {county_fips: row.county_fips})
        MERGE (t)-[:IN_COUNTY]->(c)
    """, tract_county, "CensusTract->County edges")

    tract_metro = (
        qct[qct["cbsa_code"].notna()]
        [["tract_fips", "cbsa_code"]]
        .drop_duplicates("tract_fips")
        .rename(columns={"tract_fips": "fips_code"})
        .to_dict("records")
    )
    _batched_run(session, """
        UNWIND $rows AS row
        MATCH (t:CensusTract {fips_code: row.fips_code})
        MATCH (m:MetroArea   {cbsa_code: row.cbsa_code})
        MERGE (t)-[:IN_METRO]->(m)
    """, tract_metro, "CensusTract->MetroArea edges")


# -- Step 7: Fix cbsa_code float artifact -------------------------------------
def _clean_cbsa_code(val) -> str | None:
    """Strip .0 suffix and nullify non-numeric / sentinel values."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    if s.endswith(".0"):
        s = s[:-2]
    if not s or s.lower() in {"nan", "none", "100000"}:
        return None
    return s if s.isdigit() else None


def step7_fix_cbsa_code(session) -> None:
    """
    Fix CensusTract.cbsa_code float artifact written by older pandas versions.
    e.g. '33860.0' -> '33860', 'nan' -> removed.
    This runs as a Cypher pass on the graph so it catches any residual artifacts
    regardless of how the nodes were created.
    """
    # Strip .0 suffix
    r1 = session.run("""
        MATCH (ct:CensusTract)
        WHERE ct.cbsa_code IS NOT NULL AND ct.cbsa_code ENDS WITH '.0'
        WITH ct, left(ct.cbsa_code, size(ct.cbsa_code) - 2) AS clean
        SET ct.cbsa_code = clean
        RETURN count(ct) AS fixed
    """).single()
    print(f"  cbsa_code '.0' suffix stripped: {r1['fixed']:,} nodes")

    # Remove 'nan' string values
    r2 = session.run("""
        MATCH (ct:CensusTract)
        WHERE ct.cbsa_code IN ['nan', 'None', 'none', '100000']
        REMOVE ct.cbsa_code
        RETURN count(ct) AS cleaned
    """).single()
    print(f"  cbsa_code invalid values removed: {r2['cleaned']:,} nodes")


# -- Step 8: OMB crosswalk -- enrich MetroArea.metro_name ---------------------
def step8_omb_metro_names(session) -> None:
    """
    Set MetroArea.metro_name from the OMB CBSA delineation file (List 1 2023).
    Also ensures any MetroArea nodes added by fix_sdda_gaps that were missing
    from the OMB file get a name if available.
    """
    omb = _load_omb()

    # Resolve column names flexibly (column headers vary slightly across vintages)
    cbsa_code_col = next(c for c in omb.columns if "CBSA" in c and "Code" in c)
    cbsa_title_col = next(c for c in omb.columns if "CBSA" in c and "Title" in c)

    names = (
        omb[[cbsa_code_col, cbsa_title_col]]
        .dropna()
        .drop_duplicates(subset=[cbsa_code_col])
        .copy()
    )
    names.columns = ["cbsa_code", "metro_name"]
    names["cbsa_code"] = names["cbsa_code"].str.strip()

    rows = names.to_dict("records")
    # Fix 8: set both metro_name and cbsa_title so synthesizer finds either property
    _batched_run(session, """
        UNWIND $rows AS row
        MATCH (m:MetroArea {cbsa_code: row.cbsa_code})
        SET m.metro_name  = row.metro_name,
            m.cbsa_title  = row.metro_name
    """, rows, "MetroArea.metro_name + cbsa_title from OMB")

    # Hardcode names for metros not in OMB 2023 (reclassified or metro divisions)
    extras = [
        {"cbsa_code": "19380", "metro_name": "Dayton-Kettering, OH"},
        {"cbsa_code": "39140", "metro_name": "Prescott Valley-Prescott, AZ"},
    ]
    for row in extras:
        session.run(
            "MERGE (m:MetroArea {cbsa_code: $cbsa}) "
            "SET m.metro_name = $name, m.cbsa_title = $name",
            cbsa=row["cbsa_code"], name=row["metro_name"],
        )
    print(f"  Extra MetroArea nodes ensured: {len(extras)}")


# -- Dry-run summary ----------------------------------------------------------
def dry_run(tiger: pd.DataFrame, qct: pd.DataFrame) -> None:
    print("\n=== Dry-run counts (no Neo4j writes) ===")
    print(f"  States:         {len(STATES)}")
    counties = tiger[["county_fips"]].drop_duplicates()
    print(f"  Counties:       {len(counties):,}")
    tracts = tiger[["fips_code"]].drop_duplicates()
    print(f"  CensusTract:    {len(tracts):,}")
    metro = qct[qct["cbsa_code"].notna()].drop_duplicates("cbsa_code")
    print(f"  MetroArea:      {len(metro):,}")
    county_metro = qct[qct["cbsa_code"].notna()][["county_fips", "cbsa_code"]].drop_duplicates()
    print(f"  County->MetroArea edges:  {len(county_metro):,}")
    tract_county = qct[["tract_fips", "county_fips"]].drop_duplicates("tract_fips")
    print(f"  Tract->County edges:      {len(tract_county):,}")
    tract_metro  = qct[qct["cbsa_code"].notna()][["tract_fips", "cbsa_code"]].drop_duplicates("tract_fips")
    print(f"  Tract->MetroArea edges:   {len(tract_metro):,}")
    float_artifacts = qct["cbsa_code"].dropna().astype(str).str.endswith(".0").sum()
    print(f"  cbsa_code float artifacts in QCT parquet: {float_artifacts}")


# -- Verification -------------------------------------------------------------
def verify(session) -> None:
    print("\n=== Verification ===")
    checks = [
        ("State count",          "MATCH (n:State)       RETURN count(n) AS c"),
        ("County count",         "MATCH (n:County)      RETURN count(n) AS c"),
        ("CensusTract count",    "MATCH (n:CensusTract) RETURN count(n) AS c"),
        ("MetroArea count",      "MATCH (n:MetroArea)   RETURN count(n) AS c"),
        ("IN_STATE edges",       "MATCH ()-[:IN_STATE]->()  RETURN count(*) AS c"),
        ("IN_METRO edges",       "MATCH ()-[:IN_METRO]->()  RETURN count(*) AS c"),
        ("IN_COUNTY edges",      "MATCH ()-[:IN_COUNTY]->() RETURN count(*) AS c"),
        ("Orphaned tracts",      "MATCH (t:CensusTract) WHERE NOT (t)-[:IN_COUNTY]->() RETURN count(t) AS c"),
        ("cbsa=100000 leak",     "MATCH (t:CensusTract) WHERE t.cbsa_code = '100000' RETURN count(t) AS c"),
        ("cbsa .0 artifact",     "MATCH (t:CensusTract) WHERE t.cbsa_code ENDS WITH '.0' RETURN count(t) AS c"),
        ("cbsa nan artifact",    "MATCH (t:CensusTract) WHERE t.cbsa_code = 'nan' RETURN count(t) AS c"),
        ("MetroArea w/ name",    "MATCH (m:MetroArea) WHERE m.metro_name IS NOT NULL RETURN count(m) AS c"),
    ]
    for label, q in checks:
        result = session.run(q).single()
        val = result["c"] if result else "?"
        bad_if_nonzero = label in ("Orphaned tracts", "cbsa=100000 leak", "cbsa .0 artifact", "cbsa nan artifact")
        flag = " OK" if (bad_if_nonzero and val == 0) else (" WARN" if (bad_if_nonzero and val > 0) else "")
        print(f"  {label:<28} {val:>8,}{flag}")


# -- CLI ----------------------------------------------------------------------
_ALL_STEPS = list(range(9))  # 0-8


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Bootstrap geographic nodes and edges in Neo4j.")
    p.add_argument("--step", type=int, nargs="*", metavar="N",
                   help="Run only these steps (0-8). Default: all.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print row counts without connecting to Neo4j.")
    p.add_argument("--verify-only", action="store_true",
                   help="Skip all writes -- just run verification queries.")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    steps = set(args.step) if args.step is not None else set(_ALL_STEPS)

    print("Loading source files...")
    tiger = _load_tiger()
    qct   = _load_qct()

    if args.dry_run:
        dry_run(tiger, qct)
        return

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        driver.verify_connectivity()
        print(f"Connected to Neo4j at {NEO4J_URI}")
    except Exception as e:
        print(f"Neo4j connection failed: {e}")
        driver.close()
        return

    with driver.session() as session:
        if args.verify_only:
            verify(session)
            driver.close()
            return

        step_fns = {
            0: ("Constraints",                lambda: step0_constraints(session)),
            1: ("State nodes",                lambda: step1_states(session)),
            2: ("County nodes",               lambda: step2_counties(session, tiger)),
            3: ("CensusTract Phase 1",        lambda: step3_tracts_phase1(session, tiger)),
            4: ("MetroArea nodes",            lambda: step4_metro_areas(session, qct)),
            5: ("CensusTract Phase 2",        lambda: step5_tracts_phase2(session, qct)),
            6: ("Edges",                      lambda: step6_edges(session, tiger, qct)),
            7: ("Fix cbsa_code float artifact",lambda: step7_fix_cbsa_code(session)),
            8: ("OMB metro names",            lambda: step8_omb_metro_names(session)),
        }

        for n in sorted(steps):
            if n not in step_fns:
                print(f"  Step {n}: unknown -- skipped")
                continue
            label, fn = step_fns[n]
            print(f"\n[Step {n}] {label}")
            fn()

        if max(steps, default=-1) >= 6:
            verify(session)

    driver.close()
    print("\nGeographic bootstrap complete.")


if __name__ == "__main__":
    main()
