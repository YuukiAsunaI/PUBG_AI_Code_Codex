from __future__ import annotations

from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from pubg_ai.api_job_retry import decide_api_job_retry, stale_running_cutoff
from pubg_ai.pubg_client import PubgApiError


KST = ZoneInfo("Asia/Seoul")


class ApiJobRetryTests(unittest.TestCase):
    def test_retryable_error_uses_supplied_retry_after(self) -> None:
        now = datetime(2026, 8, 2, 12, 0, tzinfo=KST)
        decision = decide_api_job_retry(
            {"attempts": 1},
            PubgApiError(
                "rate limited",
                status_code=429,
                retryable=True,
                retry_after_seconds=42.5,
            ),
            current_time=now,
        )

        self.assertTrue(decision.should_retry)
        self.assertEqual(decision.attempt, 2)
        self.assertEqual(decision.delay_seconds, 42.5)
        self.assertEqual(decision.next_run_at_kst, datetime(2026, 8, 2, 12, 0, 42, 500000, tzinfo=KST))
        self.assertIn("rate limited", decision.error)

    def test_retryable_error_uses_bounded_exponential_delay(self) -> None:
        error = PubgApiError("offline", retryable=True)

        first = decide_api_job_retry({"attempts": 0}, error)
        fourth = decide_api_job_retry({"attempts": 3}, error)

        self.assertEqual(first.delay_seconds, 15.0)
        self.assertEqual(fourth.delay_seconds, 120.0)

    def test_non_retryable_and_exhausted_errors_are_terminal(self) -> None:
        non_retryable = decide_api_job_retry(
            {"attempts": 0},
            ValueError("bad payload"),
        )
        exhausted = decide_api_job_retry(
            {"attempts": 4},
            PubgApiError("still offline", retryable=True),
        )

        self.assertFalse(non_retryable.should_retry)
        self.assertIsNone(non_retryable.next_run_at_kst)
        self.assertFalse(exhausted.should_retry)
        self.assertEqual(exhausted.attempt, 5)

    def test_stale_cutoff_uses_kst_aware_time(self) -> None:
        now = datetime(2026, 8, 2, 12, 0, tzinfo=KST)

        cutoff = stale_running_cutoff(current_time=now, stale_after_seconds=900)

        self.assertEqual(cutoff, datetime(2026, 8, 2, 11, 45, tzinfo=KST))


if __name__ == "__main__":
    unittest.main()
