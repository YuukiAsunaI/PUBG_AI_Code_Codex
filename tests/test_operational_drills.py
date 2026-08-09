from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime
from pathlib import Path
from typing import Any
import json
from unittest.mock import patch
import unittest

from pubg_ai.config import AppConfig, DatabaseConfig, RuntimeConfig, SecretConfig
from pubg_ai.operational_drills import (
    OperationalDrillCheck,
    OperationalDrillError,
    _run_live_stale_job_recovery_drill,
    list_operational_drills,
    record_operational_drill,
    run_operational_drills,
)


class OperationalDrillTests(unittest.TestCase):
    def test_simulated_drill_covers_all_four_operational_contracts(self) -> None:
        report = run_operational_drills(_runtime_config(pubg_key=None), cycles=3)

        self.assertTrue(report.passed)
        self.assertEqual(report.mode, "simulated")
        self.assertEqual(
            [check.name for check in report.checks],
            [
                "rate_limit_429_backoff",
                "storage_pressure_alert",
                "worker_stop_restart_recovery",
                "bounded_idempotent_soak",
            ],
        )
        self.assertTrue(all(check.passed for check in report.checks))
        payload = json.dumps(report.to_record(), ensure_ascii=False)
        self.assertNotIn("operational-drill-placeholder", payload)
        self.assertNotIn("operational-drill-key", payload)

    def test_drill_rejects_unbounded_cycle_count(self) -> None:
        with self.assertRaises(OperationalDrillError):
            run_operational_drills(_runtime_config(), cycles=1)
        with self.assertRaises(OperationalDrillError):
            run_operational_drills(_runtime_config(), cycles=6)

    def test_live_drill_uses_bounded_cycle_runner_and_queue_invariants(self) -> None:
        cycle_calls: list[RuntimeConfig] = []
        cycle_options: list[Any] = []
        connections: list[QueueSnapshotConnection] = []

        def connection_factory(database: DatabaseConfig) -> QueueSnapshotConnection:
            connection = QueueSnapshotConnection()
            connections.append(connection)
            return connection

        def cycle_runner(config: RuntimeConfig, *, options: Any) -> FakeCycle:
            cycle_calls.append(config)
            cycle_options.append(options)
            return FakeCycle()

        stale_check = OperationalDrillCheck(
            name="live_mysql_stale_job_recovery",
            passed=True,
            summary="passed",
            metrics={"transaction_rolled_back": True},
        )
        with patch(
            "pubg_ai.operational_drills._run_live_stale_job_recovery_drill",
            return_value=stale_check,
        ):
            report = run_operational_drills(
                _runtime_config(),
                mode="live",
                cycles=2,
                connection_factory=connection_factory,
                collector_cycle_runner=cycle_runner,
            )

        self.assertTrue(report.passed)
        live = report.checks[-1]
        self.assertEqual(live.name, "live_bounded_collection_soak")
        self.assertEqual(live.metrics["cycles"], 2)
        self.assertEqual(len(cycle_calls), 2)
        self.assertTrue(all(config.app.collector_cycle_player_limit == 10 for config in cycle_calls))
        self.assertTrue(all(options.shard == "steam" for options in cycle_options))
        self.assertEqual(live.metrics["selected_shard"], "steam")
        self.assertEqual(len(connections), 3)
        self.assertTrue(all(connection.closed for connection in connections))

    def test_live_stale_job_recovery_rolls_back_drill_rows(self) -> None:
        connection = TransactionalRecoveryConnection()
        with (
            patch("pubg_ai.operational_drills.MatchJobProcessor") as match_processor,
            patch("pubg_ai.operational_drills.TelemetryJobProcessor") as telemetry_processor,
        ):
            match_processor.return_value._recover_stale_running_jobs.return_value = 1
            telemetry_processor.return_value._recover_stale_running_jobs.return_value = 1
            check = _run_live_stale_job_recovery_drill(
                _runtime_config(),
                connection_factory=lambda database: connection,
            )

        self.assertTrue(check.passed)
        self.assertEqual(check.name, "live_mysql_stale_job_recovery")
        self.assertTrue(check.metrics["transaction_rolled_back"])
        self.assertEqual(check.metrics["rows_remaining_after_rollback"], 0)
        self.assertTrue(connection.begin_called)
        self.assertTrue(connection.rollback_called)
        self.assertTrue(connection.closed)

    def test_live_drill_converts_database_connection_failure_to_failed_checks(self) -> None:
        def failing_connection_factory(database: DatabaseConfig) -> Any:
            raise RuntimeError("database unavailable")

        report = run_operational_drills(
            _runtime_config(),
            mode="live",
            cycles=2,
            connection_factory=failing_connection_factory,
        )

        self.assertFalse(report.passed)
        checks = {check.name: check for check in report.checks}
        self.assertFalse(checks["live_mysql_stale_job_recovery"].passed)
        self.assertFalse(checks["live_bounded_collection_soak"].passed)
        self.assertIn("database unavailable", checks["live_mysql_stale_job_recovery"].summary)
        self.assertIn("database unavailable", checks["live_bounded_collection_soak"].summary)

    def test_record_and_list_drill_history(self) -> None:
        report = run_operational_drills(_runtime_config(), cycles=2)
        connection = HistoryConnection(report.to_record())

        run_id = record_operational_drill(connection, report)
        records = list_operational_drills(connection, limit=5)

        self.assertEqual(run_id, 17)
        insert_query, insert_params = connection.executed[0]
        self.assertIn("INSERT INTO operational_drill_runs", insert_query)
        self.assertEqual(insert_params[2], "passed")
        self.assertNotIn("pubg-secret", str(insert_params))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].id, 17)
        self.assertEqual(records[0].status, "passed")
        self.assertEqual(records[0].report["check_count"], 4)


