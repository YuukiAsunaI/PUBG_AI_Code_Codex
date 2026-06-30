from __future__ import annotations

import unittest

from pubg_ai.alert_history import (
    ALERT_HISTORY_EXPORT_LIMIT,
    AlertHistoryRecord,
    acknowledge_alert,
    alert_key_hash,
    get_alert_history_page,
    list_alert_history,
    snooze_alert,
    sync_alert_history,
    visible_alert_records,
)
from pubg_ai.system_alerts import SystemAlert


class AlertHistoryTests(unittest.TestCase):
    def test_sync_alert_history_upserts_and_returns_active_records(self) -> None:
        connection = FakeConnection(rows=[_alert_row()])
        alert = SystemAlert(
            key="storage:raw_data_dir:D:/BackUP/raw:missing",
            source="storage",
            severity="error",
            title="raw_data_dir storage alert",
            message="path missing",
            created_at_kst="2026-06-30T10:00:00+09:00",
            metadata={"role": "raw_data_dir"},
        )

        records = sync_alert_history(connection, [alert])

        self.assertEqual(records[0].alert_key, alert.key)
        executed_sql = "\n".join(query for query, _ in connection.cursor_obj.executed)
        self.assertIn("INSERT INTO system_alert_history", executed_sql)
        self.assertIn("acknowledged_at_kst = IF", executed_sql)
        self.assertIn("snoozed_until_kst = IF", executed_sql)
        self.assertIn("source = 'storage'", executed_sql)
        self.assertEqual(alert_key_hash(alert.key), alert_key_hash(alert.key))

    def test_acknowledge_and_snooze_update_record_state(self) -> None:
        connection = FakeConnection(rows=[_alert_row()])

        acknowledged = acknowledge_alert(connection, 7)
        snoozed = snooze_alert(connection, 7, 60)

        self.assertEqual(acknowledged.id, 7)
        self.assertEqual(snoozed.id, 7)
        executed_sql = "\n".join(query for query, _ in connection.cursor_obj.executed)
        self.assertIn("acknowledged_at_kst", executed_sql)
        self.assertIn("snoozed_until_kst", executed_sql)

    def test_history_page_applies_source_state_limit_and_offset(self) -> None:
        connection = FakeConnection(rows=[_alert_row()], total=12)

        page = get_alert_history_page(
            connection,
            source="storage",
            state="resolved",
            limit=25,
            offset=50,
        )

        self.assertEqual(page.total, 12)
        self.assertEqual(page.limit, 25)
        self.assertEqual(page.offset, 50)
        self.assertEqual(page.source, "storage")
        self.assertEqual(page.state, "resolved")
        executed_sql = "\n".join(query for query, _ in connection.cursor_obj.executed)
        self.assertIn("source = %s", executed_sql)
        self.assertIn("resolved_at_kst IS NOT NULL", executed_sql)
        self.assertIn("LIMIT %s OFFSET %s", executed_sql)
        self.assertIn("COUNT(*) AS total", executed_sql)

    def test_export_limit_can_request_more_than_page_limit(self) -> None:
        connection = FakeConnection(rows=[_alert_row()])

        list_alert_history(connection, limit=ALERT_HISTORY_EXPORT_LIMIT, max_limit=ALERT_HISTORY_EXPORT_LIMIT)

        self.assertEqual(connection.cursor_obj.executed[-1][1][-2:], (ALERT_HISTORY_EXPORT_LIMIT, 0))

    def test_visible_alert_records_filters_resolved_acknowledged_and_snoozed_records(self) -> None:
        active = _record(id=1)
        acknowledged = _record(id=2, acknowledged_at_kst="2026-06-30T10:00:00+09:00")
        snoozed = _record(id=3, snoozed_until_kst="2099-01-01T00:00:00+09:00")
        resolved = _record(id=4, resolved_at_kst="2026-06-30T11:00:00+09:00")

        self.assertEqual(visible_alert_records([active, acknowledged, snoozed, resolved]), [active])


class FakeConnection:
    def __init__(self, rows: list[dict[str, object]], total: int | None = None) -> None:
        self.cursor_obj = FakeCursor(rows, total=total)

    def cursor(self) -> "FakeCursor":
        return self.cursor_obj


class FakeCursor:
    def __init__(self, rows: list[dict[str, object]], total: int | None = None) -> None:
        self.rows = rows
        self.total = total if total is not None else len(rows)
        self.query = ""
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def execute(self, query: str, params: tuple[object, ...] = ()) -> None:
        self.query = query
        self.executed.append((query, params))

    def fetchall(self) -> list[dict[str, object]]:
        return self.rows

    def fetchone(self) -> dict[str, object]:
        if "COUNT(*) AS total" in self.query:
            return {"total": self.total}
        return self.rows[0]


def _alert_row() -> dict[str, object]:
    return {
        "id": 7,
        "alert_key": "storage:raw_data_dir:D:/BackUP/raw:missing",
        "source": "storage",
        "severity": "error",
        "title": "raw_data_dir storage alert",
        "message": "path missing",
        "metadata_json": '{"role":"raw_data_dir"}',
        "first_seen_at_kst": "2026-06-30T10:00:00+09:00",
        "last_seen_at_kst": "2026-06-30T10:01:00+09:00",
        "last_notified_at_kst": None,
        "acknowledged_at_kst": None,
        "snoozed_until_kst": None,
        "resolved_at_kst": None,
        "updated_at_kst": "2026-06-30T10:01:00+09:00",
    }


def _record(
    *,
    id: int,
    acknowledged_at_kst: str | None = None,
    snoozed_until_kst: str | None = None,
    resolved_at_kst: str | None = None,
) -> AlertHistoryRecord:
    return AlertHistoryRecord(
        id=id,
        alert_key=f"worker:{id}",
        source="worker",
        severity="error",
        title="worker failed",
        message="worker failed",
        metadata={},
        first_seen_at_kst="2026-06-30T10:00:00+09:00",
        last_seen_at_kst="2026-06-30T10:01:00+09:00",
        last_notified_at_kst=None,
        acknowledged_at_kst=acknowledged_at_kst,
        snoozed_until_kst=snoozed_until_kst,
        resolved_at_kst=resolved_at_kst,
        updated_at_kst="2026-06-30T10:01:00+09:00",
    )


if __name__ == "__main__":
    unittest.main()
