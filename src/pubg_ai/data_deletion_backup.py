from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import json
from pathlib import Path
import shutil
from typing import Any, Callable

from pubg_ai.data_deletion_confirmation import fingerprint_preview_record
from pubg_ai.data_deletion_dry_run import (
    DataDeletionDryRunPlan,
    DataDeletionDryRunService,
    fingerprint_dry_run_plan,
)
from pubg_ai.data_deletion_preview import (
    MAX_PREVIEW_FILE_LIMIT,
    DataDeletionImpactPreviewService,
)
from pubg_ai.data_deletion_requests import DataDeletionRequest
from pubg_ai.time_utils import now_kst, to_kst


BACKUP_EVIDENCE_CONTRACT_VERSION = "deletion-backup-evidence-v1"
REHEARSAL_CONTRACT_VERSION = "deletion-rehearsal-v1"
BACKUP_PREREQUISITE_KEYS = (
    "mysql_target_backup",
    "replay_artifact_backup",
    "quarantine_capacity_check",
    "backup_integrity_verification",
)
ALWAYS_EXECUTION_BLOCKERS = ("executor_not_implemented",)


class DataDeletionBackupError(RuntimeError):
    """Raised when backup evidence or a rehearsal contract is invalid."""


@dataclass(frozen=True)
class DataDeletionBackupEvidence:
    id: int
    request_id: int
    dry_run_plan_id: int
    contract_version: str
    plan_fingerprint_sha256: str
    prerequisite_key: str
    evidence_fingerprint_sha256: str
    evidence_json: dict[str, Any]
    recorded_by: str
    evidence_note: str | None
    recorded_at_kst: datetime

    def to_record(self) -> dict[str, Any]:
        return {
            **self.to_summary_record(),
            "evidence_json": deepcopy(self.evidence_json),
        }

    def to_summary_record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "request_id": self.request_id,
            "dry_run_plan_id": self.dry_run_plan_id,
            "contract_version": self.contract_version,
            "plan_fingerprint_sha256": self.plan_fingerprint_sha256,
            "prerequisite_key": self.prerequisite_key,
            "evidence_fingerprint_sha256": self.evidence_fingerprint_sha256,
            "recorded_by": self.recorded_by,
            "evidence_note": self.evidence_note,
            "recorded_at_kst": _iso_kst(self.recorded_at_kst),
            "immutable": True,
        }


@dataclass(frozen=True)
class DataDeletionRehearsalRun:
    id: int
    request_id: int
    dry_run_plan_id: int
    contract_version: str
    plan_fingerprint_sha256: str
    evidence_set_fingerprint_sha256: str
    result_fingerprint_sha256: str
    result_status: str
    result_json: dict[str, Any]
    check_count: int
    passed_check_count: int
    blocker_count: int
    run_by: str
    rehearsal_note: str | None
    run_at_kst: datetime

    def to_record(self) -> dict[str, Any]:
        return {
            **self.to_summary_record(),
            "result_json": deepcopy(self.result_json),
        }

    def to_summary_record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "request_id": self.request_id,
            "dry_run_plan_id": self.dry_run_plan_id,
            "contract_version": self.contract_version,
            "plan_fingerprint_sha256": self.plan_fingerprint_sha256,
            "evidence_set_fingerprint_sha256": self.evidence_set_fingerprint_sha256,
            "result_fingerprint_sha256": self.result_fingerprint_sha256,
            "result_status": self.result_status,
            "check_count": self.check_count,
            "passed_check_count": self.passed_check_count,
            "blocker_count": self.blocker_count,
            "run_by": self.run_by,
            "rehearsal_note": self.rehearsal_note,
            "run_at_kst": _iso_kst(self.run_at_kst),
            "execution_enabled": False,
            "execution_ready": False,
        }


