from __future__ import annotations

from datetime import datetime
import unittest

from pubg_ai.replay_artifact_catalog import ReplayArtifactRecord, normalize_artifact_limit


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


if __name__ == "__main__":
    unittest.main()
