from __future__ import annotations

import unittest
from datetime import datetime

from pubg_ai.web.app import _job_queue_summary


class WebJobQueueTests(unittest.TestCase):
    def test_job_queue_summary_counts_all_statuses(self) -> None:
        connection = FakeConnection(
            {
                "total": 18,
                "queued": 3,
                "running": 1,
                "succeeded": 12,
                "failed": 2,
                "eligible_queued": 2,
                "scheduled_queued": 1,
                "recent_succeeded": 4,
                "oldest_queued_at_kst": datetime(2026, 8, 14, 17, 17),
                "last_succeeded_at_kst": datetime(2026, 8, 21, 13, 10),
                "last_activity_at_kst": datetime(2026, 8, 21, 13, 11),
            }
        )

        summary = _job_queue_summary(connection, "match")

        self.assertEqual(summary["total"], 18)
        self.assertEqual(
            summary["by_status"],
            {"queued": 3, "running": 1, "succeeded": 12, "failed": 2},
        )
        self.assertEqual(summary["eligible_queued"], 2)
        self.assertEqual(summary["scheduled_queued"], 1)
        self.assertEqual(summary["recent_succeeded"], 4)
        self.assertEqual(summary["oldest_queued_at_kst"], "2026-08-14T17:17:00")
        self.assertEqual(summary["last_activity_at_kst"], "2026-08-21T13:11:00")
        self.assertEqual(connection.parameters, ("match",))

    def test_job_queue_summary_keeps_zero_counts_for_missing_statuses(self) -> None:
        summary = _job_queue_summary(FakeConnection({}), "telemetry")

        self.assertEqual(summary["total"], 0)
        self.assertEqual(summary["by_status"]["queued"], 0)
        self.assertEqual(summary["by_status"]["failed"], 0)


class FakeConnection:
    def __init__(self, row: dict[str, object]) -> None:
        self.row = row
        self.parameters: tuple[object, ...] | None = None

    def cursor(self) -> "FakeCursor":
        return FakeCursor(self)


class FakeCursor:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def execute(self, query: str, parameters: tuple[object, ...]) -> None:
        self.connection.parameters = parameters

    def fetchone(self) -> dict[str, object]:
        return self.connection.row


if __name__ == "__main__":
    unittest.main()
