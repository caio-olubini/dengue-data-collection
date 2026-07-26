"""The transformation, one step at a time.

Every step takes a `LazyFrame` and returns a `LazyFrame`, so the whole pipeline
composes without materialising anything, and each step can be unit-tested on a
five-row synthetic frame. Nothing here reads or writes files except `scan_year`,
which only opens a lazy handle — `transform.py` decides when to collect.

The steps mirror `r/scripts/2_transform_sinan_data.R`, whose semantics the data
paper describes in its Data Processing section.
"""

from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

from .epiweek import floor_to_sunday
from .spec import (
    CASE_COUNT,
    CLASSI_FIN,
    DATE_COLUMNS,
    DT_DIGITA,
    DT_NOTIFIC,
    DT_SIN_PRI,
    EPIWEEK_OF,
    FINAL_CLASSIFICATION,
    GROUP_KEYS,
    OUTPUT_COLUMNS,
    SG_UF_NOT,
    STATE_ABBREV,
    DiseaseSpec,
)

log = logging.getLogger("pipeline.sinan")

# R: filter(dif_datas > -180 & dif_datas < 1), where dif_datas is
# DT_SIN_PRI - DT_NOTIFIC in days. Both bounds are strict and the window is
# asymmetric: it drops onsets more than 180 days before notification (data-entry
# errors, typically a birth date typed into the onset field) AND any onset
# recorded after its own notification.
#
# The article's prose describes only the 180-day rule, not the upper bound. The
# defaults reproduce the published dataset; `filter_date_consistency` takes them
# as arguments so the article's looser reading stays reachable.
DEFAULT_DIF_LOWER = -180
DEFAULT_DIF_UPPER = 1

_DIF_DAYS = "_dif_days"


def scan_year(path: Path, year: int, spec: DiseaseSpec) -> pl.LazyFrame:
    """Open one yearly CSV, projecting only the columns the series needs.

    The projection is the whole memory story: SINAN exports carry 66-121 columns
    and the largest file is 1.7 GB, but the five columns we keep are dates and
    short codes. polars pushes the projection into the CSV reader, so the rest is
    never parsed.

    Dates are read as strings and parsed in `parse_dates`, since SINAN uses empty
    fields for missing values and a strict date reader would reject them.
    """
    wanted = spec.columns_for(year)
    available = set(pl.scan_csv(path, n_rows=0).collect_schema().names())

    # The spec is the source of truth, but a mismatch means SINAN changed its
    # export and someone needs to look — so say it loudly rather than nulling a
    # column in silence.
    missing = [column for column in wanted if column not in available]
    if missing:
        log.warning(
            "%s: spec expects %s but the file does not have %s — treating as absent. "
            "The upstream schema may have changed.",
            path.name, ", ".join(wanted), ", ".join(missing),
        )
        wanted = [column for column in wanted if column in available]

    # The 2020 export declares DT_DIGITA and leaves it entirely empty, so the
    # column being present is not on its own evidence that the spec is stale.
    # Only note it at debug level; the integrity checks catch a real mismatch.
    if DT_DIGITA in available and not spec.has_dt_digita(year):
        log.debug(
            "%s: %s is present in the header but the spec treats %d as unusable "
            "(the column is declared but empty in some years).",
            path.name, DT_DIGITA, year,
        )

    return pl.scan_csv(
        path,
        schema_overrides={column: pl.String for column in wanted},
    ).select(wanted)


def ensure_columns(frame: pl.LazyFrame, year: int, spec: DiseaseSpec) -> pl.LazyFrame:
    """Add any schema-tier column the year lacks, so every year shares one schema.

    Only DT_DIGITA varies (unusable 2014-2020). Filling it with a typed null keeps
    the later `pl.concat` safe and preserves the distinction the published table
    makes: `ew_recorded` is genuinely unknown for those years, not zero.
    """
    schema = frame.collect_schema().names()
    if DT_DIGITA not in schema:
        frame = frame.with_columns(pl.lit(None, dtype=pl.String).alias(DT_DIGITA))
    # Fix the column order too, not just the set. polars' concat aligns by name,
    # but every year sharing one column order keeps `pl.concat` cheap and makes
    # the intermediate frames comparable in tests.
    return frame.select(DT_DIGITA, DT_NOTIFIC, DT_SIN_PRI, SG_UF_NOT, CLASSI_FIN)


def parse_dates(frame: pl.LazyFrame) -> pl.LazyFrame:
    """Parse the three date columns from ISO strings.

    `strict=False` turns unparseable values into null rather than raising: SINAN
    files contain empty date fields, and a handful of malformed ones, which the R
    pipeline also silently coerced to NA.
    """
    return frame.with_columns(
        pl.col(column).str.to_date("%Y-%m-%d", strict=False) for column in DATE_COLUMNS
    )


