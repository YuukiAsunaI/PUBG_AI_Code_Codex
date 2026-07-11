from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pubg_ai.data_deletion_requests import DataDeletionRequest, normalize_deletion_scope
from pubg_ai.raw_storage import RawPayloadStore, RawStorageError
from pubg_ai.replay_storage import ReplayArtifactStore, ReplayStorageError
from pubg_ai.time_utils import now_kst


DEFAULT_PREVIEW_FILE_LIMIT = 100
MAX_PREVIEW_FILE_LIMIT = 500

NORMALIZED_PLAYER_TABLES = (
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
)

SHARED_MATCH_TABLES = (
    "matches",
    "match_care_package_events",
    "match_plane_routes",
    "match_phase_events",
)

RAW_PAYLOAD_TABLES = (
    ("raw_match_payloads", "match"),
    ("raw_telemetry_payloads", "telemetry"),
)


class DataDeletionPreviewError(RuntimeError):
    """Raised when a deletion impact preview cannot be calculated safely."""


@dataclass(frozen=True)
class DeletionRowImpact:
    table: str
    category: str
    relationship: str
    row_count: int
    deletion_candidate: bool

    def to_record(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "category": self.category,
            "relationship": self.relationship,
            "row_count": self.row_count,
            "deletion_candidate": self.deletion_candidate,
        }


@dataclass(frozen=True)
class DeletionFileImpact:
    record_id: int
    source_table: str
    file_type: str
    match_id: str
    ownership: str
    deletion_candidate: bool
    storage_backend: str
    storage_root: str
    relative_path: str
    resolved_path: str | None
    declared_size_bytes: int
    actual_size_bytes: int | None
    sha256: str
    storage_root_matches: bool
    path_safe: bool
    exists: bool
    size_matches: bool | None
    shared_match: bool
    participant_count: int | None
    verification_status: str
    verification_error: str | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "source_table": self.source_table,
            "file_type": self.file_type,
            "match_id": self.match_id,
            "ownership": self.ownership,
            "deletion_candidate": self.deletion_candidate,
            "storage_backend": self.storage_backend,
            "storage_root": self.storage_root,
            "relative_path": self.relative_path,
            "resolved_path": self.resolved_path,
            "declared_size_bytes": self.declared_size_bytes,
            "actual_size_bytes": self.actual_size_bytes,
            "sha256": self.sha256,
            "storage_root_matches": self.storage_root_matches,
            "path_safe": self.path_safe,
            "exists": self.exists,
            "size_matches": self.size_matches,
            "shared_match": self.shared_match,
            "participant_count": self.participant_count,
            "verification_status": self.verification_status,
            "verification_error": self.verification_error,
            "checksum_verified": False,
        }


@dataclass(frozen=True)
class DeletionFileCatalog:
    category: str
    included: bool
    total_records: int
    total_declared_size_bytes: int
    deletion_candidate_records: int
    shared_match_records: int
    limit: int
    files: tuple[DeletionFileImpact, ...]

    @property
    def truncated(self) -> bool:
        return self.total_records > len(self.files)

    def to_record(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "included": self.included,
            "total_records": self.total_records,
            "total_declared_size_bytes": self.total_declared_size_bytes,
            "deletion_candidate_records": self.deletion_candidate_records,
            "shared_match_records": self.shared_match_records,
            "limit": self.limit,
            "listed_records": len(self.files),
            "truncated": self.truncated,
            "files": [file.to_record() for file in self.files],
        }

    @classmethod
    def excluded(cls, category: str, limit: int) -> "DeletionFileCatalog":
        return cls(
            category=category,
            included=False,
            total_records=0,
            total_declared_size_bytes=0,
            deletion_candidate_records=0,
            shared_match_records=0,
            limit=limit,
            files=(),
        )


