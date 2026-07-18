from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping
import base64
import binascii
import hashlib
import hmac
import json
import math
import os
import re
import shutil
import tempfile
import uuid
import zipfile

from pubg_ai.data_deletion_backup import (
    BACKUP_EVIDENCE_CONTRACT_VERSION,
    DataDeletionBackupService,
    fingerprint_backup_evidence,
    normalize_evidence_payload,
)
from pubg_ai.data_deletion_backup_builder import (
    MYSQL_BACKUP_FORMAT_VERSION,
    REPLAY_BACKUP_FORMAT_VERSION,
)
from pubg_ai.data_deletion_backup_verifier import (
    BACKUP_VERIFIER_CONTRACT_VERSION,
    DataDeletionBackupVerificationRun,
    DataDeletionBackupVerifierError,
    DataDeletionBackupVerifierService,
    RevalidatedBackupBuild,
)
from pubg_ai.data_deletion_dry_run import DataDeletionDryRunPlan
from pubg_ai.data_deletion_requests import DataDeletionRequest
from pubg_ai.time_utils import now_kst, to_kst


RESTORE_REHEARSAL_CONTRACT_VERSION = "deletion-backup-restore-rehearsal-v1"
RESTORE_REHEARSAL_CONFIRMATION_PREFIX = "RUN ISOLATED RESTORE REHEARSAL"

_MYSQL_ARTIFACT_KEY = "mysql_target_backup"
_REPLAY_ARTIFACT_KEY = "replay_artifact_backup"
_INTEGRITY_EVIDENCE_KEY = "backup_integrity_verification"
_TEMP_TABLE_PREFIX = "_pubg_ai_rr_"
_TEMP_DIRECTORY_PREFIX = ".pubg-ai-restore-rehearsal-"
_COPY_CHUNK_BYTES = 1024 * 1024
_MAX_INTERNAL_MANIFEST_BYTES = 8 * 1024 * 1024
_MAX_JSONL_LINE_BYTES = 64 * 1024 * 1024
_MAX_ZIP_ENTRY_BYTES = 128 * 1024 * 1024 * 1024
_MAX_ZIP_TOTAL_BYTES = 256 * 1024 * 1024 * 1024
_MAX_COMPRESSION_RATIO = 2000
_MAX_TYPED_VALUE_DEPTH = 100
_ALLOWED_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_$]+$")
_TEMP_TABLE_PATTERN = re.compile(r"^_pubg_ai_rr_[0-9]+_[0-9]{3}_[0-9a-f]{10}$")
_MYSQL_MANIFEST_KEYS = {
    "format_version",
    "builder_contract_version",
    "request_id",
    "dry_run_plan_id",
    "plan_fingerprint_sha256",
    "source_fingerprint_sha256",
    "built_at_kst",
    "row_count",
    "tables",
    "schema_creation_included",
    "restore_supported_by_current_application",
}
_MYSQL_TABLE_KEYS = {
    "sequence",
    "table",
    "entry",
    "row_count",
    "content_bytes",
    "content_sha256",
    "selector",
}
_REPLAY_MANIFEST_KEYS = {
    "format_version",
    "builder_contract_version",
    "dry_run_plan_id",
    "plan_fingerprint_sha256",
    "source_fingerprint_sha256",
    "built_at_kst",
    "file_count",
    "source_file_bytes",
    "files",
    "restore_supported_by_current_application",
}
_REPLAY_FILE_KEYS = {
    "sequence",
    "record_id",
    "artifact_type",
    "match_id",
    "source_relative_path",
    "entry",
    "size_bytes",
    "sha256",
}


class DataDeletionRestoreRehearsalError(RuntimeError):
    """Raised when an isolated backup restore rehearsal cannot run safely."""


class _RestoreBlocked(RuntimeError):
    pass


class _DuplicateJsonKey(ValueError):
    pass


@dataclass(frozen=True)
class DataDeletionBackupRestoreRehearsalRun:
    id: int
    request_id: int
    dry_run_plan_id: int
    backup_verification_run_id: int
    contract_version: str
    plan_fingerprint_sha256: str
    artifact_evidence_set_fingerprint_sha256: str
    artifact_evidence_record_ids: dict[str, int]
    build_id: str
    manifest_path: str
    manifest_sha256: str
    backup_verification_result_fingerprint_sha256: str
    current_revalidation_result_fingerprint_sha256: str | None
    result_fingerprint_sha256: str
    result_status: str
    result_json: dict[str, Any]
    mysql_table_count: int
    mysql_restored_table_count: int
    mysql_row_count: int
    mysql_restored_row_count: int
    replay_file_count: int
    replay_restored_file_count: int
    replay_source_bytes: int
    replay_restored_bytes: int
    check_count: int
    passed_check_count: int
    blocker_count: int
    backup_integrity_evidence_id: int | None
    run_by: str
    rehearsal_note: str | None
    run_at_kst: datetime

    def to_summary_record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "request_id": self.request_id,
            "dry_run_plan_id": self.dry_run_plan_id,
            "backup_verification_run_id": self.backup_verification_run_id,
            "contract_version": self.contract_version,
            "plan_fingerprint_sha256": self.plan_fingerprint_sha256,
            "artifact_evidence_set_fingerprint_sha256": (
                self.artifact_evidence_set_fingerprint_sha256
            ),
            "artifact_evidence_record_ids": dict(self.artifact_evidence_record_ids),
            "build_id": self.build_id,
            "manifest_path": self.manifest_path,
            "manifest_sha256": self.manifest_sha256,
            "backup_verification_result_fingerprint_sha256": (
                self.backup_verification_result_fingerprint_sha256
            ),
            "current_revalidation_result_fingerprint_sha256": (
                self.current_revalidation_result_fingerprint_sha256
            ),
            "result_fingerprint_sha256": self.result_fingerprint_sha256,
            "result_status": self.result_status,
            "mysql_table_count": self.mysql_table_count,
            "mysql_restored_table_count": self.mysql_restored_table_count,
            "mysql_row_count": self.mysql_row_count,
            "mysql_restored_row_count": self.mysql_restored_row_count,
            "replay_file_count": self.replay_file_count,
            "replay_restored_file_count": self.replay_restored_file_count,
            "replay_source_bytes": self.replay_source_bytes,
            "replay_restored_bytes": self.replay_restored_bytes,
            "check_count": self.check_count,
            "passed_check_count": self.passed_check_count,
            "blocker_count": self.blocker_count,
            "backup_integrity_evidence_id": self.backup_integrity_evidence_id,
            "run_by": self.run_by,
            "rehearsal_note": self.rehearsal_note,
            "run_at_kst": to_kst(self.run_at_kst).isoformat(),
            "immutable": True,
            "production_restore_performed": False,
            "quarantine_performed": False,
            "deletion_performed": False,
            "execution_enabled": False,
            "execution_ready": False,
        }

    def to_record(self) -> dict[str, Any]:
        return {**self.to_summary_record(), "result_json": self.result_json}


