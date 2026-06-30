from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import gzip
import json
import unittest

from pubg_ai.config import AppConfig
from pubg_ai.local_settings import (
    DEFAULT_COMMAND_GROUPS,
    LocalSettingsError,
    LocalSettingsStore,
    check_storage_path,
)
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

    def test_local_program_collector_settings_override_env_values(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            settings_file = base_dir / "config" / "local_settings.json"
            store = LocalSettingsStore(settings_file, base_dir=base_dir)
            store.save_collector_settings(
                poll_interval_seconds=60,
                cycle_player_limit=75,
                player_lookup_chunk_size=5,
            )

            config = AppConfig.from_sources(
                {
                    "PUBG_COLLECTOR_POLL_INTERVAL_SECONDS": "300",
                    "PUBG_COLLECTOR_CYCLE_PLAYER_LIMIT": "100",
                    "PUBG_PLAYER_LOOKUP_CHUNK_SIZE": "10",
                    "PUBG_LOCAL_SETTINGS_FILE": str(settings_file),
                },
                base_dir=base_dir,
            )

            self.assertEqual(config.collector_poll_interval_seconds, 60)
            self.assertEqual(config.collector_cycle_player_limit, 75)
            self.assertEqual(config.player_lookup_chunk_size, 5)

    def test_local_program_web_settings_override_env_link(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            settings_file = base_dir / "config" / "local_settings.json"
            store = LocalSettingsStore(settings_file, base_dir=base_dir)
            store.save_web_settings("https://local.example:8000/")

            config = AppConfig.from_sources(
                {
                    "PUBG_LOCAL_WEB_BASE_URL": "http://env.example:8000",
                    "PUBG_LOCAL_SETTINGS_FILE": str(settings_file),
                },
                base_dir=base_dir,
            )

            self.assertEqual(config.local_web_base_url, "https://local.example:8000")

            store.save_web_settings(None)
            disabled = AppConfig.from_sources(
                {
                    "PUBG_LOCAL_WEB_BASE_URL": "http://env.example:8000",
                    "PUBG_LOCAL_SETTINGS_FILE": str(settings_file),
                },
                base_dir=base_dir,
            )

            self.assertIsNone(disabled.local_web_base_url)


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

    def test_write_telemetry_json_bytes_preserves_raw_payload(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = RawPayloadStore(Path(temp_dir), compression="gzip")
            created_at = datetime(2026, 6, 27, tzinfo=UTC)
            raw_body = b'[\n  {"_T":"LogMatchStart"}\n]'

            stored = store.write_json_bytes(
                payload_type="telemetry",
                shard="steam",
                match_id="telemetry-789",
                payload_bytes=raw_body,
                match_created_at=created_at,
            )

            self.assertEqual(
                stored.relative_path,
                "telemetry/steam/2026/06/27/telemetry-789.telemetry.json.gz",
            )
            self.assertTrue(store.verify(stored))

            stored_path = Path(temp_dir) / stored.relative_path
            with gzip.open(stored_path, "rb") as file:
                self.assertEqual(file.read(), raw_body)

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

    def test_storage_settings_do_not_overwrite_collector_settings(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            settings_file = base_dir / "config" / "local_settings.json"
            store = LocalSettingsStore(settings_file, base_dir=base_dir)
            store.save_collector_settings(60, 80, 10)
            store.save_storage_settings(base_dir / "raw", base_dir / "replays")

            collector = store.load_collector_settings()

            self.assertEqual(collector.poll_interval_seconds, 60)
            self.assertEqual(collector.cycle_player_limit, 80)
            self.assertEqual(collector.player_lookup_chunk_size, 10)

    def test_collector_settings_validate_program_adjustable_limits(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = LocalSettingsStore(Path(temp_dir) / "config" / "local_settings.json")

            with self.assertRaises(LocalSettingsError):
                store.save_collector_settings(30, 100, 10)

            with self.assertRaises(LocalSettingsError):
                store.save_collector_settings(60, 101, 10)

            with self.assertRaises(LocalSettingsError):
                store.save_collector_settings(60, 100, 11)

    def test_discord_permission_settings_can_be_saved_by_program(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = LocalSettingsStore(Path(temp_dir) / "config" / "local_settings.json")
            saved = store.save_discord_permission_settings(
                command_groups=DEFAULT_COMMAND_GROUPS,
                user_grants={"discord-user-1": ["profile_read", "ranking_read"]},
                guild_user_grants={"guild-1": {"discord-user-2": ["register"]}},
                global_admin_user_ids=["global-admin-1"],
            )
            loaded = store.load_discord_permission_settings()

            self.assertEqual(saved.user_grants["discord-user-1"], ["profile_read", "ranking_read"])
            self.assertEqual(saved.guild_user_grants["guild-1"]["discord-user-2"], ["register"])
            self.assertEqual(saved.global_admin_user_ids, ["global-admin-1"])
            self.assertEqual(loaded.user_grants["discord-user-1"], ["profile_read", "ranking_read"])
            self.assertIn("pubg-register", loaded.command_groups["register"])

    def test_discord_permission_settings_merge_new_default_commands(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings_file = Path(temp_dir) / "config" / "local_settings.json"
            settings_file.parent.mkdir(parents=True)
            settings_file.write_text(
                json.dumps(
                    {
                        "discord_permissions": {
                            "command_groups": {
                                "admin": ["유저삭제", "pubg-alerts"],
                            },
                            "user_grants": {},
                            "guild_user_grants": {},
                            "global_admin_user_ids": [],
                        }
                    }
                ),
                encoding="utf-8",
            )
            store = LocalSettingsStore(settings_file)

            loaded = store.load_discord_permission_settings()

            self.assertIn("pubg-alert-ack", loaded.command_groups["admin"])
            self.assertIn("pubg-alert-snooze", loaded.command_groups["admin"])
            self.assertIn("pubg-alert-note", loaded.command_groups["admin"])
            self.assertIn("pubg-alert-notes", loaded.command_groups["admin"])
            self.assertIn("pubg-alert-note-list", loaded.command_groups["admin"])
            self.assertIn("pubg-alert-resolution", loaded.command_groups["admin"])
            self.assertIn("pubg-register", loaded.command_groups["register"])

    def test_discord_permission_settings_reject_unknown_groups(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = LocalSettingsStore(Path(temp_dir) / "config" / "local_settings.json")

            with self.assertRaises(LocalSettingsError):
                store.save_discord_permission_settings(
                    command_groups=DEFAULT_COMMAND_GROUPS,
                    user_grants={"discord-user-1": ["unknown"]},
                )

    def test_discord_scope_settings_can_be_saved_by_program(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = LocalSettingsStore(Path(temp_dir) / "config" / "local_settings.json")
            saved = store.save_discord_scope_settings(
                guild_ranking_scopes={"guild-1": "guild", "guild-2": "global"},
                public_profile_default=True,
            )
            loaded = store.load_discord_scope_settings()

            self.assertEqual(saved.guild_ranking_scopes["guild-1"], "guild")
            self.assertEqual(saved.guild_ranking_scopes["guild-2"], "global")
            self.assertTrue(loaded.public_profile_default)

    def test_web_settings_can_be_saved_by_program(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = LocalSettingsStore(Path(temp_dir) / "config" / "local_settings.json")
            saved = store.save_web_settings("http://127.0.0.1:8000/")
            loaded = store.load_web_settings()

            self.assertEqual(saved.local_web_base_url, "http://127.0.0.1:8000")
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.local_web_base_url, "http://127.0.0.1:8000")

    def test_web_settings_reject_invalid_url(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = LocalSettingsStore(Path(temp_dir) / "config" / "local_settings.json")

            with self.assertRaises(LocalSettingsError):
                store.save_web_settings("ftp://127.0.0.1:8000")

    def test_local_settings_reject_secret_keys(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = LocalSettingsStore(Path(temp_dir) / "config" / "local_settings.json")

            with self.assertRaises(LocalSettingsError):
                store._write_settings({"PUBG_API_KEY": "secret"})

            with self.assertRaises(LocalSettingsError):
                store._write_settings({"nested": {"DISCORD_BOT_TOKEN": "secret"}})

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

    def test_write_replay_map_snapshot_jpeg_to_configured_root(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = ReplayArtifactStore(Path(temp_dir))
            created_at = datetime(2026, 6, 27, tzinfo=UTC)

            stored = store.write_bytes(
                artifact_type="map_snapshot",
                shard="steam",
                match_id="match-789",
                data=b"fake-jpeg",
                filename="route-summary.jpg",
                content_type="image/jpeg",
                match_created_at=created_at,
            )

            self.assertEqual(
                stored.relative_path,
                "map_snapshot/steam/2026/06/27/match-789/route-summary.jpg",
            )
            self.assertEqual(stored.content_type, "image/jpeg")
            self.assertTrue(store.verify(stored))

    def test_replay_resolve_path_rejects_escape_attempts(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = ReplayArtifactStore(Path(temp_dir))

            with self.assertRaises(ReplayStorageError):
                store.resolve_path("../outside.mp4")


if __name__ == "__main__":
    unittest.main()
