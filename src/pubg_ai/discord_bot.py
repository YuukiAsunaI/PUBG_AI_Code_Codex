from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from pubg_ai.config import RuntimeConfig
from pubg_ai.database import connect_mysql
from pubg_ai.discord_permissions import DiscordCommandIdentity, DiscordPermissionChecker
from pubg_ai.local_settings import LocalSettingsError, LocalSettingsStore
from pubg_ai.player_rankings import PlayerRanking, PlayerRankingService
from pubg_ai.player_recommendations import PlayerRecommendationReport, PlayerRecommendationService
from pubg_ai.player_registry import DiscordCommandContext, PlayerRegistry, RegisteredPlayer
from pubg_ai.player_stats import PlayerMatchDetail, PlayerProfileStats, PlayerStatsService, PlayerWeaponDetail
from pubg_ai.pubg_client import PubgApiClient, PubgApiError
from pubg_ai.replay_artifact_catalog import ReplayArtifactRecord, list_replay_artifacts
from pubg_ai.replay_storage import ReplayArtifactStore, ReplayStorageError
from pubg_ai.system_alerts import collect_system_alerts, format_alert_report, format_discord_alert
from pubg_ai.worker_run_history import get_latest_worker_run_id


DEFAULT_DISCORD_PREFIX = "!"


def format_player_list(players: list[RegisteredPlayer]) -> str:
    if not players:
        return "등록된 유저가 없습니다."

    lines = ["등록 유저"]
    for player in players:
        status = "수집중" if player.active else "중지"
        visibility = "공개" if player.public_profile else "비공개"
        lines.append(
            f"- {player.current_name} ({player.shard}) / {status} / {visibility} / {_short_account_id(player.account_id)}"
        )
    return "\n".join(lines)


def format_replay_artifact_summary(artifact: ReplayArtifactRecord) -> str:
    player = artifact.player_name or _short_account_id(artifact.account_id or "")
    match_id = artifact.match_id
    map_name = artifact.map_name or "unknown"
    mode = artifact.game_mode or "-"
    size_kb = artifact.size_bytes / 1024
    return (
        f"{player} 최근 2D 스냅샷\n"
        f"- match: {match_id}\n"
        f"- map/mode: {map_name} / {mode}\n"
        f"- size: {size_kb:.1f} KB"
    )


def format_player_profile_stats(profile: PlayerProfileStats) -> str:
    totals = profile.totals
    lines = [
        f"{profile.player.current_name} 전적 ({profile.player.shard})",
        f"- 경기/치킨: {totals.match_count}전 {totals.wins}치킨 ({_percent(totals.win_rate)})",
        f"- K/D/A: {totals.kills}/{totals.deaths}/{totals.assists} · KDA {_number(totals.kda, 2)}",
        f"- 평균 딜/받은 딜: {_number(totals.avg_damage_dealt, 1)} / {_number(totals.avg_damage_taken, 1)}",
        f"- 명중률/헤드샷 킬: {_percent(totals.accuracy)} / {totals.headshot_kills}",
        f"- 평균 생존/이동: {_minutes(totals.avg_survival_seconds)} / {_distance_km(totals.avg_movement_distance_m)}",
    ]

    if profile.top_weapons:
        weapons = [
            f"{weapon.weapon_name} {weapon.kills}킬 {_number(weapon.damage_dealt, 0)}딜"
            for weapon in profile.top_weapons[:3]
        ]
        lines.append(f"- 주무기: {', '.join(weapons)}")

    if profile.recent_matches:
        lines.append("최근 경기")
        for match in profile.recent_matches[:3]:
            rank = f"#{match.win_place}" if match.win_place is not None else "-"
            lines.append(
                f"- {_short_match_id(match.match_id)} {rank} "
                f"{match.kills}킬/{_number(match.damage_dealt, 0)}딜 "
                f"{match.map_name or '-'} {match.game_mode or '-'}"
            )

    if totals.match_count == 0:
        lines.append("아직 파싱된 전투 요약 데이터가 없습니다.")

    return "\n".join(lines)


