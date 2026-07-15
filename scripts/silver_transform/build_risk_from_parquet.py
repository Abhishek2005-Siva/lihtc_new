"""
Build silver_lender_behavior_risk_{year}.parquet from existing silver LAR parquets.
Fixes 2018-2021 where _build_risk used wrong MLAR column positions on snapshot format.
"""
import argparse
from collections import defaultdict
from pathlib import Path
from statistics import mode as _stat_mode

import numpy as np
import pandas as pd

SILVER_ROOT = Path(__file__).resolve().parents[2] / "silver" / "hmda"
OMB_CROSSWALK = Path(__file__).resolve().parents[2] / "bronze_files" / "omb" / "cbsa_crosswalk.csv"

_ORIG = "1"
_DENY = "3"
_GSE = {"1", "3"}
_MF_UNITS = {"5-24", "25-49", "50-99", "100-149", ">149"}
_MINORITY_RACE = {"1", "2", "3", "4"}
_WHITE_RACE = "5"
_NOT_HISPANIC = "2"
_DR_NA = {"", "NA", "10", "1111"}
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


def _load_crosswalk() -> dict:
    if not OMB_CROSSWALK.exists():
        return {}
    df = pd.read_csv(OMB_CROSSWALK, dtype=str)
    df.columns = df.columns.str.strip().str.lower()
    county_col = next((c for c in df.columns if "county" in c), None)
    cbsa_col = next((c for c in df.columns if "cbsa" in c), None)
    if not county_col or not cbsa_col:
        return {}
    return dict(zip(df[county_col].str.zfill(5), df[cbsa_col]))


