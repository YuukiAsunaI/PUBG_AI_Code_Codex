from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from pubg_ai.config import RuntimeConfig, load_dotenv_values
from pubg_ai.database import connect_mysql, count_tables
from pubg_ai.discord_permission_manager import DiscordPermissionManager
from pubg_ai.local_settings import LocalSettingsError, LocalSettingsStore
from pubg_ai.map_snapshot_renderer import MapSnapshotProcessor
from pubg_ai.match_collection import RegisteredPlayerMatchCollector
from pubg_ai.match_job_processor import MatchJobProcessor
from pubg_ai.player_registry import DiscordCommandContext, PlayerRegistry
from pubg_ai.player_rankings import PlayerRankingService
from pubg_ai.player_stats import PlayerStatsService
from pubg_ai.pubg_client import PubgApiClient, PubgApiError
from pubg_ai.raw_storage import RawPayloadStore
from pubg_ai.replay_artifact_catalog import get_replay_artifact, list_replay_artifacts
from pubg_ai.replay_storage import ReplayArtifactStore, ReplayStorageError
from pubg_ai.telemetry_combat_processor import TelemetryCombatProcessor
from pubg_ai.telemetry_item_processor import TelemetryItemProcessor
from pubg_ai.telemetry_job_processor import TelemetryJobProcessor
from pubg_ai.telemetry_movement_processor import TelemetryMovementProcessor


class RegisterPlayerRequest(BaseModel):
    account_id: str | None = None
    shard: str = Field(default="steam", min_length=1)
    current_name: str = Field(min_length=1)
    public_profile: bool = True
    discord_user_id: str | None = None
    guild_id: str | None = None
    channel_id: str | None = None


class UnregisterPlayerRequest(BaseModel):
    shard: str = Field(default="steam", min_length=1)
    account_id: str | None = None
    name: str | None = None


class CollectMatchesRequest(BaseModel):
    shard: str | None = None
    limit: int | None = None


class ProcessMatchJobsRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=500)


class ProcessTelemetryJobsRequest(BaseModel):
    limit: int = Field(default=5, ge=1, le=200)


class ParseTelemetryCombatRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=200)
    force: bool = False


class ParseTelemetryItemsRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=200)
    force: bool = False


class ParseTelemetryMovementRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=200)
    force: bool = False


class GenerateMapSnapshotsRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=200)
    force: bool = False


class DiscordPermissionGrantRequest(BaseModel):
    user_id: str = Field(min_length=1)
    group: str = Field(min_length=1)
    guild_id: str | None = None


class DiscordGlobalAdminRequest(BaseModel):
    user_id: str = Field(min_length=1)


