"""
Download every Boletim Epidemiológico published by the Brazilian
Ministério da Saúde from the public listing at:

    https://www.gov.br/saude/pt-br/centrais-de-conteudo/publicacoes/boletins/epidemiologicos

PDFs are saved under ./boletins/<year>/<original-filename>.pdf and existing
files are skipped, so the script is safe to re-run to pick up new editions.

After every run two reports are written into the output directory:
    manifest.csv  — every boletim discovered, with its disk status
    failures.csv  — subset of manifest rows whose status is "failed"

Run again normally to pick up new boletins and retry any previous failures.
Use --retry-failed to re-attempt only the URLs listed in failures.csv,
skipping the (slow) listing crawl.

Requirements:
    pip install requests beautifulsoup4
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from ..common import ExtractResult

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"

BASE_URL = "https://www.gov.br/saude/pt-br/centrais-de-conteudo/publicacoes/boletins/epidemiologicos"
YEAR_URL = BASE_URL + "/edicoes/{year}"
FIRST_YEAR = 2019
HEADERS = {
    # The portal sometimes returns 403 to bare python-requests user agents.
    "User-Agent": (
        "Mozilla/5.0 (compatible; BoletinsDownloader/1.0; "
        "+https://www.gov.br/saude)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
}
REQUEST_TIMEOUT = 60
DELAY_BETWEEN_REQUESTS = 1.0  # be polite to the public server
MAX_RETRIES = 3

MANIFEST_FILENAME = "manifest.csv"
FAILURES_FILENAME = "failures.csv"

STATUS_DOWNLOADED = "downloaded"
STATUS_EXISTING = "existing"
STATUS_FAILED = "failed"

log = logging.getLogger("boletins")


@dataclass
class Entry:
    year: int
    entry_url: str
    pdf_url: str = ""
    filename: str = ""
    status: str = ""
    error: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )


# ---------------------------------------------------------------------------
# HTTP / scraping helpers
# ---------------------------------------------------------------------------

def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def get(session: requests.Session, url: str, stream: bool = False) -> requests.Response:
    """GET with retry/backoff. Raises on final failure."""
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


def collect_year_listing_pages(session: requests.Session, year: int) -> list[str]:
    """Walk Plone pagination for one year and return every listing-page URL.

    gov.br/Plone does not emit a rel=next link; pagination is driven by
    incrementing ?b_start:int=N by however many entries were on the current
    page until a page yields no new boletim entry links.
    """
    base = YEAR_URL.format(year=year)
    listing_pages: list[str] = []
    seen_entries: set[str] = set()

    offset = 0
    while True:
        url = f"{base}?b_start:int={offset}"
        log.info("fetching listing page %s", url)
        listing_pages.append(url)

        html = get(session, url).text
        soup = BeautifulSoup(html, "html.parser")

        new_entries: set[str] = set()
        for anchor in soup.find_all("a", href=True):
            href = urljoin(url, anchor["href"])
            parsed = urlparse(href)
            clean = urlunparse(parsed._replace(query="", fragment=""))
            if "/epidemiologicos/edicoes/" not in clean:
                continue
            if _LISTING_ONLY_PATH_RE.match(parsed.path):
                continue
            if clean not in seen_entries:
                new_entries.add(clean)

        if not new_entries:
            break

        seen_entries.update(new_entries)
        offset += len(new_entries) - 1 # step by actual page size, not a fixed constant
        time.sleep(DELAY_BETWEEN_REQUESTS)

    return listing_pages


_LISTING_ONLY_PATH_RE = re.compile(r".*/edicoes(?:/\d{4})?/?$")
_PDF_HINTS = ("@@download/file", "/at_download/file")


def _looks_like_pdf_url(url: str) -> bool:
    lower = url.lower()
    if lower.endswith(".pdf") or lower.endswith(".pdf/view"):
        return True
    return any(hint in lower for hint in _PDF_HINTS)


def _strip_view_suffix(url: str) -> str:
    """`.../foo.pdf/view` → `.../foo.pdf` (Plone view URLs)."""
    parsed = urlparse(url)
    if parsed.path.endswith("/view"):
        return urlunparse(parsed._replace(path=parsed.path[: -len("/view")]))
    return url


def extract_boletim_links(session: requests.Session, listing_url: str) -> list[str]:
    """Find every link on a listing page that points at a boletim entry."""
    html = get(session, listing_url).text
    soup = BeautifulSoup(html, "html.parser")

    found: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = urljoin(listing_url, anchor["href"])
        # Drop query string (pagination uses ?b_start:int=N) and fragment
        parsed = urlparse(href)
        clean = urlunparse(parsed._replace(query="", fragment=""))

        if "/epidemiologicos/edicoes/" not in clean:
            continue
        # Skip listing pages themselves: ".../edicoes" or ".../edicoes/<year>"
        if _LISTING_ONLY_PATH_RE.match(parsed.path):
            continue
        found.add(clean)

    return sorted(found)


def normalize_to_pdf_url(
    session: requests.Session, entry_url: str, _depth: int = 0
) -> str | None:
    """Resolve a boletim entry URL to a directly-downloadable PDF URL.

    Handles the four shapes seen on gov.br/Plone in practice:
      1. `.../boletim-X.pdf`                  → returned as-is
      2. `.../boletim-X.pdf/view`             → strip `/view`
      3. Plone File item folder               → `<url>/@@download/file`
      4. Plone Document page with an Anexo    → scrape `<a>` / `<embed>` /
                                                 `<iframe>` / child item
    """
    if _depth > 2:
        return None

    parsed = urlparse(entry_url)
    path = parsed.path

    if path.endswith(".pdf"):
        return entry_url
    if path.endswith(".pdf/view"):
        return _strip_view_suffix(entry_url)

    # Plone Files expose canonical download endpoints
    for endpoint in ("/@@download/file", "/at_download/file"):
        candidate = entry_url.rstrip("/") + endpoint
        try:
            head = session.head(candidate, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            ctype = head.headers.get("Content-Type", "").lower()
            if head.status_code == 200 and "pdf" in ctype:
                return candidate
        except requests.RequestException:
            pass

    # Scrape the entry page
    try:
        html = get(session, entry_url).text
    except RuntimeError:
        return None
    soup = BeautifulSoup(html, "html.parser")

    # 1) Direct anchors that look like a PDF
    for anchor in soup.find_all("a", href=True):
        href = urljoin(entry_url, anchor["href"])
        if _looks_like_pdf_url(href):
            return _strip_view_suffix(href)

    # 2) Embedded PDFs in <embed>/<iframe>/<object>
    for tag in soup.find_all(["embed", "iframe", "object"]):
        for attr in ("src", "data"):
            src = tag.get(attr)
            if not src:
                continue
            src_abs = urljoin(entry_url, src)
            if _looks_like_pdf_url(src_abs) or src_abs.lower().endswith(".pdf"):
                return _strip_view_suffix(src_abs)

    # 3) Child Plone items: anchors pointing one level deeper under this entry
    parent_path = path.rstrip("/")
    for anchor in soup.find_all("a", href=True):
        href = urljoin(entry_url, anchor["href"])
        child_parsed = urlparse(href)
        child_path = child_parsed.path.rstrip("/")
        if (
            child_path.startswith(parent_path + "/")
            and child_path != parent_path
            and "/edicoes/" in child_path
            and not _LISTING_ONLY_PATH_RE.match(child_path)
        ):
            resolved = normalize_to_pdf_url(session, href, _depth + 1)
            if resolved:
                return resolved

    return None


def filename_from_url(pdf_url: str) -> str:
    """Pick a stable, filesystem-safe filename from a PDF URL."""
    path = urlparse(pdf_url).path.rstrip("/")
    last = path.rsplit("/", 1)[-1]

    if last == "file":
        slug = path.rsplit("/", 3)[-3] if "/@@download/" in path else last
        last = slug + ".pdf"

    if not last.lower().endswith(".pdf"):
        last += ".pdf"

    return re.sub(r"[^A-Za-z0-9._-]+", "-", last).strip("-")


# ---------------------------------------------------------------------------
# Per-entry processing
# ---------------------------------------------------------------------------

def attempt_entry(
    session: requests.Session, year: int, entry_url: str, output_dir: Path
) -> Entry:
    """Resolve and download a single entry; never raises.

    Returns a fully-populated Entry with status set to one of:
      "downloaded" — newly written
      "existing"   — found on disk, untouched
      "failed"     — could not be retrieved (see entry.error)
    """
    entry = Entry(year=year, entry_url=entry_url)
    try:
        pdf_url = normalize_to_pdf_url(session, entry_url)
        if not pdf_url:
            entry.status = STATUS_FAILED
            entry.error = "no PDF link found on entry page"
            return entry
        entry.pdf_url = pdf_url
        entry.filename = filename_from_url(pdf_url)
        destination = output_dir / str(year) / entry.filename

        if destination.exists() and destination.stat().st_size > 0:
            entry.status = STATUS_EXISTING
            return entry

        log.info("downloading %s", pdf_url)
        response = get(session, pdf_url, stream=True)
        content_type = response.headers.get("Content-Type", "").lower()
        if "pdf" not in content_type and not pdf_url.lower().endswith(".pdf"):
            entry.status = STATUS_FAILED
            entry.error = f"unexpected content-type: {content_type or 'unknown'}"
            return entry

        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp = destination.with_suffix(destination.suffix + ".part")
        with tmp.open("wb") as out:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if chunk:
                    out.write(chunk)
        tmp.rename(destination)
        entry.status = STATUS_DOWNLOADED
    except Exception as exc:  # keep going even if one boletim fails
        entry.status = STATUS_FAILED
        entry.error = str(exc)
    return entry


# ---------------------------------------------------------------------------
# Listing-mode and retry-mode workflows
# ---------------------------------------------------------------------------

def download_year(session: requests.Session, year: int, output_dir: Path) -> list[Entry]:
    """Crawl one year's listing and return an Entry per boletim discovered."""
    log.info("=== year %d ===", year)
    try:
        listing_pages = collect_year_listing_pages(session, year)
    except RuntimeError as error:
        log.error("could not load listing for %d: %s", year, error)
        # Surface the year-level failure as a single Entry so it shows up in failures.csv.
        return [Entry(
            year=year,
            entry_url=YEAR_URL.format(year=year),
            status=STATUS_FAILED,
            error=f"listing crawl failed: {error}",
        )]

    entry_urls: set[str] = set()
    for listing_url in listing_pages:
        entry_urls.update(extract_boletim_links(session, listing_url))
        time.sleep(DELAY_BETWEEN_REQUESTS)

    log.info("year %d: %d entries found", year, len(entry_urls))

    entries: list[Entry] = []
    for entry_url in sorted(entry_urls):
        entry = attempt_entry(session, year, entry_url, output_dir)
        entries.append(entry)
        if entry.status == STATUS_FAILED:
            log.warning("FAILED %s — %s", entry_url, entry.error)
        if entry.status != STATUS_EXISTING:
            time.sleep(DELAY_BETWEEN_REQUESTS)

    return entries


