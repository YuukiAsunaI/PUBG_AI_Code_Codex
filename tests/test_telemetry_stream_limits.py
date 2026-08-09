from __future__ import annotations

from dataclasses import dataclass

import pytest

from pubg_ai.telemetry_job_processor import (
    TelemetryJobProcessingError,
    _join_limited_chunks,
    _stream_httpx_get,
)


def test_chunk_reader_stops_when_download_limit_is_exceeded() -> None:
    yielded: list[bytes] = []

    def chunks():
        for value in (b"1234", b"5678", b"must-not-be-read"):
            yielded.append(value)
            yield value

    with pytest.raises(TelemetryJobProcessingError, match="exceeds"):
        _join_limited_chunks(chunks(), max_bytes=6)

    assert yielded == [b"1234", b"5678"]


def test_httpx_stream_path_disables_redirects_and_reads_bounded_chunks() -> None:
    module = FakeHttpx()

    response = _stream_httpx_get(
        module,
        "https://telemetry-cdn.pubg.com/match.json",
        headers={"Accept": "application/json"},
        timeout=10.0,
        max_bytes=10,
    )

    assert response.content == b"[]"
    assert module.call[0] == "GET"
    assert module.call[2]["follow_redirects"] is False


@dataclass
class FakeResponse:
    status_code: int = 200
    headers: dict[str, str] = None  # type: ignore[assignment]
    url: str = "https://telemetry-cdn.pubg.com/match.json"

    def __post_init__(self) -> None:
        self.headers = {"content-type": "application/json"}

    def iter_bytes(self):
        yield b"["
        yield b"]"

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        return None


class FakeHttpx:
    def __init__(self) -> None:
        self.call: tuple[str, str, dict[str, object]] | None = None

    def stream(self, method: str, url: str, **kwargs: object) -> FakeResponse:
        self.call = (method, url, kwargs)
        return FakeResponse()
