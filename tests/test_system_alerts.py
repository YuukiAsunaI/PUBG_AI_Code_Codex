from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pubg_ai.alert_history import AlertHistoryRecord
from pubg_ai.config import AppConfig, DatabaseConfig, RuntimeConfig, SecretConfig
from pubg_ai.local_settings import AlertSettings
from pubg_ai.system_alerts import collect_system_alerts, format_alert_report, format_discord_alert, worker_run_alert
from pubg_ai.worker_run_history import WorkerRunRecord


class SystemAlertsTests(unittest.TestCase):
    def test_collect_system_alerts_combines_storage_and_worker_failures(self) -> None:
        with TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing-raw"
            replay = Path(temp_dir) / "replay"
            replay.mkdir()
            connection = FakeConnection(
                rows=[
                    {
                        "id": 12,
                        "worker_name": "collector",
                        "status": "failed",
                        "started_at_kst": "2026-06-30T10:00:00+09:00",
                        "finished_at_kst": "2026-06-30T10:00:02+09:00",
                        "duration_seconds": 2.0,
                        "error_count": 1,
                        "last_error": "raw_store: RuntimeError: drive missing",
                        "summary_json": '{"errors":["raw_store: RuntimeError: drive missing"]}',
                        "created_at_kst": "2026-06-30T10:00:02+09:00",
                    }
                ],
                latest_id=12,
            )

            report = collect_system_alerts(
                config=_runtime_config(raw_data_dir=missing, replay_data_dir=replay),
                connection=connection,
                settings=AlertSettings(minimum_free_bytes=1, discord_channel_ids=[]),
                after_worker_run_id=10,
            )

        self.assertEqual(report.latest_worker_run_id, 12)
        keys = {alert.key for alert in report.alerts}
        self.assertTrue(any(key.startswith("storage:raw_data_dir:") for key in keys))
        self.assertIn("worker:12", keys)
        self.assertIn("id > %s", connection.cursor_obj.executed[0][0])

    def test_worker_alert_formats_for_discord(self) -> None:
        alert = worker_run_alert(
            WorkerRunRecord(
                id=9,
                worker_name="post_processing",
                status="failed",
                started_at_kst="2026-06-30T10:00:00+09:00",
                finished_at_kst="2026-06-30T10:00:05+09:00",
                duration_seconds=5.0,
                error_count=1,
                last_error="replay_store: RuntimeError: drive missing",
                summary={"errors": ["replay_store: RuntimeError: drive missing"]},
                created_at_kst="2026-06-30T10:00:05+09:00",
            )
        )

        message = format_discord_alert(alert)

        self.assertIn("[PUBG AI Alert] post_processing worker failed", message)
        self.assertIn("drive missing", message)
        self.assertIn("run_id=9", message)

    def test_empty_alert_report_message(self) -> None:
        self.assertEqual(format_alert_report([]), "PUBG AI alerts: no active alerts.")

    def test_alert_history_records_include_id_in_discord_messages(self) -> None:
        record = AlertHistoryRecord(
            id=7,
            alert_key="worker:7",
            source="worker",
            severity="error",
            title="collector worker failed",
            message="drive missing",
            metadata={},
            first_seen_at_kst="2026-06-30T10:00:00+09:00",
            last_seen_at_kst="2026-06-30T10:01:00+09:00",
            last_notified_at_kst=None,
            acknowledged_at_kst=None,
            snoozed_until_kst=None,
            resolved_at_kst=None,
            updated_at_kst="2026-06-30T10:01:00+09:00",
        )

        self.assertIn("- alert_id: 7", format_discord_alert(record))
        self.assertIn("#7 collector worker failed", format_alert_report([record]))


def _runtime_config(raw_data_dir: Path, replay_data_dir: Path) -> RuntimeConfig:
    return RuntimeConfig(
        app=AppConfig(raw_data_dir=raw_data_dir, replay_data_dir=replay_data_dir),
        database=DatabaseConfig(database="pubg_ai_test"),
        secrets=SecretConfig(),
    )


class FakeConnection:
    def __init__(self, rows: list[dict[str, object]], latest_id: int) -> None:
        self.cursor_obj = FakeCursor(rows, latest_id)

    def cursor(self) -> "FakeCursor":
        return self.cursor_obj


class FakeCursor:
    def __init__(self, rows: list[dict[str, object]], latest_id: int) -> None:
        self.rows = rows
        self.latest_id = latest_id
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def execute(self, query: str, params: tuple[object, ...] = ()) -> None:
        self.executed.append((query, params))

    def fetchall(self) -> list[dict[str, object]]:
        return self.rows

    def fetchone(self) -> dict[str, object]:
        return {"latest_id": self.latest_id}


if __name__ == "__main__":
    unittest.main()