class DataDeletionBackupRestoreRehearsalService:
    def __init__(
        self,
        connection: Any,
        *,
        backup_service: DataDeletionBackupService,
        verifier_service: DataDeletionBackupVerifierService,
        scratch_connection_factory: Callable[[], Any],
        backup_root: Path,
        expected_database_name: str,
    ) -> None:
        self.connection = connection
        self.backup_service = backup_service
        self.verifier_service = verifier_service
        self.scratch_connection_factory = scratch_connection_factory
        self.backup_root = backup_root.expanduser().resolve(strict=False)
        self.expected_database_name = _identifier(
            expected_database_name,
            "expected_database_name",
        )

    def rehearsal_state(self, request: DataDeletionRequest) -> dict[str, Any]:
        plans = self.backup_service.dry_run_service.list_plans(request.id, limit=1)
        plan = plans[0] if plans else None
        blockers: list[str] = []
        if request.status != "approved":
            blockers.append(f"request status must be approved, not {request.status}")
        if plan is None:
            blockers.append("latest confirmed dry-run plan is required")
            verifications: list[DataDeletionBackupVerificationRun] = []
            history: list[DataDeletionBackupRestoreRehearsalRun] = []
        else:
            verifications = self.verifier_service.list_runs(plan.id, limit=50)
            history = self.list_runs(plan.id, limit=50)
        passed = [item for item in verifications if item.result_status == "passed"]
        if plan is not None and not passed:
            blockers.append("a passed read-only backup verification is required")
        if not self.backup_root.is_dir():
            blockers.append("configured backup root must exist and be a directory")
        candidates = [
            {
                **item.to_summary_record(),
                "confirmation_text": expected_restore_rehearsal_confirmation(
                    request.id,
                    item.id,
                    item.result_fingerprint_sha256,
                ),
            }
            for item in passed
        ]
        return {
            "request_id": request.id,
            "request_status": request.status,
            "contract_version": RESTORE_REHEARSAL_CONTRACT_VERSION,
            "latest_plan_id": plan.id if plan is not None else None,
            "plan_fingerprint_sha256": (
                plan.plan_fingerprint_sha256 if plan is not None else None
            ),
            "backup_root": str(self.backup_root),
            "expected_database_name": self.expected_database_name,
            "verification_candidates": candidates,
            "latest_restore_rehearsal": history[0].to_record() if history else None,
            "restore_rehearsal_history": [item.to_summary_record() for item in history],
            "restore_rehearsal_allowed": not blockers,
            "restore_rehearsal_blockers": blockers,
            "mysql_scratch_strategy": "dedicated_connection_temporary_tables",
            "replay_scratch_strategy": "temporary_directory_under_backup_root",
            "revalidates_backup_contents": True,
            "writes_temporary_mysql_rows": True,
            "writes_temporary_replay_files": True,
            "temporary_resources_removed": True,
            "appends_restore_rehearsal_audit_row": True,
            "appends_integrity_evidence_only_on_pass": True,
            "production_restore_performed": False,
            "quarantine_performed": False,
            "deletion_performed": False,
            "execution_enabled": False,
            "execution_ready": False,
        }

    def run(
        self,
        request: DataDeletionRequest,
        *,
        backup_verification_run_id: int,
        confirmation_text: str,
        actor_id: str,
        note: str | None = None,
        reference_kst: datetime | None = None,
    ) -> DataDeletionBackupRestoreRehearsalRun:
        verification_id = _positive_int(
            backup_verification_run_id,
            "backup_verification_run_id",
        )
        actor_id = _required_text(actor_id, "actor_id", 191)
        note = _optional_text(note, "note", 1000)
        if request.status != "approved":
            raise DataDeletionRestoreRehearsalError("request must remain approved.")
        verification = self.verifier_service.get_run(verification_id)
        if verification.request_id != request.id:
            raise DataDeletionRestoreRehearsalError(
                "backup verification belongs to another deletion request."
            )
        if verification.contract_version != BACKUP_VERIFIER_CONTRACT_VERSION:
            raise DataDeletionRestoreRehearsalError(
                "backup verification contract version is unsupported."
            )
        if verification.result_status != "passed":
            raise DataDeletionRestoreRehearsalError(
                "backup restore rehearsal requires a passed artifact verification."
            )
        plan = self.backup_service.require_latest_plan(
            request,
            verification.dry_run_plan_id,
        )
        if not hmac.compare_digest(
            verification.plan_fingerprint_sha256,
            plan.plan_fingerprint_sha256,
        ):
            raise DataDeletionRestoreRehearsalError(
                "backup verification plan fingerprint is stale."
            )
        expected_confirmation = expected_restore_rehearsal_confirmation(
            request.id,
            verification.id,
            verification.result_fingerprint_sha256,
        )
        supplied_confirmation = _required_text(
            confirmation_text,
            "confirmation_text",
            500,
        )
        if not hmac.compare_digest(supplied_confirmation, expected_confirmation):
            raise DataDeletionRestoreRehearsalError(
                "restore rehearsal confirmation text does not match the selected verification."
            )
        if not self.backup_root.is_dir():
            raise DataDeletionRestoreRehearsalError(
                "configured backup root must exist and be a directory."
            )

        run_at = to_kst(reference_kst or now_kst())
        checks: list[dict[str, Any]] = []
        metrics = _empty_metrics()
        current_revalidation_fingerprint: str | None = None
        revalidated: RevalidatedBackupBuild | None = None
        try:
            revalidated = self.verifier_service.revalidate_passed_run(
                request,
                verification.id,
                reference_kst=run_at,
            )
            current_revalidation_fingerprint = (
                revalidated.current_result_fingerprint_sha256
            )
            checks.append(
                _check(
                    "backup_revalidation",
                    True,
                    "selected passed verification still matches every backup byte",
                    current_revalidation_fingerprint,
                    "backup artifacts were revalidated immediately before scratch restore",
                )
            )
            runner = _IsolatedRestoreRunner(
                audit_connection=self.connection,
                scratch_connection_factory=self.scratch_connection_factory,
                backup_root=self.backup_root,
                expected_database_name=self.expected_database_name,
                revalidated=revalidated,
            )
            scratch_result = runner.run()
            checks.extend(scratch_result["checks"])
            metrics = dict(scratch_result["metrics"])
        except Exception as exc:
            message = _safe_error_message(exc)
            checks.append(
                _check(
                    "backup_revalidation",
                    False,
                    "selected passed verification still matches every backup byte",
                    None,
                    message,
                )
            )

        blockers = [
            str(item["key"])
            for item in checks
            if item.get("status") == "blocked"
        ]
        status = "passed" if not blockers else "blocked"
        result = {
            "contract_version": RESTORE_REHEARSAL_CONTRACT_VERSION,
            "request_id": request.id,
            "dry_run_plan_id": plan.id,
            "plan_fingerprint_sha256": plan.plan_fingerprint_sha256,
            "backup_verification": {
                "id": verification.id,
                "result_fingerprint_sha256": (
                    verification.result_fingerprint_sha256
                ),
                "current_revalidation_result_fingerprint_sha256": (
                    current_revalidation_fingerprint
                ),
                "artifact_evidence_set_fingerprint_sha256": (
                    verification.evidence_set_fingerprint_sha256
                ),
                "artifact_evidence_record_ids": dict(
                    verification.evidence_record_ids
                ),
                "build_id": verification.build_id,
                "manifest_path": verification.manifest_path,
                "manifest_sha256": verification.expected_manifest_sha256,
            },
            "checks": checks,
            "metrics": metrics,
            "restore_rehearsal_blockers": blockers,
            "restore_rehearsal_status": status,
            "run_at_kst": run_at.isoformat(),
            "safety": {
                "backup_files_opened_read_only": True,
                "checksums_recalculated": revalidated is not None,
                "temporary_mysql_tables_used": bool(
                    metrics["mysql_table_count"]
                ),
                "temporary_replay_files_used": bool(
                    metrics["replay_file_count"]
                ),
                "temporary_resources_removed": _cleanup_passed(checks),
                "isolated_restore_test_performed": revalidated is not None,
                "backup_integrity_prerequisite_attested": status == "passed",
                "production_database_rows_modified": False,
                "production_files_modified": False,
                "production_restore_performed": False,
                "quarantine_performed": False,
                "deletion_performed": False,
                "execution_enabled": False,
                "execution_ready": False,
                "execution_blockers": ["executor_not_implemented"],
            },
        }
        result_fingerprint = _canonical_sha256(result)
        return self._record_run(
            request,
            plan,
            verification,
            result=result,
            result_fingerprint=result_fingerprint,
            current_revalidation_fingerprint=current_revalidation_fingerprint,
            actor_id=actor_id,
            note=note,
            run_at_kst=run_at,
        )

    def get_run(self, rehearsal_id: int) -> DataDeletionBackupRestoreRehearsalRun:
        rehearsal_id = _positive_int(rehearsal_id, "rehearsal_id")
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM data_deletion_backup_restore_rehearsal_runs WHERE id = %s",
                (rehearsal_id,),
            )
            row = cursor.fetchone()
        if not row:
            raise DataDeletionRestoreRehearsalError(
                f"backup restore rehearsal {rehearsal_id} was not found."
            )
        return _restore_rehearsal_from_row(row)

    def list_runs(
        self,
        dry_run_plan_id: int,
        *,
        limit: int = 50,
    ) -> list[DataDeletionBackupRestoreRehearsalRun]:
        dry_run_plan_id = _positive_int(dry_run_plan_id, "dry_run_plan_id")
        if not 1 <= int(limit) <= 100:
            raise DataDeletionRestoreRehearsalError(
                "restore rehearsal history limit must be between 1 and 100."
            )
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM data_deletion_backup_restore_rehearsal_runs
                WHERE dry_run_plan_id = %s
                ORDER BY run_at_kst DESC, id DESC
                LIMIT %s
                """,
                (dry_run_plan_id, int(limit)),
            )
            rows = cursor.fetchall()
        return [_restore_rehearsal_from_row(row) for row in rows]

    def _record_run(
        self,
        request: DataDeletionRequest,
        plan: DataDeletionDryRunPlan,
        verification: DataDeletionBackupVerificationRun,
        *,
        result: dict[str, Any],
        result_fingerprint: str,
        current_revalidation_fingerprint: str | None,
        actor_id: str,
        note: str | None,
        run_at_kst: datetime,
    ) -> DataDeletionBackupRestoreRehearsalRun:
        checks = list(result["checks"])
        metrics = dict(result["metrics"])
        blockers = list(result["restore_rehearsal_blockers"])
        status = str(result["restore_rehearsal_status"])
        timestamp = to_kst(run_at_kst).replace(tzinfo=None)
        integrity_payload: dict[str, Any] | None = None
        integrity_fingerprint: str | None = None
        if status == "passed":
            integrity_payload = normalize_evidence_payload(
                _INTEGRITY_EVIDENCE_KEY,
                {
                    "checksums_verified": True,
                    "restore_test_passed": True,
                    "restore_tested_at_kst": run_at_kst,
                    "verified_at_kst": run_at_kst,
                    "artifact_evidence_set_fingerprint_sha256": (
                        verification.evidence_set_fingerprint_sha256
                    ),
                    "backup_verification_run_id": verification.id,
                    "backup_verification_result_fingerprint_sha256": (
                        verification.result_fingerprint_sha256
                    ),
                    "restore_rehearsal_result_fingerprint_sha256": (
                        result_fingerprint
                    ),
                    "build_id": verification.build_id,
                    "manifest_sha256": verification.expected_manifest_sha256,
                },
            )
            integrity_fingerprint = fingerprint_backup_evidence(
                request.id,
                plan,
                _INTEGRITY_EVIDENCE_KEY,
                integrity_payload,
            )

        _begin(self.connection)
        integrity_evidence_id: int | None = None
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT status FROM data_deletion_requests WHERE id = %s FOR UPDATE",
                    (request.id,),
                )
                request_row = cursor.fetchone()
                if not request_row or str(request_row.get("status")) != "approved":
                    raise DataDeletionRestoreRehearsalError(
                        "request is no longer approved."
                    )
                cursor.execute(
                    """
                    SELECT id, plan_fingerprint_sha256
                    FROM data_deletion_dry_run_plans
                    WHERE request_id = %s
                    ORDER BY generated_at_kst DESC, id DESC
                    LIMIT 1
                    FOR UPDATE
                    """,
                    (request.id,),
                )
                plan_row = cursor.fetchone()
                if (
                    not plan_row
                    or int(plan_row["id"]) != plan.id
                    or not hmac.compare_digest(
                        str(plan_row["plan_fingerprint_sha256"]),
                        plan.plan_fingerprint_sha256,
                    )
                ):
                    raise DataDeletionRestoreRehearsalError(
                        "latest dry-run plan changed during restore rehearsal."
                    )
                cursor.execute(
                    """
                    SELECT id, request_id, dry_run_plan_id, contract_version,
                           plan_fingerprint_sha256,
                           evidence_set_fingerprint_sha256,
                           evidence_record_ids_json,
                           build_id,
                           expected_manifest_sha256,
                           result_fingerprint_sha256,
                           result_status
                    FROM data_deletion_backup_verification_runs
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (verification.id,),
                )
                locked_verification = cursor.fetchone()
                _assert_locked_verification(
                    locked_verification,
                    request,
                    plan,
                    verification,
                )
                evidence_ids = sorted(verification.evidence_record_ids.values())
                placeholders = ", ".join(["%s"] * len(evidence_ids))
                cursor.execute(
                    f"""
                    SELECT id, prerequisite_key, evidence_fingerprint_sha256
                    FROM data_deletion_backup_evidence
                    WHERE dry_run_plan_id = %s AND id IN ({placeholders})
                    FOR UPDATE
                    """,
                    (plan.id, *evidence_ids),
                )
                locked_evidence = cursor.fetchall()
                _assert_locked_artifact_evidence(
                    plan,
                    verification,
                    locked_evidence,
                )
                if integrity_payload is not None and integrity_fingerprint is not None:
                    evidence_note = (
                        f"restore_rehearsal={RESTORE_REHEARSAL_CONTRACT_VERSION}; "
                        f"verification_run_id={verification.id}; "
                        f"result_sha256={result_fingerprint}"
                    )
                    cursor.execute(
                        """
                        INSERT INTO data_deletion_backup_evidence (
                            request_id,
                            dry_run_plan_id,
                            contract_version,
                            plan_fingerprint_sha256,
                            prerequisite_key,
                            evidence_fingerprint_sha256,
                            evidence_json,
                            recorded_by,
                            evidence_note,
                            recorded_at_kst
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            request.id,
                            plan.id,
                            BACKUP_EVIDENCE_CONTRACT_VERSION,
                            plan.plan_fingerprint_sha256,
                            _INTEGRITY_EVIDENCE_KEY,
                            integrity_fingerprint,
                            _json_dump(integrity_payload),
                            actor_id,
                            evidence_note,
                            timestamp,
                        ),
                    )
                    integrity_evidence_id = int(cursor.lastrowid)
                cursor.execute(
                    """
                    INSERT INTO data_deletion_backup_restore_rehearsal_runs (
                        request_id,
                        dry_run_plan_id,
                        backup_verification_run_id,
                        contract_version,
                        plan_fingerprint_sha256,
                        artifact_evidence_set_fingerprint_sha256,
                        artifact_evidence_record_ids_json,
                        build_id,
                        manifest_path,
                        manifest_sha256,
                        backup_verification_result_fingerprint_sha256,
                        current_revalidation_result_fingerprint_sha256,
                        result_fingerprint_sha256,
                        result_status,
                        result_json,
                        mysql_table_count,
                        mysql_restored_table_count,
                        mysql_row_count,
                        mysql_restored_row_count,
                        replay_file_count,
                        replay_restored_file_count,
                        replay_source_bytes,
                        replay_restored_bytes,
                        check_count,
                        passed_check_count,
                        blocker_count,
                        backup_integrity_evidence_id,
                        run_by,
                        rehearsal_note,
                        run_at_kst
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        request.id,
                        plan.id,
                        verification.id,
                        RESTORE_REHEARSAL_CONTRACT_VERSION,
                        plan.plan_fingerprint_sha256,
                        verification.evidence_set_fingerprint_sha256,
                        _json_dump(verification.evidence_record_ids),
                        verification.build_id,
                        verification.manifest_path,
                        verification.expected_manifest_sha256,
                        verification.result_fingerprint_sha256,
                        current_revalidation_fingerprint,
                        result_fingerprint,
                        status,
                        _json_dump(result),
                        metrics["mysql_table_count"],
                        metrics["mysql_restored_table_count"],
                        metrics["mysql_row_count"],
                        metrics["mysql_restored_row_count"],
                        metrics["replay_file_count"],
                        metrics["replay_restored_file_count"],
                        metrics["replay_source_bytes"],
                        metrics["replay_restored_bytes"],
                        len(checks),
                        sum(item.get("status") == "passed" for item in checks),
                        len(blockers),
                        integrity_evidence_id,
                        actor_id,
                        note,
                        timestamp,
                    ),
                )
                rehearsal_id = int(cursor.lastrowid)
            _commit(self.connection)
        except Exception:
            _rollback(self.connection)
            raise
        return DataDeletionBackupRestoreRehearsalRun(
            id=rehearsal_id,
            request_id=request.id,
            dry_run_plan_id=plan.id,
            backup_verification_run_id=verification.id,
            contract_version=RESTORE_REHEARSAL_CONTRACT_VERSION,
            plan_fingerprint_sha256=plan.plan_fingerprint_sha256,
            artifact_evidence_set_fingerprint_sha256=(
                verification.evidence_set_fingerprint_sha256
            ),
            artifact_evidence_record_ids=dict(verification.evidence_record_ids),
            build_id=_required_text(verification.build_id, "build_id", 64),
            manifest_path=verification.manifest_path,
            manifest_sha256=verification.expected_manifest_sha256,
            backup_verification_result_fingerprint_sha256=(
                verification.result_fingerprint_sha256
            ),
            current_revalidation_result_fingerprint_sha256=(
                current_revalidation_fingerprint
            ),
            result_fingerprint_sha256=result_fingerprint,
            result_status=status,
            result_json=result,
            mysql_table_count=metrics["mysql_table_count"],
            mysql_restored_table_count=metrics["mysql_restored_table_count"],
            mysql_row_count=metrics["mysql_row_count"],
            mysql_restored_row_count=metrics["mysql_restored_row_count"],
            replay_file_count=metrics["replay_file_count"],
            replay_restored_file_count=metrics["replay_restored_file_count"],
            replay_source_bytes=metrics["replay_source_bytes"],
            replay_restored_bytes=metrics["replay_restored_bytes"],
            check_count=len(checks),
            passed_check_count=sum(
                item.get("status") == "passed" for item in checks
            ),
            blocker_count=len(blockers),
            backup_integrity_evidence_id=integrity_evidence_id,
            run_by=actor_id,
            rehearsal_note=note,
            run_at_kst=run_at_kst,
        )