def format_player_weapon_detail(detail: PlayerWeaponDetail) -> str:
    totals = detail.totals
    lines = [
        f"{detail.player.current_name} {detail.weapon_name} 무기 통계",
        f"- 사용 경기/치킨: {totals.match_count}전 {totals.wins}치킨 ({_percent(totals.win_rate)})",
        f"- 킬/어시/기절: {totals.kills}/{totals.assists}/{totals.dbnos}",
        f"- 딜/평균 딜: {_number(totals.damage_dealt, 0)} / {_number(totals.avg_damage_dealt, 1)}",
        f"- 명중률: {_percent(totals.accuracy)} ({totals.shots_hit}/{totals.shots_fired})",
        f"- 헤드샷 킬/기절: {totals.headshot_kills}/{totals.headshot_dbnos}",
    ]

    hit_parts = _top_parts(totals.hit_parts)
    if hit_parts:
        lines.append(f"- 맞춘 부위: {hit_parts}")

    if detail.recent_matches:
        lines.append("최근 사용 경기")
        for match in detail.recent_matches[:3]:
            rank = f"#{match.win_place}" if match.win_place is not None else "-"
            lines.append(
                f"- {_short_match_id(match.match_id)} {rank} "
                f"{match.kills}킬/{match.dbnos}기절/{_number(match.damage_dealt, 0)}딜 "
                f"{_percent(match.accuracy)}"
            )

    return "\n".join(lines)


def format_player_match_detail(detail: PlayerMatchDetail) -> str:
    rank = f"#{detail.win_place}" if detail.win_place is not None else "-"
    total_players = _optional_number(detail.total_players)
    human_players = _optional_number(detail.human_players)
    bot_players = _optional_number(detail.bot_players)
    result = "치킨" if detail.is_chicken else "치킨 아님"
    lines = [
        f"{detail.player.current_name} 매치 상세 ({detail.shard})",
        f"- Match: {detail.match_id}",
        f"- 맵/모드: {detail.map_name or '-'} / {detail.game_mode or '-'} / {detail.match_type or '-'}",
        f"- 결과/등수: {result} / {rank}",
        f"- 인원: 총 {total_players}명, 사람 {human_players}명, 봇 {bot_players}명",
        f"- K/D/A/기절: {detail.kills}/{detail.deaths}/{detail.assists}/{detail.dbnos_caused}"
        f" (당한 기절 {detail.dbnos_taken})",
        f"- 딜/받은 딜: {_number(detail.damage_dealt, 1)} / {_number(detail.damage_taken, 1)}",
        f"- 발사/명중/명중률: {detail.shots_fired}/{detail.shots_hit}/{_percent(detail.accuracy)}",
        f"- 헤드샷 킬/기절: {detail.headshot_kills}/{detail.headshot_dbnos_caused}",
        f"- 생존/이동/낙하: {_optional_minutes(detail.survival_seconds)} / "
        f"{_optional_distance_km(detail.movement_distance_m)} / {_optional_distance_m(detail.landing_distance_m)}",
    ]

    if detail.weapons:
        weapon_lines = []
        for weapon in detail.weapons[:4]:
            weapon_lines.append(
                f"{weapon.weapon_name} {weapon.kills}킬/{weapon.dbnos}기절/"
                f"{_number(weapon.damage_dealt, 0)}딜/{_percent(weapon.accuracy)}"
            )
        lines.append(f"- 사용 무기: {', '.join(weapon_lines)}")

    hit_parts = _top_parts(detail.hit_parts)
    if hit_parts:
        lines.append(f"- 맞춘 부위: {hit_parts}")

    if detail.replay_artifact:
        lines.append(f"- 2D 스냅샷: 생성됨 (`!최근스냅샷 {detail.match_id}`)")

    return "\n".join(lines)


