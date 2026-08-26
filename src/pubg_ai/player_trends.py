from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable, Literal, Mapping
import json

from pubg_ai.code_translator import translate_code
from pubg_ai.player_registry import RegisteredPlayer
from pubg_ai.player_scope import PLAYER_GUILD_SCOPE_CONDITION
from pubg_ai.weapon_accuracy import AccuracyBreakdown, summarize_accuracy_rows


TrendGranularity = Literal[
    "hour",
    "date",
    "week",
    "month",
    "quarter",
    "year",
    "map",
    "weapon",
    "game_mode",
    "team_mode",
    "perspective",
    "match_type",
    "season_state",
]


@dataclass(frozen=True)
class PlayerTrendFilters:
    game_mode: str | None = None
    team_mode: str | None = None
    perspective: str | None = None
    match_type: str | None = None
    map_name: str | None = None
    season_state: str | None = None
    is_custom_match: bool | None = None
    year: int | None = None
    quarter: int | None = None
    month: int | None = None
    exact_date_kst: date | None = None
    hour: int | None = None
    from_date_kst: date | None = None
    to_date_kst: date | None = None

    def normalized(self) -> "PlayerTrendFilters":
        team_mode = _optional_text(self.team_mode)
        if team_mode is not None:
            team_mode = team_mode.lower()
            if team_mode not in {"solo", "duo", "squad", "unknown"}:
                raise ValueError("team_mode must be solo, duo, squad, or unknown.")

        perspective = _optional_text(self.perspective)
        if perspective is not None:
            perspective = perspective.lower()
            if perspective not in {"fpp", "tpp", "unknown"}:
                raise ValueError("perspective must be fpp, tpp, or unknown.")

        if self.from_date_kst and self.to_date_kst and self.from_date_kst > self.to_date_kst:
            raise ValueError("from_date_kst must be on or before to_date_kst.")
        year = _bounded_int(self.year, "year", 2000, 2100)
        quarter = _bounded_int(self.quarter, "quarter", 1, 4)
        month = _bounded_int(self.month, "month", 1, 12)
        hour = _bounded_int(self.hour, "hour", 0, 23)

        return PlayerTrendFilters(
            game_mode=_optional_text(self.game_mode),
            team_mode=team_mode,
            perspective=perspective,
            match_type=_optional_text(self.match_type),
            map_name=_optional_text(self.map_name),
            season_state=_optional_text(self.season_state),
            is_custom_match=self.is_custom_match,
            year=year,
            quarter=quarter,
            month=month,
            exact_date_kst=self.exact_date_kst,
            hour=hour,
            from_date_kst=self.from_date_kst,
            to_date_kst=self.to_date_kst,
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "game_mode": self.game_mode,
            "team_mode": self.team_mode,
            "perspective": self.perspective,
            "match_type": self.match_type,
            "map_name": self.map_name,
            "season_state": self.season_state,
            "is_custom_match": self.is_custom_match,
            "year": self.year,
            "quarter": self.quarter,
            "month": self.month,
            "exact_date_kst": self.exact_date_kst.isoformat() if self.exact_date_kst else None,
            "hour": self.hour,
            "from_date_kst": self.from_date_kst.isoformat() if self.from_date_kst else None,
            "to_date_kst": self.to_date_kst.isoformat() if self.to_date_kst else None,
        }


@dataclass(frozen=True)
class PlayerTrendMetrics:
    match_count: int
    wins: int
    non_wins: int
    win_rate: float
    kills: int
    assists: int
    deaths: int
    kda: float
    dbnos_caused: int
    dbnos_taken: int
    damage_dealt: float
    damage_taken: float
    avg_damage_dealt: float
    avg_damage_taken: float
    shots_fired: int
    shots_hit: int
    accuracy: float
    headshot_kills: int
    headshot_kill_rate: float
    avg_survival_seconds: float
    avg_movement_distance_m: float
    accuracy_breakdown: AccuracyBreakdown | None = None
    hits_taken: int = 0
    headshot_hits: int = 0
    headshot_hits_taken: int = 0
    headshot_hit_rate: float = 0.0
    headshot_hit_taken_rate: float = 0.0
    character_hits: int = 0
    vehicle_hits: int = 0
    vehicle_damage_dealt: float = 0.0
    hit_parts: dict[str, int] = field(default_factory=dict)
    taken_hit_parts: dict[str, int] = field(default_factory=dict)
    hit_part_rates: dict[str, float] = field(default_factory=dict)
    taken_hit_part_rates: dict[str, float] = field(default_factory=dict)
    avg_kills: float = 0.0
    avg_assists: float = 0.0
    avg_deaths: float = 0.0
    avg_dbnos_caused: float = 0.0
    avg_dbnos_taken: float = 0.0
    fight_count: int = 0
    fight_wins: int = 0
    fight_losses: int = 0
    fight_win_rate: float = 0.0
    avg_fights_per_match: float = 0.0

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PlayerTrendBucket:
    period_key: str
    period_label: str
    first_match_at_kst: datetime
    last_match_at_kst: datetime
    metrics: PlayerTrendMetrics

    def to_record(self) -> dict[str, Any]:
        return {
            "period_key": self.period_key,
            "period_label": self.period_label,
            "first_match_at_kst": self.first_match_at_kst.isoformat(),
            "last_match_at_kst": self.last_match_at_kst.isoformat(),
            **self.metrics.to_record(),
        }


