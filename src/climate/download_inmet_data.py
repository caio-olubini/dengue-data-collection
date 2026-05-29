"""
Download annual historical meteorological data from INMET:

    https://portal.inmet.gov.br/dadoshistoricos

Each year is published as a single ZIP file at:
    https://portal.inmet.gov.br/uploads/dadoshistoricos/<year>.zip

ZIPs are saved under ./inmet/<year>.zip and existing files are skipped,
so the script is safe to re-run.

After every run two reports are written to the output directory:
    manifest.csv  — every year attempted, with its disk status
    failures.csv  — subset whose status is "failed"

Requirements:
    pip install requests
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

from ..common import ExtractResult

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"

BASE_URL = "https://portal.inmet.gov.br/uploads/dadoshistoricos"
FIRST_YEAR = 2000
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; INMETDownloader/1.0; "
        "+https://portal.inmet.gov.br)"
    ),
    "Accept": "application/zip,application/octet-stream,*/*;q=0.8",
}
REQUEST_TIMEOUT = 120
DELAY_BETWEEN_REQUESTS = 1.0
MAX_RETRIES = 3

MANIFEST_FILENAME = "manifest.csv"
FAILURES_FILENAME = "failures.csv"

STATUS_DOWNLOADED = "downloaded"
STATUS_EXISTING = "existing"
STATUS_FAILED = "failed"

log = logging.getLogger("inmet")


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


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def get(session: requests.Session, url: str, stream: bool = False) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(url, timeout=REQUEST_TIMEOUT, stream=stream)
            response.raise_for_status()
            return response
        except requests.RequestException as error:
            last_error = error
            log.warning("attempt %d/%d failed for %s (%s)", attempt, MAX_RETRIES, url, error)
            time.sleep(2 * attempt)
    raise RuntimeError(f"giving up on {url}") from last_error


def attempt_year(session: requests.Session, year: int, output_dir: Path) -> Entry:
    url = f"{BASE_URL}/{year}.zip"
    filename = f"{year}.zip"
    entry = Entry(year=year, url=url, filename=filename)
    destination = output_dir / filename

    if destination.exists() and destination.stat().st_size > 0:
        entry.status = STATUS_EXISTING
        log.info("skipping %d (already on disk)", year)
        return entry

    try:
        log.info("downloading %s", url)
        response = get(session, url, stream=True)
        content_type = response.headers.get("Content-Type", "").lower()
        if "zip" not in content_type and "octet-stream" not in content_type:
            entry.status = STATUS_FAILED
            entry.error = f"unexpected content-type: {content_type or 'unknown'}"
            return entry

        output_dir.mkdir(parents=True, exist_ok=True)
        tmp = destination.with_suffix(".zip.part")
        with tmp.open("wb") as out:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if chunk:
                    out.write(chunk)
        tmp.rename(destination)
        entry.status = STATUS_DOWNLOADED
        log.info("saved %s", destination)
    except Exception as exc:
        entry.status = STATUS_FAILED
        entry.error = str(exc)
        log.warning("FAILED %d — %s", year, exc)

    return entry


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


def extract(
    out_dir: Path = DATA_DIR / "climate",
    from_year: int = FIRST_YEAR,
    to_year: int = datetime.now().year,
) -> ExtractResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    session = make_session()
    entries: list[Entry] = []
    for year in range(from_year, to_year + 1):
        entry = attempt_year(session, year, out_dir)
        entries.append(entry)
        if entry.status != STATUS_EXISTING:
            time.sleep(DELAY_BETWEEN_REQUESTS)
    manifest_path, failures_path = write_manifest(entries, out_dir)
    counts = {STATUS_DOWNLOADED: 0, STATUS_EXISTING: 0, STATUS_FAILED: 0}
    for e in entries:
        counts[e.status] = counts.get(e.status, 0) + 1
    return ExtractResult(
        downloaded=counts[STATUS_DOWNLOADED],
        existing=counts[STATUS_EXISTING],
        failed=counts[STATUS_FAILED],
        manifest_path=manifest_path,
        failures_path=failures_path,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("./inmet"),
        help="Where to save ZIP files and CSVs (default: ./inmet)",
    )
    parser.add_argument(
        "--from-year", type=int, default=FIRST_YEAR,
        help=f"Earliest year to download (default: {FIRST_YEAR})",
    )
    parser.add_argument(
        "--to-year", type=int, default=datetime.now().year,
        help="Latest year to download (default: current year)",
    )
    parser.add_argument(
        "--retry-failed", action="store_true",
        help="Re-attempt only the years listed in failures.csv",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    args = parse_args()
    session = make_session()

    if args.retry_failed:
        failures_path = args.output_dir / FAILURES_FILENAME
        if not failures_path.exists():
            log.error("failures file not found: %s", failures_path)
            return 2
        with failures_path.open("r", encoding="utf-8", newline="") as fh:
            years = [int(row["year"]) for row in csv.DictReader(fh)]
        log.info("retrying %d previously-failed years", len(years))
    else:
        years = list(range(args.from_year, args.to_year + 1))

    entries: list[Entry] = []
    for year in years:
        entry = attempt_year(session, year, args.output_dir)
        entries.append(entry)
        if entry.status != STATUS_EXISTING:
            time.sleep(DELAY_BETWEEN_REQUESTS)

    manifest_path, failures_path = write_manifest(entries, args.output_dir)

    counts = {STATUS_DOWNLOADED: 0, STATUS_EXISTING: 0, STATUS_FAILED: 0}
    for e in entries:
        counts[e.status] = counts.get(e.status, 0) + 1

    log.info(
        "done. downloaded=%d, existing=%d, failed=%d",
        counts[STATUS_DOWNLOADED], counts[STATUS_EXISTING], counts[STATUS_FAILED],
    )
    log.info("manifest → %s", manifest_path)
    if counts[STATUS_FAILED]:
        log.info("failures → %s  (re-run with --retry-failed to retry)", failures_path)

    return 0 if counts[STATUS_FAILED] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
