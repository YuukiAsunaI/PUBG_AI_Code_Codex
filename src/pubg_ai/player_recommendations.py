from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from pubg_ai.code_translator import translate_code
from pubg_ai.map_snapshot_renderer import DEFAULT_WORLD_SIZE_CM, MAP_WORLD_SIZE_CM
from pubg_ai.player_registry import RegisteredPlayer


@dataclass(frozen=True)
class WeaponRecommendation:
    weapon_code: str
    weapon_name: str
    score: float
    match_count: int
    wins: int
    kills: int
    assists: int
    deaths: int
    dbnos: int
    damage_dealt: float
    shots_fired: int
    shots_hit: int
    win_rate: float
    kills_per_match: float
    dbnos_per_match: float
    avg_damage_dealt: float
    accuracy: float
    reason: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AttachmentRecommendation:
    item_code: str
    item_name: str
    item_category: str | None
    item_sub_category: str | None
    score: float
    match_count: int
    attached_events: int
    wins: int
    win_rate: float
    avg_damage_dealt: float
    reason: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MapRecommendation:
    map_name: str
    map_name_ko: str
    score: float
    match_count: int
    wins: int
    kills: int
    assists: int
    deaths: int
    dbnos: int
    damage_dealt: float
    win_rate: float
    kda: float
    avg_damage_dealt: float
    avg_survival_seconds: float
    reason: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TeammateRecommendation:
    account_id: str
    name: str
    registered: bool
    score: float
    match_count: int
    wins: int
    kills: int
    assists: int
    deaths: int
    dbnos: int
    damage_dealt: float
    win_rate: float
    kda: float
    avg_damage_dealt: float
    reason: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DropZoneRecommendation:
    map_name: str
    map_name_ko: str
    grid_x: int
    grid_y: int
    x_pct: float
    y_pct: float
    score: float
    match_count: int
    wins: int
    kills: int
    deaths: int
    damage_dealt: float
    win_rate: float
    avg_damage_dealt: float
    avg_survival_seconds: float
    reason: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PlayerRecommendationReport:
    player: RegisteredPlayer
    min_matches: int
    weapons: list[WeaponRecommendation]
    attachments: list[AttachmentRecommendation]
    maps: list[MapRecommendation]
    teammates: list[TeammateRecommendation]
    drop_zones: list[DropZoneRecommendation]

    def to_record(self) -> dict[str, Any]:
        return {
            "player": self.player.to_record(),
            "min_matches": self.min_matches,
            "weapons": [item.to_record() for item in self.weapons],
            "attachments": [item.to_record() for item in self.attachments],
            "maps": [item.to_record() for item in self.maps],
            "teammates": [item.to_record() for item in self.teammates],
            "drop_zones": [item.to_record() for item in self.drop_zones],
        }


