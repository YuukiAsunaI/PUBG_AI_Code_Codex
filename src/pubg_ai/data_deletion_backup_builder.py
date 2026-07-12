from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping
import base64
import hashlib
import hmac
import json
import math
import os
import shutil
import tempfile
import uuid
import zipfile

from pubg_ai.data_deletion_backup import (
    DataDeletionBackupEvidence,
    DataDeletionBackupService,
)
from pubg_ai.data_deletion_confirmation import fingerprint_preview_record
from pubg_ai.data_deletion_dry_run import (
    DataDeletionDryRunPlan,
    fingerprint_dry_run_plan,
)
from pubg_ai.data_deletion_preview import MAX_PREVIEW_FILE_LIMIT
from pubg_ai.data_deletion_requests import DataDeletionRequest
from pubg_ai.local_settings import check_storage_path
from pubg_ai.replay_storage import ReplayArtifactStore, ReplayStorageError
from pubg_ai.time_utils import now_kst, to_kst


BACKUP_BUILDER_CONTRACT_VERSION = "deletion-backup-builder-v1"
MYSQL_BACKUP_FORMAT_VERSION = "mysql-row-jsonl-v1"
REPLAY_BACKUP_FORMAT_VERSION = "replay-artifact-zip-v1"
BACKUP_BUILD_CONFIRMATION_PREFIX = "BUILD BACKUP ARTIFACTS REQUEST"

_ARTIFACT_PREREQUISITE_KEYS = (
    "mysql_target_backup",
    "replay_artifact_backup",
)
_IDENTITY_TABLES = {
    "registered_players",
    "player_aliases",
    "raw_player_snapshots",
    "replay_artifacts",
}
_NORMALIZED_TABLES = {
    "match_participants",
    "player_match_combat_summaries",
    "player_weapon_match_stats",
    "player_item_events",
    "player_item_match_stats",
    "player_position_samples",
    "player_landing_events",
    "player_movement_summaries",
    "player_combat_location_events",
    "player_combat_loadout_snapshots",
}
_DATABASE_TABLES = _IDENTITY_TABLES | _NORMALIZED_TABLES | {"player_collection_states"}
_COPY_CHUNK_BYTES = 1024 * 1024


class DataDeletionBackupBuilderError(RuntimeError):
    """Raised when opt-in backup artifacts cannot be built safely."""


@dataclass(frozen=True)
class BuiltBackupArtifact:
    prerequisite_key: str
    path: Path
    sha256: str
    size_bytes: int
    covered_row_count: int | None = None
    covered_file_count: int | None = None
    covered_file_bytes: int | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "prerequisite_key": self.prerequisite_key,
            "path": str(self.path),
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "covered_row_count": self.covered_row_count,
            "covered_file_count": self.covered_file_count,
            "covered_file_bytes": self.covered_file_bytes,
        }


@dataclass(frozen=True)
class DataDeletionBackupBuildResult:
    request_id: int
    dry_run_plan_id: int
    contract_version: str
    plan_fingerprint_sha256: str
    build_id: str
    build_directory: Path
    manifest_path: Path
    manifest_sha256: str
    confirmation_text_sha256: str
    artifacts: tuple[BuiltBackupArtifact, ...]
    evidence: tuple[DataDeletionBackupEvidence, ...]
    built_by: str
    build_note: str | None
    built_at_kst: datetime

    def to_record(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "dry_run_plan_id": self.dry_run_plan_id,
            "contract_version": self.contract_version,
            "plan_fingerprint_sha256": self.plan_fingerprint_sha256,
            "build_id": self.build_id,
            "build_directory": str(self.build_directory),
            "manifest_path": str(self.manifest_path),
            "manifest_sha256": self.manifest_sha256,
            "confirmation_text_sha256": self.confirmation_text_sha256,
            "artifacts": [artifact.to_record() for artifact in self.artifacts],
            "evidence": [item.to_record() for item in self.evidence],
            "built_by": self.built_by,
            "build_note": self.build_note,
            "built_at_kst": to_kst(self.built_at_kst).isoformat(),
            "source_rows_modified": False,
            "source_files_modified": False,
            "restore_test_performed": False,
            "quarantine_performed": False,
            "deletion_performed": False,
            "execution_enabled": False,
            "execution_ready": False,
        }


