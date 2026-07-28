from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import MagicMock
import hashlib
import json
import unittest

from pubg_ai.data_deletion_backup_verifier import (
    BACKUP_VERIFIER_CONTRACT_VERSION,
    DataDeletionBackupVerificationRun,
)
from pubg_ai.data_deletion_combined_rehearsal import (
    COMBINED_REHEARSAL_CONTRACT_VERSION,
    DataDeletionCombinedRehearsalRun,
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
    fault_scenario_contract,
    fingerprint_fault_scenario_contract,
)
from pubg_ai.data_deletion_quarantine_planner import (
    QUARANTINE_PLANNER_CONTRACT_VERSION,
    DataDeletionQuarantinePlanningRun,
)
from pubg_ai.data_deletion_requests import DataDeletionRequest
from pubg_ai.data_deletion_review_packet import (
    REVIEW_PACKET_CONFIRMATION_PREFIX,
    REVIEW_STATUS_BLOCKED,
    REVIEW_STATUS_PASSED,
    DataDeletionReviewPacketError,
    DataDeletionReviewPacketService,
    _review_packet_from_row,
    canonical_review_packet_bytes,
)


class DataDeletionReviewPacketTests(unittest.TestCase):
    def setUp(self) -> None:
        self.request = _request()
        self.plan = _plan(self.request)
        self.verification = _verification(self.plan)
        self.planning = _planning(self.plan)
        self.combined = _combined(
            self.plan,
            self.verification,
            self.planning,
        )
        self.matrix = _matrix(
            self.plan,
            self.verification,
            self.planning,
            self.combined,
        )
        self.connection = _AuditConnection(
            self.request,
            self.plan,
            self.verification,
            self.planning,
            self.combined,
            self.matrix,
        )
        self.backup_service = MagicMock()
        self.backup_service.dry_run_service.list_plans.return_value = [self.plan]
        self.verifier_service = MagicMock()
        self.verifier_service.get_run.return_value = self.verification
        self.verifier_service.list_runs.return_value = [self.verification]
        self.planner_service = MagicMock()
        self.planner_service.get_run.return_value = self.planning
        self.planner_service.list_runs.return_value = [self.planning]
        self.combined_service = MagicMock()
        self.combined_service.get_run.return_value = self.combined
        self.combined_service.list_runs.return_value = [self.combined]
        self.matrix_service = MagicMock()
        self.matrix_service.get_run.return_value = self.matrix
        self.matrix_service.list_runs.return_value = [self.matrix]

    def test_state_binds_latest_chain_and_never_authorizes(self) -> None:
        state = self._service().packet_state(self.request)

        self.assertTrue(state["review_packet_allowed"])
        self.assertEqual(state["review_packet_blockers"], [])
        self.assertEqual(
            state["packet_candidate"]["predicted_review_status"],
            REVIEW_STATUS_PASSED,
        )
        self.assertTrue(
            state["packet_candidate"]["confirmation_text"].startswith(
                REVIEW_PACKET_CONFIRMATION_PREFIX
            )
        )
        self.assertEqual(
            state["packet_candidate"]["fault_matrix"]["id"],
            self.matrix.id,
        )
        self.assertFalse(state["authorization_granted"])
        self.assertFalse(state["readiness_promoted"])
        self.assertFalse(state["appends_readiness_evidence"])
        self.assertFalse(state["execution_enabled"])
        self.assertFalse(state["execution_ready"])
        self.assertEqual(
            state["execution_blockers"],
            ["review_packet_is_advisory_only", "executor_not_implemented"],
        )

    def test_passed_packet_is_canonical_and_only_appends_packet_audit(self) -> None:
        state = self._service().packet_state(self.request)
        packet = self._service().generate(
            self.request,
            fault_matrix_run_id=self.matrix.id,
            confirmation_text=state["packet_candidate"]["confirmation_text"],
            actor_id="local-owner",
            note="operator review export",
            reference_kst=datetime(2026, 7, 30, 10, 0, 0),
        )

        self.assertEqual(packet.review_status, REVIEW_STATUS_PASSED)
        self.assertEqual(packet.input_count, 6)
        self.assertEqual(packet.passed_input_count, 6)
        self.assertEqual(packet.blocked_input_count, 0)
        self.assertEqual(packet.fault_scenario_count, 4)
        self.assertEqual(packet.passed_fault_scenario_count, 4)
        self.assertEqual(packet.contained_fault_count, 4)
        self.assertTrue(packet.scratch_resources_removed)
        self.assertFalse(packet.packet_json["safety"]["authorization_granted"])
        self.assertFalse(packet.packet_json["safety"]["execution_ready"])
        self.assertEqual(
            self.connection.dml,
            ["INSERT INTO data_deletion_readiness_review_packets"],
        )
        exported = canonical_review_packet_bytes(packet)
        self.assertEqual(exported, canonical_review_packet_bytes(packet))
        self.assertTrue(exported.endswith(b"\n"))
        payload = json.loads(exported)
        self.assertEqual(
            payload["packet_fingerprint_sha256"],
            packet.packet_fingerprint_sha256,
        )
        self.assertTrue(self.connection.committed)

    def test_blocked_fault_matrix_is_captured_as_advisory_blocked_packet(self) -> None:
        self.matrix = _blocked_matrix(self.matrix)
        self.connection = _AuditConnection(
            self.request,
            self.plan,
            self.verification,
            self.planning,
            self.combined,
            self.matrix,
        )
        self.matrix_service.get_run.return_value = self.matrix
        self.matrix_service.list_runs.return_value = [self.matrix]
        state = self._service().packet_state(self.request)

        self.assertTrue(state["review_packet_allowed"])
        self.assertEqual(
            state["packet_candidate"]["predicted_review_status"],
            REVIEW_STATUS_BLOCKED,
        )
        packet = self._service().generate(
            self.request,
            fault_matrix_run_id=self.matrix.id,
            confirmation_text=state["packet_candidate"]["confirmation_text"],
            actor_id="local-owner",
        )

        self.assertEqual(packet.review_status, REVIEW_STATUS_BLOCKED)
        self.assertEqual(packet.passed_input_count, 5)
        self.assertEqual(packet.blocked_input_count, 1)
        self.assertGreater(packet.blocked_check_count, 0)
        self.assertFalse(packet.scratch_resources_removed)
        self.assertFalse(packet.packet_json["assessment"]["authorization_granted"])
        self.assertEqual(
            self.connection.dml,
            ["INSERT INTO data_deletion_readiness_review_packets"],
        )
        self.assertTrue(canonical_review_packet_bytes(packet))

    def test_wrong_confirmation_blocks_before_transaction_or_audit(self) -> None:
        with self.assertRaisesRegex(DataDeletionReviewPacketError, "confirmation"):
            self._service().generate(
                self.request,
                fault_matrix_run_id=self.matrix.id,
                confirmation_text="GENERATE SOMETHING ELSE",
                actor_id="local-owner",
            )

        self.assertFalse(self.connection.began)
        self.assertEqual(self.connection.dml, [])

    def test_newer_verification_makes_fault_matrix_chain_stale(self) -> None:
        newer = replace(
            self.verification,
            id=self.verification.id + 1,
            result_fingerprint_sha256="8" * 64,
        )
        self.verifier_service.list_runs.return_value = [newer]

        state = self._service().packet_state(self.request)

        self.assertFalse(state["review_packet_allowed"])
        self.assertIn(
            "fault-matrix backup verification is not the latest run",
            state["review_packet_blockers"],
        )
        self.assertIsNone(state["packet_candidate"])
        self.assertEqual(self.connection.dml, [])

    def test_reader_rejects_rehashed_authorization_flag(self) -> None:
        packet = self._passed_packet()
        row = _packet_row(packet)
        changed = deepcopy(packet.packet_json)
        changed["safety"]["authorization_granted"] = True
        _rehash_packet(row, changed)

        with self.assertRaisesRegex(
            DataDeletionReviewPacketError,
            "non-authorization safety contract",
        ):
            _review_packet_from_row(row)

    def test_reader_rejects_rehashed_generation_actor_change(self) -> None:
        packet = self._passed_packet()
        row = _packet_row(packet)
        changed = deepcopy(packet.packet_json)
        changed["generation"]["generated_by"] = "different-operator"
        _rehash_packet(row, changed)

        with self.assertRaisesRegex(
            DataDeletionReviewPacketError,
            "generation metadata bindings",
        ):
            _review_packet_from_row(row)

    def test_reader_rejects_rehashed_input_id_contract_change(self) -> None:
        packet = self._passed_packet()
        row = _packet_row(packet)
        changed = deepcopy(packet.packet_json)
        changed["input_contract"]["fault_matrix_run_id"] = 9999
        input_fingerprint = _canonical_fingerprint(changed["input_contract"])
        changed["input_contract_fingerprint_sha256"] = input_fingerprint
        row["input_contract_fingerprint_sha256"] = input_fingerprint
        _rehash_packet(row, changed)

        with self.assertRaisesRegex(
            DataDeletionReviewPacketError,
            "input contract ID bindings",
        ):
            _review_packet_from_row(row)

    def _passed_packet(self):
        state = self._service().packet_state(self.request)
        return self._service().generate(
            self.request,
            fault_matrix_run_id=self.matrix.id,
            confirmation_text=state["packet_candidate"]["confirmation_text"],
            actor_id="local-owner",
            reference_kst=datetime(2026, 7, 30, 10, 0, 0),
        )

    def _service(self) -> DataDeletionReviewPacketService:
        return DataDeletionReviewPacketService(
            self.connection,
            backup_service=self.backup_service,
            verifier_service=self.verifier_service,
            planner_service=self.planner_service,
            combined_rehearsal_service=self.combined_service,
            fault_matrix_service=self.matrix_service,
        )


