from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import json
from typing import Any

from pubg_ai.data_deletion_confirmation import (
    CONFIRMATION_CONTRACT_VERSION,
    DataDeletionConfirmation,
    DataDeletionConfirmationService,
    DataDeletionPreviewSnapshot,
    fingerprint_preview_record,
)
from pubg_ai.data_deletion_preview import (
    MAX_PREVIEW_FILE_LIMIT,
    DataDeletionImpactPreviewService,
)
from pubg_ai.data_deletion_requests import DataDeletionRequest
from pubg_ai.time_utils import now_kst, to_kst


DRY_RUN_CONTRACT_VERSION = "deletion-dry-run-v2"
DRY_RUN_REQUEST_STATUSES = {"approved"}
EXECUTION_BLOCKERS = (
    "executor_not_implemented",
    "backup_evidence_not_recorded",
)

_REGISTRATION_TABLE_ORDER = {
    "player_collection_states": 0,
    "player_aliases": 1,
    "registered_players": 2,
}
_PLAYER_TABLE_ORDER = {
    "player_combat_loadout_snapshots": 0,
    "player_combat_location_events": 1,
    "player_position_samples": 2,
    "player_landing_events": 3,
    "player_movement_summaries": 4,
    "player_item_events": 5,
    "player_item_match_stats": 6,
    "player_weapon_match_stats": 7,
    "player_match_combat_summaries": 8,
    "match_participants": 9,
    "raw_player_snapshots": 10,
}
_AUDIT_TABLE_EXCLUSIONS = (
    "data_deletion_requests",
    "data_deletion_request_events",
    "data_deletion_preview_snapshots",
    "data_deletion_confirmations",
    "data_deletion_dry_run_plans",
    "data_deletion_backup_evidence",
    "data_deletion_rehearsal_runs",
)


class DataDeletionDryRunError(RuntimeError):
    """Raised when a deletion dry-run plan cannot be produced safely."""


@dataclass(frozen=True)
class DataDeletionDryRunPlan:
    id: int
    request_id: int
    preview_snapshot_id: int
    confirmation_id: int
    contract_version: str
    source_fingerprint_sha256: str
    plan_fingerprint_sha256: str
    plan_json: dict[str, Any]
    operation_count: int
    candidate_row_count: int
    candidate_file_count: int
    candidate_file_bytes: int
    excluded_row_count: int
    excluded_file_count: int
    generated_by: str
    generation_note: str | None
    generated_at_kst: datetime

    def to_record(self) -> dict[str, Any]:
        return {
            **self.to_summary_record(),
            "plan_json": deepcopy(self.plan_json),
        }

    def to_summary_record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "request_id": self.request_id,
            "preview_snapshot_id": self.preview_snapshot_id,
            "confirmation_id": self.confirmation_id,
            "contract_version": self.contract_version,
            "source_fingerprint_sha256": self.source_fingerprint_sha256,
            "plan_fingerprint_sha256": self.plan_fingerprint_sha256,
            "operation_count": self.operation_count,
            "candidate_row_count": self.candidate_row_count,
            "candidate_file_count": self.candidate_file_count,
            "candidate_file_bytes": self.candidate_file_bytes,
            "excluded_row_count": self.excluded_row_count,
            "excluded_file_count": self.excluded_file_count,
            "generated_by": self.generated_by,
            "generation_note": self.generation_note,
            "generated_at_kst": _iso_kst(self.generated_at_kst),
            "execution_enabled": False,
            "execution_ready": False,
        }


