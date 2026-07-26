"""Shared fixtures for the pipeline tests."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

REPO = Path(__file__).resolve().parents[1]
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "sinan"
FEDERATIVE_UNITS = REPO / "data" / "epidemiological" / "br_federative_units.csv"

# Where `arboili transform sinan` writes by default. Present only once the real
# ~7 GB transformation has been run, which is why those tests are opt-in.
REAL_SERIES = REPO / "data" / "epidemiological" / "SINAN" / "SINAN_dengue_cases.parquet"


@pytest.fixture(scope="session")
def fixture_dir() -> Path:
    return FIXTURE_DIR


@pytest.fixture(scope="session")
def fu_path() -> Path:
    return FEDERATIVE_UNITS


@pytest.fixture(scope="session")
def fixture_series(tmp_path_factory: pytest.TempPathFactory) -> pl.DataFrame:
    """The full pipeline run over the committed fixtures.

    Session-scoped so the integrity checks share one transformation rather than
    rebuilding it per test.
    """
    from src.pipeline.sinan import transform

    out_dir = tmp_path_factory.mktemp("sinan_series")
    transform(
        in_dir=FIXTURE_DIR,
        out_dir=out_dir,
        fu_path=FEDERATIVE_UNITS,
        formats=["parquet"],
    )
    return pl.read_parquet(out_dir / "SINAN_dengue_cases.parquet")


@pytest.fixture(scope="session")
def real_series() -> pl.DataFrame:
    """The series built from the full SINAN archive; skips when absent."""
    if not REAL_SERIES.exists():
        pytest.skip(f"{REAL_SERIES.name} not built — run `arboili transform sinan`")
    return pl.read_parquet(REAL_SERIES)
