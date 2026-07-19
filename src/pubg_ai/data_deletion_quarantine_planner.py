from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping
import hashlib
import hmac
import json
import os
import re
import shutil

from pubg_ai.data_deletion_backup import (
    BACKUP_EVIDENCE_CONTRACT_VERSION,
    DataDeletionBackupService,
    fingerprint_backup_evidence,
    normalize_evidence_payload,
)
from pubg_ai.data_deletion_dry_run import (
    DataDeletionDryRunPlan,
    fingerprint_dry_run_plan,
)
from pubg_ai.data_deletion_requests import DataDeletionRequest
from pubg_ai.storage_contract import (
    canonical_storage_path,
    inspect_directory_read_only,
    overlapping_named_root,
)
from pubg_ai.time_utils import now_kst, to_kst


QUARANTINE_PLANNER_CONTRACT_VERSION = "deletion-quarantine-planner-v1"
QUARANTINE_DESTINATION_CONTRACT_VERSION = "deletion-quarantine-destination-v1"
QUARANTINE_LAYOUT_VERSION = "request-plan-item-v1"
QUARANTINE_PLANNER_CONFIRMATION_PREFIX = "RUN READ-ONLY QUARANTINE PLAN"

MINIMUM_CAPACITY_RESERVE_BYTES = 64 * 1024 * 1024
CAPACITY_RESERVE_PERCENT = 5
_CAPACITY_EVIDENCE_KEY = "quarantine_capacity_check"
_COPY_CHUNK_BYTES = 1024 * 1024
_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_VERIFICATION_STATUSES = {"present", "verified"}
_EXECUTION_BLOCKERS = (
    "quarantine_executor_not_implemented",
    "quarantine_rollback_not_rehearsed",
    "quarantine_crash_recovery_not_rehearsed",
    "database_deletion_not_implemented",
    "executor_not_implemented",
)


class DataDeletionQuarantinePlannerError(RuntimeError):
    """Raised when a read-only quarantine plan cannot be produced safely."""


@dataclass(frozen=True)
class DataDeletionQuarantinePlanningRun:
    id: int
    request_id: int
    dry_run_plan_id: int
    contract_version: str
    plan_fingerprint_sha256: str
    destination_contract_fingerprint_sha256: str
    quarantine_root: str
    result_fingerprint_sha256: str
    result_status: str
    result_json: dict[str, Any]
    candidate_file_count: int
    candidate_file_bytes: int
    safety_reserve_bytes: int
    required_free_bytes: int
    observed_free_bytes: int | None
    source_verified_file_count: int
    source_verified_bytes: int
    target_conflict_count: int
    check_count: int
    passed_check_count: int
    blocker_count: int
    capacity_evidence_id: int | None
    planned_by: str
    planning_note: str | None
    planned_at_kst: datetime

    def to_summary_record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "request_id": self.request_id,
            "dry_run_plan_id": self.dry_run_plan_id,
            "contract_version": self.contract_version,
            "plan_fingerprint_sha256": self.plan_fingerprint_sha256,
            "destination_contract_fingerprint_sha256": (
                self.destination_contract_fingerprint_sha256
            ),
            "quarantine_root": self.quarantine_root,
            "result_fingerprint_sha256": self.result_fingerprint_sha256,
            "result_status": self.result_status,
            "candidate_file_count": self.candidate_file_count,
            "candidate_file_bytes": self.candidate_file_bytes,
            "safety_reserve_bytes": self.safety_reserve_bytes,
            "required_free_bytes": self.required_free_bytes,
            "observed_free_bytes": self.observed_free_bytes,
            "source_verified_file_count": self.source_verified_file_count,
            "source_verified_bytes": self.source_verified_bytes,
            "target_conflict_count": self.target_conflict_count,
            "check_count": self.check_count,
            "passed_check_count": self.passed_check_count,
            "blocker_count": self.blocker_count,
            "capacity_evidence_id": self.capacity_evidence_id,
            "planned_by": self.planned_by,
            "planning_note": self.planning_note,
            "planned_at_kst": to_kst(self.planned_at_kst).isoformat(),
            "immutable": True,
            "read_only": True,
            "directories_created": False,
            "files_copied": False,
            "files_moved": False,
            "source_files_removed": False,
            "database_rows_modified": False,
            "quarantine_performed": False,
            "deletion_performed": False,
            "execution_enabled": False,
            "execution_ready": False,
        }

    def to_record(self) -> dict[str, Any]:
        return {**self.to_summary_record(), "result_json": self.result_json}


DirectoryProbe = Callable[[Path], dict[str, Any]]
FreeBytesProbe = Callable[[Path], int]
FileHasher = Callable[[Path], str]


