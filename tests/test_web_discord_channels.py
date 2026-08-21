from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from pubg_ai.web.app import create_app


class WebDiscordChannelTests(unittest.TestCase):
    @patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "test-token"}, clear=True)
    @patch("pubg_ai.web.app.DiscordAcceptanceClient")
    def test_channel_endpoint_only_returns_safe_probe_records(
        self,
        client_class: MagicMock,
    ) -> None:
        channel = SimpleNamespace(
            to_record=lambda: {
                "guild_id": "100",
                "guild_name": "Test Guild",
                "channel_id": "300",
                "channel_name": "pubg-alerts",
                "can_view": True,
                "can_send": True,
                "can_read_history": True,
                "last_message_id": None,
                "position": 1,
                "candidate_score": 20,
            }
        )
        client_class.return_value.probe.return_value = SimpleNamespace(
            guilds=[
                SimpleNamespace(
                    guild_id="100",
                    guild_name="Test Guild",
                    eligible_channel_count=1,
                    channels=[channel],
                )
            ]
        )

        with TemporaryDirectory() as temp_dir:
            client = TestClient(create_app(base_dir=Path(temp_dir), env_file=".missing"))
            response = client.get("/discord/channels?guild_id=100&limit=50")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["channels"][0]["channel_id"], "300")
        self.assertNotIn("test-token", response.text)
        client_class.assert_called_once_with("test-token")
        client_class.return_value.probe.assert_called_once_with(
            guild_id="100",
            channel_limit=50,
        )

    @patch.dict(os.environ, {}, clear=True)
    def test_channel_endpoint_requires_configured_token(self) -> None:
        with TemporaryDirectory() as temp_dir:
            client = TestClient(create_app(base_dir=Path(temp_dir), env_file=".missing"))
            response = client.get("/discord/channels?guild_id=100")

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("token", response.text.casefold().replace("discord_bot_token", ""))


if __name__ == "__main__":
    unittest.main()
