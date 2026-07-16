"""
silver_dda_pdf.py
-----------------
Parses HUD DDA standalone PDFs (2008-2016) using pdfplumber word-position
extraction. No OCR fallback — if a PDF cannot be parsed the run fails so
the file can be inspected and fixed.

HUD publishes two files per year:
  DDA{YEAR}M.PDF   — Metro (SDDA) designated areas
  DDA{YEAR}NM.PDF  — Non-metro (NMDDA) designated counties

Both PDFs share the same column layout (identified by x-coordinate):
  col 1  x < 90    : U.S. state name
  col 2  x 90-270  : Metropolitan area / county group name
  col 3  x > 270   : County components (2008-2015) or ZCTA codes (2016)

Text may be garbled (words concatenated) in 2012-2015 PDFs; word-position
extraction handles this correctly.

Called from silver_qct_dda.run_dda() for year < 2017.
Can also be run standalone:
    python silver_dda_pdf.py --year 2016
    python silver_dda_pdf.py --year-range 2008 2016
"""

from __future__ import annotations

import argparse
import hashlib
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pdfplumber

ROOT       = Path(__file__).resolve().parents[2]
BRONZE_DIR = ROOT / "bronze_files" / "dda"
SILVER_DIR = ROOT / "silver" / "dda"
_COUNTY_REF = ROOT / "bronze_files" / "Geographic" / "national_county.txt"

# ── County name → FIPS lookup ────────────────────────────────────────────────

def _build_county_lookup() -> dict[tuple[str, str], str]:
    """
    Build (state_abbr, normalised_county_name) → 5-digit FIPS mapping
    from census national_county.txt.
    """
    if not _COUNTY_REF.exists():
        return {}
    lookup: dict[tuple[str, str], str] = {}
    df = pd.read_csv(_COUNTY_REF, sep="|", dtype=str)
    for _, row in df.iterrows():
        state = row["STATE"].strip().upper()
        fips5 = row["STATEFP"].zfill(2) + row["COUNTYFP"].zfill(3)
        raw   = row["COUNTYNAME"].strip()
        # Normalise: strip "County", "Parish", "Borough", "Census Area",
        # "Municipio" etc. and lower-case for fuzzy matching.
        for suffix in (
            " County", " Parish", " Borough", " Census Area",
            " Municipio", " Municipality", " city", " City",
            " Area", " Island",
        ):
            if raw.endswith(suffix):
                raw = raw[: -len(suffix)]
        lookup[(state, raw.lower())] = fips5
        # Also store with full original name
        lookup[(state, row["COUNTYNAME"].strip().lower())] = fips5
    return lookup


_COUNTY_LOOKUP: dict[tuple[str, str], str] | None = None


def _county_fips(state_abbr: str, raw_name: str) -> str | None:
    global _COUNTY_LOOKUP
    if _COUNTY_LOOKUP is None:
        _COUNTY_LOOKUP = _build_county_lookup()
    raw_name = raw_name.strip()
    st = state_abbr.upper()

    def _try(name: str) -> str | None:
        key = (st, name.lower())
        if key in _COUNTY_LOOKUP:
            return _COUNTY_LOOKUP[key]
        # Strip common county-type suffixes
        for suffix in (
            " County", " Parish", " Borough", " Census Area", " Municipio",
            " Municipality", " city", " City", " Area", " Island", " Division",
            " City and Borough", " and Borough",
        ):
            if name.endswith(suffix):
                stripped = name[: -len(suffix)]
                result = _COUNTY_LOOKUP.get((st, stripped.lower()))
                if result:
                    return result
        return None

    # Direct lookup
    result = _try(raw_name)
    if result:
        return result

    # Normalize "St." → "Saint" (census uses "Saint")
    saint_name = re.sub(r"\bSt\.\s*", "Saint ", raw_name, flags=re.I).strip()
    if saint_name != raw_name:
        result = _try(saint_name)
        if result:
            return result

    # Normalize "De " / "Mc " → "De" / "Mc" (census sometimes omits space)
    compact = re.sub(r"\b(De|Mc|La|Las|Los|San|Santa)\s+", lambda m: m.group(1), raw_name)
    if compact != raw_name:
        result = _try(compact)
        if result:
            return result

    return None