class DataDeletionQuarantinePlannerService:
    def __init__(
        self,
        connection: Any,
        *,
        backup_service: DataDeletionBackupService,
        quarantine_root: Path,
        raw_data_dir: Path,
        replay_data_dir: Path,
        backup_root: Path,
        directory_probe: DirectoryProbe | None = None,
        free_bytes_probe: FreeBytesProbe | None = None,
        file_hasher: FileHasher | None = None,
    ) -> None:
        self.connection = connection
        self.backup_service = backup_service
        self.quarantine_root_input = _absolute_input_path(quarantine_root)
        self.quarantine_root = canonical_storage_path(self.quarantine_root_input)
        self.raw_data_dir = canonical_storage_path(raw_data_dir)
        self.replay_data_dir = canonical_storage_path(replay_data_dir)
        self.backup_root = canonical_storage_path(backup_root)
        self.directory_probe = directory_probe or inspect_directory_read_only
        self.free_bytes_probe = free_bytes_probe or _free_bytes
        self.file_hasher = file_hasher or _sha256_file

    def planning_state(self, request: DataDeletionRequest) -> dict[str, Any]:
        plans = self.backup_service.dry_run_service.list_plans(request.id, limit=1)
        plan = plans[0] if plans else None
        root_status = self.directory_probe(self.quarantine_root_input)
        blockers = self._planning_blockers(request, plan, root_status)
        history = self.list_runs(plan.id, limit=50) if plan is not None else []
        destination = (
            build_quarantine_destination_contract(plan, self.quarantine_root)
            if plan is not None
            else None
        )
        destination_fingerprint = (
            fingerprint_quarantine_destination_contract(destination)
            if destination is not None
            else None
        )
        latest = history[0] if history else None
        latest_current = bool(
            latest is not None
            and destination_fingerprint is not None
            and hmac.compare_digest(
                latest.destination_contract_fingerprint_sha256,
                destination_fingerprint,
            )
            and latest.result_status == "passed"
        )
        confirmation = (
            expected_quarantine_planning_confirmation(
                request.id,
                plan.id,
                plan.plan_fingerprint_sha256,
                destination_fingerprint,
            )
            if plan is not None and destination_fingerprint is not None
            else None
        )
        return {
            "request_id": request.id,
            "request_status": request.status,
            "contract_version": QUARANTINE_PLANNER_CONTRACT_VERSION,
            "destination_contract_version": QUARANTINE_DESTINATION_CONTRACT_VERSION,
            "quarantine_layout_version": QUARANTINE_LAYOUT_VERSION,
            "quarantine_root": str(self.quarantine_root),
            "quarantine_root_status": root_status,
            "source_roots": {
                "PUBG_RAW_DATA_DIR": str(self.raw_data_dir),
                "PUBG_REPLAY_DATA_DIR": str(self.replay_data_dir),
                "PUBG_BACKUP_DATA_DIR": str(self.backup_root),
            },
            "latest_plan_id": plan.id if plan is not None else None,
            "plan_fingerprint_sha256": (
                plan.plan_fingerprint_sha256 if plan is not None else None
            ),
            "candidate_file_count": (
                _plan_metric(plan, "candidate_file_count") if plan is not None else 0
            ),
            "candidate_file_bytes": (
                _plan_metric(plan, "candidate_file_bytes") if plan is not None else 0
            ),
            "destination_contract": destination,
            "destination_contract_fingerprint_sha256": destination_fingerprint,
            "confirmation_text": confirmation,
            "planning_allowed": not blockers,
            "planning_blockers": blockers,
            "latest_planning_run": latest.to_record() if latest is not None else None,
            "planning_history": [item.to_summary_record() for item in history],
            "latest_passed_plan_matches_current_destination": latest_current,
            "read_only_probe": True,
            "write_probe_performed": False,
            "appends_capacity_evidence_only_on_pass": True,
            "directories_created": False,
            "files_copied": False,
            "files_moved": False,
            "source_files_removed": False,
            "database_rows_modified": False,
            "quarantine_performed": False,
            "deletion_performed": False,
            "execution_blockers": list(_EXECUTION_BLOCKERS),
            "execution_enabled": False,
            "execution_ready": False,
        }

    def run(
        self,
        request: DataDeletionRequest,
        *,
        dry_run_plan_id: int,
        confirmation_text: str,
        actor_id: str,
        note: str | None = None,
        reference_kst: datetime | None = None,
    ) -> DataDeletionQuarantinePlanningRun:
        actor_id = _required_text(actor_id, "actor_id", 191)
        note = _optional_text(note, "note", 1000)
        plan = self.backup_service.require_latest_plan(request, dry_run_plan_id)
        root_status = self.directory_probe(self.quarantine_root_input)
        blockers = self._planning_blockers(request, plan, root_status)
        if blockers:
            raise DataDeletionQuarantinePlannerError(
                "quarantine planning blocked: " + "; ".join(blockers)
            )
        destination = build_quarantine_destination_contract(
            plan,
            self.quarantine_root,
        )
        destination_fingerprint = fingerprint_quarantine_destination_contract(
            destination
        )
        expected_confirmation = expected_quarantine_planning_confirmation(
            request.id,
            plan.id,
            plan.plan_fingerprint_sha256,
            destination_fingerprint,
        )
        supplied_confirmation = _required_text(
            confirmation_text,
            "confirmation_text",
            500,
        )
        if not hmac.compare_digest(supplied_confirmation, expected_confirmation):
            raise DataDeletionQuarantinePlannerError(
                "quarantine planning confirmation does not match the current destination contract."
            )
        planned_at = to_kst(reference_kst or now_kst())
        result = build_quarantine_planning_result(
            request,
            plan,
            quarantine_root=self.quarantine_root,
            raw_data_dir=self.raw_data_dir,
            replay_data_dir=self.replay_data_dir,
            backup_root=self.backup_root,
            destination_contract=destination,
            destination_contract_fingerprint_sha256=destination_fingerprint,
            root_status=root_status,
            free_bytes_probe=self.free_bytes_probe,
            file_hasher=self.file_hasher,
            planned_at_kst=planned_at,
        )
        result_fingerprint = _canonical_sha256(result)
        return self._record_run(
            request,
            plan,
            destination_fingerprint=destination_fingerprint,
            result=result,
            result_fingerprint=result_fingerprint,
            actor_id=actor_id,
            note=note,
            planned_at_kst=planned_at,
        )

    def get_run(self, planning_run_id: int) -> DataDeletionQuarantinePlanningRun:
        planning_run_id = _positive_int(planning_run_id, "planning_run_id")
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM data_deletion_quarantine_planning_runs WHERE id = %s",
                (planning_run_id,),
            )
            row = cursor.fetchone()
        if not row:
            raise DataDeletionQuarantinePlannerError(
                f"quarantine planning run {planning_run_id} was not found."
            )
        return _planning_run_from_row(row)

    def list_runs(
        self,
        dry_run_plan_id: int,
        *,
        limit: int = 50,
    ) -> list[DataDeletionQuarantinePlanningRun]:
        dry_run_plan_id = _positive_int(dry_run_plan_id, "dry_run_plan_id")
        if not 1 <= int(limit) <= 100:
            raise DataDeletionQuarantinePlannerError(
                "quarantine planning history limit must be between 1 and 100."
            )
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM data_deletion_quarantine_planning_runs
                WHERE dry_run_plan_id = %s
                ORDER BY planned_at_kst DESC, id DESC
                LIMIT %s
                """,
                (dry_run_plan_id, int(limit)),
            )
            rows = cursor.fetchall()
        return [_planning_run_from_row(row) for row in rows]

    def _planning_blockers(
        self,
        request: DataDeletionRequest,
        plan: DataDeletionDryRunPlan | None,
        root_status: dict[str, Any],
    ) -> list[str]:
        blockers: list[str] = []
        if request.status != "approved":
            blockers.append(f"request status must be approved, not {request.status}")
        if plan is None:
            blockers.append("latest confirmed dry-run plan is required")
        else:
            if not hmac.compare_digest(
                plan.plan_fingerprint_sha256,
                fingerprint_dry_run_plan(plan.plan_json),
            ):
                blockers.append("latest dry-run plan fingerprint is invalid")
            if _plan_metric(plan, "candidate_file_count") <= 0:
                blockers.append("latest plan has no player-owned replay files to quarantine")
        if not bool(root_status.get("absolute")):
            blockers.append("configured quarantine root must be absolute")
        if not bool(root_status.get("exists")) or not bool(root_status.get("is_dir")):
            blockers.append("configured quarantine root must exist as a directory")
        if bool(root_status.get("is_symlink")):
            blockers.append("configured quarantine root must not be a symbolic link")
        if bool(root_status.get("is_filesystem_root")):
            blockers.append("configured quarantine root must not be a filesystem root")
        if root_status.get("error"):
            blockers.append(f"quarantine root inspection failed: {root_status['error']}")
        overlap = overlapping_named_root(
            self.quarantine_root,
            (
                ("PUBG_RAW_DATA_DIR", self.raw_data_dir),
                ("PUBG_REPLAY_DATA_DIR", self.replay_data_dir),
                ("PUBG_BACKUP_DATA_DIR", self.backup_root),
            ),
        )
        if overlap is not None:
            blockers.append(f"quarantine root overlaps configured storage: {overlap}")
        return _deduplicate(blockers)

    def _record_run(
        self,
        request: DataDeletionRequest,
        plan: DataDeletionDryRunPlan,
        *,
        destination_fingerprint: str,
        result: dict[str, Any],
        result_fingerprint: str,
        actor_id: str,
        note: str | None,
        planned_at_kst: datetime,
    ) -> DataDeletionQuarantinePlanningRun:
        metrics = _result_metrics(result)
        checks = list(result.get("checks") or [])
        blockers = list(result.get("planning_blockers") or [])
        status = str(result.get("planning_status") or "")
        if status not in {"passed", "blocked"}:
            raise DataDeletionQuarantinePlannerError(
                "quarantine planning result status is invalid."
            )
        timestamp = to_kst(planned_at_kst).replace(tzinfo=None)
        evidence_payload: dict[str, Any] | None = None
        evidence_fingerprint: str | None = None
        if status == "passed":
            evidence_payload = normalize_evidence_payload(
                _CAPACITY_EVIDENCE_KEY,
                {
                    "checked_path": str(self.quarantine_root),
                    "available_bytes": metrics["observed_free_bytes"],
                    "verified_at_kst": planned_at_kst,
                    "destination_contract_fingerprint_sha256": destination_fingerprint,
                    "quarantine_planning_result_fingerprint_sha256": result_fingerprint,
                    "candidate_file_count": metrics["candidate_file_count"],
                    "candidate_file_bytes": metrics["candidate_file_bytes"],
                    "safety_reserve_bytes": metrics["safety_reserve_bytes"],
                    "required_free_bytes": metrics["required_free_bytes"],
                    "source_disjoint_verified": True,
                },
            )
            evidence_fingerprint = fingerprint_backup_evidence(
                request.id,
                plan,
                _CAPACITY_EVIDENCE_KEY,
                evidence_payload,
            )

        _begin(self.connection)
        evidence_id: int | None = None
        try:
            with self.connection.cursor() as cursor:
                self.backup_service._assert_latest_plan_locked(cursor, request, plan)
                if evidence_payload is not None and evidence_fingerprint is not None:
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
                            _CAPACITY_EVIDENCE_KEY,
                            evidence_fingerprint,
                            _json_dump(evidence_payload),
                            actor_id,
                            (
                                f"planner={QUARANTINE_PLANNER_CONTRACT_VERSION}; "
                                f"destination_sha256={destination_fingerprint}; "
                                f"result_sha256={result_fingerprint}"
                            ),
                            timestamp,
                        ),
                    )
                    evidence_id = int(cursor.lastrowid)
                cursor.execute(
                    """
                    INSERT INTO data_deletion_quarantine_planning_runs (
                        request_id,
                        dry_run_plan_id,
                        contract_version,
                        plan_fingerprint_sha256,
                        destination_contract_fingerprint_sha256,
                        quarantine_root,
                        result_fingerprint_sha256,
                        result_status,
                        result_json,
                        candidate_file_count,
                        candidate_file_bytes,
                        safety_reserve_bytes,
                        required_free_bytes,
                        observed_free_bytes,
                        source_verified_file_count,
                        source_verified_bytes,
                        target_conflict_count,
                        check_count,
                        passed_check_count,
                        blocker_count,
                        capacity_evidence_id,
                        planned_by,
                        planning_note,
                        planned_at_kst
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                              %s, %s, %s, %s, %s, %s, %s, %s,
                              %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        request.id,
                        plan.id,
                        QUARANTINE_PLANNER_CONTRACT_VERSION,
                        plan.plan_fingerprint_sha256,
                        destination_fingerprint,
                        str(self.quarantine_root),
                        result_fingerprint,
                        status,
                        _json_dump(result),
                        metrics["candidate_file_count"],
                        metrics["candidate_file_bytes"],
                        metrics["safety_reserve_bytes"],
                        metrics["required_free_bytes"],
                        metrics["observed_free_bytes"],
                        metrics["source_verified_file_count"],
                        metrics["source_verified_bytes"],
                        metrics["target_conflict_count"],
                        len(checks),
                        sum(item.get("status") == "passed" for item in checks),
                        len(blockers),
                        evidence_id,
                        actor_id,
                        note,
                        timestamp,
                    ),
                )
                run_id = int(cursor.lastrowid)
            _commit(self.connection)
        except Exception:
            _rollback(self.connection)
            raise
        return DataDeletionQuarantinePlanningRun(
            id=run_id,
            request_id=request.id,
            dry_run_plan_id=plan.id,
            contract_version=QUARANTINE_PLANNER_CONTRACT_VERSION,
            plan_fingerprint_sha256=plan.plan_fingerprint_sha256,
            destination_contract_fingerprint_sha256=destination_fingerprint,
            quarantine_root=str(self.quarantine_root),
            result_fingerprint_sha256=result_fingerprint,
            result_status=status,
            result_json=result,
            candidate_file_count=metrics["candidate_file_count"],
            candidate_file_bytes=metrics["candidate_file_bytes"],
            safety_reserve_bytes=metrics["safety_reserve_bytes"],
            required_free_bytes=metrics["required_free_bytes"],
            observed_free_bytes=metrics["observed_free_bytes"],
            source_verified_file_count=metrics["source_verified_file_count"],
            source_verified_bytes=metrics["source_verified_bytes"],
            target_conflict_count=metrics["target_conflict_count"],
            check_count=len(checks),
            passed_check_count=sum(
                item.get("status") == "passed" for item in checks
            ),
            blocker_count=len(blockers),
            capacity_evidence_id=evidence_id,
            planned_by=actor_id,
            planning_note=note,
            planned_at_kst=planned_at_kst,
        )


