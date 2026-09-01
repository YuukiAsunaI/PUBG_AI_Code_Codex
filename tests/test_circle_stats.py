from __future__ import annotations

from datetime import date, datetime
import unittest
from unittest.mock import patch

from pubg_ai.circle_stats import CircleStatsService, summarize_circle_patterns
from pubg_ai.player_trends import PlayerTrendFilters


class CircleStatsTests(unittest.TestCase):
    def test_clusters_each_integer_phase_and_counts_matches(self) -> None:
        rows = [
            self._row("match-1", phase=1, x=300000, y=300000, radius=192000),
            self._row("match-2", phase=1, x=301000, y=299500, radius=191500),
            self._row("match-1", phase=2, x=350000, y=340000, radius=105600),
        ]

        report = summarize_circle_patterns(
            rows,
            center_bin_m=500,
            radius_bin_m=250,
            top_per_phase=8,
        )

        self.assertEqual(report.analyzed_circle_count, 3)
        self.assertEqual(report.analyzed_match_count, 2)
        self.assertEqual([item.phase_number for item in report.phases], [1, 2])
        phase_one = next(item for item in report.clusters if item.phase_number == 1)
        self.assertEqual(phase_one.circle_count, 2)
        self.assertEqual(phase_one.phase_circle_count, 2)
        self.assertAlmostEqual(phase_one.phase_share, 1.0)

    def test_route_membership_filters_circle_samples(self) -> None:
        rows = [
            self._row("match-1", phase=1, x=300000, y=300000, radius=192000),
            self._row("match-2", phase=1, x=500000, y=500000, radius=192000),
        ]

        report = summarize_circle_patterns(
            rows,
            flight_cluster_id="route-1",
            route_match_ids={"match-2"},
        )

        self.assertEqual(report.total_circle_count, 2)
        self.assertEqual(report.filtered_out_by_route, 1)
        self.assertEqual(report.analyzed_circle_count, 1)
        self.assertEqual(report.route_match_count, 1)
        self.assertAlmostEqual(report.clusters[0].center_x_pct, 500000 / 816000)

    def test_service_uses_first_integer_phase_sample_and_kst_filters(self) -> None:
        connection = _Connection([])
        report = CircleStatsService(connection).get_report(
            shard="steam",
            account_id="account.test",
            filters=PlayerTrendFilters(
                map_name="Baltic_Main",
                team_mode="squad",
                year=2026,
                month=8,
                from_date_kst=date(2026, 8, 1),
                to_date_kst=date(2026, 8, 31),
            ),
            phase_number=3,
            circle_limit=1000,
        )

        self.assertEqual(report.analyzed_circle_count, 0)
        query, params = connection.executed[0]
        compact_query = " ".join(query.split())
        self.assertIn("MIN(event_index) AS event_index", compact_query)
        self.assertIn("phases.common_is_game = FLOOR(phases.common_is_game)", compact_query)
        self.assertIn("poison_gas_warning_x IS NOT NULL", compact_query)
        self.assertIn("poison_gas_warning_y IS NOT NULL", compact_query)
        self.assertIn("phases.poison_gas_warning_radius > 0", compact_query)
        self.assertIn("EXISTS (SELECT 1 FROM match_participants", compact_query)
        self.assertIn("matches.created_at_kst >= %s", compact_query)
        self.assertEqual(params[0:5], ["steam", "account.test", "squad", "Baltic_Main", 2026])
        self.assertEqual(params[-2:], [3.0, 1000])

    def test_service_applies_route_membership_before_circle_limit(self) -> None:
        connection = _Connection([])
        with patch(
            "pubg_ai.circle_stats.FlightPathStatsService.get_cluster_match_ids",
            return_value={"route-1": {"match-2", "match-1"}},
        ):
            report = CircleStatsService(connection).get_report(
                flight_cluster_id="route-1",
                circle_limit=1000,
            )

        self.assertEqual(report.route_match_count, 2)
        query, params = connection.executed[0]
        compact_query = " ".join(query.split())
        self.assertIn("phases.match_id IN (%s, %s)", compact_query)
        self.assertLess(compact_query.index("phases.match_id IN"), compact_query.index("LIMIT %s"))
        self.assertEqual(params[-3:], ["match-1", "match-2", 1000])

    @staticmethod
    def _row(
        match_id: str,
        *,
        phase: int,
        x: float,
        y: float,
        radius: float,
    ) -> dict[str, object]:
        return {
            "match_id": match_id,
            "common_is_game": float(phase),
            "poison_gas_warning_x": x,
            "poison_gas_warning_y": y,
            "poison_gas_warning_radius": radius,
            "shard": "steam",
            "map_name": "Baltic_Main",
            "created_at_kst": datetime(2026, 8, 24, 12, 0),
        }


class _Cursor:
    def __init__(self, connection: "_Connection") -> None:
        self.connection = connection

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def execute(self, query, params=()) -> None:
        self.connection.executed.append((query, list(params)))

    def fetchall(self):
        return self.connection.rows


class _Connection:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.executed: list[tuple[str, list[object]]] = []

    def cursor(self) -> _Cursor:
        return _Cursor(self)


if __name__ == "__main__":
    unittest.main()
