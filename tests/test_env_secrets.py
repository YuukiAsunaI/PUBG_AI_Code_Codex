from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pubg_ai.config import load_dotenv_values
from pubg_ai.env_secrets import EnvSecretError, EnvSecretStore


class EnvSecretStoreTests(unittest.TestCase):
    def test_set_secret_preserves_other_lines_and_removes_duplicates(self) -> None:
        with TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            env_file.write_text(
                "# local configuration\nMYSQL_HOST=127.0.0.1\nPUBG_API_KEY=old\nPUBG_API_KEY=duplicate\n",
                encoding="utf-8",
            )

            EnvSecretStore(env_file).set_secret("PUBG_API_KEY", 'new\\value"quoted')

            body = env_file.read_text(encoding="utf-8")
            self.assertIn("# local configuration", body)
            self.assertIn("MYSQL_HOST=127.0.0.1", body)
            self.assertEqual(body.count("PUBG_API_KEY="), 1)
            self.assertEqual(load_dotenv_values(env_file)["PUBG_API_KEY"], 'new\\value"quoted')

    def test_clear_secret_does_not_remove_other_configuration(self) -> None:
        with TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            env_file.write_text(
                "DISCORD_BOT_TOKEN=secret\nMYSQL_DATABASE=pubg_ai\n",
                encoding="utf-8",
            )

            EnvSecretStore(env_file).clear_secret("DISCORD_BOT_TOKEN")

            body = env_file.read_text(encoding="utf-8")
            self.assertNotIn("DISCORD_BOT_TOKEN", body)
            self.assertIn("MYSQL_DATABASE=pubg_ai", body)

    def test_rejects_unknown_names_and_line_breaks(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = EnvSecretStore(Path(temp_dir) / ".env")

            with self.assertRaises(EnvSecretError):
                store.set_secret("MYSQL_PASSWORD", "secret")
            with self.assertRaises(EnvSecretError):
                store.set_secret("PUBG_API_KEY", "first\nINJECTED=value")


if __name__ == "__main__":
    unittest.main()
