from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from .models import Article, State
from .parsers import article_filename, utc_now_iso

log = logging.getLogger("ebc")


class Storage:
    """Owns all on-disk artefacts for one scraping run."""

    def __init__(self, output_dir: Path):
        self.root = output_dir
        self.state_path = output_dir / "state.json"
        self.manifest_path = output_dir / "manifest.jsonl"
        self.listings_dir = output_dir / "listings"
        self.articles_dir = output_dir / "articles"

        for directory in (self.root, self.listings_dir, self.articles_dir):
            directory.mkdir(parents=True, exist_ok=True)

    # ----- state ----------------------------------------------------------

    def load_state(self) -> State | None:
        if not self.state_path.exists():
            return None
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        return State(**data)

    def save_state(self, state: State) -> None:
        """Atomic write via temp file + rename to guard against mid-write crashes."""
        state.updated_at = utc_now_iso()
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(state.to_json(), encoding="utf-8")
        tmp.replace(self.state_path)

    # ----- manifest -------------------------------------------------------

    def load_seen_urls(self) -> set[str]:
        """Read the manifest at startup to skip already-fetched URLs."""
        if not self.manifest_path.exists():
            return set()
        seen: set[str] = set()
        with self.manifest_path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("skipping malformed manifest line")
                    continue
                # Only treat successful fetches as done — retry failures.
                if record.get("url") and record.get("http_status") == 200:
                    seen.add(record["url"])
        return seen

    def append_manifest(self, article: Article) -> None:
        with self.manifest_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(article), ensure_ascii=False) + "\n")
            handle.flush()

    # ----- raw HTML -------------------------------------------------------

    def save_listing_html(self, page: int, html: str) -> None:
        (self.listings_dir / f"page-{page:04d}.html").write_text(html, encoding="utf-8")

    def save_article_html(self, article: Article, html: str) -> str:
        name = article_filename(article.url, article.published_at)
        path = self.articles_dir / name
        path.write_text(html, encoding="utf-8")
        return str(path.relative_to(self.root))