class DataDeletionDryRunService:
    def __init__(
        self,
        connection: Any,
        *,
        preview_service: DataDeletionImpactPreviewService,
        confirmation_service: DataDeletionConfirmationService,
    ) -> None:
        self.connection = connection
        self.preview_service = preview_service
        self.confirmation_service = confirmation_service

    def create_plan(
        self,
        request: DataDeletionRequest,
        *,
        actor_id: str,
        note: str | None = None,
        reference_kst: datetime | None = None,
    ) -> DataDeletionDryRunPlan:
        actor_id = _required_text(actor_id, "actor_id", 191)
        note = _optional_text(note, "note", 1000)
        snapshots = self.confirmation_service.list_snapshots(request.id, limit=1)
        snapshot = snapshots[0] if snapshots else None
        confirmations = self.confirmation_service.list_confirmations(request.id, limit=100)
        confirmation = _confirmation_for_snapshot(confirmations, snapshot)
        blockers = dry_run_generation_blockers(request, snapshot, confirmation)
        if blockers:
            raise DataDeletionDryRunError("dry-run plan blocked: " + "; ".join(blockers))
        assert snapshot is not None
        assert confirmation is not None

        snapshot_fingerprint, snapshot_manifest = fingerprint_preview_record(snapshot.preview_json)
        if not hmac.compare_digest(snapshot.fingerprint_sha256, snapshot_fingerprint):
            raise DataDeletionDryRunError("immutable preview snapshot fingerprint is invalid.")
        if snapshot.manifest_json != snapshot_manifest:
            raise DataDeletionDryRunError("immutable preview snapshot manifest is invalid.")

        live_preview = self.preview_service.build_preview(
            request,
            file_limit=MAX_PREVIEW_FILE_LIMIT,
        ).to_record()
        live_fingerprint, _ = fingerprint_preview_record(live_preview)
        if not hmac.compare_digest(snapshot.fingerprint_sha256, live_fingerprint):
            raise DataDeletionDryRunError(
                "current deletion impact differs from the confirmed snapshot; capture and confirm a new snapshot."
            )

        plan_manifest = build_dry_run_plan_manifest(request, snapshot, confirmation)
        plan_fingerprint = fingerprint_dry_run_plan(plan_manifest)
        metrics = _plan_metrics(plan_manifest)
        timestamp = _mysql_kst(reference_kst or now_kst())

        _begin(self.connection)
        try:
            with self.connection.cursor() as cursor:
                self._assert_plan_contract_locked(
                    cursor,
                    request,
                    snapshot,
                    confirmation,
                )
                cursor.execute(
                    """
                    INSERT INTO data_deletion_dry_run_plans (
                        request_id,
                        preview_snapshot_id,
                        confirmation_id,
                        contract_version,
                        source_fingerprint_sha256,
                        plan_fingerprint_sha256,
                        plan_json,
                        operation_count,
                        candidate_row_count,
                        candidate_file_count,
                        candidate_file_bytes,
                        excluded_row_count,
                        excluded_file_count,
                        generated_by,
                        generation_note,
                        generated_at_kst
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        request.id,
                        snapshot.id,
                        confirmation.id,
                        DRY_RUN_CONTRACT_VERSION,
                        snapshot.fingerprint_sha256,
                        plan_fingerprint,
                        _json_dump(plan_manifest),
                        metrics["operation_count"],
                        metrics["candidate_row_count"],
                        metrics["candidate_file_count"],
                        metrics["candidate_file_bytes"],
                        metrics["excluded_row_count"],
                        metrics["excluded_file_count"],
                        actor_id,
                        note,
                        timestamp,
                    ),
                )
                plan_id = int(cursor.lastrowid)
            _commit(self.connection)
        except Exception:
            _rollback(self.connection)
            raise
        return self.get_plan(plan_id)

    @staticmethod
    def _assert_plan_contract_locked(
        cursor: Any,
        request: DataDeletionRequest,
        snapshot: DataDeletionPreviewSnapshot,
        confirmation: DataDeletionConfirmation,
    ) -> None:
        cursor.execute(
            "SELECT status FROM data_deletion_requests WHERE id = %s FOR UPDATE",
            (request.id,),
        )
        request_row = cursor.fetchone()
        if not request_row:
            raise DataDeletionDryRunError(
                f"deletion request {request.id} was not found during dry-run planning."
            )
        status = str(request_row["status"])
        if status not in DRY_RUN_REQUEST_STATUSES:
            raise DataDeletionDryRunError(
                f"deletion request {request.id} changed to {status} before dry-run planning."
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
        snapshot_row = cursor.fetchone()
        if not snapshot_row or int(snapshot_row["id"]) != snapshot.id:
            raise DataDeletionDryRunError(
                "a newer preview snapshot appeared before dry-run planning."
            )
        if not hmac.compare_digest(
            _fingerprint(snapshot_row["fingerprint_sha256"]),
            snapshot.fingerprint_sha256,
        ):
            raise DataDeletionDryRunError(
                "latest preview snapshot fingerprint changed before dry-run planning."
            )

        cursor.execute(
            """
            SELECT id, request_id, preview_snapshot_id, fingerprint_sha256
            FROM data_deletion_confirmations
            WHERE id = %s
            FOR UPDATE
            """,
            (confirmation.id,),
        )
        confirmation_row = cursor.fetchone()
        if not confirmation_row:
            raise DataDeletionDryRunError(
                "confirmed snapshot record disappeared before dry-run planning."
            )
        if (
            int(confirmation_row["request_id"]) != request.id
            or int(confirmation_row["preview_snapshot_id"]) != snapshot.id
            or not hmac.compare_digest(
                _fingerprint(confirmation_row["fingerprint_sha256"]),
                snapshot.fingerprint_sha256,
            )
        ):
            raise DataDeletionDryRunError(
                "confirmation contract changed before dry-run planning."
            )

    def get_plan(self, plan_id: int) -> DataDeletionDryRunPlan:
        plan_id = _positive_id(plan_id, "plan_id")
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM data_deletion_dry_run_plans WHERE id = %s",
                (plan_id,),
            )
            row = cursor.fetchone()
        if not row:
            raise DataDeletionDryRunError(f"deletion dry-run plan {plan_id} was not found.")
        return _plan_from_row(row)

    def list_plans(
        self,
        request_id: int,
        *,
        limit: int = 20,
    ) -> list[DataDeletionDryRunPlan]:
        request_id = _positive_id(request_id, "request_id")
        if not 1 <= limit <= 100:
            raise DataDeletionDryRunError("dry-run plan limit must be between 1 and 100.")
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM data_deletion_dry_run_plans
                WHERE request_id = %s
                ORDER BY generated_at_kst DESC, id DESC
                LIMIT %s
                """,
                (request_id, limit),
            )
            rows = cursor.fetchall()
        return [_plan_from_row(row) for row in rows]

    def plan_state(self, request: DataDeletionRequest) -> dict[str, Any]:
        snapshots = self.confirmation_service.list_snapshots(request.id, limit=1)
        snapshot = snapshots[0] if snapshots else None
        confirmations = self.confirmation_service.list_confirmations(request.id, limit=100)
        confirmation = _confirmation_for_snapshot(confirmations, snapshot)
        plans = self.list_plans(request.id)
        blockers = dry_run_generation_blockers(request, snapshot, confirmation)
        return {
            "request_id": request.id,
            "request_status": request.status,
            "contract_version": DRY_RUN_CONTRACT_VERSION,
            "generation_allowed": not blockers,
            "generation_blockers": blockers,
            "live_revalidation_required": True,
            "latest_confirmed_snapshot_id": snapshot.id if confirmation else None,
            "latest_confirmation_id": confirmation.id if confirmation else None,
            "latest_plan": plans[0].to_record() if plans else None,
            "plans": [plan.to_summary_record() for plan in plans],
            "execution_blockers": list(EXECUTION_BLOCKERS),
            "execution_enabled": False,
            "execution_ready": False,
        }


