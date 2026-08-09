from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any
import unittest

from pubg_ai.match_job_processor import MatchJobProcessor
from pubg_ai.pubg_client import PubgApiError
from pubg_ai.telemetry_job_processor import (
    TelemetryJobProcessingError,
    TelemetryJobProcessor,
)


class RecordingCursor(AbstractContextManager["RecordingCursor"]):
    def __init__(self, connection: "RecordingConnection") -> None:
        self.connection = connection
        self.rowcount = connection.rowcount

    def __enter__(self) -> "RecordingCursor":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self.connection.executed.append((query, params))


class RecordingConnection:
    def __init__(self, *, rowcount: int = 1) -> None:
        self.rowcount = rowcount
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    def cursor(self) -> RecordingCursor:
        return RecordingCursor(self)


class FakeTelemetryResponse:
    def __init__(
        self,
        status_code: int,
        *,
        content: bytes = b"[]",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}
        self.url = "https://telemetry.example.test/match.json"


class ApiJobProcessorRetryTests(unittest.TestCase):
    def test_match_failure_is_requeued_with_delayed_next_run(self) -> None:
        connection = RecordingConnection()
        processor = MatchJobProcessor(connection, object(), object())  # type: ignore[arg-type]

        requeued = processor._handle_job_failure(
            {"id": 7, "attempts": 1},
            PubgApiError(
                "rate limited",
                status_code=429,
                retryable=True,
                retry_after_seconds=30.0,
            ),
        )

        self.assertTrue(requeued)
        query, params = connection.executed[-1]
        self.assertIn("SET status = %s", query)
        self.assertEqual(params[0], "queued")
        self.assertIsNotNone(params[1])
        self.assertIn("rate limited", params[2])
        self.assertEqual(params[-1], 7)

    def test_match_non_retryable_failure_is_terminal(self) -> None:
        connection = RecordingConnection()
        processor = MatchJobProcessor(connection, object(), object())  # type: ignore[arg-type]

        requeued = processor._handle_job_failure(
            {"id": 8, "attempts": 0},
            ValueError("invalid payload"),
        )

        self.assertFalse(requeued)
        _, params = connection.executed[-1]
        self.assertEqual(params[0], "failed")
        self.assertIsNone(params[1])

    def test_match_stale_running_recovery_requeues_and_restores_attempt(self) -> None:
        connection = RecordingConnection(rowcount=2)
        processor = MatchJobProcessor(connection, object(), object())  # type: ignore[arg-type]

        recovered = processor._recover_stale_running_jobs()

        self.assertEqual(recovered, 2)
        query, params = connection.executed[-1]
        self.assertIn("status = 'queued'", query)
        self.assertIn("attempts - 1", query)
        self.assertIn("job_type = 'match'", query)
        self.assertEqual(len(params), 3)

    def test_telemetry_http_error_exposes_retry_metadata(self) -> None:
        response = FakeTelemetryResponse(503, headers={"Retry-After": "17"})
        processor = TelemetryJobProcessor(
            RecordingConnection(),
            object(),  # type: ignore[arg-type]
            request_get=lambda *args, **kwargs: response,
        )

        with self.assertRaises(TelemetryJobProcessingError) as raised:
            processor._fetch_telemetry("https://telemetry.example.test/match.json")

        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.retry_after_seconds, 17.0)

    def test_telemetry_404_is_typed_for_durable_queue_retry(self) -> None:
        response = FakeTelemetryResponse(404)
        processor = TelemetryJobProcessor(
            RecordingConnection(),
            object(),  # type: ignore[arg-type]
            request_get=lambda *args, **kwargs: response,
        )

        with self.assertRaises(TelemetryJobProcessingError) as raised:
            processor._fetch_telemetry("https://telemetry.example.test/match.json")

        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.status_code, 404)

    def test_telemetry_non_json_response_is_retryable(self) -> None:
        response = FakeTelemetryResponse(
            200,
            content=b"<html>temporary CDN response</html>",
            headers={"content-type": "text/html"},
        )
        processor = TelemetryJobProcessor(
            RecordingConnection(),
            object(),  # type: ignore[arg-type]
            request_get=lambda *args, **kwargs: response,
        )

        with self.assertRaises(TelemetryJobProcessingError) as raised:
            processor._fetch_telemetry("https://telemetry.example.test/match.json")

        self.assertTrue(raised.exception.retryable)
        self.assertIn("not JSON-like", str(raised.exception))

    def test_telemetry_stale_running_recovery_uses_telemetry_scope(self) -> None:
        connection = RecordingConnection(rowcount=3)
        processor = TelemetryJobProcessor(connection, object())  # type: ignore[arg-type]

        recovered = processor._recover_stale_running_jobs()

        self.assertEqual(recovered, 3)
        query, _ = connection.executed[-1]
        self.assertIn("job_type = 'telemetry'", query)
        self.assertIn("after worker restart", query)


if __name__ == "__main__":
    unittest.main()
