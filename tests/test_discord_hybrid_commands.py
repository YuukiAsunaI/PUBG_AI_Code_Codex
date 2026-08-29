from __future__ import annotations

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch
import unittest

from discord.ext.commands import HybridCommand

from pubg_ai.config import AppConfig, DatabaseConfig, RuntimeConfig, SecretConfig
from pubg_ai.discord_bot import create_discord_bot
from pubg_ai.discord_command_catalog import DISCORD_COMMAND_SPECS
from pubg_ai.discord_permissions import DiscordPermissionChecker
from pubg_ai.local_settings import DEFAULT_COMMAND_GROUPS, DiscordPermissionSettings, LocalSettingsStore
from pubg_ai.player_registry import PlayerRegistry, RegisteredPlayer
from pubg_ai.player_stats import PlayerCatalogMatch, PlayerLookupCatalog, PlayerStatsService


class DiscordHybridCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_core_commands_are_registered_for_prefix_and_slash_use(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            store = LocalSettingsStore(base_dir / "config" / "local_settings.json", base_dir=base_dir)
            checker = DiscordPermissionChecker(
                DiscordPermissionSettings(
                    command_groups=DEFAULT_COMMAND_GROUPS,
                    user_grants={},
                    guild_user_grants={},
                    global_admin_user_ids=[],
                )
            )
            config = RuntimeConfig(
                app=AppConfig(raw_data_dir=base_dir / "raw", replay_data_dir=base_dir / "replay"),
                database=DatabaseConfig(),
                secrets=SecretConfig(discord_bot_token="test-token"),
            )
            bot = create_discord_bot(
                config=config,
                permission_checker=checker,
                scope_settings_store=store,
            )
            try:
                for spec in DISCORD_COMMAND_SPECS:
                    command = bot.get_command(spec.name)
                    self.assertIsInstance(command, HybridCommand, spec.name)
                    for alias in spec.aliases:
                        self.assertIs(bot.get_command(alias), command, alias)
                slash_names = {command.name for command in bot.tree.get_commands()}
                self.assertTrue(
                    {spec.name for spec in DISCORD_COMMAND_SPECS}.issubset(slash_names)
                )
            finally:
                await bot.close()

    async def test_application_commands_are_filtered_per_guild(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            store = LocalSettingsStore(base_dir / "local_settings.json", base_dir=base_dir)
            store.save_discord_bot_settings(
                auto_start=False,
                command_prefix="!",
                guild_enabled_commands={"100": ["전적", "추천"]},
            )
            checker = DiscordPermissionChecker(store.load_discord_permission_settings())
            config = RuntimeConfig(
                app=AppConfig(raw_data_dir=base_dir / "raw", replay_data_dir=base_dir / "replay"),
                database=DatabaseConfig(),
                secrets=SecretConfig(discord_bot_token="test-token"),
            )
            bot = create_discord_bot(
                config=config,
                permission_checker=checker,
                scope_settings_store=store,
            )
            guild = SimpleNamespace(id=100, name="Test Guild")
            bot._connection._guilds = {100: guild}

            async def fake_sync(*, guild=None):
                return list(bot.tree.get_commands(guild=guild))

            bot.tree.sync = AsyncMock(side_effect=fake_sync)
            try:
                counts = await bot.pubg_sync_application_commands()
                guild_commands = {
                    command.name for command in bot.tree.get_commands(guild=guild)
                }

                self.assertEqual(guild_commands, {"전적", "추천"})
                self.assertEqual(counts, {"100": 2})
                self.assertEqual(bot.tree.get_commands(), [])
            finally:
                await bot.close()

    async def test_partial_guild_events_preserve_full_managed_membership(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            store = LocalSettingsStore(base_dir / "local_settings.json", base_dir=base_dir)
            checker = DiscordPermissionChecker(store.load_discord_permission_settings())
            config = RuntimeConfig(
                app=AppConfig(raw_data_dir=base_dir / "raw", replay_data_dir=base_dir / "replay"),
                database=DatabaseConfig(),
                secrets=SecretConfig(discord_bot_token="test-token"),
            )
            bot = create_discord_bot(
                config=config,
                permission_checker=checker,
                scope_settings_store=store,
            )
            bot._connection.user = SimpleNamespace(id=42, name="PUBG Metrics")
            first = SimpleNamespace(id=100, name="First")
            second = SimpleNamespace(id=200, name="Second renamed")
            bot._connection._guilds = {100: first, 200: second}

            with (
                patch("pubg_ai.discord_bot.connect_mysql") as connect_mysql,
                patch("pubg_ai.discord_bot.sync_discord_guild_catalog"),
            ):
                try:
                    bot.pubg_sync_known_guilds([second])
                    after_update = store.load_discord_bot_settings()
                    bot._connection._guilds = {100: first}
                    bot.pubg_sync_known_guilds([])
                    after_remove = store.load_discord_bot_settings()
                finally:
                    await bot.close()

            self.assertEqual(after_update.managed_guild_ids, ["100", "200"])
            self.assertEqual(after_remove.managed_guild_ids, ["100"])
            self.assertEqual(connect_mysql.call_count, 2)

    async def test_every_slash_option_has_a_real_description_and_core_inputs_are_ergonomic(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            store = LocalSettingsStore(base_dir / "local_settings.json", base_dir=base_dir)
            checker = DiscordPermissionChecker(store.load_discord_permission_settings())
            config = RuntimeConfig(
                app=AppConfig(raw_data_dir=base_dir / "raw", replay_data_dir=base_dir / "replay"),
                database=DatabaseConfig(),
                secrets=SecretConfig(discord_bot_token="test-token"),
            )
            bot = create_discord_bot(
                config=config,
                permission_checker=checker,
                scope_settings_store=store,
            )
            try:
                payloads = {
                    command.name: command.to_dict(bot.tree)
                    for command in bot.tree.get_commands()
                }
                for command_name, payload in payloads.items():
                    for option in payload.get("options", []):
                        self.assertNotEqual(option["description"], "…", (command_name, option["name"]))
                        self.assertTrue(option["description"].strip(), (command_name, option["name"]))
                        self.assertLessEqual(len(option.get("choices", [])), 25)

                trend_names = {item["name"] for item in payloads["추세"]["options"]}
                self.assertNotIn("옵션", trend_names)
                self.assertTrue(
                    {"닉네임", "집계_단위", "맵", "게임_모드", "시작일_kst", "종료일_kst"}
                    <= trend_names
                )

                recommendation_names = {item["name"] for item in payloads["추천"]["options"]}
                self.assertIn("최소_표본_경기", recommendation_names)
                self.assertIn("결과_수", recommendation_names)

                match_options = {item["name"]: item for item in payloads["매치"]["options"]}
                self.assertFalse(match_options["닉네임"].get("required", False))
                self.assertFalse(match_options["닉네임"].get("autocomplete", False))
                self.assertFalse(match_options["최근_매치"].get("required", False))
                self.assertTrue(match_options["최근_매치"]["autocomplete"])

                snapshot_options = {
                    item["name"]: item for item in payloads["최근스냅샷"]["options"]
                }
                self.assertFalse(snapshot_options["닉네임"].get("required", False))
                self.assertTrue(snapshot_options["최근_매치"]["autocomplete"])

                ranking_options = {item["name"]: item for item in payloads["랭킹"]["options"]}
                self.assertTrue(ranking_options["랭킹_지표"]["autocomplete"])
                self.assertIn("최소_경기", ranking_options)

                registered_player_parameters = {
                    "유저조회": "name",
                    "전적": "name",
                    "교전": "name",
                    "추세": "name",
                    "무기": "name",
                    "추천": "name",
                    "매치": "name",
                    "유저삭제": "target",
                    "최근스냅샷": "name",
                    "pubg-delete-data": "target",
                }
                for command_name, parameter_name in registered_player_parameters.items():
                    parameter = bot.get_command(command_name).app_command._params[parameter_name]
                    self.assertFalse(callable(parameter.autocomplete), command_name)
                    option = next(
                        item
                        for item in payloads[command_name]["options"]
                        if item["name"] in {"닉네임", "대상"}
                    )
                    self.assertIn("현재 Discord 서버", option["description"])
                    self.assertIn("페이지·검색", option["description"])
                    self.assertFalse(option.get("required", False))
            finally:
                await bot.close()

    async def test_registered_player_picker_is_guild_scoped_paged_and_match_autocomplete_is_searchable(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            store = LocalSettingsStore(base_dir / "local_settings.json", base_dir=base_dir)
            checker = DiscordPermissionChecker(
                DiscordPermissionSettings(
                    command_groups=DEFAULT_COMMAND_GROUPS,
                    user_grants={"7": ["profile_read"]},
                    guild_user_grants={},
                    global_admin_user_ids=[],
                )
            )
            config = RuntimeConfig(
                app=AppConfig(raw_data_dir=base_dir / "raw", replay_data_dir=base_dir / "replay"),
                database=DatabaseConfig(),
                secrets=SecretConfig(discord_bot_token="test-token"),
            )
            bot = create_discord_bot(
                config=config,
                permission_checker=checker,
                scope_settings_store=None,
            )
            connection = SimpleNamespace(close=lambda: None)
            players = [
                RegisteredPlayer(
                    id=index,
                    account_id=f"account.{index:04d}",
                    shard="steam",
                    current_name=f"KiPlayer{index:02d}",
                    active=True,
                    public_profile=True,
                )
                for index in range(40)
            ]
            ctx = SimpleNamespace(
                author=SimpleNamespace(id=7),
                guild=SimpleNamespace(id=100),
                channel=SimpleNamespace(id=200),
                interaction=SimpleNamespace(),
                reply=AsyncMock(),
            )
            try:
                stats_command = bot.get_command("전적")
                ctx.command = stats_command

                def page_registered_players(**kwargs):
                    offset = int(kwargs["offset"])
                    limit = int(kwargs["limit"])
                    return players[offset : offset + limit], len(players)

                interaction = SimpleNamespace(
                    user=ctx.author,
                    guild=ctx.guild,
                    channel=ctx.channel,
                    response=SimpleNamespace(
                        edit_message=AsyncMock(),
                        send_message=AsyncMock(),
                        send_modal=AsyncMock(),
                        defer=AsyncMock(),
                    ),
                    edit_original_response=AsyncMock(),
                )

                with (
                    patch("pubg_ai.discord_bot.connect_mysql", return_value=connection),
                    patch.object(
                        PlayerRegistry,
                        "list_players_page",
                        side_effect=page_registered_players,
                    ) as list_players_page,
                ):
                    await stats_command.callback(ctx, None, "steam")
                    picker = ctx.reply.await_args.kwargs["view"]
                    first_select = next(item for item in picker.children if hasattr(item, "options"))
                    self.assertEqual(len(first_select.options), 25)
                    self.assertIn(
                        "검색 결과 40명",
                        ctx.reply.await_args.kwargs["embed"].fields[2].value,
                    )
                    self.assertTrue(ctx.reply.await_args.kwargs["ephemeral"])

                    search_button = next(
                        item for item in picker.children if getattr(item, "label", None) == "검색"
                    )
                    await search_button.callback(interaction)
                    search_modal = interaction.response.send_modal.await_args.args[0]
                    self.assertEqual(search_modal.title, "등록 유저 검색")
                    self.assertIn("일부 글자", search_modal.query_input.placeholder)

                    next_button = next(
                        item
                        for item in picker.children
                        if getattr(item, "label", None) == "다음 25명"
                    )
                    await next_button.callback(interaction)
                    second_select = next(item for item in picker.children if hasattr(item, "options"))
                    self.assertEqual(len(second_select.options), 15)
                    second_select._values = [players[25].account_id]
                    await second_select.callback(interaction)

                    with (
                        patch.object(
                            PlayerStatsService,
                            "get_profile",
                            return_value=object(),
                        ) as get_profile,
                        patch(
                            "pubg_ai.discord_bot.format_player_profile_stats",
                            return_value="전적 분석\n- 경기: 1\n- KDA: 2.0",
                        ),
                    ):
                        execute_button = next(
                            item
                            for item in picker.children
                            if getattr(item, "label", None) == "전적 조회"
                        )
                        await execute_button.callback(interaction)

                    get_profile.assert_called_once_with(
                        shard="steam",
                        account_id=players[25].account_id,
                        name=None,
                        guild_id="100",
                        global_scope=False,
                    )
                    result_embed = interaction.edit_original_response.await_args.kwargs["embed"]
                    self.assertEqual(result_embed.title, "전적 분석")

                self.assertEqual(
                    list_players_page.call_args_list,
                    [
                        call(
                            shard="steam",
                            registered_guild_id="100",
                            search=None,
                            active_only=False,
                            limit=25,
                            offset=0,
                        ),
                        call(
                            shard="steam",
                            registered_guild_id="100",
                            search=None,
                            active_only=False,
                            limit=25,
                            offset=25,
                        ),
                    ],
                )

                player = players[0]
                match = PlayerCatalogMatch(
                    match_id="f4ae05ae-e027-4123-9cbb-ed862da93e9c",
                    created_at_kst=datetime(2026, 8, 25, 22, 10),
                    map_name="Tiger_Main",
                    game_mode="squad-fpp",
                    team_mode="squad",
                    perspective="fpp",
                    match_type="official",
                    season_state="progress",
                    win_place=3,
                    kills=5,
                    assists=1,
                    deaths=1,
                    dbnos_caused=4,
                    damage_dealt=712.4,
                )
                catalog = PlayerLookupCatalog(
                    player=player,
                    weapons=[],
                    matches=[match],
                    facets={"maps": ["Tiger_Main"], "game_modes": ["squad-fpp"]},
                )
                match_interaction = SimpleNamespace(
                    guild_id=100,
                    user=SimpleNamespace(id=7),
                    namespace=SimpleNamespace(**{"닉네임": player.current_name, "플랫폼": "steam"}),
                )
                match_callback = bot.get_command("매치").app_command._params["match_id"].autocomplete
                with (
                    patch("pubg_ai.discord_bot.connect_mysql", return_value=connection),
                    patch.object(
                        PlayerStatsService,
                        "get_lookup_catalog",
                        return_value=catalog,
                    ) as get_catalog,
                ):
                    match_choices = await match_callback(match_interaction, "태이고")

                self.assertEqual(len(match_choices), 1)
                self.assertEqual(match_choices[0].value, match.match_id)
                self.assertIn("2026-08-25 22:10", match_choices[0].name)
                self.assertIn("태이고", match_choices[0].name)
                self.assertIn("3등", match_choices[0].name)
                get_catalog.assert_called_once_with(
                    shard="steam",
                    account_id=None,
                    name=player.current_name,
                    guild_id="100",
                    global_scope=False,
                    match_limit=500,
                )
            finally:
                await bot.close()

    async def test_recommendation_and_latest_match_commands_forward_clear_options_and_reply_with_embeds(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            checker = DiscordPermissionChecker(
                DiscordPermissionSettings(
                    command_groups=DEFAULT_COMMAND_GROUPS,
                    user_grants={"7": ["profile_read"]},
                    guild_user_grants={},
                    global_admin_user_ids=[],
                )
            )
            config = RuntimeConfig(
                app=AppConfig(raw_data_dir=base_dir / "raw", replay_data_dir=base_dir / "replay"),
                database=DatabaseConfig(),
                secrets=SecretConfig(discord_bot_token="test-token"),
            )
            bot = create_discord_bot(
                config=config,
                permission_checker=checker,
                scope_settings_store=None,
            )
            connection = SimpleNamespace(close=lambda: None)
            ctx = SimpleNamespace(
                author=SimpleNamespace(id=7),
                guild=SimpleNamespace(id=100),
                channel=SimpleNamespace(id=200),
                interaction=SimpleNamespace(),
                reply=AsyncMock(),
            )
            try:
                recommendation_command = bot.get_command("추천")
                ctx.command = recommendation_command
                with (
                    patch("pubg_ai.discord_bot.connect_mysql", return_value=connection),
                    patch(
                        "pubg_ai.discord_bot.PlayerRecommendationService.get_recommendations",
                        return_value=object(),
                    ) as get_recommendations,
                    patch(
                        "pubg_ai.discord_bot.format_player_recommendations",
                        return_value="추천 결과\n- 최소 표본 경기: 987\n- 추천 무기: M416",
                    ),
                ):
                    await recommendation_command.callback(
                        ctx,
                        "Yuuki_Asuna---",
                        "steam",
                        987,
                        7,
                    )

                get_recommendations.assert_called_once_with(
                    shard="steam",
                    account_id=None,
                    name="Yuuki_Asuna---",
                    guild_id="100",
                    global_scope=False,
                    limit=7,
                    min_matches=987,
                )
                reply_kwargs = ctx.reply.await_args.kwargs
                self.assertNotIn("content", reply_kwargs)
                self.assertEqual(reply_kwargs["embed"].title, "추천 결과")
                self.assertEqual(reply_kwargs["embed"].fields[0].name, "최소 표본 경기")

                ctx.reply.reset_mock()
                player = RegisteredPlayer(
                    id=1,
                    account_id="account.test",
                    shard="steam",
                    current_name="Yuuki_Asuna---",
                    active=True,
                    public_profile=True,
                )
                latest_match = PlayerCatalogMatch(
                    match_id="f4ae05ae-e027-4123-9cbb-ed862da93e9c",
                    created_at_kst=datetime(2026, 8, 25, 22, 10),
                    map_name="Tiger_Main",
                    game_mode="squad-fpp",
                    team_mode="squad",
                    perspective="fpp",
                    match_type="official",
                    season_state="progress",
                    win_place=3,
                    kills=5,
                    assists=1,
                    deaths=1,
                    dbnos_caused=4,
                    damage_dealt=712.4,
                )
                catalog = PlayerLookupCatalog(
                    player=player,
                    weapons=[],
                    matches=[latest_match],
                    facets={},
                )
                match_command = bot.get_command("매치")
                ctx.command = match_command
                with (
                    patch("pubg_ai.discord_bot.connect_mysql", return_value=connection),
                    patch.object(
                        PlayerStatsService,
                        "get_lookup_catalog",
                        return_value=catalog,
                    ),
                    patch.object(
                        PlayerStatsService,
                        "get_match_detail",
                        return_value=object(),
                    ) as get_match_detail,
                    patch(
                        "pubg_ai.discord_bot.format_player_match_detail",
                        return_value="최근 매치 상세\n- 결과: 3등\n- 맵: 태이고",
                    ),
                ):
                    await match_command.callback(ctx, "Yuuki_Asuna---", None, "steam")

                get_match_detail.assert_called_once_with(
                    shard="steam",
                    match_id=latest_match.match_id,
                    account_id=None,
                    name="Yuuki_Asuna---",
                    guild_id="100",
                    global_scope=False,
                )
                self.assertEqual(ctx.reply.await_args.kwargs["embed"].title, "최근 매치 상세")
            finally:
                await bot.close()


if __name__ == "__main__":
    unittest.main()
