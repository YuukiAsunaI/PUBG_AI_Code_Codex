from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping
import json

from pubg_ai.code_translator import translate_code
from pubg_ai.player_registry import RegisteredPlayer
from pubg_ai.player_scope import PLAYER_GUILD_SCOPE_CONDITION


@dataclass(frozen=True)
class FightOutcomeTotals:
    fight_count: int
    wins: int
    losses: int
    fight_win_rate: float
    kill_wins: int
    dbno_wins: int
    death_losses: int
    dbno_losses: int
    headshot_wins: int
    human_opponent_fights: int
    human_opponent_wins: int
    bot_opponent_fights: int
    bot_opponent_wins: int
    environmental_or_unknown_opponent_losses: int
    unknown_weapon_contexts: int
    excluded_non_firearm_contexts: int
    excluded_friendly_fire: int

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FightWeaponStats:
    weapon_code: str
    weapon_name: str
    fight_count: int
    wins: int
    losses: int
    fight_win_rate: float
    kill_wins: int
    dbno_wins: int
    death_losses: int
    dbno_losses: int

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FightLoadoutStats:
    weapon_code: str
    weapon_name: str
    attachment_codes: tuple[str, ...]
    attachment_names: tuple[str, ...]
    fight_count: int
    wins: int
    losses: int
    fight_win_rate: float

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["attachment_codes"] = list(self.attachment_codes)
        record["attachment_names"] = list(self.attachment_names)
        return record


@dataclass(frozen=True)
class RecentFightOutcome:
    match_id: str
    event_index: int
    event_at_kst: datetime | None
    map_name: str | None
    game_mode: str | None
    outcome_type: str
    outcome_reason: str
    opponent_account_id: str | None
    opponent_is_bot: bool | None
    weapon_code: str | None
    weapon_name: str | None
    attachment_names: tuple[str, ...]
    weapon_context_source: str
    opponent_weapon_name: str | None
    is_headshot: bool
    distance_m: float | None

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["event_at_kst"] = self.event_at_kst.isoformat() if self.event_at_kst else None
        record["attachment_names"] = list(self.attachment_names)
        return record


@dataclass(frozen=True)
class PlayerFightOutcomeReport:
    player: RegisteredPlayer
    totals: FightOutcomeTotals
    weapons: list[FightWeaponStats]
    loadouts: list[FightLoadoutStats]
    recent_outcomes: list[RecentFightOutcome]

    def to_record(self) -> dict[str, Any]:
        return {
            "player": self.player.to_record(),
            "totals": self.totals.to_record(),
            "weapons": [item.to_record() for item in self.weapons],
            "loadouts": [item.to_record() for item in self.loadouts],
            "recent_outcomes": [item.to_record() for item in self.recent_outcomes],
        }


