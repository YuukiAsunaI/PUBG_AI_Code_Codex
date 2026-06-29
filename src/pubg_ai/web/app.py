from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field

from pubg_ai.config import RuntimeConfig, load_dotenv_values
from pubg_ai.database import connect_mysql, count_tables
from pubg_ai.discord_permission_manager import DiscordPermissionManager
from pubg_ai.local_settings import LocalSettingsError, LocalSettingsStore
from pubg_ai.loadout_snapshot_processor import LoadoutSnapshotProcessor
from pubg_ai.map_snapshot_renderer import MAP_ASSET_FILENAMES, MapAssetProvider, MapSnapshotProcessor
from pubg_ai.match_collection import RegisteredPlayerMatchCollector
from pubg_ai.match_job_processor import MatchJobProcessor
from pubg_ai.player_registry import DiscordCommandContext, PlayerRegistry
from pubg_ai.player_rankings import PlayerRankingService
from pubg_ai.player_recommendations import PlayerRecommendationService
from pubg_ai.player_stats import PlayerStatsService
from pubg_ai.pubg_client import PubgApiClient, PubgApiError
from pubg_ai.raw_storage import RawPayloadStore
from pubg_ai.replay_artifact_catalog import get_replay_artifact, list_replay_artifacts
from pubg_ai.replay_storage import ReplayArtifactStore, ReplayStorageError
from pubg_ai.replay_timeline_builder import ReplayTimelineProcessor
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


class GenerateReplayTimelinesRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=200)
    force: bool = False


class GenerateLoadoutSnapshotsRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=500)
    force: bool = False


class DiscordPermissionGrantRequest(BaseModel):
    user_id: str = Field(min_length=1)
    group: str = Field(min_length=1)
    guild_id: str | None = None


class DiscordGlobalAdminRequest(BaseModel):
    user_id: str = Field(min_length=1)


class WebSettingsRequest(BaseModel):
    local_web_base_url: str | None = None