def format_player_ranking(ranking: PlayerRanking) -> str:
    scope = "전체" if ranking.global_scope else f"서버 {ranking.guild_id}"
    lines = [f"{ranking.metric_label} 랭킹 ({ranking.shard}, {scope})"]
    if not ranking.rows:
        lines.append("- 랭킹 데이터가 없습니다.")
        return "\n".join(lines)

    for row in ranking.rows:
        lines.append(
            f"- #{row.rank} {row.player.current_name}: {_ranking_score(ranking.metric, row.score)} "
            f"({row.match_count}전 {row.wins}치킨, {row.kills}K/{row.deaths}D/{row.assists}A, "
            f"평딜 {_number(row.avg_damage_dealt, 1)})"
        )
    return "\n".join(lines)


def format_player_recommendations(
    report: PlayerRecommendationReport,
    *,
    evidence_base_url: str | None = None,
) -> str:
    lines = [
        f"{report.player.current_name} recommendations ({report.player.shard})",
        f"- min matches: {report.min_matches}",
    ]
    if report.weapons:
        lines.append("- weapons: " + ", ".join(
            f"{item.weapon_name} score {_number(item.score, 1)} "
            f"({_number(item.avg_damage_dealt, 1)} dmg, {_percent(item.win_rate)} win)"
            for item in report.weapons[:3]
        ))
    else:
        lines.append("- weapons: no data")

    if report.weapon_attachments:
        lines.append("- weapon parts: " + ", ".join(
            f"{item.weapon_name} + {item.attachment_name} "
            f"({_number(item.avg_damage_dealt, 1)} dmg, {_percent(item.win_rate)} win)"
            f"{_recommendation_evidence_link(report, item, evidence_base_url)}"
            for item in report.weapon_attachments[:3]
        ))
    else:
        lines.append("- weapon parts: no data")

    if report.weapon_ranges:
        lines.append("- weapon ranges: " + ", ".join(
            f"{item.weapon_name} {item.bucket_label} "
            f"({item.kills}K/{item.dbnos}DBNO)"
            for item in report.weapon_ranges[:3]
        ))
    else:
        lines.append("- weapon ranges: no data")

    if report.attachments:
        lines.append("- attachments: " + ", ".join(
            f"{item.item_name} ({item.attached_events} attach)"
            for item in report.attachments[:3]
        ))
    else:
        lines.append("- attachments: no data")

    if report.maps:
        lines.append("- maps: " + ", ".join(
            f"{item.map_name_ko} {_percent(item.win_rate)} win"
            for item in report.maps[:3]
        ))
    else:
        lines.append("- maps: no data")

    if report.teammates:
        lines.append("- teammates: " + ", ".join(
            f"{item.name}{' registered' if item.registered else ''} {_percent(item.win_rate)} win"
            for item in report.teammates[:3]
        ))
    else:
        lines.append("- teammates: no data")

    if report.drop_zones:
        lines.append("- drop zones: " + ", ".join(
            f"{item.map_name_ko} grid {item.grid_x},{item.grid_y} {_percent(item.win_rate)} win"
            for item in report.drop_zones[:3]
        ))
    else:
        lines.append("- drop zones: no data")

    return "\n".join(lines)


