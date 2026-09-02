from __future__ import annotations

from datetime import datetime
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from pubg_ai.web.app import create_app


class WebFlightPathStatsTests(unittest.TestCase):
    def test_endpoint_returns_clustered_routes(self) -> None:
        connection = _Connection(
            [
                {
                    "match_id": "match-1",
                    "sample_count": 25,
                    "start_x": 100000.0,
                    "start_y": 100000.0,
                    "end_x": 700000.0,
                    "end_y": 700000.0,
                    "shard": "steam",
                    "map_name": "Tiger_Main",
                    "created_at_kst": datetime(2026, 8, 24, 12, 0),
                },
                {
                    "match_id": "match-2",
                    "sample_count": 27,
                    "start_x": 700000.0,
                    "start_y": 700000.0,
                    "end_x": 100000.0,
                    "end_y": 100000.0,
                    "shard": "steam",
                    "map_name": "Tiger_Main",
                    "created_at_kst": datetime(2026, 8, 23, 12, 0),
                },
            ]
        )
        with patch("pubg_ai.web.app.connect_mysql", return_value=connection):
            response = TestClient(create_app()).get(
                "/analytics/flight-paths",
                params={
                    "shard": "steam",
                    "map_name": "Tiger_Main",
                    "angle_bin_degrees": 10,
                    "offset_bin_m": 500,
                },
            )

        self.assertEqual(response.status_code, 200)
        report = response.json()["flight_paths"]
        self.assertEqual(report["analyzed_route_count"], 2)
        self.assertEqual(report["available_cluster_count"], 1)
        self.assertEqual(report["clusters"][0]["route_count"], 2)
        self.assertEqual(report["clusters"][0]["forward_count"], 1)
        self.assertEqual(report["clusters"][0]["reverse_count"], 1)
        self.assertTrue(connection.closed)

    def test_local_manager_contains_flight_path_map_and_filters(self) -> None:
        body = TestClient(create_app()).get("/").text

        self.assertIn('id="flight-path-analysis"', body)
        self.assertIn('id="flightPathForm"', body)
        self.assertIn('id="flightPathPlayerSelect"', body)
        self.assertIn('id="flightPathViewControls"', body)
        self.assertIn('id="flightPathPhaseSelect"', body)
        self.assertIn('data-flight-analysis-view="combined"', body)
        self.assertIn('name="angle_bin_degrees"', body)
        self.assertIn('name="offset_bin_m"', body)
        self.assertIn("/analytics/flight-paths?", body)
        self.assertIn("/analytics/circles?", body)
        self.assertIn('id="flightPathOverlay"', body)
        self.assertIn('id="circlePathList"', body)
        self.assertIn('id="circleMapPhaseSelect"', body)
        self.assertIn('id="flightPathShowAllRoutes"', body)
        self.assertIn('id="flightMapResetViewport"', body)
        self.assertIn('class="circle-location-label"', body)
        self.assertIn("다른 항로 겹쳐보기", body)
        self.assertIn("전체 단계 겹쳐보기", body)
        self.assertIn("가장 가까운 지도 지명 기준", body)
        self.assertIn("marker-end=\"url(#flightArrow)\"", body)
        self.assertIn("방향이 반대여도 같은 물리 항로", body)

    def test_circle_endpoint_returns_integer_phase_clusters(self) -> None:
        connection = _Connection(
            [
                {
                    "match_id": "match-1",
                    "common_is_game": 1.0,
                    "poison_gas_warning_x": 300000.0,
                    "poison_gas_warning_y": 310000.0,
                    "poison_gas_warning_radius": 192000.0,
                    "shard": "steam",
                    "map_name": "Baltic_Main",
                    "created_at_kst": datetime(2026, 8, 24, 12, 0),
                }
            ]
        )
        with patch("pubg_ai.web.app.connect_mysql", return_value=connection):
            response = TestClient(create_app()).get(
                "/analytics/circles",
                params={"shard": "steam", "map_name": "Baltic_Main"},
            )

        self.assertEqual(response.status_code, 200)
        report = response.json()["circles"]
        self.assertEqual(report["analyzed_circle_count"], 1)
        self.assertEqual(report["clusters"][0]["phase_number"], 1)
        self.assertEqual(report["clusters"][0]["circle_count"], 1)
        self.assertTrue(connection.closed)


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
        self.closed = False

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def close(self) -> None:
        self.closed = True


if __name__ == "__main__":
    unittest.main()
