"""Integrity of the produced series.

Every check runs twice: against the committed fixtures on each test run, and
against the real full-archive output when it exists (`-m integration`). Same
assertions, two scales — so a bug that only shows up at scale still has somewhere
to surface, and the checks don't rot on machines without the ~7 GB of input.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from src.pipeline.sinan import integrity
from src.pipeline.sinan.spec import (
    CASE_COUNT,
    EW_NOTIFICATION,
    EW_SYMPTOM_ONSET,
    GROUP_KEYS,
)
from src.pipeline.sinan.transform import (
    ARTICLE_LAST_YEAR,
    MIN_WEEKS_FOR_COMPLETE_YEAR,
)

# The data paper's SINAN coverage.
ARTICLE_FIRST_YEAR = 2010


def assert_passed(check: integrity.Check) -> None:
    assert check.passed, str(check)


# --------------------------------------------------------------------------
# Fixture-scale: runs on every invocation
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "check_fn",
    [
        integrity.check_schema,
        integrity.check_unique_keys,
        integrity.check_positive_counts,
        integrity.check_weeks_start_on_sunday,
        integrity.check_onset_not_after_notification,
        integrity.check_classification_domain,
    ],
    ids=lambda fn: fn.__name__,
)
def test_single_column_checks_pass(fixture_series: pl.DataFrame, check_fn) -> None:
    assert_passed(check_fn(fixture_series))


def test_states_are_known(fixture_series: pl.DataFrame, fu_path: Path) -> None:
    """Fixtures are a head-sample, so only check for unknown abbreviations."""
    known = set(pl.read_csv(fu_path)["ABBREVIATION"].to_list())
    assert set(fixture_series["state_abbrev"].unique().to_list()) <= known


def test_dt_digita_gap(fixture_series: pl.DataFrame) -> None:
    """ew_recorded is null exactly for 2014-2020, populated on either side.

    The pipeline's subtlest failure mode: the gap is non-monotonic, so a
    threshold instead of a year set would null 2021+ silently.
    """
    assert_passed(integrity.check_dt_digita_gap(fixture_series))


def test_fixture_covers_every_schema_tier(fixture_series: pl.DataFrame) -> None:
    """The fixtures must exercise all three source schemas, or they prove little."""
    years = set(
        fixture_series.select(pl.col(EW_NOTIFICATION).dt.year().unique())[EW_NOTIFICATION]
        .to_list()
    )
    assert 2013 in years, "no pre-symptom tier (66-column export)"
    assert 2016 in years, "no DT_DIGITA-absent tier (119-column export)"
    assert 2020 in years, "no DT_DIGITA-empty tier"
    assert 2022 in years, "no fully-populated tier"


def test_no_null_group_keys_except_classification(fixture_series: pl.DataFrame) -> None:
    """Only final_classification and ew_recorded may be null."""
    for key in GROUP_KEYS:
        if key in ("final_classification", "ew_recorded"):
            continue
        assert fixture_series[key].null_count() == 0, f"{key} has nulls"


def test_run_all_reports_every_check(fixture_series: pl.DataFrame, fu_path: Path) -> None:
    checks = integrity.run_all(fixture_series, fu_path)
    assert len(checks) >= 8
    # `states` is expected to fail on a head-sample; everything else must pass.
    for check in checks:
        if check.name == "states":
            continue
        assert_passed(check)


# --------------------------------------------------------------------------
# Full-archive scale: opt-in via `-m integration`
# --------------------------------------------------------------------------

@pytest.mark.integration
def test_real_series_passes_every_check(real_series: pl.DataFrame, fu_path: Path) -> None:
    """The whole battery, including the ones a sample cannot support."""
    last_year = real_series.select(pl.col(EW_SYMPTOM_ONSET).dt.year().max()).item()
    failures = [
        str(check)
        for check in integrity.run_all(
            real_series, fu_path, continuity_years=(ARTICLE_FIRST_YEAR, last_year)
        )
        if not check.passed
    ]
    assert not failures, "\n".join(failures)


@pytest.mark.integration
def test_real_series_covers_the_article_window(real_series: pl.DataFrame) -> None:
    """Every year the data paper claims must be present."""
    years = set(
        real_series.select(pl.col(EW_SYMPTOM_ONSET).dt.year().unique())[EW_SYMPTOM_ONSET]
        .to_list()
    )
    expected = set(range(ARTICLE_FIRST_YEAR, ARTICLE_LAST_YEAR + 1))
    assert expected <= years, f"missing years: {sorted(expected - years)}"


@pytest.mark.integration
def test_real_series_extends_past_the_article_window(real_series: pl.DataFrame) -> None:
    """The series is meant to extend the published one, not just reproduce it.

    Guards against a future change quietly truncating at the article's 2024
    boundary, which would drop the extension years the pipeline exists to add.
    """
    years = set(
        real_series.select(pl.col(EW_SYMPTOM_ONSET).dt.year().unique())[EW_SYMPTOM_ONSET]
        .to_list()
    )
    assert max(years) > ARTICLE_LAST_YEAR, (
        f"series stops at {max(years)}; extension years are missing"
    )


@pytest.mark.integration
def test_complete_extension_years_are_not_flagged_partial(real_series: pl.DataFrame) -> None:
    """A full year past the article window is a complete season, not a caveat.

    Partial-ness is measured in weeks, not inferred from the year — 2025 has all
    52 weeks and must not be reported as incomplete merely for being recent.
    """
    weeks = (
        real_series.group_by(pl.col(EW_SYMPTOM_ONSET).dt.year().alias("year"))
        .agg(pl.col(EW_SYMPTOM_ONSET).n_unique().alias("weeks"))
        .filter(pl.col("year") > ARTICLE_LAST_YEAR)
        .sort("year")
    )
    complete = weeks.filter(pl.col("weeks") >= MIN_WEEKS_FOR_COMPLETE_YEAR)
    assert complete.height >= 1, (
        f"no complete year beyond {ARTICLE_LAST_YEAR}:\n{weeks}"
    )


@pytest.mark.integration
def test_real_series_has_no_missing_weeks(real_series: pl.DataFrame) -> None:
    """A gap means a week of notifications vanished in transformation.

    Checked across the whole series, extension years included — not just the
    article window — since the extension is the part with no external reference
    to compare against and therefore needs the structural checks most.

    Starts at ARTICLE_FIRST_YEAR because earlier onset weeks are a thin residue
    spilling back from the first file's notifications, and are genuinely sparse.
    """
    last_year = (
        real_series.select(pl.col(EW_SYMPTOM_ONSET).dt.year().max()).item()
    )
    assert last_year >= ARTICLE_LAST_YEAR
    assert_passed(
        integrity.check_weekly_continuity(real_series, ARTICLE_FIRST_YEAR, last_year)
    )


@pytest.mark.integration
def test_real_series_includes_all_federative_units(
    real_series: pl.DataFrame, fu_path: Path
) -> None:
    assert_passed(integrity.check_states(real_series, fu_path))


@pytest.mark.integration
def test_real_series_case_totals_are_plausible(real_series: pl.DataFrame) -> None:
    """Dengue notifications run into the millions per year at the national level.

    A loose sanity band: catches a filter silently discarding most of the data,
    without encoding numbers that legitimately shift as SINAN is revised.
    """
    weeks = (
        real_series.group_by(pl.col(EW_SYMPTOM_ONSET).dt.year().alias("year"))
        .agg(pl.col(EW_SYMPTOM_ONSET).n_unique().alias("weeks"))
    )
    # Every complete year, including the extension years — not just the article
    # window. Partial years are excluded because a half-season legitimately
    # totals less than a full one.
    per_year = (
        real_series.group_by(pl.col(EW_SYMPTOM_ONSET).dt.year().alias("year"))
        .agg(pl.col(CASE_COUNT).sum().alias("cases"))
        .join(weeks, on="year")
        .filter(
            pl.col("year").ge(ARTICLE_FIRST_YEAR)
            & pl.col("weeks").ge(MIN_WEEKS_FOR_COMPLETE_YEAR)
        )
        .sort("year")
    )
    assert per_year.height >= 15
    assert per_year["cases"].min() > 100_000, f"implausibly low year:\n{per_year}"
