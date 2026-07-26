"""The epidemiological-week anchor.

Worth its own file: an off-by-one here shifts every observation in the series by
up to six days, and no downstream step would notice.
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from src.pipeline.sinan.epiweek import floor_to_sunday

SUNDAY = 7  # polars dt.weekday(): Monday=1 ... Sunday=7


def floor_one(value: date | None) -> date | None:
    frame = pl.DataFrame({"d": [value]}, schema={"d": pl.Date})
    return frame.select(floor_to_sunday("d").alias("out"))["out"][0]


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (date(2024, 1, 7), date(2024, 1, 7)),    # Sunday: idempotent
        (date(2024, 1, 10), date(2024, 1, 7)),   # midweek
        (date(2024, 1, 13), date(2024, 1, 7)),   # Saturday
        (date(2024, 1, 14), date(2024, 1, 14)),  # next Sunday: new week
        (date(2010, 1, 1), date(2009, 12, 27)),  # crosses a year boundary
        (date(2012, 2, 29), date(2012, 2, 26)),  # leap day
        (None, None),                            # missing stays missing, not epoch
    ],
)
def test_floors_to_the_preceding_sunday(given: date | None, expected: date | None) -> None:
    assert floor_one(given) == expected


def test_differs_from_polars_truncate() -> None:
    """Regression guard against 'simplifying' this to dt.truncate("1w").

    polars anchors week windows on Monday, so truncate returns a date one day
    later than R's floor_date for every non-Monday input. If a future polars
    release changes that, this test fails and someone re-reads epiweek.py — which
    is the point.
    """
    wednesday = pl.DataFrame({"d": [date(2024, 1, 10)]}, schema={"d": pl.Date})
    result = wednesday.select(
        sunday=floor_to_sunday("d"),
        truncated=pl.col("d").dt.truncate("1w"),
    )
    assert result["sunday"][0] == date(2024, 1, 7)
    assert result["truncated"][0] == date(2024, 1, 8)


def test_holds_over_two_years_swept_daily() -> None:
    """Every result is a Sunday, on or before its input, idempotent, and stable."""
    frame = pl.select(
        pl.date_range(date(2023, 1, 1), date(2024, 12, 31), "1d").alias("d")
    ).with_columns(floor_to_sunday("d").alias("week"))
    frame = frame.with_columns(floor_to_sunday("week").alias("twice"))

    assert frame.select(pl.col("week").dt.weekday().eq(SUNDAY).all()).item()
    assert frame.select(pl.col("week").le(pl.col("d")).all()).item()
    assert frame.select((pl.col("d") - pl.col("week")).dt.total_days().max()).item() == 6
    assert frame.select(pl.col("week").eq(pl.col("twice")).all()).item()
