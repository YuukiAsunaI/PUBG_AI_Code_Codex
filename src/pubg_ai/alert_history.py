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
ALERT_HISTORY_SORTS = {"newest", "oldest", "severity"}
ALERT_HISTORY_PAGE_LIMIT = 200
ALERT_HISTORY_EXPORT_LIMIT = 5000
ALERT_NOTE_TYPES = {"note", "resolution"}


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
    note_count: int = 0
    latest_note: str | None = None
    latest_note_type: str | None = None
    latest_note_at_kst: str | None = None

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
    sort: str = "newest"
    search: str = ""

    def to_record(self) -> dict[str, Any]:
        return {
            "records": [record.to_record() for record in self.records],
            "total": self.total,
            "limit": self.limit,
            "offset": self.offset,
            "source": self.source,
            "state": self.state,
            "sort": self.sort,
            "search": self.search,
            "has_previous": self.offset > 0,
            "has_next": self.offset + len(self.records) < self.total,
        }


@dataclass(frozen=True)
class AlertHistoryNote:
    id: int
    alert_history_id: int
    note_type: str
    note_text: str
    created_by: str | None
    created_at_kst: str | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


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
    max_limit: int = ALERT_HISTORY_PAGE_LIMIT,
    offset: int = 0,
    source: str | None = None,
    state: str = "all",
    sort: str = "newest",
    search: str | None = None,
) -> list[AlertHistoryRecord]:
    bounded_limit = _bounded_limit(limit, max_limit=max_limit)
    bounded_offset = max(0, int(offset))
    _, _, _, where, params = _history_filter_clause(
        source=source,
        state=state,
        active_only=active_only,
        search=search,
    )
    order_by = _history_order_by(sort)
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
                updated_at_kst,
                (
                    SELECT COUNT(*)
                    FROM system_alert_notes
                    WHERE system_alert_notes.alert_history_id = system_alert_history.id
                ) AS note_count,
                (
                    SELECT note_text
                    FROM system_alert_notes
                    WHERE system_alert_notes.alert_history_id = system_alert_history.id
                    ORDER BY created_at_kst DESC, id DESC
                    LIMIT 1
                ) AS latest_note,
                (
                    SELECT note_type
                    FROM system_alert_notes
                    WHERE system_alert_notes.alert_history_id = system_alert_history.id
                    ORDER BY created_at_kst DESC, id DESC
                    LIMIT 1
                ) AS latest_note_type,
                (
                    SELECT created_at_kst
                    FROM system_alert_notes
                    WHERE system_alert_notes.alert_history_id = system_alert_history.id
                    ORDER BY created_at_kst DESC, id DESC
                    LIMIT 1
                ) AS latest_note_at_kst
            FROM system_alert_history
            {where}
            ORDER BY {order_by}
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
    search: str | None = None,
) -> int:
    _, _, _, where, params = _history_filter_clause(
        source=source,
        state=state,
        active_only=active_only,
        search=search,
    )
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
    sort: str = "newest",
    search: str | None = None,
) -> AlertHistoryPage:
    normalized_source, normalized_state, normalized_search, _, _ = _history_filter_clause(
        source=source,
        state=state,
        active_only=False,
        search=search,
    )
    normalized_sort = _normalize_history_sort(sort)
    bounded_limit = _bounded_limit(limit, max_limit=ALERT_HISTORY_PAGE_LIMIT)
    bounded_offset = max(0, int(offset))
    records = list_alert_history(
        connection,
        limit=bounded_limit,
        max_limit=ALERT_HISTORY_PAGE_LIMIT,
        offset=bounded_offset,
        source=normalized_source,
        state=normalized_state,
        sort=normalized_sort,
        search=normalized_search,
    )
    total = count_alert_history(
        connection,
        source=normalized_source,
        state=normalized_state,
        search=normalized_search,
    )
    return AlertHistoryPage(
        records=records,
        total=total,
        limit=bounded_limit,
        offset=bounded_offset,
        source=normalized_source,
        state=normalized_state,
        sort=normalized_sort,
        search=normalized_search,
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
                updated_at_kst,
                (
                    SELECT COUNT(*)
                    FROM system_alert_notes
                    WHERE system_alert_notes.alert_history_id = system_alert_history.id
                ) AS note_count,
                (
                    SELECT note_text
                    FROM system_alert_notes
                    WHERE system_alert_notes.alert_history_id = system_alert_history.id
                    ORDER BY created_at_kst DESC, id DESC
                    LIMIT 1
                ) AS latest_note,
                (
                    SELECT note_type
                    FROM system_alert_notes
                    WHERE system_alert_notes.alert_history_id = system_alert_history.id
                    ORDER BY created_at_kst DESC, id DESC
                    LIMIT 1
                ) AS latest_note_type,
                (
                    SELECT created_at_kst
                    FROM system_alert_notes
                    WHERE system_alert_notes.alert_history_id = system_alert_history.id
                    ORDER BY created_at_kst DESC, id DESC
                    LIMIT 1
                ) AS latest_note_at_kst
            FROM system_alert_history
            WHERE id = %s
            """,
            (int(alert_id),),
        )
        row = cursor.fetchone()
    if not row:
        raise AlertHistoryError(f"alert history record not found: {alert_id}")
    return _row_to_record(row)


def add_alert_note(
    connection: Any,
    alert_id: int,
    note_text: str,
    *,
    note_type: str = "note",
    created_by: str | None = "local-manager",
) -> AlertHistoryNote:
    get_alert_history_record(connection, alert_id)
    normalized_note_type = _normalize_note_type(note_type)
    normalized_text = _normalize_note_text(note_text)
    timestamp = now_kst()
    creator = created_by.strip()[:191] if isinstance(created_by, str) and created_by.strip() else None
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO system_alert_notes (
                alert_history_id,
                note_type,
                note_text,
                created_by,
                created_at_kst
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                int(alert_id),
                normalized_note_type,
                normalized_text,
                creator,
                _mysql_datetime(timestamp),
            ),
        )
        note_id = int(getattr(cursor, "lastrowid", 0) or 0)
    if note_id:
        return get_alert_note(connection, note_id)
    notes = list_alert_notes(connection, alert_id, limit=1)
    if not notes:
        raise AlertHistoryError("alert note was not created.")
    return notes[0]


def list_alert_notes(connection: Any, alert_id: int, *, limit: int = 50) -> list[AlertHistoryNote]:
    bounded_limit = _bounded_limit(limit, max_limit=200)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                id,
                alert_history_id,
                note_type,
                note_text,
                created_by,
                created_at_kst
            FROM system_alert_notes
            WHERE alert_history_id = %s
            ORDER BY created_at_kst DESC, id DESC
            LIMIT %s
            """,
            (int(alert_id), bounded_limit),
        )
        rows = cursor.fetchall()
    return [_note_row_to_record(row) for row in rows]


