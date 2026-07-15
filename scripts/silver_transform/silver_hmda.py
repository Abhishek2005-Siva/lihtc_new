"""
silver_hmda.py

Reads HMDA bronze files (LAR, Panel, Transmittal Sheet) and produces:
  - silver_hmda_{year}.xlsx                        — LAR sample, Panel, TS sheets
  - silver_hmda_{year}_lar.parquet                 — full LAR (millions of rows)
  - silver_lender_behavior_risk_{year}.parquet     — 22-column LenderBehaviorRisk nodes

All bronze files are pipe-delimited (|). LAR has no header row.
cbsa_code is resolved via bronze_files/omb/cbsa_crosswalk.csv (county_fips, cbsa_code)
if that file exists; otherwise left null.

Usage:
    python scripts/silver_hmda.py --year 2024
    python scripts/silver_hmda.py --year 2023 2024 --lar-sample 50000
    python scripts/silver_hmda.py --year 2023 --skip-lar        # Panel + TS only
    python scripts/silver_hmda.py --year 2024 --skip-risk        # skip LenderBehaviorRisk
"""

from __future__ import annotations

import argparse
import zipfile
from collections import defaultdict
from io import TextIOWrapper
from pathlib import Path
from statistics import mode as _stat_mode

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

BRONZE_ROOT  = Path(__file__).resolve().parents[2] / "bronze_files" / "hmda"
SILVER_ROOT  = Path(__file__).resolve().parents[2] / "silver" / "hmda"
OMB_CROSSWALK = Path(__file__).resolve().parents[2] / "bronze_files" / "omb" / "cbsa_crosswalk.csv"

MODIFIED_LAR_SINCE = 2022

# ── Combined Modified LAR schema (2022+, empirically verified) ────────────────
MLAR_COLUMNS = [
    "activity_year",              # [00]
    "lei",                        # [01]
    "action_taken",               # [02] 1=originated 3=denied
    "loan_purpose",               # [03]
    "loan_type",                  # [04]
    "preapproval",                # [05]
    "lien_status",                # [06]
    "loan_amount",                # [07] whole dollars
    "purchaser_type",             # [08] 1=Fannie 3=Freddie
    "state_code",                 # [09]
    "county_code",                # [10] 5-digit FIPS
    "census_tract",               # [11]
    "col_12",                     # [12]
    "applicant_ethnicity_1",      # [13]
    "applicant_ethnicity_2",      # [14]
    "applicant_ethnicity_3",      # [15]
    "applicant_ethnicity_4",      # [16]
    "co_applicant_ethnicity_1",   # [17]
    "co_applicant_ethnicity_2",   # [18]
    "co_applicant_ethnicity_3",   # [19]
    "co_applicant_ethnicity_4",   # [20]
    "applicant_ethnicity_5",      # [21]
    "applicant_ethnicity_observed",    # [22]
    "co_applicant_ethnicity_observed", # [23]
    "applicant_race_1",           # [24]
    "applicant_race_2",           # [25]
    "applicant_race_3",           # [26]
    "applicant_race_4",           # [27]
    "applicant_race_5",           # [28]
    "co_applicant_race_1",        # [29]
    "co_applicant_race_2",        # [30]
    "co_applicant_race_3",        # [31]
    "co_applicant_race_4",        # [32]
    "co_applicant_race_5",        # [33]
    "applicant_race_observed",    # [34]
    "co_applicant_race_observed", # [35]
    "applicant_sex",              # [36]
    "co_applicant_sex",           # [37]
    "applicant_sex_observed",     # [38]
    "co_applicant_sex_observed",  # [39]
    "applicant_age",              # [40]
    "applicant_age_above_62",     # [41]
    "co_applicant_age",           # [42]
    "co_applicant_age_above_62",  # [43]
    "income",                     # [44]
    "col_45",                     # [45]
    "rate_spread",                # [46]
    "hoepa_status",               # [47]
    "applicant_credit_score_type",    # [48]
    "co_applicant_credit_score_type", # [49]
    "col_50",  "col_51", "col_52", "col_53", "col_54",  # [50-54]
    "total_loan_costs",           # [55]
    "total_points_and_fees",      # [56]
    "origination_charges",        # [57]
    "discount_points",            # [58]
    "lender_credits",             # [59]
    "interest_rate",              # [60]
    "col_61",                     # [61]
    "debt_to_income_ratio",       # [62]
    "combined_loan_to_value_ratio", # [63]
    "loan_term",                  # [64]
    "prepayment_penalty_term",    # [65]
    "negative_amortization",      # [66]
    "interest_only_payment",      # [67]
    "balloon_payment",            # [68]
    "other_nonamortizing_features", # [69]
    "property_value",             # [70]
    "manufactured_home_secured_property_type",  # [71]
    "manufactured_home_land_property_interest", # [72]
    "total_units",                # [73] 5-24/25-49/... = multifamily
    "multifamily_affordable_units", # [74]
    "submission_of_application",  # [75]
    "initially_payable_to_institution", # [76]
    "aus_1", "aus_2", "aus_3", "aus_4", "aus_5",  # [77-81]
    "col_82", "col_83", "col_84",  # [82-84]
]