# ── State abbreviation lookup ─────────────────────────────────────────────────

_ALL_STATE_ABBRS: frozenset[str] = frozenset({
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID",
    "IL","IN","IA","KS","KY","LA","ME","MD","MA","MI","MN","MS",
    "MO","MT","NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK",
    "OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA","WV",
    "WI","WY","DC","PR","VI","GU","AS","MP",
})

_STATE_NAME_TO_ABBR: dict[str, str] = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT",
    "Delaware": "DE", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI",
    "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA",
    "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME",
    "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI",
    "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO",
    "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "NewHampshire": "NH", "New Hampshire": "NH",
    "NewJersey": "NJ", "New Jersey": "NJ",
    "NewMexico": "NM", "New Mexico": "NM",
    "NewYork": "NY", "New York": "NY",
    "NorthCarolina": "NC", "North Carolina": "NC",
    "NorthDakota": "ND", "North Dakota": "ND",
    "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR",
    "Pennsylvania": "PA", "RhodeIsland": "RI", "Rhode Island": "RI",
    "SouthCarolina": "SC", "South Carolina": "SC",
    "SouthDakota": "SD", "South Dakota": "SD",
    "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT",
    "Virginia": "VA", "Washington": "WA",
    "WestVirginia": "WV", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY",
    "DistrictofColumbia": "DC", "District of Columbia": "DC",
    "PuertoRico": "PR", "Puerto Rico": "PR",
    "VirginIslands": "VI", "Virgin Islands": "VI",
    "Guam": "GU", "AmericanSamoa": "AS", "American Samoa": "AS",
    "NorthernMarianaIslands": "MP", "Northern Mariana Islands": "MP",
}

# Column boundary defaults (overridden per-PDF by auto-detection)
_COL1_MAX_X  = 92    # fallback: state names
_COL2_MAX_X  = 270   # fallback: area/county col2


# ── Word-position text extraction ─────────────────────────────────────────────

def _detect_columns(pdf_path: Path) -> tuple[float, float]:
    """
    Auto-detect column boundaries from the PDF header row.
    Returns (col1_max_x, col2_max_x) by finding the x-positions of
    the 'State' header and the content header that follows it.
    """
    with pdfplumber.open(pdf_path) as pdf:
        words = pdf.pages[0].extract_words(x_tolerance=3, y_tolerance=3)
    # Find the 'State' header word
    state_words = [w for w in words if w["text"].strip().lower() == "state"]
    if not state_words:
        return _COL1_MAX_X, _COL2_MAX_X

    state_x = state_words[0]["x0"]
    state_y = state_words[0]["top"]

    # Find header words on the same y-row that are to the right of 'State'
    header_row = [
        w for w in words
        if abs(w["top"] - state_y) < 5 and w["x0"] > state_x + 20
    ]
    if header_row:
        # col1 boundary: midpoint between 'State' x and first content-column x
        first_content_x = min(w["x0"] for w in header_row)
        col1_max = (state_x + first_content_x) / 2
    else:
        col1_max = state_x + 50

    # col2 boundary: midpoint between first and second content column headers
    if len(header_row) >= 2:
        xs = sorted(set(round(w["x0"]) for w in header_row))
        # Look for a large gap that marks the start of col3
        gaps = [(xs[i + 1] - xs[i], xs[i], xs[i + 1]) for i in range(len(xs) - 1)]
        if gaps:
            big_gap = max(gaps, key=lambda g: g[0])
            col2_max = (big_gap[1] + big_gap[2]) / 2
        else:
            col2_max = first_content_x + 150
    else:
        col2_max = col1_max + 200

    return col1_max, col2_max


