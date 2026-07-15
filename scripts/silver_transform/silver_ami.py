"""
silver_ami.py
Processes AMI Bronze files into Silver parquet.
Run order: crosswalk (npg) -> section8 -> special_program -> state_ami -> national_floor.

Usage:
    python scripts/silver_ami.py --year 2025
    python scripts/silver_ami.py --year 2023 2024 2025
"""

import argparse
import re
from datetime import date
from pathlib import Path

import pandas as pd

_ROOT      = Path(__file__).resolve().parents[2]
BRONZE_DIR = _ROOT / "bronze_files" / "ami"
SILVER_DIR = _ROOT / "silver" / "ami"

# ---------------------------------------------------------------------------
# State info: state_name -> (abbr, fips2)
# ---------------------------------------------------------------------------

_STATE_INFO: dict[str, tuple[str, str]] = {
    "ALABAMA": ("AL", "01"), "ALASKA": ("AK", "02"), "ARIZONA": ("AZ", "04"),
    "ARKANSAS": ("AR", "05"), "CALIFORNIA": ("CA", "06"), "COLORADO": ("CO", "08"),
    "CONNECTICUT": ("CT", "09"), "DELAWARE": ("DE", "10"), "DISTRICT OF COLUMBIA": ("DC", "11"),
    "FLORIDA": ("FL", "12"), "GEORGIA": ("GA", "13"), "HAWAII": ("HI", "15"),
    "IDAHO": ("ID", "16"), "ILLINOIS": ("IL", "17"), "INDIANA": ("IN", "18"),
    "IOWA": ("IA", "19"), "KANSAS": ("KS", "20"), "KENTUCKY": ("KY", "21"),
    "LOUISIANA": ("LA", "22"), "MAINE": ("ME", "23"), "MARYLAND": ("MD", "24"),
    "MASSACHUSETTS": ("MA", "25"), "MICHIGAN": ("MI", "26"), "MINNESOTA": ("MN", "27"),
    "MISSISSIPPI": ("MS", "28"), "MISSOURI": ("MO", "29"), "MONTANA": ("MT", "30"),
    "NEBRASKA": ("NE", "31"), "NEVADA": ("NV", "32"), "NEW HAMPSHIRE": ("NH", "33"),
    "NEW JERSEY": ("NJ", "34"), "NEW MEXICO": ("NM", "35"), "NEW YORK": ("NY", "36"),
    "NORTH CAROLINA": ("NC", "37"), "NORTH DAKOTA": ("ND", "38"), "OHIO": ("OH", "39"),
    "OKLAHOMA": ("OK", "40"), "OREGON": ("OR", "41"), "PENNSYLVANIA": ("PA", "42"),
    "RHODE ISLAND": ("RI", "44"), "SOUTH CAROLINA": ("SC", "45"), "SOUTH DAKOTA": ("SD", "46"),
    "TENNESSEE": ("TN", "47"), "TEXAS": ("TX", "48"), "UTAH": ("UT", "49"),
    "VERMONT": ("VT", "50"), "VIRGINIA": ("VA", "51"), "WASHINGTON": ("WA", "53"),
    "WEST VIRGINIA": ("WV", "54"), "WISCONSIN": ("WI", "55"), "WYOMING": ("WY", "56"),
    "PUERTO RICO": ("PR", "72"), "VIRGIN ISLANDS": ("VI", "78"), "GUAM": ("GU", "66"),
    "AMERICAN SAMOA": ("AS", "60"), "NORTHERN MARIANA ISLANDS": ("MP", "69"),
    "NORTHERN MARIANAS": ("MP", "69"),
}


def read_excel(path: Path, sheet_index: int = 0) -> pd.DataFrame:
    engine = "xlrd" if path.suffix.lower() == ".xls" else None
    try:
        return pd.read_excel(path, sheet_name=sheet_index, engine=engine or "openpyxl")
    except Exception:
        if engine:
            raise
        return pd.read_excel(path, sheet_name=sheet_index, engine="calamine")


def _find_bronze(year: int, dataset: str) -> Path | None:
    """Return the bronze path for a dataset, trying .xlsx then .xls then .pdf, None if absent."""
    base = BRONZE_DIR / str(year)
    for ext in (".xlsx", ".xls", ".pdf"):
        p = base / f"{dataset}_{year}{ext}"
        if p.exists():
            return p
    return None


def _find_bronze_pdf(year: int, base_dataset: str) -> Path | None:
    """Finds {base_dataset}_pdf_{year}.pdf in the bronze dir. Returns Path or None."""
    p = BRONZE_DIR / str(year) / f"{base_dataset}_pdf_{year}.pdf"
    return p if p.exists() else None


# ---------------------------------------------------------------------------
# Shared row builder
# ---------------------------------------------------------------------------

def _make_row(area_name: str, mfi: int, program_type: str, limits: list[int]) -> dict:
    row: dict = {"area_name": area_name, "mfi": mfi, "program_type": program_type}
    for i, v in enumerate(limits, 1):
        row[f"limit_{i}person"] = v
    return row