RowProvider = Callable[
    [dict[str, Any], DataDeletionRequest],
    Iterable[Mapping[str, Any]],
]


class DataDeletionBackupBuilderService:
    def __init__(
        self,
        connection: Any,
        *,
        backup_service: DataDeletionBackupService,
        backup_root: Path,
        raw_data_dir: Path,
        replay_data_dir: Path,
        row_provider: RowProvider | None = None,
    ) -> None:
        self.connection = connection
        self.backup_service = backup_service
        self.backup_root = backup_root.expanduser().resolve(strict=False)
        self.raw_data_dir = raw_data_dir.expanduser().resolve(strict=False)
        self.replay_data_dir = replay_data_dir.expanduser().resolve(strict=False)
        self.replay_store = ReplayArtifactStore(self.replay_data_dir)
        self.row_provider = row_provider or self._query_rows

    def build_state(self, request: DataDeletionRequest) -> dict[str, Any]:
        plans = self.backup_service.dry_run_service.list_plans(request.id, limit=1)
        plan = plans[0] if plans else None
        status = check_storage_path(self.backup_root)
        blockers: list[str] = []
        if request.status != "approved":
            blockers.append(f"request status must be approved, not {request.status}")
        if plan is None:
            blockers.append("latest confirmed dry-run plan is required")
        elif not hmac.compare_digest(
            plan.plan_fingerprint_sha256,
            fingerprint_dry_run_plan(plan.plan_json),
        ):
            blockers.append("latest dry-run plan fingerprint is invalid")
        if not status.exists or not status.is_dir or not status.writable:
            blockers.append("configured backup root must exist and be writable")
        overlap = overlapping_source_root(
            self.backup_root,
            self.raw_data_dir,
            self.replay_data_dir,
        )
        if overlap is not None:
            blockers.append(f"backup root overlaps source storage: {overlap}")
        keys = artifact_prerequisite_keys(plan) if plan is not None else ()
        if plan is not None and not keys:
            blockers.append("latest plan has no artifact backup prerequisites")
        confirmation_text = (
            expected_backup_build_confirmation(request.id, plan.plan_fingerprint_sha256)
            if plan is not None
            else None
        )
        return {
            "request_id": request.id,
            "request_status": request.status,
            "contract_version": BACKUP_BUILDER_CONTRACT_VERSION,
            "backup_root": str(self.backup_root),
            "backup_root_status": status.to_record(),
            "latest_plan_id": plan.id if plan is not None else None,
            "plan_fingerprint_sha256": plan.plan_fingerprint_sha256 if plan is not None else None,
            "artifact_prerequisite_keys": list(keys),
            "confirmation_text": confirmation_text,
            "build_allowed": not blockers,
            "build_blockers": blockers,
            "writes_backup_files": True,
            "appends_evidence_rows": True,
            "source_rows_modified": False,
            "source_files_modified": False,
            "restore_test_performed": False,
            "quarantine_performed": False,
            "deletion_performed": False,
            "execution_enabled": False,
            "execution_ready": False,
        }

    def build(
        self,
        request: DataDeletionRequest,
        *,
        dry_run_plan_id: int,
        confirmation_text: str,
        actor_id: str,
        note: str | None = None,
        reference_kst: datetime | None = None,
    ) -> DataDeletionBackupBuildResult:
        actor_id = _required_text(actor_id, "actor_id", 191)
        note = _optional_text(note, "note", 500)
        plan = self.backup_service.require_latest_plan(request, dry_run_plan_id)
        expected_confirmation = expected_backup_build_confirmation(
            request.id,
            plan.plan_fingerprint_sha256,
        )
        supplied_confirmation = _required_text(
            confirmation_text,
            "confirmation_text",
            500,
        )
        if not hmac.compare_digest(supplied_confirmation, expected_confirmation):
            raise DataDeletionBackupBuilderError(
                "backup build confirmation text does not match the latest plan fingerprint."
            )
        state = self.build_state(request)
        if not state["build_allowed"]:
            raise DataDeletionBackupBuilderError(
                "backup build blocked: " + "; ".join(state["build_blockers"])
            )
        if state["latest_plan_id"] != plan.id:
            raise DataDeletionBackupBuilderError("latest dry-run plan changed before backup build.")

        built_at = to_kst(reference_kst or now_kst())
        if built_at < to_kst(plan.generated_at_kst):
            raise DataDeletionBackupBuilderError("backup build time predates the dry-run plan.")
        build_id = uuid.uuid4().hex
        confirmation_sha256 = hashlib.sha256(expected_confirmation.encode("utf-8")).hexdigest()
        plan_root = (
            self.backup_root
            / "data-deletions"
            / f"request-{request.id}"
            / f"plan-{plan.id}-{plan.plan_fingerprint_sha256[:12]}"
        )
        plan_root.mkdir(parents=True, exist_ok=True)
        final_directory = plan_root / f"build-{built_at:%Y%m%dT%H%M%S}-{build_id[:12]}"
        if final_directory.exists():
            raise DataDeletionBackupBuilderError("backup build directory already exists.")
        temporary_directory = Path(
            tempfile.mkdtemp(prefix=".build-", suffix=".tmp", dir=plan_root)
        )
        renamed = False
        try:
            artifacts = self._build_artifacts(
                request,
                plan,
                temporary_directory,
                built_at,
            )
            manifest = build_backup_manifest(
                request,
                plan,
                build_id=build_id,
                confirmation_text_sha256=confirmation_sha256,
                artifacts=artifacts,
                built_by=actor_id,
                build_note=note,
                built_at_kst=built_at,
            )
            manifest_path = temporary_directory / "build-manifest.json"
            _write_new_file(manifest_path, _pretty_json_bytes(manifest))
            manifest_sha256 = _sha256_file(manifest_path)

            self._assert_live_source(plan, request)
            os.replace(temporary_directory, final_directory)
            renamed = True

            final_artifacts = tuple(
                BuiltBackupArtifact(
                    prerequisite_key=artifact.prerequisite_key,
                    path=final_directory / artifact.path.name,
                    sha256=artifact.sha256,
                    size_bytes=artifact.size_bytes,
                    covered_row_count=artifact.covered_row_count,
                    covered_file_count=artifact.covered_file_count,
                    covered_file_bytes=artifact.covered_file_bytes,
                )
                for artifact in artifacts
            )
            evidence_payloads = {
                artifact.prerequisite_key: _artifact_evidence_payload(artifact, built_at)
                for artifact in final_artifacts
            }
            audit_note = _builder_evidence_note(
                build_id,
                confirmation_sha256,
                manifest_sha256,
                note,
            )
            try:
                evidence_map = self.backup_service.record_evidence_batch(
                    request,
                    dry_run_plan_id=plan.id,
                    evidence_by_key=evidence_payloads,
                    actor_id=actor_id,
                    note=audit_note,
                    reference_kst=built_at,
                )
            except Exception as exc:
                raise DataDeletionBackupBuilderError(
                    f"backup artifacts were retained at {final_directory}, but evidence recording failed: {exc}"
                ) from exc
            final_manifest_path = final_directory / manifest_path.name
            return DataDeletionBackupBuildResult(
                request_id=request.id,
                dry_run_plan_id=plan.id,
                contract_version=BACKUP_BUILDER_CONTRACT_VERSION,
                plan_fingerprint_sha256=plan.plan_fingerprint_sha256,
                build_id=build_id,
                build_directory=final_directory,
                manifest_path=final_manifest_path,
                manifest_sha256=manifest_sha256,
                confirmation_text_sha256=confirmation_sha256,
                artifacts=final_artifacts,
                evidence=tuple(
                    evidence_map[key]
                    for key in _ARTIFACT_PREREQUISITE_KEYS
                    if key in evidence_map
                ),
                built_by=actor_id,
                build_note=note,
                built_at_kst=built_at,
            )
        except Exception:
            if not renamed:
                shutil.rmtree(temporary_directory, ignore_errors=True)
            raise

    def _build_artifacts(
        self,
        request: DataDeletionRequest,
        plan: DataDeletionDryRunPlan,
        directory: Path,
        built_at_kst: datetime,
    ) -> tuple[BuiltBackupArtifact, ...]:
        required_keys = artifact_prerequisite_keys(plan)
        artifacts: list[BuiltBackupArtifact] = []
        self.connection.begin()
        try:
            self._assert_live_source(plan, request)
            if "mysql_target_backup" in required_keys:
                artifacts.append(
                    self._write_mysql_artifact(
                        request,
                        plan,
                        directory / "mysql-target-backup.zip",
                        built_at_kst,
                    )
                )
            if "replay_artifact_backup" in required_keys:
                artifacts.append(
                    self._write_replay_artifact(
                        plan,
                        directory / "replay-artifact-backup.zip",
                        built_at_kst,
                    )
                )
        finally:
            self.connection.rollback()
        return tuple(artifacts)

    def _assert_live_source(
        self,
        plan: DataDeletionDryRunPlan,
        request: DataDeletionRequest,
    ) -> None:
        preview = self.backup_service.preview_service.build_preview(
            request,
            file_limit=MAX_PREVIEW_FILE_LIMIT,
        ).to_record()
        live_fingerprint, _ = fingerprint_preview_record(preview)
        if not hmac.compare_digest(plan.source_fingerprint_sha256, live_fingerprint):
            raise DataDeletionBackupBuilderError(
                "current deletion impact differs from the latest dry-run plan."
            )

    def _write_mysql_artifact(
        self,
        request: DataDeletionRequest,
        plan: DataDeletionDryRunPlan,
        path: Path,
        built_at_kst: datetime,
    ) -> BuiltBackupArtifact:
        operations = _database_operations(plan)
        table_records: list[dict[str, Any]] = []
        total_rows = 0
        try:
            with zipfile.ZipFile(
                path,
                mode="x",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
                strict_timestamps=False,
            ) as archive:
                for operation in operations:
                    table = str(operation["table"])
                    entry_name = f"tables/{int(operation['sequence']):03d}-{table}.jsonl"
                    content_hash = hashlib.sha256()
                    content_bytes = 0
                    row_count = 0
                    with archive.open(entry_name, mode="w", force_zip64=True) as sink:
                        for source_row in self.row_provider(operation, request):
                            row = _row_record(source_row)
                            line = _compact_json_bytes(row) + b"\n"
                            sink.write(line)
                            content_hash.update(line)
                            content_bytes += len(line)
                            row_count += 1
                    expected_rows = _nonnegative_int(
                        operation.get("estimated_rows"),
                        "estimated_rows",
                    )
                    if row_count != expected_rows:
                        raise DataDeletionBackupBuilderError(
                            f"table {table} row count changed: expected {expected_rows}, found {row_count}."
                        )
                    total_rows += row_count
                    table_records.append(
                        {
                            "sequence": int(operation["sequence"]),
                            "table": table,
                            "entry": entry_name,
                            "row_count": row_count,
                            "content_bytes": content_bytes,
                            "content_sha256": content_hash.hexdigest(),
                            "selector": dict(operation["selector"]),
                        }
                    )
                expected_total = _plan_metric(plan, "candidate_row_count")
                if total_rows != expected_total:
                    raise DataDeletionBackupBuilderError(
                        f"database backup row count changed: expected {expected_total}, found {total_rows}."
                    )
                archive.writestr(
                    "manifest.json",
                    _pretty_json_bytes(
                        {
                            "format_version": MYSQL_BACKUP_FORMAT_VERSION,
                            "builder_contract_version": BACKUP_BUILDER_CONTRACT_VERSION,
                            "request_id": request.id,
                            "dry_run_plan_id": plan.id,
                            "plan_fingerprint_sha256": plan.plan_fingerprint_sha256,
                            "source_fingerprint_sha256": plan.source_fingerprint_sha256,
                            "built_at_kst": to_kst(built_at_kst).isoformat(),
                            "row_count": total_rows,
                            "tables": table_records,
                            "schema_creation_included": False,
                            "restore_supported_by_current_application": False,
                        }
                    ),
                )
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            raise DataDeletionBackupBuilderError(
                f"failed to write MySQL row backup artifact: {exc}"
            ) from exc
        return BuiltBackupArtifact(
            prerequisite_key="mysql_target_backup",
            path=path,
            sha256=_sha256_file(path),
            size_bytes=path.stat().st_size,
            covered_row_count=total_rows,
        )

    def _write_replay_artifact(
        self,
        plan: DataDeletionDryRunPlan,
        path: Path,
        built_at_kst: datetime,
    ) -> BuiltBackupArtifact:
        operations = _file_operations(plan)
        file_records: list[dict[str, Any]] = []
        seen_entries: set[str] = set()
        total_bytes = 0
        try:
            with zipfile.ZipFile(
                path,
                mode="x",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
                strict_timestamps=False,
            ) as archive:
                for operation in operations:
                    relative_path = _safe_relative_path(str(operation.get("relative_path") or ""))
                    entry_name = f"files/{relative_path.as_posix()}"
                    if entry_name in seen_entries:
                        raise DataDeletionBackupBuilderError(
                            f"duplicate replay backup path: {relative_path.as_posix()}"
                        )
                    seen_entries.add(entry_name)
                    try:
                        source_path = self.replay_store.resolve_path(str(relative_path))
                    except ReplayStorageError as exc:
                        raise DataDeletionBackupBuilderError(str(exc)) from exc
                    if not source_path.is_file():
                        raise DataDeletionBackupBuilderError(
                            f"replay source file is missing: {relative_path.as_posix()}"
                        )
                    digest = hashlib.sha256()
                    copied_bytes = 0
                    with source_path.open("rb") as source, archive.open(
                        entry_name,
                        mode="w",
                        force_zip64=True,
                    ) as sink:
                        while chunk := source.read(_COPY_CHUNK_BYTES):
                            sink.write(chunk)
                            digest.update(chunk)
                            copied_bytes += len(chunk)
                    expected_bytes = _nonnegative_int(
                        operation.get("declared_size_bytes"),
                        "declared_size_bytes",
                    )
                    expected_sha256 = _fingerprint(operation.get("sha256"), "sha256")
                    if copied_bytes != expected_bytes:
                        raise DataDeletionBackupBuilderError(
                            f"replay file size changed for {relative_path.as_posix()}."
                        )
                    if not hmac.compare_digest(digest.hexdigest(), expected_sha256):
                        raise DataDeletionBackupBuilderError(
                            f"replay file checksum changed for {relative_path.as_posix()}."
                        )
                    total_bytes += copied_bytes
                    file_records.append(
                        {
                            "sequence": int(operation["sequence"]),
                            "record_id": int(operation["record_id"]),
                            "artifact_type": str(operation.get("artifact_type") or ""),
                            "match_id": str(operation.get("match_id") or ""),
                            "source_relative_path": relative_path.as_posix(),
                            "entry": entry_name,
                            "size_bytes": copied_bytes,
                            "sha256": digest.hexdigest(),
                        }
                    )
                expected_count = _plan_metric(plan, "candidate_file_count")
                expected_bytes = _plan_metric(plan, "candidate_file_bytes")
                if len(file_records) != expected_count or total_bytes != expected_bytes:
                    raise DataDeletionBackupBuilderError(
                        "replay backup totals differ from the latest dry-run plan."
                    )
                archive.writestr(
                    "manifest.json",
                    _pretty_json_bytes(
                        {
                            "format_version": REPLAY_BACKUP_FORMAT_VERSION,
                            "builder_contract_version": BACKUP_BUILDER_CONTRACT_VERSION,
                            "dry_run_plan_id": plan.id,
                            "plan_fingerprint_sha256": plan.plan_fingerprint_sha256,
                            "source_fingerprint_sha256": plan.source_fingerprint_sha256,
                            "built_at_kst": to_kst(built_at_kst).isoformat(),
                            "file_count": len(file_records),
                            "source_file_bytes": total_bytes,
                            "files": file_records,
                            "restore_supported_by_current_application": False,
                        }
                    ),
                )
        except DataDeletionBackupBuilderError:
            raise
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            raise DataDeletionBackupBuilderError(
                f"failed to write replay backup artifact: {exc}"
            ) from exc
        return BuiltBackupArtifact(
            prerequisite_key="replay_artifact_backup",
            path=path,
            sha256=_sha256_file(path),
            size_bytes=path.stat().st_size,
            covered_file_count=len(file_records),
            covered_file_bytes=total_bytes,
        )

    def _query_rows(
        self,
        operation: dict[str, Any],
        request: DataDeletionRequest,
    ) -> Iterable[Mapping[str, Any]]:
        statement, parameters = database_backup_select(operation, request)
        with self.connection.cursor() as cursor:
            cursor.execute(statement, parameters)
            while rows := cursor.fetchmany(500):
                for row in rows:
                    if not isinstance(row, Mapping):
                        raise DataDeletionBackupBuilderError(
                            "database backup query returned a non-object row."
                        )
                    yield row


