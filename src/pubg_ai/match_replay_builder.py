from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping
import json

from pubg_ai.map_snapshot_renderer import (
    DEFAULT_WORLD_SIZE_CM,
    MAP_WORLD_SIZE_CM,
    extend_line_to_world_bounds,
)
from pubg_ai.match_explorer import MatchExplorerError, MatchExplorerService
from pubg_ai.raw_storage import RawPayloadStore
from pubg_ai.replay_storage import (
    ReplayArtifactStore,
    StoredReplayArtifact,
    content_addressed_filename,
)
from pubg_ai.replay_timeline_builder import (
    TIMELINE_RENDERER_VERSION,
    _annotate_actor_events,
    _apply_plane_route_clock,
    _apply_timeline_clock,
    _assign_movement_modes,
    _assign_position_segments,
    _build_timeline_anchors,
    _circle_record,
    _damage_causer_label,
    _derive_drop_starts,
    _derive_engagements,
    _derive_timeline_origin,
    _filter_timeline_positions,
    _map_point,
    _vehicle_label,
    _weapon_label,
)
from pubg_ai.telemetry_movement_processor import (
    CarePackageEvent,
    CombatLocationEvent,
    LandingEvent,
    PhaseEvent,
    PlaneRoute,
    PositionSample,
    parse_care_package_events,
    parse_combat_location_events,
    parse_landing_events,
    parse_phase_events,
    parse_plane_route,
    parse_position_samples,
)
from pubg_ai.time_utils import now_kst


MATCH_TIMELINE_ARTIFACT_NAME = "match-timeline"


class MatchReplayError(RuntimeError):
    """Raised when an all-participant match replay cannot be generated."""


@dataclass(frozen=True)
class MatchReplayResult:
    match_id: str
    artifact_id: int
    generated: bool
    participant_count: int
    tracked_participant_count: int
    position_sample_count: int
    combat_event_count: int
    artifact: StoredReplayArtifact | None = None

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["artifact"] = self.artifact.to_record() if self.artifact else None
        return record


