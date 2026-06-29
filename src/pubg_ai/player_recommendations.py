from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import sqrt
from typing import Any, Mapping
import json

from pubg_ai.code_translator import translate_code
from pubg_ai.distance_buckets import WeaponFamily, distance_bucket
from pubg_ai.map_snapshot_renderer import DEFAULT_WORLD_SIZE_CM, MAP_WORLD_SIZE_CM
from pubg_ai.player_registry import RegisteredPlayer
from pubg_ai.weapon_stats import normalize_weapon_code


@dataclass(frozen=True)
class WeaponDistanceBucketRecommendation:
    weapon_code: str
    weapon_name: str
    bucket_label: str
    min_m: int
    max_m: int | None
    weapon_family: str
    score: float
    event_count: int
    kills: int
    dbnos: int
    finishes: int
    avg_distance_m: float
    reason: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WeaponAttachmentRecommendation:
    weapon_code: str
    weapon_name: str
    attachment_code: str
    attachment_name: str
    attachment_category: str | None
    attachment_sub_category: str | None
    score: float
    match_count: int
    attached_events: int
    wins: int
    kills: int
    dbnos: int
    damage_dealt: float
    win_rate: float
    kills_per_match: float
    avg_damage_dealt: float
    reason: str
    event_count: int = 0
    finishes: int = 0
    headshots: int = 0
    avg_distance_m: float | None = None
    source: str = "attach_events"

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WeaponAttachmentSnapshotEvidence:
    match_id: str
    shard: str
    map_name: str | None
    map_name_ko: str | None
    game_mode: str | None
    match_type: str | None
    match_created_at_kst: str | None
    combat_event_index: int
    combat_action: str
    combat_event_at_kst: str | None
    weapon_code: str
    weapon_name: str
    attachment_code: str
    attachment_name: str
    equipped_attachment_codes: tuple[str, ...]
    equipped_attachment_names: tuple[str, ...]
    distance_m: float | None
    is_headshot: bool
    win_place: int | None
    player_kills: int
    player_dbnos: int
    player_damage_dealt: float

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["equipped_attachment_codes"] = list(self.equipped_attachment_codes)
        record["equipped_attachment_names"] = list(self.equipped_attachment_names)
        return record