def expected_backup_build_confirmation(request_id: int, plan_fingerprint: str) -> str:
    if int(request_id) <= 0:
        raise DataDeletionBackupBuilderError("request_id must be positive.")
    fingerprint = _fingerprint(plan_fingerprint, "plan_fingerprint")
    return f"{BACKUP_BUILD_CONFIRMATION_PREFIX} {int(request_id)} {fingerprint}"


def artifact_prerequisite_keys(
    plan: DataDeletionDryRunPlan | None,
) -> tuple[str, ...]:
    if plan is None:
        return ()
    prerequisites = plan.plan_json.get("backup_prerequisites")
    if not isinstance(prerequisites, list):
        raise DataDeletionBackupBuilderError("dry-run plan backup prerequisites are missing.")
    required: set[str] = set()
    for item in prerequisites:
        if not isinstance(item, dict) or not bool(item.get("required")):
            continue
        key = str(item.get("key") or "")
        if key in _ARTIFACT_PREREQUISITE_KEYS:
            required.add(key)
    return tuple(key for key in _ARTIFACT_PREREQUISITE_KEYS if key in required)


def overlapping_source_root(
    backup_root: Path,
    raw_data_dir: Path,
    replay_data_dir: Path,
) -> str | None:
    for label, source in (
        ("PUBG_RAW_DATA_DIR", raw_data_dir),
        ("PUBG_REPLAY_DATA_DIR", replay_data_dir),
    ):
        if _paths_overlap(backup_root, source):
            return label
    return None


