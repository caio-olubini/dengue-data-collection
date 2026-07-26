"""Each transformation step, in isolation, on small synthetic frames."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from src.pipeline.sinan.spec import (
    CASE_COUNT,
    CHIKUNGUNYA,
    DENGUE,
    DT_DIGITA,
    DT_NOTIFIC,
    DT_SIN_PRI,
    EW_NOTIFICATION,
    EW_RECORDED,
    EW_SYMPTOM_ONSET,
    FINAL_CLASSIFICATION,
    OUTPUT_COLUMNS,
    SG_UF_NOT,
    STATE_ABBREV,
)
from src.pipeline.sinan.steps import (
    add_epiweeks,
    aggregate,
    attach_state,
    build_year,
    ensure_columns,
    filter_date_consistency,
    load_federative_units,
    merge_years,
    normalise_keys,
    order_columns,
    parse_dates,
    scan_year,
)

NOTIFICATION = date(2024, 6, 12)


def raw_frame(**overrides: list) -> pl.LazyFrame:
    """A minimal post-scan frame: all five columns, as strings."""
    data = {
        DT_DIGITA: ["2024-06-14"],
        DT_NOTIFIC: ["2024-06-12"],
        DT_SIN_PRI: ["2024-06-10"],
        SG_UF_NOT: ["35"],
        "CLASSI_FIN": ["10"],
    }
    data.update(overrides)
    return pl.DataFrame(data).lazy()


# --------------------------------------------------------------------------
# spec: schema tiers
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("year", "expected"),
    [
        (2010, True), (2013, True),      # 66-column exports carry DT_DIGITA
        (2014, False), (2016, False),    # 119-column exports omit it
        (2019, False),
        (2020, False),                   # declared, but every value is empty
        (2021, True), (2024, True),      # populated again
    ],
)
def test_dt_digita_availability_by_year(year: int, expected: bool) -> None:
    """The gap is 2014-2020 and non-monotonic.

    A `year >= N` threshold cannot express this. Getting it wrong nulls
    `ew_recorded` for years that do have the column, with no error anywhere —
    which is exactly what this test exists to prevent.
    """
    assert DENGUE.has_dt_digita(year) is expected
    assert (DT_DIGITA in DENGUE.columns_for(year)) is expected


def test_columns_for_always_includes_the_required_four() -> None:
    for year in (2010, 2016, 2020, 2024):
        columns = DENGUE.columns_for(year)
        assert {DT_NOTIFIC, DT_SIN_PRI, SG_UF_NOT, "CLASSI_FIN"} <= set(columns)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("DENGBR10.csv", 2010),
        ("DENGBR24.csv", 2024),
        ("DENGBR22.csv.gz", 2022),        # committed fixtures are gzipped
        ("SINAN_dengue_cases.csv.gz", None),  # the pipeline's own output
        ("manifest.csv", None),
        ("CHIKBR17.csv", None),           # wrong disease
    ],
)
def test_year_of(filename: str, expected: int | None) -> None:
    assert DENGUE.year_of(filename) == expected


def test_chikungunya_spec_reads_its_own_files() -> None:
    """Chik is defined but unwired; the spec should still be self-consistent."""
    assert CHIKUNGUNYA.year_of("CHIKBR17.csv") == 2017
    assert CHIKUNGUNYA.year_of("DENGBR17.csv") is None


# --------------------------------------------------------------------------
# ensure_columns
# --------------------------------------------------------------------------

def test_ensure_columns_adds_missing_dt_digita_but_leaves_existing_alone() -> None:
    without = pl.DataFrame({
        DT_NOTIFIC: ["2016-06-12"], DT_SIN_PRI: ["2016-06-10"],
        SG_UF_NOT: ["35"], "CLASSI_FIN": ["10"],
    }).lazy()
    added = ensure_columns(without, 2016, DENGUE).collect()
    assert DT_DIGITA in added.columns
    assert added[DT_DIGITA][0] is None

    kept = ensure_columns(raw_frame(), 2024, DENGUE).collect()
    assert kept[DT_DIGITA][0] == "2024-06-14"


def test_every_schema_tier_yields_one_concatenable_schema() -> None:
    """Years with different source schemas must line up for pl.concat."""
    tiers = {
        2013: raw_frame(),
        2016: pl.DataFrame({
            DT_NOTIFIC: ["2016-06-12"], DT_SIN_PRI: ["2016-06-10"],
            SG_UF_NOT: ["35"], "CLASSI_FIN": ["10"],
        }).lazy(),
        2024: raw_frame(),
    }
    frames = [
        parse_dates(ensure_columns(frame, year, DENGUE)).collect()
        for year, frame in tiers.items()
    ]
    assert len({tuple(f.columns) for f in frames}) == 1
    assert pl.concat(frames, how="vertical").height == 3


# --------------------------------------------------------------------------
# parse_dates
# --------------------------------------------------------------------------

def test_parse_dates_handles_empty_and_malformed() -> None:
    """SINAN uses empty fields for missing dates; bad values become null, not errors."""
    frame = raw_frame(
        DT_DIGITA=["", "not-a-date", "2024-06-14"],
        DT_NOTIFIC=["2024-06-12"] * 3,
        DT_SIN_PRI=["2024-06-10"] * 3,
        SG_UF_NOT=["35"] * 3,
        CLASSI_FIN=["10"] * 3,
    )
    result = parse_dates(frame).collect()
    assert result[DT_DIGITA].to_list() == [None, None, date(2024, 6, 14)]
    assert result.schema[DT_NOTIFIC] == pl.Date


# --------------------------------------------------------------------------
# filter_date_consistency
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("difference", "kept"),
    [
        (-181, False),  # beyond the window
        (-180, False),  # boundary: strict >, so excluded
        (-179, True),   # first day inside
        (-1, True),
        (0, True),      # onset and notification on the same day
        (1, False),     # boundary: strict <, so excluded
        (2, False),     # onset after notification
    ],
)
def test_filter_boundaries(difference: int, kept: bool) -> None:
    """Exactly where an off-by-one would hide: both bounds are strict."""
    frame = raw_frame(
        DT_NOTIFIC=[NOTIFICATION.isoformat()],
        DT_SIN_PRI=[(NOTIFICATION + timedelta(days=difference)).isoformat()],
    )
    result = filter_date_consistency(parse_dates(frame)).collect()
    assert (result.height == 1) is kept


def test_filter_drops_rows_with_null_dates() -> None:
    """A null comparison is not true, so the row goes — matching R's filter."""
    frame = raw_frame(
        DT_NOTIFIC=["2024-06-12", ""],
        DT_SIN_PRI=["2024-06-10", "2024-06-10"],
        DT_DIGITA=["2024-06-14"] * 2,
        SG_UF_NOT=["35"] * 2,
        CLASSI_FIN=["10"] * 2,
    )
    assert filter_date_consistency(parse_dates(frame)).collect().height == 1