def _extract_rows(pdf_path: Path, y_merge_tol: int = 4) -> list[dict]:
    """
    Extract word rows from a PDF using word-position data.
    Returns list of {page, y, col1, col2, col3} in reading order.

    Words within y_merge_tol pixels of each other are merged into one
    logical row — this handles PDF rendering artefacts where the state
    label and the first county in the same visual line differ by 1-2px.
    """
    col1_max, col2_max = _detect_columns(pdf_path)

    all_rows: list[dict] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            words = page.extract_words(x_tolerance=3, y_tolerance=3)
            # Collect raw pixel-y → row mapping
            page_rows: dict[int, dict] = {}
            for w in words:
                y   = round(w["top"])
                x   = w["x0"]
                txt = w["text"]
                if y not in page_rows:
                    page_rows[y] = {"page": page_num, "y": y, "col1": [], "col2": [], "col3": []}
                if x < col1_max:
                    page_rows[y]["col1"].append(txt)
                elif x < col2_max:
                    page_rows[y]["col2"].append(txt)
                else:
                    page_rows[y]["col3"].append(txt)

            # Merge rows whose y-coordinates are within y_merge_tol of each other
            ys = sorted(page_rows)
            merged: list[dict] = []
            for y in ys:
                row = page_rows[y]
                if merged and abs(y - merged[-1]["y"]) <= y_merge_tol:
                    # Merge into previous logical row
                    merged[-1]["col1"].extend(row["col1"])
                    merged[-1]["col2"].extend(row["col2"])
                    merged[-1]["col3"].extend(row["col3"])
                else:
                    merged.append({
                        "page": page_num,
                        "y": y,
                        "col1": list(row["col1"]),
                        "col2": list(row["col2"]),
                        "col3": list(row["col3"]),
                    })
            all_rows.extend(merged)
    return all_rows


# ── Garbled-text cleaners ─────────────────────────────────────────────────────

# Metro area name pattern (garbled): e.g. "Flagstaff,AZMSA" or "Oakland-Fremont,CAHMFA"
_METRO_GARBLED = re.compile(
    r"^(.+?),([A-Z]{2})(MSA|HMFA|MDIV|HSD)(\[.*\])?$", re.IGNORECASE
)
# Clean up suffix like "[GOZone]"
_GO_ZONE = re.compile(r"\s*\[.*?\]", re.IGNORECASE)


def _clean_metro_name(raw: str) -> str | None:
    """
    Return cleaned metro area name from either:
      - garbled:  "Flagstaff,AZMSA"  → "Flagstaff, AZ MSA"
      - readable: "Flagstaff, AZ MSA" → same
    Returns None if the token doesn't look like a metro area name.
    """
    raw = raw.strip()
    # Try garbled pattern (no spaces between name, state, type)
    m = _METRO_GARBLED.match(raw)
    if m:
        name, st, mtype = m.group(1), m.group(2), m.group(3).upper()
        # The name part itself may be garbled CamelCase (e.g. "LosAngeles-LongBeach")
        name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
        return f"{name}, {st} {mtype}"
    # Already readable: check if it contains ", XX MSA/HMFA"
    if re.search(r",\s*[A-Z]{2}\s+(MSA|HMFA|MDIV|HSD)", raw, re.I):
        return _GO_ZONE.sub("", raw).strip()
    return None


_PAGE_NOISE = re.compile(r"^Page\s*\d+\s*of\s*\d+$", re.I)

def _clean_county_name(raw: str) -> str:
    """
    Add spaces before known suffix tokens that may be concatenated.
    e.g. "BarbourCounty"       → "Barbour County"
    e.g. "AleutiansWestCensus Area" → "Aleutians West Census Area"
    e.g. "JuneauCityandBorough"  → "Juneau City and Borough"
    """
    # Insert space before uppercase run after lowercase (CamelCase splitting)
    raw = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", raw)
    # Handle "Cityand" / "Cityand Borough" type gluing of 'and'
    raw = re.sub(r"(?i)(?<=[a-z])(and)(?=[A-Z])", r" \1 ", raw)
    return raw.strip()


def _is_noise(name: str) -> bool:
    """Return True if the name looks like a page footer/header, not a county."""
    if _PAGE_NOISE.match(name):
        return True
    if re.match(r"^\d+$", name):
        return True
    if len(name) < 3:
        return True
    return False


# ── Metro parser ──────────────────────────────────────────────────────────────

_ZCTA_RE = re.compile(r"^\d{5}$")

# State names that appear in col1 (col1 lists)
_KNOWN_STATES = set(_STATE_NAME_TO_ABBR.keys())