def capacity_reserve_bytes(candidate_file_bytes: int) -> int:
    candidate = _nonnegative_int(candidate_file_bytes, "candidate_file_bytes")
    if candidate == 0:
        return 0
    percentage = (candidate * CAPACITY_RESERVE_PERCENT + 99) // 100
    return max(MINIMUM_CAPACITY_RESERVE_BYTES, percentage)


def build_quarantine_destination_contract(
    plan: DataDeletionDryRunPlan,
    quarantine_root: Path,
) -> dict[str, Any]:
    candidate_count = _plan_metric(plan, "candidate_file_count")
    candidate_bytes = _plan_metric(plan, "candidate_file_bytes")
    reserve_bytes = capacity_reserve_bytes(candidate_bytes)
    root = canonical_storage_path(quarantine_root)
    return {
        "contract_version": QUARANTINE_DESTINATION_CONTRACT_VERSION,
        "layout_version": QUARANTINE_LAYOUT_VERSION,
        "request_id": plan.request_id,
        "dry_run_plan_id": plan.id,
        "plan_fingerprint_sha256": plan.plan_fingerprint_sha256,
        "quarantine_root": str(root),
        "request_relative_root": (
            f"requests/{plan.request_id}/plans/{plan.id}"
        ),
        "item_path_template": (
            "requests/{request_id}/plans/{dry_run_plan_id}/items/"
            "{sequence:06d}-{record_id}/{source_relative_path}"
        ),
        "journal_relative_path": (
            f"requests/{plan.request_id}/plans/{plan.id}/.quarantine-transaction.json"
        ),
        "candidate_file_count": candidate_count,
        "candidate_file_bytes": candidate_bytes,
        "safety_reserve_bytes": reserve_bytes,
        "required_free_bytes": candidate_bytes + reserve_bytes,
        "copy_then_verify_required": True,
        "atomic_source_removal_required": True,
        "rollback_required": True,
        "crash_recovery_journal_required": True,
        "mutation_enabled": False,
    }


