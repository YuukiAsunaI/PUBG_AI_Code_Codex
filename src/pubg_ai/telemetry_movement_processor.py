from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from math import sqrt
from pathlib import Path
from typing import Any, Iterable, Mapping
import gzip
import json

from pubg_ai.raw_storage import RawPayloadStore
from pubg_ai.time_utils import now_kst, to_kst


class TelemetryMovementProcessingError(RuntimeError):
    """Raised when raw telemetry cannot be parsed into movement/location rows."""


@dataclass(frozen=True)
class PositionSample:
    match_id: str
    account_id: str
    event_index: int
    event_at_kst: datetime | None
    common_is_game: float | None
    elapsed_time_seconds: float | None
    num_alive_players: int | None
    x: float | None
    y: float | None
    z: float | None
    is_in_vehicle: bool | None
    is_in_blue_zone: bool | None
    is_in_red_zone: bool | None
    in_special_zone: str | None
    is_dbno: bool | None
    zone: Any

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["event_at_kst"] = self.event_at_kst.isoformat() if self.event_at_kst else None
        return record


@dataclass(frozen=True)
class LandingEvent:
    match_id: str
    account_id: str
    event_index: int
    event_at_kst: datetime | None
    common_is_game: float | None
    x: float | None
    y: float | None
    z: float | None
    distance_m: float | None

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["event_at_kst"] = self.event_at_kst.isoformat() if self.event_at_kst else None
        return record


@dataclass(frozen=True)
class MovementSummary:
    match_id: str
    account_id: str
    sample_count: int
    first_event_at_kst: datetime | None
    last_event_at_kst: datetime | None
    first_x: float | None
    first_y: float | None
    first_z: float | None
    last_x: float | None
    last_y: float | None
    last_z: float | None
    landing_event_at_kst: datetime | None
    landing_x: float | None
    landing_y: float | None
    landing_z: float | None
    landing_distance_m: float | None
    total_sampled_distance_m: float
    in_game_sampled_distance_m: float
    vehicle_sample_count: int
    dbno_sample_count: int
    max_altitude_z: float | None
    min_altitude_z: float | None

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        for key in ("first_event_at_kst", "last_event_at_kst", "landing_event_at_kst"):
            record[key] = record[key].isoformat() if record[key] else None
        return record


@dataclass(frozen=True)
class CombatLocationEvent:
    match_id: str
    account_id: str
    related_account_id: str | None
    event_index: int
    event_type: str
    action: str
    event_at_kst: datetime | None
    common_is_game: float | None
    damage_type_category: str | None
    damage_causer_name: str | None
    damage_reason: str | None
    is_headshot: bool
    distance_m: float | None
    x: float | None
    y: float | None
    z: float | None
    related_x: float | None
    related_y: float | None
    related_z: float | None
    raw_event: Mapping[str, Any]

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["event_at_kst"] = self.event_at_kst.isoformat() if self.event_at_kst else None
        return record


@dataclass(frozen=True)
class CarePackageEvent:
    match_id: str
    event_index: int
    event_type: str
    event_at_kst: datetime | None
    common_is_game: float | None
    item_package_id: str | None
    item_count: int
    item_codes: list[str]
    x: float | None
    y: float | None
    z: float | None
    raw_event: Mapping[str, Any]

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["event_at_kst"] = self.event_at_kst.isoformat() if self.event_at_kst else None
        return record


@dataclass(frozen=True)
class PlaneRoute:
    match_id: str
    source: str
    sample_count: int
    start_event_index: int
    end_event_index: int
    start_event_at_kst: datetime | None
    end_event_at_kst: datetime | None
    start_x: float | None
    start_y: float | None
    start_z: float | None
    end_x: float | None
    end_y: float | None
    end_z: float | None
    sample_account_id: str | None

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["start_event_at_kst"] = self.start_event_at_kst.isoformat() if self.start_event_at_kst else None
        record["end_event_at_kst"] = self.end_event_at_kst.isoformat() if self.end_event_at_kst else None
        return record


@dataclass(frozen=True)
class TelemetryMovementProcessingResult:
    candidate_payloads: int
    parsed_payloads: int
    skipped_existing: int
    skipped_no_tracked_player: int
    failed_payloads: int
    events_read: int
    position_samples: int
    landing_events: int
    movement_summaries: int
    combat_location_events: int
    care_package_events: int
    plane_routes: int

    def to_record(self) -> dict[str, int]:
        return asdict(self)