def test_filter_bounds_are_configurable() -> None:
    """The article's prose describes only the 180-day rule; that must stay reachable."""
    frame = raw_frame(
        DT_NOTIFIC=[NOTIFICATION.isoformat()],
        DT_SIN_PRI=[(NOTIFICATION + timedelta(days=5)).isoformat()],
    )
    parsed = parse_dates(frame)
    assert filter_date_consistency(parsed).collect().height == 0
    assert filter_date_consistency(parsed, lower=-180, upper=181).collect().height == 1


# --------------------------------------------------------------------------
# add_epiweeks / normalise_keys / aggregate
# --------------------------------------------------------------------------

def test_add_epiweeks_replaces_raw_dates() -> None:
    result = add_epiweeks(parse_dates(raw_frame())).collect()
    assert {EW_RECORDED, EW_NOTIFICATION, EW_SYMPTOM_ONSET} <= set(result.columns)
    assert not {DT_DIGITA, DT_NOTIFIC, DT_SIN_PRI} & set(result.columns)
    # 2024-06-10 is a Monday; its week starts Sunday 2024-06-09.
    assert result[EW_SYMPTOM_ONSET][0] == date(2024, 6, 9)


def test_normalise_keys_casts_codes_and_keeps_empty_classification() -> None:
    """An unclassified case is a real state, not a row to drop."""
    frame = raw_frame(
        DT_DIGITA=["2024-06-14"] * 2, DT_NOTIFIC=["2024-06-12"] * 2,
        DT_SIN_PRI=["2024-06-10"] * 2, SG_UF_NOT=["35", "33"],
        CLASSI_FIN=["10", ""],
    )
    result = normalise_keys(parse_dates(frame)).collect()
    assert result[FINAL_CLASSIFICATION].to_list() == [10, None]
    assert result[SG_UF_NOT].to_list() == [35, 33]


def test_aggregate_counts_rows_per_group() -> None:
    """Three identical rows plus one differing state give counts of 3 and 1."""
    frame = raw_frame(
        DT_DIGITA=["2024-06-14"] * 4, DT_NOTIFIC=["2024-06-12"] * 4,
        DT_SIN_PRI=["2024-06-10"] * 4, SG_UF_NOT=["35", "35", "35", "33"],
        CLASSI_FIN=["10"] * 4,
    )
    result = aggregate(normalise_keys(add_epiweeks(parse_dates(frame)))).collect()
    assert result.height == 2
    assert sorted(result[CASE_COUNT].to_list()) == [1, 3]
    assert result[CASE_COUNT].sum() == 4


