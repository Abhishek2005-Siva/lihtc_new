"""
full_reingest.py — Wipe everything and rebuild from scratch.

Steps:
  1. Wipe Neo4j graph (all nodes + relationships + constraints)
  2. Delete graph_state.db (so ingest treats everything as new)
  3. Delete all silver parquet files
  4. Re-run silver transforms: QCT/DDA (2003-2025), AMI (2010-2025), LBR (2018-2025)
  5. Bootstrap geographic nodes (steps 0-8)
  6. Ingest all datasets into Neo4j

Usage:
    python scripts/full_reingest.py
    python scripts/full_reingest.py --skip-silver   # if silver is already regenerated
    python scripts/full_reingest.py --dry-run       # print plan only
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SILVER = ROOT / "silver"
STATE_DB = ROOT / "graph_state.db"

NEO4J_URI      = os.getenv("NEO4J_URI", "neo4j://127.0.0.1:7687")
NEO4J_USER     = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")

PY = sys.executable


def _hdr(msg: str) -> None:
    print(f"\n{'='*60}\n  {msg}\n{'='*60}", flush=True)


def _run(cmd: list[str], label: str) -> None:
    print(f"\n>>> {label}", flush=True)
    print(f"    {' '.join(cmd)}", flush=True)
    t0 = time.time()
    result = subprocess.run(cmd, cwd=str(ROOT))
    elapsed = time.time() - t0
    if result.returncode != 0:
        print(f"  FAILED ({elapsed:.0f}s) — exit code {result.returncode}", flush=True)
        raise SystemExit(f"Step failed: {label}")
    print(f"  OK ({elapsed:.0f}s)", flush=True)


def step1_wipe_neo4j(dry_run: bool) -> None:
    _hdr("Step 1 — Wipe Neo4j")
    if dry_run:
        print("  DRY-RUN: would delete all nodes, relationships, and constraints")
        return

    from neo4j import GraphDatabase

    # Large socket timeout so server-side bulk deletes don't disconnect us
    driver = GraphDatabase.driver(
        NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD),
        connection_timeout=300,
        max_transaction_retry_time=600,
    )
    try:
        driver.verify_connectivity()
        print(f"  Connected to {NEO4J_URI}", flush=True)
    except Exception as e:
        raise SystemExit(f"Neo4j connection failed: {e}")

    with driver.session() as s:
        # Drop all constraints first
        constraints = s.run("SHOW CONSTRAINTS").data()
        print(f"  Dropping {len(constraints)} constraints...", flush=True)
        for c in constraints:
            name = c.get("name")
            if name:
                s.run(f"DROP CONSTRAINT {name} IF EXISTS")

        # Drop non-LOOKUP indexes
        indexes = s.run("SHOW INDEXES").data()
        droppable = [i for i in indexes if i.get("type") not in ("LOOKUP",)]
        print(f"  Dropping {len(droppable)} indexes...", flush=True)
        for idx in droppable:
            name = idx.get("name")
            if name:
                try:
                    s.run(f"DROP INDEX {name} IF EXISTS")
                except Exception:
                    pass

        # Delete all nodes server-side using CALL {} IN TRANSACTIONS
        # (avoids client timeout — runs entirely on the server in small chunks)
        print("  Deleting all nodes and relationships (server-side batches)...", flush=True)
        s.run(
            "MATCH (n) "
            "CALL { WITH n DETACH DELETE n } "
            "IN TRANSACTIONS OF 500 ROWS"
        )
        remaining = s.run("MATCH (n) RETURN count(n) AS cnt").single()["cnt"]
        print(f"  Nodes remaining after wipe: {remaining}", flush=True)

    driver.close()
    print("  Neo4j wiped.", flush=True)


def step2_delete_state_db(dry_run: bool) -> None:
    _hdr("Step 2 — Delete graph_state.db")
    if dry_run:
        print(f"  DRY-RUN: would delete {STATE_DB}")
        return
    if STATE_DB.exists():
        STATE_DB.unlink()
        print(f"  Deleted {STATE_DB}", flush=True)
    else:
        print("  graph_state.db not found — nothing to delete", flush=True)


def step3_delete_silver(dry_run: bool) -> None:
    _hdr("Step 3 — Delete silver parquets")
    parquets = list(SILVER.rglob("*.parquet"))
    print(f"  Found {len(parquets)} parquet files to delete", flush=True)
    if dry_run:
        for p in parquets[:10]:
            print(f"    {p.relative_to(ROOT)}")
        if len(parquets) > 10:
            print(f"    ... and {len(parquets)-10} more")
        return
    for p in parquets:
        p.unlink()
    print(f"  Deleted {len(parquets)} parquet files", flush=True)


def step4_silver_transforms(dry_run: bool) -> None:
    _hdr("Step 4 — Silver transforms")

    silver_qct_dda = str(ROOT / "scripts" / "silver_transform" / "silver_qct_dda.py")
    silver_ami     = str(ROOT / "scripts" / "silver_transform" / "silver_ami.py")
    silver_lbr     = str(ROOT / "scripts" / "silver_transform" / "silver_lender_behavior_risk.py")

    if dry_run:
        print("  DRY-RUN: would run:")
        print(f"    QCT+DDA 2003-2025")
        print(f"    AMI     2010-2025")
        print(f"    LBR     2018-2025")
        return

    # QCT + DDA (single script, both datasets)
    _run([PY, silver_qct_dda, "--dataset", "all", "--year-range", "2003", "2025"],
         "Silver QCT+DDA 2003-2025")

    # AMI (all years)
    ami_years = [str(y) for y in range(2010, 2026)]
    _run([PY, silver_ami, "--year"] + ami_years, "Silver AMI 2010-2025")

    # LBR / HMDA (2018-2025)
    lbr_years = [str(y) for y in range(2018, 2026)]
    _run([PY, silver_lbr, "--year"] + lbr_years, "Silver LBR 2018-2025")


def step5_bootstrap(dry_run: bool) -> None:
    _hdr("Step 5 — Bootstrap geography (steps 0-8)")
    bootstrap = str(ROOT / "scripts" / "bootstrap" / "bootstrap_geography.py")
    if dry_run:
        print("  DRY-RUN: would run bootstrap_geography.py (all steps)")
        return
    _run([PY, bootstrap], "Bootstrap geography steps 0-8")


def step6_ingest(dry_run: bool) -> None:
    _hdr("Step 6 — Ingest all datasets into Neo4j")
    ingest = str(ROOT / "scripts" / "neo4j_ingest" / "ingest_datasets.py")
    flags = ["--dataset", "qct", "dda", "ami", "hmda"]
    if dry_run:
        flags.append("--dry-run")
    _run([PY, ingest] + flags, "Ingest QCT + DDA + AMI + HMDA")


def main() -> None:
    p = argparse.ArgumentParser(description="Full Neo4j wipe and re-ingest pipeline.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print what would happen without doing anything")
    p.add_argument("--skip-silver", action="store_true",
                   help="Skip silver regeneration (steps 3-4); use existing parquets")
    p.add_argument("--start-step", type=int, default=1, metavar="N",
                   help="Start from this step (1=wipe neo4j, 2=del state db, "
                        "3=del silver, 4=silver transforms, 5=bootstrap, 6=ingest)")
    args = p.parse_args()

    if args.dry_run:
        print("DRY-RUN MODE — no changes will be made\n")

    t0 = time.time()

    if args.start_step <= 1:
        step1_wipe_neo4j(args.dry_run)
    if args.start_step <= 2:
        step2_delete_state_db(args.dry_run)
    if not args.skip_silver:
        if args.start_step <= 3:
            step3_delete_silver(args.dry_run)
        if args.start_step <= 4:
            step4_silver_transforms(args.dry_run)
    if args.start_step <= 5:
        step5_bootstrap(args.dry_run)
    if args.start_step <= 6:
        step6_ingest(args.dry_run)

    elapsed = time.time() - t0
    _hdr(f"DONE — total time {elapsed/60:.1f} min")


if __name__ == "__main__":
    main()
