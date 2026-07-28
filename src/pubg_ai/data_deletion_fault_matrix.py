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
from pubg_ai.data_deletion_combined_rehearsal import (
    COMBINED_REHEARSAL_CONTRACT_VERSION,
    DataDeletionCombinedRehearsalRun,
    DataDeletionCombinedRehearsalService,
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
    QUARANTINE_FAULT_POINTS,
    DataDeletionQuarantineRehearsalError,
    DataDeletionQuarantineRehearsalService,
)
from pubg_ai.data_deletion_requests import DataDeletionRequest
from pubg_ai.data_deletion_restore_rehearsal import (
    MYSQL_DELETION_FAULT_POINTS,
    run_isolated_mysql_deletion_fault_rehearsal,
)
from pubg_ai.time_utils import now_kst, to_kst


FAULT_MATRIX_CONTRACT_VERSION = "deletion-combined-fault-matrix-v1"
FAULT_MATRIX_CONFIRMATION_PREFIX = "RUN ISOLATED COMBINED FAULT MATRIX"

_FAULT_SCENARIOS = (
    {
        "sequence": 1,
        "key": "mysql_after_first_delete",
        "category": "mysql",
        "fault_point": "after_first_delete",
        "expected": (
            "a positive-row DELETE changes only a generated temporary table, "
            "the injected statement failure is observed, and ROLLBACK restores "
            "every candidate row count and row-set fingerprint"
        ),
    },
    {
        "sequence": 2,
        "key": "quarantine_after_verified_copy",
        "category": "quarantine",
        "fault_point": "after_verified_copy",
        "expected": (
            "a copied-and-verified synthetic target is rolled back to its "
            "synthetic source without opening production replay bytes"
        ),
    },
    {
        "sequence": 3,
        "key": "quarantine_after_source_removal",
        "category": "quarantine",
        "fault_point": "after_source_removal",
        "expected": (
            "a synthetic source-removal interruption restores the source from "
            "the verified synthetic target"
        ),
    },
    {
        "sequence": 4,
        "key": "quarantine_cleanup_first_attempt",
        "category": "quarantine",
        "fault_point": "cleanup_first_attempt",
        "expected": (
            "the injected first cleanup failure is detected and a guarded "
            "emergency cleanup removes the exact owned scratch directory"
        ),
    },
)


class DataDeletionFaultMatrixError(RuntimeError):
    """Raised when the isolated combined fault matrix cannot run safely."""


@dataclass(frozen=True)
class DataDeletionFaultMatrixRun:
    id: int
    request_id: int
    dry_run_plan_id: int
    backup_verification_run_id: int
    quarantine_planning_run_id: int
    combined_rehearsal_run_id: int
    contract_version: str
    plan_fingerprint_sha256: str
    backup_verification_result_fingerprint_sha256: str
    quarantine_planning_result_fingerprint_sha256: str
    destination_contract_fingerprint_sha256: str
    combined_rehearsal_result_fingerprint_sha256: str
    scenario_contract_fingerprint_sha256: str
    result_fingerprint_sha256: str
    result_status: str
    result_json: dict[str, Any]
    scenario_count: int
    passed_scenario_count: int
    contained_fault_count: int
    mysql_scenario_count: int
    quarantine_scenario_count: int
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
            "combined_rehearsal_run_id": self.combined_rehearsal_run_id,
            "contract_version": self.contract_version,
            "plan_fingerprint_sha256": self.plan_fingerprint_sha256,
            "backup_verification_result_fingerprint_sha256": (
                self.backup_verification_result_fingerprint_sha256
            ),
            "quarantine_planning_result_fingerprint_sha256": (
                self.quarantine_planning_result_fingerprint_sha256
            ),
            "destination_contract_fingerprint_sha256": (
                self.destination_contract_fingerprint_sha256
            ),
            "combined_rehearsal_result_fingerprint_sha256": (
                self.combined_rehearsal_result_fingerprint_sha256
            ),
            "scenario_contract_fingerprint_sha256": (
                self.scenario_contract_fingerprint_sha256
            ),
            "result_fingerprint_sha256": self.result_fingerprint_sha256,
            "result_status": self.result_status,
            "scenario_count": self.scenario_count,
            "passed_scenario_count": self.passed_scenario_count,
            "contained_fault_count": self.contained_fault_count,
            "mysql_scenario_count": self.mysql_scenario_count,
            "quarantine_scenario_count": self.quarantine_scenario_count,
            "scratch_resources_removed": self.scratch_resources_removed,
            "check_count": self.check_count,
            "passed_check_count": self.passed_check_count,
            "blocker_count": self.blocker_count,
            "run_by": self.run_by,
            "rehearsal_note": self.rehearsal_note,
            "run_at_kst": to_kst(self.run_at_kst).isoformat(),
            "immutable": True,
            "faults_are_deterministic_and_declared": True,
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


