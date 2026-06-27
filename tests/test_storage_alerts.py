from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pubg_ai.storage_alerts import assess_storage_capacity


class StorageAlertsTests(unittest.TestCase):
    def test_missing_storage_path_notifies_program_and_discord(self) -> None:
        alert = assess_storage_capacity(Path("Z:/definitely/missing/path"))

        self.assertEqual(alert.severity, "error")
        self.assertTrue(alert.should_notify)
        self.assertEqual(alert.targets, ("local_program", "discord"))

    def test_low_capacity_notifies_without_deleting_files(self) -> None:
        with TemporaryDirectory() as temp_dir:
            alert = assess_storage_capacity(
                Path(temp_dir),
                minimum_free_bytes=10**30,
            )

            self.assertEqual(alert.severity, "error")
            self.assertIn("raw files must be preserved", alert.message)
            self.assertTrue(alert.should_notify)

    def test_available_capacity_does_not_notify(self) -> None:
        with TemporaryDirectory() as temp_dir:
            alert = assess_storage_capacity(Path(temp_dir), minimum_free_bytes=1)

            self.assertEqual(alert.severity, "ok")
            self.assertFalse(alert.should_notify)


if __name__ == "__main__":
    unittest.main()