class MatchReplayProcessor:
    def __init__(
        self,
        connection: Any,
        raw_store: RawPayloadStore,
        replay_store: ReplayArtifactStore,
    ) -> None:
        self.connection = connection
        self.explorer = MatchExplorerService(connection, raw_store)
        self.replay_store = replay_store

    def generate(self, *, match_id: str, force: bool = False) -> MatchReplayResult:
        normalized_match_id = str(match_id or "").strip()
        if not normalized_match_id:
            raise MatchReplayError("매치 ID가 비어 있습니다.")
        existing = self._existing_artifact_id(normalized_match_id)
        if existing is not None and not force:
            counts = self._artifact_counts(existing)
            return MatchReplayResult(
                match_id=normalized_match_id,
                artifact_id=existing,
                generated=False,
                participant_count=counts[0],
                tracked_participant_count=counts[1],
                position_sample_count=counts[2],
                combat_event_count=counts[3],
            )

        try:
            source = self.explorer.get_replay_source(normalized_match_id)
        except MatchExplorerError as exc:
            raise MatchReplayError(str(exc)) from exc
        if source is None:
            raise MatchReplayError("저장된 매치를 찾을 수 없습니다.")

        payload = self._build_payload(source)
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        match = source["match"]
        stored = self.replay_store.write_bytes(
            artifact_type="timeline",
            shard=str(match.get("shard") or "unknown"),
            match_id=normalized_match_id,
            data=body,
            filename=content_addressed_filename(
                stem=MATCH_TIMELINE_ARTIFACT_NAME,
                data=body,
                suffix=".json",
            ),
            content_type="application/json",
            match_created_at=_datetime(match.get("created_at_kst")),
        )
        artifact_id = self._upsert_artifact(match=match, stored=stored)
        return MatchReplayResult(
            match_id=normalized_match_id,
            artifact_id=artifact_id,
            generated=True,
            participant_count=int(payload["team"]["member_count"]),
            tracked_participant_count=1 + len(payload["team_tracks"]),
            position_sample_count=(
                len(payload["positions"])
                + sum(len(track["positions"]) for track in payload["team_tracks"])
            ),
            combat_event_count=(
                len(payload["combat_events"])
                + sum(len(track["combat_events"]) for track in payload["team_tracks"])
            ),
            artifact=stored,
        )

    def _build_payload(self, source: Mapping[str, Any]) -> dict[str, Any]:
        match = dict(source["match"])
        participants = [dict(row) for row in source["participants"]]
        events = tuple(source["events"])
        match_id = str(match["match_id"])
        map_name = _text(match.get("map_name"))
        world_size_cm = MAP_WORLD_SIZE_CM.get(map_name or "", DEFAULT_WORLD_SIZE_CM)
        account_ids = {
            account_id
            for participant in participants
            if (account_id := _text(participant.get("account_id"))) is not None
        }
        if not account_ids:
            raise MatchReplayError("참가자 계정 정보가 없어 리플레이를 만들 수 없습니다.")

        positions_by_actor = _group(
            (
                _position_record(sample, world_size_cm)
                for sample in parse_position_samples(
                    events,
                    match_id=match_id,
                    tracked_account_ids=account_ids,
                )
            ),
            "account_id",
        )
        landing_by_actor = _group(
            (
                _landing_record(item, world_size_cm)
                for item in parse_landing_events(
                    events,
                    match_id=match_id,
                    tracked_account_ids=account_ids,
                )
            ),
            "account_id",
        )
        participant_index = {
            str(item["account_id"]): item
            for item in participants
            if item.get("account_id")
        }
        combat_by_actor = _group(
            (
                _combat_record(item, participant_index, world_size_cm)
                for item in parse_combat_location_events(
                    events,
                    match_id=match_id,
                    tracked_account_ids=account_ids,
                )
            ),
            "account_id",
        )
        plane_route = _plane_record(
            parse_plane_route(
                events,
                match_id=match_id,
                preferred_account_ids=account_ids,
            ),
            world_size_cm,
        )
        phase_events = [
            _phase_record(item, world_size_cm)
            for item in parse_phase_events(events, match_id=match_id)
        ]
        care_packages = [
            _care_record(item, world_size_cm)
            for item in parse_care_package_events(events, match_id=match_id)
        ]
        return self._assemble_payload(
            match=match,
            participants=participants,
            world_size_cm=world_size_cm,
            positions_by_actor=positions_by_actor,
            landing_by_actor=landing_by_actor,
            combat_by_actor=combat_by_actor,
            plane_route=plane_route,
            phase_events=phase_events,
            care_packages=care_packages,
        )

    def _assemble_payload(
        self,
        *,
        match: Mapping[str, Any],
        participants: list[dict[str, Any]],
        world_size_cm: float,
        positions_by_actor: defaultdict[str, list[dict[str, Any]]],
        landing_by_actor: defaultdict[str, list[dict[str, Any]]],
        combat_by_actor: defaultdict[str, list[dict[str, Any]]],
        plane_route: dict[str, Any] | None,
        phase_events: list[dict[str, Any]],
        care_packages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        members = [_member_record(item) for item in participants]
        for member in members:
            account_id = str(member["account_id"])
            positions_by_actor[account_id] = _filter_timeline_positions(
                positions_by_actor.get(account_id, []),
                plane_route=plane_route,
            )
        focus = _focus_member(members, positions_by_actor)
        focus_id = str(focus["account_id"])
        member_index = {str(item["account_id"]): item for item in members}
        for member in members:
            account_id = str(member["account_id"])
            member["is_self"] = account_id == focus_id
            member["position_sample_count"] = len(positions_by_actor.get(account_id, []))
            member["combat_event_count"] = len(combat_by_actor.get(account_id, []))

        tracks: list[dict[str, Any]] = []
        for member in members:
            account_id = str(member["account_id"])
            actor_positions = positions_by_actor.get(account_id, [])
            actor_landings = landing_by_actor.get(account_id, [])
            actor_combat = combat_by_actor.get(account_id, [])
            _annotate_actor_events(actor_landings, member)
            _annotate_actor_events(actor_combat, member)
            if account_id != focus_id and (actor_positions or actor_landings or actor_combat):
                tracks.append(
                    {
                        "account_id": account_id,
                        "name": member.get("name"),
                        "match_name": member.get("match_name"),
                        "team_id": member.get("team_id"),
                        "registered": member.get("registered"),
                        "is_ai_or_bot": member.get("is_ai_or_bot"),
                        "sample_count": len(actor_positions),
                        "positions": actor_positions,
                        "landings": actor_landings,
                        "combat_events": actor_combat,
                    }
                )

        focus_positions = positions_by_actor[focus_id]
        focus_landings = landing_by_actor.get(focus_id, [])
        focus_combat = combat_by_actor.get(focus_id, [])
        _annotate_actor_events(focus_landings, focus)
        _annotate_actor_events(focus_combat, focus)
        anchor_events = [*focus_positions, *(item for track in tracks for item in track["positions"])]
        anchors = _build_timeline_anchors(
            preferred_events=anchor_events,
            fallback_events=phase_events,
        )
        timeline_origin = _derive_timeline_origin(anchors)

        all_tracks: list[dict[str, Any]] = [
            {
                "positions": focus_positions,
                "landings": focus_landings,
                "combat_events": focus_combat,
                "actor": focus,
            }
        ]
        all_tracks.extend(
            {
                "positions": track["positions"],
                "landings": track["landings"],
                "combat_events": track["combat_events"],
                "actor": member_index[str(track["account_id"])],
                "track": track,
            }
            for track in tracks
        )
        for actor_track in all_tracks:
            _apply_timeline_clock(actor_track["positions"], anchors)
            _apply_timeline_clock(actor_track["landings"], anchors)
            _apply_timeline_clock(actor_track["combat_events"], anchors)
            _assign_position_segments(actor_track["positions"])
            drops = _derive_drop_starts(actor_track["positions"])
            _annotate_actor_events(drops, actor_track["actor"])
            _assign_movement_modes(
                actor_track["positions"],
                drop_starts=drops,
                landings=actor_track["landings"],
            )
            actor_track["drop_starts"] = drops
            if track := actor_track.get("track"):
                track["drop_starts"] = drops

        _apply_timeline_clock(care_packages, anchors)
        _apply_timeline_clock(phase_events, anchors)
        _apply_plane_route_clock(plane_route, anchors)
        all_combat = [
            *focus_combat,
            *(item for track in tracks for item in track["combat_events"]),
        ]
        engagements: list[dict[str, Any]] = []
        for team_ids in _team_account_groups(members):
            engagements.extend(
                _derive_engagements(
                    [
                        event
                        for event in all_combat
                        if event.get("actor_account_id") in team_ids
                    ],
                    team_account_ids=team_ids,
                )
            )
        engagements.sort(key=lambda item: float(item.get("start_time_seconds") or 0.0))
        return _payload_record(
            match=match,
            world_size_cm=world_size_cm,
            members=members,
            focus=focus,
            focus_positions=focus_positions,
            focus_landings=focus_landings,
            focus_combat=focus_combat,
            focus_drops=all_tracks[0]["drop_starts"],
            tracks=tracks,
            anchors=anchors,
            timeline_origin=timeline_origin,
            plane_route=plane_route,
            phase_events=phase_events,
            care_packages=care_packages,
            engagements=engagements,
        )

    def _existing_artifact_id(self, match_id: str) -> int | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id FROM replay_artifacts
                WHERE match_id = %s AND artifact_type = 'timeline'
                  AND artifact_name = %s AND account_id = ''
                  AND renderer_version = %s
                LIMIT 1
                """,
                (match_id, MATCH_TIMELINE_ARTIFACT_NAME, TIMELINE_RENDERER_VERSION),
            )
            row = cursor.fetchone()
        return int(row["id"]) if row else None

    def _artifact_counts(self, artifact_id: int) -> tuple[int, int, int, int]:
        from pubg_ai.replay_artifact_catalog import get_replay_artifact

        artifact = get_replay_artifact(self.connection, artifact_id)
        if artifact is None:
            return (0, 0, 0, 0)
        path = self.replay_store.resolve_path(artifact.relative_path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return (0, 0, 0, 0)
        tracks = payload.get("team_tracks") or []
        return (
            int(payload.get("team", {}).get("member_count") or 0),
            1 + len(tracks),
            len(payload.get("positions") or [])
            + sum(len(track.get("positions") or []) for track in tracks),
            len(payload.get("combat_events") or [])
            + sum(len(track.get("combat_events") or []) for track in tracks),
        )

    def _upsert_artifact(
        self,
        *,
        match: Mapping[str, Any],
        stored: StoredReplayArtifact,
    ) -> int:
        source = {
            "renderer": TIMELINE_RENDERER_VERSION,
            "scope": "all_participants",
            "tables": ["raw_telemetry_payloads", "match_participants"],
        }
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO replay_artifacts (
                    match_id, shard, artifact_type, artifact_name, account_id,
                    storage_backend, storage_root, relative_path, content_type,
                    size_bytes, sha256, renderer_version, source_tables, generated_at_kst
                )
                VALUES (%s, %s, %s, %s, '', %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    shard = VALUES(shard),
                    storage_backend = VALUES(storage_backend),
                    storage_root = VALUES(storage_root),
                    relative_path = VALUES(relative_path),
                    content_type = VALUES(content_type),
                    size_bytes = VALUES(size_bytes),
                    sha256 = VALUES(sha256),
                    source_tables = VALUES(source_tables),
                    generated_at_kst = VALUES(generated_at_kst)
                """,
                (
                    str(match["match_id"]),
                    str(match.get("shard") or ""),
                    stored.artifact_type,
                    MATCH_TIMELINE_ARTIFACT_NAME,
                    stored.storage_backend,
                    stored.storage_root,
                    stored.relative_path,
                    stored.content_type,
                    stored.size_bytes,
                    stored.sha256,
                    TIMELINE_RENDERER_VERSION,
                    json.dumps(source, ensure_ascii=False, separators=(",", ":")),
                    now_kst().replace(tzinfo=None),
                ),
            )
        artifact_id = self._existing_artifact_id(str(match["match_id"]))
        if artifact_id is None:
            raise MatchReplayError("리플레이 메타데이터 저장 결과를 찾을 수 없습니다.")
        return artifact_id


