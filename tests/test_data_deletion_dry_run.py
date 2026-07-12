from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import json
import unittest
from unittest.mock import MagicMock

from pubg_ai.data_deletion_confirmation import (
    CONFIRMATION_CONTRACT_VERSION,
    DataDeletionConfirmation,
    DataDeletionPreviewSnapshot,
    fingerprint_preview_record,
)
from pubg_ai.data_deletion_dry_run import (
    DRY_RUN_CONTRACT_VERSION,
    DataDeletionDryRunError,
    DataDeletionDryRunService,
    build_dry_run_plan_manifest,
    dry_run_generation_blockers,
    fingerprint_dry_run_plan,
)
from pubg_ai.data_deletion_requests import DataDeletionRequest


class DataDeletionDryRunTests(unittest.TestCase):
    def test_manifest_is_deterministic_and_protects_shared_match_data(self) -> None:
        preview = _preview_record()
        snapshot = _snapshot(preview)
        confirmation = _confirmation(snapshot)

        first = build_dry_run_plan_manifest(_request(), snapshot, confirmation)
        reordered = deepcopy(preview)
        reordered["row_impacts"].reverse()
        reordered["preserved_references"].reverse()
        reordered["replay_files"]["files"].reverse()
        second_snapshot = _snapshot(reordered)
        second = build_dry_run_plan_manifest(_request(), second_snapshot, _confirmation(second_snapshot))

        self.assertEqual(first, second)
        self.assertEqual(fingerprint_dry_run_plan(first), fingerprint_dry_run_plan(second))
        self.assertFalse(first["safety"]["execution_enabled"])
        self.assertFalse(first["safety"]["execution_ready"])
        self.assertIn("executor_not_implemented", first["safety"]["execution_blockers"])
        self.assertIn("backup_evidence_not_recorded", first["safety"]["execution_blockers"])
        self.assertEqual(
            {operation["source_table"] for operation in first["file_operations"]},
            {"replay_artifacts"},
        )
        self.assertEqual(first["metrics"]["candidate_file_count"], 2)
        self.assertEqual(first["metrics"]["excluded_file_count"], 2)
        self.assertTrue(
            any(
                exclusion["table"] == "matches"
                for exclusion in first["row_exclusions"]
            )
        )
        self.assertTrue(
            any(
                exclusion["table"] == "raw_match_payloads"
                for exclusion in first["row_exclusions"]
            )
        )
        audit_exclusions = {
            exclusion["table"] for exclusion in first["audit_table_exclusions"]
        }
        self.assertIn("data_deletion_dry_run_plans", audit_exclusions)
        self.assertIn("data_deletion_backup_evidence", audit_exclusions)
        self.assertIn("data_deletion_rehearsal_runs", audit_exclusions)
        serialized = json.dumps(first, ensure_ascii=False)
        self.assertNotIn("DELETE FROM", serialized.upper())
        self.assertTrue(
            all(not operation["mutation_enabled"] for operation in first["database_operations"])
        )
        self.assertTrue(
            all(not operation["mutation_enabled"] for operation in first["file_operations"])
        )

    def test_database_and_file_operations_follow_safety_order(self) -> None:
        preview = _preview_record()
        snapshot = _snapshot(preview)
        plan = build_dry_run_plan_manifest(_request(), snapshot, _confirmation(snapshot))

        tables = [operation["table"] for operation in plan["database_operations"]]
        self.assertLess(tables.index("player_item_events"), tables.index("match_participants"))
        self.assertLess(tables.index("match_participants"), tables.index("replay_artifacts"))
        self.assertLess(tables.index("replay_artifacts"), tables.index("player_collection_states"))
        self.assertLess(tables.index("player_collection_states"), tables.index("registered_players"))
        phase_keys = [phase["key"] for phase in plan["phases"]]
        self.assertLess(
            phase_keys.index("quarantine_player_files"),
            phase_keys.index("remove_replay_metadata"),
        )

    def test_create_plan_revalidates_and_only_inserts_audit_plan(self) -> None:
        preview_record = _preview_record()
        snapshot = _snapshot(preview_record)
        confirmation = _confirmation(snapshot)
        plan_manifest = build_dry_run_plan_manifest(_request(), snapshot, confirmation)
        plan_fingerprint = fingerprint_dry_run_plan(plan_manifest)
        connection = ScriptedConnection(
            [
                {"contains": "SELECT status FROM data_deletion_requests", "row": {"status": "approved"}},
                {
                    "contains": "SELECT id, fingerprint_sha256 FROM data_deletion_preview_snapshots",
                    "row": {"id": snapshot.id, "fingerprint_sha256": snapshot.fingerprint_sha256},
                },
                {
                    "contains": "SELECT id, request_id, preview_snapshot_id, fingerprint_sha256",
                    "row": {
                        "id": confirmation.id,
                        "request_id": 17,
                        "preview_snapshot_id": snapshot.id,
                        "fingerprint_sha256": snapshot.fingerprint_sha256,
                    },
                },
                {"contains": "INSERT INTO data_deletion_dry_run_plans", "lastrowid": 901},
                {"contains": "WHERE id = %s", "row": _plan_row(plan_manifest, plan_fingerprint)},
            ]
        )
        preview = MagicMock()
        preview.to_record.return_value = preview_record
        preview_service = MagicMock()
        preview_service.build_preview.return_value = preview
        confirmation_service = MagicMock()
        confirmation_service.list_snapshots.return_value = [snapshot]
        confirmation_service.list_confirmations.return_value = [confirmation]
        service = DataDeletionDryRunService(
            connection,
            preview_service=preview_service,
            confirmation_service=confirmation_service,
        )

        plan = service.create_plan(
            _request(),
            actor_id="local-owner",
            note="reviewed dry-run",
            reference_kst=datetime(2026, 7, 12, 12, 10, 0),
        )

        self.assertEqual(plan.id, 901)
        self.assertEqual(plan.plan_fingerprint_sha256, plan_fingerprint)
        self.assertEqual(connection.begin_count, 1)
        self.assertEqual(connection.commit_count, 1)
        self.assertEqual(connection.rollback_count, 0)
        preview_service.build_preview.assert_called_once_with(
            unittest.mock.ANY,
            file_limit=500,
        )
        mutation_queries = [
            query
            for query, _ in connection.executed
            if query.lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE ", "REPLACE "))
        ]
        self.assertEqual(len(mutation_queries), 1)
        self.assertIn("INSERT INTO data_deletion_dry_run_plans", mutation_queries[0])
        insert_params = next(
            params
            for query, params in connection.executed
            if "INSERT INTO data_deletion_dry_run_plans" in query
        )
        self.assertEqual(insert_params[0:6], (
            17,
            501,
            701,
            DRY_RUN_CONTRACT_VERSION,
            snapshot.fingerprint_sha256,
            plan_fingerprint,
        ))

    def test_stale_live_fingerprint_blocks_before_transaction(self) -> None:
        preview_record = _preview_record()
        snapshot = _snapshot(preview_record)
        confirmation = _confirmation(snapshot)
        stale = deepcopy(preview_record)
        stale["candidate_row_count"] = 999
        preview = MagicMock()
        preview.to_record.return_value = stale
        preview_service = MagicMock()
        preview_service.build_preview.return_value = preview
        confirmation_service = MagicMock()
        confirmation_service.list_snapshots.return_value = [snapshot]
        confirmation_service.list_confirmations.return_value = [confirmation]
        connection = ScriptedConnection([])
        service = DataDeletionDryRunService(
            connection,
            preview_service=preview_service,
            confirmation_service=confirmation_service,
        )

        with self.assertRaisesRegex(DataDeletionDryRunError, "current deletion impact differs"):
            service.create_plan(_request(), actor_id="local-owner")

        self.assertEqual(connection.begin_count, 0)
        self.assertEqual(connection.executed, [])

    def test_concurrent_request_status_change_rolls_back_without_insert(self) -> None:
        preview_record = _preview_record()
        snapshot = _snapshot(preview_record)
        confirmation = _confirmation(snapshot)
        preview = MagicMock()
        preview.to_record.return_value = preview_record
        preview_service = MagicMock()
        preview_service.build_preview.return_value = preview
        confirmation_service = MagicMock()
        confirmation_service.list_snapshots.return_value = [snapshot]
        confirmation_service.list_confirmations.return_value = [confirmation]
        connection = ScriptedConnection(
            [
                {"contains": "SELECT status FROM data_deletion_requests", "row": {"status": "cancelled"}},
            ]
        )
        service = DataDeletionDryRunService(
            connection,
            preview_service=preview_service,
            confirmation_service=confirmation_service,
        )

        with self.assertRaisesRegex(DataDeletionDryRunError, "changed to cancelled"):
            service.create_plan(_request(), actor_id="local-owner")

        self.assertEqual(connection.begin_count, 1)
        self.assertEqual(connection.commit_count, 0)
        self.assertEqual(connection.rollback_count, 1)
        self.assertFalse(
            any("INSERT INTO data_deletion_dry_run_plans" in query for query, _ in connection.executed)
        )

    def test_generation_blockers_require_latest_matching_confirmation(self) -> None:
        snapshot = _snapshot(_preview_record())
        wrong_confirmation = DataDeletionConfirmation(
            **{**_confirmation(snapshot).__dict__, "preview_snapshot_id": 999}
        )

        blockers = dry_run_generation_blockers(
            _request(status="pending"),
            snapshot,
            wrong_confirmation,
        )

        self.assertTrue(any("approved" in blocker for blocker in blockers))
        self.assertTrue(any("latest snapshot" in blocker for blocker in blockers))


