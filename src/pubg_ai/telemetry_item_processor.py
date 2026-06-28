from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping
import gzip
import json

from pubg_ai.code_translator import CodeTranslator
from pubg_ai.raw_storage import RawPayloadStore
from pubg_ai.time_utils import now_kst, to_kst


ITEM_EVENT_ACTIONS = {
    "LogItemPickup": "pickup",
    "LogItemPickupFromLootBox": "pickup_lootbox",
    "LogItemPickupFromCarepackage": "pickup_carepackage",
    "LogItemDrop": "drop",
    "LogItemUse": "use",
    "LogItemEquip": "equip",
    "LogItemUnequip": "unequip",
    "LogItemAttach": "attach",
    "LogItemDetach": "detach",
}


class TelemetryItemProcessingError(RuntimeError):
    """Raised when raw telemetry cannot be parsed into item event rows."""


@dataclass(frozen=True)
class ItemEventRecord:
    match_id: str
    account_id: str
    event_index: int
    event_type: str
    action: str
    event_at_kst: datetime | None
    common_is_game: float | None
    item_code: str | None
    item_name_ko: str | None
    item_category: str | None
    item_sub_category: str | None
    stack_count: int | None
    parent_item_code: str | None
    parent_item_name_ko: str | None
    child_item_code: str | None
    child_item_name_ko: str | None
    location_x: float | None
    location_y: float | None
    location_z: float | None
    raw_event: Mapping[str, Any]

    @property
    def quantity(self) -> int:
        return self.stack_count if self.stack_count and self.stack_count > 0 else 1

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["event_at_kst"] = self.event_at_kst.isoformat() if self.event_at_kst else None
        return record


@dataclass
class ItemMatchStats:
    match_id: str
    account_id: str
    item_code: str
    item_name_ko: str | None = None
    item_category: str | None = None
    item_sub_category: str | None = None
    picked_up_events: int = 0
    picked_up_quantity: int = 0
    loot_box_pickup_events: int = 0
    carepackage_pickup_events: int = 0
    dropped_events: int = 0
    dropped_quantity: int = 0
    used_events: int = 0
    used_quantity: int = 0
    equipped_events: int = 0
    unequipped_events: int = 0
    attached_events: int = 0
    detached_events: int = 0

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TelemetryItemProcessingResult:
    candidate_payloads: int
    parsed_payloads: int
    skipped_existing: int
    skipped_no_tracked_player: int
    failed_payloads: int
    events_read: int
    item_events: int
    item_stats: int

    def to_record(self) -> dict[str, int]:
        return asdict(self)