class _IsolatedRestoreRunner:
    def __init__(
        self,
        *,
        audit_connection: Any,
        scratch_connection_factory: Callable[[], Any],
        backup_root: Path,
        expected_database_name: str,
        revalidated: RevalidatedBackupBuild,
    ) -> None:
        self.audit_connection = audit_connection
        self.scratch_connection_factory = scratch_connection_factory
        self.backup_root = backup_root
        self.expected_database_name = expected_database_name
        self.revalidated = revalidated
        self.checks: list[dict[str, Any]] = []
        self.metrics = _empty_metrics()
        self._scratch_connection: Any | None = None
        self._temp_tables: list[str] = []
        self._scratch_directory: Path | None = None
        self._token = uuid.uuid4().hex[:10]

    def run(self) -> dict[str, Any]:
        try:
            audit_identity = _database_identity(self.audit_connection)
            self._scratch_connection = self.scratch_connection_factory()
            scratch_identity = _database_identity(self._scratch_connection)
            isolation_passed = (
                str(audit_identity["database_name"]) == self.expected_database_name
                and str(scratch_identity["database_name"])
                == self.expected_database_name
                and int(audit_identity["connection_id"])
                != int(scratch_identity["connection_id"])
            )
            self._require(
                "scratch_mysql_connection_isolation",
                isolation_passed,
                "a dedicated connection to the configured database",
                {
                    "database_name": scratch_identity["database_name"],
                    "dedicated_connection": (
                        int(audit_identity["connection_id"])
                        != int(scratch_identity["connection_id"])
                    ),
                },
                "scratch MySQL connection is isolated from the audit connection",
            )
            artifacts = _artifact_records(self.revalidated)
            if _MYSQL_ARTIFACT_KEY in artifacts:
                self._restore_mysql(artifacts[_MYSQL_ARTIFACT_KEY])
            else:
                self.checks.append(
                    _not_required(
                        "mysql_restore_round_trip",
                        "latest plan has no MySQL backup prerequisite",
                    )
                )
            if _REPLAY_ARTIFACT_KEY in artifacts:
                self._restore_replay(artifacts[_REPLAY_ARTIFACT_KEY])
            else:
                self.checks.append(
                    _not_required(
                        "replay_restore_round_trip",
                        "latest plan has no replay backup prerequisite",
                    )
                )
            self.checks.append(
                _check(
                    "production_mutation_guard",
                    True,
                    "all writes target generated temporary tables or a generated scratch directory",
                    {
                        "temporary_table_count": len(self._temp_tables),
                        "scratch_directory_created": self._scratch_directory is not None,
                    },
                    "no production table or source file write target was used",
                )
            )
        except _RestoreBlocked as exc:
            if not any(item.get("status") == "blocked" for item in self.checks):
                self.checks.append(
                    _check(
                        "isolated_restore_operation",
                        False,
                        "all scratch restore round trips match their backup sources",
                        None,
                        _safe_error_message(exc),
                    )
                )
        except Exception as exc:
            self.checks.append(
                _check(
                    "isolated_restore_operation",
                    False,
                    "scratch restore completes without an unexpected error",
                    None,
                    _safe_error_message(exc),
                )
            )
        finally:
            self._cleanup()
        return {"checks": self.checks, "metrics": self.metrics}

    def _restore_mysql(self, artifact: dict[str, Any]) -> None:
        path = self.revalidated.artifact_paths[_MYSQL_ARTIFACT_KEY]
        expected_sha256 = _fingerprint(artifact.get("sha256"), "MySQL artifact SHA-256")
        expected_size = _nonnegative_int(artifact.get("size_bytes"), "MySQL artifact size")
        identity_before = _file_identity(path)
        self._require(
            "mysql_archive_pre_restore_identity",
            identity_before[0] == expected_size
            and hmac.compare_digest(_sha256_file(path), expected_sha256),
            "verified MySQL archive size and SHA-256",
            {"size_bytes": identity_before[0], "sha256": expected_sha256},
            "MySQL archive still matches the selected build",
        )
        table_results: list[dict[str, Any]] = []
        with path.open("rb") as source:
            with zipfile.ZipFile(source, mode="r") as archive:
                infos = _zip_catalog(archive)
                manifest = _read_zip_json_object(
                    archive,
                    infos["manifest.json"],
                    "MySQL manifest",
                )
                _exact_keys(manifest, _MYSQL_MANIFEST_KEYS, "MySQL manifest")
                if manifest.get("format_version") != MYSQL_BACKUP_FORMAT_VERSION:
                    raise _RestoreBlocked("MySQL backup format version is unsupported")
                records = manifest.get("tables")
                if not isinstance(records, list):
                    raise _RestoreBlocked("MySQL manifest tables must be a list")
                expected_tables = _planned_database_tables(self.revalidated.plan)
                observed_tables = [
                    str(item.get("table") or "")
                    for item in records
                    if isinstance(item, dict)
                ]
                if len(observed_tables) != len(records) or observed_tables != expected_tables:
                    raise _RestoreBlocked("MySQL manifest tables differ from the dry-run plan")
                declared_entries = {"manifest.json"}
                total_rows = 0
                for record in records:
                    _exact_keys(record, _MYSQL_TABLE_KEYS, "MySQL table record")
                    entry = str(record["entry"])
                    declared_entries.add(entry)
                    table_result = self._restore_mysql_table(
                        archive,
                        infos,
                        record,
                    )
                    table_results.append(table_result)
                    total_rows += int(table_result["row_count"])
                if set(infos) != declared_entries:
                    raise _RestoreBlocked("MySQL archive contains undeclared entries")
                if total_rows != _nonnegative_int(manifest.get("row_count"), "row_count"):
                    raise _RestoreBlocked("MySQL restored row total differs from its manifest")
        identity_after = _file_identity(path)
        self._require(
            "mysql_archive_post_restore_identity",
            identity_after == identity_before
            and hmac.compare_digest(_sha256_file(path), expected_sha256),
            "unchanged MySQL archive identity and SHA-256 after restore",
            {"identity_stable": identity_after == identity_before},
            "MySQL archive remained unchanged throughout the restore rehearsal",
        )
        self.metrics["mysql_table_count"] = len(table_results)
        self.metrics["mysql_restored_table_count"] = len(table_results)
        self.metrics["mysql_row_count"] = total_rows
        self.metrics["mysql_restored_row_count"] = total_rows
        self.checks.append(
            _check(
                "mysql_restore_round_trip",
                True,
                "every declared row round-trips through isolated temporary tables",
                {"tables": table_results, "row_count": total_rows},
                "all MySQL rows matched after temporary-table restore and readback",
            )
        )

    def _restore_mysql_table(
        self,
        archive: zipfile.ZipFile,
        infos: dict[str, zipfile.ZipInfo],
        record: dict[str, Any],
    ) -> dict[str, Any]:
        if self._scratch_connection is None:
            raise _RestoreBlocked("scratch MySQL connection is unavailable")
        sequence = _positive_int(record.get("sequence"), "table sequence")
        table = _identifier(str(record.get("table") or ""), "table")
        entry = _safe_zip_entry_name(str(record.get("entry") or ""))
        info = infos.get(entry)
        if info is None:
            raise _RestoreBlocked(f"declared MySQL entry is missing: {entry}")
        temp_name = f"{_TEMP_TABLE_PREFIX}{self.revalidated.verification_run.id}_{sequence:03d}_{self._token}"
        if not _TEMP_TABLE_PATTERN.fullmatch(temp_name):
            raise _RestoreBlocked("generated temporary table name is invalid")
        with self._scratch_connection.cursor() as cursor:
            cursor.execute(f"SHOW FULL COLUMNS FROM `{table}`")
            column_rows = cursor.fetchall()
            if not column_rows:
                raise _RestoreBlocked(f"source table schema is unavailable: {table}")
            columns = [str(item.get("Field") or "") for item in column_rows]
            if any(not _IDENTIFIER_PATTERN.fullmatch(column) for column in columns):
                raise _RestoreBlocked(f"source table contains an unsafe column: {table}")
            if len(columns) != len(set(columns)):
                raise _RestoreBlocked(f"source table contains duplicate columns: {table}")
            generated = [
                column
                for column, item in zip(columns, column_rows, strict=True)
                if "GENERATED" in str(item.get("Extra") or "").upper()
            ]
            if generated:
                raise _RestoreBlocked(
                    f"generated columns are unsupported in restore rehearsal: {table}"
                )
            column_types = {
                column: str(item.get("Type") or "").lower()
                for column, item in zip(columns, column_rows, strict=True)
            }
            cursor.execute(f"SHOW CREATE TABLE `{table}`")
            source_create = _show_create_sql(cursor.fetchone())
            cursor.execute(f"CREATE TEMPORARY TABLE `{temp_name}` LIKE `{table}`")
            self._temp_tables.append(temp_name)
            cursor.execute(f"SHOW CREATE TABLE `{temp_name}`")
            temporary_create = _show_create_sql(cursor.fetchone())
            if "FOREIGN KEY" in temporary_create.upper():
                raise _RestoreBlocked(
                    f"temporary table unexpectedly contains a foreign key: {table}"
                )

        expected_row_count = _nonnegative_int(record.get("row_count"), "row_count")
        expected_content_bytes = _nonnegative_int(
            record.get("content_bytes"),
            "content_bytes",
        )
        expected_content_sha256 = _fingerprint(
            record.get("content_sha256"),
            "content_sha256",
        )
        source_hashes: list[str] = []
        content_digest = hashlib.sha256()
        content_bytes = 0
        row_count = 0
        column_sql = ", ".join(f"`{column}`" for column in columns)
        placeholders = ", ".join(["%s"] * len(columns))
        insert_sql = (
            f"INSERT INTO `{temp_name}` ({column_sql}) VALUES ({placeholders})"
        )
        with archive.open(info, mode="r") as row_stream:
            while True:
                line = row_stream.readline(_MAX_JSONL_LINE_BYTES + 2)
                if not line:
                    break
                if len(line) > _MAX_JSONL_LINE_BYTES + 1 or not line.endswith(b"\n"):
                    raise _RestoreBlocked(f"JSONL row framing is invalid: {entry}")
                content_digest.update(line)
                content_bytes += len(line)
                row = _json_object_bytes(line[:-1], f"JSONL row in {entry}")
                if list(row) != columns:
                    raise _RestoreBlocked(
                        f"backup row columns differ from current source schema: {table}"
                    )
                decoded = {
                    column: _decode_typed_json_value(row[column])
                    for column in columns
                }
                source_hashes.append(
                    _row_fingerprint(decoded, column_types)
                )
                values = tuple(
                    _mysql_insert_value(decoded[column], column_types[column])
                    for column in columns
                )
                with self._scratch_connection.cursor() as cursor:
                    cursor.execute(insert_sql, values)
                row_count += 1
                if row_count > expected_row_count:
                    raise _RestoreBlocked(
                        f"MySQL entry contains more rows than declared: {table}"
                    )
        if (
            row_count != expected_row_count
            or content_bytes != expected_content_bytes
            or not hmac.compare_digest(
                content_digest.hexdigest(),
                expected_content_sha256,
            )
        ):
            raise _RestoreBlocked(f"MySQL entry content differs from its manifest: {table}")

        restored_hashes: list[str] = []
        with self._scratch_connection.cursor() as cursor:
            cursor.execute(f"SELECT {column_sql} FROM `{temp_name}`")
            while rows := cursor.fetchmany(500):
                for restored in rows:
                    if not isinstance(restored, Mapping):
                        raise _RestoreBlocked(
                            f"temporary table returned a non-object row: {table}"
                        )
                    restored_hashes.append(
                        _row_fingerprint(dict(restored), column_types)
                    )
        source_set_fingerprint = _row_set_fingerprint(source_hashes)
        restored_set_fingerprint = _row_set_fingerprint(restored_hashes)
        if (
            len(restored_hashes) != row_count
            or not hmac.compare_digest(
                source_set_fingerprint,
                restored_set_fingerprint,
            )
        ):
            raise _RestoreBlocked(f"temporary MySQL row readback differs: {table}")
        return {
            "sequence": sequence,
            "table": table,
            "row_count": row_count,
            "schema_sha256": hashlib.sha256(
                source_create.encode("utf-8")
            ).hexdigest(),
            "source_row_set_sha256": source_set_fingerprint,
            "restored_row_set_sha256": restored_set_fingerprint,
            "foreign_keys_copied": False,
        }

    def _restore_replay(self, artifact: dict[str, Any]) -> None:
        path = self.revalidated.artifact_paths[_REPLAY_ARTIFACT_KEY]
        expected_sha256 = _fingerprint(artifact.get("sha256"), "replay artifact SHA-256")
        expected_size = _nonnegative_int(artifact.get("size_bytes"), "replay artifact size")
        identity_before = _file_identity(path)
        self._require(
            "replay_archive_pre_restore_identity",
            identity_before[0] == expected_size
            and hmac.compare_digest(_sha256_file(path), expected_sha256),
            "verified replay archive size and SHA-256",
            {"size_bytes": identity_before[0], "sha256": expected_sha256},
            "replay archive still matches the selected build",
        )
        self._scratch_directory = Path(
            tempfile.mkdtemp(
                prefix=_TEMP_DIRECTORY_PREFIX,
                dir=str(self.backup_root),
            )
        ).resolve(strict=True)
        _assert_scratch_directory(self.backup_root, self._scratch_directory)
        file_results: list[dict[str, Any]] = []
        total_bytes = 0
        with path.open("rb") as source:
            with zipfile.ZipFile(source, mode="r") as archive:
                infos = _zip_catalog(archive)
                manifest = _read_zip_json_object(
                    archive,
                    infos["manifest.json"],
                    "replay manifest",
                )
                _exact_keys(manifest, _REPLAY_MANIFEST_KEYS, "replay manifest")
                if manifest.get("format_version") != REPLAY_BACKUP_FORMAT_VERSION:
                    raise _RestoreBlocked("replay backup format version is unsupported")
                records = manifest.get("files")
                if not isinstance(records, list):
                    raise _RestoreBlocked("replay manifest files must be a list")
                required_bytes = _nonnegative_int(
                    manifest.get("source_file_bytes"),
                    "source_file_bytes",
                )
                free_bytes = shutil.disk_usage(self.backup_root).free
                self._require(
                    "replay_scratch_capacity",
                    free_bytes >= required_bytes,
                    f"at least {required_bytes} free bytes on the backup volume",
                    {"free_bytes": free_bytes, "required_bytes": required_bytes},
                    "backup volume has enough space for the temporary replay restore",
                )
                declared_entries = {"manifest.json"}
                for record in records:
                    _exact_keys(record, _REPLAY_FILE_KEYS, "replay file record")
                    entry = _safe_zip_entry_name(str(record.get("entry") or ""))
                    declared_entries.add(entry)
                    info = infos.get(entry)
                    if info is None:
                        raise _RestoreBlocked(f"declared replay entry is missing: {entry}")
                    relative_path = _safe_relative_path(
                        str(record.get("source_relative_path") or "")
                    )
                    target = (self._scratch_directory / Path(*relative_path.parts)).resolve(
                        strict=False
                    )
                    if not _is_within(target, self._scratch_directory):
                        raise _RestoreBlocked("replay scratch target escaped its directory")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    expected_file_size = _nonnegative_int(
                        record.get("size_bytes"),
                        "replay size_bytes",
                    )
                    expected_file_sha256 = _fingerprint(
                        record.get("sha256"),
                        "replay sha256",
                    )
                    digest = hashlib.sha256()
                    copied = 0
                    with archive.open(info, mode="r") as item_source, target.open("xb") as sink:
                        while chunk := item_source.read(_COPY_CHUNK_BYTES):
                            copied += len(chunk)
                            if copied > expected_file_size:
                                raise _RestoreBlocked(
                                    f"replay entry exceeds its declared size: {entry}"
                                )
                            sink.write(chunk)
                            digest.update(chunk)
                        sink.flush()
                        os.fsync(sink.fileno())
                    restored_sha256 = _sha256_file(target)
                    if (
                        copied != expected_file_size
                        or not hmac.compare_digest(
                            digest.hexdigest(),
                            expected_file_sha256,
                        )
                        or not hmac.compare_digest(
                            restored_sha256,
                            expected_file_sha256,
                        )
                    ):
                        raise _RestoreBlocked(
                            f"replay scratch readback differs from backup: {entry}"
                        )
                    total_bytes += copied
                    file_results.append(
                        {
                            "sequence": _positive_int(
                                record.get("sequence"),
                                "replay sequence",
                            ),
                            "record_id": _positive_int(
                                record.get("record_id"),
                                "replay record_id",
                            ),
                            "source_relative_path": relative_path.as_posix(),
                            "size_bytes": copied,
                            "sha256": restored_sha256,
                        }
                    )
                if set(infos) != declared_entries:
                    raise _RestoreBlocked("replay archive contains undeclared entries")
                if (
                    len(file_results)
                    != _nonnegative_int(manifest.get("file_count"), "file_count")
                    or total_bytes != required_bytes
                ):
                    raise _RestoreBlocked("replay restored totals differ from the manifest")
        identity_after = _file_identity(path)
        self._require(
            "replay_archive_post_restore_identity",
            identity_after == identity_before
            and hmac.compare_digest(_sha256_file(path), expected_sha256),
            "unchanged replay archive identity and SHA-256 after restore",
            {"identity_stable": identity_after == identity_before},
            "replay archive remained unchanged throughout the restore rehearsal",
        )
        self.metrics["replay_file_count"] = len(file_results)
        self.metrics["replay_restored_file_count"] = len(file_results)
        self.metrics["replay_source_bytes"] = total_bytes
        self.metrics["replay_restored_bytes"] = total_bytes
        self.checks.append(
            _check(
                "replay_restore_round_trip",
                True,
                "every declared replay file round-trips through scratch storage",
                {"files": file_results, "source_file_bytes": total_bytes},
                "all replay files matched after scratch restore and readback",
            )
        )

    def _require(
        self,
        key: str,
        passed: bool,
        expected: Any,
        observed: Any,
        message: str,
    ) -> None:
        self.checks.append(_check(key, passed, expected, observed, message))
        if not passed:
            raise _RestoreBlocked(message)

    def _cleanup(self) -> None:
        errors: list[str] = []
        if self._scratch_connection is not None:
            for table in reversed(self._temp_tables):
                try:
                    if not _TEMP_TABLE_PATTERN.fullmatch(table):
                        raise DataDeletionRestoreRehearsalError(
                            "refused to drop an invalid temporary table name"
                        )
                    with self._scratch_connection.cursor() as cursor:
                        cursor.execute(f"DROP TEMPORARY TABLE IF EXISTS `{table}`")
                except Exception as exc:
                    errors.append(_safe_error_message(exc))
            try:
                self._scratch_connection.close()
            except Exception as exc:
                errors.append(_safe_error_message(exc))
        if self._scratch_directory is not None:
            try:
                _assert_scratch_directory(self.backup_root, self._scratch_directory)
                shutil.rmtree(self._scratch_directory)
                if self._scratch_directory.exists():
                    raise OSError("scratch directory still exists after cleanup")
            except Exception as exc:
                errors.append(_safe_error_message(exc))
        self.checks.append(
            _check(
                "scratch_cleanup",
                not errors,
                "all connection-scoped tables and scratch files are removed",
                {"cleanup_errors": errors},
                (
                    "all temporary restore resources were removed"
                    if not errors
                    else "; ".join(errors)
                ),
            )
        )


