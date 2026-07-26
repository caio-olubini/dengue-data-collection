"""Integrity checks over a produced case series.

Written as functions over a DataFrame rather than as test assertions so the same
checks run against a small committed fixture (fast, every test run) and against
the real ~7 GB-derived output (slow, opt-in). A check that only ever runs on one
machine is not much of a check.

Each returns a `Check`; nothing raises. `run_all` gathers them so a caller can
report every problem at once instead of stopping at the first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from .spec import (
    CASE_COUNT,
    EW_NOTIFICATION,
    EW_RECORDED,
    EW_SYMPTOM_ONSET,
    FINAL_CLASSIFICATION,
    GROUP_KEYS,
    OUTPUT_COLUMNS,
    STATE_ABBREV,
)

# Years for which `ew_recorded` is legitimately null: 2014-2019 omit DT_DIGITA
# from the export, and 2020 declares it but leaves every value empty. Matches the
# data paper's Table 1 footnote. Duplicated from DENGUE.dt_digita_missing_years
# rather than imported, so this check fails if someone edits the spec by accident.
DT_DIGITA_MISSING_YEARS = frozenset(range(2014, 2021))

SUNDAY = 7  # polars dt.weekday(): Monday=1 ... Sunday=7


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str = ""

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return f"[{status}] {self.name}{f': {self.detail}' if self.detail else ''}"


def check_schema(series: pl.DataFrame) -> Check:
    """The published column set, in order."""
    actual = tuple(series.columns)
    if actual == OUTPUT_COLUMNS:
        return Check("schema", True, f"{len(actual)} columns in published order")
    return Check("schema", False, f"expected {OUTPUT_COLUMNS}, got {actual}")


def check_unique_keys(series: pl.DataFrame) -> Check:
    """One row per group key — aggregation must not leave duplicates."""
    duplicates = series.height - series.select(GROUP_KEYS).unique().height
    if duplicates == 0:
        return Check("unique_keys", True, f"{series.height:,} rows, all keys distinct")
    return Check("unique_keys", False, f"{duplicates:,} duplicate group keys")


def check_positive_counts(series: pl.DataFrame) -> Check:
    """Every row counts at least one case; a zero would mean a phantom group."""
    bad = series.filter(pl.col(CASE_COUNT) < 1).height
    if bad == 0:
        total = series[CASE_COUNT].sum()
        return Check("positive_counts", True, f"{total:,} cases total")
    return Check("positive_counts", False, f"{bad:,} rows with case_count < 1")


def check_weeks_start_on_sunday(series: pl.DataFrame) -> Check:
    """Every epi-week column must land on a Sunday.

    The single most valuable check in the file: it is what catches a wrong week
    anchor, which would otherwise shift the whole series silently.
    """
    offenders: dict[str, int] = {}
    for column in (EW_RECORDED, EW_NOTIFICATION, EW_SYMPTOM_ONSET):
        bad = series.filter(
            pl.col(column).is_not_null() & (pl.col(column).dt.weekday() != SUNDAY)
        ).height
        if bad:
            offenders[column] = bad
    if not offenders:
        return Check("weeks_start_on_sunday", True, "all epi-week dates are Sundays")
    detail = ", ".join(f"{col}: {n:,} non-Sunday" for col, n in offenders.items())
    return Check("weeks_start_on_sunday", False, detail)


def check_onset_not_after_notification(series: pl.DataFrame) -> Check:
    """The date filter's upper bound, verified on the output.

    Weeks, not raw dates, so same-week rows are equal rather than ordered.
    """
    bad = series.filter(pl.col(EW_SYMPTOM_ONSET) > pl.col(EW_NOTIFICATION)).height
    if bad == 0:
        return Check("onset_not_after_notification", True, "onset week <= notification week")
    return Check("onset_not_after_notification", False, f"{bad:,} rows with onset after notification")


def check_states(series: pl.DataFrame, fu_path: Path) -> Check:
    """State abbreviations must all come from the reference table.

    Checks membership across the whole series, not per week: many states report
    no dengue in a given week, so a per-slice "all 27 present" assertion would
    fail on correct data.
    """
    expected = set(pl.read_csv(fu_path)["ABBREVIATION"].to_list())
    actual = set(series[STATE_ABBREV].unique().to_list())

    unknown = actual - expected
    if unknown:
        return Check("states", False, f"not in reference table: {sorted(unknown)}")

    missing = expected - actual
    if missing:
        return Check("states", False, f"never appear in the series: {sorted(missing)}")
    return Check("states", True, f"all {len(expected)} federative units present")


def check_dt_digita_gap(series: pl.DataFrame) -> Check:
    """`ew_recorded` is null for the years whose exports cannot supply DT_DIGITA.

    Guards the pipeline's subtlest trap: the gap is 2014-2020 and non-monotonic
    (present, then absent, then present), so a `year >= N` threshold would null
    2021+ without any error surfacing.

    Keyed on notification year, which is the closest proxy for source file year
    available in the output. The two disagree at the margins — SINAN names files
    by processing year, so a file holds some notifications from the adjacent
    year — hence the tolerance below rather than an exact test. Rows attributed
    to a year are checked only for a *wholesale* gap, which is the failure mode
    that matters; `test_dt_digita_gap` in the step tests covers the exact
    per-year behaviour.
    """
    by_year = (
        series.select(
            pl.col(EW_NOTIFICATION).dt.year().alias("year"),
            pl.col(EW_RECORDED).is_null().alias("recorded_null"),
        )
        .group_by("year")
        .agg(
            pl.col("recorded_null").mean().alias("null_share"),
            pl.len().alias("rows"),
        )
    )

    # A year straddling the gap boundary legitimately mixes populated and null
    # rows, so only years wholly inside or outside it can be asserted on.
    boundary_years = {min(DT_DIGITA_MISSING_YEARS) - 1, max(DT_DIGITA_MISSING_YEARS) + 1}

    problems: list[str] = []
    for year, null_share, rows in by_year.iter_rows():
        if year in DT_DIGITA_MISSING_YEARS:
            if null_share < 1.0:
                problems.append(
                    f"{year}: expected all-null ew_recorded, {1 - null_share:.1%} populated"
                )
        elif year in boundary_years or rows < 1000:
            continue  # too few rows, or a year that straddles the boundary
        elif null_share > 0.5:
            problems.append(
                f"{year}: ew_recorded {null_share:.1%} null but DT_DIGITA should be populated"
            )

    if problems:
        return Check("dt_digita_gap", False, "; ".join(problems))
    first, last = min(DT_DIGITA_MISSING_YEARS), max(DT_DIGITA_MISSING_YEARS)
    return Check("dt_digita_gap", True, f"ew_recorded null across {first}-{last}")


def check_weekly_continuity(
    series: pl.DataFrame, first_year: int, last_year: int
) -> Check:
    """No missing weeks in the onset series across the given window.

    A gap means a whole week of notifications vanished — far more likely a
    pipeline bug than a real week in which Brazil reported no dengue at all.
    """
    weeks = (
        series.filter(
            pl.col(EW_SYMPTOM_ONSET).dt.year().is_between(first_year, last_year)
        )
        .select(EW_SYMPTOM_ONSET)
        .unique()
        .sort(EW_SYMPTOM_ONSET)[EW_SYMPTOM_ONSET]
        .to_list()
    )
    if not weeks:
        return Check("weekly_continuity", False, f"no weeks at all in {first_year}-{last_year}")

    expected: list[date] = []
    cursor = weeks[0]
    while cursor <= weeks[-1]:
        expected.append(cursor)
        cursor += timedelta(days=7)

    gaps = sorted(set(expected) - set(weeks))
    if not gaps:
        return Check(
            "weekly_continuity", True,
            f"{len(weeks)} consecutive weeks, {weeks[0]} to {weeks[-1]}",
        )
    shown = ", ".join(str(g) for g in gaps[:5])
    suffix = f" (+{len(gaps) - 5} more)" if len(gaps) > 5 else ""
    return Check("weekly_continuity", False, f"{len(gaps)} missing weeks: {shown}{suffix}")


def check_classification_domain(series: pl.DataFrame) -> Check:
    """Final classifications must be plausible SINAN codes.

    Null is valid (not yet classified) and deliberately retained.

    The data paper's Table 6 documents 1-6, 8, and 10-13. Real exports also carry
    0, which appears from 2022 and is not in that list — treated as in-range
    rather than as corruption, since it is clearly a code SINAN emits and this
    check exists to catch parsing damage (negative values, codes in the
    hundreds), not to police the ministry's code book.
    """
    codes = set(
        series.filter(pl.col(FINAL_CLASSIFICATION).is_not_null())[
            FINAL_CLASSIFICATION
        ].unique().to_list()
    )
    out_of_range = {code for code in codes if code < 0 or code > 13}
    if out_of_range:
        return Check(
            "classification_domain", False,
            f"codes outside SINAN's 0-13 range: {sorted(out_of_range)}",
        )
    return Check("classification_domain", True, f"codes {sorted(codes)}")


def run_all(
    series: pl.DataFrame,
    fu_path: Path,
    continuity_years: tuple[int, int] | None = None,
) -> list[Check]:
    """Run every check. `continuity_years` is skipped when the data is a sample."""
    checks = [
        check_schema(series),
        check_unique_keys(series),
        check_positive_counts(series),
        check_weeks_start_on_sunday(series),
        check_onset_not_after_notification(series),
        check_states(series, fu_path),
        check_dt_digita_gap(series),
        check_classification_domain(series),
    ]
    if continuity_years is not None:
        checks.append(check_weekly_continuity(series, *continuity_years))
    return checks
