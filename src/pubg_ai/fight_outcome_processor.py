from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping
import gzip
import json

from pubg_ai.code_translator import translate_code
from pubg_ai.combat_outcomes import is_dbno_fight_mode
from pubg_ai.database import mysql_transaction
from pubg_ai.raw_storage import RawPayloadStore
from pubg_ai.time_utils import now_kst, to_kst
from pubg_ai.weapon_stats import normalize_weapon_code


FIGHT_OUTCOME_PARSER_VERSION = "fight-outcomes-v2"
FightOutcomeType = Literal["win", "loss"]
FightOutcomeReason = Literal["kill", "dbno_caused", "death", "dbno_taken"]


class FightOutcomeProcessingError(RuntimeError):
    """Raised when raw telemetry cannot be parsed into fight-outcome rows."""


@dataclass(frozen=True)
class WeaponContext:
    event_index: int
    weapon_code: str
    weapon_name_ko: str
    attachment_codes: tuple[str, ...]
    attachment_names_ko: tuple[str, ...]
    source: str


@dataclass(frozen=True)
class PlayerFightOutcome:
    match_id: str
    account_id: str
    opponent_account_id: str | None
    event_index: int
    event_type: str
    event_at_kst: datetime | None
    common_is_game: float | None
    outcome_type: FightOutcomeType
    outcome_reason: FightOutcomeReason
    game_mode: str
    dbno_id: str | None
    weapon_code: str | None
    weapon_name_ko: str | None
    attachment_codes: tuple[str, ...]
    attachment_names_ko: tuple[str, ...]
    weapon_context_source: str
    weapon_context_event_index: int | None
    opponent_weapon_code: str | None
    opponent_weapon_name_ko: str | None
    opponent_is_bot: bool | None
    is_friendly_fire: bool
    damage_type_category: str | None
    damage_reason: str | None
    is_headshot: bool
    distance_m: float | None

    @property
    def counts_as_fight_win(self) -> bool:
        return self.outcome_type == "win"

    @property
    def counts_as_fight_loss(self) -> bool:
        return self.outcome_type == "loss"

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["event_at_kst"] = self.event_at_kst.isoformat() if self.event_at_kst else None
        record["attachment_codes"] = list(self.attachment_codes)
        record["attachment_names_ko"] = list(self.attachment_names_ko)
        return record


@dataclass(frozen=True)
class FightOutcomeProcessingResult:
    candidate_payloads: int
    parsed_payloads: int
    skipped_existing: int
    skipped_no_tracked_player: int
    failed_payloads: int
    events_read: int
    tracked_players: int
    generated_outcomes: int
    generated_wins: int
    generated_losses: int
    generated_loadout_snapshots: int

    def to_record(self) -> dict[str, int]:
        return asdict(self)


