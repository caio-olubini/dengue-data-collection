"""Run the SINAN case-series transformation over every yearly file.

The memory strategy lives here. The inputs total ~7 GB against ~6 GB of usable
RAM, so years are processed one at a time and each is aggregated down to a few
hundred thousand rows before the next is opened. Combined with the five-column
projection in `steps.scan_year` and polars' streaming engine, peak RSS stays
well under a gigabyte — no two raw years are ever resident together.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from .spec import DENGUE, EW_SYMPTOM_ONSET, DiseaseSpec
from .steps import (
    DEFAULT_DIF_LOWER,
    DEFAULT_DIF_UPPER,
    attach_state,
    build_year,
    load_federative_units,
    merge_years,
    order_columns,
)

log = logging.getLogger("pipeline.sinan")

# The data paper's SINAN coverage. Years beyond it are a deliberate extension of
# the published series, not an anomaly — they are processed and reported the same
# way. This constant exists so the integrity tests can assert the reference
# window is complete; it does not limit what gets transformed.
ARTICLE_LAST_YEAR = 2024

# A calendar year is only "partial" if the source stops part-way through it.
# Being past ARTICLE_LAST_YEAR does not make a year incomplete: 2025 is a full
# 52-week year. Flagging it as partial would wrongly imply a data-quality caveat
# on a complete season, so completeness is measured, not inferred from the year.
MIN_WEEKS_FOR_COMPLETE_YEAR = 52

FORMAT_PARQUET = "parquet"
FORMAT_CSV_GZ = "csv_gz"
DEFAULT_FORMATS = (FORMAT_PARQUET, FORMAT_CSV_GZ)


@dataclass
class SinanTransformResult:
    """Outcome of one transformation run.

    Mirrors `ExtractResult` from `src.common` — notably `failed`, which the CLI
    sums into its exit code.
    """

    disease: str
    years: list[int] = field(default_factory=list)
    partial_years: list[int] = field(default_factory=list)
    rows_out: int = 0
    failed: int = 0
    outputs: list[Path] = field(default_factory=list)
    elapsed: float = 0.0

    def __str__(self) -> str:
        parts = [
            f"disease={self.disease}",
            f"years={len(self.years)}",
            f"rows={self.rows_out:,}",
            f"failed={self.failed}",
            f"elapsed={self.elapsed:.1f}s",
        ]
        if self.partial_years:
            parts.append(f"partial={','.join(str(y) for y in self.partial_years)}")
        if self.outputs:
            parts.append(f"outputs={', '.join(p.name for p in self.outputs)}")
        return f"SinanTransformResult({', '.join(parts)})"


def discover_inputs(in_dir: Path, spec: DiseaseSpec) -> list[tuple[int, Path]]:
    """Find the yearly CSVs for `spec`, sorted by year.

    Skips the pipeline's own outputs and anything else whose name doesn't encode
    a year, so re-running over an output directory is safe.
    """
    found: list[tuple[int, Path]] = []
    for path in sorted(in_dir.glob(spec.file_glob)):
        year = spec.year_of(path)
        if year is None:
            continue
        found.append((year, path))
    return found


def transform(
    in_dir: Path,
    out_dir: Path,
    fu_path: Path,
    spec: DiseaseSpec = DENGUE,
    from_year: int | None = None,
    to_year: int | None = None,
    dif_lower: int = DEFAULT_DIF_LOWER,
    dif_upper: int = DEFAULT_DIF_UPPER,
    formats: tuple[str, ...] | list[str] = DEFAULT_FORMATS,
) -> SinanTransformResult:
    """Build the weekly case series from yearly case-level SINAN CSVs.

    Returns counts grouped by (recorded week, notification week, symptom-onset
    week, final classification, state). One row per combination present in the
    data; combinations that never occur are simply absent rather than zero-filled.
    """
    started = time.monotonic()
    result = SinanTransformResult(disease=spec.name)

    inputs = discover_inputs(in_dir, spec)
    if from_year is not None:
        inputs = [(year, path) for year, path in inputs if year >= from_year]
    if to_year is not None:
        inputs = [(year, path) for year, path in inputs if year <= to_year]

    if not inputs:
        raise FileNotFoundError(
            f"No {spec.file_glob} files in {in_dir}. "
            f"Run `arboili sinan` first to download them."
        )

    federative_units = load_federative_units(fu_path)

    yearly: list[pl.DataFrame] = []
    for year, path in inputs:
        log.info("%s: aggregating %s", spec.name, path.name)
        try:
            # Collected per year, not at the end: this is what bounds memory.
            # The streaming engine keeps the group-by from materialising the
            # whole year at once, and only the small aggregate survives the loop.
            frame = build_year(path, year, spec, dif_lower, dif_upper).collect(
                engine="streaming"
            )
        except Exception as error:  # one bad year shouldn't lose the other sixteen
            log.error("%s: failed on %s: %s", spec.name, path.name, error)
            result.failed += 1
            continue

        log.info("%s: %s -> %d grouped rows", spec.name, path.name, frame.height)
        yearly.append(frame)
        result.years.append(year)

    if not yearly:
        raise RuntimeError(f"Every input file failed for {spec.name}; see the log above.")

    # merge_years must run before the state join: SINAN splits the epi week that
    # straddles New Year across two yearly files, so the same group key arrives
    # from two of the per-year frames and has to be summed back together.
    combined = merge_years(pl.concat(yearly, how="vertical").lazy())
    series = order_columns(attach_state(combined, federative_units)).collect()

    result.rows_out = series.height
    result.partial_years = _incomplete_years(series, result.years)
    result.outputs = _write(series, out_dir, spec, formats)
    result.elapsed = time.monotonic() - started

    if result.partial_years:
        log.warning(
            "%s: %s cover fewer than %d epidemiological weeks — partial calendar "
            "years, do not read them as complete seasons.",
            spec.name,
            ", ".join(str(y) for y in result.partial_years),
            MIN_WEEKS_FOR_COMPLETE_YEAR,
        )

    span = series.select(
        pl.col(EW_SYMPTOM_ONSET).min().alias("first"),
        pl.col(EW_SYMPTOM_ONSET).max().alias("last"),
    ).row(0)
    log.info("%s: %d rows spanning %s to %s", spec.name, series.height, *span)

    return result


def _incomplete_years(series: pl.DataFrame, years: list[int]) -> list[int]:
    """Which processed years cover fewer than a full set of epidemiological weeks.

    Measured from the output rather than assumed from the calendar, so extending
    the series past the article's window does not mark complete years as partial.
    Only years the pipeline actually read are considered — the first year carries
    a handful of onset weeks spilling back from earlier notifications, and those
    are not an incomplete season.
    """
    weeks_per_year = (
        series.group_by(pl.col(EW_SYMPTOM_ONSET).dt.year().alias("year"))
        .agg(pl.col(EW_SYMPTOM_ONSET).n_unique().alias("weeks"))
    )
    processed = set(years)
    return sorted(
        year
        for year, weeks in weeks_per_year.iter_rows()
        if year in processed and weeks < MIN_WEEKS_FOR_COMPLETE_YEAR
    )


def _write(
    series: pl.DataFrame, out_dir: Path, spec: DiseaseSpec, formats: tuple[str, ...] | list[str]
) -> list[Path]:
    """Write the series in each requested format.

    Parquet is the primary artifact — typed, compact, and cheap for the next
    stage to read. The gzipped CSV exists so the R scripts and anything else
    without a parquet reader can still consume it.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for fmt in formats:
        if fmt == FORMAT_PARQUET:
            path = out_dir / f"{spec.output_name}.parquet"
            series.write_parquet(path, compression="zstd")
        elif fmt == FORMAT_CSV_GZ:
            path = out_dir / f"{spec.output_name}.csv.gz"
            _write_csv_gz(series, path)
        else:
            raise ValueError(
                f"Unknown output format {fmt!r}; expected one of {DEFAULT_FORMATS}"
            )
        log.info("wrote %s (%.1f MB)", path, path.stat().st_size / 1e6)
        written.append(path)

    return written


def _write_csv_gz(series: pl.DataFrame, path: Path) -> None:
    import gzip

    with gzip.open(path, "wb", compresslevel=6) as handle:
        series.write_csv(handle)