class _AuditConnection:
    def __init__(self, request, plan, verification, planning, combined, matrix):
        self.request = request
        self.plan = plan
        self.verification = verification
        self.planning = planning
        self.combined = combined
        self.matrix = matrix
        self.cursor_obj = _AuditCursor(self)
        self.began = False
        self.committed = False
        self.rolled_back = False
        self.dml: list[str] = []

    def cursor(self):
        return self.cursor_obj

    def begin(self):
        self.began = True

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class _AuditCursor:
    def __init__(self, connection: _AuditConnection):
        self.connection = connection
        self._rows: list[dict] = []
        self.lastrowid = 2201

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def execute(self, query, params=()):
        normalized = " ".join(str(query).split())
        value = self.connection
        self._rows = []
        if normalized.startswith("INSERT INTO"):
            self.connection.dml.append(normalized.split(" (")[0])
        elif "FROM data_deletion_readiness_review_packets" in normalized:
            self._rows = []
        elif "FROM data_deletion_requests" in normalized:
            self._rows = [
                {
                    "id": value.request.id,
                    "status": value.request.status,
                    "executed_at_kst": value.request.executed_at_kst,
                    "execution_summary_json": value.request.execution_summary_json,
                }
            ]
        elif "FROM data_deletion_dry_run_plans" in normalized:
            self._rows = [
                {
                    "id": value.plan.id,
                    "request_id": value.plan.request_id,
                    "contract_version": value.plan.contract_version,
                    "plan_fingerprint_sha256": value.plan.plan_fingerprint_sha256,
                }
            ]
        elif "FROM data_deletion_backup_verification_runs" in normalized:
            item = value.verification
            self._rows = [
                {
                    "id": item.id,
                    "contract_version": item.contract_version,
                    "dry_run_plan_id": item.dry_run_plan_id,
                    "plan_fingerprint_sha256": item.plan_fingerprint_sha256,
                    "result_fingerprint_sha256": item.result_fingerprint_sha256,
                    "result_status": item.result_status,
                }
            ]
        elif "FROM data_deletion_quarantine_planning_runs" in normalized:
            item = value.planning
            self._rows = [
                {
                    "id": item.id,
                    "contract_version": item.contract_version,
                    "dry_run_plan_id": item.dry_run_plan_id,
                    "plan_fingerprint_sha256": item.plan_fingerprint_sha256,
                    "destination_contract_fingerprint_sha256": (
                        item.destination_contract_fingerprint_sha256
                    ),
                    "result_fingerprint_sha256": item.result_fingerprint_sha256,
                    "result_status": item.result_status,
                    "capacity_evidence_id": item.capacity_evidence_id,
                }
            ]
        elif "FROM data_deletion_combined_rehearsal_runs" in normalized:
            item = value.combined
            self._rows = [
                {
                    "id": item.id,
                    "contract_version": item.contract_version,
                    "dry_run_plan_id": item.dry_run_plan_id,
                    "backup_verification_run_id": item.backup_verification_run_id,
                    "quarantine_planning_run_id": item.quarantine_planning_run_id,
                    "plan_fingerprint_sha256": item.plan_fingerprint_sha256,
                    "result_fingerprint_sha256": item.result_fingerprint_sha256,
                    "result_status": item.result_status,
                    "scratch_resources_removed": item.scratch_resources_removed,
                }
            ]
        elif "FROM data_deletion_combined_fault_matrix_runs" in normalized:
            item = value.matrix
            self._rows = [
                {
                    "id": item.id,
                    "contract_version": item.contract_version,
                    "dry_run_plan_id": item.dry_run_plan_id,
                    "backup_verification_run_id": item.backup_verification_run_id,
                    "quarantine_planning_run_id": item.quarantine_planning_run_id,
                    "combined_rehearsal_run_id": item.combined_rehearsal_run_id,
                    "result_fingerprint_sha256": item.result_fingerprint_sha256,
                    "result_status": item.result_status,
                    "scenario_contract_fingerprint_sha256": (
                        item.scenario_contract_fingerprint_sha256
                    ),
                    "scratch_resources_removed": item.scratch_resources_removed,
                }
            ]

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