class ScriptedConnection:
    def __init__(self, steps: list[dict[str, object]]) -> None:
        self.steps = list(steps)
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.begin_count = 0
        self.commit_count = 0
        self.rollback_count = 0

    def cursor(self) -> "ScriptedCursor":
        return ScriptedCursor(self)

    def begin(self) -> None:
        self.begin_count += 1

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1


class ScriptedCursor:
    def __init__(self, connection: ScriptedConnection) -> None:
        self.connection = connection
        self.step: dict[str, object] = {}
        self.lastrowid = 0

    def __enter__(self) -> "ScriptedCursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, query: str, params: tuple[object, ...] = ()) -> None:
        if not self.connection.steps:
            raise AssertionError(f"unexpected query: {query}")
        self.step = self.connection.steps.pop(0)
        expected = str(self.step.get("contains") or "")
        normalized = " ".join(query.split())
        if expected not in normalized:
            raise AssertionError(f"expected {expected!r} in {normalized!r}")
        self.lastrowid = int(self.step.get("lastrowid") or 0)
        self.connection.executed.append((normalized, tuple(params)))

    def fetchone(self) -> dict[str, object] | None:
        row = self.step.get("row")
        return dict(row) if isinstance(row, dict) else None

    def fetchall(self) -> list[dict[str, object]]:
        rows = self.step.get("rows") or []
        return [dict(row) for row in rows if isinstance(row, dict)]


