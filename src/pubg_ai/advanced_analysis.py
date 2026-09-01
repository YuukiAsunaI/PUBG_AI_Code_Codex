from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from math import hypot
from statistics import mean
from typing import Any, Iterable, Mapping

from pubg_ai.time_utils import to_kst


PARSER_VERSION = "advanced-analysis-v1"
FIGHT_GAP_SECONDS = 15.0
TRADE_WINDOW_SECONDS = 10.0
POSITION_FRESHNESS_SECONDS = 15.0
ISOLATION_DISTANCE_M = 150.0
CLOSE_SUPPORT_DISTANCE_M = 50.0


@dataclass(frozen=True)
class FightEpisode:
    match_id: str
    account_id: str
    episode_index: int
    start_event_index: int
    end_event_index: int
    started_at_kst: datetime | None
    ended_at_kst: datetime | None
    duration_seconds: float
    phase_number: int | None
    outcome: str
    opening_actor: str
    first_hit_actor: str
    primary_opponent_account_id: str | None
    primary_opponent_team_id: int | None
    opponent_count: int
    opponent_team_count: int
    shots_fired: int
    shots_hit: int
    damage_dealt: float
    damage_taken: float
    dbnos_caused: int
    dbnos_taken: int
    kills: int
    deaths: int
    assists: int
    revives_given: int
    revives_received: int
    trade_opportunities: int
    trade_successes: int
    is_third_party: bool
    weapon_codes: tuple[str, ...]
    opponent_weapon_codes: tuple[str, ...]
    min_distance_m: float | None
    avg_distance_m: float | None
    max_distance_m: float | None
    summary_reason: str

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["started_at_kst"] = _iso(self.started_at_kst)
        record["ended_at_kst"] = _iso(self.ended_at_kst)
        record["weapon_codes"] = list(self.weapon_codes)
        record["opponent_weapon_codes"] = list(self.opponent_weapon_codes)
        record["parser_version"] = PARSER_VERSION
        return record


@dataclass(frozen=True)
class ZonePhaseSummary:
    match_id: str
    account_id: str
    phase_number: int
    sample_count: int
    phase_started_elapsed_seconds: float | None
    phase_ended_elapsed_seconds: float | None
    first_inside_elapsed_seconds: float | None
    late_entry_seconds: float | None
    outside_safe_zone_seconds: float
    blue_zone_exposure_seconds: float
    max_outside_distance_m: float
    avg_center_distance_ratio: float | None
    edge_position_seconds: float
    center_position_seconds: float
    rotation_distance_m: float
    foot_distance_m: float
    vehicle_distance_m: float
    vehicle_seconds: float
    dbnos_taken: int
    deaths: int

    def to_record(self) -> dict[str, Any]:
        return {**asdict(self), "parser_version": PARSER_VERSION}


@dataclass(frozen=True)
class TeamCoordinationSummary:
    match_id: str
    account_id: str
    sample_count: int
    avg_nearest_teammate_distance_m: float | None
    max_nearest_teammate_distance_m: float | None
    avg_visible_teammates: float | None
    isolated_seconds: float
    close_support_seconds: float
    regroup_count: int
    trade_opportunities: int
    trade_successes: int
    revives_given: int
    revives_received: int
    avg_revive_latency_seconds: float | None
    team_dbnos_taken: int
    team_deaths: int

    def to_record(self) -> dict[str, Any]:
        return {**asdict(self), "parser_version": PARSER_VERSION}


@dataclass(frozen=True)
class LootReadinessSummary:
    match_id: str
    account_id: str
    landed_at_kst: datetime | None
    first_fight_at_kst: datetime | None
    first_primary_weapon_code: str | None
    second_primary_weapon_code: str | None
    seconds_to_first_primary_weapon: float | None
    seconds_to_second_primary_weapon: float | None
    seconds_to_vest: float | None
    seconds_to_helmet: float | None
    seconds_to_heal: float | None
    seconds_to_throwable: float | None
    seconds_to_scope: float | None
    seconds_to_first_fight: float | None
    ready_before_first_fight: bool | None
    pickup_events: int
    ground_pickups: int
    loot_box_pickups: int
    care_package_pickups: int
    vehicle_trunk_pickups: int
    readiness_score: float
    early_inventory: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["landed_at_kst"] = _iso(self.landed_at_kst)
        record["first_fight_at_kst"] = _iso(self.first_fight_at_kst)
        record["early_inventory"] = list(self.early_inventory)
        record["parser_version"] = PARSER_VERSION
        return record


@dataclass(frozen=True)
class AdvancedAnalysisBundle:
    fight_episodes: tuple[FightEpisode, ...]
    zone_phases: tuple[ZonePhaseSummary, ...]
    team_coordination: tuple[TeamCoordinationSummary, ...]
    loot_readiness: tuple[LootReadinessSummary, ...]

    def output_count(self, account_id: str) -> int:
        return sum(
            1
            for row in (
                *self.fight_episodes,
                *self.zone_phases,
                *self.team_coordination,
                *self.loot_readiness,
            )
            if row.account_id == account_id
        )


@dataclass(frozen=True)
class ParticipantContext:
    account_id: str
    team_id: int | None
    is_bot: bool = False


@dataclass(frozen=True)
class _FightObservation:
    event_index: int
    event_at_kst: datetime | None
    seconds: float
    phase_number: int | None
    kind: str
    actor_id: str | None
    target_id: str | None
    actor_team_id: int | None
    target_team_id: int | None
    damage: float
    distance_m: float | None
    weapon_code: str | None


@dataclass(frozen=True)
class _Takedown:
    event_index: int
    seconds: float
    attacker_id: str | None
    victim_id: str | None
    kind: str


