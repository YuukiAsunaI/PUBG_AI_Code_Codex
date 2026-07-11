from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from typing import Any

from pubg_ai.player_registry import RegisteredPlayer
from pubg_ai.time_utils import now_kst, to_kst


DELETION_SCOPES = {"registration", "normalized", "raw", "replay", "all"}
DELETION_STATUSES = {"pending", "approved", "rejected", "cancelled", "expired", "executed", "failed"}
DEFAULT_DELETION_REQUEST_TTL_HOURS = 24


class DataDeletionRequestError(RuntimeError):
    """Raised when a deletion request or state transition is invalid."""


@dataclass(frozen=True)
class DataDeletionRequest:
    id: int
    registered_player_id: int | None
    account_id: str
    shard: str
    player_name: str
    deletion_scope: str
    status: str
    reason: str | None
    requested_by_discord_user_id: str
    requested_guild_id: str | None
    requested_channel_id: str | None
    requested_at_kst: datetime
    expires_at_kst: datetime
    reviewed_by: str | None = None
    reviewed_at_kst: datetime | None = None
    review_note: str | None = None
    executed_at_kst: datetime | None = None
    execution_summary_json: dict[str, Any] | None = None
    updated_at_kst: datetime | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "registered_player_id": self.registered_player_id,
            "account_id": self.account_id,
            "shard": self.shard,
            "player_name": self.player_name,
            "deletion_scope": self.deletion_scope,
            "status": self.status,
            "reason": self.reason,
            "requested_by_discord_user_id": self.requested_by_discord_user_id,
            "requested_guild_id": self.requested_guild_id,
            "requested_channel_id": self.requested_channel_id,
            "requested_at_kst": _iso_kst(self.requested_at_kst),
            "expires_at_kst": _iso_kst(self.expires_at_kst),
            "reviewed_by": self.reviewed_by,
            "reviewed_at_kst": _iso_kst(self.reviewed_at_kst),
            "review_note": self.review_note,
            "executed_at_kst": _iso_kst(self.executed_at_kst),
            "execution_summary_json": self.execution_summary_json,
            "updated_at_kst": _iso_kst(self.updated_at_kst),
        }


@dataclass(frozen=True)
class DataDeletionRequestEvent:
    id: int
    request_id: int
    event_type: str
    actor_type: str
    actor_id: str
    note: str | None
    details_json: dict[str, Any] | None
    created_at_kst: datetime

    def to_record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "request_id": self.request_id,
            "event_type": self.event_type,
            "actor_type": self.actor_type,
            "actor_id": self.actor_id,
            "note": self.note,
            "details_json": self.details_json,
            "created_at_kst": _iso_kst(self.created_at_kst),
        }


