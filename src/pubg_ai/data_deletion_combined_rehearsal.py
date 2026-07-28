from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
import hashlib
import hmac
import json

from pubg_ai.data_deletion_backup import DataDeletionBackupService
from pubg_ai.data_deletion_backup_verifier import (
    BACKUP_VERIFIER_CONTRACT_VERSION,
    DataDeletionBackupVerificationRun,
    DataDeletionBackupVerifierError,
    DataDeletionBackupVerifierService,
)
from pubg_ai.data_deletion_dry_run import (
    AUDIT_TABLE_EXCLUSIONS,
    DRY_RUN_CONTRACT_VERSION,
    DataDeletionDryRunPlan,
    fingerprint_dry_run_plan,
)
from pubg_ai.data_deletion_quarantine_planner import (
    QUARANTINE_PLANNER_CONTRACT_VERSION,
    DataDeletionQuarantinePlanningRun,
)
from pubg_ai.data_deletion_quarantine_rehearsal import (
    DataDeletionQuarantineRehearsalError,
    DataDeletionQuarantineRehearsalService,
)
from pubg_ai.data_deletion_requests import DataDeletionRequest
from pubg_ai.data_deletion_restore_rehearsal import (
    run_isolated_mysql_deletion_rehearsal,
)
from pubg_ai.time_utils import now_kst, to_kst


COMBINED_REHEARSAL_CONTRACT_VERSION = "deletion-combined-rehearsal-v1"
COMBINED_REHEARSAL_CONFIRMATION_PREFIX = (
    "RUN ISOLATED COMBINED DELETION REHEARSAL"
)


class DataDeletionCombinedRehearsalError(RuntimeError):
    """Raised when the isolated combined deletion rehearsal is unsafe."""


@dataclass(frozen=True)
class DataDeletionCombinedRehearsalRun:
    id: int
    request_id: int
    dry_run_plan_id: int
    backup_verification_run_id: int
    quarantine_planning_run_id: int
    contract_version: str
    plan_fingerprint_sha256: str
    backup_verification_result_fingerprint_sha256: str
    current_backup_revalidation_result_fingerprint_sha256: str | None
    quarantine_planning_result_fingerprint_sha256: str
    destination_contract_fingerprint_sha256: str
    result_fingerprint_sha256: str
    result_status: str
    result_json: dict[str, Any]
    mysql_candidate_table_count: int
    mysql_candidate_row_count: int
    mysql_delete_operation_count: int
    mysql_deleted_row_count: int
    mysql_rolled_back_row_count: int
    preserved_descriptor_count: int
    quarantine_fixture_file_count: int
    quarantine_recovery_case_count: int
    quarantine_recovered_case_count: int
    scratch_resources_removed: bool
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
            "backup_verification_run_id": self.backup_verification_run_id,
            "quarantine_planning_run_id": self.quarantine_planning_run_id,
            "contract_version": self.contract_version,
            "plan_fingerprint_sha256": self.plan_fingerprint_sha256,
            "backup_verification_result_fingerprint_sha256": (
                self.backup_verification_result_fingerprint_sha256
            ),
            "current_backup_revalidation_result_fingerprint_sha256": (
                self.current_backup_revalidation_result_fingerprint_sha256
            ),
            "quarantine_planning_result_fingerprint_sha256": (
                self.quarantine_planning_result_fingerprint_sha256
            ),
            "destination_contract_fingerprint_sha256": (
                self.destination_contract_fingerprint_sha256
            ),
            "result_fingerprint_sha256": self.result_fingerprint_sha256,
            "result_status": self.result_status,
            "mysql_candidate_table_count": self.mysql_candidate_table_count,
            "mysql_candidate_row_count": self.mysql_candidate_row_count,
            "mysql_delete_operation_count": self.mysql_delete_operation_count,
            "mysql_deleted_row_count": self.mysql_deleted_row_count,
            "mysql_rolled_back_row_count": self.mysql_rolled_back_row_count,
            "preserved_descriptor_count": self.preserved_descriptor_count,
            "quarantine_fixture_file_count": self.quarantine_fixture_file_count,
            "quarantine_recovery_case_count": self.quarantine_recovery_case_count,
            "quarantine_recovered_case_count": self.quarantine_recovered_case_count,
            "scratch_resources_removed": self.scratch_resources_removed,
            "check_count": self.check_count,
            "passed_check_count": self.passed_check_count,
            "blocker_count": self.blocker_count,
            "run_by": self.run_by,
            "rehearsal_note": self.rehearsal_note,
            "run_at_kst": to_kst(self.run_at_kst).isoformat(),
            "immutable": True,
            "temporary_mysql_tables_only": True,
            "synthetic_quarantine_only": True,
            "production_database_rows_modified": False,
            "production_source_files_opened": False,
            "production_source_files_modified": False,
            "production_quarantine_performed": False,
            "production_restore_performed": False,
            "deletion_performed": False,
            "execution_enabled": False,
            "execution_ready": False,
        }

    def to_record(self) -> dict[str, Any]:
        return {**self.to_summary_record(), "result_json": self.result_json}


