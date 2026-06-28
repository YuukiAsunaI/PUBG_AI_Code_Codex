from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping


BODY_PART_BY_DAMAGE_REASON = {
    "HeadShot": "head",
    "TorsoShot": "torso",
    "PelvisShot": "pelvis",
    "ArmShot": "arm",
    "LegShot": "leg",
    "NonSpecific": "non_specific",
    "None": "none",
}


@dataclass
class WeaponCombatStats:
    match_id: str
    account_id: str
    weapon_code: str
    shots_fired: int = 0
    shots_hit: int = 0
    hits_taken: int = 0
    damage_dealt: float = 0.0
    damage_taken: float = 0.0
    kills: int = 0
    assists: int = 0
    deaths: int = 0
    dbnos: int = 0
    dbnos_taken: int = 0
    finishes: int = 0
    finishes_taken: int = 0
    headshot_hits: int = 0
    headshot_hits_taken: int = 0
    headshot_kills: int = 0
    headshot_deaths: int = 0
    headshot_dbnos: int = 0
    headshot_dbnos_taken: int = 0
    headshot_finishes: int = 0
    headshot_finishes_taken: int = 0
    hit_parts: dict[str, int] = field(default_factory=dict)
    taken_hit_parts: dict[str, int] = field(default_factory=dict)

    @property
    def accuracy(self) -> float | None:
        if self.shots_fired <= 0:
            return None
        return self.shots_hit / self.shots_fired

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["accuracy"] = self.accuracy
        return record


@dataclass
class PlayerMatchCombatSummary:
    match_id: str
    account_id: str
    shots_fired: int = 0
    shots_hit: int = 0
    hits_taken: int = 0
    damage_dealt: float = 0.0
    damage_taken: float = 0.0
    kills: int = 0
    assists: int = 0
    deaths: int = 0
    dbnos_caused: int = 0
    dbnos_taken: int = 0
    finishes: int = 0
    finishes_taken: int = 0
    headshot_hits: int = 0
    headshot_hits_taken: int = 0
    headshot_kills: int = 0
    headshot_deaths: int = 0
    headshot_dbnos_caused: int = 0
    headshot_dbnos_taken: int = 0
    headshot_finishes: int = 0
    headshot_finishes_taken: int = 0
    hit_parts: dict[str, int] = field(default_factory=dict)
    taken_hit_parts: dict[str, int] = field(default_factory=dict)

    @property
    def accuracy(self) -> float | None:
        if self.shots_fired <= 0:
            return None
        return self.shots_hit / self.shots_fired

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["accuracy"] = self.accuracy
        return record


def summarize_weapon_combat_stats(
    events: Iterable[Mapping[str, Any]],
    match_id: str,
    tracked_account_ids: Iterable[str] | None = None,
    *,
    include_lobby: bool = False,
) -> list[WeaponCombatStats]:
    tracked = set(tracked_account_ids) if tracked_account_ids is not None else None
    stats_by_key: dict[tuple[str, str], WeaponCombatStats] = {}
    last_gun_damage_weapon_by_pair: dict[tuple[str, str], str] = {}

    def get_stats(account_id: str | None, weapon_code: str | None) -> WeaponCombatStats | None:
        if not account_id or not weapon_code:
            return None
        if tracked is not None and account_id not in tracked:
            return None
        key = (account_id, weapon_code)
        if key not in stats_by_key:
            stats_by_key[key] = WeaponCombatStats(
                match_id=match_id,
                account_id=account_id,
                weapon_code=weapon_code,
            )
        return stats_by_key[key]

    for event in events:
        if not include_lobby and not _is_in_game_event(event):
            continue

        event_type = event.get("_T")
        if event_type == "LogWeaponFireCount":
            stats = get_stats(
                _character_account_id(event.get("character")),
                normalize_weapon_code(event.get("weaponId")),
            )
            if stats is not None:
                stats.shots_fired += _int_or_zero(event.get("fireCount"))

        elif event_type == "LogPlayerTakeDamage":
            if event.get("damageTypeCategory") != "Damage_Gun":
                continue

            weapon_code = normalize_weapon_code(event.get("damageCauserName"))
            body_part = body_part_from_damage_reason(event.get("damageReason"))
            damage = _float_or_zero(event.get("damage"))
            attacker_account_id = _character_account_id(event.get("attacker"))
            victim_account_id = _character_account_id(event.get("victim"))

            if attacker_account_id and victim_account_id and weapon_code:
                last_gun_damage_weapon_by_pair[(attacker_account_id, victim_account_id)] = weapon_code

            attacker_stats = get_stats(attacker_account_id, weapon_code)
            if attacker_stats is not None:
                attacker_stats.shots_hit += 1
                attacker_stats.damage_dealt += damage
                _increment(attacker_stats.hit_parts, body_part)
                if body_part == "head":
                    attacker_stats.headshot_hits += 1

            victim_stats = get_stats(victim_account_id, weapon_code)
            if victim_stats is not None:
                victim_stats.hits_taken += 1
                victim_stats.damage_taken += damage
                _increment(victim_stats.taken_hit_parts, body_part)
                if body_part == "head":
                    victim_stats.headshot_hits_taken += 1

        elif event_type == "LogPlayerMakeGroggy":
            if event.get("damageTypeCategory") != "Damage_Gun":
                continue

            weapon_code = normalize_weapon_code(event.get("damageCauserName"))
            body_part = body_part_from_damage_reason(event.get("damageReason"))

            attacker_stats = get_stats(_character_account_id(event.get("attacker")), weapon_code)
            if attacker_stats is not None:
                attacker_stats.dbnos += 1
                if body_part == "head":
                    attacker_stats.headshot_dbnos += 1

            victim_stats = get_stats(_character_account_id(event.get("victim")), weapon_code)
            if victim_stats is not None:
                victim_stats.dbnos_taken += 1
                if body_part == "head":
                    victim_stats.headshot_dbnos_taken += 1

        elif event_type == "LogPlayerKillV2":
            _apply_kill_event(event, get_stats)
            _apply_assist_event(event, get_stats, last_gun_damage_weapon_by_pair)

    return sorted(
        stats_by_key.values(),
        key=lambda stats: (stats.account_id, stats.weapon_code),
    )