@dataclass(frozen=True)
class _Position:
    event_index: int
    account_id: str
    team_id: int | None
    event_at_kst: datetime | None
    elapsed_seconds: float | None
    seconds: float
    phase_number: int | None
    x: float
    y: float
    is_in_vehicle: bool
    is_in_blue_zone: bool


@dataclass(frozen=True)
class _GameState:
    event_index: int
    elapsed_seconds: float | None
    phase_number: int | None
    safety_x: float | None
    safety_y: float | None
    safety_radius: float | None


def build_advanced_analysis(
    events: Iterable[Mapping[str, Any]],
    *,
    match_id: str,
    tracked_account_ids: set[str],
    participants: Mapping[str, ParticipantContext],
) -> AdvancedAnalysisBundle:
    ordered_events = [event for event in events if isinstance(event, Mapping)]
    return AdvancedAnalysisBundle(
        fight_episodes=tuple(
            build_fight_episodes(
                ordered_events,
                match_id=match_id,
                tracked_account_ids=tracked_account_ids,
                participants=participants,
            )
        ),
        zone_phases=tuple(
            build_zone_phase_summaries(
                ordered_events,
                match_id=match_id,
                tracked_account_ids=tracked_account_ids,
                participants=participants,
            )
        ),
        team_coordination=tuple(
            build_team_coordination_summaries(
                ordered_events,
                match_id=match_id,
                tracked_account_ids=tracked_account_ids,
                participants=participants,
            )
        ),
        loot_readiness=tuple(
            build_loot_readiness_summaries(
                ordered_events,
                match_id=match_id,
                tracked_account_ids=tracked_account_ids,
            )
        ),
    )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _event_time(event: Mapping[str, Any]) -> datetime | None:
    value = event.get("_D")
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return to_kst(parsed)


def _event_seconds(event: Mapping[str, Any], event_index: int) -> float:
    event_time = _event_time(event)
    return event_time.timestamp() if event_time is not None else float(event_index)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _account(character: Mapping[str, Any]) -> str | None:
    return _text(character.get("accountId"))


def _team(character: Mapping[str, Any], participants: Mapping[str, ParticipantContext]) -> int | None:
    account_id = _account(character)
    if account_id and account_id in participants:
        return participants[account_id].team_id
    return _int(character.get("teamId"))


def _phase(event: Mapping[str, Any]) -> int | None:
    common = _mapping(event.get("common"))
    is_game = _float(common.get("isGame"))
    if is_game is None or is_game < 1:
        return None
    return max(1, int(is_game))


def _location(character: Mapping[str, Any]) -> tuple[float, float] | None:
    location = _mapping(character.get("location"))
    x = _float(location.get("x"))
    y = _float(location.get("y"))
    return (x, y) if x is not None and y is not None else None


def _distance_m(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    explicit: Any = None,
) -> float | None:
    direct = _float(explicit)
    if direct is not None and direct >= 0:
        return direct / 100.0
    left_xy = _location(left)
    right_xy = _location(right)
    if left_xy is None or right_xy is None:
        return None
    return hypot(left_xy[0] - right_xy[0], left_xy[1] - right_xy[1]) / 100.0


def _weapon_code(event: Mapping[str, Any], *, damage_info: Mapping[str, Any] | None = None) -> str | None:
    info = damage_info or event
    code = _text(info.get("damageCauserName"))
    if code:
        return code
    return _text(_mapping(event.get("weapon")).get("itemId"))


def build_fight_episodes(
    events: Iterable[Mapping[str, Any]],
    *,
    match_id: str,
    tracked_account_ids: set[str],
    participants: Mapping[str, ParticipantContext],
) -> list[FightEpisode]:
    observations, takedowns = _fight_observations(events, participants=participants)
    episodes: list[FightEpisode] = []
    for account_id in sorted(tracked_account_ids):
        direct = [
            observation
            for observation in observations
            if observation.actor_id == account_id or observation.target_id == account_id
        ]
        if not direct:
            continue
        groups: list[list[_FightObservation]] = []
        for observation in sorted(direct, key=lambda item: (item.seconds, item.event_index)):
            if not groups or observation.seconds - groups[-1][-1].seconds > FIGHT_GAP_SECONDS:
                groups.append([observation])
            else:
                groups[-1].append(observation)

        episode_index = 0
        for group in groups:
            meaningful = [
                item
                for item in group
                if item.kind in {"damage", "dbno", "kill", "death", "assist"}
            ]
            if not meaningful:
                continue
            episode_index += 1
            episodes.append(
                _summarize_fight_group(
                    group,
                    match_id=match_id,
                    account_id=account_id,
                    episode_index=episode_index,
                    participants=participants,
                    takedowns=takedowns,
                )
            )
    return episodes


