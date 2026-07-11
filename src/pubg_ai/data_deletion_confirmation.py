from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import json
import re
from typing import Any

from pubg_ai.data_deletion_preview import (
    MAX_PREVIEW_FILE_LIMIT,
    DataDeletionImpactPreviewService,
)
from pubg_ai.data_deletion_requests import DataDeletionRequest
from pubg_ai.time_utils import now_kst, to_kst


CONFIRMATION_CONTRACT_VERSION = "deletion-preview-confirmation-v1"
CONFIRMABLE_REQUEST_STATUSES = {"approved"}
SNAPSHOT_CAPTURE_STATUSES = {"pending", "approved"}


class DataDeletionConfirmationError(RuntimeError):
    """Raised when a preview snapshot or confirmation contract is invalid."""


@dataclass(frozen=True)
class DataDeletionPreviewSnapshot:
    id: int
    request_id: int
    contract_version: str
    fingerprint_sha256: str
    preview_json: dict[str, Any]
    manifest_json: dict[str, Any]
    catalog_complete: bool
    filesystem_issue_count: int
    candidate_row_count: int
    candidate_file_count: int
    captured_by: str
    capture_note: str | None
    captured_at_kst: datetime

    def to_record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "request_id": self.request_id,
            "contract_version": self.contract_version,
            "fingerprint_sha256": self.fingerprint_sha256,
            "preview_json": deepcopy(self.preview_json),
            "manifest_json": deepcopy(self.manifest_json),
            "catalog_complete": self.catalog_complete,
            "filesystem_issue_count": self.filesystem_issue_count,
            "candidate_row_count": self.candidate_row_count,
            "candidate_file_count": self.candidate_file_count,
            "captured_by": self.captured_by,
            "capture_note": self.capture_note,
            "captured_at_kst": _iso_kst(self.captured_at_kst),
        }

    def to_summary_record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "request_id": self.request_id,
            "contract_version": self.contract_version,
            "fingerprint_sha256": self.fingerprint_sha256,
            "catalog_complete": self.catalog_complete,
            "filesystem_issue_count": self.filesystem_issue_count,
            "candidate_row_count": self.candidate_row_count,
            "candidate_file_count": self.candidate_file_count,
            "captured_by": self.captured_by,
            "capture_note": self.capture_note,
            "captured_at_kst": _iso_kst(self.captured_at_kst),
        }


@dataclass(frozen=True)
class DataDeletionConfirmation:
    id: int
    request_id: int
    preview_snapshot_id: int
    contract_version: str
    fingerprint_sha256: str
    confirmed_by: str
    confirmation_text_sha256: str
    confirmation_note: str | None
    confirmed_at_kst: datetime

    def to_record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "request_id": self.request_id,
            "preview_snapshot_id": self.preview_snapshot_id,
            "contract_version": self.contract_version,
            "fingerprint_sha256": self.fingerprint_sha256,
            "confirmed_by": self.confirmed_by,
            "confirmation_text_sha256": self.confirmation_text_sha256,
            "confirmation_note": self.confirmation_note,
            "confirmed_at_kst": _iso_kst(self.confirmed_at_kst),
        }


