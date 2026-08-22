from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
import gzip
import json

from pubg_ai.code_translator import CodeTranslator
from pubg_ai.database import mysql_transaction
from pubg_ai.raw_storage import RawPayloadStore
from pubg_ai.telemetry_processing_state import (
    count_outputs_by_account,
    list_pending_telemetry_payloads,
    pending_tracked_account_ids,
    upsert_processing_states,
)
from pubg_ai.time_utils import now_kst, to_kst


PROCESSOR_NAME = "activity"
PARSER_VERSION = "activity-v2"


class TelemetryActivityProcessingError(RuntimeError):
    """Raised when raw telemetry cannot be normalized into player activities."""


@dataclass(frozen=True)
class ActivityEventRecord:
    match_id: str
    account_id: str
    related_account_id: str | None
    event_index: int
    event_type: str
    action: str
    role: str
    event_at_kst: datetime | None
    common_is_game: float | None
    item_code: str | None
    item_name_ko: str | None
    amount: float | None
    damage: float | None
    distance_m: float | None
    swim_distance_m: float | None
    max_swim_depth: float | None
    vehicle_type: str | None
    vehicle_id: str | None
    vehicle_unique_id: int | None
    seat_index: int | None
    max_speed: float | None
    object_type: str | None
    object_status: str | None
    is_ledge_grab: bool | None
    is_vault_on_vehicle: bool | None
    use_trauma_bag: bool | None
    location_x: float | None
    location_y: float | None
    location_z: float | None
    metadata: Mapping[str, Any]
    raw_event: Mapping[str, Any]

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["event_at_kst"] = self.event_at_kst.isoformat() if self.event_at_kst else None
        return record


@dataclass
class ActivityMatchSummary:
    match_id: str
    account_id: str
    heal_events: int = 0
    heal_amount: float = 0.0
    item_heal_events: int = 0
    item_heal_amount: float = 0.0
    passive_heal_events: int = 0
    passive_heal_amount: float = 0.0
    throwable_uses: int = 0
    flare_uses: int = 0
    revives_caused: int = 0
    revives_received: int = 0
    trauma_bag_revives: int = 0
    carry_events: int = 0
    vehicle_rides: int = 0
    vehicle_leaves: int = 0
    vehicle_distance_m: float = 0.0
    vehicle_max_speed: float = 0.0
    vehicle_damage: float = 0.0
    vehicle_destroys: int = 0
    wheel_destroys: int = 0
    vaults: int = 0
    ledge_grabs: int = 0
    vehicle_vaults: int = 0
    swim_sessions: int = 0
    swim_distance_m: float = 0.0
    armor_destroys_caused: int = 0
    armor_destroys_taken: int = 0
    object_interactions: int = 0
    object_destroys: int = 0
    emergency_pickup_calls: int = 0
    emergency_pickup_rides: int = 0
    redeploys: int = 0
    normalized_event_count: int = 0

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TelemetryEventCount:
    match_id: str
    event_type: str
    event_count: int
    tracked_event_count: int
    normalized_event_count: int


@dataclass(frozen=True)
class TelemetryActivityProcessingResult:
    candidate_payloads: int
    parsed_payloads: int
    skipped_no_tracked_player: int
    failed_payloads: int
    events_read: int
    activity_events: int
    activity_summaries: int
    event_types: int

    def to_record(self) -> dict[str, int]:
        return asdict(self)