def _fight_observations(
    events: Iterable[Mapping[str, Any]],
    *,
    participants: Mapping[str, ParticipantContext],
) -> tuple[list[_FightObservation], list[_Takedown]]:
    observations: list[_FightObservation] = []
    takedowns: list[_Takedown] = []
    for event_index, event in enumerate(events):
        event_type = _text(event.get("_T"))
        event_at = _event_time(event)
        seconds = _event_seconds(event, event_index)
        phase_number = _phase(event)

        if event_type == "LogPlayerAttack":
            actor = _mapping(event.get("attacker"))
            actor_id = _account(actor)
            if actor_id:
                observations.append(
                    _FightObservation(
                        event_index=event_index,
                        event_at_kst=event_at,
                        seconds=seconds,
                        phase_number=phase_number,
                        kind="attack",
                        actor_id=actor_id,
                        target_id=None,
                        actor_team_id=_team(actor, participants),
                        target_team_id=None,
                        damage=0.0,
                        distance_m=None,
                        weapon_code=_weapon_code(event),
                    )
                )
            continue

        if event_type == "LogPlayerTakeDamage":
            actor = _mapping(event.get("attacker"))
            target = _mapping(event.get("victim"))
            actor_id = _account(actor)
            target_id = _account(target)
            if actor_id or target_id:
                observations.append(
                    _FightObservation(
                        event_index=event_index,
                        event_at_kst=event_at,
                        seconds=seconds,
                        phase_number=phase_number,
                        kind="damage",
                        actor_id=actor_id,
                        target_id=target_id,
                        actor_team_id=_team(actor, participants),
                        target_team_id=_team(target, participants),
                        damage=max(0.0, _float(event.get("damage")) or 0.0),
                        distance_m=_distance_m(actor, target, event.get("distance")),
                        weapon_code=_weapon_code(event),
                    )
                )
            continue

        if event_type == "LogPlayerMakeGroggy":
            actor = _mapping(event.get("attacker"))
            target = _mapping(event.get("victim"))
            actor_id = _account(actor)
            target_id = _account(target)
            if actor_id or target_id:
                observation = _FightObservation(
                    event_index=event_index,
                    event_at_kst=event_at,
                    seconds=seconds,
                    phase_number=phase_number,
                    kind="dbno",
                    actor_id=actor_id,
                    target_id=target_id,
                    actor_team_id=_team(actor, participants),
                    target_team_id=_team(target, participants),
                    damage=0.0,
                    distance_m=_distance_m(actor, target, event.get("distance")),
                    weapon_code=_weapon_code(event),
                )
                observations.append(observation)
                takedowns.append(
                    _Takedown(
                        event_index=event_index,
                        seconds=seconds,
                        attacker_id=actor_id,
                        victim_id=target_id,
                        kind="dbno",
                    )
                )
            continue

        if event_type == "LogPlayerKillV2":
            victim = _mapping(event.get("victim"))
            killer = _mapping(event.get("killer"))
            finisher = _mapping(event.get("finisher"))
            victim_id = _account(victim)
            killer_id = _account(killer)
            finisher_id = _account(finisher)
            actor = finisher if finisher_id and finisher_id != victim_id else killer
            actor_id = _account(actor)
            damage_info = _mapping(
                event.get("finishDamageInfo") if finisher_id else event.get("killerDamageInfo")
            )
            if victim_id:
                observation = _FightObservation(
                    event_index=event_index,
                    event_at_kst=event_at,
                    seconds=seconds,
                    phase_number=phase_number,
                    kind="death",
                    actor_id=actor_id,
                    target_id=victim_id,
                    actor_team_id=_team(actor, participants),
                    target_team_id=_team(victim, participants),
                    damage=0.0,
                    distance_m=_distance_m(actor, victim, damage_info.get("distance")),
                    weapon_code=_weapon_code(event, damage_info=damage_info),
                )
                observations.append(observation)
                takedowns.append(
                    _Takedown(
                        event_index=event_index,
                        seconds=seconds,
                        attacker_id=actor_id,
                        victim_id=victim_id,
                        kind="death",
                    )
                )
            for assistant_id in _assist_account_ids(event):
                observations.append(
                    _FightObservation(
                        event_index=event_index,
                        event_at_kst=event_at,
                        seconds=seconds,
                        phase_number=phase_number,
                        kind="assist",
                        actor_id=assistant_id,
                        target_id=victim_id,
                        actor_team_id=participants.get(
                            assistant_id,
                            ParticipantContext(assistant_id, None),
                        ).team_id,
                        target_team_id=_team(victim, participants),
                        damage=0.0,
                        distance_m=None,
                        weapon_code=None,
                    )
                )
            continue

        if event_type == "LogPlayerRevive":
            actor = _mapping(event.get("reviver"))
            target = _mapping(event.get("victim"))
            actor_id = _account(actor)
            target_id = _account(target)
            if actor_id or target_id:
                observations.append(
                    _FightObservation(
                        event_index=event_index,
                        event_at_kst=event_at,
                        seconds=seconds,
                        phase_number=phase_number,
                        kind="revive",
                        actor_id=actor_id,
                        target_id=target_id,
                        actor_team_id=_team(actor, participants),
                        target_team_id=_team(target, participants),
                        damage=0.0,
                        distance_m=_distance_m(actor, target),
                        weapon_code=None,
                    )
                )
    return observations, takedowns


def _assist_account_ids(event: Mapping[str, Any]) -> set[str]:
    result: set[str] = set()
    for key in ("assists_AccountId", "assistants", "assistant"):
        value = event.get(key)
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, Mapping):
                account_id = _account(item)
            else:
                account_id = _text(item)
            if account_id:
                result.add(account_id)
    return result


