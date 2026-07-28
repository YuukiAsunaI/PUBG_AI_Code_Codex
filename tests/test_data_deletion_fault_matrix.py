from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
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
    DataDeletionFaultMatrixError,
    DataDeletionFaultMatrixService,
    _fault_matrix_run_from_row,
    expected_fault_matrix_confirmation,
    fault_scenario_contract,
    fingerprint_fault_scenario_contract,
)
from pubg_ai.data_deletion_quarantine_planner import (
    QUARANTINE_PLANNER_CONTRACT_VERSION,
    DataDeletionQuarantinePlanningRun,
)
from pubg_ai.data_deletion_requests import DataDeletionRequest


class DataDeletionFaultMatrixTests(unittest.TestCase):
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
        self.connection = _AuditConnection(
            self.combined,
            self.verification,
            self.planning,
        )
        self.backup_service = MagicMock()
        self.backup_service.require_latest_plan.return_value = self.plan
        self.backup_service.dry_run_service.list_plans.return_value = [self.plan]
        self.verifier_service = MagicMock()
        self.verifier_service.get_run.return_value = self.verification
        self.verifier_service.revalidate_passed_run.return_value = SimpleNamespace(
            plan=self.plan,
        )
        self.quarantine_service = MagicMock()
        self.quarantine_service.planner_service.get_run.return_value = self.planning
        self.quarantine_service.planner_service.list_runs.return_value = [self.planning]
        self.quarantine_service.run_bound_synthetic_fault_state.side_effect = (
            lambda request, quarantine_planning_run_id, fault_point, reference_kst=None: (
                self.plan,
                self.planning,
                _quarantine_fault_result(fault_point),
            )
        )
        self.combined_service = MagicMock()
        self.combined_service.get_run.return_value = self.combined
        self.combined_service.list_runs.return_value = [self.combined]
        self.mysql_runner = MagicMock(return_value=_mysql_fault_result())

    def test_passed_matrix_contains_all_faults_and_appends_only_its_audit(self) -> None:
        run = self._service().run(
            self.request,
            combined_rehearsal_run_id=self.combined.id,
            confirmation_text=self._confirmation(),
            actor_id="local-owner",
            note="deterministic fault matrix",
            reference_kst=datetime(2026, 7, 29, 10, 0, 0),
        )

        self.assertEqual(run.result_status, "passed")
        self.assertEqual(run.scenario_count, 4)
        self.assertEqual(run.passed_scenario_count, 4)
        self.assertEqual(run.contained_fault_count, 4)
        self.assertEqual(run.mysql_scenario_count, 1)
        self.assertEqual(run.quarantine_scenario_count, 3)
        self.assertTrue(run.scratch_resources_removed)
        self.assertFalse(run.result_json["safety"]["execution_enabled"])
        self.assertFalse(
            run.result_json["safety"]["production_database_rows_modified"]
        )
        self.assertEqual(
            self.connection.dml,
            ["INSERT INTO data_deletion_combined_fault_matrix_runs"],
        )
        self.assertEqual(
            self.quarantine_service.run_bound_synthetic_fault_state.call_count,
            3,
        )
        self.assertTrue(self.connection.committed)

    def test_wrong_confirmation_blocks_before_any_fault_or_audit(self) -> None:
        with self.assertRaisesRegex(DataDeletionFaultMatrixError, "confirmation"):
            self._service().run(
                self.request,
                combined_rehearsal_run_id=self.combined.id,
                confirmation_text="RUN SOMETHING ELSE",
                actor_id="local-owner",
            )

        self.mysql_runner.assert_not_called()
        self.quarantine_service.run_bound_synthetic_fault_state.assert_not_called()
        self.assertEqual(self.connection.dml, [])

    def test_one_fault_failure_is_audited_blocked_and_later_scenarios_run(self) -> None:
        def run_quarantine(
            request,
            quarantine_planning_run_id,
            fault_point,
            reference_kst=None,
        ):
            if fault_point == "after_verified_copy":
                raise RuntimeError("injected recovery did not converge")
            return self.plan, self.planning, _quarantine_fault_result(fault_point)

        self.quarantine_service.run_bound_synthetic_fault_state.side_effect = (
            run_quarantine
        )

        run = self._service().run(
            self.request,
            combined_rehearsal_run_id=self.combined.id,
            confirmation_text=self._confirmation(),
            actor_id="local-owner",
        )

        self.assertEqual(run.result_status, "blocked")
        self.assertEqual(run.scenario_count, 4)
        self.assertEqual(run.passed_scenario_count, 3)
        self.assertEqual(
            self.quarantine_service.run_bound_synthetic_fault_state.call_count,
            3,
        )
        self.assertEqual(
            self.connection.dml,
            ["INSERT INTO data_deletion_combined_fault_matrix_runs"],
        )
        failed = next(
            item
            for item in run.result_json["scenarios"]
            if item["fault_point"] == "after_verified_copy"
        )
        self.assertFalse(failed["production_data_unchanged"])
        boundary = next(
            item
            for item in run.result_json["checks"]
            if item["key"] == "production_mutation_boundary"
        )
        self.assertEqual(boundary["status"], "blocked")

    def test_state_requires_latest_combined_and_never_enables_execution(self) -> None:
        state = self._service().matrix_state(self.request)

        self.assertTrue(state["fault_matrix_allowed"])
        self.assertEqual(
            state["fault_matrix_candidate"]["confirmation_text"],
            self._confirmation(),
        )
        self.assertEqual(state["scenario_count"], 4)
        self.assertTrue(state["appends_fault_matrix_audit_row"])
        self.assertFalse(state["appends_combined_rehearsal_audit_row"])
        self.assertFalse(state["appends_readiness_evidence"])
        self.assertFalse(state["production_database_rows_modified"])
        self.assertFalse(state["execution_enabled"])

    def test_stale_audit_exclusion_contract_blocks_before_faults(self) -> None:
        stale_json = deepcopy(self.plan.plan_json)
        stale_json["audit_table_exclusions"] = stale_json[
            "audit_table_exclusions"
        ][:-1]
        stale_plan = replace(
            self.plan,
            plan_json=stale_json,
            plan_fingerprint_sha256=fingerprint_dry_run_plan(stale_json),
        )
        self.backup_service.require_latest_plan.return_value = stale_plan

        with self.assertRaisesRegex(DataDeletionFaultMatrixError, "audit-table"):
            self._service().run(
                self.request,
                combined_rehearsal_run_id=self.combined.id,
                confirmation_text=self._confirmation(),
                actor_id="local-owner",
            )

        self.mysql_runner.assert_not_called()
        self.assertEqual(self.connection.dml, [])

    def test_audit_reader_rejects_rehashed_unsafe_production_flag(self) -> None:
        run = self._passed_run()
        row = _audit_row(run)
        unsafe = deepcopy(run.result_json)
        unsafe["safety"]["production_source_files_opened"] = True
        row["result_json"] = unsafe
        row["result_fingerprint_sha256"] = _canonical_fingerprint(unsafe)

        with self.assertRaisesRegex(DataDeletionFaultMatrixError, "safety contract"):
            _fault_matrix_run_from_row(row)

    def test_audit_reader_rejects_rehashed_scenario_contract_change(self) -> None:
        run = self._passed_run()
        row = _audit_row(run)
        changed = deepcopy(run.result_json)
        changed["scenario_contract"][0]["fault_point"] = "another-point"
        changed["scenario_contract_fingerprint_sha256"] = _canonical_fingerprint(
            changed["scenario_contract"]
        )
        row["scenario_contract_fingerprint_sha256"] = changed[
            "scenario_contract_fingerprint_sha256"
        ]
        row["result_json"] = changed
        row["result_fingerprint_sha256"] = _canonical_fingerprint(changed)

        with self.assertRaisesRegex(DataDeletionFaultMatrixError, "scenario contract"):
            _fault_matrix_run_from_row(row)

    def _passed_run(self):
        return self._service().run(
            self.request,
            combined_rehearsal_run_id=self.combined.id,
            confirmation_text=self._confirmation(),
            actor_id="local-owner",
        )

    def _service(self) -> DataDeletionFaultMatrixService:
        return DataDeletionFaultMatrixService(
            self.connection,
            backup_service=self.backup_service,
            verifier_service=self.verifier_service,
            quarantine_rehearsal_service=self.quarantine_service,
            combined_rehearsal_service=self.combined_service,
            scratch_connection_factory=MagicMock(),
            backup_root=Path("C:/isolated-backup"),
            expected_database_name="pubg_ai",
            mysql_fault_runner=self.mysql_runner,
        )

    def _confirmation(self) -> str:
        scenario_fingerprint = fingerprint_fault_scenario_contract(
            fault_scenario_contract()
        )
        return expected_fault_matrix_confirmation(
            self.request.id,
            self.plan.id,
            self.combined.id,
            self.combined.result_fingerprint_sha256,
            self.verification.id,
            self.verification.result_fingerprint_sha256,
            self.planning.id,
            self.planning.result_fingerprint_sha256,
            self.planning.destination_contract_fingerprint_sha256,
            scenario_fingerprint,
        )