class TelemetryActivityProcessor:
    def __init__(
        self,
        connection: Any,
        raw_store: RawPayloadStore,
        translator: CodeTranslator | None = None,
    ) -> None:
        self.connection = connection
        self.raw_store = raw_store
        self.translator = translator or CodeTranslator()

    def process_raw_telemetry(
        self,
        *,
        limit: int = 10,
        force: bool = False,
    ) -> TelemetryActivityProcessingResult:
        bounded_limit = max(1, min(int(limit), 200))
        payloads = list_pending_telemetry_payloads(
            self.connection,
            processor_name=PROCESSOR_NAME,
            parser_version=PARSER_VERSION,
            limit=bounded_limit,
            force=force,
        )
        parsed_payloads = 0
        skipped_no_tracked_player = 0
        failed_payloads = 0
        events_read = 0
        activity_events = 0
        activity_summaries = 0
        event_types = 0

        for payload in payloads:
            match_id = str(payload["match_id"])
            shard = str(payload["shard"])
            pending_accounts = pending_tracked_account_ids(
                self.connection,
                match_id=match_id,
                shard=shard,
                processor_name=PROCESSOR_NAME,
                parser_version=PARSER_VERSION,
                force=force,
            )
            if not pending_accounts:
                skipped_no_tracked_player += 1
                continue
            all_tracked_accounts = pending_tracked_account_ids(
                self.connection,
                match_id=match_id,
                shard=shard,
                processor_name=PROCESSOR_NAME,
                parser_version=PARSER_VERSION,
                force=True,
            )
            try:
                events = self._load_telemetry_events(payload)
                all_records = parse_activity_events(
                    events,
                    match_id=match_id,
                    tracked_account_ids=all_tracked_accounts,
                    translator=self.translator,
                )
                records = [row for row in all_records if row.account_id in pending_accounts]
                summaries = summarize_activity_match(
                    records,
                    match_id=match_id,
                    account_ids=pending_accounts,
                )
                counts = summarize_telemetry_event_counts(events, all_records, match_id=match_id)
                self._replace_rows(
                    match_id=match_id,
                    account_ids=pending_accounts,
                    records=records,
                    summaries=summaries,
                    counts=counts,
                )
            except Exception:
                failed_payloads += 1
                continue
            parsed_payloads += 1
            events_read += len(events)
            activity_events += len(records)
            activity_summaries += len(summaries)
            event_types += len(counts)

        return TelemetryActivityProcessingResult(
            candidate_payloads=len(payloads),
            parsed_payloads=parsed_payloads,
            skipped_no_tracked_player=skipped_no_tracked_player,
            failed_payloads=failed_payloads,
            events_read=events_read,
            activity_events=activity_events,
            activity_summaries=activity_summaries,
            event_types=event_types,
        )

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
            raise TelemetryActivityProcessingError(
                f"failed to read telemetry payload: {relative_path}"
            ) from exc
        if not isinstance(loaded, list):
            raise TelemetryActivityProcessingError("telemetry payload root must be a list.")
        return [event for event in loaded if isinstance(event, Mapping)]

    def _replace_rows(
        self,
        *,
        match_id: str,
        account_ids: set[str],
        records: list[ActivityEventRecord],
        summaries: list[ActivityMatchSummary],
        counts: list[TelemetryEventCount],
    ) -> None:
        output_counts = count_outputs_by_account(records)
        with mysql_transaction(self.connection):
            self._delete_player_rows(match_id, account_ids)
            self._insert_events(records)
            self._insert_summaries(summaries)
            self._replace_event_counts(match_id, counts)
            upsert_processing_states(
                self.connection,
                match_id=match_id,
                account_ids=account_ids,
                processor_name=PROCESSOR_NAME,
                parser_version=PARSER_VERSION,
                output_counts=output_counts,
            )

    def _delete_player_rows(self, match_id: str, account_ids: set[str]) -> None:
        placeholders = ", ".join(["%s"] * len(account_ids))
        params = [match_id, *sorted(account_ids)]
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM player_activity_events "
                f"WHERE match_id = %s AND account_id IN ({placeholders})",
                params,
            )
            cursor.execute(
                f"DELETE FROM player_match_activity_summaries "
                f"WHERE match_id = %s AND account_id IN ({placeholders})",
                params,
            )

    def _insert_events(self, records: list[ActivityEventRecord]) -> None:
        if not records:
            return
        timestamp = _mysql_kst_now()
        rows = [
            (
                row.match_id,
                row.account_id,
                row.related_account_id,
                row.event_index,
                row.event_type,
                row.action,
                row.role,
                _mysql_datetime(row.event_at_kst),
                row.common_is_game,
                row.item_code,
                row.item_name_ko,
                row.amount,
                row.damage,
                row.distance_m,
                row.swim_distance_m,
                row.max_swim_depth,
                row.vehicle_type,
                row.vehicle_id,
                row.vehicle_unique_id,
                row.seat_index,
                row.max_speed,
                row.object_type,
                row.object_status,
                row.is_ledge_grab,
                row.is_vault_on_vehicle,
                row.use_trauma_bag,
                row.location_x,
                row.location_y,
                row.location_z,
                _json(row.metadata),
                _json(row.raw_event),
                timestamp,
            )
            for row in records
        ]
        with self.connection.cursor() as cursor:
            for chunk in _chunked(rows, 500):
                cursor.executemany(
                    """
                    INSERT INTO player_activity_events (
                        match_id, account_id, related_account_id, event_index,
                        event_type, action, role, event_at_kst, common_is_game,
                        item_code, item_name_ko, amount, damage, distance_m,
                        swim_distance_m, max_swim_depth, vehicle_type, vehicle_id,
                        vehicle_unique_id, seat_index, max_speed, object_type,
                        object_status, is_ledge_grab, is_vault_on_vehicle,
                        use_trauma_bag, location_x, location_y, location_z,
                        metadata, raw_event, updated_at_kst
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    chunk,
                )

    def _insert_summaries(self, summaries: list[ActivityMatchSummary]) -> None:
        if not summaries:
            return
        timestamp = _mysql_kst_now()
        rows = [(*asdict(summary).values(), timestamp) for summary in summaries]
        with self.connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO player_match_activity_summaries (
                    match_id, account_id, heal_events, heal_amount,
                    item_heal_events, item_heal_amount,
                    passive_heal_events, passive_heal_amount,
                    throwable_uses, flare_uses, revives_caused, revives_received,
                    trauma_bag_revives, carry_events, vehicle_rides, vehicle_leaves,
                    vehicle_distance_m, vehicle_max_speed, vehicle_damage,
                    vehicle_destroys, wheel_destroys, vaults, ledge_grabs,
                    vehicle_vaults, swim_sessions, swim_distance_m,
                    armor_destroys_caused, armor_destroys_taken,
                    object_interactions, object_destroys, emergency_pickup_calls,
                    emergency_pickup_rides, redeploys, normalized_event_count,
                    updated_at_kst
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s
                )
                """,
                rows,
            )

    def _replace_event_counts(
        self,
        match_id: str,
        counts: list[TelemetryEventCount],
    ) -> None:
        timestamp = _mysql_kst_now()
        with self.connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM match_telemetry_event_counts WHERE match_id = %s",
                (match_id,),
            )
            if counts:
                cursor.executemany(
                    """
                    INSERT INTO match_telemetry_event_counts (
                        match_id, event_type, event_count, tracked_event_count,
                        normalized_event_count, parser_version, updated_at_kst
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        (
                            row.match_id,
                            row.event_type,
                            row.event_count,
                            row.tracked_event_count,
                            row.normalized_event_count,
                            PARSER_VERSION,
                            timestamp,
                        )
                        for row in counts
                    ],
                )


def parse_activity_events(
    events: Iterable[Mapping[str, Any]],
    *,
    match_id: str,
    tracked_account_ids: set[str],
    translator: CodeTranslator | None = None,
) -> list[ActivityEventRecord]:
    code_translator = translator or CodeTranslator()
    event_rows = list(events)
    records: list[ActivityEventRecord] = []
    seen: set[tuple[str, int, str]] = set()

    def add(
        *,
        event: Mapping[str, Any],
        event_index: int,
        actor: Mapping[str, Any],
        action: str,
        role: str,
        related: Mapping[str, Any] | None = None,
        item: Mapping[str, Any] | None = None,
        amount: Any = None,
        damage: Any = None,
        distance_m: Any = None,
        swim_distance_m: Any = None,
        max_swim_depth: Any = None,
        vehicle: Mapping[str, Any] | None = None,
        seat_index: Any = None,
        max_speed: Any = None,
        object_type: Any = None,
        object_status: Any = None,
        is_ledge_grab: Any = None,
        is_vault_on_vehicle: Any = None,
        use_trauma_bag: Any = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        account_id = _account_id(actor)
        if account_id not in tracked_account_ids:
            return
        key = (account_id, event_index, action)
        if key in seen:
            return
        seen.add(key)
        item_object = item or {}
        vehicle_object = vehicle or {}
        item_code = _optional_text(item_object.get("itemId"))
        location = _mapping(actor.get("location"))
        records.append(
            ActivityEventRecord(
                match_id=match_id,
                account_id=account_id,
                related_account_id=_account_id(related or {}) or None,
                event_index=event_index,
                event_type=_optional_text(event.get("_T")) or "",
                action=action,
                role=role,
                event_at_kst=_parse_event_time(event.get("_D")),
                common_is_game=_common_is_game(event),
                item_code=item_code,
                item_name_ko=(
                    code_translator.translate_auto(item_code).label if item_code else None
                ),
                amount=_optional_float(amount),
                damage=_optional_float(damage),
                distance_m=_optional_float(distance_m),
                swim_distance_m=_optional_float(swim_distance_m),
                max_swim_depth=_optional_float(max_swim_depth),
                vehicle_type=_optional_text(vehicle_object.get("vehicleType")),
                vehicle_id=_optional_text(vehicle_object.get("vehicleId")),
                vehicle_unique_id=_optional_int(vehicle_object.get("vehicleUniqueId")),
                seat_index=_optional_int(seat_index),
                max_speed=_optional_float(max_speed),
                object_type=_optional_text(object_type),
                object_status=_optional_text(object_status),
                is_ledge_grab=_optional_bool(is_ledge_grab),
                is_vault_on_vehicle=_optional_bool(is_vault_on_vehicle),
                use_trauma_bag=_optional_bool(use_trauma_bag),
                location_x=_optional_float(location.get("x")),
                location_y=_optional_float(location.get("y")),
                location_z=_optional_float(location.get("z")),
                metadata=dict(metadata or {}),
                raw_event=event,
            )
        )

    for event_index, event in enumerate(event_rows):
        event_type = _optional_text(event.get("_T")) or ""
        character = _mapping(event.get("character"))
        attacker = _mapping(event.get("attacker"))
        victim = _mapping(event.get("victim"))
        vehicle = _mapping(event.get("vehicle"))

        if event_type == "LogHeal":
            heal_item = _mapping(event.get("item"))
            add(
                event=event,
                event_index=event_index,
                actor=character,
                action=("heal_item" if _optional_text(heal_item.get("itemId")) else "heal_passive"),
                role="self",
                item=heal_item,
                amount=event.get("healAmount"),
            )
        elif event_type == "LogArmorDestroy":
            item = _mapping(event.get("item"))
            add(
                event=event,
                event_index=event_index,
                actor=attacker,
                related=victim,
                action="armor_destroy_caused",
                role="attacker",
                item=item,
                damage=event.get("damage"),
                distance_m=event.get("distance"),
            )
            add(
                event=event,
                event_index=event_index,
                actor=victim,
                related=attacker,
                action="armor_destroy_taken",
                role="victim",
                item=item,
                damage=event.get("damage"),
                distance_m=event.get("distance"),
            )
        elif event_type == "LogPlayerRevive":
            reviver = _mapping(event.get("reviver"))
            add(
                event=event,
                event_index=event_index,
                actor=reviver,
                related=victim,
                action="revive_caused",
                role="reviver",
                use_trauma_bag=event.get("useTraumaBag"),
            )
            add(
                event=event,
                event_index=event_index,
                actor=victim,
                related=reviver,
                action="revive_received",
                role="victim",
                use_trauma_bag=event.get("useTraumaBag"),
            )
        elif event_type == "LogCharacterCarry":
            for role_name, actor in _named_characters(
                event,
                ("character", "carrier", "victim", "target"),
            ):
                add(
                    event=event,
                    event_index=event_index,
                    actor=actor,
                    action="carry_event",
                    role=_carry_role(event.get("carryState"), role_name),
                    metadata={"carry_state": event.get("carryState") or event.get("state")},
                )
        elif event_type == "LogPlayerUseThrowable":
            add(
                event=event,
                event_index=event_index,
                actor=attacker,
                action="throwable_use",
                role="attacker",
                item=_mapping(event.get("weapon")),
            )
        elif event_type == "LogPlayerUseFlareGun":
            add(
                event=event,
                event_index=event_index,
                actor=attacker,
                action="flare_use",
                role="attacker",
                item=_mapping(event.get("weapon")),
                metadata={"fire_weapon_stack_count": event.get("fireWeaponStackCount")},
            )
        elif event_type == "LogVehicleRide":
            add(
                event=event,
                event_index=event_index,
                actor=character,
                action="vehicle_ride",
                role="rider",
                vehicle=vehicle,
                seat_index=event.get("seatIndex"),
            )
        elif event_type == "LogVehicleLeave":
            add(
                event=event,
                event_index=event_index,
                actor=character,
                action="vehicle_leave",
                role="rider",
                vehicle=vehicle,
                distance_m=event.get("rideDistance"),
                max_speed=event.get("maxSpeed"),
            )
        elif event_type == "LogVehicleDamage":
            add(
                event=event,
                event_index=event_index,
                actor=attacker,
                action="vehicle_damage_caused",
                role="attacker",
                vehicle=vehicle,
                damage=(
                    event.get("damageAmount")
                    if event.get("damageAmount") is not None
                    else event.get("damage")
                ),
                distance_m=event.get("distance"),
                metadata={"damage_percent": event.get("damagePercent")},
            )
        elif event_type == "LogVehicleDestroy":
            add(
                event=event,
                event_index=event_index,
                actor=attacker,
                action="vehicle_destroy_caused",
                role="attacker",
                vehicle=vehicle,
                distance_m=event.get("distance"),
            )
        elif event_type == "LogWheelDestroy":
            add(
                event=event,
                event_index=event_index,
                actor=attacker,
                action="wheel_destroy_caused",
                role="attacker",
                vehicle=vehicle,
            )
        elif event_type == "LogVaultStart":
            add(
                event=event,
                event_index=event_index,
                actor=character,
                action="vault",
                role="self",
                is_ledge_grab=event.get("isLedgeGrab"),
                is_vault_on_vehicle=event.get("isVaultOnVehicle"),
                metadata={"height": event.get("height")},
            )
        elif event_type == "LogSwimStart":
            add(
                event=event,
                event_index=event_index,
                actor=character,
                action="swim_start",
                role="self",
            )
        elif event_type == "LogSwimEnd":
            add(
                event=event,
                event_index=event_index,
                actor=character,
                action="swim_end",
                role="self",
                swim_distance_m=event.get("swimDistance"),
                max_swim_depth=event.get("maxSwimDepth"),
            )
        elif event_type == "LogObjectInteraction":
            add(
                event=event,
                event_index=event_index,
                actor=character,
                action="object_interaction",
                role="self",
                object_type=event.get("objectType"),
                object_status=event.get("objectStatus"),
            )

    for event_index, event in enumerate(event_rows):
        event_type = _optional_text(event.get("_T")) or ""
        character = _mapping(event.get("character"))
        attacker = _mapping(event.get("attacker"))
        if event_type in {
            "LogObjectDestroy",
            "LogPlayerDestroyProp",
            "LogPlayerDestroyBreachableWall",
        }:
            actor = character or attacker
            actions = {
                "LogObjectDestroy": "object_destroy",
                "LogPlayerDestroyProp": "prop_destroy",
                "LogPlayerDestroyBreachableWall": "breachable_wall_destroy",
            }
            add(
                event=event,
                event_index=event_index,
                actor=actor,
                action=actions[event_type],
                role="self",
                object_type=event.get("objectType") or event.get("objectName"),
                object_status=event.get("objectStatus"),
            )
        elif event_type == "LogEmergencyPickup":
            instigator = _mapping(event.get("instigator")) or character
            add(
                event=event,
                event_index=event_index,
                actor=instigator,
                action="emergency_pickup_called",
                role="instigator",
            )
            riders = event.get("riders")
            if isinstance(riders, Sequence) and not isinstance(riders, (str, bytes)):
                for rider in riders:
                    if isinstance(rider, Mapping):
                        add(
                            event=event,
                            event_index=event_index,
                            actor=rider,
                            related=instigator,
                            action="emergency_pickup_ride",
                            role="rider",
                        )
        elif event_type in {"LogPlayerRedeploy", "LogPlayerRedeployBRStart"}:
            actor = character or _mapping(event.get("player"))
            add(
                event=event,
                event_index=event_index,
                actor=actor,
                action=("redeploy" if event_type == "LogPlayerRedeploy" else "redeploy_start"),
                role="self",
            )
        elif event_type == "LogSpecialZoneInCharacters":
            characters = event.get("characters")
            if isinstance(characters, Sequence) and not isinstance(characters, (str, bytes)):
                for zone_character in characters:
                    if isinstance(zone_character, Mapping):
                        add(
                            event=event,
                            event_index=event_index,
                            actor=zone_character,
                            action="special_zone_presence",
                            role="member",
                            metadata={"zone_type": event.get("zoneType")},
                        )

    return sorted(records, key=lambda row: (row.event_index, row.account_id, row.action))


def summarize_activity_match(
    records: Iterable[ActivityEventRecord],
    *,
    match_id: str,
    account_ids: set[str],
) -> list[ActivityMatchSummary]:
    summaries = {
        account_id: ActivityMatchSummary(match_id=match_id, account_id=account_id)
        for account_id in sorted(account_ids)
    }
    for row in records:
        summary = summaries[row.account_id]
        summary.normalized_event_count += 1
        if row.action in {"heal_item", "heal_passive"}:
            summary.heal_events += 1
            summary.heal_amount += row.amount or 0.0
            if row.action == "heal_item":
                summary.item_heal_events += 1
                summary.item_heal_amount += row.amount or 0.0
            else:
                summary.passive_heal_events += 1
                summary.passive_heal_amount += row.amount or 0.0
        elif row.action == "throwable_use":
            summary.throwable_uses += 1
        elif row.action == "flare_use":
            summary.flare_uses += 1
        elif row.action == "revive_caused":
            summary.revives_caused += 1
            summary.trauma_bag_revives += int(bool(row.use_trauma_bag))
        elif row.action == "revive_received":
            summary.revives_received += 1
        elif row.action == "carry_event":
            summary.carry_events += 1
        elif row.action == "vehicle_ride":
            summary.vehicle_rides += 1
        elif row.action == "vehicle_leave":
            summary.vehicle_leaves += 1
            summary.vehicle_distance_m += max(0.0, row.distance_m or 0.0)
            summary.vehicle_max_speed = max(summary.vehicle_max_speed, row.max_speed or 0.0)
        elif row.action == "vehicle_damage_caused":
            summary.vehicle_damage += max(0.0, row.damage or 0.0)
        elif row.action == "vehicle_destroy_caused":
            summary.vehicle_destroys += 1
        elif row.action == "wheel_destroy_caused":
            summary.wheel_destroys += 1
        elif row.action == "vault":
            summary.vaults += 1
            summary.ledge_grabs += int(bool(row.is_ledge_grab))
            summary.vehicle_vaults += int(bool(row.is_vault_on_vehicle))
        elif row.action == "swim_end":
            summary.swim_sessions += 1
            summary.swim_distance_m += max(0.0, row.swim_distance_m or 0.0)
        elif row.action == "armor_destroy_caused":
            summary.armor_destroys_caused += 1
        elif row.action == "armor_destroy_taken":
            summary.armor_destroys_taken += 1
        elif row.action == "object_interaction":
            summary.object_interactions += 1
        elif row.action in {"object_destroy", "prop_destroy", "breachable_wall_destroy"}:
            summary.object_destroys += 1
        elif row.action == "emergency_pickup_called":
            summary.emergency_pickup_calls += 1
        elif row.action == "emergency_pickup_ride":
            summary.emergency_pickup_rides += 1
        elif row.action == "redeploy":
            summary.redeploys += 1
    return list(summaries.values())


def summarize_telemetry_event_counts(
    events: Iterable[Mapping[str, Any]],
    records: Iterable[ActivityEventRecord],
    *,
    match_id: str,
) -> list[TelemetryEventCount]:
    event_rows = list(events)
    record_rows = list(records)
    totals = Counter(_optional_text(event.get("_T")) or "(missing)" for event in event_rows)
    tracked_indices: dict[str, set[int]] = {}
    normalized = Counter()
    for row in record_rows:
        tracked_indices.setdefault(row.event_type, set()).add(row.event_index)
        normalized[row.event_type] += 1
    return [
        TelemetryEventCount(
            match_id=match_id,
            event_type=event_type,
            event_count=count,
            tracked_event_count=len(tracked_indices.get(event_type, set())),
            normalized_event_count=normalized[event_type],
        )
        for event_type, count in sorted(totals.items())
    ]


def _named_characters(
    event: Mapping[str, Any],
    names: Sequence[str],
) -> list[tuple[str, Mapping[str, Any]]]:
    rows: list[tuple[str, Mapping[str, Any]]] = []
    seen: set[str] = set()
    for name in names:
        actor = _mapping(event.get(name))
        account_id = _account_id(actor)
        if account_id and account_id not in seen:
            rows.append((name, actor))
            seen.add(account_id)
    return rows


def _carry_role(carry_state: Any, fallback: str) -> str:
    text = _optional_text(carry_state)
    if text:
        suffix = text.rsplit("_", 1)[-1].lower()
        if suffix in {"carrier", "victim"}:
            return suffix
    return fallback


def _account_id(character: Mapping[str, Any]) -> str:
    return _optional_text(character.get("accountId")) or ""


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _common_is_game(event: Mapping[str, Any]) -> float | None:
    return _optional_float(_mapping(event.get("common")).get("isGame"))


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


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _required_text(value: Any, label: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise TelemetryActivityProcessingError(f"{label} is required.")
    return text


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
        return None
    return bool(value)


def _json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _mysql_datetime(value: datetime | None) -> datetime | None:
    return to_kst(value).replace(tzinfo=None) if value else None


def _mysql_kst_now() -> datetime:
    return now_kst().replace(tzinfo=None)


def _chunked(values: list[tuple[Any, ...]], size: int) -> Iterable[list[tuple[Any, ...]]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]