def fingerprint_quarantine_destination_contract(contract: dict[str, Any]) -> str:
    if not isinstance(contract, dict):
        raise DataDeletionQuarantinePlannerError(
            "quarantine destination contract must be an object."
        )
    return _canonical_sha256(contract)


def expected_quarantine_planning_confirmation(
    request_id: int,
    dry_run_plan_id: int,
    plan_fingerprint_sha256: str,
    destination_contract_fingerprint_sha256: str,
) -> str:
    return (
        f"{QUARANTINE_PLANNER_CONFIRMATION_PREFIX} REQUEST "
        f"{_positive_int(request_id, 'request_id')} PLAN "
        f"{_positive_int(dry_run_plan_id, 'dry_run_plan_id')} "
        f"{_fingerprint(plan_fingerprint_sha256, 'plan fingerprint')} DESTINATION "
        f"{_fingerprint(destination_contract_fingerprint_sha256, 'destination fingerprint')}"
    )


def build_quarantine_planning_result(
    request: DataDeletionRequest,
    plan: DataDeletionDryRunPlan,
    *,
    quarantine_root: Path,
    raw_data_dir: Path,
    replay_data_dir: Path,
    backup_root: Path,
    destination_contract: dict[str, Any],
    destination_contract_fingerprint_sha256: str,
    root_status: dict[str, Any],
    free_bytes_probe: FreeBytesProbe | None = None,
    file_hasher: FileHasher | None = None,
    planned_at_kst: datetime | None = None,
) -> dict[str, Any]:
    hasher = file_hasher or _sha256_file
    free_probe = free_bytes_probe or _free_bytes
    quarantine = canonical_storage_path(quarantine_root)
    replay_root = canonical_storage_path(replay_data_dir)
    destination_fingerprint = _fingerprint(
        destination_contract_fingerprint_sha256,
        "destination contract fingerprint",
    )
    if not hmac.compare_digest(
        destination_fingerprint,
        fingerprint_quarantine_destination_contract(destination_contract),
    ):
        raise DataDeletionQuarantinePlannerError(
            "quarantine destination contract fingerprint is invalid."
        )
    if not hmac.compare_digest(
        plan.plan_fingerprint_sha256,
        fingerprint_dry_run_plan(plan.plan_json),
    ):
        raise DataDeletionQuarantinePlannerError(
            "dry-run plan fingerprint is invalid."
        )
    overlap = overlapping_named_root(
        quarantine,
        (
            ("PUBG_RAW_DATA_DIR", raw_data_dir),
            ("PUBG_REPLAY_DATA_DIR", replay_root),
            ("PUBG_BACKUP_DATA_DIR", backup_root),
        ),
    )
    root_ok = bool(
        root_status.get("absolute")
        and root_status.get("exists")
        and root_status.get("is_dir")
        and not root_status.get("is_symlink")
        and not root_status.get("is_filesystem_root")
        and not root_status.get("error")
    )
    checks: list[dict[str, Any]] = [
        _check(
            "quarantine_root_read_only_inspection",
            root_ok,
            "existing absolute non-symlink directory inspected without writes",
            root_status,
            (
                "quarantine root is structurally valid"
                if root_ok
                else "quarantine root failed the read-only directory contract"
            ),
        ),
        _check(
            "source_disjoint_destination",
            overlap is None,
            "quarantine root does not overlap raw, replay, or backup roots",
            {"overlapping_root": overlap},
            (
                "quarantine destination is source-disjoint"
                if overlap is None
                else f"quarantine destination overlaps {overlap}"
            ),
        ),
    ]

    operations = _plan_file_operations(plan)
    operation_records: list[dict[str, Any]] = []
    operation_errors: list[dict[str, Any]] = []
    source_keys: set[str] = set()
    target_keys: set[str] = set()
    target_conflicts: list[str] = []
    verified_bytes = 0
    for operation in operations:
        try:
            record = _inspect_file_operation(
                request,
                plan,
                operation,
                replay_root=replay_root,
                quarantine_root=quarantine,
                file_hasher=hasher,
            )
            source_key = os.path.normcase(str(record["source_path"]))
            target_key = os.path.normcase(str(record["target_path"]))
            if source_key in source_keys:
                raise DataDeletionQuarantinePlannerError(
                    "dry-run plan contains a duplicate source file"
                )
            if target_key in target_keys:
                raise DataDeletionQuarantinePlannerError(
                    "quarantine layout contains a duplicate target file"
                )
            source_keys.add(source_key)
            target_keys.add(target_key)
            if bool(record["target_exists"]):
                target_conflicts.append(str(record["target_path"]))
            operation_records.append(record)
            verified_bytes += int(record["declared_size_bytes"])
        except Exception as exc:
            operation_errors.append(
                {
                    "sequence": operation.get("sequence"),
                    "record_id": operation.get("record_id"),
                    "relative_path": operation.get("relative_path"),
                    "error": _safe_error_message(exc),
                }
            )

    candidate_count = _plan_metric(plan, "candidate_file_count")
    candidate_bytes = _plan_metric(plan, "candidate_file_bytes")
    source_contract_ok = bool(
        not operation_errors
        and len(operation_records) == candidate_count
        and verified_bytes == candidate_bytes
    )
    checks.append(
        _check(
            "source_file_contract",
            source_contract_ok,
            {
                "candidate_file_count": candidate_count,
                "candidate_file_bytes": candidate_bytes,
                "stable_size_and_sha256": True,
            },
            {
                "verified_file_count": len(operation_records),
                "verified_bytes": verified_bytes,
                "errors": operation_errors,
            },
            (
                "every planned source file matched its stable size and SHA-256"
                if source_contract_ok
                else "one or more planned source files failed read-only verification"
            ),
        )
    )
    checks.append(
        _check(
            "destination_target_conflicts",
            not target_conflicts,
            "no planned quarantine target already exists",
            {"conflicts": target_conflicts},
            (
                "all quarantine targets are currently absent"
                if not target_conflicts
                else "one or more quarantine targets already exist"
            ),
        )
    )

    reserve_bytes = capacity_reserve_bytes(candidate_bytes)
    required_free_bytes = candidate_bytes + reserve_bytes
    observed_free_bytes: int | None = None
    capacity_error: str | None = None
    try:
        observed_free_bytes = _nonnegative_int(
            free_probe(quarantine),
            "observed_free_bytes",
        )
    except Exception as exc:
        capacity_error = _safe_error_message(exc)
    capacity_ok = bool(
        observed_free_bytes is not None
        and observed_free_bytes >= required_free_bytes
    )
    checks.append(
        _check(
            "quarantine_capacity",
            capacity_ok,
            {
                "candidate_file_bytes": candidate_bytes,
                "safety_reserve_bytes": reserve_bytes,
                "required_free_bytes": required_free_bytes,
            },
            {
                "observed_free_bytes": observed_free_bytes,
                "error": capacity_error,
            },
            (
                "quarantine volume has enough free space for copy-verify and reserve"
                if capacity_ok
                else "quarantine volume free space is below the planning requirement"
            ),
        )
    )

    protected_baseline = _canonical_sha256(
        {
            "row_exclusions": plan.plan_json.get("row_exclusions") or [],
            "file_exclusions": plan.plan_json.get("file_exclusions") or [],
            "audit_table_exclusions": plan.plan_json.get("audit_table_exclusions") or [],
        }
    )
    postconditions = _postcondition_contract(
        request,
        plan,
        operation_records,
        quarantine,
        protected_baseline,
    )
    rollback = _rollback_contract(operation_records)
    crash_recovery = _crash_recovery_contract(destination_contract)
    contract_complete = bool(
        len(postconditions["item_checks"]) == len(operation_records)
        and len(rollback["item_actions"]) == len(operation_records)
        and crash_recovery.get("journal_relative_path")
    )
    checks.append(
        _check(
            "postcondition_rollback_recovery_contract",
            contract_complete,
            "deterministic postcondition, rollback, and crash-recovery descriptors",
            {
                "postcondition_item_count": len(postconditions["item_checks"]),
                "rollback_item_count": len(rollback["item_actions"]),
                "journal_relative_path": crash_recovery.get("journal_relative_path"),
            },
            (
                "postcondition, rollback, and crash-recovery descriptors are complete"
                if contract_complete
                else "one or more recovery contract descriptors are incomplete"
            ),
        )
    )

    blockers = [
        str(item["key"])
        for item in checks
        if item.get("status") == "blocked"
    ]
    status = "passed" if not blockers else "blocked"
    planned_at = to_kst(planned_at_kst or now_kst())
    return {
        "contract_version": QUARANTINE_PLANNER_CONTRACT_VERSION,
        "request_id": request.id,
        "dry_run_plan_id": plan.id,
        "plan_fingerprint_sha256": plan.plan_fingerprint_sha256,
        "destination_contract": destination_contract,
        "destination_contract_fingerprint_sha256": destination_fingerprint,
        "quarantine_root": str(quarantine),
        "checks": checks,
        "metrics": {
            "candidate_file_count": candidate_count,
            "candidate_file_bytes": candidate_bytes,
            "safety_reserve_bytes": reserve_bytes,
            "required_free_bytes": required_free_bytes,
            "observed_free_bytes": observed_free_bytes,
            "source_verified_file_count": len(operation_records),
            "source_verified_bytes": verified_bytes,
            "target_conflict_count": len(target_conflicts),
        },
        "file_operations": operation_records,
        "protected_data_baseline_fingerprint_sha256": protected_baseline,
        "postcondition_contract": postconditions,
        "rollback_contract": rollback,
        "crash_recovery_contract": crash_recovery,
        "planning_blockers": blockers,
        "planning_status": status,
        "planned_at_kst": planned_at.isoformat(),
        "safety": {
            "read_only": True,
            "source_files_opened_read_only": bool(operation_records),
            "write_probe_performed": False,
            "directories_created": False,
            "journal_written": False,
            "files_copied": False,
            "files_moved": False,
            "source_files_removed": False,
            "database_rows_modified": False,
            "quarantine_performed": False,
            "rollback_performed": False,
            "deletion_performed": False,
            "execution_blockers": list(_EXECUTION_BLOCKERS),
            "execution_enabled": False,
            "execution_ready": False,
        },
    }


