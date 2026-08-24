from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Lock, RLock
from typing import Any, Callable, Iterator, TypeVar
import json
import os
import shutil
import tempfile
import time

from pubg_ai.file_io import atomic_write_bytes
from pubg_ai.discord_command_catalog import (
    default_command_groups,
    normalize_command_selection,
)
from pubg_ai.storage_alerts import DEFAULT_MINIMUM_FREE_BYTES
from pubg_ai.storage_contract import storage_root_contract_conflicts
from pubg_ai.time_utils import isoformat_kst


class LocalSettingsError(RuntimeError):
    """Raised when local program settings cannot be read or saved."""


NUMBER_FORMATS = frozenset({"grouped", "korean_units", "plain"})


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
    backup_data_dir: Path | None = None
    quarantine_data_dir: Path | None = None
    raw_compression: str = "gzip"
    updated_at: str | None = None

    def to_record(self) -> dict[str, Any]:
        record = {
            "raw_data_dir": str(self.raw_data_dir),
            "replay_data_dir": str(self.replay_data_dir),
            "raw_compression": self.raw_compression,
            "updated_at": self.updated_at,
        }
        if self.backup_data_dir is not None:
            record["backup_data_dir"] = str(self.backup_data_dir)
        if self.quarantine_data_dir is not None:
            record["quarantine_data_dir"] = str(self.quarantine_data_dir)
        return record


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
    guild_user_grants: dict[str, dict[str, list[str]]]
    global_admin_user_ids: list[str]
    updated_at: str | None = None
    command_aliases: dict[str, str] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return {
            "command_groups": self.command_groups,
            "user_grants": self.user_grants,
            "guild_user_grants": self.guild_user_grants,
            "global_admin_user_ids": self.global_admin_user_ids,
            "command_aliases": self.command_aliases,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class DiscordScopeSettings:
    guild_ranking_scopes: dict[str, str]
    public_profile_default: bool = True
    updated_at: str | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "guild_ranking_scopes": self.guild_ranking_scopes,
            "public_profile_default": self.public_profile_default,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class DiscordBotSettings:
    auto_start: bool = False
    command_prefix: str = "!"
    guild_enabled_commands: dict[str, list[str]] = field(default_factory=dict)
    updated_at: str | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "auto_start": self.auto_start,
            "command_prefix": self.command_prefix,
            "guild_enabled_commands": self.guild_enabled_commands,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class WebSettings:
    local_web_base_url: str | None = None
    updated_at: str | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "local_web_base_url": self.local_web_base_url,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class DisplaySettings:
    number_format: str = "grouped"
    updated_at: str | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "number_format": self.number_format,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class AlertSettings:
    minimum_free_bytes: int = DEFAULT_MINIMUM_FREE_BYTES
    discord_channel_ids: list[str] | None = None
    storage_alerts_enabled: bool = True
    worker_error_alerts_enabled: bool = True
    updated_at: str | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "minimum_free_bytes": self.minimum_free_bytes,
            "discord_channel_ids": list(self.discord_channel_ids or []),
            "storage_alerts_enabled": self.storage_alerts_enabled,
            "worker_error_alerts_enabled": self.worker_error_alerts_enabled,
            "updated_at": self.updated_at,
        }


DEFAULT_COMMAND_GROUPS: dict[str, list[str]] = default_command_groups()

_T = TypeVar("_T")
_PROCESS_LOCKS: dict[str, RLock] = {}
_PROCESS_LOCKS_GUARD = Lock()


FORBIDDEN_LOCAL_SETTING_KEYS = {
    "PUBG_API_KEY",
    "DISCORD_BOT_TOKEN",
    "pubg_api_key",
    "discord_bot_token",
    "api_key",
    "bot_token",
    "token",
}


