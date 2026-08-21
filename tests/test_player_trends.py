from __future__ import annotations

from datetime import date, datetime
import unittest

from pubg_ai.player_trends import (
    PlayerTrendFilters,
    PlayerTrendService,
    normalize_trend_granularity,
    parse_optional_bool,
    parse_trend_date,
    summarize_player_trends,
)


class PlayerTrendSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = [
            self.row(
                created_at_kst=datetime(2026, 7, 27, 23, 10),
                win_place=2,
                kills=1,
                assists=1,
                deaths=1,
                damage_dealt=100,
                survival_seconds=900,
            ),
            self.row(
                created_at_kst=datetime(2026, 8, 1, 1, 20),
                win_place=1,
                kills=3,
                assists=0,
                deaths=0,
                damage_dealt=300,
                survival_seconds=1800,
            ),
            self.row(
                created_at_kst=datetime(2026, 8, 3, 18, 30),
                win_place=4,
                kills=0,
                assists=2,
                deaths=1,
                damage_dealt=50,
                survival_seconds=None,
                duration_seconds=600,
            ),
        ]

    def test_summarizes_month_metrics_using_kst_match_times(self) -> None:
        report = summarize_player_trends(self.rows, granularity="month")

        self.assertEqual(report.available_bucket_count, 2)
        self.assertEqual([bucket.period_key for bucket in report.buckets], ["2026-07", "2026-08"])
        self.assertEqual(report.totals.match_count, 3)
        self.assertEqual(report.totals.wins, 1)
        self.assertEqual(report.totals.non_wins, 2)
        self.assertEqual(report.totals.kills, 4)
        self.assertEqual(report.totals.assists, 3)
        self.assertEqual(report.totals.deaths, 2)
        self.assertEqual(report.totals.kda, 3.5)
        self.assertEqual(report.totals.avg_damage_dealt, 150.0)
        self.assertEqual(report.totals.avg_survival_seconds, 1100.0)
        self.assertAlmostEqual(report.totals.avg_kills, 4 / 3)
        self.assertEqual(report.totals.fight_count, 6)
        self.assertEqual(report.totals.fight_wins, 4)
        self.assertEqual(report.totals.fight_losses, 2)
        self.assertAlmostEqual(report.totals.fight_win_rate, 4 / 6)
        self.assertEqual(report.totals.avg_fights_per_match, 2.0)
        self.assertAlmostEqual(report.buckets[1].metrics.win_rate, 0.5)

    def test_accuracy_uses_single_projectile_events_and_keeps_pellet_evidence(self) -> None:
        row = self.row(
            created_at_kst=datetime(2026, 8, 1, 1, 20),
            win_place=1,
        )
        row["shots_fired"] = 110
        row["shots_hit"] = 120
        row["weapon_accuracy_rows"] = [
            {
                "weapon_code": "WeapHK416_C",
                "shots_fired": 100,
                "shots_hit": 30,
            },
            {
                "weapon_code": "WeapSaiga12_C",
                "shots_fired": 10,
                "shots_hit": 90,
            },
        ]

        report = summarize_player_trends([row], granularity="month")

        self.assertAlmostEqual(report.totals.accuracy, 0.3)
        self.assertEqual(report.totals.shots_fired, 110)
        self.assertEqual(report.totals.shots_hit, 120)
        self.assertEqual(report.totals.accuracy_breakdown.pellet_shells, 10)
        self.assertEqual(report.totals.accuracy_breakdown.pellet_hit_events, 90)
    def test_uses_iso_weeks_and_hour_of_day_buckets(self) -> None:
        weekly = summarize_player_trends(self.rows, granularity="week")
        hourly = summarize_player_trends(self.rows, granularity="시간대")

        self.assertEqual([bucket.period_key for bucket in weekly.buckets], ["2026-W31", "2026-W32"])
        self.assertEqual([bucket.period_key for bucket in hourly.buckets], ["01", "18", "23"])
        self.assertEqual(hourly.buckets[0].period_label, "01시")

    def test_groups_by_map_quarter_year_and_match_dimensions(self) -> None:
        rows = [
            {**self.rows[0], "map_name": "Erangel_Main", "team_mode": "squad"},
            {**self.rows[1], "map_name": "Tiger_Main", "team_mode": "squad"},
            {**self.rows[2], "map_name": "Erangel_Main", "team_mode": "duo"},
        ]

        maps = summarize_player_trends(rows, granularity="map")
        quarters = summarize_player_trends(rows, granularity="quarter")
        years = summarize_player_trends(rows, granularity="year")
        teams = summarize_player_trends(rows, granularity="team_mode")

        self.assertEqual([bucket.period_key for bucket in maps.buckets], ["Erangel_Main", "Tiger_Main"])
        self.assertEqual(maps.buckets[0].period_label, "에란겔")
        self.assertEqual([bucket.period_key for bucket in quarters.buckets], ["2026-Q3"])
        self.assertEqual([bucket.period_key for bucket in years.buckets], ["2026"])
        self.assertEqual([bucket.period_label for bucket in teams.buckets], ["듀오", "스쿼드"])

    def test_returns_most_recent_buckets_when_limit_truncates(self) -> None:
        report = summarize_player_trends(self.rows, granularity="date", bucket_limit=2)

        self.assertTrue(report.truncated)
        self.assertEqual(report.available_bucket_count, 3)
        self.assertEqual([bucket.period_key for bucket in report.buckets], ["2026-08-01", "2026-08-03"])
        self.assertEqual(report.totals.match_count, 3)

    def test_skips_rows_without_a_valid_kst_timestamp(self) -> None:
        report = summarize_player_trends(
            [*self.rows, self.row(created_at_kst=None, win_place=1)],
            granularity="date",
        )

        self.assertEqual(report.totals.match_count, 3)

    def test_normalizes_aliases_and_validates_filter_values(self) -> None:
        self.assertEqual(normalize_trend_granularity("월별"), "month")
        self.assertEqual(parse_trend_date("2026-08-02", "from"), date(2026, 8, 2))
        self.assertIsNone(parse_optional_bool("전체", "custom"))
        self.assertTrue(parse_optional_bool("custom", "custom"))
        with self.assertRaises(ValueError):
            PlayerTrendFilters(team_mode="trio").normalized()
        with self.assertRaises(ValueError):
            PlayerTrendFilters(
                from_date_kst=date(2026, 8, 3),
                to_date_kst=date(2026, 8, 2),
            ).normalized()

    @staticmethod
    def row(
        *,
        created_at_kst: datetime | None,
        win_place: int,
        kills: int = 0,
        assists: int = 0,
        deaths: int = 1,
        damage_dealt: float = 0,
        survival_seconds: float | None = 100,
        duration_seconds: int = 500,
    ) -> dict[str, object]:
        raw_stats = {} if survival_seconds is None else {"timeSurvived": survival_seconds}
        return {
            "match_id": f"match-{created_at_kst}",
            "created_at_kst": created_at_kst,
            "duration_seconds": duration_seconds,
            "win_place": win_place,
            "raw_stats": raw_stats,
            "kills": kills,
            "assists": assists,
            "deaths": deaths,
            "dbnos_caused": kills,
            "dbnos_taken": deaths,
            "damage_dealt": damage_dealt,
            "damage_taken": damage_dealt / 2,
            "shots_fired": 100,
            "shots_hit": 10,
            "headshot_kills": 1 if kills else 0,
            "fight_count": kills + deaths,
            "fight_wins": kills,
            "fight_losses": deaths,
            "in_game_sampled_distance_m": 1000,
        }