def _payload_record(
    *,
    match: Mapping[str, Any],
    world_size_cm: float,
    members: list[dict[str, Any]],
    focus: Mapping[str, Any],
    focus_positions: list[dict[str, Any]],
    focus_landings: list[dict[str, Any]],
    focus_combat: list[dict[str, Any]],
    focus_drops: list[dict[str, Any]],
    tracks: list[dict[str, Any]],
    anchors: list[tuple[float, float]],
    timeline_origin: datetime | None,
    plane_route: dict[str, Any] | None,
    phase_events: list[dict[str, Any]],
    care_packages: list[dict[str, Any]],
    engagements: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": TIMELINE_RENDERER_VERSION,
        "scope": "match",
        "generated_at_kst": now_kst().isoformat(),
        "time_origin_at_kst": timeline_origin.isoformat() if timeline_origin else None,
        "time_basis": "telemetry_elapsed_time_with_piecewise_timestamp_interpolation",
        "clock": {"anchor_count": len(anchors), "interpolation": "piecewise-linear"},
        "match": {
            "match_id": str(match["match_id"]),
            "shard": str(match.get("shard") or ""),
            "map_name": _text(match.get("map_name")),
            "game_mode": _text(match.get("game_mode")),
            "match_type": _text(match.get("match_type")),
            "created_at_kst": match.get("created_at_kst"),
            "duration_seconds": _integer(match.get("duration_seconds")),
            "world_size_cm": world_size_cm,
        },
        "player": {"account_id": str(focus["account_id"]), "name": focus.get("name")},
        "team": {
            "scope": "all_participants",
            "member_count": len(members),
            "registered_member_count": sum(bool(item["registered"]) for item in members),
            "registered_teammate_count": sum(
                bool(item["registered"]) and not item["is_self"] for item in members
            ),
            "track_count": len(tracks),
            "position_sample_count": len(focus_positions)
            + sum(track["sample_count"] for track in tracks),
            "members": members,
        },
        "counts": {
            "positions": len(focus_positions),
            "team_tracks": len(tracks),
            "team_position_samples": sum(track["sample_count"] for track in tracks),
            "team_combat_events": sum(len(track["combat_events"]) for track in tracks),
            "drop_starts": len(focus_drops),
            "landings": len(focus_landings),
            "combat_events": len(focus_combat),
            "engagements": len(engagements),
            "care_packages": len(care_packages),
            "has_plane_route": plane_route is not None,
            "phase_events": len(phase_events),
        },
        "plane_route": plane_route,
        "phase_events": phase_events,
        "positions": focus_positions,
        "team_tracks": tracks,
        "drop_starts": focus_drops,
        "landings": focus_landings,
        "combat_events": focus_combat,
        "engagements": engagements,
        "care_packages": care_packages,
    }