@dataclass(frozen=True)
class DataDeletionImpactPreview:
    request_id: int
    account_id: str
    shard: str
    player_name: str
    deletion_scope: str
    generated_at_kst: str
    file_limit_per_catalog: int
    included_sections: dict[str, bool]
    matched_match_count: int
    row_impacts: tuple[DeletionRowImpact, ...]
    preserved_references: tuple[DeletionRowImpact, ...]
    raw_files: DeletionFileCatalog
    replay_files: DeletionFileCatalog
    warnings: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        files = (*self.raw_files.files, *self.replay_files.files)
        issue_files = [file for file in files if file.verification_status != "present"]
        return {
            "request_id": self.request_id,
            "target": {
                "account_id": self.account_id,
                "shard": self.shard,
                "player_name": self.player_name,
            },
            "deletion_scope": self.deletion_scope,
            "generated_at_kst": self.generated_at_kst,
            "file_limit_per_catalog": self.file_limit_per_catalog,
            "included_sections": dict(self.included_sections),
            "matched_match_count": self.matched_match_count,
            "candidate_row_count": sum(
                impact.row_count for impact in self.row_impacts if impact.deletion_candidate
            ),
            "preserved_reference_row_count": sum(
                impact.row_count for impact in self.preserved_references
            ),
            "row_impacts": [impact.to_record() for impact in self.row_impacts],
            "preserved_references": [impact.to_record() for impact in self.preserved_references],
            "raw_files": self.raw_files.to_record(),
            "replay_files": self.replay_files.to_record(),
            "verification": {
                "read_only": True,
                "execution_enabled": False,
                "ready_for_execution": False,
                "catalog_complete": not self.raw_files.truncated and not self.replay_files.truncated,
                "listed_file_count": len(files),
                "filesystem_issue_count": len(issue_files),
                "unsafe_path_count": sum(not file.path_safe for file in files),
                "missing_file_count": sum(
                    file.path_safe and not file.exists for file in files
                ),
                "size_mismatch_count": sum(file.size_matches is False for file in files),
                "checksum_verification_performed": False,
            },
            "warnings": list(self.warnings),
        }


