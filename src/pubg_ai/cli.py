from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pubg_ai.config import RuntimeConfig
from pubg_ai.database import connect_mysql, count_tables, initialize_database


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pubg-ai")
    parser.add_argument("--base-dir", default=".", help="Project base directory. Defaults to current directory.")
    parser.add_argument("--env-file", default=".env", help="dotenv file path relative to base-dir.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("config-status", help="Print safe runtime configuration status.")
    subparsers.add_parser("init-db", help="Create the MySQL database and MVP schema tables.")
    subparsers.add_parser("db-status", help="Check MySQL connection and table count.")

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