def _group(
    records: Iterable[dict[str, Any]],
    key: str,
) -> defaultdict[str, list[dict[str, Any]]]:
    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        value = _text(record.pop(key, None))
        if value is not None:
            grouped[value].append(record)
    return grouped


def _position_record(item: PositionSample, world_size_cm: float) -> dict[str, Any]:
    return {
        "account_id": item.account_id,
        "event_index": item.event_index,
        "event_at_kst": item.event_at_kst.isoformat() if item.event_at_kst else None,
        "common_is_game": item.common_is_game,
        "elapsed_time_seconds": item.elapsed_time_seconds,
        "num_alive_players": item.num_alive_players,
        "x": item.x,
        "y": item.y,
        "z": item.z,
        "map": _map_point(item.x, item.y, world_size_cm),
        "is_in_vehicle": item.is_in_vehicle,
        "vehicle_type": item.vehicle_type,
        "vehicle_id": item.vehicle_id,
        "vehicle_unique_id": item.vehicle_unique_id,
        "vehicle_label": _vehicle_label(item.vehicle_id, item.vehicle_type),
        "is_in_blue_zone": item.is_in_blue_zone,
        "is_in_red_zone": item.is_in_red_zone,
        "in_special_zone": item.in_special_zone,
        "is_dbno": item.is_dbno,
    }


