from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from math import sqrt
from typing import Any, Iterable, Mapping
import json

from pubg_ai.code_translator import translate_code
from pubg_ai.time_utils import now_kst
from pubg_ai.weapon_stats import normalize_weapon_code


LOADOUT_SNAPSHOT_ACTIONS = {"kill", "dbno_caused", "finish"}


@dataclass(frozen=True)
class ItemLoadoutEvent:
    event_index: int
    action: str
    item_code: str | None
    item_name_ko: str | None
    item_category: str | None
    item_sub_category: str | None
    parent_item_code: str | None


@dataclass(frozen=True)
class CombatLoadoutEvent:
    event_index: int
    action: str
    event_at_kst: datetime | None
    damage_causer_name: str | None
    distance_m: float | None
    is_headshot: bool


@dataclass(frozen=True)
class CombatLoadoutSnapshot:
    match_id: str
    account_id: str
    combat_event_index: int
    combat_action: str
    combat_event_at_kst: datetime | None
    weapon_code: str
    weapon_name_ko: str
    attachment_codes: tuple[str, ...]
    attachment_names_ko: tuple[str, ...]
    distance_m: float | None
    is_headshot: bool

    @property
    def attachment_count(self) -> int:
        return len(self.attachment_codes)

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["attachment_codes"] = list(self.attachment_codes)
        record["attachment_names_ko"] = list(self.attachment_names_ko)
        record["attachment_count"] = self.attachment_count
        return record


@dataclass(frozen=True)
class LoadoutSnapshotProcessingResult:
    candidate_matches: int
    processed_matches: int
    skipped_existing: int
    skipped_no_items: int
    generated_snapshots: int
    failed_matches: int

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


