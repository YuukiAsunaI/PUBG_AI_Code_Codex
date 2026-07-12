from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock
import hashlib
import json
import unittest
import zipfile

from pubg_ai.data_deletion_backup import (
    BACKUP_EVIDENCE_CONTRACT_VERSION,
    DataDeletionBackupEvidence,
    fingerprint_backup_evidence,
    normalize_evidence_payload,
)
from pubg_ai.data_deletion_backup_builder import (
    BACKUP_BUILDER_CONTRACT_VERSION,
    DataDeletionBackupBuilderError,
    DataDeletionBackupBuilderService,
    database_backup_select,
    expected_backup_build_confirmation,
    overlapping_source_root,
)
from pubg_ai.data_deletion_confirmation import fingerprint_preview_record
from pubg_ai.data_deletion_dry_run import (
    DRY_RUN_CONTRACT_VERSION,
    DataDeletionDryRunPlan,
    fingerprint_dry_run_plan,
)
from pubg_ai.data_deletion_requests import DataDeletionRequest


class DataDeletionBackupBuilderTests(unittest.TestCase):
    def test_builder_creates_verified_artifacts_and_records_only_artifact_evidence(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            backup_root = base / "backups"
            raw_root = base / "raw"
            replay_root = base / "replays"
            backup_root.mkdir()
            raw_root.mkdir()
            replay_root.mkdir()
            replay_relative = "timeline/steam/2026/07/12/match-1/timeline.json"
            replay_path = replay_root / replay_relative
            replay_path.parent.mkdir(parents=True)
            replay_body = b'{"timeline":true}'
            replay_path.write_bytes(replay_body)

            preview = _preview()
            source_fingerprint, _ = fingerprint_preview_record(preview)
            plan = _plan(
                source_fingerprint,
                replay_relative,
                replay_body,
            )
            request = _request()
            connection = ReadOnlyConnection()
            backup_service = _backup_service(plan, preview)
            backup_service.record_evidence_batch.side_effect = _evidence_batch(plan)
            rows = {
                "registered_players": [
                    {
                        "id": 1,
                        "account_id": request.account_id,
                        "shard": request.shard,
                        "created_at_kst": datetime(2026, 7, 1, 12, 0, 0),
                    }
                ],
                "player_collection_states": [
                    {"registered_player_id": 1, "last_error": None}
                ],
            }
            service = DataDeletionBackupBuilderService(
                connection,
                backup_service=backup_service,
                backup_root=backup_root,
                raw_data_dir=raw_root,
                replay_data_dir=replay_root,
                row_provider=lambda operation, _request: rows[str(operation["table"])],
            )
            confirmation = expected_backup_build_confirmation(
                request.id,
                plan.plan_fingerprint_sha256,
            )

            result = service.build(
                request,
                dry_run_plan_id=plan.id,
                confirmation_text=confirmation,
                actor_id="local-owner",
                note="operator requested local backup",
                reference_kst=datetime(2026, 7, 12, 12, 5, 0),
            )

            self.assertEqual(result.contract_version, BACKUP_BUILDER_CONTRACT_VERSION)
            self.assertTrue(result.build_directory.is_dir())
            self.assertTrue(result.manifest_path.is_file())
            self.assertEqual(len(result.artifacts), 2)
            self.assertEqual(connection.begin_count, 1)
            self.assertEqual(connection.rollback_count, 1)
            self.assertEqual(backup_service.preview_service.build_preview.call_count, 2)
            evidence_call = backup_service.record_evidence_batch.call_args.kwargs
            self.assertEqual(
                set(evidence_call["evidence_by_key"]),
                {"mysql_target_backup", "replay_artifact_backup"},
            )
            self.assertNotIn("backup_integrity_verification", evidence_call["evidence_by_key"])
            self.assertNotIn("quarantine_capacity_check", evidence_call["evidence_by_key"])
            self.assertIn("opt_in_sha256=", evidence_call["note"])

            mysql_artifact = next(
                artifact
                for artifact in result.artifacts
                if artifact.prerequisite_key == "mysql_target_backup"
            )
            with zipfile.ZipFile(mysql_artifact.path) as archive:
                mysql_manifest = json.loads(archive.read("manifest.json"))
                self.assertEqual(mysql_manifest["row_count"], 2)
                self.assertFalse(mysql_manifest["restore_supported_by_current_application"])
                entries = [name for name in archive.namelist() if name.startswith("tables/")]
                self.assertEqual(len(entries), 2)
                exported = b"".join(archive.read(name) for name in entries)
                self.assertIn(b'"account_id":"account.test"', exported)

            replay_artifact = next(
                artifact
                for artifact in result.artifacts
                if artifact.prerequisite_key == "replay_artifact_backup"
            )
            with zipfile.ZipFile(replay_artifact.path) as archive:
                replay_manifest = json.loads(archive.read("manifest.json"))
                self.assertEqual(replay_manifest["file_count"], 1)
                self.assertEqual(
                    archive.read(f"files/{replay_relative}"),
                    replay_body,
                )

            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            self.assertFalse(manifest["safety"]["restore_test_performed"])
            self.assertFalse(manifest["safety"]["quarantine_performed"])
            self.assertFalse(manifest["safety"]["deletion_performed"])
            self.assertFalse(result.to_record()["execution_enabled"])
            self.assertEqual(replay_path.read_bytes(), replay_body)

    def test_wrong_confirmation_creates_no_backup_directory(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            backup_root = base / "backups"
            raw_root = base / "raw"
            replay_root = base / "replays"
            for path in (backup_root, raw_root, replay_root):
                path.mkdir()
            preview = _preview()
            source_fingerprint, _ = fingerprint_preview_record(preview)
            plan = _plan(source_fingerprint, "timeline/file.json", b"x")
            service = DataDeletionBackupBuilderService(
                ReadOnlyConnection(),
                backup_service=_backup_service(plan, preview),
                backup_root=backup_root,
                raw_data_dir=raw_root,
                replay_data_dir=replay_root,
                row_provider=lambda *_: [],
            )

            with self.assertRaises(DataDeletionBackupBuilderError):
                service.build(
                    _request(),
                    dry_run_plan_id=plan.id,
                    confirmation_text="BUILD SOMETHING ELSE",
                    actor_id="local-owner",
                )

            self.assertEqual(list(backup_root.iterdir()), [])

    def test_backup_root_must_not_overlap_source_roots(self) -> None:
        base = Path("C:/PUBG")
        self.assertEqual(
            overlapping_source_root(base, base / "raw", Path("D:/replays")),
            "PUBG_RAW_DATA_DIR",
        )
        self.assertEqual(
            overlapping_source_root(base / "replays" / "backup", base / "raw", base / "replays"),
            "PUBG_REPLAY_DATA_DIR",
        )
        self.assertIsNone(
            overlapping_source_root(base / "backup", base / "raw", base / "replays")
        )

    def test_database_select_uses_only_whitelisted_tables_and_select_statements(self) -> None:
        request = _request()
        operation = {
            "action": "delete_rows_planned",
            "table": "registered_players",
            "selector": {
                "kind": "target_identity",
                "account_id": request.account_id,
                "shard": request.shard,
            },
            "mutation_enabled": False,
        }

        statement, parameters = database_backup_select(operation, request)

        self.assertTrue(statement.lstrip().upper().startswith("SELECT"))
        self.assertNotIn("DELETE", statement.upper())
        self.assertEqual(parameters, (request.account_id, request.shard))
        operation["table"] = "data_deletion_requests"
        with self.assertRaises(DataDeletionBackupBuilderError):
            database_backup_select(operation, request)


class ReadOnlyConnection:
    def __init__(self) -> None:
        self.begin_count = 0
        self.rollback_count = 0

    def begin(self) -> None:
        self.begin_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1


def _request() -> DataDeletionRequest:
    requested_at = datetime(2026, 7, 12, 10, 0, 0)
    return DataDeletionRequest(
        id=17,
        registered_player_id=1,
        account_id="account.test",
        shard="steam",
        player_name="Yuuki_Asuna---",
        deletion_scope="all",
        status="approved",
        reason="backup builder test",
        requested_by_discord_user_id="100",
        requested_guild_id="10",
        requested_channel_id="20",
        requested_at_kst=requested_at,
        expires_at_kst=requested_at + timedelta(hours=24),
        reviewed_by="local:local-owner",
        reviewed_at_kst=requested_at,
        review_note="approved",
        updated_at_kst=requested_at,
    )


def _preview() -> dict[str, object]:
    return {
        "request_id": 17,
        "target": {
            "account_id": "account.test",
            "shard": "steam",
            "player_name": "Yuuki_Asuna---",
        },
        "deletion_scope": "all",
        "included_sections": {"registration": True, "normalized": True, "raw": True, "replay": True},
        "matched_match_count": 1,
        "candidate_row_count": 2,
        "preserved_reference_row_count": 0,
        "row_impacts": [],
        "preserved_references": [],
        "raw_files": None,
        "replay_files": None,
        "verification": {"catalog_complete": True, "filesystem_issue_count": 0},
    }


def _plan(
    source_fingerprint: str,
    replay_relative_path: str,
    replay_body: bytes,
) -> DataDeletionDryRunPlan:
    request = _request()
    database_operations = [
        {
            "sequence": 1,
            "action": "delete_rows_planned",
            "table": "registered_players",
            "selector": {
                "kind": "target_identity",
                "account_id": request.account_id,
                "shard": request.shard,
            },
            "estimated_rows": 1,
            "mutation_enabled": False,
        },
        {
            "sequence": 2,
            "action": "delete_rows_planned",
            "table": "player_collection_states",
            "selector": {
                "kind": "registered_player_join",
                "account_id": request.account_id,
                "shard": request.shard,
            },
            "estimated_rows": 1,
            "mutation_enabled": False,
        },
    ]
    file_operations = [
        {
            "sequence": 1,
            "action": "quarantine_file_planned",
            "source_table": "replay_artifacts",
            "record_id": 10,
            "artifact_type": "timeline",
            "match_id": "match-1",
            "storage_root": "PUBG_REPLAY_DATA_DIR",
            "relative_path": replay_relative_path,
            "declared_size_bytes": len(replay_body),
            "sha256": hashlib.sha256(replay_body).hexdigest(),
            "verification_status": "verified",
            "ownership": "player_artifact",
            "mutation_enabled": False,
        }
    ]
    plan_json = {
        "contract_version": DRY_RUN_CONTRACT_VERSION,
        "request_id": request.id,
        "source_fingerprint_sha256": source_fingerprint,
        "metrics": {
            "candidate_row_count": 2,
            "candidate_file_count": 1,
            "candidate_file_bytes": len(replay_body),
        },
        "backup_prerequisites": [
            {"key": "mysql_target_backup", "required": True},
            {"key": "replay_artifact_backup", "required": True},
            {"key": "quarantine_capacity_check", "required": True},
            {"key": "backup_integrity_verification", "required": True},
        ],
        "database_operations": database_operations,
        "file_operations": file_operations,
    }
    fingerprint = fingerprint_dry_run_plan(plan_json)
    return DataDeletionDryRunPlan(
        id=901,
        request_id=request.id,
        preview_snapshot_id=501,
        confirmation_id=701,
        contract_version=DRY_RUN_CONTRACT_VERSION,
        source_fingerprint_sha256=source_fingerprint,
        plan_fingerprint_sha256=fingerprint,
        plan_json=plan_json,
        operation_count=3,
        candidate_row_count=2,
        candidate_file_count=1,
        candidate_file_bytes=len(replay_body),
        excluded_row_count=0,
        excluded_file_count=0,
        generated_by="local-owner",
        generation_note=None,
        generated_at_kst=datetime(2026, 7, 12, 12, 0, 0),
    )


def _backup_service(plan: DataDeletionDryRunPlan, preview: dict[str, object]) -> MagicMock:
    service = MagicMock()
    service.require_latest_plan.return_value = plan
    service.dry_run_service.list_plans.return_value = [plan]
    preview_result = MagicMock()
    preview_result.to_record.return_value = preview
    service.preview_service.build_preview.return_value = preview_result
    return service


def _evidence_batch(plan: DataDeletionDryRunPlan):
    def record(
        request: DataDeletionRequest,
        *,
        evidence_by_key: dict[str, dict[str, object]],
        actor_id: str,
        note: str | None,
        **_: object,
    ) -> dict[str, DataDeletionBackupEvidence]:
        records: dict[str, DataDeletionBackupEvidence] = {}
        for index, (key, payload) in enumerate(evidence_by_key.items(), start=1):
            normalized = normalize_evidence_payload(key, payload)
            records[key] = DataDeletionBackupEvidence(
                id=800 + index,
                request_id=request.id,
                dry_run_plan_id=plan.id,
                contract_version=BACKUP_EVIDENCE_CONTRACT_VERSION,
                plan_fingerprint_sha256=plan.plan_fingerprint_sha256,
                prerequisite_key=key,
                evidence_fingerprint_sha256=fingerprint_backup_evidence(
                    request.id,
                    plan,
                    key,
                    normalized,
                ),
                evidence_json=normalized,
                recorded_by=actor_id,
                evidence_note=note,
                recorded_at_kst=datetime(2026, 7, 12, 12, 5, 0),
            )
        return records

    return record


if __name__ == "__main__":
    unittest.main()