class FightOutcomeStatsService:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def get_report(
        self,
        *,
        shard: str,
        account_id: str | None = None,
        name: str | None = None,
        guild_id: str | None = None,
        global_scope: bool = False,
        weapon_limit: int = 10,
        loadout_limit: int = 10,
        recent_limit: int = 20,
        include_friendly_fire: bool = False,
        include_bots: bool = True,
    ) -> PlayerFightOutcomeReport | None:
        player = self._get_player(
            shard=shard,
            account_id=account_id,
            name=name,
            guild_id=guild_id,
            global_scope=global_scope,
        )
        if player is None:
            return None

        rows = self._get_rows(player)
        summary = summarize_fight_outcomes(
            rows,
            include_friendly_fire=include_friendly_fire,
            include_bots=include_bots,
            weapon_limit=weapon_limit,
            loadout_limit=loadout_limit,
            recent_limit=recent_limit,
        )
        return PlayerFightOutcomeReport(
            player=player,
            totals=summary.totals,
            weapons=summary.weapons,
            loadouts=summary.loadouts,
            recent_outcomes=summary.recent_outcomes,
        )

    def _get_player(
        self,
        *,
        shard: str,
        account_id: str | None,
        name: str | None,
        guild_id: str | None,
        global_scope: bool,
    ) -> RegisteredPlayer | None:
        normalized_shard = _required_text(shard, "shard").lower()
        conditions = ["shard = %s"]
        params: list[Any] = [normalized_shard]
        if account_id:
            conditions.append("account_id = %s")
            params.append(account_id)
        elif name:
            conditions.append("current_name = %s")
            params.append(name)
        else:
            raise ValueError("account_id or name is required.")

        if not global_scope:
            if not guild_id:
                return None
            conditions.append(PLAYER_GUILD_SCOPE_CONDITION)
            params.append(guild_id)

        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, account_id, shard, current_name, active, public_profile, "
                "registered_by_discord_user_id, registered_guild_id, registered_channel_id "
                "FROM registered_players WHERE "
                + " AND ".join(conditions)
                + " ORDER BY active DESC, updated_at_kst DESC LIMIT 1",
                params,
            )
            row = cursor.fetchone()
        return _player_from_row(row) if row else None

    def _get_rows(self, player: RegisteredPlayer) -> list[dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    outcomes.match_id,
                    outcomes.event_index,
                    outcomes.event_at_kst,
                    outcomes.outcome_type,
                    outcomes.outcome_reason,
                    outcomes.opponent_account_id,
                    outcomes.opponent_is_bot,
                    outcomes.is_friendly_fire,
                    outcomes.weapon_code,
                    outcomes.weapon_name_ko,
                    outcomes.attachment_codes,
                    outcomes.attachment_names_ko,
                    outcomes.weapon_context_source,
                    outcomes.opponent_weapon_code,
                    outcomes.opponent_weapon_name_ko,
                    outcomes.is_headshot,
                    outcomes.distance_m,
                    matches.map_name,
                    matches.game_mode,
                    matches.created_at_kst
                FROM player_fight_outcomes outcomes
                INNER JOIN analysis_matches AS matches
                    ON matches.match_id = outcomes.match_id
                WHERE outcomes.account_id = %s
                  AND matches.shard = %s
                ORDER BY
                    COALESCE(outcomes.event_at_kst, matches.created_at_kst) DESC,
                    outcomes.match_id DESC,
                    outcomes.event_index DESC
                """,
                (player.account_id, player.shard),
            )
            return list(cursor.fetchall())


@dataclass(frozen=True)
class FightOutcomeSummary:
    totals: FightOutcomeTotals
    weapons: list[FightWeaponStats]
    loadouts: list[FightLoadoutStats]
    recent_outcomes: list[RecentFightOutcome]


def summarize_fight_outcomes(
    rows: Iterable[Mapping[str, Any]],
    *,
    include_friendly_fire: bool = False,
    include_bots: bool = True,
    weapon_limit: int = 10,
    loadout_limit: int = 10,
    recent_limit: int = 20,
) -> FightOutcomeSummary:
    all_rows = list(rows)
    excluded_friendly_fire = sum(bool(row.get("is_friendly_fire")) for row in all_rows)
    eligible = [
        row
        for row in all_rows
        if (include_friendly_fire or not bool(row.get("is_friendly_fire")))
        and (include_bots or _optional_bool(row.get("opponent_is_bot")) is not True)
    ]

    wins = sum(row.get("outcome_type") == "win" for row in eligible)
    losses = sum(row.get("outcome_type") == "loss" for row in eligible)
    totals = FightOutcomeTotals(
        fight_count=len(eligible),
        wins=wins,
        losses=losses,
        fight_win_rate=_safe_divide(wins, wins + losses),
        kill_wins=_reason_count(eligible, "kill"),
        dbno_wins=_reason_count(eligible, "dbno_caused"),
        death_losses=_reason_count(eligible, "death"),
        dbno_losses=_reason_count(eligible, "dbno_taken"),
        headshot_wins=sum(
            row.get("outcome_type") == "win" and bool(row.get("is_headshot"))
            for row in eligible
        ),
        human_opponent_fights=sum(
            _optional_bool(row.get("opponent_is_bot")) is False for row in eligible
        ),
        human_opponent_wins=sum(
            _optional_bool(row.get("opponent_is_bot")) is False
            and row.get("outcome_type") == "win"
            for row in eligible
        ),
        bot_opponent_fights=sum(
            _optional_bool(row.get("opponent_is_bot")) is True for row in eligible
        ),
        bot_opponent_wins=sum(
            _optional_bool(row.get("opponent_is_bot")) is True
            and row.get("outcome_type") == "win"
            for row in eligible
        ),
        environmental_or_unknown_opponent_losses=sum(
            row.get("opponent_account_id") is None and row.get("outcome_type") == "loss"
            for row in eligible
        ),
        unknown_weapon_contexts=sum(not _optional_text(row.get("weapon_code")) for row in eligible),
        excluded_non_firearm_contexts=sum(
            bool(weapon_code := _optional_text(row.get("weapon_code")))
            and not _is_recommendable_firearm_context(weapon_code)
            for row in eligible
        ),
        excluded_friendly_fire=excluded_friendly_fire if not include_friendly_fire else 0,
    )

    weapon_groups: dict[str, list[Mapping[str, Any]]] = {}
    loadout_groups: dict[tuple[str, tuple[str, ...]], list[Mapping[str, Any]]] = {}
    for row in eligible:
        weapon_code = _optional_text(row.get("weapon_code"))
        if not weapon_code or not _is_recommendable_firearm_context(weapon_code):
            continue
        weapon_groups.setdefault(weapon_code, []).append(row)
        attachment_codes = tuple(sorted(_json_string_list(row.get("attachment_codes"))))
        loadout_groups.setdefault((weapon_code, attachment_codes), []).append(row)

    weapon_rows = [
        _weapon_stats(weapon_code, grouped)
        for weapon_code, grouped in weapon_groups.items()
    ]
    weapon_rows.sort(
        key=lambda item: (-item.fight_count, -item.fight_win_rate, -item.wins, item.weapon_code)
    )

    loadout_rows = [
        _loadout_stats(weapon_code, attachment_codes, grouped)
        for (weapon_code, attachment_codes), grouped in loadout_groups.items()
    ]
    loadout_rows.sort(
        key=lambda item: (-item.fight_count, -item.fight_win_rate, -item.wins, item.weapon_code)
    )

    recent_rows = [_recent_outcome(row) for row in eligible[: max(1, min(int(recent_limit), 100))]]
    return FightOutcomeSummary(
        totals=totals,
        weapons=weapon_rows[: max(1, min(int(weapon_limit), 100))],
        loadouts=loadout_rows[: max(1, min(int(loadout_limit), 100))],
        recent_outcomes=recent_rows,
    )


def _weapon_stats(weapon_code: str, rows: list[Mapping[str, Any]]) -> FightWeaponStats:
    wins = sum(row.get("outcome_type") == "win" for row in rows)
    losses = sum(row.get("outcome_type") == "loss" for row in rows)
    name = next((_optional_text(row.get("weapon_name_ko")) for row in rows if row.get("weapon_name_ko")), None)
    return FightWeaponStats(
        weapon_code=weapon_code,
        weapon_name=_translated_display_name(name, weapon_code, "damage_causer"),
        fight_count=len(rows),
        wins=wins,
        losses=losses,
        fight_win_rate=_safe_divide(wins, wins + losses),
        kill_wins=_reason_count(rows, "kill"),
        dbno_wins=_reason_count(rows, "dbno_caused"),
        death_losses=_reason_count(rows, "death"),
        dbno_losses=_reason_count(rows, "dbno_taken"),
    )


def _loadout_stats(
    weapon_code: str,
    attachment_codes: tuple[str, ...],
    rows: list[Mapping[str, Any]],
) -> FightLoadoutStats:
    wins = sum(row.get("outcome_type") == "win" for row in rows)
    losses = sum(row.get("outcome_type") == "loss" for row in rows)
    name = next((_optional_text(row.get("weapon_name_ko")) for row in rows if row.get("weapon_name_ko")), None)
    names_by_code: dict[str, str] = {}
    for row in rows:
        codes = _json_string_list(row.get("attachment_codes"))
        names = _json_string_list(row.get("attachment_names_ko"))
        names_by_code.update({code: names[index] for index, code in enumerate(codes) if index < len(names)})
    return FightLoadoutStats(
        weapon_code=weapon_code,
        weapon_name=_translated_display_name(name, weapon_code, "damage_causer"),
        attachment_codes=attachment_codes,
        attachment_names=tuple(
            _translated_display_name(names_by_code.get(code), code, "item")
            for code in attachment_codes
        ),
        fight_count=len(rows),
        wins=wins,
        losses=losses,
        fight_win_rate=_safe_divide(wins, wins + losses),
    )


def _recent_outcome(row: Mapping[str, Any]) -> RecentFightOutcome:
    weapon_code = _optional_text(row.get("weapon_code"))
    opponent_weapon_code = _optional_text(row.get("opponent_weapon_code"))
    attachment_codes = _json_string_list(row.get("attachment_codes"))
    stored_attachment_names = _json_string_list(row.get("attachment_names_ko"))
    attachment_names = tuple(
        _translated_display_name(
            stored_attachment_names[index] if index < len(stored_attachment_names) else None,
            code,
            "item",
        )
        for index, code in enumerate(attachment_codes)
    )
    return RecentFightOutcome(
        match_id=str(row.get("match_id") or ""),
        event_index=_int(row.get("event_index")),
        event_at_kst=row.get("event_at_kst") if isinstance(row.get("event_at_kst"), datetime) else None,
        map_name=_optional_text(row.get("map_name")),
        game_mode=_optional_text(row.get("game_mode")),
        outcome_type=str(row.get("outcome_type") or ""),
        outcome_reason=str(row.get("outcome_reason") or ""),
        opponent_account_id=_optional_text(row.get("opponent_account_id")),
        opponent_is_bot=_optional_bool(row.get("opponent_is_bot")),
        weapon_code=weapon_code,
        weapon_name=(
            _translated_display_name(row.get("weapon_name_ko"), weapon_code, "damage_causer")
            if weapon_code
            else None
        ),
        attachment_names=attachment_names,
        weapon_context_source=str(row.get("weapon_context_source") or "unknown"),
        opponent_weapon_name=(
            _translated_display_name(
                row.get("opponent_weapon_name_ko"),
                opponent_weapon_code,
                "damage_causer",
            )
            if opponent_weapon_code
            else None
        ),
        is_headshot=bool(row.get("is_headshot")),
        distance_m=_optional_float(row.get("distance_m")),
    )


def _is_recommendable_firearm_context(weapon_code: str) -> bool:
    return weapon_code.startswith("Weap") and weapon_code not in _NON_FIREARM_CONTEXT_CODES


def _translated_display_name(stored_name: Any, code: str, category: str) -> str:
    stored = _optional_text(stored_name)
    if not stored or stored == code or stored.startswith(("Item_", "Weap")):
        return translate_code(code, category)
    return stored


def _reason_count(rows: Iterable[Mapping[str, Any]], reason: str) -> int:
    return sum(row.get("outcome_reason") == reason for row in rows)


def _player_from_row(row: Mapping[str, Any]) -> RegisteredPlayer:
    return RegisteredPlayer(
        id=int(row["id"]),
        account_id=str(row["account_id"]),
        shard=str(row["shard"]),
        current_name=str(row["current_name"]),
        active=bool(row["active"]),
        public_profile=bool(row["public_profile"]),
        registered_by_discord_user_id=row.get("registered_by_discord_user_id"),
        registered_guild_id=row.get("registered_guild_id"),
        registered_channel_id=row.get("registered_channel_id"),
    )


def _json_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item]


def _required_text(value: str, label: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError(f"{label} is required.")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _int(value: Any) -> int:
    return int(value or 0)


def _safe_divide(numerator: int | float, denominator: int | float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


_NON_FIREARM_CONTEXT_CODES = frozenset(
    {
        "WeapBluezoneGrenade_C",
        "WeapC4_C",
        "WeapCowbar_C",
        "WeapDecoyGrenade_C",
        "WeapFlareGun_C",
        "WeapFlashBang_C",
        "WeapM79_C",
        "WeapMachete_C",
        "WeapMolotov_C",
        "WeapMortar_C",
        "WeapPan_C",
        "WeapSickle_C",
        "WeapSmokeBomb_C",
        "WeapSpikeTrap_C",
        "WeapSpotter_Scope_C",
        "WeapStickyGrenade_C",
        "WeapStunGun_C",
        "WeapTraumaBag_C",
    }
)
