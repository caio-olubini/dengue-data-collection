"""SINAN case-series pipeline.

Turns the yearly case-level CSVs downloaded by
`src.collection.epidemiological.sinan_dengue` into the weekly historical series
the data paper describes: case counts by epidemiological week, federative unit,
and final classification.

Case counts only — the symptom columns the R pipeline carried serve a separate
analysis and are not part of this series.
"""

from __future__ import annotations

from .spec import CHIKUNGUNYA, DENGUE, SPECS, DiseaseSpec
from .transform import SinanTransformResult, transform

__all__ = [
    "CHIKUNGUNYA",
    "DENGUE",
    "SPECS",
    "DiseaseSpec",
    "SinanTransformResult",
    "transform",
]
