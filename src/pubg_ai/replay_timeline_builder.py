from __future__ import annotations

from bisect import bisect_right
from dataclasses import asdict, dataclass
from datetime import datetime
from math import hypot
from typing import Any, Mapping
import json

from pubg_ai.code_translator import translate_code
from pubg_ai.map_snapshot_renderer import DEFAULT_WORLD_SIZE_CM, MAP_WORLD_SIZE_CM, extend_line_to_world_bounds
from pubg_ai.replay_path_policy import ReplayPathSampleState, select_replay_path_samples
from pubg_ai.replay_storage import (
    ReplayArtifactStore,
    StoredReplayArtifact,
    content_addressed_filename,
)
from pubg_ai.time_utils import KST, now_kst, to_kst


TIMELINE_RENDERER_VERSION = "player-timeline-v9"
MAX_PATH_SAMPLE_GAP_SECONDS = 45.0
MAX_PATH_HORIZONTAL_SPEED_MPS = 120.0
TRANSPORT_AIRCRAFT_ALTITUDE_CM = 100000.0
DROP_START_ALTITUDE_CM = 20000.0


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
                body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                stored = self.replay_store.write_bytes(
                    artifact_type="timeline",
                    shard=str(job["shard"]),
                    match_id=match_id,
                    data=body,
                    filename=content_addressed_filename(
                        stem=f"player-{_short_account_id(account_id)}-timeline",
                        data=body,
                        suffix=".json",
                    ),
                    content_type="application/json",
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
        where = """
            WHERE EXISTS (
                SELECT 1
                FROM player_position_samples candidate_positions
                WHERE candidate_positions.match_id = summaries.match_id
                  AND candidate_positions.account_id = summaries.account_id
                  AND candidate_positions.common_is_game > 0
                  AND NOT (
                      COALESCE(candidate_positions.is_in_vehicle, 0) = 1
                      AND COALESCE(candidate_positions.z, 0) >= 100000
                  )
            )
        """
        if not force:
            where += """
                AND NOT EXISTS (
                    SELECT 1
                    FROM replay_artifacts artifacts
                    WHERE artifacts.match_id = summaries.match_id
                      AND artifacts.account_id = summaries.account_id
                      AND artifacts.artifact_type = 'timeline'
                      AND artifacts.artifact_name = 'player-timeline'
                      AND artifacts.renderer_version = %s
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
                ((TIMELINE_RENDERER_VERSION, limit) if not force else (limit,)),
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
                  AND renderer_version = %s
                LIMIT 1
                """,
                (match_id, account_id, TIMELINE_RENDERER_VERSION),
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
            shard=str(job["shard"]),
            world_size_cm=world_size_cm,
        )
        care_packages = self._load_care_packages(match_id=match_id, world_size_cm=world_size_cm)
        plane_route = self._load_plane_route(match_id=match_id, world_size_cm=world_size_cm)
        positions = _filter_timeline_positions(positions, plane_route=plane_route)
        phase_events = self._load_phase_events(match_id=match_id, world_size_cm=world_size_cm)
        team_members = self._load_team_members(match_id=match_id, account_id=account_id, shard=str(job["shard"]))
        self_member = next(
            (member for member in team_members if member.get("account_id") == account_id),
            {
                "account_id": account_id,
                "name": _optional_text(job.get("current_name")),
                "registered": True,
                "is_self": True,
                "is_ai_or_bot": False,
            },
        )
        _annotate_actor_events(combat_events, self_member)
        _annotate_actor_events(landings, self_member)
        team_tracks = self._load_team_position_tracks(
            match_id=match_id,
            tracked_account_id=account_id,
            team_members=team_members,
            shard=str(job["shard"]),
            world_size_cm=world_size_cm,
            plane_route=plane_route,
        )
        _set_team_position_counts(team_members=team_members, tracked_account_id=account_id, positions=positions)
        _set_team_track_counts(team_members=team_members, team_tracks=team_tracks)
        _set_team_combat_counts(
            team_members=team_members,
            tracked_account_id=account_id,
            combat_events=combat_events,
            team_tracks=team_tracks,
        )
        anchor_events = list(positions)
        for track in team_tracks:
            anchor_events.extend(track["positions"])
        clock_anchors = _build_timeline_anchors(
            preferred_events=anchor_events,
            fallback_events=phase_events,
        )
        timeline_origin = _derive_timeline_origin(clock_anchors)
        _apply_timeline_clock(positions, clock_anchors)
        _apply_timeline_clock(landings, clock_anchors)
        _apply_timeline_clock(combat_events, clock_anchors)
        _apply_timeline_clock(care_packages, clock_anchors)
        _apply_timeline_clock(phase_events, clock_anchors)
        _assign_position_segments(positions)
        drop_starts = _derive_drop_starts(positions)
        _annotate_actor_events(drop_starts, self_member)
        _assign_movement_modes(positions, drop_starts=drop_starts, landings=landings)
        for track in team_tracks:
            _apply_timeline_clock(track["positions"], clock_anchors)
            _apply_timeline_clock(track["landings"], clock_anchors)
            _apply_timeline_clock(track["combat_events"], clock_anchors)
            _assign_position_segments(track["positions"])
            track["drop_starts"] = _derive_drop_starts(track["positions"])
            actor = {
                "account_id": track.get("account_id"),
                "name": track.get("name"),
                "registered": track.get("registered"),
                "is_self": False,
                "is_ai_or_bot": track.get("is_ai_or_bot"),
            }
            _annotate_actor_events(track["landings"], actor)
            _annotate_actor_events(track["drop_starts"], actor)
            _assign_movement_modes(
                track["positions"],
                drop_starts=track["drop_starts"],
                landings=track["landings"],
            )
        _apply_plane_route_clock(plane_route, clock_anchors)
        all_combat_events = [
            *combat_events,
            *(event for track in team_tracks for event in track["combat_events"]),
        ]
        engagements = _derive_engagements(all_combat_events)

        return {
            "schema_version": TIMELINE_RENDERER_VERSION,
            "generated_at_kst": now_kst().isoformat(),
            "time_origin_at_kst": timeline_origin.isoformat() if timeline_origin else None,
            "time_basis": "telemetry_elapsed_time_with_piecewise_timestamp_interpolation",
            "clock": {
                "anchor_count": len(clock_anchors),
                "interpolation": "piecewise-linear",
            },
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
            "team": {
                "member_count": len(team_members),
                "registered_member_count": sum(1 for member in team_members if member["registered"]),
                "registered_teammate_count": sum(
                    1 for member in team_members if member["registered"] and not member["is_self"]
                ),
                "track_count": len(team_tracks),
                "position_sample_count": len(positions) + sum(track["sample_count"] for track in team_tracks),
                "members": team_members,
            },
            "counts": {
                "positions": len(positions),
                "team_tracks": len(team_tracks),
                "team_position_samples": sum(track["sample_count"] for track in team_tracks),
                "team_combat_events": sum(len(track["combat_events"]) for track in team_tracks),
                "drop_starts": len(drop_starts),
                "landings": len(landings),
                "combat_events": len(combat_events),
                "engagements": len(engagements),
                "care_packages": len(care_packages),
                "has_plane_route": plane_route is not None,
                "phase_events": len(phase_events),
            },
            "plane_route": plane_route,
            "phase_events": phase_events,
            "positions": positions,
            "team_tracks": team_tracks,
            "drop_starts": drop_starts,
            "landings": landings,
            "combat_events": combat_events,
            "engagements": engagements,
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
                    vehicle_type,
                    vehicle_id,
                    vehicle_unique_id,
                    is_in_blue_zone,
                    is_in_red_zone,
                    in_special_zone,
                    is_dbno
                FROM player_position_samples
                WHERE match_id = %s AND account_id = %s
                  AND common_is_game > 0
                ORDER BY event_index ASC
                """,
                (match_id, account_id),
            )
            rows = cursor.fetchall()

        return [
            {
                "event_index": _int(row.get("event_index")),
                "event_at_kst": _datetime_record(row.get("event_at_kst")),
                "common_is_game": _optional_float(row.get("common_is_game")),
                "elapsed_time_seconds": _optional_float(row.get("elapsed_time_seconds")),
                "num_alive_players": _optional_int(row.get("num_alive_players")),
                "x": _optional_float(row.get("x")),
                "y": _optional_float(row.get("y")),
                "z": _optional_float(row.get("z")),
                "map": _map_point(row.get("x"), row.get("y"), world_size_cm),
                "is_in_vehicle": _optional_bool(row.get("is_in_vehicle")),
                "vehicle_type": _optional_text(row.get("vehicle_type")),
                "vehicle_id": _optional_text(row.get("vehicle_id")),
                "vehicle_unique_id": _optional_int(row.get("vehicle_unique_id")),
                "vehicle_label": _vehicle_label(row.get("vehicle_id"), row.get("vehicle_type")),
                "is_in_blue_zone": _optional_bool(row.get("is_in_blue_zone")),
                "is_in_red_zone": _optional_bool(row.get("is_in_red_zone")),
                "in_special_zone": _optional_text(row.get("in_special_zone")),
                "is_dbno": _optional_bool(row.get("is_dbno")),
            }
            for row in rows
        ]

    def _load_team_position_tracks(
        self,
        *,
        match_id: str,
        tracked_account_id: str,
        team_members: list[dict[str, Any]],
        shard: str,
        world_size_cm: float,
        plane_route: Mapping[str, Any] | None,
    ) -> list[dict[str, Any]]:
        tracks: list[dict[str, Any]] = []
        for member in team_members:
            account_id = _optional_text(member.get("account_id"))
            if account_id is None or account_id == tracked_account_id:
                continue
            positions = self._load_positions(
                match_id=match_id,
                account_id=account_id,
                world_size_cm=world_size_cm,
            )
            positions = _filter_timeline_positions(positions, plane_route=plane_route)
            landings = self._load_landings(
                match_id=match_id,
                account_id=account_id,
                world_size_cm=world_size_cm,
            )
            combat_events = self._load_combat_events(
                match_id=match_id,
                account_id=account_id,
                shard=shard,
                world_size_cm=world_size_cm,
            )
            if not positions and not landings and not combat_events:
                continue
            _annotate_actor_events(combat_events, member)
            tracks.append(
                {
                    "account_id": account_id,
                    "name": _optional_text(member.get("name")),
                    "match_name": _optional_text(member.get("match_name")),
                    "registered": bool(member.get("registered")),
                    "registered_active": _optional_bool(member.get("registered_active")),
                    "public_profile": _optional_bool(member.get("public_profile")),
                    "is_ai_or_bot": bool(member.get("is_ai_or_bot")),
                    "sample_count": len(positions),
                    "positions": positions,
                    "landings": landings,
                    "combat_events": combat_events,
                }
            )
        return tracks

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
                "common_is_game": _optional_float(row.get("common_is_game")),
                "x": _optional_float(row.get("x")),
                "y": _optional_float(row.get("y")),
                "z": _optional_float(row.get("z")),
                "map": _map_point(row.get("x"), row.get("y"), world_size_cm),
                "distance_m": _optional_float(row.get("distance_m")),
            }
            for row in rows
        ]

    def _load_combat_events(
        self,
        *,
        match_id: str,
        account_id: str,
        shard: str,
        world_size_cm: float,
    ) -> list[dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    events.related_account_id,
                    related_participant.name AS related_name,
                    related_participant.is_ai_or_bot AS related_is_ai_or_bot,
                    CASE WHEN related_registered.id IS NULL THEN 0 ELSE 1 END AS related_registered,
                    related_registered.active AS related_registered_active,
                    related_registered.current_name AS related_registered_name,
                    events.event_index,
                    events.event_type,
                    events.action,
                    events.event_at_kst,
                    events.common_is_game,
                    events.damage_type_category,
                    events.damage_causer_name,
                    events.damage_reason,
                    events.is_headshot,
                    events.distance_m,
                    events.x,
                    events.y,
                    events.z,
                    events.related_x,
                    events.related_y,
                    events.related_z,
                    events.raw_event
                FROM player_combat_location_events events
                LEFT JOIN match_participants related_participant
                    ON related_participant.match_id = events.match_id
                   AND related_participant.account_id = events.related_account_id
                LEFT JOIN registered_players related_registered
                    ON related_registered.account_id = events.related_account_id
                   AND related_registered.shard = %s
                WHERE events.match_id = %s AND events.account_id = %s
                ORDER BY events.event_index ASC, events.action ASC
                """,
                (shard, match_id, account_id),
            )
            rows = cursor.fetchall()

        result: list[dict[str, Any]] = []
        for row in rows:
            raw_event = _json_object(row.get("raw_event"))
            weapon = _json_object(raw_event.get("weapon"))
            weapon_code = _optional_text(weapon.get("itemId")) or _optional_text(row.get("damage_causer_name"))
            result.append(
                {
                "event_index": _int(row.get("event_index")),
                "event_type": _optional_text(row.get("event_type")),
                "action": _optional_text(row.get("action")),
                "event_at_kst": _datetime_record(row.get("event_at_kst")),
                "common_is_game": _optional_float(row.get("common_is_game")),
                "related_account_id": _optional_text(row.get("related_account_id")),
                "related_name": _optional_text(row.get("related_registered_name"))
                or _optional_text(row.get("related_name")),
                "related_registered": bool(row.get("related_registered")),
                "related_registered_active": _optional_bool(row.get("related_registered_active")),
                "related_is_ai_or_bot": _optional_bool(row.get("related_is_ai_or_bot")),
                "damage_type_category": _optional_text(row.get("damage_type_category")),
                "damage_causer_name": _optional_text(row.get("damage_causer_name")),
                "damage_causer_label": _damage_causer_label(row.get("damage_causer_name")),
                "attack_id": _optional_int(raw_event.get("attackId")),
                "attack_type": _optional_text(raw_event.get("attackType")),
                "fire_weapon_stack_count": _optional_int(raw_event.get("fireWeaponStackCount")),
                "damage": _optional_float(raw_event.get("damage")),
                "weapon_code": weapon_code,
                "weapon_label": _weapon_label(weapon_code),
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
                "has_verified_direction": (
                    _map_point(row.get("x"), row.get("y"), world_size_cm) is not None
                    and _map_point(row.get("related_x"), row.get("related_y"), world_size_cm) is not None
                ),
                }
            )
        return result

    def _load_team_members(self, *, match_id: str, account_id: str, shard: str) -> list[dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    teammate.account_id,
                    teammate.name,
                    teammate.roster_id,
                    teammate.team_id,
                    teammate.win_place,
                    teammate.kills,
                    teammate.assists,
                    teammate.damage_dealt,
                    teammate.death_type,
                    teammate.is_ai_or_bot,
                    CASE WHEN registered_players.id IS NULL THEN 0 ELSE 1 END AS registered,
                    registered_players.active AS registered_active,
                    registered_players.public_profile,
                    registered_players.current_name AS registered_name,
                    CASE WHEN teammate.account_id = %s THEN 1 ELSE 0 END AS is_self
                FROM match_participants self_participant
                INNER JOIN match_participants teammate
                    ON teammate.match_id = self_participant.match_id
                   AND (
                        (
                            self_participant.roster_id IS NOT NULL
                            AND teammate.roster_id = self_participant.roster_id
                        )
                        OR (
                            self_participant.roster_id IS NULL
                            AND self_participant.team_id IS NOT NULL
                            AND teammate.team_id = self_participant.team_id
                        )
                   )
                LEFT JOIN registered_players
                    ON registered_players.account_id = teammate.account_id
                   AND registered_players.shard = %s
                WHERE self_participant.match_id = %s
                  AND self_participant.account_id = %s
                ORDER BY
                    CASE WHEN teammate.account_id = %s THEN 0 ELSE 1 END,
                    CASE WHEN registered_players.id IS NULL THEN 1 ELSE 0 END,
                    teammate.name ASC,
                    teammate.account_id ASC
                """,
                (account_id, shard, match_id, account_id, account_id),
            )
            rows = cursor.fetchall()

        return [
            {
                "account_id": _optional_text(row.get("account_id")),
                "name": _optional_text(row.get("registered_name")) or _optional_text(row.get("name")),
                "match_name": _optional_text(row.get("name")),
                "roster_id": _optional_text(row.get("roster_id")),
                "team_id": _optional_int(row.get("team_id")),
                "win_place": _optional_int(row.get("win_place")),
                "kills": _optional_int(row.get("kills")),
                "assists": _optional_int(row.get("assists")),
                "damage_dealt": _optional_float(row.get("damage_dealt")),
                "death_type": _optional_text(row.get("death_type")),
                "is_ai_or_bot": bool(row.get("is_ai_or_bot")),
                "registered": bool(row.get("registered")),
                "registered_active": _optional_bool(row.get("registered_active")),
                "public_profile": _optional_bool(row.get("public_profile")),
                "is_self": bool(row.get("is_self")),
                "position_sample_count": 0,
                "combat_event_count": 0,
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
                "common_is_game": _optional_float(row.get("common_is_game")),
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

        extended = extend_line_to_world_bounds(
            _optional_float(row.get("start_x")),
            _optional_float(row.get("start_y")),
            _optional_float(row.get("end_x")),
            _optional_float(row.get("end_y")),
            world_size_cm,
        )
        if extended is None:
            return None
        start_x, start_y, end_x, end_y = extended

        return {
            "source": _optional_text(row.get("source")),
            "sample_count": _int(row.get("sample_count")),
            "start_event_index": _int(row.get("start_event_index")),
            "end_event_index": _int(row.get("end_event_index")),
            "start_event_at_kst": _datetime_record(row.get("start_event_at_kst")),
            "end_event_at_kst": _datetime_record(row.get("end_event_at_kst")),
            "start": {
                "x": start_x,
                "y": start_y,
                "z": _optional_float(row.get("start_z")),
                "map": _map_point(start_x, start_y, world_size_cm),
            },
            "end": {
                "x": end_x,
                "y": end_y,
                "z": _optional_float(row.get("end_z")),
                "map": _map_point(end_x, end_y, world_size_cm),
            },
            "sample_account_id": _optional_text(row.get("sample_account_id")),
        }

    def _load_phase_events(self, *, match_id: str, world_size_cm: float) -> list[dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    event_index,
                    event_at_kst,
                    common_is_game,
                    elapsed_time_seconds,
                    num_alive_players,
                    num_alive_teams,
                    safety_zone_x,
                    safety_zone_y,
                    safety_zone_z,
                    safety_zone_radius,
                    poison_gas_warning_x,
                    poison_gas_warning_y,
                    poison_gas_warning_z,
                    poison_gas_warning_radius,
                    red_zone_x,
                    red_zone_y,
                    red_zone_z,
                    red_zone_radius,
                    black_zone_x,
                    black_zone_y,
                    black_zone_z,
                    black_zone_radius
                FROM match_phase_events
                WHERE match_id = %s
                ORDER BY event_index ASC
                """,
                (match_id,),
            )
            rows = cursor.fetchall()

        return [
            {
                "event_index": _int(row.get("event_index")),
                "event_at_kst": _datetime_record(row.get("event_at_kst")),
                "common_is_game": _optional_float(row.get("common_is_game")),
                "elapsed_time_seconds": _optional_float(row.get("elapsed_time_seconds")),
                "num_alive_players": _optional_int(row.get("num_alive_players")),
                "num_alive_teams": _optional_int(row.get("num_alive_teams")),
                "safety_zone": _circle_record(
                    row.get("safety_zone_x"),
                    row.get("safety_zone_y"),
                    row.get("safety_zone_z"),
                    row.get("safety_zone_radius"),
                    world_size_cm,
                ),
                "poison_gas_warning": _circle_record(
                    row.get("poison_gas_warning_x"),
                    row.get("poison_gas_warning_y"),
                    row.get("poison_gas_warning_z"),
                    row.get("poison_gas_warning_radius"),
                    world_size_cm,
                ),
                "red_zone": _circle_record(
                    row.get("red_zone_x"),
                    row.get("red_zone_y"),
                    row.get("red_zone_z"),
                    row.get("red_zone_radius"),
                    world_size_cm,
                ),
                "black_zone": _circle_record(
                    row.get("black_zone_x"),
                    row.get("black_zone_y"),
                    row.get("black_zone_z"),
                    row.get("black_zone_radius"),
                    world_size_cm,
                ),
            }
            for row in rows
        ]

    def _upsert_artifact(self, *, job: Mapping[str, Any], stored: StoredReplayArtifact) -> None:
        source_tables = {
            "renderer": TIMELINE_RENDERER_VERSION,
            "tables": [
                "player_position_samples",
                "player_landing_events",
                "player_combat_location_events",
                "match_participants",
                "match_care_package_events",
                "match_plane_routes",
                "match_phase_events",
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


def _build_timeline_anchors(
    *,
    preferred_events: list[dict[str, Any]],
    fallback_events: list[dict[str, Any]],
) -> list[tuple[float, float]]:
    candidates = _timeline_anchor_candidates(preferred_events)
    if len(candidates) < 2:
        candidates = _timeline_anchor_candidates(fallback_events)
    candidates.sort(key=lambda item: (item[0], item[1]))

    anchors: list[tuple[float, float]] = []
    for timestamp, elapsed in candidates:
        if anchors and timestamp <= anchors[-1][0]:
            if timestamp == anchors[-1][0] and elapsed > anchors[-1][1]:
                anchors[-1] = (timestamp, elapsed)
            continue
        if anchors and elapsed < anchors[-1][1]:
            continue
        anchors.append((timestamp, elapsed))
    return anchors


def _timeline_anchor_candidates(events: list[dict[str, Any]]) -> list[tuple[float, float]]:
    candidates: list[tuple[float, float]] = []
    for event in events:
        event_at = _record_datetime(event.get("event_at_kst"))
        elapsed = _optional_float(event.get("elapsed_time_seconds"))
        if event_at is None or elapsed is None or elapsed < 0:
            continue
        candidates.append((event_at.timestamp(), elapsed))
    return candidates


def _derive_timeline_origin(anchors: list[tuple[float, float]]) -> datetime | None:
    if not anchors:
        return None
    timestamp, elapsed = anchors[0]
    return datetime.fromtimestamp(timestamp - elapsed, tz=KST)


def _apply_timeline_clock(
    events: list[dict[str, Any]],
    anchors: list[tuple[float, float]],
) -> None:
    for event in events:
        elapsed = _optional_float(event.get("elapsed_time_seconds"))
        if elapsed is not None and elapsed >= 0:
            event["time_seconds"] = elapsed
            continue
        event_at = _record_datetime(event.get("event_at_kst"))
        if event_at is not None and anchors:
            event["time_seconds"] = _interpolate_timeline_seconds(event_at.timestamp(), anchors)
        else:
            event["time_seconds"] = None
    events.sort(
        key=lambda event: (
            float("inf") if event.get("time_seconds") is None else float(event["time_seconds"]),
            _int(event.get("event_index")),
        )
    )


def _apply_plane_route_clock(
    route: dict[str, Any] | None,
    anchors: list[tuple[float, float]],
) -> None:
    if route is None or not anchors:
        return
    for prefix in ("start", "end"):
        event_at = _record_datetime(route.get(f"{prefix}_event_at_kst"))
        route[f"{prefix}_time_seconds"] = (
            _interpolate_timeline_seconds(event_at.timestamp(), anchors)
            if event_at is not None
            else None
        )


def _interpolate_timeline_seconds(
    timestamp: float,
    anchors: list[tuple[float, float]],
) -> float:
    anchor_times = [anchor[0] for anchor in anchors]
    right_index = bisect_right(anchor_times, timestamp)
    if right_index <= 0:
        anchor_at, anchor_elapsed = anchors[0]
        return max(0.0, anchor_elapsed - (anchor_at - timestamp))
    if right_index >= len(anchors):
        anchor_at, anchor_elapsed = anchors[-1]
        return max(0.0, anchor_elapsed + (timestamp - anchor_at))

    left_at, left_elapsed = anchors[right_index - 1]
    right_at, right_elapsed = anchors[right_index]
    span = right_at - left_at
    if span <= 0:
        return max(0.0, left_elapsed)
    ratio = max(0.0, min(1.0, (timestamp - left_at) / span))
    return max(0.0, left_elapsed + (right_elapsed - left_elapsed) * ratio)


def _assign_position_segments(events: list[dict[str, Any]]) -> None:
    segment_id = 0
    previous: dict[str, Any] | None = None
    for event in events:
        forced_reason = _optional_text(event.pop("_forced_segment_start_reason", None))
        reason = "initial"
        if previous is not None:
            reason = forced_reason or _path_break_reason(previous, event) or ""
            if reason:
                segment_id += 1
        event["segment_id"] = segment_id
        event["segment_start_reason"] = reason if previous is None or reason else None
        previous = event


def _filter_timeline_positions(
    positions: list[dict[str, Any]],
    *,
    plane_route: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    states = [
        ReplayPathSampleState(
            event_index=_int(position.get("event_index")),
            z_cm=_optional_float(position.get("z")),
            is_in_vehicle=position.get("is_in_vehicle") is True,
        )
        for position in positions
    ]
    selections = select_replay_path_samples(
        states,
        plane_start_event_index=(
            _optional_int(plane_route.get("start_event_index")) if plane_route else None
        ),
        plane_end_event_index=(
            _optional_int(plane_route.get("end_event_index")) if plane_route else None
        ),
        transport_aircraft_altitude_cm=TRANSPORT_AIRCRAFT_ALTITUDE_CM,
        drop_start_altitude_cm=DROP_START_ALTITUDE_CM,
    )
    filtered: list[dict[str, Any]] = []
    for selection in selections:
        position = positions[selection.source_index]
        if selection.force_segment_break:
            position["_forced_segment_start_reason"] = "transport_aircraft"
        filtered.append(position)
    return filtered


def _path_break_reason(left: Mapping[str, Any], right: Mapping[str, Any]) -> str | None:
    left_time = _optional_float(left.get("time_seconds"))
    right_time = _optional_float(right.get("time_seconds"))
    if left_time is None or right_time is None:
        return "missing_time"
    elapsed = right_time - left_time
    if elapsed <= 0:
        return "non_monotonic_time"
    if elapsed > MAX_PATH_SAMPLE_GAP_SECONDS:
        return "sample_gap"

    left_x = _optional_float(left.get("x"))
    left_y = _optional_float(left.get("y"))
    right_x = _optional_float(right.get("x"))
    right_y = _optional_float(right.get("y"))
    if None in {left_x, left_y, right_x, right_y}:
        return "missing_position"
    distance_m = hypot(float(right_x) - float(left_x), float(right_y) - float(left_y)) / 100.0
    if distance_m / elapsed > MAX_PATH_HORIZONTAL_SPEED_MPS:
        return "position_jump"

    left_z = _optional_float(left.get("z"))
    right_z = _optional_float(right.get("z"))
    if (
        left_z is not None
        and right_z is not None
        and right_z >= TRANSPORT_AIRCRAFT_ALTITUDE_CM
        and right_z - left_z >= 30000.0
    ):
        return "altitude_jump"
    return None


def _derive_drop_starts(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    starts: list[dict[str, Any]] = []
    seen_segments: set[int] = set()
    for position in positions:
        segment_id = _int(position.get("segment_id"))
        if segment_id in seen_segments:
            continue
        seen_segments.add(segment_id)
        altitude = _optional_float(position.get("z"))
        if (
            altitude is None
            or altitude < DROP_START_ALTITUDE_CM
            or position.get("is_in_vehicle") is True
        ):
            continue
        starts.append(
            {
                key: position.get(key)
                for key in (
                    "event_index",
                    "event_at_kst",
                    "common_is_game",
                    "elapsed_time_seconds",
                    "time_seconds",
                    "segment_id",
                    "x",
                    "y",
                    "z",
                    "map",
                )
            }
        )
    return starts


def _assign_movement_modes(
    positions: list[dict[str, Any]],
    *,
    drop_starts: list[dict[str, Any]],
    landings: list[dict[str, Any]],
) -> None:
    landing_times = sorted(
        time_value
        for landing in landings
        if (time_value := _optional_float(landing.get("time_seconds"))) is not None
    )
    airborne_intervals: list[tuple[float, float]] = []
    for start in drop_starts:
        start_time = _optional_float(start.get("time_seconds"))
        if start_time is None:
            continue
        landing_time = next((value for value in landing_times if value >= start_time), float("inf"))
        airborne_intervals.append((start_time, landing_time))

    labels = {
        "airborne": "낙하 중",
        "vehicle": "차량 이동",
        "on_foot": "도보 이동",
        "dbno": "기절 상태",
    }
    for position in positions:
        time_value = _optional_float(position.get("time_seconds"))
        if position.get("is_dbno") is True:
            mode = "dbno"
        elif position.get("is_in_vehicle") is True:
            mode = "vehicle"
        elif time_value is not None and any(start <= time_value <= end for start, end in airborne_intervals):
            mode = "airborne"
        else:
            mode = "on_foot"
        position["movement_mode"] = mode
        position["movement_label"] = labels[mode]


def _annotate_actor_events(events: list[dict[str, Any]], actor: Mapping[str, Any]) -> None:
    for event in events:
        event["actor_account_id"] = _optional_text(actor.get("account_id"))
        event["actor_name"] = _optional_text(actor.get("name"))
        event["actor_registered"] = bool(actor.get("registered"))
        event["actor_is_self"] = bool(actor.get("is_self"))
        event["actor_is_ai_or_bot"] = bool(actor.get("is_ai_or_bot"))


def _derive_engagements(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    combat_actions = {
        "shot",
        "throw",
        "melee",
        "attack",
        "hit_caused",
        "hit_taken",
        "dbno_caused",
        "dbno_taken",
        "kill",
        "death",
        "finish",
        "finished_taken",
    }
    by_actor: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        actor_id = _optional_text(event.get("actor_account_id"))
        time_value = _optional_float(event.get("time_seconds"))
        if actor_id is None or time_value is None or event.get("action") not in combat_actions:
            continue
        by_actor.setdefault(actor_id, []).append(event)

    engagements: list[dict[str, Any]] = []
    for actor_id, actor_events in by_actor.items():
        actor_events.sort(key=lambda item: (_optional_float(item.get("time_seconds")) or 0.0, _int(item.get("event_index"))))
        clusters: list[list[dict[str, Any]]] = []
        for event in actor_events:
            if not clusters:
                clusters.append([event])
                continue
            previous_time = _optional_float(clusters[-1][-1].get("time_seconds")) or 0.0
            current_time = _optional_float(event.get("time_seconds")) or 0.0
            if current_time - previous_time > 20.0:
                clusters.append([event])
            else:
                clusters[-1].append(event)

        for cluster_index, cluster in enumerate(clusters, start=1):
            actions = [str(event.get("action") or "") for event in cluster]
            map_points = [event["map"] for event in cluster if isinstance(event.get("map"), Mapping)]
            related_ids = sorted(
                {
                    related_id
                    for event in cluster
                    if (related_id := _optional_text(event.get("related_account_id"))) is not None
                }
            )
            weapon_labels = sorted(
                {
                    weapon
                    for event in cluster
                    if (weapon := _optional_text(event.get("weapon_label") or event.get("damage_causer_label"))) is not None
                }
            )
            wins = actions.count("kill") + actions.count("dbno_caused")
            losses = actions.count("death") + actions.count("dbno_taken")
            outcome = "won" if wins > losses else "lost" if losses > wins else "contested"
            first = cluster[0]
            last = cluster[-1]
            engagements.append(
                {
                    "engagement_id": f"{_short_account_id(actor_id)}-{cluster_index}",
                    "actor_account_id": actor_id,
                    "actor_name": first.get("actor_name"),
                    "actor_registered": bool(first.get("actor_registered")),
                    "actor_is_self": bool(first.get("actor_is_self")),
                    "start_time_seconds": first.get("time_seconds"),
                    "end_time_seconds": last.get("time_seconds"),
                    "start_at_kst": first.get("event_at_kst"),
                    "end_at_kst": last.get("event_at_kst"),
                    "event_count": len(cluster),
                    "shots": actions.count("shot"),
                    "throws": actions.count("throw"),
                    "melee_attacks": actions.count("melee"),
                    "hits_caused": actions.count("hit_caused"),
                    "hits_taken": actions.count("hit_taken"),
                    "dbnos_caused": actions.count("dbno_caused"),
                    "dbnos_taken": actions.count("dbno_taken"),
                    "kills": actions.count("kill"),
                    "deaths": actions.count("death"),
                    "damage_caused": round(
                        sum(_optional_float(event.get("damage")) or 0.0 for event in cluster if event.get("action") == "hit_caused"),
                        1,
                    ),
                    "outcome": outcome,
                    "opponent_account_ids": related_ids,
                    "weapons": weapon_labels,
                    "map": (
                        {
                            "x_pct": sum(float(point["x_pct"]) for point in map_points) / len(map_points),
                            "y_pct": sum(float(point["y_pct"]) for point in map_points) / len(map_points),
                        }
                        if map_points
                        else None
                    ),
                }
            )
    engagements.sort(key=lambda item: (_optional_float(item.get("start_time_seconds")) or 0.0, str(item.get("actor_name") or "")))
    return engagements


def _record_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return to_kst(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return to_kst(parsed)


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


def _circle_record(x: Any, y: Any, z: Any, radius: Any, world_size_cm: float) -> dict[str, Any] | None:
    px = _optional_float(x)
    py = _optional_float(y)
    pz = _optional_float(z)
    circle_radius = _optional_float(radius)
    if px is None or py is None or circle_radius is None or circle_radius <= 0 or world_size_cm <= 0:
        return None
    return {
        "x": px,
        "y": py,
        "z": pz,
        "radius": circle_radius,
        "radius_m": circle_radius / 100.0,
        "map": {
            **(_map_point(px, py, world_size_cm) or {"x_pct": 0.0, "y_pct": 0.0}),
            "radius_pct": circle_radius / world_size_cm,
        },
    }


def _damage_causer_label(value: Any) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    return translate_code(text, "damage_causer")


def _weapon_label(value: Any) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    category = "item" if text.startswith("Item_") else "damage_causer"
    return translate_code(text, category)


def _vehicle_label(vehicle_id: Any, vehicle_type: Any) -> str | None:
    code = _optional_text(vehicle_id) or _optional_text(vehicle_type)
    if code is None:
        return None
    translated = translate_code(code, "vehicle")
    if translated != code:
        return translated
    readable = code.removeprefix("BP_").removesuffix("_C").replace("_", " ").strip()
    return readable or code


def _set_team_position_counts(
    *,
    team_members: list[dict[str, Any]],
    tracked_account_id: str,
    positions: list[dict[str, Any]],
) -> None:
    for member in team_members:
        if member.get("account_id") == tracked_account_id:
            member["position_sample_count"] = len(positions)
            return


def _set_team_track_counts(*, team_members: list[dict[str, Any]], team_tracks: list[dict[str, Any]]) -> None:
    counts = {track["account_id"]: track["sample_count"] for track in team_tracks}
    for member in team_members:
        account_id = member.get("account_id")
        if account_id in counts:
            member["position_sample_count"] = counts[account_id]


def _set_team_combat_counts(
    *,
    team_members: list[dict[str, Any]],
    tracked_account_id: str,
    combat_events: list[dict[str, Any]],
    team_tracks: list[dict[str, Any]],
) -> None:
    counts = {track["account_id"]: len(track["combat_events"]) for track in team_tracks}
    counts[tracked_account_id] = len(combat_events)
    for member in team_members:
        member["combat_event_count"] = counts.get(member.get("account_id"), 0)


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    return value if isinstance(value, list) else []


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return dict(value) if isinstance(value, Mapping) else {}


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
