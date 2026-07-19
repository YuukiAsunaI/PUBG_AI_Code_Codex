from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable
import os
import shutil


def canonical_storage_path(path: str | Path) -> Path:
    value = Path(path).expanduser()
    if not value.is_absolute():
        value = Path.cwd() / value
    return value.resolve(strict=False)


def storage_paths_overlap(first: str | Path, second: str | Path) -> bool:
    first_text = os.path.normcase(str(canonical_storage_path(first)))
    second_text = os.path.normcase(str(canonical_storage_path(second)))
    try:
        common = os.path.normcase(os.path.commonpath((first_text, second_text)))
    except ValueError:
        return False
    return common in {first_text, second_text}


def overlapping_named_root(
    candidate: str | Path,
    roots: Iterable[tuple[str, str | Path]],
) -> str | None:
    for label, root in roots:
        if storage_paths_overlap(candidate, root):
            return label
    return None


def storage_root_contract_conflicts(
    roots: Iterable[tuple[str, str | Path]],
) -> list[str]:
    records = [(label, canonical_storage_path(path)) for label, path in roots]
    conflicts: list[str] = []
    for index, (first_label, first_path) in enumerate(records):
        if first_path.parent == first_path:
            conflicts.append(f"{first_label} must not be a filesystem root")
        for second_label, second_path in records[index + 1 :]:
            if storage_paths_overlap(first_path, second_path):
                conflicts.append(
                    f"{first_label} overlaps {second_label}: {first_path} / {second_path}"
                )
    return conflicts


def inspect_directory_read_only(path: str | Path) -> dict[str, Any]:
    supplied = Path(path).expanduser()
    absolute = supplied.is_absolute()
    resolved = canonical_storage_path(supplied)
    result: dict[str, Any] = {
        "path": str(supplied),
        "resolved_path": str(resolved),
        "absolute": absolute,
        "exists": False,
        "is_dir": False,
        "is_symlink": False,
        "is_filesystem_root": resolved.parent == resolved,
        "free_bytes": None,
        "device_id": None,
        "inode": None,
        "error": None,
        "read_only_probe": True,
        "write_probe_performed": False,
    }
    if not absolute:
        result["error"] = "path is not absolute"
        return result
    try:
        result["is_symlink"] = supplied.is_symlink()
        result["exists"] = supplied.exists()
        if not result["exists"]:
            result["error"] = "path does not exist"
            return result
        result["is_dir"] = supplied.is_dir()
        if not result["is_dir"]:
            result["error"] = "path is not a directory"
            return result
        stat = supplied.stat()
        result["device_id"] = int(getattr(stat, "st_dev", 0))
        result["inode"] = int(getattr(stat, "st_ino", 0))
        result["free_bytes"] = int(shutil.disk_usage(supplied).free)
    except OSError as exc:
        result["error"] = str(exc)
    return result