def create_discord_bot(
    *,
    config: RuntimeConfig,
    permission_checker: DiscordPermissionChecker,
    scope_settings_store: LocalSettingsStore | None = None,
    command_prefix: str = DEFAULT_DISCORD_PREFIX,
) -> Any:
    import discord
    from discord.ext import commands

    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix=command_prefix, intents=intents)
    alert_task_started = False
    alert_last_worker_run_id: int | None = None
    sent_storage_alert_keys: set[str] = set()

    def guild_id_for(ctx: Any) -> str | None:
        return str(ctx.guild.id) if ctx.guild else None

    def identity_for(ctx: Any) -> DiscordCommandIdentity:
        return DiscordCommandIdentity(user_id=str(ctx.author.id), guild_id=guild_id_for(ctx))

    def has_global_scope(ctx: Any) -> bool:
        return permission_checker.is_global_admin(identity_for(ctx))

    def guild_ranking_scope(ctx: Any) -> str:
        guild_id = guild_id_for(ctx)
        if guild_id is None or scope_settings_store is None:
            return "guild"
        try:
            settings = scope_settings_store.load_discord_scope_settings()
        except LocalSettingsError:
            return "guild"
        return settings.guild_ranking_scopes.get(guild_id, "guild")

    def public_profile_default() -> bool:
        if scope_settings_store is None:
            return True
        try:
            settings = scope_settings_store.load_discord_scope_settings()
        except LocalSettingsError:
            return True
        return settings.public_profile_default

    async def require_permission(ctx: Any, command_group: str) -> bool:
        if permission_checker.is_allowed(identity_for(ctx), command_group):
            return True
        await ctx.reply("이 명령어를 사용할 권한이 없습니다.", mention_author=False)
        return False

    async def require_scoped_guild(ctx: Any) -> str | None:
        guild_id = guild_id_for(ctx)
        if has_global_scope(ctx) or guild_id:
            return guild_id
        await ctx.reply("이 명령어는 디스코드 서버 채널에서 사용해 주세요.", mention_author=False)
        return None

    async def send_alert_to_channel(channel_id: str, message: str) -> bool:
        try:
            numeric_channel_id = int(channel_id)
        except ValueError:
            return False

        try:
            channel = bot.get_channel(numeric_channel_id)
            if channel is None:
                channel = await bot.fetch_channel(numeric_channel_id)
            await channel.send(message)
            return True
        except Exception as exc:
            print(f"failed to send PUBG AI alert to Discord channel {channel_id}: {exc}")
            return False

    async def dispatch_alerts_once() -> None:
        nonlocal alert_last_worker_run_id
        if scope_settings_store is None:
            return

        try:
            alert_settings = scope_settings_store.load_alert_settings()
        except LocalSettingsError as exc:
            print(f"failed to load PUBG AI alert settings: {exc}")
            return

        if not alert_settings.discord_channel_ids:
            return

        connection = connect_mysql(config.database)
        try:
            if alert_last_worker_run_id is None:
                alert_last_worker_run_id = get_latest_worker_run_id(connection)
            report = collect_system_alerts(
                config=config,
                connection=connection,
                settings=alert_settings,
                after_worker_run_id=alert_last_worker_run_id,
            )
        finally:
            connection.close()

        sent_worker_alert = False
        worker_alert_count = 0
        for alert in report.alerts:
            if alert.source == "storage" and alert.key in sent_storage_alert_keys:
                continue
            if alert.source == "worker":
                worker_alert_count += 1

            sent_alert = False
            for channel_id in alert_settings.discord_channel_ids or []:
                sent_alert = await send_alert_to_channel(channel_id, format_discord_alert(alert)) or sent_alert

            if sent_alert:
                if alert.source == "storage":
                    sent_storage_alert_keys.add(alert.key)
                if alert.source == "worker":
                    sent_worker_alert = True

        if worker_alert_count == 0 or sent_worker_alert:
            alert_last_worker_run_id = report.latest_worker_run_id

    async def alert_loop() -> None:
        await bot.wait_until_ready()
        while not bot.is_closed():
            try:
                await dispatch_alerts_once()
            except Exception as exc:
                print(f"PUBG AI alert loop failed: {exc}")
            await asyncio.sleep(60)

    @bot.event
    async def on_ready() -> None:
        nonlocal alert_task_started
        print(f"PUBG AI Discord bot logged in as {bot.user}")
        if scope_settings_store is not None and not alert_task_started:
            alert_task_started = True
            bot.loop.create_task(alert_loop())

    @bot.command(name="배그도움말", aliases=["pubg-help", "pubg-ai"])
    async def help_command(ctx: Any) -> None:
        await ctx.reply(
            "\n".join(
                [
                    "PUBG AI 명령어",
                    f"- `{command_prefix}유저등록 steam 닉네임`",
                    f"- `{command_prefix}유저조회 [닉네임] [shard]`",
                    f"- `{command_prefix}전적 닉네임 [shard]`",
                    f"- `{command_prefix}무기 닉네임 무기명 [shard]`",
                    f"- `{command_prefix}추천 닉네임 [shard]`",
                    f"- `{command_prefix}매치 match_id [닉네임|accountId] [shard]`",
                    f"- `{command_prefix}랭킹 [지표] [shard] [limit] [전체]`",
                    f"- `{command_prefix}최근스냅샷 [match_id]`",
                    f"- `{command_prefix}유저삭제 steam 닉네임또는accountId`",
                ]
            ),
            mention_author=False,
        )

    @bot.command(name="유저조회", aliases=["pubg-profile"])
    async def list_players_command(ctx: Any, name: str | None = None, shard: str = "steam") -> None:
        if not await require_permission(ctx, "profile_read"):
            return
        guild_id = await require_scoped_guild(ctx)
        if guild_id is None and not has_global_scope(ctx):
            return
        global_scope = has_global_scope(ctx)

        connection = connect_mysql(config.database)
        try:
            registry = PlayerRegistry(connection)
            if name:
                player = registry.get_player(shard=shard, name=name, include_inactive=True)
                players = [player] if player and _player_visible_to_scope(player, guild_id, global_scope) else []
            else:
                players = registry.list_players(
                    active_only=False,
                    registered_guild_id=None if global_scope else guild_id,
                    limit=20,
                )
        finally:
            connection.close()

        await ctx.reply(format_player_list(players), mention_author=False)

    @bot.command(name="전적", aliases=["pubg-stats"])
    async def player_stats_command(ctx: Any, name: str | None = None, shard: str = "steam") -> None:
        if not await require_permission(ctx, "profile_read"):
            return
        if not name:
            await ctx.reply(f"사용법: `{command_prefix}전적 닉네임 [shard]`", mention_author=False)
            return

        guild_id = await require_scoped_guild(ctx)
        if guild_id is None and not has_global_scope(ctx):
            return
        global_scope = has_global_scope(ctx)

        connection = connect_mysql(config.database)
        try:
            profile = PlayerStatsService(connection).get_profile(
                shard=shard,
                account_id=name if name.startswith("account.") else None,
                name=None if name.startswith("account.") else name,
                guild_id=None if global_scope else guild_id,
                global_scope=global_scope,
            )
        finally:
            connection.close()

        if profile is None:
            await ctx.reply("조회 가능한 등록 유저를 찾지 못했습니다.", mention_author=False)
            return

        await ctx.reply(format_player_profile_stats(profile), mention_author=False)

    @bot.command(name="무기", aliases=["pubg-weapon"])
    async def player_weapon_command(
        ctx: Any,
        name: str | None = None,
        weapon: str | None = None,
        shard: str = "steam",
    ) -> None:
        if not await require_permission(ctx, "profile_read"):
            return
        if not name or not weapon:
            await ctx.reply(f"사용법: `{command_prefix}무기 닉네임 무기명 [shard]`", mention_author=False)
            return

        guild_id = await require_scoped_guild(ctx)
        if guild_id is None and not has_global_scope(ctx):
            return
        global_scope = has_global_scope(ctx)

        connection = connect_mysql(config.database)
        try:
            detail = PlayerStatsService(connection).get_weapon_detail(
                shard=shard,
                account_id=name if name.startswith("account.") else None,
                name=None if name.startswith("account.") else name,
                weapon=weapon,
                guild_id=None if global_scope else guild_id,
                global_scope=global_scope,
            )
        finally:
            connection.close()

        if detail is None:
            await ctx.reply("조회 가능한 무기 통계를 찾지 못했습니다.", mention_author=False)
            return

        await ctx.reply(format_player_weapon_detail(detail), mention_author=False)

    @bot.command(name="추천", aliases=["pubg-recommend"])
    async def player_recommendations_command(
        ctx: Any,
        name: str | None = None,
        shard: str = "steam",
    ) -> None:
        if not await require_permission(ctx, "profile_read"):
            return
        if not name:
            await ctx.reply(f"사용법: `{command_prefix}추천 닉네임 [shard]`", mention_author=False)
            return

        guild_id = await require_scoped_guild(ctx)
        if guild_id is None and not has_global_scope(ctx):
            return
        global_scope = has_global_scope(ctx)

        connection = connect_mysql(config.database)
        try:
            recommendations = PlayerRecommendationService(connection).get_recommendations(
                shard=shard,
                account_id=name if name.startswith("account.") else None,
                name=None if name.startswith("account.") else name,
                guild_id=None if global_scope else guild_id,
                global_scope=global_scope,
            )
        finally:
            connection.close()

        if recommendations is None:
            await ctx.reply("조회 가능한 추천 데이터를 찾지 못했습니다.", mention_author=False)
            return

        await ctx.reply(
            format_player_recommendations(
                recommendations,
                evidence_base_url=config.app.local_web_base_url,
            ),
            mention_author=False,
        )

    @bot.command(name="매치", aliases=["pubg-match"])
    async def player_match_command(
        ctx: Any,
        match_id: str | None = None,
        name: str | None = None,
        shard: str = "steam",
    ) -> None:
        if not await require_permission(ctx, "profile_read"):
            return
        if not match_id:
            await ctx.reply(
                f"사용법: `{command_prefix}매치 match_id [닉네임|accountId] [shard]`",
                mention_author=False,
            )
            return

        if name and shard == "steam" and name.lower() in {"steam", "kakao", "psn", "xbox", "console"}:
            shard = name
            name = None

        guild_id = await require_scoped_guild(ctx)
        if guild_id is None and not has_global_scope(ctx):
            return
        global_scope = has_global_scope(ctx)

        connection = connect_mysql(config.database)
        try:
            detail = PlayerStatsService(connection).get_match_detail(
                shard=shard,
                match_id=match_id,
                account_id=name if name and name.startswith("account.") else None,
                name=None if not name or name.startswith("account.") else name,
                guild_id=None if global_scope else guild_id,
                global_scope=global_scope,
            )
        finally:
            connection.close()

        if detail is None:
            await ctx.reply("조회 가능한 등록 유저의 매치 상세를 찾지 못했습니다.", mention_author=False)
            return

        await ctx.reply(format_player_match_detail(detail), mention_author=False)

    @bot.command(name="랭킹", aliases=["pubg-ranking"])
    async def ranking_command(
        ctx: Any,
        metric: str = "kda",
        shard_or_limit: str = "steam",
        limit_or_scope: str | None = None,
        scope: str | None = None,
    ) -> None:
        if not await require_permission(ctx, "ranking_read"):
            return

        parsed_metric, shard, limit, global_requested = _parse_ranking_args(
            metric,
            shard_or_limit,
            limit_or_scope,
            scope,
        )
        guild_id = await require_scoped_guild(ctx)
        if guild_id is None and not has_global_scope(ctx):
            return
        if global_requested and not has_global_scope(ctx):
            await ctx.reply("전체 랭킹은 글로벌 관리자만 조회할 수 있습니다.", mention_author=False)
            return

        global_scope = (
            global_requested
            or (guild_id is None and has_global_scope(ctx))
            or (guild_id is not None and guild_ranking_scope(ctx) == "global")
        )
        ranking_guild_id = None if global_scope else guild_id

        connection = connect_mysql(config.database)
        try:
            ranking = PlayerRankingService(connection).get_player_ranking(
                shard=shard,
                metric=parsed_metric,
                guild_id=ranking_guild_id,
                global_scope=global_scope,
                limit=limit,
            )
        finally:
            connection.close()

        await ctx.reply(format_player_ranking(ranking), mention_author=False)

    @bot.command(name="유저등록", aliases=["pubg-register"])
    async def register_player_command(ctx: Any, shard: str, nickname: str) -> None:
        if not await require_permission(ctx, "register"):
            return
        if not config.secrets.pubg_api_key:
            await ctx.reply("PUBG_API_KEY가 설정되어 있지 않습니다.", mention_author=False)
            return

        connection = connect_mysql(config.database)
        try:
            try:
                player = PlayerRegistry(connection).register_player_by_name(
                    pubg_client=PubgApiClient(config.secrets.pubg_api_key),
                    shard=shard,
                    player_name=nickname,
                    public_profile=public_profile_default(),
                    context=DiscordCommandContext(
                        user_id=str(ctx.author.id),
                        guild_id=str(ctx.guild.id) if ctx.guild else None,
                        channel_id=str(ctx.channel.id) if ctx.channel else None,
                    ),
                )
            except PubgApiError as exc:
                await ctx.reply(f"PUBG API 조회 실패: {exc}", mention_author=False)
                return
        finally:
            connection.close()

        await ctx.reply(f"등록 완료: {player.current_name} ({player.shard})", mention_author=False)

    @bot.command(name="유저삭제", aliases=["pubg-unregister"])
    async def unregister_player_command(ctx: Any, shard: str, target: str) -> None:
        if not await require_permission(ctx, "admin"):
            return
        guild_id = await require_scoped_guild(ctx)
        if guild_id is None and not has_global_scope(ctx):
            return
        global_scope = has_global_scope(ctx)

        connection = connect_mysql(config.database)
        try:
            registry = PlayerRegistry(connection)
            existing = registry.get_player(
                shard=shard,
                account_id=target if target.startswith("account.") else None,
                name=None if target.startswith("account.") else target,
                include_inactive=True,
            )
            if not existing or not _player_visible_to_scope(existing, guild_id, global_scope):
                player = None
            else:
                player = registry.unregister_player(
                    shard=shard,
                    account_id=existing.account_id,
                )
        finally:
            connection.close()

        if player is None:
            await ctx.reply("대상 유저를 찾지 못했습니다.", mention_author=False)
        else:
            await ctx.reply(f"수집 중지 완료: {player.current_name} ({player.shard})", mention_author=False)

    @bot.command(name="최근스냅샷", aliases=["pubg-replay"])
    async def latest_snapshot_command(ctx: Any, match_id: str | None = None) -> None:
        if not await require_permission(ctx, "replay_read"):
            return
        guild_id = await require_scoped_guild(ctx)
        if guild_id is None and not has_global_scope(ctx):
            return

        connection = connect_mysql(config.database)
        try:
            artifacts = list_replay_artifacts(
                connection,
                limit=1,
                artifact_type="map_snapshot",
                match_id=match_id,
                registered_guild_id=None if has_global_scope(ctx) else guild_id,
            )
        finally:
            connection.close()

        if not artifacts:
            await ctx.reply("생성된 2D 스냅샷이 없습니다.", mention_author=False)
            return

        artifact = artifacts[0]
        store = ReplayArtifactStore(config.app.replay_data_dir)
        try:
            path = store.resolve_path(artifact.relative_path)
        except ReplayStorageError:
            await ctx.reply("스냅샷 파일 경로가 올바르지 않습니다.", mention_author=False)
            return

        if not path.is_file():
            await ctx.reply("스냅샷 파일을 찾지 못했습니다.", mention_author=False)
            return

        await ctx.reply(
            format_replay_artifact_summary(artifact),
            file=discord.File(Path(path), filename=path.name),
            mention_author=False,
        )

    @bot.command(name="pubg-alerts")
    async def alerts_command(ctx: Any) -> None:
        if not await require_permission(ctx, "admin"):
            return
        if scope_settings_store is None:
            await ctx.reply("PUBG AI alert settings are unavailable.", mention_author=False)
            return

        try:
            alert_settings = scope_settings_store.load_alert_settings()
        except LocalSettingsError as exc:
            await ctx.reply(f"PUBG AI alert settings error: {exc}", mention_author=False)
            return

        connection = connect_mysql(config.database)
        try:
            report = collect_system_alerts(
                config=config,
                connection=connection,
                settings=alert_settings,
                after_worker_run_id=None,
            )
        finally:
            connection.close()

        await ctx.reply(format_alert_report(report.alerts), mention_author=False)

    return bot