def _inspect_file_operation(
    request: DataDeletionRequest,
    plan: DataDeletionDryRunPlan,
    operation: dict[str, Any],
    *,
    replay_root: Path,
    quarantine_root: Path,
    file_hasher: FileHasher,
) -> dict[str, Any]:
    sequence = _positive_int(operation.get("sequence"), "sequence")
    record_id = _positive_int(operation.get("record_id"), "record_id")
    if operation.get("action") != "quarantine_file_planned":
        raise DataDeletionQuarantinePlannerError("file operation action is unsupported")
    if operation.get("source_table") != "replay_artifacts":
        raise DataDeletionQuarantinePlannerError("file operation source table is unsupported")
    if operation.get("storage_root") != "PUBG_REPLAY_DATA_DIR":
        raise DataDeletionQuarantinePlannerError("file operation storage root is unsupported")
    if operation.get("ownership") != "player_artifact":
        raise DataDeletionQuarantinePlannerError("file operation ownership is unsupported")
    if operation.get("verification_status") not in _ALLOWED_VERIFICATION_STATUSES:
        raise DataDeletionQuarantinePlannerError("file operation verification status is unsafe")
    if operation.get("mutation_enabled") is not False:
        raise DataDeletionQuarantinePlannerError("file operation unexpectedly enables mutation")
    relative = _safe_relative_path(str(operation.get("relative_path") or ""))
    source = (replay_root / Path(*relative.parts)).resolve(strict=False)
    if not _is_within(source, replay_root):
        raise DataDeletionQuarantinePlannerError("source file escaped replay storage")
    if source.is_symlink() or not source.is_file():
        raise DataDeletionQuarantinePlannerError("source file is missing or symbolic")
    before = _file_identity(source)
    declared_size = _nonnegative_int(
        operation.get("declared_size_bytes"),
        "declared_size_bytes",
    )
    expected_sha256 = _fingerprint(operation.get("sha256"), "source SHA-256")
    observed_sha256 = _fingerprint(file_hasher(source), "observed source SHA-256")
    after = _file_identity(source)
    if before != after:
        raise DataDeletionQuarantinePlannerError(
            "source file identity changed during read-only hashing"
        )
    if before[0] != declared_size:
        raise DataDeletionQuarantinePlannerError("source file size differs from dry-run plan")
    if not hmac.compare_digest(observed_sha256, expected_sha256):
        raise DataDeletionQuarantinePlannerError("source file SHA-256 differs from dry-run plan")
    target_relative = PurePosixPath(
        "requests",
        str(request.id),
        "plans",
        str(plan.id),
        "items",
        f"{sequence:06d}-{record_id}",
        *relative.parts,
    )
    target = (
        quarantine_root / Path(*target_relative.parts)
    ).resolve(strict=False)
    if not _is_within(target, quarantine_root):
        raise DataDeletionQuarantinePlannerError("target file escaped quarantine storage")
    symlink_ancestor = _existing_symlink_ancestor(target.parent, quarantine_root)
    if symlink_ancestor is not None:
        raise DataDeletionQuarantinePlannerError(
            f"target parent contains a symbolic link: {symlink_ancestor}"
        )
    target_exists = os.path.lexists(str(target))
    return {
        "sequence": sequence,
        "record_id": record_id,
        "artifact_type": _required_text(
            operation.get("artifact_type"),
            "artifact_type",
            64,
        ),
        "match_id": _required_text(operation.get("match_id"), "match_id", 128),
        "source_root": "PUBG_REPLAY_DATA_DIR",
        "source_relative_path": relative.as_posix(),
        "source_path": str(source),
        "source_identity": {
            "size_bytes": before[0],
            "mtime_ns": before[1],
            "device_id": before[2],
            "inode": before[3],
        },
        "declared_size_bytes": declared_size,
        "sha256": expected_sha256,
        "target_relative_path": target_relative.as_posix(),
        "target_path": str(target),
        "target_exists": target_exists,
        "future_action": "copy_verify_then_remove_source",
        "mutation_enabled": False,
    }


