from __future__ import annotations

import unittest

from pubg_ai.combat_outcomes import dbno_outcomes_from_event, is_dbno_fight_mode


class CombatOutcomeTests(unittest.TestCase):
    def test_registered_attacker_dbno_counts_as_fight_win_in_squad(self) -> None:
        outcomes = dbno_outcomes_from_event(
            {
                "_T": "LogPlayerMakeGroggy",
                "attacker": {"accountId": "account.attacker"},
                "victim": {"accountId": "account.victim"},
                "dBNOId": "dbno-1",
                "damageCauserName": "WeapBerylM762_C",
                "distance": 42.5,
            },
            registered_account_ids={"account.attacker"},
            game_mode="squad-fpp",
            match_id="match-1",
        )

        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].outcome_type, "dbno_win")
        self.assertTrue(outcomes[0].counts_as_fight_win)
        self.assertEqual(outcomes[0].opponent_account_id, "account.victim")
        self.assertEqual(outcomes[0].dbno_id, "dbno-1")

    def test_registered_victim_dbno_counts_as_fight_loss_in_duo(self) -> None:
        outcomes = dbno_outcomes_from_event(
            {
                "_T": "LogPlayerMakeGroggy",
                "attacker": {"accountId": "account.attacker"},
                "victim": {"accountId": "account.victim"},
            },
            registered_account_ids={"account.victim"},
            game_mode="duo",
            match_id="match-1",
        )

        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].outcome_type, "dbno_loss")
        self.assertTrue(outcomes[0].counts_as_fight_loss)
        self.assertEqual(outcomes[0].opponent_account_id, "account.attacker")

    def test_both_registered_players_get_opposite_dbno_outcomes(self) -> None:
        outcomes = dbno_outcomes_from_event(
            {
                "_T": "LogPlayerMakeGroggy",
                "attacker": {"accountId": "account.attacker"},
                "victim": {"accountId": "account.victim"},
            },
            registered_account_ids={"account.attacker", "account.victim"},
            game_mode="squad",
            match_id="match-1",
        )

        self.assertEqual([outcome.outcome_type for outcome in outcomes], ["dbno_win", "dbno_loss"])

    def test_dbno_is_not_counted_as_fight_outcome_in_solo(self) -> None:
        outcomes = dbno_outcomes_from_event(
            {
                "_T": "LogPlayerMakeGroggy",
                "attacker": {"accountId": "account.attacker"},
                "victim": {"accountId": "account.victim"},
            },
            registered_account_ids={"account.attacker"},
            game_mode="solo",
            match_id="match-1",
        )

        self.assertEqual(outcomes, [])

    def test_dbno_fight_modes_are_duo_and_squad(self) -> None:
        self.assertTrue(is_dbno_fight_mode("duo-fpp"))
        self.assertTrue(is_dbno_fight_mode("squad"))
        self.assertFalse(is_dbno_fight_mode("solo-fpp"))


if __name__ == "__main__":
    unittest.main()
