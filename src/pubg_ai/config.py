from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
import os


def _env_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class AppConfig:
    raw_data_dir: Path
    raw_compression: str = "gzip"
    allow_storage_fallback: bool = False

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        base_dir: Path | None = None,
    ) -> "AppConfig":
        values = env or os.environ
        base = base_dir or Path.cwd()
        raw_dir = Path(values.get("PUBG_RAW_DATA_DIR", "./data/raw")).expanduser()
        if not raw_dir.is_absolute():
            raw_dir = base / raw_dir

        compression = values.get("PUBG_RAW_COMPRESSION", "gzip").strip().lower()
        if compression not in {"gzip", "none"}:
            raise ValueError("PUBG_RAW_COMPRESSION must be either 'gzip' or 'none'.")

        return cls(
            raw_data_dir=raw_dir,
            raw_compression=compression,
            allow_storage_fallback=_env_bool(
                values.get("PUBG_ALLOW_STORAGE_FALLBACK"),
                default=False,
            ),
        )