def database_backup_select(
    operation: dict[str, Any],
    request: DataDeletionRequest,
) -> tuple[str, tuple[Any, ...]]:
    table = str(operation.get("table") or "")
    if table not in _DATABASE_TABLES:
        raise DataDeletionBackupBuilderError(f"database backup table is not allowed: {table}")
    if str(operation.get("action") or "") != "delete_rows_planned":
        raise DataDeletionBackupBuilderError(f"unexpected database plan action for {table}.")
    if bool(operation.get("mutation_enabled")):
        raise DataDeletionBackupBuilderError(f"database plan unexpectedly enables mutation for {table}.")
    selector = operation.get("selector")
    if not isinstance(selector, dict):
        raise DataDeletionBackupBuilderError(f"database selector is missing for {table}.")
    if (
        str(selector.get("account_id") or "") != request.account_id
        or str(selector.get("shard") or "") != request.shard
    ):
        raise DataDeletionBackupBuilderError(f"database selector target mismatch for {table}.")
    if table == "player_collection_states":
        if str(selector.get("kind") or "") != "registered_player_join":
            raise DataDeletionBackupBuilderError("collection-state selector kind is invalid.")
        return (
            """
            SELECT target_rows.*
            FROM player_collection_states AS target_rows
            INNER JOIN registered_players AS target_players
                ON target_players.id = target_rows.registered_player_id
            WHERE target_players.account_id = %s AND target_players.shard = %s
            ORDER BY target_rows.registered_player_id ASC
            """,
            (request.account_id, request.shard),
        )
    if table in _IDENTITY_TABLES:
        if str(selector.get("kind") or "") != "target_identity":
            raise DataDeletionBackupBuilderError(f"identity selector kind is invalid for {table}.")
        return (
            f"""
            SELECT target_rows.*
            FROM {table} AS target_rows
            WHERE target_rows.account_id = %s AND target_rows.shard = %s
            ORDER BY target_rows.id ASC
            """,
            (request.account_id, request.shard),
        )
    if str(selector.get("kind") or "") != "player_match_scope":
        raise DataDeletionBackupBuilderError(f"player-match selector kind is invalid for {table}.")
    return (
        f"""
        SELECT target_rows.*
        FROM {table} AS target_rows
        INNER JOIN matches AS target_matches
            ON target_matches.match_id = target_rows.match_id
        WHERE target_rows.account_id = %s AND target_matches.shard = %s
        ORDER BY target_rows.id ASC
        """,
        (request.account_id, request.shard),
    )


