"""
Shared ingest logbook writer.
All three ingest scripts append to bronze_files/ingest_logbook.md.

Columns:
  Timestamp (UTC) | Source | Dataset | Year | Status | File | Size | URL | Reason
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

_ROOT        = Path(__file__).resolve().parents[2]
LOGBOOK_PATH = _ROOT / "bronze_files" / "ingest_logbook.md"

_HEADER = (
    "# Ingest Logbook\n\n"
    "| Timestamp (UTC) | Source | Dataset | Year | Status | File | Size | URL | Reason |\n"
    "|---|---|---|---|---|---|---|---|---|\n"
)

_STATUS_ICON = {"OK": "OK", "SKIP": "SKIP", "FAIL": "FAIL"}


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _fmt_size(size_bytes: int) -> str:
    if not size_bytes:
        return ""
    if size_bytes >= 1_048_576:
        return f"{size_bytes / 1_048_576:.1f} MB"
    return f"{size_bytes / 1_024:.1f} KB"


def write(
    source: str,
    dataset: str,
    year: int | str,
    status: str,
    filename: str = "",
    size_bytes: int = 0,
    url: str = "",
    reason: str = "",
) -> None:
    """
    Append one row to the unified logbook.

    Args:
        source:     Script identity — 'HMDA', 'AMI', 'QCT', 'DDA'
        dataset:    Sub-type within that source — e.g. 'LAR', 'Panel', 'section8'
        year:       Filing/designation year
        status:     'OK', 'SKIP', or 'FAIL'
        filename:   Saved filename (basename only)
        size_bytes: Bytes downloaded (0 = unknown/not downloaded)
        url:        URL that was used (or attempted)
        reason:     Error message for SKIP/FAIL; empty for OK
    """
    LOGBOOK_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not LOGBOOK_PATH.exists():
        LOGBOOK_PATH.write_text(_HEADER, encoding="utf-8")

    row = (
        f"| {_ts()} "
        f"| {source} "
        f"| {dataset} "
        f"| {year} "
        f"| {status} "
        f"| {filename} "
        f"| {_fmt_size(size_bytes)} "
        f"| {url} "
        f"| {reason} |\n"
    )
    with LOGBOOK_PATH.open("a", encoding="utf-8") as fh:
        fh.write(row)