@dataclass(frozen=True)
class WeaponAttachmentEvidenceReport:
    player: RegisteredPlayer
    weapon_code: str
    weapon_name: str
    attachment_code: str
    attachment_name: str
    snapshots: list[WeaponAttachmentSnapshotEvidence]

    def to_record(self) -> dict[str, Any]:
        return {
            "player": self.player.to_record(),
            "weapon_code": self.weapon_code,
            "weapon_name": self.weapon_name,
            "attachment_code": self.attachment_code,
            "attachment_name": self.attachment_name,
            "snapshot_count": len(self.snapshots),
            "totals": _weapon_attachment_evidence_totals(self.snapshots),
            "snapshots": [snapshot.to_record() for snapshot in self.snapshots],
        }


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
    range_score: float = 0.0
    top_distance_buckets: list[WeaponDistanceBucketRecommendation] = field(default_factory=list)

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
    weapon_attachments: list[WeaponAttachmentRecommendation]
    weapon_ranges: list[WeaponDistanceBucketRecommendation]
    attachments: list[AttachmentRecommendation]
    maps: list[MapRecommendation]
    teammates: list[TeammateRecommendation]
    drop_zones: list[DropZoneRecommendation]

    def to_record(self) -> dict[str, Any]:
        return {
            "player": self.player.to_record(),
            "min_matches": self.min_matches,
            "weapons": [item.to_record() for item in self.weapons],
            "weapon_attachments": [item.to_record() for item in self.weapon_attachments],
            "weapon_ranges": [item.to_record() for item in self.weapon_ranges],
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
        weapon_ranges = self._weapon_distance_recommendations(player, limit=max(limit * 4, 12))
        return PlayerRecommendationReport(
            player=player,
            min_matches=min_matches,
            weapons=self._weapon_recommendations(
                player,
                limit=limit,
                min_matches=min_matches,
                distance_by_weapon=_distance_by_weapon(weapon_ranges),
            ),
            weapon_attachments=self._weapon_attachment_recommendations(player, limit=limit, min_matches=min_matches),
            weapon_ranges=weapon_ranges[:limit],
            attachments=self._attachment_recommendations(player, limit=limit, min_matches=min_matches),
            maps=self._map_recommendations(player, limit=limit, min_matches=min_matches),
            teammates=self._teammate_recommendations(player, limit=limit, min_matches=min_matches),
            drop_zones=self._drop_zone_recommendations(player, limit=limit, min_matches=min_matches),
        )

    def get_weapon_attachment_evidence(
        self,
        *,
        shard: str,
        weapon_code: str,
        attachment_code: str,
        account_id: str | None = None,
        name: str | None = None,
        guild_id: str | None = None,
        global_scope: bool = False,
        limit: int = 20,
    ) -> WeaponAttachmentEvidenceReport | None:
        player = self._get_player(
            shard=shard,
            account_id=account_id,
            name=name,
            guild_id=guild_id,
            global_scope=global_scope,
        )
        if player is None:
            return None

        normalized_weapon_code = normalize_weapon_code(weapon_code) or _required_text(weapon_code, "weapon_code")
        attachment_code = _required_text(attachment_code, "attachment_code")
        limit = max(1, min(int(limit), 100))
        snapshots = self._weapon_attachment_snapshot_evidence(
            player,
            weapon_code=normalized_weapon_code,
            attachment_code=attachment_code,
            limit=limit,
        )
        return WeaponAttachmentEvidenceReport(
            player=player,
            weapon_code=normalized_weapon_code,
            weapon_name=translate_code(normalized_weapon_code, "damage_causer"),
            attachment_code=attachment_code,
            attachment_name=translate_code(attachment_code, "item"),
            snapshots=snapshots,
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
        distance_by_weapon: Mapping[str, list[WeaponDistanceBucketRecommendation]],
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
            weapon_code = str(row["weapon_code"])
            top_distance_buckets = list(distance_by_weapon.get(weapon_code, []))[:3]
            range_score = sum(bucket.score for bucket in top_distance_buckets[:2]) * 0.05
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
            ) + range_score
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
                    range_score=range_score,
                    top_distance_buckets=top_distance_buckets,
                )
            )
        return _top(recommendations, limit)

    def _weapon_distance_recommendations(
        self,
        player: RegisteredPlayer,
        *,
        limit: int,
    ) -> list[WeaponDistanceBucketRecommendation]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    location_events.damage_causer_name,
                    location_events.action,
                    location_events.distance_m,
                    location_events.x,
                    location_events.y,
                    location_events.related_x,
                    location_events.related_y
                FROM player_combat_location_events location_events
                INNER JOIN matches
                    ON matches.match_id = location_events.match_id
                WHERE location_events.account_id = %s
                  AND matches.shard = %s
                  AND location_events.action IN ('kill', 'dbno_caused', 'finish')
                  AND location_events.damage_causer_name IS NOT NULL
                  AND location_events.distance_m IS NOT NULL
                  AND location_events.distance_m >= 0
                ORDER BY matches.created_at_kst DESC, location_events.match_id DESC, location_events.event_index DESC
                LIMIT 5000
                """,
                (player.account_id, player.shard),
            )
            rows = cursor.fetchall()

        buckets: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            weapon_code = normalize_weapon_code(row.get("damage_causer_name"))
            if not weapon_code or not weapon_code.startswith("Weap"):
                continue
            distance_m = _combat_distance_from_row(row)
            if distance_m is None or distance_m < 0:
                continue
            bucket = distance_bucket(distance_m, _weapon_family(weapon_code))
            key = (weapon_code, bucket.label)
            record = buckets.setdefault(
                key,
                {
                    "weapon_code": weapon_code,
                    "bucket": bucket,
                    "event_count": 0,
                    "kills": 0,
                    "dbnos": 0,
                    "finishes": 0,
                    "distance_sum": 0.0,
                },
            )
            action = str(row.get("action") or "")
            record["event_count"] += 1
            record["distance_sum"] += distance_m
            if action == "kill":
                record["kills"] += 1
            elif action == "dbno_caused":
                record["dbnos"] += 1
            elif action == "finish":
                record["finishes"] += 1

        recommendations: list[WeaponDistanceBucketRecommendation] = []
        for record in buckets.values():
            bucket = record["bucket"]
            event_count = _int(record["event_count"])
            kills = _int(record["kills"])
            dbnos = _int(record["dbnos"])
            finishes = _int(record["finishes"])
            score = kills * 120 + dbnos * 70 + finishes * 40 + event_count * 8
            weapon_code = str(record["weapon_code"])
            recommendations.append(
                WeaponDistanceBucketRecommendation(
                    weapon_code=weapon_code,
                    weapon_name=translate_code(weapon_code, "damage_causer"),
                    bucket_label=bucket.label,
                    min_m=bucket.min_m,
                    max_m=bucket.max_m,
                    weapon_family=bucket.weapon_family,
                    score=score,
                    event_count=event_count,
                    kills=kills,
                    dbnos=dbnos,
                    finishes=finishes,
                    avg_distance_m=_safe_divide(record["distance_sum"], event_count),
                    reason=f"{event_count} events, {kills} kills, {dbnos} DBNOs at {bucket.label}",
                )
            )
        return _top(recommendations, limit)

    def _weapon_attachment_recommendations(
        self,
        player: RegisteredPlayer,
        *,
        limit: int,
        min_matches: int,
    ) -> list[WeaponAttachmentRecommendation]:
        snapshot_recommendations = self._loadout_snapshot_attachment_recommendations(
            player,
            limit=limit,
            min_matches=min_matches,
        )
        if snapshot_recommendations:
            return snapshot_recommendations
        return self._attach_event_weapon_attachment_recommendations(
            player,
            limit=limit,
            min_matches=min_matches,
        )

    def _loadout_snapshot_attachment_recommendations(
        self,
        player: RegisteredPlayer,
        *,
        limit: int,
        min_matches: int,
    ) -> list[WeaponAttachmentRecommendation]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    snapshots.match_id,
                    snapshots.weapon_code,
                    snapshots.weapon_name_ko,
                    snapshots.attachment_codes,
                    snapshots.attachment_names_ko,
                    snapshots.combat_action,
                    snapshots.distance_m,
                    snapshots.is_headshot,
                    CASE WHEN participants.win_place = 1 THEN 1 ELSE 0 END AS win,
                    COALESCE(summaries.damage_dealt, 0) AS damage_dealt
                FROM player_combat_loadout_snapshots snapshots
                INNER JOIN matches
                    ON matches.match_id = snapshots.match_id
                LEFT JOIN player_match_combat_summaries summaries
                    ON summaries.match_id = snapshots.match_id
                   AND summaries.account_id = snapshots.account_id
                LEFT JOIN match_participants participants
                    ON participants.match_id = snapshots.match_id
                   AND participants.account_id = snapshots.account_id
                WHERE snapshots.account_id = %s
                  AND matches.shard = %s
                  AND snapshots.attachment_count > 0
                ORDER BY matches.created_at_kst DESC, snapshots.match_id DESC, snapshots.combat_event_index DESC
                LIMIT 5000
                """,
                (player.account_id, player.shard),
            )
            rows = cursor.fetchall()

        combos: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            match_id = str(row.get("match_id") or "")
            weapon_code = str(row.get("weapon_code") or "")
            if not weapon_code.startswith("Weap"):
                continue

            attachment_codes = _json_string_list(row.get("attachment_codes"))
            attachment_names = _json_string_list(row.get("attachment_names_ko"))
            names_by_code = dict(zip(attachment_codes, attachment_names))
            for attachment_code in attachment_codes:
                if not attachment_code:
                    continue
                key = (weapon_code, attachment_code)
                record = combos.setdefault(
                    key,
                    {
                        "weapon_code": weapon_code,
                        "weapon_name": row.get("weapon_name_ko"),
                        "attachment_code": attachment_code,
                        "attachment_name": names_by_code.get(attachment_code),
                        "match_ids": set(),
                        "win_match_ids": set(),
                        "damage_by_match": {},
                        "event_count": 0,
                        "kills": 0,
                        "dbnos": 0,
                        "finishes": 0,
                        "headshots": 0,
                        "distance_sum": 0.0,
                        "distance_count": 0,
                    },
                )
                record["match_ids"].add(match_id)
                if _int(row.get("win")):
                    record["win_match_ids"].add(match_id)
                record["damage_by_match"][match_id] = max(
                    _float(record["damage_by_match"].get(match_id)),
                    _float(row.get("damage_dealt")),
                )
                record["event_count"] += 1
                action = str(row.get("combat_action") or "")
                if action == "kill":
                    record["kills"] += 1
                elif action == "dbno_caused":
                    record["dbnos"] += 1
                elif action == "finish":
                    record["finishes"] += 1
                if _int(row.get("is_headshot")):
                    record["headshots"] += 1
                distance_m = _optional_float(row.get("distance_m"))
                if distance_m is not None:
                    record["distance_sum"] += distance_m
                    record["distance_count"] += 1

        recommendations: list[WeaponAttachmentRecommendation] = []
        for record in combos.values():
            match_count = len(record["match_ids"])
            if match_count < min_matches:
                continue
            wins = len(record["win_match_ids"])
            kills = _int(record["kills"])
            dbnos = _int(record["dbnos"])
            finishes = _int(record["finishes"])
            headshots = _int(record["headshots"])
            event_count = _int(record["event_count"])
            damage_dealt = sum(_float(value) for value in record["damage_by_match"].values())
            score = (
                kills * 120
                + dbnos * 70
                + finishes * 40
                + headshots * 20
                + event_count * 8
                + wins * 50
                + _safe_divide(damage_dealt, match_count) * 0.15
            )
            weapon_code = str(record["weapon_code"])
            attachment_code = str(record["attachment_code"])
            recommendations.append(
                WeaponAttachmentRecommendation(
                    weapon_code=weapon_code,
                    weapon_name=str(record["weapon_name"] or translate_code(weapon_code, "damage_causer")),
                    attachment_code=attachment_code,
                    attachment_name=str(record["attachment_name"] or translate_code(attachment_code, "item")),
                    attachment_category=None,
                    attachment_sub_category=None,
                    score=score,
                    match_count=match_count,
                    attached_events=event_count,
                    wins=wins,
                    kills=kills,
                    dbnos=dbnos,
                    damage_dealt=damage_dealt,
                    win_rate=_safe_divide(wins, match_count),
                    kills_per_match=_safe_divide(kills, match_count),
                    avg_damage_dealt=_safe_divide(damage_dealt, match_count),
                    reason=(
                        f"{event_count} combat snapshots with "
                        f"{translate_code(weapon_code, 'damage_causer')} + "
                        f"{translate_code(attachment_code, 'item')}"
                    ),
                    event_count=event_count,
                    finishes=finishes,
                    headshots=headshots,
                    avg_distance_m=(
                        _safe_divide(record["distance_sum"], record["distance_count"])
                        if record["distance_count"]
                        else None
                    ),
                    source="loadout_snapshots",
                )
            )
        return _top(recommendations, limit)

    def _attach_event_weapon_attachment_recommendations(
        self,
        player: RegisteredPlayer,
        *,
        limit: int,
        min_matches: int,
    ) -> list[WeaponAttachmentRecommendation]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    item_events.match_id,
                    item_events.parent_item_code,
                    item_events.item_code,
                    MAX(item_events.item_name_ko) AS item_name_ko,
                    MAX(item_events.item_category) AS item_category,
                    MAX(item_events.item_sub_category) AS item_sub_category,
                    COUNT(*) AS attached_events,
                    MAX(CASE WHEN participants.win_place = 1 THEN 1 ELSE 0 END) AS win,
                    MAX(COALESCE(summaries.kills, 0)) AS kills,
                    MAX(COALESCE(summaries.dbnos_caused, 0)) AS dbnos,
                    MAX(COALESCE(summaries.damage_dealt, 0)) AS damage_dealt
                FROM player_item_events item_events
                INNER JOIN matches
                    ON matches.match_id = item_events.match_id
                LEFT JOIN player_match_combat_summaries summaries
                    ON summaries.match_id = item_events.match_id
                   AND summaries.account_id = item_events.account_id
                LEFT JOIN match_participants participants
                    ON participants.match_id = item_events.match_id
                   AND participants.account_id = item_events.account_id
                WHERE item_events.account_id = %s
                  AND matches.shard = %s
                  AND item_events.action = 'attach'
                  AND item_events.parent_item_code IS NOT NULL
                  AND item_events.item_code IS NOT NULL
                  AND item_events.item_code LIKE %s
                GROUP BY
                    item_events.match_id,
                    item_events.parent_item_code,
                    item_events.item_code
                LIMIT 1000
                """,
                (player.account_id, player.shard, "Item_Attach_%"),
            )
            rows = cursor.fetchall()

        combos: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            weapon_code = normalize_weapon_code(row.get("parent_item_code"))
            attachment_code = str(row.get("item_code") or "")
            if not weapon_code or not weapon_code.startswith("Weap") or not attachment_code:
                continue
            key = (weapon_code, attachment_code)
            record = combos.setdefault(
                key,
                {
                    "weapon_code": weapon_code,
                    "attachment_code": attachment_code,
                    "attachment_name": row.get("item_name_ko"),
                    "attachment_category": row.get("item_category"),
                    "attachment_sub_category": row.get("item_sub_category"),
                    "match_count": 0,
                    "attached_events": 0,
                    "wins": 0,
                    "kills": 0,
                    "dbnos": 0,
                    "damage_dealt": 0.0,
                },
            )
            record["match_count"] += 1
            record["attached_events"] += _int(row.get("attached_events"))
            record["wins"] += _int(row.get("win"))
            record["kills"] += _int(row.get("kills"))
            record["dbnos"] += _int(row.get("dbnos"))
            record["damage_dealt"] += _float(row.get("damage_dealt"))

        recommendations: list[WeaponAttachmentRecommendation] = []
        for record in combos.values():
            match_count = _int(record["match_count"])
            if match_count < min_matches:
                continue
            wins = _int(record["wins"])
            kills = _int(record["kills"])
            dbnos = _int(record["dbnos"])
            damage_dealt = _float(record["damage_dealt"])
            attached_events = _int(record["attached_events"])
            score = (
                _safe_divide(damage_dealt, match_count)
                + _safe_divide(kills, match_count) * 70
                + _safe_divide(dbnos, match_count) * 30
                + _safe_divide(wins, match_count) * 100
                + attached_events
            )
            weapon_code = str(record["weapon_code"])
            attachment_code = str(record["attachment_code"])
            recommendations.append(
                WeaponAttachmentRecommendation(
                    weapon_code=weapon_code,
                    weapon_name=translate_code(weapon_code, "damage_causer"),
                    attachment_code=attachment_code,
                    attachment_name=str(record["attachment_name"] or translate_code(attachment_code, "item")),
                    attachment_category=record["attachment_category"],
                    attachment_sub_category=record["attachment_sub_category"],
                    score=score,
                    match_count=match_count,
                    attached_events=attached_events,
                    wins=wins,
                    kills=kills,
                    dbnos=dbnos,
                    damage_dealt=damage_dealt,
                    win_rate=_safe_divide(wins, match_count),
                    kills_per_match=_safe_divide(kills, match_count),
                    avg_damage_dealt=_safe_divide(damage_dealt, match_count),
                    reason=(
                        f"{match_count} matches with {translate_code(weapon_code, 'damage_causer')} + "
                        f"{translate_code(attachment_code, 'item')}"
                    ),
                    event_count=attached_events,
                    source="attach_events",
                )
            )
        return _top(recommendations, limit)

    def _weapon_attachment_snapshot_evidence(
        self,
        player: RegisteredPlayer,
        *,
        weapon_code: str,
        attachment_code: str,
        limit: int,
    ) -> list[WeaponAttachmentSnapshotEvidence]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    snapshots.match_id,
                    matches.shard,
                    matches.map_name,
                    matches.game_mode,
                    matches.match_type,
                    matches.created_at_kst,
                    snapshots.combat_event_index,
                    snapshots.combat_action,
                    snapshots.combat_event_at_kst,
                    snapshots.weapon_code,
                    snapshots.weapon_name_ko,
                    snapshots.attachment_codes,
                    snapshots.attachment_names_ko,
                    snapshots.distance_m,
                    snapshots.is_headshot,
                    participants.win_place,
                    COALESCE(summaries.kills, 0) AS player_kills,
                    COALESCE(summaries.dbnos_caused, 0) AS player_dbnos,
                    COALESCE(summaries.damage_dealt, 0) AS player_damage_dealt
                FROM player_combat_loadout_snapshots snapshots
                INNER JOIN matches
                    ON matches.match_id = snapshots.match_id
                LEFT JOIN match_participants participants
                    ON participants.match_id = snapshots.match_id
                   AND participants.account_id = snapshots.account_id
                LEFT JOIN player_match_combat_summaries summaries
                    ON summaries.match_id = snapshots.match_id
                   AND summaries.account_id = snapshots.account_id
                WHERE snapshots.account_id = %s
                  AND matches.shard = %s
                  AND snapshots.weapon_code = %s
                  AND snapshots.attachment_count > 0
                ORDER BY matches.created_at_kst DESC, snapshots.match_id DESC, snapshots.combat_event_index DESC
                LIMIT 5000
                """,
                (player.account_id, player.shard, weapon_code),
            )
            rows = cursor.fetchall()

        evidence: list[WeaponAttachmentSnapshotEvidence] = []
        for row in rows:
            attachment_codes = tuple(_json_string_list(row.get("attachment_codes")))
            if attachment_code not in attachment_codes:
                continue
            attachment_names = tuple(_json_string_list(row.get("attachment_names_ko")))
            names_by_code = dict(zip(attachment_codes, attachment_names))
            map_name = _optional_text(row.get("map_name"))
            evidence.append(
                WeaponAttachmentSnapshotEvidence(
                    match_id=str(row.get("match_id") or ""),
                    shard=str(row.get("shard") or player.shard),
                    map_name=map_name,
                    map_name_ko=translate_code(map_name, "map") if map_name else None,
                    game_mode=_optional_text(row.get("game_mode")),
                    match_type=_optional_text(row.get("match_type")),
                    match_created_at_kst=_datetime_record(row.get("created_at_kst")),
                    combat_event_index=_int(row.get("combat_event_index")),
                    combat_action=str(row.get("combat_action") or ""),
                    combat_event_at_kst=_datetime_record(row.get("combat_event_at_kst")),
                    weapon_code=str(row.get("weapon_code") or weapon_code),
                    weapon_name=str(row.get("weapon_name_ko") or translate_code(weapon_code, "damage_causer")),
                    attachment_code=attachment_code,
                    attachment_name=names_by_code.get(attachment_code) or translate_code(attachment_code, "item"),
                    equipped_attachment_codes=attachment_codes,
                    equipped_attachment_names=attachment_names,
                    distance_m=_optional_float(row.get("distance_m")),
                    is_headshot=bool(_int(row.get("is_headshot"))),
                    win_place=_optional_int(row.get("win_place")),
                    player_kills=_int(row.get("player_kills")),
                    player_dbnos=_int(row.get("player_dbnos")),
                    player_damage_dealt=_float(row.get("player_damage_dealt")),
                )
            )
            if len(evidence) >= limit:
                break
        return evidence

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
        key=lambda item: (
            -float(item.score),
            -int(getattr(item, "match_count", getattr(item, "event_count", 0))),
            str(getattr(item, "reason", "")),
        ),
    )[:limit]


