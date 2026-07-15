"""
silver_lender_behavior_risk.py

Reads HMDA bronze LAR + Panel files directly and produces LenderBehaviorRisk nodes.
Output parquet contains ONLY the 22 spec-defined attributes — nothing else.

Group-by key: county_code (5-digit FIPS) used as msamd proxy, because the
Combined Modified LAR (2022+) omits an explicit MSA/MD field. Set cbsa_code
by supplying an OMB delineation crosswalk at bronze_files/omb/cbsa_crosswalk.csv
(columns: county_fips, cbsa_code). If absent, cbsa_code is left null.

LAR column positions (Combined Modified LAR 2022+, empirically verified):
  [00] activity_year   [01] lei            [02] action_taken
  [07] loan_amount     [08] purchaser_type  [10] county_code
  [13] applicant_ethnicity_1               [24] applicant_race_1
  [73] total_units (5-24 / 25-49 / ... = multifamily)
  [82] denial_reason_1 candidate

Usage:
    python scripts/silver_lender_behavior_risk.py --year 2024
    python scripts/silver_lender_behavior_risk.py --year 2023 2024
"""

from __future__ import annotations

import argparse
import zipfile
from collections import defaultdict
from io import TextIOWrapper
from pathlib import Path
from statistics import mode as _mode

import numpy as np
import pandas as pd

BRONZE_ROOT = Path(__file__).resolve().parents[2] / "bronze_files" / "hmda"
SILVER_ROOT = Path(__file__).resolve().parents[2] / "silver_files" / "hmda"
OMB_CROSSWALK = Path(__file__).resolve().parents[2] / "bronze_files" / "omb" / "cbsa_crosswalk.csv"

MODIFIED_LAR_SINCE = 2022

# ── LAR field positions (0-indexed, Combined Modified LAR 2022+) ──────────────
_P_YEAR   = 0
_P_LEI    = 1
_P_AT     = 2   # action_taken
_P_AMT    = 7   # loan_amount (dollars)
_P_PTYPE  = 8   # purchaser_type
_P_COUNTY = 10  # county_code (5-digit FIPS — used as msamd)
_P_ETH1   = 13  # applicant_ethnicity_1
_P_RACE1  = 24  # applicant_race_1
_P_UNITS  = 73  # total_units
_P_DR1    = 82  # denial_reason_1 candidate

# action_taken codes
_ORIG  = "1"
_DENY  = "3"

# purchaser_type codes for GSE (Fannie=1, Freddie=3)
_GSE = {"1", "3"}

# total_units codes that indicate multifamily (5+ units)
_MF_UNITS = {"5-24", "25-49", "50-99", "100-149", ">149"}

# applicant_race_1 minority codes (AI/AN, Asian, Black, NH/OPI)
_MINORITY_RACE = {"1", "2", "3", "4"}
_WHITE_RACE    = "5"
_NOT_HISPANIC  = "2"   # applicant_ethnicity_1

# denial_reason values meaning "not applicable / exempt"
_DR_NA = {"", "NA", "10", "1111"}

# risk tier thresholds for fair_lending_risk_score
_TIERS = [(0.0, 0.75, "low"), (0.75, 1.0, "moderate"),
          (1.0, 1.5, "elevated"), (1.5, float("inf"), "high")]


# ── helpers ───────────────────────────────────────────────────────────────────

def _open_zip_or_txt(zip_path: Path, txt_path: Path):
    """Open the pipe-delimited file, preferring extracted txt over zip."""
    if txt_path.exists():
        return open(txt_path, encoding="utf-8", errors="replace"), None
    zf = zipfile.ZipFile(zip_path)
    inner = next(n for n in zf.namelist() if not n.startswith((".", "__")))
    return TextIOWrapper(zf.open(inner), encoding="utf-8", errors="replace"), zf


