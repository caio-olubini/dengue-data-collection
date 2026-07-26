"""Which columns to read from SINAN, and which years actually carry them.

SINAN's export schema changed twice over the covered period, and not
monotonically. Rather than scatter year checks through the transformation, every
schema quirk is declared here as data, so a future upstream change is a one-line
edit instead of a hunt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# The five raw columns the case series needs. SINAN files carry 66-121 columns;
# reading only these is what keeps the ~7 GB of input inside a ~6 GB RAM budget.
DT_DIGITA = "DT_DIGITA"      # date the notification was keyed into the system
DT_NOTIFIC = "DT_NOTIFIC"    # date the notification form was completed
DT_SIN_PRI = "DT_SIN_PRI"    # date of first symptoms
SG_UF_NOT = "SG_UF_NOT"      # federative unit of notification (numeric IBGE code)
CLASSI_FIN = "CLASSI_FIN"    # final case classification

DATE_COLUMNS = (DT_DIGITA, DT_NOTIFIC, DT_SIN_PRI)
REQUIRED_COLUMNS = (DT_NOTIFIC, DT_SIN_PRI, SG_UF_NOT, CLASSI_FIN)

# Epidemiological-week columns derived from the three dates above, and the
# output names they carry in the published table.
EW_RECORDED = "ew_recorded"
EW_NOTIFICATION = "ew_notification"
EW_SYMPTOM_ONSET = "ew_symptom_onset"

EPIWEEK_OF: dict[str, str] = {
    DT_DIGITA: EW_RECORDED,
    DT_NOTIFIC: EW_NOTIFICATION,
    DT_SIN_PRI: EW_SYMPTOM_ONSET,
}

FINAL_CLASSIFICATION = "final_classification"
STATE_ABBREV = "state_abbrev"
CASE_COUNT = "case_count"

# The grouping key of the series: one row per (three weeks x classification x UF).
GROUP_KEYS = (
    EW_RECORDED,
    EW_NOTIFICATION,
    EW_SYMPTOM_ONSET,
    FINAL_CLASSIFICATION,
    STATE_ABBREV,
)

OUTPUT_COLUMNS = (*GROUP_KEYS, CASE_COUNT)


@dataclass(frozen=True)
class DiseaseSpec:
    """Per-disease file naming and schema history.

    `dt_digita_missing_years` is a set rather than a cutoff year on purpose:
    DT_DIGITA is present 2010-2013, unusable 2014-2020, then present again from
    2021. A `year >= N` threshold cannot express that gap, and getting it wrong
    silently nulls `ew_recorded` for years that do have the column.

    "Unusable" covers two distinct cases, deliberately treated the same way: the
    2014-2019 exports omit the column outright, while the 2020 export declares it
    and leaves every value empty. Both mean `ew_recorded` is unknown, and the
    data paper's Table 1 footnote records the span as 2014-2020.
    """

    name: str
    filename_pattern: str            # regex with one group capturing the 2-digit year
    file_glob: str                   # glob used to discover input files
    dt_digita_missing_years: frozenset[int]
    output_name: str
    _year_re: re.Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_year_re", re.compile(self.filename_pattern))

    def year_of(self, path: Path | str) -> int | None:
        """Return the four-digit year encoded in a filename, or None if it doesn't match.

        SINAN names files with a two-digit year (DENGBR24.csv), which is
        unambiguous here because the archive starts in 2000.
        """
        match = self._year_re.fullmatch(Path(path).name)
        if match is None:
            return None
        return 2000 + int(match.group(1))

    def has_dt_digita(self, year: int) -> bool:
        return year not in self.dt_digita_missing_years

    def columns_for(self, year: int) -> list[str]:
        """The raw columns to project out of a given year's CSV."""
        columns = list(REQUIRED_COLUMNS)
        if self.has_dt_digita(year):
            columns.insert(0, DT_DIGITA)
        return columns


# Verified against all 17 files on disk (2010-2026). The 2014-2019 exports (119
# columns) omit DT_DIGITA entirely; the 2020 export declares it among its 121
# columns but every value is empty. 2021+ populate it normally (~99%). This
# matches the data paper's Table 1 footnote: "Missing variable in the databases
# for the years 2014 to 2020."
DENGUE = DiseaseSpec(
    name="dengue",
    # `.gz` is optional: the downloader writes plain CSVs, but polars reads
    # gzipped ones transparently and the committed test fixtures are compressed.
    filename_pattern=r"DENGBR(\d{2})\.csv(?:\.gz)?",
    file_glob="DENGBR*.csv*",
    dt_digita_missing_years=frozenset(range(2014, 2021)),
    output_name="SINAN_dengue_cases",
)

# Defined so adding chikungunya is a config change rather than a refactor. There
# is no CHIKBR downloader yet and no files on disk, so this is not wired into the
# CLI and its year tiers are taken from the R script rather than verified data.
CHIKUNGUNYA = DiseaseSpec(
    name="chikungunya",
    filename_pattern=r"CHIKBR(\d{2})\.csv(?:\.gz)?",
    file_glob="CHIKBR*.csv*",
    dt_digita_missing_years=frozenset(range(2015, 2021)),
    output_name="SINAN_chik_cases",
)

SPECS: dict[str, DiseaseSpec] = {spec.name: spec for spec in (DENGUE, CHIKUNGUNYA)}
