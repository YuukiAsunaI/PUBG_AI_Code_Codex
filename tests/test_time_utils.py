from __future__ import annotations

from datetime import UTC, datetime
import unittest

from pubg_ai.time_utils import KST, isoformat_kst, to_kst


class TimeUtilsTests(unittest.TestCase):
    def test_utc_datetime_is_converted_to_kst(self) -> None:
        value = datetime(2026, 6, 27, 12, 0, tzinfo=UTC)

        self.assertEqual(to_kst(value).hour, 21)
        self.assertEqual(to_kst(value).tzinfo, KST)

    def test_naive_datetime_is_treated_as_kst(self) -> None:
        value = datetime(2026, 6, 27, 12, 0)

        self.assertEqual(to_kst(value).hour, 12)
        self.assertEqual(to_kst(value).tzinfo, KST)

    def test_isoformat_kst_uses_kst_offset(self) -> None:
        value = datetime(2026, 6, 27, 12, 0, tzinfo=UTC)

        self.assertTrue(isoformat_kst(value).endswith("+09:00"))


if __name__ == "__main__":
    unittest.main()