def _landing_record(item: LandingEvent, world_size_cm: float) -> dict[str, Any]:
    return {
        "account_id": item.account_id,
        "event_index": item.event_index,
        "event_at_kst": item.event_at_kst.isoformat() if item.event_at_kst else None,
        "common_is_game": item.common_is_game,
        "x": item.x,
        "y": item.y,
        "z": item.z,
        "map": _map_point(item.x, item.y, world_size_cm),
        "distance_m": item.distance_m,
    }


def _combat_record(
    item: CombatLocationEvent,
    participants: Mapping[str, Mapping[str, Any]],
    world_size_cm: float,
) -> dict[str, Any]:
    raw = dict(item.raw_event)
    weapon = raw.get("weapon") if isinstance(raw.get("weapon"), Mapping) else {}
    weapon_code = _text(weapon.get("itemId")) or item.damage_causer_name
    related = participants.get(item.related_account_id or "", {})
    actor_map = _map_point(item.x, item.y, world_size_cm)
    related_map = _map_point(item.related_x, item.related_y, world_size_cm)
    return {
        "account_id": item.account_id,
        "event_index": item.event_index,
        "event_type": item.event_type,
        "action": item.action,
        "event_at_kst": item.event_at_kst.isoformat() if item.event_at_kst else None,
        "common_is_game": item.common_is_game,
        "related_account_id": item.related_account_id,
        "related_name": related.get("name"),
        "related_registered": bool(related.get("is_registered")),
        "related_is_ai_or_bot": bool(related.get("is_ai_or_bot")),
        "damage_type_category": item.damage_type_category,
        "damage_causer_name": item.damage_causer_name,
        "damage_causer_label": _damage_causer_label(item.damage_causer_name),
        "attack_id": _integer(raw.get("attackId")),
        "attack_type": _text(raw.get("attackType")),
        "fire_weapon_stack_count": _integer(raw.get("fireWeaponStackCount")),
        "damage": _number(raw.get("damage")),
        "weapon_code": weapon_code,
        "weapon_label": _weapon_label(weapon_code),
        "damage_reason": item.damage_reason,
        "is_headshot": item.is_headshot,
        "distance_m": item.distance_m,
        "x": item.x,
        "y": item.y,
        "z": item.z,
        "map": actor_map,
        "related_x": item.related_x,
        "related_y": item.related_y,
        "related_z": item.related_z,
        "related_map": related_map,
        "has_verified_direction": actor_map is not None and related_map is not None,
    }


def _care_record(item: CarePackageEvent, world_size_cm: float) -> dict[str, Any]:
    return {
        "event_index": item.event_index,
        "event_type": item.event_type,
        "event_at_kst": item.event_at_kst.isoformat() if item.event_at_kst else None,
        "common_is_game": item.common_is_game,
        "item_package_id": item.item_package_id,
        "item_count": item.item_count,
        "item_codes": item.item_codes,
        "x": item.x,
        "y": item.y,
        "z": item.z,
        "map": _map_point(item.x, item.y, world_size_cm),
    }