def _parse_nums(text: str) -> list[int]:
    return [int(x) for x in text.split() if x.isdigit()]


# ---------------------------------------------------------------------------
# Generalized PDF income-limits parser
# ---------------------------------------------------------------------------

def _parse_il_pdf(path: Path, prog_map: dict[str, str]) -> pd.DataFrame:
    """
    Generalized HUD income limits PDF parser (fixed-width text, area-level).
    Works for Section8 format (EXTR LOW INCOME / VERY LOW INCOME / LOW-INCOME)
    and Section235_236 format (Section 236 / Sec. 221 BMIR / Section 235).
    Returns DataFrame with: area_name, mfi, program_type, limit_1person..limit_8person.
    """
    try:
        import pdfplumber
    except ImportError as e:
        raise RuntimeError("pdfplumber not installed; run: pip install pdfplumber") from e

    prog_alts = "|".join(re.escape(k) for k in prog_map)
    re_mfi = re.compile(
        rf"FY\s+\d{{4}}\s+MFI:\s*(\d+)\s+({prog_alts})\s+([\d\s]+)"
    )
    re_cont = re.compile(rf"^({prog_alts})\s+([\d\s]+)")
    re_skip = re.compile(r"^(STATE:|PROGRAM\s+\d|---|\s*$|PAGE\s+\d|\d{1,3}\s+PERSON)")

    rows: list[dict] = []
    current_area: str | None = None
    current_mfi: int | None = None

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for raw in (page.extract_text() or "").splitlines():
                line = raw.strip()
                if not line:
                    continue
                # Data rows first
                m = re_mfi.match(line)
                if m:
                    current_mfi = int(m.group(1))
                    prog = prog_map[m.group(2)]
                    nums = [int(x) for x in m.group(3).split() if x.isdigit()]
                    if len(nums) >= 8 and current_area:
                        rows.append(_make_row(current_area, current_mfi, prog, nums[:8]))
                    continue
                m = re_cont.match(line)
                if m and current_area and current_mfi is not None:
                    prog = prog_map[m.group(1)]
                    nums = [int(x) for x in m.group(2).split() if x.isdigit()]
                    if len(nums) >= 8:
                        rows.append(_make_row(current_area, current_mfi, prog, nums[:8]))
                    continue
                # Skip headers
                if re_skip.match(line) or "INCOME LIMITS" in line.upper() or line.startswith("---"):
                    continue
                # Area name
                current_area = line
                current_mfi = None

    if not rows:
        raise ValueError(f"No rows extracted from {path}")
    df = pd.DataFrame(rows)
    df["pdf_source"] = path.name
    return df


# ---------------------------------------------------------------------------
# Section8 PDF parser (calls generalized _parse_il_pdf)
# ---------------------------------------------------------------------------

_S8_PROG = {
    "EXTR LOW INCOME": "ELI",
    "VERY LOW INCOME": "VLI",
    "LOW-INCOME": "LI",
    "LOW INCOME": "LI",
}


def parse_pdf_section8(path: Path) -> pd.DataFrame:
    """
    Parse a HUD Section8 income-limits PDF (pre-2016 fixed-width layout).
    Returns a DataFrame with columns:
      area_name, mfi, program_type, limit_1person..limit_8person, pdf_source.
    area_code is NOT present in PDFs — caller must join from another source.
    """
    return _parse_il_pdf(path, _S8_PROG)


# ---------------------------------------------------------------------------
# Section235_236 PDF parser
# ---------------------------------------------------------------------------

_S236_PROG = {
    "Section 236": "SEC236",
    "Sec. 221 BMIR": "SEC221_BMIR",
    "Section 235": "SEC235",
}


def parse_pdf_section235_236(path: Path) -> pd.DataFrame:
    """
    Parse a HUD Section 235/236 income-limits PDF (fixed-width layout).
    Returns a DataFrame with columns:
      area_name, mfi, program_type, limit_1person..limit_8person, pdf_source.
    """
    return _parse_il_pdf(path, _S236_PROG)


# ---------------------------------------------------------------------------
# Area definitions PDF parser -> crosswalk DataFrame
# ---------------------------------------------------------------------------

_RE_STATE_HDR = re.compile(r'^(\d{2})\s+([A-Z][A-Z ]+)$')
_RE_AREA_LINE = re.compile(r'^(?:CBSA|SA):\s+(.+?)\s+-\s+(\S+)\s+(.+)$')
_RE_CTY_ENTRY = re.compile(r'(\d{3})-')   # 3-digit county code prefix