def _is_state(words: list[str]) -> bool:
    combined = "".join(words)
    if combined.upper() in _ALL_STATE_ABBRS and len(combined) == 2:
        return True
    return combined in _STATE_NAME_TO_ABBR or " ".join(words) in _STATE_NAME_TO_ABBR


def _state_abbr(words: list[str]) -> str | None:
    combined = "".join(words)
    if combined.upper() in _ALL_STATE_ABBRS and len(combined) == 2:
        return combined.upper()
    return _STATE_NAME_TO_ABBR.get(combined) or _STATE_NAME_TO_ABBR.get(" ".join(words))


def _parse_metro_rows(pdf_path: Path, year: int) -> list[dict]:
    """Parse metro (SDDA) rows. Handles ZCTA format (2016+) and name format."""
    word_rows = _extract_rows(pdf_path)

    # Detect format: 2016+ has 5-digit ZCTA codes in col3
    has_zctas = any(
        any(_ZCTA_RE.match(t) for t in r["col3"])
        for r in word_rows
    )

    rows: list[dict] = []
    current_state: str | None = None
    current_abbr:  str | None = None

    for row in word_rows:
        # Skip header rows
        col2_text = " ".join(row["col2"]).lower()
        if "metropolitan" in col2_text and "area" in col2_text and "state" in " ".join(row["col1"]).lower():
            continue
        if "section" in " ".join(row["col1"] + row["col2"]).lower():
            continue

        # Update current state if col1 has a state name
        if row["col1"] and _is_state(row["col1"]):
            current_state = " ".join(row["col1"])
            current_abbr  = _state_abbr(row["col1"])

        if not row["col2"]:
            continue

        area_raw = " ".join(row["col2"])
        area_name = _clean_metro_name(area_raw)
        if area_name is None:
            continue

        # Remove GO Zone / disaster tags
        area_name = _GO_ZONE.sub("", area_name).strip()

        if has_zctas:
            zctas = [t for t in row["col3"] if _ZCTA_RE.match(t)]
            for zcta in zctas:
                rows.append({
                    "designation_id":   f"{zcta}_{year}",
                    "zcta_code":        zcta,
                    "area_name":        area_name,
                    "state_abbr":       current_abbr,
                    "is_designated":    1,
                    "designation_year": year,
                    "basis_boost_pct":  30,
                    "effective_date":   f"{year}-01-01",
                    "hud_notice_ref":   "HUD DDA Metro PDF",
                })
        else:
            # Name-only format: one row per metro area
            rows.append({
                "designation_id":   f"{area_name.replace(' ', '_')}_{year}",
                "area_name":        area_name,
                "state_abbr":       current_abbr,
                "cbsa_code":        None,
                "fmr_area_code":    None,
                "is_designated":    1,
                "designation_year": year,
                "basis_boost_pct":  30,
                "effective_date":   f"{year}-01-01",
                "hud_notice_ref":   "HUD DDA Metro PDF",
            })

    return rows


# County-suffix pattern used to split a word stream into individual county names
_COUNTY_SUFFIX_SPLIT = re.compile(
    r"((?:Census\s+Area|City\s+and\s+Borough|City\s*&\s*Borough"
    r"|Borough|Parish|County|Municipio|Municipality|Island)"
    r"(?:\s*\(part\))?)",
    re.IGNORECASE,
)


def _split_county_word_stream(tokens: list[str]) -> list[str]:
    """
    Reconstruct county names from a flat word list where county names were
    spread across PDF columns.  Splits on known county-type suffixes.
    Returns list of county name strings (title-cased).
    """
    text = " ".join(tokens)
    # Split on suffix terminators, keeping the suffix
    parts = _COUNTY_SUFFIX_SPLIT.split(text)
    # parts alternates: [pre-suffix, suffix, pre-suffix, suffix, ...]
    counties: list[str] = []
    i = 0
    while i < len(parts) - 1:
        name = (parts[i].strip() + " " + parts[i + 1].strip()).strip()
        if name:
            counties.append(name.title())
        i += 2
    return counties


# ── Non-metro parser ──────────────────────────────────────────────────────────