def filter_date_consistency(
    frame: pl.LazyFrame,
    lower: int = DEFAULT_DIF_LOWER,
    upper: int = DEFAULT_DIF_UPPER,
) -> pl.LazyFrame:
    """Drop records whose symptom-onset and notification dates are inconsistent.

    Keeps `lower < (onset - notification) < upper`, both bounds strict. See the
    module constants for why the window is asymmetric.

    Rows where either date is null are dropped, because the comparison yields
    null and polars' filter keeps only true — the same outcome R's `filter`
    produced for NA.
    """
    difference = (pl.col(DT_SIN_PRI) - pl.col(DT_NOTIFIC)).dt.total_days()
    return (
        frame.with_columns(difference.alias(_DIF_DAYS))
        .filter(pl.col(_DIF_DAYS) > lower, pl.col(_DIF_DAYS) < upper)
        .drop(_DIF_DAYS)
    )


def add_epiweeks(frame: pl.LazyFrame) -> pl.LazyFrame:
    """Replace the raw dates with the Sunday starting each one's epi week."""
    return frame.with_columns(
        floor_to_sunday(source).alias(target) for source, target in EPIWEEK_OF.items()
    ).drop(DATE_COLUMNS)


def normalise_keys(frame: pl.LazyFrame) -> pl.LazyFrame:
    """Coerce the two code columns to integers.

    Both arrive as strings because the CSV is read that way. `CLASSI_FIN` has
    empty values in several years, which become null — a legitimate "not yet
    classified" state that the R pipeline also carried through, so it stays a
    group key rather than being dropped.
    """
    return frame.with_columns(
        pl.col(CLASSI_FIN).cast(pl.Int8, strict=False).alias(FINAL_CLASSIFICATION),
        pl.col(SG_UF_NOT).cast(pl.Int16, strict=False),
    ).drop(CLASSI_FIN)


def aggregate(frame: pl.LazyFrame) -> pl.LazyFrame:
    """Collapse case-level rows into counts per week/classification/state.

    This is the step that makes the whole pipeline fit in memory: it turns
    millions of rows per year into at most a few hundred thousand.
    """
    keys = [EPIWEEK_OF[column] for column in DATE_COLUMNS]
    return frame.group_by([*keys, FINAL_CLASSIFICATION, SG_UF_NOT]).agg(
        pl.len().alias(CASE_COUNT).cast(pl.UInt32)
    )


def merge_years(frame: pl.LazyFrame) -> pl.LazyFrame:
    """Re-aggregate across year boundaries after the per-year frames are concatenated.

    Epidemiological weeks do not respect calendar years: the week starting
    2012-12-30 runs into January 2013, and SINAN splits those cases across the
    2012 and 2013 exports. Aggregating per year therefore emits the same group
    key twice — the counts are right, but the series has duplicate rows.

    Summing here collapses them. Cheap, because it runs on the already-aggregated
    data rather than on case-level rows.
    """
    # Same key as `aggregate`: the three epi weeks, the classification, and the
    # still-numeric unit code (the state join happens after this).
    keys = [*(key for key in GROUP_KEYS if key != STATE_ABBREV), SG_UF_NOT]
    return frame.group_by(keys).agg(pl.col(CASE_COUNT).sum())


def attach_state(frame: pl.LazyFrame, federative_units: pl.LazyFrame) -> pl.LazyFrame:
    """Swap the numeric IBGE unit code for its abbreviation (35 -> SP).

    The R pipeline named this column `state_abbrev` but left the numeric code in
    it. Doing the join here makes the name honest and the series directly usable;
    it is a deliberate divergence from the R output.

    Uses an inner join so unmapped codes are dropped rather than silently
    becoming null. `validate_state_codes` in integrity.py reports how many rows
    that removes.
    """
    return (
        frame.join(federative_units, left_on=SG_UF_NOT, right_on="CODE", how="inner")
        .with_columns(pl.col("ABBREVIATION").alias(STATE_ABBREV))
        .drop(SG_UF_NOT, "ABBREVIATION")
    )


def order_columns(frame: pl.LazyFrame) -> pl.LazyFrame:
    """Put the columns in published order and sort the series deterministically.

    Sorting by state first mirrors the R script's `arrange`. Nulls go last so the
    order is reproducible regardless of backend — polars and R disagree on where
    nulls sort by default, and `final_classification` has them.
    """
    return frame.select(OUTPUT_COLUMNS).sort(
        [STATE_ABBREV, *(key for key in GROUP_KEYS if key != STATE_ABBREV)],
        nulls_last=True,
    )


def load_federative_units(path: Path) -> pl.LazyFrame:
    """Read the CODE -> ABBREVIATION reference table."""
    return pl.scan_csv(path).select(
        pl.col("CODE").cast(pl.Int16),
        pl.col("ABBREVIATION").cast(pl.String),
    )


def build_year(
    path: Path,
    year: int,
    spec: DiseaseSpec,
    lower: int = DEFAULT_DIF_LOWER,
    upper: int = DEFAULT_DIF_UPPER,
) -> pl.LazyFrame:
    """Compose every step for one year, up to but not including the state join.

    The state join and final ordering happen once on the concatenated series
    rather than per year, since both are cheap on the aggregated data.
    """
    frame = scan_year(path, year, spec)
    frame = ensure_columns(frame, year, spec)
    frame = parse_dates(frame)
    frame = filter_date_consistency(frame, lower, upper)
    frame = add_epiweeks(frame)
    frame = normalise_keys(frame)
    return aggregate(frame)