def dry_run_generation_blockers(
    request: DataDeletionRequest,
    snapshot: DataDeletionPreviewSnapshot | None,
    confirmation: DataDeletionConfirmation | None,
) -> list[str]:
    blockers: list[str] = []
    if request.status not in DRY_RUN_REQUEST_STATUSES:
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
    if confirmation is None:
        blockers.append("latest preview snapshot requires fingerprint-bound confirmation")
        return blockers
    if confirmation.request_id != request.id:
        blockers.append("confirmation belongs to another request")
    if confirmation.preview_snapshot_id != snapshot.id:
        blockers.append("confirmation does not bind the latest snapshot")
    if confirmation.contract_version != CONFIRMATION_CONTRACT_VERSION:
        blockers.append("confirmation contract version is unsupported")
    if not hmac.compare_digest(
        confirmation.fingerprint_sha256,
        snapshot.fingerprint_sha256,
    ):
        blockers.append("confirmation fingerprint does not match the latest snapshot")
    return blockers


def build_dry_run_plan_manifest(
    request: DataDeletionRequest,
    snapshot: DataDeletionPreviewSnapshot,
    confirmation: DataDeletionConfirmation,
) -> dict[str, Any]:
    preview = deepcopy(snapshot.preview_json)
    target = preview.get("target") if isinstance(preview.get("target"), dict) else {}
    if (
        str(target.get("account_id") or "") != request.account_id
        or str(target.get("shard") or "") != request.shard
    ):
        raise DataDeletionDryRunError("preview target does not match the deletion request.")

    database_operations = _database_operations(preview, request)
    file_operations = _file_operations(preview)
    row_exclusions = _row_exclusions(preview)
    file_exclusions = _file_exclusions(preview)
    candidate_row_count = sum(item["estimated_rows"] for item in database_operations)
    candidate_file_count = len(file_operations)
    if candidate_row_count != snapshot.candidate_row_count:
        raise DataDeletionDryRunError("planned database row count does not match the snapshot.")
    if candidate_file_count != snapshot.candidate_file_count:
        raise DataDeletionDryRunError("planned file count does not match the snapshot.")

    phases = [
        {
            "sequence": 1,
            "key": "revalidate_contract",
            "description": "Lock and revalidate request, latest snapshot, confirmation, and live fingerprint.",
            "mutation_enabled": False,
        },
        {
            "sequence": 2,
            "key": "verify_backup_prerequisites",
            "description": "Require recorded, restorable database and player-artifact backups.",
            "mutation_enabled": False,
        },
        {
            "sequence": 3,
            "key": "quarantine_player_files",
            "description": "Quarantine only player-owned replay artifacts after backup verification.",
            "mutation_enabled": False,
        },
        {
            "sequence": 4,
            "key": "remove_player_database_rows",
            "description": "Remove player-owned derived, participant, and raw-player rows in dependency order.",
            "mutation_enabled": False,
        },
        {
            "sequence": 5,
            "key": "remove_replay_metadata",
            "description": "Remove replay metadata only after its player-owned files are quarantined.",
            "mutation_enabled": False,
        },
        {
            "sequence": 6,
            "key": "remove_registration",
            "description": "Remove collection state, aliases, and registration last.",
            "mutation_enabled": False,
        },
        {
            "sequence": 7,
            "key": "verify_postconditions",
            "description": "Verify target rows are absent and all protected shared data remains intact.",
            "mutation_enabled": False,
        },
    ]
    backup_prerequisites = [
        {
            "key": "mysql_target_backup",
            "required": candidate_row_count > 0,
            "evidence_status": "not_recorded",
            "description": "Create and restore-test a backup covering every candidate table and registration row.",
        },
        {
            "key": "replay_artifact_backup",
            "required": candidate_file_count > 0,
            "evidence_status": "not_recorded",
            "description": "Back up every player-owned replay artifact before quarantine.",
        },
        {
            "key": "quarantine_capacity_check",
            "required": candidate_file_count > 0,
            "evidence_status": "not_recorded",
            "description": "Verify quarantine free space exceeds the declared candidate file bytes.",
        },
        {
            "key": "backup_integrity_verification",
            "required": True,
            "evidence_status": "not_recorded",
            "description": "Record checksums and a successful restore rehearsal before any mutation.",
        },
    ]
    revalidation_checks = [
        {"key": "request_status", "expected": "approved", "observed": request.status},
        {"key": "latest_snapshot_id", "expected": snapshot.id, "observed": snapshot.id},
        {
            "key": "confirmation_snapshot_id",
            "expected": snapshot.id,
            "observed": confirmation.preview_snapshot_id,
        },
        {
            "key": "source_fingerprint",
            "expected": snapshot.fingerprint_sha256,
            "observed": confirmation.fingerprint_sha256,
        },
        {"key": "catalog_complete", "expected": True, "observed": snapshot.catalog_complete},
        {
            "key": "filesystem_issue_count",
            "expected": 0,
            "observed": snapshot.filesystem_issue_count,
        },
    ]
    for check in revalidation_checks:
        check["status"] = "passed_at_generation" if check["expected"] == check["observed"] else "failed"
        check["required_again_before_execution"] = True

    return {
        "contract_version": DRY_RUN_CONTRACT_VERSION,
        "request_id": request.id,
        "preview_snapshot_id": snapshot.id,
        "confirmation_id": confirmation.id,
        "source_fingerprint_sha256": snapshot.fingerprint_sha256,
        "target": {
            "account_id": request.account_id,
            "shard": request.shard,
            "player_name": request.player_name,
        },
        "deletion_scope": request.deletion_scope,
        "safety": {
            "read_only": True,
            "execution_enabled": False,
            "execution_ready": False,
            "execution_blockers": list(EXECUTION_BLOCKERS),
            "raw_match_files_protected": True,
            "shared_match_rows_protected": True,
        },
        "metrics": {
            "candidate_row_count": candidate_row_count,
            "candidate_file_count": candidate_file_count,
            "candidate_file_bytes": sum(item["declared_size_bytes"] for item in file_operations),
            "excluded_row_count": sum(item["row_count"] for item in row_exclusions),
            "excluded_file_count": sum(item["file_count"] for item in file_exclusions),
        },
        "phases": phases,
        "revalidation_checks": revalidation_checks,
        "backup_prerequisites": backup_prerequisites,
        "database_operations": database_operations,
        "file_operations": file_operations,
        "row_exclusions": row_exclusions,
        "file_exclusions": file_exclusions,
        "audit_table_exclusions": [
            {
                "table": table,
                "reason": "immutable deletion workflow audit data is never a target",
            }
            for table in _AUDIT_TABLE_EXCLUSIONS
        ],
        "postcondition_checks": [
            "candidate player-owned row counts must become zero",
            "quarantined replay files must match the planned path, size, and SHA-256",
            "shared match and raw payload row counts must remain unchanged",
            "raw match and telemetry files must remain present",
            "deletion request, event, snapshot, confirmation, and plan audit rows must remain present",
        ],
    }


