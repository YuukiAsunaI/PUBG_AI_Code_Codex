from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

from fastapi.testclient import TestClient

from pubg_ai.web.app import create_app


class WebRecommendationTests(unittest.TestCase):
    def test_player_recommendations_endpoint_returns_report(self) -> None:
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
                [],
                [
                    {
                        "weapon_code": "WeapHK416_C",
                        "match_count": 2,
                        "wins": 1,
                        "kills": 4,
                        "assists": 1,
                        "deaths": 1,
                        "dbnos": 3,
                        "damage_dealt": 700.0,
                        "shots_fired": 200,
                        "shots_hit": 60,
                    }
                ],
                [],
                [],
                [],
                [],
                [],
                [],
            ]
        )

        with patch("pubg_ai.web.app.connect_mysql", return_value=connection):
            client = TestClient(create_app())
            response = client.get("/players/recommendations?shard=steam&name=Yuuki_Asuna---")

        self.assertEqual(response.status_code, 200)
        payload = response.json()["recommendations"]
        self.assertEqual(payload["player"]["current_name"], "Yuuki_Asuna---")
        self.assertEqual(payload["weapons"][0]["weapon_code"], "WeapHK416_C")
        self.assertEqual(payload["weapons"][0]["weapon_name"], "M416")
        self.assertEqual(payload["attachments"], [])
        self.assertTrue(connection.closed)

    def test_player_recommendation_evidence_endpoint_returns_snapshots(self) -> None:
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
                        "attachment_codes": '["Item_Attach_Weapon_Lower_Foregrip_C"]',
                        "attachment_names_ko": '["Vertical Grip"]',
                        "distance_m": 20.0,
                        "is_headshot": 1,
                        "win_place": 1,
                        "player_kills": 3,
                        "player_dbnos": 2,
                        "player_damage_dealt": 500.0,
                    }
                ],
            ]
        )

        with patch("pubg_ai.web.app.connect_mysql", return_value=connection):
            client = TestClient(create_app())
            response = client.get(
                "/players/recommendations/weapon-attachment-evidence"
                "?shard=steam&name=Yuuki_Asuna---"
                "&weapon_code=WeapHK416_C"
                "&attachment_code=Item_Attach_Weapon_Lower_Foregrip_C"
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()["evidence"]
        self.assertEqual(payload["snapshot_count"], 1)
        self.assertEqual(payload["totals"]["kills"], 1)
        self.assertEqual(payload["snapshots"][0]["match_id"], "match-1")
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