def _postcondition_contract(
    request: DataDeletionRequest,
    plan: DataDeletionDryRunPlan,
    operations: list[dict[str, Any]],
    quarantine_root: Path,
    protected_baseline: str,
) -> dict[str, Any]:
    return {
        "request_id": request.id,
        "dry_run_plan_id": plan.id,
        "quarantine_root": str(quarantine_root),
        "item_checks": [
            {
                "sequence": item["sequence"],
                "record_id": item["record_id"],
                "source_path_expected_absent": item["source_path"],
                "target_path_expected_regular_file": item["target_path"],
                "target_size_bytes": item["declared_size_bytes"],
                "target_sha256": item["sha256"],
            }
            for item in operations
        ],
        "aggregate_checks": [
            "every planned target is a regular non-symlink file with matching size and SHA-256",
            "every corresponding source path is absent only after target verification",
            "the request/plan quarantine subtree contains no undeclared entries",
            "protected replay and raw file baselines remain unchanged",
            "shared match rows and raw payload rows remain unchanged",
            "all deletion workflow audit rows remain present",
        ],
        "protected_data_baseline_fingerprint_sha256": protected_baseline,
        "mutation_enabled": False,
    }


def _rollback_contract(operations: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "strategy": "verify-target-then-restore-source-with-no-overwrite",
        "item_actions": [
            {
                "sequence": item["sequence"],
                "record_id": item["record_id"],
                "restore_from": item["target_path"],
                "restore_to": item["source_path"],
                "expected_size_bytes": item["declared_size_bytes"],
                "expected_sha256": item["sha256"],
                "source_must_be_absent_before_restore": True,
                "target_removed_only_after_source_readback": True,
            }
            for item in reversed(operations)
        ],
        "overwrite_existing_source": False,
        "mutation_enabled": False,
        "independently_rehearsed": False,
    }