def get_alert_note(connection: Any, note_id: int) -> AlertHistoryNote:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                id,
                alert_history_id,
                note_type,
                note_text,
                created_by,
                created_at_kst
            FROM system_alert_notes
            WHERE id = %s
            """,
            (int(note_id),),
        )
        row = cursor.fetchone()
    if not row:
        raise AlertHistoryError(f"alert note not found: {note_id}")
    return _note_row_to_record(row)


def visible_alert_records(records: list[AlertHistoryRecord]) -> list[AlertHistoryRecord]:
    reference = now_kst()
    return [
        record
        for record in records
        if record.resolved_at_kst is None and not record.is_suppressed(reference)
    ]


def alert_key_hash(alert_key: str) -> str:
    return hashlib.sha256(alert_key.encode("utf-8")).hexdigest()


def _bounded_limit(limit: int, *, max_limit: int) -> int:
    return max(1, min(int(limit), max(1, int(max_limit))))


def _normalize_note_type(note_type: str) -> str:
    normalized = (note_type or "note").strip().lower()
    if normalized not in ALERT_NOTE_TYPES:
        raise AlertHistoryError(f"invalid alert note type: {note_type}")
    return normalized


def _normalize_note_text(note_text: str) -> str:
    normalized = note_text.strip() if isinstance(note_text, str) else ""
    if not normalized:
        raise AlertHistoryError("alert note text is required.")
    return normalized[:5000]


def _normalize_history_sort(sort: str) -> str:
    normalized = (sort or "newest").strip().lower() or "newest"
    if normalized in {"severity-first", "severity_first"}:
        normalized = "severity"
    if normalized not in ALERT_HISTORY_SORTS:
        raise AlertHistoryError(f"invalid alert history sort: {sort}")
    return normalized


def _normalize_history_search(search: str | None) -> str:
    normalized = search.strip() if isinstance(search, str) else ""
    return normalized[:200]


def _history_order_by(sort: str) -> str:
    normalized = _normalize_history_sort(sort)
    if normalized == "oldest":
        return "last_seen_at_kst ASC, id ASC"
    if normalized == "severity":
        return (
            "CASE severity "
            "WHEN 'error' THEN 0 "
            "WHEN 'warning' THEN 1 "
            "WHEN 'info' THEN 2 "
            "WHEN 'ok' THEN 3 "
            "ELSE 4 END ASC, last_seen_at_kst DESC, id DESC"
        )
    return "last_seen_at_kst DESC, id DESC"


def _history_filter_clause(
    *,
    source: str | None,
    state: str,
    active_only: bool,
    search: str | None = None,
) -> tuple[str, str, str, str, list[Any]]:
    normalized_source = (source or "all").strip().lower() or "all"
    if normalized_source not in ALERT_HISTORY_SOURCES:
        raise AlertHistoryError(f"invalid alert source filter: {source}")

    normalized_state = (state or "all").strip().lower() or "all"
    if active_only and normalized_state == "all":
        normalized_state = "active"
    if normalized_state not in ALERT_HISTORY_STATES:
        raise AlertHistoryError(f"invalid alert state filter: {state}")

    normalized_search = _normalize_history_search(search)
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

    if normalized_search:
        clauses.append("(title LIKE %s OR message LIKE %s)")
        pattern = f"%{normalized_search}%"
        params.extend([pattern, pattern])

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return normalized_source, normalized_state, normalized_search, where, params


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
        note_count=int(row.get("note_count") or 0),
        latest_note=_optional_text(row.get("latest_note")),
        latest_note_type=_optional_text(row.get("latest_note_type")),
        latest_note_at_kst=_iso_datetime(row.get("latest_note_at_kst")),
    )


def _note_row_to_record(row: Mapping[str, Any]) -> AlertHistoryNote:
    return AlertHistoryNote(
        id=int(row.get("id") or 0),
        alert_history_id=int(row.get("alert_history_id") or 0),
        note_type=str(row.get("note_type") or ""),
        note_text=str(row.get("note_text") or ""),
        created_by=_optional_text(row.get("created_by")),
        created_at_kst=_iso_datetime(row.get("created_at_kst")),
    )


def _optional_text(value: Any) -> str | None:
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str) and value:
        return value
    return None


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
