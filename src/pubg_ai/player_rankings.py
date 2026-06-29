from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Any

from pubg_ai.player_registry import RegisteredPlayer


@dataclass(frozen=True)
class RankingMetric:
    key: str
    label: str


@dataclass(frozen=True)
class PlayerRankingRow:
    rank: int
    player: RegisteredPlayer
    score: float
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
    last_match_at_kst: datetime | None

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["player"] = self.player.to_record()
        record["last_match_at_kst"] = _datetime_record(self.last_match_at_kst)
        return record


@dataclass(frozen=True)
class PlayerRanking:
    metric: str
    metric_label: str
    shard: str
    guild_id: str | None
    global_scope: bool
    active_only: bool
    min_matches: int
    rows: list[PlayerRankingRow]

    def to_record(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "metric_label": self.metric_label,
            "shard": self.shard,
            "guild_id": self.guild_id,
            "global_scope": self.global_scope,
            "active_only": self.active_only,
            "min_matches": self.min_matches,
            "rows": [row.to_record() for row in self.rows],
        }


class PlayerRankingService:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def get_player_ranking(
        self,
        *,
        shard: str = "steam",
        metric: str = "kda",
        guild_id: str | None = None,
        global_scope: bool = False,
        active_only: bool = True,
        min_matches: int = 1,
        limit: int = 10,
    ) -> PlayerRanking:
        shard = _required_text(shard, "shard").lower()
        metric_info = resolve_ranking_metric(metric)
        min_matches = max(1, int(min_matches))
        limit = max(1, min(int(limit), 100))
        rows = self._load_rows(
            shard=shard,
            guild_id=guild_id,
            global_scope=global_scope,
            active_only=active_only,
            min_matches=min_matches,
            metric=metric_info,
        )
        rows.sort(
            key=lambda row: (
                -row.score,
                -row.match_count,
                -row.kills,
                row.player.current_name.lower(),
            )
        )
        ranked_rows = [replace(row, rank=index + 1) for index, row in enumerate(rows[:limit])]

        return PlayerRanking(
            metric=metric_info.key,
            metric_label=metric_info.label,
            shard=shard,
            guild_id=None if global_scope else guild_id,
            global_scope=global_scope,
            active_only=active_only,
            min_matches=min_matches,
            rows=ranked_rows,
        )

    def _load_rows(
        self,
        *,
        shard: str,
        guild_id: str | None,
        global_scope: bool,
        active_only: bool,
        min_matches: int,
        metric: RankingMetric,
    ) -> list[PlayerRankingRow]:
        conditions = ["registered_players.shard = %s"]
        params: list[Any] = [shard]

        if active_only:
            conditions.append("registered_players.active = 1")
        if not global_scope:
            if not guild_id:
                return []
            conditions.append("registered_players.registered_guild_id = %s")
            params.append(guild_id)

        params.append(min_matches)

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
                    registered_players.registered_channel_id,
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
                    COALESCE(SUM(summaries.headshot_kills), 0) AS headshot_kills,
                    COALESCE(AVG(
                        COALESCE(
                            CAST(JSON_UNQUOTE(JSON_EXTRACT(participants.raw_stats, '$.timeSurvived')) AS DECIMAL(12, 3)),
                            matches.duration_seconds,
                            0
                        )
                    ), 0) AS avg_survival_seconds,
                    COALESCE(AVG(COALESCE(movement.in_game_sampled_distance_m, 0)), 0) AS avg_movement_distance_m,
                    MAX(matches.created_at_kst) AS last_match_at_kst
                FROM registered_players
                INNER JOIN player_match_combat_summaries summaries
                    ON summaries.account_id = registered_players.account_id
                INNER JOIN matches
                    ON matches.match_id = summaries.match_id
                   AND matches.shard = registered_players.shard
                LEFT JOIN match_participants participants
                    ON participants.match_id = summaries.match_id
                   AND participants.account_id = summaries.account_id
                LEFT JOIN player_movement_summaries movement
                    ON movement.match_id = summaries.match_id
                   AND movement.account_id = summaries.account_id
                WHERE
                """
                + " AND ".join(conditions)
                + """
                GROUP BY
                    registered_players.id,
                    registered_players.account_id,
                    registered_players.shard,
                    registered_players.current_name,
                    registered_players.active,
                    registered_players.public_profile,
                    registered_players.registered_by_discord_user_id,
                    registered_players.registered_guild_id,
                    registered_players.registered_channel_id
                HAVING match_count >= %s
                """,
                params,
            )
            raw_rows = cursor.fetchall()

        return [
            _ranking_row_from_record(row, rank=0, metric=metric)
            for row in raw_rows
        ]


def resolve_ranking_metric(value: str) -> RankingMetric:
    normalized = _normalize_metric(value)
    key = RANKING_METRIC_ALIASES.get(normalized, normalized)
    metric = RANKING_METRICS.get(key)
    if metric is None:
        return RANKING_METRICS["kda"]
    return metric


def _ranking_row_from_record(row: dict[str, Any], *, rank: int, metric: RankingMetric) -> PlayerRankingRow:
    match_count = _int(row.get("match_count"))
    kills = _int(row.get("kills"))
    assists = _int(row.get("assists"))
    deaths = _int(row.get("deaths"))
    wins = _int(row.get("wins"))
    damage_dealt = _float(row.get("damage_dealt"))
    damage_taken = _float(row.get("damage_taken"))
    shots_fired = _int(row.get("shots_fired"))
    shots_hit = _int(row.get("shots_hit"))
    headshot_kills = _int(row.get("headshot_kills"))

    values = {
        "match_count": match_count,
        "wins": wins,
        "kills": kills,
        "assists": assists,
        "deaths": deaths,
        "damage_dealt": damage_dealt,
        "shots_fired": shots_fired,
        "shots_hit": shots_hit,
        "headshot_kills": headshot_kills,
    }
    win_rate = _safe_divide(wins, match_count)
    kda = _safe_divide(kills + assists, deaths if deaths > 0 else 1)
    accuracy = _safe_divide(shots_hit, shots_fired)
    headshot_kill_rate = _safe_divide(headshot_kills, kills)
    avg_damage_dealt = _safe_divide(damage_dealt, match_count)
    avg_damage_taken = _safe_divide(damage_taken, match_count)

    metric_values = {
        "kda": kda,
        "win_rate": win_rate,
        "avg_damage": avg_damage_dealt,
        "damage": damage_dealt,
        "kills": kills,
        "matches": match_count,
        "accuracy": accuracy,
        "headshot_rate": headshot_kill_rate,
        "dbnos": _int(row.get("dbnos_caused")),
    }
    score = float(metric_values.get(metric.key, metric_values["kda"]))

    return PlayerRankingRow(
        rank=rank,
        player=_player_from_row(row),
        score=score,
        match_count=values["match_count"],
        wins=values["wins"],
        kills=values["kills"],
        assists=values["assists"],
        deaths=values["deaths"],
        dbnos_caused=_int(row.get("dbnos_caused")),
        dbnos_taken=_int(row.get("dbnos_taken")),
        damage_dealt=damage_dealt,
        damage_taken=damage_taken,
        shots_fired=shots_fired,
        shots_hit=shots_hit,
        headshot_kills=headshot_kills,
        avg_damage_dealt=avg_damage_dealt,
        avg_damage_taken=avg_damage_taken,
        win_rate=win_rate,
        kda=kda,
        accuracy=accuracy,
        headshot_kill_rate=headshot_kill_rate,
        avg_survival_seconds=_float(row.get("avg_survival_seconds")),
        avg_movement_distance_m=_float(row.get("avg_movement_distance_m")),
        last_match_at_kst=row.get("last_match_at_kst"),
    )


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


def _normalize_metric(value: str) -> str:
    return "".join(ch.lower() for ch in value.strip() if ch.isalnum() or ch == "_")


def _datetime_record(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _required_text(value: str, label: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError(f"{label} is required.")
    return text


def _int(value: Any) -> int:
    if value is None:
        return 0
    return int(value)


def _float(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)


def _safe_divide(numerator: float | int, denominator: float | int) -> float:
    if not denominator:
        return 0.0
    return float(numerator) / float(denominator)


RANKING_METRICS = {
    "kda": RankingMetric(key="kda", label="KDA"),
    "win_rate": RankingMetric(key="win_rate", label="승률"),
    "avg_damage": RankingMetric(key="avg_damage", label="평균 딜"),
    "damage": RankingMetric(key="damage", label="총 딜"),
    "kills": RankingMetric(key="kills", label="킬"),
    "matches": RankingMetric(key="matches", label="경기 수"),
    "accuracy": RankingMetric(key="accuracy", label="명중률"),
    "headshot_rate": RankingMetric(key="headshot_rate", label="헤드샷 킬 비율"),
    "dbnos": RankingMetric(key="dbnos", label="기절"),
}

RANKING_METRIC_ALIASES = {
    "킬": "kills",
    "kill": "kills",
    "kills": "kills",
    "딜": "damage",
    "총딜": "damage",
    "damage": "damage",
    "평딜": "avg_damage",
    "평균딜": "avg_damage",
    "avgdamage": "avg_damage",
    "승률": "win_rate",
    "winrate": "win_rate",
    "치킨": "win_rate",
    "경기": "matches",
    "판수": "matches",
    "matches": "matches",
    "명중률": "accuracy",
    "accuracy": "accuracy",
    "헤드샷": "headshot_rate",
    "headshot": "headshot_rate",
    "기절": "dbnos",
    "dbno": "dbnos",
    "dbnos": "dbnos",
}
