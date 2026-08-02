from __future__ import annotations

import unittest

from pubg_ai.fight_outcome_processor import build_fight_outcomes


def character(account_id: str, team_id: int) -> dict[str, object]:
    return {
        "accountId": account_id,
        "teamId": team_id,
        "location": {"x": 1000.0, "y": 2000.0, "z": 0.0},
    }


def attack(
    account_id: str,
    team_id: int,
    weapon: str,
    attachments: list[str],
) -> dict[str, object]:
    return {
        "_T": "LogPlayerAttack",
        "_D": "2026-08-02T00:00:00Z",
        "common": {"isGame": 1.0},
        "attacker": character(account_id, team_id),
        "weapon": {
            "itemId": weapon,
            "category": "Weapon",
            "subCategory": "Main",
            "attachedItems": attachments,
        },
    }


class FightOutcomeParserTests(unittest.TestCase):
    def test_duo_dbno_is_a_win_and_loss_with_each_players_own_loadout(self) -> None:
        events = [
            attack(
                "account.attacker",
                1,
                "Item_Weapon_AUG_C",
                ["Item_Attach_Weapon_Lower_Foregrip_C"],
            ),
            attack(
                "account.victim",
                2,
                "Item_Weapon_HK416_C",
                ["Item_Attach_Weapon_Upper_DotSight_01_C"],
            ),
            {
                "_T": "LogPlayerMakeGroggy",
                "_D": "2026-08-02T00:00:02Z",
                "common": {"isGame": 1.0},
                "attacker": character("account.attacker", 1),
                "victim": character("account.victim", 2),
                "dBNOId": "dbno-1",
                "damageTypeCategory": "Damage_Gun",
                "damageCauserName": "WeapAUG_C",
                "damageReason": "TorsoShot",
                "distance": 2500.0,
            },
        ]

        outcomes = build_fight_outcomes(
            events,
            match_id="match-1",
            tracked_account_ids={"account.attacker", "account.victim"},
            game_mode="squad-fpp",
        )

        self.assertEqual([item.outcome_reason for item in outcomes], ["dbno_caused", "dbno_taken"])
        win, loss = outcomes
        self.assertEqual(win.outcome_type, "win")
        self.assertEqual(win.weapon_code, "WeapAUG_C")
        self.assertEqual(win.attachment_codes, ("Item_Attach_Weapon_Lower_Foregrip_C",))
        self.assertEqual(loss.outcome_type, "loss")
        self.assertEqual(loss.weapon_code, "WeapHK416_C")
        self.assertEqual(loss.attachment_codes, ("Item_Attach_Weapon_Upper_DotSight_01_C",))
        self.assertEqual(loss.opponent_weapon_code, "WeapAUG_C")
        self.assertEqual(loss.distance_m, 25.0)

    def test_solo_dbno_is_not_a_fight_outcome(self) -> None:
        events = [
            {
                "_T": "LogPlayerMakeGroggy",
                "common": {"isGame": 1.0},
                "attacker": character("account.attacker", 1),
                "victim": character("account.victim", 2),
                "damageCauserName": "WeapAUG_C",
            }
        ]

        outcomes = build_fight_outcomes(
            events,
            match_id="match-1",
            tracked_account_ids={"account.attacker", "account.victim"},
            game_mode="solo-fpp",
        )

        self.assertEqual(outcomes, [])

    def test_kill_and_death_use_distinct_own_weapon_contexts(self) -> None:
        events = [
            attack(
                "account.victim",
                2,
                "Item_Weapon_Kar98k_C",
                ["Item_Attach_Weapon_Upper_Aimpoint_C"],
            ),
            attack(
                "account.killer",
                1,
                "Item_Weapon_BerylM762_C",
                ["Item_Attach_Weapon_Magazine_QuickDraw_Large_C"],
            ),
            {
                "_T": "LogPlayerKillV2",
                "_D": "2026-08-02T00:00:05Z",
                "common": {"isGame": 1.0},
                "killer": character("account.killer", 1),
                "finisher": character("account.killer", 1),
                "victim": character("account.victim", 2),
                "isSuicide": False,
                "killerDamageInfo": {
                    "damageTypeCategory": "Damage_Gun",
                    "damageCauserName": "WeapBerylM762_C",
                    "damageReason": "HeadShot",
                    "distance": 3200.0,
                },
                "finishDamageInfo": {
                    "damageTypeCategory": "Damage_Gun",
                    "damageCauserName": "WeapBerylM762_C",
                    "damageReason": "HeadShot",
                    "distance": 3200.0,
                },
            },
        ]

        outcomes = build_fight_outcomes(
            events,
            match_id="match-2",
            tracked_account_ids={"account.killer", "account.victim"},
            game_mode="squad",
        )

        self.assertEqual([item.outcome_reason for item in outcomes], ["kill", "death"])
        win, loss = outcomes
        self.assertEqual(win.weapon_code, "WeapBerylM762_C")
        self.assertTrue(win.is_headshot)
        self.assertEqual(loss.weapon_code, "WeapKar98k_C")
        self.assertEqual(loss.attachment_codes, ("Item_Attach_Weapon_Upper_Aimpoint_C",))
        self.assertEqual(loss.opponent_weapon_code, "WeapBerylM762_C")
        self.assertEqual(loss.weapon_context_source, "attack")

    def test_item_equip_is_used_when_player_has_not_attacked(self) -> None:
        events = [
            {
                "_T": "LogItemEquip",
                "_D": "2026-08-02T00:00:00Z",
                "common": {"isGame": 1.0},
                "character": character("account.victim", 2),
                "item": {
                    "itemId": "Item_Weapon_UMP_C",
                    "category": "Weapon",
                    "attachedItems": ["Item_Attach_Weapon_Upper_Holosight_C"],
                },
            },
            {
                "_T": "LogPlayerMakeGroggy",
                "_D": "2026-08-02T00:00:03Z",
                "common": {"isGame": 1.0},
                "attacker": character("account.enemy", 1),
                "victim": character("account.victim", 2),
                "damageTypeCategory": "Damage_Gun",
                "damageCauserName": "WeapAK47_C",
                "damageReason": "TorsoShot",
            },
        ]

        outcomes = build_fight_outcomes(
            events,
            match_id="match-3",
            tracked_account_ids={"account.victim"},
            game_mode="duo",
            bot_account_ids={"account.enemy"},
        )

        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].weapon_code, "WeapUMP_C")
        self.assertEqual(outcomes[0].weapon_context_source, "equip")
        self.assertTrue(outcomes[0].opponent_is_bot)

    def test_friendly_fire_is_preserved_but_explicitly_flagged(self) -> None:
        events = [
            attack("account.attacker", 1, "Item_Weapon_AUG_C", []),
            {
                "_T": "LogPlayerMakeGroggy",
                "common": {"isGame": 1.0},
                "attacker": character("account.attacker", 1),
                "victim": character("account.victim", 1),
                "damageCauserName": "WeapAUG_C",
            },
        ]

        outcomes = build_fight_outcomes(
            events,
            match_id="match-4",
            tracked_account_ids={"account.attacker"},
            game_mode="squad",
        )

        self.assertEqual(len(outcomes), 1)
        self.assertTrue(outcomes[0].is_friendly_fire)

    def test_suicide_records_only_a_loss_without_an_opponent(self) -> None:
        player = character("account.self", 1)
        events = [
            attack("account.self", 1, "Item_Weapon_HK416_C", []),
            {
                "_T": "LogPlayerKillV2",
                "common": {"isGame": 1.0},
                "killer": player,
                "finisher": player,
                "victim": player,
                "isSuicide": True,
                "killerDamageInfo": {
                    "damageTypeCategory": "Damage_Explosion_Grenade",
                    "damageCauserName": "ProjGrenade_C",
                    "damageReason": "NonSpecific",
                },
            },
        ]

        outcomes = build_fight_outcomes(
            events,
            match_id="match-5",
            tracked_account_ids={"account.self"},
            game_mode="solo",
        )

        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].outcome_reason, "death")
        self.assertIsNone(outcomes[0].opponent_account_id)


if __name__ == "__main__":
    unittest.main()