def _summarize_fight_group(
    group: list[_FightObservation],
    *,
    match_id: str,
    account_id: str,
    episode_index: int,
    participants: Mapping[str, ParticipantContext],
    takedowns: list[_Takedown],
) -> FightEpisode:
    account_team = participants.get(account_id, ParticipantContext(account_id, None)).team_id
    opponents: list[str] = []
    opponent_teams: set[int] = set()
    self_weapons: set[str] = set()
    opponent_weapons: set[str] = set()
    distances: list[float] = []

    for item in group:
        other_id = item.target_id if item.actor_id == account_id else item.actor_id
        other_team = item.target_team_id if item.actor_id == account_id else item.actor_team_id
        if other_id and other_id != account_id and (
            account_team is None or other_team is None or other_team != account_team
        ):
            opponents.append(other_id)
            if other_team is not None:
                opponent_teams.add(other_team)
            if (
                item.kind in {"damage", "dbno", "kill", "death"}
                and item.distance_m is not None
                and item.distance_m >= 0
            ):
                distances.append(item.distance_m)
        if item.weapon_code:
            if item.actor_id == account_id:
                self_weapons.add(item.weapon_code)
            elif item.actor_id:
                opponent_weapons.add(item.weapon_code)
    shots_fired = sum(item.kind == "attack" and item.actor_id == account_id for item in group)
    shots_hit = sum(item.kind == "damage" and item.actor_id == account_id for item in group)
    damage_dealt = sum(
        item.damage for item in group if item.kind == "damage" and item.actor_id == account_id
    )
    damage_taken = sum(
        item.damage for item in group if item.kind == "damage" and item.target_id == account_id
    )
    dbnos_caused = sum(item.kind == "dbno" and item.actor_id == account_id for item in group)
    dbnos_taken = sum(item.kind == "dbno" and item.target_id == account_id for item in group)
    kills = sum(item.kind == "death" and item.actor_id == account_id for item in group)
    deaths = sum(item.kind == "death" and item.target_id == account_id for item in group)
    assists = sum(item.kind == "assist" and item.actor_id == account_id for item in group)
    revives_given = sum(item.kind == "revive" and item.actor_id == account_id for item in group)
    revives_received = sum(item.kind == "revive" and item.target_id == account_id for item in group)

    opening = next(
        (item for item in group if item.kind in {"attack", "damage", "dbno", "death"}),
        group[0],
    )
    first_hit = next((item for item in group if item.kind == "damage"), None)
    opportunity_count, success_count = _episode_trade_result(
        group,
        account_id=account_id,
        account_team=account_team,
        participants=participants,
        takedowns=takedowns,
    )
    outcome = _fight_outcome(
        kills=kills,
        deaths=deaths,
        dbnos_caused=dbnos_caused,
        dbnos_taken=dbnos_taken,
    )
    primary_opponent = Counter(opponents).most_common(1)
    primary_opponent_id = primary_opponent[0][0] if primary_opponent else None
    primary_opponent_team = (
        participants.get(primary_opponent_id).team_id
        if primary_opponent_id and primary_opponent_id in participants
        else None
    )
    phases = Counter(item.phase_number for item in group if item.phase_number is not None)
    phase_number = phases.most_common(1)[0][0] if phases else None
    start = group[0]
    end = group[-1]
    duration = max(0.0, end.seconds - start.seconds)

    return FightEpisode(
        match_id=match_id,
        account_id=account_id,
        episode_index=episode_index,
        start_event_index=start.event_index,
        end_event_index=end.event_index,
        started_at_kst=start.event_at_kst,
        ended_at_kst=end.event_at_kst,
        duration_seconds=duration,
        phase_number=phase_number,
        outcome=outcome,
        opening_actor=_actor_role(opening.actor_id, account_id),
        first_hit_actor=_actor_role(first_hit.actor_id, account_id) if first_hit else "unknown",
        primary_opponent_account_id=primary_opponent_id,
        primary_opponent_team_id=primary_opponent_team,
        opponent_count=len(set(opponents)),
        opponent_team_count=len(opponent_teams),
        shots_fired=shots_fired,
        shots_hit=shots_hit,
        damage_dealt=damage_dealt,
        damage_taken=damage_taken,
        dbnos_caused=dbnos_caused,
        dbnos_taken=dbnos_taken,
        kills=kills,
        deaths=deaths,
        assists=assists,
        revives_given=revives_given,
        revives_received=revives_received,
        trade_opportunities=opportunity_count,
        trade_successes=success_count,
        is_third_party=len(opponent_teams) > 1,
        weapon_codes=tuple(sorted(self_weapons)),
        opponent_weapon_codes=tuple(sorted(opponent_weapons)),
        min_distance_m=min(distances) if distances else None,
        avg_distance_m=mean(distances) if distances else None,
        max_distance_m=max(distances) if distances else None,
        summary_reason=_fight_summary(
            outcome=outcome,
            opening_actor=_actor_role(opening.actor_id, account_id),
            damage_dealt=damage_dealt,
            damage_taken=damage_taken,
            dbnos_caused=dbnos_caused,
            dbnos_taken=dbnos_taken,
            kills=kills,
            deaths=deaths,
            third_party=len(opponent_teams) > 1,
        ),
    )


def _episode_trade_result(
    group: list[_FightObservation],
    *,
    account_id: str,
    account_team: int | None,
    participants: Mapping[str, ParticipantContext],
    takedowns: list[_Takedown],
) -> tuple[int, int]:
    losses = [item for item in group if item.kind == "dbno" and item.target_id == account_id]
    if not losses:
        losses = [item for item in group if item.kind == "death" and item.target_id == account_id]
    successes = 0
    for loss in losses:
        attacker_id = loss.actor_id
        if not attacker_id or account_team is None:
            continue
        traded = any(
            takedown.victim_id == attacker_id
            and takedown.attacker_id is not None
            and participants.get(
                takedown.attacker_id,
                ParticipantContext(takedown.attacker_id, None),
            ).team_id
            == account_team
            and 0 <= takedown.seconds - loss.seconds <= TRADE_WINDOW_SECONDS
            for takedown in takedowns
        )
        successes += int(traded)
    return len(losses), successes


def _actor_role(actor_id: str | None, account_id: str) -> str:
    if not actor_id:
        return "unknown"
    return "self" if actor_id == account_id else "opponent"


def _fight_outcome(
    *,
    kills: int,
    deaths: int,
    dbnos_caused: int,
    dbnos_taken: int,
) -> str:
    if deaths:
        return "loss"
    if dbnos_caused and dbnos_taken:
        return "mixed"
    if kills or dbnos_caused:
        return "win"
    if dbnos_taken:
        return "loss"
    return "unresolved"


def _fight_summary(
    *,
    outcome: str,
    opening_actor: str,
    damage_dealt: float,
    damage_taken: float,
    dbnos_caused: int,
    dbnos_taken: int,
    kills: int,
    deaths: int,
    third_party: bool,
) -> str:
    labels = {
        "win": "교전 승리",
        "loss": "교전 패배",
        "mixed": "상호 기절 교전",
        "unresolved": "승패 미확정",
    }
    opener = "선공" if opening_actor == "self" else "피격 시작"
    third = " · 제3자 개입" if third_party else ""
    return (
        f"{labels.get(outcome, outcome)} · {opener} · "
        f"준/받은 피해 {damage_dealt:.1f}/{damage_taken:.1f} · "
        f"킬 {kills} · 기절 +{dbnos_caused}/-{dbnos_taken} · 사망 {deaths}{third}"
    )