def test_aggregate_groups_dates_within_one_week() -> None:
    """Different days of one epi week collapse into a single row."""
    frame = raw_frame(
        DT_DIGITA=["2024-06-14"] * 3,
        DT_NOTIFIC=["2024-06-12"] * 3,
        # Mon/Tue/Wed of the same week -> all floor to Sunday 2024-06-09.
        DT_SIN_PRI=["2024-06-10", "2024-06-11", "2024-06-12"],
        SG_UF_NOT=["35"] * 3, CLASSI_FIN=["10"] * 3,
    )
    result = aggregate(normalise_keys(add_epiweeks(parse_dates(frame)))).collect()
    assert result.height == 1
    assert result[CASE_COUNT][0] == 3


# --------------------------------------------------------------------------
# attach_state / order_columns
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("codes", "expected"),
    [
        ([35, 33, 53], ["SP", "RJ", "DF"]),
        ([35, 99], ["SP"]),  # an unmappable code must not survive as a null state
    ],
)
def test_attach_state_maps_codes_and_drops_unknown(
    codes: list[int], expected: list[str], fu_path: Path
) -> None:
    frame = pl.DataFrame({SG_UF_NOT: codes}).lazy()
    result = attach_state(frame, load_federative_units(fu_path)).collect()
    assert result[STATE_ABBREV].to_list() == expected
    assert SG_UF_NOT not in result.columns


def test_order_columns_produces_the_published_schema(fu_path: Path) -> None:
    frame = aggregate(normalise_keys(add_epiweeks(parse_dates(raw_frame()))))
    result = order_columns(attach_state(frame, load_federative_units(fu_path))).collect()
    assert tuple(result.columns) == OUTPUT_COLUMNS


# --------------------------------------------------------------------------
# scan_year / build_year against the committed fixtures
# --------------------------------------------------------------------------

def test_scan_year_projects_only_needed_columns(fixture_dir: Path) -> None:
    """The projection is the memory story: 5 columns out of up to 121."""
    path = fixture_dir / "DENGBR22.csv.gz"
    columns = scan_year(path, 2022, DENGUE).collect_schema().names()
    assert set(columns) == {DT_DIGITA, DT_NOTIFIC, DT_SIN_PRI, SG_UF_NOT, "CLASSI_FIN"}


def test_scan_year_omits_dt_digita_in_the_gap(fixture_dir: Path) -> None:
    columns = scan_year(fixture_dir / "DENGBR16.csv.gz", 2016, DENGUE).collect_schema().names()
    assert DT_DIGITA not in columns


def test_build_year_is_deterministic(fixture_dir: Path) -> None:
    """Same input, same output — the transformation carries no hidden state."""
    path = fixture_dir / "DENGBR13.csv.gz"
    first = build_year(path, 2013, DENGUE).collect().sort(pl.all())
    second = build_year(path, 2013, DENGUE).collect().sort(pl.all())
    assert first.equals(second)


@pytest.mark.parametrize(
    ("states", "expected_height"),
    [
        ([26, 26], 1),  # same group split across two yearly files -> summed back
        ([26, 33], 2),  # different states -> left alone
    ],
)
def test_merge_years_sums_only_matching_groups(states: list[int], expected_height: int) -> None:
    """The epi week straddling New Year appears in two yearly exports.

    2012-12-30 starts a week that runs into January 2013, so SINAN files those
    cases under both years. Aggregating per year emits the group key twice with
    the count split between them (3 from the 2012 file, 5 from the 2013 file);
    merge_years must sum matching groups back into one row and leave distinct
    ones alone. Found on the real archive (468 such groups), which the fixtures
    cannot reach.
    """
    frame = pl.DataFrame({
        EW_RECORDED: [date(2013, 1, 6)] * 2,
        EW_NOTIFICATION: [date(2012, 12, 30)] * 2,
        EW_SYMPTOM_ONSET: [date(2012, 12, 30)] * 2,
        FINAL_CLASSIFICATION: [5, 5],
        SG_UF_NOT: states,
        CASE_COUNT: [3, 5],
    }).lazy()
    result = merge_years(frame).collect()
    assert result.height == expected_height
    assert result[CASE_COUNT].sum() == 8


def test_build_year_never_invents_cases(fixture_dir: Path) -> None:
    """Aggregated counts must not exceed the rows that went in."""
    path = fixture_dir / "DENGBR22.csv.gz"
    raw_rows = pl.scan_csv(path).select(pl.len()).collect().item()
    aggregated = build_year(path, 2022, DENGUE).collect()
    assert aggregated[CASE_COUNT].sum() <= raw_rows