def summarize_player_match_combat(
    events: Iterable[Mapping[str, Any]],
    match_id: str,
    tracked_account_ids: Iterable[str] | None = None,
    *,
    include_lobby: bool = False,
) -> list[PlayerMatchCombatSummary]:
    event_list = list(events)
    tracked = set(tracked_account_ids) if tracked_account_ids is not None else None
    summaries_by_account: dict[str, PlayerMatchCombatSummary] = {}

    def get_summary(account_id: str | None) -> PlayerMatchCombatSummary | None:
        if not account_id:
            return None
        if tracked is not None and account_id not in tracked:
            return None
        if account_id not in summaries_by_account:
            summaries_by_account[account_id] = PlayerMatchCombatSummary(
                match_id=match_id,
                account_id=account_id,
            )
        return summaries_by_account[account_id]

    weapon_stats = summarize_weapon_combat_stats(
        event_list,
        match_id=match_id,
        tracked_account_ids=tracked,
        include_lobby=include_lobby,
    )
    for stats in weapon_stats:
        summary = get_summary(stats.account_id)
        if summary is not None:
            _add_weapon_stats_to_summary(summary, stats)

    for event in event_list:
        if not include_lobby and not _is_in_game_event(event):
            continue
        if event.get("_T") != "LogPlayerKillV2":
            continue

        for account_id in _assist_account_ids(event):
            summary = get_summary(account_id)
            if summary is not None:
                summary.assists += 1

    return sorted(summaries_by_account.values(), key=lambda summary: summary.account_id)


def normalize_weapon_code(value: Any) -> str | None:
    if not isinstance(value, str):
        return None

    code = value.strip()
    if not code or code == "None":
        return None

    if code.startswith("Item_Weapon_") and code.endswith("_C"):
        weapon_name = code.removeprefix("Item_Weapon_").removesuffix("_C")
        return f"Weap{weapon_name}_C"

    if code.startswith("Item_Projectile_") and code.endswith("_C"):
        projectile_name = code.removeprefix("Item_Projectile_").removesuffix("_C")
        return f"Proj{projectile_name}_C"

    return _strip_weapon_instance_suffix(code)