class DataDeletionRequestService:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def create_request(
        self,
        *,
        player: RegisteredPlayer,
        deletion_scope: str,
        requested_by_discord_user_id: str,
        requested_guild_id: str | None = None,
        requested_channel_id: str | None = None,
        reason: str | None = None,
        ttl_hours: int = DEFAULT_DELETION_REQUEST_TTL_HOURS,
        reference_kst: datetime | None = None,
    ) -> DataDeletionRequest:
        scope = normalize_deletion_scope(deletion_scope)
        requester = _required_text(requested_by_discord_user_id, "requested_by_discord_user_id")
        reason = _optional_limited_text(reason, "reason", 500)
        if not 1 <= ttl_hours <= 168:
            raise DataDeletionRequestError("ttl_hours must be between 1 and 168.")

        timestamp = _mysql_kst(reference_kst or now_kst())
        expires_at = timestamp + timedelta(hours=ttl_hours)
        self.expire_pending_requests(reference_kst=timestamp)
        _begin(self.connection)
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id
                    FROM data_deletion_requests
                    WHERE account_id = %s
                      AND shard = %s
                      AND deletion_scope = %s
                      AND (status = 'approved' OR (status = 'pending' AND expires_at_kst > %s))
                    ORDER BY id DESC
                    LIMIT 1
                    FOR UPDATE
                    """,
                    (player.account_id, player.shard, scope, timestamp),
                )
                existing = cursor.fetchone()
                if existing:
                    raise DataDeletionRequestError(
                        f"active deletion request already exists: {int(existing['id'])}."
                    )

                cursor.execute(
                    """
                    INSERT INTO data_deletion_requests (
                        registered_player_id,
                        account_id,
                        shard,
                        player_name,
                        deletion_scope,
                        status,
                        reason,
                        requested_by_discord_user_id,
                        requested_guild_id,
                        requested_channel_id,
                        requested_at_kst,
                        expires_at_kst,
                        updated_at_kst
                    ) VALUES (%s, %s, %s, %s, %s, 'pending', %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        player.id,
                        player.account_id,
                        player.shard,
                        player.current_name,
                        scope,
                        reason,
                        requester,
                        _optional_text(requested_guild_id),
                        _optional_text(requested_channel_id),
                        timestamp,
                        expires_at,
                        timestamp,
                    ),
                )
                request_id = int(cursor.lastrowid)
                _insert_event(
                    cursor,
                    request_id=request_id,
                    event_type="requested",
                    actor_type="discord",
                    actor_id=requester,
                    note=reason,
                    details={"deletion_scope": scope, "status": "pending"},
                    created_at_kst=timestamp,
                )
            _commit(self.connection)
        except Exception:
            _rollback(self.connection)
            raise
        return self.get_request(request_id)

    def list_requests(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        reference_kst: datetime | None = None,
    ) -> list[DataDeletionRequest]:
        if status == "all":
            status = None
        if status is not None and status not in DELETION_STATUSES:
            raise DataDeletionRequestError(f"unknown deletion request status: {status}.")
        if not 1 <= limit <= 200:
            raise DataDeletionRequestError("limit must be between 1 and 200.")
        self.expire_pending_requests(reference_kst=reference_kst)

        where = "WHERE status = %s" if status else ""
        params: list[Any] = [status] if status else []
        params.append(limit)
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT *
                FROM data_deletion_requests
                {where}
                ORDER BY requested_at_kst DESC, id DESC
                LIMIT %s
                """,
                params,
            )
            rows = cursor.fetchall()
        return [_request_from_row(row) for row in rows]

    def get_request(self, request_id: int) -> DataDeletionRequest:
        request_id = _positive_id(request_id)
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT * FROM data_deletion_requests WHERE id = %s", (request_id,))
            row = cursor.fetchone()
        if not row:
            raise DataDeletionRequestError(f"deletion request {request_id} was not found.")
        return _request_from_row(row)

    def list_events(self, request_id: int, *, limit: int = 100) -> list[DataDeletionRequestEvent]:
        request_id = _positive_id(request_id)
        if not 1 <= limit <= 500:
            raise DataDeletionRequestError("event limit must be between 1 and 500.")
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM data_deletion_request_events
                WHERE request_id = %s
                ORDER BY created_at_kst ASC, id ASC
                LIMIT %s
                """,
                (request_id, limit),
            )
            rows = cursor.fetchall()
        return [_event_from_row(row) for row in rows]

    def approve_request(
        self,
        request_id: int,
        *,
        actor_id: str,
        note: str | None = None,
        reference_kst: datetime | None = None,
    ) -> DataDeletionRequest:
        return self._transition(
            request_id,
            allowed_statuses={"pending"},
            next_status="approved",
            actor_type="local",
            actor_id=actor_id,
            note=note,
            reference_kst=reference_kst,
        )

    def reject_request(
        self,
        request_id: int,
        *,
        actor_id: str,
        note: str | None = None,
        reference_kst: datetime | None = None,
    ) -> DataDeletionRequest:
        return self._transition(
            request_id,
            allowed_statuses={"pending"},
            next_status="rejected",
            actor_type="local",
            actor_id=actor_id,
            note=note,
            reference_kst=reference_kst,
        )

    def cancel_request(
        self,
        request_id: int,
        *,
        actor_type: str,
        actor_id: str,
        note: str | None = None,
        reference_kst: datetime | None = None,
    ) -> DataDeletionRequest:
        return self._transition(
            request_id,
            allowed_statuses={"pending", "approved"},
            next_status="cancelled",
            actor_type=actor_type,
            actor_id=actor_id,
            note=note,
            reference_kst=reference_kst,
        )

    def expire_pending_requests(self, *, reference_kst: datetime | None = None) -> int:
        timestamp = _mysql_kst(reference_kst or now_kst())
        _begin(self.connection)
        expired_count = 0
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, deletion_scope
                    FROM data_deletion_requests
                    WHERE status = 'pending' AND expires_at_kst <= %s
                    ORDER BY id ASC
                    FOR UPDATE
                    """,
                    (timestamp,),
                )
                rows = cursor.fetchall()
                for row in rows:
                    request_id = int(row["id"])
                    cursor.execute(
                        """
                        UPDATE data_deletion_requests
                        SET status = 'expired',
                            reviewed_by = 'system',
                            reviewed_at_kst = %s,
                            review_note = 'request expired before local approval',
                            updated_at_kst = %s
                        WHERE id = %s AND status = 'pending'
                        """,
                        (timestamp, timestamp, request_id),
                    )
                    if cursor.rowcount != 1:
                        continue
                    expired_count += 1
                    _insert_event(
                        cursor,
                        request_id=request_id,
                        event_type="expired",
                        actor_type="system",
                        actor_id="system",
                        note="request expired before local approval",
                        details={"deletion_scope": row["deletion_scope"], "status": "expired"},
                        created_at_kst=timestamp,
                    )
            _commit(self.connection)
        except Exception:
            _rollback(self.connection)
            raise
        return expired_count

    def _transition(
        self,
        request_id: int,
        *,
        allowed_statuses: set[str],
        next_status: str,
        actor_type: str,
        actor_id: str,
        note: str | None,
        reference_kst: datetime | None,
    ) -> DataDeletionRequest:
        request_id = _positive_id(request_id)
        if actor_type not in {"discord", "local", "system"}:
            raise DataDeletionRequestError(f"unknown actor_type: {actor_type}.")
        actor_id = _required_text(actor_id, "actor_id")
        note = _optional_limited_text(note, "note", 1000)
        if next_status not in DELETION_STATUSES:
            raise DataDeletionRequestError(f"unknown next status: {next_status}.")

        timestamp = _mysql_kst(reference_kst or now_kst())
        expired = False
        _begin(self.connection)
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM data_deletion_requests WHERE id = %s FOR UPDATE",
                    (request_id,),
                )
                row = cursor.fetchone()
                if not row:
                    raise DataDeletionRequestError(f"deletion request {request_id} was not found.")
                request = _request_from_row(row)

                if request.status == "pending" and _mysql_kst(request.expires_at_kst) <= timestamp:
                    cursor.execute(
                        """
                        UPDATE data_deletion_requests
                        SET status = 'expired', reviewed_by = 'system', reviewed_at_kst = %s,
                            review_note = 'request expired before local approval', updated_at_kst = %s
                        WHERE id = %s AND status = 'pending'
                        """,
                        (timestamp, timestamp, request_id),
                    )
                    _insert_event(
                        cursor,
                        request_id=request_id,
                        event_type="expired",
                        actor_type="system",
                        actor_id="system",
                        note="request expired before local approval",
                        details={"from_status": "pending", "to_status": "expired"},
                        created_at_kst=timestamp,
                    )
                    expired = True
                elif request.status not in allowed_statuses:
                    expected = ", ".join(sorted(allowed_statuses))
                    raise DataDeletionRequestError(
                        f"deletion request {request_id} is {request.status}; expected {expected}."
                    )
                else:
                    cursor.execute(
                        """
                        UPDATE data_deletion_requests
                        SET status = %s,
                            reviewed_by = %s,
                            reviewed_at_kst = %s,
                            review_note = %s,
                            updated_at_kst = %s
                        WHERE id = %s AND status = %s
                        """,
                        (
                            next_status,
                            f"{actor_type}:{actor_id}",
                            timestamp,
                            note,
                            timestamp,
                            request_id,
                            request.status,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise DataDeletionRequestError("deletion request changed concurrently; retry.")
                    _insert_event(
                        cursor,
                        request_id=request_id,
                        event_type=next_status,
                        actor_type=actor_type,
                        actor_id=actor_id,
                        note=note,
                        details={"from_status": request.status, "to_status": next_status},
                        created_at_kst=timestamp,
                    )
            _commit(self.connection)
        except Exception:
            _rollback(self.connection)
            raise

        if expired:
            raise DataDeletionRequestError(f"deletion request {request_id} expired before local approval.")
        return self.get_request(request_id)


def normalize_deletion_scope(value: str) -> str:
    normalized = _required_text(value, "deletion_scope").lower()
    aliases = {
        "registration": "registration",
        "register": "registration",
        "등록": "registration",
        "normalized": "normalized",
        "db": "normalized",
        "분석": "normalized",
        "raw": "raw",
        "원본": "raw",
        "replay": "replay",
        "리플레이": "replay",
        "all": "all",
        "전체": "all",
    }
    scope = aliases.get(normalized)
    if scope is None:
        known = ", ".join(sorted(DELETION_SCOPES))
        raise DataDeletionRequestError(f"unknown deletion scope '{value}'. Known scopes: {known}.")
    return scope


def _insert_event(
    cursor: Any,
    *,
    request_id: int,
    event_type: str,
    actor_type: str,
    actor_id: str,
    note: str | None,
    details: dict[str, Any] | None,
    created_at_kst: datetime,
) -> None:
    cursor.execute(
        """
        INSERT INTO data_deletion_request_events (
            request_id, event_type, actor_type, actor_id, note, details_json, created_at_kst
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            request_id,
            event_type,
            actor_type,
            actor_id,
            note,
            json.dumps(details, ensure_ascii=False) if details is not None else None,
            created_at_kst,
        ),
    )


