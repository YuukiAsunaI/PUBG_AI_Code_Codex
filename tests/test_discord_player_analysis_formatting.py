from __future__ import annotations

from datetime import datetime
import unittest

from pubg_ai.discord_bot import (
    format_match_explorer_detail,
    format_player_comparison,
    format_player_drop_zones,
    format_player_intelligence,
    format_player_time_insights,
)
from pubg_ai.player_intelligence import PlayerIntelligenceReport
from pubg_ai.player_recommendations import DropRegionStats, PlayerDropZoneReport
from pubg_ai.player_registry import RegisteredPlayer
from pubg_ai.player_trends import (
    PlayerTrendBucket,
    PlayerTrendFilters,
    PlayerTrendMetrics,
    PlayerTrendReport,
)


def _player(name: str = "Yuuki_Asuna---") -> RegisteredPlayer:
    return RegisteredPlayer(
        id=1,
        account_id=f"account.{name}",
        shard="steam",
        current_name=name,
        active=True,
        public_profile=True,
    )


def _metrics(*, matches: int = 12, wins: int = 3, kda: float = 2.5) -> PlayerTrendMetrics:
    return PlayerTrendMetrics(
        match_count=matches,
        wins=wins,
        non_wins=matches - wins,
        win_rate=wins / matches,
        kills=24,
        assists=6,
        deaths=12,
        kda=kda,
        dbnos_caused=18,
        dbnos_taken=9,
        damage_dealt=3600.0,
        damage_taken=2400.0,
        avg_damage_dealt=300.0,
        avg_damage_taken=200.0,
        shots_fired=2000,
        shots_hit=500,
        accuracy=0.25,
        headshot_kills=7,
        headshot_kill_rate=7 / 24,
        avg_survival_seconds=1200.0,
        avg_movement_distance_m=4200.0,
        headshot_hits=100,
        headshot_hit_rate=0.2,
        character_hits=500,
        vehicle_hits=20,
        fight_count=60,
        fight_wins=39,
        fight_losses=21,
        fight_win_rate=0.65,
        avg_fights_per_match=5.0,
        avg_kills=2.0,
    )


