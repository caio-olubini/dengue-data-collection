"""
Scrape news articles from EBC's search engine (default: Agência Brasil).

Source URL pattern:
    https://busca.ebc.com.br/sites/agenciabrasil/nodes
        ?per_page=100&q=<query>&types[]=noticia&types[]=pagina&page=<N>

The scraper is re-runnable: it keeps a state file recording the last page it
finished, and a manifest of every article already fetched. If interrupted, the
next run resumes from the next page and skips articles already in the manifest.

Output layout (under --output-dir):
    state.json            — resume cursor + run metadata
    manifest.jsonl        — one JSON record per fetched article (append-only)
    listings/<page>.html  — raw search-result page HTML (for audit/reprocess)
    articles/<file>.html  — saved article HTML

Requirements:
    pip install requests beautifulsoup4
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from .http_client import http_get, make_session
from .models import Article, State
from .parsers import build_search_url, parse_listing, parse_total_pages, utc_now_iso
from .storage import Storage
from ..common import ExtractResult

log = logging.getLogger("ebc")


def fetch_article(session: requests.Session, article: Article, storage: Storage) -> Article:
    """Download one article and update its HTTP fields."""
    try:
        response = http_get(session, article.url)
        article.http_status = response.status_code
        article.local_path = storage.save_article_html(article, response.text)
    except requests.RequestException as error:
        article.http_status = getattr(error.response, "status_code", None)
        article.error = str(error)
        log.warning("article failed: %s (%s)", article.url, error)
    article.fetched_at = utc_now_iso()
    return article


def discover_totals(session: requests.Session, args: argparse.Namespace) -> tuple[int | None, int | None]:
    """Fetch page 1 once to discover total_pages and total_results."""
    url = build_search_url(args.site, args.query, args.types, args.per_page, page=1)
    soup = BeautifulSoup(http_get(session, url).text, "html.parser")
    return parse_total_pages(soup)


def initialize_state(args: argparse.Namespace, total_pages: int | None, total_results: int | None) -> State:
    now = utc_now_iso()
    return State(
        query=args.query,
        site=args.site,
        types=list(args.types),
        per_page=args.per_page,
        total_pages=total_pages,
        total_results=total_results,
        last_completed_page=0,
        started_at=now,
        updated_at=now,
        pages_processed=0,
        articles_fetched=0,
        articles_failed=0,
    )


def reconcile_state_with_args(state: State, args: argparse.Namespace) -> None:
    """Refuse to resume if CLI args differ from the saved state."""
    mismatches = []
    if state.query != args.query:
        mismatches.append(f"query: {state.query!r} vs {args.query!r}")
    if state.site != args.site:
        mismatches.append(f"site: {state.site!r} vs {args.site!r}")
    if state.types != list(args.types):
        mismatches.append(f"types: {state.types} vs {list(args.types)}")
    if state.per_page != args.per_page:
        mismatches.append(f"per_page: {state.per_page} vs {args.per_page}")
    if mismatches:
        raise SystemExit(
            "State file does not match current arguments:\n  "
            + "\n  ".join(mismatches)
            + "\nUse a different --output-dir, or pass --restart to start over."
        )


def scrape(args: argparse.Namespace) -> ExtractResult:
    storage = Storage(args.output_dir)
    session = make_session()

    if args.restart and storage.state_path.exists():
        log.info("--restart given: removing previous state and manifest")
        storage.state_path.unlink(missing_ok=True)
        storage.manifest_path.unlink(missing_ok=True)

    state = storage.load_state()
    if state is None:
        log.info("no previous state — discovering totals")
        total_pages, total_results = discover_totals(session, args)
        state = initialize_state(args, total_pages, total_results)
        storage.save_state(state)
        log.info("found %s results across %s pages", state.total_results, state.total_pages)
    else:
        reconcile_state_with_args(state, args)
        log.info("resuming: last completed page = %d, total pages = %s",
                 state.last_completed_page, state.total_pages)

    seen_urls = storage.load_seen_urls()
    log.info("manifest already contains %d successful articles", len(seen_urls))

    last_page = min(state.total_pages or args.max_pages, args.max_pages)
    start_page = state.last_completed_page + 1

    for page_number in range(start_page, last_page + 1):
        page_url = build_search_url(args.site, args.query, args.types, args.per_page, page_number)
        log.info("=== page %d/%d === %s", page_number, last_page, page_url)
        try:
            response = http_get(session, page_url)
        except requests.RequestException as error:
            log.error("could not load listing page %d: %s", page_number, error)
            return 1

        storage.save_listing_html(page_number, response.text)
        soup = BeautifulSoup(response.text, "html.parser")

        articles_on_page = list(parse_listing(soup, page_number))
        log.info("page %d: %d items", page_number, len(articles_on_page))

        for article in articles_on_page:
            if article.url in seen_urls:
                continue
            time.sleep(args.delay)
            fetch_article(session, article, storage)
            storage.append_manifest(article)
            seen_urls.add(article.url)
            if article.error:
                state.articles_failed += 1
            else:
                state.articles_fetched += 1

        state.last_completed_page = page_number
        state.pages_processed += 1
        storage.save_state(state)
        time.sleep(args.delay)

    log.info(
        "done. pages=%d articles_ok=%d articles_failed=%d",
        state.pages_processed, state.articles_fetched, state.articles_failed,
    )
    return ExtractResult(
        downloaded=state.articles_fetched,
        existing=len(seen_urls),
        failed=state.articles_failed,
        manifest_path=storage.manifest_path,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--query", "-q", required=True, help="Search term (e.g. 'dengue')")
    parser.add_argument("--site", default="agenciabrasil",
                        help="EBC sub-site slug (default: agenciabrasil)")
    parser.add_argument("--types", nargs="+", default=["noticia", "pagina"],
                        help="Content types to include (default: noticia pagina)")
    parser.add_argument("--per-page", type=int, default=100,
                        help="Results per listing page (default: 100)")
    parser.add_argument("--max-pages", type=int, default=10_000,
                        help="Hard ceiling on pages to fetch (default: no practical limit)")
    parser.add_argument("--output-dir", type=Path, default=Path("./data/news"),
                        help="Where to store state, manifest, and HTML (default: ./data/news)")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="Seconds to sleep between HTTP requests (default: 1.0)")
    parser.add_argument("--restart", action="store_true",
                        help="Wipe state.json and manifest.jsonl before starting")
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    result = scrape(args)
    return 0 if result.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