def _preview_record() -> dict[str, object]:
    replay_files = [
        {
            "record_id": 21,
            "source_table": "replay_artifacts",
            "file_type": "timeline",
            "match_id": "match-2",
            "ownership": "player_artifact",
            "deletion_candidate": True,
            "storage_backend": "local_file",
            "storage_root": "PUBG_REPLAY_DATA_DIR",
            "relative_path": "timeline/steam/match-2.json",
            "declared_size_bytes": 20,
            "sha256": "2" * 64,
            "verification_status": "present",
        },
        {
            "record_id": 20,
            "source_table": "replay_artifacts",
            "file_type": "map_snapshot",
            "match_id": "match-1",
            "ownership": "player_artifact",
            "deletion_candidate": True,
            "storage_backend": "local_file",
            "storage_root": "PUBG_REPLAY_DATA_DIR",
            "relative_path": "map_snapshot/steam/match-1.jpeg",
            "declared_size_bytes": 10,
            "sha256": "1" * 64,
            "verification_status": "present",
        },
    ]
    row_impacts = [
        _row("registered_players", "registration", 1, True),
        _row("player_aliases", "registration", 1, True),
        _row("player_collection_states", "registration", 1, True),
        _row("player_item_events", "normalized", 2, True),
        _row("match_participants", "normalized", 1, True),
        _row("raw_player_snapshots", "raw_database", 1, True),
        _row("replay_artifacts", "replay_metadata", 2, True),
        _row("raw_match_payloads", "raw_metadata", 2, False, "shared_match"),
    ]
    return {
        "request_id": 17,
        "target": {
            "account_id": "account.test",
            "shard": "steam",
            "player_name": "Yuuki_Asuna---",
        },
        "deletion_scope": "all",
        "generated_at_kst": "2026-07-12T12:00:00+09:00",
        "file_limit_per_catalog": 500,
        "included_sections": {
            "registration": True,
            "normalized": True,
            "raw": True,
            "replay": True,
        },
        "matched_match_count": 2,
        "candidate_row_count": 9,
        "preserved_reference_row_count": 3,
        "row_impacts": row_impacts,
        "preserved_references": [
            _row("matches", "shared_match_context", 2, False, "shared_match"),
            _row(
                "player_combat_location_events.related_account_id",
                "normalized_reference",
                1,
                False,
                "referenced_by_other_player_rows",
            ),
        ],
        "raw_files": {
            "category": "raw",
            "included": True,
            "total_records": 2,
            "total_declared_size_bytes": 40,
            "deletion_candidate_records": 0,
            "shared_match_records": 2,
            "listed_records": 2,
            "truncated": False,
            "files": [],
        },
        "replay_files": {
            "category": "replay",
            "included": True,
            "total_records": 2,
            "total_declared_size_bytes": 30,
            "deletion_candidate_records": 2,
            "shared_match_records": 0,
            "listed_records": 2,
            "truncated": False,
            "files": replay_files,
        },
        "verification": {
            "read_only": True,
            "execution_enabled": False,
            "ready_for_execution": False,
            "catalog_complete": True,
            "listed_file_count": 2,
            "filesystem_issue_count": 0,
            "unsafe_path_count": 0,
            "missing_file_count": 0,
            "size_mismatch_count": 0,
            "checksum_verification_performed": False,
        },
        "warnings": ["Preview is read-only."],
    }


