from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping
import hashlib
import hmac
import json
import re

from pubg_ai.data_deletion_backup import DataDeletionBackupService
from pubg_ai.data_deletion_backup_verifier import (
    BACKUP_VERIFIER_CONTRACT_VERSION,
    DataDeletionBackupVerificationRun,
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
from pubg_ai.data_deletion_fault_matrix import (
    FAULT_MATRIX_CONTRACT_VERSION,
    DataDeletionFaultMatrixRun,
    DataDeletionFaultMatrixService,
    fault_scenario_contract,
    fingerprint_fault_scenario_contract,
)
from pubg_ai.data_deletion_quarantine_planner import (
    QUARANTINE_PLANNER_CONTRACT_VERSION,
    DataDeletionQuarantinePlannerService,
    DataDeletionQuarantinePlanningRun,
)
from pubg_ai.data_deletion_requests import DataDeletionRequest
from pubg_ai.time_utils import now_kst, to_kst


REVIEW_PACKET_CONTRACT_VERSION = "deletion-readiness-review-packet-v1"
REVIEW_PACKET_CONFIRMATION_PREFIX = "GENERATE ADVISORY DELETION REVIEW PACKET"
REVIEW_PACKET_KIND = "advisory_deletion_readiness_review"
REVIEW_STATUS_PASSED = "advisory_checks_passed"
REVIEW_STATUS_BLOCKED = "advisory_checks_blocked"
REVIEW_PACKET_INPUT_COUNT = 6

_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_EXECUTION_BLOCKERS = (
    "review_packet_is_advisory_only",
    "executor_not_implemented",
)


class DataDeletionReviewPacketError(RuntimeError):
    """Raised when an advisory deletion review packet cannot be produced safely."""


@dataclass(frozen=True)
class DataDeletionReviewPacket:
    id: int
    request_id: int
    dry_run_plan_id: int
    backup_verification_run_id: int
    quarantine_planning_run_id: int
    combined_rehearsal_run_id: int
    fault_matrix_run_id: int
    contract_version: str
    plan_fingerprint_sha256: str
    backup_verification_result_fingerprint_sha256: str
    quarantine_planning_result_fingerprint_sha256: str
    destination_contract_fingerprint_sha256: str
    combined_rehearsal_result_fingerprint_sha256: str
    fault_matrix_result_fingerprint_sha256: str
    fault_scenario_contract_fingerprint_sha256: str
    input_contract_fingerprint_sha256: str
    confirmation_text_sha256: str
    packet_fingerprint_sha256: str
    review_status: str
    packet_json: dict[str, Any]
    input_count: int
    passed_input_count: int
    blocked_input_count: int
    check_count: int
    passed_check_count: int
    blocked_check_count: int
    fault_scenario_count: int
    passed_fault_scenario_count: int
    contained_fault_count: int
    scratch_resources_removed: bool
    generated_by: str
    generation_note: str | None
    generated_at_kst: datetime

    def to_summary_record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "request_id": self.request_id,
            "dry_run_plan_id": self.dry_run_plan_id,
            "backup_verification_run_id": self.backup_verification_run_id,
            "quarantine_planning_run_id": self.quarantine_planning_run_id,
            "combined_rehearsal_run_id": self.combined_rehearsal_run_id,
            "fault_matrix_run_id": self.fault_matrix_run_id,
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
            "fault_matrix_result_fingerprint_sha256": (
                self.fault_matrix_result_fingerprint_sha256
            ),
            "fault_scenario_contract_fingerprint_sha256": (
                self.fault_scenario_contract_fingerprint_sha256
            ),
            "input_contract_fingerprint_sha256": (
                self.input_contract_fingerprint_sha256
            ),
            "confirmation_text_sha256": self.confirmation_text_sha256,
            "packet_fingerprint_sha256": self.packet_fingerprint_sha256,
            "review_status": self.review_status,
            "input_count": self.input_count,
            "passed_input_count": self.passed_input_count,
            "blocked_input_count": self.blocked_input_count,
            "check_count": self.check_count,
            "passed_check_count": self.passed_check_count,
            "blocked_check_count": self.blocked_check_count,
            "fault_scenario_count": self.fault_scenario_count,
            "passed_fault_scenario_count": self.passed_fault_scenario_count,
            "contained_fault_count": self.contained_fault_count,
            "scratch_resources_removed": self.scratch_resources_removed,
            "generated_by": self.generated_by,
            "generation_note": self.generation_note,
            "generated_at_kst": to_kst(self.generated_at_kst).isoformat(),
            "immutable": True,
            "advisory_only": True,
            "authorization_granted": False,
            "readiness_promoted": False,
            "evidence_created": False,
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
        return {**self.to_summary_record(), "packet_json": deepcopy(self.packet_json)}


@dataclass(frozen=True)
class _ReviewInputs:
    plan: DataDeletionDryRunPlan
    verification: DataDeletionBackupVerificationRun
    planning: DataDeletionQuarantinePlanningRun
    combined: DataDeletionCombinedRehearsalRun
    fault_matrix: DataDeletionFaultMatrixRun
    input_contract: dict[str, Any]
    input_contract_fingerprint_sha256: str


class DataDeletionReviewPacketService:
    def __init__(
        self,
        connection: Any,
        *,
        backup_service: DataDeletionBackupService,
        verifier_service: DataDeletionBackupVerifierService,
        planner_service: DataDeletionQuarantinePlannerService,
        combined_rehearsal_service: DataDeletionCombinedRehearsalService,
        fault_matrix_service: DataDeletionFaultMatrixService,
    ) -> None:
        self.connection = connection
        self.backup_service = backup_service
        self.verifier_service = verifier_service
        self.planner_service = planner_service
        self.combined_rehearsal_service = combined_rehearsal_service
        self.fault_matrix_service = fault_matrix_service

    def packet_state(self, request: DataDeletionRequest) -> dict[str, Any]:
        history = self.list_packets(request.id, limit=50)
        inputs, blockers = self._latest_inputs(request)
        candidate: dict[str, Any] | None = None
        if inputs is not None and not blockers:
            review_status = _review_status(inputs.fault_matrix)
            confirmation = expected_review_packet_confirmation(
                request.id,
                inputs.plan.id,
                inputs.fault_matrix.id,
                inputs.input_contract_fingerprint_sha256,
            )
            candidate = {
                "dry_run_plan": inputs.plan.to_summary_record(),
                "backup_verification": inputs.verification.to_summary_record(),
                "quarantine_planning": inputs.planning.to_summary_record(),
                "combined_rehearsal": inputs.combined.to_summary_record(),
                "fault_matrix": inputs.fault_matrix.to_summary_record(),
                "fault_scenarios": _fault_scenario_summaries(inputs.fault_matrix),
                "input_contract": deepcopy(inputs.input_contract),
                "input_contract_fingerprint_sha256": (
                    inputs.input_contract_fingerprint_sha256
                ),
                "predicted_review_status": review_status,
                "confirmation_text": confirmation,
            }
        return {
            "request_id": request.id,
            "request_status": request.status,
            "contract_version": REVIEW_PACKET_CONTRACT_VERSION,
            "packet_kind": REVIEW_PACKET_KIND,
            "packet_candidate": candidate,
            "latest_review_packet": history[0].to_record() if history else None,
            "review_packet_history": [
                item.to_summary_record() for item in history
            ],
            "review_packet_allowed": candidate is not None and not blockers,
            "review_packet_blockers": blockers,
            "captures_blocked_fault_matrix_outcome": True,
            "appends_review_packet_audit_row": True,
            "appends_fault_matrix_audit_row": False,
            "appends_combined_rehearsal_audit_row": False,
            "appends_readiness_evidence": False,
            "authorization_granted": False,
            "readiness_promoted": False,
            "production_database_rows_modified": False,
            "production_source_files_opened": False,
            "production_source_files_modified": False,
            "production_quarantine_performed": False,
            "production_restore_performed": False,
            "deletion_performed": False,
            "execution_enabled": False,
            "execution_ready": False,
            "execution_blockers": list(_EXECUTION_BLOCKERS),
        }

    def generate(
        self,
        request: DataDeletionRequest,
        *,
        fault_matrix_run_id: int,
        confirmation_text: str,
        actor_id: str,
        note: str | None = None,
        reference_kst: datetime | None = None,
    ) -> DataDeletionReviewPacket:
        matrix_id = _positive_int(fault_matrix_run_id, "fault_matrix_run_id")
        actor_id = _required_text(actor_id, "actor_id", 191)
        note = _optional_text(note, "note", 1000)
        selected_matrix = self.fault_matrix_service.get_run(matrix_id)
        if selected_matrix.request_id != request.id:
            raise DataDeletionReviewPacketError(
                "fault matrix belongs to another deletion request."
            )
        inputs, blockers = self._latest_inputs(
            request,
            selected_matrix=selected_matrix,
        )
        if inputs is None or blockers:
            raise DataDeletionReviewPacketError(
                "review packet inputs are blocked: " + "; ".join(blockers)
            )
        expected_confirmation = expected_review_packet_confirmation(
            request.id,
            inputs.plan.id,
            inputs.fault_matrix.id,
            inputs.input_contract_fingerprint_sha256,
        )
        supplied_confirmation = _required_text(
            confirmation_text,
            "confirmation_text",
            500,
        )
        if not hmac.compare_digest(supplied_confirmation, expected_confirmation):
            raise DataDeletionReviewPacketError(
                "review packet confirmation does not match the current input contract."
            )

        generated_at = to_kst(reference_kst or now_kst())
        confirmation_sha256 = hashlib.sha256(
            supplied_confirmation.encode("utf-8")
        ).hexdigest()
        packet_body = _build_packet_body(
            request,
            inputs,
            actor_id=actor_id,
            note=note,
            generated_at_kst=generated_at,
            confirmation_text_sha256=confirmation_sha256,
        )
        packet_fingerprint = _canonical_sha256(packet_body)
        packet_json = {
            **packet_body,
            "packet_fingerprint_sha256": packet_fingerprint,
        }
        metrics = _packet_metrics(packet_json)
        return self._record_packet(
            request,
            inputs,
            packet_json=packet_json,
            packet_fingerprint_sha256=packet_fingerprint,
            confirmation_text_sha256=confirmation_sha256,
            metrics=metrics,
            actor_id=actor_id,
            note=note,
            generated_at_kst=generated_at,
        )

    def get_packet(self, packet_id: int) -> DataDeletionReviewPacket:
        value = _positive_int(packet_id, "packet_id")
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM data_deletion_readiness_review_packets WHERE id = %s",
                (value,),
            )
            row = cursor.fetchone()
        if not row:
            raise DataDeletionReviewPacketError(
                f"review packet {value} was not found."
            )
        return _review_packet_from_row(row)

    def list_packets(
        self,
        request_id: int,
        *,
        limit: int = 50,
    ) -> list[DataDeletionReviewPacket]:
        value = _positive_int(request_id, "request_id")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
            raise DataDeletionReviewPacketError("limit must be between 1 and 200.")
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM data_deletion_readiness_review_packets
                WHERE request_id = %s
                ORDER BY generated_at_kst DESC, id DESC
                LIMIT %s
                """,
                (value, limit),
            )
            rows = cursor.fetchall()
        return [_review_packet_from_row(row) for row in rows]

    def _latest_inputs(
        self,
        request: DataDeletionRequest,
        *,
        selected_matrix: DataDeletionFaultMatrixRun | None = None,
    ) -> tuple[_ReviewInputs | None, list[str]]:
        blockers = _request_blockers(request)
        plans = self.backup_service.dry_run_service.list_plans(
            request.id,
            limit=1,
        )
        plan = plans[0] if plans else None
        if plan is None:
            blockers.append("latest confirmed dry-run plan is required")
            return None, _deduplicate(blockers)
        blockers.extend(_plan_blockers(plan, request))

        matrix_runs = self.fault_matrix_service.list_runs(plan.id, limit=1)
        latest_matrix = matrix_runs[0] if matrix_runs else None
        if latest_matrix is None:
            blockers.append("latest fault matrix run is required")
            return None, _deduplicate(blockers)
        matrix = selected_matrix or latest_matrix
        if selected_matrix is not None and selected_matrix.id != latest_matrix.id:
            blockers.append("selected fault matrix is not the latest run")
        if matrix.dry_run_plan_id != plan.id:
            blockers.append("fault matrix belongs to another dry-run plan")

        try:
            verification = self.verifier_service.get_run(
                matrix.backup_verification_run_id
            )
            planning = self.planner_service.get_run(
                matrix.quarantine_planning_run_id
            )
            combined = self.combined_rehearsal_service.get_run(
                matrix.combined_rehearsal_run_id
            )
        except Exception as exc:
            blockers.append(_safe_error_message(exc))
            return None, _deduplicate(blockers)

        latest_verifications = self.verifier_service.list_runs(plan.id, limit=1)
        latest_planning = self.planner_service.list_runs(plan.id, limit=1)
        latest_combined = self.combined_rehearsal_service.list_runs(
            plan.id,
            limit=1,
        )
        blockers.extend(
            _binding_blockers(
                request,
                plan,
                verification,
                planning,
                combined,
                matrix,
                latest_verification=(
                    latest_verifications[0] if latest_verifications else None
                ),
                latest_planning=latest_planning[0] if latest_planning else None,
                latest_combined=latest_combined[0] if latest_combined else None,
                latest_matrix=latest_matrix,
            )
        )
        blockers = _deduplicate(blockers)
        input_contract = _input_contract(
            request,
            plan,
            verification,
            planning,
            combined,
            matrix,
        )
        return (
            _ReviewInputs(
                plan=plan,
                verification=verification,
                planning=planning,
                combined=combined,
                fault_matrix=matrix,
                input_contract=input_contract,
                input_contract_fingerprint_sha256=_canonical_sha256(
                    input_contract
                ),
            ),
            blockers,
        )

    def _record_packet(
        self,
        request: DataDeletionRequest,
        inputs: _ReviewInputs,
        *,
        packet_json: dict[str, Any],
        packet_fingerprint_sha256: str,
        confirmation_text_sha256: str,
        metrics: dict[str, int | bool | str],
        actor_id: str,
        note: str | None,
        generated_at_kst: datetime,
    ) -> DataDeletionReviewPacket:
        _begin(self.connection)
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, status, executed_at_kst, execution_summary_json
                    FROM data_deletion_requests
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (request.id,),
                )
                _assert_locked_request(cursor.fetchone(), request)
                cursor.execute(
                    """
                    SELECT id, request_id, contract_version,
                           plan_fingerprint_sha256
                    FROM data_deletion_dry_run_plans
                    WHERE request_id = %s
                    ORDER BY generated_at_kst DESC, id DESC
                    LIMIT 1
                    FOR UPDATE
                    """,
                    (request.id,),
                )
                _assert_locked_plan(cursor.fetchone(), inputs.plan)
                cursor.execute(
                    """
                    SELECT id, contract_version, dry_run_plan_id,
                           plan_fingerprint_sha256, result_fingerprint_sha256,
                           result_status
                    FROM data_deletion_backup_verification_runs
                    WHERE dry_run_plan_id = %s
                    ORDER BY verified_at_kst DESC, id DESC
                    LIMIT 1
                    FOR UPDATE
                    """,
                    (inputs.plan.id,),
                )
                _assert_locked_verification(
                    cursor.fetchone(),
                    inputs.verification,
                )
                cursor.execute(
                    """
                    SELECT id, contract_version, dry_run_plan_id,
                           plan_fingerprint_sha256,
                           destination_contract_fingerprint_sha256,
                           result_fingerprint_sha256, result_status,
                           capacity_evidence_id
                    FROM data_deletion_quarantine_planning_runs
                    WHERE dry_run_plan_id = %s
                    ORDER BY planned_at_kst DESC, id DESC
                    LIMIT 1
                    FOR UPDATE
                    """,
                    (inputs.plan.id,),
                )
                _assert_locked_planning(cursor.fetchone(), inputs.planning)
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
                    (inputs.plan.id,),
                )
                _assert_locked_combined(cursor.fetchone(), inputs.combined)
                cursor.execute(
                    """
                    SELECT id, contract_version, dry_run_plan_id,
                           backup_verification_run_id,
                           quarantine_planning_run_id,
                           combined_rehearsal_run_id,
                           result_fingerprint_sha256, result_status,
                           scenario_contract_fingerprint_sha256,
                           scratch_resources_removed
                    FROM data_deletion_combined_fault_matrix_runs
                    WHERE dry_run_plan_id = %s
                    ORDER BY run_at_kst DESC, id DESC
                    LIMIT 1
                    FOR UPDATE
                    """,
                    (inputs.plan.id,),
                )
                _assert_locked_fault_matrix(
                    cursor.fetchone(),
                    inputs.fault_matrix,
                )
                cursor.execute(
                    """
                    INSERT INTO data_deletion_readiness_review_packets (
                        request_id,
                        dry_run_plan_id,
                        backup_verification_run_id,
                        quarantine_planning_run_id,
                        combined_rehearsal_run_id,
                        fault_matrix_run_id,
                        contract_version,
                        plan_fingerprint_sha256,
                        backup_verification_result_fingerprint_sha256,
                        quarantine_planning_result_fingerprint_sha256,
                        destination_contract_fingerprint_sha256,
                        combined_rehearsal_result_fingerprint_sha256,
                        fault_matrix_result_fingerprint_sha256,
                        fault_scenario_contract_fingerprint_sha256,
                        input_contract_fingerprint_sha256,
                        confirmation_text_sha256,
                        packet_fingerprint_sha256,
                        review_status,
                        packet_json,
                        input_count,
                        passed_input_count,
                        blocked_input_count,
                        check_count,
                        passed_check_count,
                        blocked_check_count,
                        fault_scenario_count,
                        passed_fault_scenario_count,
                        contained_fault_count,
                        scratch_resources_removed,
                        generated_by,
                        generation_note,
                        generated_at_kst
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        request.id,
                        inputs.plan.id,
                        inputs.verification.id,
                        inputs.planning.id,
                        inputs.combined.id,
                        inputs.fault_matrix.id,
                        REVIEW_PACKET_CONTRACT_VERSION,
                        inputs.plan.plan_fingerprint_sha256,
                        inputs.verification.result_fingerprint_sha256,
                        inputs.planning.result_fingerprint_sha256,
                        inputs.planning.destination_contract_fingerprint_sha256,
                        inputs.combined.result_fingerprint_sha256,
                        inputs.fault_matrix.result_fingerprint_sha256,
                        inputs.fault_matrix.scenario_contract_fingerprint_sha256,
                        inputs.input_contract_fingerprint_sha256,
                        confirmation_text_sha256,
                        packet_fingerprint_sha256,
                        metrics["review_status"],
                        _json_dump(packet_json),
                        metrics["input_count"],
                        metrics["passed_input_count"],
                        metrics["blocked_input_count"],
                        metrics["check_count"],
                        metrics["passed_check_count"],
                        metrics["blocked_check_count"],
                        metrics["fault_scenario_count"],
                        metrics["passed_fault_scenario_count"],
                        metrics["contained_fault_count"],
                        metrics["scratch_resources_removed"],
                        actor_id,
                        note,
                        generated_at_kst,
                    ),
                )
                packet_id = int(cursor.lastrowid)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return DataDeletionReviewPacket(
            id=packet_id,
            request_id=request.id,
            dry_run_plan_id=inputs.plan.id,
            backup_verification_run_id=inputs.verification.id,
            quarantine_planning_run_id=inputs.planning.id,
            combined_rehearsal_run_id=inputs.combined.id,
            fault_matrix_run_id=inputs.fault_matrix.id,
            contract_version=REVIEW_PACKET_CONTRACT_VERSION,
            plan_fingerprint_sha256=inputs.plan.plan_fingerprint_sha256,
            backup_verification_result_fingerprint_sha256=(
                inputs.verification.result_fingerprint_sha256
            ),
            quarantine_planning_result_fingerprint_sha256=(
                inputs.planning.result_fingerprint_sha256
            ),
            destination_contract_fingerprint_sha256=(
                inputs.planning.destination_contract_fingerprint_sha256
            ),
            combined_rehearsal_result_fingerprint_sha256=(
                inputs.combined.result_fingerprint_sha256
            ),
            fault_matrix_result_fingerprint_sha256=(
                inputs.fault_matrix.result_fingerprint_sha256
            ),
            fault_scenario_contract_fingerprint_sha256=(
                inputs.fault_matrix.scenario_contract_fingerprint_sha256
            ),
            input_contract_fingerprint_sha256=(
                inputs.input_contract_fingerprint_sha256
            ),
            confirmation_text_sha256=confirmation_text_sha256,
            packet_fingerprint_sha256=packet_fingerprint_sha256,
            review_status=str(metrics["review_status"]),
            packet_json=deepcopy(packet_json),
            input_count=int(metrics["input_count"]),
            passed_input_count=int(metrics["passed_input_count"]),
            blocked_input_count=int(metrics["blocked_input_count"]),
            check_count=int(metrics["check_count"]),
            passed_check_count=int(metrics["passed_check_count"]),
            blocked_check_count=int(metrics["blocked_check_count"]),
            fault_scenario_count=int(metrics["fault_scenario_count"]),
            passed_fault_scenario_count=int(
                metrics["passed_fault_scenario_count"]
            ),
            contained_fault_count=int(metrics["contained_fault_count"]),
            scratch_resources_removed=bool(
                metrics["scratch_resources_removed"]
            ),
            generated_by=actor_id,
            generation_note=note,
            generated_at_kst=generated_at_kst,
        )


