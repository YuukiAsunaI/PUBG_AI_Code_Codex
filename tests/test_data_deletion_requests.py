from __future__ import annotations

from datetime import datetime, timedelta
import unittest

from pubg_ai.data_deletion_requests import (
    DataDeletionRequestError,
    DataDeletionRequestService,
    normalize_deletion_scope,
)
from pubg_ai.player_registry import RegisteredPlayer


REFERENCE_KST = datetime(2026, 7, 11, 20, 0, 0)


class DataDeletionRequestServiceTests(unittest.TestCase):
    def test_create_request_records_pending_request_and_audit_event(self) -> None:
        row = _request_row(request_id=7, status="pending")
        connection = ScriptedConnection(
            [
                {"all": []},
                {"one": None},
                {"lastrowid": 7, "rowcount": 1},
                {"rowcount": 1},
                {"one": row},
            ]
        )

        request = DataDeletionRequestService(connection).create_request(
            player=_player(),
            deletion_scope="원본",
            requested_by_discord_user_id="100",
            requested_guild_id="10",
            requested_channel_id="20",
            reason="관리자 검토 요청",
            reference_kst=REFERENCE_KST,
        )

        self.assertEqual(request.id, 7)
        self.assertEqual(request.deletion_scope, "raw")
        self.assertEqual(request.status, "pending")
        self.assertEqual(request.to_record()["requested_at_kst"], "2026-07-11T20:00:00+09:00")
        self.assertEqual(connection.begin_count, 2)
        self.assertEqual(connection.commit_count, 2)
        self.assertEqual(connection.rollback_count, 0)
        self.assertTrue(
            any("INSERT INTO data_deletion_request_events" in query for query, _ in connection.executed)
        )

    def test_duplicate_active_request_is_rejected(self) -> None:
        connection = ScriptedConnection([{"all": []}, {"one": {"id": 9}}])

        with self.assertRaisesRegex(DataDeletionRequestError, "already exists: 9"):
            DataDeletionRequestService(connection).create_request(
                player=_player(),
                deletion_scope="raw",
                requested_by_discord_user_id="100",
                reference_kst=REFERENCE_KST,
            )

        self.assertEqual(connection.rollback_count, 1)

    def test_local_approval_changes_state_and_records_actor(self) -> None:
        pending = _request_row(request_id=7, status="pending")
        approved = {
            **pending,
            "status": "approved",
            "reviewed_by": "local:local-manager",
            "reviewed_at_kst": REFERENCE_KST,
            "review_note": "대상 확인",
        }
        connection = ScriptedConnection(
            [
                {"one": pending},
                {"rowcount": 1},
                {"rowcount": 1},
                {"one": approved},
            ]
        )

        request = DataDeletionRequestService(connection).approve_request(
            7,
            actor_id="local-manager",
            note="대상 확인",
            reference_kst=REFERENCE_KST,
        )

        self.assertEqual(request.status, "approved")
        self.assertEqual(request.reviewed_by, "local:local-manager")
        event_params = next(
            params
            for query, params in connection.executed
            if "INSERT INTO data_deletion_request_events" in query
        )
        self.assertEqual(event_params[1:4], ("approved", "local", "local-manager"))

    def test_expired_request_cannot_be_approved(self) -> None:
        expired_row = _request_row(
            request_id=7,
            status="pending",
            expires_at_kst=REFERENCE_KST - timedelta(minutes=1),
        )
        connection = ScriptedConnection(
            [
                {"one": expired_row},
                {"rowcount": 1},
                {"rowcount": 1},
            ]
        )

        with self.assertRaisesRegex(DataDeletionRequestError, "expired before local approval"):
            DataDeletionRequestService(connection).approve_request(
                7,
                actor_id="local-manager",
                reference_kst=REFERENCE_KST,
            )

        self.assertEqual(connection.commit_count, 1)
        self.assertEqual(connection.rollback_count, 0)

    def test_scope_aliases_and_unknown_scope(self) -> None:
        self.assertEqual(normalize_deletion_scope("등록"), "registration")
        self.assertEqual(normalize_deletion_scope("db"), "normalized")
        self.assertEqual(normalize_deletion_scope("리플레이"), "replay")
        self.assertEqual(normalize_deletion_scope("전체"), "all")
        with self.assertRaises(DataDeletionRequestError):
            normalize_deletion_scope("secret")


class ScriptedConnection:
    def __init__(self, steps: list[dict[str, object]]) -> None:
        self.steps = list(steps)
        self.executed: list[tuple[str, tuple[object, ...] | list[object]]] = []
        self.cursor_obj = ScriptedCursor(self)
        self.begin_count = 0
        self.commit_count = 0
        self.rollback_count = 0

    def cursor(self) -> "ScriptedCursor":
        return self.cursor_obj

    def begin(self) -> None:
        self.begin_count += 1

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1


class ScriptedCursor:
    def __init__(self, connection: ScriptedConnection) -> None:
        self.connection = connection
        self.current: dict[str, object] = {}
        self.rowcount = 0
        self.lastrowid = 0

    def __enter__(self) -> "ScriptedCursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, query: str, params: tuple[object, ...] | list[object] = ()) -> None:
        if not self.connection.steps:
            raise AssertionError(f"unexpected SQL: {query}")
        self.current = self.connection.steps.pop(0)
        self.rowcount = int(self.current.get("rowcount", 0))
        self.lastrowid = int(self.current.get("lastrowid", 0))
        self.connection.executed.append((" ".join(query.split()), params))

    def fetchone(self) -> dict[str, object] | None:
        value = self.current.get("one")
        return value if isinstance(value, dict) else None

    def fetchall(self) -> list[dict[str, object]]:
        value = self.current.get("all", [])
        return value if isinstance(value, list) else []


def _player() -> RegisteredPlayer:
    return RegisteredPlayer(
        id=1,
        account_id="account.test",
        shard="steam",
        current_name="Yuuki_Asuna---",
        active=False,
        public_profile=True,
        registered_guild_id="10",
    )


def _request_row(
    *,
    request_id: int,
    status: str,
    expires_at_kst: datetime | None = None,
) -> dict[str, object]:
    return {
        "id": request_id,
        "registered_player_id": 1,
        "account_id": "account.test",
        "shard": "steam",
        "player_name": "Yuuki_Asuna---",
        "deletion_scope": "raw",
        "status": status,
        "reason": "관리자 검토 요청",
        "requested_by_discord_user_id": "100",
        "requested_guild_id": "10",
        "requested_channel_id": "20",
        "requested_at_kst": REFERENCE_KST,
        "expires_at_kst": expires_at_kst or REFERENCE_KST + timedelta(hours=24),
        "reviewed_by": None,
        "reviewed_at_kst": None,
        "review_note": None,
        "executed_at_kst": None,
        "execution_summary_json": None,
        "updated_at_kst": REFERENCE_KST,
    }


if __name__ == "__main__":
    unittest.main()
