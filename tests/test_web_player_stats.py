from __future__ import annotations

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from pubg_ai.web.app import create_app
from pubg_ai.local_settings import LocalSettingsStore
from pubg_ai.player_registry import PlayerDiscordRegistration, RegisteredPlayer


class WebPlayerStatsTests(unittest.TestCase):
    def test_local_manager_uses_registered_player_weapon_and_match_catalogs(self) -> None:
        response = TestClient(create_app()).get("/")

        self.assertEqual(response.status_code, 200)
        body = response.text
        self.assertIn('id="registeredPlayerOptions"', body)
        self.assertIn('class="registered-player-input"', body)
        self.assertIn('<select name="weapon" required>', body)
        self.assertIn('name="match_search"', body)
        self.assertIn('<select name="match_id" required>', body)
        self.assertIn('<select name="guild_id" id="rankingGuildSelect">', body)
        self.assertIn('id="rankingGuildRefresh"', body)
        self.assertIn('id="rankingViewControls"', body)
        self.assertIn('<option value="top10_rate">TOP10 진입률</option>', body)
        self.assertIn('<option value="fight_win_rate">교전 승리 확률</option>', body)
        self.assertIn('name="min_matches"', body)
        self.assertIn('name="active_only"', body)
        self.assertIn('id="displaySettingsForm"', body)
        self.assertIn('value="korean_units"', body)
        self.assertIn("formatKoreanInteger", body)
        self.assertIn('<label>서버 범위', body)
        self.assertIn('class="advanced-filters"', body)
        self.assertIn('id="trendCards"', body)
        self.assertIn('id="analysisPlayerContextName"', body)
        self.assertIn('id="clearAnalysisPlayer"', body)
        self.assertIn('<option value="date" selected>일자</option>', body)
        self.assertIn('class="trend-line-chart"', body)
        self.assertIn('data-trend-granularity="month"', body)
        self.assertIn('data-weapon-trend-metric', body)
        self.assertIn("await setActiveAnalysisPlayer(player);", body)
        self.assertIn(
            'activeAnalysisPlayer.shard + ":" + activeAnalysisPlayer.account_id !== selectionKey',
            body,
        )
        self.assertIn(
            'activeAnalysisPlayer.shard + ":" + activeAnalysisPlayer.account_id !== preservedPlayerKey',
            body,
        )
        self.assertIn('await postJson("/players/register"', body)
        self.assertIn('await postJson("/players/unregister"', body)
        self.assertIn("교전 데이터 일부를 불러오지 못했습니다", body)
        self.assertNotIn(
            "await loadPlayerWeapon(formElement);\n        clearRegisteredPlayerSearch(formElement);",
            body,
        )
        self.assertIn('data-catalog-facet="maps"', body)
        self.assertIn('<option value="quarter">분기</option>', body)
        self.assertIn('<option value="map">맵</option>', body)
        self.assertIn('<option value="weapon">무기</option>', body)
        self.assertIn('data-player-management="active"', body)
        self.assertIn('data-player-discord-add', body)
        self.assertIn('/players/discord-registrations', body)
        self.assertIn('name="exact_date_kst"', body)
        self.assertIn('match_limit: "5000"', body)
        self.assertIn('/players/catalog?', body)
        self.assertEqual(
            body.count('data-reset-analysis-form="profileForm"'),
            1,
        )

    def test_player_management_endpoint_returns_multiple_discord_registrations(self) -> None:
        player = RegisteredPlayer(
            id=1,
            account_id="account.test",
            shard="steam",
            current_name="Yuuki_Asuna---",
            active=False,
            public_profile=True,
            discord_registrations=(
                PlayerDiscordRegistration(id=1, registered_player_id=1, guild_id="100"),
                PlayerDiscordRegistration(id=2, registered_player_id=1, guild_id="200"),
            ),
        )
        connection = FakeConnection([])
        registry = MagicMock()
        registry.set_player_management.return_value = player

        with (
            patch("pubg_ai.web.app.connect_mysql", return_value=connection),
            patch("pubg_ai.web.app.PlayerRegistry", return_value=registry),
        ):
            response = TestClient(create_app()).post(
                "/players/manage",
                json={"shard": "steam", "account_id": "account.test", "active": False},
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["player"]["active"])
        self.assertEqual(
            [item["guild_id"] for item in response.json()["player"]["discord_registrations"]],
            ["100", "200"],
        )
        registry.set_player_management.assert_called_once_with(
            shard="steam",
            account_id="account.test",
            active=False,
            public_profile=None,
        )
        self.assertTrue(connection.closed)

    def test_player_discord_registration_endpoint_adds_another_guild(self) -> None:
        player = RegisteredPlayer(
            id=1,
            account_id="account.test",
            shard="steam",
            current_name="Yuuki_Asuna---",
            active=True,
            public_profile=True,
            discord_registrations=(
                PlayerDiscordRegistration(id=1, registered_player_id=1, guild_id="100"),
            ),
        )
        refreshed = RegisteredPlayer(
            **{
                **player.__dict__,
                "discord_registrations": (
                    *player.discord_registrations,
                    PlayerDiscordRegistration(
                        id=2,
                        registered_player_id=1,
                        guild_id="200",
                        channel_id="201",
                    ),
                ),
            }
        )
        connection = FakeConnection([])
        registry = MagicMock()
        registry.get_player.side_effect = [player, refreshed]

        with (
            patch("pubg_ai.web.app.connect_mysql", return_value=connection),
            patch("pubg_ai.web.app.PlayerRegistry", return_value=registry),
        ):
            response = TestClient(create_app()).post(
                "/players/discord-registrations",
                json={
                    "shard": "steam",
                    "account_id": "account.test",
                    "guild_id": "200",
                    "channel_id": "201",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["guild_id"] for item in response.json()["player"]["discord_registrations"]],
            ["100", "200"],
        )
        registry.add_discord_registration.assert_called_once_with(
            registered_player_id=1,
            guild_id="200",
            channel_id="201",
            registered_by_discord_user_id=None,
        )
        self.assertTrue(connection.closed)

    def test_player_discord_registration_remove_keeps_other_guilds(self) -> None:
        player = RegisteredPlayer(
            id=1,
            account_id="account.test",
            shard="steam",
            current_name="Yuuki_Asuna---",
            active=True,
            public_profile=True,
            discord_registrations=(
                PlayerDiscordRegistration(id=1, registered_player_id=1, guild_id="100"),
                PlayerDiscordRegistration(id=2, registered_player_id=1, guild_id="200"),
            ),
        )
        refreshed = RegisteredPlayer(
            **{
                **player.__dict__,
                "discord_registrations": (player.discord_registrations[1],),
            }
        )
        connection = FakeConnection([])
        registry = MagicMock()
        registry.get_player.side_effect = [player, refreshed]
        registry.remove_discord_registration.return_value = True

        with (
            patch("pubg_ai.web.app.connect_mysql", return_value=connection),
            patch("pubg_ai.web.app.PlayerRegistry", return_value=registry),
        ):
            response = TestClient(create_app()).post(
                "/players/discord-registrations/remove",
                json={
                    "shard": "steam",
                    "account_id": "account.test",
                    "guild_id": "100",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["guild_id"] for item in response.json()["player"]["discord_registrations"]],
            ["200"],
        )
        registry.remove_discord_registration.assert_called_once_with(
            registered_player_id=1,
            guild_id="100",
        )
        self.assertTrue(connection.closed)

    def test_player_weapon_endpoint_returns_weapon_detail(self) -> None:
        connection = FakeConnection(
            [
                {
                    "id": 1,
                    "account_id": "account.test",
                    "shard": "steam",
                    "current_name": "Yuuki_Asuna---",
                    "active": 1,
                    "public_profile": 1,
                    "registered_by_discord_user_id": None,
                    "registered_guild_id": None,
                    "registered_channel_id": None,
                },
                [{"weapon_code": "WeapHK416_C"}],
                [
                    {
                        "match_id": "match-1",
                        "created_at_kst": datetime(2026, 6, 29, 1, 0, 0),
                        "map_name": "Erangel_Main",
                        "game_mode": "squad-fpp",
                        "win_place": 1,
                        "shots_fired": 100,
                        "shots_hit": 25,
                        "hits_taken": 0,
                        "damage_dealt": 300.0,
                        "damage_taken": 0.0,
                        "kills": 2,
                        "assists": 1,
                        "deaths": 0,
                        "dbnos": 2,
                        "dbnos_taken": 0,
                        "finishes": 1,
                        "finishes_taken": 0,
                        "headshot_hits": 5,
                        "headshot_hits_taken": 0,
                        "headshot_kills": 1,
                        "headshot_deaths": 0,
                        "headshot_dbnos": 1,
                        "headshot_dbnos_taken": 0,
                        "hit_parts": {"head": 5, "torso": 20},
                        "taken_hit_parts": {},
                    }
                ],
                [],
            ]
        )

        with patch("pubg_ai.web.app.connect_mysql", return_value=connection):
            client = TestClient(create_app())
            response = client.get("/players/weapon?shard=steam&name=Yuuki_Asuna---&weapon=M416")

        self.assertEqual(response.status_code, 200)
        payload = response.json()["weapon"]
        self.assertEqual(payload["weapon_code"], "WeapHK416_C")
        self.assertEqual(payload["weapon_name"], "M416")
        self.assertEqual(payload["totals"]["match_count"], 1)
        self.assertEqual(payload["totals"]["kills"], 2)
        self.assertEqual(payload["totals"]["hit_parts"], {"head": 5, "torso": 20})
        self.assertAlmostEqual(payload["totals"]["headshot_hit_rate"], 0.2)
        self.assertAlmostEqual(payload["totals"]["avg_kills"], 2.0)
        self.assertEqual(payload["trend_series"]["date"]["available_point_count"], 1)
        self.assertEqual(payload["trend_series"]["date"]["points"][0]["period_key"], "2026-06-29")
        self.assertEqual(payload["trend_series"]["month"]["points"][0]["match_count"], 1)
        self.assertTrue(connection.closed)

    def test_player_match_endpoint_returns_match_detail(self) -> None:
        connection = FakeConnection(
            [
                {
                    "id": 1,
                    "account_id": "account.test",
                    "shard": "steam",
                    "current_name": "Yuuki_Asuna---",
                    "active": 1,
                    "public_profile": 1,
                    "registered_by_discord_user_id": None,
                    "registered_guild_id": None,
                    "registered_channel_id": None,
                },
                {
                    "match_id": "match-1",
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
                    "human_players": 98,
                    "bot_players": 2,
                    "roster_id": "roster-1",
                    "team_id": 12,
                    "win_place": 1,
                    "death_type": "alive",
                    "raw_stats": {"timeSurvived": 1800},
                    "shots_fired": 100,
                    "shots_hit": 25,
                    "hits_taken": 0,
                    "damage_dealt": 300.0,
                    "damage_taken": 0.0,
                    "kills": 2,
                    "assists": 1,
                    "deaths": 0,
                    "dbnos_caused": 2,
                    "dbnos_taken": 0,
                    "finishes": 1,
                    "finishes_taken": 0,
                    "headshot_hits": 5,
                    "headshot_hits_taken": 0,
                    "headshot_kills": 1,
                    "headshot_deaths": 0,
                    "headshot_dbnos_caused": 1,
                    "headshot_dbnos_taken": 0,
                    "hit_parts": {"head": 5, "torso": 20},
                    "taken_hit_parts": {},
                    "landing_distance_m": 640.0,
                    "in_game_sampled_distance_m": 4200.0,
                },
                [],
                [
                    {
                        "weapon_code": "WeapHK416_C",
                        "shots_fired": 100,
                        "shots_hit": 25,
                        "hits_taken": 0,
                        "damage_dealt": 300.0,
                        "damage_taken": 0.0,
                        "kills": 2,
                        "assists": 1,
                        "deaths": 0,
                        "dbnos": 2,
                        "dbnos_taken": 0,
                        "headshot_kills": 1,
                        "hit_parts": {"head": 5, "torso": 20},
                        "taken_hit_parts": {},
                    }
                ],
                [
                    {
                        "weapon_code": "WeapHK416_C",
                        "shots_fired": 100,
                        "shots_hit": 25,
                    }
                ],
                [
                    {
                        "item_code": "Item_Heal_FirstAid_C",
                        "item_category": "Use",
                        "item_sub_category": "Heal",
                        "picked_up_events": 2,
                        "picked_up_quantity": 2,
                        "loot_box_pickup_events": 0,
                        "carepackage_pickup_events": 0,
                        "custom_package_pickup_events": 0,
                        "vehicle_trunk_pickup_events": 0,
                        "vehicle_trunk_put_events": 0,
                        "dropped_events": 0,
                        "dropped_quantity": 0,
                        "used_events": 1,
                        "used_quantity": 1,
                        "equipped_events": 0,
                        "unequipped_events": 0,
                        "attached_events": 0,
                        "detached_events": 0,
                    }
                ],
                {"heal_events": 1, "normalized_event_count": 2},
                {
                    "fight_count": 3,
                    "wins": 2,
                    "losses": 1,
                    "kill_wins": 1,
                    "dbno_wins": 1,
                    "death_losses": 1,
                    "dbno_losses": 0,
                    "headshot_wins": 1,
                    "human_opponent_fights": 3,
                    "bot_opponent_fights": 0,
                    "unknown_opponent_fights": 0,
                },
            ]
        )

        with patch("pubg_ai.web.app.connect_mysql", return_value=connection):
            client = TestClient(create_app())
            response = client.get("/players/match?shard=steam&name=Yuuki_Asuna---&match_id=match-1")

        self.assertEqual(response.status_code, 200)
        payload = response.json()["match"]
        self.assertEqual(payload["match_id"], "match-1")
        self.assertTrue(payload["is_chicken"])
        self.assertEqual(payload["total_players"], 100)
        self.assertEqual(payload["bot_players"], 2)
        self.assertEqual(payload["weapons"][0]["weapon_name"], "M416")
        self.assertEqual(payload["accuracy_breakdown"]["estimated_hit_rate"], 0.25)
        self.assertEqual(payload["team_mode_ko"], "스쿼드")
        self.assertEqual(payload["items"][0]["item_name"], "구급상자")
        self.assertEqual(payload["item_summary"]["picked_up_quantity"], 2)
        self.assertEqual(payload["activity_summary"]["heal_events"], 1)
        self.assertAlmostEqual(payload["fight_summary"]["fight_win_rate"], 2 / 3)
        self.assertTrue(connection.closed)

    def test_player_ranking_endpoint_returns_rows(self) -> None:
        connection = FakeConnection(
            [
                [
                    {
                        "id": 1,
                        "account_id": "account.test",
                        "shard": "steam",
                        "current_name": "Yuuki_Asuna---",
                        "active": 1,
                        "public_profile": 1,
                        "registered_by_discord_user_id": None,
                        "registered_guild_id": "guild-1",
                        "registered_channel_id": None,
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
                        "last_match_at_kst": datetime(2026, 6, 29, 1, 0, 0),
                    }
                ],
                [],
            ]
        )

        with patch("pubg_ai.web.app.connect_mysql", return_value=connection):
            client = TestClient(create_app())
            response = client.get("/rankings/players?shard=steam&metric=kda&guild_id=guild-1&limit=10")

        self.assertEqual(response.status_code, 200)
        payload = response.json()["ranking"]
        self.assertEqual(payload["metric"], "kda")
        self.assertEqual(payload["guild_id"], "guild-1")
        self.assertFalse(payload["global_scope"])
        self.assertEqual(payload["rows"][0]["player"]["current_name"], "Yuuki_Asuna---")
        self.assertAlmostEqual(payload["rows"][0]["score"], 3.75)
        self.assertTrue(connection.closed)

    def test_discord_guild_endpoint_merges_names_and_registered_player_counts(self) -> None:
        connection = FakeConnection(
            [
                [{"guild_id": "100", "name": "PUBG Server", "ranking_scope": "guild"}],
                [{"guild_id": "100", "registered_player_count": 3}],
                [],
            ]
        )

        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            LocalSettingsStore(base_dir / "config" / "local_settings.json").reconcile_managed_discord_bot(
                bot_user_id="42",
                bot_username="PUBG Metrics",
                guild_ids=["100"],
            )
            with patch("pubg_ai.web.app.connect_mysql", return_value=connection):
                response = TestClient(
                    create_app(base_dir=base_dir, env_file=".missing")
                ).get("/discord/guilds")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["guilds"][0],
            {
                "guild_id": "100",
                "name": "PUBG Server",
                "ranking_scope": "guild",
                "registered_player_count": 3,
                "known_to_bot": True,
            },
        )
        self.assertTrue(connection.closed)

    def test_player_ranking_endpoint_uses_guild_global_scope_setting(self) -> None:
        connection = FakeConnection(
            [
                [
                    {
                        "id": 1,
                        "account_id": "account.test",
                        "shard": "steam",
                        "current_name": "Yuuki_Asuna---",
                        "active": 1,
                        "public_profile": 1,
                        "registered_by_discord_user_id": None,
                        "registered_guild_id": "guild-2",
                        "registered_channel_id": None,
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
                        "last_match_at_kst": datetime(2026, 6, 29, 1, 0, 0),
                    }
                ],
                [],
            ]
        )

        with TemporaryDirectory() as temp_dir:
            settings_file = Path(temp_dir) / "config" / "local_settings.json"
            with patch.dict(os.environ, {"PUBG_LOCAL_SETTINGS_FILE": str(settings_file)}):
                client = TestClient(create_app())
                settings_response = client.post(
                    "/discord/scopes",
                    json={
                        "guild_ranking_scopes": {"guild-1": "global"},
                        "public_profile_default": True,
                    },
                )
                with patch("pubg_ai.web.app.connect_mysql", return_value=connection):
                    response = client.get("/rankings/players?shard=steam&metric=kda&guild_id=guild-1&limit=10")

        self.assertEqual(settings_response.status_code, 200)
        self.assertEqual(response.status_code, 200)
        payload = response.json()["ranking"]
        self.assertIsNone(payload["guild_id"])
        self.assertTrue(payload["global_scope"])
        self.assertEqual(payload["rows"][0]["player"]["registered_guild_id"], "guild-2")
        self.assertTrue(connection.closed)


class FakeConnection:
    def __init__(self, results: list[object]) -> None:
        self.results = list(results)
        self.closed = False

    def cursor(self) -> "FakeCursor":
        return FakeCursor(self)

    def close(self) -> None:
        self.closed = True


class FakeCursor:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, query: str, params: tuple[object, ...] | list[object]) -> None:
        return None

    def fetchone(self) -> object:
        return self.connection.results.pop(0)

    def fetchall(self) -> object:
        return self.connection.results.pop(0)


if __name__ == "__main__":
    unittest.main()
