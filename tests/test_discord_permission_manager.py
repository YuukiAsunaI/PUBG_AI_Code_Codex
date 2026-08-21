from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pubg_ai.discord_permission_manager import DiscordPermissionManager
from pubg_ai.local_settings import LocalSettingsError, LocalSettingsStore


class DiscordPermissionManagerTests(unittest.TestCase):
    def test_grant_and_revoke_global_user_permission(self) -> None:
        with TemporaryDirectory() as temp_dir:
            manager = _manager(Path(temp_dir))

            granted = manager.grant(user_id="user-1", group="profile_read")
            loaded = manager.load()

            self.assertTrue(granted.changed)
            self.assertEqual(loaded.user_grants["user-1"], ["profile_read"])

            revoked = manager.revoke(user_id="user-1", group="profile_read")
            loaded = manager.load()

            self.assertTrue(revoked.changed)
            self.assertNotIn("user-1", loaded.user_grants)

    def test_grant_and_revoke_guild_user_permission(self) -> None:
        with TemporaryDirectory() as temp_dir:
            manager = _manager(Path(temp_dir))

            manager.grant(user_id="user-1", group="register", guild_id="guild-1")
            loaded = manager.load()

            self.assertEqual(loaded.guild_user_grants["guild-1"]["user-1"], ["register"])

            manager.revoke(user_id="user-1", group="register", guild_id="guild-1")
            loaded = manager.load()

            self.assertNotIn("guild-1", loaded.guild_user_grants)

    def test_global_admin_add_and_remove(self) -> None:
        with TemporaryDirectory() as temp_dir:
            manager = _manager(Path(temp_dir))

            added = manager.add_global_admin("admin-1")
            added_again = manager.add_global_admin("admin-1")

            self.assertTrue(added.changed)
            self.assertFalse(added_again.changed)
            self.assertEqual(manager.load().global_admin_user_ids, ["admin-1"])

            removed = manager.remove_global_admin("admin-1")

            self.assertTrue(removed.changed)
            self.assertEqual(manager.load().global_admin_user_ids, [])

    def test_unknown_permission_group_is_rejected(self) -> None:
        with TemporaryDirectory() as temp_dir:
            manager = _manager(Path(temp_dir))

            with self.assertRaises(LocalSettingsError):
                manager.grant(user_id="user-1", group="unknown")

    def test_custom_group_controls_selected_commands_and_cannot_delete_while_assigned(self) -> None:
        with TemporaryDirectory() as temp_dir:
            manager = _manager(Path(temp_dir))

            created = manager.upsert_command_group(
                group="combat_reader",
                commands=["전적", "pubg-weapon"],
            )
            manager.grant(user_id="user-1", group="combat_reader")

            self.assertTrue(created.changed)
            self.assertEqual(
                set(manager.load().command_groups["combat_reader"]),
                {"전적", "무기"},
            )
            with self.assertRaisesRegex(LocalSettingsError, "still assigned"):
                manager.delete_command_group("combat_reader")

            manager.revoke(user_id="user-1", group="combat_reader")
            deleted = manager.delete_command_group("combat_reader")

            self.assertTrue(deleted.changed)
            self.assertNotIn("combat_reader", manager.load().command_groups)

    def test_built_in_group_is_read_only(self) -> None:
        with TemporaryDirectory() as temp_dir:
            manager = _manager(Path(temp_dir))

            with self.assertRaisesRegex(LocalSettingsError, "read-only"):
                manager.upsert_command_group(group="profile_read", commands=["전적"])

    def test_custom_prefix_alias_targets_canonical_command(self) -> None:
        with TemporaryDirectory() as temp_dir:
            manager = _manager(Path(temp_dir))

            created = manager.set_command_alias(alias="내전적", target_command="pubg-stats")

            self.assertTrue(created.changed)
            self.assertEqual(manager.load().command_aliases, {"내전적": "전적"})
            with self.assertRaisesRegex(LocalSettingsError, "conflicts"):
                manager.set_command_alias(alias="전적", target_command="전적")

            removed = manager.remove_command_alias("내전적")
            self.assertTrue(removed.changed)
            self.assertEqual(manager.load().command_aliases, {})


def _manager(base_dir: Path) -> DiscordPermissionManager:
    return DiscordPermissionManager(
        LocalSettingsStore(base_dir / "config" / "local_settings.json", base_dir=base_dir)
    )


if __name__ == "__main__":
    unittest.main()
