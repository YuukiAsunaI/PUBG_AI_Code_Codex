from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from pubg_ai.telemetry_job_processor import TelemetryJobProcessingError, TelemetryJobProcessor


OFFICIAL_URL = "https://telemetry-cdn.pubg.com/bluehole-pubg/match.json"


@dataclass
class FakeResponse:
    status_code: int
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    content: bytes = b""


def test_redirect_target_is_validated_before_second_request() -> None:
    calls: list[tuple[str, bool]] = []

    def request_get(url: str, **kwargs: object) -> FakeResponse:
        calls.append((url, bool(kwargs["follow_redirects"])))
        return FakeResponse(
            status_code=302,
            url=url,
            headers={"location": "http://127.0.0.1:3306/private"},
        )

    processor = TelemetryJobProcessor(object(), object(), request_get=request_get)  # type: ignore[arg-type]

    with pytest.raises(TelemetryJobProcessingError, match="official PUBG HTTPS CDN"):
        processor._fetch_telemetry(OFFICIAL_URL)

    assert calls == [(OFFICIAL_URL, False)]


def test_relative_official_redirect_is_followed_with_redirects_disabled() -> None:
    calls: list[tuple[str, bool]] = []

    def request_get(url: str, **kwargs: object) -> FakeResponse:
        calls.append((url, bool(kwargs["follow_redirects"])))
        if len(calls) == 1:
            return FakeResponse(status_code=302, url=url, headers={"location": "/final.json"})
        return FakeResponse(
            status_code=200,
            url=url,
            headers={"content-type": "application/json"},
            content=b"[]",
        )

    processor = TelemetryJobProcessor(object(), object(), request_get=request_get)  # type: ignore[arg-type]
    download = processor._fetch_telemetry(OFFICIAL_URL)

    assert download.source_url == "https://telemetry-cdn.pubg.com/final.json"
    assert calls == [(OFFICIAL_URL, False), (download.source_url, False)]
