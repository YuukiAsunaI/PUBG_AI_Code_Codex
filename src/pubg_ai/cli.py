from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from time import sleep
from typing import Any, Callable

from pubg_ai.collector_worker import CollectorWorkerOptions, run_collector_cycle
from pubg_ai.config import RuntimeConfig, load_dotenv_values
from pubg_ai.database import connect_mysql, count_tables, initialize_database
from pubg_ai.discord_bot import DEFAULT_DISCORD_PREFIX, run_discord_bot
from pubg_ai.discord_permission_manager import DiscordPermissionManager
from pubg_ai.discord_permissions import DiscordPermissionChecker
from pubg_ai.local_settings import LocalSettingsError, LocalSettingsStore
from pubg_ai.loadout_snapshot_processor import LoadoutSnapshotProcessor
from pubg_ai.map_snapshot_renderer import MapSnapshotProcessor
from pubg_ai.post_processing_worker import PostProcessingWorkerOptions, run_post_processing_cycle
from pubg_ai.match_collection import RegisteredPlayerMatchCollector
from pubg_ai.match_job_processor import MatchJobProcessor
from pubg_ai.player_rankings import PlayerRankingService
from pubg_ai.player_recommendations import PlayerRecommendationService
from pubg_ai.player_registry import PlayerRegistry
from pubg_ai.player_stats import PlayerStatsService
from pubg_ai.pubg_client import PubgApiClient
from pubg_ai.raw_storage import RawPayloadStore
from pubg_ai.replay_storage import ReplayArtifactStore
from pubg_ai.replay_timeline_builder import ReplayTimelineProcessor
from pubg_ai.telemetry_combat_processor import TelemetryCombatProcessor
from pubg_ai.telemetry_item_processor import TelemetryItemProcessor
from pubg_ai.telemetry_job_processor import TelemetryJobProcessor
from pubg_ai.telemetry_movement_processor import TelemetryMovementProcessor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pubg-ai")
    parser.add_argument("--base-dir", default=".", help="Project base directory. Defaults to current directory.")
    parser.add_argument("--env-file", default=".env", help="dotenv file path relative to base-dir.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("config-status", help="Print safe runtime configuration status.")
    subparsers.add_parser("init-db", help="Create the MySQL database and MVP schema tables.")
    subparsers.add_parser("db-status", help="Check MySQL connection and table count.")

    lookup_parser = subparsers.add_parser("lookup-player", help="Resolve PUBG nickname to accountId.")
    lookup_parser.add_argument("nickname")
    lookup_parser.add_argument("--shard", default="steam")

    register_parser = subparsers.add_parser("register-player", help="Resolve and register a PUBG player.")
    register_parser.add_argument("nickname")
    register_parser.add_argument("--shard", default="steam")
    register_parser.add_argument("--private", action="store_true", help="Register with public_profile disabled.")

    stats_parser = subparsers.add_parser("player-stats", help="Print registered player profile statistics.")
    stats_parser.add_argument("target", help="Registered nickname or accountId.")
    stats_parser.add_argument("--shard", default="steam")

    weapon_stats_parser = subparsers.add_parser(
        "player-weapon-stats",
        help="Print registered player weapon statistics.",
    )
    weapon_stats_parser.add_argument("target", help="Registered nickname or accountId.")
    weapon_stats_parser.add_argument("weapon", help="Weapon code or common weapon name, for example M416.")
    weapon_stats_parser.add_argument("--shard", default="steam")

    recommendations_parser = subparsers.add_parser(
        "player-recommendations",
        help="Print registered player weapon, attachment, map, teammate, and drop recommendations.",
    )
    recommendations_parser.add_argument("target", help="Registered nickname or accountId.")
    recommendations_parser.add_argument("--shard", default="steam")
    recommendations_parser.add_argument("--limit", default=5, type=int)
    recommendations_parser.add_argument("--min-matches", default=1, type=int)

    recommendation_evidence_parser = subparsers.add_parser(
        "player-recommendation-evidence",
        help="Print supporting combat snapshots for one weapon + attachment recommendation.",
    )
    recommendation_evidence_parser.add_argument("target", help="Registered nickname or accountId.")
    recommendation_evidence_parser.add_argument("weapon", help="Weapon code, for example WeapHK416_C.")
    recommendation_evidence_parser.add_argument("attachment", help="Attachment code, for example Item_Attach_...")
    recommendation_evidence_parser.add_argument("--shard", default="steam")
    recommendation_evidence_parser.add_argument("--limit", default=20, type=int)

    match_stats_parser = subparsers.add_parser(
        "player-match-stats",
        help="Print one parsed match detail for a registered player.",
    )
    match_stats_parser.add_argument("match_id")
    match_stats_parser.add_argument(
        "target",
        nargs="?",
        help="Registered nickname or accountId. If omitted, the first registered participant is used.",
    )
    match_stats_parser.add_argument("--shard", default="steam")

    ranking_parser = subparsers.add_parser("player-ranking", help="Print registered player rankings.")
    ranking_parser.add_argument("--metric", default="kda", help="kda, 승률, 평딜, 딜, 킬, 경기, 명중률, 헤드샷, 기절")
    ranking_parser.add_argument("--shard", default="steam")
    ranking_parser.add_argument("--guild-id", default=None, help="Limit ranking to one Discord guild scope.")
    ranking_parser.add_argument("--limit", default=10, type=int)
    ranking_parser.add_argument("--min-matches", default=1, type=int)
    ranking_parser.add_argument("--include-inactive", action="store_true")

    collect_parser = subparsers.add_parser("collect-matches", help="Refresh registered players and queue match jobs.")
    collect_parser.add_argument("--shard", default=None)
    collect_parser.add_argument("--limit", default=None, type=int)

    jobs_parser = subparsers.add_parser("match-jobs", help="List queued match fetch jobs.")
    jobs_parser.add_argument("--limit", default=50, type=int)

    process_jobs_parser = subparsers.add_parser(
        "process-match-jobs",
        help="Fetch queued match details, store raw JSON, and queue telemetry jobs.",
    )
    process_jobs_parser.add_argument("--limit", default=10, type=int)

    telemetry_jobs_parser = subparsers.add_parser("telemetry-jobs", help="List queued telemetry fetch jobs.")
    telemetry_jobs_parser.add_argument("--limit", default=50, type=int)

    process_telemetry_jobs_parser = subparsers.add_parser(
        "process-telemetry-jobs",
        help="Download queued telemetry JSON files and store raw payload metadata.",
    )
    process_telemetry_jobs_parser.add_argument("--limit", default=5, type=int)

    parse_combat_parser = subparsers.add_parser(
        "parse-telemetry-combat",
        help="Parse raw telemetry files into registered-player combat summary tables.",
    )
    parse_combat_parser.add_argument("--limit", default=10, type=int)
    parse_combat_parser.add_argument("--force", action="store_true", help="Reparse already summarized matches.")

    parse_items_parser = subparsers.add_parser(
        "parse-telemetry-items",
        help="Parse raw telemetry files into registered-player item event and summary tables.",
    )
    parse_items_parser.add_argument("--limit", default=10, type=int)
    parse_items_parser.add_argument("--force", action="store_true", help="Reparse already summarized item events.")

    parse_movement_parser = subparsers.add_parser(
        "parse-telemetry-movement",
        help="Parse raw telemetry files into registered-player movement and location tables.",
    )
    parse_movement_parser.add_argument("--limit", default=10, type=int)
    parse_movement_parser.add_argument("--force", action="store_true", help="Reparse already summarized movement rows.")

    map_snapshots_parser = subparsers.add_parser(
        "generate-map-snapshots",
        help="Generate registered-player 2D route JPEG snapshots under PUBG_REPLAY_DATA_DIR.",
    )
    map_snapshots_parser.add_argument("--limit", default=10, type=int)
    map_snapshots_parser.add_argument("--force", action="store_true", help="Regenerate existing map snapshot artifacts.")

    replay_timelines_parser = subparsers.add_parser(
        "generate-replay-timelines",
        help="Generate registered-player 2D replay timeline JSON artifacts under PUBG_REPLAY_DATA_DIR.",
    )
    replay_timelines_parser.add_argument("--limit", default=10, type=int)
    replay_timelines_parser.add_argument("--force", action="store_true", help="Regenerate existing timeline artifacts.")

    loadout_snapshots_parser = subparsers.add_parser(
        "generate-loadout-snapshots",
        help="Reconstruct weapon attachment loadout snapshots for kill/DBNO combat events.",
    )
    loadout_snapshots_parser.add_argument("--limit", default=10, type=int)
    loadout_snapshots_parser.add_argument("--force", action="store_true", help="Regenerate existing loadout snapshots.")

    web_parser = subparsers.add_parser("run-web", help="Run the local management web app.")
    web_parser.add_argument("--host", default="127.0.0.1", help="Bind host. Defaults to localhost only.")
    web_parser.add_argument("--port", default=8000, type=int, help="Bind port.")

    collector_parser = subparsers.add_parser(
        "run-collector",
        help="Run the automatic completed-match collector loop.",
    )
    collector_parser.add_argument("--shard", default=None, help="Optional shard filter, for example steam.")
    collector_parser.add_argument("--match-job-limit", default=10, type=int)
    collector_parser.add_argument("--telemetry-job-limit", default=5, type=int)
    collector_parser.add_argument("--once", action="store_true", help="Run one collector cycle and exit.")

    post_process_parser = subparsers.add_parser(
        "run-post-processing",
        help="Run the automatic telemetry parser and replay artifact worker loop.",
    )
    post_process_parser.add_argument("--combat-limit", default=10, type=int)
    post_process_parser.add_argument("--item-limit", default=10, type=int)
    post_process_parser.add_argument("--movement-limit", default=10, type=int)
    post_process_parser.add_argument("--loadout-limit", default=50, type=int)
    post_process_parser.add_argument("--map-snapshot-limit", default=10, type=int)
    post_process_parser.add_argument("--timeline-limit", default=10, type=int)
    post_process_parser.add_argument("--force", action="store_true")
    post_process_parser.add_argument("--once", action="store_true", help="Run one post-processing cycle and exit.")

    discord_parser = subparsers.add_parser("run-discord-bot", help="Run the Discord bot.")
    discord_parser.add_argument("--prefix", default=DEFAULT_DISCORD_PREFIX, help="Text command prefix.")

    subparsers.add_parser("discord-permissions", help="Print Discord permission settings.")

    grant_parser = subparsers.add_parser("grant-discord-permission", help="Grant a Discord command group.")
    grant_parser.add_argument("user_id")
    grant_parser.add_argument("group")
    grant_parser.add_argument("--guild-id", default=None)

    revoke_parser = subparsers.add_parser("revoke-discord-permission", help="Revoke a Discord command group.")
    revoke_parser.add_argument("user_id")
    revoke_parser.add_argument("group")
    revoke_parser.add_argument("--guild-id", default=None)

    add_admin_parser = subparsers.add_parser("add-discord-global-admin", help="Add a global Discord admin user.")
    add_admin_parser.add_argument("user_id")

    remove_admin_parser = subparsers.add_parser(
        "remove-discord-global-admin",
        help="Remove a global Discord admin user.",
    )
    remove_admin_parser.add_argument("user_id")

    args = parser.parse_args(argv)
    base_dir = Path(args.base_dir).resolve()
    config = RuntimeConfig.from_sources(base_dir=base_dir, env_file=args.env_file)

    if args.command == "config-status":
        _print_json(_safe_config_status(config))
        return 0

    if args.command == "init-db":
        result = initialize_database(config.database)
        _print_json(result.to_record())
        return 0

    if args.command == "db-status":
        connection = connect_mysql(config.database)
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT DATABASE() AS database_name, VERSION() AS version")
                row = cursor.fetchone()
            _print_json(
                {
                    "mysql_connection": "ok",
                    "database": row["database_name"],
                    "version": row["version"],
                    "table_count": count_tables(connection),
                }
            )
        finally:
            connection.close()
        return 0

    if args.command == "lookup-player":
        client = _pubg_client_from_config(config)
        player = client.lookup_player_by_name(args.shard, args.nickname)
        _print_json(player.to_record())
        return 0

    if args.command == "register-player":
        client = _pubg_client_from_config(config)
        connection = connect_mysql(config.database)
        try:
            player = PlayerRegistry(connection).register_player_by_name(
                pubg_client=client,
                shard=args.shard,
                player_name=args.nickname,
                public_profile=not args.private,
            )
            _print_json({"player": player.to_record()})
        finally:
            connection.close()
        return 0

    if args.command == "player-stats":
        connection = connect_mysql(config.database)
        try:
            profile = PlayerStatsService(connection).get_profile(
                shard=args.shard,
                account_id=args.target if args.target.startswith("account.") else None,
                name=None if args.target.startswith("account.") else args.target,
                global_scope=True,
            )
            if profile is None:
                raise SystemExit("registered player stats not found.")
            _print_json({"profile": profile.to_record()})
        finally:
            connection.close()
        return 0

    if args.command == "player-weapon-stats":
        connection = connect_mysql(config.database)
        try:
            detail = PlayerStatsService(connection).get_weapon_detail(
                shard=args.shard,
                account_id=args.target if args.target.startswith("account.") else None,
                name=None if args.target.startswith("account.") else args.target,
                weapon=args.weapon,
                global_scope=True,
            )
            if detail is None:
                raise SystemExit("registered player weapon stats not found.")
            _print_json({"weapon": detail.to_record()})
        finally:
            connection.close()
        return 0

    if args.command == "player-recommendations":
        connection = connect_mysql(config.database)
        try:
            recommendations = PlayerRecommendationService(connection).get_recommendations(
                shard=args.shard,
                account_id=args.target if args.target.startswith("account.") else None,
                name=None if args.target.startswith("account.") else args.target,
                global_scope=True,
                limit=args.limit,
                min_matches=args.min_matches,
            )
            if recommendations is None:
                raise SystemExit("registered player recommendations not found.")
            _print_json({"recommendations": recommendations.to_record()})
        finally:
            connection.close()
        return 0

    if args.command == "player-recommendation-evidence":
        connection = connect_mysql(config.database)
        try:
            evidence = PlayerRecommendationService(connection).get_weapon_attachment_evidence(
                shard=args.shard,
                account_id=args.target if args.target.startswith("account.") else None,
                name=None if args.target.startswith("account.") else args.target,
                global_scope=True,
                weapon_code=args.weapon,
                attachment_code=args.attachment,
                limit=args.limit,
            )
            if evidence is None:
                raise SystemExit("registered player recommendation evidence not found.")
            _print_json({"evidence": evidence.to_record()})
        finally:
            connection.close()
        return 0

    if args.command == "player-match-stats":
        connection = connect_mysql(config.database)
        try:
            detail = PlayerStatsService(connection).get_match_detail(
                shard=args.shard,
                match_id=args.match_id,
                account_id=args.target if args.target and args.target.startswith("account.") else None,
                name=None if not args.target or args.target.startswith("account.") else args.target,
                global_scope=True,
            )
            if detail is None:
                raise SystemExit("registered player match detail not found.")
            _print_json({"match": detail.to_record()})
        finally:
            connection.close()
        return 0

    if args.command == "player-ranking":
        connection = connect_mysql(config.database)
        try:
            ranking = PlayerRankingService(connection).get_player_ranking(
                shard=args.shard,
                metric=args.metric,
                guild_id=args.guild_id,
                global_scope=args.guild_id is None,
                active_only=not args.include_inactive,
                min_matches=args.min_matches,
                limit=args.limit,
            )
            _print_json({"ranking": ranking.to_record()})
        finally:
            connection.close()
        return 0

    if args.command == "collect-matches":
        client = _pubg_client_from_config(config)
        connection = connect_mysql(config.database)
        try:
            result = RegisteredPlayerMatchCollector(
                connection,
                client,
                lookup_chunk_size=config.app.player_lookup_chunk_size,
            ).collect_active_players(
                shard=args.shard,
                limit=args.limit or config.app.collector_cycle_player_limit,
            )
            _print_json(result.to_record())
        finally:
            connection.close()
        return 0

    if args.command == "match-jobs":
        connection = connect_mysql(config.database)
        try:
            jobs = RegisteredPlayerMatchCollector(connection).list_match_jobs(limit=args.limit)
            _print_json({"jobs": _json_ready(jobs)})
        finally:
            connection.close()
        return 0

    if args.command == "process-match-jobs":
        client = _pubg_client_from_config(config)
        connection = connect_mysql(config.database)
        try:
            result = MatchJobProcessor(
                connection,
                client,
                RawPayloadStore(
                    config.app.raw_data_dir,
                    compression=config.app.raw_compression,  # type: ignore[arg-type]
                ),
            ).process_queued_matches(limit=args.limit)
            _print_json(result.to_record())
        finally:
            connection.close()
        return 0

    if args.command == "telemetry-jobs":
        connection = connect_mysql(config.database)
        try:
            jobs = TelemetryJobProcessor(
                connection,
                RawPayloadStore(
                    config.app.raw_data_dir,
                    compression=config.app.raw_compression,  # type: ignore[arg-type]
                ),
            ).list_telemetry_jobs(limit=args.limit)
            _print_json({"jobs": _json_ready(jobs)})
        finally:
            connection.close()
        return 0

    if args.command == "process-telemetry-jobs":
        connection = connect_mysql(config.database)
        try:
            result = TelemetryJobProcessor(
                connection,
                RawPayloadStore(
                    config.app.raw_data_dir,
                    compression=config.app.raw_compression,  # type: ignore[arg-type]
                ),
            ).process_queued_telemetry(limit=args.limit)
            _print_json(result.to_record())
        finally:
            connection.close()
        return 0

    if args.command == "parse-telemetry-combat":
        connection = connect_mysql(config.database)
        try:
            result = TelemetryCombatProcessor(
                connection,
                RawPayloadStore(
                    config.app.raw_data_dir,
                    compression=config.app.raw_compression,  # type: ignore[arg-type]
                ),
            ).process_raw_telemetry(limit=args.limit, force=args.force)
            _print_json(result.to_record())
        finally:
            connection.close()
        return 0

    if args.command == "parse-telemetry-items":
        connection = connect_mysql(config.database)
        try:
            result = TelemetryItemProcessor(
                connection,
                RawPayloadStore(
                    config.app.raw_data_dir,
                    compression=config.app.raw_compression,  # type: ignore[arg-type]
                ),
            ).process_raw_telemetry(limit=args.limit, force=args.force)
            _print_json(result.to_record())
        finally:
            connection.close()
        return 0

    if args.command == "parse-telemetry-movement":
        connection = connect_mysql(config.database)
        try:
            result = TelemetryMovementProcessor(
                connection,
                RawPayloadStore(
                    config.app.raw_data_dir,
                    compression=config.app.raw_compression,  # type: ignore[arg-type]
                ),
            ).process_raw_telemetry(limit=args.limit, force=args.force)
            _print_json(result.to_record())
        finally:
            connection.close()
        return 0

    if args.command == "generate-map-snapshots":
        connection = connect_mysql(config.database)
        try:
            result = MapSnapshotProcessor(
                connection,
                ReplayArtifactStore(config.app.replay_data_dir),
            ).generate_player_snapshots(limit=args.limit, force=args.force)
            _print_json(result.to_record())
        finally:
            connection.close()
        return 0

    if args.command == "generate-replay-timelines":
        connection = connect_mysql(config.database)
        try:
            result = ReplayTimelineProcessor(
                connection,
                ReplayArtifactStore(config.app.replay_data_dir),
            ).generate_player_timelines(limit=args.limit, force=args.force)
            _print_json(result.to_record())
        finally:
            connection.close()
        return 0

    if args.command == "generate-loadout-snapshots":
        connection = connect_mysql(config.database)
        try:
            result = LoadoutSnapshotProcessor(connection).process_matches(limit=args.limit, force=args.force)
            _print_json(result.to_record())
        finally:
            connection.close()
        return 0

    if args.command == "run-web":
        _run_web_app(host=args.host, port=args.port, base_dir=base_dir, env_file=args.env_file)
        return 0

    if args.command == "run-collector":
        options = CollectorWorkerOptions(
            shard=args.shard,
            match_job_limit=args.match_job_limit,
            telemetry_job_limit=args.telemetry_job_limit,
        )
        while True:
            current = RuntimeConfig.from_sources(base_dir=base_dir, env_file=args.env_file)
            result = run_collector_cycle(current, options=options)
            _print_json({"cycle": result.to_record()})
            if args.once:
                return 0
            try:
                sleep(max(60, min(current.app.collector_poll_interval_seconds, 300)))
            except KeyboardInterrupt:
                return 0

    if args.command == "run-post-processing":
        options = PostProcessingWorkerOptions(
            combat_limit=args.combat_limit,
            item_limit=args.item_limit,
            movement_limit=args.movement_limit,
            loadout_limit=args.loadout_limit,
            map_snapshot_limit=args.map_snapshot_limit,
            timeline_limit=args.timeline_limit,
            force=args.force,
        )
        while True:
            current = RuntimeConfig.from_sources(base_dir=base_dir, env_file=args.env_file)
            result = run_post_processing_cycle(current, options=options)
            _print_json({"cycle": result.to_record()})
            if args.once:
                return 0
            try:
                sleep(max(60, min(current.app.collector_poll_interval_seconds, 300)))
            except KeyboardInterrupt:
                return 0

    if args.command == "run-discord-bot":
        if not config.secrets.discord_bot_token:
            raise SystemExit("DISCORD_BOT_TOKEN is not configured in .env.")
        settings_store = _local_settings_store(base_dir=base_dir, env_file=args.env_file)
        permission_checker = DiscordPermissionChecker(
            settings_store.load_discord_permission_settings()
        )
        run_discord_bot(
            config=config,
            permission_checker=permission_checker,
            scope_settings_store=settings_store,
            command_prefix=args.prefix,
        )
        return 0

    if args.command == "discord-permissions":
        settings_store = _local_settings_store(base_dir=base_dir, env_file=args.env_file)
        try:
            settings = settings_store.load_discord_permission_settings()
        except LocalSettingsError as exc:
            raise SystemExit(str(exc)) from exc
        _print_json({"discord_permissions": settings.to_record()})
        return 0

    if args.command == "grant-discord-permission":
        manager = DiscordPermissionManager(_local_settings_store(base_dir=base_dir, env_file=args.env_file))
        _print_json(
            _permission_change_record(
                lambda: manager.grant(user_id=args.user_id, group=args.group, guild_id=args.guild_id)
            )
        )
        return 0

    if args.command == "revoke-discord-permission":
        manager = DiscordPermissionManager(_local_settings_store(base_dir=base_dir, env_file=args.env_file))
        _print_json(
            _permission_change_record(
                lambda: manager.revoke(user_id=args.user_id, group=args.group, guild_id=args.guild_id)
            )
        )
        return 0

    if args.command == "add-discord-global-admin":
        manager = DiscordPermissionManager(_local_settings_store(base_dir=base_dir, env_file=args.env_file))
        _print_json(_permission_change_record(lambda: manager.add_global_admin(args.user_id)))
        return 0

    if args.command == "remove-discord-global-admin":
        manager = DiscordPermissionManager(_local_settings_store(base_dir=base_dir, env_file=args.env_file))
        _print_json(_permission_change_record(lambda: manager.remove_global_admin(args.user_id)))
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


def _safe_config_status(config: RuntimeConfig) -> dict[str, Any]:
    return {
        "raw_data_dir": str(config.app.raw_data_dir),
        "replay_data_dir": str(config.app.replay_data_dir),
        "backup_data_dir": str(config.app.backup_data_dir),
        "quarantine_data_dir": str(config.app.quarantine_data_dir),
        "local_web_base_url": config.app.local_web_base_url,
        "raw_compression": config.app.raw_compression,
        "collector": {
            "poll_interval_seconds": config.app.collector_poll_interval_seconds,
            "cycle_player_limit": config.app.collector_cycle_player_limit,
            "player_lookup_chunk_size": config.app.player_lookup_chunk_size,
        },
        "database": config.database.safe_record(),
        "secrets": {
            key: status.to_record()
            for key, status in config.secrets.status().items()
        },
    }


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _json_ready(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value


def _permission_change_record(change_factory: Callable[[], Any]) -> dict[str, Any]:
    try:
        return change_factory().to_record()
    except LocalSettingsError as exc:
        raise SystemExit(str(exc)) from exc


def _pubg_client_from_config(config: RuntimeConfig) -> PubgApiClient:
    if not config.secrets.pubg_api_key:
        raise SystemExit("PUBG_API_KEY is not configured in .env.")
    return PubgApiClient(config.secrets.pubg_api_key)


def _local_settings_store(*, base_dir: Path, env_file: str) -> LocalSettingsStore:
    values = load_dotenv_values(base_dir / env_file)
    merged = dict(values)
    merged.update(os.environ)
    settings_file = merged.get("PUBG_LOCAL_SETTINGS_FILE", "./config/local_settings.json")
    return LocalSettingsStore(Path(settings_file), base_dir=base_dir)


def _run_web_app(*, host: str, port: int, base_dir: Path, env_file: str) -> None:
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit("uvicorn is required. Install project dependencies before running the web app.") from exc

    if host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("Refusing to bind local management app outside localhost by default.")

    uvicorn.run(
        "pubg_ai.web.app:create_app",
        host=host,
        port=port,
        factory=True,
        reload=False,
        app_dir=str(base_dir / "src"),
        env_file=str(base_dir / env_file),
    )


if __name__ == "__main__":
    raise SystemExit(main())
