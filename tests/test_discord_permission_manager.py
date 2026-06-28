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


def _manager(base_dir: Path) -> DiscordPermissionManager:
    return DiscordPermissionManager(
        LocalSettingsStore(base_dir / "config" / "local_settings.json", base_dir=base_dir)
    )


if __name__ == "__main__":
    unittest.main()
