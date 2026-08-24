from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta
from math import sqrt
from typing import Any, Mapping
import json

from pubg_ai.code_translator import translate_code
from pubg_ai.distance_buckets import distance_bucket
from pubg_ai.player_registry import RegisteredPlayer
from pubg_ai.player_scope import PLAYER_GUILD_SCOPE_CONDITION
from pubg_ai.player_trends import PlayerTrendFilters
from pubg_ai.replay_artifact_catalog import ReplayArtifactRecord, list_replay_artifacts
from pubg_ai.weapon_accuracy import (
    AccuracyBreakdown,
    WeaponAccuracyMetric,
    summarize_accuracy_rows,
    distance_weapon_family,
    weapon_accuracy_metric,
    weapon_family,
)
from pubg_ai.weapon_stats import normalize_weapon_code


@dataclass(frozen=True)
class PlayerCombatTotals:
    match_count: int
    wins: int
    kills: int
    assists: int
    deaths: int
    dbnos_caused: int
    dbnos_taken: int
    damage_dealt: float
    damage_taken: float
    shots_fired: int
    shots_hit: int
    headshot_kills: int
    avg_damage_dealt: float
    avg_damage_taken: float
    win_rate: float
    kda: float
    accuracy: float
    headshot_kill_rate: float
    avg_survival_seconds: float
    avg_movement_distance_m: float
    first_match_at_kst: datetime | None = None
    last_match_at_kst: datetime | None = None
    accuracy_breakdown: AccuracyBreakdown | None = None
    hits_taken: int = 0
    headshot_hits: int = 0
    headshot_hits_taken: int = 0
    headshot_hit_rate: float = 0.0
    headshot_hit_taken_rate: float = 0.0
    hit_parts: dict[str, int] = field(default_factory=dict)
    taken_hit_parts: dict[str, int] = field(default_factory=dict)
    character_hits: int = 0
    vehicle_hits: int = 0
    vehicle_damage_dealt: float = 0.0

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["first_match_at_kst"] = _datetime_record(self.first_match_at_kst)
        record["last_match_at_kst"] = _datetime_record(self.last_match_at_kst)
        record["hit_part_rates"] = _part_rates(self.hit_parts)
        record["taken_hit_part_rates"] = _part_rates(self.taken_hit_parts)
        return record


@dataclass(frozen=True)
class PlayerWeaponStats:
    weapon_code: str
    weapon_name: str
    match_count: int
    kills: int
    assists: int
    deaths: int
    dbnos: int
    damage_dealt: float
    shots_fired: int
    shots_hit: int
    accuracy: float
    headshot_kills: int
    accuracy_metric: WeaponAccuracyMetric | None = None
    headshot_hits: int = 0
    character_hits: int = 0
    vehicle_hits: int = 0
    vehicle_damage_dealt: float = 0.0

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["headshot_hit_rate"] = _safe_divide(
            self.headshot_hits,
            _character_hit_denominator(
                self.character_hits,
                self.vehicle_hits,
                self.shots_hit,
            ),
        )
        record["headshot_kill_rate"] = _safe_divide(self.headshot_kills, self.kills)
        return record


@dataclass(frozen=True)
class PlayerRecentMatch:
    match_id: str
    created_at_kst: datetime | None
    map_name: str | None
    game_mode: str | None
    match_type: str | None
    win_place: int | None
    kills: int
    assists: int
    deaths: int
    dbnos_caused: int
    damage_dealt: float
    survival_seconds: float | None
    movement_distance_m: float | None

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["created_at_kst"] = _datetime_record(self.created_at_kst)
        record["map_name_ko"] = translate_code(self.map_name, "map") if self.map_name else None
        record["game_mode_ko"] = translate_code(self.game_mode, "game_mode") if self.game_mode else None
        return record


@dataclass(frozen=True)
class PlayerWeaponDetailTotals:
    match_count: int
    wins: int
    kills: int
    assists: int
    deaths_taken: int
    dbnos: int
    dbnos_taken: int
    finishes: int
    finishes_taken: int
    damage_dealt: float
    damage_taken: float
    shots_fired: int
    shots_hit: int
    hits_taken: int
    headshot_hits: int
    headshot_kills: int
    headshot_dbnos: int
    accuracy: float
    avg_damage_dealt: float
    win_rate: float
    headshot_kill_rate: float
    hit_parts: dict[str, int]
    taken_hit_parts: dict[str, int]
    accuracy_metric: WeaponAccuracyMetric | None = None
    fight_count: int = 0
    fight_wins: int = 0
    fight_losses: int = 0
    fight_win_rate: float = 0.0
    avg_fights_per_match: float = 0.0
    character_hits: int = 0
    vehicle_hits: int = 0
    vehicle_damage_dealt: float = 0.0

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["avg_kills"] = _safe_divide(self.kills, self.match_count)
        record["avg_assists"] = _safe_divide(self.assists, self.match_count)
        record["avg_dbnos"] = _safe_divide(self.dbnos, self.match_count)
        record["avg_deaths_taken"] = _safe_divide(self.deaths_taken, self.match_count)
        record["avg_damage_taken"] = _safe_divide(self.damage_taken, self.match_count)
        record["headshot_hit_rate"] = _safe_divide(
            self.headshot_hits,
            _character_hit_denominator(
                self.character_hits,
                self.vehicle_hits,
                self.shots_hit,
            ),
        )
        record["headshot_hit_taken_rate"] = _safe_divide(
            self.taken_hit_parts.get("head", 0),
            self.hits_taken,
        )
        record["hit_part_rates"] = _part_rates(self.hit_parts)
        record["taken_hit_part_rates"] = _part_rates(self.taken_hit_parts)
        return record


@dataclass(frozen=True)
class PlayerWeaponRecentMatch:
    match_id: str
    created_at_kst: datetime | None
    map_name: str | None
    game_mode: str | None
    win_place: int | None
    kills: int
    assists: int
    deaths_taken: int
    dbnos: int
    damage_dealt: float
    shots_fired: int
    shots_hit: int
    accuracy: float
    accuracy_metric: WeaponAccuracyMetric | None = None

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["created_at_kst"] = _datetime_record(self.created_at_kst)
        record["map_name_ko"] = translate_code(self.map_name, "map") if self.map_name else None
        record["game_mode_ko"] = translate_code(self.game_mode, "game_mode") if self.game_mode else None
        return record


@dataclass(frozen=True)
class PlayerWeaponFightRange:
    bucket_label: str
    min_m: int
    max_m: int
    fight_count: int
    wins: int
    losses: int
    observed_win_rate: float
    confidence_adjusted_win_rate: float
    efficiency_score: float
    avg_distance_m: float
    reliable_sample: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PlayerWeaponTrendPoint:
    period_key: str
    period_label: str
    first_match_at_kst: datetime
    last_match_at_kst: datetime
    totals: PlayerWeaponDetailTotals

    def to_record(self) -> dict[str, Any]:
        return {
            "period_key": self.period_key,
            "period_label": self.period_label,
            "first_match_at_kst": self.first_match_at_kst.isoformat(),
            "last_match_at_kst": self.last_match_at_kst.isoformat(),
            **self.totals.to_record(),
        }


@dataclass(frozen=True)
class PlayerWeaponTrendSeries:
    granularity: str
    points: list[PlayerWeaponTrendPoint]
    available_point_count: int
    truncated: bool

    def to_record(self) -> dict[str, Any]:
        return {
            "granularity": self.granularity,
            "points": [point.to_record() for point in self.points],
            "available_point_count": self.available_point_count,
            "returned_point_count": len(self.points),
            "truncated": self.truncated,
        }


@dataclass(frozen=True)
class PlayerWeaponDetail:
    player: RegisteredPlayer
    weapon_code: str
    weapon_name: str
    totals: PlayerWeaponDetailTotals
    recent_matches: list[PlayerWeaponRecentMatch]
    filters: PlayerTrendFilters = field(default_factory=PlayerTrendFilters)
    effective_ranges: list[PlayerWeaponFightRange] = field(default_factory=list)
    trend_series: dict[str, PlayerWeaponTrendSeries] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return {
            "player": self.player.to_record(),
            "weapon_code": self.weapon_code,
            "weapon_name": self.weapon_name,
            "totals": self.totals.to_record(),
            "recent_matches": [match.to_record() for match in self.recent_matches],
            "filters": self.filters.to_record(),
            "effective_ranges": [item.to_record() for item in self.effective_ranges],
            "trend_series": {
                key: series.to_record()
                for key, series in self.trend_series.items()
            },
        }


@dataclass(frozen=True)
class PlayerCatalogWeapon:
    weapon_code: str
    weapon_name: str
    weapon_family: str
    match_count: int

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PlayerCatalogMatch:
    match_id: str
    created_at_kst: datetime | None
    map_name: str | None
    game_mode: str | None
    team_mode: str | None
    perspective: str | None
    match_type: str | None
    season_state: str | None
    win_place: int | None
    kills: int
    assists: int
    deaths: int
    dbnos_caused: int
    damage_dealt: float

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["created_at_kst"] = _datetime_record(self.created_at_kst)
        record["map_name_ko"] = translate_code(self.map_name, "map") if self.map_name else None
        record["game_mode_ko"] = translate_code(self.game_mode, "game_mode") if self.game_mode else None
        return record


