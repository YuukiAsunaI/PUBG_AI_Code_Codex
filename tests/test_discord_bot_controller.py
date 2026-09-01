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
        self.pubg_fetch_application_commands = self.fetch_commands

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

    async def fetch_commands(self, guild_ids):
        return {guild_id: ["전적", "추천"] for guild_id in guild_ids}


class _FailingBot(_FakeBot):
    async def start(self, token: str) -> None:
        raise RuntimeError(f"login rejected for {token}")


class _SlowClosingBot(_FakeBot):
    async def close(self) -> None:
        await asyncio.sleep(0.15)
        await super().close()


class _GatewayFirstClosingBot(_FakeBot):
    def __init__(self, status_callback) -> None:
        super().__init__(status_callback)
        self._closing_task: asyncio.Task[None] | None = None
        self.cleanup_finished = False

    async def close(self) -> None:
        if self._closing_task is None:
            self._closing_task = asyncio.create_task(self._finish_close())
        await self._closing_task

    async def _finish_close(self) -> None:
        self.closed = True
        if self.close_event is not None:
            self.close_event.set()
        await asyncio.sleep(0.05)
        self.cleanup_finished = True

    def is_closed(self) -> bool:
        return self._closing_task is not None


class _BackgroundTaskBot(_FakeBot):
    def __init__(self, status_callback) -> None:
        super().__init__(status_callback)
        self.background_cancelled = False

    async def start(self, token: str) -> None:
        asyncio.create_task(self._background_loop())
        await super().start(token)

    async def _background_loop(self) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            self.background_cancelled = True


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

    def test_fetches_remote_command_names_for_one_guild(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = LocalSettingsStore(Path(temp_dir) / "local_settings.json")
            controller = self._controller(store, _FakeBot)

            controller.start()
            self._wait_until(lambda: controller.status().ready)
            commands = controller.fetch_commands("100")
            controller.stop()

            self.assertEqual(commands, ["전적", "추천"])

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

    def test_shutdown_waits_for_cleanup_after_gateway_exits(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = LocalSettingsStore(Path(temp_dir) / "local_settings.json")
            created: list[_GatewayFirstClosingBot] = []
            controller = self._controller(
                store,
                _GatewayFirstClosingBot,
                created=created,
            )

            controller.start()
            self._wait_until(lambda: controller.status().ready)
            stopped = controller.stop()

            self.assertTrue(created[0].cleanup_finished)
            self.assertEqual(stopped.state, "stopped")
            self.assertIsNone(stopped.last_error)

    def test_shutdown_cancels_and_awaits_orphan_background_tasks(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = LocalSettingsStore(Path(temp_dir) / "local_settings.json")
            created: list[_BackgroundTaskBot] = []
            controller = self._controller(store, _BackgroundTaskBot, created=created)

            controller.start()
            self._wait_until(lambda: controller.status().ready)
            stopped = controller.stop()

            self.assertTrue(created[0].background_cancelled)
            self.assertEqual(stopped.state, "stopped")
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
    def _controller(
        store: LocalSettingsStore,
        bot_type,
        *,
        created: list | None = None,
    ) -> DiscordBotController:
        config = RuntimeConfig(
            app=AppConfig(raw_data_dir=Path("raw"), replay_data_dir=Path("replay")),
            database=DatabaseConfig(),
            secrets=SecretConfig(discord_bot_token="super-secret-token"),
        )
        checker = DiscordPermissionChecker(store.load_discord_permission_settings())
        def bot_factory(**kwargs):
            bot = bot_type(kwargs["status_callback"])
            if created is not None:
                created.append(bot)
            return bot

        return DiscordBotController(
            config_loader=lambda: config,
            settings_store=store,
            permission_checker=checker,
            bot_factory=bot_factory,
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
