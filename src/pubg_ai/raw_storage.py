from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
import gzip
import hashlib
import json
import os
import tempfile

from pubg_ai.time_utils import isoformat_kst, now_kst, to_kst


PayloadType = Literal["match", "telemetry"]


class RawStorageError(RuntimeError):
    """Raised when raw payload storage is unavailable or invalid."""


@dataclass(frozen=True)
class StoredPayload:
    match_id: str
    shard: str
    payload_type: PayloadType
    storage_backend: str
    storage_root: str
    relative_path: str
    compression: str
    size_bytes: int
    sha256: str
    stored_at: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


class RawPayloadStore:
    def __init__(
        self,
        root: Path,
        compression: Literal["gzip", "none"] = "gzip",
        storage_root_name: str = "PUBG_RAW_DATA_DIR",
    ) -> None:
        if compression not in {"gzip", "none"}:
            raise ValueError("compression must be either 'gzip' or 'none'.")

        self.root = root.expanduser()
        self.compression = compression
        self.storage_root_name = storage_root_name

    def write_json(
        self,
        payload_type: PayloadType,
        shard: str,
        match_id: str,
        payload: Any,
        match_created_at: datetime | None = None,
    ) -> StoredPayload:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return self.write_json_bytes(
            payload_type=payload_type,
            shard=shard,
            match_id=match_id,
            payload_bytes=body,
            match_created_at=match_created_at,
        )

    def write_json_bytes(
        self,
        payload_type: PayloadType,
        shard: str,
        match_id: str,
        payload_bytes: bytes,
        match_created_at: datetime | None = None,
    ) -> StoredPayload:
        if payload_type not in {"match", "telemetry"}:
            raise ValueError("payload_type must be either 'match' or 'telemetry'.")

        created_at = to_kst(match_created_at) if match_created_at is not None else now_kst()
        relative_path = self._relative_path(payload_type, shard, match_id, created_at)
        target_path = self.root / Path(relative_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        stored_bytes = gzip.compress(payload_bytes) if self.compression == "gzip" else payload_bytes
        digest = hashlib.sha256(stored_bytes).hexdigest()

        self._atomic_write(target_path, stored_bytes)

        return StoredPayload(
            match_id=match_id,
            shard=shard,
            payload_type=payload_type,
            storage_backend="local_file",
            storage_root=self.storage_root_name,
            relative_path=relative_path,
            compression=self.compression,
            size_bytes=len(stored_bytes),
            sha256=digest,
            stored_at=isoformat_kst(),
        )

    def resolve_path(self, relative_path: str) -> Path:
        resolved = (self.root / relative_path).resolve()
        root = self.root.resolve()
        if root != resolved and root not in resolved.parents:
            raise RawStorageError("relative_path escapes the raw storage root.")
        return resolved

    def verify(self, stored: StoredPayload) -> bool:
        path = self.resolve_path(stored.relative_path)
        if not path.exists():
            return False
        return hashlib.sha256(path.read_bytes()).hexdigest() == stored.sha256

    def _relative_path(
        self,
        payload_type: PayloadType,
        shard: str,
        match_id: str,
        created_at: datetime,
    ) -> str:
        folder = "matches" if payload_type == "match" else "telemetry"
        suffix = ".json.gz" if self.compression == "gzip" else ".json"
        if payload_type == "telemetry":
            suffix = ".telemetry" + suffix

        clean_shard = self._safe_segment(shard)
        clean_match_id = self._safe_segment(match_id)

        return str(
            Path(folder)
            / clean_shard
            / f"{created_at:%Y}"
            / f"{created_at:%m}"
            / f"{created_at:%d}"
            / f"{clean_match_id}{suffix}"
        ).replace("\\", "/")

    @staticmethod
    def _safe_segment(value: str) -> str:
        cleaned = "".join(ch for ch in value if ch.isalnum() or ch in {"-", "_"})
        if not cleaned:
            raise ValueError("path segment cannot be empty.")
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
            raise RawStorageError(f"failed to write raw payload: {exc}") from exc