def build_backup_manifest(
    request: DataDeletionRequest,
    plan: DataDeletionDryRunPlan,
    *,
    build_id: str,
    confirmation_text_sha256: str,
    artifacts: tuple[BuiltBackupArtifact, ...],
    built_by: str,
    build_note: str | None,
    built_at_kst: datetime,
) -> dict[str, Any]:
    body = {
        "contract_version": BACKUP_BUILDER_CONTRACT_VERSION,
        "request_id": request.id,
        "dry_run_plan_id": plan.id,
        "plan_fingerprint_sha256": plan.plan_fingerprint_sha256,
        "source_fingerprint_sha256": plan.source_fingerprint_sha256,
        "build_id": build_id,
        "confirmation_text_sha256": confirmation_text_sha256,
        "target": {
            "account_id": request.account_id,
            "shard": request.shard,
            "player_name": request.player_name,
        },
        "artifacts": [
            {
                **artifact.to_record(),
                "path": artifact.path.name,
            }
            for artifact in artifacts
        ],
        "built_by": built_by,
        "build_note": build_note,
        "built_at_kst": to_kst(built_at_kst).isoformat(),
        "safety": {
            "source_rows_modified": False,
            "source_files_modified": False,
            "restore_test_performed": False,
            "quarantine_performed": False,
            "deletion_performed": False,
            "execution_enabled": False,
            "execution_ready": False,
        },
    }
    return {
        **body,
        "manifest_fingerprint_sha256": _canonical_sha256(body),
    }