class PlayerRecommendationService:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def get_recommendations(
        self,
        *,
        shard: str,
        account_id: str | None = None,
        name: str | None = None,
        guild_id: str | None = None,
        global_scope: bool = False,
        limit: int = 5,
        min_matches: int = 1,
    ) -> PlayerRecommendationReport | None:
        player = self._get_player(
            shard=shard,
            account_id=account_id,
            name=name,
            guild_id=guild_id,
            global_scope=global_scope,
        )
        if player is None:
            return None

        limit = max(1, min(int(limit), 20))
        min_matches = max(1, int(min_matches))
        return PlayerRecommendationReport(
            player=player,
            min_matches=min_matches,
            weapons=self._weapon_recommendations(player, limit=limit, min_matches=min_matches),
            attachments=self._attachment_recommendations(player, limit=limit, min_matches=min_matches),
            maps=self._map_recommendations(player, limit=limit, min_matches=min_matches),
            teammates=self._teammate_recommendations(player, limit=limit, min_matches=min_matches),
            drop_zones=self._drop_zone_recommendations(player, limit=limit, min_matches=min_matches),
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
            conditions.append("registered_guild_id = %s")
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

    def _weapon_recommendations(
        self,
        player: RegisteredPlayer,
        *,
        limit: int,
        min_matches: int,
    ) -> list[WeaponRecommendation]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    weapon_stats.weapon_code,
                    COUNT(DISTINCT weapon_stats.match_id) AS match_count,
                    COALESCE(SUM(CASE WHEN participants.win_place = 1 THEN 1 ELSE 0 END), 0) AS wins,
                    COALESCE(SUM(weapon_stats.kills), 0) AS kills,
                    COALESCE(SUM(weapon_stats.assists), 0) AS assists,
                    COALESCE(SUM(weapon_stats.deaths), 0) AS deaths,
                    COALESCE(SUM(weapon_stats.dbnos), 0) AS dbnos,
                    COALESCE(SUM(weapon_stats.damage_dealt), 0) AS damage_dealt,
                    COALESCE(SUM(weapon_stats.shots_fired), 0) AS shots_fired,
                    COALESCE(SUM(weapon_stats.shots_hit), 0) AS shots_hit
                FROM player_weapon_match_stats weapon_stats
                INNER JOIN matches
                    ON matches.match_id = weapon_stats.match_id
                LEFT JOIN match_participants participants
                    ON participants.match_id = weapon_stats.match_id
                   AND participants.account_id = weapon_stats.account_id
                WHERE weapon_stats.account_id = %s
                  AND matches.shard = %s
                GROUP BY weapon_stats.weapon_code
                HAVING match_count >= %s
                   AND (damage_dealt > 0 OR kills > 0 OR dbnos > 0 OR shots_fired > 0)
                LIMIT 100
                """,
                (player.account_id, player.shard, min_matches),
            )
            rows = cursor.fetchall()

        recommendations: list[WeaponRecommendation] = []
        for row in rows:
            match_count = _int(row.get("match_count"))
            wins = _int(row.get("wins"))
            kills = _int(row.get("kills"))
            assists = _int(row.get("assists"))
            deaths = _int(row.get("deaths"))
            dbnos = _int(row.get("dbnos"))
            damage_dealt = _float(row.get("damage_dealt"))
            shots_fired = _int(row.get("shots_fired"))
            shots_hit = _int(row.get("shots_hit"))
            accuracy = _accuracy(shots_hit, shots_fired)
            score = _performance_score(
                match_count=match_count,
                wins=wins,
                kills=kills,
                assists=assists,
                deaths=deaths,
                dbnos=dbnos,
                damage_dealt=damage_dealt,
                shots_fired=shots_fired,
                shots_hit=shots_hit,
            )
            weapon_code = str(row["weapon_code"])
            recommendations.append(
                WeaponRecommendation(
                    weapon_code=weapon_code,
                    weapon_name=translate_code(weapon_code, "damage_causer"),
                    score=score,
                    match_count=match_count,
                    wins=wins,
                    kills=kills,
                    assists=assists,
                    deaths=deaths,
                    dbnos=dbnos,
                    damage_dealt=damage_dealt,
                    shots_fired=shots_fired,
                    shots_hit=shots_hit,
                    win_rate=_safe_divide(wins, match_count),
                    kills_per_match=_safe_divide(kills, match_count),
                    dbnos_per_match=_safe_divide(dbnos, match_count),
                    avg_damage_dealt=_safe_divide(damage_dealt, match_count),
                    accuracy=accuracy,
                    reason=_reason(match_count, wins, damage_dealt, kills),
                )
            )
        return _top(recommendations, limit)

    def _attachment_recommendations(
        self,
        player: RegisteredPlayer,
        *,
        limit: int,
        min_matches: int,
    ) -> list[AttachmentRecommendation]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    item_stats.item_code,
                    item_stats.item_name_ko,
                    item_stats.item_category,
                    item_stats.item_sub_category,
                    COUNT(DISTINCT item_stats.match_id) AS match_count,
                    COALESCE(SUM(item_stats.attached_events), 0) AS attached_events,
                    COALESCE(SUM(CASE WHEN participants.win_place = 1 THEN 1 ELSE 0 END), 0) AS wins,
                    COALESCE(SUM(summaries.damage_dealt), 0) AS damage_dealt
                FROM player_item_match_stats item_stats
                INNER JOIN matches
                    ON matches.match_id = item_stats.match_id
                LEFT JOIN player_match_combat_summaries summaries
                    ON summaries.match_id = item_stats.match_id
                   AND summaries.account_id = item_stats.account_id
                LEFT JOIN match_participants participants
                    ON participants.match_id = item_stats.match_id
                   AND participants.account_id = item_stats.account_id
                WHERE item_stats.account_id = %s
                  AND matches.shard = %s
                  AND (
                    item_stats.attached_events > 0
                    OR item_stats.item_category = %s
                    OR item_stats.item_code LIKE %s
                  )
                GROUP BY
                    item_stats.item_code,
                    item_stats.item_name_ko,
                    item_stats.item_category,
                    item_stats.item_sub_category
                HAVING match_count >= %s
                   AND attached_events > 0
                LIMIT 100
                """,
                (player.account_id, player.shard, "Attachment", "Item_Attach_%", min_matches),
            )
            rows = cursor.fetchall()

        recommendations: list[AttachmentRecommendation] = []
        for row in rows:
            match_count = _int(row.get("match_count"))
            wins = _int(row.get("wins"))
            damage_dealt = _float(row.get("damage_dealt"))
            attached_events = _int(row.get("attached_events"))
            score = _safe_divide(damage_dealt, match_count) + _safe_divide(wins, match_count) * 100 + attached_events * 2
            item_code = str(row["item_code"])
            item_name = str(row.get("item_name_ko") or translate_code(item_code, "item"))
            recommendations.append(
                AttachmentRecommendation(
                    item_code=item_code,
                    item_name=item_name,
                    item_category=row.get("item_category"),
                    item_sub_category=row.get("item_sub_category"),
                    score=score,
                    match_count=match_count,
                    attached_events=attached_events,
                    wins=wins,
                    win_rate=_safe_divide(wins, match_count),
                    avg_damage_dealt=_safe_divide(damage_dealt, match_count),
                    reason=f"{attached_events} attach events, {_safe_divide(damage_dealt, match_count):.1f} avg damage",
                )
            )
        return _top(recommendations, limit)

    def _map_recommendations(
        self,
        player: RegisteredPlayer,
        *,
        limit: int,
        min_matches: int,
    ) -> list[MapRecommendation]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    matches.map_name,
                    COUNT(DISTINCT summaries.match_id) AS match_count,
                    COALESCE(SUM(CASE WHEN participants.win_place = 1 THEN 1 ELSE 0 END), 0) AS wins,
                    COALESCE(SUM(summaries.kills), 0) AS kills,
                    COALESCE(SUM(summaries.assists), 0) AS assists,
                    COALESCE(SUM(summaries.deaths), 0) AS deaths,
                    COALESCE(SUM(summaries.dbnos_caused), 0) AS dbnos,
                    COALESCE(SUM(summaries.damage_dealt), 0) AS damage_dealt,
                    COALESCE(AVG(
                        COALESCE(
                            CAST(JSON_UNQUOTE(JSON_EXTRACT(participants.raw_stats, '$.timeSurvived')) AS DECIMAL(12, 3)),
                            matches.duration_seconds,
                            0
                        )
                    ), 0) AS avg_survival_seconds
                FROM player_match_combat_summaries summaries
                INNER JOIN matches
                    ON matches.match_id = summaries.match_id
                LEFT JOIN match_participants participants
                    ON participants.match_id = summaries.match_id
                   AND participants.account_id = summaries.account_id
                WHERE summaries.account_id = %s
                  AND matches.shard = %s
                  AND matches.map_name IS NOT NULL
                GROUP BY matches.map_name
                HAVING match_count >= %s
                LIMIT 100
                """,
                (player.account_id, player.shard, min_matches),
            )
            rows = cursor.fetchall()

        recommendations: list[MapRecommendation] = []
        for row in rows:
            match_count = _int(row.get("match_count"))
            wins = _int(row.get("wins"))
            kills = _int(row.get("kills"))
            assists = _int(row.get("assists"))
            deaths = _int(row.get("deaths"))
            dbnos = _int(row.get("dbnos"))
            damage_dealt = _float(row.get("damage_dealt"))
            score = _performance_score(
                match_count=match_count,
                wins=wins,
                kills=kills,
                assists=assists,
                deaths=deaths,
                dbnos=dbnos,
                damage_dealt=damage_dealt,
            )
            map_name = str(row["map_name"])
            recommendations.append(
                MapRecommendation(
                    map_name=map_name,
                    map_name_ko=translate_code(map_name, "map"),
                    score=score,
                    match_count=match_count,
                    wins=wins,
                    kills=kills,
                    assists=assists,
                    deaths=deaths,
                    dbnos=dbnos,
                    damage_dealt=damage_dealt,
                    win_rate=_safe_divide(wins, match_count),
                    kda=_safe_divide(kills + assists, deaths if deaths > 0 else 1),
                    avg_damage_dealt=_safe_divide(damage_dealt, match_count),
                    avg_survival_seconds=_float(row.get("avg_survival_seconds")),
                    reason=_reason(match_count, wins, damage_dealt, kills),
                )
            )
        return _top(recommendations, limit)

    def _teammate_recommendations(
        self,
        player: RegisteredPlayer,
        *,
        limit: int,
        min_matches: int,
    ) -> list[TeammateRecommendation]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    teammate.account_id,
                    COALESCE(MAX(teammate.name), teammate.account_id) AS name,
                    MAX(CASE WHEN registered_players.id IS NULL THEN 0 ELSE 1 END) AS registered,
                    COUNT(DISTINCT summaries.match_id) AS match_count,
                    COALESCE(SUM(CASE WHEN self_participant.win_place = 1 THEN 1 ELSE 0 END), 0) AS wins,
                    COALESCE(SUM(summaries.kills), 0) AS kills,
                    COALESCE(SUM(summaries.assists), 0) AS assists,
                    COALESCE(SUM(summaries.deaths), 0) AS deaths,
                    COALESCE(SUM(summaries.dbnos_caused), 0) AS dbnos,
                    COALESCE(SUM(summaries.damage_dealt), 0) AS damage_dealt
                FROM player_match_combat_summaries summaries
                INNER JOIN matches
                    ON matches.match_id = summaries.match_id
                INNER JOIN match_participants self_participant
                    ON self_participant.match_id = summaries.match_id
                   AND self_participant.account_id = summaries.account_id
                INNER JOIN match_participants teammate
                    ON teammate.match_id = self_participant.match_id
                   AND teammate.account_id <> self_participant.account_id
                   AND (
                        (self_participant.roster_id IS NOT NULL AND teammate.roster_id = self_participant.roster_id)
                        OR (self_participant.team_id IS NOT NULL AND teammate.team_id = self_participant.team_id)
                   )
                LEFT JOIN registered_players
                    ON registered_players.account_id = teammate.account_id
                   AND registered_players.shard = matches.shard
                WHERE summaries.account_id = %s
                  AND matches.shard = %s
                  AND teammate.is_ai_or_bot = 0
                GROUP BY teammate.account_id
                HAVING match_count >= %s
                LIMIT 100
                """,
                (player.account_id, player.shard, min_matches),
            )
            rows = cursor.fetchall()

        recommendations: list[TeammateRecommendation] = []
        for row in rows:
            match_count = _int(row.get("match_count"))
            wins = _int(row.get("wins"))
            kills = _int(row.get("kills"))
            assists = _int(row.get("assists"))
            deaths = _int(row.get("deaths"))
            dbnos = _int(row.get("dbnos"))
            damage_dealt = _float(row.get("damage_dealt"))
            score = _performance_score(
                match_count=match_count,
                wins=wins,
                kills=kills,
                assists=assists,
                deaths=deaths,
                dbnos=dbnos,
                damage_dealt=damage_dealt,
            )
            recommendations.append(
                TeammateRecommendation(
                    account_id=str(row["account_id"]),
                    name=str(row.get("name") or row["account_id"]),
                    registered=bool(_int(row.get("registered"))),
                    score=score,
                    match_count=match_count,
                    wins=wins,
                    kills=kills,
                    assists=assists,
                    deaths=deaths,
                    dbnos=dbnos,
                    damage_dealt=damage_dealt,
                    win_rate=_safe_divide(wins, match_count),
                    kda=_safe_divide(kills + assists, deaths if deaths > 0 else 1),
                    avg_damage_dealt=_safe_divide(damage_dealt, match_count),
                    reason=_reason(match_count, wins, damage_dealt, kills),
                )
            )
        return _top(recommendations, limit)

    def _drop_zone_recommendations(
        self,
        player: RegisteredPlayer,
        *,
        limit: int,
        min_matches: int,
    ) -> list[DropZoneRecommendation]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    matches.match_id,
                    matches.map_name,
                    matches.duration_seconds,
                    participants.win_place,
                    participants.raw_stats,
                    summaries.kills,
                    summaries.deaths,
                    summaries.damage_dealt,
                    movement.landing_x,
                    movement.landing_y
                FROM player_movement_summaries movement
                INNER JOIN matches
                    ON matches.match_id = movement.match_id
                LEFT JOIN match_participants participants
                    ON participants.match_id = movement.match_id
                   AND participants.account_id = movement.account_id
                LEFT JOIN player_match_combat_summaries summaries
                    ON summaries.match_id = movement.match_id
                   AND summaries.account_id = movement.account_id
                WHERE movement.account_id = %s
                  AND matches.shard = %s
                  AND matches.map_name IS NOT NULL
                  AND movement.landing_x IS NOT NULL
                  AND movement.landing_y IS NOT NULL
                ORDER BY matches.created_at_kst DESC, movement.match_id DESC
                LIMIT 1000
                """,
                (player.account_id, player.shard),
            )
            rows = cursor.fetchall()

        clusters: dict[tuple[str, int, int], dict[str, Any]] = {}
        for row in rows:
            map_name = str(row["map_name"])
            world_size = MAP_WORLD_SIZE_CM.get(map_name, DEFAULT_WORLD_SIZE_CM)
            x_pct = _clamped(_safe_divide(_float(row.get("landing_x")), world_size))
            y_pct = _clamped(_safe_divide(_float(row.get("landing_y")), world_size))
            grid_x = min(9, int(x_pct * 10))
            grid_y = min(9, int(y_pct * 10))
            key = (map_name, grid_x, grid_y)
            bucket = clusters.setdefault(
                key,
                {
                    "map_name": map_name,
                    "grid_x": grid_x,
                    "grid_y": grid_y,
                    "match_count": 0,
                    "wins": 0,
                    "kills": 0,
                    "deaths": 0,
                    "damage_dealt": 0.0,
                    "survival_seconds": 0.0,
                    "x_pct_sum": 0.0,
                    "y_pct_sum": 0.0,
                },
            )
            bucket["match_count"] += 1
            bucket["wins"] += 1 if _optional_int(row.get("win_place")) == 1 else 0
            bucket["kills"] += _int(row.get("kills"))
            bucket["deaths"] += _int(row.get("deaths"))
            bucket["damage_dealt"] += _float(row.get("damage_dealt"))
            bucket["survival_seconds"] += _survival_seconds_from_row(row)
            bucket["x_pct_sum"] += x_pct
            bucket["y_pct_sum"] += y_pct

        recommendations: list[DropZoneRecommendation] = []
        for bucket in clusters.values():
            match_count = _int(bucket["match_count"])
            if match_count < min_matches:
                continue
            wins = _int(bucket["wins"])
            kills = _int(bucket["kills"])
            deaths = _int(bucket["deaths"])
            damage_dealt = _float(bucket["damage_dealt"])
            score = _performance_score(
                match_count=match_count,
                wins=wins,
                kills=kills,
                deaths=deaths,
                damage_dealt=damage_dealt,
            ) + _safe_divide(bucket["survival_seconds"], match_count) / 20
            map_name = str(bucket["map_name"])
            recommendations.append(
                DropZoneRecommendation(
                    map_name=map_name,
                    map_name_ko=translate_code(map_name, "map"),
                    grid_x=_int(bucket["grid_x"]),
                    grid_y=_int(bucket["grid_y"]),
                    x_pct=_safe_divide(bucket["x_pct_sum"], match_count),
                    y_pct=_safe_divide(bucket["y_pct_sum"], match_count),
                    score=score,
                    match_count=match_count,
                    wins=wins,
                    kills=kills,
                    deaths=deaths,
                    damage_dealt=damage_dealt,
                    win_rate=_safe_divide(wins, match_count),
                    avg_damage_dealt=_safe_divide(damage_dealt, match_count),
                    avg_survival_seconds=_safe_divide(bucket["survival_seconds"], match_count),
                    reason=f"grid {bucket['grid_x']},{bucket['grid_y']} with {_safe_divide(wins, match_count) * 100:.1f}% win rate",
                )
            )
        return _top(recommendations, limit)


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


def _top(items: list[Any], limit: int) -> list[Any]:
    return sorted(
        items,
        key=lambda item: (-float(item.score), -int(item.match_count), str(getattr(item, "reason", ""))),
    )[:limit]


def _performance_score(
    *,
    match_count: int,
    wins: int,
    kills: int = 0,
    assists: int = 0,
    deaths: int = 0,
    dbnos: int = 0,
    damage_dealt: float = 0.0,
    shots_fired: int = 0,
    shots_hit: int = 0,
) -> float:
    if match_count <= 0:
        return 0.0
    avg_damage = _safe_divide(damage_dealt, match_count)
    kills_per_match = _safe_divide(kills, match_count)
    dbnos_per_match = _safe_divide(dbnos, match_count)
    assists_per_match = _safe_divide(assists, match_count)
    deaths_per_match = _safe_divide(deaths, match_count)
    win_rate = _safe_divide(wins, match_count)
    accuracy = _accuracy(shots_hit, shots_fired)
    confidence = min(1.0, match_count / 5)
    raw_score = (
        avg_damage
        + kills_per_match * 85
        + dbnos_per_match * 35
        + assists_per_match * 20
        + win_rate * 120
        + accuracy * 60
        - deaths_per_match * 25
    )
    return max(0.0, raw_score) * (0.65 + confidence * 0.35)


def _reason(match_count: int, wins: int, damage_dealt: float, kills: int) -> str:
    return (
        f"{match_count} matches, {_safe_divide(wins, match_count) * 100:.1f}% win, "
        f"{_safe_divide(damage_dealt, match_count):.1f} avg damage, "
        f"{_safe_divide(kills, match_count):.2f} K/match"
    )


def _survival_seconds_from_row(row: Mapping[str, Any]) -> float:
    raw_stats = row.get("raw_stats")
    if isinstance(raw_stats, str):
        import json

        try:
            raw_stats = json.loads(raw_stats)
        except json.JSONDecodeError:
            raw_stats = {}
    if isinstance(raw_stats, Mapping):
        survived = _optional_float(raw_stats.get("timeSurvived"))
        if survived is not None:
            return survived
    return _float(row.get("duration_seconds"))


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


def _accuracy(shots_hit: int, shots_fired: int) -> float:
    return min(1.0, _safe_divide(shots_hit, shots_fired))


def _clamped(value: float) -> float:
    return max(0.0, min(1.0, value))
