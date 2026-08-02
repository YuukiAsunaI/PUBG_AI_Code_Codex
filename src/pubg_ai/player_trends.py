from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable, Literal, Mapping
import json

from pubg_ai.player_registry import RegisteredPlayer


TrendGranularity = Literal["hour", "date", "week", "month"]


@dataclass(frozen=True)
class PlayerTrendFilters:
    game_mode: str | None = None
    team_mode: str | None = None
    perspective: str | None = None
    match_type: str | None = None
    map_name: str | None = None
    is_custom_match: bool | None = None
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

        return PlayerTrendFilters(
            game_mode=_optional_text(self.game_mode),
            team_mode=team_mode,
            perspective=perspective,
            match_type=_optional_text(self.match_type),
            map_name=_optional_text(self.map_name),
            is_custom_match=self.is_custom_match,
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
            "is_custom_match": self.is_custom_match,
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
            granularity=normalized_granularity,
            bucket_limit=bucket_limit,
        )
        return PlayerTrendReport(
            player=player,
            granularity=normalized_granularity,
            timezone="Asia/Seoul",
            filters=normalized_filters,
            totals=summary.totals,
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
            conditions.append("registered_guild_id = %s")
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
        ):
            if value is not None:
                conditions.append(f"{column} = %s")
                params.append(value)

        if filters.is_custom_match is not None:
            conditions.append("matches.is_custom_match = %s")
            params.append(1 if filters.is_custom_match else 0)
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
                    summaries.headshot_kills,
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
                WHERE
                """
                + " AND ".join(conditions)
                + " ORDER BY matches.created_at_kst ASC, summaries.match_id ASC",
                params,
            )
            return list(cursor.fetchall())


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
    headshot_kills: int = 0
    survival_seconds: float = 0.0
    movement_distance_m: float = 0.0
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
        self.headshot_kills += _int(row.get("headshot_kills"))
        self.survival_seconds += _survival_seconds(row)
        self.movement_distance_m += _float(row.get("in_game_sampled_distance_m"))
        if self.first_match_at_kst is None or created_at_kst < self.first_match_at_kst:
            self.first_match_at_kst = created_at_kst
        if self.last_match_at_kst is None or created_at_kst > self.last_match_at_kst:
            self.last_match_at_kst = created_at_kst

    def metrics(self) -> PlayerTrendMetrics:
        non_wins = self.match_count - self.wins
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
            accuracy=_safe_divide(self.shots_hit, self.shots_fired),
            headshot_kills=self.headshot_kills,
            headshot_kill_rate=_safe_divide(self.headshot_kills, self.kills),
            avg_survival_seconds=_safe_divide(self.survival_seconds, self.match_count),
            avg_movement_distance_m=_safe_divide(self.movement_distance_m, self.match_count),
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
    grouped: dict[str, tuple[str, tuple[int, ...], _TrendAccumulator]] = {}

    for row in rows:
        created_at_kst = _datetime(row.get("created_at_kst"))
        if created_at_kst is None:
            continue
        period_key, period_label, sort_key = _period(created_at_kst, normalized_granularity)
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
    }
    if normalized not in aliases:
        raise ValueError("granularity must be hour, date, week, or month.")
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
    created_at_kst: datetime,
    granularity: TrendGranularity,
) -> tuple[str, str, tuple[int, ...]]:
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
    key = f"{created_at_kst.year:04d}-{created_at_kst.month:02d}"
    return key, f"{created_at_kst.year:04d}년 {created_at_kst.month:02d}월", (
        created_at_kst.year,
        created_at_kst.month,
    )


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
