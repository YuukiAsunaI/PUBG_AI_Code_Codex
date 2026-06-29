from __future__ import annotations

from datetime import datetime
import unittest

from pubg_ai.player_stats import PlayerStatsService, weapon_code_from_identifier


class PlayerStatsServiceTests(unittest.TestCase):
    def test_builds_player_profile_summary(self) -> None:
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
                {
                    "match_count": 10,
                    "wins": 2,
                    "kills": 25,
                    "assists": 5,
                    "deaths": 8,
                    "dbnos_caused": 13,
                    "dbnos_taken": 4,
                    "damage_dealt": 2500.0,
                    "damage_taken": 1600.0,
                    "shots_fired": 1000,
                    "shots_hit": 210,
                    "headshot_kills": 6,
                    "avg_survival_seconds": 1420.5,
                    "avg_movement_distance_m": 3650.0,
                    "first_match_at_kst": datetime(2026, 6, 1, 20, 0, 0),
                    "last_match_at_kst": datetime(2026, 6, 29, 1, 0, 0),
                },
                [
                    {
                        "weapon_code": "WeapBerylM762_C",
                        "match_count": 6,
                        "kills": 12,
                        "assists": 2,
                        "deaths": 3,
                        "dbnos": 8,
                        "damage_dealt": 1200.0,
                        "shots_fired": 500,
                        "shots_hit": 95,
                        "headshot_kills": 2,
                    }
                ],
                [
                    {
                        "match_id": "match-2",
                        "created_at_kst": datetime(2026, 6, 29, 1, 0, 0),
                        "map_name": "Erangel_Main",
                        "game_mode": "squad-fpp",
                        "match_type": "official",
                        "duration_seconds": 1800,
                        "win_place": 1,
                        "raw_stats": {"timeSurvived": 1788.5},
                        "kills": 5,
                        "assists": 1,
                        "deaths": 0,
                        "dbnos_caused": 3,
                        "damage_dealt": 550.0,
                        "in_game_sampled_distance_m": 4200.0,
                    }
                ],
            ]
        )

        profile = PlayerStatsService(connection).get_profile(
            shard="steam",
            name="Yuuki_Asuna---",
            guild_id="guild-1",
            global_scope=False,
        )

        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.player.current_name, "Yuuki_Asuna---")
        self.assertEqual(profile.totals.match_count, 10)
        self.assertAlmostEqual(profile.totals.win_rate, 0.2)
        self.assertAlmostEqual(profile.totals.kda, 3.75)
        self.assertAlmostEqual(profile.totals.accuracy, 0.21)
        self.assertAlmostEqual(profile.totals.headshot_kill_rate, 0.24)
        self.assertEqual(profile.top_weapons[0].weapon_code, "WeapBerylM762_C")
        self.assertEqual(profile.top_weapons[0].weapon_name, "베릴 M762")
        self.assertAlmostEqual(profile.top_weapons[0].accuracy, 0.19)
        self.assertEqual(profile.recent_matches[0].match_id, "match-2")
        self.assertEqual(profile.recent_matches[0].survival_seconds, 1788.5)

        player_query_params = connection.executed[0][1]
        self.assertEqual(player_query_params, ["steam", "Yuuki_Asuna---", "guild-1"])

    def test_non_global_scope_without_guild_returns_none_without_querying(self) -> None:
        connection = FakeConnection([])

        profile = PlayerStatsService(connection).get_profile(
            shard="steam",
            name="Yuuki_Asuna---",
            guild_id=None,
            global_scope=False,
        )

        self.assertIsNone(profile)
        self.assertEqual(connection.executed, [])

    def test_missing_player_returns_none(self) -> None:
        connection = FakeConnection([None])

        profile = PlayerStatsService(connection).get_profile(
            shard="steam",
            name="Missing",
            global_scope=True,
        )

        self.assertIsNone(profile)
        self.assertEqual(len(connection.executed), 1)

    def test_builds_weapon_detail_from_alias_and_part_maps(self) -> None:
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
                    {"weapon_code": "WeapHK416_C"},
                    {"weapon_code": "WeapBerylM762_C"},
                ],
                [
                    {
                        "match_id": "match-2",
                        "created_at_kst": datetime(2026, 6, 29, 1, 0, 0),
                        "map_name": "Erangel_Main",
                        "game_mode": "squad-fpp",
                        "win_place": 1,
                        "shots_fired": 100,
                        "shots_hit": 30,
                        "hits_taken": 1,
                        "damage_dealt": 350.0,
                        "damage_taken": 90.0,
                        "kills": 3,
                        "assists": 1,
                        "deaths": 0,
                        "dbnos": 2,
                        "dbnos_taken": 0,
                        "finishes": 1,
                        "finishes_taken": 0,
                        "headshot_hits": 6,
                        "headshot_hits_taken": 0,
                        "headshot_kills": 1,
                        "headshot_deaths": 0,
                        "headshot_dbnos": 1,
                        "headshot_dbnos_taken": 0,
                        "hit_parts": {"head": 6, "torso": 20},
                        "taken_hit_parts": '{"arm": 1}',
                    },
                    {
                        "match_id": "match-1",
                        "created_at_kst": datetime(2026, 6, 28, 1, 0, 0),
                        "map_name": "Tiger_Main",
                        "game_mode": "squad",
                        "win_place": 4,
                        "shots_fired": 50,
                        "shots_hit": 10,
                        "hits_taken": 0,
                        "damage_dealt": 120.0,
                        "damage_taken": 0.0,
                        "kills": 1,
                        "assists": 0,
                        "deaths": 0,
                        "dbnos": 1,
                        "dbnos_taken": 0,
                        "finishes": 0,
                        "finishes_taken": 0,
                        "headshot_hits": 2,
                        "headshot_hits_taken": 0,
                        "headshot_kills": 0,
                        "headshot_deaths": 0,
                        "headshot_dbnos": 0,
                        "headshot_dbnos_taken": 0,
                        "hit_parts": {"head": 2, "leg": 3},
                        "taken_hit_parts": {},
                    },
                ],
            ]
        )

        detail = PlayerStatsService(connection).get_weapon_detail(
            shard="steam",
            name="Yuuki_Asuna---",
            guild_id="guild-1",
            weapon="M416",
        )

        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertEqual(detail.weapon_code, "WeapHK416_C")
        self.assertEqual(detail.weapon_name, "M416")
        self.assertEqual(detail.totals.match_count, 2)
        self.assertEqual(detail.totals.wins, 1)
        self.assertEqual(detail.totals.kills, 4)
        self.assertEqual(detail.totals.dbnos, 3)
        self.assertAlmostEqual(detail.totals.accuracy, 40 / 150)
        self.assertAlmostEqual(detail.totals.avg_damage_dealt, 235.0)
        self.assertEqual(detail.totals.hit_parts, {"head": 8, "torso": 20, "leg": 3})
        self.assertEqual(detail.totals.taken_hit_parts, {"arm": 1})
        self.assertEqual(detail.recent_matches[0].match_id, "match-2")

    def test_weapon_identifier_aliases_common_names(self) -> None:
        self.assertEqual(weapon_code_from_identifier("M416"), "WeapHK416_C")
        self.assertEqual(weapon_code_from_identifier("Beryl"), "WeapBerylM762_C")
        self.assertEqual(weapon_code_from_identifier("Item_Weapon_AK47_C"), "WeapAK47_C")


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
