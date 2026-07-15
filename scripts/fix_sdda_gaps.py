"""Fix remaining 44 SDDA missing edges:
- Add missing MetroArea nodes (Dayton 19380, Prescott 39140)
- Create SDDA->MetroArea edges for real MSAs via cbsa extracted from fmr_area_code
- Leave PR territories and county-level HUD FMR areas as documented gaps
"""
from neo4j import GraphDatabase

d = GraphDatabase.driver("neo4j://127.0.0.1:7687", auth=("neo4j", "Letmein2272"))

with d.session() as s:
    # Add missing metro nodes for Dayton and Prescott (not in OMB 2023 crosswalk
    # because they became metro divisions or were reclassified)
    metros_to_add = [
        {"cbsa_code": "19380", "cbsa_title": "Dayton-Kettering, OH"},
        {"cbsa_code": "39140", "cbsa_title": "Prescott Valley-Prescott, AZ"},
    ]
    for m in metros_to_add:
        r = s.run(
            "MERGE (m:MetroArea {cbsa_code: $cbsa}) "
            "SET m.cbsa_title = $title "
            "RETURN m.cbsa_code",
            cbsa=m["cbsa_code"], title=m["cbsa_title"]
        ).single()
        print(f"  MetroArea MERGE: cbsa={r[0]}")

    # For MSA-level fmr_area_codes extract cbsa from METRO{cbsa}M{cbsa} pattern
    # and create APPLIES_TO edges
    missing = s.run(
        "MATCH (s:SDDADesignation) WHERE NOT (s)-[:APPLIES_TO]->() "
        "RETURN s.designation_id AS did, s.fmr_area_code AS fmr, s.area_name AS name"
    ).data()

    fixed = 0
    skipped_pr = 0
    skipped_county = 0

    for row in missing:
        fmr = row["fmr"] or ""
        name = row["name"] or ""

        # Skip PR territories
        if "PR" in name or "Municipio" in name:
            skipped_pr += 1
            continue

        # Extract CBSA from METRO{cbsa}M{cbsa} pattern
        cbsa = None
        if "M" in fmr[5:]:
            # Format: METRO{cbsa}M{cbsa} or METRO{cbsa}N{county}
            after_metro = fmr[5:]  # strip "METRO"
            if "M" in after_metro:
                idx = after_metro.index("M")
                cbsa_candidate = after_metro[:idx]
                # Verify it's numeric
                if cbsa_candidate.isdigit():
                    cbsa = cbsa_candidate

        if not cbsa:
            skipped_county += 1
            continue

        # Try to create edge
        result = s.run(
            "MATCH (s:SDDADesignation {designation_id: $did}) "
            "MATCH (m:MetroArea {cbsa_code: $cbsa}) "
            "MERGE (s)-[:APPLIES_TO]->(m) "
            "RETURN count(*) AS c",
            did=row["did"], cbsa=cbsa
        ).single()

        if result and result["c"] > 0:
            fixed += 1
        else:
            skipped_county += 1
            print(f"  No MetroArea for cbsa={cbsa} ({name})")

    print(f"\nFixed: {fixed} SDDA edges created")
    print(f"Skipped PR territories: {skipped_pr}")
    print(f"Skipped county-level/unresolvable: {skipped_county}")

    print()
    remaining = s.run(
        "MATCH (s:SDDADesignation) WHERE NOT (s)-[:APPLIES_TO]->() RETURN count(s) AS c"
    ).single()["c"]
    total = s.run("MATCH (s:SDDADesignation) RETURN count(s) AS c").single()["c"]
    print(f"SDDA coverage: {total - remaining}/{total} ({(total-remaining)/total*100:.1f}%)")

d.close()