class DiscordPlayerAnalysisFormattingTests(unittest.TestCase):
    def test_intelligence_formatter_covers_full_analysis_and_commas(self) -> None:
        report = PlayerIntelligenceReport(
            player=_player(),
            filters=PlayerTrendFilters(map_name="Tiger_Main"),
            generated_at_kst="2026-09-03T12:00:00+09:00",
            coverage={
                "status": "complete",
                "total_matches": 1234,
                "telemetry_matches": 1234,
                "processed_matches": 1200,
                "coverage_rate": 1200 / 1234,
            },
            overview={
                "matches": 1234,
                "wins": 100,
                "win_rate": 100 / 1234,
                "kda": 2.45,
                "avg_damage_dealt": 241.2,
                "avg_fights_per_match": 4.1,
                "fight_win_rate": 0.59,
                "avg_total_distance_m": 5100.0,
            },
            combat={
                "kills_per_10_minutes": 1.4,
                "damage_per_fight": 59.4,
                "shots_fired": 72500,
                "shots_hit": 18000,
                "character_hits": 17500,
                "vehicle_hits": 500,
                "headshot_hit_rate": 0.18,
                "dbnos_caused": 1450,
                "dbnos_taken": 900,
                "damage_dealt": 297650.0,
                "damage_taken": 223000.0,
                "damage_ratio": 1.33,
            },
            survival={
                "avg_survival_seconds": 1140.0,
                "top10": 530,
                "top10_rate": 530 / 1234,
                "avg_placement": 17.2,
                "longest_kill_m": 812.0,
            },
            support={
                "avg_heal_amount": 89.2,
                "avg_throwable_uses": 2.3,
                "revives_caused": 340,
                "revives_received": 291,
            },
            loot={
                "avg_pickups_per_match": 35.5,
                "avg_uses_per_match": 8.4,
                "top_used_items": [{"item_name_ko": "구급상자", "used_events": 1400}],
            },
            mobility={},
            vehicle={"rides": 820},
            environment={},
            advanced={
                "fights": {
                    "self_opening_rate": 0.52,
                    "self_first_hit_rate": 0.48,
                    "third_party_rate": 0.11,
                    "avg_distance_m": 67.0,
                },
                "team": {
                    "avg_nearest_teammate_distance_m": 31.0,
                    "isolated_seconds_per_match": 42.0,
                    "trade_rate": 0.36,
                    "avg_revive_latency_seconds": 7.2,
                },
                "loot_readiness": {
                    "avg_readiness_score": 78.0,
                    "ready_before_first_fight_rate": 0.72,
                    "full_primary_loadout_rate": 0.69,
                    "avg_seconds_to_first_fight": 185.0,
                },
                "change_signals": [],
            },
            breakdowns={},
            trends={},
            activity_details={},
            metric_definitions=[],
        )

        body = format_player_intelligence(report)

        self.assertIn("종합 분석", body)
        self.assertIn("1,234경기", body)
        self.assertIn("차량 명중 500회", body)
        self.assertIn("교전 판단", body)
        self.assertIn("팀 합류", body)
        self.assertIn("첫 교전 준비", body)
        self.assertIn("태이고", body)

    def test_time_comparison_drop_and_match_detail_are_readable(self) -> None:
        buckets = [
            PlayerTrendBucket(
                period_key=f"{hour:02d}",
                period_label=f"{hour:02d}시",
                first_match_at_kst=datetime(2026, 8, 1, hour),
                last_match_at_kst=datetime(2026, 8, 2, hour),
                metrics=_metrics(matches=10 + hour, wins=2, kda=2.0 + hour / 10),
            )
            for hour in (20, 21)
        ]
        report = PlayerTrendReport(
            player=_player(),
            granularity="hour",
            timezone="Asia/Seoul",
            filters=PlayerTrendFilters(),
            totals=_metrics(matches=41, wins=4),
            buckets=buckets,
            available_bucket_count=2,
            truncated=False,
        )
        time_body = format_player_time_insights(report)
        comparison_body = format_player_comparison(
            [("Yuuki_Asuna---", _metrics()), ("Teammate", _metrics(kda=3.2))],
            title="등록 유저 성과 비교",
            metric="kda",
            filters=PlayerTrendFilters(),
        )
        drop_body = format_player_drop_zones(
            PlayerDropZoneReport(
                player=_player(),
                min_matches=3,
                regions=[
                    DropRegionStats(
                        map_name="Tiger_Main",
                        map_name_ko="태이고",
                        region_id="terminal-market",
                        region_name_ko="터미널 서쪽 시장",
                        x_pct=0.2,
                        y_pct=0.3,
                        centroid_x_cm=100.0,
                        centroid_y_cm=200.0,
                        match_count=15,
                        wins=3,
                        kills=24,
                        assists=8,
                        dbnos=19,
                        deaths=12,
                        damage_dealt=4200.0,
                        avg_survival_seconds=1100.0,
                        win_rate=0.2,
                        score=10.0,
                        zone_count=2,
                    )
                ],
                zones=[],
            )
        )
        match_body = format_match_explorer_detail(
            {
                "match": {
                    "map_label": "태이고",
                    "created_at_kst": "2026-08-20T21:10:00+09:00",
                    "game_mode_label": "스쿼드 1인칭",
                    "match_type_label": "일반 매치",
                    "perspective_label": "1인칭",
                },
                "summary": {
                    "participants": 100,
                    "humans": 96,
                    "bots": 4,
                    "teams": 25,
                    "kills": 99,
                    "assists": 30,
                    "damage_dealt": 25432.5,
                    "registered_players": 2,
                    "registered_player_names": ["Yuuki_Asuna---", "Teammate"],
                },
                "participants": [
                    {
                        "name": "Yuuki_Asuna---",
                        "win_place": 1,
                        "kills": 7,
                        "assists": 2,
                        "damage_dealt": 820.5,
                        "is_ai_or_bot": False,
                        "is_registered": True,
                    }
                ],
                "telemetry": {"available": True, "event_count": 125000},
            }
        )

        self.assertIn("KST 시간대 분석", time_body)
        self.assertIn("교전 65.0%", time_body)
        self.assertIn("#1 Teammate", comparison_body)
        self.assertIn("터미널 서쪽 시장", drop_body)
        self.assertIn("총 **100명** (사람 96명 · 봇 4명)", match_body)
        self.assertIn("이벤트 125,000건", match_body)


if __name__ == "__main__":
    unittest.main()