class DataDeletionImpactPreviewService:
    def __init__(
        self,
        connection: Any,
        *,
        raw_data_dir: Path,
        replay_data_dir: Path,
    ) -> None:
        self.connection = connection
        self.raw_store = RawPayloadStore(raw_data_dir)
        self.replay_store = ReplayArtifactStore(replay_data_dir)

    def build_preview(
        self,
        request: DataDeletionRequest,
        *,
        file_limit: int = DEFAULT_PREVIEW_FILE_LIMIT,
    ) -> DataDeletionImpactPreview:
        try:
            scope = normalize_deletion_scope(request.deletion_scope)
        except Exception as exc:
            raise DataDeletionPreviewError(str(exc)) from exc
        if not 1 <= file_limit <= MAX_PREVIEW_FILE_LIMIT:
            raise DataDeletionPreviewError(
                f"file_limit must be between 1 and {MAX_PREVIEW_FILE_LIMIT}."
            )

        included = {
            "registration": scope in {"registration", "all"},
            "normalized": scope in {"normalized", "all"},
            "raw": scope in {"raw", "all"},
            "replay": scope in {"replay", "all"},
        }
        row_impacts: list[DeletionRowImpact] = []
        preserved_references: list[DeletionRowImpact] = []

        if included["registration"]:
            row_impacts.extend(self._registration_impacts(request))

        matched_match_count = 0
        if included["normalized"] or included["raw"] or included["replay"]:
            matched_match_count = self._matched_match_count(request)

        if included["normalized"]:
            row_impacts.extend(self._normalized_impacts(request))
            preserved_references.extend(
                self._preserved_normalized_references(request, matched_match_count)
            )

        if included["raw"]:
            row_impacts.append(
                DeletionRowImpact(
                    table="raw_player_snapshots",
                    category="raw_database",
                    relationship="player_owned",
                    row_count=self._count_raw_player_snapshots(request),
                    deletion_candidate=True,
                )
            )
            raw_files, raw_metadata = self._raw_file_catalog(request, file_limit)
            row_impacts.extend(raw_metadata)
        else:
            raw_files = DeletionFileCatalog.excluded("raw", file_limit)

        if included["replay"]:
            replay_files, replay_metadata = self._replay_file_catalog(request, file_limit)
            row_impacts.append(replay_metadata)
        else:
            replay_files = DeletionFileCatalog.excluded("replay", file_limit)

        warnings = self._warnings(
            included=included,
            raw_files=raw_files,
            replay_files=replay_files,
        )
        return DataDeletionImpactPreview(
            request_id=request.id,
            account_id=request.account_id,
            shard=request.shard,
            player_name=request.player_name,
            deletion_scope=scope,
            generated_at_kst=now_kst().isoformat(),
            file_limit_per_catalog=file_limit,
            included_sections=included,
            matched_match_count=matched_match_count,
            row_impacts=tuple(row_impacts),
            preserved_references=tuple(preserved_references),
            raw_files=raw_files,
            replay_files=replay_files,
            warnings=tuple(warnings),
        )

    def _registration_impacts(
        self,
        request: DataDeletionRequest,
    ) -> list[DeletionRowImpact]:
        direct_tables = ("registered_players", "player_aliases")
        impacts = [
            DeletionRowImpact(
                table=table,
                category="registration",
                relationship="player_owned",
                row_count=self._count(
                    f"""
                    SELECT COUNT(*) AS row_count
                    FROM {table}
                    WHERE account_id = %s AND shard = %s
                    """,
                    (request.account_id, request.shard),
                ),
                deletion_candidate=True,
            )
            for table in direct_tables
        ]
        impacts.append(
            DeletionRowImpact(
                table="player_collection_states",
                category="registration",
                relationship="tracking_state",
                row_count=self._count(
                    """
                    SELECT COUNT(*) AS row_count
                    FROM player_collection_states AS states
                    INNER JOIN registered_players AS players
                        ON players.id = states.registered_player_id
                    WHERE players.account_id = %s AND players.shard = %s
                    """,
                    (request.account_id, request.shard),
                ),
                deletion_candidate=True,
            )
        )
        return impacts

    def _matched_match_count(self, request: DataDeletionRequest) -> int:
        return self._count(
            """
            SELECT COUNT(DISTINCT participants.match_id) AS row_count
            FROM match_participants AS participants
            INNER JOIN matches AS target_matches
                ON target_matches.match_id = participants.match_id
            WHERE participants.account_id = %s AND target_matches.shard = %s
            """,
            (request.account_id, request.shard),
        )

    def _normalized_impacts(
        self,
        request: DataDeletionRequest,
    ) -> list[DeletionRowImpact]:
        impacts: list[DeletionRowImpact] = []
        for table in NORMALIZED_PLAYER_TABLES:
            impacts.append(
                DeletionRowImpact(
                    table=table,
                    category="normalized",
                    relationship="player_owned",
                    row_count=self._count(
                        f"""
                        SELECT COUNT(*) AS row_count
                        FROM {table} AS player_rows
                        INNER JOIN matches AS target_matches
                            ON target_matches.match_id = player_rows.match_id
                        WHERE player_rows.account_id = %s AND target_matches.shard = %s
                        """,
                        (request.account_id, request.shard),
                    ),
                    deletion_candidate=True,
                )
            )
        return impacts

    def _preserved_normalized_references(
        self,
        request: DataDeletionRequest,
        matched_match_count: int,
    ) -> list[DeletionRowImpact]:
        impacts = [
            DeletionRowImpact(
                table="player_combat_location_events.related_account_id",
                category="normalized_reference",
                relationship="referenced_by_other_player_rows",
                row_count=self._count(
                    """
                    SELECT COUNT(*) AS row_count
                    FROM player_combat_location_events AS player_rows
                    INNER JOIN matches AS target_matches
                        ON target_matches.match_id = player_rows.match_id
                    WHERE player_rows.related_account_id = %s
                      AND player_rows.account_id <> %s
                      AND target_matches.shard = %s
                    """,
                    (request.account_id, request.account_id, request.shard),
                ),
                deletion_candidate=False,
            ),
            DeletionRowImpact(
                table="matches",
                category="shared_match_context",
                relationship="shared_match",
                row_count=matched_match_count,
                deletion_candidate=False,
            ),
        ]
        for table in SHARED_MATCH_TABLES[1:]:
            impacts.append(
                DeletionRowImpact(
                    table=table,
                    category="shared_match_context",
                    relationship="shared_match",
                    row_count=self._count(
                        f"""
                        SELECT COUNT(*) AS row_count
                        FROM {table} AS shared_rows
                        INNER JOIN matches AS target_matches
                            ON target_matches.match_id = shared_rows.match_id
                        WHERE target_matches.shard = %s
                          AND EXISTS (
                              SELECT 1
                              FROM match_participants AS target_participant
                              WHERE target_participant.match_id = shared_rows.match_id
                                AND target_participant.account_id = %s
                          )
                        """,
                        (request.shard, request.account_id),
                    ),
                    deletion_candidate=False,
                )
            )
        return impacts

    def _count_raw_player_snapshots(self, request: DataDeletionRequest) -> int:
        return self._count(
            """
            SELECT COUNT(*) AS row_count
            FROM raw_player_snapshots
            WHERE account_id = %s AND shard = %s
            """,
            (request.account_id, request.shard),
        )

    def _raw_file_catalog(
        self,
        request: DataDeletionRequest,
        file_limit: int,
    ) -> tuple[DeletionFileCatalog, list[DeletionRowImpact]]:
        total_records = 0
        total_size_bytes = 0
        shared_match_records = 0
        metadata_impacts: list[DeletionRowImpact] = []
        for table, _ in RAW_PAYLOAD_TABLES:
            summary = self._raw_table_summary(table, request)
            row_count = _integer(summary.get("row_count"))
            total_records += row_count
            total_size_bytes += _integer(summary.get("total_size_bytes"))
            shared_match_records += _integer(summary.get("shared_match_count"))
            metadata_impacts.append(
                DeletionRowImpact(
                    table=table,
                    category="raw_metadata",
                    relationship="shared_match",
                    row_count=row_count,
                    deletion_candidate=False,
                )
            )

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT raw_candidates.*
                FROM (
                    SELECT
                        payloads.id AS record_id,
                        'raw_match_payloads' AS source_table,
                        'match' AS file_type,
                        payloads.match_id,
                        'local_file' AS storage_backend,
                        payloads.storage_root,
                        payloads.relative_path,
                        payloads.size_bytes AS declared_size_bytes,
                        payloads.sha256,
                        (
                            SELECT COUNT(*)
                            FROM match_participants AS all_participants
                            WHERE all_participants.match_id = payloads.match_id
                        ) AS participant_count
                    FROM raw_match_payloads AS payloads
                    INNER JOIN matches AS target_matches
                        ON target_matches.match_id = payloads.match_id
                    WHERE target_matches.shard = %s
                      AND EXISTS (
                          SELECT 1
                          FROM match_participants AS target_participant
                          WHERE target_participant.match_id = payloads.match_id
                            AND target_participant.account_id = %s
                      )
                    UNION ALL
                    SELECT
                        payloads.id AS record_id,
                        'raw_telemetry_payloads' AS source_table,
                        'telemetry' AS file_type,
                        payloads.match_id,
                        'local_file' AS storage_backend,
                        payloads.storage_root,
                        payloads.relative_path,
                        payloads.size_bytes AS declared_size_bytes,
                        payloads.sha256,
                        (
                            SELECT COUNT(*)
                            FROM match_participants AS all_participants
                            WHERE all_participants.match_id = payloads.match_id
                        ) AS participant_count
                    FROM raw_telemetry_payloads AS payloads
                    INNER JOIN matches AS target_matches
                        ON target_matches.match_id = payloads.match_id
                    WHERE target_matches.shard = %s
                      AND EXISTS (
                          SELECT 1
                          FROM match_participants AS target_participant
                          WHERE target_participant.match_id = payloads.match_id
                            AND target_participant.account_id = %s
                      )
                ) AS raw_candidates
                ORDER BY raw_candidates.match_id ASC, raw_candidates.file_type ASC
                LIMIT %s
                """,
                (
                    request.shard,
                    request.account_id,
                    request.shard,
                    request.account_id,
                    file_limit,
                ),
            )
            rows = list(cursor.fetchall())[:file_limit]

        files = tuple(
            self._inspect_file(
                row,
                category="raw",
                ownership="shared_match",
                deletion_candidate=False,
                expected_storage_root="PUBG_RAW_DATA_DIR",
                store=self.raw_store,
                shared_match=_integer(row.get("participant_count")) > 1,
                participant_count=_integer(row.get("participant_count")),
            )
            for row in rows
        )
        return (
            DeletionFileCatalog(
                category="raw",
                included=True,
                total_records=total_records,
                total_declared_size_bytes=total_size_bytes,
                deletion_candidate_records=0,
                shared_match_records=shared_match_records,
                limit=file_limit,
                files=files,
            ),
            metadata_impacts,
        )

    def _raw_table_summary(
        self,
        table: str,
        request: DataDeletionRequest,
    ) -> dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT
                    COUNT(*) AS row_count,
                    COALESCE(SUM(payloads.size_bytes), 0) AS total_size_bytes,
                    COALESCE(SUM(
                        CASE WHEN EXISTS (
                            SELECT 1
                            FROM match_participants AS other_participants
                            WHERE other_participants.match_id = payloads.match_id
                              AND other_participants.account_id <> %s
                        ) THEN 1 ELSE 0 END
                    ), 0) AS shared_match_count
                FROM {table} AS payloads
                INNER JOIN matches AS target_matches
                    ON target_matches.match_id = payloads.match_id
                WHERE target_matches.shard = %s
                  AND EXISTS (
                      SELECT 1
                      FROM match_participants AS target_participant
                      WHERE target_participant.match_id = payloads.match_id
                        AND target_participant.account_id = %s
                  )
                """,
                (request.account_id, request.shard, request.account_id),
            )
            return dict(cursor.fetchone() or {})

    def _replay_file_catalog(
        self,
        request: DataDeletionRequest,
        file_limit: int,
    ) -> tuple[DeletionFileCatalog, DeletionRowImpact]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    COUNT(*) AS row_count,
                    COALESCE(SUM(size_bytes), 0) AS total_size_bytes
                FROM replay_artifacts
                WHERE account_id = %s AND shard = %s
                """,
                (request.account_id, request.shard),
            )
            summary = dict(cursor.fetchone() or {})
            cursor.execute(
                """
                SELECT
                    id AS record_id,
                    'replay_artifacts' AS source_table,
                    artifact_type AS file_type,
                    match_id,
                    storage_backend,
                    storage_root,
                    relative_path,
                    size_bytes AS declared_size_bytes,
                    sha256
                FROM replay_artifacts
                WHERE account_id = %s AND shard = %s
                ORDER BY generated_at_kst DESC, id DESC
                LIMIT %s
                """,
                (request.account_id, request.shard, file_limit),
            )
            rows = list(cursor.fetchall())[:file_limit]

        total_records = _integer(summary.get("row_count"))
        files = tuple(
            self._inspect_file(
                row,
                category="replay",
                ownership="player_artifact",
                deletion_candidate=True,
                expected_storage_root="PUBG_REPLAY_DATA_DIR",
                store=self.replay_store,
                shared_match=False,
                participant_count=None,
            )
            for row in rows
        )
        return (
            DeletionFileCatalog(
                category="replay",
                included=True,
                total_records=total_records,
                total_declared_size_bytes=_integer(summary.get("total_size_bytes")),
                deletion_candidate_records=total_records,
                shared_match_records=0,
                limit=file_limit,
                files=files,
            ),
            DeletionRowImpact(
                table="replay_artifacts",
                category="replay_metadata",
                relationship="player_artifact",
                row_count=total_records,
                deletion_candidate=True,
            ),
        )

    def _inspect_file(
        self,
        row: dict[str, Any],
        *,
        category: str,
        ownership: str,
        deletion_candidate: bool,
        expected_storage_root: str,
        store: RawPayloadStore | ReplayArtifactStore,
        shared_match: bool,
        participant_count: int | None,
    ) -> DeletionFileImpact:
        storage_backend = str(row.get("storage_backend") or "")
        storage_root = str(row.get("storage_root") or "")
        relative_path = str(row.get("relative_path") or "")
        declared_size = _integer(row.get("declared_size_bytes"))
        root_matches = storage_root == expected_storage_root
        resolved_path: str | None = None
        actual_size: int | None = None
        path_safe = False
        exists = False
        size_matches: bool | None = None
        status = "unverified"
        error: str | None = None

        if storage_backend != "local_file":
            status = "unsupported_backend"
            error = f"unsupported storage backend: {storage_backend or 'empty'}"
        elif not root_matches:
            status = "unexpected_storage_root"
            error = f"expected storage root {expected_storage_root}"
        else:
            try:
                path = store.resolve_path(relative_path)
                path_safe = True
                resolved_path = str(path)
                exists = path.exists()
                if not exists:
                    status = "missing"
                elif not path.is_file():
                    status = "not_file"
                else:
                    actual_size = path.stat().st_size
                    size_matches = actual_size == declared_size
                    status = "present" if size_matches else "size_mismatch"
            except (RawStorageError, ReplayStorageError) as exc:
                status = "unsafe_path"
                error = str(exc)
            except OSError as exc:
                status = "unreadable"
                error = str(exc)

        return DeletionFileImpact(
            record_id=_integer(row.get("record_id")),
            source_table=str(row.get("source_table") or ""),
            file_type=str(row.get("file_type") or ""),
            match_id=str(row.get("match_id") or ""),
            ownership=ownership,
            deletion_candidate=deletion_candidate,
            storage_backend=storage_backend,
            storage_root=storage_root,
            relative_path=relative_path,
            resolved_path=resolved_path,
            declared_size_bytes=declared_size,
            actual_size_bytes=actual_size,
            sha256=str(row.get("sha256") or ""),
            storage_root_matches=root_matches,
            path_safe=path_safe,
            exists=exists,
            size_matches=size_matches,
            shared_match=shared_match,
            participant_count=participant_count,
            verification_status=status,
            verification_error=error,
        )

    def _count(self, query: str, params: tuple[Any, ...]) -> int:
        with self.connection.cursor() as cursor:
            cursor.execute(query, params)
            row = cursor.fetchone() or {}
        return _integer(row.get("row_count"))

    @staticmethod
    def _warnings(
        *,
        included: dict[str, bool],
        raw_files: DeletionFileCatalog,
        replay_files: DeletionFileCatalog,
    ) -> list[str]:
        warnings = [
            "Preview is read-only; no database rows or files were changed.",
            "Execution remains disabled and this preview is not an execution authorization.",
            "Deletion requests and immutable audit events are excluded from every scope.",
        ]
        if included["normalized"]:
            warnings.append(
                "Shared match context and references from other player rows are preserved, not deletion candidates."
            )
        if included["raw"]:
            warnings.append(
                "Raw match and telemetry files are match-shared and are protected from player-scoped deletion."
            )
        if included["replay"]:
            warnings.append(
                "Replay candidates include only artifacts whose account_id and shard match the target."
            )
        for catalog in (raw_files, replay_files):
            if catalog.included and catalog.truncated:
                warnings.append(
                    f"The {catalog.category} catalog is truncated at {catalog.limit} files; totals remain complete."
                )
            issue_count = sum(
                file.verification_status != "present" for file in catalog.files
            )
            if issue_count:
                warnings.append(
                    f"The {catalog.category} catalog has {issue_count} listed filesystem verification issue(s)."
                )
        return warnings


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError) as exc:
        raise DataDeletionPreviewError(f"invalid integer value in preview data: {value!r}.") from exc