class DataDeletionCombinedRehearsalService:
    def __init__(
        self,
        connection: Any,
        *,
        backup_service: DataDeletionBackupService,
        verifier_service: DataDeletionBackupVerifierService,
        quarantine_rehearsal_service: DataDeletionQuarantineRehearsalService,
        scratch_connection_factory: Callable[[], Any],
        backup_root: Path,
        expected_database_name: str,
        mysql_runner: Callable[..., dict[str, Any]] = (
            run_isolated_mysql_deletion_rehearsal
        ),
    ) -> None:
        self.connection = connection
        self.backup_service = backup_service
        self.verifier_service = verifier_service
        self.quarantine_rehearsal_service = quarantine_rehearsal_service
        self.scratch_connection_factory = scratch_connection_factory
        self.backup_root = backup_root.expanduser().resolve(strict=False)
        self.expected_database_name = _identifier(
            expected_database_name,
            "expected_database_name",
        )
        self.mysql_runner = mysql_runner

    def rehearsal_state(self, request: DataDeletionRequest) -> dict[str, Any]:
        plans = self.backup_service.dry_run_service.list_plans(
            request.id,
            limit=1,
        )
        plan = plans[0] if plans else None
        blockers: list[str] = []
        if request.status != "approved":
            blockers.append(f"request status must be approved, not {request.status}")
        if plan is None:
            blockers.append("latest confirmed dry-run plan is required")
            verifications: list[DataDeletionBackupVerificationRun] = []
            planning_runs: list[DataDeletionQuarantinePlanningRun] = []
            history: list[DataDeletionCombinedRehearsalRun] = []
        else:
            blockers.extend(_plan_blockers(plan, request))
            verifications = self.verifier_service.list_runs(plan.id, limit=50)
            planning_runs = self.quarantine_rehearsal_service.planner_service.list_runs(
                plan.id,
                limit=50,
            )
            history = self.list_runs(plan.id, limit=50)
        verification = next(
            (item for item in verifications if item.result_status == "passed"),
            None,
        )
        planning = planning_runs[0] if planning_runs else None
        if plan is not None and verification is None:
            blockers.append("a passed backup verification is required")
        if plan is not None:
            blockers.extend(_planning_blockers(plan, planning))
        confirmation = (
            expected_combined_rehearsal_confirmation(
                request.id,
                plan.id,
                verification.id,
                verification.result_fingerprint_sha256,
                planning.id,
                planning.result_fingerprint_sha256,
                planning.destination_contract_fingerprint_sha256,
            )
            if plan is not None
            and verification is not None
            and planning is not None
            and not _planning_blockers(plan, planning)
            else None
        )
        candidate = (
            {
                "dry_run_plan_id": plan.id,
                "plan_fingerprint_sha256": plan.plan_fingerprint_sha256,
                "backup_verification": verification.to_summary_record(),
                "quarantine_planning": planning.to_summary_record(),
                "confirmation_text": confirmation,
            }
            if plan is not None
            and verification is not None
            and planning is not None
            and confirmation is not None
            else None
        )
        blockers = _deduplicate(blockers)
        return {
            "request_id": request.id,
            "request_status": request.status,
            "contract_version": COMBINED_REHEARSAL_CONTRACT_VERSION,
            "latest_plan_id": plan.id if plan is not None else None,
            "plan_fingerprint_sha256": (
                plan.plan_fingerprint_sha256 if plan is not None else None
            ),
            "combined_candidate": candidate,
            "latest_combined_rehearsal": (
                history[0].to_record() if history else None
            ),
            "combined_rehearsal_history": [
                item.to_summary_record() for item in history
            ],
            "combined_rehearsal_allowed": not blockers and candidate is not None,
            "combined_rehearsal_blockers": blockers,
            "mysql_strategy": (
                "verified-backup-to-connection-temporary-tables-delete-rollback"
            ),
            "quarantine_strategy": "metadata-derived-synthetic-state-machine",
            "appends_combined_audit_row": True,
            "appends_readiness_evidence": False,
            "production_database_rows_modified": False,
            "production_source_files_opened": False,
            "production_source_files_modified": False,
            "production_quarantine_performed": False,
            "production_restore_performed": False,
            "deletion_performed": False,
            "execution_enabled": False,
            "execution_ready": False,
        }

    def run(
        self,
        request: DataDeletionRequest,
        *,
        backup_verification_run_id: int,
        quarantine_planning_run_id: int,
        confirmation_text: str,
        actor_id: str,
        note: str | None = None,
        reference_kst: datetime | None = None,
    ) -> DataDeletionCombinedRehearsalRun:
        verification_id = _positive_int(
            backup_verification_run_id,
            "backup_verification_run_id",
        )
        planning_id = _positive_int(
            quarantine_planning_run_id,
            "quarantine_planning_run_id",
        )
        actor_id = _required_text(actor_id, "actor_id", 191)
        note = _optional_text(note, "note", 1000)
        if request.status != "approved":
            raise DataDeletionCombinedRehearsalError(
                "request must remain approved."
            )
        verification = self.verifier_service.get_run(verification_id)
        if verification.request_id != request.id:
            raise DataDeletionCombinedRehearsalError(
                "backup verification belongs to another deletion request."
            )
        if (
            verification.contract_version != BACKUP_VERIFIER_CONTRACT_VERSION
            or verification.result_status != "passed"
        ):
            raise DataDeletionCombinedRehearsalError(
                "combined rehearsal requires a passed backup verification."
            )
        plan = self.backup_service.require_latest_plan(
            request,
            verification.dry_run_plan_id,
        )
        plan_errors = _plan_blockers(plan, request)
        if plan_errors:
            raise DataDeletionCombinedRehearsalError(
                "combined rehearsal dry-run plan is blocked: "
                + "; ".join(plan_errors)
            )
        if not hmac.compare_digest(
            verification.plan_fingerprint_sha256,
            plan.plan_fingerprint_sha256,
        ):
            raise DataDeletionCombinedRehearsalError(
                "backup verification plan fingerprint is stale."
            )
        planning = self.quarantine_rehearsal_service.planner_service.get_run(
            planning_id
        )
        if planning.request_id != request.id:
            raise DataDeletionCombinedRehearsalError(
                "quarantine planning run belongs to another deletion request."
            )
        latest_planning = (
            self.quarantine_rehearsal_service.planner_service.list_runs(
                plan.id,
                limit=1,
            )
        )
        if not latest_planning or latest_planning[0].id != planning.id:
            raise DataDeletionCombinedRehearsalError(
                "selected quarantine planning run is not the latest run."
            )
        planning_errors = _planning_blockers(plan, planning)
        if planning_errors:
            raise DataDeletionCombinedRehearsalError(
                "combined rehearsal quarantine planning is blocked: "
                + "; ".join(planning_errors)
            )
        expected_confirmation = expected_combined_rehearsal_confirmation(
            request.id,
            plan.id,
            verification.id,
            verification.result_fingerprint_sha256,
            planning.id,
            planning.result_fingerprint_sha256,
            planning.destination_contract_fingerprint_sha256,
        )
        supplied_confirmation = _required_text(
            confirmation_text,
            "confirmation_text",
            700,
        )
        if not hmac.compare_digest(supplied_confirmation, expected_confirmation):
            raise DataDeletionCombinedRehearsalError(
                "combined rehearsal confirmation does not match all selected inputs."
            )

        run_at = to_kst(reference_kst or now_kst())
        checks: list[dict[str, Any]] = [
            _check(
                "combined_input_binding",
                True,
                "latest plan, passed backup verification, and latest passed quarantine planning",
                {
                    "dry_run_plan_id": plan.id,
                    "backup_verification_run_id": verification.id,
                    "quarantine_planning_run_id": planning.id,
                },
                "all immutable combined rehearsal inputs are current and fingerprint-bound",
            )
        ]
        current_revalidation_fingerprint: str | None = None
        mysql_result: dict[str, Any] | None = None
        quarantine_result: dict[str, Any] | None = None
        try:
            revalidated = self.verifier_service.revalidate_passed_run(
                request,
                verification.id,
                reference_kst=run_at,
            )
            current_revalidation_fingerprint = (
                revalidated.current_result_fingerprint_sha256
            )
            if revalidated.plan.id != plan.id or not hmac.compare_digest(
                revalidated.plan.plan_fingerprint_sha256,
                plan.plan_fingerprint_sha256,
            ):
                raise DataDeletionCombinedRehearsalError(
                    "backup revalidation returned another dry-run plan."
                )
            mysql_result = self.mysql_runner(
                audit_connection=self.connection,
                scratch_connection_factory=self.scratch_connection_factory,
                backup_root=self.backup_root,
                expected_database_name=self.expected_database_name,
                revalidated=revalidated,
            )
            checks.extend(_prefixed_checks("mysql", mysql_result.get("checks")))
        except (DataDeletionBackupVerifierError, DataDeletionCombinedRehearsalError) as exc:
            checks.append(
                _check(
                    "mysql.revalidation_and_rehearsal",
                    False,
                    "backup revalidation and temporary-table delete/rollback succeed",
                    None,
                    _safe_error_message(exc),
                )
            )
        except Exception as exc:
            checks.append(
                _check(
                    "mysql.revalidation_and_rehearsal",
                    False,
                    "temporary MySQL rehearsal completes without an unexpected error",
                    None,
                    _safe_error_message(exc),
                )
            )

        try:
            bound_plan, bound_planning, quarantine_result = (
                self.quarantine_rehearsal_service.run_bound_synthetic_state(
                    request,
                    quarantine_planning_run_id=planning.id,
                    reference_kst=run_at,
                )
            )
            if bound_plan.id != plan.id or bound_planning.id != planning.id:
                raise DataDeletionCombinedRehearsalError(
                    "synthetic quarantine rehearsal returned another input binding."
                )
            checks.extend(
                _prefixed_checks("quarantine", quarantine_result.get("checks"))
            )
        except (
            DataDeletionQuarantineRehearsalError,
            DataDeletionCombinedRehearsalError,
        ) as exc:
            checks.append(
                _check(
                    "quarantine.synthetic_state_machine",
                    False,
                    "bound synthetic quarantine state machine passes and cleans up",
                    None,
                    _safe_error_message(exc),
                )
            )
        except Exception as exc:
            checks.append(
                _check(
                    "quarantine.synthetic_state_machine",
                    False,
                    "synthetic quarantine rehearsal completes without an unexpected error",
                    None,
                    _safe_error_message(exc),
                )
            )

        mysql_deletion = (
            mysql_result.get("deletion_exercise")
            if isinstance(mysql_result, dict)
            else None
        )
        mysql_cleanup = _check_passed(
            mysql_result.get("checks") if isinstance(mysql_result, dict) else None,
            "scratch_cleanup",
        )
        quarantine_cleanup = bool(
            isinstance(quarantine_result, dict)
            and quarantine_result.get("safety", {}).get(
                "scratch_directory_removed"
            )
            is True
        )
        subcontracts_safe = (
            _mysql_deletion_safe(mysql_deletion)
            and _quarantine_result_safe(quarantine_result)
            and mysql_cleanup
            and quarantine_cleanup
        )
        checks.append(
            _check(
                "combined_safety_contract",
                subcontracts_safe,
                "temporary MySQL rollback and synthetic quarantine cleanup both pass",
                {
                    "mysql_cleanup": mysql_cleanup,
                    "quarantine_cleanup": quarantine_cleanup,
                    "mysql_deletion_safe": _mysql_deletion_safe(mysql_deletion),
                    "quarantine_safe": _quarantine_result_safe(quarantine_result),
                },
                (
                    "both isolated sub-rehearsals passed without production mutation"
                    if subcontracts_safe
                    else "one or more isolated sub-rehearsal safety contracts failed"
                ),
            )
        )
        blockers = [
            str(item.get("message") or item.get("key") or "blocked check")
            for item in checks
            if item.get("status") == "blocked"
        ]
        status = "passed" if not blockers else "blocked"
        metrics = _combined_metrics(mysql_deletion, quarantine_result)
        scratch_removed = mysql_cleanup and quarantine_cleanup
        result = {
            "contract_version": COMBINED_REHEARSAL_CONTRACT_VERSION,
            "request_id": request.id,
            "dry_run_plan_id": plan.id,
            "backup_verification_run_id": verification.id,
            "quarantine_planning_run_id": planning.id,
            "plan_fingerprint_sha256": plan.plan_fingerprint_sha256,
            "backup_verification_result_fingerprint_sha256": (
                verification.result_fingerprint_sha256
            ),
            "current_backup_revalidation_result_fingerprint_sha256": (
                current_revalidation_fingerprint
            ),
            "quarantine_planning_result_fingerprint_sha256": (
                planning.result_fingerprint_sha256
            ),
            "destination_contract_fingerprint_sha256": (
                planning.destination_contract_fingerprint_sha256
            ),
            "checks": checks,
            "metrics": metrics,
            "mysql_deletion_exercise": mysql_deletion,
            "quarantine_rehearsal": quarantine_result,
            "blockers": blockers,
            "result_status": status,
            "run_at_kst": run_at.isoformat(),
            "safety": {
                "temporary_mysql_tables_only": True,
                "mysql_deletion_transaction_rolled_back": _mysql_deletion_safe(
                    mysql_deletion
                ),
                "synthetic_quarantine_only": True,
                "scratch_resources_removed": scratch_removed,
                "shared_data_preserved": bool(
                    isinstance(mysql_deletion, dict)
                    and mysql_deletion.get("safety", {}).get(
                        "shared_data_preserved"
                    )
                    is True
                ),
                "audit_data_preserved": bool(
                    isinstance(mysql_deletion, dict)
                    and mysql_deletion.get("safety", {}).get(
                        "audit_data_preserved"
                    )
                    is True
                ),
                "production_database_rows_modified": False,
                "production_source_files_opened": False,
                "production_source_files_modified": False,
                "production_quarantine_performed": False,
                "production_restore_performed": False,
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
            planning,
            result=result,
            result_fingerprint=result_fingerprint,
            metrics=metrics,
            scratch_removed=scratch_removed,
            actor_id=actor_id,
            note=note,
            run_at_kst=run_at,
        )

    def get_run(
        self,
        rehearsal_run_id: int,
    ) -> DataDeletionCombinedRehearsalRun:
        run_id = _positive_int(rehearsal_run_id, "rehearsal_run_id")
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM data_deletion_combined_rehearsal_runs WHERE id = %s",
                (run_id,),
            )
            row = cursor.fetchone()
        if not row:
            raise DataDeletionCombinedRehearsalError(
                f"combined rehearsal run {run_id} was not found."
            )
        return _combined_run_from_row(row)

    def list_runs(
        self,
        dry_run_plan_id: int,
        *,
        limit: int = 50,
    ) -> list[DataDeletionCombinedRehearsalRun]:
        plan_id = _positive_int(dry_run_plan_id, "dry_run_plan_id")
        if not 1 <= int(limit) <= 100:
            raise DataDeletionCombinedRehearsalError(
                "combined rehearsal history limit must be between 1 and 100."
            )
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM data_deletion_combined_rehearsal_runs
                WHERE dry_run_plan_id = %s
                ORDER BY run_at_kst DESC, id DESC
                LIMIT %s
                """,
                (plan_id, int(limit)),
            )
            rows = cursor.fetchall()
        return [_combined_run_from_row(row) for row in rows]

    def _record_run(
        self,
        request: DataDeletionRequest,
        plan: DataDeletionDryRunPlan,
        verification: DataDeletionBackupVerificationRun,
        planning: DataDeletionQuarantinePlanningRun,
        *,
        result: dict[str, Any],
        result_fingerprint: str,
        metrics: dict[str, int],
        scratch_removed: bool,
        actor_id: str,
        note: str | None,
        run_at_kst: datetime,
    ) -> DataDeletionCombinedRehearsalRun:
        checks = _checks(result)
        blockers = _blockers(result)
        status = str(result.get("result_status") or "")
        if status not in {"passed", "blocked"}:
            raise DataDeletionCombinedRehearsalError(
                "combined rehearsal result status is invalid."
            )
        _begin(self.connection)
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, contract_version, plan_fingerprint_sha256,
                           result_fingerprint_sha256, result_status
                    FROM data_deletion_backup_verification_runs
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (verification.id,),
                )
                _assert_locked_verification(cursor.fetchone(), verification)
                cursor.execute(
                    """
                    SELECT id, contract_version, plan_fingerprint_sha256,
                           destination_contract_fingerprint_sha256,
                           result_fingerprint_sha256, result_status,
                           capacity_evidence_id
                    FROM data_deletion_quarantine_planning_runs
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (planning.id,),
                )
                _assert_locked_planning(cursor.fetchone(), planning)
                cursor.execute(
                    """
                    INSERT INTO data_deletion_combined_rehearsal_runs (
                        request_id,
                        dry_run_plan_id,
                        backup_verification_run_id,
                        quarantine_planning_run_id,
                        contract_version,
                        plan_fingerprint_sha256,
                        backup_verification_result_fingerprint_sha256,
                        current_backup_revalidation_result_fingerprint_sha256,
                        quarantine_planning_result_fingerprint_sha256,
                        destination_contract_fingerprint_sha256,
                        result_fingerprint_sha256,
                        result_status,
                        result_json,
                        mysql_candidate_table_count,
                        mysql_candidate_row_count,
                        mysql_delete_operation_count,
                        mysql_deleted_row_count,
                        mysql_rolled_back_row_count,
                        preserved_descriptor_count,
                        quarantine_fixture_file_count,
                        quarantine_recovery_case_count,
                        quarantine_recovered_case_count,
                        scratch_resources_removed,
                        check_count,
                        passed_check_count,
                        blocker_count,
                        run_by,
                        rehearsal_note,
                        run_at_kst
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        request.id,
                        plan.id,
                        verification.id,
                        planning.id,
                        COMBINED_REHEARSAL_CONTRACT_VERSION,
                        plan.plan_fingerprint_sha256,
                        verification.result_fingerprint_sha256,
                        result.get(
                            "current_backup_revalidation_result_fingerprint_sha256"
                        ),
                        planning.result_fingerprint_sha256,
                        planning.destination_contract_fingerprint_sha256,
                        result_fingerprint,
                        status,
                        _json_dump(result),
                        metrics["mysql_candidate_table_count"],
                        metrics["mysql_candidate_row_count"],
                        metrics["mysql_delete_operation_count"],
                        metrics["mysql_deleted_row_count"],
                        metrics["mysql_rolled_back_row_count"],
                        metrics["preserved_descriptor_count"],
                        metrics["quarantine_fixture_file_count"],
                        metrics["quarantine_recovery_case_count"],
                        metrics["quarantine_recovered_case_count"],
                        scratch_removed,
                        len(checks),
                        sum(item.get("status") == "passed" for item in checks),
                        len(blockers),
                        actor_id,
                        note,
                        run_at_kst,
                    ),
                )
                run_id = int(cursor.lastrowid)
            _commit(self.connection)
        except Exception:
            _rollback(self.connection)
            raise
        return DataDeletionCombinedRehearsalRun(
            id=run_id,
            request_id=request.id,
            dry_run_plan_id=plan.id,
            backup_verification_run_id=verification.id,
            quarantine_planning_run_id=planning.id,
            contract_version=COMBINED_REHEARSAL_CONTRACT_VERSION,
            plan_fingerprint_sha256=plan.plan_fingerprint_sha256,
            backup_verification_result_fingerprint_sha256=(
                verification.result_fingerprint_sha256
            ),
            current_backup_revalidation_result_fingerprint_sha256=result.get(
                "current_backup_revalidation_result_fingerprint_sha256"
            ),
            quarantine_planning_result_fingerprint_sha256=(
                planning.result_fingerprint_sha256
            ),
            destination_contract_fingerprint_sha256=(
                planning.destination_contract_fingerprint_sha256
            ),
            result_fingerprint_sha256=result_fingerprint,
            result_status=status,
            result_json=result,
            mysql_candidate_table_count=metrics["mysql_candidate_table_count"],
            mysql_candidate_row_count=metrics["mysql_candidate_row_count"],
            mysql_delete_operation_count=metrics["mysql_delete_operation_count"],
            mysql_deleted_row_count=metrics["mysql_deleted_row_count"],
            mysql_rolled_back_row_count=metrics["mysql_rolled_back_row_count"],
            preserved_descriptor_count=metrics["preserved_descriptor_count"],
            quarantine_fixture_file_count=metrics[
                "quarantine_fixture_file_count"
            ],
            quarantine_recovery_case_count=metrics[
                "quarantine_recovery_case_count"
            ],
            quarantine_recovered_case_count=metrics[
                "quarantine_recovered_case_count"
            ],
            scratch_resources_removed=scratch_removed,
            check_count=len(checks),
            passed_check_count=sum(
                item.get("status") == "passed" for item in checks
            ),
            blocker_count=len(blockers),
            run_by=actor_id,
            rehearsal_note=note,
            run_at_kst=run_at_kst,
        )


def expected_combined_rehearsal_confirmation(
    request_id: int,
    dry_run_plan_id: int,
    backup_verification_run_id: int,
    backup_verification_result_fingerprint_sha256: str,
    quarantine_planning_run_id: int,
    quarantine_planning_result_fingerprint_sha256: str,
    destination_contract_fingerprint_sha256: str,
) -> str:
    return (
        f"{COMBINED_REHEARSAL_CONFIRMATION_PREFIX} REQUEST "
        f"{_positive_int(request_id, 'request_id')} PLAN "
        f"{_positive_int(dry_run_plan_id, 'dry_run_plan_id')} VERIFICATION "
        f"{_positive_int(backup_verification_run_id, 'backup_verification_run_id')} "
        f"{_fingerprint(backup_verification_result_fingerprint_sha256, 'verification result')} "
        f"QUARANTINE PLAN "
        f"{_positive_int(quarantine_planning_run_id, 'quarantine_planning_run_id')} "
        f"{_fingerprint(quarantine_planning_result_fingerprint_sha256, 'planning result')} "
        f"DESTINATION "
        f"{_fingerprint(destination_contract_fingerprint_sha256, 'destination contract')}"
    )


def _plan_blockers(
    plan: DataDeletionDryRunPlan,
    request: DataDeletionRequest,
) -> list[str]:
    blockers: list[str] = []
    if plan.request_id != request.id:
        blockers.append("dry-run plan belongs to another deletion request")
    if plan.contract_version != DRY_RUN_CONTRACT_VERSION:
        blockers.append("dry-run plan contract is unsupported")
    if not hmac.compare_digest(
        plan.plan_fingerprint_sha256,
        fingerprint_dry_run_plan(plan.plan_json),
    ):
        blockers.append("dry-run plan fingerprint is invalid")
    target = plan.plan_json.get("target")
    if not isinstance(target, dict) or (
        target.get("account_id") != request.account_id
        or target.get("shard") != request.shard
    ):
        blockers.append("dry-run plan target identity is stale")
    safety = plan.plan_json.get("safety")
    if not isinstance(safety, dict) or (
        safety.get("execution_enabled") is not False
        or safety.get("execution_ready") is not False
        or "executor_not_implemented"
        not in (safety.get("execution_blockers") or [])
    ):
        blockers.append("dry-run plan execution safety contract is invalid")
    audit_records = plan.plan_json.get("audit_table_exclusions")
    audit_tables = (
        [
            str(item.get("table") or "")
            for item in audit_records
            if isinstance(item, dict)
        ]
        if isinstance(audit_records, list)
        else []
    )
    if audit_tables != list(AUDIT_TABLE_EXCLUSIONS):
        blockers.append("dry-run audit-table protection contract is stale")
    operations = plan.plan_json.get("database_operations")
    if not isinstance(operations, list):
        blockers.append("dry-run database operations are missing")
    else:
        for sequence, operation in enumerate(operations, start=1):
            if not isinstance(operation, dict) or (
                int(operation.get("sequence") or 0) != sequence
                or operation.get("action") != "delete_rows_planned"
                or operation.get("mutation_enabled") is not False
                or not isinstance(operation.get("selector"), dict)
            ):
                blockers.append("dry-run database operation contract is invalid")
                break
    return _deduplicate(blockers)


def _planning_blockers(
    plan: DataDeletionDryRunPlan,
    planning: DataDeletionQuarantinePlanningRun | None,
) -> list[str]:
    if planning is None:
        return ["latest quarantine planning run is required"]
    blockers: list[str] = []
    if planning.contract_version != QUARANTINE_PLANNER_CONTRACT_VERSION:
        blockers.append("quarantine planning contract is unsupported")
    if planning.result_status != "passed":
        blockers.append("latest quarantine planning run must have passed")
    if planning.capacity_evidence_id is None:
        blockers.append("quarantine planning capacity evidence is missing")
    if planning.dry_run_plan_id != plan.id:
        blockers.append("quarantine planning belongs to another dry-run plan")
    if not hmac.compare_digest(
        planning.plan_fingerprint_sha256,
        plan.plan_fingerprint_sha256,
    ):
        blockers.append("quarantine planning plan fingerprint is stale")
    if planning.result_json.get("planning_status") != "passed":
        blockers.append("quarantine planning result payload is not passed")
    if (
        planning.result_json.get("safety", {}).get("execution_enabled")
        is not False
    ):
        blockers.append("quarantine planning payload unexpectedly enables execution")
    return _deduplicate(blockers)


def _prefixed_checks(prefix: str, value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return [
            _check(
                f"{prefix}.checks",
                False,
                "a structured isolated rehearsal check list",
                None,
                f"{prefix} rehearsal checks are missing",
            )
        ]
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            return [
                _check(
                    f"{prefix}.checks",
                    False,
                    "every isolated rehearsal check is an object",
                    None,
                    f"{prefix} rehearsal contains an invalid check",
                )
            ]
        copied = dict(item)
        copied["key"] = f"{prefix}.{str(item.get('key') or 'unknown')}"
        result.append(copied)
    return result


def _mysql_deletion_safe(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    safety = value.get("safety")
    return bool(
        value.get("contract_version") == "temporary-mysql-deletion-rehearsal-v1"
        and isinstance(safety, dict)
        and safety.get("temporary_tables_only") is True
        and safety.get("ordered_selectors_exercised") is True
        and safety.get("transaction_rolled_back") is True
        and safety.get("shared_data_preserved") is True
        and safety.get("audit_data_preserved") is True
        and safety.get("production_database_rows_modified") is False
        and safety.get("production_files_modified") is False
        and safety.get("deletion_performed") is False
        and safety.get("execution_enabled") is False
        and safety.get("execution_ready") is False
        and _nonnegative_int(value.get("deleted_row_count"), "deleted_row_count")
        == _nonnegative_int(
            value.get("rolled_back_row_count"),
            "rolled_back_row_count",
        )
    )


def _quarantine_result_safe(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    safety = value.get("safety")
    return bool(
        value.get("result_status") == "passed"
        and isinstance(safety, dict)
        and safety.get("synthetic_fixtures_only") is True
        and safety.get("scratch_directory_removed") is True
        and safety.get("production_source_files_opened") is False
        and safety.get("production_source_files_modified") is False
        and safety.get("production_quarantine_performed") is False
        and safety.get("database_source_rows_modified") is False
        and safety.get("deletion_performed") is False
        and safety.get("execution_enabled") is False
        and safety.get("execution_ready") is False
    )


def _combined_metrics(
    mysql_deletion: Any,
    quarantine_result: Any,
) -> dict[str, int]:
    mysql = mysql_deletion if isinstance(mysql_deletion, dict) else {}
    quarantine = (
        quarantine_result.get("metrics")
        if isinstance(quarantine_result, dict)
        and isinstance(quarantine_result.get("metrics"), dict)
        else {}
    )
    return {
        "mysql_candidate_table_count": _nonnegative_int(
            mysql.get("candidate_table_count", 0),
            "mysql_candidate_table_count",
        ),
        "mysql_candidate_row_count": _nonnegative_int(
            mysql.get("candidate_row_count", 0),
            "mysql_candidate_row_count",
        ),
        "mysql_delete_operation_count": _nonnegative_int(
            mysql.get("operation_count", 0),
            "mysql_delete_operation_count",
        ),
        "mysql_deleted_row_count": _nonnegative_int(
            mysql.get("deleted_row_count", 0),
            "mysql_deleted_row_count",
        ),
        "mysql_rolled_back_row_count": _nonnegative_int(
            mysql.get("rolled_back_row_count", 0),
            "mysql_rolled_back_row_count",
        ),
        "preserved_descriptor_count": _nonnegative_int(
            mysql.get("preserved_descriptor_count", 0),
            "preserved_descriptor_count",
        ),
        "quarantine_fixture_file_count": _nonnegative_int(
            quarantine.get("fixture_file_count", 0),
            "quarantine_fixture_file_count",
        ),
        "quarantine_recovery_case_count": _nonnegative_int(
            quarantine.get("recovery_case_count", 0),
            "quarantine_recovery_case_count",
        ),
        "quarantine_recovered_case_count": _nonnegative_int(
            quarantine.get("recovered_case_count", 0),
            "quarantine_recovered_case_count",
        ),
    }


def _check_passed(value: Any, key: str) -> bool:
    return bool(
        isinstance(value, list)
        and any(
            isinstance(item, dict)
            and item.get("key") == key
            and item.get("status") == "passed"
            for item in value
        )
    )


def _assert_locked_verification(
    row: dict[str, Any] | None,
    verification: DataDeletionBackupVerificationRun,
) -> None:
    if not row or not all(
        (
            int(row.get("id") or 0) == verification.id,
            str(row.get("contract_version") or "") == verification.contract_version,
            str(row.get("plan_fingerprint_sha256") or "")
            == verification.plan_fingerprint_sha256,
            str(row.get("result_fingerprint_sha256") or "")
            == verification.result_fingerprint_sha256,
            str(row.get("result_status") or "") == "passed",
        )
    ):
        raise DataDeletionCombinedRehearsalError(
            "backup verification changed before combined audit."
        )


def _assert_locked_planning(
    row: dict[str, Any] | None,
    planning: DataDeletionQuarantinePlanningRun,
) -> None:
    if not row or not all(
        (
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
    ):
        raise DataDeletionCombinedRehearsalError(
            "quarantine planning changed before combined audit."
        )


def _combined_run_from_row(
    row: dict[str, Any],
) -> DataDeletionCombinedRehearsalRun:
    status = str(row.get("result_status") or "")
    if status not in {"passed", "blocked"}:
        raise DataDeletionCombinedRehearsalError(
            f"unsupported combined rehearsal status: {status}."
        )
    contract = str(row.get("contract_version") or "")
    result = _json_object(row.get("result_json"), "result_json")
    fingerprint = _fingerprint(row.get("result_fingerprint_sha256"), "result")
    if not hmac.compare_digest(fingerprint, _canonical_sha256(result)):
        raise DataDeletionCombinedRehearsalError(
            "combined rehearsal result fingerprint is invalid."
        )
    safety = result.get("safety")
    if not isinstance(safety, dict) or not all(
        (
            contract == COMBINED_REHEARSAL_CONTRACT_VERSION,
            result.get("contract_version") == COMBINED_REHEARSAL_CONTRACT_VERSION,
            safety.get("temporary_mysql_tables_only") is True,
            safety.get("synthetic_quarantine_only") is True,
            safety.get("production_database_rows_modified") is False,
            safety.get("production_source_files_opened") is False,
            safety.get("production_source_files_modified") is False,
            safety.get("production_quarantine_performed") is False,
            safety.get("production_restore_performed") is False,
            safety.get("deletion_performed") is False,
            safety.get("execution_enabled") is False,
            safety.get("execution_ready") is False,
        )
    ):
        raise DataDeletionCombinedRehearsalError(
            "combined rehearsal safety contract is invalid."
        )
    checks = _checks(result)
    blockers = _blockers(result)
    metrics = _result_metrics(result)
    bindings = (
        result.get("result_status") == status,
        int(result.get("request_id") or 0) == int(row["request_id"]),
        int(result.get("dry_run_plan_id") or 0) == int(row["dry_run_plan_id"]),
        int(result.get("backup_verification_run_id") or 0)
        == int(row["backup_verification_run_id"]),
        int(result.get("quarantine_planning_run_id") or 0)
        == int(row["quarantine_planning_run_id"]),
        result.get("plan_fingerprint_sha256")
        == str(row["plan_fingerprint_sha256"]),
        result.get("backup_verification_result_fingerprint_sha256")
        == str(row["backup_verification_result_fingerprint_sha256"]),
        result.get("current_backup_revalidation_result_fingerprint_sha256")
        == row.get("current_backup_revalidation_result_fingerprint_sha256"),
        result.get("quarantine_planning_result_fingerprint_sha256")
        == str(row["quarantine_planning_result_fingerprint_sha256"]),
        result.get("destination_contract_fingerprint_sha256")
        == str(row["destination_contract_fingerprint_sha256"]),
    )
    if not all(bindings):
        raise DataDeletionCombinedRehearsalError(
            "combined rehearsal audit bindings are invalid."
        )
    for field, value in metrics.items():
        if value != _nonnegative_int(row.get(field), field):
            raise DataDeletionCombinedRehearsalError(
                "combined rehearsal metric bindings are invalid."
            )
    scratch_removed = bool(row.get("scratch_resources_removed"))
    if scratch_removed != bool(safety.get("scratch_resources_removed")):
        raise DataDeletionCombinedRehearsalError(
            "combined rehearsal cleanup binding is invalid."
        )
    passed_count = sum(item.get("status") == "passed" for item in checks)
    if (
        _nonnegative_int(row.get("check_count"), "check_count") != len(checks)
        or _nonnegative_int(row.get("passed_check_count"), "passed_check_count")
        != passed_count
        or _nonnegative_int(row.get("blocker_count"), "blocker_count")
        != len(blockers)
    ):
        raise DataDeletionCombinedRehearsalError(
            "combined rehearsal check bindings are invalid."
        )
    if status == "passed" and (
        not scratch_removed
        or blockers
        or any(item.get("status") == "blocked" for item in checks)
        or not _mysql_deletion_safe(result.get("mysql_deletion_exercise"))
        or not _quarantine_result_safe(result.get("quarantine_rehearsal"))
    ):
        raise DataDeletionCombinedRehearsalError(
            "passed combined rehearsal violates required safety invariants."
        )
    return DataDeletionCombinedRehearsalRun(
        id=_positive_int(row.get("id"), "id"),
        request_id=_positive_int(row.get("request_id"), "request_id"),
        dry_run_plan_id=_positive_int(row.get("dry_run_plan_id"), "dry_run_plan_id"),
        backup_verification_run_id=_positive_int(
            row.get("backup_verification_run_id"),
            "backup_verification_run_id",
        ),
        quarantine_planning_run_id=_positive_int(
            row.get("quarantine_planning_run_id"),
            "quarantine_planning_run_id",
        ),
        contract_version=contract,
        plan_fingerprint_sha256=_fingerprint(
            row.get("plan_fingerprint_sha256"),
            "plan fingerprint",
        ),
        backup_verification_result_fingerprint_sha256=_fingerprint(
            row.get("backup_verification_result_fingerprint_sha256"),
            "backup verification fingerprint",
        ),
        current_backup_revalidation_result_fingerprint_sha256=(
            _optional_fingerprint(
                row.get("current_backup_revalidation_result_fingerprint_sha256")
            )
        ),
        quarantine_planning_result_fingerprint_sha256=_fingerprint(
            row.get("quarantine_planning_result_fingerprint_sha256"),
            "quarantine planning fingerprint",
        ),
        destination_contract_fingerprint_sha256=_fingerprint(
            row.get("destination_contract_fingerprint_sha256"),
            "destination fingerprint",
        ),
        result_fingerprint_sha256=fingerprint,
        result_status=status,
        result_json=result,
        mysql_candidate_table_count=metrics["mysql_candidate_table_count"],
        mysql_candidate_row_count=metrics["mysql_candidate_row_count"],
        mysql_delete_operation_count=metrics["mysql_delete_operation_count"],
        mysql_deleted_row_count=metrics["mysql_deleted_row_count"],
        mysql_rolled_back_row_count=metrics["mysql_rolled_back_row_count"],
        preserved_descriptor_count=metrics["preserved_descriptor_count"],
        quarantine_fixture_file_count=metrics["quarantine_fixture_file_count"],
        quarantine_recovery_case_count=metrics[
            "quarantine_recovery_case_count"
        ],
        quarantine_recovered_case_count=metrics[
            "quarantine_recovered_case_count"
        ],
        scratch_resources_removed=scratch_removed,
        check_count=len(checks),
        passed_check_count=passed_count,
        blocker_count=len(blockers),
        run_by=_required_text(row.get("run_by"), "run_by", 191),
        rehearsal_note=_optional_text(row.get("rehearsal_note"), "rehearsal_note", 1000),
        run_at_kst=_datetime_value(row.get("run_at_kst"), "run_at_kst"),
    )


def _result_metrics(result: dict[str, Any]) -> dict[str, int]:
    value = result.get("metrics")
    if not isinstance(value, dict):
        raise DataDeletionCombinedRehearsalError(
            "combined rehearsal result metrics are missing."
        )
    return {
        key: _nonnegative_int(value.get(key), key)
        for key in (
            "mysql_candidate_table_count",
            "mysql_candidate_row_count",
            "mysql_delete_operation_count",
            "mysql_deleted_row_count",
            "mysql_rolled_back_row_count",
            "preserved_descriptor_count",
            "quarantine_fixture_file_count",
            "quarantine_recovery_case_count",
            "quarantine_recovered_case_count",
        )
    }


def _checks(result: dict[str, Any]) -> list[dict[str, Any]]:
    value = result.get("checks")
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise DataDeletionCombinedRehearsalError(
            "combined rehearsal checks are invalid."
        )
    return [dict(item) for item in value]


def _blockers(result: dict[str, Any]) -> list[str]:
    value = result.get("blockers")
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise DataDeletionCombinedRehearsalError(
            "combined rehearsal blockers are invalid."
        )
    return list(value)


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


def _json_object(value: Any, label: str) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise DataDeletionCombinedRehearsalError(f"invalid {label}.") from exc
    if not isinstance(value, dict):
        raise DataDeletionCombinedRehearsalError(f"{label} must be a JSON object.")
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


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in values if item))


def _identifier(value: Any, label: str) -> str:
    text = str(value or "")
    if not text or not text.replace("_", "a").isalnum():
        raise DataDeletionCombinedRehearsalError(f"invalid {label}.")
    return text


def _fingerprint(value: Any, label: str) -> str:
    text = str(value or "").lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise DataDeletionCombinedRehearsalError(f"invalid {label} SHA-256.")
    return text


def _optional_fingerprint(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return _fingerprint(value, "optional fingerprint")


def _positive_int(value: Any, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise DataDeletionCombinedRehearsalError(f"{label} must be an integer.") from exc
    if number <= 0:
        raise DataDeletionCombinedRehearsalError(f"{label} must be positive.")
    return number


def _nonnegative_int(value: Any, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise DataDeletionCombinedRehearsalError(f"{label} must be an integer.") from exc
    if number < 0:
        raise DataDeletionCombinedRehearsalError(f"{label} must be nonnegative.")
    return number


def _required_text(value: Any, label: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise DataDeletionCombinedRehearsalError(f"{label} is required.")
    if len(text) > maximum:
        raise DataDeletionCombinedRehearsalError(
            f"{label} must be at most {maximum} characters."
        )
    return text


def _optional_text(value: Any, label: str, maximum: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > maximum:
        raise DataDeletionCombinedRehearsalError(
            f"{label} must be at most {maximum} characters."
        )
    return text


def _datetime_value(value: Any, label: str) -> datetime:
    if isinstance(value, datetime):
        return to_kst(value)
    if isinstance(value, str):
        try:
            return to_kst(datetime.fromisoformat(value))
        except ValueError as exc:
            raise DataDeletionCombinedRehearsalError(f"invalid {label}.") from exc
    raise DataDeletionCombinedRehearsalError(f"invalid {label}.")


def _safe_error_message(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    return message[:1000]


def _begin(connection: Any) -> None:
    if hasattr(connection, "begin"):
        connection.begin()


def _commit(connection: Any) -> None:
    if hasattr(connection, "commit"):
        connection.commit()


def _rollback(connection: Any) -> None:
    if hasattr(connection, "rollback"):
        connection.rollback()
