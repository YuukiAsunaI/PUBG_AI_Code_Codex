from __future__ import annotations

import unittest
from datetime import datetime

from pubg_ai.loadout_snapshot_processor import (
    CombatLoadoutEvent,
    ItemLoadoutEvent,
    build_loadout_snapshots,
)


class LoadoutSnapshotProcessorTests(unittest.TestCase):
    def test_builds_time_ordered_weapon_attachment_snapshots(self) -> None:
        item_events = [
            ItemLoadoutEvent(
                event_index=10,
                action="attach",
                item_code="Item_Attach_Weapon_Lower_Foregrip_C",
                item_name_ko="Vertical Grip",
                item_category="Attachment",
                item_sub_category="Lower",
                parent_item_code="Item_Weapon_HK416_C",
            ),
            ItemLoadoutEvent(
                event_index=12,
                action="attach",
                item_code="Item_Attach_Weapon_Upper_DotSight_01_C",
                item_name_ko="Red Dot Sight",
                item_category="Attachment",
                item_sub_category="Sight",
                parent_item_code="Item_Weapon_HK416_C",
            ),
            ItemLoadoutEvent(
                event_index=20,
                action="detach",
                item_code="Item_Attach_Weapon_Lower_Foregrip_C",
                item_name_ko="Vertical Grip",
                item_category="Attachment",
                item_sub_category="Lower",
                parent_item_code="Item_Weapon_HK416_C",
            ),
            ItemLoadoutEvent(
                event_index=21,
                action="attach",
                item_code="Item_Attach_Weapon_Magazine_ExtendedQuickDraw_Large_C",
                item_name_ko="AR Extended QuickDraw Mag",
                item_category="Attachment",
                item_sub_category="Magazine",
                parent_item_code="Item_Weapon_BerylM762_C",
            ),
        ]
        combat_events = [
            CombatLoadoutEvent(
                event_index=15,
                action="kill",
                event_at_kst=datetime(2026, 1, 1, 12, 0, 0),
                damage_causer_name="WeapHK416_C",
                distance_m=18.0,
                is_headshot=True,
            ),
            CombatLoadoutEvent(
                event_index=25,
                action="dbno_caused",
                event_at_kst=datetime(2026, 1, 1, 12, 1, 0),
                damage_causer_name="Item_Weapon_HK416_C",
                distance_m=36.0,
                is_headshot=False,
            ),
            CombatLoadoutEvent(
                event_index=30,
                action="finish",
                event_at_kst=datetime(2026, 1, 1, 12, 2, 0),
                damage_causer_name="WeapBerylM762_C",
                distance_m=40.0,
                is_headshot=False,
            ),
        ]

        snapshots = build_loadout_snapshots(
            match_id="match-1",
            account_id="account.test",
            item_events=item_events,
            combat_events=combat_events,
        )

        self.assertEqual(len(snapshots), 3)
        self.assertEqual(snapshots[0].weapon_code, "WeapHK416_C")
        self.assertEqual(
            set(snapshots[0].attachment_codes),
            {
                "Item_Attach_Weapon_Lower_Foregrip_C",
                "Item_Attach_Weapon_Upper_DotSight_01_C",
            },
        )
        self.assertTrue(snapshots[0].is_headshot)
        self.assertEqual(
            snapshots[1].attachment_codes,
            ("Item_Attach_Weapon_Upper_DotSight_01_C",),
        )
        self.assertEqual(snapshots[2].weapon_code, "WeapBerylM762_C")
        self.assertEqual(
            snapshots[2].attachment_codes,
            ("Item_Attach_Weapon_Magazine_ExtendedQuickDraw_Large_C",),
        )


if __name__ == "__main__":
    unittest.main()
