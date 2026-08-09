from __future__ import annotations

import unittest

from pubg_ai.weapon_stats import summarize_player_match_combat, summarize_weapon_combat_stats


class OverallCombatTotalsTests(unittest.TestCase):
    def test_counts_grenade_damage_dbno_kill_and_finish_only_in_overall_totals(self) -> None:
        events = [
            {
                "_T": "LogPlayerTakeDamage",
                "attacker": {"accountId": "attacker"},
                "victim": {"accountId": "victim"},
                "damageTypeCategory": "Damage_Explosion_Grenade",
                "damageReason": "NonSpecific",
                "damageCauserName": "ProjGrenade_C",
                "damage": 75.0,
                "common": {"isGame": 1},
            },
            {
                "_T": "LogPlayerMakeGroggy",
                "attacker": {"accountId": "attacker"},
                "victim": {"accountId": "victim"},
                "damageTypeCategory": "Damage_Explosion_Grenade",
                "damageReason": "NonSpecific",
                "damageCauserName": "ProjGrenade_C",
                "common": {"isGame": 1},
            },
            {
                "_T": "LogPlayerKillV2",
                "killer": {"accountId": "attacker"},
                "finisher": {"accountId": "attacker"},
                "victim": {"accountId": "victim"},
                "killerDamageInfo": {
                    "damageTypeCategory": "Damage_Explosion_Grenade",
                    "damageReason": "NonSpecific",
                    "damageCauserName": "ProjGrenade_C",
                },
                "finishDamageInfo": {
                    "damageTypeCategory": "Damage_Explosion_Grenade",
                    "damageReason": "NonSpecific",
                    "damageCauserName": "ProjGrenade_C",
                },
                "isSuicide": False,
                "common": {"isGame": 1},
            },
        ]

        weapon_stats = summarize_weapon_combat_stats(events, match_id="match-1")
        totals = {row.account_id: row for row in summarize_player_match_combat(events, match_id="match-1")}

        self.assertEqual(weapon_stats, [])
        self.assertEqual(totals["attacker"].damage_dealt, 75.0)
        self.assertEqual(totals["attacker"].dbnos_caused, 1)
        self.assertEqual(totals["attacker"].kills, 1)
        self.assertEqual(totals["attacker"].finishes, 1)
        self.assertEqual(totals["victim"].damage_taken, 75.0)
        self.assertEqual(totals["victim"].dbnos_taken, 1)
        self.assertEqual(totals["victim"].deaths, 1)
        self.assertEqual(totals["victim"].finishes_taken, 1)

    def test_counts_suicide_death_without_awarding_kill(self) -> None:
        totals = summarize_player_match_combat(
            [
                {
                    "_T": "LogPlayerKillV2",
                    "killer": {"accountId": "player"},
                    "victim": {"accountId": "player"},
                    "killerDamageInfo": {
                        "damageTypeCategory": "Damage_BlueZone",
                        "damageReason": "NonSpecific",
                        "damageCauserName": "BlueZone",
                    },
                    "isSuicide": True,
                    "common": {"isGame": 1},
                }
            ],
            match_id="match-1",
        )

        self.assertEqual(totals[0].kills, 0)
        self.assertEqual(totals[0].deaths, 1)

    def test_unknown_gun_still_counts_overall_hit_and_headshot(self) -> None:
        totals = {
            row.account_id: row
            for row in summarize_player_match_combat(
                [
                    {
                        "_T": "LogPlayerTakeDamage",
                        "attacker": {"accountId": "attacker"},
                        "victim": {"accountId": "victim"},
                        "damageTypeCategory": "Damage_Gun",
                        "damageReason": "HeadShot",
                        "damageCauserName": None,
                        "damage": 40.0,
                        "common": {"isGame": 1},
                    }
                ],
                match_id="match-1",
            )
        }

        self.assertEqual(totals["attacker"].shots_hit, 1)
        self.assertEqual(totals["attacker"].headshot_hits, 1)
        self.assertEqual(totals["attacker"].hit_parts, {"head": 1})
        self.assertEqual(totals["victim"].hits_taken, 1)
        self.assertEqual(totals["victim"].headshot_hits_taken, 1)


if __name__ == "__main__":
    unittest.main()