def expected_restore_rehearsal_confirmation(
    request_id: int,
    verification_run_id: int,
    verification_result_fingerprint_sha256: str,
) -> str:
    return (
        f"{RESTORE_REHEARSAL_CONFIRMATION_PREFIX} REQUEST "
        f"{_positive_int(request_id, 'request_id')} VERIFICATION "
        f"{_positive_int(verification_run_id, 'verification_run_id')} "
        f"{_fingerprint(verification_result_fingerprint_sha256, 'verification fingerprint')}"
    )


def _artifact_records(revalidated: RevalidatedBackupBuild) -> dict[str, dict[str, Any]]:
    records = revalidated.manifest.get("artifacts")
    if not isinstance(records, list):
        raise _RestoreBlocked("build manifest artifacts are missing")
    result: dict[str, dict[str, Any]] = {}
    for item in records:
        if not isinstance(item, dict):
            raise _RestoreBlocked("build manifest contains an invalid artifact")
        key = str(item.get("prerequisite_key") or "")
        if key not in {_MYSQL_ARTIFACT_KEY, _REPLAY_ARTIFACT_KEY} or key in result:
            raise _RestoreBlocked("build manifest artifact key is invalid")
        if key not in revalidated.artifact_paths:
            raise _RestoreBlocked("revalidated artifact path is missing")
        result[key] = item
    if set(result) != set(revalidated.artifact_paths):
        raise _RestoreBlocked("revalidated artifact set differs from the build manifest")
    return result