def build_zone_phase_summaries(
    events: Iterable[Mapping[str, Any]],
    *,
    match_id: str,
    tracked_account_ids: set[str],
    participants: Mapping[str, ParticipantContext],
) -> list[ZonePhaseSummary]:
    event_list = list(events)
    positions, states = _positions_and_states(event_list, participants=participants)
    observations, _ = _fight_observations(event_list, participants=participants)
    loss_counts: Counter[tuple[str, int, str]] = Counter()
    for observation in observations:
        if (
            observation.target_id in tracked_account_ids
            and observation.phase_number is not None
            and observation.kind in {"dbno", "death"}
        ):
            loss_counts[
                (observation.target_id, observation.phase_number, observation.kind)
            ] += 1

    summaries: list[ZonePhaseSummary] = []
    by_account_phase: dict[tuple[str, int], list[_Position]] = defaultdict(list)
    for position in positions:
        if position.account_id in tracked_account_ids and position.phase_number is not None:
            by_account_phase[(position.account_id, position.phase_number)].append(position)

    states_by_phase: dict[int, list[_GameState]] = defaultdict(list)
    for state in states:
        if state.phase_number is not None:
            states_by_phase[state.phase_number].append(state)

    for (account_id, phase_number), phase_positions in sorted(by_account_phase.items()):
        ordered = sorted(phase_positions, key=lambda item: (item.seconds, item.event_index))
        phase_states = states_by_phase.get(phase_number, [])
        phase_elapsed_values = [
            state.elapsed_seconds
            for state in phase_states
            if state.elapsed_seconds is not None
        ]
        sample_elapsed_values = [
            item.elapsed_seconds for item in ordered if item.elapsed_seconds is not None
        ]
        phase_start = (
            min(phase_elapsed_values)
            if phase_elapsed_values
            else (min(sample_elapsed_values) if sample_elapsed_values else None)
        )
        phase_end = (
            max(phase_elapsed_values)
            if phase_elapsed_values
            else (max(sample_elapsed_values) if sample_elapsed_values else None)
        )

        outside_seconds = 0.0
        blue_seconds = 0.0
        edge_seconds = 0.0
        center_seconds = 0.0
        vehicle_seconds = 0.0
        rotation_distance = 0.0
        foot_distance = 0.0
        vehicle_distance = 0.0
        center_ratios: list[float] = []
        outside_distances: list[float] = []
        first_inside: float | None = None

        for index, position in enumerate(ordered):
            next_position = ordered[index + 1] if index + 1 < len(ordered) else None
            duration = _sample_duration(position, next_position)
            state = _state_for_position(position, states)
            center_ratio: float | None = None
            outside_distance_m = 0.0
            if (
                state is not None
                and state.safety_x is not None
                and state.safety_y is not None
                and state.safety_radius is not None
                and state.safety_radius > 0
            ):
                distance_cm = hypot(position.x - state.safety_x, position.y - state.safety_y)
                center_ratio = distance_cm / state.safety_radius
                center_ratios.append(center_ratio)
                outside_distance_m = max(0.0, distance_cm - state.safety_radius) / 100.0
                outside_distances.append(outside_distance_m)
                if outside_distance_m > 0:
                    outside_seconds += duration
                elif first_inside is None and position.elapsed_seconds is not None:
                    first_inside = position.elapsed_seconds
                if 0.75 <= center_ratio <= 1.0:
                    edge_seconds += duration
                elif center_ratio <= 0.5:
                    center_seconds += duration
            if position.is_in_blue_zone:
                blue_seconds += duration
            if position.is_in_vehicle:
                vehicle_seconds += duration

            if next_position is not None:
                distance_m = hypot(
                    next_position.x - position.x,
                    next_position.y - position.y,
                ) / 100.0
                if distance_m <= 2000.0:
                    rotation_distance += distance_m
                    if position.is_in_vehicle or next_position.is_in_vehicle:
                        vehicle_distance += distance_m
                    else:
                        foot_distance += distance_m

        late_entry = (
            max(0.0, first_inside - phase_start)
            if first_inside is not None and phase_start is not None
            else None
        )
        summaries.append(
            ZonePhaseSummary(
                match_id=match_id,
                account_id=account_id,
                phase_number=phase_number,
                sample_count=len(ordered),
                phase_started_elapsed_seconds=phase_start,
                phase_ended_elapsed_seconds=phase_end,
                first_inside_elapsed_seconds=first_inside,
                late_entry_seconds=late_entry,
                outside_safe_zone_seconds=outside_seconds,
                blue_zone_exposure_seconds=blue_seconds,
                max_outside_distance_m=max(outside_distances, default=0.0),
                avg_center_distance_ratio=mean(center_ratios) if center_ratios else None,
                edge_position_seconds=edge_seconds,
                center_position_seconds=center_seconds,
                rotation_distance_m=rotation_distance,
                foot_distance_m=foot_distance,
                vehicle_distance_m=vehicle_distance,
                vehicle_seconds=vehicle_seconds,
                dbnos_taken=loss_counts[(account_id, phase_number, "dbno")],
                deaths=loss_counts[(account_id, phase_number, "death")],
            )
        )
    return summaries