class DataDeletionFaultMatrixService:
    def __init__(
        self,
        connection: Any,
        *,
        backup_service: DataDeletionBackupService,
        verifier_service: DataDeletionBackupVerifierService,
        quarantine_rehearsal_service: DataDeletionQuarantineRehearsalService,
        combined_rehearsal_service: DataDeletionCombinedRehearsalService,
        scratch_connection_factory: Callable[[], Any],
        backup_root: Path,
        expected_database_name: str,
        mysql_fault_runner: Callable[..., dict[str, Any]] = (
            run_isolated_mysql_deletion_fault_rehearsal
        ),
    ) -> None:
        self.connection = connection
        self.backup_service = backup_service
        self.verifier_service = verifier_service
        self.quarantine_rehearsal_service = quarantine_rehearsal_service
        self.combined_rehearsal_service = combined_rehearsal_service
        self.scratch_connection_factory = scratch_connection_factory
        self.backup_root = backup_root.expanduser().resolve(strict=False)
        self.expected_database_name = _identifier(
            expected_database_name,
            "expected_database_name",
        )
        self.mysql_fault_runner = mysql_fault_runner

    def matrix_state(self, request: DataDeletionRequest) -> dict[str, Any]:
        plans = self.backup_service.dry_run_service.list_plans(
            request.id,
            limit=1,
        )
        plan = plans[0] if plans else None
        blockers: list[str] = []
        combined: DataDeletionCombinedRehearsalRun | None = None
        verification: DataDeletionBackupVerificationRun | None = None
        planning: DataDeletionQuarantinePlanningRun | None = None
        history: list[DataDeletionFaultMatrixRun] = []
        if request.status != "approved":
            blockers.append(f"request status must be approved, not {request.status}")
        if plan is None:
            blockers.append("latest confirmed dry-run plan is required")
        else:
            blockers.extend(_plan_blockers(plan, request))
            history = self.list_runs(plan.id, limit=50)
            combined_runs = self.combined_rehearsal_service.list_runs(
                plan.id,
                limit=1,
            )
            combined = combined_runs[0] if combined_runs else None
            if combined is None:
                blockers.append("latest passed combined rehearsal is required")
            else:
                verification = self.verifier_service.get_run(
                    combined.backup_verification_run_id
                )
                planning = (
                    self.quarantine_rehearsal_service.planner_service.get_run(
                        combined.quarantine_planning_run_id
                    )
                )
                latest_planning = (
                    self.quarantine_rehearsal_service.planner_service.list_runs(
                        plan.id,
                        limit=1,
                    )
                )
                blockers.extend(
                    _input_blockers(
                        plan,
                        combined,
                        verification,
                        planning,
                        latest_planning[0] if latest_planning else None,
                    )
                )
        blockers = _deduplicate(blockers)
        scenario_contract = fault_scenario_contract()
        scenario_fingerprint = fingerprint_fault_scenario_contract(
            scenario_contract
        )
        confirmation = (
            expected_fault_matrix_confirmation(
                request.id,
                plan.id,
                combined.id,
                combined.result_fingerprint_sha256,
                verification.id,
                verification.result_fingerprint_sha256,
                planning.id,
                planning.result_fingerprint_sha256,
                planning.destination_contract_fingerprint_sha256,
                scenario_fingerprint,
            )
            if plan is not None
            and combined is not None
            and verification is not None
            and planning is not None
            and not blockers
            else None
        )
        candidate = (
            {
                "dry_run_plan_id": plan.id,
                "combined_rehearsal": combined.to_summary_record(),
                "backup_verification": verification.to_summary_record(),
                "quarantine_planning": planning.to_summary_record(),
                "scenario_contract": scenario_contract,
                "scenario_contract_fingerprint_sha256": scenario_fingerprint,
                "confirmation_text": confirmation,
            }
            if confirmation is not None
            and plan is not None
            and combined is not None
            and verification is not None
            and planning is not None
            else None
        )
        return {
            "request_id": request.id,
            "request_status": request.status,
            "contract_version": FAULT_MATRIX_CONTRACT_VERSION,
            "latest_plan_id": plan.id if plan is not None else None,
            "fault_matrix_candidate": candidate,
            "latest_fault_matrix_run": history[0].to_record() if history else None,
            "fault_matrix_history": [
                item.to_summary_record() for item in history
            ],
            "fault_matrix_allowed": not blockers and candidate is not None,
            "fault_matrix_blockers": blockers,
            "scenario_count": len(scenario_contract),
            "appends_fault_matrix_audit_row": True,
            "appends_combined_rehearsal_audit_row": False,
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
        combined_rehearsal_run_id: int,
        confirmation_text: str,
        actor_id: str,
        note: str | None = None,
        reference_kst: datetime | None = None,
    ) -> DataDeletionFaultMatrixRun:
        combined_id = _positive_int(
            combined_rehearsal_run_id,
            "combined_rehearsal_run_id",
        )
        actor_id = _required_text(actor_id, "actor_id", 191)
        note = _optional_text(note, "note", 1000)
        if request.status != "approved":
            raise DataDeletionFaultMatrixError("request must remain approved.")
        combined = self.combined_rehearsal_service.get_run(combined_id)
        if combined.request_id != request.id:
            raise DataDeletionFaultMatrixError(
                "combined rehearsal belongs to another deletion request."
            )
        plan = self.backup_service.require_latest_plan(
            request,
            combined.dry_run_plan_id,
        )
        latest_combined = self.combined_rehearsal_service.list_runs(
            plan.id,
            limit=1,
        )
        if not latest_combined or latest_combined[0].id != combined.id:
            raise DataDeletionFaultMatrixError(
                "selected combined rehearsal is not the latest run."
            )
        verification = self.verifier_service.get_run(
            combined.backup_verification_run_id
        )
        planning = self.quarantine_rehearsal_service.planner_service.get_run(
            combined.quarantine_planning_run_id
        )
        latest_planning = (
            self.quarantine_rehearsal_service.planner_service.list_runs(
                plan.id,
                limit=1,
            )
        )
        blockers = _plan_blockers(plan, request)
        blockers.extend(
            _input_blockers(
                plan,
                combined,
                verification,
                planning,
                latest_planning[0] if latest_planning else None,
            )
        )
        blockers = _deduplicate(blockers)
        if blockers:
            raise DataDeletionFaultMatrixError(
                "fault matrix inputs are blocked: " + "; ".join(blockers)
            )
        scenario_contract = fault_scenario_contract()
        scenario_fingerprint = fingerprint_fault_scenario_contract(
            scenario_contract
        )
        expected_confirmation = expected_fault_matrix_confirmation(
            request.id,
            plan.id,
            combined.id,
            combined.result_fingerprint_sha256,
            verification.id,
            verification.result_fingerprint_sha256,
            planning.id,
            planning.result_fingerprint_sha256,
            planning.destination_contract_fingerprint_sha256,
            scenario_fingerprint,
        )
        supplied_confirmation = _required_text(
            confirmation_text,
            "confirmation_text",
            900,
        )
        if not hmac.compare_digest(supplied_confirmation, expected_confirmation):
            raise DataDeletionFaultMatrixError(
                "fault matrix confirmation does not match all selected inputs."
            )

        run_at = to_kst(reference_kst or now_kst())
        checks: list[dict[str, Any]] = [
            _check(
                "fault_matrix_input_binding",
                True,
                "latest plan, latest passed combined rehearsal, verification, and planning",
                {
                    "dry_run_plan_id": plan.id,
                    "combined_rehearsal_run_id": combined.id,
                    "backup_verification_run_id": verification.id,
                    "quarantine_planning_run_id": planning.id,
                },
                "all immutable fault-matrix inputs are current and fingerprint-bound",
            ),
            _check(
                "fault_scenario_contract",
                True,
                "the fixed ordered v1 fault scenario contract",
                {
                    "scenario_count": len(scenario_contract),
                    "fingerprint_sha256": scenario_fingerprint,
                },
                "the deterministic fault scenario contract is bound",
            ),
        ]
        scenarios: list[dict[str, Any]] = []
        revalidated: Any | None = None
        revalidation_error: Exception | None = None
        try:
            revalidated = self.verifier_service.revalidate_passed_run(
                request,
                verification.id,
                reference_kst=run_at,
            )
            if revalidated.plan.id != plan.id or not hmac.compare_digest(
                revalidated.plan.plan_fingerprint_sha256,
                plan.plan_fingerprint_sha256,
            ):
                raise DataDeletionFaultMatrixError(
                    "backup revalidation returned another dry-run plan."
                )
        except Exception as exc:
            revalidation_error = exc

        mysql_spec = scenario_contract[0]
        if revalidated is None:
            scenarios.append(
                _blocked_scenario(
                    mysql_spec,
                    "backup revalidation failed before the MySQL fault scenario: "
                    + _safe_error_message(
                        revalidation_error or RuntimeError("unknown revalidation error")
                    ),
                )
            )
        else:
            try:
                mysql_result = self.mysql_fault_runner(
                    audit_connection=self.connection,
                    scratch_connection_factory=self.scratch_connection_factory,
                    backup_root=self.backup_root,
                    expected_database_name=self.expected_database_name,
                    revalidated=revalidated,
                    fault_point=str(mysql_spec["fault_point"]),
                )
                scenarios.append(_mysql_scenario(mysql_spec, mysql_result))
            except Exception as exc:
                scenarios.append(_blocked_scenario(mysql_spec, _safe_error_message(exc)))

        for spec in scenario_contract[1:]:
            try:
                bound_plan, bound_planning, quarantine_result = (
                    self.quarantine_rehearsal_service.run_bound_synthetic_fault_state(
                        request,
                        quarantine_planning_run_id=planning.id,
                        fault_point=str(spec["fault_point"]),
                        reference_kst=run_at,
                    )
                )
                if bound_plan.id != plan.id or bound_planning.id != planning.id:
                    raise DataDeletionFaultMatrixError(
                        "synthetic fault scenario returned another input binding."
                    )
                scenarios.append(
                    _quarantine_scenario(spec, quarantine_result)
                )
            except Exception as exc:
                scenarios.append(_blocked_scenario(spec, _safe_error_message(exc)))

        for scenario in scenarios:
            checks.append(
                _check(
                    f"scenario.{scenario['key']}",
                    scenario.get("status") == "passed",
                    scenario.get("expected"),
                    {
                        "fault_point": scenario.get("fault_point"),
                        "fault_observed": scenario.get("fault_observed"),
                        "fault_contained": scenario.get("fault_contained"),
                        "scratch_removed": scenario.get("scratch_removed"),
                    },
                    str(scenario.get("message") or scenario.get("status")),
                )
            )
        all_contained = bool(
            len(scenarios) == len(scenario_contract)
            and all(
                item.get("status") == "passed"
                and item.get("fault_observed") is True
                and item.get("fault_contained") is True
                and item.get("scratch_removed") is True
                for item in scenarios
            )
        )
        checks.append(
            _check(
                "all_declared_faults_contained",
                all_contained,
                "every declared fault is observed, contained, rolled back or recovered, and cleaned",
                {
                    "scenario_count": len(scenarios),
                    "contained_count": sum(
                        item.get("fault_contained") is True for item in scenarios
                    ),
                },
                (
                    "all deterministic faults were contained and cleaned"
                    if all_contained
                    else "one or more deterministic faults were not safely contained"
                ),
            )
        )
        checks.append(
            _check(
                "production_mutation_boundary",
                all(
                    item.get("production_data_unchanged") is True
                    for item in scenarios
                ),
                "all writes remain inside generated temporary tables or synthetic scratch directories",
                {
                    "production_database_rows_modified": False,
                    "production_source_files_opened": False,
                    "production_source_files_modified": False,
                },
                "no fault scenario crossed the production mutation boundary",
            )
        )
        blockers = [
            str(item.get("message") or item.get("key") or "blocked check")
            for item in checks
            if item.get("status") == "blocked"
        ]
        status = "passed" if not blockers else "blocked"
        metrics = _matrix_metrics(scenarios)
        scratch_removed = bool(
            scenarios
            and all(item.get("scratch_removed") is True for item in scenarios)
        )
        result = {
            "contract_version": FAULT_MATRIX_CONTRACT_VERSION,
            "request_id": request.id,
            "dry_run_plan_id": plan.id,
            "backup_verification_run_id": verification.id,
            "quarantine_planning_run_id": planning.id,
            "combined_rehearsal_run_id": combined.id,
            "plan_fingerprint_sha256": plan.plan_fingerprint_sha256,
            "backup_verification_result_fingerprint_sha256": (
                verification.result_fingerprint_sha256
            ),
            "quarantine_planning_result_fingerprint_sha256": (
                planning.result_fingerprint_sha256
            ),
            "destination_contract_fingerprint_sha256": (
                planning.destination_contract_fingerprint_sha256
            ),
            "combined_rehearsal_result_fingerprint_sha256": (
                combined.result_fingerprint_sha256
            ),
            "scenario_contract": scenario_contract,
            "scenario_contract_fingerprint_sha256": scenario_fingerprint,
            "scenarios": scenarios,
            "checks": checks,
            "metrics": metrics,
            "blockers": blockers,
            "result_status": status,
            "run_at_kst": run_at.isoformat(),
            "safety": {
                "deterministic_declared_faults_only": True,
                "temporary_mysql_tables_only": True,
                "synthetic_quarantine_only": True,
                "scratch_resources_removed": scratch_removed,
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
            combined,
            result=result,
            result_fingerprint=result_fingerprint,
            metrics=metrics,
            scratch_removed=scratch_removed,
            actor_id=actor_id,
            note=note,
            run_at_kst=run_at,
        )

    def get_run(self, run_id: int) -> DataDeletionFaultMatrixRun:
        matrix_id = _positive_int(run_id, "run_id")
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM data_deletion_combined_fault_matrix_runs WHERE id = %s",
                (matrix_id,),
            )
            row = cursor.fetchone()
        if not row:
            raise DataDeletionFaultMatrixError(
                f"fault matrix run {matrix_id} was not found."
            )
        return _fault_matrix_run_from_row(row)

    def list_runs(
        self,
        dry_run_plan_id: int,
        *,
        limit: int = 50,
    ) -> list[DataDeletionFaultMatrixRun]:
        plan_id = _positive_int(dry_run_plan_id, "dry_run_plan_id")
        if not 1 <= int(limit) <= 100:
            raise DataDeletionFaultMatrixError(
                "fault matrix history limit must be between 1 and 100."
            )
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM data_deletion_combined_fault_matrix_runs
                WHERE dry_run_plan_id = %s
                ORDER BY run_at_kst DESC, id DESC
                LIMIT %s
                """,
                (plan_id, int(limit)),
            )
            rows = cursor.fetchall()
        return [_fault_matrix_run_from_row(row) for row in rows]

    def _record_run(
        self,
        request: DataDeletionRequest,
        plan: DataDeletionDryRunPlan,
        verification: DataDeletionBackupVerificationRun,
        planning: DataDeletionQuarantinePlanningRun,
        combined: DataDeletionCombinedRehearsalRun,
        *,
        result: dict[str, Any],
        result_fingerprint: str,
        metrics: dict[str, int],
        scratch_removed: bool,
        actor_id: str,
        note: str | None,
        run_at_kst: datetime,
    ) -> DataDeletionFaultMatrixRun:
        checks = _checks(result)
        blockers = _blockers(result)
        status = str(result.get("result_status") or "")
        if status not in {"passed", "blocked"}:
            raise DataDeletionFaultMatrixError(
                "fault matrix result status is invalid."
            )
        _begin(self.connection)
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, contract_version, dry_run_plan_id,
                           backup_verification_run_id,
                           quarantine_planning_run_id,
                           plan_fingerprint_sha256,
                           result_fingerprint_sha256, result_status,
                           scratch_resources_removed
                    FROM data_deletion_combined_rehearsal_runs
                    WHERE dry_run_plan_id = %s
                    ORDER BY run_at_kst DESC, id DESC
                    LIMIT 1
                    FOR UPDATE
                    """,
                    (plan.id,),
                )
                _assert_locked_combined(cursor.fetchone(), combined)
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
                    INSERT INTO data_deletion_combined_fault_matrix_runs (
                        request_id,
                        dry_run_plan_id,
                        backup_verification_run_id,
                        quarantine_planning_run_id,
                        combined_rehearsal_run_id,
                        contract_version,
                        plan_fingerprint_sha256,
                        backup_verification_result_fingerprint_sha256,
                        quarantine_planning_result_fingerprint_sha256,
                        destination_contract_fingerprint_sha256,
                        combined_rehearsal_result_fingerprint_sha256,
                        scenario_contract_fingerprint_sha256,
                        result_fingerprint_sha256,
                        result_status,
                        result_json,
                        scenario_count,
                        passed_scenario_count,
                        contained_fault_count,
                        mysql_scenario_count,
                        quarantine_scenario_count,
                        scratch_resources_removed,
                        check_count,
                        passed_check_count,
                        blocker_count,
                        run_by,
                        rehearsal_note,
                        run_at_kst
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        request.id,
                        plan.id,
                        verification.id,
                        planning.id,
                        combined.id,
                        FAULT_MATRIX_CONTRACT_VERSION,
                        plan.plan_fingerprint_sha256,
                        verification.result_fingerprint_sha256,
                        planning.result_fingerprint_sha256,
                        planning.destination_contract_fingerprint_sha256,
                        combined.result_fingerprint_sha256,
                        result["scenario_contract_fingerprint_sha256"],
                        result_fingerprint,
                        status,
                        _json_dump(result),
                        metrics["scenario_count"],
                        metrics["passed_scenario_count"],
                        metrics["contained_fault_count"],
                        metrics["mysql_scenario_count"],
                        metrics["quarantine_scenario_count"],
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
        return DataDeletionFaultMatrixRun(
            id=run_id,
            request_id=request.id,
            dry_run_plan_id=plan.id,
            backup_verification_run_id=verification.id,
            quarantine_planning_run_id=planning.id,
            combined_rehearsal_run_id=combined.id,
            contract_version=FAULT_MATRIX_CONTRACT_VERSION,
            plan_fingerprint_sha256=plan.plan_fingerprint_sha256,
            backup_verification_result_fingerprint_sha256=(
                verification.result_fingerprint_sha256
            ),
            quarantine_planning_result_fingerprint_sha256=(
                planning.result_fingerprint_sha256
            ),
            destination_contract_fingerprint_sha256=(
                planning.destination_contract_fingerprint_sha256
            ),
            combined_rehearsal_result_fingerprint_sha256=(
                combined.result_fingerprint_sha256
            ),
            scenario_contract_fingerprint_sha256=result[
                "scenario_contract_fingerprint_sha256"
            ],
            result_fingerprint_sha256=result_fingerprint,
            result_status=status,
            result_json=result,
            scenario_count=metrics["scenario_count"],
            passed_scenario_count=metrics["passed_scenario_count"],
            contained_fault_count=metrics["contained_fault_count"],
            mysql_scenario_count=metrics["mysql_scenario_count"],
            quarantine_scenario_count=metrics["quarantine_scenario_count"],
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


def fault_scenario_contract() -> list[dict[str, Any]]:
    return [dict(item) for item in _FAULT_SCENARIOS]


def fingerprint_fault_scenario_contract(value: Any) -> str:
    if not isinstance(value, list):
        raise DataDeletionFaultMatrixError(
            "fault scenario contract must be a list."
        )
    return _canonical_sha256(value)


def expected_fault_matrix_confirmation(
    request_id: int,
    dry_run_plan_id: int,
    combined_rehearsal_run_id: int,
    combined_rehearsal_result_fingerprint_sha256: str,
    backup_verification_run_id: int,
    backup_verification_result_fingerprint_sha256: str,
    quarantine_planning_run_id: int,
    quarantine_planning_result_fingerprint_sha256: str,
    destination_contract_fingerprint_sha256: str,
    scenario_contract_fingerprint_sha256: str,
) -> str:
    return (
        f"{FAULT_MATRIX_CONFIRMATION_PREFIX} REQUEST "
        f"{_positive_int(request_id, 'request_id')} PLAN "
        f"{_positive_int(dry_run_plan_id, 'dry_run_plan_id')} COMBINED "
        f"{_positive_int(combined_rehearsal_run_id, 'combined_rehearsal_run_id')} "
        f"{_fingerprint(combined_rehearsal_result_fingerprint_sha256, 'combined result')} "
        f"VERIFICATION "
        f"{_positive_int(backup_verification_run_id, 'backup_verification_run_id')} "
        f"{_fingerprint(backup_verification_result_fingerprint_sha256, 'verification result')} "
        f"QUARANTINE PLAN "
        f"{_positive_int(quarantine_planning_run_id, 'quarantine_planning_run_id')} "
        f"{_fingerprint(quarantine_planning_result_fingerprint_sha256, 'planning result')} "
        f"DESTINATION "
        f"{_fingerprint(destination_contract_fingerprint_sha256, 'destination contract')} "
        f"SCENARIOS "
        f"{_fingerprint(scenario_contract_fingerprint_sha256, 'scenario contract')}"
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
    positive_operation = False
    if not isinstance(operations, list):
        blockers.append("dry-run database operations are missing")
    else:
        for sequence, operation in enumerate(operations, start=1):
            if not isinstance(operation, dict):
                blockers.append("dry-run database operation contract is invalid")
                break
            try:
                valid = bool(
                    int(operation.get("sequence") or 0) == sequence
                    and operation.get("action") == "delete_rows_planned"
                    and operation.get("mutation_enabled") is False
                    and isinstance(operation.get("selector"), dict)
                )
                positive_operation = positive_operation or int(
                    operation.get("estimated_rows") or 0
                ) > 0
            except (TypeError, ValueError):
                valid = False
            if not valid:
                blockers.append("dry-run database operation contract is invalid")
                break
    if not positive_operation:
        blockers.append(
            "fault matrix requires at least one positive-row MySQL candidate operation"
        )
    return _deduplicate(blockers)


def _input_blockers(
    plan: DataDeletionDryRunPlan,
    combined: DataDeletionCombinedRehearsalRun,
    verification: DataDeletionBackupVerificationRun,
    planning: DataDeletionQuarantinePlanningRun,
    latest_planning: DataDeletionQuarantinePlanningRun | None,
) -> list[str]:
    blockers: list[str] = []
    if combined.contract_version != COMBINED_REHEARSAL_CONTRACT_VERSION:
        blockers.append("combined rehearsal contract is unsupported")
    if combined.result_status != "passed" or not combined.scratch_resources_removed:
        blockers.append("latest combined rehearsal must have passed and cleaned up")
    if combined.dry_run_plan_id != plan.id or not hmac.compare_digest(
        combined.plan_fingerprint_sha256,
        plan.plan_fingerprint_sha256,
    ):
        blockers.append("combined rehearsal dry-run plan binding is stale")
    combined_safety = combined.result_json.get("safety")
    if not isinstance(combined_safety, dict) or not all(
        (
            combined_safety.get("production_database_rows_modified") is False,
            combined_safety.get("production_source_files_opened") is False,
            combined_safety.get("production_source_files_modified") is False,
            combined_safety.get("deletion_performed") is False,
            combined_safety.get("execution_enabled") is False,
            combined_safety.get("execution_ready") is False,
        )
    ):
        blockers.append("combined rehearsal safety contract is invalid")
    if verification.contract_version != BACKUP_VERIFIER_CONTRACT_VERSION:
        blockers.append("backup verification contract is unsupported")
    if verification.result_status != "passed":
        blockers.append("backup verification must have passed")
    if verification.id != combined.backup_verification_run_id:
        blockers.append("backup verification differs from the combined rehearsal")
    if verification.dry_run_plan_id != plan.id or not hmac.compare_digest(
        verification.plan_fingerprint_sha256,
        plan.plan_fingerprint_sha256,
    ):
        blockers.append("backup verification plan binding is stale")
    if not hmac.compare_digest(
        verification.result_fingerprint_sha256,
        combined.backup_verification_result_fingerprint_sha256,
    ):
        blockers.append("backup verification result differs from the combined rehearsal")
    if planning.contract_version != QUARANTINE_PLANNER_CONTRACT_VERSION:
        blockers.append("quarantine planning contract is unsupported")
    if planning.result_status != "passed" or planning.capacity_evidence_id is None:
        blockers.append("quarantine planning must have passed with capacity evidence")
    if latest_planning is None or latest_planning.id != planning.id:
        blockers.append("selected quarantine planning run is not the latest run")
    if planning.id != combined.quarantine_planning_run_id:
        blockers.append("quarantine planning differs from the combined rehearsal")
    if planning.dry_run_plan_id != plan.id or not hmac.compare_digest(
        planning.plan_fingerprint_sha256,
        plan.plan_fingerprint_sha256,
    ):
        blockers.append("quarantine planning plan binding is stale")
    if not hmac.compare_digest(
        planning.result_fingerprint_sha256,
        combined.quarantine_planning_result_fingerprint_sha256,
    ):
        blockers.append("quarantine planning result differs from the combined rehearsal")
    if not hmac.compare_digest(
        planning.destination_contract_fingerprint_sha256,
        combined.destination_contract_fingerprint_sha256,
    ):
        blockers.append("quarantine destination differs from the combined rehearsal")
    operations = planning.result_json.get("file_operations")
    if not isinstance(operations, list) or not operations:
        blockers.append(
            "fault matrix requires at least one synthetic quarantine fixture operation"
        )
    return _deduplicate(blockers)


def _mysql_scenario(
    spec: dict[str, Any],
    runner_result: Any,
) -> dict[str, Any]:
    result = runner_result if isinstance(runner_result, dict) else {}
    deletion = result.get("deletion_exercise")
    deletion = deletion if isinstance(deletion, dict) else {}
    fault = deletion.get("fault_injection")
    fault = fault if isinstance(fault, dict) else {}
    safety = deletion.get("safety")
    safety = safety if isinstance(safety, dict) else {}
    checks = result.get("checks")
    checks = checks if isinstance(checks, list) else []
    scratch_removed = _check_passed(checks, "scratch_cleanup")
    deleted_rows = _safe_nonnegative(deletion.get("deleted_row_count"))
    rolled_back_rows = _safe_nonnegative(deletion.get("rolled_back_row_count"))
    safe = bool(
        deletion.get("contract_version")
        == "temporary-mysql-deletion-fault-rehearsal-v1"
        and fault.get("requested") is True
        and fault.get("fault_point") == spec.get("fault_point")
        and fault.get("observed") is True
        and fault.get("temporary_state_changed") is True
        and deleted_rows > 0
        and rolled_back_rows == deleted_rows
        and deletion.get("baseline") == deletion.get("after_rollback")
        and safety.get("temporary_tables_only") is True
        and safety.get("injected_statement_failure_exercised") is True
        and safety.get("transaction_rolled_back") is True
        and safety.get("shared_data_preserved") is True
        and safety.get("audit_data_preserved") is True
        and safety.get("production_database_rows_modified") is False
        and safety.get("production_files_modified") is False
        and safety.get("deletion_performed") is False
        and safety.get("execution_enabled") is False
        and safety.get("execution_ready") is False
        and scratch_removed
        and not any(
            isinstance(item, dict) and item.get("status") == "blocked"
            for item in checks
        )
    )
    return {
        **dict(spec),
        "status": "passed" if safe else "blocked",
        "fault_observed": fault.get("observed") is True,
        "fault_contained": safe,
        "scratch_removed": scratch_removed,
        "production_data_unchanged": bool(
            safety.get("production_database_rows_modified") is False
            and safety.get("production_files_modified") is False
        ),
        "message": (
            "temporary DELETE fault rolled back and all scratch tables were removed"
            if safe
            else "temporary DELETE fault did not satisfy rollback and cleanup invariants"
        ),
        "result": result,
    }


def _quarantine_scenario(
    spec: dict[str, Any],
    result_value: Any,
) -> dict[str, Any]:
    result = result_value if isinstance(result_value, dict) else {}
    fault = result.get("fault_injection")
    fault = fault if isinstance(fault, dict) else {}
    safety = result.get("safety")
    safety = safety if isinstance(safety, dict) else {}
    checks = result.get("checks")
    checks = checks if isinstance(checks, list) else []
    cleanup_fault = spec.get("fault_point") == "cleanup_first_attempt"
    safe = bool(
        result.get("result_status") == "passed"
        and fault.get("fault_point") == spec.get("fault_point")
        and fault.get("observed") is True
        and fault.get("recovered") is True
        and (
            not cleanup_fault
            or fault.get("emergency_cleanup_passed") is True
        )
        and safety.get("synthetic_fixtures_only") is True
        and safety.get("scratch_directory_removed") is True
        and safety.get("production_source_files_opened") is False
        and safety.get("production_source_files_modified") is False
        and safety.get("production_quarantine_performed") is False
        and safety.get("database_source_rows_modified") is False
        and safety.get("deletion_performed") is False
        and safety.get("execution_enabled") is False
        and safety.get("execution_ready") is False
        and not any(
            isinstance(item, dict) and item.get("status") == "blocked"
            for item in checks
        )
    )
    return {
        **dict(spec),
        "status": "passed" if safe else "blocked",
        "fault_observed": fault.get("observed") is True,
        "fault_contained": safe,
        "scratch_removed": safety.get("scratch_directory_removed") is True,
        "production_data_unchanged": bool(
            safety.get("production_source_files_opened") is False
            and safety.get("production_source_files_modified") is False
            and safety.get("database_source_rows_modified") is False
        ),
        "message": (
            "synthetic quarantine fault recovered and owned scratch was removed"
            if safe
            else "synthetic quarantine fault did not satisfy recovery and cleanup invariants"
        ),
        "result": result,
    }


def _blocked_scenario(spec: dict[str, Any], message: str) -> dict[str, Any]:
    return {
        **dict(spec),
        "status": "blocked",
        "fault_observed": False,
        "fault_contained": False,
        "scratch_removed": False,
        "production_data_unchanged": False,
        "message": message,
        "result": None,
    }


def _matrix_metrics(scenarios: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "scenario_count": len(scenarios),
        "passed_scenario_count": sum(
            item.get("status") == "passed" for item in scenarios
        ),
        "contained_fault_count": sum(
            item.get("fault_contained") is True for item in scenarios
        ),
        "mysql_scenario_count": sum(
            item.get("category") == "mysql" for item in scenarios
        ),
        "quarantine_scenario_count": sum(
            item.get("category") == "quarantine" for item in scenarios
        ),
    }


def _assert_locked_combined(
    row: dict[str, Any] | None,
    combined: DataDeletionCombinedRehearsalRun,
) -> None:
    if not row or not all(
        (
            int(row.get("id") or 0) == combined.id,
            str(row.get("contract_version") or "") == combined.contract_version,
            int(row.get("dry_run_plan_id") or 0) == combined.dry_run_plan_id,
            int(row.get("backup_verification_run_id") or 0)
            == combined.backup_verification_run_id,
            int(row.get("quarantine_planning_run_id") or 0)
            == combined.quarantine_planning_run_id,
            str(row.get("plan_fingerprint_sha256") or "")
            == combined.plan_fingerprint_sha256,
            str(row.get("result_fingerprint_sha256") or "")
            == combined.result_fingerprint_sha256,
            str(row.get("result_status") or "") == "passed",
            bool(row.get("scratch_resources_removed")) is True,
        )
    ):
        raise DataDeletionFaultMatrixError(
            "combined rehearsal changed before fault-matrix audit."
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
        raise DataDeletionFaultMatrixError(
            "backup verification changed before fault-matrix audit."
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
        raise DataDeletionFaultMatrixError(
            "quarantine planning changed before fault-matrix audit."
        )


def _fault_matrix_run_from_row(row: dict[str, Any]) -> DataDeletionFaultMatrixRun:
    status = str(row.get("result_status") or "")
    if status not in {"passed", "blocked"}:
        raise DataDeletionFaultMatrixError(
            f"unsupported fault matrix status: {status}."
        )
    contract = str(row.get("contract_version") or "")
    result = _json_object(row.get("result_json"), "result_json")
    fingerprint = _fingerprint(row.get("result_fingerprint_sha256"), "result")
    if not hmac.compare_digest(fingerprint, _canonical_sha256(result)):
        raise DataDeletionFaultMatrixError(
            "fault matrix result fingerprint is invalid."
        )
    scenario_contract = result.get("scenario_contract")
    scenario_fingerprint = _fingerprint(
        result.get("scenario_contract_fingerprint_sha256"),
        "scenario contract",
    )
    expected_scenarios = fault_scenario_contract()
    if (
        scenario_contract != expected_scenarios
        or not hmac.compare_digest(
            scenario_fingerprint,
            fingerprint_fault_scenario_contract(expected_scenarios),
        )
        or not hmac.compare_digest(
            scenario_fingerprint,
            str(row.get("scenario_contract_fingerprint_sha256") or ""),
        )
    ):
        raise DataDeletionFaultMatrixError(
            "fault matrix scenario contract is invalid."
        )
    safety = result.get("safety")
    if not isinstance(safety, dict) or not all(
        (
            contract == FAULT_MATRIX_CONTRACT_VERSION,
            result.get("contract_version") == FAULT_MATRIX_CONTRACT_VERSION,
            safety.get("deterministic_declared_faults_only") is True,
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
        raise DataDeletionFaultMatrixError(
            "fault matrix safety contract is invalid."
        )
    scenarios = result.get("scenarios")
    if not isinstance(scenarios, list) or any(
        not isinstance(item, dict) for item in scenarios
    ):
        raise DataDeletionFaultMatrixError("fault matrix scenarios are invalid.")
    scenario_bindings = [
        (
            item.get("sequence"),
            item.get("key"),
            item.get("category"),
            item.get("fault_point"),
            item.get("expected"),
        )
        for item in scenarios
    ]
    expected_bindings = [
        (
            item["sequence"],
            item["key"],
            item["category"],
            item["fault_point"],
            item["expected"],
        )
        for item in expected_scenarios
    ]
    if scenario_bindings != expected_bindings:
        raise DataDeletionFaultMatrixError(
            "fault matrix scenario result ordering is invalid."
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
        int(result.get("combined_rehearsal_run_id") or 0)
        == int(row["combined_rehearsal_run_id"]),
        result.get("plan_fingerprint_sha256")
        == str(row["plan_fingerprint_sha256"]),
        result.get("backup_verification_result_fingerprint_sha256")
        == str(row["backup_verification_result_fingerprint_sha256"]),
        result.get("quarantine_planning_result_fingerprint_sha256")
        == str(row["quarantine_planning_result_fingerprint_sha256"]),
        result.get("destination_contract_fingerprint_sha256")
        == str(row["destination_contract_fingerprint_sha256"]),
        result.get("combined_rehearsal_result_fingerprint_sha256")
        == str(row["combined_rehearsal_result_fingerprint_sha256"]),
    )
    if not all(bindings):
        raise DataDeletionFaultMatrixError(
            "fault matrix audit bindings are invalid."
        )
    for field, value in metrics.items():
        if value != _nonnegative_int(row.get(field), field):
            raise DataDeletionFaultMatrixError(
                "fault matrix metric bindings are invalid."
            )
    scratch_removed = bool(row.get("scratch_resources_removed"))
    if scratch_removed != bool(safety.get("scratch_resources_removed")):
        raise DataDeletionFaultMatrixError(
            "fault matrix cleanup binding is invalid."
        )
    passed_count = sum(item.get("status") == "passed" for item in checks)
    if (
        _nonnegative_int(row.get("check_count"), "check_count") != len(checks)
        or _nonnegative_int(row.get("passed_check_count"), "passed_check_count")
        != passed_count
        or _nonnegative_int(row.get("blocker_count"), "blocker_count")
        != len(blockers)
    ):
        raise DataDeletionFaultMatrixError(
            "fault matrix check bindings are invalid."
        )
    if status == "passed" and (
        not scratch_removed
        or blockers
        or passed_count != len(checks)
        or metrics["scenario_count"] != len(expected_scenarios)
        or metrics["passed_scenario_count"] != len(expected_scenarios)
        or metrics["contained_fault_count"] != len(expected_scenarios)
        or any(
            item.get("status") != "passed"
            or item.get("fault_observed") is not True
            or item.get("fault_contained") is not True
            or item.get("scratch_removed") is not True
            or item.get("production_data_unchanged") is not True
            for item in scenarios
        )
    ):
        raise DataDeletionFaultMatrixError(
            "passed fault matrix violates required safety invariants."
        )
    return DataDeletionFaultMatrixRun(
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
        combined_rehearsal_run_id=_positive_int(
            row.get("combined_rehearsal_run_id"),
            "combined_rehearsal_run_id",
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
        quarantine_planning_result_fingerprint_sha256=_fingerprint(
            row.get("quarantine_planning_result_fingerprint_sha256"),
            "quarantine planning fingerprint",
        ),
        destination_contract_fingerprint_sha256=_fingerprint(
            row.get("destination_contract_fingerprint_sha256"),
            "destination fingerprint",
        ),
        combined_rehearsal_result_fingerprint_sha256=_fingerprint(
            row.get("combined_rehearsal_result_fingerprint_sha256"),
            "combined rehearsal fingerprint",
        ),
        scenario_contract_fingerprint_sha256=scenario_fingerprint,
        result_fingerprint_sha256=fingerprint,
        result_status=status,
        result_json=result,
        scenario_count=metrics["scenario_count"],
        passed_scenario_count=metrics["passed_scenario_count"],
        contained_fault_count=metrics["contained_fault_count"],
        mysql_scenario_count=metrics["mysql_scenario_count"],
        quarantine_scenario_count=metrics["quarantine_scenario_count"],
        scratch_resources_removed=scratch_removed,
        check_count=len(checks),
        passed_check_count=passed_count,
        blocker_count=len(blockers),
        run_by=_required_text(row.get("run_by"), "run_by", 191),
        rehearsal_note=_optional_text(
            row.get("rehearsal_note"),
            "rehearsal_note",
            1000,
        ),
        run_at_kst=_datetime_value(row.get("run_at_kst"), "run_at_kst"),
    )


def _result_metrics(result: dict[str, Any]) -> dict[str, int]:
    value = result.get("metrics")
    if not isinstance(value, dict):
        raise DataDeletionFaultMatrixError("fault matrix metrics are missing.")
    return {
        key: _nonnegative_int(value.get(key), key)
        for key in (
            "scenario_count",
            "passed_scenario_count",
            "contained_fault_count",
            "mysql_scenario_count",
            "quarantine_scenario_count",
        )
    }


def _checks(result: dict[str, Any]) -> list[dict[str, Any]]:
    value = result.get("checks")
    if not isinstance(value, list) or any(
        not isinstance(item, dict) for item in value
    ):
        raise DataDeletionFaultMatrixError("fault matrix checks are invalid.")
    return [dict(item) for item in value]


def _blockers(result: dict[str, Any]) -> list[str]:
    value = result.get("blockers")
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise DataDeletionFaultMatrixError("fault matrix blockers are invalid.")
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


def _safe_nonnegative(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return -1
    return number if number >= 0 else -1


def _json_object(value: Any, label: str) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise DataDeletionFaultMatrixError(f"invalid {label}.") from exc
    if not isinstance(value, dict):
        raise DataDeletionFaultMatrixError(f"{label} must be a JSON object.")
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
        raise DataDeletionFaultMatrixError(f"invalid {label}.")
    return text


def _fingerprint(value: Any, label: str) -> str:
    text = str(value or "").lower()
    if len(text) != 64 or any(
        character not in "0123456789abcdef" for character in text
    ):
        raise DataDeletionFaultMatrixError(f"invalid {label} SHA-256.")
    return text


def _positive_int(value: Any, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise DataDeletionFaultMatrixError(
            f"{label} must be an integer."
        ) from exc
    if number <= 0:
        raise DataDeletionFaultMatrixError(f"{label} must be positive.")
    return number


def _nonnegative_int(value: Any, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise DataDeletionFaultMatrixError(
            f"{label} must be an integer."
        ) from exc
    if number < 0:
        raise DataDeletionFaultMatrixError(f"{label} must be nonnegative.")
    return number


def _required_text(value: Any, label: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise DataDeletionFaultMatrixError(f"{label} is required.")
    if len(text) > maximum:
        raise DataDeletionFaultMatrixError(
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
        raise DataDeletionFaultMatrixError(
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
            raise DataDeletionFaultMatrixError(f"invalid {label}.") from exc
    raise DataDeletionFaultMatrixError(f"invalid {label}.")


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
