from __future__ import annotations

from contextlib import asynccontextmanager
import csv
import io
import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field

from pubg_ai.alert_history import (
    ALERT_HISTORY_EXPORT_LIMIT,
    AlertHistoryError,
    AlertHistoryRecord,
    add_alert_note,
    acknowledge_alert,
    get_alert_history_page,
    get_alert_history_record,
    list_alert_history,
    list_alert_notes,
    snooze_alert,
    sync_alert_history,
    visible_alert_records,
)
from pubg_ai.collector_worker import CollectorWorkerController, CollectorWorkerError, CollectorWorkerOptions
from pubg_ai.config import RuntimeConfig, load_dotenv_values
from pubg_ai.data_deletion_requests import DataDeletionRequestError, DataDeletionRequestService
from pubg_ai.database import connect_mysql, count_tables
from pubg_ai.discord_permission_manager import DiscordPermissionManager
from pubg_ai.local_settings import LocalSettingsError, LocalSettingsStore, check_storage_path
from pubg_ai.loadout_snapshot_processor import LoadoutSnapshotProcessor
from pubg_ai.map_snapshot_renderer import MAP_ASSET_FILENAMES, MapAssetProvider, MapSnapshotProcessor
from pubg_ai.match_collection import RegisteredPlayerMatchCollector
from pubg_ai.match_job_processor import MatchJobProcessor
from pubg_ai.player_registry import DiscordCommandContext, PlayerRegistry
from pubg_ai.player_rankings import PlayerRankingService
from pubg_ai.post_processing_worker import (
    PostProcessingWorkerController,
    PostProcessingWorkerError,
    PostProcessingWorkerOptions,
)
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
from pubg_ai.system_alerts import collect_system_alerts
from pubg_ai.worker_run_history import (
    WORKER_RUN_EXPORT_LIMIT,
    WorkerRunHistoryError,
    WorkerRunRecord,
    get_worker_run,
    get_worker_run_page,
    list_worker_runs,
)


class RegisterPlayerRequest(BaseModel):
    account_id: str | None = None
    shard: str = Field(default="steam", min_length=1)
    current_name: str = Field(min_length=1)
    public_profile: bool | None = None
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


class DiscordScopeSettingsRequest(BaseModel):
    guild_ranking_scopes: dict[str, str] = Field(default_factory=dict)
    public_profile_default: bool = True


class DataDeletionReviewRequest(BaseModel):
    actor_id: str = Field(default="local-manager", min_length=1, max_length=191)
    note: str | None = Field(default=None, max_length=1000)


class WebSettingsRequest(BaseModel):
    local_web_base_url: str | None = None


class StorageSettingsRequest(BaseModel):
    raw_data_dir: str = Field(min_length=1)
    replay_data_dir: str = Field(min_length=1)
    raw_compression: str = "gzip"


class CollectorSettingsRequest(BaseModel):
    poll_interval_seconds: int = Field(ge=60, le=300)
    cycle_player_limit: int = Field(ge=1, le=100)
    player_lookup_chunk_size: int = Field(ge=1, le=10)


class AlertSettingsRequest(BaseModel):
    minimum_free_bytes: int = Field(ge=0)
    discord_channel_ids: list[str] = Field(default_factory=list)
    storage_alerts_enabled: bool = True
    worker_error_alerts_enabled: bool = True


class AlertSnoozeRequest(BaseModel):
    minutes: int = Field(default=60, ge=1, le=43200)


class AlertNoteRequest(BaseModel):
    note_text: str = Field(min_length=1, max_length=5000)
    note_type: str = Field(default="note", max_length=32)
    created_by: str | None = Field(default="local-manager", max_length=191)


class CollectorWorkerStartRequest(BaseModel):
    shard: str | None = None
    match_job_limit: int = Field(default=10, ge=1, le=500)
    telemetry_job_limit: int = Field(default=5, ge=1, le=200)


class PostProcessingWorkerStartRequest(BaseModel):
    combat_limit: int = Field(default=10, ge=1, le=200)
    item_limit: int = Field(default=10, ge=1, le=200)
    movement_limit: int = Field(default=10, ge=1, le=200)
    loadout_limit: int = Field(default=50, ge=1, le=500)
    map_snapshot_limit: int = Field(default=10, ge=1, le=200)
    timeline_limit: int = Field(default=10, ge=1, le=200)
    force: bool = False


def create_app() -> Any:
    base_dir = Path.cwd()
    config = RuntimeConfig.from_sources(base_dir=base_dir)
    settings_store = _local_settings_store(base_dir)
    permission_manager = DiscordPermissionManager(settings_store)

    def current_config() -> RuntimeConfig:
        return RuntimeConfig.from_sources(base_dir=base_dir)
    collector_worker = CollectorWorkerController(config_loader=current_config)
    post_processing_worker = PostProcessingWorkerController(config_loader=current_config)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> Any:
        try:
            yield
        finally:
            collector_worker.stop()
            post_processing_worker.stop()

    app = FastAPI(
        title="PUBG AI Local Manager",
        version="0.1.0",
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
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
        return _settings_status_record(current_config())

    @app.post("/settings/web")
    def save_web_settings(request: WebSettingsRequest) -> dict[str, Any]:
        try:
            web_settings = settings_store.save_web_settings(request.local_web_base_url)
        except LocalSettingsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "web": web_settings.to_record(),
            "settings": _settings_status_record(current_config()),
        }

    @app.post("/settings/storage")
    def save_storage_settings(request: StorageSettingsRequest) -> dict[str, Any]:
        try:
            storage_settings = settings_store.save_storage_settings(
                raw_data_dir=request.raw_data_dir,
                replay_data_dir=request.replay_data_dir,
                raw_compression=request.raw_compression,
            )
            storage_status = {
                key: value.to_record()
                for key, value in settings_store.get_storage_status().items()
            }
        except LocalSettingsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "storage": storage_settings.to_record(),
            "storage_status": storage_status,
            "settings": _settings_status_record(current_config()),
        }

    @app.post("/settings/collector")
    def save_collector_settings(request: CollectorSettingsRequest) -> dict[str, Any]:
        try:
            collector_settings = settings_store.save_collector_settings(
                poll_interval_seconds=request.poll_interval_seconds,
                cycle_player_limit=request.cycle_player_limit,
                player_lookup_chunk_size=request.player_lookup_chunk_size,
            )
        except LocalSettingsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "collector": collector_settings.to_record(),
            "settings": _settings_status_record(current_config()),
        }

    @app.get("/alerts/status")
    def alerts_status() -> dict[str, Any]:
        return _alerts_status_record(settings_store, current_config())

    @app.get("/alerts/history")
    def alert_history(
        source: str = "all",
        state: str = "all",
        severity: str = "all",
        sort: str = "newest",
        search: str = "",
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        connection = connect_mysql(current_config().database)
        try:
            try:
                page = get_alert_history_page(
                    connection,
                    source=source,
                    state=state,
                    severity=severity,
                    sort=sort,
                    search=search,
                    limit=limit,
                    offset=offset,
                )
            except AlertHistoryError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            connection.close()
        return _alert_history_page_record(page)

    @app.get("/alerts/history/export.csv")
    def export_alert_history(
        source: str = "all",
        state: str = "all",
        severity: str = "all",
        sort: str = "newest",
        search: str = "",
        limit: int = Query(default=ALERT_HISTORY_EXPORT_LIMIT, ge=1, le=ALERT_HISTORY_EXPORT_LIMIT),
        offset: int = Query(default=0, ge=0),
    ) -> Response:
        connection = connect_mysql(current_config().database)
        try:
            try:
                records = list_alert_history(
                    connection,
                    source=source,
                    state=state,
                    severity=severity,
                    sort=sort,
                    search=search,
                    limit=limit,
                    max_limit=ALERT_HISTORY_EXPORT_LIMIT,
                    offset=offset,
                )
            except AlertHistoryError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            connection.close()
        return _alert_history_csv_response(records)

    @app.get("/alerts/history/{alert_id}")
    def alert_history_record(alert_id: int) -> dict[str, Any]:
        connection = connect_mysql(current_config().database)
        try:
            try:
                record = get_alert_history_record(connection, alert_id)
                notes = list_alert_notes(connection, alert_id, limit=100)
            except AlertHistoryError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            connection.close()
        return {
            "alert": record.to_record(),
            "notes": [note.to_record() for note in notes],
        }

    @app.get("/alerts/history/{alert_id}/notes")
    def alert_history_notes(alert_id: int) -> dict[str, Any]:
        connection = connect_mysql(current_config().database)
        try:
            notes = list_alert_notes(connection, alert_id, limit=100)
        finally:
            connection.close()
        return {"notes": [note.to_record() for note in notes]}

    @app.post("/alerts/history/{alert_id}/notes")
    def add_alert_history_note(alert_id: int, request: AlertNoteRequest) -> dict[str, Any]:
        connection = connect_mysql(current_config().database)
        try:
            try:
                note = add_alert_note(
                    connection,
                    alert_id,
                    request.note_text,
                    note_type=request.note_type,
                    created_by=request.created_by,
                )
                notes = list_alert_notes(connection, alert_id, limit=100)
            except AlertHistoryError as exc:
                status_code = 404 if "not found" in str(exc) else 400
                raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        finally:
            connection.close()
        return {
            "note": note.to_record(),
            "notes": [record.to_record() for record in notes],
        }

    @app.post("/settings/alerts")
    def save_alert_settings(request: AlertSettingsRequest) -> dict[str, Any]:
        try:
            settings_store.save_alert_settings(
                minimum_free_bytes=request.minimum_free_bytes,
                discord_channel_ids=request.discord_channel_ids,
                storage_alerts_enabled=request.storage_alerts_enabled,
                worker_error_alerts_enabled=request.worker_error_alerts_enabled,
            )
        except LocalSettingsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _alerts_status_record(settings_store, current_config())

    @app.post("/alerts/history/{alert_id}/acknowledge")
    def acknowledge_alert_history(alert_id: int) -> dict[str, Any]:
        connection = connect_mysql(current_config().database)
        try:
            record = acknowledge_alert(connection, alert_id)
        except AlertHistoryError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            connection.close()
        return {
            "alert": record.to_record(),
            **_alerts_status_record(settings_store, current_config()),
        }

    @app.post("/alerts/history/{alert_id}/snooze")
    def snooze_alert_history(alert_id: int, request: AlertSnoozeRequest) -> dict[str, Any]:
        connection = connect_mysql(current_config().database)
        try:
            record = snooze_alert(connection, alert_id, request.minutes)
        except AlertHistoryError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            connection.close()
        return {
            "alert": record.to_record(),
            **_alerts_status_record(settings_store, current_config()),
        }

    @app.get("/collector/worker/status")
    def collector_worker_status() -> dict[str, Any]:
        return {"worker": collector_worker.status().to_record()}

    @app.post("/collector/worker/start")
    def start_collector_worker(request: CollectorWorkerStartRequest) -> dict[str, Any]:
        runtime_config = current_config()
        if not runtime_config.secrets.pubg_api_key:
            raise HTTPException(status_code=500, detail="PUBG_API_KEY is not configured.")
        try:
            state = collector_worker.start(
                CollectorWorkerOptions(
                    shard=request.shard.strip() if isinstance(request.shard, str) and request.shard.strip() else None,
                    match_job_limit=request.match_job_limit,
                    telemetry_job_limit=request.telemetry_job_limit,
                )
            )
        except CollectorWorkerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"worker": state.to_record()}

    @app.post("/collector/worker/stop")
    def stop_collector_worker() -> dict[str, Any]:
        return {"worker": collector_worker.stop().to_record()}

    @app.get("/post-processing/worker/status")
    def post_processing_worker_status() -> dict[str, Any]:
        return {"worker": post_processing_worker.status().to_record()}

    @app.post("/post-processing/worker/start")
    def start_post_processing_worker(request: PostProcessingWorkerStartRequest) -> dict[str, Any]:
        try:
            state = post_processing_worker.start(
                PostProcessingWorkerOptions(
                    combat_limit=request.combat_limit,
                    item_limit=request.item_limit,
                    movement_limit=request.movement_limit,
                    loadout_limit=request.loadout_limit,
                    map_snapshot_limit=request.map_snapshot_limit,
                    timeline_limit=request.timeline_limit,
                    force=request.force,
                )
            )
        except PostProcessingWorkerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"worker": state.to_record()}

    @app.post("/post-processing/worker/stop")
    def stop_post_processing_worker() -> dict[str, Any]:
        return {"worker": post_processing_worker.stop().to_record()}

    @app.get("/workers/runs")
    def worker_runs(
        worker_name: str | None = None,
        status: str = "all",
        created_from_kst: str | None = None,
        created_to_kst: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        connection = connect_mysql(current_config().database)
        try:
            page = get_worker_run_page(
                connection,
                worker_name=worker_name,
                status=status,
                created_from_kst=created_from_kst,
                created_to_kst=created_to_kst,
                limit=limit,
                offset=offset,
            )
        except WorkerRunHistoryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            connection.close()
        return {"worker_run_page": page.to_record(), "runs": [run.to_record() for run in page.records]}

    @app.get("/workers/runs/export.csv")
    def export_worker_runs(
        worker_name: str | None = None,
        status: str = "all",
        created_from_kst: str | None = None,
        created_to_kst: str | None = None,
        limit: int = Query(default=WORKER_RUN_EXPORT_LIMIT, ge=1, le=WORKER_RUN_EXPORT_LIMIT),
        offset: int = Query(default=0, ge=0),
    ) -> Response:
        connection = connect_mysql(current_config().database)
        try:
            try:
                records = list_worker_runs(
                    connection,
                    worker_name=worker_name,
                    status=status,
                    created_from_kst=created_from_kst,
                    created_to_kst=created_to_kst,
                    limit=limit,
                    max_limit=WORKER_RUN_EXPORT_LIMIT,
                    offset=offset,
                )
            except WorkerRunHistoryError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            connection.close()
        return _worker_run_csv_response(records)

    @app.get("/workers/runs/{run_id}")
    def worker_run_detail(run_id: int) -> dict[str, Any]:
        connection = connect_mysql(current_config().database)
        try:
            run = get_worker_run(connection, run_id)
        except WorkerRunHistoryError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            connection.close()
        return {"run": run.to_record()}

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

    @app.get("/discord/scopes")
    def discord_scopes() -> dict[str, Any]:
        try:
            return {"discord_scopes": settings_store.load_discord_scope_settings().to_record()}
        except LocalSettingsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/discord/scopes")
    def save_discord_scopes(request: DiscordScopeSettingsRequest) -> dict[str, Any]:
        try:
            settings = settings_store.save_discord_scope_settings(
                guild_ranking_scopes=request.guild_ranking_scopes,
                public_profile_default=request.public_profile_default,
            )
        except LocalSettingsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"discord_scopes": settings.to_record()}

    @app.get("/data-deletions")
    def data_deletion_requests(status: str = "pending", limit: int = 50) -> dict[str, Any]:
        connection = connect_mysql(current_config().database)
        try:
            try:
                requests = DataDeletionRequestService(connection).list_requests(
                    status=status,
                    limit=limit,
                )
            except DataDeletionRequestError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            connection.close()
        return {"requests": [request.to_record() for request in requests]}

    @app.get("/data-deletions/{request_id}")
    def data_deletion_request_detail(request_id: int) -> dict[str, Any]:
        connection = connect_mysql(current_config().database)
        try:
            service = DataDeletionRequestService(connection)
            try:
                request = service.get_request(request_id)
                events = service.list_events(request_id)
            except DataDeletionRequestError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            connection.close()
        return {
            "request": request.to_record(),
            "events": [event.to_record() for event in events],
            "execution_enabled": False,
        }

    @app.post("/data-deletions/{request_id}/approve")
    def approve_data_deletion_request(
        request_id: int,
        review: DataDeletionReviewRequest,
    ) -> dict[str, Any]:
        return _review_data_deletion_request(request_id, "approve", review)

    @app.post("/data-deletions/{request_id}/reject")
    def reject_data_deletion_request(
        request_id: int,
        review: DataDeletionReviewRequest,
    ) -> dict[str, Any]:
        return _review_data_deletion_request(request_id, "reject", review)

    @app.post("/data-deletions/{request_id}/cancel")
    def cancel_data_deletion_request(
        request_id: int,
        review: DataDeletionReviewRequest,
    ) -> dict[str, Any]:
        return _review_data_deletion_request(request_id, "cancel", review)

    def _review_data_deletion_request(
        request_id: int,
        action: str,
        review: DataDeletionReviewRequest,
    ) -> dict[str, Any]:
        connection = connect_mysql(current_config().database)
        try:
            service = DataDeletionRequestService(connection)
            try:
                if action == "approve":
                    request = service.approve_request(
                        request_id,
                        actor_id=review.actor_id,
                        note=review.note,
                    )
                elif action == "reject":
                    request = service.reject_request(
                        request_id,
                        actor_id=review.actor_id,
                        note=review.note,
                    )
                else:
                    request = service.cancel_request(
                        request_id,
                        actor_type="local",
                        actor_id=review.actor_id,
                        note=review.note,
                    )
            except DataDeletionRequestError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        finally:
            connection.close()
        return {
            "request": request.to_record(),
            "execution_enabled": False,
        }

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
            global_scope = _ranking_global_scope(settings_store, guild_id)
            ranking = PlayerRankingService(connection).get_player_ranking(
                shard=shard,
                metric=metric,
                guild_id=None if global_scope else guild_id,
                global_scope=global_scope,
                active_only=active_only,
                min_matches=min_matches,
                limit=limit,
            )
            return {"ranking": ranking.to_record()}
        finally:
            connection.close()

    @app.post("/players/register")
    def register_player(request: RegisterPlayerRequest) -> dict[str, Any]:
        runtime_config = current_config()
        public_profile = (
            request.public_profile
            if request.public_profile is not None
            else _public_profile_default(settings_store)
        )
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
                    public_profile=public_profile,
                    context=context,
                )
            else:
                if not runtime_config.secrets.pubg_api_key:
                    raise HTTPException(status_code=500, detail="PUBG_API_KEY is not configured.")
                try:
                    player = registry.register_player_by_name(
                        pubg_client=PubgApiClient(runtime_config.secrets.pubg_api_key),
                        shard=request.shard,
                        player_name=request.current_name,
                        public_profile=public_profile,
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
        runtime_config = current_config()
        if not runtime_config.secrets.pubg_api_key:
            raise HTTPException(status_code=500, detail="PUBG_API_KEY is not configured.")

        connection = connect_mysql(config.database)
        try:
            try:
                result = RegisteredPlayerMatchCollector(
                    connection,
                    PubgApiClient(runtime_config.secrets.pubg_api_key),
                    lookup_chunk_size=runtime_config.app.player_lookup_chunk_size,
                ).collect_active_players(
                    shard=request.shard,
                    limit=request.limit or runtime_config.app.collector_cycle_player_limit,
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
        runtime_config = current_config()
        if not runtime_config.secrets.pubg_api_key:
            raise HTTPException(status_code=500, detail="PUBG_API_KEY is not configured.")

        connection = connect_mysql(config.database)
        try:
            result = MatchJobProcessor(
                connection,
                PubgApiClient(runtime_config.secrets.pubg_api_key),
                RawPayloadStore(
                    runtime_config.app.raw_data_dir,
                    compression=runtime_config.app.raw_compression,  # type: ignore[arg-type]
                ),
            ).process_queued_matches(limit=request.limit)
            return {"result": result.to_record()}
        finally:
            connection.close()

    @app.get("/jobs/telemetry")
    def telemetry_jobs(limit: int = 100) -> dict[str, Any]:
        runtime_config = current_config()
        connection = connect_mysql(config.database)
        try:
            jobs = TelemetryJobProcessor(
                connection,
                RawPayloadStore(
                    runtime_config.app.raw_data_dir,
                    compression=runtime_config.app.raw_compression,  # type: ignore[arg-type]
                ),
            ).list_telemetry_jobs(limit=limit)
            return {"jobs": [_json_ready(job) for job in jobs]}
        finally:
            connection.close()

    @app.post("/jobs/telemetry/process")
    def process_telemetry_jobs(request: ProcessTelemetryJobsRequest) -> dict[str, Any]:
        runtime_config = current_config()
        connection = connect_mysql(config.database)
        try:
            result = TelemetryJobProcessor(
                connection,
                RawPayloadStore(
                    runtime_config.app.raw_data_dir,
                    compression=runtime_config.app.raw_compression,  # type: ignore[arg-type]
                ),
            ).process_queued_telemetry(limit=request.limit)
            return {"result": result.to_record()}
        finally:
            connection.close()

    @app.post("/telemetry/combat/process")
    def process_telemetry_combat(request: ParseTelemetryCombatRequest) -> dict[str, Any]:
        runtime_config = current_config()
        connection = connect_mysql(config.database)
        try:
            result = TelemetryCombatProcessor(
                connection,
                RawPayloadStore(
                    runtime_config.app.raw_data_dir,
                    compression=runtime_config.app.raw_compression,  # type: ignore[arg-type]
                ),
            ).process_raw_telemetry(limit=request.limit, force=request.force)
            return {"result": result.to_record()}
        finally:
            connection.close()

    @app.post("/telemetry/items/process")
    def process_telemetry_items(request: ParseTelemetryItemsRequest) -> dict[str, Any]:
        runtime_config = current_config()
        connection = connect_mysql(config.database)
        try:
            result = TelemetryItemProcessor(
                connection,
                RawPayloadStore(
                    runtime_config.app.raw_data_dir,
                    compression=runtime_config.app.raw_compression,  # type: ignore[arg-type]
                ),
            ).process_raw_telemetry(limit=request.limit, force=request.force)
            return {"result": result.to_record()}
        finally:
            connection.close()

    @app.post("/telemetry/movement/process")
    def process_telemetry_movement(request: ParseTelemetryMovementRequest) -> dict[str, Any]:
        runtime_config = current_config()
        connection = connect_mysql(config.database)
        try:
            result = TelemetryMovementProcessor(
                connection,
                RawPayloadStore(
                    runtime_config.app.raw_data_dir,
                    compression=runtime_config.app.raw_compression,  # type: ignore[arg-type]
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
        runtime_config = current_config()
        connection = connect_mysql(config.database)
        try:
            result = MapSnapshotProcessor(
                connection,
                ReplayArtifactStore(runtime_config.app.replay_data_dir),
            ).generate_player_snapshots(limit=request.limit, force=request.force)
            return {"result": result.to_record()}
        finally:
            connection.close()

    @app.post("/replay/timelines/generate")
    def generate_replay_timelines(request: GenerateReplayTimelinesRequest) -> dict[str, Any]:
        runtime_config = current_config()
        connection = connect_mysql(config.database)
        try:
            result = ReplayTimelineProcessor(
                connection,
                ReplayArtifactStore(runtime_config.app.replay_data_dir),
            ).generate_player_timelines(limit=request.limit, force=request.force)
            return {"result": result.to_record()}
        finally:
            connection.close()

    @app.get("/replay/map-assets/{map_name}")
    def replay_map_asset(map_name: str) -> FileResponse:
        runtime_config = current_config()
        filename = MAP_ASSET_FILENAMES.get(map_name)
        if filename is None:
            raise HTTPException(status_code=404, detail="map asset is not registered.")

        cache_root = runtime_config.app.replay_data_dir / "cache"
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
        runtime_config = current_config()
        connection = connect_mysql(config.database)
        try:
            artifact = get_replay_artifact(connection, artifact_id)
        finally:
            connection.close()

        if artifact is None:
            raise HTTPException(status_code=404, detail="replay artifact not found.")

        store = ReplayArtifactStore(runtime_config.app.replay_data_dir)
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
        "storage_status": {
            "raw_data_dir": check_storage_path(config.app.raw_data_dir).to_record(),
            "replay_data_dir": check_storage_path(config.app.replay_data_dir).to_record(),
        },
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


def _alerts_status_record(settings_store: LocalSettingsStore, config: RuntimeConfig) -> dict[str, Any]:
    try:
        alert_settings = settings_store.load_alert_settings()
    except LocalSettingsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    connection = connect_mysql(config.database)
    try:
        report = collect_system_alerts(
            config=config,
            connection=connection,
            settings=alert_settings,
            after_worker_run_id=None,
        )
        active_records = sync_alert_history(connection, report.alerts)
        history_page = get_alert_history_page(connection, limit=50)
        current_alert_keys = {alert.key for alert in report.alerts}
    finally:
        connection.close()

    history_record = _alert_history_page_record(history_page)
    return {
        "alert_settings": alert_settings.to_record(),
        "alerts": [
            record.to_record()
            for record in visible_alert_records(active_records)
            if record.alert_key in current_alert_keys
        ],
        **history_record,
        "latest_worker_run_id": report.latest_worker_run_id,
    }


def _alert_history_page_record(page: Any) -> dict[str, Any]:
    record = page.to_record()
    records = record.pop("records")
    return {
        "alert_history": records,
        "alert_history_page": record,
    }


def _alert_history_csv_response(records: list[AlertHistoryRecord]) -> Response:
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "id",
            "source",
            "severity",
            "state",
            "title",
            "message",
            "first_seen_at_kst",
            "last_seen_at_kst",
            "last_notified_at_kst",
            "acknowledged_at_kst",
            "snoozed_until_kst",
            "resolved_at_kst",
            "note_count",
            "latest_note_type",
            "latest_note",
            "latest_note_at_kst",
            "alert_key",
            "metadata_json",
        ],
    )
    writer.writeheader()
    for record in records:
        writer.writerow(
            {
                "id": record.id,
                "source": record.source,
                "severity": record.severity,
                "state": _alert_history_record_state(record),
                "title": record.title,
                "message": record.message,
                "first_seen_at_kst": record.first_seen_at_kst or "",
                "last_seen_at_kst": record.last_seen_at_kst or "",
                "last_notified_at_kst": record.last_notified_at_kst or "",
                "acknowledged_at_kst": record.acknowledged_at_kst or "",
                "snoozed_until_kst": record.snoozed_until_kst or "",
                "resolved_at_kst": record.resolved_at_kst or "",
                "note_count": record.note_count,
                "latest_note_type": record.latest_note_type or "",
                "latest_note": record.latest_note or "",
                "latest_note_at_kst": record.latest_note_at_kst or "",
                "alert_key": record.alert_key,
                "metadata_json": _json_dumps_compact(record.metadata),
            }
        )
    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="pubg-ai-alert-history.csv"'},
    )


def _worker_run_csv_response(records: list[WorkerRunRecord]) -> Response:
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "id",
            "worker_name",
            "status",
            "created_at_kst",
            "started_at_kst",
            "finished_at_kst",
            "duration_seconds",
            "error_count",
            "last_error",
            "errors_json",
            "summary_json",
        ],
    )
    writer.writeheader()
    for record in records:
        errors = record.summary.get("errors")
        writer.writerow(
            {
                "id": record.id,
                "worker_name": record.worker_name,
                "status": record.status,
                "created_at_kst": record.created_at_kst or "",
                "started_at_kst": record.started_at_kst or "",
                "finished_at_kst": record.finished_at_kst or "",
                "duration_seconds": record.duration_seconds if record.duration_seconds is not None else "",
                "error_count": record.error_count,
                "last_error": record.last_error or "",
                "errors_json": _json_dumps_compact(errors if isinstance(errors, list) else []),
                "summary_json": _json_dumps_compact(record.summary),
            }
        )
    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="pubg-ai-worker-run-history.csv"'},
    )


