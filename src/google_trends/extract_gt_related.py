"""Extract monthly Google Trends related topics and queries.

Port of 3_1_extract_related_search_GT_api.R — now using pytrends instead of
the private trends/v1beta API.  No API key required.

For each (4 diseases) × (28 Brazilian states + BR) × (monthly from 2020-01)
combination, fetches:
  - top related topics   → related_topics()
  - top related queries  → related_queries()

Outputs
-------
    data/GT/top_results/GoogleTrends_related_topic.csv
    data/GT/top_results/GoogleTrends_related_query.csv
    data/GT/top_results/query_error_topic.csv   (if any errors)
    data/GT/top_results/query_error_query.csv   (if any errors)

Usage
-----
    python extract_gt_related.py

Rate limiting
-------------
This script makes 2 requests per (disease, state, month) combination.
At 28 states × 4 diseases × ~60 months = ~6 700 iterations that is ~13 400
requests.  With SLEEP_SECONDS = 5 that's ~18 hours.  Reduce sleep at your
own risk (Google will 429 you).  The script saves progress and stops cleanly
on a 429.
"""

from __future__ import annotations

import csv
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
from dateutil.relativedelta import relativedelta

from .gtrends_api import _make_client, try_gtrends_api
from ..common import ExtractResult

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # src/google_trends -> src -> root
DATA_DIR = PROJECT_ROOT / "data"
OUT_DIR = DATA_DIR / "google_trends"

SLEEP_SECONDS = 5.0

DISEASE_QUERIES = [
    {"group": "Dengue",          "query": "/m/09wsg"},
    {"group": "Chikungunya",     "query": "/m/01__7l"},
    {"group": "Influenza/gripe", "query": "/m/0cycc"},
    {"group": "COVID-19",        "query": "/m/01cpyy"},
]


def _month_sequence(start: str = "2020-01", end: str | None = None) -> list[str]:
    cutoff_str = end or (date.today() - relativedelta(days=30)).strftime("%Y-%m")
    cur = date(int(start[:4]), int(start[5:7]), 1)
    cutoff = date(int(cutoff_str[:4]), int(cutoff_str[5:7]), 1)
    months: list[str] = []
    while cur <= cutoff:
        months.append(cur.strftime("%Y-%m"))
        cur += relativedelta(months=1)
    return months


def _geo_abbr(geo: str) -> str:
    return geo[3:] if geo.startswith("BR-") else geo


_MANIFEST_FIELDS = ["geo", "topic", "month", "request_type", "status", "rows_fetched", "error", "timestamp"]


def _load_completed(manifest_path: Path) -> set[tuple[str, str, str, str]]:
    """Return set of (geo, topic, month, request_type) tuples already recorded as 'ok'."""
    if not manifest_path.exists():
        return set()
    completed: set[tuple[str, str, str, str]] = set()
    with manifest_path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("status") == "ok":
                completed.add((row["geo"], row["topic"], row["month"], row["request_type"]))
    return completed