def parse_area_definitions_pdf(path: Path, year: int) -> pd.DataFrame:
    """
    Parse HUD area_definitions PDF into a county-area crosswalk DataFrame.

    Format observed:
        01 ALABAMA
        CBSA: Anniston-Oxford, AL MSA - METRO11500M11500 015-Calhoun
        SA: Chilton County, AL HMFA - METRO13820N01021 021-Chilton
        ...NONMETROPOLITAN COUNTIES...
        003-Baldwin 005-Barbour 011-Bullock ...

    Returns columns: fips, state, county, hud_area_code, hud_area_name, metro, year.
    Non-metro FIPS format: {state2}{county3}99999
    Non-metro area_code: NCNTY{state2}{county3}N{state2}{county3}
    """
    try:
        import pdfplumber
    except ImportError as e:
        raise RuntimeError("pdfplumber not installed; run: pip install pdfplumber") from e

    rows: list[dict] = []
    state_fips = state_abbr = None
    in_nonmetro = False

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for raw in (page.extract_text() or "").splitlines():
                line = raw.strip()
                if not line:
                    continue

                # Page/section headers to skip
                if line.startswith("FY ") and "LIST OF COUNTIES" in line:
                    continue
                if re.match(r"^-{5,}", line) or line.startswith("PAGE "):
                    if "NONMETROPOLITAN" in line.upper():
                        in_nonmetro = True
                    elif "METROPOLITAN" in line.upper():
                        in_nonmetro = False
                    continue

                # State header: "01 ALABAMA"
                m = _RE_STATE_HDR.match(line)
                if m:
                    sfips = m.group(1)
                    sname = m.group(2).strip()
                    info = _STATE_INFO.get(sname)
                    if info:
                        state_abbr, state_fips = info
                    else:
                        state_fips, state_abbr = sfips, sname[:2]
                    in_nonmetro = False
                    continue

                if "NONMETROPOLITAN" in line.upper():
                    in_nonmetro = True
                    continue

                if "METROPOLITAN AREA" in line.upper() or "Counties of FMR AREA" in line:
                    in_nonmetro = False
                    continue

                # CBSA/SA area line
                m = _RE_AREA_LINE.match(line)
                if m and state_fips:
                    in_nonmetro = False
                    area_name = m.group(1).strip()
                    area_code = m.group(2).strip()
                    ctys_str = m.group(3)
                    # split on spaces before 3-digit codes
                    entries = re.split(r',?\s+(?=\d{3}-)', ctys_str.strip())
                    for entry in entries:
                        cm = re.match(r'(\d{3})', entry)
                        if cm:
                            cty = cm.group(1)
                            fips = f"{state_fips}{cty}99999"
                            rows.append({
                                "fips": fips, "state": state_fips, "county": cty,
                                "hud_area_code": area_code, "hud_area_name": area_name,
                                "metro": 1,
                            })
                    continue

                # Non-metro county codes (split on space before 3-digit code)
                if in_nonmetro and state_fips:
                    entries = re.split(r'\s+(?=\d{3}-)', line.strip())
                    for entry in entries:
                        cm = re.match(r'(\d{3})-(\S+)', entry)
                        if cm:
                            cty = cm.group(1)
                            cname = cm.group(2).rstrip(',')
                            fips = f"{state_fips}{cty}99999"
                            acode = f"NCNTY{state_fips}{cty}N{state_fips}{cty}"
                            aname = f"{cname} County, {state_abbr}"
                            rows.append({
                                "fips": fips, "state": state_fips, "county": cty,
                                "hud_area_code": acode, "hud_area_name": aname,
                                "metro": 0,
                            })

    df = pd.DataFrame(rows)
    df["year"] = year
    return df


# ---------------------------------------------------------------------------
# State income limits PDF parser
# ---------------------------------------------------------------------------

_ST_PROG = {
    "30% OF MEDIAN": "ELI",
    "VERY LOW INCOME": "VLI",
    "LOW-INCOME": "LI",
    "LOW INCOME": "LI",
}


