from __future__ import annotations

from pathlib import Path
from typing import Any

from pubg_ai.config import RuntimeConfig
from pubg_ai.database import connect_mysql
from pubg_ai.discord_permissions import DiscordCommandIdentity, DiscordPermissionChecker
from pubg_ai.player_registry import DiscordCommandContext, PlayerRegistry, RegisteredPlayer
from pubg_ai.player_stats import PlayerProfileStats, PlayerStatsService
from pubg_ai.pubg_client import PubgApiClient, PubgApiError
from pubg_ai.replay_artifact_catalog import ReplayArtifactRecord, list_replay_artifacts
from pubg_ai.replay_storage import ReplayArtifactStore, ReplayStorageError


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


def create_discord_bot(
    *,
    config: RuntimeConfig,
    permission_checker: DiscordPermissionChecker,
    command_prefix: str = DEFAULT_DISCORD_PREFIX,
) -> Any:
    import discord
    from discord.ext import commands

    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix=command_prefix, intents=intents)

    def guild_id_for(ctx: Any) -> str | None:
        return str(ctx.guild.id) if ctx.guild else None

    def identity_for(ctx: Any) -> DiscordCommandIdentity:
        return DiscordCommandIdentity(user_id=str(ctx.author.id), guild_id=guild_id_for(ctx))

    def has_global_scope(ctx: Any) -> bool:
        return permission_checker.is_global_admin(identity_for(ctx))

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

    @bot.event
    async def on_ready() -> None:
        print(f"PUBG AI Discord bot logged in as {bot.user}")

    @bot.command(name="배그도움말", aliases=["pubg-help", "pubg-ai"])
    async def help_command(ctx: Any) -> None:
        await ctx.reply(
            "\n".join(
                [
                    "PUBG AI 명령어",
                    f"- `{command_prefix}유저등록 steam 닉네임`",
                    f"- `{command_prefix}유저조회 [닉네임] [shard]`",
                    f"- `{command_prefix}전적 닉네임 [shard]`",
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

    return bot


def run_discord_bot(
    *,
    config: RuntimeConfig,
    permission_checker: DiscordPermissionChecker,
    command_prefix: str = DEFAULT_DISCORD_PREFIX,
) -> None:
    if not config.secrets.discord_bot_token:
        raise RuntimeError("DISCORD_BOT_TOKEN is not configured in .env.")

    bot = create_discord_bot(
        config=config,
        permission_checker=permission_checker,
        command_prefix=command_prefix,
    )
    bot.run(config.secrets.discord_bot_token)


def _short_account_id(account_id: str) -> str:
    if not account_id:
        return "unknown"
    if account_id.startswith("account.") and len(account_id) > 20:
        return f"{account_id[:15]}...{account_id[-4:]}"
    return account_id


def _short_match_id(match_id: str) -> str:
    return match_id[:8] if len(match_id) > 8 else match_id


def _percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def _number(value: float, digits: int) -> str:
    return f"{value:.{digits}f}"


def _minutes(seconds: float) -> str:
    return f"{seconds / 60:.1f}분"


def _distance_km(meters: float) -> str:
    return f"{meters / 1000:.1f}km"


def _player_visible_to_scope(
    player: RegisteredPlayer,
    guild_id: str | None,
    global_scope: bool,
) -> bool:
    return global_scope or (guild_id is not None and player.registered_guild_id == guild_id)
