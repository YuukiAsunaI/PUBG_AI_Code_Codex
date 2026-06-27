from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
import hashlib
import json
import os
import tempfile

from pubg_ai.time_utils import isoformat_kst, now_kst, to_kst


ReplayArtifactType = Literal[
    "timeline",
    "snapshot",
    "map_snapshot",
    "thumbnail",
    "video",
    "gif",
    "cache",
]


class ReplayStorageError(RuntimeError):
    """Raised when replay artifact storage is unavailable or invalid."""


@dataclass(frozen=True)
class StoredReplayArtifact:
    match_id: str
    shard: str
    artifact_type: ReplayArtifactType
    storage_backend: str
    storage_root: str
    relative_path: str
    content_type: str
    size_bytes: int
    sha256: str
    stored_at: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


class ReplayArtifactStore:
    def __init__(
        self,
        root: Path,
        storage_root_name: str = "PUBG_REPLAY_DATA_DIR",
    ) -> None:
        self.root = root.expanduser()
        self.storage_root_name = storage_root_name

    def write_json(
        self,
        artifact_type: ReplayArtifactType,
        shard: str,
        match_id: str,
        payload: Any,
        filename: str,
        match_created_at: datetime | None = None,
    ) -> StoredReplayArtifact:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return self.write_bytes(
            artifact_type=artifact_type,
            shard=shard,
            match_id=match_id,
            data=body,
            filename=filename,
            content_type="application/json",
            match_created_at=match_created_at,
        )

    def write_bytes(
        self,
        artifact_type: ReplayArtifactType,
        shard: str,
        match_id: str,
        data: bytes,
        filename: str,
        content_type: str,
        match_created_at: datetime | None = None,
    ) -> StoredReplayArtifact:
        if artifact_type not in {
            "timeline",
            "snapshot",
            "map_snapshot",
            "thumbnail",
            "video",
            "gif",
            "cache",
        }:
            raise ValueError("artifact_type is not supported.")

        created_at = to_kst(match_created_at) if match_created_at is not None else now_kst()
        relative_path = self._relative_path(
            artifact_type,
            shard,
            match_id,
            filename,
            created_at,
        )
        target_path = self.root / Path(relative_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        digest = hashlib.sha256(data).hexdigest()
        self._atomic_write(target_path, data)

        return StoredReplayArtifact(
            match_id=match_id,
            shard=shard,
            artifact_type=artifact_type,
            storage_backend="local_file",
            storage_root=self.storage_root_name,
            relative_path=relative_path,
            content_type=content_type,
            size_bytes=len(data),
            sha256=digest,
            stored_at=isoformat_kst(),
        )

    def resolve_path(self, relative_path: str) -> Path:
        resolved = (self.root / relative_path).resolve()
        root = self.root.resolve()
        if root != resolved and root not in resolved.parents:
            raise ReplayStorageError("relative_path escapes the replay storage root.")
        return resolved

    def verify(self, stored: StoredReplayArtifact) -> bool:
        path = self.resolve_path(stored.relative_path)
        if not path.exists():
            return False
        return hashlib.sha256(path.read_bytes()).hexdigest() == stored.sha256

    def _relative_path(
        self,
        artifact_type: ReplayArtifactType,
        shard: str,
        match_id: str,
        filename: str,
        created_at: datetime,
    ) -> str:
        clean_type = self._safe_segment(artifact_type)
        clean_shard = self._safe_segment(shard)
        clean_match_id = self._safe_segment(match_id)
        clean_filename = self._safe_filename(filename)

        return str(
            Path(clean_type)
            / clean_shard
            / f"{created_at:%Y}"
            / f"{created_at:%m}"
            / f"{created_at:%d}"
            / clean_match_id
            / clean_filename
        ).replace("\\", "/")

    @staticmethod
    def _safe_segment(value: str) -> str:
        cleaned = "".join(ch for ch in value if ch.isalnum() or ch in {"-", "_"})
        if not cleaned:
            raise ValueError("path segment cannot be empty.")
        return cleaned

    @staticmethod
    def _safe_filename(value: str) -> str:
        name = Path(value).name
        cleaned = "".join(ch for ch in name if ch.isalnum() or ch in {"-", "_", "."})
        if not cleaned or cleaned in {".", ".."}:
            raise ValueError("filename cannot be empty.")
        return cleaned

    @staticmethod
    def _atomic_write(target_path: Path, data: bytes) -> None:
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                delete=False,
                dir=target_path.parent,
                prefix=f".{target_path.name}.",
                suffix=".tmp",
            ) as temp_file:
                temp_file.write(data)
                temp_file.flush()
                os.fsync(temp_file.fileno())
                temp_path = Path(temp_file.name)

            os.replace(temp_path, target_path)
        except OSError as exc:
            raise ReplayStorageError(f"failed to write replay artifact: {exc}") from exc