SNAPSHOT_LAR_COLUMNS = [
    "activity_year", "lei",
    "derived_msa_md", "state_code", "county_code", "census_tract",
    "derived_loan_product_type", "derived_dwelling_category", "conforming_loan_limit",
    "derived_ethnicity", "derived_race", "derived_sex",
    "action_taken", "purchaser_type", "preapproval", "loan_type", "loan_purpose",
    "lien_status", "reverse_mortgage", "open_end_line_of_credit",
    "business_or_commercial_purpose", "loan_amount", "combined_loan_to_value_ratio",
    "interest_rate", "rate_spread", "hoepa_status",
    "total_loan_costs", "total_points_and_fees", "origination_charges",
    "discount_points", "lender_credits", "loan_term", "prepayment_penalty_term",
    "intro_rate_period", "negative_amortization", "interest_only_payment",
    "balloon_payment", "other_nonamortizing_features", "property_value",
    "construction_method", "occupancy_type",
    "manufactured_home_secured_property_type", "manufactured_home_land_property_interest",
    "total_units", "applicant_age", "multifamily_affordable_units",
    "income", "debt_to_income_ratio",
    "applicant_credit_score_type", "co_applicant_credit_score_type",
    "applicant_ethnicity_1", "applicant_ethnicity_2", "applicant_ethnicity_3",
    "applicant_ethnicity_4", "applicant_ethnicity_5",
    "co_applicant_ethnicity_1", "co_applicant_ethnicity_2",
    "co_applicant_ethnicity_3", "co_applicant_ethnicity_4",
    "co_applicant_ethnicity_5", "applicant_ethnicity_observed",
    "co_applicant_ethnicity_observed",
    "applicant_race_1", "applicant_race_2", "applicant_race_3",
    "applicant_race_4", "applicant_race_5",
    "co_applicant_race_1", "co_applicant_race_2", "co_applicant_race_3",
    "co_applicant_race_4", "co_applicant_race_5",
    "applicant_race_observed", "co_applicant_race_observed",
    "applicant_sex", "co_applicant_sex",
    "applicant_sex_observed", "co_applicant_sex_observed",
    "applicant_age_above_62", "co_applicant_age", "co_applicant_age_above_62",
    "submission_of_application", "initially_payable_to_institution",
    "aus_1", "aus_2", "aus_3", "aus_4", "aus_5",
    "denial_reason_1", "denial_reason_2", "denial_reason_3", "denial_reason_4",
    "tract_population", "tract_minority_population_percent",
    "ffiec_msa_md_median_family_income", "tract_to_msa_income_percentage",
    "tract_owner_occupied_units", "tract_one_to_four_family_homes",
    "tract_median_age_of_housing_units",
]

LAR_COLUMNS = MLAR_COLUMNS