def create_app() -> Any:
    config = RuntimeConfig.from_sources(base_dir=Path.cwd())
    permission_manager = DiscordPermissionManager(_local_settings_store(Path.cwd()))
    app = FastAPI(
        title="PUBG AI Local Manager",
        version="0.1.0",
        docs_url="/docs",
        redoc_url=None,
    )

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _INDEX_HTML

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "local_only": True,
            "bind_host": "127.0.0.1",
        }

    @app.get("/settings/status")
    def settings_status() -> dict[str, Any]:
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

    @app.get("/discord/permissions")
    def discord_permissions() -> dict[str, Any]:
        try:
            return {"discord_permissions": permission_manager.load().to_record()}
        except LocalSettingsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/discord/permissions/grant")
    def grant_discord_permission(request: DiscordPermissionGrantRequest) -> dict[str, Any]:
        try:
            return permission_manager.grant(
                user_id=request.user_id,
                group=request.group,
                guild_id=request.guild_id,
            ).to_record()
        except LocalSettingsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/discord/permissions/revoke")
    def revoke_discord_permission(request: DiscordPermissionGrantRequest) -> dict[str, Any]:
        try:
            return permission_manager.revoke(
                user_id=request.user_id,
                group=request.group,
                guild_id=request.guild_id,
            ).to_record()
        except LocalSettingsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/discord/global-admins/add")
    def add_discord_global_admin(request: DiscordGlobalAdminRequest) -> dict[str, Any]:
        try:
            return permission_manager.add_global_admin(request.user_id).to_record()
        except LocalSettingsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/discord/global-admins/remove")
    def remove_discord_global_admin(request: DiscordGlobalAdminRequest) -> dict[str, Any]:
        try:
            return permission_manager.remove_global_admin(request.user_id).to_record()
        except LocalSettingsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/database/status")
    def database_status() -> dict[str, Any]:
        connection = connect_mysql(config.database)
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT DATABASE() AS database_name, VERSION() AS version")
                row = cursor.fetchone()
            return {
                "mysql_connection": "ok",
                "database": row["database_name"],
                "version": row["version"],
                "table_count": count_tables(connection),
            }
        finally:
            connection.close()

    @app.get("/players")
    def list_players(shard: str | None = None, active_only: bool = True, limit: int = 100) -> dict[str, Any]:
        connection = connect_mysql(config.database)
        try:
            players = PlayerRegistry(connection).list_players(
                shard=shard,
                active_only=active_only,
                limit=limit,
            )
            return {"players": [player.to_record() for player in players]}
        finally:
            connection.close()

    @app.get("/players/profile")
    def player_profile(shard: str = "steam", name: str | None = None, account_id: str | None = None) -> dict[str, Any]:
        if not name and not account_id:
            raise HTTPException(status_code=400, detail="name or account_id is required.")

        connection = connect_mysql(config.database)
        try:
            profile = PlayerStatsService(connection).get_profile(
                shard=shard,
                account_id=account_id,
                name=name,
                global_scope=True,
            )
            if profile is None:
                raise HTTPException(status_code=404, detail="registered player stats not found.")
            return {"profile": profile.to_record()}
        finally:
            connection.close()

    @app.get("/players/weapon")
    def player_weapon(
        weapon: str,
        shard: str = "steam",
        name: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, Any]:
        if not name and not account_id:
            raise HTTPException(status_code=400, detail="name or account_id is required.")

        connection = connect_mysql(config.database)
        try:
            detail = PlayerStatsService(connection).get_weapon_detail(
                shard=shard,
                account_id=account_id,
                name=name,
                weapon=weapon,
                global_scope=True,
            )
            if detail is None:
                raise HTTPException(status_code=404, detail="registered player weapon stats not found.")
            return {"weapon": detail.to_record()}
        finally:
            connection.close()

    @app.get("/players/match")
    def player_match(
        match_id: str,
        shard: str = "steam",
        name: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, Any]:
        connection = connect_mysql(config.database)
        try:
            detail = PlayerStatsService(connection).get_match_detail(
                shard=shard,
                match_id=match_id,
                account_id=account_id,
                name=name,
                global_scope=True,
            )
            if detail is None:
                raise HTTPException(status_code=404, detail="registered player match detail not found.")
            return {"match": detail.to_record()}
        finally:
            connection.close()

    @app.get("/rankings/players")
    def player_ranking(
        metric: str = "kda",
        shard: str = "steam",
        guild_id: str | None = None,
        limit: int = 10,
        min_matches: int = 1,
        active_only: bool = True,
    ) -> dict[str, Any]:
        connection = connect_mysql(config.database)
        try:
            ranking = PlayerRankingService(connection).get_player_ranking(
                shard=shard,
                metric=metric,
                guild_id=guild_id,
                global_scope=guild_id is None,
                active_only=active_only,
                min_matches=min_matches,
                limit=limit,
            )
            return {"ranking": ranking.to_record()}
        finally:
            connection.close()

    @app.post("/players/register")
    def register_player(request: RegisterPlayerRequest) -> dict[str, Any]:
        connection = connect_mysql(config.database)
        try:
            context = DiscordCommandContext(
                user_id=request.discord_user_id,
                guild_id=request.guild_id,
                channel_id=request.channel_id,
            )
            registry = PlayerRegistry(connection)
            if request.account_id:
                player = registry.register_player(
                    account_id=request.account_id,
                    shard=request.shard,
                    current_name=request.current_name,
                    public_profile=request.public_profile,
                    context=context,
                )
            else:
                if not config.secrets.pubg_api_key:
                    raise HTTPException(status_code=500, detail="PUBG_API_KEY is not configured.")
                try:
                    player = registry.register_player_by_name(
                        pubg_client=PubgApiClient(config.secrets.pubg_api_key),
                        shard=request.shard,
                        player_name=request.current_name,
                        public_profile=request.public_profile,
                        context=context,
                    )
                except PubgApiError as exc:
                    raise HTTPException(status_code=502, detail=str(exc)) from exc
            return {"player": player.to_record()}
        finally:
            connection.close()

    @app.post("/players/unregister")
    def unregister_player(request: UnregisterPlayerRequest) -> dict[str, Any]:
        if not request.account_id and not request.name:
            raise HTTPException(status_code=400, detail="account_id or name is required.")

        connection = connect_mysql(config.database)
        try:
            player = PlayerRegistry(connection).unregister_player(
                shard=request.shard,
                account_id=request.account_id,
                name=request.name,
            )
            if player is None:
                raise HTTPException(status_code=404, detail="player not found.")
            return {"player": player.to_record()}
        finally:
            connection.close()

    @app.post("/collection/refresh")
    def refresh_collection(request: CollectMatchesRequest) -> dict[str, Any]:
        if not config.secrets.pubg_api_key:
            raise HTTPException(status_code=500, detail="PUBG_API_KEY is not configured.")

        connection = connect_mysql(config.database)
        try:
            try:
                result = RegisteredPlayerMatchCollector(
                    connection,
                    PubgApiClient(config.secrets.pubg_api_key),
                    lookup_chunk_size=config.app.player_lookup_chunk_size,
                ).collect_active_players(
                    shard=request.shard,
                    limit=request.limit or config.app.collector_cycle_player_limit,
                )
            except PubgApiError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            return {"result": result.to_record()}
        finally:
            connection.close()

    @app.get("/jobs/matches")
    def match_jobs(limit: int = 100) -> dict[str, Any]:
        connection = connect_mysql(config.database)
        try:
            jobs = RegisteredPlayerMatchCollector(connection).list_match_jobs(limit=limit)
            return {"jobs": [_json_ready(job) for job in jobs]}
        finally:
            connection.close()

    @app.post("/jobs/matches/process")
    def process_match_jobs(request: ProcessMatchJobsRequest) -> dict[str, Any]:
        if not config.secrets.pubg_api_key:
            raise HTTPException(status_code=500, detail="PUBG_API_KEY is not configured.")

        connection = connect_mysql(config.database)
        try:
            result = MatchJobProcessor(
                connection,
                PubgApiClient(config.secrets.pubg_api_key),
                RawPayloadStore(
                    config.app.raw_data_dir,
                    compression=config.app.raw_compression,  # type: ignore[arg-type]
                ),
            ).process_queued_matches(limit=request.limit)
            return {"result": result.to_record()}
        finally:
            connection.close()

    @app.get("/jobs/telemetry")
    def telemetry_jobs(limit: int = 100) -> dict[str, Any]:
        connection = connect_mysql(config.database)
        try:
            jobs = TelemetryJobProcessor(
                connection,
                RawPayloadStore(
                    config.app.raw_data_dir,
                    compression=config.app.raw_compression,  # type: ignore[arg-type]
                ),
            ).list_telemetry_jobs(limit=limit)
            return {"jobs": [_json_ready(job) for job in jobs]}
        finally:
            connection.close()

    @app.post("/jobs/telemetry/process")
    def process_telemetry_jobs(request: ProcessTelemetryJobsRequest) -> dict[str, Any]:
        connection = connect_mysql(config.database)
        try:
            result = TelemetryJobProcessor(
                connection,
                RawPayloadStore(
                    config.app.raw_data_dir,
                    compression=config.app.raw_compression,  # type: ignore[arg-type]
                ),
            ).process_queued_telemetry(limit=request.limit)
            return {"result": result.to_record()}
        finally:
            connection.close()

    @app.post("/telemetry/combat/process")
    def process_telemetry_combat(request: ParseTelemetryCombatRequest) -> dict[str, Any]:
        connection = connect_mysql(config.database)
        try:
            result = TelemetryCombatProcessor(
                connection,
                RawPayloadStore(
                    config.app.raw_data_dir,
                    compression=config.app.raw_compression,  # type: ignore[arg-type]
                ),
            ).process_raw_telemetry(limit=request.limit, force=request.force)
            return {"result": result.to_record()}
        finally:
            connection.close()

    @app.post("/telemetry/items/process")
    def process_telemetry_items(request: ParseTelemetryItemsRequest) -> dict[str, Any]:
        connection = connect_mysql(config.database)
        try:
            result = TelemetryItemProcessor(
                connection,
                RawPayloadStore(
                    config.app.raw_data_dir,
                    compression=config.app.raw_compression,  # type: ignore[arg-type]
                ),
            ).process_raw_telemetry(limit=request.limit, force=request.force)
            return {"result": result.to_record()}
        finally:
            connection.close()

    @app.post("/telemetry/movement/process")
    def process_telemetry_movement(request: ParseTelemetryMovementRequest) -> dict[str, Any]:
        connection = connect_mysql(config.database)
        try:
            result = TelemetryMovementProcessor(
                connection,
                RawPayloadStore(
                    config.app.raw_data_dir,
                    compression=config.app.raw_compression,  # type: ignore[arg-type]
                ),
            ).process_raw_telemetry(limit=request.limit, force=request.force)
            return {"result": result.to_record()}
        finally:
            connection.close()

    @app.post("/replay/map-snapshots/generate")
    def generate_map_snapshots(request: GenerateMapSnapshotsRequest) -> dict[str, Any]:
        connection = connect_mysql(config.database)
        try:
            result = MapSnapshotProcessor(
                connection,
                ReplayArtifactStore(config.app.replay_data_dir),
            ).generate_player_snapshots(limit=request.limit, force=request.force)
            return {"result": result.to_record()}
        finally:
            connection.close()

    @app.get("/replay/artifacts")
    def replay_artifacts(
        limit: int = 50,
        artifact_type: str | None = "map_snapshot",
        match_id: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, Any]:
        connection = connect_mysql(config.database)
        try:
            artifacts = list_replay_artifacts(
                connection,
                limit=limit,
                artifact_type=artifact_type,
                match_id=match_id,
                account_id=account_id,
            )
            return {"artifacts": [artifact.to_record() for artifact in artifacts]}
        finally:
            connection.close()

    @app.get("/replay/artifacts/{artifact_id}/file")
    def replay_artifact_file(artifact_id: int) -> FileResponse:
        connection = connect_mysql(config.database)
        try:
            artifact = get_replay_artifact(connection, artifact_id)
        finally:
            connection.close()

        if artifact is None:
            raise HTTPException(status_code=404, detail="replay artifact not found.")

        store = ReplayArtifactStore(config.app.replay_data_dir)
        try:
            path = store.resolve_path(artifact.relative_path)
        except ReplayStorageError as exc:
            raise HTTPException(status_code=404, detail="replay artifact path is invalid.") from exc
        if not path.is_file():
            raise HTTPException(status_code=404, detail="replay artifact file not found.")

        return FileResponse(
            path,
            media_type=artifact.content_type,
            filename=path.name,
        )

    return app


