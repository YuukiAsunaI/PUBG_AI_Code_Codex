from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import gzip
import json
import unittest

from pubg_ai.config import AppConfig
from pubg_ai.raw_storage import RawPayloadStore, RawStorageError


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
        self.assertEqual(config.raw_compression, "gzip")
        self.assertFalse(config.allow_storage_fallback)

    def test_relative_raw_data_dir_is_resolved_from_base_dir(self) -> None:
        config = AppConfig.from_env({}, base_dir=Path("C:/workspace"))

        self.assertEqual(config.raw_data_dir, Path("C:/workspace") / "data" / "raw")


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


if __name__ == "__main__":
    unittest.main()