# ═══════════════════════════════════════════════════════════════════════════════
# Shared file helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _open_pipe_file(zip_path: Path, txt_path: Path):
    """Return (fileobj, needs_close, zip_handle_or_None) for a bronze pipe file."""
    if txt_path.exists():
        return open(txt_path, encoding="utf-8", errors="replace"), True, None
    if not zip_path.exists():
        raise FileNotFoundError(
            f"Neither {txt_path.name} nor {zip_path.name} found in {zip_path.parent}"
        )
    zf = zipfile.ZipFile(zip_path)
    inner = next(
        (n for n in zf.namelist() if not n.startswith("__") and not n.startswith(".")),
        None,
    )
    if inner is None:
        zf.close()
        raise ValueError(f"No usable entry in {zip_path.name}")
    return TextIOWrapper(zf.open(inner), encoding="utf-8", errors="replace"), False, zf


def _lar_paths(year: int, bronze: Path):
    if year >= MODIFIED_LAR_SINCE:
        return bronze / f"lar_{year}.zip", bronze / f"{year}_combined_mlar.txt"
    return bronze / f"lar_{year}.zip", bronze / f"{year}_public_lar_pipe.txt"


# ═══════════════════════════════════════════════════════════════════════════════
# LAR -> full Parquet (silver_hmda_{year}_lar.parquet)
# ═══════════════════════════════════════════════════════════════════════════════

def _lar_schema(year: int) -> list[str]:
    return MLAR_COLUMNS if year >= MODIFIED_LAR_SINCE else SNAPSHOT_LAR_COLUMNS


def _reconcile_columns(actual_count: int, schema: list[str]) -> list[str]:
    base = schema[:]
    if actual_count <= len(base):
        return base[:actual_count]
    return base + [f"extra_{i}" for i in range(1, actual_count - len(base) + 1)]


def _iter_lar_chunks(zip_path: Path, txt_path: Path, chunk_size: int, year: int = 0):
    """Yield DataFrames of chunk_size rows. LAR has NO header row."""
    schema = _lar_schema(year)
    fobj, needs_close, zf = _open_pipe_file(zip_path, txt_path)
    try:
        first_line = fobj.readline()
        actual_cols = len(first_line.strip().split("|"))
        if actual_cols != len(schema):
            print(f"  WARN: file has {actual_cols} fields, schema defines {len(schema)}")
        names = _reconcile_columns(actual_cols, schema)
    finally:
        if needs_close:
            fobj.close()
        if zf:
            zf.close()

    fobj, needs_close, zf = _open_pipe_file(zip_path, txt_path)
    try:
        reader = pd.read_csv(
            fobj, sep="|", header=None, names=names,
            dtype=str, low_memory=False, chunksize=chunk_size,
        )
        for chunk in reader:
            yield chunk
    finally:
        if needs_close:
            fobj.close()
        if zf:
            zf.close()


def _read_panel_or_ts(zip_path: Path, txt_path: Path) -> pd.DataFrame:
    fobj, needs_close, zf = _open_pipe_file(zip_path, txt_path)
    try:
        df = pd.read_csv(fobj, sep="|", dtype=str, low_memory=False)
    finally:
        if needs_close:
            fobj.close()
        if zf:
            zf.close()
    df.columns = df.columns.str.strip()
    return df


def _read_lar_sample(zip_path: Path, txt_path: Path, nrows: int, year: int = 0) -> pd.DataFrame:
    chunks, collected = [], 0
    for chunk in _iter_lar_chunks(zip_path, txt_path, chunk_size=min(nrows, 100_000), year=year):
        chunks.append(chunk.head(nrows - collected))
        collected += len(chunk)
        if collected >= nrows:
            break
    return pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()


# ═══════════════════════════════════════════════════════════════════════════════
# LenderBehaviorRisk (22-column aggregated parquet)
# ═══════════════════════════════════════════════════════════════════════════════

