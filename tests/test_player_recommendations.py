from __future__ import annotations

import unittest
from datetime import datetime

from pubg_ai.player_recommendations import (
    DropZoneRecommendation,
    PlayerRecommendationService,
    WeaponRecommendation,
    _aggregate_drop_regions,
    _beta_binomial_posterior,
    _inventory_burden,
    _performance_score_components,
)


class PlayerRecommendationServiceTests(unittest.TestCase):
    def test_beta_binomial_shrinkage_prevents_one_match_perfect_rate_from_leading(self) -> None:
        one_match = _beta_binomial_posterior(
            1,
            1,
            prior_rate=0.10,
            prior_strength=12,
        )
        established = _beta_binomial_posterior(
            8,
            12,
            prior_rate=0.10,
            prior_strength=12,
        )

        self.assertLess(one_match, established)
        self.assertLess(one_match, 1.0)

    def test_performance_score_exposes_observed_and_adjusted_win_rates(self) -> None:
        one_match = _performance_score_components(
            match_count=1,
            wins=1,
            kills=1,
            damage_dealt=100,
            win_prior_rate=0.10,
        )
        established = _performance_score_components(
            match_count=12,
            wins=8,
            kills=8,
            damage_dealt=1200,
            win_prior_rate=0.10,
        )

        self.assertEqual(one_match["observed_win_rate"], 1.0)
        self.assertLess(one_match["posterior_win_rate"], established["posterior_win_rate"])
        self.assertLess(one_match["win_rate_confidence"], established["win_rate_confidence"])
        self.assertLess(
            one_match["confidence_adjusted_score"],
            established["confidence_adjusted_score"],
        )

    def test_drop_region_aggregation_preserves_weighted_map_position(self) -> None:
        zones = [
            DropZoneRecommendation(
                map_name="Tiger_Main",
                map_name_ko="태이고",
                grid_x=1,
                grid_y=2,
                x_pct=0.2,
                y_pct=0.3,
                score=10.0,
                match_count=2,
                wins=1,
                kills=2,
                deaths=1,
                damage_dealt=400.0,
                win_rate=0.5,
                avg_damage_dealt=200.0,
                avg_survival_seconds=1000.0,
                reason="test",
                centroid_x_cm=200.0,
                centroid_y_cm=300.0,
                region_id="taego.test",
                region_display_name_ko="테스트 지역",
            ),
            DropZoneRecommendation(
                map_name="Tiger_Main",
                map_name_ko="태이고",
                grid_x=2,
                grid_y=3,
                x_pct=0.4,
                y_pct=0.5,
                score=20.0,
                match_count=1,
                wins=0,
                kills=1,
                deaths=1,
                damage_dealt=100.0,
                win_rate=0.0,
                avg_damage_dealt=100.0,
                avg_survival_seconds=800.0,
                reason="test",
                centroid_x_cm=400.0,
                centroid_y_cm=500.0,
                region_id="taego.test",
                region_display_name_ko="테스트 지역",
            ),
        ]

        region = _aggregate_drop_regions(zones, min_matches=1, limit=10)[0]

        self.assertAlmostEqual(region.x_pct, 0.2 * 2 / 3 + 0.4 / 3)
        self.assertAlmostEqual(region.y_pct, 0.3 * 2 / 3 + 0.5 / 3)
        self.assertAlmostEqual(region.centroid_x_cm or 0.0, 200 * 2 / 3 + 400 / 3)
        self.assertAlmostEqual(region.centroid_y_cm or 0.0, 300 * 2 / 3 + 500 / 3)

    def test_builds_recommendations_from_summary_tables(self) -> None:
        connection = FakeConnection(
            [
                {
                    "id": 1,
                    "account_id": "account.test",
                    "shard": "steam",
                    "current_name": "Yuuki_Asuna---",
                    "active": 1,
                    "public_profile": 1,
                    "registered_by_discord_user_id": "user-1",
                    "registered_guild_id": "guild-1",
                    "registered_channel_id": "channel-1",
                },
                [
                    {
                        "damage_causer_name": "WeapHK416_C",
                        "action": "kill",
                        "distance_m": 12.0,
                    },
                    {
                        "damage_causer_name": "WeapHK416_C",
                        "action": "dbno_caused",
                        "distance_m": 82.0,
                    },
                    {
                        "damage_causer_name": "WeapBerylM762_C",
                        "action": "finish",
                        "distance_m": 28.0,
                    },
                    {
                        "damage_causer_name": "WeapM24_C",
                        "action": "dbno_caused",
                        "distance_m": 320.0,
                    },
                ],
                [
                    {
                        "weapon_code": "WeapHK416_C",
                        "fight_count": 20,
                        "fight_wins": 14,
                        "fight_losses": 6,
                    },
                    {
                        "weapon_code": "WeapBerylM762_C",
                        "fight_count": 10,
                        "fight_wins": 4,
                        "fight_losses": 6,
                    },
                    {
                        "weapon_code": "WeapM24_C",
                        "fight_count": 5,
                        "fight_wins": 3,
                        "fight_losses": 2,
                    },
                ],
                [
                    {
                        "weapon_code": "WeapHK416_C",
                        "match_count": 5,
                        "wins": 2,
                        "kills": 10,
                        "assists": 3,
                        "deaths": 2,
                        "dbnos": 8,
                        "damage_dealt": 1800.0,
                        "shots_fired": 600,
                        "shots_hit": 180,
                        "headshot_hits": 36,
                    },
                    {
                        "weapon_code": "WeapBerylM762_C",
                        "match_count": 5,
                        "wins": 1,
                        "kills": 5,
                        "assists": 1,
                        "deaths": 4,
                        "dbnos": 4,
                        "damage_dealt": 900.0,
                        "shots_fired": 20,
                        "shots_hit": 113,
                        "headshot_hits": 17,
                    },
                    {
                        "weapon_code": "WeapM24_C",
                        "match_count": 5,
                        "wins": 1,
                        "kills": 0,
                        "assists": 1,
                        "deaths": 2,
                        "dbnos": 1,
                        "damage_dealt": 300.0,
                        "shots_fired": 10,
                        "shots_hit": 2,
                        "headshot_hits": 1,
                    },
                ],
                [
                    {
                        "match_id": "match-1",
                        "weapon_code": "WeapHK416_C",
                        "weapon_name_ko": "M416",
                        "attachment_codes": '["Item_Attach_Weapon_Lower_Foregrip_C", "Item_Attach_Weapon_Upper_DotSight_01_C"]',
                        "attachment_names_ko": '["Vertical Grip", "Red Dot Sight"]',
                        "combat_action": "kill",
                        "distance_m": 12.0,
                        "is_headshot": 1,
                        "win": 1,
                        "damage_dealt": 500.0,
                    },
                    {
                        "match_id": "match-2",
                        "weapon_code": "WeapHK416_C",
                        "weapon_name_ko": "M416",
                        "attachment_codes": [
                            "Item_Attach_Weapon_Lower_Foregrip_C",
                            "Item_Attach_Weapon_Upper_DotSight_01_C",
                        ],
                        "attachment_names_ko": ["Vertical Grip", "Red Dot Sight"],
                        "combat_action": "dbno_caused",
                        "distance_m": 82.0,
                        "is_headshot": 0,
                        "win": 0,
                        "damage_dealt": 220.0,
                    },
                    {
                        "match_id": "match-3",
                        "weapon_code": "WeapM24_C",
                        "weapon_name_ko": "M24",
                        "attachment_codes": ["Item_Attach_Weapon_Stock_SniperRifle_CheekPad_C"],
                        "attachment_names_ko": ["Cheek Pad"],
                        "combat_action": "dbno_caused",
                        "distance_m": 320.0,
                        "is_headshot": 0,
                        "win": 0,
                        "damage_dealt": 300.0,
                    },
                    {
                        "match_id": "match-4",
                        "weapon_code": "WeapHK416_C",
                        "weapon_name_ko": "M416",
                        "attachment_codes": [
                            "Item_Attach_Weapon_Lower_Foregrip_C",
                            "Item_Attach_Weapon_Muzzle_AR_MuzzleBrake_C",
                            "Item_Attach_Weapon_Upper_DotSight_01_C",
                        ],
                        "attachment_names_ko": [
                            "Vertical Grip",
                            "Muzzle Brake",
                            "Red Dot Sight",
                        ],
                        "combat_action": "kill",
                        "distance_m": 35.0,
                        "is_headshot": 0,
                        "win": 0,
                        "damage_dealt": 180.0,
                    },
                ],
                [
                    {
                        "item_code": "Item_Attach_Weapon_Lower_Foregrip_C",
                        "item_name_ko": "Vertical Grip",
                        "item_category": "Attachment",
                        "item_sub_category": "Lower",
                        "match_count": 4,
                        "attached_events": 6,
                        "wins": 2,
                        "damage_dealt": 1400.0,
                    }
                ],
                [
                    {
                        "map_name": "Erangel_Main",
                        "match_count": 6,
                        "wins": 3,
                        "kills": 12,
                        "assists": 4,
                        "deaths": 3,
                        "dbnos": 9,
                        "damage_dealt": 2100.0,
                        "avg_survival_seconds": 1500.0,
                    }
                ],
                [
                    {
                        "account_id": "account.friend",
                        "name": "Friend",
                        "registered": 1,
                        "match_count": 4,
                        "wins": 2,
                        "kills": 9,
                        "assists": 4,
                        "deaths": 2,
                        "dbnos": 7,
                        "damage_dealt": 1500.0,
                    }
                ],
                [
                    {
                        "match_id": "match-1",
                        "map_name": "Tiger_Main",
                        "duration_seconds": 1800,
                        "win_place": 1,
                        "raw_stats": {"timeSurvived": 1780},
                        "kills": 3,
                        "deaths": 0,
                        "damage_dealt": 300.0,
                        "landing_x": 408000.0,
                        "landing_y": 204000.0,
                    },
                    {
                        "match_id": "match-2",
                        "map_name": "Tiger_Main",
                        "duration_seconds": 1700,
                        "win_place": 4,
                        "raw_stats": {"timeSurvived": 1600},
                        "kills": 1,
                        "deaths": 1,
                        "damage_dealt": 200.0,
                        "landing_x": 450000.0,
                        "landing_y": 220000.0,
                    },
                ],
            ]
        )

        report = PlayerRecommendationService(connection).get_recommendations(
            shard="steam",
            name="Yuuki_Asuna---",
            guild_id="guild-1",
            min_matches=1,
        )

        self.assertIsNotNone(report)
        assert report is not None
        self.assertEqual(report.player.current_name, "Yuuki_Asuna---")
        self.assertEqual(report.weapons[0].weapon_code, "WeapHK416_C")
        self.assertEqual(report.weapons[0].weapon_name, "M416")
        self.assertAlmostEqual(report.weapons[0].accuracy, 0.3)
        self.assertAlmostEqual(report.weapons[0].headshot_hit_rate, 0.2)
        self.assertEqual(report.weapons[0].headshot_hits, 36)
        self.assertEqual(report.weapons[0].fight_count, 20)
        self.assertAlmostEqual(report.weapons[0].fight_win_rate, 0.7)
        self.assertGreater(report.weapons[0].score_components["fight_adjustment"], 0)
        self.assertEqual(
            report.weapons[0].accuracy_metric.metric_kind,
            "estimated_hit_rate",
        )
        self.assertGreater(report.weapons[0].range_score, 0)
        self.assertLessEqual(report.weapons[0].range_score, 12.0)
        self.assertEqual(report.weapons[0].score_components["range_bonus_cap"], 12.0)
        self.assertEqual(report.weapons[0].top_distance_buckets[0].bucket_label, "10-15m")
        self.assertEqual(report.weapons[1].accuracy, 0.0)
        self.assertEqual(
            report.weapons[1].accuracy_metric.metric_kind,
            "hit_events_per_attack",
        )
        self.assertEqual(
            report.weapons[1].accuracy_metric.quality,
            "hit_events_exceed_attacks",
        )
        self.assertEqual(report.weapon_ranges[0].weapon_code, "WeapHK416_C")
        self.assertEqual(report.weapon_ranges[0].bucket_label, "10-15m")
        vertical_grip = next(
            item for item in report.weapon_attachments
            if item.attachment_name == "수직 손잡이"
        )
        self.assertEqual(vertical_grip.weapon_code, "WeapHK416_C")
        self.assertEqual(vertical_grip.match_count, 3)
        self.assertEqual(vertical_grip.event_count, 3)
        self.assertEqual(vertical_grip.kills, 2)
        self.assertEqual(vertical_grip.dbnos, 1)
        self.assertEqual(vertical_grip.source, "loadout_snapshots")
        self.assertEqual(report.attachment_combinations[0].weapon_code, "WeapHK416_C")
        self.assertEqual(report.attachment_combinations[0].match_count, 2)
        self.assertEqual(
            report.attachment_combinations[0].attachment_names,
            ("수직 손잡이", "레드 도트 사이트"),
        )
        self.assertEqual(report.attachments[0].item_name, "수직 손잡이")
        self.assertGreaterEqual(len(report.loadouts), 1)
        self.assertEqual(report.loadouts[0].primary.weapon_code, "WeapHK416_C")
        self.assertEqual(report.loadouts[0].secondary.weapon_code, "WeapM24_C")
        self.assertNotEqual(
            report.loadouts[0].primary.weapon_code,
            report.loadouts[0].secondary.weapon_code,
        )
        self.assertEqual(
            {item.attachment_name for item in report.loadouts[0].primary_attachments},
            {"총구 제동기", "수직 손잡이", "레드 도트 사이트"},
        )
        self.assertIsNotNone(report.loadouts[0].primary_attachment_combination)
        self.assertEqual(
            report.loadouts[0].primary_attachment_combination.attachment_names,
            ("수직 손잡이", "총구 제동기", "레드 도트 사이트"),
        )
        self.assertTrue(report.loadouts[0].primary_attachment_plan["is_complete_for_observed_slots"])
        self.assertEqual(report.loadouts[0].primary_attachment_plan["selected_slot_count"], 3)
        burden = report.loadouts[0].inventory_burden
        self.assertEqual(burden["model_version"], "inventory-weight-v3")
        self.assertEqual(burden["carried_rounds_by_ammo"], {"5.56mm": 150, "7.62mm": 45})
        self.assertAlmostEqual(burden["estimated_inventory_weight"], 102.0)
        self.assertTrue(burden["mixed_ammo"])
        self.assertIn("kg가 아닌 PUBG 인벤토리 단위", burden["basis"])
        self.assertEqual(report.maps[0].map_name, "Erangel_Main")
        self.assertTrue(report.teammates[0].registered)
        self.assertEqual(report.teammates[0].name, "Friend")
        self.assertEqual(report.drop_zones[0].map_name, "Tiger_Main")
        self.assertEqual(report.drop_zones[0].grid_x, 10)
        self.assertEqual(report.drop_zones[0].grid_y, 5)
        self.assertEqual(len(report.drop_zones), 2)
        self.assertAlmostEqual(report.drop_zones[0].win_rate, 1.0)
        self.assertEqual(report.drop_zones[0].cluster_id, "Tiger_Main:grid20:10:5")
        self.assertEqual(report.drop_zones[0].grid_size, 20)
        self.assertAlmostEqual(report.drop_zones[0].centroid_x_cm or 0.0, 408000.0)
        self.assertAlmostEqual(report.drop_zones[0].centroid_y_cm or 0.0, 204000.0)
        self.assertEqual(report.drop_zones[1].cluster_id, "Tiger_Main:grid20:11:5")
        self.assertAlmostEqual(report.drop_zones[1].win_rate, 0.0)
        self.assertEqual(report.drop_zones[0].region_status, "matched")
        self.assertEqual(report.drop_zones[0].region_id, "taego.yong_cheon")
        self.assertEqual(report.drop_zones[0].region_name_ko, "용천")
        self.assertIsNotNone(report.drop_zones[0].region_catalog_version)
        self.assertEqual(len(connection.executed), 9)

    def test_inventory_model_attributes_lmg_extra_reserve_to_weight_pressure(self) -> None:
        def weapon(code: str, name: str) -> WeaponRecommendation:
            return WeaponRecommendation(
                weapon_code=code,
                weapon_name=name,
                score=100.0,
                match_count=10,
                wins=1,
                kills=10,
                assists=0,
                deaths=5,
                dbnos=5,
                damage_dealt=1000.0,
                shots_fired=500,
                shots_hit=100,
                win_rate=0.1,
                kills_per_match=1.0,
                dbnos_per_match=0.5,
                avg_damage_dealt=100.0,
                accuracy=0.2,
                reason="test",
            )

        burden = _inventory_burden(
            weapon("WeapRPD_C", "RPD"),
            weapon("WeapM24_C", "M24"),
        )

        self.assertEqual(burden["model_version"], "inventory-weight-v3")
        self.assertEqual(burden["ammo_types"], ["7.62mm"])
        self.assertEqual(burden["carried_rounds_by_ammo"], {"7.62mm": 240})
        self.assertAlmostEqual(burden["estimated_inventory_weight"], 144.0)
        self.assertAlmostEqual(burden["lmg_extra_reserve_inventory_weight"], 42.0)
        self.assertAlmostEqual(burden["lmg_reserve_penalty"], 5.04)
        self.assertLessEqual(
            burden["lmg_reserve_penalty"],
            burden["reserve_pressure_penalty"],
        )
        self.assertIn("LMG", " ".join(burden["tradeoffs"]))
        self.assertIn("kg가 아닌", burden["basis"])

        mg3_burden = _inventory_burden(
            weapon("WeapMG3_C", "MG3"),
            weapon("WeapM24_C", "M24"),
        )
        self.assertEqual(
            mg3_burden["weapon_profiles"][0]["recommended_reserve_rounds"],
            220,
        )
        self.assertAlmostEqual(
            mg3_burden["lmg_extra_reserve_inventory_weight"],
            42.0,
        )

    def test_non_global_scope_without_guild_returns_none_without_querying(self) -> None:
        connection = FakeConnection([])

        report = PlayerRecommendationService(connection).get_recommendations(
            shard="steam",
            name="Yuuki_Asuna---",
            guild_id=None,
            global_scope=False,
        )

        self.assertIsNone(report)
        self.assertEqual(connection.executed, [])

    def test_gets_weapon_attachment_snapshot_evidence(self) -> None:
        connection = FakeConnection(
            [
                {
                    "id": 1,
                    "account_id": "account.test",
                    "shard": "steam",
                    "current_name": "Yuuki_Asuna---",
                    "active": 1,
                    "public_profile": 1,
                    "registered_by_discord_user_id": None,
                    "registered_guild_id": None,
                    "registered_channel_id": None,
                },
                [
                    {
                        "match_id": "match-1",
                        "shard": "steam",
                        "map_name": "Tiger_Main",
                        "game_mode": "squad-fpp",
                        "match_type": "official",
                        "created_at_kst": datetime(2026, 1, 1, 11, 0, 0),
                        "combat_event_index": 100,
                        "combat_action": "kill",
                        "combat_event_at_kst": datetime(2026, 1, 1, 11, 12, 0),
                        "weapon_code": "WeapHK416_C",
                        "weapon_name_ko": "M416",
                        "attachment_codes": '["Item_Attach_Weapon_Upper_DotSight_01_C","Item_Attach_Weapon_Lower_Foregrip_C"]',
                        "attachment_names_ko": '["Red Dot Sight","Vertical Grip"]',
                        "distance_m": 20.0,
                        "is_headshot": 1,
                        "win_place": 1,
                        "player_kills": 3,
                        "player_dbnos": 2,
                        "player_damage_dealt": 500.0,
                    },
                    {
                        "match_id": "match-2",
                        "shard": "steam",
                        "map_name": "Erangel_Main",
                        "game_mode": "duo",
                        "match_type": "official",
                        "created_at_kst": datetime(2026, 1, 1, 10, 0, 0),
                        "combat_event_index": 80,
                        "combat_action": "dbno_caused",
                        "combat_event_at_kst": datetime(2026, 1, 1, 10, 8, 0),
                        "weapon_code": "WeapHK416_C",
                        "weapon_name_ko": "M416",
                        "attachment_codes": ["Item_Attach_Weapon_Lower_Foregrip_C"],
                        "attachment_names_ko": ["Vertical Grip"],
                        "distance_m": 40.0,
                        "is_headshot": 0,
                        "win_place": 3,
                        "player_kills": 1,
                        "player_dbnos": 1,
                        "player_damage_dealt": 220.0,
                    },
                ],
            ]
        )

        report = PlayerRecommendationService(connection).get_weapon_attachment_evidence(
            shard="steam",
            name="Yuuki_Asuna---",
            global_scope=True,
            weapon_code="Item_Weapon_HK416_C",
            attachment_code="Item_Attach_Weapon_Lower_Foregrip_C",
        )

        self.assertIsNotNone(report)
        assert report is not None
        record = report.to_record()
        self.assertEqual(record["weapon_code"], "WeapHK416_C")
        self.assertEqual(record["snapshot_count"], 2)
        self.assertEqual(record["totals"]["kills"], 1)
        self.assertEqual(record["totals"]["dbnos"], 1)
        self.assertEqual(record["totals"]["headshots"], 1)
        self.assertEqual(record["totals"]["wins"], 1)
        self.assertEqual(record["totals"]["avg_distance_m"], 30.0)
        self.assertEqual(record["attachment_name"], "수직 손잡이")
        self.assertEqual(record["snapshots"][0]["equipped_attachment_names"][0], "레드 도트 사이트")
        self.assertEqual(record["snapshots"][0]["map_name_ko"], "태이고")
        self.assertEqual(record["snapshots"][0]["combat_event_at_kst"], "2026-01-01T11:12:00")
        self.assertEqual(len(connection.executed), 2)


class FakeConnection:
    def __init__(self, results: list[object]) -> None:
        self.results = list(results)
        self.executed: list[tuple[str, list[object]]] = []

    def cursor(self) -> "FakeCursor":
        return FakeCursor(self)


class FakeCursor:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, query: str, params: tuple[object, ...] | list[object]) -> None:
        self.connection.executed.append((query, list(params)))

    def fetchone(self) -> object:
        return self.connection.results.pop(0)

    def fetchall(self) -> object:
        return self.connection.results.pop(0)


if __name__ == "__main__":
    unittest.main()