def _request() -> DataDeletionRequest:
    requested = datetime(2026, 7, 30, 9, 0, 0)
    return DataDeletionRequest(
        id=17,
        registered_player_id=7,
        account_id="account.target",
        shard="steam",
        player_name="Yuuki_Asuna---",
        deletion_scope="all",
        status="approved",
        reason="operator packet test",
        requested_by_discord_user_id="100",
        requested_guild_id="200",
        requested_channel_id="300",
        requested_at_kst=requested,
        expires_at_kst=requested + timedelta(hours=24),
        reviewed_by="local:owner",
        reviewed_at_kst=requested,
        review_note="approved",
        updated_at_kst=requested,
    )


def _plan(request: DataDeletionRequest) -> DataDeletionDryRunPlan:
    plan_json = {
        "contract_version": DRY_RUN_CONTRACT_VERSION,
        "request_id": request.id,
        "target": {
            "account_id": request.account_id,
            "shard": request.shard,
            "player_name": request.player_name,
        },
        "safety": {
            "execution_enabled": False,
            "execution_ready": False,
            "execution_blockers": ["executor_not_implemented"],
        },
        "database_operations": [
            {
                "sequence": 1,
                "phase": "remove_registration",
                "action": "delete_rows_planned",
                "table": "registered_players",
                "selector": {
                    "kind": "target_identity",
                    "account_id": request.account_id,
                    "shard": request.shard,
                },
                "estimated_rows": 2,
                "source_category": "registration",
                "source_relationship": "player_owned",
                "mutation_enabled": False,
            }
        ],
        "file_operations": [],
        "row_exclusions": [],
        "file_exclusions": [],
        "audit_table_exclusions": [
            {
                "table": table,
                "reason": "immutable deletion workflow audit data is never a target",
            }
            for table in AUDIT_TABLE_EXCLUSIONS
        ],
    }
    fingerprint = fingerprint_dry_run_plan(plan_json)
    return DataDeletionDryRunPlan(
        id=1201,
        request_id=request.id,
        preview_snapshot_id=501,
        confirmation_id=701,
        contract_version=DRY_RUN_CONTRACT_VERSION,
        source_fingerprint_sha256="a" * 64,
        plan_fingerprint_sha256=fingerprint,
        plan_json=plan_json,
        operation_count=1,
        candidate_row_count=2,
        candidate_file_count=1,
        candidate_file_bytes=64,
        excluded_row_count=0,
        excluded_file_count=0,
        generated_by="local-owner",
        generation_note=None,
        generated_at_kst=datetime(2026, 7, 30, 9, 10, 0),
    )