def run_discord_bot(
    *,
    config: RuntimeConfig,
    permission_checker: DiscordPermissionChecker,
    scope_settings_store: LocalSettingsStore | None = None,
    command_prefix: str = DEFAULT_DISCORD_PREFIX,
) -> None:
    if not config.secrets.discord_bot_token:
        raise RuntimeError("DISCORD_BOT_TOKEN is not configured in .env.")

    bot = create_discord_bot(
        config=config,
        permission_checker=permission_checker,
        scope_settings_store=scope_settings_store,
        command_prefix=command_prefix,
    )
    bot.run(config.secrets.discord_bot_token)


def _short_account_id(account_id: str) -> str:
    if not account_id:
        return "unknown"
    if account_id.startswith("account.") and len(account_id) > 20:
        return f"{account_id[:15]}...{account_id[-4:]}"
    return account_id


def _recommendation_evidence_link(
    report: PlayerRecommendationReport,
    item: Any,
    base_url: str | None,
) -> str:
    if not base_url:
        return ""
    query = urlencode(
        {
            "shard": report.player.shard,
            "account_id": report.player.account_id,
            "weapon_code": item.weapon_code,
            "attachment_code": item.attachment_code,
        }
    )
    return f" [evidence]({base_url.rstrip('/')}/players/recommendations/weapon-attachment-evidence?{query})"