class TelemetryItemProcessor:
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
    ) -> TelemetryItemProcessingResult:
        limit = max(1, min(limit, 200))
        payloads = self._list_raw_telemetry_payloads(limit=limit, force=force)

        parsed_payloads = 0
        skipped_existing = 0
        skipped_no_tracked_player = 0
        failed_payloads = 0
        events_read = 0
        item_event_count = 0
        item_stat_count = 0

        for payload in payloads:
            match_id = str(payload["match_id"])
            shard = str(payload["shard"])

            if not force and self._item_events_exist(match_id):
                skipped_existing += 1
                continue

            tracked_account_ids = self._tracked_account_ids_for_match(match_id=match_id, shard=shard)
            if not tracked_account_ids:
                skipped_no_tracked_player += 1
                continue

            try:
                events = self._load_telemetry_events(payload)
                item_events = parse_item_events(
                    events,
                    match_id=match_id,
                    tracked_account_ids=tracked_account_ids,
                    translator=self.translator,
                )
                item_stats = summarize_item_match_stats(item_events)
                self._replace_item_rows(
                    match_id=match_id,
                    tracked_account_ids=tracked_account_ids,
                    item_events=item_events,
                    item_stats=item_stats,
                )
            except Exception:
                failed_payloads += 1
                continue

            parsed_payloads += 1
            events_read += len(events)
            item_event_count += len(item_events)
            item_stat_count += len(item_stats)

        return TelemetryItemProcessingResult(
            candidate_payloads=len(payloads),
            parsed_payloads=parsed_payloads,
            skipped_existing=skipped_existing,
            skipped_no_tracked_player=skipped_no_tracked_player,
            failed_payloads=failed_payloads,
            events_read=events_read,
            item_events=item_event_count,
            item_stats=item_stat_count,
        )

    def _list_raw_telemetry_payloads(self, *, limit: int, force: bool) -> list[dict[str, Any]]:
        where = ""
        if not force:
            where = """
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM player_item_events item_events
                    WHERE item_events.match_id = raw_telemetry_payloads.match_id
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

    def _item_events_exist(self, match_id: str) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM player_item_events WHERE match_id = %s LIMIT 1",
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
            raise TelemetryItemProcessingError(f"failed to read telemetry payload: {relative_path}") from exc

        if not isinstance(loaded, list):
            raise TelemetryItemProcessingError("telemetry payload root must be a list.")

        return [event for event in loaded if isinstance(event, Mapping)]

    def _replace_item_rows(
        self,
        *,
        match_id: str,
        tracked_account_ids: set[str],
        item_events: list[ItemEventRecord],
        item_stats: list[ItemMatchStats],
    ) -> None:
        self._delete_existing_rows(match_id=match_id, account_ids=tracked_account_ids)
        self._insert_item_events(item_events)
        self._insert_item_stats(item_stats)

    def _delete_existing_rows(self, *, match_id: str, account_ids: set[str]) -> None:
        if not account_ids:
            return

        placeholders = ", ".join(["%s"] * len(account_ids))
        params = [match_id, *sorted(account_ids)]
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""
                DELETE FROM player_item_events
                WHERE match_id = %s AND account_id IN ({placeholders})
                """,
                params,
            )
            cursor.execute(
                f"""
                DELETE FROM player_item_match_stats
                WHERE match_id = %s AND account_id IN ({placeholders})
                """,
                params,
            )

    def _insert_item_events(self, item_events: list[ItemEventRecord]) -> None:
        if not item_events:
            return

        timestamp = _mysql_kst_now()
        rows = [
            (
                event.match_id,
                event.account_id,
                event.event_index,
                event.event_type,
                event.action,
                _mysql_datetime(event.event_at_kst),
                event.common_is_game,
                event.item_code,
                event.item_name_ko,
                event.item_category,
                event.item_sub_category,
                event.stack_count,
                event.parent_item_code,
                event.parent_item_name_ko,
                event.child_item_code,
                event.child_item_name_ko,
                event.location_x,
                event.location_y,
                event.location_z,
                json.dumps(event.raw_event, ensure_ascii=False, separators=(",", ":")),
                timestamp,
            )
            for event in item_events
        ]

        with self.connection.cursor() as cursor:
            for chunk in _chunked(rows, 500):
                cursor.executemany(
                    """
                    INSERT INTO player_item_events (
                        match_id,
                        account_id,
                        event_index,
                        event_type,
                        action,
                        event_at_kst,
                        common_is_game,
                        item_code,
                        item_name_ko,
                        item_category,
                        item_sub_category,
                        stack_count,
                        parent_item_code,
                        parent_item_name_ko,
                        child_item_code,
                        child_item_name_ko,
                        location_x,
                        location_y,
                        location_z,
                        raw_event,
                        updated_at_kst
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    chunk,
                )

    def _insert_item_stats(self, item_stats: list[ItemMatchStats]) -> None:
        if not item_stats:
            return

        timestamp = _mysql_kst_now()
        rows = [
            (
                stats.match_id,
                stats.account_id,
                stats.item_code,
                stats.item_name_ko,
                stats.item_category,
                stats.item_sub_category,
                stats.picked_up_events,
                stats.picked_up_quantity,
                stats.loot_box_pickup_events,
                stats.carepackage_pickup_events,
                stats.dropped_events,
                stats.dropped_quantity,
                stats.used_events,
                stats.used_quantity,
                stats.equipped_events,
                stats.unequipped_events,
                stats.attached_events,
                stats.detached_events,
                timestamp,
            )
            for stats in item_stats
        ]

        with self.connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO player_item_match_stats (
                    match_id,
                    account_id,
                    item_code,
                    item_name_ko,
                    item_category,
                    item_sub_category,
                    picked_up_events,
                    picked_up_quantity,
                    loot_box_pickup_events,
                    carepackage_pickup_events,
                    dropped_events,
                    dropped_quantity,
                    used_events,
                    used_quantity,
                    equipped_events,
                    unequipped_events,
                    attached_events,
                    detached_events,
                    updated_at_kst
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                rows,
            )


def parse_item_events(
    events: Iterable[Mapping[str, Any]],
    *,
    match_id: str,
    tracked_account_ids: set[str],
    translator: CodeTranslator | None = None,
) -> list[ItemEventRecord]:
    code_translator = translator or CodeTranslator()
    item_events: list[ItemEventRecord] = []

    for event_index, event in enumerate(events):
        event_type = _optional_text(event.get("_T"))
        action = ITEM_EVENT_ACTIONS.get(event_type or "")
        if action is None:
            continue

        character = _mapping_value(event.get("character"))
        account_id = _optional_text(character.get("accountId"))
        if account_id not in tracked_account_ids:
            continue

        parent_item = _mapping_value(event.get("parentItem"))
        child_item = _mapping_value(event.get("childItem"))
        selected_item = child_item if action in {"attach", "detach"} else _mapping_value(event.get("item"))
        item_code = _optional_text(selected_item.get("itemId"))
        parent_item_code = _optional_text(parent_item.get("itemId"))
        child_item_code = _optional_text(child_item.get("itemId"))
        location = _mapping_value(character.get("location"))

        item_events.append(
            ItemEventRecord(
                match_id=match_id,
                account_id=account_id,
                event_index=event_index,
                event_type=event_type or "",
                action=action,
                event_at_kst=_parse_event_time(event.get("_D")),
                common_is_game=_common_is_game(event),
                item_code=item_code,
                item_name_ko=_item_label(code_translator, item_code),
                item_category=_optional_text(selected_item.get("category")),
                item_sub_category=_optional_text(selected_item.get("subCategory")),
                stack_count=_optional_int(selected_item.get("stackCount")),
                parent_item_code=parent_item_code,
                parent_item_name_ko=_item_label(code_translator, parent_item_code),
                child_item_code=child_item_code,
                child_item_name_ko=_item_label(code_translator, child_item_code),
                location_x=_optional_float(location.get("x")),
                location_y=_optional_float(location.get("y")),
                location_z=_optional_float(location.get("z")),
                raw_event=event,
            )
        )

    return item_events


def summarize_item_match_stats(item_events: Iterable[ItemEventRecord]) -> list[ItemMatchStats]:
    stats_by_key: dict[tuple[str, str, str], ItemMatchStats] = {}

    def get_stats(event: ItemEventRecord) -> ItemMatchStats | None:
        if not event.item_code:
            return None

        key = (event.match_id, event.account_id, event.item_code)
        if key not in stats_by_key:
            stats_by_key[key] = ItemMatchStats(
                match_id=event.match_id,
                account_id=event.account_id,
                item_code=event.item_code,
                item_name_ko=event.item_name_ko,
                item_category=event.item_category,
                item_sub_category=event.item_sub_category,
            )
        return stats_by_key[key]

    for event in item_events:
        stats = get_stats(event)
        if stats is None:
            continue

        if event.action == "pickup":
            stats.picked_up_events += 1
            stats.picked_up_quantity += event.quantity
        elif event.action == "pickup_lootbox":
            stats.picked_up_events += 1
            stats.picked_up_quantity += event.quantity
            stats.loot_box_pickup_events += 1
        elif event.action == "pickup_carepackage":
            stats.picked_up_events += 1
            stats.picked_up_quantity += event.quantity
            stats.carepackage_pickup_events += 1
        elif event.action == "drop":
            stats.dropped_events += 1
            stats.dropped_quantity += event.quantity
        elif event.action == "use":
            stats.used_events += 1
            stats.used_quantity += event.quantity
        elif event.action == "equip":
            stats.equipped_events += 1
        elif event.action == "unequip":
            stats.unequipped_events += 1
        elif event.action == "attach":
            stats.attached_events += 1
        elif event.action == "detach":
            stats.detached_events += 1

    return sorted(
        stats_by_key.values(),
        key=lambda stats: (stats.account_id, stats.item_code),
    )


def _item_label(translator: CodeTranslator, item_code: str | None) -> str | None:
    if item_code is None:
        return None
    return translator.translate(item_code, "item").label


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
        raise TelemetryItemProcessingError(f"{label} is required.")
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


def _chunked(values: list[tuple[Any, ...]], size: int) -> Iterable[list[tuple[Any, ...]]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]