def _distance_by_weapon(
    ranges: list[WeaponDistanceBucketRecommendation],
) -> dict[str, list[WeaponDistanceBucketRecommendation]]:
    by_weapon: dict[str, list[WeaponDistanceBucketRecommendation]] = {}
    for item in ranges:
        by_weapon.setdefault(item.weapon_code, []).append(item)
    for weapon_code, items in by_weapon.items():
        by_weapon[weapon_code] = _top(items, 3)
    return by_weapon


def _weapon_attachment_evidence_totals(snapshots: list[WeaponAttachmentSnapshotEvidence]) -> dict[str, Any]:
    match_ids = {snapshot.match_id for snapshot in snapshots}
    distance_values = [
        snapshot.distance_m
        for snapshot in snapshots
        if snapshot.distance_m is not None
    ]
    return {
        "event_count": len(snapshots),
        "match_count": len(match_ids),
        "wins": len({snapshot.match_id for snapshot in snapshots if snapshot.win_place == 1}),
        "kills": sum(1 for snapshot in snapshots if snapshot.combat_action == "kill"),
        "dbnos": sum(1 for snapshot in snapshots if snapshot.combat_action == "dbno_caused"),
        "finishes": sum(1 for snapshot in snapshots if snapshot.combat_action == "finish"),
        "headshots": sum(1 for snapshot in snapshots if snapshot.is_headshot),
        "avg_distance_m": _safe_divide(sum(distance_values), len(distance_values)) if distance_values else None,
    }