@dataclass(frozen=True)
class PlayerLookupCatalog:
    player: RegisteredPlayer
    weapons: list[PlayerCatalogWeapon]
    matches: list[PlayerCatalogMatch]
    facets: dict[str, list[Any]]

    def to_record(self) -> dict[str, Any]:
        return {
            "player": self.player.to_record(),
            "weapons": [weapon.to_record() for weapon in self.weapons],
            "matches": [match.to_record() for match in self.matches],
            "facets": self.facets,
        }


@dataclass(frozen=True)
class PlayerMatchWeaponStats:
    weapon_code: str
    weapon_name: str
    kills: int
    assists: int
    deaths: int
    dbnos: int
    dbnos_taken: int
    damage_dealt: float
    damage_taken: float
    shots_fired: int
    shots_hit: int
    accuracy: float
    headshot_kills: int
    hit_parts: dict[str, int]
    taken_hit_parts: dict[str, int]
    accuracy_metric: WeaponAccuracyMetric | None = None
    character_hits: int = 0
    vehicle_hits: int = 0
    vehicle_damage_dealt: float = 0.0

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["headshot_hit_rate"] = _safe_divide(
            self.hit_parts.get("head", 0),
            _character_hit_denominator(
                self.character_hits,
                self.vehicle_hits,
                self.shots_hit,
            ),
        )
        record["headshot_kill_rate"] = _safe_divide(self.headshot_kills, self.kills)
        record["hit_part_rates"] = _part_rates(self.hit_parts)
        record["taken_hit_part_rates"] = _part_rates(self.taken_hit_parts)
        return record


@dataclass(frozen=True)
class PlayerMatchItemStats:
    item_code: str
    item_name: str
    item_category: str | None
    item_sub_category: str | None
    picked_up_events: int
    picked_up_quantity: int
    loot_box_pickup_events: int
    carepackage_pickup_events: int
    custom_package_pickup_events: int
    vehicle_trunk_pickup_events: int
    vehicle_trunk_put_events: int
    dropped_events: int
    dropped_quantity: int
    used_events: int
    used_quantity: int
    equipped_events: int
    unequipped_events: int
    attached_events: int
    detached_events: int

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["item_category_ko"] = (
            translate_code(self.item_category, "item_category")
            if self.item_category
            else None
        )
        record["item_sub_category_ko"] = (
            translate_code(self.item_sub_category, "item_sub_category")
            if self.item_sub_category
            else None
        )
        return record


@dataclass(frozen=True)
class PlayerMatchDetail:
    player: RegisteredPlayer
    match_id: str
    shard: str
    map_name: str | None
    game_mode: str | None
    match_type: str | None
    created_at_kst: datetime | None
    duration_seconds: int | None
    total_players: int | None
    human_players: int | None
    bot_players: int | None
    roster_id: str | None
    team_id: int | None
    win_place: int | None
    is_chicken: bool
    death_type: str | None
    kills: int
    assists: int
    deaths: int
    dbnos_caused: int
    dbnos_taken: int
    finishes: int
    finishes_taken: int
    damage_dealt: float
    damage_taken: float
    shots_fired: int
    shots_hit: int
    hits_taken: int
    accuracy: float
    headshot_hits: int
    headshot_hits_taken: int
    headshot_kills: int
    headshot_deaths: int
    headshot_dbnos_caused: int
    headshot_dbnos_taken: int
    hit_parts: dict[str, int]
    taken_hit_parts: dict[str, int]
    survival_seconds: float | None
    landing_distance_m: float | None
    movement_distance_m: float | None
    weapons: list[PlayerMatchWeaponStats]
    replay_artifact: ReplayArtifactRecord | None
    accuracy_breakdown: AccuracyBreakdown | None = None
    team_mode: str | None = None
    perspective: str | None = None
    is_custom_match: bool = False
    season_state: str | None = None
    item_summary: dict[str, Any] = field(default_factory=dict)
    items: list[PlayerMatchItemStats] = field(default_factory=list)
    activity_summary: dict[str, Any] = field(default_factory=dict)
    fight_summary: dict[str, Any] = field(default_factory=dict)
    character_hits: int = 0
    vehicle_hits: int = 0
    vehicle_damage_dealt: float = 0.0

    def to_record(self) -> dict[str, Any]:
        return {
            "player": self.player.to_record(),
            "match_id": self.match_id,
            "shard": self.shard,
            "shard_ko": translate_code(self.shard, "shard"),
            "map_name": self.map_name,
            "map_name_ko": translate_code(self.map_name, "map") if self.map_name else None,
            "game_mode": self.game_mode,
            "game_mode_ko": translate_code(self.game_mode, "game_mode") if self.game_mode else None,
            "match_type": self.match_type,
            "match_type_ko": (
                translate_code(self.match_type, "match_type") if self.match_type else None
            ),
            "team_mode": self.team_mode,
            "team_mode_ko": (
                translate_code(self.team_mode, "team_mode") if self.team_mode else None
            ),
            "perspective": self.perspective,
            "perspective_ko": (
                translate_code(self.perspective, "perspective") if self.perspective else None
            ),
            "is_custom_match": self.is_custom_match,
            "season_state": self.season_state,
            "season_state_ko": (
                translate_code(self.season_state, "season_state")
                if self.season_state
                else None
            ),
            "created_at_kst": _datetime_record(self.created_at_kst),
            "duration_seconds": self.duration_seconds,
            "total_players": self.total_players,
            "human_players": self.human_players,
            "bot_players": self.bot_players,
            "roster_id": self.roster_id,
            "team_id": self.team_id,
            "win_place": self.win_place,
            "is_chicken": self.is_chicken,
            "death_type": self.death_type,
            "death_type_ko": (
                translate_code(self.death_type, "death_type") if self.death_type else None
            ),
            "kills": self.kills,
            "assists": self.assists,
            "deaths": self.deaths,
            "dbnos_caused": self.dbnos_caused,
            "dbnos_taken": self.dbnos_taken,
            "finishes": self.finishes,
            "finishes_taken": self.finishes_taken,
            "damage_dealt": self.damage_dealt,
            "damage_taken": self.damage_taken,
            "shots_fired": self.shots_fired,
            "shots_hit": self.shots_hit,
            "character_hits": self.character_hits,
            "vehicle_hits": self.vehicle_hits,
            "vehicle_damage_dealt": self.vehicle_damage_dealt,
            "hits_taken": self.hits_taken,
            "accuracy": self.accuracy,
            "accuracy_breakdown": (
                self.accuracy_breakdown.to_record() if self.accuracy_breakdown else None
            ),
            "headshot_hits": self.headshot_hits,
            "headshot_hits_taken": self.headshot_hits_taken,
            "headshot_kills": self.headshot_kills,
            "headshot_deaths": self.headshot_deaths,
            "headshot_dbnos_caused": self.headshot_dbnos_caused,
            "headshot_dbnos_taken": self.headshot_dbnos_taken,
            "hit_parts": self.hit_parts,
            "taken_hit_parts": self.taken_hit_parts,
            "headshot_hit_rate": _safe_divide(
                self.headshot_hits,
                _character_hit_denominator(
                    self.character_hits,
                    self.vehicle_hits,
                    self.shots_hit,
                ),
            ),
            "headshot_hit_taken_rate": _safe_divide(self.headshot_hits_taken, self.hits_taken),
            "headshot_kill_rate": _safe_divide(self.headshot_kills, self.kills),
            "hit_part_rates": _part_rates(self.hit_parts),
            "taken_hit_part_rates": _part_rates(self.taken_hit_parts),
            "survival_seconds": self.survival_seconds,
            "landing_distance_m": self.landing_distance_m,
            "movement_distance_m": self.movement_distance_m,
            "weapons": [weapon.to_record() for weapon in self.weapons],
            "replay_artifact": self.replay_artifact.to_record() if self.replay_artifact else None,
            "item_summary": self.item_summary,
            "items": [item.to_record() for item in self.items],
            "activity_summary": self.activity_summary,
            "fight_summary": self.fight_summary,
        }


@dataclass(frozen=True)
class PlayerProfileStats:
    player: RegisteredPlayer
    totals: PlayerCombatTotals
    top_weapons: list[PlayerWeaponStats]
    recent_matches: list[PlayerRecentMatch]

    def to_record(self) -> dict[str, Any]:
        return {
            "player": self.player.to_record(),
            "totals": self.totals.to_record(),
            "top_weapons": [weapon.to_record() for weapon in self.top_weapons],
            "recent_matches": [match.to_record() for match in self.recent_matches],
        }


