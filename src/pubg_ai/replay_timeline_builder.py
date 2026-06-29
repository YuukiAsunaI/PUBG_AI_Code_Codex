from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Mapping
import json

from pubg_ai.code_translator import translate_code
from pubg_ai.map_snapshot_renderer import DEFAULT_WORLD_SIZE_CM, MAP_WORLD_SIZE_CM
from pubg_ai.replay_storage import ReplayArtifactStore, StoredReplayArtifact
from pubg_ai.time_utils import now_kst, to_kst


TIMELINE_RENDERER_VERSION = "player-timeline-v1"


@dataclass(frozen=True)
class TimelineResult:
    candidate_timelines: int
    generated_timelines: int
    skipped_existing: int
    skipped_no_position: int
    failed_timelines: int
    artifacts: list[StoredReplayArtifact]

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["artifacts"] = [artifact.to_record() for artifact in self.artifacts]
        return record


class ReplayTimelineProcessor:
    def __init__(self, connection: Any, replay_store: ReplayArtifactStore) -> None:
        self.connection = connection
        self.replay_store = replay_store

    def generate_player_timelines(
        self,
        *,
        limit: int = 10,
        force: bool = False,
    ) -> TimelineResult:
        limit = max(1, min(int(limit), 200))
        jobs = self._list_timeline_jobs(limit=limit, force=force)

        generated = 0
        skipped_existing = 0
        skipped_no_position = 0
        failed = 0
        artifacts: list[StoredReplayArtifact] = []

        for job in jobs:
            match_id = str(job["match_id"])
            account_id = str(job["account_id"])

            if not force and self._artifact_exists(match_id=match_id, account_id=account_id):
                skipped_existing += 1
                continue

            try:
                payload = self._build_payload(job)
                if not payload["positions"]:
                    skipped_no_position += 1
                    continue
                stored = self.replay_store.write_json(
                    artifact_type="timeline",
                    shard=str(job["shard"]),
                    match_id=match_id,
                    payload=payload,
                    filename=f"player-{_short_account_id(account_id)}-timeline.json",
                    match_created_at=_optional_datetime(job.get("created_at_kst")),
                )
                self._upsert_artifact(job=job, stored=stored)
            except Exception:
                failed += 1
                continue

            generated += 1
            artifacts.append(stored)

        return TimelineResult(
            candidate_timelines=len(jobs),
            generated_timelines=generated,
            skipped_existing=skipped_existing,
            skipped_no_position=skipped_no_position,
            failed_timelines=failed,
            artifacts=artifacts,
        )

    def _list_timeline_jobs(self, *, limit: int, force: bool) -> list[dict[str, Any]]:
        where = ""
        if not force:
            where = """
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM replay_artifacts artifacts
                    WHERE artifacts.match_id = summaries.match_id
                      AND artifacts.account_id = summaries.account_id
                      AND artifacts.artifact_type = 'timeline'
                      AND artifacts.artifact_name = 'player-timeline'
                )
            """

        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT
                    summaries.match_id,
                    summaries.account_id,
                    matches.shard,
                    matches.map_name,
                    matches.game_mode,
                    matches.match_type,
                    matches.created_at_kst,
                    matches.duration_seconds,
                    registered_players.current_name
                FROM player_movement_summaries summaries
                INNER JOIN matches
                    ON matches.match_id = summaries.match_id
                LEFT JOIN registered_players
                    ON registered_players.account_id = summaries.account_id
                   AND registered_players.shard = matches.shard
                {where}
                ORDER BY matches.created_at_kst DESC, summaries.match_id ASC, summaries.account_id ASC
                LIMIT %s
                """,
                (limit,),
            )
            return list(cursor.fetchall())

    def _artifact_exists(self, *, match_id: str, account_id: str) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM replay_artifacts
                WHERE match_id = %s
                  AND account_id = %s
                  AND artifact_type = 'timeline'
                  AND artifact_name = 'player-timeline'
                LIMIT 1
                """,
                (match_id, account_id),
            )
            return cursor.fetchone() is not None

    def _build_payload(self, job: Mapping[str, Any]) -> dict[str, Any]:
        match_id = str(job["match_id"])
        account_id = str(job["account_id"])
        map_name = _optional_text(job.get("map_name"))
        world_size_cm = MAP_WORLD_SIZE_CM.get(map_name or "", DEFAULT_WORLD_SIZE_CM)

        positions = self._load_positions(match_id=match_id, account_id=account_id, world_size_cm=world_size_cm)
        landings = self._load_landings(match_id=match_id, account_id=account_id, world_size_cm=world_size_cm)
        combat_events = self._load_combat_events(
            match_id=match_id,
            account_id=account_id,
            world_size_cm=world_size_cm,
        )
        care_packages = self._load_care_packages(match_id=match_id, world_size_cm=world_size_cm)
        plane_route = self._load_plane_route(match_id=match_id, world_size_cm=world_size_cm)

        return {
            "schema_version": TIMELINE_RENDERER_VERSION,
            "generated_at_kst": now_kst().isoformat(),
            "match": {
                "match_id": match_id,
                "shard": str(job["shard"]),
                "map_name": map_name,
                "game_mode": _optional_text(job.get("game_mode")),
                "match_type": _optional_text(job.get("match_type")),
                "created_at_kst": _datetime_record(_optional_datetime(job.get("created_at_kst"))),
                "duration_seconds": _optional_int(job.get("duration_seconds")),
                "world_size_cm": world_size_cm,
            },
            "player": {
                "account_id": account_id,
                "name": _optional_text(job.get("current_name")),
            },
            "counts": {
                "positions": len(positions),
                "landings": len(landings),
                "combat_events": len(combat_events),
                "care_packages": len(care_packages),
                "has_plane_route": plane_route is not None,
            },
            "plane_route": plane_route,
            "positions": positions,
            "landings": landings,
            "combat_events": combat_events,
            "care_packages": care_packages,
        }

    def _load_positions(self, *, match_id: str, account_id: str, world_size_cm: float) -> list[dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    event_index,
                    event_at_kst,
                    common_is_game,
                    elapsed_time_seconds,
                    num_alive_players,
                    x,
                    y,
                    z,
                    is_in_vehicle,
                    is_in_blue_zone,
                    is_in_red_zone,
                    in_special_zone,
                    is_dbno
                FROM player_position_samples
                WHERE match_id = %s AND account_id = %s
                ORDER BY event_index ASC
                """,
                (match_id, account_id),
            )
            rows = cursor.fetchall()

        return [
            {
                "event_index": _int(row.get("event_index")),
                "event_at_kst": _datetime_record(row.get("event_at_kst")),
                "t": _optional_float(row.get("common_is_game")),
                "elapsed_time_seconds": _optional_float(row.get("elapsed_time_seconds")),
                "num_alive_players": _optional_int(row.get("num_alive_players")),
                "x": _optional_float(row.get("x")),
                "y": _optional_float(row.get("y")),
                "z": _optional_float(row.get("z")),
                "map": _map_point(row.get("x"), row.get("y"), world_size_cm),
                "is_in_vehicle": _optional_bool(row.get("is_in_vehicle")),
                "is_in_blue_zone": _optional_bool(row.get("is_in_blue_zone")),
                "is_in_red_zone": _optional_bool(row.get("is_in_red_zone")),
                "in_special_zone": _optional_text(row.get("in_special_zone")),
                "is_dbno": _optional_bool(row.get("is_dbno")),
            }
            for row in rows
        ]

    def _load_landings(self, *, match_id: str, account_id: str, world_size_cm: float) -> list[dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT event_index, event_at_kst, common_is_game, x, y, z, distance_m
                FROM player_landing_events
                WHERE match_id = %s AND account_id = %s
                ORDER BY event_index ASC
                """,
                (match_id, account_id),
            )
            rows = cursor.fetchall()

        return [
            {
                "event_index": _int(row.get("event_index")),
                "event_at_kst": _datetime_record(row.get("event_at_kst")),
                "t": _optional_float(row.get("common_is_game")),
                "x": _optional_float(row.get("x")),
                "y": _optional_float(row.get("y")),
                "z": _optional_float(row.get("z")),
                "map": _map_point(row.get("x"), row.get("y"), world_size_cm),
                "distance_m": _optional_float(row.get("distance_m")),
            }
            for row in rows
        ]

    def _load_combat_events(self, *, match_id: str, account_id: str, world_size_cm: float) -> list[dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    related_account_id,
                    event_index,
                    event_type,
                    action,
                    event_at_kst,
                    common_is_game,
                    damage_type_category,
                    damage_causer_name,
                    damage_reason,
                    is_headshot,
                    distance_m,
                    x,
                    y,
                    z,
                    related_x,
                    related_y,
                    related_z
                FROM player_combat_location_events
                WHERE match_id = %s AND account_id = %s
                ORDER BY event_index ASC, action ASC
                """,
                (match_id, account_id),
            )
            rows = cursor.fetchall()

        return [
            {
                "event_index": _int(row.get("event_index")),
                "event_type": _optional_text(row.get("event_type")),
                "action": _optional_text(row.get("action")),
                "event_at_kst": _datetime_record(row.get("event_at_kst")),
                "t": _optional_float(row.get("common_is_game")),
                "related_account_id": _optional_text(row.get("related_account_id")),
                "damage_type_category": _optional_text(row.get("damage_type_category")),
                "damage_causer_name": _optional_text(row.get("damage_causer_name")),
                "damage_causer_label": _damage_causer_label(row.get("damage_causer_name")),
                "damage_reason": _optional_text(row.get("damage_reason")),
                "is_headshot": bool(row.get("is_headshot")),
                "distance_m": _optional_float(row.get("distance_m")),
                "x": _optional_float(row.get("x")),
                "y": _optional_float(row.get("y")),
                "z": _optional_float(row.get("z")),
                "map": _map_point(row.get("x"), row.get("y"), world_size_cm),
                "related_x": _optional_float(row.get("related_x")),
                "related_y": _optional_float(row.get("related_y")),
                "related_z": _optional_float(row.get("related_z")),
                "related_map": _map_point(row.get("related_x"), row.get("related_y"), world_size_cm),
            }
            for row in rows
        ]

    def _load_care_packages(self, *, match_id: str, world_size_cm: float) -> list[dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT event_index, event_type, event_at_kst, common_is_game, item_package_id, item_count, item_codes, x, y, z
                FROM match_care_package_events
                WHERE match_id = %s
                ORDER BY event_index ASC
                """,
                (match_id,),
            )
            rows = cursor.fetchall()

        return [
            {
                "event_index": _int(row.get("event_index")),
                "event_type": _optional_text(row.get("event_type")),
                "event_at_kst": _datetime_record(row.get("event_at_kst")),
                "t": _optional_float(row.get("common_is_game")),
                "item_package_id": _optional_text(row.get("item_package_id")),
                "item_count": _int(row.get("item_count")),
                "item_codes": _json_list(row.get("item_codes")),
                "x": _optional_float(row.get("x")),
                "y": _optional_float(row.get("y")),
                "z": _optional_float(row.get("z")),
                "map": _map_point(row.get("x"), row.get("y"), world_size_cm),
            }
            for row in rows
        ]

    def _load_plane_route(self, *, match_id: str, world_size_cm: float) -> dict[str, Any] | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    source,
                    sample_count,
                    start_event_index,
                    end_event_index,
                    start_event_at_kst,
                    end_event_at_kst,
                    start_x,
                    start_y,
                    start_z,
                    end_x,
                    end_y,
                    end_z,
                    sample_account_id
                FROM match_plane_routes
                WHERE match_id = %s
                LIMIT 1
                """,
                (match_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None

        return {
            "source": _optional_text(row.get("source")),
            "sample_count": _int(row.get("sample_count")),
            "start_event_index": _int(row.get("start_event_index")),
            "end_event_index": _int(row.get("end_event_index")),
            "start_event_at_kst": _datetime_record(row.get("start_event_at_kst")),
            "end_event_at_kst": _datetime_record(row.get("end_event_at_kst")),
            "start": {
                "x": _optional_float(row.get("start_x")),
                "y": _optional_float(row.get("start_y")),
                "z": _optional_float(row.get("start_z")),
                "map": _map_point(row.get("start_x"), row.get("start_y"), world_size_cm),
            },
            "end": {
                "x": _optional_float(row.get("end_x")),
                "y": _optional_float(row.get("end_y")),
                "z": _optional_float(row.get("end_z")),
                "map": _map_point(row.get("end_x"), row.get("end_y"), world_size_cm),
            },
            "sample_account_id": _optional_text(row.get("sample_account_id")),
        }

    def _upsert_artifact(self, *, job: Mapping[str, Any], stored: StoredReplayArtifact) -> None:
        source_tables = {
            "renderer": TIMELINE_RENDERER_VERSION,
            "tables": [
                "player_position_samples",
                "player_landing_events",
                "player_combat_location_events",
                "match_care_package_events",
                "match_plane_routes",
            ],
        }
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO replay_artifacts (
                    match_id,
                    shard,
                    artifact_type,
                    artifact_name,
                    account_id,
                    storage_backend,
                    storage_root,
                    relative_path,
                    content_type,
                    size_bytes,
                    sha256,
                    renderer_version,
                    source_tables,
                    generated_at_kst
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    shard = VALUES(shard),
                    storage_backend = VALUES(storage_backend),
                    storage_root = VALUES(storage_root),
                    relative_path = VALUES(relative_path),
                    content_type = VALUES(content_type),
                    size_bytes = VALUES(size_bytes),
                    sha256 = VALUES(sha256),
                    renderer_version = VALUES(renderer_version),
                    source_tables = VALUES(source_tables),
                    generated_at_kst = VALUES(generated_at_kst)
                """,
                (
                    str(job["match_id"]),
                    str(job["shard"]),
                    stored.artifact_type,
                    "player-timeline",
                    str(job["account_id"]),
                    stored.storage_backend,
                    stored.storage_root,
                    stored.relative_path,
                    stored.content_type,
                    stored.size_bytes,
                    stored.sha256,
                    TIMELINE_RENDERER_VERSION,
                    json.dumps(source_tables, ensure_ascii=False, separators=(",", ":")),
                    _mysql_kst_now(),
                ),
            )


def _map_point(x: Any, y: Any, world_size_cm: float) -> dict[str, float] | None:
    px = _optional_float(x)
    py = _optional_float(y)
    if px is None or py is None or world_size_cm <= 0:
        return None
    clamped_x = max(0.0, min(world_size_cm, px))
    clamped_y = max(0.0, min(world_size_cm, py))
    return {
        "x_pct": clamped_x / world_size_cm,
        "y_pct": clamped_y / world_size_cm,
    }


def _damage_causer_label(value: Any) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    return translate_code(text, "damage_causer")


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    return value if isinstance(value, list) else []


def _datetime_record(value: Any) -> str | None:
    if isinstance(value, datetime):
        return to_kst(value).isoformat()
    return None


def _optional_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return to_kst(value)
    return None


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _int(value: Any) -> int:
    if value is None:
        return 0
    return int(value)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _short_account_id(account_id: str) -> str:
    return account_id.replace("account.", "")[:12] if account_id else "unknown"


def _mysql_kst_now() -> datetime:
    return now_kst().replace(tzinfo=None)
