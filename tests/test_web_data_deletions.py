from __future__ import annotations

from datetime import datetime, timedelta
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from pubg_ai.data_deletion_requests import DataDeletionRequest, DataDeletionRequestEvent
from pubg_ai.web.app import create_app


class WebDataDeletionTests(unittest.TestCase):
    def test_list_detail_and_local_approval_never_report_execution_enabled(self) -> None:
        pending = _request(status="pending")
        approved = _request(status="approved")
        event = DataDeletionRequestEvent(
            id=1,
            request_id=17,
            event_type="requested",
            actor_type="discord",
            actor_id="100",
            note="검토 요청",
            details_json={"status": "pending"},
            created_at_kst=datetime(2026, 7, 11, 20, 0, 0),
        )
        service = MagicMock()
        service.list_requests.return_value = [pending]
        service.get_request.return_value = pending
        service.list_events.return_value = [event]
        service.approve_request.return_value = approved
        connections: list[FakeConnection] = []

        def connection_factory(*_: object, **__: object) -> FakeConnection:
            connection = FakeConnection()
            connections.append(connection)
            return connection

        with (
            patch("pubg_ai.web.app.connect_mysql", side_effect=connection_factory),
            patch("pubg_ai.web.app.DataDeletionRequestService", return_value=service),
        ):
            client = TestClient(create_app())
            list_response = client.get("/data-deletions?status=pending&limit=50")
            detail_response = client.get("/data-deletions/17")
            approve_response = client.post(
                "/data-deletions/17/approve",
                json={"actor_id": "local-owner", "note": "대상 확인"},
            )
            execute_response = client.post(
                "/data-deletions/17/execute",
                json={"actor_id": "local-owner"},
            )

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["requests"][0]["status"], "pending")
        self.assertEqual(detail_response.status_code, 200)
        self.assertFalse(detail_response.json()["execution_enabled"])
        self.assertEqual(detail_response.json()["events"][0]["event_type"], "requested")
        self.assertEqual(approve_response.status_code, 200)
        self.assertEqual(approve_response.json()["request"]["status"], "approved")
        self.assertFalse(approve_response.json()["execution_enabled"])
        self.assertEqual(execute_response.status_code, 404)
        service.approve_request.assert_called_once_with(
            17,
            actor_id="local-owner",
            note="대상 확인",
        )
        self.assertTrue(all(connection.closed for connection in connections))


class FakeConnection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _request(*, status: str) -> DataDeletionRequest:
    requested_at = datetime(2026, 7, 11, 20, 0, 0)
    return DataDeletionRequest(
        id=17,
        registered_player_id=1,
        account_id="account.test",
        shard="steam",
        player_name="Yuuki_Asuna---",
        deletion_scope="raw",
        status=status,
        reason="검토 요청",
        requested_by_discord_user_id="100",
        requested_guild_id="10",
        requested_channel_id="20",
        requested_at_kst=requested_at,
        expires_at_kst=requested_at + timedelta(hours=24),
        reviewed_by="local:local-owner" if status == "approved" else None,
        reviewed_at_kst=requested_at if status == "approved" else None,
        review_note="대상 확인" if status == "approved" else None,
        updated_at_kst=requested_at,
    )


if __name__ == "__main__":
    unittest.main()