def _alert_history_record_state(record: AlertHistoryRecord) -> str:
    if record.resolved_at_kst:
        return "resolved"
    if record.is_acknowledged():
        return "acknowledged"
    if record.is_snoozed():
        return "snoozed"
    return "current"


def _json_dumps_compact(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, separators=(",", ":"), default=str)


def _ranking_global_scope(settings_store: LocalSettingsStore, guild_id: str | None) -> bool:
    if guild_id is None:
        return True
    try:
        settings = settings_store.load_discord_scope_settings()
    except LocalSettingsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return settings.guild_ranking_scopes.get(guild_id) == "global"


def _public_profile_default(settings_store: LocalSettingsStore) -> bool:
    try:
        settings = settings_store.load_discord_scope_settings()
    except LocalSettingsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return settings.public_profile_default


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
    .alert-history-filter {
      grid-template-columns: 110px 130px 110px 90px 145px minmax(160px, 1fr) auto;
    }
    .worker-run-filter {
      grid-template-columns: 130px 120px 130px minmax(170px, 1fr) minmax(170px, 1fr) 90px auto;
      margin-bottom: 10px;
    }
    label { display: grid; gap: 6px; color: var(--muted); font-size: 12px; }
    input, select, textarea {
      width: 100%;
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 10px;
      font-size: 14px;
      background: #fff;
    }
    textarea { min-height: 76px; resize: vertical; font-family: inherit; }
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
    .detail-note-form {
      margin-top: 10px;
      grid-template-columns: 140px minmax(0, 1fr) auto;
      align-items: end;
    }
    .alert-state-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 6px; }
    .alert-state-badge {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      border-radius: 6px;
      padding: 3px 8px;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0;
      border: 1px solid transparent;
    }
    .alert-state-active { background: #e6f4ea; color: #0f5132; border-color: #b7dfc6; }
    .alert-state-acknowledged { background: #edf2f7; color: #344054; border-color: #cbd5e1; }
    .alert-state-snoozed { background: #fff4d6; color: #7a4b00; border-color: #f5cf70; }
    .alert-state-resolved { background: #e8f0fe; color: #174ea6; border-color: #adc7ff; }
    .alert-severity-badge {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      border-radius: 6px;
      padding: 3px 8px;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0;
      border: 1px solid transparent;
    }
    .alert-severity-error { background: #fde7e9; color: #9f1239; border-color: #f5b5bf; }
    .alert-severity-warning { background: #fff4d6; color: #7a4b00; border-color: #f5cf70; }
    .alert-severity-info { background: #e8f0fe; color: #174ea6; border-color: #adc7ff; }
    .alert-severity-ok { background: #e6f4ea; color: #0f5132; border-color: #b7dfc6; }
    .alert-severity-unknown { background: #edf2f7; color: #344054; border-color: #cbd5e1; }
    .table-badge-stack { display: grid; justify-items: start; gap: 5px; }
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
    tr.linked-row td { background: #fff7ed; }
    tr.linked-row td:first-child { border-left: 3px solid var(--accent); }
    .team-member strong { overflow-wrap: anywhere; }
    .team-member span { color: var(--muted); font-size: 12px; }
    .team-member span:last-child { grid-column: 1 / -1; }
    @media (max-width: 900px) {
      .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      form { grid-template-columns: 1fr; }
      .alert-history-filter { grid-template-columns: 1fr; }
      .worker-run-filter { grid-template-columns: 1fr; }
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
    <section id="storage-settings">
      <h2>Storage Settings</h2>
      <form id="storageSettingsForm">
        <label>Raw directory
          <input name="raw_data_dir" autocomplete="off" required>
        </label>
        <label>Replay directory
          <input name="replay_data_dir" autocomplete="off" required>
        </label>
        <label>Compression
          <select name="raw_compression">
            <option value="gzip">gzip</option>
            <option value="none">none</option>
          </select>
        </label>
        <button type="submit">Save</button>
      </form>
      <div class="status" id="storageSettingsStatus" style="margin-top: 12px;">Waiting</div>
    </section>
    <section id="alerts">
      <h2>Alert Settings</h2>
      <form id="alertSettingsForm">
        <label>Minimum free GB
          <input name="minimum_free_gb" type="number" min="0" step="0.1" value="50" required>
        </label>
        <label>Discord channel IDs
          <input name="discord_channel_ids" autocomplete="off" placeholder="123456789012345678, 987654321098765432">
        </label>
        <label>Storage alerts
          <select name="storage_alerts_enabled">
            <option value="true">enabled</option>
            <option value="false">disabled</option>
          </select>
        </label>
        <label>Worker alerts
          <select name="worker_error_alerts_enabled">
            <option value="true">enabled</option>
            <option value="false">disabled</option>
          </select>
        </label>
        <button type="submit">Save</button>
      </form>
      <div class="status" id="alertSettingsStatus" style="margin-top: 12px;">Waiting</div>
      <table style="margin-top: 12px;">
        <thead>
          <tr>
            <th>Source</th>
            <th>Severity</th>
            <th>Title</th>
            <th>Message</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody id="alertsBody"></tbody>
      </table>
      <h3>Alert History</h3>
      <form id="alertHistoryFilterForm" class="alert-history-filter">
        <label>Source
          <select name="source">
            <option value="all">all</option>
            <option value="storage">storage</option>
            <option value="worker">worker</option>
          </select>
        </label>
        <label>Status
          <select name="state">
            <option value="all">all</option>
            <option value="current">current</option>
            <option value="active">active</option>
            <option value="acknowledged">acknowledged</option>
            <option value="snoozed">snoozed</option>
            <option value="resolved">resolved</option>
          </select>
        </label>
        <label>Severity
          <select name="severity">
            <option value="all">all</option>
            <option value="error">error</option>
            <option value="warning">warning</option>
            <option value="info">info</option>
            <option value="ok">ok</option>
          </select>
        </label>
        <label>Rows
          <select name="limit">
            <option value="25">25</option>
            <option value="50" selected>50</option>
            <option value="100">100</option>
            <option value="200">200</option>
          </select>
        </label>
        <label>Sort
          <select name="sort">
            <option value="newest" selected>newest</option>
            <option value="oldest">oldest</option>
            <option value="severity">severity-first</option>
          </select>
        </label>
        <label>Search
          <input name="search" placeholder="title or message">
        </label>
        <button type="submit">Apply</button>
      </form>
      <div class="actions" style="margin-top: 10px;">
        <button class="secondary" type="button" data-alert-history-preset="current-errors">Current errors</button>
        <button class="secondary" type="button" data-alert-history-preset="worker-failures">Worker failures</button>
        <button class="secondary" type="button" data-alert-history-preset="storage-pressure">Storage pressure</button>
        <button class="secondary" type="button" data-alert-history-preset="all-history">All history</button>
        <button class="secondary" type="button" id="alertHistoryExport">Export CSV</button>
        <button class="secondary" type="button" id="alertHistoryCopyFilterLink">Copy filter link</button>
        <button class="secondary" type="button" id="alertHistoryPrev">Previous</button>
        <button class="secondary" type="button" id="alertHistoryNext">Next</button>
      </div>
      <div class="status" id="alertHistoryStatus" style="margin-top: 8px;">Waiting</div>
      <table>
        <thead>
          <tr>
            <th>Last seen</th>
            <th>Source</th>
            <th>Title</th>
            <th>Status</th>
            <th>Notes</th>
            <th>Message</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody id="alertHistoryBody"></tbody>
      </table>
      <div class="detail-panel" id="alertHistoryDetail">
        Select an alert history row.
      </div>
    </section>
    <section id="collector-settings">
      <h2>Collector Settings</h2>
      <form id="collectorSettingsForm">
        <label>Poll seconds
          <input name="poll_interval_seconds" type="number" min="60" max="300" value="180" required>
        </label>
        <label>Cycle players
          <input name="cycle_player_limit" type="number" min="1" max="100" value="100" required>
        </label>
        <label>Lookup chunk
          <input name="player_lookup_chunk_size" type="number" min="1" max="10" value="10" required>
        </label>
        <button type="submit">Save</button>
      </form>
      <div class="status" id="collectorSettingsStatus" style="margin-top: 12px;">Waiting</div>
      <form id="collectorWorkerForm" style="margin-top: 10px;">
        <label>Shard
          <select name="shard">
            <option value="">all</option>
            <option value="steam">steam</option>
            <option value="kakao">kakao</option>
            <option value="psn">psn</option>
            <option value="xbox">xbox</option>
          </select>
        </label>
        <label>Match jobs
          <input name="match_job_limit" type="number" min="1" max="500" value="10" required>
        </label>
        <label>Telemetry jobs
          <input name="telemetry_job_limit" type="number" min="1" max="200" value="5" required>
        </label>
        <button type="submit">Start auto</button>
        <button class="secondary" type="button" id="collectorWorkerStop">Stop</button>
      </form>
      <div class="status" id="collectorWorkerStatus" style="margin-top: 12px;">Auto collector stopped</div>
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
    <section id="discord-permissions">
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
    <section id="discord-scopes">
      <h2>Discord Scope Settings</h2>
      <form id="discordScopeForm">
        <label>Guild ID
          <input name="guild_id" autocomplete="off" required>
        </label>
        <label>Ranking scope
          <select name="scope">
            <option value="guild">guild</option>
            <option value="global">global</option>
          </select>
        </label>
        <button type="submit">Save</button>
      </form>
      <form id="publicProfileDefaultForm" style="margin-top: 10px;">
        <label>Public profile default
          <select name="public_profile_default">
            <option value="true">public</option>
            <option value="false">private</option>
          </select>
        </label>
        <button type="submit">Save</button>
      </form>
      <table style="margin-top: 12px;">
        <thead>
          <tr>
            <th>Guild ID</th>
            <th>Ranking scope</th>
            <th></th>
          </tr>
        </thead>
        <tbody id="discordScopesBody"></tbody>
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
    <section id="registered-players">
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
    <section id="data-deletions">
      <h2>Data Deletion Review</h2>
      <form id="dataDeletionFilterForm">
        <label>Status
          <select name="status">
            <option value="pending">pending</option>
            <option value="approved">approved</option>
            <option value="all">all</option>
            <option value="rejected">rejected</option>
            <option value="cancelled">cancelled</option>
            <option value="expired">expired</option>
          </select>
        </label>
        <label>Local reviewer
          <input name="actor_id" autocomplete="off" value="local-manager" required>
        </label>
        <label>Audit note
          <input name="note" autocomplete="off" maxlength="1000">
        </label>
        <button type="submit">Refresh</button>
      </form>
      <div class="status" id="dataDeletionStatus" style="margin: 10px 0;">
        Approval records authorization only. Deletion execution is disabled.
      </div>
      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Target</th>
            <th>Scope</th>
            <th>Status</th>
            <th>Requested KST</th>
            <th>Expires KST</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody id="dataDeletionBody"></tbody>
      </table>
      <div class="detail-panel" id="dataDeletionDetail">
        Select a request to inspect its audit history.
      </div>
    </section>
    <section id="profile-lookup">
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
    <section id="weapon-lookup">
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
    <section id="recommendation-lookup">
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
    <section id="match-lookup">
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
    <section id="ranking-lookup">
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
      <h2>Post-processing Worker</h2>
      <form id="postProcessingWorkerForm">
        <label>Combat
          <input name="combat_limit" type="number" min="1" max="200" value="10" required>
        </label>
        <label>Items
          <input name="item_limit" type="number" min="1" max="200" value="10" required>
        </label>
        <label>Movement
          <input name="movement_limit" type="number" min="1" max="200" value="10" required>
        </label>
        <label>Loadout
          <input name="loadout_limit" type="number" min="1" max="500" value="50" required>
        </label>
        <label>Map JPEG
          <input name="map_snapshot_limit" type="number" min="1" max="200" value="10" required>
        </label>
        <label>Timeline
          <input name="timeline_limit" type="number" min="1" max="200" value="10" required>
        </label>
        <label>Mode
          <select name="force">
            <option value="false">skip existing</option>
            <option value="true">force</option>
          </select>
        </label>
        <button type="submit">Start auto</button>
        <button class="secondary" type="button" id="postProcessingWorkerStop">Stop</button>
      </form>
      <div class="status" id="postProcessingWorkerStatus" style="margin-top: 12px;">Post-processing stopped</div>
    </section>
    <section id="worker-runs">
      <h2>Worker Run History</h2>
      <form id="workerRunFilterForm" class="worker-run-filter">
        <label>Worker
          <select name="worker_name">
            <option value="all">all</option>
            <option value="collector">collector</option>
            <option value="post_processing">post_processing</option>
          </select>
        </label>
        <label>Status
          <select name="status">
            <option value="all">all</option>
            <option value="succeeded">succeeded</option>
            <option value="failed">failed</option>
          </select>
        </label>
        <label>Quick range
          <select name="quick_range">
            <option value="custom">custom</option>
            <option value="last_1h">last 1h</option>
            <option value="last_24h">last 24h</option>
            <option value="today">today</option>
            <option value="yesterday">yesterday</option>
            <option value="last_7d">last 7d</option>
          </select>
        </label>
        <label>Created from
          <input name="created_from_kst" type="datetime-local">
        </label>
        <label>Created to
          <input name="created_to_kst" type="datetime-local">
        </label>
        <label>Limit
          <input name="limit" type="number" min="1" max="200" value="50">
        </label>
        <button type="submit">Apply</button>
      </form>
      <div class="actions" style="margin-bottom: 10px;">
        <button class="secondary" type="button" onclick="loadWorkerRuns()">새로고침</button>
        <button class="secondary" type="button" id="workerRunsExport">Export CSV</button>
        <button class="secondary" type="button" id="workerRunsCopyFilterLink">Copy filter link</button>
        <button class="secondary" type="button" id="workerRunsPrev">Previous</button>
        <button class="secondary" type="button" id="workerRunsNext">Next</button>
      </div>
      <div class="status" id="workerRunsStatus" style="margin-bottom: 8px;">Waiting</div>
      <table>
        <thead>
          <tr>
            <th>Worker</th>
            <th>Status</th>
            <th>Finished</th>
            <th>Duration</th>
            <th>Summary</th>
            <th></th>
          </tr>
        </thead>
        <tbody id="workerRunsBody"></tbody>
      </table>
      <div class="detail-panel" id="workerRunDetail">
        Select a worker run row.
      </div>
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
    <section id="replay-player">
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
    <section id="replay-artifacts">
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
    const dataDeletionFilterForm = document.querySelector("#dataDeletionFilterForm");
    const dataDeletionBody = document.querySelector("#dataDeletionBody");
    const dataDeletionStatus = document.querySelector("#dataDeletionStatus");
    const dataDeletionDetail = document.querySelector("#dataDeletionDetail");
    const profileBody = document.querySelector("#profileBody");
    const weaponBody = document.querySelector("#weaponBody");
    const recommendationBody = document.querySelector("#recommendationBody");
    const matchBody = document.querySelector("#matchBody");
    const rankingBody = document.querySelector("#rankingBody");
    const jobsBody = document.querySelector("#jobsBody");
    const telemetryJobsBody = document.querySelector("#telemetryJobsBody");
    const workerRunFilterForm = document.querySelector("#workerRunFilterForm");
    const workerRunsBody = document.querySelector("#workerRunsBody");
    const workerRunsStatus = document.querySelector("#workerRunsStatus");
    const workerRunsExport = document.querySelector("#workerRunsExport");
    const workerRunsCopyFilterLink = document.querySelector("#workerRunsCopyFilterLink");
    const workerRunsPrev = document.querySelector("#workerRunsPrev");
    const workerRunsNext = document.querySelector("#workerRunsNext");
    const workerRunDetail = document.querySelector("#workerRunDetail");
    const combatStatus = document.querySelector("#combatStatus");
    const itemStatus = document.querySelector("#itemStatus");
    const movementStatus = document.querySelector("#movementStatus");
    const loadoutSnapshotStatus = document.querySelector("#loadoutSnapshotStatus");
    const mapSnapshotStatus = document.querySelector("#mapSnapshotStatus");
    const timelineStatus = document.querySelector("#timelineStatus");
    const replayArtifactsBody = document.querySelector("#replayArtifactsBody");
    const discordGrantForm = document.querySelector("#discordGrantForm");
    const discordPermissionsBody = document.querySelector("#discordPermissionsBody");
    const discordPermissionGroup = document.querySelector("#discordPermissionGroup");
    const discordScopeForm = document.querySelector("#discordScopeForm");
    const publicProfileDefaultForm = document.querySelector("#publicProfileDefaultForm");
    const discordScopesBody = document.querySelector("#discordScopesBody");
    const registerForm = document.querySelector("#registerForm");
    const storageSettingsForm = document.querySelector("#storageSettingsForm");
    const storageSettingsStatus = document.querySelector("#storageSettingsStatus");
    const alertSettingsForm = document.querySelector("#alertSettingsForm");
    const alertSettingsStatus = document.querySelector("#alertSettingsStatus");
    const alertsBody = document.querySelector("#alertsBody");
    const alertHistoryFilterForm = document.querySelector("#alertHistoryFilterForm");
    const alertHistoryBody = document.querySelector("#alertHistoryBody");
    const alertHistoryStatus = document.querySelector("#alertHistoryStatus");
    const alertHistoryExport = document.querySelector("#alertHistoryExport");
    const alertHistoryCopyFilterLink = document.querySelector("#alertHistoryCopyFilterLink");
    const alertHistoryPrev = document.querySelector("#alertHistoryPrev");
    const alertHistoryNext = document.querySelector("#alertHistoryNext");
    const alertHistoryPresetButtons = document.querySelectorAll("[data-alert-history-preset]");
    const alertHistoryDetail = document.querySelector("#alertHistoryDetail");
    const collectorSettingsForm = document.querySelector("#collectorSettingsForm");
    const collectorSettingsStatus = document.querySelector("#collectorSettingsStatus");
    const collectorWorkerForm = document.querySelector("#collectorWorkerForm");
    const collectorWorkerStop = document.querySelector("#collectorWorkerStop");
    const collectorWorkerStatus = document.querySelector("#collectorWorkerStatus");
    const postProcessingWorkerForm = document.querySelector("#postProcessingWorkerForm");
    const postProcessingWorkerStop = document.querySelector("#postProcessingWorkerStop");
    const postProcessingWorkerStatus = document.querySelector("#postProcessingWorkerStatus");
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
    let replayArtifactFilter = { match_id: "", account_id: "", artifact_id: "" };
    let registeredPlayerHighlight = { shard: "", account_id: "", name: "" };
    let deletionRequestHighlightId = "";
    let discordSettingsPrefill = { permission_group: "", public_profile_default: "" };
    let localSettingsPrefill = {
      collector_poll_interval_seconds: "",
      collector_cycle_player_limit: "",
      collector_player_lookup_chunk_size: "",
    };
    let alertHistoryPage = {
      source: "all",
      state: "all",
      severity: "all",
      limit: 50,
      offset: 0,
      total: 0,
      sort: "newest",
      search: "",
      has_previous: false,
      has_next: false,
    };
    let workerRunPage = {
      total: 0,
      limit: 50,
      offset: 0,
      worker_name: null,
      status: "all",
      quick_range: "custom",
      created_from_kst: "",
      created_to_kst: "",
      has_previous: false,
      has_next: false,
    };
    let activeAlertHistoryDetailId = null;
    let activeAlertHistoryDetailAlert = null;
    let activeAlertHistoryNoteType = "note";
    let alertHistoryRecords = [];
    let activeDiscordScopes = {
      guild_ranking_scopes: {},
      public_profile_default: true,
      updated_at: null,
    };

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
      storageSettingsForm.elements.raw_data_dir.value = settings.raw_data_dir || "";
      storageSettingsForm.elements.replay_data_dir.value = settings.replay_data_dir || "";
      storageSettingsForm.elements.raw_compression.value = settings.raw_compression || "gzip";
      storageSettingsStatus.textContent = [
        `Raw ${formatStoragePathStatus(settings.storage_status?.raw_data_dir)}`,
        `Replay ${formatStoragePathStatus(settings.storage_status?.replay_data_dir)}`,
      ].join(" / ");
      collectorSettingsForm.elements.poll_interval_seconds.value = settings.collector.poll_interval_seconds || 180;
      collectorSettingsForm.elements.cycle_player_limit.value = settings.collector.cycle_player_limit || 100;
      collectorSettingsForm.elements.player_lookup_chunk_size.value = settings.collector.player_lookup_chunk_size || 10;
      collectorSettingsStatus.textContent = [
        `${settings.collector.poll_interval_seconds}초`,
        `${settings.collector.cycle_player_limit}명`,
        `chunk ${settings.collector.player_lookup_chunk_size}`,
      ].join(" / ");
      webSettingsForm.elements.local_web_base_url.value = settings.local_web_base_url || "";
      webSettingsStatus.textContent = settings.local_web_base_url
        ? `Enabled: ${settings.local_web_base_url}`
        : "Disabled";
      applyCollectorSettingsPrefill();
    }

    function applyCollectorSettingsPrefill() {
      if (localSettingsPrefill.collector_poll_interval_seconds) {
        setFormElementValue(
          collectorSettingsForm,
          "poll_interval_seconds",
          localSettingsPrefill.collector_poll_interval_seconds,
        );
      }
      if (localSettingsPrefill.collector_cycle_player_limit) {
        setFormElementValue(
          collectorSettingsForm,
          "cycle_player_limit",
          localSettingsPrefill.collector_cycle_player_limit,
        );
      }
      if (localSettingsPrefill.collector_player_lookup_chunk_size) {
        setFormElementValue(
          collectorSettingsForm,
          "player_lookup_chunk_size",
          localSettingsPrefill.collector_player_lookup_chunk_size,
        );
      }
      localSettingsPrefill = {
        collector_poll_interval_seconds: "",
        collector_cycle_player_limit: "",
        collector_player_lookup_chunk_size: "",
      };
    }

    function formatStoragePathStatus(status) {
      if (!status) return "unknown";
      if (!status.exists) return "missing";
      if (!status.is_dir) return "not directory";
      if (!status.writable) return `not writable${status.error ? `: ${status.error}` : ""}`;
      return `ok / free ${formatBytes(Number(status.free_bytes || 0))}`;
    }

    async function loadAlerts(options = {}) {
      try {
        const payload = await fetch("/alerts/status").then((r) => r.json());
        renderAlertStatus(payload, options.renderHistory !== false);
      } catch (error) {
        alertSettingsStatus.textContent = `Error: ${error.message}`;
        alertsBody.innerHTML = `<tr><td colspan="5">Error: ${escapeHtml(error.message)}</td></tr>`;
        alertHistoryBody.innerHTML = `<tr><td colspan="7">Error: ${escapeHtml(error.message)}</td></tr>`;
        alertHistoryStatus.textContent = `Error: ${error.message}`;
      }
    }

    async function loadAlertHistory(options = {}) {
      const form = new FormData(alertHistoryFilterForm);
      const source = options.source || String(form.get("source") || alertHistoryPage.source || "all");
      const state = options.state || String(form.get("state") || alertHistoryPage.state || "all");
      const severity = options.severity || String(form.get("severity") || alertHistoryPage.severity || "all");
      const sort = options.sort || String(form.get("sort") || alertHistoryPage.sort || "newest");
      const search = options.search ?? String(form.get("search") ?? alertHistoryPage.search ?? "");
      const limit = Number(options.limit || form.get("limit") || alertHistoryPage.limit || 50);
      const offset = Math.max(0, Number(options.offset ?? alertHistoryPage.offset ?? 0));
      const params = new URLSearchParams({
        source,
        state,
        severity,
        sort,
        search,
        limit: String(limit),
        offset: String(offset),
      });
      const payload = await fetch(`/alerts/history?${params.toString()}`).then((r) => r.json());
      if (payload.detail) throw new Error(payload.detail);
      renderAlertHistory(payload.alert_history || [], payload.alert_history_page || {}, true);
      if (options.updateUrl) {
        updateAlertHistoryFilterUrl();
      }
    }

    async function refreshAlertsAndHistory() {
      const page = { ...alertHistoryPage };
      await loadAlerts({ renderHistory: false });
      await loadAlertHistory(page);
    }

    function exportAlertHistoryCsv() {
      const form = new FormData(alertHistoryFilterForm);
      const params = new URLSearchParams({
        source: String(form.get("source") || alertHistoryPage.source || "all"),
        state: String(form.get("state") || alertHistoryPage.state || "all"),
        severity: String(form.get("severity") || alertHistoryPage.severity || "all"),
        sort: String(form.get("sort") || alertHistoryPage.sort || "newest"),
        search: String(form.get("search") ?? alertHistoryPage.search ?? ""),
        limit: "5000",
        offset: "0",
      });
      window.location.href = `/alerts/history/export.csv?${params.toString()}`;
    }

    async function applyAlertHistoryPreset(preset) {
      const presets = {
        "current-errors": {
          source: "all",
          state: "current",
          severity: "error",
          sort: "severity",
          search: "",
        },
        "worker-failures": {
          source: "worker",
          state: "all",
          severity: "error",
          sort: "newest",
          search: "",
        },
        "storage-pressure": {
          source: "storage",
          state: "all",
          severity: "all",
          sort: "severity",
          search: "",
        },
        "all-history": {
          source: "all",
          state: "all",
          severity: "all",
          sort: "newest",
          search: "",
        },
      };
      const filters = presets[preset];
      if (!filters) throw new Error(`unknown alert history preset: ${preset}`);
      alertHistoryFilterForm.elements.source.value = filters.source;
      alertHistoryFilterForm.elements.state.value = filters.state;
      alertHistoryFilterForm.elements.severity.value = filters.severity;
      alertHistoryFilterForm.elements.sort.value = filters.sort;
      alertHistoryFilterForm.elements.search.value = filters.search;
      await loadAlertHistory({ ...filters, offset: 0, updateUrl: true });
    }

    function renderAlertStatus(payload, renderHistory = true) {
      const settings = payload.alert_settings || {};
      alertSettingsForm.elements.minimum_free_gb.value = bytesToGiB(settings.minimum_free_bytes ?? 0).toFixed(1);
      alertSettingsForm.elements.discord_channel_ids.value = (settings.discord_channel_ids || []).join(", ");
      alertSettingsForm.elements.storage_alerts_enabled.value = settings.storage_alerts_enabled === false ? "false" : "true";
      alertSettingsForm.elements.worker_error_alerts_enabled.value = settings.worker_error_alerts_enabled === false ? "false" : "true";
      alertSettingsStatus.textContent = [
        `minimum ${formatBytes(Number(settings.minimum_free_bytes || 0))}`,
        `channels ${(settings.discord_channel_ids || []).length}`,
        `active alerts ${(payload.alerts || []).length}`,
        `history ${payload.alert_history_page?.total ?? (payload.alert_history || []).length}`,
      ].join(" / ");
      renderAlerts(payload.alerts || []);
      if (renderHistory) {
        renderAlertHistory(payload.alert_history || [], payload.alert_history_page || {}, true);
      }
    }

    function renderAlerts(alerts) {
      alertsBody.innerHTML = alerts.length
        ? alerts.map((alert) => `
          <tr>
            <td>${escapeHtml(alert.source || "")}</td>
            <td>${alertSeverityBadge(alert.severity)}</td>
            <td>${escapeHtml(alert.title || "")}</td>
            <td>${escapeHtml(alert.message || "")}</td>
            <td>
              <div class="actions">
                ${alertWorkerRunButton(alert)}
                <button type="button" data-alert-action="acknowledge" data-alert-id="${attr(alert.id)}">Acknowledge</button>
                <button class="secondary" type="button" data-alert-action="snooze" data-alert-id="${attr(alert.id)}">Snooze 1h</button>
              </div>
            </td>
          </tr>
        `).join("")
        : `<tr><td colspan="5">No active alerts</td></tr>`;
    }

    function renderAlertHistory(history, page = {}, syncControls = false) {
      alertHistoryRecords = history;
      alertHistoryPage = {
        ...alertHistoryPage,
        ...page,
        limit: Number(page.limit || alertHistoryPage.limit || 50),
        offset: Number(page.offset ?? alertHistoryPage.offset ?? 0),
        total: Number(page.total ?? alertHistoryPage.total ?? history.length),
        search: String(page.search ?? ""),
      };
      if (syncControls) {
        alertHistoryFilterForm.elements.source.value = alertHistoryPage.source || "all";
        alertHistoryFilterForm.elements.state.value = alertHistoryPage.state || "all";
        alertHistoryFilterForm.elements.severity.value = alertHistoryPage.severity || "all";
        alertHistoryFilterForm.elements.sort.value = alertHistoryPage.sort || "newest";
        alertHistoryFilterForm.elements.search.value = alertHistoryPage.search || "";
        alertHistoryFilterForm.elements.limit.value = String(alertHistoryPage.limit || 50);
      }
      const start = history.length ? alertHistoryPage.offset + 1 : 0;
      const end = history.length ? alertHistoryPage.offset + history.length : 0;
      alertHistoryStatus.textContent = [
        `${start}-${end} of ${alertHistoryPage.total}`,
        `source ${alertHistoryPage.source || "all"}`,
        `status ${alertHistoryPage.state || "all"}`,
        `severity ${alertHistoryPage.severity || "all"}`,
        `sort ${alertHistoryPage.sort || "newest"}`,
        `search ${alertHistoryPage.search ? `"${alertHistoryPage.search}"` : "-"}`,
      ].join(" / ");
      alertHistoryPrev.disabled = !alertHistoryPage.has_previous;
      alertHistoryNext.disabled = !alertHistoryPage.has_next;
      alertHistoryBody.innerHTML = history.length
        ? history.map((alert) => `
          <tr>
            <td>${escapeHtml(alert.last_seen_at_kst || "")}</td>
            <td>
              <div class="table-badge-stack">
                <span>${escapeHtml(alert.source || "")}</span>
                ${alertSeverityBadge(alert.severity)}
              </div>
            </td>
            <td>${escapeHtml(alert.title || "")}</td>
            <td>
              <div class="table-badge-stack">
                ${alertStateBadge(alert)}
                <span class="status">${escapeHtml(formatAlertHistoryStatus(alert))}</span>
              </div>
            </td>
            <td>${escapeHtml(alertHistoryNoteSummary(alert))}</td>
            <td>${escapeHtml(alert.message || "")}</td>
            <td>
              <div class="actions">
                ${alertWorkerRunButton(alert)}
                <button class="secondary" type="button" data-alert-detail-id="${attr(alert.id)}">Details</button>
                <button type="button" data-alert-note-type="note" data-alert-id="${attr(alert.id)}">Note</button>
                <button class="secondary" type="button" data-alert-note-type="resolution" data-alert-id="${attr(alert.id)}">Resolution</button>
              </div>
            </td>
          </tr>
        `).join("")
        : `<tr><td colspan="7">No alert history</td></tr>`;
      if (activeAlertHistoryDetailId) {
        const selected = history.find((alert) => String(alert.id) === String(activeAlertHistoryDetailId));
        if (selected) activeAlertHistoryDetailAlert = selected;
      }
    }

    function alertHistoryState(alert) {
      if (alert.resolved_at_kst) {
        return {
          state: "resolved",
          label: "Resolved",
          timeLabel: "Resolved at",
          timeValue: alert.resolved_at_kst || "",
          helper: "The alert is no longer present in the current storage or worker checks.",
        };
      }
      if (alert.is_acknowledged) {
        return {
          state: "acknowledged",
          label: "Acknowledged",
          timeLabel: "Acknowledged at",
          timeValue: alert.acknowledged_at_kst || "",
          helper: "Repeated notifications are suppressed until this alert is seen as resolved and reappears.",
        };
      }
      if (alert.is_snoozed) {
        return {
          state: "snoozed",
          label: "Snoozed",
          timeLabel: "Snoozed until",
          timeValue: alert.snoozed_until_kst || "",
          helper: "Notifications are temporarily hidden until the snooze time expires.",
        };
      }
      return {
        state: "active",
        label: "Active",
        timeLabel: "Last seen",
        timeValue: alert.last_seen_at_kst || "",
        helper: "This alert is currently visible and can still notify admins.",
      };
    }

    function alertStateBadge(alert) {
      const state = alertHistoryState(alert);
      return `<span class="alert-state-badge alert-state-${attr(state.state)}">${escapeHtml(state.label)}</span>`;
    }

    function alertSeverityBadge(severity) {
      const level = alertSeverityLevel(severity);
      return `<span class="alert-severity-badge alert-severity-${attr(level)}">${escapeHtml(level.toUpperCase())}</span>`;
    }

    function alertSeverityLevel(severity) {
      const level = String(severity || "unknown").toLowerCase();
      return ["error", "warning", "info", "ok"].includes(level) ? level : "unknown";
    }

    function formatAlertHistoryStatus(alert) {
      const state = alertHistoryState(alert);
      return state.timeValue ? `${state.label.toLowerCase()} ${state.timeValue}` : state.label.toLowerCase();
    }

    function alertHistoryNoteSummary(alert) {
      const count = Number(alert.note_count || 0);
      if (!count) return "-";
      const note = alert.latest_note ? `: ${alert.latest_note}` : "";
      const type = alert.latest_note_type || "note";
      return `${count} ${type}${note}`;
    }

    function alertWorkerRunButton(alert) {
      const runId = alertWorkerRunId(alert);
      return runId
        ? `<button class="secondary" type="button" data-worker-run-from-alert="${attr(runId)}">Worker run</button>`
        : "";
    }

    function alertWorkerRunId(alert) {
      const metadata = alert?.metadata || {};
      const candidates = [
        metadata.run_id,
        metadata.worker_run_id,
        alert?.source_id,
      ];
      for (const candidate of candidates) {
        const parsed = positiveIntegerText(candidate);
        if (parsed) return parsed;
      }
      const keyMatch = String(alert?.alert_key || "").match(/^worker:(\\d+)$/);
      return keyMatch ? keyMatch[1] : "";
    }

    function positiveIntegerText(value) {
      const text = String(value ?? "").trim();
      if (!/^\\d+$/.test(text)) return "";
      return Number(text) > 0 ? text : "";
    }

    async function loadAlertHistoryDetail(alert, noteType = activeAlertHistoryNoteType, focusEditor = false) {
      activeAlertHistoryDetailId = alert.id;
      activeAlertHistoryDetailAlert = alert;
      activeAlertHistoryNoteType = noteType === "resolution" ? "resolution" : "note";
      alertHistoryDetail.innerHTML = `<div class="status">Loading alert #${escapeHtml(alert.id)} notes...</div>`;
      const payload = await fetch(`/alerts/history/${encodeURIComponent(alert.id)}/notes`).then((r) => r.json());
      if (payload.detail) throw new Error(payload.detail);
      renderAlertHistoryDetail(alert, payload.notes || []);
      if (focusEditor) {
        const input = alertHistoryDetail.querySelector("textarea[name='note_text']");
        if (input) input.focus();
      }
    }

    async function loadAlertHistoryDetailById(alertId, noteType = activeAlertHistoryNoteType, focusEditor = false) {
      activeAlertHistoryDetailId = alertId;
      activeAlertHistoryNoteType = noteType === "resolution" ? "resolution" : "note";
      alertHistoryDetail.innerHTML = `<div class="status">Loading alert #${escapeHtml(alertId)} detail...</div>`;
      const payload = await fetch(`/alerts/history/${encodeURIComponent(alertId)}`).then((r) => r.json());
      if (payload.detail) throw new Error(payload.detail);
      const alert = payload.alert;
      if (!alert) throw new Error("alert history row was not returned");
      activeAlertHistoryDetailId = alert.id;
      activeAlertHistoryDetailAlert = alert;
      renderAlertHistoryDetail(alert, payload.notes || []);
      if (focusEditor) {
        const input = alertHistoryDetail.querySelector("textarea[name='note_text']");
        if (input) input.focus();
      }
    }

    async function loadInitialAlertDetailFromUrl() {
      const params = new URLSearchParams(window.location.search);
      const alertId = params.get("alert_id") || params.get("alert");
      if (!alertId) return;
      await loadAlertHistoryDetailById(alertId);
      alertHistoryDetail.scrollIntoView({ block: "start" });
    }

    function loadInitialAlertHistoryFiltersFromUrl() {
      const params = new URLSearchParams(window.location.search);
      const filterKeys = [
        "alert_history_source",
        "alert_history_state",
        "alert_history_severity",
        "alert_history_sort",
        "alert_history_search",
        "alert_history_limit",
        "alert_history_offset",
      ];
      if (!filterKeys.some((key) => params.has(key))) return false;

      const source = alertHistoryUrlChoice(params.get("alert_history_source") || params.get("alert_source"), ["all", "storage", "worker"], "all");
      const state = alertHistoryUrlChoice(params.get("alert_history_state") || params.get("alert_state"), ["all", "active", "current", "acknowledged", "snoozed", "resolved"], "all");
      const severity = alertHistoryUrlChoice(params.get("alert_history_severity") || params.get("alert_severity"), ["all", "error", "warning", "info", "ok"], "all");
      const sort = alertHistoryUrlChoice(params.get("alert_history_sort") || params.get("alert_sort"), ["newest", "oldest", "severity"], "newest");
      const search = String(params.get("alert_history_search") || params.get("alert_search") || "");
      const limit = alertHistoryUrlBoundedNumber(params.get("alert_history_limit"), 50, 1, 200);
      const offset = alertHistoryUrlBoundedNumber(params.get("alert_history_offset"), 0, 0, 1000000);

      alertHistoryFilterForm.elements.source.value = source;
      alertHistoryFilterForm.elements.state.value = state;
      alertHistoryFilterForm.elements.severity.value = severity;
      alertHistoryFilterForm.elements.sort.value = sort;
      alertHistoryFilterForm.elements.search.value = search;
      alertHistoryFilterForm.elements.limit.value = String(limit);
      alertHistoryPage = {
        ...alertHistoryPage,
        source,
        state,
        severity,
        sort,
        search,
        limit,
        offset,
      };
      return true;
    }

    function alertHistoryUrlChoice(value, allowed, fallback) {
      const text = String(value || fallback);
      return allowed.includes(text) ? text : fallback;
    }

    function alertHistoryUrlBoundedNumber(value, fallback, min, max) {
      const parsed = Number(value);
      if (!Number.isFinite(parsed)) return fallback;
      return Math.max(min, Math.min(Math.floor(parsed), max));
    }

    function alertHistoryFilterUrl() {
      const url = new URL(window.location.href);
      const form = new FormData(alertHistoryFilterForm);
      const source = String(form.get("source") || alertHistoryPage.source || "all");
      const state = String(form.get("state") || alertHistoryPage.state || "all");
      const severity = String(form.get("severity") || alertHistoryPage.severity || "all");
      const sort = String(form.get("sort") || alertHistoryPage.sort || "newest");
      const search = String(form.get("search") ?? alertHistoryPage.search ?? "");
      const limit = Number(form.get("limit") || alertHistoryPage.limit || 50);
      url.searchParams.delete("alert_id");
      url.searchParams.delete("alert");
      url.searchParams.set("alert_history_source", source);
      url.searchParams.set("alert_history_state", state);
      url.searchParams.set("alert_history_severity", severity);
      url.searchParams.set("alert_history_sort", sort);
      url.searchParams.set("alert_history_search", search);
      url.searchParams.set("alert_history_limit", String(limit || 50));
      url.searchParams.set("alert_history_offset", String(alertHistoryPage.offset || 0));
      url.hash = "alerts";
      return url.toString();
    }

    function updateAlertHistoryFilterUrl() {
      window.history.replaceState({}, "", alertHistoryFilterUrl());
    }

    async function copyAlertHistoryFilterLink() {
      const url = alertHistoryFilterUrl();
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(url);
        return url;
      }
      const input = document.createElement("textarea");
      input.value = url;
      input.setAttribute("readonly", "readonly");
      input.style.position = "fixed";
      input.style.opacity = "0";
      document.body.appendChild(input);
      input.select();
      document.execCommand("copy");
      input.remove();
      return url;
    }

    function renderAlertHistoryDetail(alert, notes) {
      const selectedNote = activeAlertHistoryNoteType === "resolution" ? "resolution" : "note";
      const state = alertHistoryState(alert);
      const noteRows = notes.length
        ? notes.map((note) => `
          <tr>
            <td>${escapeHtml(note.created_at_kst || "")}</td>
            <td>${escapeHtml(note.note_type || "")}</td>
            <td>${escapeHtml(note.created_by || "")}</td>
            <td>${escapeHtml(note.note_text || "")}</td>
          </tr>
        `).join("")
        : `<tr><td colspan="4">No notes or resolution comments</td></tr>`;
      alertHistoryDetail.innerHTML = `
        <div class="recommendation-line">
          <strong>Alert #${escapeHtml(alert.id)} detail</strong>
          <div class="actions">
            ${alertWorkerRunButton(alert)}
            <button type="button" data-alert-detail-action="acknowledge" data-alert-id="${attr(alert.id)}">Acknowledge</button>
            <button class="secondary" type="button" data-alert-detail-action="snooze" data-alert-id="${attr(alert.id)}">Snooze 1h</button>
          </div>
        </div>
        <div class="status" style="margin-top: 6px;">${notes.length} notes shown</div>
        <div class="alert-state-row">
          ${alertStateBadge(alert)}
          <span class="status">${escapeHtml(state.timeLabel)}${state.timeValue ? `: ${escapeHtml(state.timeValue)}` : ""}</span>
        </div>
        <div class="status" style="margin-top: 4px;">${escapeHtml(state.helper)}</div>
        <div class="grid" style="margin-top: 10px;">
          ${cell("Source", escapeHtml(alert.source || ""))}
          ${cell("Severity", alertSeverityBadge(alert.severity))}
          ${cell("Status", escapeHtml(state.label))}
          ${cell("Last seen", escapeHtml(alert.last_seen_at_kst || ""))}
        </div>
        <form class="detail-note-form" data-alert-note-form data-alert-id="${attr(alert.id)}">
          <label>Type
            <select name="note_type">
              <option value="note"${selectedNote === "note" ? " selected" : ""}>note</option>
              <option value="resolution"${selectedNote === "resolution" ? " selected" : ""}>resolution</option>
            </select>
          </label>
          <label>Comment
            <textarea name="note_text" required placeholder="Write an alert note or resolution comment"></textarea>
          </label>
          <button type="submit">Save</button>
        </form>
        <table class="detail-table">
          <tbody>
            <tr><th>Title</th><td>${escapeHtml(alert.title || "")}</td></tr>
            <tr><th>Message</th><td>${escapeHtml(alert.message || "")}</td></tr>
          </tbody>
        </table>
        <table class="detail-table">
          <thead>
            <tr>
              <th>Created</th>
              <th>Type</th>
              <th>Created by</th>
              <th>Text</th>
            </tr>
          </thead>
          <tbody>${noteRows}</tbody>
        </table>
      `;
    }

    async function saveAlertSettings(event) {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const payload = await postJson("/settings/alerts", {
        minimum_free_bytes: Math.round(Number(form.get("minimum_free_gb") || 0) * 1024 * 1024 * 1024),
        discord_channel_ids: parseIdList(String(form.get("discord_channel_ids") || "")),
        storage_alerts_enabled: form.get("storage_alerts_enabled") !== "false",
        worker_error_alerts_enabled: form.get("worker_error_alerts_enabled") !== "false",
      });
      renderAlertStatus(payload);
    }

    async function acknowledgeAlert(alertId) {
      const page = { ...alertHistoryPage };
      await postJson(`/alerts/history/${encodeURIComponent(alertId)}/acknowledge`, {});
      await loadAlerts({ renderHistory: false });
      await loadAlertHistory(page);
      if (String(activeAlertHistoryDetailId) === String(alertId) && activeAlertHistoryDetailAlert) {
        await loadAlertHistoryDetail(activeAlertHistoryDetailAlert);
      }
    }

    async function snoozeAlert(alertId, minutes = 60) {
      const page = { ...alertHistoryPage };
      await postJson(`/alerts/history/${encodeURIComponent(alertId)}/snooze`, { minutes });
      await loadAlerts({ renderHistory: false });
      await loadAlertHistory(page);
      if (String(activeAlertHistoryDetailId) === String(alertId) && activeAlertHistoryDetailAlert) {
        await loadAlertHistoryDetail(activeAlertHistoryDetailAlert);
      }
    }

    async function saveAlertHistoryNoteForm(form) {
      const page = { ...alertHistoryPage };
      const alertId = form.dataset.alertId || "";
      const data = new FormData(form);
      const noteType = String(data.get("note_type") || "note");
      const noteText = String(data.get("note_text") || "");
      if (!noteText || !noteText.trim()) return;
      await postJson(`/alerts/history/${encodeURIComponent(alertId)}/notes`, {
        note_text: noteText.trim(),
        note_type: noteType,
        created_by: "local-manager",
      });
      await loadAlerts({ renderHistory: false });
      await loadAlertHistory(page);
      const updatedAlert = alertHistoryRecords.find((record) => String(record.id) === String(alertId))
        || activeAlertHistoryDetailAlert;
      if (updatedAlert) {
        await loadAlertHistoryDetail(updatedAlert, noteType, true);
      }
    }

    function parseIdList(value) {
      return value
        .split(/[,\\s]+/)
        .map((item) => item.trim())
        .filter(Boolean);
    }

    function bytesToGiB(value) {
      return Number(value || 0) / 1024 / 1024 / 1024;
    }

    async function loadDiscordPermissions() {
      const payload = await fetch("/discord/permissions").then((r) => r.json());
      const settings = payload.discord_permissions;
      const groupNames = Object.keys(settings.command_groups || {}).sort();
      discordPermissionGroup.innerHTML = groupNames.map((group) => (
        `<option value="${attr(group)}">${escapeHtml(group)}</option>`
      )).join("");
      if (discordSettingsPrefill.permission_group) {
        setFormElementValue(discordGrantForm, "group", discordSettingsPrefill.permission_group);
        discordSettingsPrefill.permission_group = "";
      }

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

    async function loadDiscordScopes() {
      const payload = await fetch("/discord/scopes").then((r) => r.json());
      activeDiscordScopes = payload.discord_scopes || {
        guild_ranking_scopes: {},
        public_profile_default: true,
        updated_at: null,
      };
      if (!activeDiscordScopes.guild_ranking_scopes) {
        activeDiscordScopes.guild_ranking_scopes = {};
      }
      renderDiscordScopes();
      applyPublicProfileDefault();
      if (discordSettingsPrefill.public_profile_default) {
        setFormElementValue(
          publicProfileDefaultForm,
          "public_profile_default",
          discordSettingsPrefill.public_profile_default,
        );
        discordSettingsPrefill.public_profile_default = "";
      }
    }

    function renderDiscordScopes() {
      const entries = Object.entries(activeDiscordScopes.guild_ranking_scopes || {})
        .sort(([left], [right]) => left.localeCompare(right));
      discordScopesBody.innerHTML = entries.map(([guildId, scope]) => `
        <tr>
          <td>${escapeHtml(guildId)}</td>
          <td>${escapeHtml(scope)}</td>
          <td>
            <div class="actions">
              <button
                class="danger"
                type="button"
                data-discord-scope-action="remove"
                data-guild-id="${attr(guildId)}"
              >Remove</button>
            </div>
          </td>
        </tr>
      `).join("") || `<tr><td colspan="3">No guild scope overrides.</td></tr>`;
    }

    function applyPublicProfileDefault() {
      const value = String(activeDiscordScopes.public_profile_default !== false);
      publicProfileDefaultForm.elements.public_profile_default.value = value;
      registerForm.elements.public_profile.value = value;
    }

    async function saveDiscordScopes(nextScopes) {
      const payload = await postJson("/discord/scopes", {
        guild_ranking_scopes: nextScopes.guild_ranking_scopes || {},
        public_profile_default: nextScopes.public_profile_default !== false,
      });
      activeDiscordScopes = payload.discord_scopes;
      renderDiscordScopes();
      applyPublicProfileDefault();
    }

    async function loadPlayers() {
      const payload = await fetch("/players?active_only=false").then((r) => r.json());
      playersBody.innerHTML = payload.players.map((player) => {
        const highlighted = Boolean(
          (registeredPlayerHighlight.account_id && player.account_id === registeredPlayerHighlight.account_id)
          || (
            registeredPlayerHighlight.name
            && player.current_name === registeredPlayerHighlight.name
            && (!registeredPlayerHighlight.shard || player.shard === registeredPlayerHighlight.shard)
          )
        );
        return `
        <tr${highlighted ? ' class="linked-row"' : ""}>
          <td>${escapeHtml(player.shard)}</td>
          <td>${escapeHtml(player.current_name)}</td>
          <td>${escapeHtml(player.account_id)}</td>
          <td>${player.active ? "수집중" : "중지"}</td>
          <td>
            <div class="actions">
              <button class="danger" type="button" onclick="unregisterPlayer('${attr(player.shard)}', '${attr(player.account_id)}')">
                수집 중지
              </button>
            </div>
          </td>
        </tr>`;
      }).join("");
    }

    function dataDeletionActionButtons(request) {
      const buttons = [
        `<button class="secondary" type="button" data-deletion-action="detail" data-request-id="${attr(request.id)}">Detail</button>`,
      ];
      if (request.status === "pending") {
        buttons.push(
          `<button type="button" data-deletion-action="approve" data-request-id="${attr(request.id)}">Approve</button>`,
          `<button class="danger" type="button" data-deletion-action="reject" data-request-id="${attr(request.id)}">Reject</button>`,
          `<button class="secondary" type="button" data-deletion-action="cancel" data-request-id="${attr(request.id)}">Cancel</button>`,
        );
      } else if (request.status === "approved") {
        buttons.push(
          `<button class="secondary" type="button" data-deletion-action="cancel" data-request-id="${attr(request.id)}">Cancel approval</button>`,
        );
      }
      return buttons.join("");
    }

    async function loadDataDeletionRequests() {
      const form = new FormData(dataDeletionFilterForm);
      const status = String(form.get("status") || "pending");
      const response = await fetch(`/data-deletions?status=${encodeURIComponent(status)}&limit=100`);
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || response.statusText);
      }
      const payload = await response.json();
      dataDeletionBody.innerHTML = (payload.requests || []).map((request) => {
        const highlighted = String(request.id) === deletionRequestHighlightId;
        return `
          <tr${highlighted ? ' class="linked-row"' : ""}>
            <td>${escapeHtml(request.id)}</td>
            <td>${escapeHtml(request.player_name)}<br><small>${escapeHtml(request.shard)} / ${escapeHtml(request.account_id)}</small></td>
            <td>${escapeHtml(request.deletion_scope)}</td>
            <td>${escapeHtml(request.status)}</td>
            <td>${escapeHtml(request.requested_at_kst)}</td>
            <td>${escapeHtml(request.expires_at_kst)}</td>
            <td><div class="actions">${dataDeletionActionButtons(request)}</div></td>
          </tr>`;
      }).join("") || `<tr><td colspan="7">No deletion review requests.</td></tr>`;
      dataDeletionStatus.textContent = `${(payload.requests || []).length} request(s). Approval does not execute deletion.`;
    }

    async function loadDataDeletionRequestDetail(requestId) {
      const response = await fetch(`/data-deletions/${encodeURIComponent(requestId)}`);
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || response.statusText);
      }
      const payload = await response.json();
      const request = payload.request;
      const events = payload.events || [];
      dataDeletionDetail.innerHTML = `
        <strong>Request #${escapeHtml(request.id)} / ${escapeHtml(request.status)}</strong>
        <div>${escapeHtml(request.player_name)} / ${escapeHtml(request.shard)} / ${escapeHtml(request.deletion_scope)}</div>
        <div>Reason: ${escapeHtml(request.reason || "-")}</div>
        <div>Reviewer: ${escapeHtml(request.reviewed_by || "-")} / ${escapeHtml(request.review_note || "-")}</div>
        <div>Execution enabled: ${payload.execution_enabled ? "yes" : "no"}</div>
        <ul>
          ${events.map((event) => `<li>${escapeHtml(event.created_at_kst)} / ${escapeHtml(event.event_type)} / ${escapeHtml(event.actor_type)}:${escapeHtml(event.actor_id)} / ${escapeHtml(event.note || "-")}</li>`).join("")}
        </ul>`;
    }

    async function reviewDataDeletionRequest(requestId, action) {
      const form = new FormData(dataDeletionFilterForm);
      const actorId = String(form.get("actor_id") || "").trim();
      const note = String(form.get("note") || "").trim();
      if (!actorId) throw new Error("Local reviewer is required.");
      const warning = action === "approve"
        ? "Approve this request? This records authorization only and does not delete data."
        : `${action} this deletion review request?`;
      if (!window.confirm(warning)) return;
      await postJson(`/data-deletions/${encodeURIComponent(requestId)}/${action}`, {
        actor_id: actorId,
        note: note || null,
      });
      dataDeletionFilterForm.elements.note.value = "";
      deletionRequestHighlightId = String(requestId);
      dataDeletionFilterForm.elements.status.value = "all";
      await loadDataDeletionRequests();
      await loadDataDeletionRequestDetail(requestId);
      dataDeletionStatus.textContent = `${action} recorded. Deletion execution remains disabled.`;
    }

    function firstUrlParam(params, keys) {
      for (const key of keys) {
        const value = params.get(key);
        if (value !== null && value !== "") return value;
      }
      return "";
    }

    function lookupUrlChoice(value, allowed, fallback) {
      const text = String(value || fallback);
      return allowed.includes(text) ? text : fallback;
    }

    function lookupUrlBoundedNumber(value, fallback, min, max) {
      const parsed = Number(value);
      if (!Number.isFinite(parsed)) return fallback;
      return Math.max(min, Math.min(Math.floor(parsed), max));
    }

    function setFormElementValue(form, name, value) {
      if (!form || value === "") return;
      const element = form.elements[name];
      if (element) element.value = value;
    }

    function shouldPrefillSection(hash, sectionId) {
      return !hash || hash === sectionId;
    }

    function loadInitialLookupPrefillFromUrl() {
      const params = new URLSearchParams(window.location.search);
      const lookupKeys = [
        "lookup_shard",
        "shard",
        "lookup_target",
        "target",
        "name",
        "account_id",
        "lookup_weapon",
        "weapon",
        "weapon_code",
        "lookup_match_id",
        "match_id",
        "lookup_min_matches",
        "min_matches",
        "replay_account_id",
        "replay_artifact_id",
        "artifact_id",
        "registered_shard",
        "registered_account_id",
        "registered_name",
        "ranking_metric",
        "ranking_shard",
        "ranking_guild_id",
        "ranking_limit",
        "discord_permission_user_id",
        "discord_permission_group",
        "discord_permission_guild_id",
        "discord_scope_guild_id",
        "discord_scope_value",
        "collector_poll_interval_seconds",
        "collector_cycle_player_limit",
        "collector_player_lookup_chunk_size",
        "discord_public_profile_default",
        "deletion_request_id",
      ];
      if (!lookupKeys.some((key) => params.has(key))) return false;

      const hash = window.location.hash.replace(/^#/, "");
      const shard = lookupUrlChoice(firstUrlParam(params, ["lookup_shard", "shard"]), ["steam", "kakao"], "steam");
      const target = firstUrlParam(params, ["lookup_target", "target", "name", "account_id"]);
      const weapon = firstUrlParam(params, ["lookup_weapon", "weapon", "weapon_code"]);
      const matchId = firstUrlParam(params, ["lookup_match_id", "match_id"]);
      const minMatches = lookupUrlBoundedNumber(firstUrlParam(params, ["lookup_min_matches", "min_matches"]), 1, 1, 50);
      const replayAccountId = firstUrlParam(params, ["replay_account_id", "account_id"])
        || (target.startsWith("account.") ? target : "");
      const replayArtifactId = firstUrlParam(params, ["replay_artifact_id", "artifact_id"]);
      const registeredShard = lookupUrlChoice(firstUrlParam(params, ["registered_shard", "shard"]), ["steam", "kakao"], "steam");
      const registeredAccountId = firstUrlParam(params, ["registered_account_id", "account_id"]);
      const registeredName = firstUrlParam(params, ["registered_name", "name", "target"]);
      const rankingShard = lookupUrlChoice(firstUrlParam(params, ["ranking_shard", "shard"]), ["steam", "kakao"], "steam");
      const rankingMetric = firstUrlParam(params, ["ranking_metric", "metric"]) || "kda";
      const rankingGuildId = firstUrlParam(params, ["ranking_guild_id", "guild_id"]);
      const rankingLimit = lookupUrlBoundedNumber(firstUrlParam(params, ["ranking_limit", "limit"]), 10, 1, 100);
      const discordPermissionUserId = firstUrlParam(params, ["discord_permission_user_id"]);
      const discordPermissionGroupValue = firstUrlParam(params, ["discord_permission_group"]);
      const discordPermissionGuildId = firstUrlParam(params, ["discord_permission_guild_id"]);
      const discordScopeGuildId = firstUrlParam(params, ["discord_scope_guild_id"]);
      const discordScopeValue = lookupUrlChoice(
        firstUrlParam(params, ["discord_scope_value"]),
        ["guild", "global"],
        "guild",
      );
      const collectorPollInterval = firstUrlParam(params, ["collector_poll_interval_seconds"]);
      const collectorCyclePlayerLimit = firstUrlParam(params, ["collector_cycle_player_limit"]);
      const collectorLookupChunkSize = firstUrlParam(params, ["collector_player_lookup_chunk_size"]);
      const discordPublicProfileDefault = lookupUrlChoice(
        firstUrlParam(params, ["discord_public_profile_default"]),
        ["true", "false"],
        "",
      );
      const deletionRequestId = firstUrlParam(params, ["deletion_request_id"]);

      if (shouldPrefillSection(hash, "data-deletions") && /^\\d+$/.test(deletionRequestId)) {
        deletionRequestHighlightId = deletionRequestId;
        setFormElementValue(dataDeletionFilterForm, "status", "all");
      }

      if (shouldPrefillSection(hash, "collector-settings")) {
        localSettingsPrefill = {
          collector_poll_interval_seconds: collectorPollInterval
            ? String(lookupUrlBoundedNumber(collectorPollInterval, 180, 60, 300))
            : "",
          collector_cycle_player_limit: collectorCyclePlayerLimit
            ? String(lookupUrlBoundedNumber(collectorCyclePlayerLimit, 100, 1, 100))
            : "",
          collector_player_lookup_chunk_size: collectorLookupChunkSize
            ? String(lookupUrlBoundedNumber(collectorLookupChunkSize, 10, 1, 10))
            : "",
        };
      }

      if (shouldPrefillSection(hash, "discord-permissions")) {
        setFormElementValue(discordGrantForm, "user_id", discordPermissionUserId);
        setFormElementValue(discordGrantForm, "guild_id", discordPermissionGuildId);
        discordSettingsPrefill.permission_group = discordPermissionGroupValue;
      }
      if (shouldPrefillSection(hash, "discord-scopes")) {
        setFormElementValue(discordScopeForm, "guild_id", discordScopeGuildId);
        setFormElementValue(discordScopeForm, "scope", discordScopeValue);
        discordSettingsPrefill.public_profile_default = discordPublicProfileDefault;
      }

      if (shouldPrefillSection(hash, "registered-players")) {
        registeredPlayerHighlight = {
          shard: registeredShard,
          account_id: registeredAccountId,
          name: registeredName,
        };
      }
      if (shouldPrefillSection(hash, "ranking-lookup")) {
        setFormElementValue(document.querySelector("#rankingForm"), "shard", rankingShard);
        setFormElementValue(document.querySelector("#rankingForm"), "metric", rankingMetric);
        setFormElementValue(document.querySelector("#rankingForm"), "guild_id", rankingGuildId);
        setFormElementValue(document.querySelector("#rankingForm"), "limit", String(rankingLimit));
      }

      if (shouldPrefillSection(hash, "profile-lookup")) {
        setFormElementValue(document.querySelector("#profileForm"), "shard", shard);
        setFormElementValue(document.querySelector("#profileForm"), "target", target);
      }
      if (shouldPrefillSection(hash, "weapon-lookup")) {
        setFormElementValue(document.querySelector("#weaponForm"), "shard", shard);
        setFormElementValue(document.querySelector("#weaponForm"), "target", target);
        setFormElementValue(document.querySelector("#weaponForm"), "weapon", weapon);
      }
      if (shouldPrefillSection(hash, "recommendation-lookup")) {
        setFormElementValue(document.querySelector("#recommendationForm"), "shard", shard);
        setFormElementValue(document.querySelector("#recommendationForm"), "target", target);
        setFormElementValue(document.querySelector("#recommendationForm"), "min_matches", String(minMatches));
      }
      if (shouldPrefillSection(hash, "match-lookup")) {
        setFormElementValue(document.querySelector("#matchForm"), "shard", shard);
        setFormElementValue(document.querySelector("#matchForm"), "target", target);
        setFormElementValue(document.querySelector("#matchForm"), "match_id", matchId);
      }
      if (shouldPrefillSection(hash, "replay-artifacts") || shouldPrefillSection(hash, "replay-player")) {
        replayArtifactFilter = {
          match_id: matchId,
          account_id: replayAccountId,
          artifact_id: replayArtifactId,
        };
      }
      return true;
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

    async function loadWorkerRuns(options = {}) {
      try {
        const quickRange = normalizeWorkerRunQuickRange(
          options.quick_range ?? workerRunFilterForm.elements.quick_range?.value ?? workerRunPage.quick_range
        );
        applyWorkerRunQuickRange(quickRange);
        const form = new FormData(workerRunFilterForm);
        const selectedWorker = options.worker_name ?? String(form.get("worker_name") || workerRunPage.worker_name || "all");
        const selectedStatus = options.status ?? String(form.get("status") || workerRunPage.status || "all");
        const createdFrom = options.created_from_kst ?? String(form.get("created_from_kst") || workerRunPage.created_from_kst || "");
        const createdTo = options.created_to_kst ?? String(form.get("created_to_kst") || workerRunPage.created_to_kst || "");
        const limit = Number(options.limit || form.get("limit") || workerRunPage.limit || 50);
        const offset = Math.max(0, Number(options.offset ?? workerRunPage.offset ?? 0));
        const params = new URLSearchParams({
          worker_name: selectedWorker === "all" ? "" : selectedWorker,
          status: selectedStatus,
          created_from_kst: createdFrom,
          created_to_kst: createdTo,
          limit: String(limit),
          offset: String(offset),
        });
        const payload = await fetch(`/workers/runs?${params.toString()}`).then((r) => r.json());
        if (payload.detail) throw new Error(payload.detail);
        const page = payload.worker_run_page || {
          records: payload.runs || [],
          total: (payload.runs || []).length,
          limit,
          offset,
          worker_name: selectedWorker === "all" ? null : selectedWorker,
          status: selectedStatus,
          quick_range: quickRange,
          created_from_kst: createdFrom,
          created_to_kst: createdTo,
        };
        renderWorkerRuns(page.records || payload.runs || [], page, true);
        if (options.updateUrl) {
          updateWorkerRunFilterUrl();
        }
      } catch (error) {
        workerRunsStatus.textContent = `Error: ${error.message}`;
        workerRunsBody.innerHTML = `<tr><td colspan="6">Error: ${escapeHtml(error.message)}</td></tr>`;
      }
    }

    function exportWorkerRunsCsv() {
      const quickRange = normalizeWorkerRunQuickRange(
        workerRunFilterForm.elements.quick_range?.value ?? workerRunPage.quick_range
      );
      applyWorkerRunQuickRange(quickRange);
      const form = new FormData(workerRunFilterForm);
      const selectedWorker = String(form.get("worker_name") || workerRunPage.worker_name || "all");
      const selectedStatus = String(form.get("status") || workerRunPage.status || "all");
      const params = new URLSearchParams({
        worker_name: selectedWorker === "all" ? "" : selectedWorker,
        status: selectedStatus,
        created_from_kst: String(form.get("created_from_kst") || workerRunPage.created_from_kst || ""),
        created_to_kst: String(form.get("created_to_kst") || workerRunPage.created_to_kst || ""),
        limit: "5000",
        offset: "0",
      });
      window.location.href = `/workers/runs/export.csv?${params.toString()}`;
    }

    function renderWorkerRuns(runs, page = {}, syncControls = false) {
      workerRunPage = {
        ...workerRunPage,
        ...page,
        limit: Number(page.limit || workerRunPage.limit || 50),
        offset: Number(page.offset ?? workerRunPage.offset ?? 0),
        total: Number(page.total ?? workerRunPage.total ?? runs.length),
        status: String(page.status || workerRunPage.status || "all"),
        quick_range: normalizeWorkerRunQuickRange(page.quick_range || workerRunPage.quick_range || "custom"),
        created_from_kst: String(page.created_from_kst || workerRunPage.created_from_kst || ""),
        created_to_kst: String(page.created_to_kst || workerRunPage.created_to_kst || ""),
        has_previous: Boolean(page.has_previous),
        has_next: Boolean(page.has_next),
      };
      if (syncControls) {
        workerRunFilterForm.elements.worker_name.value = workerRunPage.worker_name || "all";
        workerRunFilterForm.elements.status.value = workerRunPage.status || "all";
        workerRunFilterForm.elements.quick_range.value = workerRunPage.quick_range || "custom";
        workerRunFilterForm.elements.created_from_kst.value = workerRunDateTimeInputValue(workerRunPage.created_from_kst);
        workerRunFilterForm.elements.created_to_kst.value = workerRunDateTimeInputValue(workerRunPage.created_to_kst);
        workerRunFilterForm.elements.limit.value = String(workerRunPage.limit || 50);
      }
      const start = runs.length ? workerRunPage.offset + 1 : 0;
      const end = runs.length ? workerRunPage.offset + runs.length : 0;
      workerRunsStatus.textContent = [
        `${start}-${end} of ${workerRunPage.total}`,
        `worker ${workerRunPage.worker_name || "all"}`,
        `status ${workerRunPage.status || "all"}`,
        `range ${workerRunPage.quick_range || "custom"}`,
        `created ${workerRunDateRangeLabel(workerRunPage.created_from_kst, workerRunPage.created_to_kst)}`,
      ].join(" / ");
      workerRunsPrev.disabled = !workerRunPage.has_previous;
      workerRunsNext.disabled = !workerRunPage.has_next;
      workerRunsBody.innerHTML = runs.length
        ? runs.map((run) => `
            <tr>
              <td>${escapeHtml(run.worker_name || "")}</td>
              <td>${escapeHtml(run.status || "")}${run.error_count ? ` (${run.error_count})` : ""}</td>
              <td>${escapeHtml(run.finished_at_kst || run.created_at_kst || "")}</td>
              <td>${run.duration_seconds === null || run.duration_seconds === undefined ? "-" : `${Number(run.duration_seconds).toFixed(2)}s`}</td>
              <td>${workerRunSummary(run)}</td>
              <td><button class="secondary" type="button" data-worker-run-detail-id="${attr(run.id)}">Detail</button></td>
            </tr>
          `).join("")
        : `<tr><td colspan="6">No worker runs yet</td></tr>`;
    }

    function normalizeWorkerRunQuickRange(value) {
      const text = String(value || "custom");
      return ["custom", "last_1h", "last_24h", "today", "yesterday", "last_7d"].includes(text) ? text : "custom";
    }

    function applyWorkerRunQuickRange(value) {
      const range = normalizeWorkerRunQuickRange(value);
      workerRunFilterForm.elements.quick_range.value = range;
      if (range === "custom") return;
      const values = workerRunQuickRangeValues(range);
      if (!values) return;
      workerRunFilterForm.elements.created_from_kst.value = values.from;
      workerRunFilterForm.elements.created_to_kst.value = values.to;
    }

    function workerRunQuickRangeValues(range) {
      const now = new Date();
      const dayMs = 24 * 60 * 60 * 1000;
      const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 0, 0, 0, 0);
      if (range === "last_1h") {
        return workerRunQuickRangeResult(new Date(now.getTime() - 60 * 60 * 1000), now);
      }
      if (range === "last_24h") {
        return workerRunQuickRangeResult(new Date(now.getTime() - dayMs), now);
      }
      if (range === "today") {
        return workerRunQuickRangeResult(todayStart, now);
      }
      if (range === "yesterday") {
        return workerRunQuickRangeResult(new Date(todayStart.getTime() - dayMs), todayStart);
      }
      if (range === "last_7d") {
        return workerRunQuickRangeResult(new Date(now.getTime() - 7 * dayMs), now);
      }
      return null;
    }

    function workerRunQuickRangeResult(fromDate, toDate) {
      return {
        from: workerRunLocalDateTimeInputValue(fromDate),
        to: workerRunLocalDateTimeInputValue(toDate),
      };
    }

    function workerRunLocalDateTimeInputValue(date) {
      const pad = (value) => String(value).padStart(2, "0");
      return [
        date.getFullYear(),
        pad(date.getMonth() + 1),
        pad(date.getDate()),
      ].join("-") + `T${pad(date.getHours())}:${pad(date.getMinutes())}`;
    }

    function workerRunDateTimeInputValue(value) {
      if (!value) return "";
      return String(value).replace(" ", "T").slice(0, 16);
    }

    function workerRunDateRangeLabel(fromValue, toValue) {
      const fromText = workerRunDateTimeInputValue(fromValue) || "-";
      const toText = workerRunDateTimeInputValue(toValue) || "-";
      return `${fromText}..${toText}`;
    }

    function workerRunSummary(run) {
      if (run.last_error) {
        return escapeHtml(run.last_error);
      }
      const summary = run.summary || {};
      if (run.worker_name === "collector") {
        return escapeHtml([
          `matches ${summary.collection?.queued_match_jobs ?? "-"}`,
          `stored ${summary.match_jobs?.stored_matches ?? "-"}`,
          `telemetry ${summary.telemetry_jobs?.stored_telemetry ?? "-"}`,
        ].join(" / "));
      }
      return escapeHtml([
        `combat ${summary.combat?.parsed_payloads ?? "-"}`,
        `items ${summary.items?.parsed_payloads ?? "-"}`,
        `movement ${summary.movement?.parsed_payloads ?? "-"}`,
        `maps ${summary.map_snapshots?.generated_snapshots ?? "-"}`,
        `timelines ${summary.replay_timelines?.generated_timelines ?? "-"}`,
      ].join(" / "));
    }

    async function loadWorkerRunDetail(runId, options = {}) {
      workerRunDetail.innerHTML = `<div class="status">Loading worker run #${escapeHtml(runId)} detail...</div>`;
      const payload = await fetch(`/workers/runs/${encodeURIComponent(runId)}`).then((r) => r.json());
      if (payload.detail) throw new Error(payload.detail);
      if (!payload.run) throw new Error("worker run was not returned");
      renderWorkerRunDetail(payload.run);
      if (options.updateUrl !== false) {
        updateWorkerRunDetailUrl(payload.run.id);
      }
      if (options.scroll) {
        workerRunDetail.scrollIntoView({ block: "start" });
      }
    }

    async function loadInitialWorkerRunDetailFromUrl() {
      const params = new URLSearchParams(window.location.search);
      const runId = params.get("worker_run_id") || params.get("worker_run");
      if (!runId) return;
      await loadWorkerRunDetail(runId, { updateUrl: false, scroll: true });
    }

    function renderWorkerRunDetail(run) {
      const metrics = workerRunSummaryMetrics(run.summary || {});
      const errors = workerRunSummaryErrors(run);
      const metricRows = metrics.length
        ? metrics.map((metric) => `
          <tr>
            <th>${escapeHtml(metric.key)}</th>
            <td>${escapeHtml(metric.value)}</td>
          </tr>
        `).join("")
        : `<tr><td colspan="2">No summary metrics</td></tr>`;
      const errorRows = errors.length
        ? errors.map((error, index) => `
          <tr>
            <th>${index + 1}</th>
            <td><pre style="white-space: pre-wrap; margin: 0;">${escapeHtml(error)}</pre></td>
          </tr>
        `).join("")
        : `<tr><td colspan="2">No stored errors</td></tr>`;
      workerRunDetail.innerHTML = `
        <div class="recommendation-line">
          <strong>Worker Run #${escapeHtml(run.id)} detail</strong>
          <div class="actions">
            <button class="secondary" type="button" data-copy-worker-run-link="${attr(run.id)}">Copy link</button>
          </div>
        </div>
        <div class="status" style="margin-top: 6px;">${escapeHtml(workerRunDetailUrl(run.id))}</div>
        <div class="grid" style="margin-top: 10px;">
          ${cell("Worker", escapeHtml(run.worker_name || ""))}
          ${cell("Status", escapeHtml(run.status || ""))}
          ${cell("Finished", escapeHtml(run.finished_at_kst || run.created_at_kst || ""))}
          ${cell("Duration", run.duration_seconds === null || run.duration_seconds === undefined ? "-" : `${Number(run.duration_seconds).toFixed(2)}s`)}
        </div>
        <table class="detail-table">
          <thead><tr><th>Summary metric</th><th>Value</th></tr></thead>
          <tbody>${metricRows}</tbody>
        </table>
        <table class="detail-table">
          <thead><tr><th>#</th><th>Stored error</th></tr></thead>
          <tbody>${errorRows}</tbody>
        </table>
      `;
    }

    function loadInitialWorkerRunFiltersFromUrl() {
      const params = new URLSearchParams(window.location.search);
      const filterKeys = [
        "worker_run_worker",
        "worker_run_status",
        "worker_run_range",
        "worker_run_from",
        "worker_run_to",
        "worker_run_limit",
        "worker_run_offset",
      ];
      if (!filterKeys.some((key) => params.has(key))) return;

      const worker = workerRunUrlWorker(params.get("worker_run_worker") || params.get("worker_runs_worker") || "all");
      const status = workerRunUrlStatus(params.get("worker_run_status") || "all");
      const fromValue = workerRunDateTimeInputValue(
        params.get("worker_run_from") || params.get("worker_run_created_from_kst") || ""
      );
      const toValue = workerRunDateTimeInputValue(
        params.get("worker_run_to") || params.get("worker_run_created_to_kst") || ""
      );
      const quickRange = fromValue || toValue
        ? "custom"
        : normalizeWorkerRunQuickRange(params.get("worker_run_range") || "custom");
      const limit = workerRunUrlBoundedNumber(params.get("worker_run_limit"), 50, 1, 200);
      const offset = workerRunUrlBoundedNumber(params.get("worker_run_offset"), 0, 0, 1000000);

      workerRunFilterForm.elements.worker_name.value = worker;
      workerRunFilterForm.elements.status.value = status;
      workerRunFilterForm.elements.quick_range.value = quickRange;
      workerRunFilterForm.elements.created_from_kst.value = fromValue;
      workerRunFilterForm.elements.created_to_kst.value = toValue;
      workerRunFilterForm.elements.limit.value = String(limit);
      workerRunPage = {
        ...workerRunPage,
        worker_name: worker === "all" ? null : worker,
        status,
        quick_range: quickRange,
        created_from_kst: fromValue,
        created_to_kst: toValue,
        limit,
        offset,
      };
    }

    function workerRunUrlWorker(value) {
      return ["all", "collector", "post_processing"].includes(String(value || "all")) ? String(value || "all") : "all";
    }

    function workerRunUrlStatus(value) {
      return ["all", "succeeded", "failed"].includes(String(value || "all")) ? String(value || "all") : "all";
    }

    function workerRunUrlBoundedNumber(value, fallback, min, max) {
      const parsed = Number(value);
      if (!Number.isFinite(parsed)) return fallback;
      return Math.max(min, Math.min(Math.floor(parsed), max));
    }

    function workerRunFilterUrl() {
      const url = new URL(window.location.href);
      const form = new FormData(workerRunFilterForm);
      const worker = String(form.get("worker_name") || workerRunPage.worker_name || "all");
      const status = String(form.get("status") || workerRunPage.status || "all");
      const createdFrom = String(form.get("created_from_kst") || workerRunPage.created_from_kst || "");
      const createdTo = String(form.get("created_to_kst") || workerRunPage.created_to_kst || "");
      const limit = Number(form.get("limit") || workerRunPage.limit || 50);
      url.searchParams.delete("worker_run_id");
      url.searchParams.delete("worker_run");
      url.searchParams.set("worker_run_worker", worker === "all" ? "all" : worker);
      url.searchParams.set("worker_run_status", status);
      url.searchParams.set("worker_run_range", "custom");
      url.searchParams.set("worker_run_from", workerRunDateTimeInputValue(createdFrom));
      url.searchParams.set("worker_run_to", workerRunDateTimeInputValue(createdTo));
      url.searchParams.set("worker_run_limit", String(limit || 50));
      url.searchParams.set("worker_run_offset", String(workerRunPage.offset || 0));
      url.hash = "worker-runs";
      return url.toString();
    }

    function updateWorkerRunFilterUrl() {
      window.history.replaceState({}, "", workerRunFilterUrl());
    }

    async function copyWorkerRunFilterLink() {
      const url = workerRunFilterUrl();
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(url);
        return url;
      }
      const input = document.createElement("textarea");
      input.value = url;
      input.setAttribute("readonly", "readonly");
      input.style.position = "fixed";
      input.style.opacity = "0";
      document.body.appendChild(input);
      input.select();
      document.execCommand("copy");
      input.remove();
      return url;
    }

    function workerRunDetailUrl(runId) {
      const url = new URL(window.location.href);
      url.searchParams.set("worker_run_id", runId);
      url.hash = "workerRunDetail";
      return url.toString();
    }

    function updateWorkerRunDetailUrl(runId) {
      window.history.replaceState({}, "", workerRunDetailUrl(runId));
    }

    async function copyWorkerRunDetailLink(runId) {
      const url = workerRunDetailUrl(runId);
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(url);
        return url;
      }
      const input = document.createElement("textarea");
      input.value = url;
      input.setAttribute("readonly", "readonly");
      input.style.position = "fixed";
      input.style.opacity = "0";
      document.body.appendChild(input);
      input.select();
      document.execCommand("copy");
      input.remove();
      return url;
    }

    function workerRunSummaryMetrics(summary, prefix = "") {
      if (!summary || typeof summary !== "object" || Array.isArray(summary)) return [];
      const skippedKeys = new Set(["errors"]);
      return Object.entries(summary).flatMap(([key, value]) => {
        if (skippedKeys.has(key)) return [];
        const metricKey = prefix ? `${prefix}.${key}` : key;
        if (value && typeof value === "object" && !Array.isArray(value)) {
          return workerRunSummaryMetrics(value, metricKey);
        }
        if (Array.isArray(value)) {
          return [{ key: metricKey, value: `${value.length} items` }];
        }
        return [{ key: metricKey, value: formatWorkerRunMetricValue(value) }];
      });
    }

    function workerRunSummaryErrors(run) {
      const summary = run.summary || {};
      const rawErrors = summary.errors;
      const errors = Array.isArray(rawErrors)
        ? rawErrors.map((item) => formatWorkerRunMetricValue(item)).filter(Boolean)
        : rawErrors
          ? [formatWorkerRunMetricValue(rawErrors)]
          : [];
      if (run.last_error && !errors.includes(String(run.last_error))) {
        errors.push(String(run.last_error));
      }
      return errors;
    }

    function formatWorkerRunMetricValue(value) {
      if (value === null || value === undefined) return "-";
      if (typeof value === "object") return JSON.stringify(value);
      return String(value);
    }

    async function loadReplayArtifacts(options = {}) {
      const params = new URLSearchParams({ artifact_type: "", limit: "50" });
      const matchId = options.match_id ?? replayArtifactFilter.match_id;
      const accountId = options.account_id ?? replayArtifactFilter.account_id;
      if (matchId) params.set("match_id", matchId);
      if (accountId) params.set("account_id", accountId);
      const payload = await fetch(`/replay/artifacts?${params.toString()}`).then((r) => r.json());
      updateTimelineOptions(payload.artifacts || [], options.artifact_id ?? replayArtifactFilter.artifact_id);
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

    function updateTimelineOptions(artifacts, preferredArtifactId = "") {
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

      if (preferredArtifactId && replayTimelineArtifacts.some((artifact) => String(artifact.id) === String(preferredArtifactId))) {
        timelineSelect.value = String(preferredArtifactId);
      } else if (previous && replayTimelineArtifacts.some((artifact) => String(artifact.id) === previous)) {
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

    async function saveStorageSettings(event) {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const payload = await postJson("/settings/storage", {
        raw_data_dir: String(form.get("raw_data_dir") || "").trim(),
        replay_data_dir: String(form.get("replay_data_dir") || "").trim(),
        raw_compression: String(form.get("raw_compression") || "gzip"),
      });
      storageSettingsStatus.textContent = [
        `Raw ${formatStoragePathStatus(payload.storage_status?.raw_data_dir)}`,
        `Replay ${formatStoragePathStatus(payload.storage_status?.replay_data_dir)}`,
      ].join(" / ");
      await loadStatus();
      await loadAlerts({ renderHistory: false });
    }

    async function saveCollectorSettings(event) {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const payload = await postJson("/settings/collector", {
        poll_interval_seconds: Number(form.get("poll_interval_seconds") || 180),
        cycle_player_limit: Number(form.get("cycle_player_limit") || 100),
        player_lookup_chunk_size: Number(form.get("player_lookup_chunk_size") || 10),
      });
      collectorSettingsStatus.textContent = [
        `${payload.collector.poll_interval_seconds}초`,
        `${payload.collector.cycle_player_limit}명`,
        `chunk ${payload.collector.player_lookup_chunk_size}`,
      ].join(" / ");
      await loadStatus();
    }

    async function loadCollectorWorkerStatus() {
      const payload = await fetch("/collector/worker/status").then((r) => r.json());
      renderCollectorWorkerStatus(payload.worker);
    }

    function renderCollectorWorkerStatus(worker) {
      if (!worker) {
        collectorWorkerStatus.textContent = "Auto collector status unavailable";
        return;
      }
      const state = worker.running
        ? (worker.stop_requested ? "stopping" : "running")
        : "stopped";
      const lastCycle = worker.last_cycle;
      const lastSummary = lastCycle
        ? [
            `last ${escapeHtml(lastCycle.finished_at_kst || "-")}`,
            `new matches ${lastCycle.collection?.queued_match_jobs ?? "-"}`,
            `stored matches ${lastCycle.match_jobs?.stored_matches ?? "-"}`,
            `stored telemetry ${lastCycle.telemetry_jobs?.stored_telemetry ?? "-"}`,
          ].join(" / ")
        : "no cycle yet";
      collectorWorkerStatus.textContent = [
        `Auto collector ${state}`,
        `cycles ${worker.cycle_count || 0}`,
        worker.next_run_at_kst ? `next ${worker.next_run_at_kst}` : null,
        worker.last_error ? `error ${worker.last_error}` : null,
        lastSummary,
      ].filter(Boolean).join(" / ");
    }

    async function startCollectorWorker(event) {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const payload = await postJson("/collector/worker/start", {
        shard: String(form.get("shard") || "").trim() || null,
        match_job_limit: Number(form.get("match_job_limit") || 10),
        telemetry_job_limit: Number(form.get("telemetry_job_limit") || 5),
      });
      renderCollectorWorkerStatus(payload.worker);
      await loadWorkerRuns();
    }

    async function stopCollectorWorker() {
      const payload = await postJson("/collector/worker/stop", {});
      renderCollectorWorkerStatus(payload.worker);
      await loadWorkerRuns();
    }

    async function loadPostProcessingWorkerStatus() {
      const payload = await fetch("/post-processing/worker/status").then((r) => r.json());
      renderPostProcessingWorkerStatus(payload.worker);
    }

    function renderPostProcessingWorkerStatus(worker) {
      if (!worker) {
        postProcessingWorkerStatus.textContent = "Post-processing status unavailable";
        return;
      }
      const state = worker.running
        ? (worker.stop_requested ? "stopping" : "running")
        : "stopped";
      const lastCycle = worker.last_cycle;
      const lastSummary = lastCycle
        ? [
            `last ${escapeHtml(lastCycle.finished_at_kst || "-")}`,
            `combat ${lastCycle.combat?.parsed_payloads ?? "-"}`,
            `items ${lastCycle.items?.parsed_payloads ?? "-"}`,
            `movement ${lastCycle.movement?.parsed_payloads ?? "-"}`,
            `loadout ${lastCycle.loadout_snapshots?.generated_snapshots ?? "-"}`,
            `maps ${lastCycle.map_snapshots?.generated_snapshots ?? "-"}`,
            `timelines ${lastCycle.replay_timelines?.generated_timelines ?? "-"}`,
          ].join(" / ")
        : "no cycle yet";
      postProcessingWorkerStatus.textContent = [
        `Post-processing ${state}`,
        `cycles ${worker.cycle_count || 0}`,
        worker.next_run_at_kst ? `next ${worker.next_run_at_kst}` : null,
        worker.last_error ? `error ${worker.last_error}` : null,
        lastSummary,
      ].filter(Boolean).join(" / ");
    }

    async function startPostProcessingWorker(event) {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const payload = await postJson("/post-processing/worker/start", {
        combat_limit: Number(form.get("combat_limit") || 10),
        item_limit: Number(form.get("item_limit") || 10),
        movement_limit: Number(form.get("movement_limit") || 10),
        loadout_limit: Number(form.get("loadout_limit") || 50),
        map_snapshot_limit: Number(form.get("map_snapshot_limit") || 10),
        timeline_limit: Number(form.get("timeline_limit") || 10),
        force: form.get("force") === "true",
      });
      renderPostProcessingWorkerStatus(payload.worker);
      await loadWorkerRuns();
    }

    async function stopPostProcessingWorker() {
      const payload = await postJson("/post-processing/worker/stop", {});
      renderPostProcessingWorkerStatus(payload.worker);
      await loadWorkerRuns();
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

    registerForm.addEventListener("submit", async (event) => {
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
      applyPublicProfileDefault();
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

    storageSettingsForm.addEventListener("submit", async (event) => {
      try {
        await saveStorageSettings(event);
        banner.textContent = "Storage settings saved";
      } catch (error) {
        event.preventDefault();
        storageSettingsStatus.textContent = `Error: ${error.message}`;
        banner.textContent = `Error: ${error.message}`;
      }
    });

    alertSettingsForm.addEventListener("submit", async (event) => {
      try {
        await saveAlertSettings(event);
        banner.textContent = "Alert settings saved";
      } catch (error) {
        event.preventDefault();
        alertSettingsStatus.textContent = `Error: ${error.message}`;
        banner.textContent = `Error: ${error.message}`;
      }
    });

    alertsBody.addEventListener("click", async (event) => {
      const workerRunButton = event.target instanceof Element
        ? event.target.closest("button[data-worker-run-from-alert]")
        : null;
      if (workerRunButton) {
        try {
          await loadWorkerRunDetail(workerRunButton.dataset.workerRunFromAlert || "", { scroll: true });
          banner.textContent = "Worker run detail loaded from alert";
        } catch (error) {
          workerRunsStatus.textContent = `Error: ${error.message}`;
          banner.textContent = `Error: ${error.message}`;
        }
        return;
      }

      const button = event.target instanceof Element
        ? event.target.closest("button[data-alert-action]")
        : null;
      if (!button) return;

      try {
        if (button.dataset.alertAction === "acknowledge") {
          await acknowledgeAlert(button.dataset.alertId || "");
          banner.textContent = "Alert acknowledged";
        } else if (button.dataset.alertAction === "snooze") {
          await snoozeAlert(button.dataset.alertId || "", 60);
          banner.textContent = "Alert snoozed";
        }
      } catch (error) {
        banner.textContent = `Error: ${error.message}`;
      }
    });

    alertHistoryFilterForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await loadAlertHistory({ offset: 0, updateUrl: true });
        banner.textContent = "Alert history loaded";
      } catch (error) {
        alertHistoryStatus.textContent = `Error: ${error.message}`;
        banner.textContent = `Error: ${error.message}`;
      }
    });

    alertHistoryExport.addEventListener("click", () => {
      exportAlertHistoryCsv();
    });

    alertHistoryCopyFilterLink.addEventListener("click", async () => {
      try {
        const url = await copyAlertHistoryFilterLink();
        banner.textContent = `Alert history filter link copied: ${url}`;
      } catch (error) {
        banner.textContent = `Error: ${error.message}`;
      }
    });

    for (const button of alertHistoryPresetButtons) {
      button.addEventListener("click", async () => {
        try {
          await applyAlertHistoryPreset(button.dataset.alertHistoryPreset || "");
          banner.textContent = "Alert history preset loaded";
        } catch (error) {
          alertHistoryStatus.textContent = `Error: ${error.message}`;
          banner.textContent = `Error: ${error.message}`;
        }
      });
    }

    alertHistoryBody.addEventListener("click", async (event) => {
      const workerRunButton = event.target instanceof Element
        ? event.target.closest("button[data-worker-run-from-alert]")
        : null;
      if (workerRunButton) {
        try {
          await loadWorkerRunDetail(workerRunButton.dataset.workerRunFromAlert || "", { scroll: true });
          banner.textContent = "Worker run detail loaded from alert history";
        } catch (error) {
          workerRunsStatus.textContent = `Error: ${error.message}`;
          alertHistoryStatus.textContent = `Error: ${error.message}`;
          banner.textContent = `Error: ${error.message}`;
        }
        return;
      }

      const detailButton = event.target instanceof Element
        ? event.target.closest("button[data-alert-detail-id]")
        : null;
      if (detailButton) {
        try {
          const alert = alertHistoryRecords.find((record) => (
            String(record.id) === String(detailButton.dataset.alertDetailId || "")
          ));
          if (!alert) throw new Error("alert history row is not loaded");
          await loadAlertHistoryDetail(alert);
          banner.textContent = "Alert detail loaded";
        } catch (error) {
          alertHistoryStatus.textContent = `Error: ${error.message}`;
          banner.textContent = `Error: ${error.message}`;
        }
        return;
      }

      const button = event.target instanceof Element
        ? event.target.closest("button[data-alert-note-type]")
        : null;
      if (!button) return;

      try {
        const alert = alertHistoryRecords.find((record) => (
          String(record.id) === String(button.dataset.alertId || "")
        ));
        if (!alert) throw new Error("alert history row is not loaded");
        await loadAlertHistoryDetail(alert, button.dataset.alertNoteType || "note", true);
        banner.textContent = "Alert detail loaded";
      } catch (error) {
        alertHistoryStatus.textContent = `Error: ${error.message}`;
        banner.textContent = `Error: ${error.message}`;
      }
    });

    alertHistoryDetail.addEventListener("submit", async (event) => {
      const form = event.target instanceof Element
        ? event.target.closest("form[data-alert-note-form]")
        : null;
      if (!form) return;
      event.preventDefault();
      try {
        await saveAlertHistoryNoteForm(form);
        banner.textContent = "Alert note saved";
      } catch (error) {
        alertHistoryStatus.textContent = `Error: ${error.message}`;
        banner.textContent = `Error: ${error.message}`;
      }
    });

    alertHistoryDetail.addEventListener("click", async (event) => {
      const workerRunButton = event.target instanceof Element
        ? event.target.closest("button[data-worker-run-from-alert]")
        : null;
      if (workerRunButton) {
        try {
          await loadWorkerRunDetail(workerRunButton.dataset.workerRunFromAlert || "", { scroll: true });
          banner.textContent = "Worker run detail loaded from alert";
        } catch (error) {
          workerRunsStatus.textContent = `Error: ${error.message}`;
          alertHistoryStatus.textContent = `Error: ${error.message}`;
          banner.textContent = `Error: ${error.message}`;
        }
        return;
      }

      const button = event.target instanceof Element
        ? event.target.closest("button[data-alert-detail-action]")
        : null;
      if (!button) return;

      try {
        if (button.dataset.alertDetailAction === "acknowledge") {
          await acknowledgeAlert(button.dataset.alertId || "");
          banner.textContent = "Alert acknowledged";
        } else if (button.dataset.alertDetailAction === "snooze") {
          await snoozeAlert(button.dataset.alertId || "", 60);
          banner.textContent = "Alert snoozed";
        }
      } catch (error) {
        alertHistoryStatus.textContent = `Error: ${error.message}`;
        banner.textContent = `Error: ${error.message}`;
      }
    });

    alertHistoryPrev.addEventListener("click", async () => {
      try {
        await loadAlertHistory({
          offset: Math.max(0, alertHistoryPage.offset - alertHistoryPage.limit),
          updateUrl: true,
        });
      } catch (error) {
        alertHistoryStatus.textContent = `Error: ${error.message}`;
        banner.textContent = `Error: ${error.message}`;
      }
    });

    alertHistoryNext.addEventListener("click", async () => {
      try {
        await loadAlertHistory({
          offset: alertHistoryPage.offset + alertHistoryPage.limit,
          updateUrl: true,
        });
      } catch (error) {
        alertHistoryStatus.textContent = `Error: ${error.message}`;
        banner.textContent = `Error: ${error.message}`;
      }
    });

    workerRunFilterForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await loadWorkerRuns({ offset: 0, updateUrl: true });
        banner.textContent = "Worker run history loaded";
      } catch (error) {
        workerRunsStatus.textContent = `Error: ${error.message}`;
        banner.textContent = `Error: ${error.message}`;
      }
    });

    workerRunsExport.addEventListener("click", () => {
      exportWorkerRunsCsv();
    });

    workerRunsCopyFilterLink.addEventListener("click", async () => {
      try {
        const url = await copyWorkerRunFilterLink();
        banner.textContent = `Worker run filter link copied: ${url}`;
      } catch (error) {
        banner.textContent = `Error: ${error.message}`;
      }
    });

    workerRunFilterForm.elements.quick_range.addEventListener("change", () => {
      applyWorkerRunQuickRange(workerRunFilterForm.elements.quick_range.value);
    });

    workerRunFilterForm.elements.created_from_kst.addEventListener("input", () => {
      workerRunFilterForm.elements.quick_range.value = "custom";
      workerRunPage.quick_range = "custom";
    });

    workerRunFilterForm.elements.created_to_kst.addEventListener("input", () => {
      workerRunFilterForm.elements.quick_range.value = "custom";
      workerRunPage.quick_range = "custom";
    });

    workerRunsPrev.addEventListener("click", async () => {
      try {
        await loadWorkerRuns({
          offset: Math.max(0, workerRunPage.offset - workerRunPage.limit),
          updateUrl: true,
        });
      } catch (error) {
        workerRunsStatus.textContent = `Error: ${error.message}`;
        banner.textContent = `Error: ${error.message}`;
      }
    });

    workerRunsNext.addEventListener("click", async () => {
      try {
        await loadWorkerRuns({
          offset: workerRunPage.offset + workerRunPage.limit,
          updateUrl: true,
        });
      } catch (error) {
        workerRunsStatus.textContent = `Error: ${error.message}`;
        banner.textContent = `Error: ${error.message}`;
      }
    });

    workerRunsBody.addEventListener("click", async (event) => {
      const detailButton = event.target instanceof Element
        ? event.target.closest("button[data-worker-run-detail-id]")
        : null;
      if (!detailButton) return;
      try {
        await loadWorkerRunDetail(detailButton.dataset.workerRunDetailId || "");
        banner.textContent = "Worker run detail loaded";
      } catch (error) {
        workerRunsStatus.textContent = `Error: ${error.message}`;
        workerRunDetail.innerHTML = `<div class="status">Error: ${escapeHtml(error.message)}</div>`;
        banner.textContent = `Error: ${error.message}`;
      }
    });

    workerRunDetail.addEventListener("click", async (event) => {
      const copyButton = event.target instanceof Element
        ? event.target.closest("button[data-copy-worker-run-link]")
        : null;
      if (!copyButton) return;
      try {
        const url = await copyWorkerRunDetailLink(copyButton.dataset.copyWorkerRunLink || "");
        banner.textContent = `Worker run detail link copied: ${url}`;
      } catch (error) {
        banner.textContent = `Error: ${error.message}`;
      }
    });

    collectorSettingsForm.addEventListener("submit", async (event) => {
      try {
        await saveCollectorSettings(event);
        banner.textContent = "Collector settings saved";
      } catch (error) {
        event.preventDefault();
        collectorSettingsStatus.textContent = `Error: ${error.message}`;
        banner.textContent = `Error: ${error.message}`;
      }
    });

    collectorWorkerForm.addEventListener("submit", async (event) => {
      try {
        await startCollectorWorker(event);
        banner.textContent = "Auto collector started";
      } catch (error) {
        event.preventDefault();
        collectorWorkerStatus.textContent = `Error: ${error.message}`;
        banner.textContent = `Error: ${error.message}`;
      }
    });

    collectorWorkerStop.addEventListener("click", async () => {
      try {
        await stopCollectorWorker();
        banner.textContent = "Auto collector stop requested";
      } catch (error) {
        collectorWorkerStatus.textContent = `Error: ${error.message}`;
        banner.textContent = `Error: ${error.message}`;
      }
    });

    postProcessingWorkerForm.addEventListener("submit", async (event) => {
      try {
        await startPostProcessingWorker(event);
        banner.textContent = "Post-processing worker started";
      } catch (error) {
        event.preventDefault();
        postProcessingWorkerStatus.textContent = `Error: ${error.message}`;
        banner.textContent = `Error: ${error.message}`;
      }
    });

    postProcessingWorkerStop.addEventListener("click", async () => {
      try {
        await stopPostProcessingWorker();
        banner.textContent = "Post-processing stop requested";
      } catch (error) {
        postProcessingWorkerStatus.textContent = `Error: ${error.message}`;
        banner.textContent = `Error: ${error.message}`;
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

    discordGrantForm.addEventListener("submit", async (event) => {
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

    discordScopeForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const guildId = String(form.get("guild_id") || "").trim();
      const scope = String(form.get("scope") || "guild");
      if (!guildId) {
        banner.textContent = "Guild ID is required.";
        return;
      }
      try {
        await saveDiscordScopes({
          guild_ranking_scopes: {
            ...(activeDiscordScopes.guild_ranking_scopes || {}),
            [guildId]: scope,
          },
          public_profile_default: activeDiscordScopes.public_profile_default !== false,
        });
        event.currentTarget.reset();
        banner.textContent = "Discord scope settings saved.";
      } catch (error) {
        banner.textContent = `Error: ${error.message}`;
      }
    });

    publicProfileDefaultForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      try {
        await saveDiscordScopes({
          guild_ranking_scopes: activeDiscordScopes.guild_ranking_scopes || {},
          public_profile_default: form.get("public_profile_default") === "true",
        });
        banner.textContent = "Public profile default saved.";
      } catch (error) {
        banner.textContent = `Error: ${error.message}`;
      }
    });

    discordScopesBody.addEventListener("click", async (event) => {
      const button = event.target instanceof Element
        ? event.target.closest("button[data-discord-scope-action]")
        : null;
      if (!button) return;

      const guildId = button.dataset.guildId || "";
      const nextGuildScopes = { ...(activeDiscordScopes.guild_ranking_scopes || {}) };
      delete nextGuildScopes[guildId];
      try {
        await saveDiscordScopes({
          guild_ranking_scopes: nextGuildScopes,
          public_profile_default: activeDiscordScopes.public_profile_default !== false,
        });
        banner.textContent = "Discord scope removed.";
      } catch (error) {
        banner.textContent = `Error: ${error.message}`;
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

    dataDeletionFilterForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await loadDataDeletionRequests();
      } catch (error) {
        dataDeletionStatus.textContent = `Error: ${error.message}`;
      }
    });

    dataDeletionBody.addEventListener("click", async (event) => {
      const button = event.target instanceof Element
        ? event.target.closest("button[data-deletion-action]")
        : null;
      if (!button) return;
      const requestId = button.dataset.requestId || "";
      const action = button.dataset.deletionAction || "detail";
      try {
        if (action === "detail") {
          deletionRequestHighlightId = requestId;
          await loadDataDeletionRequestDetail(requestId);
          await loadDataDeletionRequests();
        } else {
          await reviewDataDeletionRequest(requestId, action);
        }
      } catch (error) {
        dataDeletionStatus.textContent = `Error: ${error.message}`;
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
    loadInitialLookupPrefillFromUrl();
    const initialAlertHistoryFilterFromUrl = loadInitialAlertHistoryFiltersFromUrl();
    loadInitialWorkerRunFiltersFromUrl();

    Promise.all([loadStatus(), loadAlerts(), loadDiscordPermissions(), loadDiscordScopes(), loadCollectorWorkerStatus(), loadPostProcessingWorkerStatus(), loadWorkerRuns(), loadPlayers(), loadDataDeletionRequests(), loadJobs(), loadTelemetryJobs(), loadReplayArtifacts()])
      .then(() => initialAlertHistoryFilterFromUrl ? loadAlertHistory(alertHistoryPage) : null)
      .then(() => loadInitialAlertDetailFromUrl())
      .then(() => loadInitialWorkerRunDetailFromUrl())
      .then(() => deletionRequestHighlightId ? loadDataDeletionRequestDetail(deletionRequestHighlightId) : null)
      .then(() => { banner.textContent = "localhost 전용 관리 화면"; })
      .catch((error) => { banner.textContent = `오류: ${error.message}`; });
    setInterval(loadCollectorWorkerStatus, 10000);
    setInterval(loadPostProcessingWorkerStatus, 10000);
    setInterval(loadWorkerRuns, 30000);
    setInterval(() => {
      loadDataDeletionRequests().catch((error) => { dataDeletionStatus.textContent = `Error: ${error.message}`; });
    }, 30000);
    setInterval(() => {
      refreshAlertsAndHistory().catch((error) => { banner.textContent = `Error: ${error.message}`; });
    }, 30000);
  </script>
</body>
</html>
"""
