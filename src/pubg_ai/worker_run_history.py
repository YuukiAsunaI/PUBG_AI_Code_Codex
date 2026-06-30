from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
import json

from pubg_ai.time_utils import now_kst, to_kst


class WorkerRunHistoryError(RuntimeError):
    """Raised when worker cycle history cannot be stored or loaded."""


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
    limit: int = 50,
) -> list[WorkerRunRecord]:
    bounded_limit = max(1, min(int(limit), 200))
    params: list[Any] = []
    where = ""
    if worker_name:
        where = "WHERE worker_name = %s"
        params.append(_validate_worker_name(worker_name))
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
            ORDER BY created_at_kst DESC, id DESC
            LIMIT %s
            """,
            tuple(params),
        )
        rows = cursor.fetchall()

    return [_row_to_record(row) for row in rows]


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
