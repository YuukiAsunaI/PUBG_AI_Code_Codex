from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

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
        self.assertIn('id="mapRegionEditorStage"', response.text)
        self.assertIn('data-map-region-mode="point_radius"', response.text)
        self.assertIn('data-map-region-mode="rectangle"', response.text)
        self.assertIn('data-map-region-mode="polygon"', response.text)
        self.assertIn("/map-regions/custom", response.text)
        self.assertIn("/map-regions/resolve?", response.text)
        self.assertIn("region_display_name_ko", response.text)
        self.assertIn("사용자 지역 저장 완료", response.text)
        self.assertIn('id="timelineShowRegions"', response.text)
        self.assertIn('id="timelineLocationStatus"', response.text)

    def test_custom_region_crud_and_resolution_override(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            client = TestClient(create_app(base_dir=base_dir))
            record = {
                "map_name": "Savage_Main",
                "name_ko": "부트캠프 좌창",
                "geometry_type": "point_radius",
                "center_x_pct": 0.5,
                "center_y_pct": 0.5,
                "radius_pct": 0.02,
                "points_pct": [],
                "priority": 320,
                "enabled": True,
                "note": "테스트 사용자 지역",
            }

            created = client.post("/map-regions/custom", json=record)
            self.assertEqual(created.status_code, 200)
            region_id = created.json()["map_region"]["region_id"]
            self.assertTrue(region_id.startswith("local."))
            self.assertTrue((base_dir / "config" / "map_regions.local.json").exists())

            listed = client.get("/map-regions/custom?map_name=Savage_Main")
            self.assertEqual(listed.status_code, 200)
            self.assertEqual(len(listed.json()["regions"]), 1)

            resolved = client.get(
                "/map-regions/resolve?map_name=Savage_Main&x_cm=204000&y_cm=204000"
            )
            self.assertEqual(resolved.status_code, 200)
            self.assertEqual(resolved.json()["map_region"]["region_id"], region_id)
            self.assertEqual(resolved.json()["map_region"]["region_source"], "custom")

            catalog = client.get("/map-regions?map_name=Savage_Main").json()[
                "map_region_catalog"
            ]["maps"][0]
            self.assertEqual(catalog["regions"][0]["region_id"], region_id)
            self.assertEqual(catalog["regions"][0]["source"], "custom")

            record["name_ko"] = "부트캠프 우창"
            record["enabled"] = False
            updated = client.put(f"/map-regions/custom/{region_id}", json=record)
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["map_region"]["name_ko"], "부트캠프 우창")
            self.assertFalse(updated.json()["map_region"]["enabled"])

            resolved_disabled = client.get(
                "/map-regions/resolve?map_name=Savage_Main&x_cm=204000&y_cm=204000"
            )
            self.assertNotEqual(
                resolved_disabled.json()["map_region"]["region_id"],
                region_id,
            )

            deleted = client.delete(f"/map-regions/custom/{region_id}")
            self.assertEqual(deleted.status_code, 200)
            self.assertTrue(deleted.json()["deleted"])
            self.assertEqual(
                client.get("/map-regions/custom?map_name=Savage_Main").json()["regions"],
                [],
            )

    def test_custom_region_api_rejects_crossing_polygon(self) -> None:
        with TemporaryDirectory() as temp_dir:
            client = TestClient(create_app(base_dir=Path(temp_dir)))
            response = client.post(
                "/map-regions/custom",
                json={
                    "map_name": "Savage_Main",
                    "name_ko": "교차 영역",
                    "geometry_type": "polygon",
                    "points_pct": [
                        {"x_pct": 0.1, "y_pct": 0.1},
                        {"x_pct": 0.9, "y_pct": 0.9},
                        {"x_pct": 0.1, "y_pct": 0.9},
                        {"x_pct": 0.9, "y_pct": 0.1},
                    ],
                    "priority": 100,
                    "enabled": True,
                },
            )

            self.assertEqual(response.status_code, 400)
            self.assertIn("교차", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
