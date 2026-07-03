from __future__ import annotations

from datetime import datetime
import json
import unittest

from pubg_ai.worker_run_history import (
    WorkerRunHistoryError,
    count_worker_runs,
    get_latest_worker_run_id,
    get_worker_run,
    get_worker_run_page,
    list_failed_worker_runs,
    list_worker_runs,
    record_worker_cycle,
)


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

        runs = list_worker_runs(connection, worker_name="post_processing", status="succeeded", limit=500)

        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].id, 7)
        self.assertEqual(runs[0].summary["map_snapshots"]["generated_snapshots"], 4)
        query, params = connection.cursor_obj.executed[0]
        self.assertIn("WHERE worker_name = %s", query)
        self.assertIn("status = %s", query)
        self.assertIn("LIMIT %s OFFSET %s", query)
        self.assertEqual(params, ("post_processing", "succeeded", 200, 0))

    def test_list_worker_runs_supports_offset(self) -> None:
        connection = FakeConnection()

        list_worker_runs(connection, worker_name="collector", status="failed", limit=5, offset=10)

        query, params = connection.cursor_obj.executed[0]
        self.assertIn("worker_name = %s AND status = %s", query)
        self.assertIn("LIMIT %s OFFSET %s", query)
        self.assertEqual(params, ("collector", "failed", 5, 10))

    def test_list_worker_runs_supports_created_at_range(self) -> None:
        connection = FakeConnection()

        list_worker_runs(
            connection,
            worker_name="collector",
            status="failed",
            created_from_kst="2026-07-01T10:00",
            created_to_kst="2026-07-01T11:00:00+09:00",
            limit=5,
            offset=10,
        )

        query, params = connection.cursor_obj.executed[0]
        self.assertIn("worker_name = %s AND status = %s", query)
        self.assertIn("created_at_kst >= %s", query)
        self.assertIn("created_at_kst <= %s", query)
        self.assertIn("LIMIT %s OFFSET %s", query)
        self.assertEqual(params[0:2], ("collector", "failed"))
        self.assertEqual(params[2].isoformat(), "2026-07-01T10:00:00")
        self.assertEqual(params[3].isoformat(), "2026-07-01T11:00:00")
        self.assertEqual(params[4:6], (5, 10))

    def test_count_worker_runs_supports_worker_filter(self) -> None:
        connection = FakeConnection(total=13)

        total = count_worker_runs(connection, worker_name="collector", status="failed")

        self.assertEqual(total, 13)
        query, params = connection.cursor_obj.executed[0]
        self.assertIn("SELECT COUNT(*) AS total", query)
        self.assertIn("WHERE worker_name = %s", query)
        self.assertIn("status = %s", query)
        self.assertEqual(params, ("collector", "failed"))

    def test_get_worker_run_page_returns_records_and_total(self) -> None:
        connection = FakeConnection(
            rows=[
                {
                    "id": 7,
                    "worker_name": "collector",
                    "status": "succeeded",
                    "started_at_kst": "2026-07-01T10:00:00+09:00",
                    "finished_at_kst": "2026-07-01T10:00:02+09:00",
                    "duration_seconds": 2,
                    "error_count": 0,
                    "last_error": None,
                    "summary_json": "{}",
                    "created_at_kst": "2026-07-01T10:00:02+09:00",
                }
            ],
            total=3,
        )

        page = get_worker_run_page(connection, worker_name="collector", status="succeeded", limit=1, offset=1)

        self.assertEqual(page.total, 3)
        self.assertEqual(page.limit, 1)
        self.assertEqual(page.offset, 1)
        self.assertEqual(page.worker_name, "collector")
        self.assertEqual(page.status, "succeeded")
        self.assertEqual(page.records[0].id, 7)
        self.assertTrue(page.to_record()["has_previous"])
        self.assertTrue(page.to_record()["has_next"])

    def test_get_worker_run_page_returns_created_at_range(self) -> None:
        connection = FakeConnection(total=0)

        page = get_worker_run_page(
            connection,
            created_from_kst="2026-07-01T10:00",
            created_to_kst="2026-07-01T11:00",
        )

        self.assertEqual(page.created_from_kst, "2026-07-01T10:00:00+09:00")
        self.assertEqual(page.created_to_kst, "2026-07-01T11:00:00+09:00")
        self.assertEqual(page.to_record()["created_from_kst"], "2026-07-01T10:00:00+09:00")
        executed_sql = "\n".join(query for query, _ in connection.cursor_obj.executed)
        self.assertIn("created_at_kst >= %s", executed_sql)
        self.assertIn("created_at_kst <= %s", executed_sql)

    def test_rejects_unknown_worker_name(self) -> None:
        with self.assertRaises(WorkerRunHistoryError):
            list_worker_runs(FakeConnection(), worker_name="other")

    def test_rejects_unknown_worker_status(self) -> None:
        with self.assertRaises(WorkerRunHistoryError):
            list_worker_runs(FakeConnection(), status="broken")

    def test_rejects_invalid_created_at_range(self) -> None:
        with self.assertRaises(WorkerRunHistoryError):
            get_worker_run_page(FakeConnection(), created_from_kst="not-a-date")
        with self.assertRaises(WorkerRunHistoryError):
            get_worker_run_page(
                FakeConnection(),
                created_from_kst="2026-07-01T12:00",
                created_to_kst="2026-07-01T11:00",
            )

    def test_lists_failed_worker_runs_after_id(self) -> None:
        connection = FakeConnection(
            rows=[
                {
                    "id": 8,
                    "worker_name": "collector",
                    "status": "failed",
                    "started_at_kst": "2026-06-30T10:00:00+09:00",
                    "finished_at_kst": "2026-06-30T10:00:01+09:00",
                    "duration_seconds": 1,
                    "error_count": 1,
                    "last_error": "boom",
                    "summary_json": '{"errors":["boom"]}',
                    "created_at_kst": "2026-06-30T10:00:01+09:00",
                }
            ]
        )

        runs = list_failed_worker_runs(connection, after_id=7, limit=10, ascending=True)

        self.assertEqual(runs[0].id, 8)
        query, params = connection.cursor_obj.executed[0]
        self.assertIn("status = 'failed'", query)
        self.assertIn("id > %s", query)
        self.assertIn("ORDER BY id ASC", query)
        self.assertEqual(params, (7, 10))

    def test_get_worker_run_by_id(self) -> None:
        connection = FakeConnection(
            rows=[
                {
                    "id": 9,
                    "worker_name": "collector",
                    "status": "failed",
                    "started_at_kst": "2026-07-01T10:00:00+09:00",
                    "finished_at_kst": "2026-07-01T10:00:02+09:00",
                    "duration_seconds": 2,
                    "error_count": 2,
                    "last_error": "telemetry_jobs: RuntimeError: boom",
                    "summary_json": '{"collection":{"queued_match_jobs":2},"errors":["one","two"]}',
                    "created_at_kst": "2026-07-01T10:00:02+09:00",
                }
            ]
        )

        run = get_worker_run(connection, 9)

        self.assertEqual(run.id, 9)
        self.assertEqual(run.worker_name, "collector")
        self.assertEqual(run.summary["collection"]["queued_match_jobs"], 2)
        query, params = connection.cursor_obj.executed[0]
        self.assertIn("WHERE id = %s", query)
        self.assertEqual(params, (9,))

    def test_get_worker_run_raises_for_missing_id(self) -> None:
        with self.assertRaises(WorkerRunHistoryError):
            get_worker_run(FakeConnection(rows=[]), 99)

    def test_get_worker_run_rejects_invalid_id(self) -> None:
        with self.assertRaises(WorkerRunHistoryError):
            get_worker_run(FakeConnection(), 0)

    def test_get_latest_worker_run_id(self) -> None:
        connection = FakeConnection(latest_id=55)

        self.assertEqual(get_latest_worker_run_id(connection), 55)


class FakeConnection:
    def __init__(
        self,
        rows: list[dict[str, object]] | None = None,
        latest_id: int = 0,
        total: int | None = None,
    ) -> None:
        self.cursor_obj = FakeCursor(rows or [], latest_id=latest_id, total=total)

    def cursor(self) -> "FakeCursor":
        return self.cursor_obj


class FakeCursor:
    def __init__(self, rows: list[dict[str, object]], latest_id: int = 0, total: int | None = None) -> None:
        self.rows = rows
        self.latest_id = latest_id
        self.total = len(rows) if total is None else total
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

    def fetchone(self) -> dict[str, object] | None:
        last_query = self.executed[-1][0] if self.executed else ""
        if "COALESCE(MAX(id)" in last_query:
            return {"latest_id": self.latest_id}
        if "COUNT(*) AS total" in last_query:
            return {"total": self.total}
        if "FROM worker_run_history" in last_query:
            return self.rows[0] if self.rows else None
        return None


if __name__ == "__main__":
    unittest.main()
