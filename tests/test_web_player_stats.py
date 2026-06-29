from __future__ import annotations

from datetime import datetime
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from pubg_ai.web.app import create_app


class WebPlayerStatsTests(unittest.TestCase):
    def test_player_weapon_endpoint_returns_weapon_detail(self) -> None:
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
                [{"weapon_code": "WeapHK416_C"}],
                [
                    {
                        "match_id": "match-1",
                        "created_at_kst": datetime(2026, 6, 29, 1, 0, 0),
                        "map_name": "Erangel_Main",
                        "game_mode": "squad-fpp",
                        "win_place": 1,
                        "shots_fired": 100,
                        "shots_hit": 25,
                        "hits_taken": 0,
                        "damage_dealt": 300.0,
                        "damage_taken": 0.0,
                        "kills": 2,
                        "assists": 1,
                        "deaths": 0,
                        "dbnos": 2,
                        "dbnos_taken": 0,
                        "finishes": 1,
                        "finishes_taken": 0,
                        "headshot_hits": 5,
                        "headshot_hits_taken": 0,
                        "headshot_kills": 1,
                        "headshot_deaths": 0,
                        "headshot_dbnos": 1,
                        "headshot_dbnos_taken": 0,
                        "hit_parts": {"head": 5, "torso": 20},
                        "taken_hit_parts": {},
                    }
                ],
            ]
        )

        with patch("pubg_ai.web.app.connect_mysql", return_value=connection):
            client = TestClient(create_app())
            response = client.get("/players/weapon?shard=steam&name=Yuuki_Asuna---&weapon=M416")

        self.assertEqual(response.status_code, 200)
        payload = response.json()["weapon"]
        self.assertEqual(payload["weapon_code"], "WeapHK416_C")
        self.assertEqual(payload["weapon_name"], "M416")
        self.assertEqual(payload["totals"]["match_count"], 1)
        self.assertEqual(payload["totals"]["kills"], 2)
        self.assertEqual(payload["totals"]["hit_parts"], {"head": 5, "torso": 20})
        self.assertTrue(connection.closed)

    def test_player_match_endpoint_returns_match_detail(self) -> None:
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
                {
                    "match_id": "match-1",
                    "shard": "steam",
                    "map_name": "Erangel_Main",
                    "game_mode": "squad-fpp",
                    "match_type": "official",
                    "created_at_kst": datetime(2026, 6, 29, 1, 0, 0),
                    "duration_seconds": 1800,
                    "total_players": 100,
                    "human_players": 98,
                    "bot_players": 2,
                    "roster_id": "roster-1",
                    "team_id": 12,
                    "win_place": 1,
                    "death_type": "alive",
                    "raw_stats": {"timeSurvived": 1800},
                    "shots_fired": 100,
                    "shots_hit": 25,
                    "hits_taken": 0,
                    "damage_dealt": 300.0,
                    "damage_taken": 0.0,
                    "kills": 2,
                    "assists": 1,
                    "deaths": 0,
                    "dbnos_caused": 2,
                    "dbnos_taken": 0,
                    "finishes": 1,
                    "finishes_taken": 0,
                    "headshot_hits": 5,
                    "headshot_hits_taken": 0,
                    "headshot_kills": 1,
                    "headshot_deaths": 0,
                    "headshot_dbnos_caused": 1,
                    "headshot_dbnos_taken": 0,
                    "hit_parts": {"head": 5, "torso": 20},
                    "taken_hit_parts": {},
                    "landing_distance_m": 640.0,
                    "in_game_sampled_distance_m": 4200.0,
                },
                [],
                [
                    {
                        "weapon_code": "WeapHK416_C",
                        "shots_fired": 100,
                        "shots_hit": 25,
                        "hits_taken": 0,
                        "damage_dealt": 300.0,
                        "damage_taken": 0.0,
                        "kills": 2,
                        "assists": 1,
                        "deaths": 0,
                        "dbnos": 2,
                        "dbnos_taken": 0,
                        "headshot_kills": 1,
                        "hit_parts": {"head": 5, "torso": 20},
                        "taken_hit_parts": {},
                    }
                ],
            ]
        )

        with patch("pubg_ai.web.app.connect_mysql", return_value=connection):
            client = TestClient(create_app())
            response = client.get("/players/match?shard=steam&name=Yuuki_Asuna---&match_id=match-1")

        self.assertEqual(response.status_code, 200)
        payload = response.json()["match"]
        self.assertEqual(payload["match_id"], "match-1")
        self.assertTrue(payload["is_chicken"])
        self.assertEqual(payload["total_players"], 100)
        self.assertEqual(payload["bot_players"], 2)
        self.assertEqual(payload["weapons"][0]["weapon_name"], "M416")
        self.assertTrue(connection.closed)


class FakeConnection:
    def __init__(self, results: list[object]) -> None:
        self.results = list(results)
        self.closed = False

    def cursor(self) -> "FakeCursor":
        return FakeCursor(self)

    def close(self) -> None:
        self.closed = True


class FakeCursor:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, query: str, params: tuple[object, ...] | list[object]) -> None:
        return None

    def fetchone(self) -> object:
        return self.connection.results.pop(0)

    def fetchall(self) -> object:
        return self.connection.results.pop(0)


if __name__ == "__main__":
    unittest.main()