def _request_from_row(row: dict[str, Any]) -> DataDeletionRequest:
    execution_summary = row.get("execution_summary_json")
    if isinstance(execution_summary, str):
        try:
            execution_summary = json.loads(execution_summary)
        except json.JSONDecodeError:
            execution_summary = None
    return DataDeletionRequest(
        id=int(row["id"]),
        registered_player_id=int(row["registered_player_id"]) if row.get("registered_player_id") is not None else None,
        account_id=str(row["account_id"]),
        shard=str(row["shard"]),
        player_name=str(row["player_name"]),
        deletion_scope=str(row["deletion_scope"]),
        status=str(row["status"]),
        reason=_optional_text(row.get("reason")),
        requested_by_discord_user_id=str(row["requested_by_discord_user_id"]),
        requested_guild_id=_optional_text(row.get("requested_guild_id")),
        requested_channel_id=_optional_text(row.get("requested_channel_id")),
        requested_at_kst=_datetime_value(row["requested_at_kst"]),
        expires_at_kst=_datetime_value(row["expires_at_kst"]),
        reviewed_by=_optional_text(row.get("reviewed_by")),
        reviewed_at_kst=_optional_datetime(row.get("reviewed_at_kst")),
        review_note=_optional_text(row.get("review_note")),
        executed_at_kst=_optional_datetime(row.get("executed_at_kst")),
        execution_summary_json=execution_summary if isinstance(execution_summary, dict) else None,
        updated_at_kst=_optional_datetime(row.get("updated_at_kst")),
    )