class PlayerTrendServiceTests(unittest.TestCase):
    def test_applies_all_match_dimension_and_kst_date_filters(self) -> None:
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
                    "registered_guild_id": "guild-1",
                    "registered_channel_id": None,
                },
                [],
            ]
        )

        report = PlayerTrendService(connection).get_report(
            shard="steam",
            name="Player",
            guild_id="guild-1",
            granularity="week",
            filters=PlayerTrendFilters(
                game_mode="squad-fpp",
                team_mode="squad",
                perspective="fpp",
                match_type="official",
                map_name="Baltic_Main",
                season_state="progress",
                is_custom_match=False,
                year=2026,
                quarter=3,
                month=8,
                exact_date_kst=date(2026, 8, 2),
                hour=21,
                from_date_kst=date(2026, 7, 1),
                to_date_kst=date(2026, 8, 2),
            ),
        )

        self.assertIsNotNone(report)
        query, params = connection.executions[1]
        self.assertIn("matches.game_mode = %s", query)
        self.assertIn("matches.team_mode = %s", query)
        self.assertIn("matches.perspective = %s", query)
        self.assertIn("matches.match_type = %s", query)
        self.assertIn("matches.map_name = %s", query)
        self.assertIn("matches.season_state = %s", query)
        self.assertIn("matches.is_custom_match = %s", query)
        self.assertIn("YEAR(matches.created_at_kst) = %s", query)
        self.assertIn("QUARTER(matches.created_at_kst) = %s", query)
        self.assertIn("MONTH(matches.created_at_kst) = %s", query)
        self.assertIn("HOUR(matches.created_at_kst) = %s", query)
        self.assertIn("matches.created_at_kst >= %s", query)
        self.assertIn("matches.created_at_kst < %s", query)
        self.assertEqual(params[:2], ["account.test", "steam"])
        self.assertEqual(params[-1], datetime(2026, 8, 3))

    def test_loads_weapon_accuracy_rows_for_filtered_matches(self) -> None:
        match_row = PlayerTrendSummaryTests.row(
            created_at_kst=datetime(2026, 8, 1, 1, 20),
            win_place=1,
        )
        match_row["match_id"] = "match-1"
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
                    "registered_guild_id": "guild-1",
                    "registered_channel_id": None,
                },
                [match_row],
                [
                    {
                        "match_id": "match-1",
                        "weapon_code": "WeapHK416_C",
                        "shots_fired": 100,
                        "shots_hit": 25,
                    }
                ],
                [
                    {
                        "match_id": "match-1",
                        "fight_count": 4,
                        "fight_wins": 3,
                        "fight_losses": 1,
                    }
                ],
            ]
        )

        report = PlayerTrendService(connection).get_report(
            shard="steam",
            name="Player",
            guild_id="guild-1",
        )

        self.assertIsNotNone(report)
        assert report is not None
        self.assertAlmostEqual(report.totals.accuracy, 0.25)
        self.assertEqual(report.totals.accuracy_breakdown.single_projectile_attacks, 100)
        self.assertEqual(report.totals.fight_count, 4)
        self.assertEqual(report.totals.fight_win_rate, 0.75)
        query, params = connection.executions[2]
        self.assertIn("weapon_stats.match_id IN ( %s )", " ".join(query.split()))
        self.assertEqual(params, ["account.test", "steam", "match-1"])
        fight_query, fight_params = connection.executions[3]
        self.assertIn("outcomes.is_friendly_fire = 0", fight_query)
        self.assertEqual(fight_params, ["account.test", "steam", "match-1"])

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

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)


if __name__ == "__main__":
    unittest.main()