class TelemetryMovementProcessor:
    def __init__(self, connection: Any, raw_store: RawPayloadStore) -> None:
        self.connection = connection
        self.raw_store = raw_store

    def process_raw_telemetry(
        self,
        *,
        limit: int = 10,
        force: bool = False,
    ) -> TelemetryMovementProcessingResult:
        limit = max(1, min(limit, 200))
        payloads = self._list_raw_telemetry_payloads(limit=limit, force=force)

        parsed_payloads = 0
        skipped_existing = 0
        skipped_no_tracked_player = 0
        failed_payloads = 0
        events_read = 0
        position_sample_count = 0
        landing_event_count = 0
        movement_summary_count = 0
        combat_location_event_count = 0
        care_package_event_count = 0
        plane_route_count = 0

        for payload in payloads:
            match_id = str(payload["match_id"])
            shard = str(payload["shard"])

            if not force and self._movement_rows_exist(match_id):
                skipped_existing += 1
                continue

            tracked_account_ids = self._tracked_account_ids_for_match(match_id=match_id, shard=shard)
            if not tracked_account_ids:
                skipped_no_tracked_player += 1
                continue

            try:
                events = self._load_telemetry_events(payload)
                position_samples = parse_position_samples(
                    events,
                    match_id=match_id,
                    tracked_account_ids=tracked_account_ids,
                )
                landing_events = parse_landing_events(
                    events,
                    match_id=match_id,
                    tracked_account_ids=tracked_account_ids,
                )
                movement_summaries = summarize_movement(
                    position_samples,
                    landing_events=landing_events,
                    match_id=match_id,
                    tracked_account_ids=tracked_account_ids,
                )
                combat_location_events = parse_combat_location_events(
                    events,
                    match_id=match_id,
                    tracked_account_ids=tracked_account_ids,
                )
                care_package_events = parse_care_package_events(events, match_id=match_id)
                plane_route = parse_plane_route(
                    events,
                    match_id=match_id,
                    preferred_account_ids=tracked_account_ids,
                )
                self._replace_movement_rows(
                    match_id=match_id,
                    tracked_account_ids=tracked_account_ids,
                    position_samples=position_samples,
                    landing_events=landing_events,
                    movement_summaries=movement_summaries,
                    combat_location_events=combat_location_events,
                    care_package_events=care_package_events,
                    plane_route=plane_route,
                )
            except Exception:
                failed_payloads += 1
                continue

            parsed_payloads += 1
            events_read += len(events)
            position_sample_count += len(position_samples)
            landing_event_count += len(landing_events)
            movement_summary_count += len(movement_summaries)
            combat_location_event_count += len(combat_location_events)
            care_package_event_count += len(care_package_events)
            plane_route_count += 1 if plane_route is not None else 0

        return TelemetryMovementProcessingResult(
            candidate_payloads=len(payloads),
            parsed_payloads=parsed_payloads,
            skipped_existing=skipped_existing,
            skipped_no_tracked_player=skipped_no_tracked_player,
            failed_payloads=failed_payloads,
            events_read=events_read,
            position_samples=position_sample_count,
            landing_events=landing_event_count,
            movement_summaries=movement_summary_count,
            combat_location_events=combat_location_event_count,
            care_package_events=care_package_event_count,
            plane_routes=plane_route_count,
        )

    def _list_raw_telemetry_payloads(self, *, limit: int, force: bool) -> list[dict[str, Any]]:
        where = ""
        if not force:
            where = """
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM player_movement_summaries summaries
                    WHERE summaries.match_id = raw_telemetry_payloads.match_id
                )
            """

        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT id, match_id, shard, relative_path, compression
                FROM raw_telemetry_payloads
                {where}
                ORDER BY id ASC
                LIMIT %s
                """,
                (limit,),
            )
            return list(cursor.fetchall())

    def _movement_rows_exist(self, match_id: str) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM player_movement_summaries WHERE match_id = %s LIMIT 1",
                (match_id,),
            )
            return cursor.fetchone() is not None

    def _tracked_account_ids_for_match(self, *, match_id: str, shard: str) -> set[str]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT registered_players.account_id
                FROM registered_players
                INNER JOIN match_participants
                    ON match_participants.account_id = registered_players.account_id
                   AND match_participants.match_id = %s
                WHERE registered_players.shard = %s
                """,
                (match_id, shard),
            )
            return {str(row["account_id"]) for row in cursor.fetchall()}

    def _load_telemetry_events(self, payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        relative_path = _required_text(payload.get("relative_path"), "relative_path")
        compression = _required_text(payload.get("compression"), "compression")
        path = self.raw_store.resolve_path(relative_path)

        try:
            if compression == "gzip" or path.suffix == ".gz":
                with gzip.open(path, "rt", encoding="utf-8") as file:
                    loaded = json.load(file)
            else:
                with Path(path).open("r", encoding="utf-8") as file:
                    loaded = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            raise TelemetryMovementProcessingError(f"failed to read telemetry payload: {relative_path}") from exc

        if not isinstance(loaded, list):
            raise TelemetryMovementProcessingError("telemetry payload root must be a list.")

        return [event for event in loaded if isinstance(event, Mapping)]

    def _replace_movement_rows(
        self,
        *,
        match_id: str,
        tracked_account_ids: set[str],
        position_samples: list[PositionSample],
        landing_events: list[LandingEvent],
        movement_summaries: list[MovementSummary],
        combat_location_events: list[CombatLocationEvent],
        care_package_events: list[CarePackageEvent],
        plane_route: PlaneRoute | None,
    ) -> None:
        self._delete_existing_rows(match_id=match_id, account_ids=tracked_account_ids)
        self._insert_position_samples(position_samples)
        self._insert_landing_events(landing_events)
        self._insert_movement_summaries(movement_summaries)
        self._insert_combat_location_events(combat_location_events)
        self._insert_care_package_events(care_package_events)
        self._insert_plane_route(plane_route)

    def _delete_existing_rows(self, *, match_id: str, account_ids: set[str]) -> None:
        with self.connection.cursor() as cursor:
            if account_ids:
                placeholders = ", ".join(["%s"] * len(account_ids))
                params = [match_id, *sorted(account_ids)]
                for table_name in (
                    "player_position_samples",
                    "player_landing_events",
                    "player_movement_summaries",
                    "player_combat_location_events",
                ):
                    cursor.execute(
                        f"""
                        DELETE FROM {table_name}
                        WHERE match_id = %s AND account_id IN ({placeholders})
                        """,
                        params,
                    )

            cursor.execute("DELETE FROM match_care_package_events WHERE match_id = %s", (match_id,))
            cursor.execute("DELETE FROM match_plane_routes WHERE match_id = %s", (match_id,))

    def _insert_position_samples(self, samples: list[PositionSample]) -> None:
        if not samples:
            return

        timestamp = _mysql_kst_now()
        rows = [
            (
                sample.match_id,
                sample.account_id,
                sample.event_index,
                _mysql_datetime(sample.event_at_kst),
                sample.common_is_game,
                sample.elapsed_time_seconds,
                sample.num_alive_players,
                sample.x,
                sample.y,
                sample.z,
                sample.is_in_vehicle,
                sample.is_in_blue_zone,
                sample.is_in_red_zone,
                sample.in_special_zone,
                sample.is_dbno,
                json.dumps(sample.zone, ensure_ascii=False, separators=(",", ":")),
                timestamp,
            )
            for sample in samples
        ]

        with self.connection.cursor() as cursor:
            for chunk in _chunked(rows, 1000):
                cursor.executemany(
                    """
                    INSERT INTO player_position_samples (
                        match_id,
                        account_id,
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
                        is_dbno,
                        zone,
                        updated_at_kst
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    chunk,
                )

    def _insert_landing_events(self, events: list[LandingEvent]) -> None:
        if not events:
            return

        timestamp = _mysql_kst_now()
        rows = [
            (
                event.match_id,
                event.account_id,
                event.event_index,
                _mysql_datetime(event.event_at_kst),
                event.common_is_game,
                event.x,
                event.y,
                event.z,
                event.distance_m,
                timestamp,
            )
            for event in events
        ]

        with self.connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO player_landing_events (
                    match_id,
                    account_id,
                    event_index,
                    event_at_kst,
                    common_is_game,
                    x,
                    y,
                    z,
                    distance_m,
                    updated_at_kst
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                rows,
            )

    def _insert_movement_summaries(self, summaries: list[MovementSummary]) -> None:
        if not summaries:
            return

        timestamp = _mysql_kst_now()
        rows = [
            (
                summary.match_id,
                summary.account_id,
                summary.sample_count,
                _mysql_datetime(summary.first_event_at_kst),
                _mysql_datetime(summary.last_event_at_kst),
                summary.first_x,
                summary.first_y,
                summary.first_z,
                summary.last_x,
                summary.last_y,
                summary.last_z,
                _mysql_datetime(summary.landing_event_at_kst),
                summary.landing_x,
                summary.landing_y,
                summary.landing_z,
                summary.landing_distance_m,
                summary.total_sampled_distance_m,
                summary.in_game_sampled_distance_m,
                summary.vehicle_sample_count,
                summary.dbno_sample_count,
                summary.max_altitude_z,
                summary.min_altitude_z,
                timestamp,
            )
            for summary in summaries
        ]

        with self.connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO player_movement_summaries (
                    match_id,
                    account_id,
                    sample_count,
                    first_event_at_kst,
                    last_event_at_kst,
                    first_x,
                    first_y,
                    first_z,
                    last_x,
                    last_y,
                    last_z,
                    landing_event_at_kst,
                    landing_x,
                    landing_y,
                    landing_z,
                    landing_distance_m,
                    total_sampled_distance_m,
                    in_game_sampled_distance_m,
                    vehicle_sample_count,
                    dbno_sample_count,
                    max_altitude_z,
                    min_altitude_z,
                    updated_at_kst
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                rows,
            )

    def _insert_combat_location_events(self, events: list[CombatLocationEvent]) -> None:
        if not events:
            return

        timestamp = _mysql_kst_now()
        rows = [
            (
                event.match_id,
                event.account_id,
                event.related_account_id,
                event.event_index,
                event.event_type,
                event.action,
                _mysql_datetime(event.event_at_kst),
                event.common_is_game,
                event.damage_type_category,
                event.damage_causer_name,
                event.damage_reason,
                event.is_headshot,
                event.distance_m,
                event.x,
                event.y,
                event.z,
                event.related_x,
                event.related_y,
                event.related_z,
                json.dumps(event.raw_event, ensure_ascii=False, separators=(",", ":")),
                timestamp,
            )
            for event in events
        ]

        with self.connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO player_combat_location_events (
                    match_id,
                    account_id,
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
                    related_z,
                    raw_event,
                    updated_at_kst
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                rows,
            )

    def _insert_care_package_events(self, events: list[CarePackageEvent]) -> None:
        if not events:
            return

        timestamp = _mysql_kst_now()
        rows = [
            (
                event.match_id,
                event.event_index,
                event.event_type,
                _mysql_datetime(event.event_at_kst),
                event.common_is_game,
                event.item_package_id,
                event.item_count,
                json.dumps(event.item_codes, ensure_ascii=False, separators=(",", ":")),
                event.x,
                event.y,
                event.z,
                json.dumps(event.raw_event, ensure_ascii=False, separators=(",", ":")),
                timestamp,
            )
            for event in events
        ]

        with self.connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO match_care_package_events (
                    match_id,
                    event_index,
                    event_type,
                    event_at_kst,
                    common_is_game,
                    item_package_id,
                    item_count,
                    item_codes,
                    x,
                    y,
                    z,
                    raw_event,
                    updated_at_kst
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                rows,
            )

    def _insert_plane_route(self, route: PlaneRoute | None) -> None:
        if route is None:
            return

        timestamp = _mysql_kst_now()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO match_plane_routes (
                    match_id,
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
                    sample_account_id,
                    updated_at_kst
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    route.match_id,
                    route.source,
                    route.sample_count,
                    route.start_event_index,
                    route.end_event_index,
                    _mysql_datetime(route.start_event_at_kst),
                    _mysql_datetime(route.end_event_at_kst),
                    route.start_x,
                    route.start_y,
                    route.start_z,
                    route.end_x,
                    route.end_y,
                    route.end_z,
                    route.sample_account_id,
                    timestamp,
                ),
            )


def parse_position_samples(
    events: Iterable[Mapping[str, Any]],
    *,
    match_id: str,
    tracked_account_ids: set[str],
) -> list[PositionSample]:
    samples: list[PositionSample] = []

    for event_index, event in enumerate(events):
        if event.get("_T") != "LogPlayerPosition":
            continue

        character = _mapping_value(event.get("character"))
        account_id = _optional_text(character.get("accountId"))
        if account_id not in tracked_account_ids:
            continue

        location = _mapping_value(character.get("location"))
        samples.append(
            PositionSample(
                match_id=match_id,
                account_id=account_id,
                event_index=event_index,
                event_at_kst=_parse_event_time(event.get("_D")),
                common_is_game=_common_is_game(event),
                elapsed_time_seconds=_optional_float(event.get("elapsedTime")),
                num_alive_players=_optional_int(event.get("numAlivePlayers")),
                x=_optional_float(location.get("x")),
                y=_optional_float(location.get("y")),
                z=_optional_float(location.get("z")),
                is_in_vehicle=_optional_bool(character.get("isInVehicle")),
                is_in_blue_zone=_optional_bool(character.get("isInBlueZone")),
                is_in_red_zone=_optional_bool(character.get("isInRedZone")),
                in_special_zone=_optional_text(character.get("inSpecialZone")),
                is_dbno=_optional_bool(character.get("isDBNO")),
                zone=character.get("zone") if character.get("zone") is not None else [],
            )
        )

    return samples


def parse_landing_events(
    events: Iterable[Mapping[str, Any]],
    *,
    match_id: str,
    tracked_account_ids: set[str],
) -> list[LandingEvent]:
    landing_events: list[LandingEvent] = []

    for event_index, event in enumerate(events):
        if event.get("_T") != "LogParachuteLanding":
            continue

        character = _mapping_value(event.get("character"))
        account_id = _optional_text(character.get("accountId"))
        if account_id not in tracked_account_ids:
            continue

        location = _mapping_value(character.get("location"))
        landing_events.append(
            LandingEvent(
                match_id=match_id,
                account_id=account_id,
                event_index=event_index,
                event_at_kst=_parse_event_time(event.get("_D")),
                common_is_game=_common_is_game(event),
                x=_optional_float(location.get("x")),
                y=_optional_float(location.get("y")),
                z=_optional_float(location.get("z")),
                distance_m=_optional_float(event.get("distance")),
            )
        )

    return landing_events


def summarize_movement(
    position_samples: Iterable[PositionSample],
    *,
    landing_events: Iterable[LandingEvent],
    match_id: str,
    tracked_account_ids: set[str],
) -> list[MovementSummary]:
    samples_by_account: dict[str, list[PositionSample]] = defaultdict(list)
    for sample in position_samples:
        samples_by_account[sample.account_id].append(sample)

    landing_by_account: dict[str, LandingEvent] = {}
    for event in sorted(landing_events, key=lambda event: event.event_index):
        landing_by_account.setdefault(event.account_id, event)

    summaries: list[MovementSummary] = []
    for account_id in sorted(tracked_account_ids):
        samples = sorted(samples_by_account.get(account_id, []), key=lambda sample: sample.event_index)
        landing = landing_by_account.get(account_id)
        if not samples:
            summaries.append(
                MovementSummary(
                    match_id=match_id,
                    account_id=account_id,
                    sample_count=0,
                    first_event_at_kst=None,
                    last_event_at_kst=None,
                    first_x=None,
                    first_y=None,
                    first_z=None,
                    last_x=None,
                    last_y=None,
                    last_z=None,
                    landing_event_at_kst=landing.event_at_kst if landing else None,
                    landing_x=landing.x if landing else None,
                    landing_y=landing.y if landing else None,
                    landing_z=landing.z if landing else None,
                    landing_distance_m=landing.distance_m if landing else None,
                    total_sampled_distance_m=0.0,
                    in_game_sampled_distance_m=0.0,
                    vehicle_sample_count=0,
                    dbno_sample_count=0,
                    max_altitude_z=None,
                    min_altitude_z=None,
                )
            )
            continue

        total_distance = _sampled_distance_m(samples, in_game_only=False)
        in_game_distance = _sampled_distance_m(samples, in_game_only=True)
        z_values = [sample.z for sample in samples if sample.z is not None]
        first = samples[0]
        last = samples[-1]
        summaries.append(
            MovementSummary(
                match_id=match_id,
                account_id=account_id,
                sample_count=len(samples),
                first_event_at_kst=first.event_at_kst,
                last_event_at_kst=last.event_at_kst,
                first_x=first.x,
                first_y=first.y,
                first_z=first.z,
                last_x=last.x,
                last_y=last.y,
                last_z=last.z,
                landing_event_at_kst=landing.event_at_kst if landing else None,
                landing_x=landing.x if landing else None,
                landing_y=landing.y if landing else None,
                landing_z=landing.z if landing else None,
                landing_distance_m=landing.distance_m if landing else None,
                total_sampled_distance_m=total_distance,
                in_game_sampled_distance_m=in_game_distance,
                vehicle_sample_count=sum(1 for sample in samples if sample.is_in_vehicle),
                dbno_sample_count=sum(1 for sample in samples if sample.is_dbno),
                max_altitude_z=max(z_values) if z_values else None,
                min_altitude_z=min(z_values) if z_values else None,
            )
        )

    return summaries


def parse_combat_location_events(
    events: Iterable[Mapping[str, Any]],
    *,
    match_id: str,
    tracked_account_ids: set[str],
) -> list[CombatLocationEvent]:
    location_events: list[CombatLocationEvent] = []

    for event_index, event in enumerate(events):
        event_type = event.get("_T")
        if event_type == "LogPlayerMakeGroggy":
            attacker = _mapping_value(event.get("attacker"))
            victim = _mapping_value(event.get("victim"))
            attacker_account_id = _optional_text(attacker.get("accountId"))
            victim_account_id = _optional_text(victim.get("accountId"))

            if attacker_account_id in tracked_account_ids:
                location_events.append(
                    _combat_location_record(
                        event=event,
                        match_id=match_id,
                        event_index=event_index,
                        event_type="LogPlayerMakeGroggy",
                        action="dbno_caused",
                        account=attacker,
                        related=victim,
                        damage_info=event,
                    )
                )
            if victim_account_id in tracked_account_ids:
                location_events.append(
                    _combat_location_record(
                        event=event,
                        match_id=match_id,
                        event_index=event_index,
                        event_type="LogPlayerMakeGroggy",
                        action="dbno_taken",
                        account=victim,
                        related=attacker,
                        damage_info=event,
                    )
                )

        elif event_type == "LogPlayerKillV2":
            victim = _mapping_value(event.get("victim"))
            killer = _mapping_value(event.get("killer"))
            finisher = _mapping_value(event.get("finisher"))
            victim_account_id = _optional_text(victim.get("accountId"))
            killer_account_id = _optional_text(killer.get("accountId"))
            finisher_account_id = _optional_text(finisher.get("accountId"))

            if killer_account_id in tracked_account_ids:
                location_events.append(
                    _combat_location_record(
                        event=event,
                        match_id=match_id,
                        event_index=event_index,
                        event_type="LogPlayerKillV2",
                        action="kill",
                        account=killer,
                        related=victim,
                        damage_info=_mapping_value(event.get("killerDamageInfo")),
                    )
                )
            if victim_account_id in tracked_account_ids:
                location_events.append(
                    _combat_location_record(
                        event=event,
                        match_id=match_id,
                        event_index=event_index,
                        event_type="LogPlayerKillV2",
                        action="death",
                        account=victim,
                        related=killer,
                        damage_info=_mapping_value(event.get("killerDamageInfo")),
                    )
                )
            if finisher_account_id in tracked_account_ids:
                location_events.append(
                    _combat_location_record(
                        event=event,
                        match_id=match_id,
                        event_index=event_index,
                        event_type="LogPlayerKillV2",
                        action="finish",
                        account=finisher,
                        related=victim,
                        damage_info=_mapping_value(event.get("finishDamageInfo")),
                    )
                )
            if victim_account_id in tracked_account_ids and finisher_account_id and finisher_account_id != victim_account_id:
                location_events.append(
                    _combat_location_record(
                        event=event,
                        match_id=match_id,
                        event_index=event_index,
                        event_type="LogPlayerKillV2",
                        action="finished_taken",
                        account=victim,
                        related=finisher,
                        damage_info=_mapping_value(event.get("finishDamageInfo")),
                    )
                )

        elif event_type == "LogPlayerRevive":
            reviver = _mapping_value(event.get("reviver"))
            victim = _mapping_value(event.get("victim"))
            reviver_account_id = _optional_text(reviver.get("accountId"))
            victim_account_id = _optional_text(victim.get("accountId"))
            revive_info = {
                "damageTypeCategory": "Revive",
                "damageReason": "TraumaBag" if event.get("useTraumaBag") is True else "Revive",
                "distance": _character_xy_distance_cm(reviver, victim),
            }

            if reviver_account_id in tracked_account_ids:
                location_events.append(
                    _combat_location_record(
                        event=event,
                        match_id=match_id,
                        event_index=event_index,
                        event_type="LogPlayerRevive",
                        action="revive_given",
                        account=reviver,
                        related=victim,
                        damage_info=revive_info,
                    )
                )
            if victim_account_id in tracked_account_ids:
                location_events.append(
                    _combat_location_record(
                        event=event,
                        match_id=match_id,
                        event_index=event_index,
                        event_type="LogPlayerRevive",
                        action="revive_received",
                        account=victim,
                        related=reviver,
                        damage_info=revive_info,
                    )
                )

    return location_events


def parse_care_package_events(
    events: Iterable[Mapping[str, Any]],
    *,
    match_id: str,
) -> list[CarePackageEvent]:
    care_events: list[CarePackageEvent] = []

    for event_index, event in enumerate(events):
        event_type = _optional_text(event.get("_T"))
        if event_type not in {"LogCarePackageSpawn", "LogCarePackageLand"}:
            continue

        item_package = _mapping_value(event.get("itemPackage"))
        location = _mapping_value(item_package.get("location"))
        items = item_package.get("items")
        item_codes = [
            item_id
            for item in items
            if isinstance(item, Mapping)
            for item_id in [_optional_text(item.get("itemId"))]
            if item_id is not None
        ] if isinstance(items, list) else []

        care_events.append(
            CarePackageEvent(
                match_id=match_id,
                event_index=event_index,
                event_type=event_type,
                event_at_kst=_parse_event_time(event.get("_D")),
                common_is_game=_common_is_game(event),
                item_package_id=_optional_text(item_package.get("itemPackageId")),
                item_count=len(item_codes),
                item_codes=item_codes,
                x=_optional_float(location.get("x")),
                y=_optional_float(location.get("y")),
                z=_optional_float(location.get("z")),
                raw_event=event,
            )
        )

    return care_events


def parse_plane_route(
    events: Iterable[Mapping[str, Any]],
    *,
    match_id: str,
    preferred_account_ids: set[str],
) -> PlaneRoute | None:
    aircraft_samples_by_account: dict[str, list[PositionSample]] = defaultdict(list)
    fallback_aircraft_samples: list[PositionSample] = []

    for event_index, event in enumerate(events):
        if event.get("_T") != "LogPlayerPosition":
            continue

        character = _mapping_value(event.get("character"))
        if not _looks_like_aircraft_sample(event, character):
            continue

        account_id = _optional_text(character.get("accountId"))
        location = _mapping_value(character.get("location"))
        sample = PositionSample(
            match_id=match_id,
            account_id=account_id or "",
            event_index=event_index,
            event_at_kst=_parse_event_time(event.get("_D")),
            common_is_game=_common_is_game(event),
            elapsed_time_seconds=_optional_float(event.get("elapsedTime")),
            num_alive_players=_optional_int(event.get("numAlivePlayers")),
            x=_optional_float(location.get("x")),
            y=_optional_float(location.get("y")),
            z=_optional_float(location.get("z")),
            is_in_vehicle=_optional_bool(character.get("isInVehicle")),
            is_in_blue_zone=_optional_bool(character.get("isInBlueZone")),
            is_in_red_zone=_optional_bool(character.get("isInRedZone")),
            in_special_zone=_optional_text(character.get("inSpecialZone")),
            is_dbno=_optional_bool(character.get("isDBNO")),
            zone=character.get("zone") if character.get("zone") is not None else [],
        )
        fallback_aircraft_samples.append(sample)
        if account_id:
            aircraft_samples_by_account[account_id].append(sample)

    selected_samples: list[PositionSample] = []
    selected_account_id: str | None = None
    for account_id in sorted(preferred_account_ids):
        samples = aircraft_samples_by_account.get(account_id, [])
        if len(samples) >= 2:
            selected_account_id = account_id
            selected_samples = samples
            break

    if not selected_samples and len(fallback_aircraft_samples) >= 2:
        selected_account_id = fallback_aircraft_samples[0].account_id or None
        selected_samples = fallback_aircraft_samples

    if len(selected_samples) < 2:
        return None

    selected_samples = sorted(selected_samples, key=lambda sample: sample.event_index)
    start = selected_samples[0]
    end = selected_samples[-1]
    return PlaneRoute(
        match_id=match_id,
        source="log_player_position_aircraft_heuristic",
        sample_count=len(selected_samples),
        start_event_index=start.event_index,
        end_event_index=end.event_index,
        start_event_at_kst=start.event_at_kst,
        end_event_at_kst=end.event_at_kst,
        start_x=start.x,
        start_y=start.y,
        start_z=start.z,
        end_x=end.x,
        end_y=end.y,
        end_z=end.z,
        sample_account_id=selected_account_id,
    )


def _combat_location_record(
    *,
    event: Mapping[str, Any],
    match_id: str,
    event_index: int,
    event_type: str,
    action: str,
    account: Mapping[str, Any],
    related: Mapping[str, Any],
    damage_info: Mapping[str, Any],
) -> CombatLocationEvent:
    location = _mapping_value(account.get("location"))
    related_location = _mapping_value(related.get("location"))
    damage_reason = _optional_text(damage_info.get("damageReason"))
    return CombatLocationEvent(
        match_id=match_id,
        account_id=_optional_text(account.get("accountId")) or "",
        related_account_id=_optional_text(related.get("accountId")),
        event_index=event_index,
        event_type=event_type,
        action=action,
        event_at_kst=_parse_event_time(event.get("_D")),
        common_is_game=_common_is_game(event),
        damage_type_category=_optional_text(damage_info.get("damageTypeCategory")),
        damage_causer_name=_optional_text(damage_info.get("damageCauserName")),
        damage_reason=damage_reason,
        is_headshot=damage_reason == "HeadShot",
        distance_m=_damage_distance_m(damage_info.get("distance")),
        x=_optional_float(location.get("x")),
        y=_optional_float(location.get("y")),
        z=_optional_float(location.get("z")),
        related_x=_optional_float(related_location.get("x")),
        related_y=_optional_float(related_location.get("y")),
        related_z=_optional_float(related_location.get("z")),
        raw_event=event,
    )


def _sampled_distance_m(samples: list[PositionSample], *, in_game_only: bool) -> float:
    total = 0.0
    previous: PositionSample | None = None
    for sample in samples:
        if in_game_only and (sample.common_is_game is None or sample.common_is_game <= 0):
            previous = None
            continue

        if previous is not None:
            distance = _xy_distance_m(previous, sample)
            if distance is not None:
                total += distance
        previous = sample
    return total


def _xy_distance_m(left: PositionSample, right: PositionSample) -> float | None:
    if left.x is None or left.y is None or right.x is None or right.y is None:
        return None
    return sqrt((right.x - left.x) ** 2 + (right.y - left.y) ** 2) / 100.0


def _damage_distance_m(value: Any) -> float | None:
    distance = _optional_float(value)
    if distance is None:
        return None
    return distance / 100.0


def _character_xy_distance_cm(left: Mapping[str, Any], right: Mapping[str, Any]) -> float | None:
    left_location = _mapping_value(left.get("location"))
    right_location = _mapping_value(right.get("location"))
    left_x = _optional_float(left_location.get("x"))
    left_y = _optional_float(left_location.get("y"))
    right_x = _optional_float(right_location.get("x"))
    right_y = _optional_float(right_location.get("y"))
    if left_x is None or left_y is None or right_x is None or right_y is None:
        return None
    return sqrt((right_x - left_x) ** 2 + (right_y - left_y) ** 2)


def _looks_like_aircraft_sample(event: Mapping[str, Any], character: Mapping[str, Any]) -> bool:
    if character.get("isInVehicle") is not True:
        return False

    common_is_game = _common_is_game(event)
    if common_is_game is None or common_is_game > 0.2:
        return False

    location = _mapping_value(character.get("location"))
    z = _optional_float(location.get("z"))
    return z is not None and z >= 100000.0


def _common_is_game(event: Mapping[str, Any]) -> float | None:
    common = event.get("common")
    if not isinstance(common, Mapping):
        return None
    return _optional_float(common.get("isGame"))


def _parse_event_time(value: Any) -> datetime | None:
    text = _optional_text(value)
    if text is None:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return to_kst(parsed)


def _mysql_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return to_kst(value).replace(tzinfo=None)


def _mysql_kst_now() -> datetime:
    return now_kst().replace(tzinfo=None)


def _mapping_value(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _required_text(value: Any, label: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise TelemetryMovementProcessingError(f"{label} is required.")
    return text


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


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _chunked(values: list[tuple[Any, ...]], size: int) -> Iterable[list[tuple[Any, ...]]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]
