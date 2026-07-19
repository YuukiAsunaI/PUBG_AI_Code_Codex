from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any
import ctypes
import hashlib
import hmac
import json
import os
import shutil
import uuid

from pubg_ai.data_deletion_backup import DataDeletionBackupService
from pubg_ai.data_deletion_dry_run import DataDeletionDryRunPlan
from pubg_ai.data_deletion_quarantine_planner import (
    QUARANTINE_PLANNER_CONTRACT_VERSION,
    DataDeletionQuarantinePlannerService,
    DataDeletionQuarantinePlanningRun,
    build_quarantine_destination_contract,
    fingerprint_quarantine_destination_contract,
)
from pubg_ai.data_deletion_requests import DataDeletionRequest
from pubg_ai.storage_contract import (
    canonical_storage_path,
    inspect_directory_read_only,
    overlapping_named_root,
)
from pubg_ai.time_utils import now_kst, to_kst


QUARANTINE_REHEARSAL_CONTRACT_VERSION = "deletion-quarantine-rehearsal-v1"
QUARANTINE_REHEARSAL_CONFIRMATION_PREFIX = "RUN ISOLATED QUARANTINE REHEARSAL"
QUARANTINE_FIXTURE_FORMAT_VERSION = "deletion-quarantine-fixture-v1"

_SCRATCH_PREFIX = ".pubg-ai-quarantine-rehearsal-"
_MARKER_NAME = ".pubg-ai-owned-scratch.json"
_JOURNAL_NAME = ".quarantine-transaction.json"
_COPY_CHUNK_BYTES = 1024 * 1024
_FINGERPRINT_HEX_LENGTH = 64
_REQUIRED_JOURNAL_STATES = {
    "planned",
    "copying",
    "copied_and_verified",
    "source_removal_committing",
    "committed",
    "rollback_required",
    "rolled_back",
}


class DataDeletionQuarantineRehearsalError(RuntimeError):
    """Raised when an isolated quarantine rehearsal cannot run safely."""


class _RehearsalBlocked(RuntimeError):
    pass


@dataclass(frozen=True)
class DataDeletionQuarantineRehearsalRun:
    id: int
    request_id: int
    dry_run_plan_id: int
    quarantine_planning_run_id: int
    contract_version: str
    plan_fingerprint_sha256: str
    destination_contract_fingerprint_sha256: str
    planning_result_fingerprint_sha256: str
    result_fingerprint_sha256: str
    result_status: str
    result_json: dict[str, Any]
    scratch_directory: str
    scratch_directory_removed: bool
    fixture_file_count: int
    fixture_bytes: int
    normal_committed_count: int
    normal_rolled_back_count: int
    recovery_case_count: int
    recovered_case_count: int
    ambiguous_case_count: int
    ambiguous_case_blocked_count: int
    journal_transition_count: int
    check_count: int
    passed_check_count: int
    blocker_count: int
    run_by: str
    rehearsal_note: str | None
    run_at_kst: datetime

    def to_summary_record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "request_id": self.request_id,
            "dry_run_plan_id": self.dry_run_plan_id,
            "quarantine_planning_run_id": self.quarantine_planning_run_id,
            "contract_version": self.contract_version,
            "plan_fingerprint_sha256": self.plan_fingerprint_sha256,
            "destination_contract_fingerprint_sha256": (
                self.destination_contract_fingerprint_sha256
            ),
            "planning_result_fingerprint_sha256": (
                self.planning_result_fingerprint_sha256
            ),
            "result_fingerprint_sha256": self.result_fingerprint_sha256,
            "result_status": self.result_status,
            "scratch_directory": self.scratch_directory,
            "scratch_directory_removed": self.scratch_directory_removed,
            "fixture_file_count": self.fixture_file_count,
            "fixture_bytes": self.fixture_bytes,
            "normal_committed_count": self.normal_committed_count,
            "normal_rolled_back_count": self.normal_rolled_back_count,
            "recovery_case_count": self.recovery_case_count,
            "recovered_case_count": self.recovered_case_count,
            "ambiguous_case_count": self.ambiguous_case_count,
            "ambiguous_case_blocked_count": self.ambiguous_case_blocked_count,
            "journal_transition_count": self.journal_transition_count,
            "check_count": self.check_count,
            "passed_check_count": self.passed_check_count,
            "blocker_count": self.blocker_count,
            "run_by": self.run_by,
            "rehearsal_note": self.rehearsal_note,
            "run_at_kst": to_kst(self.run_at_kst).isoformat(),
            "immutable": True,
            "synthetic_fixtures_only": True,
            "production_source_files_opened": False,
            "production_source_files_modified": False,
            "production_quarantine_performed": False,
            "database_source_rows_modified": False,
            "deletion_performed": False,
            "execution_enabled": False,
            "execution_ready": False,
        }

    def to_record(self) -> dict[str, Any]:
        return {**self.to_summary_record(), "result_json": self.result_json}