def parse_state_il_pdf(path: Path, year: int) -> pd.DataFrame:
    """
    Parse a HUD State Income Limits PDF.

    Format:
        ALABAMA
        FY 2022 MFI: 73600 30% OF MEDIAN 15450 17650 19850 22100 23850 25600 27400 29150
        VERY LOW INCOME 25750 29450 33100 36800 39750 42700 45650 48600
        LOW-INCOME 41200 47100 53000 58900 63600 68300 73000 77700

    Returns columns: state_name, state_fips, state_abbr, mfi, program_type,
                     limit_1person..limit_8person, year.
    """
    try:
        import pdfplumber
    except ImportError as e:
        raise RuntimeError("pdfplumber not installed; run: pip install pdfplumber") from e

    prog_alts = "|".join(re.escape(k) for k in _ST_PROG)
    re_mfi = re.compile(rf"FY\s+\d{{4}}\s+MFI:\s*(\d+)\s+({prog_alts})\s+([\d\s]+)")
    re_cont = re.compile(rf"^({prog_alts})\s+([\d\s]+)")
    re_state_name = re.compile(r"^([A-Z][A-Z ]+[A-Z])$")

    rows: list[dict] = []
    current_state: str | None = None
    current_state_fips: str | None = None
    current_state_abbr: str | None = None
    current_mfi: int | None = None

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for raw in (page.extract_text() or "").splitlines():
                line = raw.strip()
                if not line:
                    continue
                if "STATE INCOME LIMITS" in line or "INCOME LIMITS" in line or line.startswith("STATE "):
                    continue
                if re.match(r"^(FY\s+\d{4}\s+STATE|PAGE\s+\d|---)", line):
                    continue
                if re.match(r"^(1\s+PERSON|PROGRAM\s+)", line):
                    continue

                # Data rows
                m = re_mfi.match(line)
                if m:
                    current_mfi = int(m.group(1))
                    prog = _ST_PROG[m.group(2)]
                    nums = [int(x) for x in m.group(3).split() if x.isdigit()]
                    if len(nums) >= 8 and current_state:
                        rows.append({
                            "state_name": current_state,
                            "state_fips": current_state_fips,
                            "state_abbr": current_state_abbr,
                            "mfi": current_mfi,
                            "program_type": prog,
                            **{f"limit_{i+1}person": v for i, v in enumerate(nums[:8])},
                        })
                    continue
                m = re_cont.match(line)
                if m and current_state and current_mfi is not None:
                    prog = _ST_PROG[m.group(1)]
                    nums = [int(x) for x in m.group(2).split() if x.isdigit()]
                    if len(nums) >= 8:
                        rows.append({
                            "state_name": current_state,
                            "state_fips": current_state_fips,
                            "state_abbr": current_state_abbr,
                            "mfi": current_mfi,
                            "program_type": prog,
                            **{f"limit_{i+1}person": v for i, v in enumerate(nums[:8])},
                        })
                    continue

                # State name line
                ms = re_state_name.match(line)
                if ms:
                    sname = ms.group(1).strip()
                    if sname in _STATE_INFO:
                        current_state = sname
                        current_state_abbr, current_state_fips = _STATE_INFO[sname]
                        current_mfi = None

    if not rows:
        raise ValueError(f"No state rows extracted from {path}")
    df = pd.DataFrame(rows)
    df["year"] = year
    return df


# ---------------------------------------------------------------------------
# Column utilities
# ---------------------------------------------------------------------------

def normalize_columns(df: pd.DataFrame, year: int) -> pd.DataFrame:
    """Normalize column names across HUD schema versions (2020-2022 vs 2023+)."""
    df.columns = df.columns.str.strip()
    lower_map = {c.lower(): c for c in df.columns}
    renames = {}
    if "fips2010" in lower_map and "fips" not in lower_map:
        renames[lower_map["fips2010"]] = "fips"
    elif "fips2000" in lower_map and "fips" not in lower_map and "fips2010" not in lower_map:
        renames[lower_map["fips2000"]] = "fips"
    if "cbsasub" in lower_map and "hud_area_code" not in lower_map:
        renames[lower_map["cbsasub"]] = "hud_area_code"
    if "metro_area_name" in lower_map and "hud_area_name" not in lower_map:
        renames[lower_map["metro_area_name"]] = "hud_area_name"
    # Normalise any mediaNNNN column to "median_mfi" for consistent downstream access
    for key, orig in lower_map.items():
        if key.startswith("median") and key[6:].isdigit() and "median_mfi" not in lower_map:
            renames[orig] = "median_mfi"
            break
    if "State" in df.columns and "state" not in df.columns:
        renames["State"] = "state"
    if "County" in df.columns and "county" not in df.columns:
        renames["County"] = "county"
    return df.rename(columns=renames)


def cast_fips_cols(df: pd.DataFrame) -> pd.DataFrame:
    df["fips"] = df["fips"].astype(str).str.zfill(10)
    df["state"] = df["state"].astype(str).str.zfill(2)
    df["county"] = df["county"].astype(str).str.zfill(3)
    return df


def pivot_wide_limits(df: pd.DataFrame, program_map: dict, id_cols: list) -> pd.DataFrame:
    """Keep limits in wide format — one row per (area, program_type) with limit_1person..limit_8person columns."""
    frames = []
    for prefix, program_type in program_map.items():
        src_cols = [f"{prefix}_{i}" for i in range(1, 9)]
        sub = df[id_cols + src_cols].copy()
        rename = {f"{prefix}_{i}": f"limit_{i}person" for i in range(1, 9)}
        sub = sub.rename(columns=rename)
        sub["program_type"] = program_type
        frames.append(sub)
    return pd.concat(frames, ignore_index=True)


def dedup_to_area(wide_df: pd.DataFrame) -> pd.DataFrame:
    """
    Collapse county-level rows to one row per (hud_area_code, program_type).
    Limit values must be identical across all member counties of every HUD area.
    State is taken as the most common state code (handles cross-state MSAs by plurality).
    """
    limit_cols = [f"limit_{i}person" for i in range(1, 9)]

    # Verify all 8 limit columns are consistent within each area+program
    for col in limit_cols:
        check = wide_df.groupby(["hud_area_code", "program_type"])[col].nunique()
        bad = check[check > 1]
        if not bad.empty:
            raise ValueError(
                f"{len(bad)} (hud_area_code, program_type) groups have inconsistent "
                f"'{col}' values across member counties."
            )

    def mode_first(s):
        return s.mode().iloc[0] if not s.empty else None

    agg_dict = {col: (col, "first") for col in limit_cols}
    agg_dict["mfi_value"] = ("median_mfi", "first")
    agg_dict["state"] = ("state", mode_first)

    return (
        wide_df.groupby(["hud_area_code", "hud_area_name", "program_type"])
        .agg(**agg_dict)
        .reset_index()
    )


