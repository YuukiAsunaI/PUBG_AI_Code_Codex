from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
import os


def _env_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class AppConfig:
    raw_data_dir: Path
    replay_data_dir: Path
    local_web_base_url: str | None = None
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
            local_web_base_url=_normalize_base_url(values.get("PUBG_LOCAL_WEB_BASE_URL")),
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
            local_web_base_url=config.local_web_base_url,
            raw_compression=storage_settings.raw_compression if storage_settings else config.raw_compression,
            allow_storage_fallback=config.allow_storage_fallback,
            allow_replay_storage_fallback=config.allow_replay_storage_fallback,
            collector_poll_interval_seconds=collector_settings.poll_interval_seconds,
            collector_cycle_player_limit=collector_settings.cycle_player_limit,
            player_lookup_chunk_size=collector_settings.player_lookup_chunk_size,
        )


@dataclass(frozen=True)
class SecretStatus:
    name: str
    configured: bool
    length: int = 0

    def to_record(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "configured": self.configured,
            "length": self.length,
        }


@dataclass(frozen=True)
class SecretConfig:
    pubg_api_key: str | None = None
    discord_bot_token: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "SecretConfig":
        values = env or os.environ
        return cls(
            pubg_api_key=_non_empty(values.get("PUBG_API_KEY")),
            discord_bot_token=_non_empty(values.get("DISCORD_BOT_TOKEN")),
        )

    def status(self) -> dict[str, SecretStatus]:
        return {
            "PUBG_API_KEY": _secret_status("PUBG_API_KEY", self.pubg_api_key),
            "DISCORD_BOT_TOKEN": _secret_status("DISCORD_BOT_TOKEN", self.discord_bot_token),
        }


@dataclass(frozen=True)
class DatabaseConfig:
    host: str = "127.0.0.1"
    port: int = 3306
    database: str = "pubg_ai"
    user: str = "pubg_ai"
    password: str = ""
    charset: str = "utf8mb4"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "DatabaseConfig":
        values = env or os.environ
        return cls(
            host=values.get("MYSQL_HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=_env_int(values.get("MYSQL_PORT"), 3306),
            database=values.get("MYSQL_DATABASE", "pubg_ai").strip() or "pubg_ai",
            user=values.get("MYSQL_USER", "pubg_ai").strip() or "pubg_ai",
            password=values.get("MYSQL_PASSWORD", ""),
            charset=values.get("MYSQL_CHARSET", "utf8mb4").strip() or "utf8mb4",
        )

    def safe_record(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "user": self.user,
            "password": {
                "configured": bool(self.password),
                "length": len(self.password),
            },
            "charset": self.charset,
        }


@dataclass(frozen=True)
class RuntimeConfig:
    app: AppConfig
    database: DatabaseConfig
    secrets: SecretConfig

    @classmethod
    def from_sources(
        cls,
        env: Mapping[str, str] | None = None,
        base_dir: Path | None = None,
        env_file: Path | str = ".env",
    ) -> "RuntimeConfig":
        base = base_dir or Path.cwd()
        values = dict(load_dotenv_values(_config_path(str(env_file), base)))
        if env is None:
            values.update(os.environ)
        else:
            values.update(env)
        return cls(
            app=AppConfig.from_sources(values, base),
            database=DatabaseConfig.from_env(values),
            secrets=SecretConfig.from_env(values),
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


def _normalize_base_url(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip().rstrip("/")
    if not stripped:
        return None
    if not (stripped.startswith("http://") or stripped.startswith("https://")):
        raise ValueError("PUBG_LOCAL_WEB_BASE_URL must start with http:// or https://.")
    return stripped


def load_dotenv_values(env_file: Path) -> dict[str, str]:
    if not env_file.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = _parse_dotenv_value(raw_value.strip())
    return values


def _parse_dotenv_value(raw_value: str) -> str:
    if len(raw_value) >= 2 and raw_value[0] == raw_value[-1] and raw_value[0] in {"'", '"'}:
        value = raw_value[1:-1]
        if raw_value[0] == '"':
            value = value.replace('\\"', '"').replace("\\\\", "\\")
        return value
    return raw_value


def _non_empty(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _secret_status(name: str, value: str | None) -> SecretStatus:
    return SecretStatus(name=name, configured=bool(value), length=len(value or ""))