def _append_manifest_row(manifest_path: Path, row: dict) -> None:
    write_header = not manifest_path.exists()
    with manifest_path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_MANIFEST_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def extract(
    start_month: str = "2020-01",
    out_dir: Path = OUT_DIR,
    fu_path: Path = DATA_DIR / "epidemiological" / "br_federative_units.csv",
    popular_terms_path: Path = DATA_DIR / "google_trends" / "popular_terms.csv",
    sleep: float = SLEEP_SECONDS,
) -> ExtractResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest_related.csv"
    completed = _load_completed(manifest_path)

    fus = pd.read_csv(fu_path)
    fu_codes = [f"BR-{a}" for a in sorted(fus["ABBREVIATION"].tolist())] + ["BR"]
    months = _month_sequence(start_month)

    client = _make_client()

    topic_results: list[dict] = []
    query_results: list[dict] = []
    topic_errors: list[dict] = []
    query_errors: list[dict] = []

    total = len(DISEASE_QUERIES) * len(fu_codes) * len(months)
    done = 0
    n_downloaded = 0
    n_existing = 0
    n_failed = 0

    for disease in DISEASE_QUERIES:
        topic_name: str = disease["group"]
        query_str: str = disease["query"]

        for geo in fu_codes:
            for month in months:
                done += 1
                ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

                # ---- related TOPICS ----
                if (geo, topic_name, month, "topics") in completed:
                    n_existing += 1
                else:
                    print(f"[{done}/{total}] {topic_name} | {geo} | {month} | topics", flush=True)
                    df_t, err_t = try_gtrends_api(
                        topic_keyword=query_str,
                        geo_location=geo,
                        start_date=month,
                        end_date=month,
                        fun="topics",
                        client=client,
                        sleep=sleep,
                    )
                    if err_t is not None:
                        if "429" in err_t:
                            print("  Rate limit hit (429) — saving progress and stopping.")
                            _save(topic_results, query_results, topic_errors,
                                  query_errors, out_dir, popular_terms_path)
                            return ExtractResult(
                                downloaded=n_downloaded, existing=n_existing, failed=n_failed,
                                manifest_path=manifest_path,
                            )
                        topic_errors.append(
                            {"keyword": topic_name, "geo": geo, "time": month, "error": err_t}
                        )
                        _append_manifest_row(manifest_path, {
                            "geo": geo, "topic": topic_name, "month": month,
                            "request_type": "topics", "status": "error",
                            "rows_fetched": 0, "error": err_t, "timestamp": ts,
                        })
                        n_failed += 1
                    else:
                        df_t = df_t.copy()
                        df_t["keyword"] = topic_name
                        df_t["time"] = month
                        df_t["value"] = pd.to_numeric(
                            df_t["value"].replace("<1", "0.1"), errors="coerce"
                        )
                        topic_results.extend(
                            df_t[["topicTitle", "topicId", "value", "geo", "keyword", "time"]]
                            .to_dict("records")
                        )
                        _append_manifest_row(manifest_path, {
                            "geo": geo, "topic": topic_name, "month": month,
                            "request_type": "topics", "status": "ok",
                            "rows_fetched": len(df_t), "error": "", "timestamp": ts,
                        })
                        n_downloaded += 1

                # ---- related QUERIES ----
                if (geo, topic_name, month, "queries") in completed:
                    n_existing += 1
                else:
                    df_q, err_q = try_gtrends_api(
                        topic_keyword=query_str,
                        geo_location=geo,
                        start_date=month,
                        end_date=month,
                        fun="queries",
                        client=client,
                        sleep=sleep,
                    )
                    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    if err_q is not None:
                        query_errors.append(
                            {"keyword": topic_name, "geo": geo, "time": month, "error": err_q}
                        )
                        _append_manifest_row(manifest_path, {
                            "geo": geo, "topic": topic_name, "month": month,
                            "request_type": "queries", "status": "error",
                            "rows_fetched": 0, "error": err_q, "timestamp": ts,
                        })
                        n_failed += 1
                    else:
                        df_q = df_q.copy()
                        df_q["keyword"] = topic_name
                        df_q["time"] = month
                        df_q["value"] = pd.to_numeric(
                            df_q["value"].replace("<1", "0.1"), errors="coerce"
                        )
                        query_results.extend(
                            df_q[["topSearches", "value", "geo", "keyword", "time"]]
                            .to_dict("records")
                        )
                        _append_manifest_row(manifest_path, {
                            "geo": geo, "topic": topic_name, "month": month,
                            "request_type": "queries", "status": "ok",
                            "rows_fetched": len(df_q), "error": "", "timestamp": ts,
                        })
                        n_downloaded += 1

    _save(topic_results, query_results, topic_errors, query_errors,
          out_dir, popular_terms_path)
    return ExtractResult(
        downloaded=n_downloaded,
        existing=n_existing,
        failed=n_failed,
        manifest_path=manifest_path,
    )


def _save(
    topic_results: list[dict],
    query_results: list[dict],
    topic_errors: list[dict],
    query_errors: list[dict],
    out_dir: Path,
    popular_terms_path: Path,
) -> None:
    popular_terms = pd.read_csv(popular_terms_path)
    symptom_code_set = set(
        popular_terms[
            ~popular_terms["exclude"] & popular_terms["terms"].str.contains("/", na=False)
        ]["terms"]
    )

    if topic_results:
        gt_t = pd.DataFrame(topic_results)
        gt_t["location"] = gt_t["geo"].apply(_geo_abbr)
        gt_t["key_symptom"] = (
            gt_t["topicId"].isin(symptom_code_set) |
            (gt_t["topicTitle"] == "Symptom")
        )
        gt_t = gt_t[["topicTitle", "topicId", "time", "location", "value", "keyword", "key_symptom"]]
        gt_t.columns = pd.Index(
            ["topic_title", "topic_id", "date", "location", "value", "disease", "key_symptom"]
        )
        path = out_dir / "GoogleTrends_related_topic.csv"
        gt_t.to_csv(path, index=False)
        print(f"\nWrote {path}  ({len(gt_t):,} rows)")

    if query_results:
        gt_q = pd.DataFrame(query_results)
        gt_q["location"] = gt_q["geo"].apply(_geo_abbr)
        gt_q = gt_q[["topSearches", "time", "location", "value", "keyword"]]
        gt_q.columns = pd.Index(["topic_title", "date", "location", "value", "disease"])
        path = out_dir / "GoogleTrends_related_query.csv"
        gt_q.to_csv(path, index=False)
        print(f"Wrote {path}  ({len(gt_q):,} rows)")

    if topic_errors:
        p = out_dir / "query_error_topic.csv"
        pd.DataFrame(topic_errors).to_csv(p, index=False)
        print(f"Wrote {p}  ({len(topic_errors)} errors)")

    if query_errors:
        p = out_dir / "query_error_query.csv"
        pd.DataFrame(query_errors).to_csv(p, index=False)
        print(f"Wrote {p}  ({len(query_errors)} errors)")


if __name__ == "__main__":
    extract()
