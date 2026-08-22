from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from pubg_ai.web.app import create_app


class WebPlayerIntelligenceTests(unittest.TestCase):
    def test_intelligence_endpoint_passes_normalized_filters(self) -> None:
        connection = FakeConnection()
        service = FakeIntelligenceService(connection)
        with (
            patch("pubg_ai.web.app.connect_mysql", return_value=connection),
            patch("pubg_ai.web.app.PlayerIntelligenceService", return_value=service),
        ):
            response = TestClient(create_app()).get(
                "/players/intelligence?shard=steam&name=Player&team_mode=squad"
                "&perspective=fpp&map_name=Baltic_Main&from_date_kst=2026-08-01"
                "&to_date_kst=2026-08-22&trend_limit=999"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["intelligence"]["player"]["current_name"], "Player")
        self.assertEqual(service.kwargs["filters"].team_mode, "squad")
        self.assertEqual(service.kwargs["filters"].perspective, "fpp")
        self.assertEqual(service.kwargs["filters"].map_name, "Baltic_Main")
        self.assertEqual(service.kwargs["trend_limit"], 999)
        self.assertTrue(connection.closed)

    def test_intelligence_endpoint_rejects_inverted_dates(self) -> None:
        response = TestClient(create_app()).get(
            "/players/intelligence?shard=steam&name=Player"
            "&from_date_kst=2026-08-22&to_date_kst=2026-08-01"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("on or before", response.json()["detail"])

    def test_manager_contains_progressive_intelligence_views(self) -> None:
        response = TestClient(create_app()).get("/")
        self.assertEqual(response.status_code, 200)
        body = response.text
        self.assertIn('id="intelligence-analysis"', body)
        self.assertIn('id="intelligenceForm"', body)
        self.assertIn('data-intelligence-view="overview"', body)
        self.assertIn('data-intelligence-view="trends"', body)
        self.assertIn('data-intelligence-view="breakdowns"', body)
        self.assertIn('data-intelligence-view="evidence"', body)
        self.assertIn("/players/intelligence?", body)
        self.assertIn("function renderIntelligenceReport", body)
        self.assertIn(
            "async function loadPlayerIntelligence(formElement) {\n"
            "      const form = new FormData(formElement);",
            body,
        )
        self.assertIn("LogHeal.healAmount", body)
        self.assertIn('activity_limit: Number(form.get("activity_limit") || 10)', body)
        self.assertIn('const analysisForms = [intelligenceForm, profileForm', body)
        self.assertIn('id="player-intelligence-audit"', body)
        self.assertIn('id="playerIntelligenceAuditRun"', body)
        self.assertIn("/operations/player-intelligence-audit", body)

    def test_data_quality_audit_endpoint_closes_connection(self) -> None:
        connection = FakeConnection()
        audit = FakeAudit()
        with (
            patch("pubg_ai.web.app.connect_mysql", return_value=connection),
            patch("pubg_ai.web.app.audit_player_intelligence", return_value=audit),
        ):
            response = TestClient(create_app()).get("/operations/player-intelligence-audit")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["player_intelligence_audit"]["passed"])
        self.assertTrue(connection.closed)

    def test_metric_catalog_endpoint_exposes_denominators(self) -> None:
        response = TestClient(create_app()).get("/analytics/metrics?category=combat")
        self.assertEqual(response.status_code, 200)
        metrics = response.json()["metrics"]
        self.assertTrue(metrics)
        self.assertTrue(all(metric["category"] == "combat" for metric in metrics))
        self.assertTrue(all(metric["denominator_ko"] for metric in metrics))


class FakeConnection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeIntelligenceService:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.kwargs: dict[str, object] = {}

    def get_report(self, **kwargs: object) -> "FakeReport":
        self.kwargs = kwargs
        return FakeReport()


class FakeReport:
    def to_record(self) -> dict[str, object]:
        return {
            "player": {"account_id": "account.test", "shard": "steam", "current_name": "Player"},
            "coverage": {"status": "complete", "coverage_rate": 1.0},
        }


class FakeAudit:
    def to_record(self) -> dict[str, object]:
        return {"passed": True, "checks": [], "counts": {}}


if __name__ == "__main__":
    unittest.main()