def _positions_and_states(
    events: Iterable[Mapping[str, Any]],
    *,
    participants: Mapping[str, ParticipantContext],
) -> tuple[list[_Position], list[_GameState]]:
    positions: list[_Position] = []
    states: list[_GameState] = []
    for event_index, event in enumerate(events):
        event_type = _text(event.get("_T"))
        if event_type == "LogGameStatePeriodic":
            game_state = _mapping(event.get("gameState"))
            safety = _mapping(game_state.get("safetyZonePosition"))
            states.append(
                _GameState(
                    event_index=event_index,
                    elapsed_seconds=_float(game_state.get("elapsedTime")),
                    phase_number=_phase(event),
                    safety_x=_float(safety.get("x")),
                    safety_y=_float(safety.get("y")),
                    safety_radius=_float(game_state.get("safetyZoneRadius")),
                )
            )
            continue
        if event_type != "LogPlayerPosition":
            continue
        character = _mapping(event.get("character"))
        account_id = _account(character)
        location = _location(character)
        if not account_id or location is None:
            continue
        vehicle = _mapping(event.get("vehicle"))
        vehicle_type = _text(vehicle.get("vehicleType"))
        positions.append(
            _Position(
                event_index=event_index,
                account_id=account_id,
                team_id=_team(character, participants),
                event_at_kst=_event_time(event),
                elapsed_seconds=_float(event.get("elapsedTime")),
                seconds=_event_seconds(event, event_index),
                phase_number=_phase(event),
                x=location[0],
                y=location[1],
                is_in_vehicle=bool(vehicle_type and vehicle_type.casefold() not in {"none", "aircraft"}),
                is_in_blue_zone=bool(character.get("isInBlueZone")),
            )
        )
    return positions, states


def _state_for_position(
    position: _Position,
    states: list[_GameState],
) -> _GameState | None:
    candidates = [
        state
        for state in states
        if state.event_index <= position.event_index
        and (
            position.phase_number is None
            or state.phase_number is None
            or state.phase_number == position.phase_number
        )
    ]
    if candidates:
        return candidates[-1]
    later = [
        state
        for state in states
        if position.phase_number is None
        or state.phase_number is None
        or state.phase_number == position.phase_number
    ]
    return later[0] if later else None


def _sample_duration(position: _Position, next_position: _Position | None) -> float:
    if next_position is None:
        return 0.0
    if (
        position.elapsed_seconds is not None
        and next_position.elapsed_seconds is not None
    ):
        delta = next_position.elapsed_seconds - position.elapsed_seconds
    else:
        delta = next_position.seconds - position.seconds
    return max(0.0, min(30.0, delta))


def build_team_coordination_summaries(
    events: Iterable[Mapping[str, Any]],
    *,
    match_id: str,
    tracked_account_ids: set[str],
    participants: Mapping[str, ParticipantContext],
) -> list[TeamCoordinationSummary]:
    event_list = list(events)
    positions, _ = _positions_and_states(event_list, participants=participants)
    observations, takedowns = _fight_observations(event_list, participants=participants)
    by_account: dict[str, list[_Position]] = defaultdict(list)
    for position in positions:
        if position.phase_number is not None:
            by_account[position.account_id].append(position)
    for account_positions in by_account.values():
        account_positions.sort(key=lambda item: (item.seconds, item.event_index))

    summaries: list[TeamCoordinationSummary] = []
    for account_id in sorted(tracked_account_ids):
        context = participants.get(account_id, ParticipantContext(account_id, None))
        team_accounts = {
            participant.account_id
            for participant in participants.values()
            if context.team_id is not None
            and participant.team_id == context.team_id
            and participant.account_id != account_id
        }
        account_positions = by_account.get(account_id, [])
        nearest_distances: list[float] = []
        visible_counts: list[int] = []
        isolated_seconds = 0.0
        close_support_seconds = 0.0
        regroup_count = 0
        was_isolated = False

        for index, position in enumerate(account_positions):
            candidates: list[_Position] = []
            for teammate_id in team_accounts:
                candidate = _nearest_position(
                    by_account.get(teammate_id, []),
                    seconds=position.seconds,
                )
                if candidate is not None:
                    candidates.append(candidate)
            visible_counts.append(len(candidates))
            next_position = (
                account_positions[index + 1]
                if index + 1 < len(account_positions)
                else None
            )
            duration = _sample_duration(position, next_position)
            if not candidates:
                continue
            distances = [
                hypot(candidate.x - position.x, candidate.y - position.y) / 100.0
                for candidate in candidates
            ]
            nearest = min(distances)
            nearest_distances.append(nearest)
            isolated = nearest > ISOLATION_DISTANCE_M
            if isolated:
                isolated_seconds += duration
            elif nearest <= CLOSE_SUPPORT_DISTANCE_M:
                close_support_seconds += duration
            if was_isolated and not isolated:
                regroup_count += 1
            was_isolated = isolated

        team_members = team_accounts | {account_id}
        team_dbnos = [
            item
            for item in observations
            if item.kind == "dbno" and item.target_id in team_members
        ]
        team_deaths = [
            item
            for item in observations
            if item.kind == "death" and item.target_id in team_members
        ]
        trade_events = list(team_dbnos)
        for death in team_deaths:
            has_prior_dbno = any(
                down.target_id == death.target_id
                and 0 <= death.seconds - down.seconds <= 180.0
                for down in team_dbnos
            )
            if not has_prior_dbno:
                trade_events.append(death)
        trade_successes = sum(
            _team_trade_succeeded(
                event,
                team_members=team_members,
                takedowns=takedowns,
            )
            for event in trade_events
            if event.actor_id and event.actor_id not in team_members
        )

        revives_given = sum(
            item.kind == "revive" and item.actor_id == account_id for item in observations
        )
        revives_received = sum(
            item.kind == "revive" and item.target_id == account_id for item in observations
        )
        revive_latencies: list[float] = []
        for revive in observations:
            if revive.kind != "revive":
                continue
            if revive.actor_id != account_id and revive.target_id != account_id:
                continue
            prior_downs = [
                down
                for down in team_dbnos
                if down.target_id == revive.target_id and down.seconds <= revive.seconds
            ]
            if prior_downs:
                latency = revive.seconds - prior_downs[-1].seconds
                if 0 <= latency <= 300:
                    revive_latencies.append(latency)

        summaries.append(
            TeamCoordinationSummary(
                match_id=match_id,
                account_id=account_id,
                sample_count=len(nearest_distances),
                avg_nearest_teammate_distance_m=(
                    mean(nearest_distances) if nearest_distances else None
                ),
                max_nearest_teammate_distance_m=(
                    max(nearest_distances) if nearest_distances else None
                ),
                avg_visible_teammates=mean(visible_counts) if visible_counts else None,
                isolated_seconds=isolated_seconds,
                close_support_seconds=close_support_seconds,
                regroup_count=regroup_count,
                trade_opportunities=sum(
                    bool(event.actor_id and event.actor_id not in team_members)
                    for event in trade_events
                ),
                trade_successes=trade_successes,
                revives_given=revives_given,
                revives_received=revives_received,
                avg_revive_latency_seconds=(
                    mean(revive_latencies) if revive_latencies else None
                ),
                team_dbnos_taken=len(team_dbnos),
                team_deaths=len(team_deaths),
            )
        )
    return summaries


