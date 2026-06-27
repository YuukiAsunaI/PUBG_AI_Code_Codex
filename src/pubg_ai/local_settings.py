from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
import json
import os
import shutil
import tempfile

from pubg_ai.time_utils import isoformat_kst


class LocalSettingsError(RuntimeError):
    """Raised when local program settings cannot be read or saved."""


@dataclass(frozen=True)
class StoragePathStatus:
    path: str
    exists: bool
    is_dir: bool
    writable: bool
    free_bytes: int | None
    error: str | None = None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StorageSettings:
    raw_data_dir: Path
    replay_data_dir: Path
    raw_compression: str = "gzip"
    updated_at: str | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "raw_data_dir": str(self.raw_data_dir),
            "replay_data_dir": str(self.replay_data_dir),
            "raw_compression": self.raw_compression,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class CollectorSettings:
    poll_interval_seconds: int = 180
    cycle_player_limit: int = 100
    player_lookup_chunk_size: int = 10
    updated_at: str | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "poll_interval_seconds": self.poll_interval_seconds,
            "cycle_player_limit": self.cycle_player_limit,
            "player_lookup_chunk_size": self.player_lookup_chunk_size,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class DiscordPermissionSettings:
    command_groups: dict[str, list[str]]
    user_grants: dict[str, list[str]]
    updated_at: str | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "command_groups": self.command_groups,
            "user_grants": self.user_grants,
            "updated_at": self.updated_at,
        }


DEFAULT_COMMAND_GROUPS: dict[str, list[str]] = {
    "register": ["pubg-register"],
    "profile_read": ["pubg-profile", "pubg-recent", "pubg-match", "pubg-weapon"],
    "ranking_read": ["pubg-ranking"],
    "replay_read": ["pubg-replay"],
    "settings_write": ["pubg-settings"],
    "admin": ["pubg-permission", "pubg-unregister", "pubg-delete-data"],
}


