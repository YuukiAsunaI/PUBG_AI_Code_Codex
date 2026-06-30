from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping
import hashlib
import json

from pubg_ai.system_alerts import SystemAlert
from pubg_ai.time_utils import now_kst, to_kst


class AlertHistoryError(RuntimeError):
    """Raised when alert history cannot be updated or read."""


ALERT_HISTORY_SOURCES = {"all", "storage", "worker"}
ALERT_HISTORY_STATES = {"all", "active", "current", "acknowledged", "snoozed", "resolved"}


@dataclass(frozen=True)
class AlertHistoryRecord:
    id: int
    alert_key: str
    source: str
    severity: str
    title: str
    message: str
    metadata: dict[str, Any]
    first_seen_at_kst: str | None
    last_seen_at_kst: str | None
    last_notified_at_kst: str | None
    acknowledged_at_kst: str | None
    snoozed_until_kst: str | None
    resolved_at_kst: str | None
    updated_at_kst: str | None

    def is_acknowledged(self) -> bool:
        return self.acknowledged_at_kst is not None

    def is_snoozed(self, reference: datetime | None = None) -> bool:
        snoozed_until = _parse_iso_datetime(self.snoozed_until_kst)
        if snoozed_until is None:
            return False
        return snoozed_until > (reference or now_kst())

    def is_suppressed(self, reference: datetime | None = None) -> bool:
        return self.is_acknowledged() or self.is_snoozed(reference)

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["is_acknowledged"] = self.is_acknowledged()
        record["is_snoozed"] = self.is_snoozed()
        record["is_suppressed"] = self.is_suppressed()
        return record


@dataclass(frozen=True)
class AlertHistoryPage:
    records: list[AlertHistoryRecord]
    total: int
    limit: int
    offset: int
    source: str
    state: str

    def to_record(self) -> dict[str, Any]:
        return {
            "records": [record.to_record() for record in self.records],
            "total": self.total,
            "limit": self.limit,
            "offset": self.offset,
            "source": self.source,
            "state": self.state,
            "has_previous": self.offset > 0,
            "has_next": self.offset + len(self.records) < self.total,
        }


def sync_alert_history(connection: Any, alerts: list[SystemAlert]) -> list[AlertHistoryRecord]:
    now = now_kst()
    hashes: list[str] = []
    for alert in alerts:
        alert_hash = alert_key_hash(alert.key)
        hashes.append(alert_hash)
        seen_at = _parse_iso_datetime(alert.created_at_kst) or now
        metadata_json = json.dumps(alert.metadata or {}, ensure_ascii=False, separators=(",", ":"), default=str)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO system_alert_history (
                    alert_key_hash,
                    alert_key,
                    source,
                    severity,
                    title,
                    message,
                    metadata_json,
                    first_seen_at_kst,
                    last_seen_at_kst,
                    resolved_at_kst,
                    updated_at_kst
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s)
                ON DUPLICATE KEY UPDATE
                    source = VALUES(source),
                    severity = VALUES(severity),
                    title = VALUES(title),
                    message = VALUES(message),
                    metadata_json = VALUES(metadata_json),
                    last_seen_at_kst = VALUES(last_seen_at_kst),
                    acknowledged_at_kst = IF(
                        system_alert_history.resolved_at_kst IS NULL,
                        system_alert_history.acknowledged_at_kst,
                        NULL
                    ),
                    snoozed_until_kst = IF(
                        system_alert_history.resolved_at_kst IS NULL,
                        system_alert_history.snoozed_until_kst,
                        NULL
                    ),
                    resolved_at_kst = NULL,
                    updated_at_kst = VALUES(updated_at_kst)
                """,
                (
                    alert_hash,
                    alert.key,
                    alert.source,
                    alert.severity,
                    alert.title,
                    alert.message,
                    metadata_json,
                    _mysql_datetime(seen_at),
                    _mysql_datetime(seen_at),
                    _mysql_datetime(now),
                ),
            )

    _resolve_missing_storage_alerts(connection, hashes, now)
    return list_alert_history(connection, active_only=True, limit=max(50, len(hashes)))


def list_alert_history(
    connection: Any,
    *,
    active_only: bool = False,
    limit: int = 50,
    offset: int = 0,
    source: str | None = None,
    state: str = "all",
) -> list[AlertHistoryRecord]:
    bounded_limit = max(1, min(int(limit), 200))
    bounded_offset = max(0, int(offset))
    _, _, where, params = _history_filter_clause(source=source, state=state, active_only=active_only)
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT
                id,
                alert_key,
                source,
                severity,
                title,
                message,
                metadata_json,
                first_seen_at_kst,
                last_seen_at_kst,
                last_notified_at_kst,
                acknowledged_at_kst,
                snoozed_until_kst,
                resolved_at_kst,
                updated_at_kst
            FROM system_alert_history
            {where}
            ORDER BY last_seen_at_kst DESC, id DESC
            LIMIT %s OFFSET %s
            """,
            tuple(params + [bounded_limit, bounded_offset]),
        )
        rows = cursor.fetchall()
    return [_row_to_record(row) for row in rows]


