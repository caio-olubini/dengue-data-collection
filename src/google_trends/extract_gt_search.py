"""Extract 5-year weekly Google Trends search-interest index.

Port of 3_extract_GT_api.R — now using pytrends instead of the private
trends/v1beta API.  No API key required.

For each (symptom/disease query group) × (Brazilian state + BR) pair, calls
interest_over_time() over a rolling 5-year window and saves the result to
data/GoogleTrends/GoogleTrends_search.csv.

Usage
-----
    python extract_gt_search.py

Outputs
-------
    data/GoogleTrends/GoogleTrends_search.csv   — successful extractions
    data/GoogleTrends/query_error.csv           — any failed calls

Rate limiting
-------------
pytrends scrapes the public Google Trends site.  The default 2 s sleep between
requests is usually enough for small loops; increase SLEEP_SECONDS for larger
runs or if you start seeing 429 errors.
"""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from .gtrends_api import _make_client, try_gtrends_api
from ..common import ExtractResult

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # src/google_trends -> src -> root
DATA_DIR = PROJECT_ROOT / "data"
OUT_DIR = DATA_DIR / "google_trends"

SLEEP_SECONDS = 2.0

EXCLUDED_GROUPS = {
    "Perda de olfato", "Perda do paladar", "Alteração do paladar",
    "Alteração de olfato", "Dor ocular", "Dor atrás dos olhos",
}

# Disease-level topic IDs (Freebase) — same as the R script
DISEASE_QUERIES = [
    {"group": "Dengue",      "query": "/m/09wsg"},
    {"group": "Chikungunya", "query": "/m/01__7l"},
    {"group": "Influenza",   "query": "/m/0cycc"},
    # COVID: pytrends doesn't support URL-encoded boolean OR strings,
    # so we use the canonical Freebase topic ID for COVID-19
    {"group": "COVID-19",    "query": "/m/01cpyy"},
]


# ---------------------------------------------------------------------------
# Query-table construction
# ---------------------------------------------------------------------------

def _build_query_table(popular_terms: pd.DataFrame) -> pd.DataFrame:
    """Return a DataFrame[group, query] for each symptom/disease group.

    Strategy (per group):
      1. If a Freebase code exists (is_code=True), use it — canonical topic,
         best coverage.
      2. Otherwise, use the term flagged as main=True; fall back to first term.

    This is simpler than the R boolean-OR URL encoding, and works with
    pytrends which does not support compound boolean queries.
    """
    pt = popular_terms[~popular_terms["exclude"]].copy()

    rows: list[dict] = []
    for group, grp in pt.groupby("group"):
        coded = grp[grp["is_code"]]
        if not coded.empty:
            query = coded.iloc[0]["terms"]
        else:
            main_terms = grp[grp.get("main", False)] if "main" in grp.columns else pd.DataFrame()
            query = main_terms.iloc[0]["terms"] if not main_terms.empty else grp.iloc[0]["terms"]
        rows.append({"group": group, "query": query})

    symptom_df = pd.DataFrame(rows)
    disease_df = pd.DataFrame(DISEASE_QUERIES)
    combined = pd.concat([symptom_df, disease_df], ignore_index=True).drop_duplicates(subset="group")
    return combined[~combined["group"].isin(EXCLUDED_GROUPS)].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Date window
# ---------------------------------------------------------------------------

def _five_year_window(reference: date | None = None) -> tuple[str, str]:
    ref = reference or date.today()
    end = ref.strftime("%Y-%m")
    start = (ref - timedelta(days=365 * 5 - 31)).strftime("%Y-%m")
    return start, end


# ---------------------------------------------------------------------------
# Main extraction loop
# ---------------------------------------------------------------------------

_MANIFEST_FIELDS = ["geo", "topic", "start_date", "end_date", "status", "rows_fetched", "error", "timestamp"]


def _load_completed(manifest_path: Path) -> set[tuple[str, str]]:
    """Return set of (geo, topic) pairs already recorded as 'ok' in the manifest."""
    if not manifest_path.exists():
        return set()
    completed: set[tuple[str, str]] = set()
    with manifest_path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("status") == "ok":
                completed.add((row["geo"], row["topic"]))
    return completed