class PlayerStatsService:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def get_profile(
        self,
        *,
        shard: str,
        account_id: str | None = None,
        name: str | None = None,
        guild_id: str | None = None,
        global_scope: bool = False,
        weapon_limit: int = 5,
        recent_limit: int = 5,
    ) -> PlayerProfileStats | None:
        player = self._get_player(
            shard=shard,
            account_id=account_id,
            name=name,
            guild_id=guild_id,
            global_scope=global_scope,
        )
        if player is None:
            return None

        return PlayerProfileStats(
            player=player,
            totals=self._get_totals(player),
            top_weapons=self._get_top_weapons(player, limit=weapon_limit),
            recent_matches=self._get_recent_matches(player, limit=recent_limit),
        )

    def get_lookup_catalog(
        self,
        *,
        shard: str,
        account_id: str | None = None,
        name: str | None = None,
        guild_id: str | None = None,
        global_scope: bool = False,
        match_limit: int = 1000,
    ) -> PlayerLookupCatalog | None:
        player = self._get_player(
            shard=shard,
            account_id=account_id,
            name=name,
            guild_id=guild_id,
            global_scope=global_scope,
        )
        if player is None:
            return None

        weapons = self._get_catalog_weapons(player)
        matches = self._get_catalog_matches(player, limit=match_limit)
        return PlayerLookupCatalog(
            player=player,
            weapons=weapons,
            matches=matches,
            facets=_catalog_facets(matches),
        )

    def get_weapon_detail(
        self,
        *,
        shard: str,
        weapon: str,
        account_id: str | None = None,
        name: str | None = None,
        guild_id: str | None = None,
        global_scope: bool = False,
        recent_limit: int = 5,
        filters: PlayerTrendFilters | None = None,
    ) -> PlayerWeaponDetail | None:
        normalized_filters = (filters or PlayerTrendFilters()).normalized()
        player = self._get_player(
            shard=shard,
            account_id=account_id,
            name=name,
            guild_id=guild_id,
            global_scope=global_scope,
        )
        if player is None:
            return None

        weapon_code = self._resolve_player_weapon_code(player, weapon)
        if weapon_code is None:
            return None

        rows = self._get_weapon_match_rows(player, weapon_code, normalized_filters)
        if not rows:
            return None
        fight_rows = self._get_weapon_fight_rows(player, weapon_code, normalized_filters)

        return _weapon_detail_from_rows(
            player=player,
            weapon_code=weapon_code,
            rows=rows,
            recent_limit=recent_limit,
            filters=normalized_filters,
            fight_rows=fight_rows,
        )

    def get_match_detail(
        self,
        *,
        shard: str,
        match_id: str,
        account_id: str | None = None,
        name: str | None = None,
        guild_id: str | None = None,
        global_scope: bool = False,
        weapon_limit: int = 8,
    ) -> PlayerMatchDetail | None:
        match_id = _required_text(match_id, "match_id")
        if account_id or name:
            player = self._get_player(
                shard=shard,
                account_id=account_id,
                name=name,
                guild_id=guild_id,
                global_scope=global_scope,
            )
        else:
            player = self._get_match_player(
                shard=shard,
                match_id=match_id,
                guild_id=guild_id,
                global_scope=global_scope,
            )
        if player is None:
            return None

        row = self._get_match_detail_row(player, match_id)
        if row is None:
            return None

        shots_fired = _int(row.get("shots_fired"))
        shots_hit = _int(row.get("shots_hit"))
        character_hits = _int_or_fallback(row.get("character_hits"), shots_hit)
        win_place = _optional_int(row.get("win_place"))
        artifacts = list_replay_artifacts(
            self.connection,
            limit=1,
            artifact_type="map_snapshot",
            match_id=match_id,
            account_id=player.account_id,
        )
        weapons = self._get_match_weapons(player, match_id=match_id, limit=max(weapon_limit, 20))
        accuracy_breakdown = self._get_accuracy_breakdown(player, match_id=match_id)
        items = self._get_match_items(player, match_id=match_id)
        item_summary = _match_item_summary(items)
        activity_summary = self._get_match_activity_summary(player, match_id=match_id)
        fight_summary = self._get_match_fight_summary(player, match_id=match_id)

        return PlayerMatchDetail(
            player=player,
            match_id=str(row["match_id"]),
            shard=str(row["shard"]),
            map_name=row.get("map_name"),
            game_mode=row.get("game_mode"),
            match_type=row.get("match_type"),
            team_mode=row.get("team_mode"),
            perspective=row.get("perspective"),
            is_custom_match=bool(row.get("is_custom_match")),
            season_state=row.get("season_state"),
            created_at_kst=row.get("created_at_kst"),
            duration_seconds=_optional_int(row.get("duration_seconds")),
            total_players=_optional_int(row.get("total_players")),
            human_players=_optional_int(row.get("human_players")),
            bot_players=_optional_int(row.get("bot_players")),
            roster_id=row.get("roster_id"),
            team_id=_optional_int(row.get("team_id")),
            win_place=win_place,
            is_chicken=win_place == 1,
            death_type=row.get("death_type"),
            kills=_int(row.get("kills")),
            assists=_int(row.get("assists")),
            deaths=_int(row.get("deaths")),
            dbnos_caused=_int(row.get("dbnos_caused")),
            dbnos_taken=_int(row.get("dbnos_taken")),
            finishes=_int(row.get("finishes")),
            finishes_taken=_int(row.get("finishes_taken")),
            damage_dealt=_float(row.get("damage_dealt")),
            damage_taken=_float(row.get("damage_taken")),
            shots_fired=shots_fired,
            shots_hit=shots_hit,
            character_hits=character_hits,
            vehicle_hits=_int(row.get("vehicle_hits")),
            vehicle_damage_dealt=_float(row.get("vehicle_damage_dealt")),
            hits_taken=_int(row.get("hits_taken")),
            accuracy=accuracy_breakdown.estimated_hit_rate or 0.0,
            headshot_hits=_int(row.get("headshot_hits")),
            headshot_hits_taken=_int(row.get("headshot_hits_taken")),
            headshot_kills=_int(row.get("headshot_kills")),
            headshot_deaths=_int(row.get("headshot_deaths")),
            headshot_dbnos_caused=_int(row.get("headshot_dbnos_caused")),
            headshot_dbnos_taken=_int(row.get("headshot_dbnos_taken")),
            hit_parts=_part_map(row.get("hit_parts")),
            taken_hit_parts=_part_map(row.get("taken_hit_parts")),
            survival_seconds=_survival_seconds_from_row(row),
            landing_distance_m=_optional_float(row.get("landing_distance_m")),
            movement_distance_m=_movement_distance_from_row(row),
            weapons=weapons[: max(1, min(int(weapon_limit), 20))],
            replay_artifact=artifacts[0] if artifacts else None,
            accuracy_breakdown=accuracy_breakdown,
            item_summary=item_summary,
            items=items,
            activity_summary=activity_summary,
            fight_summary=fight_summary,
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
        shard = _required_text(shard, "shard").lower()
        conditions = ["shard = %s"]
        params: list[Any] = [shard]

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

        query = (
            "SELECT id, account_id, shard, current_name, active, public_profile, "
            "registered_by_discord_user_id, registered_guild_id, registered_channel_id "
            "FROM registered_players WHERE "
            + " AND ".join(conditions)
            + " ORDER BY active DESC, updated_at_kst DESC LIMIT 1"
        )

        with self.connection.cursor() as cursor:
            cursor.execute(query, params)
            row = cursor.fetchone()
        return _player_from_row(row) if row else None

    def _get_match_player(
        self,
        *,
        shard: str,
        match_id: str,
        guild_id: str | None,
        global_scope: bool,
    ) -> RegisteredPlayer | None:
        shard = _required_text(shard, "shard").lower()
        conditions = ["matches.shard = %s", "summaries.match_id = %s"]
        params: list[Any] = [shard, match_id]

        if not global_scope:
            if not guild_id:
                return None
            conditions.append(PLAYER_GUILD_SCOPE_CONDITION)
            params.append(guild_id)

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    registered_players.id,
                    registered_players.account_id,
                    registered_players.shard,
                    registered_players.current_name,
                    registered_players.active,
                    registered_players.public_profile,
                    registered_players.registered_by_discord_user_id,
                    registered_players.registered_guild_id,
                    registered_players.registered_channel_id
                FROM player_match_combat_summaries summaries
                INNER JOIN matches
                    ON matches.match_id = summaries.match_id
                INNER JOIN registered_players
                    ON registered_players.account_id = summaries.account_id
                   AND registered_players.shard = matches.shard
                WHERE
                """
                + " AND ".join(conditions)
                + """
                ORDER BY registered_players.active DESC, registered_players.current_name ASC
                LIMIT 1
                """,
                params,
            )
            row = cursor.fetchone()
        return _player_from_row(row) if row else None

    def _get_match_detail_row(self, player: RegisteredPlayer, match_id: str) -> dict[str, Any] | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    matches.match_id,
                    matches.shard,
                    matches.map_name,
                    matches.game_mode,
                    matches.match_type,
                    matches.team_mode,
                    matches.perspective,
                    matches.is_custom_match,
                    matches.season_state,
                    matches.created_at_kst,
                    matches.duration_seconds,
                    matches.total_players,
                    matches.human_players,
                    matches.bot_players,
                    participants.roster_id,
                    participants.team_id,
                    participants.win_place,
                    participants.death_type,
                    participants.raw_stats,
                    summaries.shots_fired,
                    summaries.shots_hit,
                    summaries.character_hits,
                    summaries.vehicle_hits,
                    summaries.vehicle_damage_dealt,
                    summaries.hits_taken,
                    summaries.damage_dealt,
                    summaries.damage_taken,
                    summaries.kills,
                    summaries.assists,
                    summaries.deaths,
                    summaries.dbnos_caused,
                    summaries.dbnos_taken,
                    summaries.finishes,
                    summaries.finishes_taken,
                    summaries.headshot_hits,
                    summaries.headshot_hits_taken,
                    summaries.headshot_kills,
                    summaries.headshot_deaths,
                    summaries.headshot_dbnos_caused,
                    summaries.headshot_dbnos_taken,
                    summaries.hit_parts,
                    summaries.taken_hit_parts,
                    movement.landing_distance_m,
                    movement.in_game_sampled_distance_m
                FROM player_match_combat_summaries summaries
                INNER JOIN matches
                    ON matches.match_id = summaries.match_id
                LEFT JOIN match_participants participants
                    ON participants.match_id = summaries.match_id
                   AND participants.account_id = summaries.account_id
                LEFT JOIN player_movement_summaries movement
                    ON movement.match_id = summaries.match_id
                   AND movement.account_id = summaries.account_id
                WHERE summaries.account_id = %s
                  AND matches.shard = %s
                  AND summaries.match_id = %s
                LIMIT 1
                """,
                (player.account_id, player.shard, match_id),
            )
            row = cursor.fetchone()
        return dict(row) if row else None

    def _get_match_weapons(
        self,
        player: RegisteredPlayer,
        *,
        match_id: str,
        limit: int,
    ) -> list[PlayerMatchWeaponStats]:
        limit = max(1, min(int(limit), 20))
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    weapon_code,
                    shots_fired,
                    shots_hit,
                    character_hits,
                    vehicle_hits,
                    vehicle_damage_dealt,
                    hits_taken,
                    damage_dealt,
                    damage_taken,
                    kills,
                    assists,
                    deaths,
                    dbnos,
                    dbnos_taken,
                    headshot_kills,
                    hit_parts,
                    taken_hit_parts
                FROM player_weapon_match_stats
                WHERE account_id = %s
                  AND match_id = %s
                  AND (
                    shots_fired > 0
                    OR damage_dealt > 0
                    OR damage_taken > 0
                    OR kills > 0
                    OR assists > 0
                    OR dbnos > 0
                    OR dbnos_taken > 0
                  )
                ORDER BY kills DESC, damage_dealt DESC, shots_fired DESC, weapon_code ASC
                LIMIT %s
                """,
                (player.account_id, match_id, limit),
            )
            rows = cursor.fetchall()

        weapons: list[PlayerMatchWeaponStats] = []
        for row in rows:
            weapon_code = str(row["weapon_code"])
            shots_fired = _int(row.get("shots_fired"))
            shots_hit = _int(row.get("shots_hit"))
            metric = weapon_accuracy_metric(weapon_code, shots_fired, shots_hit)
            weapons.append(
                PlayerMatchWeaponStats(
                    weapon_code=weapon_code,
                    weapon_name=translate_code(weapon_code, "damage_causer"),
                    kills=_int(row.get("kills")),
                    assists=_int(row.get("assists")),
                    deaths=_int(row.get("deaths")),
                    dbnos=_int(row.get("dbnos")),
                    dbnos_taken=_int(row.get("dbnos_taken")),
                    damage_dealt=_float(row.get("damage_dealt")),
                    damage_taken=_float(row.get("damage_taken")),
                    shots_fired=shots_fired,
                    shots_hit=shots_hit,
                    character_hits=_int_or_fallback(row.get("character_hits"), shots_hit),
                    vehicle_hits=_int(row.get("vehicle_hits")),
                    vehicle_damage_dealt=_float(row.get("vehicle_damage_dealt")),
                    accuracy=metric.estimated_hit_rate or 0.0,
                    headshot_kills=_int(row.get("headshot_kills")),
                    hit_parts=_part_map(row.get("hit_parts")),
                    taken_hit_parts=_part_map(row.get("taken_hit_parts")),
                    accuracy_metric=metric,
                )
            )
        return weapons

    def _get_match_items(
        self,
        player: RegisteredPlayer,
        *,
        match_id: str,
    ) -> list[PlayerMatchItemStats]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    item_code,
                    item_category,
                    item_sub_category,
                    picked_up_events,
                    picked_up_quantity,
                    loot_box_pickup_events,
                    carepackage_pickup_events,
                    custom_package_pickup_events,
                    vehicle_trunk_pickup_events,
                    vehicle_trunk_put_events,
                    dropped_events,
                    dropped_quantity,
                    used_events,
                    used_quantity,
                    equipped_events,
                    unequipped_events,
                    attached_events,
                    detached_events
                FROM player_item_match_stats
                WHERE account_id = %s
                  AND match_id = %s
                  AND (
                    picked_up_events > 0
                    OR dropped_events > 0
                    OR used_events > 0
                    OR equipped_events > 0
                    OR unequipped_events > 0
                    OR attached_events > 0
                    OR detached_events > 0
                  )
                ORDER BY
                    used_events DESC,
                    picked_up_events DESC,
                    equipped_events DESC,
                    item_code ASC
                """,
                (player.account_id, match_id),
            )
            rows = cursor.fetchall()

        return [
            PlayerMatchItemStats(
                item_code=str(row["item_code"]),
                item_name=translate_code(str(row["item_code"]), "item"),
                item_category=_optional_text(row.get("item_category")),
                item_sub_category=_optional_text(row.get("item_sub_category")),
                picked_up_events=_int(row.get("picked_up_events")),
                picked_up_quantity=_int(row.get("picked_up_quantity")),
                loot_box_pickup_events=_int(row.get("loot_box_pickup_events")),
                carepackage_pickup_events=_int(row.get("carepackage_pickup_events")),
                custom_package_pickup_events=_int(row.get("custom_package_pickup_events")),
                vehicle_trunk_pickup_events=_int(row.get("vehicle_trunk_pickup_events")),
                vehicle_trunk_put_events=_int(row.get("vehicle_trunk_put_events")),
                dropped_events=_int(row.get("dropped_events")),
                dropped_quantity=_int(row.get("dropped_quantity")),
                used_events=_int(row.get("used_events")),
                used_quantity=_int(row.get("used_quantity")),
                equipped_events=_int(row.get("equipped_events")),
                unequipped_events=_int(row.get("unequipped_events")),
                attached_events=_int(row.get("attached_events")),
                detached_events=_int(row.get("detached_events")),
            )
            for row in rows
        ]

    def _get_match_activity_summary(
        self,
        player: RegisteredPlayer,
        *,
        match_id: str,
    ) -> dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    heal_events,
                    heal_amount,
                    item_heal_events,
                    item_heal_amount,
                    passive_heal_events,
                    passive_heal_amount,
                    throwable_uses,
                    flare_uses,
                    revives_caused,
                    revives_received,
                    trauma_bag_revives,
                    carry_events,
                    vehicle_rides,
                    vehicle_leaves,
                    vehicle_distance_m,
                    vehicle_max_speed,
                    vehicle_damage,
                    vehicle_destroys,
                    wheel_destroys,
                    vaults,
                    ledge_grabs,
                    vehicle_vaults,
                    swim_sessions,
                    swim_distance_m,
                    armor_destroys_caused,
                    armor_destroys_taken,
                    object_interactions,
                    object_destroys,
                    emergency_pickup_calls,
                    emergency_pickup_rides,
                    redeploys,
                    normalized_event_count
                FROM player_match_activity_summaries
                WHERE account_id = %s
                  AND match_id = %s
                LIMIT 1
                """,
                (player.account_id, match_id),
            )
            row = cursor.fetchone()
        if not row:
            return {"available": False}
        record = dict(row)
        record["available"] = True
        return record

    def _get_match_fight_summary(
        self,
        player: RegisteredPlayer,
        *,
        match_id: str,
    ) -> dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    COUNT(*) AS fight_count,
                    COALESCE(SUM(outcome_type = 'win'), 0) AS wins,
                    COALESCE(SUM(outcome_type = 'loss'), 0) AS losses,
                    COALESCE(SUM(outcome_reason = 'kill'), 0) AS kill_wins,
                    COALESCE(SUM(outcome_reason = 'dbno_caused'), 0) AS dbno_wins,
                    COALESCE(SUM(outcome_reason = 'death'), 0) AS death_losses,
                    COALESCE(SUM(outcome_reason = 'dbno_taken'), 0) AS dbno_losses,
                    COALESCE(SUM(outcome_type = 'win' AND is_headshot = 1), 0) AS headshot_wins,
                    COALESCE(SUM(opponent_is_bot = 0), 0) AS human_opponent_fights,
                    COALESCE(SUM(opponent_is_bot = 1), 0) AS bot_opponent_fights,
                    COALESCE(SUM(opponent_account_id IS NULL), 0) AS unknown_opponent_fights
                FROM player_fight_outcomes
                WHERE account_id = %s
                  AND match_id = %s
                  AND is_friendly_fire = 0
                """,
                (player.account_id, match_id),
            )
            row = cursor.fetchone()
        record = dict(row or {})
        wins = _int(record.get("wins"))
        losses = _int(record.get("losses"))
        record["fight_count"] = _int(record.get("fight_count"))
        record["wins"] = wins
        record["losses"] = losses
        record["fight_win_rate"] = _safe_divide(wins, wins + losses)
        record["definition"] = (
            "킬·가한 기절·사망·당한 기절 결과를 각각 1회로 계산하며 "
            "아군 피해 결과는 제외합니다."
        )
        return record

    def _get_totals(self, player: RegisteredPlayer) -> PlayerCombatTotals:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    COUNT(DISTINCT summaries.match_id) AS match_count,
                    COALESCE(SUM(CASE WHEN participants.win_place = 1 THEN 1 ELSE 0 END), 0) AS wins,
                    COALESCE(SUM(summaries.kills), 0) AS kills,
                    COALESCE(SUM(summaries.assists), 0) AS assists,
                    COALESCE(SUM(summaries.deaths), 0) AS deaths,
                    COALESCE(SUM(summaries.dbnos_caused), 0) AS dbnos_caused,
                    COALESCE(SUM(summaries.dbnos_taken), 0) AS dbnos_taken,
                    COALESCE(SUM(summaries.damage_dealt), 0) AS damage_dealt,
                    COALESCE(SUM(summaries.damage_taken), 0) AS damage_taken,
                    COALESCE(SUM(summaries.shots_fired), 0) AS shots_fired,
                    COALESCE(SUM(summaries.shots_hit), 0) AS shots_hit,
                    COALESCE(SUM(summaries.character_hits), 0) AS character_hits,
                    COALESCE(SUM(summaries.vehicle_hits), 0) AS vehicle_hits,
                    COALESCE(SUM(summaries.vehicle_damage_dealt), 0) AS vehicle_damage_dealt,
                    COALESCE(SUM(summaries.hits_taken), 0) AS hits_taken,
                    COALESCE(SUM(summaries.headshot_hits), 0) AS headshot_hits,
                    COALESCE(SUM(summaries.headshot_hits_taken), 0) AS headshot_hits_taken,
                    COALESCE(SUM(summaries.headshot_kills), 0) AS headshot_kills,
                    JSON_ARRAYAGG(summaries.hit_parts) AS hit_part_maps,
                    JSON_ARRAYAGG(summaries.taken_hit_parts) AS taken_hit_part_maps,
                    COALESCE(AVG(
                        COALESCE(
                            CAST(JSON_UNQUOTE(JSON_EXTRACT(participants.raw_stats, '$.timeSurvived')) AS DECIMAL(12, 3)),
                            matches.duration_seconds,
                            0
                        )
                    ), 0) AS avg_survival_seconds,
                    COALESCE(AVG(
                        CASE
                            WHEN JSON_EXTRACT(participants.raw_stats, '$.walkDistance') IS NOT NULL
                              OR JSON_EXTRACT(participants.raw_stats, '$.rideDistance') IS NOT NULL
                              OR JSON_EXTRACT(participants.raw_stats, '$.swimDistance') IS NOT NULL
                            THEN
                                COALESCE(CAST(JSON_UNQUOTE(JSON_EXTRACT(participants.raw_stats, '$.walkDistance')) AS DECIMAL(14, 3)), 0)
                              + COALESCE(CAST(JSON_UNQUOTE(JSON_EXTRACT(participants.raw_stats, '$.rideDistance')) AS DECIMAL(14, 3)), 0)
                              + COALESCE(CAST(JSON_UNQUOTE(JSON_EXTRACT(participants.raw_stats, '$.swimDistance')) AS DECIMAL(14, 3)), 0)
                            ELSE COALESCE(movement.in_game_sampled_distance_m, 0)
                        END
                    ), 0) AS avg_movement_distance_m,
                    MIN(matches.created_at_kst) AS first_match_at_kst,
                    MAX(matches.created_at_kst) AS last_match_at_kst
                FROM player_match_combat_summaries summaries
                INNER JOIN matches
                    ON matches.match_id = summaries.match_id
                LEFT JOIN match_participants participants
                    ON participants.match_id = summaries.match_id
                   AND participants.account_id = summaries.account_id
                LEFT JOIN player_movement_summaries movement
                    ON movement.match_id = summaries.match_id
                   AND movement.account_id = summaries.account_id
                WHERE summaries.account_id = %s
                  AND matches.shard = %s
                """,
                (player.account_id, player.shard),
            )
            row = cursor.fetchone() or {}

        match_count = _int(row.get("match_count"))
        kills = _int(row.get("kills"))
        assists = _int(row.get("assists"))
        deaths = _int(row.get("deaths"))
        shots_fired = _int(row.get("shots_fired"))
        shots_hit = _int(row.get("shots_hit"))
        character_hits = _int_or_fallback(row.get("character_hits"), shots_hit)
        hits_taken = _int(row.get("hits_taken"))
        headshot_hits = _int(row.get("headshot_hits"))
        headshot_hits_taken = _int(row.get("headshot_hits_taken"))
        headshot_kills = _int(row.get("headshot_kills"))
        damage_dealt = _float(row.get("damage_dealt"))
        damage_taken = _float(row.get("damage_taken"))
        accuracy_breakdown = self._get_accuracy_breakdown(player)

        return PlayerCombatTotals(
            match_count=match_count,
            wins=_int(row.get("wins")),
            kills=kills,
            assists=assists,
            deaths=deaths,
            dbnos_caused=_int(row.get("dbnos_caused")),
            dbnos_taken=_int(row.get("dbnos_taken")),
            damage_dealt=damage_dealt,
            damage_taken=damage_taken,
            shots_fired=shots_fired,
            shots_hit=shots_hit,
            character_hits=character_hits,
            vehicle_hits=_int(row.get("vehicle_hits")),
            vehicle_damage_dealt=_float(row.get("vehicle_damage_dealt")),
            headshot_kills=headshot_kills,
            avg_damage_dealt=_safe_divide(damage_dealt, match_count),
            avg_damage_taken=_safe_divide(damage_taken, match_count),
            win_rate=_safe_divide(_int(row.get("wins")), match_count),
            kda=_safe_divide(kills + assists, deaths if deaths > 0 else 1),
            accuracy=accuracy_breakdown.estimated_hit_rate or 0.0,
            headshot_kill_rate=_safe_divide(headshot_kills, kills),
            avg_survival_seconds=_float(row.get("avg_survival_seconds")),
            avg_movement_distance_m=_float(row.get("avg_movement_distance_m")),
            first_match_at_kst=row.get("first_match_at_kst"),
            last_match_at_kst=row.get("last_match_at_kst"),
            accuracy_breakdown=accuracy_breakdown,
            hits_taken=hits_taken,
            headshot_hits=headshot_hits,
            headshot_hits_taken=headshot_hits_taken,
            headshot_hit_rate=_safe_divide(headshot_hits, character_hits),
            headshot_hit_taken_rate=_safe_divide(headshot_hits_taken, hits_taken),
            hit_parts=_sum_json_part_maps(row.get("hit_part_maps")),
            taken_hit_parts=_sum_json_part_maps(row.get("taken_hit_part_maps")),
        )

    def _get_accuracy_breakdown(
        self,
        player: RegisteredPlayer,
        *,
        match_id: str | None = None,
    ) -> AccuracyBreakdown:
        match_condition = ""
        params: list[Any] = [player.account_id, player.shard]
        if match_id is not None:
            match_condition = "AND weapon_stats.match_id = %s"
            params.append(match_id)

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    weapon_stats.weapon_code,
                    COALESCE(SUM(weapon_stats.shots_fired), 0) AS shots_fired,
                    COALESCE(SUM(weapon_stats.shots_hit), 0) AS shots_hit
                FROM player_weapon_match_stats weapon_stats
                INNER JOIN matches
                    ON matches.match_id = weapon_stats.match_id
                WHERE weapon_stats.account_id = %s
                  AND matches.shard = %s
                """
                + match_condition
                + """
                GROUP BY weapon_stats.weapon_code
                """,
                params,
            )
            rows = cursor.fetchall()
        return summarize_accuracy_rows(rows)

    def _get_top_weapons(self, player: RegisteredPlayer, *, limit: int) -> list[PlayerWeaponStats]:
        limit = max(1, min(int(limit), 20))
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    weapon_stats.weapon_code,
                    COUNT(DISTINCT weapon_stats.match_id) AS match_count,
                    COALESCE(SUM(weapon_stats.kills), 0) AS kills,
                    COALESCE(SUM(weapon_stats.assists), 0) AS assists,
                    COALESCE(SUM(weapon_stats.deaths), 0) AS deaths,
                    COALESCE(SUM(weapon_stats.dbnos), 0) AS dbnos,
                    COALESCE(SUM(weapon_stats.damage_dealt), 0) AS damage_dealt,
                    COALESCE(SUM(weapon_stats.shots_fired), 0) AS shots_fired,
                    COALESCE(SUM(weapon_stats.shots_hit), 0) AS shots_hit,
                    COALESCE(SUM(weapon_stats.character_hits), 0) AS character_hits,
                    COALESCE(SUM(weapon_stats.vehicle_hits), 0) AS vehicle_hits,
                    COALESCE(SUM(weapon_stats.vehicle_damage_dealt), 0) AS vehicle_damage_dealt,
                    COALESCE(SUM(weapon_stats.headshot_hits), 0) AS headshot_hits,
                    COALESCE(SUM(weapon_stats.headshot_kills), 0) AS headshot_kills
                FROM player_weapon_match_stats weapon_stats
                INNER JOIN matches
                    ON matches.match_id = weapon_stats.match_id
                WHERE weapon_stats.account_id = %s
                  AND matches.shard = %s
                GROUP BY weapon_stats.weapon_code
                HAVING shots_fired > 0 OR damage_dealt > 0 OR kills > 0 OR dbnos > 0
                ORDER BY kills DESC, damage_dealt DESC, shots_fired DESC, weapon_stats.weapon_code ASC
                LIMIT %s
                """,
                (player.account_id, player.shard, limit),
            )
            rows = cursor.fetchall()

        weapons: list[PlayerWeaponStats] = []
        for row in rows:
            weapon_code = str(row["weapon_code"])
            shots_fired = _int(row.get("shots_fired"))
            shots_hit = _int(row.get("shots_hit"))
            character_hits = _int_or_fallback(row.get("character_hits"), shots_hit)
            metric = weapon_accuracy_metric(weapon_code, shots_fired, shots_hit)
            weapons.append(
                PlayerWeaponStats(
                    weapon_code=weapon_code,
                    weapon_name=translate_code(weapon_code, "damage_causer"),
                    match_count=_int(row.get("match_count")),
                    kills=_int(row.get("kills")),
                    assists=_int(row.get("assists")),
                    deaths=_int(row.get("deaths")),
                    dbnos=_int(row.get("dbnos")),
                    damage_dealt=_float(row.get("damage_dealt")),
                    shots_fired=shots_fired,
                    shots_hit=shots_hit,
                    character_hits=character_hits,
                    vehicle_hits=_int(row.get("vehicle_hits")),
                    vehicle_damage_dealt=_float(row.get("vehicle_damage_dealt")),
                    accuracy=metric.estimated_hit_rate or 0.0,
                    headshot_hits=_int(row.get("headshot_hits")),
                    headshot_kills=_int(row.get("headshot_kills")),
                    accuracy_metric=metric,
                )
            )
        return weapons

    def _get_catalog_weapons(self, player: RegisteredPlayer) -> list[PlayerCatalogWeapon]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    weapon_stats.weapon_code,
                    COUNT(DISTINCT weapon_stats.match_id) AS match_count
                FROM player_weapon_match_stats weapon_stats
                INNER JOIN matches
                    ON matches.match_id = weapon_stats.match_id
                WHERE weapon_stats.account_id = %s
                  AND matches.shard = %s
                  AND (
                    weapon_stats.shots_fired > 0
                    OR weapon_stats.damage_dealt > 0
                    OR weapon_stats.kills > 0
                    OR weapon_stats.assists > 0
                    OR weapon_stats.dbnos > 0
                  )
                GROUP BY weapon_stats.weapon_code
                ORDER BY match_count DESC, weapon_stats.weapon_code ASC
                """,
                (player.account_id, player.shard),
            )
            rows = cursor.fetchall()

        return [
            PlayerCatalogWeapon(
                weapon_code=str(row["weapon_code"]),
                weapon_name=translate_code(str(row["weapon_code"]), "damage_causer"),
                weapon_family=weapon_family(str(row["weapon_code"])),
                match_count=_int(row.get("match_count")),
            )
            for row in rows
        ]

    def _get_catalog_matches(
        self,
        player: RegisteredPlayer,
        *,
        limit: int,
    ) -> list[PlayerCatalogMatch]:
        normalized_limit = max(1, min(int(limit), 5000))
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    matches.match_id,
                    matches.created_at_kst,
                    matches.map_name,
                    matches.game_mode,
                    matches.team_mode,
                    matches.perspective,
                    matches.match_type,
                    matches.season_state,
                    participants.win_place,
                    summaries.kills,
                    summaries.assists,
                    summaries.deaths,
                    summaries.dbnos_caused,
                    summaries.damage_dealt
                FROM player_match_combat_summaries summaries
                INNER JOIN matches
                    ON matches.match_id = summaries.match_id
                LEFT JOIN match_participants participants
                    ON participants.match_id = summaries.match_id
                   AND participants.account_id = summaries.account_id
                WHERE summaries.account_id = %s
                  AND matches.shard = %s
                ORDER BY matches.created_at_kst DESC, matches.match_id DESC
                LIMIT %s
                """,
                (player.account_id, player.shard, normalized_limit),
            )
            rows = cursor.fetchall()

        return [
            PlayerCatalogMatch(
                match_id=str(row["match_id"]),
                created_at_kst=row.get("created_at_kst"),
                map_name=row.get("map_name"),
                game_mode=row.get("game_mode"),
                team_mode=row.get("team_mode"),
                perspective=row.get("perspective"),
                match_type=row.get("match_type"),
                season_state=row.get("season_state"),
                win_place=_optional_int(row.get("win_place")),
                kills=_int(row.get("kills")),
                assists=_int(row.get("assists")),
                deaths=_int(row.get("deaths")),
                dbnos_caused=_int(row.get("dbnos_caused")),
                damage_dealt=_float(row.get("damage_dealt")),
            )
            for row in rows
        ]

    def _resolve_player_weapon_code(self, player: RegisteredPlayer, weapon: str) -> str | None:
        requested = _required_text(weapon, "weapon")
        direct_code = weapon_code_from_identifier(requested)

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT weapon_stats.weapon_code
                FROM player_weapon_match_stats weapon_stats
                INNER JOIN matches
                    ON matches.match_id = weapon_stats.match_id
                WHERE weapon_stats.account_id = %s
                  AND matches.shard = %s
                GROUP BY weapon_stats.weapon_code
                """,
                (player.account_id, player.shard),
            )
            rows = cursor.fetchall()

        available_codes = [str(row["weapon_code"]) for row in rows]
        if direct_code in available_codes:
            return direct_code

        normalized_request = _normalize_weapon_text(requested)
        for weapon_code in available_codes:
            if normalized_request in {
                _normalize_weapon_text(weapon_code),
                _normalize_weapon_text(translate_code(weapon_code, "damage_causer")),
            }:
                return weapon_code

        return direct_code

    def _get_weapon_match_rows(
        self,
        player: RegisteredPlayer,
        weapon_code: str,
        filters: PlayerTrendFilters,
    ) -> list[dict[str, Any]]:
        conditions = [
            "weapon_stats.account_id = %s",
            "matches.shard = %s",
            "weapon_stats.weapon_code = %s",
            """(
                weapon_stats.shots_fired > 0
                OR weapon_stats.damage_dealt > 0
                OR weapon_stats.kills > 0
                OR weapon_stats.assists > 0
                OR weapon_stats.dbnos > 0
            )""",
        ]
        params: list[Any] = [player.account_id, player.shard, weapon_code]
        _append_match_filters(conditions, params, filters)

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    weapon_stats.match_id,
                    weapon_stats.shots_fired,
                    weapon_stats.shots_hit,
                    weapon_stats.character_hits,
                    weapon_stats.vehicle_hits,
                    weapon_stats.vehicle_damage_dealt,
                    weapon_stats.hits_taken,
                    weapon_stats.damage_dealt,
                    weapon_stats.damage_taken,
                    weapon_stats.kills,
                    weapon_stats.assists,
                    weapon_stats.deaths,
                    weapon_stats.dbnos,
                    weapon_stats.dbnos_taken,
                    weapon_stats.finishes,
                    weapon_stats.finishes_taken,
                    weapon_stats.headshot_hits,
                    weapon_stats.headshot_hits_taken,
                    weapon_stats.headshot_kills,
                    weapon_stats.headshot_deaths,
                    weapon_stats.headshot_dbnos,
                    weapon_stats.headshot_dbnos_taken,
                    weapon_stats.hit_parts,
                    weapon_stats.taken_hit_parts,
                    matches.created_at_kst,
                    matches.map_name,
                    matches.game_mode,
                    matches.team_mode,
                    matches.perspective,
                    matches.match_type,
                    matches.season_state,
                    participants.win_place
                FROM player_weapon_match_stats weapon_stats
                INNER JOIN matches
                    ON matches.match_id = weapon_stats.match_id
                LEFT JOIN match_participants participants
                    ON participants.match_id = weapon_stats.match_id
                   AND participants.account_id = weapon_stats.account_id
                WHERE
                """
                + " AND ".join(conditions)
                + """
                ORDER BY matches.created_at_kst DESC, weapon_stats.match_id DESC
                """,
                params,
            )
            return list(cursor.fetchall())

    def _get_weapon_fight_rows(
        self,
        player: RegisteredPlayer,
        weapon_code: str,
        filters: PlayerTrendFilters,
    ) -> list[dict[str, Any]]:
        conditions = [
            "outcomes.account_id = %s",
            "matches.shard = %s",
            "outcomes.weapon_code = %s",
            "outcomes.is_friendly_fire = 0",
        ]
        params: list[Any] = [player.account_id, player.shard, weapon_code]
        _append_match_filters(conditions, params, filters)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    outcomes.match_id,
                    outcomes.outcome_type,
                    outcomes.distance_m
                FROM player_fight_outcomes outcomes
                INNER JOIN matches
                    ON matches.match_id = outcomes.match_id
                WHERE
                """
                + " AND ".join(conditions)
                + """
                ORDER BY matches.created_at_kst DESC, outcomes.event_index DESC
                """,
                params,
            )
            return list(cursor.fetchall())

    def _get_recent_matches(self, player: RegisteredPlayer, *, limit: int) -> list[PlayerRecentMatch]:
        limit = max(1, min(int(limit), 20))
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    matches.match_id,
                    matches.created_at_kst,
                    matches.map_name,
                    matches.game_mode,
                    matches.match_type,
                    matches.duration_seconds,
                    participants.win_place,
                    participants.raw_stats,
                    summaries.kills,
                    summaries.assists,
                    summaries.deaths,
                    summaries.dbnos_caused,
                    summaries.damage_dealt,
                    movement.in_game_sampled_distance_m
                FROM player_match_combat_summaries summaries
                INNER JOIN matches
                    ON matches.match_id = summaries.match_id
                LEFT JOIN match_participants participants
                    ON participants.match_id = summaries.match_id
                   AND participants.account_id = summaries.account_id
                LEFT JOIN player_movement_summaries movement
                    ON movement.match_id = summaries.match_id
                   AND movement.account_id = summaries.account_id
                WHERE summaries.account_id = %s
                  AND matches.shard = %s
                ORDER BY matches.created_at_kst DESC, summaries.match_id DESC
                LIMIT %s
                """,
                (player.account_id, player.shard, limit),
            )
            rows = cursor.fetchall()

        return [
            PlayerRecentMatch(
                match_id=str(row["match_id"]),
                created_at_kst=row.get("created_at_kst"),
                map_name=row.get("map_name"),
                game_mode=row.get("game_mode"),
                match_type=row.get("match_type"),
                win_place=_optional_int(row.get("win_place")),
                kills=_int(row.get("kills")),
                assists=_int(row.get("assists")),
                deaths=_int(row.get("deaths")),
                dbnos_caused=_int(row.get("dbnos_caused")),
                damage_dealt=_float(row.get("damage_dealt")),
                survival_seconds=_survival_seconds_from_row(row),
                movement_distance_m=_movement_distance_from_row(row),
            )
            for row in rows
        ]


def weapon_code_from_identifier(value: str) -> str:
    normalized = _normalize_weapon_text(value)
    if normalized in WEAPON_ALIASES:
        return WEAPON_ALIASES[normalized]
    return normalize_weapon_code(value) or value.strip()


def _weapon_totals_from_rows(
    weapon_code: str,
    rows: list[dict[str, Any]],
    fight_rows: list[dict[str, Any]],
) -> PlayerWeaponDetailTotals:
    match_count = len({str(row["match_id"]) for row in rows})
    wins = sum(1 for row in rows if _optional_int(row.get("win_place")) == 1)
    kills = sum(_int(row.get("kills")) for row in rows)
    assists = sum(_int(row.get("assists")) for row in rows)
    deaths_taken = sum(_int(row.get("deaths")) for row in rows)
    dbnos = sum(_int(row.get("dbnos")) for row in rows)
    dbnos_taken = sum(_int(row.get("dbnos_taken")) for row in rows)
    finishes = sum(_int(row.get("finishes")) for row in rows)
    finishes_taken = sum(_int(row.get("finishes_taken")) for row in rows)
    damage_dealt = sum(_float(row.get("damage_dealt")) for row in rows)
    damage_taken = sum(_float(row.get("damage_taken")) for row in rows)
    shots_fired = sum(_int(row.get("shots_fired")) for row in rows)
    shots_hit = sum(_int(row.get("shots_hit")) for row in rows)
    character_hits = sum(
        _int_or_fallback(row.get("character_hits"), _int(row.get("shots_hit")))
        for row in rows
    )
    vehicle_hits = sum(_int(row.get("vehicle_hits")) for row in rows)
    vehicle_damage_dealt = sum(_float(row.get("vehicle_damage_dealt")) for row in rows)
    hits_taken = sum(_int(row.get("hits_taken")) for row in rows)
    headshot_hits = sum(_int(row.get("headshot_hits")) for row in rows)
    headshot_kills = sum(_int(row.get("headshot_kills")) for row in rows)
    headshot_dbnos = sum(_int(row.get("headshot_dbnos")) for row in rows)
    hit_parts = _sum_part_maps(row.get("hit_parts") for row in rows)
    taken_hit_parts = _sum_part_maps(row.get("taken_hit_parts") for row in rows)
    accuracy_metric = weapon_accuracy_metric(weapon_code, shots_fired, shots_hit)
    fight_wins = sum(str(row.get("outcome_type")) == "win" for row in fight_rows)
    fight_losses = sum(str(row.get("outcome_type")) == "loss" for row in fight_rows)
    fight_count = fight_wins + fight_losses
    return PlayerWeaponDetailTotals(
        match_count=match_count,
        wins=wins,
        kills=kills,
        assists=assists,
        deaths_taken=deaths_taken,
        dbnos=dbnos,
        dbnos_taken=dbnos_taken,
        finishes=finishes,
        finishes_taken=finishes_taken,
        damage_dealt=damage_dealt,
        damage_taken=damage_taken,
        shots_fired=shots_fired,
        shots_hit=shots_hit,
        character_hits=character_hits,
        vehicle_hits=vehicle_hits,
        vehicle_damage_dealt=vehicle_damage_dealt,
        hits_taken=hits_taken,
        headshot_hits=headshot_hits,
        headshot_kills=headshot_kills,
        headshot_dbnos=headshot_dbnos,
        accuracy=accuracy_metric.estimated_hit_rate or 0.0,
        avg_damage_dealt=_safe_divide(damage_dealt, match_count),
        win_rate=_safe_divide(wins, match_count),
        headshot_kill_rate=_safe_divide(headshot_kills, kills),
        hit_parts=hit_parts,
        taken_hit_parts=taken_hit_parts,
        accuracy_metric=accuracy_metric,
        fight_count=fight_count,
        fight_wins=fight_wins,
        fight_losses=fight_losses,
        fight_win_rate=_safe_divide(fight_wins, fight_count),
        avg_fights_per_match=_safe_divide(fight_count, match_count),
    )


def _weapon_trend_series(
    weapon_code: str,
    rows: list[dict[str, Any]],
    fight_rows: list[dict[str, Any]],
    *,
    point_limit: int = 500,
) -> dict[str, PlayerWeaponTrendSeries]:
    fights_by_match: dict[str, list[dict[str, Any]]] = {}
    for fight_row in fight_rows:
        match_id = str(fight_row.get("match_id") or "")
        if match_id:
            fights_by_match.setdefault(match_id, []).append(fight_row)

    result: dict[str, PlayerWeaponTrendSeries] = {}
    for granularity in ("date", "week", "month"):
        grouped: dict[str, tuple[str, list[dict[str, Any]]]] = {}
        for row in rows:
            created_at_kst = row.get("created_at_kst")
            if not isinstance(created_at_kst, datetime):
                continue
            if granularity == "date":
                period_key = created_at_kst.strftime("%Y-%m-%d")
                period_label = period_key
            elif granularity == "week":
                iso_year, iso_week, _ = created_at_kst.isocalendar()
                period_key = f"{iso_year}-W{iso_week:02d}"
                period_label = f"{iso_year}년 {iso_week}주"
            else:
                period_key = created_at_kst.strftime("%Y-%m")
                period_label = created_at_kst.strftime("%Y년 %m월")
            grouped.setdefault(period_key, (period_label, []))[1].append(row)

        points: list[PlayerWeaponTrendPoint] = []
        for period_key in sorted(grouped):
            period_label, period_rows = grouped[period_key]
            period_fights = [
                fight
                for row in period_rows
                for fight in fights_by_match.get(str(row.get("match_id") or ""), [])
            ]
            timestamps = [
                row["created_at_kst"]
                for row in period_rows
                if isinstance(row.get("created_at_kst"), datetime)
            ]
            points.append(
                PlayerWeaponTrendPoint(
                    period_key=period_key,
                    period_label=period_label,
                    first_match_at_kst=min(timestamps),
                    last_match_at_kst=max(timestamps),
                    totals=_weapon_totals_from_rows(
                        weapon_code,
                        period_rows,
                        period_fights,
                    ),
                )
            )
        normalized_limit = max(1, min(int(point_limit), 500))
        available_point_count = len(points)
        selected = points[-normalized_limit:]
        result[granularity] = PlayerWeaponTrendSeries(
            granularity=granularity,
            points=selected,
            available_point_count=available_point_count,
            truncated=available_point_count > len(selected),
        )
    return result


def _weapon_detail_from_rows(
    *,
    player: RegisteredPlayer,
    weapon_code: str,
    rows: list[dict[str, Any]],
    recent_limit: int,
    filters: PlayerTrendFilters,
    fight_rows: list[dict[str, Any]] | None = None,
) -> PlayerWeaponDetail:
    eligible_fights = list(fight_rows or [])
    totals = _weapon_totals_from_rows(weapon_code, rows, eligible_fights)

    return PlayerWeaponDetail(
        player=player,
        weapon_code=weapon_code,
        weapon_name=translate_code(weapon_code, "damage_causer"),
        totals=totals,
        recent_matches=[
            PlayerWeaponRecentMatch(
                match_id=str(row["match_id"]),
                created_at_kst=row.get("created_at_kst"),
                map_name=row.get("map_name"),
                game_mode=row.get("game_mode"),
                win_place=_optional_int(row.get("win_place")),
                kills=_int(row.get("kills")),
                assists=_int(row.get("assists")),
                deaths_taken=_int(row.get("deaths")),
                dbnos=_int(row.get("dbnos")),
                damage_dealt=_float(row.get("damage_dealt")),
                shots_fired=_int(row.get("shots_fired")),
                shots_hit=_int(row.get("shots_hit")),
                accuracy=(
                    weapon_accuracy_metric(
                        weapon_code,
                        _int(row.get("shots_fired")),
                        _int(row.get("shots_hit")),
                    ).estimated_hit_rate
                    or 0.0
                ),
                accuracy_metric=weapon_accuracy_metric(
                    weapon_code,
                    _int(row.get("shots_fired")),
                    _int(row.get("shots_hit")),
                ),
            )
            for row in rows[: max(1, min(int(recent_limit), 20))]
        ],
        filters=filters,
        effective_ranges=_weapon_fight_ranges(weapon_code, eligible_fights),
        trend_series=_weapon_trend_series(
            weapon_code,
            rows,
            eligible_fights,
        ),
    )


def _weapon_fight_ranges(
    weapon_code: str,
    rows: list[dict[str, Any]],
) -> list[PlayerWeaponFightRange]:
    grouped: dict[str, dict[str, Any]] = {}
    family = distance_weapon_family(weapon_code)
    for row in rows:
        distance_m = _optional_float(row.get("distance_m"))
        if distance_m is None or distance_m < 0:
            continue
        bucket = distance_bucket(distance_m, family)
        item = grouped.setdefault(
            bucket.label,
            {
                "bucket": bucket,
                "wins": 0,
                "losses": 0,
                "distance_sum": 0.0,
            },
        )
        outcome_type = str(row.get("outcome_type") or "")
        if outcome_type == "win":
            item["wins"] += 1
        elif outcome_type == "loss":
            item["losses"] += 1
        else:
            continue
        item["distance_sum"] += distance_m

    ranges: list[PlayerWeaponFightRange] = []
    for item in grouped.values():
        wins = _int(item["wins"])
        losses = _int(item["losses"])
        fight_count = wins + losses
        if not fight_count:
            continue
        # Beta(2, 2) smoothing and a 10-fight confidence ramp keep one-off wins
        # from outranking distance bands with repeatable evidence.
        adjusted_rate = _safe_divide(wins + 2, fight_count + 4)
        confidence = min(1.0, sqrt(fight_count / 10))
        bucket = item["bucket"]
        ranges.append(
            PlayerWeaponFightRange(
                bucket_label=bucket.label,
                min_m=bucket.min_m,
                max_m=bucket.max_m,
                fight_count=fight_count,
                wins=wins,
                losses=losses,
                observed_win_rate=_safe_divide(wins, fight_count),
                confidence_adjusted_win_rate=adjusted_rate,
                efficiency_score=adjusted_rate * confidence * 100,
                avg_distance_m=_safe_divide(item["distance_sum"], fight_count),
                reliable_sample=fight_count >= 5,
            )
        )
    ranges.sort(
        key=lambda item: (
            -item.efficiency_score,
            -item.fight_count,
            item.min_m,
        )
    )
    return ranges


def _append_match_filters(
    conditions: list[str],
    params: list[Any],
    filters: PlayerTrendFilters,
) -> None:
    for column, value in (
        ("matches.game_mode", filters.game_mode),
        ("matches.team_mode", filters.team_mode),
        ("matches.perspective", filters.perspective),
        ("matches.match_type", filters.match_type),
        ("matches.map_name", filters.map_name),
        ("matches.season_state", filters.season_state),
    ):
        if value is not None:
            conditions.append(f"{column} = %s")
            params.append(value)

    if filters.is_custom_match is not None:
        conditions.append("matches.is_custom_match = %s")
        params.append(1 if filters.is_custom_match else 0)
    for expression, value in (
        ("YEAR(matches.created_at_kst)", filters.year),
        ("QUARTER(matches.created_at_kst)", filters.quarter),
        ("MONTH(matches.created_at_kst)", filters.month),
        ("HOUR(matches.created_at_kst)", filters.hour),
    ):
        if value is not None:
            conditions.append(f"{expression} = %s")
            params.append(value)
    if filters.exact_date_kst is not None:
        conditions.append("matches.created_at_kst >= %s")
        params.append(datetime.combine(filters.exact_date_kst, time.min))
        conditions.append("matches.created_at_kst < %s")
        params.append(datetime.combine(filters.exact_date_kst + timedelta(days=1), time.min))
    if filters.from_date_kst is not None:
        conditions.append("matches.created_at_kst >= %s")
        params.append(datetime.combine(filters.from_date_kst, time.min))
    if filters.to_date_kst is not None:
        conditions.append("matches.created_at_kst < %s")
        params.append(datetime.combine(filters.to_date_kst + timedelta(days=1), time.min))


def _catalog_facets(matches: list[PlayerCatalogMatch]) -> dict[str, list[Any]]:
    def values(attribute: str) -> list[Any]:
        return sorted(
            {
                value
                for match in matches
                for value in [getattr(match, attribute)]
                if value is not None and value != ""
            }
        )

    years = sorted(
        {
            match.created_at_kst.year
            for match in matches
            if isinstance(match.created_at_kst, datetime)
        },
        reverse=True,
    )
    return {
        "maps": values("map_name"),
        "game_modes": values("game_mode"),
        "team_modes": values("team_mode"),
        "perspectives": values("perspective"),
        "match_types": values("match_type"),
        "season_states": values("season_state"),
        "years": years,
    }


def _player_from_row(row: dict[str, Any]) -> RegisteredPlayer:
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


def _survival_seconds_from_row(row: dict[str, Any]) -> float | None:
    raw_stats = _json_mapping(row.get("raw_stats"))
    survived = _optional_float(raw_stats.get("timeSurvived"))
    if survived is not None:
        return survived
    return _optional_float(row.get("duration_seconds"))


def _movement_distance_from_row(row: dict[str, Any]) -> float | None:
    raw_stats = _json_mapping(row.get("raw_stats"))
    parts = [
        _optional_float(raw_stats.get("walkDistance")),
        _optional_float(raw_stats.get("rideDistance")),
        _optional_float(raw_stats.get("swimDistance")),
    ]
    known_parts = [part for part in parts if part is not None]
    if known_parts:
        return sum(known_parts)
    return _optional_float(row.get("in_game_sampled_distance_m"))


def _datetime_record(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _sum_part_maps(values: Any) -> dict[str, int]:
    totals: dict[str, int] = {}
    for value in values:
        for key, count in _part_map(value).items():
            totals[key] = totals.get(key, 0) + count
    return totals


def _sum_json_part_maps(value: Any) -> dict[str, int]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    if not isinstance(value, list):
        return _part_map(value)
    return _sum_part_maps(value)


def _part_map(value: Any) -> dict[str, int]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): _int(count)
        for key, count in value.items()
        if _int(count) > 0
    }


def _part_rates(parts: Mapping[str, Any]) -> dict[str, float]:
    counts = {
        str(key): _int(value)
        for key, value in parts.items()
        if _int(value) > 0
    }
    total = sum(counts.values())
    if total <= 0:
        return {}
    return {key: count / total for key, count in counts.items()}


def _json_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    if isinstance(value, Mapping):
        return value
    return {}


def _normalize_weapon_text(value: str) -> str:
    return "".join(ch.lower() for ch in value if ch.isalnum())


def _match_item_summary(items: list[PlayerMatchItemStats]) -> dict[str, int]:
    return {
        "unique_item_types": len(items),
        "used_item_types": sum(item.used_events > 0 for item in items),
        "picked_up_events": sum(item.picked_up_events for item in items),
        "picked_up_quantity": sum(item.picked_up_quantity for item in items),
        "loot_box_pickup_events": sum(item.loot_box_pickup_events for item in items),
        "carepackage_pickup_events": sum(item.carepackage_pickup_events for item in items),
        "custom_package_pickup_events": sum(
            item.custom_package_pickup_events for item in items
        ),
        "vehicle_trunk_pickup_events": sum(
            item.vehicle_trunk_pickup_events for item in items
        ),
        "vehicle_trunk_put_events": sum(item.vehicle_trunk_put_events for item in items),
        "dropped_events": sum(item.dropped_events for item in items),
        "dropped_quantity": sum(item.dropped_quantity for item in items),
        "used_events": sum(item.used_events for item in items),
        "used_quantity": sum(item.used_quantity for item in items),
        "equipped_events": sum(item.equipped_events for item in items),
        "unequipped_events": sum(item.unequipped_events for item in items),
        "attached_events": sum(item.attached_events for item in items),
        "detached_events": sum(item.detached_events for item in items),
    }


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required_text(value: str, label: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError(f"{label} is required.")
    return text


def _int(value: Any) -> int:
    if value is None:
        return 0
    return int(value)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _float(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _safe_divide(numerator: float | int, denominator: float | int) -> float:
    if not denominator:
        return 0.0
    return float(numerator) / float(denominator)


def _int_or_fallback(value: Any, fallback: int) -> int:
    return fallback if value is None else _int(value)


def _character_hit_denominator(
    character_hits: int,
    vehicle_hits: int,
    shots_hit: int,
) -> int:
    if character_hits > 0 or vehicle_hits > 0:
        return character_hits
    return shots_hit


WEAPON_ALIASES = {
    "m416": "WeapHK416_C",
    "hk416": "WeapHK416_C",
    "beryl": "WeapBerylM762_C",
    "m762": "WeapBerylM762_C",
    "akm": "WeapAK47_C",
    "ak47": "WeapAK47_C",
    "aug": "WeapAUG_C",
    "ace32": "WeapACE32_C",
    "scar": "WeapSCAR-L_C",
    "scarl": "WeapSCAR-L_C",
    "famas": "WeapFAMASG2_C",
    "mini": "WeapMini14_C",
    "mini14": "WeapMini14_C",
    "mk12": "WeapMk12_C",
    "mk14": "WeapMk14_C",
    "slr": "WeapFNFal_C",
    "sks": "WeapSKS_C",
    "dragunov": "WeapDragunov_C",
    "kar98": "WeapKar98k_C",
    "kar98k": "WeapKar98k_C",
    "m24": "WeapM24_C",
    "awm": "WeapAWM_C",
    "ump": "WeapUMP_C",
    "ump9": "WeapUMP_C",
    "vector": "WeapVector_C",
    "uzi": "WeapUZI_C",
    "mp5": "WeapMP5K_C",
    "mp5k": "WeapMP5K_C",
    "p90": "WeapP90_C",
    "rpd": "WeapRPD_C",
}
