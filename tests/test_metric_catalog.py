from __future__ import annotations

import unittest

from pubg_ai.metric_catalog import metric_catalog_records, metric_definition
from pubg_ai.telemetry_event_catalog import get_telemetry_event_definition


class MetricCatalogTests(unittest.TestCase):
    def test_headshot_hit_rate_uses_hits_not_fired_shots(self) -> None:
        definition = metric_definition("headshot_hit_rate")
        self.assertIsNotNone(definition)
        assert definition is not None
        self.assertIn("전체 명중 횟수", definition.formula_ko)
        self.assertIn("빗나간 탄환", definition.caveat_ko)

    def test_catalog_filters_categories_and_keeps_unknown_events_visible(self) -> None:
        self.assertTrue(metric_catalog_records(category="combat"))
        for event_type, domain in (
            ("LogPlayerTakeDamage", "combat"),
            ("LogItemPickupFromVehicleTrunk", "loot"),
            ("LogPlayerPosition", "movement"),
            ("LogCarePackageLand", "map"),
        ):
            definition = get_telemetry_event_definition(event_type)
            self.assertEqual(definition.support, "normalized")
            self.assertEqual(definition.domain, domain)
        unknown = get_telemetry_event_definition("LogFutureFeature")
        self.assertEqual(unknown.support, "raw_only")
        self.assertEqual(unknown.domain, "unclassified")


if __name__ == "__main__":
    unittest.main()