class LocalSettingsStore:
    def __init__(self, settings_file: Path, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or Path.cwd()
        self.settings_file = _resolve_config_path(settings_file, self.base_dir)

    def load_storage_settings(self) -> StorageSettings | None:
        payload = self._read_settings()
        if payload is None:
            return None

        storage = payload.get("storage")
        if not isinstance(storage, dict):
            return None

        raw_value = storage.get("raw_data_dir")
        replay_value = storage.get("replay_data_dir")
        if not isinstance(raw_value, str) or not isinstance(replay_value, str):
            return None

        compression = storage.get("raw_compression", "gzip")
        if compression not in {"gzip", "none"}:
            raise LocalSettingsError("raw_compression must be either 'gzip' or 'none'.")

        updated_at = storage.get("updated_at")
        if updated_at is not None and not isinstance(updated_at, str):
            updated_at = None

        return StorageSettings(
            raw_data_dir=_resolve_config_path(raw_value, self.base_dir),
            replay_data_dir=_resolve_config_path(replay_value, self.base_dir),
            raw_compression=compression,
            updated_at=updated_at,
        )

    def load_collector_settings(self, default: CollectorSettings | None = None) -> CollectorSettings:
        payload = self._read_settings() or {}
        collector = payload.get("collector")
        if not isinstance(collector, dict):
            return default or CollectorSettings()

        return _collector_settings_from_record(collector)

    def load_discord_permission_settings(self) -> DiscordPermissionSettings:
        payload = self._read_settings() or {}
        discord_permissions = payload.get("discord_permissions")
        if not isinstance(discord_permissions, dict):
            return DiscordPermissionSettings(
                command_groups=_copy_groups(DEFAULT_COMMAND_GROUPS),
                user_grants={},
            )

        return _discord_permissions_from_record(discord_permissions)

    def save_storage_settings(
        self,
        raw_data_dir: str | Path,
        replay_data_dir: str | Path,
        raw_compression: str = "gzip",
        create_dirs: bool = True,
        require_writable: bool = True,
    ) -> StorageSettings:
        if raw_compression not in {"gzip", "none"}:
            raise LocalSettingsError("raw_compression must be either 'gzip' or 'none'.")

        raw_path = _resolve_config_path(raw_data_dir, self.base_dir)
        replay_path = _resolve_config_path(replay_data_dir, self.base_dir)

        if require_writable:
            raw_status = check_storage_path(raw_path, create=create_dirs)
            replay_status = check_storage_path(replay_path, create=create_dirs)
            errors = [
                status.error or f"{status.path} is not writable"
                for status in (raw_status, replay_status)
                if not status.writable
            ]
            if errors:
                raise LocalSettingsError("; ".join(errors))
        elif create_dirs:
            raw_path.mkdir(parents=True, exist_ok=True)
            replay_path.mkdir(parents=True, exist_ok=True)

        settings = StorageSettings(
            raw_data_dir=raw_path,
            replay_data_dir=replay_path,
            raw_compression=raw_compression,
            updated_at=isoformat_kst(),
        )
        payload = self._read_settings() or {}
        payload["storage"] = settings.to_record()
        self._write_settings(payload)
        return settings

    def save_collector_settings(
        self,
        poll_interval_seconds: int,
        cycle_player_limit: int,
        player_lookup_chunk_size: int,
    ) -> CollectorSettings:
        settings = CollectorSettings(
            poll_interval_seconds=poll_interval_seconds,
            cycle_player_limit=cycle_player_limit,
            player_lookup_chunk_size=player_lookup_chunk_size,
            updated_at=isoformat_kst(),
        )
        _validate_collector_settings(settings)
        payload = self._read_settings() or {}
        payload["collector"] = settings.to_record()
        self._write_settings(payload)
        return settings

    def save_discord_permission_settings(
        self,
        command_groups: dict[str, list[str]],
        user_grants: dict[str, list[str]],
    ) -> DiscordPermissionSettings:
        settings = DiscordPermissionSettings(
            command_groups=_normalize_groups(command_groups),
            user_grants=_normalize_groups(user_grants),
            updated_at=isoformat_kst(),
        )
        _validate_discord_permission_settings(settings)
        payload = self._read_settings() or {}
        payload["discord_permissions"] = settings.to_record()
        self._write_settings(payload)
        return settings

    def get_storage_status(self) -> dict[str, StoragePathStatus]:
        settings = self.load_storage_settings()
        if settings is None:
            raise LocalSettingsError("storage settings have not been saved yet.")

        return {
            "raw_data_dir": check_storage_path(settings.raw_data_dir),
            "replay_data_dir": check_storage_path(settings.replay_data_dir),
        }

    def _write_settings(self, payload: dict[str, Any]) -> None:
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")

        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                delete=False,
                dir=self.settings_file.parent,
                prefix=f".{self.settings_file.name}.",
                suffix=".tmp",
            ) as temp_file:
                temp_file.write(body)
                temp_file.flush()
                os.fsync(temp_file.fileno())
                temp_path = Path(temp_file.name)

            os.replace(temp_path, self.settings_file)
        except OSError as exc:
            raise LocalSettingsError(f"failed to save local settings: {exc}") from exc

    def _read_settings(self) -> dict[str, Any] | None:
        if not self.settings_file.exists():
            return None

        try:
            with self.settings_file.open("r", encoding="utf-8") as file:
                payload = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            raise LocalSettingsError(f"failed to load local settings: {exc}") from exc

        if not isinstance(payload, dict):
            raise LocalSettingsError("local settings root must be a JSON object.")
        return payload


def check_storage_path(path: str | Path, create: bool = False) -> StoragePathStatus:
    resolved = Path(path).expanduser()

    try:
        if create:
            resolved.mkdir(parents=True, exist_ok=True)

        exists = resolved.exists()
        is_dir = resolved.is_dir()
        free_bytes = _free_bytes_for(resolved)

        if not exists:
            return StoragePathStatus(
                path=str(resolved),
                exists=False,
                is_dir=False,
                writable=False,
                free_bytes=free_bytes,
                error="path does not exist",
            )

        if not is_dir:
            return StoragePathStatus(
                path=str(resolved),
                exists=True,
                is_dir=False,
                writable=False,
                free_bytes=free_bytes,
                error="path is not a directory",
            )

        _write_probe(resolved)
        return StoragePathStatus(
            path=str(resolved),
            exists=True,
            is_dir=True,
            writable=True,
            free_bytes=free_bytes,
        )
    except OSError as exc:
        return StoragePathStatus(
            path=str(resolved),
            exists=resolved.exists(),
            is_dir=resolved.is_dir(),
            writable=False,
            free_bytes=_free_bytes_for(resolved),
            error=str(exc),
        )


