from __future__ import annotations

import unittest
from typing import Any

import httpx

from pubg_ai.pubg_client import PubgApiClient, PubgApiError, PubgRetryPolicy


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        *,
        headers: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._payload = payload or {"data": []}

    def json(self) -> dict[str, Any]:
        return self._payload


class SequenceGet:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class PubgClientRetryTests(unittest.TestCase):
    def test_429_waits_for_official_reset_header_then_recovers(self) -> None:
        clock = [1000.0]
        sleeps: list[float] = []
        request_get = SequenceGet(
            [
                FakeResponse(
                    429,
                    headers={
                        "X-RateLimit-Limit": "10",
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": "1005",
                    },
                ),
                FakeResponse(
                    200,
                    headers={
                        "X-RateLimit-Limit": "10",
                        "X-RateLimit-Remaining": "9",
                        "X-RateLimit-Reset": "1060",
                    },
                ),
            ]
        )

        def sleep_func(seconds: float) -> None:
            sleeps.append(seconds)
            clock[0] += seconds

        client = PubgApiClient(
            "test-key",
            request_get=request_get,
            sleep_func=sleep_func,
            time_func=lambda: clock[0],
        )
        result = client.lookup_players_by_names("steam", ["Retry_Test"])

        self.assertEqual(result.players, [])
        self.assertEqual(result.rate_limit.remaining, 9)
        self.assertEqual(len(request_get.calls), 2)
        self.assertEqual(sleeps, [5.25])
        self.assertNotIn("test-key", request_get.calls[0]["url"])
        self.assertEqual(request_get.calls[0]["headers"]["Authorization"], "Bearer test-key")

    def test_transport_error_uses_exponential_retry(self) -> None:
        request = httpx.Request("GET", "https://api.pubg.test/status")
        request_get = SequenceGet(
            [
                httpx.ConnectError("offline", request=request),
                FakeResponse(200),
            ]
        )
        sleeps: list[float] = []
        client = PubgApiClient(
            "test-key",
            request_get=request_get,
            sleep_func=sleeps.append,
        )

        result = client.lookup_players_by_names("steam", ["Retry_Test"])

        self.assertEqual(result.players, [])
        self.assertEqual(sleeps, [1.0])
        self.assertEqual(len(request_get.calls), 2)

    def test_persistent_429_is_typed_as_retryable_after_bounded_attempts(self) -> None:
        request_get = SequenceGet([FakeResponse(429), FakeResponse(429), FakeResponse(429)])
        sleeps: list[float] = []
        client = PubgApiClient(
            "test-key",
            retry_policy=PubgRetryPolicy(max_attempts=3, max_total_delay_seconds=10.0),
            request_get=request_get,
            sleep_func=sleeps.append,
        )

        with self.assertRaises(PubgApiError) as raised:
            client.lookup_players_by_names("steam", ["Retry_Test"])

        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(raised.exception.attempts, 3)
        self.assertEqual(raised.exception.retry_after_seconds, 4.0)
        self.assertEqual(sleeps, [1.0, 2.0])
        self.assertEqual(len(request_get.calls), 3)

    def test_match_404_is_typed_for_durable_queue_retry(self) -> None:
        request_get = SequenceGet([FakeResponse(404)])
        sleeps: list[float] = []
        client = PubgApiClient(
            "test-key",
            request_get=request_get,
            sleep_func=sleeps.append,
        )

        with self.assertRaises(PubgApiError) as raised:
            client.fetch_match("steam", "match-not-propagated")

        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(raised.exception.retry_after_seconds, 15.0)
        self.assertEqual(raised.exception.attempts, 1)
        self.assertEqual(sleeps, [])
        self.assertEqual(len(request_get.calls), 1)

    def test_non_retryable_authentication_error_returns_immediately(self) -> None:
        request_get = SequenceGet([FakeResponse(401)])
        sleeps: list[float] = []
        client = PubgApiClient(
            "test-key",
            request_get=request_get,
            sleep_func=sleeps.append,
        )

        with self.assertRaises(PubgApiError) as raised:
            client.refresh_players_by_ids("steam", ["account.test"])

        self.assertFalse(raised.exception.retryable)
        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(raised.exception.attempts, 1)
        self.assertEqual(sleeps, [])
        self.assertEqual(len(request_get.calls), 1)

    def test_retry_policy_rejects_invalid_bounds(self) -> None:
        with self.assertRaises(ValueError):
            PubgRetryPolicy(max_attempts=0)
        with self.assertRaises(ValueError):
            PubgRetryPolicy(base_delay_seconds=2.0, max_delay_seconds=1.0)


if __name__ == "__main__":
    unittest.main()