class _AuditConnection:
    def __init__(self, combined, verification, planning) -> None:
        self.combined = combined
        self.verification = verification
        self.planning = planning
        self.dml: list[str] = []
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return _AuditCursor(self)

    def begin(self) -> None:
        pass

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


class _AuditCursor:
    def __init__(self, connection: _AuditConnection) -> None:
        self.connection = connection
        self._rows: list[dict] = []
        self.lastrowid = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, statement, parameters=None) -> None:
        normalized = " ".join(str(statement).split())
        if "FROM data_deletion_combined_rehearsal_runs" in normalized:
            item = self.connection.combined
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
        elif "FROM data_deletion_backup_verification_runs" in normalized:
            item = self.connection.verification
            self._rows = [
                {
                    "id": item.id,
                    "contract_version": item.contract_version,
                    "plan_fingerprint_sha256": item.plan_fingerprint_sha256,
                    "result_fingerprint_sha256": item.result_fingerprint_sha256,
                    "result_status": item.result_status,
                }
            ]
        elif "FROM data_deletion_quarantine_planning_runs" in normalized:
            item = self.connection.planning
            self._rows = [
                {
                    "id": item.id,
                    "contract_version": item.contract_version,
                    "plan_fingerprint_sha256": item.plan_fingerprint_sha256,
                    "destination_contract_fingerprint_sha256": (
                        item.destination_contract_fingerprint_sha256
                    ),
                    "result_fingerprint_sha256": item.result_fingerprint_sha256,
                    "result_status": item.result_status,
                    "capacity_evidence_id": item.capacity_evidence_id,
                }
            ]
        elif normalized.startswith(
            "INSERT INTO data_deletion_combined_fault_matrix_runs"
        ):
            self.connection.dml.append(
                "INSERT INTO data_deletion_combined_fault_matrix_runs"
            )
            self.lastrowid = 1901
        elif "FROM data_deletion_combined_fault_matrix_runs" in normalized:
            self._rows = []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