def _write_probe(directory: Path) -> None:
    probe_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            delete=False,
            dir=directory,
            prefix=".pubg-ai-write-test.",
            suffix=".tmp",
        ) as temp_file:
            temp_file.write(b"ok")
            temp_file.flush()
            os.fsync(temp_file.fileno())
            probe_path = Path(temp_file.name)
    finally:
        if probe_path is not None:
            try:
                probe_path.unlink(missing_ok=True)
            except OSError:
                pass


def _free_bytes_for(path: Path) -> int | None:
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent

    if not probe.exists():
        return None

    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return None


def _resolve_config_path(value: str | Path, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path


def _collector_settings_from_record(record: dict[str, Any]) -> CollectorSettings:
    settings = CollectorSettings(
        poll_interval_seconds=_int_value(record.get("poll_interval_seconds"), 180),
        cycle_player_limit=_int_value(record.get("cycle_player_limit"), 100),
        player_lookup_chunk_size=_int_value(record.get("player_lookup_chunk_size"), 10),
        updated_at=_optional_str(record.get("updated_at")),
    )
    _validate_collector_settings(settings)
    return settings


def _validate_collector_settings(settings: CollectorSettings) -> None:
    if not 60 <= settings.poll_interval_seconds <= 300:
        raise LocalSettingsError("poll_interval_seconds must be between 60 and 300.")
    if not 1 <= settings.cycle_player_limit <= 100:
        raise LocalSettingsError("cycle_player_limit must be between 1 and 100.")
    if not 1 <= settings.player_lookup_chunk_size <= 10:
        raise LocalSettingsError("player_lookup_chunk_size must be between 1 and 10.")


def _discord_permissions_from_record(record: dict[str, Any]) -> DiscordPermissionSettings:
    command_groups = record.get("command_groups")
    user_grants = record.get("user_grants")
    settings = DiscordPermissionSettings(
        command_groups=_normalize_groups(command_groups if isinstance(command_groups, dict) else DEFAULT_COMMAND_GROUPS),
        user_grants=_normalize_groups(user_grants if isinstance(user_grants, dict) else {}),
        updated_at=_optional_str(record.get("updated_at")),
    )
    _validate_discord_permission_settings(settings)
    return settings


def _validate_discord_permission_settings(settings: DiscordPermissionSettings) -> None:
    known_groups = set(settings.command_groups)
    if not known_groups:
        raise LocalSettingsError("at least one Discord command group is required.")

    for user_id, groups in settings.user_grants.items():
        if not groups:
            raise LocalSettingsError(f"user {user_id} must have at least one group.")
        unknown = sorted(set(groups) - known_groups)
        if unknown:
            raise LocalSettingsError(f"user {user_id} has unknown permission groups: {', '.join(unknown)}.")


def _normalize_groups(value: dict[str, Any]) -> dict[str, list[str]]:
    normalized: dict[str, list[str]] = {}
    for key, raw_values in value.items():
        if not isinstance(key, str) or not key:
            raise LocalSettingsError("permission group keys must be non-empty strings.")
        if not isinstance(raw_values, list):
            raise LocalSettingsError(f"{key} must be a list.")

        normalized_values: list[str] = []
        for raw_value in raw_values:
            if not isinstance(raw_value, str) or not raw_value:
                raise LocalSettingsError(f"{key} contains an invalid value.")
            normalized_values.append(raw_value)

        normalized[key] = sorted(set(normalized_values))
    return normalized


def _copy_groups(groups: dict[str, list[str]]) -> dict[str, list[str]]:
    return {key: list(values) for key, values in groups.items()}


def _int_value(value: Any, default: int) -> int:
    if isinstance(value, int):
        return value
    return default


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None