def body_part_from_damage_reason(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return "unknown"
    return BODY_PART_BY_DAMAGE_REASON.get(value, value)


def _apply_kill_event(
    event: Mapping[str, Any],
    get_stats: Any,
) -> None:
    killer_info = event.get("killerDamageInfo")
    if isinstance(killer_info, Mapping) and _is_gun_damage(killer_info):
        weapon_code = normalize_weapon_code(killer_info.get("damageCauserName"))
        body_part = body_part_from_damage_reason(killer_info.get("damageReason"))

        killer_stats = get_stats(_character_account_id(event.get("killer")), weapon_code)
        if killer_stats is not None and not event.get("isSuicide"):
            killer_stats.kills += 1
            if body_part == "head":
                killer_stats.headshot_kills += 1

        victim_stats = get_stats(_character_account_id(event.get("victim")), weapon_code)
        if victim_stats is not None:
            victim_stats.deaths += 1
            if body_part == "head":
                victim_stats.headshot_deaths += 1

    finish_info = event.get("finishDamageInfo")
    if isinstance(finish_info, Mapping) and _is_gun_damage(finish_info):
        weapon_code = normalize_weapon_code(finish_info.get("damageCauserName"))
        body_part = body_part_from_damage_reason(finish_info.get("damageReason"))

        finisher_stats = get_stats(_character_account_id(event.get("finisher")), weapon_code)
        if finisher_stats is not None and not event.get("isSuicide"):
            finisher_stats.finishes += 1
            if body_part == "head":
                finisher_stats.headshot_finishes += 1

        victim_stats = get_stats(_character_account_id(event.get("victim")), weapon_code)
        if victim_stats is not None:
            victim_stats.finishes_taken += 1
            if body_part == "head":
                victim_stats.headshot_finishes_taken += 1


def _apply_assist_event(
    event: Mapping[str, Any],
    get_stats: Any,
    last_gun_damage_weapon_by_pair: Mapping[tuple[str, str], str],
) -> None:
    if event.get("isSuicide"):
        return

    victim_account_id = _character_account_id(event.get("victim"))
    if not victim_account_id:
        return

    for assistant_account_id in _assist_account_ids(event):
        weapon_code = last_gun_damage_weapon_by_pair.get((assistant_account_id, victim_account_id))
        assistant_stats = get_stats(assistant_account_id, weapon_code)
        if assistant_stats is not None:
            assistant_stats.assists += 1


def _add_weapon_stats_to_summary(
    summary: PlayerMatchCombatSummary,
    stats: WeaponCombatStats,
) -> None:
    summary.shots_fired += stats.shots_fired
    summary.shots_hit += stats.shots_hit
    summary.hits_taken += stats.hits_taken
    summary.damage_dealt += stats.damage_dealt
    summary.damage_taken += stats.damage_taken
    summary.kills += stats.kills
    summary.deaths += stats.deaths
    summary.dbnos_caused += stats.dbnos
    summary.dbnos_taken += stats.dbnos_taken
    summary.finishes += stats.finishes
    summary.finishes_taken += stats.finishes_taken
    summary.headshot_hits += stats.headshot_hits
    summary.headshot_hits_taken += stats.headshot_hits_taken
    summary.headshot_kills += stats.headshot_kills
    summary.headshot_deaths += stats.headshot_deaths
    summary.headshot_dbnos_caused += stats.headshot_dbnos
    summary.headshot_dbnos_taken += stats.headshot_dbnos_taken
    summary.headshot_finishes += stats.headshot_finishes
    summary.headshot_finishes_taken += stats.headshot_finishes_taken
    _merge_counts(summary.hit_parts, stats.hit_parts)
    _merge_counts(summary.taken_hit_parts, stats.taken_hit_parts)


def _assist_account_ids(event: Mapping[str, Any]) -> list[str]:
    assists = event.get("assists_AccountId")
    if not isinstance(assists, (list, tuple, set)):
        return []

    account_ids = []
    seen = set()
    for account_id in assists:
        if not isinstance(account_id, str) or not account_id or account_id in seen:
            continue
        seen.add(account_id)
        account_ids.append(account_id)
    return account_ids


def _character_account_id(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    account_id = value.get("accountId")
    if isinstance(account_id, str) and account_id:
        return account_id
    return None


def _strip_weapon_instance_suffix(code: str) -> str:
    parts = code.rsplit("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return code


def _is_in_game_event(event: Mapping[str, Any]) -> bool:
    common = event.get("common")
    if not isinstance(common, Mapping):
        return True
    is_game = common.get("isGame")
    if isinstance(is_game, int | float):
        return is_game > 0
    return True


def _is_gun_damage(value: Mapping[str, Any]) -> bool:
    return value.get("damageTypeCategory") == "Damage_Gun"


def _int_or_zero(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def _float_or_zero(value: Any) -> float:
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def _increment(values: dict[str, int], key: str) -> None:
    values[key] = values.get(key, 0) + 1


def _merge_counts(destination: dict[str, int], source: Mapping[str, int]) -> None:
    for key, value in source.items():
        destination[key] = destination.get(key, 0) + value