def _database_operations(plan: DataDeletionDryRunPlan) -> list[dict[str, Any]]:
    records = plan.plan_json.get("database_operations")
    if not isinstance(records, list):
        raise DataDeletionBackupBuilderError("dry-run plan database operations are missing.")
    operations = [dict(item) for item in records if isinstance(item, dict)]
    if len(operations) != len(records):
        raise DataDeletionBackupBuilderError("dry-run plan contains an invalid database operation.")
    operations.sort(key=lambda item: int(item.get("sequence") or 0))
    seen: set[str] = set()
    for operation in operations:
        table = str(operation.get("table") or "")
        if table in seen:
            raise DataDeletionBackupBuilderError(f"duplicate database backup table: {table}")
        seen.add(table)
    return operations


def _file_operations(plan: DataDeletionDryRunPlan) -> list[dict[str, Any]]:
    records = plan.plan_json.get("file_operations")
    if not isinstance(records, list):
        raise DataDeletionBackupBuilderError("dry-run plan file operations are missing.")
    operations = [dict(item) for item in records if isinstance(item, dict)]
    if len(operations) != len(records):
        raise DataDeletionBackupBuilderError("dry-run plan contains an invalid file operation.")
    operations.sort(key=lambda item: int(item.get("sequence") or 0))
    for operation in operations:
        if str(operation.get("action") or "") != "quarantine_file_planned":
            raise DataDeletionBackupBuilderError("unexpected replay file plan action.")
        if bool(operation.get("mutation_enabled")):
            raise DataDeletionBackupBuilderError("replay file plan unexpectedly enables mutation.")
        if str(operation.get("source_table") or "") != "replay_artifacts":
            raise DataDeletionBackupBuilderError("replay source table is not allowed.")
        if str(operation.get("storage_root") or "") != "PUBG_REPLAY_DATA_DIR":
            raise DataDeletionBackupBuilderError("replay storage root does not match local settings.")
        if str(operation.get("ownership") or "") != "player_artifact":
            raise DataDeletionBackupBuilderError("replay file is not a player-owned artifact.")
        if str(operation.get("verification_status") or "") != "verified":
            raise DataDeletionBackupBuilderError("replay file was not verified by the dry-run preview.")
    return operations


