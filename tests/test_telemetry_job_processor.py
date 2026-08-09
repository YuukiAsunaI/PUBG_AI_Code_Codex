from __future__ import annotations

import gzip
import unittest

from pubg_ai.telemetry_job_processor import (
    TelemetryJobProcessingError,
    _looks_like_json_bytes,
    _maybe_decompress_gzip,
    _validate_telemetry_url,
)


class TelemetryJobProcessorTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
