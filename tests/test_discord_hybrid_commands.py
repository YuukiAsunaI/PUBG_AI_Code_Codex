from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock
import unittest

from discord.ext.commands import HybridCommand

from pubg_ai.config import AppConfig, DatabaseConfig, RuntimeConfig, SecretConfig
from pubg_ai.discord_bot import create_discord_bot
from pubg_ai.discord_command_catalog import DISCORD_COMMAND_SPECS
from pubg_ai.discord_permissions import DiscordPermissionChecker
from pubg_ai.local_settings import DEFAULT_COMMAND_GROUPS, DiscordPermissionSettings, LocalSettingsStore


class DiscordHybridCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_core_commands_are_registered_for_prefix_and_slash_use(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            store = LocalSettingsStore(base_dir / "config" / "local_settings.json", base_dir=base_dir)
            checker = DiscordPermissionChecker(
                DiscordPermissionSettings(
                    command_groups=DEFAULT_COMMAND_GROUPS,
                    user_grants={},
                    guild_user_grants={},
                    global_admin_user_ids=[],
                )
            )
            config = RuntimeConfig(
                app=AppConfig(raw_data_dir=base_dir / "raw", replay_data_dir=base_dir / "replay"),
                database=DatabaseConfig(),
                secrets=SecretConfig(discord_bot_token="test-token"),
            )
            bot = create_discord_bot(
                config=config,
                permission_checker=checker,
                scope_settings_store=store,
            )
            try:
                for spec in DISCORD_COMMAND_SPECS:
                    command = bot.get_command(spec.name)
                    self.assertIsInstance(command, HybridCommand, spec.name)
                    for alias in spec.aliases:
                        self.assertIs(bot.get_command(alias), command, alias)
                slash_names = {command.name for command in bot.tree.get_commands()}
                self.assertTrue(
                    {spec.name for spec in DISCORD_COMMAND_SPECS}.issubset(slash_names)
                )
            finally:
                await bot.close()

    async def test_application_commands_are_filtered_per_guild(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            store = LocalSettingsStore(base_dir / "local_settings.json", base_dir=base_dir)
            store.save_discord_bot_settings(
                auto_start=False,
                command_prefix="!",
                guild_enabled_commands={"100": ["전적", "추천"]},
            )
            checker = DiscordPermissionChecker(store.load_discord_permission_settings())
            config = RuntimeConfig(
                app=AppConfig(raw_data_dir=base_dir / "raw", replay_data_dir=base_dir / "replay"),
                database=DatabaseConfig(),
                secrets=SecretConfig(discord_bot_token="test-token"),
            )
            bot = create_discord_bot(
                config=config,
                permission_checker=checker,
                scope_settings_store=store,
            )
            guild = SimpleNamespace(id=100, name="Test Guild")
            bot._connection._guilds = {100: guild}

            async def fake_sync(*, guild=None):
                return list(bot.tree.get_commands(guild=guild))

            bot.tree.sync = AsyncMock(side_effect=fake_sync)
            try:
                counts = await bot.pubg_sync_application_commands()
                guild_commands = {
                    command.name for command in bot.tree.get_commands(guild=guild)
                }

                self.assertEqual(guild_commands, {"전적", "추천"})
                self.assertEqual(counts, {"100": 2})
                self.assertEqual(bot.tree.get_commands(), [])
            finally:
                await bot.close()


if __name__ == "__main__":
    unittest.main()
