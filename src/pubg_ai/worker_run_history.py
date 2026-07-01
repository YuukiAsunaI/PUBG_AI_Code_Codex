from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
import json

from pubg_ai.time_utils import now_kst, to_kst


class WorkerRunHistoryError(RuntimeError):
    """Raised when worker cycle history cannot be stored or loaded."""


WORKER_RUN_STATUSES = {"all", "succeeded", "failed"}


@dataclass(frozen=True)
class WorkerRunRecord:
    id: int
    worker_name: str
    status: str
    started_at_kst: str | None
    finished_at_kst: str | None
    duration_seconds: float | None
    error_count: int
    last_error: str | None
    summary: dict[str, Any]
    created_at_kst: str | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WorkerRunPage:
    records: list[WorkerRunRecord]
    total: int
    limit: int
    offset: int
    worker_name: str | None = None
    status: str = "all"

    def to_record(self) -> dict[str, Any]:
        return {
            "records": [record.to_record() for record in self.records],
            "total": self.total,
            "limit": self.limit,
            "offset": self.offset,
            "worker_name": self.worker_name,
            "status": self.status,
            "has_previous": self.offset > 0,
            "has_next": self.offset + len(self.records) < self.total,
        }


def record_worker_cycle(
    connection: Any,
    *,
    worker_name: str,
    cycle: Mapping[str, Any],
) -> WorkerRunRecord:
    name = _validate_worker_name(worker_name)
    summary = dict(cycle)
    errors = _error_list(summary.get("errors"))
    status = "failed" if errors else "succeeded"
    started = _parse_iso_datetime(summary.get("started_at_kst"))
    finished = _parse_iso_datetime(summary.get("finished_at_kst"))
    duration = _optional_float(summary.get("duration_seconds"))
    created = now_kst()
    payload = json.dumps(summary, ensure_ascii=False, separators=(",", ":"), default=str)
    last_error = errors[-1] if errors else None

    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO worker_run_history (
                worker_name,
                status,
                started_at_kst,
                finished_at_kst,
                duration_seconds,
                error_count,
                last_error,
                summary_json,
                created_at_kst
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                name,
                status,
                _mysql_datetime(started),
                _mysql_datetime(finished),
                duration,
                len(errors),
                last_error,
                payload,
                _mysql_datetime(created),
            ),
        )
        record_id = int(getattr(cursor, "lastrowid", 0) or 0)

    return WorkerRunRecord(
        id=record_id,
        worker_name=name,
        status=status,
        started_at_kst=started.isoformat() if started is not None else None,
        finished_at_kst=finished.isoformat() if finished is not None else None,
        duration_seconds=duration,
        error_count=len(errors),
        last_error=last_error,
        summary=summary,
        created_at_kst=created.isoformat(),
    )


def list_worker_runs(
    connection: Any,
    *,
    worker_name: str | None = None,
    status: str = "all",
    limit: int = 50,
    offset: int = 0,
) -> list[WorkerRunRecord]:
    bounded_limit = max(1, min(int(limit), 200))
    bounded_offset = max(0, int(offset))
    where, params = _worker_run_filter_clause(worker_name, status=status)
    params.extend([bounded_limit, bounded_offset])

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT
                id,
                worker_name,
                status,
                started_at_kst,
                finished_at_kst,
                duration_seconds,
                error_count,
                last_error,
                summary_json,
                created_at_kst
            FROM worker_run_history
            {where}
            ORDER BY created_at_kst DESC, id DESC
            LIMIT %s OFFSET %s
            """,
            tuple(params),
        )
        rows = cursor.fetchall()

    return [_row_to_record(row) for row in rows]


def count_worker_runs(
    connection: Any,
    *,
    worker_name: str | None = None,
    status: str = "all",
) -> int:
    where, params = _worker_run_filter_clause(worker_name, status=status)
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT COUNT(*) AS total
            FROM worker_run_history
            {where}
            """,
            tuple(params),
        )
        row = cursor.fetchone()
    return int((row or {}).get("total") or 0)


def get_worker_run_page(
    connection: Any,
    *,
    worker_name: str | None = None,
    status: str = "all",
    limit: int = 50,
    offset: int = 0,
) -> WorkerRunPage:
    bounded_limit = max(1, min(int(limit), 200))
    bounded_offset = max(0, int(offset))
    normalized_worker_name = _validate_worker_name(worker_name) if worker_name else None
    normalized_status = _validate_worker_status(status)
    records = list_worker_runs(
        connection,
        worker_name=normalized_worker_name,
        status=normalized_status,
        limit=bounded_limit,
        offset=bounded_offset,
    )
    total = count_worker_runs(connection, worker_name=normalized_worker_name, status=normalized_status)
    return WorkerRunPage(
        records=records,
        total=total,
        limit=bounded_limit,
        offset=bounded_offset,
        worker_name=normalized_worker_name,
        status=normalized_status,
    )


