from __future__ import annotations

import unittest

from pubg_ai.code_translator import CodeTranslator
from pubg_ai.telemetry_activity_processor import (
    PARSER_VERSION,
    parse_activity_events,
    summarize_activity_match,
    summarize_telemetry_event_counts,
)


class TelemetryActivityProcessorTests(unittest.TestCase):
    def test_normalizes_roles_and_summarizes_player_activity(self) -> None:
        tracked = {"account.a", "account.b"}
        a = {"accountId": "account.a", "location": {"x": 1, "y": 2, "z": 3}}
        b = {"accountId": "account.b", "location": {"x": 4, "y": 5, "z": 6}}
        events = [
            {
                "_T": "LogHeal",
                "_D": "2026-08-22T00:00:00Z",
                "character": a,
                "item": {"itemId": "Item_Heal_FirstAid_C"},
                "healAmount": 75,
                "common": {"isGame": 1},
            },
            {
                "_T": "LogPlayerRevive",
                "reviver": a,
                "victim": b,
                "useTraumaBag": True,
            },
            {
                "_T": "LogPlayerUseThrowable",
                "attacker": a,
                "weapon": {"itemId": "Item_Weapon_Grenade_C"},
            },
            {
                "_T": "LogVehicleLeave",
                "character": a,
                "vehicle": {
                    "vehicleType": "Dacia",
                    "vehicleId": "BP_Vehicle_Dacia_C",
                    "vehicleUniqueId": 42,
                },
                "rideDistance": 1234.5,
                "maxSpeed": 88.2,
            },
            {
                "_T": "LogArmorDestroy",
                "attacker": a,
                "victim": b,
                "item": {"itemId": "Item_Armor_C_01_Lv2_C"},
                "damage": 25,
            },
            {
                "_T": "LogVaultStart",
                "character": a,
                "isLedgeGrab": True,
                "isVaultOnVehicle": True,
            },
            {
                "_T": "LogSwimEnd",
                "character": a,
                "swimDistance": 27.5,
                "maxSwimDepth": 4.2,
            },
            {
                "_T": "LogEmergencyPickup",
                "instigator": a,
                "riders": [a, b],
            },
            {"_T": "LogUnknownFutureEvent", "character": a},
        ]
        translator = CodeTranslator(
            {
                "item": {
                    "Item_Heal_FirstAid_C": "구급상자",
                    "Item_Weapon_Grenade_C": "수류탄",
                    "Item_Armor_C_01_Lv2_C": "2레벨 조끼",
                }
            }
        )

        records = parse_activity_events(
            events,
            match_id="match-1",
            tracked_account_ids=tracked,
            translator=translator,
        )
        summaries = summarize_activity_match(
            records,
            match_id="match-1",
            account_ids=tracked,
        )
        by_account = {row.account_id: row for row in summaries}

        self.assertEqual(PARSER_VERSION, "activity-v2")
        self.assertEqual(records[0].event_at_kst.hour, 9)
        self.assertEqual(records[0].item_name_ko, "구급상자")
        self.assertEqual(by_account["account.a"].heal_amount, 75.0)
        self.assertEqual(by_account["account.a"].item_heal_amount, 75.0)
        self.assertEqual(by_account["account.a"].passive_heal_amount, 0.0)
        self.assertEqual(by_account["account.a"].revives_caused, 1)
        self.assertEqual(by_account["account.b"].revives_received, 1)
        self.assertEqual(by_account["account.a"].trauma_bag_revives, 1)
        self.assertEqual(by_account["account.a"].throwable_uses, 1)
        self.assertEqual(by_account["account.a"].vehicle_distance_m, 1234.5)
        self.assertEqual(by_account["account.a"].armor_destroys_caused, 1)
        self.assertEqual(by_account["account.b"].armor_destroys_taken, 1)
        self.assertEqual(by_account["account.a"].ledge_grabs, 1)
        self.assertEqual(by_account["account.a"].swim_distance_m, 27.5)
        self.assertEqual(by_account["account.a"].emergency_pickup_calls, 1)
        self.assertEqual(by_account["account.b"].emergency_pickup_rides, 1)

        counts = summarize_telemetry_event_counts(events, records, match_id="match-1")
        by_type = {row.event_type: row for row in counts}
        self.assertEqual(by_type["LogHeal"].tracked_event_count, 1)
        self.assertEqual(by_type["LogPlayerRevive"].normalized_event_count, 2)
        self.assertEqual(by_type["LogUnknownFutureEvent"].normalized_event_count, 0)

    def test_accepts_generator_input_without_losing_second_pass_events(self) -> None:
        events = (
            event
            for event in [
                {
                    "_T": "LogObjectDestroy",
                    "character": {"accountId": "account.a"},
                    "objectType": "Door",
                }
            ]
        )
        records = parse_activity_events(
            events,
            match_id="match-1",
            tracked_account_ids={"account.a"},
        )
        self.assertEqual([row.action for row in records], ["object_destroy"])


if __name__ == "__main__":
    unittest.main()
