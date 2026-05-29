from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class ExtractResult:
    downloaded: int
    existing: int
    failed: int
    manifest_path: Path
    failures_path: Path | None = None

    def __str__(self) -> str:
        parts = [
            f"downloaded={self.downloaded}",
            f"existing={self.existing}",
            f"failed={self.failed}",
            f"manifest={self.manifest_path}",
        ]
        if self.failures_path:
            parts.append(f"failures={self.failures_path}")
        return f"ExtractResult({', '.join(parts)})"