def _planned_database_tables(plan: DataDeletionDryRunPlan) -> list[str]:
    records = plan.plan_json.get("database_operations")
    if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        raise _RestoreBlocked("dry-run database operations are invalid")
    ordered = sorted(records, key=lambda item: int(item.get("sequence") or 0))
    tables = [_identifier(str(item.get("table") or ""), "planned table") for item in ordered]
    if len(tables) != len(set(tables)):
        raise _RestoreBlocked("dry-run database operations contain duplicate tables")
    return tables


def _database_identity(connection: Any) -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT DATABASE() AS database_name, CONNECTION_ID() AS connection_id"
        )
        row = cursor.fetchone()
    if (
        not isinstance(row, Mapping)
        or not str(row.get("database_name") or "")
        or int(row.get("connection_id") or 0) <= 0
    ):
        raise _RestoreBlocked("MySQL connection identity is unavailable")
    return {
        "database_name": str(row["database_name"]),
        "connection_id": int(row["connection_id"]),
    }


def _show_create_sql(row: Any) -> str:
    if not isinstance(row, Mapping):
        raise _RestoreBlocked("SHOW CREATE TABLE returned an invalid result")
    values = [str(value) for key, value in row.items() if str(key).startswith("Create ")]
    if len(values) != 1 or not values[0]:
        raise _RestoreBlocked("SHOW CREATE TABLE SQL is unavailable")
    return values[0]


