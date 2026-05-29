"""Download SINAN dengue yearly CSVs from the Brazilian Ministry of Health S3 bucket.

Why this builds a URL instead of scraping the portal page
----------------------------------------------------------
The dataset page at dadosabertos.saude.gov.br is a JavaScript app, so the
real download link is not in its static HTML.  Behind that page, every yearly
file lives at a fixed, predictable address on Amazon S3:

    https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/SINAN/Dengue/csv/DENGBR<YY>.csv.zip

where <YY> is the two-digit year (2010 → "10", 2024 → "24").  Because the
address is deterministic we construct it directly — no API, no HTML parsing,
nothing that breaks when the portal re-renders.

Each file is a zipped CSV; we download the .zip in memory and extract the CSV.
Already-present files are skipped, making the script safe to re-run.

After every run two reports are written to the output directory:
    manifest.csv  — every year attempted, with its disk status
    failures.csv  — subset whose status is "failed"

CLI usage
---------
    python sinan_dengue.py --year 2024
    python sinan_dengue.py --year 2024 2023 2022
    python sinan_dengue.py --year 2024 --out ./data/epidemiological/SINAN --keep-zip

Requires: requests  (pip install requests)
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

from ..common import ExtractResult

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"

S3_CSV_TEMPLATE = (
    "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br"
    "/SINAN/Dengue/csv/DENGBR{two_digit_year}.csv.zip"
)
EARLIEST_YEAR = 2000

STATUS_DOWNLOADED = "downloaded"
STATUS_EXISTING = "existing"
STATUS_FAILED = "failed"

MANIFEST_FILENAME = "manifest.csv"
FAILURES_FILENAME = "failures.csv"


@dataclass
class Entry:
    year: int
    url: str
    filename: str = ""
    status: str = ""
    error: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def csv_zip_url(year: int) -> str:
    return S3_CSV_TEMPLATE.format(two_digit_year=f"{year % 100:02d}")


def probe_year(year: int, timeout: int = 10) -> bool:
    try:
        r = requests.head(csv_zip_url(year), timeout=timeout)
        return r.status_code == 200
    except requests.RequestException:
        return False


def list_available_years(start: int = EARLIEST_YEAR, end: int = 2030) -> list[int]:
    return [y for y in range(start, end + 1) if probe_year(y)]


# ---------------------------------------------------------------------------
# Download / extraction
# ---------------------------------------------------------------------------

def download_year(year: int, output_dir: Path, keep_zip: bool = False) -> Entry:
    """Download and extract the dengue CSV for *year* into *output_dir*.

    Skips years whose CSV is already on disk. Returns a populated Entry.
    """
    url = csv_zip_url(year)
    entry = Entry(year=year, url=url)

    # Skip if the extracted CSV already exists (any file matching DENGBR<YY>*.csv)
    existing = list(output_dir.glob(f"DENGBR{year % 100:02d}*.csv"))
    if existing:
        entry.status = STATUS_EXISTING
        entry.filename = existing[0].name
        return entry

    print(f"[{year}] downloading {url}")
    try:
        response = requests.get(url, timeout=300)
        response.raise_for_status()
        zip_bytes = response.content
    except requests.HTTPError as error:
        status = error.response.status_code if error.response else "?"
        print(f"[{year}] not available (HTTP {status}) — skipping")
        entry.status = STATUS_FAILED
        entry.error = f"HTTP {status}"
        return entry
    except requests.RequestException as error:
        entry.status = STATUS_FAILED
        entry.error = str(error)
        return entry

    if keep_zip:
        zip_path = output_dir / f"DENGBR{year % 100:02d}.csv.zip"
        zip_path.write_bytes(zip_bytes)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        csv_members = [m for m in archive.namelist() if m.lower().endswith(".csv")]
        if not csv_members:
            print(f"[{year}] warning: archive contained no .csv file")
            entry.status = STATUS_FAILED
            entry.error = "archive contained no .csv file"
            return entry
        for member in csv_members:
            target = output_dir / Path(member).name
            target.write_bytes(archive.read(member))
            print(f"[{year}] extracted {target}")
        entry.filename = Path(csv_members[0]).name

    entry.status = STATUS_DOWNLOADED
    return entry


# ---------------------------------------------------------------------------
# Manifest I/O
# ---------------------------------------------------------------------------

_FIELDS = ["year", "url", "filename", "status", "error", "timestamp"]


def write_manifest(entries: list[Entry], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / MANIFEST_FILENAME
    failures_path = output_dir / FAILURES_FILENAME

    rows = [asdict(e) for e in entries]
    failed_rows = [r for r in rows if r["status"] == STATUS_FAILED]

    for path, data in ((manifest_path, rows), (failures_path, failed_rows)):
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=_FIELDS)
            writer.writeheader()
            writer.writerows(data)

    return manifest_path, failures_path


# ---------------------------------------------------------------------------
# Top-level extract()
# ---------------------------------------------------------------------------

def extract(
    out_dir: Path = DATA_DIR / "epidemiological" / "SINAN",
    from_year: int = EARLIEST_YEAR,
    to_year: int = datetime.now().year,
    keep_zip: bool = False,
) -> ExtractResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    entries = [download_year(y, out_dir, keep_zip) for y in range(from_year, to_year + 1)]
    manifest_path, failures_path = write_manifest(entries, out_dir)
    counts = {STATUS_DOWNLOADED: 0, STATUS_EXISTING: 0, STATUS_FAILED: 0}
    for e in entries:
        counts[e.status] = counts.get(e.status, 0) + 1
    print(
        f"\nSINAN dengue: downloaded={counts[STATUS_DOWNLOADED]}"
        f"  existing={counts[STATUS_EXISTING]}"
        f"  failed={counts[STATUS_FAILED]}"
    )
    print(f"Manifest: {manifest_path}")
    return ExtractResult(
        downloaded=counts[STATUS_DOWNLOADED],
        existing=counts[STATUS_EXISTING],
        failed=counts[STATUS_FAILED],
        manifest_path=manifest_path,
        failures_path=failures_path,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download SINAN dengue CSVs by year.")
    parser.add_argument("--year", type=int, nargs="+", required=True,
                        help=f"One or more years from {EARLIEST_YEAR} onward")
    parser.add_argument("--out", type=Path,
                        default=DATA_DIR / "epidemiological" / "SINAN",
                        help="Output directory")
    parser.add_argument("--keep-zip", action="store_true",
                        help="Also keep the downloaded .zip")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    out_of_range = [y for y in args.year if y < EARLIEST_YEAR]
    if out_of_range:
        sys.exit(f"No data before {EARLIEST_YEAR}: {out_of_range}")
    extract(out_dir=args.out, from_year=min(args.year), to_year=max(args.year),
            keep_zip=args.keep_zip)


if __name__ == "__main__":
    main()
