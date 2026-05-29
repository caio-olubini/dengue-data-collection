"""Google Trends wrappers using pytrends (public web interface).

Replaces the private trends/v1beta endpoint used in the original R scripts.
Same DataFrame column conventions are preserved so the extraction scripts work
without changes.

    pip install pytrends

Three public functions mirror the R originals:
    get_graph()       ← getGraph2.R       (weekly interest time series)
    get_top_topics()  ← getTopTopics2.R   (related topics)
    get_top_queries() ← getTopQueries2.R  (related queries)

try_gtrends_api() wraps all three safely, returning (DataFrame | None, error | None)
instead of raising — matching fun.R behaviour.

Rate limiting: pytrends scrapes the public Trends site, which throttles heavy
traffic. Each function sleeps `sleep` seconds after the request (default 2 s).
Increase to 5–10 s for large loops to avoid 429s.
"""

from __future__ import annotations

import calendar
import time
from datetime import date
from typing import Literal

import pandas as pd
from pytrends.request import TrendReq

try:
    from pytrends.exceptions import TooManyRequestsError
except ImportError:
    # Older pytrends versions don't expose this as a named exception
    TooManyRequestsError = Exception  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(hl: str = "pt-BR", tz: int = 180) -> TrendReq:
    """Create a TrendReq session with sensible retry defaults."""
    return TrendReq(hl=hl, tz=tz, retries=3, backoff_factor=2.0, timeout=(4, 27))


def _to_timeframe(start_ym: str, end_ym: str) -> str:
    """Convert YYYY-MM pair to the 'YYYY-MM-DD YYYY-MM-DD' string pytrends expects."""
    y, m = int(end_ym[:4]), int(end_ym[5:7])
    last = calendar.monthrange(y, m)[1]
    return f"{start_ym}-01 {end_ym}-{last:02d}"


def _time_label(start_date: str | None, end_date: str | None) -> str:
    today = date.today().strftime("%Y-%m")
    return f"{start_date or '2004-01'} {end_date or today}"


# ---------------------------------------------------------------------------
# Public API functions
# ---------------------------------------------------------------------------

def get_graph(
    term: str,
    geo: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    client: TrendReq | None = None,
    sleep: float = 2.0,
) -> pd.DataFrame:
    """Weekly search-interest time series for *term*.

    Equivalent to getGraph2() / trends/v1beta/graph.
    Ranges longer than ~270 days return weekly data; shorter ranges return
    daily data — match the R behaviour by keeping the 5-year window.

    Parameters
    ----------
    term : str
        A search keyword or Freebase topic ID (e.g. '/m/09wsg').
    geo : str, optional
        ISO-3166-2 code ('BR', 'BR-SP', …).  None → worldwide.
    start_date, end_date : str, optional
        'YYYY-MM' boundaries.  Default: 2004-01 to current month.
    client : TrendReq, optional
        Reuse an existing session to share cookies/throttling state.
    sleep : float
        Seconds to wait after the request to avoid rate-limiting.

    Returns
    -------
    DataFrame with columns: value, date, geo, time, keyword, gprop, category
    """
    pt = client or _make_client()
    sd = start_date or "2004-01"
    ed = end_date or date.today().strftime("%Y-%m")

    pt.build_payload(
        kw_list=[term],
        geo=geo or "",
        timeframe=_to_timeframe(sd, ed),
    )
    time.sleep(sleep)

    df = pt.interest_over_time()
    if df.empty:
        return pd.DataFrame(
            columns=["value", "date", "geo", "time", "keyword", "gprop", "category"]
        )

    df = df.reset_index()[["date", term]].rename(columns={term: "value"})
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["keyword"] = term
    df["geo"] = geo if geo else "world"
    df["time"] = _time_label(sd, ed)
    df["gprop"] = "web"
    df["category"] = "All categories"
    return df[["value", "date", "geo", "time", "keyword", "gprop", "category"]]


def get_top_topics(
    term: str,
    geo: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    client: TrendReq | None = None,
    sleep: float = 2.0,
) -> pd.DataFrame:
    """Top related topics for *term*.

    Equivalent to getTopTopics2() / trends/v1beta/topTopics.

    Returns
    -------
    DataFrame with columns: topicTitle, topicId, value, geo, time, keyword, gprop, category
    """
    pt = client or _make_client()
    sd = start_date or "2004-01"
    ed = end_date or date.today().strftime("%Y-%m")

    pt.build_payload(
        kw_list=[term],
        geo=geo or "",
        timeframe=_to_timeframe(sd, ed),
    )
    time.sleep(sleep)

    try:
        raw = pt.related_topics()
    except IndexError:
        raw = {}
    top = (raw.get(term) or {}).get("top")

    if top is None or (isinstance(top, pd.DataFrame) and top.empty):
        res = pd.DataFrame([{"topicTitle": None, "topicId": None, "value": None}])
    else:
        res = top[["topic_title", "topic_mid", "value"]].copy()
        res.columns = pd.Index(["topicTitle", "topicId", "value"])

    res["geo"] = geo if geo else "world"
    res["time"] = _time_label(sd, ed)
    res["keyword"] = term
    res["gprop"] = "web"
    res["category"] = "All categories"
    return res


def get_top_queries(
    term: str,
    geo: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    client: TrendReq | None = None,
    sleep: float = 2.0,
) -> pd.DataFrame:
    """Top related search queries for *term*.

    Equivalent to getTopQueries2() / trends/v1beta/topQueries.

    Returns
    -------
    DataFrame with columns: topSearches, value, geo, time, keyword, gprop, category
    """
    pt = client or _make_client()
    sd = start_date or "2004-01"
    ed = end_date or date.today().strftime("%Y-%m")

    pt.build_payload(
        kw_list=[term],
        geo=geo or "",
        timeframe=_to_timeframe(sd, ed),
    )
    time.sleep(sleep)

    try:
        raw = pt.related_queries()
    except IndexError:
        raw = {}
    top = (raw.get(term) or {}).get("top")

    if top is None or (isinstance(top, pd.DataFrame) and top.empty):
        res = pd.DataFrame([{"topSearches": None, "value": None}])
    else:
        res = top[["query", "value"]].copy()
        res.columns = pd.Index(["topSearches", "value"])

    res["geo"] = geo if geo else "world"
    res["time"] = _time_label(sd, ed)
    res["keyword"] = term
    res["gprop"] = "web"
    res["category"] = "All categories"
    return res


def try_gtrends_api(
    topic_keyword: str,
    geo_location: str,
    start_date: str | None = None,
    end_date: str | None = None,
    fun: Literal["graph", "topics", "queries"] = "graph",
    client: TrendReq | None = None,
    sleep: float = 2.0,
) -> tuple[pd.DataFrame | None, str | None]:
    """Safe wrapper — never raises; port of fun.R / try_gtrends_api().

    Returns
    -------
    (DataFrame, None)  on success
    (None, error_str)  on failure.  A '429' substring signals a rate-limit hit.
    """
    kwargs: dict = dict(
        geo=geo_location,
        start_date=start_date,
        end_date=end_date,
        client=client,
        sleep=sleep,
    )
    try:
        if fun == "graph":
            return get_graph(topic_keyword, **kwargs), None
        if fun == "topics":
            return get_top_topics(topic_keyword, **kwargs), None
        if fun == "queries":
            return get_top_queries(topic_keyword, **kwargs), None
        raise ValueError(f"fun must be 'graph', 'topics', or 'queries', got {fun!r}")
    except TooManyRequestsError:
        return None, "Status code was not 200. Returned status code:429"
    except Exception as exc:
        return None, str(exc)