def _short_match_id(match_id: str) -> str:
    return match_id[:8] if len(match_id) > 8 else match_id


def _percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def _number(value: float, digits: int) -> str:
    return f"{value:.{digits}f}"


def _minutes(seconds: float) -> str:
    return f"{seconds / 60:.1f}분"


def _optional_minutes(seconds: float | None) -> str:
    return _minutes(seconds) if seconds is not None else "-"


def _distance_km(meters: float) -> str:
    return f"{meters / 1000:.1f}km"


def _optional_distance_km(meters: float | None) -> str:
    return _distance_km(meters) if meters is not None else "-"


def _optional_distance_m(meters: float | None) -> str:
    return f"{meters:.0f}m" if meters is not None else "-"


def _optional_number(value: int | None) -> str:
    return str(value) if value is not None else "-"


def _ranking_score(metric: str, score: float) -> str:
    if metric in {"win_rate", "accuracy", "headshot_rate"}:
        return _percent(score)
    if metric in {"kda", "avg_damage"}:
        return _number(score, 2)
    return _number(score, 0)


def _parse_ranking_args(
    metric: str,
    shard_or_limit: str,
    limit_or_scope: str | None,
    scope: str | None,
) -> tuple[str, str, int, bool]:
    metric_value = metric or "kda"
    shard = "steam"
    limit = 10
    global_requested = False

    if _is_scope_token(metric_value):
        metric_value = "kda"
        global_requested = True
    elif _is_shard_token(metric_value):
        shard = metric_value.lower()
        metric_value = "kda"
    elif _is_int_token(metric_value):
        limit = _ranking_limit(metric_value)
        metric_value = "kda"

    for token in [shard_or_limit, limit_or_scope, scope]:
        if not token:
            continue
        if _is_scope_token(token):
            global_requested = True
        elif _is_shard_token(token):
            shard = token.lower()
        elif _is_int_token(token):
            limit = _ranking_limit(token)

    return metric_value, shard, limit, global_requested


def _is_scope_token(value: str) -> bool:
    return value.lower() in {"전체", "global", "all"}


def _is_shard_token(value: str) -> bool:
    return value.lower() in {"steam", "kakao", "psn", "xbox", "console"}


def _is_int_token(value: str) -> bool:
    try:
        int(value)
    except ValueError:
        return False
    return True


def _ranking_limit(value: str) -> int:
    return max(1, min(int(value), 20))


def _top_parts(parts: dict[str, int]) -> str:
    if not parts:
        return ""
    ordered = sorted(parts.items(), key=lambda item: (-item[1], item[0]))
    return ", ".join(f"{_part_label(key)} {value}" for key, value in ordered[:4])


def _part_label(value: str) -> str:
    return {
        "head": "머리",
        "torso": "몸통",
        "pelvis": "골반",
        "arm": "팔",
        "leg": "다리",
    }.get(value, value)


def _player_visible_to_scope(
    player: RegisteredPlayer,
    guild_id: str | None,
    global_scope: bool,
) -> bool:
    return global_scope or (guild_id is not None and player.registered_guild_id == guild_id)
