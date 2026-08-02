from __future__ import annotations

from datetime import datetime
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from pubg_ai.web.app import create_app


class WebPlayerTrendTests(unittest.TestCase):
    def test_trend_endpoint_returns_filtered_kst_buckets(self) -> None:
        connection = FakeConnection(
            [
                {
                    "id": 1,
                    "account_id": "account.test",
                    "shard": "steam",
                    "current_name": "Player",
                    "active": 1,
                    "public_profile": 1,
                    "registered_by_discord_user_id": None,
                    "registered_guild_id": None,
                    "registered_channel_id": None,
                },
                [
                    {
                        "match_id": "match-1",
                        "created_at_kst": datetime(2026, 8, 2, 21, 0),
                        "duration_seconds": 1200,
                        "win_place": 1,
                        "raw_stats": {"timeSurvived": 1200},
                        "kills": 4,
                        "assists": 1,
                        "deaths": 0,
                        "dbnos_caused": 3,
                        "dbnos_taken": 0,
                        "damage_dealt": 500,
                        "damage_taken": 100,
                        "shots_fired": 100,
                        "shots_hit": 20,
                        "headshot_kills": 2,
                        "in_game_sampled_distance_m": 3000,
                    }
                ],
            ]
        )

        with patch("pubg_ai.web.app.connect_mysql", return_value=connection):
            response = TestClient(create_app()).get(
                "/players/trends?shard=steam&name=Player&granularity=hour&team_mode=squad"
                "&perspective=fpp&from_date_kst=2026-08-01&to_date_kst=2026-08-02"
            )

        self.assertEqual(response.status_code, 200)
        report = response.json()["trends"]
        self.assertEqual(report["timezone"], "Asia/Seoul")
        self.assertEqual(report["granularity"], "hour")
        self.assertEqual(report["filters"]["team_mode"], "squad")
        self.assertEqual(report["totals"]["wins"], 1)
        self.assertEqual(report["buckets"][0]["period_key"], "21")
        self.assertTrue(connection.closed)

    def test_trend_endpoint_rejects_bad_date_range(self) -> None:
        connection = FakeConnection([])
        with patch("pubg_ai.web.app.connect_mysql", return_value=connection):
            response = TestClient(create_app()).get(
                "/players/trends?shard=steam&name=Player&from_date_kst=2026-08-03&to_date_kst=2026-08-02"
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("on or before", response.json()["detail"])

    def test_local_manager_contains_complete_trend_controls_and_renderer(self) -> None:
        response = TestClient(create_app()).get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="trend-lookup"', response.text)
        self.assertIn('name="granularity"', response.text)
        self.assertIn('name="team_mode"', response.text)
        self.assertIn('name="perspective"', response.text)
        self.assertIn('name="match_type"', response.text)
        self.assertIn('name="map_name"', response.text)
        self.assertIn('name="from_date_kst"', response.text)
        self.assertIn("/players/trends?", response.text)
        self.assertIn('if (!text) return fallback;', response.text)
        self.assertIn("KST 추세 조회 완료", response.text)


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self.connection = connection
        self.result: object = None

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def execute(self, query: str, params: object = None) -> None:
        self.connection.executions.append((query, list(params or [])))
        self.result = self.connection.results.pop(0)

    def fetchone(self) -> object:
        return self.result

    def fetchall(self) -> object:
        return self.result


class FakeConnection:
    def __init__(self, results: list[object]) -> None:
        self.results = list(results)
        self.executions: list[tuple[str, list[object]]] = []
        self.closed = False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def close(self) -> None:
        self.closed = True


if __name__ == "__main__":
    unittest.main()
