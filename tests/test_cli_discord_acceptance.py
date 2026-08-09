from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import os
import unittest
from unittest.mock import MagicMock, patch

from pubg_ai.cli import main


class DiscordAcceptanceCliTests(unittest.TestCase):
    @patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "test-token"})
    @patch("pubg_ai.cli.DiscordAcceptanceClient")
    def test_probe_command_prints_safe_report(self, client_class: MagicMock) -> None:
        report = client_class.return_value.probe.return_value
        report.to_record.return_value = {"guild_count": 1}
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(
                [
                    "discord-acceptance-probe",
                    "--guild-id",
                    "100",
                    "--channel-id",
                    "300",
                ]
            )

        self.assertEqual(exit_code, 0)
        client_class.return_value.probe.assert_called_once_with(
            guild_id="100",
            channel_id="300",
            channel_limit=8,
        )
        self.assertIn('"guild_count": 1', output.getvalue())
        self.assertNotIn("test-token", output.getvalue())

    @patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "test-token"})
    @patch("pubg_ai.cli.DiscordAcceptanceClient")
    def test_send_alert_forwards_explicit_confirmation(self, client_class: MagicMock) -> None:
        delivery = client_class.return_value.send_controlled_alert.return_value
        delivery.to_record.return_value = {"verified": True, "message_id": "500"}
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(
                [
                    "discord-acceptance-send-alert",
                    "--guild-id",
                    "100",
                    "--channel-id",
                    "300",
                    "--confirm",
                    "SEND_DISCORD_ACCEPTANCE_TEST",
                ]
            )

        self.assertEqual(exit_code, 0)
        client_class.return_value.send_controlled_alert.assert_called_once_with(
            guild_id="100",
            channel_id="300",
            confirmation="SEND_DISCORD_ACCEPTANCE_TEST",
        )
        self.assertIn('"verified": true', output.getvalue())

    @patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "test-token"})
    @patch("pubg_ai.cli.DiscordAcceptanceClient")
    def test_observe_returns_two_until_round_trip_is_verified(self, client_class: MagicMock) -> None:
        report = client_class.return_value.observe_round_trip.return_value
        report.verified = False
        report.to_record.return_value = {"verified": False, "round_trips": []}
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(
                [
                    "discord-acceptance-observe",
                    "--guild-id",
                    "100",
                    "--channel-id",
                    "300",
                    "--after-message-id",
                    "400",
                ]
            )

        self.assertEqual(exit_code, 2)
        client_class.return_value.observe_round_trip.assert_called_once_with(
            guild_id="100",
            channel_id="300",
            after_message_id="400",
            expected_command="배그도움말",
            command_prefix="!",
            limit=50,
        )
        self.assertIn('"verified": false', output.getvalue())


if __name__ == "__main__":
    unittest.main()