def _append_manifest_row(manifest_path: Path, row: dict) -> None:
    write_header = not manifest_path.exists()
    with manifest_path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_MANIFEST_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def extract(
    reference_date: date | None = None,
    out_dir: Path = OUT_DIR,
    popular_terms_path: Path = DATA_DIR / "google_trends" / "popular_terms.csv",
    fu_path: Path = DATA_DIR / "epidemiological" / "br_federative_units.csv",
    sleep: float = SLEEP_SECONDS,
) -> ExtractResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest_search.csv"

    fus = pd.read_csv(fu_path)
    federative_units = [f"BR-{a}" for a in sorted(fus["ABBREVIATION"].tolist())] + ["BR"]

    popular_terms = pd.read_csv(popular_terms_path)
    query_table = _build_query_table(popular_terms)

    start_date, end_date = _five_year_window(reference_date)
    completed = _load_completed(manifest_path)

    # Reuse a single TrendReq session — shares cookies, better for throttling
    client = _make_client()

    results: list[dict] = []
    errors: list[dict] = []
    total = len(query_table) * len(federative_units)
    done = 0
    n_downloaded = 0
    n_existing = 0
    n_failed = 0

    for _, row in query_table.iterrows():
        topic: str = row["group"]
        query: str = row["query"]

        for geo in federative_units:
            done += 1

            if (geo, topic) in completed:
                n_existing += 1
                continue

            print(f"[{done}/{total}] {topic} | {geo} | {start_date} → {end_date}", flush=True)

            df, err = try_gtrends_api(
                topic_keyword=query,
                geo_location=geo,
                start_date=start_date,
                end_date=end_date,
                fun="graph",
                client=client,
                sleep=sleep,
            )

            ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

            if err is not None:
                if "429" in err:
                    print("  Rate limit hit (429) — saving progress and stopping.")
                    _save(results, errors, out_dir)
                    return ExtractResult(
                        downloaded=n_downloaded,
                        existing=n_existing,
                        failed=n_failed,
                        manifest_path=manifest_path,
                    )
                errors.append({"keyword": topic, "geo": geo,
                               "time": f"{start_date} to {end_date}", "error": err})
                _append_manifest_row(manifest_path, {
                    "geo": geo, "topic": topic, "start_date": start_date,
                    "end_date": end_date, "status": "error", "rows_fetched": 0,
                    "error": err, "timestamp": ts,
                })
                n_failed += 1
                print(f"  error: {err}")
                continue

            df = df.copy()
            df["topic"] = topic
            df["value"] = pd.to_numeric(df["value"], errors="coerce")
            results.extend(df[["date", "geo", "topic", "value"]].to_dict("records"))
            _append_manifest_row(manifest_path, {
                "geo": geo, "topic": topic, "start_date": start_date,
                "end_date": end_date, "status": "ok", "rows_fetched": len(df),
                "error": "", "timestamp": ts,
            })
            n_downloaded += 1

    _save(results, errors, out_dir)
    return ExtractResult(
        downloaded=n_downloaded,
        existing=n_existing,
        failed=n_failed,
        manifest_path=manifest_path,
    )


def _save(results: list[dict], errors: list[dict], out_dir: Path) -> None:
    if results:
        gt = pd.DataFrame(results)
        gt["location"] = gt["geo"].str[3:].where(gt["geo"].str.startswith("BR-"), gt["geo"])
        gt["location"] = gt["location"].replace("", "BR")
        gt["date"] = pd.to_datetime(gt["date"], errors="coerce")

        # Fill missing (date, location, topic) combinations with 0
        dates = gt["date"].dropna().unique()
        locations = gt["location"].dropna().unique()
        topics = gt["topic"].dropna().unique()
        full_idx = pd.MultiIndex.from_product(
            [dates, locations, topics], names=["date", "location", "topic"]
        )
        gt = (
            gt.groupby(["date", "location", "topic"])["value"]
            .mean()
            .reindex(full_idx, fill_value=0)
            .reset_index()
        )

        path = out_dir / "GoogleTrends_search.csv"
        gt.to_csv(path, index=False)
        print(f"\nWrote {path}  ({len(gt):,} rows)")

    if errors:
        p = out_dir / "query_error.csv"
        pd.DataFrame(errors).to_csv(p, index=False)
        print(f"Wrote {p}  ({len(errors)} errors)")


if __name__ == "__main__":
    # Fixed reference date for reproducibility, matching the R script
    extract(reference_date=date(2024, 12, 31))