# LAR field positions (0-indexed, Combined Modified LAR 2022+)
_P_YEAR   = 0
_P_LEI    = 1
_P_AT     = 2   # action_taken
_P_AMT    = 7   # loan_amount (dollars)
_P_PTYPE  = 8   # purchaser_type
_P_COUNTY = 10  # county_code (5-digit FIPS — used as msamd proxy)
_P_ETH1   = 13  # applicant_ethnicity_1
_P_RACE1  = 24  # applicant_race_1
_P_UNITS  = 73  # total_units
_P_DR1    = 82  # denial_reason_1 candidate

_ORIG = "1"
_DENY = "3"
_GSE  = {"1", "3"}
_MF_UNITS      = {"5-24", "25-49", "50-99", "100-149", ">149"}
_MINORITY_RACE = {"1", "2", "3", "4"}
_WHITE_RACE    = "5"
_NOT_HISPANIC  = "2"
_DR_NA         = {"", "NA", "10", "1111"}
_TIERS = [(0.0, 0.75, "low"), (0.75, 1.0, "moderate"),
          (1.0, 1.5, "elevated"), (1.5, float("inf"), "high")]


def _safe_div(num, denom):
    if num is None or denom is None or denom == 0:
        return None
    return float(num) / float(denom)


def _risk_tier(score) -> str:
    if score is None or (isinstance(score, float) and np.isnan(score)):
        return "unknown"
    for lo, hi, label in _TIERS:
        if lo <= score < hi:
            return label
    return "high"


def _top_mode(values: list):
    clean = [v for v in values if v not in _DR_NA]
    if not clean:
        return None
    try:
        return _stat_mode(clean)
    except Exception:
        from collections import Counter
        return Counter(clean).most_common(1)[0][0]


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


def _load_panel_map(year: int, bronze: Path) -> dict[str, str]:
    """Return {lei: topholder_rssd} for lender_count, or {} if Panel missing."""
    zip_p = bronze / f"panel_{year}.zip"
    txt_p = bronze / f"{year}_public_panel_pipe.txt"
    if not zip_p.exists() and not txt_p.exists():
        print(f"  Panel not found for {year} — lender_count will be null")
        return {}
    fobj, needs_close, zf = _open_pipe_file(zip_p, txt_p)
    mapping: dict[str, str] = {}
    try:
        fobj.readline()  # skip header
        for line in fobj:
            parts = line.strip().split("|")
            if len(parts) < 14:
                continue
            lei, rssd = parts[1].strip(), parts[13].strip()
            if lei and rssd:
                mapping[lei] = rssd
    finally:
        if needs_close:
            fobj.close()
        if zf:
            zf.close()
    print(f"  Panel loaded: {len(mapping):,} LEI entries")
    return mapping


def _load_crosswalk() -> dict[str, str]:
    if not OMB_CROSSWALK.exists():
        return {}
    df = pd.read_csv(OMB_CROSSWALK, dtype=str)
    df.columns = df.columns.str.strip().str.lower()
    county_col = next((c for c in df.columns if "county" in c), None)
    cbsa_col   = next((c for c in df.columns if "cbsa" in c), None)
    if not county_col or not cbsa_col:
        return {}
    return dict(zip(df[county_col].str.zfill(5), df[cbsa_col]))


def _ingest_risk_lines(lines: list[str], acc: dict, panel: dict[str, str]) -> None:
    for line in lines:
        p = line.rstrip("\n").split("|")
        if len(p) < 85:
            continue
        at = p[_P_AT].strip()
        if at not in (_ORIG, _DENY):
            continue

        county = p[_P_COUNTY].strip()
        year   = p[_P_YEAR].strip()
        b      = acc[(county, year)]

        lei   = p[_P_LEI].strip()
        race1 = p[_P_RACE1].strip()
        eth1  = p[_P_ETH1].strip()
        units = p[_P_UNITS].strip()

        b["apps"] += 1
        if at == _ORIG:
            b["orig"] += 1
        else:
            b["denied"] += 1
            dr = p[_P_DR1].strip()
            if dr not in _DR_NA:
                b["denial_reasons"].append(dr)

        if units in _MF_UNITS:
            b["mf_apps"] += 1
            if at == _DENY:
                b["mf_denied"] += 1

        try:
            b["loan_sum"] += float(p[_P_AMT].strip())
            b["loan_cnt"] += 1
        except (ValueError, IndexError):
            pass

        if at == _ORIG:
            b["orig_cnt"] += 1
            if p[_P_PTYPE].strip() in _GSE:
                b["gse_cnt"] += 1

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

        if lei and panel:
            rssd = panel.get(lei)
            if rssd:
                b["leis"].add(rssd)


