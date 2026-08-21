from __future__ import annotations

from datetime import datetime
import unittest

from pubg_ai.replay_artifact_catalog import (
    ReplayArtifactRecord,
    is_timeline_playback_ready,
    list_replay_artifacts,
    normalize_artifact_limit,
)


class ReplayArtifactCatalogTests(unittest.TestCase):
    def test_normalizes_artifact_limit(self) -> None:
        self.assertEqual(normalize_artifact_limit(-5), 1)
        self.assertEqual(normalize_artifact_limit(0), 1)
        self.assertEqual(normalize_artifact_limit(50), 50)
        self.assertEqual(normalize_artifact_limit(999), 200)

    def test_record_serializes_datetimes_and_view_url(self) -> None:
        record = ReplayArtifactRecord(
            id=7,
            match_id="match-1",
            shard="steam",
            artifact_type="map_snapshot",
            artifact_name="player-route",
            account_id="account.test",
            player_name="Yuuki_Asuna---",
            map_name="Tiger_Main",
            game_mode="squad",
            match_type="official",
            match_created_at_kst=datetime(2026, 6, 28, 9, 13, 17),
            storage_backend="local_file",
            storage_root="PUBG_REPLAY_DATA_DIR",
            relative_path="map_snapshot/steam/2026/06/28/match-1/player-route.jpg",
            content_type="image/jpeg",
            size_bytes=12345,
            sha256="a" * 64,
            renderer_version="map-snapshot-v1",
            generated_at_kst=datetime(2026, 6, 29, 3, 30, 0),
        )

        payload = record.to_record()

        self.assertEqual(payload["view_url"], "/replay/artifacts/7/file")
        self.assertEqual(payload["match_created_at_kst"], "2026-06-28T09:13:17")
        self.assertEqual(payload["generated_at_kst"], "2026-06-29T03:30:00")
        self.assertEqual(payload["relative_path"], "map_snapshot/steam/2026/06/28/match-1/player-route.jpg")
        self.assertTrue(payload["playback_ready"])

    def test_only_current_timeline_shapes_are_playback_ready(self) -> None:
        self.assertFalse(is_timeline_playback_ready("player-timeline-v3"))
        self.assertFalse(is_timeline_playback_ready("player-timeline"))
        self.assertFalse(is_timeline_playback_ready(""))
        self.assertFalse(is_timeline_playback_ready("player-timeline-v5"))
        self.assertFalse(is_timeline_playback_ready("player-timeline-v6"))
        self.assertFalse(is_timeline_playback_ready("player-timeline-v7"))
        self.assertFalse(is_timeline_playback_ready("player-timeline-v8"))
        self.assertTrue(is_timeline_playback_ready("player-timeline-v9"))
        self.assertTrue(is_timeline_playback_ready("player-timeline-v12"))

        record = ReplayArtifactRecord(
            id=8,
            match_id="match-2",
            shard="steam",
            artifact_type="timeline",
            artifact_name="player-timeline",
            account_id="account.test",
            player_name="Yuuki_Asuna---",
            map_name="Tiger_Main",
            game_mode="squad",
            match_type="official",
            match_created_at_kst=datetime(2026, 6, 28, 9, 13, 17),
            storage_backend="local_file",
            storage_root="PUBG_REPLAY_DATA_DIR",
            relative_path="timeline/steam/2026/06/28/match-2/player-timeline.json",
            content_type="application/json",
            size_bytes=12345,
            sha256="b" * 64,
            renderer_version="player-timeline-v3",
            generated_at_kst=datetime(2026, 6, 29, 3, 30, 0),
        )
        self.assertFalse(record.to_record()["playback_ready"])

    def test_list_filters_out_lower_renderer_versions(self) -> None:
        connection = CatalogConnection()

        records = list_replay_artifacts(connection, artifact_type="timeline", limit=25)

        self.assertEqual(records, [])
        query, params = connection.cursor_obj.executed[0]
        self.assertIn("NOT EXISTS", query)
        self.assertIn("newer_artifacts.renderer_version", query)
        self.assertIn("SUBSTRING_INDEX", query)
        self.assertEqual(params, ["timeline", 25])


class CatalogConnection:
    def __init__(self) -> None:
        self.cursor_obj = CatalogCursor()

    def cursor(self) -> "CatalogCursor":
        return self.cursor_obj


class CatalogCursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, list[object]]] = []

    def __enter__(self) -> "CatalogCursor":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, query: str, params: list[object]) -> None:
        self.executed.append((query, params))

    def fetchall(self) -> list[dict[str, object]]:
        return []


if __name__ == "__main__":
    unittest.main()
