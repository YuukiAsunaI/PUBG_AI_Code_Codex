from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import os
import unittest

from pubg_ai.config import AppConfig, DatabaseConfig, SecretConfig


class ExplicitEnvironmentTests(unittest.TestCase):
    def test_explicit_empty_mapping_ignores_process_environment(self) -> None:
        process_values = {
            "PUBG_RAW_DATA_DIR": r"Z:\process-raw",
            "PUBG_LOCAL_SETTINGS_FILE": r"Z:\process-settings.json",
            "PUBG_API_KEY": "process-api-key",
            "DISCORD_BOT_TOKEN": "process-discord-token",
            "MYSQL_HOST": "process-db.example",
            "MYSQL_PASSWORD": "process-password",
        }

        with TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            process_values,
            clear=True,
        ):
            base_dir = Path(temp_dir)
            app = AppConfig.from_env({}, base_dir=base_dir)
            app_from_sources = AppConfig.from_sources({}, base_dir=base_dir)
            secrets = SecretConfig.from_env({})
            database = DatabaseConfig.from_env({})

        self.assertEqual(app.raw_data_dir, base_dir / "data" / "raw")
        self.assertEqual(app_from_sources.raw_data_dir, base_dir / "data" / "raw")
        self.assertIsNone(secrets.pubg_api_key)
        self.assertIsNone(secrets.discord_bot_token)
        self.assertEqual(database.host, "127.0.0.1")
        self.assertEqual(database.password, "")


if __name__ == "__main__":
    unittest.main()