def _plane_record(
    item: PlaneRoute | None,
    world_size_cm: float,
) -> dict[str, Any] | None:
    if item is None:
        return None
    extended = extend_line_to_world_bounds(
        item.start_x,
        item.start_y,
        item.end_x,
        item.end_y,
        world_size_cm,
    )
    if extended is None:
        return None
    start_x, start_y, end_x, end_y = extended
    return {
        "source": item.source,
        "sample_count": item.sample_count,
        "start_event_index": item.start_event_index,
        "end_event_index": item.end_event_index,
        "start_event_at_kst": (
            item.start_event_at_kst.isoformat() if item.start_event_at_kst else None
        ),
        "end_event_at_kst": (
            item.end_event_at_kst.isoformat() if item.end_event_at_kst else None
        ),
        "start": {
            "x": start_x,
            "y": start_y,
            "z": item.start_z,
            "map": _map_point(start_x, start_y, world_size_cm),
        },
        "end": {
            "x": end_x,
            "y": end_y,
            "z": item.end_z,
            "map": _map_point(end_x, end_y, world_size_cm),
        },
        "sample_account_id": item.sample_account_id,
    }


def _phase_record(item: PhaseEvent, world_size_cm: float) -> dict[str, Any]:
    return {
        "event_index": item.event_index,
        "event_at_kst": item.event_at_kst.isoformat() if item.event_at_kst else None,
        "common_is_game": item.common_is_game,
        "elapsed_time_seconds": item.elapsed_time_seconds,
        "num_alive_players": item.num_alive_players,
        "num_alive_teams": item.num_alive_teams,
        "safety_zone": _circle_record(
            item.safety_zone_x,
            item.safety_zone_y,
            item.safety_zone_z,
            item.safety_zone_radius,
            world_size_cm,
        ),
        "poison_gas_warning": _circle_record(
            item.poison_gas_warning_x,
            item.poison_gas_warning_y,
            item.poison_gas_warning_z,
            item.poison_gas_warning_radius,
            world_size_cm,
        ),
        "red_zone": _circle_record(
            item.red_zone_x,
            item.red_zone_y,
            item.red_zone_z,
            item.red_zone_radius,
            world_size_cm,
        ),
        "black_zone": _circle_record(
            item.black_zone_x,
            item.black_zone_y,
            item.black_zone_z,
            item.black_zone_radius,
            world_size_cm,
        ),
    }


def _member_record(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "account_id": str(item["account_id"]),
        "name": _text(item.get("name")) or str(item["account_id"]),
        "match_name": _text(item.get("name")),
        "roster_id": _text(item.get("roster_id")),
        "team_id": _integer(item.get("team_id")),
        "win_place": _integer(item.get("win_place")),
        "kills": _integer(item.get("kills")),
        "assists": _integer(item.get("assists")),
        "damage_dealt": _number(item.get("damage_dealt")),
        "death_type": _text(item.get("death_type")),
        "is_ai_or_bot": bool(item.get("is_ai_or_bot")),
        "registered": bool(item.get("is_registered")),
        "is_self": False,
        "position_sample_count": 0,
        "combat_event_count": 0,
    }


def _focus_member(
    members: list[dict[str, Any]],
    positions: Mapping[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    candidates = [
        item for item in members if positions.get(str(item["account_id"]))
    ]
    if not candidates:
        raise MatchReplayError("게임 시작 이후의 참가자 위치 이벤트가 없습니다.")
    return (
        next(
            (
                item
                for item in candidates
                if item["registered"] and not item["is_ai_or_bot"]
            ),
            None,
        )
        or next(
            (item for item in candidates if not item["is_ai_or_bot"]),
            candidates[0],
        )
    )


def _team_account_groups(
    members: Iterable[Mapping[str, Any]],
) -> list[set[str]]:
    groups: defaultdict[str, set[str]] = defaultdict(set)
    for item in members:
        account_id = str(item["account_id"])
        team_id = item.get("team_id")
        key = f"team:{team_id}" if team_id is not None else f"player:{account_id}"
        groups[key].add(account_id)
    return list(groups.values())


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _integer(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
