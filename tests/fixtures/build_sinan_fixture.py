"""Regenerate the small SINAN fixtures from the real downloaded CSVs.

The fixtures let the integrity checks run on every test invocation instead of
only on a machine holding ~7 GB of input. They are committed, so this script only
needs re-running if the upstream schema changes.

    uv run python tests/fixtures/build_sinan_fixture.py

Samples the head of each year so the extract stays internally consistent (whole
epidemiological weeks, plausible state coverage) rather than being a random
scatter that would make continuity checks meaningless.
"""

from __future__ import annotations

import gzip
import sys
from pathlib import Path

import polars as pl

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "data" / "epidemiological" / "SINAN"
TARGET = Path(__file__).resolve().parent / "sinan"

sys.path.insert(0, str(REPO))
from src.pipeline.sinan.spec import DENGUE  # noqa: E402

# One year per schema generation, plus the years that bracket the DT_DIGITA gap.
# 2013: 66 columns, DT_DIGITA populated.
# 2016: 119 columns, no DT_DIGITA at all.
# 2020: 121 columns, DT_DIGITA declared but empty.
# 2022: 121 columns, DT_DIGITA populated.
YEARS = (2013, 2016, 2020, 2022)
ROWS = 40_000


def main() -> int:
    if not SOURCE.is_dir():
        print(f"No SINAN data at {SOURCE}; run `arboili sinan` first.", file=sys.stderr)
        return 1

    TARGET.mkdir(parents=True, exist_ok=True)
    for year in YEARS:
        source = SOURCE / f"DENGBR{year % 100:02d}.csv"
        if not source.exists():
            print(f"skipping {source.name}: not downloaded", file=sys.stderr)
            continue

        # Keep only the columns the pipeline reads, so the committed fixture is
        # a few hundred KB instead of tens of MB. The pipeline projects to these
        # five anyway, so a narrower file exercises exactly the same code path —
        # including the tier logic, since which columns exist still varies.
        available = set(pl.scan_csv(source, n_rows=0).collect_schema().names())
        columns = [c for c in DENGUE.columns_for(year) if c in available]

        sample = pl.read_csv(
            source,
            columns=columns,
            n_rows=ROWS,
            schema_overrides={c: pl.String for c in columns},
        ).select(columns)

        destination = TARGET / f"{source.name}.gz"
        with gzip.open(destination, "wb", compresslevel=9) as handle:
            sample.write_csv(handle)
        print(
            f"{destination.relative_to(REPO)}: {destination.stat().st_size / 1e3:.0f} KB, "
            f"{sample.height:,} rows, {len(columns)} columns"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