def _nearest_position(
    positions: list[_Position],
    *,
    seconds: float,
) -> _Position | None:
    if not positions:
        return None
    observed = [
        item
        for item in positions
        if item.seconds <= seconds
        and seconds - item.seconds <= POSITION_FRESHNESS_SECONDS
    ]
    if observed:
        return max(observed, key=lambda item: item.seconds)

    # Position events for teammates can arrive a fraction later in the same
    # telemetry sample. Allow only a narrow clock-skew fallback so an analysis
    # point cannot borrow materially future movement.
    near_future = [item for item in positions if 0 < item.seconds - seconds <= 2.0]
    return min(near_future, key=lambda item: item.seconds) if near_future else None


def _team_trade_succeeded(
    down: _FightObservation,
    *,
    team_members: set[str],
    takedowns: list[_Takedown],
) -> bool:
    if not down.actor_id:
        return False
    return any(
        takedown.attacker_id in team_members
        and takedown.attacker_id != down.target_id
        and takedown.victim_id == down.actor_id
        and 0 <= takedown.seconds - down.seconds <= TRADE_WINDOW_SECONDS
        for takedown in takedowns
    )


def build_loot_readiness_summaries(
    events: Iterable[Mapping[str, Any]],
    *,
    match_id: str,
    tracked_account_ids: set[str],
) -> list[LootReadinessSummary]:
    event_list = list(events)
    summaries: list[LootReadinessSummary] = []
    for account_id in sorted(tracked_account_ids):
        landing_event = next(
            (
                (index, event)
                for index, event in enumerate(event_list)
                if event.get("_T") == "LogParachuteLanding"
                and _account(_mapping(event.get("character"))) == account_id
            ),
            None,
        )
        landed_at = _event_time(landing_event[1]) if landing_event else None
        landing_seconds = (
            _event_seconds(landing_event[1], landing_event[0]) if landing_event else None
        )
        first_fight = _first_fight_event(event_list, account_id=account_id)
        first_fight_at = _event_time(first_fight[1]) if first_fight else None
        first_fight_seconds = (
            _event_seconds(first_fight[1], first_fight[0]) if first_fight else None
        )

        primary_weapons: list[tuple[str, float]] = []
        milestone_seconds: dict[str, float] = {}
        early_inventory: list[str] = []
        pickup_events = 0
        source_counts: Counter[str] = Counter()

        for event_index, event in enumerate(event_list):
            event_type = _text(event.get("_T")) or ""
            if not _is_item_acquisition_event(event_type):
                continue
            character = _mapping(event.get("character"))
            if _account(character) != account_id:
                continue
            item = _mapping(event.get("item"))
            item_code = _text(item.get("itemId"))
            if not item_code:
                continue
            event_seconds = _event_seconds(event, event_index)
            if event_type.startswith("LogItemPickup"):
                pickup_events += 1
                source_counts[_loot_source(event_type)] += 1

            if _is_primary_weapon(item, item_code):
                if item_code not in {code for code, _ in primary_weapons}:
                    primary_weapons.append((item_code, event_seconds))
            if _is_vest(item_code):
                milestone_seconds.setdefault("vest", event_seconds)
            if _is_helmet(item_code):
                milestone_seconds.setdefault("helmet", event_seconds)
            if _is_heal_item(item_code):
                milestone_seconds.setdefault("heal", event_seconds)
            if _is_throwable(item, item_code):
                milestone_seconds.setdefault("throwable", event_seconds)
            if _is_scope(item_code):
                milestone_seconds.setdefault("scope", event_seconds)

            if (
                landing_seconds is not None
                and 0 <= event_seconds - landing_seconds <= 180.0
                and item_code not in early_inventory
                and len(early_inventory) < 30
            ):
                early_inventory.append(item_code)

        first_weapon = primary_weapons[0] if primary_weapons else None
        second_weapon = primary_weapons[1] if len(primary_weapons) > 1 else None
        if first_weapon:
            milestone_seconds["first_weapon"] = first_weapon[1]
        if second_weapon:
            milestone_seconds["second_weapon"] = second_weapon[1]

        evaluation_seconds = (
            first_fight_seconds
            if first_fight_seconds is not None
            else (landing_seconds + 180.0 if landing_seconds is not None else None)
        )
        readiness_weights = {
            "first_weapon": 25.0,
            "second_weapon": 20.0,
            "vest": 15.0,
            "helmet": 15.0,
            "heal": 15.0,
            "scope": 5.0,
            "throwable": 5.0,
        }
        readiness_score = sum(
            weight
            for key, weight in readiness_weights.items()
            if key in milestone_seconds
            and (
                evaluation_seconds is None
                or milestone_seconds[key] <= evaluation_seconds
            )
        )
        required_before_fight = {"first_weapon", "vest", "helmet", "heal"}
        ready_before_fight = None
        if first_fight_seconds is not None:
            ready_before_fight = all(
                key in milestone_seconds
                and milestone_seconds[key] <= first_fight_seconds
                for key in required_before_fight
            )

        summaries.append(
            LootReadinessSummary(
                match_id=match_id,
                account_id=account_id,
                landed_at_kst=landed_at,
                first_fight_at_kst=first_fight_at,
                first_primary_weapon_code=first_weapon[0] if first_weapon else None,
                second_primary_weapon_code=second_weapon[0] if second_weapon else None,
                seconds_to_first_primary_weapon=_seconds_after(
                    first_weapon[1] if first_weapon else None,
                    landing_seconds,
                ),
                seconds_to_second_primary_weapon=_seconds_after(
                    second_weapon[1] if second_weapon else None,
                    landing_seconds,
                ),
                seconds_to_vest=_seconds_after(
                    milestone_seconds.get("vest"),
                    landing_seconds,
                ),
                seconds_to_helmet=_seconds_after(
                    milestone_seconds.get("helmet"),
                    landing_seconds,
                ),
                seconds_to_heal=_seconds_after(
                    milestone_seconds.get("heal"),
                    landing_seconds,
                ),
                seconds_to_throwable=_seconds_after(
                    milestone_seconds.get("throwable"),
                    landing_seconds,
                ),
                seconds_to_scope=_seconds_after(
                    milestone_seconds.get("scope"),
                    landing_seconds,
                ),
                seconds_to_first_fight=_seconds_after(
                    first_fight_seconds,
                    landing_seconds,
                ),
                ready_before_first_fight=ready_before_fight,
                pickup_events=pickup_events,
                ground_pickups=source_counts["ground"],
                loot_box_pickups=source_counts["loot_box"],
                care_package_pickups=source_counts["care_package"],
                vehicle_trunk_pickups=source_counts["vehicle_trunk"],
                readiness_score=readiness_score,
                early_inventory=tuple(early_inventory),
            )
        )
    return summaries


