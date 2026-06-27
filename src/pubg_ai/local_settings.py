from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import json
import os
import shutil
import tempfile


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


class LocalSettingsStore:
    def __init__(self, settings_file: Path, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or Path.cwd()
        self.settings_file = _resolve_config_path(settings_file, self.base_dir)

    def load_storage_settings(self) -> StorageSettings | None:
        if not self.settings_file.exists():
            return None

        try:
            with self.settings_file.open("r", encoding="utf-8") as file:
                payload = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            raise LocalSettingsError(f"failed to load local settings: {exc}") from exc

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
            updated_at=datetime.now(UTC).isoformat(),
        )
        self._write_settings({"storage": settings.to_record()})
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
