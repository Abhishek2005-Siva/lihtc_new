"""
audit_skipped.py — Report all rows that were not ingested across all datasets.
"""
from pathlib import Path
import pandas as pd

SILVER = Path(__file__).resolve().parents[1] / "silver"

def fmt(d): return ", ".join(f"{k}={v}" for k, v in d.items()) if d else ""

rows = []

# ── QCT ──────────────────────────────────────────────────────────────────────
qct_dir = SILVER / "qct"
for p in sorted(qct_dir.glob("silver_qct_*.parquet")):
    year = p.stem.split("_")[2]
    df = pd.read_parquet(p)
    if "skipped" in p.name:
        reasons = df["_skip_reason"].value_counts().to_dict() if "_skip_reason" in df.columns else {}
        rows.append(("QCT", year, "SKIPPED_FILE", len(df), fmt(reasons)))
    else:
        if "_row_status" in df.columns:
            skip_df = df[df["_row_status"] == "SKIP"]
            if not skip_df.empty:
                reasons = skip_df["_skip_reason"].value_counts().to_dict() if "_skip_reason" in skip_df.columns else {}
                rows.append(("QCT", year, "rows_skipped", len(skip_df), fmt(reasons)))

# ── DDA ──────────────────────────────────────────────────────────────────────
dda_dir = SILVER / "dda"
for p in sorted(dda_dir.glob("*.parquet")):
    year = p.stem.split("_")[2]
    df = pd.read_parquet(p)
    kind = "SDDA_metro" if "metro_area" in p.name else \
           "SDDA_zcta"  if "metro_zcta" in p.name else \
           "NMDDA"      if "nonmetro"   in p.name else "DDA_other"
    total = len(df)
    not_designated = (df["is_designated"] == 0).sum() if "is_designated" in df.columns else 0
    no_fips = df["county_fips"].isna().sum() if "county_fips" in df.columns else 0
    if not_designated or no_fips:
        rows.append(("DDA", f"{year}/{kind}", "not_ingested_rows",
                     f"not_designated={not_designated}", f"no_county_fips={no_fips}"))

# ── AMI ──────────────────────────────────────────────────────────────────────
for yr_dir in sorted((SILVER / "ami").iterdir()):
    if not yr_dir.is_dir(): continue
    year = yr_dir.name
    for p in yr_dir.glob("silver_section8_ami_limit_*.parquet"):
        df = pd.read_parquet(p)
        no_code = df["hud_fmr_area_code"].isna().sum() if "hud_fmr_area_code" in df.columns else \
                  df["area_code"].isna().sum() if "area_code" in df.columns else "?"
        if no_code:
            rows.append(("AMI", year, "Section8_missing_area_code", no_code, ""))

# ── HMDA/LBR ─────────────────────────────────────────────────────────────────
hmda_dir = SILVER / "hmda"
for yr_dir in sorted(hmda_dir.iterdir()) if hmda_dir.exists() else []:
    if not yr_dir.is_dir(): continue
    year = yr_dir.name
    for p in yr_dir.glob("silver_lender_behavior_risk_*.parquet"):
        df = pd.read_parquet(p)
        no_cbsa = df["cbsa_code"].isna().sum() if "cbsa_code" in df.columns else 0
        rows.append(("LBR", year, "no_cbsa_code_rows", no_cbsa, f"total={len(df)}"))

# ── Print ─────────────────────────────────────────────────────────────────────
print(f"\n{'Dataset':<8} {'Year/Type':<25} {'Issue':<25} {'Count':<10} {'Detail'}")
print("-" * 100)
for r in rows:
    print(f"{r[0]:<8} {str(r[1]):<25} {str(r[2]):<25} {str(r[3]):<10} {str(r[4])}")

print(f"\n=== Missing years (no silver file at all) ===")
qct_years  = {p.stem.split("_")[2] for p in qct_dir.glob("silver_qct_[0-9]*.parquet") if "skipped" not in p.name}
dda_years  = {p.stem.split("_")[2] for p in dda_dir.glob("silver_dda_*_nonmetro*.parquet")}
ami_years  = {yr_dir.name for yr_dir in (SILVER/"ami").iterdir() if yr_dir.is_dir() and any(yr_dir.glob("silver_section8*"))}
hmda_years = {yr_dir.name for yr_dir in hmda_dir.iterdir() if yr_dir.is_dir() and any(yr_dir.glob("*.parquet"))} if hmda_dir.exists() else set()

for ds, found, expected in [
    ("QCT",  qct_years,  set(str(y) for y in range(2003,2026))),
    ("DDA",  dda_years,  set(str(y) for y in range(2003,2026))),
    ("AMI",  ami_years,  set(str(y) for y in range(2010,2026))),
    ("HMDA", hmda_years, set(str(y) for y in range(2022,2026))),
]:
    missing = sorted(expected - found)
    print(f"  {ds}: missing years = {missing if missing else 'none'}")
