from __future__ import annotations

from datetime import date, datetime
import unittest

from pubg_ai.discord_bot import format_player_trends
from pubg_ai.player_registry import RegisteredPlayer
from pubg_ai.weapon_accuracy import summarize_accuracy_rows
from pubg_ai.player_trends import (
    PlayerTrendBucket,
    PlayerTrendFilters,
    PlayerTrendMetrics,
    PlayerTrendReport,
)


class DiscordPlayerTrendTests(unittest.TestCase):
    def test_formats_filtered_trends_and_local_link(self) -> None:
        metrics = PlayerTrendMetrics(
            match_count=10,
            wins=2,
            non_wins=8,
            win_rate=0.2,
            kills=15,
            assists=5,
            deaths=8,
            kda=2.5,
            dbnos_caused=12,
            dbnos_taken=7,
            damage_dealt=2000,
            damage_taken=1200,
            avg_damage_dealt=200,
            avg_damage_taken=120,
            shots_fired=1000,
            shots_hit=100,
            accuracy=0.1,
            headshot_kills=3,
            headshot_kill_rate=0.2,
            avg_survival_seconds=900,
            avg_movement_distance_m=2500,
            accuracy_breakdown=summarize_accuracy_rows(
                [{"weapon_code": "WeapHK416_C", "shots_fired": 1000, "shots_hit": 100}]
            ),
        )
        report = PlayerTrendReport(
            player=RegisteredPlayer(
                id=1,
                account_id="account.test",
                shard="steam",
                current_name="Player",
                active=True,
                public_profile=True,
                registered_by_discord_user_id=None,
                registered_guild_id="guild-1",
                registered_channel_id=None,
            ),
            granularity="month",
            timezone="Asia/Seoul",
            filters=PlayerTrendFilters(
                team_mode="squad",
                perspective="fpp",
                from_date_kst=date(2026, 7, 1),
            ),
            totals=metrics,
            buckets=[
                PlayerTrendBucket(
                    period_key=f"2026-{month:02d}",
                    period_label=f"2026년 {month:02d}월",
                    first_match_at_kst=datetime(2026, month, 1),
                    last_match_at_kst=datetime(2026, month, 2),
                    metrics=metrics,
                )
                for month in range(1, 9)
            ],
            available_bucket_count=8,
            truncated=False,
        )

        body = format_player_trends(report, detail_base_url="http://127.0.0.1:8018/")

        self.assertIn("Player KST 월별 추세 (스팀)", body)
        self.assertIn("10전 2치킨/8비치킨 (20.0%)", body)
        self.assertIn("일반 탄환 추정 10.0%", body)
        self.assertIn("팀 모드=스쿼드, 시점=1인칭, 시작일=2026-07-01", body)
        self.assertIn("2026년 01월: 10전 2치킨 20.0%", body)
        self.assertIn("2026년 08월: 10전 2치킨 20.0%", body)
        self.assertIn("#trend-lookup", body)

if __name__ == "__main__":
    unittest.main()
