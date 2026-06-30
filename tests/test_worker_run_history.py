from __future__ import annotations

from datetime import datetime
import json
import unittest

from pubg_ai.worker_run_history import WorkerRunHistoryError, list_worker_runs, record_worker_cycle


class WorkerRunHistoryTests(unittest.TestCase):
    def test_record_worker_cycle_stores_failed_summary(self) -> None:
        connection = FakeConnection()

        record = record_worker_cycle(
            connection,
            worker_name="collector",
            cycle={
                "started_at_kst": "2026-06-30T10:00:00+09:00",
                "finished_at_kst": "2026-06-30T10:00:03+09:00",
                "duration_seconds": 3.0,
                "collection": {"queued_match_jobs": 2},
                "errors": ["match_jobs: RuntimeError: boom"],
            },
        )

        self.assertEqual(record.id, 123)
        self.assertEqual(record.worker_name, "collector")
        self.assertEqual(record.status, "failed")
        self.assertEqual(record.error_count, 1)
        self.assertEqual(record.last_error, "match_jobs: RuntimeError: boom")
        params = connection.cursor_obj.executed[0][1]
        self.assertEqual(params[0], "collector")
        self.assertEqual(params[1], "failed")
        self.assertEqual(params[5], 1)
        stored_summary = json.loads(params[7])
        self.assertEqual(stored_summary["collection"]["queued_match_jobs"], 2)

    def test_list_worker_runs_parses_summary_json(self) -> None:
        connection = FakeConnection(
            rows=[
                {
                    "id": 7,
                    "worker_name": "post_processing",
                    "status": "succeeded",
                    "started_at_kst": datetime(2026, 6, 30, 10, 0, 0),
                    "finished_at_kst": datetime(2026, 6, 30, 10, 0, 5),
                    "duration_seconds": 5,
                    "error_count": 0,
                    "last_error": None,
                    "summary_json": '{"errors":[],"map_snapshots":{"generated_snapshots":4}}',
                    "created_at_kst": datetime(2026, 6, 30, 10, 0, 5),
                }
            ]
        )

        runs = list_worker_runs(connection, worker_name="post_processing", limit=500)

        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].id, 7)
        self.assertEqual(runs[0].summary["map_snapshots"]["generated_snapshots"], 4)
        query, params = connection.cursor_obj.executed[0]
        self.assertIn("WHERE worker_name = %s", query)
        self.assertEqual(params, ("post_processing", 200))

    def test_rejects_unknown_worker_name(self) -> None:
        with self.assertRaises(WorkerRunHistoryError):
            list_worker_runs(FakeConnection(), worker_name="other")


class FakeConnection:
    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self.cursor_obj = FakeCursor(rows or [])

    def cursor(self) -> "FakeCursor":
        return self.cursor_obj


class FakeCursor:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.lastrowid = 123

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def execute(self, query: str, params: tuple[object, ...] = ()) -> None:
        self.executed.append((query, params))

    def fetchall(self) -> list[dict[str, object]]:
        return self.rows


if __name__ == "__main__":
    unittest.main()
