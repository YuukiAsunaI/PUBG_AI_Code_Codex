from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone


KST = timezone(timedelta(hours=9), "KST")


def now_kst() -> datetime:
    return datetime.now(KST)


def to_kst(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=KST)
    return value.astimezone(KST)


def isoformat_kst(value: datetime | None = None) -> str:
    return to_kst(value).isoformat() if value is not None else now_kst().isoformat()