class LoadoutSnapshotProcessor:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def process_matches(self, *, limit: int = 10, force: bool = False) -> LoadoutSnapshotProcessingResult:
        limit = max(1, min(int(limit), 500))
        candidates = self._candidate_matches(limit=limit, include_existing=force)
        processed_matches = 0
        skipped_existing = 0
        skipped_no_items = 0
        generated_snapshots = 0
        failed_matches = 0

        for candidate in candidates:
            match_id = str(candidate["match_id"])
            try:
                if not force and self._snapshots_exist(match_id):
                    skipped_existing += 1
                    continue
                if not self._item_events_exist(match_id):
                    skipped_no_items += 1
                    continue

                if force:
                    self._delete_snapshots(match_id)

                snapshots = self._build_match_snapshots(match_id)
                self._upsert_snapshots(snapshots)
                generated_snapshots += len(snapshots)
                processed_matches += 1
            except Exception:
                failed_matches += 1

        return LoadoutSnapshotProcessingResult(
            candidate_matches=len(candidates),
            processed_matches=processed_matches,
            skipped_existing=skipped_existing,
            skipped_no_items=skipped_no_items,
            generated_snapshots=generated_snapshots,
            failed_matches=failed_matches,
        )

    def _candidate_matches(self, *, limit: int, include_existing: bool) -> list[Mapping[str, Any]]:
        existing_filter = (
            ""
            if include_existing
            else """
                  AND NOT EXISTS (
                        SELECT 1
                        FROM player_combat_loadout_snapshots snapshots
                        WHERE snapshots.match_id = location_events.match_id
                  )
            """
        )
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT location_events.match_id
                FROM player_combat_location_events location_events
                INNER JOIN matches
                    ON matches.match_id = location_events.match_id
                WHERE location_events.action IN ('kill', 'dbno_caused', 'finish')
                  AND location_events.damage_causer_name IS NOT NULL
                  AND (
                        location_events.damage_causer_name LIKE 'Weap%%'
                        OR location_events.damage_causer_name LIKE 'Item_Weapon_%%'
                  )
                  {existing_filter}
                GROUP BY location_events.match_id
                ORDER BY MAX(matches.created_at_kst) DESC, location_events.match_id DESC
                LIMIT %s
                """,
                (limit,),
            )
            return list(cursor.fetchall())

    def _snapshots_exist(self, match_id: str) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM player_combat_loadout_snapshots WHERE match_id = %s LIMIT 1",
                (match_id,),
            )
            return cursor.fetchone() is not None

    def _item_events_exist(self, match_id: str) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM player_item_events WHERE match_id = %s LIMIT 1",
                (match_id,),
            )
            return cursor.fetchone() is not None

    def _delete_snapshots(self, match_id: str) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM player_combat_loadout_snapshots WHERE match_id = %s",
                (match_id,),
            )

    def _build_match_snapshots(self, match_id: str) -> list[CombatLoadoutSnapshot]:
        account_ids = self._load_match_accounts(match_id)
        snapshots: list[CombatLoadoutSnapshot] = []
        for account_id in account_ids:
            item_events = self._load_item_events(match_id=match_id, account_id=account_id)
            combat_events = self._load_combat_events(match_id=match_id, account_id=account_id)
            snapshots.extend(
                build_loadout_snapshots(
                    match_id=match_id,
                    account_id=account_id,
                    item_events=item_events,
                    combat_events=combat_events,
                )
            )
        return snapshots

    def _load_match_accounts(self, match_id: str) -> list[str]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT location_events.account_id
                FROM player_combat_location_events location_events
                WHERE location_events.match_id = %s
                  AND location_events.action IN ('kill', 'dbno_caused', 'finish')
                  AND location_events.damage_causer_name IS NOT NULL
                  AND (
                        location_events.damage_causer_name LIKE 'Weap%%'
                        OR location_events.damage_causer_name LIKE 'Item_Weapon_%%'
                  )
                ORDER BY location_events.account_id ASC
                """,
                (match_id,),
            )
            return [str(row["account_id"]) for row in cursor.fetchall()]

    def _load_item_events(self, *, match_id: str, account_id: str) -> list[ItemLoadoutEvent]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    event_index,
                    action,
                    item_code,
                    item_name_ko,
                    item_category,
                    item_sub_category,
                    parent_item_code
                FROM player_item_events
                WHERE match_id = %s
                  AND account_id = %s
                  AND action IN ('attach', 'detach')
                  AND parent_item_code IS NOT NULL
                  AND item_code IS NOT NULL
                ORDER BY event_index ASC
                """,
                (match_id, account_id),
            )
            return [
                ItemLoadoutEvent(
                    event_index=_int(row.get("event_index")),
                    action=str(row.get("action") or ""),
                    item_code=_optional_text(row.get("item_code")),
                    item_name_ko=_optional_text(row.get("item_name_ko")),
                    item_category=_optional_text(row.get("item_category")),
                    item_sub_category=_optional_text(row.get("item_sub_category")),
                    parent_item_code=_optional_text(row.get("parent_item_code")),
                )
                for row in cursor.fetchall()
            ]

    def _load_combat_events(self, *, match_id: str, account_id: str) -> list[CombatLoadoutEvent]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    event_index,
                    action,
                    event_at_kst,
                    damage_causer_name,
                    distance_m,
                    is_headshot,
                    x,
                    y,
                    related_x,
                    related_y
                FROM player_combat_location_events
                WHERE match_id = %s
                  AND account_id = %s
                  AND action IN ('kill', 'dbno_caused', 'finish')
                  AND damage_causer_name IS NOT NULL
                  AND (
                        damage_causer_name LIKE 'Weap%%'
                        OR damage_causer_name LIKE 'Item_Weapon_%%'
                  )
                ORDER BY event_index ASC, action ASC
                """,
                (match_id, account_id),
            )
            return [
                CombatLoadoutEvent(
                    event_index=_int(row.get("event_index")),
                    action=str(row.get("action") or ""),
                    event_at_kst=row.get("event_at_kst"),
                    damage_causer_name=_optional_text(row.get("damage_causer_name")),
                    distance_m=_combat_distance_from_row(row),
                    is_headshot=bool(row.get("is_headshot")),
                )
                for row in cursor.fetchall()
            ]

    def _upsert_snapshots(self, snapshots: list[CombatLoadoutSnapshot]) -> None:
        if not snapshots:
            return

        timestamp = _mysql_datetime(now_kst())
        rows = [
            (
                snapshot.match_id,
                snapshot.account_id,
                snapshot.combat_event_index,
                snapshot.combat_action,
                _mysql_datetime(snapshot.combat_event_at_kst),
                snapshot.weapon_code,
                snapshot.weapon_name_ko,
                json.dumps(list(snapshot.attachment_codes), ensure_ascii=False, separators=(",", ":")),
                json.dumps(list(snapshot.attachment_names_ko), ensure_ascii=False, separators=(",", ":")),
                snapshot.attachment_count,
                snapshot.distance_m,
                snapshot.is_headshot,
                timestamp,
            )
            for snapshot in snapshots
        ]

        with self.connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO player_combat_loadout_snapshots (
                    match_id,
                    account_id,
                    combat_event_index,
                    combat_action,
                    combat_event_at_kst,
                    weapon_code,
                    weapon_name_ko,
                    attachment_codes,
                    attachment_names_ko,
                    attachment_count,
                    distance_m,
                    is_headshot,
                    updated_at_kst
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    combat_event_at_kst = VALUES(combat_event_at_kst),
                    weapon_code = VALUES(weapon_code),
                    weapon_name_ko = VALUES(weapon_name_ko),
                    attachment_codes = VALUES(attachment_codes),
                    attachment_names_ko = VALUES(attachment_names_ko),
                    attachment_count = VALUES(attachment_count),
                    distance_m = VALUES(distance_m),
                    is_headshot = VALUES(is_headshot),
                    updated_at_kst = VALUES(updated_at_kst)
                """,
                rows,
            )


def build_loadout_snapshots(
    *,
    match_id: str,
    account_id: str,
    item_events: Iterable[ItemLoadoutEvent],
    combat_events: Iterable[CombatLoadoutEvent],
) -> list[CombatLoadoutSnapshot]:
    sorted_items = sorted(item_events, key=lambda event: event.event_index)
    sorted_combat = sorted(combat_events, key=lambda event: (event.event_index, event.action))
    attachments_by_weapon: dict[str, set[str]] = {}
    attachment_names: dict[str, str] = {}
    snapshots: list[CombatLoadoutSnapshot] = []
    item_index = 0

    for combat_event in sorted_combat:
        while item_index < len(sorted_items) and sorted_items[item_index].event_index <= combat_event.event_index:
            _apply_item_event(sorted_items[item_index], attachments_by_weapon, attachment_names)
            item_index += 1

        if combat_event.action not in LOADOUT_SNAPSHOT_ACTIONS:
            continue
        weapon_code = normalize_weapon_code(combat_event.damage_causer_name)
        if not weapon_code or not weapon_code.startswith("Weap"):
            continue

        attachment_codes = tuple(sorted(attachments_by_weapon.get(weapon_code, set())))
        attachment_names_ko = tuple(
            attachment_names.get(code) or translate_code(code, "item")
            for code in attachment_codes
        )
        snapshots.append(
            CombatLoadoutSnapshot(
                match_id=match_id,
                account_id=account_id,
                combat_event_index=combat_event.event_index,
                combat_action=combat_event.action,
                combat_event_at_kst=combat_event.event_at_kst,
                weapon_code=weapon_code,
                weapon_name_ko=translate_code(weapon_code, "damage_causer"),
                attachment_codes=attachment_codes,
                attachment_names_ko=attachment_names_ko,
                distance_m=combat_event.distance_m,
                is_headshot=combat_event.is_headshot,
            )
        )

    return snapshots


def _apply_item_event(
    event: ItemLoadoutEvent,
    attachments_by_weapon: dict[str, set[str]],
    attachment_names: dict[str, str],
) -> None:
    weapon_code = normalize_weapon_code(event.parent_item_code)
    attachment_code = event.item_code
    if not weapon_code or not weapon_code.startswith("Weap") or not attachment_code:
        return
    if not attachment_code.startswith("Item_Attach_"):
        return

    attachment_names[attachment_code] = event.item_name_ko or translate_code(attachment_code, "item")
    weapon_attachments = attachments_by_weapon.setdefault(weapon_code, set())
    if event.action == "attach":
        weapon_attachments.add(attachment_code)
    elif event.action == "detach":
        weapon_attachments.discard(attachment_code)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _combat_distance_from_row(row: Mapping[str, Any]) -> float | None:
    x = _optional_float(row.get("x"))
    y = _optional_float(row.get("y"))
    related_x = _optional_float(row.get("related_x"))
    related_y = _optional_float(row.get("related_y"))
    if x is not None and y is not None and related_x is not None and related_y is not None:
        return sqrt((related_x - x) ** 2 + (related_y - y) ** 2) / 100.0

    distance = _optional_float(row.get("distance_m"))
    if distance is None:
        return None
    return distance / 100.0 if distance > 1500 else distance


def _mysql_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=None)