def _artifact_evidence_payload(
    artifact: BuiltBackupArtifact,
    built_at_kst: datetime,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "artifact_path": str(artifact.path),
        "artifact_sha256": artifact.sha256,
        "artifact_size_bytes": artifact.size_bytes,
        "backup_created_at_kst": built_at_kst,
    }
    if artifact.prerequisite_key == "mysql_target_backup":
        payload["covered_row_count"] = artifact.covered_row_count
    else:
        payload["covered_file_count"] = artifact.covered_file_count
        payload["covered_file_bytes"] = artifact.covered_file_bytes
    return payload


def _builder_evidence_note(
    build_id: str,
    confirmation_sha256: str,
    manifest_sha256: str,
    note: str | None,
) -> str:
    text = (
        f"builder={BACKUP_BUILDER_CONTRACT_VERSION}; build_id={build_id}; "
        f"opt_in_sha256={confirmation_sha256}; manifest_sha256={manifest_sha256}"
    )
    if note:
        text += f"; note={note}"
    if len(text) > 1000:
        raise DataDeletionBackupBuilderError("generated evidence note exceeds 1000 characters.")
    return text


def _row_record(row: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): _json_value(value) for key, value in row.items()}


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DataDeletionBackupBuilderError("database row contains a non-finite float.")
        return value
    if isinstance(value, Decimal):
        return {"$pubg_ai_type": "decimal", "value": str(value)}
    if isinstance(value, datetime):
        return {"$pubg_ai_type": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"$pubg_ai_type": "date", "value": value.isoformat()}
    if isinstance(value, time):
        return {"$pubg_ai_type": "time", "value": value.isoformat()}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {
            "$pubg_ai_type": "bytes_base64",
            "value": base64.b64encode(bytes(value)).decode("ascii"),
        }
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise DataDeletionBackupBuilderError(
        f"database row contains unsupported value type: {type(value).__name__}."
    )


