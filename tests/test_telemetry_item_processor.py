from __future__ import annotations

import unittest

from pubg_ai.code_translator import CodeTranslator
from pubg_ai.telemetry_item_processor import (
    PARSER_VERSION,
    parse_item_events,
    summarize_item_match_stats,
)


class TelemetryItemProcessorTests(unittest.TestCase):
    def test_stack_count_semantics_change_uses_v5_processing_state(self) -> None:
        self.assertEqual(PARSER_VERSION, "items-v5")

    def test_parses_tracked_item_events_and_summarizes_quantities(self) -> None:
        translator = CodeTranslator(
            {
                "item": {
                    "Item_Ammo_556mm_C": "5.56mm",
                    "Item_Heal_FirstAid_C": "구급상자",
                    "Item_Weapon_HK416_C": "M416",
                    "Item_Attach_Weapon_Upper_DotSight_01_C": "레드도트",
                }
            }
        )
        events = [
            {
                "_T": "LogItemPickupFromVehicleTrunk",
                "character": {"accountId": "account.tracked"},
                "item": {"itemId": "Item_Ammo_556mm_C", "stackCount": 20},
            },
            {
                "_T": "LogItemPutToVehicleTrunk",
                "character": {"accountId": "account.tracked"},
                "item": {"itemId": "Item_Ammo_556mm_C", "stackCount": 10},
            },
            {
                "_T": "LogItemPickup",
                "_D": "2026-06-28T00:00:00Z",
                "character": {
                    "accountId": "account.tracked",
                    "location": {"x": 1, "y": 2, "z": 3},
                },
                "item": {
                    "itemId": "Item_Ammo_556mm_C",
                    "stackCount": 30,
                    "category": "Ammunition",
                    "subCategory": "None",
                },
                "common": {"isGame": 1},
            },
            {
                "_T": "LogItemDrop",
                "character": {"accountId": "account.tracked"},
                "item": {"itemId": "Item_Ammo_556mm_C", "stackCount": 5},
                "common": {"isGame": 1},
            },
            {
                "_T": "LogItemUse",
                "character": {"accountId": "account.tracked"},
                "item": {"itemId": "Item_Heal_FirstAid_C", "stackCount": 5},
                "common": {"isGame": 1},
            },
            {
                "_T": "LogItemAttach",
                "character": {"accountId": "account.tracked"},
                "parentItem": {"itemId": "Item_Weapon_HK416_C", "category": "Weapon"},
                "childItem": {
                    "itemId": "Item_Attach_Weapon_Upper_DotSight_01_C",
                    "stackCount": 1,
                    "category": "Attachment",
                    "subCategory": "Sight",
                },
                "common": {"isGame": 1},
            },
            {
                "_T": "LogItemPickup",
                "character": {"accountId": "account.other"},
                "item": {"itemId": "Item_Ammo_556mm_C", "stackCount": 100},
            },
            {
                "_T": "LogItemPickupFromLootbox",
                "character": {"accountId": "account.tracked"},
                "item": {"itemId": "Item_Heal_Bandage_C", "stackCount": 5},
            },
        ]

        item_events = parse_item_events(
            events,
            match_id="match-1",
            tracked_account_ids={"account.tracked"},
            translator=translator,
        )
        stats = summarize_item_match_stats(item_events)
        by_code = {item.item_code: item for item in stats}

        self.assertEqual(len(item_events), 7)
        self.assertEqual(item_events[2].event_at_kst.hour, 9)
        self.assertEqual(item_events[2].item_name_ko, "5.56mm")
        self.assertEqual(item_events[2].location_x, 1.0)
        self.assertEqual(item_events[5].action, "attach")
        self.assertEqual(item_events[5].parent_item_code, "Item_Weapon_HK416_C")
        self.assertEqual(item_events[5].child_item_name_ko, "레드도트")

        self.assertEqual(by_code["Item_Ammo_556mm_C"].picked_up_events, 2)
        self.assertEqual(by_code["Item_Ammo_556mm_C"].picked_up_quantity, 50)
        self.assertEqual(by_code["Item_Ammo_556mm_C"].vehicle_trunk_pickup_events, 1)
        self.assertEqual(by_code["Item_Ammo_556mm_C"].vehicle_trunk_put_events, 1)
        self.assertEqual(by_code["Item_Ammo_556mm_C"].dropped_events, 1)
        self.assertEqual(by_code["Item_Ammo_556mm_C"].dropped_quantity, 5)
        self.assertEqual(by_code["Item_Heal_FirstAid_C"].used_events, 1)
        self.assertEqual(by_code["Item_Heal_FirstAid_C"].used_quantity, 1)
        self.assertEqual(by_code["Item_Heal_Bandage_C"].loot_box_pickup_events, 1)
        self.assertEqual(by_code["Item_Attach_Weapon_Upper_DotSight_01_C"].attached_events, 1)


if __name__ == "__main__":
    unittest.main()