class DataDeletionConfirmationService:
    def __init__(
        self,
        connection: Any,
        *,
        preview_service: DataDeletionImpactPreviewService,
    ) -> None:
        self.connection = connection
        self.preview_service = preview_service

    def capture_snapshot(
        self,
        request: DataDeletionRequest,
        *,
        actor_id: str,
        note: str | None = None,
        reference_kst: datetime | None = None,
    ) -> DataDeletionPreviewSnapshot:
        timestamp = _mysql_kst(reference_kst or now_kst())
        if request.status not in SNAPSHOT_CAPTURE_STATUSES:
            allowed = ", ".join(sorted(SNAPSHOT_CAPTURE_STATUSES))
            raise DataDeletionConfirmationError(
                f"deletion request {request.id} is {request.status}; snapshot requires {allowed}."
            )
        if request.status == "pending" and _mysql_kst(request.expires_at_kst) <= timestamp:
            raise DataDeletionConfirmationError(
                f"deletion request {request.id} expired before snapshot capture."
            )
        actor_id = _required_text(actor_id, "actor_id", 191)
        note = _optional_text(note, "note", 1000)
        preview_record = self.preview_service.build_preview(
            request,
            file_limit=MAX_PREVIEW_FILE_LIMIT,
        ).to_record()
        fingerprint, manifest = fingerprint_preview_record(preview_record)
        metrics = _preview_metrics(preview_record)

        _begin(self.connection)
        try:
            with self.connection.cursor() as cursor:
                self._assert_snapshot_capture_request_locked(
                    cursor,
                    request,
                    timestamp,
                )
                cursor.execute(
                    """
                    INSERT INTO data_deletion_preview_snapshots (
                        request_id,
                        contract_version,
                        fingerprint_sha256,
                        preview_json,
                        manifest_json,
                        catalog_complete,
                        filesystem_issue_count,
                        candidate_row_count,
                        candidate_file_count,
                        captured_by,
                        capture_note,
                        captured_at_kst
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        request.id,
                        CONFIRMATION_CONTRACT_VERSION,
                        fingerprint,
                        _json_dump(preview_record),
                        _json_dump(manifest),
                        metrics["catalog_complete"],
                        metrics["filesystem_issue_count"],
                        metrics["candidate_row_count"],
                        metrics["candidate_file_count"],
                        actor_id,
                        note,
                        timestamp,
                    ),
                )
                snapshot_id = int(cursor.lastrowid)
            _commit(self.connection)
        except Exception:
            _rollback(self.connection)
            raise
        return self.get_snapshot(snapshot_id)

    @staticmethod
    def _assert_snapshot_capture_request_locked(
        cursor: Any,
        request: DataDeletionRequest,
        timestamp: datetime,
    ) -> None:
        cursor.execute(
            """
            SELECT status, expires_at_kst
            FROM data_deletion_requests
            WHERE id = %s
            FOR UPDATE
            """,
            (request.id,),
        )
        row = cursor.fetchone()
        if not row:
            raise DataDeletionConfirmationError(
                f"deletion request {request.id} was not found during snapshot capture."
            )
        status = str(row["status"])
        if status not in SNAPSHOT_CAPTURE_STATUSES:
            raise DataDeletionConfirmationError(
                f"deletion request {request.id} changed to {status} before snapshot capture."
            )
        if status == "pending" and _mysql_kst(_datetime_value(row["expires_at_kst"])) <= timestamp:
            raise DataDeletionConfirmationError(
                f"deletion request {request.id} expired before snapshot capture."
            )

    def list_snapshots(
        self,
        request_id: int,
        *,
        limit: int = 20,
    ) -> list[DataDeletionPreviewSnapshot]:
        request_id = _positive_id(request_id, "request_id")
        if not 1 <= limit <= 100:
            raise DataDeletionConfirmationError("snapshot limit must be between 1 and 100.")
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM data_deletion_preview_snapshots
                WHERE request_id = %s
                ORDER BY captured_at_kst DESC, id DESC
                LIMIT %s
                """,
                (request_id, limit),
            )
            rows = cursor.fetchall()
        return [_snapshot_from_row(row) for row in rows]

    def get_snapshot(self, snapshot_id: int) -> DataDeletionPreviewSnapshot:
        snapshot_id = _positive_id(snapshot_id, "snapshot_id")
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM data_deletion_preview_snapshots WHERE id = %s",
                (snapshot_id,),
            )
            row = cursor.fetchone()
        if not row:
            raise DataDeletionConfirmationError(f"preview snapshot {snapshot_id} was not found.")
        return _snapshot_from_row(row)

    def list_confirmations(
        self,
        request_id: int,
        *,
        limit: int = 20,
    ) -> list[DataDeletionConfirmation]:
        request_id = _positive_id(request_id, "request_id")
        if not 1 <= limit <= 100:
            raise DataDeletionConfirmationError("confirmation limit must be between 1 and 100.")
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM data_deletion_confirmations
                WHERE request_id = %s
                ORDER BY confirmed_at_kst DESC, id DESC
                LIMIT %s
                """,
                (request_id, limit),
            )
            rows = cursor.fetchall()
        return [_confirmation_from_row(row) for row in rows]

    def confirmation_state(self, request: DataDeletionRequest) -> dict[str, Any]:
        snapshots = self.list_snapshots(request.id)
        confirmations = self.list_confirmations(request.id)
        latest = snapshots[0] if snapshots else None
        confirmed_snapshot_ids = {confirmation.preview_snapshot_id for confirmation in confirmations}
        already_confirmed = latest is not None and latest.id in confirmed_snapshot_ids
        blockers = confirmation_blockers(
            request,
            latest,
            already_confirmed=already_confirmed,
        )
        return {
            "request_id": request.id,
            "request_status": request.status,
            "contract_version": CONFIRMATION_CONTRACT_VERSION,
            "snapshot_capture_enabled": _snapshot_capture_enabled(request),
            "confirmation_allowed": latest is not None and not blockers,
            "confirmation_blockers": blockers,
            "expected_confirmation_text": (
                expected_confirmation_text(request.id, latest.fingerprint_sha256)
                if latest is not None
                else None
            ),
            "latest_snapshot": latest.to_summary_record() if latest is not None else None,
            "snapshots": [snapshot.to_summary_record() for snapshot in snapshots],
            "confirmations": [confirmation.to_record() for confirmation in confirmations],
            "execution_enabled": False,
        }

    def confirm_snapshot(
        self,
        request: DataDeletionRequest,
        *,
        snapshot_id: int,
        fingerprint_sha256: str,
        confirmation_text: str,
        actor_id: str,
        note: str | None = None,
        reference_kst: datetime | None = None,
    ) -> DataDeletionConfirmation:
        actor_id = _required_text(actor_id, "actor_id", 191)
        note = _optional_text(note, "note", 1000)
        snapshot_id = _positive_id(snapshot_id, "snapshot_id")
        supplied_fingerprint = _fingerprint(fingerprint_sha256)
        confirmation_text = _required_text(confirmation_text, "confirmation_text", 512)

        snapshots = self.list_snapshots(request.id, limit=1)
        if not snapshots:
            raise DataDeletionConfirmationError("capture an immutable preview snapshot first.")
        snapshot = snapshots[0]
        if snapshot.id != snapshot_id:
            raise DataDeletionConfirmationError(
                f"snapshot {snapshot_id} is not the latest snapshot for request {request.id}."
            )
        if not hmac.compare_digest(snapshot.fingerprint_sha256, supplied_fingerprint):
            raise DataDeletionConfirmationError("supplied fingerprint does not match the snapshot.")

        existing = self._confirmation_for_snapshot(snapshot.id)
        blockers = confirmation_blockers(
            request,
            snapshot,
            already_confirmed=existing is not None,
        )
        if blockers:
            raise DataDeletionConfirmationError("confirmation blocked: " + "; ".join(blockers))

        live_preview = self.preview_service.build_preview(
            request,
            file_limit=MAX_PREVIEW_FILE_LIMIT,
        ).to_record()
        live_fingerprint, _ = fingerprint_preview_record(live_preview)
        if not hmac.compare_digest(snapshot.fingerprint_sha256, live_fingerprint):
            raise DataDeletionConfirmationError(
                "current deletion impact differs from the snapshot; capture a new snapshot."
            )

        expected = expected_confirmation_text(request.id, snapshot.fingerprint_sha256)
        if not hmac.compare_digest(expected, confirmation_text):
            raise DataDeletionConfirmationError("confirmation text does not match the snapshot contract.")

        timestamp = _mysql_kst(reference_kst or now_kst())
        confirmation_hash = hashlib.sha256(confirmation_text.encode("utf-8")).hexdigest()
        _begin(self.connection)
        try:
            with self.connection.cursor() as cursor:
                self._assert_confirmation_contract_locked(cursor, request, snapshot)
                cursor.execute(
                    """
                    INSERT INTO data_deletion_confirmations (
                        request_id,
                        preview_snapshot_id,
                        contract_version,
                        fingerprint_sha256,
                        confirmed_by,
                        confirmation_text_sha256,
                        confirmation_note,
                        confirmed_at_kst
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        request.id,
                        snapshot.id,
                        CONFIRMATION_CONTRACT_VERSION,
                        snapshot.fingerprint_sha256,
                        actor_id,
                        confirmation_hash,
                        note,
                        timestamp,
                    ),
                )
                confirmation_id = int(cursor.lastrowid)
            _commit(self.connection)
        except Exception:
            _rollback(self.connection)
            raise
        return self.get_confirmation(confirmation_id)

    @staticmethod
    def _assert_confirmation_contract_locked(
        cursor: Any,
        request: DataDeletionRequest,
        snapshot: DataDeletionPreviewSnapshot,
    ) -> None:
        cursor.execute(
            "SELECT status FROM data_deletion_requests WHERE id = %s FOR UPDATE",
            (request.id,),
        )
        request_row = cursor.fetchone()
        if not request_row:
            raise DataDeletionConfirmationError(
                f"deletion request {request.id} was not found during confirmation."
            )
        locked_status = str(request_row["status"])
        if locked_status not in CONFIRMABLE_REQUEST_STATUSES:
            raise DataDeletionConfirmationError(
                f"deletion request {request.id} changed to {locked_status} before confirmation."
            )

        cursor.execute(
            """
            SELECT id, fingerprint_sha256
            FROM data_deletion_preview_snapshots
            WHERE request_id = %s
            ORDER BY captured_at_kst DESC, id DESC
            LIMIT 1
            FOR UPDATE
            """,
            (request.id,),
        )
        latest_row = cursor.fetchone()
        if not latest_row or int(latest_row["id"]) != snapshot.id:
            raise DataDeletionConfirmationError(
                "a newer preview snapshot appeared before confirmation."
            )
        if not hmac.compare_digest(
            _fingerprint(latest_row["fingerprint_sha256"]),
            snapshot.fingerprint_sha256,
        ):
            raise DataDeletionConfirmationError(
                "latest preview snapshot fingerprint changed before confirmation."
            )

        cursor.execute(
            """
            SELECT id
            FROM data_deletion_confirmations
            WHERE preview_snapshot_id = %s
            LIMIT 1
            FOR UPDATE
            """,
            (snapshot.id,),
        )
        if cursor.fetchone():
            raise DataDeletionConfirmationError(
                "latest preview snapshot was confirmed concurrently."
            )

    def get_confirmation(self, confirmation_id: int) -> DataDeletionConfirmation:
        confirmation_id = _positive_id(confirmation_id, "confirmation_id")
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM data_deletion_confirmations WHERE id = %s",
                (confirmation_id,),
            )
            row = cursor.fetchone()
        if not row:
            raise DataDeletionConfirmationError(
                f"deletion confirmation {confirmation_id} was not found."
            )
        return _confirmation_from_row(row)

    def _confirmation_for_snapshot(
        self,
        snapshot_id: int,
    ) -> DataDeletionConfirmation | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM data_deletion_confirmations
                WHERE preview_snapshot_id = %s
                ORDER BY id DESC
                LIMIT 1
                """,
                (snapshot_id,),
            )
            row = cursor.fetchone()
        return _confirmation_from_row(row) if row else None


def fingerprint_preview_record(
    preview_record: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    manifest = preview_manifest(preview_record)
    canonical = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest(), manifest


def preview_manifest(preview_record: dict[str, Any]) -> dict[str, Any]:
    record = deepcopy(preview_record)
    manifest = {
        "contract_version": CONFIRMATION_CONTRACT_VERSION,
        "request_id": record.get("request_id"),
        "target": record.get("target"),
        "deletion_scope": record.get("deletion_scope"),
        "included_sections": record.get("included_sections"),
        "matched_match_count": record.get("matched_match_count"),
        "candidate_row_count": record.get("candidate_row_count"),
        "preserved_reference_row_count": record.get("preserved_reference_row_count"),
        "row_impacts": _sorted_records(record.get("row_impacts")),
        "preserved_references": _sorted_records(record.get("preserved_references")),
        "raw_files": _catalog_manifest(record.get("raw_files")),
        "replay_files": _catalog_manifest(record.get("replay_files")),
        "verification": _verification_manifest(record.get("verification")),
    }
    return manifest


def expected_confirmation_text(request_id: int, fingerprint_sha256: str) -> str:
    return (
        f"CONFIRM DELETE REQUEST {_positive_id(request_id, 'request_id')} "
        f"{_fingerprint(fingerprint_sha256)}"
    )


def _snapshot_capture_enabled(request: DataDeletionRequest) -> bool:
    if request.status not in SNAPSHOT_CAPTURE_STATUSES:
        return False
    if request.status == "pending":
        return _mysql_kst(request.expires_at_kst) > _mysql_kst(now_kst())
    return True


def confirmation_blockers(
    request: DataDeletionRequest,
    snapshot: DataDeletionPreviewSnapshot | None,
    *,
    already_confirmed: bool,
) -> list[str]:
    blockers: list[str] = []
    if request.status not in CONFIRMABLE_REQUEST_STATUSES:
        blockers.append(f"request status must be approved, not {request.status}")
    if snapshot is None:
        blockers.append("immutable preview snapshot is required")
        return blockers
    if snapshot.request_id != request.id:
        blockers.append("snapshot belongs to another request")
    if snapshot.contract_version != CONFIRMATION_CONTRACT_VERSION:
        blockers.append("snapshot contract version is unsupported")
    if not snapshot.catalog_complete:
        blockers.append("file catalog is truncated")
    if snapshot.filesystem_issue_count:
        blockers.append(f"filesystem issues must be zero, found {snapshot.filesystem_issue_count}")
    if snapshot.candidate_row_count + snapshot.candidate_file_count <= 0:
        blockers.append("snapshot has no player-owned deletion candidates")
    if already_confirmed:
        blockers.append("latest snapshot is already confirmed")
    return blockers


def _catalog_manifest(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    files = []
    for file_record in value.get("files") or []:
        if not isinstance(file_record, dict):
            continue
        stable_file = dict(file_record)
        stable_file.pop("verification_error", None)
        files.append(stable_file)
    files.sort(
        key=lambda item: (
            str(item.get("source_table") or ""),
            int(item.get("record_id") or 0),
            str(item.get("match_id") or ""),
            str(item.get("relative_path") or ""),
        )
    )
    return {
        "category": value.get("category"),
        "included": value.get("included"),
        "total_records": value.get("total_records"),
        "total_declared_size_bytes": value.get("total_declared_size_bytes"),
        "deletion_candidate_records": value.get("deletion_candidate_records"),
        "shared_match_records": value.get("shared_match_records"),
        "listed_records": value.get("listed_records"),
        "truncated": value.get("truncated"),
        "files": files,
    }


def _verification_manifest(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    keys = (
        "catalog_complete",
        "listed_file_count",
        "filesystem_issue_count",
        "unsafe_path_count",
        "missing_file_count",
        "size_mismatch_count",
        "checksum_verification_performed",
    )
    return {key: value.get(key) for key in keys}


def _sorted_records(value: Any) -> list[dict[str, Any]]:
    records = [dict(item) for item in (value or []) if isinstance(item, dict)]
    records.sort(
        key=lambda item: (
            str(item.get("table") or ""),
            str(item.get("category") or ""),
            str(item.get("relationship") or ""),
        )
    )
    return records


def _preview_metrics(preview_record: dict[str, Any]) -> dict[str, int | bool]:
    verification = preview_record.get("verification")
    if not isinstance(verification, dict):
        raise DataDeletionConfirmationError("preview verification record is missing.")
    candidate_file_count = 0
    for key in ("raw_files", "replay_files"):
        catalog = preview_record.get(key)
        if isinstance(catalog, dict):
            candidate_file_count += _integer(catalog.get("deletion_candidate_records"))
    return {
        "catalog_complete": bool(verification.get("catalog_complete")),
        "filesystem_issue_count": _integer(verification.get("filesystem_issue_count")),
        "candidate_row_count": _integer(preview_record.get("candidate_row_count")),
        "candidate_file_count": candidate_file_count,
    }


def _snapshot_from_row(row: dict[str, Any]) -> DataDeletionPreviewSnapshot:
    return DataDeletionPreviewSnapshot(
        id=int(row["id"]),
        request_id=int(row["request_id"]),
        contract_version=str(row["contract_version"]),
        fingerprint_sha256=_fingerprint(row["fingerprint_sha256"]),
        preview_json=_json_object(row.get("preview_json"), "preview_json"),
        manifest_json=_json_object(row.get("manifest_json"), "manifest_json"),
        catalog_complete=bool(row.get("catalog_complete")),
        filesystem_issue_count=_integer(row.get("filesystem_issue_count")),
        candidate_row_count=_integer(row.get("candidate_row_count")),
        candidate_file_count=_integer(row.get("candidate_file_count")),
        captured_by=str(row["captured_by"]),
        capture_note=_optional_text(row.get("capture_note"), "capture_note", 1000),
        captured_at_kst=_datetime_value(row["captured_at_kst"]),
    )


def _confirmation_from_row(row: dict[str, Any]) -> DataDeletionConfirmation:
    return DataDeletionConfirmation(
        id=int(row["id"]),
        request_id=int(row["request_id"]),
        preview_snapshot_id=int(row["preview_snapshot_id"]),
        contract_version=str(row["contract_version"]),
        fingerprint_sha256=_fingerprint(row["fingerprint_sha256"]),
        confirmed_by=str(row["confirmed_by"]),
        confirmation_text_sha256=_fingerprint(row["confirmation_text_sha256"]),
        confirmation_note=_optional_text(row.get("confirmation_note"), "confirmation_note", 1000),
        confirmed_at_kst=_datetime_value(row["confirmed_at_kst"]),
    )


def _json_object(value: Any, label: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return deepcopy(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise DataDeletionConfirmationError(f"invalid {label} JSON.") from exc
        if isinstance(parsed, dict):
            return parsed
    raise DataDeletionConfirmationError(f"{label} must be a JSON object.")


def _json_dump(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    text = str(value).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", text):
        raise DataDeletionConfirmationError("fingerprint must be 64 lowercase hexadecimal characters.")
    return text


def _required_text(value: Any, label: str, max_length: int) -> str:
    text = str(value).strip()
    if not text:
        raise DataDeletionConfirmationError(f"{label} is required.")
    if len(text) > max_length:
        raise DataDeletionConfirmationError(f"{label} must be {max_length} characters or fewer.")
    return text


def _optional_text(value: Any, label: str, max_length: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > max_length:
        raise DataDeletionConfirmationError(f"{label} must be {max_length} characters or fewer.")
    return text


def _positive_id(value: Any, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise DataDeletionConfirmationError(f"{label} must be a positive integer.") from exc
    if parsed <= 0:
        raise DataDeletionConfirmationError(f"{label} must be a positive integer.")
    return parsed


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError) as exc:
        raise DataDeletionConfirmationError(f"invalid integer value: {value!r}.") from exc


def _datetime_value(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise DataDeletionConfirmationError(f"invalid datetime value: {value}.") from exc
    raise DataDeletionConfirmationError(f"invalid datetime value: {value!r}.")


def _mysql_kst(value: datetime) -> datetime:
    return to_kst(value).replace(tzinfo=None)


def _iso_kst(value: datetime) -> str:
    return to_kst(value).isoformat()


def _begin(connection: Any) -> None:
    begin = getattr(connection, "begin", None)
    if callable(begin):
        begin()


def _commit(connection: Any) -> None:
    commit = getattr(connection, "commit", None)
    if callable(commit):
        commit()


def _rollback(connection: Any) -> None:
    rollback = getattr(connection, "rollback", None)
    if callable(rollback):
        rollback()
