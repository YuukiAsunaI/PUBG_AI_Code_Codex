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