def retry_from_failures(
    session: requests.Session, failures_path: Path, output_dir: Path
) -> list[Entry]:
    """Re-attempt only the rows listed in a previous failures.csv.

    Existing files on disk are treated as authoritative — those rows are
    silently promoted to "existing" without hitting the network.
    """
    if not failures_path.exists():
        raise FileNotFoundError(f"failures file not found: {failures_path}")

    with failures_path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    log.info("retrying %d previously-failed entries from %s", len(rows), failures_path)
    entries: list[Entry] = []
    for row in rows:
        year = int(row["year"])
        entry_url = row["entry_url"]
        entry = attempt_entry(session, year, entry_url, output_dir)
        entries.append(entry)
        if entry.status == STATUS_FAILED:
            log.warning("still failing %s — %s", entry_url, entry.error)
        else:
            log.info("recovered %s (%s)", entry_url, entry.status)
        if entry.status != STATUS_EXISTING:
            time.sleep(DELAY_BETWEEN_REQUESTS)

    return entries


# ---------------------------------------------------------------------------
# Manifest I/O
# ---------------------------------------------------------------------------

_FIELDS = ["year", "entry_url", "pdf_url", "filename", "status", "error", "timestamp"]


def write_manifest(entries: list[Entry], output_dir: Path) -> tuple[Path, Path]:
    """Write manifest.csv (everything) and failures.csv (status=failed).

    Both files are overwritten on every run so they always reflect the latest
    known state. Returns the two paths.
    """
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
# CLI
# ---------------------------------------------------------------------------

