from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest

from pubg_ai.replay_storage import ReplayArtifactStore
from pubg_ai.replay_timeline_builder import ReplayTimelineProcessor


class ReplayTimelineProcessorTests(unittest.TestCase):
    def test_generates_player_timeline_json_artifact(self) -> None:
        connection = FakeConnection(
            [
                [
                    {
                        "match_id": "match-1",
                        "account_id": "account.tracked",
                        "shard": "steam",
                        "map_name": "Tiger_Main",
                        "game_mode": "squad",
                        "match_type": "official",
                        "created_at_kst": datetime(2026, 6, 28, 9, 13, 17),
                        "duration_seconds": 1800,
                        "current_name": "Yuuki_Asuna---",
                    }
                ],
                None,
                [
                    {
                        "event_index": 10,
                        "event_at_kst": datetime(2026, 6, 28, 9, 14, 0),
                        "common_is_game": 0.1,
                        "elapsed_time_seconds": 43.0,
                        "num_alive_players": 98,
                        "x": 100000.0,
                        "y": 200000.0,
                        "z": 3000.0,
                        "is_in_vehicle": 0,
                        "is_in_blue_zone": 0,
                        "is_in_red_zone": 0,
                        "in_special_zone": None,
                        "is_dbno": 0,
                    },
                    {
                        "event_index": 20,
                        "event_at_kst": datetime(2026, 6, 28, 9, 15, 0),
                        "common_is_game": 1.0,
                        "elapsed_time_seconds": 103.0,
                        "num_alive_players": 96,
                        "x": 120000.0,
                        "y": 220000.0,
                        "z": 0.0,
                        "is_in_vehicle": 1,
                        "is_in_blue_zone": 0,
                        "is_in_red_zone": 0,
                        "in_special_zone": None,
                        "is_dbno": 0,
                    },
                ],
                [
                    {
                        "event_index": 18,
                        "event_at_kst": datetime(2026, 6, 28, 9, 14, 45),
                        "common_is_game": 0.8,
                        "x": 115000.0,
                        "y": 215000.0,
                        "z": 0.0,
                        "distance_m": 730.0,
                    }
                ],
                [
                    {
                        "related_account_id": "account.enemy",
                        "event_index": 30,
                        "event_type": "LogPlayerKillV2",
                        "action": "kill",
                        "event_at_kst": datetime(2026, 6, 28, 9, 18, 0),
                        "common_is_game": 1.0,
                        "damage_type_category": "Damage_Gun",
                        "damage_causer_name": "WeapHK416_C",
                        "damage_reason": "HeadShot",
                        "is_headshot": 1,
                        "distance_m": 120.0,
                        "x": 150000.0,
                        "y": 250000.0,
                        "z": 0.0,
                        "related_x": 151000.0,
                        "related_y": 251000.0,
                        "related_z": 0.0,
                    }
                ],
                [
                    {
                        "event_index": 40,
                        "event_type": "LogCarePackageLand",
                        "event_at_kst": datetime(2026, 6, 28, 9, 20, 0),
                        "common_is_game": 1.0,
                        "item_package_id": "package-1",
                        "item_count": 2,
                        "item_codes": '["Item_Weapon_AWM_C"]',
                        "x": 300000.0,
                        "y": 300000.0,
                        "z": 0.0,
                    }
                ],
                {
                    "source": "aircraft",
                    "sample_count": 2,
                    "start_event_index": 1,
                    "end_event_index": 2,
                    "start_event_at_kst": datetime(2026, 6, 28, 9, 13, 20),
                    "end_event_at_kst": datetime(2026, 6, 28, 9, 14, 0),
                    "start_x": 50000.0,
                    "start_y": 50000.0,
                    "start_z": 0.0,
                    "end_x": 300000.0,
                    "end_y": 300000.0,
                    "end_z": 0.0,
                    "sample_account_id": "account.tracked",
                },
            ]
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            result = ReplayTimelineProcessor(
                connection,
                ReplayArtifactStore(Path(temp_dir)),
            ).generate_player_timelines(limit=10)

            self.assertEqual(result.generated_timelines, 1)
            self.assertEqual(result.failed_timelines, 0)
            artifact = result.artifacts[0]
            self.assertEqual(artifact.artifact_type, "timeline")
            path = Path(temp_dir) / artifact.relative_path
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(payload["schema_version"], "player-timeline-v1")
        self.assertEqual(payload["match"]["match_id"], "match-1")
        self.assertEqual(payload["player"]["name"], "Yuuki_Asuna---")
        self.assertEqual(payload["counts"]["positions"], 2)
        self.assertEqual(payload["counts"]["combat_events"], 1)
        self.assertEqual(payload["positions"][0]["map"]["x_pct"], 100000.0 / 816000.0)
        self.assertEqual(payload["combat_events"][0]["damage_causer_label"], "M416")
        self.assertEqual(payload["care_packages"][0]["item_codes"], ["Item_Weapon_AWM_C"])
        self.assertIn("INSERT INTO replay_artifacts", connection.executed[-1][0])
        self.assertIn("player-timeline", connection.executed[-1][1])

    def test_skips_timeline_without_positions(self) -> None:
        connection = FakeConnection(
            [
                [
                    {
                        "match_id": "match-1",
                        "account_id": "account.tracked",
                        "shard": "steam",
                        "map_name": "Tiger_Main",
                        "game_mode": "squad",
                        "match_type": "official",
                        "created_at_kst": datetime(2026, 6, 28, 9, 13, 17),
                        "duration_seconds": 1800,
                        "current_name": "Yuuki_Asuna---",
                    }
                ],
                None,
                [],
                [],
                [],
                [],
                None,
            ]
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            result = ReplayTimelineProcessor(
                connection,
                ReplayArtifactStore(Path(temp_dir)),
            ).generate_player_timelines(limit=10)

        self.assertEqual(result.generated_timelines, 0)
        self.assertEqual(result.skipped_no_position, 1)


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
