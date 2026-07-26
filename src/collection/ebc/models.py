from __future__ import annotations

import json
from dataclasses import asdict, dataclass


@dataclass
class Article:
    """Metadata for a single article, persisted as one line of manifest.jsonl."""
    url: str
    title: str
    snippet: str
    published_at: str | None      # ISO 8601 if parseable, else original text
    published_raw: str            # original "DD/MM/YYYY às HH:MM" string
    site_label: str               # e.g. "Agência Brasil"
    section: str | None           # e.g. "saude", "cultura" (from URL path)
    thumbnail_url: str | None
    listing_page: int
    local_path: str | None        # path of saved HTML, relative to output dir
    http_status: int | None
    error: str | None
    fetched_at: str               # ISO 8601 UTC


@dataclass
class State:
    """Resume cursor + run-level bookkeeping. Persisted as state.json."""
    query: str
    site: str
    types: list[str]
    per_page: int
    total_pages: int | None
    total_results: int | None
    last_completed_page: int      # 0 means nothing done yet; resume at page 1
    started_at: str
    updated_at: str
    pages_processed: int
    articles_fetched: int
    articles_failed: int

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)