def _json_ready(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value


def _local_settings_store(base_dir: Path) -> LocalSettingsStore:
    values = load_dotenv_values(base_dir / ".env")
    merged = dict(values)
    merged.update(os.environ)
    settings_file = merged.get("PUBG_LOCAL_SETTINGS_FILE", "./config/local_settings.json")
    return LocalSettingsStore(Path(settings_file), base_dir=base_dir)


_INDEX_HTML = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PUBG AI Local Manager</title>
  <style>
    :root {
      color-scheme: light;
      font-family: Arial, "Malgun Gothic", sans-serif;
      --bg: #f4f6f8;
      --panel: #ffffff;
      --text: #17202a;
      --muted: #65727f;
      --line: #d8dee6;
      --accent: #1677c7;
      --danger: #b42318;
    }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--bg); color: var(--text); }
    header {
      padding: 18px 24px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }
    h1 { margin: 0; font-size: 20px; letter-spacing: 0; }
    main { max-width: 1180px; margin: 0 auto; padding: 24px; display: grid; gap: 18px; }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
    }
    h2 { margin: 0 0 14px; font-size: 16px; letter-spacing: 0; }
    .grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
    .kv { border-left: 3px solid var(--accent); padding: 4px 10px; min-width: 0; }
    .kv span { display: block; color: var(--muted); font-size: 12px; }
    .kv strong { display: block; margin-top: 4px; font-size: 14px; overflow-wrap: anywhere; }
    form { display: grid; grid-template-columns: 120px 1fr 1fr 150px auto; gap: 10px; align-items: end; }
    label { display: grid; gap: 6px; color: var(--muted); font-size: 12px; }
    input, select {
      width: 100%;
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 10px;
      font-size: 14px;
      background: #fff;
    }
    button {
      min-height: 38px;
      border: 0;
      border-radius: 6px;
      padding: 8px 12px;
      font-size: 14px;
      background: var(--accent);
      color: #fff;
      cursor: pointer;
    }
    button.secondary { background: #46515c; }
    button.danger { background: var(--danger); }
    table { width: 100%; border-collapse: collapse; table-layout: fixed; }
    th, td { border-bottom: 1px solid var(--line); padding: 10px; text-align: left; font-size: 14px; }
    th { color: var(--muted); font-weight: 600; }
    td { overflow-wrap: anywhere; }
    .actions { display: flex; gap: 8px; justify-content: flex-end; }
    .status { color: var(--muted); font-size: 13px; }
    @media (max-width: 900px) {
      .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      form { grid-template-columns: 1fr; }
      header { align-items: flex-start; flex-direction: column; }
    }
  </style>
</head>
<body>
  <header>
    <h1>PUBG AI Local Manager</h1>
    <div class="status" id="banner">localhost 전용 관리 화면</div>
  </header>
  <main>
    <section>
      <h2>상태</h2>
      <div class="grid" id="statusGrid"></div>
    </section>
    <section>
      <h2>Discord 권한</h2>
      <form id="discordGrantForm">
        <label>User ID
          <input name="user_id" autocomplete="off" required>
        </label>
        <label>권한 그룹
          <select name="group" id="discordPermissionGroup" required></select>
        </label>
        <label>Guild ID
          <input name="guild_id" autocomplete="off" placeholder="비우면 전체 권한">
        </label>
        <button type="submit">권한 추가</button>
      </form>
      <form id="discordAdminForm" style="margin-top: 10px;">
        <label>Global Admin User ID
          <input name="user_id" autocomplete="off" required>
        </label>
        <button type="submit">전역 관리자 추가</button>
      </form>
      <table style="margin-top: 12px;">
        <thead>
          <tr>
            <th>범위</th>
            <th>User ID</th>
            <th>권한</th>
            <th></th>
          </tr>
        </thead>
        <tbody id="discordPermissionsBody"></tbody>
      </table>
    </section>
    <section>
      <h2>유저 등록</h2>
    <form id="registerForm">
        <label>플랫폼
          <select name="shard">
            <option value="steam">steam</option>
            <option value="kakao">kakao</option>
          </select>
        </label>
        <label>닉네임
          <input name="current_name" autocomplete="off" required>
        </label>
        <label>Account ID
          <input name="account_id" autocomplete="off" placeholder="자동 조회">
        </label>
        <label>공개 프로필
          <select name="public_profile">
            <option value="true">공개</option>
            <option value="false">비공개</option>
          </select>
        </label>
        <button type="submit">등록</button>
      </form>
    </section>
    <section>
      <h2>등록 유저</h2>
      <div class="actions" style="margin-bottom: 10px;">
        <button type="button" onclick="refreshCollection()">최근 매치 수집</button>
        <button class="secondary" type="button" onclick="loadPlayers()">새로고침</button>
      </div>
      <table>
        <thead>
          <tr>
            <th>플랫폼</th>
            <th>닉네임</th>
            <th>Account ID</th>
            <th>상태</th>
            <th></th>
          </tr>
        </thead>
        <tbody id="playersBody"></tbody>
      </table>
    </section>
    <section>
      <h2>전적 조회</h2>
      <form id="profileForm">
        <label>플랫폼
          <select name="shard">
            <option value="steam">steam</option>
            <option value="kakao">kakao</option>
          </select>
        </label>
        <label>닉네임 또는 Account ID
          <input name="target" autocomplete="off" required>
        </label>
        <button type="submit">조회</button>
      </form>
      <div class="status" id="profileBody" style="margin-top: 12px;">조회 대기 중</div>
    </section>
    <section>
      <h2>무기 조회</h2>
      <form id="weaponForm">
        <label>플랫폼
          <select name="shard">
            <option value="steam">steam</option>
            <option value="kakao">kakao</option>
          </select>
        </label>
        <label>닉네임 또는 Account ID
          <input name="target" autocomplete="off" required>
        </label>
        <label>무기
          <input name="weapon" autocomplete="off" placeholder="M416" required>
        </label>
        <button type="submit">조회</button>
      </form>
      <div class="status" id="weaponBody" style="margin-top: 12px;">조회 대기 중</div>
    </section>
    <section>
      <h2>매치 조회</h2>
      <form id="matchForm">
        <label>플랫폼
          <select name="shard">
            <option value="steam">steam</option>
            <option value="kakao">kakao</option>
          </select>
        </label>
        <label>Match ID
          <input name="match_id" autocomplete="off" required>
        </label>
        <label>닉네임 또는 Account ID
          <input name="target" autocomplete="off" placeholder="비우면 등록 참가자 자동 선택">
        </label>
        <button type="submit">조회</button>
      </form>
      <div class="status" id="matchBody" style="margin-top: 12px;">조회 대기 중</div>
    </section>
    <section>
      <h2>랭킹 조회</h2>
      <form id="rankingForm">
        <label>플랫폼
          <select name="shard">
            <option value="steam">steam</option>
            <option value="kakao">kakao</option>
          </select>
        </label>
        <label>지표
          <select name="metric">
            <option value="kda">KDA</option>
            <option value="win_rate">승률</option>
            <option value="avg_damage">평균 딜</option>
            <option value="damage">총 딜</option>
            <option value="kills">킬</option>
            <option value="dbnos">기절</option>
            <option value="accuracy">명중률</option>
            <option value="headshot_rate">헤드샷 킬 비율</option>
            <option value="matches">경기 수</option>
          </select>
        </label>
        <label>Guild ID
          <input name="guild_id" autocomplete="off" placeholder="비우면 전체">
        </label>
        <label>Limit
          <input name="limit" type="number" min="1" max="100" value="10">
        </label>
        <button type="submit">조회</button>
      </form>
      <div class="status" id="rankingBody" style="margin-top: 12px;">조회 대기 중</div>
    </section>
    <section>
      <h2>Match 수집 큐</h2>
      <div class="actions" style="margin-bottom: 10px;">
        <button type="button" onclick="processMatchJobs()">상세 저장</button>
        <button class="secondary" type="button" onclick="loadJobs()">새로고침</button>
      </div>
      <table>
        <thead>
          <tr>
            <th>플랫폼</th>
            <th>Match ID</th>
            <th>상태</th>
            <th>시도</th>
            <th>생성</th>
          </tr>
        </thead>
        <tbody id="jobsBody"></tbody>
      </table>
    </section>
    <section>
      <h2>Telemetry 수집 큐</h2>
      <div class="actions" style="margin-bottom: 10px;">
        <button type="button" onclick="processTelemetryJobs()">Telemetry 저장</button>
        <button class="secondary" type="button" onclick="loadTelemetryJobs()">새로고침</button>
      </div>
      <table>
        <thead>
          <tr>
            <th>플랫폼</th>
            <th>Match ID</th>
            <th>상태</th>
            <th>시도</th>
            <th>생성</th>
          </tr>
        </thead>
        <tbody id="telemetryJobsBody"></tbody>
      </table>
    </section>
    <section>
      <h2>Combat 파싱</h2>
      <div class="actions" style="margin-bottom: 10px;">
        <button type="button" onclick="parseTelemetryCombat(false)">전투 파싱</button>
        <button class="secondary" type="button" onclick="parseTelemetryCombat(true)">재파싱</button>
      </div>
      <div class="status" id="combatStatus">대기 중</div>
    </section>
    <section>
      <h2>Item 파싱</h2>
      <div class="actions" style="margin-bottom: 10px;">
        <button type="button" onclick="parseTelemetryItems(false)">아이템 파싱</button>
        <button class="secondary" type="button" onclick="parseTelemetryItems(true)">재파싱</button>
      </div>
      <div class="status" id="itemStatus">대기 중</div>
    </section>
    <section>
      <h2>Movement 파싱</h2>
      <div class="actions" style="margin-bottom: 10px;">
        <button type="button" onclick="parseTelemetryMovement(false)">위치 파싱</button>
        <button class="secondary" type="button" onclick="parseTelemetryMovement(true)">재파싱</button>
      </div>
      <div class="status" id="movementStatus">대기 중</div>
    </section>
    <section>
      <h2>Map Snapshot 생성</h2>
      <div class="actions" style="margin-bottom: 10px;">
        <button type="button" onclick="generateMapSnapshots(false)">JPEG 생성</button>
        <button class="secondary" type="button" onclick="generateMapSnapshots(true)">재생성</button>
      </div>
      <div class="status" id="mapSnapshotStatus">대기 중</div>
    </section>
    <section>
      <h2>Map Snapshot 목록</h2>
      <div class="actions" style="margin-bottom: 10px;">
        <button class="secondary" type="button" onclick="loadReplayArtifacts()">새로고침</button>
      </div>
      <table>
        <thead>
          <tr>
            <th>생성</th>
            <th>맵</th>
            <th>모드</th>
            <th>Match ID</th>
            <th>크기</th>
            <th></th>
          </tr>
        </thead>
        <tbody id="replayArtifactsBody"></tbody>
      </table>
    </section>
  </main>
  <script>
    const statusGrid = document.querySelector("#statusGrid");
    const playersBody = document.querySelector("#playersBody");
    const profileBody = document.querySelector("#profileBody");
    const weaponBody = document.querySelector("#weaponBody");
    const matchBody = document.querySelector("#matchBody");
    const rankingBody = document.querySelector("#rankingBody");
    const jobsBody = document.querySelector("#jobsBody");
    const telemetryJobsBody = document.querySelector("#telemetryJobsBody");
    const combatStatus = document.querySelector("#combatStatus");
    const itemStatus = document.querySelector("#itemStatus");
    const movementStatus = document.querySelector("#movementStatus");
    const mapSnapshotStatus = document.querySelector("#mapSnapshotStatus");
    const replayArtifactsBody = document.querySelector("#replayArtifactsBody");
    const discordPermissionsBody = document.querySelector("#discordPermissionsBody");
    const discordPermissionGroup = document.querySelector("#discordPermissionGroup");
    const banner = document.querySelector("#banner");

    function cell(label, value) {
      return `<div class="kv"><span>${label}</span><strong>${value}</strong></div>`;
    }

    function escapeHtml(value) {
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
    }

    function attr(value) {
      return escapeHtml(value);
    }

    async function loadStatus() {
      const [settings, database] = await Promise.all([
        fetch("/settings/status").then((r) => r.json()),
        fetch("/database/status").then((r) => r.json()).catch(() => ({ mysql_connection: "error" })),
      ]);
      statusGrid.innerHTML = [
        cell("MySQL", `${database.mysql_connection || "unknown"} / ${database.database || "-"}`),
        cell("PUBG API Key", settings.secrets.PUBG_API_KEY.configured ? "설정됨" : "없음"),
        cell("Discord Token", settings.secrets.DISCORD_BOT_TOKEN.configured ? "설정됨" : "없음"),
        cell("Raw 저장소", settings.raw_data_dir),
        cell("Replay 저장소", settings.replay_data_dir),
        cell("수집 주기", `${settings.collector.poll_interval_seconds}초`),
        cell("주기당 대상", `${settings.collector.cycle_player_limit}명`),
        cell("조회 chunk", `${settings.collector.player_lookup_chunk_size}명`),
      ].join("");
    }

    async function loadDiscordPermissions() {
      const payload = await fetch("/discord/permissions").then((r) => r.json());
      const settings = payload.discord_permissions;
      const groupNames = Object.keys(settings.command_groups || {}).sort();
      discordPermissionGroup.innerHTML = groupNames.map((group) => (
        `<option value="${attr(group)}">${escapeHtml(group)}</option>`
      )).join("");

      const rows = [];
      for (const userId of settings.global_admin_user_ids || []) {
        rows.push({
          scope: "global_admin",
          userId,
          group: "all",
          action: "remove-global-admin",
          guildId: "",
        });
      }
      for (const [userId, groups] of Object.entries(settings.user_grants || {})) {
        for (const group of groups) {
          rows.push({
            scope: "global",
            userId,
            group,
            action: "revoke-permission",
            guildId: "",
          });
        }
      }
      for (const [guildId, grants] of Object.entries(settings.guild_user_grants || {})) {
        for (const [userId, groups] of Object.entries(grants)) {
          for (const group of groups) {
            rows.push({
              scope: `guild:${guildId}`,
              userId,
              group,
              action: "revoke-permission",
              guildId,
            });
          }
        }
      }

      discordPermissionsBody.innerHTML = rows.map((row) => `
        <tr>
          <td>${escapeHtml(row.scope)}</td>
          <td>${escapeHtml(row.userId)}</td>
          <td>${escapeHtml(row.group)}</td>
          <td>
            <div class="actions">
              <button
                class="danger"
                type="button"
                data-discord-action="${attr(row.action)}"
                data-user-id="${attr(row.userId)}"
                data-group="${attr(row.group)}"
                data-guild-id="${attr(row.guildId)}"
              >해제</button>
            </div>
          </td>
        </tr>
      `).join("") || `<tr><td colspan="4">등록된 권한이 없습니다.</td></tr>`;
    }

    async function loadPlayers() {
      const payload = await fetch("/players?active_only=false").then((r) => r.json());
      playersBody.innerHTML = payload.players.map((player) => `
        <tr>
          <td>${player.shard}</td>
          <td>${player.current_name}</td>
          <td>${player.account_id}</td>
          <td>${player.active ? "수집중" : "중지"}</td>
          <td>
            <div class="actions">
              <button class="danger" type="button" onclick="unregisterPlayer('${player.shard}', '${player.account_id}')">
                삭제
              </button>
            </div>
          </td>
        </tr>
      `).join("");
    }

    async function loadPlayerProfile(target, shard) {
      const params = new URLSearchParams({ shard });
      if (target.startsWith("account.")) {
        params.set("account_id", target);
      } else {
        params.set("name", target);
      }
      const response = await fetch(`/players/profile?${params.toString()}`);
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || response.statusText);
      }
      const payload = await response.json();
      const profile = payload.profile;
      const totals = profile.totals;
      const weapons = (profile.top_weapons || []).slice(0, 3).map((weapon) => (
        `${escapeHtml(weapon.weapon_name)} ${weapon.kills}킬 ${Number(weapon.damage_dealt).toFixed(0)}딜`
      )).join(", ") || "-";
      profileBody.innerHTML = [
        `<strong>${escapeHtml(profile.player.current_name)} (${escapeHtml(profile.player.shard)})</strong>`,
        `경기/치킨: ${totals.match_count}전 ${totals.wins}치킨 (${percent(totals.win_rate)})`,
        `K/D/A: ${totals.kills}/${totals.deaths}/${totals.assists} · KDA ${Number(totals.kda).toFixed(2)}`,
        `평균 딜/받은 딜: ${Number(totals.avg_damage_dealt).toFixed(1)} / ${Number(totals.avg_damage_taken).toFixed(1)}`,
        `명중률: ${percent(totals.accuracy)}`,
        `주무기: ${weapons}`,
      ].join("<br>");
    }

    async function loadPlayerWeapon(target, weapon, shard) {
      const params = new URLSearchParams({ shard, weapon });
      if (target.startsWith("account.")) {
        params.set("account_id", target);
      } else {
        params.set("name", target);
      }
      const response = await fetch(`/players/weapon?${params.toString()}`);
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || response.statusText);
      }
      const payload = await response.json();
      const detail = payload.weapon;
      const totals = detail.totals;
      const recent = (detail.recent_matches || []).slice(0, 3).map((match) => (
        `${escapeHtml(match.match_id.slice(0, 8))} ${match.kills}킬 ${match.dbnos}기절 ${Number(match.damage_dealt).toFixed(0)}딜`
      )).join("<br>") || "-";
      weaponBody.innerHTML = [
        `<strong>${escapeHtml(detail.player.current_name)} ${escapeHtml(detail.weapon_name)}</strong>`,
        `사용 경기/치킨: ${totals.match_count}전 ${totals.wins}치킨 (${percent(totals.win_rate)})`,
        `킬/어시/기절: ${totals.kills}/${totals.assists}/${totals.dbnos}`,
        `딜/평균 딜: ${Number(totals.damage_dealt).toFixed(0)} / ${Number(totals.avg_damage_dealt).toFixed(1)}`,
        `명중률: ${percent(totals.accuracy)} (${totals.shots_hit}/${totals.shots_fired})`,
        `최근 사용 경기:<br>${recent}`,
      ].join("<br>");
    }

    async function loadPlayerMatch(matchId, target, shard) {
      const params = new URLSearchParams({ shard, match_id: matchId });
      if (target) {
        if (target.startsWith("account.")) {
          params.set("account_id", target);
        } else {
          params.set("name", target);
        }
      }
      const response = await fetch(`/players/match?${params.toString()}`);
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || response.statusText);
      }
      const payload = await response.json();
      const detail = payload.match;
      const weapons = (detail.weapons || []).slice(0, 4).map((weapon) => (
        `${escapeHtml(weapon.weapon_name)} ${weapon.kills}킬/${weapon.dbnos}기절/${Number(weapon.damage_dealt).toFixed(0)}딜/${percent(weapon.accuracy)}`
      )).join(", ") || "-";
      const snapshot = detail.replay_artifact
        ? `<a href="${detail.replay_artifact.view_url}" target="_blank" rel="noreferrer">2D 스냅샷 열기</a>`
        : "-";
      matchBody.innerHTML = [
        `<strong>${escapeHtml(detail.player.current_name)} ${escapeHtml(detail.match_id)}</strong>`,
        `맵/모드: ${escapeHtml(detail.map_name || "-")} / ${escapeHtml(detail.game_mode || "-")} / ${escapeHtml(detail.match_type || "-")}`,
        `결과/등수: ${detail.is_chicken ? "치킨" : "치킨 아님"} / ${detail.win_place ? `#${detail.win_place}` : "-"}`,
        `인원: 총 ${detail.total_players ?? "-"}명, 사람 ${detail.human_players ?? "-"}명, 봇 ${detail.bot_players ?? "-"}명`,
        `K/D/A/기절: ${detail.kills}/${detail.deaths}/${detail.assists}/${detail.dbnos_caused} (당한 기절 ${detail.dbnos_taken})`,
        `딜/받은 딜: ${Number(detail.damage_dealt).toFixed(1)} / ${Number(detail.damage_taken).toFixed(1)}`,
        `발사/명중/명중률: ${detail.shots_fired}/${detail.shots_hit}/${percent(detail.accuracy)}`,
        `생존/이동/낙하: ${minutes(detail.survival_seconds)} / ${distanceKm(detail.movement_distance_m)} / ${distanceM(detail.landing_distance_m)}`,
        `사용 무기: ${weapons}`,
        `2D 스냅샷: ${snapshot}`,
      ].join("<br>");
    }

    async function loadPlayerRanking(metric, shard, guildId, limit) {
      const params = new URLSearchParams({
        metric,
        shard,
        limit: String(limit || 10),
      });
      if (guildId) {
        params.set("guild_id", guildId);
      }
      const response = await fetch(`/rankings/players?${params.toString()}`);
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || response.statusText);
      }
      const payload = await response.json();
      const ranking = payload.ranking;
      const rows = (ranking.rows || []).map((row) => `
        <tr>
          <td>#${row.rank}</td>
          <td>${escapeHtml(row.player.current_name)}</td>
          <td>${rankingScore(ranking.metric, row.score)}</td>
          <td>${row.match_count}</td>
          <td>${row.wins}</td>
          <td>${row.kills}/${row.deaths}/${row.assists}</td>
          <td>${Number(row.avg_damage_dealt).toFixed(1)}</td>
        </tr>
      `).join("");
      rankingBody.innerHTML = `
        <strong>${escapeHtml(ranking.metric_label)} 랭킹 (${escapeHtml(ranking.shard)}, ${ranking.global_scope ? "전체" : escapeHtml(ranking.guild_id || "-")})</strong>
        <table style="margin-top: 10px;">
          <thead>
            <tr>
              <th>순위</th>
              <th>닉네임</th>
              <th>점수</th>
              <th>경기</th>
              <th>치킨</th>
              <th>K/D/A</th>
              <th>평딜</th>
            </tr>
          </thead>
          <tbody>${rows || `<tr><td colspan="7">랭킹 데이터가 없습니다.</td></tr>`}</tbody>
        </table>
      `;
    }

    function percent(value) {
      return `${(Number(value || 0) * 100).toFixed(1)}%`;
    }

    function rankingScore(metric, value) {
      if (["win_rate", "accuracy", "headshot_rate"].includes(metric)) {
        return percent(value);
      }
      if (["kda", "avg_damage"].includes(metric)) {
        return Number(value || 0).toFixed(2);
      }
      return Number(value || 0).toFixed(0);
    }

    function minutes(value) {
      return value === null || value === undefined ? "-" : `${(Number(value) / 60).toFixed(1)}분`;
    }

    function distanceKm(value) {
      return value === null || value === undefined ? "-" : `${(Number(value) / 1000).toFixed(1)}km`;
    }

    function distanceM(value) {
      return value === null || value === undefined ? "-" : `${Number(value).toFixed(0)}m`;
    }

    async function loadJobs() {
      const payload = await fetch("/jobs/matches?limit=50").then((r) => r.json());
      jobsBody.innerHTML = payload.jobs.map((job) => `
        <tr>
          <td>${job.shard || ""}</td>
          <td>${job.target_id || ""}</td>
          <td>${job.status || ""}</td>
          <td>${job.attempts || 0}</td>
          <td>${job.created_at_kst || ""}</td>
        </tr>
      `).join("");
    }

    async function loadTelemetryJobs() {
      const payload = await fetch("/jobs/telemetry?limit=50").then((r) => r.json());
      telemetryJobsBody.innerHTML = payload.jobs.map((job) => `
        <tr>
          <td>${job.shard || ""}</td>
          <td>${job.target_id || ""}</td>
          <td>${job.status || ""}</td>
          <td>${job.attempts || 0}</td>
          <td>${job.created_at_kst || ""}</td>
        </tr>
      `).join("");
    }

    async function loadReplayArtifacts() {
      const payload = await fetch("/replay/artifacts?artifact_type=map_snapshot&limit=50").then((r) => r.json());
      replayArtifactsBody.innerHTML = payload.artifacts.map((artifact) => `
        <tr>
          <td>${artifact.generated_at_kst || ""}</td>
          <td>${artifact.map_name || ""}</td>
          <td>${artifact.game_mode || ""}</td>
          <td>${artifact.match_id || ""}</td>
          <td>${formatBytes(artifact.size_bytes || 0)}</td>
          <td>
            <div class="actions">
              <a href="${artifact.view_url}" target="_blank" rel="noreferrer">열기</a>
            </div>
          </td>
        </tr>
      `).join("");
    }

    function formatBytes(value) {
      if (!Number.isFinite(value) || value <= 0) return "0 B";
      const units = ["B", "KB", "MB", "GB"];
      let size = value;
      let unit = 0;
      while (size >= 1024 && unit < units.length - 1) {
        size /= 1024;
        unit += 1;
      }
      return `${size.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
    }

    async function refreshCollection() {
      banner.textContent = "최근 매치 수집 중";
      const response = await fetch("/collection/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || response.statusText);
      }
      const payload = await response.json();
      banner.textContent = `수집 완료: 신규 ${payload.result.queued_match_jobs}개, 기존 ${payload.result.existing_match_jobs}개`;
      await Promise.all([loadPlayers(), loadJobs()]);
    }

    async function processMatchJobs() {
      banner.textContent = "Match 상세 저장 중";
      const response = await fetch("/jobs/matches/process", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ limit: 10 }),
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || response.statusText);
      }
      const payload = await response.json();
      banner.textContent = `상세 저장 완료: 저장 ${payload.result.stored_matches}개, telemetry 신규 ${payload.result.queued_telemetry_jobs}개, 실패 ${payload.result.failed_jobs}개`;
      await Promise.all([loadJobs(), loadTelemetryJobs()]);
    }

    async function processTelemetryJobs() {
      banner.textContent = "Telemetry 저장 중";
      const response = await fetch("/jobs/telemetry/process", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ limit: 5 }),
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || response.statusText);
      }
      const payload = await response.json();
      const mb = (payload.result.stored_bytes / 1024 / 1024).toFixed(1);
      banner.textContent = `Telemetry 저장 완료: 저장 ${payload.result.stored_telemetry}개, ${mb}MB, 실패 ${payload.result.failed_jobs}개`;
      await loadTelemetryJobs();
    }

    async function parseTelemetryCombat(force) {
      banner.textContent = force ? "전투 재파싱 중" : "전투 파싱 중";
      const response = await fetch("/telemetry/combat/process", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ limit: 10, force }),
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || response.statusText);
      }
      const payload = await response.json();
      combatStatus.textContent = `파싱 ${payload.result.parsed_payloads}개, 요약 ${payload.result.combat_summaries}개, 무기 ${payload.result.weapon_stats}개, 실패 ${payload.result.failed_payloads}개`;
      banner.textContent = "전투 파싱 완료";
    }

    async function parseTelemetryItems(force) {
      banner.textContent = force ? "아이템 재파싱 중" : "아이템 파싱 중";
      const response = await fetch("/telemetry/items/process", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ limit: 10, force }),
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || response.statusText);
      }
      const payload = await response.json();
      itemStatus.textContent = `파싱 ${payload.result.parsed_payloads}개, 이벤트 ${payload.result.item_events}개, 아이템 ${payload.result.item_stats}개, 실패 ${payload.result.failed_payloads}개`;
      banner.textContent = "아이템 파싱 완료";
    }

    async function parseTelemetryMovement(force) {
      banner.textContent = force ? "위치 재파싱 중" : "위치 파싱 중";
      const response = await fetch("/telemetry/movement/process", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ limit: 10, force }),
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || response.statusText);
      }
      const payload = await response.json();
      movementStatus.textContent = `파싱 ${payload.result.parsed_payloads}개, 위치 ${payload.result.position_samples}개, 전투위치 ${payload.result.combat_location_events}개, 보급 ${payload.result.care_package_events}개, 비행기 ${payload.result.plane_routes}개, 실패 ${payload.result.failed_payloads}개`;
      banner.textContent = "위치 파싱 완료";
    }

    async function generateMapSnapshots(force) {
      banner.textContent = force ? "JPEG 재생성 중" : "JPEG 생성 중";
      const response = await fetch("/replay/map-snapshots/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ limit: 10, force }),
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || response.statusText);
      }
      const payload = await response.json();
      mapSnapshotStatus.textContent = `생성 ${payload.result.generated_snapshots}개, 기존 ${payload.result.skipped_existing}개, 위치없음 ${payload.result.skipped_no_position}개, 실패 ${payload.result.failed_snapshots}개, artifact ${payload.result.artifacts.length}개`;
      banner.textContent = "JPEG 생성 완료";
      await loadReplayArtifacts();
    }

    async function unregisterPlayer(shard, accountId) {
      await fetch("/players/unregister", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ shard, account_id: accountId }),
      });
      await loadPlayers();
    }

    async function postJson(url, payload) {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || response.statusText);
      }
      return response.json();
    }

    async function revokeDiscordPermission(userId, group, guildId) {
      await postJson("/discord/permissions/revoke", {
        user_id: userId,
        group,
        guild_id: guildId || null,
      });
      await loadDiscordPermissions();
    }

    async function removeDiscordGlobalAdmin(userId) {
      await postJson("/discord/global-admins/remove", { user_id: userId });
      await loadDiscordPermissions();
    }

    document.querySelector("#registerForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      await fetch("/players/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          shard: form.get("shard"),
          current_name: form.get("current_name"),
          account_id: form.get("account_id") || null,
          public_profile: form.get("public_profile") === "true",
        }),
      });
      event.currentTarget.reset();
      await loadPlayers();
    });

    document.querySelector("#profileForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      try {
        await loadPlayerProfile(String(form.get("target") || ""), String(form.get("shard") || "steam"));
        banner.textContent = "전적 조회 완료";
      } catch (error) {
        profileBody.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    document.querySelector("#weaponForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      try {
        await loadPlayerWeapon(
          String(form.get("target") || ""),
          String(form.get("weapon") || ""),
          String(form.get("shard") || "steam"),
        );
        banner.textContent = "무기 조회 완료";
      } catch (error) {
        weaponBody.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    document.querySelector("#matchForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      try {
        await loadPlayerMatch(
          String(form.get("match_id") || ""),
          String(form.get("target") || ""),
          String(form.get("shard") || "steam"),
        );
        banner.textContent = "매치 조회 완료";
      } catch (error) {
        matchBody.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    document.querySelector("#rankingForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      try {
        await loadPlayerRanking(
          String(form.get("metric") || "kda"),
          String(form.get("shard") || "steam"),
          String(form.get("guild_id") || ""),
          Number(form.get("limit") || 10),
        );
        banner.textContent = "랭킹 조회 완료";
      } catch (error) {
        rankingBody.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    document.querySelector("#discordGrantForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      try {
        await postJson("/discord/permissions/grant", {
          user_id: form.get("user_id"),
          group: form.get("group"),
          guild_id: form.get("guild_id") || null,
        });
        event.currentTarget.reset();
        await loadDiscordPermissions();
        banner.textContent = "Discord 권한이 추가되었습니다.";
      } catch (error) {
        banner.textContent = `오류: ${error.message}`;
      }
    });

    document.querySelector("#discordAdminForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      try {
        await postJson("/discord/global-admins/add", { user_id: form.get("user_id") });
        event.currentTarget.reset();
        await loadDiscordPermissions();
        banner.textContent = "Discord 전역 관리자가 추가되었습니다.";
      } catch (error) {
        banner.textContent = `오류: ${error.message}`;
      }
    });

    discordPermissionsBody.addEventListener("click", async (event) => {
      const button = event.target instanceof Element
        ? event.target.closest("button[data-discord-action]")
        : null;
      if (!button) return;

      try {
        if (button.dataset.discordAction === "remove-global-admin") {
          await removeDiscordGlobalAdmin(button.dataset.userId);
        } else {
          await revokeDiscordPermission(
            button.dataset.userId,
            button.dataset.group,
            button.dataset.guildId || null,
          );
        }
        banner.textContent = "Discord 권한이 해제되었습니다.";
      } catch (error) {
        banner.textContent = `오류: ${error.message}`;
      }
    });

    Promise.all([loadStatus(), loadDiscordPermissions(), loadPlayers(), loadJobs(), loadTelemetryJobs(), loadReplayArtifacts()])
      .then(() => { banner.textContent = "localhost 전용 관리 화면"; })
      .catch((error) => { banner.textContent = `오류: ${error.message}`; });
  </script>
</body>
</html>
"""