def _risk_tier(score) -> str:
    if score is None or (isinstance(score, float) and np.isnan(score)):
        return "unknown"
    for lo, hi, label in _TIERS:
        if lo <= score < hi:
            return label
    return "high"


def _safe_div(num, denom):
    if num is None or denom is None or denom == 0:
        return None
    return float(num) / float(denom)


def _top_mode(values: list) -> str | None:
    clean = [v for v in values if v not in _DR_NA]
    if not clean:
        return None
    try:
        return _mode(clean)
    except Exception:
        from collections import Counter
        return Counter(clean).most_common(1)[0][0]


# ── Panel reader ──────────────────────────────────────────────────────────────

def _load_panel(year: int) -> dict[str, str]:
    """Return {lei: topholder_rssd} from bronze Panel file."""
    zip_p = BRONZE_ROOT / str(year) / f"panel_{year}.zip"
    txt_p = BRONZE_ROOT / str(year) / f"{year}_public_panel_pipe.txt"
    if not zip_p.exists() and not txt_p.exists():
        print(f"  Panel not found for {year} — lender_count will be null")
        return {}
    fobj, zf = _open_zip_or_txt(zip_p, txt_p)
    mapping: dict[str, str] = {}
    try:
        header = fobj.readline()          # Panel has a header row
        for line in fobj:
            parts = line.strip().split("|")
            if len(parts) < 14:
                continue
            lei, topholder = parts[1].strip(), parts[13].strip()
            if lei and topholder:
                mapping[lei] = topholder
    finally:
        fobj.close()
        if zf:
            zf.close()
    print(f"  Panel loaded: {len(mapping):,} LEI entries")
    return mapping


# ── OMB crosswalk ─────────────────────────────────────────────────────────────

def _load_crosswalk() -> dict[str, str]:
    """Return {county_fips: cbsa_code} if the crosswalk CSV exists, else {}."""
    if not OMB_CROSSWALK.exists():
        return {}
    df = pd.read_csv(OMB_CROSSWALK, dtype=str)
    df.columns = df.columns.str.strip().str.lower()
    county_col = next((c for c in df.columns if "county" in c), None)
    cbsa_col   = next((c for c in df.columns if "cbsa" in c), None)
    if not county_col or not cbsa_col:
        return {}
    return dict(zip(df[county_col].str.zfill(5), df[cbsa_col]))


# ── accumulator ───────────────────────────────────────────────────────────────

def _new_bucket():
    return {
        "apps": 0, "orig": 0, "denied": 0,
        "mf_apps": 0, "mf_denied": 0,
        "loan_sum": 0.0, "loan_cnt": 0,
        "gse_cnt": 0, "orig_cnt": 0,
        "min_apps": 0, "min_denied": 0,
        "wnh_apps": 0, "wnh_denied": 0,
        "leis": set(),
        "denial_reasons": [],
    }


def _ingest_chunk(lines: list[str], acc: dict, panel: dict[str, str]) -> None:
    """Process one chunk of raw LAR lines into the accumulator."""
    for line in lines:
        p = line.rstrip("\n").split("|")
        if len(p) < 85:
            continue

        at     = p[_P_AT].strip()
        if at not in (_ORIG, _DENY):
            continue                  # only keep originated + denied

        county = p[_P_COUNTY].strip()
        year   = p[_P_YEAR].strip()
        key    = (county, year)
        b      = acc[key]

        lei    = p[_P_LEI].strip()
        race1  = p[_P_RACE1].strip()
        eth1   = p[_P_ETH1].strip()
        units  = p[_P_UNITS].strip()

        b["apps"] += 1
        if at == _ORIG:
            b["orig"] += 1
        else:
            b["denied"] += 1
            dr = p[_P_DR1].strip()
            if dr not in _DR_NA:
                b["denial_reasons"].append(dr)

        # multifamily
        is_mf = units in _MF_UNITS
        if is_mf:
            b["mf_apps"] += 1
            if at == _DENY:
                b["mf_denied"] += 1

        # loan amount
        try:
            amt = float(p[_P_AMT].strip())
            b["loan_sum"] += amt
            b["loan_cnt"] += 1
        except (ValueError, IndexError):
            pass

        # GSE sold (originated rows only)
        if at == _ORIG:
            b["orig_cnt"] += 1
            if p[_P_PTYPE].strip() in _GSE:
                b["gse_cnt"] += 1

        # fair-lending demographics
        is_minority = race1 in _MINORITY_RACE or eth1 == "1"
        is_white_nh = race1 == _WHITE_RACE and eth1 == _NOT_HISPANIC

        if is_minority:
            b["min_apps"] += 1
            if at == _DENY:
                b["min_denied"] += 1
        if is_white_nh:
            b["wnh_apps"] += 1
            if at == _DENY:
                b["wnh_denied"] += 1

        # lender tracking (for lender_count)
        if lei and panel:
            rssd = panel.get(lei)
            if rssd:
                b["leis"].add(rssd)


