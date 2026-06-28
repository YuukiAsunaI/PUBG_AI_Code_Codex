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

                response = client.post(
                    "/discord/permissions/grant",
                    json={
                        "user_id": "user-1",
                        "group": "register",
                        "guild_id": "guild-1",
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()["changed"])

                response = client.get("/discord/permissions")
                self.assertEqual(response.status_code, 200)
                settings = response.json()["discord_permissions"]
                self.assertEqual(settings["guild_user_grants"]["guild-1"]["user-1"], ["register"])

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


if __name__ == "__main__":
    unittest.main()
