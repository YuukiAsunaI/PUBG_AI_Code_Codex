from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from pubg_ai.local_settings import LocalSettingsError, LocalSettingsStore


class DiscordBotSettingsTests(unittest.TestCase):
    def test_settings_round_trip_canonicalizes_command_aliases(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings_file = Path(temp_dir) / "config" / "local_settings.json"
            store = LocalSettingsStore(settings_file)

            saved = store.save_discord_bot_settings(
                auto_start=True,
                command_prefix="?",
                guild_enabled_commands={"100": ["pubg-stats", "추천", "추천"]},
            )
            loaded = store.load_discord_bot_settings()

            self.assertTrue(saved.auto_start)
            self.assertEqual(loaded.command_prefix, "?")
            self.assertEqual(loaded.guild_enabled_commands["100"], ["전적", "추천"])
            payload = json.loads(settings_file.read_text(encoding="utf-8"))
            self.assertNotIn("token", json.dumps(payload).lower())

    def test_empty_command_list_intentionally_hides_all_commands(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = LocalSettingsStore(Path(temp_dir) / "local_settings.json")
            saved = store.save_discord_bot_settings(
                auto_start=False,
                command_prefix="!",
                guild_enabled_commands={"100": []},
            )

            self.assertEqual(saved.guild_enabled_commands, {"100": []})

    def test_rejects_invalid_prefix_guild_and_command(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = LocalSettingsStore(Path(temp_dir) / "local_settings.json")

            with self.assertRaises(LocalSettingsError):
                store.save_discord_bot_settings(
                    auto_start=False,
                    command_prefix="bad prefix",
                    guild_enabled_commands={},
                )
            with self.assertRaises(LocalSettingsError):
                store.save_discord_bot_settings(
                    auto_start=False,
                    command_prefix="!",
                    guild_enabled_commands={"guild": ["전적"]},
                )
            with self.assertRaises(LocalSettingsError):
                store.save_discord_bot_settings(
                    auto_start=False,
                    command_prefix="!",
                    guild_enabled_commands={"100": ["없는명령"]},
                )


if __name__ == "__main__":
    unittest.main()
