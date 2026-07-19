from __future__ import annotations

from datetime import datetime, timedelta
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from pubg_ai.data_deletion_requests import DataDeletionRequest, DataDeletionRequestEvent
from pubg_ai.web.app import create_app


class WebDataDeletionTests(unittest.TestCase):
    def test_list_detail_and_local_approval_never_report_execution_enabled(self) -> None:
        pending = _request(status="pending")
        approved = _request(status="approved")
        event = DataDeletionRequestEvent(
            id=1,
            request_id=17,
            event_type="requested",
            actor_type="discord",
            actor_id="100",
            note="검토 요청",
            details_json={"status": "pending"},
            created_at_kst=datetime(2026, 7, 11, 20, 0, 0),
        )
        service = MagicMock()
        service.list_requests.return_value = [pending]
        service.get_request.return_value = pending
        service.list_events.return_value = [event]
        service.approve_request.return_value = approved
        preview = MagicMock()
        preview.to_record.return_value = {
            "request_id": 17,
            "deletion_scope": "raw",
            "verification": {
                "read_only": True,
                "execution_enabled": False,
                "ready_for_execution": False,
            },
        }
        preview_service = MagicMock()
        preview_service.build_preview.return_value = preview
        fingerprint = "a" * 64
        snapshot = MagicMock()
        snapshot.to_summary_record.return_value = {
            "id": 501,
            "request_id": 17,
            "fingerprint_sha256": fingerprint,
            "catalog_complete": True,
            "filesystem_issue_count": 0,
        }
        confirmation = MagicMock()
        confirmation.to_record.return_value = {
            "id": 701,
            "request_id": 17,
            "preview_snapshot_id": 501,
            "fingerprint_sha256": fingerprint,
            "confirmed_by": "local-owner",
        }
        confirmation_service = MagicMock()
        confirmation_service.confirmation_state.return_value = {
            "request_id": 17,
            "request_status": "pending",
            "snapshot_capture_enabled": True,
            "confirmation_allowed": False,
            "confirmation_blockers": ["request status must be approved, not pending"],
            "expected_confirmation_text": None,
            "latest_snapshot": None,
            "snapshots": [],
            "confirmations": [],
            "execution_enabled": False,
        }
        confirmation_service.capture_snapshot.return_value = snapshot
        confirmation_service.confirm_snapshot.return_value = confirmation
        dry_run_plan = MagicMock()
        dry_run_plan.to_record.return_value = {
            "id": 901,
            "request_id": 17,
            "preview_snapshot_id": 501,
            "confirmation_id": 701,
            "plan_fingerprint_sha256": "b" * 64,
            "execution_enabled": False,
            "execution_ready": False,
        }
        dry_run_service = MagicMock()
        dry_run_service.plan_state.return_value = {
            "request_id": 17,
            "request_status": "pending",
            "generation_allowed": False,
            "generation_blockers": ["request status must be approved, not pending"],
            "latest_plan": None,
            "plans": [],
            "execution_blockers": [
                "executor_not_implemented",
                "backup_evidence_not_recorded",
            ],
            "execution_enabled": False,
            "execution_ready": False,
        }
        dry_run_service.create_plan.return_value = dry_run_plan
        backup_evidence = MagicMock()
        backup_evidence.to_record.return_value = {
            "id": 801,
            "request_id": 17,
            "dry_run_plan_id": 901,
            "prerequisite_key": "mysql_target_backup",
            "execution_enabled": False,
        }
        rehearsal = MagicMock()
        rehearsal.to_record.return_value = {
            "id": 1001,
            "request_id": 17,
            "dry_run_plan_id": 901,
            "result_status": "blocked",
            "execution_enabled": False,
            "execution_ready": False,
        }
        backup_service = MagicMock()
        backup_service.readiness_state.return_value = {
            "request_id": 17,
            "request_status": "pending",
            "latest_plan": None,
            "evidence_recording_allowed": False,
            "rehearsal_allowed": False,
            "prerequisites": [],
            "evidence_history": [],
            "latest_rehearsal": None,
            "rehearsals": [],
            "execution_blockers": [
                "executor_not_implemented",
                "backup_evidence_not_recorded",
                "rehearsal_not_passed",
            ],
            "execution_enabled": False,
            "execution_ready": False,
        }
        backup_service.record_evidence.return_value = backup_evidence
        backup_service.run_rehearsal.return_value = rehearsal
        backup_build = MagicMock()
        backup_build.to_record.return_value = {
            "request_id": 17,
            "dry_run_plan_id": 901,
            "build_id": "build-1",
            "artifacts": [],
            "restore_test_performed": False,
            "quarantine_performed": False,
            "deletion_performed": False,
            "execution_enabled": False,
            "execution_ready": False,
        }
        builder_service = MagicMock()
        builder_service.build_state.return_value = {
            "request_id": 17,
            "request_status": "pending",
            "latest_plan_id": 901,
            "backup_root": "D:/BackUP/deletion-backups",
            "confirmation_text": "BUILD BACKUP ARTIFACTS REQUEST 17 " + "b" * 64,
            "build_allowed": False,
            "build_blockers": ["request status must be approved, not pending"],
            "execution_enabled": False,
            "execution_ready": False,
        }
        builder_service.build.return_value = backup_build
        backup_verification = MagicMock()
        backup_verification.to_record.return_value = {
            "id": 1201,
            "request_id": 17,
            "dry_run_plan_id": 901,
            "result_status": "passed",
            "artifact_count": 2,
            "verified_artifact_count": 2,
            "restore_test_performed": False,
            "quarantine_performed": False,
            "deletion_performed": False,
            "execution_enabled": False,
            "execution_ready": False,
        }
        verifier_service = MagicMock()
        verifier_service.verification_state.return_value = {
            "request_id": 17,
            "request_status": "pending",
            "latest_plan_id": 901,
            "backup_root": "D:/BackUP/deletion-backups",
            "candidates": [],
            "selectable_candidate_count": 0,
            "latest_verification": None,
            "verification_history": [],
            "verification_allowed": False,
            "verification_blockers": ["request status must be approved, not pending"],
            "restore_test_performed": False,
            "execution_enabled": False,
            "execution_ready": False,
        }
        verifier_service.verify.return_value = backup_verification
        restore_rehearsal = MagicMock()
        restore_rehearsal.to_record.return_value = {
            "id": 1301,
            "request_id": 17,
            "dry_run_plan_id": 901,
            "backup_verification_run_id": 1201,
            "result_status": "passed",
            "mysql_row_count": 10,
            "mysql_restored_row_count": 10,
            "replay_file_count": 2,
            "replay_restored_file_count": 2,
            "backup_integrity_evidence_id": 1401,
            "production_restore_performed": False,
            "quarantine_performed": False,
            "deletion_performed": False,
            "execution_enabled": False,
            "execution_ready": False,
        }
        restore_service = MagicMock()
        restore_service.rehearsal_state.return_value = {
            "request_id": 17,
            "request_status": "pending",
            "latest_plan_id": 901,
            "verification_candidates": [],
            "latest_restore_rehearsal": None,
            "restore_rehearsal_history": [],
            "restore_rehearsal_allowed": False,
            "restore_rehearsal_blockers": [
                "request status must be approved, not pending"
            ],
            "production_restore_performed": False,
            "quarantine_performed": False,
            "deletion_performed": False,
            "execution_enabled": False,
            "execution_ready": False,
        }
        restore_service.run.return_value = restore_rehearsal
        quarantine_confirmation = (
            "RUN READ-ONLY QUARANTINE PLAN REQUEST 17 PLAN 901 "
            + "b" * 64
            + " DESTINATION "
            + "f" * 64
        )
        quarantine_planning = MagicMock()
        quarantine_planning.to_record.return_value = {
            "id": 1501,
            "request_id": 17,
            "dry_run_plan_id": 901,
            "result_status": "passed",
            "capacity_evidence_id": 1601,
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
        quarantine_planner_service = MagicMock()
        quarantine_planner_service.planning_state.return_value = {
            "request_id": 17,
            "request_status": "pending",
            "latest_plan_id": 901,
            "quarantine_root": "E:/PUBG_Quarantine",
            "confirmation_text": quarantine_confirmation,
            "planning_allowed": False,
            "planning_blockers": ["request status must be approved, not pending"],
            "latest_planning_run": None,
            "planning_history": [],
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
        quarantine_planner_service.run.return_value = quarantine_planning
        quarantine_rehearsal_confirmation = (
            "RUN ISOLATED QUARANTINE REHEARSAL REQUEST 17 PLAN 901 "
            "PLANNING RUN 1501 RESULT "
            + "g" * 64
            + " DESTINATION "
            + "f" * 64
        )
        quarantine_rehearsal = MagicMock()
        quarantine_rehearsal.to_record.return_value = {
            "id": 1701,
            "request_id": 17,
            "dry_run_plan_id": 901,
            "quarantine_planning_run_id": 1501,
            "result_status": "passed",
            "scratch_directory_removed": True,
            "fixture_file_count": 2,
            "normal_rolled_back_count": 2,
            "recovery_case_count": 5,
            "recovered_case_count": 4,
            "ambiguous_case_blocked_count": 1,
            "production_source_files_opened": False,
            "production_quarantine_performed": False,
            "deletion_performed": False,
            "execution_enabled": False,
            "execution_ready": False,
        }
        quarantine_rehearsal_service = MagicMock()
        quarantine_rehearsal_service.rehearsal_state.return_value = {
            "request_id": 17,
            "request_status": "pending",
            "latest_plan_id": 901,
            "quarantine_root": "E:/PUBG_Quarantine",
            "planning_candidate": None,
            "latest_quarantine_rehearsal": None,
            "quarantine_rehearsal_history": [],
            "rehearsal_allowed": False,
            "rehearsal_blockers": [
                "request status must be approved, not pending"
            ],
            "synthetic_fixtures_only": True,
            "production_source_files_opened": False,
            "production_quarantine_performed": False,
            "deletion_performed": False,
            "execution_enabled": False,
            "execution_ready": False,
        }
        quarantine_rehearsal_service.run.return_value = quarantine_rehearsal
        connections: list[FakeConnection] = []

        def connection_factory(*_: object, **__: object) -> FakeConnection:
            connection = FakeConnection()
            connections.append(connection)
            return connection

        with (
            patch("pubg_ai.web.app.connect_mysql", side_effect=connection_factory),
            patch("pubg_ai.web.app.DataDeletionRequestService", return_value=service),
            patch("pubg_ai.web.app.DataDeletionImpactPreviewService", return_value=preview_service),
            patch("pubg_ai.web.app.DataDeletionConfirmationService", return_value=confirmation_service),
            patch("pubg_ai.web.app.DataDeletionDryRunService", return_value=dry_run_service),
            patch("pubg_ai.web.app.DataDeletionBackupService", return_value=backup_service),
            patch(
                "pubg_ai.web.app.DataDeletionBackupBuilderService",
                return_value=builder_service,
            ),
            patch(
                "pubg_ai.web.app.DataDeletionBackupVerifierService",
                return_value=verifier_service,
            ),
            patch(
                "pubg_ai.web.app.DataDeletionBackupRestoreRehearsalService",
                return_value=restore_service,
            ),
            patch(
                "pubg_ai.web.app.DataDeletionQuarantinePlannerService",
                return_value=quarantine_planner_service,
            ),
            patch(
                "pubg_ai.web.app.DataDeletionQuarantineRehearsalService",
                return_value=quarantine_rehearsal_service,
            ),
        ):
            client = TestClient(create_app())
            list_response = client.get("/data-deletions?status=pending&limit=50")
            detail_response = client.get("/data-deletions/17")
            preview_response = client.get("/data-deletions/17/preview?file_limit=25")
            invalid_preview_response = client.get("/data-deletions/17/preview?file_limit=0")
            confirmation_state_response = client.get("/data-deletions/17/confirmation-state")
            snapshot_response = client.post(
                "/data-deletions/17/preview-snapshots",
                json={"actor_id": "local-owner", "note": "impact captured"},
            )
            expected_confirmation_text = f"CONFIRM DELETE REQUEST 17 {fingerprint}"
            confirmation_response = client.post(
                "/data-deletions/17/confirmations",
                json={
                    "snapshot_id": 501,
                    "fingerprint_sha256": fingerprint,
                    "confirmation_text": expected_confirmation_text,
                    "actor_id": "local-owner",
                    "note": "typed full fingerprint",
                },
            )
            invalid_confirmation_response = client.post(
                "/data-deletions/17/confirmations",
                json={
                    "snapshot_id": 501,
                    "fingerprint_sha256": "not-a-fingerprint",
                    "confirmation_text": "invalid",
                    "actor_id": "local-owner",
                },
            )
            dry_run_state_response = client.get("/data-deletions/17/dry-run-state")
            dry_run_plan_response = client.post(
                "/data-deletions/17/dry-run-plans",
                json={"actor_id": "local-owner", "note": "plan reviewed"},
            )
            backup_state_response = client.get(
                "/data-deletions/17/backup-readiness-state"
            )
            backup_build_confirmation = "BUILD BACKUP ARTIFACTS REQUEST 17 " + "b" * 64
            backup_build_response = client.post(
                "/data-deletions/17/backup-builds",
                json={
                    "dry_run_plan_id": 901,
                    "confirmation_text": backup_build_confirmation,
                    "actor_id": "local-owner",
                    "note": "build local artifacts",
                },
            )
            backup_verification_response = client.post(
                "/data-deletions/17/backup-verifications",
                json={
                    "dry_run_plan_id": 901,
                    "manifest_path": "D:/BackUP/deletion-backups/build-manifest.json",
                    "expected_manifest_sha256": "d" * 64,
                    "actor_id": "local-owner",
                    "note": "read-only artifact verification",
                },
            )
            invalid_backup_verification_response = client.post(
                "/data-deletions/17/backup-verifications",
                json={
                    "dry_run_plan_id": 901,
                    "manifest_path": "D:/BackUP/deletion-backups/build-manifest.json",
                    "expected_manifest_sha256": "short",
                    "actor_id": "local-owner",
                },
            )
            restore_confirmation = (
                "RUN ISOLATED RESTORE REHEARSAL REQUEST 17 VERIFICATION 1201 "
                + "e" * 64
            )
            restore_response = client.post(
                "/data-deletions/17/backup-restore-rehearsals",
                json={
                    "backup_verification_run_id": 1201,
                    "confirmation_text": restore_confirmation,
                    "actor_id": "local-owner",
                    "note": "temporary table and file restore",
                },
            )
            invalid_restore_response = client.post(
                "/data-deletions/17/backup-restore-rehearsals",
                json={
                    "backup_verification_run_id": 0,
                    "confirmation_text": "invalid",
                    "actor_id": "local-owner",
                },
            )
            quarantine_planning_response = client.post(
                "/data-deletions/17/quarantine-plans",
                json={
                    "dry_run_plan_id": 901,
                    "confirmation_text": quarantine_confirmation,
                    "actor_id": "local-owner",
                    "note": "read-only source and destination checks",
                },
            )
            invalid_quarantine_planning_response = client.post(
                "/data-deletions/17/quarantine-plans",
                json={
                    "dry_run_plan_id": 0,
                    "confirmation_text": "invalid",
                    "actor_id": "local-owner",
                },
            )
            quarantine_rehearsal_response = client.post(
                "/data-deletions/17/quarantine-rehearsals",
                json={
                    "quarantine_planning_run_id": 1501,
                    "confirmation_text": quarantine_rehearsal_confirmation,
                    "actor_id": "local-owner",
                    "note": "synthetic rollback and recovery",
                },
            )
            invalid_quarantine_rehearsal_response = client.post(
                "/data-deletions/17/quarantine-rehearsals",
                json={
                    "quarantine_planning_run_id": 0,
                    "confirmation_text": "invalid",
                    "actor_id": "local-owner",
                },
            )
            backup_evidence_response = client.post(
                "/data-deletions/17/backup-evidence",
                json={
                    "dry_run_plan_id": 901,
                    "prerequisite_key": "mysql_target_backup",
                    "artifact_path": "D:/BackUP/audit/mysql-plan-901.sql.gz",
                    "artifact_sha256": "c" * 64,
                    "artifact_size_bytes": 100,
                    "covered_row_count": 10,
                    "backup_created_at_kst": "2026-07-12T12:05:00",
                    "actor_id": "local-owner",
                    "note": "backup metadata",
                },
            )
            rehearsal_response = client.post(
                "/data-deletions/17/rehearsals",
                json={
                    "dry_run_plan_id": 901,
                    "actor_id": "local-owner",
                    "note": "metadata-only rehearsal",
                },
            )
            approve_response = client.post(
                "/data-deletions/17/approve",
                json={"actor_id": "local-owner", "note": "대상 확인"},
            )
            execute_response = client.post(
                "/data-deletions/17/execute",
                json={"actor_id": "local-owner"},
            )

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["requests"][0]["status"], "pending")
        self.assertEqual(detail_response.status_code, 200)
        self.assertFalse(detail_response.json()["execution_enabled"])
        self.assertEqual(detail_response.json()["events"][0]["event_type"], "requested")
        self.assertEqual(detail_response.json()["preview_url"], "/data-deletions/17/preview")
        self.assertEqual(
            detail_response.json()["confirmation_state_url"],
            "/data-deletions/17/confirmation-state",
        )
        self.assertEqual(
            detail_response.json()["dry_run_state_url"],
            "/data-deletions/17/dry-run-state",
        )
        self.assertEqual(
            detail_response.json()["dry_run_plan_url"],
            "/data-deletions/17/dry-run-plans",
        )
        self.assertEqual(
            detail_response.json()["backup_readiness_state_url"],
            "/data-deletions/17/backup-readiness-state",
        )
        self.assertEqual(
            detail_response.json()["backup_build_url"],
            "/data-deletions/17/backup-builds",
        )
        self.assertEqual(
            detail_response.json()["backup_verification_url"],
            "/data-deletions/17/backup-verifications",
        )
        self.assertEqual(
            detail_response.json()["backup_restore_rehearsal_url"],
            "/data-deletions/17/backup-restore-rehearsals",
        )
        self.assertEqual(
            detail_response.json()["quarantine_planning_url"],
            "/data-deletions/17/quarantine-plans",
        )
        self.assertEqual(
            detail_response.json()["quarantine_rehearsal_url"],
            "/data-deletions/17/quarantine-rehearsals",
        )
        self.assertEqual(
            detail_response.json()["backup_evidence_url"],
            "/data-deletions/17/backup-evidence",
        )
        self.assertEqual(
            detail_response.json()["rehearsal_url"],
            "/data-deletions/17/rehearsals",
        )
        self.assertEqual(preview_response.status_code, 200)
        self.assertFalse(preview_response.json()["execution_enabled"])
        self.assertTrue(preview_response.json()["preview"]["verification"]["read_only"])
        self.assertFalse(preview_response.json()["preview"]["verification"]["ready_for_execution"])
        self.assertEqual(invalid_preview_response.status_code, 422)
        self.assertEqual(confirmation_state_response.status_code, 200)
        self.assertFalse(confirmation_state_response.json()["execution_enabled"])
        self.assertFalse(
            confirmation_state_response.json()["confirmation_state"]["confirmation_allowed"]
        )
        self.assertEqual(snapshot_response.status_code, 200)
        self.assertEqual(snapshot_response.json()["snapshot"]["id"], 501)
        self.assertNotIn("preview_json", snapshot_response.json()["snapshot"])
        self.assertFalse(snapshot_response.json()["execution_enabled"])
        self.assertEqual(confirmation_response.status_code, 200)
        self.assertEqual(confirmation_response.json()["confirmation"]["id"], 701)
        self.assertFalse(confirmation_response.json()["execution_enabled"])
        self.assertEqual(invalid_confirmation_response.status_code, 422)
        self.assertEqual(dry_run_state_response.status_code, 200)
        self.assertFalse(dry_run_state_response.json()["execution_enabled"])
        self.assertFalse(dry_run_state_response.json()["execution_ready"])
        self.assertIn(
            "executor_not_implemented",
            dry_run_state_response.json()["dry_run_state"]["execution_blockers"],
        )
        self.assertEqual(dry_run_plan_response.status_code, 200)
        self.assertEqual(dry_run_plan_response.json()["dry_run_plan"]["id"], 901)
        self.assertFalse(dry_run_plan_response.json()["execution_enabled"])
        self.assertFalse(dry_run_plan_response.json()["execution_ready"])
        self.assertEqual(backup_state_response.status_code, 200)
        self.assertFalse(backup_state_response.json()["execution_enabled"])
        self.assertFalse(backup_state_response.json()["execution_ready"])
        self.assertIn(
            "rehearsal_not_passed",
            backup_state_response.json()["backup_readiness_state"]["execution_blockers"],
        )
        self.assertFalse(backup_state_response.json()["backup_builder_state"]["build_allowed"])
        self.assertFalse(
            backup_state_response.json()["backup_verifier_state"]["verification_allowed"]
        )
        self.assertFalse(
            backup_state_response.json()["backup_restore_rehearsal_state"]
            ["restore_rehearsal_allowed"]
        )
        self.assertFalse(
            backup_state_response.json()["quarantine_planner_state"]
            ["planning_allowed"]
        )
        self.assertFalse(
            backup_state_response.json()["quarantine_rehearsal_state"]
            ["rehearsal_allowed"]
        )
        self.assertTrue(
            backup_state_response.json()["quarantine_rehearsal_state"]
            ["synthetic_fixtures_only"]
        )
        self.assertEqual(backup_build_response.status_code, 200)
        self.assertEqual(backup_build_response.json()["backup_build"]["build_id"], "build-1")
        self.assertFalse(backup_build_response.json()["execution_enabled"])
        self.assertFalse(backup_build_response.json()["execution_ready"])
        self.assertEqual(backup_verification_response.status_code, 200)
        self.assertEqual(
            backup_verification_response.json()["backup_verification"]["id"],
            1201,
        )
        self.assertFalse(backup_verification_response.json()["execution_enabled"])
        self.assertFalse(backup_verification_response.json()["execution_ready"])
        self.assertEqual(invalid_backup_verification_response.status_code, 422)
        self.assertEqual(restore_response.status_code, 200)
        self.assertEqual(
            restore_response.json()["backup_restore_rehearsal"]["id"],
            1301,
        )
        self.assertEqual(
            restore_response.json()["backup_restore_rehearsal"]
            ["backup_integrity_evidence_id"],
            1401,
        )
        self.assertFalse(restore_response.json()["execution_enabled"])
        self.assertFalse(restore_response.json()["execution_ready"])
        self.assertEqual(invalid_restore_response.status_code, 422)
        self.assertEqual(quarantine_planning_response.status_code, 200)
        self.assertEqual(
            quarantine_planning_response.json()["quarantine_planning"]["id"],
            1501,
        )
        self.assertFalse(quarantine_planning_response.json()["execution_enabled"])
        self.assertFalse(quarantine_planning_response.json()["execution_ready"])
        self.assertEqual(invalid_quarantine_planning_response.status_code, 422)
        self.assertEqual(quarantine_rehearsal_response.status_code, 200)
        self.assertEqual(
            quarantine_rehearsal_response.json()["quarantine_rehearsal"]["id"],
            1701,
        )
        self.assertTrue(
            quarantine_rehearsal_response.json()["quarantine_rehearsal"]
            ["scratch_directory_removed"]
        )
        self.assertFalse(quarantine_rehearsal_response.json()["execution_enabled"])
        self.assertFalse(quarantine_rehearsal_response.json()["execution_ready"])
        self.assertEqual(invalid_quarantine_rehearsal_response.status_code, 422)
        self.assertEqual(backup_evidence_response.status_code, 200)
        self.assertEqual(backup_evidence_response.json()["backup_evidence"]["id"], 801)
        self.assertFalse(backup_evidence_response.json()["execution_enabled"])
        self.assertFalse(backup_evidence_response.json()["execution_ready"])
        self.assertEqual(rehearsal_response.status_code, 200)
        self.assertEqual(rehearsal_response.json()["rehearsal"]["id"], 1001)
        self.assertFalse(rehearsal_response.json()["execution_enabled"])
        self.assertFalse(rehearsal_response.json()["execution_ready"])
        self.assertEqual(approve_response.status_code, 200)
        self.assertEqual(approve_response.json()["request"]["status"], "approved")
        self.assertFalse(approve_response.json()["execution_enabled"])
        self.assertEqual(execute_response.status_code, 404)
        service.approve_request.assert_called_once_with(
            17,
            actor_id="local-owner",
            note="대상 확인",
        )
        preview_service.build_preview.assert_called_once_with(pending, file_limit=25)
        confirmation_service.confirmation_state.assert_called_once_with(pending)
        confirmation_service.capture_snapshot.assert_called_once_with(
            pending,
            actor_id="local-owner",
            note="impact captured",
        )
        confirmation_service.confirm_snapshot.assert_called_once_with(
            pending,
            snapshot_id=501,
            fingerprint_sha256=fingerprint,
            confirmation_text=expected_confirmation_text,
            actor_id="local-owner",
            note="typed full fingerprint",
        )
        dry_run_service.plan_state.assert_called_once_with(pending)
        dry_run_service.create_plan.assert_called_once_with(
            pending,
            actor_id="local-owner",
            note="plan reviewed",
        )
        backup_service.readiness_state.assert_called_once_with(pending)
        builder_service.build_state.assert_called_once_with(pending)
        builder_service.build.assert_called_once_with(
            pending,
            dry_run_plan_id=901,
            confirmation_text=backup_build_confirmation,
            actor_id="local-owner",
            note="build local artifacts",
        )
        verifier_service.verification_state.assert_called_once_with(pending)
        verifier_service.verify.assert_called_once_with(
            pending,
            dry_run_plan_id=901,
            manifest_path="D:/BackUP/deletion-backups/build-manifest.json",
            expected_manifest_sha256="d" * 64,
            actor_id="local-owner",
            note="read-only artifact verification",
        )
        restore_service.rehearsal_state.assert_called_once_with(pending)
        restore_service.run.assert_called_once_with(
            pending,
            backup_verification_run_id=1201,
            confirmation_text=restore_confirmation,
            actor_id="local-owner",
            note="temporary table and file restore",
        )
        quarantine_planner_service.planning_state.assert_called_once_with(pending)
        quarantine_planner_service.run.assert_called_once_with(
            pending,
            dry_run_plan_id=901,
            confirmation_text=quarantine_confirmation,
            actor_id="local-owner",
            note="read-only source and destination checks",
        )
        quarantine_rehearsal_service.rehearsal_state.assert_called_once_with(pending)
        quarantine_rehearsal_service.run.assert_called_once_with(
            pending,
            quarantine_planning_run_id=1501,
            confirmation_text=quarantine_rehearsal_confirmation,
            actor_id="local-owner",
            note="synthetic rollback and recovery",
        )
        backup_service.record_evidence.assert_called_once_with(
            pending,
            dry_run_plan_id=901,
            prerequisite_key="mysql_target_backup",
            evidence={
                "artifact_path": "D:/BackUP/audit/mysql-plan-901.sql.gz",
                "artifact_sha256": "c" * 64,
                "artifact_size_bytes": 100,
                "covered_row_count": 10,
                "covered_file_count": None,
                "covered_file_bytes": None,
                "checked_path": None,
                "available_bytes": None,
                "backup_created_at_kst": datetime(2026, 7, 12, 12, 5, 0),
                "verified_at_kst": None,
                "restore_tested_at_kst": None,
                "checksums_verified": False,
                "restore_test_passed": False,
            },
            actor_id="local-owner",
            note="backup metadata",
        )
        backup_service.run_rehearsal.assert_called_once_with(
            pending,
            dry_run_plan_id=901,
            actor_id="local-owner",
            note="metadata-only rehearsal",
        )
        self.assertTrue(all(connection.closed for connection in connections))


class FakeConnection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _request(*, status: str) -> DataDeletionRequest:
    requested_at = datetime(2026, 7, 11, 20, 0, 0)
    return DataDeletionRequest(
        id=17,
        registered_player_id=1,
        account_id="account.test",
        shard="steam",
        player_name="Yuuki_Asuna---",
        deletion_scope="raw",
        status=status,
        reason="검토 요청",
        requested_by_discord_user_id="100",
        requested_guild_id="10",
        requested_channel_id="20",
        requested_at_kst=requested_at,
        expires_at_kst=requested_at + timedelta(hours=24),
        reviewed_by="local:local-owner" if status == "approved" else None,
        reviewed_at_kst=requested_at if status == "approved" else None,
        review_note="대상 확인" if status == "approved" else None,
        updated_at_kst=requested_at,
    )


if __name__ == "__main__":
    unittest.main()
