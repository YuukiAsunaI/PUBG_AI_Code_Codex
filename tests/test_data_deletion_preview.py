from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from pubg_ai.data_deletion_preview import (
    DataDeletionImpactPreviewService,
    DataDeletionPreviewError,
    NORMALIZED_PLAYER_TABLES,
)
from pubg_ai.data_deletion_requests import DataDeletionRequest


class DataDeletionImpactPreviewServiceTests(unittest.TestCase):
    def test_all_scope_counts_rows_and_catalogs_files_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            raw_root = base / "raw"
            replay_root = base / "replay"
            raw_path = raw_root / "matches/steam/2026/07/12/match-1.json.gz"
            replay_path = replay_root / "timeline/steam/2026/07/12/match-1/player.json"
            raw_path.parent.mkdir(parents=True)
            replay_path.parent.mkdir(parents=True)
            raw_body = b"raw-body"
            replay_body = b"replay-body"
            raw_path.write_bytes(raw_body)
            replay_path.write_bytes(replay_body)

            connection = PreviewConnection(
                counts={
                    "registered_players": 1,
                    "player_aliases": 2,
                    "player_collection_states": 1,
                    "_matched_matches": 2,
                    "match_participants": 2,
                    "player_item_events": 3,
                    "_related_combat_locations": 5,
                    "match_care_package_events": 1,
                    "match_plane_routes": 1,
                    "match_phase_events": 2,
                    "raw_player_snapshots": 4,
                },
                raw_summaries={
                    "raw_match_payloads": {
                        "row_count": 2,
                        "total_size_bytes": len(raw_body) + 50,
                        "shared_match_count": 2,
                    },
                    "raw_telemetry_payloads": {
                        "row_count": 1,
                        "total_size_bytes": 30,
                        "shared_match_count": 1,
                    },
                },
                raw_files=[
                    {
                        "record_id": 10,
                        "source_table": "raw_match_payloads",
                        "file_type": "match",
                        "match_id": "match-1",
                        "storage_backend": "local_file",
                        "storage_root": "PUBG_RAW_DATA_DIR",
                        "relative_path": "matches/steam/2026/07/12/match-1.json.gz",
                        "declared_size_bytes": len(raw_body),
                        "sha256": "a" * 64,
                        "participant_count": 100,
                    },
                    {
                        "record_id": 11,
                        "source_table": "raw_telemetry_payloads",
                        "file_type": "telemetry",
                        "match_id": "match-2",
                        "storage_backend": "local_file",
                        "storage_root": "PUBG_RAW_DATA_DIR",
                        "relative_path": "../outside.telemetry.json.gz",
                        "declared_size_bytes": 30,
                        "sha256": "b" * 64,
                        "participant_count": 98,
                    },
                ],
                replay_summary={"row_count": 1, "total_size_bytes": len(replay_body)},
                replay_files=[
                    {
                        "record_id": 20,
                        "source_table": "replay_artifacts",
                        "file_type": "timeline",
                        "match_id": "match-1",
                        "storage_backend": "local_file",
                        "storage_root": "PUBG_REPLAY_DATA_DIR",
                        "relative_path": "timeline/steam/2026/07/12/match-1/player.json",
                        "declared_size_bytes": len(replay_body),
                        "sha256": "c" * 64,
                    }
                ],
            )

            preview = DataDeletionImpactPreviewService(
                connection,
                raw_data_dir=raw_root,
                replay_data_dir=replay_root,
            ).build_preview(_request("all"), file_limit=2)
            record = preview.to_record()

            self.assertEqual(record["matched_match_count"], 2)
            self.assertEqual(record["candidate_row_count"], 14)
            self.assertEqual(record["preserved_reference_row_count"], 11)
            self.assertEqual(record["raw_files"]["total_records"], 3)
            self.assertEqual(record["raw_files"]["deletion_candidate_records"], 0)
            self.assertEqual(record["raw_files"]["shared_match_records"], 3)
            self.assertTrue(record["raw_files"]["truncated"])
            self.assertEqual(record["raw_files"]["files"][0]["verification_status"], "present")
            self.assertFalse(record["raw_files"]["files"][0]["deletion_candidate"])
            self.assertEqual(record["raw_files"]["files"][1]["verification_status"], "unsafe_path")
            self.assertIsNone(record["raw_files"]["files"][1]["resolved_path"])
            self.assertEqual(record["replay_files"]["files"][0]["verification_status"], "present")
            self.assertTrue(record["replay_files"]["files"][0]["deletion_candidate"])
            self.assertFalse(record["verification"]["execution_enabled"])
            self.assertFalse(record["verification"]["ready_for_execution"])
            self.assertFalse(record["verification"]["catalog_complete"])
            self.assertEqual(record["verification"]["unsafe_path_count"], 1)
            self.assertFalse(record["verification"]["checksum_verification_performed"])
            self.assertEqual(raw_path.read_bytes(), raw_body)
            self.assertEqual(replay_path.read_bytes(), replay_body)
            self.assertFalse(
                any(
                    keyword in query.upper()
                    for query in connection.queries
                    for keyword in ("INSERT ", "UPDATE ", "DELETE ", "REPLACE ")
                )
            )

    def test_normalized_scope_excludes_registration_and_file_catalog_queries(self) -> None:
        connection = PreviewConnection(
            counts={"_matched_matches": 1, "match_participants": 1},
        )
        preview = DataDeletionImpactPreviewService(
            connection,
            raw_data_dir=Path("D:/BackUP/raw"),
            replay_data_dir=Path("D:/BackUP/replay"),
        ).build_preview(_request("normalized"), file_limit=25)
        record = preview.to_record()

        self.assertEqual(
            record["included_sections"],
            {"registration": False, "normalized": True, "raw": False, "replay": False},
        )
        self.assertFalse(record["raw_files"]["included"])
        self.assertFalse(record["replay_files"]["included"])
        query_text = "\n".join(connection.queries)
        self.assertNotIn("raw_match_payloads AS payloads", query_text)
        self.assertNotIn("FROM replay_artifacts", query_text)
        self.assertNotIn("FROM registered_players\n", query_text)
        self.assertEqual(
            {impact["table"] for impact in record["row_impacts"]},
            set(NORMALIZED_PLAYER_TABLES),
        )

    def test_file_limit_is_validated_before_querying(self) -> None:
        connection = PreviewConnection()
        service = DataDeletionImpactPreviewService(
            connection,
            raw_data_dir=Path("D:/BackUP/raw"),
            replay_data_dir=Path("D:/BackUP/replay"),
        )

        with self.assertRaisesRegex(DataDeletionPreviewError, "file_limit"):
            service.build_preview(_request("raw"), file_limit=0)

        self.assertEqual(connection.queries, [])


