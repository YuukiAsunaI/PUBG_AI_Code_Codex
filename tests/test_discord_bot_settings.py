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

    def test_single_guild_command_update_preserves_other_guilds(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = LocalSettingsStore(Path(temp_dir) / "local_settings.json")
            store.save_discord_bot_settings(
                auto_start=True,
                command_prefix="?",
                guild_enabled_commands={"100": ["전적"], "200": ["추천"]},
            )

            updated = store.save_discord_guild_commands(
                guild_id="100",
                commands=["무기", "전적"],
            )
            restored_default = store.save_discord_guild_commands(
                guild_id="100",
                commands=None,
            )

            self.assertEqual(updated.guild_enabled_commands["100"], ["무기", "전적"])
            self.assertEqual(updated.guild_enabled_commands["200"], ["추천"])
            self.assertNotIn("100", restored_default.guild_enabled_commands)
            self.assertEqual(restored_default.guild_enabled_commands["200"], ["추천"])
            self.assertTrue(restored_default.auto_start)
            self.assertEqual(restored_default.command_prefix, "?")

    def test_multi_guild_ranking_scope_round_trip_and_prune(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = LocalSettingsStore(Path(temp_dir) / "local_settings.json")
            saved = store.save_discord_scope_settings(
                guild_ranking_scopes={"100": "guild"},
                guild_ranking_selected_guild_ids={"100": ["300", "200", "200"]},
            )

            self.assertEqual(
                saved.guild_ranking_selected_guild_ids,
                {"100": ["200", "300"]},
            )
            loaded = store.load_discord_scope_settings()
            self.assertEqual(loaded.guild_ranking_selected_guild_ids, {"100": ["200", "300"]})

            store.reconcile_managed_discord_bot(
                bot_user_id="42",
                bot_username="PUBG Metrics",
                guild_ids=["100", "200"],
            )
            pruned = store.reconcile_managed_discord_bot(
                bot_user_id="42",
                bot_username="PUBG Metrics",
                guild_ids=["100", "200"],
                prune_stale=True,
            )
            self.assertEqual(pruned.scopes.guild_ranking_selected_guild_ids, {"100": ["200"]})

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

    def test_managed_bot_reconciliation_prunes_only_explicit_guild_state(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = LocalSettingsStore(Path(temp_dir) / "local_settings.json")
            first = store.reconcile_managed_discord_bot(
                bot_user_id="42",
                bot_username="PUBG Metrics",
                guild_ids=["100", "200"],
            )
            store.save_discord_bot_settings(
                auto_start=True,
                command_prefix="!",
                guild_enabled_commands={"100": ["전적"], "900": ["추천"]},
            )
            permissions = store.load_discord_permission_settings()
            store.save_discord_permission_settings(
                command_groups=permissions.command_groups,
                user_grants={"global-user": ["profile_read"]},
                guild_user_grants={
                    "100": {"current-user": ["profile_read"]},
                    "900": {"legacy-user": ["profile_read"]},
                },
                global_admin_user_ids=["global-admin"],
            )
            store.save_discord_scope_settings(
                guild_ranking_scopes={"100": "guild", "900": "global"},
            )

            preview = store.reconcile_managed_discord_bot(
                bot_user_id="42",
                bot_username="PUBG Metrics",
                guild_ids=["100", "200"],
            )
            pruned = store.reconcile_managed_discord_bot(
                bot_user_id="42",
                bot_username="PUBG Metrics",
                guild_ids=["100", "200"],
                prune_stale=True,
            )

            self.assertTrue(first.first_binding)
            self.assertFalse(preview.identity_changed)
            self.assertEqual(preview.removed_guild_ids, ["900"])
            self.assertIn("900", preview.bot.guild_enabled_commands)
            self.assertNotIn("900", pruned.bot.guild_enabled_commands)
            self.assertNotIn("900", pruned.permissions.guild_user_grants)
            self.assertNotIn("900", pruned.scopes.guild_ranking_scopes)
            self.assertEqual(pruned.permissions.user_grants["global-user"], ["profile_read"])
            self.assertEqual(pruned.permissions.global_admin_user_ids, ["global-admin"])
            self.assertEqual(pruned.bot.managed_bot_user_id, "42")
            self.assertEqual(pruned.bot.managed_guild_ids, ["100", "200"])

    def test_loading_replaces_obsolete_built_in_command_entries(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = LocalSettingsStore(Path(temp_dir) / "local_settings.json")
            permissions = store.load_discord_permission_settings()
            stale_groups = dict(permissions.command_groups)
            stale_groups["profile_read"] = stale_groups["profile_read"] + ["pubg-recent"]
            store.save_discord_permission_settings(
                command_groups=stale_groups,
                user_grants={},
            )

            loaded = store.load_discord_permission_settings()

            self.assertNotIn("pubg-recent", loaded.command_groups["profile_read"])
            self.assertIn("추천", loaded.command_groups["profile_read"])

    def test_loading_preserves_granted_custom_group_after_legacy_commands_disappear(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = LocalSettingsStore(Path(temp_dir) / "local_settings.json")
            permissions = store.load_discord_permission_settings()
            stale_groups = dict(permissions.command_groups)
            stale_groups["legacy_custom"] = ["pubg-recent"]
            store.save_discord_permission_settings(
                command_groups=stale_groups,
                user_grants={"user-1": ["legacy_custom"]},
            )

            loaded = store.load_discord_permission_settings()

            self.assertEqual(loaded.command_groups["legacy_custom"], [])
            self.assertEqual(loaded.user_grants["user-1"], ["legacy_custom"])


if __name__ == "__main__":
    unittest.main()
