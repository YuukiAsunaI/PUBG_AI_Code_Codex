from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from unittest.mock import MagicMock
import os
import unittest

from fastapi.testclient import TestClient

from pubg_ai.config import load_dotenv_values
from pubg_ai.web.app import create_app


class WebDiscordBotTests(unittest.TestCase):
    def test_secret_endpoints_are_write_only(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            secret = "private-discord-token"
            with patch.dict(os.environ, {"DISCORD_BOT_TOKEN": ""}):
                client = TestClient(create_app(base_dir=base_dir))
                response = client.post(
                    "/settings/secrets/discord",
                    json={"value": secret},
                )

            self.assertEqual(response.status_code, 200)
            body = response.text
            self.assertNotIn(secret, body)
            self.assertTrue(response.json()["configured"])
            self.assertNotIn("length", response.json()["settings"]["secrets"]["DISCORD_BOT_TOKEN"])
            self.assertEqual(load_dotenv_values(base_dir / ".env")["DISCORD_BOT_TOKEN"], secret)

    def test_bot_settings_endpoint_canonicalizes_commands(self) -> None:
        with TemporaryDirectory() as temp_dir:
            client = TestClient(create_app(base_dir=Path(temp_dir)))

            response = client.post(
                "/discord/bot/settings",
                json={
                    "auto_start": True,
                    "command_prefix": "?",
                    "guild_enabled_commands": {"100": ["pubg-stats", "추천"]},
                },
            )
            loaded = client.get("/discord/bot/settings")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                loaded.json()["discord_bot"]["guild_enabled_commands"]["100"],
                ["전적", "추천"],
            )
            self.assertEqual(loaded.json()["discord_bot"]["command_prefix"], "?")

    def test_bot_start_requires_configured_token(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"DISCORD_BOT_TOKEN": ""}):
                client = TestClient(create_app(base_dir=Path(temp_dir)))
                response = client.post("/discord/bot/start", json={})

            self.assertEqual(response.status_code, 400)
            self.assertIn("토큰", response.json()["detail"])

    def test_app_icon_endpoints_serve_binary_assets(self) -> None:
        client = TestClient(create_app())

        png = client.get("/assets/app-icon.png")
        ico = client.get("/favicon.ico")

        self.assertEqual(png.status_code, 200)
        self.assertEqual(png.headers["content-type"], "image/png")
        self.assertGreater(len(png.content), 1000)
        self.assertEqual(ico.status_code, 200)
        self.assertIn("image/x-icon", ico.headers["content-type"])

    @patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "test-token"}, clear=True)
    @patch("pubg_ai.web.app.sync_discord_guild_catalog")
    @patch("pubg_ai.web.app.list_stored_discord_guild_ids")
    @patch("pubg_ai.web.app.connect_mysql")
    @patch("pubg_ai.web.app.DiscordAcceptanceClient")
    def test_guild_sync_binds_one_managed_bot_and_requires_prune_confirmation(
        self,
        client_class: MagicMock,
        connect_mysql: MagicMock,
        list_stored_guild_ids: MagicMock,
        sync_catalog: MagicMock,
    ) -> None:
        client_class.return_value.current_bot.return_value = SimpleNamespace(
            user_id="42",
            username="PUBG Metrics",
        )
        client_class.return_value.list_guilds.return_value = [
            SimpleNamespace(
                guild_id="100",
                to_record=lambda: {"guild_id": "100", "guild_name": "Current"},
            )
        ]
        list_stored_guild_ids.return_value = ["100", "900"]
        sync_catalog.return_value = 1
        with TemporaryDirectory() as temp_dir:
            client = TestClient(create_app(base_dir=Path(temp_dir), env_file=".missing"))

            rejected = client.post(
                "/discord/guilds/sync",
                json={"prune_stale": True},
            )
            synced = client.post(
                "/discord/guilds/sync",
                json={
                    "prune_stale": True,
                    "confirmation": "CONFIRM MANAGED DISCORD GUILD SYNC",
                },
            )
            settings = client.get("/discord/bot/settings").json()["discord_bot"]
            index = client.get("/").text

        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(synced.status_code, 200)
        self.assertEqual(synced.json()["managed_bot"]["username"], "PUBG Metrics")
        self.assertEqual(synced.json()["removed_catalog_guild_ids"], ["900"])
        self.assertEqual(synced.json()["removed_guild_ids"], ["900"])
        self.assertEqual(settings["managed_bot_user_id"], "42")
        self.assertEqual(settings["managed_guild_ids"], ["100"])
        self.assertIn("앱 관리 Discord 봇", index)
        self.assertNotIn("전용 Discord 봇", index)
        sync_catalog.assert_called_once_with(
            connect_mysql.return_value,
            [{"guild_id": "100", "guild_name": "Current"}],
            prune_missing=True,
        )


if __name__ == "__main__":
    unittest.main()