def _request() -> DataDeletionRequest:
    requested = datetime(2026, 7, 29, 9, 0, 0)
    return DataDeletionRequest(
        id=17,
        registered_player_id=7,
        account_id="account.target",
        shard="steam",
        player_name="Yuuki_Asuna---",
        deletion_scope="all",
        status="approved",
        reason="fault matrix test",
        requested_by_discord_user_id="100",
        requested_guild_id="200",
        requested_channel_id="300",
        requested_at_kst=requested,
        expires_at_kst=requested + timedelta(hours=24),
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
        generated_at_kst=datetime(2026, 7, 29, 9, 10, 0),
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
        verified_at_kst=datetime(2026, 7, 29, 9, 20, 0),
    )


def _planning(plan: DataDeletionDryRunPlan) -> DataDeletionQuarantinePlanningRun:
    file_operation = {
        "sequence": 1,
        "record_id": 41,
        "artifact_type": "timeline",
        "match_id": "match-1",
        "source_relative_path": "timeline/steam/match-1.json",
        "target_relative_path": "request-17/timeline/steam/match-1.json",
        "declared_size_bytes": 64,
        "sha256": "9" * 64,
        "target_exists": False,
        "mutation_enabled": False,
    }
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
            "file_operations": [file_operation],
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
        planned_at_kst=datetime(2026, 7, 29, 9, 30, 0),
    )


