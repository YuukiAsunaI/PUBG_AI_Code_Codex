from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from pubg_ai.config import AppConfig, DatabaseConfig, RuntimeConfig, SecretConfig
from pubg_ai.discord_bot_controller import DiscordBotController
from pubg_ai.discord_permissions import DiscordPermissionChecker
from pubg_ai.local_settings import LocalSettingsStore


class _FakeBot:
    def __init__(self, status_callback) -> None:
        self.status_callback = status_callback
        self.closed = False
        self.close_event: asyncio.Event | None = None
        self.pubg_sync_application_commands = self.sync_commands

    async def start(self, _token: str) -> None:
        self.close_event = asyncio.Event()
        self.status_callback("ready", {"bot_user": "PUBG Test", "guild_count": 2})
        await self.close_event.wait()

    async def close(self) -> None:
        self.closed = True
        if self.close_event is not None:
            self.close_event.set()

    def is_closed(self) -> bool:
        return self.closed

    async def sync_commands(self, guild_ids=None):
        ids = guild_ids or ["100", "200"]
        return {guild_id: 3 for guild_id in ids}


class _FailingBot(_FakeBot):
    async def start(self, token: str) -> None:
        raise RuntimeError(f"login rejected for {token}")


class _SlowClosingBot(_FakeBot):
    async def close(self) -> None:
        await asyncio.sleep(0.15)
        await super().close()


class DiscordBotControllerTests(unittest.TestCase):
    def test_start_sync_and_stop(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = LocalSettingsStore(Path(temp_dir) / "local_settings.json")
            store.save_discord_bot_settings(
                auto_start=False,
                command_prefix="?",
                guild_enabled_commands={},
            )
            controller = self._controller(store, _FakeBot)

            controller.start()
            self._wait_until(lambda: controller.status().ready)
            synced = controller.sync_commands("100")
            stopped = controller.stop()

            self.assertEqual(synced.last_sync, {"100": 3})
            self.assertEqual(stopped.state, "stopped")
            self.assertFalse(stopped.running)
            self.assertEqual(stopped.command_prefix, "?")
            self.assertIsNone(stopped.last_error)

    def test_completed_shutdown_does_not_keep_intermediate_close_timeout(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = LocalSettingsStore(Path(temp_dir) / "local_settings.json")
            controller = self._controller(store, _SlowClosingBot)

            controller.start()
            self._wait_until(lambda: controller.status().ready)
            stopped = controller.stop(timeout_seconds=0.4)

            self.assertEqual(stopped.state, "stopped")
            self.assertFalse(stopped.running)
            self.assertIsNone(stopped.last_error)

    def test_runtime_error_redacts_token(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = LocalSettingsStore(Path(temp_dir) / "local_settings.json")
            controller = self._controller(store, _FailingBot)

            controller.start()
            self._wait_until(lambda: not controller.status().running)
            state = controller.status()

            self.assertEqual(state.state, "error")
            self.assertIn("[redacted]", state.last_error or "")
            self.assertNotIn("super-secret-token", state.last_error or "")

    @staticmethod
    def _controller(store: LocalSettingsStore, bot_type) -> DiscordBotController:
        config = RuntimeConfig(
            app=AppConfig(raw_data_dir=Path("raw"), replay_data_dir=Path("replay")),
            database=DatabaseConfig(),
            secrets=SecretConfig(discord_bot_token="super-secret-token"),
        )
        checker = DiscordPermissionChecker(store.load_discord_permission_settings())
        return DiscordBotController(
            config_loader=lambda: config,
            settings_store=store,
            permission_checker=checker,
            bot_factory=lambda **kwargs: bot_type(kwargs["status_callback"]),
        )

    def _wait_until(self, predicate) -> None:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail("timed out waiting for Discord bot controller state")


if __name__ == "__main__":
    unittest.main()
