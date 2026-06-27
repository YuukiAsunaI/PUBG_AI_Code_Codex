from __future__ import annotations

import unittest

from pubg_ai.match_classification import (
    classify_match_payload,
    perspective_from_game_mode,
    team_mode_from_game_mode,
)


class MatchClassificationTests(unittest.TestCase):
    def test_classifies_match_immediately_from_payload(self) -> None:
        classification = classify_match_payload(
            {
                "data": {
                    "id": "match-1",
                    "attributes": {
                        "shardId": "kakao",
                        "gameMode": "squad-fpp",
                        "matchType": "official",
                        "mapName": "Erangel_Main",
                        "isCustomMatch": False,
                    },
                }
            }
        )

        self.assertEqual(classification.match_id, "match-1")
        self.assertEqual(classification.shard, "kakao")
        self.assertEqual(classification.game_mode, "squad-fpp")
        self.assertEqual(classification.match_type, "official")
        self.assertEqual(classification.map_name, "Erangel_Main")
        self.assertEqual(classification.team_mode, "squad")
        self.assertEqual(classification.perspective, "fpp")
        self.assertFalse(classification.is_custom_match)
        self.assertFalse(classification.is_ranked)

    def test_classifies_ranked_match(self) -> None:
        classification = classify_match_payload(
            {
                "data": {
                    "id": "match-2",
                    "attributes": {
                        "gameMode": "squad-fpp",
                        "matchType": "ranked",
                        "mapName": "Baltic_Main",
                    },
                }
            },
            fallback_shard="steam",
        )

        self.assertEqual(classification.shard, "steam")
        self.assertTrue(classification.is_ranked)

    def test_game_mode_helpers(self) -> None:
        self.assertEqual(team_mode_from_game_mode("solo-fpp"), "solo")
        self.assertEqual(team_mode_from_game_mode("duo"), "duo")
        self.assertEqual(team_mode_from_game_mode("squad-fpp"), "squad")
        self.assertEqual(perspective_from_game_mode("duo-fpp"), "fpp")
        self.assertEqual(perspective_from_game_mode("duo"), "tpp")
        self.assertEqual(perspective_from_game_mode("unknown"), "unknown")


if __name__ == "__main__":
    unittest.main()
