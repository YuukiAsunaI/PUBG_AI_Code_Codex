from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Any

from pubg_ai.player_registry import RegisteredPlayer
from pubg_ai.player_scope import PLAYER_GUILD_SCOPE_CONDITION
from pubg_ai.weapon_accuracy import AccuracyBreakdown, summarize_accuracy_rows


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
    accuracy_breakdown: AccuracyBreakdown | None = None
    top10s: int = 0
    top10_rate: float = 0.0
    kd: float = 0.0
    avg_kills: float = 0.0
    avg_assists: float = 0.0
    avg_deaths: float = 0.0
    avg_dbnos_caused: float = 0.0
    damage_ratio: float = 0.0
    fight_count: int = 0
    fight_wins: int = 0
    fight_losses: int = 0
    fight_win_rate: float = 0.0
    avg_fights_per_match: float = 0.0
    headshot_hits: int = 0
    headshot_hit_rate: float = 0.0
    character_hits: int = 0
    vehicle_hits: int = 0
    vehicle_damage_dealt: float = 0.0

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
            conditions.append(PLAYER_GUILD_SCOPE_CONDITION)
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
                    COALESCE(SUM(CASE WHEN participants.win_place BETWEEN 1 AND 10 THEN 1 ELSE 0 END), 0) AS top10s,
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
                    COALESCE(SUM(summaries.headshot_hits), 0) AS headshot_hits,
                    COALESCE(SUM(summaries.headshot_kills), 0) AS headshot_kills,
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
                    COALESCE(MAX(fight_totals.fight_count), 0) AS fight_count,
                    COALESCE(MAX(fight_totals.fight_wins), 0) AS fight_wins,
                    COALESCE(MAX(fight_totals.fight_losses), 0) AS fight_losses,
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
                LEFT JOIN (
                    SELECT
                        fights.account_id,
                        fight_matches.shard,
                        COUNT(*) AS fight_count,
                        COALESCE(SUM(fights.outcome_type = 'win'), 0) AS fight_wins,
                        COALESCE(SUM(fights.outcome_type = 'loss'), 0) AS fight_losses
                    FROM player_fight_outcomes fights
                    INNER JOIN matches fight_matches
                        ON fight_matches.match_id = fights.match_id
                    WHERE fights.is_friendly_fire = 0
                    GROUP BY fights.account_id, fight_matches.shard
                ) fight_totals
                    ON fight_totals.account_id = registered_players.account_id
                   AND fight_totals.shard = registered_players.shard
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

        accuracy_by_account = self._load_accuracy_breakdowns(shard=shard, raw_rows=raw_rows)
        return [
            _ranking_row_from_record(
                row,
                rank=0,
                metric=metric,
                accuracy_breakdown=accuracy_by_account.get(str(row["account_id"])),
            )
            for row in raw_rows
        ]

    def _load_accuracy_breakdowns(
        self,
        *,
        shard: str,
        raw_rows: list[dict[str, Any]],
    ) -> dict[str, AccuracyBreakdown]:
        account_ids = sorted({str(row["account_id"]) for row in raw_rows})
        if not account_ids:
            return {}
        placeholders = ", ".join(["%s"] * len(account_ids))
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    weapon_stats.account_id,
                    weapon_stats.weapon_code,
                    COALESCE(SUM(weapon_stats.shots_fired), 0) AS shots_fired,
                    COALESCE(SUM(weapon_stats.shots_hit), 0) AS shots_hit
                FROM player_weapon_match_stats weapon_stats
                INNER JOIN matches
                    ON matches.match_id = weapon_stats.match_id
                WHERE matches.shard = %s
                  AND weapon_stats.account_id IN (
                """
                + placeholders
                + """
                  )
                GROUP BY weapon_stats.account_id, weapon_stats.weapon_code
                """,
                [shard, *account_ids],
            )
            accuracy_rows = cursor.fetchall()

        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in accuracy_rows:
            grouped.setdefault(str(row["account_id"]), []).append(row)
        return {
            account_id: summarize_accuracy_rows(rows)
            for account_id, rows in grouped.items()
        }


def resolve_ranking_metric(value: str) -> RankingMetric:
    normalized = _normalize_metric(value)
    key = RANKING_METRIC_ALIASES.get(normalized, normalized)
    metric = RANKING_METRICS.get(key)
    if metric is None:
        return RANKING_METRICS["kda"]
    return metric


def _ranking_row_from_record(
    row: dict[str, Any],
    *,
    rank: int,
    metric: RankingMetric,
    accuracy_breakdown: AccuracyBreakdown | None = None,
) -> PlayerRankingRow:
    match_count = _int(row.get("match_count"))
    kills = _int(row.get("kills"))
    assists = _int(row.get("assists"))
    deaths = _int(row.get("deaths"))
    wins = _int(row.get("wins"))
    top10s = _int(row.get("top10s"))
    damage_dealt = _float(row.get("damage_dealt"))
    damage_taken = _float(row.get("damage_taken"))
    shots_fired = _int(row.get("shots_fired"))
    shots_hit = _int(row.get("shots_hit"))
    character_hits = (
        shots_hit
        if row.get("character_hits") is None
        else _int(row.get("character_hits"))
    )
    vehicle_hits = _int(row.get("vehicle_hits"))
    vehicle_damage_dealt = _float(row.get("vehicle_damage_dealt"))
    headshot_hits = _int(row.get("headshot_hits"))
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
        "headshot_hits": headshot_hits,
        "headshot_kills": headshot_kills,
    }
    win_rate = _safe_divide(wins, match_count)
    top10_rate = _safe_divide(top10s, match_count)
    kd = _safe_divide(kills, deaths if deaths > 0 else 1)
    kda = _safe_divide(kills + assists, deaths if deaths > 0 else 1)
    accuracy = (
        _safe_divide(shots_hit, shots_fired)
        if accuracy_breakdown is None
        else accuracy_breakdown.estimated_hit_rate or 0.0
    )
    headshot_hit_rate = _safe_divide(headshot_hits, character_hits)
    headshot_kill_rate = _safe_divide(headshot_kills, kills)
    avg_damage_dealt = _safe_divide(damage_dealt, match_count)
    avg_damage_taken = _safe_divide(damage_taken, match_count)
    avg_kills = _safe_divide(kills, match_count)
    avg_assists = _safe_divide(assists, match_count)
    avg_deaths = _safe_divide(deaths, match_count)
    dbnos_caused = _int(row.get("dbnos_caused"))
    avg_dbnos_caused = _safe_divide(dbnos_caused, match_count)
    damage_ratio = _safe_divide(damage_dealt, damage_taken if damage_taken > 0 else 1)
    fight_count = _int(row.get("fight_count"))
    fight_wins = _int(row.get("fight_wins"))
    fight_losses = _int(row.get("fight_losses"))
    fight_win_rate = _safe_divide(fight_wins, fight_wins + fight_losses)
    avg_fights_per_match = _safe_divide(fight_count, match_count)

    metric_values = {
        "kda": kda,
        "kd": kd,
        "win_rate": win_rate,
        "top10_rate": top10_rate,
        "top10s": top10s,
        "wins": wins,
        "avg_damage": avg_damage_dealt,
        "avg_damage_taken": avg_damage_taken,
        "damage_ratio": damage_ratio,
        "damage": damage_dealt,
        "kills": kills,
        "avg_kills": avg_kills,
        "assists": assists,
        "avg_assists": avg_assists,
        "matches": match_count,
        "accuracy": accuracy,
        "shots_hit": shots_hit,
        "character_hits": character_hits,
        "vehicle_hits": vehicle_hits,
        "vehicle_damage_dealt": vehicle_damage_dealt,
        "headshot_hits": headshot_hits,
        "headshot_kills": headshot_kills,
        "headshot_hit_rate": headshot_hit_rate,
        "headshot_rate": headshot_kill_rate,
        "dbnos": dbnos_caused,
        "avg_dbnos": avg_dbnos_caused,
        "fight_win_rate": fight_win_rate,
        "fight_wins": fight_wins,
        "avg_fights": avg_fights_per_match,
        "avg_survival": _float(row.get("avg_survival_seconds")),
        "avg_movement": _float(row.get("avg_movement_distance_m")),
    }
    score = float(metric_values.get(metric.key, metric_values["kda"]))

    return PlayerRankingRow(
        rank=rank,
        player=_player_from_row(row),
        score=score,
        match_count=values["match_count"],
        wins=values["wins"],
        top10s=top10s,
        kills=values["kills"],
        assists=values["assists"],
        deaths=values["deaths"],
        dbnos_caused=dbnos_caused,
        dbnos_taken=_int(row.get("dbnos_taken")),
        damage_dealt=damage_dealt,
        damage_taken=damage_taken,
        shots_fired=shots_fired,
        shots_hit=shots_hit,
        character_hits=character_hits,
        vehicle_hits=vehicle_hits,
        vehicle_damage_dealt=vehicle_damage_dealt,
        headshot_hits=headshot_hits,
        headshot_kills=headshot_kills,
        avg_damage_dealt=avg_damage_dealt,
        avg_damage_taken=avg_damage_taken,
        win_rate=win_rate,
        top10_rate=top10_rate,
        kd=kd,
        kda=kda,
        avg_kills=avg_kills,
        avg_assists=avg_assists,
        avg_deaths=avg_deaths,
        avg_dbnos_caused=avg_dbnos_caused,
        damage_ratio=damage_ratio,
        accuracy=accuracy,
        headshot_hit_rate=headshot_hit_rate,
        headshot_kill_rate=headshot_kill_rate,
        avg_survival_seconds=_float(row.get("avg_survival_seconds")),
        avg_movement_distance_m=_float(row.get("avg_movement_distance_m")),
        fight_count=fight_count,
        fight_wins=fight_wins,
        fight_losses=fight_losses,
        fight_win_rate=fight_win_rate,
        avg_fights_per_match=avg_fights_per_match,
        last_match_at_kst=row.get("last_match_at_kst"),
        accuracy_breakdown=accuracy_breakdown,
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
    "kd": RankingMetric(key="kd", label="KD"),
    "win_rate": RankingMetric(key="win_rate", label="승률"),
    "top10_rate": RankingMetric(key="top10_rate", label="TOP10 진입률"),
    "top10s": RankingMetric(key="top10s", label="TOP10 횟수"),
    "wins": RankingMetric(key="wins", label="치킨 수"),
    "avg_damage": RankingMetric(key="avg_damage", label="평균 딜"),
    "avg_damage_taken": RankingMetric(key="avg_damage_taken", label="평균 받은 피해"),
    "damage_ratio": RankingMetric(key="damage_ratio", label="가한/받은 피해 비율"),
    "damage": RankingMetric(key="damage", label="총 딜"),
    "kills": RankingMetric(key="kills", label="킬"),
    "avg_kills": RankingMetric(key="avg_kills", label="경기당 킬"),
    "assists": RankingMetric(key="assists", label="어시스트"),
    "avg_assists": RankingMetric(key="avg_assists", label="경기당 어시스트"),
    "matches": RankingMetric(key="matches", label="경기 수"),
    "accuracy": RankingMetric(key="accuracy", label="추정 명중률(일반 탄환)"),
    "shots_hit": RankingMetric(key="shots_hit", label="총 명중 횟수"),
    "character_hits": RankingMetric(key="character_hits", label="캐릭터 명중 횟수"),
    "vehicle_hits": RankingMetric(key="vehicle_hits", label="차량 명중 횟수"),
    "vehicle_damage_dealt": RankingMetric(key="vehicle_damage_dealt", label="차량에 가한 피해"),
    "headshot_hits": RankingMetric(key="headshot_hits", label="헤드샷 명중 횟수"),
    "headshot_kills": RankingMetric(key="headshot_kills", label="헤드샷 킬 수"),
    "headshot_hit_rate": RankingMetric(key="headshot_hit_rate", label="헤드샷 명중 확률"),
    "headshot_rate": RankingMetric(key="headshot_rate", label="헤드샷 킬 비율"),
    "dbnos": RankingMetric(key="dbnos", label="기절"),
    "avg_dbnos": RankingMetric(key="avg_dbnos", label="경기당 가한 기절"),
    "fight_win_rate": RankingMetric(key="fight_win_rate", label="교전 승리 확률"),
    "fight_wins": RankingMetric(key="fight_wins", label="교전 승리 수"),
    "avg_fights": RankingMetric(key="avg_fights", label="경기당 교전 수"),
    "avg_survival": RankingMetric(key="avg_survival", label="평균 생존 시간"),
    "avg_movement": RankingMetric(key="avg_movement", label="평균 이동 거리"),
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
    "top10": "top10_rate",
    "탑10": "top10_rate",
    "top10수": "top10s",
    "탑10수": "top10s",
    "치킨수": "wins",
    "경기": "matches",
    "판수": "matches",
    "matches": "matches",
    "명중률": "accuracy",
    "accuracy": "accuracy",
    "명중수": "shots_hit",
    "캐릭터명중수": "character_hits",
    "차량명중수": "vehicle_hits",
    "차량피해": "vehicle_damage_dealt",
    "헤드샷명중수": "headshot_hits",
    "헤드샷킬수": "headshot_kills",
    "헤드샷": "headshot_hit_rate",
    "헤드샷명중": "headshot_hit_rate",
    "headshot": "headshot_hit_rate",
    "headshot_hit_rate": "headshot_hit_rate",
    "헤드샷킬": "headshot_rate",
    "headshot_kill_rate": "headshot_rate",
    "기절": "dbnos",
    "dbno": "dbnos",
    "dbnos": "dbnos",
    "kd": "kd",
    "평균킬": "avg_kills",
    "평균기절": "avg_dbnos",
    "어시": "assists",
    "평균어시": "avg_assists",
    "받은피해": "avg_damage_taken",
    "피해비율": "damage_ratio",
    "교전승률": "fight_win_rate",
    "교전승리수": "fight_wins",
    "평균교전": "avg_fights",
    "생존시간": "avg_survival",
    "이동거리": "avg_movement",
}