def _zip_catalog(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    if archive.comment:
        raise _RestoreBlocked("ZIP archive comments are not allowed")
    result: dict[str, zipfile.ZipInfo] = {}
    total_uncompressed = 0
    for info in archive.infolist():
        name = _safe_zip_entry_name(info.filename)
        if name in result:
            raise _RestoreBlocked(f"duplicate ZIP entry: {name}")
        if info.is_dir() or info.flag_bits & 0x1:
            raise _RestoreBlocked(f"unsupported ZIP entry: {name}")
        unix_mode = (info.external_attr >> 16) & 0o170000
        if unix_mode == 0o120000:
            raise _RestoreBlocked(f"symbolic-link ZIP entry is not allowed: {name}")
        if info.compress_type not in _ALLOWED_COMPRESSION:
            raise _RestoreBlocked(f"unsupported ZIP compression method: {name}")
        if info.file_size < 0 or info.file_size > _MAX_ZIP_ENTRY_BYTES:
            raise _RestoreBlocked(f"ZIP entry exceeds the restore limit: {name}")
        total_uncompressed += info.file_size
        if total_uncompressed > _MAX_ZIP_TOTAL_BYTES:
            raise _RestoreBlocked("ZIP total uncompressed size exceeds the restore limit")
        if info.file_size > _MAX_INTERNAL_MANIFEST_BYTES and info.compress_size == 0:
            raise _RestoreBlocked(f"ZIP entry has an unsafe compression ratio: {name}")
        if (
            info.compress_size > 0
            and info.file_size
            > max(_MAX_INTERNAL_MANIFEST_BYTES, info.compress_size * _MAX_COMPRESSION_RATIO)
        ):
            raise _RestoreBlocked(f"ZIP entry has an unsafe compression ratio: {name}")
        result[name] = info
    if "manifest.json" not in result:
        raise _RestoreBlocked("ZIP manifest.json is missing")
    return result


def _read_zip_json_object(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    label: str,
) -> dict[str, Any]:
    if info.file_size > _MAX_INTERNAL_MANIFEST_BYTES:
        raise _RestoreBlocked(f"{label} exceeds the size limit")
    with archive.open(info, mode="r") as source:
        body = source.read(_MAX_INTERNAL_MANIFEST_BYTES + 1)
        if len(body) > _MAX_INTERNAL_MANIFEST_BYTES or source.read(1):
            raise _RestoreBlocked(f"{label} exceeds the size limit")
    return _json_object_bytes(body, label)


def _json_object_bytes(body: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number: {token}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonKey, ValueError) as exc:
        raise _RestoreBlocked(f"invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise _RestoreBlocked(f"{label} must be a JSON object")
    return value


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(f"duplicate key: {key}")
        result[key] = value
    return result


def _decode_typed_json_value(value: Any, depth: int = 0) -> Any:
    if depth > _MAX_TYPED_VALUE_DEPTH:
        raise _RestoreBlocked("typed JSON value exceeds the nesting limit")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _RestoreBlocked("JSONL contains a non-finite number")
        return value
    if isinstance(value, list):
        return [_decode_typed_json_value(item, depth + 1) for item in value]
    if not isinstance(value, dict):
        raise _RestoreBlocked("JSONL contains an unsupported value")
    if "$pubg_ai_type" not in value:
        return {
            str(key): _decode_typed_json_value(item, depth + 1)
            for key, item in value.items()
        }
    if set(value) != {"$pubg_ai_type", "value"} or not isinstance(
        value["value"], str
    ):
        raise _RestoreBlocked("typed JSON wrapper fields are invalid")
    kind = value["$pubg_ai_type"]
    text = value["value"]
    try:
        if kind == "decimal":
            parsed = Decimal(text)
            if not parsed.is_finite():
                raise InvalidOperation
            return parsed
        if kind == "datetime":
            return datetime.fromisoformat(text)
        if kind == "date":
            return date.fromisoformat(text)
        if kind == "time":
            return time.fromisoformat(text)
        if kind == "bytes_base64":
            return base64.b64decode(text, validate=True)
        raise _RestoreBlocked(f"unsupported typed JSON wrapper: {kind}")
    except (ValueError, InvalidOperation, binascii.Error) as exc:
        raise _RestoreBlocked(f"invalid typed JSON wrapper: {kind}") from exc


def _mysql_insert_value(value: Any, mysql_type: str) -> Any:
    if value is None:
        return None
    if mysql_type.startswith("json"):
        if isinstance(value, str):
            _strict_json_value(value)
            return value
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=_json_default,
        )
    if isinstance(value, (dict, list)):
        raise _RestoreBlocked("structured JSON value targets a non-JSON MySQL column")
    return value


def _row_fingerprint(row: dict[str, Any], column_types: dict[str, str]) -> str:
    if set(row) != set(column_types):
        raise _RestoreBlocked("restored row columns differ from the source schema")
    normalized: dict[str, Any] = {}
    for column in sorted(column_types):
        value = row[column]
        if column_types[column].startswith("json") and value is not None:
            if isinstance(value, str):
                value = _strict_json_value(value)
            normalized[column] = {"$pubg_ai_mysql_json": value}
        else:
            normalized[column] = _typed_json_value(value)
    return _canonical_sha256(normalized)


def _strict_json_value(text: str) -> Any:
    try:
        return json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number: {token}")
            ),
        )
    except (json.JSONDecodeError, _DuplicateJsonKey, ValueError) as exc:
        raise _RestoreBlocked(f"invalid MySQL JSON value: {exc}") from exc


