"""Load `config.yml` and resolve its paths against the project root.

The CLI reads defaults from here; every value can still be overridden by a
command-line flag, so this module only answers the question "what does the
config file say?" — it never merges in argv itself.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yml"


class Config:
    """Thin accessor over the parsed config file."""

    def __init__(self, raw: dict[str, Any], path: Path):
        self._raw = raw
        self.path = path

    @property
    def data_dir(self) -> Path:
        return self.resolve(self._raw.get("data_dir", "data"))

    def resolve(self, value: str | Path) -> Path:
        """Make *value* absolute, relative to the project root."""
        p = Path(value).expanduser()
        return p if p.is_absolute() else PROJECT_ROOT / p

    def reference(self, key: str) -> Path:
        return self.resolve(self._raw.get("reference", {})[key])

    def source(self, name: str) -> dict[str, Any]:
        """Return the settings block for one source (empty dict if absent)."""
        return dict(self._raw.get("sources", {}).get(name) or {})


def load_config(path: Path | None = None) -> Config:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}\n"
            "Pass --config to point at one, or restore config.yml at the project root."
        )
    with config_path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return Config(raw, config_path)


def pick(override: Any, configured: Any, fallback: Any = None) -> Any:
    """First non-None of the CLI override, the config value, then the fallback."""
    for candidate in (override, configured):
        if candidate is not None:
            return candidate
    return fallback


def as_date(value: Any) -> date | None:
    """Accept a date, an ISO string, or None (YAML parses bare dates already)."""
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