def list_failed_worker_runs(
    connection: Any,
    *,
    after_id: int | None = None,
    limit: int = 20,
    ascending: bool = False,
) -> list[WorkerRunRecord]:
    bounded_limit = max(1, min(int(limit), 200))
    params: list[Any] = []
    where = "WHERE status = 'failed'"
    if after_id is not None:
        where += " AND id > %s"
        params.append(max(0, int(after_id)))
    order = "id ASC" if ascending else "created_at_kst DESC, id DESC"
    params.append(bounded_limit)

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT
                id,
                worker_name,
                status,
                started_at_kst,
                finished_at_kst,
                duration_seconds,
                error_count,
                last_error,
                summary_json,
                created_at_kst
            FROM worker_run_history
            {where}
            ORDER BY {order}
            LIMIT %s
            """,
            tuple(params),
        )
        rows = cursor.fetchall()

    return [_row_to_record(row) for row in rows]


def get_worker_run(connection: Any, run_id: int) -> WorkerRunRecord:
    try:
        parsed_id = int(run_id)
    except (TypeError, ValueError) as exc:
        raise WorkerRunHistoryError(f"invalid worker run id: {run_id}") from exc
    if parsed_id <= 0:
        raise WorkerRunHistoryError(f"invalid worker run id: {run_id}")

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                id,
                worker_name,
                status,
                started_at_kst,
                finished_at_kst,
                duration_seconds,
                error_count,
                last_error,
                summary_json,
                created_at_kst
            FROM worker_run_history
            WHERE id = %s
            LIMIT 1
            """,
            (parsed_id,),
        )
        row = cursor.fetchone()

    if not row:
        raise WorkerRunHistoryError(f"worker run not found: {parsed_id}")
    return _row_to_record(row)


def get_latest_worker_run_id(connection: Any) -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT COALESCE(MAX(id), 0) AS latest_id FROM worker_run_history")
        row = cursor.fetchone()
    return int((row or {}).get("latest_id") or 0)


def _row_to_record(row: Mapping[str, Any]) -> WorkerRunRecord:
    summary = _json_object(row.get("summary_json"))
    return WorkerRunRecord(
        id=int(row.get("id") or 0),
        worker_name=str(row.get("worker_name") or ""),
        status=str(row.get("status") or ""),
        started_at_kst=_iso_datetime(row.get("started_at_kst")),
        finished_at_kst=_iso_datetime(row.get("finished_at_kst")),
        duration_seconds=_optional_float(row.get("duration_seconds")),
        error_count=int(row.get("error_count") or 0),
        last_error=str(row.get("last_error")) if row.get("last_error") is not None else None,
        summary=summary,
        created_at_kst=_iso_datetime(row.get("created_at_kst")),
    )


def _validate_worker_name(value: str) -> str:
    text = str(value).strip()
    if text not in {"collector", "post_processing"}:
        raise WorkerRunHistoryError(f"unsupported worker_name: {value!r}")
    return text


def _validate_worker_status(value: str) -> str:
    text = str(value).strip().lower()
    if text not in WORKER_RUN_STATUSES:
        raise WorkerRunHistoryError(f"unsupported worker run status: {value!r}")
    return text


def _worker_run_filter_clause(worker_name: str | None, *, status: str = "all") -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if worker_name:
        clauses.append("worker_name = %s")
        params.append(_validate_worker_name(worker_name))

    normalized_status = _validate_worker_status(status)
    if normalized_status != "all":
        clauses.append("status = %s")
        params.append(normalized_status)

    if not clauses:
        return "", []
    return "WHERE " + " AND ".join(clauses), params


def _error_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if value:
        return [str(value)]
    return []


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
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return to_kst(parsed)


def _iso_datetime(value: Any) -> str | None:
    if isinstance(value, datetime):
        return to_kst(value).isoformat()
    if isinstance(value, str) and value.strip():
        parsed = _parse_iso_datetime(value)
        return parsed.isoformat() if parsed is not None else value
    return None


def _mysql_datetime(value: datetime | None) -> datetime | None:
    return to_kst(value).replace(tzinfo=None) if value is not None else None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