def _typed_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _RestoreBlocked("restored row contains a non-finite float")
        return value
    if isinstance(value, Decimal):
        return {"$pubg_ai_type": "decimal", "value": str(value)}
    if isinstance(value, datetime):
        return {"$pubg_ai_type": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"$pubg_ai_type": "date", "value": value.isoformat()}
    if isinstance(value, time):
        return {"$pubg_ai_type": "time", "value": value.isoformat()}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {
            "$pubg_ai_type": "bytes_base64",
            "value": base64.b64encode(bytes(value)).decode("ascii"),
        }
    if isinstance(value, Mapping):
        return {str(key): _typed_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_typed_json_value(item) for item in value]
    raise _RestoreBlocked(
        f"restored row contains unsupported value type: {type(value).__name__}"
    )


def _json_default(value: Any) -> Any:
    return _typed_json_value(value)


def _row_set_fingerprint(hashes: list[str]) -> str:
    digest = hashlib.sha256()
    for value in sorted(hashes):
        digest.update(bytes.fromhex(_fingerprint(value, "row fingerprint")))
    return digest.hexdigest()


def _safe_zip_entry_name(value: str) -> str:
    if not value or "\\" in value or ":" in value:
        raise _RestoreBlocked("ZIP entry path is unsafe")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise _RestoreBlocked("ZIP entry path is unsafe")
    return path.as_posix()


def _safe_relative_path(value: str) -> PurePosixPath:
    if not value or "\\" in value or ":" in value:
        raise _RestoreBlocked("replay relative path is unsafe")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise _RestoreBlocked("replay relative path is unsafe")
    return path


