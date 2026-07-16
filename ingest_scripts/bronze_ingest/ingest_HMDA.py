"""
ingest_HMDA.py
Downloads HMDA datasets (LAR, Panel, Transmittal Sheet) from files.ffiec.cfpb.gov.

All files are pipe-delimited (|), not CSV — extracted as .txt.

Source layout on files.ffiec.cfpb.gov:
  LAR  2022+      : modified-lar/combined-mlar/{y}/{y}_combined_mlar.zip
  LAR  2018-2021  : static-data/snapshot/{y}/{y}_public_lar_pipe.zip
  TS   2018-2024  : static-data/snapshot/{y}/{y}_public_ts_pipe.zip
  Panel 2018-2024 : static-data/snapshot/{y}/{y}_public_panel_pipe.zip

Note: Snapshot TS/Panel for the current filing year (e.g. 2025) are published
mid-year of the *following* year. The script skips gracefully when not yet available.

Usage:
    python scripts/ingest_HMDA.py --year 2023
    python scripts/ingest_HMDA.py --year 2021 2022 2023 --dataset panel ts
    python scripts/ingest_HMDA.py --year 2023 --no-extract
"""

from __future__ import annotations

import argparse
import zipfile
from io import BytesIO
from pathlib import Path

import requests

import _logbook

BRONZE_ROOT = Path(__file__).resolve().parents[2] / "bronze_files"
BASE_URL    = "https://files.ffiec.cfpb.gov"

FIRST_REFORM_YEAR   = 2018   # HMDA 2.0 — different schema pre-2018
MODIFIED_LAR_SINCE  = 2022   # combined modified-LAR published starting 2022


def _lar_url(year: int) -> str:
    if year >= MODIFIED_LAR_SINCE:
        return f"{BASE_URL}/modified-lar/combined-mlar/{year}/{year}_combined_mlar.zip"
    return f"{BASE_URL}/static-data/snapshot/{year}/{year}_public_lar_pipe.zip"


def _ts_url(year: int) -> str:
    return f"{BASE_URL}/static-data/snapshot/{year}/{year}_public_ts_pipe.zip"


def _panel_url(year: int) -> str:
    return f"{BASE_URL}/static-data/snapshot/{year}/{year}_public_panel_pipe.zip"


DATASET_URLS = {
    "lar":   _lar_url,
    "ts":    _ts_url,
    "panel": _panel_url,
}

# Extracted inner filename for each dataset
def _inner_name(dataset: str, year: int) -> str:
    if dataset == "lar":
        if year >= MODIFIED_LAR_SINCE:
            return f"{year}_combined_mlar.txt"
        return f"{year}_public_lar_pipe.txt"
    if dataset == "ts":
        return f"{year}_public_ts_pipe.txt"
    return f"{year}_public_panel_pipe.txt"


def make_session() -> requests.Session:
    # files.ffiec.cfpb.gov accepts the default python-requests UA.
    # Browser-spoofed UAs can trigger Akamai's JS-challenge path — don't use them.
    return requests.Session()


def download_streaming(
    session: requests.Session,
    url: str,
    chunk_size: int = 4 * 1024 * 1024,
) -> bytes:
    """
    Stream-download url into memory.
    HEAD is blocked by Akamai on ffiec.cfpb.gov — use GET throughout.
    Raises FileNotFoundError on 404, requests.HTTPError on other failures.
    """
    print(f"    GET {url}")
    r = session.get(url, stream=True, timeout=120)
    if r.status_code == 404:
        raise FileNotFoundError(404)
    r.raise_for_status()

    total = int(r.headers.get("Content-Length", 0))
    chunks: list[bytes] = []
    downloaded = 0
    last_report = 0

    for chunk in r.iter_content(chunk_size=chunk_size):
        if chunk:
            chunks.append(chunk)
            downloaded += len(chunk)
            if downloaded - last_report >= 50 * 1024 * 1024:
                if total:
                    print(f"    ... {downloaded/1_048_576:.0f} / {total/1_048_576:.0f} MB "
                          f"({downloaded/total*100:.0f}%)")
                else:
                    print(f"    ... {downloaded/1_048_576:.0f} MB")
                last_report = downloaded

    return b"".join(chunks)


