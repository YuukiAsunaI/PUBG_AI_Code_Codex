from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest

from pubg_ai.replay_storage import ReplayArtifactStore
from pubg_ai.replay_timeline_builder import (
    ReplayTimelineProcessor,
    _apply_timeline_clock,
    _assign_position_segments,
    _build_timeline_anchors,
    _derive_drop_starts,
    _derive_engagements,
)


class ReplayTimelineProcessorTests(unittest.TestCase):
    def test_engagements_count_firearm_and_throwable_attacks_separately(self) -> None:
        events = [
            {
                "actor_account_id": "account.tracked",
                "actor_name": "Tracked",
                "actor_is_self": True,
                "action": "shot",
                "time_seconds": 10.0,
                "event_index": 1,
                "map": {"x_pct": 0.1, "y_pct": 0.2},
            },
            {
                "actor_account_id": "account.tracked",
                "actor_name": "Tracked",
                "actor_is_self": True,
                "action": "throw",
                "time_seconds": 12.0,
                "event_index": 2,
                "map": {"x_pct": 0.2, "y_pct": 0.3},
            },
        ]

        engagements = _derive_engagements(events)

        self.assertEqual(len(engagements), 1)
        self.assertEqual(engagements[0]["shots"], 1)
        self.assertEqual(engagements[0]["throws"], 1)
        self.assertEqual(engagements[0]["evidence"], "inferred_attack_activity")
        self.assertEqual(engagements[0]["opponent_count"], 0)

    def test_engagements_exclude_environmental_damage_and_require_opponent_for_hits(self) -> None:
        events = [
            {
                "actor_account_id": "account.tracked",
                "actor_name": "Tracked",
                "actor_is_self": True,
                "action": "hit_caused",
                "time_seconds": 9.0,
                "event_index": 0,
                "weapon_label": "M416",
                "related_account_id": "account.teammate",
                "map": {"x_pct": 0.1, "y_pct": 0.2},
            },
            {
                "actor_account_id": "account.tracked",
                "actor_name": "Tracked",
                "actor_is_self": True,
                "action": "hit_taken",
                "time_seconds": 10.0,
                "event_index": 1,
                "damage_causer_label": "블루존",
                "damage_type_category": "Damage_BlueZone",
                "related_account_id": None,
                "map": {"x_pct": 0.1, "y_pct": 0.2},
            },
            {
                "actor_account_id": "account.tracked",
                "actor_name": "Tracked",
                "actor_is_self": True,
                "action": "shot",
                "time_seconds": 11.0,
                "event_index": 2,
                "weapon_label": "M416",
                "related_account_id": None,
                "map": {"x_pct": 0.2, "y_pct": 0.3},
            },
            {
                "actor_account_id": "account.tracked",
                "actor_name": "Tracked",
                "actor_is_self": True,
                "action": "hit_caused",
                "time_seconds": 12.0,
                "event_index": 3,
                "weapon_label": "M416",
                "related_account_id": "account.enemy",
                "map": {"x_pct": 0.2, "y_pct": 0.3},
            },
            {
                "actor_account_id": "account.tracked",
                "actor_name": "Tracked",
                "actor_is_self": True,
                "action": "finish",
                "time_seconds": 13.0,
                "event_index": 4,
                "damage_type_category": "Damage_BlueZone",
                "weapon_label": "블루존",
                "related_account_id": "account.enemy",
                "map": {"x_pct": 0.2, "y_pct": 0.3},
            },
            {
                "actor_account_id": "account.tracked",
                "actor_name": "Tracked",
                "actor_is_self": True,
                "action": "death",
                "time_seconds": 40.0,
                "event_index": 5,
                "damage_causer_label": "추락",
                "related_account_id": None,
                "map": {"x_pct": 0.4, "y_pct": 0.5},
            },
        ]

        engagements = _derive_engagements(
            events,
            team_account_ids={"account.tracked", "account.teammate"},
        )

        self.assertEqual(len(engagements), 1)
        self.assertEqual(engagements[0]["event_count"], 3)
        self.assertEqual(engagements[0]["hits_taken"], 0)
        self.assertEqual(engagements[0]["evidence"], "verified_opponent")
        self.assertEqual(engagements[0]["opponent_count"], 1)
        self.assertEqual(engagements[0]["opponent_account_ids"], ["account.enemy"])
        self.assertEqual(engagements[0]["weapons"], ["M416"])

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
                        "z": 30000.0,
                        "is_in_vehicle": 0,
                        "vehicle_type": None,
                        "vehicle_id": None,
                        "vehicle_unique_id": None,
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
                        "vehicle_type": "WheeledVehicle",
                        "vehicle_id": "Dacia_A_01_v2_C",
                        "vehicle_unique_id": 77,
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
                        "related_name": "EnemyPlayer",
                        "related_is_ai_or_bot": 0,
                        "related_registered": 1,
                        "related_registered_active": 1,
                        "related_registered_name": "EnemyRegistered",
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
                [
                    {
                        "event_index": 35,
                        "event_at_kst": datetime(2026, 6, 28, 9, 19, 0),
                        "common_is_game": 1.0,
                        "elapsed_time_seconds": 340.0,
                        "num_alive_players": 92,
                        "num_alive_teams": 28,
                        "safety_zone_x": 202000.0,
                        "safety_zone_y": 203000.0,
                        "safety_zone_z": 0.0,
                        "safety_zone_radius": 291000.0,
                        "poison_gas_warning_x": 250000.0,
                        "poison_gas_warning_y": 260000.0,
                        "poison_gas_warning_z": 0.0,
                        "poison_gas_warning_radius": 120000.0,
                        "red_zone_x": 300000.0,
                        "red_zone_y": 310000.0,
                        "red_zone_z": 0.0,
                        "red_zone_radius": 40000.0,
                        "black_zone_x": None,
                        "black_zone_y": None,
                        "black_zone_z": None,
                        "black_zone_radius": None,
                    }
                ],
                [
                    {
                        "account_id": "account.tracked",
                        "name": "Yuuki_Asuna---",
                        "roster_id": "roster-1",
                        "team_id": 12,
                        "win_place": 1,
                        "kills": 5,
                        "assists": 1,
                        "damage_dealt": 650.0,
                        "death_type": "alive",
                        "is_ai_or_bot": 0,
                        "registered": 1,
                        "registered_active": 1,
                        "public_profile": 1,
                        "registered_name": "Yuuki_Asuna---",
                        "is_self": 1,
                    },
                    {
                        "account_id": "account.teammate",
                        "name": "Teammate",
                        "roster_id": "roster-1",
                        "team_id": 12,
                        "win_place": 1,
                        "kills": 2,
                        "assists": 3,
                        "damage_dealt": 310.5,
                        "death_type": "alive",
                        "is_ai_or_bot": 0,
                        "registered": 1,
                        "registered_active": 1,
                        "public_profile": 1,
                        "registered_name": "TrackedMate",
                        "is_self": 0,
                    },
                ],
                [
                    {
                        "event_index": 12,
                        "event_at_kst": datetime(2026, 6, 28, 9, 14, 5),
                        "common_is_game": 0.2,
                        "elapsed_time_seconds": 48.0,
                        "num_alive_players": 98,
                        "x": 101000.0,
                        "y": 201000.0,
                        "z": 2800.0,
                        "is_in_vehicle": 0,
                        "vehicle_type": None,
                        "vehicle_id": None,
                        "vehicle_unique_id": None,
                        "is_in_blue_zone": 0,
                        "is_in_red_zone": 0,
                        "in_special_zone": None,
                        "is_dbno": 0,
                    },
                    {
                        "event_index": 22,
                        "event_at_kst": datetime(2026, 6, 28, 9, 15, 5),
                        "common_is_game": 1.0,
                        "elapsed_time_seconds": 108.0,
                        "num_alive_players": 96,
                        "x": 121000.0,
                        "y": 221000.0,
                        "z": 0.0,
                        "is_in_vehicle": 0,
                        "vehicle_type": None,
                        "vehicle_id": None,
                        "vehicle_unique_id": None,
                        "is_in_blue_zone": 0,
                        "is_in_red_zone": 0,
                        "in_special_zone": None,
                        "is_dbno": 0,
                    },
                ],
                [],
                [
                    {
                        "related_account_id": "account.enemy-two",
                        "related_name": "EnemyTwo",
                        "related_is_ai_or_bot": 0,
                        "related_registered": 0,
                        "related_registered_active": None,
                        "related_registered_name": None,
                        "event_index": 24,
                        "event_type": "LogPlayerAttack",
                        "action": "shot",
                        "event_at_kst": datetime(2026, 6, 28, 9, 16, 0),
                        "common_is_game": 1.0,
                        "damage_type_category": "Damage_Gun",
                        "damage_causer_name": "WeapBerylM762_C",
                        "damage_reason": None,
                        "is_headshot": 0,
                        "distance_m": None,
                        "x": 125000.0,
                        "y": 225000.0,
                        "z": 0.0,
                        "related_x": None,
                        "related_y": None,
                        "related_z": None,
                        "raw_event": {
                            "attackId": 501,
                            "attackType": "Weapon",
                            "fireWeaponStackCount": 3,
                            "weapon": {"itemId": "Item_Weapon_BerylM762_C"},
                        },
                    },
                    {
                        "related_account_id": "account.enemy-two",
                        "related_name": "EnemyTwo",
                        "related_is_ai_or_bot": 0,
                        "related_registered": 0,
                        "related_registered_active": None,
                        "related_registered_name": None,
                        "event_index": 25,
                        "event_type": "LogPlayerMakeGroggy",
                        "action": "dbno_caused",
                        "event_at_kst": datetime(2026, 6, 28, 9, 16, 5),
                        "common_is_game": 1.0,
                        "damage_type_category": "Damage_Gun",
                        "damage_causer_name": "WeapBerylM762_C",
                        "damage_reason": "TorsoShot",
                        "is_headshot": 0,
                        "distance_m": 42.0,
                        "x": 126000.0,
                        "y": 226000.0,
                        "z": 0.0,
                        "related_x": 129000.0,
                        "related_y": 229000.0,
                        "related_z": 0.0,
                        "raw_event": {"attackId": 501},
                    },
                ],
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
            self.assertIn(artifact.sha256, Path(artifact.relative_path).name)
            path = Path(temp_dir) / artifact.relative_path
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(payload["schema_version"], "player-timeline-v13")
        self.assertEqual(payload["time_basis"], "telemetry_elapsed_time_with_piecewise_timestamp_interpolation")
        self.assertEqual(payload["clock"]["anchor_count"], 4)
        self.assertEqual(payload["clock"]["interpolation"], "piecewise-linear")
        self.assertTrue(payload["time_origin_at_kst"].startswith("2026-06-28T09:13:17"))
        self.assertEqual(payload["match"]["match_id"], "match-1")
        self.assertEqual(payload["player"]["account_id"], "account.tracked")
        self.assertEqual(payload["player"]["name"], "Yuuki_Asuna---")
        self.assertEqual(payload["team"]["member_count"], 2)
        self.assertEqual(payload["team"]["registered_member_count"], 2)
        self.assertEqual(payload["team"]["registered_teammate_count"], 1)
        self.assertEqual(payload["team"]["track_count"], 1)
        self.assertEqual(payload["team"]["position_sample_count"], 4)
        self.assertTrue(payload["team"]["members"][0]["is_self"])
        self.assertEqual(payload["team"]["members"][0]["position_sample_count"], 2)
        self.assertEqual(payload["team"]["members"][1]["name"], "TrackedMate")
        self.assertEqual(payload["team"]["members"][1]["position_sample_count"], 2)
        self.assertEqual(payload["team"]["members"][1]["combat_event_count"], 2)
        self.assertEqual(payload["counts"]["positions"], 2)
        self.assertEqual(payload["counts"]["team_tracks"], 1)
        self.assertEqual(payload["counts"]["team_position_samples"], 2)
        self.assertEqual(payload["counts"]["team_combat_events"], 2)
        self.assertEqual(payload["counts"]["combat_events"], 1)
        self.assertEqual(payload["counts"]["engagements"], 2)
        self.assertEqual(payload["counts"]["phase_events"], 1)
        self.assertEqual(payload["positions"][0]["map"]["x_pct"], 100000.0 / 816000.0)
        self.assertEqual(payload["positions"][0]["time_seconds"], 43.0)
        self.assertEqual(payload["positions"][1]["time_seconds"], 103.0)
        self.assertEqual(payload["positions"][0]["movement_mode"], "airborne")
        self.assertEqual(payload["positions"][1]["movement_mode"], "vehicle")
        self.assertEqual(payload["positions"][1]["vehicle_label"], "Dacia A 01 v2")
        self.assertEqual(payload["landings"][0]["time_seconds"], 88.0)
        position_query = next(
            query
            for query, _ in connection.executed
            if "FROM player_position_samples" in query and "elapsed_time_seconds" in query
        )
        self.assertIn("common_is_game > 0", position_query)
        self.assertNotIn("COALESCE(is_in_vehicle, 0)", position_query)
        self.assertEqual(payload["team_tracks"][0]["name"], "TrackedMate")
        self.assertEqual(payload["team_tracks"][0]["positions"][0]["map"]["x_pct"], 101000.0 / 816000.0)
        self.assertEqual(payload["team_tracks"][0]["positions"][1]["movement_mode"], "on_foot")
        self.assertEqual(payload["team_tracks"][0]["combat_events"][0]["action"], "shot")
        self.assertEqual(payload["team_tracks"][0]["combat_events"][0]["attack_id"], 501)
        self.assertEqual(payload["team_tracks"][0]["combat_events"][0]["weapon_label"], "베릴 M762")
        self.assertFalse(payload["team_tracks"][0]["combat_events"][0]["has_verified_direction"])
        self.assertTrue(payload["team_tracks"][0]["combat_events"][1]["has_verified_direction"])
        teammate_engagement = next(
            item for item in payload["engagements"] if item["actor_account_id"] == "account.teammate"
        )
        self.assertEqual(teammate_engagement["outcome"], "won")
        self.assertEqual(teammate_engagement["shots"], 1)
        self.assertEqual(teammate_engagement["dbnos_caused"], 1)
        self.assertEqual(payload["combat_events"][0]["damage_causer_label"], "M416")
        self.assertEqual(payload["combat_events"][0]["time_seconds"], 283.0)
        self.assertEqual(payload["combat_events"][0]["related_name"], "EnemyRegistered")
        self.assertTrue(payload["combat_events"][0]["related_registered"])
        self.assertEqual(payload["care_packages"][0]["item_codes"], ["Item_Weapon_AWM_C"])
        self.assertEqual(payload["care_packages"][0]["time_seconds"], 403.0)
        self.assertEqual(payload["plane_route"]["start_time_seconds"], 3.0)
        self.assertEqual(payload["plane_route"]["end_time_seconds"], 43.0)
        self.assertEqual(payload["phase_events"][0]["elapsed_time_seconds"], 340.0)
        self.assertEqual(payload["phase_events"][0]["time_seconds"], 340.0)
        self.assertEqual(payload["phase_events"][0]["num_alive_teams"], 28)
        self.assertEqual(payload["phase_events"][0]["safety_zone"]["radius_m"], 2910.0)
        self.assertEqual(payload["phase_events"][0]["safety_zone"]["map"]["x_pct"], 202000.0 / 816000.0)
        self.assertEqual(payload["phase_events"][0]["poison_gas_warning"]["radius_m"], 1200.0)
        self.assertEqual(payload["phase_events"][0]["red_zone"]["radius_m"], 400.0)
        self.assertIsNone(payload["phase_events"][0]["black_zone"])
        self.assertIn("INSERT INTO replay_artifacts", connection.executed[-1][0])
        self.assertIn("player-timeline", connection.executed[-1][1])

    def test_piecewise_clock_places_events_between_neighbouring_position_samples(self) -> None:
        positions = [
            {
                "event_at_kst": "2026-08-21T04:36:51+09:00",
                "elapsed_time_seconds": 47.0,
            },
            {
                "event_at_kst": "2026-08-21T04:37:31+09:00",
                "elapsed_time_seconds": 86.0,
            },
        ]
        anchors = _build_timeline_anchors(preferred_events=positions, fallback_events=[])
        events = [{"event_index": 10, "event_at_kst": "2026-08-21T04:37:11+09:00"}]

        _apply_timeline_clock(events, anchors)

        self.assertAlmostEqual(events[0]["time_seconds"], 66.5)

    def test_team_combat_track_is_kept_without_position_samples(self) -> None:
        connection = FakeConnection(
            [
                [],
                [],
                [
                    {
                        "event_index": 9,
                        "event_type": "LogPlayerAttack",
                        "action": "shot",
                        "event_at_kst": datetime(2026, 6, 28, 9, 14, 0),
                        "common_is_game": 1.0,
                        "damage_causer_name": "WeapHK416_C",
                        "is_headshot": 0,
                        "x": 1000.0,
                        "y": 2000.0,
                        "z": 0.0,
                        "raw_event": {"attackId": 77, "attackType": "Weapon"},
                    }
                ],
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            processor = ReplayTimelineProcessor(connection, ReplayArtifactStore(Path(temp_dir)))
            tracks = processor._load_team_position_tracks(
                match_id="match-1",
                tracked_account_id="account.self",
                team_members=[
                    {
                        "account_id": "account.mate",
                        "name": "Mate",
                        "registered": False,
                        "is_ai_or_bot": False,
                    }
                ],
                shard="steam",
                world_size_cm=816000.0,
                plane_route=None,
            )

        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]["sample_count"], 0)
        self.assertEqual(tracks[0]["combat_events"][0]["attack_id"], 77)
        self.assertFalse(tracks[0]["combat_events"][0]["actor_is_self"])

    def test_position_segments_break_on_gap_and_implausible_jump(self) -> None:
        positions = [
            {"event_index": 1, "time_seconds": 1.0, "x": 1000.0, "y": 1000.0, "z": 0.0},
            {"event_index": 2, "time_seconds": 2.0, "x": 1100.0, "y": 1100.0, "z": 0.0},
            {"event_index": 3, "time_seconds": 52.0, "x": 1200.0, "y": 1200.0, "z": 0.0},
            {"event_index": 4, "time_seconds": 53.0, "x": 250000.0, "y": 250000.0, "z": 0.0},
        ]

        _assign_position_segments(positions)

        self.assertEqual([row["segment_id"] for row in positions], [0, 0, 1, 2])
        self.assertEqual(positions[2]["segment_start_reason"], "sample_gap")
        self.assertEqual(positions[3]["segment_start_reason"], "position_jump")

    def test_drop_start_is_first_airborne_sample_per_path_segment(self) -> None:
        positions = [
            {"event_index": 1, "segment_id": 0, "z": 140000.0, "is_in_vehicle": False},
            {"event_index": 2, "segment_id": 0, "z": 90000.0, "is_in_vehicle": False},
            {"event_index": 3, "segment_id": 1, "z": 0.0, "is_in_vehicle": False},
            {"event_index": 4, "segment_id": 2, "z": 50000.0, "is_in_vehicle": False},
        ]

        starts = _derive_drop_starts(positions)

        self.assertEqual([row["event_index"] for row in starts], [1, 4])

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
                [],
                [],
            ]
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            result = ReplayTimelineProcessor(
                connection,
                ReplayArtifactStore(Path(temp_dir)),
            ).generate_player_timelines(limit=10)

        self.assertEqual(result.generated_timelines, 0)
        self.assertEqual(result.skipped_no_position, 1)
        candidate_query = connection.executed[0][0]
        self.assertIn("FROM player_position_samples candidate_positions", candidate_query)
        self.assertIn("candidate_positions.common_is_game > 0", candidate_query)


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