def _verification(plan: DataDeletionDryRunPlan) -> DataDeletionBackupVerificationRun:
    return DataDeletionBackupVerificationRun(
        id=1401,
        request_id=plan.request_id,
        dry_run_plan_id=plan.id,
        contract_version=BACKUP_VERIFIER_CONTRACT_VERSION,
        plan_fingerprint_sha256=plan.plan_fingerprint_sha256,
        evidence_set_fingerprint_sha256="b" * 64,
        evidence_record_ids={"mysql_target_backup": 91},
        build_id="build-1",
        manifest_path="C:/isolated-backup/build-1/manifest.json",
        expected_manifest_sha256="c" * 64,
        observed_manifest_sha256="c" * 64,
        manifest_fingerprint_sha256="d" * 64,
        result_fingerprint_sha256="e" * 64,
        result_status="passed",
        result_json={"verification_status": "passed"},
        artifact_count=1,
        verified_artifact_count=1,
        check_count=1,
        passed_check_count=1,
        blocker_count=0,
        verified_by="local-owner",
        verification_note=None,
        verified_at_kst=datetime(2026, 7, 30, 9, 20, 0),
    )


def _planning(plan: DataDeletionDryRunPlan) -> DataDeletionQuarantinePlanningRun:
    return DataDeletionQuarantinePlanningRun(
        id=1601,
        request_id=plan.request_id,
        dry_run_plan_id=plan.id,
        contract_version=QUARANTINE_PLANNER_CONTRACT_VERSION,
        plan_fingerprint_sha256=plan.plan_fingerprint_sha256,
        destination_contract_fingerprint_sha256="1" * 64,
        quarantine_root="D:/BackUP/quarantine",
        result_fingerprint_sha256="2" * 64,
        result_status="passed",
        result_json={
            "planning_status": "passed",
            "file_operations": [],
            "metrics": {"candidate_file_count": 1},
            "safety": {"execution_enabled": False},
        },
        candidate_file_count=1,
        candidate_file_bytes=64,
        safety_reserve_bytes=64 * 1024 * 1024,
        required_free_bytes=64 * 1024 * 1024 + 64,
        observed_free_bytes=1024 * 1024 * 1024,
        source_verified_file_count=1,
        source_verified_bytes=64,
        target_conflict_count=0,
        check_count=1,
        passed_check_count=1,
        blocker_count=0,
        capacity_evidence_id=92,
        planned_by="local-owner",
        planning_note=None,
        planned_at_kst=datetime(2026, 7, 30, 9, 30, 0),
    )


