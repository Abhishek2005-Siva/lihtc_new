"""
ingest_datasets.py — LOAD CSV approach for fast Neo4j ingestion

Exports silver parquet → CSV in Neo4j's import/ dir, then runs server-side
LOAD CSV ... IN TRANSACTIONS for ~5-10x faster ingestion vs Python driver batches.

Usage
-----
    python ingest_scripts/neo4j_ingest/ingest_datasets.py --dataset qct --year 2003-2025
    python ingest_scripts/neo4j_ingest/ingest_datasets.py --dataset qct dda ami hmda
    python ingest_scripts/neo4j_ingest/ingest_datasets.py --dataset qct --year 2025 --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from neo4j import GraphDatabase

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT     = Path(__file__).resolve().parents[2]
SILVER   = ROOT / "silver"
STATE_DB = ROOT / "graph_state.db"

NEO4J_IMPORT = Path(
    r"C:\Users\Abhishek\.Neo4jDesktop2\Data\dbmss"
    r"\dbms-739a327b-c575-4606-92db-b2355c89faf3\import"
)

NEO4J_URI      = os.getenv("NEO4J_URI", "neo4j://127.0.0.1:7687")
NEO4J_USER     = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")

TX_BATCH = 5_000  # rows per CALL {} IN TRANSACTIONS

_META = {
    "ingested_at", "last_updated_at", "pipeline_run_id",
    "bronze_source_id", "content_hash", "_row_status",
    "_skip_reason", "_new_hash", "action",
}


# ═══════════════════════════════════════════════════════════════════════════════
# graph_state.db
# ═══════════════════════════════════════════════════════════════════════════════

def _init_state_db() -> None:
    con = duckdb.connect(str(STATE_DB))
    con.execute("""
        CREATE TABLE IF NOT EXISTS node_state (
            label        VARCHAR,
            pk           VARCHAR,
            content_hash VARCHAR(16),
            ingested_at  TIMESTAMP DEFAULT now(),
            PRIMARY KEY (label, pk)
        )
    """)
    con.close()


def _hash_rows_fast(df: pd.DataFrame, exclude: set) -> pd.Series:
    """Vectorized hashing via pandas internals — no Python loop."""
    cols = [c for c in df.columns if c not in exclude]
    # cast to object first so fillna("") works on nullable Int64/Float64 columns
    h = pd.util.hash_pandas_object(df[cols].astype(object).fillna(""), index=False)
    return h.apply(lambda x: format(x, '016x'))


def _get_actions(df: pd.DataFrame, label: str, pk_col: str) -> pd.DataFrame:
    df = df.copy()

    # Fast path: sample incoming PKs — if none exist in state DB, skip hashing
    sample = df[pk_col].head(50).tolist()
    if not sample:
        df["_new_hash"] = "empty"
        df["action"] = "CREATE"
        return df
    con = duckdb.connect(str(STATE_DB))
    placeholders = ",".join(["?"] * len(sample))
    matched = con.execute(
        f"SELECT count(*) FROM node_state WHERE label = ? AND pk IN ({placeholders})",
        [label] + sample,
    ).fetchone()[0]
    con.close()

    if matched == 0:
        df["_new_hash"] = "new"
        df["action"] = "CREATE"
        return df

    # Slow path: compute hashes and compare
    df["_new_hash"] = _hash_rows_fast(df, _META)
    con = duckdb.connect(str(STATE_DB))
    con.register("incoming", df[[pk_col, "_new_hash"]].rename(columns={pk_col: "__pk"}))
    result = con.execute(f"""
        SELECT i.__pk AS {pk_col}, i._new_hash,
               CASE
                 WHEN s.pk IS NULL                  THEN 'CREATE'
                 WHEN s.content_hash != i._new_hash THEN 'MERGE'
                 ELSE 'SKIP'
               END AS action
        FROM incoming i
        LEFT JOIN node_state s ON s.label = '{label}' AND s.pk = i.__pk
    """).df()
    con.close()
    return df.merge(result[[pk_col, "action"]], on=pk_col)


def _update_state_bulk(label: str, pks: list[str], hashes: list[str]) -> None:
    if not pks:
        return
    incoming = pd.DataFrame({
        "label": label,
        "pk": pks,
        "content_hash": hashes,
    }).drop_duplicates(subset=["label", "pk"], keep="last")
    con = duckdb.connect(str(STATE_DB))
    con.register("_incoming_state", incoming)
    # Delete-then-insert is faster than UPSERT for bulk ops
    con.execute(f"DELETE FROM node_state WHERE label = '{label}' AND pk IN (SELECT pk FROM _incoming_state)")
    con.execute("INSERT INTO node_state (label, pk, content_hash) SELECT label, pk, content_hash FROM _incoming_state")
    con.close()


# ═══════════════════════════════════════════════════════════════════════════════
# Neo4j driver
# ═══════════════════════════════════════════════════════════════════════════════

_driver = None

def _get_driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD),
            max_connection_lifetime=600,
        )
    return _driver


def _clean_for_csv(v):
    if v is None:
        return ""
    if isinstance(v, float) and np.isnan(v):
        return ""
    if isinstance(v, (np.integer,)):
        return str(int(v))
    if isinstance(v, (np.floating,)):
        return str(float(v))
    if isinstance(v, (np.bool_,)):
        return str(bool(v))
    return str(v)


def _year_range(raw: list[str]) -> list[int]:
    years: list[int] = []
    for token in raw:
        if "-" in token and not token.lstrip("-").isdigit():
            parts = token.split("-")
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                years.extend(range(int(parts[0]), int(parts[1]) + 1))
                continue
        years.append(int(token))
    return sorted(set(years))


# ═══════════════════════════════════════════════════════════════════════════════
# LOAD CSV ingestion core
# ═══════════════════════════════════════════════════════════════════════════════

def _build_set_clause(cols: list[str], pk_col: str, type_hints: dict) -> str:
    """Build SET n.col = row.col or toInteger/toFloat for typed columns."""
    parts = []
    for c in cols:
        if c == pk_col:
            continue
        hint = type_hints.get(c, "str")
        if hint == "int":
            parts.append(f"n.{c} = CASE WHEN row.{c} = '' THEN null ELSE toInteger(row.{c}) END")
        elif hint == "float":
            parts.append(f"n.{c} = CASE WHEN row.{c} = '' THEN null ELSE toFloat(row.{c}) END")
        elif hint == "bool":
            parts.append(f"n.{c} = CASE WHEN row.{c} = '' THEN null ELSE (row.{c} = 'True') END")
        else:
            parts.append(f"n.{c} = CASE WHEN row.{c} = '' THEN null ELSE row.{c} END")
    return ",\n        ".join(parts)


def _infer_types(df: pd.DataFrame, exclude: set) -> dict[str, str]:
    """Infer Neo4j type hints from pandas dtypes."""
    hints = {}
    for col in df.columns:
        if col in exclude:
            continue
        dtype = df[col].dtype
        if pd.api.types.is_integer_dtype(dtype):
            hints[col] = "int"
        elif pd.api.types.is_float_dtype(dtype):
            hints[col] = "float"
        elif pd.api.types.is_bool_dtype(dtype):
            hints[col] = "bool"
        else:
            hints[col] = "str"
    return hints


def _ingest_via_csv(
    *,
    year: int,
    label: str,
    pk_col: str,
    parquet_path: Path,
    prop_exclude: set,
    edge_fk_col: str | None = None,
    edge_to_label: str | None = None,
    edge_to_pk: str | None = None,
    edge_rel: str = "APPLIES_TO",
    edge_match_by_prop: bool = False,
    filter_col: str | None = None,
    filter_value=None,
    dry_run: bool = False,
) -> None:
    if not parquet_path.exists():
        print(f"  [{label} {year}] SKIP — file not found", flush=True)
        return

    t0 = time.time()
    df = pd.read_parquet(parquet_path)

    if filter_col and filter_value is not None:
        df = df[df[filter_col] == filter_value].copy()

    if pk_col not in df.columns:
        print(f"  [{label} {year}] SKIP — no '{pk_col}' column", flush=True)
        return

    df = _get_actions(df, label, pk_col)
    active = df[df["action"] != "SKIP"]
    n_create = (df["action"] == "CREATE").sum()
    n_merge = (df["action"] == "MERGE").sum()
    n_skip = (df["action"] == "SKIP").sum()

    if dry_run:
        print(f"  [{label} {year}] DRY-RUN — CREATE={n_create} MERGE={n_merge} SKIP={n_skip}", flush=True)
        return

    if active.empty:
        print(f"  [{label} {year}] SKIP={n_skip} (all up-to-date)", flush=True)
        return

    full_exclude = _META | prop_exclude | {"action"}
    has_edge = edge_fk_col and edge_to_label and edge_to_pk

    # Determine columns for CSV (pk + data cols + optional FK)
    csv_cols = [c for c in active.columns if c not in full_exclude]
    if edge_fk_col and edge_fk_col not in csv_cols:
        csv_cols.append(edge_fk_col)
    type_hints = _infer_types(active, full_exclude)

    # Export CSV to Neo4j import directory
    csv_name = f"{label.lower()}_{year}.csv"
    csv_path = NEO4J_IMPORT / csv_name
    export_df = active[csv_cols].copy()
    for c in export_df.columns:
        export_df[c] = export_df[c].apply(_clean_for_csv)
    export_df.to_csv(csv_path, index=False)

    # Build SET clause
    node_cols = [c for c in csv_cols if c not in (prop_exclude | {edge_fk_col} if edge_fk_col else prop_exclude)]
    set_clause = _build_set_clause(node_cols, pk_col, type_hints)

    driver = _get_driver()

    # LOAD CSV for nodes
    node_query = f"""
        LOAD CSV WITH HEADERS FROM 'file:///{csv_name}' AS row
        CALL {{
            WITH row
            MERGE (n:{label} {{{pk_col}: row.{pk_col}}})
            SET {set_clause}
        }} IN TRANSACTIONS OF {TX_BATCH} ROWS
    """
    with driver.session() as s:
        print(f"    Loading {len(active):,} nodes...", end=" ", flush=True)
        s.run(node_query)
        t_nodes = time.time() - t0
        print(f"done ({t_nodes:.1f}s)", flush=True)

    # LOAD CSV for edges
    n_edges = 0
    if has_edge:
        if edge_match_by_prop:
            edge_query = f"""
                LOAD CSV WITH HEADERS FROM 'file:///{csv_name}' AS row
                WITH row WHERE row.{edge_fk_col} <> ''
                CALL {{
                    WITH row
                    MATCH (a:{label} {{{pk_col}: row.{pk_col}}})
                    MATCH (b:{edge_to_label}) WHERE b.{edge_to_pk} = row.{edge_fk_col}
                    MERGE (a)-[:{edge_rel}]->(b)
                }} IN TRANSACTIONS OF {TX_BATCH} ROWS
            """
        else:
            edge_query = f"""
                LOAD CSV WITH HEADERS FROM 'file:///{csv_name}' AS row
                WITH row WHERE row.{edge_fk_col} <> ''
                CALL {{
                    WITH row
                    MATCH (a:{label} {{{pk_col}: row.{pk_col}}})
                    MATCH (b:{edge_to_label} {{{edge_to_pk}: row.{edge_fk_col}}})
                    MERGE (a)-[:{edge_rel}]->(b)
                }} IN TRANSACTIONS OF {TX_BATCH} ROWS
            """
        with driver.session() as s:
            n_edges = (active[edge_fk_col].apply(lambda x: _clean_for_csv(x) != "")).sum()
            print(f"    Loading {n_edges:,} edges...", end=" ", flush=True)
            s.run(edge_query)
            t_edges = time.time() - t0 - t_nodes
            print(f"done ({t_edges:.1f}s)", flush=True)

    # Update state DB
    _update_state_bulk(label,
                       active[pk_col].tolist(),
                       active["_new_hash"].tolist())

    # Cleanup CSV
    csv_path.unlink(missing_ok=True)

    elapsed = time.time() - t0
    rate = len(active) / elapsed if elapsed > 0 else 0
    print(f"  [{label} {year}] CREATE={n_create} MERGE={n_merge} SKIP={n_skip} "
          f"| {len(active):,} nodes, {n_edges:,} edges — {elapsed:.1f}s ({rate:.0f}/s)", flush=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Dataset functions
# ═══════════════════════════════════════════════════════════════════════════════

def ingest_qct(year: int, dry_run: bool = False) -> None:
    _ingest_via_csv(
        year=year, label="QCTDesignation", pk_col="designation_id",
        parquet_path=SILVER / "qct" / f"silver_qct_{year}.parquet",
        prop_exclude={"tract_fips"},
        edge_fk_col="tract_fips", edge_to_label="CensusTract",
        edge_to_pk="fips_code", edge_rel="APPLIES_TO",
        dry_run=dry_run,
    )


def ingest_sdda(year: int, dry_run: bool = False) -> None:
    path = SILVER / "dda" / f"silver_dda_{year}_metro_area.parquet"
    if not path.exists():
        path = SILVER / "dda" / f"silver_dda_{year}_metro_zcta.parquet"
        if not path.exists():
            print(f"  [SDDA {year}] SKIP -- no metro file found", flush=True)
            return
        df = pd.read_parquet(path)
        df = df[df["is_designated"] == 1].copy()
        if "fmr_area_code" not in df.columns or df["fmr_area_code"].isna().all():
            # Pre-2017 zcta file: no fmr_area_code — ingest as bare SDDA nodes
            # grouped by area_name since we have no CBSA key
            if "area_name" in df.columns:
                df = df.groupby("area_name", as_index=False).first()
                df["designation_id"] = (
                    df["area_name"].str.replace(r"\W+", "_", regex=True) + "_" + str(year)
                )
            if "designation_year" not in df.columns:
                df["designation_year"] = year
            tmp = SILVER / "dda" / f"_tmp_sdda_{year}.parquet"
            df.to_parquet(tmp, index=False)
            # Ingest nodes only (no MetroArea edge — no CBSA code available)
            _ingest_via_csv(
                year=year, label="SDDADesignation", pk_col="designation_id",
                parquet_path=tmp,
                prop_exclude={"fmr_area_code"},
                edge_fk_col=None, dry_run=dry_run,
            )
            tmp.unlink(missing_ok=True)
        else:
            df = df.groupby("fmr_area_code", as_index=False).first()
            df["designation_id"] = df["fmr_area_code"] + "_" + str(year)
            if "designation_year" not in df.columns:
                df["designation_year"] = year
            tmp = SILVER / "dda" / f"_tmp_sdda_{year}.parquet"
            df.to_parquet(tmp, index=False)
            _ingest_via_csv(
                year=year, label="SDDADesignation", pk_col="designation_id",
                parquet_path=tmp,
                prop_exclude={"fmr_area_code"},
                edge_fk_col="fmr_area_code", edge_to_label="MetroArea",
                edge_to_pk="fmr_area_code", edge_rel="APPLIES_TO",
                edge_match_by_prop=True, dry_run=dry_run,
            )
            tmp.unlink(missing_ok=True)
    else:
        _ingest_via_csv(
            year=year, label="SDDADesignation", pk_col="designation_id",
            parquet_path=path,
            prop_exclude={"fmr_area_code"},
            edge_fk_col="fmr_area_code", edge_to_label="MetroArea",
            edge_to_pk="fmr_area_code", edge_rel="APPLIES_TO",
            edge_match_by_prop=True,
            filter_col="is_designated", filter_value=1,
            dry_run=dry_run,
        )

    # After ingesting nodes, fix any SDDA records that still have no APPLIES_TO edge.
    # This happens when fmr_area_code uses METRO{cbsa}M{cbsa} format and MetroArea
    # is keyed by cbsa_code not fmr_area_code. Extracted from fix_sdda_gaps.py.
    if not dry_run:
        _fix_sdda_missing_edges(year)


def ingest_nmdda(year: int, dry_run: bool = False) -> None:
    _ingest_via_csv(
        year=year, label="NMDDADesignation", pk_col="designation_id",
        parquet_path=SILVER / "dda" / f"silver_dda_{year}_nonmetro_county.parquet",
        prop_exclude={"county_fips"},
        edge_fk_col="county_fips", edge_to_label="County",
        edge_to_pk="county_fips", edge_rel="APPLIES_TO",
        filter_col="is_designated", filter_value=1,
        dry_run=dry_run,
    )


def ingest_ami(year: int, dry_run: bool = False) -> None:
    base = SILVER / "ami" / str(year)

    _ingest_via_csv(
        year=year, label="Section8AMILimit", pk_col="limit_id",
        parquet_path=base / f"silver_section8_ami_limit_{year}.parquet",
        prop_exclude=set(), dry_run=dry_run,
    )
    _ingest_via_csv(
        year=year, label="SpecialProgramLimit", pk_col="limit_id",
        parquet_path=base / f"silver_special_program_limit_{year}.parquet",
        prop_exclude=set(), dry_run=dry_run,
    )
    _ingest_via_csv(
        year=year, label="StateAMILimit", pk_col="limit_id",
        parquet_path=base / f"silver_state_ami_limit_{year}.parquet",
        prop_exclude=set(),
        edge_fk_col="state", edge_to_label="State",
        edge_to_pk="state_fips", edge_rel="APPLIES_TO",
        dry_run=dry_run,
    )

    # County AMI edges from crosswalk
    _ingest_county_ami_edges(year, dry_run=dry_run)


def _ingest_county_ami_edges(year: int, dry_run: bool = False) -> None:
    path = SILVER / "ami" / str(year) / f"silver_county_area_crosswalk_{year}.parquet"
    if not path.exists():
        print(f"  [AMI crosswalk {year}] SKIP — file not found", flush=True)
        return

    t0 = time.time()
    xwalk = pd.read_parquet(path)
    xwalk["county_fips"] = xwalk["fips"].astype(str).str[:5]

    metro = xwalk[xwalk["metro"] == 1][["county_fips", "hud_area_code"]].copy()
    nonmetro = xwalk[xwalk["metro"] == 0][["county_fips"]].drop_duplicates().copy()
    nonmetro["state_fips"] = nonmetro["county_fips"].str[:2]

    if dry_run:
        print(f"  [AMI crosswalk {year}] DRY-RUN — "
              f"{len(metro)} HAS_MSA_AMI, {len(nonmetro)} HAS_STATE_AMI", flush=True)
        return

    driver = _get_driver()

    # Metro edges via LOAD CSV
    if not metro.empty:
        csv_name = f"ami_metro_edges_{year}.csv"
        metro.to_csv(NEO4J_IMPORT / csv_name, index=False)
        with driver.session() as s:
            s.run(f"""
                LOAD CSV WITH HEADERS FROM 'file:///{csv_name}' AS row
                CALL {{
                    WITH row
                    MATCH (c:County {{county_fips: row.county_fips}})
                    MATCH (a:Section8AMILimit) WHERE a.hud_fmr_area_code = row.hud_area_code
                    MERGE (c)-[:HAS_MSA_AMI]->(a)
                }} IN TRANSACTIONS OF {TX_BATCH} ROWS
            """)
        (NEO4J_IMPORT / csv_name).unlink(missing_ok=True)

    # Non-metro edges
    if not nonmetro.empty:
        csv_name = f"ami_nonmetro_edges_{year}.csv"
        nonmetro.to_csv(NEO4J_IMPORT / csv_name, index=False)
        with driver.session() as s:
            s.run(f"""
                LOAD CSV WITH HEADERS FROM 'file:///{csv_name}' AS row
                CALL {{
                    WITH row
                    MATCH (c:County {{county_fips: row.county_fips}})
                    MATCH (a:StateAMILimit) WHERE a.state = row.state_fips
                    MERGE (c)-[:HAS_STATE_AMI]->(a)
                }} IN TRANSACTIONS OF {TX_BATCH} ROWS
            """)
        (NEO4J_IMPORT / csv_name).unlink(missing_ok=True)

    elapsed = time.time() - t0
    print(f"  [AMI crosswalk {year}] {len(metro)} HAS_MSA_AMI, "
          f"{len(nonmetro)} HAS_STATE_AMI — {elapsed:.1f}s", flush=True)


def _fix_sdda_missing_edges(year: int) -> None:
    """
    Create APPLIES_TO->MetroArea edges for SDDA nodes that have no edge yet.
    Happens when fmr_area_code is METRO{cbsa}M{cbsa} format; extract the CBSA
    and match to MetroArea.cbsa_code directly.
    Also ensures the two metros not in the OMB 2023 crosswalk exist as nodes.
    Originally in fix_sdda_gaps.py.
    """
    driver = _get_driver()

    # Ensure Dayton and Prescott exist (reclassified metros absent from OMB 2023)
    for cbsa, name in [("19380", "Dayton-Kettering, OH"), ("39140", "Prescott Valley-Prescott, AZ")]:
        with driver.session() as s:
            s.run(
                "MERGE (m:MetroArea {cbsa_code: $cbsa}) "
                "ON CREATE SET m.metro_name = $name "
                "ON MATCH SET m.metro_name = COALESCE(m.metro_name, $name)",
                cbsa=cbsa, name=name,
            )

    with driver.session() as s:
        missing = s.run(
            "MATCH (sd:SDDADesignation) "
            "WHERE NOT (sd)-[:APPLIES_TO]->() "
            "AND sd.designation_id ENDS WITH $suffix "
            "RETURN sd.designation_id AS did, sd.fmr_area_code AS fmr, sd.area_name AS name",
            suffix=f"_{year}",
        ).data()

    if not missing:
        return

    fixed = skipped_pr = skipped_county = 0
    for row in missing:
        fmr  = row["fmr"]  or ""
        name = row["name"] or ""

        # Skip Puerto Rico territories
        if "PR" in name or "Municipio" in name:
            skipped_pr += 1
            continue

        # Extract CBSA from METRO{cbsa}M{cbsa} pattern
        cbsa = None
        after_metro = fmr[5:] if fmr.upper().startswith("METRO") else fmr
        if "M" in after_metro:
            idx = after_metro.index("M")
            candidate = after_metro[:idx]
            if candidate.isdigit():
                cbsa = candidate

        if not cbsa:
            skipped_county += 1
            continue

        with driver.session() as s:
            result = s.run(
                "MATCH (sd:SDDADesignation {designation_id: $did}) "
                "MATCH (m:MetroArea {cbsa_code: $cbsa}) "
                "MERGE (sd)-[:APPLIES_TO]->(m) "
                "RETURN count(*) AS c",
                did=row["did"], cbsa=cbsa,
            ).single()
            if result and result["c"] > 0:
                fixed += 1
            else:
                skipped_county += 1

    if fixed or skipped_pr or skipped_county:
        print(
            f"  [SDDA gap fix {year}] edges created={fixed} "
            f"PR_skipped={skipped_pr} unresolvable={skipped_county}",
            flush=True,
        )


def ingest_hmda(year: int, dry_run: bool = False) -> None:
    # Remove any stale LenderBehaviorRisk->County edges before ingesting.
    # These were created by an earlier pipeline version and are incorrect --
    # HMDA risk records apply to MetroArea, not County.
    if not dry_run:
        driver = _get_driver()
        with driver.session() as s:
            r = s.run(
                "MATCH (rr:LenderBehaviorRisk)-[e:APPLIES_TO]->(c:County) "
                "DELETE e RETURN count(e) AS c"
            ).single()
            deleted = r["c"] if r else 0
            if deleted:
                print(f"  [HMDA {year}] Removed {deleted:,} stale APPLIES_TO->County edges", flush=True)

    _ingest_via_csv(
        year=year, label="LenderBehaviorRisk", pk_col="risk_id",
        parquet_path=SILVER / "hmda" / str(year) / f"silver_lender_behavior_risk_{year}.parquet",
        prop_exclude={"cbsa_code"},
        edge_fk_col="cbsa_code", edge_to_label="MetroArea",
        edge_to_pk="cbsa_code", edge_rel="APPLIES_TO",
        dry_run=dry_run,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

_DATASET_DEFAULTS = {
    "qct":  list(range(2003, 2026)),
    "dda":  list(range(2003, 2026)),  # 2003-2016 from PDF, 2017+ from xlsx
    "ami":  list(range(2010, 2026)),
    "hmda": [2022, 2023, 2024, 2025],
}

_DATASET_FNS = {
    "qct":  lambda y, d: ingest_qct(y, d),
    "dda":  lambda y, d: (ingest_sdda(y, d), ingest_nmdda(y, d)),
    "ami":  lambda y, d: ingest_ami(y, d),
    "hmda": lambda y, d: ingest_hmda(y, d),
}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ingest silver → Neo4j via LOAD CSV.")
    p.add_argument("--dataset", nargs="+", choices=["qct", "dda", "ami", "hmda"],
                   default=["qct", "dda", "ami", "hmda"], metavar="DS")
    p.add_argument("--year", nargs="+", metavar="Y",
                   help="Year(s) or range(s), e.g. 2025 or 2010-2025")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    _init_state_db()

    if not args.dry_run:
        driver = _get_driver()
        try:
            driver.verify_connectivity()
            print(f"Connected to Neo4j at {NEO4J_URI}", flush=True)
        except Exception as e:
            print(f"Neo4j connection failed: {e}")
            return

        with driver.session() as s:
            for lbl, pk in [
                ("QCTDesignation", "designation_id"),
                ("SDDADesignation", "designation_id"),
                ("NMDDADesignation", "designation_id"),
                ("Section8AMILimit", "limit_id"),
                ("SpecialProgramLimit", "limit_id"),
                ("StateAMILimit", "limit_id"),
                ("LenderBehaviorRisk", "risk_id"),
            ]:
                s.run(f"CREATE CONSTRAINT IF NOT EXISTS "
                      f"FOR (n:{lbl}) REQUIRE n.{pk} IS UNIQUE")
            print("Data node constraints verified.", flush=True)

    t_start = time.time()
    for dataset in args.dataset:
        years = _year_range(args.year) if args.year else _DATASET_DEFAULTS[dataset]
        print(f"\n=== {dataset.upper()} ({len(years)} years: {years[0]}-{years[-1]}) ===", flush=True)
        fn = _DATASET_FNS[dataset]
        for year in years:
            fn(year, args.dry_run)

    elapsed = time.time() - t_start
    if _driver:
        _driver.close()
    print(f"\nDone in {elapsed:.0f}s.", flush=True)


if __name__ == "__main__":
    main()