def _crash_recovery_contract(destination: dict[str, Any]) -> dict[str, Any]:
    return {
        "journal_relative_path": destination["journal_relative_path"],
        "journal_states": [
            "planned",
            "copying",
            "copied_and_verified",
            "source_removal_committing",
            "committed",
            "rollback_required",
            "rolled_back",
        ],
        "journal_update_strategy": "write-temp-fsync-atomic-replace-parent-fsync",
        "restart_policy": (
            "never guess; rehash both source and target and resume or rollback from the last durable state"
        ),
        "ambiguous_state_policy": "block and require administrator review",
        "mutation_enabled": False,
        "independently_rehearsed": False,
    }


def _plan_file_operations(plan: DataDeletionDryRunPlan) -> list[dict[str, Any]]:
    value = plan.plan_json.get("file_operations")
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise DataDeletionQuarantinePlannerError(
            "dry-run plan file operations are invalid."
        )
    ordered = sorted(value, key=lambda item: _positive_int(item.get("sequence"), "sequence"))
    observed = [_positive_int(item.get("sequence"), "sequence") for item in ordered]
    if observed != list(range(1, len(ordered) + 1)):
        raise DataDeletionQuarantinePlannerError(
            "dry-run plan file operation sequences are not contiguous."
        )
    return [dict(item) for item in ordered]


def _planning_run_from_row(row: dict[str, Any]) -> DataDeletionQuarantinePlanningRun:
    status = str(row.get("result_status") or "")
    if status not in {"passed", "blocked"}:
        raise DataDeletionQuarantinePlannerError(
            f"unsupported quarantine planning status: {status}."
        )
    result = _json_object(row.get("result_json"), "result_json")
    result_fingerprint = _fingerprint(
        row.get("result_fingerprint_sha256"),
        "result fingerprint",
    )
    if not hmac.compare_digest(result_fingerprint, _canonical_sha256(result)):
        raise DataDeletionQuarantinePlannerError(
            "quarantine planning result fingerprint is invalid."
        )
    metrics = _result_metrics(result)
    evidence_id = (
        _positive_int(row["capacity_evidence_id"], "capacity_evidence_id")
        if row.get("capacity_evidence_id") is not None
        else None
    )
    if (status == "passed") != (evidence_id is not None):
        raise DataDeletionQuarantinePlannerError(
            "quarantine capacity evidence binding is invalid."
        )
    binding_checks = (
        result.get("planning_status") == status,
        int(result.get("request_id") or 0) == int(row["request_id"]),
        int(result.get("dry_run_plan_id") or 0) == int(row["dry_run_plan_id"]),
        result.get("plan_fingerprint_sha256") == str(row["plan_fingerprint_sha256"]),
        result.get("destination_contract_fingerprint_sha256")
        == str(row["destination_contract_fingerprint_sha256"]),
        result.get("quarantine_root") == str(row["quarantine_root"]),
    )
    if not all(binding_checks):
        raise DataDeletionQuarantinePlannerError(
            "quarantine planning audit bindings are invalid."
        )
    row_metric_fields = (
        "candidate_file_count",
        "candidate_file_bytes",
        "safety_reserve_bytes",
        "required_free_bytes",
        "source_verified_file_count",
        "source_verified_bytes",
        "target_conflict_count",
    )
    if any(
        metrics[field] != _nonnegative_int(row.get(field), field)
        for field in row_metric_fields
    ):
        raise DataDeletionQuarantinePlannerError(
            "quarantine planning metric bindings are invalid."
        )
    observed_row = row.get("observed_free_bytes")
    if metrics["observed_free_bytes"] != (
        _nonnegative_int(observed_row, "observed_free_bytes")
        if observed_row is not None
        else None
    ):
        raise DataDeletionQuarantinePlannerError(
            "quarantine planning free-space binding is invalid."
        )
    return DataDeletionQuarantinePlanningRun(
        id=_positive_int(row["id"], "id"),
        request_id=_positive_int(row["request_id"], "request_id"),
        dry_run_plan_id=_positive_int(row["dry_run_plan_id"], "dry_run_plan_id"),
        contract_version=str(row["contract_version"]),
        plan_fingerprint_sha256=_fingerprint(
            row["plan_fingerprint_sha256"],
            "plan fingerprint",
        ),
        destination_contract_fingerprint_sha256=_fingerprint(
            row["destination_contract_fingerprint_sha256"],
            "destination fingerprint",
        ),
        quarantine_root=str(row["quarantine_root"]),
        result_fingerprint_sha256=result_fingerprint,
        result_status=status,
        result_json=result,
        candidate_file_count=metrics["candidate_file_count"],
        candidate_file_bytes=metrics["candidate_file_bytes"],
        safety_reserve_bytes=metrics["safety_reserve_bytes"],
        required_free_bytes=metrics["required_free_bytes"],
        observed_free_bytes=metrics["observed_free_bytes"],
        source_verified_file_count=metrics["source_verified_file_count"],
        source_verified_bytes=metrics["source_verified_bytes"],
        target_conflict_count=metrics["target_conflict_count"],
        check_count=_nonnegative_int(row["check_count"], "check_count"),
        passed_check_count=_nonnegative_int(
            row["passed_check_count"],
            "passed_check_count",
        ),
        blocker_count=_nonnegative_int(row["blocker_count"], "blocker_count"),
        capacity_evidence_id=evidence_id,
        planned_by=_required_text(row["planned_by"], "planned_by", 191),
        planning_note=_optional_text(row.get("planning_note"), "planning_note", 1000),
        planned_at_kst=_datetime_value(row["planned_at_kst"], "planned_at_kst"),
    )


