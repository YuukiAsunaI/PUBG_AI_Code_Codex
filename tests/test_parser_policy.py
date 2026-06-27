from __future__ import annotations

import unittest

from pubg_ai.parser_policy import CURRENT_TELEMETRY_PARSER_VERSION, ParseRunPolicy


class ParserPolicyTests(unittest.TestCase):
    def test_missing_or_old_parser_version_requires_reparse(self) -> None:
        policy = ParseRunPolicy()

        self.assertTrue(policy.should_reparse(None))
        self.assertTrue(policy.should_reparse("telemetry-parser-v0"))
        self.assertEqual(policy.next_status_for_version("telemetry-parser-v0"), "pending")

    def test_current_parser_version_does_not_require_reparse(self) -> None:
        policy = ParseRunPolicy()

        self.assertFalse(policy.should_reparse(CURRENT_TELEMETRY_PARSER_VERSION))
        self.assertEqual(policy.next_status_for_version(CURRENT_TELEMETRY_PARSER_VERSION), "succeeded")


if __name__ == "__main__":
    unittest.main()