def _weapon_family(weapon_code: str) -> WeaponFamily:
    if weapon_code in AR_WEAPONS:
        return "AR"
    if weapon_code in DMR_WEAPONS:
        return "DMR"
    if weapon_code in SR_WEAPONS:
        return "SR"
    return "OTHER"


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
        try:
            raw_stats = json.loads(raw_stats)
        except json.JSONDecodeError:
            raw_stats = {}
    if isinstance(raw_stats, Mapping):
        survived = _optional_float(raw_stats.get("timeSurvived"))
        if survived is not None:
            return survived
    return _float(row.get("duration_seconds"))


def _json_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    payload = value
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", errors="replace")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return []
    if not isinstance(payload, list):
        return []
    return [str(item) for item in payload if item is not None and str(item)]


def _datetime_record(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _combat_distance_from_row(row: Mapping[str, Any]) -> float | None:
    x = _optional_float(row.get("x"))
    y = _optional_float(row.get("y"))
    related_x = _optional_float(row.get("related_x"))
    related_y = _optional_float(row.get("related_y"))
    if x is not None and y is not None and related_x is not None and related_y is not None:
        return sqrt((related_x - x) ** 2 + (related_y - y) ** 2) / 100.0

    distance = _optional_float(row.get("distance_m"))
    if distance is None:
        return None
    return distance / 100.0 if distance > 1000 else distance


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


AR_WEAPONS = {
    "WeapACE32_C",
    "WeapAK47_C",
    "WeapAUG_C",
    "WeapBerylM762_C",
    "WeapFAMASG2_C",
    "WeapG36C_C",
    "WeapGroza_C",
    "WeapHK416_C",
    "WeapK2_C",
    "WeapM16A4_C",
    "WeapMk47Mutant_C",
    "WeapQBZ95_C",
    "WeapSCAR-L_C",
}

DMR_WEAPONS = {
    "WeapDragunov_C",
    "WeapFNFal_C",
    "WeapMini14_C",
    "WeapMk12_C",
    "WeapMk14_C",
    "WeapQBU88_C",
    "WeapSKS_C",
    "WeapVSS_C",
}

SR_WEAPONS = {
    "WeapAWM_C",
    "WeapKar98k_C",
    "WeapL6_C",
    "WeapM24_C",
    "WeapMosinNagant_C",
    "WeapWin94_C",
}