# ── finalise accumulator into output rows ─────────────────────────────────────

def _finalise(acc: dict, crosswalk: dict[str, str]) -> pd.DataFrame:
    rows = []
    for (county, year_val), b in acc.items():
        tot_denom = b["orig"] + b["denied"]
        denial_overall = _safe_div(b["denied"], tot_denom)

        dr_minority = _safe_div(b["min_denied"], b["min_apps"])
        dr_white    = _safe_div(b["wnh_denied"], b["wnh_apps"])
        dr_mf       = _safe_div(b["mf_denied"],  b["mf_apps"])

        disp = _safe_div(dr_minority, dr_white)

        if disp is not None and dr_mf is not None and denial_overall is not None:
            score = 0.50 * disp + 0.30 * denial_overall + 0.20 * dr_mf
        elif disp is not None and denial_overall is not None:
            score = 0.625 * disp + 0.375 * denial_overall
        else:
            score = None

        rows.append({
            "risk_id":                     f"{county}_{year_val}",
            "msa_code":                    county,
            "assessment_year":             int(year_val),  # Fix 3: integer not string
            "total_applications":          b["apps"],
            "total_originated":            b["orig"],
            "total_denied":                b["denied"],
            "multifamily_applications":    b["mf_apps"],
            "multifamily_denied":          b["mf_denied"],
            "lender_count":                len(b["leis"]) or None,
            "avg_loan_amount":             (b["loan_sum"] / b["loan_cnt"]) if b["loan_cnt"] else None,
            "gse_sold_pct":                _safe_div(b["gse_cnt"], b["orig_cnt"]),
            "denial_rate_overall":         denial_overall,
            "denial_rate_minority":        dr_minority,
            "denial_rate_white":           dr_white,
            "multifamily_denial_rate":     dr_mf,
            "denial_rate_disparity_ratio": disp,
            "fair_lending_risk_score":     score,
            "risk_tier":                   _risk_tier(score),
            "top_denial_reason":           _top_mode(b["denial_reasons"]),
            "is_high_disparity":           bool(disp and disp > 2.0),
            "is_multifamily_constrained":  None,   # filled in post-process below
            "cbsa_code":                   crosswalk.get(county),
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # is_multifamily_constrained: rate > national_avg + 1 std across all counties
    rates = pd.to_numeric(df["multifamily_denial_rate"], errors="coerce")
    threshold = rates.mean() + rates.std()
    df["is_multifamily_constrained"] = (rates > threshold).fillna(False)

    # Fix 5: deduplicate to one row per (cbsa_code, assessment_year).
    # Metro areas with multiple Metropolitan Divisions would otherwise produce
    # multiple LenderBehaviorRisk nodes per MetroArea per year.
    cbsa_rows_before = df["cbsa_code"].notna().sum()
    df_metro = df[df["cbsa_code"].notna()].copy()
    df_null  = df[df["cbsa_code"].isna()].copy()
    if not df_metro.empty:
        df_metro = (
            df_metro
            .sort_values("total_applications", ascending=False)
            .groupby(["cbsa_code", "assessment_year"], as_index=False)
            .first()
        )
        # Rebuild risk_id keyed on cbsa_code for deduplicated rows
        df_metro["risk_id"] = df_metro["cbsa_code"] + "_" + df_metro["assessment_year"].astype(str)
        saved = cbsa_rows_before - len(df_metro)
        if saved:
            print(f"  Fix 5: collapsed {saved} county-level rows to {len(df_metro)} CBSA-level rows")
    df = pd.concat([df_metro, df_null], ignore_index=True)

    # Enforce bool dtype for boolean fields
    for bcol in ("is_high_disparity", "is_multifamily_constrained"):
        if bcol in df.columns:
            df[bcol] = df[bcol].fillna(False).astype(bool)

    return df


# ── LAR stream ────────────────────────────────────────────────────────────────

def _stream_lar(year: int, acc: dict, panel: dict[str, str], chunk: int = 200_000) -> None:
    if year >= MODIFIED_LAR_SINCE:
        zip_p = BRONZE_ROOT / str(year) / f"lar_{year}.zip"
        txt_p = BRONZE_ROOT / str(year) / f"{year}_combined_mlar.txt"
    else:
        zip_p = BRONZE_ROOT / str(year) / f"lar_{year}.zip"
        txt_p = BRONZE_ROOT / str(year) / f"{year}_public_lar_pipe.txt"

    if not zip_p.exists() and not txt_p.exists():
        raise FileNotFoundError(f"No LAR file for {year} in {BRONZE_ROOT / str(year)}")

    fobj, zf = _open_zip_or_txt(zip_p, txt_p)
    total = 0
    buf: list[str] = []
    try:
        for line in fobj:
            buf.append(line)
            if len(buf) >= chunk:
                _ingest_chunk(buf, acc, panel)
                total += len(buf)
                buf = []
                print(f"  ... {total:,} rows", end="\r")
        if buf:
            _ingest_chunk(buf, acc, panel)
            total += len(buf)
    finally:
        fobj.close()
        if zf:
            zf.close()
    print(f"  {total:,} rows read from LAR")


# ── entry point ───────────────────────────────────────────────────────────────

def process_year(year: int) -> None:
    silver = SILVER_ROOT / str(year)
    silver.mkdir(parents=True, exist_ok=True)
    out = silver / f"silver_lender_behavior_risk_{year}.parquet"

    print(f"\n=== LenderBehaviorRisk {year} ===")

    panel     = _load_panel(year)
    crosswalk = _load_crosswalk()
    if crosswalk:
        print(f"  OMB crosswalk loaded: {len(crosswalk):,} county mappings")
    else:
        print("  OMB crosswalk not found — cbsa_code will be null")

    acc: dict = defaultdict(_new_bucket)
    _stream_lar(year, acc, panel)

    print(f"  Finalising {len(acc):,} county-year groups...")
    df = _finalise(acc, crosswalk)

    if df.empty:
        print("  No data — nothing written")
        return

    df.to_parquet(out, index=False, engine="pyarrow")
    print(f"  {len(df):,} rows -> {out.name}")
    print(f"  Columns ({len(df.columns)}): {list(df.columns)}")
    scored = df["fair_lending_risk_score"].notna().sum()
    print(f"  Risk tier distribution ({scored} scored):")
    for tier, cnt in df["risk_tier"].value_counts().items():
        print(f"    {tier}: {cnt}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Build LenderBehaviorRisk nodes from bronze HMDA LAR.")
    ap.add_argument("--year", type=int, nargs="+", required=True)
    args = ap.parse_args()
    for yr in args.year:
        process_year(yr)
    print("\nDone.")


if __name__ == "__main__":
    main()
