import argparse
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

import _logbook

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

HUD_API_KEY = os.getenv("HUD_API_KEY")
if not HUD_API_KEY:
    sys.exit("HUD_API_KEY not found in .env")

BRONZE_ROOT = Path(__file__).resolve().parents[2] / "bronze_files"

# ── URL routing ───────────────────────────────────────────────────────────────
# QCT sources (confirmed from huduser.gov, July 2026):
#   2026+     : www.huduser.gov/portal/datasets/qct/qct_data_{year}.xlsx
#   2015-2025 : docs.huduser.gov/portal/datasets/qct/qct_data_{year}.xlsx
#   2013-2014 : docs.huduser.gov  combined xls  qct_data_2013_2014.xls
#   2010-2012 : docs.huduser.gov  combined xlsx qct_data_2010_2011_2012.xlsx
#   2007-2009 : docs.huduser.gov  combined xlsx qct_data_2007_2008_2009.xlsx
#   2003-2006 : docs.huduser.gov  combined xlsx qct_data_2003_2004_2005_2006.xlsx
#
# DDA sources:
#   2017+     : www.huduser.gov/portal/datasets/qct/{year}-DDAs-Data-Used-to-Designate.xlsx
#   2008-2016 : HUD standalone PDFs  DDA{YEAR}M.PDF (metro/SDDA) + DDA{YEAR}NM.PDF (nonmetro/NMDDA)
#   2003-2007 : Same pattern attempted; fallback to FR notice PDF if 404.

_QCT_BUNDLE: dict[int, str] = {}
for _y in range(2003, 2007): _QCT_BUNDLE[_y] = "qct_data_2003_2004_2005_2006.xlsx"
for _y in range(2007, 2010): _QCT_BUNDLE[_y] = "qct_data_2007_2008_2009.xlsx"
for _y in range(2010, 2013): _QCT_BUNDLE[_y] = "qct_data_2010_2011_2012.xlsx"
for _y in range(2013, 2015): _QCT_BUNDLE[_y] = "qct_data_2013_2014.xls"

# HUD hosts standalone metro/nonmetro DDA PDFs at these URLs (confirmed July 2026).
# Base URL uses /Datasets/qct/ (capital D) for pre-2017; both cases accepted by server.
_HUD_DDA_BASE = "https://www.huduser.gov/portal/Datasets/qct"

# FR notice fallback URLs (full Federal Register notice PDF, not the standalone lists).
# Only used when the standalone M/NM PDFs are unavailable (2003-2007 era).
_DDA_FR_NOTICE_URLS: dict[int, str] = {
    2007: "https://www.govinfo.gov/content/pkg/FR-2006-09-28/pdf/06-8197.pdf",
    2006: "https://www.govinfo.gov/content/pkg/FR-2005-08-22/pdf/05-16605.pdf",
    2005: "https://www.govinfo.gov/content/pkg/FR-2004-11-30/pdf/04-26328.pdf",
    2004: "https://www.govinfo.gov/content/pkg/FR-2003-12-19/pdf/03-31268.pdf",
    2003: "https://www.govinfo.gov/content/pkg/FR-2002-12-12/pdf/02-31081.pdf",
}


def _qct_urls(year: int) -> list[str]:
    base_docs = "https://docs.huduser.gov/portal/datasets/qct"
    base_www  = "https://www.huduser.gov/portal/datasets/qct"
    if year >= 2026:
        return [f"{base_www}/qct_data_{year}.xlsx"]
    elif year >= 2015:
        return [f"{base_docs}/qct_data_{year}.xlsx"]
    elif year in _QCT_BUNDLE:
        return [f"{base_docs}/{_QCT_BUNDLE[year]}"]
    return []


def _dda_xlsx_urls(year: int) -> list[str]:
    if year >= 2017:
        base = "https://www.huduser.gov/portal/datasets/qct"
        return [f"{base}/{year}-DDAs-Data-Used-to-Designate.xlsx"]
    return []


