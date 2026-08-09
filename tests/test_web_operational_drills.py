from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch
import unittest

from fastapi.testclient import TestClient

from pubg_ai.operational_drills import (
    OperationalDrillCheck,
    OperationalDrillReport,
    OperationalDrillRunRecord,
)
from pubg_ai.web.app import create_app


class WebOperationalDrillTests(unittest.TestCase):
    def test_run_endpoint_persists_and_returns_report(self) -> None:
        report = _report()
        connection = FakeConnection()
        with (
            patch("pubg_ai.web.app.run_operational_drills", return_value=report) as runner,
            patch("pubg_ai.web.app.connect_mysql", return_value=connection),
            patch("pubg_ai.web.app.record_operational_drill", return_value=41) as recorder,
        ):
            response = TestClient(create_app()).post(
                "/operations/drills",
                json={"mode": "simulated", "cycles": 3},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["run_id"], 41)
        self.assertTrue(payload["operational_drill"]["passed"])
        self.assertEqual(payload["operational_drill"]["check_count"], 1)
        runner.assert_called_once()
        recorder.assert_called_once_with(connection, report)
        self.assertTrue(connection.closed)

    def test_history_endpoint_returns_recent_records(self) -> None:
        connection = FakeConnection()
        record = OperationalDrillRunRecord(
            id=41,
            contract_version="operational-drill-v1",
            mode="simulated",
            status="passed",
            started_at_kst="2026-08-09T12:00:00",
            finished_at_kst="2026-08-09T12:00:01",
            duration_seconds=1.0,
            requested_cycles=3,
            check_count=4,
            passed_check_count=4,
            report={"passed": True, "checks": []},
            created_at_kst="2026-08-09T12:00:01",
        )
        with (
            patch("pubg_ai.web.app.connect_mysql", return_value=connection),
            patch("pubg_ai.web.app.list_operational_drills", return_value=[record]) as loader,
        ):
            response = TestClient(create_app()).get("/operations/drills?limit=7")

        self.assertEqual(response.status_code, 200)
        rows = response.json()["operational_drill_runs"]
        self.assertEqual(rows[0]["id"], 41)
        self.assertEqual(rows[0]["status"], "passed")
        loader.assert_called_once_with(connection, limit=7)
        self.assertTrue(connection.closed)

    def test_request_validation_bounds_mode_and_cycles(self) -> None:
        client = TestClient(create_app())

        invalid_mode = client.post("/operations/drills", json={"mode": "unsafe", "cycles": 3})
        invalid_cycles = client.post("/operations/drills", json={"mode": "simulated", "cycles": 99})

        self.assertEqual(invalid_mode.status_code, 422)
        self.assertEqual(invalid_cycles.status_code, 422)

    def test_live_drill_is_blocked_while_collector_is_running(self) -> None:
        with (
            patch(
                "pubg_ai.web.app.CollectorWorkerController.status",
                return_value=SimpleNamespace(running=True),
            ),
            patch("pubg_ai.web.app.run_operational_drills") as runner,
        ):
            response = TestClient(create_app()).post(
                "/operations/drills",
                json={"mode": "live", "cycles": 3},
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("Stop the automatic collector", response.json()["detail"])
        runner.assert_not_called()

    def test_local_manager_contains_drill_controls_and_history(self) -> None:
        response = TestClient(create_app()).get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="operational-drills"', response.text)
        self.assertIn('id="operationalDrillForm"', response.text)
        self.assertIn('value="simulated"', response.text)
        self.assertIn('value="live"', response.text)
        self.assertIn("/operations/drills", response.text)
        self.assertIn("data-operational-drill-id", response.text)
        self.assertIn('class="operational-drill-table"', response.text)


def _report() -> OperationalDrillReport:
    return OperationalDrillReport(
        contract_version="operational-drill-v1",
        mode="simulated",
        started_at_kst="2026-08-09T12:00:00+09:00",
        finished_at_kst="2026-08-09T12:00:01+09:00",
        duration_seconds=1.0,
        requested_cycles=3,
        passed=True,
        checks=[
            OperationalDrillCheck(
                name="rate_limit_429_backoff",
                passed=True,
                summary="passed",
                metrics={"request_count": 2},
            )
        ],
    )


class FakeConnection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


if __name__ == "__main__":
    unittest.main()
