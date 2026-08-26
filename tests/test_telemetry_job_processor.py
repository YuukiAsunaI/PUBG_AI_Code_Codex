from __future__ import annotations

import gzip
import unittest

from pubg_ai.telemetry_job_processor import (
    TelemetryJobProcessor,
    TelemetryJobProcessingError,
    _looks_like_json_bytes,
    _maybe_decompress_gzip,
    _validate_telemetry_url,
)


class TelemetryJobProcessorTests(unittest.TestCase):
    def test_custom_match_is_excluded_before_payload_lookup_or_download(self) -> None:
        processor = RecordingTelemetryProcessor(
            {
                "match_id": "match-1",
                "shard": "steam",
                "telemetry_url": "https://telemetry-cdn.pubg.com/match-1",
                "created_at_kst": None,
                "map_name": "Baltic_Main",
                "game_mode": "squad-fpp",
                "match_type": "official",
                "is_custom_match": 1,
            }
        )

        processed = processor._process_job({"shard": "steam", "target_id": "match-1"})

        self.assertEqual(processed.status, "excluded")
        self.assertEqual(processor.payload_lookup_calls, 0)
        self.assertEqual(processor.download_calls, 0)

    def test_training_match_is_excluded_before_payload_lookup_or_download(self) -> None:
        processor = RecordingTelemetryProcessor(
            {
                "match_id": "match-2",
                "shard": "steam",
                "telemetry_url": "https://telemetry-cdn.pubg.com/match-2",
                "created_at_kst": None,
                "map_name": "Baltic_Main",
                "game_mode": "solo",
                "match_type": "airoyale",
                "is_custom_match": 0,
            }
        )

        processed = processor._process_job({"shard": "steam", "target_id": "match-2"})

        self.assertEqual(processed.status, "excluded")
        self.assertEqual(processor.payload_lookup_calls, 0)
        self.assertEqual(processor.download_calls, 0)

    def test_detects_json_like_payloads(self) -> None:
        self.assertTrue(_looks_like_json_bytes(b' [{"_T":"LogMatchStart"}]'))
        self.assertTrue(_looks_like_json_bytes(b' {"events":[]}'))
        self.assertFalse(_looks_like_json_bytes(b""))
        self.assertFalse(_looks_like_json_bytes(b"<html>not json</html>"))

    def test_accepts_only_official_pubg_telemetry_https_urls(self) -> None:
        _validate_telemetry_url("https://telemetry-cdn.pubg.com/bluehole-pubg/match.json")

        for value in (
            "http://telemetry-cdn.pubg.com/match.json",
            "https://evil.example/match.json",
            "https://telemetry-cdn.pubg.com.evil.example/match.json",
            "https://user@telemetry-cdn.pubg.com/match.json",
            "https://telemetry-cdn.pubg.com:8443/match.json",
        ):
            with self.subTest(value=value):
                with self.assertRaises(TelemetryJobProcessingError):
                    _validate_telemetry_url(value)

    def test_rejects_gzip_payload_that_expands_past_limit(self) -> None:
        with self.assertRaisesRegex(ValueError, "exceeds"):
            _maybe_decompress_gzip(gzip.compress(b"x" * 100), max_output_bytes=10)

    def test_decompresses_gzip_payloads_when_needed(self) -> None:
        body = b'[{"_T":"LogMatchStart"}]'

        self.assertEqual(_maybe_decompress_gzip(gzip.compress(body)), body)
        self.assertEqual(_maybe_decompress_gzip(body), body)


class RecordingTelemetryProcessor(TelemetryJobProcessor):
    def __init__(self, match: dict[str, object]) -> None:
        super().__init__(object(), object())  # type: ignore[arg-type]
        self.match = match
        self.payload_lookup_calls = 0
        self.download_calls = 0

    def _load_match_for_telemetry(self, *, match_id: str, shard: str) -> dict[str, object]:
        return self.match

    def _telemetry_payload_exists(self, match_id: str) -> bool:
        self.payload_lookup_calls += 1
        return False

    def _fetch_telemetry(self, telemetry_url: str) -> object:
        self.download_calls += 1
        raise AssertionError("excluded telemetry must not be downloaded")


if __name__ == "__main__":
    unittest.main()
