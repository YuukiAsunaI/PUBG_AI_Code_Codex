from __future__ import annotations

import unittest

from pubg_ai.weapon_stats import (
    body_part_from_damage_reason,
    normalize_weapon_code,
    summarize_player_match_combat,
    summarize_weapon_combat_stats,
)


class WeaponStatsTests(unittest.TestCase):
    def test_normalizes_item_weapon_code_to_damage_causer_code(self) -> None:
        self.assertEqual(normalize_weapon_code("Item_Weapon_BerylM762_C"), "WeapBerylM762_C")
        self.assertEqual(normalize_weapon_code("WeapBerylM762_C_1"), "WeapBerylM762_C")
        self.assertEqual(normalize_weapon_code("Item_Projectile_Grenade_C"), "ProjGrenade_C")
        self.assertIsNone(normalize_weapon_code("None"))

    def test_normalizes_damage_causer_case_to_known_dictionary_code(self) -> None:
        self.assertEqual(normalize_weapon_code("WeapFamasG2_C"), "WeapFAMASG2_C")
        self.assertEqual(normalize_weapon_code("Item_Weapon_FAMASG2_C"), "WeapFAMASG2_C")

    def test_maps_damage_reason_to_body_part(self) -> None:
        self.assertEqual(body_part_from_damage_reason("HeadShot"), "head")
        self.assertEqual(body_part_from_damage_reason("TorsoShot"), "torso")
        self.assertEqual(body_part_from_damage_reason("NewPartShot"), "NewPartShot")

    def test_summarizes_shots_hits_body_parts_and_kills_by_weapon(self) -> None:
        stats = summarize_weapon_combat_stats(
            [
                {
                    "_T": "LogWeaponFireCount",
                    "character": {"accountId": "account.attacker"},
                    "weaponId": "Item_Weapon_BerylM762_C",
                    "fireCount": 30,
                    "common": {"isGame": 1},
                },
                {
                    "_T": "LogPlayerTakeDamage",
                    "attacker": {"accountId": "account.attacker"},
                    "victim": {"accountId": "account.victim"},
                    "damageTypeCategory": "Damage_Gun",
                    "damageReason": "HeadShot",
                    "damage": 90.0,
                    "damageCauserName": "WeapBerylM762_C",
                    "common": {"isGame": 1},
                },
                {
                    "_T": "LogPlayerTakeDamage",
                    "attacker": {"accountId": "account.attacker"},
                    "victim": {"accountId": "account.victim"},
                    "damageTypeCategory": "Damage_Gun",
                    "damageReason": "TorsoShot",
                    "damage": 40.0,
                    "damageCauserName": "WeapBerylM762_C",
                    "common": {"isGame": 1},
                },
                {
                    "_T": "LogPlayerKillV2",
                    "killer": {"accountId": "account.attacker"},
                    "finisher": {"accountId": "account.attacker"},
                    "victim": {"accountId": "account.victim"},
                    "killerDamageInfo": {
                        "damageReason": "HeadShot",
                        "damageTypeCategory": "Damage_Gun",
                        "damageCauserName": "WeapBerylM762_C",
                    },
                    "finishDamageInfo": {
                        "damageReason": "HeadShot",
                        "damageTypeCategory": "Damage_Gun",
                        "damageCauserName": "WeapBerylM762_C",
                    },
                    "isSuicide": False,
                    "common": {"isGame": 1},
                },
            ],
            match_id="match-1",
        )

        by_key = {(item.account_id, item.weapon_code): item for item in stats}
        attacker = by_key[("account.attacker", "WeapBerylM762_C")]
        victim = by_key[("account.victim", "WeapBerylM762_C")]

        self.assertEqual(attacker.shots_fired, 30)
        self.assertEqual(attacker.shots_hit, 2)
        self.assertAlmostEqual(attacker.accuracy, 2 / 30)
        self.assertEqual(attacker.hit_parts, {"head": 1, "torso": 1})
        self.assertEqual(attacker.headshot_hits, 1)
        self.assertEqual(attacker.kills, 1)
        self.assertEqual(attacker.headshot_kills, 1)
        self.assertEqual(attacker.finishes, 1)
        self.assertEqual(attacker.headshot_finishes, 1)
        self.assertEqual(attacker.damage_dealt, 130.0)

        self.assertEqual(victim.hits_taken, 2)
        self.assertEqual(victim.taken_hit_parts, {"head": 1, "torso": 1})
        self.assertEqual(victim.headshot_hits_taken, 1)
        self.assertEqual(victim.deaths, 1)
        self.assertEqual(victim.headshot_deaths, 1)
        self.assertEqual(victim.finishes_taken, 1)
        self.assertEqual(victim.headshot_finishes_taken, 1)
        self.assertEqual(victim.damage_taken, 130.0)

    def test_summarizes_dbno_and_headshot_dbno_by_weapon(self) -> None:
        stats = summarize_weapon_combat_stats(
            [
                {
                    "_T": "LogPlayerMakeGroggy",
                    "attacker": {"accountId": "account.attacker"},
                    "victim": {"accountId": "account.victim"},
                    "damageTypeCategory": "Damage_Gun",
                    "damageReason": "HeadShot",
                    "damageCauserName": "WeapMini14_C",
                    "common": {"isGame": 1},
                }
            ],
            match_id="match-1",
        )

        by_key = {(item.account_id, item.weapon_code): item for item in stats}

        self.assertEqual(by_key[("account.attacker", "WeapMini14_C")].dbnos, 1)
        self.assertEqual(by_key[("account.attacker", "WeapMini14_C")].headshot_dbnos, 1)
        self.assertEqual(by_key[("account.victim", "WeapMini14_C")].dbnos_taken, 1)
        self.assertEqual(
            by_key[("account.victim", "WeapMini14_C")].headshot_dbnos_taken,
            1,
        )

    def test_attributes_weapon_assists_from_prior_gun_damage(self) -> None:
        stats = summarize_weapon_combat_stats(
            [
                {
                    "_T": "LogPlayerTakeDamage",
                    "attacker": {"accountId": "account.assist"},
                    "victim": {"accountId": "account.victim"},
                    "damageTypeCategory": "Damage_Gun",
                    "damageReason": "LegShot",
                    "damage": 30.0,
                    "damageCauserName": "WeapMini14_C",
                    "common": {"isGame": 1},
                },
                {
                    "_T": "LogPlayerKillV2",
                    "killer": {"accountId": "account.killer"},
                    "victim": {"accountId": "account.victim"},
                    "assists_AccountId": ["account.assist", "account.assist"],
                    "killerDamageInfo": {
                        "damageReason": "TorsoShot",
                        "damageTypeCategory": "Damage_Gun",
                        "damageCauserName": "WeapBerylM762_C",
                    },
                    "isSuicide": False,
                    "common": {"isGame": 1},
                },
            ],
            match_id="match-1",
        )

        by_key = {(item.account_id, item.weapon_code): item for item in stats}

        self.assertEqual(by_key[("account.assist", "WeapMini14_C")].assists, 1)
        self.assertEqual(by_key[("account.assist", "WeapMini14_C")].damage_dealt, 30.0)
        self.assertEqual(by_key[("account.killer", "WeapBerylM762_C")].kills, 1)

    def test_can_limit_results_to_tracked_accounts(self) -> None:
        stats = summarize_weapon_combat_stats(
            [
                {
                    "_T": "LogPlayerTakeDamage",
                    "attacker": {"accountId": "account.attacker"},
                    "victim": {"accountId": "account.victim"},
                    "damageTypeCategory": "Damage_Gun",
                    "damageReason": "LegShot",
                    "damage": 20.0,
                    "damageCauserName": "WeapUMP_C",
                    "common": {"isGame": 1},
                }
            ],
            match_id="match-1",
            tracked_account_ids={"account.victim"},
        )

        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0].account_id, "account.victim")
        self.assertEqual(stats[0].hits_taken, 1)

    def test_excludes_lobby_events_by_default(self) -> None:
        stats = summarize_weapon_combat_stats(
            [
                {
                    "_T": "LogWeaponFireCount",
                    "character": {"accountId": "account.attacker"},
                    "weaponId": "Item_Weapon_Apple_C",
                    "fireCount": 10,
                    "common": {"isGame": 0},
                }
            ],
            match_id="match-1",
        )

        self.assertEqual(stats, [])

    def test_ignores_non_gun_kill_damage_for_weapon_stats(self) -> None:
        stats = summarize_weapon_combat_stats(
            [
                {
                    "_T": "LogPlayerKillV2",
                    "killer": {"accountId": "account.zone"},
                    "victim": {"accountId": "account.victim"},
                    "killerDamageInfo": {
                        "damageReason": "NonSpecific",
                        "damageTypeCategory": "Damage_BlueZone",
                        "damageCauserName": "TslGameModeBase_BattleRoyaleBP_C",
                    },
                    "finishDamageInfo": {
                        "damageReason": "None",
                        "damageTypeCategory": "Damage_None",
                        "damageCauserName": "None",
                    },
                    "isSuicide": False,
                    "common": {"isGame": 1},
                }
            ],
            match_id="match-1",
        )

        self.assertEqual(stats, [])

    def test_summarizes_player_match_combat_totals_and_assists(self) -> None:
        stats = summarize_player_match_combat(
            [
                {
                    "_T": "LogWeaponFireCount",
                    "character": {"accountId": "account.attacker"},
                    "weaponId": "Item_Weapon_BerylM762_C",
                    "fireCount": 30,
                    "common": {"isGame": 1},
                },
                {
                    "_T": "LogPlayerTakeDamage",
                    "attacker": {"accountId": "account.attacker"},
                    "victim": {"accountId": "account.victim"},
                    "damageTypeCategory": "Damage_Gun",
                    "damageReason": "HeadShot",
                    "damage": 90.0,
                    "damageCauserName": "WeapBerylM762_C",
                    "common": {"isGame": 1},
                },
                {
                    "_T": "LogPlayerTakeDamage",
                    "attacker": {"accountId": "account.attacker"},
                    "victim": {"accountId": "account.victim"},
                    "damageTypeCategory": "Damage_Gun",
                    "damageReason": "TorsoShot",
                    "damage": 40.0,
                    "damageCauserName": "WeapBerylM762_C",
                    "common": {"isGame": 1},
                },
                {
                    "_T": "LogPlayerMakeGroggy",
                    "attacker": {"accountId": "account.attacker"},
                    "victim": {"accountId": "account.victim"},
                    "damageTypeCategory": "Damage_Gun",
                    "damageReason": "HeadShot",
                    "damageCauserName": "WeapBerylM762_C",
                    "common": {"isGame": 1},
                },
                {
                    "_T": "LogPlayerKillV2",
                    "killer": {"accountId": "account.attacker"},
                    "finisher": {"accountId": "account.attacker"},
                    "victim": {"accountId": "account.victim"},
                    "assists_AccountId": ["account.assist", "account.assist"],
                    "killerDamageInfo": {
                        "damageReason": "HeadShot",
                        "damageTypeCategory": "Damage_Gun",
                        "damageCauserName": "WeapBerylM762_C",
                    },
                    "finishDamageInfo": {
                        "damageReason": "TorsoShot",
                        "damageTypeCategory": "Damage_Gun",
                        "damageCauserName": "WeapBerylM762_C",
                    },
                    "isSuicide": False,
                    "common": {"isGame": 1},
                },
            ],
            match_id="match-1",
        )

        by_account = {item.account_id: item for item in stats}
        attacker = by_account["account.attacker"]
        victim = by_account["account.victim"]
        assist = by_account["account.assist"]

        self.assertEqual(attacker.shots_fired, 30)
        self.assertEqual(attacker.shots_hit, 2)
        self.assertAlmostEqual(attacker.accuracy, 2 / 30)
        self.assertEqual(attacker.damage_dealt, 130.0)
        self.assertEqual(attacker.kills, 1)
        self.assertEqual(attacker.dbnos_caused, 1)
        self.assertEqual(attacker.dbnos_taken, 0)
        self.assertEqual(attacker.finishes, 1)
        self.assertEqual(attacker.headshot_hits, 1)
        self.assertEqual(attacker.headshot_kills, 1)
        self.assertEqual(attacker.headshot_dbnos_caused, 1)
        self.assertEqual(attacker.hit_parts, {"head": 1, "torso": 1})

        self.assertEqual(victim.hits_taken, 2)
        self.assertEqual(victim.damage_taken, 130.0)
        self.assertEqual(victim.deaths, 1)
        self.assertEqual(victim.dbnos_caused, 0)
        self.assertEqual(victim.dbnos_taken, 1)
        self.assertEqual(victim.finishes_taken, 1)
        self.assertEqual(victim.headshot_hits_taken, 1)
        self.assertEqual(victim.headshot_deaths, 1)
        self.assertEqual(victim.headshot_dbnos_taken, 1)
        self.assertEqual(victim.taken_hit_parts, {"head": 1, "torso": 1})

        self.assertEqual(assist.assists, 1)
        self.assertEqual(assist.kills, 0)
        self.assertEqual(assist.damage_dealt, 0.0)

    def test_player_match_combat_totals_honor_tracked_account_filter(self) -> None:
        stats = summarize_player_match_combat(
            [
                {
                    "_T": "LogPlayerTakeDamage",
                    "attacker": {"accountId": "account.attacker"},
                    "victim": {"accountId": "account.victim"},
                    "damageTypeCategory": "Damage_Gun",
                    "damageReason": "LegShot",
                    "damage": 20.0,
                    "damageCauserName": "WeapUMP_C",
                    "common": {"isGame": 1},
                },
                {
                    "_T": "LogPlayerKillV2",
                    "killer": {"accountId": "account.other"},
                    "victim": {"accountId": "account.victim"},
                    "assists_AccountId": ["account.assist"],
                    "killerDamageInfo": {
                        "damageReason": "TorsoShot",
                        "damageTypeCategory": "Damage_Gun",
                        "damageCauserName": "WeapMini14_C",
                    },
                    "isSuicide": False,
                    "common": {"isGame": 1},
                },
            ],
            match_id="match-1",
            tracked_account_ids={"account.victim", "account.assist"},
        )

        by_account = {item.account_id: item for item in stats}

        self.assertEqual(set(by_account), {"account.victim", "account.assist"})
        self.assertEqual(by_account["account.victim"].damage_taken, 20.0)
        self.assertEqual(by_account["account.victim"].deaths, 1)
        self.assertEqual(by_account["account.assist"].assists, 1)

    def test_player_match_combat_does_not_double_count_weapon_assists(self) -> None:
        stats = summarize_player_match_combat(
            [
                {
                    "_T": "LogPlayerTakeDamage",
                    "attacker": {"accountId": "account.assist"},
                    "victim": {"accountId": "account.victim"},
                    "damageTypeCategory": "Damage_Gun",
                    "damageReason": "TorsoShot",
                    "damage": 30.0,
                    "damageCauserName": "WeapMini14_C",
                    "common": {"isGame": 1},
                },
                {
                    "_T": "LogPlayerKillV2",
                    "killer": {"accountId": "account.killer"},
                    "victim": {"accountId": "account.victim"},
                    "assists_AccountId": ["account.assist"],
                    "killerDamageInfo": {
                        "damageReason": "TorsoShot",
                        "damageTypeCategory": "Damage_Gun",
                        "damageCauserName": "WeapBerylM762_C",
                    },
                    "isSuicide": False,
                    "common": {"isGame": 1},
                },
            ],
            match_id="match-1",
            tracked_account_ids={"account.assist"},
        )

        self.assertEqual(stats[0].assists, 1)
        self.assertEqual(stats[0].damage_dealt, 30.0)


if __name__ == "__main__":
    unittest.main()