def _parse_nonmetro_rows(pdf_path: Path, year: int) -> list[dict]:
    """Parse nonmetro (NMDDA) rows. Extracts county names by state.

    HUD nonmetro PDFs list 2+ counties per visual row across col2+col3. County
    names are reconstructed by collecting all tokens per state section and
    splitting on known county-type suffixes (County, Borough, Census Area, etc.).
    """
    word_rows = _extract_rows(pdf_path)
    rows: list[dict] = []
    current_abbr: str | None = None
    state_token_buffer: dict[str, list[str]] = {}
    # Track state order for output ordering
    state_order: list[str] = []

    _SKIP_HEADER_COL2 = {"state", "nonmetropolitan", "counties", "county", "equivalents",
                          "nonmetro", "or", "county", "equivalent"}

    for row in word_rows:
        all_text = " ".join(row["col1"] + row["col2"] + row["col3"]).lower()
        # Skip header rows (title, section citation, column headers)
        if "section" in all_text and ("42" in all_text or "irc" in all_text):
            continue
        col1_lower = " ".join(row["col1"]).lower()
        col2_lower = " ".join(row["col2"]).lower()
        if "state" in col1_lower and any(h in col2_lower for h in ("nonmetro", "county", "counties")):
            continue

        if row["col1"] and _is_state(row["col1"]):
            current_abbr = _state_abbr(row["col1"])
            if current_abbr and current_abbr not in state_token_buffer:
                state_order.append(current_abbr)

        if current_abbr is None:
            continue

        county_tokens = row["col2"] + row["col3"]
        if not county_tokens:
            continue

        # Skip header-only rows
        col2_words_lower = {w.lower() for w in row["col2"]}
        if col2_words_lower & _SKIP_HEADER_COL2 and len(row["col2"]) <= 2:
            continue

        # Strip [GO Zone] / [GO Zone] bracketed tags before buffering
        clean_tokens = [t for t in county_tokens if not re.match(r"^\[.*\]?$", t)]
        state_token_buffer.setdefault(current_abbr, []).extend(clean_tokens)

    # Split each state's word buffer into county names using suffix markers
    for abbr in state_order:
        tokens = state_token_buffer.get(abbr, [])
        county_names = _split_county_word_stream(tokens)
        for name in county_names:
            name = name.strip()
            if not name or _is_noise(name):
                continue
            # Skip very short or clearly non-county tokens
            if len(name) < 4:
                continue
            fips = _county_fips(abbr, name)
            pk = fips or f"{abbr}_{name.replace(' ', '_')}"
            rows.append({
                "designation_id":   f"{pk}_{year}",
                "county_fips":      fips,
                "area_name":        name,
                "state_abbr":       abbr,
                "is_designated":    1,
                "designation_year": year,
                "basis_boost_pct":  30,
                "effective_date":   f"{year}-01-01",
                "fmr_area_code":    None,
                "hud_notice_ref":   "HUD DDA Nonmetro PDF",
            })

    # Deduplicate by designation_id
    seen: set[str] = set()
    deduped = []
    for r in rows:
        if r["designation_id"] not in seen:
            seen.add(r["designation_id"])
            deduped.append(r)
    return deduped


# ── Bronze file locator ───────────────────────────────────────────────────────

def _find_pdf(year: int, kind: str) -> Path | None:
    """
    Locate a DDA PDF for *year* and *kind* ('metro' or 'nonmetro').
    Returns None if not present.
    """
    year_dir = BRONZE_DIR / str(year)
    if not year_dir.exists():
        raise FileNotFoundError(
            f"bronze_files/dda/{year}/ does not exist. "
            f"Run: python ingest_scripts/bronze_ingest/ingest_QCTDDA.py --dataset dda --year {year}"
        )
    # Preferred: standalone metro/nonmetro file
    preferred = year_dir / f"DDAs{year}_{kind}.pdf"
    if preferred.exists():
        return preferred
    # Fallback: FR notice PDF
    notice = year_dir / f"DDAs{year}_notice.pdf"
    if notice.exists():
        return notice
    return None


# ── Parquet writers (with dedup) ──────────────────────────────────────────────