def _combined(plan, verification, planning) -> DataDeletionCombinedRehearsalRun:
    return DataDeletionCombinedRehearsalRun(
        id=1801,
        request_id=plan.request_id,
        dry_run_plan_id=plan.id,
        backup_verification_run_id=verification.id,
        quarantine_planning_run_id=planning.id,
        contract_version=COMBINED_REHEARSAL_CONTRACT_VERSION,
        plan_fingerprint_sha256=plan.plan_fingerprint_sha256,
        backup_verification_result_fingerprint_sha256=(
            verification.result_fingerprint_sha256
        ),
        current_backup_revalidation_result_fingerprint_sha256="3" * 64,
        quarantine_planning_result_fingerprint_sha256=(
            planning.result_fingerprint_sha256
        ),
        destination_contract_fingerprint_sha256=(
            planning.destination_contract_fingerprint_sha256
        ),
        result_fingerprint_sha256="4" * 64,
        result_status="passed",
        result_json={
            "safety": {
                "production_database_rows_modified": False,
                "production_source_files_opened": False,
                "production_source_files_modified": False,
                "deletion_performed": False,
                "execution_enabled": False,
                "execution_ready": False,
            }
        },
        mysql_candidate_table_count=1,
        mysql_candidate_row_count=2,
        mysql_delete_operation_count=1,
        mysql_deleted_row_count=2,
        mysql_rolled_back_row_count=2,
        preserved_descriptor_count=1,
        quarantine_fixture_file_count=1,
        quarantine_recovery_case_count=5,
        quarantine_recovered_case_count=4,
        scratch_resources_removed=True,
        check_count=1,
        passed_check_count=1,
        blocker_count=0,
        run_by="local-owner",
        rehearsal_note=None,
        run_at_kst=datetime(2026, 7, 30, 9, 40, 0),
    )


