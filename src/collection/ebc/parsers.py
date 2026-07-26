from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Iterator
from urllib.parse import urlencode, urlparse

from bs4 import BeautifulSoup

from .models import Article

SEARCH_BASE = "https://busca.ebc.com.br/sites/{site}/nodes"

_DATE_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})(?:\s*às\s*(\d{2}):(\d{2}))?")
_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def build_search_url(site: str, query: str, types: list[str], per_page: int, page: int) -> str:
    params: list[tuple[str, str]] = [("per_page", str(per_page)), ("q", query)]
    for entry_type in types:
        params.append(("types[]", entry_type))
    if page > 1:
        params.append(("page", str(page)))
    return f"{SEARCH_BASE.format(site=site)}?{urlencode(params)}"


def parse_total_pages(soup: BeautifulSoup) -> tuple[int | None, int | None]:
    """Return (total_pages, total_results) discovered from a listing page."""
    total_results: int | None = None
    counter = soup.select_one("div.info-result .result span")
    if counter:
        match = re.search(r"([\d.]+)", counter.get_text(strip=True))
        if match:
            total_results = int(match.group(1).replace(".", ""))

    total_pages: int | None = None
    pagination = soup.select_one("div.pagination")
    if pagination:
        candidates = pagination.select("a[href]")
        page_numbers = [
            int(match.group(1))
            for anchor in candidates
            if (match := re.search(r"[?&]page=(\d+)", anchor["href"]))
        ]
        if page_numbers:
            total_pages = max(page_numbers)
        elif soup.select("ul#results > li"):
            total_pages = 1
    elif soup.select("ul#results > li"):
        total_pages = 1
    return total_pages, total_results


def parse_listing(soup: BeautifulSoup, page_number: int) -> Iterator[Article]:
    """Yield Article records (without HTTP fields) from a search-result page."""
    fetched_at = utc_now_iso()
    for item in soup.select("ul#results > li"):
        heading = item.select_one("h4.media-heading a")
        if not heading or not heading.get("href"):
            continue

        url = heading["href"].strip()
        title = heading.get_text(strip=True)
        snippet_node = item.select_one("div.media-body > p")
        snippet = snippet_node.get_text(" ", strip=True) if snippet_node else ""

        published_raw = ""
        published_node = item.select_one("p.info-new")
        if published_node:
            published_raw = published_node.get_text(" ", strip=True)

        site_label = ""
        site_node = item.select_one("p.info-new .site")
        if site_node:
            site_label = site_node.get_text(strip=True)
            published_raw = published_raw.replace(site_label, "").strip()

        thumb = item.select_one("img.media-object")
        thumbnail_url = thumb["src"].strip() if thumb and thumb.get("src") else None

        yield Article(
            url=url,
            title=title,
            snippet=snippet,
            published_at=parse_brazilian_datetime(published_raw),
            published_raw=published_raw,
            site_label=site_label,
            section=section_from_url(url),
            thumbnail_url=thumbnail_url,
            listing_page=page_number,
            local_path=None,
            http_status=None,
            error=None,
            fetched_at=fetched_at,
        )


def parse_brazilian_datetime(text: str) -> str | None:
    """Convert 'DD/MM/YYYY às HH:MM' to ISO 8601. None if unparseable."""
    match = _DATE_RE.search(text or "")
    if not match:
        return None
    day, month, year, hour, minute = match.groups()
    try:
        dt = datetime(int(year), int(month), int(day), int(hour or 0), int(minute or 0))
    except ValueError:
        return None
    return dt.isoformat()


def section_from_url(url: str) -> str | None:
    """Extract the section slug from an Agência Brasil URL path."""
    parts = [p for p in urlparse(url).path.split("/") if p]
    return parts[0] if parts else None


def article_filename(url: str, published_at: str | None) -> str:
    """Build a stable, sortable filename: <YYYY-MM-DD>_<slug>_<6-char-hash>.html"""
    slug = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1] or "index"
    slug = _FILENAME_SAFE.sub("-", slug).strip("-")[:80]
    date_prefix = published_at[:10] if published_at else "0000-00-00"
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:6]
    return f"{date_prefix}_{slug}_{digest}.html"
