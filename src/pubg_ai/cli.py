from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pubg_ai.config import RuntimeConfig
from pubg_ai.database import connect_mysql, count_tables, initialize_database
from pubg_ai.match_collection import RegisteredPlayerMatchCollector
from pubg_ai.match_job_processor import MatchJobProcessor
from pubg_ai.player_registry import PlayerRegistry
from pubg_ai.pubg_client import PubgApiClient
from pubg_ai.raw_storage import RawPayloadStore
from pubg_ai.telemetry_job_processor import TelemetryJobProcessor


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

    web_parser = subparsers.add_parser("run-web", help="Run the local management web app.")
    web_parser.add_argument("--host", default="127.0.0.1", help="Bind host. Defaults to localhost only.")
    web_parser.add_argument("--port", default=8000, type=int, help="Bind port.")

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

    if args.command == "run-web":
        _run_web_app(host=args.host, port=args.port, base_dir=base_dir, env_file=args.env_file)
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


def _safe_config_status(config: RuntimeConfig) -> dict[str, Any]:
    return {
        "raw_data_dir": str(config.app.raw_data_dir),
        "replay_data_dir": str(config.app.replay_data_dir),
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


def _pubg_client_from_config(config: RuntimeConfig) -> PubgApiClient:
    if not config.secrets.pubg_api_key:
        raise SystemExit("PUBG_API_KEY is not configured in .env.")
    return PubgApiClient(config.secrets.pubg_api_key)


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