def _source_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_with_dedup(
    new_rows: list[dict],
    pk_col: str,
    out_path: Path,
    source: Path,
    label: str,
) -> pd.DataFrame:
    bronze_id = _source_hash(source)
    run_id    = str(uuid.uuid4())
    ts        = datetime.now(timezone.utc).isoformat()

    for r in new_rows:
        r["bronze_source_id"] = bronze_id
        r["pipeline_run_id"]  = run_id
        r["ingested_at"]      = ts

    new_df = pd.DataFrame(new_rows)

    if out_path.exists():
        existing = pd.read_parquet(out_path)
        existing = existing[~existing[pk_col].isin(new_df[pk_col])]
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df

    combined = combined.drop_duplicates(subset=[pk_col], keep="last").reset_index(drop=True)

    for int_col in ["designation_year", "basis_boost_pct", "is_designated"]:
        if int_col in combined.columns:
            combined[int_col] = pd.to_numeric(combined[int_col], errors="coerce").astype("Int64")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(out_path, index=False)
    print(f"  Wrote {label}: {out_path.name} ({len(combined)} total rows, {len(new_df)} new)")
    return combined


# ── Public run function ───────────────────────────────────────────────────────

def run_dda_from_pdf(year: int) -> None:
    """
    Parse HUD DDA PDFs for *year* and write/merge silver parquets.
    Called automatically by silver_qct_dda.run_dda() for year < 2017.
    """
    metro_path    = _find_pdf(year, "metro")
    nonmetro_path = _find_pdf(year, "nonmetro")

    if metro_path is None and nonmetro_path is None:
        raise FileNotFoundError(
            f"No DDA PDFs found in bronze_files/dda/{year}/. "
            f"Run: python ingest_scripts/bronze_ingest/ingest_QCTDDA.py --dataset dda --year {year}"
        )

    # Metro (SDDA)
    if metro_path is not None:
        try:
            metro_rows = _parse_metro_rows(metro_path, year)
            if metro_rows:
                # Route to correct parquet based on format
                sample = metro_rows[0]
                if "zcta_code" in sample:
                    out = SILVER_DIR / f"silver_dda_{year}_metro_zcta.parquet"
                    pk  = "designation_id"
                    lbl = "metro SDDA ZCTAs"
                else:
                    out = SILVER_DIR / f"silver_dda_{year}_metro_area.parquet"
                    pk  = "designation_id"
                    lbl = "metro SDDA areas"
                _write_with_dedup(metro_rows, pk, out, metro_path, lbl)
                print(f"  Metro: {len(metro_rows)} rows")
            else:
                print(f"  WARN: no metro rows extracted from {metro_path.name}")
        except Exception as e:
            print(f"  WARN metro {year}: {e}")

    # Non-metro (NMDDA)
    if nonmetro_path is not None:
        try:
            nonmetro_rows = _parse_nonmetro_rows(nonmetro_path, year)
            if nonmetro_rows:
                out = SILVER_DIR / f"silver_dda_{year}_nonmetro_county.parquet"
                _write_with_dedup(nonmetro_rows, "designation_id", out,
                                  nonmetro_path, "nonmetro NMDDA")
                fips_count = sum(1 for r in nonmetro_rows if r.get("county_fips"))
                print(f"  Nonmetro: {len(nonmetro_rows)} rows, {fips_count} with FIPS")
            else:
                print(f"  WARN: no nonmetro rows extracted from {nonmetro_path.name}")
        except Exception as e:
            print(f"  WARN nonmetro {year}: {e}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build Silver DDA Parquet from HUD standalone PDFs (2008-2016).",
    )
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--year", type=int, nargs="+")
    group.add_argument("--year-range", type=int, nargs=2, metavar=("FROM", "TO"))
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    years = args.year or list(range(args.year_range[0], args.year_range[1] + 1))

    for year in years:
        print(f"\n--- DDA PDF {year} ---")
        if year >= 2017:
            print(f"  SKIP: {year} has xlsx -- use silver_qct_dda.py instead")
            continue
        try:
            run_dda_from_pdf(year)
        except FileNotFoundError as e:
            print(f"  SKIP: {e}")
        except Exception as e:
            print(f"  FAIL: {e}")
            raise


if __name__ == "__main__":
    main()