def _dda_metro_pdf_urls(year: int) -> list[str]:
    """Metro (SDDA) standalone PDF — HUD hosts these for 2003-2016."""
    if year >= 2017:
        return []
    return [
        f"{_HUD_DDA_BASE}/DDA{year}M.PDF",
        f"{_HUD_DDA_BASE}/DDA{year}M.pdf",
    ]


def _dda_nonmetro_pdf_urls(year: int) -> list[str]:
    """Non-metro (NMDDA) standalone PDF — HUD hosts these for 2003-2016."""
    if year >= 2017:
        return []
    return [
        f"{_HUD_DDA_BASE}/DDA{year}NM.PDF",
        f"{_HUD_DDA_BASE}/DDA{year}NM.pdf",
    ]


def _dda_fr_notice_url(year: int) -> str | None:
    """FR notice fallback for years where standalone M/NM PDFs are unavailable."""
    return _DDA_FR_NOTICE_URLS.get(year)


# ── session ───────────────────────────────────────────────────────────────────

def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Authorization": f"Bearer {HUD_API_KEY}",
    })
    try:
        session.get("https://www.huduser.gov/", timeout=15)
    except Exception:
        pass
    return session


# ── download helpers ──────────────────────────────────────────────────────────

_EXCEL_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "application/octet-stream",
}

_PDF_TYPES = {
    "application/pdf",
    "application/octet-stream",
}


def _download(session: requests.Session, url: str, accept_types: set[str]) -> bytes:
    print(f"  GET {url}")
    r = session.get(url, timeout=60, allow_redirects=True)
    ct = r.headers.get("Content-Type", "")
    if r.status_code != 200:
        raise FileNotFoundError(f"HTTP {r.status_code} from {url}")
    if not any(t in ct for t in accept_types):
        raise FileNotFoundError(f"Unexpected content-type {ct!r} from {url}")
    return r.content


def download_xlsx(session: requests.Session, urls: list[str]) -> bytes:
    last_err = None
    for url in urls:
        try:
            return _download(session, url, _EXCEL_TYPES)
        except Exception as e:
            last_err = str(e)
            print(f"    skip: {last_err}")
    raise FileNotFoundError(f"No working xlsx URL. Last error: {last_err}")


def download_pdf(session: requests.Session, url: str) -> bytes:
    return _download(session, url, _PDF_TYPES)


# ── ingest functions ──────────────────────────────────────────────────────────

def ingest_qct(session: requests.Session, year: int, output_dir: Path) -> None:
    print(f"\nIngesting QCT {year}...")
    urls = _qct_urls(year)
    if not urls:
        print(f"  NO DATA: no source known for QCT {year}")
        _logbook.write("QCT", "qct", year, "NO_DATA", reason="no source published by HUD")
        return

    bundle_name = _QCT_BUNDLE.get(year)
    filename = bundle_name if bundle_name else f"qct_data_{year}.xlsx"
    dest = output_dir / filename

    if dest.exists():
        print(f"  Already exists: {filename}")
        _logbook.write("QCT", "qct", year, "OK", filename, dest.stat().st_size, urls[0],
                       reason="already downloaded")
        return

    try:
        data = download_xlsx(session, urls)
        dest.write_bytes(data)
        print(f"  Saved {filename} ({len(data):,} bytes)")
        _logbook.write("QCT", "qct", year, "OK", filename, len(data), urls[0])
    except Exception as e:
        print(f"  FAIL: {e}")
        _logbook.write("QCT", "qct", year, "FAIL", filename, reason=str(e))