@dataclass(frozen=True)
class PlayerTrendReport:
    player: RegisteredPlayer
    granularity: TrendGranularity
    timezone: str
    filters: PlayerTrendFilters
    totals: PlayerTrendMetrics
    buckets: list[PlayerTrendBucket]
    available_bucket_count: int
    truncated: bool

    def to_record(self) -> dict[str, Any]:
        return {
            "player": self.player.to_record(),
            "granularity": self.granularity,
            "timezone": self.timezone,
            "filters": self.filters.to_record(),
            "totals": self.totals.to_record(),
            "buckets": [bucket.to_record() for bucket in self.buckets],
            "available_bucket_count": self.available_bucket_count,
            "returned_bucket_count": len(self.buckets),
            "truncated": self.truncated,
        }


class PlayerTrendService:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def get_report(
        self,
        *,
        shard: str,
        granularity: str = "month",
        filters: PlayerTrendFilters | None = None,
        account_id: str | None = None,
        name: str | None = None,
        guild_id: str | None = None,
        global_scope: bool = False,
        bucket_limit: int = 120,
    ) -> PlayerTrendReport | None:
        normalized_granularity = normalize_trend_granularity(granularity)
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

        rows = self._get_rows(player, normalized_filters)
        summary = summarize_player_trends(
            rows,
            granularity="month" if normalized_granularity == "weapon" else normalized_granularity,
            bucket_limit=bucket_limit,
        )
        totals = summary.totals
        if normalized_granularity == "weapon":
            weapon_rows = self._get_weapon_dimension_rows(player, rows)
            summary = summarize_player_trends(
                weapon_rows,
                granularity=normalized_granularity,
                bucket_limit=bucket_limit,
            )
        return PlayerTrendReport(
            player=player,
            granularity=normalized_granularity,
            timezone="Asia/Seoul",
            filters=normalized_filters,
            totals=totals,
            buckets=summary.buckets,
            available_bucket_count=summary.available_bucket_count,
            truncated=summary.truncated,
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

    def _get_rows(
        self,
        player: RegisteredPlayer,
        filters: PlayerTrendFilters,
    ) -> list[dict[str, Any]]:
        conditions = [
            "summaries.account_id = %s",
            "matches.shard = %s",
            "matches.created_at_kst IS NOT NULL",
        ]
        params: list[Any] = [player.account_id, player.shard]
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

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    summaries.match_id,
                    matches.created_at_kst,
                    matches.duration_seconds,
                    matches.game_mode,
                    matches.team_mode,
                    matches.perspective,
                    matches.match_type,
                    matches.map_name,
                    matches.season_state,
                    matches.is_custom_match,
                    participants.win_place,
                    participants.raw_stats,
                    summaries.kills,
                    summaries.assists,
                    summaries.deaths,
                    summaries.dbnos_caused,
                    summaries.dbnos_taken,
                    summaries.damage_dealt,
                    summaries.damage_taken,
                    summaries.shots_fired,
                    summaries.shots_hit,
                    summaries.character_hits,
                    summaries.vehicle_hits,
                    summaries.vehicle_damage_dealt,
                    summaries.hits_taken,
                    summaries.headshot_hits,
                    summaries.headshot_hits_taken,
                    summaries.headshot_kills,
                    summaries.hit_parts,
                    summaries.taken_hit_parts,
                    CASE
                        WHEN JSON_EXTRACT(participants.raw_stats, '$.walkDistance') IS NOT NULL
                          OR JSON_EXTRACT(participants.raw_stats, '$.rideDistance') IS NOT NULL
                          OR JSON_EXTRACT(participants.raw_stats, '$.swimDistance') IS NOT NULL
                        THEN
                            COALESCE(CAST(JSON_UNQUOTE(JSON_EXTRACT(participants.raw_stats, '$.walkDistance')) AS DECIMAL(14, 3)), 0)
                          + COALESCE(CAST(JSON_UNQUOTE(JSON_EXTRACT(participants.raw_stats, '$.rideDistance')) AS DECIMAL(14, 3)), 0)
                          + COALESCE(CAST(JSON_UNQUOTE(JSON_EXTRACT(participants.raw_stats, '$.swimDistance')) AS DECIMAL(14, 3)), 0)
                        ELSE COALESCE(movement.in_game_sampled_distance_m, 0)
                    END AS movement_distance_m
                FROM player_match_combat_summaries summaries
                INNER JOIN analysis_matches AS matches
                    ON matches.match_id = summaries.match_id
                LEFT JOIN match_participants participants
                    ON participants.match_id = summaries.match_id
                   AND participants.account_id = summaries.account_id
                LEFT JOIN player_movement_summaries movement
                    ON movement.match_id = summaries.match_id
                   AND movement.account_id = summaries.account_id
                WHERE
                """
                + " AND ".join(conditions)
                + " ORDER BY matches.created_at_kst ASC, summaries.match_id ASC",
                params,
            )
            rows = [dict(row) for row in cursor.fetchall()]

        if not rows:
            return []
        match_ids = [str(row["match_id"]) for row in rows]
        placeholders = ", ".join(["%s"] * len(match_ids))
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    weapon_stats.match_id,
                    weapon_stats.weapon_code,
                    COALESCE(SUM(weapon_stats.shots_fired), 0) AS shots_fired,
                    COALESCE(SUM(weapon_stats.shots_hit), 0) AS shots_hit
                FROM player_weapon_match_stats weapon_stats
                INNER JOIN analysis_matches AS matches
                    ON matches.match_id = weapon_stats.match_id
                WHERE weapon_stats.account_id = %s
                  AND matches.shard = %s
                  AND weapon_stats.match_id IN (
                """
                + placeholders
                + """
                  )
                GROUP BY weapon_stats.match_id, weapon_stats.weapon_code
                """,
                [player.account_id, player.shard, *match_ids],
            )
            accuracy_rows = cursor.fetchall()

        accuracy_by_match: dict[str, list[dict[str, Any]]] = {}
        for accuracy_row in accuracy_rows:
            accuracy_by_match.setdefault(str(accuracy_row["match_id"]), []).append(accuracy_row)

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    outcomes.match_id,
                    COUNT(*) AS fight_count,
                    COALESCE(SUM(outcomes.outcome_type = 'win'), 0) AS fight_wins,
                    COALESCE(SUM(outcomes.outcome_type = 'loss'), 0) AS fight_losses
                FROM player_fight_outcomes outcomes
                INNER JOIN analysis_matches AS matches
                    ON matches.match_id = outcomes.match_id
                WHERE outcomes.account_id = %s
                  AND matches.shard = %s
                  AND outcomes.is_friendly_fire = 0
                  AND outcomes.match_id IN (
                """
                + placeholders
                + """
                  )
                GROUP BY outcomes.match_id
                """,
                [player.account_id, player.shard, *match_ids],
            )
            fight_rows = cursor.fetchall()

        fights_by_match = {
            str(fight_row["match_id"]): fight_row
            for fight_row in fight_rows
        }
        for row in rows:
            row["weapon_accuracy_rows"] = accuracy_by_match.get(str(row["match_id"]), [])
            fight_row = fights_by_match.get(str(row["match_id"]), {})
            row["fight_count"] = _int(fight_row.get("fight_count"))
            row["fight_wins"] = _int(fight_row.get("fight_wins"))
            row["fight_losses"] = _int(fight_row.get("fight_losses"))
        return rows

    def _get_weapon_dimension_rows(
        self,
        player: RegisteredPlayer,
        match_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        match_by_id = {str(row["match_id"]): row for row in match_rows}
        if not match_by_id:
            return []
        match_ids = list(match_by_id)
        placeholders = ", ".join(["%s"] * len(match_ids))
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    match_id,
                    weapon_code,
                    shots_fired,
                    shots_hit,
                    hits_taken,
                    damage_dealt,
                    damage_taken,
                    kills,
                    assists,
                    deaths,
                    dbnos,
                    dbnos_taken,
                    headshot_hits,
                    headshot_hits_taken,
                    headshot_kills,
                    hit_parts,
                    taken_hit_parts
                FROM player_weapon_match_stats
                WHERE account_id = %s
                  AND match_id IN (
                """
                + placeholders
                + """
                  )
                  AND (
                    shots_fired > 0
                    OR damage_dealt > 0
                    OR kills > 0
                    OR assists > 0
                    OR dbnos > 0
                  )
                ORDER BY match_id ASC, weapon_code ASC
                """,
                [player.account_id, *match_ids],
            )
            weapon_rows = [dict(row) for row in cursor.fetchall()]

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    match_id,
                    weapon_code,
                    COUNT(*) AS fight_count,
                    COALESCE(SUM(outcome_type = 'win'), 0) AS fight_wins,
                    COALESCE(SUM(outcome_type = 'loss'), 0) AS fight_losses,
                    COALESCE(SUM(outcome_reason = 'death'), 0) AS deaths,
                    COALESCE(SUM(outcome_reason = 'dbno_taken'), 0) AS dbnos_taken
                FROM player_fight_outcomes
                WHERE account_id = %s
                  AND is_friendly_fire = 0
                  AND weapon_code IS NOT NULL
                  AND match_id IN (
                """
                + placeholders
                + """
                  )
                GROUP BY match_id, weapon_code
                """,
                [player.account_id, *match_ids],
            )
            fight_rows = cursor.fetchall()
        fights_by_match_weapon = {
            (str(row["match_id"]), str(row["weapon_code"])): row
            for row in fight_rows
        }

        result: list[dict[str, Any]] = []
        for weapon_row in weapon_rows:
            match_id = str(weapon_row["match_id"])
            weapon_code = str(weapon_row["weapon_code"])
            base_row = match_by_id.get(match_id)
            if base_row is None:
                continue
            fight_row = fights_by_match_weapon.get((match_id, weapon_code), {})
            row = dict(base_row)
            row.update(
                {
                    "weapon_code": weapon_code,
                    "kills": _int(weapon_row.get("kills")),
                    "assists": _int(weapon_row.get("assists")),
                    "deaths": _int(fight_row.get("deaths")),
                    "dbnos_caused": _int(weapon_row.get("dbnos")),
                    "dbnos_taken": _int(fight_row.get("dbnos_taken")),
                    "damage_dealt": _float(weapon_row.get("damage_dealt")),
                    "damage_taken": _float(weapon_row.get("damage_taken")),
                    "shots_fired": _int(weapon_row.get("shots_fired")),
                    "shots_hit": _int(weapon_row.get("shots_hit")),
                    "hits_taken": _int(weapon_row.get("hits_taken")),
                    "headshot_hits": _int(weapon_row.get("headshot_hits")),
                    "headshot_hits_taken": _int(weapon_row.get("headshot_hits_taken")),
                    "headshot_kills": _int(weapon_row.get("headshot_kills")),
                    "hit_parts": weapon_row.get("hit_parts"),
                    "taken_hit_parts": weapon_row.get("taken_hit_parts"),
                    "weapon_accuracy_rows": [
                        {
                            "weapon_code": weapon_code,
                            "shots_fired": _int(weapon_row.get("shots_fired")),
                            "shots_hit": _int(weapon_row.get("shots_hit")),
                        }
                    ],
                    "fight_count": _int(fight_row.get("fight_count")),
                    "fight_wins": _int(fight_row.get("fight_wins")),
                    "fight_losses": _int(fight_row.get("fight_losses")),
                }
            )
            result.append(row)
        return result


@dataclass(frozen=True)
class PlayerTrendSummary:
    totals: PlayerTrendMetrics
    buckets: list[PlayerTrendBucket]
    available_bucket_count: int
    truncated: bool


@dataclass
class _TrendAccumulator:
    match_count: int = 0
    wins: int = 0
    kills: int = 0
    assists: int = 0
    deaths: int = 0
    dbnos_caused: int = 0
    dbnos_taken: int = 0
    damage_dealt: float = 0.0
    damage_taken: float = 0.0
    shots_fired: int = 0
    shots_hit: int = 0
    character_hits: int = 0
    vehicle_hits: int = 0
    vehicle_damage_dealt: float = 0.0
    hits_taken: int = 0
    headshot_hits: int = 0
    headshot_hits_taken: int = 0
    headshot_kills: int = 0
    survival_seconds: float = 0.0
    movement_distance_m: float = 0.0
    accuracy_rows: list[Mapping[str, Any]] = field(default_factory=list)
    hit_parts: dict[str, int] = field(default_factory=dict)
    taken_hit_parts: dict[str, int] = field(default_factory=dict)
    fight_count: int = 0
    fight_wins: int = 0
    fight_losses: int = 0
    first_match_at_kst: datetime | None = None
    last_match_at_kst: datetime | None = None

    def add(self, row: Mapping[str, Any], created_at_kst: datetime) -> None:
        self.match_count += 1
        self.wins += 1 if _optional_int(row.get("win_place")) == 1 else 0
        self.kills += _int(row.get("kills"))
        self.assists += _int(row.get("assists"))
        self.deaths += _int(row.get("deaths"))
        self.dbnos_caused += _int(row.get("dbnos_caused"))
        self.dbnos_taken += _int(row.get("dbnos_taken"))
        self.damage_dealt += _float(row.get("damage_dealt"))
        self.damage_taken += _float(row.get("damage_taken"))
        self.shots_fired += _int(row.get("shots_fired"))
        self.shots_hit += _int(row.get("shots_hit"))
        self.character_hits += (
            _int(row.get("shots_hit"))
            if row.get("character_hits") is None
            else _int(row.get("character_hits"))
        )
        self.vehicle_hits += _int(row.get("vehicle_hits"))
        self.vehicle_damage_dealt += _float(row.get("vehicle_damage_dealt"))
        self.hits_taken += _int(row.get("hits_taken"))
        self.headshot_hits += _int(row.get("headshot_hits"))
        self.headshot_hits_taken += _int(row.get("headshot_hits_taken"))
        self.fight_count += _int(row.get("fight_count"))
        self.fight_wins += _int(row.get("fight_wins"))
        self.fight_losses += _int(row.get("fight_losses"))
        weapon_accuracy_rows = row.get("weapon_accuracy_rows")
        if isinstance(weapon_accuracy_rows, list):
            self.accuracy_rows.extend(
                item for item in weapon_accuracy_rows if isinstance(item, Mapping)
            )
        self.headshot_kills += _int(row.get("headshot_kills"))
        _merge_part_counts(self.hit_parts, row.get("hit_parts"))
        _merge_part_counts(self.taken_hit_parts, row.get("taken_hit_parts"))
        self.survival_seconds += _survival_seconds(row)
        self.movement_distance_m += _float(
            row.get("movement_distance_m", row.get("in_game_sampled_distance_m"))
        )
        if self.first_match_at_kst is None or created_at_kst < self.first_match_at_kst:
            self.first_match_at_kst = created_at_kst
        if self.last_match_at_kst is None or created_at_kst > self.last_match_at_kst:
            self.last_match_at_kst = created_at_kst

    def metrics(self) -> PlayerTrendMetrics:
        non_wins = self.match_count - self.wins
        accuracy_breakdown = (
            summarize_accuracy_rows(self.accuracy_rows)
            if self.accuracy_rows
            else None
        )
        accuracy = (
            _safe_divide(self.shots_hit, self.shots_fired)
            if accuracy_breakdown is None
            else accuracy_breakdown.estimated_hit_rate or 0.0
        )
        return PlayerTrendMetrics(
            match_count=self.match_count,
            wins=self.wins,
            non_wins=non_wins,
            win_rate=_safe_divide(self.wins, self.match_count),
            kills=self.kills,
            assists=self.assists,
            deaths=self.deaths,
            kda=_safe_divide(self.kills + self.assists, self.deaths if self.deaths > 0 else 1),
            dbnos_caused=self.dbnos_caused,
            dbnos_taken=self.dbnos_taken,
            damage_dealt=self.damage_dealt,
            damage_taken=self.damage_taken,
            avg_damage_dealt=_safe_divide(self.damage_dealt, self.match_count),
            avg_damage_taken=_safe_divide(self.damage_taken, self.match_count),
            shots_fired=self.shots_fired,
            shots_hit=self.shots_hit,
            character_hits=self.character_hits,
            vehicle_hits=self.vehicle_hits,
            vehicle_damage_dealt=self.vehicle_damage_dealt,
            accuracy=accuracy,
            headshot_kills=self.headshot_kills,
            headshot_kill_rate=_safe_divide(self.headshot_kills, self.kills),
            avg_survival_seconds=_safe_divide(self.survival_seconds, self.match_count),
            avg_movement_distance_m=_safe_divide(self.movement_distance_m, self.match_count),
            accuracy_breakdown=accuracy_breakdown,
            hits_taken=self.hits_taken,
            headshot_hits=self.headshot_hits,
            headshot_hits_taken=self.headshot_hits_taken,
            headshot_hit_rate=_safe_divide(self.headshot_hits, self.character_hits),
            headshot_hit_taken_rate=_safe_divide(self.headshot_hits_taken, self.hits_taken),
            hit_parts=dict(self.hit_parts),
            taken_hit_parts=dict(self.taken_hit_parts),
            hit_part_rates=_part_rates(self.hit_parts),
            taken_hit_part_rates=_part_rates(self.taken_hit_parts),
            avg_kills=_safe_divide(self.kills, self.match_count),
            avg_assists=_safe_divide(self.assists, self.match_count),
            avg_deaths=_safe_divide(self.deaths, self.match_count),
            avg_dbnos_caused=_safe_divide(self.dbnos_caused, self.match_count),
            avg_dbnos_taken=_safe_divide(self.dbnos_taken, self.match_count),
            fight_count=self.fight_count,
            fight_wins=self.fight_wins,
            fight_losses=self.fight_losses,
            fight_win_rate=_safe_divide(
                self.fight_wins,
                self.fight_wins + self.fight_losses,
            ),
            avg_fights_per_match=_safe_divide(self.fight_count, self.match_count),
        )


def summarize_player_trends(
    rows: Iterable[Mapping[str, Any]],
    *,
    granularity: str,
    bucket_limit: int = 120,
) -> PlayerTrendSummary:
    normalized_granularity = normalize_trend_granularity(granularity)
    limit = max(1, min(int(bucket_limit), 500))
    totals = _TrendAccumulator()
    grouped: dict[str, tuple[str, tuple[Any, ...], _TrendAccumulator]] = {}

    for row in rows:
        created_at_kst = _datetime(row.get("created_at_kst"))
        if created_at_kst is None:
            continue
        period_key, period_label, sort_key = _period(row, created_at_kst, normalized_granularity)
        if period_key not in grouped:
            grouped[period_key] = (period_label, sort_key, _TrendAccumulator())
        grouped[period_key][2].add(row, created_at_kst)
        totals.add(row, created_at_kst)

    all_buckets = [
        PlayerTrendBucket(
            period_key=period_key,
            period_label=period_label,
            first_match_at_kst=accumulator.first_match_at_kst or datetime.min,
            last_match_at_kst=accumulator.last_match_at_kst or datetime.min,
            metrics=accumulator.metrics(),
        )
        for period_key, (period_label, _, accumulator) in sorted(
            grouped.items(),
            key=lambda item: item[1][1],
        )
    ]
    available_bucket_count = len(all_buckets)
    selected = all_buckets[-limit:] if available_bucket_count > limit else all_buckets
    return PlayerTrendSummary(
        totals=totals.metrics(),
        buckets=selected,
        available_bucket_count=available_bucket_count,
        truncated=available_bucket_count > len(selected),
    )


def normalize_trend_granularity(value: str) -> TrendGranularity:
    normalized = _required_text(value, "granularity").lower()
    aliases: dict[str, TrendGranularity] = {
        "hour": "hour",
        "hour_of_day": "hour",
        "time": "hour",
        "시간": "hour",
        "시간대": "hour",
        "date": "date",
        "day": "date",
        "일": "date",
        "일자": "date",
        "week": "week",
        "iso_week": "week",
        "주": "week",
        "주차": "week",
        "month": "month",
        "월": "month",
        "월별": "month",
        "quarter": "quarter",
        "분기": "quarter",
        "분기별": "quarter",
        "year": "year",
        "연": "year",
        "연도": "year",
        "연도별": "year",
        "map": "map",
        "맵": "map",
        "맵별": "map",
        "weapon": "weapon",
        "weapons": "weapon",
        "무기": "weapon",
        "무기별": "weapon",
        "game_mode": "game_mode",
        "mode": "game_mode",
        "모드": "game_mode",
        "team_mode": "team_mode",
        "team": "team_mode",
        "팀": "team_mode",
        "perspective": "perspective",
        "view": "perspective",
        "시점": "perspective",
        "match_type": "match_type",
        "매치유형": "match_type",
        "season_state": "season_state",
        "season": "season_state",
        "시즌": "season_state",
    }
    if normalized not in aliases:
        raise ValueError(
            "granularity must be a time period or one of map, weapon, game_mode, team_mode, "
            "perspective, match_type, season_state."
        )
    return aliases[normalized]


def parse_trend_date(value: str | None, label: str) -> date | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{label} must use YYYY-MM-DD.") from exc


def parse_optional_bool(value: str | bool | None, label: str) -> bool | None:
    if isinstance(value, bool) or value is None:
        return value
    normalized = value.strip().lower()
    if normalized in {"", "any", "all", "전체"}:
        return None
    if normalized in {"1", "true", "yes", "custom", "커스텀"}:
        return True
    if normalized in {"0", "false", "no", "normal", "일반"}:
        return False
    raise ValueError(f"{label} must be any, true, or false.")


def _period(
    row: Mapping[str, Any],
    created_at_kst: datetime,
    granularity: TrendGranularity,
) -> tuple[str, str, tuple[Any, ...]]:
    if granularity == "hour":
        hour = created_at_kst.hour
        return f"{hour:02d}", f"{hour:02d}시", (hour,)
    if granularity == "date":
        value = created_at_kst.date()
        return value.isoformat(), value.isoformat(), (value.year, value.month, value.day)
    if granularity == "week":
        iso = created_at_kst.isocalendar()
        key = f"{iso.year}-W{iso.week:02d}"
        return key, f"{iso.year}년 {iso.week:02d}주", (iso.year, iso.week)
    if granularity == "month":
        key = f"{created_at_kst.year:04d}-{created_at_kst.month:02d}"
        return key, f"{created_at_kst.year:04d}년 {created_at_kst.month:02d}월", (
            created_at_kst.year,
            created_at_kst.month,
        )
    if granularity == "quarter":
        quarter = (created_at_kst.month - 1) // 3 + 1
        key = f"{created_at_kst.year:04d}-Q{quarter}"
        return key, f"{created_at_kst.year:04d}년 {quarter}분기", (created_at_kst.year, quarter)
    if granularity == "year":
        key = f"{created_at_kst.year:04d}"
        return key, f"{created_at_kst.year:04d}년", (created_at_kst.year,)

    field_name = {
        "map": "map_name",
        "weapon": "weapon_code",
    }.get(granularity, granularity)
    value = _optional_text(row.get(field_name)) or "unknown"
    category = {
        "map": "map",
        "weapon": "damage_causer",
        "game_mode": "game_mode",
        "team_mode": "team_mode",
        "perspective": "perspective",
        "match_type": "match_type",
        "season_state": "season_state",
    }[granularity]
    return value, translate_code(value, category), (value.lower(),)


def _survival_seconds(row: Mapping[str, Any]) -> float:
    raw_stats = row.get("raw_stats")
    if isinstance(raw_stats, str):
        try:
            raw_stats = json.loads(raw_stats)
        except json.JSONDecodeError:
            raw_stats = None
    if isinstance(raw_stats, Mapping):
        value = raw_stats.get("timeSurvived")
        if isinstance(value, int | float):
            return float(value)
    return _float(row.get("duration_seconds"))


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


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


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


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _int(value: Any) -> int:
    return int(value or 0)


def _float(value: Any) -> float:
    return float(value or 0.0)


def _safe_divide(numerator: int | float, denominator: int | float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _merge_part_counts(target: dict[str, int], value: Any) -> None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return
    if not isinstance(value, Mapping):
        return
    for key, count in value.items():
        normalized_count = _int(count)
        if normalized_count > 0:
            normalized_key = str(key)
            target[normalized_key] = target.get(normalized_key, 0) + normalized_count


def _part_rates(parts: Mapping[str, int]) -> dict[str, float]:
    total = sum(max(0, _int(value)) for value in parts.values())
    if total <= 0:
        return {}
    return {
        str(key): max(0, _int(value)) / total
        for key, value in parts.items()
        if _int(value) > 0
    }


def _bounded_int(value: int | None, label: str, minimum: int, maximum: int) -> int | None:
    if value is None:
        return None
    parsed = int(value)
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}.")
    return parsed
