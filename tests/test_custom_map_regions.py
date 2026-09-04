from __future__ import annotations

from pathlib import Path

import pytest

from pubg_ai.custom_map_regions import (
    CustomMapRegionError,
    CustomMapRegionStore,
    build_custom_map_region,
    select_custom_map_region,
)
from pubg_ai.map_regions import map_region_catalog_record, resolve_map_region
from pubg_ai.map_snapshot_renderer import MAP_WORLD_SIZE_CM


def point_region(**overrides: object):
    values: dict[str, object] = {
        "region_id": "local.point",
        "map_name": "Savage_Main",
        "name_ko": "부트캠프 좌창",
        "geometry_type": "point_radius",
        "center_x_pct": 0.5,
        "center_y_pct": 0.5,
        "radius_pct": 0.02,
        "priority": 100,
        "enabled": True,
    }
    values.update(overrides)
    return build_custom_map_region(**values)


def polygon_region(**overrides: object):
    values: dict[str, object] = {
        "region_id": "local.polygon",
        "map_name": "Savage_Main",
        "name_ko": "부트캠프 12시",
        "geometry_type": "polygon",
        "points_pct": [
            {"x_pct": 0.45, "y_pct": 0.45},
            {"x_pct": 0.55, "y_pct": 0.45},
            {"x_pct": 0.55, "y_pct": 0.55},
            {"x_pct": 0.45, "y_pct": 0.55},
        ],
        "priority": 100,
        "enabled": True,
    }
    values.update(overrides)
    return build_custom_map_region(**values)


def test_point_and_polygon_geometry_include_boundaries() -> None:
    point = point_region()
    polygon = polygon_region()

    assert point.contains(0.5, 0.5)
    assert point.contains(0.52, 0.5)
    assert not point.contains(0.53, 0.5)
    assert polygon.contains(0.5, 0.5)
    assert polygon.contains(0.45, 0.50)
    assert not polygon.contains(0.60, 0.50)


def test_priority_then_smaller_area_selects_overlapping_region() -> None:
    broad = point_region(region_id="local.broad", radius_pct=0.10)
    small = point_region(region_id="local.small", radius_pct=0.01)
    higher_priority = point_region(region_id="local.priority", radius_pct=0.20, priority=200)

    selected = select_custom_map_region(
        (broad, small), map_name="Savage_Main", x_pct=0.5, y_pct=0.5
    )
    assert selected is not None
    assert selected.region_id == "local.small"

    selected = select_custom_map_region(
        (broad, small, higher_priority), map_name="Savage_Main", x_pct=0.5, y_pct=0.5
    )
    assert selected is not None
    assert selected.region_id == "local.priority"


def test_disabled_and_other_map_regions_do_not_match() -> None:
    disabled = point_region(enabled=False)
    assert select_custom_map_region(
        (disabled,), map_name="Savage_Main", x_pct=0.5, y_pct=0.5
    ) is None
    assert select_custom_map_region(
        (point_region(),), map_name="Baltic_Main", x_pct=0.5, y_pct=0.5
    ) is None


def test_rejects_crossing_polygon() -> None:
    with pytest.raises(CustomMapRegionError, match="교차"):
        polygon_region(
            points_pct=[
                (0.40, 0.40),
                (0.60, 0.60),
                (0.60, 0.40),
                (0.40, 0.60),
            ]
        )


def test_store_persists_updates_and_deletes(tmp_path: Path) -> None:
    path = tmp_path / "config" / "map_regions.local.json"
    store = CustomMapRegionStore(path, base_dir=tmp_path)
    created = store.create_region(point_region().to_record())
    assert path.exists()

    reloaded = CustomMapRegionStore(path, base_dir=tmp_path)
    assert reloaded.get_region(created.region_id) == created

    updated = reloaded.update_region(
        created.region_id,
        {"name_ko": "부트캠프 우창", "priority": 250, "enabled": False},
    )
    assert updated.name_ko == "부트캠프 우창"
    assert updated.priority == 250
    assert not updated.enabled
    assert reloaded.list_regions(include_disabled=False) == ()

    deleted = reloaded.delete_region(created.region_id)
    assert deleted.region_id == created.region_id
    assert reloaded.list_regions() == ()


def test_custom_region_overrides_official_resolution_and_catalog() -> None:
    custom = point_region(center_x_pct=0.50, center_y_pct=0.50, radius_pct=0.10)
    world_size = MAP_WORLD_SIZE_CM["Savage_Main"]
    resolved = resolve_map_region(
        "Savage_Main",
        0.50 * world_size,
        0.50 * world_size,
        custom_regions=(custom,),
    )
    assert resolved.status == "matched"
    assert resolved.region_id == custom.region_id
    assert resolved.region_display_name_ko == "부트캠프 좌창"
    assert resolved.region_source == "custom"
    assert resolved.region_priority == 100

    map_record = map_region_catalog_record(
        "Savage_Main", custom_regions=(custom,)
    )["maps"][0]
    assert map_record["custom_region_count"] == 1
    assert map_record["regions"][0]["source"] == "custom"
