from __future__ import annotations

from dataclasses import replace
import unittest

from pubg_ai.match_collection_policy import (
    CUSTOM_MATCH_REASON,
    TRAINING_MATCH_REASON,
    decide_match_collection,
)
from pubg_ai.pubg_client import PubgMatchDetails, PubgRateLimit


class MatchCollectionPolicyTests(unittest.TestCase):
    def test_supported_match_families_are_collected(self) -> None:
        cases = [
            _match(game_mode="squad-fpp", match_type="official"),
            _match(game_mode="squad-fpp", match_type="competitive"),
            _match(game_mode="solo", match_type="seasonal"),
            _match(game_mode="tdm", match_type="official"),
            _match(game_mode="lab-fpp", match_type="official"),
        ]

        for match in cases:
            with self.subTest(game_mode=match.game_mode, match_type=match.match_type):
                self.assertTrue(decide_match_collection(match).should_collect)

    def test_custom_match_flag_is_excluded(self) -> None:
        decision = decide_match_collection(_match(is_custom_match=True))

        self.assertTrue(decision.is_excluded)
        self.assertEqual(decision.exclusion_reason, CUSTOM_MATCH_REASON)
        self.assertEqual(decision.matched_rule, "isCustomMatch")

    def test_custom_like_mode_name_without_official_flag_is_not_discarded(self) -> None:
        decision = decide_match_collection(
            _match(game_mode="normal-squad-fpp", is_custom_match=False)
        )

        self.assertTrue(decision.should_collect)

    def test_ai_training_match_type_is_excluded(self) -> None:
        decision = decide_match_collection(_match(match_type="airoyale"))

        self.assertEqual(decision.exclusion_reason, TRAINING_MATCH_REASON)
        self.assertEqual(decision.matched_rule, "matchType:airoyale")

    def test_legacy_training_match_types_are_excluded_without_map_dependency(
        self,
    ) -> None:
        for match_type in ("trainingroom", "tutorialatoz"):
            with self.subTest(match_type=match_type):
                decision = decide_match_collection(_match(match_type=match_type))

                self.assertTrue(decision.is_excluded)
                self.assertEqual(decision.exclusion_reason, TRAINING_MATCH_REASON)
                self.assertEqual(decision.matched_rule, f"matchType:{match_type}")

    def test_camp_jackal_training_map_is_excluded(self) -> None:
        decision = decide_match_collection(_match(map_name="Range_Main"))

        self.assertEqual(decision.exclusion_reason, TRAINING_MATCH_REASON)
        self.assertEqual(decision.matched_rule, "mapName:range-main")

    def test_explicit_training_game_mode_is_separator_insensitive(self) -> None:
        decision = decide_match_collection(_match(game_mode="Basic_Training"))

        self.assertEqual(decision.exclusion_reason, TRAINING_MATCH_REASON)


def _match(**changes: object) -> PubgMatchDetails:
    base = PubgMatchDetails(
        match_id="match-1",
        shard="steam",
        map_name="Baltic_Main",
        game_mode="squad-fpp",
        match_type="official",
        created_at="2026-08-26T00:00:00Z",
        duration_seconds=1800,
        season_state="progress",
        is_custom_match=False,
        telemetry_url="https://telemetry-cdn.pubg.com/match-1",
        participants=[],
        raw_payload={"data": {"id": "match-1"}},
        rate_limit=PubgRateLimit(),
    )
    return replace(base, **changes)


if __name__ == "__main__":
    unittest.main()
