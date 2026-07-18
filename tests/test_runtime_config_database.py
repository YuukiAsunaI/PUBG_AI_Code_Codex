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
            "worker_run_history",
            "system_alert_history",
            "system_alert_notes",
            "data_deletion_requests",
            "data_deletion_request_events",
            "data_deletion_preview_snapshots",
            "data_deletion_confirmations",
            "data_deletion_dry_run_plans",
            "data_deletion_backup_evidence",
            "data_deletion_rehearsal_runs",
            "data_deletion_backup_verification_runs",
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
            "match_phase_events",
            "replay_artifacts",
        ]:
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table_name}", schema)

        self.assertEqual(SCHEMA_VERSION, 14)
        self.assertIn("fk_data_deletion_events_request", schema)
        self.assertIn("fk_data_deletion_confirmation_snapshot", schema)
        self.assertIn("uq_data_deletion_confirmation_snapshot", schema)
        self.assertIn("fk_data_deletion_dry_run_snapshot", schema)
        self.assertIn("fk_data_deletion_dry_run_confirmation", schema)
        self.assertIn("plan_fingerprint_sha256", schema)
        self.assertIn("fk_data_deletion_backup_plan", schema)
        self.assertIn("fk_data_deletion_rehearsal_plan", schema)
        self.assertIn("fk_data_deletion_backup_verification_plan", schema)
        self.assertIn("evidence_record_ids_json", schema)
        self.assertIn("evidence_set_fingerprint_sha256", schema)
        self.assertIn("result_status ENUM('passed', 'blocked')", schema)
        self.assertIn("ON DELETE RESTRICT", schema)

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

        self.assertIn("pubg-alerts", DEFAULT_COMMAND_GROUPS["admin"])
        self.assertIn("pubg-alert-ack", DEFAULT_COMMAND_GROUPS["admin"])
        self.assertIn("pubg-alert-snooze", DEFAULT_COMMAND_GROUPS["admin"])
        self.assertIn("pubg-alert-note", DEFAULT_COMMAND_GROUPS["admin"])
        self.assertIn("pubg-alert-notes", DEFAULT_COMMAND_GROUPS["admin"])
        self.assertIn("pubg-alert-note-list", DEFAULT_COMMAND_GROUPS["admin"])
        self.assertIn("pubg-alert-resolution", DEFAULT_COMMAND_GROUPS["admin"])
        self.assertIn("pubg-alert-history", DEFAULT_COMMAND_GROUPS["admin"])
        self.assertIn("pubg-alert-log", DEFAULT_COMMAND_GROUPS["admin"])
        self.assertIn("pubg-worker-runs", DEFAULT_COMMAND_GROUPS["admin"])
        self.assertIn("pubg-worker-history", DEFAULT_COMMAND_GROUPS["admin"])
        self.assertIn("pubg-worker-log", DEFAULT_COMMAND_GROUPS["admin"])
        self.assertIn("pubg-worker-run", DEFAULT_COMMAND_GROUPS["admin"])
        self.assertIn("pubg-worker-run-detail", DEFAULT_COMMAND_GROUPS["admin"])
        self.assertIn("pubg-worker-detail", DEFAULT_COMMAND_GROUPS["admin"])
        self.assertIn("pubg-ranking-scope", DEFAULT_COMMAND_GROUPS["admin"])
        self.assertIn("pubg-guild-scope", DEFAULT_COMMAND_GROUPS["admin"])
        self.assertIn("pubg-delete-data", DEFAULT_COMMAND_GROUPS["admin"])
        self.assertIn("pubg-delete-cancel", DEFAULT_COMMAND_GROUPS["admin"])


if __name__ == "__main__":
    unittest.main()
