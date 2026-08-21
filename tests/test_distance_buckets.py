from __future__ import annotations

import unittest

from pubg_ai.distance_buckets import distance_bucket


class DistanceBucketTests(unittest.TestCase):
    def test_ar_close_range_is_fine_grained(self) -> None:
        self.assertEqual(distance_bucket(4.9, "AR").label, "0-5m")
        self.assertEqual(distance_bucket(12, "AR").label, "10-15m")
        self.assertEqual(distance_bucket(24.9, "AR").label, "20-25m")
        self.assertEqual(distance_bucket(74.9, "AR").label, "50-75m")

    def test_ar_uses_100m_buckets_after_100m(self) -> None:
        self.assertEqual(distance_bucket(125, "AR").label, "100-200m")
        self.assertEqual(distance_bucket(999.9, "AR").label, "900-1000m")

    def test_dmr_and_sr_use_100m_buckets_to_1km(self) -> None:
        self.assertEqual(distance_bucket(99.9, "DMR").label, "0-100m")
        self.assertEqual(distance_bucket(450, "DMR").label, "400-500m")
        self.assertEqual(distance_bucket(999.9, "SR").label, "900-1000m")

    def test_smg_and_shotgun_keep_close_range_detail(self) -> None:
        self.assertEqual(distance_bucket(4.9, "SMG").label, "0-5m")
        self.assertEqual(distance_bucket(18, "SHOTGUN").label, "15-20m")
        self.assertEqual(distance_bucket(125, "SMG").label, "100-200m")

    def test_lmg_uses_close_and_long_range_buckets(self) -> None:
        self.assertEqual(distance_bucket(8, "LMG").label, "0-10m")
        self.assertEqual(distance_bucket(42, "LMG").label, "25-50m")
        self.assertEqual(distance_bucket(450, "LMG").label, "400-500m")

    def test_over_1km_is_tracked_as_overflow(self) -> None:
        bucket = distance_bucket(1200, "SR")

        self.assertEqual(bucket.label, "1000m+")
        self.assertTrue(bucket.is_overflow)

    def test_negative_distance_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            distance_bucket(-1, "AR")


if __name__ == "__main__":
    unittest.main()
