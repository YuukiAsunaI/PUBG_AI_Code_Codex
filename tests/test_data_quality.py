from __future__ import annotations

from decimal import Decimal
import unittest

from pubg_ai.data_quality import (
    DataQualityCheck,
    PlayerIntelligenceAudit,
    _eligible_player_match_counts,
    _item_summary_mismatches,
    _item_use_quantity_mismatches,
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

    def test_item_use_quantity_audit_checks_v5_event_semantics(self) -> None:
        class Cursor:
            def __init__(self) -> None:
                self.query = ""
                self.params = ()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def execute(self, query, params):
                self.query = query
                self.params = params

            def fetchone(self):
                return {"value": 0}

        class Connection:
            def __init__(self) -> None:
                self.cursor_obj = Cursor()

            def cursor(self):
                return self.cursor_obj

        connection = Connection()

        self.assertEqual(_item_use_quantity_mismatches(connection), 0)
        self.assertIn("stats.used_quantity <> stats.used_events", connection.cursor_obj.query)
        self.assertEqual(connection.cursor_obj.params, ("items-v5",))

    def test_item_summary_audit_reconciles_every_supported_action(self) -> None:
        class Cursor:
            def __init__(self) -> None:
                self.query = ""
                self.params = ()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def execute(self, query, params):
                self.query = query
                self.params = params

            def fetchone(self):
                return {"value": 0}

        class Connection:
            def __init__(self) -> None:
                self.cursor_obj = Cursor()

            def cursor(self):
                return self.cursor_obj

        connection = Connection()

        self.assertEqual(_item_summary_mismatches(connection), 0)
        for action in (
            "pickup_carepackage",
            "pickup_vehicle_trunk",
            "put_vehicle_trunk",
            "drop",
            "use",
            "equip",
            "unequip",
            "attach",
            "detach",
        ):
            self.assertIn(action, connection.cursor_obj.query)
        self.assertIn("INNER JOIN analysis_matches", connection.cursor_obj.query)
        self.assertIn("stack_count > 0", connection.cursor_obj.query)
        self.assertEqual(connection.cursor_obj.params, ("items-v5",))

    def test_eligible_coverage_excludes_policy_matches_and_recent_ingestion(self) -> None:
        class Cursor:
            def __init__(self) -> None:
                self.query = ""
                self.params = ()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def execute(self, query, params):
                self.query = query
                self.params = params

            def fetchone(self):
                return {"player_matches": 12, "matches": 10}

        class Connection:
            def __init__(self) -> None:
                self.cursor_obj = Cursor()

            def cursor(self):
                return self.cursor_obj

        connection = Connection()

        counts = _eligible_player_match_counts(connection, recent=False)

        self.assertEqual(counts, {"player_matches": 12, "matches": 10})
        self.assertIn("INNER JOIN analysis_matches", connection.cursor_obj.query)
        self.assertIn("TIMESTAMPADD(MINUTE", connection.cursor_obj.query)
        self.assertIn("<=", connection.cursor_obj.query)
        self.assertEqual(connection.cursor_obj.params, (15,))


if __name__ == "__main__":
    unittest.main()