def ingest_dda(session: requests.Session, year: int, output_dir: Path) -> None:
    print(f"\nIngesting DDA {year}...")

    # -- Excel (2017+) --------------------------------------------------------
    xlsx_urls = _dda_xlsx_urls(year)
    if xlsx_urls:
        filename = f"{year}-DDAs-Data-Used-to-Designate.xlsx"
        dest = output_dir / filename
        if dest.exists():
            print(f"  xlsx already exists: {filename}")
            _logbook.write("DDA", "dda", year, "OK", filename, dest.stat().st_size,
                           xlsx_urls[0], reason="already downloaded")
        else:
            try:
                data = download_xlsx(session, xlsx_urls)
                dest.write_bytes(data)
                print(f"  Saved xlsx {filename} ({len(data):,} bytes)")
                _logbook.write("DDA", "dda", year, "OK", filename, len(data), xlsx_urls[0])
            except Exception as e:
                print(f"  xlsx FAIL: {e}")
                _logbook.write("DDA", "dda", year, "FAIL", filename, reason=str(e))

    # -- PDFs (2003-2016): separate metro (SDDA) and non-metro (NMDDA) files ---
    if year < 2017:
        for label, filename, url_fn in [
            ("metro",    f"DDAs{year}_metro.pdf",    _dda_metro_pdf_urls),
            ("nonmetro", f"DDAs{year}_nonmetro.pdf", _dda_nonmetro_pdf_urls),
        ]:
            dest = output_dir / filename
            urls = url_fn(year)
            if dest.exists():
                print(f"  PDF already exists: {filename}")
                _logbook.write(f"DDA-PDF-{label}", "dda", year, "OK", filename,
                               dest.stat().st_size, urls[0], reason="already downloaded")
                continue
            downloaded = False
            for url in urls:
                try:
                    data = download_pdf(session, url)
                    dest.write_bytes(data)
                    print(f"  Saved {label} PDF {filename} ({len(data):,} bytes)")
                    _logbook.write(f"DDA-PDF-{label}", "dda", year, "OK",
                                   filename, len(data), url)
                    downloaded = True
                    break
                except Exception as e:
                    print(f"    {label} PDF skip {url}: {e}")
            if not downloaded:
                # Fallback: FR notice PDF (covers both metro+nonmetro in one doc)
                fr_url = _dda_fr_notice_url(year)
                fr_file = f"DDAs{year}_notice.pdf"
                fr_dest = output_dir / fr_file
                if fr_dest.exists():
                    print(f"  FR notice already exists: {fr_file}")
                elif fr_url:
                    try:
                        data = download_pdf(session, fr_url)
                        fr_dest.write_bytes(data)
                        print(f"  Saved FR notice {fr_file} ({len(data):,} bytes)")
                        _logbook.write(f"DDA-PDF-{label}", "dda", year, "OK",
                                       fr_file, len(data), fr_url, reason="FR notice fallback")
                    except Exception as e:
                        print(f"  FR notice FAIL for {year}: {e}")
                        _logbook.write(f"DDA-PDF-{label}", "dda", year, "FAIL",
                                       fr_file, reason=str(e))
                else:
                    print(f"  WARN: no {label} PDF source for DDA {year}")
                    _logbook.write(f"DDA-PDF-{label}", "dda", year, "NO_DATA",
                                   reason="no standalone PDF; no FR fallback known")
                break  # only try FR notice once, not once per label

    if not xlsx_urls and year >= 2017:
        print(f"  NO DATA: no source known for DDA {year}")
        _logbook.write("DDA", "dda", year, "NO_DATA", reason="no source published by HUD")


# ── entry point ───────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest QCT/DDA bronze files from HUD.")
    parser.add_argument("--year", type=int, nargs="+", required=True,
                        help="One or more designation years (e.g. --year 2024 2025)")
    parser.add_argument("--dataset", choices=["all", "qct", "dda"], default="all")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    BRONZE_ROOT.mkdir(parents=True, exist_ok=True)

    print("Warming up session...")
    session = make_session()

    for year in args.year:
        print(f"\n--- Year {year} ---")
        if args.dataset in ("all", "qct"):
            qct_dir = BRONZE_ROOT / "qct" / str(year)
            qct_dir.mkdir(parents=True, exist_ok=True)
            ingest_qct(session, year, qct_dir)
        if args.dataset in ("all", "dda"):
            dda_dir = BRONZE_ROOT / "dda" / str(year)
            dda_dir.mkdir(parents=True, exist_ok=True)
            ingest_dda(session, year, dda_dir)

    print("\nDone.")


if __name__ == "__main__":
    main()
