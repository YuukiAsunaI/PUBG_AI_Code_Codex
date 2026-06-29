from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Mapping
import json

from pubg_ai.code_translator import translate_code
from pubg_ai.player_registry import RegisteredPlayer
from pubg_ai.replay_artifact_catalog import ReplayArtifactRecord, list_replay_artifacts
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

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["first_match_at_kst"] = _datetime_record(self.first_match_at_kst)
        record["last_match_at_kst"] = _datetime_record(self.last_match_at_kst)
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

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


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

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


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

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["created_at_kst"] = _datetime_record(self.created_at_kst)
        return record


@dataclass(frozen=True)
class PlayerWeaponDetail:
    player: RegisteredPlayer
    weapon_code: str
    weapon_name: str
    totals: PlayerWeaponDetailTotals
    recent_matches: list[PlayerWeaponRecentMatch]

    def to_record(self) -> dict[str, Any]:
        return {
            "player": self.player.to_record(),
            "weapon_code": self.weapon_code,
            "weapon_name": self.weapon_name,
            "totals": self.totals.to_record(),
            "recent_matches": [match.to_record() for match in self.recent_matches],
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

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


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

    def to_record(self) -> dict[str, Any]:
        return {
            "player": self.player.to_record(),
            "match_id": self.match_id,
            "shard": self.shard,
            "map_name": self.map_name,
            "game_mode": self.game_mode,
            "match_type": self.match_type,
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
            "hits_taken": self.hits_taken,
            "accuracy": self.accuracy,
            "headshot_hits": self.headshot_hits,
            "headshot_hits_taken": self.headshot_hits_taken,
            "headshot_kills": self.headshot_kills,
            "headshot_deaths": self.headshot_deaths,
            "headshot_dbnos_caused": self.headshot_dbnos_caused,
            "headshot_dbnos_taken": self.headshot_dbnos_taken,
            "hit_parts": self.hit_parts,
            "taken_hit_parts": self.taken_hit_parts,
            "survival_seconds": self.survival_seconds,
            "landing_distance_m": self.landing_distance_m,
            "movement_distance_m": self.movement_distance_m,
            "weapons": [weapon.to_record() for weapon in self.weapons],
            "replay_artifact": self.replay_artifact.to_record() if self.replay_artifact else None,
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
    ) -> PlayerWeaponDetail | None:
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

        rows = self._get_weapon_match_rows(player, weapon_code)
        if not rows:
            return None

        return _weapon_detail_from_rows(
            player=player,
            weapon_code=weapon_code,
            rows=rows,
            recent_limit=recent_limit,
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
        win_place = _optional_int(row.get("win_place"))
        artifacts = list_replay_artifacts(
            self.connection,
            limit=1,
            artifact_type="map_snapshot",
            match_id=match_id,
            account_id=player.account_id,
        )

        return PlayerMatchDetail(
            player=player,
            match_id=str(row["match_id"]),
            shard=str(row["shard"]),
            map_name=row.get("map_name"),
            game_mode=row.get("game_mode"),
            match_type=row.get("match_type"),
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
            hits_taken=_int(row.get("hits_taken")),
            accuracy=_safe_divide(shots_hit, shots_fired),
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
            weapons=self._get_match_weapons(player, match_id=match_id, limit=weapon_limit),
            replay_artifact=artifacts[0] if artifacts else None,
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
            conditions.append("registered_players.registered_guild_id = %s")
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
                    accuracy=_safe_divide(shots_hit, shots_fired),
                    headshot_kills=_int(row.get("headshot_kills")),
                    hit_parts=_part_map(row.get("hit_parts")),
                    taken_hit_parts=_part_map(row.get("taken_hit_parts")),
                )
            )
        return weapons

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
                    COALESCE(SUM(summaries.headshot_kills), 0) AS headshot_kills,
                    COALESCE(AVG(
                        COALESCE(
                            CAST(JSON_UNQUOTE(JSON_EXTRACT(participants.raw_stats, '$.timeSurvived')) AS DECIMAL(12, 3)),
                            matches.duration_seconds,
                            0
                        )
                    ), 0) AS avg_survival_seconds,
                    COALESCE(AVG(COALESCE(movement.in_game_sampled_distance_m, 0)), 0) AS avg_movement_distance_m,
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
        headshot_kills = _int(row.get("headshot_kills"))
        damage_dealt = _float(row.get("damage_dealt"))
        damage_taken = _float(row.get("damage_taken"))

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
            headshot_kills=headshot_kills,
            avg_damage_dealt=_safe_divide(damage_dealt, match_count),
            avg_damage_taken=_safe_divide(damage_taken, match_count),
            win_rate=_safe_divide(_int(row.get("wins")), match_count),
            kda=_safe_divide(kills + assists, deaths if deaths > 0 else 1),
            accuracy=_safe_divide(shots_hit, shots_fired),
            headshot_kill_rate=_safe_divide(headshot_kills, kills),
            avg_survival_seconds=_float(row.get("avg_survival_seconds")),
            avg_movement_distance_m=_float(row.get("avg_movement_distance_m")),
            first_match_at_kst=row.get("first_match_at_kst"),
            last_match_at_kst=row.get("last_match_at_kst"),
        )

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
                    accuracy=_safe_divide(shots_hit, shots_fired),
                    headshot_kills=_int(row.get("headshot_kills")),
                )
            )
        return weapons

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

    def _get_weapon_match_rows(self, player: RegisteredPlayer, weapon_code: str) -> list[dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    weapon_stats.match_id,
                    weapon_stats.shots_fired,
                    weapon_stats.shots_hit,
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
                    participants.win_place
                FROM player_weapon_match_stats weapon_stats
                INNER JOIN matches
                    ON matches.match_id = weapon_stats.match_id
                LEFT JOIN match_participants participants
                    ON participants.match_id = weapon_stats.match_id
                   AND participants.account_id = weapon_stats.account_id
                WHERE weapon_stats.account_id = %s
                  AND matches.shard = %s
                  AND weapon_stats.weapon_code = %s
                  AND (
                    weapon_stats.shots_fired > 0
                    OR weapon_stats.damage_dealt > 0
                    OR weapon_stats.kills > 0
                    OR weapon_stats.assists > 0
                    OR weapon_stats.dbnos > 0
                  )
                ORDER BY matches.created_at_kst DESC, weapon_stats.match_id DESC
                """,
                (player.account_id, player.shard, weapon_code),
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
                movement_distance_m=_optional_float(row.get("in_game_sampled_distance_m")),
            )
            for row in rows
        ]


def weapon_code_from_identifier(value: str) -> str:
    normalized = _normalize_weapon_text(value)
    if normalized in WEAPON_ALIASES:
        return WEAPON_ALIASES[normalized]
    return normalize_weapon_code(value) or value.strip()


def _weapon_detail_from_rows(
    *,
    player: RegisteredPlayer,
    weapon_code: str,
    rows: list[dict[str, Any]],
    recent_limit: int,
) -> PlayerWeaponDetail:
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
    hits_taken = sum(_int(row.get("hits_taken")) for row in rows)
    headshot_hits = sum(_int(row.get("headshot_hits")) for row in rows)
    headshot_kills = sum(_int(row.get("headshot_kills")) for row in rows)
    headshot_dbnos = sum(_int(row.get("headshot_dbnos")) for row in rows)
    hit_parts = _sum_part_maps(row.get("hit_parts") for row in rows)
    taken_hit_parts = _sum_part_maps(row.get("taken_hit_parts") for row in rows)

    return PlayerWeaponDetail(
        player=player,
        weapon_code=weapon_code,
        weapon_name=translate_code(weapon_code, "damage_causer"),
        totals=PlayerWeaponDetailTotals(
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
            hits_taken=hits_taken,
            headshot_hits=headshot_hits,
            headshot_kills=headshot_kills,
            headshot_dbnos=headshot_dbnos,
            accuracy=_safe_divide(shots_hit, shots_fired),
            avg_damage_dealt=_safe_divide(damage_dealt, match_count),
            win_rate=_safe_divide(wins, match_count),
            headshot_kill_rate=_safe_divide(headshot_kills, kills),
            hit_parts=hit_parts,
            taken_hit_parts=taken_hit_parts,
        ),
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
                accuracy=_safe_divide(_int(row.get("shots_hit")), _int(row.get("shots_fired"))),
            )
            for row in rows[: max(1, min(int(recent_limit), 20))]
        ],
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


def _survival_seconds_from_row(row: dict[str, Any]) -> float | None:
    raw_stats = _json_mapping(row.get("raw_stats"))
    survived = _optional_float(raw_stats.get("timeSurvived"))
    if survived is not None:
        return survived
    return _optional_float(row.get("duration_seconds"))


def _movement_distance_from_row(row: dict[str, Any]) -> float | None:
    movement_distance = _optional_float(row.get("in_game_sampled_distance_m"))
    if movement_distance is not None:
        return movement_distance

    raw_stats = _json_mapping(row.get("raw_stats"))
    parts = [
        _optional_float(raw_stats.get("walkDistance")),
        _optional_float(raw_stats.get("rideDistance")),
        _optional_float(raw_stats.get("swimDistance")),
    ]
    known_parts = [part for part in parts if part is not None]
    return sum(known_parts) if known_parts else None


def _datetime_record(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _sum_part_maps(values: Any) -> dict[str, int]:
    totals: dict[str, int] = {}
    for value in values:
        for key, count in _part_map(value).items():
            totals[key] = totals.get(key, 0) + count
    return totals


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
}