def count_alert_history(
    connection: Any,
    *,
    active_only: bool = False,
    source: str | None = None,
    state: str = "all",
) -> int:
    _, _, where, params = _history_filter_clause(source=source, state=state, active_only=active_only)
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT COUNT(*) AS total
            FROM system_alert_history
            {where}
            """,
            tuple(params),
        )
        row = cursor.fetchone()
    return int((row or {}).get("total") or 0)


def get_alert_history_page(
    connection: Any,
    *,
    limit: int = 50,
    offset: int = 0,
    source: str | None = None,
    state: str = "all",
) -> AlertHistoryPage:
    normalized_source, normalized_state, _, _ = _history_filter_clause(
        source=source,
        state=state,
        active_only=False,
    )
    bounded_limit = max(1, min(int(limit), 200))
    bounded_offset = max(0, int(offset))
    records = list_alert_history(
        connection,
        limit=bounded_limit,
        offset=bounded_offset,
        source=normalized_source,
        state=normalized_state,
    )
    total = count_alert_history(connection, source=normalized_source, state=normalized_state)
    return AlertHistoryPage(
        records=records,
        total=total,
        limit=bounded_limit,
        offset=bounded_offset,
        source=normalized_source,
        state=normalized_state,
    )


def acknowledge_alert(connection: Any, alert_id: int) -> AlertHistoryRecord:
    timestamp = now_kst()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE system_alert_history
            SET acknowledged_at_kst = %s,
                updated_at_kst = %s
            WHERE id = %s
            """,
            (_mysql_datetime(timestamp), _mysql_datetime(timestamp), int(alert_id)),
        )
    return get_alert_history_record(connection, alert_id)


def snooze_alert(connection: Any, alert_id: int, minutes: int) -> AlertHistoryRecord:
    if minutes < 1:
        raise AlertHistoryError("snooze minutes must be 1 or greater.")
    timestamp = now_kst()
    snoozed_until = timestamp + timedelta(minutes=min(minutes, 60 * 24 * 30))
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE system_alert_history
            SET snoozed_until_kst = %s,
                updated_at_kst = %s
            WHERE id = %s
            """,
            (_mysql_datetime(snoozed_until), _mysql_datetime(timestamp), int(alert_id)),
        )
    return get_alert_history_record(connection, alert_id)


def mark_alert_notified(connection: Any, alert_id: int) -> AlertHistoryRecord:
    timestamp = now_kst()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE system_alert_history
            SET last_notified_at_kst = %s,
                updated_at_kst = %s
            WHERE id = %s
            """,
            (_mysql_datetime(timestamp), _mysql_datetime(timestamp), int(alert_id)),
        )
    return get_alert_history_record(connection, alert_id)