def extract(
    out_dir: Path = DATA_DIR / "bulletins",
    from_year: int = FIRST_YEAR,
    to_year: int = datetime.now().year,
) -> ExtractResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    session = make_session()
    entries: list[Entry] = []
    for year in range(from_year, to_year + 1):
        entries.extend(download_year(session, year, out_dir))
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
        "--output-dir", type=Path, default=Path("./boletins"),
        help="Where to save PDFs and manifest/failures CSVs (default: ./boletins)",
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
        "--retry-failed", type=Path, nargs="?", const=None, default=False,
        metavar="PATH",
        help=(
            "Skip the listing crawl and re-attempt only the URLs recorded in a "
            "previous failures.csv. If no PATH is given, defaults to "
            "<output-dir>/failures.csv."
        ),
    )
    return parser.parse_args()


def _summarize(entries: list[Entry]) -> dict[str, int]:
    counts = {STATUS_DOWNLOADED: 0, STATUS_EXISTING: 0, STATUS_FAILED: 0}
    for e in entries:
        counts[e.status] = counts.get(e.status, 0) + 1
    return counts


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    args = parse_args()
    session = make_session()

    if args.retry_failed is not False:  # flag was passed
        failures_path = args.retry_failed or (args.output_dir / FAILURES_FILENAME)
        try:
            entries = retry_from_failures(session, failures_path, args.output_dir)
        except FileNotFoundError as exc:
            log.error(str(exc))
            return 2
    else:
        entries = []
        for year in range(args.from_year, args.to_year + 1):
            entries.extend(download_year(session, year, args.output_dir))

    manifest_path, failures_path = write_manifest(entries, args.output_dir)
    counts = _summarize(entries)

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