def _row(
    table: str,
    category: str,
    row_count: int,
    candidate: bool,
    relationship: str = "player_owned",
) -> dict[str, object]:
    return {
        "table": table,
        "category": category,
        "relationship": relationship,
        "row_count": row_count,
        "deletion_candidate": candidate,
    }


def _snapshot(preview: dict[str, object]) -> DataDeletionPreviewSnapshot:
    fingerprint, manifest = fingerprint_preview_record(preview)
    return DataDeletionPreviewSnapshot(
        id=501,
        request_id=17,
        contract_version=CONFIRMATION_CONTRACT_VERSION,
        fingerprint_sha256=fingerprint,
        preview_json=deepcopy(preview),
        manifest_json=manifest,
        catalog_complete=True,
        filesystem_issue_count=0,
        candidate_row_count=9,
        candidate_file_count=2,
        captured_by="local-owner",
        capture_note="impact reviewed",
        captured_at_kst=datetime(2026, 7, 12, 12, 0, 0),
    )


def _confirmation(snapshot: DataDeletionPreviewSnapshot) -> DataDeletionConfirmation:
    return DataDeletionConfirmation(
        id=701,
        request_id=17,
        preview_snapshot_id=snapshot.id,
        contract_version=CONFIRMATION_CONTRACT_VERSION,
        fingerprint_sha256=snapshot.fingerprint_sha256,
        confirmed_by="local-owner",
        confirmation_text_sha256="a" * 64,
        confirmation_note="typed full fingerprint",
        confirmed_at_kst=datetime(2026, 7, 12, 12, 5, 0),
    )


def _plan_row(plan: dict[str, object], fingerprint: str) -> dict[str, object]:
    metrics = plan["metrics"]
    assert isinstance(metrics, dict)
    return {
        "id": 901,
        "request_id": 17,
        "preview_snapshot_id": 501,
        "confirmation_id": 701,
        "contract_version": DRY_RUN_CONTRACT_VERSION,
        "source_fingerprint_sha256": plan["source_fingerprint_sha256"],
        "plan_fingerprint_sha256": fingerprint,
        "plan_json": json.dumps(plan, ensure_ascii=False),
        "operation_count": len(plan["database_operations"]) + len(plan["file_operations"]),
        "candidate_row_count": metrics["candidate_row_count"],
        "candidate_file_count": metrics["candidate_file_count"],
        "candidate_file_bytes": metrics["candidate_file_bytes"],
        "excluded_row_count": metrics["excluded_row_count"],
        "excluded_file_count": metrics["excluded_file_count"],
        "generated_by": "local-owner",
        "generation_note": "reviewed dry-run",
        "generated_at_kst": datetime(2026, 7, 12, 12, 10, 0),
    }


def _request(*, status: str = "approved") -> DataDeletionRequest:
    requested_at = datetime(2026, 7, 12, 10, 0, 0)
    return DataDeletionRequest(
        id=17,
        registered_player_id=1,
        account_id="account.test",
        shard="steam",
        player_name="Yuuki_Asuna---",
        deletion_scope="all",
        status=status,
        reason="dry-run test",
        requested_by_discord_user_id="100",
        requested_guild_id="10",
        requested_channel_id="20",
        requested_at_kst=requested_at,
        expires_at_kst=requested_at + timedelta(hours=24),
        reviewed_by="local:local-owner" if status == "approved" else None,
        reviewed_at_kst=requested_at if status == "approved" else None,
        review_note="approved" if status == "approved" else None,
        updated_at_kst=requested_at,
    )


if __name__ == "__main__":
    unittest.main()
