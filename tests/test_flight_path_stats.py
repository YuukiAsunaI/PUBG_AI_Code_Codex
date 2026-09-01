from __future__ import annotations

from datetime import date, datetime
import unittest

from pubg_ai.flight_path_stats import (
    FlightPathStatsService,
    cluster_flight_path_match_ids,
    summarize_flight_paths,
)
from pubg_ai.player_trends import PlayerTrendFilters


class FlightPathStatsTests(unittest.TestCase):
    def test_reversed_travel_direction_is_same_physical_route(self) -> None:
        rows = [
            self._row(
                "match-1",
                start_x=100000.0,
                start_y=100000.0,
                end_x=700000.0,
                end_y=700000.0,
            ),
            self._row(
                "match-2",
                start_x=700000.0,
                start_y=700000.0,
                end_x=100000.0,
                end_y=100000.0,
            ),
        ]

        report = summarize_flight_paths(rows, angle_bin_degrees=10, offset_bin_m=500)

        self.assertEqual(report.analyzed_route_count, 2)
        self.assertEqual(report.available_cluster_count, 1)
        cluster = report.clusters[0]
        self.assertEqual(cluster.route_count, 2)
        self.assertEqual(cluster.forward_count, 1)
        self.assertEqual(cluster.reverse_count, 1)
        self.assertAlmostEqual(cluster.dominant_direction_share, 0.5)
        memberships = cluster_flight_path_match_ids(
            rows,
            angle_bin_degrees=10,
            offset_bin_m=500,
        )
        self.assertEqual(memberships[cluster.cluster_id], {"match-1", "match-2"})
        self.assertTrue(
            0.0 in {
                round(cluster.start_x_pct, 8),
                round(cluster.end_x_pct, 8),
                round(cluster.start_y_pct, 8),
                round(cluster.end_y_pct, 8),
            }
        )
        self.assertTrue(
            1.0 in {
                round(cluster.start_x_pct, 8),
                round(cluster.end_x_pct, 8),
                round(cluster.start_y_pct, 8),
                round(cluster.end_y_pct, 8),
            }
        )

    def test_parallel_routes_with_distant_offsets_are_separate(self) -> None:
        rows = [
            self._row(
                "match-1",
                start_x=100000.0,
                start_y=200000.0,
                end_x=700000.0,
                end_y=200000.0,
            ),
            self._row(
                "match-2",
                start_x=100000.0,
                start_y=400000.0,
                end_x=700000.0,
                end_y=400000.0,
            ),
        ]

        report = summarize_flight_paths(rows, angle_bin_degrees=10, offset_bin_m=500)

        self.assertEqual(report.available_cluster_count, 2)
        self.assertEqual([item.route_count for item in report.clusters], [1, 1])

    def test_near_horizontal_routes_share_cluster_across_angle_wrap(self) -> None:
        rows = [
            self._row(
                "match-1",
                start_x=100000.0,
                start_y=295000.0,
                end_x=700000.0,
                end_y=305000.0,
            ),
            self._row(
                "match-2",
                start_x=100000.0,
                start_y=305000.0,
                end_x=700000.0,
                end_y=295000.0,
            ),
        ]

        report = summarize_flight_paths(rows, angle_bin_degrees=10, offset_bin_m=500)

        self.assertEqual(report.available_cluster_count, 1)
        self.assertEqual(report.clusters[0].route_count, 2)
        self.assertAlmostEqual(report.clusters[0].physical_angle_degrees, 0.0)

    def test_service_applies_scoped_filters_with_bound_parameters(self) -> None:
        connection = _Connection([])
        filters = PlayerTrendFilters(
            map_name="Tiger_Main",
            team_mode="squad",
            year=2026,
            month=8,
            from_date_kst=date(2026, 8, 1),
            to_date_kst=date(2026, 8, 24),
        )

        report = FlightPathStatsService(connection).get_report(
            shard="steam",
            account_id="account.test",
            filters=filters,
            route_limit=1000,
        )

        self.assertEqual(report.analyzed_route_count, 0)
        query, params = connection.executed[0]
        self.assertIn("FROM match_plane_routes routes", query)
        self.assertIn("EXISTS (SELECT 1 FROM match_participants", " ".join(query.split()))
        self.assertIn("matches.map_name = %s", query)
        self.assertIn("matches.team_mode = %s", query)
        self.assertIn("YEAR(matches.created_at_kst) = %s", query)
        self.assertIn("MONTH(matches.created_at_kst) = %s", query)
        self.assertEqual(params[0:5], ["steam", "account.test", "squad", "Tiger_Main", 2026])
        self.assertEqual(params[-1], 1000)

    @staticmethod
    def _row(
        match_id: str,
        *,
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
    ) -> dict[str, object]:
        return {
            "match_id": match_id,
            "shard": "steam",
            "map_name": "Tiger_Main",
            "created_at_kst": datetime(2026, 8, 24, 12, 0),
            "sample_count": 30,
            "start_x": start_x,
            "start_y": start_y,
            "end_x": end_x,
            "end_y": end_y,
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