def create_app() -> Any:
    base_dir = Path.cwd()
    config = RuntimeConfig.from_sources(base_dir=base_dir)
    settings_store = _local_settings_store(base_dir)
    permission_manager = DiscordPermissionManager(settings_store)
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

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        return Response(status_code=204)

    @app.get("/settings/status")
    def settings_status() -> dict[str, Any]:
        return _settings_status_record(RuntimeConfig.from_sources(base_dir=base_dir))

    @app.post("/settings/web")
    def save_web_settings(request: WebSettingsRequest) -> dict[str, Any]:
        try:
            web_settings = settings_store.save_web_settings(request.local_web_base_url)
        except LocalSettingsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "web": web_settings.to_record(),
            "settings": _settings_status_record(RuntimeConfig.from_sources(base_dir=base_dir)),
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

    @app.get("/players/recommendations")
    def player_recommendations(
        shard: str = "steam",
        name: str | None = None,
        account_id: str | None = None,
        limit: int = 5,
        min_matches: int = 1,
    ) -> dict[str, Any]:
        if not name and not account_id:
            raise HTTPException(status_code=400, detail="name or account_id is required.")

        connection = connect_mysql(config.database)
        try:
            recommendations = PlayerRecommendationService(connection).get_recommendations(
                shard=shard,
                account_id=account_id,
                name=name,
                global_scope=True,
                limit=limit,
                min_matches=min_matches,
            )
            if recommendations is None:
                raise HTTPException(status_code=404, detail="registered player recommendations not found.")
            return {"recommendations": recommendations.to_record()}
        finally:
            connection.close()

    @app.get("/players/recommendations/weapon-attachment-evidence")
    def player_recommendation_weapon_attachment_evidence(
        weapon_code: str,
        attachment_code: str,
        shard: str = "steam",
        name: str | None = None,
        account_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        if not name and not account_id:
            raise HTTPException(status_code=400, detail="name or account_id is required.")

        connection = connect_mysql(config.database)
        try:
            evidence = PlayerRecommendationService(connection).get_weapon_attachment_evidence(
                shard=shard,
                account_id=account_id,
                name=name,
                global_scope=True,
                weapon_code=weapon_code,
                attachment_code=attachment_code,
                limit=limit,
            )
            if evidence is None:
                raise HTTPException(status_code=404, detail="registered player recommendation evidence not found.")
            return {"evidence": evidence.to_record()}
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

    @app.post("/telemetry/loadout-snapshots/generate")
    def generate_loadout_snapshots(request: GenerateLoadoutSnapshotsRequest) -> dict[str, Any]:
        connection = connect_mysql(config.database)
        try:
            result = LoadoutSnapshotProcessor(connection).process_matches(limit=request.limit, force=request.force)
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

    @app.post("/replay/timelines/generate")
    def generate_replay_timelines(request: GenerateReplayTimelinesRequest) -> dict[str, Any]:
        connection = connect_mysql(config.database)
        try:
            result = ReplayTimelineProcessor(
                connection,
                ReplayArtifactStore(config.app.replay_data_dir),
            ).generate_player_timelines(limit=request.limit, force=request.force)
            return {"result": result.to_record()}
        finally:
            connection.close()

    @app.get("/replay/map-assets/{map_name}")
    def replay_map_asset(map_name: str) -> FileResponse:
        filename = MAP_ASSET_FILENAMES.get(map_name)
        if filename is None:
            raise HTTPException(status_code=404, detail="map asset is not registered.")

        cache_root = config.app.replay_data_dir / "cache"
        asset_path = cache_root / "map_assets" / filename
        if not asset_path.exists():
            MapAssetProvider(cache_root).load_map(map_name)

        resolved = asset_path.resolve()
        allowed_root = (cache_root / "map_assets").resolve()
        if allowed_root != resolved.parent or not resolved.is_file():
            raise HTTPException(status_code=404, detail="map asset file not found.")

        return FileResponse(
            resolved,
            media_type="image/png",
            filename=filename,
        )

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


def _settings_status_record(config: RuntimeConfig) -> dict[str, Any]:
    return {
        "raw_data_dir": str(config.app.raw_data_dir),
        "replay_data_dir": str(config.app.replay_data_dir),
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
    .recommendation-line {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      min-height: 32px;
    }
    .recommendation-line button { min-height: 30px; padding: 5px 9px; font-size: 12px; flex: 0 0 auto; }
    .detail-panel {
      margin-top: 12px;
      padding: 10px 12px;
      border-left: 3px solid var(--accent);
      background: #f8fafc;
    }
    .detail-table { margin-top: 8px; table-layout: auto; }
    .detail-table th, .detail-table td { font-size: 12px; padding: 7px; vertical-align: top; }
    .player-controls { display: grid; grid-template-columns: minmax(220px, 1fr) 110px auto auto; gap: 10px; align-items: end; }
    .toggle-row { display: flex; flex-wrap: wrap; gap: 12px; margin: 12px 0; color: var(--muted); font-size: 13px; }
    .toggle-row label { display: inline-flex; grid-template-columns: none; align-items: center; gap: 6px; }
    .toggle-row input { width: auto; min-height: 0; }
    .timeline-range { display: grid; grid-template-columns: 1fr 110px; gap: 12px; align-items: center; margin: 12px 0; }
    #timelineScrubber { padding: 0; }
    .replay-detail-layout {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(280px, 360px);
      gap: 14px;
      align-items: start;
    }
    .replay-canvas-wrap {
      width: 100%;
      max-width: 960px;
      aspect-ratio: 1 / 1;
      border: 1px solid var(--line);
      background: #111820;
      overflow: hidden;
    }
    #replayCanvas { display: block; width: 100%; height: 100%; }
    .timeline-event-panel {
      display: grid;
      gap: 10px;
      min-width: 0;
    }
    .timeline-event-list {
      display: grid;
      gap: 6px;
      max-height: 360px;
      overflow: auto;
      padding-right: 4px;
    }
    .timeline-event-row {
      display: grid;
      grid-template-columns: 58px minmax(0, 1fr);
      gap: 4px 8px;
      align-items: center;
      text-align: left;
      min-height: 46px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--text);
    }
    .timeline-event-row span { color: var(--muted); font-size: 12px; }
    .timeline-event-row strong { overflow-wrap: anywhere; }
    .timeline-event-row em { grid-column: 2; color: var(--muted); font-size: 12px; font-style: normal; overflow-wrap: anywhere; }
    .timeline-event-row.active { border-color: var(--accent); background: #eef7ff; }
    .timeline-event-detail {
      border-left: 3px solid var(--accent);
      padding: 10px 12px;
      background: #f8fafc;
      min-height: 110px;
    }
    .timeline-team-list {
      display: grid;
      gap: 6px;
    }
    .team-member {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 4px 8px;
      align-items: center;
      border: 1px solid var(--line);
      background: #fff;
      padding: 8px 10px;
    }
    .team-member.self { border-color: #39ff14; }
    .team-member.registered { background: #eef7ff; border-color: var(--accent); }
    .team-member strong { overflow-wrap: anywhere; }
    .team-member span { color: var(--muted); font-size: 12px; }
    .team-member span:last-child { grid-column: 1 / -1; }
    @media (max-width: 900px) {
      .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      form { grid-template-columns: 1fr; }
      .player-controls { grid-template-columns: 1fr; }
      .timeline-range { grid-template-columns: 1fr; }
      .replay-detail-layout { grid-template-columns: 1fr; }
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
      <h2>Local Web Link</h2>
      <form id="webSettingsForm">
        <label>Base URL
          <input name="local_web_base_url" autocomplete="off" placeholder="http://127.0.0.1:8000">
        </label>
        <button type="submit">Save</button>
      </form>
      <div class="status" id="webSettingsStatus" style="margin-top: 12px;">Waiting</div>
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
      <h2>Recommendation 조회</h2>
      <form id="recommendationForm">
        <label>플랫폼
          <select name="shard">
            <option value="steam">steam</option>
            <option value="kakao">kakao</option>
          </select>
        </label>
        <label>닉네임 또는 Account ID
          <input name="target" autocomplete="off" required>
        </label>
        <label>Min matches
          <input name="min_matches" type="number" min="1" max="50" value="1">
        </label>
        <button type="submit">조회</button>
      </form>
      <div class="status" id="recommendationBody" style="margin-top: 12px;">조회 대기 중</div>
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
      <h2>Loadout Snapshot 생성</h2>
      <div class="actions" style="margin-bottom: 10px;">
        <button type="button" onclick="generateLoadoutSnapshots(false)">파츠 스냅샷 생성</button>
        <button class="secondary" type="button" onclick="generateLoadoutSnapshots(true)">재생성</button>
      </div>
      <div class="status" id="loadoutSnapshotStatus">대기 중</div>
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
      <h2>Replay Timeline 생성</h2>
      <div class="actions" style="margin-bottom: 10px;">
        <button type="button" onclick="generateReplayTimelines(false)">JSON 생성</button>
        <button class="secondary" type="button" onclick="generateReplayTimelines(true)">재생성</button>
      </div>
      <div class="status" id="timelineStatus">대기 중</div>
    </section>
    <section>
      <h2>2D Replay Player</h2>
      <div class="player-controls">
        <label>Timeline
          <select id="timelineSelect"></select>
        </label>
        <label>속도
          <select id="timelineSpeed">
            <option value="0.5">0.5x</option>
            <option value="1" selected>1x</option>
            <option value="2">2x</option>
            <option value="4">4x</option>
            <option value="8">8x</option>
          </select>
        </label>
        <button type="button" id="timelinePlayButton">재생</button>
        <button class="secondary" type="button" id="timelineResetButton">처음</button>
      </div>
      <div class="toggle-row">
        <label><input type="checkbox" id="timelineShowPath" checked>이동</label>
        <label><input type="checkbox" id="timelineShowCombat" checked>전투</label>
        <label><input type="checkbox" id="timelineShowCare" checked>보급</label>
        <label><input type="checkbox" id="timelineShowPlane" checked>비행기</label>
        <label><input type="checkbox" id="timelineShowPhase" checked>자기장</label>
        <label><input type="checkbox" id="timelineShowTeam" checked>팀원</label>
        <label><input type="checkbox" id="timelineFollowPlayer">팔로우</label>
        <label>줌
          <select id="timelineZoom">
            <option value="1" selected>1x</option>
            <option value="1.5">1.5x</option>
            <option value="2">2x</option>
            <option value="3">3x</option>
            <option value="4">4x</option>
          </select>
        </label>
      </div>
      <div class="timeline-range">
        <input id="timelineScrubber" type="range" min="0" max="0" value="0" step="0.1">
        <div class="status" id="timelineClock">0.0초</div>
      </div>
      <div class="replay-detail-layout">
        <div class="replay-canvas-wrap">
          <canvas id="replayCanvas" width="960" height="960"></canvas>
        </div>
        <div class="timeline-event-panel">
          <div class="timeline-team-list" id="timelineTeamList"></div>
          <div class="status" id="timelineEventDetail">이벤트 대기 중</div>
          <div class="timeline-event-list" id="timelineEventList"></div>
        </div>
      </div>
      <div class="status" id="replayPlayerStatus" style="margin-top: 12px;">대기 중</div>
    </section>
    <section>
      <h2>Replay Artifact 목록</h2>
      <div class="actions" style="margin-bottom: 10px;">
        <button class="secondary" type="button" onclick="loadReplayArtifacts()">새로고침</button>
      </div>
      <table>
        <thead>
          <tr>
            <th>생성</th>
            <th>타입</th>
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
    const recommendationBody = document.querySelector("#recommendationBody");
    const matchBody = document.querySelector("#matchBody");
    const rankingBody = document.querySelector("#rankingBody");
    const jobsBody = document.querySelector("#jobsBody");
    const telemetryJobsBody = document.querySelector("#telemetryJobsBody");
    const combatStatus = document.querySelector("#combatStatus");
    const itemStatus = document.querySelector("#itemStatus");
    const movementStatus = document.querySelector("#movementStatus");
    const loadoutSnapshotStatus = document.querySelector("#loadoutSnapshotStatus");
    const mapSnapshotStatus = document.querySelector("#mapSnapshotStatus");
    const timelineStatus = document.querySelector("#timelineStatus");
    const replayArtifactsBody = document.querySelector("#replayArtifactsBody");
    const discordPermissionsBody = document.querySelector("#discordPermissionsBody");
    const discordPermissionGroup = document.querySelector("#discordPermissionGroup");
    const webSettingsForm = document.querySelector("#webSettingsForm");
    const webSettingsStatus = document.querySelector("#webSettingsStatus");
    const banner = document.querySelector("#banner");
    const timelineSelect = document.querySelector("#timelineSelect");
    const timelineSpeed = document.querySelector("#timelineSpeed");
    const timelinePlayButton = document.querySelector("#timelinePlayButton");
    const timelineResetButton = document.querySelector("#timelineResetButton");
    const timelineScrubber = document.querySelector("#timelineScrubber");
    const timelineClock = document.querySelector("#timelineClock");
    const timelineEventDetail = document.querySelector("#timelineEventDetail");
    const timelineEventList = document.querySelector("#timelineEventList");
    const timelineTeamList = document.querySelector("#timelineTeamList");
    const replayCanvas = document.querySelector("#replayCanvas");
    const replayPlayerStatus = document.querySelector("#replayPlayerStatus");
    const timelineShowPath = document.querySelector("#timelineShowPath");
    const timelineShowCombat = document.querySelector("#timelineShowCombat");
    const timelineShowCare = document.querySelector("#timelineShowCare");
    const timelineShowPlane = document.querySelector("#timelineShowPlane");
    const timelineShowPhase = document.querySelector("#timelineShowPhase");
    const timelineShowTeam = document.querySelector("#timelineShowTeam");
    const timelineFollowPlayer = document.querySelector("#timelineFollowPlayer");
    const timelineZoom = document.querySelector("#timelineZoom");
    const replayCtx = replayCanvas.getContext("2d");
    let replayTimelineArtifacts = [];
    let activeTimeline = null;
    let activeTimelineArtifact = null;
    let activeTimelineEvents = [];
    let activeTimelineSelectedEventId = null;
    let activeTimelineDetailKey = "";
    let activeTimelineDuration = 0;
    let activeTimelineTime = 0;
    let replayMapImage = null;
    let replayMapImageName = "";
    let replayAnimationId = null;
    let replayLastFrameMs = 0;
    let replayPlaying = false;
    let activeRecommendationTarget = "";
    let activeRecommendationShard = "steam";

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
      webSettingsForm.elements.local_web_base_url.value = settings.local_web_base_url || "";
      webSettingsStatus.textContent = settings.local_web_base_url
        ? `Enabled: ${settings.local_web_base_url}`
        : "Disabled";
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

    async function loadPlayerRecommendations(target, shard, minMatches) {
      activeRecommendationTarget = target;
      activeRecommendationShard = shard;
      const params = new URLSearchParams({
        shard,
        limit: "5",
        min_matches: String(minMatches || 1),
      });
      if (target.startsWith("account.")) {
        params.set("account_id", target);
      } else {
        params.set("name", target);
      }
      const response = await fetch(`/players/recommendations?${params.toString()}`);
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || response.statusText);
      }
      const payload = await response.json();
      const report = payload.recommendations;
      const weapons = recommendationLines(report.weapons, (item) => (
        `${escapeHtml(item.weapon_name)} score ${Number(item.score).toFixed(1)} / ${item.match_count} matches / ${Number(item.avg_damage_dealt).toFixed(1)} avg dmg / ${percent(item.win_rate)} win`
      ));
      const weaponParts = recommendationLines(report.weapon_attachments, (item) => (
        `<span>${escapeHtml(item.weapon_name)} + ${escapeHtml(item.attachment_name)} score ${Number(item.score).toFixed(1)} / ${item.match_count} matches / ${item.event_count || item.attached_events} events / ${distanceM(item.avg_distance_m)} avg</span>
        <button class="secondary" type="button" data-evidence="weapon-attachment" data-weapon-code="${attr(item.weapon_code)}" data-attachment-code="${attr(item.attachment_code)}">근거</button>`
      ));
      const weaponRanges = recommendationLines(report.weapon_ranges, (item) => (
        `${escapeHtml(item.weapon_name)} ${escapeHtml(item.bucket_label)} / ${item.event_count} events / ${item.kills} kills / ${item.dbnos} DBNOs`
      ));
      const attachments = recommendationLines(report.attachments, (item) => (
        `${escapeHtml(item.item_name)} score ${Number(item.score).toFixed(1)} / ${item.attached_events} attaches / ${Number(item.avg_damage_dealt).toFixed(1)} avg dmg`
      ));
      const maps = recommendationLines(report.maps, (item) => (
        `${escapeHtml(item.map_name_ko)} score ${Number(item.score).toFixed(1)} / ${item.match_count} matches / ${percent(item.win_rate)} win`
      ));
      const teammates = recommendationLines(report.teammates, (item) => (
        `${escapeHtml(item.name)}${item.registered ? " (registered)" : ""} score ${Number(item.score).toFixed(1)} / ${item.match_count} matches / ${percent(item.win_rate)} win`
      ));
      const drops = recommendationLines(report.drop_zones, (item) => (
        `${escapeHtml(item.map_name_ko)} grid ${item.grid_x},${item.grid_y} / ${percent(item.x_pct)} x ${percent(item.y_pct)} / ${item.match_count} matches / ${percent(item.win_rate)} win`
      ));
      recommendationBody.innerHTML = [
        `<strong>${escapeHtml(report.player.current_name)} recommendations</strong>`,
        `<br><strong>Weapons</strong><br>${weapons}`,
        `<br><strong>Weapon parts</strong><br>${weaponParts}`,
        `<br><strong>Weapon ranges</strong><br>${weaponRanges}`,
        `<br><strong>Attachments</strong><br>${attachments}`,
        `<br><strong>Maps</strong><br>${maps}`,
        `<br><strong>Teammates</strong><br>${teammates}`,
        `<br><strong>Drop zones</strong><br>${drops}`,
        `<div class="detail-panel status" id="recommendationEvidence">추천 근거 대기 중</div>`,
      ].join("");
    }

    function recommendationLines(items, formatter) {
      if (!items || !items.length) return "-";
      return items.slice(0, 5).map((item) => `<div class="recommendation-line">- ${formatter(item)}</div>`).join("");
    }

    async function loadWeaponAttachmentEvidence(weaponCode, attachmentCode) {
      const panel = document.querySelector("#recommendationEvidence");
      if (!panel) return;
      if (!activeRecommendationTarget) {
        panel.textContent = "추천 조회 후 근거를 확인할 수 있습니다.";
        return;
      }

      panel.textContent = "추천 근거 조회 중";
      const params = new URLSearchParams({
        shard: activeRecommendationShard,
        weapon_code: weaponCode,
        attachment_code: attachmentCode,
        limit: "10",
      });
      if (activeRecommendationTarget.startsWith("account.")) {
        params.set("account_id", activeRecommendationTarget);
      } else {
        params.set("name", activeRecommendationTarget);
      }

      const response = await fetch(`/players/recommendations/weapon-attachment-evidence?${params.toString()}`);
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || response.statusText);
      }
      const payload = await response.json();
      const report = payload.evidence;
      const totals = report.totals || {};
      const rows = (report.snapshots || []).map((snapshot) => `
        <tr>
          <td>${escapeHtml(snapshot.combat_event_at_kst || "-")}</td>
          <td>${escapeHtml(snapshot.map_name_ko || snapshot.map_name || "-")}<br>${escapeHtml(snapshot.game_mode || "-")}</td>
          <td>${escapeHtml(snapshot.combat_action)}${snapshot.is_headshot ? " / HS" : ""}</td>
          <td>${distanceM(snapshot.distance_m)}</td>
          <td>${escapeHtml((snapshot.equipped_attachment_names || []).join(", ") || "-")}</td>
          <td>${escapeHtml(snapshot.match_id)}</td>
        </tr>
      `).join("");

      panel.innerHTML = [
        `<strong>${escapeHtml(report.weapon_name)} + ${escapeHtml(report.attachment_name)} 근거</strong>`,
        `<br>events ${totals.event_count || 0}, matches ${totals.match_count || 0}, kills ${totals.kills || 0}, DBNO ${totals.dbnos || 0}, finishes ${totals.finishes || 0}, HS ${totals.headshots || 0}, avg ${distanceM(totals.avg_distance_m)}`,
        rows
          ? `<table class="detail-table"><thead><tr><th>시간</th><th>맵/모드</th><th>결과</th><th>거리</th><th>장착 파츠</th><th>Match</th></tr></thead><tbody>${rows}</tbody></table>`
          : `<br>해당 무기+파츠 스냅샷 근거가 없습니다.`,
      ].join("");
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
      const payload = await fetch("/replay/artifacts?artifact_type=&limit=50").then((r) => r.json());
      updateTimelineOptions(payload.artifacts || []);
      replayArtifactsBody.innerHTML = payload.artifacts.map((artifact) => `
        <tr>
          <td>${artifact.generated_at_kst || ""}</td>
          <td>${artifact.artifact_type || ""}</td>
          <td>${artifact.map_name || ""}</td>
          <td>${artifact.game_mode || ""}</td>
          <td>${artifact.match_id || ""}</td>
          <td>${formatBytes(artifact.size_bytes || 0)}</td>
          <td>
            <div class="actions">
              ${artifact.artifact_type === "timeline" ? `<button type="button" data-load-timeline="${artifact.id}">재생</button>` : ""}
              <a href="${artifact.view_url}" target="_blank" rel="noreferrer">열기</a>
            </div>
          </td>
        </tr>
      `).join("");
      if (replayTimelineArtifacts.length && (!activeTimelineArtifact || String(activeTimelineArtifact.id) !== timelineSelect.value)) {
        await loadSelectedTimeline();
      }
    }

    function updateTimelineOptions(artifacts) {
      const previous = timelineSelect.value;
      replayTimelineArtifacts = artifacts.filter((artifact) => artifact.artifact_type === "timeline");
      if (!replayTimelineArtifacts.length) {
        pauseReplay();
        activeTimeline = null;
        activeTimelineArtifact = null;
        activeTimelineEvents = [];
        activeTimelineSelectedEventId = null;
        renderTimelineTeamList();
        renderTimelineEventList();
        renderTimelineEventDetail(null);
        replayPlayerStatus.textContent = "timeline artifact가 없습니다.";
        drawEmptyReplayCanvas();
      }
      timelineSelect.innerHTML = replayTimelineArtifacts.map((artifact) => {
        const label = [
          artifact.player_name || "unknown",
          artifact.map_name || "-",
          artifact.game_mode || "-",
          artifact.match_id ? artifact.match_id.slice(0, 8) : "-",
        ].join(" / ");
        return `<option value="${attr(artifact.id)}">${escapeHtml(label)}</option>`;
      }).join("") || `<option value="">timeline 없음</option>`;

      if (previous && replayTimelineArtifacts.some((artifact) => String(artifact.id) === previous)) {
        timelineSelect.value = previous;
      }
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

    async function loadSelectedTimeline() {
      const artifact = replayTimelineArtifacts.find((item) => String(item.id) === timelineSelect.value);
      if (!artifact) {
        activeTimeline = null;
        activeTimelineArtifact = null;
        activeTimelineEvents = [];
        activeTimelineSelectedEventId = null;
        renderTimelineTeamList();
        renderTimelineEventList();
        renderTimelineEventDetail(null);
        replayPlayerStatus.textContent = "timeline artifact가 없습니다.";
        drawEmptyReplayCanvas();
        return;
      }

      pauseReplay();
      const payload = await fetch(artifact.view_url).then((response) => {
        if (!response.ok) throw new Error(response.statusText);
        return response.json();
      });
      activeTimeline = payload;
      activeTimelineArtifact = artifact;
      activeTimelineEvents = timelineEvents(payload);
      activeTimelineSelectedEventId = null;
      activeTimelineDetailKey = "";
      activeTimelineDuration = Math.max(1, timelineDuration(payload));
      activeTimelineTime = 0;
      timelineScrubber.max = String(activeTimelineDuration);
      timelineScrubber.value = "0";
      replayPlayerStatus.textContent = `${payload.player?.name || artifact.player_name || "unknown"} / ${payload.match?.map_name || "-"} / ${payload.match?.match_id || artifact.match_id}`;
      await loadReplayMapImage(payload.match?.map_name);
      renderTimelineTeamList();
      renderTimelineEventList();
      renderTimelineEventDetail(null);
      renderReplayFrame();
    }

    async function loadReplayMapImage(mapName) {
      replayMapImage = null;
      replayMapImageName = mapName || "";
      if (!mapName) return;

      const image = new Image();
      image.decoding = "async";
      image.src = `/replay/map-assets/${encodeURIComponent(mapName)}`;
      await new Promise((resolve) => {
        image.onload = resolve;
        image.onerror = resolve;
      });
      if (image.naturalWidth > 0 && image.naturalHeight > 0 && replayMapImageName === mapName) {
        replayMapImage = image;
      }
    }

    function timelineDuration(timeline) {
      const times = [];
      for (const sample of timeline.positions || []) times.push(eventTime(sample));
      for (const event of timeline.landings || []) times.push(eventTime(event));
      for (const event of timeline.combat_events || []) times.push(eventTime(event));
      for (const event of timeline.care_packages || []) times.push(eventTime(event));
      for (const event of timeline.phase_events || []) times.push(eventTime(event));
      const matchDuration = Number(timeline.match?.duration_seconds || 0);
      if (Number.isFinite(matchDuration) && matchDuration > 0) times.push(matchDuration);
      return Math.max(0, ...times.filter((value) => Number.isFinite(value)));
    }

    function eventTime(event) {
      const elapsed = Number(event?.elapsed_time_seconds);
      if (Number.isFinite(elapsed)) return elapsed;
      const t = Number(event?.t);
      return Number.isFinite(t) ? t : 0;
    }

    function timelineEvents(timeline) {
      const events = [];
      let sequence = 0;
      const add = (category, source, label, meta) => {
        const time = eventTime(source);
        if (!Number.isFinite(time)) return;
        events.push({
          id: `${category}-${sequence}-${source?.event_index ?? "x"}`,
          sequence,
          category,
          time,
          event_index: Number(source?.event_index ?? 0),
          label,
          meta,
          source,
        });
        sequence += 1;
      };

      for (const event of timeline.landings || []) {
        add("landing", event, "Landing", `${distanceM(event.distance_m)} from plane`);
      }
      for (const event of timeline.combat_events || []) {
        const action = combatActionLabel(event.action);
        const weapon = event.damage_causer_label || event.damage_causer_name || "-";
        const suffix = event.is_headshot ? " / HS" : "";
        const related = event.related_name || event.related_account_id;
        const baseMeta = isReviveAction(event.action)
          ? [event.damage_reason || "Revive", distanceM(event.distance_m)]
          : [weapon, distanceM(event.distance_m)];
        const meta = baseMeta.concat(related ? [related] : []).join(" / ");
        add("combat", event, `${action}${suffix}`, meta);
      }
      for (const event of timeline.care_packages || []) {
        const label = event.event_type === "LogCarePackageLand" ? "Care package landed" : "Care package spawned";
        add("care", event, label, `${event.item_count || 0} items`);
      }

      return events.sort((left, right) => (
        left.time - right.time
        || left.event_index - right.event_index
        || left.sequence - right.sequence
      ));
    }

    function combatActionLabel(action) {
      const labels = {
        dbno_caused: "DBNO caused",
        dbno_taken: "DBNO taken",
        kill: "Kill",
        death: "Death",
        finish: "Finish",
        finished_taken: "Finished taken",
        revive_given: "Revive given",
        revive_received: "Revived",
      };
      return labels[action] || action || "Combat";
    }

    function isReviveAction(action) {
      return action === "revive_given" || action === "revive_received";
    }

    function renderTimelineTeamList() {
      if (!timelineTeamList) return;
      const members = activeTimeline?.team?.members || [];
      if (!members.length) {
        timelineTeamList.innerHTML = `<div class="status">Team data unavailable</div>`;
        return;
      }
      timelineTeamList.innerHTML = members.map((member) => {
        const badges = [];
        if (member.is_self) badges.push("self");
        if (member.registered && !member.is_self) badges.push("registered");
        if (member.is_ai_or_bot) badges.push("bot");
        if (member.position_sample_count > 0 && !member.is_self) badges.push("route");
        const stats = [
          `K ${Number(member.kills || 0)}`,
          `A ${Number(member.assists || 0)}`,
          `DMG ${Number(member.damage_dealt || 0).toFixed(0)}`,
          member.position_sample_count > 0 ? `Route ${Number(member.position_sample_count || 0)}` : "",
          member.win_place ? `#${member.win_place}` : "",
        ].filter(Boolean).join(" / ");
        return `
          <div class="team-member ${member.is_self ? "self" : ""} ${member.registered && !member.is_self ? "registered" : ""}">
            <strong>${escapeHtml(member.name || member.account_id || "unknown")}</strong>
            <span>${escapeHtml(badges.join(" / ") || "team")}</span>
            <span>${escapeHtml(stats || "-")}</span>
          </div>
        `;
      }).join("");
    }

    function formatReplayTime(value) {
      const seconds = Math.max(0, Number(value || 0));
      const minutes = Math.floor(seconds / 60);
      const rest = seconds - minutes * 60;
      return `${minutes}:${rest.toFixed(1).padStart(4, "0")}`;
    }

    function renderTimelineEventList() {
      if (!timelineEventList) return;
      if (!activeTimelineEvents.length) {
        timelineEventList.innerHTML = `<div class="status">표시할 timeline 이벤트가 없습니다.</div>`;
        return;
      }
      timelineEventList.innerHTML = activeTimelineEvents.slice(0, 250).map((event) => `
        <button class="timeline-event-row ${event.id === activeTimelineSelectedEventId ? "active" : ""}" type="button" data-timeline-event="${attr(event.id)}">
          <span>${formatReplayTime(event.time)}</span>
          <strong>${escapeHtml(event.label)}</strong>
          <em>${escapeHtml(event.meta || "")}</em>
        </button>
      `).join("");
    }

    function renderTimelineEventDetail(event) {
      if (!timelineEventDetail) return;
      const selected = event || selectedTimelineEvent();
      const nearest = selected || nearestTimelineEvent(activeTimelineTime, 2.5);
      const key = nearest ? `${nearest.id}:${activeTimelineSelectedEventId || ""}:${activeTimelineTime.toFixed(1)}` : `empty:${activeTimelineEvents.length}`;
      if (key === activeTimelineDetailKey) return;
      activeTimelineDetailKey = key;

      if (!nearest) {
        timelineEventDetail.className = "timeline-event-detail status";
        timelineEventDetail.innerHTML = activeTimelineEvents.length
          ? `이벤트를 선택하거나 재생 시점이 이벤트에 가까워지면 상세가 표시됩니다.`
          : `이 timeline에는 상세 이벤트가 없습니다.`;
        return;
      }

      const source = nearest.source || {};
      const detailLines = [
        `<strong>${escapeHtml(nearest.label)}</strong>`,
        `time ${formatReplayTime(nearest.time)} / index ${nearest.event_index || "-"}`,
      ];
      if (nearest.category === "combat") {
        if (isReviveAction(source.action)) {
          detailLines.push(`method ${escapeHtml(source.damage_reason || "Revive")} / distance ${distanceM(source.distance_m)}`);
        } else {
          detailLines.push(`weapon ${escapeHtml(source.damage_causer_label || source.damage_causer_name || "-")}`);
          detailLines.push(`reason ${escapeHtml(source.damage_reason || "-")} / distance ${distanceM(source.distance_m)}`);
        }
        const relatedLabel = combatRelatedLabel(source);
        if (relatedLabel) detailLines.push(`related ${relatedLabel}`);
      } else if (nearest.category === "care") {
        detailLines.push(`type ${escapeHtml(source.event_type || "-")} / items ${source.item_count || 0}`);
        const itemCodes = (source.item_codes || []).slice(0, 8).join(", ");
        if (itemCodes) detailLines.push(`items ${escapeHtml(itemCodes)}`);
      } else if (nearest.category === "landing") {
        detailLines.push(`landing distance ${distanceM(source.distance_m)}`);
      }
      if (source.event_at_kst) detailLines.push(`KST ${escapeHtml(source.event_at_kst)}`);
      timelineEventDetail.className = "timeline-event-detail";
      timelineEventDetail.innerHTML = detailLines.join("<br>");
    }

    function combatRelatedLabel(source) {
      const name = source.related_name || source.related_account_id;
      if (!name) return "";
      const badges = [];
      if (source.related_registered) badges.push("registered");
      if (source.related_is_ai_or_bot) badges.push("bot");
      return `${escapeHtml(name)}${badges.length ? ` (${escapeHtml(badges.join(", "))})` : ""}`;
    }

    function selectedTimelineEvent() {
      return activeTimelineEvents.find((event) => event.id === activeTimelineSelectedEventId) || null;
    }

    function nearestTimelineEvent(time, windowSeconds) {
      let best = null;
      let bestDelta = Infinity;
      for (const event of activeTimelineEvents) {
        const delta = Math.abs(event.time - time);
        if (delta <= windowSeconds && delta < bestDelta) {
          best = event;
          bestDelta = delta;
        }
      }
      return best;
    }

    function seekTimelineEvent(eventId) {
      const event = activeTimelineEvents.find((item) => item.id === eventId);
      if (!event) return;
      pauseReplay();
      activeTimelineSelectedEventId = event.id;
      activeTimelineTime = Math.max(0, Math.min(activeTimelineDuration, event.time));
      renderTimelineEventList();
      renderTimelineEventDetail(event);
      renderReplayFrame();
    }

    function renderReplayFrame() {
      if (!activeTimeline || !replayCtx) {
        drawEmptyReplayCanvas();
        return;
      }

      const width = replayCanvas.width;
      const height = replayCanvas.height;
      replayCtx.clearRect(0, 0, width, height);
      drawReplayBackground(width, height);

      if (timelineShowPhase.checked) drawReplayPhaseRings(activeTimeline.phase_events || []);
      if (timelineShowPlane.checked) drawReplayPlaneRoute(activeTimeline.plane_route);
      if (timelineShowCare.checked) drawReplayCarePackages(activeTimeline.care_packages || []);
      if (timelineShowPath.checked) drawReplayPath(activeTimeline.positions || []);
      if (timelineShowTeam.checked) drawReplayTeamTracks(activeTimeline.team_tracks || []);
      drawReplayLandings(activeTimeline.landings || []);
      if (timelineShowCombat.checked) drawReplayCombatEvents(activeTimeline.combat_events || []);
      drawReplaySelectedEvent();
      drawReplayPlayer(activeTimeline.positions || []);
      drawReplayOverlay();
      renderTimelineEventDetail(null);
      timelineClock.textContent = `${activeTimelineTime.toFixed(1)}초`;
      timelineScrubber.value = String(activeTimelineTime);
    }

    function drawReplayBackground(width, height) {
      const viewport = replayViewport();
      if (replayMapImage) {
        replayCtx.drawImage(
          replayMapImage,
          viewport.x * replayMapImage.naturalWidth,
          viewport.y * replayMapImage.naturalHeight,
          viewport.size * replayMapImage.naturalWidth,
          viewport.size * replayMapImage.naturalHeight,
          0,
          0,
          width,
          height,
        );
        replayCtx.fillStyle = "rgba(10,16,22,0.16)";
        replayCtx.fillRect(0, 0, width, height);
      } else {
        replayCtx.fillStyle = "#17212b";
        replayCtx.fillRect(0, 0, width, height);
      }
      replayCtx.strokeStyle = "rgba(255,255,255,0.12)";
      replayCtx.lineWidth = 1;
      for (let index = 0; index <= 8; index += 1) {
        const mapPosition = index / 8;
        const vertical = canvasPoint({ x_pct: mapPosition, y_pct: viewport.y });
        const horizontal = canvasPoint({ x_pct: viewport.x, y_pct: mapPosition });
        replayCtx.beginPath();
        replayCtx.moveTo(vertical.x, 0);
        replayCtx.lineTo(vertical.x, height);
        replayCtx.moveTo(0, horizontal.y);
        replayCtx.lineTo(width, horizontal.y);
        replayCtx.stroke();
      }
    }

    function drawReplayPlaneRoute(route) {
      if (!route?.start?.map || !route?.end?.map) return;
      const start = canvasPoint(route.start.map);
      const end = canvasPoint(route.end.map);
      replayCtx.strokeStyle = "rgba(53,162,235,0.95)";
      replayCtx.lineWidth = 4;
      replayCtx.beginPath();
      replayCtx.moveTo(start.x, start.y);
      replayCtx.lineTo(end.x, end.y);
      replayCtx.stroke();
      drawCircle(start, 7, "#ffffff", "#1976d2");
      drawCircle(end, 7, "#ffffff", "#1976d2");
    }

    function drawReplayPhaseRings(events) {
      const phase = activePhaseEvent(events);
      if (!phase) return;
      drawMapCircle(phase.poison_gas_warning, "rgba(33,150,243,0.78)", "rgba(33,150,243,0.05)", [10, 8], 3);
      drawMapCircle(phase.safety_zone, "rgba(76,175,80,0.92)", "rgba(76,175,80,0.08)", [], 4);
      drawMapCircle(phase.red_zone, "rgba(244,67,54,0.82)", "rgba(244,67,54,0.10)", [6, 6], 2);
      drawMapCircle(phase.black_zone, "rgba(33,33,33,0.84)", "rgba(33,33,33,0.14)", [4, 5], 2);
      replayCtx.setLineDash([]);
    }

    function activePhaseEvent(events) {
      let current = null;
      for (const event of events || []) {
        if (eventTime(event) <= activeTimelineTime) current = event;
        else break;
      }
      return current;
    }

    function drawMapCircle(circle, stroke, fill, dash, lineWidth) {
      const radiusPct = Number(circle?.map?.radius_pct);
      if (!circle?.map || !Number.isFinite(radiusPct) || radiusPct <= 0) return;
      const center = canvasPoint(circle.map);
      const radius = (radiusPct / replayViewport().size) * replayCanvas.width;
      if (!Number.isFinite(radius) || radius <= 0) return;
      replayCtx.beginPath();
      replayCtx.arc(center.x, center.y, radius, 0, Math.PI * 2);
      replayCtx.fillStyle = fill;
      replayCtx.fill();
      replayCtx.strokeStyle = stroke;
      replayCtx.lineWidth = lineWidth;
      replayCtx.setLineDash(dash || []);
      replayCtx.stroke();
      replayCtx.setLineDash([]);
    }

    function drawReplayCarePackages(events) {
      for (const event of events) {
        if (eventTime(event) > activeTimelineTime || !event.map) continue;
        const point = canvasPoint(event.map);
        replayCtx.fillStyle = event.event_type === "LogCarePackageLand" ? "rgba(211,47,47,0.85)" : "rgba(255,193,7,0.7)";
        replayCtx.strokeStyle = "rgba(255,255,255,0.65)";
        replayCtx.lineWidth = 1;
        replayCtx.fillRect(point.x - 5, point.y - 5, 10, 10);
        replayCtx.strokeRect(point.x - 5, point.y - 5, 10, 10);
      }
    }

    function drawReplayPath(samples) {
      const visible = visiblePositionSamples(samples);
      if (visible.length < 2) return;
      replayCtx.strokeStyle = "rgba(57,255,20,0.9)";
      replayCtx.lineWidth = 4;
      replayCtx.beginPath();
      visible.forEach((sample, index) => {
        const point = canvasPoint(sample.map);
        if (index === 0) replayCtx.moveTo(point.x, point.y);
        else replayCtx.lineTo(point.x, point.y);
      });
      replayCtx.stroke();
    }

    function drawReplayTeamTracks(tracks) {
      tracks.forEach((track, index) => {
        const samples = track.positions || [];
        const visible = visiblePositionSamples(samples);
        const color = teamTrackColor(index, Boolean(track.registered));
        if (visible.length >= 2) {
          replayCtx.strokeStyle = color;
          replayCtx.lineWidth = track.registered ? 3 : 2;
          replayCtx.setLineDash([9, 7]);
          replayCtx.beginPath();
          visible.forEach((sample, sampleIndex) => {
            const point = canvasPoint(sample.map);
            if (sampleIndex === 0) replayCtx.moveTo(point.x, point.y);
            else replayCtx.lineTo(point.x, point.y);
          });
          replayCtx.stroke();
          replayCtx.setLineDash([]);
        }

        const current = interpolatedPosition(samples, activeTimelineTime);
        if (!current) return;
        const point = canvasPoint(current);
        if (!canvasPointVisible(point, 16)) return;
        drawCircle(point, track.registered ? 7 : 6, track.registered ? "#ffffff" : color, color);
        drawReplayLabel(point, track.name || track.account_id || "team", color);
      });
      replayCtx.setLineDash([]);
    }

    function drawReplayLandings(events) {
      replayCtx.fillStyle = "rgba(255,235,59,0.95)";
      replayCtx.strokeStyle = "rgba(20,20,20,0.8)";
      replayCtx.lineWidth = 2;
      for (const event of events) {
        if (eventTime(event) > activeTimelineTime || !event.map) continue;
        const point = canvasPoint(event.map);
        replayCtx.beginPath();
        replayCtx.moveTo(point.x, point.y - 12);
        replayCtx.lineTo(point.x - 10, point.y + 8);
        replayCtx.lineTo(point.x + 10, point.y + 8);
        replayCtx.closePath();
        replayCtx.fill();
        replayCtx.stroke();
      }
    }

    function drawReplayCombatEvents(events) {
      for (const event of events) {
        if (eventTime(event) > activeTimelineTime || !event.map) continue;
        const point = canvasPoint(event.map);
        if (event.related_map && ["dbno_caused", "kill", "finish", "revive_given"].includes(event.action)) {
          const related = canvasPoint(event.related_map);
          replayCtx.strokeStyle = "rgba(255,255,255,0.35)";
          replayCtx.lineWidth = 1;
          replayCtx.beginPath();
          replayCtx.moveTo(point.x, point.y);
          replayCtx.lineTo(related.x, related.y);
          replayCtx.stroke();
        }
        if (isReviveAction(event.action)) {
          drawPlus(point, event.action === "revive_given" ? "#00bcd4" : "#26a69a");
        } else if (["kill", "finish"].includes(event.action)) {
          drawX(point, event.is_headshot ? "#ff1744" : "#ef5350");
        } else if (event.action === "dbno_caused") {
          drawCircle(point, 8, "#ff9800", "#202020");
        } else if (["death", "finished_taken", "dbno_taken"].includes(event.action)) {
          drawCircle(point, 9, "#202020", "#ef5350");
        }
      }
    }

    function drawReplaySelectedEvent() {
      const selected = selectedTimelineEvent();
      const mapPoint = selected?.source?.map;
      if (!mapPoint) return;
      const point = canvasPoint(mapPoint);
      replayCtx.strokeStyle = "rgba(255,255,255,0.95)";
      replayCtx.lineWidth = 3;
      replayCtx.beginPath();
      replayCtx.arc(point.x, point.y, 18, 0, Math.PI * 2);
      replayCtx.stroke();
      replayCtx.strokeStyle = "rgba(22,119,199,0.95)";
      replayCtx.lineWidth = 2;
      replayCtx.beginPath();
      replayCtx.arc(point.x, point.y, 24, 0, Math.PI * 2);
      replayCtx.stroke();
    }

    function drawReplayPlayer(samples) {
      const current = interpolatedPosition(samples, activeTimelineTime);
      if (!current) return;
      drawCircle(canvasPoint(current), 8, "#ffffff", "#39ff14");
    }

    function drawReplayOverlay() {
      replayCtx.fillStyle = "rgba(17,24,32,0.82)";
      replayCtx.fillRect(12, 12, 360, 88);
      replayCtx.fillStyle = "#f5f7fa";
      replayCtx.font = "14px Arial";
      replayCtx.fillText(activeTimelineArtifact?.match_id || activeTimeline?.match?.match_id || "-", 24, 36);
      replayCtx.fillStyle = "#c3ccd6";
      replayCtx.fillText(`${activeTimeline?.match?.map_name || "-"} / ${activeTimeline?.match?.game_mode || "-"} / ${activeTimelineTime.toFixed(1)}s`, 24, 60);
      replayCtx.fillText(`view ${replayZoom().toFixed(1)}x / follow ${timelineFollowPlayer.checked ? "on" : "off"}`, 24, 84);
    }

    function drawReplayLabel(point, label, color) {
      const text = String(label || "").slice(0, 18);
      if (!text) return;
      replayCtx.font = "12px Arial";
      const width = Math.min(150, replayCtx.measureText(text).width + 12);
      const x = Math.max(4, Math.min(replayCanvas.width - width - 4, point.x + 10));
      const y = Math.max(18, Math.min(replayCanvas.height - 8, point.y - 8));
      replayCtx.fillStyle = "rgba(17,24,32,0.78)";
      replayCtx.fillRect(x, y - 14, width, 18);
      replayCtx.strokeStyle = color;
      replayCtx.lineWidth = 1;
      replayCtx.strokeRect(x, y - 14, width, 18);
      replayCtx.fillStyle = "#f5f7fa";
      replayCtx.fillText(text, x + 6, y);
    }

    function teamTrackColor(index, registered) {
      const registeredColors = ["#00bcd4", "#ffca28", "#ab47bc", "#26a69a"];
      const defaultColors = ["#90a4ae", "#ffab91", "#b0bec5", "#a5d6a7"];
      const colors = registered ? registeredColors : defaultColors;
      return colors[index % colors.length];
    }

    function visiblePositionSamples(samples) {
      return samples
        .filter((sample) => sample.map && eventTime(sample) <= activeTimelineTime)
        .concat(interpolatedPosition(samples, activeTimelineTime) ? [{ map: interpolatedPosition(samples, activeTimelineTime), elapsed_time_seconds: activeTimelineTime }] : []);
    }

    function interpolatedPosition(samples, time) {
      const valid = samples.filter((sample) => sample.map);
      if (!valid.length) return null;
      let previous = valid[0];
      for (const sample of valid) {
        const sampleTime = eventTime(sample);
        if (sampleTime >= time) {
          const prevTime = eventTime(previous);
          const ratio = sampleTime === prevTime ? 0 : Math.max(0, Math.min(1, (time - prevTime) / (sampleTime - prevTime)));
          return {
            x_pct: previous.map.x_pct + (sample.map.x_pct - previous.map.x_pct) * ratio,
            y_pct: previous.map.y_pct + (sample.map.y_pct - previous.map.y_pct) * ratio,
          };
        }
        previous = sample;
      }
      return previous.map;
    }

    function canvasPoint(mapPoint) {
      const viewport = replayViewport();
      return {
        x: ((Math.max(0, Math.min(1, Number(mapPoint.x_pct || 0))) - viewport.x) / viewport.size) * replayCanvas.width,
        y: ((Math.max(0, Math.min(1, Number(mapPoint.y_pct || 0))) - viewport.y) / viewport.size) * replayCanvas.height,
      };
    }

    function canvasPointVisible(point, margin = 0) {
      return (
        point.x >= -margin
        && point.x <= replayCanvas.width + margin
        && point.y >= -margin
        && point.y <= replayCanvas.height + margin
      );
    }

    function replayViewport() {
      const zoom = replayZoom();
      const size = 1 / zoom;
      const center = replayViewportCenter();
      return {
        x: Math.max(0, Math.min(1 - size, center.x_pct - size / 2)),
        y: Math.max(0, Math.min(1 - size, center.y_pct - size / 2)),
        size,
      };
    }

    function replayViewportCenter() {
      if (timelineFollowPlayer?.checked && activeTimeline) {
        const current = interpolatedPosition(activeTimeline.positions || [], activeTimelineTime);
        if (current) return current;
      }
      return { x_pct: 0.5, y_pct: 0.5 };
    }

    function replayZoom() {
      const value = Number(timelineZoom?.value || 1);
      return Number.isFinite(value) ? Math.max(1, Math.min(4, value)) : 1;
    }

    function drawCircle(point, radius, fill, stroke) {
      replayCtx.beginPath();
      replayCtx.arc(point.x, point.y, radius, 0, Math.PI * 2);
      replayCtx.fillStyle = fill;
      replayCtx.strokeStyle = stroke;
      replayCtx.lineWidth = 2;
      replayCtx.fill();
      replayCtx.stroke();
    }

    function drawX(point, color) {
      replayCtx.strokeStyle = color;
      replayCtx.lineWidth = 5;
      replayCtx.beginPath();
      replayCtx.moveTo(point.x - 9, point.y - 9);
      replayCtx.lineTo(point.x + 9, point.y + 9);
      replayCtx.moveTo(point.x + 9, point.y - 9);
      replayCtx.lineTo(point.x - 9, point.y + 9);
      replayCtx.stroke();
    }

    function drawPlus(point, color) {
      drawCircle(point, 8, "rgba(255,255,255,0.72)", color);
      replayCtx.strokeStyle = color;
      replayCtx.lineWidth = 5;
      replayCtx.beginPath();
      replayCtx.moveTo(point.x - 10, point.y);
      replayCtx.lineTo(point.x + 10, point.y);
      replayCtx.moveTo(point.x, point.y - 10);
      replayCtx.lineTo(point.x, point.y + 10);
      replayCtx.stroke();
    }

    function toggleReplayPlayback() {
      if (!activeTimeline) return;
      if (replayPlaying) pauseReplay();
      else playReplay();
    }

    function playReplay() {
      replayPlaying = true;
      replayLastFrameMs = performance.now();
      timelinePlayButton.textContent = "일시정지";
      replayAnimationId = requestAnimationFrame(stepReplay);
    }

    function pauseReplay() {
      replayPlaying = false;
      timelinePlayButton.textContent = "재생";
      if (replayAnimationId) cancelAnimationFrame(replayAnimationId);
      replayAnimationId = null;
    }

    function stepReplay(frameMs) {
      if (!replayPlaying) return;
      const speed = Number(timelineSpeed.value || 1);
      const deltaSeconds = Math.max(0, (frameMs - replayLastFrameMs) / 1000) * speed;
      replayLastFrameMs = frameMs;
      activeTimelineTime = Math.min(activeTimelineDuration, activeTimelineTime + deltaSeconds);
      renderReplayFrame();
      if (activeTimelineTime >= activeTimelineDuration) {
        pauseReplay();
        return;
      }
      replayAnimationId = requestAnimationFrame(stepReplay);
    }

    function resetReplay() {
      pauseReplay();
      activeTimelineTime = 0;
      activeTimelineSelectedEventId = null;
      renderTimelineEventList();
      renderTimelineEventDetail(null);
      renderReplayFrame();
    }

    function drawEmptyReplayCanvas() {
      if (!replayCtx) return;
      drawReplayBackground(replayCanvas.width, replayCanvas.height);
      replayCtx.fillStyle = "#c3ccd6";
      replayCtx.font = "16px Arial";
      replayCtx.fillText("No timeline", 24, 36);
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
      movementStatus.textContent = `파싱 ${payload.result.parsed_payloads}개, 위치 ${payload.result.position_samples}개, 전투위치 ${payload.result.combat_location_events}개, 보급 ${payload.result.care_package_events}개, 비행기 ${payload.result.plane_routes}개, 자기장 ${payload.result.phase_events || 0}개, 실패 ${payload.result.failed_payloads}개`;
      banner.textContent = "위치 파싱 완료";
    }

    async function generateLoadoutSnapshots(force) {
      banner.textContent = force ? "Loadout snapshot 재생성 중" : "Loadout snapshot 생성 중";
      const response = await fetch("/telemetry/loadout-snapshots/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ limit: 50, force }),
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || response.statusText);
      }
      const payload = await response.json();
      loadoutSnapshotStatus.textContent = `처리 ${payload.result.processed_matches}개, 기존 ${payload.result.skipped_existing}개, item없음 ${payload.result.skipped_no_items}개, 실패 ${payload.result.failed_matches}개, snapshot ${payload.result.generated_snapshots}개`;
      banner.textContent = "Loadout snapshot 생성 완료";
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

    async function generateReplayTimelines(force) {
      banner.textContent = force ? "Timeline 재생성 중" : "Timeline 생성 중";
      const response = await fetch("/replay/timelines/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ limit: 10, force }),
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || response.statusText);
      }
      const payload = await response.json();
      timelineStatus.textContent = `생성 ${payload.result.generated_timelines}개, 기존 ${payload.result.skipped_existing}개, 위치없음 ${payload.result.skipped_no_position}개, 실패 ${payload.result.failed_timelines}개, artifact ${payload.result.artifacts.length}개`;
      banner.textContent = "Timeline 생성 완료";
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

    async function saveWebSettings(event) {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const localWebBaseUrl = String(form.get("local_web_base_url") || "").trim();
      const payload = await postJson("/settings/web", {
        local_web_base_url: localWebBaseUrl || null,
      });
      webSettingsStatus.textContent = payload.web.local_web_base_url
        ? `Saved: ${payload.web.local_web_base_url}`
        : "Saved: disabled";
      await loadStatus();
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

    document.querySelector("#recommendationForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      try {
        await loadPlayerRecommendations(
          String(form.get("target") || ""),
          String(form.get("shard") || "steam"),
          Number(form.get("min_matches") || 1),
        );
        banner.textContent = "Recommendation 조회 완료";
      } catch (error) {
        recommendationBody.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    recommendationBody.addEventListener("click", async (event) => {
      const button = event.target.closest("button[data-evidence='weapon-attachment']");
      if (!button) return;
      try {
        await loadWeaponAttachmentEvidence(
          button.dataset.weaponCode || "",
          button.dataset.attachmentCode || "",
        );
        banner.textContent = "추천 근거 조회 완료";
      } catch (error) {
        const panel = document.querySelector("#recommendationEvidence");
        if (panel) panel.textContent = `오류: ${error.message}`;
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

    webSettingsForm.addEventListener("submit", async (event) => {
      try {
        await saveWebSettings(event);
        banner.textContent = "Local web link settings saved";
      } catch (error) {
        event.preventDefault();
        webSettingsStatus.textContent = `Error: ${error.message}`;
        banner.textContent = `Error: ${error.message}`;
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

    replayArtifactsBody.addEventListener("click", async (event) => {
      const button = event.target instanceof Element
        ? event.target.closest("button[data-load-timeline]")
        : null;
      if (!button) return;

      timelineSelect.value = button.dataset.loadTimeline || "";
      try {
        await loadSelectedTimeline();
        banner.textContent = "Replay timeline 로드 완료";
      } catch (error) {
        replayPlayerStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    timelineSelect.addEventListener("change", async () => {
      try {
        await loadSelectedTimeline();
      } catch (error) {
        replayPlayerStatus.textContent = `오류: ${error.message}`;
      }
    });

    timelinePlayButton.addEventListener("click", toggleReplayPlayback);
    timelineResetButton.addEventListener("click", resetReplay);
    timelineScrubber.addEventListener("input", () => {
      activeTimelineSelectedEventId = null;
      activeTimelineTime = Number(timelineScrubber.value || 0);
      renderTimelineEventList();
      renderReplayFrame();
    });
    timelineEventList.addEventListener("click", (event) => {
      const button = event.target instanceof Element
        ? event.target.closest("button[data-timeline-event]")
        : null;
      if (!button) return;
      seekTimelineEvent(button.dataset.timelineEvent || "");
    });
    for (const toggle of [timelineShowPath, timelineShowCombat, timelineShowCare, timelineShowPlane, timelineShowPhase, timelineShowTeam, timelineFollowPlayer]) {
      toggle.addEventListener("change", renderReplayFrame);
    }
    timelineZoom.addEventListener("change", renderReplayFrame);

    drawEmptyReplayCanvas();
    Promise.all([loadStatus(), loadDiscordPermissions(), loadPlayers(), loadJobs(), loadTelemetryJobs(), loadReplayArtifacts()])
      .then(() => { banner.textContent = "localhost 전용 관리 화면"; })
      .catch((error) => { banner.textContent = `오류: ${error.message}`; });
  </script>
</body>
</html>
"""
