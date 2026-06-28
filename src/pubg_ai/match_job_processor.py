from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Mapping
import json

from pubg_ai.match_classification import classify_match_payload
from pubg_ai.match_population import detect_bot_player, summarize_match_population
from pubg_ai.parser_policy import CURRENT_MATCH_METADATA_PARSER_VERSION
from pubg_ai.pubg_client import PubgApiClient, PubgMatchDetails, PubgRateLimit
from pubg_ai.raw_storage import RawPayloadStore
from pubg_ai.time_utils import now_kst, to_kst


class MatchJobProcessingError(RuntimeError):
    """Raised when queued PUBG match detail jobs cannot be processed."""


@dataclass(frozen=True)
class MatchJobProcessingResult:
    picked_jobs: int
    fetched_matches: int
    stored_matches: int
    stored_participants: int
    queued_telemetry_jobs: int
    existing_telemetry_jobs: int
    missing_telemetry_jobs: int
    failed_jobs: int

    def to_record(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class ProcessedMatchJob:
    stored_participants: int
    telemetry_job_status: str
    rate_limit: PubgRateLimit


class MatchJobProcessor:
    def __init__(
        self,
        connection: Any,
        pubg_client: PubgApiClient,
        raw_store: RawPayloadStore,
    ) -> None:
        self.connection = connection
        self.pubg_client = pubg_client
        self.raw_store = raw_store

    def process_queued_matches(self, *, limit: int = 10) -> MatchJobProcessingResult:
        limit = max(1, min(limit, 500))
        jobs = self._list_queued_match_jobs(limit=limit)

        fetched_matches = 0
        stored_matches = 0
        stored_participants = 0
        queued_telemetry_jobs = 0
        existing_telemetry_jobs = 0
        missing_telemetry_jobs = 0
        failed_jobs = 0

        for job in jobs:
            if not self._mark_job_running(job):
                continue

            try:
                processed = self._process_job(job)
                self._mark_job_succeeded(job["id"], processed_rate_limit=processed.rate_limit)
            except Exception as exc:
                failed_jobs += 1
                self._mark_job_failed(job["id"], exc)
                continue

            fetched_matches += 1
            stored_matches += 1
            stored_participants += processed.stored_participants
            if processed.telemetry_job_status == "queued":
                queued_telemetry_jobs += 1
            elif processed.telemetry_job_status == "existing":
                existing_telemetry_jobs += 1
            else:
                missing_telemetry_jobs += 1

        return MatchJobProcessingResult(
            picked_jobs=len(jobs),
            fetched_matches=fetched_matches,
            stored_matches=stored_matches,
            stored_participants=stored_participants,
            queued_telemetry_jobs=queued_telemetry_jobs,
            existing_telemetry_jobs=existing_telemetry_jobs,
            missing_telemetry_jobs=missing_telemetry_jobs,
            failed_jobs=failed_jobs,
        )

    def _process_job(self, job: Mapping[str, Any]) -> ProcessedMatchJob:
        shard = _required_job_text(job.get("shard"), "shard")
        match_id = _required_job_text(job.get("target_id"), "target_id")

        match = self.pubg_client.fetch_match(shard, match_id)
        created_at = _parse_pubg_datetime(match.created_at)
        stored = self.raw_store.write_json(
            "match",
            match.shard,
            match.match_id,
            match.raw_payload,
            match_created_at=created_at,
        )
        if not self.raw_store.verify(stored):
            raise MatchJobProcessingError(f"raw match payload verification failed: {match.match_id}")

        classification = classify_match_payload(match.raw_payload, fallback_shard=match.shard)
        population = summarize_match_population(match.participants)
        fetched_at = _mysql_kst_now()

        self._upsert_match(
            match=match,
            created_at=created_at,
            fetched_at=fetched_at,
            team_mode=classification.team_mode,
            perspective=classification.perspective,
            total_players=population.total_players,
            human_players=population.human_players,
            bot_players=population.bot_players,
        )
        self._upsert_raw_match_payload(
            match=match,
            stored_relative_path=stored.relative_path,
            storage_root=stored.storage_root,
            compression=stored.compression,
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
            fetched_at=fetched_at,
        )
        participant_count = self._upsert_match_participants(match)
        telemetry_job_status = self._ensure_telemetry_job(match)

        return ProcessedMatchJob(
            stored_participants=participant_count,
            telemetry_job_status=telemetry_job_status,
            rate_limit=match.rate_limit,
        )

    def _list_queued_match_jobs(self, *, limit: int) -> list[dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, job_type, shard, target_id, status, attempts, next_run_at_kst
                FROM api_fetch_jobs
                WHERE job_type = 'match'
                  AND status = 'queued'
                  AND (next_run_at_kst IS NULL OR next_run_at_kst <= %s)
                ORDER BY next_run_at_kst ASC, id ASC
                LIMIT %s
                """,
                (_mysql_kst_now(), limit),
            )
            return list(cursor.fetchall())

    def _mark_job_running(self, job: Mapping[str, Any]) -> bool:
        timestamp = _mysql_kst_now()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE api_fetch_jobs
                SET status = 'running',
                    attempts = attempts + 1,
                    updated_at_kst = %s
                WHERE id = %s AND status = 'queued'
                """,
                (timestamp, job["id"]),
            )
            return cursor.rowcount == 1

    def _mark_job_succeeded(
        self,
        job_id: int,
        *,
        processed_rate_limit: PubgRateLimit | None,
    ) -> None:
        timestamp = _mysql_kst_now()
        rate_limit = processed_rate_limit or PubgRateLimit()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE api_fetch_jobs
                SET status = 'succeeded',
                    next_run_at_kst = NULL,
                    last_error = NULL,
                    rate_limit_limit = %s,
                    rate_limit_remaining = %s,
                    rate_limit_reset_epoch = %s,
                    updated_at_kst = %s
                WHERE id = %s
                """,
                (
                    rate_limit.limit,
                    rate_limit.remaining,
                    rate_limit.reset_epoch,
                    timestamp,
                    job_id,
                ),
            )

    def _mark_job_failed(self, job_id: int, exc: Exception) -> None:
        timestamp = _mysql_kst_now()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE api_fetch_jobs
                SET status = 'failed',
                    last_error = %s,
                    updated_at_kst = %s
                WHERE id = %s
                """,
                (_safe_error(exc), timestamp, job_id),
            )

    def _upsert_match(
        self,
        *,
        match: PubgMatchDetails,
        created_at: datetime | None,
        fetched_at: datetime,
        team_mode: str,
        perspective: str,
        total_players: int,
        human_players: int,
        bot_players: int,
    ) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO matches (
                    match_id,
                    shard,
                    map_name,
                    game_mode,
                    match_type,
                    team_mode,
                    perspective,
                    is_custom_match,
                    season_state,
                    created_at_kst,
                    duration_seconds,
                    telemetry_url,
                    total_players,
                    human_players,
                    bot_players,
                    fetched_at_kst,
                    updated_at_kst
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    shard = VALUES(shard),
                    map_name = VALUES(map_name),
                    game_mode = VALUES(game_mode),
                    match_type = VALUES(match_type),
                    team_mode = VALUES(team_mode),
                    perspective = VALUES(perspective),
                    is_custom_match = VALUES(is_custom_match),
                    season_state = VALUES(season_state),
                    created_at_kst = VALUES(created_at_kst),
                    duration_seconds = VALUES(duration_seconds),
                    telemetry_url = VALUES(telemetry_url),
                    total_players = VALUES(total_players),
                    human_players = VALUES(human_players),
                    bot_players = VALUES(bot_players),
                    fetched_at_kst = VALUES(fetched_at_kst),
                    updated_at_kst = VALUES(updated_at_kst)
                """,
                (
                    match.match_id,
                    match.shard,
                    match.map_name,
                    match.game_mode,
                    match.match_type,
                    team_mode,
                    perspective,
                    int(match.is_custom_match),
                    match.season_state,
                    _mysql_kst_datetime(created_at),
                    match.duration_seconds,
                    match.telemetry_url,
                    total_players,
                    human_players,
                    bot_players,
                    fetched_at,
                    fetched_at,
                ),
            )

    def _upsert_raw_match_payload(
        self,
        *,
        match: PubgMatchDetails,
        stored_relative_path: str,
        storage_root: str,
        compression: str,
        size_bytes: int,
        sha256: str,
        fetched_at: datetime,
    ) -> None:
        source_url = f"{self.pubg_client.base_url}/shards/{match.shard}/matches/{match.match_id}"
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO raw_match_payloads (
                    match_id,
                    shard,
                    storage_root,
                    relative_path,
                    compression,
                    size_bytes,
                    sha256,
                    source_url,
                    fetched_at_kst,
                    parser_version
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    shard = VALUES(shard),
                    storage_root = VALUES(storage_root),
                    relative_path = VALUES(relative_path),
                    compression = VALUES(compression),
                    size_bytes = VALUES(size_bytes),
                    sha256 = VALUES(sha256),
                    source_url = VALUES(source_url),
                    fetched_at_kst = VALUES(fetched_at_kst),
                    parser_version = VALUES(parser_version)
                """,
                (
                    match.match_id,
                    match.shard,
                    storage_root,
                    stored_relative_path,
                    compression,
                    size_bytes,
                    sha256,
                    source_url,
                    fetched_at,
                    CURRENT_MATCH_METADATA_PARSER_VERSION,
                ),
            )

    def _upsert_match_participants(self, match: PubgMatchDetails) -> int:
        roster_by_participant = _roster_by_participant(match.raw_payload)
        rows = []

        for participant in match.participants:
            participant_id = _optional_text(participant.get("id"))
            attributes = _mapping_value(participant.get("attributes"))
            stats = _mapping_value(attributes.get("stats"))
            account_id = _optional_text(stats.get("playerId")) or participant_id
            if not account_id:
                continue

            roster = roster_by_participant.get(participant_id or "")
            team_id = _optional_int(stats.get("teamId"))
            if team_id is None and roster is not None:
                team_id = _optional_int(roster.get("teamId"))

            is_bot, detection_source = detect_bot_player(
                account_id=account_id,
                player_id=_optional_text(stats.get("playerId")),
                name=_optional_text(stats.get("name")),
            )
            rows.append(
                (
                    match.match_id,
                    account_id,
                    _optional_text(stats.get("name")),
                    roster.get("roster_id") if roster else None,
                    team_id,
                    _optional_int(stats.get("winPlace")),
                    _optional_int(stats.get("kills")),
                    _optional_int(stats.get("assists")),
                    _optional_float(stats.get("damageDealt")),
                    _optional_text(stats.get("deathType")),
                    int(is_bot),
                    detection_source,
                    json.dumps(stats, ensure_ascii=False, separators=(",", ":")),
                )
            )

        if not rows:
            return 0

        timestamp = _mysql_kst_now()
        with self.connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO match_participants (
                    match_id,
                    account_id,
                    name,
                    roster_id,
                    team_id,
                    win_place,
                    kills,
                    assists,
                    damage_dealt,
                    death_type,
                    is_ai_or_bot,
                    ai_detection_source,
                    raw_stats
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    name = VALUES(name),
                    roster_id = VALUES(roster_id),
                    team_id = VALUES(team_id),
                    win_place = VALUES(win_place),
                    kills = VALUES(kills),
                    assists = VALUES(assists),
                    damage_dealt = VALUES(damage_dealt),
                    death_type = VALUES(death_type),
                    is_ai_or_bot = VALUES(is_ai_or_bot),
                    ai_detection_source = VALUES(ai_detection_source),
                    raw_stats = VALUES(raw_stats)
                """,
                rows,
            )
            cursor.execute(
                "UPDATE matches SET updated_at_kst = %s WHERE match_id = %s",
                (timestamp, match.match_id),
            )
        return len(rows)

    def _ensure_telemetry_job(self, match: PubgMatchDetails) -> str:
        if not match.telemetry_url:
            return "missing"
        if self._telemetry_payload_exists(match.match_id):
            return "existing"
        if self._fetch_job_exists("telemetry", match.shard, match.match_id):
            return "existing"

        timestamp = _mysql_kst_now()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO api_fetch_jobs (
                    job_type,
                    shard,
                    target_id,
                    status,
                    attempts,
                    next_run_at_kst,
                    created_at_kst,
                    updated_at_kst
                )
                VALUES ('telemetry', %s, %s, 'queued', 0, %s, %s, %s)
                """,
                (match.shard, match.match_id, timestamp, timestamp, timestamp),
            )
        return "queued"

    def _telemetry_payload_exists(self, match_id: str) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM raw_telemetry_payloads WHERE match_id = %s LIMIT 1",
                (match_id,),
            )
            return cursor.fetchone() is not None

    def _fetch_job_exists(self, job_type: str, shard: str, target_id: str) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM api_fetch_jobs
                WHERE job_type = %s AND shard = %s AND target_id = %s
                LIMIT 1
                """,
                (job_type, shard, target_id),
            )
            return cursor.fetchone() is not None


def _roster_by_participant(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    included = payload.get("included")
    if not isinstance(included, list):
        return {}

    roster_map: dict[str, dict[str, Any]] = {}
    for item in included:
        if not isinstance(item, Mapping) or item.get("type") != "roster":
            continue

        roster_id = _optional_text(item.get("id"))
        attributes = _mapping_value(item.get("attributes"))
        stats = _mapping_value(attributes.get("stats"))
        team_id = stats.get("teamId")

        relationships = _mapping_value(item.get("relationships"))
        participants = _mapping_value(relationships.get("participants"))
        refs = participants.get("data")
        if not isinstance(refs, list):
            continue

        for ref in refs:
            if not isinstance(ref, Mapping):
                continue
            participant_id = _optional_text(ref.get("id"))
            if participant_id:
                roster_map[participant_id] = {
                    "roster_id": roster_id,
                    "teamId": team_id,
                }

    return roster_map


def _parse_pubg_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _mysql_kst_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return to_kst(value).replace(tzinfo=None)


def _mysql_kst_now() -> datetime:
    return now_kst().replace(tzinfo=None)


def _required_job_text(value: Any, label: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise MatchJobProcessingError(f"queued match job is missing {label}.")
    return text


def _mapping_value(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_error(exc: Exception) -> str:
    message = f"{exc.__class__.__name__}: {exc}"
    return message[:1000]
