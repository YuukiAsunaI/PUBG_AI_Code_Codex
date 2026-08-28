from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping
import gzip
import json
import math

from pubg_ai.code_translator import CodeTranslator
from pubg_ai.file_io import sha256_file
from pubg_ai.raw_storage import RawPayloadStore, RawStorageError
from pubg_ai.telemetry_event_catalog import get_telemetry_event_definition
from pubg_ai.time_utils import KST, to_kst


class MatchExplorerError(RuntimeError):
    """Raised when a stored match cannot be explored safely."""


_CHARACTER_KEYS = (
    "attacker",
    "victim",
    "killer",
    "finisher",
    "character",
    "reviver",
    "instigator",
    "owner",
)
_POSITION_EVENT = "LogPlayerPosition"
_DAMAGE_REASON_KO = {
    "HeadShot": "머리",
    "TorsoShot": "몸통",
    "PelvisShot": "골반",
    "ArmShot": "팔",
    "LegShot": "다리",
    "NonSpecific": "부위 미상",
}


class MatchExplorerService:
    def __init__(
        self,
        connection: Any,
        raw_store: RawPayloadStore,
        translator: CodeTranslator | None = None,
    ) -> None:
        self.connection = connection
        self.raw_store = raw_store
        self.translator = translator or CodeTranslator()

    def list_matches(
        self,
        *,
        shard: str | None = None,
        account_id: str | None = None,
        search: str | None = None,
        map_name: str | None = None,
        game_mode: str | None = None,
        match_type: str | None = None,
        created_from_kst: date | None = None,
        created_to_kst: date | None = None,
        telemetry_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        normalized_limit = max(1, min(int(limit), 200))
        normalized_offset = max(0, int(offset))
        clauses: list[str] = []
        params: list[Any] = []

        if normalized := _optional_text(shard):
            clauses.append("matches.shard = %s")
            params.append(normalized)
        if normalized := _optional_text(account_id):
            clauses.append(
                "EXISTS ("
                "SELECT 1 FROM match_participants requested_participant "
                "WHERE requested_participant.match_id = matches.match_id "
                "AND requested_participant.account_id = %s)"
            )
            params.append(normalized)
        if normalized := _optional_text(map_name):
            clauses.append("matches.map_name = %s")
            params.append(normalized)
        if normalized := _optional_text(game_mode):
            clauses.append("matches.game_mode = %s")
            params.append(normalized)
        if normalized := _optional_text(match_type):
            clauses.append("matches.match_type = %s")
            params.append(normalized)
        if created_from_kst is not None:
            clauses.append("matches.created_at_kst >= %s")
            params.append(datetime.combine(created_from_kst, time.min))
        if created_to_kst is not None:
            clauses.append("matches.created_at_kst < %s")
            params.append(datetime.combine(created_to_kst + timedelta(days=1), time.min))
        if telemetry_only:
            clauses.append("raw_telemetry.match_id IS NOT NULL")
        if normalized := _optional_text(search):
            pattern = f"%{normalized}%"
            clauses.append(
                "(matches.match_id LIKE %s OR matches.map_name LIKE %s "
                "OR matches.game_mode LIKE %s OR EXISTS ("
                "SELECT 1 FROM match_participants searched_participant "
                "WHERE searched_participant.match_id = matches.match_id "
                "AND searched_participant.name LIKE %s))"
            )
            params.extend([pattern, pattern, pattern, pattern])

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        from_sql = (
            "FROM analysis_matches AS matches "
            "LEFT JOIN raw_telemetry_payloads raw_telemetry "
            "ON raw_telemetry.match_id = matches.match_id"
        )
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"SELECT COUNT(*) AS total {from_sql} {where_sql}",
                tuple(params),
            )
            total_row = cursor.fetchone() or {}
            cursor.execute(
                f"""
                SELECT
                    matches.*,
                    (raw_telemetry.match_id IS NOT NULL) AS has_telemetry,
                    raw_telemetry.size_bytes AS telemetry_size_bytes,
                    (
                        SELECT COUNT(*) FROM match_participants scoped_participant
                        WHERE scoped_participant.match_id = matches.match_id
                    ) AS participant_count,
                    (
                        SELECT COUNT(*) FROM match_participants scoped_participant
                        WHERE scoped_participant.match_id = matches.match_id
                          AND EXISTS (
                              SELECT 1 FROM registered_players registered
                              WHERE registered.shard = matches.shard
                                AND registered.account_id = scoped_participant.account_id
                          )
                    ) AS registered_participant_count
                {from_sql}
                {where_sql}
                ORDER BY matches.created_at_kst DESC, matches.match_id DESC
                LIMIT %s OFFSET %s
                """,
                tuple([*params, normalized_limit, normalized_offset]),
            )
            rows = cursor.fetchall()

        records = [self._match_record(row) for row in rows]
        total = int(total_row.get("total") or 0)
        return {
            "matches": records,
            "total": total,
            "limit": normalized_limit,
            "offset": normalized_offset,
            "has_previous": normalized_offset > 0,
            "has_next": normalized_offset + len(records) < total,
        }

    def get_match_detail(self, match_id: str) -> dict[str, Any] | None:
        payload = self._match_with_telemetry(match_id)
        if payload is None:
            return None
        participants = self._participants(match_id, str(payload.get("shard") or ""))
        participant_index = {
            str(row["account_id"]): row
            for row in participants
            if row.get("account_id")
        }
        teams = _team_records(participants)
        return {
            "match": self._match_record(payload),
            "participants": participants,
            "teams": teams,
            "summary": {
                "participants": len(participants),
                "humans": sum(not bool(row.get("is_ai_or_bot")) for row in participants),
                "bots": sum(bool(row.get("is_ai_or_bot")) for row in participants),
                "registered_players": sum(bool(row.get("is_registered")) for row in participants),
                "teams": len(teams),
                "kills": sum(int(row.get("kills") or 0) for row in participants),
                "assists": sum(int(row.get("assists") or 0) for row in participants),
                "damage_dealt": round(
                    sum(float(row.get("damage_dealt") or 0.0) for row in participants),
                    2,
                ),
            },
            "telemetry": self._telemetry_summary(payload, participant_index),
        }

    def list_events(
        self,
        *,
        match_id: str,
        domain: str | None = None,
        event_type: str | None = None,
        account_id: str | None = None,
        team_id: int | None = None,
        search: str | None = None,
        include_positions: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        payload = self._match_with_telemetry(match_id)
        if payload is None:
            raise MatchExplorerError("저장된 매치를 찾을 수 없습니다.")
        events = self._load_events(payload)
        participants = self._participants(match_id, str(payload.get("shard") or ""))
        participant_index = {
            str(row["account_id"]): row
            for row in participants
            if row.get("account_id")
        }
        match_started_at = _match_start_time(events)
        normalized_domain = _optional_text(domain)
        normalized_type = _optional_text(event_type)
        normalized_account = _optional_text(account_id)
        normalized_search = (_optional_text(search) or "").casefold()
        normalized_limit = max(1, min(int(limit), 500))
        normalized_offset = max(0, int(offset))

        matched: list[dict[str, Any]] = []
        type_counts: Counter[str] = Counter()
        domain_counts: Counter[str] = Counter()
        for sequence, event in enumerate(events):
            raw_type = _optional_text(event.get("_T")) or "(missing)"
            definition = get_telemetry_event_definition(raw_type)
            type_counts[raw_type] += 1
            domain_counts[definition.domain] += 1
            if not include_positions and raw_type == _POSITION_EVENT:
                continue
            if normalized_domain and definition.domain != normalized_domain:
                continue
            if normalized_type and raw_type != normalized_type:
                continue
            record = self._event_record(
                event,
                sequence=sequence,
                match_started_at=match_started_at,
                participant_index=participant_index,
            )
            if normalized_account and normalized_account not in record["participant_account_ids"]:
                continue
            if team_id is not None and team_id not in record["participant_team_ids"]:
                continue
            if normalized_search and normalized_search not in _event_search_text(record).casefold():
                continue
            matched.append(record)

        total = len(matched)
        page = matched[normalized_offset : normalized_offset + normalized_limit]
        return {
            "match_id": match_id,
            "events": page,
            "total": total,
            "limit": normalized_limit,
            "offset": normalized_offset,
            "has_previous": normalized_offset > 0,
            "has_next": normalized_offset + len(page) < total,
            "available_event_types": [
                {
                    "event_type": name,
                    "label": get_telemetry_event_definition(name).label_ko,
                    "domain": get_telemetry_event_definition(name).domain,
                    "count": count,
                }
                for name, count in sorted(type_counts.items(), key=lambda row: (-row[1], row[0]))
            ],
            "available_domains": [
                {"domain": name, "count": count}
                for name, count in sorted(domain_counts.items(), key=lambda row: (-row[1], row[0]))
            ],
        }

    def get_event(self, *, match_id: str, sequence: int) -> dict[str, Any] | None:
        payload = self._match_with_telemetry(match_id)
        if payload is None:
            return None
        events = self._load_events(payload)
        if sequence < 0 or sequence >= len(events):
            return None
        participants = self._participants(match_id, str(payload.get("shard") or ""))
        participant_index = {
            str(row["account_id"]): row
            for row in participants
            if row.get("account_id")
        }
        event = events[sequence]
        return {
            "summary": self._event_record(
                event,
                sequence=sequence,
                match_started_at=_match_start_time(events),
                participant_index=participant_index,
            ),
            "raw_event": _json_value(self.translator.translate_event_codes(event)),
        }

    def get_replay_source(self, match_id: str) -> dict[str, Any] | None:
        """Return the stored match, every participant, and verified raw telemetry events."""
        payload = self._match_with_telemetry(match_id)
        if payload is None:
            return None
        participants = self._participants(match_id, str(payload.get("shard") or ""))
        return {
            "match": self._match_record(payload),
            "participants": participants,
            "events": self._load_events(payload),
        }

    def _match_with_telemetry(self, match_id: str) -> dict[str, Any] | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    matches.*,
                    raw_telemetry.storage_root AS telemetry_storage_root,
                    raw_telemetry.relative_path AS telemetry_relative_path,
                    raw_telemetry.compression AS telemetry_compression,
                    raw_telemetry.size_bytes AS telemetry_size_bytes,
                    raw_telemetry.sha256 AS telemetry_sha256,
                    raw_telemetry.fetched_at_kst AS telemetry_fetched_at_kst,
                    (raw_telemetry.match_id IS NOT NULL) AS has_telemetry
                FROM analysis_matches AS matches
                LEFT JOIN raw_telemetry_payloads raw_telemetry
                    ON raw_telemetry.match_id = matches.match_id
                WHERE matches.match_id = %s
                LIMIT 1
                """,
                (match_id,),
            )
            row = cursor.fetchone()
        return dict(row) if row else None

    def _participants(self, match_id: str, shard: str) -> list[dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    participants.*,
                    EXISTS (
                        SELECT 1 FROM registered_players registered
                        WHERE registered.shard = %s
                          AND registered.account_id = participants.account_id
                    ) AS is_registered
                FROM match_participants participants
                WHERE participants.match_id = %s
                ORDER BY
                    CASE WHEN participants.win_place IS NULL THEN 1 ELSE 0 END,
                    participants.win_place,
                    participants.team_id,
                    participants.name
                """,
                (shard, match_id),
            )
            rows = cursor.fetchall()
        records: list[dict[str, Any]] = []
        for source in rows:
            row = dict(source)
            row["is_ai_or_bot"] = bool(row.get("is_ai_or_bot"))
            row["is_registered"] = bool(row.get("is_registered"))
            row["raw_stats"] = _json_mapping(row.get("raw_stats"))
            records.append(_json_value(row))
        return records

    def _load_events(self, payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
        relative_path = _optional_text(payload.get("telemetry_relative_path"))
        if relative_path is None:
            raise MatchExplorerError("이 매치의 원본 텔레메트리가 아직 저장되지 않았습니다.")
        try:
            path = self.raw_store.resolve_path(relative_path)
        except RawStorageError as exc:
            raise MatchExplorerError(str(exc)) from exc
        if not path.exists():
            raise MatchExplorerError(f"원본 텔레메트리 파일을 찾을 수 없습니다: {relative_path}")
        stat = path.stat()
        return _load_telemetry_file(
            str(path),
            _optional_text(payload.get("telemetry_compression")) or "gzip",
            _optional_text(payload.get("telemetry_sha256")) or "",
            stat.st_mtime_ns,
            stat.st_size,
        )

    def _telemetry_summary(
        self,
        payload: Mapping[str, Any],
        participant_index: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        if not payload.get("has_telemetry"):
            return {"available": False, "event_count": 0, "event_types": [], "domains": []}
        events = self._load_events(payload)
        type_counts = Counter(_optional_text(event.get("_T")) or "(missing)" for event in events)
        domain_counts: Counter[str] = Counter()
        for event_type, count in type_counts.items():
            domain_counts[get_telemetry_event_definition(event_type).domain] += count
        timestamps = [
            parsed
            for event in events
            if (parsed := _parse_event_time(event.get("_D"))) is not None
        ]
        return {
            "available": True,
            "event_count": len(events),
            "size_bytes": int(payload.get("telemetry_size_bytes") or 0),
            "first_event_at_kst": timestamps[0].isoformat() if timestamps else None,
            "last_event_at_kst": timestamps[-1].isoformat() if timestamps else None,
            "event_types": [
                {
                    "event_type": event_type,
                    "label": get_telemetry_event_definition(event_type).label_ko,
                    "domain": get_telemetry_event_definition(event_type).domain,
                    "count": count,
                }
                for event_type, count in type_counts.most_common()
            ],
            "domains": [
                {"domain": domain, "count": count}
                for domain, count in domain_counts.most_common()
            ],
            "known_participant_count": len(participant_index),
        }

    def _match_record(self, source: Mapping[str, Any]) -> dict[str, Any]:
        row = dict(source)
        for internal_key in (
            "telemetry_storage_root",
            "telemetry_relative_path",
            "telemetry_compression",
            "telemetry_sha256",
            "telemetry_fetched_at_kst",
        ):
            row.pop(internal_key, None)
        row["map_label"] = self.translator.translate(row.get("map_name"), "map").label
        row["game_mode_label"] = self.translator.translate(row.get("game_mode"), "game_mode").label
        row["match_type_label"] = self.translator.translate(row.get("match_type"), "match_type").label
        row["team_mode_label"] = self.translator.translate(row.get("team_mode"), "team_mode").label
        row["perspective_label"] = self.translator.translate(row.get("perspective"), "perspective").label
        row["season_state_label"] = self.translator.translate(row.get("season_state"), "season_state").label
        for key in ("is_custom_match", "has_telemetry"):
            if key in row:
                row[key] = bool(row[key])
        return _json_value(row)

    def _event_record(
        self,
        event: Mapping[str, Any],
        *,
        sequence: int,
        match_started_at: datetime | None,
        participant_index: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        event_type = _optional_text(event.get("_T")) or "(missing)"
        definition = get_telemetry_event_definition(event_type)
        characters = _event_characters(event, participant_index)
        actor, target = _actor_target(event_type, characters)
        weapon_code = _weapon_code(event)
        item_code = _item_code(event)
        event_at = _parse_event_time(event.get("_D"))
        elapsed_seconds = (
            max(0.0, (event_at - match_started_at).total_seconds())
            if event_at is not None and match_started_at is not None
            else _optional_float(event.get("elapsedTime"))
        )
        location = _character_location(actor) or _event_location(event)
        target_location = _character_location(target)
        damage = _optional_float(event.get("damage"))
        distance_m = _event_distance_m(event, actor, target)
        participant_account_ids = sorted(
            {
                str(character["account_id"])
                for character in characters.values()
                if character.get("account_id")
            }
            | set(_assist_account_ids(event))
        )
        participant_team_ids = sorted(
            {
                int(character["team_id"])
                for character in characters.values()
                if character.get("team_id") is not None
            }
        )
        weapon_name = (
            self.translator.translate(weapon_code, "damage_causer").label
            if weapon_code
            else None
        )
        item_name = self.translator.translate(item_code, "item").label if item_code else None
        body_part = _DAMAGE_REASON_KO.get(_optional_text(event.get("damageReason")) or "")
        record = {
            "sequence": sequence,
            "event_type": event_type,
            "event_label": definition.label_ko,
            "domain": definition.domain,
            "support": definition.support,
            "timestamp_kst": event_at.isoformat() if event_at else None,
            "elapsed_seconds": round(elapsed_seconds, 3) if elapsed_seconds is not None else None,
            "actor": actor,
            "target": target,
            "characters": characters,
            "participant_account_ids": participant_account_ids,
            "participant_team_ids": participant_team_ids,
            "assist_account_ids": _assist_account_ids(event),
            "weapon_code": weapon_code,
            "weapon_name": weapon_name,
            "item_code": item_code,
            "item_name": item_name,
            "damage": round(damage, 2) if damage is not None else None,
            "damage_reason": _optional_text(event.get("damageReason")),
            "body_part": body_part,
            "is_headshot": body_part == "머리",
            "distance_m": round(distance_m, 2) if distance_m is not None else None,
            "location": location,
            "target_location": target_location,
            "summary": "",
        }
        record["summary"] = _event_summary(record)
        return _json_value(record)


@lru_cache(maxsize=3)
def _load_telemetry_file(
    path_text: str,
    compression: str,
    expected_sha256: str,
    _mtime_ns: int,
    _size_bytes: int,
) -> tuple[Mapping[str, Any], ...]:
    path = Path(path_text)
    if expected_sha256 and sha256_file(path).casefold() != expected_sha256.casefold():
        raise MatchExplorerError("원본 텔레메트리 파일의 SHA-256 검증에 실패했습니다.")
    try:
        if compression == "gzip" or path.suffix.casefold() == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as file:
                loaded = json.load(file)
        else:
            with path.open("r", encoding="utf-8") as file:
                loaded = json.load(file)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MatchExplorerError("원본 텔레메트리 파일을 읽을 수 없습니다.") from exc
    if not isinstance(loaded, list):
        raise MatchExplorerError("원본 텔레메트리의 최상위 값이 이벤트 배열이 아닙니다.")
    return tuple(event for event in loaded if isinstance(event, Mapping))


def _team_records(participants: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for participant in participants:
        roster_id = _optional_text(participant.get("roster_id"))
        team_id = participant.get("team_id")
        key = roster_id or (
            f"team:{team_id}"
            if team_id is not None
            else f"player:{participant.get('account_id')}"
        )
        grouped.setdefault(key, []).append(participant)
    records = []
    for key, members in grouped.items():
        places = [
            int(value)
            for row in members
            if (value := row.get("win_place")) is not None
        ]
        records.append(
            {
                "team_key": key,
                "team_id": next(
                    (row.get("team_id") for row in members if row.get("team_id") is not None),
                    None,
                ),
                "win_place": min(places) if places else None,
                "member_count": len(members),
                "human_count": sum(not bool(row.get("is_ai_or_bot")) for row in members),
                "bot_count": sum(bool(row.get("is_ai_or_bot")) for row in members),
                "registered_count": sum(bool(row.get("is_registered")) for row in members),
                "kills": sum(int(row.get("kills") or 0) for row in members),
                "assists": sum(int(row.get("assists") or 0) for row in members),
                "damage_dealt": round(
                    sum(float(row.get("damage_dealt") or 0.0) for row in members),
                    2,
                ),
                "members": [
                    {
                        "account_id": row.get("account_id"),
                        "name": row.get("name"),
                        "is_ai_or_bot": bool(row.get("is_ai_or_bot")),
                        "is_registered": bool(row.get("is_registered")),
                    }
                    for row in members
                ],
            }
        )
    return sorted(
        records,
        key=lambda row: (
            row["win_place"] is None,
            row["win_place"] if row["win_place"] is not None else 9999,
            row["team_id"] if row["team_id"] is not None else 9999,
        ),
    )


def _event_characters(
    event: Mapping[str, Any],
    participant_index: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for key in _CHARACTER_KEYS:
        value = event.get(key)
        if not isinstance(value, Mapping):
            continue
        account_id = _optional_text(value.get("accountId"))
        participant = participant_index.get(account_id or "", {})
        records[key] = {
            "account_id": account_id,
            "name": _optional_text(value.get("name")) or participant.get("name"),
            "team_id": (
                _optional_int(value.get("teamId"))
                if value.get("teamId") is not None
                else participant.get("team_id")
            ),
            "health": _optional_float(value.get("health")),
            "is_in_vehicle": bool(value.get("isInVehicle")),
            "is_dbno": bool(value.get("isDBNO")),
            "location": _location(value.get("location")),
            "is_ai_or_bot": bool(participant.get("is_ai_or_bot")),
            "is_registered": bool(participant.get("is_registered")),
        }
    return records


def _actor_target(
    event_type: str,
    characters: Mapping[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if event_type == "LogPlayerKillV2":
        return characters.get("killer") or characters.get("finisher"), characters.get("victim")
    if event_type == "LogPlayerRevive":
        return characters.get("reviver") or characters.get("character"), characters.get("victim")
    if event_type in {"LogPlayerTakeDamage", "LogPlayerMakeGroggy", "LogArmorDestroy"}:
        return characters.get("attacker"), characters.get("victim")
    return (
        characters.get("character")
        or characters.get("attacker")
        or characters.get("killer")
        or characters.get("owner"),
        characters.get("victim"),
    )


def _weapon_code(event: Mapping[str, Any]) -> str | None:
    weapon = event.get("weapon")
    if isinstance(weapon, Mapping):
        if code := _optional_text(weapon.get("itemId")):
            return code
    for key in ("killerDamageInfo", "finishDamageInfo"):
        damage_info = event.get(key)
        if isinstance(damage_info, Mapping):
            if code := _optional_text(damage_info.get("damageCauserName")):
                return code
    return _optional_text(event.get("damageCauserName"))


def _item_code(event: Mapping[str, Any]) -> str | None:
    for key in ("item", "childItem", "parentItem"):
        item = event.get(key)
        if isinstance(item, Mapping):
            if code := _optional_text(item.get("itemId")):
                return code
    return None


def _assist_account_ids(event: Mapping[str, Any]) -> list[str]:
    result: list[str] = []
    for key in ("assists_AccountId", "assistsAccountId"):
        value = event.get(key)
        if not isinstance(value, list):
            continue
        result.extend(text for item in value if (text := _optional_text(item)))
    return sorted(set(result))


def _event_distance_m(
    event: Mapping[str, Any],
    actor: Mapping[str, Any] | None,
    target: Mapping[str, Any] | None,
) -> float | None:
    for source in (event, event.get("killerDamageInfo"), event.get("finishDamageInfo")):
        if isinstance(source, Mapping):
            if (distance := _optional_float(source.get("distance"))) is not None:
                return distance / 100.0
    left = actor.get("location") if actor else None
    right = target.get("location") if target else None
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        left_x = _optional_float(left.get("x"))
        left_y = _optional_float(left.get("y"))
        right_x = _optional_float(right.get("x"))
        right_y = _optional_float(right.get("y"))
        if None not in {left_x, left_y, right_x, right_y}:
            return math.hypot(
                float(right_x) - float(left_x),
                float(right_y) - float(left_y),
            ) / 100.0
    return None


def _event_summary(record: Mapping[str, Any]) -> str:
    label = str(record.get("event_label") or record.get("event_type") or "이벤트")
    actor = record.get("actor") if isinstance(record.get("actor"), Mapping) else {}
    target = record.get("target") if isinstance(record.get("target"), Mapping) else {}
    actor_name = _optional_text(actor.get("name")) or "행위자 미상"
    target_name = _optional_text(target.get("name"))
    weapon_or_item = _optional_text(record.get("weapon_name")) or _optional_text(
        record.get("item_name")
    )
    parts = [actor_name]
    if target_name:
        parts.append(f"-> {target_name}")
    parts.append(label)
    if weapon_or_item:
        parts.append(weapon_or_item)
    if record.get("damage") is not None:
        parts.append(f"피해 {float(record['damage']):.1f}")
    if body_part := _optional_text(record.get("body_part")):
        parts.append(body_part)
    if record.get("distance_m") is not None:
        parts.append(f"{float(record['distance_m']):.1f}m")
    return " · ".join(parts)


def _event_search_text(record: Mapping[str, Any]) -> str:
    values: list[str] = []
    for key in (
        "event_type",
        "event_label",
        "domain",
        "summary",
        "weapon_code",
        "weapon_name",
        "item_code",
        "item_name",
    ):
        if value := _optional_text(record.get(key)):
            values.append(value)
    for role in ("actor", "target"):
        character = record.get(role)
        if isinstance(character, Mapping):
            for key in ("name", "account_id"):
                if value := _optional_text(character.get(key)):
                    values.append(value)
    return " ".join(values)


def _match_start_time(events: Iterable[Mapping[str, Any]]) -> datetime | None:
    fallback: datetime | None = None
    for event in events:
        event_at = _parse_event_time(event.get("_D"))
        if fallback is None and event_at is not None:
            fallback = event_at
        if event.get("_T") == "LogMatchStart" and event_at is not None:
            return event_at
    return fallback


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


def _character_location(
    character: Mapping[str, Any] | None,
) -> dict[str, float | None] | None:
    if not character:
        return None
    return _location(character.get("location"))


def _event_location(event: Mapping[str, Any]) -> dict[str, float | None] | None:
    return _location(event.get("location"))


def _location(value: Any) -> dict[str, float | None] | None:
    if not isinstance(value, Mapping):
        return None
    x = _optional_float(value.get("x"))
    y = _optional_float(value.get("y"))
    z = _optional_float(value.get("z"))
    if x is None and y is None and z is None:
        return None
    return {"x": x, "y": y, "z": z}


def _json_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(loaded) if isinstance(loaded, Mapping) else {}
    return {}


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        localized = value if value.tzinfo is not None else value.replace(tzinfo=KST)
        return localized.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


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


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
