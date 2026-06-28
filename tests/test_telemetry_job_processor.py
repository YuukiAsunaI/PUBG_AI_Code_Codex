from __future__ import annotations

import gzip
import unittest

from pubg_ai.telemetry_job_processor import _looks_like_json_bytes, _maybe_decompress_gzip


class TelemetryJobProcessorTests(unittest.TestCase):
    def test_detects_json_like_payloads(self) -> None:
        self.assertTrue(_looks_like_json_bytes(b' [{"_T":"LogMatchStart"}]'))
        self.assertTrue(_looks_like_json_bytes(b' {"events":[]}'))
        self.assertFalse(_looks_like_json_bytes(b""))
        self.assertFalse(_looks_like_json_bytes(b"<html>not json</html>"))

    def test_decompresses_gzip_payloads_when_needed(self) -> None:
        body = b'[{"_T":"LogMatchStart"}]'

        self.assertEqual(_maybe_decompress_gzip(gzip.compress(body)), body)
        self.assertEqual(_maybe_decompress_gzip(body), body)


if __name__ == "__main__":
    unittest.main()
