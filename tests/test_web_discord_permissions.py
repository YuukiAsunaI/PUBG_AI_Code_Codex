from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from pubg_ai.web.app import create_app


class WebDiscordPermissionTests(unittest.TestCase):
    def test_discord_permission_endpoints_update_local_settings(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings_file = Path(temp_dir) / "config" / "local_settings.json"
            with patch.dict(os.environ, {"PUBG_LOCAL_SETTINGS_FILE": str(settings_file)}):
                client = TestClient(create_app())

                index = client.get("/")
                self.assertEqual(index.status_code, 200)
                self.assertIn('id="discordCommandGroupForm"', index.text)
                self.assertIn('id="discordCommandAliasForm"', index.text)
                self.assertIn(
                    'ids: ["discord-permissions", "discord-command-groups"]',
                    index.text,
                )

                response = client.post(
                    "/discord/permissions/grant",
                    json={
                        "user_id": "user-1",
                        "group": "register",
                        "guild_id": "guild-1",
                        "member_label": "아스나",
                        "member_guild_id": "guild-1",
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()["changed"])

                response = client.get("/discord/permissions")
                self.assertEqual(response.status_code, 200)
                permission_payload = response.json()
                settings = permission_payload["discord_permissions"]
                self.assertEqual(settings["guild_user_grants"]["guild-1"]["user-1"], ["register"])
                self.assertEqual(settings["guild_member_labels"]["guild-1"]["user-1"], "아스나")
                self.assertIn("추천", [item["name"] for item in permission_payload["command_catalog"]])
                self.assertIn("profile_read", permission_payload["reserved_groups"])
                self.assertEqual(permission_payload["group_labels"]["profile_read"], "플레이어 분석 조회")

                response = client.put(
                    "/discord/permissions/groups/전투분석",
                    json={"commands": ["전적", "pubg-weapon"]},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    set(response.json()["settings"]["command_groups"]["전투분석"]),
                    {"전적", "무기"},
                )

                response = client.put(
                    "/discord/permissions/aliases/my-stats",
                    json={"target_command": "pubg-stats"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["settings"]["command_aliases"]["my-stats"], "전적")

                response = client.delete("/discord/permissions/aliases/my-stats")
                self.assertEqual(response.status_code, 200)
                response = client.delete("/discord/permissions/groups/전투분석")
                self.assertEqual(response.status_code, 200)

                response = client.post("/discord/global-admins/add", json={"user_id": "admin-1"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["settings"]["global_admin_user_ids"], ["admin-1"])

                response = client.post(
                    "/discord/permissions/revoke",
                    json={
                        "user_id": "user-1",
                        "group": "register",
                        "guild_id": "guild-1",
                    },
                )
                self.assertEqual(response.status_code, 200)

                response = client.get("/discord/permissions")
                settings = response.json()["discord_permissions"]
                self.assertNotIn("guild-1", settings["guild_user_grants"])
                self.assertEqual(settings["command_aliases"], {})


if __name__ == "__main__":
    unittest.main()
