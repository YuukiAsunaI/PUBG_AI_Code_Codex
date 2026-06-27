from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import gzip
import json
import unittest

from pubg_ai.config import AppConfig
from pubg_ai.local_settings import LocalSettingsStore, check_storage_path
from pubg_ai.raw_storage import RawPayloadStore, RawStorageError
from pubg_ai.replay_storage import ReplayArtifactStore, ReplayStorageError


class AppConfigTests(unittest.TestCase):
    def test_raw_data_dir_can_be_configured_from_env(self) -> None:
        config = AppConfig.from_env(
            {
                "PUBG_RAW_DATA_DIR": "E:\\PUBG_AI_Data\\raw",
                "PUBG_RAW_COMPRESSION": "gzip",
            },
            base_dir=Path("C:/workspace"),
        )

        self.assertEqual(str(config.raw_data_dir), "E:\\PUBG_AI_Data\\raw")
        self.assertEqual(config.replay_data_dir, Path("C:/workspace") / "data" / "replays")
        self.assertEqual(config.raw_compression, "gzip")
        self.assertFalse(config.allow_storage_fallback)

    def test_relative_raw_data_dir_is_resolved_from_base_dir(self) -> None:
        config = AppConfig.from_env({}, base_dir=Path("C:/workspace"))

        self.assertEqual(config.raw_data_dir, Path("C:/workspace") / "data" / "raw")
        self.assertEqual(config.replay_data_dir, Path("C:/workspace") / "data" / "replays")

    def test_replay_data_dir_can_be_configured_from_env(self) -> None:
        config = AppConfig.from_env(
            {"PUBG_REPLAY_DATA_DIR": "F:\\PUBG_AI_Replays"},
            base_dir=Path("C:/workspace"),
        )

        self.assertEqual(str(config.replay_data_dir), "F:\\PUBG_AI_Replays")

    def test_local_program_settings_override_env_paths(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            settings_file = base_dir / "config" / "local_settings.json"
            raw_dir = base_dir / "selected-raw"
            replay_dir = base_dir / "selected-replays"
            LocalSettingsStore(settings_file, base_dir=base_dir).save_storage_settings(
                raw_data_dir=raw_dir,
                replay_data_dir=replay_dir,
            )

            config = AppConfig.from_sources(
                {
                    "PUBG_RAW_DATA_DIR": "env-raw",
                    "PUBG_REPLAY_DATA_DIR": "env-replays",
                    "PUBG_LOCAL_SETTINGS_FILE": str(settings_file),
                },
                base_dir=base_dir,
            )

            self.assertEqual(config.raw_data_dir, raw_dir)
            self.assertEqual(config.replay_data_dir, replay_dir)


class RawPayloadStoreTests(unittest.TestCase):
    def test_write_match_json_to_configured_root(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = RawPayloadStore(Path(temp_dir), compression="gzip")
            created_at = datetime(2026, 6, 27, tzinfo=UTC)

            stored = store.write_json(
                payload_type="match",
                shard="steam",
                match_id="match-123",
                payload={"data": {"id": "match-123"}},
                match_created_at=created_at,
            )

            self.assertEqual(
                stored.relative_path,
                "matches/steam/2026/06/27/match-123.json.gz",
            )
            self.assertTrue(stored.stored_at.endswith("+09:00"))
            self.assertTrue(store.verify(stored))

            stored_path = Path(temp_dir) / stored.relative_path
            with gzip.open(stored_path, "rt", encoding="utf-8") as file:
                self.assertEqual(json.load(file), {"data": {"id": "match-123"}})

    def test_write_telemetry_json_to_configured_root(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = RawPayloadStore(Path(temp_dir), compression="none")
            created_at = datetime(2026, 6, 27, tzinfo=UTC)

            stored = store.write_json(
                payload_type="telemetry",
                shard="kakao",
                match_id="telemetry-456",
                payload=[{"_T": "LogMatchStart"}],
                match_created_at=created_at,
            )

            self.assertEqual(
                stored.relative_path,
                "telemetry/kakao/2026/06/27/telemetry-456.telemetry.json",
            )
            self.assertTrue(store.verify(stored))

    def test_resolve_path_rejects_escape_attempts(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = RawPayloadStore(Path(temp_dir))

            with self.assertRaises(RawStorageError):
                store.resolve_path("../outside.json")


class LocalSettingsStoreTests(unittest.TestCase):
    def test_save_storage_settings_creates_dirs_and_loads_them(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            settings_file = base_dir / "config" / "local_settings.json"
            raw_dir = base_dir / "raw-drive" / "raw"
            replay_dir = base_dir / "replay-drive" / "replays"

            store = LocalSettingsStore(settings_file, base_dir=base_dir)
            saved = store.save_storage_settings(raw_dir, replay_dir)
            loaded = store.load_storage_settings()

            self.assertEqual(saved.raw_data_dir, raw_dir)
            self.assertEqual(saved.replay_data_dir, replay_dir)
            self.assertTrue(raw_dir.is_dir())
            self.assertTrue(replay_dir.is_dir())
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.raw_data_dir, raw_dir)
            self.assertEqual(loaded.replay_data_dir, replay_dir)

    def test_storage_status_reports_writable_paths(self) -> None:
        with TemporaryDirectory() as temp_dir:
            status = check_storage_path(Path(temp_dir))

            self.assertTrue(status.exists)
            self.assertTrue(status.is_dir)
            self.assertTrue(status.writable)
            self.assertIsNotNone(status.free_bytes)


class ReplayArtifactStoreTests(unittest.TestCase):
    def test_write_replay_timeline_to_configured_root(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = ReplayArtifactStore(Path(temp_dir))
            created_at = datetime(2026, 6, 27, tzinfo=UTC)

            stored = store.write_json(
                artifact_type="timeline",
                shard="steam",
                match_id="match-123",
                payload={"events": [{"t": 10, "x": 1200, "y": 2200}]},
                filename="timeline.json",
                match_created_at=created_at,
            )

            self.assertEqual(
                stored.relative_path,
                "timeline/steam/2026/06/27/match-123/timeline.json",
            )
            self.assertEqual(stored.storage_root, "PUBG_REPLAY_DATA_DIR")
            self.assertTrue(stored.stored_at.endswith("+09:00"))
            self.assertTrue(store.verify(stored))

    def test_write_replay_thumbnail_to_configured_root(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = ReplayArtifactStore(Path(temp_dir))
            created_at = datetime(2026, 6, 27, tzinfo=UTC)

            stored = store.write_bytes(
                artifact_type="thumbnail",
                shard="kakao",
                match_id="match-456",
                data=b"fake-png",
                filename="summary.png",
                content_type="image/png",
                match_created_at=created_at,
            )

            self.assertEqual(
                stored.relative_path,
                "thumbnail/kakao/2026/06/27/match-456/summary.png",
            )
            self.assertTrue(store.verify(stored))

    def test_replay_resolve_path_rejects_escape_attempts(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = ReplayArtifactStore(Path(temp_dir))

            with self.assertRaises(ReplayStorageError):
                store.resolve_path("../outside.mp4")


if __name__ == "__main__":
    unittest.main()
