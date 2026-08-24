from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from fastapi.testclient import TestClient

from pubg_ai.local_settings import LocalSettingsError, LocalSettingsStore
from pubg_ai.release import APP_RELEASE
from pubg_ai.web.app import create_app


class DisplaySettingsStoreTests(unittest.TestCase):
    def test_number_format_round_trip_is_shared_by_store_instances(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings_file = Path(temp_dir) / "config" / "local_settings.json"
            first = LocalSettingsStore(settings_file)
            second = LocalSettingsStore(settings_file)

            self.assertEqual(first.load_display_settings().number_format, "grouped")
            saved = first.save_display_settings("korean_units")

            self.assertEqual(saved.number_format, "korean_units")
            self.assertEqual(second.load_display_settings().number_format, "korean_units")
            payload = json.loads(settings_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["display"]["number_format"], "korean_units")

    def test_invalid_number_format_is_rejected(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = LocalSettingsStore(Path(temp_dir) / "local_settings.json")

            with self.assertRaisesRegex(LocalSettingsError, "number_format"):
                store.save_display_settings("mixed")


class DisplaySettingsApiTests(unittest.TestCase):
    def test_saved_format_is_injected_into_new_page_loads(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            (base_dir / "config").mkdir()
            (base_dir / ".env").write_text("", encoding="utf-8")
            client = TestClient(create_app(base_dir=base_dir))

            initial = client.get("/settings/display")
            saved = client.post(
                "/settings/display",
                json={"number_format": "korean_units"},
            )
            page = client.get("/")
            health = client.get("/health")

            self.assertEqual(initial.json()["display"]["number_format"], "grouped")
            self.assertEqual(saved.status_code, 200)
            self.assertEqual(saved.json()["display"]["number_format"], "korean_units")
            self.assertIn('data-app-release="' + APP_RELEASE + '"', page.text)
            self.assertIn('let activeNumberFormat = ["grouped", "korean_units", "plain"].includes("korean_units")', page.text)
            self.assertEqual(health.json()["app_release"], APP_RELEASE)

    def test_invalid_api_format_is_rejected(self) -> None:
        client = TestClient(create_app())

        response = client.post("/settings/display", json={"number_format": "mixed"})

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