def _finalise_risk(acc: dict, crosswalk: dict[str, str]) -> pd.DataFrame:
    rows = []
    for (county, year_val), b in acc.items():
        denial_overall = _safe_div(b["denied"], b["orig"] + b["denied"])
        dr_minority    = _safe_div(b["min_denied"], b["min_apps"])
        dr_white       = _safe_div(b["wnh_denied"], b["wnh_apps"])
        dr_mf          = _safe_div(b["mf_denied"],  b["mf_apps"])
        disp           = _safe_div(dr_minority, dr_white)

        if disp is not None and dr_mf is not None and denial_overall is not None:
            score = 0.50 * disp + 0.30 * denial_overall + 0.20 * dr_mf
        elif disp is not None and denial_overall is not None:
            score = 0.625 * disp + 0.375 * denial_overall
        else:
            score = None

        rows.append({
            "risk_id":                     f"{county}_{year_val}",
            "msa_code":                    county,
            "assessment_year":             year_val,
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
            "is_multifamily_constrained":  None,
            "cbsa_code":                   crosswalk.get(county),
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    rates = pd.to_numeric(df["multifamily_denial_rate"], errors="coerce")
    df["is_multifamily_constrained"] = rates > (rates.mean() + rates.std())
    return df


def _build_risk(year: int, bronze: Path, silver: Path) -> None:
    out = silver / f"silver_lender_behavior_risk_{year}.parquet"
    print(f"  Building LenderBehaviorRisk...")

    panel     = _load_panel_map(year, bronze)
    crosswalk = _load_crosswalk()
    if not crosswalk:
        print("  OMB crosswalk not found — cbsa_code will be null")

    lar_zip, lar_txt = _lar_paths(year, bronze)
    if not lar_zip.exists() and not lar_txt.exists():
        print(f"  LAR not found — skipping LenderBehaviorRisk")
        return

    acc: dict = defaultdict(_new_bucket)
    fobj, needs_close, zf = _open_pipe_file(lar_zip, lar_txt)
    total, buf = 0, []
    try:
        for line in fobj:
            buf.append(line)
            if len(buf) >= 200_000:
                _ingest_risk_lines(buf, acc, panel)
                total += len(buf)
                buf = []
                print(f"    ... {total:,} rows", end="\r")
        if buf:
            _ingest_risk_lines(buf, acc, panel)
            total += len(buf)
    finally:
        if needs_close:
            fobj.close()
        if zf:
            zf.close()
    print(f"    {total:,} rows read")

    print(f"    Finalising {len(acc):,} county-year groups...")
    df = _finalise_risk(acc, crosswalk)
    if df.empty:
        print("    No data — nothing written")
        return

    df.to_parquet(out, index=False, engine="pyarrow")
    print(f"    {len(df):,} rows, {len(df.columns)} columns -> {out.name}")
    scored = df["fair_lending_risk_score"].notna().sum()
    print(f"    Risk tiers ({scored} scored): " +
          ", ".join(f"{t}={c}" for t, c in df["risk_tier"].value_counts().items()))


# ═══════════════════════════════════════════════════════════════════════════════
# Main processing
# ═══════════════════════════════════════════════════════════════════════════════

def process_year(year: int, lar_sample_rows: int, skip_lar: bool, skip_risk: bool) -> None:
    bronze = BRONZE_ROOT / str(year)
    silver = SILVER_ROOT / str(year)
    silver.mkdir(parents=True, exist_ok=True)

    print(f"\n=== HMDA Silver {year} ===")

    # Panel
    panel_df = None
    panel_zip = bronze / f"panel_{year}.zip"
    panel_txt = bronze / f"{year}_public_panel_pipe.txt"
    if panel_zip.exists() or panel_txt.exists():
        print("  Reading Panel...")
        panel_df = _read_panel_or_ts(panel_zip, panel_txt)
        print(f"    {len(panel_df):,} rows x {len(panel_df.columns)} cols")
    else:
        print("  Panel: not found — skipping")

    # Transmittal Sheet
    ts_df = None
    ts_zip = bronze / f"ts_{year}.zip"
    ts_txt = bronze / f"{year}_public_ts_pipe.txt"
    if ts_zip.exists() or ts_txt.exists():
        print("  Reading TS...")
        ts_df = _read_panel_or_ts(ts_zip, ts_txt)
        print(f"    {len(ts_df):,} rows x {len(ts_df.columns)} cols")
    else:
        print("  TS: not found — skipping")

    # LAR -> full Parquet + sample for Excel
    lar_sample_df = None
    if not skip_lar:
        lar_zip, lar_txt = _lar_paths(year, bronze)
        if lar_zip.exists() or lar_txt.exists():
            parquet_path = silver / f"silver_hmda_{year}_lar.parquet"
            print("  Reading LAR -> Parquet (chunked)...")
            total_rows, pq_writer = 0, None
            try:
                for chunk in _iter_lar_chunks(lar_zip, lar_txt, chunk_size=500_000, year=year):
                    if total_rows == 0:
                        lar_sample_df = chunk.head(lar_sample_rows).copy()
                    table = pa.Table.from_pandas(chunk, preserve_index=False)
                    if pq_writer is None:
                        pq_writer = pq.ParquetWriter(str(parquet_path), table.schema)
                    pq_writer.write_table(table)
                    total_rows += len(chunk)
                    print(f"    ... {total_rows:,} rows", end="\r")
            finally:
                if pq_writer:
                    pq_writer.close()
            print(f"    {total_rows:,} rows -> {parquet_path.name}")
            if lar_sample_df is None:
                lar_sample_df = pd.DataFrame()
        else:
            print("  LAR: not found — skipping")

    # Excel workbook
    xlsx_path = silver / f"silver_hmda_{year}.xlsx"
    if any(df is not None for df in [panel_df, ts_df, lar_sample_df]):
        print("  Writing Excel workbook...")
        with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
            if lar_sample_df is not None and len(lar_sample_df) > 0:
                sheet = f"LAR_sample_{lar_sample_rows // 1000}k"
                lar_sample_df.to_excel(writer, sheet_name=sheet, index=False)
                print(f"    Sheet '{sheet}': {len(lar_sample_df):,} rows")
            if panel_df is not None:
                panel_df.to_excel(writer, sheet_name="Panel", index=False)
                print(f"    Sheet 'Panel': {len(panel_df):,} rows")
            if ts_df is not None:
                ts_df.to_excel(writer, sheet_name="TS", index=False)
                print(f"    Sheet 'TS': {len(ts_df):,} rows")
        print(f"  -> {xlsx_path.name}")
    else:
        print(f"  No data found for {year}")

    # LenderBehaviorRisk
    if not skip_risk:
        _build_risk(year, bronze, silver)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build Silver HMDA files from bronze: LAR parquet, Excel, LenderBehaviorRisk parquet.",
    )
    p.add_argument("--year", type=int, nargs="+", required=True)
    p.add_argument("--lar-sample", type=int, default=10_000, metavar="N",
                   help="LAR rows in Excel sample sheet (default 10000)")
    p.add_argument("--skip-lar", action="store_true",
                   help="Skip full LAR parquet (Panel + TS + risk only)")
    p.add_argument("--skip-risk", action="store_true",
                   help="Skip LenderBehaviorRisk parquet")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    for year in args.year:
        try:
            process_year(year, args.lar_sample, args.skip_lar, args.skip_risk)
        except Exception as e:
            print(f"  FAIL {year}: {e}")
            raise
    print("\nDone.")


if __name__ == "__main__":
    main()
