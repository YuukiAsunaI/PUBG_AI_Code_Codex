from __future__ import annotations

import unittest

from pubg_ai.discord_permissions import DiscordCommandIdentity, DiscordPermissionChecker
from pubg_ai.local_settings import DEFAULT_COMMAND_GROUPS, DiscordPermissionSettings


class DiscordPermissionCheckerTests(unittest.TestCase):
    def test_global_admin_can_use_every_command_group(self) -> None:
        checker = DiscordPermissionChecker(
            DiscordPermissionSettings(
                command_groups=DEFAULT_COMMAND_GROUPS,
                user_grants={},
                guild_user_grants={},
                global_admin_user_ids=["admin-1"],
            )
        )

        self.assertTrue(
            checker.is_global_admin(DiscordCommandIdentity(user_id="admin-1"))
        )
        self.assertTrue(
            checker.is_allowed(DiscordCommandIdentity(user_id="admin-1"), "admin")
        )
        self.assertTrue(
            checker.is_allowed(DiscordCommandIdentity(user_id="admin-1"), "register")
        )

    def test_global_user_grant_works_without_guild(self) -> None:
        checker = DiscordPermissionChecker(
            DiscordPermissionSettings(
                command_groups=DEFAULT_COMMAND_GROUPS,
                user_grants={"user-1": ["profile_read"]},
                guild_user_grants={},
                global_admin_user_ids=[],
            )
        )

        self.assertTrue(
            checker.is_allowed(DiscordCommandIdentity(user_id="user-1"), "profile_read")
        )
        self.assertTrue(
            checker.is_globally_allowed(DiscordCommandIdentity(user_id="user-1"), "profile_read")
        )
        self.assertFalse(
            checker.is_allowed(DiscordCommandIdentity(user_id="user-1"), "register")
        )

    def test_guild_user_grant_is_limited_to_matching_guild(self) -> None:
        checker = DiscordPermissionChecker(
            DiscordPermissionSettings(
                command_groups=DEFAULT_COMMAND_GROUPS,
                user_grants={},
                guild_user_grants={"guild-1": {"user-1": ["register"]}},
                global_admin_user_ids=[],
            )
        )

        self.assertTrue(
            checker.is_allowed(
                DiscordCommandIdentity(user_id="user-1", guild_id="guild-1"),
                "register",
            )
        )
        self.assertFalse(
            checker.is_globally_allowed(
                DiscordCommandIdentity(user_id="user-1", guild_id="guild-1"),
                "register",
            )
        )
        self.assertFalse(
            checker.is_allowed(
                DiscordCommandIdentity(user_id="user-1", guild_id="guild-2"),
                "register",
            )
        )

    def test_ungranted_user_is_denied(self) -> None:
        checker = DiscordPermissionChecker(
            DiscordPermissionSettings(
                command_groups=DEFAULT_COMMAND_GROUPS,
                user_grants={},
                guild_user_grants={},
                global_admin_user_ids=[],
            )
        )

        self.assertFalse(
            checker.is_allowed(DiscordCommandIdentity(user_id="user-1"), "profile_read")
        )


if __name__ == "__main__":
    unittest.main()
