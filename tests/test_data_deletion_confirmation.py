from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import json
import unittest
from unittest.mock import MagicMock

from pubg_ai.data_deletion_confirmation import (
    CONFIRMATION_CONTRACT_VERSION,
    DataDeletionConfirmationError,
    DataDeletionConfirmationService,
    confirmation_blockers,
    expected_confirmation_text,
    fingerprint_preview_record,
)
from pubg_ai.data_deletion_requests import DataDeletionRequest


class DataDeletionConfirmationServiceTests(unittest.TestCase):
    def test_expired_pending_request_cannot_capture_snapshot(self) -> None:
        connection = ScriptedConnection([])
        preview_service = MagicMock()
        service = DataDeletionConfirmationService(
            connection,
            preview_service=preview_service,
        )
        request = replace(
            _request(status="pending"),
            expires_at_kst=datetime(2026, 7, 12, 11, 59, 59),
        )

        with self.assertRaisesRegex(DataDeletionConfirmationError, "expired"):
            service.capture_snapshot(
                request,
                actor_id="local-owner",
                reference_kst=datetime(2026, 7, 12, 12, 0, 0),
            )

        preview_service.build_preview.assert_not_called()
        self.assertEqual(connection.executed, [])

    def test_fingerprint_ignores_volatile_display_fields_and_record_order(self) -> None:
        first = _preview_record()
        second = deepcopy(first)
        second["generated_at_kst"] = "2026-07-12T13:00:00+09:00"
        second["file_limit_per_catalog"] = 500
        second["warnings"] = ["different presentation warning"]
        second["row_impacts"].reverse()
        second["replay_files"]["files"].reverse()

        first_fingerprint, first_manifest = fingerprint_preview_record(first)
        second_fingerprint, second_manifest = fingerprint_preview_record(second)

        self.assertEqual(first_fingerprint, second_fingerprint)
        self.assertEqual(first_manifest, second_manifest)
        changed = deepcopy(first)
        changed["candidate_row_count"] = 4
        changed_fingerprint, _ = fingerprint_preview_record(changed)
        self.assertNotEqual(first_fingerprint, changed_fingerprint)

    def test_capture_snapshot_inserts_immutable_preview_and_manifest(self) -> None:
        preview_record = _preview_record()
        fingerprint, manifest = fingerprint_preview_record(preview_record)
        snapshot_row = _snapshot_row(fingerprint=fingerprint, preview=preview_record, manifest=manifest)
        connection = ScriptedConnection(
            [
                {
                    "contains": "SELECT status, expires_at_kst",
                    "row": {
                        "status": "pending",
                        "expires_at_kst": datetime(2026, 7, 13, 12, 0, 0),
                    },
                },
                {"contains": "INSERT INTO data_deletion_preview_snapshots", "lastrowid": 501},
                {"contains": "WHERE id = %s", "row": snapshot_row},
            ]
        )
        preview = MagicMock()
        preview.to_record.return_value = preview_record
        preview_service = MagicMock()
        preview_service.build_preview.return_value = preview
        service = DataDeletionConfirmationService(
            connection,
            preview_service=preview_service,
        )

        snapshot = service.capture_snapshot(
            _request(status="pending"),
            actor_id="local-owner",
            note="impact reviewed",
            reference_kst=datetime(2026, 7, 12, 12, 0, 0),
        )

        self.assertEqual(snapshot.id, 501)
        self.assertEqual(snapshot.fingerprint_sha256, fingerprint)
        self.assertEqual(snapshot.manifest_json, manifest)
        self.assertEqual(connection.begin_count, 1)
        self.assertEqual(connection.commit_count, 1)
        self.assertEqual(connection.rollback_count, 0)
        preview_service.build_preview.assert_called_once_with(
            unittest.mock.ANY,
            file_limit=500,
        )
        insert_params = connection.executed[1][1]
        self.assertEqual(insert_params[0], 17)
        self.assertEqual(insert_params[1], CONFIRMATION_CONTRACT_VERSION)
        self.assertEqual(insert_params[2], fingerprint)
        self.assertEqual(json.loads(str(insert_params[3])), preview_record)
        self.assertEqual(json.loads(str(insert_params[4])), manifest)
        self.assertFalse(
            any(
                keyword in query.upper()
                for query, _ in connection.executed
                for keyword in ("UPDATE ", "DELETE ", "REPLACE ")
            )
        )

    def test_confirm_snapshot_revalidates_live_fingerprint_and_records_hash(self) -> None:
        preview_record = _preview_record()
        fingerprint, manifest = fingerprint_preview_record(preview_record)
        snapshot_row = _snapshot_row(fingerprint=fingerprint, preview=preview_record, manifest=manifest)
        confirmation_text = expected_confirmation_text(17, fingerprint)
        confirmation_hash = hashlib.sha256(confirmation_text.encode("utf-8")).hexdigest()
        confirmation_row = _confirmation_row(
            fingerprint=fingerprint,
            confirmation_text_sha256=confirmation_hash,
        )
        connection = ScriptedConnection(
            [
                {"contains": "FROM data_deletion_preview_snapshots", "rows": [snapshot_row]},
                {"contains": "WHERE preview_snapshot_id = %s", "row": None},
                {"contains": "SELECT status FROM data_deletion_requests", "row": {"status": "approved"}},
                {
                    "contains": "SELECT id, fingerprint_sha256",
                    "row": {"id": 501, "fingerprint_sha256": fingerprint},
                },
                {"contains": "SELECT id FROM data_deletion_confirmations", "row": None},
                {"contains": "INSERT INTO data_deletion_confirmations", "lastrowid": 701},
                {"contains": "WHERE id = %s", "row": confirmation_row},
            ]
        )
        preview = MagicMock()
        preview.to_record.return_value = preview_record
        preview_service = MagicMock()
        preview_service.build_preview.return_value = preview
        service = DataDeletionConfirmationService(
            connection,
            preview_service=preview_service,
        )

        confirmation = service.confirm_snapshot(
            _request(status="approved"),
            snapshot_id=501,
            fingerprint_sha256=fingerprint,
            confirmation_text=confirmation_text,
            actor_id="local-owner",
            note="typed full fingerprint",
            reference_kst=datetime(2026, 7, 12, 12, 5, 0),
        )

        self.assertEqual(confirmation.id, 701)
        self.assertEqual(confirmation.confirmation_text_sha256, confirmation_hash)
        self.assertEqual(connection.begin_count, 1)
        self.assertEqual(connection.commit_count, 1)
        preview_service.build_preview.assert_called_once_with(
            unittest.mock.ANY,
            file_limit=500,
        )
        insert_params = next(
            params
            for query, params in connection.executed
            if "INSERT INTO data_deletion_confirmations" in query
        )
        self.assertEqual(insert_params[0:4], (17, 501, CONFIRMATION_CONTRACT_VERSION, fingerprint))
        self.assertEqual(insert_params[5], confirmation_hash)

    def test_confirmation_rolls_back_when_request_is_cancelled_during_lock(self) -> None:
        preview_record = _preview_record()
        fingerprint, manifest = fingerprint_preview_record(preview_record)
        snapshot_row = _snapshot_row(fingerprint=fingerprint, preview=preview_record, manifest=manifest)
        connection = ScriptedConnection(
            [
                {"contains": "FROM data_deletion_preview_snapshots", "rows": [snapshot_row]},
                {"contains": "WHERE preview_snapshot_id = %s", "row": None},
                {"contains": "SELECT status FROM data_deletion_requests", "row": {"status": "cancelled"}},
            ]
        )
        preview = MagicMock()
        preview.to_record.return_value = preview_record
        preview_service = MagicMock()
        preview_service.build_preview.return_value = preview
        service = DataDeletionConfirmationService(
            connection,
            preview_service=preview_service,
        )

        with self.assertRaisesRegex(DataDeletionConfirmationError, "changed to cancelled"):
            service.confirm_snapshot(
                _request(status="approved"),
                snapshot_id=501,
                fingerprint_sha256=fingerprint,
                confirmation_text=expected_confirmation_text(17, fingerprint),
                actor_id="local-owner",
            )

        self.assertEqual(connection.begin_count, 1)
        self.assertEqual(connection.commit_count, 0)
        self.assertEqual(connection.rollback_count, 1)
        self.assertFalse(
            any("INSERT INTO data_deletion_confirmations" in query for query, _ in connection.executed)
        )

    def test_confirmation_rejects_stale_snapshot_and_wrong_text_without_insert(self) -> None:
        preview_record = _preview_record()
        fingerprint, manifest = fingerprint_preview_record(preview_record)
        snapshot_row = _snapshot_row(fingerprint=fingerprint, preview=preview_record, manifest=manifest)
        stale_record = deepcopy(preview_record)
        stale_record["candidate_row_count"] = 99

        for live_record, confirmation_text, message in [
            (
                stale_record,
                expected_confirmation_text(17, fingerprint),
                "current deletion impact differs",
            ),
            (preview_record, "CONFIRM DELETE REQUEST 17 wrong", "confirmation text"),
        ]:
            connection = ScriptedConnection(
                [
                    {"contains": "FROM data_deletion_preview_snapshots", "rows": [snapshot_row]},
                    {"contains": "WHERE preview_snapshot_id = %s", "row": None},
                ]
            )
            preview = MagicMock()
            preview.to_record.return_value = live_record
            preview_service = MagicMock()
            preview_service.build_preview.return_value = preview
            service = DataDeletionConfirmationService(
                connection,
                preview_service=preview_service,
            )

            with self.assertRaisesRegex(DataDeletionConfirmationError, message):
                service.confirm_snapshot(
                    _request(status="approved"),
                    snapshot_id=501,
                    fingerprint_sha256=fingerprint,
                    confirmation_text=confirmation_text,
                    actor_id="local-owner",
                )

            self.assertEqual(connection.begin_count, 0)
            self.assertFalse(
                any("INSERT INTO data_deletion_confirmations" in query for query, _ in connection.executed)
            )

    def test_confirmation_blockers_require_approved_complete_clean_candidate_snapshot(self) -> None:
        preview_record = _preview_record()
        fingerprint, manifest = fingerprint_preview_record(preview_record)
        row = _snapshot_row(fingerprint=fingerprint, preview=preview_record, manifest=manifest)
        row["catalog_complete"] = 0
        row["filesystem_issue_count"] = 2
        row["candidate_row_count"] = 0
        row["candidate_file_count"] = 0
        snapshot = _snapshot_from_service_row(row)

        blockers = confirmation_blockers(
            _request(status="pending"),
            snapshot,
            already_confirmed=True,
        )

        self.assertTrue(any("approved" in blocker for blocker in blockers))
        self.assertTrue(any("truncated" in blocker for blocker in blockers))
        self.assertTrue(any("filesystem" in blocker for blocker in blockers))
        self.assertTrue(any("no player-owned" in blocker for blocker in blockers))
        self.assertTrue(any("already confirmed" in blocker for blocker in blockers))


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
            "resolved_path": "D:/BackUP/replay/timeline/steam/match-2.json",
            "declared_size_bytes": 20,
            "actual_size_bytes": 20,
            "sha256": "2" * 64,
            "storage_root_matches": True,
            "path_safe": True,
            "exists": True,
            "size_matches": True,
            "shared_match": False,
            "participant_count": None,
            "verification_status": "present",
            "verification_error": None,
            "checksum_verified": False,
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
            "resolved_path": "D:/BackUP/replay/map_snapshot/steam/match-1.jpeg",
            "declared_size_bytes": 10,
            "actual_size_bytes": 10,
            "sha256": "1" * 64,
            "storage_root_matches": True,
            "path_safe": True,
            "exists": True,
            "size_matches": True,
            "shared_match": False,
            "participant_count": None,
            "verification_status": "present",
            "verification_error": None,
            "checksum_verified": False,
        },
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
        "file_limit_per_catalog": 100,
        "included_sections": {
            "registration": True,
            "normalized": True,
            "raw": True,
            "replay": True,
        },
        "matched_match_count": 2,
        "candidate_row_count": 3,
        "preserved_reference_row_count": 2,
        "row_impacts": [
            {
                "table": "player_item_events",
                "category": "normalized",
                "relationship": "player_owned",
                "row_count": 2,
                "deletion_candidate": True,
            },
            {
                "table": "registered_players",
                "category": "registration",
                "relationship": "player_owned",
                "row_count": 1,
                "deletion_candidate": True,
            },
        ],
        "preserved_references": [
            {
                "table": "matches",
                "category": "shared_match_context",
                "relationship": "shared_match",
                "row_count": 2,
                "deletion_candidate": False,
            }
        ],
        "raw_files": {
            "category": "raw",
            "included": True,
            "total_records": 2,
            "total_declared_size_bytes": 40,
            "deletion_candidate_records": 0,
            "shared_match_records": 2,
            "limit": 100,
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
            "limit": 100,
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


def _snapshot_row(
    *,
    fingerprint: str,
    preview: dict[str, object],
    manifest: dict[str, object],
) -> dict[str, object]:
    return {
        "id": 501,
        "request_id": 17,
        "contract_version": CONFIRMATION_CONTRACT_VERSION,
        "fingerprint_sha256": fingerprint,
        "preview_json": json.dumps(preview, ensure_ascii=False),
        "manifest_json": json.dumps(manifest, ensure_ascii=False),
        "catalog_complete": 1,
        "filesystem_issue_count": 0,
        "candidate_row_count": 3,
        "candidate_file_count": 2,
        "captured_by": "local-owner",
        "capture_note": "impact reviewed",
        "captured_at_kst": datetime(2026, 7, 12, 12, 0, 0),
    }


def _confirmation_row(
    *,
    fingerprint: str,
    confirmation_text_sha256: str,
) -> dict[str, object]:
    return {
        "id": 701,
        "request_id": 17,
        "preview_snapshot_id": 501,
        "contract_version": CONFIRMATION_CONTRACT_VERSION,
        "fingerprint_sha256": fingerprint,
        "confirmed_by": "local-owner",
        "confirmation_text_sha256": confirmation_text_sha256,
        "confirmation_note": "typed full fingerprint",
        "confirmed_at_kst": datetime(2026, 7, 12, 12, 5, 0),
    }


def _snapshot_from_service_row(row: dict[str, object]):
    connection = ScriptedConnection([{"contains": "WHERE id = %s", "row": row}])
    service = DataDeletionConfirmationService(
        connection,
        preview_service=MagicMock(),
    )
    return service.get_snapshot(int(row["id"]))


def _request(*, status: str) -> DataDeletionRequest:
    requested_at = datetime(2026, 7, 12, 10, 0, 0)
    return DataDeletionRequest(
        id=17,
        registered_player_id=1,
        account_id="account.test",
        shard="steam",
        player_name="Yuuki_Asuna---",
        deletion_scope="all",
        status=status,
        reason="confirmation test",
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
