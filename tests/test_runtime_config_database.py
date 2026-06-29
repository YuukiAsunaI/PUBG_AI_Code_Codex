from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pubg_ai.config import DatabaseConfig, RuntimeConfig, load_dotenv_values
from pubg_ai.database import SCHEMA_VERSION, schema_statements
from pubg_ai.local_settings import DEFAULT_COMMAND_GROUPS


class RuntimeConfigTests(unittest.TestCase):
    def test_load_dotenv_values_parses_quoted_values(self) -> None:
        with TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "PUBG_API_KEY=pubg-key",
                        'MYSQL_PASSWORD="complex value"',
                        "MYSQL_DATABASE=pubg_ai",
                    ]
                ),
                encoding="utf-8",
            )

            values = load_dotenv_values(env_file)

            self.assertEqual(values["PUBG_API_KEY"], "pubg-key")
            self.assertEqual(values["MYSQL_PASSWORD"], "complex value")
            self.assertEqual(values["MYSQL_DATABASE"], "pubg_ai")

    def test_runtime_config_reports_secret_status_without_raw_values(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            (base_dir / ".env").write_text(
                "\n".join(
                    [
                        "PUBG_API_KEY=pubg-secret",
                        "DISCORD_BOT_TOKEN=discord-secret",
                        "MYSQL_PASSWORD=db-secret",
                        "PUBG_RAW_DATA_DIR=raw",
                        "PUBG_REPLAY_DATA_DIR=replays",
                        "PUBG_LOCAL_WEB_BASE_URL=http://127.0.0.1:8000/",
                    ]
                ),
                encoding="utf-8",
            )

            config = RuntimeConfig.from_sources(env={}, base_dir=base_dir)

            secret_status = config.secrets.status()
            self.assertTrue(secret_status["PUBG_API_KEY"].configured)
            self.assertEqual(secret_status["PUBG_API_KEY"].length, len("pubg-secret"))
            self.assertNotIn("pubg-secret", str(secret_status["PUBG_API_KEY"].to_record()))
            self.assertEqual(config.database.password, "db-secret")
            self.assertEqual(config.app.raw_data_dir, base_dir / "raw")
            self.assertEqual(config.app.local_web_base_url, "http://127.0.0.1:8000")

    def test_database_config_safe_record_masks_password(self) -> None:
        config = DatabaseConfig(password="super-secret")

        record = config.safe_record()

        self.assertEqual(record["password"], {"configured": True, "length": 12})
        self.assertNotIn("super-secret", str(record))


class DatabaseSchemaTests(unittest.TestCase):
    def test_schema_contains_mvp_tables(self) -> None:
        schema = "\n".join(schema_statements())

        for table_name in [
            "registered_players",
            "player_aliases",
            "api_fetch_jobs",
            "matches",
            "raw_match_payloads",
            "raw_telemetry_payloads",
            "player_match_combat_summaries",
            "player_weapon_match_stats",
            "player_item_events",
            "player_item_match_stats",
            "player_position_samples",
            "player_landing_events",
            "player_movement_summaries",
            "player_combat_location_events",
            "player_combat_loadout_snapshots",
            "match_care_package_events",
            "match_plane_routes",
            "replay_artifacts",
        ]:
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table_name}", schema)

        self.assertEqual(SCHEMA_VERSION, 5)

    def test_schema_tracks_official_rate_limit_headers(self) -> None:
        schema = "\n".join(schema_statements())

        self.assertIn("rate_limit_limit", schema)
        self.assertIn("rate_limit_remaining", schema)
        self.assertIn("rate_limit_reset_epoch", schema)


class DiscordCommandDefaultsTests(unittest.TestCase):
    def test_first_mvp_korean_command_names_are_present(self) -> None:
        self.assertIn("유저등록", DEFAULT_COMMAND_GROUPS["register"])
        self.assertIn("유저조회", DEFAULT_COMMAND_GROUPS["profile_read"])
        self.assertIn("전적", DEFAULT_COMMAND_GROUPS["profile_read"])
        self.assertIn("무기", DEFAULT_COMMAND_GROUPS["profile_read"])
        self.assertIn("매치", DEFAULT_COMMAND_GROUPS["profile_read"])
        self.assertIn("pubg-stats", DEFAULT_COMMAND_GROUPS["profile_read"])
        self.assertIn("pubg-weapon", DEFAULT_COMMAND_GROUPS["profile_read"])
        self.assertIn("랭킹", DEFAULT_COMMAND_GROUPS["ranking_read"])
        self.assertIn("유저삭제", DEFAULT_COMMAND_GROUPS["admin"])


if __name__ == "__main__":
    unittest.main()
