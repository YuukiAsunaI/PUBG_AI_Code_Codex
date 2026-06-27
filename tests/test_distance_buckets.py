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

    def test_over_1km_is_tracked_as_overflow(self) -> None:
        bucket = distance_bucket(1200, "SR")

        self.assertEqual(bucket.label, "1000m+")
        self.assertTrue(bucket.is_overflow)

    def test_negative_distance_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            distance_bucket(-1, "AR")


if __name__ == "__main__":
    unittest.main()
