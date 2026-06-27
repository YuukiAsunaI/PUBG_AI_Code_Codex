from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal
import shutil


AlertSeverity = Literal["ok", "error"]
AlertTarget = Literal["local_program", "discord"]

DEFAULT_MINIMUM_FREE_BYTES = 50 * 1024 * 1024 * 1024
DEFAULT_ALERT_TARGETS: tuple[AlertTarget, AlertTarget] = ("local_program", "discord")


@dataclass(frozen=True)
class StorageCapacityAlert:
    path: str
    severity: AlertSeverity
    message: str
    free_bytes: int | None
    minimum_free_bytes: int
    targets: tuple[AlertTarget, ...]

    @property
    def should_notify(self) -> bool:
        return self.severity == "error"

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["targets"] = list(self.targets)
        return record


def assess_storage_capacity(
    path: str | Path,
    minimum_free_bytes: int = DEFAULT_MINIMUM_FREE_BYTES,
    targets: tuple[AlertTarget, ...] = DEFAULT_ALERT_TARGETS,
) -> StorageCapacityAlert:
    resolved = Path(path).expanduser()

    if not resolved.exists():
        return StorageCapacityAlert(
            path=str(resolved),
            severity="error",
            message="storage path does not exist",
            free_bytes=None,
            minimum_free_bytes=minimum_free_bytes,
            targets=targets,
        )

    if not resolved.is_dir():
        return StorageCapacityAlert(
            path=str(resolved),
            severity="error",
            message="storage path is not a directory",
            free_bytes=None,
            minimum_free_bytes=minimum_free_bytes,
            targets=targets,
        )

    free_bytes = shutil.disk_usage(resolved).free
    if free_bytes < minimum_free_bytes:
        return StorageCapacityAlert(
            path=str(resolved),
            severity="error",
            message="storage free space is below the configured minimum; raw files must be preserved",
            free_bytes=free_bytes,
            minimum_free_bytes=minimum_free_bytes,
            targets=targets,
        )

    return StorageCapacityAlert(
        path=str(resolved),
        severity="ok",
        message="storage capacity is available",
        free_bytes=free_bytes,
        minimum_free_bytes=minimum_free_bytes,
        targets=targets,
    )