class DataDeletionBackupService:
    def __init__(
        self,
        connection: Any,
        *,
        dry_run_service: DataDeletionDryRunService,
        preview_service: DataDeletionImpactPreviewService,
        path_probe: Callable[[str], dict[str, Any]] | None = None,
        quarantine_data_dir: Path | None = None,
    ) -> None:
        self.connection = connection
        self.dry_run_service = dry_run_service
        self.preview_service = preview_service
        self.path_probe = path_probe or inspect_local_path
        self.quarantine_data_dir = (
            quarantine_data_dir.expanduser().resolve(strict=False)
            if quarantine_data_dir is not None
            else None
        )

    def require_latest_plan(
        self,
        request: DataDeletionRequest,
        dry_run_plan_id: int,
    ) -> DataDeletionDryRunPlan:
        return self._latest_plan(request, dry_run_plan_id)

    def record_evidence(
        self,
        request: DataDeletionRequest,
        *,
        dry_run_plan_id: int,
        prerequisite_key: str,
        evidence: dict[str, Any],
        actor_id: str,
        note: str | None = None,
        reference_kst: datetime | None = None,
    ) -> DataDeletionBackupEvidence:
        actor_id = _required_text(actor_id, "actor_id", 191)
        note = _optional_text(note, "note", 1000)
        plan = self._latest_plan(request, dry_run_plan_id)
        prerequisite_key = _prerequisite_key(prerequisite_key)
        if prerequisite_key in {
            "quarantine_capacity_check",
            "backup_integrity_verification",
        }:
            raise DataDeletionBackupError(
                "capacity and integrity evidence are created only by their verified local workflows."
            )
        prerequisites = _plan_prerequisites(plan)
        prerequisite = prerequisites.get(prerequisite_key)
        if prerequisite is None:
            raise DataDeletionBackupError(
                f"prerequisite {prerequisite_key} is not part of dry-run plan {plan.id}."
            )
        if not bool(prerequisite.get("required")):
            raise DataDeletionBackupError(
                f"prerequisite {prerequisite_key} is not required for dry-run plan {plan.id}."
            )
        normalized = normalize_evidence_payload(prerequisite_key, evidence)
        evidence_fingerprint = fingerprint_backup_evidence(
            request.id,
            plan,
            prerequisite_key,
            normalized,
        )
        timestamp = _mysql_kst(reference_kst or now_kst())

        _begin(self.connection)
        try:
            with self.connection.cursor() as cursor:
                self._assert_latest_plan_locked(cursor, request, plan)
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
                        prerequisite_key,
                        evidence_fingerprint,
                        _json_dump(normalized),
                        actor_id,
                        note,
                        timestamp,
                    ),
                )
                evidence_id = int(cursor.lastrowid)
            _commit(self.connection)
        except Exception:
            _rollback(self.connection)
            raise
        return self.get_evidence(evidence_id)

    def record_evidence_batch(
        self,
        request: DataDeletionRequest,
        *,
        dry_run_plan_id: int,
        evidence_by_key: dict[str, dict[str, Any]],
        actor_id: str,
        note: str | None = None,
        reference_kst: datetime | None = None,
    ) -> dict[str, DataDeletionBackupEvidence]:
        if not isinstance(evidence_by_key, dict) or not evidence_by_key:
            raise DataDeletionBackupError("evidence_by_key must contain at least one record.")
        actor_id = _required_text(actor_id, "actor_id", 191)
        note = _optional_text(note, "note", 1000)
        plan = self._latest_plan(request, dry_run_plan_id)
        prerequisites = _plan_prerequisites(plan)
        normalized_records: list[tuple[str, dict[str, Any], str]] = []
        for prerequisite_key in BACKUP_PREREQUISITE_KEYS:
            if prerequisite_key not in evidence_by_key:
                continue
            if prerequisite_key in {
                "quarantine_capacity_check",
                "backup_integrity_verification",
            }:
                raise DataDeletionBackupError(
                    "capacity and integrity evidence are created only by their verified local workflows."
                )
            prerequisite = prerequisites.get(prerequisite_key)
            if prerequisite is None:
                raise DataDeletionBackupError(
                    f"prerequisite {prerequisite_key} is not part of dry-run plan {plan.id}."
                )
            if not bool(prerequisite.get("required")):
                raise DataDeletionBackupError(
                    f"prerequisite {prerequisite_key} is not required for dry-run plan {plan.id}."
                )
            normalized = normalize_evidence_payload(
                prerequisite_key,
                evidence_by_key[prerequisite_key],
            )
            normalized_records.append(
                (
                    prerequisite_key,
                    normalized,
                    fingerprint_backup_evidence(
                        request.id,
                        plan,
                        prerequisite_key,
                        normalized,
                    ),
                )
            )
        unknown_keys = sorted(set(evidence_by_key) - set(BACKUP_PREREQUISITE_KEYS))
        if unknown_keys:
            raise DataDeletionBackupError(
                "unsupported backup prerequisite keys: " + ", ".join(unknown_keys)
            )
        if len(normalized_records) != len(evidence_by_key):
            raise DataDeletionBackupError("one or more backup evidence records are invalid.")
        timestamp = _mysql_kst(reference_kst or now_kst())
        evidence_ids: dict[str, int] = {}

        _begin(self.connection)
        try:
            with self.connection.cursor() as cursor:
                self._assert_latest_plan_locked(cursor, request, plan)
                for prerequisite_key, normalized, evidence_fingerprint in normalized_records:
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
                            prerequisite_key,
                            evidence_fingerprint,
                            _json_dump(normalized),
                            actor_id,
                            note,
                            timestamp,
                        ),
                    )
                    evidence_ids[prerequisite_key] = int(cursor.lastrowid)
            _commit(self.connection)
        except Exception:
            _rollback(self.connection)
            raise
        return {
            prerequisite_key: self.get_evidence(evidence_id)
            for prerequisite_key, evidence_id in evidence_ids.items()
        }

    def get_evidence(self, evidence_id: int) -> DataDeletionBackupEvidence:
        evidence_id = _positive_id(evidence_id, "evidence_id")
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM data_deletion_backup_evidence WHERE id = %s",
                (evidence_id,),
            )
            row = cursor.fetchone()
        if not row:
            raise DataDeletionBackupError(f"backup evidence {evidence_id} was not found.")
        return _evidence_from_row(row)

    def list_evidence(
        self,
        dry_run_plan_id: int,
        *,
        limit: int = 100,
    ) -> list[DataDeletionBackupEvidence]:
        dry_run_plan_id = _positive_id(dry_run_plan_id, "dry_run_plan_id")
        if not 1 <= limit <= 500:
            raise DataDeletionBackupError("backup evidence limit must be between 1 and 500.")
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM data_deletion_backup_evidence
                WHERE dry_run_plan_id = %s
                ORDER BY recorded_at_kst DESC, id DESC
                LIMIT %s
                """,
                (dry_run_plan_id, limit),
            )
            rows = cursor.fetchall()
        return [_evidence_from_row(row) for row in rows]

    def latest_evidence(
        self,
        dry_run_plan_id: int,
    ) -> dict[str, DataDeletionBackupEvidence]:
        dry_run_plan_id = _positive_id(dry_run_plan_id, "dry_run_plan_id")
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT evidence.*
                FROM data_deletion_backup_evidence AS evidence
                INNER JOIN (
                    SELECT prerequisite_key, MAX(id) AS latest_id
                    FROM data_deletion_backup_evidence
                    WHERE dry_run_plan_id = %s
                    GROUP BY prerequisite_key
                ) AS latest
                    ON latest.latest_id = evidence.id
                ORDER BY evidence.prerequisite_key ASC
                """,
                (dry_run_plan_id,),
            )
            rows = cursor.fetchall()
        records = [_evidence_from_row(row) for row in rows]
        return {record.prerequisite_key: record for record in records}

    def run_rehearsal(
        self,
        request: DataDeletionRequest,
        *,
        dry_run_plan_id: int,
        actor_id: str,
        note: str | None = None,
        reference_kst: datetime | None = None,
    ) -> DataDeletionRehearsalRun:
        actor_id = _required_text(actor_id, "actor_id", 191)
        note = _optional_text(note, "note", 1000)
        plan = self._latest_plan(request, dry_run_plan_id)
        evidence = self.latest_evidence(plan.id)
        live_preview = self.preview_service.build_preview(
            request,
            file_limit=MAX_PREVIEW_FILE_LIMIT,
        ).to_record()
        live_fingerprint, _ = fingerprint_preview_record(live_preview)
        result = build_rehearsal_result(
            request,
            plan,
            evidence,
            live_fingerprint_sha256=live_fingerprint,
            path_probe=self.path_probe,
            quarantine_data_dir=self.quarantine_data_dir,
        )
        evidence_set_fingerprint = str(result["evidence_set"]["fingerprint_sha256"])
        result_fingerprint = fingerprint_rehearsal_result(result)
        checks = list(result["checks"])
        passed_count = sum(check.get("status") in {"passed", "not_required"} for check in checks)
        blockers = list(result["rehearsal_blockers"])
        result_status = "passed" if not blockers else "blocked"
        timestamp = _mysql_kst(reference_kst or now_kst())

        _begin(self.connection)
        try:
            with self.connection.cursor() as cursor:
                self._assert_latest_plan_locked(cursor, request, plan)
                self._assert_evidence_set_locked(cursor, plan, evidence)
                cursor.execute(
                    """
                    INSERT INTO data_deletion_rehearsal_runs (
                        request_id,
                        dry_run_plan_id,
                        contract_version,
                        plan_fingerprint_sha256,
                        evidence_set_fingerprint_sha256,
                        result_fingerprint_sha256,
                        result_status,
                        result_json,
                        check_count,
                        passed_check_count,
                        blocker_count,
                        run_by,
                        rehearsal_note,
                        run_at_kst
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        request.id,
                        plan.id,
                        REHEARSAL_CONTRACT_VERSION,
                        plan.plan_fingerprint_sha256,
                        evidence_set_fingerprint,
                        result_fingerprint,
                        result_status,
                        _json_dump(result),
                        len(checks),
                        passed_count,
                        len(blockers),
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
        return self.get_rehearsal(rehearsal_id)

    def get_rehearsal(self, rehearsal_id: int) -> DataDeletionRehearsalRun:
        rehearsal_id = _positive_id(rehearsal_id, "rehearsal_id")
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM data_deletion_rehearsal_runs WHERE id = %s",
                (rehearsal_id,),
            )
            row = cursor.fetchone()
        if not row:
            raise DataDeletionBackupError(f"deletion rehearsal {rehearsal_id} was not found.")
        return _rehearsal_from_row(row)

    def list_rehearsals(
        self,
        dry_run_plan_id: int,
        *,
        limit: int = 50,
    ) -> list[DataDeletionRehearsalRun]:
        dry_run_plan_id = _positive_id(dry_run_plan_id, "dry_run_plan_id")
        if not 1 <= limit <= 100:
            raise DataDeletionBackupError("rehearsal limit must be between 1 and 100.")
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM data_deletion_rehearsal_runs
                WHERE dry_run_plan_id = %s
                ORDER BY run_at_kst DESC, id DESC
                LIMIT %s
                """,
                (dry_run_plan_id, limit),
            )
            rows = cursor.fetchall()
        return [_rehearsal_from_row(row) for row in rows]

    def readiness_state(self, request: DataDeletionRequest) -> dict[str, Any]:
        plans = self.dry_run_service.list_plans(request.id, limit=1)
        plan = plans[0] if plans else None
        blockers = list(ALWAYS_EXECUTION_BLOCKERS)
        if plan is None:
            blockers.extend(
                (
                    "dry_run_plan_not_recorded",
                    "backup_evidence_not_recorded",
                    "rehearsal_not_passed",
                )
            )
            return {
                "request_id": request.id,
                "request_status": request.status,
                "latest_plan": None,
                "evidence_recording_allowed": False,
                "rehearsal_allowed": False,
                "prerequisites": [],
                "evidence_history": [],
                "latest_rehearsal": None,
                "rehearsals": [],
                "execution_blockers": blockers,
                "execution_enabled": False,
                "execution_ready": False,
            }

        _assert_plan_integrity(plan)
        latest_evidence = self.latest_evidence(plan.id)
        evidence_history = self.list_evidence(plan.id)
        rehearsals = self.list_rehearsals(plan.id)
        prerequisites = _readiness_prerequisites(plan, latest_evidence)
        required_keys = {
            item["key"] for item in prerequisites if bool(item.get("required"))
        }
        current_set_fingerprint = fingerprint_evidence_set(plan, latest_evidence)
        missing = sorted(required_keys - set(latest_evidence))
        latest_rehearsal = rehearsals[0] if rehearsals else None

        if missing:
            blockers.append("backup_evidence_not_recorded")
        if request.status != "approved":
            blockers.append("request_not_approved")
        if latest_rehearsal is None or latest_rehearsal.result_status != "passed":
            blockers.append("rehearsal_not_passed")
        elif not _rehearsal_integrity_valid(latest_rehearsal, plan):
            blockers.append("rehearsal_record_invalid")
        elif not hmac.compare_digest(
            latest_rehearsal.evidence_set_fingerprint_sha256,
            current_set_fingerprint,
        ):
            blockers.append("rehearsal_stale")

        allowed = request.status == "approved"
        return {
            "request_id": request.id,
            "request_status": request.status,
            "latest_plan": plan.to_summary_record(),
            "evidence_recording_allowed": allowed,
            "rehearsal_allowed": allowed,
            "prerequisites": prerequisites,
            "missing_prerequisite_keys": missing,
            "current_evidence_set_fingerprint_sha256": current_set_fingerprint,
            "evidence_history": [item.to_record() for item in evidence_history],
            "latest_rehearsal": latest_rehearsal.to_record() if latest_rehearsal else None,
            "rehearsals": [item.to_summary_record() for item in rehearsals],
            "execution_blockers": blockers,
            "execution_enabled": False,
            "execution_ready": False,
        }

    def _latest_plan(
        self,
        request: DataDeletionRequest,
        dry_run_plan_id: int,
    ) -> DataDeletionDryRunPlan:
        if request.status != "approved":
            raise DataDeletionBackupError(
                f"deletion request {request.id} is {request.status}; backup evidence requires approved."
            )
        dry_run_plan_id = _positive_id(dry_run_plan_id, "dry_run_plan_id")
        plans = self.dry_run_service.list_plans(request.id, limit=1)
        if not plans:
            raise DataDeletionBackupError("generate a confirmed deletion dry-run plan first.")
        plan = plans[0]
        if plan.id != dry_run_plan_id:
            raise DataDeletionBackupError(
                f"dry-run plan {dry_run_plan_id} is not the latest plan for request {request.id}."
            )
        _assert_plan_integrity(plan)
        return plan

    @staticmethod
    def _assert_latest_plan_locked(
        cursor: Any,
        request: DataDeletionRequest,
        plan: DataDeletionDryRunPlan,
    ) -> None:
        cursor.execute(
            "SELECT status FROM data_deletion_requests WHERE id = %s FOR UPDATE",
            (request.id,),
        )
        request_row = cursor.fetchone()
        if not request_row:
            raise DataDeletionBackupError(
                f"deletion request {request.id} was not found during backup audit."
            )
        status = str(request_row["status"])
        if status != "approved":
            raise DataDeletionBackupError(
                f"deletion request {request.id} changed to {status} during backup audit."
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
        if not plan_row or int(plan_row["id"]) != plan.id:
            raise DataDeletionBackupError(
                "a newer dry-run plan appeared during backup audit."
            )
        if not hmac.compare_digest(
            _fingerprint(plan_row["plan_fingerprint_sha256"]),
            plan.plan_fingerprint_sha256,
        ):
            raise DataDeletionBackupError(
                "latest dry-run plan fingerprint changed during backup audit."
            )

    @staticmethod
    def _assert_evidence_set_locked(
        cursor: Any,
        plan: DataDeletionDryRunPlan,
        expected: dict[str, DataDeletionBackupEvidence],
    ) -> None:
        cursor.execute(
            """
            SELECT id, prerequisite_key
            FROM data_deletion_backup_evidence
            WHERE dry_run_plan_id = %s
            ORDER BY id ASC
            FOR UPDATE
            """,
            (plan.id,),
        )
        rows = cursor.fetchall()
        latest_ids: dict[str, int] = {}
        for row in rows:
            latest_ids[str(row["prerequisite_key"])] = int(row["id"])
        expected_ids = {key: item.id for key, item in expected.items()}
        if latest_ids != expected_ids:
            raise DataDeletionBackupError(
                "backup evidence changed concurrently; run the rehearsal again."
            )


def normalize_evidence_payload(
    prerequisite_key: str,
    value: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DataDeletionBackupError("evidence must be a JSON object.")
    key = _prerequisite_key(prerequisite_key)
    if key in {"mysql_target_backup", "replay_artifact_backup"}:
        record = {
            "artifact_path": _absolute_path_text(value.get("artifact_path"), "artifact_path"),
            "artifact_sha256": _fingerprint(value.get("artifact_sha256")),
            "artifact_size_bytes": _positive_integer(
                value.get("artifact_size_bytes"), "artifact_size_bytes"
            ),
            "backup_created_at_kst": _iso_kst(
                _datetime_value(value.get("backup_created_at_kst"))
            ),
        }
        if key == "mysql_target_backup":
            record["covered_row_count"] = _nonnegative_integer(
                value.get("covered_row_count"), "covered_row_count"
            )
        else:
            record["covered_file_count"] = _nonnegative_integer(
                value.get("covered_file_count"), "covered_file_count"
            )
            record["covered_file_bytes"] = _nonnegative_integer(
                value.get("covered_file_bytes"), "covered_file_bytes"
            )
        return record
    if key == "quarantine_capacity_check":
        record = {
            "checked_path": _absolute_path_text(value.get("checked_path"), "checked_path"),
            "available_bytes": _nonnegative_integer(
                value.get("available_bytes"), "available_bytes"
            ),
            "verified_at_kst": _iso_kst(_datetime_value(value.get("verified_at_kst"))),
        }
        binding_keys = (
            "destination_contract_fingerprint_sha256",
            "quarantine_planning_result_fingerprint_sha256",
            "candidate_file_count",
            "candidate_file_bytes",
            "safety_reserve_bytes",
            "required_free_bytes",
            "source_disjoint_verified",
        )
        supplied = [value.get(binding_key) not in {None, ""} for binding_key in binding_keys]
        if any(supplied):
            if not all(supplied):
                raise DataDeletionBackupError(
                    "quarantine capacity evidence bindings must be supplied together."
                )
            record.update(
                {
                    "destination_contract_fingerprint_sha256": _fingerprint(
                        value.get("destination_contract_fingerprint_sha256")
                    ),
                    "quarantine_planning_result_fingerprint_sha256": _fingerprint(
                        value.get("quarantine_planning_result_fingerprint_sha256")
                    ),
                    "candidate_file_count": _nonnegative_integer(
                        value.get("candidate_file_count"), "candidate_file_count"
                    ),
                    "candidate_file_bytes": _nonnegative_integer(
                        value.get("candidate_file_bytes"), "candidate_file_bytes"
                    ),
                    "safety_reserve_bytes": _nonnegative_integer(
                        value.get("safety_reserve_bytes"), "safety_reserve_bytes"
                    ),
                    "required_free_bytes": _nonnegative_integer(
                        value.get("required_free_bytes"), "required_free_bytes"
                    ),
                    "source_disjoint_verified": bool(
                        value.get("source_disjoint_verified")
                    ),
                }
            )
        return record
    record = {
        "checksums_verified": bool(value.get("checksums_verified")),
        "restore_test_passed": bool(value.get("restore_test_passed")),
        "restore_tested_at_kst": _iso_kst(
            _datetime_value(value.get("restore_tested_at_kst"))
        ),
        "verified_at_kst": _iso_kst(_datetime_value(value.get("verified_at_kst"))),
    }
    binding_keys = (
        "artifact_evidence_set_fingerprint_sha256",
        "backup_verification_run_id",
        "backup_verification_result_fingerprint_sha256",
        "restore_rehearsal_result_fingerprint_sha256",
        "build_id",
        "manifest_sha256",
    )
    supplied = [value.get(key) not in {None, ""} for key in binding_keys]
    if any(supplied):
        if not all(supplied):
            raise DataDeletionBackupError(
                "restore integrity evidence bindings must be supplied together."
            )
        record.update(
            {
                "artifact_evidence_set_fingerprint_sha256": _fingerprint(
                    value.get("artifact_evidence_set_fingerprint_sha256")
                ),
                "backup_verification_run_id": _positive_id(
                    value.get("backup_verification_run_id"),
                    "backup_verification_run_id",
                ),
                "backup_verification_result_fingerprint_sha256": _fingerprint(
                    value.get("backup_verification_result_fingerprint_sha256")
                ),
                "restore_rehearsal_result_fingerprint_sha256": _fingerprint(
                    value.get("restore_rehearsal_result_fingerprint_sha256")
                ),
                "build_id": _required_text(value.get("build_id"), "build_id", 64),
                "manifest_sha256": _fingerprint(value.get("manifest_sha256")),
            }
        )
    return record


def fingerprint_backup_evidence(
    request_id: int,
    plan: DataDeletionDryRunPlan,
    prerequisite_key: str,
    evidence_json: dict[str, Any],
) -> str:
    manifest = {
        "contract_version": BACKUP_EVIDENCE_CONTRACT_VERSION,
        "request_id": _positive_id(request_id, "request_id"),
        "dry_run_plan_id": plan.id,
        "plan_fingerprint_sha256": plan.plan_fingerprint_sha256,
        "prerequisite_key": _prerequisite_key(prerequisite_key),
        "evidence": deepcopy(evidence_json),
    }
    return _canonical_sha256(manifest)


def fingerprint_evidence_set(
    plan: DataDeletionDryRunPlan,
    evidence: dict[str, DataDeletionBackupEvidence],
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
            for key, item in sorted(evidence.items())
        ],
    }
    return _canonical_sha256(manifest)


def build_rehearsal_result(
    request: DataDeletionRequest,
    plan: DataDeletionDryRunPlan,
    evidence: dict[str, DataDeletionBackupEvidence],
    *,
    live_fingerprint_sha256: str,
    path_probe: Callable[[str], dict[str, Any]] = None,
    quarantine_data_dir: Path | None = None,
) -> dict[str, Any]:
    probe = path_probe or inspect_local_path
    _assert_plan_integrity(plan)
    live_fingerprint = _fingerprint(live_fingerprint_sha256)
    artifact_evidence = {
        key: evidence[key]
        for key in ("mysql_target_backup", "replay_artifact_backup")
        if key in evidence
    }
    artifact_evidence_set_fingerprint = fingerprint_evidence_set(
        plan,
        artifact_evidence,
    )
    checks: list[dict[str, Any]] = [
        _check(
            "request_status",
            request.status == "approved",
            "approved",
            request.status,
            "request must remain approved",
        ),
        _check(
            "plan_fingerprint_integrity",
            True,
            plan.plan_fingerprint_sha256,
            fingerprint_dry_run_plan(plan.plan_json),
            "canonical dry-run plan fingerprint must match",
        ),
        _check(
            "live_source_fingerprint",
            hmac.compare_digest(plan.source_fingerprint_sha256, live_fingerprint),
            plan.source_fingerprint_sha256,
            live_fingerprint,
            "live deletion impact must still match the planned source",
        ),
    ]

    prerequisites = _plan_prerequisites(plan)
    for key in BACKUP_PREREQUISITE_KEYS:
        prerequisite = prerequisites.get(key)
        required = bool(prerequisite and prerequisite.get("required"))
        item = evidence.get(key)
        if not required:
            checks.append(
                {
                    "key": key,
                    "status": "not_required",
                    "expected": "not required by plan",
                    "observed": None,
                    "message": "prerequisite is not required for this plan",
                }
            )
            continue
        if item is None:
            checks.append(
                _check(
                    key,
                    False,
                    "latest immutable evidence record",
                    None,
                    "required backup evidence is missing",
                )
            )
            continue
        check = _evaluate_evidence(plan, item, probe)
        if key == "quarantine_capacity_check":
            check = _bind_capacity_evidence_to_destination(
                check,
                plan,
                item,
                quarantine_data_dir,
            )
        if key == "backup_integrity_verification":
            check = _bind_integrity_evidence_to_artifacts(
                check,
                item,
                artifact_evidence_set_fingerprint,
            )
        checks.append(check)

    blockers = [str(check["key"]) for check in checks if check["status"] == "blocked"]
    evidence_set_fingerprint = fingerprint_evidence_set(plan, evidence)
    return {
        "contract_version": REHEARSAL_CONTRACT_VERSION,
        "request_id": request.id,
        "dry_run_plan_id": plan.id,
        "plan_fingerprint_sha256": plan.plan_fingerprint_sha256,
        "source_fingerprint_sha256": plan.source_fingerprint_sha256,
        "evidence_set": {
            "fingerprint_sha256": evidence_set_fingerprint,
            "record_ids": {
                key: item.id for key, item in sorted(evidence.items())
            },
        },
        "checks": checks,
        "rehearsal_blockers": blockers,
        "rehearsal_status": "passed" if not blockers else "blocked",
        "safety": {
            "read_only": True,
            "target_mutation_performed": False,
            "backup_creation_performed": False,
            "checksum_recalculation_performed": False,
            "restore_operation_performed": False,
            "execution_enabled": False,
            "execution_ready": False,
            "execution_blockers": list(ALWAYS_EXECUTION_BLOCKERS),
        },
    }


def fingerprint_rehearsal_result(result_json: dict[str, Any]) -> str:
    return _canonical_sha256(result_json)


def inspect_local_path(path_text: str) -> dict[str, Any]:
    path = Path(path_text)
    result: dict[str, Any] = {
        "path": str(path),
        "absolute": path.is_absolute(),
        "exists": False,
        "is_file": False,
        "is_dir": False,
        "size_bytes": None,
        "free_bytes": None,
        "error": None,
    }
    if not path.is_absolute():
        result["error"] = "path is not absolute"
        return result
    try:
        result["exists"] = path.exists()
        if not result["exists"]:
            return result
        result["is_file"] = path.is_file()
        result["is_dir"] = path.is_dir()
        if result["is_file"]:
            result["size_bytes"] = path.stat().st_size
        if result["is_dir"]:
            result["free_bytes"] = shutil.disk_usage(path).free
    except OSError as exc:
        result["error"] = str(exc)
    return result


def _evaluate_evidence(
    plan: DataDeletionDryRunPlan,
    evidence: DataDeletionBackupEvidence,
    path_probe: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    key = evidence.prerequisite_key
    fingerprint = fingerprint_backup_evidence(
        evidence.request_id,
        plan,
        key,
        evidence.evidence_json,
    )
    issues: list[str] = []
    if evidence.contract_version != BACKUP_EVIDENCE_CONTRACT_VERSION:
        issues.append("unsupported evidence contract version")
    if evidence.dry_run_plan_id != plan.id:
        issues.append("evidence belongs to another plan")
    if not hmac.compare_digest(evidence.plan_fingerprint_sha256, plan.plan_fingerprint_sha256):
        issues.append("evidence plan fingerprint mismatch")
    if not hmac.compare_digest(evidence.evidence_fingerprint_sha256, fingerprint):
        issues.append("evidence fingerprint mismatch")

    payload = evidence.evidence_json
    metrics = plan.plan_json.get("metrics") or {}
    plan_time = _mysql_kst(plan.generated_at_kst)
    observed: dict[str, Any] = {
        "evidence_id": evidence.id,
        "evidence_fingerprint_sha256": evidence.evidence_fingerprint_sha256,
    }
    recorded_time = _mysql_kst(evidence.recorded_at_kst)
    if key in {"mysql_target_backup", "replay_artifact_backup"}:
        path_state = path_probe(str(payload.get("artifact_path") or ""))
        observed["path_state"] = path_state
        expected_size = _nonnegative_integer(payload.get("artifact_size_bytes"), "artifact_size_bytes")
        if not path_state.get("absolute"):
            issues.append("backup artifact path is not absolute")
        if not path_state.get("exists") or not path_state.get("is_file"):
            issues.append("backup artifact file is not present")
        if path_state.get("size_bytes") != expected_size:
            issues.append("backup artifact size differs from evidence")
        backup_time = _mysql_kst(_datetime_value(payload.get("backup_created_at_kst")))
        if backup_time < plan_time:
            issues.append("backup predates the dry-run plan")
        if backup_time > recorded_time:
            issues.append("backup creation time is later than evidence recording time")
        if key == "mysql_target_backup":
            expected_rows = _nonnegative_integer(metrics.get("candidate_row_count"), "candidate_row_count")
            observed_rows = _nonnegative_integer(payload.get("covered_row_count"), "covered_row_count")
            observed["covered_row_count"] = observed_rows
            if observed_rows != expected_rows:
                issues.append("covered row count differs from the plan")
        else:
            expected_files = _nonnegative_integer(metrics.get("candidate_file_count"), "candidate_file_count")
            expected_bytes = _nonnegative_integer(metrics.get("candidate_file_bytes"), "candidate_file_bytes")
            observed_files = _nonnegative_integer(payload.get("covered_file_count"), "covered_file_count")
            observed_bytes = _nonnegative_integer(payload.get("covered_file_bytes"), "covered_file_bytes")
            observed["covered_file_count"] = observed_files
            observed["covered_file_bytes"] = observed_bytes
            if observed_files != expected_files:
                issues.append("covered file count differs from the plan")
            if observed_bytes != expected_bytes:
                issues.append("covered source bytes differ from the plan")
    elif key == "quarantine_capacity_check":
        path_state = path_probe(str(payload.get("checked_path") or ""))
        observed["path_state"] = path_state
        required_bytes = _nonnegative_integer(metrics.get("candidate_file_bytes"), "candidate_file_bytes")
        recorded_available = _nonnegative_integer(payload.get("available_bytes"), "available_bytes")
        observed["recorded_available_bytes"] = recorded_available
        if not path_state.get("absolute"):
            issues.append("capacity path is not absolute")
        if not path_state.get("exists") or not path_state.get("is_dir"):
            issues.append("capacity path directory is not present")
        if recorded_available < required_bytes:
            issues.append("recorded available bytes are below the plan requirement")
        live_free = path_state.get("free_bytes")
        if not isinstance(live_free, int) or live_free < required_bytes:
            issues.append("current free bytes are below the plan requirement")
        verified_time = _mysql_kst(_datetime_value(payload.get("verified_at_kst")))
        if verified_time < plan_time:
            issues.append("capacity evidence predates the dry-run plan")
        if verified_time > recorded_time:
            issues.append("capacity verification time is later than evidence recording time")
    else:
        observed["checksums_verified"] = bool(payload.get("checksums_verified"))
        observed["restore_test_passed"] = bool(payload.get("restore_test_passed"))
        if not observed["checksums_verified"]:
            issues.append("checksum verification was not attested")
        if not observed["restore_test_passed"]:
            issues.append("restore rehearsal was not attested as passed")
        restore_time = _mysql_kst(_datetime_value(payload.get("restore_tested_at_kst")))
        verified_time = _mysql_kst(_datetime_value(payload.get("verified_at_kst")))
        if restore_time < plan_time or verified_time < plan_time:
            issues.append("integrity evidence predates the dry-run plan")
        if verified_time < restore_time:
            issues.append("verification time predates restore rehearsal time")
        if restore_time > recorded_time or verified_time > recorded_time:
            issues.append("integrity evidence time is later than evidence recording time")

    return {
        "key": key,
        "status": "passed" if not issues else "blocked",
        "expected": "evidence consistent with the latest immutable plan",
        "observed": observed,
        "message": "evidence is consistent" if not issues else "; ".join(issues),
    }


def _bind_capacity_evidence_to_destination(
    check: dict[str, Any],
    plan: DataDeletionDryRunPlan,
    evidence: DataDeletionBackupEvidence,
    quarantine_data_dir: Path | None,
) -> dict[str, Any]:
    result = deepcopy(check)
    payload = evidence.evidence_json
    observed = dict(result.get("observed") or {})
    metrics = plan.plan_json.get("metrics") or {}
    expected_count = _nonnegative_integer(
        metrics.get("candidate_file_count"), "candidate_file_count"
    )
    expected_bytes = _nonnegative_integer(
        metrics.get("candidate_file_bytes"), "candidate_file_bytes"
    )
    minimum_reserve = 0 if expected_bytes == 0 else max(
        64 * 1024 * 1024,
        (expected_bytes * 5 + 99) // 100,
    )
    required_binding_keys = (
        "destination_contract_fingerprint_sha256",
        "quarantine_planning_result_fingerprint_sha256",
        "candidate_file_count",
        "candidate_file_bytes",
        "safety_reserve_bytes",
        "required_free_bytes",
        "source_disjoint_verified",
    )
    issues: list[str] = []
    if any(payload.get(key) in {None, ""} for key in required_binding_keys):
        issues.append("capacity evidence is not bound to a read-only quarantine plan")
    else:
        candidate_count = _nonnegative_integer(
            payload.get("candidate_file_count"), "candidate_file_count"
        )
        candidate_bytes = _nonnegative_integer(
            payload.get("candidate_file_bytes"), "candidate_file_bytes"
        )
        reserve_bytes = _nonnegative_integer(
            payload.get("safety_reserve_bytes"), "safety_reserve_bytes"
        )
        required_bytes = _nonnegative_integer(
            payload.get("required_free_bytes"), "required_free_bytes"
        )
        available_bytes = _nonnegative_integer(
            payload.get("available_bytes"), "available_bytes"
        )
        observed.update(
            {
                "candidate_file_count": candidate_count,
                "candidate_file_bytes": candidate_bytes,
                "safety_reserve_bytes": reserve_bytes,
                "required_free_bytes": required_bytes,
                "destination_contract_fingerprint_sha256": payload.get(
                    "destination_contract_fingerprint_sha256"
                ),
                "quarantine_planning_result_fingerprint_sha256": payload.get(
                    "quarantine_planning_result_fingerprint_sha256"
                ),
            }
        )
        if candidate_count != expected_count or candidate_bytes != expected_bytes:
            issues.append("capacity evidence candidate metrics differ from the plan")
        if reserve_bytes < minimum_reserve:
            issues.append("capacity evidence safety reserve is below the policy")
        if required_bytes != candidate_bytes + reserve_bytes:
            issues.append("capacity evidence required bytes are inconsistent")
        if available_bytes < required_bytes:
            issues.append("recorded available bytes are below the bound requirement")
        live_free = (observed.get("path_state") or {}).get("free_bytes")
        if not isinstance(live_free, int) or live_free < required_bytes:
            issues.append("current free bytes are below the bound requirement")
        if payload.get("source_disjoint_verified") is not True:
            issues.append("source-disjoint destination was not verified")
        if quarantine_data_dir is not None:
            checked = os.path.normcase(
                str(Path(str(payload.get("checked_path"))).resolve(strict=False))
            )
            configured = os.path.normcase(
                str(quarantine_data_dir.resolve(strict=False))
            )
            observed["configured_quarantine_path"] = str(quarantine_data_dir)
            if checked != configured:
                issues.append("capacity evidence belongs to a different quarantine root")
    result["observed"] = observed
    if result.get("status") == "blocked":
        existing = str(result.get("message") or "").strip()
        if existing:
            issues.insert(0, existing)
    if issues:
        result["status"] = "blocked"
        result["message"] = "; ".join(issues)
    return result


def _bind_integrity_evidence_to_artifacts(
    check: dict[str, Any],
    evidence: DataDeletionBackupEvidence,
    artifact_evidence_set_fingerprint: str,
) -> dict[str, Any]:
    result = deepcopy(check)
    observed = dict(result.get("observed") or {})
    binding = evidence.evidence_json.get(
        "artifact_evidence_set_fingerprint_sha256"
    )
    observed["artifact_evidence_set_fingerprint_sha256"] = binding
    observed["current_artifact_evidence_set_fingerprint_sha256"] = (
        artifact_evidence_set_fingerprint
    )
    result["observed"] = observed
    issues: list[str] = []
    required_binding_keys = (
        "artifact_evidence_set_fingerprint_sha256",
        "backup_verification_run_id",
        "backup_verification_result_fingerprint_sha256",
        "restore_rehearsal_result_fingerprint_sha256",
        "build_id",
        "manifest_sha256",
    )
    if any(evidence.evidence_json.get(key) in {None, ""} for key in required_binding_keys):
        issues.append("integrity evidence is not bound to an isolated restore rehearsal")
    elif not hmac.compare_digest(
        str(binding),
        artifact_evidence_set_fingerprint,
    ):
        issues.append("integrity evidence belongs to a different artifact evidence set")
    if result.get("status") == "blocked":
        existing = str(result.get("message") or "").strip()
        if existing:
            issues.insert(0, existing)
    if issues:
        result["status"] = "blocked"
        result["message"] = "; ".join(issues)
    return result


def _readiness_prerequisites(
    plan: DataDeletionDryRunPlan,
    evidence: dict[str, DataDeletionBackupEvidence],
) -> list[dict[str, Any]]:
    prerequisites = _plan_prerequisites(plan)
    rows: list[dict[str, Any]] = []
    for key in BACKUP_PREREQUISITE_KEYS:
        prerequisite = prerequisites.get(key)
        if prerequisite is None:
            continue
        item = evidence.get(key)
        rows.append(
            {
                "key": key,
                "required": bool(prerequisite.get("required")),
                "description": str(prerequisite.get("description") or ""),
                "evidence_status": "recorded" if item else "not_recorded",
                "latest_evidence": item.to_record() if item else None,
            }
        )
    return rows


def _plan_prerequisites(plan: DataDeletionDryRunPlan) -> dict[str, dict[str, Any]]:
    records = plan.plan_json.get("backup_prerequisites")
    if not isinstance(records, list):
        raise DataDeletionBackupError("dry-run plan backup prerequisites are missing.")
    result: dict[str, dict[str, Any]] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "")
        if key in BACKUP_PREREQUISITE_KEYS:
            result[key] = dict(item)
    if not result:
        raise DataDeletionBackupError("dry-run plan has no supported backup prerequisites.")
    return result


def _assert_plan_integrity(plan: DataDeletionDryRunPlan) -> None:
    calculated = fingerprint_dry_run_plan(plan.plan_json)
    if not hmac.compare_digest(plan.plan_fingerprint_sha256, calculated):
        raise DataDeletionBackupError("dry-run plan fingerprint is invalid.")
    if int(plan.plan_json.get("request_id") or 0) != plan.request_id:
        raise DataDeletionBackupError("dry-run plan request binding is invalid.")
    if str(plan.plan_json.get("contract_version") or "") != plan.contract_version:
        raise DataDeletionBackupError("dry-run plan contract binding is invalid.")
    if not hmac.compare_digest(
        _fingerprint(plan.plan_json.get("source_fingerprint_sha256")),
        plan.source_fingerprint_sha256,
    ):
        raise DataDeletionBackupError("dry-run plan source fingerprint binding is invalid.")


def _rehearsal_integrity_valid(
    rehearsal: DataDeletionRehearsalRun,
    plan: DataDeletionDryRunPlan,
) -> bool:
    if rehearsal.contract_version != REHEARSAL_CONTRACT_VERSION:
        return False
    if rehearsal.dry_run_plan_id != plan.id:
        return False
    if not hmac.compare_digest(
        rehearsal.plan_fingerprint_sha256,
        plan.plan_fingerprint_sha256,
    ):
        return False
    return hmac.compare_digest(
        rehearsal.result_fingerprint_sha256,
        fingerprint_rehearsal_result(rehearsal.result_json),
    )


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


def _evidence_from_row(row: dict[str, Any]) -> DataDeletionBackupEvidence:
    return DataDeletionBackupEvidence(
        id=int(row["id"]),
        request_id=int(row["request_id"]),
        dry_run_plan_id=int(row["dry_run_plan_id"]),
        contract_version=str(row["contract_version"]),
        plan_fingerprint_sha256=_fingerprint(row["plan_fingerprint_sha256"]),
        prerequisite_key=_prerequisite_key(row["prerequisite_key"]),
        evidence_fingerprint_sha256=_fingerprint(row["evidence_fingerprint_sha256"]),
        evidence_json=_json_object(row.get("evidence_json"), "evidence_json"),
        recorded_by=str(row["recorded_by"]),
        evidence_note=_optional_text(row.get("evidence_note"), "evidence_note", 1000),
        recorded_at_kst=_datetime_value(row["recorded_at_kst"]),
    )


def _rehearsal_from_row(row: dict[str, Any]) -> DataDeletionRehearsalRun:
    status = str(row["result_status"])
    if status not in {"passed", "blocked"}:
        raise DataDeletionBackupError(f"unsupported rehearsal result status: {status}.")
    return DataDeletionRehearsalRun(
        id=int(row["id"]),
        request_id=int(row["request_id"]),
        dry_run_plan_id=int(row["dry_run_plan_id"]),
        contract_version=str(row["contract_version"]),
        plan_fingerprint_sha256=_fingerprint(row["plan_fingerprint_sha256"]),
        evidence_set_fingerprint_sha256=_fingerprint(
            row["evidence_set_fingerprint_sha256"]
        ),
        result_fingerprint_sha256=_fingerprint(row["result_fingerprint_sha256"]),
        result_status=status,
        result_json=_json_object(row.get("result_json"), "result_json"),
        check_count=_nonnegative_integer(row.get("check_count"), "check_count"),
        passed_check_count=_nonnegative_integer(
            row.get("passed_check_count"), "passed_check_count"
        ),
        blocker_count=_nonnegative_integer(row.get("blocker_count"), "blocker_count"),
        run_by=str(row["run_by"]),
        rehearsal_note=_optional_text(row.get("rehearsal_note"), "rehearsal_note", 1000),
        run_at_kst=_datetime_value(row["run_at_kst"]),
    )


def _prerequisite_key(value: Any) -> str:
    key = str(value).strip().lower()
    if key not in BACKUP_PREREQUISITE_KEYS:
        allowed = ", ".join(BACKUP_PREREQUISITE_KEYS)
        raise DataDeletionBackupError(f"prerequisite_key must be one of: {allowed}.")
    return key


def _absolute_path_text(value: Any, label: str) -> str:
    text = _required_text(value, label, 1000)
    if not Path(text).is_absolute():
        raise DataDeletionBackupError(f"{label} must be an absolute local path.")
    return str(Path(text))


def _json_object(value: Any, label: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return deepcopy(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise DataDeletionBackupError(f"invalid {label} JSON.") from exc
        if isinstance(parsed, dict):
            return parsed
    raise DataDeletionBackupError(f"{label} must be a JSON object.")


def _json_dump(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _canonical_sha256(value: dict[str, Any]) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _fingerprint(value: Any) -> str:
    text = str(value).strip().lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise DataDeletionBackupError(
            "fingerprint must be 64 lowercase hexadecimal characters."
        )
    return text


def _required_text(value: Any, label: str, max_length: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise DataDeletionBackupError(f"{label} is required.")
    if len(text) > max_length:
        raise DataDeletionBackupError(f"{label} must be {max_length} characters or fewer.")
    return text


def _optional_text(value: Any, label: str, max_length: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > max_length:
        raise DataDeletionBackupError(f"{label} must be {max_length} characters or fewer.")
    return text


def _positive_id(value: Any, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise DataDeletionBackupError(f"{label} must be a positive integer.") from exc
    if parsed <= 0:
        raise DataDeletionBackupError(f"{label} must be a positive integer.")
    return parsed


def _positive_integer(value: Any, label: str) -> int:
    parsed = _nonnegative_integer(value, label)
    if parsed <= 0:
        raise DataDeletionBackupError(f"{label} must be greater than zero.")
    return parsed


def _nonnegative_integer(value: Any, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise DataDeletionBackupError(f"{label} must be a non-negative integer.") from exc
    if parsed < 0:
        raise DataDeletionBackupError(f"{label} must be a non-negative integer.")
    return parsed


def _datetime_value(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise DataDeletionBackupError(f"invalid datetime value: {value}.") from exc
    raise DataDeletionBackupError(f"invalid datetime value: {value!r}.")


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