def _safe_relative_path(value: str) -> PurePosixPath:
    normalized = value.replace("\\", "/").strip()
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise DataDeletionBackupBuilderError("replay relative path is unsafe.")
    return path


def _plan_metric(plan: DataDeletionDryRunPlan, key: str) -> int:
    metrics = plan.plan_json.get("metrics")
    if not isinstance(metrics, dict):
        raise DataDeletionBackupBuilderError("dry-run plan metrics are missing.")
    return _nonnegative_int(metrics.get(key), key)


def _paths_overlap(first: Path, second: Path) -> bool:
    first_text = os.path.normcase(str(first.resolve(strict=False)))
    second_text = os.path.normcase(str(second.resolve(strict=False)))
    try:
        common = os.path.normcase(os.path.commonpath((first_text, second_text)))
    except ValueError:
        return False
    return common in {first_text, second_text}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(_COPY_CHUNK_BYTES):
                digest.update(chunk)
    except OSError as exc:
        raise DataDeletionBackupBuilderError(f"failed to checksum backup artifact: {exc}") from exc
    return digest.hexdigest()


def _write_new_file(path: Path, body: bytes) -> None:
    try:
        with path.open("xb") as target:
            target.write(body)
            target.flush()
            os.fsync(target.fileno())
    except OSError as exc:
        raise DataDeletionBackupBuilderError(f"failed to write backup manifest: {exc}") from exc


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_compact_json_bytes(value)).hexdigest()


def _compact_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _pretty_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    ).encode("utf-8")


def _fingerprint(value: Any, label: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise DataDeletionBackupBuilderError(f"{label} must be a 64-character SHA-256.")
    return text


def _nonnegative_int(value: Any, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise DataDeletionBackupBuilderError(f"{label} must be an integer.") from exc
    if number < 0:
        raise DataDeletionBackupBuilderError(f"{label} must be nonnegative.")
    return number


def _required_text(value: Any, label: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise DataDeletionBackupBuilderError(f"{label} is required.")
    if len(text) > limit:
        raise DataDeletionBackupBuilderError(f"{label} must be at most {limit} characters.")
    return text


def _optional_text(value: Any, label: str, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > limit:
        raise DataDeletionBackupBuilderError(f"{label} must be at most {limit} characters.")
    return text
