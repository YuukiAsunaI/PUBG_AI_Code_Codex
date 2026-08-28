from __future__ import annotations

from contextlib import asynccontextmanager
import csv
from datetime import date, datetime
import io
import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

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
from pubg_ai.data_quality import audit_player_intelligence
from pubg_ai.data_deletion_backup import (
    DataDeletionBackupError,
    DataDeletionBackupService,
)
from pubg_ai.data_deletion_backup_builder import (
    DataDeletionBackupBuilderError,
    DataDeletionBackupBuilderService,
)
from pubg_ai.data_deletion_backup_verifier import (
    DataDeletionBackupVerifierError,
    DataDeletionBackupVerifierService,
)
from pubg_ai.data_deletion_combined_rehearsal import (
    DataDeletionCombinedRehearsalError,
    DataDeletionCombinedRehearsalService,
)
from pubg_ai.data_deletion_fault_matrix import (
    DataDeletionFaultMatrixError,
    DataDeletionFaultMatrixService,
)
from pubg_ai.data_deletion_review_packet import (
    DataDeletionReviewPacketError,
    DataDeletionReviewPacketService,
    canonical_review_packet_bytes,
)
from pubg_ai.data_deletion_review_packet_comparer import (
    ExportedReviewPacketComparer,
    ExportedReviewPacketComparerError,
)
from pubg_ai.data_deletion_review_packet_verifier import (
    MAX_EXPORTED_REVIEW_PACKET_BYTES,
    ExportedReviewPacketVerifier,
    ExportedReviewPacketVerifierError,
)
from pubg_ai.data_deletion_quarantine_planner import (
    DataDeletionQuarantinePlannerError,
    DataDeletionQuarantinePlannerService,
)
from pubg_ai.data_deletion_quarantine_rehearsal import (
    DataDeletionQuarantineRehearsalError,
    DataDeletionQuarantineRehearsalService,
)
from pubg_ai.data_deletion_restore_rehearsal import (
    DataDeletionBackupRestoreRehearsalService,
    DataDeletionRestoreRehearsalError,
)
from pubg_ai.data_deletion_confirmation import (
    DataDeletionConfirmationError,
    DataDeletionConfirmationService,
)
from pubg_ai.data_deletion_dry_run import (
    DataDeletionDryRunError,
    DataDeletionDryRunService,
)
from pubg_ai.data_deletion_preview import (
    DEFAULT_PREVIEW_FILE_LIMIT,
    MAX_PREVIEW_FILE_LIMIT,
    DataDeletionImpactPreviewService,
    DataDeletionPreviewError,
)
from pubg_ai.data_deletion_requests import DataDeletionRequestError, DataDeletionRequestService
from pubg_ai.database import connect_mysql, count_tables
from pubg_ai.discord_acceptance import DiscordAcceptanceClient, DiscordAcceptanceError
from pubg_ai.discord_bot_controller import (
    DiscordBotController,
    DiscordBotControllerError,
)
from pubg_ai.discord_command_catalog import (
    RESERVED_COMMAND_GROUPS,
    command_catalog_records,
    command_group_label,
)
from pubg_ai.discord_guild_catalog import (
    list_discord_guild_catalog,
    list_stored_discord_guild_ids,
    sync_discord_guild_catalog,
)
from pubg_ai.fight_outcome_processor import FightOutcomeProcessor
from pubg_ai.fight_outcome_stats import FightOutcomeStatsService
from pubg_ai.flight_path_stats import FlightPathStatsService
from pubg_ai.discord_permission_manager import DiscordPermissionManager
from pubg_ai.discord_permissions import DiscordPermissionChecker
from pubg_ai.env_secrets import EnvSecretError, EnvSecretStore
from pubg_ai.local_settings import LocalSettingsError, LocalSettingsStore, check_storage_path
from pubg_ai.loadout_snapshot_processor import LoadoutSnapshotProcessor
from pubg_ai.map_regions import map_region_catalog_record, resolve_map_region
from pubg_ai.map_snapshot_renderer import MAP_ASSET_FILENAMES, MapAssetProvider, MapSnapshotProcessor
from pubg_ai.operational_drills import (
    OperationalDrillError,
    list_operational_drills,
    record_operational_drill,
    run_operational_drills,
)
from pubg_ai.match_collection import RegisteredPlayerMatchCollector
from pubg_ai.match_explorer import MatchExplorerError, MatchExplorerService
from pubg_ai.match_replay_builder import MatchReplayError, MatchReplayProcessor
from pubg_ai.match_job_processor import MatchJobProcessor
from pubg_ai.metric_catalog import metric_catalog_records
from pubg_ai.player_intelligence import PlayerIntelligenceService
from pubg_ai.player_registry import DiscordCommandContext, PlayerRegistry, PlayerRegistryError
from pubg_ai.player_rankings import PlayerRankingService
from pubg_ai.post_processing_worker import (
    PostProcessingWorkerController,
    PostProcessingWorkerError,
    PostProcessingWorkerOptions,
)
from pubg_ai.player_recommendations import PlayerRecommendationService
from pubg_ai.player_stats import PlayerStatsService
from pubg_ai.player_trends import (
    PlayerTrendFilters,
    PlayerTrendService,
    parse_optional_bool,
    parse_trend_date,
)
from pubg_ai.pubg_client import PubgApiClient, PubgApiError
from pubg_ai.release import APP_RELEASE
from pubg_ai.raw_storage import RawPayloadStore
from pubg_ai.replay_artifact_catalog import get_replay_artifact, list_replay_artifacts
from pubg_ai.replay_storage import ReplayArtifactStore, ReplayStorageError
from pubg_ai.replay_timeline_builder import ReplayTimelineProcessor
from pubg_ai.telemetry_combat_processor import TelemetryCombatProcessor
from pubg_ai.telemetry_activity_processor import TelemetryActivityProcessor
from pubg_ai.telemetry_event_catalog import telemetry_event_catalog_records
from pubg_ai.telemetry_item_processor import TelemetryItemProcessor
from pubg_ai.telemetry_job_processor import TelemetryJobProcessor
from pubg_ai.telemetry_movement_processor import TelemetryMovementProcessor
from pubg_ai.system_alerts import alert_display_message, alert_display_title, collect_system_alerts
from pubg_ai.worker_run_history import (
    WORKER_RUN_EXPORT_LIMIT,
    WorkerRunHistoryError,
    WorkerRunRecord,
    get_worker_run,
    get_worker_run_page,
    list_worker_runs,
)
from pubg_ai.watchlist import WatchlistError, WatchlistService


_ASSET_DIR = Path(__file__).resolve().parent.parent / "assets"


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


class UpdatePlayerManagementRequest(BaseModel):
    shard: str = Field(default="steam", min_length=1)
    account_id: str = Field(min_length=1)
    active: bool | None = None
    public_profile: bool | None = None


class PlayerDiscordRegistrationRequest(BaseModel):
    shard: str = Field(default="steam", min_length=1)
    account_id: str = Field(min_length=1)
    guild_id: str = Field(min_length=1, max_length=32)
    channel_id: str | None = Field(default=None, max_length=32)
    discord_user_id: str | None = Field(default=None, max_length=32)


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


class ParseFightOutcomesRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=200)
    force: bool = False


class GenerateMapSnapshotsRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=200)
    force: bool = False


class GenerateReplayTimelinesRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=200)
    force: bool = False


class GenerateMatchReplayRequest(BaseModel):
    force: bool = False


class RegisterWatchedPlayerRequest(BaseModel):
    shard: str = Field(default="steam", min_length=1)
    player_name: str = Field(min_length=1, max_length=191)
    notify_name_change: bool = True
    notify_kill: bool = True
    notify_repeated_engagement: bool = True
    engagement_threshold: int = Field(default=3, ge=1, le=100)
    notification_channel_ids: list[str] = Field(default_factory=list)


class UpdateWatchedPlayerRequest(BaseModel):
    active: bool = True
    notify_name_change: bool = True
    notify_kill: bool = True
    notify_repeated_engagement: bool = True
    engagement_threshold: int = Field(default=3, ge=1, le=100)
    notification_channel_ids: list[str] = Field(default_factory=list)


class ScanWatchlistRequest(BaseModel):
    limit: int = Field(default=100, ge=1, le=500)
    force: bool = False
    refresh_identity: bool = True


class GenerateLoadoutSnapshotsRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=500)
    force: bool = False


class DiscordPermissionGrantRequest(BaseModel):
    user_id: str = Field(min_length=1)
    group: str = Field(min_length=1)
    guild_id: str | None = None
    member_label: str | None = Field(default=None, max_length=128)
    member_guild_id: str | None = Field(default=None, max_length=32)


class DiscordGlobalAdminRequest(BaseModel):
    user_id: str = Field(min_length=1)
    member_label: str | None = Field(default=None, max_length=128)
    member_guild_id: str | None = Field(default=None, max_length=32)


class DiscordCommandGroupRequest(BaseModel):
    commands: list[str] = Field(min_length=1)


class DiscordCommandAliasRequest(BaseModel):
    target_command: str = Field(min_length=1, max_length=32)


class DiscordScopeSettingsRequest(BaseModel):
    guild_ranking_scopes: dict[str, str] = Field(default_factory=dict)
    guild_ranking_selected_guild_ids: dict[str, list[str]] = Field(default_factory=dict)
    public_profile_default: bool = True


class SecretUpdateRequest(BaseModel):
    value: str = Field(min_length=1, max_length=4096)


class DisplaySettingsRequest(BaseModel):
    number_format: str = Field(pattern=r"^(grouped|korean_units|plain)$")


class DiscordBotSettingsRequest(BaseModel):
    auto_start: bool = False
    command_prefix: str = Field(default="!", min_length=1, max_length=5)
    guild_enabled_commands: dict[str, list[str]] = Field(default_factory=dict)


class DiscordCommandSyncRequest(BaseModel):
    guild_id: str | None = Field(default=None, max_length=32)


class DiscordGuildCommandsRequest(BaseModel):
    guild_id: str = Field(pattern=r"^\d{1,32}$")
    use_default: bool = False
    commands: list[str] = Field(default_factory=list)


class DiscordGuildCatalogSyncRequest(BaseModel):
    prune_stale: bool = False
    confirmation: str | None = Field(default=None, max_length=64)


MANAGED_DISCORD_GUILD_PRUNE_CONFIRMATION = "CONFIRM MANAGED DISCORD GUILD SYNC"


class DataDeletionReviewRequest(BaseModel):
    actor_id: str = Field(default="local-manager", min_length=1, max_length=191)
    note: str | None = Field(default=None, max_length=1000)


class DataDeletionSnapshotCaptureRequest(BaseModel):
    actor_id: str = Field(default="local-manager", min_length=1, max_length=191)
    note: str | None = Field(default=None, max_length=1000)


class DataDeletionConfirmationCreateRequest(BaseModel):
    snapshot_id: int = Field(gt=0)
    fingerprint_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    confirmation_text: str = Field(min_length=1, max_length=512)
    actor_id: str = Field(default="local-manager", min_length=1, max_length=191)
    note: str | None = Field(default=None, max_length=1000)


class DataDeletionDryRunCreateRequest(BaseModel):
    actor_id: str = Field(default="local-manager", min_length=1, max_length=191)
    note: str | None = Field(default=None, max_length=1000)


class DataDeletionBackupEvidenceCreateRequest(BaseModel):
    dry_run_plan_id: int = Field(gt=0)
    prerequisite_key: str = Field(min_length=1, max_length=64)
    artifact_path: str | None = Field(default=None, max_length=1000)
    artifact_sha256: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
    )
    artifact_size_bytes: int | None = Field(default=None, gt=0)
    covered_row_count: int | None = Field(default=None, ge=0)
    covered_file_count: int | None = Field(default=None, ge=0)
    covered_file_bytes: int | None = Field(default=None, ge=0)
    checked_path: str | None = Field(default=None, max_length=1000)
    available_bytes: int | None = Field(default=None, ge=0)
    backup_created_at_kst: datetime | None = None
    verified_at_kst: datetime | None = None
    restore_tested_at_kst: datetime | None = None
    checksums_verified: bool = False
    restore_test_passed: bool = False
    actor_id: str = Field(default="local-manager", min_length=1, max_length=191)
    note: str | None = Field(default=None, max_length=1000)


class DataDeletionBackupBuildCreateRequest(BaseModel):
    dry_run_plan_id: int = Field(gt=0)
    confirmation_text: str = Field(min_length=1, max_length=500)
    actor_id: str = Field(default="local-manager", min_length=1, max_length=191)
    note: str | None = Field(default=None, max_length=500)


class DataDeletionBackupVerificationCreateRequest(BaseModel):
    dry_run_plan_id: int = Field(gt=0)
    manifest_path: str = Field(min_length=1, max_length=1000)
    expected_manifest_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    actor_id: str = Field(default="local-manager", min_length=1, max_length=191)
    note: str | None = Field(default=None, max_length=1000)


class DataDeletionBackupRestoreRehearsalCreateRequest(BaseModel):
    backup_verification_run_id: int = Field(gt=0)
    confirmation_text: str = Field(min_length=1, max_length=500)
    actor_id: str = Field(default="local-manager", min_length=1, max_length=191)
    note: str | None = Field(default=None, max_length=1000)


class DataDeletionQuarantinePlanningCreateRequest(BaseModel):
    dry_run_plan_id: int = Field(gt=0)
    confirmation_text: str = Field(min_length=1, max_length=500)
    actor_id: str = Field(default="local-manager", min_length=1, max_length=191)
    note: str | None = Field(default=None, max_length=1000)


class DataDeletionQuarantineRehearsalCreateRequest(BaseModel):
    quarantine_planning_run_id: int = Field(gt=0)
    confirmation_text: str = Field(min_length=1, max_length=500)
    actor_id: str = Field(default="local-manager", min_length=1, max_length=191)
    note: str | None = Field(default=None, max_length=1000)


class DataDeletionCombinedRehearsalCreateRequest(BaseModel):
    backup_verification_run_id: int = Field(gt=0)
    quarantine_planning_run_id: int = Field(gt=0)
    confirmation_text: str = Field(min_length=1, max_length=700)
    actor_id: str = Field(default="local-manager", min_length=1, max_length=191)
    note: str | None = Field(default=None, max_length=1000)


class DataDeletionFaultMatrixCreateRequest(BaseModel):
    combined_rehearsal_run_id: int = Field(gt=0)
    confirmation_text: str = Field(min_length=1, max_length=900)
    actor_id: str = Field(default="local-manager", min_length=1, max_length=191)
    note: str | None = Field(default=None, max_length=1000)


class DataDeletionReviewPacketCreateRequest(BaseModel):
    fault_matrix_run_id: int = Field(gt=0)
    confirmation_text: str = Field(min_length=1, max_length=500)
    actor_id: str = Field(default="local-manager", min_length=1, max_length=191)
    note: str | None = Field(default=None, max_length=1000)


class ExportedReviewPacketVerifyRequest(BaseModel):
    packet_text: str = Field(
        min_length=2,
        max_length=MAX_EXPORTED_REVIEW_PACKET_BYTES,
    )
    cross_check_database: bool = True


class ExportedReviewPacketCompareRequest(BaseModel):
    baseline_packet_text: str = Field(
        min_length=2,
        max_length=MAX_EXPORTED_REVIEW_PACKET_BYTES,
    )
    candidate_packet_text: str = Field(
        min_length=2,
        max_length=MAX_EXPORTED_REVIEW_PACKET_BYTES,
    )
    cross_check_database: bool = True


class DataDeletionRehearsalCreateRequest(BaseModel):
    dry_run_plan_id: int = Field(gt=0)
    actor_id: str = Field(default="local-manager", min_length=1, max_length=191)
    note: str | None = Field(default=None, max_length=1000)


class WebSettingsRequest(BaseModel):
    local_web_base_url: str | None = None


class StorageSettingsRequest(BaseModel):
    raw_data_dir: str = Field(min_length=1)
    replay_data_dir: str = Field(min_length=1)
    backup_data_dir: str = Field(min_length=1)
    quarantine_data_dir: str = Field(min_length=1)
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
    activity_limit: int = Field(default=10, ge=1, le=200)
    item_limit: int = Field(default=10, ge=1, le=200)
    movement_limit: int = Field(default=10, ge=1, le=200)
    loadout_limit: int = Field(default=50, ge=1, le=500)
    fight_outcome_limit: int = Field(default=10, ge=1, le=200)
    map_snapshot_limit: int = Field(default=10, ge=1, le=200)
    timeline_limit: int = Field(default=10, ge=1, le=200)
    force: bool = False


class OperationalDrillRunRequest(BaseModel):
    mode: str = Field(default="simulated", pattern=r"^(simulated|live)$")
    cycles: int = Field(default=3, ge=2, le=5)


def create_app(*, base_dir: Path | None = None, env_file: str = ".env") -> Any:
    base_dir = (base_dir or Path.cwd()).resolve()
    config = RuntimeConfig.from_sources(base_dir=base_dir, env_file=env_file)
    _ensure_configured_storage_directories(config)
    settings_store = _local_settings_store(base_dir, env_file=env_file)
    permission_manager = DiscordPermissionManager(settings_store)
    env_path = Path(env_file).expanduser()
    if not env_path.is_absolute():
        env_path = base_dir / env_path
    secret_store = EnvSecretStore(env_path)

    def current_config() -> RuntimeConfig:
        return RuntimeConfig.from_sources(base_dir=base_dir, env_file=env_file)

    def build_data_deletion_confirmation_service(
        connection: Any,
        runtime_config: RuntimeConfig,
    ) -> DataDeletionConfirmationService:
        return DataDeletionConfirmationService(
            connection,
            preview_service=DataDeletionImpactPreviewService(
                connection,
                raw_data_dir=runtime_config.app.raw_data_dir,
                replay_data_dir=runtime_config.app.replay_data_dir,
            ),
        )

    def build_data_deletion_dry_run_service(
        connection: Any,
        runtime_config: RuntimeConfig,
    ) -> DataDeletionDryRunService:
        preview_service = DataDeletionImpactPreviewService(
            connection,
            raw_data_dir=runtime_config.app.raw_data_dir,
            replay_data_dir=runtime_config.app.replay_data_dir,
        )
        confirmation_service = DataDeletionConfirmationService(
            connection,
            preview_service=preview_service,
        )
        return DataDeletionDryRunService(
            connection,
            preview_service=preview_service,
            confirmation_service=confirmation_service,
        )

    def build_data_deletion_backup_service(
        connection: Any,
        runtime_config: RuntimeConfig,
    ) -> DataDeletionBackupService:
        preview_service = DataDeletionImpactPreviewService(
            connection,
            raw_data_dir=runtime_config.app.raw_data_dir,
            replay_data_dir=runtime_config.app.replay_data_dir,
        )
        confirmation_service = DataDeletionConfirmationService(
            connection,
            preview_service=preview_service,
        )
        dry_run_service = DataDeletionDryRunService(
            connection,
            preview_service=preview_service,
            confirmation_service=confirmation_service,
        )
        return DataDeletionBackupService(
            connection,
            dry_run_service=dry_run_service,
            preview_service=preview_service,
            quarantine_data_dir=runtime_config.app.quarantine_data_dir,
        )

    def build_data_deletion_backup_builder_service(
        connection: Any,
        runtime_config: RuntimeConfig,
        *,
        backup_service: DataDeletionBackupService | None = None,
    ) -> DataDeletionBackupBuilderService:
        service = backup_service or build_data_deletion_backup_service(
            connection,
            runtime_config,
        )
        return DataDeletionBackupBuilderService(
            connection,
            backup_service=service,
            backup_root=runtime_config.app.backup_data_dir,
            raw_data_dir=runtime_config.app.raw_data_dir,
            replay_data_dir=runtime_config.app.replay_data_dir,
        )

    def build_data_deletion_backup_verifier_service(
        connection: Any,
        runtime_config: RuntimeConfig,
        *,
        backup_service: DataDeletionBackupService | None = None,
    ) -> DataDeletionBackupVerifierService:
        service = backup_service or build_data_deletion_backup_service(
            connection,
            runtime_config,
        )
        return DataDeletionBackupVerifierService(
            connection,
            backup_service=service,
            backup_root=runtime_config.app.backup_data_dir,
            raw_data_dir=runtime_config.app.raw_data_dir,
            replay_data_dir=runtime_config.app.replay_data_dir,
        )

    def build_data_deletion_restore_rehearsal_service(
        connection: Any,
        runtime_config: RuntimeConfig,
        *,
        backup_service: DataDeletionBackupService | None = None,
        verifier_service: DataDeletionBackupVerifierService | None = None,
    ) -> DataDeletionBackupRestoreRehearsalService:
        backup = backup_service or build_data_deletion_backup_service(
            connection,
            runtime_config,
        )
        verifier = verifier_service or build_data_deletion_backup_verifier_service(
            connection,
            runtime_config,
            backup_service=backup,
        )
        return DataDeletionBackupRestoreRehearsalService(
            connection,
            backup_service=backup,
            verifier_service=verifier,
            scratch_connection_factory=lambda: connect_mysql(runtime_config.database),
            backup_root=runtime_config.app.backup_data_dir,
            expected_database_name=runtime_config.database.database,
        )

    def build_data_deletion_quarantine_planner_service(
        connection: Any,
        runtime_config: RuntimeConfig,
        *,
        backup_service: DataDeletionBackupService | None = None,
    ) -> DataDeletionQuarantinePlannerService:
        backup = backup_service or build_data_deletion_backup_service(
            connection,
            runtime_config,
        )
        return DataDeletionQuarantinePlannerService(
            connection,
            backup_service=backup,
            quarantine_root=runtime_config.app.quarantine_data_dir,
            raw_data_dir=runtime_config.app.raw_data_dir,
            replay_data_dir=runtime_config.app.replay_data_dir,
            backup_root=runtime_config.app.backup_data_dir,
        )

    def build_data_deletion_quarantine_rehearsal_service(
        connection: Any,
        runtime_config: RuntimeConfig,
        *,
        backup_service: DataDeletionBackupService | None = None,
        planner_service: DataDeletionQuarantinePlannerService | None = None,
    ) -> DataDeletionQuarantineRehearsalService:
        backup = backup_service or build_data_deletion_backup_service(
            connection,
            runtime_config,
        )
        planner = planner_service or build_data_deletion_quarantine_planner_service(
            connection,
            runtime_config,
            backup_service=backup,
        )
        return DataDeletionQuarantineRehearsalService(
            connection,
            backup_service=backup,
            planner_service=planner,
            quarantine_root=runtime_config.app.quarantine_data_dir,
            raw_data_dir=runtime_config.app.raw_data_dir,
            replay_data_dir=runtime_config.app.replay_data_dir,
            backup_root=runtime_config.app.backup_data_dir,
        )

    def build_data_deletion_combined_rehearsal_service(
        connection: Any,
        runtime_config: RuntimeConfig,
        *,
        backup_service: DataDeletionBackupService | None = None,
        verifier_service: DataDeletionBackupVerifierService | None = None,
        planner_service: DataDeletionQuarantinePlannerService | None = None,
        quarantine_rehearsal_service: DataDeletionQuarantineRehearsalService | None = None,
    ) -> DataDeletionCombinedRehearsalService:
        backup = backup_service or build_data_deletion_backup_service(
            connection,
            runtime_config,
        )
        verifier = verifier_service or build_data_deletion_backup_verifier_service(
            connection,
            runtime_config,
            backup_service=backup,
        )
        planner = planner_service or build_data_deletion_quarantine_planner_service(
            connection,
            runtime_config,
            backup_service=backup,
        )
        quarantine = (
            quarantine_rehearsal_service
            or build_data_deletion_quarantine_rehearsal_service(
                connection,
                runtime_config,
                backup_service=backup,
                planner_service=planner,
            )
        )
        return DataDeletionCombinedRehearsalService(
            connection,
            backup_service=backup,
            verifier_service=verifier,
            quarantine_rehearsal_service=quarantine,
            scratch_connection_factory=lambda: connect_mysql(runtime_config.database),
            backup_root=runtime_config.app.backup_data_dir,
            expected_database_name=runtime_config.database.database,
        )

    def build_data_deletion_fault_matrix_service(
        connection: Any,
        runtime_config: RuntimeConfig,
        *,
        backup_service: DataDeletionBackupService | None = None,
        verifier_service: DataDeletionBackupVerifierService | None = None,
        planner_service: DataDeletionQuarantinePlannerService | None = None,
        quarantine_rehearsal_service: DataDeletionQuarantineRehearsalService | None = None,
        combined_rehearsal_service: DataDeletionCombinedRehearsalService | None = None,
    ) -> DataDeletionFaultMatrixService:
        backup = backup_service or build_data_deletion_backup_service(
            connection,
            runtime_config,
        )
        verifier = verifier_service or build_data_deletion_backup_verifier_service(
            connection,
            runtime_config,
            backup_service=backup,
        )
        planner = planner_service or build_data_deletion_quarantine_planner_service(
            connection,
            runtime_config,
            backup_service=backup,
        )
        quarantine = (
            quarantine_rehearsal_service
            or build_data_deletion_quarantine_rehearsal_service(
                connection,
                runtime_config,
                backup_service=backup,
                planner_service=planner,
            )
        )
        combined = (
            combined_rehearsal_service
            or build_data_deletion_combined_rehearsal_service(
                connection,
                runtime_config,
                backup_service=backup,
                verifier_service=verifier,
                planner_service=planner,
                quarantine_rehearsal_service=quarantine,
            )
        )
        return DataDeletionFaultMatrixService(
            connection,
            backup_service=backup,
            verifier_service=verifier,
            quarantine_rehearsal_service=quarantine,
            combined_rehearsal_service=combined,
            scratch_connection_factory=lambda: connect_mysql(runtime_config.database),
            backup_root=runtime_config.app.backup_data_dir,
            expected_database_name=runtime_config.database.database,
        )

    def build_data_deletion_review_packet_service(
        connection: Any,
        runtime_config: RuntimeConfig,
        *,
        backup_service: DataDeletionBackupService | None = None,
        verifier_service: DataDeletionBackupVerifierService | None = None,
        planner_service: DataDeletionQuarantinePlannerService | None = None,
        quarantine_rehearsal_service: DataDeletionQuarantineRehearsalService | None = None,
        combined_rehearsal_service: DataDeletionCombinedRehearsalService | None = None,
        fault_matrix_service: DataDeletionFaultMatrixService | None = None,
    ) -> DataDeletionReviewPacketService:
        backup = backup_service or build_data_deletion_backup_service(
            connection,
            runtime_config,
        )
        verifier = verifier_service or build_data_deletion_backup_verifier_service(
            connection,
            runtime_config,
            backup_service=backup,
        )
        planner = planner_service or build_data_deletion_quarantine_planner_service(
            connection,
            runtime_config,
            backup_service=backup,
        )
        quarantine = (
            quarantine_rehearsal_service
            or build_data_deletion_quarantine_rehearsal_service(
                connection,
                runtime_config,
                backup_service=backup,
                planner_service=planner,
            )
        )
        combined = (
            combined_rehearsal_service
            or build_data_deletion_combined_rehearsal_service(
                connection,
                runtime_config,
                backup_service=backup,
                verifier_service=verifier,
                planner_service=planner,
                quarantine_rehearsal_service=quarantine,
            )
        )
        fault_matrix = (
            fault_matrix_service
            or build_data_deletion_fault_matrix_service(
                connection,
                runtime_config,
                backup_service=backup,
                verifier_service=verifier,
                planner_service=planner,
                quarantine_rehearsal_service=quarantine,
                combined_rehearsal_service=combined,
            )
        )
        return DataDeletionReviewPacketService(
            connection,
            backup_service=backup,
            verifier_service=verifier,
            planner_service=planner,
            combined_rehearsal_service=combined,
            fault_matrix_service=fault_matrix,
        )

    collector_worker = CollectorWorkerController(config_loader=current_config)
    post_processing_worker = PostProcessingWorkerController(config_loader=current_config)
    discord_bot_controller = DiscordBotController(
        config_loader=current_config,
        settings_store=settings_store,
        permission_checker=DiscordPermissionChecker(
            settings_store.load_discord_permission_settings()
        ),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> Any:
        bot_settings = settings_store.load_discord_bot_settings()
        if bot_settings.auto_start and current_config().secrets.discord_bot_token:
            try:
                discord_bot_controller.start()
            except DiscordBotControllerError:
                pass
        try:
            yield
        finally:
            try:
                discord_bot_controller.stop()
            except DiscordBotControllerError:
                pass
            collector_worker.stop()
            post_processing_worker.stop()

    app = FastAPI(
        title="PUBG AI Local Manager",
        version="0.1.0",
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"],
    )
    app.state.discord_bot_controller = discord_bot_controller

    @app.middleware("http")
    async def enforce_local_browser_boundary(request: Request, call_next: Any) -> Response:
        blocked = False
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            fetch_site = request.headers.get("sec-fetch-site", "").strip().lower()
            origin = request.headers.get("origin", "").strip()
            expected_origin = str(request.base_url).rstrip("/")
            blocked = (
                fetch_site == "cross-site"
                or origin == "null"
                or bool(origin and origin.rstrip("/") != expected_origin)
            )

        response = (
            Response(content="Cross-origin state changes are not allowed.", status_code=403)
            if blocked
            else await call_next(request)
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        display_settings = settings_store.load_display_settings()
        return (
            _INDEX_HTML
            .replace("__PUBG_AI_APP_RELEASE__", APP_RELEASE)
            .replace("__PUBG_AI_NUMBER_FORMAT__", display_settings.number_format)
        )

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "local_only": True,
            "bind_host": "127.0.0.1",
            "app_release": APP_RELEASE,
        }

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> FileResponse:
        return FileResponse(_ASSET_DIR / "app_icon.ico", media_type="image/x-icon")

    @app.get("/assets/app-icon.png", include_in_schema=False)
    def app_icon() -> FileResponse:
        return FileResponse(_ASSET_DIR / "app_icon.png", media_type="image/png")

    @app.get("/settings/status")
    def settings_status() -> dict[str, Any]:
        return _settings_status_record(current_config())

    def save_managed_secret(name: str, value: str) -> dict[str, Any]:
        try:
            secret_store.set_secret(name, value)
        except EnvSecretError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        runtime_config = current_config()
        configured = (
            bool(runtime_config.secrets.pubg_api_key)
            if name == "PUBG_API_KEY"
            else bool(runtime_config.secrets.discord_bot_token)
        )
        return {"configured": configured, "settings": _settings_status_record(runtime_config)}

    @app.post("/settings/secrets/pubg")
    def save_pubg_api_key(request: SecretUpdateRequest) -> dict[str, Any]:
        return save_managed_secret("PUBG_API_KEY", request.value)

    @app.post("/settings/secrets/discord")
    def save_discord_bot_token(request: SecretUpdateRequest) -> dict[str, Any]:
        return save_managed_secret("DISCORD_BOT_TOKEN", request.value)

    @app.delete("/settings/secrets/pubg")
    def clear_pubg_api_key() -> dict[str, Any]:
        try:
            secret_store.clear_secret("PUBG_API_KEY")
        except EnvSecretError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"configured": False, "settings": _settings_status_record(current_config())}

    @app.delete("/settings/secrets/discord")
    def clear_discord_bot_token() -> dict[str, Any]:
        try:
            discord_bot_controller.stop()
            secret_store.clear_secret("DISCORD_BOT_TOKEN")
        except (DiscordBotControllerError, EnvSecretError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"configured": False, "settings": _settings_status_record(current_config())}

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

    @app.get("/settings/display")
    def display_settings() -> dict[str, Any]:
        try:
            settings = settings_store.load_display_settings()
        except LocalSettingsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"display": settings.to_record()}

    @app.post("/settings/display")
    def save_display_settings(request: DisplaySettingsRequest) -> dict[str, Any]:
        try:
            settings = settings_store.save_display_settings(request.number_format)
        except LocalSettingsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"display": settings.to_record()}

    @app.post("/settings/storage")
    def save_storage_settings(request: StorageSettingsRequest) -> dict[str, Any]:
        try:
            storage_settings = settings_store.save_storage_settings(
                raw_data_dir=request.raw_data_dir,
                replay_data_dir=request.replay_data_dir,
                backup_data_dir=request.backup_data_dir,
                quarantine_data_dir=request.quarantine_data_dir,
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
            "alert": _alert_record_with_display(record.to_record()),
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
                    activity_limit=request.activity_limit,
                    item_limit=request.item_limit,
                    movement_limit=request.movement_limit,
                    loadout_limit=request.loadout_limit,
                    fight_outcome_limit=request.fight_outcome_limit,
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

    @app.post("/operations/drills")
    def run_operations_drill(request: OperationalDrillRunRequest) -> dict[str, Any]:
        if request.mode == "live" and collector_worker.status().running:
            raise HTTPException(
                status_code=409,
                detail="Stop the automatic collector before running a live operational drill.",
            )
        try:
            report = run_operational_drills(
                current_config(),
                mode=request.mode,  # type: ignore[arg-type]
                cycles=request.cycles,
            )
        except OperationalDrillError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        connection = connect_mysql(current_config().database)
        try:
            run_id = record_operational_drill(connection, report)
        finally:
            connection.close()
        return {"run_id": run_id, "operational_drill": report.to_record()}

    @app.get("/operations/drills")
    def operations_drill_history(limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
        connection = connect_mysql(current_config().database)
        try:
            records = list_operational_drills(connection, limit=limit)
        finally:
            connection.close()
        return {"operational_drill_runs": [record.to_record() for record in records]}

    @app.get("/operations/player-intelligence-audit")
    def player_intelligence_data_quality_audit() -> dict[str, Any]:
        connection = connect_mysql(current_config().database)
        try:
            audit = audit_player_intelligence(connection)
            return {"player_intelligence_audit": audit.to_record()}
        finally:
            connection.close()

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

    @app.get("/discord/bot/status")
    def discord_bot_status() -> dict[str, Any]:
        runtime_config = current_config()
        return {
            "bot": discord_bot_controller.status().to_record(),
            "configured": bool(runtime_config.secrets.discord_bot_token),
            "pubg_api_configured": bool(runtime_config.secrets.pubg_api_key),
            "settings": settings_store.load_discord_bot_settings().to_record(),
        }

    @app.get("/discord/bot/settings")
    def discord_bot_settings() -> dict[str, Any]:
        return {
            "discord_bot": settings_store.load_discord_bot_settings().to_record(),
            "command_catalog": command_catalog_records(),
        }

    @app.post("/discord/bot/settings")
    def save_discord_bot_settings(request: DiscordBotSettingsRequest) -> dict[str, Any]:
        try:
            settings = settings_store.save_discord_bot_settings(
                auto_start=request.auto_start,
                command_prefix=request.command_prefix,
                guild_enabled_commands=request.guild_enabled_commands,
            )
        except LocalSettingsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "discord_bot": settings.to_record(),
            "bot": discord_bot_controller.status().to_record(),
        }

    @app.post("/discord/bot/guild-commands")
    def save_discord_bot_guild_commands(
        request: DiscordGuildCommandsRequest,
    ) -> dict[str, Any]:
        current = settings_store.load_discord_bot_settings()
        if current.managed_guild_ids and request.guild_id not in current.managed_guild_ids:
            raise HTTPException(
                status_code=400,
                detail="현재 앱 관리 봇이 참여 중인 Discord 서버만 설정할 수 있습니다.",
            )
        try:
            settings = settings_store.save_discord_guild_commands(
                guild_id=request.guild_id,
                commands=None if request.use_default else request.commands,
            )
        except LocalSettingsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        state = discord_bot_controller.status()
        synchronized = False
        verification_error: str | None = None
        actual_commands: list[str] | None = None
        guild_commands: list[str] | None = None
        global_commands: list[str] | None = None
        if state.ready:
            try:
                state = discord_bot_controller.sync_commands(request.guild_id)
                synchronized = True
                exposure = discord_bot_controller.fetch_command_exposure(request.guild_id)
                actual_commands = exposure["visible_commands"]
                guild_commands = exposure["guild_commands"]
                global_commands = exposure["global_commands"]
            except DiscordBotControllerError as exc:
                verification_error = str(exc)

        expected_commands = (
            sorted(record["name"] for record in command_catalog_records())
            if request.use_default
            else sorted(settings.guild_enabled_commands.get(request.guild_id, []))
        )
        return {
            "discord_bot": settings.to_record(),
            "bot": state.to_record(),
            "guild_id": request.guild_id,
            "use_default": request.use_default,
            "expected_commands": expected_commands,
            "actual_commands": actual_commands,
            "guild_commands": guild_commands,
            "global_commands": global_commands,
            "synchronized": synchronized,
            "in_sync": (
                sorted(actual_commands) == expected_commands and not global_commands
                if actual_commands is not None
                else None
            ),
            "verification_error": verification_error,
        }

    @app.get("/discord/bot/guild-commands/{guild_id}")
    def discord_bot_guild_commands(guild_id: str) -> dict[str, Any]:
        if not guild_id.isdigit() or len(guild_id) > 32:
            raise HTTPException(status_code=400, detail="Discord 서버 ID 형식이 올바르지 않습니다.")
        settings = settings_store.load_discord_bot_settings()
        configured = settings.guild_enabled_commands.get(guild_id)
        expected_commands = (
            sorted(record["name"] for record in command_catalog_records())
            if configured is None
            else sorted(configured)
        )
        actual_commands: list[str] | None = None
        guild_commands: list[str] | None = None
        global_commands: list[str] | None = None
        verification_error: str | None = None
        if discord_bot_controller.status().ready:
            try:
                exposure = discord_bot_controller.fetch_command_exposure(guild_id)
                actual_commands = exposure["visible_commands"]
                guild_commands = exposure["guild_commands"]
                global_commands = exposure["global_commands"]
            except DiscordBotControllerError as exc:
                verification_error = str(exc)
        return {
            "guild_id": guild_id,
            "use_default": configured is None,
            "expected_commands": expected_commands,
            "actual_commands": actual_commands,
            "guild_commands": guild_commands,
            "global_commands": global_commands,
            "in_sync": (
                sorted(actual_commands) == expected_commands and not global_commands
                if actual_commands is not None
                else None
            ),
            "verification_error": verification_error,
        }

    @app.post("/discord/bot/start")
    def start_discord_bot() -> dict[str, Any]:
        try:
            state = discord_bot_controller.start()
        except (DiscordBotControllerError, LocalSettingsError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"bot": state.to_record()}

    @app.post("/discord/bot/stop")
    def stop_discord_bot() -> dict[str, Any]:
        try:
            state = discord_bot_controller.stop()
        except DiscordBotControllerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"bot": state.to_record()}

    @app.post("/discord/bot/sync")
    def sync_discord_bot_commands(request: DiscordCommandSyncRequest) -> dict[str, Any]:
        guild_id = str(request.guild_id or "").strip() or None
        if guild_id is not None and not guild_id.isdigit():
            raise HTTPException(status_code=400, detail="Discord 서버 ID 형식이 올바르지 않습니다.")
        try:
            state = discord_bot_controller.sync_commands(guild_id)
        except DiscordBotControllerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"bot": state.to_record()}

    @app.get("/discord/permissions")
    def discord_permissions() -> dict[str, Any]:
        try:
            return {
                "discord_permissions": permission_manager.load().to_record(),
                "command_catalog": command_catalog_records(),
                "reserved_groups": sorted(RESERVED_COMMAND_GROUPS),
                "group_labels": {
                    group: command_group_label(group)
                    for group in sorted(RESERVED_COMMAND_GROUPS)
                },
            }
        except LocalSettingsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/discord/guilds")
    def discord_guilds() -> dict[str, Any]:
        try:
            permissions = permission_manager.load()
            scopes = settings_store.load_discord_scope_settings()
            bot_settings = settings_store.load_discord_bot_settings()
        except LocalSettingsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        configured_ids = (
            set(permissions.guild_user_grants)
            | set(scopes.guild_ranking_scopes)
            | set(bot_settings.guild_enabled_commands)
        )
        connection = connect_mysql(current_config().database)
        try:
            guilds = list_discord_guild_catalog(
                connection,
                configured_guild_ids=configured_ids,
                ranking_scope_overrides=scopes.guild_ranking_scopes,
                managed_guild_ids=bot_settings.managed_guild_ids,
            )
        finally:
            connection.close()
        return {"guilds": [guild.to_record() for guild in guilds]}

    @app.post("/discord/guilds/sync")
    def sync_discord_guilds(request: DiscordGuildCatalogSyncRequest) -> dict[str, Any]:
        if (
            request.prune_stale
            and request.confirmation != MANAGED_DISCORD_GUILD_PRUNE_CONFIRMATION
        ):
            raise HTTPException(
                status_code=400,
                detail="현재 앱 관리 봇에 없는 서버를 정리하려면 확인 문구가 필요합니다.",
            )
        runtime_config = current_config()
        token = runtime_config.secrets.discord_bot_token
        if not token:
            raise HTTPException(status_code=400, detail="DISCORD_BOT_TOKEN is not configured.")
        try:
            discord = DiscordAcceptanceClient(token)
            identity = discord.current_bot()
            remote_guilds = discord.list_guilds()
        except DiscordAcceptanceError as exc:
            status_code = exc.status_code if exc.status_code and 400 <= exc.status_code < 500 else 502
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc

        reconciliation = settings_store.reconcile_managed_discord_bot(
            bot_user_id=identity.user_id,
            bot_username=identity.username,
            guild_ids=[guild.guild_id for guild in remote_guilds],
            prune_stale=request.prune_stale,
        )
        connection = connect_mysql(runtime_config.database)
        try:
            stored_guild_ids = (
                set(list_stored_discord_guild_ids(connection))
                if request.prune_stale
                else set()
            )
            synced_count = sync_discord_guild_catalog(
                connection,
                [guild.to_record() for guild in remote_guilds],
                prune_missing=request.prune_stale,
            )
        finally:
            connection.close()
        managed_guild_ids = {guild.guild_id for guild in remote_guilds}
        removed_catalog_guild_ids = sorted(stored_guild_ids - managed_guild_ids)
        removed_settings_guild_ids = (
            reconciliation.removed_guild_ids if request.prune_stale else []
        )
        return {
            "synced_count": synced_count,
            "managed_bot": {
                "user_id": identity.user_id,
                "username": identity.username,
            },
            "managed_guild_count": len(remote_guilds),
            "pruned": request.prune_stale,
            "removed_guild_ids": sorted(
                set(removed_catalog_guild_ids) | set(removed_settings_guild_ids)
            ),
            "removed_catalog_guild_ids": removed_catalog_guild_ids,
            "removed_settings_guild_ids": removed_settings_guild_ids,
        }

    @app.get("/discord/channels")
    def discord_channels(
        guild_id: str,
        limit: int = Query(default=50, ge=1, le=50),
    ) -> dict[str, Any]:
        runtime_config = current_config()
        token = runtime_config.secrets.discord_bot_token
        if not token:
            raise HTTPException(status_code=400, detail="DISCORD_BOT_TOKEN is not configured.")
        try:
            report = DiscordAcceptanceClient(token).probe(
                guild_id=guild_id,
                channel_limit=limit,
            )
        except DiscordAcceptanceError as exc:
            status_code = exc.status_code if exc.status_code and 400 <= exc.status_code < 500 else 502
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        guild = report.guilds[0]
        return {
            "guild": {
                "guild_id": guild.guild_id,
                "guild_name": guild.guild_name,
                "eligible_channel_count": guild.eligible_channel_count,
            },
            "channels": [channel.to_record() for channel in guild.channels],
        }

    @app.get("/discord/guild-members")
    def discord_guild_members(
        guild_id: str = Query(pattern=r"^\d{1,32}$"),
        search: str = Query(default="", max_length=100),
        limit: int = Query(default=100, ge=1, le=500),
        include_bots: bool = False,
    ) -> dict[str, Any]:
        runtime_config = current_config()
        token = runtime_config.secrets.discord_bot_token
        if not token:
            raise HTTPException(status_code=400, detail="Discord 봇 토큰이 설정되지 않았습니다.")
        try:
            members = DiscordAcceptanceClient(token).list_guild_members(
                guild_id=guild_id,
                search=search,
                limit=limit,
            )
        except DiscordAcceptanceError as exc:
            if exc.status_code == 403:
                detail = (
                    "Discord 서버 멤버를 조회할 권한이 없습니다. Discord Developer Portal에서 "
                    "Server Members Intent를 활성화했는지 확인해 주세요."
                )
            else:
                detail = str(exc)
            status_code = exc.status_code if exc.status_code and 400 <= exc.status_code < 500 else 502
            raise HTTPException(status_code=status_code, detail=detail) from exc
        visible = [member for member in members if include_bots or not member.is_bot]
        return {
            "guild_id": guild_id,
            "search": search,
            "members": [member.to_record() for member in visible],
        }

    @app.post("/discord/permissions/grant")
    def grant_discord_permission(request: DiscordPermissionGrantRequest) -> dict[str, Any]:
        try:
            return permission_manager.grant(
                user_id=request.user_id,
                group=request.group,
                guild_id=request.guild_id,
                member_label=request.member_label,
                member_guild_id=request.member_guild_id,
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

    @app.put("/discord/permissions/groups/{group}")
    def upsert_discord_permission_group(
        group: str,
        request: DiscordCommandGroupRequest,
    ) -> dict[str, Any]:
        try:
            return permission_manager.upsert_command_group(
                group=group,
                commands=request.commands,
            ).to_record()
        except LocalSettingsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/discord/permissions/groups/{group}")
    def delete_discord_permission_group(group: str) -> dict[str, Any]:
        try:
            return permission_manager.delete_command_group(group).to_record()
        except LocalSettingsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.put("/discord/permissions/aliases/{alias}")
    def set_discord_command_alias(
        alias: str,
        request: DiscordCommandAliasRequest,
    ) -> dict[str, Any]:
        try:
            return permission_manager.set_command_alias(
                alias=alias,
                target_command=request.target_command,
            ).to_record()
        except LocalSettingsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/discord/permissions/aliases/{alias}")
    def remove_discord_command_alias(alias: str) -> dict[str, Any]:
        try:
            return permission_manager.remove_command_alias(alias).to_record()
        except LocalSettingsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/discord/global-admins/add")
    def add_discord_global_admin(request: DiscordGlobalAdminRequest) -> dict[str, Any]:
        try:
            return permission_manager.add_global_admin(
                request.user_id,
                member_label=request.member_label,
                member_guild_id=request.member_guild_id,
            ).to_record()
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
                guild_ranking_selected_guild_ids=request.guild_ranking_selected_guild_ids,
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
            "preview_url": f"/data-deletions/{request_id}/preview",
            "confirmation_state_url": f"/data-deletions/{request_id}/confirmation-state",
            "preview_snapshot_url": f"/data-deletions/{request_id}/preview-snapshots",
            "confirmation_url": f"/data-deletions/{request_id}/confirmations",
            "dry_run_state_url": f"/data-deletions/{request_id}/dry-run-state",
            "dry_run_plan_url": f"/data-deletions/{request_id}/dry-run-plans",
            "backup_readiness_state_url": f"/data-deletions/{request_id}/backup-readiness-state",
            "backup_build_url": f"/data-deletions/{request_id}/backup-builds",
            "backup_verification_url": f"/data-deletions/{request_id}/backup-verifications",
            "backup_restore_rehearsal_url": f"/data-deletions/{request_id}/backup-restore-rehearsals",
            "quarantine_planning_url": f"/data-deletions/{request_id}/quarantine-plans",
            "quarantine_rehearsal_url": f"/data-deletions/{request_id}/quarantine-rehearsals",
            "combined_rehearsal_url": f"/data-deletions/{request_id}/combined-rehearsals",
            "fault_matrix_url": f"/data-deletions/{request_id}/fault-matrix-runs",
            "review_packet_url": f"/data-deletions/{request_id}/review-packets",
            "backup_evidence_url": f"/data-deletions/{request_id}/backup-evidence",
            "rehearsal_url": f"/data-deletions/{request_id}/rehearsals",
            "execution_enabled": False,
        }

    @app.get("/data-deletions/{request_id}/preview")
    def data_deletion_request_preview(
        request_id: int,
        file_limit: int = Query(
            default=DEFAULT_PREVIEW_FILE_LIMIT,
            ge=1,
            le=MAX_PREVIEW_FILE_LIMIT,
        ),
    ) -> dict[str, Any]:
        runtime_config = current_config()
        connection = connect_mysql(runtime_config.database)
        try:
            try:
                request = DataDeletionRequestService(connection).get_request(request_id)
            except DataDeletionRequestError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            try:
                preview = DataDeletionImpactPreviewService(
                    connection,
                    raw_data_dir=runtime_config.app.raw_data_dir,
                    replay_data_dir=runtime_config.app.replay_data_dir,
                ).build_preview(request, file_limit=file_limit)
            except DataDeletionPreviewError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            connection.close()
        return {
            "preview": preview.to_record(),
            "execution_enabled": False,
        }

    @app.get("/data-deletions/{request_id}/confirmation-state")
    def data_deletion_confirmation_state(request_id: int) -> dict[str, Any]:
        runtime_config = current_config()
        connection = connect_mysql(runtime_config.database)
        try:
            try:
                request = DataDeletionRequestService(connection).get_request(request_id)
                state = build_data_deletion_confirmation_service(
                    connection,
                    runtime_config,
                ).confirmation_state(request)
            except DataDeletionRequestError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except DataDeletionConfirmationError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            connection.close()
        return {"confirmation_state": state, "execution_enabled": False}

    @app.post("/data-deletions/{request_id}/preview-snapshots")
    def capture_data_deletion_preview_snapshot(
        request_id: int,
        capture: DataDeletionSnapshotCaptureRequest,
    ) -> dict[str, Any]:
        runtime_config = current_config()
        connection = connect_mysql(runtime_config.database)
        try:
            try:
                request = DataDeletionRequestService(connection).get_request(request_id)
                snapshot = build_data_deletion_confirmation_service(
                    connection,
                    runtime_config,
                ).capture_snapshot(
                    request,
                    actor_id=capture.actor_id,
                    note=capture.note,
                )
            except DataDeletionRequestError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except DataDeletionConfirmationError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        finally:
            connection.close()
        return {
            "snapshot": snapshot.to_summary_record(),
            "execution_enabled": False,
        }

    @app.post("/data-deletions/{request_id}/confirmations")
    def confirm_data_deletion_preview_snapshot(
        request_id: int,
        confirmation_request: DataDeletionConfirmationCreateRequest,
    ) -> dict[str, Any]:
        runtime_config = current_config()
        connection = connect_mysql(runtime_config.database)
        try:
            try:
                request = DataDeletionRequestService(connection).get_request(request_id)
                confirmation = build_data_deletion_confirmation_service(
                    connection,
                    runtime_config,
                ).confirm_snapshot(
                    request,
                    snapshot_id=confirmation_request.snapshot_id,
                    fingerprint_sha256=confirmation_request.fingerprint_sha256,
                    confirmation_text=confirmation_request.confirmation_text,
                    actor_id=confirmation_request.actor_id,
                    note=confirmation_request.note,
                )
            except DataDeletionRequestError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except DataDeletionConfirmationError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        finally:
            connection.close()
        return {
            "confirmation": confirmation.to_record(),
            "execution_enabled": False,
        }

    @app.get("/data-deletions/{request_id}/dry-run-state")
    def data_deletion_dry_run_state(request_id: int) -> dict[str, Any]:
        runtime_config = current_config()
        connection = connect_mysql(runtime_config.database)
        try:
            try:
                request = DataDeletionRequestService(connection).get_request(request_id)
                state = build_data_deletion_dry_run_service(
                    connection,
                    runtime_config,
                ).plan_state(request)
            except DataDeletionRequestError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except DataDeletionDryRunError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            connection.close()
        return {
            "dry_run_state": state,
            "execution_enabled": False,
            "execution_ready": False,
        }

    @app.post("/data-deletions/{request_id}/dry-run-plans")
    def create_data_deletion_dry_run_plan(
        request_id: int,
        plan_request: DataDeletionDryRunCreateRequest,
    ) -> dict[str, Any]:
        runtime_config = current_config()
        connection = connect_mysql(runtime_config.database)
        try:
            try:
                request = DataDeletionRequestService(connection).get_request(request_id)
                plan = build_data_deletion_dry_run_service(
                    connection,
                    runtime_config,
                ).create_plan(
                    request,
                    actor_id=plan_request.actor_id,
                    note=plan_request.note,
                )
            except DataDeletionRequestError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except DataDeletionDryRunError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        finally:
            connection.close()
        return {
            "dry_run_plan": plan.to_record(),
            "execution_enabled": False,
            "execution_ready": False,
        }

    @app.get("/data-deletions/{request_id}/backup-readiness-state")
    def data_deletion_backup_readiness_state(request_id: int) -> dict[str, Any]:
        runtime_config = current_config()
        connection = connect_mysql(runtime_config.database)
        try:
            try:
                request = DataDeletionRequestService(connection).get_request(request_id)
                backup_service = build_data_deletion_backup_service(
                    connection,
                    runtime_config,
                )
                state = backup_service.readiness_state(request)
                builder_state = build_data_deletion_backup_builder_service(
                    connection,
                    runtime_config,
                    backup_service=backup_service,
                ).build_state(request)
                verifier_service = build_data_deletion_backup_verifier_service(
                    connection,
                    runtime_config,
                    backup_service=backup_service,
                )
                verifier_state = verifier_service.verification_state(request)
                restore_rehearsal_state = build_data_deletion_restore_rehearsal_service(
                    connection,
                    runtime_config,
                    backup_service=backup_service,
                    verifier_service=verifier_service,
                ).rehearsal_state(request)
                quarantine_planner_service = build_data_deletion_quarantine_planner_service(
                    connection,
                    runtime_config,
                    backup_service=backup_service,
                )
                quarantine_planner_state = quarantine_planner_service.planning_state(
                    request
                )
                quarantine_rehearsal_service = (
                    build_data_deletion_quarantine_rehearsal_service(
                        connection,
                        runtime_config,
                        backup_service=backup_service,
                        planner_service=quarantine_planner_service,
                    )
                )
                quarantine_rehearsal_state = (
                    quarantine_rehearsal_service.rehearsal_state(request)
                )
                combined_rehearsal_service = (
                    build_data_deletion_combined_rehearsal_service(
                        connection,
                        runtime_config,
                        backup_service=backup_service,
                        verifier_service=verifier_service,
                        planner_service=quarantine_planner_service,
                        quarantine_rehearsal_service=(
                            quarantine_rehearsal_service
                        ),
                    )
                )
                combined_rehearsal_state = (
                    combined_rehearsal_service.rehearsal_state(request)
                )
                fault_matrix_service = build_data_deletion_fault_matrix_service(
                    connection,
                    runtime_config,
                    backup_service=backup_service,
                    verifier_service=verifier_service,
                    planner_service=quarantine_planner_service,
                    quarantine_rehearsal_service=quarantine_rehearsal_service,
                    combined_rehearsal_service=combined_rehearsal_service,
                )
                fault_matrix_state = fault_matrix_service.matrix_state(request)
                review_packet_state = build_data_deletion_review_packet_service(
                    connection,
                    runtime_config,
                    backup_service=backup_service,
                    verifier_service=verifier_service,
                    planner_service=quarantine_planner_service,
                    quarantine_rehearsal_service=quarantine_rehearsal_service,
                    combined_rehearsal_service=combined_rehearsal_service,
                    fault_matrix_service=fault_matrix_service,
                ).packet_state(request)
            except DataDeletionRequestError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except (
                DataDeletionBackupBuilderError,
                DataDeletionBackupVerifierError,
                DataDeletionRestoreRehearsalError,
                DataDeletionQuarantinePlannerError,
                DataDeletionQuarantineRehearsalError,
                DataDeletionCombinedRehearsalError,
                DataDeletionFaultMatrixError,
                DataDeletionReviewPacketError,
                DataDeletionBackupError,
                DataDeletionDryRunError,
            ) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            connection.close()
        return {
            "backup_readiness_state": state,
            "backup_builder_state": builder_state,
            "backup_verifier_state": verifier_state,
            "backup_restore_rehearsal_state": restore_rehearsal_state,
            "quarantine_planner_state": quarantine_planner_state,
            "quarantine_rehearsal_state": quarantine_rehearsal_state,
            "combined_rehearsal_state": combined_rehearsal_state,
            "fault_matrix_state": fault_matrix_state,
            "review_packet_state": review_packet_state,
            "execution_enabled": False,
            "execution_ready": False,
        }

    @app.post("/data-deletions/{request_id}/backup-builds")
    def build_data_deletion_backup_artifacts(
        request_id: int,
        build_request: DataDeletionBackupBuildCreateRequest,
    ) -> dict[str, Any]:
        runtime_config = current_config()
        connection = connect_mysql(runtime_config.database)
        try:
            try:
                request = DataDeletionRequestService(connection).get_request(request_id)
                result = build_data_deletion_backup_builder_service(
                    connection,
                    runtime_config,
                ).build(
                    request,
                    dry_run_plan_id=build_request.dry_run_plan_id,
                    confirmation_text=build_request.confirmation_text,
                    actor_id=build_request.actor_id,
                    note=build_request.note,
                )
            except DataDeletionRequestError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except (
                DataDeletionBackupBuilderError,
                DataDeletionBackupError,
                DataDeletionDryRunError,
            ) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        finally:
            connection.close()
        return {
            "backup_build": result.to_record(),
            "execution_enabled": False,
            "execution_ready": False,
        }

    @app.post("/data-deletions/{request_id}/backup-verifications")
    def verify_data_deletion_backup_artifacts(
        request_id: int,
        verification_request: DataDeletionBackupVerificationCreateRequest,
    ) -> dict[str, Any]:
        runtime_config = current_config()
        connection = connect_mysql(runtime_config.database)
        try:
            try:
                request = DataDeletionRequestService(connection).get_request(request_id)
                verification = build_data_deletion_backup_verifier_service(
                    connection,
                    runtime_config,
                ).verify(
                    request,
                    dry_run_plan_id=verification_request.dry_run_plan_id,
                    manifest_path=verification_request.manifest_path,
                    expected_manifest_sha256=verification_request.expected_manifest_sha256,
                    actor_id=verification_request.actor_id,
                    note=verification_request.note,
                )
            except DataDeletionRequestError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except (
                DataDeletionBackupVerifierError,
                DataDeletionBackupError,
                DataDeletionDryRunError,
            ) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        finally:
            connection.close()
        return {
            "backup_verification": verification.to_record(),
            "execution_enabled": False,
            "execution_ready": False,
        }

    @app.post("/data-deletions/{request_id}/backup-restore-rehearsals")
    def run_data_deletion_backup_restore_rehearsal(
        request_id: int,
        rehearsal_request: DataDeletionBackupRestoreRehearsalCreateRequest,
    ) -> dict[str, Any]:
        runtime_config = current_config()
        connection = connect_mysql(runtime_config.database)
        try:
            try:
                request = DataDeletionRequestService(connection).get_request(request_id)
                rehearsal = build_data_deletion_restore_rehearsal_service(
                    connection,
                    runtime_config,
                ).run(
                    request,
                    backup_verification_run_id=(
                        rehearsal_request.backup_verification_run_id
                    ),
                    confirmation_text=rehearsal_request.confirmation_text,
                    actor_id=rehearsal_request.actor_id,
                    note=rehearsal_request.note,
                )
            except DataDeletionRequestError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except (
                DataDeletionRestoreRehearsalError,
                DataDeletionBackupVerifierError,
                DataDeletionBackupError,
                DataDeletionDryRunError,
            ) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        finally:
            connection.close()
        return {
            "backup_restore_rehearsal": rehearsal.to_record(),
            "execution_enabled": False,
            "execution_ready": False,
        }

    @app.post("/data-deletions/{request_id}/quarantine-plans")
    def plan_data_deletion_quarantine(
        request_id: int,
        planning_request: DataDeletionQuarantinePlanningCreateRequest,
    ) -> dict[str, Any]:
        runtime_config = current_config()
        connection = connect_mysql(runtime_config.database)
        try:
            try:
                request = DataDeletionRequestService(connection).get_request(request_id)
                planning = build_data_deletion_quarantine_planner_service(
                    connection,
                    runtime_config,
                ).run(
                    request,
                    dry_run_plan_id=planning_request.dry_run_plan_id,
                    confirmation_text=planning_request.confirmation_text,
                    actor_id=planning_request.actor_id,
                    note=planning_request.note,
                )
            except DataDeletionRequestError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except (
                DataDeletionQuarantinePlannerError,
                DataDeletionBackupError,
                DataDeletionDryRunError,
            ) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        finally:
            connection.close()
        return {
            "quarantine_planning": planning.to_record(),
            "execution_enabled": False,
            "execution_ready": False,
        }

    @app.post("/data-deletions/{request_id}/quarantine-rehearsals")
    def run_data_deletion_quarantine_rehearsal(
        request_id: int,
        rehearsal_request: DataDeletionQuarantineRehearsalCreateRequest,
    ) -> dict[str, Any]:
        runtime_config = current_config()
        connection = connect_mysql(runtime_config.database)
        try:
            try:
                request = DataDeletionRequestService(connection).get_request(request_id)
                rehearsal = build_data_deletion_quarantine_rehearsal_service(
                    connection,
                    runtime_config,
                ).run(
                    request,
                    quarantine_planning_run_id=(
                        rehearsal_request.quarantine_planning_run_id
                    ),
                    confirmation_text=rehearsal_request.confirmation_text,
                    actor_id=rehearsal_request.actor_id,
                    note=rehearsal_request.note,
                )
            except DataDeletionRequestError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except (
                DataDeletionQuarantineRehearsalError,
                DataDeletionQuarantinePlannerError,
                DataDeletionBackupError,
                DataDeletionDryRunError,
            ) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        finally:
            connection.close()
        return {
            "quarantine_rehearsal": rehearsal.to_record(),
            "execution_enabled": False,
            "execution_ready": False,
        }

    @app.post("/data-deletions/{request_id}/combined-rehearsals")
    def run_data_deletion_combined_rehearsal(
        request_id: int,
        rehearsal_request: DataDeletionCombinedRehearsalCreateRequest,
    ) -> dict[str, Any]:
        runtime_config = current_config()
        connection = connect_mysql(runtime_config.database)
        try:
            try:
                request = DataDeletionRequestService(connection).get_request(request_id)
                rehearsal = build_data_deletion_combined_rehearsal_service(
                    connection,
                    runtime_config,
                ).run(
                    request,
                    backup_verification_run_id=(
                        rehearsal_request.backup_verification_run_id
                    ),
                    quarantine_planning_run_id=(
                        rehearsal_request.quarantine_planning_run_id
                    ),
                    confirmation_text=rehearsal_request.confirmation_text,
                    actor_id=rehearsal_request.actor_id,
                    note=rehearsal_request.note,
                )
            except DataDeletionRequestError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except (
                DataDeletionCombinedRehearsalError,
                DataDeletionQuarantineRehearsalError,
                DataDeletionQuarantinePlannerError,
                DataDeletionBackupVerifierError,
                DataDeletionBackupError,
                DataDeletionDryRunError,
            ) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        finally:
            connection.close()
        return {
            "combined_rehearsal": rehearsal.to_record(),
            "execution_enabled": False,
            "execution_ready": False,
        }

    @app.post("/data-deletions/{request_id}/fault-matrix-runs")
    def run_data_deletion_fault_matrix(
        request_id: int,
        matrix_request: DataDeletionFaultMatrixCreateRequest,
    ) -> dict[str, Any]:
        runtime_config = current_config()
        connection = connect_mysql(runtime_config.database)
        try:
            try:
                request = DataDeletionRequestService(connection).get_request(request_id)
                matrix_run = build_data_deletion_fault_matrix_service(
                    connection,
                    runtime_config,
                ).run(
                    request,
                    combined_rehearsal_run_id=(
                        matrix_request.combined_rehearsal_run_id
                    ),
                    confirmation_text=matrix_request.confirmation_text,
                    actor_id=matrix_request.actor_id,
                    note=matrix_request.note,
                )
            except DataDeletionRequestError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except (
                DataDeletionFaultMatrixError,
                DataDeletionCombinedRehearsalError,
                DataDeletionQuarantineRehearsalError,
                DataDeletionQuarantinePlannerError,
                DataDeletionBackupVerifierError,
                DataDeletionBackupError,
                DataDeletionDryRunError,
            ) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        finally:
            connection.close()
        return {
            "fault_matrix_run": matrix_run.to_record(),
            "execution_enabled": False,
            "execution_ready": False,
        }

    @app.post("/data-deletion-review-packets/verify")
    def verify_exported_data_deletion_review_packet(
        verify_request: ExportedReviewPacketVerifyRequest,
        response: Response,
    ) -> dict[str, Any]:
        verification_headers = {
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        }
        response.headers.update(verification_headers)
        connection = None
        try:
            if verify_request.cross_check_database:
                connection = connect_mysql(current_config().database)
            verification = ExportedReviewPacketVerifier(connection).verify_text(
                verify_request.packet_text,
                cross_check_database=verify_request.cross_check_database,
            )
        except ExportedReviewPacketVerifierError as exc:
            raise HTTPException(
                status_code=409,
                detail=str(exc),
                headers=verification_headers,
            ) from exc
        finally:
            if connection is not None:
                connection.close()
        return {
            "verification": verification.to_record(),
            "uploaded_text_persisted": False,
            "records_created": False,
            "database_writes_performed": False,
            "authorization_granted": False,
            "readiness_promoted": False,
            "execution_enabled": False,
            "execution_ready": False,
        }

    @app.post("/data-deletion-review-packets/compare")
    def compare_exported_data_deletion_review_packets(
        compare_request: ExportedReviewPacketCompareRequest,
        response: Response,
    ) -> dict[str, Any]:
        comparison_headers = {
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        }
        response.headers.update(comparison_headers)
        connection = None
        try:
            if compare_request.cross_check_database:
                connection = connect_mysql(current_config().database)
            comparison = ExportedReviewPacketComparer(connection).compare_texts(
                compare_request.baseline_packet_text,
                compare_request.candidate_packet_text,
                cross_check_database=compare_request.cross_check_database,
            )
        except ExportedReviewPacketComparerError as exc:
            raise HTTPException(
                status_code=409,
                detail=str(exc),
                headers=comparison_headers,
            ) from exc
        finally:
            if connection is not None:
                connection.close()
        return {
            "comparison": comparison.to_record(),
            "uploaded_text_persisted": False,
            "comparison_persisted": False,
            "records_created": False,
            "database_writes_performed": False,
            "authorization_granted": False,
            "readiness_promoted": False,
            "execution_enabled": False,
            "execution_ready": False,
        }

    @app.post("/data-deletions/{request_id}/review-packets")
    def generate_data_deletion_review_packet(
        request_id: int,
        packet_request: DataDeletionReviewPacketCreateRequest,
    ) -> dict[str, Any]:
        runtime_config = current_config()
        connection = connect_mysql(runtime_config.database)
        try:
            try:
                request = DataDeletionRequestService(connection).get_request(request_id)
                packet = build_data_deletion_review_packet_service(
                    connection,
                    runtime_config,
                ).generate(
                    request,
                    fault_matrix_run_id=packet_request.fault_matrix_run_id,
                    confirmation_text=packet_request.confirmation_text,
                    actor_id=packet_request.actor_id,
                    note=packet_request.note,
                )
            except DataDeletionRequestError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except (
                DataDeletionReviewPacketError,
                DataDeletionFaultMatrixError,
                DataDeletionCombinedRehearsalError,
                DataDeletionQuarantineRehearsalError,
                DataDeletionQuarantinePlannerError,
                DataDeletionBackupVerifierError,
                DataDeletionBackupError,
                DataDeletionDryRunError,
            ) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        finally:
            connection.close()
        return {
            "review_packet": packet.to_record(),
            "export_url": (
                f"/data-deletions/{request_id}/review-packets/{packet.id}/export.json"
            ),
            "authorization_granted": False,
            "readiness_promoted": False,
            "execution_enabled": False,
            "execution_ready": False,
        }

    @app.get("/data-deletions/{request_id}/review-packets/{packet_id}")
    def data_deletion_review_packet_detail(
        request_id: int,
        packet_id: int,
    ) -> dict[str, Any]:
        runtime_config = current_config()
        connection = connect_mysql(runtime_config.database)
        try:
            try:
                DataDeletionRequestService(connection).get_request(request_id)
                packet = build_data_deletion_review_packet_service(
                    connection,
                    runtime_config,
                ).get_packet(packet_id)
                if packet.request_id != request_id:
                    raise DataDeletionReviewPacketError(
                        "review packet does not belong to the requested deletion request."
                    )
            except DataDeletionRequestError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except DataDeletionReviewPacketError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            connection.close()
        return {
            "review_packet": packet.to_record(),
            "export_url": (
                f"/data-deletions/{request_id}/review-packets/{packet.id}/export.json"
            ),
            "authorization_granted": False,
            "readiness_promoted": False,
            "execution_enabled": False,
            "execution_ready": False,
        }

    @app.get("/data-deletions/{request_id}/review-packets/{packet_id}/export.json")
    def export_data_deletion_review_packet(
        request_id: int,
        packet_id: int,
    ) -> Response:
        runtime_config = current_config()
        connection = connect_mysql(runtime_config.database)
        try:
            try:
                DataDeletionRequestService(connection).get_request(request_id)
                packet = build_data_deletion_review_packet_service(
                    connection,
                    runtime_config,
                ).get_packet(packet_id)
                if packet.request_id != request_id:
                    raise DataDeletionReviewPacketError(
                        "review packet does not belong to the requested deletion request."
                    )
                content = canonical_review_packet_bytes(packet)
            except DataDeletionRequestError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except DataDeletionReviewPacketError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            connection.close()
        return Response(
            content=content,
            media_type="application/json",
            headers={
                "Content-Disposition": (
                    'attachment; filename="pubg-ai-deletion-review-request-'
                    f'{request_id}-packet-{packet_id}.json"'
                ),
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/data-deletions/{request_id}/backup-evidence")
    def record_data_deletion_backup_evidence(
        request_id: int,
        evidence_request: DataDeletionBackupEvidenceCreateRequest,
    ) -> dict[str, Any]:
        runtime_config = current_config()
        connection = connect_mysql(runtime_config.database)
        try:
            try:
                request = DataDeletionRequestService(connection).get_request(request_id)
                evidence = build_data_deletion_backup_service(
                    connection,
                    runtime_config,
                ).record_evidence(
                    request,
                    dry_run_plan_id=evidence_request.dry_run_plan_id,
                    prerequisite_key=evidence_request.prerequisite_key,
                    evidence={
                        "artifact_path": evidence_request.artifact_path,
                        "artifact_sha256": evidence_request.artifact_sha256,
                        "artifact_size_bytes": evidence_request.artifact_size_bytes,
                        "covered_row_count": evidence_request.covered_row_count,
                        "covered_file_count": evidence_request.covered_file_count,
                        "covered_file_bytes": evidence_request.covered_file_bytes,
                        "checked_path": evidence_request.checked_path,
                        "available_bytes": evidence_request.available_bytes,
                        "backup_created_at_kst": evidence_request.backup_created_at_kst,
                        "verified_at_kst": evidence_request.verified_at_kst,
                        "restore_tested_at_kst": evidence_request.restore_tested_at_kst,
                        "checksums_verified": evidence_request.checksums_verified,
                        "restore_test_passed": evidence_request.restore_test_passed,
                    },
                    actor_id=evidence_request.actor_id,
                    note=evidence_request.note,
                )
            except DataDeletionRequestError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except (DataDeletionBackupError, DataDeletionDryRunError) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        finally:
            connection.close()
        return {
            "backup_evidence": evidence.to_record(),
            "execution_enabled": False,
            "execution_ready": False,
        }

    @app.post("/data-deletions/{request_id}/rehearsals")
    def run_data_deletion_rehearsal(
        request_id: int,
        rehearsal_request: DataDeletionRehearsalCreateRequest,
    ) -> dict[str, Any]:
        runtime_config = current_config()
        connection = connect_mysql(runtime_config.database)
        try:
            try:
                request = DataDeletionRequestService(connection).get_request(request_id)
                rehearsal = build_data_deletion_backup_service(
                    connection,
                    runtime_config,
                ).run_rehearsal(
                    request,
                    dry_run_plan_id=rehearsal_request.dry_run_plan_id,
                    actor_id=rehearsal_request.actor_id,
                    note=rehearsal_request.note,
                )
            except DataDeletionRequestError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except (DataDeletionBackupError, DataDeletionDryRunError) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        finally:
            connection.close()
        return {
            "rehearsal": rehearsal.to_record(),
            "execution_enabled": False,
            "execution_ready": False,
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
                cursor.execute(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM registered_players) AS registered_players,
                        (SELECT COUNT(*) FROM registered_players WHERE active = 1) AS active_players,
                        (SELECT COUNT(*) FROM matches) AS matches,
                        (SELECT COUNT(*) FROM raw_match_payloads) AS raw_matches,
                        (SELECT COUNT(*) FROM raw_telemetry_payloads) AS raw_telemetry,
                        (SELECT COUNT(*) FROM replay_artifacts WHERE artifact_type = 'map_snapshot') AS map_snapshots,
                        (SELECT COUNT(*) FROM replay_artifacts WHERE artifact_type = 'timeline') AS timelines,
                        (
                            SELECT COUNT(*) FROM api_fetch_jobs
                            WHERE job_type = 'match' AND status IN ('queued', 'running')
                        ) AS pending_match_jobs,
                        (
                            SELECT COUNT(*) FROM api_fetch_jobs
                            WHERE job_type = 'telemetry' AND status IN ('queued', 'running')
                        ) AS pending_telemetry_jobs,
                        (SELECT COUNT(*) FROM api_fetch_jobs WHERE status = 'failed') AS failed_jobs
                    """
                )
                operation_row = cursor.fetchone()
            return {
                "mysql_connection": "ok",
                "database": row["database_name"],
                "version": row["version"],
                "table_count": count_tables(connection),
                "operations": {
                    key: int(value or 0)
                    for key, value in operation_row.items()
                },
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

    @app.get("/analytics/metrics")
    def analytics_metrics(category: str | None = None) -> dict[str, Any]:
        return {"metrics": metric_catalog_records(category=category)}

    @app.get("/analytics/telemetry-events")
    def analytics_telemetry_events() -> dict[str, Any]:
        return {"events": telemetry_event_catalog_records()}

    @app.get("/analytics/flight-paths")
    def analytics_flight_paths(
        shard: str | None = None,
        account_id: str | None = None,
        game_mode: str | None = None,
        team_mode: str | None = None,
        perspective: str | None = None,
        match_type: str | None = None,
        map_name: str | None = None,
        season_state: str | None = None,
        is_custom_match: str | None = None,
        year: int | None = Query(default=None, ge=2000, le=2100),
        quarter: int | None = Query(default=None, ge=1, le=4),
        month: int | None = Query(default=None, ge=1, le=12),
        exact_date_kst: str | None = None,
        hour: int | None = Query(default=None, ge=0, le=23),
        from_date_kst: str | None = None,
        to_date_kst: str | None = None,
        angle_bin_degrees: float = Query(default=10.0, ge=1.0, le=45.0),
        offset_bin_m: float = Query(default=500.0, ge=50.0, le=4000.0),
        top_per_map: int = Query(default=20, ge=1, le=50),
        recent_limit: int = Query(default=50, ge=1, le=200),
        route_limit: int = Query(default=50000, ge=100, le=100000),
    ) -> dict[str, Any]:
        try:
            filters = PlayerTrendFilters(
                game_mode=game_mode,
                team_mode=team_mode,
                perspective=perspective,
                match_type=match_type,
                map_name=map_name,
                season_state=season_state,
                is_custom_match=parse_optional_bool(is_custom_match, "is_custom_match"),
                year=year,
                quarter=quarter,
                month=month,
                exact_date_kst=parse_trend_date(exact_date_kst, "exact_date_kst"),
                hour=hour,
                from_date_kst=parse_trend_date(from_date_kst, "from_date_kst"),
                to_date_kst=parse_trend_date(to_date_kst, "to_date_kst"),
            ).normalized()
            connection = connect_mysql(config.database)
            try:
                report = FlightPathStatsService(connection).get_report(
                    shard=shard,
                    account_id=account_id,
                    filters=filters,
                    angle_bin_degrees=angle_bin_degrees,
                    offset_bin_m=offset_bin_m,
                    top_per_map=top_per_map,
                    recent_limit=recent_limit,
                    route_limit=route_limit,
                )
            finally:
                connection.close()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"flight_paths": report.to_record()}

    @app.get("/players/intelligence")
    def player_intelligence(
        shard: str = "steam",
        name: str | None = None,
        account_id: str | None = None,
        game_mode: str | None = None,
        team_mode: str | None = None,
        perspective: str | None = None,
        match_type: str | None = None,
        map_name: str | None = None,
        season_state: str | None = None,
        is_custom_match: str | None = None,
        year: int | None = Query(default=None, ge=2000, le=2100),
        quarter: int | None = Query(default=None, ge=1, le=4),
        month: int | None = Query(default=None, ge=1, le=12),
        exact_date_kst: str | None = None,
        hour: int | None = Query(default=None, ge=0, le=23),
        from_date_kst: str | None = None,
        to_date_kst: str | None = None,
        trend_limit: int = Query(default=365, ge=1, le=1000),
    ) -> dict[str, Any]:
        if not name and not account_id:
            raise HTTPException(status_code=400, detail="name or account_id is required.")
        try:
            filters = PlayerTrendFilters(
                game_mode=game_mode,
                team_mode=team_mode,
                perspective=perspective,
                match_type=match_type,
                map_name=map_name,
                season_state=season_state,
                is_custom_match=parse_optional_bool(is_custom_match, "is_custom_match"),
                year=year,
                quarter=quarter,
                month=month,
                exact_date_kst=parse_trend_date(exact_date_kst, "exact_date_kst"),
                hour=hour,
                from_date_kst=parse_trend_date(from_date_kst, "from_date_kst"),
                to_date_kst=parse_trend_date(to_date_kst, "to_date_kst"),
            ).normalized()
            connection = connect_mysql(config.database)
            try:
                report = PlayerIntelligenceService(connection).get_report(
                    shard=shard,
                    account_id=account_id,
                    name=name,
                    global_scope=True,
                    filters=filters,
                    trend_limit=trend_limit,
                )
            finally:
                connection.close()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if report is None:
            raise HTTPException(status_code=404, detail="registered player intelligence not found.")
        return {"intelligence": report.to_record()}

    @app.get("/players/catalog")
    def player_catalog(
        shard: str = "steam",
        name: str | None = None,
        account_id: str | None = None,
        match_limit: int = Query(default=1000, ge=1, le=5000),
    ) -> dict[str, Any]:
        if not name and not account_id:
            raise HTTPException(status_code=400, detail="name or account_id is required.")

        connection = connect_mysql(config.database)
        try:
            catalog = PlayerStatsService(connection).get_lookup_catalog(
                shard=shard,
                account_id=account_id,
                name=name,
                global_scope=True,
                match_limit=match_limit,
            )
            if catalog is None:
                raise HTTPException(status_code=404, detail="registered player catalog not found.")
            return {"catalog": catalog.to_record()}
        finally:
            connection.close()

    @app.get("/players/weapon")
    def player_weapon(
        weapon: str,
        shard: str = "steam",
        name: str | None = None,
        account_id: str | None = None,
        game_mode: str | None = None,
        team_mode: str | None = None,
        perspective: str | None = None,
        match_type: str | None = None,
        map_name: str | None = None,
        season_state: str | None = None,
        is_custom_match: str | None = None,
        year: int | None = Query(default=None, ge=2000, le=2100),
        quarter: int | None = Query(default=None, ge=1, le=4),
        month: int | None = Query(default=None, ge=1, le=12),
        exact_date_kst: str | None = None,
        hour: int | None = Query(default=None, ge=0, le=23),
        from_date_kst: str | None = None,
        to_date_kst: str | None = None,
    ) -> dict[str, Any]:
        if not name and not account_id:
            raise HTTPException(status_code=400, detail="name or account_id is required.")

        try:
            filters = PlayerTrendFilters(
                game_mode=game_mode,
                team_mode=team_mode,
                perspective=perspective,
                match_type=match_type,
                map_name=map_name,
                season_state=season_state,
                is_custom_match=parse_optional_bool(is_custom_match, "is_custom_match"),
                year=year,
                quarter=quarter,
                month=month,
                exact_date_kst=parse_trend_date(exact_date_kst, "exact_date_kst"),
                hour=hour,
                from_date_kst=parse_trend_date(from_date_kst, "from_date_kst"),
                to_date_kst=parse_trend_date(to_date_kst, "to_date_kst"),
            ).normalized()
            connection = connect_mysql(config.database)
            try:
                detail = PlayerStatsService(connection).get_weapon_detail(
                    shard=shard,
                    account_id=account_id,
                    name=name,
                    weapon=weapon,
                    global_scope=True,
                    filters=filters,
                )
            finally:
                connection.close()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if detail is None:
            raise HTTPException(status_code=404, detail="registered player weapon stats not found.")
        return {"weapon": detail.to_record()}

    @app.get("/players/fight-outcomes")
    def player_fight_outcomes(
        shard: str = "steam",
        name: str | None = None,
        account_id: str | None = None,
        weapon_limit: int = 10,
        loadout_limit: int = 10,
        recent_limit: int = 20,
        include_friendly_fire: bool = False,
        include_bots: bool = True,
    ) -> dict[str, Any]:
        if not name and not account_id:
            raise HTTPException(status_code=400, detail="name or account_id is required.")

        connection = connect_mysql(config.database)
        try:
            report = FightOutcomeStatsService(connection).get_report(
                shard=shard,
                account_id=account_id,
                name=name,
                global_scope=True,
                weapon_limit=weapon_limit,
                loadout_limit=loadout_limit,
                recent_limit=recent_limit,
                include_friendly_fire=include_friendly_fire,
                include_bots=include_bots,
            )
            if report is None:
                raise HTTPException(status_code=404, detail="registered player fight outcomes not found.")
            return {"fight_outcomes": report.to_record()}
        finally:
            connection.close()

    @app.get("/players/trends")
    def player_trends(
        shard: str = "steam",
        name: str | None = None,
        account_id: str | None = None,
        granularity: str = "month",
        game_mode: str | None = None,
        team_mode: str | None = None,
        perspective: str | None = None,
        match_type: str | None = None,
        map_name: str | None = None,
        season_state: str | None = None,
        is_custom_match: str | None = None,
        year: int | None = Query(default=None, ge=2000, le=2100),
        quarter: int | None = Query(default=None, ge=1, le=4),
        month: int | None = Query(default=None, ge=1, le=12),
        exact_date_kst: str | None = None,
        hour: int | None = Query(default=None, ge=0, le=23),
        from_date_kst: str | None = None,
        to_date_kst: str | None = None,
        bucket_limit: int = Query(default=120, ge=1, le=500),
    ) -> dict[str, Any]:
        if not name and not account_id:
            raise HTTPException(status_code=400, detail="name or account_id is required.")
        try:
            filters = PlayerTrendFilters(
                game_mode=game_mode,
                team_mode=team_mode,
                perspective=perspective,
                match_type=match_type,
                map_name=map_name,
                season_state=season_state,
                is_custom_match=parse_optional_bool(is_custom_match, "is_custom_match"),
                year=year,
                quarter=quarter,
                month=month,
                exact_date_kst=parse_trend_date(exact_date_kst, "exact_date_kst"),
                hour=hour,
                from_date_kst=parse_trend_date(from_date_kst, "from_date_kst"),
                to_date_kst=parse_trend_date(to_date_kst, "to_date_kst"),
            ).normalized()
            connection = connect_mysql(config.database)
            try:
                report = PlayerTrendService(connection).get_report(
                    shard=shard,
                    account_id=account_id,
                    name=name,
                    global_scope=True,
                    granularity=granularity,
                    filters=filters,
                    bucket_limit=bucket_limit,
                )
            finally:
                connection.close()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if report is None:
            raise HTTPException(status_code=404, detail="registered player trends not found.")
        return {"trends": report.to_record()}

    @app.get("/map-regions")
    def map_regions(map_name: str | None = None) -> dict[str, Any]:
        return {"map_region_catalog": map_region_catalog_record(map_name)}

    @app.get("/map-regions/resolve")
    def map_region_resolution(map_name: str, x_cm: float, y_cm: float) -> dict[str, Any]:
        return {"map_region": resolve_map_region(map_name, x_cm, y_cm).to_record()}

    @app.get("/players/recommendations")
    def player_recommendations(
        shard: str = "steam",
        name: str | None = None,
        account_id: str | None = None,
        limit: int = 5,
        min_matches: int = Query(default=1, ge=1, le=2_147_483_647),
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

    @app.get("/players/drop-zones")
    def player_drop_zones(
        shard: str = "steam",
        name: str | None = None,
        account_id: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        min_matches: int = Query(default=1, ge=1, le=2_147_483_647),
    ) -> dict[str, Any]:
        if not name and not account_id:
            raise HTTPException(status_code=400, detail="name or account_id is required.")
        connection = connect_mysql(config.database)
        try:
            report = PlayerRecommendationService(connection).get_drop_zone_analysis(
                shard=shard,
                name=name,
                account_id=account_id,
                global_scope=True,
                limit=limit,
                min_matches=min_matches,
            )
            if report is None:
                raise HTTPException(status_code=404, detail="registered player drop-zone analysis not found.")
            return {"drop_zones": report.to_record()}
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

    @app.get("/watchlist/players")
    def watched_players(
        include_inactive: bool = True,
        search: str | None = None,
        limit: int = Query(default=500, ge=1, le=1000),
    ) -> dict[str, Any]:
        connection = connect_mysql(config.database)
        try:
            players = WatchlistService(connection).list_players(
                include_inactive=include_inactive,
                search=search,
                limit=limit,
            )
            return {"players": [player.to_record() for player in players]}
        finally:
            connection.close()

    @app.post("/watchlist/players")
    def register_watched_player(
        request: RegisterWatchedPlayerRequest,
    ) -> dict[str, Any]:
        runtime_config = current_config()
        if not runtime_config.secrets.pubg_api_key:
            raise HTTPException(status_code=500, detail="PUBG API 키가 설정되지 않았습니다.")
        connection = connect_mysql(config.database)
        try:
            try:
                player = WatchlistService(connection).register_by_name(
                    pubg_client=PubgApiClient(runtime_config.secrets.pubg_api_key),
                    shard=request.shard,
                    player_name=request.player_name,
                    notify_name_change=request.notify_name_change,
                    notify_kill=request.notify_kill,
                    notify_repeated_engagement=request.notify_repeated_engagement,
                    engagement_threshold=request.engagement_threshold,
                    notification_channel_ids=request.notification_channel_ids,
                )
            except PubgApiError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            except WatchlistError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {"player": player.to_record()}
        finally:
            connection.close()

    @app.put("/watchlist/players/{shard}/{account_id}")
    def update_watched_player(
        shard: str,
        account_id: str,
        request: UpdateWatchedPlayerRequest,
    ) -> dict[str, Any]:
        connection = connect_mysql(config.database)
        try:
            try:
                player = WatchlistService(connection).update(
                    account_id=account_id,
                    shard=shard,
                    active=request.active,
                    notify_name_change=request.notify_name_change,
                    notify_kill=request.notify_kill,
                    notify_repeated_engagement=request.notify_repeated_engagement,
                    engagement_threshold=request.engagement_threshold,
                    notification_channel_ids=request.notification_channel_ids,
                )
            except WatchlistError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            return {"player": player.to_record()}
        finally:
            connection.close()

    @app.post("/watchlist/scan")
    def scan_watchlist(request: ScanWatchlistRequest) -> dict[str, Any]:
        runtime_config = current_config()
        connection = connect_mysql(config.database)
        try:
            service = WatchlistService(connection)
            identity = {"checked": 0, "changed": 0, "alerts_created": 0}
            if request.refresh_identity:
                if not runtime_config.secrets.pubg_api_key:
                    raise HTTPException(
                        status_code=500,
                        detail="닉네임 재확인에 필요한 PUBG API 키가 설정되지 않았습니다.",
                    )
                try:
                    identity = service.refresh_identities(
                        pubg_client=PubgApiClient(runtime_config.secrets.pubg_api_key),
                        force=request.force,
                    )
                except (PubgApiError, WatchlistError) as exc:
                    raise HTTPException(status_code=502, detail=str(exc)) from exc
            encounters = service.scan_encounters(
                raw_store=RawPayloadStore(
                    runtime_config.app.raw_data_dir,
                    compression=runtime_config.app.raw_compression,  # type: ignore[arg-type]
                ),
                limit=request.limit,
                force=request.force,
            )
            return {
                "identity": identity,
                "encounters": encounters.to_record(),
            }
        finally:
            connection.close()

    @app.get("/watchlist/encounters")
    def watched_player_encounters(
        watched_player_id: int | None = None,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        connection = connect_mysql(config.database)
        try:
            return {
                "encounters": WatchlistService(connection).list_encounters(
                    watched_player_id=watched_player_id,
                    limit=limit,
                )
            }
        finally:
            connection.close()

    @app.get("/matches/explorer")
    def explore_matches(
        shard: str | None = None,
        account_id: str | None = None,
        search: str | None = None,
        map_name: str | None = None,
        game_mode: str | None = None,
        match_type: str | None = None,
        created_from_kst: date | None = None,
        created_to_kst: date | None = None,
        telemetry_only: bool = False,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        runtime_config = current_config()
        connection = connect_mysql(config.database)
        try:
            return MatchExplorerService(
                connection,
                RawPayloadStore(
                    runtime_config.app.raw_data_dir,
                    compression=runtime_config.app.raw_compression,  # type: ignore[arg-type]
                ),
            ).list_matches(
                shard=shard,
                account_id=account_id,
                search=search,
                map_name=map_name,
                game_mode=game_mode,
                match_type=match_type,
                created_from_kst=created_from_kst,
                created_to_kst=created_to_kst,
                telemetry_only=telemetry_only,
                limit=limit,
                offset=offset,
            )
        finally:
            connection.close()

    @app.get("/matches/{match_id}/detail")
    def explore_match_detail(match_id: str) -> dict[str, Any]:
        runtime_config = current_config()
        connection = connect_mysql(config.database)
        try:
            try:
                detail = MatchExplorerService(
                    connection,
                    RawPayloadStore(
                        runtime_config.app.raw_data_dir,
                        compression=runtime_config.app.raw_compression,  # type: ignore[arg-type]
                    ),
                ).get_match_detail(match_id)
            except MatchExplorerError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            if detail is None:
                raise HTTPException(status_code=404, detail="저장된 매치를 찾을 수 없습니다.")
            return {"detail": detail}
        finally:
            connection.close()

    @app.get("/matches/{match_id}/events")
    def explore_match_events(
        match_id: str,
        domain: str | None = None,
        event_type: str | None = None,
        account_id: str | None = None,
        team_id: int | None = None,
        search: str | None = None,
        include_positions: bool = False,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        runtime_config = current_config()
        connection = connect_mysql(config.database)
        try:
            try:
                return MatchExplorerService(
                    connection,
                    RawPayloadStore(
                        runtime_config.app.raw_data_dir,
                        compression=runtime_config.app.raw_compression,  # type: ignore[arg-type]
                    ),
                ).list_events(
                    match_id=match_id,
                    domain=domain,
                    event_type=event_type,
                    account_id=account_id,
                    team_id=team_id,
                    search=search,
                    include_positions=include_positions,
                    limit=limit,
                    offset=offset,
                )
            except MatchExplorerError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        finally:
            connection.close()

    @app.get("/matches/{match_id}/events/{sequence}")
    def explore_match_event(
        match_id: str,
        sequence: int,
    ) -> dict[str, Any]:
        if sequence < 0:
            raise HTTPException(status_code=400, detail="이벤트 순번은 0 이상이어야 합니다.")
        runtime_config = current_config()
        connection = connect_mysql(config.database)
        try:
            try:
                event = MatchExplorerService(
                    connection,
                    RawPayloadStore(
                        runtime_config.app.raw_data_dir,
                        compression=runtime_config.app.raw_compression,  # type: ignore[arg-type]
                    ),
                ).get_event(match_id=match_id, sequence=sequence)
            except MatchExplorerError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            if event is None:
                raise HTTPException(status_code=404, detail="매치 이벤트를 찾을 수 없습니다.")
            return {"event": event}
        finally:
            connection.close()

    @app.post("/matches/{match_id}/replay")
    def generate_match_replay(
        match_id: str,
        request: GenerateMatchReplayRequest,
    ) -> dict[str, Any]:
        runtime_config = current_config()
        connection = connect_mysql(config.database)
        try:
            try:
                result = MatchReplayProcessor(
                    connection,
                    RawPayloadStore(
                        runtime_config.app.raw_data_dir,
                        compression=runtime_config.app.raw_compression,  # type: ignore[arg-type]
                    ),
                    ReplayArtifactStore(runtime_config.app.replay_data_dir),
                ).generate(match_id=match_id, force=request.force)
            except MatchReplayError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            artifact = get_replay_artifact(connection, result.artifact_id)
            if artifact is None:
                raise HTTPException(
                    status_code=500,
                    detail="생성된 리플레이 메타데이터를 찾을 수 없습니다.",
                )
            return {
                "result": result.to_record(),
                "artifact": artifact.to_record(),
            }
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
            global_scope, scope_guild_ids = _ranking_scope(
                settings_store,
                guild_id,
            )
            ranking = PlayerRankingService(connection).get_player_ranking(
                shard=shard,
                metric=metric,
                guild_id=None if global_scope else guild_id,
                guild_ids=scope_guild_ids,
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
                user_id=request.discord_user_id if request.guild_id else None,
                guild_id=request.guild_id,
                channel_id=request.channel_id if request.guild_id else None,
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

    @app.post("/players/manage")
    def update_player_management(request: UpdatePlayerManagementRequest) -> dict[str, Any]:
        if request.active is None and request.public_profile is None:
            raise HTTPException(status_code=400, detail="active or public_profile is required.")
        connection = connect_mysql(config.database)
        try:
            player = PlayerRegistry(connection).set_player_management(
                shard=request.shard,
                account_id=request.account_id,
                active=request.active,
                public_profile=request.public_profile,
            )
            if player is None:
                raise HTTPException(status_code=404, detail="player not found.")
            return {"player": player.to_record()}
        finally:
            connection.close()

    @app.post("/players/discord-registrations")
    def add_player_discord_registration(
        request: PlayerDiscordRegistrationRequest,
    ) -> dict[str, Any]:
        connection = connect_mysql(config.database)
        try:
            registry = PlayerRegistry(connection)
            player = registry.get_player(
                shard=request.shard,
                account_id=request.account_id,
                include_inactive=True,
            )
            if player is None:
                raise HTTPException(status_code=404, detail="player not found.")
            try:
                registry.add_discord_registration(
                    registered_player_id=player.id,
                    guild_id=request.guild_id,
                    channel_id=request.channel_id,
                    registered_by_discord_user_id=request.discord_user_id,
                )
            except PlayerRegistryError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            refreshed = registry.get_player(
                shard=player.shard,
                account_id=player.account_id,
                include_inactive=True,
            )
            return {"player": refreshed.to_record() if refreshed else player.to_record()}
        finally:
            connection.close()

    @app.post("/players/discord-registrations/remove")
    def remove_player_discord_registration(
        request: PlayerDiscordRegistrationRequest,
    ) -> dict[str, Any]:
        connection = connect_mysql(config.database)
        try:
            registry = PlayerRegistry(connection)
            player = registry.get_player(
                shard=request.shard,
                account_id=request.account_id,
                include_inactive=True,
            )
            if player is None:
                raise HTTPException(status_code=404, detail="player not found.")
            if not registry.remove_discord_registration(
                registered_player_id=player.id,
                guild_id=request.guild_id,
            ):
                raise HTTPException(status_code=404, detail="Discord registration not found.")
            refreshed = registry.get_player(
                shard=player.shard,
                account_id=player.account_id,
                include_inactive=True,
            )
            return {"player": refreshed.to_record() if refreshed else player.to_record()}
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
            return {
                "jobs": [_json_ready(job) for job in jobs],
                "summary": _job_queue_summary(connection, "match"),
            }
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
            return {
                "jobs": [_json_ready(job) for job in jobs],
                "summary": _job_queue_summary(connection, "telemetry"),
            }
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

    @app.post("/telemetry/fight-outcomes/process")
    def process_fight_outcomes(request: ParseFightOutcomesRequest) -> dict[str, Any]:
        runtime_config = current_config()
        connection = connect_mysql(config.database)
        try:
            result = FightOutcomeProcessor(
                connection,
                RawPayloadStore(
                    runtime_config.app.raw_data_dir,
                    compression=runtime_config.app.raw_compression,  # type: ignore[arg-type]
                ),
            ).process_raw_telemetry(limit=request.limit, force=request.force)
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


def _ensure_configured_storage_directories(config: RuntimeConfig) -> None:
    for path in (
        config.app.raw_data_dir,
        config.app.replay_data_dir,
        config.app.backup_data_dir,
        config.app.quarantine_data_dir,
    ):
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OS오류:
            # The storage alert system reports the exact inaccessible path.
            continue


def _json_ready(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value


def _job_queue_summary(connection: Any, job_type: str) -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(status = 'queued'), 0) AS queued,
                COALESCE(SUM(status = 'running'), 0) AS running,
                COALESCE(SUM(status = 'succeeded'), 0) AS succeeded,
                COALESCE(SUM(status = 'failed'), 0) AS failed,
                COALESCE(SUM(
                    status = 'queued'
                    AND (next_run_at_kst IS NULL OR next_run_at_kst <= NOW(6))
                ), 0) AS eligible_queued,
                COALESCE(SUM(
                    status = 'queued'
                    AND next_run_at_kst > NOW(6)
                ), 0) AS scheduled_queued,
                COALESCE(SUM(
                    status = 'succeeded'
                    AND updated_at_kst >= DATE_SUB(NOW(6), INTERVAL 10 MINUTE)
                ), 0) AS recent_succeeded,
                MIN(CASE WHEN status = 'queued' THEN created_at_kst END)
                    AS oldest_queued_at_kst,
                MAX(CASE WHEN status = 'succeeded' THEN updated_at_kst END)
                    AS last_succeeded_at_kst,
                MAX(updated_at_kst) AS last_activity_at_kst
            FROM api_fetch_jobs
            WHERE job_type = %s
            """,
            (job_type,),
        )
        row = cursor.fetchone() or {}
    by_status = {
        "queued": int(row.get("queued") or 0),
        "running": int(row.get("running") or 0),
        "succeeded": int(row.get("succeeded") or 0),
        "failed": int(row.get("failed") or 0),
    }
    return {
        "total": int(row.get("total") or 0),
        "by_status": by_status,
        "eligible_queued": int(row.get("eligible_queued") or 0),
        "scheduled_queued": int(row.get("scheduled_queued") or 0),
        "recent_succeeded": int(row.get("recent_succeeded") or 0),
        "oldest_queued_at_kst": _json_ready(row.get("oldest_queued_at_kst")),
        "last_succeeded_at_kst": _json_ready(row.get("last_succeeded_at_kst")),
        "last_activity_at_kst": _json_ready(row.get("last_activity_at_kst")),
    }


def _settings_status_record(config: RuntimeConfig) -> dict[str, Any]:
    return {
        "raw_data_dir": str(config.app.raw_data_dir),
        "replay_data_dir": str(config.app.replay_data_dir),
        "backup_data_dir": str(config.app.backup_data_dir),
        "quarantine_data_dir": str(config.app.quarantine_data_dir),
        "local_web_base_url": config.app.local_web_base_url,
        "raw_compression": config.app.raw_compression,
        "storage_status": {
            "raw_data_dir": check_storage_path(config.app.raw_data_dir).to_record(),
            "replay_data_dir": check_storage_path(config.app.replay_data_dir).to_record(),
            "backup_data_dir": check_storage_path(config.app.backup_data_dir).to_record(),
            "quarantine_data_dir": check_storage_path(config.app.quarantine_data_dir).to_record(),
        },
        "collector": {
            "poll_interval_seconds": config.app.collector_poll_interval_seconds,
            "cycle_player_limit": config.app.collector_cycle_player_limit,
            "player_lookup_chunk_size": config.app.player_lookup_chunk_size,
        },
        "database": config.database.safe_record(),
        "secrets": {
            key: {"configured": status.configured}
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
            _alert_record_with_display(record.to_record())
            for record in visible_alert_records(active_records)
            if record.alert_key in current_alert_keys
        ],
        **history_record,
        "latest_worker_run_id": report.latest_worker_run_id,
    }


def _alert_history_page_record(page: Any) -> dict[str, Any]:
    record = page.to_record()
    records = [
        _alert_record_with_display(item)
        for item in record.pop("records")
    ]
    return {
        "alert_history": records,
        "alert_history_page": record,
    }


def _alert_record_with_display(record: dict[str, Any]) -> dict[str, Any]:
    result = dict(record)
    result["display_title"] = alert_display_title(result)
    result["display_message"] = alert_display_message(result)
    return result


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


def _ranking_scope(
    settings_store: LocalSettingsStore,
    guild_id: str | None,
) -> tuple[bool, list[str]]:
    if guild_id is None:
        return True, []
    try:
        settings = settings_store.load_discord_scope_settings()
    except LocalSettingsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if settings.guild_ranking_scopes.get(guild_id) == "global":
        return True, []
    selected = settings.guild_ranking_selected_guild_ids.get(guild_id)
    return False, list(selected or [guild_id])


def _public_profile_default(settings_store: LocalSettingsStore) -> bool:
    try:
        settings = settings_store.load_discord_scope_settings()
    except LocalSettingsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return settings.public_profile_default


def _local_settings_store(base_dir: Path, *, env_file: str = ".env") -> LocalSettingsStore:
    values = load_dotenv_values(base_dir / env_file)
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
  <link rel="icon" href="/favicon.ico" sizes="any">
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
      min-width: 0;
      overflow-x: auto;
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
    .trend-filter { grid-template-columns: repeat(5, minmax(0, 1fr)); }
    .trend-table { min-width: 840px; table-layout: auto; }
    label { display: grid; gap: 6px; color: var(--muted); font-size: 12px; }
    label.is-disabled { opacity: 0.45; }
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
    .section-heading-row {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 14px;
    }
    .section-heading-row h2 { margin-bottom: 5px; }
    .data-quality-checks { min-width: 720px; table-layout: auto; }
    .data-quality-checks th:nth-child(1) { width: 92px; }
    .data-quality-checks th:nth-child(3),
    .data-quality-checks th:nth-child(4) { width: 120px; }
    .recommendation-line {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      min-height: 32px;
    }
    .recommendation-line button { min-height: 30px; padding: 5px 9px; font-size: 12px; flex: 0 0 auto; }
    .query-form { grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }
    .analysis-form {
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      gap: 10px;
    }
    .query-primary,
    .filter-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      align-items: end;
    }
    .advanced-filters {
      min-width: 0;
      border-top: 1px solid var(--line);
      padding-top: 9px;
    }
    .advanced-filters summary {
      width: max-content;
      cursor: pointer;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }
    .advanced-filters[open] summary { margin-bottom: 10px; color: var(--text); }
    .result-shell {
      display: grid;
      gap: 14px;
      min-width: 0;
      color: var(--text);
    }
    .result-warning {
      border: 1px solid #756226;
      border-left: 3px solid var(--warning);
      border-radius: 4px;
      padding: 9px 10px;
      background: #211d12;
      color: #f0d479;
      font-size: 10px;
      overflow-wrap: anywhere;
    }
    .result-heading {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
    }
    .result-heading strong {
      display: block;
      color: var(--text);
      font-size: 15px;
      overflow-wrap: anywhere;
    }
    .result-heading span {
      display: block;
      margin-top: 4px;
      color: var(--muted);
      font-size: 11px;
    }
    .result-badge {
      display: inline-flex;
      flex: 0 0 auto;
      align-items: center;
      min-height: 24px;
      border: 1px solid var(--line-strong);
      border-radius: 5px;
      padding: 3px 8px;
      color: var(--text);
      font-size: 10px;
      font-weight: 700;
    }
    .result-heading .result-badge { display: inline-flex; margin-top: 0; }
    .result-badge.success { border-color: #2e725b; background: #143126; color: #75e6bd; }
    .result-metric-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(135px, 1fr));
      gap: 7px;
    }
    .result-metric {
      min-width: 0;
      min-height: 62px;
      border: 1px solid var(--line);
      border-left: 3px solid var(--accent);
      border-radius: 4px;
      padding: 9px 10px;
      background: var(--panel-soft);
    }
    .result-metric span,
    .result-row span,
    .loadout-role,
    .result-caption {
      color: var(--muted);
      font-size: 10px;
    }
    .result-metric strong {
      display: block;
      margin-top: 4px;
      color: var(--text);
      font-size: 12px;
      overflow-wrap: anywhere;
    }
    .result-columns {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 18px;
    }
    .result-section {
      min-width: 0;
      border-top: 1px solid var(--line);
      padding-top: 12px;
    }
    .result-section h3,
    .result-disclosure summary {
      margin: 0 0 8px;
      color: #dce1e5;
      font-size: 12px;
      font-weight: 700;
    }
    .result-list { display: grid; gap: 0; min-width: 0; }
    .result-row {
      min-width: 0;
      display: grid;
      grid-template-columns: minmax(105px, 0.35fr) minmax(0, 1fr) auto;
      gap: 5px 12px;
      align-items: center;
      min-height: 42px;
      border-bottom: 1px solid var(--line);
      padding: 7px 0;
    }
    .result-row:last-child { border-bottom: 0; }
    .result-row strong { color: var(--text); font-size: 11px; overflow-wrap: anywhere; }
    .result-row p { margin: 0; color: #cbd2d8; font-size: 11px; line-height: 1.5; overflow-wrap: anywhere; }
    .result-row > button { min-height: 30px; padding: 5px 9px; }
    .result-row-tail { display: flex; align-items: center; justify-content: flex-end; gap: 8px; min-width: 0; }
    .result-row-tail p { text-align: right; }
    .result-row-tail button { flex: 0 0 auto; min-height: 30px; padding: 5px 9px; }
    .result-chip-list { display: flex; flex-wrap: wrap; gap: 5px; }
    .result-chip {
      display: inline-flex;
      min-height: 24px;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 5px;
      padding: 3px 7px;
      background: var(--panel-soft);
      color: #cbd2d8;
      font-size: 10px;
      overflow-wrap: anywhere;
    }
    .player-registry-table { min-width: 920px; table-layout: auto; }
    .player-registry-table th:nth-child(1) { min-width: 210px; }
    .player-registry-table th:nth-child(2),
    .player-registry-table th:nth-child(3) { width: 112px; }
    .player-registry-table th:nth-child(4) { min-width: 420px; }
    .registry-player-name { display: block; color: var(--text); font-size: 12px; font-weight: 700; }
    .registry-player-meta { display: block; margin-top: 3px; color: var(--muted); font-size: 9px; overflow-wrap: anywhere; }
    .registry-state-button { min-width: 88px; min-height: 30px; padding: 5px 8px; }
    .discord-registration-list { display: flex; flex-wrap: wrap; gap: 5px; min-height: 25px; align-items: center; }
    .discord-registration-chip { display: inline-flex; align-items: center; gap: 6px; max-width: 100%; }
    .discord-registration-chip span { overflow-wrap: anywhere; }
    .discord-registration-chip button {
      width: 22px;
      min-width: 22px;
      min-height: 22px;
      padding: 0;
      border: 0;
      background: transparent;
      color: var(--danger);
      font-size: 16px;
      line-height: 1;
    }
    .player-discord-editor { margin-top: 8px; }
    .player-discord-editor summary { color: var(--accent); font-size: 10px; cursor: pointer; }
    .player-discord-editor-grid {
      display: grid;
      grid-template-columns: minmax(170px, 1fr) minmax(150px, 0.75fr) auto;
      gap: 7px;
      align-items: end;
      margin-top: 8px;
    }
    .player-discord-editor-grid label { min-width: 0; }
    .player-discord-editor-grid select,
    .player-discord-editor-grid input { min-width: 0; }
    .alert-settings-form { grid-template-columns: 160px 1fr 1fr auto; }
    .alert-channel-picker {
      grid-column: 1 / -1;
      display: grid;
      grid-template-columns: minmax(180px, 0.8fr) minmax(240px, 1.2fr) auto;
      gap: 10px;
      min-width: 0;
      margin: 0;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
    }
    .alert-channel-picker legend {
      padding: 0 5px;
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
    }
    .alert-channel-actions { display: flex; gap: 6px; align-items: end; }
    .alert-channel-selection,
    .alert-channel-picker .status { grid-column: 1 / -1; }
    .selected-channel-chip { gap: 6px; padding-right: 3px; }
    .selected-channel-chip button {
      width: 20px;
      min-height: 20px;
      padding: 0;
      border-radius: 4px;
      background: transparent;
      color: var(--muted);
      font-size: 16px;
      line-height: 1;
    }
    .discord-command-group-form {
      grid-template-columns: minmax(180px, 0.7fr) minmax(220px, 1fr) auto auto;
    }
    .section-note { margin: 2px 0 10px; color: var(--muted); font-size: 11px; line-height: 1.6; }
    .discord-member-permission-form {
      grid-template-columns: minmax(210px, 1fr) minmax(210px, 1fr) minmax(180px, 0.8fr) minmax(180px, 0.8fr) auto;
    }
    .discord-member-permission-form .checkbox-field {
      display: flex;
      min-height: 57px;
      justify-content: flex-end;
    }
    .discord-member-permission-form .checkbox-field span {
      display: inline-flex;
      align-items: center;
      min-height: 36px;
      gap: 7px;
      color: var(--text);
    }
    .discord-member-permission-form .checkbox-field input { width: auto; min-height: 0; }
    .bot-state-strip {
      display: grid;
      grid-template-columns: repeat(4, minmax(120px, 1fr));
      margin: 10px 0;
      border-top: 1px solid var(--line);
      border-bottom: 1px solid var(--line);
    }
    .bot-state-strip > div {
      min-width: 0;
      padding: 10px 12px;
      border-right: 1px solid var(--line);
    }
    .bot-state-strip > div:last-child { border-right: 0; }
    .bot-state-strip span { display: block; color: var(--muted); font-size: 10px; }
    .bot-state-strip strong {
      display: block;
      margin-top: 4px;
      overflow: hidden;
      color: var(--text);
      font-size: 12px;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .discord-bot-settings-form {
      grid-template-columns: minmax(150px, 0.6fr) minmax(130px, 0.4fr) auto;
      align-items: end;
    }
    .bot-runtime-actions { margin: 10px 0; }
    .switch-field { min-height: 57px; justify-content: space-between; }
    .switch-control { display: inline-flex; align-items: center; min-height: 34px; }
    .switch-control input { position: absolute; inline-size: 1px; block-size: 1px; opacity: 0; }
    .switch-control span {
      position: relative;
      display: inline-block;
      width: 42px;
      height: 22px;
      border: 1px solid var(--line-strong);
      border-radius: 11px;
      background: var(--panel-soft);
      transition: background 120ms ease, border-color 120ms ease;
    }
    .switch-control span::after {
      position: absolute;
      top: 3px;
      left: 3px;
      width: 14px;
      height: 14px;
      border-radius: 50%;
      background: var(--muted);
      content: "";
      transition: transform 120ms ease, background 120ms ease;
    }
    .switch-control input:checked + span { border-color: var(--accent); background: rgb(59 207 166 / 18%); }
    .switch-control input:checked + span::after { transform: translateX(19px); background: var(--accent); }
    .switch-control input:focus-visible + span { outline: 2px solid var(--accent); outline-offset: 2px; }
    .bot-secret-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    .bot-secret-grid form {
      grid-template-columns: minmax(220px, 1fr) auto auto;
      align-items: end;
      padding: 10px 0;
      border-top: 1px solid var(--line);
    }
    .bot-secret-grid .status { grid-column: 1 / -1; }
    .discord-bot-guild-form {
      grid-template-columns: minmax(220px, 0.8fr) minmax(220px, 1fr) auto;
      align-items: end;
    }
    .bot-command-actions { align-self: end; }
    .command-catalog-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 6px;
      margin-top: 10px;
    }
    .command-choice {
      display: grid;
      grid-template-columns: 18px minmax(0, 1fr);
      align-items: start;
      gap: 8px;
      min-height: 58px;
      padding: 8px;
      border: 1px solid var(--line);
      border-radius: 5px;
      background: var(--panel-soft);
      cursor: pointer;
    }
    .command-choice input { width: auto; min-height: 18px; margin: 1px 0 0; }
    .command-choice strong,
    .command-choice small { display: block; overflow-wrap: anywhere; }
    .command-choice strong { color: var(--text); font-size: 11px; }
    .command-choice small { margin-top: 3px; color: var(--muted); font-size: 10px; line-height: 1.4; }
    .discord-ranking-selection {
      grid-column: 1 / -1;
      min-width: 0;
      margin: 0;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 4px;
    }
    .discord-ranking-selection legend { padding: 0 6px; color: var(--muted); font-size: 11px; }
    .discord-ranking-guild-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 6px;
    }
    .discord-ranking-guild-grid label {
      display: grid;
      grid-template-columns: 18px minmax(0, 1fr);
      align-items: center;
      gap: 8px;
      min-height: 42px;
      padding: 8px;
      border: 1px solid var(--line);
      border-radius: 4px;
      background: var(--panel-soft);
    }
    .discord-group-table { min-width: 760px; table-layout: auto; }
    .discord-group-table th:nth-child(1) { width: 150px; }
    .discord-group-table th:nth-child(3) { width: 80px; }
    .discord-group-table th:nth-child(4) { width: 160px; }
    .discord-alias-block { margin-top: 18px; padding-top: 14px; border-top: 1px solid var(--line); }
    .discord-alias-form { grid-template-columns: minmax(180px, 0.7fr) minmax(220px, 1fr) auto auto; }
    .loadout-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(265px, 1fr));
      gap: 8px;
    }
    .loadout-card {
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: 5px;
      padding: 11px;
      background: var(--panel-soft);
    }
    .loadout-weapons {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
      gap: 8px;
      align-items: center;
    }
    .loadout-weapons strong { display: block; margin-top: 3px; font-size: 13px; overflow-wrap: anywhere; }
    .loadout-plus { color: var(--accent); font-weight: 800; }
    .loadout-parts { display: grid; gap: 7px; margin-top: 10px; padding-top: 9px; border-top: 1px solid var(--line); }
    .loadout-score { margin-top: 9px; color: var(--warning); font-size: 10px; font-weight: 700; }
    .result-disclosure { min-width: 0; border-top: 1px solid var(--line); padding-top: 10px; }
    .result-disclosure summary { width: max-content; cursor: pointer; }
    .drop-analysis-layout {
      display: grid;
      grid-template-columns: minmax(300px, 0.72fr) minmax(0, 1.28fr);
      gap: 18px;
      align-items: start;
      min-width: 0;
    }
    .drop-map-panel { min-width: 0; }
    .drop-map-toolbar {
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 8px;
    }
    .drop-map-toolbar label { width: min(260px, 100%); }
    .drop-map-stage {
      position: relative;
      width: 100%;
      aspect-ratio: 1;
      overflow: hidden;
      border: 1px solid var(--line-strong);
      border-radius: 5px;
      background: #080b0d;
    }
    .drop-map-stage img {
      display: block;
      width: 100%;
      height: 100%;
      object-fit: contain;
    }
    .drop-map-markers { position: absolute; inset: 0; }
    .drop-map-marker {
      position: absolute;
      width: 24px;
      min-height: 24px;
      transform: translate(-50%, -50%);
      border: 2px solid #08100d;
      border-radius: 50%;
      padding: 0;
      background: #46d2aa;
      color: #07110e;
      font-size: 9px;
      font-weight: 800;
      box-shadow: 0 0 0 2px rgb(70 210 170 / 25%);
    }
    .drop-map-marker:hover,
    .drop-map-marker.active {
      z-index: 2;
      background: #f0d479;
      box-shadow: 0 0 0 4px rgb(240 212 121 / 28%);
    }
    .drop-map-info {
      min-height: 58px;
      margin-top: 8px;
      border-left: 3px solid var(--accent);
      padding: 7px 9px;
      background: var(--panel-soft);
      color: #cbd2d8;
      font-size: 10px;
      line-height: 1.55;
    }
    .drop-map-info strong { display: block; color: var(--text); font-size: 12px; }
    .drop-region-table tr.active td { background: #14231e; }
    .drop-region-map-button { min-height: 28px; padding: 4px 8px; }
    .drop-view-controls { margin: 4px 0 12px; }
    .drop-view-panel { min-width: 0; }
    .drop-view-panel[hidden] { display: none !important; }
    .drop-view-panel .drop-map-panel { width: min(760px, 100%); }
    .flight-path-layout {
      display: grid;
      grid-template-columns: minmax(420px, 1.35fr) minmax(280px, 0.65fr);
      gap: 16px;
      align-items: start;
      min-width: 0;
    }
    .flight-path-map-stage {
      position: relative;
      width: 100%;
      aspect-ratio: 1;
      overflow: hidden;
      border: 1px solid var(--line-strong);
      border-radius: 5px;
      background: #080b0d;
    }
    .flight-path-map-stage img,
    .flight-path-map-stage svg {
      position: absolute;
      inset: 0;
      display: block;
      width: 100%;
      height: 100%;
    }
    .flight-path-map-stage img { object-fit: contain; }
    .flight-path-map-stage svg { overflow: visible; }
    .flight-route-line {
      cursor: pointer;
      transition: opacity 120ms ease, stroke-width 120ms ease;
      vector-effect: non-scaling-stroke;
    }
    .flight-route-line:hover,
    .flight-route-line:focus,
    .flight-route-line.active {
      opacity: 1 !important;
      outline: none;
      filter: drop-shadow(0 0 3px #ffffff);
    }
    .flight-path-list {
      max-height: 640px;
      overflow: auto;
      border-top: 1px solid var(--line);
    }
    .flight-path-row {
      width: 100%;
      min-height: 58px;
      display: grid;
      grid-template-columns: 34px minmax(0, 1fr) auto;
      gap: 9px;
      align-items: center;
      border: 0;
      border-bottom: 1px solid var(--line);
      border-radius: 0;
      padding: 8px 4px;
      background: transparent;
      color: var(--text);
      text-align: left;
    }
    .flight-path-row:hover,
    .flight-path-row.active { background: #14231e; }
    .flight-path-rank {
      display: grid;
      width: 27px;
      height: 27px;
      place-items: center;
      border: 1px solid currentColor;
      border-radius: 50%;
      font-size: 10px;
      font-weight: 700;
    }
    .flight-path-row strong,
    .flight-path-row small { display: block; overflow-wrap: anywhere; }
    .flight-path-row strong { font-size: 11px; }
    .flight-path-row small { margin-top: 3px; color: var(--muted); font-size: 10px; }
    .flight-path-share { color: var(--accent); font-size: 12px; font-weight: 700; }
    .flight-path-detail {
      min-height: 66px;
      margin-top: 8px;
      border-left: 3px solid var(--accent);
      padding: 8px 10px;
      background: var(--panel-soft);
      color: var(--muted);
      font-size: 11px;
      line-height: 1.55;
    }
    .flight-path-detail strong { display: block; color: var(--text); font-size: 12px; }
    @media (max-width: 1050px) {
      .flight-path-layout { grid-template-columns: 1fr; }
      .flight-path-list { max-height: 360px; }
    }
    .recommendation-view-switch {
      display: inline-flex;
      width: max-content;
      max-width: 100%;
      gap: 3px;
      border: 1px solid var(--line-strong);
      border-radius: 6px;
      padding: 3px;
      background: #0b0f11;
    }
    .recommendation-view-switch button {
      min-width: 76px;
      min-height: 30px;
      border-color: transparent;
      padding: 5px 12px;
      background: transparent;
      color: var(--muted);
    }
    .recommendation-view-switch button.active {
      border-color: #2d725c;
      background: #14231e;
      color: var(--accent);
    }
    .recommendation-panel {
      display: grid;
      min-width: 0;
      gap: 14px;
    }
    .recommendation-panel[hidden] { display: none; }
    .recommendation-chart-toolbar {
      display: flex;
      align-items: end;
      justify-content: flex-end;
      gap: 8px;
    }
    .recommendation-chart-toolbar label { width: min(280px, 100%); }
    #trendViewControls {
      justify-content: space-between;
      flex-wrap: wrap;
      margin-top: 12px;
    }
    .weapon-trend-toolbar {
      justify-content: space-between;
      flex-wrap: wrap;
      margin-bottom: 10px;
    }
    .trend-control-cluster {
      display: flex;
      align-items: end;
      flex-wrap: wrap;
      gap: 8px;
    }
    .metric-chart-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px 20px;
      min-width: 0;
    }
    .metric-chart {
      min-width: 0;
      border-top: 1px solid var(--line);
      padding-top: 11px;
    }
    .metric-chart h3 {
      margin: 0 0 9px;
      color: #dce1e5;
      font-size: 12px;
    }
    .metric-chart-list { display: grid; gap: 9px; min-width: 0; }
    .metric-chart-row {
      display: grid;
      grid-template-columns: minmax(105px, 0.55fr) minmax(120px, 1fr) auto;
      align-items: center;
      gap: 7px 10px;
      min-width: 0;
    }
    .metric-chart-label {
      min-width: 0;
      color: var(--text);
      font-size: 11px;
      font-weight: 700;
      overflow-wrap: anywhere;
    }
    .metric-chart-label small {
      display: block;
      margin-top: 2px;
      color: var(--muted);
      font-size: 9px;
      font-weight: 500;
    }
    .metric-chart-track {
      position: relative;
      width: 100%;
      height: 10px;
      overflow: hidden;
      border: 1px solid #253037;
      border-radius: 3px;
      background: #0a0d0f;
    }
    .metric-chart-fill {
      display: block;
      height: 100%;
      background: var(--accent);
    }
    .metric-chart-fill.warning { background: var(--warning); }
    .metric-chart-fill.info { background: #67b7dc; }
    .metric-chart-value {
      color: #dce1e5;
      font-size: 10px;
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
    }
    .comparison-primary { grid-template-columns: repeat(4, minmax(0, 1fr)); }
    .comparison-picker {
      min-width: 0;
      margin: 0;
      padding: 10px 12px 12px;
      border: 1px solid var(--line);
      border-radius: 4px;
    }
    .comparison-picker legend { padding: 0 6px; color: #dce1e5; font-size: 12px; font-weight: 700; }
    .comparison-picker legend span { color: var(--accent); font-variant-numeric: tabular-nums; }
    .comparison-item-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 7px;
      max-height: 210px;
      overflow: auto;
    }
    .comparison-item {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      align-items: center;
      gap: 8px;
      min-height: 38px;
      padding: 7px 9px;
      border: 1px solid var(--line);
      border-radius: 3px;
      background: #0d1114;
      color: var(--text);
      cursor: pointer;
    }
    .comparison-item:has(input:checked) { border-color: var(--accent); background: #13211d; }
    .comparison-item input { width: auto; min-height: 0; }
    .comparison-item span { min-width: 0; overflow-wrap: anywhere; font-size: 11px; }
    .comparison-item small { display: block; margin-top: 2px; color: var(--muted); font-size: 9px; }
    .comparison-view-controls { margin-top: 12px; }
    .comparison-bars { display: grid; gap: 10px; }
    .comparison-bar-row {
      display: grid;
      grid-template-columns: minmax(130px, 0.45fr) minmax(140px, 1fr) minmax(105px, auto);
      align-items: center;
      gap: 10px;
      min-width: 0;
    }
    .comparison-bar-label { min-width: 0; overflow-wrap: anywhere; font-size: 11px; font-weight: 700; }
    .comparison-bar-label small { display: block; margin-top: 2px; color: var(--muted); font-size: 9px; font-weight: 500; }
    .comparison-bar-track { height: 15px; border: 1px solid #253037; border-radius: 3px; background: #080a0c; overflow: hidden; }
    .comparison-bar-fill { display: block; height: 100%; background: var(--accent); }
    .comparison-bar-value { text-align: right; color: #e1e6ea; font-size: 11px; font-variant-numeric: tabular-nums; white-space: nowrap; }
    .time-insight-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin-bottom: 14px; }
    .time-insight-kpi { border-left: 3px solid var(--accent); padding: 8px 10px; background: #0d1114; }
    .time-insight-kpi span { display: block; color: var(--muted); font-size: 9px; text-transform: uppercase; }
    .time-insight-kpi strong { display: block; margin-top: 4px; color: #f3f6f8; font-size: 16px; }
    .time-hour-grid { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: 5px; }
    .time-hour-cell { min-width: 0; padding: 7px 3px; border: 1px solid var(--line); border-radius: 3px; background: #0a0d0f; text-align: center; }
    .time-hour-cell i { display: block; width: 100%; min-height: 6px; margin-bottom: 5px; border-radius: 2px; background: var(--accent); }
    .time-hour-cell strong { display: block; font-size: 10px; }
    .time-hour-cell span { display: block; margin-top: 2px; color: var(--muted); font-size: 8px; overflow-wrap: anywhere; }
    .trend-chart-overview {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
      margin-bottom: 10px;
    }
    .trend-chart-stat {
      min-width: 0;
      padding: 9px 10px;
      border: 1px solid var(--line);
      border-radius: 4px;
      background: var(--panel-soft);
    }
    .trend-chart-stat span,
    .trend-chart-stat small {
      display: block;
      color: var(--muted);
      font-size: 9px;
    }
    .trend-chart-stat strong {
      display: block;
      margin: 4px 0 2px;
      color: var(--text);
      font-size: 14px;
      font-variant-numeric: tabular-nums;
      overflow-wrap: anywhere;
    }
    .trend-line-frame {
      width: 100%;
      min-width: 0;
      overflow-x: auto;
      border: 1px solid var(--line);
      border-radius: 5px;
      background: #0a0d0f;
    }
    .trend-line-chart {
      display: block;
      width: 100%;
      min-width: 680px;
      height: auto;
      aspect-ratio: 920 / 330;
    }
    .trend-line-grid { stroke: #253037; stroke-width: 1; }
    .trend-line-axis { stroke: #44515a; stroke-width: 1; }
    .trend-line-path {
      fill: none;
      stroke: var(--accent);
      stroke-width: 3;
      stroke-linecap: round;
      stroke-linejoin: round;
    }
    .trend-line-point {
      fill: #0a0d0f;
      stroke: var(--accent);
      stroke-width: 2;
    }
    .trend-line-point.latest { fill: var(--accent); }
    .trend-line-chart text {
      fill: #8f9aa3;
      font-size: 10px;
      letter-spacing: 0;
      font-variant-numeric: tabular-nums;
    }
    .trend-chart-note {
      margin: 8px 0 0;
      color: var(--muted);
      font-size: 10px;
      line-height: 1.5;
    }
    .trend-card-list { display: none; gap: 8px; margin-top: 10px; }
    .trend-card {
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: 5px;
      padding: 10px;
      background: var(--panel-soft);
    }
    .trend-card > strong { display: block; margin-bottom: 8px; font-size: 12px; }
    .trend-card dl { display: grid; grid-template-columns: 1fr 1fr; gap: 7px 12px; margin: 0; }
    .trend-card dt { color: var(--muted); font-size: 10px; }
    .trend-card dd { margin: 2px 0 0; color: var(--text); font-size: 11px; }
    .detail-panel {
      margin-top: 12px;
      padding: 10px 12px;
      border-left: 3px solid var(--accent);
      background: #f8fafc;
    }
    .app-dialog {
      width: min(920px, calc(100vw - 28px));
      max-width: none;
      max-height: min(88vh, 900px);
      margin: auto;
      padding: 0;
      overflow: hidden;
      color: var(--text);
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel);
      box-shadow: 0 24px 80px rgb(0 0 0 / 55%);
    }
    .app-dialog::backdrop { background: rgb(0 0 0 / 72%); }
    .app-dialog-shell {
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      max-height: min(88vh, 900px);
    }
    .app-dialog-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
      background: var(--panel-strong);
    }
    .app-dialog-header h3 { margin: 0; font-size: 14px; }
    .app-dialog-close {
      width: 32px;
      min-width: 32px;
      height: 32px;
      padding: 0;
      font-size: 21px;
      line-height: 1;
    }
    .app-dialog-content {
      min-width: 0;
      padding: 14px;
      overflow: auto;
      overscroll-behavior: contain;
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
    .operational-drill-table { min-width: 720px; table-layout: auto; }
    .operational-drill-table th, .operational-drill-table td { white-space: nowrap; }
    .operational-drill-table th:nth-child(1) { width: 54px; }
    .operational-drill-table th:nth-child(2) { width: 94px; }
    .operational-drill-table th:nth-child(3) { width: 84px; }
    .operational-drill-table th:nth-child(4) { min-width: 190px; }
    .operational-drill-table th:nth-child(5) { width: 88px; }
    .operational-drill-table th:nth-child(6) { width: 70px; }
    .operational-drill-table th:nth-child(7) { width: 74px; }
    .deletion-request-table { min-width: 720px; table-layout: auto; }
    .table-scroll { width: 100%; max-width: 100%; overflow-x: auto; }
    .table-scroll .detail-table { min-width: 680px; }
    .confirmation-contract, .dry-run-contract, .backup-readiness-contract { margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--line); }
    .backup-builder-contract { margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--line); }
    .confirmation-contract code, .dry-run-contract code, .backup-readiness-contract code { display: block; margin: 8px 0; overflow-wrap: anywhere; font-size: 12px; }
    .dry-run-contract, .backup-readiness-contract { min-width: 0; max-width: 100%; }
    .dry-run-contract .table-scroll, .backup-readiness-contract .table-scroll { margin-top: 8px; }
    .backup-evidence-form { grid-template-columns: repeat(3, minmax(0, 1fr)); margin-top: 12px; align-items: end; }
    .backup-evidence-form [hidden] { display: none !important; }
    .backup-evidence-form .checkbox-field { display: inline-flex; align-items: center; gap: 8px; min-height: 38px; }
    .backup-evidence-form .checkbox-field input { width: auto; min-height: 0; }
    .backup-evidence-form button { align-self: end; }
    .review-packet-verifier-form { grid-template-columns: minmax(0, 1fr) auto auto; margin-top: 12px; }
    .review-packet-verifier-form .checkbox-field { display: inline-flex; align-items: center; gap: 8px; min-height: 38px; }
    .review-packet-verifier-form .checkbox-field input { width: auto; min-height: 0; }
    .review-packet-verifier-result { min-width: 0; max-width: 100%; margin-top: 12px; }
    .review-packet-verifier-result .status, .review-packet-verifier-result code { overflow-wrap: anywhere; word-break: break-word; }
    .review-packet-comparer-form { grid-template-columns: repeat(2, minmax(0, 1fr)); margin-top: 12px; align-items: end; }
    .review-packet-comparer-form .checkbox-field { display: inline-flex; align-items: center; gap: 8px; min-height: 38px; }
    .review-packet-comparer-form .checkbox-field input { width: auto; min-height: 0; }
    .review-packet-comparer-result { min-width: 0; max-width: 100%; margin-top: 12px; }
    .review-packet-comparer-result .status, .review-packet-comparer-result code, .comparison-value { overflow-wrap: anywhere; word-break: break-word; }
    .review-packet-comparer-result .comparison-table { min-width: 760px; table-layout: auto; }
    .review-packet-comparer-result .comparison-check-table { min-width: 960px; }
    .review-packet-comparer-result .comparison-contract-table th:nth-child(1), .review-packet-comparer-result .comparison-contract-table td:nth-child(1) { min-width: 110px; }
    .review-packet-comparer-result .comparison-contract-table th:nth-child(2), .review-packet-comparer-result .comparison-contract-table td:nth-child(2) { min-width: 190px; }
    .review-packet-comparer-result .comparison-check-table th:nth-child(1), .review-packet-comparer-result .comparison-check-table td:nth-child(1) { min-width: 170px; }
    .review-packet-comparer-result .comparison-check-table th:nth-child(2), .review-packet-comparer-result .comparison-check-table td:nth-child(2) { min-width: 120px; }
    .review-packet-comparer-result .comparison-canonical-table th:nth-child(1), .review-packet-comparer-result .comparison-canonical-table td:nth-child(1) { min-width: 240px; }
    .review-packet-comparer-result .comparison-canonical-table th:nth-child(2), .review-packet-comparer-result .comparison-canonical-table td:nth-child(2) { min-width: 90px; }
    .review-packet-comparer-result .comparison-value { min-width: 180px; max-width: 420px; white-space: normal; }
    .confirmation-input-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; align-items: end; }
    .player-controls { display: grid; grid-template-columns: minmax(260px, 1fr) 110px 130px auto auto; gap: 10px; align-items: end; }
    .toggle-row { display: flex; flex-wrap: wrap; gap: 12px; margin: 12px 0; color: var(--muted); font-size: 13px; }
    .toggle-row label { display: inline-flex; grid-template-columns: none; align-items: center; gap: 6px; }
    .toggle-row input { width: auto; min-height: 0; }
    .replay-explorer-bar {
      display: grid;
      grid-template-columns: repeat(3, minmax(150px, 220px)) auto auto minmax(100px, 1fr);
      gap: 10px;
      align-items: end;
      margin: 12px 0;
    }
    .replay-explorer-bar .checkbox-field {
      display: inline-flex;
      align-items: center;
      align-self: center;
      gap: 7px;
      min-height: 38px;
      color: var(--muted);
      font-size: 12px;
    }
    .replay-explorer-bar .checkbox-field input { width: auto; min-height: 0; }
    .timeline-event-count { align-self: center; text-align: right; font-variant-numeric: tabular-nums; }
    .replay-quick-nav {
      display: grid;
      grid-template-columns: 86px minmax(0, 1fr);
      gap: 10px;
      align-items: center;
      margin: 4px 0 12px;
      padding: 9px 0;
      border-top: 1px solid var(--line);
      border-bottom: 1px solid var(--line);
    }
    .replay-quick-nav > strong { color: var(--muted); font-size: 11px; text-transform: uppercase; }
    .replay-quick-actions { display: flex; flex-wrap: wrap; gap: 7px; min-width: 0; }
    .replay-quick-actions button { min-height: 32px; padding: 6px 10px; font-size: 11px; }
    .replay-legend {
      display: grid;
      gap: 8px;
      margin: -2px 0 12px;
      padding: 10px 11px;
      border: 1px solid var(--line);
      border-radius: 4px;
      background: #0a0d0f;
      color: var(--muted);
      font-size: 11px;
    }
    .replay-legend-group { display: flex; flex-wrap: wrap; gap: 7px 14px; align-items: center; }
    .replay-legend-group > strong { width: 70px; color: var(--text); font-size: 11px; }
    .replay-legend span { display: inline-flex; align-items: center; gap: 6px; min-height: 22px; white-space: nowrap; }
    .legend-actor { display: inline-block; width: 11px; height: 11px; border: 2px solid currentColor; border-radius: 50%; background: currentColor; }
    .legend-actor.focus { color: #4bd0a0; box-shadow: 0 0 0 2px #fff; }
    .legend-actor.ally { color: #54c8ff; }
    .legend-actor.enemy { color: #ff786e; }
    .legend-actor.registered { color: #d98cff; }
    .legend-actor.bot { color: #87939d; }
    .legend-line { display: inline-block; width: 28px; height: 0; border-top: 3px solid #4bd0a0; }
    .legend-line.vehicle { border-top-color: #ffb84d; border-top-style: dashed; }
    .legend-line.airborne { border-top-color: #54c8ff; border-top-style: dashed; }
    .legend-line.dbno { border-top-color: #ff5f6d; border-top-style: dotted; }
    .legend-symbol {
      display: inline-grid;
      width: 32px;
      height: 20px;
      place-items: center;
      color: #f5f7fa;
      font-style: normal;
      font-size: 18px;
      font-weight: 800;
      line-height: 1;
    }
    .legend-symbol.shot { color: #64d8ff; }
    .legend-symbol.throw { color: #ffb74d; }
    .legend-symbol.hit { color: #ffd54f; }
    .legend-symbol.dbno { color: #ff9f43; }
    .legend-symbol.kill { color: #ff5f6d; font-size: 24px; }
    .legend-symbol.revive { color: #45d6b0; font-size: 21px; }
    .legend-symbol.drop { color: #54c8ff; }
    .legend-symbol.landing { color: #ffeb3b; }
    .legend-symbol.hit-taken { color: #ff6b6b; }
    .legend-symbol.environment { color: #c3ccd6; border: 1px dotted currentColor; font-size: 14px; }
    .legend-symbol.dbno-taken { color: #ff5f6d; }
    .legend-symbol.death { color: #ff8a80; border: 1px solid currentColor; font-size: 20px; }
    .legend-symbol.engagement { border: 2px dashed #ffd54f; border-radius: 50%; font-size: 0; }
    .legend-symbol.activity { border: 2px dotted #69b8e8; border-radius: 50%; font-size: 0; }
    .timeline-now-event {
      display: grid;
      grid-template-columns: 30px minmax(0, 1fr) auto;
      gap: 9px;
      align-items: center;
      min-height: 52px;
      margin: 10px 0 12px;
      padding: 8px 11px;
      border: 1px solid var(--line);
      border-left: 3px solid var(--info);
      background: #0d1114;
    }
    .timeline-now-event .event-copy { min-width: 0; display: grid; gap: 2px; }
    .timeline-now-event strong, .timeline-now-event span { overflow-wrap: anywhere; }
    .timeline-now-event .event-meta { color: var(--muted); font-size: 11px; }
    .timeline-now-event .event-time { color: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums; white-space: nowrap; }
    .timeline-event-badge {
      display: inline-grid;
      width: 26px;
      height: 26px;
      place-items: center;
      flex: 0 0 26px;
      border: 1px solid currentColor;
      border-radius: 50%;
      color: #cbd2d8;
      background: #0b0d0f;
      font-size: 14px;
      font-style: normal;
      font-weight: 800;
      line-height: 1;
    }
    .timeline-event-badge.event-tone-drop { color: #54c8ff; border-radius: 3px; transform: rotate(45deg); }
    .timeline-event-badge.event-tone-drop > span { transform: rotate(-45deg); }
    .timeline-event-badge.event-tone-landing { color: #ffeb3b; border-radius: 3px 3px 50% 50%; }
    .timeline-event-badge.event-tone-shot { color: #64d8ff; }
    .timeline-event-badge.event-tone-throw { color: #ffb74d; border-radius: 3px; }
    .timeline-event-badge.event-tone-attack { color: #ffb74d; }
    .timeline-event-badge.event-tone-hit-caused { color: #ffd54f; }
    .timeline-event-badge.event-tone-hit-taken { color: #ff6b6b; border-radius: 3px; }
    .timeline-event-badge.event-tone-environment { color: #c3ccd6; border-radius: 3px; border-style: dotted; }
    .timeline-event-badge.event-tone-dbno-caused { color: #ff9f43; border-radius: 3px; }
    .timeline-event-badge.event-tone-dbno-taken { color: #ff5f6d; border-radius: 3px; background: #35171b; }
    .timeline-event-badge.event-tone-kill { color: #ff5f6d; border-width: 2px; }
    .timeline-event-badge.event-tone-death { color: #ff8a80; border-radius: 3px; border-width: 2px; }
    .timeline-event-badge.event-tone-revive { color: #45d6b0; }
    .timeline-event-badge.event-tone-engagement { color: #ffd54f; border-style: dashed; }
    .timeline-event-badge.event-tone-activity { color: #69b8e8; border-style: dotted; }
    .timeline-event-badge.event-tone-plane { color: #69b8e8; border-radius: 3px; }
    .timeline-event-badge.event-tone-care { color: #ef9a9a; border-radius: 3px; }
    .timeline-range { display: grid; grid-template-columns: minmax(0, 1fr) minmax(210px, auto); gap: 12px; align-items: center; margin: 12px 0; }
    #timelineClock { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
    #timelineScrubber { width: 100%; margin: 0; padding: 0; }
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
    .timeline-event-item {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 52px;
      gap: 6px;
      min-width: 0;
    }
    .timeline-event-row {
      display: grid;
      grid-template-columns: 28px 54px minmax(0, 1fr);
      gap: 7px;
      align-items: center;
      text-align: left;
      min-height: 52px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--text);
    }
    .timeline-event-row > span:not(.timeline-event-copy) { color: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
    .timeline-event-copy { min-width: 0; display: grid; gap: 3px; }
    .timeline-event-row strong { overflow-wrap: anywhere; font-size: 12px; }
    .timeline-event-row em { color: var(--muted); font-size: 11px; font-style: normal; overflow-wrap: anywhere; }
    .timeline-event-item.active .timeline-event-row { border-color: var(--accent); background: #eef7ff; }
    .timeline-event-item.current .timeline-event-row { box-shadow: inset 3px 0 0 var(--info); }
    .timeline-map-button { min-width: 0; width: 52px; min-height: 52px; padding: 5px; font-size: 11px; }
    .timeline-event-detail {
      border-left: 3px solid var(--accent);
      padding: 10px 12px;
      background: #f8fafc;
      min-height: 110px;
    }
    .timeline-team-list {
      display: grid;
      gap: 6px;
      max-height: 360px;
      overflow: auto;
      padding-right: 3px;
    }
    .team-member {
      display: grid;
      width: 100%;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 4px 8px;
      align-items: center;
      text-align: left;
      border: 1px solid var(--line);
      background: #fff;
      padding: 8px 10px;
    }
    .team-member.self { border-color: #39ff14; }
    .team-member.registered { background: #eef7ff; border-color: var(--accent); }
    .team-member.selected { box-shadow: inset 3px 0 0 var(--accent); }
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
      .trend-filter { grid-template-columns: 1fr; }
      .query-primary, .filter-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .result-columns { grid-template-columns: 1fr; }
      .drop-analysis-layout { grid-template-columns: 1fr; }
      .player-controls { grid-template-columns: 1fr; }
      .replay-explorer-bar { grid-template-columns: 1fr; }
      .timeline-event-count { text-align: left; }
      .replay-quick-nav { grid-template-columns: 1fr; }
      .replay-legend-group > strong { width: 100%; }
      .confirmation-input-row { grid-template-columns: 1fr; }
      .backup-evidence-form { grid-template-columns: 1fr; }
      .review-packet-verifier-form { grid-template-columns: 1fr; }
      .review-packet-comparer-form { grid-template-columns: 1fr; }
      .timeline-range { grid-template-columns: 1fr; }
      .replay-detail-layout { grid-template-columns: 1fr; }
      .alert-settings-form,
      .alert-channel-picker,
      .discord-member-permission-form,
      .discord-command-group-form,
      .discord-alias-form,
      .discord-bot-settings-form,
      .discord-bot-guild-form,
      .bot-secret-grid,
      .bot-secret-grid form { grid-template-columns: 1fr; }
      .bot-state-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .bot-state-strip > div:nth-child(2) { border-right: 0; }
      .bot-state-strip > div:nth-child(-n + 2) { border-bottom: 1px solid var(--line); }
      header { align-items: flex-start; flex-direction: column; }
    }

    :root {
      color-scheme: dark;
      font-family: "Segoe UI", "Malgun Gothic", Arial, sans-serif;
      --bg: #0b0d0f;
      --panel: #111417;
      --panel-strong: #171b1f;
      --panel-soft: #0e1113;
      --text: #e7ebee;
      --muted: #8f99a3;
      --line: #2a3036;
      --line-strong: #3a424a;
      --accent: #42d3a4;
      --accent-ink: #07130f;
      --info: #69b8e8;
      --warning: #e6c15d;
      --danger: #df6670;
    }
    html, body { min-height: 100%; }
    body {
      height: 100vh;
      overflow: hidden;
      background: var(--bg);
      color: var(--text);
    }
    button, input, select, textarea { font-family: inherit; }
    .app-header {
      min-height: 62px;
      padding: 9px 14px;
      border-bottom: 1px solid var(--line);
      background: #0d0f11;
      display: grid;
      grid-template-columns: 218px minmax(280px, 1fr) auto;
      align-items: center;
      gap: 14px;
    }
    .brand-lockup {
      display: flex;
      align-items: center;
      gap: 10px;
      min-width: 0;
    }
    .brand-mark {
      width: 36px;
      height: 36px;
      border: 1px solid var(--accent);
      border-radius: 5px;
      object-fit: contain;
    }
    .brand-lockup h1 {
      margin: 0;
      font-size: 16px;
      line-height: 1.2;
      letter-spacing: 0;
    }
    .brand-lockup div > span,
    .rail-header > span,
    .side-heading > span,
    #workspaceEyebrow {
      display: block;
      margin-top: 3px;
      color: var(--muted);
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0;
    }
    .command-strip {
      min-width: 0;
      min-height: 40px;
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 7px 12px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel-soft);
    }
    .command-message {
      min-width: 0;
      overflow: hidden;
      color: #cbd2d8;
      font-size: 12px;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .live-indicator {
      display: inline-flex;
      flex: 0 0 auto;
      align-items: center;
      gap: 6px;
      color: var(--accent);
      font-size: 10px;
      font-weight: 800;
    }
    .live-indicator i,
    .status-dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--accent);
      box-shadow: 0 0 10px rgba(66, 211, 164, 0.55);
    }
    .header-meta {
      display: grid;
      grid-template-columns: auto auto;
      align-items: center;
      gap: 12px;
      white-space: nowrap;
    }
    .header-meta span {
      color: var(--muted);
      font-size: 10px;
      font-weight: 700;
    }
    .header-meta strong {
      color: var(--warning);
      font-family: Consolas, "Courier New", monospace;
      font-size: 16px;
      font-variant-numeric: tabular-nums;
    }
    .app-shell {
      height: calc(100vh - 62px);
      display: grid;
      grid-template-columns: 218px minmax(0, 1fr) 252px;
      overflow: hidden;
    }
    .side-panel,
    .system-rail {
      min-width: 0;
      overflow-y: auto;
      background: #0d1012;
    }
    .side-panel {
      display: flex;
      flex-direction: column;
      border-right: 1px solid var(--line);
    }
    .side-heading,
    .rail-header {
      padding: 15px 14px 11px;
      border-bottom: 1px solid var(--line);
    }
    .side-heading strong,
    .rail-header strong {
      display: block;
      margin-top: 4px;
      font-size: 13px;
    }
    .side-nav {
      display: grid;
      padding: 8px;
      gap: 4px;
    }
    .side-nav button {
      min-height: 42px;
      display: grid;
      grid-template-columns: 28px minmax(0, 1fr);
      align-items: center;
      gap: 8px;
      padding: 8px 10px;
      border: 1px solid transparent;
      border-radius: 5px;
      background: transparent;
      color: #b2bac1;
      font-size: 13px;
      text-align: left;
    }
    .side-nav button span {
      color: #68727b;
      font-family: Consolas, "Courier New", monospace;
      font-size: 10px;
    }
    .side-nav button:hover {
      border-color: var(--line);
      background: var(--panel);
      color: var(--text);
    }
    .side-nav button.active {
      border-color: #2d5548;
      background: #14211d;
      color: var(--accent);
    }
    .side-nav button.active span { color: var(--accent); }
    .side-foot {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      align-items: center;
      gap: 9px;
      margin-top: auto;
      padding: 14px;
      border-top: 1px solid var(--line);
    }
    .side-foot strong,
    .side-foot span {
      display: block;
      font-size: 11px;
    }
    .side-foot div > span {
      margin-top: 3px;
      color: var(--muted);
      font-size: 10px;
    }
    main {
      max-width: none;
      min-width: 0;
      margin: 0;
      padding: 0 18px 24px;
      display: block;
      overflow-y: auto;
      background: #0a0c0e;
    }
    .workspace-heading {
      position: sticky;
      z-index: 10;
      top: 0;
      min-height: 92px;
      margin: 0 -18px 16px;
      padding: 18px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
      border-bottom: 1px solid var(--line);
      background: rgba(10, 12, 14, 0.97);
    }
    .workspace-heading h2 {
      margin: 4px 0 0;
      font-size: 20px;
    }
    .workspace-heading p {
      margin: 5px 0 0;
      color: var(--muted);
      font-size: 12px;
    }
    .workspace-section-tabs {
      display: none;
      min-width: 0;
      margin: -4px 0 14px;
      padding: 0 0 8px;
      gap: 6px;
      overflow-x: auto;
      scrollbar-width: thin;
    }
    .workspace-section-tabs.visible {
      position: sticky;
      z-index: 9;
      top: 92px;
      display: flex;
      background: rgba(10, 12, 14, 0.98);
    }
    .workspace-section-tabs button {
      min-height: 34px;
      flex: 0 0 auto;
      padding: 6px 11px;
      border-color: var(--line-strong);
      background: #101417;
      color: #aeb7be;
      white-space: nowrap;
    }
    .workspace-section-tabs button.active {
      border-color: #2d725c;
      background: #14231e;
      color: var(--accent);
    }
    .analysis-player-context {
      display: none;
      min-width: 0;
      margin: -4px 0 14px;
      padding: 10px 12px;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      border: 1px solid var(--line);
      border-left: 3px solid var(--accent);
      border-radius: 5px;
      background: #0f1416;
    }
    body[data-active-view="players"] .analysis-player-context { display: flex; }
    .analysis-player-context-copy {
      display: grid;
      min-width: 0;
      gap: 3px;
    }
    .analysis-player-context-copy > span {
      color: var(--muted);
      font-size: 9px;
      font-weight: 700;
      text-transform: uppercase;
    }
    .analysis-player-context-copy > strong {
      color: var(--text);
      font-size: 13px;
      overflow-wrap: anywhere;
    }
    .analysis-player-context-copy > small {
      color: var(--muted);
      font-size: 10px;
      overflow-wrap: anywhere;
    }
    .analysis-player-context button { flex: 0 0 auto; }
    .intelligence-view-toolbar {
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 12px;
      margin-top: 14px;
      padding: 10px 0;
      border-top: 1px solid var(--line);
      border-bottom: 1px solid var(--line);
    }
    .intelligence-view-toolbar .segmented-control { flex-wrap: wrap; }
    .intelligence-chart-options {
      display: flex;
      align-items: end;
      gap: 8px;
    }
    .intelligence-chart-options label,
    .intelligence-breakdown-option { min-width: 150px; }
    .intelligence-status {
      display: block;
      color: var(--text);
      line-height: 1.45;
    }
    .intelligence-quality-strip {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      margin-bottom: 12px;
      padding: 10px 12px;
      border-left: 3px solid var(--accent);
      background: #0d1714;
    }
    .intelligence-quality-strip.warning {
      border-left-color: var(--warning);
      background: #18160d;
    }
    .intelligence-quality-strip.error {
      border-left-color: var(--danger);
      background: #1a1012;
    }
    .intelligence-quality-strip strong { display: block; color: var(--text); }
    .intelligence-quality-strip small { color: var(--muted); }
    .intelligence-quality-strip output {
      flex: 0 0 auto;
      color: var(--accent);
      font-weight: 800;
      font-size: 17px;
    }
    .intelligence-kpi-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
    }
    .intelligence-kpi {
      min-width: 0;
      padding: 11px 12px;
      border: 1px solid var(--line);
      border-left: 2px solid #67b7dc;
      border-radius: 5px;
      background: #0e1214;
    }
    .intelligence-kpi:nth-child(3n + 2) { border-left-color: var(--accent); }
    .intelligence-kpi:nth-child(3n) { border-left-color: var(--warning); }
    .intelligence-kpi span {
      display: block;
      color: var(--muted);
      font-size: 10px;
    }
    .intelligence-kpi strong {
      display: block;
      margin-top: 5px;
      color: var(--text);
      font-size: 17px;
      overflow-wrap: anywhere;
    }
    .intelligence-data-section {
      margin-top: 16px;
      padding-top: 13px;
      border-top: 1px solid var(--line);
    }
    .intelligence-data-section > h3 {
      margin: 0 0 9px;
      color: var(--text);
      font-size: 12px;
    }
    .intelligence-metric-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 0 18px;
    }
    .intelligence-metric-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: baseline;
      gap: 10px;
      min-width: 0;
      padding: 7px 0;
      border-bottom: 1px solid #20272b;
    }
    .intelligence-metric-row span { color: var(--muted); overflow-wrap: anywhere; }
    .intelligence-metric-row strong { color: var(--text); text-align: right; }
    .intelligence-table { width: 100%; min-width: 720px; }
    .intelligence-table td:first-child,
    .intelligence-table th:first-child { text-align: left; }
    .intelligence-table td:not(:first-child),
    .intelligence-table th:not(:first-child) { text-align: right; }
    .intelligence-list {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px 18px;
    }
    .intelligence-list-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      padding: 7px 0;
      border-bottom: 1px solid #20272b;
    }
    .intelligence-list-row small { display: block; color: var(--muted); }
    .intelligence-definition {
      display: grid;
      grid-template-columns: minmax(130px, .7fr) minmax(0, 2fr) minmax(120px, .8fr);
      gap: 12px;
      padding: 9px 0;
      border-bottom: 1px solid #20272b;
    }
    .intelligence-definition strong { color: var(--text); }
    .intelligence-definition span,
    .intelligence-definition small { color: var(--muted); overflow-wrap: anywhere; }
    .intelligence-empty { padding: 18px 0; color: var(--muted); }
    main > section[data-view] {
      display: none;
      min-width: 0;
      margin: 0 0 14px;
      padding: 16px;
      overflow-x: auto;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel);
    }
    main > section[data-view][hidden] { display: none !important; }
    body[data-active-view="overview"] main > section[data-view="overview"],
    body[data-active-view="players"] main > section[data-view="players"],
    body[data-active-view="replay"] main > section[data-view="replay"],
    body[data-active-view="collection"] main > section[data-view="collection"],
    body[data-active-view="discord"] main > section[data-view="discord"],
    body[data-active-view="operations"] main > section[data-view="operations"],
    body[data-active-view="settings"] main > section[data-view="settings"] {
      display: block;
    }
    main > section h2 {
      margin: 0 0 14px;
      color: #f0f3f5;
      font-size: 14px;
      font-weight: 700;
    }
    h3 { color: #dce1e5; font-size: 13px; }
    .grid {
      grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 8px;
    }
    .kv {
      min-height: 72px;
      padding: 10px 11px;
      border: 1px solid var(--line);
      border-left: 3px solid var(--accent);
      border-radius: 4px;
      background: var(--panel-soft);
    }
    .kv span { color: var(--muted); font-size: 10px; }
    .kv strong { color: #e6eaed; font-size: 12px; }
    form,
    form.alert-history-filter,
    form.worker-run-filter,
    form.trend-filter,
    form.backup-evidence-form,
    form.review-packet-verifier-form,
    form.review-packet-comparer-form {
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    }
    form.worker-run-filter {
      grid-template-columns: repeat(3, minmax(0, 1fr));
    }
    form.worker-run-filter > * { min-width: 0; }
    label { color: #99a3ac; font-size: 11px; }
    input, select, textarea {
      min-height: 38px;
      border-color: var(--line-strong);
      border-radius: 5px;
      background: #0b0e10;
      color: var(--text);
      outline: none;
    }
    input:focus, select:focus, textarea:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 2px rgba(66, 211, 164, 0.12);
    }
    input::placeholder, textarea::placeholder { color: #68717a; }
    button {
      border: 1px solid #50c79f;
      border-radius: 5px;
      background: var(--accent);
      color: var(--accent-ink);
      font-size: 12px;
      font-weight: 700;
    }
    button:hover { filter: brightness(1.08); }
    button:disabled { cursor: not-allowed; filter: grayscale(0.7); opacity: 0.55; }
    button.secondary {
      border-color: var(--line-strong);
      background: #20262b;
      color: #d5dbe0;
    }
    button.danger {
      border-color: #bd515b;
      background: #a83f49;
      color: #fff;
    }
    .compact-button { min-height: 34px; padding: 6px 10px; }
    .actions { flex-wrap: wrap; }
    table { background: transparent; }
    th, td {
      border-bottom-color: var(--line);
      color: #cbd2d8;
      font-size: 12px;
    }
    th {
      background: #101316;
      color: #8e99a2;
      font-size: 10px;
    }
    tbody tr:hover td { background: #14191c; }
    .status { color: var(--muted); font-size: 11px; }
    .status-badge,
    .historical-severity {
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      padding: 2px 7px;
      border: 1px solid var(--line-strong);
      border-radius: 4px;
      font-size: 10px;
      font-weight: 700;
      white-space: nowrap;
    }
    .status-badge.success { border-color: #2e725b; background: #143126; color: #75e6bd; }
    .status-badge.warning { border-color: #756226; background: #382f15; color: #f0d479; }
    .status-badge.error { border-color: #7a3039; background: #3a181d; color: #f29aa2; }
    .status-badge.info { border-color: #285a73; background: #142b39; color: #8bd3f5; }
    .historical-severity { color: #89949d; background: #15191c; }
    .dense-card-list {
      display: none;
      gap: 8px;
    }
    .dense-card {
      min-width: 0;
      padding: 11px;
      border: 1px solid var(--line);
      border-radius: 5px;
      background: #0d1114;
    }
    .dense-card-head,
    .dense-card-row {
      min-width: 0;
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 10px;
    }
    .dense-card-head { margin-bottom: 9px; }
    .dense-card-head strong,
    .dense-card-row strong,
    .identifier { overflow-wrap: anywhere; word-break: break-word; }
    .dense-card-row {
      padding: 4px 0;
      color: #cbd2d8;
      font-size: 11px;
    }
    .dense-card-row span { color: var(--muted); }
    .dense-card-actions { margin-top: 9px; display: flex; flex-wrap: wrap; gap: 6px; }
    .identifier { font-family: Consolas, "Courier New", monospace; font-size: 11px; }
    .player-controls,
    .timeline-range,
    .timeline-range input { min-width: 0; }
    .timeline-range input { width: 100%; }
    .detail-panel,
    .timeline-event-detail {
      border-left-color: var(--info);
      background: #0d1114;
    }
    .timeline-event-row,
    .team-member {
      border-color: var(--line);
      background: #0d1114;
      color: var(--text);
    }
    .timeline-event-item.active .timeline-event-row {
      border-color: var(--accent);
      background: #13211d;
    }
    .timeline-event-item.current .timeline-event-row {
      border-color: var(--info);
      box-shadow: inset 3px 0 0 var(--info);
    }
    .team-member.registered {
      border-color: var(--info);
      background: #111d24;
    }
    tr.linked-row td { background: #211d12; }
    .replay-canvas-wrap {
      border-color: var(--line-strong);
      border-radius: 4px;
      background: #060708;
    }
    .alert-state-active,
    .alert-severity-ok { background: #143126; color: #75e6bd; border-color: #2e725b; }
    .alert-state-acknowledged,
    .alert-severity-unknown { background: #20262b; color: #c3cad0; border-color: #46515a; }
    .alert-state-snoozed,
    .alert-severity-warning { background: #382f15; color: #f0d479; border-color: #756226; }
    .alert-state-resolved,
    .alert-severity-info { background: #142b39; color: #8bd3f5; border-color: #285a73; }
    .alert-severity-error { background: #3a181d; color: #f29aa2; border-color: #7a3039; }
    .system-rail { border-left: 1px solid var(--line); }
    .rail-group { padding: 4px 12px 10px; }
    .rail-row {
      min-height: 34px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      border-bottom: 1px solid #1f2428;
    }
    .rail-row span { color: var(--muted); font-size: 10px; }
    .rail-row strong {
      max-width: 132px;
      overflow: hidden;
      color: #cfd5da;
      font-size: 10px;
      text-align: right;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .rail-row strong.ok { color: var(--accent); }
    .rail-row strong.error { color: var(--danger); }
    .rail-row strong.warning { color: var(--warning); }
    .rail-storage {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 7px;
      padding: 10px 12px 14px;
    }
    .rail-storage > div {
      min-width: 0;
      min-height: 62px;
      padding: 9px;
      border: 1px solid var(--line);
      border-radius: 4px;
      background: var(--panel-soft);
    }
    .rail-storage span,
    .rail-storage strong { display: block; }
    .rail-storage span { color: var(--muted); font-size: 9px; }
    .rail-storage strong {
      margin-top: 8px;
      overflow: hidden;
      color: var(--warning);
      font-size: 11px;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .rail-activity {
      margin: 0;
      padding: 12px 14px 20px;
      color: #aeb7be;
      font-size: 11px;
      line-height: 1.55;
      overflow-wrap: anywhere;
    }
    .desktop-only { display: none; }
    body.desktop-host .desktop-only { display: inline-flex; }
    .path-input-row {
      min-width: 0;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 6px;
    }
    .path-picker { min-width: 48px; padding: 7px 9px; }
    .checkbox-line {
      display: flex;
      min-height: 34px;
      align-items: center;
      gap: 7px;
      color: var(--text);
    }
    .checkbox-line input { width: 16px; height: 16px; margin: 0; }
    .match-explorer-pager { margin-top: 10px; }
    .match-explorer-detail {
      min-width: 0;
      margin-top: 18px;
      padding-top: 16px;
      border-top: 1px solid var(--line);
    }
    .match-detail-heading {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 12px;
    }
    .match-detail-heading h3 { margin: 3px 0 0; font-size: 16px; }
    .match-detail-panel { min-width: 0; margin-top: 12px; }
    .match-event-filter { margin-bottom: 10px; }
    .match-explorer-detail table { min-width: 760px; table-layout: auto; }
    .match-explorer-detail td { vertical-align: top; }
    .json-detail {
      min-height: 260px;
      margin: 12px 0 0;
      padding: 12px;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 4px;
      background: #080b0d;
      color: #cbd5dd;
      font: 11px/1.55 Consolas, monospace;
      white-space: pre-wrap;
      word-break: break-word;
    }
    @media (max-width: 1180px) {
      .app-shell { grid-template-columns: 190px minmax(0, 1fr); }
      .system-rail { display: none; }
      .app-header { grid-template-columns: 190px minmax(260px, 1fr) auto; }
    }
    @media (max-width: 820px) {
      body { height: auto; overflow: auto; }
      .app-header {
        grid-template-columns: minmax(0, 1fr) auto;
        align-items: center;
      }
      .command-strip { grid-column: 1 / -1; grid-row: 2; }
      .app-shell { height: auto; display: block; overflow: visible; }
      .side-panel { border-right: 0; border-bottom: 1px solid var(--line); }
      .side-heading, .side-foot { display: none; }
      .side-nav { grid-template-columns: repeat(4, minmax(0, 1fr)); }
      .side-nav button { grid-template-columns: auto; justify-items: center; text-align: center; }
      .side-nav button span { display: none; }
      main { padding: 0 12px 18px; overflow: visible; }
      .workspace-heading { margin: 0 -12px 12px; padding: 14px 12px; }
      .workspace-section-tabs { margin-top: 0; }
      .workspace-section-tabs.visible { position: static; }
      .analysis-player-context { align-items: flex-start; }
      .intelligence-kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .intelligence-metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .section-heading-row { align-items: stretch; flex-direction: column; }
      .section-heading-row button { align-self: flex-start; }
      main > section[data-view] { padding: 13px; }
      .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .trend-table-wrap { display: none; }
      .trend-card-list { display: grid; }
      .dense-table-wrap { display: none; }
      .dense-card-list {
        display: grid;
        max-height: min(60vh, 560px);
        padding-right: 4px;
        overflow-y: auto;
        overscroll-behavior: contain;
        scrollbar-width: thin;
      }
      .match-explorer-list,
      .match-explorer-detail .dense-table-wrap { display: block; }
      .match-detail-heading { flex-direction: column; }
      .match-detail-heading .actions { width: 100%; justify-content: flex-start; }
    }
    @media (max-width: 520px) {
      .app-header { padding: 8px 10px; }
      .brand-mark { width: 32px; height: 32px; }
      .header-meta { gap: 7px; }
      .header-meta strong { font-size: 13px; }
      .command-message { white-space: normal; }
      .side-nav { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .workspace-heading { align-items: flex-start; flex-direction: column; }
      .workspace-section-tabs { margin-right: -12px; padding-right: 12px; }
      .analysis-player-context {
        align-items: stretch;
        flex-direction: column;
      }
      .intelligence-view-toolbar,
      .intelligence-chart-options,
      .intelligence-quality-strip { align-items: stretch; flex-direction: column; }
      .intelligence-chart-options label,
      .intelligence-breakdown-option { width: 100%; min-width: 0; }
      .intelligence-kpi-grid,
      .intelligence-metric-grid,
      .intelligence-list { grid-template-columns: 1fr; }
      .intelligence-definition { grid-template-columns: 1fr; gap: 3px; }
      .grid { grid-template-columns: 1fr; }
      #statusGrid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .query-primary, .filter-grid { grid-template-columns: 1fr; }
      .result-heading { display: grid; }
      .result-row { grid-template-columns: 1fr; }
      .result-row-tail { justify-content: space-between; }
      .result-row-tail p { text-align: left; }
      .loadout-weapons { grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr); }
      .metric-chart-grid { grid-template-columns: 1fr; }
      .metric-chart-row { grid-template-columns: minmax(0, 1fr) auto; }
      .metric-chart-track { grid-column: 1 / -1; grid-row: 2; }
      .trend-chart-overview { grid-template-columns: 1fr; }
      .comparison-primary { grid-template-columns: 1fr; }
      .comparison-bar-row { grid-template-columns: minmax(0, 1fr) auto; }
      .comparison-bar-track { grid-column: 1 / -1; grid-row: 2; }
      .time-insight-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .time-hour-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
      .player-registry-table { min-width: 0; display: block; }
      .player-registry-table thead { display: none; }
      .player-registry-table tbody { display: block; }
      .player-registry-table tr {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 8px;
        padding: 12px 0;
        border-bottom: 1px solid var(--line);
      }
      .player-registry-table tr:last-child { border-bottom: 0; }
      .player-registry-table td {
        display: block;
        min-width: 0;
        padding: 0;
        border: 0;
      }
      .player-registry-table td:first-child,
      .player-registry-table td:last-child { grid-column: 1 / -1; }
      .player-registry-table .registry-state-button { width: 100%; }
      .player-discord-editor-grid { grid-template-columns: 1fr; }
      .recommendation-chart-toolbar { justify-content: stretch; }
      .recommendation-chart-toolbar label { width: 100%; }
      .trend-control-cluster { width: 100%; }
      form,
      form.query-form,
      form.analysis-form,
      form.alert-history-filter,
      form.worker-run-filter,
      form.trend-filter,
      form.backup-evidence-form,
      form.review-packet-verifier-form,
      form.review-packet-comparer-form {
        grid-template-columns: 1fr;
      }
      .player-controls > *,
      .player-controls label,
      .player-controls select,
      .player-controls button { min-width: 0; max-width: 100%; }
    }
  </style>
</head>
<body data-active-view="overview" data-app-release="__PUBG_AI_APP_RELEASE__">
  <header class="app-header">
    <div class="brand-lockup">
      <img class="brand-mark" src="/assets/app-icon.png" alt="PUBG AI">
      <div>
        <h1>PUBG AI</h1>
        <span>로컬 운영</span>
      </div>
    </div>
    <div class="command-strip">
      <span class="live-indicator"><i></i> 로컬 전용</span>
      <span class="command-message" id="banner">localhost 전용 관리 화면</span>
    </div>
    <div class="header-meta">
      <span id="runtimeMode">브라우저 · __PUBG_AI_APP_RELEASE__</span>
      <strong id="kstClock">--:--:--</strong>
    </div>
  </header>
  <div class="app-shell">
    <aside class="side-panel" aria-label="관리 화면 탐색">
      <div class="side-heading">
        <span>관리 메뉴</span>
        <strong>관리 콘솔</strong>
      </div>
      <nav class="side-nav" id="workspaceNav">
        <button type="button" data-view-target="overview"><span>01</span>개요</button>
        <button type="button" data-view-target="players"><span>02</span>플레이어</button>
        <button type="button" data-view-target="replay"><span>03</span>2D 리플레이</button>
        <button type="button" data-view-target="collection"><span>04</span>수집·처리</button>
        <button type="button" data-view-target="discord"><span>05</span>Discord 봇</button>
        <button type="button" data-view-target="operations"><span>06</span>운영·알림</button>
        <button type="button" data-view-target="settings"><span>07</span>설정</button>
      </nav>
      <div class="side-foot">
        <span class="status-dot"></span>
        <div><strong>127.0.0.1</strong><span>외부 접속 차단</span></div>
      </div>
    </aside>
    <main id="workspace">
      <div class="workspace-heading">
        <div>
          <span id="workspaceEyebrow">시스템 개요</span>
          <h2 id="workspaceTitle">운영 개요</h2>
          <p id="workspaceDescription">로컬 데이터 수집과 저장 상태를 한눈에 확인합니다.</p>
        </div>
        <button class="secondary compact-button" type="button" id="refreshWorkspace">새로고침</button>
      </div>
      <nav class="workspace-section-tabs" id="workspaceSections" aria-label="현재 화면 세부 메뉴" role="tablist"></nav>
      <div class="analysis-player-context" id="analysisPlayerContext" aria-live="polite">
        <div class="analysis-player-context-copy">
          <span>분석 대상</span>
          <strong id="analysisPlayerContextName">분석 대상을 선택하세요</strong>
          <small id="analysisPlayerContextMeta">한 번 선택하면 전적·추세·무기·추천·낙하·매치 탭에서 유지됩니다.</small>
        </div>
        <button class="secondary compact-button" id="clearAnalysisPlayer" type="button" disabled>분석 대상 해제</button>
      </div>
    <section id="overview-status" data-view="overview">
      <h2>상태</h2>
      <div class="grid" id="statusGrid"></div>
    </section>
    <section id="storage-settings" data-view="settings">
      <h2>저장 경로 설정</h2>
      <form id="storageSettingsForm">
        <label>원본 매치 저장 경로
          <span class="path-input-row">
            <input name="raw_data_dir" autocomplete="off" required>
            <button class="secondary desktop-only path-picker" type="button" data-path-purpose="raw" data-path-input="raw_data_dir" title="원본 저장 폴더 선택">찾기</button>
          </span>
        </label>
        <label>2D 리플레이 저장 경로
          <span class="path-input-row">
            <input name="replay_data_dir" autocomplete="off" required>
            <button class="secondary desktop-only path-picker" type="button" data-path-purpose="replay" data-path-input="replay_data_dir" title="리플레이 저장 폴더 선택">찾기</button>
          </span>
        </label>
        <label>삭제 백업 저장 경로
          <span class="path-input-row">
            <input name="backup_data_dir" autocomplete="off" required>
            <button class="secondary desktop-only path-picker" type="button" data-path-purpose="backup" data-path-input="backup_data_dir" title="백업 저장 폴더 선택">찾기</button>
          </span>
        </label>
        <label>삭제 격리 저장 경로
          <span class="path-input-row">
            <input name="quarantine_data_dir" autocomplete="off" required>
            <button class="secondary desktop-only path-picker" type="button" data-path-purpose="quarantine" data-path-input="quarantine_data_dir" title="격리 저장 폴더 선택">찾기</button>
          </span>
        </label>
        <label>원본 압축 방식
          <select name="raw_compression">
            <option value="gzip">gzip</option>
            <option value="none">압축 안 함</option>
          </select>
        </label>
        <button type="submit">저장</button>
      </form>
      <div class="status" id="storageSettingsStatus" style="margin-top: 12px;">저장 경로 확인 중</div>
    </section>
    <section id="alerts" data-view="operations">
      <h2>알림 설정</h2>
      <form id="alertSettingsForm" class="alert-settings-form">
        <label>최소 여유 공간 (GB)
          <input name="minimum_free_gb" type="number" min="0" step="0.1" value="50" required>
        </label>
        <label>저장소 알림
          <select name="storage_alerts_enabled">
            <option value="true">사용</option>
            <option value="false">사용 안 함</option>
          </select>
        </label>
        <label>자동 작업 오류 알림
          <select name="worker_error_alerts_enabled">
            <option value="true">사용</option>
            <option value="false">사용 안 함</option>
          </select>
        </label>
        <button type="submit">저장</button>
        <fieldset class="alert-channel-picker">
          <legend>Discord 알림 채널</legend>
          <label>서버
            <select
              id="alertDiscordGuildSelect"
              class="discord-guild-select"
              data-empty-label="서버 선택"
            ><option value="">서버 선택</option></select>
          </label>
          <label>메시지 전송 가능 채널
            <select id="alertDiscordChannelSelect" disabled>
              <option value="">서버 선택 필요</option>
            </select>
          </label>
          <div class="alert-channel-actions">
            <button id="alertDiscordChannelAdd" type="button" disabled>추가</button>
            <button id="alertDiscordChannelsRefresh" class="secondary" type="button">새로고침</button>
          </div>
          <input id="alertDiscordChannelIds" name="discord_channel_ids" type="hidden">
          <div id="alertDiscordChannelSelection" class="alert-channel-selection result-chip-list"></div>
          <div id="alertDiscordChannelsStatus" class="status">선택된 알림 채널 없음</div>
        </fieldset>
      </form>
      <div class="status" id="alertSettingsStatus" style="margin-top: 12px;">알림 상태 확인 중</div>
      <div class="table-scroll" style="margin-top: 12px;"><table>
        <thead>
          <tr>
            <th>발생 위치</th>
            <th>심각도</th>
            <th>제목</th>
            <th>내용</th>
            <th>처리</th>
          </tr>
        </thead>
        <tbody id="alertsBody"></tbody>
      </table></div>
      <h3>알림 이력</h3>
      <form id="alertHistoryFilterForm" class="alert-history-filter">
        <label>발생 위치
          <select name="source">
            <option value="all">전체</option>
            <option value="storage">저장소</option>
            <option value="worker">자동 작업</option>
            <option value="watchlist">밴 플레이어</option>
          </select>
        </label>
        <label>상태
          <select name="state">
            <option value="all">전체</option>
            <option value="current">현재 항목</option>
            <option value="active">활성</option>
            <option value="acknowledged">확인함</option>
            <option value="snoozed">숨김</option>
            <option value="resolved">해결됨</option>
          </select>
        </label>
        <label>심각도
          <select name="severity">
            <option value="all">전체</option>
            <option value="error">오류</option>
            <option value="warning">주의</option>
            <option value="info">정보</option>
            <option value="ok">정상</option>
          </select>
        </label>
        <label>표시 개수
          <select name="limit">
            <option value="20" selected>20개</option>
            <option value="50">50개</option>
            <option value="100">100개</option>
            <option value="200">200개</option>
          </select>
        </label>
        <label>정렬
          <select name="sort">
            <option value="newest" selected>최신순</option>
            <option value="oldest">오래된순</option>
            <option value="severity">심각도 우선</option>
          </select>
        </label>
        <label>검색
          <input name="search" placeholder="제목 또는 내용">
        </label>
        <button type="submit">조회</button>
      </form>
      <div class="actions" style="margin-top: 10px;">
        <button class="secondary" type="button" data-alert-history-preset="current-errors">현재 오류</button>
        <button class="secondary" type="button" data-alert-history-preset="worker-failures">자동 작업 실패</button>
        <button class="secondary" type="button" data-alert-history-preset="storage-pressure">저장 공간 부족</button>
        <button class="secondary" type="button" data-alert-history-preset="watchlist-alerts">밴 플레이어 알림</button>
        <button class="secondary" type="button" data-alert-history-preset="all-history">전체 이력</button>
        <button class="secondary" type="button" id="alertHistoryExport">CSV 내보내기</button>
        <button class="secondary" type="button" id="alertHistoryCopyFilterLink">조회 링크 복사</button>
        <button class="secondary" type="button" id="alertHistoryPrev">이전</button>
        <button class="secondary" type="button" id="alertHistoryNext">다음</button>
      </div>
      <div class="status" id="alertHistoryStatus" style="margin-top: 8px;">알림 이력 확인 중</div>
      <div class="table-scroll dense-table-wrap"><table>
        <thead>
          <tr>
            <th>최근 발생 (KST)</th>
            <th>발생 위치</th>
            <th>제목</th>
            <th>상태</th>
            <th>메모</th>
            <th>내용</th>
            <th>처리</th>
          </tr>
        </thead>
        <tbody id="alertHistoryBody"></tbody>
      </table></div>
      <div class="dense-card-list" id="alertHistoryCards"></div>
      <dialog class="app-dialog" id="alertHistoryDialog" aria-labelledby="alertHistoryDialogTitle">
        <div class="app-dialog-shell">
          <header class="app-dialog-header">
            <h3 id="alertHistoryDialogTitle">알림 상세</h3>
            <button class="secondary app-dialog-close" type="button" id="alertHistoryDialogClose" title="상세 창 닫기" aria-label="알림 상세 창 닫기">×</button>
          </header>
          <div class="app-dialog-content" id="alertHistoryDetail">알림 상세를 불러오는 중입니다.</div>
        </div>
      </dialog>
    </section>
    <section id="collector-settings" data-view="collection">
      <h2>자동 수집 설정</h2>
      <form id="collectorSettingsForm">
        <label>조회 주기 (초)
          <input name="poll_interval_seconds" type="number" min="60" max="300" value="180" required>
        </label>
        <label>주기당 조회 유저
          <input name="cycle_player_limit" type="number" min="1" max="100" value="100" required>
        </label>
        <label>API 조회 묶음 인원
          <input name="player_lookup_chunk_size" type="number" min="1" max="10" value="10" required>
        </label>
        <button type="submit">저장</button>
      </form>
      <div class="status" id="collectorSettingsStatus" style="margin-top: 12px;">수집 설정 확인 중</div>
      <form id="collectorWorkerForm" style="margin-top: 10px;">
        <label>플랫폼 범위
          <select name="shard">
            <option value="">전체</option>
            <option value="steam">steam</option>
            <option value="kakao">kakao</option>
            <option value="psn">psn</option>
            <option value="xbox">xbox</option>
          </select>
        </label>
        <label>주기당 매치 작업
          <input name="match_job_limit" type="number" min="1" max="500" value="10" required>
        </label>
        <label>주기당 텔레메트리 작업
          <input name="telemetry_job_limit" type="number" min="1" max="200" value="5" required>
        </label>
        <button type="submit">자동 수집 시작</button>
        <button class="secondary" type="button" id="collectorWorkerStop">중지</button>
      </form>
      <div class="status" id="collectorWorkerStatus" style="margin-top: 12px;">자동 수집 중지</div>
    </section>
    <section id="web-link-settings" data-view="settings">
      <h2>로컬 상세 링크</h2>
      <form id="webSettingsForm">
        <label>기본 주소
          <input name="local_web_base_url" autocomplete="off" placeholder="http://127.0.0.1:8000">
        </label>
        <button type="submit">저장</button>
      </form>
      <div class="status" id="webSettingsStatus" style="margin-top: 12px;">로컬 링크 확인 중</div>
    </section>
    <section id="display-settings" data-view="settings">
      <h2>표시 설정</h2>
      <form id="displaySettingsForm">
        <label>숫자 표기
          <select name="number_format">
            <option value="grouped">콤마 구분 · 59,452</option>
            <option value="korean_units">한국식 단위 · 5만 9,452</option>
            <option value="plain">구분 없음 · 59452</option>
          </select>
        </label>
        <button type="submit">적용</button>
      </form>
      <div class="status" id="displaySettingsStatus" style="margin-top: 12px;">현재 표기 예시: <strong id="displayNumberPreview">59,452</strong></div>
    </section>
    <section id="discord-bot-manager" data-view="discord">
      <h2>앱 관리 Discord 봇</h2>
      <div class="bot-state-strip" aria-live="polite">
        <div><span>실행 상태</span><strong id="discordBotState">중지</strong></div>
        <div><span>봇 계정</span><strong id="discordBotUser">-</strong></div>
        <div><span>연결 서버</span><strong id="discordBotGuildCount">0개</strong></div>
        <div><span>최근 동기화</span><strong id="discordBotLastSync">-</strong></div>
      </div>
      <form id="discordBotSettingsForm" class="discord-bot-settings-form">
        <label class="switch-field">자동 시작
          <span class="switch-control">
            <input name="auto_start" type="checkbox">
            <span aria-hidden="true"></span>
          </span>
        </label>
        <label>접두사
          <input name="command_prefix" value="!" maxlength="5" autocomplete="off" required>
        </label>
        <button type="submit">설정 저장</button>
      </form>
      <div class="actions bot-runtime-actions">
        <button type="button" id="discordBotStart">시작</button>
        <button class="secondary" type="button" id="discordBotStop">중지</button>
        <button class="secondary" type="button" id="discordBotSyncAll">전체 서버 명령 동기화</button>
        <button class="secondary" type="button" id="discordBotRefreshGuilds">서버 목록 정리</button>
      </div>
      <div class="status" id="discordBotStatus">봇 상태 확인 중</div>

      <h3>보안 키</h3>
      <div class="bot-secret-grid">
        <form id="pubgApiKeyForm">
          <label>PUBG API 키
            <input name="value" type="password" autocomplete="new-password" spellcheck="false" required>
          </label>
          <button type="submit">저장</button>
          <button class="danger" type="button" id="pubgApiKeyClear">삭제</button>
          <span class="status" id="pubgApiKeyStatus">확인 중</span>
        </form>
        <form id="discordTokenForm">
          <label>Discord 봇 토큰
            <input name="value" type="password" autocomplete="new-password" spellcheck="false" required>
          </label>
          <button type="submit">저장</button>
          <button class="danger" type="button" id="discordTokenClear">삭제</button>
          <span class="status" id="discordTokenStatus">확인 중</span>
        </form>
      </div>

      <h3>서버별 명령 공개</h3>
      <form id="discordBotGuildCommandsForm" class="discord-bot-guild-form">
        <label>Discord 서버
          <select name="guild_id" id="discordBotGuildSelect" class="discord-guild-select" data-empty-label="서버 선택" required>
            <option value="">서버 선택</option>
          </select>
        </label>
        <label>명령 찾기
          <input id="discordBotCommandSearch" type="search" autocomplete="off" placeholder="전적, 추천, 알림">
        </label>
        <div class="actions bot-command-actions">
          <button class="secondary" type="button" id="discordBotCommandsAll">전체 선택</button>
          <button class="secondary" type="button" id="discordBotCommandsNone">전체 해제</button>
          <button class="secondary" type="button" id="discordBotCommandsDefault">기본값</button>
        </div>
      </form>
      <div id="discordBotCommandCatalog" class="command-catalog-grid"></div>
      <div class="actions" style="margin-top: 10px;">
        <button type="button" id="discordBotCommandsSave">저장 후 동기화</button>
        <button class="secondary" type="button" id="discordBotCommandsVerify">현재 공개 상태 확인</button>
      </div>
      <div class="status" id="discordBotCommandsStatus" style="margin-top: 10px;">서버를 선택하세요.</div>
    </section>
    <section id="discord-permissions" data-view="discord">
      <h2>앱 봇 사용자 권한</h2>
      <p class="section-note">봇이 참여한 서버와 멤버를 선택하면 Discord ID를 직접 찾을 필요가 없습니다. 닉네임과 ID를 함께 보존합니다.</p>
      <form id="discordGrantForm" class="discord-member-permission-form">
        <label>사용자 소속 서버
          <select name="guild_id" id="discordGrantMemberGuild" class="discord-guild-select" data-empty-label="서버 선택" required>
            <option value="">서버 선택</option>
          </select>
        </label>
        <label>Discord 사용자
          <input name="member_query" id="discordGrantMemberQuery" list="discordGrantMemberOptions" autocomplete="off" placeholder="닉네임 일부 입력" required>
          <input name="user_id" type="hidden">
        </label>
        <label>권한 그룹
          <select name="group" id="discordPermissionGroup" required></select>
        </label>
        <label class="checkbox-field">적용 범위
          <span><input name="global_scope" type="checkbox"> 모든 Discord 서버에 적용</span>
        </label>
        <button type="submit">권한 추가</button>
      </form>
      <datalist id="discordGrantMemberOptions"></datalist>
      <div class="status" id="discordGrantMemberStatus">사용자 소속 서버를 선택하세요.</div>
      <form id="discordAdminForm" class="discord-member-permission-form" style="margin-top: 10px;">
        <label>관리자 소속 서버
          <select name="member_guild_id" id="discordAdminMemberGuild" class="discord-guild-select" data-empty-label="서버 선택" required>
            <option value="">서버 선택</option>
          </select>
        </label>
        <label>전역 관리자
          <input name="member_query" id="discordAdminMemberQuery" list="discordAdminMemberOptions" autocomplete="off" placeholder="닉네임 일부 입력" required>
          <input name="user_id" type="hidden">
        </label>
        <button type="submit">전역 관리자 추가</button>
      </form>
      <datalist id="discordAdminMemberOptions"></datalist>
      <div class="status" id="discordAdminMemberStatus">관리자 소속 서버를 선택하세요.</div>
      <table style="margin-top: 12px;">
        <thead>
          <tr>
            <th>범위</th>
            <th>Discord 사용자</th>
            <th>권한</th>
            <th></th>
          </tr>
        </thead>
        <tbody id="discordPermissionsBody"></tbody>
      </table>
    </section>
    <section id="discord-command-groups" data-view="discord">
      <h2>앱 봇 명령 권한 그룹</h2>
      <form id="discordCommandGroupForm" class="discord-command-group-form">
        <label>사용자 그룹 키
          <input
            name="group"
            autocomplete="off"
            minlength="1"
            maxlength="32"
            pattern="[A-Za-z가-힣][A-Za-z0-9가-힣_-]{0,31}"
            placeholder="예: 전적조회, 운영자, 분석팀"
            required
          >
        </label>
        <label>명령 찾기
          <input id="discordCommandSearch" type="search" autocomplete="off" placeholder="전적, 무기, alert">
        </label>
        <button type="submit">그룹 저장</button>
        <button id="discordCommandGroupReset" class="secondary" type="button">초기화</button>
      </form>
      <div id="discordCommandCatalog" class="command-catalog-grid"></div>
      <div id="discordCommandGroupStatus" class="status" style="margin-top: 10px;">명령 카탈로그 확인 중</div>
      <div class="table-scroll" style="margin-top: 10px;">
        <table class="discord-group-table">
          <thead>
            <tr><th>그룹</th><th>사용 가능한 명령</th><th>할당</th><th></th></tr>
          </thead>
          <tbody id="discordCommandGroupsBody"></tbody>
        </table>
      </div>
      <div class="discord-alias-block">
        <h3>접두사 명령 별칭</h3>
        <form id="discordCommandAliasForm" class="discord-alias-form">
          <label>새 별칭
            <input
              name="alias"
              autocomplete="off"
              maxlength="32"
              pattern="[A-Za-z0-9_가-힣-]{1,32}"
              required
            >
          </label>
          <label>실행할 명령
            <select name="target_command" id="discordCommandAliasTarget" required></select>
          </label>
          <button type="submit">별칭 저장</button>
          <button id="discordCommandAliasReset" class="secondary" type="button">초기화</button>
        </form>
        <div class="table-scroll" style="margin-top: 10px;">
          <table>
            <thead><tr><th>별칭</th><th>실행 명령</th><th></th></tr></thead>
            <tbody id="discordCommandAliasesBody"></tbody>
          </table>
        </div>
      </div>
    </section>
    <section id="discord-scopes" data-view="discord">
      <h2>앱 봇 서버 관리</h2>
      <form id="discordScopeForm">
        <label>설정 대상 서버
          <select name="guild_id" class="discord-guild-select" data-empty-label="서버 선택" required>
            <option value="">서버 선택</option>
          </select>
        </label>
        <label>랭킹 범위
          <select name="scope" id="discordScopeMode">
            <option value="global">전체 서버</option>
            <option value="guild">선택한 서버들</option>
          </select>
        </label>
        <fieldset id="discordRankingGuildSelection" class="discord-ranking-selection" hidden>
          <legend>랭킹에 포함할 서버</legend>
          <div id="discordRankingGuildCheckboxes" class="discord-ranking-guild-grid"></div>
          <div id="discordRankingGuildStatus" class="status" style="margin-top: 8px;">서버를 선택하세요.</div>
        </fieldset>
        <button type="submit">저장</button>
        <button type="button" class="secondary" id="discordScopeReset">초기화</button>
      </form>
      <form id="publicProfileDefaultForm" style="margin-top: 10px;">
        <label>기본 전적 공개 범위
          <select name="public_profile_default">
            <option value="true">공개</option>
            <option value="false">비공개</option>
          </select>
        </label>
        <button type="submit">저장</button>
      </form>
      <table style="margin-top: 12px;">
        <thead>
          <tr>
            <th>서버</th>
            <th>랭킹 범위</th>
            <th></th>
          </tr>
        </thead>
        <tbody id="discordScopesBody"></tbody>
      </table>
    </section>
    <section id="player-registration" data-view="players">
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
        <label>PUBG 계정 ID
          <input name="account_id" autocomplete="off" placeholder="자동 조회">
        </label>
        <label>공개 프로필
          <select name="public_profile">
            <option value="true">공개</option>
            <option value="false">비공개</option>
          </select>
        </label>
        <label>Discord 서버
          <select name="guild_id" class="discord-guild-select" data-empty-label="연결 안 함">
            <option value="">연결 안 함</option>
          </select>
        </label>
        <label>Discord 채널 ID
          <input name="channel_id" autocomplete="off" inputmode="numeric" placeholder="선택 입력">
        </label>
        <button type="submit">등록</button>
      </form>
    </section>
    <section id="registered-players" data-view="players">
      <h2>등록 유저</h2>
      <div class="actions" style="margin-bottom: 10px;">
        <button type="button" onclick="refreshCollection()">최근 매치 수집</button>
        <button class="secondary" type="button" onclick="loadPlayers()">새로고침</button>
      </div>
      <div class="table-scroll">
        <table class="player-registry-table">
          <thead>
            <tr>
              <th>플레이어</th>
              <th>수집 관리</th>
              <th>전적 공개</th>
              <th>Discord 등록 서버</th>
            </tr>
          </thead>
          <tbody id="playersBody"></tbody>
        </table>
      </div>
    </section>
    <section id="watchlist-management" data-view="players">
      <h2>밴 플레이어</h2>
      <form id="watchlistRegisterForm" class="analysis-form">
        <div class="query-primary">
          <label>플랫폼
            <select name="shard"><option value="steam">steam</option><option value="kakao">kakao</option></select>
          </label>
          <label>닉네임
            <input name="player_name" autocomplete="off" placeholder="정확한 PUBG 닉네임" required>
          </label>
          <label>반복 교전 알림 기준
            <input name="engagement_threshold" type="number" min="1" max="100" value="3" required>
          </label>
          <button type="submit">등록</button>
          <button class="secondary" type="button" id="watchlistRegisterReset">초기화</button>
        </div>
        <details class="advanced-filters" open>
          <summary>알림 설정</summary>
          <div class="filter-grid">
            <label class="checkbox-line"><input name="notify_name_change" type="checkbox" checked> 닉네임 변경</label>
            <label class="checkbox-line"><input name="notify_kill" type="checkbox" checked> 등록 유저 처치</label>
            <label class="checkbox-line"><input name="notify_repeated_engagement" type="checkbox" checked> 등록 유저와 반복 교전</label>
            <label>Discord 서버
              <select id="watchlistGuildSelect" class="discord-guild-select" data-empty-label="서버 선택"><option value="">서버 선택</option></select>
            </label>
            <label>알림 채널
              <select id="watchlistChannelSelect" disabled><option value="">서버 선택 필요</option></select>
            </label>
            <div class="actions"><button class="secondary" type="button" id="watchlistChannelAdd" disabled>채널 추가</button></div>
          </div>
          <div id="watchlistChannelSelection" class="selected-channel-list"><span class="result-caption">개별 채널 미지정 · 운영 알림 기본 채널 사용</span></div>
          <div id="watchlistChannelStatus" class="status">Discord 서버를 선택하세요.</div>
        </details>
      </form>
      <div class="actions" style="margin: 12px 0 8px;">
        <input id="watchlistSearch" type="search" autocomplete="off" placeholder="닉네임·이전 닉네임·Account ID 검색">
        <label class="checkbox-line"><input id="watchlistIncludeInactive" type="checkbox" checked> 중지 포함</label>
        <button class="secondary" type="button" id="watchlistReload">새로고침</button>
        <button type="button" id="watchlistScan">닉네임·조우 지금 확인</button>
      </div>
      <div id="watchlistStatus" class="status">밴 플레이어 목록 확인 중</div>
      <div class="table-scroll dense-table-wrap" style="margin-top: 8px;">
        <table>
          <thead><tr><th>플레이어</th><th>상태</th><th>알림</th><th>Discord 채널</th><th>조우</th><th></th></tr></thead>
          <tbody id="watchlistBody"></tbody>
        </table>
      </div>
      <h3>등록 유저와의 최근 조우</h3>
      <div id="watchlistEncounterStatus" class="status">조우 이력 확인 중</div>
      <div class="table-scroll dense-table-wrap" style="margin-top: 8px;">
        <table>
          <thead><tr><th>KST·매치</th><th>밴 플레이어</th><th>등록 유저</th><th>교전·피해</th><th>기절</th><th>처치</th><th></th></tr></thead>
          <tbody id="watchlistEncounterBody"></tbody>
        </table>
      </div>
    </section>
    <dialog id="watchlistEditDialog" class="app-dialog" aria-labelledby="watchlistEditTitle">
      <form id="watchlistEditForm" class="app-dialog-shell">
        <header class="app-dialog-header"><h3 id="watchlistEditTitle">밴 플레이어 설정</h3><button class="secondary app-dialog-close" type="button" id="watchlistEditClose" aria-label="밴 플레이어 설정 창 닫기">×</button></header>
        <div class="app-dialog-content">
          <input name="shard" type="hidden"><input name="account_id" type="hidden">
          <div id="watchlistEditIdentity" class="result-summary"></div>
          <div class="filter-grid">
            <label>감시 상태<select name="active"><option value="true">감시 중</option><option value="false">감시 중지</option></select></label>
            <label>반복 교전 기준<input name="engagement_threshold" type="number" min="1" max="100" required></label>
            <label class="checkbox-line"><input name="notify_name_change" type="checkbox"> 닉네임 변경 알림</label>
            <label class="checkbox-line"><input name="notify_kill" type="checkbox"> 등록 유저 처치 알림</label>
            <label class="checkbox-line"><input name="notify_repeated_engagement" type="checkbox"> 반복 교전 알림</label>
            <label>Discord 서버<select id="watchlistEditGuildSelect" class="discord-guild-select" data-empty-label="서버 선택"><option value="">서버 선택</option></select></label>
            <label>알림 채널<select id="watchlistEditChannelSelect" disabled><option value="">서버 선택 필요</option></select></label>
            <div class="actions"><button class="secondary" type="button" id="watchlistEditChannelAdd" disabled>채널 추가</button></div>
          </div>
          <div id="watchlistEditChannelSelection" class="selected-channel-list"></div>
          <div id="watchlistEditChannelStatus" class="status"></div>
        </div>
        <footer class="app-dialog-footer"><button type="submit">저장</button><button class="secondary" type="button" id="watchlistEditCancel">취소</button></footer>
      </form>
    </dialog>
    <section id="data-deletions" data-view="operations">
      <h2>데이터 삭제 검토</h2>
      <form id="dataDeletionFilterForm">
        <label>상태
          <select name="status">
            <option value="pending">대기</option>
            <option value="approved">승인</option>
            <option value="all">전체</option>
            <option value="rejected">거절</option>
            <option value="cancelled">취소</option>
            <option value="expired">만료</option>
          </select>
        </label>
        <label>로컬 검토자
          <input name="actor_id" autocomplete="off" value="local-manager" required>
        </label>
        <label>감사 메모
          <input name="note" autocomplete="off" maxlength="1000">
        </label>
        <button type="submit">새로고침</button>
      </form>
      <div class="status" id="dataDeletionStatus" style="margin: 10px 0;">
        승인은 권한 기록만 남기며 실제 삭제 실행은 비활성화되어 있습니다.
      </div>
      <div class="backup-builder-contract" id="exportedReviewPacketVerifier">
        <h3>내보낸 검토 패킷 검증</h3>
        <div class="status">엄격한 JSON 및 정규 해시 검증 / 업로드 원문 미보존 / 권한 부여·실행 비활성</div>
        <form class="review-packet-verifier-form" id="exportedReviewPacketVerifierForm">
          <label>패킷 JSON
            <input name="packet_file" type="file" accept="application/json,.json" required>
          </label>
          <label class="checkbox-field">
            <input name="cross_check_database" type="checkbox" checked>
            현재 MySQL 체인 대조
          </label>
          <button class="secondary" type="submit">패킷 검증</button>
        </form>
        <div class="status" id="exportedReviewPacketVerifierStatus">선택한 패킷이 없습니다.</div>
        <div class="review-packet-verifier-result" id="exportedReviewPacketVerifierResult"></div>
      </div>
      <div class="backup-builder-contract" id="exportedReviewPacketComparer">
        <h3>검토 패킷 비교</h3>
        <div class="status">비교 방향: 기준→후보 / 비교 결과 미보존 / 권한 부여·실행 비활성</div>
        <form class="review-packet-comparer-form" id="exportedReviewPacketComparerForm">
          <label>기준 JSON
            <input name="baseline_packet_file" type="file" accept="application/json,.json" required>
          </label>
          <label>후보 JSON
            <input name="candidate_packet_file" type="file" accept="application/json,.json" required>
          </label>
          <label class="checkbox-field">
            <input name="cross_check_database" type="checkbox" checked>
            두 패킷 모두 현재 MySQL 체인과 대조
          </label>
          <button class="secondary" type="submit">패킷 비교</button>
        </form>
        <div class="status" id="exportedReviewPacketComparerStatus">선택한 패킷 쌍이 없습니다.</div>
        <div class="review-packet-comparer-result" id="exportedReviewPacketComparerResult"></div>
      </div>
      <div class="table-scroll">
      <table class="deletion-request-table">
        <thead>
          <tr>
            <th>ID</th>
            <th>대상</th>
            <th>범위</th>
            <th>상태</th>
            <th>요청 시각 (KST)</th>
            <th>만료 시각 (KST)</th>
            <th>작업</th>
          </tr>
        </thead>
        <tbody id="dataDeletionBody"></tbody>
      </table>
      </div>
      <div class="detail-panel" id="dataDeletionDetail">
        요청을 선택하면 감사 이력과 읽기 전용 영향 미리보기를 확인할 수 있습니다.
      </div>
    </section>
    <datalist id="registeredPlayerOptions"></datalist>
    <section id="intelligence-analysis" data-view="players">
      <h2>종합 분석</h2>
      <form id="intelligenceForm" class="analysis-form">
        <div class="query-primary">
          <label>플랫폼
            <select name="shard"><option value="steam">steam</option><option value="kakao">kakao</option></select>
          </label>
          <label>등록 유저
            <input class="registered-player-input" name="target" list="registeredPlayerOptions" autocomplete="off" placeholder="닉네임 일부 입력" required>
          </label>
          <button type="submit">분석</button>
          <button class="secondary" type="button" data-reset-analysis-form="intelligenceForm">필터 초기화</button>
        </div>
        <details class="advanced-filters">
          <summary>상세 필터</summary>
          <div class="filter-grid">
            <label>맵<select name="map_name" data-catalog-facet="maps"><option value="">전체</option></select></label>
            <label>게임 모드<select name="game_mode" data-catalog-facet="game_modes"><option value="">전체</option></select></label>
            <label>팀 모드<select name="team_mode" data-catalog-facet="team_modes"><option value="">전체</option></select></label>
            <label>시점<select name="perspective" data-catalog-facet="perspectives"><option value="">전체</option></select></label>
            <label>매치 유형<select name="match_type" data-catalog-facet="match_types"><option value="">전체</option></select></label>
            <label>시즌 상태<select name="season_state" data-catalog-facet="season_states"><option value="">전체</option></select></label>
            <label>커스텀<select name="is_custom_match"><option value="">전체</option><option value="false">일반</option><option value="true">커스텀</option></select></label>
            <label>연도<select name="year" data-catalog-facet="years"><option value="">전체</option></select></label>
            <label>분기<select name="quarter"><option value="">전체</option><option value="1">1분기</option><option value="2">2분기</option><option value="3">3분기</option><option value="4">4분기</option></select></label>
            <label>월<select name="month"><option value="">전체</option><option value="1">1월</option><option value="2">2월</option><option value="3">3월</option><option value="4">4월</option><option value="5">5월</option><option value="6">6월</option><option value="7">7월</option><option value="8">8월</option><option value="9">9월</option><option value="10">10월</option><option value="11">11월</option><option value="12">12월</option></select></label>
            <label>특정 일자 (KST)<input name="exact_date_kst" type="date"></label>
            <label>시간대 (KST)<select name="hour"><option value="">전체</option></select></label>
            <label>시작일 (KST)<input name="from_date_kst" type="date"></label>
            <label>종료일 (KST)<input name="to_date_kst" type="date"></label>
          </div>
        </details>
      </form>
      <div class="intelligence-view-toolbar" id="intelligenceViewControls" hidden>
        <div class="segmented-control" role="group" aria-label="종합 분석 보기">
          <button type="button" class="active" data-intelligence-view="overview">핵심 요약</button>
          <button type="button" data-intelligence-view="trends">변화 그래프</button>
          <button type="button" data-intelligence-view="breakdowns">조건 비교</button>
          <button type="button" data-intelligence-view="evidence">원본·정의</button>
        </div>
        <div class="intelligence-chart-options" id="intelligenceChartOptions" hidden>
          <label id="intelligenceGranularityOption">집계
            <select id="intelligenceTrendGranularity"><option value="daily">일자</option><option value="monthly">월</option></select>
          </label>
          <label>지표
            <select id="intelligenceTrendMetric">
              <option value="win_rate">치킨율</option>
              <option value="fight_win_rate">교전 승리율</option>
              <option value="accuracy">명중률</option>
              <option value="headshot_hit_rate">헤드샷 명중 비율</option>
              <option value="avg_damage_dealt">평균 준 피해</option>
              <option value="avg_fights_per_match">경기당 교전</option>
              <option value="avg_survival_seconds">평균 생존</option>
              <option value="avg_heal_amount">경기당 총 체력 회복량</option>
              <option value="avg_revives_caused">경기당 팀원 부활</option>
              <option value="avg_throwable_uses">경기당 투척물 사용</option>
            </select>
          </label>
        </div>
        <label class="intelligence-breakdown-option" id="intelligenceBreakdownOption" hidden>비교 기준
          <select id="intelligenceBreakdownDimension">
            <option value="maps">맵</option><option value="team_modes">팀 모드</option>
            <option value="game_modes">게임 모드</option><option value="perspectives">시점</option>
            <option value="match_types">매치 유형</option><option value="hours">시간대</option>
            <option value="weekdays">요일</option>
          </select>
        </label>
      </div>
      <div class="status intelligence-status" id="intelligenceBody" style="margin-top: 12px;">분석 대기 중</div>
    </section>
    <section id="profile-lookup" data-view="players">
      <h2>전적 조회</h2>
      <form id="profileForm" class="query-form">
        <label>플랫폼
          <select name="shard">
            <option value="steam">steam</option>
            <option value="kakao">kakao</option>
          </select>
        </label>
        <label>등록 유저
          <input class="registered-player-input" name="target" list="registeredPlayerOptions" autocomplete="off" placeholder="닉네임 일부 입력" required>
        </label>
        <button type="submit">조회</button>
        <button class="secondary" type="button" data-reset-analysis-form="profileForm">결과 초기화</button>
      </form>
      <div class="status" id="profileBody" style="margin-top: 12px;">조회 대기 중</div>
    </section>
    <section id="trend-lookup" data-view="players">
      <h2>상세 성과 및 KST 추세</h2>
      <form id="trendForm" class="analysis-form">
        <div class="query-primary">
          <label>플랫폼
            <select name="shard"><option value="steam">steam</option><option value="kakao">kakao</option></select>
          </label>
          <label>등록 유저<input class="registered-player-input" name="target" list="registeredPlayerOptions" autocomplete="off" placeholder="닉네임 일부 입력" required></label>
          <label>집계 기준
            <select name="granularity">
              <option value="hour">시간대</option><option value="date" selected>일자</option>
              <option value="week">ISO 주차</option><option value="month">월</option>
              <option value="quarter">분기</option><option value="year">연도</option>
              <option value="map">맵</option><option value="weapon">무기</option><option value="game_mode">게임 모드</option>
              <option value="team_mode">팀 모드</option><option value="perspective">시점</option>
              <option value="match_type">매치 유형</option><option value="season_state">시즌 상태</option>
            </select>
          </label>
          <button type="submit">조회</button>
          <button class="secondary" type="button" data-reset-analysis-form="trendForm">필터 초기화</button>
        </div>
        <details class="advanced-filters">
          <summary>상세 필터</summary>
          <div class="filter-grid">
            <label>팀 모드
              <select name="team_mode"><option value="">전체</option><option value="solo">솔로</option><option value="duo">듀오</option><option value="squad">스쿼드</option><option value="unknown">알 수 없음</option></select>
            </label>
            <label>시점<select name="perspective"><option value="">전체</option><option value="fpp">1인칭</option><option value="tpp">3인칭</option><option value="unknown">알 수 없음</option></select></label>
            <label>게임 모드<select name="game_mode" data-catalog-facet="game_modes"><option value="">전체</option></select></label>
            <label>매치 유형<select name="match_type" data-catalog-facet="match_types"><option value="">전체</option></select></label>
            <label>맵<select name="map_name" data-catalog-facet="maps"><option value="">전체</option></select></label>
            <label>시즌 상태<select name="season_state" data-catalog-facet="season_states"><option value="">전체</option></select></label>
            <label>커스텀<select name="is_custom_match"><option value="">전체</option><option value="false">일반</option><option value="true">커스텀</option></select></label>
            <label>연도<select name="year" data-catalog-facet="years"><option value="">전체</option></select></label>
            <label>분기<select name="quarter"><option value="">전체</option><option value="1">1분기</option><option value="2">2분기</option><option value="3">3분기</option><option value="4">4분기</option></select></label>
            <label>월<select name="month"><option value="">전체</option><option value="1">1월</option><option value="2">2월</option><option value="3">3월</option><option value="4">4월</option><option value="5">5월</option><option value="6">6월</option><option value="7">7월</option><option value="8">8월</option><option value="9">9월</option><option value="10">10월</option><option value="11">11월</option><option value="12">12월</option></select></label>
            <label>특정 일자 (KST)<input name="exact_date_kst" type="date"></label>
            <label>시간대 (KST)<select name="hour"><option value="">전체</option></select></label>
            <label>시작일 (KST)<input name="from_date_kst" type="date"></label>
            <label>종료일 (KST)<input name="to_date_kst" type="date"></label>
            <label>최대 구간<input name="bucket_limit" type="number" min="1" max="500" value="120" required></label>
          </div>
        </details>
      </form>
      <div class="status" id="trendSummary" style="margin-top: 12px;">조회 대기 중</div>
      <div id="trendViewControls" class="recommendation-chart-toolbar" hidden>
        <div class="trend-control-cluster">
          <div class="recommendation-view-switch" role="group" aria-label="추세 보기 방식">
            <button class="secondary active" type="button" data-trend-view="table">표</button>
            <button class="secondary" type="button" data-trend-view="chart">그래프</button>
          </div>
          <div class="recommendation-view-switch" role="group" aria-label="시간 집계 빠른 선택">
            <button class="secondary" type="button" data-trend-granularity="date">일</button>
            <button class="secondary" type="button" data-trend-granularity="week">주</button>
            <button class="secondary" type="button" data-trend-granularity="month">월</button>
          </div>
        </div>
        <label>그래프 지표
          <select id="trendChartMetric">
            <optgroup label="성과">
              <option value="win_rate">치킨 승률</option>
              <option value="fight_win_rate">교전 승리 확률</option>
              <option value="kda">KDA</option>
              <option value="avg_kills">경기당 평균 킬</option>
              <option value="avg_assists">경기당 평균 어시스트</option>
              <option value="avg_dbnos_caused">경기당 평균 기절</option>
              <option value="avg_deaths">경기당 평균 사망</option>
              <option value="avg_fights_per_match">경기당 평균 교전</option>
              <option value="avg_damage_dealt">평균 준 피해</option>
              <option value="avg_damage_taken">평균 받은 피해</option>
            </optgroup>
            <optgroup label="사격">
              <option value="accuracy">명중 확률</option>
              <option value="headshot_hit_rate">헤드샷 명중 확률</option>
              <option value="headshot_kill_rate">헤드샷 킬 비율</option>
              <option value="hit_head">머리 명중 비율</option>
              <option value="hit_neck">목 명중 비율</option>
              <option value="hit_torso">몸통 명중 비율</option>
              <option value="hit_pelvis">골반 명중 비율</option>
              <option value="hit_arm">팔 명중 비율</option>
              <option value="hit_leg">다리 명중 비율</option>
            </optgroup>
            <optgroup label="피격·생존">
              <option value="taken_head">머리 피격 비율</option>
              <option value="taken_neck">목 피격 비율</option>
              <option value="taken_torso">몸통 피격 비율</option>
              <option value="taken_pelvis">골반 피격 비율</option>
              <option value="taken_arm">팔 피격 비율</option>
              <option value="taken_leg">다리 피격 비율</option>
              <option value="avg_dbnos_taken">경기당 당한 기절</option>
              <option value="avg_survival_seconds">평균 생존시간</option>
              <option value="avg_movement_distance_m">평균 이동거리</option>
            </optgroup>
          </select>
        </label>
      </div>
      <div id="trendChartPanel" class="metric-chart" hidden></div>
      <div class="trend-card-list" id="trendCards"></div>
      <div class="table-scroll trend-table-wrap" id="trendTableWrap" style="margin-top: 10px;">
        <table class="trend-table">
          <thead><tr><th>구간</th><th>경기 / 치킨</th><th>승률</th><th>K/D/A · KDA</th><th>경기당 킬 / 기절</th><th>평균 딜 / 받은 딜</th><th>명중 지표</th><th>헤드샷 명중</th><th>교전</th><th>기절 +/-</th><th>평균 생존</th></tr></thead>
          <tbody id="trendBody"><tr><td colspan="11">조회 대기 중</td></tr></tbody>
        </table>
      </div>
    </section>
    <section id="time-analysis" data-view="players">
      <h2>KST 시간대 분석</h2>
      <form id="timeInsightForm" class="analysis-form">
        <div class="query-primary">
          <label>플랫폼<select name="shard"><option value="steam">steam</option><option value="kakao">kakao</option></select></label>
          <label>등록 유저<input class="registered-player-input" name="target" list="registeredPlayerOptions" autocomplete="off" placeholder="닉네임 일부 입력" required></label>
          <label>그래프 지표
            <select name="metric">
              <option value="match_count">플레이 경기</option>
              <option value="wins">치킨 횟수</option>
              <option value="win_rate">치킨 승률</option>
              <option value="fight_win_rate">교전 승률</option>
              <option value="accuracy">명중률</option>
              <option value="headshot_hit_rate">헤드샷 명중률</option>
              <option value="avg_damage_dealt">평균 피해</option>
            </select>
          </label>
          <button type="submit">분석</button>
          <button class="secondary" type="button" data-reset-analysis-form="timeInsightForm">필터 초기화</button>
        </div>
        <details class="advanced-filters">
          <summary>상세 필터</summary>
          <div class="filter-grid">
            <label>맵<select name="map_name" data-catalog-facet="maps"><option value="">전체</option></select></label>
            <label>게임 모드<select name="game_mode" data-catalog-facet="game_modes"><option value="">전체</option></select></label>
            <label>팀 모드<select name="team_mode" data-catalog-facet="team_modes"><option value="">전체</option></select></label>
            <label>시점<select name="perspective" data-catalog-facet="perspectives"><option value="">전체</option></select></label>
            <label>매치 유형<select name="match_type" data-catalog-facet="match_types"><option value="">전체</option></select></label>
            <label>시즌 상태<select name="season_state" data-catalog-facet="season_states"><option value="">전체</option></select></label>
            <label>시작일 (KST)<input name="from_date_kst" type="date"></label>
            <label>종료일 (KST)<input name="to_date_kst" type="date"></label>
          </div>
        </details>
      </form>
      <div class="status" id="timeInsightBody" style="margin-top: 12px;">분석 대기 중</div>
    </section>
    <section id="comparison-analysis" data-view="players">
      <h2>상세 비교</h2>
      <form id="comparisonForm" class="analysis-form">
        <div class="query-primary comparison-primary">
          <label>비교 유형
            <select name="comparison_type">
              <option value="player">유저 비교</option>
              <option value="weapon">무기 비교</option>
              <option value="map">맵 비교</option>
              <option value="game_mode">게임 모드 비교</option>
              <option value="team_mode">팀 모드 비교</option>
              <option value="perspective">시점 비교</option>
              <option value="match_type">매치 유형 비교</option>
            </select>
          </label>
          <label>플랫폼<select name="shard"><option value="steam">steam</option><option value="kakao">kakao</option></select></label>
          <label data-comparison-player-field>기준 등록 유저<input class="registered-player-input" name="target" list="registeredPlayerOptions" autocomplete="off" placeholder="닉네임 일부 입력"></label>
          <label>대표 지표
            <select name="metric">
              <option value="win_rate">승률</option>
              <option value="kda">KDA</option>
              <option value="avg_kills">경기당 킬</option>
              <option value="avg_assists">경기당 어시스트</option>
              <option value="avg_dbnos_caused">경기당 기절</option>
              <option value="avg_dbnos_taken">경기당 당한 기절</option>
              <option value="avg_deaths">경기당 사망</option>
              <option value="avg_damage_dealt">평균 피해</option>
              <option value="avg_damage_taken">평균 받은 피해</option>
              <option value="damage_ratio">가한/받은 피해 비율</option>
              <option value="accuracy">명중률</option>
              <option value="headshot_hit_rate">헤드샷 명중률</option>
              <option value="headshot_kill_rate">헤드샷 킬 비율</option>
              <option value="fight_win_rate">교전 승률</option>
              <option value="avg_fights_per_match">경기당 교전</option>
              <option value="avg_survival_seconds">평균 생존</option>
              <option value="avg_movement_distance_m">평균 이동</option>
            </select>
          </label>
          <label>추세 집계
            <select name="trend_granularity"><option value="date">일자</option><option value="week">주</option><option value="month" selected>월</option></select>
          </label>
          <button type="submit">비교</button>
          <button class="secondary" type="button" id="comparisonReset">초기화</button>
        </div>
        <fieldset class="comparison-picker">
          <legend>비교 대상 <span id="comparisonSelectionCount">0/5</span></legend>
          <div class="comparison-item-grid" id="comparisonItemPicker"><span class="status">유형과 유저를 선택하세요.</span></div>
        </fieldset>
        <details class="advanced-filters">
          <summary>공통 상세 필터</summary>
          <div class="filter-grid">
            <label>맵<select name="map_name" data-catalog-facet="maps"><option value="">전체</option></select></label>
            <label>게임 모드<select name="game_mode" data-catalog-facet="game_modes"><option value="">전체</option></select></label>
            <label>팀 모드<select name="team_mode" data-catalog-facet="team_modes"><option value="">전체</option></select></label>
            <label>시점<select name="perspective" data-catalog-facet="perspectives"><option value="">전체</option></select></label>
            <label>매치 유형<select name="match_type" data-catalog-facet="match_types"><option value="">전체</option></select></label>
            <label>시즌 상태<select name="season_state" data-catalog-facet="season_states"><option value="">전체</option></select></label>
            <label>시작일 (KST)<input name="from_date_kst" type="date"></label>
            <label>종료일 (KST)<input name="to_date_kst" type="date"></label>
          </div>
        </details>
      </form>
      <div class="segmented-control comparison-view-controls" id="comparisonViewControls" role="group" aria-label="비교 결과 보기">
        <button type="button" data-comparison-view="chart" class="active">그래프</button>
        <button type="button" data-comparison-view="table">표</button>
        <button type="button" data-comparison-view="trend">일·주·월 추세</button>
      </div>
      <div class="status" id="comparisonBody" style="margin-top: 12px;">비교 대기 중</div>
    </section>
    <section id="weapon-lookup" data-view="players">
      <h2>무기 조회</h2>
      <form id="weaponForm" class="analysis-form">
        <div class="query-primary">
          <label>플랫폼<select name="shard"><option value="steam">steam</option><option value="kakao">kakao</option></select></label>
          <label>등록 유저<input class="registered-player-input" name="target" list="registeredPlayerOptions" autocomplete="off" placeholder="닉네임 일부 입력" required></label>
          <label>무기<select name="weapon" required><option value="">유저를 먼저 선택하세요</option></select></label>
          <button type="submit">조회</button>
          <button class="secondary" type="button" data-reset-analysis-form="weaponForm">필터 초기화</button>
        </div>
        <details class="advanced-filters">
          <summary>상세 필터</summary>
          <div class="filter-grid">
            <label>맵<select name="map_name" data-catalog-facet="maps"><option value="">전체</option></select></label>
            <label>게임 모드<select name="game_mode" data-catalog-facet="game_modes"><option value="">전체</option></select></label>
            <label>팀 모드<select name="team_mode" data-catalog-facet="team_modes"><option value="">전체</option></select></label>
            <label>시점<select name="perspective" data-catalog-facet="perspectives"><option value="">전체</option></select></label>
            <label>매치 유형<select name="match_type" data-catalog-facet="match_types"><option value="">전체</option></select></label>
            <label>시즌 상태<select name="season_state" data-catalog-facet="season_states"><option value="">전체</option></select></label>
            <label>연도<select name="year" data-catalog-facet="years"><option value="">전체</option></select></label>
            <label>분기<select name="quarter"><option value="">전체</option><option value="1">1분기</option><option value="2">2분기</option><option value="3">3분기</option><option value="4">4분기</option></select></label>
            <label>월<select name="month"><option value="">전체</option><option value="1">1월</option><option value="2">2월</option><option value="3">3월</option><option value="4">4월</option><option value="5">5월</option><option value="6">6월</option><option value="7">7월</option><option value="8">8월</option><option value="9">9월</option><option value="10">10월</option><option value="11">11월</option><option value="12">12월</option></select></label>
            <label>특정 일자 (KST)<input name="exact_date_kst" type="date"></label>
            <label>시간대 (KST)<select name="hour"><option value="">전체</option></select></label>
            <label>커스텀<select name="is_custom_match"><option value="">전체</option><option value="false">일반</option><option value="true">커스텀</option></select></label>
            <label>시작일 (KST)<input name="from_date_kst" type="date"></label>
            <label>종료일 (KST)<input name="to_date_kst" type="date"></label>
          </div>
        </details>
      </form>
      <div class="status" id="weaponBody" style="margin-top: 12px;">조회 대기 중</div>
    </section>
    <section id="recommendation-lookup" data-view="players">
      <h2>추천 조회</h2>
      <form id="recommendationForm" class="query-form">
        <label>플랫폼
          <select name="shard">
            <option value="steam">steam</option>
            <option value="kakao">kakao</option>
          </select>
        </label>
        <label>등록 유저
          <input class="registered-player-input" name="target" list="registeredPlayerOptions" autocomplete="off" placeholder="닉네임 일부 입력" required>
        </label>
        <label>추천 최소 표본 경기
          <input name="min_matches" type="number" min="1" step="1" value="1" inputmode="numeric" title="무기·파츠 추천에 포함할 최소 경기 수">
        </label>
        <button type="submit">조회</button>
        <button class="secondary" type="button" data-reset-analysis-form="recommendationForm">필터 초기화</button>
      </form>
      <div class="status" id="recommendationBody" style="margin-top: 12px;">조회 대기 중</div>
    </section>
    <section id="landing-analysis" data-view="players">
      <h2>낙하 지역 분석</h2>
      <form id="dropZoneForm" class="analysis-form">
        <div class="query-primary">
          <label>플랫폼
            <select name="shard"><option value="steam">steam</option><option value="kakao">kakao</option></select>
          </label>
          <label>등록 유저
            <input class="registered-player-input" name="target" list="registeredPlayerOptions" autocomplete="off" placeholder="닉네임 일부 입력" required>
          </label>
          <label>최소 착지 경기
            <input name="min_matches" type="number" min="1" step="1" value="1" inputmode="numeric">
          </label>
          <label>맵
            <select name="map_filter" data-catalog-facet="maps"><option value="">전체 맵</option></select>
          </label>
          <button type="submit">조회</button>
          <button class="secondary" type="button" data-reset-analysis-form="dropZoneForm">필터 초기화</button>
        </div>
        <details class="advanced-filters">
          <summary>표시 옵션</summary>
          <div class="filter-grid">
            <label>지역 검색
              <input name="region_search" autocomplete="off" placeholder="지역명 일부 입력">
            </label>
            <label>지역 정렬
              <select name="sort_metric">
                <option value="landings" selected>착지 횟수</option>
                <option value="win_rate">승률</option>
                <option value="avg_kills">평균 킬</option>
                <option value="avg_damage">평균 피해</option>
              </select>
            </label>
            <label>그래프 지역 수
              <select name="chart_limit">
                <option value="10">10개</option>
                <option value="20" selected>20개</option>
                <option value="50">50개</option>
                <option value="100">100개</option>
                <option value="500">전체</option>
              </select>
            </label>
          </div>
        </details>
      </form>
      <div class="status" id="dropZoneBody" style="margin-top: 12px;">조회 대기 중</div>
    </section>
    <section id="map-region-lookup" data-view="replay">
      <h2>맵 좌표 지역 확인</h2>
      <form id="mapRegionForm">
        <label>맵
          <select name="map_name">
            <option value="Baltic_Main">에란겔 리마스터</option>
            <option value="Erangel_Main">에란겔</option>
            <option value="Desert_Main">미라마</option>
            <option value="DihorOtok_Main">비켄디</option>
            <option value="Savage_Main">사녹</option>
            <option value="Summerland_Main">카라킨</option>
            <option value="Tiger_Main">태이고</option>
            <option value="Chimera_Main">파라모</option>
            <option value="Neon_Main">론도</option>
            <option value="Range_Main">캠프 자칼</option>
            <option value="Kiki_Main">데스턴</option>
            <option value="Heaven_Main">헤이븐</option>
          </select>
        </label>
        <label>X (cm)<input name="x_cm" type="number" min="0" step="0.01" required></label>
        <label>Y (cm)<input name="y_cm" type="number" min="0" step="0.01" required></label>
        <button type="submit">확인</button>
      </form>
      <div class="status" id="mapRegionBody" style="margin-top: 12px;">조회 대기 중</div>
    </section>
    <section id="match-lookup" data-view="players">
      <h2>매치 조회</h2>
      <form id="matchForm" class="query-form">
        <label>플랫폼
          <select name="shard">
            <option value="steam">steam</option>
            <option value="kakao">kakao</option>
          </select>
        </label>
        <label>등록 유저
          <input class="registered-player-input" name="target" list="registeredPlayerOptions" autocomplete="off" placeholder="닉네임 일부 입력" required>
        </label>
        <label>매치 조건검색
          <input name="match_search" autocomplete="off" placeholder="날짜, 맵, 모드, 등수, 킬">
        </label>
        <label>매치
          <select name="match_id" required><option value="">유저를 먼저 선택하세요</option></select>
        </label>
        <button type="submit">조회</button>
        <button class="secondary" type="button" data-reset-analysis-form="matchForm">필터 초기화</button>
      </form>
      <div class="status" id="matchBody" style="margin-top: 12px;">조회 대기 중</div>
    </section>
    <section id="match-explorer" data-view="players">
      <h2>매치 상세 조회</h2>
      <form id="matchExplorerForm" class="analysis-form">
        <div class="query-primary">
          <label>플랫폼
            <select name="shard"><option value="">전체</option><option value="steam">steam</option><option value="kakao">kakao</option></select>
          </label>
          <label>매치·참가자 검색
            <input name="search" type="search" autocomplete="off" placeholder="닉네임, 매치, 맵, 모드">
          </label>
          <label>표시 수
            <select name="limit"><option value="25">25개</option><option value="50" selected>50개</option><option value="100">100개</option><option value="200">200개</option></select>
          </label>
          <label class="checkbox-line"><input name="telemetry_only" type="checkbox" checked> 2D·이벤트 원본 저장 완료만</label>
          <button type="submit">조회</button>
          <button class="secondary" type="button" id="matchExplorerReset">초기화</button>
        </div>
        <details class="advanced-filters">
          <summary>상세 필터</summary>
          <div class="filter-grid">
            <label>맵<select name="map_name" data-catalog-facet="maps"><option value="">전체</option></select></label>
            <label>게임 모드<select name="game_mode" data-catalog-facet="game_modes"><option value="">전체</option></select></label>
            <label>매치 유형<select name="match_type" data-catalog-facet="match_types"><option value="">전체</option></select></label>
            <label>시작일 (KST)<input name="created_from_kst" type="date"></label>
            <label>종료일 (KST)<input name="created_to_kst" type="date"></label>
          </div>
        </details>
      </form>
      <div class="status" id="matchExplorerStatus" style="margin-top: 12px;">저장된 매치 조회 대기 중</div>
      <div class="table-scroll dense-table-wrap match-explorer-list" style="margin-top: 10px;">
        <table>
          <thead><tr><th>KST</th><th>맵·모드</th><th>인원</th><th>등록 유저</th><th>원본</th><th></th></tr></thead>
          <tbody id="matchExplorerBody"></tbody>
        </table>
      </div>
      <div class="actions match-explorer-pager">
        <button class="secondary" type="button" id="matchExplorerPrev">이전</button>
        <button class="secondary" type="button" id="matchExplorerNext">다음</button>
      </div>

      <div id="matchExplorerDetail" class="match-explorer-detail" hidden>
        <div class="match-detail-heading">
          <div><span class="result-caption">선택한 매치</span><h3 id="matchExplorerDetailTitle">-</h3></div>
          <div class="actions">
            <button class="secondary" type="button" id="matchExplorerReplay">전체 참가자 2D 재생</button>
            <button class="secondary" type="button" id="matchExplorerDetailClose">닫기</button>
          </div>
        </div>
        <div id="matchExplorerSummary"></div>
        <div class="segmented-control" id="matchExplorerDetailViews" role="group" aria-label="매치 상세 보기">
          <button type="button" class="active" data-match-detail-view="participants">참가자</button>
          <button type="button" data-match-detail-view="teams">팀</button>
          <button type="button" data-match-detail-view="events">이벤트</button>
        </div>
        <div id="matchExplorerParticipants" class="match-detail-panel"></div>
        <div id="matchExplorerTeams" class="match-detail-panel" hidden></div>
        <div id="matchExplorerEvents" class="match-detail-panel" hidden>
          <form id="matchEventFilterForm" class="query-form match-event-filter">
            <label>종류
              <select name="domain">
                <option value="">전체</option><option value="combat" selected>전투</option><option value="loot">아이템 획득·사용</option><option value="loadout">장비·파츠</option><option value="support">회복·부활</option><option value="utility">투척·특수 사용</option><option value="mobility">차량·이동</option><option value="vehicle">차량 피해</option><option value="movement">위치·착지</option><option value="map">지도·페이즈</option><option value="match">매치</option><option value="environment">환경</option><option value="participant">참가자</option><option value="session">접속</option><option value="unclassified">미분류</option>
              </select>
            </label>
            <label>세부 이벤트<select name="event_type"><option value="">전체</option></select></label>
            <label>플레이어<select name="account_id"><option value="">전체 참가자</option></select></label>
            <label>팀<select name="team_id"><option value="">전체 팀</option></select></label>
            <label>내용 검색<input name="search" type="search" autocomplete="off" placeholder="닉네임, 무기, 아이템"></label>
            <label>표시 수<select name="limit"><option value="50">50개</option><option value="100" selected>100개</option><option value="250">250개</option><option value="500">500개</option></select></label>
            <label class="checkbox-line"><input name="include_positions" type="checkbox"> 위치 주기 이벤트 포함</label>
            <button type="submit">적용</button>
            <button class="secondary" type="button" id="matchEventFilterReset">초기화</button>
          </form>
          <div class="status" id="matchEventStatus">이벤트 조회 대기 중</div>
          <div class="table-scroll dense-table-wrap">
            <table><thead><tr><th>경과</th><th>종류</th><th>내용</th><th>위치</th><th></th></tr></thead><tbody id="matchEventBody"></tbody></table>
          </div>
          <div class="actions"><button class="secondary" type="button" id="matchEventPrev">이전</button><button class="secondary" type="button" id="matchEventNext">다음</button></div>
        </div>
      </div>
    </section>
    <dialog id="matchEventDialog" class="app-dialog" aria-labelledby="matchEventDialogTitle">
      <div class="app-dialog-shell">
        <header class="app-dialog-header"><h3 id="matchEventDialogTitle">이벤트 상세</h3><button class="secondary app-dialog-close" type="button" id="matchEventDialogClose" aria-label="이벤트 상세 창 닫기">×</button></header>
        <div class="app-dialog-content"><div id="matchEventDialogSummary"></div><pre id="matchEventDialogRaw" class="json-detail"></pre></div>
      </div>
    </dialog>
    <section id="ranking-lookup" data-view="players">
      <h2>랭킹 조회</h2>
      <form id="rankingForm" class="query-form ranking-form">
        <label>플랫폼
          <select name="shard">
            <option value="steam">steam</option>
            <option value="kakao">kakao</option>
          </select>
        </label>
        <label>지표
          <select name="metric">
            <optgroup label="종합 성과">
              <option value="kda">KDA</option>
              <option value="kd">KD</option>
              <option value="win_rate">승률</option>
              <option value="top10_rate">TOP10 진입률</option>
              <option value="top10s">TOP10 횟수</option>
              <option value="wins">치킨 수</option>
              <option value="matches">경기 수</option>
            </optgroup>
            <optgroup label="전투">
              <option value="avg_damage">평균 딜</option>
              <option value="avg_damage_taken">평균 받은 피해</option>
              <option value="damage_ratio">가한/받은 피해 비율</option>
              <option value="damage">총 딜</option>
              <option value="kills">킬</option>
              <option value="avg_kills">경기당 킬</option>
              <option value="assists">어시스트</option>
              <option value="avg_assists">경기당 어시스트</option>
              <option value="dbnos">기절</option>
              <option value="avg_dbnos">경기당 가한 기절</option>
              <option value="fight_win_rate">교전 승리 확률</option>
              <option value="fight_wins">교전 승리 수</option>
              <option value="avg_fights">경기당 교전 수</option>
            </optgroup>
            <optgroup label="사격·생존">
              <option value="accuracy">명중률(캐릭터·차량)</option>
              <option value="shots_hit">전체 명중 횟수</option>
              <option value="character_hits">캐릭터 명중 횟수</option>
              <option value="vehicle_hits">차량 명중 횟수</option>
              <option value="vehicle_damage_dealt">차량에 가한 피해</option>
              <option value="headshot_hit_rate">헤드샷 명중 확률</option>
              <option value="headshot_hits">헤드샷 명중 횟수</option>
              <option value="headshot_rate">헤드샷 킬 비율</option>
              <option value="headshot_kills">헤드샷 킬 수</option>
              <option value="avg_survival">평균 생존 시간</option>
              <option value="avg_movement">평균 이동 거리</option>
            </optgroup>
          </select>
        </label>
        <label>서버 범위
          <select name="guild_id" id="rankingGuildSelect">
            <option value="">전체 서버</option>
          </select>
        </label>
        <label>표시 인원
          <input name="limit" type="number" min="1" max="100" value="10">
        </label>
        <label>최소 경기 수
          <input name="min_matches" type="number" min="1" step="1" value="1">
        </label>
        <label>등록 상태
          <select name="active_only"><option value="true" selected>수집 중인 유저</option><option value="false">중지 유저 포함</option></select>
        </label>
        <button type="submit">조회</button>
        <button class="secondary" type="button" id="rankingGuildRefresh" title="Discord 서버 목록 새로고침">서버 새로고침</button>
      </form>
      <div class="segmented-control" id="rankingViewControls" role="group" aria-label="랭킹 결과 보기" style="margin-top:10px;">
        <button type="button" class="active" data-ranking-view="table">표</button>
        <button type="button" data-ranking-view="chart">그래프</button>
      </div>
      <div class="status" id="rankingBody" style="margin-top: 12px;">조회 대기 중</div>
    </section>
    <section id="match-job-queue" data-view="collection">
      <h2>매치 수집 큐</h2>
      <div class="actions" style="margin-bottom: 10px;">
        <button type="button" onclick="processMatchJobs()">상세 저장</button>
        <button class="secondary" type="button" onclick="loadJobs()">새로고침</button>
      </div>
      <div class="status" id="jobsSummary" style="margin-bottom: 8px;">수집 큐 확인 중</div>
      <div class="table-scroll dense-table-wrap">
        <table>
          <thead>
            <tr>
              <th>플랫폼</th>
              <th>매치 ID</th>
              <th>상태</th>
              <th>시도</th>
              <th>마지막 변경 (KST)</th>
            </tr>
          </thead>
          <tbody id="jobsBody"></tbody>
        </table>
      </div>
      <div class="dense-card-list" id="jobsCards"></div>
    </section>
    <section id="telemetry-job-queue" data-view="collection">
      <h2>텔레메트리 수집 큐</h2>
      <div class="actions" style="margin-bottom: 10px;">
        <button type="button" onclick="processTelemetryJobs()">텔레메트리 저장</button>
        <button class="secondary" type="button" onclick="loadTelemetryJobs()">새로고침</button>
      </div>
      <div class="status" id="telemetryJobsSummary" style="margin-bottom: 8px;">수집 큐 확인 중</div>
      <div class="table-scroll dense-table-wrap">
        <table>
          <thead>
            <tr>
              <th>플랫폼</th>
              <th>매치 ID</th>
              <th>상태</th>
              <th>시도</th>
              <th>마지막 변경 (KST)</th>
            </tr>
          </thead>
          <tbody id="telemetryJobsBody"></tbody>
        </table>
      </div>
      <div class="dense-card-list" id="telemetryJobsCards"></div>
    </section>
    <section id="post-processing-worker" data-view="collection">
      <h2>자동 후처리 설정</h2>
      <form id="postProcessingWorkerForm">
        <label>전투 파싱
          <input name="combat_limit" type="number" min="1" max="200" value="10" required>
        </label>
        <label>행동 파싱
          <input name="activity_limit" type="number" min="1" max="200" value="10" required>
        </label>
        <label>아이템 파싱
          <input name="item_limit" type="number" min="1" max="200" value="10" required>
        </label>
        <label>이동 파싱
          <input name="movement_limit" type="number" min="1" max="200" value="10" required>
        </label>
        <label>장비 조합
          <input name="loadout_limit" type="number" min="1" max="500" value="50" required>
        </label>
        <label>교전 승패
          <input name="fight_outcome_limit" type="number" min="1" max="200" value="10" required>
        </label>
        <label>2D 스냅샷 JPEG
          <input name="map_snapshot_limit" type="number" min="1" max="200" value="10" required>
        </label>
        <label>재생 타임라인
          <input name="timeline_limit" type="number" min="1" max="200" value="10" required>
        </label>
        <label>처리 방식
          <select name="force">
            <option value="false">기존 결과 제외</option>
            <option value="true">강제 재처리</option>
          </select>
        </label>
        <button type="submit">자동 후처리 시작</button>
        <button class="secondary" type="button" id="postProcessingWorkerStop">중지</button>
      </form>
      <div class="status" id="postProcessingWorkerStatus" style="margin-top: 12px;">자동 후처리 중지</div>
    </section>
    <section id="player-intelligence-audit" data-view="operations">
      <div class="section-heading-row">
        <div>
          <h2>플레이어 데이터 품질</h2>
          <div class="status" id="playerIntelligenceAuditStatus">아직 검사하지 않음</div>
        </div>
        <button type="button" id="playerIntelligenceAuditRun">검사 실행</button>
      </div>
      <div id="playerIntelligenceAuditBody" class="intelligence-empty">검사 결과 대기 중</div>
    </section>
    <section id="operational-drills" data-view="operations">
      <h2>운영 훈련</h2>
      <form id="operationalDrillForm">
        <label>모드
          <select name="mode">
            <option value="simulated">시뮬레이션</option>
            <option value="live">실환경 제한 실행</option>
          </select>
        </label>
        <label>반복
          <input name="cycles" type="number" min="2" max="5" value="3" required>
        </label>
        <button type="submit">실행</button>
        <button class="secondary" type="button" id="operationalDrillsReload">새로고침</button>
      </form>
      <div class="status" id="operationalDrillsStatus" style="margin: 12px 0 8px;">대기 중</div>
      <div class="table-scroll">
        <table class="operational-drill-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>모드</th>
              <th>상태</th>
              <th>완료 KST</th>
              <th>시간</th>
              <th>체크</th>
              <th></th>
            </tr>
          </thead>
          <tbody id="operationalDrillsBody"></tbody>
        </table>
      </div>
      <div class="detail-panel" id="operationalDrillDetail">훈련 이력 대기 중</div>
    </section>
    <section id="worker-runs" data-view="operations">
      <h2>자동 작업 이력</h2>
      <form id="workerRunFilterForm" class="worker-run-filter">
        <label>작업 종류
          <select name="worker_name">
            <option value="all">전체</option>
            <option value="collector">수집기</option>
            <option value="post_processing">후처리</option>
          </select>
        </label>
        <label>상태
          <select name="status">
            <option value="all">전체</option>
            <option value="succeeded">완료</option>
            <option value="failed">실패</option>
          </select>
        </label>
        <label>기간
          <select name="quick_range">
            <option value="custom">직접 지정</option>
            <option value="last_1h">최근 1시간</option>
            <option value="last_24h">최근 24시간</option>
            <option value="today">오늘</option>
            <option value="yesterday">어제</option>
            <option value="last_7d">최근 7일</option>
          </select>
        </label>
        <label>시작 시각 (KST)
          <input name="created_from_kst" type="datetime-local">
        </label>
        <label>종료 시각 (KST)
          <input name="created_to_kst" type="datetime-local">
        </label>
        <label>표시 개수
          <input name="limit" type="number" min="1" max="200" value="20">
        </label>
        <button type="submit">조회</button>
        <button class="secondary" type="button" data-reset-analysis-form="workerRunFilterForm">초기화</button>
      </form>
      <div class="actions" style="margin-bottom: 10px;">
        <button class="secondary" type="button" onclick="loadWorkerRuns()">새로고침</button>
        <button class="secondary" type="button" id="workerRunsExport">CSV 내보내기</button>
        <button class="secondary" type="button" id="workerRunsCopyFilterLink">조회 링크 복사</button>
        <button class="secondary" type="button" id="workerRunsPrev">이전</button>
        <button class="secondary" type="button" id="workerRunsNext">다음</button>
      </div>
      <div class="status" id="workerRunsStatus" style="margin-bottom: 8px;">작업 이력 확인 중</div>
      <div class="table-scroll dense-table-wrap">
        <table>
          <thead>
            <tr>
              <th>작업 종류</th>
              <th>상태</th>
              <th>완료 시각 (KST)</th>
              <th>소요 시간</th>
              <th>요약</th>
              <th></th>
            </tr>
          </thead>
          <tbody id="workerRunsBody"></tbody>
        </table>
      </div>
      <div class="dense-card-list" id="workerRunsCards"></div>
      <div class="detail-panel" id="workerRunDetail">
        작업 이력을 선택하세요.
      </div>
    </section>
    <section id="combat-parser" data-view="collection">
      <h2>전투 데이터 파싱</h2>
      <div class="actions" style="margin-bottom: 10px;">
        <button type="button" onclick="parseTelemetryCombat(false)">전투 파싱</button>
        <button class="secondary" type="button" onclick="parseTelemetryCombat(true)">재파싱</button>
      </div>
      <div class="status" id="combatStatus">대기 중</div>
    </section>
    <section id="item-parser" data-view="collection">
      <h2>아이템 데이터 파싱</h2>
      <div class="actions" style="margin-bottom: 10px;">
        <button type="button" onclick="parseTelemetryItems(false)">아이템 파싱</button>
        <button class="secondary" type="button" onclick="parseTelemetryItems(true)">재파싱</button>
      </div>
      <div class="status" id="itemStatus">대기 중</div>
    </section>
    <section id="movement-parser" data-view="collection">
      <h2>이동 데이터 파싱</h2>
      <div class="actions" style="margin-bottom: 10px;">
        <button type="button" onclick="parseTelemetryMovement(false)">위치 파싱</button>
        <button class="secondary" type="button" onclick="parseTelemetryMovement(true)">재파싱</button>
      </div>
      <div class="status" id="movementStatus">대기 중</div>
    </section>
    <section id="loadout-generator" data-view="collection">
      <h2>장비 조합 스냅샷 생성</h2>
      <div class="actions" style="margin-bottom: 10px;">
        <button type="button" onclick="generateLoadoutSnapshots(false)">파츠 스냅샷 생성</button>
        <button class="secondary" type="button" onclick="generateLoadoutSnapshots(true)">재생성</button>
      </div>
      <div class="status" id="loadoutSnapshotStatus">대기 중</div>
    </section>
    <section id="fight-outcome-generator" data-view="collection">
      <h2>교전 승패 생성</h2>
      <div class="actions" style="margin-bottom: 10px;">
        <button type="button" onclick="generateFightOutcomes(false)">승패 생성</button>
        <button class="secondary" type="button" onclick="generateFightOutcomes(true)">재생성</button>
      </div>
      <div class="status" id="fightOutcomeStatus">대기 중</div>
    </section>
    <section id="map-snapshot-generator" data-view="collection">
      <h2>2D 지도 스냅샷 생성</h2>
      <div class="actions" style="margin-bottom: 10px;">
        <button type="button" onclick="generateMapSnapshots(false)">JPEG 생성</button>
        <button class="secondary" type="button" onclick="generateMapSnapshots(true)">재생성</button>
      </div>
      <div class="status" id="mapSnapshotStatus">대기 중</div>
    </section>
    <section id="timeline-generator" data-view="collection">
      <h2>2D 재생 타임라인 생성</h2>
      <div class="actions" style="margin-bottom: 10px;">
        <button type="button" onclick="generateReplayTimelines(false)">JSON 생성</button>
        <button class="secondary" type="button" onclick="generateReplayTimelines(true)">재생성</button>
      </div>
      <div class="status" id="timelineStatus">대기 중</div>
    </section>
    <section id="replay-player" data-view="replay">
      <h2>2D 전체 경기 리플레이</h2>
      <form id="timelinePlayerForm" class="query-form">
        <label>플랫폼
          <select name="shard">
            <option value="steam">steam</option>
            <option value="kakao">kakao</option>
          </select>
        </label>
        <label>기준 등록 유저
          <input class="registered-player-input" name="target" id="timelinePlayerInput" list="registeredPlayerOptions" autocomplete="off" placeholder="닉네임 일부 입력" required>
        </label>
        <button type="submit">경기 목록</button>
        <button class="secondary" type="button" id="timelinePlayerClear">초기화</button>
      </form>
      <div class="player-controls">
        <label>경기
          <select id="timelineSelect" disabled><option value="">유저를 선택하세요</option></select>
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
        <label>이동 흔적
          <select id="timelineTrailSeconds">
            <option value="15">15초</option>
            <option value="30">30초</option>
            <option value="60" selected>1분</option>
            <option value="120">2분</option>
            <option value="0">경기 전체</option>
          </select>
        </label>
        <button type="button" id="timelinePlayButton">재생</button>
        <button class="secondary" type="button" id="timelineResetButton">처음</button>
      </div>
      <div class="toggle-row">
        <label><input type="checkbox" id="timelineShowPath" checked>이동</label>
        <label><input type="checkbox" id="timelineShowCombat" checked>전투</label>
        <label><input type="checkbox" id="timelineShowEngagements" checked>교전 구간</label>
        <label><input type="checkbox" id="timelineShowShots" checked>발사</label>
        <label><input type="checkbox" id="timelineShowThrows" checked>투척</label>
        <label><input type="checkbox" id="timelineShowHits" checked>피격 방향</label>
        <label><input type="checkbox" id="timelineShowDbno" checked>기절</label>
        <label><input type="checkbox" id="timelineShowKills" checked>킬·사망</label>
        <label><input type="checkbox" id="timelineShowCare" checked>보급</label>
        <label><input type="checkbox" id="timelineShowPlane" checked>비행기</label>
        <label><input type="checkbox" id="timelineShowPhase" checked>자기장</label>
        <label><input type="checkbox" id="timelineShowAllies" checked>아군</label>
        <label><input type="checkbox" id="timelineShowEnemies" checked>적군</label>
        <label><input type="checkbox" id="timelineShowBots" checked>봇</label>
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
      <div class="replay-explorer-bar" aria-label="이벤트 탐색 조건">
        <label>이벤트 대상
          <select id="timelineActorFilter"><option value="focus">선택 유저</option><option value="all">전체 참가자</option></select>
        </label>
        <label>참가자 찾기
          <input id="timelineParticipantSearch" autocomplete="off" placeholder="닉네임 일부 입력">
        </label>
        <label>이벤트 종류
          <select id="timelineEventTypeFilter">
            <option value="all">전체 사건</option>
            <option value="drop_landing">낙하·착지</option>
            <option value="engagement">교전·공격 활동</option>
            <option value="attack">발사·투척·공격</option>
            <option value="hit">명중·피격</option>
            <option value="environment">환경·상태 피해</option>
            <option value="dbno">기절</option>
            <option value="kill">킬·사망</option>
            <option value="revive">부활</option>
            <option value="world">비행기·보급</option>
          </select>
        </label>
        <label class="checkbox-field"><input type="checkbox" id="timelineFollowEvents" checked>목록 자동 추적</label>
        <button class="secondary" type="button" id="timelineEventFilterReset">필터 초기화</button>
        <div class="status timeline-event-count" id="timelineEventCount">0개 사건</div>
      </div>
      <div class="replay-quick-nav" aria-label="주요 위치 바로가기">
        <strong>주요 위치</strong>
        <div class="replay-quick-actions" id="timelineQuickEvents"><span class="status">경기를 불러오세요.</span></div>
      </div>
      <div class="replay-legend" aria-label="리플레이 기호 범례">
        <div class="replay-legend-group">
          <strong>참가자</strong>
          <span><i class="legend-actor focus"></i>기준 유저</span>
          <span><i class="legend-actor ally"></i>아군</span>
          <span><i class="legend-actor enemy"></i>적군</span>
          <span><i class="legend-actor registered"></i>다른 등록 유저</span>
          <span><i class="legend-actor bot"></i>봇</span>
        </div>
        <div class="replay-legend-group">
          <strong>이동 경로</strong>
          <span><i class="legend-line foot"></i>도보</span>
          <span><i class="legend-line vehicle"></i>차량</span>
          <span><i class="legend-line airborne"></i>낙하</span>
          <span><i class="legend-line dbno"></i>기절 이동</span>
        </div>
        <div class="replay-legend-group">
          <strong>주요 위치</strong>
          <span><i class="legend-symbol drop">◆</i>낙하 시작</span>
          <span><i class="legend-symbol landing">▲</i>착지</span>
          <span><i class="legend-symbol engagement"></i>교전(상대 확인)</span>
          <span><i class="legend-symbol activity"></i>공격 활동(상대 미확인)</span>
          <span><i class="legend-symbol revive">+</i>부활</span>
        </div>
        <div class="replay-legend-group">
          <strong>전투 사건</strong>
          <span><i class="legend-symbol shot">◎</i>발사</span>
          <span><i class="legend-symbol throw">◆</i>투척</span>
          <span><i class="legend-symbol hit">⊙</i>명중시킴</span>
          <span><i class="legend-symbol hit-taken">■</i>피격당함</span>
          <span><i class="legend-symbol environment">!</i>환경·상태 피해</span>
          <span><i class="legend-symbol dbno">◇+</i>기절시킴</span>
          <span><i class="legend-symbol dbno-taken">◆−</i>기절당함</span>
          <span><i class="legend-symbol kill">×</i>킬 위치</span>
          <span><i class="legend-symbol death">×</i>사망 위치</span>
        </div>
      </div>
      <div class="timeline-range">
        <input id="timelineScrubber" type="range" min="0" max="0" value="0" step="0.1" aria-label="리플레이 재생 위치">
        <div class="status" id="timelineClock">0.0초</div>
      </div>
      <div class="timeline-now-event" id="timelineNowEvent" aria-live="polite" aria-atomic="true">
        <i class="timeline-event-badge"><span>·</span></i>
        <span class="event-copy"><strong>현재 사건 없음</strong><span class="event-meta">재생을 시작하거나 사건을 선택하세요.</span></span>
        <span class="event-time">0:00.0</span>
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
    <section id="flight-path-analysis" data-view="replay">
      <h2>자주 나온 비행기 동선</h2>
      <form id="flightPathForm" class="analysis-form">
        <div class="query-primary">
          <label>플랫폼
            <select name="shard">
              <option value="">전체</option>
              <option value="steam">Steam</option>
              <option value="kakao">Kakao</option>
            </select>
          </label>
          <label>등록 유저
            <select name="account_id" id="flightPathPlayerSelect">
              <option value="">전체 등록 유저</option>
            </select>
          </label>
          <label>맵
            <select name="map_name" id="flightPathMapFilter" data-catalog-facet="maps">
              <option value="">전체 맵</option>
            </select>
          </label>
          <button type="submit">분석</button>
          <button class="secondary" type="button" id="flightPathReset">초기화</button>
        </div>
        <details class="advanced-filters">
          <summary>상세 필터와 군집 기준</summary>
          <div class="filter-grid">
            <label>게임 모드<select name="game_mode" data-catalog-facet="game_modes"><option value="">전체</option></select></label>
            <label>팀 모드<select name="team_mode"><option value="">전체</option><option value="solo">솔로</option><option value="duo">듀오</option><option value="squad">스쿼드</option></select></label>
            <label>시점<select name="perspective"><option value="">전체</option><option value="fpp">1인칭</option><option value="tpp">3인칭</option></select></label>
            <label>매치 유형<select name="match_type" data-catalog-facet="match_types"><option value="">전체</option></select></label>
            <label>시즌 상태<select name="season_state" data-catalog-facet="season_states"><option value="">전체</option></select></label>
            <label>커스텀<select name="is_custom_match"><option value="">전체</option><option value="false">일반</option><option value="true">커스텀</option></select></label>
            <label>연도<input name="year" type="number" min="2000" max="2100" inputmode="numeric"></label>
            <label>분기<select name="quarter"><option value="">전체</option><option value="1">1분기</option><option value="2">2분기</option><option value="3">3분기</option><option value="4">4분기</option></select></label>
            <label>월<select name="month"><option value="">전체</option><option value="1">1월</option><option value="2">2월</option><option value="3">3월</option><option value="4">4월</option><option value="5">5월</option><option value="6">6월</option><option value="7">7월</option><option value="8">8월</option><option value="9">9월</option><option value="10">10월</option><option value="11">11월</option><option value="12">12월</option></select></label>
            <label>특정 일자 (KST)<input name="exact_date_kst" type="date"></label>
            <label>시간대 (KST)<select name="hour"><option value="">전체</option></select></label>
            <label>시작일 (KST)<input name="from_date_kst" type="date"></label>
            <label>종료일 (KST)<input name="to_date_kst" type="date"></label>
            <label>각도 묶음
              <select name="angle_bin_degrees"><option value="5">5도</option><option value="10" selected>10도</option><option value="15">15도</option><option value="30">30도</option></select>
            </label>
            <label>항로 간격 묶음
              <select name="offset_bin_m"><option value="250">250m</option><option value="500" selected>500m</option><option value="1000">1km</option><option value="2000">2km</option></select>
            </label>
            <label>맵별 상위 항로<input name="top_per_map" type="number" min="1" max="50" value="20" inputmode="numeric"></label>
            <label>최근 원본 항로<input name="recent_limit" type="number" min="1" max="200" value="50" inputmode="numeric"></label>
            <label>분석 최대 항로<input name="route_limit" type="number" min="100" max="100000" value="50000" inputmode="numeric"></label>
          </div>
        </details>
      </form>
      <div class="status" id="flightPathStatus">분석 대기 중</div>
      <div id="flightPathResult" hidden></div>
    </section>
    <section id="replay-artifacts" data-view="replay">
      <h2>2D 리플레이 저장 목록</h2>
      <form id="replayArtifactListForm" class="query-form">
        <label>등록 유저
          <select name="account_id" id="replayArtifactPlayerSelect">
            <option value="">전체 등록 유저</option>
          </select>
        </label>
        <label>파일 종류
          <select name="artifact_type">
            <option value="">전체</option>
            <option value="timeline">재생 타임라인</option>
            <option value="map_snapshot">2D 스냅샷</option>
          </select>
        </label>
        <label>표시 개수
          <select name="limit">
            <option value="20" selected>20개</option>
            <option value="50">50개</option>
            <option value="100">100개</option>
          </select>
        </label>
        <button type="submit">조회</button>
        <button class="secondary" type="button" id="replayArtifactListReset">초기화</button>
      </form>
      <div class="status" id="replayArtifactsStatus" style="margin: 10px 0 8px;">저장 목록 확인 중</div>
      <div class="table-scroll dense-table-wrap">
        <table>
          <thead>
            <tr>
              <th>플레이어</th>
              <th>경기 시각 (KST)</th>
              <th>종류</th>
              <th>맵 / 모드</th>
              <th>매치 ID</th>
              <th>생성 시각 (KST)</th>
              <th>크기</th>
              <th></th>
            </tr>
          </thead>
          <tbody id="replayArtifactsBody"></tbody>
        </table>
      </div>
      <div class="dense-card-list" id="replayArtifactsCards"></div>
    </section>
    </main>
    <aside class="system-rail" aria-label="실시간 시스템 상태">
      <div class="rail-header">
        <span>실시간 상태</span>
        <strong>시스템</strong>
      </div>
      <div class="rail-group">
        <div class="rail-row"><span>MySQL</span><strong id="railDatabase">확인 중</strong></div>
        <div class="rail-row"><span>PUBG API</span><strong id="railPubgApi">확인 중</strong></div>
        <div class="rail-row"><span>Discord</span><strong id="railDiscord">확인 중</strong></div>
      </div>
      <div class="rail-header">
        <span>작업기</span>
        <strong>자동 처리</strong>
      </div>
      <div class="rail-group">
        <div class="rail-row"><span>수집기</span><strong id="railCollector">중지</strong></div>
        <div class="rail-row"><span>후처리</span><strong id="railPostProcessing">중지</strong></div>
      </div>
      <div class="rail-header">
        <span>저장 공간</span>
        <strong>저장 공간</strong>
      </div>
      <div class="rail-storage">
        <div><span>원본</span><strong id="railRawStorage">확인 중</strong></div>
        <div><span>리플레이</span><strong id="railReplayStorage">확인 중</strong></div>
      </div>
      <div class="rail-header">
        <span>최근 상태</span>
        <strong>최근 상태</strong>
      </div>
      <p class="rail-activity" id="railActivity">관리 화면을 준비하고 있습니다.</p>
    </aside>
  </div>
  <script>
    const statusGrid = document.querySelector("#statusGrid");
    const playersBody = document.querySelector("#playersBody");
    const registeredPlayerOptions = document.querySelector("#registeredPlayerOptions");
    const analysisPlayerContextName = document.querySelector("#analysisPlayerContextName");
    const analysisPlayerContextMeta = document.querySelector("#analysisPlayerContextMeta");
    const clearAnalysisPlayerButton = document.querySelector("#clearAnalysisPlayer");
    const intelligenceForm = document.querySelector("#intelligenceForm");
    const intelligenceBody = document.querySelector("#intelligenceBody");
    const intelligenceViewControls = document.querySelector("#intelligenceViewControls");
    const intelligenceChartOptions = document.querySelector("#intelligenceChartOptions");
    const intelligenceGranularityOption = document.querySelector("#intelligenceGranularityOption");
    const intelligenceTrendGranularity = document.querySelector("#intelligenceTrendGranularity");
    const intelligenceTrendMetric = document.querySelector("#intelligenceTrendMetric");
    const intelligenceBreakdownOption = document.querySelector("#intelligenceBreakdownOption");
    const intelligenceBreakdownDimension = document.querySelector("#intelligenceBreakdownDimension");
    const profileForm = document.querySelector("#profileForm");
    const trendForm = document.querySelector("#trendForm");
    const timeInsightForm = document.querySelector("#timeInsightForm");
    const timeInsightBody = document.querySelector("#timeInsightBody");
    const comparisonForm = document.querySelector("#comparisonForm");
    const comparisonItemPicker = document.querySelector("#comparisonItemPicker");
    const comparisonSelectionCount = document.querySelector("#comparisonSelectionCount");
    const comparisonViewControls = document.querySelector("#comparisonViewControls");
    const comparisonBody = document.querySelector("#comparisonBody");
    const comparisonReset = document.querySelector("#comparisonReset");
    const weaponForm = document.querySelector("#weaponForm");
    const recommendationForm = document.querySelector("#recommendationForm");
    const dropZoneForm = document.querySelector("#dropZoneForm");
    const matchForm = document.querySelector("#matchForm");
    const matchExplorerForm = document.querySelector("#matchExplorerForm");
    const matchExplorerReset = document.querySelector("#matchExplorerReset");
    const matchExplorerStatus = document.querySelector("#matchExplorerStatus");
    const matchExplorerBody = document.querySelector("#matchExplorerBody");
    const matchExplorerPrev = document.querySelector("#matchExplorerPrev");
    const matchExplorerNext = document.querySelector("#matchExplorerNext");
    const matchExplorerDetail = document.querySelector("#matchExplorerDetail");
    const matchExplorerDetailTitle = document.querySelector("#matchExplorerDetailTitle");
    const matchExplorerDetailClose = document.querySelector("#matchExplorerDetailClose");
    const matchExplorerReplay = document.querySelector("#matchExplorerReplay");
    const matchExplorerSummary = document.querySelector("#matchExplorerSummary");
    const matchExplorerDetailViews = document.querySelector("#matchExplorerDetailViews");
    const matchExplorerParticipants = document.querySelector("#matchExplorerParticipants");
    const matchExplorerTeams = document.querySelector("#matchExplorerTeams");
    const matchExplorerEvents = document.querySelector("#matchExplorerEvents");
    const matchEventFilterForm = document.querySelector("#matchEventFilterForm");
    const matchEventFilterReset = document.querySelector("#matchEventFilterReset");
    const matchEventStatus = document.querySelector("#matchEventStatus");
    const matchEventBody = document.querySelector("#matchEventBody");
    const matchEventPrev = document.querySelector("#matchEventPrev");
    const matchEventNext = document.querySelector("#matchEventNext");
    const matchEventDialog = document.querySelector("#matchEventDialog");
    const matchEventDialogClose = document.querySelector("#matchEventDialogClose");
    const matchEventDialogTitle = document.querySelector("#matchEventDialogTitle");
    const matchEventDialogSummary = document.querySelector("#matchEventDialogSummary");
    const matchEventDialogRaw = document.querySelector("#matchEventDialogRaw");
    const analysisForms = [intelligenceForm, profileForm, trendForm, timeInsightForm, comparisonForm, weaponForm, recommendationForm, dropZoneForm, matchForm];
    const rankingForm = document.querySelector("#rankingForm");
    const rankingGuildSelect = document.querySelector("#rankingGuildSelect");
    const rankingGuildRefresh = document.querySelector("#rankingGuildRefresh");
    const rankingViewControls = document.querySelector("#rankingViewControls");
    const dataDeletionFilterForm = document.querySelector("#dataDeletionFilterForm");
    const dataDeletionBody = document.querySelector("#dataDeletionBody");
    const dataDeletionStatus = document.querySelector("#dataDeletionStatus");
    const dataDeletionDetail = document.querySelector("#dataDeletionDetail");
    const exportedReviewPacketVerifierForm = document.querySelector("#exportedReviewPacketVerifierForm");
    const exportedReviewPacketVerifierStatus = document.querySelector("#exportedReviewPacketVerifierStatus");
    const exportedReviewPacketVerifierResult = document.querySelector("#exportedReviewPacketVerifierResult");
    const exportedReviewPacketComparerForm = document.querySelector("#exportedReviewPacketComparerForm");
    const exportedReviewPacketComparerStatus = document.querySelector("#exportedReviewPacketComparerStatus");
    const exportedReviewPacketComparerResult = document.querySelector("#exportedReviewPacketComparerResult");
    const profileBody = document.querySelector("#profileBody");
    const trendSummary = document.querySelector("#trendSummary");
    const trendBody = document.querySelector("#trendBody");
    const trendCards = document.querySelector("#trendCards");
    const trendViewControls = document.querySelector("#trendViewControls");
    const trendChartMetric = document.querySelector("#trendChartMetric");
    const trendChartPanel = document.querySelector("#trendChartPanel");
    const trendTableWrap = document.querySelector("#trendTableWrap");
    const weaponBody = document.querySelector("#weaponBody");
    const recommendationBody = document.querySelector("#recommendationBody");
    const dropZoneBody = document.querySelector("#dropZoneBody");
    const mapRegionBody = document.querySelector("#mapRegionBody");
    const matchBody = document.querySelector("#matchBody");
    const rankingBody = document.querySelector("#rankingBody");
    const jobsBody = document.querySelector("#jobsBody");
    const jobsCards = document.querySelector("#jobsCards");
    const jobsSummary = document.querySelector("#jobsSummary");
    const telemetryJobsBody = document.querySelector("#telemetryJobsBody");
    const telemetryJobsCards = document.querySelector("#telemetryJobsCards");
    const telemetryJobsSummary = document.querySelector("#telemetryJobsSummary");
    const operationalDrillForm = document.querySelector("#operationalDrillForm");
    const playerIntelligenceAuditRun = document.querySelector("#playerIntelligenceAuditRun");
    const playerIntelligenceAuditStatus = document.querySelector("#playerIntelligenceAuditStatus");
    const playerIntelligenceAuditBody = document.querySelector("#playerIntelligenceAuditBody");
    const operationalDrillsReload = document.querySelector("#operationalDrillsReload");
    const operationalDrillsStatus = document.querySelector("#operationalDrillsStatus");
    const operationalDrillsBody = document.querySelector("#operationalDrillsBody");
    const operationalDrillDetail = document.querySelector("#operationalDrillDetail");
    const workerRunFilterForm = document.querySelector("#workerRunFilterForm");
    const workerRunsBody = document.querySelector("#workerRunsBody");
    const workerRunsCards = document.querySelector("#workerRunsCards");
    const workerRunsStatus = document.querySelector("#workerRunsStatus");
    const workerRunsExport = document.querySelector("#workerRunsExport");
    const workerRunsCopyFilterLink = document.querySelector("#workerRunsCopyFilterLink");
    const workerRunsPrev = document.querySelector("#workerRunsPrev");
    const workerRunsNext = document.querySelector("#workerRunsNext");
    const workerRunDetail = document.querySelector("#workerRunDetail");
    let operationalDrillRecords = [];
    const combatStatus = document.querySelector("#combatStatus");
    const itemStatus = document.querySelector("#itemStatus");
    const movementStatus = document.querySelector("#movementStatus");
    const loadoutSnapshotStatus = document.querySelector("#loadoutSnapshotStatus");
    const fightOutcomeStatus = document.querySelector("#fightOutcomeStatus");
    const mapSnapshotStatus = document.querySelector("#mapSnapshotStatus");
    const timelineStatus = document.querySelector("#timelineStatus");
    const replayArtifactListForm = document.querySelector("#replayArtifactListForm");
    const replayArtifactListReset = document.querySelector("#replayArtifactListReset");
    const replayArtifactPlayerSelect = document.querySelector("#replayArtifactPlayerSelect");
    const replayArtifactsBody = document.querySelector("#replayArtifactsBody");
    const replayArtifactsCards = document.querySelector("#replayArtifactsCards");
    const replayArtifactsStatus = document.querySelector("#replayArtifactsStatus");
    const flightPathForm = document.querySelector("#flightPathForm");
    const flightPathPlayerSelect = document.querySelector("#flightPathPlayerSelect");
    const flightPathMapFilter = document.querySelector("#flightPathMapFilter");
    const flightPathReset = document.querySelector("#flightPathReset");
    const flightPathStatus = document.querySelector("#flightPathStatus");
    const flightPathResult = document.querySelector("#flightPathResult");
    const discordBotSettingsForm = document.querySelector("#discordBotSettingsForm");
    const discordBotState = document.querySelector("#discordBotState");
    const discordBotUser = document.querySelector("#discordBotUser");
    const discordBotGuildCount = document.querySelector("#discordBotGuildCount");
    const discordBotLastSync = document.querySelector("#discordBotLastSync");
    const discordBotStatus = document.querySelector("#discordBotStatus");
    const discordBotStart = document.querySelector("#discordBotStart");
    const discordBotStop = document.querySelector("#discordBotStop");
    const discordBotSyncAll = document.querySelector("#discordBotSyncAll");
    const discordBotRefreshGuilds = document.querySelector("#discordBotRefreshGuilds");
    const pubgApiKeyForm = document.querySelector("#pubgApiKeyForm");
    const pubgApiKeyClear = document.querySelector("#pubgApiKeyClear");
    const pubgApiKeyStatus = document.querySelector("#pubgApiKeyStatus");
    const discordTokenForm = document.querySelector("#discordTokenForm");
    const discordTokenClear = document.querySelector("#discordTokenClear");
    const discordTokenStatus = document.querySelector("#discordTokenStatus");
    const discordBotGuildSelect = document.querySelector("#discordBotGuildSelect");
    const discordBotCommandSearch = document.querySelector("#discordBotCommandSearch");
    const discordBotCommandCatalog = document.querySelector("#discordBotCommandCatalog");
    const discordBotCommandsAll = document.querySelector("#discordBotCommandsAll");
    const discordBotCommandsNone = document.querySelector("#discordBotCommandsNone");
    const discordBotCommandsDefault = document.querySelector("#discordBotCommandsDefault");
    const discordBotCommandsSave = document.querySelector("#discordBotCommandsSave");
    const discordBotCommandsVerify = document.querySelector("#discordBotCommandsVerify");
    const discordBotCommandsStatus = document.querySelector("#discordBotCommandsStatus");
    const discordGrantForm = document.querySelector("#discordGrantForm");
    const discordPermissionsBody = document.querySelector("#discordPermissionsBody");
    const discordPermissionGroup = document.querySelector("#discordPermissionGroup");
    const discordGrantMemberGuild = document.querySelector("#discordGrantMemberGuild");
    const discordGrantMemberQuery = document.querySelector("#discordGrantMemberQuery");
    const discordGrantMemberOptions = document.querySelector("#discordGrantMemberOptions");
    const discordGrantMemberStatus = document.querySelector("#discordGrantMemberStatus");
    const discordAdminForm = document.querySelector("#discordAdminForm");
    const discordAdminMemberGuild = document.querySelector("#discordAdminMemberGuild");
    const discordAdminMemberQuery = document.querySelector("#discordAdminMemberQuery");
    const discordAdminMemberOptions = document.querySelector("#discordAdminMemberOptions");
    const discordAdminMemberStatus = document.querySelector("#discordAdminMemberStatus");
    const discordCommandGroupForm = document.querySelector("#discordCommandGroupForm");
    const discordCommandSearch = document.querySelector("#discordCommandSearch");
    const discordCommandGroupReset = document.querySelector("#discordCommandGroupReset");
    const discordCommandCatalog = document.querySelector("#discordCommandCatalog");
    const discordCommandGroupStatus = document.querySelector("#discordCommandGroupStatus");
    const discordCommandGroupsBody = document.querySelector("#discordCommandGroupsBody");
    const discordCommandAliasForm = document.querySelector("#discordCommandAliasForm");
    const discordCommandAliasTarget = document.querySelector("#discordCommandAliasTarget");
    const discordCommandAliasReset = document.querySelector("#discordCommandAliasReset");
    const discordCommandAliasesBody = document.querySelector("#discordCommandAliasesBody");
    const discordScopeForm = document.querySelector("#discordScopeForm");
    const discordScopeMode = document.querySelector("#discordScopeMode");
    const discordRankingGuildSelection = document.querySelector("#discordRankingGuildSelection");
    const discordRankingGuildCheckboxes = document.querySelector("#discordRankingGuildCheckboxes");
    const discordRankingGuildStatus = document.querySelector("#discordRankingGuildStatus");
    const discordScopeReset = document.querySelector("#discordScopeReset");
    const publicProfileDefaultForm = document.querySelector("#publicProfileDefaultForm");
    const discordScopesBody = document.querySelector("#discordScopesBody");
    const registerForm = document.querySelector("#registerForm");
    const watchlistRegisterForm = document.querySelector("#watchlistRegisterForm");
    const watchlistRegisterReset = document.querySelector("#watchlistRegisterReset");
    const watchlistGuildSelect = document.querySelector("#watchlistGuildSelect");
    const watchlistChannelSelect = document.querySelector("#watchlistChannelSelect");
    const watchlistChannelAdd = document.querySelector("#watchlistChannelAdd");
    const watchlistChannelSelection = document.querySelector("#watchlistChannelSelection");
    const watchlistChannelStatus = document.querySelector("#watchlistChannelStatus");
    const watchlistSearch = document.querySelector("#watchlistSearch");
    const watchlistIncludeInactive = document.querySelector("#watchlistIncludeInactive");
    const watchlistReload = document.querySelector("#watchlistReload");
    const watchlistScan = document.querySelector("#watchlistScan");
    const watchlistStatus = document.querySelector("#watchlistStatus");
    const watchlistBody = document.querySelector("#watchlistBody");
    const watchlistEncounterStatus = document.querySelector("#watchlistEncounterStatus");
    const watchlistEncounterBody = document.querySelector("#watchlistEncounterBody");
    const watchlistEditDialog = document.querySelector("#watchlistEditDialog");
    const watchlistEditForm = document.querySelector("#watchlistEditForm");
    const watchlistEditTitle = document.querySelector("#watchlistEditTitle");
    const watchlistEditIdentity = document.querySelector("#watchlistEditIdentity");
    const watchlistEditClose = document.querySelector("#watchlistEditClose");
    const watchlistEditCancel = document.querySelector("#watchlistEditCancel");
    const watchlistEditGuildSelect = document.querySelector("#watchlistEditGuildSelect");
    const watchlistEditChannelSelect = document.querySelector("#watchlistEditChannelSelect");
    const watchlistEditChannelAdd = document.querySelector("#watchlistEditChannelAdd");
    const watchlistEditChannelSelection = document.querySelector("#watchlistEditChannelSelection");
    const watchlistEditChannelStatus = document.querySelector("#watchlistEditChannelStatus");
    const storageSettingsForm = document.querySelector("#storageSettingsForm");
    const storageSettingsStatus = document.querySelector("#storageSettingsStatus");
    const alertSettingsForm = document.querySelector("#alertSettingsForm");
    const alertSettingsStatus = document.querySelector("#alertSettingsStatus");
    const alertDiscordGuildSelect = document.querySelector("#alertDiscordGuildSelect");
    const alertDiscordChannelSelect = document.querySelector("#alertDiscordChannelSelect");
    const alertDiscordChannelAdd = document.querySelector("#alertDiscordChannelAdd");
    const alertDiscordChannelsRefresh = document.querySelector("#alertDiscordChannelsRefresh");
    const alertDiscordChannelIds = document.querySelector("#alertDiscordChannelIds");
    const alertDiscordChannelSelection = document.querySelector("#alertDiscordChannelSelection");
    const alertDiscordChannelsStatus = document.querySelector("#alertDiscordChannelsStatus");
    const alertsBody = document.querySelector("#alertsBody");
    const alertHistoryFilterForm = document.querySelector("#alertHistoryFilterForm");
    const alertHistoryBody = document.querySelector("#alertHistoryBody");
    const alertHistoryCards = document.querySelector("#alertHistoryCards");
    const alertHistoryStatus = document.querySelector("#alertHistoryStatus");
    const alertHistoryExport = document.querySelector("#alertHistoryExport");
    const alertHistoryCopyFilterLink = document.querySelector("#alertHistoryCopyFilterLink");
    const alertHistoryPrev = document.querySelector("#alertHistoryPrev");
    const alertHistoryNext = document.querySelector("#alertHistoryNext");
    const alertHistoryPresetButtons = document.querySelectorAll("[data-alert-history-preset]");
    const alertHistoryDialog = document.querySelector("#alertHistoryDialog");
    const alertHistoryDialogClose = document.querySelector("#alertHistoryDialogClose");
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
    const displaySettingsForm = document.querySelector("#displaySettingsForm");
    const displaySettingsStatus = document.querySelector("#displaySettingsStatus");
    const displayNumberPreview = document.querySelector("#displayNumberPreview");
    const banner = document.querySelector("#banner");
    const workspaceMain = document.querySelector("#workspace");
    const sidePanel = document.querySelector(".side-panel");
    const workspaceNav = document.querySelector("#workspaceNav");
    const workspaceSections = document.querySelector("#workspaceSections");
    const workspaceHeading = document.querySelector(".workspace-heading");
    const workspaceTitle = document.querySelector("#workspaceTitle");
    const workspaceEyebrow = document.querySelector("#workspaceEyebrow");
    const workspaceDescription = document.querySelector("#workspaceDescription");
    const refreshWorkspace = document.querySelector("#refreshWorkspace");
    const runtimeMode = document.querySelector("#runtimeMode");
    const kstClock = document.querySelector("#kstClock");
    const railDatabase = document.querySelector("#railDatabase");
    const railPubgApi = document.querySelector("#railPubgApi");
    const railDiscord = document.querySelector("#railDiscord");
    const railCollector = document.querySelector("#railCollector");
    const railPostProcessing = document.querySelector("#railPostProcessing");
    const railRawStorage = document.querySelector("#railRawStorage");
    const railReplayStorage = document.querySelector("#railReplayStorage");
    const railActivity = document.querySelector("#railActivity");
    const pathPickerButtons = document.querySelectorAll("[data-path-purpose][data-path-input]");
    const timelinePlayerForm = document.querySelector("#timelinePlayerForm");
    const timelinePlayerInput = document.querySelector("#timelinePlayerInput");
    const timelinePlayerClear = document.querySelector("#timelinePlayerClear");
    const timelineSelect = document.querySelector("#timelineSelect");
    const timelineSpeed = document.querySelector("#timelineSpeed");
    const timelineTrailSeconds = document.querySelector("#timelineTrailSeconds");
    const timelinePlayButton = document.querySelector("#timelinePlayButton");
    const timelineResetButton = document.querySelector("#timelineResetButton");
    const timelineScrubber = document.querySelector("#timelineScrubber");
    const timelineClock = document.querySelector("#timelineClock");
    const timelineEventDetail = document.querySelector("#timelineEventDetail");
    const timelineEventList = document.querySelector("#timelineEventList");
    const timelineTeamList = document.querySelector("#timelineTeamList");
    const timelineActorFilter = document.querySelector("#timelineActorFilter");
    const timelineParticipantSearch = document.querySelector("#timelineParticipantSearch");
    const timelineEventTypeFilter = document.querySelector("#timelineEventTypeFilter");
    const timelineFollowEvents = document.querySelector("#timelineFollowEvents");
    const timelineEventFilterReset = document.querySelector("#timelineEventFilterReset");
    const timelineEventCount = document.querySelector("#timelineEventCount");
    const timelineQuickEvents = document.querySelector("#timelineQuickEvents");
    const timelineNowEvent = document.querySelector("#timelineNowEvent");
    const replayCanvas = document.querySelector("#replayCanvas");
    const replayPlayerStatus = document.querySelector("#replayPlayerStatus");
    const timelineShowPath = document.querySelector("#timelineShowPath");
    const timelineShowCombat = document.querySelector("#timelineShowCombat");
    const timelineShowEngagements = document.querySelector("#timelineShowEngagements");
    const timelineShowShots = document.querySelector("#timelineShowShots");
    const timelineShowThrows = document.querySelector("#timelineShowThrows");
    const timelineShowHits = document.querySelector("#timelineShowHits");
    const timelineShowDbno = document.querySelector("#timelineShowDbno");
    const timelineShowKills = document.querySelector("#timelineShowKills");
    const timelineShowCare = document.querySelector("#timelineShowCare");
    const timelineShowPlane = document.querySelector("#timelineShowPlane");
    const timelineShowPhase = document.querySelector("#timelineShowPhase");
    const timelineShowAllies = document.querySelector("#timelineShowAllies");
    const timelineShowEnemies = document.querySelector("#timelineShowEnemies");
    const timelineShowBots = document.querySelector("#timelineShowBots");
    const timelineFollowPlayer = document.querySelector("#timelineFollowPlayer");
    const timelineZoom = document.querySelector("#timelineZoom");
    const replayCtx = replayCanvas.getContext("2d");
    let replayTimelineArtifacts = [];
    let activeTimeline = null;
    let activeTimelineArtifact = null;
    let activeTimelineEvents = [];
    let activeTimelineVisibleEvents = [];
    let activeTimelineSelectedEventId = null;
    let activeTimelineCurrentEventId = null;
    let activeTimelineDetailKey = "";
    let activeTimelineDuration = 0;
    let activeTimelineTime = 0;
    let replayMapImage = null;
    let replayMapImageName = "";
    let replayAnimationId = null;
    let replayLastFrameMs = 0;
    let replayPlaying = false;
    let replayPinnedMap = null;
    let replayPinnedEventId = null;
    let activeReplayPlayer = null;
    let activeAnalysisPlayer = null;
    let activeIntelligenceReport = null;
    let activeIntelligenceView = "overview";
    let activeProfilePlayer = null;
    let activeTrendReport = null;
    let activeTrendView = "table";
    let activeTimeInsightReport = null;
    let activeComparisonRows = [];
    let activeComparisonView = "chart";
    let activeWeaponDetail = null;
    let activeWeaponTrendGranularity = "month";
    let activeWeaponTrendMetric = "fight_win_rate";
    let activeRecommendationTarget = "";
    let activeRecommendationShard = "steam";
    let activeRecommendationReport = null;
    let activeRecommendationView = "summary";
    let activeRecommendationChartMetric = "score";
    let activeRankingReport = null;
    let activeRankingView = "table";
    let activeMatchExplorerDetail = null;
    let activeMatchExplorerView = "participants";
    let matchExplorerPage = { total: 0, limit: 50, offset: 0, has_previous: false, has_next: false };
    let matchEventPage = { total: 0, limit: 100, offset: 0, has_previous: false, has_next: false };
    let activeNumberFormat = ["grouped", "korean_units", "plain"].includes("__PUBG_AI_NUMBER_FORMAT__")
      ? "__PUBG_AI_NUMBER_FORMAT__"
      : "grouped";
    let registeredPlayers = [];
    let activeDiscordGuilds = [];
    let activeDiscordPermissions = {
      command_groups: {},
      user_grants: {},
      guild_user_grants: {},
      global_admin_user_ids: [],
      command_aliases: {},
    };
    let activeDiscordCommandCatalog = [];
    let activeDiscordGroupLabels = {};
    let reservedDiscordCommandGroups = new Set();
    let selectedDiscordGroupCommands = new Set();
    const discordMemberCatalogByForm = new WeakMap();
    const discordMemberLoadTimerByForm = new WeakMap();
    let activeDiscordBotSettings = {
      auto_start: false,
      command_prefix: "!",
      guild_enabled_commands: {},
      updated_at: null,
    };
    let activeDiscordBotState = {
      state: "stopped",
      running: false,
      ready: false,
      guild_count: 0,
    };
    let selectedDiscordBotCommands = new Set();
    let discordBotGuildUsesDefault = true;
    let editedDiscordRankingGuildIds = new Set();
    let activeAlertChannelIds = new Set();
    const alertChannelCatalog = new Map();
    let watchedPlayers = [];
    let watchlistRegisterChannelIds = new Set();
    let watchlistEditChannelIds = new Set();
    let watchlistSearchTimer = null;
    let rankingGuildPrefill = "";
    const playerCatalogCache = new Map();
    const catalogByForm = new WeakMap();
    let replayArtifactFilter = { match_id: "", account_id: "", artifact_id: "" };
    let registeredPlayerHighlight = { shard: "", account_id: "", name: "" };
    let deletionRequestHighlightId = "";
    let discordSettingsPrefill = {
      permission_group: "",
      permission_guild_id: "",
      scope_guild_id: "",
      public_profile_default: "",
    };
    let localSettingsPrefill = {
      collector_poll_interval_seconds: "",
      collector_cycle_player_limit: "",
      collector_player_lookup_chunk_size: "",
    };
    let alertHistoryPage = {
      source: "all",
      state: "all",
      severity: "all",
      limit: 20,
      offset: 0,
      total: 0,
      sort: "newest",
      search: "",
      has_previous: false,
      has_next: false,
    };
    let workerRunPage = {
      total: 0,
      limit: 20,
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
      guild_ranking_selected_guild_ids: {},
      public_profile_default: true,
      updated_at: null,
    };

    const workspaceViews = {
      overview: {
        eyebrow: "시스템 개요",
        title: "운영 개요",
        description: "로컬 데이터 수집과 저장 상태를 한눈에 확인합니다.",
      },
      players: {
        eyebrow: "플레이어 분석",
        title: "플레이어 분석",
        description: "추적 대상 등록, 전적, 무기, 추세와 추천 정보를 조회합니다.",
      },
      replay: {
        eyebrow: "전술 리플레이",
        title: "2D 리플레이",
        description: "비행 동선, 이동, 교전과 사망 위치를 타임라인으로 확인합니다.",
      },
      collection: {
        eyebrow: "데이터 처리",
        title: "수집 및 처리",
        description: "매치 수집 큐와 텔레메트리 후처리 작업을 제어합니다.",
      },
      discord: {
        eyebrow: "DISCORD 관리",
        title: "Discord 봇",
        description: "이 앱이 실행하는 하나의 봇에서 명령, 사용자 권한과 서버 범위를 모두 관리합니다.",
      },
      operations: {
        eyebrow: "운영 센터",
        title: "운영 및 알림",
        description: "저장소 경고, 작업 이력, 운영 훈련과 삭제 검토를 확인합니다.",
      },
      settings: {
        eyebrow: "로컬 설정",
        title: "로컬 설정",
        description: "저장 경로, 로컬 상세 링크와 화면 표기를 이 컴퓨터에만 저장합니다.",
      },
    };

    const workspaceSectionsByView = {
      players: [
        { key: "intelligence", label: "종합", ids: ["intelligence-analysis"] },
        { key: "profile", label: "전적", ids: ["profile-lookup"] },
        { key: "trends", label: "추세", ids: ["trend-lookup"] },
        { key: "time", label: "시간대", ids: ["time-analysis"] },
        { key: "compare", label: "비교", ids: ["comparison-analysis"] },
        { key: "weapons", label: "무기", ids: ["weapon-lookup"] },
        { key: "recommendations", label: "추천", ids: ["recommendation-lookup"] },
        { key: "landing", label: "낙하", ids: ["landing-analysis"] },
        { key: "matches", label: "플레이어 매치", ids: ["match-lookup"] },
        { key: "match-detail", label: "매치 상세", ids: ["match-explorer"] },
        { key: "ranking", label: "랭킹", ids: ["ranking-lookup"] },
        { key: "registry", label: "유저 관리", ids: ["player-registration", "registered-players"] },
        { key: "watchlist", label: "밴 플레이어", ids: ["watchlist-management"] },
      ],
      replay: [
        { key: "player", label: "2D 재생", ids: ["replay-player"] },
        { key: "flight-paths", label: "비행기 동선", ids: ["flight-path-analysis"] },
        { key: "artifacts", label: "저장 목록", ids: ["replay-artifacts"] },
        { key: "regions", label: "지역 확인", ids: ["map-region-lookup"] },
      ],
      collection: [
        { key: "collector", label: "자동 수집", ids: ["collector-settings"] },
        { key: "queues", label: "수집 큐", ids: ["match-job-queue", "telemetry-job-queue"] },
        { key: "post", label: "자동 후처리", ids: ["post-processing-worker"] },
        {
          key: "manual",
          label: "수동 도구",
          ids: [
            "combat-parser",
            "item-parser",
            "movement-parser",
            "loadout-generator",
            "fight-outcome-generator",
            "map-snapshot-generator",
            "timeline-generator",
          ],
        },
      ],
      discord: [
        { key: "bot", label: "앱 봇 제어", ids: ["discord-bot-manager"] },
        {
          key: "permissions",
          label: "명령·권한",
          ids: ["discord-permissions", "discord-command-groups"],
        },
        { key: "scopes", label: "서버 관리", ids: ["discord-scopes"] },
      ],
      operations: [
        { key: "quality", label: "데이터 품질", ids: ["player-intelligence-audit"] },
        { key: "alerts", label: "알림", ids: ["alerts"] },
        { key: "deletions", label: "삭제 검토", ids: ["data-deletions"] },
        { key: "drills", label: "운영 훈련", ids: ["operational-drills"] },
        { key: "runs", label: "작업 이력", ids: ["worker-runs"] },
      ],
      settings: [
        { key: "storage", label: "저장 경로", ids: ["storage-settings"] },
        { key: "web", label: "로컬 링크", ids: ["web-link-settings"] },
        { key: "display", label: "화면 표시", ids: ["display-settings"] },
      ],
    };
    const activeWorkspaceSections = {};

    function setRailStatus(element, value, state = "") {
      element.textContent = value;
      element.classList.remove("ok", "warning", "error");
      if (state) element.classList.add(state);
    }

    function storageRailText(status) {
      if (!status?.exists || !status?.is_dir || !status?.writable) return "확인 필요";
      return formatBytes(Number(status.free_bytes || 0)) + " 여유";
    }

    function resetDocumentViewport() {
      window.scrollTo(0, 0);
      document.documentElement.scrollTop = 0;
      document.body.scrollTop = 0;
    }

    function enforceDesktopAppShellOrigin() {
      if (window.matchMedia("(max-width: 820px)").matches) return;
      if (window.scrollY || document.documentElement.scrollTop || document.body.scrollTop) {
        resetDocumentViewport();
      }
    }

    function revealActiveWorkspaceSection() {
      const active = workspaceSections.querySelector("button.active");
      if (!active) return;
      const visibleLeft = workspaceSections.scrollLeft;
      const visibleRight = visibleLeft + workspaceSections.clientWidth;
      const activeLeft = active.offsetLeft;
      const activeRight = activeLeft + active.offsetWidth;
      let nextLeft = visibleLeft;
      if (activeLeft < visibleLeft) nextLeft = activeLeft;
      if (activeRight > visibleRight) nextLeft = activeRight - workspaceSections.clientWidth;
      if (nextLeft !== visibleLeft) {
        workspaceSections.scrollTo({ left: Math.max(0, nextLeft), behavior: "auto" });
      }
    }

    function resetWorkspaceViewport() {
      resetDocumentViewport();
      sidePanel?.scrollTo({ top: 0, left: 0, behavior: "auto" });
      workspaceMain?.scrollTo({ top: 0, left: 0, behavior: "auto" });
      revealActiveWorkspaceSection();
      requestAnimationFrame(resetDocumentViewport);
    }

    function scrollWithinWorkspace(element, { behavior = "smooth", block = "start" } = {}) {
      if (!element || !workspaceMain) return;
      sidePanel?.scrollTo({ top: 0, left: 0, behavior: "auto" });

      if (window.matchMedia("(max-width: 820px)").matches) {
        const top = window.scrollY + element.getBoundingClientRect().top - 12;
        window.scrollTo({ top: Math.max(0, top), behavior });
        return;
      }

      resetDocumentViewport();
      const workspaceRect = workspaceMain.getBoundingClientRect();
      const elementRect = element.getBoundingClientRect();
      const headingBottom = Math.max(
        workspaceRect.top,
        workspaceHeading?.getBoundingClientRect().bottom || workspaceRect.top,
      );
      const visibleTop = workspaceSections.classList.contains("visible")
        ? Math.max(headingBottom, workspaceSections.getBoundingClientRect().bottom) + 8
        : headingBottom + 8;
      const visibleBottom = workspaceRect.bottom - 8;
      let delta = 0;
      if (block === "start") {
        delta = elementRect.top - visibleTop;
      } else if (elementRect.top < visibleTop) {
        delta = elementRect.top - visibleTop;
      } else if (elementRect.bottom > visibleBottom) {
        delta = elementRect.bottom - visibleBottom;
      }
      workspaceMain.scrollTo({
        top: Math.max(0, workspaceMain.scrollTop + delta),
        behavior,
      });
      requestAnimationFrame(resetDocumentViewport);
    }

    function workspaceViewFromLocation() {
      const hash = decodeURIComponent(window.location.hash.replace(/^#/, ""));
      if (hash.startsWith("workspace-")) {
        const requested = hash.slice("workspace-".length);
        if (workspaceViews[requested]) return requested;
      }
      const target = hash ? document.getElementById(hash) : null;
      const targetView = target?.closest("[data-view]")?.dataset.view;
      return targetView && workspaceViews[targetView]
        ? targetView
        : "overview";
    }

    function workspaceSectionForFocus(view, focusId) {
      if (!focusId) return null;
      const sectionId = document.getElementById(focusId)?.closest("section[data-view]")?.id || focusId;
      return (workspaceSectionsByView[view] || []).find((group) => group.ids.includes(sectionId)) || null;
    }

    function renderWorkspaceSections(view, focusId = "") {
      const groups = workspaceSectionsByView[view] || [];
      const viewSections = document.querySelectorAll(`main > section[data-view="${view}"]`);
      if (!groups.length) {
        workspaceSections.classList.remove("visible");
        workspaceSections.innerHTML = "";
        viewSections.forEach((section) => { section.hidden = false; });
        return;
      }

      const focused = workspaceSectionForFocus(view, focusId);
      const active = focused
        || groups.find((group) => group.key === activeWorkspaceSections[view])
        || groups[0];
      activeWorkspaceSections[view] = active.key;
      workspaceSections.classList.add("visible");
      workspaceSections.innerHTML = groups.map((group) => `
        <button type="button" role="tab" data-workspace-section="${attr(group.key)}"
          class="${group.key === active.key ? "active" : ""}"
          aria-selected="${group.key === active.key ? "true" : "false"}">
          ${escapeHtml(group.label)}
        </button>
      `).join("");
      viewSections.forEach((section) => {
        section.hidden = !active.ids.includes(section.id);
      });
    }

    function activateWorkspace(view, options = {}) {
      const nextView = workspaceViews[view] ? view : "overview";
      const details = workspaceViews[nextView];
      document.body.dataset.activeView = nextView;
      workspaceEyebrow.textContent = details.eyebrow;
      workspaceTitle.textContent = details.title;
      workspaceDescription.textContent = details.description;
      renderWorkspaceSections(nextView, options.focusId || "");
      for (const button of workspaceNav.querySelectorAll("[data-view-target]")) {
        const active = button.dataset.viewTarget === nextView;
        button.classList.toggle("active", active);
        if (active) {
          button.setAttribute("aria-current", "page");
        } else {
          button.removeAttribute("aria-current");
        }
      }
      if (options.updateUrl) {
        const url = new URL(window.location.href);
        url.hash = "workspace-" + nextView;
        window.history.pushState({}, "", url);
      }
      resetWorkspaceViewport();
      requestAnimationFrame(resetWorkspaceViewport);
    }

    function syncWorkspaceToLocation() {
      const hash = decodeURIComponent(window.location.hash.replace(/^#/, ""));
      activateWorkspace(workspaceViewFromLocation(), {
        focusId: hash && !hash.startsWith("workspace-") ? hash : "",
      });
    }

    function updateKstClock() {
      kstClock.textContent = new Intl.DateTimeFormat("ko-KR", {
        timeZone: "Asia/Seoul",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      }).format(new Date());
    }

    async function enableDesktopFeatures() {
      if (!window.pywebview?.api) return false;
      document.body.classList.add("desktop-host");
      runtimeMode.textContent = `데스크톱 · ${document.body.dataset.appRelease || "버전 확인 불가"}`;
      try {
        const status = await window.pywebview.api.runtime_status();
        if (status.app_release) runtimeMode.textContent = `데스크톱 · ${status.app_release}`;
        runtimeMode.title = status.base_url + " / " + status.project_dir;
      } catch (error) {
        runtimeMode.title = "데스크톱 연결 오류: " + error.message;
      }
      return true;
    }

    async function chooseStorageDirectory(button) {
      if (!window.pywebview?.api) throw new Error("폴더 선택은 데스크톱 프로그램에서 사용할 수 있습니다.");
      button.disabled = true;
      try {
        const result = await window.pywebview.api.choose_directory(button.dataset.pathPurpose || "");
        if (!result?.selected || !result.path) return;
        const input = storageSettingsForm.elements[button.dataset.pathInput || ""];
        if (!input) throw new Error("저장 경로 입력란을 찾을 수 없습니다.");
        input.value = result.path;
        input.dispatchEvent(new Event("input", { bubbles: true }));
        storageSettingsStatus.textContent = "선택한 경로를 적용하려면 저장 버튼을 누르세요.";
        banner.textContent = result.path + " 선택됨";
      } finally {
        button.disabled = false;
      }
    }

    async function refreshActiveWorkspace() {
      const activeView = document.body.dataset.activeView || "overview";
      const refreshers = {
        overview: () => Promise.all([loadStatus(), loadAlerts({ renderHistory: false })]),
        players: () => Promise.all([
          loadPlayers(),
          loadDiscordGuilds(),
          loadWatchlistPlayers(),
          loadWatchlistEncounters(),
        ]),
        replay: () => loadReplayArtifacts(),
        collection: () => Promise.all([
          loadCollectorWorkerStatus(),
          loadPostProcessingWorkerStatus(),
          loadJobs(),
          loadTelemetryJobs(),
        ]),
        discord: () => Promise.all([loadDiscordBot(), loadDiscordPermissions(), loadDiscordScopes(), loadDiscordGuilds()]),
        operations: () => Promise.all([
          loadAlerts(),
          loadOperationalDrills(),
          loadWorkerRuns(),
          loadDataDeletionRequests(),
        ]),
        settings: () => loadStatus(),
      };
      refreshWorkspace.disabled = true;
      banner.textContent = workspaceViews[activeView].title + " 새로고침 중";
      try {
        await refreshers[activeView]();
        banner.textContent = workspaceViews[activeView].title + " 새로고침 완료";
      } finally {
        refreshWorkspace.disabled = false;
      }
    }

    function cell(label, value) {
      return `<div class="kv"><span>${label}</span><strong>${value}</strong></div>`;
    }

    function resultHeading(title, subtitle = "", badge = "", badgeClass = "") {
      return `
        <div class="result-heading">
          <div>
            <strong>${escapeHtml(title)}</strong>
            ${subtitle ? `<span>${escapeHtml(subtitle)}</span>` : ""}
          </div>
          ${badge ? `<span class="result-badge ${attr(badgeClass)}">${escapeHtml(badge)}</span>` : ""}
        </div>`;
    }

    function resultMetricGrid(items) {
      return `<div class="result-metric-grid">${items.map((item) => `
        <div class="result-metric">
          <span>${escapeHtml(item[0])}</span>
          <strong>${escapeHtml(item[1])}</strong>
        </div>`).join("")}</div>`;
    }

    function resultSection(title, body) {
      return `<div class="result-section"><h3>${escapeHtml(title)}</h3>${body}</div>`;
    }

    function resultTextRows(items) {
      return `<div class="result-list">${items.map((item) => `
        <div class="result-row">
          <span>${escapeHtml(item[0])}</span>
          <strong>${escapeHtml(item[1])}</strong>
        </div>`).join("")}</div>`;
    }

    function resultChips(items, emptyText = "기록 없음") {
      const values = (items || []).filter((item) => String(item || "").trim());
      if (!values.length) return `<span class="result-caption">${escapeHtml(emptyText)}</span>`;
      return `<div class="result-chip-list">${values.map((item) => (
        `<span class="result-chip">${escapeHtml(item)}</span>`
      )).join("")}</div>`;
    }

    function compactIdentifier(value, head = 8, tail = 4) {
      const text = String(value || "");
      if (!text) return "-";
      if (text.length <= head + tail + 1) return text;
      return `${text.slice(0, head)}...${text.slice(-tail)}`;
    }

    function formatKstShort(value) {
      if (!value) return "-";
      const date = new Date(String(value).replace(" ", "T"));
      if (Number.isNaN(date.getTime())) return String(value).replace("T", " ").slice(0, 16);
      return new Intl.DateTimeFormat("ko-KR", {
        timeZone: "Asia/Seoul",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }).format(date);
    }

    function jobStatusMeta(status) {
      const key = String(status || "unknown").toLowerCase();
      const values = {
        queued: ["대기", "warning"],
        pending: ["대기", "warning"],
        running: ["처리 중", "info"],
        processing: ["처리 중", "info"],
        retry: ["재시도", "warning"],
        retrying: ["재시도", "warning"],
        succeeded: ["완료", "success"],
        completed: ["완료", "success"],
        failed: ["실패", "error"],
      };
      return values[key] || [status || "알 수 없음", "info"];
    }

    function jobStatusBadge(status) {
      const [label, style] = jobStatusMeta(status);
      return `<span class="status-badge ${style}">${escapeHtml(label)}</span>`;
    }

    function queueSummaryText(summary, jobs) {
      const fallback = (jobs || []).reduce((counts, job) => {
        const key = String(job.status || "unknown").toLowerCase();
        counts[key] = Number(counts[key] || 0) + 1;
        return counts;
      }, {});
      const byStatus = summary?.by_status || fallback;
      const total = Number(summary?.total ?? Object.values(byStatus).reduce((sum, value) => sum + Number(value || 0), 0));
      const count = (...keys) => keys.reduce((sum, key) => sum + Number(byStatus[key] || 0), 0);
      const parts = [
        `전체 ${formatInteger(total)}건`,
        `처리 가능 ${formatInteger(summary?.eligible_queued ?? count("queued", "pending"))}건`,
        `재시도 예약 ${formatInteger(summary?.scheduled_queued || 0)}건`,
        `처리 중 ${formatInteger(count("running", "processing"))}건`,
        `실패 ${formatInteger(count("failed"))}건`,
        `최근 10분 완료 ${formatInteger(summary?.recent_succeeded || 0)}건`,
        `누적 완료 ${formatInteger(count("succeeded", "completed"))}건`,
      ];
      if (summary?.oldest_queued_at_kst) {
        parts.push(`최장 대기 ${formatKstShort(summary.oldest_queued_at_kst)}부터`);
      }
      if (summary?.last_activity_at_kst) {
        parts.push(`최근 활동 ${formatKstShort(summary.last_activity_at_kst)}`);
      }
      return parts.join(" · ");
    }

    function workerNameLabel(value) {
      return {
        collector: "수집기",
        post_processing: "후처리",
      }[String(value || "")] || String(value || "-");
    }

    function artifactTypeLabel(value) {
      return {
        timeline: "재생 타임라인",
        map_snapshot: "2D 스냅샷",
      }[String(value || "")] || String(value || "-");
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
        requestJson("/settings/status", "GET"),
        requestJson("/database/status", "GET").catch(() => ({ mysql_connection: "error" })),
      ]);
      const databaseReady = database.mysql_connection === "ok";
      setRailStatus(
        railDatabase,
        databaseReady ? (database.database || "연결됨") : "연결 오류",
        databaseReady ? "ok" : "error",
      );
      const pubgReady = Boolean(settings.secrets.PUBG_API_KEY.configured);
      const discordReady = Boolean(settings.secrets.DISCORD_BOT_TOKEN.configured);
      setRailStatus(railPubgApi, pubgReady ? "키 설정됨" : "키 없음", pubgReady ? "ok" : "error");
      setRailStatus(railDiscord, discordReady ? "토큰 설정됨" : "토큰 없음", discordReady ? "ok" : "error");
      const rawStorage = settings.storage_status?.raw_data_dir;
      const replayStorage = settings.storage_status?.replay_data_dir;
      const operations = database.operations || {};
      setRailStatus(
        railRawStorage,
        storageRailText(rawStorage),
        rawStorage?.exists && rawStorage?.is_dir && rawStorage?.writable ? "ok" : "error",
      );
      setRailStatus(
        railReplayStorage,
        storageRailText(replayStorage),
        replayStorage?.exists && replayStorage?.is_dir && replayStorage?.writable ? "ok" : "error",
      );
      statusGrid.innerHTML = [
        cell("MySQL", escapeHtml(`${databaseReady ? "연결됨" : "연결 오류"} / ${database.database || "-"}`)),
        cell("추적 유저", `${formatInteger(operations.active_players || 0)} / ${formatInteger(operations.registered_players || 0)}명 활성`),
        cell("저장 매치", `${formatInteger(operations.matches || 0)}경기`),
        cell("수집 대기", `매치 ${formatInteger(operations.pending_match_jobs || 0)} · 텔레메트리 ${formatInteger(operations.pending_telemetry_jobs || 0)}`),
        cell("원본 데이터", `매치 ${formatInteger(operations.raw_matches || 0)} · 텔레메트리 ${formatInteger(operations.raw_telemetry || 0)}`),
        cell("2D 결과", `스냅샷 ${formatInteger(operations.map_snapshots || 0)} · 타임라인 ${formatInteger(operations.timelines || 0)}`),
        cell("실패 작업", `${formatInteger(operations.failed_jobs || 0)}건`),
        cell("PUBG API 키", settings.secrets.PUBG_API_KEY.configured ? "설정됨" : "없음"),
        cell("Discord 봇 토큰", settings.secrets.DISCORD_BOT_TOKEN.configured ? "설정됨" : "없음"),
        cell("원본 저장소", escapeHtml(storageRailText(settings.storage_status?.raw_data_dir))),
        cell("2D 저장소", escapeHtml(storageRailText(settings.storage_status?.replay_data_dir))),
        cell("백업 저장소", escapeHtml(storageRailText(settings.storage_status?.backup_data_dir))),
        cell("격리 저장소", escapeHtml(storageRailText(settings.storage_status?.quarantine_data_dir))),
        cell("수집 주기", escapeHtml(`${formatInteger(settings.collector.poll_interval_seconds)}초`)),
        cell("주기당 대상", escapeHtml(`${formatInteger(settings.collector.cycle_player_limit)}명`)),
        cell("조회 묶음", escapeHtml(`${formatInteger(settings.collector.player_lookup_chunk_size)}명`)),
      ].join("");
      storageSettingsForm.elements.raw_data_dir.value = settings.raw_data_dir || "";
      storageSettingsForm.elements.replay_data_dir.value = settings.replay_data_dir || "";
      storageSettingsForm.elements.backup_data_dir.value = settings.backup_data_dir || "";
      storageSettingsForm.elements.quarantine_data_dir.value = settings.quarantine_data_dir || "";
      storageSettingsForm.elements.raw_compression.value = settings.raw_compression || "gzip";
      storageSettingsStatus.textContent = [
        `원본 ${formatStoragePathStatus(settings.storage_status?.raw_data_dir)}`,
        `리플레이 ${formatStoragePathStatus(settings.storage_status?.replay_data_dir)}`,
        `백업 ${formatStoragePathStatus(settings.storage_status?.backup_data_dir)}`,
        `격리 ${formatStoragePathStatus(settings.storage_status?.quarantine_data_dir)}`,
      ].join(" / ");
      collectorSettingsForm.elements.poll_interval_seconds.value = settings.collector.poll_interval_seconds || 180;
      collectorSettingsForm.elements.cycle_player_limit.value = settings.collector.cycle_player_limit || 100;
      collectorSettingsForm.elements.player_lookup_chunk_size.value = settings.collector.player_lookup_chunk_size || 10;
      collectorSettingsStatus.textContent = [
        `${formatInteger(settings.collector.poll_interval_seconds)}초`,
        `${formatInteger(settings.collector.cycle_player_limit)}명`,
        `조회 묶음 ${formatInteger(settings.collector.player_lookup_chunk_size)}명`,
      ].join(" / ");
      webSettingsForm.elements.local_web_base_url.value = settings.local_web_base_url || "";
      webSettingsStatus.textContent = settings.local_web_base_url
        ? `사용 중: ${settings.local_web_base_url}`
        : "사용 안 함";
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
      if (!status) return "상태 알 수 없음";
      if (!status.exists) return "경로 없음";
      if (!status.is_dir) return "폴더가 아님";
      if (!status.writable) return `쓰기 불가${status.error ? `: ${status.error}` : ""}`;
      return `정상 · ${formatBytes(Number(status.free_bytes || 0))} 여유`;
    }

    async function loadAlerts(options = {}) {
      try {
        const payload = await requestJson("/alerts/status", "GET");
        renderAlertStatus(payload, options.renderHistory !== false);
      } catch (error) {
        alertSettingsStatus.textContent = `오류: ${error.message}`;
        alertsBody.innerHTML = `<tr><td colspan="5">오류: ${escapeHtml(error.message)}</td></tr>`;
        alertHistoryBody.innerHTML = `<tr><td colspan="7">오류: ${escapeHtml(error.message)}</td></tr>`;
        alertHistoryStatus.textContent = `오류: ${error.message}`;
      }
    }

    async function loadAlertHistory(options = {}) {
      const form = new FormData(alertHistoryFilterForm);
      const source = options.source || String(form.get("source") || alertHistoryPage.source || "all");
      const state = options.state || String(form.get("state") || alertHistoryPage.state || "all");
      const severity = options.severity || String(form.get("severity") || alertHistoryPage.severity || "all");
      const sort = options.sort || String(form.get("sort") || alertHistoryPage.sort || "newest");
      const search = options.search ?? String(form.get("search") ?? alertHistoryPage.search ?? "");
      const limit = Number(options.limit || form.get("limit") || alertHistoryPage.limit || 20);
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
      const payload = await requestJson(`/alerts/history?${params.toString()}`, "GET");
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
        "watchlist-alerts": {
          source: "watchlist",
          state: "all",
          severity: "all",
          sort: "newest",
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
      if (!filters) throw new Error(`알 수 없는 알림 이력 필터: ${preset}`);
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
      activeAlertChannelIds = new Set((settings.discord_channel_ids || []).map(String));
      renderSelectedAlertChannels();
      alertSettingsForm.elements.storage_alerts_enabled.value = settings.storage_alerts_enabled === false ? "false" : "true";
      alertSettingsForm.elements.worker_error_alerts_enabled.value = settings.worker_error_alerts_enabled === false ? "false" : "true";
      alertSettingsStatus.textContent = [
        `최소 여유 ${formatBytes(Number(settings.minimum_free_bytes || 0))}`,
        `알림 채널 ${formatInteger((settings.discord_channel_ids || []).length)}개`,
        `활성 경고 ${formatInteger((payload.alerts || []).length)}건`,
        `전체 이력 ${formatInteger(payload.alert_history_page?.total ?? (payload.alert_history || []).length)}건`,
      ].join(" · ");
      renderAlerts(payload.alerts || []);
      if (renderHistory) {
        renderAlertHistory(payload.alert_history || [], payload.alert_history_page || {}, true);
      }
    }

    function alertChannelLabel(channelId) {
      const channel = alertChannelCatalog.get(String(channelId));
      if (channel) return `${channel.guild_name} / #${channel.channel_name}`;
      return `채널 ID ${compactIdentifier(channelId, 6, 6)}`;
    }

    function renderSelectedAlertChannels() {
      const ids = Array.from(activeAlertChannelIds);
      alertDiscordChannelIds.value = ids.join(",");
      alertDiscordChannelSelection.innerHTML = ids.length
        ? ids.map((channelId) => `
          <span class="result-chip selected-channel-chip">
            <span title="${attr(channelId)}">${escapeHtml(alertChannelLabel(channelId))}</span>
            <button
              type="button"
              data-alert-channel-remove="${attr(channelId)}"
              title="알림 채널 제거"
              aria-label="${attr(alertChannelLabel(channelId))} 제거"
            >×</button>
          </span>
        `).join("")
        : '<span class="result-caption">선택된 알림 채널 없음</span>';
      alertDiscordChannelsStatus.textContent = ids.length
        ? `저장 대상 ${formatInteger(ids.length)}개 채널`
        : "선택된 알림 채널 없음";
    }

    async function loadDiscordAlertChannels() {
      const guildId = alertDiscordGuildSelect.value;
      alertDiscordChannelSelect.disabled = true;
      alertDiscordChannelAdd.disabled = true;
      if (!guildId) {
        alertDiscordChannelSelect.innerHTML = '<option value="">서버 선택 필요</option>';
        alertDiscordChannelsStatus.textContent = activeAlertChannelIds.size
          ? `저장 대상 ${formatInteger(activeAlertChannelIds.size)}개 채널`
          : "선택된 알림 채널 없음";
        return;
      }
      alertDiscordChannelsStatus.textContent = "전송 가능 채널 확인 중";
      const response = await fetch(`/discord/channels?guild_id=${encodeURIComponent(guildId)}&limit=50`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || response.statusText);
      const channels = payload.channels || [];
      for (const channel of channels) {
        alertChannelCatalog.set(String(channel.channel_id), channel);
      }
      alertDiscordChannelSelect.innerHTML = channels.length
        ? '<option value="">채널 선택</option>' + channels.map((channel) => (
          `<option value="${attr(channel.channel_id)}">#${escapeHtml(channel.channel_name)}${channel.can_read_history ? "" : " · 기록 읽기 불가"}</option>`
        )).join("")
        : '<option value="">전송 가능한 채널 없음</option>';
      alertDiscordChannelSelect.disabled = !channels.length;
      alertDiscordChannelAdd.disabled = true;
      alertDiscordChannelsStatus.textContent = `${payload.guild?.guild_name || discordGuildName(guildId)} · 전송 가능 ${formatInteger(channels.length)}개`;
      renderSelectedAlertChannels();
    }

    function renderWatchlistChannelSelection(kind) {
      const editing = kind === "edit";
      const ids = Array.from(editing ? watchlistEditChannelIds : watchlistRegisterChannelIds);
      const container = editing ? watchlistEditChannelSelection : watchlistChannelSelection;
      const status = editing ? watchlistEditChannelStatus : watchlistChannelStatus;
      const removeAttribute = editing
        ? "data-watchlist-edit-channel-remove"
        : "data-watchlist-channel-remove";
      container.innerHTML = ids.length
        ? ids.map((channelId) => `
          <span class="result-chip selected-channel-chip">
            <span title="${attr(channelId)}">${escapeHtml(alertChannelLabel(channelId))}</span>
            <button type="button" ${removeAttribute}="${attr(channelId)}" title="알림 채널 제거" aria-label="${attr(alertChannelLabel(channelId))} 제거">×</button>
          </span>
        `).join("")
        : '<span class="result-caption">개별 채널 미지정 · 운영 알림 기본 채널 사용</span>';
      status.textContent = ids.length
        ? `개별 알림 채널 ${formatInteger(ids.length)}개`
        : "운영 알림 기본 채널로 전송";
    }

    async function loadWatchlistChannelOptions(kind) {
      const editing = kind === "edit";
      const guildSelect = editing ? watchlistEditGuildSelect : watchlistGuildSelect;
      const channelSelect = editing ? watchlistEditChannelSelect : watchlistChannelSelect;
      const addButton = editing ? watchlistEditChannelAdd : watchlistChannelAdd;
      const status = editing ? watchlistEditChannelStatus : watchlistChannelStatus;
      const guildId = String(guildSelect.value || "");
      channelSelect.disabled = true;
      addButton.disabled = true;
      if (!guildId) {
        channelSelect.innerHTML = '<option value="">서버 선택 필요</option>';
        renderWatchlistChannelSelection(kind);
        return;
      }
      status.textContent = "전송 가능한 Discord 채널 확인 중";
      const payload = await requestJson(`/discord/channels?guild_id=${encodeURIComponent(guildId)}&limit=50`, "GET");
      const channels = payload.channels || [];
      for (const channel of channels) alertChannelCatalog.set(String(channel.channel_id), channel);
      channelSelect.innerHTML = channels.length
        ? '<option value="">채널 선택</option>' + channels.map((channel) => (
          `<option value="${attr(channel.channel_id)}">#${escapeHtml(channel.channel_name)}${channel.can_send_messages === false ? " · 전송 불가" : ""}</option>`
        )).join("")
        : '<option value="">전송 가능한 채널 없음</option>';
      channelSelect.disabled = !channels.length;
      renderWatchlistChannelSelection(kind);
    }

    function watchlistAlertLabel(player) {
      const labels = [];
      if (player.notify_name_change) labels.push("닉네임 변경");
      if (player.notify_kill) labels.push("등록 유저 처치");
      if (player.notify_repeated_engagement) labels.push(`반복 교전 ${formatInteger(player.engagement_threshold)}회`);
      return labels.join(" · ") || "알림 없음";
    }

    function renderWatchlistPlayers() {
      watchlistBody.innerHTML = watchedPlayers.map((player) => {
        const aliases = (player.aliases || []).filter((name) => name !== player.current_name);
        const channels = (player.notification_channel_ids || []).map(alertChannelLabel);
        return `
          <tr>
            <td><strong>${escapeHtml(player.current_name)}</strong> · ${escapeHtml(player.shard)}<br><span class="result-caption" title="${attr(player.account_id)}">${escapeHtml(compactIdentifier(player.account_id, 14, 7))}</span>${aliases.length ? `<br><span class="result-caption">이전 이름: ${escapeHtml(aliases.slice(0, 4).join(", "))}</span>` : ""}</td>
            <td>${player.active ? '<span class="result-badge success">감시 중</span>' : '<span class="result-badge muted">감시 중지</span>'}<br><span class="result-caption">확인 ${escapeHtml(formatKstShort(player.last_identity_checked_at_kst))}</span></td>
            <td>${escapeHtml(watchlistAlertLabel(player))}</td>
            <td>${channels.length ? escapeHtml(channels.join(", ")) : '<span class="result-caption">운영 기본 채널</span>'}</td>
            <td>${formatInteger(player.encounter_count)}건<br><span class="result-caption">${escapeHtml(formatKstShort(player.latest_encounter_at_kst))}</span></td>
            <td><button class="secondary" type="button" data-watchlist-edit="${attr(player.id)}">설정</button></td>
          </tr>
        `;
      }).join("") || '<tr><td colspan="6">등록된 밴 플레이어가 없습니다.</td></tr>';
    }

    async function loadWatchlistPlayers() {
      const params = new URLSearchParams({
        include_inactive: String(Boolean(watchlistIncludeInactive.checked)),
        limit: "500",
      });
      const search = String(watchlistSearch.value || "").trim();
      if (search) params.set("search", search);
      watchlistStatus.textContent = "밴 플레이어 목록을 조회하는 중입니다.";
      const payload = await requestJson(`/watchlist/players?${params.toString()}`, "GET");
      watchedPlayers = payload.players || [];
      renderWatchlistPlayers();
      watchlistStatus.textContent = `${formatInteger(watchedPlayers.length)}명 · Account ID 기준 동일 계정 추적`;
      return watchedPlayers;
    }

    async function loadWatchlistEncounters(watchedPlayerId = null) {
      const params = new URLSearchParams({ limit: "200" });
      if (watchedPlayerId) params.set("watched_player_id", String(watchedPlayerId));
      watchlistEncounterStatus.textContent = "등록 유저와의 조우 이력을 조회하는 중입니다.";
      const payload = await requestJson(`/watchlist/encounters?${params.toString()}`, "GET");
      const encounters = payload.encounters || [];
      watchlistEncounterBody.innerHTML = encounters.map((encounter) => `
        <tr>
          <td>${escapeHtml(formatKstShort(encounter.created_at_kst))}<br><span class="result-caption" title="${attr(encounter.match_id)}">${escapeHtml(compactIdentifier(encounter.match_id, 10, 6))}</span><br><span class="result-caption">${escapeHtml(displayCode(encounter.map_name, "map"))} · ${escapeHtml(displayCode(encounter.game_mode, "game_mode"))}</span></td>
          <td><strong>${escapeHtml(encounter.watched_name)}</strong></td>
          <td><strong>${escapeHtml(encounter.registered_name)}</strong><br><span class="result-caption" title="${attr(encounter.registered_account_id)}">${escapeHtml(compactIdentifier(encounter.registered_account_id, 12, 6))}</span></td>
          <td><strong>${formatInteger(encounter.engagement_count)}회</strong><br><span class="result-caption">피해 가함 ${formatInteger(encounter.damage_events_by_watched)} / 받음 ${formatInteger(encounter.damage_events_by_registered)}</span></td>
          <td>가함 ${formatInteger(encounter.dbnos_by_watched)} / 당함 ${formatInteger(encounter.dbnos_by_registered)}</td>
          <td>가함 ${formatInteger(encounter.kills_by_watched)} / 당함 ${formatInteger(encounter.kills_by_registered)}</td>
          <td><div class="actions"><button class="secondary" type="button" data-watchlist-match="${attr(encounter.match_id)}">매치 상세</button><button class="secondary" type="button" data-watchlist-replay="${attr(encounter.match_id)}">2D 재생</button></div></td>
        </tr>
      `).join("") || '<tr><td colspan="7">분석된 조우 이력이 없습니다.</td></tr>';
      watchlistEncounterStatus.textContent = `최근 조우 ${formatInteger(encounters.length)}건 표시`;
      return encounters;
    }

    function openWatchlistEditor(playerId) {
      const player = watchedPlayers.find((item) => Number(item.id) === Number(playerId));
      if (!player) throw new Error("밴 플레이어 정보를 찾을 수 없습니다.");
      watchlistEditTitle.textContent = `${player.current_name} 알림 설정`;
      watchlistEditIdentity.innerHTML = `<strong>${escapeHtml(player.current_name)}</strong> · ${escapeHtml(player.shard)}<br><span class="result-caption" title="${attr(player.account_id)}">${escapeHtml(player.account_id)}</span>`;
      watchlistEditForm.elements.shard.value = player.shard;
      watchlistEditForm.elements.account_id.value = player.account_id;
      watchlistEditForm.elements.active.value = String(Boolean(player.active));
      watchlistEditForm.elements.engagement_threshold.value = String(player.engagement_threshold || 3);
      watchlistEditForm.elements.notify_name_change.checked = Boolean(player.notify_name_change);
      watchlistEditForm.elements.notify_kill.checked = Boolean(player.notify_kill);
      watchlistEditForm.elements.notify_repeated_engagement.checked = Boolean(player.notify_repeated_engagement);
      watchlistEditGuildSelect.value = "";
      watchlistEditChannelIds = new Set((player.notification_channel_ids || []).map(String));
      renderWatchlistChannelSelection("edit");
      watchlistEditDialog.showModal();
    }

    async function saveWatchlistEditor() {
      const form = new FormData(watchlistEditForm);
      const shard = String(form.get("shard") || "");
      const accountId = String(form.get("account_id") || "");
      const payload = await requestJson(
        `/watchlist/players/${encodeURIComponent(shard)}/${encodeURIComponent(accountId)}`,
        "PUT",
        {
          active: form.get("active") === "true",
          notify_name_change: Boolean(form.get("notify_name_change")),
          notify_kill: Boolean(form.get("notify_kill")),
          notify_repeated_engagement: Boolean(form.get("notify_repeated_engagement")),
          engagement_threshold: Number(form.get("engagement_threshold") || 3),
          notification_channel_ids: Array.from(watchlistEditChannelIds),
        },
      );
      watchlistEditDialog.close();
      await Promise.all([loadWatchlistPlayers(), loadWatchlistEncounters()]);
      banner.textContent = `${payload.player.current_name} 밴 플레이어 설정 저장 완료`;
    }

    async function openWatchlistMatch(matchId, replay = false) {
      activateWorkspace("players", { focusId: "match-explorer", updateUrl: true, smooth: true });
      await loadMatchExplorerDetail(matchId);
      if (replay) await generateAndOpenMatchReplay();
    }

    function alertDisplayTitle(alert) {
      return String(alert?.display_title || alert?.title || "");
    }

    function alertDisplayMessage(alert) {
      return String(alert?.display_message || alert?.message || "");
    }

    function alertNoteTypeLabel(value) {
      return String(value || "") === "resolution" ? "해결 기록" : "메모";
    }

    function renderAlerts(alerts) {
      alertsBody.innerHTML = alerts.length
        ? alerts.map((alert) => `
          <tr>
            <td>${escapeHtml(alertSourceLabel(alert.source))}</td>
            <td>${alertSeverityBadge(alert.severity)}</td>
            <td>${escapeHtml(alertDisplayTitle(alert))}</td>
            <td>${escapeHtml(alertDisplayMessage(alert))}</td>
            <td>
              <div class="actions">
                ${alertWorkerRunButton(alert)}
                <button class="secondary" type="button" data-alert-detail-id="${attr(alert.id)}">상세</button>
                <button type="button" data-alert-action="acknowledge" data-alert-id="${attr(alert.id)}">확인</button>
                <button class="secondary" type="button" data-alert-action="snooze" data-alert-id="${attr(alert.id)}">1시간 숨김</button>
              </div>
            </td>
          </tr>
        `).join("")
        : `<tr><td colspan="5">현재 활성 경고가 없습니다.</td></tr>`;
    }

    function renderAlertHistory(history, page = {}, syncControls = false) {
      alertHistoryRecords = history;
      alertHistoryPage = {
        ...alertHistoryPage,
        ...page,
        limit: Number(page.limit || alertHistoryPage.limit || 20),
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
        alertHistoryFilterForm.elements.limit.value = String(alertHistoryPage.limit || 20);
      }
      const start = history.length ? alertHistoryPage.offset + 1 : 0;
      const end = history.length ? alertHistoryPage.offset + history.length : 0;
      alertHistoryStatus.textContent = [
        `전체 ${formatInteger(alertHistoryPage.total)}건 중 ${formatInteger(start)}-${formatInteger(end)}`,
        `위치 ${alertHistoryPage.source === "all" ? "전체" : alertSourceLabel(alertHistoryPage.source)}`,
        `상태 ${alertHistoryPage.state === "all" ? "전체" : alertStateFilterLabel(alertHistoryPage.state)}`,
        `심각도 ${alertHistoryPage.severity === "all" ? "전체" : alertSeverityLabel(alertHistoryPage.severity)}`,
        `정렬 ${alertHistoryPage.sort === "oldest" ? "오래된순" : (alertHistoryPage.sort === "severity" ? "심각도 우선" : "최신순")}`,
        `검색 ${alertHistoryPage.search ? `"${alertHistoryPage.search}"` : "없음"}`,
      ].join(" · ");
      alertHistoryPrev.disabled = !alertHistoryPage.has_previous;
      alertHistoryNext.disabled = !alertHistoryPage.has_next;
      alertHistoryBody.innerHTML = history.length
        ? history.map((alert) => `
          <tr>
            <td>${escapeHtml(formatKstShort(alert.last_seen_at_kst))}</td>
            <td>
              <div class="table-badge-stack">
                <span>${escapeHtml(alertSourceLabel(alert.source))}</span>
                ${alert.resolved_at_kst
                  ? `<span class="historical-severity">과거 ${escapeHtml(alertSeverityLabel(alert.severity))}</span>`
                  : alertSeverityBadge(alert.severity)}
              </div>
            </td>
            <td>${escapeHtml(alertDisplayTitle(alert))}</td>
            <td>
              <div class="table-badge-stack">
                ${alertStateBadge(alert)}
                <span class="status">${escapeHtml(formatAlertHistoryStatus(alert))}</span>
              </div>
            </td>
            <td>${escapeHtml(alertHistoryNoteSummary(alert))}</td>
            <td>${escapeHtml(alertDisplayMessage(alert))}</td>
            <td>
              <div class="actions">
                ${alertWorkerRunButton(alert)}
                <button class="secondary" type="button" data-alert-detail-id="${attr(alert.id)}">상세</button>
                <button type="button" data-alert-note-type="note" data-alert-id="${attr(alert.id)}">메모</button>
                <button class="secondary" type="button" data-alert-note-type="resolution" data-alert-id="${attr(alert.id)}">해결 기록</button>
              </div>
            </td>
          </tr>
        `).join("")
        : `<tr><td colspan="7">저장된 알림 이력이 없습니다.</td></tr>`;
      alertHistoryCards.innerHTML = history.length
        ? history.map((alert) => `
          <article class="dense-card">
            <div class="dense-card-head">
              <strong>${escapeHtml(alertDisplayTitle(alert) || "제목 없음")}</strong>
              ${alertStateBadge(alert)}
            </div>
            <div class="dense-card-row"><span>최근 발생</span><strong>${escapeHtml(formatKstShort(alert.last_seen_at_kst))}</strong></div>
            <div class="dense-card-row"><span>위치 / 심각도</span><strong>${escapeHtml(alertSourceLabel(alert.source))} · ${escapeHtml(alertSeverityLabel(alert.severity))}</strong></div>
            <div class="dense-card-row"><span>내용</span><strong>${escapeHtml(alertDisplayMessage(alert) || "-")}</strong></div>
            <div class="dense-card-actions">
              ${alertWorkerRunButton(alert)}
              <button class="secondary" type="button" data-alert-detail-id="${attr(alert.id)}">상세</button>
              <button type="button" data-alert-note-type="note" data-alert-id="${attr(alert.id)}">메모</button>
              <button class="secondary" type="button" data-alert-note-type="resolution" data-alert-id="${attr(alert.id)}">해결 기록</button>
            </div>
          </article>
        `).join("")
        : `<div class="dense-card"><span class="status">저장된 알림 이력이 없습니다.</span></div>`;
      if (activeAlertHistoryDetailId) {
        const selected = history.find((alert) => String(alert.id) === String(activeAlertHistoryDetailId));
        if (selected) activeAlertHistoryDetailAlert = selected;
      }
    }

    function alertHistoryState(alert) {
      if (alert.resolved_at_kst) {
        return {
          state: "resolved",
          label: "해결됨",
          timeLabel: "해결 시각",
          timeValue: alert.resolved_at_kst || "",
          helper: "현재 저장소 또는 자동 작업 점검에서는 더 이상 발생하지 않는 경고입니다.",
        };
      }
      if (alert.is_acknowledged) {
        return {
          state: "acknowledged",
          label: "확인함",
          timeLabel: "확인 시각",
          timeValue: alert.acknowledged_at_kst || "",
          helper: "이 경고가 해결된 뒤 다시 나타날 때까지 반복 알림을 보내지 않습니다.",
        };
      }
      if (alert.is_snoozed) {
        return {
          state: "snoozed",
          label: "숨김",
          timeLabel: "숨김 종료",
          timeValue: alert.snoozed_until_kst || "",
          helper: "설정한 숨김 시각까지 알림을 잠시 표시하지 않습니다.",
        };
      }
      return {
        state: "active",
        label: "활성",
        timeLabel: "최근 발생",
        timeValue: alert.last_seen_at_kst || "",
        helper: "현재 발생 중이며 관리자에게 알림을 보낼 수 있습니다.",
      };
    }

    function alertStateBadge(alert) {
      const state = alertHistoryState(alert);
      return `<span class="alert-state-badge alert-state-${attr(state.state)}">${escapeHtml(state.label)}</span>`;
    }

    function alertSeverityBadge(severity) {
      const level = alertSeverityLevel(severity);
      return `<span class="alert-severity-badge alert-severity-${attr(level)}">${escapeHtml(alertSeverityLabel(level))}</span>`;
    }

    function alertSeverityLabel(severity) {
      return {
        error: "오류",
        warning: "주의",
        info: "정보",
        ok: "정상",
        unknown: "알 수 없음",
      }[alertSeverityLevel(severity)] || "알 수 없음";
    }

    function alertSourceLabel(source) {
      return { storage: "저장소", worker: "자동 작업", watchlist: "밴 플레이어" }[String(source || "")] || String(source || "-");
    }

    function alertStateFilterLabel(state) {
      return {
        current: "현재 항목",
        active: "활성",
        acknowledged: "확인함",
        snoozed: "숨김",
        resolved: "해결됨",
      }[String(state || "")] || String(state || "-");
    }

    function alertSeverityLevel(severity) {
      const level = String(severity || "unknown").toLowerCase();
      return ["error", "warning", "info", "ok"].includes(level) ? level : "unknown";
    }

    function formatAlertHistoryStatus(alert) {
      const state = alertHistoryState(alert);
      return state.timeValue ? `${state.label} · ${formatKstShort(state.timeValue)}` : state.label;
    }

    function alertHistoryNoteSummary(alert) {
      const count = Number(alert.note_count || 0);
      if (!count) return "-";
      const note = alert.latest_note ? `: ${alert.latest_note}` : "";
      const type = alert.latest_note_type === "resolution" ? "해결 기록" : "메모";
      return `${formatInteger(count)}개 ${type}${note}`;
    }

    function alertWorkerRunButton(alert) {
      const runId = alertWorkerRunId(alert);
      return runId
        ? `<button class="secondary" type="button" data-worker-run-from-alert="${attr(runId)}">작업 이력</button>`
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
      openAlertHistoryDialog();
      activeAlertHistoryDetailId = alert.id;
      activeAlertHistoryDetailAlert = alert;
      activeAlertHistoryNoteType = noteType === "resolution" ? "resolution" : "note";
      alertHistoryDetail.innerHTML = `<div class="status">알림 #${escapeHtml(alert.id)}의 메모를 불러오는 중...</div>`;
      const payload = await requestJson(`/alerts/history/${encodeURIComponent(alert.id)}/notes`, "GET");
      if (payload.detail) throw new Error(payload.detail);
      renderAlertHistoryDetail(alert, payload.notes || []);
      if (focusEditor) {
        const input = alertHistoryDetail.querySelector("textarea[name='note_text']");
        if (input) input.focus();
      }
    }

    async function loadAlertHistoryDetailById(alertId, noteType = activeAlertHistoryNoteType, focusEditor = false) {
      openAlertHistoryDialog();
      activeAlertHistoryDetailId = alertId;
      activeAlertHistoryNoteType = noteType === "resolution" ? "resolution" : "note";
      alertHistoryDetail.innerHTML = `<div class="status">알림 #${escapeHtml(alertId)} 상세를 불러오는 중...</div>`;
      const payload = await requestJson(`/alerts/history/${encodeURIComponent(alertId)}`, "GET");
      if (payload.detail) throw new Error(payload.detail);
      const alert = payload.alert;
      if (!alert) throw new Error("알림 이력 항목을 불러오지 못했습니다.");
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
    }

    function openAlertHistoryDialog() {
      if (!alertHistoryDialog.open) alertHistoryDialog.showModal();
    }

    function closeAlertHistoryDialog() {
      if (alertHistoryDialog.open) alertHistoryDialog.close();
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

      const source = alertHistoryUrlChoice(params.get("alert_history_source") || params.get("alert_source"), ["all", "storage", "worker", "watchlist"], "all");
      const state = alertHistoryUrlChoice(params.get("alert_history_state") || params.get("alert_state"), ["all", "active", "current", "acknowledged", "snoozed", "resolved"], "all");
      const severity = alertHistoryUrlChoice(params.get("alert_history_severity") || params.get("alert_severity"), ["all", "error", "warning", "info", "ok"], "all");
      const sort = alertHistoryUrlChoice(params.get("alert_history_sort") || params.get("alert_sort"), ["newest", "oldest", "severity"], "newest");
      const search = String(params.get("alert_history_search") || params.get("alert_search") || "");
      const limit = alertHistoryUrlBoundedNumber(params.get("alert_history_limit"), 20, 1, 200);
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
      const limit = Number(form.get("limit") || alertHistoryPage.limit || 20);
      url.searchParams.delete("alert_id");
      url.searchParams.delete("alert");
      url.searchParams.set("alert_history_source", source);
      url.searchParams.set("alert_history_state", state);
      url.searchParams.set("alert_history_severity", severity);
      url.searchParams.set("alert_history_sort", sort);
      url.searchParams.set("alert_history_search", search);
      url.searchParams.set("alert_history_limit", String(limit || 20));
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
            <td>${escapeHtml(alertNoteTypeLabel(note.note_type))}</td>
            <td>${escapeHtml(note.created_by || "")}</td>
            <td>${escapeHtml(note.note_text || "")}</td>
          </tr>
        `).join("")
        : `<tr><td colspan="4">저장된 메모 또는 해결 기록이 없습니다.</td></tr>`;
      alertHistoryDetail.innerHTML = `
        <div class="recommendation-line">
          <strong>알림 #${escapeHtml(alert.id)} 상세</strong>
          <div class="actions">
            ${alertWorkerRunButton(alert)}
            <button type="button" data-alert-detail-action="acknowledge" data-alert-id="${attr(alert.id)}">확인</button>
            <button class="secondary" type="button" data-alert-detail-action="snooze" data-alert-id="${attr(alert.id)}">1시간 숨김</button>
          </div>
        </div>
        <div class="status" style="margin-top: 6px;">메모 ${formatInteger(notes.length)}개 표시</div>
        <div class="alert-state-row">
          ${alertStateBadge(alert)}
          <span class="status">${escapeHtml(state.timeLabel)}${state.timeValue ? `: ${escapeHtml(state.timeValue)}` : ""}</span>
        </div>
        <div class="status" style="margin-top: 4px;">${escapeHtml(state.helper)}</div>
        <div class="grid" style="margin-top: 10px;">
          ${cell("발생 위치", escapeHtml(alertSourceLabel(alert.source)))}
          ${cell("심각도", alertSeverityBadge(alert.severity))}
          ${cell("상태", escapeHtml(state.label))}
          ${cell("최근 발생", escapeHtml(alert.last_seen_at_kst || ""))}
        </div>
        <form class="detail-note-form" data-alert-note-form data-alert-id="${attr(alert.id)}">
          <label>종류
            <select name="note_type">
              <option value="note"${selectedNote === "note" ? " selected" : ""}>메모</option>
              <option value="resolution"${selectedNote === "resolution" ? " selected" : ""}>해결 기록</option>
            </select>
          </label>
          <label>내용
            <textarea name="note_text" required placeholder="알림 메모 또는 해결 내용을 입력하세요"></textarea>
          </label>
          <button type="submit">저장</button>
        </form>
        <table class="detail-table">
          <tbody>
            <tr><th>제목</th><td>${escapeHtml(alertDisplayTitle(alert))}</td></tr>
            <tr><th>내용</th><td>${escapeHtml(alertDisplayMessage(alert))}</td></tr>
          </tbody>
        </table>
        <table class="detail-table">
          <thead>
            <tr>
              <th>작성 시각</th>
              <th>종류</th>
              <th>작성자</th>
              <th>내용</th>
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
        discord_channel_ids: Array.from(activeAlertChannelIds),
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

    function discordBotStateLabel(value) {
      return {
        stopped: "중지",
        starting: "시작 중",
        connecting: "연결 중",
        running: "실행 중",
        reconnecting: "재연결 중",
        stopping: "종료 중",
        error: "오류",
      }[String(value || "")] || String(value || "-");
    }

    function renderDiscordBotState(payload = {}) {
      activeDiscordBotState = payload.bot || activeDiscordBotState;
      const running = Boolean(activeDiscordBotState.running);
      const managedGuildCount = (activeDiscordBotSettings.managed_guild_ids || []).length;
      const visibleGuildCount = running
        ? Number(activeDiscordBotState.guild_count || 0)
        : managedGuildCount;
      discordBotState.textContent = discordBotStateLabel(activeDiscordBotState.state);
      discordBotUser.textContent = activeDiscordBotState.bot_user
        || activeDiscordBotSettings.managed_bot_username
        || "-";
      discordBotUser.title = activeDiscordBotState.bot_user_id
        || activeDiscordBotSettings.managed_bot_user_id
        || "";
      discordBotGuildCount.textContent = `${formatInteger(visibleGuildCount)}개`;
      discordBotLastSync.textContent = activeDiscordBotState.last_sync_at_kst
        ? formatKstShort(activeDiscordBotState.last_sync_at_kst)
        : "-";
      discordBotStart.disabled = running;
      discordBotStop.disabled = !running;
      discordBotSyncAll.disabled = !activeDiscordBotState.ready;
      if (Object.prototype.hasOwnProperty.call(payload, "pubg_api_configured")) {
        pubgApiKeyStatus.textContent = payload.pubg_api_configured ? "설정됨" : "설정되지 않음";
      }
      if (Object.prototype.hasOwnProperty.call(payload, "configured")) {
        discordTokenStatus.textContent = payload.configured ? "설정됨" : "설정되지 않음";
      }
      const statusParts = [
        discordBotStateLabel(activeDiscordBotState.state),
        `접두사 ${activeDiscordBotSettings.command_prefix || "!"}`,
        `관리 서버 ${formatInteger(visibleGuildCount)}개`,
      ];
      if (activeDiscordBotState.last_error) statusParts.push(`오류: ${activeDiscordBotState.last_error}`);
      discordBotStatus.textContent = statusParts.join(" · ");
      if (running || Object.prototype.hasOwnProperty.call(payload, "configured")) {
        setRailStatus(
          railDiscord,
          running ? "봇 실행 중" : (payload.configured ? "토큰 설정됨" : "토큰 없음"),
          running || payload.configured ? "ok" : "error",
        );
      }
    }

    function applyDiscordBotSettingsForm() {
      discordBotSettingsForm.elements.auto_start.checked = Boolean(activeDiscordBotSettings.auto_start);
      discordBotSettingsForm.elements.command_prefix.value = activeDiscordBotSettings.command_prefix || "!";
    }

    async function loadDiscordBot() {
      const [statusPayload, settingsPayload] = await Promise.all([
        requestJson("/discord/bot/status", "GET"),
        requestJson("/discord/bot/settings", "GET"),
      ]);
      activeDiscordBotSettings = settingsPayload.discord_bot || statusPayload.settings || activeDiscordBotSettings;
      if (!activeDiscordBotSettings.guild_enabled_commands) activeDiscordBotSettings.guild_enabled_commands = {};
      if ((settingsPayload.command_catalog || []).length) {
        activeDiscordCommandCatalog = settingsPayload.command_catalog;
      }
      applyDiscordBotSettingsForm();
      renderDiscordBotState(statusPayload);
      loadDiscordBotGuildSelection();
    }

    async function saveActiveDiscordBotSettings(settings = activeDiscordBotSettings) {
      const payload = await postJson("/discord/bot/settings", {
        auto_start: Boolean(settings.auto_start),
        command_prefix: String(settings.command_prefix || "!"),
        guild_enabled_commands: settings.guild_enabled_commands || {},
      });
      activeDiscordBotSettings = payload.discord_bot;
      applyDiscordBotSettingsForm();
      renderDiscordBotState({ bot: payload.bot });
      return payload;
    }

    function loadDiscordBotGuildSelection() {
      const guildId = String(discordBotGuildSelect.value || "");
      const configured = activeDiscordBotSettings.guild_enabled_commands || {};
      if (!guildId) {
        selectedDiscordBotCommands = new Set();
        discordBotGuildUsesDefault = true;
        renderDiscordBotCommandCatalog();
        return;
      }
      discordBotGuildUsesDefault = !Object.prototype.hasOwnProperty.call(configured, guildId);
      selectedDiscordBotCommands = new Set(
        discordBotGuildUsesDefault
          ? activeDiscordCommandCatalog.map((command) => command.name)
          : configured[guildId] || [],
      );
      renderDiscordBotCommandCatalog();
    }

    function renderDiscordBotCommandCatalog() {
      const guildId = String(discordBotGuildSelect.value || "");
      const search = String(discordBotCommandSearch.value || "").trim().toLocaleLowerCase();
      const commands = activeDiscordCommandCatalog.filter((command) => (
        !search
        || [command.name, command.label, command.description, ...(command.aliases || [])]
          .some((value) => String(value || "").toLocaleLowerCase().includes(search))
      ));
      discordBotCommandCatalog.innerHTML = guildId
        ? commands.map((command) => `
          <label class="command-choice">
            <input type="checkbox" value="${attr(command.name)}" ${selectedDiscordBotCommands.has(command.name) ? "checked" : ""}>
            <span>
              <strong>${escapeHtml(command.label)} · /${escapeHtml(command.name)}</strong>
              <small>${escapeHtml(command.description)}</small>
            </span>
          </label>
        `).join("") || '<span class="result-caption">조건에 맞는 명령이 없습니다.</span>'
        : '<span class="result-caption">Discord 서버를 선택하세요.</span>';
      discordBotCommandsSave.disabled = !guildId;
      discordBotCommandsVerify.disabled = !guildId || !activeDiscordBotState.ready;
      discordBotCommandsStatus.textContent = guildId
        ? [
            discordGuildName(guildId),
            discordBotGuildUsesDefault ? "기본값 · 전체 공개" : "사용자 설정",
            `선택 ${formatInteger(selectedDiscordBotCommands.size)}/${formatInteger(activeDiscordCommandCatalog.length)}개`,
            `검색 결과 ${formatInteger(commands.length)}개`,
          ].join(" · ")
        : "서버를 선택하세요.";
    }

    async function verifyDiscordBotGuildCommands() {
      const guildId = String(discordBotGuildSelect.value || "");
      if (!guildId) return null;
      if (!activeDiscordBotState.ready) {
        discordBotCommandsStatus.textContent = "봇을 실행한 뒤 Discord 공개 상태를 확인할 수 있습니다.";
        return null;
      }
      discordBotCommandsVerify.disabled = true;
      discordBotCommandsStatus.textContent = "Discord의 실제 공개 명령 목록을 확인하는 중입니다.";
      try {
        const payload = await requestJson(`/discord/bot/guild-commands/${encodeURIComponent(guildId)}`, "GET");
        const globalCount = payload.global_commands?.length || 0;
        const visibleCount = payload.actual_commands?.length || 0;
        const expectedCount = payload.expected_commands?.length || 0;
        if (payload.in_sync === true) {
          discordBotCommandsStatus.textContent = `${discordGuildName(guildId)} · 설정 ${formatInteger(expectedCount)}개 · Discord 공개 ${formatInteger(visibleCount)}개 · 일치`;
        } else if (globalCount) {
          discordBotCommandsStatus.textContent = `${discordGuildName(guildId)} · 전역 잔존 명령 ${formatInteger(globalCount)}개 때문에 서버별 공개 설정과 다릅니다. 저장 후 동기화를 실행하세요.`;
        } else if (payload.actual_commands) {
          discordBotCommandsStatus.textContent = `${discordGuildName(guildId)} · 설정 ${formatInteger(expectedCount)}개 / Discord 공개 ${formatInteger(visibleCount)}개 · 불일치`;
        } else {
          discordBotCommandsStatus.textContent = `Discord 공개 상태 확인 실패: ${payload.verification_error || "알 수 없는 오류"}`;
        }
        return payload;
      } finally {
        discordBotCommandsVerify.disabled = !activeDiscordBotState.ready || !discordBotGuildSelect.value;
      }
    }

    function discordGroupLabel(group) {
      return activeDiscordGroupLabels[group] || group;
    }

    function discordMemberOptionValue(member) {
      const username = member.username && member.username !== member.display_name
        ? ` · @${member.username}`
        : "";
      return `${member.display_name || member.username || "이름 없음"}${username} · ID …${String(member.user_id).slice(-6)}`;
    }

    function storedDiscordMemberLabel(userId, preferredGuildId = "") {
      const labels = activeDiscordPermissions.guild_member_labels || {};
      if (preferredGuildId && labels[preferredGuildId]?.[userId]) {
        return labels[preferredGuildId][userId];
      }
      for (const guildLabels of Object.values(labels)) {
        if (guildLabels?.[userId]) return guildLabels[userId];
      }
      return "";
    }

    function selectDiscordMemberFromInput(form, input) {
      const value = String(input.value || "").trim();
      const catalog = discordMemberCatalogByForm.get(form) || [];
      const selected = catalog.find((member) => member.option_value === value);
      form.elements.user_id.value = selected?.user_id || "";
      return selected || null;
    }

    function resolveDiscordMemberSelection(form, input) {
      const selected = selectDiscordMemberFromInput(form, input);
      if (selected) return selected;
      const directId = String(input.value || "").trim();
      if (/^\\d{1,32}$/.test(directId)) {
        form.elements.user_id.value = directId;
        return {
          user_id: directId,
          display_name: storedDiscordMemberLabel(directId) || "ID 직접 입력 사용자",
        };
      }
      throw new Error("Discord 멤버 검색 목록에서 사용자를 선택하세요. 필요한 경우 사용자 ID를 직접 입력할 수도 있습니다.");
    }

    async function loadDiscordMembersForForm(form, guildSelect, input, datalist, status, search = "") {
      const guildId = String(guildSelect.value || "").trim();
      if (!guildId) {
        discordMemberCatalogByForm.set(form, []);
        datalist.innerHTML = "";
        form.elements.user_id.value = "";
        status.textContent = "사용자 소속 서버를 선택하세요.";
        return [];
      }
      const params = new URLSearchParams({ guild_id: guildId, limit: "100" });
      const normalizedSearch = String(search || "").trim();
      if (normalizedSearch) params.set("search", normalizedSearch);
      status.textContent = `${discordGuildName(guildId)} 멤버를 조회하는 중입니다.`;
      try {
        const payload = await requestJson(`/discord/guild-members?${params.toString()}`, "GET");
        const members = (payload.members || []).map((member) => ({
          ...member,
          option_value: discordMemberOptionValue(member),
        }));
        discordMemberCatalogByForm.set(form, members);
        datalist.innerHTML = members.map((member) => (
          `<option value="${attr(member.option_value)}" label="${attr(member.nickname ? `서버 닉네임 · ${member.username}` : member.username)}"></option>`
        )).join("");
        selectDiscordMemberFromInput(form, input);
        status.textContent = normalizedSearch
          ? `검색 결과 ${formatInteger(members.length)}명 · 목록에서 사용자를 선택하세요.`
          : `${discordGuildName(guildId)} 멤버 ${formatInteger(members.length)}명 · 닉네임 일부를 입력해 검색할 수 있습니다.`;
        return members;
      } catch (error) {
        discordMemberCatalogByForm.set(form, []);
        datalist.innerHTML = "";
        form.elements.user_id.value = "";
        status.textContent = `멤버 조회 실패: ${error.message}`;
        return [];
      }
    }

    function setupDiscordMemberPicker(form, guildSelect, input, datalist, status) {
      guildSelect.addEventListener("change", () => {
        input.value = "";
        form.elements.user_id.value = "";
        void loadDiscordMembersForForm(form, guildSelect, input, datalist, status);
      });
      input.addEventListener("input", () => {
        if (selectDiscordMemberFromInput(form, input)) return;
        form.elements.user_id.value = "";
        const previous = discordMemberLoadTimerByForm.get(form);
        if (previous) clearTimeout(previous);
        const query = String(input.value || "").trim();
        if (query.length < 2) return;
        const timer = setTimeout(() => {
          void loadDiscordMembersForForm(form, guildSelect, input, datalist, status, query);
        }, 300);
        discordMemberLoadTimerByForm.set(form, timer);
      });
      input.addEventListener("change", () => selectDiscordMemberFromInput(form, input));
    }

    setupDiscordMemberPicker(
      discordGrantForm,
      discordGrantMemberGuild,
      discordGrantMemberQuery,
      discordGrantMemberOptions,
      discordGrantMemberStatus,
    );
    setupDiscordMemberPicker(
      discordAdminForm,
      discordAdminMemberGuild,
      discordAdminMemberQuery,
      discordAdminMemberOptions,
      discordAdminMemberStatus,
    );

    async function loadDiscordPermissions() {
      const response = await fetch("/discord/permissions");
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || response.statusText);
      const settings = payload.discord_permissions || {};
      activeDiscordPermissions = settings;
      activeDiscordCommandCatalog = payload.command_catalog || [];
      activeDiscordGroupLabels = payload.group_labels || {};
      reservedDiscordCommandGroups = new Set(payload.reserved_groups || []);
      const groupNames = Object.keys(settings.command_groups || {}).sort();
      discordPermissionGroup.innerHTML = groupNames.map((group) => (
        `<option value="${attr(group)}">${escapeHtml(discordGroupLabel(group))} · ${formatInteger(canonicalCommandsForGroup(group).length)}개 명령</option>`
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
          memberLabel: storedDiscordMemberLabel(userId),
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
            memberLabel: storedDiscordMemberLabel(userId),
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
              memberLabel: storedDiscordMemberLabel(userId, guildId),
            });
          }
        }
      }

      discordPermissionsBody.innerHTML = rows.map((row) => {
        const commandCount = row.group === "all" ? activeDiscordCommandCatalog.length : canonicalCommandsForGroup(row.group).length;
        const scopeLabel = row.scope === "global_admin"
          ? "전역 관리자"
          : row.scope === "global"
            ? "전체 서버"
            : discordGuildName(row.guildId);
        return `
        <tr>
          <td>${escapeHtml(scopeLabel)}${row.guildId ? `<br><span class="result-caption">${escapeHtml(row.guildId)}</span>` : ""}</td>
          <td><strong>${escapeHtml(row.memberLabel || "닉네임 미확인")}</strong><br><span class="result-caption">ID ${escapeHtml(row.userId)}</span></td>
          <td><strong>${escapeHtml(row.group === "all" ? "모든 권한" : discordGroupLabel(row.group))}</strong>${row.group === "all" ? "" : `<br><span class="result-caption">키 ${escapeHtml(row.group)} · ${formatInteger(commandCount)}개 명령</span>`}</td>
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
      `;
      }).join("") || `<tr><td colspan="4">등록된 권한이 없습니다.</td></tr>`;
      discordCommandAliasTarget.innerHTML = activeDiscordCommandCatalog.map((command) => (
        `<option value="${attr(command.name)}">${escapeHtml(command.label)} · /${escapeHtml(command.name)}</option>`
      )).join("");
      renderDiscordCommandCatalog();
      renderDiscordCommandGroups();
      renderDiscordCommandAliases();
    }

    function canonicalCommandsForGroup(group) {
      const names = new Set(activeDiscordPermissions.command_groups?.[group] || []);
      return activeDiscordCommandCatalog.filter((command) => (
        names.has(command.name) || (command.aliases || []).some((alias) => names.has(alias))
      ));
    }

    function commandSearchText(command) {
      return [
        command.name,
        command.label,
        command.description,
        command.permission_group,
        ...(command.aliases || []),
      ].join(" ").toLocaleLowerCase("ko-KR");
    }

    function renderDiscordCommandCatalog() {
      const query = String(discordCommandSearch.value || "").trim().toLocaleLowerCase("ko-KR");
      const commands = activeDiscordCommandCatalog.filter((command) => (
        !query || commandSearchText(command).includes(query)
      ));
      discordCommandCatalog.innerHTML = commands.map((command) => `
        <label class="command-choice">
          <input
            type="checkbox"
            value="${attr(command.name)}"
            ${selectedDiscordGroupCommands.has(command.name) ? "checked" : ""}
          >
          <span>
            <strong>${escapeHtml(command.label)} · /${escapeHtml(command.name)}</strong>
            <small>${escapeHtml(command.description)}${(command.aliases || []).length ? ` · 접두사 별칭 ${escapeHtml(command.aliases.join(", "))}` : ""}</small>
          </span>
        </label>
      `).join("") || '<span class="result-caption">조건에 맞는 명령이 없습니다.</span>';
      discordCommandGroupStatus.textContent = `전체 ${formatInteger(activeDiscordCommandCatalog.length)}개 · 선택 ${formatInteger(selectedDiscordGroupCommands.size)}개 · 표시 ${formatInteger(commands.length)}개`;
    }

    function discordGroupGrantCount(group) {
      let count = Object.values(activeDiscordPermissions.user_grants || {})
        .filter((groups) => groups.includes(group)).length;
      for (const grants of Object.values(activeDiscordPermissions.guild_user_grants || {})) {
        count += Object.values(grants).filter((groups) => groups.includes(group)).length;
      }
      return count;
    }

    function renderDiscordCommandGroups() {
      const entries = Object.keys(activeDiscordPermissions.command_groups || {}).sort((left, right) => (
        Number(reservedDiscordCommandGroups.has(right)) - Number(reservedDiscordCommandGroups.has(left))
        || left.localeCompare(right)
      ));
      discordCommandGroupsBody.innerHTML = entries.map((group) => {
        const commands = canonicalCommandsForGroup(group);
        const reserved = reservedDiscordCommandGroups.has(group);
        const commandChips = commands.map((command) => (
          `<span class="result-chip" title="${attr(command.description)}">${escapeHtml(command.label)}</span>`
        )).join("");
        return `
          <tr>
            <td><strong>${escapeHtml(discordGroupLabel(group))}</strong><br><span class="result-caption">키 ${escapeHtml(group)} · ${reserved ? "기본 · 읽기 전용" : "사용자 정의"}</span></td>
            <td><div class="result-chip-list">${commandChips || '<span class="result-caption">명령 없음</span>'}</div></td>
            <td>${formatInteger(discordGroupGrantCount(group))}명</td>
            <td><div class="actions">
              <button class="secondary" type="button" data-discord-group-action="${reserved ? "clone" : "edit"}" data-group="${attr(group)}">${reserved ? "복제" : "수정"}</button>
              ${reserved ? "" : `<button class="danger" type="button" data-discord-group-action="delete" data-group="${attr(group)}">삭제</button>`}
            </div></td>
          </tr>
        `;
      }).join("") || '<tr><td colspan="4">권한 그룹이 없습니다.</td></tr>';
    }

    function renderDiscordCommandAliases() {
      const aliases = Object.entries(activeDiscordPermissions.command_aliases || {})
        .sort(([left], [right]) => left.localeCompare(right));
      discordCommandAliasesBody.innerHTML = aliases.map(([alias, target]) => {
        const command = activeDiscordCommandCatalog.find((item) => item.name === target);
        return `
          <tr>
            <td><strong>${escapeHtml(alias)}</strong></td>
            <td>${escapeHtml(command?.label || target)} · <span class="result-caption">${escapeHtml(target)}</span></td>
            <td><div class="actions">
              <button class="secondary" type="button" data-discord-alias-action="edit" data-alias="${attr(alias)}" data-target="${attr(target)}">수정</button>
              <button class="danger" type="button" data-discord-alias-action="delete" data-alias="${attr(alias)}">삭제</button>
            </div></td>
          </tr>
        `;
      }).join("") || '<tr><td colspan="3">사용자 정의 접두사 별칭이 없습니다.</td></tr>';
    }

    function resetDiscordCommandGroupEditor() {
      discordCommandGroupForm.reset();
      discordCommandGroupForm.elements.group.readOnly = false;
      selectedDiscordGroupCommands = new Set();
      renderDiscordCommandCatalog();
    }

    function editDiscordCommandGroup(group, { clone = false } = {}) {
      const commands = canonicalCommandsForGroup(group).map((command) => command.name);
      selectedDiscordGroupCommands = new Set(commands);
      discordCommandGroupForm.elements.group.value = clone ? "" : group;
      discordCommandGroupForm.elements.group.readOnly = !clone;
      discordCommandSearch.value = "";
      renderDiscordCommandCatalog();
      discordCommandGroupForm.elements.group.focus();
    }

    function resetDiscordCommandAliasEditor() {
      discordCommandAliasForm.reset();
      discordCommandAliasForm.elements.alias.readOnly = false;
    }

    async function loadDiscordScopes() {
      const payload = await requestJson("/discord/scopes", "GET");
      activeDiscordScopes = payload.discord_scopes || {
        guild_ranking_scopes: {},
        guild_ranking_selected_guild_ids: {},
        public_profile_default: true,
        updated_at: null,
      };
      if (!activeDiscordScopes.guild_ranking_scopes) {
        activeDiscordScopes.guild_ranking_scopes = {};
      }
      if (!activeDiscordScopes.guild_ranking_selected_guild_ids) {
        activeDiscordScopes.guild_ranking_selected_guild_ids = {};
      }
      renderDiscordScopes();
      loadDiscordScopeSelection();
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

    function discordGuildName(guildId) {
      if (!guildId) return "전체 서버";
      const guild = activeDiscordGuilds.find((item) => item.guild_id === guildId);
      if (guild?.name) return guild.name;
      return `이름 미확인 서버 · ${String(guildId).slice(-6)}`;
    }

    function discordGuildOptionLabel(guild) {
      const name = guild.name || `이름 미확인 서버 · ${String(guild.guild_id).slice(-6)}`;
      const playerCount = Number(guild.registered_player_count || 0);
      const scope = guild.ranking_scope === "global" ? "전체 범위" : "서버 범위";
      return `${name} · 등록 ${formatInteger(playerCount)}명 · ${scope}`;
    }

    async function loadDiscordGuilds({ sync = false, pruneStale = false } = {}) {
      let syncResult = null;
      if (sync) {
        syncResult = await postJson("/discord/guilds/sync", {
          prune_stale: Boolean(pruneStale),
          confirmation: pruneStale ? "CONFIRM MANAGED DISCORD GUILD SYNC" : null,
        });
        if (syncResult.managed_bot) {
          activeDiscordBotSettings.managed_bot_user_id = syncResult.managed_bot.user_id;
          activeDiscordBotSettings.managed_bot_username = syncResult.managed_bot.username;
        }
      }
      const response = await fetch("/discord/guilds");
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || response.statusText);
      }
      activeDiscordGuilds = (await response.json()).guilds || [];
      for (const select of document.querySelectorAll("select.discord-guild-select, #rankingGuildSelect")) {
        let requested = select.value;
        if (select === rankingGuildSelect) requested ||= rankingGuildPrefill;
        if (select === discordGrantForm.elements.guild_id) requested ||= discordSettingsPrefill.permission_guild_id;
        if (select === discordScopeForm.elements.guild_id) requested ||= discordSettingsPrefill.scope_guild_id;
        const visibleGuilds = select === rankingGuildSelect
          ? activeDiscordGuilds.filter((guild) => Number(guild.registered_player_count || 0) > 0 || guild.guild_id === requested)
          : activeDiscordGuilds;
        const guildOptions = visibleGuilds.map((guild) => (
          `<option value="${attr(guild.guild_id)}">${escapeHtml(discordGuildOptionLabel(guild))}</option>`
        )).join("");
        const emptyLabel = select.dataset.emptyLabel || "전체 서버";
        select.innerHTML = `<option value="">${escapeHtml(emptyLabel)}</option>${guildOptions}`;
        if (visibleGuilds.some((guild) => guild.guild_id === requested)) {
          select.value = requested;
        }
      }
      rankingGuildPrefill = "";
      discordSettingsPrefill.permission_guild_id = "";
      discordSettingsPrefill.scope_guild_id = "";
      renderDiscordScopes();
      loadDiscordScopeSelection();
      loadDiscordBotGuildSelection();
      if (discordGrantMemberGuild.value) {
        void loadDiscordMembersForForm(
          discordGrantForm,
          discordGrantMemberGuild,
          discordGrantMemberQuery,
          discordGrantMemberOptions,
          discordGrantMemberStatus,
        );
      }
      if (discordAdminMemberGuild.value) {
        void loadDiscordMembersForForm(
          discordAdminForm,
          discordAdminMemberGuild,
          discordAdminMemberQuery,
          discordAdminMemberOptions,
          discordAdminMemberStatus,
        );
      }
      if (registeredPlayers.length) renderPlayersTable();
      return syncResult;
    }

    function renderDiscordScopes() {
      const entries = Object.entries(activeDiscordScopes.guild_ranking_scopes || {})
        .sort(([left], [right]) => left.localeCompare(right));
      discordScopesBody.innerHTML = entries.map(([guildId, scope]) => {
        const selectedGuildIds = activeDiscordScopes.guild_ranking_selected_guild_ids?.[guildId] || [guildId];
        const scopeLabel = scope === "global"
          ? "전체 서버"
          : selectedGuildIds.map((selectedGuildId) => discordGuildName(selectedGuildId)).join(", ");
        return `
        <tr>
          <td>${escapeHtml(discordGuildName(guildId))}<br><span class="result-caption">${escapeHtml(guildId)}</span></td>
          <td>${escapeHtml(scopeLabel)}${scope === "global" ? "" : `<br><span class="result-caption">${formatInteger(selectedGuildIds.length)}개 서버 포함</span>`}</td>
          <td>
            <div class="actions">
              <button
                class="danger"
                type="button"
                data-discord-scope-action="remove"
                data-guild-id="${attr(guildId)}"
              >삭제</button>
            </div>
          </td>
        </tr>
      `;
      }).join("") || `<tr><td colspan="3">별도 랭킹 범위 설정이 없습니다.</td></tr>`;
    }

    function loadDiscordScopeSelection() {
      const sourceGuildId = String(discordScopeForm.elements.guild_id.value || "");
      const scope = activeDiscordScopes.guild_ranking_scopes?.[sourceGuildId] || "guild";
      discordScopeMode.value = scope;
      const configured = activeDiscordScopes.guild_ranking_selected_guild_ids?.[sourceGuildId];
      editedDiscordRankingGuildIds = new Set(
        configured?.length ? configured.map(String) : (sourceGuildId ? [sourceGuildId] : []),
      );
      renderDiscordRankingGuildSelection();
    }

    function renderDiscordRankingGuildSelection() {
      const sourceGuildId = String(discordScopeForm.elements.guild_id.value || "");
      const selectedMode = discordScopeMode.value === "global" ? "global" : "guild";
      discordRankingGuildSelection.hidden = selectedMode === "global";
      discordRankingGuildCheckboxes.innerHTML = activeDiscordGuilds.map((guild) => `
        <label class="command-choice">
          <input type="checkbox" value="${attr(guild.guild_id)}" ${editedDiscordRankingGuildIds.has(String(guild.guild_id)) ? "checked" : ""}>
          <span>
            <strong>${escapeHtml(guild.name || `이름 미확인 서버 · ${String(guild.guild_id).slice(-6)}`)}</strong>
            <small>등록 ${formatInteger(guild.registered_player_count || 0)}명 · ${escapeHtml(guild.guild_id)}</small>
          </span>
        </label>
      `).join("") || '<span class="result-caption">앱 봇이 참여 중인 Discord 서버가 없습니다.</span>';
      discordRankingGuildStatus.textContent = sourceGuildId
        ? `랭킹에 포함할 서버 ${formatInteger(editedDiscordRankingGuildIds.size)}개 선택`
        : "먼저 설정 대상 서버를 선택하세요.";
    }

    function applyPublicProfileDefault() {
      const value = String(activeDiscordScopes.public_profile_default !== false);
      publicProfileDefaultForm.elements.public_profile_default.value = value;
      registerForm.elements.public_profile.value = value;
    }

    async function saveDiscordScopes(nextScopes) {
      const payload = await postJson("/discord/scopes", {
        guild_ranking_scopes: nextScopes.guild_ranking_scopes || {},
        guild_ranking_selected_guild_ids: nextScopes.guild_ranking_selected_guild_ids
          || activeDiscordScopes.guild_ranking_selected_guild_ids
          || {},
        public_profile_default: nextScopes.public_profile_default !== false,
      });
      activeDiscordScopes = payload.discord_scopes;
      renderDiscordScopes();
      loadDiscordScopeSelection();
      applyPublicProfileDefault();
    }

    function resolveRegisteredPlayer(value, preferredShard = "") {
      const normalized = String(value || "").trim().toLocaleLowerCase();
      if (!normalized) return null;
      const exact = registeredPlayers.filter((player) => (
        String(player.current_name || "").toLocaleLowerCase() === normalized
        || String(player.account_id || "").toLocaleLowerCase() === normalized
      ));
      return exact.find((player) => !preferredShard || player.shard === preferredShard) || exact[0] || null;
    }

    function selectedRegisteredPlayer(formElement) {
      const input = formElement.elements.target;
      const requested = String(input?.value || "").trim();
      let player = resolveRegisteredPlayer(requested, formElement.elements.shard?.value || "");
      if (
        !player
        && !requested
        && activeAnalysisPlayer
        && analysisForms.includes(formElement)
        && (!formElement.elements.shard?.value || formElement.elements.shard.value === activeAnalysisPlayer.shard)
      ) {
        player = activeAnalysisPlayer;
      }
      if (!player) throw new Error("등록 유저 목록에서 닉네임을 선택하세요.");
      return player;
    }

    function renderAnalysisPlayerContext() {
      if (!activeAnalysisPlayer) {
        analysisPlayerContextName.textContent = "분석 대상을 선택하세요";
        analysisPlayerContextMeta.textContent = "한 번 선택하면 전적·추세·무기·추천·낙하·매치 탭에서 유지됩니다.";
        clearAnalysisPlayerButton.disabled = true;
        return;
      }
      analysisPlayerContextName.textContent = activeAnalysisPlayer.current_name;
      analysisPlayerContextMeta.textContent = [
        activeAnalysisPlayer.shard,
        activeAnalysisPlayer.active ? "수집 중" : "수집 중지",
        "Account " + String(activeAnalysisPlayer.account_id || "").slice(-12),
      ].join(" · ");
      clearAnalysisPlayerButton.disabled = false;
    }

    function renderRegisteredPlayerOptions() {
      registeredPlayerOptions.innerHTML = registeredPlayers
        .slice()
        .sort((left, right) => String(left.current_name).localeCompare(String(right.current_name)))
        .map((player) => {
          const label = displayCode(player.shard, "shard") + " · " + (player.active ? "수집중" : "수집중지") + " · " + String(player.account_id).slice(-8);
          return '<option value="' + attr(player.current_name) + '" label="' + attr(label) + '"></option>';
        })
        .join("");
      const selectedAccountId = replayArtifactPlayerSelect.value;
      replayArtifactPlayerSelect.innerHTML = [
        '<option value="">전체 등록 유저</option>',
        ...registeredPlayers
          .slice()
          .sort((left, right) => String(left.current_name).localeCompare(String(right.current_name)))
          .map((player) => {
            const state = player.active ? "수집 중" : "수집 중지";
            return `<option value="${attr(player.account_id)}">${escapeHtml(player.current_name)} · ${escapeHtml(displayCode(player.shard, "shard"))} · ${state}</option>`;
          }),
      ].join("");
      if ([...replayArtifactPlayerSelect.options].some((option) => option.value === selectedAccountId)) {
        replayArtifactPlayerSelect.value = selectedAccountId;
      }
      const selectedFlightAccountId = flightPathPlayerSelect.value;
      flightPathPlayerSelect.innerHTML = replayArtifactPlayerSelect.innerHTML;
      for (const option of flightPathPlayerSelect.options) {
        const player = registeredPlayers.find((item) => item.account_id === option.value);
        if (player) option.dataset.shard = player.shard;
      }
      if ([...flightPathPlayerSelect.options].some((option) => option.value === selectedFlightAccountId)) {
        flightPathPlayerSelect.value = selectedFlightAccountId;
      }
    }

    function catalogOptionLabel(facet, value, catalog) {
      const field = {
        maps: "map_name",
        game_modes: "game_mode",
        team_modes: "team_mode",
        perspectives: "perspective",
        match_types: "match_type",
        season_states: "season_state",
      }[facet];
      const match = (catalog.matches || []).find((item) => item[field] === value);
      if (facet === "maps") return match?.map_name_ko || value;
      if (facet === "game_modes") return match?.game_mode_ko || displayCode(value, "game_mode");
      if (facet === "team_modes") return displayCode(value, "team_mode");
      if (facet === "perspectives") return displayCode(value, "perspective");
      if (facet === "match_types") return displayCode(value, "match_type");
      if (facet === "season_states") return displayCode(value, "season_state");
      return String(value);
    }

    function populateCatalogSelect(select, values, facet, catalog) {
      const current = select.value;
      const options = (values || []).map((value) => (
        '<option value="' + attr(value) + '">' + escapeHtml(catalogOptionLabel(facet, value, catalog)) + '</option>'
      )).join("");
      select.innerHTML = '<option value="">전체</option>' + options;
      if ([...select.options].some((option) => option.value === current)) select.value = current;
    }

    function matchOptionLabel(match) {
      const playedAt = String(match.created_at_kst || "-").replace("T", " ").slice(0, 16);
      const result = Number(match.win_place) === 1 ? "치킨" : "#" + (match.win_place || "-");
      return [
        playedAt,
        match.map_name_ko || displayCode(match.map_name, "map"),
        match.game_mode_ko || displayCode(match.game_mode, "game_mode"),
        result,
        formatInteger(match.kills) + "킬",
      ].join(" · ");
    }

    function renderMatchOptions(formElement, search = "") {
      const catalog = catalogByForm.get(formElement);
      const select = formElement.elements.match_id;
      if (!catalog || !select) return;
      const normalized = String(search || "").trim().toLocaleLowerCase();
      const matches = (catalog.matches || []).filter((match) => (
        !normalized
        || (matchOptionLabel(match) + " " + match.match_id).toLocaleLowerCase().includes(normalized)
      ));
      const current = select.value;
      select.innerHTML = matches.map((match) => (
        '<option value="' + attr(match.match_id) + '">' + escapeHtml(matchOptionLabel(match)) + '</option>'
      )).join("") || '<option value="">조건에 맞는 매치가 없습니다</option>';
      if (matches.some((match) => match.match_id === current)) select.value = current;
    }

    function applyPlayerCatalog(formElement, catalog) {
      catalogByForm.set(formElement, catalog);
      formElement.querySelectorAll("select[data-catalog-facet]").forEach((select) => {
        const facet = select.dataset.catalogFacet;
        populateCatalogSelect(select, catalog.facets?.[facet] || [], facet, catalog);
      });
      if (formElement === weaponForm) {
        const select = formElement.elements.weapon;
        const current = select.value;
        select.innerHTML = (catalog.weapons || []).map((weapon) => (
          '<option value="' + attr(weapon.weapon_code) + '">' + escapeHtml(
            weapon.weapon_name + " · " + weapon.weapon_family + " · " + weapon.match_count + "경기"
          ) + '</option>'
        )).join("") || '<option value="">사용 기록이 있는 무기가 없습니다</option>';
        if ((catalog.weapons || []).some((weapon) => weapon.weapon_code === current)) select.value = current;
      }
      if (formElement === matchForm) {
        renderMatchOptions(formElement, formElement.elements.match_search?.value || "");
      }
      if (formElement === comparisonForm) renderComparisonPicker(catalog);
    }

    async function loadPlayerCatalog(player) {
      const key = player.shard + ":" + player.account_id;
      if (!playerCatalogCache.has(key)) {
        const params = new URLSearchParams({
          shard: player.shard,
          account_id: player.account_id,
          match_limit: "5000",
        });
        const request = fetch("/players/catalog?" + params.toString()).then(async (response) => {
          if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: response.statusText }));
            throw new Error(error.detail || response.statusText);
          }
          return (await response.json()).catalog;
        }).catch((error) => {
          playerCatalogCache.delete(key);
          throw error;
        });
        playerCatalogCache.set(key, request);
      }
      return playerCatalogCache.get(key);
    }

    async function syncRegisteredPlayerForm(formElement) {
      const input = formElement.elements.target;
      if (!input) return;
      const player = resolveRegisteredPlayer(input.value, formElement.elements.shard?.value || "");
      if (!player) return;
      if (analysisForms.includes(formElement)) {
        await setActiveAnalysisPlayer(player);
        return player;
      }
      input.value = player.current_name;
      input.dataset.accountId = player.account_id;
      if (formElement.elements.shard) formElement.elements.shard.value = player.shard;
      return player;
    }

    async function setActiveAnalysisPlayer(player) {
      const refreshedPlayer = registeredPlayers.find((candidate) => (
        candidate.account_id === player.account_id && candidate.shard === player.shard
      )) || player;
      const selectionKey = refreshedPlayer.shard + ":" + refreshedPlayer.account_id;
      activeAnalysisPlayer = refreshedPlayer;
      for (const formElement of analysisForms) {
        const input = formElement.elements.target;
        if (input && formElement !== profileForm) {
          input.value = refreshedPlayer.current_name;
          input.dataset.accountId = refreshedPlayer.account_id;
        }
        if (formElement.elements.shard) formElement.elements.shard.value = refreshedPlayer.shard;
      }
      renderAnalysisPlayerContext();
      const catalog = await loadPlayerCatalog(refreshedPlayer);
      if (
        !activeAnalysisPlayer
        || activeAnalysisPlayer.shard + ":" + activeAnalysisPlayer.account_id !== selectionKey
      ) {
        return activeAnalysisPlayer;
      }
      for (const formElement of [intelligenceForm, trendForm, timeInsightForm, comparisonForm, weaponForm, matchForm]) {
        applyPlayerCatalog(formElement, catalog);
      }
      return refreshedPlayer;
    }

    async function initializeRegisteredPlayerForms() {
      if (activeAnalysisPlayer) {
        const refreshedPlayer = registeredPlayers.find((candidate) => (
          candidate.account_id === activeAnalysisPlayer.account_id
          && candidate.shard === activeAnalysisPlayer.shard
        ));
        if (refreshedPlayer) {
          await setActiveAnalysisPlayer(refreshedPlayer);
        } else {
          await clearAnalysisPlayerSelection();
        }
        return;
      }
      const prefilled = analysisForms.filter((formElement) => (
        String(formElement.elements.target?.value || "").trim()
      ));
      if (prefilled.length) await syncRegisteredPlayerForm(prefilled[0]);
    }

    function clearRegisteredPlayerSearch(formElement) {
      const input = formElement.elements.target;
      if (!input) return;
      input.value = "";
      delete input.dataset.accountId;
    }

    async function resetAnalysisForm(formElement, { preservePlayer = true } = {}) {
      const preservedWeapon = formElement === weaponForm
        ? String(formElement.elements.weapon?.value || "")
        : "";
      const preservedPlayer = preservePlayer && activeAnalysisPlayer && analysisForms.includes(formElement)
        ? activeAnalysisPlayer
        : null;
      const preservedPlayerKey = preservedPlayer
        ? preservedPlayer.shard + ":" + preservedPlayer.account_id
        : "";
      formElement.reset();
      for (const details of formElement.querySelectorAll("details")) details.open = false;
      catalogByForm.delete(formElement);

      if (formElement === intelligenceForm) {
        activeIntelligenceReport = null;
        activeIntelligenceView = "overview";
        intelligenceViewControls.hidden = true;
        intelligenceChartOptions.hidden = true;
        intelligenceBreakdownOption.hidden = true;
        intelligenceBody.textContent = "분석 대기 중";
      } else if (formElement === profileForm) {
        activeProfilePlayer = null;
        profileBody.textContent = "조회 대기 중";
      } else if (formElement === trendForm) {
        activeTrendReport = null;
        activeTrendView = "table";
        trendViewControls.hidden = true;
        trendChartPanel.hidden = true;
        trendTableWrap.hidden = false;
        trendCards.hidden = false;
        trendSummary.textContent = "조회 대기 중";
        trendBody.innerHTML = '<tr><td colspan="11">조회 대기 중</td></tr>';
        trendCards.innerHTML = "";
      } else if (formElement === timeInsightForm) {
        activeTimeInsightReport = null;
        timeInsightBody.textContent = "분석 대기 중";
      } else if (formElement === comparisonForm) {
        activeComparisonRows = [];
        activeComparisonView = "chart";
        comparisonBody.textContent = "비교 대기 중";
        comparisonItemPicker.innerHTML = '<span class="status">유형과 유저를 선택하세요.</span>';
        comparisonSelectionCount.textContent = "0/5";
        setComparisonView("chart");
      } else if (formElement === weaponForm) {
        activeWeaponDetail = null;
        activeWeaponTrendGranularity = "month";
        activeWeaponTrendMetric = "fight_win_rate";
        formElement.elements.weapon.innerHTML = '<option value="">유저를 먼저 선택하세요</option>';
        weaponBody.textContent = "조회 대기 중";
      } else if (formElement === recommendationForm) {
        activeRecommendationReport = null;
        recommendationBody.textContent = "조회 대기 중";
      } else if (formElement === dropZoneForm) {
        dropZoneBody.textContent = "조회 대기 중";
      } else if (formElement === matchForm) {
        formElement.elements.match_id.innerHTML = '<option value="">유저를 먼저 선택하세요</option>';
        formElement.elements.match_search.value = "";
        matchBody.textContent = "조회 대기 중";
      }

      if (preservedPlayer) {
        const input = formElement.elements.target;
        if (input) {
          input.value = preservedPlayer.current_name;
          input.dataset.accountId = preservedPlayer.account_id;
        }
        if (formElement.elements.shard) formElement.elements.shard.value = preservedPlayer.shard;
        if ([intelligenceForm, trendForm, timeInsightForm, comparisonForm, weaponForm, matchForm].includes(formElement)) {
          const catalog = await loadPlayerCatalog(preservedPlayer);
          if (
            !activeAnalysisPlayer
            || activeAnalysisPlayer.shard + ":" + activeAnalysisPlayer.account_id !== preservedPlayerKey
          ) {
            return;
          }
          applyPlayerCatalog(formElement, catalog);
        }
        if (
          formElement === weaponForm
          && preservedWeapon
          && [...formElement.elements.weapon.options].some((option) => option.value === preservedWeapon)
        ) {
          formElement.elements.weapon.value = preservedWeapon;
        }
      } else if (analysisForms.includes(formElement)) {
        clearRegisteredPlayerSearch(formElement);
      }
    }

    async function clearAnalysisPlayerSelection() {
      activeAnalysisPlayer = null;
      renderAnalysisPlayerContext();
      await Promise.all(analysisForms.map((formElement) => (
        resetAnalysisForm(formElement, { preservePlayer: false })
      )));
      renderComparisonPicker();
    }

    document.querySelectorAll('select[name="hour"]').forEach((select) => {
      const options = Array.from({ length: 24 }, (_, hour) => (
        '<option value="' + hour + '">' + String(hour).padStart(2, "0") + '시</option>'
      )).join("");
      select.insertAdjacentHTML("beforeend", options);
    });

    function playerDiscordRegistrations(player) {
      if (Array.isArray(player.discord_registrations)) return player.discord_registrations;
      if (!player.registered_guild_id) return [];
      return [{
        guild_id: player.registered_guild_id,
        channel_id: player.registered_channel_id || null,
        registered_by_discord_user_id: player.registered_by_discord_user_id || null,
        active: true,
      }];
    }

    function renderPlayersTable() {
      playersBody.innerHTML = registeredPlayers.map((player) => {
        const highlighted = Boolean(
          (registeredPlayerHighlight.account_id && player.account_id === registeredPlayerHighlight.account_id)
          || (
            registeredPlayerHighlight.name
            && player.current_name === registeredPlayerHighlight.name
            && (!registeredPlayerHighlight.shard || player.shard === registeredPlayerHighlight.shard)
          )
        );
        const registrations = playerDiscordRegistrations(player);
        const linkedGuilds = new Set(registrations.map((registration) => String(registration.guild_id)));
        const registrationChips = registrations.map((registration) => {
          const guildName = discordGuildName(registration.guild_id);
          const channel = registration.channel_id ? ` · 채널 ${registration.channel_id}` : "";
          return `<span class="result-chip discord-registration-chip">
            <span>${escapeHtml(guildName + channel)}</span>
            <button type="button" title="이 서버 등록 해제" aria-label="${attr(guildName)} 등록 해제" data-player-discord-remove data-shard="${attr(player.shard)}" data-account-id="${attr(player.account_id)}" data-guild-id="${attr(registration.guild_id)}">&times;</button>
          </span>`;
        }).join("") || '<span class="result-caption">Discord 미등록</span>';
        const availableGuilds = activeDiscordGuilds.filter((guild) => !linkedGuilds.has(String(guild.guild_id)));
        const guildOptions = availableGuilds.map((guild) => (
          `<option value="${attr(guild.guild_id)}">${escapeHtml(discordGuildOptionLabel(guild))}</option>`
        )).join("");
        const editorDisabled = availableGuilds.length ? "" : " disabled";
        return `
        <tr${highlighted ? ' class="linked-row"' : ""}>
          <td>
            <span class="registry-player-name">${escapeHtml(player.current_name)}</span>
            <span class="registry-player-meta">${escapeHtml(displayCode(player.shard, "shard"))} · ${escapeHtml(player.account_id)}</span>
          </td>
          <td><button class="${player.active ? "secondary" : ""} registry-state-button" type="button" data-player-management="active" data-next-value="${String(!player.active)}" data-shard="${attr(player.shard)}" data-account-id="${attr(player.account_id)}">${player.active ? "수집 중" : "수집 중지"}</button></td>
          <td><button class="${player.public_profile ? "secondary" : ""} registry-state-button" type="button" data-player-management="public_profile" data-next-value="${String(!player.public_profile)}" data-shard="${attr(player.shard)}" data-account-id="${attr(player.account_id)}">${player.public_profile ? "공개" : "비공개"}</button></td>
          <td>
            <div class="discord-registration-list">${registrationChips}</div>
            <details class="player-discord-editor">
              <summary>Discord 서버 연결 관리 · ${formatInteger(registrations.length)}곳</summary>
              <div class="player-discord-editor-grid">
                <label>추가할 서버<select data-player-discord-guild${editorDisabled}><option value="">${availableGuilds.length ? "서버 선택" : "추가 가능한 서버 없음"}</option>${guildOptions}</select></label>
                <label>채널 ID<input data-player-discord-channel autocomplete="off" inputmode="numeric" placeholder="선택 입력"${editorDisabled}></label>
                <button type="button" data-player-discord-add data-shard="${attr(player.shard)}" data-account-id="${attr(player.account_id)}"${editorDisabled}>서버 추가</button>
              </div>
            </details>
          </td>
        </tr>`;
      }).join("");
    }

    async function loadPlayers() {
      const payload = await requestJson("/players?active_only=false", "GET");
      registeredPlayers = payload.players || [];
      playerCatalogCache.clear();
      renderRegisteredPlayerOptions();
      renderPlayersTable();
      await initializeRegisteredPlayerForms();
      if (!activeAnalysisPlayer) renderComparisonPicker();
    }

    function dataDeletionActionButtons(request) {
      const buttons = [
        `<button class="secondary" type="button" data-deletion-action="detail" data-request-id="${attr(request.id)}">상세</button>`,
      ];
      if (request.status === "pending") {
        buttons.push(
          `<button type="button" data-deletion-action="approve" data-request-id="${attr(request.id)}">승인</button>`,
          `<button class="danger" type="button" data-deletion-action="reject" data-request-id="${attr(request.id)}">거절</button>`,
          `<button class="secondary" type="button" data-deletion-action="cancel" data-request-id="${attr(request.id)}">취소</button>`,
        );
      } else if (request.status === "approved") {
        buttons.push(
          `<button class="secondary" type="button" data-deletion-action="cancel" data-request-id="${attr(request.id)}">승인 취소</button>`,
        );
      }
      return buttons.join("");
    }

    function operationValueLabel(value) {
      const text = String(value ?? "");
      const labels = {
        pending: "대기", approved: "승인", rejected: "거절", cancelled: "취소",
        candidate: "삭제 후보", protected: "보호", preserved: "보존",
        complete: "완전", truncated: "일부만 표시", required: "필수", "not required": "선택",
        passed: "통과", pass: "통과", failed: "실패", fail: "실패", blocked: "차단",
        selectable: "선택 가능", absent: "없음", conflict: "충돌", removed: "정리 완료",
        "cleanup blocked": "정리 차단", none: "없음", unknown: "알 수 없음",
        unavailable: "사용 불가", immutable: "변경 불가", bound: "연결됨",
        matched: "일치", mismatch: "불일치", equivalent: "동일", different: "차이 있음",
        current: "최신", stale: "오래됨", "both current": "둘 다 최신",
        "not requested": "요청 안 함", yes: "예", no: "아니요",
        database: "데이터베이스", file: "파일", raw: "원본", replay: "리플레이",
      };
      return labels[text.toLowerCase()] || text || "-";
    }

    function operationBooleanLabel(value) {
      return value === true ? "예" : value === false ? "아니요" : "-";
    }

    function renderExportedReviewPacketVerification(verification) {
      const checks = verification?.checks || [];
      const checkRows = checks.map((check) => `
        <tr>
          <td>${escapeHtml(check.key)}</td>
          <td>${escapeHtml(check.status)}</td>
          <td>${escapeHtml(check.message)}</td>
        </tr>`).join("") || `<tr><td colspan="3">반환된 검증 항목이 없습니다.</td></tr>`;
      return `
        <div class="grid">
          <div class="kv"><span>검증</span><strong>${escapeHtml(operationValueLabel(verification?.verification_status))}</strong></div>
          <div class="kv"><span>판정</span><strong>${escapeHtml(operationValueLabel(verification?.review_status))}</strong></div>
          <div class="kv"><span>요청 / 계획 / 장애 행렬</span><strong>#${escapeHtml(verification?.request_id || "-")} / #${escapeHtml(verification?.dry_run_plan_id || "-")} / #${escapeHtml(verification?.fault_matrix_run_id || "-")}</strong></div>
          <div class="kv"><span>로컬 패킷 기록</span><strong>${verification?.matched_packet_id ? `#${escapeHtml(verification.matched_packet_id)}` : "일치 항목 없음"}</strong></div>
        </div>
        <div class="status">정규화 크기: ${formatBytes(Number(verification?.canonical_export_size_bytes || 0))} / SHA-256: <code>${escapeHtml(verification?.canonical_export_sha256 || "-")}</code> / 현재 DB와 일치: ${verification?.database_cross_check_requested ? operationBooleanLabel(verification.database_cross_check_passed) : "검사 안 함"} / 새 기록 생성: 아니요</div>
        <div class="table-scroll">
          <table class="detail-table">
            <thead><tr><th>검사</th><th>상태</th><th>내용</th></tr></thead>
            <tbody>${checkRows}</tbody>
          </table>
        </div>`;
    }

    async function readExportedReviewPacketFile(file, label) {
      if (!file) throw new Error(`${label} 파일이 필요합니다.`);
      if (file.size > 2097152) {
        throw new Error(`${label} 파일이 2 MiB 검증 제한을 초과했습니다.`);
      }
      const packetText = await file.text();
      if (new TextEncoder().encode(packetText).byteLength > 2097152) {
        throw new Error(`${label} 파일이 2 MiB 검증 제한을 초과했습니다.`);
      }
      return packetText;
    }

    async function verifyExportedReviewPacket(formElement) {
      const values = new FormData(formElement);
      const fileInput = formElement.elements.packet_file;
      const file = fileInput?.files?.[0] || null;
      const packetText = await readExportedReviewPacketFile(file, "패킷 JSON");
      const button = formElement.querySelector("button[type='submit']");
      if (button) button.disabled = true;
      exportedReviewPacketVerifierResult.innerHTML = "";
      exportedReviewPacketVerifierStatus.textContent = "메모리에서 검토 패킷을 검증하는 중...";
      try {
        const payload = await postJson("/data-deletion-review-packets/verify", {
          packet_text: packetText,
          cross_check_database: values.get("cross_check_database") === "on",
        });
        const verification = payload.verification || {};
        exportedReviewPacketVerifierResult.innerHTML = renderExportedReviewPacketVerification(verification);
        exportedReviewPacketVerifierStatus.textContent = `${operationValueLabel(verification.verification_status)} · 업로드한 내용은 저장하지 않았으며 승인, 실행 준비 및 실제 실행은 계속 비활성 상태입니다.`;
      } finally {
        if (button) button.disabled = false;
      }
    }

    function comparisonValueText(value) {
      if (value === null) return "null";
      if (value === undefined) return "-";
      if (typeof value === "string") return value;
      try {
        return JSON.stringify(value);
      } catch (_) {
        return String(value);
      }
    }

    function renderExportedReviewPacketComparison(comparison) {
      const differences = comparison?.differences || {};
      const metrics = comparison?.metrics || {};
      const baseline = comparison?.baseline_verification || {};
      const candidate = comparison?.candidate_verification || {};
      const databaseState = comparison?.database_cross_check_requested
        ? (comparison?.database_cross_check_passed ? "둘 다 최신" : "불일치")
        : "검사 안 함";
      const groupedDifferences = [
        ["입력 ID", differences.input_ids || []],
        ["지문값", differences.fingerprints || []],
        ["판정", differences.assessment || []],
      ];
      const contractRows = groupedDifferences.flatMap(([category, items]) => items.map((item) => `
        <tr>
          <td>${escapeHtml(category)}</td>
          <td>${escapeHtml(item.field || "-")}</td>
          <td class="comparison-value">${escapeHtml(comparisonValueText(item.baseline_value))}</td>
          <td class="comparison-value">${escapeHtml(comparisonValueText(item.candidate_value))}</td>
        </tr>`)).join("") || `<tr><td colspan="4">입력, 지문값 또는 판정 차이가 없습니다.</td></tr>`;
      const checkRows = (differences.review_checks || []).map((item) => `
        <tr>
          <td>${escapeHtml(item.key || "-")}</td>
          <td>${escapeHtml((item.changed_fields || []).join(", ") || "-")}</td>
          <td>${escapeHtml(item.baseline_status || "-")}</td>
          <td>${escapeHtml(item.candidate_status || "-")}</td>
          <td class="comparison-value">${escapeHtml(item.baseline_message || "-")}</td>
          <td class="comparison-value">${escapeHtml(item.candidate_message || "-")}</td>
        </tr>`).join("") || `<tr><td colspan="6">검토 항목 결과 차이가 없습니다.</td></tr>`;
      const canonicalRows = (differences.canonical_fields || []).map((item) => `
        <tr>
          <td><code>${escapeHtml(item.path || "-")}</code></td>
          <td>${escapeHtml(item.change_type || "-")}</td>
          <td class="comparison-value">${escapeHtml(comparisonValueText(item.baseline_value))}</td>
          <td class="comparison-value">${escapeHtml(comparisonValueText(item.candidate_value))}</td>
        </tr>`).join("") || `<tr><td colspan="4">정규화된 패킷 내용이 같습니다.</td></tr>`;
      const truncation = metrics.canonical_field_differences_truncated
        ? ` / 표시: ${formatInteger(metrics.reported_canonical_field_difference_count || 0)}개(일부만 표시)`
        : "";
      return `
        <div class="grid">
          <div class="kv"><span>비교 결과</span><strong>${escapeHtml(operationValueLabel(comparison?.comparison_status))}</strong></div>
          <div class="kv"><span>기준 패킷 검증</span><strong>${escapeHtml(operationValueLabel(baseline.verification_status))}</strong></div>
          <div class="kv"><span>비교 패킷 검증</span><strong>${escapeHtml(operationValueLabel(candidate.verification_status))}</strong></div>
          <div class="kv"><span>현재 MySQL</span><strong>${escapeHtml(databaseState)}</strong></div>
        </div>
        <div class="status">정규화 필드 변경 ${formatInteger(metrics.canonical_field_difference_count || 0)}개${truncation} / 입력 ID ${formatInteger(metrics.input_id_difference_count || 0)}개 / 지문값 ${formatInteger(metrics.fingerprint_difference_count || 0)}개 / 판정 ${formatInteger(metrics.assessment_difference_count || 0)}개 / 검사 ${formatInteger(metrics.review_check_difference_count || 0)}개 / 새 기록 생성 없음</div>
        <div class="status">비교 SHA-256: <code>${escapeHtml(comparison?.comparison_fingerprint_sha256 || "-")}</code></div>
        <h4>계약 및 판정 차이</h4>
        <div class="table-scroll">
          <table class="detail-table comparison-table comparison-contract-table">
            <thead><tr><th>분류</th><th>필드</th><th>기준</th><th>비교 대상</th></tr></thead>
            <tbody>${contractRows}</tbody>
          </table>
        </div>
        <h4>검토 항목 결과 차이</h4>
        <div class="table-scroll">
          <table class="detail-table comparison-table comparison-check-table">
            <thead><tr><th>검사</th><th>변경 필드</th><th>기준</th><th>비교 대상</th><th>기준 내용</th><th>비교 대상 내용</th></tr></thead>
            <tbody>${checkRows}</tbody>
          </table>
        </div>
        <h4>정규화 필드 차이</h4>
        <div class="table-scroll">
          <table class="detail-table comparison-table comparison-canonical-table">
            <thead><tr><th>JSON 경로</th><th>변경</th><th>기준</th><th>비교 대상</th></tr></thead>
            <tbody>${canonicalRows}</tbody>
          </table>
        </div>`;
    }

    async function compareExportedReviewPackets(formElement) {
      const values = new FormData(formElement);
      const baselineFile = formElement.elements.baseline_packet_file?.files?.[0] || null;
      const candidateFile = formElement.elements.candidate_packet_file?.files?.[0] || null;
      const [baselineText, candidateText] = await Promise.all([
        readExportedReviewPacketFile(baselineFile, "기준 JSON"),
        readExportedReviewPacketFile(candidateFile, "비교 대상 JSON"),
      ]);
      const button = formElement.querySelector("button[type='submit']");
      if (button) button.disabled = true;
      exportedReviewPacketComparerResult.innerHTML = "";
      exportedReviewPacketComparerStatus.textContent = "메모리에서 검증된 패킷을 비교하는 중...";
      try {
        const payload = await postJson("/data-deletion-review-packets/compare", {
          baseline_packet_text: baselineText,
          candidate_packet_text: candidateText,
          cross_check_database: values.get("cross_check_database") === "on",
        });
        const comparison = payload.comparison || {};
        exportedReviewPacketComparerResult.innerHTML = renderExportedReviewPacketComparison(comparison);
        exportedReviewPacketComparerStatus.textContent = `${operationValueLabel(comparison.comparison_status)} · 업로드 내용과 비교 결과는 저장하지 않았으며 승인, 실행 준비 및 실제 실행은 계속 비활성 상태입니다.`;
      } finally {
        if (button) button.disabled = false;
      }
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
            <td>${escapeHtml(operationValueLabel(request.deletion_scope))}</td>
            <td>${escapeHtml(operationValueLabel(request.status))}</td>
            <td>${escapeHtml(request.requested_at_kst)}</td>
            <td>${escapeHtml(request.expires_at_kst)}</td>
            <td><div class="actions">${dataDeletionActionButtons(request)}</div></td>
          </tr>`;
      }).join("") || `<tr><td colspan="7">삭제 검토 요청이 없습니다.</td></tr>`;
      dataDeletionStatus.textContent = `${formatInteger((payload.requests || []).length)}개 요청 · 승인은 실제 삭제를 실행하지 않습니다.`;
    }

    function dataDeletionPreviewRowRows(preview) {
      const candidateRows = (preview.row_impacts || []).map((row) => ({ ...row, preview_state: row.deletion_candidate ? "candidate" : "protected" }));
      const preservedRows = (preview.preserved_references || []).map((row) => ({ ...row, preview_state: "preserved" }));
      const rows = [...candidateRows, ...preservedRows];
      return rows.map((row) => `
        <tr>
          <td>${escapeHtml(row.table)}</td>
          <td>${escapeHtml(row.category)}</td>
          <td>${escapeHtml(row.relationship)}</td>
          <td>${formatInteger(row.row_count)}</td>
          <td>${escapeHtml(operationValueLabel(row.preview_state))}</td>
        </tr>`).join("") || `<tr><td colspan="5">이 범위에 해당하는 데이터베이스 행이 없습니다.</td></tr>`;
    }

    function dataDeletionPreviewFileRows(preview) {
      const files = [
        ...(preview.raw_files?.files || []).map((file) => ({ ...file, catalog_category: "raw" })),
        ...(preview.replay_files?.files || []).map((file) => ({ ...file, catalog_category: "replay" })),
      ];
      return files.map((file) => `
        <tr>
          <td>${escapeHtml(operationValueLabel(file.catalog_category))} / ${escapeHtml(file.file_type)}</td>
          <td>${escapeHtml(file.match_id)}</td>
          <td>${escapeHtml(operationValueLabel(file.ownership))}<br><small>${file.deletion_candidate ? "삭제 후보" : "보호"}</small></td>
          <td>${escapeHtml(operationValueLabel(file.verification_status))}<br><small>${formatBytes(file.declared_size_bytes || 0)}</small></td>
          <td>${escapeHtml(file.relative_path)}<br><small>${escapeHtml(file.resolved_path || "-")}</small></td>
        </tr>`).join("") || `<tr><td colspan="5">이 범위에 해당하는 파일이 없습니다.</td></tr>`;
    }

    function renderDataDeletionPreview(preview) {
      const verification = preview.verification || {};
      const catalogs = [preview.raw_files, preview.replay_files].filter((catalog) => catalog?.included);
      const catalogSummary = catalogs.map((catalog) => `
        <tr>
          <td>${escapeHtml(operationValueLabel(catalog.category))}</td>
          <td>${formatInteger(catalog.total_records)}</td>
          <td>${formatBytes(catalog.total_declared_size_bytes || 0)}</td>
          <td>${formatInteger(catalog.listed_records)}</td>
          <td>${operationBooleanLabel(catalog.truncated)}</td>
          <td>${formatInteger(catalog.shared_match_records)}</td>
        </tr>`).join("") || `<tr><td colspan="6">이 범위에 파일 카탈로그가 없습니다.</td></tr>`;
      return `
        <h3>읽기 전용 영향 미리보기</h3>
        <div class="status">
          생성 ${escapeHtml(preview.generated_at_kst)} / 매치 ${formatInteger(preview.matched_match_count)}개 /
          삭제 후보 행 ${formatInteger(preview.candidate_row_count)}개 / 실제 실행: 비활성
        </div>
        <table class="detail-table">
          <thead><tr><th>테이블</th><th>분류</th><th>관계</th><th>행</th><th>상태</th></tr></thead>
          <tbody>${dataDeletionPreviewRowRows(preview)}</tbody>
        </table>
        <h3>파일 카탈로그 요약</h3>
        <table class="detail-table">
          <thead><tr><th>저장소</th><th>전체</th><th>기록 크기</th><th>표시</th><th>일부만 표시</th><th>공유</th></tr></thead>
          <tbody>${catalogSummary}</tbody>
        </table>
        <div class="status">
          파일 시스템 문제 ${formatInteger(verification.filesystem_issue_count || 0)}건 /
          안전하지 않은 경로 ${formatInteger(verification.unsafe_path_count || 0)}건 /
          체크섬 검증: 수행 안 함
        </div>
        <table class="detail-table">
          <thead><tr><th>유형</th><th>매치</th><th>소유 관계</th><th>검증</th><th>경로</th></tr></thead>
          <tbody>${dataDeletionPreviewFileRows(preview)}</tbody>
        </table>
        <ul>${(preview.warnings || []).map((warning) => `<li>${escapeHtml(warning)}</li>`).join("")}</ul>`;
    }

    function renderDataDeletionConfirmationState(state) {
      const latest = state.latest_snapshot;
      const blockers = state.confirmation_blockers || [];
      const snapshotRows = (state.snapshots || []).map((snapshot) => `
        <tr>
          <td>${escapeHtml(snapshot.id)}</td>
          <td>${escapeHtml(snapshot.captured_at_kst)}</td>
          <td>${escapeHtml(snapshot.fingerprint_sha256)}</td>
          <td>${snapshot.catalog_complete ? "완전" : "일부만 표시"} / 문제 ${formatInteger(snapshot.filesystem_issue_count)}건</td>
        </tr>`).join("") || `<tr><td colspan="4">변경 불가 스냅샷이 없습니다.</td></tr>`;
      const confirmationRows = (state.confirmations || []).map((confirmation) => `
        <tr>
          <td>${escapeHtml(confirmation.id)}</td>
          <td>${escapeHtml(confirmation.preview_snapshot_id)}</td>
          <td>${escapeHtml(confirmation.confirmed_by)}</td>
          <td>${escapeHtml(confirmation.confirmed_at_kst)}</td>
        </tr>`).join("") || `<tr><td colspan="4">확인 기록이 없습니다.</td></tr>`;
      const captureButton = state.snapshot_capture_enabled
        ? `<button type="button" data-deletion-contract-action="capture" data-request-id="${escapeHtml(state.request_id)}">변경 불가 스냅샷 저장</button>`
        : "";
      const confirmationControl = state.confirmation_allowed && latest
        ? `
          <div>필요한 확인 문구:</div>
          <code>${escapeHtml(state.expected_confirmation_text)}</code>
          <div class="confirmation-input-row">
            <label>확인 문구
              <input id="dataDeletionConfirmationText-${escapeHtml(latest.id)}" autocomplete="off">
            </label>
            <button class="danger" type="button"
              data-deletion-contract-action="confirm"
              data-request-id="${escapeHtml(state.request_id)}"
              data-snapshot-id="${escapeHtml(latest.id)}"
              data-fingerprint="${escapeHtml(latest.fingerprint_sha256)}">확인 기록 저장</button>
          </div>`
        : "";
      return `
        <div class="confirmation-contract">
          <h3>변경 불가 확인 계약</h3>
          <div class="status">요청 ${escapeHtml(operationValueLabel(state.request_status))} / 실제 실행: 비활성</div>
          <div class="actions">${captureButton}</div>
          ${blockers.length ? `<ul>${blockers.map((blocker) => `<li>${escapeHtml(blocker)}</li>`).join("")}</ul>` : ""}
          ${confirmationControl}
          <h3>스냅샷 이력</h3>
          <table class="detail-table">
            <thead><tr><th>ID</th><th>저장 시각(KST)</th><th>SHA-256 지문값</th><th>검증</th></tr></thead>
            <tbody>${snapshotRows}</tbody>
          </table>
          <h3>확인 이력</h3>
          <table class="detail-table">
            <thead><tr><th>ID</th><th>스냅샷</th><th>처리자</th><th>확인 시각(KST)</th></tr></thead>
            <tbody>${confirmationRows}</tbody>
          </table>
        </div>`;
    }

    function renderDataDeletionDryRunState(state) {
      const latest = state.latest_plan;
      const plan = latest?.plan_json || {};
      const metrics = plan.metrics || {};
      const databaseRows = (plan.database_operations || []).map((operation) => `
        <tr>
          <td>${escapeHtml(operation.sequence)}</td>
          <td>${escapeHtml(operation.phase)}</td>
          <td>${escapeHtml(operation.table)}</td>
          <td>${Number.isFinite(Number(operation.estimated_rows)) ? formatInteger(operation.estimated_rows) : escapeHtml(operationValueLabel(operation.estimated_rows))}</td>
          <td>${escapeHtml(JSON.stringify(operation.selector || {}))}</td>
        </tr>`).join("") || `<tr><td colspan="5">예정된 데이터베이스 작업이 없습니다.</td></tr>`;
      const fileRows = (plan.file_operations || []).map((operation) => `
        <tr>
          <td>${escapeHtml(operation.sequence)}</td>
          <td>${escapeHtml(operation.artifact_type)}</td>
          <td>${escapeHtml(operation.match_id)}</td>
          <td>${formatBytes(operation.declared_size_bytes || 0)}</td>
          <td>${escapeHtml(operation.relative_path)}</td>
        </tr>`).join("") || `<tr><td colspan="5">예정된 플레이어 소유 파일 작업이 없습니다.</td></tr>`;
      const backupRows = (plan.backup_prerequisites || []).map((item) => `
        <tr>
          <td>${escapeHtml(item.key)}</td>
          <td>${item.required ? "필수" : "선택"}</td>
          <td>${escapeHtml(operationValueLabel(item.evidence_status))}</td>
          <td>${escapeHtml(item.description)}</td>
        </tr>`).join("") || `<tr><td colspan="4">기록된 백업 선행 조건이 없습니다.</td></tr>`;
      const exclusionRows = (plan.row_exclusions || []).map((item) => `
        <tr>
          <td>데이터베이스</td>
          <td>${escapeHtml(item.table)}</td>
          <td>${formatInteger(item.row_count)}</td>
          <td>${escapeHtml(item.reason)}</td>
        </tr>`).join("") + (plan.file_exclusions || []).map((item) => `
        <tr>
          <td>파일</td>
          <td>${escapeHtml(item.category)}</td>
          <td>${formatInteger(item.file_count)}</td>
          <td>${escapeHtml(item.reason)}</td>
        </tr>`).join("");
      const historyRows = (state.plans || []).map((item) => `
        <tr>
          <td>${escapeHtml(item.id)}</td>
          <td>${escapeHtml(item.preview_snapshot_id)} / ${escapeHtml(item.confirmation_id)}</td>
          <td>${escapeHtml(item.plan_fingerprint_sha256)}</td>
          <td>${escapeHtml(item.generated_by)} / ${escapeHtml(item.generated_at_kst)}</td>
        </tr>`).join("") || `<tr><td colspan="4">사전 점검 계획 기록이 없습니다.</td></tr>`;
      const generationButton = state.generation_allowed
        ? `<button type="button" data-deletion-contract-action="dry-run" data-request-id="${escapeHtml(state.request_id)}">읽기 전용 사전 점검 계획 생성</button>`
        : "";
      const planDetail = latest ? `
        <div class="status">
          계획 #${escapeHtml(latest.id)} / 작업 ${formatInteger(latest.operation_count)}개 /
          삭제 후보 행 ${formatInteger(metrics.candidate_row_count || 0)}개 / 파일 ${formatInteger(metrics.candidate_file_count || 0)}개 /
          ${formatBytes(metrics.candidate_file_bytes || 0)}
        </div>
        <code>계획 SHA-256: ${escapeHtml(latest.plan_fingerprint_sha256)}</code>
        <h3>백업 선행 조건</h3>
        <div class="table-scroll">
          <table class="detail-table">
            <thead><tr><th>키</th><th>필수 여부</th><th>증거</th><th>조건</th></tr></thead>
            <tbody>${backupRows}</tbody>
          </table>
        </div>
        <h3>데이터베이스 작업 순서</h3>
        <div class="table-scroll">
          <table class="detail-table">
            <thead><tr><th>순서</th><th>단계</th><th>테이블</th><th>행</th><th>선택 조건</th></tr></thead>
            <tbody>${databaseRows}</tbody>
          </table>
        </div>
        <h3>플레이어 소유 리플레이 파일 작업</h3>
        <div class="table-scroll">
          <table class="detail-table">
            <thead><tr><th>순서</th><th>유형</th><th>매치</th><th>크기</th><th>경로</th></tr></thead>
            <tbody>${fileRows}</tbody>
          </table>
        </div>
        <h3>보호 제외 항목</h3>
        <div class="table-scroll">
          <table class="detail-table">
            <thead><tr><th>유형</th><th>대상</th><th>수량</th><th>이유</th></tr></thead>
            <tbody>${exclusionRows || `<tr><td colspan="4">보호 제외 항목이 없습니다.</td></tr>`}</tbody>
          </table>
        </div>` : `<div class="status">기록된 사전 점검 계획이 없습니다.</div>`;
      return `
        <div class="dry-run-contract">
          <h3>확인된 삭제 사전 점검</h3>
          <div class="status">요청 ${escapeHtml(operationValueLabel(state.request_status))} / 실제 실행: 비활성 / 실행 준비: 안 됨</div>
          <div class="actions">${generationButton}</div>
          ${(state.generation_blockers || []).length ? `<ul>${state.generation_blockers.map((blocker) => `<li>${escapeHtml(blocker)}</li>`).join("")}</ul>` : ""}
          <ul>${(state.execution_blockers || []).map((blocker) => `<li>${escapeHtml(blocker)}</li>`).join("")}</ul>
          ${planDetail}
          <h3>사전 점검 이력</h3>
          <div class="table-scroll">
            <table class="detail-table">
              <thead><tr><th>ID</th><th>스냅샷 / 확인</th><th>계획 SHA-256</th><th>생성 정보</th></tr></thead>
              <tbody>${historyRows}</tbody>
            </table>
          </div>
        </div>`;
    }

    async function loadDataDeletionDryRunState(requestId) {
      const response = await fetch(`/data-deletions/${encodeURIComponent(requestId)}/dry-run-state`);
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || response.statusText);
      }
      const payload = await response.json();
      const host = document.querySelector("#dataDeletionDryRun");
      host.innerHTML = renderDataDeletionDryRunState(payload.dry_run_state);
    }

    async function createDataDeletionDryRunPlan(requestId) {
      const form = new FormData(dataDeletionFilterForm);
      const actorId = String(form.get("actor_id") || "").trim();
      const note = String(form.get("note") || "").trim();
      if (!actorId) throw new Error("로컬 검토자 ID가 필요합니다.");
      if (!window.confirm("변경 불가 읽기 전용 사전 점검 계획을 생성할까요? 데이터 행과 파일은 변경하지 않습니다.")) return;
      await postJson(`/data-deletions/${encodeURIComponent(requestId)}/dry-run-plans`, {
        actor_id: actorId,
        note: note || null,
      });
      await loadDataDeletionRequestDetail(requestId);
      dataDeletionStatus.textContent = "읽기 전용 사전 점검 계획을 기록했습니다. 실제 삭제는 계속 비활성 상태입니다.";
    }

    function renderDataDeletionBackupReadiness(state, builderState, verifierState, restoreState, plannerState, quarantineRehearsalState, combinedRehearsalState, faultMatrixState, reviewPacketState) {
      const plan = state.latest_plan;
      const builderBlockers = (builderState?.build_blockers || [])
        .map((blocker) => `<li>${escapeHtml(blocker)}</li>`)
        .join("");
      const builderForm = builderState?.confirmation_text ? `
        <div class="backup-builder-contract">
          <h3>선택 실행 백업 결과 생성기</h3>
          <div class="status">기준 폴더: ${escapeHtml(builderState.backup_root || "-")} / 체크섬 계산: 예 / 복원·격리·삭제: 아니요</div>
          <ul>${builderBlockers}</ul>
          <code>${escapeHtml(builderState.confirmation_text)}</code>
          <form class="confirmation-input-row backup-builder-form" data-backup-build-form data-request-id="${attr(state.request_id)}" data-plan-id="${attr(builderState.latest_plan_id || "")}">
            <label>정확한 생성 확인 문구
              <input name="confirmation_text" autocomplete="off" required>
            </label>
            <button type="submit" ${builderState.build_allowed ? "" : "disabled"}>백업 결과 생성</button>
          </form>
        </div>` : `
        <div class="backup-builder-contract">
          <h3>선택 실행 백업 결과 생성기</h3>
          <div class="status">기준 폴더: ${escapeHtml(builderState?.backup_root || "-")} / 사용 불가</div>
          <ul>${builderBlockers}</ul>
        </div>`;
      const verifierBlockers = (verifierState?.verification_blockers || [])
        .map((blocker) => `<li>${escapeHtml(blocker)}</li>`)
        .join("");
      const candidateRows = (verifierState?.candidates || []).map((candidate) => {
        const action = candidate.selectable && verifierState.verification_allowed
          ? `<button class="secondary" type="button" data-deletion-contract-action="verify-backup" data-request-id="${attr(state.request_id)}" data-plan-id="${attr(verifierState.latest_plan_id || "")}" data-manifest-path="${attr(candidate.manifest_path || "")}" data-manifest-sha256="${attr(candidate.manifest_sha256 || "")}">검증</button>`
          : "-";
        return `<tr>
          <td>${escapeHtml(candidate.build_id || "-")}<br>${escapeHtml(candidate.built_at_kst || "-")}</td>
          <td><code>${escapeHtml(candidate.manifest_path || "-")}</code></td>
          <td><code>${escapeHtml(candidate.manifest_sha256 || "-")}</code></td>
          <td>${candidate.selectable ? "선택 가능" : escapeHtml(candidate.inspection_error || "차단")}</td>
          <td>${action}</td>
        </tr>`;
      }).join("") || `<tr><td colspan="5">지문값에 연결된 백업 생성 기록이 없습니다.</td></tr>`;
      const latestVerification = verifierState?.latest_verification || null;
      const verificationCheckRows = (latestVerification?.result_json?.checks || []).map((check) => `
        <tr>
          <td>${escapeHtml(check.key)}</td>
          <td>${escapeHtml(check.status)}</td>
          <td>${escapeHtml(check.message)}</td>
        </tr>`).join("") || `<tr><td colspan="3">기록된 결과 파일 검증 항목이 없습니다.</td></tr>`;
      const verificationHistoryRows = (verifierState?.verification_history || []).map((item) => `
        <tr>
          <td>${escapeHtml(item.id)}</td>
          <td>${escapeHtml(operationValueLabel(item.result_status))}</td>
          <td>${formatInteger(item.verified_artifact_count)} / ${formatInteger(item.artifact_count)}</td>
          <td>${formatInteger(item.passed_check_count)} / ${formatInteger(item.check_count)}</td>
          <td>${escapeHtml(item.verified_by)} / ${escapeHtml(item.verified_at_kst)}</td>
        </tr>`).join("") || `<tr><td colspan="5">변경 불가 결과 파일 검증 기록이 없습니다.</td></tr>`;
      const verifierPanel = `
        <div class="backup-builder-contract">
          <h3>읽기 전용 백업 결과 검증</h3>
          <div class="status">기준 폴더: ${escapeHtml(verifierState?.backup_root || "-")} / ZIP·JSONL 체크섬: 예 / 복원·격리·삭제: 아니요</div>
          <ul>${verifierBlockers}</ul>
          <div class="table-scroll">
            <table class="detail-table">
              <thead><tr><th>생성 기록</th><th>명세서</th><th>선택한 SHA-256</th><th>상태</th><th>동작</th></tr></thead>
              <tbody>${candidateRows}</tbody>
            </table>
          </div>
          <h3>최근 결과 파일 검증 항목</h3>
          <div class="table-scroll">
            <table class="detail-table">
              <thead><tr><th>검사</th><th>상태</th><th>내용</th></tr></thead>
              <tbody>${verificationCheckRows}</tbody>
            </table>
          </div>
          <h3>결과 파일 검증 이력</h3>
          <div class="table-scroll">
            <table class="detail-table">
              <thead><tr><th>ID</th><th>상태</th><th>결과 파일</th><th>검사</th><th>검증 정보</th></tr></thead>
              <tbody>${verificationHistoryRows}</tbody>
            </table>
          </div>
        </div>`;
      const restoreBlockers = (restoreState?.restore_rehearsal_blockers || [])
        .map((blocker) => `<li>${escapeHtml(blocker)}</li>`)
        .join("");
      const restoreCandidateRows = (restoreState?.verification_candidates || []).map((candidate) => `
        <tr>
          <td>#${escapeHtml(candidate.id)} / ${escapeHtml(candidate.build_id || "-")}</td>
          <td>${escapeHtml(candidate.verified_by)} / ${escapeHtml(candidate.verified_at_kst)}</td>
          <td><code>${escapeHtml(candidate.confirmation_text || "-")}</code></td>
          <td>
            <form class="confirmation-input-row restore-rehearsal-form" data-restore-rehearsal-form data-request-id="${attr(state.request_id)}" data-verification-id="${attr(candidate.id)}">
              <label>정확한 모의 복원 확인 문구
                <input name="confirmation_text" autocomplete="off" required>
              </label>
              <button class="secondary" type="submit" ${restoreState?.restore_rehearsal_allowed ? "" : "disabled"}>실행</button>
            </form>
          </td>
        </tr>`).join("") || `<tr><td colspan="4">통과한 백업 검증 기록이 없습니다.</td></tr>`;
      const latestRestore = restoreState?.latest_restore_rehearsal || null;
      const restoreCheckRows = (latestRestore?.result_json?.checks || []).map((check) => `
        <tr>
          <td>${escapeHtml(check.key)}</td>
          <td>${escapeHtml(check.status)}</td>
          <td>${escapeHtml(check.message)}</td>
        </tr>`).join("") || `<tr><td colspan="3">격리된 복원 검사 기록이 없습니다.</td></tr>`;
      const restoreHistoryRows = (restoreState?.restore_rehearsal_history || []).map((item) => `
        <tr>
          <td>${escapeHtml(item.id)}</td>
          <td>${escapeHtml(operationValueLabel(item.result_status))}</td>
          <td>${formatInteger(item.mysql_restored_row_count)} / ${formatInteger(item.mysql_row_count)}</td>
          <td>${formatInteger(item.replay_restored_file_count)} / ${formatInteger(item.replay_file_count)}</td>
          <td>${item.backup_integrity_evidence_id ? `#${escapeHtml(item.backup_integrity_evidence_id)}` : "-"}</td>
          <td>${escapeHtml(item.run_by)} / ${escapeHtml(item.run_at_kst)}</td>
        </tr>`).join("") || `<tr><td colspan="6">격리된 모의 복원 기록이 없습니다.</td></tr>`;
      const restorePanel = `
        <div class="backup-builder-contract">
          <h3>격리된 백업 복원 모의 실행</h3>
          <div class="status">MySQL: 전용 임시 테이블 / 리플레이: 백업 볼륨의 임시 폴더 / 운영 데이터 복원·격리·삭제: 아니요</div>
          <ul>${restoreBlockers}</ul>
          <div class="table-scroll">
            <table class="detail-table">
              <thead><tr><th>검증</th><th>검증 정보</th><th>필요 문구</th><th>동작</th></tr></thead>
              <tbody>${restoreCandidateRows}</tbody>
            </table>
          </div>
          <h3>최근 격리 복원 검사</h3>
          <div class="table-scroll">
            <table class="detail-table">
              <thead><tr><th>검사</th><th>상태</th><th>내용</th></tr></thead>
              <tbody>${restoreCheckRows}</tbody>
            </table>
          </div>
          <h3>격리 복원 이력</h3>
          <div class="table-scroll">
            <table class="detail-table">
              <thead><tr><th>ID</th><th>상태</th><th>MySQL 행</th><th>리플레이 파일</th><th>무결성 증거</th><th>실행 정보</th></tr></thead>
              <tbody>${restoreHistoryRows}</tbody>
            </table>
          </div>
        </div>`;
      const plannerBlockers = (plannerState?.planning_blockers || [])
        .map((blocker) => `<li>${escapeHtml(blocker)}</li>`)
        .join("");
      const latestPlanning = plannerState?.latest_planning_run || null;
      const planningResult = latestPlanning?.result_json || {};
      const planningCheckRows = (planningResult.checks || []).map((check) => `
        <tr>
          <td>${escapeHtml(check.key)}</td>
          <td>${escapeHtml(check.status)}</td>
          <td>${escapeHtml(check.message)}</td>
        </tr>`).join("") || `<tr><td colspan="3">읽기 전용 격리 검사 기록이 없습니다.</td></tr>`;
      const planningOperationRows = (planningResult.file_operations || []).map((item) => `
        <tr>
          <td>${escapeHtml(item.sequence)} / #${escapeHtml(item.record_id)}</td>
          <td><code>${escapeHtml(item.source_relative_path || "-")}</code></td>
          <td><code>${escapeHtml(item.target_relative_path || "-")}</code></td>
          <td>${formatBytes(Number(item.declared_size_bytes || 0))}<br><code>${escapeHtml(item.sha256 || "-")}</code></td>
          <td>${item.target_exists ? "충돌" : "없음"}</td>
        </tr>`).join("") || `<tr><td colspan="5">검사한 파일 작업이 없습니다.</td></tr>`;
      const planningHistoryRows = (plannerState?.planning_history || []).map((item) => `
        <tr>
          <td>${escapeHtml(item.id)}</td>
          <td>${escapeHtml(operationValueLabel(item.result_status))}</td>
          <td>${formatInteger(item.source_verified_file_count)} / ${formatBytes(Number(item.source_verified_bytes || 0))}</td>
          <td>${formatBytes(Number(item.observed_free_bytes || 0))} / ${formatBytes(Number(item.required_free_bytes || 0))}</td>
          <td>${item.capacity_evidence_id ? `#${escapeHtml(item.capacity_evidence_id)}` : "-"}</td>
          <td>${escapeHtml(item.planned_by)} / ${escapeHtml(item.planned_at_kst)}</td>
        </tr>`).join("") || `<tr><td colspan="6">변경 불가 격리 계획 기록이 없습니다.</td></tr>`;
      const postcondition = planningResult.postcondition_contract || {};
      const rollback = planningResult.rollback_contract || {};
      const crashRecovery = planningResult.crash_recovery_contract || {};
      const plannerForm = plannerState?.confirmation_text ? `
        <code>${escapeHtml(plannerState.confirmation_text)}</code>
        <form class="confirmation-input-row quarantine-planner-form" data-quarantine-planner-form data-request-id="${attr(state.request_id)}" data-plan-id="${attr(plannerState.latest_plan_id || "")}">
          <label>정확한 읽기 전용 계획 확인 문구
            <input name="confirmation_text" autocomplete="off" required>
          </label>
          <button class="secondary" type="submit" ${plannerState.planning_allowed ? "" : "disabled"}>읽기 전용 계획 실행</button>
        </form>` : "";
      const plannerPanel = `
        <div class="backup-builder-contract">
          <h3>읽기 전용 격리 계획</h3>
          <div class="status">기준 폴더: ${escapeHtml(plannerState?.quarantine_root || "-")} / 원본 해시·용량 검사: 예 / 폴더·파일 생성 및 DB 원본 변경: 아니요</div>
          <ul>${plannerBlockers}</ul>
          ${plannerForm}
          <div class="status">최근 결과: ${escapeHtml(operationValueLabel(latestPlanning?.result_status || "none"))} / 후보 파일 ${formatInteger(latestPlanning?.candidate_file_count || 0)}개, ${formatBytes(Number(latestPlanning?.candidate_file_bytes || 0))} / 연결된 용량 증거 ${latestPlanning?.capacity_evidence_id ? `#${escapeHtml(latestPlanning.capacity_evidence_id)}` : "없음"}</div>
          <h3>최근 읽기 전용 검사</h3>
          <div class="table-scroll">
            <table class="detail-table">
              <thead><tr><th>검사</th><th>상태</th><th>내용</th></tr></thead>
              <tbody>${planningCheckRows}</tbody>
            </table>
          </div>
          <h3>결정된 향후 작업</h3>
          <div class="table-scroll">
            <table class="detail-table">
              <thead><tr><th>항목</th><th>원본</th><th>대상</th><th>크기 / SHA-256</th><th>대상 상태</th></tr></thead>
              <tbody>${planningOperationRows}</tbody>
            </table>
          </div>
          <div class="status">사후 조건: ${formatInteger((postcondition.item_checks || []).length)}개 항목 검사 / 되돌리기: ${formatInteger((rollback.item_actions || []).length)}개 역작업, 독립 모의 실행 ${operationBooleanLabel(rollback.independently_rehearsed)} / 장애 복구 일지: ${escapeHtml(crashRecovery.journal_relative_path || "-")}, 독립 모의 실행 ${operationBooleanLabel(crashRecovery.independently_rehearsed)}</div>
          <h3>격리 계획 이력</h3>
          <div class="table-scroll">
            <table class="detail-table">
              <thead><tr><th>ID</th><th>상태</th><th>검증한 원본</th><th>여유 / 필요</th><th>용량 증거</th><th>실행 정보</th></tr></thead>
              <tbody>${planningHistoryRows}</tbody>
            </table>
          </div>
        </div>`;
      const quarantineRehearsalBlockers = (quarantineRehearsalState?.rehearsal_blockers || [])
        .map((blocker) => `<li>${escapeHtml(blocker)}</li>`)
        .join("");
      const quarantineRehearsalCandidate = quarantineRehearsalState?.planning_candidate || null;
      const quarantineRehearsalForm = quarantineRehearsalCandidate?.confirmation_text ? `
        <code>${escapeHtml(quarantineRehearsalCandidate.confirmation_text)}</code>
        <form class="confirmation-input-row quarantine-rehearsal-form" data-quarantine-rehearsal-form data-request-id="${attr(state.request_id)}" data-planning-run-id="${attr(quarantineRehearsalCandidate.id || "")}">
          <label>정확한 격리 모의 실행 확인 문구
            <input name="confirmation_text" autocomplete="off" required>
          </label>
          <button class="secondary" type="submit" ${quarantineRehearsalState?.rehearsal_allowed ? "" : "disabled"}>격리 모의 실행</button>
        </form>` : "";
      const latestQuarantineRehearsal = quarantineRehearsalState?.latest_quarantine_rehearsal || null;
      const quarantineRehearsalCheckRows = (latestQuarantineRehearsal?.result_json?.checks || []).map((check) => `
        <tr>
          <td>${escapeHtml(check.key)}</td>
          <td>${escapeHtml(check.status)}</td>
          <td>${escapeHtml(check.message)}</td>
        </tr>`).join("") || `<tr><td colspan="3">격리된 격리 모의 실행 검사 기록이 없습니다.</td></tr>`;
      const quarantineRehearsalHistoryRows = (quarantineRehearsalState?.quarantine_rehearsal_history || []).map((item) => `
        <tr>
          <td>${escapeHtml(item.id)}</td>
          <td>${escapeHtml(operationValueLabel(item.result_status))}</td>
          <td>${formatInteger(item.normal_rolled_back_count)} / ${formatInteger(item.fixture_file_count)}</td>
          <td>${formatInteger(item.recovered_case_count)} + 모호함 ${formatInteger(item.ambiguous_case_blocked_count)} / ${formatInteger(item.recovery_case_count)}</td>
          <td>${item.scratch_directory_removed ? "정리 완료" : "정리 차단"}</td>
          <td>${escapeHtml(item.run_by)} / ${escapeHtml(item.run_at_kst)}</td>
        </tr>`).join("") || `<tr><td colspan="6">격리된 격리 모의 실행 기록이 없습니다.</td></tr>`;
      const quarantineRehearsalPanel = `
        <div class="backup-builder-contract">
          <h3>격리된 격리 모의 실행</h3>
          <div class="status">기준 폴더: ${escapeHtml(quarantineRehearsalState?.quarantine_root || "-")} / 합성 시험 자료만 사용: 예 / 운영 원본 접근·격리·삭제: 아니요</div>
          <ul>${quarantineRehearsalBlockers}</ul>
          ${quarantineRehearsalForm}
          <div class="status">최근 결과: ${escapeHtml(operationValueLabel(latestQuarantineRehearsal?.result_status || "none"))} / 임시 공간 정리: ${latestQuarantineRehearsal ? (latestQuarantineRehearsal.scratch_directory_removed ? "완료" : "차단") : "실행 안 함"} / 일지 상태 전환: ${formatInteger(latestQuarantineRehearsal?.journal_transition_count || 0)}회</div>
          <h3>최근 격리 모의 실행 검사</h3>
          <div class="table-scroll">
            <table class="detail-table">
              <thead><tr><th>검사</th><th>상태</th><th>내용</th></tr></thead>
              <tbody>${quarantineRehearsalCheckRows}</tbody>
            </table>
          </div>
          <h3>격리 모의 실행 이력</h3>
          <div class="table-scroll">
            <table class="detail-table">
              <thead><tr><th>ID</th><th>상태</th><th>되돌리기 시험 자료</th><th>복구 사례</th><th>임시 공간</th><th>실행 정보</th></tr></thead>
              <tbody>${quarantineRehearsalHistoryRows}</tbody>
            </table>
          </div>
        </div>`;
      const combinedBlockers = (combinedRehearsalState?.combined_rehearsal_blockers || [])
        .map((blocker) => `<li>${escapeHtml(blocker)}</li>`)
        .join("");
      const combinedCandidate = combinedRehearsalState?.combined_candidate || null;
      const combinedForm = combinedCandidate?.confirmation_text ? `
        <code>${escapeHtml(combinedCandidate.confirmation_text)}</code>
        <form class="confirmation-input-row combined-rehearsal-form" data-combined-rehearsal-form data-request-id="${attr(state.request_id)}" data-verification-run-id="${attr(combinedCandidate.backup_verification?.id || "")}" data-planning-run-id="${attr(combinedCandidate.quarantine_planning?.id || "")}">
          <label>정확한 통합 모의 실행 확인 문구
            <input name="confirmation_text" autocomplete="off" required>
          </label>
          <button class="secondary" type="submit" ${combinedRehearsalState?.combined_rehearsal_allowed ? "" : "disabled"}>통합 모의 실행</button>
        </form>` : "";
      const latestCombined = combinedRehearsalState?.latest_combined_rehearsal || null;
      const combinedCheckRows = (latestCombined?.result_json?.checks || []).map((check) => `
        <tr>
          <td>${escapeHtml(check.key)}</td>
          <td>${escapeHtml(check.status)}</td>
          <td>${escapeHtml(check.message)}</td>
        </tr>`).join("") || `<tr><td colspan="3">통합 모의 실행 검사 기록이 없습니다.</td></tr>`;
      const combinedHistoryRows = (combinedRehearsalState?.combined_rehearsal_history || []).map((item) => `
        <tr>
          <td>${escapeHtml(item.id)}</td>
          <td>${escapeHtml(operationValueLabel(item.result_status))}</td>
          <td>${formatInteger(item.mysql_deleted_row_count)} / ${formatInteger(item.mysql_rolled_back_row_count)}</td>
          <td>${formatInteger(item.quarantine_recovered_case_count)} / ${formatInteger(item.quarantine_recovery_case_count)}</td>
          <td>${item.scratch_resources_removed ? "정리 완료" : "정리 차단"}</td>
          <td>${escapeHtml(item.run_by)} / ${escapeHtml(item.run_at_kst)}</td>
        </tr>`).join("") || `<tr><td colspan="6">통합 모의 실행 기록이 없습니다.</td></tr>`;
      const combinedRehearsalPanel = `
        <div class="backup-builder-contract">
          <h3>격리된 통합 삭제 모의 실행</h3>
          <div class="status">MySQL: 연결 전용 임시 테이블에서 DELETE 후 ROLLBACK / 파일: 합성 격리 상태 전이 / 운영 행·파일: 변경 없음 / 실제 실행: 비활성</div>
          <ul>${combinedBlockers}</ul>
          ${combinedForm}
          <div class="status">최근 결과: ${escapeHtml(operationValueLabel(latestCombined?.result_status || "none"))} / MySQL 삭제·되돌림: ${formatInteger(latestCombined?.mysql_deleted_row_count || 0)} / ${formatInteger(latestCombined?.mysql_rolled_back_row_count || 0)} / 임시 공간 정리: ${latestCombined ? (latestCombined.scratch_resources_removed ? "완료" : "차단") : "실행 안 함"}</div>
          <h3>최근 통합 검사</h3>
          <div class="table-scroll">
            <table class="detail-table">
              <thead><tr><th>검사</th><th>상태</th><th>내용</th></tr></thead>
              <tbody>${combinedCheckRows}</tbody>
            </table>
          </div>
          <h3>통합 모의 실행 이력</h3>
          <div class="table-scroll">
            <table class="detail-table">
              <thead><tr><th>ID</th><th>상태</th><th>DB 삭제 / 되돌림</th><th>파일 복구</th><th>임시 공간</th><th>실행 정보</th></tr></thead>
              <tbody>${combinedHistoryRows}</tbody>
            </table>
          </div>
        </div>`;
      const faultMatrixBlockers = (faultMatrixState?.fault_matrix_blockers || [])
        .map((blocker) => `<li>${escapeHtml(blocker)}</li>`)
        .join("");
      const faultMatrixCandidate = faultMatrixState?.fault_matrix_candidate || null;
      const faultMatrixForm = faultMatrixCandidate?.confirmation_text ? `
        <code>${escapeHtml(faultMatrixCandidate.confirmation_text)}</code>
        <form class="confirmation-input-row fault-matrix-form" data-fault-matrix-form data-request-id="${attr(state.request_id)}" data-combined-run-id="${attr(faultMatrixCandidate.combined_rehearsal?.id || "")}">
          <label>정확한 장애 행렬 확인 문구
            <input name="confirmation_text" autocomplete="off" required>
          </label>
          <button class="secondary" type="submit" ${faultMatrixState?.fault_matrix_allowed ? "" : "disabled"}>장애 행렬 실행</button>
        </form>` : "";
      const latestFaultMatrix = faultMatrixState?.latest_fault_matrix_run || null;
      const faultMatrixChecks = latestFaultMatrix?.result_json?.checks || [];
      const faultMatrixCheckRows = faultMatrixChecks.map((check) => `
        <tr>
          <td>${escapeHtml(check.key)}</td>
          <td>${escapeHtml(check.status)}</td>
          <td>${escapeHtml(check.message)}</td>
        </tr>`).join("") || `<tr><td colspan="3">장애 행렬 검사 기록이 없습니다.</td></tr>`;
      const displayedFaultScenarios = latestFaultMatrix?.result_json?.scenarios
        || faultMatrixCandidate?.scenario_contract
        || [];
      const faultScenarioRows = displayedFaultScenarios.map((scenario) => `
        <tr>
          <td>${escapeHtml(scenario.key)}</td>
          <td>${escapeHtml(scenario.category)}</td>
          <td>${escapeHtml(scenario.fault_point)}</td>
          <td>${escapeHtml(operationValueLabel(scenario.status || "실행 안 함"))}</td>
          <td>${scenario.fault_observed === true ? "예" : "-"}</td>
          <td>${scenario.fault_contained === true ? "예" : "-"}</td>
          <td>${scenario.scratch_removed === true ? "정리 완료" : (scenario.status ? "차단" : "-")}</td>
        </tr>`).join("") || `<tr><td colspan="7">정의된 장애 시나리오가 없습니다.</td></tr>`;
      const faultMatrixHistoryRows = (faultMatrixState?.fault_matrix_history || []).map((item) => `
        <tr>
          <td>${escapeHtml(item.id)}</td>
          <td>${escapeHtml(operationValueLabel(item.result_status))}</td>
          <td>${formatInteger(item.passed_scenario_count)} / ${formatInteger(item.scenario_count)}</td>
          <td>${formatInteger(item.contained_fault_count)} / ${formatInteger(item.scenario_count)}</td>
          <td>${item.scratch_resources_removed ? "정리 완료" : "정리 차단"}</td>
          <td>${escapeHtml(item.run_by)} / ${escapeHtml(item.run_at_kst)}</td>
        </tr>`).join("") || `<tr><td colspan="6">장애 행렬 기록이 없습니다.</td></tr>`;
      const faultMatrixPanel = `
        <div class="backup-builder-contract">
          <h3>격리된 통합 장애 행렬</h3>
          <div class="status">MySQL 임시 테이블 장애 1개 + 합성 격리 장애 3개 / 운영 행·파일: 변경 없음 / 실제 실행: 비활성</div>
          <ul>${faultMatrixBlockers}</ul>
          ${faultMatrixForm}
          <div class="status">최근 결과: ${escapeHtml(operationValueLabel(latestFaultMatrix?.result_status || "none"))} / 통과 시나리오: ${formatInteger(latestFaultMatrix?.passed_scenario_count || 0)} / ${formatInteger(latestFaultMatrix?.scenario_count || faultMatrixState?.scenario_count || 4)} / 격리된 장애: ${formatInteger(latestFaultMatrix?.contained_fault_count || 0)} / 임시 공간 정리: ${latestFaultMatrix ? (latestFaultMatrix.scratch_resources_removed ? "완료" : "차단") : "실행 안 함"}</div>
          <h3>정의된 장애 시나리오</h3>
          <div class="table-scroll">
            <table class="detail-table">
              <thead><tr><th>시나리오</th><th>범위</th><th>장애 지점</th><th>상태</th><th>관측</th><th>격리</th><th>임시 공간</th></tr></thead>
              <tbody>${faultScenarioRows}</tbody>
            </table>
          </div>
          <h3>최근 장애 행렬 검사</h3>
          <div class="table-scroll">
            <table class="detail-table">
              <thead><tr><th>검사</th><th>상태</th><th>내용</th></tr></thead>
              <tbody>${faultMatrixCheckRows}</tbody>
            </table>
          </div>
          <h3>장애 행렬 이력</h3>
          <div class="table-scroll">
            <table class="detail-table">
              <thead><tr><th>ID</th><th>상태</th><th>통과</th><th>격리</th><th>임시 공간</th><th>실행 정보</th></tr></thead>
              <tbody>${faultMatrixHistoryRows}</tbody>
            </table>
          </div>
        </div>`;
      const reviewPacketBlockers = (reviewPacketState?.review_packet_blockers || [])
        .map((blocker) => `<li>${escapeHtml(blocker)}</li>`)
        .join("");
      const reviewPacketCandidate = reviewPacketState?.packet_candidate || null;
      const reviewPacketForm = reviewPacketCandidate?.confirmation_text ? `
        <code>${escapeHtml(reviewPacketCandidate.confirmation_text)}</code>
        <form class="confirmation-input-row review-packet-form" data-review-packet-form data-request-id="${attr(state.request_id)}" data-fault-matrix-run-id="${attr(reviewPacketCandidate.fault_matrix?.id || "")}">
          <label>정확한 검토 패킷 확인 문구
            <input name="confirmation_text" autocomplete="off" required>
          </label>
          <button class="secondary" type="submit" ${reviewPacketState?.review_packet_allowed ? "" : "disabled"}>검토 패킷 생성</button>
        </form>` : "";
      const reviewInputRows = reviewPacketCandidate ? [
        ["삭제 요청", { id: state.request_id, result_status: reviewPacketState?.request_status || "bound" }, null],
        ["사전 점검 계획", reviewPacketCandidate.dry_run_plan, "plan_fingerprint_sha256"],
        ["백업 검증", reviewPacketCandidate.backup_verification, "result_fingerprint_sha256"],
        ["격리 계획", reviewPacketCandidate.quarantine_planning, "result_fingerprint_sha256"],
        ["통합 모의 실행", reviewPacketCandidate.combined_rehearsal, "result_fingerprint_sha256"],
        ["장애 행렬", reviewPacketCandidate.fault_matrix, "result_fingerprint_sha256"],
      ].map(([label, input, fingerprintKey]) => `
        <tr>
          <td>${escapeHtml(label)}</td>
          <td>#${escapeHtml(input?.id || "-")}</td>
          <td>${escapeHtml(operationValueLabel(input?.result_status || "immutable"))}</td>
          <td><code>${escapeHtml(fingerprintKey ? (input?.[fingerprintKey] || "-") : reviewPacketCandidate.input_contract_fingerprint_sha256)}</code></td>
        </tr>`).join("") : `<tr><td colspan="4">현재 사용할 수 있는 6개 입력 검토 계약이 없습니다.</td></tr>`;
      const latestReviewPacket = reviewPacketState?.latest_review_packet || null;
      const reviewPacketCheckRows = (latestReviewPacket?.packet_json?.checks || []).map((check) => `
        <tr>
          <td>${escapeHtml(check.key)}</td>
          <td>${escapeHtml(check.status)}</td>
          <td>${escapeHtml(check.message)}</td>
        </tr>`).join("") || `<tr><td colspan="3">변경 불가 검토 패킷 검사 기록이 없습니다.</td></tr>`;
      const reviewPacketHistoryRows = (reviewPacketState?.review_packet_history || []).map((item) => `
        <tr>
          <td>#${escapeHtml(item.id)}</td>
          <td>${escapeHtml(operationValueLabel(item.review_status))}</td>
          <td>${formatInteger(item.passed_input_count)} / ${formatInteger(item.input_count)}</td>
          <td>${formatInteger(item.passed_check_count)} / ${formatInteger(item.check_count)}</td>
          <td>${formatInteger(item.passed_fault_scenario_count)} / ${formatInteger(item.fault_scenario_count)}</td>
          <td>${item.scratch_resources_removed ? "정리 완료" : "차단"}</td>
          <td>${escapeHtml(item.generated_by)} / ${escapeHtml(item.generated_at_kst)}</td>
          <td><a href="/data-deletions/${attr(state.request_id)}/review-packets/${attr(item.id)}/export.json" download>JSON</a></td>
        </tr>`).join("") || `<tr><td colspan="8">변경 불가 검토 패킷이 없습니다.</td></tr>`;
      const reviewPacketPanel = `
        <div class="backup-builder-contract">
          <h3>삭제 검토 패킷</h3>
          <div class="status">변경 불가 JSON 감사 패킷: 예 / 승인 부여·실행 준비 승격: 아니요 / 실제 실행: 비활성</div>
          <ul>${reviewPacketBlockers}</ul>
          ${reviewPacketForm}
          <div class="status">후보 판정: ${escapeHtml(operationValueLabel(reviewPacketCandidate?.predicted_review_status || "unavailable"))} / 최근 패킷: ${escapeHtml(operationValueLabel(latestReviewPacket?.review_status || "none"))} / 입력 지문값: <code>${escapeHtml(reviewPacketCandidate?.input_contract_fingerprint_sha256 || "-")}</code></div>
          <h3>정규화 입력 연결</h3>
          <div class="table-scroll">
            <table class="detail-table">
              <thead><tr><th>입력</th><th>ID</th><th>상태</th><th>지문값</th></tr></thead>
              <tbody>${reviewInputRows}</tbody>
            </table>
          </div>
          <h3>최근 패킷 판정 검사</h3>
          <div class="table-scroll">
            <table class="detail-table">
              <thead><tr><th>검사</th><th>상태</th><th>내용</th></tr></thead>
              <tbody>${reviewPacketCheckRows}</tbody>
            </table>
          </div>
          <h3>검토 패킷 이력</h3>
          <div class="table-scroll">
            <table class="detail-table">
              <thead><tr><th>ID</th><th>판정</th><th>입력</th><th>검사</th><th>장애</th><th>임시 공간</th><th>생성 정보</th><th>내보내기</th></tr></thead>
              <tbody>${reviewPacketHistoryRows}</tbody>
            </table>
          </div>
        </div>`;
      const prerequisiteRows = (state.prerequisites || []).map((item) => `
        <tr>
          <td>${escapeHtml(item.key)}</td>
          <td>${item.required ? "필수" : "선택"}</td>
          <td>${escapeHtml(operationValueLabel(item.evidence_status))}</td>
          <td>${item.latest_evidence ? `#${escapeHtml(item.latest_evidence.id)} / ${escapeHtml(item.latest_evidence.recorded_at_kst)}` : "-"}</td>
        </tr>`).join("") || `<tr><td colspan="4">사전 점검용 백업 선행 조건이 없습니다.</td></tr>`;
      const evidenceRows = (state.evidence_history || []).map((item) => `
        <tr>
          <td>${escapeHtml(item.id)}</td>
          <td>${escapeHtml(item.prerequisite_key)}</td>
          <td>${escapeHtml(item.recorded_by)} / ${escapeHtml(item.recorded_at_kst)}</td>
          <td>${escapeHtml(JSON.stringify(item.evidence_json || {}))}</td>
        </tr>`).join("") || `<tr><td colspan="4">변경 불가 백업 증거가 없습니다.</td></tr>`;
      const latestResult = state.latest_rehearsal?.result_json || {};
      const checkRows = (latestResult.checks || []).map((check) => `
        <tr>
          <td>${escapeHtml(check.key)}</td>
          <td>${escapeHtml(check.status)}</td>
          <td>${escapeHtml(check.message)}</td>
        </tr>`).join("") || `<tr><td colspan="3">모의 실행 검사 기록이 없습니다.</td></tr>`;
      const rehearsalRows = (state.rehearsals || []).map((item) => `
        <tr>
          <td>${escapeHtml(item.id)}</td>
          <td>${escapeHtml(operationValueLabel(item.result_status))}</td>
          <td>${formatInteger(item.passed_check_count)} / ${formatInteger(item.check_count)}</td>
          <td>${escapeHtml(item.run_by)} / ${escapeHtml(item.run_at_kst)}</td>
        </tr>`).join("") || `<tr><td colspan="4">실제 실행 없는 모의 기록이 없습니다.</td></tr>`;
      const evidenceOptions = (state.prerequisites || [])
        .filter((item) => item.required && !["quarantine_capacity_check", "backup_integrity_verification"].includes(item.key))
        .map((item) => `<option value="${attr(item.key)}">${escapeHtml(item.key)}</option>`)
        .join("");
      const evidenceForm = plan && state.evidence_recording_allowed && evidenceOptions ? `
        <form class="backup-evidence-form" data-backup-evidence-form data-request-id="${attr(state.request_id)}" data-plan-id="${attr(plan.id)}">
          <label>선행 조건
            <select name="prerequisite_key">${evidenceOptions}</select>
          </label>
          <label data-evidence-field="artifact_path">백업 결과 경로
            <input name="artifact_path" autocomplete="off">
          </label>
          <label data-evidence-field="artifact_sha256">결과 파일 SHA-256
            <input name="artifact_sha256" autocomplete="off" minlength="64" maxlength="64">
          </label>
          <label data-evidence-field="artifact_size_bytes">결과 파일 크기(바이트)
            <input name="artifact_size_bytes" type="number" min="1" step="1">
          </label>
          <label data-evidence-field="covered_row_count">포함된 행 수
            <input name="covered_row_count" type="number" min="0" step="1">
          </label>
          <label data-evidence-field="covered_file_count">포함된 파일 수
            <input name="covered_file_count" type="number" min="0" step="1">
          </label>
          <label data-evidence-field="covered_file_bytes">포함된 원본 크기(바이트)
            <input name="covered_file_bytes" type="number" min="0" step="1">
          </label>
          <label data-evidence-field="backup_created_at_kst">백업 생성 시각(KST)
            <input name="backup_created_at_kst" type="datetime-local">
          </label>
          <button type="submit">변경 불가 증거 기록</button>
        </form>` : "";
      const rehearsalButton = plan && state.rehearsal_allowed
        ? `<button class="secondary" type="button" data-deletion-contract-action="rehearsal" data-request-id="${attr(state.request_id)}" data-plan-id="${attr(plan.id)}">실제 실행 없는 모의 절차</button>`
        : "";
      return `
        <div class="backup-readiness-contract">
          <h3>백업 증거 및 모의 실행</h3>
          <div class="status">실제 실행: 비활성 / 실행 준비: 안 됨 / 모의 체크섬 재계산·복원 작업: 아니요</div>
          <ul>${(state.execution_blockers || []).map((blocker) => `<li>${escapeHtml(blocker)}</li>`).join("")}</ul>
          <div class="actions">${rehearsalButton}</div>
          ${builderForm}
          ${verifierPanel}
          ${restorePanel}
          ${plannerPanel}
          ${quarantineRehearsalPanel}
          ${combinedRehearsalPanel}
          ${faultMatrixPanel}
          ${reviewPacketPanel}
          <h3>선행 조건 증거</h3>
          <div class="table-scroll">
            <table class="detail-table">
              <thead><tr><th>키</th><th>필수 여부</th><th>상태</th><th>최근 증거</th></tr></thead>
              <tbody>${prerequisiteRows}</tbody>
            </table>
          </div>
          ${evidenceForm}
          <h3>최근 모의 실행 검사</h3>
          <div class="table-scroll">
            <table class="detail-table">
              <thead><tr><th>검사</th><th>상태</th><th>내용</th></tr></thead>
              <tbody>${checkRows}</tbody>
            </table>
          </div>
          <h3>증거 이력</h3>
          <div class="table-scroll">
            <table class="detail-table">
              <thead><tr><th>ID</th><th>선행 조건</th><th>기록 정보</th><th>증거</th></tr></thead>
              <tbody>${evidenceRows}</tbody>
            </table>
          </div>
          <h3>모의 실행 이력</h3>
          <div class="table-scroll">
            <table class="detail-table">
              <thead><tr><th>ID</th><th>상태</th><th>통과 검사</th><th>실행 정보</th></tr></thead>
              <tbody>${rehearsalRows}</tbody>
            </table>
          </div>
        </div>`;
    }

    function updateBackupEvidenceFields(form) {
      const key = String(form?.elements.prerequisite_key?.value || "");
      const fields = {
        mysql_target_backup: ["artifact_path", "artifact_sha256", "artifact_size_bytes", "covered_row_count", "backup_created_at_kst"],
        replay_artifact_backup: ["artifact_path", "artifact_sha256", "artifact_size_bytes", "covered_file_count", "covered_file_bytes", "backup_created_at_kst"],
      };
      const enabled = new Set(fields[key] || []);
      for (const label of form?.querySelectorAll("[data-evidence-field]") || []) {
        const active = enabled.has(label.dataset.evidenceField || "");
        label.hidden = !active;
        for (const input of label.querySelectorAll("input,select")) input.disabled = !active;
      }
    }

    async function loadDataDeletionBackupReadiness(requestId) {
      const response = await fetch(`/data-deletions/${encodeURIComponent(requestId)}/backup-readiness-state`);
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || response.statusText);
      }
      const payload = await response.json();
      const host = document.querySelector("#dataDeletionBackupReadiness");
      host.innerHTML = renderDataDeletionBackupReadiness(
        payload.backup_readiness_state,
        payload.backup_builder_state,
        payload.backup_verifier_state,
        payload.backup_restore_rehearsal_state,
        payload.quarantine_planner_state,
        payload.quarantine_rehearsal_state,
        payload.combined_rehearsal_state,
        payload.fault_matrix_state,
        payload.review_packet_state,
      );
      updateBackupEvidenceFields(host.querySelector("form[data-backup-evidence-form]"));
    }

    function optionalFormText(form, name) {
      const value = String(form.get(name) || "").trim();
      return value || null;
    }

    function optionalFormNumber(form, name) {
      const value = optionalFormText(form, name);
      return value === null ? null : Number(value);
    }

    async function buildDataDeletionBackupArtifacts(formElement) {
      const values = new FormData(formElement);
      const reviewer = new FormData(dataDeletionFilterForm);
      const requestId = formElement.dataset.requestId || "";
      const actorId = String(reviewer.get("actor_id") || "").trim();
      const note = String(reviewer.get("note") || "").trim();
      const confirmationText = String(values.get("confirmation_text") || "").trim();
      if (!actorId) throw new Error("로컬 검토자 ID가 필요합니다.");
      if (!confirmationText) throw new Error("정확한 생성 확인 문구가 필요합니다.");
      const button = formElement.querySelector("button[type='submit']");
      if (button) button.disabled = true;
      try {
        await postJson(`/data-deletions/${encodeURIComponent(requestId)}/backup-builds`, {
          dry_run_plan_id: Number(formElement.dataset.planId),
          confirmation_text: confirmationText,
          actor_id: actorId,
          note: note || null,
        });
        await loadDataDeletionRequestDetail(requestId);
        dataDeletionStatus.textContent = "백업 파일과 변경 불가 증거를 기록했습니다. 복원·격리·삭제 작업은 실행하지 않았습니다.";
      } finally {
        if (button) button.disabled = false;
      }
    }

    async function runDataDeletionBackupVerification(button) {
      const reviewer = new FormData(dataDeletionFilterForm);
      const requestId = button.dataset.requestId || "";
      const actorId = String(reviewer.get("actor_id") || "").trim();
      const note = String(reviewer.get("note") || "").trim();
      if (!actorId) throw new Error("로컬 검토자 ID가 필요합니다.");
      if (!window.confirm("이 백업 생성 결과를 다시 열어 읽기 전용으로 검증할까요? 변경 불가 감사 기록 1개만 추가하며 복원이나 삭제는 실행하지 않습니다.")) return;
      button.disabled = true;
      try {
        const payload = await postJson(`/data-deletions/${encodeURIComponent(requestId)}/backup-verifications`, {
          dry_run_plan_id: Number(button.dataset.planId),
          manifest_path: button.dataset.manifestPath || "",
          expected_manifest_sha256: button.dataset.manifestSha256 || "",
          actor_id: actorId,
          note: note || null,
        });
        const status = payload.backup_verification?.result_status || "unknown";
        await loadDataDeletionRequestDetail(requestId);
        dataDeletionStatus.textContent = `백업 결과 검증 ${operationValueLabel(status)} · 복원, 격리 또는 삭제 작업은 실행하지 않았습니다.`;
      } finally {
        button.disabled = false;
      }
    }

    async function runDataDeletionBackupRestoreRehearsal(formElement) {
      const values = new FormData(formElement);
      const reviewer = new FormData(dataDeletionFilterForm);
      const requestId = formElement.dataset.requestId || "";
      const actorId = String(reviewer.get("actor_id") || "").trim();
      const note = String(reviewer.get("note") || "").trim();
      const confirmationText = String(values.get("confirmation_text") || "").trim();
      if (!actorId) throw new Error("로컬 검토자 ID가 필요합니다.");
      if (!confirmationText) throw new Error("정확한 복원 모의 실행 확인 문구가 필요합니다.");
      if (!window.confirm("연결 전용 MySQL 테이블과 임시 리플레이 파일로 격리된 복원 모의 실행을 진행할까요? 운영 데이터 복원, 격리 및 삭제는 계속 비활성 상태입니다.")) return;
      const button = formElement.querySelector("button[type='submit']");
      if (button) button.disabled = true;
      try {
        const payload = await postJson(`/data-deletions/${encodeURIComponent(requestId)}/backup-restore-rehearsals`, {
          backup_verification_run_id: Number(formElement.dataset.verificationId),
          confirmation_text: confirmationText,
          actor_id: actorId,
          note: note || null,
        });
        const run = payload.backup_restore_rehearsal || {};
        await loadDataDeletionRequestDetail(requestId);
        dataDeletionStatus.textContent = `격리 복원 모의 실행 ${operationValueLabel(run.result_status || "unknown")} · 무결성 증거: ${run.backup_integrity_evidence_id ? `#${run.backup_integrity_evidence_id}` : "기록 없음"} · 운영 데이터 복원과 삭제는 계속 비활성 상태입니다.`;
      } finally {
        if (button) button.disabled = false;
      }
    }

    async function runDataDeletionQuarantinePlanning(formElement) {
      const values = new FormData(formElement);
      const reviewer = new FormData(dataDeletionFilterForm);
      const requestId = formElement.dataset.requestId || "";
      const actorId = String(reviewer.get("actor_id") || "").trim();
      const note = String(reviewer.get("note") || "").trim();
      const confirmationText = String(values.get("confirmation_text") || "").trim();
      if (!actorId) throw new Error("로컬 검토자 ID가 필요합니다.");
      if (!confirmationText) throw new Error("정확한 읽기 전용 계획 확인 문구가 필요합니다.");
      if (!window.confirm("읽기 전용 격리 계획을 실행할까요? 원본 파일은 식별 정보, 크기, SHA-256 검증에만 열고 대상 경로 및 여유 공간만 확인합니다. 폴더·일지 생성, 복사, 이동, 원본 제거, 복원, 격리 또는 삭제는 실행하지 않습니다.")) return;
      const button = formElement.querySelector("button[type='submit']");
      if (button) button.disabled = true;
      try {
        const payload = await postJson(`/data-deletions/${encodeURIComponent(requestId)}/quarantine-plans`, {
          dry_run_plan_id: Number(formElement.dataset.planId),
          confirmation_text: confirmationText,
          actor_id: actorId,
          note: note || null,
        });
        const run = payload.quarantine_planning || {};
        await loadDataDeletionRequestDetail(requestId);
        dataDeletionStatus.textContent = `읽기 전용 격리 계획 ${operationValueLabel(run.result_status || "unknown")} · 용량 증거: ${run.capacity_evidence_id ? `#${run.capacity_evidence_id}` : "기록 없음"} · 폴더, 파일, 원본 행, 격리 및 삭제 데이터는 변경하지 않았습니다.`;
      } finally {
        if (button) button.disabled = false;
      }
    }

    async function runDataDeletionQuarantineRehearsal(formElement) {
      const values = new FormData(formElement);
      const reviewer = new FormData(dataDeletionFilterForm);
      const requestId = formElement.dataset.requestId || "";
      const actorId = String(reviewer.get("actor_id") || "").trim();
      const note = String(reviewer.get("note") || "").trim();
      const confirmationText = String(values.get("confirmation_text") || "").trim();
      if (!actorId) throw new Error("로컬 검토자 ID가 필요합니다.");
      if (!confirmationText) throw new Error("정확한 격리 모의 실행 확인 문구가 필요합니다.");
      if (!window.confirm("격리된 격리 모의 실행을 진행할까요? 작은 합성 시험 자료와 일지를 격리 저장소 아래의 임의 전용 임시 폴더에만 만들고 제거합니다. 운영 리플레이 파일은 열거나 변경하지 않으며 운영 격리, DB 삭제 또는 복원은 실행하지 않습니다.")) return;
      const button = formElement.querySelector("button[type='submit']");
      if (button) button.disabled = true;
      try {
        const payload = await postJson(`/data-deletions/${encodeURIComponent(requestId)}/quarantine-rehearsals`, {
          quarantine_planning_run_id: Number(formElement.dataset.planningRunId),
          confirmation_text: confirmationText,
          actor_id: actorId,
          note: note || null,
        });
        const run = payload.quarantine_rehearsal || {};
        await loadDataDeletionRequestDetail(requestId);
        dataDeletionStatus.textContent = `격리된 격리 모의 실행 ${operationValueLabel(run.result_status || "unknown")} · 임시 공간 정리: ${run.scratch_directory_removed ? "완료" : "차단"} · 운영 원본 접근, 격리 및 삭제는 비활성 상태를 유지했습니다.`;
      } finally {
        if (button) button.disabled = false;
      }
    }

    async function runDataDeletionCombinedRehearsal(formElement) {
      const values = new FormData(formElement);
      const reviewer = new FormData(dataDeletionFilterForm);
      const requestId = formElement.dataset.requestId || "";
      const actorId = String(reviewer.get("actor_id") || "").trim();
      const note = String(reviewer.get("note") || "").trim();
      const confirmationText = String(values.get("confirmation_text") || "").trim();
      if (!actorId) throw new Error("로컬 검토자 ID가 필요합니다.");
      if (!confirmationText) throw new Error("정확한 격리 통합 모의 실행 확인 문구가 필요합니다.");
      if (!window.confirm("격리된 통합 삭제 모의 실행을 진행할까요? 검증된 백업 행은 연결 전용 MySQL 임시 테이블에서만 삭제 후 되돌리고, 파일 동작은 합성 시험 자료만 사용합니다. 운영 행과 파일은 변경하지 않으며 실제 실행은 계속 비활성 상태입니다.")) return;
      const button = formElement.querySelector("button[type='submit']");
      if (button) button.disabled = true;
      try {
        const payload = await postJson(`/data-deletions/${encodeURIComponent(requestId)}/combined-rehearsals`, {
          backup_verification_run_id: Number(formElement.dataset.verificationRunId),
          quarantine_planning_run_id: Number(formElement.dataset.planningRunId),
          confirmation_text: confirmationText,
          actor_id: actorId,
          note: note || null,
        });
        const run = payload.combined_rehearsal || {};
        await loadDataDeletionRequestDetail(requestId);
        dataDeletionStatus.textContent = `통합 모의 실행 ${operationValueLabel(run.result_status || "unknown")} · MySQL 삭제 / 되돌림: ${formatInteger(run.mysql_deleted_row_count || 0)} / ${formatInteger(run.mysql_rolled_back_row_count || 0)} · 임시 공간 정리: ${run.scratch_resources_removed ? "완료" : "차단"} · 운영 행·파일, 격리 및 삭제는 비활성 상태를 유지했습니다.`;
      } finally {
        if (button) button.disabled = false;
      }
    }

    async function runDataDeletionFaultMatrix(formElement) {
      const values = new FormData(formElement);
      const reviewer = new FormData(dataDeletionFilterForm);
      const requestId = formElement.dataset.requestId || "";
      const actorId = String(reviewer.get("actor_id") || "").trim();
      const note = String(reviewer.get("note") || "").trim();
      const confirmationText = String(values.get("confirmation_text") || "").trim();
      if (!actorId) throw new Error("로컬 검토자 ID가 필요합니다.");
      if (!confirmationText) throw new Error("정확한 격리 장애 행렬 확인 문구가 필요합니다.");
      if (!window.confirm("격리된 장애 행렬을 실행할까요? 임시 테이블 DELETE 이후 장애 1개와 합성 격리 시험 자료 장애 3개만 주입합니다. 운영 행과 파일은 변경하지 않으며 실제 삭제는 계속 비활성 상태입니다.")) return;
      const button = formElement.querySelector("button[type='submit']");
      if (button) button.disabled = true;
      try {
        const payload = await postJson(`/data-deletions/${encodeURIComponent(requestId)}/fault-matrix-runs`, {
          combined_rehearsal_run_id: Number(formElement.dataset.combinedRunId),
          confirmation_text: confirmationText,
          actor_id: actorId,
          note: note || null,
        });
        const run = payload.fault_matrix_run || {};
        await loadDataDeletionRequestDetail(requestId);
        dataDeletionStatus.textContent = `장애 행렬 ${operationValueLabel(run.result_status || "unknown")} · 통과 및 격리: ${formatInteger(run.passed_scenario_count || 0)} / ${formatInteger(run.scenario_count || 0)} · 임시 공간 정리: ${run.scratch_resources_removed ? "완료" : "차단"} · 운영 행·파일, 격리, 복원 및 삭제는 비활성 상태를 유지했습니다.`;
      } finally {
        if (button) button.disabled = false;
      }
    }

    async function generateDataDeletionReviewPacket(formElement) {
      const values = new FormData(formElement);
      const reviewer = new FormData(dataDeletionFilterForm);
      const requestId = formElement.dataset.requestId || "";
      const actorId = String(reviewer.get("actor_id") || "").trim();
      const note = String(reviewer.get("note") || "").trim();
      const confirmationText = String(values.get("confirmation_text") || "").trim();
      if (!actorId) throw new Error("로컬 검토자 ID가 필요합니다.");
      if (!confirmationText) throw new Error("정확한 검토 패킷 확인 문구가 필요합니다.");
      if (!window.confirm("변경 불가 검토 패킷을 생성할까요? 감사 행 1개와 JSON 내보내기만 기록하며 승인이나 실행 준비를 부여하지 않고 삭제를 실행할 수도 없습니다.")) return;
      const button = formElement.querySelector("button[type='submit']");
      if (button) button.disabled = true;
      try {
        const payload = await postJson(`/data-deletions/${encodeURIComponent(requestId)}/review-packets`, {
          fault_matrix_run_id: Number(formElement.dataset.faultMatrixRunId),
          confirmation_text: confirmationText,
          actor_id: actorId,
          note: note || null,
        });
        const packet = payload.review_packet || {};
        await loadDataDeletionRequestDetail(requestId);
        dataDeletionStatus.textContent = `검토 패킷 #${packet.id || "?"}을 ${operationValueLabel(packet.review_status || "unknown")} 상태로 기록했습니다. JSON: ${payload.export_url || "사용 불가"} · 승인, 실행 준비 승격 및 실제 삭제는 계속 비활성 상태입니다.`;
      } finally {
        if (button) button.disabled = false;
      }
    }

    async function recordDataDeletionBackupEvidence(formElement) {
      const values = new FormData(formElement);
      const reviewer = new FormData(dataDeletionFilterForm);
      const requestId = formElement.dataset.requestId || "";
      const actorId = String(reviewer.get("actor_id") || "").trim();
      const note = String(reviewer.get("note") || "").trim();
      if (!actorId) throw new Error("로컬 검토자 ID가 필요합니다.");
      if (!window.confirm("변경 불가 백업 증거를 기록할까요? 백업 내용 자체를 만들거나 검증하지는 않습니다.")) return;
      await postJson(`/data-deletions/${encodeURIComponent(requestId)}/backup-evidence`, {
        dry_run_plan_id: Number(formElement.dataset.planId),
        prerequisite_key: String(values.get("prerequisite_key") || ""),
        artifact_path: optionalFormText(values, "artifact_path"),
        artifact_sha256: optionalFormText(values, "artifact_sha256"),
        artifact_size_bytes: optionalFormNumber(values, "artifact_size_bytes"),
        covered_row_count: optionalFormNumber(values, "covered_row_count"),
        covered_file_count: optionalFormNumber(values, "covered_file_count"),
        covered_file_bytes: optionalFormNumber(values, "covered_file_bytes"),
        checked_path: optionalFormText(values, "checked_path"),
        available_bytes: optionalFormNumber(values, "available_bytes"),
        backup_created_at_kst: optionalFormText(values, "backup_created_at_kst"),
        verified_at_kst: optionalFormText(values, "verified_at_kst"),
        restore_tested_at_kst: optionalFormText(values, "restore_tested_at_kst"),
        checksums_verified: values.get("checksums_verified") === "on",
        restore_test_passed: values.get("restore_test_passed") === "on",
        actor_id: actorId,
        note: note || null,
      });
      await loadDataDeletionRequestDetail(requestId);
      dataDeletionStatus.textContent = "변경 불가 백업 증거를 기록했습니다. 실제 백업·삭제 작업은 실행하지 않았습니다.";
    }

    async function runDataDeletionRehearsal(requestId, planId) {
      const reviewer = new FormData(dataDeletionFilterForm);
      const actorId = String(reviewer.get("actor_id") || "").trim();
      const note = String(reviewer.get("note") || "").trim();
      if (!actorId) throw new Error("로컬 검토자 ID가 필요합니다.");
      if (!window.confirm("메타데이터만 사용하는 모의 절차를 실행할까요? 백업, 체크섬, 복원 또는 삭제 작업은 실행하지 않습니다.")) return;
      await postJson(`/data-deletions/${encodeURIComponent(requestId)}/rehearsals`, {
        dry_run_plan_id: Number(planId),
        actor_id: actorId,
        note: note || null,
      });
      await loadDataDeletionRequestDetail(requestId);
      dataDeletionStatus.textContent = "실행 없는 모의 절차를 기록했습니다. 실제 삭제는 계속 비활성 상태입니다.";
    }

    async function loadDataDeletionConfirmationState(requestId) {
      const response = await fetch(`/data-deletions/${encodeURIComponent(requestId)}/confirmation-state`);
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || response.statusText);
      }
      const payload = await response.json();
      const host = document.querySelector("#dataDeletionConfirmation");
      host.innerHTML = renderDataDeletionConfirmationState(payload.confirmation_state);
    }

    async function captureDataDeletionSnapshot(requestId) {
      const form = new FormData(dataDeletionFilterForm);
      const actorId = String(form.get("actor_id") || "").trim();
      const note = String(form.get("note") || "").trim();
      if (!actorId) throw new Error("로컬 검토자 ID가 필요합니다.");
      await postJson(`/data-deletions/${encodeURIComponent(requestId)}/preview-snapshots`, {
        actor_id: actorId,
        note: note || null,
      });
      await loadDataDeletionRequestDetail(requestId);
      dataDeletionStatus.textContent = "변경 불가 미리보기 스냅샷을 저장했습니다. 실제 삭제는 계속 비활성 상태입니다.";
    }

    async function confirmDataDeletionSnapshot(requestId, snapshotId, fingerprint) {
      const form = new FormData(dataDeletionFilterForm);
      const actorId = String(form.get("actor_id") || "").trim();
      const note = String(form.get("note") || "").trim();
      const input = document.querySelector(`#dataDeletionConfirmationText-${CSS.escape(String(snapshotId))}`);
      const confirmationText = String(input?.value || "").trim();
      if (!actorId) throw new Error("로컬 검토자 ID가 필요합니다.");
      if (!confirmationText) throw new Error("전체 확인 문구가 필요합니다.");
      if (!window.confirm("이 지문값에 연결된 확인 기록을 저장할까요? 이 작업도 데이터를 삭제하지 않습니다.")) return;
      await postJson(`/data-deletions/${encodeURIComponent(requestId)}/confirmations`, {
        snapshot_id: Number(snapshotId),
        fingerprint_sha256: fingerprint,
        confirmation_text: confirmationText,
        actor_id: actorId,
        note: note || null,
      });
      await loadDataDeletionRequestDetail(requestId);
      dataDeletionStatus.textContent = "지문값에 연결된 확인 기록을 저장했습니다. 실제 삭제는 계속 비활성 상태입니다.";
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
        <strong>요청 #${escapeHtml(request.id)} / ${escapeHtml(operationValueLabel(request.status))}</strong>
        <div>${escapeHtml(request.player_name)} / ${escapeHtml(displayCode(request.shard, "shard"))} / ${escapeHtml(operationValueLabel(request.deletion_scope))}</div>
        <div>요청 사유: ${escapeHtml(request.reason || "-")}</div>
        <div>검토자: ${escapeHtml(request.reviewed_by || "-")} / ${escapeHtml(request.review_note || "-")}</div>
        <div>실제 실행: ${payload.execution_enabled ? "활성" : "비활성"}</div>
        <ul>
          ${events.map((event) => `<li>${escapeHtml(event.created_at_kst)} / ${escapeHtml(event.event_type)} / ${escapeHtml(event.actor_type)}:${escapeHtml(event.actor_id)} / ${escapeHtml(event.note || "-")}</li>`).join("")}
        </ul>
        <div id="dataDeletionPreview" class="status">읽기 전용 영향 미리보기를 불러오는 중...</div>
        <div id="dataDeletionConfirmation" class="status">변경 불가 확인 상태를 불러오는 중...</div>
        <div id="dataDeletionDryRun" class="status">확인된 삭제 사전 점검 상태를 불러오는 중...</div>
        <div id="dataDeletionBackupReadiness" class="status">백업 증거와 모의 실행 상태를 불러오는 중...</div>`;
      const previewHost = document.querySelector("#dataDeletionPreview");
      try {
        const previewUrl = payload.preview_url || `/data-deletions/${encodeURIComponent(requestId)}/preview`;
        const previewResponse = await fetch(`${previewUrl}?file_limit=100`);
        if (!previewResponse.ok) {
          const error = await previewResponse.json().catch(() => ({ detail: previewResponse.statusText }));
          throw new Error(error.detail || previewResponse.statusText);
        }
        const previewPayload = await previewResponse.json();
        previewHost.innerHTML = renderDataDeletionPreview(previewPayload.preview);
      } catch (error) {
        previewHost.textContent = `미리보기 오류: ${error.message}`;
      }
      try {
        await loadDataDeletionConfirmationState(requestId);
      } catch (error) {
        const confirmationHost = document.querySelector("#dataDeletionConfirmation");
        confirmationHost.textContent = `확인 상태 오류: ${error.message}`;
      }
      try {
        await loadDataDeletionDryRunState(requestId);
      } catch (error) {
        const dryRunHost = document.querySelector("#dataDeletionDryRun");
        dryRunHost.textContent = `사전 점검 상태 오류: ${error.message}`;
      }
      try {
        await loadDataDeletionBackupReadiness(requestId);
      } catch (error) {
        const backupHost = document.querySelector("#dataDeletionBackupReadiness");
        backupHost.textContent = `백업 준비 상태 오류: ${error.message}`;
      }
    }

    async function reviewDataDeletionRequest(requestId, action) {
      const form = new FormData(dataDeletionFilterForm);
      const actorId = String(form.get("actor_id") || "").trim();
      const note = String(form.get("note") || "").trim();
      if (!actorId) throw new Error("로컬 검토자 ID가 필요합니다.");
      const warning = action === "approve"
        ? "이 요청을 승인할까요? 승인 기록만 남기며 데이터를 삭제하지 않습니다."
        : `${{ reject: "거절", cancel: "취소" }[action] || action} 처리를 기록할까요?`;
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
      dataDeletionStatus.textContent = `${{ approve: "승인", reject: "거절", cancel: "취소" }[action] || action} 처리를 기록했습니다. 실제 삭제는 계속 비활성 상태입니다.`;
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
      const text = String(value ?? "").trim();
      if (!text) return fallback;
      const parsed = Number(text);
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
        "granularity",
        "game_mode",
        "team_mode",
        "perspective",
        "match_type",
        "map_name",
        "is_custom_match",
        "from_date_kst",
        "to_date_kst",
        "bucket_limit",
      ];
      if (!lookupKeys.some((key) => params.has(key))) return false;

      const hash = window.location.hash.replace(/^#/, "");
      const shard = lookupUrlChoice(firstUrlParam(params, ["lookup_shard", "shard"]), ["steam", "kakao"], "steam");
      const target = firstUrlParam(params, ["lookup_target", "target", "name", "account_id"]);
      const weapon = firstUrlParam(params, ["lookup_weapon", "weapon", "weapon_code"]);
      const matchId = firstUrlParam(params, ["lookup_match_id", "match_id"]);
      const minMatches = lookupUrlBoundedNumber(
        firstUrlParam(params, ["lookup_min_matches", "min_matches"]),
        1,
        1,
        2147483647,
      );
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
      const trendGranularity = lookupUrlChoice(
        firstUrlParam(params, ["granularity"]),
        ["hour", "date", "week", "month", "quarter", "year", "map", "weapon", "game_mode", "team_mode", "perspective", "match_type", "season_state"],
        "month",
      );
      const trendGameMode = firstUrlParam(params, ["game_mode"]);
      const trendTeamMode = lookupUrlChoice(firstUrlParam(params, ["team_mode"]), ["", "solo", "duo", "squad", "unknown"], "");
      const trendPerspective = lookupUrlChoice(firstUrlParam(params, ["perspective"]), ["", "fpp", "tpp", "unknown"], "");
      const trendMatchType = firstUrlParam(params, ["match_type"]);
      const trendMapName = firstUrlParam(params, ["map_name"]);
      const trendCustom = lookupUrlChoice(firstUrlParam(params, ["is_custom_match"]), ["", "true", "false"], "");
      const trendFromDate = firstUrlParam(params, ["from_date_kst"]);
      const trendToDate = firstUrlParam(params, ["to_date_kst"]);
      const trendBucketLimit = lookupUrlBoundedNumber(firstUrlParam(params, ["bucket_limit"]), 120, 1, 500);

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
        setFormElementValue(discordGrantForm, "member_query", discordPermissionUserId);
        discordSettingsPrefill.permission_group = discordPermissionGroupValue;
        discordSettingsPrefill.permission_guild_id = discordPermissionGuildId;
      }
      if (shouldPrefillSection(hash, "discord-scopes")) {
        discordSettingsPrefill.scope_guild_id = discordScopeGuildId;
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
        rankingGuildPrefill = rankingGuildId;
        setFormElementValue(rankingForm, "shard", rankingShard);
        setFormElementValue(rankingForm, "metric", rankingMetric);
        setFormElementValue(rankingForm, "limit", String(rankingLimit));
      }

      if (shouldPrefillSection(hash, "profile-lookup")) {
        setFormElementValue(document.querySelector("#profileForm"), "shard", shard);
        setFormElementValue(document.querySelector("#profileForm"), "target", target);
      }
      if (shouldPrefillSection(hash, "trend-lookup")) {
        const trendForm = document.querySelector("#trendForm");
        setFormElementValue(trendForm, "shard", shard);
        setFormElementValue(trendForm, "target", target);
        setFormElementValue(trendForm, "granularity", trendGranularity);
        setFormElementValue(trendForm, "game_mode", trendGameMode);
        setFormElementValue(trendForm, "team_mode", trendTeamMode);
        setFormElementValue(trendForm, "perspective", trendPerspective);
        setFormElementValue(trendForm, "match_type", trendMatchType);
        setFormElementValue(trendForm, "map_name", trendMapName);
        setFormElementValue(trendForm, "is_custom_match", trendCustom);
        setFormElementValue(trendForm, "from_date_kst", trendFromDate);
        setFormElementValue(trendForm, "to_date_kst", trendToDate);
        setFormElementValue(trendForm, "bucket_limit", String(trendBucketLimit));
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

    function intelligenceDefinitions(report) {
      return new Map((report.metric_definitions || []).map((definition) => [definition.key, definition]));
    }

    function intelligenceValue(key, value) {
      if (value === null || value === undefined) return "미수집";
      if (typeof value === "string") return value;
      if (["win_rate", "top10_rate", "accuracy", "headshot_hit_rate", "headshot_hit_taken_rate", "fight_win_rate"].includes(key)) {
        return percent(value);
      }
      if (key.includes("survival_seconds")) return minutes(value);
      if (key.endsWith("distance_m") || key.endsWith("distance_meters")) return distanceKm(value);
      if (key === "longest_kill_m" || key === "avg_landing_distance_m") return distanceM(value);
      if (key === "kda" || key.startsWith("avg_") || key === "heal_amount" || key === "vehicle_damage") {
        return formatNumber(value, 2);
      }
      return formatNumber(value, 1);
    }

    function intelligenceQuality(report) {
      const coverage = report.coverage || {};
      const activityMatches = report.support?.activity_covered_matches || 0;
      const tone = coverage.status === "complete" ? "" : (coverage.status === "partial" ? "warning" : "error");
      const labels = {
        complete: "행동 텔레메트리 처리 완료",
        partial: "행동 텔레메트리 일부 처리",
        not_processed: "행동 텔레메트리 미처리",
        unavailable: "저장된 텔레메트리 없음",
      };
      const last = coverage.last_processed_at_kst
        ? ` · 마지막 처리 ${formatKstShort(coverage.last_processed_at_kst)}`
        : "";
      return `<div class="intelligence-quality-strip ${tone}">
        <div><strong>${escapeHtml(labels[coverage.status] || "수집 상태 확인 필요")}</strong>
          <small>${formatInteger(coverage.eligible_processed_matches)}/${formatInteger(coverage.telemetry_matches)}경기${escapeHtml(last)} · 미처리 ${formatInteger(coverage.missing_match_count)}경기 · 활동 평균 분모 ${formatInteger(activityMatches)}경기</small>
        </div>
        <output>${escapeHtml(percent(coverage.coverage_rate || 0))}</output>
      </div>`;
    }

    function intelligenceKpis(entries) {
      return `<div class="intelligence-kpi-grid">${entries.map(([label, key, value]) => (
        `<div class="intelligence-kpi"><span>${escapeHtml(label)}</span><strong>${escapeHtml(intelligenceValue(key, value))}</strong></div>`
      )).join("")}</div>`;
    }

    function intelligenceMetricSection(report, title, entries) {
      const definitions = intelligenceDefinitions(report);
      return `<div class="intelligence-data-section"><h3>${escapeHtml(title)}</h3><div class="intelligence-metric-grid">${entries.map(([label, key, value]) => {
        const definition = definitions.get(key);
        const tooltip = definition ? `${definition.formula_ko} · 분모: ${definition.denominator_ko}` : "";
        return `<div class="intelligence-metric-row"${tooltip ? ` title="${attr(tooltip)}"` : ""}><span>${escapeHtml(label)}</span><strong>${escapeHtml(intelligenceValue(key, value))}</strong></div>`;
      }).join("")}</div></div>`;
    }

    function renderIntelligenceOverview(report) {
      const overview = report.overview || {};
      const combat = report.combat || {};
      const survival = report.survival || {};
      const support = report.support || {};
      const mobility = report.mobility || {};
      const vehicle = report.vehicle || {};
      const environment = report.environment || {};
      const loot = report.loot || {};
      const topItems = (loot.top_picked_items || []).slice(0, 8);
      const itemList = topItems.length
        ? `<div class="intelligence-list">${topItems.map((item) => `<div class="intelligence-list-row"><span>${escapeHtml(item.item_name_ko || item.item_code || "-")}<small>${escapeHtml(displayCode(item.item_category, "item_category") || "분류 없음")}</small></span><strong>${formatInteger(item.picked_up_events)}회</strong></div>`).join("")}</div>`
        : '<div class="intelligence-empty">현재 조건에서 정규화된 아이템 기록이 없습니다.</div>';
      return intelligenceQuality(report) + intelligenceKpis([
        ["경기", "matches", overview.matches], ["치킨율", "win_rate", overview.win_rate],
        ["KDA", "kda", overview.kda], ["평균 준 피해", "avg_damage_dealt", overview.avg_damage_dealt],
        ["명중률", "accuracy", combat.accuracy], ["헤드샷 명중", "headshot_hit_rate", combat.headshot_hit_rate],
        ["교전 승리율", "fight_win_rate", combat.fight_win_rate], ["경기당 교전", "avg_fights_per_match", combat.avg_fights_per_match],
        ["평균 생존", "avg_survival_seconds", overview.avg_survival_seconds], ["평균 이동", "avg_total_distance_m", overview.avg_total_distance_m],
        ["총 체력 회복량", "heal_amount", support.heal_amount], ["팀원 부활", "revives_caused", support.revives_caused],
      ])
      + intelligenceMetricSection(report, "전투와 교전", [
        ["킬 / 어시스트 / 사망", "kills", `${formatInteger(combat.kills)} / ${formatInteger(combat.assists)} / ${formatInteger(combat.deaths)}`],
        ["가한 / 당한 기절", "dbnos_caused", `${formatInteger(combat.dbnos_caused)} / ${formatInteger(combat.dbnos_taken)}`],
        ["준 / 받은 피해", "damage_dealt", `${formatNumber(combat.damage_dealt, 1)} / ${formatNumber(combat.damage_taken, 1)}`],
        ["발사 / 명중", "shots_fired", `${formatInteger(combat.shots_fired)} / ${formatInteger(combat.shots_hit)}`],
        ["캐릭터 / 차량 명중", "shots_hit", hitTargetEvidence(combat)],
        ["차량에 가한 피해", "vehicle_damage_dealt", formatNumber(combat.vehicle_damage_dealt, 1)],
        ["헤드샷 명중 / 피격", "headshot_hits", `${formatInteger(combat.headshot_hits)} / ${formatInteger(combat.headshot_hits_taken)}`],
        ["교전 승 / 패", "fight_count", `${formatInteger(combat.fight_wins)} / ${formatInteger(combat.fight_losses)}`],
      ].map(([label, key, value]) => [label, key, value]))
      + intelligenceMetricSection(report, "생존과 지원", [
        ["활동 지표 처리 경기", "activity_covered_matches", `${formatInteger(support.activity_covered_matches)} / ${formatInteger(overview.matches)}`],
        ["TOP 10 비율", "top10_rate", survival.top10_rate], ["평균 등수", "avg_placement", survival.avg_placement],
        ["최장 킬 거리", "longest_kill_m", survival.longest_kill_m], ["최대 연속 킬", "max_kill_streak", survival.max_kill_streak],
        ["회복 아이템 이벤트", "item_heal_events", support.item_heal_events], ["아이템 회복량", "item_heal_amount", support.item_heal_amount],
        ["부스트 지속 회복량", "passive_heal_amount", support.passive_heal_amount], ["부스트 사용", "boost_uses", support.participant_boost_uses],
        ["투척물 사용", "throwable_uses", support.throwable_uses], ["부활 받음", "revives_received", support.revives_received],
      ])
      + intelligenceMetricSection(report, "이동과 차량", [
        ["보행 거리", "walk_distance_m", mobility.walk_distance_m], ["차량 거리", "ride_distance_m", mobility.ride_distance_m],
        ["수영 거리", "swim_distance_m", mobility.swim_distance_m], ["파쿠르 / 렛지", "vaults", `${formatInteger(mobility.vaults)} / ${formatInteger(mobility.ledge_grabs)}`],
        ["차량 탑승 / 하차", "vehicle_rides", `${formatInteger(vehicle.rides)} / ${formatInteger(vehicle.leaves)}`], ["차량 피해", "vehicle_damage", vehicle.damage],
        ["차량 / 바퀴 파괴", "vehicle_destroys", `${formatInteger(vehicle.destroys)} / ${formatInteger(vehicle.wheel_destroys)}`], ["비상 호출 / 재배치", "redeploys", `${formatInteger(mobility.emergency_pickup_calls)} / ${formatInteger(mobility.redeploys)}`],
      ])
      + intelligenceMetricSection(report, "방어구와 환경", [
        ["방어구 파괴", "armor_destroys_caused", environment.armor_destroys_caused], ["방어구 파괴당함", "armor_destroys_taken", environment.armor_destroys_taken],
        ["오브젝트 상호작용", "object_interactions", environment.object_interactions], ["오브젝트 파괴", "object_destroys", environment.object_destroys],
        ["트렁크에서 획득", "vehicle_trunk_pickup_events", loot.vehicle_trunk_pickup_events], ["트렁크에 보관", "vehicle_trunk_put_events", loot.vehicle_trunk_put_events],
      ])
      + `<div class="intelligence-data-section"><h3>주요 획득 아이템</h3>${itemList}</div>`;
    }

    function intelligenceChartDefinition(metric) {
      const custom = {
        avg_heal_amount: {
          label: "경기당 총 체력 회복량",
          value: (row) => Number(row.avg_heal_amount || 0),
          format: (value) => formatNumber(value, 1),
          basis: "LogHeal.healAmount 합계(healamount 호환) ÷ 행동 파서 처리 경기 수",
        },
        avg_revives_caused: {
          label: "경기당 팀원 부활",
          value: (row) => Number(row.avg_revives_caused || 0),
          format: (value) => `${formatNumber(value, 2)}회`,
          basis: "부활 실행 횟수 ÷ 행동 파서 처리 경기 수",
        },
        avg_throwable_uses: {
          label: "경기당 투척물 사용",
          value: (row) => Number(row.avg_throwable_uses || 0),
          format: (value) => `${formatNumber(value, 2)}회`,
          basis: "투척물 사용 횟수 ÷ 행동 파서 처리 경기 수",
        },
      };
      return custom[metric] || trendMetricDefinition(metric);
    }

    function intelligenceTrendBuckets(report) {
      const granularity = intelligenceTrendGranularity.value || "daily";
      const metric = intelligenceTrendMetric.value || "win_rate";
      const activityMetric = ["avg_heal_amount", "avg_revives_caused", "avg_throwable_uses"].includes(metric);
      return (report.trends?.[granularity] || []).filter((row) => (
        !activityMetric || Number(row.activity_covered_matches || 0) > 0
      )).map((row) => ({
        ...row,
        period_label: row.label,
        match_count: row.matches,
      }));
    }

    function intelligenceTotals(report) {
      return {
        match_count: report.overview?.matches || 0,
        ...(report.combat || {}),
        ...(report.survival || {}),
        ...(report.support || {}),
        ...(report.mobility || {}),
      };
    }

    function renderIntelligenceTrends(report) {
      const definition = intelligenceChartDefinition(intelligenceTrendMetric.value || "win_rate");
      const buckets = intelligenceTrendBuckets(report);
      if (!buckets.length) return intelligenceQuality(report) + '<div class="intelligence-empty">현재 조건에 표시할 시간 구간이 없습니다.</div>';
      return intelligenceQuality(report) + `<div class="metric-chart">${renderTimeSeriesLineChart(definition, buckets, {
        totals: intelligenceTotals(report),
        availablePointCount: buckets.length,
        title: `${definition.label} KST 변화`,
      })}</div>`;
    }

    function renderIntelligenceBreakdowns(report) {
      const dimension = intelligenceBreakdownDimension.value || "maps";
      const metric = intelligenceTrendMetric.value || "win_rate";
      const activityMetric = ["avg_heal_amount", "avg_revives_caused", "avg_throwable_uses"].includes(metric);
      const breakdownCategories = { maps: "map", game_modes: "game_mode", team_modes: "team_mode", perspectives: "perspective", match_types: "match_type", season_states: "season_state" };
      const rows = (report.breakdowns?.[dimension] || []).filter((row) => (
        !activityMetric || Number(row.activity_covered_matches || 0) > 0
      )).map((row) => ({
        ...row,
        period_label: breakdownCategories[dimension]
          ? displayCode(row.key || row.label, breakdownCategories[dimension])
          : row.label,
        match_count: row.matches,
      }));
      const definition = intelligenceChartDefinition(metric);
      if (!rows.length) return intelligenceQuality(report) + '<div class="intelligence-empty">현재 조건에 비교할 항목이 없습니다.</div>';
      const tableRows = rows.map((row) => `<tr>
        <td>${escapeHtml(breakdownCategories[dimension] ? displayCode(row.key || row.label, breakdownCategories[dimension]) : (row.label || row.key || "-"))}</td><td>${formatInteger(row.matches)}</td>
        <td>${percent(row.win_rate)}</td><td>${formatNumber(row.kda, 2)}</td>
        <td>${formatNumber(row.avg_damage_dealt, 1)}</td><td>${percent(row.accuracy)}</td>
        <td>${percent(row.headshot_hit_rate)}</td><td>${percent(row.fight_win_rate)}</td>
        <td>${formatNumber(row.avg_fights_per_match, 2)}</td><td>${minutes(row.avg_survival_seconds)}</td>
      </tr>`).join("");
      return intelligenceQuality(report)
        + `<div class="metric-chart">${renderTrendComparisonChart(definition, rows)}</div>`
        + `<div class="table-scroll intelligence-data-section"><table class="intelligence-table"><thead><tr><th>항목</th><th>경기</th><th>치킨율</th><th>KDA</th><th>평균 피해</th><th>명중</th><th>헤드샷</th><th>교전 승리</th><th>경기당 교전</th><th>평균 생존</th></tr></thead><tbody>${tableRows}</tbody></table></div>`;
    }

    function intelligenceDetailRows(rows, kind) {
      if (!rows?.length) return '<div class="intelligence-empty">기록 없음</div>';
      return `<div class="intelligence-list">${rows.slice(0, 16).map((row) => {
        const label = row.item_name_ko || row.vehicle_type_ko || row.object_type || row.action_ko || row.item_code || "분류 없음";
        const detail = kind === "vehicle"
          ? `${formatNumber(row.distance_m, 0)}m · 피해 ${formatNumber(row.damage, 1)}`
          : `${escapeHtml(row.action_ko || row.action || "이벤트")} · ${formatNumber(row.amount, 1)}`;
        return `<div class="intelligence-list-row"><span>${escapeHtml(label)}<small>${detail}</small></span><strong>${formatInteger(row.event_count)}회</strong></div>`;
      }).join("")}</div>`;
    }

    function renderIntelligenceEvidence(report) {
      const coverage = report.coverage || {};
      const eventCoverage = coverage.event_type_coverage || {};
      const rawOnly = eventCoverage.raw_only_types || [];
      const definitions = report.metric_definitions || [];
      const rawRows = rawOnly.length ? rawOnly.map((row) => `<tr>
        <td>${escapeHtml(row.label_ko || row.event_type)}</td><td class="identifier">${escapeHtml(row.event_type)}</td>
        <td>${escapeHtml(row.domain || "-")}</td><td>${formatInteger(row.event_count)}</td>
        <td>${formatInteger(row.tracked_event_count)}</td><td>${escapeHtml(row.note || "-")}</td>
      </tr>`).join("") : '<tr><td colspan="6">원본 전용 이벤트가 없습니다.</td></tr>';
      const definitionRows = definitions.map((definition) => `<div class="intelligence-definition">
        <strong>${escapeHtml(definition.label_ko)}</strong>
        <span>${escapeHtml(definition.formula_ko)}<br><small>분모: ${escapeHtml(definition.denominator_ko)}</small></span>
        <small>${escapeHtml(definition.source)}${definition.caveat_ko ? `<br>${escapeHtml(definition.caveat_ko)}` : ""}</small>
      </div>`).join("");
      const details = report.activity_details || {};
      return intelligenceQuality(report)
        + `<div class="intelligence-data-section"><h3>회복 아이템</h3>${intelligenceDetailRows(details.healing_items, "item")}</div>`
        + `<div class="intelligence-data-section"><h3>투척물과 플레어</h3>${intelligenceDetailRows(details.throwables, "item")}</div>`
        + `<div class="intelligence-data-section"><h3>차량 행동</h3>${intelligenceDetailRows(details.vehicles, "vehicle")}</div>`
        + `<div class="intelligence-data-section"><h3>원본 보존·미분류 이벤트</h3><div class="table-scroll"><table class="intelligence-table"><thead><tr><th>이벤트</th><th>코드</th><th>영역</th><th>전체</th><th>추적 관련</th><th>상태</th></tr></thead><tbody>${rawRows}</tbody></table></div></div>`
        + `<div class="intelligence-data-section"><h3>지표 정의와 분모</h3>${definitionRows}</div>`;
    }

    function renderIntelligenceReport() {
      if (!activeIntelligenceReport) {
        intelligenceBody.textContent = "분석 대기 중";
        return;
      }
      intelligenceChartOptions.hidden = !["trends", "breakdowns"].includes(activeIntelligenceView);
      intelligenceGranularityOption.hidden = activeIntelligenceView !== "trends";
      intelligenceBreakdownOption.hidden = activeIntelligenceView !== "breakdowns";
      for (const button of intelligenceViewControls.querySelectorAll("[data-intelligence-view]")) {
        button.classList.toggle("active", button.dataset.intelligenceView === activeIntelligenceView);
      }
      const renderers = {
        overview: renderIntelligenceOverview,
        trends: renderIntelligenceTrends,
        breakdowns: renderIntelligenceBreakdowns,
        evidence: renderIntelligenceEvidence,
      };
      intelligenceBody.innerHTML = (renderers[activeIntelligenceView] || renderIntelligenceOverview)(activeIntelligenceReport);
    }

    async function loadPlayerIntelligence(formElement) {
      const form = new FormData(formElement);
      const player = selectedRegisteredPlayer(formElement);
      await setActiveAnalysisPlayer(player);
      const params = new URLSearchParams({ shard: player.shard, account_id: player.account_id, trend_limit: "1000" });
      appendAnalysisFilters(form, params);
      intelligenceBody.textContent = "상세 데이터를 분석하는 중...";
      const payload = await fetchAnalysisJson(`/players/intelligence?${params.toString()}`);
      activeIntelligenceReport = payload.intelligence;
      intelligenceViewControls.hidden = false;
      renderIntelligenceReport();
    }

    async function loadPlayerProfile(target, shard) {
      const params = new URLSearchParams({ shard });
      if (target.startsWith("account.")) {
        params.set("account_id", target);
      } else {
        params.set("name", target);
      }
      const [profileResponse, fightResult] = await Promise.all([
        fetch(`/players/profile?${params.toString()}`),
        fetch(`/players/fight-outcomes?${params.toString()}&weapon_limit=3&loadout_limit=3&recent_limit=5`)
          .then((response) => ({ response }))
          .catch((error) => ({ error })),
      ]);
      if (!profileResponse.ok) {
        const error = await profileResponse.json().catch(() => ({ detail: profileResponse.statusText }));
        throw new Error(error.detail || profileResponse.statusText);
      }
      const profile = (await profileResponse.json()).profile;
      activeProfilePlayer = profile.player;
      const fightResponse = fightResult.response;
      let fights = null;
      let fightLoadWarning = "";
      if (fightResponse?.ok) {
        fights = (await fightResponse.json()).fight_outcomes;
      } else if (fightResponse) {
        const error = await fightResponse.json().catch(() => ({ detail: fightResponse.statusText }));
        fightLoadWarning = error.detail || fightResponse.statusText || "알 수 없는 오류";
      } else {
        fightLoadWarning = fightResult.error?.message || "네트워크 연결 오류";
      }
      const fightMetricsAvailable = Boolean(fights);
      const totals = profile.totals;
      const fightTotals = fights?.totals || {
        fight_count: 0,
        wins: 0,
        losses: 0,
        fight_win_rate: 0,
        kill_wins: 0,
        dbno_wins: 0,
        death_losses: 0,
        dbno_losses: 0,
        excluded_non_firearm_contexts: 0,
      };
      const metricCells = [
        ["경기", formatInteger(totals.match_count) + "전"],
        ["치킨", formatInteger(totals.wins) + "회 · " + percent(totals.win_rate)],
        ["KDA", formatNumber(totals.kda, 2)],
        ["킬 / 사망 / 어시", formatInteger(totals.kills) + " / " + formatInteger(totals.deaths) + " / " + formatInteger(totals.assists)],
        ["기절시킴 / 당함", formatInteger(totals.dbnos_caused) + " / " + formatInteger(totals.dbnos_taken)],
        ["평균 딜 / 받은 딜", formatNumber(totals.avg_damage_dealt, 1) + " / " + formatNumber(totals.avg_damage_taken, 1)],
        ["평균 생존", minutes(totals.avg_survival_seconds)],
        ["평균 이동", distanceKm(totals.avg_movement_distance_m)],
        ["명중 확률", accuracyBreakdownText(totals.accuracy, totals.accuracy_breakdown)],
        ["헤드샷 명중 확률", `${percent(totals.headshot_hit_rate)} · ${formatInteger(totals.headshot_hits)}/${formatInteger(characterHitCount(totals))} 캐릭터 명중`],
        ["캐릭터 / 차량 명중", hitTargetEvidence(totals)],
        ["차량에 가한 피해", formatNumber(totals.vehicle_damage_dealt, 1)],
        ["헤드샷 킬 비율", `${percent(totals.headshot_kill_rate)} · ${formatInteger(totals.headshot_kills)}/${formatInteger(totals.kills)}킬`],
        ["교전 승리 확률", fightMetricsAvailable
          ? `${percent(fightTotals.fight_win_rate)} · ${formatInteger(fightTotals.wins)}/${formatInteger(fightTotals.fight_count)}교전`
          : "불러오기 실패"],
        ["경기당 평균 교전", fightMetricsAvailable
          ? `${formatNumber((fightTotals.fight_count || 0) / Math.max(1, totals.match_count || 0), 2)}회`
          : "불러오기 실패"],
      ];
      const topWeaponRows = (profile.top_weapons || []).slice(0, 5).map((weapon, index) => `
        <div class="result-row">
          <span>${index + 1}위</span>
          <strong>${escapeHtml(weapon.weapon_name)}</strong>
          <p>${formatInteger(weapon.kills)}킬 · ${formatNumber(weapon.damage_dealt, 0)}딜 · 명중 ${percent(weapon.accuracy)} · 헤드샷 명중 ${percent(weapon.headshot_hit_rate)}</p>
        </div>`).join("") || '<span class="result-caption">무기 기록 없음</span>';
      const fightUnavailable = '<span class="result-caption">교전 데이터를 불러오지 못했습니다.</span>';
      const fightWeaponRows = fightMetricsAvailable ? ((fights?.weapons || []).map((weapon) => `
        <div class="result-row">
          <span>교전 ${formatInteger(weapon.fight_count)}회</span>
          <strong>${escapeHtml(weapon.weapon_name)}</strong>
          <p>${formatInteger(weapon.wins)}승 ${formatInteger(weapon.losses)}패 · ${percent(weapon.fight_win_rate)}</p>
        </div>`).join("") || '<span class="result-caption">총기 교전 기록 없음</span>') : fightUnavailable;
      const fightLoadoutRows = fightMetricsAvailable ? ((fights?.loadouts || []).map((loadout) => `
        <div class="result-row">
          <span>${escapeHtml(loadout.weapon_name)}</span>
          <strong>${escapeHtml((loadout.attachment_names || []).join(" · ") || "파츠 없음")}</strong>
          <p>${formatInteger(loadout.wins)}승 ${formatInteger(loadout.losses)}패 · ${percent(loadout.fight_win_rate)}</p>
        </div>`).join("") || '<span class="result-caption">교전 조합 기록 없음</span>') : fightUnavailable;
      const recentRows = (profile.recent_matches || []).map((match) => (
        '<tr><td>' + escapeHtml(String(match.created_at_kst || "-").replace("T", " ").slice(0, 16)) + '</td>'
        + '<td>' + escapeHtml(match.map_name_ko || match.map_name || "-") + '</td>'
        + '<td>' + escapeHtml(match.game_mode_ko || displayCode(match.game_mode, "game_mode")) + '</td>'
        + '<td>' + (Number(match.win_place) === 1 ? "치킨" : "#" + (match.win_place || "-")) + '</td>'
        + '<td>' + formatInteger(match.kills) + ' / ' + formatInteger(match.assists) + ' / ' + formatInteger(match.dbnos_caused) + '</td>'
        + '<td>' + formatNumber(match.damage_dealt, 0) + '</td>'
        + '<td><button class="secondary" type="button" data-profile-match-id="' + attr(match.match_id) + '">상세</button></td></tr>'
      )).join("");
      profileBody.innerHTML = `<div class="result-shell">
        ${resultHeading(
          profile.player.current_name,
          `${displayCode(profile.player.shard, "shard")} · 완료된 매치 기준`,
          profile.player.active ? "수집 중" : "수집 중지",
          profile.player.active ? "success" : "",
        )}
        ${resultMetricGrid(metricCells)}
        ${fightLoadWarning
          ? `<div class="result-warning">교전 데이터 일부를 불러오지 못했습니다: ${escapeHtml(fightLoadWarning)}</div>`
          : ""}
        <div class="result-columns">
          ${resultSection("주요 무기", `<div class="result-list">${topWeaponRows}</div>`)}
          ${resultSection("교전 요약", fightMetricsAvailable ? resultTextRows([
            ["교전 승/패", `${formatInteger(fightTotals.wins)} / ${formatInteger(fightTotals.losses)} · ${percent(fightTotals.fight_win_rate)}`],
            ["승리 원인", `킬 ${formatInteger(fightTotals.kill_wins)} · 기절 ${formatInteger(fightTotals.dbno_wins)}`],
            ["패배 원인", `사망 ${formatInteger(fightTotals.death_losses)} · 기절 ${formatInteger(fightTotals.dbno_losses)}`],
            ["명중 지표", accuracyBreakdownText(totals.accuracy, totals.accuracy_breakdown)],
          ]) : fightUnavailable)}
        </div>
        <div class="result-columns">
          ${resultSection("부위별 명중 확률", resultChips(hitPartEntries(totals.hit_parts, totals.hit_part_rates)))}
          ${resultSection("부위별 피격 확률", resultChips(hitPartEntries(totals.taken_hit_parts, totals.taken_hit_part_rates)))}
        </div>
        ${resultSection("교전 무기", `<div class="result-list">${fightWeaponRows}</div>`)}
        ${resultSection("교전 조합", `<div class="result-list">${fightLoadoutRows}</div>`)}
        ${fightTotals.excluded_non_firearm_contexts
          ? `<span class="result-caption">총기 순위 제외: 비총기 장비 ${formatInteger(fightTotals.excluded_non_firearm_contexts)}건</span>`
          : ""}
        ${resultSection("최근 경기", `<div class="table-scroll"><table><thead><tr><th>KST</th><th>맵</th><th>모드</th><th>결과</th><th>킬/어시/기절</th><th>딜</th><th></th></tr></thead><tbody>${recentRows || '<tr><td colspan="7">완료 경기 데이터가 없습니다.</td></tr>'}</tbody></table></div>`)}
      </div>`;
    }

    async function loadPlayerTrends(formElement) {
      const form = new FormData(formElement);
      const player = selectedRegisteredPlayer(formElement);
      const params = new URLSearchParams({
        shard: player.shard,
        account_id: player.account_id,
        granularity: String(form.get("granularity") || "month"),
        bucket_limit: String(form.get("bucket_limit") || 120),
      });
      for (const name of [
        "game_mode",
        "team_mode",
        "perspective",
        "match_type",
        "map_name",
        "season_state",
        "is_custom_match",
        "year",
        "quarter",
        "month",
        "exact_date_kst",
        "hour",
        "from_date_kst",
        "to_date_kst",
      ]) {
        const value = String(form.get(name) || "");
        if (value) params.set(name, value);
      }
      const response = await fetch(`/players/trends?${params.toString()}`);
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || response.statusText);
      }
      const report = (await response.json()).trends;
      activeTrendReport = report;
      trendViewControls.hidden = false;
      const totals = report.totals;
      const granularityLabel = {
        hour: "시간대별",
        date: "일자별",
        week: "주별",
        month: "월별",
        quarter: "분기별",
        year: "연도별",
        map: "맵별",
        weapon: "무기별",
        game_mode: "게임 모드별",
        team_mode: "팀 모드별",
        perspective: "시점별",
        match_type: "매치 유형별",
        season_state: "시즌 상태별",
      }[report.granularity] || report.granularity;
      const filterLabels = {
        game_mode: "게임 모드",
        team_mode: "팀",
        perspective: "시점",
        match_type: "매치 유형",
        map_name: "맵",
        season_state: "시즌 상태",
        is_custom_match: "커스텀",
        year: "연도",
        quarter: "분기",
        month: "월",
        exact_date_kst: "특정 일자",
        hour: "시간대",
        from_date_kst: "시작일",
        to_date_kst: "종료일",
      };
      const filterCategories = {
        game_mode: "game_mode",
        team_mode: "team_mode",
        perspective: "perspective",
        match_type: "match_type",
        map_name: "map",
        season_state: "season_state",
      };
      const activeFilters = Object.entries(report.filters || {})
        .filter(([, value]) => value !== null && value !== "")
        .map(([key, value]) => {
          let displayValue = filterCategories[key] ? displayCode(value, filterCategories[key]) : String(value);
          if (key === "is_custom_match") displayValue = value ? "커스텀" : "일반";
          if (key === "quarter") displayValue = `${formatInteger(value)}분기`;
          if (key === "month") displayValue = `${formatInteger(value)}월`;
          if (key === "hour") displayValue = `${String(value).padStart(2, "0")}시`;
          return `${filterLabels[key] || key}: ${displayValue}`;
        });
      const bucketStatus = report.truncated
        ? `최근 ${formatInteger(report.returned_bucket_count)}/${formatInteger(report.available_bucket_count)}개 구간`
        : `${formatInteger(report.returned_bucket_count)}개 구간`;
      const categoricalGranularities = new Set(["map", "weapon", "game_mode", "team_mode", "perspective", "match_type", "season_state"]);
      const reportScopeLabel = categoricalGranularities.has(report.granularity)
        ? granularityLabel
        : `KST ${granularityLabel}`;
      const calculationNote = report.granularity === "weapon"
        ? "무기 경기 수는 해당 무기로 사격·피해·킬·어시스트·기절 기록이 있는 경기입니다. 치킨률은 그 경기 중 1위 비율이고, KDA 사망·피기절과 교전 승패는 당시 플레이어가 들고 있던 무기 기준입니다. 받은 피해·피격은 해당 상대 무기에 맞은 기록입니다."
        : "치킨률은 각 구간의 완료 경기 중 1위 비율이며, 교전 승률은 아군 피해를 제외한 승리·패배 교전 기준입니다.";
      trendSummary.innerHTML = `<div class="result-shell">
        ${resultHeading(report.player.current_name, reportScopeLabel, bucketStatus)}
        ${resultMetricGrid([
          ["경기", `${formatInteger(totals.match_count)}전`],
          ["치킨", `${formatInteger(totals.wins)}회 · ${percent(totals.win_rate)}`],
          ["비치킨", `${formatInteger(totals.non_wins)}회`],
          ["KDA", formatNumber(totals.kda, 2)],
          ["평균 딜", formatNumber(totals.avg_damage_dealt, 1)],
          ["명중 지표", accuracyBreakdownText(totals.accuracy, totals.accuracy_breakdown)],
          ["헤드샷 명중 확률", `${percent(totals.headshot_hit_rate)} · ${formatInteger(totals.headshot_hits)}/${formatInteger(characterHitCount(totals))} 캐릭터 명중`],
          ["캐릭터 / 차량 명중", hitTargetEvidence(totals)],
          ["헤드샷 킬 비율", `${percent(totals.headshot_kill_rate)} · ${formatInteger(totals.headshot_kills)}/${formatInteger(totals.kills)}킬`],
          ["교전 승리 확률", `${percent(totals.fight_win_rate)} · ${formatInteger(totals.fight_wins)}/${formatInteger(totals.fight_count)}교전`],
          ["경기당 평균 교전", `${formatNumber(totals.avg_fights_per_match, 2)}회`],
          ["경기당 킬 / 기절", `${formatNumber(totals.avg_kills, 2)} / ${formatNumber(totals.avg_dbnos_caused, 2)}`],
        ])}
        ${resultSection("적용 조건", resultChips(activeFilters, "전체 경기"))}
        <p class="trend-chart-note">${escapeHtml(calculationNote)}</p>
      </div>`;
      trendBody.innerHTML = (report.buckets || []).map((bucket) => `
        <tr>
          <td>${escapeHtml(bucket.period_label)}</td>
          <td>${formatInteger(bucket.match_count)}전 · ${formatInteger(bucket.wins)}치킨</td>
          <td>${percent(bucket.win_rate)}</td>
          <td>${formatInteger(bucket.kills)}/${formatInteger(bucket.deaths)}/${formatInteger(bucket.assists)}<br>KDA ${formatNumber(bucket.kda, 2)}</td>
          <td>${formatNumber(bucket.avg_kills, 2)} / ${formatNumber(bucket.avg_dbnos_caused, 2)}</td>
          <td>${formatNumber(bucket.avg_damage_dealt, 1)} / ${formatNumber(bucket.avg_damage_taken, 1)}</td>
          <td>${accuracyBreakdownText(bucket.accuracy, bucket.accuracy_breakdown)}</td>
          <td>${percent(bucket.headshot_hit_rate)}<br><span class="status">${formatInteger(bucket.headshot_hits)}/${formatInteger(characterHitCount(bucket))} 캐릭터 명중 · 차량 ${formatInteger(bucket.vehicle_hits)}회</span></td>
          <td>${percent(bucket.fight_win_rate)}<br><span class="status">${formatInteger(bucket.fight_wins)}/${formatInteger(bucket.fight_count)} · 경기당 ${formatNumber(bucket.avg_fights_per_match, 2)}회</span></td>
          <td>${formatInteger(bucket.dbnos_caused)} / ${formatInteger(bucket.dbnos_taken)}</td>
          <td>${minutes(bucket.avg_survival_seconds)}</td>
        </tr>
      `).join("") || `<tr><td colspan="11">조건에 맞는 완료 경기 데이터가 없습니다.</td></tr>`;
      trendCards.innerHTML = (report.buckets || []).map((bucket) => `
        <article class="trend-card">
          <strong>${escapeHtml(bucket.period_label)}</strong>
          <dl>
            <div><dt>경기 / 치킨</dt><dd>${formatInteger(bucket.match_count)}전 / ${formatInteger(bucket.wins)}회</dd></div>
            <div><dt>승률</dt><dd>${percent(bucket.win_rate)}</dd></div>
            <div><dt>K/D/A · KDA</dt><dd>${formatInteger(bucket.kills)}/${formatInteger(bucket.deaths)}/${formatInteger(bucket.assists)} · ${formatNumber(bucket.kda, 2)}</dd></div>
            <div><dt>경기당 킬 / 기절</dt><dd>${formatNumber(bucket.avg_kills, 2)} / ${formatNumber(bucket.avg_dbnos_caused, 2)}</dd></div>
            <div><dt>평균 딜 / 받은 딜</dt><dd>${formatNumber(bucket.avg_damage_dealt, 1)} / ${formatNumber(bucket.avg_damage_taken, 1)}</dd></div>
            <div><dt>헤드샷 명중 확률</dt><dd>${percent(bucket.headshot_hit_rate)} · ${formatInteger(bucket.headshot_hits)}/${formatInteger(characterHitCount(bucket))} 캐릭터 명중</dd></div>
            <div><dt>차량 명중 / 피해</dt><dd>${formatInteger(bucket.vehicle_hits)}회 / ${formatNumber(bucket.vehicle_damage_dealt, 1)}</dd></div>
            <div><dt>교전 승리 확률</dt><dd>${percent(bucket.fight_win_rate)} · ${formatInteger(bucket.fight_wins)}/${formatInteger(bucket.fight_count)}</dd></div>
            <div><dt>경기당 교전</dt><dd>${formatNumber(bucket.avg_fights_per_match, 2)}회</dd></div>
            <div><dt>기절 +/-</dt><dd>${formatInteger(bucket.dbnos_caused)} / ${formatInteger(bucket.dbnos_taken)}</dd></div>
            <div><dt>평균 생존</dt><dd>${minutes(bucket.avg_survival_seconds)}</dd></div>
          </dl>
          <div class="result-caption" style="margin-top:8px">${escapeHtml(accuracyBreakdownText(bucket.accuracy, bucket.accuracy_breakdown))}</div>
        </article>
      `).join("") || '<span class="result-caption">조건에 맞는 완료 경기 데이터가 없습니다.</span>';
      renderTrendView();
    }

    function trendMetricDefinition(metric) {
      const definitions = {
        win_rate: {
          label: "승률",
          value: (bucket) => bucket.win_rate,
          format: percent,
          percentage: true,
          basis: "치킨 경기 수 ÷ 완료 경기 수",
        },
        fight_win_rate: {
          label: "교전 승리 확률",
          value: (bucket) => bucket.fight_win_rate,
          format: percent,
          percentage: true,
          basis: "기록된 승리 결과(킬·가한 기절) ÷ 승리·패배 결과",
        },
        accuracy: {
          label: "명중 확률",
          value: (bucket) => bucket.accuracy,
          format: percent,
          percentage: true,
          basis: "일반 탄환의 명중 이벤트 ÷ 발사 이벤트; 산탄은 별도 셸당 펠릿 지표",
        },
        headshot_hit_rate: {
          label: "헤드샷 명중 확률",
          value: (bucket) => bucket.headshot_hit_rate,
          format: percent,
          percentage: true,
          basis: "머리 명중 횟수 ÷ 전체 명중 횟수; 빗나간 탄은 분모에서 제외",
        },
        headshot_kill_rate: {
          label: "헤드샷 킬 비율",
          value: (bucket) => bucket.headshot_kill_rate,
          format: percent,
          percentage: true,
          basis: "헤드샷 킬 수 ÷ 전체 킬 수",
        },
        kda: { label: "KDA", value: (bucket) => bucket.kda, format: (value) => formatNumber(value, 2) },
        avg_kills: { label: "경기당 평균 킬", value: (bucket) => bucket.avg_kills, format: (value) => formatNumber(value, 2) },
        avg_assists: { label: "경기당 평균 어시스트", value: (bucket) => bucket.avg_assists, format: (value) => formatNumber(value, 2) },
        avg_deaths: { label: "경기당 평균 사망", value: (bucket) => bucket.avg_deaths, format: (value) => formatNumber(value, 2) },
        avg_dbnos_caused: { label: "경기당 평균 기절", value: (bucket) => bucket.avg_dbnos_caused, format: (value) => formatNumber(value, 2) },
        avg_dbnos_taken: { label: "경기당 당한 기절", value: (bucket) => bucket.avg_dbnos_taken, format: (value) => formatNumber(value, 2) },
        avg_fights_per_match: { label: "경기당 평균 교전", value: (bucket) => bucket.avg_fights_per_match, format: (value) => `${formatNumber(value, 2)}회` },
        avg_damage_dealt: { label: "평균 준 피해", value: (bucket) => bucket.avg_damage_dealt, format: (value) => formatNumber(value, 1) },
        avg_damage_taken: { label: "평균 받은 피해", value: (bucket) => bucket.avg_damage_taken, format: (value) => formatNumber(value, 1) },
        avg_survival_seconds: { label: "평균 생존시간", value: (bucket) => bucket.avg_survival_seconds, format: minutes },
        avg_movement_distance_m: { label: "평균 이동거리", value: (bucket) => bucket.avg_movement_distance_m, format: distanceKm },
        hit_head: { label: "머리 명중 비율", value: (bucket) => bucket.hit_part_rates?.head || 0, format: percent, percentage: true },
        hit_neck: { label: "목 명중 비율", value: (bucket) => bucket.hit_part_rates?.neck || 0, format: percent, percentage: true },
        hit_torso: { label: "몸통 명중 비율", value: (bucket) => bucket.hit_part_rates?.torso || 0, format: percent, percentage: true },
        hit_pelvis: { label: "골반 명중 비율", value: (bucket) => bucket.hit_part_rates?.pelvis || 0, format: percent, percentage: true },
        hit_arm: { label: "팔 명중 비율", value: (bucket) => bucket.hit_part_rates?.arm || 0, format: percent, percentage: true },
        hit_leg: { label: "다리 명중 비율", value: (bucket) => bucket.hit_part_rates?.leg || 0, format: percent, percentage: true },
        taken_head: { label: "머리 피격 비율", value: (bucket) => bucket.taken_hit_part_rates?.head || 0, format: percent, percentage: true },
        taken_neck: { label: "목 피격 비율", value: (bucket) => bucket.taken_hit_part_rates?.neck || 0, format: percent, percentage: true },
        taken_torso: { label: "몸통 피격 비율", value: (bucket) => bucket.taken_hit_part_rates?.torso || 0, format: percent, percentage: true },
        taken_pelvis: { label: "골반 피격 비율", value: (bucket) => bucket.taken_hit_part_rates?.pelvis || 0, format: percent, percentage: true },
        taken_arm: { label: "팔 피격 비율", value: (bucket) => bucket.taken_hit_part_rates?.arm || 0, format: percent, percentage: true },
        taken_leg: { label: "다리 피격 비율", value: (bucket) => bucket.taken_hit_part_rates?.leg || 0, format: percent, percentage: true },
      };
      return definitions[metric] || definitions.win_rate;
    }

    function isTemporalTrendGranularity(granularity) {
      return ["date", "week", "month", "quarter", "year"].includes(String(granularity || ""));
    }

    function trendDeltaText(definition, current, previous) {
      if (!Number.isFinite(previous)) return "비교 구간 없음";
      const difference = current - previous;
      const sign = difference > 0 ? "+" : "";
      if (definition.percentage) return sign + formatNumber(difference * 100, 1) + "%p";
      return sign + definition.format(difference);
    }

    function trendAxisText(definition, value) {
      if (definition.percentage) {
        const percentage = value * 100;
        return percentage < 10 && percentage > 0
          ? formatNumber(percentage, 1) + "%"
          : formatNumber(percentage, 0) + "%";
      }
      return definition.format(value);
    }

    function renderTrendComparisonChart(definition, buckets) {
      const values = buckets.map((bucket) => Math.max(0, Number(definition.value(bucket) || 0)));
      const maximum = definition.percentage ? 1 : Math.max(1, ...values);
      const rows = buckets.map((bucket, index) => {
        const value = values[index];
        const width = Math.max(0, Math.min(100, value / maximum * 100));
        return `<div class="metric-chart-row">
          <strong class="metric-chart-label">${escapeHtml(bucket.period_label)}<small>${formatInteger(bucket.match_count)}경기</small></strong>
          <span class="metric-chart-track"><span class="metric-chart-fill" style="width:${width.toFixed(2)}%"></span></span>
          <span class="metric-chart-value">${escapeHtml(definition.format(value))}</span>
        </div>`;
      }).join("");
      return `
        <h3>${escapeHtml(definition.label)} 구간 비교</h3>
        <div class="metric-chart-list">${rows || '<span class="result-caption">표시할 구간이 없습니다.</span>'}</div>
        <p class="trend-chart-note">시간 순서가 없는 맵·모드·시점 등의 집계는 항목 비교 막대로 표시합니다.${definition.basis ? " 산식: " + escapeHtml(definition.basis) + "." : ""}</p>`;
    }

    function renderTimeSeriesLineChart(definition, buckets, options = {}) {
      if (!buckets.length) return '<span class="result-caption">표시할 시간 구간이 없습니다.</span>';
      const values = buckets.map((bucket) => Math.max(0, Number(definition.value(bucket) || 0)));
      const highest = Math.max(0, ...values);
      const maximum = definition.percentage
        ? Math.min(1, Math.max(0.05, highest * 1.15))
        : Math.max(1, highest * 1.12);
      const width = 920;
      const height = 330;
      const plot = { left: 68, right: 24, top: 28, bottom: 58 };
      const plotWidth = width - plot.left - plot.right;
      const plotHeight = height - plot.top - plot.bottom;
      const x = (index) => (
        buckets.length === 1
          ? plot.left + plotWidth / 2
          : plot.left + index / (buckets.length - 1) * plotWidth
      );
      const y = (value) => plot.top + (1 - value / maximum) * plotHeight;
      const points = values.map((value, index) => [x(index), y(value)]);
      const path = points.map((point, index) => (
        (index ? "L" : "M") + point[0].toFixed(2) + " " + point[1].toFixed(2)
      )).join(" ");
      const gridLines = Array.from({ length: 5 }, (_, index) => {
        const ratio = index / 4;
        const gridY = plot.top + ratio * plotHeight;
        const gridValue = maximum * (1 - ratio);
        return `
          <line class="trend-line-grid" x1="${plot.left}" y1="${gridY.toFixed(2)}" x2="${width - plot.right}" y2="${gridY.toFixed(2)}"></line>
          <text x="${plot.left - 10}" y="${(gridY + 4).toFixed(2)}" text-anchor="end">${escapeHtml(trendAxisText(definition, gridValue))}</text>`;
      }).join("");
      const labelCount = Math.min(7, buckets.length);
      const labelIndexes = new Set(Array.from({ length: labelCount }, (_, index) => (
        labelCount === 1 ? 0 : Math.round(index * (buckets.length - 1) / (labelCount - 1))
      )));
      const xLabels = [...labelIndexes].map((index) => `
        <text x="${x(index).toFixed(2)}" y="${height - 24}" text-anchor="middle">${escapeHtml(buckets[index].period_label)}</text>
      `).join("");
      const circles = points.map((point, index) => {
        const bucket = buckets[index];
        const radius = Math.min(6, 3 + Math.sqrt(Math.max(1, Number(bucket.match_count || 0))) / 5);
        return `<circle
          class="trend-line-point${index === points.length - 1 ? ' latest' : ''}"
          cx="${point[0].toFixed(2)}"
          cy="${point[1].toFixed(2)}"
          r="${radius.toFixed(2)}"
        ><title>${escapeHtml(bucket.period_label)} · ${escapeHtml(definition.format(values[index]))} · ${formatInteger(bucket.match_count)}경기</title></circle>`;
      }).join("");
      const latestIndex = values.length - 1;
      const latest = values[latestIndex];
      const previous = latestIndex > 0 ? values[latestIndex - 1] : Number.NaN;
      const totals = options.totals || {};
      const overall = Math.max(0, Number(definition.value(totals) || 0));
      const availablePointCount = Number(options.availablePointCount || buckets.length);
      const note = options.truncated
        ? `전체 ${formatInteger(availablePointCount)}개 중 최근 ${formatInteger(buckets.length)}개 구간을 표시합니다.`
        : `전체 ${formatInteger(buckets.length)}개 구간을 표시합니다.`;
      return `
        <h3>${escapeHtml(options.title || definition.label + " KST 시계열")}</h3>
        <div class="trend-chart-overview">
          <div class="trend-chart-stat">
            <span>최근 · ${escapeHtml(buckets[latestIndex].period_label)}</span>
            <strong>${escapeHtml(definition.format(latest))}</strong>
            <small>${formatInteger(buckets[latestIndex].match_count)}경기 표본</small>
          </div>
          <div class="trend-chart-stat">
            <span>직전 구간 대비 증감</span>
            <strong>${escapeHtml(trendDeltaText(definition, latest, previous))}</strong>
            <small>증감은 향상·악화 판정이 아닌 값의 차이입니다.</small>
          </div>
          <div class="trend-chart-stat">
            <span>현재 조회 조건 전체</span>
            <strong>${escapeHtml(definition.format(overall))}</strong>
            <small>${formatInteger(totals.match_count)}경기 집계</small>
          </div>
        </div>
        <div class="trend-line-frame">
          <svg class="trend-line-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${attr(definition.label)} 시간 변화 선 그래프">
            ${gridLines}
            <line class="trend-line-axis" x1="${plot.left}" y1="${height - plot.bottom}" x2="${width - plot.right}" y2="${height - plot.bottom}"></line>
            <path class="trend-line-path" d="${attr(path)}"></path>
            ${circles}
            ${xLabels}
          </svg>
        </div>
        <p class="trend-chart-note">${escapeHtml(note)} 점 크기는 해당 구간의 경기 수를 반영하며, 표본이 적은 구간은 변동이 크게 보일 수 있습니다.${definition.basis ? " 산식: " + escapeHtml(definition.basis) + "." : ""}</p>`;
    }

    function renderTrendLineChart(definition, buckets) {
      return renderTimeSeriesLineChart(definition, buckets, {
        totals: activeTrendReport?.totals || {},
        availablePointCount: activeTrendReport?.available_bucket_count || buckets.length,
        truncated: Boolean(activeTrendReport?.truncated),
      });
    }

    function renderTrendChart() {
      if (!activeTrendReport) {
        trendChartPanel.innerHTML = '<span class="result-caption">조회된 추세가 없습니다.</span>';
        return;
      }
      const definition = trendMetricDefinition(trendChartMetric.value);
      const buckets = activeTrendReport.buckets || [];
      trendChartPanel.innerHTML = isTemporalTrendGranularity(activeTrendReport.granularity)
        ? renderTrendLineChart(definition, buckets)
        : renderTrendComparisonChart(definition, buckets);
    }

    function renderTrendView() {
      const chart = activeTrendView === "chart";
      trendTableWrap.hidden = chart;
      trendCards.hidden = chart;
      trendChartPanel.hidden = !chart;
      for (const button of trendViewControls.querySelectorAll("[data-trend-view]")) {
        button.classList.toggle("active", button.dataset.trendView === activeTrendView);
      }
      for (const button of trendViewControls.querySelectorAll("[data-trend-granularity]")) {
        button.classList.toggle(
          "active",
          button.dataset.trendGranularity === String(activeTrendReport?.granularity || "")
        );
      }
      if (chart) renderTrendChart();
    }

    function appendAnalysisFilters(form, params, names = [
      "game_mode",
      "team_mode",
      "perspective",
      "match_type",
      "map_name",
      "season_state",
      "from_date_kst",
      "to_date_kst",
    ]) {
      for (const name of names) {
        const value = String(form.get(name) || "").trim();
        if (value) params.set(name, value);
      }
    }

    async function fetchAnalysisJson(url) {
      const response = await fetch(url);
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || response.statusText);
      }
      return response.json();
    }

    async function loadTimeInsights(formElement) {
      const form = new FormData(formElement);
      const player = selectedRegisteredPlayer(formElement);
      const params = new URLSearchParams({
        shard: player.shard,
        account_id: player.account_id,
        granularity: "hour",
        bucket_limit: "24",
      });
      appendAnalysisFilters(form, params);
      const payload = await fetchAnalysisJson(`/players/trends?${params.toString()}`);
      activeTimeInsightReport = payload.trends;
      renderTimeInsights(String(form.get("metric") || "match_count"));
    }

    function timeMetricDefinition(metric) {
      const definitions = {
        match_count: { label: "플레이 경기", value: (item) => item.match_count, format: (value) => `${formatInteger(value)}전`, basis: "해당 KST 시간에 시작한 완료 경기" },
        wins: { label: "치킨 횟수", value: (item) => item.wins, format: (value) => `${formatInteger(value)}회`, basis: "해당 KST 시간에 시작한 치킨 경기" },
        win_rate: { label: "치킨 승률", value: (item) => item.win_rate, format: percent, percentage: true, basis: "치킨 경기 ÷ 완료 경기" },
        fight_win_rate: { label: "교전 승률", value: (item) => item.fight_win_rate, format: percent, percentage: true, basis: "교전 승리 ÷ 승리·패배 교전" },
        accuracy: { label: "명중률", value: (item) => item.accuracy, format: percent, percentage: true, basis: "일반 탄환 명중 이벤트 ÷ 발사 이벤트" },
        headshot_hit_rate: { label: "헤드샷 명중률", value: (item) => item.headshot_hit_rate, format: percent, percentage: true, basis: "머리 명중 ÷ 전체 명중; 빗나간 탄 제외" },
        avg_damage_dealt: { label: "평균 피해", value: (item) => item.avg_damage_dealt, format: (value) => formatNumber(value, 1), basis: "준 피해 합계 ÷ 완료 경기" },
      };
      return definitions[metric] || definitions.match_count;
    }

    function completeHourBuckets(report) {
      const byHour = new Map((report?.buckets || []).map((bucket) => [Number(bucket.period_key), bucket]));
      return Array.from({ length: 24 }, (_, hour) => byHour.get(hour) || {
        period_key: String(hour).padStart(2, "0"),
        period_label: `${String(hour).padStart(2, "0")}시`,
        match_count: 0,
        wins: 0,
        win_rate: 0,
        fight_count: 0,
        fight_wins: 0,
        fight_win_rate: 0,
        shots_fired: 0,
        shots_hit: 0,
        accuracy: 0,
        headshot_hits: 0,
        headshot_hit_rate: 0,
        avg_damage_dealt: 0,
      });
    }

    function bestTimeBucket(buckets, score, eligible = () => true) {
      return buckets.filter(eligible).sort((left, right) => score(right) - score(left) || right.match_count - left.match_count)[0] || null;
    }

    function renderTimeInsights(metric) {
      if (!activeTimeInsightReport) return;
      const buckets = completeHourBuckets(activeTimeInsightReport);
      const definition = timeMetricDefinition(metric);
      const values = buckets.map((bucket) => Math.max(0, Number(definition.value(bucket) || 0)));
      const maximum = definition.percentage ? 1 : Math.max(1, ...values);
      const busiest = bestTimeBucket(buckets, (item) => item.match_count);
      const mostWins = bestTimeBucket(buckets, (item) => item.wins);
      const bestWinRate = bestTimeBucket(buckets, (item) => item.win_rate, (item) => item.match_count >= 5);
      const bestFight = bestTimeBucket(buckets, (item) => item.fight_win_rate, (item) => item.fight_count >= 10);
      const cells = buckets.map((bucket, index) => {
        const value = values[index];
        const ratio = Math.max(0, Math.min(1, value / maximum));
        const detail = definition.percentage
          ? `${definition.format(value)} · ${formatInteger(bucket.match_count)}전`
          : definition.format(value);
        return `<div class="time-hour-cell" title="${attr(`${bucket.period_label} · ${detail}`)}">
          <i style="height:${(6 + ratio * 42).toFixed(1)}px;opacity:${(0.2 + ratio * 0.8).toFixed(2)}"></i>
          <strong>${escapeHtml(bucket.period_label)}</strong>
          <span>${escapeHtml(detail)}</span>
        </div>`;
      }).join("");
      const tableRows = buckets.filter((bucket) => bucket.match_count > 0).map((bucket) => `
        <tr><td>${escapeHtml(bucket.period_label)}</td><td>${formatInteger(bucket.match_count)}</td><td>${formatInteger(bucket.wins)} · ${percent(bucket.win_rate)}</td><td>${formatNumber(bucket.avg_damage_dealt, 1)}</td><td>${percent(bucket.accuracy)} · ${formatInteger(bucket.shots_hit)}/${formatInteger(bucket.shots_fired)}</td><td>${percent(bucket.headshot_hit_rate)} · ${formatInteger(bucket.headshot_hits)}/${formatInteger(characterHitCount(bucket))}</td><td>${percent(bucket.fight_win_rate)} · ${formatInteger(bucket.fight_wins)}/${formatInteger(bucket.fight_count)}</td></tr>
      `).join("") || '<tr><td colspan="7">조건에 맞는 완료 경기가 없습니다.</td></tr>';
      timeInsightBody.innerHTML = `<div class="result-shell">
        ${resultHeading(activeTimeInsightReport.player.current_name, `KST · ${definition.label}`, `${formatInteger(activeTimeInsightReport.totals.match_count)}경기`)}
        <div class="time-insight-grid">
          <div class="time-insight-kpi"><span>주 플레이 시간</span><strong>${escapeHtml(busiest?.period_label || "-")}</strong><small>${formatInteger(busiest?.match_count)}경기</small></div>
          <div class="time-insight-kpi"><span>치킨 최다 시간</span><strong>${escapeHtml(mostWins?.period_label || "-")}</strong><small>${formatInteger(mostWins?.wins)}치킨 · ${percent(mostWins?.win_rate)}</small></div>
          <div class="time-insight-kpi"><span>승률 우수 시간</span><strong>${escapeHtml(bestWinRate?.period_label || "표본 부족")}</strong><small>최소 5경기 · ${bestWinRate ? percent(bestWinRate.win_rate) : "-"}</small></div>
          <div class="time-insight-kpi"><span>교전 우수 시간</span><strong>${escapeHtml(bestFight?.period_label || "표본 부족")}</strong><small>최소 10교전 · ${bestFight ? percent(bestFight.fight_win_rate) : "-"}</small></div>
        </div>
        <div class="metric-chart"><h3>24시간 ${escapeHtml(definition.label)} 분포</h3><div class="time-hour-grid">${cells}</div><p class="trend-chart-note">산식: ${escapeHtml(definition.basis)}. 시간은 경기 시작 시각 KST 기준이며 표본 수를 함께 표시합니다.</p></div>
        <details class="result-disclosure"><summary>시간대별 상세 · 플레이 기록 ${formatInteger(buckets.filter((item) => item.match_count > 0).length)}개 시간대</summary><div class="table-scroll"><table><thead><tr><th>KST</th><th>경기</th><th>치킨·승률</th><th>평균 피해</th><th>명중</th><th>헤드샷 명중</th><th>교전 승률</th></tr></thead><tbody>${tableRows}</tbody></table></div></details>
      </div>`;
    }

    function renderComparisonPicker(catalog = catalogByForm.get(comparisonForm)) {
      const type = String(comparisonForm.elements.comparison_type.value || "player");
      const shard = String(comparisonForm.elements.shard.value || activeAnalysisPlayer?.shard || "steam");
      const targetField = comparisonForm.querySelector("[data-comparison-player-field]");
      const targetInput = comparisonForm.elements.target;
      if (targetField) targetField.hidden = type === "player";
      if (targetInput) targetInput.required = type !== "player";
      const comparedField = { map: "map_name", game_mode: "game_mode", team_mode: "team_mode", perspective: "perspective", match_type: "match_type" }[type] || "";
      for (const fieldName of ["map_name", "game_mode", "team_mode", "perspective", "match_type"]) {
        const control = comparisonForm.elements[fieldName];
        if (!control) continue;
        const compared = fieldName === comparedField;
        control.disabled = compared;
        if (compared) control.value = "";
        control.closest("label")?.classList.toggle("is-disabled", compared);
      }
      const selected = new Set([...comparisonItemPicker.querySelectorAll("input:checked")].map((input) => input.value));
      let items = [];
      if (type === "player") {
        items = registeredPlayers.filter((player) => player.shard === shard).map((player) => ({
          value: player.account_id,
          label: player.current_name,
          note: player.active ? "수집 중" : "수집 중지",
        }));
        if (!selected.size && activeAnalysisPlayer?.shard === shard) selected.add(activeAnalysisPlayer.account_id);
      } else if (type === "weapon") {
        items = (catalog?.weapons || []).map((weapon) => ({
          value: weapon.weapon_code,
          label: weapon.weapon_name,
          note: `${weapon.weapon_family || "무기"} · ${formatInteger(weapon.match_count)}경기`,
        }));
      } else {
        const facetByType = {
          map: "maps",
          game_mode: "game_modes",
          team_mode: "team_modes",
          perspective: "perspectives",
          match_type: "match_types",
        };
        const facet = facetByType[type] || "maps";
        const typeLabels = {
          map: "맵",
          game_mode: "게임 모드",
          team_mode: "팀 모드",
          perspective: "시점",
          match_type: "매치 유형",
        };
        items = (catalog?.facets?.[facet] || []).map((value) => ({
          value,
          label: catalogOptionLabel(facet, value, catalog),
          note: typeLabels[type] || "비교 항목",
        }));
      }
      comparisonItemPicker.innerHTML = items.map((item) => `
        <label class="comparison-item"><input type="checkbox" name="comparison_item" value="${attr(item.value)}" data-item-label="${attr(item.label)}" ${selected.has(item.value) ? "checked" : ""}><span>${escapeHtml(item.label)}<small>${escapeHtml(item.note)}</small></span></label>
      `).join("") || `<span class="status">${type === "player" ? "이 플랫폼에 등록된 유저가 없습니다." : "기준 유저의 사용 기록이 없습니다."}</span>`;
      updateComparisonSelectionCount();
    }

    function updateComparisonSelectionCount() {
      const checked = [...comparisonItemPicker.querySelectorAll('input[name="comparison_item"]:checked')];
      comparisonSelectionCount.textContent = `${formatInteger(checked.length)}/${formatInteger(5)}`;
      for (const input of comparisonItemPicker.querySelectorAll('input[name="comparison_item"]:not(:checked)')) {
        input.disabled = checked.length >= 5;
      }
    }

    function comparisonMetricDefinition(metric) {
      const definitions = {
        win_rate: { label: "승률", value: (row) => row.metrics.win_rate, format: percent, percentage: true, basis: "치킨 경기 ÷ 완료 경기" },
        kda: { label: "KDA", value: (row) => row.metrics.kda, format: (value) => formatNumber(value, 2), basis: "(킬 + 어시스트) ÷ 사망" },
        avg_kills: { label: "경기당 킬", value: (row) => row.metrics.avg_kills, format: (value) => formatNumber(value, 2), basis: "킬 ÷ 완료 경기" },
        avg_assists: { label: "경기당 어시스트", value: (row) => row.metrics.avg_assists, format: (value) => formatNumber(value, 2), basis: "어시스트 ÷ 완료 경기" },
        avg_dbnos_caused: { label: "경기당 기절", value: (row) => row.metrics.avg_dbnos_caused, format: (value) => formatNumber(value, 2), basis: "가한 기절 ÷ 완료 경기" },
        avg_dbnos_taken: { label: "경기당 당한 기절", value: (row) => row.metrics.avg_dbnos_taken, format: (value) => formatNumber(value, 2), basis: "당한 기절 ÷ 완료 경기" },
        avg_deaths: { label: "경기당 사망", value: (row) => row.metrics.avg_deaths, format: (value) => formatNumber(value, 2), basis: "사망 ÷ 완료 경기" },
        avg_damage_dealt: { label: "평균 피해", value: (row) => row.metrics.avg_damage_dealt, format: (value) => formatNumber(value, 1), basis: "준 피해 ÷ 완료 경기" },
        avg_damage_taken: { label: "평균 받은 피해", value: (row) => row.metrics.avg_damage_taken, format: (value) => formatNumber(value, 1), basis: "받은 피해 ÷ 완료 경기" },
        damage_ratio: { label: "가한/받은 피해 비율", value: (row) => row.metrics.damage_ratio, format: (value) => formatNumber(value, 2), basis: "준 피해 ÷ 받은 피해" },
        accuracy: { label: "명중률", value: (row) => row.metrics.accuracy, format: percent, percentage: true, basis: "일반 탄환 명중 이벤트 ÷ 발사 이벤트" },
        headshot_hit_rate: { label: "헤드샷 명중률", value: (row) => row.metrics.headshot_hit_rate, format: percent, percentage: true, basis: "머리 명중 ÷ 전체 명중; 빗나간 탄 제외" },
        headshot_kill_rate: { label: "헤드샷 킬 비율", value: (row) => row.metrics.headshot_kill_rate, format: percent, percentage: true, basis: "헤드샷 킬 ÷ 전체 킬" },
        fight_win_rate: { label: "교전 승률", value: (row) => row.metrics.fight_win_rate, format: percent, percentage: true, basis: "교전 승리 ÷ 승리·패배 교전" },
        avg_fights_per_match: { label: "경기당 교전", value: (row) => row.metrics.avg_fights_per_match, format: (value) => formatNumber(value, 2), basis: "교전 수 ÷ 완료 경기" },
        avg_survival_seconds: { label: "평균 생존", value: (row) => row.metrics.avg_survival_seconds, format: minutes, basis: "생존 시간 합계 ÷ 완료 경기" },
        avg_movement_distance_m: { label: "평균 이동", value: (row) => row.metrics.avg_movement_distance_m, format: distanceKm, basis: "도보·차량·수영 이동 거리 합계 ÷ 완료 경기" },
      };
      return definitions[metric] || definitions.win_rate;
    }

    function normalizeComparisonMetrics(metrics, type) {
      const matchCount = Number(metrics.match_count || 0);
      const deaths = Number(metrics.deaths ?? metrics.deaths_taken ?? 0);
      const kills = Number(metrics.kills || 0);
      const assists = Number(metrics.assists || 0);
      const damageDealt = Number(metrics.damage_dealt || 0);
      const damageTaken = Number(metrics.damage_taken || 0);
      return {
        ...metrics,
        deaths,
        kda: Number(metrics.kda ?? ((kills + assists) / Math.max(1, deaths))),
        avg_kills: Number(metrics.avg_kills ?? (kills / Math.max(1, matchCount))),
        avg_assists: Number(metrics.avg_assists ?? (assists / Math.max(1, matchCount))),
        avg_deaths: Number(metrics.avg_deaths ?? (deaths / Math.max(1, matchCount))),
        avg_dbnos_caused: Number(metrics.avg_dbnos_caused ?? metrics.avg_dbnos ?? 0),
        avg_dbnos_taken: Number(metrics.avg_dbnos_taken ?? 0),
        avg_damage_dealt: Number(metrics.avg_damage_dealt ?? (damageDealt / Math.max(1, matchCount))),
        avg_damage_taken: Number(metrics.avg_damage_taken ?? (damageTaken / Math.max(1, matchCount))),
        damage_ratio: damageDealt / Math.max(1, damageTaken),
        headshot_kill_rate: Number(metrics.headshot_kill_rate ?? (Number(metrics.headshot_kills || 0) / Math.max(1, kills))),
        avg_survival_seconds: type === "weapon" ? null : metrics.avg_survival_seconds,
        avg_movement_distance_m: type === "weapon" ? null : metrics.avg_movement_distance_m,
      };
    }

    async function loadComparison(formElement) {
      const form = new FormData(formElement);
      const type = String(form.get("comparison_type") || "player");
      const selected = [...comparisonItemPicker.querySelectorAll('input[name="comparison_item"]:checked')].map((input) => ({
        value: input.value,
        label: input.dataset.itemLabel || input.value,
      }));
      if (selected.length < 2) throw new Error("비교 대상을 2개 이상 선택하세요.");
      if (selected.length > 5) throw new Error("한 번에 최대 5개까지 비교할 수 있습니다.");
      const basePlayer = type === "player" ? null : selectedRegisteredPlayer(formElement);
      const granularity = String(form.get("trend_granularity") || "month");
      const dimensionParam = {
        map: "map_name",
        game_mode: "game_mode",
        team_mode: "team_mode",
        perspective: "perspective",
        match_type: "match_type",
      }[type];
      const commonFilterFields = ["map_name", "game_mode", "team_mode", "perspective", "match_type", "season_state", "from_date_kst", "to_date_kst"]
        .filter((field) => field !== dimensionParam);
      const requests = selected.map(async (item) => {
        const params = new URLSearchParams();
        if (type === "player") {
          const player = registeredPlayers.find((candidate) => candidate.account_id === item.value);
          if (!player) throw new Error(`${item.label} 유저 정보를 찾을 수 없습니다.`);
          params.set("shard", player.shard);
          params.set("account_id", player.account_id);
          params.set("granularity", granularity);
          params.set("bucket_limit", "500");
          appendAnalysisFilters(form, params, commonFilterFields);
          const report = (await fetchAnalysisJson(`/players/trends?${params.toString()}`)).trends;
          return { key: item.value, label: player.current_name, type, metrics: normalizeComparisonMetrics(report.totals, type), trend: report.buckets || [] };
        }
        params.set("shard", basePlayer.shard);
        params.set("account_id", basePlayer.account_id);
        appendAnalysisFilters(form, params, commonFilterFields);
        if (type !== "weapon") {
          params.set("granularity", granularity);
          params.set("bucket_limit", "500");
          params.set(dimensionParam || "map_name", item.value);
          const report = (await fetchAnalysisJson(`/players/trends?${params.toString()}`)).trends;
          return { key: item.value, label: item.label, type, metrics: normalizeComparisonMetrics(report.totals, type), trend: report.buckets || [] };
        }
        params.set("weapon", item.value);
        const detail = (await fetchAnalysisJson(`/players/weapon?${params.toString()}`)).weapon;
        return {
          key: item.value,
          label: detail.weapon_name || item.label,
          type,
          metrics: normalizeComparisonMetrics(detail.totals, type),
          trend: detail.trend_series?.[granularity]?.points || [],
        };
      });
      activeComparisonRows = await Promise.all(requests);
      renderComparisonResult();
    }

    function comparisonSampleNote(row) {
      const metrics = row.metrics || {};
      return `${formatInteger(metrics.match_count)}경기 · ${formatInteger(metrics.shots_hit)}명중 · ${formatInteger(metrics.fight_count)}교전`;
    }

    function renderComparisonTrendChart(definition) {
      const colors = ["#45d6b0", "#67b7dc", "#f0c75e", "#ef7f6d", "#b49ddd"];
      const periodLabels = new Map();
      for (const row of activeComparisonRows) {
        for (const point of row.trend || []) {
          const key = String(point.period_key || point.period_label || "");
          if (key) periodLabels.set(key, String(point.period_label || key));
        }
      }
      const periodKeys = [...periodLabels.keys()].sort((left, right) => left.localeCompare(right));
      if (!periodKeys.length) return '<span class="result-caption">선택 조건에 표시할 시간 구간이 없습니다.</span>';
      const normalizedSeries = activeComparisonRows.map((row) => {
        const points = new Map((row.trend || []).map((point) => {
          const key = String(point.period_key || point.period_label || "");
          const metrics = normalizeComparisonMetrics(point, row.type);
          return [key, Math.max(0, Number(definition.value({ ...row, metrics }) || 0))];
        }));
        return { row, points };
      });
      const allValues = normalizedSeries.flatMap((series) => [...series.points.values()]);
      const highest = Math.max(0, ...allValues);
      const maximum = definition.percentage ? Math.min(1, Math.max(0.05, highest * 1.15)) : Math.max(1, highest * 1.12);
      const width = 920;
      const height = 360;
      const plot = { left: 68, right: 24, top: 28, bottom: 66 };
      const plotWidth = width - plot.left - plot.right;
      const plotHeight = height - plot.top - plot.bottom;
      const x = (index) => periodKeys.length === 1 ? plot.left + plotWidth / 2 : plot.left + index / (periodKeys.length - 1) * plotWidth;
      const y = (value) => plot.top + (1 - value / maximum) * plotHeight;
      const gridLines = Array.from({ length: 5 }, (_, index) => {
        const ratio = index / 4;
        const gridY = plot.top + ratio * plotHeight;
        return `<line class="trend-line-grid" x1="${plot.left}" y1="${gridY.toFixed(2)}" x2="${width - plot.right}" y2="${gridY.toFixed(2)}"></line><text x="${plot.left - 10}" y="${(gridY + 4).toFixed(2)}" text-anchor="end">${escapeHtml(trendAxisText(definition, maximum * (1 - ratio)))}</text>`;
      }).join("");
      const labelCount = Math.min(7, periodKeys.length);
      const labelIndexes = new Set(Array.from({ length: labelCount }, (_, index) => labelCount === 1 ? 0 : Math.round(index * (periodKeys.length - 1) / (labelCount - 1))));
      const xLabels = [...labelIndexes].map((index) => `<text x="${x(index).toFixed(2)}" y="${height - 25}" text-anchor="middle">${escapeHtml(periodLabels.get(periodKeys[index]) || periodKeys[index])}</text>`).join("");
      const seriesSvg = normalizedSeries.map((series, seriesIndex) => {
        const color = colors[seriesIndex % colors.length];
        const points = periodKeys.map((key, index) => series.points.has(key) ? { key, index, value: series.points.get(key) } : null).filter(Boolean);
        const path = points.map((point, index) => `${index ? "L" : "M"}${x(point.index).toFixed(2)} ${y(point.value).toFixed(2)}`).join(" ");
        const circles = points.map((point) => `<circle cx="${x(point.index).toFixed(2)}" cy="${y(point.value).toFixed(2)}" r="4" fill="${color}" stroke="#08100d" stroke-width="2"><title>${escapeHtml(series.row.label)} · ${escapeHtml(periodLabels.get(point.key) || point.key)} · ${escapeHtml(definition.format(point.value))}</title></circle>`).join("");
        return `<path d="${attr(path)}" fill="none" stroke="${color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"></path>${circles}`;
      }).join("");
      const legend = normalizedSeries.map((series, index) => `<span class="result-chip"><i style="width:9px;height:9px;background:${colors[index % colors.length]};display:inline-block;margin-right:5px"></i>${escapeHtml(series.row.label)}</span>`).join("");
      const granularityLabel = { date: "일자별", week: "주별", month: "월별" }[comparisonForm.elements.trend_granularity.value] || "기간별";
      return `<div class="metric-chart"><h3>${escapeHtml(definition.label)} · ${granularityLabel} 변화</h3><div class="result-chip-list">${legend}</div><div class="trend-line-frame"><svg class="trend-line-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${attr(definition.label)} 대상별 시간 변화 선 그래프">${gridLines}<line class="trend-line-axis" x1="${plot.left}" y1="${height - plot.bottom}" x2="${width - plot.right}" y2="${height - plot.bottom}"></line>${seriesSvg}${xLabels}</svg></div><p class="trend-chart-note">같은 KST 구간과 조회 조건으로 비교합니다. 선 위의 점에 마우스를 올리면 대상·구간·값을 확인할 수 있습니다. 산식: ${escapeHtml(definition.basis)}.</p></div>`;
    }

    function renderComparisonResult() {
      if (!activeComparisonRows.length) {
        comparisonBody.textContent = "비교 대기 중";
        return;
      }
      const metric = String(comparisonForm.elements.metric.value || "win_rate");
      const definition = comparisonMetricDefinition(metric);
      const values = activeComparisonRows.map((row) => Math.max(0, Number(definition.value(row) || 0)));
      const maximum = definition.percentage ? 1 : Math.max(1, ...values);
      const colors = ["#45d6b0", "#67b7dc", "#f0c75e", "#ef7f6d", "#b49ddd"];
      const bars = activeComparisonRows.map((row, index) => {
        const value = values[index];
        const width = Math.max(0, Math.min(100, value / maximum * 100));
        return `<div class="comparison-bar-row"><strong class="comparison-bar-label">${escapeHtml(row.label)}<small>${escapeHtml(comparisonSampleNote(row))}</small></strong><span class="comparison-bar-track"><span class="comparison-bar-fill" style="width:${width.toFixed(2)}%;background:${colors[index % colors.length]}"></span></span><span class="comparison-bar-value">${escapeHtml(definition.format(value))}</span></div>`;
      }).join("");
      const tableRows = activeComparisonRows.map((row) => {
        const item = row.metrics || {};
        return `<tr><td><strong>${escapeHtml(row.label)}</strong></td><td>${formatInteger(item.match_count)}</td><td>${formatInteger(item.wins)} · ${percent(item.win_rate)}</td><td>${formatNumber(item.kda, 2)}</td><td>${formatNumber(item.avg_kills, 2)} / ${formatNumber(item.avg_assists, 2)} / ${formatNumber(item.avg_dbnos_caused, 2)} / ${formatNumber(item.avg_deaths, 2)}</td><td>${formatNumber(item.avg_damage_dealt, 1)} / ${formatNumber(item.avg_damage_taken, 1)}<br><span class="result-caption">교환 ${formatNumber(item.damage_ratio, 2)}</span></td><td>${percent(item.accuracy)}<br><span class="result-caption">${formatInteger(item.shots_hit)}/${formatInteger(item.shots_fired)}</span></td><td>${percent(item.headshot_hit_rate)} / ${percent(item.headshot_kill_rate)}<br><span class="result-caption">명중 / 킬</span></td><td>${percent(item.fight_win_rate)}<br><span class="result-caption">${formatInteger(item.fight_wins)}/${formatInteger(item.fight_count)}교전 · 경기당 ${formatNumber(item.avg_fights_per_match, 2)}</span></td><td>${item.avg_survival_seconds === null || item.avg_survival_seconds === undefined ? "-" : minutes(item.avg_survival_seconds)}<br><span class="result-caption">${item.avg_movement_distance_m === null || item.avg_movement_distance_m === undefined ? "-" : distanceKm(item.avg_movement_distance_m)}</span></td></tr>`;
      }).join("");
      const typeLabel = { player: "유저", weapon: "무기", map: "맵", game_mode: "게임 모드", team_mode: "팀 모드", perspective: "시점", match_type: "매치 유형" }[activeComparisonRows[0].type] || "상세";
      let result;
      if (activeComparisonView === "trend") {
        result = renderComparisonTrendChart(definition);
      } else if (activeComparisonView === "chart") {
        result = `<div class="metric-chart"><h3>${escapeHtml(definition.label)}</h3><div class="comparison-bars">${bars}</div><p class="trend-chart-note">산식: ${escapeHtml(definition.basis)}. 각 대상의 경기·명중·교전 표본을 함께 표시합니다.</p></div>`;
      } else {
        result = `<div class="table-scroll"><table><thead><tr><th>${typeLabel}</th><th>경기</th><th>치킨·승률</th><th>KDA</th><th>경기당 킬/어시/기절/사망</th><th>평균 준/받은 피해</th><th>명중</th><th>헤드샷 명중/킬</th><th>교전 승률</th><th>생존/이동</th></tr></thead><tbody>${tableRows}</tbody></table></div>`;
      }
      comparisonBody.innerHTML = `<div class="result-shell">${resultHeading(`${typeLabel} 비교`, definition.label, `${formatInteger(activeComparisonRows.length)}개 대상`)}${result}</div>`;
    }

    function setComparisonView(view) {
      activeComparisonView = ["table", "chart", "trend"].includes(view) ? view : "chart";
      for (const button of comparisonViewControls.querySelectorAll("[data-comparison-view]")) {
        button.classList.toggle("active", button.dataset.comparisonView === activeComparisonView);
      }
      if (activeComparisonRows.length) renderComparisonResult();
    }

    function weaponTrendMetricDefinition(metric, detail) {
      const accuracyMetric = detail?.totals?.accuracy_metric || {};
      const accuracyIsPercentage = Boolean(accuracyMetric.is_percentage);
      const accuracyLabel = accuracyMetric.metric_kind === "pellet_hits_per_shell"
        ? "셸당 펠릿 명중"
        : accuracyMetric.metric_kind === "hit_events_per_attack"
          ? "공격당 피격 이벤트"
          : "명중 확률";
      const definitions = {
        fight_win_rate: {
          label: "교전 승리 확률",
          value: (point) => point.fight_win_rate,
          format: percent,
          percentage: true,
          basis: "이 무기로 기록된 승리 결과(킬·가한 기절) ÷ 승리·패배 결과",
        },
        win_rate: {
          label: "사용 경기 승률",
          value: (point) => point.win_rate,
          format: percent,
          percentage: true,
          basis: "이 무기를 사용한 치킨 경기 수 ÷ 이 무기를 사용한 완료 경기 수",
        },
        accuracy: {
          label: accuracyLabel,
          value: (point) => point.accuracy_metric?.metric_value ?? point.accuracy ?? 0,
          format: accuracyIsPercentage
            ? percent
            : (value) => formatNumber(value, 2) + "회",
          percentage: accuracyIsPercentage,
          basis: accuracyIsPercentage
            ? "이 무기의 명중 이벤트 ÷ 발사 이벤트"
            : "산탄·다중 피격 무기는 일반 탄환 명중률과 분리한 이벤트 비율",
        },
        headshot_hit_rate: {
          label: "헤드샷 명중 확률",
          value: (point) => point.headshot_hit_rate,
          format: percent,
          percentage: true,
          basis: "이 무기의 머리 명중 횟수 ÷ 전체 명중 횟수; 빗나간 탄은 제외",
        },
        avg_damage_dealt: {
          label: "경기당 준 피해",
          value: (point) => point.avg_damage_dealt,
          format: (value) => formatNumber(value, 1),
        },
        avg_damage_taken: {
          label: "경기당 받은 피해",
          value: (point) => point.avg_damage_taken,
          format: (value) => formatNumber(value, 1),
        },
        avg_kills: {
          label: "경기당 킬",
          value: (point) => point.avg_kills,
          format: (value) => formatNumber(value, 2),
        },
        avg_dbnos: {
          label: "경기당 가한 기절",
          value: (point) => point.avg_dbnos,
          format: (value) => formatNumber(value, 2),
        },
        avg_deaths_taken: {
          label: "경기당 사망",
          value: (point) => point.avg_deaths_taken,
          format: (value) => formatNumber(value, 2),
        },
        match_count: {
          label: "사용 경기 수",
          value: (point) => point.match_count,
          format: (value) => formatInteger(value) + "경기",
        },
      };
      return definitions[metric] || definitions.fight_win_rate;
    }

    function renderWeaponTrendChart() {
      const panel = document.querySelector("#weaponTrendChart");
      if (!panel || !activeWeaponDetail) return;
      const series = activeWeaponDetail.trend_series?.[activeWeaponTrendGranularity];
      const points = series?.points || [];
      const definition = weaponTrendMetricDefinition(activeWeaponTrendMetric, activeWeaponDetail);
      for (const button of weaponBody.querySelectorAll("[data-weapon-trend-granularity]")) {
        button.classList.toggle(
          "active",
          button.dataset.weaponTrendGranularity === activeWeaponTrendGranularity
        );
      }
      const metricSelect = weaponBody.querySelector("[data-weapon-trend-metric]");
      if (metricSelect) metricSelect.value = activeWeaponTrendMetric;
      panel.innerHTML = renderTimeSeriesLineChart(definition, points, {
        title: activeWeaponDetail.weapon_name + " · " + definition.label,
        totals: activeWeaponDetail.totals || {},
        availablePointCount: series?.available_point_count || points.length,
        truncated: Boolean(series?.truncated),
      });
    }

    async function loadPlayerWeapon(formElement) {
      const form = new FormData(formElement);
      const player = selectedRegisteredPlayer(formElement);
      const params = new URLSearchParams({
        shard: player.shard,
        account_id: player.account_id,
        weapon: String(form.get("weapon") || ""),
      });
      for (const name of [
        "game_mode",
        "team_mode",
        "perspective",
        "match_type",
        "map_name",
        "season_state",
        "is_custom_match",
        "year",
        "quarter",
        "month",
        "exact_date_kst",
        "hour",
        "from_date_kst",
        "to_date_kst",
      ]) {
        const value = String(form.get(name) || "");
        if (value) params.set(name, value);
      }
      const response = await fetch(`/players/weapon?${params.toString()}`);
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || response.statusText);
      }
      const payload = await response.json();
      const detail = payload.weapon;
      activeWeaponDetail = detail;
      const totals = detail.totals;
      const effectiveRanges = (detail.effective_ranges || []).map((item, index) => `
        <div class="result-row">
          <span>${index === 0 ? "최우선" : `#${index + 1}`} · ${item.reliable_sample ? "표본 확보" : "표본 부족"}</span>
          <strong>${escapeHtml(item.bucket_label)} · 교전 승리 ${percent(item.observed_win_rate)}</strong>
          <p>${formatInteger(item.wins)}승 / ${formatInteger(item.losses)}패 · ${formatInteger(item.fight_count)}교전 · 평균 ${formatNumber(item.avg_distance_m, 0)}m · 신뢰 보정 ${percent(item.confidence_adjusted_win_rate)} · 효율 ${formatNumber(item.efficiency_score, 1)}</p>
        </div>`).join("") || '<span class="result-caption">거리 정보가 있는 교전 기록이 없습니다.</span>';
      const recentRows = (detail.recent_matches || []).slice(0, 5).map((match) => `
        <div class="result-row">
          <span>${escapeHtml(String(match.created_at_kst || "-").replace("T", " ").slice(0, 16))}</span>
          <strong>${escapeHtml(match.map_name_ko || displayCode(match.map_name, "map"))} · ${escapeHtml(match.game_mode_ko || displayCode(match.game_mode, "game_mode"))}</strong>
          <p>${formatInteger(match.kills)}킬 · ${formatInteger(match.dbnos)}기절 · ${formatNumber(match.damage_dealt, 0)}딜</p>
        </div>`).join("") || '<span class="result-caption">최근 사용 경기 없음</span>';
      weaponBody.innerHTML = `<div class="result-shell">
        ${resultHeading(detail.weapon_name, `${detail.player.current_name} · 조건에 맞는 완료 경기`, `${formatInteger(totals.match_count)}경기`)}
        ${resultMetricGrid([
          ["사용 경기 / 치킨", `${formatInteger(totals.match_count)}전 / ${formatInteger(totals.wins)}회 · ${percent(totals.win_rate)}`],
          ["킬 / 어시 / 기절", `${formatInteger(totals.kills)} / ${formatInteger(totals.assists)} / ${formatInteger(totals.dbnos)}`],
          ["사망 / 당한 기절", `${formatInteger(totals.deaths_taken)} / ${formatInteger(totals.dbnos_taken)}`],
          ["피니시 / 당한 피니시", `${formatInteger(totals.finishes)} / ${formatInteger(totals.finishes_taken)}`],
          ["준 딜 / 받은 딜", `${formatNumber(totals.damage_dealt, 0)} / ${formatNumber(totals.damage_taken, 0)}`],
          ["경기당 평균 딜", formatNumber(totals.avg_damage_dealt, 1)],
          ["명중 지표", `${accuracyMetricText(totals.accuracy, totals.accuracy_metric)} · ${formatInteger(totals.shots_hit)}/${formatInteger(totals.shots_fired)}`],
          ["헤드샷 명중 확률", `${percent(totals.headshot_hit_rate)} · ${formatInteger(totals.headshot_hits)}/${formatInteger(characterHitCount(totals))} 캐릭터 명중`],
          ["캐릭터 / 차량 명중", hitTargetEvidence(totals)],
          ["차량에 가한 피해", formatNumber(totals.vehicle_damage_dealt, 1)],
          ["헤드샷 킬 비율", `${percent(totals.headshot_kill_rate)} · ${formatInteger(totals.headshot_kills)}/${formatInteger(totals.kills)}킬`],
          ["받은 헤드샷 비율", `${percent(totals.headshot_hit_taken_rate)} · ${formatInteger(totals.taken_hit_parts?.head)}/${formatInteger(totals.hits_taken)}피격`],
          ["교전 승리 확률", `${percent(totals.fight_win_rate)} · ${formatInteger(totals.fight_wins)}/${formatInteger(totals.fight_count)}교전`],
          ["경기당 평균 교전", `${formatNumber(totals.avg_fights_per_match, 2)}회`],
        ])}
        <div class="result-columns">
          ${resultSection("부위별 명중 확률", resultChips(hitPartEntries(totals.hit_parts, totals.hit_part_rates)))}
          ${resultSection("부위별 피격 확률", resultChips(hitPartEntries(totals.taken_hit_parts, totals.taken_hit_part_rates)))}
        </div>
        ${resultSection("무기 성과 시간 변화", `
          <div class="recommendation-chart-toolbar weapon-trend-toolbar">
            <div class="recommendation-view-switch" role="group" aria-label="무기 추세 집계 기준">
              <button class="secondary" type="button" data-weapon-trend-granularity="date">일별</button>
              <button class="secondary" type="button" data-weapon-trend-granularity="month">월별</button>
            </div>
            <label>그래프 지표
              <select data-weapon-trend-metric>
                <option value="fight_win_rate">교전 승리 확률</option>
                <option value="win_rate">사용 경기 승률</option>
                <option value="accuracy">명중 지표</option>
                <option value="headshot_hit_rate">헤드샷 명중 확률</option>
                <option value="avg_damage_dealt">경기당 준 피해</option>
                <option value="avg_damage_taken">경기당 받은 피해</option>
                <option value="avg_kills">경기당 킬</option>
                <option value="avg_dbnos">경기당 가한 기절</option>
                <option value="avg_deaths_taken">경기당 사망</option>
                <option value="match_count">사용 경기 수</option>
              </select>
            </label>
          </div>
          <div id="weaponTrendChart" class="metric-chart"></div>
        `)}
        ${resultSection("효율 교전 거리", `<div class="result-list">${effectiveRanges}</div>`)}
        ${resultSection("최근 사용 경기", `<div class="result-list">${recentRows}</div>`)}
      </div>`;
      renderWeaponTrendChart();
    }

    function dropZoneLocation(item) {
      if (item.region_display_name_ko) return item.region_display_name_ko;
      if (item.region_status === "dynamic_map") return `동적 맵 격자 ${item.grid_x},${item.grid_y}`;
      return `격자 ${item.grid_x},${item.grid_y}`;
    }

    async function loadDropZoneAnalysis(formElement) {
      const form = new FormData(formElement);
      const player = selectedRegisteredPlayer(formElement);
      const params = new URLSearchParams({
        shard: player.shard,
        account_id: player.account_id,
        min_matches: String(form.get("min_matches") || 1),
        limit: "500",
      });
      const response = await fetch(`/players/drop-zones?${params.toString()}`);
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || response.statusText);
      }
      const report = (await response.json()).drop_zones;
      const allRegions = report.regions || [];
      const allZones = report.zones || [];
      const mapFilter = String(form.get("map_filter") || "");
      const regionSearch = String(form.get("region_search") || "").trim().toLocaleLowerCase("ko-KR");
      const regions = allRegions.filter((item) => {
        if (mapFilter && item.map_name !== mapFilter) return false;
        const searchable = `${item.map_name_ko || ""} ${item.map_name || ""} ${item.region_name_ko || ""}`.toLocaleLowerCase("ko-KR");
        return !regionSearch || searchable.includes(regionSearch);
      });
      const zones = allZones.filter((item) => {
        if (mapFilter && item.map_name !== mapFilter) return false;
        const searchable = `${item.map_name_ko || ""} ${item.map_name || ""} ${dropZoneLocation(item)}`.toLocaleLowerCase("ko-KR");
        return !regionSearch || searchable.includes(regionSearch);
      });
      const sortMetric = String(form.get("sort_metric") || "landings");
      const chartLimit = Math.max(1, Math.min(500, Number(form.get("chart_limit") || 20)));
      const sortValues = {
        landings: (item) => Number(item.match_count || 0),
        win_rate: (item) => Number(item.win_rate || 0),
        avg_kills: (item) => Number(item.avg_kills || 0),
        avg_damage: (item) => Number(item.avg_damage_dealt || 0),
      };
      const sortValue = sortValues[sortMetric] || sortValues.landings;
      const sortedRegions = [...regions].sort((left, right) => (
        sortValue(right) - sortValue(left)
        || Number(right.match_count || 0) - Number(left.match_count || 0)
        || String(left.region_name_ko || "").localeCompare(String(right.region_name_ko || ""), "ko")
      ));
      const sortedZones = [...zones].sort((left, right) => (
        Number(right.match_count || 0) - Number(left.match_count || 0)
        || Number(right.win_rate || 0) - Number(left.win_rate || 0)
      ));
      const chartRegions = sortedRegions.slice(0, chartLimit);
      const sortLabels = {
        landings: "착지 횟수",
        win_rate: "승률",
        avg_kills: "평균 킬",
        avg_damage: "평균 피해",
      };
      const allLandings = allRegions.reduce((sum, item) => sum + Number(item.match_count || 0), 0);
      const totalLandings = regions.reduce((sum, item) => sum + Number(item.match_count || 0), 0);
      const totalWins = regions.reduce((sum, item) => sum + Number(item.wins || 0), 0);
      const regionKey = (item) => `${item.map_name}::${item.region_id}`;
      const regionRows = sortedRegions.map((item) => `
        <tr data-drop-region-row="${attr(regionKey(item))}">
          <td>${escapeHtml(item.map_name_ko || displayCode(item.map_name, "map"))}</td>
          <td><strong>${escapeHtml(item.region_name_ko)}</strong></td>
          <td>${formatInteger(item.match_count)}</td>
          <td>${formatInteger(item.wins)} · ${percent(item.win_rate)}</td>
          <td>${formatNumber(item.avg_kills, 2)} / ${formatNumber(item.avg_assists, 2)} / ${formatNumber(item.avg_dbnos, 2)} / ${formatNumber(item.avg_deaths, 2)}</td>
          <td>${formatNumber(item.avg_damage_dealt, 1)}</td>
          <td>${minutes(item.avg_survival_seconds)}</td>
          <td><button class="secondary drop-region-map-button" type="button" data-drop-region-map="${attr(item.map_name)}" data-drop-region-key="${attr(regionKey(item))}">지도에서 보기</button></td>
        </tr>
      `).join("");
      const chartDefinitions = {
        landings: {
          value: (item) => Number(item.match_count || 0),
          display: (item) => `${formatInteger(item.match_count)}회`,
        },
        win_rate: {
          value: (item) => Number(item.win_rate || 0) * 100,
          display: (item) => percent(item.win_rate),
          maximum: 100,
        },
        avg_kills: {
          value: (item) => Number(item.avg_kills || 0),
          display: (item) => formatNumber(item.avg_kills, 2),
        },
        avg_damage: {
          value: (item) => Number(item.avg_damage_dealt || 0),
          display: (item) => formatNumber(item.avg_damage_dealt, 1),
        },
      };
      const chartDefinition = chartDefinitions[sortMetric] || chartDefinitions.landings;
      const regionChart = recommendationChartRows(chartRegions, {
        label: (item) => `${item.map_name_ko || item.map_name} · ${item.region_name_ko}`,
        note: (item) => `${formatInteger(item.match_count)}회 착지 · ${formatInteger(item.wins)}치킨`,
        ...chartDefinition,
      });
      const zoneRows = sortedZones.map((item) => `
        <div class="result-row">
          <span>${escapeHtml(item.map_name_ko || displayCode(item.map_name, "map"))} · 격자 ${item.grid_x},${item.grid_y}</span>
          <strong>${escapeHtml(dropZoneLocation(item))}</strong>
          <p>${formatInteger(item.match_count)}회 · 승률 ${percent(item.win_rate)} · 평균 킬 ${formatNumber(item.avg_kills, 2)} · 기절 ${formatNumber(item.avg_dbnos, 2)} · 피해 ${formatNumber(item.avg_damage_dealt, 1)}</p>
        </div>
      `).join("") || '<span class="result-caption">조건을 충족한 세부 격자가 없습니다.</span>';
      const availableMaps = [...new Map(sortedRegions.map((item) => [item.map_name, item.map_name_ko || item.map_name])).entries()];
      const initialMap = mapFilter || availableMaps[0]?.[0] || "";
      const mapOptions = availableMaps.map(([name, label]) => `<option value="${attr(name)}"${name === initialMap ? " selected" : ""}>${escapeHtml(label)}</option>`).join("");
      dropZoneBody.innerHTML = `<div class="result-shell">
        ${resultHeading(report.player.current_name, `지역명 우선 · 최소 ${formatInteger(report.min_matches)}회 착지`, `${formatInteger(regions.length)}개 지역`)}
        ${resultMetricGrid([
          ["전체 기록 착지", `${formatInteger(allLandings)}회`],
          ["현재 조건 착지", `${formatInteger(totalLandings)}회`],
          ["착지 경기 치킨", `${formatInteger(totalWins)}회 · ${percent(totalWins / Math.max(1, totalLandings))}`],
          ["지역명 집계", `${formatInteger(regions.length)}개`],
          ["세부 격자", `${formatInteger(zones.length)}개`],
        ])}
        <div class="segmented-control drop-view-controls" id="dropAnalysisViewControls" role="group" aria-label="낙하 분석 보기">
          <button type="button" class="active" data-drop-analysis-view-button="map">지도</button>
          <button type="button" data-drop-analysis-view-button="chart">성과 그래프</button>
          <button type="button" data-drop-analysis-view-button="table">지역 상세</button>
          <button type="button" data-drop-analysis-view-button="grid">세부 격자</button>
        </div>
        <div class="drop-view-panel" data-drop-analysis-view="map">
          <div class="drop-map-panel" id="dropMapPanel">
            <div class="drop-map-toolbar"><h3>지역 위치</h3><label>표시할 맵<select id="dropMapSelect"${mapOptions ? "" : " disabled"}>${mapOptions || '<option value="">표시할 지역 없음</option>'}</select></label></div>
            <div class="drop-map-stage"><img id="dropMapImage" alt="낙하 지역 지도"><div class="drop-map-markers" id="dropMapMarkers"></div></div>
            <div class="drop-map-info" id="dropMapInfo">지역 행의 지도 버튼이나 지도 표식을 선택하세요.</div>
          </div>
        </div>
        <div class="drop-view-panel" data-drop-analysis-view="chart" hidden>
          <div class="metric-chart"><h3>지역별 ${escapeHtml(sortLabels[sortMetric] || sortLabels.landings)} · 상위 ${formatInteger(chartRegions.length)}개</h3>${regionChart}</div>
        </div>
        <div class="drop-view-panel" data-drop-analysis-view="table" hidden>
          ${resultSection("지역별 상세", `<div class="table-scroll"><table class="drop-region-table"><thead><tr><th>맵</th><th>지역</th><th>착지</th><th>치킨·승률</th><th>평균 킬/어시/기절/사망</th><th>평균 피해</th><th>평균 생존</th><th>위치</th></tr></thead><tbody>${regionRows || '<tr><td colspan="8">조건을 충족한 지역이 없습니다.</td></tr>'}</tbody></table></div>`)}
        </div>
        <div class="drop-view-panel" data-drop-analysis-view="grid" hidden>
          ${resultSection("세부 20×20 격자", `<div class="result-list">${zoneRows}</div>`)}
        </div>
      </div>`;

      const mapSelect = dropZoneBody.querySelector("#dropMapSelect");
      const mapImage = dropZoneBody.querySelector("#dropMapImage");
      const mapMarkers = dropZoneBody.querySelector("#dropMapMarkers");
      const mapInfo = dropZoneBody.querySelector("#dropMapInfo");
      const mapPanel = dropZoneBody.querySelector("#dropMapPanel");
      const setDropView = (view) => {
        const selectedView = ["map", "chart", "table", "grid"].includes(view) ? view : "map";
        dropZoneBody.querySelectorAll("[data-drop-analysis-view]").forEach((panel) => {
          panel.hidden = panel.dataset.dropAnalysisView !== selectedView;
        });
        dropZoneBody.querySelectorAll("[data-drop-analysis-view-button]").forEach((button) => {
          button.classList.toggle("active", button.dataset.dropAnalysisViewButton === selectedView);
        });
      };
      dropZoneBody.querySelectorAll("[data-drop-analysis-view-button]").forEach((button) => {
        button.addEventListener("click", () => setDropView(button.dataset.dropAnalysisViewButton || "map"));
      });
      const selectRegion = (selectedKey) => {
        const selected = sortedRegions.find((item) => regionKey(item) === selectedKey);
        dropZoneBody.querySelectorAll("[data-drop-region-row]").forEach((row) => row.classList.toggle("active", row.dataset.dropRegionRow === selectedKey));
        dropZoneBody.querySelectorAll("[data-drop-map-marker]").forEach((marker) => marker.classList.toggle("active", marker.dataset.dropMapMarker === selectedKey));
        if (!mapInfo) return;
        mapInfo.innerHTML = selected
          ? `<strong>${escapeHtml(selected.map_name_ko || selected.map_name)} · ${escapeHtml(selected.region_name_ko)}</strong>${formatInteger(selected.match_count)}회 착지 · ${formatInteger(selected.wins)}치킨 (${percent(selected.win_rate)}) · 평균 킬 ${formatNumber(selected.avg_kills, 2)} · 평균 피해 ${formatNumber(selected.avg_damage_dealt, 1)}<br>대표 좌표 X ${formatInteger(selected.centroid_x_cm)}cm · Y ${formatInteger(selected.centroid_y_cm)}cm`
          : "지도 표식을 선택하면 해당 지역의 성과가 표시됩니다.";
      };
      const renderMap = (mapName, selectedRegionKey = "") => {
        if (!mapImage || !mapMarkers) return;
        mapImage.src = mapName ? `/replay/map-assets/${encodeURIComponent(mapName)}` : "";
        mapImage.alt = `${availableMaps.find(([name]) => name === mapName)?.[1] || "맵"} 낙하 지역 지도`;
        const mapRegions = sortedRegions.filter((item) => item.map_name === mapName);
        mapMarkers.innerHTML = mapRegions.map((item, index) => {
          const x = Math.max(0, Math.min(100, Number(item.x_pct || 0) * 100));
          const y = Math.max(0, Math.min(100, Number(item.y_pct || 0) * 100));
          return `<button class="drop-map-marker" type="button" style="left:${x.toFixed(3)}%;top:${y.toFixed(3)}%" data-drop-map-marker="${attr(regionKey(item))}" title="${attr(item.region_name_ko)} · ${formatInteger(item.match_count)}회">${formatInteger(index + 1)}</button>`;
        }).join("");
        mapMarkers.querySelectorAll("[data-drop-map-marker]").forEach((marker) => marker.addEventListener("click", () => selectRegion(marker.dataset.dropMapMarker || "")));
        selectRegion(selectedRegionKey || (mapRegions[0] ? regionKey(mapRegions[0]) : ""));
      };
      mapSelect?.addEventListener("change", () => renderMap(mapSelect.value));
      dropZoneBody.querySelectorAll("[data-drop-region-map]").forEach((button) => button.addEventListener("click", () => {
        if (mapSelect) mapSelect.value = button.dataset.dropRegionMap || "";
        setDropView("map");
        renderMap(button.dataset.dropRegionMap || "", button.dataset.dropRegionKey || "");
        scrollWithinWorkspace(mapPanel, { behavior: "smooth", block: "nearest" });
      }));
      setDropView("map");
      renderMap(initialMap);
    }

    async function loadMapRegion(formElement) {
      const form = new FormData(formElement);
      const params = new URLSearchParams({
        map_name: String(form.get("map_name") || "Baltic_Main"),
        x_cm: String(form.get("x_cm") || "0"),
        y_cm: String(form.get("y_cm") || "0"),
      });
      const response = await fetch(`/map-regions/resolve?${params.toString()}`);
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || response.statusText);
      }
      const region = (await response.json()).map_region;
      const fallbackLabels = {
        unmatched: "등록 지명 밖",
        dynamic_map: "동적 지형",
        unsupported_map: "미지원 맵",
        invalid_coordinate: "좌표 범위 밖",
      };
      const statusLabel = fallbackLabels[region.status] || "확인됨";
      const location = region.region_display_name_ko || statusLabel;
      const normalized = region.x_pct === null || region.y_pct === null
        ? "-"
        : `${percent(region.x_pct)} / ${percent(region.y_pct)}`;
      const distance = region.distance_to_center_m === null
        ? "-"
        : `${formatNumber(region.distance_to_center_m, 1)}m`;
      mapRegionBody.innerHTML = [
        `<strong>${escapeHtml(region.map_name_ko)} · ${escapeHtml(location)}</strong>`,
        `원본 좌표: ${formatNumber(region.x_cm, 1)}, ${formatNumber(region.y_cm, 1)}cm · 정규화: ${normalized}`,
        `상태: ${escapeHtml(statusLabel)} · 지역 ID: ${escapeHtml(region.region_id || "-")} · 중심 거리: ${distance}`,
        `사전: ${escapeHtml(region.catalog_version)} · 출처: ${escapeHtml(String(region.source_commit || "-").slice(0, 7))}`,
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
      activeRecommendationReport = report;
      const loadouts = (report.loadouts || []).slice(0, 5).map((item) => {
        const primaryCombo = item.primary_attachment_combination;
        const secondaryCombo = item.secondary_attachment_combination;
        const primaryPlan = item.primary_attachment_plan || {};
        const secondaryPlan = item.secondary_attachment_plan || {};
        const primaryParts = (item.primary_attachments || []).map((part) => part.attachment_name);
        const secondaryParts = (item.secondary_attachments || []).map((part) => part.attachment_name);
        const burden = item.inventory_burden || {};
        const score = item.score_components || {};
        const planCaption = (plan, combo) => {
          const coverage = plan.known_slot_count
            ? `확인 슬롯 ${formatInteger(plan.selected_slot_count)}/${formatInteger(plan.known_slot_count)}`
            : "호환 파츠 표본 부족";
          const evidence = combo
            ? `${formatInteger(combo.match_count)}경기 · ${formatInteger(combo.event_count)}교전 · 승률 ${percent(combo.win_rate)} · 평균 딜 ${formatNumber(combo.avg_damage_dealt, 1)}`
            : `${formatInteger(plan.evidence_match_count || 0)}경기 근거`;
          return `${plan.basis || "무기별 파츠 성과 조합"} · ${coverage} · 신뢰도 ${plan.confidence || "낮음"} · ${evidence}`;
        };
        const ammoProfiles = (burden.weapon_profiles || []).map((profile) => (
          `${profile.weapon_name} · ${profile.ammo_type} ${formatInteger(profile.recommended_reserve_rounds)}발 · ` +
          `인벤토리 ${formatNumber(profile.reserve_inventory_weight, 1)}단위 · 경기당 발사 ${formatNumber(profile.observed_shots_per_match, 1)}발`
        ));
        const carriedAmmo = Object.entries(burden.carried_rounds_by_ammo || {}).map(([ammo, rounds]) => (
          `${ammo} ${formatInteger(rounds)}발 · 인벤토리 ${formatNumber(burden.inventory_weight_by_ammo?.[ammo], 1)}단위`
        ));
        return `<article class="loadout-card">
          <div class="loadout-weapons">
            <div><span class="loadout-role">근·중거리</span><strong>${escapeHtml(item.primary.weapon_name)}</strong></div>
            <span class="loadout-plus">+</span>
            <div><span class="loadout-role">중·장거리</span><strong>${escapeHtml(item.secondary.weapon_name)}</strong></div>
          </div>
          <div class="loadout-parts">
            <div><span class="loadout-role">${escapeHtml(item.primary.weapon_name)} 추천 파츠 계획</span>${resultChips(primaryParts, "호환 파츠 실전 표본 부족")}<span class="result-caption">${escapeHtml(planCaption(primaryPlan, primaryCombo))}</span></div>
            <div><span class="loadout-role">${escapeHtml(item.secondary.weapon_name)} 추천 파츠 계획</span>${resultChips(secondaryParts, "호환 파츠 실전 표본 부족")}<span class="result-caption">${escapeHtml(planCaption(secondaryPlan, secondaryCombo))}</span></div>
            <div><span class="loadout-role">무기별 예비탄 기준</span>${resultChips(ammoProfiles, "탄종 확인 불가")}</div>
            <div><span class="loadout-role">실제 휴대 계산</span>${resultChips(carriedAmmo, "탄종 확인 불가")}</div>
            <div><span class="loadout-role">인벤토리 고려사항</span>${resultChips(burden.tradeoffs || [], "추가 고려사항 없음")}</div>
          </div>
          <div class="loadout-score">조합 점수 ${formatNumber(item.score, 1)} · 예상 탄약 인벤토리 ${formatNumber(burden.estimated_inventory_weight, 1)}단위 · 부담 ${escapeHtml(burden.pressure_level || "-")} · 조정 ${formatNumber(score.inventory_adjustment, 1)}</div>
          <details class="result-disclosure">
            <summary>점수 계산</summary>
            ${resultTextRows([
              ["근·중거리 성과 55%", formatNumber(score.primary_performance_55pct, 1)],
              ["중·장거리 성과 45%", formatNumber(score.secondary_performance_45pct, 1)],
              ["혼합 탄종 확보 부담", `-${formatNumber(burden.mixed_ammo_penalty, 1)}`],
              ["동일 탄종 공유 이점", `+${formatNumber(burden.shared_ammo_bonus, 1)}`],
              ["탄약 인벤토리 부담", `-${formatNumber(burden.reserve_pressure_penalty, 1)}`],
              ["LMG 초과 예비탄 인벤토리", `${formatNumber(burden.lmg_extra_reserve_inventory_weight, 1)}단위`],
              ["전체 부담 중 LMG 영향", `-${formatNumber(burden.lmg_reserve_penalty, 1)}`],
              ["탄약·인벤토리 조정", formatNumber(score.inventory_adjustment, 1)],
              ["예상 총 탄약 인벤토리", `${formatNumber(burden.estimated_inventory_weight, 1)}단위`],
              ["모델 기준", burden.basis || "-"],
            ])}
          </details>
        </article>`;
      }).join("") || '<span class="result-caption">조건을 충족한 2주무기 조합이 없습니다.</span>';
      const weapons = recommendationRows(report.weapons, (item, index) => {
        const score = item.score_components || {};
        return `<div class="result-row">
          <span>${formatInteger(index + 1)}위 · ${formatInteger(item.match_count)}경기</span>
          <strong>${escapeHtml(item.weapon_name)}</strong>
          <p>점수 ${formatNumber(item.score, 1)} · 평균 딜 ${formatNumber(item.avg_damage_dealt, 1)} · 승률 ${percent(item.win_rate)} · ${escapeHtml(accuracyMetricText(item.accuracy, item.accuracy_metric))}<br>
          헤드샷 명중 ${percent(item.headshot_hit_rate)} (${formatInteger(item.headshot_hits)}/${formatInteger(characterHitCount(item))} 캐릭터 명중) · 차량 명중 ${formatInteger(item.vehicle_hits)}회 · 교전 승률 ${percent(item.fight_win_rate)} (${formatInteger(item.fight_wins)}승/${formatInteger(item.fight_losses)}패)<br>${escapeHtml(item.reason || "")}</p>
          <details class="result-disclosure">
            <summary>무기 점수 계산</summary>
            ${resultTextRows([
              ["평균 피해", formatNumber(score.average_damage, 1)],
              ["킬 기여", formatNumber(score.kills, 1)],
              ["기절 기여", formatNumber(score.dbnos, 1)],
              ["어시스트 기여", formatNumber(score.assists, 1)],
              ["치킨 기여", formatNumber(score.wins, 1)],
              ["명중 기여", formatNumber(score.accuracy, 1)],
              ["사망 감점", formatNumber(score.deaths_penalty, 1)],
              ["표본 신뢰도", percent(score.confidence_factor || 0)],
              ["거리 표본 보너스", `${formatNumber(score.range_bonus, 1)} / 상한 ${formatNumber(score.range_bonus_cap || 12, 0)}`],
              ["거리 성과 이벤트 표본", `${formatInteger(score.range_evidence_events)}건`],
              ["교전 승률 조정", formatNumber(score.fight_adjustment, 1)],
            ])}
          </details>
        </div>`;
      });
      const weaponParts = groupedWeaponRecommendationRows(report.weapon_attachments, (item) => `
        <div class="result-row">
          <span>${formatInteger(item.match_count)}경기 · ${formatInteger(item.event_count || item.attached_events)}교전</span>
          <strong>${escapeHtml(item.attachment_name)}</strong>
          <div class="result-row-tail">
            <p>점수 ${formatNumber(item.score, 1)} · ${formatInteger(item.event_count || item.attached_events)}회 · ${distanceM(item.avg_distance_m)}</p>
            <button class="secondary" type="button" data-evidence="weapon-attachment" data-weapon-code="${attr(item.weapon_code)}" data-attachment-code="${attr(item.attachment_code)}">근거</button>
          </div>
        </div>`);
      const attachmentCombinations = groupedWeaponRecommendationRows(report.attachment_combinations, (item) => `
        <div class="result-row">
          <span>${formatInteger(item.match_count)}경기 · ${formatInteger(item.event_count)}교전</span>
          <strong>${escapeHtml((item.attachment_names || []).join(" + "))}</strong>
          <p>조합 점수 ${formatNumber(item.score, 1)} · 승률 ${percent(item.win_rate)} · ${formatInteger(item.kills)}킬 · ${formatInteger(item.dbnos)}기절 · 평균 딜 ${formatNumber(item.avg_damage_dealt, 1)} · 평균 ${distanceM(item.avg_distance_m)}</p>
          <details class="result-disclosure">
            <summary>조합 점수 계산</summary>
            ${resultTextRows([
              ["킬 기여", formatNumber(item.score_components?.kills, 1)],
              ["기절 기여", formatNumber(item.score_components?.dbnos, 1)],
              ["피니시 기여", formatNumber(item.score_components?.finishes, 1)],
              ["헤드샷 기여", formatNumber(item.score_components?.headshots, 1)],
              ["교전 표본 기여", formatNumber(item.score_components?.events, 1)],
              ["치킨 기여", formatNumber(item.score_components?.wins, 1)],
              ["평균 피해 기여", formatNumber(item.score_components?.average_damage, 1)],
            ])}
          </details>
        </div>`);
      const weaponRanges = groupedWeaponRecommendationRows(report.weapon_ranges, (item) => `
        <div class="result-row"><span>${formatInteger(item.event_count)}교전</span><strong>${escapeHtml(item.bucket_label)}</strong><p>${formatInteger(item.kills)}킬 · ${formatInteger(item.dbnos)}기절 · 평균 ${distanceM(item.avg_distance_m)}</p></div>`);
      const attachments = recommendationRows(report.attachments, (item) => `
        <div class="result-row"><span>${formatInteger(item.attached_events)}회 장착</span><strong>${escapeHtml(item.item_name)}</strong><p>점수 ${formatNumber(item.score, 1)} · 평균 딜 ${formatNumber(item.avg_damage_dealt, 1)}</p></div>`);
      const maps = recommendationRows(report.maps, (item) => `
        <div class="result-row"><span>${formatInteger(item.match_count)}경기 · ${formatInteger(item.wins)}치킨</span><strong>${escapeHtml(item.map_name_ko)}</strong><p>점수 ${formatNumber(item.score, 1)} · 승률 ${percent(item.win_rate)} · 경기당 킬 ${formatNumber(item.kills / Math.max(1, item.match_count), 2)} · 기절 ${formatNumber(item.dbnos / Math.max(1, item.match_count), 2)} · 어시 ${formatNumber(item.assists / Math.max(1, item.match_count), 2)} · 사망 ${formatNumber(item.deaths / Math.max(1, item.match_count), 2)} · 평균 딜 ${formatNumber(item.avg_damage_dealt, 1)} · 생존 ${minutes(item.avg_survival_seconds)}</p></div>`);
      const teammates = recommendationRows(report.teammates, (item) => `
        <div class="result-row"><span>${item.registered ? "등록 유저" : `${formatInteger(item.match_count)}경기`}</span><strong>${escapeHtml(item.name)}</strong><p>점수 ${formatNumber(item.score, 1)} · 승률 ${percent(item.win_rate)}</p></div>`);
      recommendationBody.innerHTML = `<div class="result-shell">
        ${resultHeading(report.player.current_name, `추천 채택 기준 · 최소 ${formatInteger(report.min_matches || minMatches || 1)}경기`, "추천 분석")}
        <div class="recommendation-view-switch" role="tablist" aria-label="추천 결과 보기">
          <button type="button" role="tab" data-recommendation-view="summary">요약</button>
          <button type="button" role="tab" data-recommendation-view="chart">그래프</button>
        </div>
        <div class="recommendation-panel" data-recommendation-panel="summary">
          ${resultSection("추천 2주무기 조합", `<div class="loadout-grid">${loadouts}</div>`)}
          <details class="result-disclosure"><summary>무기별 상세 · ${formatInteger((report.weapons || []).length)}개</summary><div class="result-list">${weapons}</div></details>
          <details class="result-disclosure"><summary>실전 파츠 전체 조합 · ${formatInteger((report.attachment_combinations || []).length)}개</summary><div class="result-list">${attachmentCombinations}</div></details>
          <details class="result-disclosure"><summary>파츠별 개별 성과 · ${formatInteger((report.weapon_attachments || []).length)}개</summary><div class="result-list">${weaponParts}</div></details>
          <details class="result-disclosure"><summary>성과 발생 거리 · ${formatInteger((report.weapon_ranges || []).length)}개</summary><div class="result-list">${weaponRanges}</div></details>
          <details class="result-disclosure"><summary>전체 파츠 성과 · ${formatInteger((report.attachments || []).length)}개</summary><div class="result-list">${attachments}</div></details>
          <details class="result-disclosure"><summary>맵 · ${formatInteger((report.maps || []).length)}개</summary><div class="result-list">${maps}</div></details>
          <details class="result-disclosure"><summary>팀원 · ${formatInteger((report.teammates || []).length)}명</summary><div class="result-list">${teammates}</div></details>
          <div class="detail-panel status" id="recommendationEvidence">추천 근거 대기 중</div>
        </div>
        <div class="recommendation-panel" data-recommendation-panel="chart" hidden></div>
      </div>`;
      renderRecommendationCharts(report, activeRecommendationChartMetric);
      setRecommendationView(activeRecommendationView);
    }

    function recommendationRows(items, formatter) {
      if (!items || !items.length) return '<span class="result-caption">조건을 충족한 기록이 없습니다.</span>';
      return items.map(formatter).join("");
    }

    function groupedWeaponRecommendationRows(items, formatter) {
      if (!items || !items.length) return '<span class="result-caption">조건을 충족한 기록이 없습니다.</span>';
      const groups = new Map();
      for (const item of items) {
        const key = item.weapon_code || item.weapon_name;
        if (!groups.has(key)) groups.set(key, { name: item.weapon_name || key, items: [] });
        groups.get(key).items.push(item);
      }
      return Array.from(groups.values()).map((group) => `
        <div class="result-section">
          <h3>${escapeHtml(group.name)} · ${formatInteger(group.items.length)}개</h3>
          <div class="result-list">${group.items.map(formatter).join("")}</div>
        </div>
      `).join("");
    }

    function setRecommendationView(view) {
      const selected = view === "chart" ? "chart" : "summary";
      activeRecommendationView = selected;
      recommendationBody.querySelectorAll("[data-recommendation-view]").forEach((button) => {
        const active = button.dataset.recommendationView === selected;
        button.classList.toggle("active", active);
        button.setAttribute("aria-selected", active ? "true" : "false");
        button.tabIndex = active ? 0 : -1;
      });
      recommendationBody.querySelectorAll("[data-recommendation-panel]").forEach((panel) => {
        panel.hidden = panel.dataset.recommendationPanel !== selected;
      });
    }

    function recommendationChartRows(items, options) {
      const rows = (items || []).map((item) => {
        const value = Number(options.value(item));
        return {
          label: options.label(item),
          note: options.note ? options.note(item) : "",
          value: Number.isFinite(value) && value >= 0 ? value : 0,
          display: options.display(item, value),
        };
      });
      if (!rows.length) return '<span class="result-caption">조건을 충족한 그래프 데이터가 없습니다.</span>';
      const maximum = options.maximum || Math.max(1, ...rows.map((row) => row.value));
      return `<div class="metric-chart-list">${rows.map((row) => {
        const width = Math.min(100, Math.max(row.value > 0 ? 2 : 0, (row.value / maximum) * 100));
        const aria = `${row.label} ${row.display}`;
        return `<div class="metric-chart-row">
          <span class="metric-chart-label">${escapeHtml(row.label)}${row.note ? `<small>${escapeHtml(row.note)}</small>` : ""}</span>
          <span class="metric-chart-track" role="img" aria-label="${attr(aria)}"><i class="metric-chart-fill ${options.tone || ""}" style="width:${width.toFixed(2)}%"></i></span>
          <strong class="metric-chart-value">${escapeHtml(row.display)}</strong>
        </div>`;
      }).join("")}</div>`;
    }

    function recommendationWeaponMetric(metric) {
      const definitions = {
        score: {
          label: "종합 점수",
          value: (item) => item.score,
          display: (item) => formatNumber(item.score, 1),
        },
        matches: {
          label: "사용 경기",
          value: (item) => item.match_count,
          display: (item) => `${formatInteger(item.match_count)}경기`,
        },
        damage: {
          label: "경기당 평균 딜",
          value: (item) => item.avg_damage_dealt,
          display: (item) => formatNumber(item.avg_damage_dealt, 1),
        },
        win_rate: {
          label: "승률",
          value: (item) => Number(item.win_rate) * 100,
          display: (item) => percent(item.win_rate),
          maximum: 100,
        },
        accuracy: {
          label: "명중 지표",
          value: (item) => Number(item.accuracy) * 100,
          display: (item) => percent(item.accuracy),
          maximum: 100,
        },
        headshot_hit_rate: {
          label: "헤드샷 명중 확률",
          value: (item) => Number(item.headshot_hit_rate) * 100,
          display: (item) => percent(item.headshot_hit_rate),
          maximum: 100,
        },
        fight_win_rate: {
          label: "교전 승리 확률",
          value: (item) => Number(item.fight_win_rate) * 100,
          display: (item) => percent(item.fight_win_rate),
          maximum: 100,
        },
        kills: {
          label: "경기당 평균 킬",
          value: (item) => item.kills_per_match,
          display: (item) => formatNumber(item.kills_per_match, 2),
        },
        dbnos: {
          label: "경기당 평균 기절",
          value: (item) => item.dbnos_per_match,
          display: (item) => formatNumber(item.dbnos_per_match, 2),
        },
      };
      return definitions[metric] || definitions.score;
    }

    function renderRecommendationCharts(report, metric) {
      const panel = recommendationBody.querySelector('[data-recommendation-panel="chart"]');
      if (!panel) return;
      const definition = recommendationWeaponMetric(metric);
      const metricOptions = [
        ["score", "종합 점수"],
        ["matches", "사용 경기"],
        ["damage", "경기당 평균 딜"],
        ["win_rate", "승률"],
        ["accuracy", "명중 지표"],
        ["headshot_hit_rate", "헤드샷 명중 확률"],
        ["fight_win_rate", "교전 승리 확률"],
        ["kills", "경기당 평균 킬"],
        ["dbnos", "경기당 평균 기절"],
      ].map(([value, label]) => `<option value="${value}"${value === metric ? " selected" : ""}>${label}</option>`).join("");
      const weaponChart = recommendationChartRows(report.weapons, {
        label: (item) => item.weapon_name,
        note: (item) => `${formatInteger(item.match_count)}경기 표본`,
        value: definition.value,
        display: definition.display,
        maximum: definition.maximum,
      });
      const loadoutChart = recommendationChartRows(report.loadouts, {
        label: (item) => `${item.primary.weapon_name} + ${item.secondary.weapon_name}`,
        value: (item) => item.score,
        display: (item) => formatNumber(item.score, 1),
        tone: "warning",
      });
      const attachmentChart = recommendationChartRows(report.weapon_attachments, {
        label: (item) => `${item.weapon_name} · ${item.attachment_name}`,
        note: (item) => `${formatInteger(item.match_count)}경기 · ${formatInteger(item.event_count || item.attached_events)}회`,
        value: (item) => item.score,
        display: (item) => formatNumber(item.score, 1),
        tone: "info",
      });
      const combinationChart = recommendationChartRows(report.attachment_combinations, {
        label: (item) => `${item.weapon_name} · ${(item.attachment_names || []).join(" + ")}`,
        note: (item) => `${formatInteger(item.match_count)}경기 · ${formatInteger(item.event_count)}교전`,
        value: (item) => item.score,
        display: (item) => formatNumber(item.score, 1),
        tone: "info",
      });
      const mapChart = recommendationChartRows(report.maps, {
        label: (item) => item.map_name_ko,
        note: (item) => `${formatInteger(item.match_count)}경기 표본`,
        value: (item) => Number(item.win_rate) * 100,
        display: (item) => percent(item.win_rate),
        maximum: 100,
      });
      panel.innerHTML = `
        <div class="recommendation-chart-toolbar">
          <label>무기 비교 지표
            <select data-recommendation-chart-metric>${metricOptions}</select>
          </label>
        </div>
        <div class="metric-chart-grid">
          <div class="metric-chart"><h3>무기 · ${escapeHtml(definition.label)}</h3>${weaponChart}</div>
          <div class="metric-chart"><h3>추천 2주무기 조합 · 점수</h3>${loadoutChart}</div>
          <div class="metric-chart"><h3>실전 파츠 전체 조합 · 점수</h3>${combinationChart}</div>
          <div class="metric-chart"><h3>파츠별 개별 성과 · 점수</h3>${attachmentChart}</div>
          <div class="metric-chart"><h3>맵 · 승률</h3>${mapChart}</div>
        </div>`;
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
      const actionLabels = { kill: "킬", dbno: "기절", dbno_caused: "기절시킴", finish: "피니시", damage: "피해", hit_caused: "명중", shot: "발사" };
      const rows = (report.snapshots || []).map((snapshot) => `
        <tr>
          <td>${escapeHtml(snapshot.combat_event_at_kst || "-")}</td>
          <td>${escapeHtml(snapshot.map_name_ko || displayCode(snapshot.map_name, "map"))}<br>${escapeHtml(snapshot.game_mode_ko || displayCode(snapshot.game_mode, "game_mode"))}</td>
          <td>${escapeHtml(actionLabels[snapshot.combat_action] || snapshot.combat_action)}${snapshot.is_headshot ? " · 헤드샷" : ""}</td>
          <td>${distanceM(snapshot.distance_m)}</td>
          <td>${escapeHtml((snapshot.equipped_attachment_names || []).join(", ") || "-")}</td>
          <td>${escapeHtml(String(snapshot.match_id || "").slice(0, 8))}</td>
        </tr>
      `).join("");

      panel.innerHTML = `<div class="result-shell">
        ${resultHeading(`${report.weapon_name} + ${report.attachment_name}`, "추천 산정 근거", `${formatInteger(totals.match_count)}경기`)}
        ${resultMetricGrid([
          ["교전", `${formatInteger(totals.event_count)}회`],
          ["킬", `${formatInteger(totals.kills)}회`],
          ["기절", `${formatInteger(totals.dbnos)}회`],
          ["피니시", `${formatInteger(totals.finishes)}회`],
          ["헤드샷", `${formatInteger(totals.headshots)}회`],
          ["평균 거리", distanceM(totals.avg_distance_m)],
        ])}
        ${rows
          ? `<div class="table-scroll"><table class="detail-table"><thead><tr><th>시간</th><th>맵 / 모드</th><th>결과</th><th>거리</th><th>장착 파츠</th><th>매치</th></tr></thead><tbody>${rows}</tbody></table></div>`
          : '<span class="result-caption">해당 무기와 파츠의 교전 근거가 없습니다.</span>'}
      </div>`;
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
      const weapons = (detail.weapons || []).slice(0, 6).map((weapon) => `
        <div class="result-row">
          <span>${formatInteger(weapon.shots_hit)}/${formatInteger(weapon.shots_fired)} 명중</span>
          <strong>${escapeHtml(weapon.weapon_name)}</strong>
          <p>${formatInteger(weapon.kills)}킬 · ${formatInteger(weapon.dbnos)}기절 · 준/받은 피해 ${formatNumber(weapon.damage_dealt, 1)}/${formatNumber(weapon.damage_taken, 1)} · ${escapeHtml(accuracyMetricText(weapon.accuracy, weapon.accuracy_metric))}<br>헤드샷 명중 ${percent(weapon.headshot_hit_rate)} · 헤드샷 킬 ${percent(weapon.headshot_kill_rate)}</p>
        </div>`).join("") || '<span class="result-caption">무기별 기록 없음</span>';
      const itemSummary = detail.item_summary || {};
      const itemRows = (detail.items || []).map((item) => `
        <tr>
          <td><strong>${escapeHtml(item.item_name || item.item_code || "-")}</strong><br><span class="result-caption">${escapeHtml(item.item_category_ko || "기타")} · ${escapeHtml(item.item_sub_category_ko || "기타")}</span></td>
          <td>${formatInteger(item.picked_up_quantity)}개<br><span class="result-caption">${formatInteger(item.picked_up_events)}회</span></td>
          <td>${formatInteger(item.used_quantity)}개<br><span class="result-caption">${formatInteger(item.used_events)}회</span></td>
          <td>${formatInteger(item.dropped_quantity)}개<br><span class="result-caption">${formatInteger(item.dropped_events)}회</span></td>
          <td>${formatInteger(item.equipped_events)} / ${formatInteger(item.unequipped_events)}</td>
          <td>${formatInteger(item.attached_events)} / ${formatInteger(item.detached_events)}</td>
        </tr>`).join("");
      const itemDetail = itemRows
        ? `<details class="result-disclosure"><summary>아이템별 상세 · ${formatInteger((detail.items || []).length)}종</summary>
            <div class="table-scroll"><table class="detail-table"><thead><tr><th>아이템</th><th>획득</th><th>사용</th><th>버림</th><th>장착 / 해제</th><th>부착 / 해제</th></tr></thead><tbody>${itemRows}</tbody></table></div>
          </details>`
        : '<span class="result-caption">수집된 아이템 기록이 없습니다.</span>';
      const activity = detail.activity_summary || {};
      const activityContent = activity.available
        ? resultTextRows([
            ["회복", `${formatInteger(activity.heal_events)}회 · ${formatNumber(activity.heal_amount, 1)} 회복`],
            ["투척물 / 플레어", `${formatInteger(activity.throwable_uses)} / ${formatInteger(activity.flare_uses)}회`],
            ["팀원 부활 / 부활 받음", `${formatInteger(activity.revives_caused)} / ${formatInteger(activity.revives_received)}회`],
            ["차량 탑승 / 하차", `${formatInteger(activity.vehicle_rides)} / ${formatInteger(activity.vehicle_leaves)}회`],
            ["차량 이동 / 최고 속도", `${distanceKm(activity.vehicle_distance_m)} / ${formatNumber(activity.vehicle_max_speed, 1)}`],
            ["차량 피해 / 파괴", `${formatNumber(activity.vehicle_damage, 1)} / ${formatInteger(activity.vehicle_destroys)}회`],
            ["볼팅 / 수영", `${formatInteger(activity.vaults)} / ${formatInteger(activity.swim_sessions)}회`],
            ["기절 플레이어 운반", `${formatInteger(activity.carry_events)}회`],
            ["상대 / 내 방어구 파괴", `${formatInteger(activity.armor_destroys_caused)} / ${formatInteger(activity.armor_destroys_taken)}회`],
            ["오브젝트 상호작용 / 파괴", `${formatInteger(activity.object_interactions)} / ${formatInteger(activity.object_destroys)}회`],
            ["긴급 수송 호출 / 탑승", `${formatInteger(activity.emergency_pickup_calls)} / ${formatInteger(activity.emergency_pickup_rides)}회`],
            ["복귀", `${formatInteger(activity.redeploys)}회`],
            ["기타 정규화 사건", `${formatInteger(activity.normalized_event_count)}건`],
          ])
        : '<span class="result-caption">활동 집계가 아직 생성되지 않았습니다.</span>';
      const fights = detail.fight_summary || {};
      const fightContent = resultTextRows([
        ["전체 교전", `${formatInteger(fights.fight_count)}회`],
        ["교전 승리 / 패배", `${formatInteger(fights.wins)} / ${formatInteger(fights.losses)}회 · 승리 ${percent(fights.fight_win_rate)}`],
        ["킬 / 기절시킴 승리", `${formatInteger(fights.kill_wins)} / ${formatInteger(fights.dbno_wins)}회`],
        ["사망 / 기절당함 패배", `${formatInteger(fights.death_losses)} / ${formatInteger(fights.dbno_losses)}회`],
        ["사람 / 봇 상대", `${formatInteger(fights.human_opponent_fights)} / ${formatInteger(fights.bot_opponent_fights)}회`],
        ["헤드샷 교전 승리", `${formatInteger(fights.headshot_wins)}회`],
      ]) + `<p class="result-caption">${escapeHtml(fights.definition || "교전 결과 데이터 기준으로 계산합니다.")}</p>`;
      const snapshot = detail.replay_artifact
        ? `<a class="result-badge success" href="${attr(detail.replay_artifact.view_url)}" target="_blank" rel="noreferrer">2D 스냅샷 열기</a>`
        : '<span class="result-caption">생성된 2D 스냅샷 없음</span>';
      const playedAt = String(detail.created_at_kst || "-").replace("T", " ").slice(0, 16) + " KST";
      const mapAndMode = [
        detail.map_name_ko || displayCode(detail.map_name, "map"),
        detail.game_mode_ko || displayCode(detail.game_mode, "game_mode"),
        detail.match_type_ko || displayCode(detail.match_type, "match_type"),
        detail.season_state_ko,
      ].filter(Boolean).join(" · ");
      matchBody.innerHTML = `<div class="result-shell">
        ${resultHeading(
          detail.player.current_name,
          `${playedAt} · ${mapAndMode}`,
          detail.is_chicken ? "치킨" : (detail.win_place ? `${formatInteger(detail.win_place)}위` : "결과 없음"),
          detail.is_chicken ? "success" : "",
        )}
        ${resultMetricGrid([
          ["경기 분류", `${detail.team_mode_ko || "-"} · ${detail.perspective_ko || "-"}${detail.is_custom_match ? " · 사용자 지정" : ""}`],
          ["전체 / 사람 / 봇", `${formatInteger(detail.total_players)} / ${formatInteger(detail.human_players)} / ${formatInteger(detail.bot_players)}`],
          ["킬 / 사망 / 어시", `${formatInteger(detail.kills)} / ${formatInteger(detail.deaths)} / ${formatInteger(detail.assists)}`],
          ["기절시킴 / 당함", `${formatInteger(detail.dbnos_caused)} / ${formatInteger(detail.dbnos_taken)}`],
          ["준 딜 / 받은 딜", `${formatNumber(detail.damage_dealt, 1)} / ${formatNumber(detail.damage_taken, 1)}`],
          ["공격 / 명중", `${formatInteger(detail.shots_fired)} / ${formatInteger(detail.shots_hit)}`],
          ["명중 지표", accuracyBreakdownText(detail.accuracy, detail.accuracy_breakdown)],
          ["헤드샷 명중 확률", `${percent(detail.headshot_hit_rate)} · ${formatInteger(detail.headshot_hits)}/${formatInteger(characterHitCount(detail))} 캐릭터 명중`],
          ["캐릭터 / 차량 명중", hitTargetEvidence(detail)],
          ["차량에 가한 피해", formatNumber(detail.vehicle_damage_dealt, 1)],
          ["받은 헤드샷 비율", `${percent(detail.headshot_hit_taken_rate)} · ${formatInteger(detail.headshot_hits_taken)}/${formatInteger(detail.hits_taken)}피격`],
          ["헤드샷 킬 비율", `${percent(detail.headshot_kill_rate)} · ${formatInteger(detail.headshot_kills)}/${formatInteger(detail.kills)}킬`],
          ["교전 / 승리 확률", `${formatInteger(fights.fight_count)}회 · ${percent(fights.fight_win_rate)}`],
          ["아이템 획득 / 사용", `${formatInteger(itemSummary.picked_up_quantity)}개 / ${formatInteger(itemSummary.used_quantity)}개`],
          ["생존 / 이동", `${minutes(detail.survival_seconds)} / ${distanceKm(detail.movement_distance_m)}`],
          ["낙하 이동", distanceM(detail.landing_distance_m)],
        ])}
        <div class="result-columns">
          ${resultSection("헤드샷", resultTextRows([
            ["가한 기록", `명중 ${formatInteger(detail.headshot_hits)} · 킬 ${formatInteger(detail.headshot_kills)} · 기절 ${formatInteger(detail.headshot_dbnos_caused)}`],
            ["받은 기록", `명중 ${formatInteger(detail.headshot_hits_taken)} · 사망 ${formatInteger(detail.headshot_deaths)} · 기절 ${formatInteger(detail.headshot_dbnos_taken)}`],
          ]))}
          ${resultSection("2D 리플레이", snapshot)}
        </div>
        <div class="result-columns">
          ${resultSection("부위별 명중 확률", resultChips(hitPartEntries(detail.hit_parts, detail.hit_part_rates)))}
          ${resultSection("부위별 피격 확률", resultChips(hitPartEntries(detail.taken_hit_parts, detail.taken_hit_part_rates)))}
        </div>
        <div class="result-columns">
          ${resultSection("교전 종합", fightContent)}
          ${resultSection("활동 종합", activityContent)}
        </div>
        ${resultSection("사용 무기", `<div class="result-list">${weapons}</div>`)}
        ${resultSection("아이템 종합", `${resultMetricGrid([
          ["아이템 종류", `${formatInteger(itemSummary.unique_item_types)}종`],
          ["사용 아이템 종류", `${formatInteger(itemSummary.used_item_types)}종`],
          ["획득", `${formatInteger(itemSummary.picked_up_quantity)}개 · ${formatInteger(itemSummary.picked_up_events)}회`],
          ["사용", `${formatInteger(itemSummary.used_quantity)}개 · ${formatInteger(itemSummary.used_events)}회`],
          ["버림", `${formatInteger(itemSummary.dropped_quantity)}개 · ${formatInteger(itemSummary.dropped_events)}회`],
          ["루트박스 / 보급", `${formatInteger(itemSummary.loot_box_pickup_events)} / ${formatInteger(itemSummary.carepackage_pickup_events)}회`],
          ["커스텀 패키지", `${formatInteger(itemSummary.custom_package_pickup_events)}회`],
          ["차량 트렁크 획득 / 보관", `${formatInteger(itemSummary.vehicle_trunk_pickup_events)} / ${formatInteger(itemSummary.vehicle_trunk_put_events)}회`],
          ["장착 / 해제", `${formatInteger(itemSummary.equipped_events)} / ${formatInteger(itemSummary.unequipped_events)}회`],
          ["파츠 부착 / 해제", `${formatInteger(itemSummary.attached_events)} / ${formatInteger(itemSummary.detached_events)}회`],
        ])}${itemDetail}`)}
      </div>`;
    }

    async function loadMatchExplorer({ offset = 0 } = {}) {
      const form = new FormData(matchExplorerForm);
      const params = new URLSearchParams();
      for (const key of ["shard", "search", "map_name", "game_mode", "match_type", "created_from_kst", "created_to_kst", "limit"]) {
        const value = String(form.get(key) || "").trim();
        if (value) params.set(key, value);
      }
      params.set("telemetry_only", String(Boolean(matchExplorerForm.elements.telemetry_only.checked)));
      params.set("offset", String(Math.max(0, Number(offset || 0))));
      matchExplorerStatus.textContent = "저장된 매치를 조회하는 중입니다.";
      const payload = await requestJson(`/matches/explorer?${params.toString()}`, "GET");
      matchExplorerPage = {
        total: Number(payload.total || 0),
        limit: Number(payload.limit || 50),
        offset: Number(payload.offset || 0),
        has_previous: Boolean(payload.has_previous),
        has_next: Boolean(payload.has_next),
      };
      matchExplorerBody.innerHTML = (payload.matches || []).map((match) => `
        <tr>
          <td>${escapeHtml(formatKstShort(match.created_at_kst))}<br><span class="result-caption">${escapeHtml(compactIdentifier(match.match_id, 10, 6))}</span></td>
          <td><strong>${escapeHtml(match.map_label || match.map_name || "-")}</strong><br><span class="result-caption">${escapeHtml([match.game_mode_label || match.game_mode, match.match_type_label || match.match_type].filter(Boolean).join(" · "))}</span></td>
          <td>${formatInteger(match.total_players ?? match.participant_count)}명<br><span class="result-caption">사람 ${formatInteger(match.human_players)} · 봇 ${formatInteger(match.bot_players)}</span></td>
          <td>${formatInteger(match.registered_participant_count)}명</td>
          <td>${match.has_telemetry ? `<span class="result-badge success">저장 완료</span><br><span class="result-caption">${escapeHtml(formatBytes(Number(match.telemetry_size_bytes || 0)))}</span>` : '<span class="result-badge warning">대기</span>'}</td>
          <td><button type="button" class="secondary" data-match-explorer-open="${attr(match.match_id)}">상세</button></td>
        </tr>
      `).join("") || '<tr><td colspan="6">조건에 맞는 저장 매치가 없습니다.</td></tr>';
      const end = Math.min(matchExplorerPage.total, matchExplorerPage.offset + (payload.matches || []).length);
      matchExplorerStatus.textContent = `${formatInteger(matchExplorerPage.total)}개 중 ${formatInteger(matchExplorerPage.offset + ((payload.matches || []).length ? 1 : 0))}-${formatInteger(end)} 표시`;
      matchExplorerPrev.disabled = !matchExplorerPage.has_previous;
      matchExplorerNext.disabled = !matchExplorerPage.has_next;
    }

    async function loadMatchExplorerDetail(matchId) {
      matchExplorerDetail.hidden = false;
      matchExplorerDetailTitle.textContent = "매치 상세를 불러오는 중입니다.";
      matchExplorerSummary.innerHTML = '<span class="status">참가자와 원본 이벤트를 확인하는 중입니다.</span>';
      const payload = await requestJson(`/matches/${encodeURIComponent(matchId)}/detail`, "GET");
      activeMatchExplorerDetail = payload.detail;
      renderMatchExplorerDetail();
      setMatchExplorerDetailView("participants");
      scrollWithinWorkspace(matchExplorerDetail, { behavior: "smooth", block: "start" });
    }

    function renderMatchExplorerDetail() {
      const detail = activeMatchExplorerDetail;
      if (!detail) return;
      const match = detail.match || {};
      const summary = detail.summary || {};
      const telemetry = detail.telemetry || {};
      matchExplorerDetailTitle.textContent = [
        formatKstShort(match.created_at_kst),
        match.map_label || match.map_name || "맵 미상",
        match.game_mode_label || match.game_mode || "모드 미상",
      ].join(" · ");
      matchExplorerSummary.innerHTML = `<div class="result-shell">
        ${resultMetricGrid([
          ["전체 / 사람 / 봇", `${formatInteger(summary.participants)} / ${formatInteger(summary.humans)} / ${formatInteger(summary.bots)}`],
          ["팀 / 등록 유저", `${formatInteger(summary.teams)}팀 / ${formatInteger(summary.registered_players)}명`],
          ["전체 킬 / 어시", `${formatInteger(summary.kills)} / ${formatInteger(summary.assists)}`],
          ["전체 피해", formatNumber(summary.damage_dealt, 1)],
          ["경기 시간", minutes(match.duration_seconds)],
          ["원본 이벤트", telemetry.available ? `${formatInteger(telemetry.event_count)}건 · ${formatBytes(Number(telemetry.size_bytes || 0))}` : "미저장"],
        ])}
        <p class="result-caption">${escapeHtml(match.match_id || "-")} · ${escapeHtml([match.team_mode_label, match.perspective_label, match.season_state_label].filter(Boolean).join(" · "))}</p>
      </div>`;
      matchExplorerParticipants.innerHTML = renderMatchParticipants(detail.participants || []);
      matchExplorerTeams.innerHTML = renderMatchTeams(detail.teams || []);
      const playerOptions = (detail.participants || []).map((player) => (
        `<option value="${attr(player.account_id)}">${escapeHtml(player.name || player.account_id)}${player.is_ai_or_bot ? " · 봇" : ""}${player.is_registered ? " · 등록" : ""}</option>`
      )).join("");
      matchEventFilterForm.elements.account_id.innerHTML = `<option value="">전체 참가자</option>${playerOptions}`;
      const teamIds = [...new Set((detail.teams || []).map((team) => team.team_id).filter((value) => value !== null && value !== undefined))].sort((left, right) => Number(left) - Number(right));
      matchEventFilterForm.elements.team_id.innerHTML = `<option value="">전체 팀</option>${teamIds.map((teamId) => `<option value="${attr(teamId)}">${formatInteger(teamId)}팀</option>`).join("")}`;
      matchExplorerReplay.disabled = !telemetry.available;
    }

    function renderMatchParticipants(participants) {
      const rows = participants.map((player) => `
        <tr${player.is_registered ? ' class="linked-row"' : ""}>
          <td><strong>${escapeHtml(player.name || "이름 미상")}</strong><br><span class="result-caption">${escapeHtml(compactIdentifier(player.account_id, 10, 6))}</span></td>
          <td>${player.is_ai_or_bot ? '<span class="result-badge warning">봇</span>' : '<span class="result-badge success">사람</span>'}${player.is_registered ? ' <span class="result-badge info">등록</span>' : ""}</td>
          <td>${formatInteger(player.team_id)}</td>
          <td>${player.win_place ? `${formatInteger(player.win_place)}위` : "-"}</td>
          <td>${formatInteger(player.kills)} / ${formatInteger(player.assists)}</td>
          <td>${formatNumber(player.damage_dealt, 1)}</td>
          <td>${escapeHtml(displayCode(player.death_type, "death_type"))}</td>
          <td><button type="button" class="secondary" data-match-event-player="${attr(player.account_id)}">이벤트</button></td>
        </tr>
      `).join("");
      return `<div class="table-scroll dense-table-wrap"><table class="detail-table"><thead><tr><th>플레이어</th><th>구분</th><th>팀</th><th>등수</th><th>킬 / 어시</th><th>피해</th><th>결과</th><th></th></tr></thead><tbody>${rows || '<tr><td colspan="8">참가자 정보가 없습니다.</td></tr>'}</tbody></table></div>`;
    }

    function renderMatchTeams(teams) {
      const rows = teams.map((team) => `
        <tr${team.registered_count ? ' class="linked-row"' : ""}>
          <td><strong>${team.team_id === null || team.team_id === undefined ? "팀 미상" : `${formatInteger(team.team_id)}팀`}</strong></td>
          <td>${team.win_place ? `${formatInteger(team.win_place)}위` : "-"}</td>
          <td>${formatInteger(team.member_count)}명<br><span class="result-caption">사람 ${formatInteger(team.human_count)} · 봇 ${formatInteger(team.bot_count)}</span></td>
          <td>${formatInteger(team.registered_count)}명</td>
          <td>${formatInteger(team.kills)} / ${formatInteger(team.assists)}</td>
          <td>${formatNumber(team.damage_dealt, 1)}</td>
          <td>${escapeHtml((team.members || []).map((member) => member.name || member.account_id).join(", "))}</td>
          <td>${team.team_id === null || team.team_id === undefined ? "" : `<button type="button" class="secondary" data-match-event-team="${attr(team.team_id)}">이벤트</button>`}</td>
        </tr>
      `).join("");
      return `<div class="table-scroll dense-table-wrap"><table class="detail-table"><thead><tr><th>팀</th><th>등수</th><th>구성</th><th>등록</th><th>킬 / 어시</th><th>피해</th><th>팀원</th><th></th></tr></thead><tbody>${rows || '<tr><td colspan="8">팀 정보가 없습니다.</td></tr>'}</tbody></table></div>`;
    }

    function setMatchExplorerDetailView(view) {
      activeMatchExplorerView = ["participants", "teams", "events"].includes(view) ? view : "participants";
      matchExplorerDetailViews.querySelectorAll("[data-match-detail-view]").forEach((button) => {
        button.classList.toggle("active", button.dataset.matchDetailView === activeMatchExplorerView);
      });
      matchExplorerParticipants.hidden = activeMatchExplorerView !== "participants";
      matchExplorerTeams.hidden = activeMatchExplorerView !== "teams";
      matchExplorerEvents.hidden = activeMatchExplorerView !== "events";
      if (activeMatchExplorerView === "events" && activeMatchExplorerDetail) {
        loadMatchExplorerEvents({ offset: 0 }).catch((error) => {
          matchEventStatus.textContent = `오류: ${error.message}`;
        });
      }
    }

    async function loadMatchExplorerEvents({ offset = 0 } = {}) {
      const matchId = activeMatchExplorerDetail?.match?.match_id;
      if (!matchId) return;
      const form = new FormData(matchEventFilterForm);
      const params = new URLSearchParams({
        limit: String(form.get("limit") || "100"),
        offset: String(Math.max(0, Number(offset || 0))),
        include_positions: String(Boolean(matchEventFilterForm.elements.include_positions.checked)),
      });
      for (const key of ["domain", "event_type", "account_id", "team_id", "search"]) {
        const value = String(form.get(key) || "").trim();
        if (value) params.set(key, value);
      }
      matchEventStatus.textContent = "전체 참가자 이벤트를 조회하는 중입니다.";
      const payload = await requestJson(`/matches/${encodeURIComponent(matchId)}/events?${params.toString()}`, "GET");
      matchEventPage = {
        total: Number(payload.total || 0),
        limit: Number(payload.limit || 100),
        offset: Number(payload.offset || 0),
        has_previous: Boolean(payload.has_previous),
        has_next: Boolean(payload.has_next),
      };
      renderMatchEventTypeOptions(payload.available_event_types || []);
      matchEventBody.innerHTML = (payload.events || []).map((event) => {
        const location = event.location && event.location.x !== null && event.location.y !== null
          ? `${formatNumber(Number(event.location.x) / 100, 0)}, ${formatNumber(Number(event.location.y) / 100, 0)}m`
          : "-";
        const elapsed = formatMatchElapsed(event.elapsed_seconds);
        return `<tr>
          <td>${escapeHtml(elapsed)}<br><span class="result-caption">${escapeHtml(formatKstShort(event.timestamp_kst))}</span></td>
          <td><strong>${escapeHtml(event.event_label || event.event_type)}</strong><br><span class="result-caption">${escapeHtml(event.domain || "-")}</span></td>
          <td>${escapeHtml(event.summary || "-")}${event.is_headshot ? ' <span class="result-badge warning">헤드샷</span>' : ""}</td>
          <td>${escapeHtml(location)}</td>
          <td><button type="button" class="secondary" data-match-event-open="${attr(event.sequence)}">상세</button></td>
        </tr>`;
      }).join("") || '<tr><td colspan="5">조건에 맞는 이벤트가 없습니다.</td></tr>';
      const end = Math.min(matchEventPage.total, matchEventPage.offset + (payload.events || []).length);
      matchEventStatus.textContent = `${formatInteger(matchEventPage.total)}건 중 ${formatInteger(matchEventPage.offset + ((payload.events || []).length ? 1 : 0))}-${formatInteger(end)} 표시 · 위치 주기 이벤트 ${matchEventFilterForm.elements.include_positions.checked ? "포함" : "제외"}`;
      matchEventPrev.disabled = !matchEventPage.has_previous;
      matchEventNext.disabled = !matchEventPage.has_next;
    }

    function renderMatchEventTypeOptions(eventTypes) {
      const select = matchEventFilterForm.elements.event_type;
      const selected = select.value;
      const domain = matchEventFilterForm.elements.domain.value;
      const visible = eventTypes.filter((event) => !domain || event.domain === domain);
      select.innerHTML = `<option value="">전체</option>${visible.map((event) => (
        `<option value="${attr(event.event_type)}">${escapeHtml(event.label || event.event_type)} · ${formatInteger(event.count)}건</option>`
      )).join("")}`;
      if (visible.some((event) => event.event_type === selected)) select.value = selected;
    }

    function formatMatchElapsed(value) {
      const seconds = Math.max(0, Number(value || 0));
      const hours = Math.floor(seconds / 3600);
      const minutesValue = Math.floor((seconds % 3600) / 60);
      const secondsValue = Math.floor(seconds % 60);
      return hours > 0
        ? `${String(hours).padStart(2, "0")}:${String(minutesValue).padStart(2, "0")}:${String(secondsValue).padStart(2, "0")}`
        : `${String(minutesValue).padStart(2, "0")}:${String(secondsValue).padStart(2, "0")}`;
    }

    async function openMatchEventDetail(sequence) {
      const matchId = activeMatchExplorerDetail?.match?.match_id;
      if (!matchId) return;
      matchEventDialogTitle.textContent = `이벤트 #${formatInteger(sequence)}`;
      matchEventDialogSummary.innerHTML = '<span class="status">원본 이벤트를 불러오는 중입니다.</span>';
      matchEventDialogRaw.textContent = "";
      matchEventDialog.showModal();
      try {
        const payload = await requestJson(`/matches/${encodeURIComponent(matchId)}/events/${encodeURIComponent(sequence)}`, "GET");
        const event = payload.event || {};
        const summary = event.summary || {};
        matchEventDialogTitle.textContent = `${summary.event_label || summary.event_type || "이벤트"} · #${formatInteger(sequence)}`;
        matchEventDialogSummary.innerHTML = resultMetricGrid([
          ["경과 시간", formatMatchElapsed(summary.elapsed_seconds)],
          ["발생 시각", formatKstShort(summary.timestamp_kst)],
          ["행위자", summary.actor?.name || summary.actor?.account_id || "-"],
          ["대상", summary.target?.name || summary.target?.account_id || "-"],
          ["무기·아이템", summary.weapon_name || summary.item_name || "-"],
          ["피해·거리", `${summary.damage === null || summary.damage === undefined ? "-" : formatNumber(summary.damage, 1)} / ${summary.distance_m === null || summary.distance_m === undefined ? "-" : `${formatNumber(summary.distance_m, 1)}m`}`],
        ]) + `<p class="result-caption">${escapeHtml(summary.summary || "")}</p>`;
        matchEventDialogRaw.textContent = JSON.stringify(event.raw_event || {}, null, 2);
      } catch (error) {
        matchEventDialogSummary.textContent = `오류: ${error.message}`;
      }
    }

    async function loadPlayerRanking(metric, shard, guildId, limit, minMatches = 1, activeOnly = true) {
      const params = new URLSearchParams({
        metric,
        shard,
        limit: String(limit || 10),
        min_matches: String(minMatches || 1),
        active_only: String(Boolean(activeOnly)),
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
      const selectedScope = ranking.global_scope
        ? (guildId ? `${discordGuildName(guildId)} · 전체 범위 설정` : "전체 서버")
        : discordGuildName(ranking.guild_id || guildId);
      activeRankingReport = { ...ranking, selected_scope: selectedScope };
      renderPlayerRanking();
    }

    function renderPlayerRanking() {
      const ranking = activeRankingReport;
      if (!ranking) {
        rankingBody.textContent = "조회 대기 중";
        return;
      }
      rankingViewControls?.querySelectorAll("[data-ranking-view]").forEach((button) => {
        button.classList.toggle("active", button.dataset.rankingView === activeRankingView);
      });
      const rows = (ranking.rows || []).map((row) => `
        <tr${row.rank <= 3 ? ' class="linked-row"' : ""}>
          <td><strong>${formatInteger(row.rank)}위</strong></td>
          <td><strong>${escapeHtml(row.player.current_name)}</strong></td>
          <td>${rankingScore(ranking.metric, row.score)}</td>
          <td>${formatInteger(row.match_count)}</td>
          <td>${formatInteger(row.wins)} · ${percent(row.win_rate)}<br><span class="result-caption">TOP10 ${percent(row.top10_rate)}</span></td>
          <td>${formatNumber(row.kd, 2)} / ${formatNumber(row.kda, 2)}</td>
          <td>${formatNumber(row.avg_kills, 2)} / ${formatNumber(row.avg_assists, 2)} / ${formatNumber(row.avg_dbnos_caused, 2)}</td>
          <td>${formatNumber(row.avg_damage_dealt, 1)} / ${formatNumber(row.avg_damage_taken, 1)}<br><span class="result-caption">교환 ${formatNumber(row.damage_ratio, 2)}</span></td>
          <td>${percent(row.accuracy)} / ${percent(row.headshot_hit_rate)}<br><span class="result-caption">캐릭터 ${formatInteger(row.character_hits)} · 차량 ${formatInteger(row.vehicle_hits)}</span></td>
          <td>${percent(row.fight_win_rate)}<br><span class="result-caption">경기당 ${formatNumber(row.avg_fights_per_match, 2)}회</span></td>
          <td>${minutes(row.avg_survival_seconds)} / ${distanceKm(row.avg_movement_distance_m)}</td>
        </tr>
      `).join("");
      const chart = recommendationChartRows(ranking.rows || [], {
        label: (row) => `${formatInteger(row.rank)}위 ${row.player.current_name}`,
        note: (row) => `${formatInteger(row.match_count)}경기 · ${formatInteger(row.wins)}치킨`,
        value: (row) => Number(row.score || 0),
        display: (row) => rankingScore(ranking.metric, row.score),
      });
      const content = activeRankingView === "chart"
        ? `<div class="metric-chart"><h3>${escapeHtml(ranking.metric_label)} 비교</h3>${chart}</div>`
        : `<div class="table-scroll"><table class="detail-table">
          <thead><tr><th>순위</th><th>닉네임</th><th>선택 점수</th><th>경기</th><th>치킨·승률</th><th>KD / KDA</th><th>경기당 킬/어시/기절</th><th>평균 준/받은 피해</th><th>명중/헤드샷</th><th>교전 승률</th><th>생존/이동</th></tr></thead>
          <tbody>${rows || `<tr><td colspan="11">조건을 충족한 랭킹 데이터가 없습니다.</td></tr>`}</tbody>
        </table></div>`;
      rankingBody.innerHTML = `<div class="result-shell">
        ${resultHeading(`${ranking.metric_label} 랭킹`, `${ranking.shard === "steam" ? "스팀" : ranking.shard === "kakao" ? "카카오" : ranking.shard} · ${ranking.selected_scope}`, `${formatInteger((ranking.rows || []).length)}명`)}
        ${resultMetricGrid([
          ["선택 지표", ranking.metric_label],
          ["최소 경기", `${formatInteger(ranking.min_matches)}경기`],
          ["등록 상태", ranking.active_only ? "수집 중인 유저" : "중지 유저 포함"],
          ["표시 인원", `${formatInteger((ranking.rows || []).length)}명`],
        ])}
        ${content}
        <p class="result-caption">헤드샷 명중 확률은 헤드샷 명중 ÷ 캐릭터 명중으로 계산해 차량 명중을 제외합니다. 전체 명중률은 캐릭터와 차량에 적중한 일반 탄환을 포함합니다.</p>
      </div>`;
    }

    function hitPartEntries(parts, rates = null) {
      const labels = {
        head: "머리",
        neck: "목",
        torso: "몸통",
        pelvis: "골반",
        arm: "팔",
        leg: "다리",
        none: "기타",
      };
      const entries = Object.entries(parts || {}).filter((entry) => Number(entry[1]) > 0);
      const total = entries.reduce((sum, entry) => sum + Number(entry[1] || 0), 0);
      return entries.map((entry) => {
        const rate = rates?.[entry[0]] ?? (total > 0 ? Number(entry[1]) / total : 0);
        return (labels[entry[0]] || entry[0]) + " " + formatInteger(entry[1]) + "회 · " + percent(rate);
      });
    }

    function hitPartsText(parts) {
      return hitPartEntries(parts).join(" · ") || "-";
    }

    function accuracyMetricText(value, metric) {
      if (!metric) return percent(value);
      const metricValue = metric.metric_value;
      if (metric.metric_kind === "estimated_hit_rate" && metricValue !== null && metricValue !== undefined) {
        return `추정 ${percent(metricValue)}`;
      }
      if (metric.metric_kind === "pellet_hits_per_shell" && metricValue !== null && metricValue !== undefined) {
        return `셸당 펠릿 ${formatNumber(metricValue, 2)}회`;
      }
      if (metric.metric_kind === "hit_events_per_attack" && metricValue !== null && metricValue !== undefined) {
        return `공격당 피격 ${formatNumber(metricValue, 2)}회`;
      }
      return "측정 불가";
    }

    function accuracyBreakdownText(value, breakdown) {
      if (!breakdown) return percent(value);
      const parts = [];
      if (breakdown.estimated_hit_rate !== null && breakdown.estimated_hit_rate !== undefined) {
        parts.push(`일반 탄환 추정 ${percent(breakdown.estimated_hit_rate)}`);
      } else if (Number(breakdown.single_projectile_attacks || 0) > 0) {
        parts.push("일반 탄환 측정 불가");
      }
      if (Number(breakdown.pellet_shells || 0) > 0 && breakdown.pellet_hits_per_shell !== null && breakdown.pellet_hits_per_shell !== undefined) {
        parts.push(`산탄 셸당 ${formatNumber(breakdown.pellet_hits_per_shell, 2)}회`);
      }
      if (Number(breakdown.unclassified_attacks || 0) > 0) {
        parts.push(`분류 제외 ${formatInteger(breakdown.unclassified_attacks)}회`);
      }
      return parts.join(" · ") || "측정 불가";
    }
    function percent(value) {
      return formatNumber(Number(value || 0) * 100, 1) + "%";
    }

    function characterHitCount(value) {
      const characterHits = Number(value?.character_hits || 0);
      const vehicleHits = Number(value?.vehicle_hits || 0);
      if (characterHits > 0 || vehicleHits > 0) return characterHits;
      return Number(value?.shots_hit || 0);
    }

    function hitTargetEvidence(value) {
      return `캐릭터 ${formatInteger(characterHitCount(value))}회 · 차량 ${formatInteger(value?.vehicle_hits || 0)}회`;
    }

    function formatNumber(value, maximumFractionDigits = 0) {
      if (value === null || value === undefined || value === "") return "-";
      const numeric = Number(value);
      if (!Number.isFinite(numeric)) return "-";
      if (activeNumberFormat === "plain") {
        return numeric.toLocaleString("ko-KR", {
          useGrouping: false,
          minimumFractionDigits: maximumFractionDigits,
          maximumFractionDigits,
        });
      }
      if (activeNumberFormat === "korean_units") {
        return formatKoreanNumber(numeric, maximumFractionDigits);
      }
      return numeric.toLocaleString("ko-KR", {
        minimumFractionDigits: maximumFractionDigits,
        maximumFractionDigits,
      });
    }

    function formatInteger(value) {
      return formatNumber(value, 0);
    }

    function formatKoreanInteger(value) {
      const numeric = Number(value);
      if (!Number.isFinite(numeric)) return "-";
      const sign = numeric < 0 ? "-" : "";
      let remaining = Math.abs(Math.trunc(numeric));
      if (remaining < 10000) return sign + remaining.toLocaleString("ko-KR");
      const units = ["", "만", "억", "조", "경"];
      const groups = [];
      let index = 0;
      while (remaining > 0 && index < units.length) {
        const group = remaining % 10000;
        if (group > 0) groups.unshift(`${group.toLocaleString("ko-KR")}${units[index]}`);
        remaining = Math.floor(remaining / 10000);
        index += 1;
      }
      return sign + groups.join(" ");
    }

    function formatKoreanNumber(value, maximumFractionDigits = 0) {
      const numeric = Number(value);
      if (!Number.isFinite(numeric)) return "-";
      const sign = numeric < 0 ? "-" : "";
      const fixed = Math.abs(numeric).toLocaleString("en-US", {
        useGrouping: false,
        minimumFractionDigits: maximumFractionDigits,
        maximumFractionDigits,
      });
      const [integerText, fractionText = ""] = fixed.split(".");
      const integerPart = Number(integerText);
      const integerDisplay = formatKoreanInteger(integerPart);
      return sign + integerDisplay + (fractionText ? "." + fractionText : "");
    }

    function displayCode(value, category = "") {
      if (value === null || value === undefined || value === "") return "-";
      const text = String(value);
      const labels = {
        shard: { steam: "스팀", kakao: "카카오", psn: "플레이스테이션", xbox: "엑스박스" },
        team_mode: { solo: "솔로", duo: "듀오", squad: "스쿼드", unknown: "알 수 없음" },
        perspective: { fpp: "1인칭", tpp: "3인칭", unknown: "알 수 없음" },
        match_type: {
          unknown: "알 수 없음",
          official: "일반 매치",
          competitive: "경쟁전",
          airoyale: "AI 배틀로얄",
          arcade: "아케이드",
          event: "이벤트 모드",
          trainingroom: "훈련장",
          custom: "사용자 지정 매치",
          tutorialatoz: "튜토리얼",
        },
        season_state: { progress: "진행 시즌", in_progress: "진행 시즌", closed: "종료 시즌", offseason: "비시즌", unknown: "알 수 없음" },
        game_mode: {
          solo: "솔로",
          "solo-fpp": "1인칭 솔로",
          duo: "듀오",
          "duo-fpp": "1인칭 듀오",
          squad: "스쿼드",
          "squad-fpp": "1인칭 스쿼드",
          "normal-squad": "일반 스쿼드",
          tdm: "팀 데스매치",
          ibr: "인텐스 배틀로얄",
          "sdm-fpp": "솔로 데스매치 (1인칭)",
        },
        map: {
          Baltic_Main: "에란겔 리마스터",
          Erangel_Main: "에란겔",
          Desert_Main: "미라마",
          Savage_Main: "사녹",
          DihorOtok_Main: "비켄디",
          Summerland_Main: "카라킨",
          Tiger_Main: "태이고",
          Chimera_Main: "파라모",
          Neon_Main: "론도",
          Kiki_Main: "데스턴",
          Heaven_Main: "헤이븐",
          Range_Main: "캠프 자칼",
          Boardwalk_Main: "보드워크",
          Italy_TDM_Main: "이탈리아 (팀 데스매치)",
        },
        vehicle: {
          BP_RoadGlideST_LGD_C: "CVO 로드 글라이드 ST",
          BP_RoadGlideST_ULT_C: "CVO 로드 글라이드 ST",
          TransportAircraft_Chimera_C: "헬리콥터",
          IBRTransportAircraft_C: "인텐스 배틀로얄 수송기",
          IBRTransportAircraft_Helicopter_C: "인텐스 배틀로얄 헬리콥터",
          BP_Dirtbike_C: "더트 바이크",
          MortarPawn_C: "박격포",
          BP_McLarenGT_St_white_C: "맥라렌 GT 스탠다드 (실리카 화이트)",
          BP_McLarenGT_St_black_C: "맥라렌 GT 스탠다드 (블랙)",
          BP_McLarenGT_Lx_Yellow_C: "맥라렌 GT 엘리트 (볼케이노 옐로우)",
        },
        item_category: { Equipment: "장비", Attachment: "부착물", Ammunition: "탄약", Use: "소모품", Weapon: "무기", None: "기타" },
        item_sub_category: {
          None: "기타", Throwable: "투척물", Main: "주무기", backpack: "배낭", Boost: "부스트", Heal: "회복",
          Vest: "조끼", Headgear: "헬멧", Sight: "조준경", Melee: "근접 무기", Handgun: "권총",
          Parachute: "낙하산", Ascender: "등강 장비", BlueChip: "블루칩", Fuel: "연료", Gadget: "전술 장비",
          CamoNetting: "위장막", Revive: "부활 장비",
        },
        weapon_family: { AR: "AR · 돌격소총", DMR: "DMR · 지정사수소총", SR: "SR · 저격소총", SMG: "SMG · 기관단총", LMG: "LMG · 경기관총", SG: "SG · 산탄총", HG: "권총", MELEE: "근접 무기", THROWABLE: "투척물" },
      };
      const exact = labels[category]?.[text];
      if (exact) return exact;
      if (category === "vehicle") {
        const patterns = [
          [/^BP_Snowmobile_\\d+_C$/, "스노우모빌"],
          [/^BP_Van_A_\\d+_C$/, "밴"],
          [/^BP_Scooter_\\d+_A_C$/, "스쿠터"],
          [/^BP_TukTukTuk_A_\\d+_C$/, "툭샤이"],
          [/^BP_Mirado_Open_\\d+_C$/, "미라도 (오픈탑)"],
          [/^BP_RoadGlideST_[A-Z0-9]+_C$/, "CVO 로드 글라이드 ST"],
          [/^BP_PanigaleV4S_(?:EP|LGD)\\d+_C$/, "두카티 파니갈레 V4 S"],
          [/^BP_McLarenGT_[A-Za-z0-9_]+_C$/, "맥라렌 GT"],
          [/^BP_Classic_\\d+_C$/, "클래식 차량"],
          [/^BP_Motorbike_\\d+(?:_SideCar)?(?:_Desert)?_C$/, "오토바이"],
          [/^Uaz(?:2|_[ABC]_\\d+(?:_esports)?)_C$/, "UAZ"],
        ];
        return patterns.find(([pattern]) => pattern.test(text))?.[1] || text;
      }
      return text;
    }

    function localizeStaticDisplayValues() {
      const selectors = {
        shard: 'select[name="shard"] option',
        team_mode: 'select[name="team_mode"] option',
        perspective: 'select[name="perspective"] option',
        match_type: 'select[name="match_type"] option',
        season_state: 'select[name="season_state"] option',
      };
      for (const [category, selector] of Object.entries(selectors)) {
        document.querySelectorAll(selector).forEach((option) => {
          if (!option.value) return;
          option.textContent = displayCode(option.value, category);
        });
      }
    }

    function applyDisplaySettings() {
      if (displaySettingsForm) displaySettingsForm.elements.number_format.value = activeNumberFormat;
      if (displayNumberPreview) displayNumberPreview.textContent = formatInteger(59452);
      localizeStaticDisplayValues();
    }

    function rankingScore(metric, value) {
      if (["win_rate", "top10_rate", "accuracy", "headshot_hit_rate", "headshot_rate", "fight_win_rate"].includes(metric)) {
        return percent(value);
      }
      if (metric === "avg_survival") return minutes(value);
      if (metric === "avg_movement") return distanceKm(value);
      if (metric === "vehicle_damage_dealt") return formatNumber(value, 1);
      if (["kda", "kd", "avg_damage", "avg_damage_taken", "damage_ratio", "avg_kills", "avg_assists", "avg_dbnos", "avg_fights"].includes(metric)) {
        return formatNumber(value, 2);
      }
      return formatInteger(value);
    }

    function minutes(value) {
      return value === null || value === undefined ? "-" : formatNumber(Number(value) / 60, 1) + "분";
    }

    function distanceKm(value) {
      return value === null || value === undefined ? "-" : formatNumber(Number(value) / 1000, 1) + "km";
    }

    function distanceM(value) {
      return value === null || value === undefined ? "-" : formatNumber(Number(value), 0) + "m";
    }

    function renderJobQueue(payload, tableBody, cardList, summaryElement) {
      const jobs = payload.jobs || [];
      summaryElement.textContent = `${queueSummaryText(payload.summary, jobs)} · 최근 ${formatInteger(jobs.length)}건 표시`;
      tableBody.innerHTML = jobs.length
        ? jobs.map((job) => `
          <tr>
            <td>${escapeHtml(displayCode(job.shard, "shard"))}</td>
            <td class="identifier" title="${attr(job.target_id || "")}">${escapeHtml(compactIdentifier(job.target_id))}</td>
            <td>${jobQueueStatusBadge(job)}</td>
            <td>${formatInteger(job.attempts || 0)}회</td>
            <td>${escapeHtml(formatKstShort(job.updated_at_kst || job.created_at_kst))}</td>
          </tr>
        `).join("")
        : `<tr><td colspan="5">표시할 작업이 없습니다.</td></tr>`;
      cardList.innerHTML = jobs.length
        ? jobs.map((job) => `
          <article class="dense-card">
            <div class="dense-card-head">
              <strong class="identifier" title="${attr(job.target_id || "")}">${escapeHtml(compactIdentifier(job.target_id))}</strong>
              ${jobQueueStatusBadge(job)}
            </div>
            <div class="dense-card-row"><span>플랫폼</span><strong>${escapeHtml(displayCode(job.shard, "shard"))}</strong></div>
            <div class="dense-card-row"><span>시도 횟수</span><strong>${formatInteger(job.attempts || 0)}회</strong></div>
            <div class="dense-card-row"><span>마지막 변경</span><strong>${escapeHtml(formatKstShort(job.updated_at_kst || job.created_at_kst))}</strong></div>
            ${job.next_run_at_kst ? `<div class="dense-card-row"><span>다음 시도</span><strong>${escapeHtml(formatKstShort(job.next_run_at_kst))}</strong></div>` : ""}
            ${job.last_error ? `<div class="dense-card-row"><span>최근 오류</span><strong title="${attr(job.last_error)}">${escapeHtml(String(job.last_error).slice(0, 80))}</strong></div>` : ""}
          </article>
        `).join("")
        : `<div class="dense-card"><span class="status">표시할 작업이 없습니다.</span></div>`;
    }

    function jobQueueStatusBadge(job) {
      if (String(job?.status || "").toLowerCase() !== "queued") {
        return jobStatusBadge(job?.status);
      }
      const attempts = Number(job?.attempts || 0);
      const nextRun = job?.next_run_at_kst
        ? new Date(String(job.next_run_at_kst).replace(" ", "T"))
        : null;
      if (nextRun && !Number.isNaN(nextRun.getTime()) && nextRun.getTime() > Date.now()) {
        return '<span class="status-badge warning">재시도 예약</span>';
      }
      return attempts > 0
        ? '<span class="status-badge warning">재시도 가능</span>'
        : '<span class="status-badge warning">처리 대기</span>';
    }

    async function loadJobs() {
      const response = await fetch("/jobs/matches?limit=20");
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || response.statusText);
      renderJobQueue(payload, jobsBody, jobsCards, jobsSummary);
    }

    async function loadTelemetryJobs() {
      const response = await fetch("/jobs/telemetry?limit=20");
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || response.statusText);
      renderJobQueue(payload, telemetryJobsBody, telemetryJobsCards, telemetryJobsSummary);
    }

    function renderPlayerIntelligenceAudit(audit) {
      const counts = audit.counts || {};
      const eventCatalog = audit.event_catalog || {};
      const sources = audit.item_source_totals || {};
      const translations = audit.translation_coverage || {};
      const checks = audit.checks || [];
      const checkRows = checks.map((check) => `<tr>
        <td><span class="alert-severity ${check.passed ? "alert-severity-ok" : "alert-severity-error"}">${check.passed ? "통과" : "실패"}</span></td>
        <td>${escapeHtml(check.label_ko || check.key)}</td>
        <td>${escapeHtml(check.expected)}</td>
        <td>${escapeHtml(check.actual)}</td>
        <td>${escapeHtml(check.detail_ko || "-")}</td>
      </tr>`).join("") || '<tr><td colspan="5">검사 항목 없음</td></tr>';
      const processorLabels = {
        combat: "전투",
        activity: "행동",
        items: "아이템",
        movement: "이동·좌표",
      };
      const parserRows = (audit.parser_versions || []).map((row) => `<tr>
        <td>${escapeHtml(processorLabels[row.processor_name] || row.processor_name)}</td><td class="identifier">${escapeHtml(row.parser_version)}</td>
        <td>${formatInteger(row.matches)}</td>
        <td>${formatInteger(row.player_matches)}</td>
      </tr>`).join("") || '<tr><td colspan="4">파서 상태 없음</td></tr>';
      const weaponTranslations = translations.weapons || {};
      const itemTranslations = translations.items || {};
      playerIntelligenceAuditStatus.textContent = `${audit.passed ? "전체 통과" : "확인 필요"} · ${formatKstShort(audit.generated_at_kst)}`;
      playerIntelligenceAuditBody.className = "";
      playerIntelligenceAuditBody.innerHTML = intelligenceKpis([
        ["원본 경기", "raw_matches", counts.raw_matches],
        ["분석 대상 경기", "analysis_raw_matches", `${formatInteger(counts.analysis_raw_matches || 0)} · 정책 제외 ${formatInteger(counts.excluded_raw_matches || 0)}`],
        ["검사 대상 경기·플레이어", "eligible_player_matches", counts.eligible_player_matches],
        ["처리 유예 중", "grace_player_matches", `${formatInteger(counts.grace_matches || 0)}경기 · ${formatInteger(counts.grace_player_matches || 0)}건`],
        ["마지막 분석 경기", "latest_match_started_at_kst", formatKstShort(counts.latest_match_started_at_kst)],
        ["마지막 텔레메트리 저장", "latest_raw_fetched_at_kst", formatKstShort(counts.latest_raw_fetched_at_kst)],
        ["전투 파서", "combat_matches", `${formatInteger(counts.combat_matches || 0)} / ${formatInteger(counts.eligible_matches || 0)}경기`],
        ["교전 승패", "fight_outcome_matches", `${formatInteger(counts.fight_outcome_matches || 0)} / ${formatInteger(counts.eligible_matches || 0)}경기`],
        ["2D 지도·타임라인", "map_snapshot_player_matches", `${formatInteger(counts.map_snapshot_player_matches || 0)} / ${formatInteger(counts.timeline_player_matches || 0)}`],
        ["지상 경로 없음", "unrenderable_player_matches", counts.unrenderable_player_matches],
        ["정규화 이벤트 타입", "normalized_type_count", `${formatInteger(eventCatalog.normalized_type_count || 0)} / ${formatInteger(eventCatalog.event_type_count || 0)}`],
        ["원본 전용 타입", "raw_only_type_count", eventCatalog.raw_only_type_count],
      ]) + `<div class="intelligence-data-section"><h3>정합성 검사</h3>
        <div class="table-scroll"><table class="data-quality-checks"><thead><tr><th>상태</th><th>검사</th><th>기대</th><th>실제</th><th>판정 근거</th></tr></thead><tbody>${checkRows}</tbody></table></div>
      </div><div class="intelligence-data-section"><h3>분석 대상 파서 버전</h3>
        <div class="table-scroll"><table class="intelligence-table"><thead><tr><th>처리기</th><th>버전</th><th>경기</th><th>경기·플레이어</th></tr></thead><tbody>${parserRows}</tbody></table></div>
      </div><div class="intelligence-data-section"><h3>번역 사전 커버리지</h3><div class="intelligence-metric-grid">
        <div class="intelligence-metric-row"><span>무기 코드</span><strong>${formatInteger(weaponTranslations.known_distinct_code_count || 0)} / ${formatInteger(weaponTranslations.distinct_code_count || 0)}</strong></div>
        <div class="intelligence-metric-row"><span>무기 미번역 행</span><strong>${formatInteger(weaponTranslations.unknown_row_count || 0)}</strong></div>
        <div class="intelligence-metric-row"><span>아이템 코드</span><strong>${formatInteger(itemTranslations.known_distinct_code_count || 0)} / ${formatInteger(itemTranslations.distinct_code_count || 0)}</strong></div>
        <div class="intelligence-metric-row"><span>아이템 미번역 행</span><strong>${formatInteger(itemTranslations.unknown_row_count || 0)}</strong></div>
      </div></div><div class="intelligence-data-section"><h3>분석 대상 아이템 출처 합계</h3><div class="intelligence-metric-grid">
        <div class="intelligence-metric-row"><span>루트박스 획득</span><strong>${formatInteger(sources.loot_box_pickups)}</strong></div>
        <div class="intelligence-metric-row"><span>보급 획득</span><strong>${formatInteger(sources.carepackage_pickups)}</strong></div>
        <div class="intelligence-metric-row"><span>커스텀 패키지 획득</span><strong>${formatInteger(sources.custom_package_pickups)}</strong></div>
        <div class="intelligence-metric-row"><span>트렁크 획득 / 보관</span><strong>${formatInteger(sources.vehicle_trunk_pickups)} / ${formatInteger(sources.vehicle_trunk_puts)}</strong></div>
      </div></div>`;
    }

    async function runPlayerIntelligenceAudit() {
      playerIntelligenceAuditRun.disabled = true;
      playerIntelligenceAuditStatus.textContent = "검사 중";
      try {
        const payload = await requestJson("/operations/player-intelligence-audit", "GET");
        renderPlayerIntelligenceAudit(payload.player_intelligence_audit || {});
      } finally {
        playerIntelligenceAuditRun.disabled = false;
      }
    }

    function renderOperationalDrillDetail(record) {
      const report = record.report || {};
      const checks = report.checks || [];
      operationalDrillDetail.innerHTML = [
        `<strong>#${escapeHtml(record.id)} ${escapeHtml(record.mode)} / ${escapeHtml(record.status)}</strong>`,
        `계약: ${escapeHtml(record.contract_version || "-")} · 반복: ${formatInteger(record.requested_cycles || 0)} · 시간: ${formatNumber(record.duration_seconds || 0, 3)}초`,
        checks.length
          ? checks.map((check) => `${check.passed ? "통과" : "실패"} · ${escapeHtml(check.name)} · ${escapeHtml(check.summary || "-")}<br><code>${escapeHtml(JSON.stringify(check.metrics || {}))}</code>`).join("<br>")
          : "저장된 체크가 없습니다.",
      ].join("<br>");
    }

    function renderOperationalDrills(records) {
      operationalDrillRecords = records || [];
      operationalDrillsBody.innerHTML = operationalDrillRecords.map((record) => `
        <tr>
          <td>${escapeHtml(record.id)}</td>
          <td>${escapeHtml(record.mode)}</td>
          <td><strong>${escapeHtml(record.status)}</strong></td>
          <td>${escapeHtml(record.finished_at_kst || record.created_at_kst || "-")}</td>
          <td>${formatNumber(record.duration_seconds || 0, 3)}초</td>
          <td>${formatInteger(record.passed_check_count)}/${formatInteger(record.check_count)}</td>
          <td><button class="secondary" type="button" data-operational-drill-id="${attr(record.id)}">상세</button></td>
        </tr>
      `).join("") || `<tr><td colspan="7">저장된 운영 훈련이 없습니다.</td></tr>`;
      operationalDrillsStatus.textContent = `${formatInteger(operationalDrillRecords.length)}개 이력`;
    }

    async function loadOperationalDrills() {
      const response = await fetch("/operations/drills?limit=20");
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || response.statusText);
      }
      const payload = await response.json();
      renderOperationalDrills(payload.operational_drill_runs || []);
    }

    async function runOperationalDrill(event) {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const mode = String(form.get("mode") || "simulated");
      const cycles = Number(form.get("cycles") || 3);
      operationalDrillsStatus.textContent = `${mode} 실행 중`;
      const payload = await postJson("/operations/drills", { mode, cycles });
      const report = payload.operational_drill;
      operationalDrillsStatus.textContent = `#${formatInteger(payload.run_id)} ${report.passed ? "통과" : "실패"} · ${formatInteger(report.passed_check_count)}/${formatInteger(report.check_count)}`;
      await loadOperationalDrills();
      const saved = operationalDrillRecords.find((record) => String(record.id) === String(payload.run_id));
      if (saved) renderOperationalDrillDetail(saved);
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
        const limit = Number(options.limit || form.get("limit") || workerRunPage.limit || 20);
        const offset = Math.max(0, Number(options.offset ?? workerRunPage.offset ?? 0));
        const params = new URLSearchParams({
          worker_name: selectedWorker === "all" ? "" : selectedWorker,
          status: selectedStatus,
          created_from_kst: createdFrom,
          created_to_kst: createdTo,
          limit: String(limit),
          offset: String(offset),
        });
        const payload = await requestJson(`/workers/runs?${params.toString()}`, "GET");
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
        workerRunsStatus.textContent = `오류: ${error.message}`;
        workerRunsBody.innerHTML = `<tr><td colspan="6">오류: ${escapeHtml(error.message)}</td></tr>`;
        workerRunsCards.innerHTML = `<div class="dense-card"><span class="status">오류: ${escapeHtml(error.message)}</span></div>`;
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
        limit: Number(page.limit || workerRunPage.limit || 20),
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
        workerRunFilterForm.elements.limit.value = String(workerRunPage.limit || 20);
      }
      const start = runs.length ? workerRunPage.offset + 1 : 0;
      const end = runs.length ? workerRunPage.offset + runs.length : 0;
      const statusLabel = workerRunPage.status === "all"
        ? "전체"
        : jobStatusMeta(workerRunPage.status)[0];
      const rangeLabels = {
        custom: "직접 지정",
        last_1h: "최근 1시간",
        last_24h: "최근 24시간",
        today: "오늘",
        yesterday: "어제",
        last_7d: "최근 7일",
      };
      workerRunsStatus.textContent = [
        `전체 ${formatInteger(workerRunPage.total)}건 중 ${formatInteger(start)}-${formatInteger(end)}`,
        `작업 ${workerRunPage.worker_name ? workerNameLabel(workerRunPage.worker_name) : "전체"}`,
        `상태 ${statusLabel}`,
        `기간 ${rangeLabels[workerRunPage.quick_range] || "직접 지정"}`,
        `시각 ${workerRunDateRangeLabel(workerRunPage.created_from_kst, workerRunPage.created_to_kst)}`,
      ].join(" · ");
      workerRunsPrev.disabled = !workerRunPage.has_previous;
      workerRunsNext.disabled = !workerRunPage.has_next;
      workerRunsBody.innerHTML = runs.length
        ? runs.map((run) => `
            <tr>
              <td>${escapeHtml(workerNameLabel(run.worker_name))}</td>
              <td>${jobStatusBadge(run.status)}${run.error_count ? ` <span class="status">오류 ${formatInteger(run.error_count)}건</span>` : ""}</td>
              <td>${escapeHtml(formatKstShort(run.finished_at_kst || run.created_at_kst))}</td>
              <td>${run.duration_seconds === null || run.duration_seconds === undefined ? "-" : formatNumber(run.duration_seconds, 2) + "초"}</td>
              <td>${workerRunSummary(run)}</td>
              <td><button class="secondary" type="button" data-worker-run-detail-id="${attr(run.id)}">상세</button></td>
            </tr>
          `).join("")
        : `<tr><td colspan="6">표시할 작업 이력이 없습니다.</td></tr>`;
      workerRunsCards.innerHTML = runs.length
        ? runs.map((run) => `
          <article class="dense-card">
            <div class="dense-card-head">
              <strong>${escapeHtml(workerNameLabel(run.worker_name))} #${escapeHtml(run.id)}</strong>
              ${jobStatusBadge(run.status)}
            </div>
            <div class="dense-card-row"><span>완료 시각</span><strong>${escapeHtml(formatKstShort(run.finished_at_kst || run.created_at_kst))}</strong></div>
            <div class="dense-card-row"><span>소요 시간</span><strong>${run.duration_seconds === null || run.duration_seconds === undefined ? "-" : formatNumber(run.duration_seconds, 2) + "초"}</strong></div>
            <div class="dense-card-row"><span>요약</span><strong>${workerRunSummary(run)}</strong></div>
            <div class="dense-card-actions"><button class="secondary" type="button" data-worker-run-detail-id="${attr(run.id)}">상세</button></div>
          </article>
        `).join("")
        : `<div class="dense-card"><span class="status">표시할 작업 이력이 없습니다.</span></div>`;
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
      if (fromText === "-" && toText === "-") return "전체";
      return `${fromText}..${toText}`;
    }

    function workerRunSummary(run) {
      if (run.last_error) {
        return escapeHtml(run.last_error);
      }
      const summary = run.summary || {};
      if (run.worker_name === "collector") {
        return escapeHtml([
          `매치 대기 ${formatInteger(summary.collection?.queued_match_jobs)}`,
          `매치 저장 ${formatInteger(summary.match_jobs?.stored_matches)}`,
          `텔레메트리 저장 ${formatInteger(summary.telemetry_jobs?.stored_telemetry)}`,
        ].join(" · "));
      }
      return escapeHtml([
        `전투 ${formatInteger(summary.combat?.parsed_payloads)}`,
        `아이템 ${formatInteger(summary.items?.parsed_payloads)}`,
        `이동 ${formatInteger(summary.movement?.parsed_payloads)}`,
        `지도 ${formatInteger(summary.map_snapshots?.generated_snapshots)}`,
        `타임라인 ${formatInteger(summary.replay_timelines?.generated_timelines)}`,
      ].join(" · "));
    }

    async function loadWorkerRunDetail(runId, options = {}) {
      workerRunDetail.innerHTML = `<div class="status">작업 #${escapeHtml(runId)} 상세 정보를 불러오는 중...</div>`;
      const payload = await requestJson(`/workers/runs/${encodeURIComponent(runId)}`, "GET");
      if (payload.detail) throw new Error(payload.detail);
      if (!payload.run) throw new Error("작업 이력이 반환되지 않았습니다.");
      renderWorkerRunDetail(payload.run);
      if (options.updateUrl !== false) {
        updateWorkerRunDetailUrl(payload.run.id);
      }
      if (options.scroll) {
        scrollWithinWorkspace(workerRunDetail, { behavior: "auto", block: "start" });
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
        : `<tr><td colspan="2">저장된 요약 지표가 없습니다.</td></tr>`;
      const errorRows = errors.length
        ? errors.map((error, index) => `
          <tr>
            <th>${index + 1}</th>
            <td><pre style="white-space: pre-wrap; margin: 0;">${escapeHtml(error)}</pre></td>
          </tr>
        `).join("")
        : `<tr><td colspan="2">저장된 오류가 없습니다.</td></tr>`;
      workerRunDetail.innerHTML = `
        <div class="recommendation-line">
          <strong>자동 작업 #${escapeHtml(run.id)} 상세</strong>
          <div class="actions">
            <button class="secondary" type="button" data-copy-worker-run-link="${attr(run.id)}">링크 복사</button>
          </div>
        </div>
        <div class="status" style="margin-top: 6px;">${escapeHtml(workerRunDetailUrl(run.id))}</div>
        <div class="grid" style="margin-top: 10px;">
          ${cell("작업 종류", escapeHtml(workerNameLabel(run.worker_name)))}
          ${cell("상태", jobStatusBadge(run.status))}
          ${cell("완료 시각 (KST)", escapeHtml(formatKstShort(run.finished_at_kst || run.created_at_kst)))}
          ${cell("소요 시간", run.duration_seconds === null || run.duration_seconds === undefined ? "-" : formatNumber(run.duration_seconds, 2) + "초")}
        </div>
        <table class="detail-table">
          <thead><tr><th>요약 지표</th><th>값</th></tr></thead>
          <tbody>${metricRows}</tbody>
        </table>
        <table class="detail-table">
          <thead><tr><th>#</th><th>저장된 오류</th></tr></thead>
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
      const limit = workerRunUrlBoundedNumber(params.get("worker_run_limit"), 20, 1, 200);
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
      const limit = Number(form.get("limit") || workerRunPage.limit || 20);
      url.searchParams.delete("worker_run_id");
      url.searchParams.delete("worker_run");
      url.searchParams.set("worker_run_worker", worker === "all" ? "all" : worker);
      url.searchParams.set("worker_run_status", status);
      url.searchParams.set("worker_run_range", "custom");
      url.searchParams.set("worker_run_from", workerRunDateTimeInputValue(createdFrom));
      url.searchParams.set("worker_run_to", workerRunDateTimeInputValue(createdTo));
      url.searchParams.set("worker_run_limit", String(limit || 20));
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
          return [{ key: metricKey, value: `${formatInteger(value.length)}개 항목` }];
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
      if (typeof value === "number") {
        return Number.isInteger(value) ? formatInteger(value) : formatNumber(value, 3);
      }
      return String(value);
    }

    async function loadReplayArtifacts(options = {}) {
      const form = new FormData(replayArtifactListForm);
      const matchId = options.match_id !== undefined ? options.match_id : replayArtifactFilter.match_id;
      const accountId = options.account_id !== undefined
        ? options.account_id
        : (replayArtifactFilter.account_id || String(form.get("account_id") || ""));
      const artifactType = options.artifact_type !== undefined
        ? options.artifact_type
        : String(form.get("artifact_type") || "");
      const limit = Number(options.limit || form.get("limit") || 20);
      const params = new URLSearchParams({ artifact_type: artifactType, limit: String(limit) });
      if (matchId) params.set("match_id", matchId);
      if (accountId) params.set("account_id", accountId);
      replayArtifactsStatus.textContent = "저장 목록을 불러오는 중";
      const response = await fetch(`/replay/artifacts?${params.toString()}`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || response.statusText);
      const artifacts = payload.artifacts || [];
      replayArtifactsStatus.textContent = [
        `${formatInteger(artifacts.length)}개 표시`,
        accountId ? `유저 ${registeredPlayers.find((player) => player.account_id === accountId)?.current_name || compactIdentifier(accountId)}` : "전체 등록 유저",
        artifactType ? artifactTypeLabel(artifactType) : "전체 파일",
      ].join(" · ");
      replayArtifactsBody.innerHTML = artifacts.length
        ? artifacts.map((artifact) => `
          <tr>
            <td><strong>${escapeHtml(artifact.player_name || compactIdentifier(artifact.account_id))}</strong></td>
            <td>${escapeHtml(formatKstShort(artifact.match_created_at_kst))}</td>
            <td>${escapeHtml(artifactTypeLabel(artifact.artifact_type))}</td>
            <td>${escapeHtml(displayCode(artifact.map_name, "map"))}<br><span class="status">${escapeHtml(displayCode(artifact.game_mode, "game_mode"))}</span></td>
            <td class="identifier" title="${attr(artifact.match_id || "")}">${escapeHtml(compactIdentifier(artifact.match_id))}</td>
            <td>${escapeHtml(formatKstShort(artifact.generated_at_kst))}</td>
            <td>${escapeHtml(formatBytes(artifact.size_bytes || 0))}</td>
            <td>
              <div class="actions">
                ${canPlayTimelineArtifact(artifact) ? `<button type="button" data-load-timeline="${attr(artifact.id)}" data-load-match-id="${attr(artifact.match_id)}" data-load-account-id="${attr(artifact.account_id)}">전체 경기 재생</button>` : ""}
                ${artifact.artifact_type === "timeline" && !canPlayTimelineArtifact(artifact) ? `<span class="status-badge warning" title="${attr(artifact.renderer_version || "버전 정보 없음")}">재생성 필요</span>` : ""}
                <a href="${attr(artifact.view_url)}" target="_blank" rel="noreferrer">열기</a>
              </div>
            </td>
          </tr>
        `).join("")
        : `<tr><td colspan="8">조건에 맞는 저장 파일이 없습니다.</td></tr>`;
      replayArtifactsCards.innerHTML = artifacts.length
        ? artifacts.map((artifact) => `
          <article class="dense-card">
            <div class="dense-card-head">
              <strong>${escapeHtml(artifact.player_name || compactIdentifier(artifact.account_id))}</strong>
              <span class="status-badge info">${escapeHtml(artifactTypeLabel(artifact.artifact_type))}</span>
            </div>
            <div class="dense-card-row"><span>경기</span><strong>${escapeHtml(formatKstShort(artifact.match_created_at_kst))}</strong></div>
            <div class="dense-card-row"><span>맵 / 모드</span><strong>${escapeHtml(displayCode(artifact.map_name, "map"))} · ${escapeHtml(displayCode(artifact.game_mode, "game_mode"))}</strong></div>
            <div class="dense-card-row"><span>매치 ID</span><strong class="identifier" title="${attr(artifact.match_id || "")}">${escapeHtml(compactIdentifier(artifact.match_id))}</strong></div>
            <div class="dense-card-row"><span>생성 / 크기</span><strong>${escapeHtml(formatKstShort(artifact.generated_at_kst))} · ${escapeHtml(formatBytes(artifact.size_bytes || 0))}</strong></div>
            <div class="dense-card-actions">
              ${canPlayTimelineArtifact(artifact) ? `<button type="button" data-load-timeline="${attr(artifact.id)}" data-load-match-id="${attr(artifact.match_id)}" data-load-account-id="${attr(artifact.account_id)}">전체 경기 재생</button>` : ""}
              ${artifact.artifact_type === "timeline" && !canPlayTimelineArtifact(artifact) ? `<span class="status-badge warning" title="${attr(artifact.renderer_version || "버전 정보 없음")}">재생성 필요</span>` : ""}
              <a href="${attr(artifact.view_url)}" target="_blank" rel="noreferrer">열기</a>
            </div>
          </article>
        `).join("")
        : `<div class="dense-card"><span class="status">조건에 맞는 저장 파일이 없습니다.</span></div>`;
    }

    const flightPathColors = ["#46d2aa", "#f0d479", "#ff8a65", "#70b7ff", "#d0a7ff", "#9ccc65"];

    async function loadFlightPathMapCatalog() {
      const selected = flightPathMapFilter.value;
      const response = await fetch("/map-regions");
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || response.statusText);
      const maps = (payload.map_region_catalog?.maps || [])
        .filter((item) => item.world_size_cm && item.canonical_map_name)
        .sort((left, right) => String(left.map_name_ko).localeCompare(String(right.map_name_ko)));
      flightPathMapFilter.innerHTML = '<option value="">전체 맵</option>' + maps.map((item) => (
        '<option value="' + attr(item.map_name) + '">' + escapeHtml(item.map_name_ko || item.map_name) + '</option>'
      )).join("");
      if ([...flightPathMapFilter.options].some((option) => option.value === selected)) {
        flightPathMapFilter.value = selected;
      }
    }

    function flightPathClusterDetail(cluster) {
      if (!cluster) return "항로를 선택하면 빈도와 방향 근거가 표시됩니다.";
      const offset = Number(cluster.offset_from_center_m || 0);
      const offsetText = Math.abs(offset) < 1
        ? "맵 중심 통과"
        : `${formatNumber(Math.abs(offset), 0)}m ${offset > 0 ? "우측" : "좌측"}`;
      return `
        <strong>${escapeHtml(cluster.map_name_ko)} · ${escapeHtml(cluster.dominant_direction_ko)} 진행</strong>
        ${formatInteger(cluster.route_count)}회 / 이 맵 ${formatInteger(cluster.map_route_count)}회 · 출현 ${percent(cluster.map_share)}
        · 주 방향 일치 ${percent(cluster.dominant_direction_share)}<br>
        물리 각도 ${formatNumber(cluster.physical_angle_degrees, 1)}도 · ${escapeHtml(offsetText)}
        · 평균 관측 ${formatNumber(cluster.avg_observed_length_m, 0)}m / ${formatNumber(cluster.avg_sample_count, 1)}개 표본<br>
        기간 ${escapeHtml(formatKstShort(cluster.first_seen_at_kst))} ~ ${escapeHtml(formatKstShort(cluster.last_seen_at_kst))}
      `;
    }

    function selectFlightPathCluster(clusterId) {
      const line = flightPathResult.querySelector('[data-flight-line="' + CSS.escape(clusterId) + '"]');
      const row = flightPathResult.querySelector('[data-flight-row="' + CSS.escape(clusterId) + '"]');
      flightPathResult.querySelectorAll("[data-flight-line]").forEach((item) => item.classList.toggle("active", item === line));
      flightPathResult.querySelectorAll("[data-flight-row]").forEach((item) => item.classList.toggle("active", item === row));
      const detail = flightPathResult.querySelector("#flightPathDetail");
      const cluster = (flightPathResult._report?.clusters || []).find((item) => item.cluster_id === clusterId);
      if (detail) detail.innerHTML = flightPathClusterDetail(cluster);
      row?.scrollIntoView({ block: "nearest" });
    }

    function renderFlightPathMap(report, mapName) {
      const image = flightPathResult.querySelector("#flightPathMapImage");
      const overlay = flightPathResult.querySelector("#flightPathOverlay");
      const list = flightPathResult.querySelector("#flightPathList");
      if (!image || !overlay || !list) return;
      const clusters = (report.clusters || []).filter((item) => item.map_name === mapName);
      image.src = mapName ? "/replay/map-assets/" + encodeURIComponent(mapName) : "";
      image.alt = (report.maps || []).find((item) => item.map_name === mapName)?.map_name_ko + " 비행기 항로 지도";
      overlay.innerHTML = `
        <defs>
          <marker id="flightArrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto" markerUnits="strokeWidth">
            <path d="M0,0 L7,3.5 L0,7 Z" fill="#ffffff"></path>
          </marker>
        </defs>
        ${clusters.map((cluster, index) => {
          const color = flightPathColors[index % flightPathColors.length];
          const width = 2 + 8 * Math.sqrt(Math.max(0, Number(cluster.map_share || 0)));
          const opacity = 0.38 + 0.58 * Math.sqrt(Math.max(0, Number(cluster.map_share || 0)));
          return `<line class="flight-route-line" tabindex="0" role="button"
            x1="${Number(cluster.start_x_pct) * 1000}" y1="${Number(cluster.start_y_pct) * 1000}"
            x2="${Number(cluster.end_x_pct) * 1000}" y2="${Number(cluster.end_y_pct) * 1000}"
            stroke="${color}" stroke-width="${width.toFixed(2)}" opacity="${opacity.toFixed(3)}"
            marker-end="url(#flightArrow)" data-flight-line="${attr(cluster.cluster_id)}"
          ><title>#${formatInteger(index + 1)} · ${escapeHtml(cluster.dominant_direction_ko)} · ${formatInteger(cluster.route_count)}회 (${percent(cluster.map_share)})</title></line>`;
        }).join("")}
      `;
      list.innerHTML = clusters.map((cluster, index) => {
        const color = flightPathColors[index % flightPathColors.length];
        return `
          <button type="button" class="flight-path-row" data-flight-row="${attr(cluster.cluster_id)}">
            <span class="flight-path-rank" style="color:${color}">#${formatInteger(index + 1)}</span>
            <span><strong>${escapeHtml(cluster.dominant_direction_ko)} 진행 · 중심 간격 ${formatNumber(Math.abs(cluster.offset_from_center_m), 0)}m</strong><small>${formatInteger(cluster.route_count)}회 · 주 방향 ${percent(cluster.dominant_direction_share)} · 평균 ${formatNumber(cluster.avg_sample_count, 1)}표본</small></span>
            <span class="flight-path-share">${percent(cluster.map_share)}</span>
          </button>
        `;
      }).join("") || '<div class="status">선택한 맵에 조건을 충족한 항로가 없습니다.</div>';
      flightPathResult.querySelectorAll("[data-flight-line]").forEach((line) => {
        line.addEventListener("click", () => selectFlightPathCluster(line.dataset.flightLine || ""));
        line.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            selectFlightPathCluster(line.dataset.flightLine || "");
          }
        });
      });
      flightPathResult.querySelectorAll("[data-flight-row]").forEach((row) => {
        row.addEventListener("click", () => selectFlightPathCluster(row.dataset.flightRow || ""));
      });
      const detail = flightPathResult.querySelector("#flightPathDetail");
      if (detail) detail.innerHTML = flightPathClusterDetail(clusters[0]);
      if (clusters[0]) selectFlightPathCluster(clusters[0].cluster_id);
    }

    function renderFlightPathReport(report) {
      flightPathResult._report = report;
      const maps = report.maps || [];
      const initialMap = report.filters?.map_name || maps[0]?.map_name || "";
      const recentRows = (report.recent_routes || []).map((route) => `
        <tr>
          <td>${escapeHtml(formatKstShort(route.created_at_kst))}</td>
          <td>${escapeHtml(route.map_name_ko)}</td>
          <td>${escapeHtml(route.travel_direction_ko)}</td>
          <td>${formatInteger(route.sample_count)}개</td>
          <td>${formatNumber(route.observed_length_m, 0)}m</td>
          <td class="identifier" title="${attr(route.match_id)}">${escapeHtml(compactIdentifier(route.match_id))}</td>
        </tr>
      `).join("");
      flightPathResult.hidden = false;
      flightPathResult.innerHTML = `<div class="result-shell">
        ${resultHeading("비행기 동선 빈도", "완료 경기 · KST · 맵 경계까지 연장", `${formatInteger(report.analyzed_route_count)}개 항로`)}
        ${resultMetricGrid([
          ["분석 항로", `${formatInteger(report.analyzed_route_count)}개`],
          ["맵", `${formatInteger(maps.length)}개`],
          ["항로 군집", `${formatInteger(report.available_cluster_count)}개`],
          ["제외", `${formatInteger(report.rejected_route_count)}개`],
          ["군집 기준", `${formatNumber(report.angle_bin_degrees, 0)}도 · ${formatNumber(report.offset_bin_m, 0)}m`],
        ])}
        <div class="flight-path-layout">
          <div class="drop-map-panel">
            <div class="drop-map-toolbar">
              <h3>빈도 지도</h3>
              <label>표시할 맵<select id="flightPathMapSelect">${maps.map((item) => `<option value="${attr(item.map_name)}"${item.map_name === initialMap ? " selected" : ""}>${escapeHtml(item.map_name_ko)} · ${formatInteger(item.route_count)}회</option>`).join("")}</select></label>
            </div>
            <div class="flight-path-map-stage">
              <img id="flightPathMapImage" alt="비행기 항로 지도">
              <svg id="flightPathOverlay" viewBox="0 0 1000 1000" aria-label="빈도별 비행기 항로"></svg>
            </div>
            <div class="flight-path-detail" id="flightPathDetail">항로를 선택하세요.</div>
          </div>
          <div>
            <h3>선택 맵 항로 순위</h3>
            <div class="flight-path-list" id="flightPathList"></div>
          </div>
        </div>
        <details class="result-disclosure">
          <summary>최근 원본 항로 · ${formatInteger(report.recent_routes?.length || 0)}개</summary>
          <div class="table-scroll"><table><thead><tr><th>KST</th><th>맵</th><th>진행 방향</th><th>표본</th><th>관측 길이</th><th>매치</th></tr></thead><tbody>${recentRows || '<tr><td colspan="6">원본 항로가 없습니다.</td></tr>'}</tbody></table></div>
        </details>
        <p class="result-caption">${escapeHtml(report.method_ko)} 방향이 반대여도 같은 물리 항로로 묶고, 주 운항 방향은 별도로 표시합니다.</p>
      </div>`;
      const mapSelect = flightPathResult.querySelector("#flightPathMapSelect");
      mapSelect?.addEventListener("change", () => renderFlightPathMap(report, mapSelect.value));
      renderFlightPathMap(report, initialMap);
    }

    async function loadFlightPathAnalysis() {
      const form = new FormData(flightPathForm);
      const accountId = String(form.get("account_id") || "");
      if (accountId && !form.get("shard")) {
        const player = registeredPlayers.find((item) => item.account_id === accountId);
        if (player) form.set("shard", player.shard);
      }
      const params = new URLSearchParams();
      for (const [key, value] of form.entries()) {
        const text = String(value || "").trim();
        if (text) params.set(key, text);
      }
      flightPathStatus.textContent = "저장된 항로를 분석하는 중";
      const response = await fetch("/analytics/flight-paths?" + params.toString());
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || response.statusText);
      renderFlightPathReport(payload.flight_paths);
      flightPathStatus.textContent = `분석 완료 · ${formatInteger(payload.flight_paths.analyzed_route_count)}개 항로 · ${formatInteger(payload.flight_paths.available_cluster_count)}개 군집`;
    }

    function clearReplayTimeline(message = "등록 유저를 선택한 뒤 경기를 불러오세요.") {
      pauseReplay();
      activeReplayPlayer = null;
      replayTimelineArtifacts = [];
      activeTimeline = null;
      activeTimelineArtifact = null;
      activeTimelineEvents = [];
      activeTimelineVisibleEvents = [];
      activeTimelineSelectedEventId = null;
      activeTimelineCurrentEventId = null;
      activeTimelineDetailKey = "";
      activeTimelineDuration = 0;
      activeTimelineTime = 0;
      replayPinnedMap = null;
      replayPinnedEventId = null;
      timelineActorFilter.value = "focus";
      timelineParticipantSearch.value = "";
      timelineEventTypeFilter.value = "all";
      timelineFollowEvents.checked = true;
      timelineSelect.disabled = true;
      timelineSelect.innerHTML = '<option value="">기준 유저를 선택하세요</option>';
      timelineScrubber.max = "0";
      timelineScrubber.value = "0";
      timelineClock.textContent = "0.0초";
      replayPlayerStatus.textContent = message;
      renderTimelineActorFilter();
      renderTimelineQuickEvents();
      renderTimelineTeamList();
      renderTimelineEventList();
      renderTimelineEventDetail(null);
      renderTimelineNowEvent(null);
      drawEmptyReplayCanvas();
    }

    async function loadReplayTimelinesForPlayer(player, preferredMatchId = "") {
      activeReplayPlayer = player;
      timelinePlayerInput.value = player.current_name;
      timelinePlayerInput.dataset.accountId = player.account_id;
      timelinePlayerForm.elements.shard.value = player.shard;
      replayPlayerStatus.textContent = `${player.current_name}의 종료된 경기를 불러오는 중`;

      const params = new URLSearchParams({
        shard: player.shard,
        account_id: player.account_id,
        telemetry_only: "true",
        limit: "200",
      });
      const response = await fetch(`/matches/explorer?${params.toString()}`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || response.statusText);
      updateTimelineMatchOptions(payload.matches || [], preferredMatchId);
      if (replayTimelineArtifacts.length) {
        await loadSelectedTimeline();
      }
    }

    function updateTimelineMatchOptions(matches, preferredMatchId = "") {
      const previousMatchId = replayTimelineArtifacts.find(
        (item) => String(item.id) === String(timelineSelect.value),
      )?.match_id || "";
      replayTimelineArtifacts = matches.map((match) => ({
        id: `match:${match.match_id}`,
        source_kind: "match_catalog",
        match_id: match.match_id,
        shard: match.shard,
        match_created_at_kst: match.created_at_kst,
        map_name: match.map_name,
        map_label: match.map_label,
        game_mode: match.game_mode,
        game_mode_label: match.game_mode_label,
        match_type: match.match_type,
        participant_count: match.participant_count,
      }));
      if (!replayTimelineArtifacts.length) {
        const player = activeReplayPlayer;
        clearReplayTimeline(player ? `${player.current_name}의 저장된 전체 경기 리플레이 원본이 없습니다.` : undefined);
        activeReplayPlayer = player;
        return;
      }
      timelineSelect.disabled = false;
      timelineSelect.innerHTML = replayTimelineArtifacts.map((match) => {
        const label = [
          formatKstShort(match.match_created_at_kst),
          match.map_label || displayCode(match.map_name, "map"),
          match.game_mode_label || displayCode(match.game_mode, "game_mode"),
          `${formatInteger(match.participant_count || 0)}명`,
        ].join(" / ");
        return `<option value="${attr(match.id)}">${escapeHtml(label)}</option>`;
      }).join("");
      const selectedMatchId = preferredMatchId || previousMatchId;
      const selected = replayTimelineArtifacts.find((item) => item.match_id === selectedMatchId);
      if (selected) timelineSelect.value = String(selected.id);
    }

    function canPlayTimelineArtifact(artifact) {
      return artifact?.artifact_type === "timeline" && artifact?.playback_ready === true;
    }

    function updateTimelineOptions(artifacts, preferredArtifactId = "") {
      const previous = timelineSelect.value;
      const timelineArtifacts = artifacts.filter((artifact) => artifact.artifact_type === "timeline");
      replayTimelineArtifacts = timelineArtifacts.filter(canPlayTimelineArtifact);
      if (!replayTimelineArtifacts.length) {
        const player = activeReplayPlayer;
        clearReplayTimeline(
          player
            ? (
              timelineArtifacts.length
                ? `${player.current_name}의 리플레이는 구버전입니다. 수집·처리에서 타임라인 저장을 실행해 재생성하세요.`
                : `${player.current_name}의 저장된 2D 리플레이가 없습니다.`
            )
            : "등록 유저를 선택한 뒤 경기를 불러오세요."
        );
        activeReplayPlayer = player;
        return;
      }
      timelineSelect.disabled = false;
      timelineSelect.innerHTML = replayTimelineArtifacts.map((artifact) => {
        const label = [
          artifact.artifact_name === "match-timeline"
            ? "전체 참가자"
            : (artifact.player_name || "알 수 없음"),
          formatKstShort(artifact.match_created_at_kst),
          artifact.map_name || "-",
          displayCode(artifact.game_mode, "game_mode"),
        ].join(" / ");
        return `<option value="${attr(artifact.id)}">${escapeHtml(label)}</option>`;
      }).join("") || `<option value="">재생 타임라인 없음</option>`;

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
      return formatNumber(size, unit === 0 ? 0 : 1) + " " + units[unit];
    }

    async function loadSelectedTimeline() {
      const selection = replayTimelineArtifacts.find((item) => String(item.id) === timelineSelect.value);
      if (!selection) {
        activeTimeline = null;
        activeTimelineArtifact = null;
        activeTimelineEvents = [];
        activeTimelineVisibleEvents = [];
        activeTimelineSelectedEventId = null;
        activeTimelineCurrentEventId = null;
        replayPinnedMap = null;
        replayPinnedEventId = null;
        renderTimelineActorFilter();
        renderTimelineQuickEvents();
        renderTimelineTeamList();
        renderTimelineEventList();
        renderTimelineEventDetail(null);
        renderTimelineNowEvent(null);
        replayPlayerStatus.textContent = "재생할 타임라인 파일이 없습니다.";
        drawEmptyReplayCanvas();
        return;
      }

      pauseReplay();
      let artifact = selection;
      if (selection.source_kind === "match_catalog") {
        replayPlayerStatus.textContent = `${formatKstShort(selection.match_created_at_kst)} 전체 참가자 리플레이 준비 중`;
        const generated = await requestJson(
          `/matches/${encodeURIComponent(selection.match_id)}/replay`,
          "POST",
          { force: false },
        );
        artifact = generated.artifact;
      }
      const payload = await fetch(artifact.view_url).then((response) => {
        if (!response.ok) throw new Error(response.statusText);
        return response.json();
      });
      validateTimelinePayload(payload);
      activeTimeline = rebaseMatchTimelineFocus(
        normalizeTimelineTiming(payload),
        activeReplayPlayer?.account_id || payload?.player?.account_id || "",
      );
      activeTimelineArtifact = artifact;
      activeTimelineEvents = timelineEvents(activeTimeline);
      activeTimelineVisibleEvents = [];
      activeTimelineSelectedEventId = null;
      activeTimelineCurrentEventId = null;
      activeTimelineDetailKey = "";
      activeTimelineDuration = Math.max(1, timelineDuration(activeTimeline));
      activeTimelineTime = 0;
      replayPinnedMap = null;
      replayPinnedEventId = null;
      timelineActorFilter.value = "focus";
      timelineEventTypeFilter.value = "all";
      timelineScrubber.max = String(activeTimelineDuration);
      timelineScrubber.value = "0";
      const replayTeam = activeTimeline.team || {};
      const replayMembers = replayTeam.members || [];
      const replayTotal = replayTeam.member_count ?? replayMembers.length;
      const replayHumans = replayTeam.human_member_count ?? replayMembers.filter((member) => !member.is_ai_or_bot).length;
      const replayBots = replayTeam.bot_member_count ?? replayMembers.filter((member) => member.is_ai_or_bot).length;
      replayPlayerStatus.textContent = [
        activeTimeline.player?.name || artifact.player_name || "알 수 없음",
        formatKstShort(artifact.match_created_at_kst),
        displayCode(activeTimeline.match?.map_name, "map"),
        `전체 ${formatInteger(replayTotal)}명`,
        `사람 ${formatInteger(replayHumans)} · 봇 ${formatInteger(replayBots)}`,
        compactIdentifier(activeTimeline.match?.match_id || artifact.match_id),
      ].join(" · ");
      await loadReplayMapImage(activeTimeline.match?.map_name);
      renderTimelineActorFilter();
      renderTimelineQuickEvents();
      renderTimelineTeamList();
      renderTimelineEventList();
      renderTimelineEventDetail(null);
      renderTimelineNowEvent(null);
      renderReplayFrame();
    }

    async function generateAndOpenMatchReplay() {
      const matchId = activeMatchExplorerDetail?.match?.match_id;
      if (!matchId) throw new Error("먼저 상세 조회할 매치를 선택하세요.");
      matchExplorerReplay.disabled = true;
      matchExplorerReplay.textContent = "리플레이 생성 중";
      banner.textContent = "전체 참가자 2D 리플레이 생성 중";
      try {
        const payload = await requestJson(
          `/matches/${encodeURIComponent(matchId)}/replay`,
          "POST",
          { force: false },
        );
        const artifact = payload.artifact;
        activeReplayPlayer = null;
        timelinePlayerInput.value = "";
        delete timelinePlayerInput.dataset.accountId;
        updateTimelineOptions([artifact], String(artifact.id));
        activateWorkspace("replay", {
          focusId: "replay-player",
          updateUrl: true,
          smooth: true,
        });
        await loadSelectedTimeline();
        const result = payload.result || {};
        replayPlayerStatus.textContent = `전체 참가자 ${formatInteger(result.participant_count || 0)}명 · 이동 표시 ${formatInteger(result.tracked_participant_count || 0)}명 · 위치 ${formatInteger(result.position_sample_count || 0)}개 · 전투 ${formatInteger(result.combat_event_count || 0)}개`;
        banner.textContent = result.generated
          ? "전체 참가자 2D 리플레이 생성 완료"
          : "저장된 전체 참가자 2D 리플레이를 열었습니다.";
      } finally {
        matchExplorerReplay.disabled = !activeMatchExplorerDetail?.telemetry?.available;
        matchExplorerReplay.textContent = "전체 참가자 2D 재생";
      }
    }

    function timelineSchemaVersion(value) {
      const match = /^player-timeline-v(\\d+)$/.exec(String(value || "").trim());
      return match ? Number(match[1]) : null;
    }

    function validateTimelinePayload(timeline) {
      const version = timelineSchemaVersion(timeline?.schema_version);
      if (version === null || version < 13) {
        throw new Error("이 타임라인은 현재 재생기와 호환되지 않습니다. 타임라인 저장을 실행해 재생성하세요.");
      }
      if (!Number.isFinite(Date.parse(timeline?.time_origin_at_kst || ""))) {
        throw new Error("타임라인 기준 시간이 없어 재생할 수 없습니다. 파일을 재생성하세요.");
      }
      const tracks = [
        Array.isArray(timeline?.positions) ? timeline.positions : [],
        ...(timeline?.team_tracks || []).map((track) => Array.isArray(track?.positions) ? track.positions : []),
      ];
      if (!tracks[0].length) {
        throw new Error("플레이어 이동 기록이 없는 타임라인입니다.");
      }
      for (const track of tracks) {
        for (const sample of track) {
          const seconds = replayNumber(sample?.time_seconds);
          const segmentId = Number(sample?.segment_id);
          const movementMode = String(sample?.movement_mode || "");
          if (
            seconds === null
            || seconds < 0
            || !Number.isInteger(segmentId)
            || segmentId < 0
            || !["on_foot", "vehicle", "airborne", "dbno"].includes(movementMode)
          ) {
            throw new Error("타임라인 시간축 또는 이동 구간이 손상되었습니다. 파일을 재생성하세요.");
          }
        }
      }
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
      for (const event of timeline.drop_starts || []) times.push(eventTime(event));
      for (const event of timeline.landings || []) times.push(eventTime(event));
      for (const event of timeline.combat_events || []) times.push(eventTime(event));
      for (const event of timeline.care_packages || []) times.push(eventTime(event));
      for (const event of timeline.phase_events || []) times.push(eventTime(event));
      for (const track of timeline.team_tracks || []) {
        for (const sample of track.positions || []) times.push(eventTime(sample));
        for (const event of track.drop_starts || []) times.push(eventTime(event));
        for (const event of track.landings || []) times.push(eventTime(event));
        for (const event of track.combat_events || []) times.push(eventTime(event));
      }
      for (const engagement of timeline.engagements || []) times.push(Number(engagement.end_time_seconds));
      const planeEnd = replayNumber(timeline.plane_route?.end_time_seconds);
      if (planeEnd !== null) times.push(planeEnd);
      const matchDuration = Number(timeline.match?.duration_seconds || 0);
      if (Number.isFinite(matchDuration) && matchDuration > 0) times.push(matchDuration);
      return Math.max(0, ...times.filter((value) => Number.isFinite(value)));
    }

    function replayNumber(value) {
      if (value === null || value === undefined || value === "") return null;
      const number = Number(value);
      return Number.isFinite(number) ? number : null;
    }

    function replayEventCollections(timeline) {
      const collections = [
        timeline.positions,
        timeline.drop_starts,
        timeline.landings,
        timeline.combat_events,
        timeline.care_packages,
        timeline.phase_events,
      ].filter(Array.isArray);
      for (const track of timeline.team_tracks || []) {
        if (Array.isArray(track.positions)) collections.push(track.positions);
        if (Array.isArray(track.drop_starts)) collections.push(track.drop_starts);
        if (Array.isArray(track.landings)) collections.push(track.landings);
        if (Array.isArray(track.combat_events)) collections.push(track.combat_events);
      }
      return collections;
    }

    function normalizeTimelineTiming(timeline) {
      const collections = replayEventCollections(timeline);
      const origins = [];
      for (const collection of collections) {
        for (const event of collection) {
          const elapsed = replayNumber(event?.elapsed_time_seconds);
          const at = Date.parse(event?.event_at_kst || "");
          if (elapsed !== null && elapsed >= 0 && Number.isFinite(at)) {
            origins.push(at - elapsed * 1000);
          }
        }
      }
      origins.sort((left, right) => left - right);
      let origin = Date.parse(timeline.time_origin_at_kst || "");
      if (!Number.isFinite(origin) && origins.length) {
        const middle = Math.floor(origins.length / 2);
        origin = origins.length % 2
          ? origins[middle]
          : (origins[middle - 1] + origins[middle]) / 2;
      }
      if (!Number.isFinite(origin)) {
        const timestamps = collections.flatMap((collection) => (
          collection.map((event) => Date.parse(event?.event_at_kst || "")).filter(Number.isFinite)
        ));
        if (timestamps.length) origin = Math.min(...timestamps);
      }

      for (const collection of collections) {
        for (const event of collection) {
          let seconds = replayNumber(event?.time_seconds);
          if (seconds === null) seconds = replayNumber(event?.elapsed_time_seconds);
          if (seconds === null && Number.isFinite(origin)) {
            const at = Date.parse(event?.event_at_kst || "");
            if (Number.isFinite(at)) seconds = Math.max(0, (at - origin) / 1000);
          }
          event.time_seconds = seconds;
        }
        collection.sort((left, right) => {
          const leftTime = eventTime(left);
          const rightTime = eventTime(right);
          return (Number.isFinite(leftTime) ? leftTime : Number.POSITIVE_INFINITY)
            - (Number.isFinite(rightTime) ? rightTime : Number.POSITIVE_INFINITY)
            || Number(left?.event_index || 0) - Number(right?.event_index || 0);
        });
      }
      const route = timeline.plane_route;
      if (route) {
        for (const prefix of ["start", "end"]) {
          let seconds = replayNumber(route[`${prefix}_time_seconds`]);
          if (seconds === null && Number.isFinite(origin)) {
            const at = Date.parse(route[`${prefix}_event_at_kst`] || "");
            if (Number.isFinite(at)) seconds = Math.max(0, (at - origin) / 1000);
          }
          route[`${prefix}_time_seconds`] = seconds;
        }
      }
      ensureReplayPathSegments(timeline.positions || []);
      for (const track of timeline.team_tracks || []) {
        ensureReplayPathSegments(track.positions || []);
      }
      if (!Array.isArray(timeline.drop_starts) || !timeline.drop_starts.length) {
        timeline.drop_starts = deriveReplayDropStarts(timeline.positions || []);
      }
      if (Number.isFinite(origin)) {
        timeline.time_origin_at_kst = new Date(origin).toISOString();
      }
      return timeline;
    }

    function rebaseMatchTimelineFocus(timeline, requestedAccountId) {
      if (timeline?.scope !== "match" || !requestedAccountId) return timeline;
      const members = Array.isArray(timeline.team?.members) ? timeline.team.members : [];
      const memberByAccount = new Map(
        members.filter((member) => member?.account_id).map((member) => [String(member.account_id), member]),
      );
      const rootAccountId = String(timeline.player?.account_id || "");
      const rootMember = memberByAccount.get(rootAccountId) || {};
      const tracks = [
        {
          account_id: rootAccountId,
          name: timeline.player?.name || rootMember.name,
          match_name: rootMember.match_name,
          team_id: rootMember.team_id,
          registered: Boolean(rootMember.registered),
          is_ai_or_bot: Boolean(rootMember.is_ai_or_bot),
          sample_count: (timeline.positions || []).length,
          positions: timeline.positions || [],
          drop_starts: timeline.drop_starts || [],
          landings: timeline.landings || [],
          combat_events: timeline.combat_events || [],
        },
        ...(timeline.team_tracks || []),
      ].filter((track) => track?.account_id);
      if (
        memberByAccount.has(String(requestedAccountId))
        && !tracks.some((track) => String(track.account_id) === String(requestedAccountId))
      ) {
        const requestedMember = memberByAccount.get(String(requestedAccountId));
        tracks.push({
          account_id: String(requestedAccountId),
          name: requestedMember?.name || requestedMember?.match_name || String(requestedAccountId),
          match_name: requestedMember?.match_name,
          team_id: requestedMember?.team_id ?? null,
          registered: Boolean(requestedMember?.registered),
          is_ai_or_bot: Boolean(requestedMember?.is_ai_or_bot),
          sample_count: 0,
          positions: [],
          drop_starts: [],
          landings: [],
          combat_events: [],
        });
      }
      const focusIndex = tracks.findIndex((track) => String(track.account_id) === String(requestedAccountId));
      if (focusIndex < 0) return timeline;

      const focus = tracks[focusIndex];
      const focusMember = memberByAccount.get(String(focus.account_id)) || {};
      for (const member of members) {
        member.is_self = String(member.account_id || "") === String(focus.account_id);
      }
      for (const track of tracks) {
        const member = memberByAccount.get(String(track.account_id)) || {};
        track.name = track.name || member.name || track.account_id;
        track.match_name = track.match_name || member.match_name;
        track.team_id = track.team_id ?? member.team_id ?? null;
        track.registered = Boolean(track.registered || member.registered);
        track.is_ai_or_bot = Boolean(track.is_ai_or_bot || member.is_ai_or_bot);
        track.sample_count = (track.positions || []).length;
        for (const event of [
          ...(track.drop_starts || []),
          ...(track.landings || []),
          ...(track.combat_events || []),
        ]) {
          event.actor_account_id = track.account_id;
          event.actor_name = track.name;
          event.actor_registered = track.registered;
          event.actor_is_self = String(track.account_id) === String(focus.account_id);
          event.actor_is_ai_or_bot = track.is_ai_or_bot;
        }
      }
      for (const engagement of timeline.engagements || []) {
        engagement.actor_is_self = String(engagement.actor_account_id || "") === String(focus.account_id);
      }

      timeline.player = {
        account_id: String(focus.account_id),
        name: focus.name || focusMember.name || String(focus.account_id),
      };
      timeline.positions = focus.positions || [];
      timeline.drop_starts = focus.drop_starts || [];
      timeline.landings = focus.landings || [];
      timeline.combat_events = focus.combat_events || [];
      timeline.team_tracks = tracks.filter((_, index) => index !== focusIndex);
      return timeline;
    }

    function ensureReplayPathSegments(samples) {
      if (!samples.length || samples.every((sample) => Number.isInteger(Number(sample.segment_id)))) return;
      let segmentId = 0;
      let previous = null;
      for (const sample of samples) {
        if (previous && replayPathBreak(previous, sample)) segmentId += 1;
        sample.segment_id = segmentId;
        previous = sample;
      }
    }

    function replayPathBreak(left, right) {
      const leftTime = eventTime(left);
      const rightTime = eventTime(right);
      const elapsed = rightTime - leftTime;
      if (!Number.isFinite(elapsed) || elapsed <= 0 || elapsed > 45) return true;
      const leftX = replayNumber(left?.x);
      const leftY = replayNumber(left?.y);
      const rightX = replayNumber(right?.x);
      const rightY = replayNumber(right?.y);
      if ([leftX, leftY, rightX, rightY].some((value) => value === null)) return true;
      const distanceM = Math.hypot(rightX - leftX, rightY - leftY) / 100;
      const leftZ = replayNumber(left?.z);
      const rightZ = replayNumber(right?.z);
      return distanceM / elapsed > 120
        || (leftZ !== null && rightZ !== null && rightZ >= 100000 && rightZ - leftZ >= 30000);
    }

    function deriveReplayDropStarts(samples) {
      const starts = [];
      const seen = new Set();
      for (const sample of samples) {
        const segmentId = Number(sample.segment_id || 0);
        if (seen.has(segmentId)) continue;
        seen.add(segmentId);
        if (Number(sample.z || 0) >= 20000 && sample.is_in_vehicle !== true) starts.push({ ...sample });
      }
      return starts;
    }

    function eventTime(event) {
      const seconds = replayNumber(event?.time_seconds);
      if (seconds !== null) return seconds;
      const elapsed = replayNumber(event?.elapsed_time_seconds);
      return elapsed === null ? Number.NaN : elapsed;
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

      const route = timeline.plane_route;
      if (route?.start?.map) {
        add("plane", {
          event_index: route.start_event_index,
          event_at_kst: route.start_event_at_kst,
          time_seconds: route.start_time_seconds,
          map: route.start.map,
        }, "비행 시작", "전체 비행 경로");
      }
      if (route?.end?.map) {
        add("plane", {
          event_index: route.end_event_index,
          event_at_kst: route.end_event_at_kst,
          time_seconds: route.end_time_seconds,
          map: route.end.map,
        }, "비행 종료", "전체 비행 경로");
      }
      const addActorTrackEvents = (track) => {
        for (const event of track.drop_starts || []) {
          add(
            "drop",
            event,
            `${replayActorName(event)} · 낙하 시작`,
            `고도 ${formatInteger(Math.round(Number(event.z || 0) / 100))}m`,
          );
        }
        for (const event of track.landings || []) {
          add("landing", event, `${replayActorName(event)} · 낙하산 착지`, `비행 거리 ${distanceM(event.distance_m)}`);
        }
        for (const event of track.combat_events || []) {
          const action = combatEventActionLabel(event);
          const weapon = event.weapon_label || event.damage_causer_label || event.weapon_code || event.damage_causer_name || "-";
          const suffix = event.is_headshot ? " · 헤드샷" : "";
          const related = event.related_name || event.related_account_id;
          const details = isReviveAction(event.action)
            ? [event.damage_reason || "부활", distanceM(event.distance_m)]
            : [weapon];
          if (Number(event.damage || 0) > 0) details.push(`피해 ${formatNumber(event.damage, 1)}`);
          if (Number(event.distance_m || 0) > 0) details.push(distanceM(event.distance_m));
          if (related) details.push(related);
          add("combat", event, `${replayActorName(event)} · ${action}${suffix}`, details.join(" / "));
        }
      };
      addActorTrackEvents({
        drop_starts: timeline.drop_starts || [],
        landings: timeline.landings || [],
        combat_events: timeline.combat_events || [],
      });
      for (const track of timeline.team_tracks || []) addActorTrackEvents(track);
      for (const engagement of timeline.engagements || []) {
        const source = {
          ...engagement,
          event_index: 0,
          event_at_kst: engagement.start_at_kst,
          time_seconds: engagement.start_time_seconds,
        };
        const outcome = engagement.outcome === "won" ? "우세" : engagement.outcome === "lost" ? "열세" : "공방";
        const verifiedOpponent = engagement.evidence === "verified_opponent" || Number(engagement.opponent_count || 0) > 0;
        add(
          "engagement",
          source,
          `${engagement.actor_name || "참가자"} · ${verifiedOpponent ? "교전" : "공격 활동"}`,
          `${verifiedOpponent ? `상대 ${formatInteger(engagement.opponent_count || 0)}명 확인` : "상대 미확인"} / ${outcome} / ${formatInteger(engagement.event_count || 0)}개 사건 / 킬 ${formatInteger(engagement.kills || 0)} / 기절 ${formatInteger(engagement.dbnos_caused || 0)}`,
        );
      }
      for (const event of timeline.care_packages || []) {
        const label = event.event_type === "LogCarePackageLand" ? "보급 상자 착지" : "보급 상자 생성";
        add("care", event, label, `${formatInteger(event.item_count || 0)}개 아이템`);
      }

      return events.sort((left, right) => (
        left.time - right.time
        || left.event_index - right.event_index
        || left.sequence - right.sequence
      ));
    }

    function replayActorName(event) {
      if (event?.actor_is_self) return event.actor_name || activeTimeline?.player?.name || "선택 유저";
      return event?.actor_name || compactIdentifier(event?.actor_account_id || "참가자");
    }

    function combatActionLabel(action) {
      const labels = {
        shot: "발사",
        throw: "투척",
        melee: "근접 공격",
        attack: "공격",
        hit_caused: "명중",
        hit_taken: "피격",
        dbno_caused: "기절시킴",
        dbno_taken: "기절당함",
        kill: "킬",
        death: "사망",
        finish: "확정 처치",
        finished_taken: "확정 처치당함",
        revive_given: "팀원 부활",
        revive_received: "부활받음",
      };
      return labels[action] || action || "교전";
    }

    function isNonOpponentDamage(event) {
      if (!["hit_taken", "dbno_taken", "death", "finished_taken"].includes(event?.action)) return false;
      const actorId = String(event.actor_account_id || "");
      const relatedId = String(event.related_account_id || "");
      if (relatedId) return relatedId === actorId;
      const category = String(event.damage_type_category || "");
      return category !== "Damage_Gun" && category !== "Damage_Explosion_Grenade" && category !== "Damage_Molotov";
    }

    function combatEventActionLabel(event) {
      if (isNonOpponentDamage(event)) {
        return ["death", "finished_taken"].includes(event.action) ? "환경·상태 사망" : "환경·상태 피해";
      }
      return combatActionLabel(event.action);
    }

    function isReviveAction(action) {
      return action === "revive_given" || action === "revive_received";
    }

    function timelineEventPresentation(event) {
      const category = event?.category || "event";
      if (category === "drop") return { tone: "drop", symbol: "◆", name: "낙하 시작" };
      if (category === "landing") return { tone: "landing", symbol: "▲", name: "착지" };
      if (category === "engagement") {
        return event?.source?.evidence === "verified_opponent" || Number(event?.source?.opponent_count || 0) > 0
          ? { tone: "engagement", symbol: "○", name: "교전" }
          : { tone: "activity", symbol: "○", name: "공격 활동" };
      }
      if (category === "plane") return { tone: "plane", symbol: "━", name: "비행기 동선" };
      if (category === "care") return { tone: "care", symbol: "■", name: "보급" };
      if (category !== "combat") return { tone: "event", symbol: "·", name: "사건" };

      const action = event?.source?.action;
      if (isNonOpponentDamage(event?.source)) {
        return ["death", "finished_taken"].includes(action)
          ? { tone: "death", symbol: "×", name: "환경·상태 사망" }
          : { tone: "environment", symbol: "!", name: "환경·상태 피해" };
      }
      const presentations = {
        shot: { tone: "shot", symbol: "◎", name: "발사" },
        throw: { tone: "throw", symbol: "◆", name: "투척" },
        melee: { tone: "attack", symbol: "△", name: "근접 공격" },
        attack: { tone: "attack", symbol: "△", name: "공격" },
        hit_caused: { tone: "hit-caused", symbol: "⊙", name: "명중시킴" },
        hit_taken: { tone: "hit-taken", symbol: "■", name: "피격당함" },
        dbno_caused: { tone: "dbno-caused", symbol: "+", name: "기절시킴" },
        dbno_taken: { tone: "dbno-taken", symbol: "−", name: "기절당함" },
        kill: { tone: "kill", symbol: "×", name: "킬" },
        finish: { tone: "kill", symbol: "×", name: "확정 처치" },
        death: { tone: "death", symbol: "×", name: "사망" },
        finished_taken: { tone: "death", symbol: "×", name: "확정 처치당함" },
        revive_given: { tone: "revive", symbol: "+", name: "팀원 부활" },
        revive_received: { tone: "revive", symbol: "+", name: "부활받음" },
      };
      return presentations[action] || { tone: "event", symbol: "·", name: combatActionLabel(action) };
    }

    function timelineEventBadgeHtml(event) {
      const presentation = timelineEventPresentation(event);
      return `<i class="timeline-event-badge event-tone-${presentation.tone}" title="${attr(presentation.name)}" aria-label="${attr(presentation.name)}"><span>${escapeHtml(presentation.symbol)}</span></i>`;
    }

    function timelineEventActorId(event) {
      return String(event?.source?.actor_account_id || event?.source?.account_id || "");
    }

    function timelineEventMatchesActor(event) {
      const selected = timelineActorFilter?.value || "focus";
      const source = event?.source || {};
      const actorId = timelineEventActorId(event);
      if (!actorId) return true;
      if (selected === "all") return true;
      if (selected === "focus") return source.actor_is_self !== false;
      return actorId === selected;
    }

    function timelineEventMatchesType(event) {
      const selected = timelineEventTypeFilter?.value || "all";
      if (selected === "all") return true;
      if (selected === "drop_landing") return ["drop", "landing"].includes(event.category);
      if (selected === "engagement") return event.category === "engagement";
      if (selected === "world") return ["plane", "care"].includes(event.category);
      if (event.category !== "combat") return false;
      const action = event.source?.action;
      if (selected === "environment") return isNonOpponentDamage(event.source);
      if (selected === "attack") return ["shot", "throw", "melee", "attack"].includes(action);
      if (selected === "hit") return ["hit_caused", "hit_taken"].includes(action) && !isNonOpponentDamage(event.source);
      if (selected === "dbno") return ["dbno_caused", "dbno_taken"].includes(action);
      if (selected === "kill") return ["kill", "finish", "death", "finished_taken"].includes(action);
      if (selected === "revive") return isReviveAction(action);
      return true;
    }

    function filteredTimelineEvents() {
      return activeTimelineEvents.filter((event) => (
        timelineEventVisible(event)
        && timelineEventMatchesActor(event)
        && timelineEventMatchesType(event)
      ));
    }

    function timelineEventMapPoint(event) {
      const source = event?.source || {};
      if (
        event?.category === "combat"
        && ["hit_caused", "dbno_caused", "kill", "finish"].includes(source.action)
        && source.related_map
      ) return source.related_map;
      return source.map || null;
    }

    function timelineFocusMember() {
      const focusId = String(activeTimeline?.player?.account_id || "");
      return (activeTimeline?.team?.members || []).find(
        (member) => String(member.account_id || "") === focusId,
      ) || null;
    }

    function timelineTrackByAccount(accountId) {
      const normalized = String(accountId || "");
      if (!normalized) return null;
      if (normalized === String(activeTimeline?.player?.account_id || "")) {
        const member = timelineFocusMember() || {};
        return {
          ...member,
          account_id: normalized,
          name: activeTimeline?.player?.name || member.name,
          positions: activeTimeline?.positions || [],
          drop_starts: activeTimeline?.drop_starts || [],
          landings: activeTimeline?.landings || [],
          combat_events: activeTimeline?.combat_events || [],
          is_self: true,
        };
      }
      return (activeTimeline?.team_tracks || []).find(
        (track) => String(track.account_id || "") === normalized,
      ) || null;
    }

    function replayParticipantRelation(participant) {
      if (!participant) return "enemy";
      if (String(participant.account_id || "") === String(activeTimeline?.player?.account_id || "")) return "focus";
      const focusTeamId = timelineFocusMember()?.team_id;
      if (focusTeamId !== null && focusTeamId !== undefined && String(participant.team_id) === String(focusTeamId)) return "ally";
      if (participant.is_ai_or_bot) return "bot";
      return "enemy";
    }

    function replayParticipantVisible(participant) {
      const relation = replayParticipantRelation(participant);
      if (relation === "focus") return true;
      if (relation === "ally") return timelineShowAllies.checked;
      if (relation === "bot") return timelineShowBots.checked;
      return timelineShowEnemies.checked;
    }

    function replayParticipantColor(participant) {
      const relation = replayParticipantRelation(participant);
      if (relation === "focus") return "#4bd0a0";
      if (relation === "ally") return "#54c8ff";
      if (relation === "bot") return "#87939d";
      if (participant?.registered) return "#d98cff";
      return "#ff786e";
    }

    function replayRelationLabel(participant) {
      const labels = { focus: "기준 유저", ally: "아군", enemy: "적군", bot: "봇" };
      return labels[replayParticipantRelation(participant)] || "참가자";
    }

    function renderTimelineActorFilter() {
      if (!timelineActorFilter) return;
      const previous = timelineActorFilter.value || "focus";
      const members = activeTimeline?.team?.members || [];
      const options = [
        { value: "focus", label: `선택 유저${activeTimeline?.player?.name ? ` · ${activeTimeline.player.name}` : ""}` },
        { value: "all", label: activeTimeline?.scope === "match" ? "전체 참가자" : "전체 팀" },
        ...members.filter((member) => !member.is_self && member.account_id).map((member) => ({
          value: member.account_id,
          label: `${member.name || compactIdentifier(member.account_id)} · ${replayRelationLabel(member)}${member.registered ? " · 등록 유저" : ""}`,
        })),
      ];
      timelineActorFilter.innerHTML = options.map((option) => (
        `<option value="${attr(option.value)}">${escapeHtml(option.label)}</option>`
      )).join("");
      timelineActorFilter.value = options.some((option) => option.value === previous) ? previous : "focus";
    }

    function quickTimelineCandidates() {
      return activeTimelineEvents.filter((event) => {
        if (!timelineEventMapPoint(event)) return false;
        return timelineEventMatchesActor(event);
      });
    }

    function renderTimelineQuickEvents() {
      if (!timelineQuickEvents) return;
      if (!activeTimeline) {
        timelineQuickEvents.innerHTML = `<span class="status">경기를 불러오세요.</span>`;
        return;
      }
      const candidates = quickTimelineCandidates();
      const definitions = [
        { label: "낙하 시작", match: (event) => event.category === "drop" },
        { label: "착지 위치", match: (event) => event.category === "landing" },
        { label: "첫 교전", match: (event) => event.category === "engagement" && (event.source?.evidence === "verified_opponent" || Number(event.source?.opponent_count || 0) > 0) },
        { label: "첫 공격 활동", match: (event) => event.category === "engagement" && event.source?.evidence === "inferred_attack_activity" },
        { label: "첫 명중", match: (event) => event.category === "combat" && event.source?.action === "hit_caused" },
        { label: "첫 기절시킴", match: (event) => event.category === "combat" && event.source?.action === "dbno_caused" },
        { label: "첫 기절당함", match: (event) => event.category === "combat" && event.source?.action === "dbno_taken" },
        { label: "첫 킬", match: (event) => event.category === "combat" && ["kill", "finish"].includes(event.source?.action) },
        { label: "사망 위치", match: (event) => event.category === "combat" && ["death", "finished_taken"].includes(event.source?.action) },
      ];
      const shortcuts = definitions.map((definition) => ({
        ...definition,
        event: candidates.find(definition.match),
      })).filter((item) => item.event);
      timelineQuickEvents.innerHTML = shortcuts.length
        ? shortcuts.map((item) => `<button class="secondary" type="button" data-timeline-map-event="${attr(item.event.id)}" title="${attr(`${item.label}를 지도에서 보기`)}">${escapeHtml(item.label)} · ${formatReplayTime(item.event.time)}</button>`).join("")
        : `<span class="status">선택 대상의 주요 위치 기록이 없습니다.</span>`;
    }

    function currentPlaybackTimelineEvent() {
      const visible = activeTimelineVisibleEvents;
      let current = null;
      for (const event of visible) {
        if (event.time > activeTimelineTime) break;
        current = event;
      }
      if (!current) return null;
      const age = activeTimelineTime - current.time;
      const keepSeconds = current.category === "combat" ? 4.5 : current.category === "engagement" ? 8 : 6;
      return age <= keepSeconds ? current : null;
    }

    function renderTimelineNowEvent(event = undefined) {
      if (!timelineNowEvent) return;
      const current = event === undefined ? (selectedTimelineEvent() || currentPlaybackTimelineEvent()) : event;
      const artifactKey = activeTimelineArtifact?.id || activeTimeline?.match?.match_id || "none";
      const renderKey = current
        ? `${artifactKey}:${current.id}:${activeTimelineSelectedEventId || ""}`
        : `${artifactKey}:empty:${activeTimeline ? Math.floor(activeTimelineTime) : "none"}`;
      if (timelineNowEvent.dataset.renderKey === renderKey) return;
      timelineNowEvent.dataset.renderKey = renderKey;
      if (!current) {
        timelineNowEvent.innerHTML = `
          <i class="timeline-event-badge"><span>·</span></i>
          <span class="event-copy"><strong>현재 사건 없음</strong><span class="event-meta">${activeTimeline ? "다음 사건까지 재생 중" : "경기를 불러오세요."}</span></span>
          <span class="event-time">${formatReplayTime(activeTimelineTime)}</span>
        `;
        return;
      }
      const source = current.source || {};
      const meta = [current.meta, source.event_at_kst ? `KST ${formatKstShort(source.event_at_kst)}` : ""].filter(Boolean).join(" · ");
      timelineNowEvent.innerHTML = `
        ${timelineEventBadgeHtml(current)}
        <span class="event-copy"><strong>${escapeHtml(current.label)}</strong><span class="event-meta">${escapeHtml(meta || timelineEventPresentation(current).name)}</span></span>
        <span class="event-time">${formatReplayTime(current.time)}</span>
      `;
    }

    function syncTimelineCurrentEvent() {
      const current = currentPlaybackTimelineEvent();
      const nextId = current?.id || null;
      if (nextId === activeTimelineCurrentEventId) {
        renderTimelineNowEvent(activeTimelineSelectedEventId ? selectedTimelineEvent() : current);
        return;
      }
      activeTimelineCurrentEventId = nextId;
      timelineEventList?.querySelectorAll(".timeline-event-item.current").forEach((element) => element.classList.remove("current"));
      const item = Array.from(timelineEventList?.querySelectorAll("[data-timeline-event-item]") || [])
        .find((element) => element.dataset.timelineEventItem === nextId);
      if (item) {
        item.classList.add("current");
        if (replayPlaying && timelineFollowEvents?.checked) item.scrollIntoView({ block: "nearest" });
      }
      renderTimelineNowEvent(activeTimelineSelectedEventId ? selectedTimelineEvent() : current);
    }

    function refreshTimelineEventExplorer({ clearSelection = true } = {}) {
      if (clearSelection) {
        activeTimelineSelectedEventId = null;
        activeTimelineCurrentEventId = null;
        activeTimelineDetailKey = "";
        replayPinnedMap = null;
        replayPinnedEventId = null;
      }
      if (!clearSelection) {
        const selected = selectedTimelineEvent();
        if (selected && (!timelineEventVisible(selected) || !timelineEventMatchesActor(selected) || !timelineEventMatchesType(selected))) {
          activeTimelineSelectedEventId = null;
          activeTimelineDetailKey = "";
          replayPinnedMap = null;
          replayPinnedEventId = null;
        }
      }
      renderTimelineTeamList();
      renderTimelineQuickEvents();
      renderTimelineEventList();
      renderTimelineEventDetail(null);
      renderTimelineNowEvent(null);
      renderReplayFrame();
    }

    function renderTimelineTeamList() {
      if (!timelineTeamList) return;
      const query = String(timelineParticipantSearch?.value || "").trim().toLocaleLowerCase("ko-KR");
      const members = (activeTimeline?.team?.members || []).filter((member) => {
        if (!query) return true;
        return String(member.name || member.account_id || "").toLocaleLowerCase("ko-KR").includes(query);
      });
      if (!members.length) {
        timelineTeamList.innerHTML = `<div class="status">조건에 맞는 참가자가 없습니다.</div>`;
        return;
      }
      const ordered = [...members].sort((left, right) => {
        const order = { focus: 0, ally: 1, enemy: 2, bot: 3 };
        const relationDelta = order[replayParticipantRelation(left)] - order[replayParticipantRelation(right)];
        if (relationDelta) return relationDelta;
        return String(left.name || "").localeCompare(String(right.name || ""), "ko-KR");
      });
      timelineTeamList.innerHTML = ordered.map((member) => {
        const badges = [];
        badges.push(replayRelationLabel(member));
        if (member.registered && !member.is_self) badges.push("등록 유저");
        if (member.position_sample_count > 0 && !member.is_self) badges.push("이동 경로");
        if (member.combat_event_count > 0) badges.push("전투 기록");
        const stats = [
          `킬 ${formatInteger(member.kills || 0)}`,
          `어시 ${Number(member.assists || 0)}`,
          `피해 ${formatNumber(member.damage_dealt || 0, 0)}`,
          member.position_sample_count > 0 ? `위치 ${formatInteger(member.position_sample_count || 0)}개` : "",
          member.combat_event_count > 0 ? `전투 ${formatInteger(member.combat_event_count || 0)}건` : "",
          member.win_place ? `${member.win_place}위` : "",
        ].filter(Boolean).join(" / ");
        const actorFilterValue = member.is_self ? "focus" : member.account_id;
        const selected = (timelineActorFilter?.value || "focus") === actorFilterValue;
        return `
          <button type="button" class="team-member ${member.is_self ? "self" : ""} ${member.registered && !member.is_self ? "registered" : ""} ${selected ? "selected" : ""}" data-timeline-actor="${attr(actorFilterValue)}" style="border-left-color:${attr(replayParticipantColor(member))}">
            <strong>${escapeHtml(member.name || member.account_id || "알 수 없음")}</strong>
            <span>${escapeHtml(badges.join(" / ") || "참가자")}</span>
            <span>${escapeHtml(stats || "-")}</span>
          </button>
        `;
      }).join("");
    }

    function formatReplayTime(value) {
      const seconds = Math.max(0, Number(value || 0));
      const minutes = Math.floor(seconds / 60);
      const rest = seconds - minutes * 60;
      return `${formatInteger(minutes)}:${formatNumber(rest, 1).padStart(4, "0")}`;
    }

    function renderTimelineEventList() {
      if (!timelineEventList) return;
      const visibleEvents = filteredTimelineEvents();
      activeTimelineVisibleEvents = visibleEvents;
      if (timelineEventCount) timelineEventCount.textContent = `${formatInteger(visibleEvents.length)}개 사건`;
      if (!visibleEvents.length) {
        timelineEventList.innerHTML = `<div class="status">표시할 리플레이 이벤트가 없습니다.</div>`;
        return;
      }
      timelineEventList.innerHTML = visibleEvents.map((event) => `
        <div class="timeline-event-item ${event.id === activeTimelineSelectedEventId ? "active" : ""} ${event.id === activeTimelineCurrentEventId ? "current" : ""}" data-timeline-event-item="${attr(event.id)}">
          <button class="timeline-event-row" type="button" data-timeline-event="${attr(event.id)}">
            ${timelineEventBadgeHtml(event)}
            <span>${formatReplayTime(event.time)}</span>
            <span class="timeline-event-copy"><strong>${escapeHtml(event.label)}</strong><em>${escapeHtml(event.meta || "")}</em></span>
          </button>
          ${timelineEventMapPoint(event) ? `<button class="secondary timeline-map-button" type="button" data-timeline-map-event="${attr(event.id)}" title="${attr(`${event.label} 위치를 지도에서 보기`)}">지도</button>` : ""}
        </div>
      `).join("");
    }

    function timelineEventVisible(event) {
      const source = event?.source || {};
      const actorId = timelineEventActorId(event);
      if (actorId && !replayParticipantVisible(timelineTrackByAccount(actorId))) return false;
      if (event.category === "plane") return timelineShowPlane.checked;
      if (["drop", "landing"].includes(event.category)) return timelineShowPath.checked;
      if (event.category === "care") return timelineShowCare.checked;
      if (event.category === "engagement") return timelineShowCombat.checked && timelineShowEngagements.checked;
      if (event.category !== "combat") return true;
      if (!timelineShowCombat.checked) return false;
      if (source.action === "shot") return timelineShowShots.checked;
      if (source.action === "throw") return timelineShowThrows.checked;
      if (["hit_caused", "hit_taken"].includes(source.action)) return timelineShowHits.checked;
      if (["dbno_caused", "dbno_taken"].includes(source.action)) return timelineShowDbno.checked;
      if (["kill", "death", "finish", "finished_taken"].includes(source.action)) return timelineShowKills.checked;
      return true;
    }

    function renderTimelineEventDetail(event) {
      if (!timelineEventDetail) return;
      const selected = event || selectedTimelineEvent();
      const nearest = selected || currentPlaybackTimelineEvent();
      const key = nearest ? `${nearest.id}:${activeTimelineSelectedEventId || ""}` : `empty:${activeTimelineEvents.length}`;
      if (key === activeTimelineDetailKey) return;
      activeTimelineDetailKey = key;

      if (!nearest) {
        timelineEventDetail.className = "timeline-event-detail status";
        timelineEventDetail.innerHTML = activeTimelineEvents.length
          ? `이벤트를 선택하거나 재생 시점이 이벤트에 가까워지면 상세가 표시됩니다.`
          : `이 리플레이에는 상세 이벤트가 없습니다.`;
        return;
      }

      const source = nearest.source || {};
      const detailLines = [
        `<strong>${escapeHtml(nearest.label)}</strong>`,
        `시각 ${formatReplayTime(nearest.time)} / 이벤트 #${nearest.event_index ?? "-"}`,
      ];
      if (nearest.category === "combat") {
        detailLines.push(`행위자 ${escapeHtml(replayActorName(source))}`);
        if (isReviveAction(source.action)) {
          detailLines.push(`방식 ${escapeHtml(source.damage_reason || "부활")} / 거리 ${distanceM(source.distance_m)}`);
        } else {
          detailLines.push(`무기 ${escapeHtml(source.weapon_label || source.damage_causer_label || source.weapon_code || source.damage_causer_name || "-")}`);
          if (!["shot", "throw", "melee", "attack"].includes(source.action)) {
            const impactDetails = [`피격 부위 ${escapeHtml(source.damage_reason || "-")}`];
            if (source.damage !== null && source.damage !== undefined && source.damage !== "" && Number.isFinite(Number(source.damage))) {
              impactDetails.push(`피해 ${formatNumber(source.damage, 1)}`);
            }
            impactDetails.push(`거리 ${distanceM(source.distance_m)}`);
            detailLines.push(impactDetails.join(" / "));
          }
          if (source.attack_id !== null && source.attack_id !== undefined) detailLines.push(`공격 ID ${escapeHtml(String(source.attack_id))}`);
          if (isNonOpponentDamage(source)) {
            detailLines.push(`분류 환경·상태 피해 / 원인 ${escapeHtml(source.damage_causer_label || source.damage_causer_name || source.damage_type_category || "-")}`);
          } else if (["hit_caused", "hit_taken", "dbno_caused", "dbno_taken", "kill", "death", "finish", "finished_taken"].includes(source.action)) {
            detailLines.push(source.has_verified_direction ? "방향 근거 공격자·피격자 좌표" : "방향 근거 없음");
          }
        }
        const relatedLabel = combatRelatedLabel(source);
        if (relatedLabel) detailLines.push(`상대 ${relatedLabel}`);
      } else if (nearest.category === "engagement") {
        detailLines.push(`구간 ${formatReplayTime(source.start_time_seconds)}–${formatReplayTime(source.end_time_seconds)}`);
        detailLines.push(source.evidence === "verified_opponent" || Number(source.opponent_count || 0) > 0
          ? `근거 상대 계정 ${formatInteger(source.opponent_count || 0)}명 확인`
          : "근거 공격 기록만 확인 · 상대 미확인");
        detailLines.push(`사건 ${formatInteger(source.event_count || 0)} / 발사 ${formatInteger(source.shots || 0)} / 투척 ${formatInteger(source.throws || 0)} / 명중 ${formatInteger(source.hits_caused || 0)} / 피격 ${formatInteger(source.hits_taken || 0)}`);
        detailLines.push(`킬 ${formatInteger(source.kills || 0)} / 기절 ${formatInteger(source.dbnos_caused || 0)} / 가한 피해 ${formatNumber(source.damage_caused || 0, 1)}`);
        if ((source.weapons || []).length) detailLines.push(`무기 ${escapeHtml(source.weapons.join(", "))}`);
      } else if (nearest.category === "care") {
        detailLines.push(`유형 ${escapeHtml(source.event_type || "-")} / 아이템 ${formatInteger(source.item_count || 0)}개`);
        const itemCodes = (source.item_codes || []).slice(0, 8).join(", ");
        if (itemCodes) detailLines.push(`아이템 코드 ${escapeHtml(itemCodes)}`);
      } else if (nearest.category === "landing") {
        detailLines.push(`비행 거리 ${distanceM(source.distance_m)}`);
      } else if (nearest.category === "drop") {
        detailLines.push(`첫 기록 고도 ${formatInteger(Math.round(Number(source.z || 0) / 100))}m`);
      }
      if (source.event_at_kst) detailLines.push(`KST ${escapeHtml(formatKstShort(source.event_at_kst))}`);
      timelineEventDetail.className = "timeline-event-detail";
      timelineEventDetail.innerHTML = detailLines.join("<br>");
    }

    function combatRelatedLabel(source) {
      const name = source.related_name || source.related_account_id;
      if (!name) return "";
      const badges = [];
      if (source.related_registered) badges.push("등록 유저");
      if (source.related_is_ai_or_bot) badges.push("봇");
      return `${escapeHtml(name)}${badges.length ? ` (${escapeHtml(badges.join(", "))})` : ""}`;
    }

    function selectedTimelineEvent() {
      return activeTimelineEvents.find((event) => event.id === activeTimelineSelectedEventId) || null;
    }

    function seekTimelineEvent(eventId, focusMap = false) {
      const event = activeTimelineEvents.find((item) => item.id === eventId);
      if (!event) return;
      pauseReplay();
      activeTimelineSelectedEventId = event.id;
      activeTimelineCurrentEventId = event.id;
      activeTimelineTime = Math.max(0, Math.min(activeTimelineDuration, event.time));
      if (focusMap) {
        const mapPoint = timelineEventMapPoint(event);
        replayPinnedMap = mapPoint ? { x_pct: Number(mapPoint.x_pct), y_pct: Number(mapPoint.y_pct) } : null;
        replayPinnedEventId = replayPinnedMap ? event.id : null;
        if (replayPinnedMap) {
          timelineFollowPlayer.checked = false;
          if (Number(timelineZoom.value || 1) < 2) timelineZoom.value = "2";
        }
      } else {
        replayPinnedMap = null;
        replayPinnedEventId = null;
      }
      renderTimelineEventList();
      renderTimelineEventDetail(event);
      renderTimelineNowEvent(event);
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
      if (timelineShowPath.checked) {
        drawReplayPath(activeTimeline.positions || []);
        drawReplayDropStarts(activeTimeline.drop_starts || [], "#4bd0a0");
        drawReplayLandings(activeTimeline.landings || [], "#4bd0a0");
      }
      drawReplayTeamTracks(activeTimeline.team_tracks || []);
      if (timelineShowCombat.checked && timelineShowEngagements.checked) {
        drawReplayEngagements(activeTimeline.engagements || []);
      }
      if (timelineShowCombat.checked) {
        drawReplayAllCombatEvents();
      }
      syncTimelineCurrentEvent();
      drawReplaySelectedEvent();
      drawReplayPlayer(activeTimeline.positions || []);
      drawReplayCurrentEventCallout();
      drawReplayOverlay();
      renderTimelineEventDetail(null);
      timelineClock.textContent = `${formatReplayTime(activeTimelineTime)} · ${formatReplayKst(activeTimelineTime)} KST`;
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
      const startTime = replayNumber(route.start_time_seconds);
      const endTime = replayNumber(route.end_time_seconds);
      if (
        startTime !== null
        && endTime !== null
        && endTime > startTime
        && activeTimelineTime >= startTime
        && activeTimelineTime <= endTime
      ) {
        const ratio = Math.max(0, Math.min(1, (activeTimelineTime - startTime) / (endTime - startTime)));
        const aircraft = {
          x: start.x + (end.x - start.x) * ratio,
          y: start.y + (end.y - start.y) * ratio,
        };
        drawCircle(aircraft, 10, "#e3f2fd", "#1565c0");
        replayCtx.strokeStyle = "#1565c0";
        replayCtx.lineWidth = 3;
        replayCtx.beginPath();
        replayCtx.moveTo(aircraft.x - 13, aircraft.y);
        replayCtx.lineTo(aircraft.x + 13, aircraft.y);
        replayCtx.stroke();
      }
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
      drawMovementTrack(samples, "#4bd0a0", true);
    }

    function drawReplayTeamTracks(tracks) {
      tracks.forEach((track) => {
        if (!replayParticipantVisible(track)) return;
        const samples = track.positions || [];
        const color = replayParticipantColor(track);
        if (timelineShowPath.checked) {
          drawMovementTrack(samples, color, false, track.registered ? 3 : 2);
          drawReplayDropStarts(track.drop_starts || [], color);
          drawReplayLandings(track.landings || [], color);
        }

        const current = replayTrackPosition(track, activeTimelineTime);
        if (!current) return;
        const point = canvasPoint(current);
        if (!canvasPointVisible(point, 16)) return;
        drawReplayActorMarker(point, current.movement_mode, color, false);
        const selectedActor = timelineActorFilter?.value || "focus";
        const relation = replayParticipantRelation(track);
        if (relation === "ally" || track.registered || selectedActor === String(track.account_id)) {
          drawReplayLabel(point, track.name || track.account_id || "참가자", color);
        }
      });
      replayCtx.setLineDash([]);
    }

    function drawMovementTrack(samples, actorColor, isSelf, baseWidth = 4) {
      for (const segment of visiblePositionModeSegments(samples)) {
        if (segment.samples.length < 2) continue;
        const style = movementPathStyle(segment.mode, baseWidth);
        if (!isSelf) {
          replayCtx.save();
          replayCtx.globalAlpha = 0.55;
          replayCtx.strokeStyle = actorColor;
          replayCtx.lineWidth = style.width + 4;
          replayCtx.setLineDash([]);
          replayCtx.beginPath();
          segment.samples.forEach((sample, index) => {
            const point = canvasPoint(sample.map);
            if (index === 0) replayCtx.moveTo(point.x, point.y);
            else replayCtx.lineTo(point.x, point.y);
          });
          replayCtx.stroke();
          replayCtx.restore();
        }
        replayCtx.strokeStyle = style.color;
        replayCtx.lineWidth = style.width;
        replayCtx.setLineDash(style.dash);
        replayCtx.beginPath();
        segment.samples.forEach((sample, index) => {
          const point = canvasPoint(sample.map);
          if (index === 0) replayCtx.moveTo(point.x, point.y);
          else replayCtx.lineTo(point.x, point.y);
        });
        replayCtx.stroke();
      }
      replayCtx.setLineDash([]);
    }

    function movementPathStyle(mode, baseWidth) {
      const selfColors = {
        on_foot: "#4bd0a0",
        vehicle: "#ffb84d",
        airborne: "#54c8ff",
        dbno: "#ff5f6d",
      };
      const dash = {
        on_foot: [],
        vehicle: [16, 4],
        airborne: [5, 8],
        dbno: [2, 6],
      };
      return {
        color: selfColors[mode] || selfColors.on_foot,
        dash: dash[mode] || [],
        width: mode === "vehicle" ? baseWidth + 1 : baseWidth,
      };
    }

    function drawReplayDropStarts(events, actorColor = "#54c8ff") {
      for (const event of events) {
        if (eventTime(event) > activeTimelineTime || !event.map) continue;
        const point = canvasPoint(event.map);
        replayCtx.fillStyle = "#54c8ff";
        replayCtx.strokeStyle = actorColor;
        replayCtx.lineWidth = 2;
        replayCtx.beginPath();
        replayCtx.moveTo(point.x, point.y - 10);
        replayCtx.lineTo(point.x + 10, point.y);
        replayCtx.lineTo(point.x, point.y + 10);
        replayCtx.lineTo(point.x - 10, point.y);
        replayCtx.closePath();
        replayCtx.fill();
        replayCtx.stroke();
      }
    }

    function drawReplayLandings(events, actorColor = "#4bd0a0") {
      replayCtx.fillStyle = "rgba(255,235,59,0.95)";
      replayCtx.strokeStyle = actorColor;
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

    function drawReplayCombatEvents(events, actorColor = "#4bd0a0") {
      for (const event of events) {
        if (eventTime(event) > activeTimelineTime || !event.map) continue;
        const eventAge = activeTimelineTime - eventTime(event);
        const actorPoint = canvasPoint(event.map);
        const relatedPoint = event.related_map ? canvasPoint(event.related_map) : null;
        if (event.action === "shot") {
          if (timelineShowShots.checked && eventAge <= 3.5) drawShotBurst(actorPoint, actorColor, eventAge);
          continue;
        }
        if (event.action === "throw") {
          if (timelineShowThrows.checked && eventAge <= 4.5) drawThrowBurst(actorPoint, actorColor, eventAge);
          continue;
        }
        if (["melee", "attack"].includes(event.action)) {
          if (eventAge <= 3.5) drawDiamond(actorPoint, "#ffb74d", actorColor);
          continue;
        }
        if (["hit_caused", "hit_taken"].includes(event.action)) {
          if (!timelineShowHits.checked || eventAge > 5) continue;
          if (isNonOpponentDamage(event)) {
            drawEnvironmentalDamageMarker(actorPoint);
            continue;
          }
          const start = event.action === "hit_taken" ? relatedPoint : actorPoint;
          const end = event.action === "hit_taken" ? actorPoint : relatedPoint;
          if (start && end) drawDirectionArrow(start, end, event.action === "hit_taken" ? "#ff6b6b" : "#ffd54f");
          drawHitMarker(
            end || actorPoint,
            event.action === "hit_taken" ? "#ff6b6b" : "#ffd54f",
            event.is_headshot,
            event.action === "hit_taken",
          );
          continue;
        }
        if (["dbno_caused", "dbno_taken"].includes(event.action)) {
          if (!timelineShowDbno.checked) continue;
          const marker = event.action === "dbno_caused" ? (relatedPoint || actorPoint) : actorPoint;
          if (event.action === "dbno_caused" && relatedPoint) drawDirectionArrow(actorPoint, relatedPoint, "rgba(255,159,67,0.72)");
          if (event.action === "dbno_taken" && relatedPoint) drawDirectionArrow(relatedPoint, actorPoint, "rgba(255,95,109,0.72)");
          drawDbnoMarker(marker, event.action === "dbno_caused", actorColor);
          continue;
        }
        if (isReviveAction(event.action)) {
          drawPlus(actorPoint, event.action === "revive_given" ? "#00bcd4" : "#26a69a");
        } else if (["kill", "finish"].includes(event.action)) {
          if (!timelineShowKills.checked) continue;
          const marker = relatedPoint || actorPoint;
          if (relatedPoint) drawDirectionArrow(actorPoint, relatedPoint, "rgba(255,95,109,0.68)");
          drawCircle(marker, 11, "rgba(8,11,13,0.72)", actorColor);
          drawX(marker, event.is_headshot ? "#ff1744" : "#ff5f6d");
        } else if (["death", "finished_taken"].includes(event.action)) {
          if (!timelineShowKills.checked) continue;
          drawSquare(actorPoint, 22, "rgba(8,11,13,0.82)", actorColor);
          drawX(actorPoint, "#ff5f6d");
        }
      }
    }

    function drawReplayAllCombatEvents() {
      const tracks = [
        timelineTrackByAccount(activeTimeline?.player?.account_id),
        ...(activeTimeline?.team_tracks || []),
      ].filter((track) => track && replayParticipantVisible(track));
      const seen = new Set();
      for (const track of tracks) {
        const color = replayParticipantColor(track);
        for (const event of track.combat_events || []) {
          if (!canonicalReplayCombatEvent(event)) continue;
          const actionGroup = ["kill", "finish"].includes(event.action) ? "kill" : event.action;
          const key = `${event.event_index ?? "x"}:${actionGroup}:${event.related_account_id || ""}`;
          if (seen.has(key)) continue;
          seen.add(key);
          drawReplayCombatEvents([event], color);
        }
      }
    }

    function canonicalReplayCombatEvent(event) {
      const relatedTrack = timelineTrackByAccount(event?.related_account_id);
      if (!relatedTrack) return true;
      return !["hit_taken", "dbno_taken", "death", "finished_taken", "revive_received"].includes(event.action);
    }

    function drawReplayEngagements(engagements) {
      for (const engagement of engagements) {
        const start = Number(engagement.start_time_seconds);
        const end = Number(engagement.end_time_seconds);
        if (!engagement.map || !Number.isFinite(start) || !Number.isFinite(end)) continue;
        if (activeTimelineTime < start || activeTimelineTime > end + 6) continue;
        if (!replayParticipantVisible(timelineTrackByAccount(engagement.actor_account_id))) continue;
        const point = canvasPoint(engagement.map);
        const pulse = 18 + Math.sin(activeTimelineTime * 4) * 3;
        replayCtx.strokeStyle = engagement.evidence === "inferred_attack_activity"
          ? "rgba(105,184,232,0.74)"
          : engagement.outcome === "won"
            ? "rgba(75,208,160,0.75)"
            : engagement.outcome === "lost"
              ? "rgba(255,95,109,0.78)"
              : "rgba(255,213,79,0.72)";
        replayCtx.lineWidth = 3;
        replayCtx.setLineDash(engagement.evidence === "inferred_attack_activity" ? [2, 7] : [7, 5]);
        replayCtx.beginPath();
        replayCtx.arc(point.x, point.y, pulse, 0, Math.PI * 2);
        replayCtx.stroke();
        replayCtx.setLineDash([]);
      }
    }

    function drawDirectionArrow(start, end, color) {
      replayCtx.strokeStyle = color;
      replayCtx.fillStyle = color;
      replayCtx.lineWidth = 2;
      replayCtx.beginPath();
      replayCtx.moveTo(start.x, start.y);
      replayCtx.lineTo(end.x, end.y);
      replayCtx.stroke();
      const angle = Math.atan2(end.y - start.y, end.x - start.x);
      const size = 9;
      replayCtx.beginPath();
      replayCtx.moveTo(end.x, end.y);
      replayCtx.lineTo(end.x - Math.cos(angle - 0.55) * size, end.y - Math.sin(angle - 0.55) * size);
      replayCtx.lineTo(end.x - Math.cos(angle + 0.55) * size, end.y - Math.sin(angle + 0.55) * size);
      replayCtx.closePath();
      replayCtx.fill();
    }

    function drawShotBurst(point, color, age) {
      const radius = 4 + Math.max(0, age) * 3;
      replayCtx.strokeStyle = "#64d8ff";
      replayCtx.lineWidth = 2;
      replayCtx.beginPath();
      replayCtx.arc(point.x, point.y, Math.min(14, radius), 0, Math.PI * 2);
      replayCtx.stroke();
      drawCircle(point, 3, "#ffffff", color);
    }

    function drawThrowBurst(point, color, age) {
      const radius = Math.min(15, 5 + Math.max(0, age) * 2.5);
      replayCtx.strokeStyle = "#ffb74d";
      replayCtx.fillStyle = color;
      replayCtx.lineWidth = 2;
      replayCtx.beginPath();
      replayCtx.arc(point.x, point.y, radius, Math.PI * 0.15, Math.PI * 1.45);
      replayCtx.stroke();
      replayCtx.beginPath();
      replayCtx.moveTo(point.x + 2, point.y - 5);
      replayCtx.lineTo(point.x + 8, point.y - 10);
      replayCtx.lineTo(point.x + 7, point.y - 2);
      replayCtx.closePath();
      replayCtx.fill();
      drawDiamond(point, "#ffb74d", color);
    }

    function drawHitMarker(point, color, headshot, taken = false) {
      if (taken) drawSquare(point, headshot ? 14 : 10, "rgba(8,11,13,0.72)", color);
      else drawCircle(point, headshot ? 7 : 5, "rgba(8,11,13,0.72)", color);
      if (!headshot) return;
      replayCtx.fillStyle = color;
      replayCtx.font = "bold 11px Arial";
      replayCtx.fillText("H", point.x + 8, point.y - 7);
    }

    function drawEnvironmentalDamageMarker(point) {
      drawSquare(point, 12, "rgba(8,11,13,0.82)", "#c3ccd6");
      replayCtx.fillStyle = "#f5f7fa";
      replayCtx.font = "bold 11px Arial";
      replayCtx.fillText("!", point.x - 2, point.y + 4);
    }

    function drawDiamond(point, fill, stroke) {
      replayCtx.fillStyle = fill;
      replayCtx.strokeStyle = stroke;
      replayCtx.lineWidth = 3;
      replayCtx.beginPath();
      replayCtx.moveTo(point.x, point.y - 11);
      replayCtx.lineTo(point.x + 11, point.y);
      replayCtx.lineTo(point.x, point.y + 11);
      replayCtx.lineTo(point.x - 11, point.y);
      replayCtx.closePath();
      replayCtx.fill();
      replayCtx.stroke();
    }

    function drawDbnoMarker(point, caused, actorColor) {
      drawDiamond(point, caused ? "rgba(8,11,13,0.82)" : "#ff5f6d", caused ? "#ff9f43" : actorColor);
      replayCtx.strokeStyle = caused ? "#ff9f43" : "#ffffff";
      replayCtx.lineWidth = 3;
      replayCtx.beginPath();
      replayCtx.moveTo(point.x - 5, point.y);
      replayCtx.lineTo(point.x + 5, point.y);
      if (caused) {
        replayCtx.moveTo(point.x, point.y - 5);
        replayCtx.lineTo(point.x, point.y + 5);
      }
      replayCtx.stroke();
    }

    function drawReplaySelectedEvent() {
      const selected = selectedTimelineEvent();
      const mapPoint = timelineEventMapPoint(selected);
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

    function drawReplayCurrentEventCallout() {
      const event = selectedTimelineEvent() || currentPlaybackTimelineEvent();
      const mapPoint = timelineEventMapPoint(event);
      if (!event || !mapPoint) return;
      const point = canvasPoint(mapPoint);
      if (!canvasPointVisible(point, 34)) return;
      const presentation = timelineEventPresentation(event);
      const text = `${presentation.name} · ${replayActorName(event.source || {})}`.slice(0, 34);
      replayCtx.save();
      replayCtx.font = "bold 13px Arial";
      const width = Math.min(270, replayCtx.measureText(text).width + 38);
      const x = Math.max(6, Math.min(replayCanvas.width - width - 6, point.x + 18));
      const y = Math.max(34, Math.min(replayCanvas.height - 8, point.y - 18));
      replayCtx.fillStyle = "rgba(8,11,13,0.9)";
      replayCtx.fillRect(x, y - 24, width, 28);
      replayCtx.strokeStyle = replayEventToneColor(presentation.tone);
      replayCtx.lineWidth = 2;
      replayCtx.strokeRect(x, y - 24, width, 28);
      replayCtx.fillStyle = replayEventToneColor(presentation.tone);
      replayCtx.fillText(presentation.symbol, x + 8, y - 5);
      replayCtx.fillStyle = "#f5f7fa";
      replayCtx.fillText(text, x + 27, y - 5);
      replayCtx.restore();
    }

    function replayEventToneColor(tone) {
      const colors = {
        drop: "#54c8ff",
        landing: "#ffeb3b",
        shot: "#64d8ff",
        throw: "#ffb74d",
        attack: "#ffb74d",
        "hit-caused": "#ffd54f",
        "hit-taken": "#ff6b6b",
        environment: "#c3ccd6",
        "dbno-caused": "#ff9f43",
        "dbno-taken": "#ff5f6d",
        kill: "#ff5f6d",
        death: "#ff8a80",
        revive: "#45d6b0",
        engagement: "#ffd54f",
        activity: "#69b8e8",
        plane: "#69b8e8",
        care: "#ef9a9a",
      };
      return colors[tone] || "#cbd2d8";
    }

    function drawReplayPlayer(samples) {
      const current = replayTrackPosition(timelineTrackByAccount(activeTimeline?.player?.account_id), activeTimelineTime);
      if (!current) return;
      drawReplayActorMarker(canvasPoint(current), current.movement_mode, "#4bd0a0", true);
    }

    function drawReplayActorMarker(point, movementMode, color, isSelf) {
      const size = isSelf ? 9 : 7;
      if (movementMode === "vehicle") {
        replayCtx.fillStyle = color;
        replayCtx.strokeStyle = "#ffffff";
        replayCtx.lineWidth = 2;
        replayCtx.fillRect(point.x - size, point.y - size * 0.65, size * 2, size * 1.3);
        replayCtx.strokeRect(point.x - size, point.y - size * 0.65, size * 2, size * 1.3);
      } else if (movementMode === "airborne") {
        drawDiamond(point, color, "#ffffff");
      } else if (movementMode === "dbno") {
        drawCircle(point, size + 2, "rgba(8,11,13,0.8)", "#ff5f6d");
        replayCtx.strokeStyle = "#ff5f6d";
        replayCtx.lineWidth = 3;
        replayCtx.beginPath();
        replayCtx.moveTo(point.x - size, point.y);
        replayCtx.lineTo(point.x + size, point.y);
        replayCtx.stroke();
      } else {
        drawCircle(point, size, isSelf ? "#ffffff" : color, color);
      }
    }

    function drawReplayOverlay() {
      const focusTrack = timelineTrackByAccount(activeTimeline?.player?.account_id);
      const current = replayTrackPosition(focusTrack, activeTimelineTime);
      replayCtx.fillStyle = "rgba(17,24,32,0.82)";
      replayCtx.fillRect(12, 12, 430, 112);
      replayCtx.fillStyle = "#f5f7fa";
      replayCtx.font = "14px Arial";
      const playerName = activeTimeline?.player?.name || "알 수 없음";
      const matchId = compactIdentifier(activeTimelineArtifact?.match_id || activeTimeline?.match?.match_id || "-");
      replayCtx.fillText(`${playerName} · ${matchId}`, 24, 36);
      replayCtx.fillStyle = "#c3ccd6";
      replayCtx.fillText(`${displayCode(activeTimeline?.match?.map_name, "map")} · ${displayCode(activeTimeline?.match?.game_mode, "game_mode")} · ${formatReplayTime(activeTimelineTime)}`, 24, 60);
      const vehicleLabel = current?.vehicle_id
        ? displayCode(current.vehicle_id, "vehicle")
        : (current?.vehicle_label || "");
      const movementLabel = current?.movement_label || (replayTrackTerminalTime(focusTrack) !== null ? "생존 종료" : "위치 대기");
      replayCtx.fillText(`KST ${formatReplayKst(activeTimelineTime)} · ${movementLabel}${vehicleLabel ? ` (${vehicleLabel})` : ""}`, 24, 84);
      const viewportMode = replayPinnedEventId ? "선택 사건" : timelineFollowPlayer.checked ? "플레이어" : "맵 중앙";
      replayCtx.fillText(`확대 ${formatNumber(replayZoom(), 1)}x · 화면 중심 ${viewportMode}`, 24, 108);
    }

    function formatReplayKst(seconds) {
      const origin = Date.parse(activeTimeline?.time_origin_at_kst || "");
      if (!Number.isFinite(origin)) return "-";
      return new Intl.DateTimeFormat("ko-KR", {
        timeZone: "Asia/Seoul",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      }).format(new Date(origin + Number(seconds || 0) * 1000));
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

    function visiblePositionModeSegments(samples) {
      const segments = [];
      let previous = null;
      const trailSeconds = Math.max(0, Number(timelineTrailSeconds?.value || 0));
      const minimumTime = trailSeconds > 0 ? Math.max(0, activeTimelineTime - trailSeconds) : Number.NEGATIVE_INFINITY;
      for (const sample of samples) {
        const time = eventTime(sample);
        if (!sample.map || !Number.isFinite(time) || time > activeTimelineTime) continue;
        if (time < minimumTime) {
          previous = sample;
          continue;
        }
        const key = `${Number(sample.segment_id || 0)}:${sample.movement_mode || "on_foot"}`;
        const current = segments[segments.length - 1];
        if (!current || current.key !== key) {
          segments.push({
            key,
            mode: sample.movement_mode || "on_foot",
            samples: previous && Number(previous.segment_id || 0) === Number(sample.segment_id || 0)
              ? [previous, sample]
              : [sample],
          });
        } else {
          current.samples.push(sample);
        }
        previous = sample;
      }
      const current = interpolatedPosition(samples, activeTimelineTime);
      if (current) {
        const key = `${Number(current.segment_id || 0)}:${current.movement_mode || "on_foot"}`;
        const active = segments[segments.length - 1];
        const point = { ...current, map: current, time_seconds: activeTimelineTime };
        if (active?.key === key) active.samples.push(point);
        else segments.push({ key, mode: current.movement_mode || "on_foot", samples: previous ? [previous, point] : [point] });
      }
      return segments;
    }

    function replayTrackPosition(track, time) {
      if (!track) return null;
      const terminalTime = replayTrackTerminalTime(track);
      if (terminalTime !== null && time >= terminalTime + 0.75) return null;
      return interpolatedPosition(track.positions || [], time);
    }

    function replayTrackTerminalTime(track) {
      const terminal = (track?.combat_events || [])
        .filter((event) => ["death", "finished_taken"].includes(event.action))
        .map(eventTime)
        .filter(Number.isFinite)
        .sort((left, right) => left - right)[0];
      return Number.isFinite(terminal) ? terminal : null;
    }

    function interpolatedPosition(samples, time) {
      const valid = samples.filter((sample) => sample.map && Number.isFinite(eventTime(sample)));
      if (!valid.length) return null;
      if (time < eventTime(valid[0])) return null;
      let previous = valid[0];
      for (const sample of valid) {
        const sampleTime = eventTime(sample);
        if (sampleTime >= time) {
          const prevTime = eventTime(previous);
          const previousSegment = Number(previous.segment_id || 0);
          const sampleSegment = Number(sample.segment_id || 0);
          if (sampleSegment !== previousSegment) {
            if (time >= sampleTime) return { ...sample.map, ...replayMovementState(sample), segment_id: sampleSegment };
            return time - prevTime <= 15 ? { ...previous.map, ...replayMovementState(previous), segment_id: previousSegment } : null;
          }
          const ratio = sampleTime === prevTime ? 0 : Math.max(0, Math.min(1, (time - prevTime) / (sampleTime - prevTime)));
          return {
            x_pct: previous.map.x_pct + (sample.map.x_pct - previous.map.x_pct) * ratio,
            y_pct: previous.map.y_pct + (sample.map.y_pct - previous.map.y_pct) * ratio,
            segment_id: sampleSegment,
            ...replayMovementState(ratio >= 1 ? sample : previous),
          };
        }
        previous = sample;
      }
      return { ...previous.map, ...replayMovementState(previous), segment_id: Number(previous.segment_id || 0) };
    }

    function replayMovementState(sample) {
      return {
        movement_mode: sample?.movement_mode || "on_foot",
        movement_label: sample?.movement_label || "도보 이동",
        vehicle_label: sample?.vehicle_label || "",
        vehicle_id: sample?.vehicle_id || "",
      };
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
      if (replayPinnedMap) return replayPinnedMap;
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

    function drawSquare(point, size, fill, stroke) {
      const half = size / 2;
      replayCtx.fillStyle = fill;
      replayCtx.strokeStyle = stroke;
      replayCtx.lineWidth = 2;
      replayCtx.fillRect(point.x - half, point.y - half, size, size);
      replayCtx.strokeRect(point.x - half, point.y - half, size, size);
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
      if (activeTimelineSelectedEventId || replayPinnedMap) {
        activeTimelineSelectedEventId = null;
        activeTimelineDetailKey = "";
        replayPinnedMap = null;
        replayPinnedEventId = null;
        renderTimelineEventList();
      }
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
      activeTimelineCurrentEventId = null;
      activeTimelineDetailKey = "";
      replayPinnedMap = null;
      replayPinnedEventId = null;
      renderTimelineEventList();
      renderTimelineEventDetail(null);
      renderTimelineNowEvent(null);
      renderReplayFrame();
    }

    function drawEmptyReplayCanvas() {
      if (!replayCtx) return;
      drawReplayBackground(replayCanvas.width, replayCanvas.height);
      replayCtx.fillStyle = "#c3ccd6";
      replayCtx.font = "16px Arial";
      replayCtx.fillText("재생할 타임라인 없음", 24, 36);
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
      banner.textContent = `수집 완료: 신규 ${formatInteger(payload.result.queued_match_jobs)}개, 기존 ${formatInteger(payload.result.existing_match_jobs)}개`;
      await Promise.all([loadPlayers(), loadJobs()]);
    }

    async function processMatchJobs() {
      banner.textContent = "매치 상세 저장 중";
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
      banner.textContent = `상세 저장 완료: 저장 ${formatInteger(payload.result.stored_matches)}개, 텔레메트리 신규 ${formatInteger(payload.result.queued_telemetry_jobs)}개, 실패 ${formatInteger(payload.result.failed_jobs)}개`;
      await Promise.all([loadJobs(), loadTelemetryJobs()]);
    }

    async function processTelemetryJobs() {
      banner.textContent = "텔레메트리 저장 중";
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
      const mb = formatNumber(payload.result.stored_bytes / 1024 / 1024, 1);
      banner.textContent = `텔레메트리 저장 완료: 저장 ${formatInteger(payload.result.stored_telemetry)}개, ${mb}MB, 실패 ${formatInteger(payload.result.failed_jobs)}개`;
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
      combatStatus.textContent = `파싱 ${formatInteger(payload.result.parsed_payloads)}개, 요약 ${formatInteger(payload.result.combat_summaries)}개, 무기 ${formatInteger(payload.result.weapon_stats)}개, 실패 ${formatInteger(payload.result.failed_payloads)}개`;
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
      itemStatus.textContent = `파싱 ${formatInteger(payload.result.parsed_payloads)}개, 이벤트 ${formatInteger(payload.result.item_events)}개, 아이템 ${formatInteger(payload.result.item_stats)}개, 실패 ${formatInteger(payload.result.failed_payloads)}개`;
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
      movementStatus.textContent = `파싱 ${formatInteger(payload.result.parsed_payloads)}개, 위치 ${formatInteger(payload.result.position_samples)}개, 전투 위치 ${formatInteger(payload.result.combat_location_events)}개, 보급 ${formatInteger(payload.result.care_package_events)}개, 비행기 ${formatInteger(payload.result.plane_routes)}개, 자기장 ${formatInteger(payload.result.phase_events || 0)}개, 실패 ${formatInteger(payload.result.failed_payloads)}개`;
      banner.textContent = "위치 파싱 완료";
    }

    async function generateLoadoutSnapshots(force) {
      banner.textContent = force ? "장비 조합 스냅샷 재생성 중" : "장비 조합 스냅샷 생성 중";
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
      loadoutSnapshotStatus.textContent = `처리 ${formatInteger(payload.result.processed_matches)}개, 기존 ${formatInteger(payload.result.skipped_existing)}개, 아이템 없음 ${formatInteger(payload.result.skipped_no_items)}개, 실패 ${formatInteger(payload.result.failed_matches)}개, 장비 스냅샷 ${formatInteger(payload.result.generated_snapshots)}개`;
      banner.textContent = "장비 조합 스냅샷 생성 완료";
    }

    async function generateFightOutcomes(force) {
      banner.textContent = force ? "교전 결과 재생성 중" : "교전 결과 생성 중";
      const response = await fetch("/telemetry/fight-outcomes/process", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ limit: 50, force }),
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || response.statusText);
      }
      const payload = await response.json();
      const result = payload.result;
      fightOutcomeStatus.textContent = `처리 ${formatInteger(result.parsed_payloads)}개, 대상 ${formatInteger(result.tracked_players)}명, 승리 ${formatInteger(result.generated_wins)}개, 패배 ${formatInteger(result.generated_losses)}개, 장비 ${formatInteger(result.generated_loadout_snapshots)}개, 실패 ${formatInteger(result.failed_payloads)}개`;
      banner.textContent = "교전 결과 생성 완료";
    }

    async function generateMapSnapshots(force) {
      banner.textContent = force ? "지도 이미지 재생성 중" : "지도 이미지 생성 중";
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
      mapSnapshotStatus.textContent = `생성 ${formatInteger(payload.result.generated_snapshots)}개, 기존 ${formatInteger(payload.result.skipped_existing)}개, 위치 없음 ${formatInteger(payload.result.skipped_no_position)}개, 실패 ${formatInteger(payload.result.failed_snapshots)}개, 결과 파일 ${formatInteger(payload.result.artifacts.length)}개`;
      banner.textContent = "지도 이미지 생성 완료";
      await loadReplayArtifacts();
    }

    async function generateReplayTimelines(force) {
      banner.textContent = force ? "재생 타임라인 재생성 중" : "재생 타임라인 생성 중";
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
      timelineStatus.textContent = `생성 ${formatInteger(payload.result.generated_timelines)}개, 기존 ${formatInteger(payload.result.skipped_existing)}개, 위치 없음 ${formatInteger(payload.result.skipped_no_position)}개, 실패 ${formatInteger(payload.result.failed_timelines)}개, 결과 파일 ${formatInteger(payload.result.artifacts.length)}개`;
      banner.textContent = "재생 타임라인 생성 완료";
      await loadReplayArtifacts();
    }

    async function unregisterPlayer(shard, accountId) {
      try {
        await postJson("/players/unregister", { shard, account_id: accountId });
        await loadPlayers();
        banner.textContent = "수집 중지 완료";
      } catch (error) {
        banner.textContent = `수집 중지 오류: ${error.message}`;
      }
    }

    async function updatePlayerManagement(button) {
      const field = button.dataset.playerManagement;
      if (!field) return;
      const nextValue = button.dataset.nextValue === "true";
      await postJson("/players/manage", {
        shard: button.dataset.shard,
        account_id: button.dataset.accountId,
        [field]: nextValue,
      });
      await loadPlayers();
      banner.textContent = field === "active"
        ? `수집 관리를 ${nextValue ? "시작" : "중지"}했습니다.`
        : `전적 공개 범위를 ${nextValue ? "공개" : "비공개"}로 변경했습니다.`;
    }

    async function addPlayerDiscordRegistration(button) {
      const editor = button.closest(".player-discord-editor");
      const guildSelect = editor?.querySelector("[data-player-discord-guild]");
      const channelInput = editor?.querySelector("[data-player-discord-channel]");
      const guildId = String(guildSelect?.value || "").trim();
      if (!guildId) throw new Error("추가할 Discord 서버를 선택하세요.");
      await postJson("/players/discord-registrations", {
        shard: button.dataset.shard,
        account_id: button.dataset.accountId,
        guild_id: guildId,
        channel_id: String(channelInput?.value || "").trim() || null,
      });
      await loadDiscordGuilds();
      await loadPlayers();
      banner.textContent = `${discordGuildName(guildId)} 등록을 추가했습니다.`;
    }

    async function removePlayerDiscordRegistration(button) {
      const guildId = String(button.dataset.guildId || "");
      const guildName = discordGuildName(guildId);
      await postJson("/players/discord-registrations/remove", {
        shard: button.dataset.shard,
        account_id: button.dataset.accountId,
        guild_id: guildId,
      });
      await loadDiscordGuilds();
      await loadPlayers();
      banner.textContent = `${guildName} 등록을 해제했습니다. 다른 서버 등록과 수집 데이터는 유지됩니다.`;
    }

    async function requestJson(url, method, payload = null) {
      const options = { method, headers: {} };
      if (payload !== null) {
        options.headers["Content-Type"] = "application/json";
        options.body = JSON.stringify(payload);
      }
      const response = await fetch(url, options);
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || response.statusText);
      }
      return response.json();
    }

    async function postJson(url, payload) {
      return requestJson(url, "POST", payload);
    }

    async function saveStorageSettings(event) {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const payload = await postJson("/settings/storage", {
        raw_data_dir: String(form.get("raw_data_dir") || "").trim(),
        replay_data_dir: String(form.get("replay_data_dir") || "").trim(),
        backup_data_dir: String(form.get("backup_data_dir") || "").trim(),
        quarantine_data_dir: String(form.get("quarantine_data_dir") || "").trim(),
        raw_compression: String(form.get("raw_compression") || "gzip"),
      });
      storageSettingsStatus.textContent = [
        `원본 ${formatStoragePathStatus(payload.storage_status?.raw_data_dir)}`,
        `리플레이 ${formatStoragePathStatus(payload.storage_status?.replay_data_dir)}`,
        `백업 ${formatStoragePathStatus(payload.storage_status?.backup_data_dir)}`,
        `격리 ${formatStoragePathStatus(payload.storage_status?.quarantine_data_dir)}`,
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
        `${formatInteger(payload.collector.poll_interval_seconds)}초`,
        `${formatInteger(payload.collector.cycle_player_limit)}명`,
        `조회 묶음 ${formatInteger(payload.collector.player_lookup_chunk_size)}명`,
      ].join(" / ");
      await loadStatus();
    }

    async function loadCollectorWorkerStatus() {
      const payload = await requestJson("/collector/worker/status", "GET");
      renderCollectorWorkerStatus(payload.worker);
    }

    function renderCollectorWorkerStatus(worker) {
      if (!worker) {
        collectorWorkerStatus.textContent = "자동 수집기 상태를 확인할 수 없습니다.";
        setRailStatus(railCollector, "상태 오류", "error");
        return;
      }
      const state = worker.running
        ? (worker.stop_requested ? "stopping" : "running")
        : "stopped";
      setRailStatus(
        railCollector,
        state === "running" ? "실행 중" : (state === "stopping" ? "종료 중" : "중지"),
        state === "running" ? "ok" : (state === "stopping" ? "warning" : ""),
      );
      const lastCycle = worker.last_cycle;
      const lastSummary = lastCycle
        ? [
            `최근 완료 ${lastCycle.finished_at_kst || "-"}`,
            `새 매치 ${formatInteger(lastCycle.collection?.queued_match_jobs)}`,
            `저장 매치 ${formatInteger(lastCycle.match_jobs?.stored_matches)}`,
            `저장 텔레메트리 ${formatInteger(lastCycle.telemetry_jobs?.stored_telemetry)}`,
          ].join(" / ")
        : "아직 실행 이력 없음";
      const stateLabel = { running: "실행 중", stopping: "종료 중", stopped: "중지" }[state] || "알 수 없음";
      collectorWorkerStatus.textContent = [
        `자동 수집기 ${stateLabel}`,
        `실행 ${formatInteger(worker.cycle_count || 0)}회`,
        worker.next_run_at_kst ? `다음 실행 ${worker.next_run_at_kst}` : null,
        worker.last_error ? `오류 ${worker.last_error}` : null,
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
      const payload = await requestJson("/post-processing/worker/status", "GET");
      renderPostProcessingWorkerStatus(payload.worker);
    }

    function renderPostProcessingWorkerStatus(worker) {
      if (!worker) {
        postProcessingWorkerStatus.textContent = "자동 후처리 상태를 확인할 수 없습니다.";
        setRailStatus(railPostProcessing, "상태 오류", "error");
        return;
      }
      const state = worker.running
        ? (worker.stop_requested ? "stopping" : "running")
        : "stopped";
      setRailStatus(
        railPostProcessing,
        state === "running" ? "실행 중" : (state === "stopping" ? "종료 중" : "중지"),
        state === "running" ? "ok" : (state === "stopping" ? "warning" : ""),
      );
      const lastCycle = worker.last_cycle;
      const lastSummary = lastCycle
        ? [
            `최근 완료 ${lastCycle.finished_at_kst || "-"}`,
            `전투 ${formatInteger(lastCycle.combat?.parsed_payloads)}`,
            `행동 ${formatInteger(lastCycle.activity?.parsed_payloads)}`,
            `아이템 ${formatInteger(lastCycle.items?.parsed_payloads)}`,
            `이동 ${formatInteger(lastCycle.movement?.parsed_payloads)}`,
            `장비 조합 ${formatInteger(lastCycle.loadout_snapshots?.generated_snapshots)}`,
            `지도 ${formatInteger(lastCycle.map_snapshots?.generated_snapshots)}`,
            `타임라인 ${formatInteger(lastCycle.replay_timelines?.generated_timelines)}`,
          ].join(" / ")
        : "아직 실행 이력 없음";
      const stateLabel = { running: "실행 중", stopping: "종료 중", stopped: "중지" }[state] || "알 수 없음";
      postProcessingWorkerStatus.textContent = [
        `자동 후처리 ${stateLabel}`,
        `실행 ${formatInteger(worker.cycle_count || 0)}회`,
        worker.next_run_at_kst ? `다음 실행 ${worker.next_run_at_kst}` : null,
        worker.last_error ? `오류 ${worker.last_error}` : null,
        lastSummary,
      ].filter(Boolean).join(" / ");
    }

    async function startPostProcessingWorker(event) {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const payload = await postJson("/post-processing/worker/start", {
        combat_limit: Number(form.get("combat_limit") || 10),
        activity_limit: Number(form.get("activity_limit") || 10),
        item_limit: Number(form.get("item_limit") || 10),
        movement_limit: Number(form.get("movement_limit") || 10),
        loadout_limit: Number(form.get("loadout_limit") || 50),
        fight_outcome_limit: Number(form.get("fight_outcome_limit") || 10),
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
        ? `저장 완료: ${payload.web.local_web_base_url}`
        : "저장 완료: 사용 안 함";
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

    for (const formElement of [...analysisForms, timelinePlayerForm]) {
      const input = formElement.elements.target;
      input.addEventListener("input", () => {
        if (resolveRegisteredPlayer(input.value, formElement.elements.shard?.value || "")) {
          syncRegisteredPlayerForm(formElement).catch((error) => {
            banner.textContent = "오류: " + error.message;
          });
        }
      });
      input.addEventListener("change", () => {
        syncRegisteredPlayerForm(formElement).catch((error) => {
          banner.textContent = "오류: " + error.message;
        });
      });
      formElement.elements.shard?.addEventListener("change", () => {
        syncRegisteredPlayerForm(formElement).catch((error) => {
          banner.textContent = "오류: " + error.message;
        });
      });
    }
    for (const button of document.querySelectorAll("[data-reset-analysis-form]")) {
      button.addEventListener("click", async () => {
        const formElement = document.getElementById(button.dataset.resetAnalysisForm || "");
        if (!formElement) return;
        await resetAnalysisForm(formElement, { preservePlayer: formElement !== profileForm });
        if (formElement === workerRunFilterForm) {
          workerRunPage = {
            total: 0,
            limit: 20,
            offset: 0,
            worker_name: null,
            status: "all",
            quick_range: "custom",
            created_from_kst: "",
            created_to_kst: "",
            has_previous: false,
            has_next: false,
          };
          try {
            await loadWorkerRuns({
              worker_name: "all",
              status: "all",
              quick_range: "custom",
              created_from_kst: "",
              created_to_kst: "",
              limit: 20,
              offset: 0,
              updateUrl: true,
            });
          } catch (error) {
            banner.textContent = `오류: ${error.message}`;
            return;
          }
        }
        banner.textContent = "조회 조건을 초기화했습니다.";
      });
    }
    matchForm.elements.match_search.addEventListener("input", (event) => {
      renderMatchOptions(matchForm, event.currentTarget.value);
    });
    clearAnalysisPlayerButton.addEventListener("click", async () => {
      await clearAnalysisPlayerSelection();
      banner.textContent = "분석 대상을 해제했습니다.";
    });

    playersBody.addEventListener("click", async (event) => {
      const button = event.target instanceof Element ? event.target.closest("button") : null;
      if (!button) return;
      const isPlayerAction = button.dataset.playerManagement
        || button.hasAttribute("data-player-discord-add")
        || button.hasAttribute("data-player-discord-remove");
      if (!isPlayerAction) return;
      button.disabled = true;
      try {
        if (button.dataset.playerManagement) {
          await updatePlayerManagement(button);
        } else if (button.hasAttribute("data-player-discord-add")) {
          await addPlayerDiscordRegistration(button);
        } else {
          await removePlayerDiscordRegistration(button);
        }
      } catch (error) {
        banner.textContent = `유저 관리 오류: ${error.message}`;
        if (button.isConnected) button.disabled = false;
      }
    });

    registerForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const formElement = event.currentTarget;
      const form = new FormData(formElement);
      try {
        await postJson("/players/register", {
          shard: form.get("shard"),
          current_name: form.get("current_name"),
          account_id: form.get("account_id") || null,
          public_profile: form.get("public_profile") === "true",
          guild_id: form.get("guild_id") || null,
          channel_id: form.get("channel_id") || null,
        });
        formElement.reset();
        applyPublicProfileDefault();
        await loadPlayers();
        banner.textContent = "유저 등록 완료";
      } catch (error) {
        banner.textContent = `유저 등록 오류: ${error.message}`;
      }
    });

    watchlistRegisterForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      try {
        const payload = await postJson("/watchlist/players", {
          shard: String(form.get("shard") || "steam"),
          player_name: String(form.get("player_name") || "").trim(),
          notify_name_change: Boolean(form.get("notify_name_change")),
          notify_kill: Boolean(form.get("notify_kill")),
          notify_repeated_engagement: Boolean(form.get("notify_repeated_engagement")),
          engagement_threshold: Number(form.get("engagement_threshold") || 3),
          notification_channel_ids: Array.from(watchlistRegisterChannelIds),
        });
        event.currentTarget.reset();
        watchlistRegisterChannelIds = new Set();
        watchlistGuildSelect.value = "";
        await loadWatchlistChannelOptions("register");
        await Promise.all([loadWatchlistPlayers(), loadWatchlistEncounters()]);
        banner.textContent = `${payload.player.current_name} 밴 플레이어 등록 완료 · Account ID 확인됨`;
      } catch (error) {
        watchlistStatus.textContent = `등록 오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });
    watchlistRegisterReset.addEventListener("click", () => {
      watchlistRegisterForm.reset();
      watchlistRegisterChannelIds = new Set();
      watchlistGuildSelect.value = "";
      loadWatchlistChannelOptions("register").catch(() => {});
    });
    watchlistGuildSelect.addEventListener("change", () => {
      loadWatchlistChannelOptions("register").catch((error) => {
        watchlistChannelStatus.textContent = `오류: ${error.message}`;
      });
    });
    watchlistChannelSelect.addEventListener("change", () => {
      watchlistChannelAdd.disabled = !watchlistChannelSelect.value;
    });
    watchlistChannelAdd.addEventListener("click", () => {
      if (!watchlistChannelSelect.value) return;
      watchlistRegisterChannelIds.add(String(watchlistChannelSelect.value));
      watchlistChannelSelect.value = "";
      watchlistChannelAdd.disabled = true;
      renderWatchlistChannelSelection("register");
    });
    watchlistChannelSelection.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-watchlist-channel-remove]");
      if (!button) return;
      watchlistRegisterChannelIds.delete(button.dataset.watchlistChannelRemove || "");
      renderWatchlistChannelSelection("register");
    });
    watchlistSearch.addEventListener("input", () => {
      window.clearTimeout(watchlistSearchTimer);
      watchlistSearchTimer = window.setTimeout(() => {
        loadWatchlistPlayers().catch((error) => { watchlistStatus.textContent = `오류: ${error.message}`; });
      }, 250);
    });
    watchlistIncludeInactive.addEventListener("change", () => {
      loadWatchlistPlayers().catch((error) => { watchlistStatus.textContent = `오류: ${error.message}`; });
    });
    watchlistReload.addEventListener("click", () => {
      Promise.all([loadWatchlistPlayers(), loadWatchlistEncounters()]).catch((error) => {
        watchlistStatus.textContent = `오류: ${error.message}`;
      });
    });
    watchlistScan.addEventListener("click", async () => {
      watchlistScan.disabled = true;
      watchlistStatus.textContent = "닉네임 동일 계정 확인과 저장 매치 조우 분석 중입니다.";
      try {
        const payload = await postJson("/watchlist/scan", { limit: 500, force: false, refresh_identity: true });
        await Promise.all([loadWatchlistPlayers(), loadWatchlistEncounters()]);
        watchlistStatus.textContent = `닉네임 ${formatInteger(payload.identity.checked)}명 확인 · 변경 ${formatInteger(payload.identity.changed)}명 · 조우 ${formatInteger(payload.encounters.analyzed_encounters)}건 분석 · 알림 ${formatInteger(payload.identity.alerts_created + payload.encounters.alerts_created)}건 생성`;
        banner.textContent = "밴 플레이어 확인 완료";
      } catch (error) {
        watchlistStatus.textContent = `확인 오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      } finally {
        watchlistScan.disabled = false;
      }
    });
    watchlistBody.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-watchlist-edit]");
      if (!button) return;
      try { openWatchlistEditor(button.dataset.watchlistEdit); }
      catch (error) { banner.textContent = `오류: ${error.message}`; }
    });
    watchlistEncounterBody.addEventListener("click", (event) => {
      const matchButton = event.target.closest("button[data-watchlist-match]");
      const replayButton = event.target.closest("button[data-watchlist-replay]");
      if (!matchButton && !replayButton) return;
      const matchId = (replayButton || matchButton).dataset.watchlistReplay
        || (replayButton || matchButton).dataset.watchlistMatch
        || "";
      openWatchlistMatch(matchId, Boolean(replayButton)).catch((error) => {
        banner.textContent = `매치 열기 오류: ${error.message}`;
      });
    });
    watchlistEditGuildSelect.addEventListener("change", () => {
      loadWatchlistChannelOptions("edit").catch((error) => {
        watchlistEditChannelStatus.textContent = `오류: ${error.message}`;
      });
    });
    watchlistEditChannelSelect.addEventListener("change", () => {
      watchlistEditChannelAdd.disabled = !watchlistEditChannelSelect.value;
    });
    watchlistEditChannelAdd.addEventListener("click", () => {
      if (!watchlistEditChannelSelect.value) return;
      watchlistEditChannelIds.add(String(watchlistEditChannelSelect.value));
      watchlistEditChannelSelect.value = "";
      watchlistEditChannelAdd.disabled = true;
      renderWatchlistChannelSelection("edit");
    });
    watchlistEditChannelSelection.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-watchlist-edit-channel-remove]");
      if (!button) return;
      watchlistEditChannelIds.delete(button.dataset.watchlistEditChannelRemove || "");
      renderWatchlistChannelSelection("edit");
    });
    watchlistEditForm.addEventListener("submit", (event) => {
      event.preventDefault();
      saveWatchlistEditor().catch((error) => {
        watchlistEditChannelStatus.textContent = `저장 오류: ${error.message}`;
      });
    });
    watchlistEditClose.addEventListener("click", () => watchlistEditDialog.close());
    watchlistEditCancel.addEventListener("click", () => watchlistEditDialog.close());
    watchlistEditDialog.addEventListener("click", (event) => {
      if (event.target === watchlistEditDialog) watchlistEditDialog.close();
    });

    intelligenceForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await loadPlayerIntelligence(event.currentTarget);
        banner.textContent = "종합 분석 완료";
      } catch (error) {
        intelligenceBody.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });
    intelligenceViewControls.addEventListener("click", (event) => {
      const button = event.target instanceof Element
        ? event.target.closest("[data-intelligence-view]")
        : null;
      if (!button) return;
      activeIntelligenceView = button.dataset.intelligenceView || "overview";
      renderIntelligenceReport();
    });
    intelligenceTrendGranularity.addEventListener("change", renderIntelligenceReport);
    intelligenceTrendMetric.addEventListener("change", renderIntelligenceReport);
    intelligenceBreakdownDimension.addEventListener("change", renderIntelligenceReport);

    profileForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const formElement = event.currentTarget;
      try {
        const player = selectedRegisteredPlayer(formElement);
        await setActiveAnalysisPlayer(player);
        await loadPlayerProfile(player.account_id, player.shard);
        clearRegisteredPlayerSearch(formElement);
        banner.textContent = "전적 조회 완료";
      } catch (error) {
        profileBody.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    profileBody.addEventListener("click", async (event) => {
      const button = event.target.closest("button[data-profile-match-id]");
      if (!button) return;
      try {
        const player = activeProfilePlayer;
        if (!player) throw new Error("먼저 전적을 조회하세요.");
        matchForm.elements.target.value = player.current_name;
        matchForm.elements.shard.value = player.shard;
        await syncRegisteredPlayerForm(matchForm);
        matchForm.elements.match_id.value = button.dataset.profileMatchId || "";
        await loadPlayerMatch(button.dataset.profileMatchId || "", player.account_id, player.shard);
        activateWorkspace("players", { focusId: "match-lookup", smooth: true });
        banner.textContent = "매치 상세 조회 완료";
      } catch (error) {
        banner.textContent = "오류: " + error.message;
      }
    });

    trendForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const formElement = event.currentTarget;
      try {
        await loadPlayerTrends(formElement);
        banner.textContent = "KST 추세 조회 완료";
      } catch (error) {
        trendSummary.textContent = `오류: ${error.message}`;
        trendBody.innerHTML = `<tr><td colspan="11">오류: ${escapeHtml(error.message)}</td></tr>`;
        trendCards.innerHTML = `<span class="result-caption">오류: ${escapeHtml(error.message)}</span>`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    timeInsightForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await loadTimeInsights(event.currentTarget);
        banner.textContent = "KST 시간대 분석 완료";
      } catch (error) {
        timeInsightBody.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });
    timeInsightForm.elements.metric.addEventListener("change", () => {
      if (activeTimeInsightReport) renderTimeInsights(String(timeInsightForm.elements.metric.value || "match_count"));
    });

    comparisonForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      comparisonBody.textContent = "비교 데이터를 불러오는 중...";
      try {
        await loadComparison(event.currentTarget);
        banner.textContent = "상세 비교 완료";
      } catch (error) {
        comparisonBody.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });
    comparisonForm.elements.comparison_type.addEventListener("change", () => {
      activeComparisonRows = [];
      comparisonBody.textContent = "비교 대기 중";
      renderComparisonPicker();
    });
    comparisonForm.elements.metric.addEventListener("change", () => {
      if (activeComparisonRows.length) renderComparisonResult();
    });
    comparisonForm.elements.trend_granularity.addEventListener("change", async () => {
      if (!activeComparisonRows.length) return;
      comparisonBody.textContent = "선택한 시간 단위로 비교 추세를 다시 불러오는 중...";
      try {
        await loadComparison(comparisonForm);
        activeComparisonView = "trend";
        setComparisonView("trend");
        banner.textContent = "비교 추세 단위 변경 완료";
      } catch (error) {
        comparisonBody.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });
    comparisonItemPicker.addEventListener("change", (event) => {
      const input = event.target instanceof HTMLInputElement ? event.target : null;
      if (!input) return;
      const checked = comparisonItemPicker.querySelectorAll('input[name="comparison_item"]:checked');
      if (checked.length > 5) {
        input.checked = false;
        banner.textContent = "비교 대상은 최대 5개까지 선택할 수 있습니다.";
      }
      updateComparisonSelectionCount();
    });
    comparisonViewControls.addEventListener("click", (event) => {
      const button = event.target instanceof Element ? event.target.closest("[data-comparison-view]") : null;
      if (button) setComparisonView(button.dataset.comparisonView || "chart");
    });
    comparisonReset.addEventListener("click", async () => {
      await resetAnalysisForm(comparisonForm);
      banner.textContent = "비교 조건을 초기화했습니다.";
    });

    weaponForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const formElement = event.currentTarget;
      try {
        await loadPlayerWeapon(formElement);
        banner.textContent = "무기 조회 완료";
      } catch (error) {
        weaponBody.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });
    weaponBody.addEventListener("click", (event) => {
      const button = event.target instanceof Element
        ? event.target.closest("button[data-weapon-trend-granularity]")
        : null;
      if (!button || !activeWeaponDetail) return;
      activeWeaponTrendGranularity = button.dataset.weaponTrendGranularity || "month";
      renderWeaponTrendChart();
    });
    weaponBody.addEventListener("change", (event) => {
      const select = event.target instanceof Element
        ? event.target.closest("select[data-weapon-trend-metric]")
        : null;
      if (!select || !activeWeaponDetail) return;
      activeWeaponTrendMetric = select.value || "fight_win_rate";
      renderWeaponTrendChart();
    });

    document.querySelector("#mapRegionForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await loadMapRegion(event.currentTarget);
        banner.textContent = "맵 지역 확인 완료";
      } catch (error) {
        mapRegionBody.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });
    recommendationForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const formElement = event.currentTarget;
      const form = new FormData(formElement);
      try {
        const player = selectedRegisteredPlayer(formElement);
        await loadPlayerRecommendations(
          player.account_id,
          player.shard,
          Number(form.get("min_matches") || 1),
        );
        banner.textContent = "추천 조회 완료";
      } catch (error) {
        recommendationBody.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    dropZoneForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const formElement = event.currentTarget;
      try {
        await loadDropZoneAnalysis(formElement);
        banner.textContent = "낙하 지역 분석 완료";
      } catch (error) {
        dropZoneBody.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    recommendationBody.addEventListener("click", async (event) => {
      const viewButton = event.target.closest("button[data-recommendation-view]");
      if (viewButton) {
        setRecommendationView(viewButton.dataset.recommendationView || "summary");
        return;
      }
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

    matchForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const formElement = event.currentTarget;
      const form = new FormData(formElement);
      try {
        const player = selectedRegisteredPlayer(formElement);
        await loadPlayerMatch(
          String(form.get("match_id") || ""),
          player.account_id,
          player.shard,
        );
        banner.textContent = "매치 조회 완료";
      } catch (error) {
        matchBody.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    matchExplorerForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await loadMatchExplorer({ offset: 0 });
        banner.textContent = "저장된 전체 매치 조회 완료";
      } catch (error) {
        matchExplorerStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });
    matchExplorerReset.addEventListener("click", async () => {
      matchExplorerForm.reset();
      matchExplorerForm.elements.telemetry_only.checked = true;
      activeMatchExplorerDetail = null;
      matchExplorerDetail.hidden = true;
      try {
        await loadMatchExplorer({ offset: 0 });
      } catch (error) {
        matchExplorerStatus.textContent = `오류: ${error.message}`;
      }
    });
    matchExplorerPrev.addEventListener("click", () => {
      loadMatchExplorer({ offset: Math.max(0, matchExplorerPage.offset - matchExplorerPage.limit) }).catch((error) => {
        matchExplorerStatus.textContent = `오류: ${error.message}`;
      });
    });
    matchExplorerNext.addEventListener("click", () => {
      loadMatchExplorer({ offset: matchExplorerPage.offset + matchExplorerPage.limit }).catch((error) => {
        matchExplorerStatus.textContent = `오류: ${error.message}`;
      });
    });
    matchExplorerBody.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-match-explorer-open]");
      if (!button) return;
      loadMatchExplorerDetail(button.dataset.matchExplorerOpen || "").catch((error) => {
        matchExplorerSummary.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      });
    });
    matchExplorerDetailClose.addEventListener("click", () => {
      matchExplorerDetail.hidden = true;
      activeMatchExplorerDetail = null;
    });
    matchExplorerReplay.addEventListener("click", () => {
      generateAndOpenMatchReplay().catch((error) => {
        matchExplorerSummary.insertAdjacentHTML(
          "afterbegin",
          `<div class="inline-notice danger">리플레이 오류: ${escapeHtml(error.message)}</div>`,
        );
        banner.textContent = `오류: ${error.message}`;
      });
    });
    matchExplorerDetailViews.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-match-detail-view]");
      if (button) setMatchExplorerDetailView(button.dataset.matchDetailView || "participants");
    });
    for (const panel of [matchExplorerParticipants, matchExplorerTeams]) {
      panel.addEventListener("click", (event) => {
        const playerButton = event.target.closest("button[data-match-event-player]");
        const teamButton = event.target.closest("button[data-match-event-team]");
        if (!playerButton && !teamButton) return;
        if (playerButton) matchEventFilterForm.elements.account_id.value = playerButton.dataset.matchEventPlayer || "";
        if (teamButton) matchEventFilterForm.elements.team_id.value = teamButton.dataset.matchEventTeam || "";
        setMatchExplorerDetailView("events");
      });
    }
    matchEventFilterForm.addEventListener("submit", (event) => {
      event.preventDefault();
      loadMatchExplorerEvents({ offset: 0 }).catch((error) => {
        matchEventStatus.textContent = `오류: ${error.message}`;
      });
    });
    matchEventFilterForm.elements.domain.addEventListener("change", () => {
      matchEventFilterForm.elements.event_type.value = "";
      loadMatchExplorerEvents({ offset: 0 }).catch((error) => {
        matchEventStatus.textContent = `오류: ${error.message}`;
      });
    });
    matchEventFilterReset.addEventListener("click", () => {
      matchEventFilterForm.reset();
      matchEventFilterForm.elements.domain.value = "combat";
      matchEventFilterForm.elements.limit.value = "100";
      loadMatchExplorerEvents({ offset: 0 }).catch((error) => {
        matchEventStatus.textContent = `오류: ${error.message}`;
      });
    });
    matchEventPrev.addEventListener("click", () => {
      loadMatchExplorerEvents({ offset: Math.max(0, matchEventPage.offset - matchEventPage.limit) }).catch((error) => {
        matchEventStatus.textContent = `오류: ${error.message}`;
      });
    });
    matchEventNext.addEventListener("click", () => {
      loadMatchExplorerEvents({ offset: matchEventPage.offset + matchEventPage.limit }).catch((error) => {
        matchEventStatus.textContent = `오류: ${error.message}`;
      });
    });
    matchEventBody.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-match-event-open]");
      if (!button) return;
      openMatchEventDetail(Number(button.dataset.matchEventOpen || 0));
    });
    matchEventDialogClose.addEventListener("click", () => matchEventDialog.close());
    matchEventDialog.addEventListener("click", (event) => {
      if (event.target === matchEventDialog) matchEventDialog.close();
    });

    rankingForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      try {
        await loadPlayerRanking(
          String(form.get("metric") || "kda"),
          String(form.get("shard") || "steam"),
          String(form.get("guild_id") || ""),
          Number(form.get("limit") || 10),
          Number(form.get("min_matches") || 1),
          String(form.get("active_only") || "true") === "true",
        );
        banner.textContent = "랭킹 조회 완료";
      } catch (error) {
        rankingBody.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    displaySettingsForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const mode = String(new FormData(displaySettingsForm).get("number_format") || "grouped");
      try {
        displaySettingsStatus.textContent = "표시 설정 저장 중";
        const payload = await postJson("/settings/display", { number_format: mode });
        activeNumberFormat = payload.display.number_format;
        try {
          window.localStorage.setItem("pubg-ai-number-format", activeNumberFormat);
        } catch (_error) {
          // The server-side setting remains authoritative when webview storage is unavailable.
        }
        applyDisplaySettings();
        banner.textContent = "숫자 표기 설정 적용 완료";
        window.location.reload();
      } catch (error) {
        displaySettingsStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });
    displaySettingsForm?.elements.number_format.addEventListener("change", () => {
      const previous = activeNumberFormat;
      activeNumberFormat = displaySettingsForm.elements.number_format.value || "grouped";
      if (displayNumberPreview) displayNumberPreview.textContent = formatInteger(59452);
      activeNumberFormat = previous;
    });

    storageSettingsForm.addEventListener("submit", async (event) => {
      try {
        await saveStorageSettings(event);
        banner.textContent = "저장 경로 설정 저장 완료";
      } catch (error) {
        event.preventDefault();
        storageSettingsStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    alertSettingsForm.addEventListener("submit", async (event) => {
      try {
        await saveAlertSettings(event);
        banner.textContent = "알림 설정 저장 완료";
      } catch (error) {
        event.preventDefault();
        alertSettingsStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    alertsBody.addEventListener("click", async (event) => {
      const workerRunButton = event.target instanceof Element
        ? event.target.closest("button[data-worker-run-from-alert]")
        : null;
      if (workerRunButton) {
        try {
          await loadWorkerRunDetail(workerRunButton.dataset.workerRunFromAlert || "", { scroll: true });
          banner.textContent = "알림에서 작업 실행 상세를 불러왔습니다.";
        } catch (error) {
          workerRunsStatus.textContent = `오류: ${error.message}`;
          banner.textContent = `오류: ${error.message}`;
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
          banner.textContent = "알림 확인 처리 완료";
        } else if (button.dataset.alertAction === "snooze") {
          await snoozeAlert(button.dataset.alertId || "", 60);
          banner.textContent = "알림 숨김 처리 완료";
        }
      } catch (error) {
        banner.textContent = `오류: ${error.message}`;
      }
    });

    alertHistoryFilterForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await loadAlertHistory({ offset: 0, updateUrl: true });
        banner.textContent = "알림 이력 조회 완료";
      } catch (error) {
        alertHistoryStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    alertHistoryExport.addEventListener("click", () => {
      exportAlertHistoryCsv();
    });

    alertHistoryCopyFilterLink.addEventListener("click", async () => {
      try {
        const url = await copyAlertHistoryFilterLink();
        banner.textContent = `알림 이력 조회 링크 복사 완료: ${url}`;
      } catch (error) {
        banner.textContent = `오류: ${error.message}`;
      }
    });

    for (const button of alertHistoryPresetButtons) {
      button.addEventListener("click", async () => {
        try {
          await applyAlertHistoryPreset(button.dataset.alertHistoryPreset || "");
          banner.textContent = "알림 이력 빠른 조건 조회 완료";
        } catch (error) {
          alertHistoryStatus.textContent = `오류: ${error.message}`;
          banner.textContent = `오류: ${error.message}`;
        }
      });
    }

    document.querySelector("#alerts").addEventListener("click", async (event) => {
      const workerRunButton = event.target instanceof Element
        ? event.target.closest("button[data-worker-run-from-alert]")
        : null;
      if (workerRunButton) {
        try {
          await loadWorkerRunDetail(workerRunButton.dataset.workerRunFromAlert || "", { scroll: true });
          banner.textContent = "알림과 연결된 자동 작업 상세 조회 완료";
        } catch (error) {
          workerRunsStatus.textContent = `오류: ${error.message}`;
          alertHistoryStatus.textContent = `오류: ${error.message}`;
          banner.textContent = `오류: ${error.message}`;
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
          if (alert) {
            await loadAlertHistoryDetail(alert);
          } else {
            await loadAlertHistoryDetailById(detailButton.dataset.alertDetailId || "");
          }
          banner.textContent = "알림 상세 조회 완료";
        } catch (error) {
          alertHistoryStatus.textContent = `오류: ${error.message}`;
          banner.textContent = `오류: ${error.message}`;
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
        if (!alert) throw new Error("알림 이력 항목을 불러오지 못했습니다.");
        await loadAlertHistoryDetail(alert, button.dataset.alertNoteType || "note", true);
        banner.textContent = "알림 상세 조회 완료";
      } catch (error) {
        alertHistoryStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
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
        banner.textContent = "알림 메모 저장 완료";
      } catch (error) {
        alertHistoryStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    alertHistoryDetail.addEventListener("click", async (event) => {
      const workerRunButton = event.target instanceof Element
        ? event.target.closest("button[data-worker-run-from-alert]")
        : null;
      if (workerRunButton) {
        try {
          closeAlertHistoryDialog();
          await loadWorkerRunDetail(workerRunButton.dataset.workerRunFromAlert || "", { scroll: true });
          banner.textContent = "알림에서 작업 실행 상세 조회 완료";
        } catch (error) {
          workerRunsStatus.textContent = `오류: ${error.message}`;
          alertHistoryStatus.textContent = `오류: ${error.message}`;
          banner.textContent = `오류: ${error.message}`;
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
          banner.textContent = "알림 확인 처리 완료";
        } else if (button.dataset.alertDetailAction === "snooze") {
          await snoozeAlert(button.dataset.alertId || "", 60);
          banner.textContent = "알림 숨김 처리 완료";
        }
      } catch (error) {
        alertHistoryStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    alertHistoryDialogClose.addEventListener("click", closeAlertHistoryDialog);
    alertHistoryDialog.addEventListener("click", (event) => {
      if (event.target === alertHistoryDialog) closeAlertHistoryDialog();
    });

    alertHistoryPrev.addEventListener("click", async () => {
      try {
        await loadAlertHistory({
          offset: Math.max(0, alertHistoryPage.offset - alertHistoryPage.limit),
          updateUrl: true,
        });
      } catch (error) {
        alertHistoryStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    alertHistoryNext.addEventListener("click", async () => {
      try {
        await loadAlertHistory({
          offset: alertHistoryPage.offset + alertHistoryPage.limit,
          updateUrl: true,
        });
      } catch (error) {
        alertHistoryStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    workerRunFilterForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await loadWorkerRuns({ offset: 0, updateUrl: true });
        banner.textContent = "작업 실행 이력을 불러왔습니다.";
      } catch (error) {
        workerRunsStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    workerRunsExport.addEventListener("click", () => {
      exportWorkerRunsCsv();
    });

    workerRunsCopyFilterLink.addEventListener("click", async () => {
      try {
        const url = await copyWorkerRunFilterLink();
        banner.textContent = `작업 실행 필터 링크를 복사했습니다: ${url}`;
      } catch (error) {
        banner.textContent = `오류: ${error.message}`;
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
        workerRunsStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    workerRunsNext.addEventListener("click", async () => {
      try {
        await loadWorkerRuns({
          offset: workerRunPage.offset + workerRunPage.limit,
          updateUrl: true,
        });
      } catch (error) {
        workerRunsStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    document.querySelector("#worker-runs").addEventListener("click", async (event) => {
      const detailButton = event.target instanceof Element
        ? event.target.closest("button[data-worker-run-detail-id]")
        : null;
      if (!detailButton) return;
      try {
        await loadWorkerRunDetail(detailButton.dataset.workerRunDetailId || "");
        banner.textContent = "자동 작업 상세 조회 완료";
      } catch (error) {
        workerRunsStatus.textContent = `오류: ${error.message}`;
        workerRunDetail.innerHTML = `<div class="status">오류: ${escapeHtml(error.message)}</div>`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    workerRunDetail.addEventListener("click", async (event) => {
      const copyButton = event.target instanceof Element
        ? event.target.closest("button[data-copy-worker-run-link]")
        : null;
      if (!copyButton) return;
      try {
        const url = await copyWorkerRunDetailLink(copyButton.dataset.copyWorkerRunLink || "");
        banner.textContent = `작업 실행 상세 링크를 복사했습니다: ${url}`;
      } catch (error) {
        banner.textContent = `오류: ${error.message}`;
      }
    });

    collectorSettingsForm.addEventListener("submit", async (event) => {
      try {
        await saveCollectorSettings(event);
        banner.textContent = "수집기 설정 저장 완료";
      } catch (error) {
        event.preventDefault();
        collectorSettingsStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    collectorWorkerForm.addEventListener("submit", async (event) => {
      try {
        await startCollectorWorker(event);
        banner.textContent = "자동 수집기 시작 완료";
      } catch (error) {
        event.preventDefault();
        collectorWorkerStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    collectorWorkerStop.addEventListener("click", async () => {
      try {
        await stopCollectorWorker();
        banner.textContent = "자동 수집기 중지 요청 완료";
      } catch (error) {
        collectorWorkerStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    postProcessingWorkerForm.addEventListener("submit", async (event) => {
      try {
        await startPostProcessingWorker(event);
        banner.textContent = "자동 후처리 시작 완료";
      } catch (error) {
        event.preventDefault();
        postProcessingWorkerStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    postProcessingWorkerStop.addEventListener("click", async () => {
      try {
        await stopPostProcessingWorker();
        banner.textContent = "자동 후처리 중지 요청 완료";
      } catch (error) {
        postProcessingWorkerStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    playerIntelligenceAuditRun.addEventListener("click", async () => {
      try {
        await runPlayerIntelligenceAudit();
        banner.textContent = "플레이어 데이터 품질 검사 완료";
      } catch (error) {
        playerIntelligenceAuditStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });
    rankingViewControls?.addEventListener("click", (event) => {
      const button = event.target instanceof Element ? event.target.closest("[data-ranking-view]") : null;
      if (!button) return;
      activeRankingView = button.dataset.rankingView || "table";
      renderPlayerRanking();
    });

    operationalDrillForm.addEventListener("submit", async (event) => {
      try {
        await runOperationalDrill(event);
        banner.textContent = "운영 훈련 완료";
      } catch (error) {
        event.preventDefault();
        operationalDrillsStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    operationalDrillsReload.addEventListener("click", async () => {
      try {
        await loadOperationalDrills();
        banner.textContent = "운영 훈련 이력 새로고침 완료";
      } catch (error) {
        operationalDrillsStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    operationalDrillsBody.addEventListener("click", (event) => {
      const button = event.target instanceof Element
        ? event.target.closest("button[data-operational-drill-id]")
        : null;
      if (!button) return;
      const record = operationalDrillRecords.find((item) => (
        String(item.id) === String(button.dataset.operationalDrillId || "")
      ));
      if (record) renderOperationalDrillDetail(record);
    });

    webSettingsForm.addEventListener("submit", async (event) => {
      try {
        await saveWebSettings(event);
        banner.textContent = "로컬 상세 링크 설정 저장 완료";
      } catch (error) {
        event.preventDefault();
        webSettingsStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    discordBotSettingsForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const wasRunning = Boolean(activeDiscordBotState.running);
      const previousPrefix = activeDiscordBotSettings.command_prefix || "!";
      const nextPrefix = String(event.currentTarget.elements.command_prefix.value || "!").trim();
      try {
        await saveActiveDiscordBotSettings({
          ...activeDiscordBotSettings,
          auto_start: event.currentTarget.elements.auto_start.checked,
          command_prefix: nextPrefix,
        });
        if (wasRunning && previousPrefix !== nextPrefix) {
          await postJson("/discord/bot/stop", {});
          await postJson("/discord/bot/start", {});
        }
        await loadDiscordBot();
        banner.textContent = wasRunning && previousPrefix !== nextPrefix
          ? "Discord 봇 설정 저장 및 접두사 적용 완료"
          : "Discord 봇 설정 저장 완료";
      } catch (error) {
        discordBotStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    discordBotStart.addEventListener("click", async () => {
      discordBotStart.disabled = true;
      try {
        await postJson("/discord/bot/start", {});
        await loadDiscordBot();
        banner.textContent = "Discord 봇 시작 요청 완료";
      } catch (error) {
        discordBotStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      } finally {
        discordBotStart.disabled = Boolean(activeDiscordBotState.running);
      }
    });

    discordBotStop.addEventListener("click", async () => {
      discordBotStop.disabled = true;
      try {
        await postJson("/discord/bot/stop", {});
        await loadDiscordBot();
        banner.textContent = "Discord 봇 중지 완료";
      } catch (error) {
        discordBotStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      } finally {
        discordBotStop.disabled = !activeDiscordBotState.running;
      }
    });

    discordBotSyncAll.addEventListener("click", async () => {
      discordBotSyncAll.disabled = true;
      try {
        await postJson("/discord/bot/sync", { guild_id: null });
        await loadDiscordBot();
        banner.textContent = "전체 Discord 서버 명령 동기화 완료";
      } catch (error) {
        discordBotStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      } finally {
        discordBotSyncAll.disabled = !activeDiscordBotState.ready;
      }
    });

    discordBotRefreshGuilds.addEventListener("click", async () => {
      const confirmed = window.confirm(
        "현재 앱 관리 봇이 참여하지 않은 이전 서버 목록과 해당 서버 전용 설정을 정리할까요? "
        + "플레이어 분석 데이터와 전체 서버 권한은 유지됩니다.",
      );
      if (!confirmed) return;
      discordBotRefreshGuilds.disabled = true;
      try {
        const result = await loadDiscordGuilds({ sync: true, pruneStale: true });
        await Promise.all([loadDiscordBot(), loadDiscordPermissions(), loadDiscordScopes()]);
        const catalogCount = (result?.removed_catalog_guild_ids || []).length;
        const settingsCount = (result?.removed_settings_guild_ids || []).length;
        banner.textContent = `앱 관리 봇 서버 ${formatInteger(result?.managed_guild_count || 0)}개 동기화 · 이전 서버 목록 ${formatInteger(catalogCount)}개 · 서버 전용 설정 ${formatInteger(settingsCount)}개 정리`;
      } catch (error) {
        discordBotStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      } finally {
        discordBotRefreshGuilds.disabled = false;
      }
    });

    pubgApiKeyForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const value = String(event.currentTarget.elements.value.value || "");
      try {
        await postJson("/settings/secrets/pubg", { value });
        pubgApiKeyForm.reset();
        await Promise.all([loadStatus(), loadDiscordBot()]);
        banner.textContent = "PUBG API 키 저장 완료";
      } catch (error) {
        pubgApiKeyStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    discordTokenForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const value = String(event.currentTarget.elements.value.value || "");
      const wasRunning = Boolean(activeDiscordBotState.running);
      try {
        if (wasRunning) await postJson("/discord/bot/stop", {});
        await postJson("/settings/secrets/discord", { value });
        discordTokenForm.reset();
        const syncResult = await loadDiscordGuilds({ sync: true });
        if (wasRunning) await postJson("/discord/bot/start", {});
        await Promise.all([loadStatus(), loadDiscordBot()]);
        banner.textContent = `앱 관리 봇 ${syncResult?.managed_bot?.username || ""} 연결 · 서버 ${formatInteger(syncResult?.managed_guild_count || 0)}개`;
      } catch (error) {
        discordTokenStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    pubgApiKeyClear.addEventListener("click", async () => {
      if (!window.confirm("저장된 PUBG API 키를 삭제할까요?")) return;
      try {
        await requestJson("/settings/secrets/pubg", "DELETE");
        pubgApiKeyForm.reset();
        await Promise.all([loadStatus(), loadDiscordBot()]);
        banner.textContent = "PUBG API 키 삭제 완료";
      } catch (error) {
        banner.textContent = `오류: ${error.message}`;
      }
    });

    discordTokenClear.addEventListener("click", async () => {
      if (!window.confirm("Discord 봇을 중지하고 저장된 토큰을 삭제할까요?")) return;
      try {
        await requestJson("/settings/secrets/discord", "DELETE");
        discordTokenForm.reset();
        await Promise.all([loadStatus(), loadDiscordBot()]);
        banner.textContent = "Discord 봇 토큰 삭제 완료";
      } catch (error) {
        banner.textContent = `오류: ${error.message}`;
      }
    });

    discordBotGuildSelect.addEventListener("change", () => {
      loadDiscordBotGuildSelection();
      verifyDiscordBotGuildCommands().catch((error) => {
        discordBotCommandsStatus.textContent = `공개 상태 확인 오류: ${error.message}`;
      });
    });
    discordBotCommandSearch.addEventListener("input", renderDiscordBotCommandCatalog);
    discordBotCommandCatalog.addEventListener("change", (event) => {
      const checkbox = event.target instanceof HTMLInputElement && event.target.type === "checkbox"
        ? event.target
        : null;
      if (!checkbox) return;
      discordBotGuildUsesDefault = false;
      if (checkbox.checked) selectedDiscordBotCommands.add(checkbox.value);
      else selectedDiscordBotCommands.delete(checkbox.value);
      renderDiscordBotCommandCatalog();
    });
    discordBotCommandsAll.addEventListener("click", () => {
      discordBotGuildUsesDefault = false;
      selectedDiscordBotCommands = new Set(activeDiscordCommandCatalog.map((command) => command.name));
      renderDiscordBotCommandCatalog();
    });
    discordBotCommandsNone.addEventListener("click", () => {
      discordBotGuildUsesDefault = false;
      selectedDiscordBotCommands = new Set();
      renderDiscordBotCommandCatalog();
    });
    discordBotCommandsDefault.addEventListener("click", () => {
      discordBotGuildUsesDefault = true;
      selectedDiscordBotCommands = new Set(activeDiscordCommandCatalog.map((command) => command.name));
      renderDiscordBotCommandCatalog();
    });
    discordBotCommandsVerify.addEventListener("click", () => {
      verifyDiscordBotGuildCommands().catch((error) => {
        discordBotCommandsStatus.textContent = `공개 상태 확인 오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      });
    });
    discordBotCommandsSave.addEventListener("click", async () => {
      const guildId = String(discordBotGuildSelect.value || "");
      if (!guildId) {
        banner.textContent = "Discord 서버를 선택하세요.";
        return;
      }
      discordBotCommandsSave.disabled = true;
      try {
        discordBotCommandsStatus.textContent = "설정을 저장하고 Discord 공개 목록을 확인하는 중입니다.";
        const payload = await postJson("/discord/bot/guild-commands", {
          guild_id: guildId,
          use_default: discordBotGuildUsesDefault,
          commands: Array.from(selectedDiscordBotCommands).sort(),
        });
        activeDiscordBotSettings = payload.discord_bot || activeDiscordBotSettings;
        renderDiscordBotState({ bot: payload.bot || activeDiscordBotState });
        loadDiscordBotGuildSelection();
        if (payload.in_sync === true) {
          const message = `${discordGuildName(guildId)} 명령 ${formatInteger(payload.actual_commands?.length || 0)}개 공개 확인 완료`;
          discordBotCommandsStatus.textContent = message;
          banner.textContent = message;
        } else if (payload.global_commands?.length) {
          discordBotCommandsStatus.textContent = `전역 잔존 명령 ${formatInteger(payload.global_commands.length)}개가 확인되었습니다. Discord 전파 후 다시 확인하세요.`;
          banner.textContent = "서버별 명령은 저장됐지만 전역 잔존 명령이 아직 제거되지 않았습니다.";
        } else if (payload.verification_error) {
          discordBotCommandsStatus.textContent = `설정은 저장됐지만 Discord 확인 실패: ${payload.verification_error}`;
          banner.textContent = "명령 설정은 저장됐습니다. Discord 연결 상태를 확인한 뒤 다시 동기화하세요.";
        } else if (payload.synchronized) {
          discordBotCommandsStatus.textContent = "Discord 동기화는 완료됐지만 실제 공개 목록이 설정과 다릅니다.";
          banner.textContent = "Discord 공개 명령 목록 불일치를 확인했습니다.";
        } else {
          discordBotCommandsStatus.textContent = "설정 저장 완료 · 봇이 연결되면 자동으로 동기화됩니다.";
          banner.textContent = `${discordGuildName(guildId)} 명령 공개 설정 저장 완료`;
        }
      } catch (error) {
        discordBotCommandsStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      } finally {
        discordBotCommandsSave.disabled = false;
      }
    });

    discordGrantForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const formElement = event.currentTarget;
      const form = new FormData(formElement);
      try {
        const member = resolveDiscordMemberSelection(formElement, discordGrantMemberQuery);
        const memberGuildId = String(form.get("guild_id") || "");
        await postJson("/discord/permissions/grant", {
          user_id: member.user_id,
          group: form.get("group"),
          guild_id: form.get("global_scope") ? null : memberGuildId,
          member_label: member.display_name,
          member_guild_id: memberGuildId,
        });
        discordGrantMemberQuery.value = "";
        formElement.elements.user_id.value = "";
        await loadDiscordPermissions();
        banner.textContent = `${member.display_name} 사용자에게 Discord 권한을 추가했습니다.`;
      } catch (error) {
        banner.textContent = `오류: ${error.message}`;
      }
    });

    discordCommandSearch.addEventListener("input", renderDiscordCommandCatalog);
    discordCommandCatalog.addEventListener("change", (event) => {
      const checkbox = event.target instanceof HTMLInputElement && event.target.type === "checkbox"
        ? event.target
        : null;
      if (!checkbox) return;
      if (checkbox.checked) {
        selectedDiscordGroupCommands.add(checkbox.value);
      } else {
        selectedDiscordGroupCommands.delete(checkbox.value);
      }
      discordCommandGroupStatus.textContent = `전체 ${formatInteger(activeDiscordCommandCatalog.length)}개 · 선택 ${formatInteger(selectedDiscordGroupCommands.size)}개`;
    });
    discordCommandGroupReset.addEventListener("click", resetDiscordCommandGroupEditor);
    discordCommandGroupForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const group = String(event.currentTarget.elements.group.value || "").trim();
      try {
        await requestJson(
          `/discord/permissions/groups/${encodeURIComponent(group)}`,
          "PUT",
          { commands: Array.from(selectedDiscordGroupCommands) },
        );
        resetDiscordCommandGroupEditor();
        await loadDiscordPermissions();
        banner.textContent = "Discord 사용자 권한 그룹이 저장되었습니다.";
      } catch (error) {
        discordCommandGroupStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });
    discordCommandGroupsBody.addEventListener("click", async (event) => {
      const button = event.target instanceof Element
        ? event.target.closest("button[data-discord-group-action]")
        : null;
      if (!button) return;
      const group = button.dataset.group || "";
      const action = button.dataset.discordGroupAction;
      if (action === "edit" || action === "clone") {
        editDiscordCommandGroup(group, { clone: action === "clone" });
        return;
      }
      if (action !== "delete") return;
      try {
        await requestJson(
          `/discord/permissions/groups/${encodeURIComponent(group)}`,
          "DELETE",
        );
        resetDiscordCommandGroupEditor();
        await loadDiscordPermissions();
        banner.textContent = "Discord 사용자 권한 그룹이 삭제되었습니다.";
      } catch (error) {
        banner.textContent = `오류: ${error.message}`;
      }
    });
    discordCommandAliasReset.addEventListener("click", resetDiscordCommandAliasEditor);
    discordCommandAliasForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const alias = String(event.currentTarget.elements.alias.value || "").trim();
      const target = String(event.currentTarget.elements.target_command.value || "").trim();
      try {
        await requestJson(
          `/discord/permissions/aliases/${encodeURIComponent(alias)}`,
          "PUT",
          { target_command: target },
        );
        resetDiscordCommandAliasEditor();
        await loadDiscordPermissions();
        banner.textContent = "Discord 접두사 명령 별칭이 저장되었습니다.";
      } catch (error) {
        banner.textContent = `오류: ${error.message}`;
      }
    });
    discordCommandAliasesBody.addEventListener("click", async (event) => {
      const button = event.target instanceof Element
        ? event.target.closest("button[data-discord-alias-action]")
        : null;
      if (!button) return;
      const alias = button.dataset.alias || "";
      if (button.dataset.discordAliasAction === "edit") {
        discordCommandAliasForm.elements.alias.value = alias;
        discordCommandAliasForm.elements.alias.readOnly = true;
        discordCommandAliasForm.elements.target_command.value = button.dataset.target || "";
        discordCommandAliasForm.elements.target_command.focus();
        return;
      }
      try {
        await requestJson(
          `/discord/permissions/aliases/${encodeURIComponent(alias)}`,
          "DELETE",
        );
        resetDiscordCommandAliasEditor();
        await loadDiscordPermissions();
        banner.textContent = "Discord 접두사 명령 별칭이 삭제되었습니다.";
      } catch (error) {
        banner.textContent = `오류: ${error.message}`;
      }
    });

    discordAdminForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const formElement = event.currentTarget;
      const form = new FormData(formElement);
      try {
        const member = resolveDiscordMemberSelection(formElement, discordAdminMemberQuery);
        await postJson("/discord/global-admins/add", {
          user_id: member.user_id,
          member_label: member.display_name,
          member_guild_id: form.get("member_guild_id"),
        });
        discordAdminMemberQuery.value = "";
        formElement.elements.user_id.value = "";
        await loadDiscordPermissions();
        banner.textContent = `${member.display_name} 사용자를 Discord 전역 관리자로 추가했습니다.`;
      } catch (error) {
        banner.textContent = `오류: ${error.message}`;
      }
    });

    discordScopeForm.elements.guild_id.addEventListener("change", loadDiscordScopeSelection);
    discordScopeMode.addEventListener("change", renderDiscordRankingGuildSelection);
    discordRankingGuildCheckboxes.addEventListener("change", (event) => {
      const checkbox = event.target instanceof HTMLInputElement && event.target.type === "checkbox"
        ? event.target
        : null;
      if (!checkbox) return;
      if (checkbox.checked) editedDiscordRankingGuildIds.add(checkbox.value);
      else editedDiscordRankingGuildIds.delete(checkbox.value);
      discordRankingGuildStatus.textContent = `랭킹에 포함할 서버 ${formatInteger(editedDiscordRankingGuildIds.size)}개 선택`;
    });
    discordScopeReset.addEventListener("click", () => {
      discordScopeForm.reset();
      editedDiscordRankingGuildIds = new Set();
      renderDiscordRankingGuildSelection();
    });

    discordScopeForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const formElement = event.currentTarget;
      const form = new FormData(formElement);
      const guildId = String(form.get("guild_id") || "").trim();
      const scope = String(form.get("scope") || "guild");
      if (!guildId) {
        banner.textContent = "서버를 선택하세요.";
        return;
      }
      if (scope === "guild" && !editedDiscordRankingGuildIds.size) {
        banner.textContent = "랭킹에 포함할 Discord 서버를 한 개 이상 선택하세요.";
        return;
      }
      try {
        const selectedGuilds = {
          ...(activeDiscordScopes.guild_ranking_selected_guild_ids || {}),
        };
        if (scope === "guild") selectedGuilds[guildId] = Array.from(editedDiscordRankingGuildIds).sort();
        else delete selectedGuilds[guildId];
        await saveDiscordScopes({
          guild_ranking_scopes: {
            ...(activeDiscordScopes.guild_ranking_scopes || {}),
            [guildId]: scope,
          },
          guild_ranking_selected_guild_ids: selectedGuilds,
          public_profile_default: activeDiscordScopes.public_profile_default !== false,
        });
        formElement.reset();
        editedDiscordRankingGuildIds = new Set();
        renderDiscordRankingGuildSelection();
        banner.textContent = "Discord 랭킹 범위를 저장했습니다.";
      } catch (error) {
        banner.textContent = `오류: ${error.message}`;
      }
    });

    publicProfileDefaultForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      try {
        await saveDiscordScopes({
          guild_ranking_scopes: activeDiscordScopes.guild_ranking_scopes || {},
          guild_ranking_selected_guild_ids: activeDiscordScopes.guild_ranking_selected_guild_ids || {},
          public_profile_default: form.get("public_profile_default") === "true",
        });
        banner.textContent = "공개 프로필 기본값 저장 완료";
      } catch (error) {
        banner.textContent = `오류: ${error.message}`;
      }
    });

    discordScopesBody.addEventListener("click", async (event) => {
      const button = event.target instanceof Element
        ? event.target.closest("button[data-discord-scope-action]")
        : null;
      if (!button) return;

      const guildId = button.dataset.guildId || "";
      const nextGuildScopes = { ...(activeDiscordScopes.guild_ranking_scopes || {}) };
      const nextSelectedGuilds = { ...(activeDiscordScopes.guild_ranking_selected_guild_ids || {}) };
      delete nextGuildScopes[guildId];
      delete nextSelectedGuilds[guildId];
      try {
        await saveDiscordScopes({
          guild_ranking_scopes: nextGuildScopes,
          guild_ranking_selected_guild_ids: nextSelectedGuilds,
          public_profile_default: activeDiscordScopes.public_profile_default !== false,
        });
        banner.textContent = "Discord 서버 범위 삭제 완료";
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

    dataDeletionDetail.addEventListener("click", async (event) => {
      const button = event.target instanceof Element
        ? event.target.closest("button[data-deletion-contract-action]")
        : null;
      if (!button) return;
      const requestId = button.dataset.requestId || "";
      try {
        if (button.dataset.deletionContractAction === "capture") {
          await captureDataDeletionSnapshot(requestId);
        } else if (button.dataset.deletionContractAction === "confirm") {
          await confirmDataDeletionSnapshot(
            requestId,
            button.dataset.snapshotId || "",
            button.dataset.fingerprint || "",
          );
        } else if (button.dataset.deletionContractAction === "dry-run") {
          await createDataDeletionDryRunPlan(requestId);
        } else if (button.dataset.deletionContractAction === "rehearsal") {
          await runDataDeletionRehearsal(requestId, button.dataset.planId || "");
        } else if (button.dataset.deletionContractAction === "verify-backup") {
          await runDataDeletionBackupVerification(button);
        }
      } catch (error) {
        dataDeletionStatus.textContent = `오류: ${error.message}`;
      }
    });

    dataDeletionDetail.addEventListener("submit", async (event) => {
      const form = event.target instanceof Element
        ? event.target.closest("form[data-backup-build-form]")
        : null;
      if (!form) return;
      event.preventDefault();
      try {
        await buildDataDeletionBackupArtifacts(form);
      } catch (error) {
        dataDeletionStatus.textContent = `오류: ${error.message}`;
      }
    });

    dataDeletionDetail.addEventListener("submit", async (event) => {
      const form = event.target instanceof Element
        ? event.target.closest("form[data-restore-rehearsal-form]")
        : null;
      if (!form) return;
      event.preventDefault();
      try {
        await runDataDeletionBackupRestoreRehearsal(form);
      } catch (error) {
        dataDeletionStatus.textContent = `오류: ${error.message}`;
      }
    });

    dataDeletionDetail.addEventListener("submit", async (event) => {
      const form = event.target instanceof Element
        ? event.target.closest("form[data-quarantine-planner-form]")
        : null;
      if (!form) return;
      event.preventDefault();
      try {
        await runDataDeletionQuarantinePlanning(form);
      } catch (error) {
        dataDeletionStatus.textContent = `오류: ${error.message}`;
      }
    });

    dataDeletionDetail.addEventListener("submit", async (event) => {
      const form = event.target instanceof Element
        ? event.target.closest("form[data-quarantine-rehearsal-form]")
        : null;
      if (!form) return;
      event.preventDefault();
      try {
        await runDataDeletionQuarantineRehearsal(form);
      } catch (error) {
        dataDeletionStatus.textContent = `오류: ${error.message}`;
      }
    });

    dataDeletionDetail.addEventListener("submit", async (event) => {
      const form = event.target instanceof Element
        ? event.target.closest("form[data-combined-rehearsal-form]")
        : null;
      if (!form) return;
      event.preventDefault();
      try {
        await runDataDeletionCombinedRehearsal(form);
      } catch (error) {
        dataDeletionStatus.textContent = `오류: ${error.message}`;
      }
    });

    dataDeletionDetail.addEventListener("submit", async (event) => {
      const form = event.target instanceof Element
        ? event.target.closest("form[data-fault-matrix-form]")
        : null;
      if (!form) return;
      event.preventDefault();
      try {
        await runDataDeletionFaultMatrix(form);
      } catch (error) {
        dataDeletionStatus.textContent = `오류: ${error.message}`;
      }
    });

    dataDeletionDetail.addEventListener("submit", async (event) => {
      const form = event.target instanceof Element
        ? event.target.closest("form[data-review-packet-form]")
        : null;
      if (!form) return;
      event.preventDefault();
      try {
        await generateDataDeletionReviewPacket(form);
      } catch (error) {
        dataDeletionStatus.textContent = `오류: ${error.message}`;
      }
    });

    dataDeletionDetail.addEventListener("submit", async (event) => {
      const form = event.target instanceof Element
        ? event.target.closest("form[data-backup-evidence-form]")
        : null;
      if (!form) return;
      event.preventDefault();
      try {
        await recordDataDeletionBackupEvidence(form);
      } catch (error) {
        dataDeletionStatus.textContent = `오류: ${error.message}`;
      }
    });

    dataDeletionDetail.addEventListener("change", (event) => {
      const select = event.target instanceof Element
        ? event.target.closest("form[data-backup-evidence-form] select[name='prerequisite_key']")
        : null;
      if (!select) return;
      updateBackupEvidenceFields(select.closest("form[data-backup-evidence-form]"));
    });

    exportedReviewPacketVerifierForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await verifyExportedReviewPacket(exportedReviewPacketVerifierForm);
      } catch (error) {
        exportedReviewPacketVerifierResult.innerHTML = "";
        exportedReviewPacketVerifierStatus.textContent = `오류: ${error.message}`;
      }
    });

    exportedReviewPacketComparerForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await compareExportedReviewPackets(exportedReviewPacketComparerForm);
      } catch (error) {
        exportedReviewPacketComparerResult.innerHTML = "";
        exportedReviewPacketComparerStatus.textContent = `오류: ${error.message}`;
      }
    });

    dataDeletionFilterForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await loadDataDeletionRequests();
      } catch (error) {
        dataDeletionStatus.textContent = `오류: ${error.message}`;
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
        dataDeletionStatus.textContent = `오류: ${error.message}`;
      }
    });

    replayArtifactListForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = new FormData(replayArtifactListForm);
      replayArtifactFilter = { match_id: "", account_id: "", artifact_id: "" };
      try {
        await loadReplayArtifacts({
          account_id: String(form.get("account_id") || ""),
          artifact_type: String(form.get("artifact_type") || ""),
          limit: Number(form.get("limit") || 20),
          artifact_id: "",
        });
        banner.textContent = "2D 리플레이 저장 목록 조회 완료";
      } catch (error) {
        replayArtifactsStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    replayArtifactListReset.addEventListener("click", async () => {
      replayArtifactListForm.reset();
      replayArtifactFilter = { match_id: "", account_id: "", artifact_id: "" };
      try {
        await loadReplayArtifacts({
          match_id: "",
          account_id: "",
          artifact_type: "",
          limit: 20,
          artifact_id: "",
        });
        banner.textContent = "2D 리플레이 저장 목록 필터를 초기화했습니다.";
      } catch (error) {
        replayArtifactsStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    flightPathForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await loadFlightPathAnalysis();
        banner.textContent = "비행기 동선 분석 완료";
      } catch (error) {
        flightPathStatus.textContent = "오류: " + error.message;
        banner.textContent = "오류: " + error.message;
      }
    });
    flightPathReset.addEventListener("click", async () => {
      flightPathForm.reset();
      flightPathResult.hidden = true;
      flightPathResult.innerHTML = "";
      flightPathStatus.textContent = "분석 대기 중";
      try {
        await loadFlightPathMapCatalog();
      } catch (error) {
        flightPathStatus.textContent = "맵 목록 오류: " + error.message;
      }
      banner.textContent = "비행기 동선 필터를 초기화했습니다.";
    });
    flightPathPlayerSelect.addEventListener("change", async () => {
      const player = registeredPlayers.find((item) => item.account_id === flightPathPlayerSelect.value);
      try {
        if (!player) {
          for (const select of flightPathForm.querySelectorAll("select[data-catalog-facet]")) {
            if (select !== flightPathMapFilter) select.innerHTML = '<option value="">전체</option>';
          }
          await loadFlightPathMapCatalog();
          return;
        }
        flightPathForm.elements.shard.value = player.shard;
        applyPlayerCatalog(flightPathForm, await loadPlayerCatalog(player));
      } catch (error) {
        flightPathStatus.textContent = "필터 목록 오류: " + error.message;
      }
    });

    alertDiscordGuildSelect.addEventListener("change", async () => {
      try {
        await loadDiscordAlertChannels();
      } catch (error) {
        alertDiscordChannelsStatus.textContent = `오류: ${error.message}`;
      }
    });
    alertDiscordChannelSelect.addEventListener("change", () => {
      alertDiscordChannelAdd.disabled = !alertDiscordChannelSelect.value;
    });
    alertDiscordChannelAdd.addEventListener("click", () => {
      const channelId = alertDiscordChannelSelect.value;
      if (!channelId) return;
      activeAlertChannelIds.add(channelId);
      alertDiscordChannelSelect.value = "";
      alertDiscordChannelAdd.disabled = true;
      renderSelectedAlertChannels();
    });
    alertDiscordChannelSelection.addEventListener("click", (event) => {
      const button = event.target instanceof Element
        ? event.target.closest("button[data-alert-channel-remove]")
        : null;
      if (!button) return;
      activeAlertChannelIds.delete(button.dataset.alertChannelRemove || "");
      renderSelectedAlertChannels();
    });
    alertDiscordChannelsRefresh.addEventListener("click", async () => {
      alertDiscordChannelsRefresh.disabled = true;
      try {
        await loadDiscordGuilds({ sync: true });
        await loadDiscordAlertChannels();
        banner.textContent = "Discord 서버와 채널 목록 새로고침 완료";
      } catch (error) {
        alertDiscordChannelsStatus.textContent = `오류: ${error.message}`;
      } finally {
        alertDiscordChannelsRefresh.disabled = false;
      }
    });

    timelinePlayerForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const player = selectedRegisteredPlayer(timelinePlayerForm);
        await loadReplayTimelinesForPlayer(player);
        banner.textContent = "선택한 유저의 전체 경기 2D 리플레이를 불러왔습니다.";
      } catch (error) {
        replayPlayerStatus.textContent = `오류: ${error.message}`;
        banner.textContent = `오류: ${error.message}`;
      }
    });

    timelinePlayerClear.addEventListener("click", () => {
      timelinePlayerForm.reset();
      timelinePlayerInput.value = "";
      delete timelinePlayerInput.dataset.accountId;
      clearReplayTimeline();
      banner.textContent = "2D 리플레이 선택을 초기화했습니다.";
    });

    recommendationBody.addEventListener("change", (event) => {
      const select = event.target.closest("select[data-recommendation-chart-metric]");
      if (!select || !activeRecommendationReport) return;
      activeRecommendationChartMetric = select.value;
      renderRecommendationCharts(activeRecommendationReport, activeRecommendationChartMetric);
    });

    trendViewControls.addEventListener("click", (event) => {
      const granularityButton = event.target instanceof Element
        ? event.target.closest("button[data-trend-granularity]")
        : null;
      if (granularityButton) {
        trendForm.elements.granularity.value = granularityButton.dataset.trendGranularity || "date";
        trendForm.requestSubmit();
        return;
      }
      const button = event.target instanceof Element
        ? event.target.closest("button[data-trend-view]")
        : null;
      if (!button) return;
      activeTrendView = button.dataset.trendView || "table";
      renderTrendView();
    });

    trendChartMetric.addEventListener("change", () => {
      if (activeTrendView === "chart") renderTrendChart();
    });

    document.querySelector("#replay-artifacts").addEventListener("click", async (event) => {
      const button = event.target instanceof Element
        ? event.target.closest("button[data-load-timeline]")
        : null;
      if (!button) return;

      try {
        const accountId = button.dataset.loadAccountId || "";
        const player = registeredPlayers.find((item) => item.account_id === accountId);
        const matchId = button.dataset.loadMatchId || "";
        if (player) {
          await loadReplayTimelinesForPlayer(player, matchId);
        } else {
          if (!matchId) throw new Error("재생할 경기 정보를 찾을 수 없습니다.");
          const generated = await requestJson(
            `/matches/${encodeURIComponent(matchId)}/replay`,
            "POST",
            { force: false },
          );
          activeReplayPlayer = null;
          timelinePlayerInput.value = "";
          delete timelinePlayerInput.dataset.accountId;
          updateTimelineOptions([generated.artifact], String(generated.artifact.id));
          await loadSelectedTimeline();
        }
        const url = new URL(window.location.href);
        url.hash = "replay-player";
        window.history.pushState({}, "", url);
        activateWorkspace("replay", { focusId: "replay-player", smooth: true });
        banner.textContent = "2D 리플레이 타임라인 로드 완료";
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
      activeTimelineCurrentEventId = null;
      activeTimelineDetailKey = "";
      replayPinnedMap = null;
      replayPinnedEventId = null;
      activeTimelineTime = Number(timelineScrubber.value || 0);
      renderTimelineEventList();
      renderReplayFrame();
    });
    timelineEventList.addEventListener("click", (event) => {
      const mapButton = event.target instanceof Element
        ? event.target.closest("button[data-timeline-map-event]")
        : null;
      if (mapButton) {
        seekTimelineEvent(mapButton.dataset.timelineMapEvent || "", true);
        return;
      }
      const button = event.target instanceof Element
        ? event.target.closest("button[data-timeline-event]")
        : null;
      if (!button) return;
      seekTimelineEvent(button.dataset.timelineEvent || "");
    });
    timelineQuickEvents.addEventListener("click", (event) => {
      const button = event.target instanceof Element
        ? event.target.closest("button[data-timeline-map-event]")
        : null;
      if (!button) return;
      seekTimelineEvent(button.dataset.timelineMapEvent || "", true);
    });
    timelineTeamList.addEventListener("click", (event) => {
      const button = event.target instanceof Element
        ? event.target.closest("button[data-timeline-actor]")
        : null;
      if (!button) return;
      timelineActorFilter.value = button.dataset.timelineActor || "focus";
      refreshTimelineEventExplorer();
    });
    timelineActorFilter.addEventListener("change", () => refreshTimelineEventExplorer());
    timelineEventTypeFilter.addEventListener("change", () => refreshTimelineEventExplorer());
    timelineEventFilterReset.addEventListener("click", () => {
      timelineActorFilter.value = "focus";
      timelineEventTypeFilter.value = "all";
      timelineFollowEvents.checked = true;
      refreshTimelineEventExplorer();
    });
    for (const toggle of [
      timelineShowPath,
      timelineShowCombat,
      timelineShowEngagements,
      timelineShowShots,
      timelineShowThrows,
      timelineShowHits,
      timelineShowDbno,
      timelineShowKills,
      timelineShowCare,
      timelineShowPlane,
      timelineShowPhase,
      timelineShowAllies,
      timelineShowEnemies,
      timelineShowBots,
      timelineFollowPlayer,
    ]) {
      toggle.addEventListener("change", () => {
        if (toggle === timelineFollowPlayer) {
          if (timelineFollowPlayer.checked) {
            replayPinnedMap = null;
            replayPinnedEventId = null;
          }
          renderReplayFrame();
          return;
        }
        if (toggle === timelineShowPhase) {
          renderReplayFrame();
          return;
        }
        if ([timelineShowAllies, timelineShowEnemies, timelineShowBots].includes(toggle)) {
          const selectedTrack = timelineTrackByAccount(timelineActorFilter.value);
          if (selectedTrack && !replayParticipantVisible(selectedTrack)) timelineActorFilter.value = "focus";
        }
        refreshTimelineEventExplorer({ clearSelection: false });
      });
    }
    timelineTrailSeconds.addEventListener("change", renderReplayFrame);
    timelineParticipantSearch.addEventListener("input", renderTimelineTeamList);
    timelineZoom.addEventListener("change", renderReplayFrame);

    workspaceNav.addEventListener("click", (event) => {
      const button = event.target instanceof Element
        ? event.target.closest("button[data-view-target]")
        : null;
      if (!button) return;
      activateWorkspace(button.dataset.viewTarget || "overview", {
        updateUrl: true,
        smooth: true,
      });
    });
    workspaceSections.addEventListener("click", (event) => {
      const button = event.target instanceof Element
        ? event.target.closest("button[data-workspace-section]")
        : null;
      if (!button) return;
      const view = document.body.dataset.activeView || "overview";
      const group = (workspaceSectionsByView[view] || []).find(
        (item) => item.key === button.dataset.workspaceSection
      );
      if (!group) return;
      const focusId = group.ids[0];
      const url = new URL(window.location.href);
      url.hash = focusId;
      window.history.pushState({}, "", url);
      activateWorkspace(view, { focusId, smooth: true });
    });
    refreshWorkspace.addEventListener("click", async () => {
      try {
        await refreshActiveWorkspace();
      } catch (error) {
        banner.textContent = "새로고침 오류: " + error.message;
      }
    });

    rankingGuildRefresh.addEventListener("click", async () => {
      rankingGuildRefresh.disabled = true;
      try {
        await loadDiscordGuilds({ sync: true });
        banner.textContent = "Discord 서버 목록 새로고침 완료";
      } catch (error) {
        banner.textContent = `오류: ${error.message}`;
      } finally {
        rankingGuildRefresh.disabled = false;
      }
    });
    for (const button of pathPickerButtons) {
      button.addEventListener("click", async () => {
        try {
          await chooseStorageDirectory(button);
        } catch (error) {
          storageSettingsStatus.textContent = "오류: " + error.message;
          banner.textContent = "오류: " + error.message;
        }
      });
    }
    window.addEventListener("hashchange", syncWorkspaceToLocation);
    window.addEventListener("popstate", syncWorkspaceToLocation);
    window.addEventListener("scroll", enforceDesktopAppShellOrigin, { passive: true });
    window.addEventListener("resize", enforceDesktopAppShellOrigin);
    window.addEventListener("pywebviewready", enableDesktopFeatures);
    new MutationObserver(() => {
      railActivity.textContent = banner.textContent || "대기 중";
    }).observe(banner, { childList: true, characterData: true, subtree: true });

    if ("scrollRestoration" in window.history) {
      window.history.scrollRestoration = "manual";
    }
    applyDisplaySettings();
    syncWorkspaceToLocation();
    updateKstClock();
    setInterval(updateKstClock, 1000);
    enableDesktopFeatures();
    window.addEventListener("load", resetWorkspaceViewport, { once: true });
    clearReplayTimeline();
    loadInitialLookupPrefillFromUrl();
    const initialAlertHistoryFilterFromUrl = loadInitialAlertHistoryFiltersFromUrl();
    loadInitialWorkerRunFiltersFromUrl();

    Promise.all([loadStatus(), loadAlerts(), loadDiscordBot(), loadDiscordPermissions(), loadDiscordScopes(), loadDiscordGuilds(), loadCollectorWorkerStatus(), loadPostProcessingWorkerStatus(), loadOperationalDrills(), loadWorkerRuns(), loadPlayers(), loadWatchlistPlayers(), loadWatchlistEncounters(), loadDataDeletionRequests(), loadJobs(), loadTelemetryJobs(), loadReplayArtifacts(), loadFlightPathMapCatalog()])
      .then(() => initialAlertHistoryFilterFromUrl ? loadAlertHistory(alertHistoryPage) : null)
      .then(() => loadInitialAlertDetailFromUrl())
      .then(() => loadInitialWorkerRunDetailFromUrl())
      .then(() => deletionRequestHighlightId ? loadDataDeletionRequestDetail(deletionRequestHighlightId) : null)
      .then(() => { banner.textContent = "localhost 전용 관리 화면"; })
      .catch((error) => { banner.textContent = `오류: ${error.message}`; });
    function runBackgroundRefresh(label, task) {
      task().catch((error) => {
        banner.textContent = `${label} 자동 갱신 오류: ${error.message}`;
      });
    }
    setInterval(() => runBackgroundRefresh("수집기", loadCollectorWorkerStatus), 10000);
    setInterval(() => runBackgroundRefresh("후처리", loadPostProcessingWorkerStatus), 10000);
    setInterval(() => runBackgroundRefresh("Discord 봇", loadDiscordBot), 10000);
    setInterval(() => runBackgroundRefresh("작업 이력", loadWorkerRuns), 30000);
    setInterval(() => loadOperationalDrills().catch(() => {}), 30000);
    setInterval(() => {
      loadDataDeletionRequests().catch((error) => { dataDeletionStatus.textContent = `오류: ${error.message}`; });
    }, 30000);
    setInterval(() => {
      refreshAlertsAndHistory().catch((error) => { banner.textContent = `오류: ${error.message}`; });
    }, 30000);
  </script>
</body>
</html>
"""
