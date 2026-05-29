from __future__ import annotations

import logging
import time

import requests

REQUEST_TIMEOUT = 60
MAX_RETRIES = 3
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; EBCNewsScraper/1.0; research/archival use)"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}

log = logging.getLogger("ebc")


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def http_get(session: requests.Session, url: str) -> requests.Response:
    """GET with retry/backoff. Raises requests.RequestException on final failure."""
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return response
        except requests.RequestException as error:
            last_error = error
            log.warning("attempt %d/%d failed for %s (%s)", attempt, MAX_RETRIES, url, error)
            time.sleep(2 ** attempt)  # 2s, 4s, 8s
    raise last_error  # type: ignore[misc]
