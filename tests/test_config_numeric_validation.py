from __future__ import annotations

import unittest

from pubg_ai.config import AppConfig, DatabaseConfig


class ConfigNumericValidationTests(unittest.TestCase):
    def test_rejects_non_integer_collector_setting(self) -> None:
        with self.assertRaisesRegex(ValueError, "PUBG_PLAYER_LOOKUP_CHUNK_SIZE must be an integer"):
            AppConfig.from_env({"PUBG_PLAYER_LOOKUP_CHUNK_SIZE": "many"})

    def test_rejects_out_of_range_collector_settings(self) -> None:
        cases = (
            ("PUBG_COLLECTOR_POLL_INTERVAL_SECONDS", "59"),
            ("PUBG_COLLECTOR_POLL_INTERVAL_SECONDS", "301"),
            ("PUBG_COLLECTOR_CYCLE_PLAYER_LIMIT", "0"),
            ("PUBG_COLLECTOR_CYCLE_PLAYER_LIMIT", "101"),
            ("PUBG_PLAYER_LOOKUP_CHUNK_SIZE", "0"),
            ("PUBG_PLAYER_LOOKUP_CHUNK_SIZE", "11"),
        )
        for key, value in cases:
            with self.subTest(key=key, value=value):
                with self.assertRaisesRegex(ValueError, key):
                    AppConfig.from_env({key: value})

    def test_accepts_collector_boundary_values(self) -> None:
        config = AppConfig.from_env(
            {
                "PUBG_COLLECTOR_POLL_INTERVAL_SECONDS": "60",
                "PUBG_COLLECTOR_CYCLE_PLAYER_LIMIT": "1",
                "PUBG_PLAYER_LOOKUP_CHUNK_SIZE": "10",
            }
        )

        self.assertEqual(config.collector_poll_interval_seconds, 60)
        self.assertEqual(config.collector_cycle_player_limit, 1)
        self.assertEqual(config.player_lookup_chunk_size, 10)

    def test_rejects_invalid_mysql_port(self) -> None:
        for value in ("invalid", "0", "65536"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "MYSQL_PORT"):
                    DatabaseConfig.from_env({"MYSQL_PORT": value})


if __name__ == "__main__":
    unittest.main()