def _runtime_config(pubg_key: str | None = "pubg-secret") -> RuntimeConfig:
    return RuntimeConfig(
        app=AppConfig(
            raw_data_dir=Path("raw"),
            replay_data_dir=Path("replay"),
            collector_poll_interval_seconds=180,
            collector_cycle_player_limit=100,
            player_lookup_chunk_size=10,
        ),
        database=DatabaseConfig(database="pubg_ai_test"),
        secrets=SecretConfig(pubg_api_key=pubg_key),
    )


class FakeCycle:
    errors: list[str] = []


class QueueSnapshotCursor(AbstractContextManager["QueueSnapshotCursor"]):
    def __init__(self) -> None:
        self.query = ""

    def __enter__(self) -> "QueueSnapshotCursor":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def execute(self, query: str, params: tuple[Any, ...] | None = None) -> None:
        self.query = query

    def fetchone(self) -> dict[str, int | str]:
        if "FROM registered_players" in self.query:
            return {"shard": "steam"}
        if "duplicate_job_groups" in self.query:
            return {"duplicate_job_groups": 0}
        return {
            "queued_jobs": 0,
            "running_jobs": 0,
            "failed_jobs": 0,
            "succeeded_jobs": 12,
        }


class QueueSnapshotConnection:
    def __init__(self) -> None:
        self.closed = False

    def cursor(self) -> QueueSnapshotCursor:
        return QueueSnapshotCursor()

    def close(self) -> None:
        self.closed = True


class TransactionalRecoveryCursor(AbstractContextManager["TransactionalRecoveryCursor"]):
    def __init__(self, connection: "TransactionalRecoveryConnection") -> None:
        self.connection = connection
        self.query = ""

    def __enter__(self) -> "TransactionalRecoveryCursor":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def execute(self, query: str, params: tuple[Any, ...] | None = None) -> None:
        self.query = query
        if "INSERT INTO api_fetch_jobs" in query and params is not None:
            self.connection.targets.append(str(params[2]))

    def fetchall(self) -> list[dict[str, Any]]:
        return [
            {
                "target_id": target,
                "status": "queued",
                "attempts": 1,
                "next_run_at_kst": datetime(2026, 8, 9, 12, 0),
                "last_error": "recovered stale running test job after worker restart",
            }
            for target in self.connection.targets
        ]

    def fetchone(self) -> dict[str, int]:
        return {"remaining_rows": len(self.connection.targets)}


class TransactionalRecoveryConnection:
    def __init__(self) -> None:
        self.targets: list[str] = []
        self.begin_called = False
        self.rollback_called = False
        self.closed = False

    def begin(self) -> None:
        self.begin_called = True

    def rollback(self) -> None:
        self.rollback_called = True
        self.targets.clear()

    def cursor(self) -> TransactionalRecoveryCursor:
        return TransactionalRecoveryCursor(self)

    def close(self) -> None:
        self.closed = True


class HistoryCursor(AbstractContextManager["HistoryCursor"]):
    def __init__(self, connection: "HistoryConnection") -> None:
        self.connection = connection
        self.lastrowid = 17

    def __enter__(self) -> "HistoryCursor":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self.connection.executed.append((query, params))

    def fetchall(self) -> list[dict[str, Any]]:
        return [
            {
                "id": 17,
                "contract_version": "operational-drill-v1",
                "mode": "simulated",
                "status": "passed",
                "started_at_kst": datetime(2026, 8, 9, 12, 0),
                "finished_at_kst": datetime(2026, 8, 9, 12, 1),
                "duration_seconds": 60.0,
                "requested_cycles": 2,
                "check_count": 4,
                "passed_check_count": 4,
                "report_json": self.connection.report,
                "created_at_kst": datetime(2026, 8, 9, 12, 1),
            }
        ]


class HistoryConnection:
    def __init__(self, report: dict[str, Any]) -> None:
        self.report = report
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    def cursor(self) -> HistoryCursor:
        return HistoryCursor(self)


if __name__ == "__main__":
    unittest.main()