class DataDeletionQuarantineRehearsalService:
    def __init__(
        self,
        connection: Any,
        *,
        backup_service: DataDeletionBackupService,
        planner_service: DataDeletionQuarantinePlannerService,
        quarantine_root: Path,
        raw_data_dir: Path,
        replay_data_dir: Path,
        backup_root: Path,
    ) -> None:
        self.connection = connection
        self.backup_service = backup_service
        self.planner_service = planner_service
        self.quarantine_root_input = _absolute_input_path(quarantine_root)
        self.quarantine_root = canonical_storage_path(self.quarantine_root_input)
        self.raw_data_dir = canonical_storage_path(raw_data_dir)
        self.replay_data_dir = canonical_storage_path(replay_data_dir)
        self.backup_root = canonical_storage_path(backup_root)

    def rehearsal_state(self, request: DataDeletionRequest) -> dict[str, Any]:
        plans = self.backup_service.dry_run_service.list_plans(request.id, limit=1)
        plan = plans[0] if plans else None
        root_status = inspect_directory_read_only(self.quarantine_root_input)
        blockers = self._root_blockers(request, plan, root_status)
        planning_history = (
            self.planner_service.list_runs(plan.id, limit=50)
            if plan is not None
            else []
        )
        latest_planning = planning_history[0] if planning_history else None
        if plan is not None:
            blockers.extend(self._planning_blockers(plan, latest_planning))
            history = self.list_runs(plan.id, limit=50)
        else:
            history = []
        candidate = (
            latest_planning
            if latest_planning is not None
            and latest_planning.result_status == "passed"
            and not self._planning_blockers(plan, latest_planning)
            else None
        ) if plan is not None else None
        confirmation = (
            expected_quarantine_rehearsal_confirmation(
                request.id,
                plan.id,
                candidate.id,
                candidate.result_fingerprint_sha256,
                candidate.destination_contract_fingerprint_sha256,
            )
            if plan is not None and candidate is not None
            else None
        )
        return {
            "request_id": request.id,
            "request_status": request.status,
            "contract_version": QUARANTINE_REHEARSAL_CONTRACT_VERSION,
            "fixture_format_version": QUARANTINE_FIXTURE_FORMAT_VERSION,
            "quarantine_root": str(self.quarantine_root),
            "quarantine_root_status": root_status,
            "latest_plan_id": plan.id if plan is not None else None,
            "latest_planning_run_id": (
                latest_planning.id if latest_planning is not None else None
            ),
            "planning_candidate": (
                {
                    **candidate.to_summary_record(),
                    "confirmation_text": confirmation,
                }
                if candidate is not None
                else None
            ),
            "latest_quarantine_rehearsal": (
                history[0].to_record() if history else None
            ),
            "quarantine_rehearsal_history": [
                item.to_summary_record() for item in history
            ],
            "rehearsal_allowed": not _deduplicate(blockers),
            "rehearsal_blockers": _deduplicate(blockers),
            "scratch_strategy": "random-owned-directory-under-quarantine-root",
            "normal_flow_rehearsed": True,
            "rollback_rehearsed": True,
            "crash_recovery_states_rehearsed": [
                "copying",
                "copied_and_verified",
                "source_removal_committing",
                "committed",
                "ambiguous_state",
            ],
            "synthetic_fixtures_only": True,
            "production_source_files_opened": False,
            "production_source_files_modified": False,
            "production_quarantine_performed": False,
            "database_source_rows_modified": False,
            "deletion_performed": False,
            "execution_blockers": [
                "quarantine_executor_not_implemented",
                "database_deletion_not_implemented",
                "executor_not_implemented",
            ],
            "execution_enabled": False,
            "execution_ready": False,
        }

    def run(
        self,
        request: DataDeletionRequest,
        *,
        quarantine_planning_run_id: int,
        confirmation_text: str,
        actor_id: str,
        note: str | None = None,
        reference_kst: datetime | None = None,
    ) -> DataDeletionQuarantineRehearsalRun:
        planning_run_id = _positive_int(
            quarantine_planning_run_id,
            "quarantine_planning_run_id",
        )
        actor_id = _required_text(actor_id, "actor_id", 191)
        note = _optional_text(note, "note", 1000)
        if request.status != "approved":
            raise DataDeletionQuarantineRehearsalError(
                "request must remain approved."
            )
        planning = self.planner_service.get_run(planning_run_id)
        if planning.request_id != request.id:
            raise DataDeletionQuarantineRehearsalError(
                "quarantine planning run belongs to another deletion request."
            )
        plan = self.backup_service.require_latest_plan(
            request,
            planning.dry_run_plan_id,
        )
        latest = self.planner_service.list_runs(plan.id, limit=1)
        if not latest or latest[0].id != planning.id:
            raise DataDeletionQuarantineRehearsalError(
                "selected quarantine planning run is not the latest run."
            )
        planning_blockers = self._planning_blockers(plan, planning)
        if planning_blockers:
            raise DataDeletionQuarantineRehearsalError(
                "quarantine rehearsal planning input is blocked: "
                + "; ".join(planning_blockers)
            )
        root_status = inspect_directory_read_only(self.quarantine_root_input)
        root_blockers = self._root_blockers(request, plan, root_status)
        if root_blockers:
            raise DataDeletionQuarantineRehearsalError(
                "quarantine rehearsal root is blocked: "
                + "; ".join(root_blockers)
            )
        expected_confirmation = expected_quarantine_rehearsal_confirmation(
            request.id,
            plan.id,
            planning.id,
            planning.result_fingerprint_sha256,
            planning.destination_contract_fingerprint_sha256,
        )
        supplied_confirmation = _required_text(
            confirmation_text,
            "confirmation_text",
            500,
        )
        if not hmac.compare_digest(supplied_confirmation, expected_confirmation):
            raise DataDeletionQuarantineRehearsalError(
                "quarantine rehearsal confirmation does not match the latest planning run."
            )

        run_at = to_kst(reference_kst or now_kst())
        runner = _IsolatedQuarantineRunner(
            request=request,
            plan=plan,
            planning=planning,
            quarantine_root=self.quarantine_root,
            run_at_kst=run_at,
        )
        result = runner.run()
        result_fingerprint = _canonical_sha256(result)
        return self._record_run(
            request,
            plan,
            planning,
            result=result,
            result_fingerprint=result_fingerprint,
            actor_id=actor_id,
            note=note,
            run_at_kst=run_at,
        )

    def get_run(self, rehearsal_run_id: int) -> DataDeletionQuarantineRehearsalRun:
        rehearsal_run_id = _positive_int(rehearsal_run_id, "rehearsal_run_id")
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM data_deletion_quarantine_rehearsal_runs WHERE id = %s",
                (rehearsal_run_id,),
            )
            row = cursor.fetchone()
        if not row:
            raise DataDeletionQuarantineRehearsalError(
                f"quarantine rehearsal run {rehearsal_run_id} was not found."
            )
        return _rehearsal_run_from_row(row)

    def list_runs(
        self,
        dry_run_plan_id: int,
        *,
        limit: int = 50,
    ) -> list[DataDeletionQuarantineRehearsalRun]:
        dry_run_plan_id = _positive_int(dry_run_plan_id, "dry_run_plan_id")
        if not 1 <= int(limit) <= 100:
            raise DataDeletionQuarantineRehearsalError(
                "quarantine rehearsal history limit must be between 1 and 100."
            )
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM data_deletion_quarantine_rehearsal_runs
                WHERE dry_run_plan_id = %s
                ORDER BY run_at_kst DESC, id DESC
                LIMIT %s
                """,
                (dry_run_plan_id, int(limit)),
            )
            rows = cursor.fetchall()
        return [_rehearsal_run_from_row(row) for row in rows]

    def _root_blockers(
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

    def _planning_blockers(
        self,
        plan: DataDeletionDryRunPlan,
        planning: DataDeletionQuarantinePlanningRun | None,
    ) -> list[str]:
        if planning is None:
            return ["latest quarantine planning run is required"]
        blockers: list[str] = []
        if planning.contract_version != QUARANTINE_PLANNER_CONTRACT_VERSION:
            blockers.append("latest quarantine planning contract is unsupported")
        if planning.result_status != "passed":
            blockers.append("latest quarantine planning run must have passed")
        if planning.capacity_evidence_id is None:
            blockers.append("latest quarantine planning run lacks capacity evidence")
        if planning.dry_run_plan_id != plan.id:
            blockers.append("quarantine planning run belongs to another dry-run plan")
        if not hmac.compare_digest(
            planning.plan_fingerprint_sha256,
            plan.plan_fingerprint_sha256,
        ):
            blockers.append("quarantine planning plan fingerprint is stale")
        destination = build_quarantine_destination_contract(plan, self.quarantine_root)
        destination_fingerprint = fingerprint_quarantine_destination_contract(
            destination
        )
        if not hmac.compare_digest(
            planning.destination_contract_fingerprint_sha256,
            destination_fingerprint,
        ):
            blockers.append("quarantine planning destination contract is stale")
        result = planning.result_json
        if result.get("planning_status") != "passed":
            blockers.append("quarantine planning result payload is not passed")
        if result.get("safety", {}).get("execution_enabled") is not False:
            blockers.append("quarantine planning payload unexpectedly enables execution")
        return _deduplicate(blockers)

    def _record_run(
        self,
        request: DataDeletionRequest,
        plan: DataDeletionDryRunPlan,
        planning: DataDeletionQuarantinePlanningRun,
        *,
        result: dict[str, Any],
        result_fingerprint: str,
        actor_id: str,
        note: str | None,
        run_at_kst: datetime,
    ) -> DataDeletionQuarantineRehearsalRun:
        metrics = _result_metrics(result)
        checks = _checks(result)
        blockers = _blockers(result)
        status = str(result.get("result_status") or "")
        if status not in {"passed", "blocked"}:
            raise DataDeletionQuarantineRehearsalError(
                "quarantine rehearsal result status is invalid."
            )
        timestamp = to_kst(run_at_kst).replace(tzinfo=None)
        _begin(self.connection)
        try:
            with self.connection.cursor() as cursor:
                self.backup_service._assert_latest_plan_locked(cursor, request, plan)
                cursor.execute(
                    """
                    SELECT id, contract_version, plan_fingerprint_sha256,
                           destination_contract_fingerprint_sha256,
                           result_fingerprint_sha256, result_status,
                           capacity_evidence_id
                    FROM data_deletion_quarantine_planning_runs
                    WHERE dry_run_plan_id = %s
                    ORDER BY planned_at_kst DESC, id DESC
                    LIMIT 1
                    FOR UPDATE
                    """,
                    (plan.id,),
                )
                locked = cursor.fetchone()
                _assert_locked_planning_run(locked, planning)
                cursor.execute(
                    """
                    INSERT INTO data_deletion_quarantine_rehearsal_runs (
                        request_id,
                        dry_run_plan_id,
                        quarantine_planning_run_id,
                        contract_version,
                        plan_fingerprint_sha256,
                        destination_contract_fingerprint_sha256,
                        planning_result_fingerprint_sha256,
                        result_fingerprint_sha256,
                        result_status,
                        result_json,
                        scratch_directory,
                        scratch_directory_removed,
                        fixture_file_count,
                        fixture_bytes,
                        normal_committed_count,
                        normal_rolled_back_count,
                        recovery_case_count,
                        recovered_case_count,
                        ambiguous_case_count,
                        ambiguous_case_blocked_count,
                        journal_transition_count,
                        check_count,
                        passed_check_count,
                        blocker_count,
                        run_by,
                        rehearsal_note,
                        run_at_kst
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                              %s, %s, %s, %s, %s, %s, %s, %s, %s,
                              %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        request.id,
                        plan.id,
                        planning.id,
                        QUARANTINE_REHEARSAL_CONTRACT_VERSION,
                        plan.plan_fingerprint_sha256,
                        planning.destination_contract_fingerprint_sha256,
                        planning.result_fingerprint_sha256,
                        result_fingerprint,
                        status,
                        _json_dump(result),
                        str(result.get("scratch_directory") or ""),
                        bool(result.get("safety", {}).get("scratch_directory_removed")),
                        metrics["fixture_file_count"],
                        metrics["fixture_bytes"],
                        metrics["normal_committed_count"],
                        metrics["normal_rolled_back_count"],
                        metrics["recovery_case_count"],
                        metrics["recovered_case_count"],
                        metrics["ambiguous_case_count"],
                        metrics["ambiguous_case_blocked_count"],
                        metrics["journal_transition_count"],
                        len(checks),
                        sum(item.get("status") == "passed" for item in checks),
                        len(blockers),
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
        return DataDeletionQuarantineRehearsalRun(
            id=run_id,
            request_id=request.id,
            dry_run_plan_id=plan.id,
            quarantine_planning_run_id=planning.id,
            contract_version=QUARANTINE_REHEARSAL_CONTRACT_VERSION,
            plan_fingerprint_sha256=plan.plan_fingerprint_sha256,
            destination_contract_fingerprint_sha256=(
                planning.destination_contract_fingerprint_sha256
            ),
            planning_result_fingerprint_sha256=(
                planning.result_fingerprint_sha256
            ),
            result_fingerprint_sha256=result_fingerprint,
            result_status=status,
            result_json=result,
            scratch_directory=str(result.get("scratch_directory") or ""),
            scratch_directory_removed=bool(
                result.get("safety", {}).get("scratch_directory_removed")
            ),
            fixture_file_count=metrics["fixture_file_count"],
            fixture_bytes=metrics["fixture_bytes"],
            normal_committed_count=metrics["normal_committed_count"],
            normal_rolled_back_count=metrics["normal_rolled_back_count"],
            recovery_case_count=metrics["recovery_case_count"],
            recovered_case_count=metrics["recovered_case_count"],
            ambiguous_case_count=metrics["ambiguous_case_count"],
            ambiguous_case_blocked_count=metrics[
                "ambiguous_case_blocked_count"
            ],
            journal_transition_count=metrics["journal_transition_count"],
            check_count=len(checks),
            passed_check_count=sum(
                item.get("status") == "passed" for item in checks
            ),
            blocker_count=len(blockers),
            run_by=actor_id,
            rehearsal_note=note,
            run_at_kst=run_at_kst,
        )


class _IsolatedQuarantineRunner:
    def __init__(
        self,
        *,
        request: DataDeletionRequest,
        plan: DataDeletionDryRunPlan,
        planning: DataDeletionQuarantinePlanningRun,
        quarantine_root: Path,
        run_at_kst: datetime,
    ) -> None:
        self.request = request
        self.plan = plan
        self.planning = planning
        self.quarantine_root = quarantine_root
        self.run_at_kst = run_at_kst
        self.token = uuid.uuid4().hex
        self.scratch = quarantine_root / f"{_SCRATCH_PREFIX}{self.token}"
        self.checks: list[dict[str, Any]] = []
        self.blockers: list[str] = []
        self.metrics = _empty_metrics()
        self.scratch_created = False
        self.scratch_removed = False
        self.journal_strategy = (
            "write-temp-fsync-movefileex-write-through"
            if os.name == "nt"
            else "write-temp-fsync-atomic-replace-parent-fsync"
        )

    def run(self) -> dict[str, Any]:
        specs: list[dict[str, Any]] = []
        try:
            specs = _fixture_specs(self.planning)
            self._record_contract_check(specs)
            self._create_scratch()
            self._run_normal_and_rollback(specs)
            self._run_recovery_cases(specs[0])
            self.checks.append(
                _check(
                    "production_source_isolation",
                    True,
                    "only deterministic synthetic fixtures were opened or modified",
                    {
                        "production_source_files_opened": False,
                        "production_source_files_modified": False,
                        "production_target_paths_used": False,
                    },
                )
            )
        except Exception as exc:
            self.blockers.append(_safe_error_message(exc))
            self.checks.append(
                _check(
                    "rehearsal_execution",
                    False,
                    "isolated fixture rehearsal completes without an unexpected error",
                    {"error": _safe_error_message(exc)},
                )
            )
        finally:
            self._cleanup()

        if not self.scratch_removed:
            self.blockers.append("owned scratch directory cleanup did not complete")
        self.blockers.extend(
            str(item["key"])
            for item in self.checks
            if item.get("status") == "blocked"
        )
        blockers = _deduplicate(self.blockers)
        status = "passed" if not blockers else "blocked"
        return {
            "contract_version": QUARANTINE_REHEARSAL_CONTRACT_VERSION,
            "fixture_format_version": QUARANTINE_FIXTURE_FORMAT_VERSION,
            "request_id": self.request.id,
            "dry_run_plan_id": self.plan.id,
            "quarantine_planning_run_id": self.planning.id,
            "plan_fingerprint_sha256": self.plan.plan_fingerprint_sha256,
            "destination_contract_fingerprint_sha256": (
                self.planning.destination_contract_fingerprint_sha256
            ),
            "planning_result_fingerprint_sha256": (
                self.planning.result_fingerprint_sha256
            ),
            "quarantine_root": str(self.quarantine_root),
            "scratch_directory": str(self.scratch),
            "journal_strategy": self.journal_strategy,
            "checks": self.checks,
            "metrics": dict(self.metrics),
            "blockers": blockers,
            "result_status": status,
            "run_at_kst": self.run_at_kst.isoformat(),
            "safety": {
                "synthetic_fixtures_only": True,
                "scratch_directory_created": self.scratch_created,
                "scratch_directory_removed": self.scratch_removed,
                "journal_written": self.metrics["journal_transition_count"] > 0,
                "fixture_files_created": self.metrics["fixture_file_count"] > 0,
                "fixture_files_copied": self.metrics["normal_committed_count"] > 0,
                "fixture_files_removed": self.metrics["normal_rolled_back_count"] > 0,
                "production_source_files_opened": False,
                "production_source_files_modified": False,
                "production_quarantine_performed": False,
                "database_source_rows_modified": False,
                "deletion_performed": False,
                "execution_enabled": False,
                "execution_ready": False,
            },
        }

    def _record_contract_check(self, specs: list[dict[str, Any]]) -> None:
        result = self.planning.result_json
        crash = result.get("crash_recovery_contract") or {}
        rollback = result.get("rollback_contract") or {}
        postcondition = result.get("postcondition_contract") or {}
        destination = result.get("destination_contract") or {}
        states = set(crash.get("journal_states") or [])
        rollback_items = rollback.get("item_actions") or []
        postcondition_items = postcondition.get("item_checks") or []
        contract_error: str | None = None
        try:
            postcondition_bindings = [
                (
                    _positive_int(item.get("sequence"), "postcondition sequence"),
                    _positive_int(item.get("record_id"), "postcondition record_id"),
                    _nonnegative_int(
                        item.get("target_size_bytes"),
                        "postcondition target_size_bytes",
                    ),
                    _fingerprint(item.get("target_sha256"), "postcondition SHA-256"),
                )
                for item in postcondition_items
            ]
            expected_postconditions = [
                (
                    int(spec["sequence"]),
                    int(spec["record_id"]),
                    int(spec["declared_size_bytes"]),
                    str(spec["declared_sha256"]),
                )
                for spec in specs
            ]
            rollback_bindings = [
                (
                    _positive_int(item.get("sequence"), "rollback sequence"),
                    _positive_int(item.get("record_id"), "rollback record_id"),
                    _nonnegative_int(
                        item.get("expected_size_bytes"),
                        "rollback expected_size_bytes",
                    ),
                    _fingerprint(item.get("expected_sha256"), "rollback SHA-256"),
                    item.get("source_must_be_absent_before_restore") is True,
                    item.get("target_removed_only_after_source_readback") is True,
                )
                for item in rollback_items
            ]
            expected_rollback = [
                (
                    int(spec["sequence"]),
                    int(spec["record_id"]),
                    int(spec["declared_size_bytes"]),
                    str(spec["declared_sha256"]),
                    True,
                    True,
                )
                for spec in reversed(specs)
            ]
        except Exception as exc:
            contract_error = _safe_error_message(exc)
            postcondition_bindings = []
            expected_postconditions = []
            rollback_bindings = []
            expected_rollback = []
        valid = bool(
            specs
            and contract_error is None
            and postcondition_bindings == expected_postconditions
            and rollback_bindings == expected_rollback
            and _REQUIRED_JOURNAL_STATES.issubset(states)
            and crash.get("journal_relative_path")
            == destination.get("journal_relative_path")
            and crash.get("journal_update_strategy")
            == "write-temp-fsync-atomic-replace-parent-fsync"
            and crash.get("ambiguous_state_policy")
            == "block and require administrator review"
            and crash.get("mutation_enabled") is False
            and rollback.get("strategy")
            == "verify-target-then-restore-source-with-no-overwrite"
            and rollback.get("overwrite_existing_source") is False
            and rollback.get("mutation_enabled") is False
            and postcondition.get("mutation_enabled") is False
        )
        self.checks.append(
            _check(
                "planning_contract_binding",
                valid,
                "latest passed planning result binds every postcondition, rollback, and recovery item",
                {
                    "fixture_count": len(specs),
                    "postcondition_item_count": len(postcondition_items),
                    "rollback_item_count": len(rollback_items),
                    "journal_states": sorted(states),
                    "declared_journal_strategy": crash.get(
                        "journal_update_strategy"
                    ),
                    "error": contract_error,
                },
            )
        )
        if not valid:
            raise _RehearsalBlocked("planning recovery contract binding is invalid")

    def _create_scratch(self) -> None:
        if os.path.lexists(str(self.scratch)):
            raise _RehearsalBlocked("random rehearsal scratch path already exists")
        self.scratch.mkdir(mode=0o700)
        self.scratch_created = True
        _assert_owned_scratch(self.quarantine_root, self.scratch, self.token)
        marker = {
            "contract_version": QUARANTINE_REHEARSAL_CONTRACT_VERSION,
            "token": self.token,
            "request_id": self.request.id,
            "dry_run_plan_id": self.plan.id,
            "planning_run_id": self.planning.id,
        }
        _write_new_file(
            self.scratch / _MARKER_NAME,
            _canonical_json_bytes(marker),
        )
        self.checks.append(
            _check(
                "scratch_isolation",
                True,
                "random owned scratch directory is a direct child of the quarantine root",
                {"scratch_directory": str(self.scratch)},
            )
        )

    def _run_normal_and_rollback(self, specs: list[dict[str, Any]]) -> None:
        scenario = self.scratch / "normal-flow"
        source_root = scenario / "source"
        target_root = scenario / "target"
        source_root.mkdir(parents=True)
        target_root.mkdir()
        payloads: dict[int, bytes] = {}
        for spec in specs:
            payload = _fixture_payload(self.planning, spec)
            payloads[int(spec["sequence"])] = payload
            _write_new_file(source_root / _fixture_name(spec), payload)
            self.metrics["fixture_file_count"] += 1
            self.metrics["fixture_bytes"] += len(payload)
        baseline = _fixture_tree_fingerprint(source_root, specs)
        journal = scenario / _JOURNAL_NAME
        self._write_journal(journal, "planned", None)
        for spec in specs:
            source = source_root / _fixture_name(spec)
            target = target_root / _fixture_name(spec)
            self._write_journal(journal, "copying", spec)
            _copy_new_verified(source, target, payloads[int(spec["sequence"])])
            self._write_journal(journal, "copied_and_verified", spec)
            self._write_journal(journal, "source_removal_committing", spec)
            source.unlink()
            self.metrics["normal_committed_count"] += 1
        self._write_journal(journal, "committed", None)
        committed_ok = all(
            not (source_root / _fixture_name(spec)).exists()
            and _file_matches(
                target_root / _fixture_name(spec),
                payloads[int(spec["sequence"])],
            )
            for spec in specs
        )
        self.checks.append(
            _check(
                "normal_quarantine_postconditions",
                committed_ok,
                "every synthetic target is verified before its synthetic source is removed",
                {"committed_fixture_count": self.metrics["normal_committed_count"]},
            )
        )
        if not committed_ok:
            raise _RehearsalBlocked("normal quarantine postconditions failed")

        self._write_journal(journal, "rollback_required", None)
        for spec in reversed(specs):
            source = source_root / _fixture_name(spec)
            target = target_root / _fixture_name(spec)
            payload = payloads[int(spec["sequence"])]
            _copy_new_verified(target, source, payload)
            target.unlink()
            self.metrics["normal_rolled_back_count"] += 1
        self._write_journal(journal, "rolled_back", None)
        rollback_ok = bool(
            all(
                _file_matches(
                    source_root / _fixture_name(spec),
                    payloads[int(spec["sequence"])],
                )
                and not (target_root / _fixture_name(spec)).exists()
                for spec in specs
            )
            and hmac.compare_digest(
                baseline,
                _fixture_tree_fingerprint(source_root, specs),
            )
        )
        self.checks.append(
            _check(
                "no_overwrite_rollback",
                rollback_ok,
                "reverse-order rollback restores every fixture without overwriting a source",
                {"rolled_back_fixture_count": self.metrics["normal_rolled_back_count"]},
            )
        )
        if not rollback_ok:
            raise _RehearsalBlocked("fixture rollback did not restore its baseline")

    def _run_recovery_cases(self, spec: dict[str, Any]) -> None:
        cases = (
            ("copying", "partial_target", "rolled_back"),
            ("copied_and_verified", "source_and_target", "rolled_back"),
            ("source_removal_committing", "target_only", "rolled_back"),
            ("committed", "target_only", "committed_verified"),
            ("copying", "corrupt_source_and_target", "blocked_ambiguous"),
        )
        outcomes: list[dict[str, str]] = []
        payload = _fixture_payload(self.planning, spec)
        for index, (state, layout, expected) in enumerate(cases, start=1):
            case_root = self.scratch / "recovery" / f"{index:02d}-{state}"
            source = case_root / "source.fixture"
            target = case_root / "target.fixture"
            case_root.mkdir(parents=True)
            if layout in {"partial_target", "source_and_target"}:
                _write_new_file(source, payload)
            elif layout == "corrupt_source_and_target":
                _write_new_file(source, b"corrupt-source")
            if layout == "partial_target":
                _write_new_file(target, payload[: max(1, len(payload) // 2)])
            elif layout in {
                "source_and_target",
                "target_only",
                "corrupt_source_and_target",
            }:
                _write_new_file(target, payload)
            journal = case_root / _JOURNAL_NAME
            self._write_journal(journal, state, spec)
            before = _case_identity(source, target)
            outcome = _recover_fixture_case(
                state=state,
                source=source,
                target=target,
                expected_payload=payload,
            )
            after = _case_identity(source, target)
            if outcome == "blocked_ambiguous" and before != after:
                raise _RehearsalBlocked("ambiguous recovery case mutated fixture state")
            if outcome != "blocked_ambiguous":
                self._write_journal(journal, outcome, spec)
            outcomes.append(
                {
                    "state": state,
                    "layout": layout,
                    "expected": expected,
                    "observed": outcome,
                }
            )
        self.metrics["recovery_case_count"] = len(cases)
        self.metrics["recovered_case_count"] = sum(
            item["observed"] == item["expected"]
            and item["observed"] != "blocked_ambiguous"
            for item in outcomes
        )
        self.metrics["ambiguous_case_count"] = 1
        self.metrics["ambiguous_case_blocked_count"] = sum(
            item["observed"] == "blocked_ambiguous" for item in outcomes
        )
        recovery_ok = all(
            item["observed"] == item["expected"] for item in outcomes
        )
        self.checks.append(
            _check(
                "crash_recovery_cases",
                recovery_ok,
                "restart policy recovers known states and blocks ambiguous bytes without mutation",
                {"cases": outcomes},
            )
        )
        if not recovery_ok:
            raise _RehearsalBlocked("one or more crash recovery cases failed")
        self.checks.append(
            _check(
                "durable_journal_transitions",
                self.metrics["journal_transition_count"] >= len(cases),
                "journal transitions use fsynced temporary files and durable atomic replacement",
                {
                    "strategy": self.journal_strategy,
                    "transition_count": self.metrics["journal_transition_count"],
                },
            )
        )

    def _write_journal(
        self,
        path: Path,
        state: str,
        spec: dict[str, Any] | None,
    ) -> None:
        if state not in _REQUIRED_JOURNAL_STATES and state not in {
            "committed_verified"
        }:
            raise _RehearsalBlocked(f"unsupported journal state: {state}")
        payload = {
            "contract_version": QUARANTINE_REHEARSAL_CONTRACT_VERSION,
            "fixture_format_version": QUARANTINE_FIXTURE_FORMAT_VERSION,
            "request_id": self.request.id,
            "dry_run_plan_id": self.plan.id,
            "quarantine_planning_run_id": self.planning.id,
            "planning_result_fingerprint_sha256": (
                self.planning.result_fingerprint_sha256
            ),
            "state": state,
            "item": (
                {
                    "sequence": spec["sequence"],
                    "record_id": spec["record_id"],
                    "fixture_name": _fixture_name(spec),
                }
                if spec is not None
                else None
            ),
        }
        _atomic_write_json(path, payload)
        observed = _strict_json_object(path.read_bytes(), "journal")
        if observed != payload:
            raise _RehearsalBlocked("journal readback differs from written payload")
        self.metrics["journal_transition_count"] += 1

    def _cleanup(self) -> None:
        errors: list[str] = []
        if self.scratch_created:
            try:
                _assert_owned_scratch(
                    self.quarantine_root,
                    self.scratch,
                    self.token,
                )
                shutil.rmtree(self.scratch)
                if os.path.lexists(str(self.scratch)):
                    raise OSError("scratch directory still exists after cleanup")
                self.scratch_removed = True
            except Exception as exc:
                errors.append(_safe_error_message(exc))
        else:
            self.scratch_removed = not os.path.lexists(str(self.scratch))
        cleanup_ok = self.scratch_removed and not errors
        self.checks.append(
            _check(
                "scratch_cleanup",
                cleanup_ok,
                "the exact owned scratch directory is removed after every outcome",
                {"errors": errors, "scratch_directory": str(self.scratch)},
            )
        )


def expected_quarantine_rehearsal_confirmation(
    request_id: int,
    dry_run_plan_id: int,
    quarantine_planning_run_id: int,
    planning_result_fingerprint_sha256: str,
    destination_contract_fingerprint_sha256: str,
) -> str:
    return (
        f"{QUARANTINE_REHEARSAL_CONFIRMATION_PREFIX} REQUEST "
        f"{_positive_int(request_id, 'request_id')} PLAN "
        f"{_positive_int(dry_run_plan_id, 'dry_run_plan_id')} PLANNING RUN "
        f"{_positive_int(quarantine_planning_run_id, 'quarantine_planning_run_id')} "
        f"RESULT {_fingerprint(planning_result_fingerprint_sha256, 'planning result')} "
        f"DESTINATION "
        f"{_fingerprint(destination_contract_fingerprint_sha256, 'destination contract')}"
    )


def _fixture_specs(
    planning: DataDeletionQuarantinePlanningRun,
) -> list[dict[str, Any]]:
    result = planning.result_json
    if result.get("planning_status") != "passed":
        raise _RehearsalBlocked("planning result did not pass")
    operations = result.get("file_operations")
    if not isinstance(operations, list) or not operations:
        raise _RehearsalBlocked("planning result contains no file operations")
    specs: list[dict[str, Any]] = []
    for item in operations:
        if not isinstance(item, dict):
            raise _RehearsalBlocked("planning file operation is not an object")
        sequence = _positive_int(item.get("sequence"), "sequence")
        record_id = _positive_int(item.get("record_id"), "record_id")
        relative = _safe_relative_path(item.get("source_relative_path"))
        if item.get("mutation_enabled") is not False:
            raise _RehearsalBlocked("planning file operation enables mutation")
        if item.get("target_exists") is not False:
            raise _RehearsalBlocked("planning target was not absent")
        specs.append(
            {
                "sequence": sequence,
                "record_id": record_id,
                "artifact_type": _required_text(
                    item.get("artifact_type"),
                    "artifact_type",
                    64,
                ),
                "match_id": _required_text(item.get("match_id"), "match_id", 128),
                "source_relative_path": relative.as_posix(),
                "declared_size_bytes": _nonnegative_int(
                    item.get("declared_size_bytes"),
                    "declared_size_bytes",
                ),
                "declared_sha256": _fingerprint(item.get("sha256"), "source SHA-256"),
            }
        )
    specs.sort(key=lambda item: int(item["sequence"]))
    if [item["sequence"] for item in specs] != list(range(1, len(specs) + 1)):
        raise _RehearsalBlocked("planning file operation sequences are not contiguous")
    if len({int(item["record_id"]) for item in specs}) != len(specs):
        raise _RehearsalBlocked("planning file operations contain duplicate record IDs")
    expected_count = _nonnegative_int(
        result.get("metrics", {}).get("candidate_file_count"),
        "candidate_file_count",
    )
    if len(specs) != expected_count:
        raise _RehearsalBlocked("planning candidate count differs from file operations")
    return specs


def _fixture_payload(
    planning: DataDeletionQuarantinePlanningRun,
    spec: dict[str, Any],
) -> bytes:
    return _canonical_json_bytes(
        {
            "fixture_format_version": QUARANTINE_FIXTURE_FORMAT_VERSION,
            "quarantine_planning_run_id": planning.id,
            "planning_result_fingerprint_sha256": planning.result_fingerprint_sha256,
            **spec,
        }
    ) + b"\n"


def _fixture_name(spec: dict[str, Any]) -> str:
    return f"{int(spec['sequence']):06d}-{int(spec['record_id'])}.fixture"


def _fixture_tree_fingerprint(root: Path, specs: list[dict[str, Any]]) -> str:
    records = []
    for spec in specs:
        path = root / _fixture_name(spec)
        records.append(
            {
                "name": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    return _canonical_sha256(records)


def _recover_fixture_case(
    *,
    state: str,
    source: Path,
    target: Path,
    expected_payload: bytes,
) -> str:
    source_exists = os.path.lexists(str(source))
    target_exists = os.path.lexists(str(target))
    source_valid = source_exists and _file_matches(source, expected_payload)
    target_valid = target_exists and _file_matches(target, expected_payload)
    if state == "copying":
        if source_valid:
            if target_exists:
                target.unlink()
            return "rolled_back"
        return "blocked_ambiguous"
    if state in {"copied_and_verified", "source_removal_committing"}:
        if source_valid and target_valid:
            target.unlink()
            return "rolled_back"
        if not source_exists and target_valid:
            _copy_new_verified(target, source, expected_payload)
            target.unlink()
            return "rolled_back"
        return "blocked_ambiguous"
    if state == "committed":
        if not source_exists and target_valid:
            return "committed_verified"
        return "blocked_ambiguous"
    return "blocked_ambiguous"


def _case_identity(source: Path, target: Path) -> tuple[Any, ...]:
    return (_path_identity(source), _path_identity(target))


def _path_identity(path: Path) -> tuple[Any, ...]:
    if not os.path.lexists(str(path)):
        return (False,)
    if path.is_symlink() or not path.is_file():
        return (True, "unsafe")
    stat = path.stat()
    return (True, int(stat.st_size), _sha256_file(path))


def _copy_new_verified(source: Path, target: Path, expected_payload: bytes) -> None:
    if source.is_symlink() or not source.is_file():
        raise _RehearsalBlocked("fixture copy source is missing or symbolic")
    if os.path.lexists(str(target)):
        raise _RehearsalBlocked("fixture copy refused to overwrite a target")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with source.open("rb") as read_handle, target.open("xb") as write_handle:
            while chunk := read_handle.read(_COPY_CHUNK_BYTES):
                write_handle.write(chunk)
            write_handle.flush()
            os.fsync(write_handle.fileno())
    except Exception:
        if target.is_file() and not target.is_symlink():
            target.unlink()
        raise
    if not _file_matches(target, expected_payload):
        raise _RehearsalBlocked("fixture copy readback mismatch")


def _write_new_file(path: Path, body: bytes) -> None:
    if os.path.lexists(str(path)):
        raise _RehearsalBlocked("fixture writer refused to overwrite a path")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    body = _canonical_json_bytes(payload)
    try:
        _write_new_file(temporary, body)
        _durable_replace(temporary, path)
    finally:
        if temporary.is_file() and not temporary.is_symlink():
            temporary.unlink()


def _durable_replace(source: Path, target: Path) -> None:
    if os.name == "nt":
        move_file_ex = ctypes.windll.kernel32.MoveFileExW
        move_file_ex.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
        move_file_ex.restype = ctypes.c_int
        replace_existing = 0x1
        write_through = 0x8
        if not move_file_ex(
            str(source),
            str(target),
            replace_existing | write_through,
        ):
            error = ctypes.get_last_error()
            raise OSError(error, "MoveFileExW journal replacement failed")
        return
    os.replace(source, target)
    descriptor = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _assert_owned_scratch(root: Path, scratch: Path, token: str) -> None:
    root_resolved = root.resolve(strict=True)
    scratch_resolved = scratch.resolve(strict=True)
    expected_name = f"{_SCRATCH_PREFIX}{token}"
    if scratch.name != expected_name or scratch_resolved.name != expected_name:
        raise _RehearsalBlocked("scratch directory name does not match its owner token")
    if scratch_resolved.parent != root_resolved:
        raise _RehearsalBlocked("scratch directory escaped the quarantine root")
    if root.is_symlink() or scratch.is_symlink() or not scratch.is_dir():
        raise _RehearsalBlocked("scratch directory ownership contract is unsafe")


def _file_matches(path: Path, expected: bytes) -> bool:
    return bool(
        path.is_file()
        and not path.is_symlink()
        and path.stat().st_size == len(expected)
        and hmac.compare_digest(_sha256_file(path), hashlib.sha256(expected).hexdigest())
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_COPY_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_locked_planning_run(
    row: dict[str, Any] | None,
    planning: DataDeletionQuarantinePlanningRun,
) -> None:
    if not row:
        raise DataDeletionQuarantineRehearsalError(
            "latest quarantine planning run disappeared before audit."
        )
    expected = (
        int(row.get("id") or 0) == planning.id,
        str(row.get("contract_version") or "") == planning.contract_version,
        str(row.get("plan_fingerprint_sha256") or "")
        == planning.plan_fingerprint_sha256,
        str(row.get("destination_contract_fingerprint_sha256") or "")
        == planning.destination_contract_fingerprint_sha256,
        str(row.get("result_fingerprint_sha256") or "")
        == planning.result_fingerprint_sha256,
        str(row.get("result_status") or "") == "passed",
        int(row.get("capacity_evidence_id") or 0)
        == int(planning.capacity_evidence_id or 0),
    )
    if not all(expected):
        raise DataDeletionQuarantineRehearsalError(
            "latest quarantine planning run changed before audit."
        )


def _rehearsal_run_from_row(
    row: dict[str, Any],
) -> DataDeletionQuarantineRehearsalRun:
    status = str(row.get("result_status") or "")
    if status not in {"passed", "blocked"}:
        raise DataDeletionQuarantineRehearsalError(
            f"unsupported quarantine rehearsal status: {status}."
        )
    result = _json_object(row.get("result_json"), "result_json")
    fingerprint = _fingerprint(row.get("result_fingerprint_sha256"), "result")
    if not hmac.compare_digest(fingerprint, _canonical_sha256(result)):
        raise DataDeletionQuarantineRehearsalError(
            "quarantine rehearsal result fingerprint is invalid."
        )
    metrics = _result_metrics(result)
    checks = _checks(result)
    blockers = _blockers(result)
    contract_version = str(row.get("contract_version") or "")
    safety = result.get("safety")
    if not isinstance(safety, dict):
        raise DataDeletionQuarantineRehearsalError(
            "quarantine rehearsal safety contract is missing."
        )
    safety_bindings = (
        result.get("contract_version") == QUARANTINE_REHEARSAL_CONTRACT_VERSION,
        result.get("fixture_format_version") == QUARANTINE_FIXTURE_FORMAT_VERSION,
        contract_version == QUARANTINE_REHEARSAL_CONTRACT_VERSION,
        safety.get("synthetic_fixtures_only") is True,
        safety.get("production_source_files_opened") is False,
        safety.get("production_source_files_modified") is False,
        safety.get("production_quarantine_performed") is False,
        safety.get("database_source_rows_modified") is False,
        safety.get("deletion_performed") is False,
        safety.get("execution_enabled") is False,
        safety.get("execution_ready") is False,
    )
    if not all(safety_bindings):
        raise DataDeletionQuarantineRehearsalError(
            "quarantine rehearsal safety contract is invalid."
        )
    bindings = (
        result.get("result_status") == status,
        int(result.get("request_id") or 0) == int(row["request_id"]),
        int(result.get("dry_run_plan_id") or 0) == int(row["dry_run_plan_id"]),
        int(result.get("quarantine_planning_run_id") or 0)
        == int(row["quarantine_planning_run_id"]),
        result.get("plan_fingerprint_sha256")
        == str(row["plan_fingerprint_sha256"]),
        result.get("destination_contract_fingerprint_sha256")
        == str(row["destination_contract_fingerprint_sha256"]),
        result.get("planning_result_fingerprint_sha256")
        == str(row["planning_result_fingerprint_sha256"]),
        str(result.get("scratch_directory") or "")
        == str(row.get("scratch_directory") or ""),
    )
    if not all(bindings):
        raise DataDeletionQuarantineRehearsalError(
            "quarantine rehearsal audit bindings are invalid."
        )
    metric_fields = tuple(_empty_metrics())
    if any(
        metrics[field] != _nonnegative_int(row.get(field), field)
        for field in metric_fields
    ):
        raise DataDeletionQuarantineRehearsalError(
            "quarantine rehearsal metric bindings are invalid."
        )
    removed = bool(row.get("scratch_directory_removed"))
    if removed != bool(result.get("safety", {}).get("scratch_directory_removed")):
        raise DataDeletionQuarantineRehearsalError(
            "quarantine rehearsal cleanup binding is invalid."
        )
    if _nonnegative_int(row.get("check_count"), "check_count") != len(checks):
        raise DataDeletionQuarantineRehearsalError(
            "quarantine rehearsal check-count binding is invalid."
        )
    passed_check_count = sum(item.get("status") == "passed" for item in checks)
    if (
        _nonnegative_int(row.get("passed_check_count"), "passed_check_count")
        != passed_check_count
    ):
        raise DataDeletionQuarantineRehearsalError(
            "quarantine rehearsal passed-check binding is invalid."
        )
    if _nonnegative_int(row.get("blocker_count"), "blocker_count") != len(blockers):
        raise DataDeletionQuarantineRehearsalError(
            "quarantine rehearsal blocker-count binding is invalid."
        )
    if status == "passed" and (
        not removed
        or blockers
        or passed_check_count != len(checks)
    ):
        raise DataDeletionQuarantineRehearsalError(
            "passed quarantine rehearsal does not satisfy cleanup and check invariants."
        )
    return DataDeletionQuarantineRehearsalRun(
        id=_positive_int(row["id"], "id"),
        request_id=_positive_int(row["request_id"], "request_id"),
        dry_run_plan_id=_positive_int(row["dry_run_plan_id"], "dry_run_plan_id"),
        quarantine_planning_run_id=_positive_int(
            row["quarantine_planning_run_id"],
            "quarantine_planning_run_id",
        ),
        contract_version=contract_version,
        plan_fingerprint_sha256=_fingerprint(
            row["plan_fingerprint_sha256"],
            "plan fingerprint",
        ),
        destination_contract_fingerprint_sha256=_fingerprint(
            row["destination_contract_fingerprint_sha256"],
            "destination fingerprint",
        ),
        planning_result_fingerprint_sha256=_fingerprint(
            row["planning_result_fingerprint_sha256"],
            "planning result fingerprint",
        ),
        result_fingerprint_sha256=fingerprint,
        result_status=status,
        result_json=result,
        scratch_directory=str(row["scratch_directory"]),
        scratch_directory_removed=removed,
        fixture_file_count=metrics["fixture_file_count"],
        fixture_bytes=metrics["fixture_bytes"],
        normal_committed_count=metrics["normal_committed_count"],
        normal_rolled_back_count=metrics["normal_rolled_back_count"],
        recovery_case_count=metrics["recovery_case_count"],
        recovered_case_count=metrics["recovered_case_count"],
        ambiguous_case_count=metrics["ambiguous_case_count"],
        ambiguous_case_blocked_count=metrics["ambiguous_case_blocked_count"],
        journal_transition_count=metrics["journal_transition_count"],
        check_count=len(checks),
        passed_check_count=passed_check_count,
        blocker_count=len(blockers),
        run_by=_required_text(row.get("run_by"), "run_by", 191),
        rehearsal_note=_optional_text(row.get("rehearsal_note"), "rehearsal_note", 1000),
        run_at_kst=_datetime_value(row.get("run_at_kst"), "run_at_kst"),
    )


def _empty_metrics() -> dict[str, int]:
    return {
        "fixture_file_count": 0,
        "fixture_bytes": 0,
        "normal_committed_count": 0,
        "normal_rolled_back_count": 0,
        "recovery_case_count": 0,
        "recovered_case_count": 0,
        "ambiguous_case_count": 0,
        "ambiguous_case_blocked_count": 0,
        "journal_transition_count": 0,
    }


def _result_metrics(result: dict[str, Any]) -> dict[str, int]:
    value = result.get("metrics")
    if not isinstance(value, dict):
        raise DataDeletionQuarantineRehearsalError(
            "quarantine rehearsal result metrics are missing."
        )
    return {
        key: _nonnegative_int(value.get(key), key)
        for key in _empty_metrics()
    }


def _checks(result: dict[str, Any]) -> list[dict[str, Any]]:
    value = result.get("checks")
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise DataDeletionQuarantineRehearsalError(
            "quarantine rehearsal checks are invalid."
        )
    return [dict(item) for item in value]


def _blockers(result: dict[str, Any]) -> list[str]:
    value = result.get("blockers")
    if not isinstance(value, list):
        raise DataDeletionQuarantineRehearsalError(
            "quarantine rehearsal blockers are invalid."
        )
    return [str(item) for item in value]


def _check(
    key: str,
    passed: bool,
    message: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    return {
        "key": key,
        "status": "passed" if passed else "blocked",
        "message": message,
        "details": details,
    }


def _strict_json_object(body: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _RehearsalBlocked(f"{label} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise _RehearsalBlocked(f"{label} must be a JSON object")
    return value


def _json_object(value: Any, label: str) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise DataDeletionQuarantineRehearsalError(
                f"{label} is invalid JSON."
            ) from exc
    if not isinstance(value, dict):
        raise DataDeletionQuarantineRehearsalError(
            f"{label} must be an object."
        )
    return value


def _safe_relative_path(value: Any) -> PurePosixPath:
    text = str(value or "")
    if not text or "\\" in text or ":" in text or "\x00" in text:
        raise _RehearsalBlocked("planning source relative path is unsafe")
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise _RehearsalBlocked("planning source relative path is unsafe")
    return path


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _json_dump(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _absolute_input_path(path: str | Path) -> Path:
    value = Path(path).expanduser()
    if not value.is_absolute():
        value = Path.cwd() / value
    return value


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _fingerprint(value: Any, label: str) -> str:
    text = str(value or "").lower()
    if len(text) != _FINGERPRINT_HEX_LENGTH or any(
        character not in "0123456789abcdef" for character in text
    ):
        raise DataDeletionQuarantineRehearsalError(
            f"{label} must be a lowercase SHA-256 fingerprint."
        )
    return text


def _positive_int(value: Any, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise DataDeletionQuarantineRehearsalError(
            f"{label} must be a positive integer."
        ) from exc
    if number <= 0:
        raise DataDeletionQuarantineRehearsalError(
            f"{label} must be a positive integer."
        )
    return number


def _nonnegative_int(value: Any, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise DataDeletionQuarantineRehearsalError(
            f"{label} must be a non-negative integer."
        ) from exc
    if number < 0:
        raise DataDeletionQuarantineRehearsalError(
            f"{label} must be a non-negative integer."
        )
    return number


def _required_text(value: Any, label: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum:
        raise DataDeletionQuarantineRehearsalError(
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
        raise DataDeletionQuarantineRehearsalError(
            f"{label} must contain no more than {maximum} characters."
        )
    return text


def _datetime_value(value: Any, label: str) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise DataDeletionQuarantineRehearsalError(
                f"{label} is invalid."
            ) from exc
    raise DataDeletionQuarantineRehearsalError(f"{label} is invalid.")


def _safe_error_message(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc)[:1000]}"


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
