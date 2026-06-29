from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import os
import unittest

from fastapi.testclient import TestClient

from pubg_ai.web.app import create_app


class WebSettingsTests(unittest.TestCase):
    def test_web_settings_endpoint_updates_local_settings_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings_file = Path(temp_dir) / "config" / "local_settings.json"
            with patch.dict(os.environ, {"PUBG_LOCAL_SETTINGS_FILE": str(settings_file)}):
                client = TestClient(create_app())
                response = client.post(
                    "/settings/web",
                    json={"local_web_base_url": "http://127.0.0.1:8000/"},
                )
                status = client.get("/settings/status")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["web"]["local_web_base_url"], "http://127.0.0.1:8000")
            self.assertEqual(status.json()["local_web_base_url"], "http://127.0.0.1:8000")

            payload = json.loads(settings_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["web"]["local_web_base_url"], "http://127.0.0.1:8000")
            self.assertNotIn("PUBG_API_KEY", str(payload))
            self.assertNotIn("DISCORD_BOT_TOKEN", str(payload))

    def test_web_settings_endpoint_rejects_invalid_url(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings_file = Path(temp_dir) / "config" / "local_settings.json"
            with patch.dict(os.environ, {"PUBG_LOCAL_SETTINGS_FILE": str(settings_file)}):
                client = TestClient(create_app())
                response = client.post(
                    "/settings/web",
                    json={"local_web_base_url": "ftp://127.0.0.1:8000"},
                )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(settings_file.exists())


if __name__ == "__main__":
    unittest.main()