def add_max_income(df: pd.DataFrame) -> pd.DataFrame:
    """Alias limit_4person as max_income (same value, explicit column per spec)."""
    df["max_income"] = df["limit_4person"]
    return df


def validate_silver(df: pd.DataFrame, label: str) -> None:
    limit_cols = [f"limit_{i}person" for i in range(1, 9)]
    for col in limit_cols:
        if col not in df.columns:
            raise ValueError(f"{label}: missing column '{col}'")
        if df[col].isna().any():
            raise ValueError(f"{label}: NULL in {col}")
        if (pd.to_numeric(df[col], errors="coerce") <= 0).any():
            raise ValueError(f"{label}: non-positive value in {col}")
    if df["hud_fmr_area_code"].isna().any():
        raise ValueError(f"{label}: missing hud_fmr_area_code")

    # Check monotonic scaling across person counts
    limit_matrix = df[limit_cols].apply(pd.to_numeric, errors="coerce")
    non_monotonic = (limit_matrix.diff(axis=1).dropna(axis=1, how="all") < 0).any(axis=1)
    if non_monotonic.any():
        print(f"  WARN {label}: {non_monotonic.sum()} rows have non-monotonic person-count scaling")

    print(f"  OK {label}: {len(df):,} rows validated")


# ---------------------------------------------------------------------------
# Crosswalk
# ---------------------------------------------------------------------------

def run_crosswalk(year: int, silver_dir: Path) -> pd.DataFrame:
    path = _find_bronze(year, "npg")
    if path is None:
        # Try area_definitions_pdf (covers 2010-2014 where NPG not published)
        pdf_path = _find_bronze_pdf(year, "area_definitions")
        if pdf_path is None:
            print(f"  npg_{year}: not found — skipping crosswalk (no NPG published pre-2015)")
            return pd.DataFrame()
        print(f"  npg_{year}: using area_definitions PDF fallback ({pdf_path.name})")
        try:
            crosswalk = parse_area_definitions_pdf(pdf_path, year)
            crosswalk["year"] = year
            out = silver_dir / f"silver_county_area_crosswalk_{year}.parquet"
            crosswalk.to_parquet(out, index=False)
            print(f"  Crosswalk (PDF): {len(crosswalk):,} counties -> "
                  f"{crosswalk['hud_area_code'].nunique():,} HUD areas -> {out}")
            return crosswalk
        except Exception as e:
            print(f"  WARN crosswalk PDF parse failed: {e}")
            return pd.DataFrame()

    df = read_excel(path)
    df = normalize_columns(df, year)
    df = cast_fips_cols(df)
    crosswalk = df[["fips", "state", "county", "hud_area_code", "hud_area_name", "metro"]].copy()
    crosswalk["year"] = year
    out = silver_dir / f"silver_county_area_crosswalk_{year}.parquet"
    crosswalk.to_parquet(out, index=False)
    print(f"  Crosswalk: {len(crosswalk):,} counties -> {crosswalk['hud_area_code'].nunique():,} HUD areas -> {out}")
    return crosswalk


# ---------------------------------------------------------------------------
# Section8
# ---------------------------------------------------------------------------

def _finalise_section8(area_df: pd.DataFrame, year: int, silver_dir: Path) -> pd.DataFrame:
    """Shared finalisation logic for both Excel and PDF section8 paths."""
    area_df["year"] = year
    area_df["ami_pct"] = area_df["program_type"].map({"ELI": 30, "VLI": 50, "LI": 80})
    area_df = area_df.rename(columns={"area_code": "hud_fmr_area_code"})
    area_df["limit_id"] = (
        area_df["hud_fmr_area_code"].astype(str) + "_" + str(year) + "_" + area_df["program_type"]
    )
    area_df = add_max_income(area_df)
    area_df["max_rent"] = area_df["limit_4person"] * 0.03
    area_df.loc[area_df["program_type"] != "VLI", "max_rent"] = None
    area_df["held_harmless"] = None
    area_df["volatility_cap_applied"] = None
    area_df["uncapped_value"] = None

    limit_cols = [f"limit_{i}person" for i in range(1, 9)]
    final_cols = (
        ["limit_id", "hud_fmr_area_code", "area_name", "state", "program_type",
         "mfi_value", "year", "ami_pct"]
        + limit_cols
        + ["max_income", "max_rent", "held_harmless", "volatility_cap_applied", "uncapped_value"]
    )
    # state may be absent from PDF path
    if "state" not in area_df.columns:
        area_df["state"] = None
    area_df = area_df[final_cols]

    validate_silver(area_df, f"Section8AMILimit {year}")
    out = silver_dir / f"silver_section8_ami_limit_{year}.parquet"
    area_df.to_parquet(out, index=False)
    print(f"  Section8AMILimit: {len(area_df):,} rows ({area_df['hud_fmr_area_code'].nunique():,} areas) -> {out}")
    return area_df


