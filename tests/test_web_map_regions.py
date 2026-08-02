from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from pubg_ai.map_regions import MAP_REGION_CATALOG_VERSION, MAP_REGION_SOURCE_COMMIT
from pubg_ai.web.app import create_app


class WebMapRegionTests(unittest.TestCase):
    def test_catalog_endpoint_returns_pinned_map_geometry(self) -> None:
        response = TestClient(create_app()).get("/map-regions?map_name=Baltic_Main")

        self.assertEqual(response.status_code, 200)
        catalog = response.json()["map_region_catalog"]
        self.assertEqual(catalog["catalog_version"], MAP_REGION_CATALOG_VERSION)
        self.assertEqual(catalog["source_commit"], MAP_REGION_SOURCE_COMMIT)
        self.assertEqual(len(catalog["maps"]), 1)
        self.assertEqual(catalog["maps"][0]["canonical_map_name"], "Erangel_Main")
        self.assertGreater(catalog["maps"][0]["region_count"], 20)

    def test_resolution_endpoint_preserves_raw_coordinate_and_returns_name(self) -> None:
        response = TestClient(create_app()).get(
            "/map-regions/resolve?map_name=Baltic_Main&x_cm=575857&y_cm=134391"
        )

        self.assertEqual(response.status_code, 200)
        region = response.json()["map_region"]
        self.assertEqual(region["status"], "matched")
        self.assertEqual(region["region_id"], "erangel.stalber")
        self.assertEqual(region["region_display_name_ko"], "스탈베르")
        self.assertEqual(region["x_cm"], 575857.0)
        self.assertEqual(region["y_cm"], 134391.0)

    def test_resolution_endpoint_serializes_non_finite_coordinate_safely(self) -> None:
        response = TestClient(create_app()).get(
            "/map-regions/resolve?map_name=Tiger_Main&x_cm=nan&y_cm=100"
        )

        self.assertEqual(response.status_code, 200)
        region = response.json()["map_region"]
        self.assertEqual(region["status"], "invalid_coordinate")
        self.assertIsNone(region["x_cm"])
        self.assertEqual(region["y_cm"], 100.0)

    def test_local_manager_contains_region_resolver_and_named_drop_renderer(self) -> None:
        response = TestClient(create_app()).get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="map-region-lookup"', response.text)
        self.assertIn('id="mapRegionForm"', response.text)
        self.assertIn('name="x_cm"', response.text)
        self.assertIn('name="y_cm"', response.text)
        self.assertIn("/map-regions/resolve?", response.text)
        self.assertIn("region_display_name_ko", response.text)
        self.assertIn("맵 지역 확인 완료", response.text)


if __name__ == "__main__":
    unittest.main()
