from __future__ import annotations

from datetime import datetime
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from pubg_ai.web.app import create_app


class WebFightOutcomeTests(unittest.TestCase):
    def test_fight_outcome_endpoint_returns_totals_weapons_and_loadouts(self) -> None:
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
                        "event_index": 50,
                        "event_at_kst": datetime(2026, 8, 2, 9, 0, 0),
                        "map_name": "Baltic_Main",
                        "game_mode": "squad-fpp",
                        "outcome_type": "win",
                        "outcome_reason": "dbno_caused",
                        "opponent_account_id": "account.enemy",
                        "opponent_is_bot": 0,
                        "is_friendly_fire": 0,
                        "weapon_code": "WeapHK416_C",
                        "weapon_name_ko": "M416",
                        "attachment_codes": ["Item_Attach_Weapon_Upper_DotSight_01_C"],
                        "attachment_names_ko": ["Red Dot Sight"],
                        "weapon_context_source": "attack",
                        "opponent_weapon_code": "WeapAK47_C",
                        "opponent_weapon_name_ko": "AKM",
                        "is_headshot": 0,
                        "distance_m": 25.0,
                    },
                    {
                        "match_id": "match-2",
                        "event_index": 70,
                        "event_at_kst": datetime(2026, 8, 1, 9, 0, 0),
                        "map_name": "Desert_Main",
                        "game_mode": "squad-fpp",
                        "outcome_type": "loss",
                        "outcome_reason": "death",
                        "opponent_account_id": "account.enemy2",
                        "opponent_is_bot": 0,
                        "is_friendly_fire": 0,
                        "weapon_code": "WeapHK416_C",
                        "weapon_name_ko": "M416",
                        "attachment_codes": ["Item_Attach_Weapon_Upper_DotSight_01_C"],
                        "attachment_names_ko": ["Red Dot Sight"],
                        "weapon_context_source": "attack",
                        "opponent_weapon_code": "WeapBerylM762_C",
                        "opponent_weapon_name_ko": "Beryl M762",
                        "is_headshot": 1,
                        "distance_m": 40.0,
                    },
                ],
            ]
        )

        with patch("pubg_ai.web.app.connect_mysql", return_value=connection):
            response = TestClient(create_app()).get(
                "/players/fight-outcomes?shard=steam&name=Yuuki_Asuna---"
            )

        self.assertEqual(response.status_code, 200)
        report = response.json()["fight_outcomes"]
        self.assertEqual(report["totals"]["wins"], 1)
        self.assertEqual(report["totals"]["losses"], 1)
        self.assertEqual(report["totals"]["dbno_wins"], 1)
        self.assertEqual(report["weapons"][0]["weapon_code"], "WeapHK416_C")
        self.assertEqual(report["loadouts"][0]["fight_count"], 2)
        self.assertTrue(connection.closed)

    def test_process_endpoint_passes_limit_and_force_to_processor(self) -> None:
        connection = FakeConnection([])
        processor = FakeProcessor()

        with (
            patch("pubg_ai.web.app.connect_mysql", return_value=connection),
            patch("pubg_ai.web.app.FightOutcomeProcessor", return_value=processor),
        ):
            response = TestClient(create_app()).post(
                "/telemetry/fight-outcomes/process",
                json={"limit": 37, "force": True},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(processor.calls, [(37, True)])
        self.assertEqual(response.json()["result"]["generated_wins"], 4)
        self.assertTrue(connection.closed)

    def test_local_manager_contains_fight_controls_and_profile_fetch(self) -> None:
        response = TestClient(create_app()).get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn('name="fight_outcome_limit"', response.text)
        self.assertIn("generateFightOutcomes(false)", response.text)
        self.assertIn("/players/fight-outcomes?", response.text)
        self.assertIn("교전 승/패", response.text)
        self.assertIn("총기 순위 제외", response.text)


class FakeResult:
    def to_record(self) -> dict[str, int]:
        return {
            "parsed_payloads": 1,
            "tracked_players": 1,
            "generated_outcomes": 7,
            "generated_wins": 4,
            "generated_losses": 3,
            "generated_loadout_snapshots": 7,
            "failed_payloads": 0,
        }


class FakeProcessor:
    def __init__(self) -> None:
        self.calls: list[tuple[int, bool]] = []

    def process_raw_telemetry(self, *, limit: int, force: bool) -> FakeResult:
        self.calls.append((limit, force))
        return FakeResult()


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

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, query: str, params: object = None) -> None:
        return None

    def fetchone(self) -> object:
        return self.connection.results.pop(0)

    def fetchall(self) -> object:
        return self.connection.results.pop(0)


if __name__ == "__main__":
    unittest.main()
