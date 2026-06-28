from __future__ import annotations

import unittest

from pubg_ai.match_population import (
    detect_bot_player,
    population_records,
    summarize_match_population,
)


class MatchPopulationTests(unittest.TestCase):
    def test_summarizes_total_human_and_bot_players(self) -> None:
        summary = summarize_match_population(
            [
                {"accountId": "account.1", "name": "HumanOne"},
                {"accountId": "account.2", "name": "HumanTwo"},
                {"accountId": "ai.123", "name": "BotOne"},
            ]
        )

        self.assertEqual(summary.total_players, 3)
        self.assertEqual(summary.human_players, 2)
        self.assertEqual(summary.bot_players, 1)
        self.assertEqual(summary.detection_source_counts["account_id_prefix"], 1)

    def test_unwraps_telemetry_character_records(self) -> None:
        records = population_records(
            [
                {"character": {"accountId": "account.1", "name": "HumanOne"}},
                {"character": {"accountId": "ai.777", "name": "BotOne"}},
            ]
        )

        self.assertEqual(len(records), 2)
        self.assertFalse(records[0].is_bot)
        self.assertTrue(records[1].is_bot)

    def test_unwraps_match_api_participant_stats(self) -> None:
        summary = summarize_match_population(
            [
                {
                    "type": "participant",
                    "attributes": {
                        "stats": {
                            "playerId": "account.1",
                            "name": "HumanOne",
                        }
                    },
                },
                {
                    "type": "participant",
                    "attributes": {
                        "stats": {
                            "playerId": "ai.888",
                            "name": "BotTwo",
                        }
                    },
                },
            ]
        )

        self.assertEqual(summary.total_players, 2)
        self.assertEqual(summary.human_players, 1)
        self.assertEqual(summary.bot_players, 1)

    def test_deduplicates_by_player_identifier(self) -> None:
        summary = summarize_match_population(
            [
                {"accountId": "account.1", "name": "HumanOne"},
                {"accountId": "account.1", "name": "HumanOne"},
                {"accountId": "ai.123", "name": "BotOne"},
                {"accountId": "ai.123", "name": "BotOne"},
            ]
        )

        self.assertEqual(summary.total_players, 2)
        self.assertEqual(summary.human_players, 1)
        self.assertEqual(summary.bot_players, 1)

    def test_bot_detection_uses_name_as_fallback(self) -> None:
        is_bot, source = detect_bot_player(name="bot.training-1")

        self.assertTrue(is_bot)
        self.assertEqual(source, "name_prefix")


if __name__ == "__main__":
    unittest.main()