class LocalSettingsStore:
    def __init__(self, settings_file: Path, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or Path.cwd()
        self.settings_file = _resolve_config_path(settings_file, self.base_dir)
        self.lock_file = self.settings_file.with_name(f"{self.settings_file.name}.lock")

    def load_storage_settings(self) -> StorageSettings | None:
        payload = self._read_settings()
        if payload is None:
            return None

        storage = payload.get("storage")
        if not isinstance(storage, dict):
            return None

        raw_value = storage.get("raw_data_dir")
        replay_value = storage.get("replay_data_dir")
        backup_value = storage.get("backup_data_dir")
        quarantine_value = storage.get("quarantine_data_dir")
        if not isinstance(raw_value, str) or not isinstance(replay_value, str):
            return None
        if backup_value is not None and not isinstance(backup_value, str):
            raise LocalSettingsError("backup_data_dir must be a path string.")
        if quarantine_value is not None and not isinstance(quarantine_value, str):
            raise LocalSettingsError("quarantine_data_dir must be a path string.")

        compression = storage.get("raw_compression", "gzip")
        if compression not in {"gzip", "none"}:
            raise LocalSettingsError("raw_compression must be either 'gzip' or 'none'.")

        updated_at = storage.get("updated_at")
        if updated_at is not None and not isinstance(updated_at, str):
            updated_at = None

        return StorageSettings(
            raw_data_dir=_resolve_config_path(raw_value, self.base_dir),
            replay_data_dir=_resolve_config_path(replay_value, self.base_dir),
            backup_data_dir=(
                _resolve_config_path(backup_value, self.base_dir)
                if isinstance(backup_value, str) and backup_value.strip()
                else None
            ),
            quarantine_data_dir=(
                _resolve_config_path(quarantine_value, self.base_dir)
                if isinstance(quarantine_value, str) and quarantine_value.strip()
                else None
            ),
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
                guild_user_grants={},
                global_admin_user_ids=[],
            )

        return _discord_permissions_from_record(discord_permissions)

    def load_discord_scope_settings(self) -> DiscordScopeSettings:
        payload = self._read_settings() or {}
        discord_scopes = payload.get("discord_scopes")
        if not isinstance(discord_scopes, dict):
            return DiscordScopeSettings(guild_ranking_scopes={})

        return _discord_scopes_from_record(discord_scopes)

    def load_discord_bot_settings(self) -> DiscordBotSettings:
        payload = self._read_settings() or {}
        discord_bot = payload.get("discord_bot")
        if not isinstance(discord_bot, dict):
            return DiscordBotSettings()
        return _discord_bot_settings_from_record(discord_bot)

    def load_web_settings(self) -> WebSettings | None:
        payload = self._read_settings() or {}
        web = payload.get("web")
        if not isinstance(web, dict):
            return None

        return _web_settings_from_record(web)

    def load_display_settings(self) -> DisplaySettings:
        payload = self._read_settings() or {}
        display = payload.get("display")
        if not isinstance(display, dict):
            return DisplaySettings()
        return _display_settings_from_record(display)

    def load_alert_settings(self) -> AlertSettings:
        payload = self._read_settings() or {}
        alerts = payload.get("alerts")
        if not isinstance(alerts, dict):
            return AlertSettings(discord_channel_ids=[])

        return _alert_settings_from_record(alerts)

    def save_storage_settings(
        self,
        raw_data_dir: str | Path,
        replay_data_dir: str | Path,
        backup_data_dir: str | Path | None = None,
        quarantine_data_dir: str | Path | None = None,
        raw_compression: str = "gzip",
        create_dirs: bool = True,
        require_writable: bool = True,
    ) -> StorageSettings:
        if raw_compression not in {"gzip", "none"}:
            raise LocalSettingsError("raw_compression must be either 'gzip' or 'none'.")

        raw_path = _resolve_config_path(raw_data_dir, self.base_dir)
        replay_path = _resolve_config_path(replay_data_dir, self.base_dir)
        current = self.load_storage_settings()
        backup_path = (
            _resolve_config_path(backup_data_dir, self.base_dir)
            if backup_data_dir is not None
            else current.backup_data_dir if current is not None else None
        )
        quarantine_path = (
            _resolve_config_path(quarantine_data_dir, self.base_dir)
            if quarantine_data_dir is not None
            else current.quarantine_data_dir if current is not None else None
        )
        named_paths = [
            ("raw_data_dir", raw_path),
            ("replay_data_dir", replay_path),
        ]
        if backup_path is not None:
            named_paths.append(("backup_data_dir", backup_path))
        if quarantine_path is not None:
            named_paths.append(("quarantine_data_dir", quarantine_path))
        conflicts = storage_root_contract_conflicts(named_paths)
        if conflicts:
            raise LocalSettingsError("; ".join(conflicts))
        paths = [path for _, path in named_paths]

        if require_writable:
            statuses = [check_storage_path(path, create=create_dirs) for path in paths]
            errors = [
                status.error or f"{status.path} is not writable"
                for status in statuses
                if not status.writable
            ]
            if errors:
                raise LocalSettingsError("; ".join(errors))
        elif create_dirs:
            for path in paths:
                path.mkdir(parents=True, exist_ok=True)

        settings = StorageSettings(
            raw_data_dir=raw_path,
            replay_data_dir=replay_path,
            backup_data_dir=backup_path,
            quarantine_data_dir=quarantine_path,
            raw_compression=raw_compression,
            updated_at=isoformat_kst(),
        )
        self._update_settings_section("storage", settings.to_record())
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
        self._update_settings_section("collector", settings.to_record())
        return settings

    def save_discord_permission_settings(
        self,
        command_groups: dict[str, list[str]],
        user_grants: dict[str, list[str]],
        guild_user_grants: dict[str, dict[str, list[str]]] | None = None,
        global_admin_user_ids: list[str] | None = None,
        command_aliases: dict[str, str] | None = None,
    ) -> DiscordPermissionSettings:
        settings = DiscordPermissionSettings(
            command_groups=_normalize_groups(command_groups),
            user_grants=_normalize_groups(user_grants),
            guild_user_grants=_normalize_guild_grants(guild_user_grants or {}),
            global_admin_user_ids=_normalize_id_list(global_admin_user_ids or []),
            updated_at=isoformat_kst(),
            command_aliases=_normalize_command_aliases(command_aliases or {}),
        )
        _validate_discord_permission_settings(settings)
        self._update_settings_section("discord_permissions", settings.to_record())
        return settings

    def update_discord_permission_settings(
        self,
        update: Callable[[DiscordPermissionSettings], DiscordPermissionSettings],
    ) -> DiscordPermissionSettings:
        def mutate(payload: dict[str, Any]) -> DiscordPermissionSettings:
            current_record = payload.get("discord_permissions")
            current = (
                _discord_permissions_from_record(current_record)
                if isinstance(current_record, dict)
                else DiscordPermissionSettings(
                    command_groups=_copy_groups(DEFAULT_COMMAND_GROUPS),
                    user_grants={},
                    guild_user_grants={},
                    global_admin_user_ids=[],
                )
            )
            candidate = update(current)
            if not isinstance(candidate, DiscordPermissionSettings):
                raise LocalSettingsError("Discord permission updater returned invalid settings.")
            settings = DiscordPermissionSettings(
                command_groups=_normalize_groups(candidate.command_groups),
                user_grants=_normalize_groups(candidate.user_grants),
                guild_user_grants=_normalize_guild_grants(candidate.guild_user_grants),
                global_admin_user_ids=_normalize_id_list(candidate.global_admin_user_ids),
                updated_at=isoformat_kst(),
                command_aliases=_normalize_command_aliases(candidate.command_aliases),
            )
            _validate_discord_permission_settings(settings)
            payload["discord_permissions"] = settings.to_record()
            return settings

        return self._mutate_settings(mutate)

    def save_discord_scope_settings(
        self,
        guild_ranking_scopes: dict[str, str],
        public_profile_default: bool = True,
    ) -> DiscordScopeSettings:
        settings = DiscordScopeSettings(
            guild_ranking_scopes=_normalize_scope_map(guild_ranking_scopes),
            public_profile_default=public_profile_default,
            updated_at=isoformat_kst(),
        )
        self._update_settings_section("discord_scopes", settings.to_record())
        return settings

    def save_discord_bot_settings(
        self,
        *,
        auto_start: bool,
        command_prefix: str,
        guild_enabled_commands: dict[str, list[str]] | None = None,
    ) -> DiscordBotSettings:
        settings = DiscordBotSettings(
            auto_start=bool(auto_start),
            command_prefix=_normalize_command_prefix(command_prefix),
            guild_enabled_commands=_normalize_guild_enabled_commands(
                guild_enabled_commands or {}
            ),
            updated_at=isoformat_kst(),
        )
        self._update_settings_section("discord_bot", settings.to_record())
        return settings

    def save_web_settings(self, local_web_base_url: str | None) -> WebSettings:
        settings = WebSettings(
            local_web_base_url=_normalize_optional_url(local_web_base_url),
            updated_at=isoformat_kst(),
        )
        self._update_settings_section("web", settings.to_record())
        return settings

    def save_display_settings(self, number_format: str) -> DisplaySettings:
        settings = DisplaySettings(
            number_format=_normalize_number_format(number_format),
            updated_at=isoformat_kst(),
        )
        self._update_settings_section("display", settings.to_record())
        return settings

    def save_alert_settings(
        self,
        minimum_free_bytes: int,
        discord_channel_ids: list[str] | None = None,
        storage_alerts_enabled: bool = True,
        worker_error_alerts_enabled: bool = True,
    ) -> AlertSettings:
        settings = AlertSettings(
            minimum_free_bytes=int(minimum_free_bytes),
            discord_channel_ids=_normalize_id_list(discord_channel_ids or []),
            storage_alerts_enabled=bool(storage_alerts_enabled),
            worker_error_alerts_enabled=bool(worker_error_alerts_enabled),
            updated_at=isoformat_kst(),
        )
        _validate_alert_settings(settings)
        self._update_settings_section("alerts", settings.to_record())
        return settings

    def get_storage_status(self) -> dict[str, StoragePathStatus]:
        settings = self.load_storage_settings()
        if settings is None:
            raise LocalSettingsError("storage settings have not been saved yet.")

        statuses = {
            "raw_data_dir": check_storage_path(settings.raw_data_dir),
            "replay_data_dir": check_storage_path(settings.replay_data_dir),
        }
        if settings.backup_data_dir is not None:
            statuses["backup_data_dir"] = check_storage_path(settings.backup_data_dir)
        if settings.quarantine_data_dir is not None:
            statuses["quarantine_data_dir"] = check_storage_path(
                settings.quarantine_data_dir
            )
        return statuses

    def _update_settings_section(self, section: str, record: dict[str, Any]) -> None:
        def update(payload: dict[str, Any]) -> None:
            payload[section] = record

        self._mutate_settings(update)

    def _mutate_settings(self, update: Callable[[dict[str, Any]], _T]) -> _T:
        with _exclusive_settings_lock(self.lock_file):
            payload = self._read_settings() or {}
            result = update(payload)
            self._write_settings_unlocked(payload)
            return result

    def _write_settings(self, payload: dict[str, Any]) -> None:
        with _exclusive_settings_lock(self.lock_file):
            self._write_settings_unlocked(payload)

    def _write_settings_unlocked(self, payload: dict[str, Any]) -> None:
        _reject_forbidden_secret_keys(payload)
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")

        try:
            atomic_write_bytes(self.settings_file, body)
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


def _process_lock_for(path: Path) -> RLock:
    key = str(path.resolve()).casefold()
    with _PROCESS_LOCKS_GUARD:
        return _PROCESS_LOCKS.setdefault(key, RLock())


@contextmanager
def _exclusive_settings_lock(lock_file: Path) -> Iterator[None]:
    process_lock = _process_lock_for(lock_file)
    with process_lock:
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle = lock_file.open("a+b")
            _acquire_file_lock(handle)
        except OSError as exc:
            try:
                handle.close()
            except (NameError, OSError):
                pass
            raise LocalSettingsError(f"failed to lock local settings: {exc}") from exc

        try:
            yield
        finally:
            try:
                _release_file_lock(handle)
            finally:
                handle.close()


def _acquire_file_lock(handle: Any) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + 30.0
        while True:
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("timed out waiting for the local settings lock")
                time.sleep(0.05)

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _release_file_lock(handle: Any) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


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


def _display_settings_from_record(record: dict[str, Any]) -> DisplaySettings:
    return DisplaySettings(
        number_format=_normalize_number_format(record.get("number_format", "grouped")),
        updated_at=_optional_str(record.get("updated_at")),
    )


def _normalize_number_format(value: Any) -> str:
    normalized = str(value or "").strip()
    if normalized not in NUMBER_FORMATS:
        allowed = ", ".join(sorted(NUMBER_FORMATS))
        raise LocalSettingsError(f"number_format must be one of: {allowed}.")
    return normalized


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
    normalized_command_groups = _normalize_groups(
        command_groups if isinstance(command_groups, dict) else DEFAULT_COMMAND_GROUPS
    )
    settings = DiscordPermissionSettings(
        command_groups=_merge_default_command_groups(normalized_command_groups),
        user_grants=_normalize_groups(user_grants if isinstance(user_grants, dict) else {}),
        guild_user_grants=_normalize_guild_grants(
            record.get("guild_user_grants") if isinstance(record.get("guild_user_grants"), dict) else {}
        ),
        global_admin_user_ids=_normalize_id_list(
            record.get("global_admin_user_ids") if isinstance(record.get("global_admin_user_ids"), list) else []
        ),
        updated_at=_optional_str(record.get("updated_at")),
        command_aliases=_normalize_command_aliases(
            record.get("command_aliases") if isinstance(record.get("command_aliases"), dict) else {}
        ),
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

    for guild_id, guild_grants in settings.guild_user_grants.items():
        if not guild_grants:
            raise LocalSettingsError(f"guild {guild_id} must have at least one user grant.")
        for user_id, groups in guild_grants.items():
            if not groups:
                raise LocalSettingsError(f"user {user_id} in guild {guild_id} must have at least one group.")
            unknown = sorted(set(groups) - known_groups)
            if unknown:
                raise LocalSettingsError(
                    f"user {user_id} in guild {guild_id} has unknown permission groups: {', '.join(unknown)}."
                )


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


def _normalize_command_aliases(value: dict[str, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for raw_alias, raw_target in value.items():
        if not isinstance(raw_alias, str) or not raw_alias.strip():
            raise LocalSettingsError("Discord command aliases must have non-empty names.")
        if not isinstance(raw_target, str) or not raw_target.strip():
            raise LocalSettingsError(f"Discord command alias {raw_alias!r} has an invalid target.")
        normalized[raw_alias.strip()] = raw_target.strip()
    return dict(sorted(normalized.items()))


def _merge_default_command_groups(groups: dict[str, list[str]]) -> dict[str, list[str]]:
    merged = _copy_groups(groups)
    for group, default_values in DEFAULT_COMMAND_GROUPS.items():
        merged[group] = sorted(set(merged.get(group, []) + default_values))
    # Older settings placed soft unregister beside destructive admin commands.
    # Keep the commands in their dedicated least-privilege group after upgrade.
    merged["admin"] = sorted(
        set(merged.get("admin", [])) - set(DEFAULT_COMMAND_GROUPS["player_manage"])
    )
    return merged


def _copy_groups(groups: dict[str, list[str]]) -> dict[str, list[str]]:
    return {key: list(values) for key, values in groups.items()}


def _normalize_guild_grants(value: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    normalized: dict[str, dict[str, list[str]]] = {}
    for guild_id, raw_grants in value.items():
        if not isinstance(guild_id, str) or not guild_id:
            raise LocalSettingsError("guild ids must be non-empty strings.")
        if not isinstance(raw_grants, dict):
            raise LocalSettingsError(f"guild {guild_id} grants must be an object.")
        normalized[guild_id] = _normalize_groups(raw_grants)
    return normalized


def _normalize_id_list(values: list[Any]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value:
            raise LocalSettingsError("ids must be non-empty strings.")
        normalized.append(value)
    return sorted(set(normalized))


def _normalize_scope_map(value: dict[str, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for guild_id, scope in value.items():
        if not isinstance(guild_id, str) or not guild_id:
            raise LocalSettingsError("guild ids must be non-empty strings.")
        if not isinstance(scope, str) or scope not in {"guild", "global"}:
            raise LocalSettingsError("guild ranking scope must be either 'guild' or 'global'.")
        normalized[guild_id] = scope
    return normalized


def _discord_scopes_from_record(record: dict[str, Any]) -> DiscordScopeSettings:
    guild_ranking_scopes = record.get("guild_ranking_scopes")
    public_profile_default = record.get("public_profile_default", True)
    if not isinstance(public_profile_default, bool):
        public_profile_default = True

    return DiscordScopeSettings(
        guild_ranking_scopes=_normalize_scope_map(
            guild_ranking_scopes if isinstance(guild_ranking_scopes, dict) else {}
        ),
        public_profile_default=public_profile_default,
        updated_at=_optional_str(record.get("updated_at")),
    )


def _discord_bot_settings_from_record(record: dict[str, Any]) -> DiscordBotSettings:
    auto_start = record.get("auto_start", False)
    if not isinstance(auto_start, bool):
        auto_start = False
    command_prefix = record.get("command_prefix", "!")
    if not isinstance(command_prefix, str):
        command_prefix = "!"
    guild_enabled_commands = record.get("guild_enabled_commands")
    return DiscordBotSettings(
        auto_start=auto_start,
        command_prefix=_normalize_command_prefix(command_prefix),
        guild_enabled_commands=_normalize_guild_enabled_commands(
            guild_enabled_commands if isinstance(guild_enabled_commands, dict) else {}
        ),
        updated_at=_optional_str(record.get("updated_at")),
    )


def _normalize_command_prefix(value: str) -> str:
    normalized = str(value or "")
    if not 1 <= len(normalized) <= 5:
        raise LocalSettingsError("Discord command prefix must be 1-5 characters.")
    if any(character.isspace() or ord(character) < 32 for character in normalized):
        raise LocalSettingsError("Discord command prefix cannot contain whitespace or control characters.")
    return normalized


def _normalize_guild_enabled_commands(value: dict[str, Any]) -> dict[str, list[str]]:
    normalized: dict[str, list[str]] = {}
    for raw_guild_id, raw_commands in value.items():
        guild_id = str(raw_guild_id or "").strip()
        if not guild_id or len(guild_id) > 32 or not guild_id.isdigit():
            raise LocalSettingsError("Discord guild ids must contain only digits.")
        if not isinstance(raw_commands, list):
            raise LocalSettingsError(f"Discord guild {guild_id} commands must be a list.")
        try:
            normalized[guild_id] = normalize_command_selection(raw_commands)
        except ValueError as exc:
            raise LocalSettingsError(str(exc)) from exc
    return dict(sorted(normalized.items()))


def _web_settings_from_record(record: dict[str, Any]) -> WebSettings:
    return WebSettings(
        local_web_base_url=_normalize_optional_url(record.get("local_web_base_url")),
        updated_at=_optional_str(record.get("updated_at")),
    )


def _alert_settings_from_record(record: dict[str, Any]) -> AlertSettings:
    settings = AlertSettings(
        minimum_free_bytes=_int_value(record.get("minimum_free_bytes"), DEFAULT_MINIMUM_FREE_BYTES),
        discord_channel_ids=_normalize_id_list(
            record.get("discord_channel_ids") if isinstance(record.get("discord_channel_ids"), list) else []
        ),
        storage_alerts_enabled=record.get("storage_alerts_enabled") is not False,
        worker_error_alerts_enabled=record.get("worker_error_alerts_enabled") is not False,
        updated_at=_optional_str(record.get("updated_at")),
    )
    _validate_alert_settings(settings)
    return settings


def _validate_alert_settings(settings: AlertSettings) -> None:
    if settings.minimum_free_bytes < 0:
        raise LocalSettingsError("minimum_free_bytes must be 0 or greater.")


def _normalize_optional_url(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise LocalSettingsError("local_web_base_url must be a string.")

    stripped = value.strip().rstrip("/")
    if not stripped:
        return None
    if not (stripped.startswith("http://") or stripped.startswith("https://")):
        raise LocalSettingsError("local_web_base_url must start with http:// or https://.")
    return stripped


def _reject_forbidden_secret_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(key, str) and key in FORBIDDEN_LOCAL_SETTING_KEYS:
                raise LocalSettingsError(f"{key} must stay in .env and cannot be saved in local settings.")
            _reject_forbidden_secret_keys(nested)
    elif isinstance(value, list):
        for item in value:
            _reject_forbidden_secret_keys(item)


def _int_value(value: Any, default: int) -> int:
    if isinstance(value, int):
        return value
    return default


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None
