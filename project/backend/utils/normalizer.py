"""Normalisation layer — runs before the pipeline sees the question.

Pass A — deterministic regex/lookup rules (zero tokens, instant)
Pass B — small LLM call, only fires when unknown domain terms are detected

Output:
  NormalizeResult.text         -> normalised question for the pipeline
  NormalizeResult.changes      -> list of substitutions made (shown in UI)
  NormalizeResult.fips_in_text -> 11-digit FIPS code extracted from question
  NormalizeResult.year_in_text -> 4-digit year extracted from question
  NormalizeResult.geo_missing  -> True when no geographic anchor found
  NormalizeResult.used_llm     -> True if Pass B fired
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

_THIS_YEAR = datetime.now().year
_LAST_YEAR = _THIS_YEAR - 1

_RULES: list[tuple[str, str, str]] = [
    # BHK / bedroom regional terms
    (r"\ball\s*bhk\b",           "all bedroom sizes (1 through 8)", "all BHK → all bedroom sizes"),
    (r"\ball\s*bedrooms?\b",     "all bedroom sizes (1 through 8)", "all bedrooms → all bedroom sizes"),
    (r"\b(\d+)\s*bhk\b",        r"\1 bedroom",                     "N BHK → N bedroom"),
    (r"\bbhk\b",                 "bedroom",                         "BHK → bedroom"),
    (r"\bstudio\b",              "0 bedroom (efficiency/studio)",   "studio → 0 bedroom"),
    (r"\bflat\b",                "unit",                            "flat → unit"),

    # AMI / program type
    (r"\b60\s*%\s*ami\b",        "50% AMI (program_type=VLI, note: 60% AMI rent = VLI limit x1.2)", "60% AMI → VLI mapping"),
    (r"\b50\s*%\s*ami\b",        "50% AMI (program_type=VLI)",     "50% AMI → VLI"),
    (r"\b30\s*%\s*ami\b",        "30% AMI (program_type=ELI)",     "30% AMI → ELI"),
    (r"\b80\s*%\s*ami\b",        "80% AMI (program_type=LI)",      "80% AMI → LI"),
    (r"\bvery\s*low\s*income\b", "Very Low Income (50% AMI, program_type=VLI)", "Very Low Income → VLI"),
    (r"\bextremely\s*low\s*income\b", "Extremely Low Income (30% AMI, program_type=ELI)", "Extremely Low Income → ELI"),
    (r"\blow\s*income\b",        "Low Income (80% AMI, program_type=LI)", "Low Income → LI"),

    # Abbreviations
    (r"\bLIHTC\b", "Low Income Housing Tax Credit (LIHTC)", "LIHTC → expanded"),
    (r"\bQCT\b",   "Qualified Census Tract (QCT)",          "QCT → expanded"),
    (r"\bDDA\b",   "Difficult Development Area (DDA)",       "DDA → expanded"),
    (r"\bSDDA\b",  "Small Difficult Development Area (SDDA)", "SDDA → expanded"),
    (r"\bNMDDA\b", "Non-Metropolitan Difficult Development Area (NMDDA)", "NMDDA → expanded"),
    (r"\bHMDA\b",  "Home Mortgage Disclosure Act (HMDA)",   "HMDA → expanded"),
    (r"\bAMI\b",   "Area Median Income (AMI)",              "AMI → expanded"),
    (r"\bMFI\b",   "Median Family Income (MFI)",            "MFI → expanded"),
    (r"\bFMR\b",   "Fair Market Rent (FMR)",                "FMR → expanded"),
    (r"\bHUD\b",   "U.S. Department of Housing and Urban Development (HUD)", "HUD → expanded"),
    (r"\bELI\b",   "Extremely Low Income (30% AMI, program_type=ELI)", "ELI → expanded"),
    (r"\bVLI\b",   "Very Low Income (50% AMI, program_type=VLI)", "VLI → expanded"),
    (r"\bLI\b",    "Low Income (80% AMI, program_type=LI)", "LI → expanded"),
    (r"\bIRC\b",   "Internal Revenue Code (IRC)",           "IRC → expanded"),
    (r"\b§\s*42\b","Section 42 of the Internal Revenue Code", "§42 → Section 42"),
    (r"\bUW\b",    "underwriting",                          "UW → underwriting"),
    (r"\bPIS\b",   "placed in service (pis_year)",          "PIS → placed in service"),

    # Time references
    (r"\bthis\s+year\b",       f"year {_THIS_YEAR}", f"this year → {_THIS_YEAR}"),
    (r"\blast\s+year\b",       f"year {_LAST_YEAR}", f"last year → {_LAST_YEAR}"),
    (r"\bcurrent\s+year\b",    f"year {_THIS_YEAR}", f"current year → {_THIS_YEAR}"),
    (r"\bcurrent\s+limits?\b", f"year {_THIS_YEAR} income limits", f"current limits → {_THIS_YEAR} limits"),
    (r"\blatest\s+limits?\b",  f"year {_THIS_YEAR} income limits", f"latest limits → {_THIS_YEAR} limits"),

    # Common phrasings
    (r"\bmax(?:imum)?\s+rent\b",   "maximum allowable gross rent (max_rent)", "max rent → max_rent"),
    # NOTE: replacement deliberately omits "30%" — if the user already wrote
    # "30% basis boost", a rule that re-adds "30%" produces a duplicated
    # "30% 30% eligible basis boost..." (a real bug that occurred).
    (r"\bbasis\s+boost\b",         "eligible basis boost under IRC Section 42(d)(5)(B)", "basis boost → §42 boost"),
    (r"\bboost\s+eligible\b",      "eligible for the 30% basis boost",        "boost eligible → §42 eligible"),
    (r"\bfair\s+lending\b",        "fair lending risk (HMDA denial rate disparity)", "fair lending → HMDA disparity"),
    (r"\blending\s+risk\b",        "lending risk (HMDA denial rates and disparity ratio)", "lending risk → HMDA risk"),
]

_UNKNOWN_SIGNALS = [r"\b[A-Z]{3,6}\b", r"\b\d+\s*(?:br|bd|bdrm)\b"]
_KNOWN_CAPS = {
    "FIPS", "MSA", "AMI", "IRC", "HUD", "GSE", "HAS", "NOT", "AND", "FOR", "THE",
    # LIHTC domain abbreviations already handled by Pass A — don't re-trigger Pass B
    "QCT", "DDA", "SDDA", "NMDDA", "HMDA", "LIHTC", "ELI", "VLI", "FMR", "MFI",
    "HUD", "IRC", "UW", "PIS", "AMI",
}

_GEO_PATTERNS = [
    r"\b\d{11}\b", r"\bfips\b", r"\btract\b", r"\bcounty\b",
    r"\bstate\b",  r"\bcbsa\b", r"\bmsa\b",   r"\bcity\b",
]

_FIPS_RE = re.compile(r"\b(\d{11})\b")
_YEAR_RE = re.compile(r"\b(20\d{2})\b")

_PASS_B_SYSTEM = f"""\
You are a LIHTC terminology normalizer. Your ONLY job is to rewrite the input
question using standard LIHTC underwriting language.