class FightOutcomeProcessor:
    def __init__(self, connection: Any, raw_store: RawPayloadStore) -> None:
        self.connection = connection
        self.raw_store = raw_store

    def process_raw_telemetry(
        self,
        *,
        limit: int = 10,
        force: bool = False,
    ) -> FightOutcomeProcessingResult:
        limit = max(1, min(int(limit), 200))
        payloads = self._list_raw_telemetry_payloads(limit=limit, force=force)
        parsed_payloads = 0
        skipped_existing = 0
        skipped_no_tracked_player = 0
        failed_payloads = 0
        events_read = 0
        tracked_players = 0
        generated_outcomes = 0
        generated_wins = 0
        generated_losses = 0
        generated_loadout_snapshots = 0

        for payload in payloads:
            match_id = str(payload["match_id"])
            shard = str(payload["shard"])
            account_ids = self._tracked_account_ids_for_match(
                match_id=match_id,
                shard=shard,
                include_processed=force,
            )
            if not account_ids:
                if force:
                    skipped_no_tracked_player += 1
                else:
                    skipped_existing += 1
                continue

            try:
                events = self._load_telemetry_events(payload)
                bot_account_ids = self._bot_account_ids_for_match(match_id)
                outcomes = build_fight_outcomes(
                    events,
                    match_id=match_id,
                    tracked_account_ids=account_ids,
                    game_mode=str(payload.get("game_mode") or payload.get("team_mode") or "unknown"),
                    bot_account_ids=bot_account_ids,
                )
                snapshot_count = self._replace_rows(
                    match_id=match_id,
                    account_ids=account_ids,
                    outcomes=outcomes,
                )
            except Exception:
                failed_payloads += 1
                continue

            parsed_payloads += 1
            events_read += len(events)
            tracked_players += len(account_ids)
            generated_outcomes += len(outcomes)
            generated_wins += sum(outcome.outcome_type == "win" for outcome in outcomes)
            generated_losses += sum(outcome.outcome_type == "loss" for outcome in outcomes)
            generated_loadout_snapshots += snapshot_count

        return FightOutcomeProcessingResult(
            candidate_payloads=len(payloads),
            parsed_payloads=parsed_payloads,
            skipped_existing=skipped_existing,
            skipped_no_tracked_player=skipped_no_tracked_player,
            failed_payloads=failed_payloads,
            events_read=events_read,
            tracked_players=tracked_players,
            generated_outcomes=generated_outcomes,
            generated_wins=generated_wins,
            generated_losses=generated_losses,
            generated_loadout_snapshots=generated_loadout_snapshots,
        )

    def _list_raw_telemetry_payloads(self, *, limit: int, force: bool) -> list[dict[str, Any]]:
        where = ""
        if not force:
            where = """
                WHERE EXISTS (
                    SELECT 1
                    FROM match_participants participants
                    INNER JOIN registered_players players
                        ON players.account_id = participants.account_id
                       AND players.shard = raw_payloads.shard
                    LEFT JOIN player_fight_outcome_processing_states states
                        ON states.match_id = participants.match_id
                       AND states.account_id = participants.account_id
                    WHERE participants.match_id = raw_payloads.match_id
                      AND (
                            states.match_id IS NULL
                            OR states.parser_version <> %s
                      )
                )
            """

        params: tuple[Any, ...] = (
            (FIGHT_OUTCOME_PARSER_VERSION, limit)
            if not force
            else (limit,)
        )
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT
                    raw_payloads.id,
                    raw_payloads.match_id,
                    raw_payloads.shard,
                    raw_payloads.relative_path,
                    raw_payloads.compression,
                    matches.game_mode,
                    matches.team_mode
                FROM raw_telemetry_payloads raw_payloads
                INNER JOIN matches
                    ON matches.match_id = raw_payloads.match_id
                {where}
                ORDER BY raw_payloads.id ASC
                LIMIT %s
                """,
                params,
            )
            return list(cursor.fetchall())

    def _tracked_account_ids_for_match(
        self,
        *,
        match_id: str,
        shard: str,
        include_processed: bool,
    ) -> set[str]:
        state_filter = (
            ""
            if include_processed
            else "AND (states.match_id IS NULL OR states.parser_version <> %s)"
        )
        params: list[Any] = [match_id, shard]
        if not include_processed:
            params.append(FIGHT_OUTCOME_PARSER_VERSION)
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT DISTINCT players.account_id
                FROM registered_players players
                INNER JOIN match_participants participants
                    ON participants.account_id = players.account_id
                   AND participants.match_id = %s
                LEFT JOIN player_fight_outcome_processing_states states
                    ON states.match_id = participants.match_id
                   AND states.account_id = participants.account_id
                WHERE players.shard = %s
                  {state_filter}
                """,
                params,
            )
            return {str(row["account_id"]) for row in cursor.fetchall()}

    def _bot_account_ids_for_match(self, match_id: str) -> set[str]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT account_id
                FROM match_participants
                WHERE match_id = %s AND is_ai_or_bot = 1
                """,
                (match_id,),
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
            raise FightOutcomeProcessingError(
                f"failed to read telemetry payload: {relative_path}"
            ) from exc

        if not isinstance(loaded, list):
            raise FightOutcomeProcessingError("telemetry payload root must be a list.")
        return [event for event in loaded if isinstance(event, Mapping)]

    def _replace_rows(
        self,
        *,
        match_id: str,
        account_ids: set[str],
        outcomes: list[PlayerFightOutcome],
    ) -> int:
        placeholders = ", ".join(["%s"] * len(account_ids))
        params = [match_id, *sorted(account_ids)]
        with mysql_transaction(self.connection):
            with self.connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    DELETE FROM player_fight_outcomes
                    WHERE match_id = %s AND account_id IN ({placeholders})
                    """,
                    params,
                )
                cursor.execute(
                    f"""
                    DELETE FROM player_combat_loadout_snapshots
                    WHERE match_id = %s
                      AND account_id IN ({placeholders})
                      AND combat_action IN ('kill', 'dbno_caused', 'death', 'dbno_taken')
                    """,
                    params,
                )

            self._insert_outcomes(outcomes)
            snapshot_count = self._upsert_loadout_snapshots(outcomes)
            self._upsert_processing_states(match_id=match_id, account_ids=account_ids, outcomes=outcomes)
        return snapshot_count

    def _insert_outcomes(self, outcomes: list[PlayerFightOutcome]) -> None:
        if not outcomes:
            return
        timestamp = _mysql_datetime(now_kst())
        rows = [
            (
                outcome.match_id,
                outcome.account_id,
                outcome.opponent_account_id,
                outcome.event_index,
                outcome.event_type,
                _mysql_datetime(outcome.event_at_kst),
                outcome.common_is_game,
                outcome.outcome_type,
                outcome.outcome_reason,
                outcome.game_mode,
                outcome.dbno_id,
                outcome.weapon_code,
                outcome.weapon_name_ko,
                _json_list(outcome.attachment_codes),
                _json_list(outcome.attachment_names_ko),
                outcome.weapon_context_source,
                outcome.weapon_context_event_index,
                outcome.opponent_weapon_code,
                outcome.opponent_weapon_name_ko,
                outcome.opponent_is_bot,
                outcome.is_friendly_fire,
                outcome.damage_type_category,
                outcome.damage_reason,
                outcome.is_headshot,
                outcome.distance_m,
                timestamp,
            )
            for outcome in outcomes
        ]
        with self.connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO player_fight_outcomes (
                    match_id,
                    account_id,
                    opponent_account_id,
                    event_index,
                    event_type,
                    event_at_kst,
                    common_is_game,
                    outcome_type,
                    outcome_reason,
                    game_mode,
                    dbno_id,
                    weapon_code,
                    weapon_name_ko,
                    attachment_codes,
                    attachment_names_ko,
                    weapon_context_source,
                    weapon_context_event_index,
                    opponent_weapon_code,
                    opponent_weapon_name_ko,
                    opponent_is_bot,
                    is_friendly_fire,
                    damage_type_category,
                    damage_reason,
                    is_headshot,
                    distance_m,
                    updated_at_kst
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                rows,
            )

    def _upsert_loadout_snapshots(self, outcomes: list[PlayerFightOutcome]) -> int:
        rows = []
        timestamp = _mysql_datetime(now_kst())
        for outcome in outcomes:
            if not outcome.weapon_code or not outcome.weapon_code.startswith("Weap"):
                continue
            rows.append(
                (
                    outcome.match_id,
                    outcome.account_id,
                    outcome.event_index,
                    outcome.outcome_reason,
                    _mysql_datetime(outcome.event_at_kst),
                    outcome.weapon_code,
                    outcome.weapon_name_ko,
                    _json_list(outcome.attachment_codes),
                    _json_list(outcome.attachment_names_ko),
                    len(outcome.attachment_codes),
                    outcome.distance_m,
                    outcome.is_headshot,
                    timestamp,
                )
            )
        if not rows:
            return 0

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
        return len(rows)

    def _upsert_processing_states(
        self,
        *,
        match_id: str,
        account_ids: set[str],
        outcomes: list[PlayerFightOutcome],
    ) -> None:
        counts = Counter(outcome.account_id for outcome in outcomes)
        timestamp = _mysql_datetime(now_kst())
        rows = [
            (
                match_id,
                account_id,
                FIGHT_OUTCOME_PARSER_VERSION,
                counts.get(account_id, 0),
                timestamp,
                timestamp,
            )
            for account_id in sorted(account_ids)
        ]
        with self.connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO player_fight_outcome_processing_states (
                    match_id,
                    account_id,
                    parser_version,
                    outcome_count,
                    processed_at_kst,
                    updated_at_kst
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    parser_version = VALUES(parser_version),
                    outcome_count = VALUES(outcome_count),
                    processed_at_kst = VALUES(processed_at_kst),
                    updated_at_kst = VALUES(updated_at_kst)
                """,
                rows,
            )


def build_fight_outcomes(
    events: Iterable[Mapping[str, Any]],
    *,
    match_id: str,
    tracked_account_ids: set[str],
    game_mode: str,
    bot_account_ids: set[str] | None = None,
) -> list[PlayerFightOutcome]:
    tracked = set(tracked_account_ids)
    bots = set(bot_account_ids or set())
    current_weapons: dict[str, WeaponContext] = {}
    weapon_states: dict[tuple[str, str], WeaponContext] = {}
    outcomes: list[PlayerFightOutcome] = []

    for event_index, event in enumerate(events):
        event_type = _optional_text(event.get("_T"))
        if event_type == "LogMatchStart":
            current_weapons.clear()
            weapon_states.clear()

        if _is_in_game_event(event):
            _update_weapon_state(
                event,
                event_index=event_index,
                current_weapons=current_weapons,
                weapon_states=weapon_states,
            )
        else:
            continue

        if event_type == "LogPlayerMakeGroggy" and is_dbno_fight_mode(game_mode):
            attacker = _mapping_value(event.get("attacker"))
            victim = _mapping_value(event.get("victim"))
            attacker_id = _account_id(attacker)
            victim_id = _account_id(victim)
            if not attacker_id or not victim_id or attacker_id == victim_id:
                continue

            attacker_context = _offensive_weapon_context(
                account_id=attacker_id,
                event_index=event_index,
                damage_info=event,
                current_weapons=current_weapons,
                weapon_states=weapon_states,
            )
            victim_context = current_weapons.get(victim_id)
            friendly_fire = _same_team(attacker, victim)

            if attacker_id in tracked:
                outcomes.append(
                    _build_outcome(
                        event=event,
                        event_index=event_index,
                        match_id=match_id,
                        game_mode=game_mode,
                        account_id=attacker_id,
                        opponent_account_id=victim_id,
                        outcome_type="win",
                        outcome_reason="dbno_caused",
                        weapon_context=attacker_context,
                        opponent_weapon_context=victim_context,
                        damage_info=event,
                        dbno_id=_optional_text(event.get("dBNOId")),
                        opponent_is_bot=victim_id in bots,
                        is_friendly_fire=friendly_fire,
                    )
                )
            if victim_id in tracked:
                outcomes.append(
                    _build_outcome(
                        event=event,
                        event_index=event_index,
                        match_id=match_id,
                        game_mode=game_mode,
                        account_id=victim_id,
                        opponent_account_id=attacker_id,
                        outcome_type="loss",
                        outcome_reason="dbno_taken",
                        weapon_context=victim_context,
                        opponent_weapon_context=attacker_context,
                        damage_info=event,
                        dbno_id=_optional_text(event.get("dBNOId")),
                        opponent_is_bot=attacker_id in bots,
                        is_friendly_fire=friendly_fire,
                    )
                )

        elif event_type == "LogPlayerKillV2":
            victim = _mapping_value(event.get("victim"))
            killer = _mapping_value(event.get("killer"))
            finisher = _mapping_value(event.get("finisher"))
            victim_id = _account_id(victim)
            killer_id = _account_id(killer)
            finisher_id = _account_id(finisher)
            killer_damage = _mapping_value(event.get("killerDamageInfo"))

            if (
                killer_id
                and victim_id
                and killer_id != victim_id
                and killer_id in tracked
                and event.get("isSuicide") is not True
            ):
                killer_context = _offensive_weapon_context(
                    account_id=killer_id,
                    event_index=event_index,
                    damage_info=killer_damage,
                    current_weapons=current_weapons,
                    weapon_states=weapon_states,
                )
                outcomes.append(
                    _build_outcome(
                        event=event,
                        event_index=event_index,
                        match_id=match_id,
                        game_mode=game_mode,
                        account_id=killer_id,
                        opponent_account_id=victim_id,
                        outcome_type="win",
                        outcome_reason="kill",
                        weapon_context=killer_context,
                        opponent_weapon_context=current_weapons.get(victim_id),
                        damage_info=killer_damage,
                        dbno_id=None,
                        opponent_is_bot=victim_id in bots,
                        is_friendly_fire=_same_team(killer, victim),
                    )
                )

            if victim_id and victim_id in tracked:
                opponent = finisher if finisher_id and finisher_id != victim_id else killer
                opponent_id = _account_id(opponent)
                finish_damage = _mapping_value(event.get("finishDamageInfo"))
                loss_damage = finish_damage if _has_damage_causer(finish_damage) else killer_damage
                opponent_context = None
                if opponent_id:
                    opponent_context = _offensive_weapon_context(
                        account_id=opponent_id,
                        event_index=event_index,
                        damage_info=loss_damage,
                        current_weapons=current_weapons,
                        weapon_states=weapon_states,
                    )
                outcomes.append(
                    _build_outcome(
                        event=event,
                        event_index=event_index,
                        match_id=match_id,
                        game_mode=game_mode,
                        account_id=victim_id,
                        opponent_account_id=opponent_id if opponent_id != victim_id else None,
                        outcome_type="loss",
                        outcome_reason="death",
                        weapon_context=current_weapons.get(victim_id),
                        opponent_weapon_context=opponent_context,
                        damage_info=loss_damage,
                        dbno_id=None,
                        opponent_is_bot=(opponent_id in bots) if opponent_id else None,
                        is_friendly_fire=_same_team(opponent, victim),
                    )
                )

    return outcomes


def _update_weapon_state(
    event: Mapping[str, Any],
    *,
    event_index: int,
    current_weapons: dict[str, WeaponContext],
    weapon_states: dict[tuple[str, str], WeaponContext],
) -> None:
    event_type = _optional_text(event.get("_T"))
    if event_type == "LogPlayerAttack":
        account_id = _account_id(_mapping_value(event.get("attacker")))
        context = _weapon_context_from_item(event.get("weapon"), event_index=event_index, source="attack")
        if account_id and context:
            current_weapons[account_id] = context
            weapon_states[(account_id, context.weapon_code)] = context
        return

    if event_type in {"LogItemEquip", "LogItemUnequip"}:
        account_id = _account_id(_mapping_value(event.get("character")))
        context = _weapon_context_from_item(event.get("item"), event_index=event_index, source="equip")
        if not account_id or not context:
            return
        if event_type == "LogItemEquip":
            weapon_states[(account_id, context.weapon_code)] = context
            existing = current_weapons.get(account_id)
            if existing is None or existing.source != "attack":
                current_weapons[account_id] = context
        elif current_weapons.get(account_id, None) == context or (
            account_id in current_weapons
            and current_weapons[account_id].weapon_code == context.weapon_code
        ):
            current_weapons.pop(account_id, None)
        return

    if event_type not in {"LogItemAttach", "LogItemDetach"}:
        return
    account_id = _account_id(_mapping_value(event.get("character")))
    parent_item = _mapping_value(event.get("parentItem"))
    weapon_code = normalize_weapon_code(parent_item.get("itemId"))
    if not account_id or not weapon_code:
        return

    context = _weapon_context_from_item(parent_item, event_index=event_index, source="item_state")
    previous = weapon_states.get((account_id, weapon_code))
    if context and context.attachment_codes:
        updated = context
    else:
        attachment_codes = set(previous.attachment_codes if previous else ())
        child_item = _mapping_value(event.get("childItem"))
        attachment_code = _optional_text(child_item.get("itemId"))
        if attachment_code:
            if event_type == "LogItemAttach":
                attachment_codes.add(attachment_code)
            else:
                attachment_codes.discard(attachment_code)
        updated = _weapon_context(
            event_index=event_index,
            weapon_code=weapon_code,
            attachment_codes=attachment_codes,
            source="item_state",
        )
    weapon_states[(account_id, weapon_code)] = updated
    if account_id in current_weapons and current_weapons[account_id].weapon_code == weapon_code:
        current_weapons[account_id] = updated


def _offensive_weapon_context(
    *,
    account_id: str,
    event_index: int,
    damage_info: Mapping[str, Any],
    current_weapons: Mapping[str, WeaponContext],
    weapon_states: Mapping[tuple[str, str], WeaponContext],
) -> WeaponContext | None:
    desired_code = normalize_weapon_code(damage_info.get("damageCauserName"))
    current = current_weapons.get(account_id)
    if desired_code and current and current.weapon_code == desired_code:
        return current
    if desired_code:
        known = weapon_states.get((account_id, desired_code))
        if known:
            return known
        return _weapon_context(
            event_index=event_index,
            weapon_code=desired_code,
            attachment_codes=(),
            source="damage_event",
        )
    return current


def _build_outcome(
    *,
    event: Mapping[str, Any],
    event_index: int,
    match_id: str,
    game_mode: str,
    account_id: str,
    opponent_account_id: str | None,
    outcome_type: FightOutcomeType,
    outcome_reason: FightOutcomeReason,
    weapon_context: WeaponContext | None,
    opponent_weapon_context: WeaponContext | None,
    damage_info: Mapping[str, Any],
    dbno_id: str | None,
    opponent_is_bot: bool | None,
    is_friendly_fire: bool,
) -> PlayerFightOutcome:
    damage_reason = _optional_text(damage_info.get("damageReason"))
    return PlayerFightOutcome(
        match_id=match_id,
        account_id=account_id,
        opponent_account_id=opponent_account_id,
        event_index=event_index,
        event_type=_optional_text(event.get("_T")) or "unknown",
        event_at_kst=_parse_event_time(event.get("_D")),
        common_is_game=_common_is_game(event),
        outcome_type=outcome_type,
        outcome_reason=outcome_reason,
        game_mode=game_mode,
        dbno_id=dbno_id,
        weapon_code=weapon_context.weapon_code if weapon_context else None,
        weapon_name_ko=weapon_context.weapon_name_ko if weapon_context else None,
        attachment_codes=weapon_context.attachment_codes if weapon_context else (),
        attachment_names_ko=weapon_context.attachment_names_ko if weapon_context else (),
        weapon_context_source=weapon_context.source if weapon_context else "unknown",
        weapon_context_event_index=weapon_context.event_index if weapon_context else None,
        opponent_weapon_code=(
            opponent_weapon_context.weapon_code
            if opponent_weapon_context
            else normalize_weapon_code(damage_info.get("damageCauserName"))
        ),
        opponent_weapon_name_ko=(
            opponent_weapon_context.weapon_name_ko
            if opponent_weapon_context
            else _weapon_name(normalize_weapon_code(damage_info.get("damageCauserName")))
        ),
        opponent_is_bot=opponent_is_bot,
        is_friendly_fire=is_friendly_fire,
        damage_type_category=_optional_text(damage_info.get("damageTypeCategory")),
        damage_reason=damage_reason,
        is_headshot=damage_reason == "HeadShot",
        distance_m=_distance_m(damage_info, event),
    )


def _weapon_context_from_item(value: Any, *, event_index: int, source: str) -> WeaponContext | None:
    item = _mapping_value(value)
    weapon_code = normalize_weapon_code(item.get("itemId"))
    category = _optional_text(item.get("category"))
    if not weapon_code:
        return None
    if category is not None and category.lower() != "weapon":
        return None
    if not weapon_code.startswith(("Weap", "Proj")):
        return None
    raw_attachments = item.get("attachedItems")
    attachments = (
        sorted({str(code) for code in raw_attachments if isinstance(code, str) and code})
        if isinstance(raw_attachments, list)
        else []
    )
    return _weapon_context(
        event_index=event_index,
        weapon_code=weapon_code,
        attachment_codes=attachments,
        source=source,
    )


def _weapon_context(
    *,
    event_index: int,
    weapon_code: str,
    attachment_codes: Iterable[str],
    source: str,
) -> WeaponContext:
    codes = tuple(sorted(set(attachment_codes)))
    return WeaponContext(
        event_index=event_index,
        weapon_code=weapon_code,
        weapon_name_ko=_weapon_name(weapon_code) or weapon_code,
        attachment_codes=codes,
        attachment_names_ko=tuple(translate_code(code, "item") for code in codes),
        source=source,
    )


def _weapon_name(weapon_code: str | None) -> str | None:
    return translate_code(weapon_code, "damage_causer") if weapon_code else None


def _same_team(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_team = _optional_int(left.get("teamId"))
    right_team = _optional_int(right.get("teamId"))
    return left_team is not None and right_team is not None and left_team == right_team


def _distance_m(damage_info: Mapping[str, Any], event: Mapping[str, Any]) -> float | None:
    distance = _optional_float(damage_info.get("distance"))
    if distance is None:
        distance = _optional_float(event.get("distance"))
    return distance / 100.0 if distance is not None else None


def _has_damage_causer(value: Mapping[str, Any]) -> bool:
    return normalize_weapon_code(value.get("damageCauserName")) is not None


def _account_id(value: Mapping[str, Any]) -> str | None:
    return _optional_text(value.get("accountId"))


def _mapping_value(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _is_in_game_event(event: Mapping[str, Any]) -> bool:
    common = _mapping_value(event.get("common"))
    is_game = common.get("isGame")
    return not isinstance(is_game, int | float) or is_game > 0


def _common_is_game(event: Mapping[str, Any]) -> float | None:
    return _optional_float(_mapping_value(event.get("common")).get("isGame"))


def _parse_event_time(value: Any) -> datetime | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return to_kst(parsed)


def _required_text(value: Any, label: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise FightOutcomeProcessingError(f"{label} is required.")
    return text


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


def _json_list(values: Iterable[str]) -> str:
    return json.dumps(list(values), ensure_ascii=False, separators=(",", ":"))


def _mysql_datetime(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=None) if value else None
