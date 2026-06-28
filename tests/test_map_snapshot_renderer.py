from __future__ import annotations

from datetime import datetime
from io import BytesIO
import unittest

from PIL import Image

from pubg_ai.map_snapshot_renderer import (
    CarePackageLocation,
    CombatLocation,
    MapAssetProvider,
    MapSnapshotContext,
    PlaneRoute,
    PositionSample,
    render_player_map_snapshot,
)


class NoAssetProvider(MapAssetProvider):
    def __init__(self) -> None:
        pass

    def load_map(self, map_name: str | None) -> Image.Image | None:
        return None


class MapSnapshotRendererTests(unittest.TestCase):
    def test_renders_jpeg_snapshot_with_fallback_grid(self) -> None:
        context = MapSnapshotContext(
            match_id="match-1",
            shard="steam",
            map_name="Tiger_Main",
            game_mode="squad",
            match_type="official",
            created_at_kst=datetime(2026, 6, 28, 9, 13, 17),
            account_id="account.tracked",
            player_name="Yuuki_Asuna---",
            positions=[
                PositionSample(event_index=10, x=100000, y=200000, common_is_game=0.1),
                PositionSample(event_index=20, x=120000, y=220000, common_is_game=0.1),
                PositionSample(event_index=30, x=180000, y=260000, common_is_game=1.0),
            ],
            landing_points=[
                PositionSample(event_index=25, x=140000, y=240000, common_is_game=0.1),
            ],
            combat_locations=[
                CombatLocation(
                    action="dbno_caused",
                    x=170000,
                    y=250000,
                    related_x=180000,
                    related_y=260000,
                    damage_causer_name="WeapAUG_C",
                    damage_reason="HeadShot",
                    distance_m=100,
                    is_headshot=True,
                ),
                CombatLocation(action="kill", x=180000, y=260000, related_x=190000, related_y=270000),
                CombatLocation(action="death", x=200000, y=280000),
            ],
            care_packages=[
                CarePackageLocation(event_type="LogCarePackageLand", x=300000, y=300000),
            ],
            plane_route=PlaneRoute(start_x=50000, start_y=50000, end_x=300000, end_y=300000),
        )

        body = render_player_map_snapshot(context, NoAssetProvider())
        image = Image.open(BytesIO(body))

        self.assertEqual(image.format, "JPEG")
        self.assertEqual(image.size, (1280, 1418))
        self.assertGreater(len(body), 20_000)


if __name__ == "__main__":
    unittest.main()
