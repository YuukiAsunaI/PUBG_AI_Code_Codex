from __future__ import annotations

import unittest

from pubg_ai.player_recommendations import PlayerRecommendationService


class PlayerRecommendationServiceTests(unittest.TestCase):
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
                    },
                ],
                [
                    {
                        "match_id": "match-1",
                        "parent_item_code": "Item_Weapon_HK416_C",
                        "item_code": "Item_Attach_Weapon_Lower_Foregrip_C",
                        "item_name_ko": "Vertical Grip",
                        "item_category": "Attachment",
                        "item_sub_category": "Lower",
                        "attached_events": 2,
                        "win": 1,
                        "kills": 3,
                        "dbnos": 2,
                        "damage_dealt": 500.0,
                    },
                    {
                        "match_id": "match-2",
                        "parent_item_code": "Item_Weapon_HK416_C",
                        "item_code": "Item_Attach_Weapon_Lower_Foregrip_C",
                        "item_name_ko": "Vertical Grip",
                        "item_category": "Attachment",
                        "item_sub_category": "Lower",
                        "attached_events": 1,
                        "win": 0,
                        "kills": 1,
                        "dbnos": 1,
                        "damage_dealt": 220.0,
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
        self.assertGreater(report.weapons[0].range_score, 0)
        self.assertEqual(report.weapons[0].top_distance_buckets[0].bucket_label, "10-15m")
        self.assertEqual(report.weapons[1].accuracy, 1.0)
        self.assertEqual(report.weapon_ranges[0].weapon_code, "WeapHK416_C")
        self.assertEqual(report.weapon_ranges[0].bucket_label, "10-15m")
        self.assertEqual(report.weapon_attachments[0].weapon_code, "WeapHK416_C")
        self.assertEqual(report.weapon_attachments[0].attachment_name, "Vertical Grip")
        self.assertEqual(report.weapon_attachments[0].match_count, 2)
        self.assertEqual(report.attachments[0].item_name, "Vertical Grip")
        self.assertEqual(report.maps[0].map_name, "Erangel_Main")
        self.assertTrue(report.teammates[0].registered)
        self.assertEqual(report.teammates[0].name, "Friend")
        self.assertEqual(report.drop_zones[0].map_name, "Tiger_Main")
        self.assertEqual(report.drop_zones[0].grid_x, 5)
        self.assertEqual(report.drop_zones[0].grid_y, 2)
        self.assertAlmostEqual(report.drop_zones[0].win_rate, 0.5)
        self.assertEqual(len(connection.executed), 8)

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