def _name_key(s: str) -> str:
    """Normalised key for fuzzy area-name matching (lower, strip punctuation)."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _load_area_code_map(year: int) -> dict[str, str]:
    """
    Build area_name -> area_code lookup from section235_236 (same year) or
    the 2015 NPG crosswalk as fallback. Returns {} if neither is available.
    """
    # Try section235_236 for the same year first
    for src_year in (year, 2015, 2016):
        p235 = _find_bronze(src_year, "section235_236")
        if p235 is not None:
            df = read_excel(p235, sheet_index=0)
            df = normalize_columns(df, src_year)
            if "hud_area_code" in df.columns and "hud_area_name" in df.columns:
                code_map = (
                    df[["hud_area_code", "hud_area_name"]]
                    .dropna()
                    .drop_duplicates("hud_area_name")
                    .set_index("hud_area_name")["hud_area_code"]
                    .to_dict()
                )
                # Build normalised-key -> code
                return {_name_key(k): v for k, v in code_map.items()}
    return {}


def _run_section8_pdf(path: Path, year: int, silver_dir: Path) -> pd.DataFrame:
    """Process a Section8 PDF (e.g. FY2014) into silver parquet."""
    print(f"  section8_{year}: parsing PDF ({path.name})")
    df = parse_pdf_section8(path)

    # Try to join area codes from section235_236 of the same year (or nearest year)
    code_map = _load_area_code_map(year)
    if code_map:
        df["area_code"] = df["area_name"].apply(
            lambda n: code_map.get(_name_key(n))
        )
        matched = df["area_code"].notna().sum()
        total = len(df)
        unmatched = total - matched
        # For unmatched rows, fall back to area_name as a synthetic code (name-keyed)
        df.loc[df["area_code"].isna(), "area_code"] = df.loc[
            df["area_code"].isna(), "area_name"
        ]
        print(f"  PDF area-code join: {matched}/{total} rows matched "
              f"({unmatched} unmatched — using area_name as code)")
    else:
        print("  PDF area-code join: no reference Excel available — area_code=None for all rows")
        df["area_code"] = None

    # Deduplicate to one row per (area_code/area_name, program_type)
    limit_cols = [f"limit_{i}person" for i in range(1, 9)]
    group_col = "area_code" if df["area_code"].notna().any() else "area_name"
    df = (
        df.groupby([group_col, "area_name", "program_type"], dropna=False)
        .agg(mfi_value=("mfi", "first"),
             **{c: (c, "first") for c in limit_cols})
        .reset_index()
    )
    if group_col == "area_name" and "area_code" not in df.columns:
        df["area_code"] = None
    df = df.rename(columns={group_col: "area_code"} if group_col == "area_code" else {})

    return _finalise_section8(df, year, silver_dir)


def run_section8(year: int, silver_dir: Path) -> pd.DataFrame:
    path = _find_bronze(year, "section8")
    if path is None:
        print(f"  section8_{year}: not found — skipping Section8AMILimit")
        return pd.DataFrame()

    # -----------------------------------------------------------------------
    # PDF fallback path
    # -----------------------------------------------------------------------
    if path.suffix.lower() == ".pdf":
        return _run_section8_pdf(path, year, silver_dir)

    df = read_excel(path, sheet_index=0)
    df = normalize_columns(df, year)
    df = cast_fips_cols(df)

    id_cols = ["fips", "state", "county", "hud_area_code", "hud_area_name", "median_mfi"]
    # Pre-2015 HUD used 'l30' for ELI (30% of median); 2015+ uses 'ELI'
    eli_prefix = "l30" if year < 2015 else "ELI"
    wide_df = pivot_wide_limits(df, {"l50": "VLI", eli_prefix: "ELI", "l80": "LI"}, id_cols)
    area_df = dedup_to_area(wide_df)

    area_df = area_df.rename(columns={"hud_area_code": "area_code", "hud_area_name": "area_name",
                                       "mfi_value": "mfi_value"})
    return _finalise_section8(area_df, year, silver_dir)


# ---------------------------------------------------------------------------
# Special program (Section 235/236)
# ---------------------------------------------------------------------------

def _finalise_special_program(area_df: pd.DataFrame, year: int, silver_dir: Path) -> pd.DataFrame:
    """Shared finalisation for special program (Excel and PDF paths)."""
    area_df = area_df.rename(columns={"area_code": "hud_fmr_area_code"})
    area_df["year"] = year
    area_df["ami_pct"] = None
    area_df["limit_id"] = (
        area_df["hud_fmr_area_code"].astype(str) + "_" + str(year) + "_" + area_df["program_type"]
    )
    area_df = add_max_income(area_df)
    area_df["held_harmless"] = None
    area_df["volatility_cap_applied"] = None
    area_df["uncapped_value"] = None

    limit_cols = [f"limit_{i}person" for i in range(1, 9)]
    final_cols = (
        ["limit_id", "hud_fmr_area_code", "area_name", "state", "program_type",
         "mfi_value", "year", "ami_pct"]
        + limit_cols
        + ["max_income", "held_harmless", "volatility_cap_applied", "uncapped_value"]
    )
    if "state" not in area_df.columns:
        area_df["state"] = None
    if "mfi_value" not in area_df.columns:
        area_df["mfi_value"] = None
    area_df = area_df[final_cols]

    validate_silver(area_df, f"SpecialProgramLimit {year}")
    out = silver_dir / f"silver_special_program_limit_{year}.parquet"
    area_df.to_parquet(out, index=False)
    print(f"  SpecialProgramLimit: {len(area_df):,} rows -> {out}")
    return area_df


def _run_special_program_pdf(path: Path, year: int, silver_dir: Path) -> pd.DataFrame:
    """Process a Section 235/236 PDF into silver parquet."""
    print(f"  section235_236_{year}: parsing PDF ({path.name})")
    df = parse_pdf_section235_236(path)

    # Try to join area codes using same approach as section8 PDF path
    code_map = _load_area_code_map(year)
    if code_map:
        df["area_code"] = df["area_name"].apply(
            lambda n: code_map.get(_name_key(n))
        )
        matched = df["area_code"].notna().sum()
        total = len(df)
        unmatched = total - matched
        df.loc[df["area_code"].isna(), "area_code"] = df.loc[
            df["area_code"].isna(), "area_name"
        ]
        print(f"  PDF area-code join: {matched}/{total} rows matched "
              f"({unmatched} unmatched — using area_name as code)")
    else:
        print("  PDF area-code join: no reference available — area_code=area_name fallback")
        df["area_code"] = df["area_name"]

    # Deduplicate to one row per (area_code, program_type)
    limit_cols = [f"limit_{i}person" for i in range(1, 9)]
    df = (
        df.groupby(["area_code", "area_name", "program_type"], dropna=False)
        .agg(mfi_value=("mfi", "first"),
             **{c: (c, "first") for c in limit_cols})
        .reset_index()
    )

    return _finalise_special_program(df, year, silver_dir)


def run_special_program(year: int, silver_dir: Path) -> pd.DataFrame:
    path = _find_bronze(year, "section235_236")
    if path is None:
        pdf_path = _find_bronze_pdf(year, "section235_236")
        if pdf_path is None:
            print(f"  section235_236_{year}: not found — skipping SpecialProgramLimit")
            return pd.DataFrame()
        return _run_special_program_pdf(pdf_path, year, silver_dir)

    df = read_excel(path, sheet_index=0)
    df = normalize_columns(df, year)
    df = cast_fips_cols(df)

    id_cols = ["fips", "state", "county", "hud_area_code", "hud_area_name", "median_mfi"]
    wide_df = pivot_wide_limits(
        df, {"S236": "SEC236", "S235": "SEC235", "BMIR": "SEC221_BMIR"}, id_cols
    )
    area_df = dedup_to_area(wide_df)

    area_df = area_df.rename(columns={"hud_area_code": "area_code", "hud_area_name": "area_name"})

    return _finalise_special_program(area_df, year, silver_dir)


# ---------------------------------------------------------------------------
# State AMI
# ---------------------------------------------------------------------------

def _run_state_ami_pdf(path: Path, year: int, silver_dir: Path) -> pd.DataFrame:
    """Process a state income limits PDF into silver StateAMILimit parquet."""
    print(f"  state_ami_{year}: parsing PDF ({path.name})")
    df = parse_state_il_pdf(path, year)

    # Rename mfi -> state_mfi to match the Excel schema
    df = df.rename(columns={"mfi": "state_mfi"})
    df["state"] = df["state_fips"].astype(str).str.zfill(2)

    df["ami_pct"] = df["program_type"].map({"ELI": 30, "VLI": 50, "LI": 80})
    df["limit_id"] = df["state"] + "_" + str(year) + "_" + df["program_type"]
    df["max_income"] = df["limit_4person"]
    df["max_rent"] = df["limit_4person"] * 0.03
    df.loc[df["program_type"] != "VLI", "max_rent"] = None
    df["held_harmless"] = None
    df["volatility_cap_applied"] = None
    df["uncapped_value"] = None

    limit_cols = [f"limit_{i}person" for i in range(1, 9)]
    final_cols = (
        ["limit_id", "state", "program_type", "state_mfi", "year", "ami_pct"]
        + limit_cols
        + ["max_income", "max_rent", "held_harmless", "volatility_cap_applied", "uncapped_value"]
    )
    wide = df[final_cols]

    out = silver_dir / f"silver_state_ami_limit_{year}.parquet"
    wide.to_parquet(out, index=False)
    print(f"  StateAMILimit (PDF): {len(wide):,} rows -> {out}")
    return wide


def run_state_ami(year: int, silver_dir: Path) -> pd.DataFrame | None:
    """
    Produces StateAMILimit from the HUD State AMI Excel file.
    Expected bronze path: bronze_files/ami/{year}/state_ami_{year}.xlsx
    Columns: state, program_type (30%/VLI/LI labels), mfi column, 8 person-count income columns.
    Falls back to state_il PDF if Excel not found.
    """
    path = BRONZE_DIR / str(year) / f"state_ami_{year}.xlsx"
    if not path.exists():
        pdf_path = _find_bronze_pdf(year, "state_il")
        if pdf_path is None:
            print(f"  state_ami_{year}.xlsx not found — skipping StateAMILimit")
            return None
        return _run_state_ami_pdf(pdf_path, year, silver_dir)

    df = read_excel(path, sheet_index=0)
    df.columns = df.columns.str.strip()

    # Normalize program labels
    label_map = {
        "30% of median": "ELI",
        "30% of area median": "ELI",
        "very low income": "VLI",
        "low-income": "LI",
        "low income": "LI",
    }
    prog_col = [c for c in df.columns if "program" in c.lower() or "income" in c.lower()][0]
    df["program_type"] = df[prog_col].str.strip().str.lower().map(label_map)
    df = df[df["program_type"].notna()].copy()

    mfi_col = [c for c in df.columns if "mfi" in c.lower() or "median" in c.lower()][0]
    df = df.rename(columns={mfi_col: "state_mfi"})

    state_col = [c for c in df.columns if c.lower() in ("state", "st")][0]
    df = df.rename(columns={state_col: "state"})
    df["state"] = df["state"].astype(str).str.zfill(2)

    # Detect the 8 person-count limit columns (numeric columns after mfi col)
    numeric_cols = [c for c in df.columns if df[c].dtype in ("int64", "float64") and c != "state_mfi"]
    limit_src_cols = numeric_cols[:8]

    id_cols = ["state", "program_type", "state_mfi"]
    wide = df[id_cols + limit_src_cols].copy()
    rename = {src: f"limit_{i+1}person" for i, src in enumerate(limit_src_cols)}
    wide = wide.rename(columns=rename)

    wide["year"] = year
    wide["ami_pct"] = wide["program_type"].map({"ELI": 30, "VLI": 50, "LI": 80})
    wide["limit_id"] = wide["state"] + "_" + str(year) + "_" + wide["program_type"]
    wide["max_income"] = wide["limit_4person"]
    wide["max_rent"] = wide["limit_4person"] * 0.03
    wide.loc[wide["program_type"] != "VLI", "max_rent"] = None
    wide["held_harmless"] = None
    wide["volatility_cap_applied"] = None
    wide["uncapped_value"] = None

    limit_cols = [f"limit_{i}person" for i in range(1, 9)]
    final_cols = (
        ["limit_id", "state", "program_type", "state_mfi", "year", "ami_pct"]
        + limit_cols
        + ["max_income", "max_rent", "held_harmless", "volatility_cap_applied", "uncapped_value"]
    )
    wide = wide[final_cols]

    out = silver_dir / f"silver_state_ami_limit_{year}.parquet"
    wide.to_parquet(out, index=False)
    print(f"  StateAMILimit: {len(wide):,} rows -> {out}")
    return wide


# ---------------------------------------------------------------------------
# National floor
# ---------------------------------------------------------------------------

def run_national_floor(year: int, silver_dir: Path) -> dict | None:
    path = BRONZE_DIR / str(year) / f"national_nonmet_{year}.xlsx"
    if not path.exists():
        print(f"  national_nonmet_{year}.xlsx not found — skipping floor constant")
        return None

    try:
        df = read_excel(path, sheet_index=0)
    except Exception as e:
        print(f"  WARN national_nonmet_{year}.xlsx unreadable: {e}")
        return None

    data_row = None
    for _, row in df.iterrows():
        if pd.notna(row.iloc[0]) and str(row.iloc[0]).strip().lstrip("-").isdigit():
            data_row = row
            break

    if data_row is None:
        print(f"  WARN national_nonmet_{year}.xlsx: could not locate data row")
        return None

    values = data_row.tolist()
    floor = {"median": int(values[0])}
    for i in range(1, 9):
        floor[f"limit_{i}person"] = int(values[i])

    out = silver_dir / f"national_nonmetro_vlil_floor_{year}.parquet"
    pd.DataFrame([{"year": year, **floor}]).to_parquet(out, index=False)
    print(f"  National non-metro VLIL floor: median=${floor['median']:,}, "
          f"4-person=${floor['limit_4person']:,} -> {out}")
    return floor


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_ami(year: int) -> None:
    silver_dir = SILVER_DIR / str(year)
    silver_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n=== AMI Silver {year} ===")
    run_crosswalk(year, silver_dir)
    run_section8(year, silver_dir)
    run_special_program(year, silver_dir)
    run_state_ami(year, silver_dir)
    run_national_floor(year, silver_dir)
    print(f"  Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process AMI Bronze files to Silver parquet.")
    parser.add_argument("--year", type=int, nargs="+", required=True,
                        help="One or more years to process (e.g. --year 2024 2025)")
    args = parser.parse_args()

    for year in args.year:
        run_ami(year)