def build_risk(year: int) -> None:
    silver_dir = SILVER_ROOT / str(year)
    lar_path = silver_dir / f"silver_hmda_{year}_lar.parquet"
    out_path = silver_dir / f"silver_lender_behavior_risk_{year}.parquet"

    if not lar_path.exists():
        print(f"  {year}: LAR parquet not found — skip")
        return

    print(f"\n=== Building risk {year} from {lar_path.name} ===")

    # Determine which columns exist in this parquet
    import pyarrow.parquet as pq
    schema = pq.read_schema(str(lar_path))
    available = set(schema.names)

    # Column name mapping: try MLAR names first, then snapshot names
    def _col(mlar_name, snap_name):
        if mlar_name in available:
            return mlar_name
        if snap_name and snap_name in available:
            return snap_name
        return None

    col_year   = _col("activity_year", "activity_year")
    col_county = _col("county_code", "county_code")
    col_at     = _col("action_taken", "action_taken")
    col_amt    = _col("loan_amount", "loan_amount")
    col_ptype  = _col("purchaser_type", "purchaser_type")
    col_eth1   = _col("applicant_ethnicity_1", "applicant_ethnicity_1")
    col_race1  = _col("applicant_race_1", "applicant_race_1")
    col_units  = _col("total_units", "total_units")
    # denial reason varies by schema
    col_dr     = next((c for c in ["col_82", "denial_reason_1"] if c in available), None)

    needed = [c for c in [col_year, col_county, col_at, col_amt, col_ptype,
                           col_eth1, col_race1, col_units, col_dr] if c]
    print(f"  Reading columns: {needed}")

    crosswalk = _load_crosswalk()
    acc = defaultdict(lambda: {
        "apps": 0, "orig": 0, "denied": 0,
        "mf_apps": 0, "mf_denied": 0,
        "loan_sum": 0.0, "loan_cnt": 0,
        "gse_cnt": 0, "orig_cnt": 0,
        "min_apps": 0, "min_denied": 0,
        "wnh_apps": 0, "wnh_denied": 0,
        "denial_reasons": [],
    })

    total = 0
    pf = pq.ParquetFile(str(lar_path))
    for batch in pf.iter_batches(batch_size=500_000, columns=needed):
        df = batch.to_pandas()
        df = df.fillna("")

        at_col = df[col_at].astype(str).str.strip() if col_at else pd.Series([""] * len(df))
        mask = at_col.isin([_ORIG, _DENY])
        df = df[mask].copy()
        if df.empty:
            total += len(batch)
            print(f"    ... {total:,} rows", end="\r")
            continue

        county_s = df[col_county].astype(str).str.strip().str.zfill(5) if col_county else pd.Series([""] * len(df))
        year_s = df[col_year].astype(str).str.strip() if col_year else pd.Series([str(year)] * len(df))
        at_s = at_col[mask]
        amt_s = pd.to_numeric(df[col_amt], errors="coerce") if col_amt else pd.Series([None] * len(df))
        ptype_s = df[col_ptype].astype(str).str.strip() if col_ptype else pd.Series([""] * len(df))
        eth1_s = df[col_eth1].astype(str).str.strip() if col_eth1 else pd.Series([""] * len(df))
        race1_s = df[col_race1].astype(str).str.strip() if col_race1 else pd.Series([""] * len(df))
        units_s = df[col_units].astype(str).str.strip() if col_units else pd.Series([""] * len(df))
        dr_s = df[col_dr].astype(str).str.strip() if col_dr else pd.Series([""] * len(df))

        for i in range(len(df)):
            county = county_s.iloc[i]
            yr = year_s.iloc[i]
            at = at_s.iloc[i]
            b = acc[(county, yr)]

            b["apps"] += 1
            if at == _ORIG:
                b["orig"] += 1
            else:
                b["denied"] += 1
                dr = dr_s.iloc[i]
                if dr not in _DR_NA:
                    b["denial_reasons"].append(dr)

            units = units_s.iloc[i]
            if units in _MF_UNITS:
                b["mf_apps"] += 1
                if at == _DENY:
                    b["mf_denied"] += 1

            amt = amt_s.iloc[i]
            if not pd.isna(amt):
                b["loan_sum"] += amt
                b["loan_cnt"] += 1

            if at == _ORIG:
                b["orig_cnt"] += 1
                if ptype_s.iloc[i] in _GSE:
                    b["gse_cnt"] += 1

            race1 = race1_s.iloc[i]
            eth1 = eth1_s.iloc[i]
            is_minority = race1 in _MINORITY_RACE or eth1 == "1"
            is_wnh = race1 == _WHITE_RACE and eth1 == _NOT_HISPANIC
            if is_minority:
                b["min_apps"] += 1
                if at == _DENY:
                    b["min_denied"] += 1
            if is_wnh:
                b["wnh_apps"] += 1
                if at == _DENY:
                    b["wnh_denied"] += 1

        total += len(batch)
        print(f"    ... {total:,} rows", end="\r")

    print(f"\n    {total:,} rows read, {len(acc):,} county-year groups")

    rows = []
    for (county, year_val), b in acc.items():
        denial_overall = _safe_div(b["denied"], b["orig"] + b["denied"])
        dr_minority = _safe_div(b["min_denied"], b["min_apps"])
        dr_white = _safe_div(b["wnh_denied"], b["wnh_apps"])
        dr_mf = _safe_div(b["mf_denied"], b["mf_apps"])
        disp = _safe_div(dr_minority, dr_white)

        if disp is not None and dr_mf is not None and denial_overall is not None:
            score = 0.50 * disp + 0.30 * denial_overall + 0.20 * dr_mf
        elif disp is not None and denial_overall is not None:
            score = 0.625 * disp + 0.375 * denial_overall
        else:
            score = None

        rows.append({
            "risk_id": f"{county}_{year_val}",
            "msa_code": county,
            "assessment_year": year_val,
            "total_applications": b["apps"],
            "total_originated": b["orig"],
            "total_denied": b["denied"],
            "multifamily_applications": b["mf_apps"],
            "multifamily_denied": b["mf_denied"],
            "lender_count": None,
            "avg_loan_amount": (b["loan_sum"] / b["loan_cnt"]) if b["loan_cnt"] else None,
            "gse_sold_pct": _safe_div(b["gse_cnt"], b["orig_cnt"]),
            "denial_rate_overall": denial_overall,
            "denial_rate_minority": dr_minority,
            "denial_rate_white": dr_white,
            "multifamily_denial_rate": dr_mf,
            "denial_rate_disparity_ratio": disp,
            "fair_lending_risk_score": score,
            "risk_tier": _risk_tier(score),
            "top_denial_reason": _top_mode(b["denial_reasons"]),
            "is_high_disparity": bool(disp and disp > 2.0),
            "is_multifamily_constrained": None,
            "cbsa_code": crosswalk.get(county),
        })

    df_out = pd.DataFrame(rows)
    if df_out.empty:
        print("    No data — nothing written")
        return

    rates = pd.to_numeric(df_out["multifamily_denial_rate"], errors="coerce")
    df_out["is_multifamily_constrained"] = rates > (rates.mean() + rates.std())

    df_out.to_parquet(out_path, index=False, engine="pyarrow")
    print(f"    {len(df_out):,} rows -> {out_path.name}")
    scored = df_out["fair_lending_risk_score"].notna().sum()
    print(f"    Risk tiers ({scored} scored): " +
          ", ".join(f"{t}={c}" for t, c in df_out["risk_tier"].value_counts().items()))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--year", type=int, nargs="+", required=True)
    args = p.parse_args()
    for year in args.year:
        build_risk(year)
    print("\nDone.")


if __name__ == "__main__":
    main()
