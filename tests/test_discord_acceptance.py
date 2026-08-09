from __future__ import annotations

from datetime import datetime
import unittest

from pubg_ai.discord_acceptance import (
    DISCORD_ACCEPTANCE_CONFIRMATION,
    DiscordAcceptanceClient,
    DiscordAcceptanceError,
    READ_MESSAGE_HISTORY,
    SEND_MESSAGES,
    VIEW_CHANNEL,
)
from pubg_ai.time_utils import KST


class FakeResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        return self._payload


class FakeDiscordTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.sent_content = ""
        self.messages: list[dict[str, object]] = []

    def __call__(self, method: str, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        path = url.removeprefix("https://discord.test/api/v10")
        if method == "GET" and path == "/users/@me":
            return FakeResponse(200, {"id": "999", "username": "test-bot", "bot": True})
        if method == "GET" and path == "/users/@me/guilds":
            return FakeResponse(200, [{"id": "100", "name": "test guild"}])
        if method == "GET" and path == "/guilds/100/members/999":
            return FakeResponse(200, {"roles": ["200"]})
        if method == "GET" and path == "/guilds/100/roles":
            return FakeResponse(
                200,
                [
                    {"id": "100", "permissions": str(VIEW_CHANNEL | READ_MESSAGE_HISTORY)},
                    {"id": "200", "permissions": str(SEND_MESSAGES)},
                ],
            )
        if method == "GET" and path == "/guilds/100/channels":
            return FakeResponse(
                200,
                [
                    {
                        "id": "300",
                        "name": "pubg-bot-test",
                        "type": 0,
                        "position": 2,
                        "last_message_id": "400",
                        "permission_overwrites": [],
                    },
                    {
                        "id": "301",
                        "name": "hidden",
                        "type": 0,
                        "position": 1,
                        "permission_overwrites": [
                            {"id": "100", "type": 0, "allow": "0", "deny": str(VIEW_CHANNEL)}
                        ],
                    },
                ],
            )
        if method == "POST" and path == "/channels/300/messages":
            body = kwargs.get("json")
            assert isinstance(body, dict)
            self.sent_content = str(body["content"])
            return FakeResponse(200, {"id": "500", "author": {"id": "999"}})
        if method == "GET" and path == "/channels/300/messages/500":
            return FakeResponse(200, {"id": "500", "author": {"id": "999"}})
        if method == "GET" and path == "/channels/300/messages":
            return FakeResponse(200, self.messages)
        return FakeResponse(404, {"message": "not found"})


class DiscordAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.transport = FakeDiscordTransport()
        self.client = DiscordAcceptanceClient(
            "secret-token",
            base_url="https://discord.test/api/v10",
            request=self.transport,
            now=lambda: datetime(2026, 8, 10, 12, 30, tzinfo=KST),
            test_id_factory=lambda: "test-123",
        )

    def test_probe_lists_only_viewable_sendable_text_channels(self) -> None:
        report = self.client.probe(guild_id="100")

        self.assertEqual(report.bot.user_id, "999")
        self.assertEqual(report.guild_count, 1)
        self.assertEqual(len(report.guilds), 1)
        self.assertEqual(report.guilds[0].eligible_channel_count, 1)
        channel = report.guilds[0].channels[0]
        self.assertEqual(channel.channel_id, "300")
        self.assertEqual(channel.last_message_id, "400")
        self.assertTrue(channel.can_read_history)
        self.assertNotIn("secret-token", str(report.to_record()))

    def test_probe_rejects_channel_from_another_guild(self) -> None:
        with self.assertRaisesRegex(DiscordAcceptanceError, "does not belong"):
            self.client.probe(guild_id="100", channel_id="999999")

    def test_controlled_alert_requires_exact_confirmation_before_network(self) -> None:
        with self.assertRaisesRegex(DiscordAcceptanceError, "confirmation must exactly equal"):
            self.client.send_controlled_alert(
                guild_id="100",
                channel_id="300",
                confirmation="yes",
            )
        self.assertEqual(self.transport.calls, [])

    def test_controlled_alert_sends_fixed_secret_free_message_and_verifies_it(self) -> None:
        result = self.client.send_controlled_alert(
            guild_id="100",
            channel_id="300",
            confirmation=DISCORD_ACCEPTANCE_CONFIRMATION,
        )

        self.assertTrue(result.verified)
        self.assertEqual(result.message_id, "500")
        self.assertEqual(result.test_id, "test-123")
        self.assertIn("Controlled alert delivery only", self.transport.sent_content)
        self.assertNotIn("secret-token", self.transport.sent_content)
        post_call = next(call for call in self.transport.calls if call["method"] == "POST")
        self.assertEqual(post_call["json"]["allowed_mentions"], {"parse": []})

    def test_observe_pairs_human_command_with_bot_reply_without_returning_content(self) -> None:
        self.transport.messages = [
            {
                "id": "402",
                "content": "PUBG AI 명령어\nsecret-looking reply text",
                "timestamp": "2026-08-10T03:31:01+00:00",
                "author": {"id": "999", "bot": True},
                "message_reference": {"message_id": "401"},
            },
            {
                "id": "401",
                "content": "!배그도움말",
                "timestamp": "2026-08-10T03:31:00+00:00",
                "author": {"id": "777", "bot": False},
            },
        ]

        report = self.client.observe_round_trip(
            guild_id="100",
            channel_id="300",
            after_message_id="400",
        )

        self.assertTrue(report.verified)
        self.assertEqual(report.messages_examined, 2)
        self.assertEqual(report.round_trips[0].command_author_id, "777")
        self.assertEqual(report.round_trips[0].command_name, "배그도움말")
        self.assertEqual(report.round_trips[0].reply_length, 37)
        self.assertNotIn("secret-looking", str(report.to_record()))

    def test_observe_requires_a_numeric_baseline(self) -> None:
        with self.assertRaisesRegex(DiscordAcceptanceError, "after_message_id"):
            self.client.observe_round_trip(
                guild_id="100",
                channel_id="300",
                after_message_id="",
            )

    def test_api_error_is_sanitized_and_never_contains_token(self) -> None:
        client = DiscordAcceptanceClient(
            "secret-token",
            base_url="https://discord.test/api/v10",
            request=lambda *args, **kwargs: FakeResponse(401, {"message": "bad token"}),
        )

        with self.assertRaises(DiscordAcceptanceError) as context:
            client.current_bot()
        self.assertEqual(context.exception.status_code, 401)
        self.assertNotIn("secret-token", str(context.exception))


if __name__ == "__main__":
    unittest.main()