def _combined(plan, verification, planning) -> DataDeletionCombinedRehearsalRun:
    result = {
        "safety": {
            "production_database_rows_modified": False,
            "production_source_files_opened": False,
            "production_source_files_modified": False,
            "deletion_performed": False,
            "execution_enabled": False,
            "execution_ready": False,
        }
    }
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
        result_json=result,
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
        run_at_kst=datetime(2026, 7, 29, 9, 40, 0),
    )


def _mysql_fault_result() -> dict:
    baseline = {"registered_players": {"row_count": 2, "row_set": "a"}}
    deletion = {
        "contract_version": "temporary-mysql-deletion-fault-rehearsal-v1",
        "candidate_table_count": 1,
        "candidate_row_count": 2,
        "deleted_row_count": 2,
        "rolled_back_row_count": 2,
        "baseline": baseline,
        "after_rollback": deepcopy(baseline),
        "fault_injection": {
            "requested": True,
            "fault_point": "after_first_delete",
            "observed": True,
            "temporary_state_changed": True,
        },
        "safety": {
            "temporary_tables_only": True,
            "injected_statement_failure_exercised": True,
            "transaction_rolled_back": True,
            "shared_data_preserved": True,
            "audit_data_preserved": True,
            "production_database_rows_modified": False,
            "production_files_modified": False,
            "deletion_performed": False,
            "execution_enabled": False,
            "execution_ready": False,
        },
    }
    return {
        "checks": [
            {
                "key": "temporary_deletion_rollback",
                "status": "passed",
                "message": "rollback restored temporary rows",
            },
            {
                "key": "scratch_cleanup",
                "status": "passed",
                "message": "temporary tables removed",
            },
        ],
        "metrics": {},
        "deletion_exercise": deletion,
    }


def _quarantine_fault_result(point: str) -> dict:
    cleanup_fault = point == "cleanup_first_attempt"
    return {
        "result_status": "passed",
        "fault_injection": {
            "fault_point": point,
            "observed": True,
            "recovered": True,
            "emergency_cleanup_passed": cleanup_fault,
        },
        "checks": [
            {
                "key": "scratch_cleanup",
                "status": "passed",
                "message": "owned scratch removed",
            }
        ],
        "safety": {
            "synthetic_fixtures_only": True,
            "scratch_directory_removed": True,
            "production_source_files_opened": False,
            "production_source_files_modified": False,
            "production_quarantine_performed": False,
            "database_source_rows_modified": False,
            "deletion_performed": False,
            "execution_enabled": False,
            "execution_ready": False,
        },
    }


def _audit_row(run) -> dict:
    return {
        "id": run.id,
        "request_id": run.request_id,
        "dry_run_plan_id": run.dry_run_plan_id,
        "backup_verification_run_id": run.backup_verification_run_id,
        "quarantine_planning_run_id": run.quarantine_planning_run_id,
        "combined_rehearsal_run_id": run.combined_rehearsal_run_id,
        "contract_version": run.contract_version,
        "plan_fingerprint_sha256": run.plan_fingerprint_sha256,
        "backup_verification_result_fingerprint_sha256": (
            run.backup_verification_result_fingerprint_sha256
        ),
        "quarantine_planning_result_fingerprint_sha256": (
            run.quarantine_planning_result_fingerprint_sha256
        ),
        "destination_contract_fingerprint_sha256": (
            run.destination_contract_fingerprint_sha256
        ),
        "combined_rehearsal_result_fingerprint_sha256": (
            run.combined_rehearsal_result_fingerprint_sha256
        ),
        "scenario_contract_fingerprint_sha256": (
            run.scenario_contract_fingerprint_sha256
        ),
        "result_fingerprint_sha256": run.result_fingerprint_sha256,
        "result_status": run.result_status,
        "result_json": run.result_json,
        **run.result_json["metrics"],
        "scratch_resources_removed": run.scratch_resources_removed,
        "check_count": run.check_count,
        "passed_check_count": run.passed_check_count,
        "blocker_count": run.blocker_count,
        "run_by": run.run_by,
        "rehearsal_note": run.rehearsal_note,
        "run_at_kst": run.run_at_kst,
    }


def _canonical_fingerprint(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


if __name__ == "__main__":
    unittest.main()