def _result_metrics(result: dict[str, Any]) -> dict[str, int | None]:
    value = result.get("metrics")
    if not isinstance(value, dict):
        raise DataDeletionQuarantinePlannerError(
            "quarantine planning result metrics are missing."
        )
    observed = value.get("observed_free_bytes")
    return {
        "candidate_file_count": _nonnegative_int(
            value.get("candidate_file_count"),
            "candidate_file_count",
        ),
        "candidate_file_bytes": _nonnegative_int(
            value.get("candidate_file_bytes"),
            "candidate_file_bytes",
        ),
        "safety_reserve_bytes": _nonnegative_int(
            value.get("safety_reserve_bytes"),
            "safety_reserve_bytes",
        ),
        "required_free_bytes": _nonnegative_int(
            value.get("required_free_bytes"),
            "required_free_bytes",
        ),
        "observed_free_bytes": (
            _nonnegative_int(observed, "observed_free_bytes")
            if observed is not None
            else None
        ),
        "source_verified_file_count": _nonnegative_int(
            value.get("source_verified_file_count"),
            "source_verified_file_count",
        ),
        "source_verified_bytes": _nonnegative_int(
            value.get("source_verified_bytes"),
            "source_verified_bytes",
        ),
        "target_conflict_count": _nonnegative_int(
            value.get("target_conflict_count"),
            "target_conflict_count",
        ),
    }


def _plan_metric(plan: DataDeletionDryRunPlan, key: str) -> int:
    metrics = plan.plan_json.get("metrics")
    if not isinstance(metrics, dict):
        raise DataDeletionQuarantinePlannerError("dry-run plan metrics are missing.")
    return _nonnegative_int(metrics.get(key), key)


def _safe_relative_path(value: str) -> PurePosixPath:
    if not value or "\\" in value or ":" in value or "\x00" in value:
        raise DataDeletionQuarantinePlannerError("file relative path is unsafe")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise DataDeletionQuarantinePlannerError("file relative path is unsafe")
    return path


def _existing_symlink_ancestor(path: Path, root: Path) -> Path | None:
    cursor = path
    while cursor != root:
        if not _is_within(cursor, root):
            raise DataDeletionQuarantinePlannerError(
                "target ancestor escaped quarantine storage"
            )
        if cursor.is_symlink():
            return cursor
        cursor = cursor.parent
    return root if root.is_symlink() else None


def _file_identity(path: Path) -> tuple[int, int, int, int]:
    try:
        stat = path.stat()
    except OSError as exc:
        raise DataDeletionQuarantinePlannerError(
            f"source file stat failed: {path}: {exc}"
        ) from exc
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
        raise DataDeletionQuarantinePlannerError(
            f"source file hashing failed: {path}: {exc}"
        ) from exc
    return digest.hexdigest()


def _free_bytes(path: Path) -> int:
    return int(shutil.disk_usage(path).free)


def _absolute_input_path(path: str | Path) -> Path:
    value = Path(path).expanduser()
    if not value.is_absolute():
        value = Path.cwd() / value
    return value


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


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


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _safe_error_message(exc: Exception) -> str:
    return (str(exc).strip() or type(exc).__name__)[:2000]


def _json_object(value: Any, label: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise DataDeletionQuarantinePlannerError(f"invalid {label}.") from exc
        if isinstance(parsed, dict):
            return parsed
    raise DataDeletionQuarantinePlannerError(f"{label} must be a JSON object.")


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


def _fingerprint(value: Any, label: str) -> str:
    text = str(value or "").strip().lower()
    if not _FINGERPRINT_PATTERN.fullmatch(text):
        raise DataDeletionQuarantinePlannerError(f"{label} must be a SHA-256 value.")
    return text


def _positive_int(value: Any, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise DataDeletionQuarantinePlannerError(f"{label} must be an integer.") from exc
    if parsed <= 0:
        raise DataDeletionQuarantinePlannerError(f"{label} must be positive.")
    return parsed


def _nonnegative_int(value: Any, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise DataDeletionQuarantinePlannerError(f"{label} must be an integer.") from exc
    if parsed < 0:
        raise DataDeletionQuarantinePlannerError(f"{label} must not be negative.")
    return parsed


def _required_text(value: Any, label: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum:
        raise DataDeletionQuarantinePlannerError(
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
        raise DataDeletionQuarantinePlannerError(
            f"{label} must contain at most {maximum} characters."
        )
    return text


def _datetime_value(value: Any, label: str) -> datetime:
    if isinstance(value, datetime):
        return to_kst(value)
    try:
        return to_kst(datetime.fromisoformat(str(value)))
    except (TypeError, ValueError) as exc:
        raise DataDeletionQuarantinePlannerError(f"invalid {label}.") from exc


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
