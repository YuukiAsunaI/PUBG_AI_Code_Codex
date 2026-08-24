from __future__ import annotations

import unittest

from pubg_ai.map_regions import (
    MAP_REGION_CATALOG_VERSION,
    MAP_REGION_SOURCE_COMMIT,
    map_region_catalog_record,
    resolve_map_region,
)


class MapRegionTests(unittest.TestCase):
    def test_catalog_is_versioned_and_geometry_is_valid(self) -> None:
        catalog = map_region_catalog_record()

        self.assertEqual(catalog["catalog_version"], MAP_REGION_CATALOG_VERSION)
        self.assertEqual(catalog["source_commit"], MAP_REGION_SOURCE_COMMIT)
        self.assertEqual(catalog["coordinate_origin"], "top-left")
        self.assertEqual(catalog["coordinate_unit"], "centimeter")
        self.assertGreaterEqual(len(catalog["maps"]), 12)

        seen_canonical_maps: set[str] = set()
        static_region_count = 0
        for map_record in catalog["maps"]:
            map_region_ids: set[str] = set()
            canonical_map_name = map_record["canonical_map_name"]
            self.assertEqual(len(map_record["source_sha256"]), 64)
            if map_record["policy"] == "static":
                self.assertGreater(map_record["region_count"], 0)
                if canonical_map_name not in seen_canonical_maps:
                    static_region_count += map_record["region_count"]
                    seen_canonical_maps.add(canonical_map_name)
            for region in map_record["regions"]:
                self.assertNotIn(region["region_id"], map_region_ids)
                map_region_ids.add(region["region_id"])
                self.assertEqual(region["geometry_type"], "circle")
                self.assertTrue(0.0 <= region["center_x_pct"] <= 1.0)
                self.assertTrue(0.0 <= region["center_y_pct"] <= 1.0)
                self.assertTrue(0.0 < region["radius_pct"] < 0.2)
        self.assertGreaterEqual(static_region_count, 150)

    def test_erangel_alias_resolves_against_same_official_map(self) -> None:
        x_cm = 0.70 * 816000.0
        y_cm = 0.16 * 816000.0

        legacy = resolve_map_region("Erangel_Main", x_cm, y_cm)
        remaster = resolve_map_region("Baltic_Main", x_cm, y_cm)

        self.assertEqual(legacy.status, "matched")
        self.assertEqual(remaster.status, "matched")
        self.assertEqual(legacy.region_id, "erangel.stalber")
        self.assertEqual(remaster.region_id, legacy.region_id)
        self.assertEqual(remaster.canonical_map_name, "Erangel_Main")
        self.assertEqual(remaster.x_cm, x_cm)
        self.assertEqual(remaster.y_cm, y_cm)
        self.assertEqual(remaster.region_display_name_ko, "스탈베르")
        self.assertEqual(remaster.source_asset, "Erangel_Main_Low_Res.png")
        self.assertEqual(
            remaster.source_sha256,
            "56bd4bf0bdcd5e902ef4c232bc54d9546ae0a6cc8b881833f45f0f0a9e93aea9",
        )

    def test_real_drop_coordinates_resolve_to_expected_landmarks(self) -> None:
        cases = (
            ("Baltic_Main", 0.72636, 0.58103, "erangel.mylta"),
            ("Desert_Main", 0.21757, 0.35849, "miramar.el_pozo"),
            ("Savage_Main", 0.19592, 0.40706, "sanhok.camp_alpha"),
            ("Tiger_Main", 0.56289, 0.29987, "taego.yong_cheon"),
            ("Tiger_Main", 0.71234, 0.72667, "taego.oh_hyang"),
            ("Range_Main", 0.30749, 0.49318, "camp_jackal.race_track"),
            ("Neon_Main", 0.34330, 0.29838, "rondo.stadium"),
        )
        world_sizes = {
            "Baltic_Main": 816000.0,
            "Desert_Main": 816000.0,
            "Savage_Main": 408000.0,
            "Tiger_Main": 816000.0,
            "Range_Main": 204000.0,
            "Neon_Main": 816000.0,
        }

        for map_name, x_pct, y_pct, region_id in cases:
            with self.subTest(map_name=map_name, region_id=region_id):
                world_size = world_sizes[map_name]
                resolution = resolve_map_region(
                    map_name,
                    x_pct * world_size,
                    y_pct * world_size,
                )
                self.assertEqual(resolution.status, "matched")
                self.assertEqual(resolution.region_id, region_id)
                self.assertIsNotNone(resolution.distance_to_center_m)
                self.assertIsNotNone(resolution.radius_m)

    def test_taego_market_and_terminal_are_distinct_reviewed_areas(self) -> None:
        world_size = 816000.0

        market = resolve_map_region("Tiger_Main", 0.535 * world_size, 0.445 * world_size)
        terminal = resolve_map_region("Tiger_Main", 0.600 * world_size, 0.445 * world_size)

        self.assertEqual(market.region_id, "taego.market")
        self.assertEqual(market.region_display_name_ko, "시장")
        self.assertEqual(terminal.region_id, "taego.terminal")
        self.assertEqual(terminal.region_display_name_ko, "터미널")

        catalog = map_region_catalog_record("Tiger_Main")
        regions = {
            region["region_id"]: region
            for region in catalog["maps"][0]["regions"]
        }
        self.assertEqual(regions["taego.market"]["review"]["confidence"], "high")
        self.assertEqual(
            regions["taego.market"]["review"]["parent_region_id"],
            "taego.terminal_district",
        )
        self.assertGreaterEqual(len(regions["taego.market"]["review"]["sources"]), 3)

    def test_unmatched_coordinate_is_not_forced_to_nearest_landmark(self) -> None:
        resolution = resolve_map_region("Baltic_Main", 0.50 * 816000.0, 0.02 * 816000.0)

        self.assertEqual(resolution.status, "unmatched")
        self.assertIsNone(resolution.region_id)
        self.assertIsNone(resolution.region_display_name_ko)
        self.assertAlmostEqual(resolution.x_pct or 0.0, 0.50)
        self.assertAlmostEqual(resolution.y_pct or 0.0, 0.02)

    def test_paramo_is_explicitly_dynamic(self) -> None:
        resolution = resolve_map_region("Chimera_Main", 153000.0, 153000.0)

        self.assertEqual(resolution.status, "dynamic_map")
        self.assertEqual(resolution.canonical_map_name, "Paramo_Main")
        self.assertIsNone(resolution.region_id)
        self.assertEqual(resolution.source_asset, "Paramo_Main_Low_Res.png")

    def test_unknown_map_and_out_of_bounds_coordinates_are_distinct(self) -> None:
        unsupported = resolve_map_region("Future_Main", 100.0, 100.0)
        out_of_bounds = resolve_map_region("Tiger_Main", -1.0, 100.0)
        non_finite = resolve_map_region("Tiger_Main", float("nan"), 100.0)

        self.assertEqual(unsupported.status, "unsupported_map")
        self.assertEqual(out_of_bounds.status, "invalid_coordinate")
        self.assertEqual(non_finite.status, "invalid_coordinate")
        self.assertIsNone(unsupported.canonical_map_name)
        self.assertEqual(out_of_bounds.canonical_map_name, "Taego_Main")
        self.assertIsNone(non_finite.x_cm)

    def test_catalog_can_be_filtered_by_api_map_name(self) -> None:
        catalog = map_region_catalog_record("Baltic_Main")

        self.assertEqual(len(catalog["maps"]), 1)
        map_record = catalog["maps"][0]
        self.assertEqual(map_record["map_name"], "Baltic_Main")
        self.assertEqual(map_record["canonical_map_name"], "Erangel_Main")
        self.assertEqual(map_record["policy"], "static")
        self.assertGreater(map_record["region_count"], 20)


if __name__ == "__main__":
    unittest.main()
