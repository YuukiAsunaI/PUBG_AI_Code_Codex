from __future__ import annotations

import unittest

from pubg_ai.telemetry_movement_processor import (
    parse_care_package_events,
    parse_combat_location_events,
    parse_landing_events,
    parse_phase_events,
    parse_plane_route,
    parse_position_samples,
    summarize_movement,
)


class TelemetryMovementProcessorTests(unittest.TestCase):
    def test_classifies_throwables_and_skips_empty_weapon_attacks(self) -> None:
        attacker = {
            "accountId": "account.tracked",
            "location": {"x": 10000, "y": 20000, "z": 0},
        }
        events = [
            {
                "_T": "LogPlayerAttack",
                "_D": "2026-08-21T00:00:10Z",
                "attackType": "Weapon",
                "attacker": attacker,
                "weapon": {
                    "itemId": "Item_Weapon_SmokeBomb_C",
                    "category": "Equipment",
                    "subCategory": "Throwable",
                },
                "common": {"isGame": 1},
            },
            {
                "_T": "LogPlayerAttack",
                "_D": "2026-08-21T00:00:11Z",
                "attackType": "Weapon",
                "attacker": attacker,
                "weapon": {
                    "itemId": "Item_Weapon_Pan_C",
                    "category": "Weapon",
                    "subCategory": "Melee",
                },
                "common": {"isGame": 1},
            },
            {
                "_T": "LogPlayerAttack",
                "_D": "2026-08-21T00:00:12Z",
                "attackType": "Weapon",
                "attacker": attacker,
                "weapon": {"itemId": "", "category": "", "subCategory": ""},
                "common": {"isGame": 1},
            },
        ]

        combat = parse_combat_location_events(
            events,
            match_id="match-attacks",
            tracked_account_ids={"account.tracked"},
        )

        self.assertEqual([event.action for event in combat], ["throw", "melee"])
        self.assertEqual(combat[0].damage_causer_name, "Item_Weapon_SmokeBomb_C")
        self.assertEqual(combat[0].damage_type_category, "Damage_Throwable")
        self.assertEqual(combat[1].damage_type_category, "Damage_Melee")

    def test_parses_team_vehicle_shots_and_verified_hit_directions(self) -> None:
        events = [
            {
                "_T": "LogPlayerPosition",
                "_D": "2026-08-21T00:00:10Z",
                "character": {
                    "accountId": "account.teammate",
                    "location": {"x": 10000, "y": 20000, "z": 0},
                    "isInVehicle": True,
                    "isDBNO": False,
                },
                "vehicle": {
                    "vehicleType": "WheeledVehicle",
                    "vehicleId": "Dacia_A_01_v2_C",
                    "vehicleUniqueId": 4321,
                },
                "elapsedTime": 70,
                "numAlivePlayers": 80,
                "common": {"isGame": 1},
            },
            {
                "_T": "LogPlayerAttack",
                "_D": "2026-08-21T00:00:11Z",
                "attackId": 991,
                "attackType": "Weapon",
                "fireWeaponStackCount": 4,
                "attacker": {
                    "accountId": "account.teammate",
                    "location": {"x": 10100, "y": 20100, "z": 0},
                },
                "weapon": {"itemId": "Item_Weapon_HK416_C"},
                "common": {"isGame": 1},
            },
            {
                "_T": "LogPlayerTakeDamage",
                "_D": "2026-08-21T00:00:12Z",
                "attackId": 991,
                "attacker": {
                    "accountId": "account.teammate",
                    "location": {"x": 10100, "y": 20100, "z": 0},
                },
                "victim": {
                    "accountId": "account.enemy",
                    "location": {"x": 13100, "y": 24100, "z": 0},
                },
                "damage": 31.5,
                "damageReason": "TorsoShot",
                "damageTypeCategory": "Damage_Gun",
                "damageCauserName": "WeapHK416_C",
                "common": {"isGame": 1},
            },
            {
                "_T": "LogPlayerTakeDamage",
                "_D": "2026-08-21T00:00:13Z",
                "attackId": 992,
                "attacker": {
                    "accountId": "account.enemy",
                    "location": {"x": 15100, "y": 25100, "z": 0},
                },
                "victim": {
                    "accountId": "account.teammate",
                    "location": {"x": 11100, "y": 22100, "z": 0},
                },
                "damage": 22.0,
                "damageReason": "ArmShot",
                "damageTypeCategory": "Damage_Gun",
                "damageCauserName": "WeapAK47_C",
                "common": {"isGame": 1},
            },
        ]

        samples = parse_position_samples(
            events,
            match_id="match-team",
            tracked_account_ids={"account.tracked", "account.teammate"},
        )
        combat = parse_combat_location_events(
            events,
            match_id="match-team",
            tracked_account_ids={"account.tracked", "account.teammate"},
        )

        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0].account_id, "account.teammate")
        self.assertEqual(samples[0].vehicle_type, "WheeledVehicle")
        self.assertEqual(samples[0].vehicle_id, "Dacia_A_01_v2_C")
        self.assertEqual(samples[0].vehicle_unique_id, 4321)
        self.assertEqual([event.action for event in combat], ["shot", "hit_caused", "hit_taken"])
        self.assertEqual(combat[0].damage_causer_name, "Item_Weapon_HK416_C")
        self.assertIsNone(combat[0].related_account_id)
        self.assertAlmostEqual(combat[1].distance_m or 0.0, 50.0)
        self.assertEqual(combat[1].related_account_id, "account.enemy")
        self.assertEqual(combat[2].account_id, "account.teammate")
        self.assertEqual(combat[2].related_account_id, "account.enemy")

    def test_parses_tracked_position_landing_and_summary_distance(self) -> None:
        events = [
            {
                "_T": "LogPlayerPosition",
                "_D": "2026-06-28T00:00:00Z",
                "character": {
                    "accountId": "account.tracked",
                    "location": {"x": 0, "y": 0, "z": 100},
                    "isInVehicle": False,
                    "isInBlueZone": False,
                    "isInRedZone": False,
                    "inSpecialZone": "None",
                    "isDBNO": False,
                    "zone": [],
                },
                "elapsedTime": 0,
                "numAlivePlayers": 64,
                "common": {"isGame": 0},
            },
            {
                "_T": "LogPlayerPosition",
                "_D": "2026-06-28T00:00:10Z",
                "character": {
                    "accountId": "account.tracked",
                    "location": {"x": 0, "y": 0, "z": 100},
                    "isInVehicle": False,
                    "isInBlueZone": False,
                    "isInRedZone": False,
                    "inSpecialZone": "None",
                    "isDBNO": False,
                    "zone": ["school"],
                },
                "elapsedTime": 10,
                "numAlivePlayers": 63,
                "common": {"isGame": 1},
            },
            {
                "_T": "LogPlayerPosition",
                "_D": "2026-06-28T00:00:20Z",
                "character": {
                    "accountId": "account.tracked",
                    "location": {"x": 300, "y": 400, "z": 120},
                    "isInVehicle": False,
                    "isInBlueZone": True,
                    "isInRedZone": False,
                    "inSpecialZone": "None",
                    "isDBNO": True,
                    "zone": ["school"],
                },
                "elapsedTime": 20,
                "numAlivePlayers": 62,
                "common": {"isGame": 1},
            },
            {
                "_T": "LogPlayerPosition",
                "character": {
                    "accountId": "account.other",
                    "location": {"x": 9999, "y": 9999, "z": 0},
                },
                "common": {"isGame": 1},
            },
            {
                "_T": "LogParachuteLanding",
                "_D": "2026-06-28T00:00:30Z",
                "character": {
                    "accountId": "account.tracked",
                    "location": {"x": 500, "y": 600, "z": 50},
                },
                "distance": 123.45,
                "common": {"isGame": 0.1},
            },
            {
                "_T": "LogParachuteLanding",
                "_D": "2026-06-28T00:02:30Z",
                "character": {
                    "accountId": "account.tracked",
                    "location": {"x": 999, "y": 999, "z": 50},
                },
                "distance": 999.0,
                "common": {"isGame": 1},
            },
        ]

        samples = parse_position_samples(
            events,
            match_id="match-1",
            tracked_account_ids={"account.tracked"},
        )
        landings = parse_landing_events(
            events,
            match_id="match-1",
            tracked_account_ids={"account.tracked"},
        )
        summaries = summarize_movement(
            samples,
            landing_events=landings,
            match_id="match-1",
            tracked_account_ids={"account.tracked"},
        )

        self.assertEqual(len(samples), 3)
        self.assertEqual(samples[0].event_at_kst.hour, 9)
        self.assertEqual(samples[1].zone, ["school"])
        self.assertTrue(samples[2].is_in_blue_zone)
        self.assertEqual(len(landings), 2)
        self.assertEqual(landings[0].distance_m, 123.45)

        summary = summaries[0]
        self.assertEqual(summary.sample_count, 3)
        self.assertAlmostEqual(summary.total_sampled_distance_m, 5.0)
        self.assertAlmostEqual(summary.in_game_sampled_distance_m, 5.0)
        self.assertEqual(summary.dbno_sample_count, 1)
        self.assertEqual(summary.landing_x, 500.0)

    def test_parses_combat_locations_care_packages_and_plane_route(self) -> None:
        events = [
            {
                "_T": "LogPlayerPosition",
                "_D": "2026-06-28T00:01:00Z",
                "character": {
                    "accountId": "account.tracked",
                    "location": {"x": 1000, "y": 2000, "z": 150000},
                    "isInVehicle": True,
                },
                "common": {"isGame": 0.1},
            },
            {
                "_T": "LogPlayerPosition",
                "_D": "2026-06-28T00:01:10Z",
                "character": {
                    "accountId": "account.tracked",
                    "location": {"x": 9000, "y": 12000, "z": 150000},
                    "isInVehicle": True,
                },
                "common": {"isGame": 0.1},
            },
            {
                "_T": "LogPlayerMakeGroggy",
                "_D": "2026-06-28T00:02:00Z",
                "attacker": {
                    "accountId": "account.tracked",
                    "location": {"x": 100, "y": 200, "z": 300},
                },
                "victim": {
                    "accountId": "account.enemy",
                    "location": {"x": 150, "y": 250, "z": 300},
                },
                "damageReason": "HeadShot",
                "damageTypeCategory": "Damage_Gun",
                "damageCauserName": "WeapAUG_C",
                "distance": 70.7,
                "common": {"isGame": 1},
            },
            {
                "_T": "LogPlayerKillV2",
                "_D": "2026-06-28T00:02:10Z",
                "victim": {
                    "accountId": "account.enemy",
                    "location": {"x": 160, "y": 260, "z": 300},
                },
                "killer": {
                    "accountId": "account.tracked",
                    "location": {"x": 110, "y": 210, "z": 300},
                },
                "finisher": {
                    "accountId": "account.tracked",
                    "location": {"x": 112, "y": 212, "z": 300},
                },
                "killerDamageInfo": {
                    "damageReason": "HeadShot",
                    "damageTypeCategory": "Damage_Gun",
                    "damageCauserName": "WeapAUG_C",
                    "distance": 70.7,
                },
                "finishDamageInfo": {
                    "damageReason": "TorsoShot",
                    "damageTypeCategory": "Damage_Gun",
                    "damageCauserName": "WeapAUG_C",
                    "distance": 50.0,
                },
                "common": {"isGame": 1},
            },
            {
                "_T": "LogPlayerRevive",
                "_D": "2026-06-28T00:02:20Z",
                "reviver": {
                    "accountId": "account.tracked",
                    "location": {"x": 210, "y": 310, "z": 300},
                },
                "victim": {
                    "accountId": "account.friend",
                    "location": {"x": 240, "y": 350, "z": 300},
                },
                "useTraumaBag": True,
                "dBNOId": 123,
                "common": {"isGame": 1},
            },
            {
                "_T": "LogPlayerRevive",
                "_D": "2026-06-28T00:02:30Z",
                "reviver": {
                    "accountId": "account.friend",
                    "location": {"x": 410, "y": 510, "z": 300},
                },
                "victim": {
                    "accountId": "account.tracked",
                    "location": {"x": 440, "y": 550, "z": 300},
                },
                "useTraumaBag": False,
                "dBNOId": 124,
                "common": {"isGame": 1},
            },
            {
                "_T": "LogCarePackageSpawn",
                "_D": "2026-06-28T00:03:00Z",
                "itemPackage": {
                    "itemPackageId": "Carapackage_RedBox_C",
                    "location": {"x": 1000, "y": 2000, "z": 30000},
                    "items": [
                        {"itemId": "Item_Weapon_FAMASG2_C"},
                        {"itemId": "Item_Armor_C_01_Lv3_C"},
                    ],
                },
                "common": {"isGame": 1},
            },
            {
                "_T": "LogGameStatePeriodic",
                "_D": "2026-06-28T00:03:10Z",
                "gameState": {
                    "elapsedTime": 190,
                    "numAlivePlayers": 63,
                    "numAliveTeams": 22,
                    "safetyZonePosition": {"x": 202000.0, "y": 203000.0, "z": 0.0},
                    "safetyZoneRadius": 291000.0,
                    "poisonGasWarningPosition": {"x": 250000.0, "y": 260000.0, "z": 0.0},
                    "poisonGasWarningRadius": 120000.0,
                    "redZonePosition": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "redZoneRadius": 0.0,
                    "blackZonePosition": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "blackZoneRadius": 0.0,
                },
                "common": {"isGame": 1.0},
            },
        ]

        combat_locations = parse_combat_location_events(
            events,
            match_id="match-1",
            tracked_account_ids={"account.tracked"},
        )
        care_packages = parse_care_package_events(events, match_id="match-1")
        phase_events = parse_phase_events(events, match_id="match-1")
        plane_route = parse_plane_route(
            events,
            match_id="match-1",
            preferred_account_ids={"account.tracked"},
        )

        self.assertEqual(
            [event.action for event in combat_locations],
            ["dbno_caused", "kill", "finish", "revive_given", "revive_received"],
        )
        self.assertTrue(combat_locations[0].is_headshot)
        self.assertEqual(combat_locations[0].damage_causer_name, "WeapAUG_C")
        self.assertAlmostEqual(combat_locations[0].distance_m, 0.707)
        self.assertEqual(combat_locations[0].related_x, 150.0)
        self.assertEqual(combat_locations[3].event_type, "LogPlayerRevive")
        self.assertEqual(combat_locations[3].damage_reason, "TraumaBag")
        self.assertAlmostEqual(combat_locations[3].distance_m, 0.5)
        self.assertEqual(combat_locations[4].damage_reason, "Revive")
        self.assertEqual(care_packages[0].item_count, 2)
        self.assertEqual(care_packages[0].item_codes[0], "Item_Weapon_FAMASG2_C")
        self.assertEqual(len(phase_events), 1)
        self.assertEqual(phase_events[0].elapsed_time_seconds, 190.0)
        self.assertEqual(phase_events[0].num_alive_teams, 22)
        self.assertEqual(phase_events[0].safety_zone_x, 202000.0)
        self.assertEqual(phase_events[0].safety_zone_radius, 291000.0)
        self.assertEqual(phase_events[0].poison_gas_warning_radius, 120000.0)
        self.assertIsNotNone(plane_route)
        assert plane_route is not None
        self.assertEqual(plane_route.sample_count, 2)
        self.assertEqual(plane_route.start_x, 1000.0)
        self.assertEqual(plane_route.end_y, 12000.0)

    def test_plane_route_excludes_redeploy_aircraft(self) -> None:
        events = [
            {
                "_T": "LogVehicleRide",
                "character": {
                    "accountId": "account.tracked",
                    "location": {"x": 1000, "y": 9000, "z": 150000},
                },
                "vehicle": {
                    "vehicleType": "TransportAircraft",
                    "location": {"x": 1000, "y": 9000, "z": 150000},
                },
                "common": {"isGame": 0.1},
            },
            {
                "_T": "LogVehicleLeave",
                "character": {
                    "accountId": "account.other",
                    "location": {"x": 8000, "y": 2000, "z": 150000},
                },
                "vehicle": {"vehicleType": "TransportAircraft"},
                "common": {"isGame": 0.1},
            },
            {
                "_T": "LogVehicleRide",
                "character": {
                    "accountId": "account.revived",
                    "location": {"x": 9000, "y": 9000, "z": 100000},
                },
                "vehicle": {
                    "vehicleType": "TransportAircraft",
                    "vehicleId": "RedeployAircraft_Tiger_C",
                },
                "common": {"isGame": 1.5},
            },
        ]

        route = parse_plane_route(
            events,
            match_id="match-no-redeploy",
            preferred_account_ids={"account.tracked"},
        )

        self.assertIsNotNone(route)
        assert route is not None
        self.assertEqual(route.source, "global_transport_aircraft_events")
        self.assertEqual(route.sample_count, 2)
        self.assertEqual(route.start_x, 1000.0)
        self.assertEqual(route.end_x, 8000.0)
        self.assertIsNone(route.sample_account_id)

    def test_plane_route_uses_all_players_after_tracked_player_jumps(self) -> None:
        events = [
            {
                "_T": "LogVehicleRide",
                "character": {
                    "accountId": "account.tracked",
                    "location": {"x": 1000, "y": 9000, "z": 150000},
                },
                "vehicle": {
                    "vehicleType": "TransportAircraft",
                    "location": {"x": 1000, "y": 9000, "z": 150000},
                },
                "common": {"isGame": 0.1},
            },
            {
                "_T": "LogVehicleLeave",
                "character": {
                    "accountId": "account.tracked",
                    "location": {"x": 2000, "y": 8000, "z": 150000},
                },
                "vehicle": {"vehicleType": "TransportAircraft"},
                "common": {"isGame": 0.1},
            },
            {
                "_T": "LogPlayerPosition",
                "character": {
                    "accountId": "account.other",
                    "location": {"x": 8000, "y": 2000, "z": 150000},
                    "isInVehicle": True,
                },
                "common": {"isGame": 0.1},
            },
        ]

        route = parse_plane_route(
            events,
            match_id="match-global-plane",
            preferred_account_ids={"account.tracked"},
        )

        self.assertIsNotNone(route)
        assert route is not None
        self.assertEqual(route.source, "global_transport_aircraft_events")
        self.assertEqual(route.sample_count, 3)
        self.assertEqual(route.start_x, 1000.0)
        self.assertEqual(route.end_x, 8000.0)
        self.assertIsNone(route.sample_account_id)

    def test_sampled_distance_excludes_transport_plane_and_discontinuous_paths(self) -> None:
        events = [
            {
                "_T": "LogPlayerPosition",
                "_D": "2026-08-21T00:00:00Z",
                "character": {
                    "accountId": "account.tracked",
                    "location": {"x": 1000, "y": 1000, "z": 150000},
                    "isInVehicle": True,
                },
                "elapsedTime": 10,
                "common": {"isGame": 0.1},
            },
            {
                "_T": "LogPlayerPosition",
                "_D": "2026-08-21T00:00:10Z",
                "character": {
                    "accountId": "account.tracked",
                    "location": {"x": 300000, "y": 300000, "z": 90000},
                    "isInVehicle": False,
                },
                "elapsedTime": 20,
                "common": {"isGame": 0.2},
            },
            {
                "_T": "LogPlayerPosition",
                "_D": "2026-08-21T00:00:20Z",
                "character": {
                    "accountId": "account.tracked",
                    "location": {"x": 300300, "y": 300400, "z": 80000},
                    "isInVehicle": False,
                },
                "elapsedTime": 30,
                "common": {"isGame": 0.3},
            },
            {
                "_T": "LogPlayerPosition",
                "_D": "2026-08-21T00:01:20Z",
                "character": {
                    "accountId": "account.tracked",
                    "location": {"x": 600000, "y": 600000, "z": 0},
                    "isInVehicle": False,
                },
                "elapsedTime": 90,
                "common": {"isGame": 1},
            },
            {
                "_T": "LogPlayerPosition",
                "_D": "2026-08-21T00:01:30Z",
                "character": {
                    "accountId": "account.tracked",
                    "location": {"x": 600000, "y": 600500, "z": 0},
                    "isInVehicle": False,
                },
                "elapsedTime": 100,
                "common": {"isGame": 1},
            },
        ]

        samples = parse_position_samples(
            events,
            match_id="match-distance",
            tracked_account_ids={"account.tracked"},
        )
        summaries = summarize_movement(
            samples,
            landing_events=[],
            match_id="match-distance",
            tracked_account_ids={"account.tracked"},
        )

        self.assertEqual(len(samples), 5)
        self.assertAlmostEqual(summaries[0].total_sampled_distance_m, 10.0)
        self.assertAlmostEqual(summaries[0].in_game_sampled_distance_m, 10.0)


if __name__ == "__main__":
    unittest.main()
