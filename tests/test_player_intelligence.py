from __future__ import annotations

from datetime import datetime
import unittest

from pubg_ai.player_intelligence import (
    MATCH_ID_QUERY_CHUNK_SIZE,
    _chunks,
    _merge_activity_detail_rows,
    _merge_item_rows,
    summarize_advanced_analysis,
    summarize_player_intelligence,
)
from pubg_ai.telemetry_activity_processor import PARSER_VERSION as ACTIVITY_PARSER_VERSION


class PlayerIntelligenceTests(unittest.TestCase):
    def test_aggregates_detailed_metrics_with_correct_denominators(self) -> None:
        rows = [
            _row(
                match_id="match-1",
                created_at_kst=datetime(2026, 8, 21, 21, 0),
                win_place=1,
                map_name="Baltic_Main",
                kills=3,
                assists=1,
                deaths=0,
                shots_fired=10,
                shots_hit=5,
                headshot_hits=2,
                damage_dealt=400,
                damage_taken=150,
                heal_amount=60,
                revives_caused=1,
                throwable_uses=3,
                raw_stats={
                    "timeSurvived": 1800,
                    "walkDistance": 2000,
                    "rideDistance": 1000,
                    "swimDistance": 10,
                    "heals": 2,
                    "boosts": 3,
                    "killPlace": 1,
                    "longestKill": 250,
                },
            ),
            _row(
                match_id="match-2",
                created_at_kst=datetime(2026, 8, 22, 1, 0),
                win_place=20,
                map_name="Baltic_Main",
                kills=1,
                assists=0,
                deaths=1,
                shots_fired=10,
                shots_hit=3,
                headshot_hits=1,
                damage_dealt=100,
                damage_taken=100,
                heal_amount=20,
                revives_caused=0,
                throwable_uses=1,
                raw_stats={
                    "timeSurvived": 600,
                    "walkDistance": 1000,
                    "rideDistance": 0,
                    "swimDistance": 0,
                    "heals": 1,
                    "boosts": 0,
                    "killPlace": 12,
                    "longestKill": 50,
                },
            ),
        ]
        fights = [
            {"match_id": "match-1", "fight_count": 3, "fight_wins": 2, "fight_losses": 1},
            {"match_id": "match-2", "fight_count": 1, "fight_wins": 0, "fight_losses": 1},
        ]

        report = summarize_player_intelligence(rows, fight_rows=fights)

        self.assertEqual(report["survival"]["win_rate"], 0.5)
        self.assertEqual(report["survival"]["top10_rate"], 0.5)
        self.assertEqual(report["combat"]["accuracy"], 0.4)
        self.assertEqual(report["combat"]["headshot_hit_rate"], 3 / 8)
        self.assertEqual(report["combat"]["fight_win_rate"], 0.5)
        self.assertEqual(report["combat"]["avg_fights_per_match"], 2.0)
        self.assertEqual(report["combat"]["damage_ratio"], 2.0)
        self.assertEqual(report["combat"]["kills_per_10_minutes"], 1.0)
        self.assertEqual(report["combat"]["fights_per_10_minutes"], 1.0)
        self.assertEqual(report["combat"]["damage_per_10_minutes"], 125.0)
        self.assertEqual(report["combat"]["damage_per_fight"], 125.0)
        self.assertEqual(report["support"]["heal_amount"], 80)
        self.assertEqual(report["mobility"]["total_distance_m"], 4010)
        self.assertEqual(len(report["trends"]["daily"]), 2)
        self.assertEqual(len(report["trends"]["monthly"]), 1)
        self.assertEqual(report["breakdowns"]["maps"][0]["matches"], 2)
        self.assertEqual(report["trends"]["monthly"][0]["damage_per_10_minutes"], 125.0)

    def test_tempo_metrics_cap_survival_at_match_duration(self) -> None:
        report = summarize_player_intelligence(
            [
                _row(
                    kills=2,
                    damage_dealt=300,
                    duration_seconds=600,
                    raw_stats={"timeSurvived": 999999},
                )
            ],
            fight_rows=[
                {
                    "match_id": "match",
                    "fight_count": 2,
                    "fight_wins": 2,
                    "fight_losses": 0,
                }
            ],
        )

        self.assertEqual(report["combat"]["total_survival_seconds"], 600)
        self.assertEqual(report["combat"]["kills_per_10_minutes"], 2)
        self.assertEqual(report["combat"]["damage_per_10_minutes"], 300)

    def test_activity_averages_only_use_current_parser_coverage(self) -> None:
        rows = [
            _row(
                match_id="processed",
                activity_parser_version=ACTIVITY_PARSER_VERSION,
                heal_amount=100,
                item_heal_amount=70,
                passive_heal_amount=30,
                throwable_uses=4,
                revives_caused=1,
            ),
            _row(
                match_id="stale",
                activity_parser_version="activity-v1",
                heal_amount=999,
                item_heal_amount=999,
                passive_heal_amount=999,
                throwable_uses=99,
                revives_caused=99,
            ),
        ]

        report = summarize_player_intelligence(rows)

        self.assertEqual(report["support"]["activity_covered_matches"], 1)
        self.assertEqual(report["support"]["activity_coverage_rate"], 0.5)
        self.assertEqual(report["support"]["heal_amount"], 100)
        self.assertEqual(report["support"]["avg_heal_amount"], 100)
        self.assertEqual(report["support"]["avg_throwable_uses"], 4)
        self.assertEqual(report["trends"]["monthly"][0]["activity_covered_matches"], 1)
        self.assertEqual(report["trends"]["monthly"][0]["avg_heal_amount"], 100)

    def test_activity_average_is_unavailable_without_current_parser_rows(self) -> None:
        report = summarize_player_intelligence(
            [_row(activity_parser_version="activity-v1", heal_amount=50)]
        )

        self.assertEqual(report["support"]["heal_amount"], 0)
        self.assertIsNone(report["support"]["avg_heal_amount"])
        self.assertIsNone(report["trends"]["daily"][0]["avg_heal_amount"])

    def test_large_match_queries_are_chunked_without_losing_ids(self) -> None:
        match_ids = [f"match-{index}" for index in range(MATCH_ID_QUERY_CHUNK_SIZE + 3)]

        chunks = list(_chunks(match_ids))

        self.assertEqual([len(chunk) for chunk in chunks], [MATCH_ID_QUERY_CHUNK_SIZE, 3])
        self.assertEqual([match_id for chunk in chunks for match_id in chunk], match_ids)

    def test_batch_aggregates_merge_item_and_activity_groups(self) -> None:
        item_rows = _merge_item_rows(
            [
                {"item_code": "Item_A", "item_name_ko": "아이템", "picked_up_events": 2, "used_events": 1},
                {"item_code": "Item_A", "item_name_ko": "아이템", "picked_up_events": 3, "used_events": 4},
            ]
        )
        activity_rows = _merge_activity_detail_rows(
            [
                {"action": "vehicle_damage_caused", "vehicle_type": "Dacia", "event_count": 2, "damage": 30, "max_speed": 40},
                {"action": "vehicle_damage_caused", "vehicle_type": "Dacia", "event_count": 3, "damage": 45, "max_speed": 60},
            ]
        )

        self.assertEqual(item_rows[0]["picked_up_events"], 5)
        self.assertEqual(item_rows[0]["used_events"], 5)
        self.assertEqual(activity_rows[0]["event_count"], 5)
        self.assertEqual(activity_rows[0]["damage"], 75)
        self.assertEqual(activity_rows[0]["max_speed"], 60)

    def test_advanced_analysis_builds_reviews_and_detects_sustained_change(self) -> None:
        match_rows = []
        fight_rows = []
        zone_rows = []
        team_rows = []
        loot_rows = []
        for index in range(20):
            match_id = f"advanced-{index:02d}"
            improved = index >= 10
            match_rows.append(
                {
                    "match_id": match_id,
                    "created_at_kst": datetime(2026, 8, index + 1, 20, 0),
                    "map_name": "Baltic_Main",
                    "game_mode": "squad-fpp",
                }
            )
            fight_rows.append(
                {
                    "match_id": match_id,
                    "episode_index": 1,
                    "started_at_kst": datetime(2026, 8, index + 1, 20, 5),
                    "duration_seconds": 12,
                    "phase_number": 1,
                    "outcome": "win" if improved else "loss",
                    "opening_actor": "self" if improved else "opponent",
                    "first_hit_actor": "self" if improved else "opponent",
                    "primary_opponent_account_id": "enemy",
                    "opponent_count": 1,
                    "opponent_team_count": 1,
                    "shots_fired": 10,
                    "shots_hit": 5 if improved else 2,
                    "damage_dealt": 100 if improved else 30,
                    "damage_taken": 20 if improved else 100,
                    "dbnos_caused": 1 if improved else 0,
                    "dbnos_taken": 0 if improved else 1,
                    "kills": 1 if improved else 0,
                    "deaths": 0 if improved else 1,
                    "trade_opportunities": 0 if improved else 1,
                    "trade_successes": 0,
                    "is_third_party": False,
                    "weapon_codes": '["WeapHK416_C"]',
                    "opponent_weapon_codes": '["WeapAK47_C"]',
                    "avg_distance_m": 50,
                    "summary_reason": "교전 승리" if improved else "교전 패배",
                }
            )
            zone_rows.append(
                {
                    "match_id": match_id,
                    "phase_number": 1,
                    "sample_count": 3,
                    "late_entry_seconds": 5,
                    "blue_zone_exposure_seconds": 0 if improved else 60,
                    "outside_safe_zone_seconds": 0 if improved else 40,
                    "avg_center_distance_ratio": 0.5,
                    "rotation_distance_m": 1000,
                    "vehicle_distance_m": 500,
                    "dbnos_taken": 0 if improved else 1,
                    "deaths": 0 if improved else 1,
                }
            )
            team_rows.append(
                {
                    "match_id": match_id,
                    "sample_count": 3,
                    "avg_nearest_teammate_distance_m": 40 if improved else 200,
                    "avg_visible_teammates": 2,
                    "isolated_seconds": 0 if improved else 100,
                    "close_support_seconds": 30 if improved else 0,
                    "regroup_count": 1,
                    "trade_opportunities": 1,
                    "trade_successes": 1 if improved else 0,
                    "avg_revive_latency_seconds": 8,
                }
            )
            loot_rows.append(
                {
                    "match_id": match_id,
                    "second_primary_weapon_code": "Item_Weapon_M24_C",
                    "ready_before_first_fight": improved,
                    "readiness_score": 100 if improved else 50,
                    "seconds_to_first_primary_weapon": 15,
                    "seconds_to_second_primary_weapon": 45,
                    "seconds_to_first_fight": 90,
                    "pickup_events": 8,
                    "ground_pickups": 7,
                    "loot_box_pickups": 1,
                    "care_package_pickups": 0,
                }
            )

        report = summarize_advanced_analysis(
            match_rows=match_rows,
            fight_episodes=fight_rows,
            zone_phases=zone_rows,
            team_summaries=team_rows,
            loot_summaries=loot_rows,
        )

        self.assertEqual(report["fights"]["episodes"], 20)
        self.assertEqual(report["fights"]["win_rate"], 0.5)
        self.assertEqual(report["zone_rotation"]["covered_matches"], 20)
        self.assertEqual(report["team"]["trade_rate"], 0.5)
        self.assertEqual(report["loot_readiness"]["avg_readiness_score"], 75)
        self.assertEqual(len(report["death_reviews"]), 10)
        signals = {item["key"]: item for item in report["change_signals"]}
        self.assertTrue(signals["fight_win_rate"]["detected"])
        self.assertEqual(signals["fight_win_rate"]["direction"], "improved")
        self.assertEqual(signals["blue_exposure"]["direction"], "improved")
        self.assertEqual(signals["isolation"]["direction"], "improved")
        self.assertEqual(signals["readiness"]["direction"], "improved")


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "match_id": "match",
        "created_at_kst": datetime(2026, 8, 22, 0, 0),
        "win_place": 10,
        "map_name": "Baltic_Main",
        "game_mode": "squad",
        "team_mode": "squad",
        "perspective": "tpp",
        "match_type": "official",
        "kills": 0,
        "assists": 0,
        "deaths": 0,
        "shots_fired": 0,
        "shots_hit": 0,
        "hits_taken": 0,
        "headshot_hits": 0,
        "headshot_hits_taken": 0,
        "damage_dealt": 0,
        "damage_taken": 0,
        "dbnos_caused": 0,
        "dbnos_taken": 0,
        "fight_count": 0,
        "fight_wins": 0,
        "fight_losses": 0,
        "activity_parser_version": ACTIVITY_PARSER_VERSION,
        "raw_stats": {},
    }
    row.update(overrides)
    return row


if __name__ == "__main__":
    unittest.main()