def _event_from_row(row: dict[str, Any]) -> DataDeletionRequestEvent:
    details = row.get("details_json")
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except json.JSONDecodeError:
            details = None
    return DataDeletionRequestEvent(
        id=int(row["id"]),
        request_id=int(row["request_id"]),
        event_type=str(row["event_type"]),
        actor_type=str(row["actor_type"]),
        actor_id=str(row["actor_id"]),
        note=_optional_text(row.get("note")),
        details_json=details if isinstance(details, dict) else None,
        created_at_kst=_datetime_value(row["created_at_kst"]),
    )


def _begin(connection: Any) -> None:
    begin = getattr(connection, "begin", None)
    if callable(begin):
        begin()


def _commit(connection: Any) -> None:
    commit = getattr(connection, "commit", None)
    if callable(commit):
        commit()


def _rollback(connection: Any) -> None:
    rollback = getattr(connection, "rollback", None)
    if callable(rollback):
        rollback()


def _positive_id(value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise DataDeletionRequestError("request_id must be a positive integer.") from exc
    if parsed <= 0:
        raise DataDeletionRequestError("request_id must be a positive integer.")
    return parsed


def _required_text(value: str, label: str) -> str:
    text = str(value).strip()
    if not text:
        raise DataDeletionRequestError(f"{label} is required.")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_limited_text(value: Any, label: str, max_length: int) -> str | None:
    text = _optional_text(value)
    if text is not None and len(text) > max_length:
        raise DataDeletionRequestError(f"{label} must be {max_length} characters or fewer.")
    return text


def _datetime_value(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise DataDeletionRequestError(f"invalid datetime value: {value}.") from exc
    raise DataDeletionRequestError(f"invalid datetime value: {value!r}.")


def _optional_datetime(value: Any) -> datetime | None:
    return None if value is None else _datetime_value(value)


def _mysql_kst(value: datetime) -> datetime:
    return to_kst(value).replace(tzinfo=None)


def _iso_kst(value: datetime | None) -> str | None:
    if value is None:
        return None
    return to_kst(value).isoformat()