def fingerprint_dry_run_plan(plan_manifest: dict[str, Any]) -> str:
    canonical = json.dumps(
        plan_manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _database_operations(
    preview: dict[str, Any],
    request: DataDeletionRequest,
) -> list[dict[str, Any]]:
    candidates = [
        dict(item)
        for item in preview.get("row_impacts") or []
        if isinstance(item, dict) and bool(item.get("deletion_candidate"))
    ]

    def operation_order(item: dict[str, Any]) -> tuple[int, int, str]:
        table = str(item.get("table") or "")
        if table == "replay_artifacts":
            return (1, 0, table)
        if table in _REGISTRATION_TABLE_ORDER:
            return (2, _REGISTRATION_TABLE_ORDER[table], table)
        return (0, _PLAYER_TABLE_ORDER.get(table, 100), table)

    operations: list[dict[str, Any]] = []
    for sequence, item in enumerate(sorted(candidates, key=operation_order), start=1):
        table = str(item.get("table") or "")
        if table in _REGISTRATION_TABLE_ORDER:
            phase = "remove_registration"
        elif table == "replay_artifacts":
            phase = "remove_replay_metadata"
        else:
            phase = "remove_player_database_rows"
        operations.append(
            {
                "sequence": sequence,
                "phase": phase,
                "action": "delete_rows_planned",
                "table": table,
                "selector": _selector_contract(table, request),
                "estimated_rows": _integer(item.get("row_count")),
                "source_category": str(item.get("category") or ""),
                "source_relationship": str(item.get("relationship") or ""),
                "mutation_enabled": False,
            }
        )
    return operations


def _selector_contract(table: str, request: DataDeletionRequest) -> dict[str, Any]:
    target = {"account_id": request.account_id, "shard": request.shard}
    if table == "player_collection_states":
        return {
            "kind": "registered_player_join",
            **target,
            "via_table": "registered_players",
            "via_column": "registered_player_id",
        }
    if table in {"player_aliases", "registered_players", "raw_player_snapshots", "replay_artifacts"}:
        return {"kind": "target_identity", **target}
    return {
        "kind": "player_match_scope",
        **target,
        "account_column": "account_id",
        "match_join": "matches.match_id",
    }


def _file_operations(preview: dict[str, Any]) -> list[dict[str, Any]]:
    catalog = preview.get("replay_files")
    files = catalog.get("files") if isinstance(catalog, dict) else []
    candidates = [
        dict(item)
        for item in files or []
        if isinstance(item, dict) and bool(item.get("deletion_candidate"))
    ]
    candidates.sort(
        key=lambda item: (
            str(item.get("relative_path") or ""),
            _integer(item.get("record_id")),
        )
    )
    return [
        {
            "sequence": sequence,
            "phase": "quarantine_player_files",
            "action": "quarantine_file_planned",
            "source_table": str(item.get("source_table") or ""),
            "record_id": _integer(item.get("record_id")),
            "artifact_type": str(item.get("file_type") or ""),
            "match_id": str(item.get("match_id") or ""),
            "storage_root": str(item.get("storage_root") or ""),
            "relative_path": str(item.get("relative_path") or ""),
            "declared_size_bytes": _integer(item.get("declared_size_bytes")),
            "sha256": str(item.get("sha256") or ""),
            "verification_status": str(item.get("verification_status") or ""),
            "ownership": str(item.get("ownership") or ""),
            "mutation_enabled": False,
        }
        for sequence, item in enumerate(candidates, start=1)
    ]


def _row_exclusions(preview: dict[str, Any]) -> list[dict[str, Any]]:
    exclusions: list[dict[str, Any]] = []
    for source_key in ("row_impacts", "preserved_references"):
        for item in preview.get(source_key) or []:
            if not isinstance(item, dict):
                continue
            if source_key == "row_impacts" and bool(item.get("deletion_candidate")):
                continue
            exclusions.append(
                {
                    "table": str(item.get("table") or ""),
                    "category": str(item.get("category") or ""),
                    "relationship": str(item.get("relationship") or ""),
                    "row_count": _integer(item.get("row_count")),
                    "reason": "shared or externally-referenced data is protected",
                }
            )
    exclusions.sort(key=lambda item: (item["table"], item["category"], item["relationship"]))
    return exclusions


def _file_exclusions(preview: dict[str, Any]) -> list[dict[str, Any]]:
    exclusions: list[dict[str, Any]] = []
    for catalog_key in ("raw_files", "replay_files"):
        catalog = preview.get(catalog_key)
        if not isinstance(catalog, dict) or not bool(catalog.get("included")):
            continue
        total = _integer(catalog.get("total_records"))
        candidate_count = _integer(catalog.get("deletion_candidate_records"))
        protected_count = max(0, total - candidate_count)
        if protected_count <= 0:
            continue
        exclusions.append(
            {
                "category": str(catalog.get("category") or catalog_key),
                "file_count": protected_count,
                "declared_size_bytes": _integer(catalog.get("total_declared_size_bytes")),
                "ownership": "shared_match" if catalog_key == "raw_files" else "protected",
                "reason": (
                    "raw match and telemetry files are shared match evidence"
                    if catalog_key == "raw_files"
                    else "file is not a player-owned deletion candidate"
                ),
            }
        )
    exclusions.sort(key=lambda item: item["category"])
    return exclusions


def _confirmation_for_snapshot(
    confirmations: list[DataDeletionConfirmation],
    snapshot: DataDeletionPreviewSnapshot | None,
) -> DataDeletionConfirmation | None:
    if snapshot is None:
        return None
    return next(
        (
            confirmation
            for confirmation in confirmations
            if confirmation.preview_snapshot_id == snapshot.id
        ),
        None,
    )


def _plan_metrics(plan_manifest: dict[str, Any]) -> dict[str, int]:
    metrics = plan_manifest.get("metrics")
    if not isinstance(metrics, dict):
        raise DataDeletionDryRunError("dry-run plan metrics are missing.")
    values = {
        "operation_count": len(plan_manifest.get("database_operations") or [])
        + len(plan_manifest.get("file_operations") or []),
        "candidate_row_count": _integer(metrics.get("candidate_row_count")),
        "candidate_file_count": _integer(metrics.get("candidate_file_count")),
        "candidate_file_bytes": _integer(metrics.get("candidate_file_bytes")),
        "excluded_row_count": _integer(metrics.get("excluded_row_count")),
        "excluded_file_count": _integer(metrics.get("excluded_file_count")),
    }
    return values


def _plan_from_row(row: dict[str, Any]) -> DataDeletionDryRunPlan:
    return DataDeletionDryRunPlan(
        id=int(row["id"]),
        request_id=int(row["request_id"]),
        preview_snapshot_id=int(row["preview_snapshot_id"]),
        confirmation_id=int(row["confirmation_id"]),
        contract_version=str(row["contract_version"]),
        source_fingerprint_sha256=_fingerprint(row["source_fingerprint_sha256"]),
        plan_fingerprint_sha256=_fingerprint(row["plan_fingerprint_sha256"]),
        plan_json=_json_object(row.get("plan_json"), "plan_json"),
        operation_count=_integer(row.get("operation_count")),
        candidate_row_count=_integer(row.get("candidate_row_count")),
        candidate_file_count=_integer(row.get("candidate_file_count")),
        candidate_file_bytes=_integer(row.get("candidate_file_bytes")),
        excluded_row_count=_integer(row.get("excluded_row_count")),
        excluded_file_count=_integer(row.get("excluded_file_count")),
        generated_by=str(row["generated_by"]),
        generation_note=_optional_text(row.get("generation_note"), "generation_note", 1000),
        generated_at_kst=_datetime_value(row["generated_at_kst"]),
    )


def _json_object(value: Any, label: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return deepcopy(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise DataDeletionDryRunError(f"invalid {label} JSON.") from exc
        if isinstance(parsed, dict):
            return parsed
    raise DataDeletionDryRunError(f"{label} must be a JSON object.")


def _json_dump(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    text = str(value).strip().lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise DataDeletionDryRunError(
            "fingerprint must be 64 lowercase hexadecimal characters."
        )
    return text


def _required_text(value: Any, label: str, max_length: int) -> str:
    text = str(value).strip()
    if not text:
        raise DataDeletionDryRunError(f"{label} is required.")
    if len(text) > max_length:
        raise DataDeletionDryRunError(f"{label} must be {max_length} characters or fewer.")
    return text


def _optional_text(value: Any, label: str, max_length: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > max_length:
        raise DataDeletionDryRunError(f"{label} must be {max_length} characters or fewer.")
    return text


def _positive_id(value: Any, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise DataDeletionDryRunError(f"{label} must be a positive integer.") from exc
    if parsed <= 0:
        raise DataDeletionDryRunError(f"{label} must be a positive integer.")
    return parsed


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError) as exc:
        raise DataDeletionDryRunError(f"invalid integer value: {value!r}.") from exc


def _datetime_value(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise DataDeletionDryRunError(f"invalid datetime value: {value}.") from exc
    raise DataDeletionDryRunError(f"invalid datetime value: {value!r}.")


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