Rules:
- Expand all abbreviations (e.g. "UW" -> "underwriting", "PIS" -> "placed in service")
- Replace regional terms with standard ones (e.g. "2BHK" -> "2 bedroom")
- Resolve time references ("this year" -> "year {_THIS_YEAR}", "last year" -> "year {_LAST_YEAR}")
- Do NOT answer the question. Do NOT add opinions. Do NOT add data.
- Output ONLY the rewritten question. No explanation. No preamble.
"""


@dataclass
class NormalizeResult:
    original: str
    text: str
    changes: list[str] = field(default_factory=list)
    fips_in_text: str | None = None
    year_in_text: int | None = None
    geo_missing: bool = False
    used_llm: bool = False


class Normalizer:
    def __init__(self, llm_client=None) -> None:
        self._llm = llm_client

    def normalize(self, question: str) -> NormalizeResult:
        text, changes = _pass_a(question)
        unknown = _detect_unknown(text)
        used_llm = False

        if unknown and self._llm is not None:
            result = self._pass_b(text)
            if result:
                text = result
                used_llm = True

        fips_m = _FIPS_RE.search(question)
        year_m = _YEAR_RE.search(question)

        return NormalizeResult(
            original=question,
            text=text,
            changes=changes,
            fips_in_text=fips_m.group(1) if fips_m else None,
            year_in_text=int(year_m.group(1)) if year_m else None,
            geo_missing=not _has_geo_anchor(question),
            used_llm=used_llm,
        )

    def _pass_b(self, text: str) -> str | None:
        try:
            return self._llm.complete(
                [
                    {"role": "system", "content": _PASS_B_SYSTEM},
                    {"role": "user",   "content": text},
                ],
                label="Normalizer Pass B",
            ).strip() or None
        except Exception:
            return None


def _pass_a(text: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    for pattern, replacement, description in _RULES:
        new_text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        if new_text != text:
            changes.append(description)
            text = new_text
    return text, changes


def _detect_unknown(normalized: str) -> list[str]:
    found: list[str] = []
    for pat in _UNKNOWN_SIGNALS:
        for m in re.findall(pat, normalized):
            if re.fullmatch(r"\d+", m) or m.upper() in _KNOWN_CAPS:
                continue
            found.append(m)
    return list(set(found))


def _has_geo_anchor(text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in _GEO_PATTERNS)