class PreviewConnection:
    def __init__(
        self,
        *,
        counts: dict[str, int] | None = None,
        raw_summaries: dict[str, dict[str, int]] | None = None,
        raw_files: list[dict[str, object]] | None = None,
        replay_summary: dict[str, int] | None = None,
        replay_files: list[dict[str, object]] | None = None,
    ) -> None:
        self.counts = counts or {}
        self.raw_summaries = raw_summaries or {}
        self.raw_files = raw_files or []
        self.replay_summary = replay_summary or {"row_count": 0, "total_size_bytes": 0}
        self.replay_files = replay_files or []
        self.queries: list[str] = []

    def cursor(self) -> "PreviewCursor":
        return PreviewCursor(self)


class PreviewCursor:
    def __init__(self, connection: PreviewConnection) -> None:
        self.connection = connection
        self.row: dict[str, object] | None = None
        self.rows: list[dict[str, object]] = []

    def __enter__(self) -> "PreviewCursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, query: str, params: tuple[object, ...] = ()) -> None:
        del params
        normalized = "\n".join(line.rstrip() for line in query.strip().splitlines())
        self.connection.queries.append(normalized)
        self.row = None
        self.rows = []

        if "AS raw_candidates" in normalized:
            self.rows = list(self.connection.raw_files)
            return
        if "FROM replay_artifacts" in normalized and "id AS record_id" in normalized:
            self.rows = list(self.connection.replay_files)
            return
        if "FROM replay_artifacts" in normalized and "total_size_bytes" in normalized:
            self.row = dict(self.connection.replay_summary)
            return
        for table in ("raw_match_payloads", "raw_telemetry_payloads"):
            if f"FROM {table} AS payloads" in normalized and "total_size_bytes" in normalized:
                self.row = dict(
                    self.connection.raw_summaries.get(
                        table,
                        {"row_count": 0, "total_size_bytes": 0, "shared_match_count": 0},
                    )
                )
                return
        if "COUNT(DISTINCT participants.match_id)" in normalized:
            self.row = {"row_count": self.connection.counts.get("_matched_matches", 0)}
            return
        if "related_account_id" in normalized:
            self.row = {"row_count": self.connection.counts.get("_related_combat_locations", 0)}
            return

        known_tables = (
            "match_care_package_events",
            "match_plane_routes",
            "match_phase_events",
            "registered_players",
            "player_aliases",
            "player_collection_states",
            *NORMALIZED_PLAYER_TABLES,
            "raw_player_snapshots",
        )
        for table in known_tables:
            if f"FROM {table}" in normalized:
                self.row = {"row_count": self.connection.counts.get(table, 0)}
                return
        raise AssertionError(f"unexpected query: {normalized}")

    def fetchone(self) -> dict[str, object] | None:
        return self.row

    def fetchall(self) -> list[dict[str, object]]:
        return self.rows


def _request(scope: str) -> DataDeletionRequest:
    requested_at = datetime(2026, 7, 12, 10, 0, 0)
    return DataDeletionRequest(
        id=102,
        registered_player_id=1,
        account_id="account.test",
        shard="steam",
        player_name="Yuuki_Asuna---",
        deletion_scope=scope,
        status="pending",
        reason="preview test",
        requested_by_discord_user_id="100",
        requested_guild_id="10",
        requested_channel_id="20",
        requested_at_kst=requested_at,
        expires_at_kst=requested_at + timedelta(hours=24),
        updated_at_kst=requested_at,
    )


if __name__ == "__main__":
    unittest.main()
