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
    replay_data_dir: Path
    raw_compression: str = "gzip"
    allow_storage_fallback: bool = False
    allow_replay_storage_fallback: bool = False
    collector_poll_interval_seconds: int = 180
    collector_cycle_player_limit: int = 100
    player_lookup_chunk_size: int = 10

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        base_dir: Path | None = None,
    ) -> "AppConfig":
        values = env or os.environ
        base = base_dir or Path.cwd()
        raw_dir = _config_path(values.get("PUBG_RAW_DATA_DIR", "./data/raw"), base)
        replay_dir = _config_path(
            values.get("PUBG_REPLAY_DATA_DIR", "./data/replays"),
            base,
        )

        compression = values.get("PUBG_RAW_COMPRESSION", "gzip").strip().lower()
        if compression not in {"gzip", "none"}:
            raise ValueError("PUBG_RAW_COMPRESSION must be either 'gzip' or 'none'.")

        return cls(
            raw_data_dir=raw_dir,
            replay_data_dir=replay_dir,
            raw_compression=compression,
            allow_storage_fallback=_env_bool(
                values.get("PUBG_ALLOW_STORAGE_FALLBACK"),
                default=False,
            ),
            allow_replay_storage_fallback=_env_bool(
                values.get("PUBG_ALLOW_REPLAY_STORAGE_FALLBACK"),
                default=False,
            ),
            collector_poll_interval_seconds=_env_int(
                values.get("PUBG_COLLECTOR_POLL_INTERVAL_SECONDS"),
                default=180,
            ),
            collector_cycle_player_limit=_env_int(
                values.get("PUBG_COLLECTOR_CYCLE_PLAYER_LIMIT"),
                default=100,
            ),
            player_lookup_chunk_size=_env_int(
                values.get("PUBG_PLAYER_LOOKUP_CHUNK_SIZE"),
                default=10,
            ),
        )

    @classmethod
    def from_sources(
        cls,
        env: Mapping[str, str] | None = None,
        base_dir: Path | None = None,
    ) -> "AppConfig":
        values = env or os.environ
        base = base_dir or Path.cwd()
        config = cls.from_env(values, base)
        settings_file = _config_path(
            values.get("PUBG_LOCAL_SETTINGS_FILE", "./config/local_settings.json"),
            base,
        )

        if not settings_file.exists():
            return config

        from pubg_ai.local_settings import CollectorSettings, LocalSettingsStore

        settings_store = LocalSettingsStore(settings_file, base_dir=base)
        storage_settings = settings_store.load_storage_settings()
        collector_settings = settings_store.load_collector_settings(
            default=CollectorSettings(
                poll_interval_seconds=config.collector_poll_interval_seconds,
                cycle_player_limit=config.collector_cycle_player_limit,
                player_lookup_chunk_size=config.player_lookup_chunk_size,
            )
        )

        return cls(
            raw_data_dir=storage_settings.raw_data_dir if storage_settings else config.raw_data_dir,
            replay_data_dir=storage_settings.replay_data_dir if storage_settings else config.replay_data_dir,
            raw_compression=storage_settings.raw_compression if storage_settings else config.raw_compression,
            allow_storage_fallback=config.allow_storage_fallback,
            allow_replay_storage_fallback=config.allow_replay_storage_fallback,
            collector_poll_interval_seconds=collector_settings.poll_interval_seconds,
            collector_cycle_player_limit=collector_settings.cycle_player_limit,
            player_lookup_chunk_size=collector_settings.player_lookup_chunk_size,
        )


def _config_path(value: str, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path


def _env_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default
