from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from math import isfinite
from typing import Any, Mapping

from pubg_ai.time_utils import now_kst


DEFAULT_MAX_JOB_ATTEMPTS = 5
DEFAULT_JOB_RETRY_BASE_SECONDS = 15.0
DEFAULT_JOB_RETRY_MAX_SECONDS = 300.0
DEFAULT_STALE_RUNNING_SECONDS = 900


@dataclass(frozen=True)
class ApiJobRetryDecision:
    should_retry: bool
    attempt: int
    max_attempts: int
    delay_seconds: float | None
    next_run_at_kst: datetime | None
    error: str

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["next_run_at_kst"] = (
            self.next_run_at_kst.isoformat() if self.next_run_at_kst else None
        )
        return record


def decide_api_job_retry(
    job: Mapping[str, Any],
    exc: Exception,
    *,
    current_time: datetime | None = None,
    max_attempts: int = DEFAULT_MAX_JOB_ATTEMPTS,
) -> ApiJobRetryDecision:
    if not 1 <= max_attempts <= 20:
        raise ValueError("max_attempts must be between 1 and 20.")

    attempt = max(0, _integer(job.get("attempts"))) + 1
    retryable = bool(getattr(exc, "retryable", False))
    should_retry = retryable and attempt < max_attempts
    delay_seconds = _retry_delay_seconds(exc, attempt) if should_retry else None
    now = current_time or now_kst()
    next_run = now + timedelta(seconds=delay_seconds) if delay_seconds is not None else None
    return ApiJobRetryDecision(
        should_retry=should_retry,
        attempt=attempt,
        max_attempts=max_attempts,
        delay_seconds=delay_seconds,
        next_run_at_kst=next_run,
        error=_safe_error(exc),
    )


def stale_running_cutoff(
    *,
    current_time: datetime | None = None,
    stale_after_seconds: int = DEFAULT_STALE_RUNNING_SECONDS,
) -> datetime:
    if not 1 <= stale_after_seconds <= 86400:
        raise ValueError("stale_after_seconds must be between 1 and 86400.")
    return (current_time or now_kst()) - timedelta(seconds=stale_after_seconds)


def _retry_delay_seconds(exc: Exception, attempt: int) -> float:
    requested = _finite_non_negative(getattr(exc, "retry_after_seconds", None))
    if requested is not None:
        return min(DEFAULT_JOB_RETRY_MAX_SECONDS, requested)
    exponential = DEFAULT_JOB_RETRY_BASE_SECONDS * (2 ** max(0, attempt - 1))
    return min(DEFAULT_JOB_RETRY_MAX_SECONDS, exponential)


def _integer(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _finite_non_negative(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(parsed) or parsed < 0:
        return None
    return parsed


def _safe_error(exc: Exception) -> str:
    return f"{exc.__class__.__name__}: {exc}"[:1000]