def _matrix(plan, verification, planning, combined) -> DataDeletionFaultMatrixRun:
    specs = fault_scenario_contract()
    scenarios = [
        {
            **spec,
            "status": "passed",
            "fault_observed": True,
            "fault_contained": True,
            "scratch_removed": True,
            "production_data_unchanged": True,
            "message": "fault observed, contained, and cleaned",
        }
        for spec in specs
    ]
    result = {
        "scenarios": scenarios,
        "checks": [],
        "metrics": {
            "scenario_count": 4,
            "passed_scenario_count": 4,
            "contained_fault_count": 4,
            "mysql_scenario_count": 1,
            "quarantine_scenario_count": 3,
        },
        "blockers": [],
        "safety": {
            "scratch_resources_removed": True,
            "production_database_rows_modified": False,
            "production_source_files_opened": False,
            "production_source_files_modified": False,
            "production_quarantine_performed": False,
            "production_restore_performed": False,
            "deletion_performed": False,
            "execution_enabled": False,
            "execution_ready": False,
        },
    }
    return DataDeletionFaultMatrixRun(
        id=1901,
        request_id=plan.request_id,
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
        scenario_contract_fingerprint_sha256=(
            fingerprint_fault_scenario_contract(specs)
        ),
        result_fingerprint_sha256="5" * 64,
        result_status="passed",
        result_json=result,
        scenario_count=4,
        passed_scenario_count=4,
        contained_fault_count=4,
        mysql_scenario_count=1,
        quarantine_scenario_count=3,
        scratch_resources_removed=True,
        check_count=1,
        passed_check_count=1,
        blocker_count=0,
        run_by="local-owner",
        rehearsal_note=None,
        run_at_kst=datetime(2026, 7, 30, 9, 50, 0),
    )


def _blocked_matrix(matrix: DataDeletionFaultMatrixRun) -> DataDeletionFaultMatrixRun:
    result = deepcopy(matrix.result_json)
    result["scenarios"][-1].update(
        {
            "status": "blocked",
            "fault_observed": False,
            "fault_contained": False,
            "scratch_removed": False,
            "production_data_unchanged": False,
            "message": "cleanup fault did not converge",
        }
    )
    result["metrics"].update(
        {
            "passed_scenario_count": 3,
            "contained_fault_count": 3,
        }
    )
    result["blockers"] = ["cleanup fault did not converge"]
    result["safety"]["scratch_resources_removed"] = False
    return replace(
        matrix,
        result_fingerprint_sha256="6" * 64,
        result_status="blocked",
        result_json=result,
        passed_scenario_count=3,
        contained_fault_count=3,
        scratch_resources_removed=False,
        passed_check_count=0,
        blocker_count=1,
    )


def _packet_row(packet) -> dict:
    return {**packet.to_summary_record(), "packet_json": deepcopy(packet.packet_json)}


def _rehash_packet(row: dict, packet_json: dict) -> None:
    body = deepcopy(packet_json)
    body.pop("packet_fingerprint_sha256", None)
    fingerprint = _canonical_fingerprint(body)
    packet_json["packet_fingerprint_sha256"] = fingerprint
    row["packet_json"] = packet_json
    row["packet_fingerprint_sha256"] = fingerprint


def _canonical_fingerprint(value) -> str:
    body = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


if __name__ == "__main__":
    unittest.main()