def get_alert_history_record(connection: Any, alert_id: int) -> AlertHistoryRecord:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                id,
                alert_key,
                source,
                severity,
                title,
                message,
                metadata_json,
                first_seen_at_kst,
                last_seen_at_kst,
                last_notified_at_kst,
                acknowledged_at_kst,
                snoozed_until_kst,
                resolved_at_kst,
                updated_at_kst
            FROM system_alert_history
            WHERE id = %s
            """,
            (int(alert_id),),
        )
        row = cursor.fetchone()
    if not row:
        raise AlertHistoryError(f"alert history record not found: {alert_id}")
    return _row_to_record(row)


def visible_alert_records(records: list[AlertHistoryRecord]) -> list[AlertHistoryRecord]:
    reference = now_kst()
    return [
        record
        for record in records
        if record.resolved_at_kst is None and not record.is_suppressed(reference)
    ]


def alert_key_hash(alert_key: str) -> str:
    return hashlib.sha256(alert_key.encode("utf-8")).hexdigest()


def _history_filter_clause(
    *,
    source: str | None,
    state: str,
    active_only: bool,
) -> tuple[str, str, str, list[Any]]:
    normalized_source = (source or "all").strip().lower() or "all"
    if normalized_source not in ALERT_HISTORY_SOURCES:
        raise AlertHistoryError(f"invalid alert source filter: {source}")

    normalized_state = (state or "all").strip().lower() or "all"
    if active_only and normalized_state == "all":
        normalized_state = "active"
    if normalized_state not in ALERT_HISTORY_STATES:
        raise AlertHistoryError(f"invalid alert state filter: {state}")

    clauses: list[str] = []
    params: list[Any] = []
    if normalized_source != "all":
        clauses.append("source = %s")
        params.append(normalized_source)

    if normalized_state == "active":
        clauses.append("resolved_at_kst IS NULL")
    elif normalized_state == "current":
        clauses.append("resolved_at_kst IS NULL")
        clauses.append("acknowledged_at_kst IS NULL")
        clauses.append("(snoozed_until_kst IS NULL OR snoozed_until_kst <= %s)")
        params.append(_mysql_datetime(now_kst()))
    elif normalized_state == "acknowledged":
        clauses.append("acknowledged_at_kst IS NOT NULL")
    elif normalized_state == "snoozed":
        clauses.append("snoozed_until_kst IS NOT NULL")
        clauses.append("snoozed_until_kst > %s")
        params.append(_mysql_datetime(now_kst()))
    elif normalized_state == "resolved":
        clauses.append("resolved_at_kst IS NOT NULL")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return normalized_source, normalized_state, where, params


def _resolve_missing_storage_alerts(connection: Any, active_hashes: list[str], timestamp: datetime) -> None:
    params: list[Any] = [_mysql_datetime(timestamp), _mysql_datetime(timestamp)]
    if active_hashes:
        placeholders = ", ".join(["%s"] * len(active_hashes))
        params.extend(active_hashes)
        condition = f"AND alert_key_hash NOT IN ({placeholders})"
    else:
        condition = ""
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            UPDATE system_alert_history
            SET resolved_at_kst = %s,
                updated_at_kst = %s
            WHERE source = 'storage'
                AND resolved_at_kst IS NULL
                {condition}
            """,
            tuple(params),
        )


def _row_to_record(row: Mapping[str, Any]) -> AlertHistoryRecord:
    return AlertHistoryRecord(
        id=int(row.get("id") or 0),
        alert_key=str(row.get("alert_key") or ""),
        source=str(row.get("source") or ""),
        severity=str(row.get("severity") or ""),
        title=str(row.get("title") or ""),
        message=str(row.get("message") or ""),
        metadata=_json_object(row.get("metadata_json")),
        first_seen_at_kst=_iso_datetime(row.get("first_seen_at_kst")),
        last_seen_at_kst=_iso_datetime(row.get("last_seen_at_kst")),
        last_notified_at_kst=_iso_datetime(row.get("last_notified_at_kst")),
        acknowledged_at_kst=_iso_datetime(row.get("acknowledged_at_kst")),
        snoozed_until_kst=_iso_datetime(row.get("snoozed_until_kst")),
        resolved_at_kst=_iso_datetime(row.get("resolved_at_kst")),
        updated_at_kst=_iso_datetime(row.get("updated_at_kst")),
    )


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str) and value.strip():
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else {"value": loaded}
    return {}


def _parse_iso_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return to_kst(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return to_kst(datetime.fromisoformat(value.strip()))
    except ValueError:
        return None


def _iso_datetime(value: Any) -> str | None:
    if isinstance(value, datetime):
        return to_kst(value).isoformat()
    if isinstance(value, str) and value.strip():
        parsed = _parse_iso_datetime(value)
        return parsed.isoformat() if parsed else value
    return None


def _mysql_datetime(value: datetime | None) -> datetime | None:
    return to_kst(value).replace(tzinfo=None) if value is not None else None