def expected_review_packet_confirmation(
    request_id: int,
    dry_run_plan_id: int,
    fault_matrix_run_id: int,
    input_contract_fingerprint_sha256: str,
) -> str:
    return (
        f"{REVIEW_PACKET_CONFIRMATION_PREFIX} REQUEST "
        f"{_positive_int(request_id, 'request_id')} PLAN "
        f"{_positive_int(dry_run_plan_id, 'dry_run_plan_id')} FAULT MATRIX "
        f"{_positive_int(fault_matrix_run_id, 'fault_matrix_run_id')} INPUTS "
        f"{_fingerprint(input_contract_fingerprint_sha256, 'input contract')}"
    )


def canonical_review_packet_bytes(packet: DataDeletionReviewPacket) -> bytes:
    validated = _review_packet_from_row(_packet_as_row(packet))
    return (
        json.dumps(
            validated.packet_json,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _build_packet_body(
    request: DataDeletionRequest,
    inputs: _ReviewInputs,
    *,
    actor_id: str,
    note: str | None,
    generated_at_kst: datetime,
    confirmation_text_sha256: str,
) -> dict[str, Any]:
    checks = _review_checks(request, inputs)
    blocked_checks = [
        str(item["message"])
        for item in checks
        if item.get("status") == "blocked"
    ]
    review_status = (
        REVIEW_STATUS_PASSED if not blocked_checks else REVIEW_STATUS_BLOCKED
    )
    matrix = inputs.fault_matrix
    input_results = [
        True,
        True,
        inputs.verification.result_status == "passed",
        inputs.planning.result_status == "passed",
        inputs.combined.result_status == "passed",
        matrix.result_status == "passed",
    ]
    metrics = {
        "input_count": REVIEW_PACKET_INPUT_COUNT,
        "passed_input_count": sum(input_results),
        "blocked_input_count": REVIEW_PACKET_INPUT_COUNT - sum(input_results),
        "check_count": len(checks),
        "passed_check_count": sum(
            item.get("status") == "passed" for item in checks
        ),
        "blocked_check_count": sum(
            item.get("status") == "blocked" for item in checks
        ),
        "fault_scenario_count": matrix.scenario_count,
        "passed_fault_scenario_count": matrix.passed_scenario_count,
        "contained_fault_count": matrix.contained_fault_count,
        "scratch_resources_removed": matrix.scratch_resources_removed,
    }
    return {
        "contract_version": REVIEW_PACKET_CONTRACT_VERSION,
        "packet_kind": REVIEW_PACKET_KIND,
        "canonicalization": "utf8-json-sort-keys-compact-sha256-body-v1",
        "request_id": request.id,
        "dry_run_plan_id": inputs.plan.id,
        "backup_verification_run_id": inputs.verification.id,
        "quarantine_planning_run_id": inputs.planning.id,
        "combined_rehearsal_run_id": inputs.combined.id,
        "fault_matrix_run_id": matrix.id,
        "input_contract": deepcopy(inputs.input_contract),
        "input_contract_fingerprint_sha256": (
            inputs.input_contract_fingerprint_sha256
        ),
        "subject": request.to_record(),
        "artifacts": {
            "dry_run_plan": inputs.plan.to_summary_record(),
            "backup_verification": inputs.verification.to_summary_record(),
            "quarantine_planning": inputs.planning.to_summary_record(),
            "combined_rehearsal": inputs.combined.to_summary_record(),
            "fault_matrix": matrix.to_summary_record(),
        },
        "fault_scenarios": _fault_scenario_summaries(matrix),
        "assessment": {
            "review_status": review_status,
            "checks": checks,
            "blocked_checks": blocked_checks,
            "advisory_only": True,
            "authorization_granted": False,
            "readiness_promoted": False,
            "execution_enabled": False,
            "execution_ready": False,
        },
        "metrics": metrics,
        "generation": {
            "generated_by": actor_id,
            "generation_note": note,
            "generated_at_kst": generated_at_kst.isoformat(),
            "confirmation_text_sha256": confirmation_text_sha256,
        },
        "safety": {
            "immutable_audit_packet_only": True,
            "advisory_only": True,
            "authorization_granted": False,
            "readiness_promoted": False,
            "evidence_created": False,
            "production_database_rows_modified": False,
            "production_source_files_opened": False,
            "production_source_files_modified": False,
            "production_quarantine_performed": False,
            "production_restore_performed": False,
            "deletion_performed": False,
            "execution_enabled": False,
            "execution_ready": False,
            "execution_blockers": list(_EXECUTION_BLOCKERS),
        },
    }


def _review_checks(
    request: DataDeletionRequest,
    inputs: _ReviewInputs,
) -> list[dict[str, Any]]:
    matrix = inputs.fault_matrix
    scenarios = _fault_scenario_summaries(matrix)
    scenario_passed = bool(
        len(scenarios) == len(fault_scenario_contract())
        and all(
            item.get("status") == "passed"
            and item.get("fault_observed") is True
            and item.get("fault_contained") is True
            and item.get("scratch_removed") is True
            and item.get("production_data_unchanged") is True
            for item in scenarios
        )
    )
    return [
        _check(
            "request_approved_unexecuted",
            request.status == "approved"
            and request.executed_at_kst is None
            and request.execution_summary_json is None,
            "approved request with no execution metadata",
            {"status": request.status, "executed_at_kst": request.executed_at_kst},
            "request remains approved and unexecuted",
        ),
        _check(
            "dry_run_plan_current",
            not _plan_blockers(inputs.plan, request),
            "latest canonical non-executable dry-run plan",
            {
                "plan_id": inputs.plan.id,
                "plan_fingerprint_sha256": inputs.plan.plan_fingerprint_sha256,
            },
            "dry-run plan is current and non-executable",
        ),
        _check(
            "backup_verification_passed",
            inputs.verification.result_status == "passed",
            "latest backup verification passed",
            {
                "verification_id": inputs.verification.id,
                "result_status": inputs.verification.result_status,
            },
            "backup verification passed",
        ),
        _check(
            "quarantine_planning_passed",
            inputs.planning.result_status == "passed"
            and inputs.planning.capacity_evidence_id is not None,
            "latest quarantine planning passed with bound capacity evidence",
            {
                "planning_id": inputs.planning.id,
                "result_status": inputs.planning.result_status,
                "capacity_evidence_id": inputs.planning.capacity_evidence_id,
            },
            "quarantine planning passed with capacity evidence",
        ),
        _check(
            "combined_rehearsal_passed",
            inputs.combined.result_status == "passed"
            and inputs.combined.scratch_resources_removed,
            "latest combined rehearsal passed and cleaned all scratch",
            {
                "combined_rehearsal_id": inputs.combined.id,
                "result_status": inputs.combined.result_status,
                "scratch_resources_removed": (
                    inputs.combined.scratch_resources_removed
                ),
            },
            "combined rehearsal passed and cleaned scratch resources",
        ),
        _check(
            "fault_matrix_outcome",
            matrix.result_status == "passed",
            "latest fault matrix passed",
            {
                "fault_matrix_run_id": matrix.id,
                "result_status": matrix.result_status,
            },
            (
                "fault matrix passed"
                if matrix.result_status == "passed"
                else "fault matrix contains one or more blocked checks"
            ),
        ),
        _check(
            "all_declared_faults_contained",
            scenario_passed,
            "all four declared faults were observed, contained, and cleaned",
            {
                "scenario_count": len(scenarios),
                "passed_scenario_count": matrix.passed_scenario_count,
                "contained_fault_count": matrix.contained_fault_count,
                "scratch_resources_removed": matrix.scratch_resources_removed,
            },
            (
                "all declared faults were contained and cleaned"
                if scenario_passed
                else "one or more declared fault outcomes remain blocked"
            ),
        ),
        _check(
            "advisory_non_authorization",
            True,
            "packet cannot authorize, promote readiness, or enable execution",
            {
                "authorization_granted": False,
                "readiness_promoted": False,
                "execution_enabled": False,
                "execution_ready": False,
            },
            "packet is advisory only and grants no authority",
        ),
    ]


def _request_blockers(request: DataDeletionRequest) -> list[str]:
    blockers: list[str] = []
    if request.status != "approved":
        blockers.append(f"request status must be approved, not {request.status}")
    if request.executed_at_kst is not None or request.execution_summary_json is not None:
        blockers.append("request contains execution metadata")
    return blockers


def _plan_blockers(
    plan: DataDeletionDryRunPlan,
    request: DataDeletionRequest,
) -> list[str]:
    blockers: list[str] = []
    if plan.request_id != request.id:
        blockers.append("dry-run plan belongs to another request")
    if plan.contract_version != DRY_RUN_CONTRACT_VERSION:
        blockers.append("dry-run plan contract is unsupported")
    try:
        observed_fingerprint = fingerprint_dry_run_plan(plan.plan_json)
    except Exception:
        observed_fingerprint = ""
    if not hmac.compare_digest(
        plan.plan_fingerprint_sha256,
        observed_fingerprint,
    ):
        blockers.append("dry-run plan fingerprint is invalid")
    target = plan.plan_json.get("target")
    if not isinstance(target, dict) or (
        target.get("account_id") != request.account_id
        or target.get("shard") != request.shard
    ):
        blockers.append("dry-run plan target differs from the request")
    safety = plan.plan_json.get("safety")
    if not isinstance(safety, dict) or (
        safety.get("execution_enabled") is not False
        or safety.get("execution_ready") is not False
        or "executor_not_implemented"
        not in (safety.get("execution_blockers") or [])
    ):
        blockers.append("dry-run plan safety contract is invalid")
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
    elif any(
        not isinstance(item, dict)
        or int(item.get("sequence") or 0) != sequence
        or item.get("action") != "delete_rows_planned"
        or item.get("mutation_enabled") is not False
        or not isinstance(item.get("selector"), dict)
        for sequence, item in enumerate(operations, start=1)
    ):
        blockers.append("dry-run database operation contract is invalid")
    return _deduplicate(blockers)


def _binding_blockers(
    request: DataDeletionRequest,
    plan: DataDeletionDryRunPlan,
    verification: DataDeletionBackupVerificationRun,
    planning: DataDeletionQuarantinePlanningRun,
    combined: DataDeletionCombinedRehearsalRun,
    matrix: DataDeletionFaultMatrixRun,
    *,
    latest_verification: DataDeletionBackupVerificationRun | None,
    latest_planning: DataDeletionQuarantinePlanningRun | None,
    latest_combined: DataDeletionCombinedRehearsalRun | None,
    latest_matrix: DataDeletionFaultMatrixRun | None,
) -> list[str]:
    blockers: list[str] = []
    if latest_verification is None or latest_verification.id != verification.id:
        blockers.append("fault-matrix backup verification is not the latest run")
    if latest_planning is None or latest_planning.id != planning.id:
        blockers.append("fault-matrix quarantine planning is not the latest run")
    if latest_combined is None or latest_combined.id != combined.id:
        blockers.append("fault-matrix combined rehearsal is not the latest run")
    if latest_matrix is None or latest_matrix.id != matrix.id:
        blockers.append("selected fault matrix is not the latest run")

    if verification.contract_version != BACKUP_VERIFIER_CONTRACT_VERSION:
        blockers.append("backup verification contract is unsupported")
    if verification.result_status != "passed":
        blockers.append("backup verification must have passed")
    if verification.request_id != request.id or verification.dry_run_plan_id != plan.id:
        blockers.append("backup verification binding differs from the request or plan")
    if not hmac.compare_digest(
        verification.plan_fingerprint_sha256,
        plan.plan_fingerprint_sha256,
    ):
        blockers.append("backup verification plan fingerprint is stale")
    if not _summary_safety_false(
        verification.to_summary_record(),
        ("deletion_performed", "execution_enabled", "execution_ready"),
    ):
        blockers.append("backup verification safety contract is invalid")

    if planning.contract_version != QUARANTINE_PLANNER_CONTRACT_VERSION:
        blockers.append("quarantine planning contract is unsupported")
    if planning.result_status != "passed" or planning.capacity_evidence_id is None:
        blockers.append("quarantine planning must pass with capacity evidence")
    if planning.request_id != request.id or planning.dry_run_plan_id != plan.id:
        blockers.append("quarantine planning binding differs from the request or plan")
    if not hmac.compare_digest(
        planning.plan_fingerprint_sha256,
        plan.plan_fingerprint_sha256,
    ):
        blockers.append("quarantine planning plan fingerprint is stale")
    if not _summary_safety_false(
        planning.to_summary_record(),
        (
            "database_rows_modified",
            "quarantine_performed",
            "deletion_performed",
            "execution_enabled",
            "execution_ready",
        ),
    ):
        blockers.append("quarantine planning safety contract is invalid")

    if combined.contract_version != COMBINED_REHEARSAL_CONTRACT_VERSION:
        blockers.append("combined rehearsal contract is unsupported")
    if combined.result_status != "passed" or not combined.scratch_resources_removed:
        blockers.append("combined rehearsal must have passed and cleaned scratch")
    if combined.request_id != request.id or combined.dry_run_plan_id != plan.id:
        blockers.append("combined rehearsal binding differs from the request or plan")
    if (
        combined.backup_verification_run_id != verification.id
        or combined.quarantine_planning_run_id != planning.id
    ):
        blockers.append("combined rehearsal input IDs differ from selected inputs")
    if not all(
        (
            hmac.compare_digest(
                combined.plan_fingerprint_sha256,
                plan.plan_fingerprint_sha256,
            ),
            hmac.compare_digest(
                combined.backup_verification_result_fingerprint_sha256,
                verification.result_fingerprint_sha256,
            ),
            hmac.compare_digest(
                combined.quarantine_planning_result_fingerprint_sha256,
                planning.result_fingerprint_sha256,
            ),
            hmac.compare_digest(
                combined.destination_contract_fingerprint_sha256,
                planning.destination_contract_fingerprint_sha256,
            ),
        )
    ):
        blockers.append("combined rehearsal fingerprints differ from selected inputs")
    if not _summary_safety_false(
        combined.to_summary_record(),
        (
            "production_database_rows_modified",
            "production_source_files_opened",
            "production_quarantine_performed",
            "deletion_performed",
            "execution_enabled",
            "execution_ready",
        ),
    ):
        blockers.append("combined rehearsal safety contract is invalid")

    if matrix.contract_version != FAULT_MATRIX_CONTRACT_VERSION:
        blockers.append("fault matrix contract is unsupported")
    if matrix.result_status not in {"passed", "blocked"}:
        blockers.append("fault matrix status is unsupported")
    if matrix.request_id != request.id or matrix.dry_run_plan_id != plan.id:
        blockers.append("fault matrix binding differs from the request or plan")
    if (
        matrix.backup_verification_run_id != verification.id
        or matrix.quarantine_planning_run_id != planning.id
        or matrix.combined_rehearsal_run_id != combined.id
    ):
        blockers.append("fault matrix input IDs differ from selected inputs")
    expected_scenario_fingerprint = fingerprint_fault_scenario_contract(
        fault_scenario_contract()
    )
    if not all(
        (
            hmac.compare_digest(
                matrix.plan_fingerprint_sha256,
                plan.plan_fingerprint_sha256,
            ),
            hmac.compare_digest(
                matrix.backup_verification_result_fingerprint_sha256,
                verification.result_fingerprint_sha256,
            ),
            hmac.compare_digest(
                matrix.quarantine_planning_result_fingerprint_sha256,
                planning.result_fingerprint_sha256,
            ),
            hmac.compare_digest(
                matrix.destination_contract_fingerprint_sha256,
                planning.destination_contract_fingerprint_sha256,
            ),
            hmac.compare_digest(
                matrix.combined_rehearsal_result_fingerprint_sha256,
                combined.result_fingerprint_sha256,
            ),
            hmac.compare_digest(
                matrix.scenario_contract_fingerprint_sha256,
                expected_scenario_fingerprint,
            ),
        )
    ):
        blockers.append("fault matrix fingerprints differ from selected inputs")
    if not _summary_safety_false(
        matrix.to_summary_record(),
        (
            "production_database_rows_modified",
            "production_source_files_opened",
            "production_quarantine_performed",
            "deletion_performed",
            "execution_enabled",
            "execution_ready",
        ),
    ):
        blockers.append("fault matrix safety contract is invalid")
    return _deduplicate(blockers)


def _input_contract(
    request: DataDeletionRequest,
    plan: DataDeletionDryRunPlan,
    verification: DataDeletionBackupVerificationRun,
    planning: DataDeletionQuarantinePlanningRun,
    combined: DataDeletionCombinedRehearsalRun,
    matrix: DataDeletionFaultMatrixRun,
) -> dict[str, Any]:
    return {
        "contract_version": REVIEW_PACKET_CONTRACT_VERSION,
        "request_id": request.id,
        "request_status": request.status,
        "dry_run_plan_id": plan.id,
        "plan_fingerprint_sha256": plan.plan_fingerprint_sha256,
        "backup_verification_run_id": verification.id,
        "backup_verification_result_fingerprint_sha256": (
            verification.result_fingerprint_sha256
        ),
        "quarantine_planning_run_id": planning.id,
        "quarantine_planning_result_fingerprint_sha256": (
            planning.result_fingerprint_sha256
        ),
        "destination_contract_fingerprint_sha256": (
            planning.destination_contract_fingerprint_sha256
        ),
        "combined_rehearsal_run_id": combined.id,
        "combined_rehearsal_result_fingerprint_sha256": (
            combined.result_fingerprint_sha256
        ),
        "fault_matrix_run_id": matrix.id,
        "fault_matrix_result_fingerprint_sha256": (
            matrix.result_fingerprint_sha256
        ),
        "fault_scenario_contract_fingerprint_sha256": (
            matrix.scenario_contract_fingerprint_sha256
        ),
    }


def _fault_scenario_summaries(
    matrix: DataDeletionFaultMatrixRun,
) -> list[dict[str, Any]]:
    value = matrix.result_json.get("scenarios")
    if not isinstance(value, list):
        return []
    return [
        {
            "sequence": item.get("sequence"),
            "key": item.get("key"),
            "category": item.get("category"),
            "fault_point": item.get("fault_point"),
            "status": item.get("status"),
            "fault_observed": item.get("fault_observed"),
            "fault_contained": item.get("fault_contained"),
            "scratch_removed": item.get("scratch_removed"),
            "production_data_unchanged": item.get("production_data_unchanged"),
            "message": item.get("message"),
        }
        for item in value
        if isinstance(item, dict)
    ]


def _review_status(matrix: DataDeletionFaultMatrixRun) -> str:
    scenarios = _fault_scenario_summaries(matrix)
    passed = bool(
        matrix.result_status == "passed"
        and matrix.scenario_count == len(fault_scenario_contract())
        and matrix.passed_scenario_count == matrix.scenario_count
        and matrix.contained_fault_count == matrix.scenario_count
        and matrix.scratch_resources_removed
        and len(scenarios) == matrix.scenario_count
        and all(
            item.get("status") == "passed"
            and item.get("fault_observed") is True
            and item.get("fault_contained") is True
            and item.get("scratch_removed") is True
            and item.get("production_data_unchanged") is True
            for item in scenarios
        )
    )
    return REVIEW_STATUS_PASSED if passed else REVIEW_STATUS_BLOCKED


def _packet_metrics(packet_json: dict[str, Any]) -> dict[str, int | bool | str]:
    metrics = packet_json.get("metrics")
    assessment = packet_json.get("assessment")
    if not isinstance(metrics, dict) or not isinstance(assessment, dict):
        raise DataDeletionReviewPacketError("review packet metrics are missing.")
    return {
        "review_status": str(assessment.get("review_status") or ""),
        "input_count": _nonnegative_int(metrics.get("input_count"), "input_count"),
        "passed_input_count": _nonnegative_int(
            metrics.get("passed_input_count"),
            "passed_input_count",
        ),
        "blocked_input_count": _nonnegative_int(
            metrics.get("blocked_input_count"),
            "blocked_input_count",
        ),
        "check_count": _nonnegative_int(metrics.get("check_count"), "check_count"),
        "passed_check_count": _nonnegative_int(
            metrics.get("passed_check_count"),
            "passed_check_count",
        ),
        "blocked_check_count": _nonnegative_int(
            metrics.get("blocked_check_count"),
            "blocked_check_count",
        ),
        "fault_scenario_count": _nonnegative_int(
            metrics.get("fault_scenario_count"),
            "fault_scenario_count",
        ),
        "passed_fault_scenario_count": _nonnegative_int(
            metrics.get("passed_fault_scenario_count"),
            "passed_fault_scenario_count",
        ),
        "contained_fault_count": _nonnegative_int(
            metrics.get("contained_fault_count"),
            "contained_fault_count",
        ),
        "scratch_resources_removed": bool(metrics.get("scratch_resources_removed")),
    }


def _review_packet_from_row(row: Mapping[str, Any]) -> DataDeletionReviewPacket:
    packet_json = _json_object(row.get("packet_json"), "packet_json")
    contract = str(row.get("contract_version") or "")
    if contract != REVIEW_PACKET_CONTRACT_VERSION or (
        packet_json.get("contract_version") != contract
    ):
        raise DataDeletionReviewPacketError(
            "review packet contract version is invalid."
        )
    if packet_json.get("packet_kind") != REVIEW_PACKET_KIND:
        raise DataDeletionReviewPacketError("review packet kind is invalid.")
    packet_fingerprint = _fingerprint(
        row.get("packet_fingerprint_sha256"),
        "packet fingerprint",
    )
    if packet_json.get("packet_fingerprint_sha256") != packet_fingerprint:
        raise DataDeletionReviewPacketError(
            "review packet fingerprint binding is invalid."
        )
    fingerprint_body = deepcopy(packet_json)
    fingerprint_body.pop("packet_fingerprint_sha256", None)
    if not hmac.compare_digest(
        packet_fingerprint,
        _canonical_sha256(fingerprint_body),
    ):
        raise DataDeletionReviewPacketError(
            "review packet canonical fingerprint is invalid."
        )

    expected_bindings = {
        "request_id": _positive_int(row.get("request_id"), "request_id"),
        "dry_run_plan_id": _positive_int(
            row.get("dry_run_plan_id"),
            "dry_run_plan_id",
        ),
        "backup_verification_run_id": _positive_int(
            row.get("backup_verification_run_id"),
            "backup_verification_run_id",
        ),
        "quarantine_planning_run_id": _positive_int(
            row.get("quarantine_planning_run_id"),
            "quarantine_planning_run_id",
        ),
        "combined_rehearsal_run_id": _positive_int(
            row.get("combined_rehearsal_run_id"),
            "combined_rehearsal_run_id",
        ),
        "fault_matrix_run_id": _positive_int(
            row.get("fault_matrix_run_id"),
            "fault_matrix_run_id",
        ),
    }
    if any(packet_json.get(key) != value for key, value in expected_bindings.items()):
        raise DataDeletionReviewPacketError(
            "review packet input IDs are not bound to the audit row."
        )
    input_contract = packet_json.get("input_contract")
    if not isinstance(input_contract, dict):
        raise DataDeletionReviewPacketError("review packet input contract is missing.")
    if (
        input_contract.get("contract_version") != REVIEW_PACKET_CONTRACT_VERSION
        or input_contract.get("request_status") != "approved"
        or any(
            input_contract.get(key) != value
            for key, value in expected_bindings.items()
        )
    ):
        raise DataDeletionReviewPacketError(
            "review packet input contract ID bindings are invalid."
        )
    input_fingerprint = _fingerprint(
        row.get("input_contract_fingerprint_sha256"),
        "input contract fingerprint",
    )
    if (
        packet_json.get("input_contract_fingerprint_sha256") != input_fingerprint
        or not hmac.compare_digest(input_fingerprint, _canonical_sha256(input_contract))
    ):
        raise DataDeletionReviewPacketError(
            "review packet input contract fingerprint is invalid."
        )

    fingerprint_bindings = {
        "plan_fingerprint_sha256": "plan_fingerprint_sha256",
        "backup_verification_result_fingerprint_sha256": (
            "backup_verification_result_fingerprint_sha256"
        ),
        "quarantine_planning_result_fingerprint_sha256": (
            "quarantine_planning_result_fingerprint_sha256"
        ),
        "destination_contract_fingerprint_sha256": (
            "destination_contract_fingerprint_sha256"
        ),
        "combined_rehearsal_result_fingerprint_sha256": (
            "combined_rehearsal_result_fingerprint_sha256"
        ),
        "fault_matrix_result_fingerprint_sha256": (
            "fault_matrix_result_fingerprint_sha256"
        ),
        "fault_scenario_contract_fingerprint_sha256": (
            "fault_scenario_contract_fingerprint_sha256"
        ),
    }
    for contract_key, row_key in fingerprint_bindings.items():
        observed = _fingerprint(row.get(row_key), contract_key)
        if input_contract.get(contract_key) != observed:
            raise DataDeletionReviewPacketError(
                "review packet fingerprint bindings are invalid."
            )

    generation = packet_json.get("generation")
    if not isinstance(generation, dict):
        raise DataDeletionReviewPacketError("review packet generation metadata is missing.")
    generated_by = _required_text(
        row.get("generated_by"),
        "generated_by",
        191,
    )
    generation_note = _optional_text(
        row.get("generation_note"),
        "generation_note",
        1000,
    )
    generated_at_kst = _datetime(
        row.get("generated_at_kst"),
        "generated_at_kst",
    )
    if (
        generation.get("generated_by") != generated_by
        or generation.get("generation_note") != generation_note
        or generation.get("generated_at_kst") != generated_at_kst.isoformat()
    ):
        raise DataDeletionReviewPacketError(
            "review packet generation metadata bindings are invalid."
        )
    confirmation_hash = _fingerprint(
        row.get("confirmation_text_sha256"),
        "confirmation text fingerprint",
    )
    if generation.get("confirmation_text_sha256") != confirmation_hash:
        raise DataDeletionReviewPacketError(
            "review packet confirmation hash binding is invalid."
        )
    review_status = str(row.get("review_status") or "")
    if review_status not in {REVIEW_STATUS_PASSED, REVIEW_STATUS_BLOCKED}:
        raise DataDeletionReviewPacketError("review packet status is invalid.")
    assessment = packet_json.get("assessment")
    safety = packet_json.get("safety")
    if not isinstance(assessment, dict) or not isinstance(safety, dict):
        raise DataDeletionReviewPacketError("review packet safety metadata is missing.")
    if assessment.get("review_status") != review_status:
        raise DataDeletionReviewPacketError("review packet status binding is invalid.")
    required_false = (
        "authorization_granted",
        "readiness_promoted",
        "evidence_created",
        "production_database_rows_modified",
        "production_source_files_opened",
        "production_source_files_modified",
        "production_quarantine_performed",
        "production_restore_performed",
        "deletion_performed",
        "execution_enabled",
        "execution_ready",
    )
    if (
        safety.get("immutable_audit_packet_only") is not True
        or safety.get("advisory_only") is not True
        or any(safety.get(key) is not False for key in required_false)
        or safety.get("execution_blockers") != list(_EXECUTION_BLOCKERS)
        or assessment.get("advisory_only") is not True
        or assessment.get("authorization_granted") is not False
        or assessment.get("readiness_promoted") is not False
        or assessment.get("execution_enabled") is not False
        or assessment.get("execution_ready") is not False
    ):
        raise DataDeletionReviewPacketError(
            "review packet non-authorization safety contract is invalid."
        )

    metrics = _packet_metrics(packet_json)
    metric_fields = (
        "input_count",
        "passed_input_count",
        "blocked_input_count",
        "check_count",
        "passed_check_count",
        "blocked_check_count",
        "fault_scenario_count",
        "passed_fault_scenario_count",
        "contained_fault_count",
    )
    for field in metric_fields:
        if int(metrics[field]) != _nonnegative_int(row.get(field), field):
            raise DataDeletionReviewPacketError(
                "review packet metric bindings are invalid."
            )
    scratch_removed = bool(row.get("scratch_resources_removed"))
    if scratch_removed != bool(metrics["scratch_resources_removed"]):
        raise DataDeletionReviewPacketError(
            "review packet cleanup binding is invalid."
        )
    checks = assessment.get("checks")
    blocked_checks = assessment.get("blocked_checks")
    if not isinstance(checks, list) or not isinstance(blocked_checks, list):
        raise DataDeletionReviewPacketError("review packet checks are invalid.")
    observed_passed = sum(
        isinstance(item, dict) and item.get("status") == "passed"
        for item in checks
    )
    observed_blocked = sum(
        isinstance(item, dict) and item.get("status") == "blocked"
        for item in checks
    )
    if (
        len(checks) != int(metrics["check_count"])
        or observed_passed != int(metrics["passed_check_count"])
        or observed_blocked != int(metrics["blocked_check_count"])
        or len(blocked_checks) != observed_blocked
        or int(metrics["input_count"]) != REVIEW_PACKET_INPUT_COUNT
        or int(metrics["passed_input_count"])
        + int(metrics["blocked_input_count"])
        != REVIEW_PACKET_INPUT_COUNT
    ):
        raise DataDeletionReviewPacketError(
            "review packet check totals are invalid."
        )
    scenarios = packet_json.get("fault_scenarios")
    if not isinstance(scenarios, list) or len(scenarios) != int(
        metrics["fault_scenario_count"]
    ):
        raise DataDeletionReviewPacketError(
            "review packet fault scenario totals are invalid."
        )
    if review_status == REVIEW_STATUS_PASSED and (
        observed_blocked
        or int(metrics["blocked_input_count"]) != 0
        or int(metrics["fault_scenario_count"]) != len(fault_scenario_contract())
        or int(metrics["passed_fault_scenario_count"])
        != int(metrics["fault_scenario_count"])
        or int(metrics["contained_fault_count"])
        != int(metrics["fault_scenario_count"])
        or not scratch_removed
        or any(
            not isinstance(item, dict)
            or item.get("status") != "passed"
            or item.get("fault_observed") is not True
            or item.get("fault_contained") is not True
            or item.get("scratch_removed") is not True
            or item.get("production_data_unchanged") is not True
            for item in scenarios
        )
    ):
        raise DataDeletionReviewPacketError(
            "passed advisory packet violates required review invariants."
        )
    if review_status == REVIEW_STATUS_BLOCKED and observed_blocked == 0:
        raise DataDeletionReviewPacketError(
            "blocked advisory packet contains no blocked review check."
        )

    return DataDeletionReviewPacket(
        id=_positive_int(row.get("id"), "id"),
        request_id=expected_bindings["request_id"],
        dry_run_plan_id=expected_bindings["dry_run_plan_id"],
        backup_verification_run_id=expected_bindings[
            "backup_verification_run_id"
        ],
        quarantine_planning_run_id=expected_bindings[
            "quarantine_planning_run_id"
        ],
        combined_rehearsal_run_id=expected_bindings[
            "combined_rehearsal_run_id"
        ],
        fault_matrix_run_id=expected_bindings["fault_matrix_run_id"],
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
        fault_matrix_result_fingerprint_sha256=_fingerprint(
            row.get("fault_matrix_result_fingerprint_sha256"),
            "fault matrix fingerprint",
        ),
        fault_scenario_contract_fingerprint_sha256=_fingerprint(
            row.get("fault_scenario_contract_fingerprint_sha256"),
            "fault scenario fingerprint",
        ),
        input_contract_fingerprint_sha256=input_fingerprint,
        confirmation_text_sha256=confirmation_hash,
        packet_fingerprint_sha256=packet_fingerprint,
        review_status=review_status,
        packet_json=packet_json,
        input_count=int(metrics["input_count"]),
        passed_input_count=int(metrics["passed_input_count"]),
        blocked_input_count=int(metrics["blocked_input_count"]),
        check_count=int(metrics["check_count"]),
        passed_check_count=int(metrics["passed_check_count"]),
        blocked_check_count=int(metrics["blocked_check_count"]),
        fault_scenario_count=int(metrics["fault_scenario_count"]),
        passed_fault_scenario_count=int(
            metrics["passed_fault_scenario_count"]
        ),
        contained_fault_count=int(metrics["contained_fault_count"]),
        scratch_resources_removed=scratch_removed,
        generated_by=generated_by,
        generation_note=generation_note,
        generated_at_kst=generated_at_kst,
    )


def _packet_as_row(packet: DataDeletionReviewPacket) -> dict[str, Any]:
    return {
        **packet.to_summary_record(),
        "packet_json": deepcopy(packet.packet_json),
    }


def _assert_locked_request(row: Mapping[str, Any] | None, request: DataDeletionRequest) -> None:
    if not row or not all(
        (
            int(row.get("id") or 0) == request.id,
            str(row.get("status") or "") == "approved",
            row.get("executed_at_kst") is None,
            row.get("execution_summary_json") is None,
        )
    ):
        raise DataDeletionReviewPacketError(
            "deletion request changed before review-packet audit."
        )


def _assert_locked_plan(row: Mapping[str, Any] | None, plan: DataDeletionDryRunPlan) -> None:
    if not row or not all(
        (
            int(row.get("id") or 0) == plan.id,
            int(row.get("request_id") or 0) == plan.request_id,
            str(row.get("contract_version") or "") == plan.contract_version,
            str(row.get("plan_fingerprint_sha256") or "")
            == plan.plan_fingerprint_sha256,
        )
    ):
        raise DataDeletionReviewPacketError(
            "dry-run plan changed before review-packet audit."
        )


def _assert_locked_verification(
    row: Mapping[str, Any] | None,
    value: DataDeletionBackupVerificationRun,
) -> None:
    if not row or not all(
        (
            int(row.get("id") or 0) == value.id,
            str(row.get("contract_version") or "") == value.contract_version,
            int(row.get("dry_run_plan_id") or 0) == value.dry_run_plan_id,
            str(row.get("plan_fingerprint_sha256") or "")
            == value.plan_fingerprint_sha256,
            str(row.get("result_fingerprint_sha256") or "")
            == value.result_fingerprint_sha256,
            str(row.get("result_status") or "") == "passed",
        )
    ):
        raise DataDeletionReviewPacketError(
            "backup verification changed before review-packet audit."
        )


def _assert_locked_planning(
    row: Mapping[str, Any] | None,
    value: DataDeletionQuarantinePlanningRun,
) -> None:
    if not row or not all(
        (
            int(row.get("id") or 0) == value.id,
            str(row.get("contract_version") or "") == value.contract_version,
            int(row.get("dry_run_plan_id") or 0) == value.dry_run_plan_id,
            str(row.get("plan_fingerprint_sha256") or "")
            == value.plan_fingerprint_sha256,
            str(row.get("destination_contract_fingerprint_sha256") or "")
            == value.destination_contract_fingerprint_sha256,
            str(row.get("result_fingerprint_sha256") or "")
            == value.result_fingerprint_sha256,
            str(row.get("result_status") or "") == "passed",
            int(row.get("capacity_evidence_id") or 0)
            == int(value.capacity_evidence_id or 0),
        )
    ):
        raise DataDeletionReviewPacketError(
            "quarantine planning changed before review-packet audit."
        )


def _assert_locked_combined(
    row: Mapping[str, Any] | None,
    value: DataDeletionCombinedRehearsalRun,
) -> None:
    if not row or not all(
        (
            int(row.get("id") or 0) == value.id,
            str(row.get("contract_version") or "") == value.contract_version,
            int(row.get("dry_run_plan_id") or 0) == value.dry_run_plan_id,
            int(row.get("backup_verification_run_id") or 0)
            == value.backup_verification_run_id,
            int(row.get("quarantine_planning_run_id") or 0)
            == value.quarantine_planning_run_id,
            str(row.get("plan_fingerprint_sha256") or "")
            == value.plan_fingerprint_sha256,
            str(row.get("result_fingerprint_sha256") or "")
            == value.result_fingerprint_sha256,
            str(row.get("result_status") or "") == "passed",
            bool(row.get("scratch_resources_removed")) is True,
        )
    ):
        raise DataDeletionReviewPacketError(
            "combined rehearsal changed before review-packet audit."
        )


def _assert_locked_fault_matrix(
    row: Mapping[str, Any] | None,
    value: DataDeletionFaultMatrixRun,
) -> None:
    if not row or not all(
        (
            int(row.get("id") or 0) == value.id,
            str(row.get("contract_version") or "") == value.contract_version,
            int(row.get("dry_run_plan_id") or 0) == value.dry_run_plan_id,
            int(row.get("backup_verification_run_id") or 0)
            == value.backup_verification_run_id,
            int(row.get("quarantine_planning_run_id") or 0)
            == value.quarantine_planning_run_id,
            int(row.get("combined_rehearsal_run_id") or 0)
            == value.combined_rehearsal_run_id,
            str(row.get("result_fingerprint_sha256") or "")
            == value.result_fingerprint_sha256,
            str(row.get("result_status") or "") == value.result_status,
            str(row.get("scenario_contract_fingerprint_sha256") or "")
            == value.scenario_contract_fingerprint_sha256,
            bool(row.get("scratch_resources_removed"))
            == value.scratch_resources_removed,
        )
    ):
        raise DataDeletionReviewPacketError(
            "fault matrix changed before review-packet audit."
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


def _summary_safety_false(value: Mapping[str, Any], keys: tuple[str, ...]) -> bool:
    return all(value.get(key) is False for key in keys)


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_json_dump(value).encode("utf-8")).hexdigest()


def _json_dump(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _json_object(value: Any, label: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return deepcopy(value)
    if isinstance(value, (str, bytes, bytearray)):
        try:
            parsed = json.loads(
                value,
                object_pairs_hook=_unique_json_object,
            )
        except (TypeError, ValueError, UnicodeDecodeError) as exc:
            raise DataDeletionReviewPacketError(f"{label} is invalid JSON.") from exc
        if isinstance(parsed, dict):
            return parsed
    raise DataDeletionReviewPacketError(f"{label} must be a JSON object.")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise DataDeletionReviewPacketError(f"{label} must be a positive integer.")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise DataDeletionReviewPacketError(
            f"{label} must be a positive integer."
        ) from exc
    if number <= 0:
        raise DataDeletionReviewPacketError(f"{label} must be a positive integer.")
    return number


def _nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise DataDeletionReviewPacketError(
            f"{label} must be a nonnegative integer."
        )
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise DataDeletionReviewPacketError(
            f"{label} must be a nonnegative integer."
        ) from exc
    if number < 0:
        raise DataDeletionReviewPacketError(
            f"{label} must be a nonnegative integer."
        )
    return number


def _fingerprint(value: Any, label: str) -> str:
    text = str(value or "")
    if not _FINGERPRINT_PATTERN.fullmatch(text):
        raise DataDeletionReviewPacketError(
            f"{label} must be a lowercase SHA-256 fingerprint."
        )
    return text


def _required_text(value: Any, label: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum:
        raise DataDeletionReviewPacketError(
            f"{label} must contain 1 to {maximum} characters."
        )
    return text


def _optional_text(value: Any, label: str, maximum: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > maximum:
        raise DataDeletionReviewPacketError(
            f"{label} must contain at most {maximum} characters."
        )
    return text


def _datetime(value: Any, label: str) -> datetime:
    if isinstance(value, datetime):
        return to_kst(value)
    if isinstance(value, str):
        try:
            return to_kst(datetime.fromisoformat(value))
        except ValueError as exc:
            raise DataDeletionReviewPacketError(f"{label} is invalid.") from exc
    raise DataDeletionReviewPacketError(f"{label} is invalid.")


def _safe_error_message(exc: Exception) -> str:
    text = str(exc).strip()
    return text[:500] if text else exc.__class__.__name__


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in values if item))


def _begin(connection: Any) -> None:
    begin = getattr(connection, "begin", None)
    if callable(begin):
        begin()