def extract_zip(zip_bytes: bytes, inner_name: str, dest: Path) -> Path:
    """Extract a single file from a zip archive, renaming it to inner_name."""
    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        # Pick the first entry if the exact name isn't found (handles minor naming drift)
        target = inner_name if inner_name in names else (names[0] if names else None)
        if target is None:
            raise ValueError(f"Zip is empty. Expected: {inner_name}")
        if target != inner_name:
            print(f"    WARN: expected '{inner_name}', found '{target}' — renaming")
        out_path = dest / inner_name
        out_path.write_bytes(zf.read(target))
        return out_path


def ingest_dataset(
    session: requests.Session,
    dataset: str,
    year: int,
    output_dir: Path,
    extract: bool,
) -> None:
    url      = DATASET_URLS[dataset](year)
    zip_name = f"{dataset}_{year}.zip"
    txt_name = _inner_name(dataset, year)

    print(f"\n  [{dataset.upper()}] {year}")
    try:
        zip_bytes = download_streaming(session, url)
        size      = len(zip_bytes)
        print(f"    Downloaded {size / 1_048_576:.1f} MB")

        (output_dir / zip_name).write_bytes(zip_bytes)

        if extract:
            out_path = extract_zip(zip_bytes, txt_name, output_dir)
            print(f"    Extracted -> {out_path.name} ({out_path.stat().st_size / 1_048_576:.1f} MB)")
            _logbook.write("HMDA", dataset.upper(), year, "OK", out_path.name, size, url)
        else:
            print(f"    Saved zip -> {zip_name}")
            _logbook.write("HMDA", dataset.upper(), year, "OK", zip_name, size, url)

    except FileNotFoundError:
        reason = "Not yet published for this year"
        print(f"    SKIP: {reason}")
        _logbook.write("HMDA", dataset.upper(), year, "SKIP", zip_name, url=url, reason=reason)
    except Exception as e:
        reason = str(e)
        print(f"    FAIL: {reason}")
        _logbook.write("HMDA", dataset.upper(), year, "FAIL", zip_name, url=url, reason=reason)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download HMDA LAR/Panel/Transmittal snapshots from FFIEC/CFPB.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/ingest_HMDA.py --year 2023\n"
            "  python scripts/ingest_HMDA.py --year 2021 2022 2023 --dataset panel ts\n"
            "  python scripts/ingest_HMDA.py --year 2023 --no-extract\n"
        ),
    )
    parser.add_argument(
        "--year", type=int, nargs="+", required=True,
        help=f"One or more filing years ({FIRST_REFORM_YEAR}+)",
    )
    parser.add_argument(
        "--dataset", nargs="+", choices=["lar", "panel", "ts"],
        default=["lar", "panel", "ts"],
        metavar="DATASET",
        help="Datasets to download: lar, panel, ts (default: all three)",
    )
    parser.add_argument(
        "--no-extract", action="store_true",
        help="Save zip archives without extracting",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    bad = [y for y in args.year if y < FIRST_REFORM_YEAR]
    if bad:
        print(f"WARNING: {bad} predate 2018 HMDA Reform — skipping (different schema/source)")
        args.year = [y for y in args.year if y >= FIRST_REFORM_YEAR]
    if not args.year:
        return

    BRONZE_ROOT.mkdir(parents=True, exist_ok=True)
    session = make_session()

    for year in args.year:
        print(f"\n── Year {year} ──────────────────────────")
        year_dir = BRONZE_ROOT / "hmda" / str(year)
        year_dir.mkdir(parents=True, exist_ok=True)

        for dataset in args.dataset:
            ingest_dataset(
                session=session,
                dataset=dataset,
                year=year,
                output_dir=year_dir,
                extract=not args.no_extract,
            )

    print("\nDone.")


if __name__ == "__main__":
    main()