def _first_fight_event(
    events: list[Mapping[str, Any]],
    *,
    account_id: str,
) -> tuple[int, Mapping[str, Any]] | None:
    for event_index, event in enumerate(events):
        event_type = _text(event.get("_T"))
        if event_type == "LogPlayerAttack":
            if _account(_mapping(event.get("attacker"))) == account_id:
                return event_index, event
        elif event_type in {"LogPlayerTakeDamage", "LogPlayerMakeGroggy"}:
            if account_id in {
                _account(_mapping(event.get("attacker"))),
                _account(_mapping(event.get("victim"))),
            }:
                return event_index, event
        elif event_type == "LogPlayerKillV2":
            if account_id in {
                _account(_mapping(event.get("killer"))),
                _account(_mapping(event.get("finisher"))),
                _account(_mapping(event.get("victim"))),
            }:
                return event_index, event
    return None


def _is_item_acquisition_event(event_type: str) -> bool:
    return event_type in {
        "LogItemPickup",
        "LogItemPickupFromLootBox",
        "LogItemPickupFromLootbox",
        "LogItemPickupFromCarepackage",
        "LogItemPickupFromCustomPackage",
        "LogItemPickupFromVehicleTrunk",
        "LogItemEquip",
        "LogItemAttachToWeapon",
    }


def _loot_source(event_type: str) -> str:
    normalized = event_type.casefold()
    if "carepackage" in normalized:
        return "care_package"
    if "lootbox" in normalized:
        return "loot_box"
    if "vehicletrunk" in normalized:
        return "vehicle_trunk"
    if "custompackage" in normalized:
        return "custom_package"
    return "ground"


def _is_primary_weapon(item: Mapping[str, Any], item_code: str) -> bool:
    category = (_text(item.get("category")) or "").casefold()
    sub_category = (_text(item.get("subCategory")) or "").casefold()
    normalized = item_code.casefold()
    if category and category != "weapon":
        return False
    if sub_category in {"throwable", "melee", "secondary", "handgun"}:
        return False
    if any(
        token in normalized
        for token in (
            "_attach_",
            "grenade",
            "smokebomb",
            "molotov",
            "flashbang",
            "_pan_",
            "crowbar",
            "machete",
            "sickle",
        )
    ):
        return False
    return normalized.startswith("item_weapon_")


def _is_vest(item_code: str) -> bool:
    normalized = item_code.casefold()
    return "_armor_" in normalized or "_vest_" in normalized


def _is_helmet(item_code: str) -> bool:
    return "_head_" in item_code.casefold() or "helmet" in item_code.casefold()


def _is_heal_item(item_code: str) -> bool:
    normalized = item_code.casefold()
    return any(
        token in normalized
        for token in (
            "firstaid",
            "medkit",
            "bandage",
            "painkiller",
            "energydrink",
            "adrenaline",
        )
    )


def _is_throwable(item: Mapping[str, Any], item_code: str) -> bool:
    sub_category = (_text(item.get("subCategory")) or "").casefold()
    normalized = item_code.casefold()
    return sub_category == "throwable" or any(
        token in normalized
        for token in (
            "grenade",
            "smokebomb",
            "molotov",
            "flashbang",
            "stickybomb",
            "bluezonegrenade",
            "decoygrenade",
            "_c4_",
        )
    )


def _is_scope(item_code: str) -> bool:
    normalized = item_code.casefold()
    return (
        "attach_weapon_upper" in normalized
        or "scope" in normalized
        or "reddot" in normalized
        or "holosight" in normalized
    )


def _seconds_after(value: float | None, start: float | None) -> float | None:
    if value is None or start is None:
        return None
    return max(0.0, value - start)
