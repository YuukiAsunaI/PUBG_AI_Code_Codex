from __future__ import annotations

from decimal import Decimal
import unittest

from pubg_ai.data_quality import (
    DataQualityCheck,
    PlayerIntelligenceAudit,
    _json_value,
)


class DataQualityTests(unittest.TestCase):
    def test_audit_passes_only_when_every_check_passes(self) -> None:
        passing = DataQualityCheck("coverage", "커버리지", 10, 10, True)
        failing = DataQualityCheck("rows", "행 수", 0, 1, False)

        report = PlayerIntelligenceAudit(
            generated_at_kst="2026-08-22T00:00:00+09:00",
            counts={},
            parser_versions=[],
            item_source_totals={},
            event_catalog={},
            checks=[passing, failing],
        )

        self.assertFalse(report.passed)
        self.assertFalse(report.to_record()["passed"])

    def test_mysql_decimals_are_json_safe(self) -> None:
        self.assertEqual(_json_value(Decimal("12")), 12)
        self.assertEqual(_json_value(Decimal("12.5")), 12.5)


if __name__ == "__main__":
    unittest.main()
