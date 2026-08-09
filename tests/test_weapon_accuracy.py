import unittest

from pubg_ai.weapon_accuracy import (
    distance_weapon_family,
    summarize_accuracy_rows,
    weapon_accuracy_metric,
    weapon_family,
)


class WeaponAccuracyTests(unittest.TestCase):
    def test_classifies_supported_weapon_families(self) -> None:
        self.assertEqual(weapon_family("WeapHK416_C"), "AR")
        self.assertEqual(weapon_family("WeapMk12_C"), "DMR")
        self.assertEqual(weapon_family("WeapM24_C"), "SR")
        self.assertEqual(weapon_family("WeapSaiga12_C"), "SHOTGUN")
        self.assertEqual(weapon_family("WeapCrossbow_C"), "CROSSBOW")
        self.assertEqual(weapon_family("WeapFutureWeapon_C"), "UNCLASSIFIED")
        self.assertEqual(distance_weapon_family("WeapHK416_C"), "AR")
        self.assertEqual(distance_weapon_family("WeapSaiga12_C"), "OTHER")

    def test_single_projectile_metric_is_an_unclamped_estimate(self) -> None:
        metric = weapon_accuracy_metric("WeapHK416_C", 100, 25)

        self.assertEqual(metric.metric_kind, "estimated_hit_rate")
        self.assertAlmostEqual(metric.estimated_hit_rate or 0, 0.25)
        self.assertAlmostEqual(metric.metric_value or 0, 0.25)
        self.assertTrue(metric.is_percentage)
        self.assertEqual(metric.fire_unit, "round")
        self.assertEqual(metric.quality, "ok")

    def test_shotgun_metric_preserves_pellet_hits_per_shell(self) -> None:
        metric = weapon_accuracy_metric("WeapSaiga12_C", 37, 123)

        self.assertEqual(metric.metric_kind, "pellet_hits_per_shell")
        self.assertIsNone(metric.estimated_hit_rate)
        self.assertAlmostEqual(metric.pellet_hits_per_shell or 0, 123 / 37)
        self.assertFalse(metric.is_percentage)
        self.assertEqual(metric.fire_unit, "shell")
        self.assertEqual(metric.hit_unit, "pellet_hit_event")

    def test_impossible_single_projectile_rate_becomes_event_ratio(self) -> None:
        metric = weapon_accuracy_metric("WeapM24_C", 2, 3)

        self.assertEqual(metric.metric_kind, "hit_events_per_attack")
        self.assertIsNone(metric.estimated_hit_rate)
        self.assertEqual(metric.metric_value, 1.5)
        self.assertFalse(metric.is_percentage)
        self.assertEqual(metric.quality, "hit_events_exceed_attacks")

    def test_aggregate_breakdown_keeps_single_pellet_and_unknown_separate(self) -> None:
        summary = summarize_accuracy_rows(
            [
                {"weapon_code": "WeapHK416_C", "shots_fired": 100, "shots_hit": 20},
                {"weapon_code": "WeapSaiga12_C", "shots_fired": 10, "shots_hit": 35},
                {"weapon_code": "WeapFutureWeapon_C", "shots_fired": 4, "shots_hit": 2},
            ]
        )

        self.assertEqual(summary.attack_events, 114)
        self.assertEqual(summary.hit_events, 57)
        self.assertEqual(summary.single_projectile_attacks, 100)
        self.assertEqual(summary.single_projectile_hit_events, 20)
        self.assertAlmostEqual(summary.estimated_hit_rate or 0, 0.2)
        self.assertEqual(summary.pellet_shells, 10)
        self.assertEqual(summary.pellet_hit_events, 35)
        self.assertAlmostEqual(summary.pellet_hits_per_shell or 0, 3.5)
        self.assertEqual(summary.unclassified_attacks, 4)
        self.assertEqual(summary.unclassified_hit_events, 2)
        self.assertEqual(summary.quality, "contains_unclassified_weapons")


if __name__ == "__main__":
    unittest.main()
