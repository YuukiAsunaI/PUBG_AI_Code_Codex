from __future__ import annotations

from datetime import date, datetime
import unittest

from pubg_ai.player_stats import (
    PlayerStatsService,
    _movement_distance_from_row,
    weapon_code_from_identifier,
)
from pubg_ai.player_trends import PlayerTrendFilters


class PlayerStatsServiceTests(unittest.TestCase):
    def test_movement_distance_prefers_official_participant_totals(self) -> None:
        distance = _movement_distance_from_row(
            {
                "raw_stats": {
                    "walkDistance": 1250.5,
                    "rideDistance": 3000.0,
                    "swimDistance": 49.5,
                },
                "in_game_sampled_distance_m": 2700.0,
            }
        )

        self.assertEqual(distance, 4300.0)

    def test_movement_distance_falls_back_to_sampled_route(self) -> None:
        distance = _movement_distance_from_row(
            {
                "raw_stats": {},
                "in_game_sampled_distance_m": 2700.0,
            }
        )

        self.assertEqual(distance, 2700.0)

    def test_builds_player_profile_summary(self) -> None:
        connection = FakeConnection(
            [
                {
                    "id": 1,
                    "account_id": "account.test",
                    "shard": "steam",
                    "current_name": "Yuuki_Asuna---",
                    "active": 1,
                    "public_profile": 1,
                    "registered_by_discord_user_id": "user-1",
                    "registered_guild_id": "guild-1",
                    "registered_channel_id": "channel-1",
                },
                {
                    "match_count": 10,
                    "wins": 2,
                    "kills": 25,
                    "assists": 5,
                    "deaths": 8,
                    "dbnos_caused": 13,
                    "dbnos_taken": 4,
                    "damage_dealt": 2500.0,
                    "damage_taken": 1600.0,
                    "shots_fired": 1000,
                    "shots_hit": 210,
                    "headshot_kills": 6,
                    "avg_survival_seconds": 1420.5,
                    "avg_movement_distance_m": 3650.0,
                    "first_match_at_kst": datetime(2026, 6, 1, 20, 0, 0),
                    "last_match_at_kst": datetime(2026, 6, 29, 1, 0, 0),
                },
                [
                    {
                        "weapon_code": "WeapBerylM762_C",
                        "shots_fired": 1000,
                        "shots_hit": 210,
                    }
                ],
                [
                    {
                        "weapon_code": "WeapBerylM762_C",
                        "match_count": 6,
                        "kills": 12,
                        "assists": 2,
                        "deaths": 3,
                        "dbnos": 8,
                        "damage_dealt": 1200.0,
                        "shots_fired": 500,
                        "shots_hit": 95,
                        "headshot_hits": 19,
                        "headshot_kills": 2,
                    }
                ],
                [
                    {
                        "match_id": "match-2",
                        "created_at_kst": datetime(2026, 6, 29, 1, 0, 0),
                        "map_name": "Erangel_Main",
                        "game_mode": "squad-fpp",
                        "match_type": "official",
                        "duration_seconds": 1800,
                        "win_place": 1,
                        "raw_stats": {"timeSurvived": 1788.5},
                        "kills": 5,
                        "assists": 1,
                        "deaths": 0,
                        "dbnos_caused": 3,
                        "damage_dealt": 550.0,
                        "in_game_sampled_distance_m": 4200.0,
                    }
                ],
            ]
        )

        profile = PlayerStatsService(connection).get_profile(
            shard="steam",
            name="Yuuki_Asuna---",
            guild_id="guild-1",
            global_scope=False,
        )

        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.player.current_name, "Yuuki_Asuna---")
        self.assertEqual(profile.totals.match_count, 10)
        self.assertAlmostEqual(profile.totals.win_rate, 0.2)
        self.assertAlmostEqual(profile.totals.kda, 3.75)
        self.assertAlmostEqual(profile.totals.accuracy, 0.21)
        self.assertEqual(profile.totals.accuracy_breakdown.single_projectile_attacks, 1000)
        self.assertEqual(profile.totals.accuracy_breakdown.single_projectile_hit_events, 210)
        self.assertAlmostEqual(profile.totals.headshot_kill_rate, 0.24)
        self.assertEqual(profile.top_weapons[0].weapon_code, "WeapBerylM762_C")
        self.assertEqual(profile.top_weapons[0].weapon_name, "베릴 M762")
        self.assertAlmostEqual(profile.top_weapons[0].accuracy, 0.19)
        self.assertAlmostEqual(profile.top_weapons[0].to_record()["headshot_hit_rate"], 0.2)
        self.assertEqual(
            profile.top_weapons[0].accuracy_metric.metric_kind,
            "estimated_hit_rate",
        )
        self.assertEqual(profile.recent_matches[0].match_id, "match-2")
        self.assertEqual(profile.recent_matches[0].survival_seconds, 1788.5)

        player_query_params = connection.executed[0][1]
        self.assertEqual(player_query_params, ["steam", "Yuuki_Asuna---", "guild-1"])

    def test_non_global_scope_without_guild_returns_none_without_querying(self) -> None:
        connection = FakeConnection([])

        profile = PlayerStatsService(connection).get_profile(
            shard="steam",
            name="Yuuki_Asuna---",
            guild_id=None,
            global_scope=False,
        )

        self.assertIsNone(profile)
        self.assertEqual(connection.executed, [])

    def test_missing_player_returns_none(self) -> None:
        connection = FakeConnection([None])

        profile = PlayerStatsService(connection).get_profile(
            shard="steam",
            name="Missing",
            global_scope=True,
        )

        self.assertIsNone(profile)
        self.assertEqual(len(connection.executed), 1)

    def test_builds_weapon_detail_from_alias_and_part_maps(self) -> None:
        connection = FakeConnection(
            [
                {
                    "id": 1,
                    "account_id": "account.test",
                    "shard": "steam",
                    "current_name": "Yuuki_Asuna---",
                    "active": 1,
                    "public_profile": 1,
                    "registered_by_discord_user_id": "user-1",
                    "registered_guild_id": "guild-1",
                    "registered_channel_id": "channel-1",
                },
                [
                    {"weapon_code": "WeapHK416_C"},
                    {"weapon_code": "WeapBerylM762_C"},
                ],
                [
                    {
                        "match_id": "match-2",
                        "created_at_kst": datetime(2026, 6, 29, 1, 0, 0),
                        "map_name": "Erangel_Main",
                        "game_mode": "squad-fpp",
                        "win_place": 1,
                        "shots_fired": 100,
                        "shots_hit": 30,
                        "hits_taken": 1,
                        "damage_dealt": 350.0,
                        "damage_taken": 90.0,
                        "kills": 3,
                        "assists": 1,
                        "deaths": 0,
                        "dbnos": 2,
                        "dbnos_taken": 0,
                        "finishes": 1,
                        "finishes_taken": 0,
                        "headshot_hits": 6,
                        "headshot_hits_taken": 0,
                        "headshot_kills": 1,
                        "headshot_deaths": 0,
                        "headshot_dbnos": 1,
                        "headshot_dbnos_taken": 0,
                        "hit_parts": {"head": 6, "torso": 20},
                        "taken_hit_parts": '{"arm": 1}',
                    },
                    {
                        "match_id": "match-1",
                        "created_at_kst": datetime(2026, 6, 28, 1, 0, 0),
                        "map_name": "Tiger_Main",
                        "game_mode": "squad",
                        "win_place": 4,
                        "shots_fired": 50,
                        "shots_hit": 10,
                        "hits_taken": 0,
                        "damage_dealt": 120.0,
                        "damage_taken": 0.0,
                        "kills": 1,
                        "assists": 0,
                        "deaths": 0,
                        "dbnos": 1,
                        "dbnos_taken": 0,
                        "finishes": 0,
                        "finishes_taken": 0,
                        "headshot_hits": 2,
                        "headshot_hits_taken": 0,
                        "headshot_kills": 0,
                        "headshot_deaths": 0,
                        "headshot_dbnos": 0,
                        "headshot_dbnos_taken": 0,
                        "hit_parts": {"head": 2, "leg": 3},
                        "taken_hit_parts": {},
                    },
                ],
                [
                    {
                        "match_id": "match-2",
                        "outcome_type": "win",
                        "outcome_reason": "kill",
                        "attachment_codes": '["Item_Attach_Weapon_Lower_Foregrip_C", "Item_Attach_Weapon_Muzzle_AR_Compensator_C"]',
                        "attachment_names_ko": '["수직 손잡이", "보정기"]',
                        "is_headshot": 1,
                        "win_place": 1,
                        "distance_m": 42.0,
                    },
                    {
                        "match_id": "match-2",
                        "outcome_type": "win",
                        "outcome_reason": "dbno_caused",
                        "attachment_codes": ["Item_Attach_Weapon_Lower_Foregrip_C", "Item_Attach_Weapon_Muzzle_AR_Compensator_C"],
                        "attachment_names_ko": ["수직 손잡이", "보정기"],
                        "is_headshot": 0,
                        "win_place": 1,
                        "distance_m": 48.0,
                    },
                    {
                        "match_id": "match-2",
                        "outcome_type": "loss",
                        "outcome_reason": "death",
                        "attachment_codes": ["Item_Attach_Weapon_Lower_Foregrip_C", "Item_Attach_Weapon_Muzzle_AR_Compensator_C"],
                        "attachment_names_ko": ["수직 손잡이", "보정기"],
                        "is_headshot": 0,
                        "win_place": 1,
                        "distance_m": 45.0,
                    },
                    {
                        "match_id": "match-1",
                        "outcome_type": "loss",
                        "outcome_reason": "dbno_taken",
                        "attachment_codes": [],
                        "attachment_names_ko": [],
                        "is_headshot": 0,
                        "win_place": 4,
                        "distance_m": 112.0,
                    },
                ],
            ]
        )

        detail = PlayerStatsService(connection).get_weapon_detail(
            shard="steam",
            name="Yuuki_Asuna---",
            guild_id="guild-1",
            weapon="M416",
            filters=PlayerTrendFilters(
                map_name="Erangel_Main",
                season_state="progress",
                year=2026,
                quarter=2,
                month=6,
                exact_date_kst=date(2026, 6, 29),
                hour=1,
            ),
        )

        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertEqual(detail.weapon_code, "WeapHK416_C")
        self.assertEqual(detail.weapon_name, "M416")
        self.assertEqual(detail.totals.match_count, 2)
        self.assertEqual(detail.totals.wins, 1)
        self.assertEqual(detail.totals.kills, 4)
        self.assertEqual(detail.totals.dbnos, 3)
        self.assertAlmostEqual(detail.totals.accuracy, 40 / 150)
        self.assertAlmostEqual(detail.totals.avg_damage_dealt, 235.0)
        self.assertEqual(detail.totals.hit_parts, {"head": 8, "torso": 20, "leg": 3})
        self.assertEqual(detail.totals.taken_hit_parts, {"arm": 1})
        self.assertEqual(detail.totals.fight_count, 4)
        self.assertEqual(detail.totals.fight_wins, 2)
        self.assertAlmostEqual(detail.totals.fight_win_rate, 0.5)
        self.assertEqual(detail.totals.avg_fights_per_match, 2.0)
        baseline = detail.attachment_analysis.no_attachment
        self.assertIsNotNone(baseline)
        assert baseline is not None
        self.assertEqual(baseline.fight_count, 1)
        self.assertEqual(baseline.fight_losses, 1)
        self.assertEqual(baseline.fight_win_rate, 0.0)
        vertical = next(
            item
            for item in detail.attachment_analysis.individual
            if item.attachment_codes == ("Item_Attach_Weapon_Lower_Foregrip_C",)
        )
        self.assertEqual(vertical.fight_count, 3)
        self.assertEqual(vertical.fight_wins, 2)
        self.assertAlmostEqual(vertical.fight_win_rate, 2 / 3)
        self.assertAlmostEqual(vertical.fight_win_rate_delta_vs_no_attachment or 0, 2 / 3)
        self.assertEqual(vertical.match_count, 1)
        self.assertEqual(vertical.match_wins, 1)
        self.assertEqual(len(detail.attachment_analysis.combinations), 1)
        self.assertEqual(detail.attachment_analysis.combinations[0].fight_count, 3)
        self.assertEqual(detail.effective_ranges[0].bucket_label, "25-50m")
        self.assertEqual(detail.effective_ranges[0].wins, 2)
        self.assertEqual(detail.effective_ranges[0].losses, 1)
        self.assertEqual(detail.recent_matches[0].match_id, "match-2")
        self.assertEqual(detail.filters.year, 2026)
        daily = detail.trend_series["date"]
        self.assertEqual(daily.available_point_count, 2)
        self.assertFalse(daily.truncated)
        self.assertEqual([point.period_key for point in daily.points], ["2026-06-28", "2026-06-29"])
        self.assertEqual(daily.points[0].totals.match_count, 1)
        self.assertAlmostEqual(daily.points[0].totals.headshot_hits / daily.points[0].totals.shots_hit, 0.2)
        self.assertEqual(daily.points[0].totals.fight_losses, 1)
        self.assertAlmostEqual(daily.points[1].totals.fight_win_rate, 2 / 3)
        monthly = detail.trend_series["month"]
        self.assertEqual(monthly.available_point_count, 1)
        self.assertEqual(monthly.points[0].totals.match_count, detail.totals.match_count)
        self.assertEqual(monthly.points[0].totals.kills, detail.totals.kills)
        weekly = detail.trend_series["week"]
        self.assertEqual(weekly.available_point_count, 2)
        self.assertEqual(
            [point.period_key for point in weekly.points],
            ["2026-W26", "2026-W27"],
        )
        serialized = detail.to_record()
        self.assertAlmostEqual(serialized["totals"]["headshot_hit_rate"], 8 / 40)
        self.assertAlmostEqual(serialized["totals"]["avg_kills"], 2.0)
        self.assertEqual(serialized["trend_series"]["date"]["returned_point_count"], 2)
        self.assertEqual(serialized["attachment_analysis"]["no_attachment"]["fight_losses"], 1)
        self.assertEqual(len(serialized["attachment_analysis"]["individual"]), 2)
        query, params = connection.executed[2]
        self.assertIn("matches.map_name = %s", query)
        self.assertIn("matches.season_state = %s", query)
        self.assertIn("YEAR(matches.created_at_kst) = %s", query)
        self.assertIn("QUARTER(matches.created_at_kst) = %s", query)
        self.assertIn("MONTH(matches.created_at_kst) = %s", query)
        self.assertIn("HOUR(matches.created_at_kst) = %s", query)
        self.assertIn(datetime(2026, 6, 29), params)
        fight_query, fight_params = connection.executed[3]
        self.assertIn("outcomes.is_friendly_fire = 0", fight_query)
        self.assertIn("outcomes.match_id", fight_query)
        self.assertIn("outcomes.attachment_codes", fight_query)
        self.assertIn("participants.win_place", fight_query)
        self.assertIn("matches.map_name = %s", fight_query)
        self.assertEqual(fight_params[:3], ["account.test", "steam", "WeapHK416_C"])

    def test_builds_registered_player_lookup_catalog(self) -> None:
        connection = FakeConnection(
            [
                {
                    "id": 1,
                    "account_id": "account.test",
                    "shard": "steam",
                    "current_name": "Yuuki_Asuna---",
                    "active": 1,
                    "public_profile": 1,
                    "registered_by_discord_user_id": "user-1",
                    "registered_guild_id": "guild-1",
                    "registered_channel_id": "channel-1",
                },
                [
                    {"weapon_code": "WeapRPD_C", "match_count": 3},
                    {"weapon_code": "WeapM24_C", "match_count": 2},
                ],
                [
                    {
                        "match_id": "match-2",
                        "created_at_kst": datetime(2026, 8, 13, 21, 30),
                        "map_name": "Erangel_Main",
                        "game_mode": "squad-fpp",
                        "team_mode": "squad",
                        "perspective": "fpp",
                        "match_type": "official",
                        "season_state": "progress",
                        "win_place": 1,
                        "kills": 4,
                        "assists": 2,
                        "deaths": 0,
                        "dbnos_caused": 5,
                        "damage_dealt": 620.0,
                    }
                ],
            ]
        )

        catalog = PlayerStatsService(connection).get_lookup_catalog(
            shard="steam",
            name="Yuuki_Asuna---",
            global_scope=True,
        )

        self.assertIsNotNone(catalog)
        assert catalog is not None
        self.assertEqual(catalog.weapons[0].weapon_name, "RPD")
        self.assertEqual(catalog.weapons[0].weapon_family, "LMG")
        self.assertEqual(catalog.matches[0].match_id, "match-2")
        self.assertEqual(catalog.facets["maps"], ["Erangel_Main"])
        self.assertEqual(catalog.facets["years"], [2026])
        self.assertEqual(catalog.to_record()["matches"][0]["map_name_ko"], "에란겔")
        weapon_catalog_query, _ = connection.executed[1]
        self.assertIn("weapon_stats.shots_fired > 0", weapon_catalog_query)
        self.assertIn("weapon_stats.assists > 0", weapon_catalog_query)
        self.assertNotIn("weapon_stats.damage_taken > 0", weapon_catalog_query)
        self.assertNotIn("weapon_stats.dbnos_taken > 0", weapon_catalog_query)

    def test_builds_match_detail_with_weapon_and_replay_summary(self) -> None:
        connection = FakeConnection(
            [
                {
                    "id": 1,
                    "account_id": "account.test",
                    "shard": "steam",
                    "current_name": "Yuuki_Asuna---",
                    "active": 1,
                    "public_profile": 1,
                    "registered_by_discord_user_id": "user-1",
                    "registered_guild_id": "guild-1",
                    "registered_channel_id": "channel-1",
                },
                {
                    "match_id": "match-2",
                    "shard": "steam",
                    "map_name": "Erangel_Main",
                    "game_mode": "squad-fpp",
                    "match_type": "official",
                    "team_mode": "squad",
                    "perspective": "fpp",
                    "is_custom_match": 0,
                    "season_state": "progress",
                    "created_at_kst": datetime(2026, 6, 29, 1, 0, 0),
                    "duration_seconds": 1800,
                    "total_players": 100,
                    "human_players": 96,
                    "bot_players": 4,
                    "roster_id": "roster-1",
                    "team_id": 12,
                    "win_place": 2,
                    "death_type": "byplayer",
                    "raw_stats": '{"timeSurvived": 1750.5, "walkDistance": 1000, "rideDistance": 2500}',
                    "shots_fired": 200,
                    "shots_hit": 50,
                    "hits_taken": 8,
                    "damage_dealt": 620.0,
                    "damage_taken": 310.0,
                    "kills": 4,
                    "assists": 1,
                    "deaths": 1,
                    "dbnos_caused": 5,
                    "dbnos_taken": 1,
                    "finishes": 3,
                    "finishes_taken": 1,
                    "headshot_hits": 10,
                    "headshot_hits_taken": 2,
                    "headshot_kills": 2,
                    "headshot_deaths": 0,
                    "headshot_dbnos_caused": 2,
                    "headshot_dbnos_taken": 0,
                    "hit_parts": {"head": 10, "torso": 34},
                    "taken_hit_parts": {"arm": 3, "leg": 2},
                    "landing_distance_m": 760.0,
                    "in_game_sampled_distance_m": None,
                },
                [
                    {
                        "id": 10,
                        "match_id": "match-2",
                        "shard": "steam",
                        "artifact_type": "map_snapshot",
                        "artifact_name": "player-route",
                        "account_id": "account.test",
                        "player_name": "Yuuki_Asuna---",
                        "map_name": "Erangel_Main",
                        "game_mode": "squad-fpp",
                        "match_type": "official",
                        "match_created_at_kst": datetime(2026, 6, 29, 1, 0, 0),
                        "storage_backend": "local_file",
                        "storage_root": "PUBG_REPLAY_DATA_DIR",
                        "relative_path": "map_snapshot/steam/2026/06/29/match-2/player-route.jpg",
                        "content_type": "image/jpeg",
                        "size_bytes": 2048,
                        "sha256": "abc123",
                        "renderer_version": "test",
                        "generated_at_kst": datetime(2026, 6, 29, 1, 3, 0),
                    }
                ],
                [
                    {
                        "weapon_code": "WeapHK416_C",
                        "shots_fired": 120,
                        "shots_hit": 36,
                        "hits_taken": 0,
                        "damage_dealt": 420.0,
                        "damage_taken": 50.0,
                        "kills": 3,
                        "assists": 1,
                        "deaths": 0,
                        "dbnos": 4,
                        "dbnos_taken": 1,
                        "headshot_kills": 1,
                        "hit_parts": '{"head": 6, "torso": 24}',
                        "taken_hit_parts": {"arm": 1},
                    }
                ],
                [
                    {
                        "weapon_code": "WeapHK416_C",
                        "shots_fired": 200,
                        "shots_hit": 50,
                    }
                ],
                [
                    {
                        "item_code": "Item_Heal_FirstAid_C",
                        "item_category": "Use",
                        "item_sub_category": "Heal",
                        "picked_up_events": 3,
                        "picked_up_quantity": 3,
                        "loot_box_pickup_events": 1,
                        "carepackage_pickup_events": 0,
                        "custom_package_pickup_events": 0,
                        "vehicle_trunk_pickup_events": 0,
                        "vehicle_trunk_put_events": 0,
                        "dropped_events": 1,
                        "dropped_quantity": 1,
                        "used_events": 2,
                        "used_quantity": 2,
                        "equipped_events": 0,
                        "unequipped_events": 0,
                        "attached_events": 0,
                        "detached_events": 0,
                    }
                ],
                {
                    "heal_events": 3,
                    "heal_amount": 120.0,
                    "throwable_uses": 2,
                    "vehicle_rides": 1,
                    "normalized_event_count": 8,
                },
                {
                    "fight_count": 7,
                    "wins": 5,
                    "losses": 2,
                    "kill_wins": 3,
                    "dbno_wins": 2,
                    "death_losses": 1,
                    "dbno_losses": 1,
                    "headshot_wins": 2,
                    "human_opponent_fights": 6,
                    "bot_opponent_fights": 1,
                    "unknown_opponent_fights": 0,
                },
            ]
        )

        detail = PlayerStatsService(connection).get_match_detail(
            shard="steam",
            match_id="match-2",
            name="Yuuki_Asuna---",
            guild_id="guild-1",
        )

        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertEqual(detail.player.current_name, "Yuuki_Asuna---")
        self.assertEqual(detail.match_id, "match-2")
        self.assertEqual(detail.total_players, 100)
        self.assertEqual(detail.human_players, 96)
        self.assertEqual(detail.bot_players, 4)
        self.assertFalse(detail.is_chicken)
        self.assertEqual(detail.win_place, 2)
        self.assertEqual(detail.dbnos_caused, 5)
        self.assertEqual(detail.dbnos_taken, 1)
        self.assertAlmostEqual(detail.accuracy, 0.25)
        self.assertEqual(detail.accuracy_breakdown.single_projectile_attacks, 200)
        self.assertEqual(detail.accuracy_breakdown.single_projectile_hit_events, 50)
        self.assertEqual(detail.hit_parts, {"head": 10, "torso": 34})
        self.assertEqual(detail.survival_seconds, 1750.5)
        self.assertEqual(detail.movement_distance_m, 3500.0)
        self.assertEqual(detail.weapons[0].weapon_name, "M416")
        self.assertAlmostEqual(detail.weapons[0].accuracy, 0.3)
        self.assertEqual(detail.weapons[0].accuracy_metric.metric_kind, "estimated_hit_rate")
        self.assertEqual(detail.team_mode, "squad")
        self.assertEqual(detail.items[0].item_name, "구급상자")
        self.assertEqual(detail.item_summary["picked_up_quantity"], 3)
        self.assertEqual(detail.item_summary["used_quantity"], 2)
        self.assertEqual(detail.activity_summary["heal_events"], 3)
        self.assertAlmostEqual(detail.fight_summary["fight_win_rate"], 5 / 7)
        self.assertIsNotNone(detail.replay_artifact)
        assert detail.replay_artifact is not None
        self.assertEqual(detail.replay_artifact.view_url, "/replay/artifacts/10/file")

        self.assertEqual(connection.executed[0][1], ["steam", "Yuuki_Asuna---", "guild-1"])
        self.assertIn("artifacts.match_id = %s", connection.executed[2][0])

    def test_match_detail_without_target_selects_registered_participant_in_scope(self) -> None:
        connection = FakeConnection(
            [
                {
                    "id": 1,
                    "account_id": "account.test",
                    "shard": "steam",
                    "current_name": "Yuuki_Asuna---",
                    "active": 1,
                    "public_profile": 1,
                    "registered_by_discord_user_id": "user-1",
                    "registered_guild_id": "guild-1",
                    "registered_channel_id": "channel-1",
                },
                {
                    "match_id": "match-2",
                    "shard": "steam",
                    "map_name": "Erangel_Main",
                    "game_mode": "squad-fpp",
                    "match_type": "official",
                    "team_mode": "squad",
                    "perspective": "fpp",
                    "is_custom_match": 0,
                    "season_state": "progress",
                    "created_at_kst": datetime(2026, 6, 29, 1, 0, 0),
                    "duration_seconds": 1800,
                    "total_players": 100,
                    "human_players": 100,
                    "bot_players": 0,
                    "roster_id": "roster-1",
                    "team_id": 12,
                    "win_place": 1,
                    "death_type": "alive",
                    "raw_stats": {"timeSurvived": 1800},
                    "shots_fired": 10,
                    "shots_hit": 5,
                    "hits_taken": 0,
                    "damage_dealt": 100.0,
                    "damage_taken": 0.0,
                    "kills": 1,
                    "assists": 0,
                    "deaths": 0,
                    "dbnos_caused": 1,
                    "dbnos_taken": 0,
                    "finishes": 1,
                    "finishes_taken": 0,
                    "headshot_hits": 1,
                    "headshot_hits_taken": 0,
                    "headshot_kills": 1,
                    "headshot_deaths": 0,
                    "headshot_dbnos_caused": 1,
                    "headshot_dbnos_taken": 0,
                    "hit_parts": {},
                    "taken_hit_parts": {},
                    "landing_distance_m": None,
                    "in_game_sampled_distance_m": 5000.0,
                },
                [],
                [],
                [
                    {
                        "weapon_code": "WeapHK416_C",
                        "shots_fired": 10,
                        "shots_hit": 5,
                    }
                ],
                [],
                None,
                {
                    "fight_count": 0,
                    "wins": 0,
                    "losses": 0,
                    "kill_wins": 0,
                    "dbno_wins": 0,
                    "death_losses": 0,
                    "dbno_losses": 0,
                    "headshot_wins": 0,
                    "human_opponent_fights": 0,
                    "bot_opponent_fights": 0,
                    "unknown_opponent_fights": 0,
                },
            ]
        )

        detail = PlayerStatsService(connection).get_match_detail(
            shard="steam",
            match_id="match-2",
            guild_id="guild-1",
        )

        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertTrue(detail.is_chicken)
        self.assertEqual(detail.player.account_id, "account.test")
        self.assertEqual(connection.executed[0][1], ["steam", "match-2", "guild-1"])

    def test_weapon_identifier_aliases_common_names(self) -> None:
        self.assertEqual(weapon_code_from_identifier("M416"), "WeapHK416_C")
        self.assertEqual(weapon_code_from_identifier("Beryl"), "WeapBerylM762_C")
        self.assertEqual(weapon_code_from_identifier("Item_Weapon_AK47_C"), "WeapAK47_C")
        self.assertEqual(weapon_code_from_identifier("RPD"), "WeapRPD_C")


class FakeConnection:
    def __init__(self, results: list[object]) -> None:
        self.results = list(results)
        self.executed: list[tuple[str, list[object]]] = []

    def cursor(self) -> "FakeCursor":
        return FakeCursor(self)


class FakeCursor:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, query: str, params: tuple[object, ...] | list[object]) -> None:
        self.connection.executed.append((query, list(params)))

    def fetchone(self) -> object:
        return self.connection.results.pop(0)

    def fetchall(self) -> object:
        return self.connection.results.pop(0)


if __name__ == "__main__":
    unittest.main()