def _assert_scratch_directory(backup_root: Path, scratch: Path) -> None:
    root = backup_root.resolve(strict=True)
    resolved = scratch.resolve(strict=False)
    if (
        resolved.parent != root
        or not resolved.name.startswith(_TEMP_DIRECTORY_PREFIX)
        or resolved == root
    ):
        raise DataDeletionRestoreRehearsalError(
            "refused to use a scratch directory outside the backup root"
        )


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _file_identity(path: Path) -> tuple[int, int, int, int]:
    try:
        stat = path.stat()
    except OSError as exc:
        raise _RestoreBlocked(f"backup file is unavailable: {path}: {exc}") from exc
    if not path.is_file() or path.is_symlink():
        raise _RestoreBlocked(f"backup path is not a regular file: {path}")
    return (
        int(stat.st_size),
        int(stat.st_mtime_ns),
        int(getattr(stat, "st_dev", 0)),
        int(getattr(stat, "st_ino", 0)),
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(_COPY_CHUNK_BYTES):
                digest.update(chunk)
    except OSError as exc:
        raise _RestoreBlocked(f"failed to read backup file: {path}: {exc}") from exc
    return digest.hexdigest()


def _exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise _RestoreBlocked(f"{label} fields differ from the restore contract")


def _assert_locked_verification(
    row: Any,
    request: DataDeletionRequest,
    plan: DataDeletionDryRunPlan,
    verification: DataDeletionBackupVerificationRun,
) -> None:
    if not isinstance(row, Mapping):
        raise DataDeletionRestoreRehearsalError(
            "selected backup verification disappeared during restore rehearsal."
        )
    evidence_ids = _json_id_map(row.get("evidence_record_ids_json"))
    checks = (
        int(row.get("id") or 0) == verification.id,
        int(row.get("request_id") or 0) == request.id,
        int(row.get("dry_run_plan_id") or 0) == plan.id,
        str(row.get("contract_version") or "") == BACKUP_VERIFIER_CONTRACT_VERSION,
        str(row.get("result_status") or "") == "passed",
        hmac.compare_digest(
            str(row.get("plan_fingerprint_sha256") or ""),
            plan.plan_fingerprint_sha256,
        ),
        hmac.compare_digest(
            str(row.get("evidence_set_fingerprint_sha256") or ""),
            verification.evidence_set_fingerprint_sha256,
        ),
        evidence_ids == verification.evidence_record_ids,
        str(row.get("build_id") or "") == str(verification.build_id or ""),
        hmac.compare_digest(
            str(row.get("expected_manifest_sha256") or ""),
            verification.expected_manifest_sha256,
        ),
        hmac.compare_digest(
            str(row.get("result_fingerprint_sha256") or ""),
            verification.result_fingerprint_sha256,
        ),
    )
    if not all(checks):
        raise DataDeletionRestoreRehearsalError(
            "selected backup verification changed during restore rehearsal."
        )


def _assert_locked_artifact_evidence(
    plan: DataDeletionDryRunPlan,
    verification: DataDeletionBackupVerificationRun,
    rows: list[dict[str, Any]],
) -> None:
    expected_ids = verification.evidence_record_ids
    observed = {
        str(row.get("prerequisite_key") or ""): int(row.get("id") or 0)
        for row in rows
    }
    if observed != expected_ids:
        raise DataDeletionRestoreRehearsalError(
            "builder artifact evidence changed during restore rehearsal."
        )
    records = {
        key: _EvidenceFingerprintRecord(
            id=record_id,
            evidence_fingerprint_sha256=str(
                next(
                    row["evidence_fingerprint_sha256"]
                    for row in rows
                    if int(row["id"]) == record_id
                )
            ),
        )
        for key, record_id in expected_ids.items()
    }
    if not hmac.compare_digest(
        _fingerprint_record_set(plan, records),
        verification.evidence_set_fingerprint_sha256,
    ):
        raise DataDeletionRestoreRehearsalError(
            "builder artifact evidence fingerprint changed during restore rehearsal."
        )


@dataclass(frozen=True)
class _EvidenceFingerprintRecord:
    id: int
    evidence_fingerprint_sha256: str


def _fingerprint_record_set(
    plan: DataDeletionDryRunPlan,
    records: dict[str, _EvidenceFingerprintRecord],
) -> str:
    manifest = {
        "contract_version": BACKUP_EVIDENCE_CONTRACT_VERSION,
        "dry_run_plan_id": plan.id,
        "plan_fingerprint_sha256": plan.plan_fingerprint_sha256,
        "records": [
            {
                "prerequisite_key": key,
                "evidence_id": item.id,
                "evidence_fingerprint_sha256": item.evidence_fingerprint_sha256,
            }
            for key, item in sorted(records.items())
        ],
    }
    return _canonical_sha256(manifest)


def _restore_rehearsal_from_row(
    row: dict[str, Any],
) -> DataDeletionBackupRestoreRehearsalRun:
    status = str(row.get("result_status") or "")
    if status not in {"passed", "blocked"}:
        raise DataDeletionRestoreRehearsalError(
            f"unsupported restore rehearsal status: {status}."
        )
    result = _json_object(row.get("result_json"), "result_json")
    result_fingerprint = _fingerprint(
        row.get("result_fingerprint_sha256"),
        "result_fingerprint_sha256",
    )
    if not hmac.compare_digest(result_fingerprint, _canonical_sha256(result)):
        raise DataDeletionRestoreRehearsalError(
            "restore rehearsal result fingerprint is invalid."
        )
    evidence_ids = _json_id_map(row.get("artifact_evidence_record_ids_json"))
    verification_result = result.get("backup_verification")
    metrics = result.get("metrics")
    if not isinstance(verification_result, dict) or not isinstance(metrics, dict):
        raise DataDeletionRestoreRehearsalError(
            "restore rehearsal result bindings are invalid."
        )
    integrity_evidence_id = (
        _positive_int(row["backup_integrity_evidence_id"], "integrity evidence id")
        if row.get("backup_integrity_evidence_id") is not None
        else None
    )
    if (status == "passed") != (integrity_evidence_id is not None):
        raise DataDeletionRestoreRehearsalError(
            "restore rehearsal integrity evidence binding is invalid."
        )
    binding_checks = (
        result.get("restore_rehearsal_status") == status,
        int(result.get("request_id") or 0) == int(row["request_id"]),
        int(result.get("dry_run_plan_id") or 0) == int(row["dry_run_plan_id"]),
        int(verification_result.get("id") or 0)
        == int(row["backup_verification_run_id"]),
        verification_result.get("artifact_evidence_record_ids") == evidence_ids,
        verification_result.get("artifact_evidence_set_fingerprint_sha256")
        == str(row["artifact_evidence_set_fingerprint_sha256"]),
        verification_result.get("result_fingerprint_sha256")
        == str(row["backup_verification_result_fingerprint_sha256"]),
        verification_result.get("current_revalidation_result_fingerprint_sha256")
        == row.get("current_revalidation_result_fingerprint_sha256"),
        verification_result.get("build_id") == str(row["build_id"]),
        verification_result.get("manifest_path") == str(row["manifest_path"]),
        verification_result.get("manifest_sha256") == str(row["manifest_sha256"]),
    )
    if not all(binding_checks):
        raise DataDeletionRestoreRehearsalError(
            "restore rehearsal audit bindings are invalid."
        )
    count_fields = (
        "mysql_table_count",
        "mysql_restored_table_count",
        "mysql_row_count",
        "mysql_restored_row_count",
        "replay_file_count",
        "replay_restored_file_count",
        "replay_source_bytes",
        "replay_restored_bytes",
    )
    if any(
        _nonnegative_int(metrics.get(field), field)
        != _nonnegative_int(row.get(field), field)
        for field in count_fields
    ):
        raise DataDeletionRestoreRehearsalError(
            "restore rehearsal metric bindings are invalid."
        )
    return DataDeletionBackupRestoreRehearsalRun(
        id=int(row["id"]),
        request_id=int(row["request_id"]),
        dry_run_plan_id=int(row["dry_run_plan_id"]),
        backup_verification_run_id=int(row["backup_verification_run_id"]),
        contract_version=str(row["contract_version"]),
        plan_fingerprint_sha256=_fingerprint(
            row["plan_fingerprint_sha256"],
            "plan_fingerprint_sha256",
        ),
        artifact_evidence_set_fingerprint_sha256=_fingerprint(
            row["artifact_evidence_set_fingerprint_sha256"],
            "artifact_evidence_set_fingerprint_sha256",
        ),
        artifact_evidence_record_ids=evidence_ids,
        build_id=_required_text(row["build_id"], "build_id", 64),
        manifest_path=str(row["manifest_path"]),
        manifest_sha256=_fingerprint(row["manifest_sha256"], "manifest_sha256"),
        backup_verification_result_fingerprint_sha256=_fingerprint(
            row["backup_verification_result_fingerprint_sha256"],
            "backup_verification_result_fingerprint_sha256",
        ),
        current_revalidation_result_fingerprint_sha256=_optional_fingerprint(
            row.get("current_revalidation_result_fingerprint_sha256")
        ),
        result_fingerprint_sha256=result_fingerprint,
        result_status=status,
        result_json=result,
        mysql_table_count=_nonnegative_int(row["mysql_table_count"], "mysql_table_count"),
        mysql_restored_table_count=_nonnegative_int(
            row["mysql_restored_table_count"],
            "mysql_restored_table_count",
        ),
        mysql_row_count=_nonnegative_int(row["mysql_row_count"], "mysql_row_count"),
        mysql_restored_row_count=_nonnegative_int(
            row["mysql_restored_row_count"],
            "mysql_restored_row_count",
        ),
        replay_file_count=_nonnegative_int(row["replay_file_count"], "replay_file_count"),
        replay_restored_file_count=_nonnegative_int(
            row["replay_restored_file_count"],
            "replay_restored_file_count",
        ),
        replay_source_bytes=_nonnegative_int(
            row["replay_source_bytes"],
            "replay_source_bytes",
        ),
        replay_restored_bytes=_nonnegative_int(
            row["replay_restored_bytes"],
            "replay_restored_bytes",
        ),
        check_count=_nonnegative_int(row["check_count"], "check_count"),
        passed_check_count=_nonnegative_int(
            row["passed_check_count"],
            "passed_check_count",
        ),
        blocker_count=_nonnegative_int(row["blocker_count"], "blocker_count"),
        backup_integrity_evidence_id=integrity_evidence_id,
        run_by=_required_text(row["run_by"], "run_by", 191),
        rehearsal_note=_optional_text(row.get("rehearsal_note"), "rehearsal_note", 1000),
        run_at_kst=_datetime_value(row["run_at_kst"], "run_at_kst"),
    )


def _empty_metrics() -> dict[str, int]:
    return {
        "mysql_table_count": 0,
        "mysql_restored_table_count": 0,
        "mysql_row_count": 0,
        "mysql_restored_row_count": 0,
        "replay_file_count": 0,
        "replay_restored_file_count": 0,
        "replay_source_bytes": 0,
        "replay_restored_bytes": 0,
    }


def _cleanup_passed(checks: list[dict[str, Any]]) -> bool:
    cleanup = [item for item in checks if item.get("key") == "scratch_cleanup"]
    return bool(cleanup) and cleanup[-1].get("status") == "passed"


def _check(
    key: str,
    passed: bool,
    expected: Any,
    observed: Any,
    message: str,
) -> dict[str, Any]:
    return {
        "key": key,
        "status": "passed" if passed else "blocked",
        "expected": expected,
        "observed": observed,
        "message": message,
    }


def _not_required(key: str, message: str) -> dict[str, Any]:
    return {
        "key": key,
        "status": "not_required",
        "expected": "not required by the latest plan",
        "observed": None,
        "message": message,
    }


def _json_id_map(value: Any) -> dict[str, int]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise DataDeletionRestoreRehearsalError(
                "invalid artifact evidence record ID map."
            ) from exc
    if not isinstance(value, dict) or not value:
        raise DataDeletionRestoreRehearsalError(
            "artifact evidence record ID map is invalid."
        )
    result: dict[str, int] = {}
    for key, record_id in value.items():
        if key not in {_MYSQL_ARTIFACT_KEY, _REPLAY_ARTIFACT_KEY}:
            raise DataDeletionRestoreRehearsalError(
                "artifact evidence record ID map contains an invalid key."
            )
        result[str(key)] = _positive_int(record_id, "artifact evidence record id")
    return result


def _json_object(value: Any, label: str) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise DataDeletionRestoreRehearsalError(f"invalid {label}.") from exc
    if not isinstance(value, dict):
        raise DataDeletionRestoreRehearsalError(f"{label} must be a JSON object.")
    return value


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _json_dump(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _identifier(value: Any, label: str) -> str:
    text = str(value or "")
    if not _IDENTIFIER_PATTERN.fullmatch(text):
        raise DataDeletionRestoreRehearsalError(f"invalid {label}: {text!r}.")
    return text


def _fingerprint(value: Any, label: str) -> str:
    text = str(value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", text):
        raise DataDeletionRestoreRehearsalError(f"{label} must be a SHA-256 value.")
    return text


def _optional_fingerprint(value: Any) -> str | None:
    if value in {None, ""}:
        return None
    return _fingerprint(value, "optional fingerprint")


def _positive_int(value: Any, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise DataDeletionRestoreRehearsalError(f"{label} must be an integer.") from exc
    if parsed <= 0:
        raise DataDeletionRestoreRehearsalError(f"{label} must be positive.")
    return parsed


def _nonnegative_int(value: Any, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise DataDeletionRestoreRehearsalError(f"{label} must be an integer.") from exc
    if parsed < 0:
        raise DataDeletionRestoreRehearsalError(f"{label} must not be negative.")
    return parsed


def _required_text(value: Any, label: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum:
        raise DataDeletionRestoreRehearsalError(
            f"{label} must contain between 1 and {maximum} characters."
        )
    return text


def _optional_text(value: Any, label: str, maximum: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > maximum:
        raise DataDeletionRestoreRehearsalError(
            f"{label} must contain at most {maximum} characters."
        )
    return text


def _datetime_value(value: Any, label: str) -> datetime:
    if isinstance(value, datetime):
        return to_kst(value)
    try:
        return to_kst(datetime.fromisoformat(str(value)))
    except (TypeError, ValueError) as exc:
        raise DataDeletionRestoreRehearsalError(f"invalid {label}.") from exc


def _safe_error_message(exc: Exception) -> str:
    text = str(exc).strip() or type(exc).__name__
    return text[:2000]


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
