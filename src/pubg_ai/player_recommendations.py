from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import ceil, sqrt
from typing import Any, Mapping
import json

from pubg_ai.code_translator import translate_code
from pubg_ai.distance_buckets import WeaponFamily, distance_bucket
from pubg_ai.map_regions import resolve_map_region
from pubg_ai.map_snapshot_renderer import DEFAULT_WORLD_SIZE_CM, MAP_WORLD_SIZE_CM
from pubg_ai.player_registry import RegisteredPlayer
from pubg_ai.player_scope import PLAYER_GUILD_SCOPE_CONDITION
from pubg_ai.weapon_accuracy import (
    WeaponAccuracyMetric,
    distance_weapon_family,
    recommendation_accuracy_score,
    weapon_accuracy_metric,
    weapon_family,
)
from pubg_ai.weapon_stats import normalize_weapon_code


DROP_ZONE_GRID_SIZE = 20


AMMO_TYPE_BY_WEAPON = {
    "WeapACE32_C": "7.62mm",
    "WeapAK47_C": "7.62mm",
    "WeapAUG_C": "5.56mm",
    "WeapBerylM762_C": "7.62mm",
    "WeapFAMASG2_C": "5.56mm",
    "WeapG36C_C": "5.56mm",
    "WeapGroza_C": "7.62mm",
    "WeapHK416_C": "5.56mm",
    "WeapK2_C": "5.56mm",
    "WeapM16A4_C": "5.56mm",
    "WeapMk47Mutant_C": "7.62mm",
    "WeapQBZ95_C": "5.56mm",
    "WeapSCAR-L_C": "5.56mm",
    "WeapDragunov_C": "7.62mm",
    "WeapFNFal_C": "7.62mm",
    "WeapMads_QBU88_C": "5.56mm",
    "WeapMini14_C": "5.56mm",
    "WeapMk12_C": "5.56mm",
    "WeapMk14_C": "7.62mm",
    "WeapQBU88_C": "5.56mm",
    "WeapSKS_C": "7.62mm",
    "WeapVSS_C": "9mm",
    "WeapAWM_C": ".300 매그넘",
    "WeapKar98k_C": "7.62mm",
    "WeapL6_C": ".50 BMG",
    "WeapM24_C": "7.62mm",
    "WeapMosin_C": "7.62mm",
    "WeapMosinNagant_C": "7.62mm",
    "WeapWin1894_C": ".45 ACP",
    "WeapWin94_C": ".45 ACP",
    "WeapBizonPP19_C": "9mm",
    "WeapJS9_C": "9mm",
    "WeapMP5K_C": "9mm",
    "WeapMP9_C": "9mm",
    "WeapP90_C": "5.7mm",
    "WeapThompson_C": ".45 ACP",
    "WeapUMP_C": ".45 ACP",
    "WeapUZI_C": "9mm",
    "WeapVector_C": "9mm",
    "WeapDP28_C": "7.62mm",
    "WeapM249_C": "5.56mm",
    "WeapMG3_C": "7.62mm",
    "WeapRPD_C": "7.62mm",
    "WeapBerreta686_C": "12 게이지",
    "WeapDP12_C": "12 게이지",
    "WeapOriginS12_C": "12 게이지",
    "WeapSaiga12_C": "12 게이지",
    "WeapSawnoff_C": "12 게이지",
    "WeapWinchester_C": "12 게이지",
    "ProjCrossbow_C": "석궁 볼트",
    "WeapCrossbow_C": "석궁 볼트",
}

# Per-round inventory units. Keep this versioned because PUBG can rebalance item weight.
# Update 34.1 explicitly changed 7.62mm from 0.7 to 0.6; the API itself does not expose weight.
AMMO_INVENTORY_WEIGHT_PER_ROUND = {
    "5.56mm": 0.5,
    "7.62mm": 0.6,
    "9mm": 0.3,
    ".45 ACP": 0.4,
    "5.7mm": 0.25,
    "12 게이지": 1.25,
    ".300 매그넘": 1.0,
    ".50 BMG": 1.5,
    "석궁 볼트": 2.0,
}

RESERVE_ROUNDS_BY_FAMILY = {
    "AR": 150,
    "DMR": 90,
    "SR": 45,
    "SMG": 180,
    "LMG": 220,
    "SHOTGUN": 45,
    "HANDGUN": 45,
    "CROSSBOW": 25,
    "UNCLASSIFIED": 100,
}

RESERVE_ROUNDS_BY_WEAPON = {
    "WeapP90_C": 200,
    "WeapAWM_C": 25,
    "WeapL6_C": 10,
    "WeapDP28_C": 180,
    "WeapM249_C": 240,
    "WeapMG3_C": 220,
    "WeapRPD_C": 220,
}


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
class WeaponAttachmentCombinationRecommendation:
    weapon_code: str
    weapon_name: str
    attachment_codes: tuple[str, ...]
    attachment_names: tuple[str, ...]
    score: float
    match_count: int
    event_count: int
    wins: int
    kills: int
    dbnos: int
    finishes: int
    headshots: int
    damage_dealt: float
    win_rate: float
    avg_damage_dealt: float
    avg_distance_m: float | None
    reason: str
    score_components: dict[str, float] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["attachment_codes"] = list(self.attachment_codes)
        record["attachment_names"] = list(self.attachment_names)
        return record


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
    headshot_hits: int = 0
    headshot_hit_rate: float = 0.0
    character_hits: int = 0
    vehicle_hits: int = 0
    vehicle_damage_dealt: float = 0.0
    fight_count: int = 0
    fight_wins: int = 0
    fight_losses: int = 0
    fight_win_rate: float = 0.0
    accuracy_metric: WeaponAccuracyMetric | None = None
    range_score: float = 0.0
    top_distance_buckets: list[WeaponDistanceBucketRecommendation] = field(default_factory=list)
    score_components: dict[str, float] = field(default_factory=dict)

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
class WeaponLoadoutRecommendation:
    primary: WeaponRecommendation
    secondary: WeaponRecommendation
    primary_attachments: list[WeaponAttachmentRecommendation]
    secondary_attachments: list[WeaponAttachmentRecommendation]
    score: float
    reason: str
    score_components: dict[str, float] = field(default_factory=dict)
    inventory_burden: dict[str, Any] = field(default_factory=dict)
    primary_attachment_combination: WeaponAttachmentCombinationRecommendation | None = None
    secondary_attachment_combination: WeaponAttachmentCombinationRecommendation | None = None
    primary_attachment_plan: dict[str, Any] = field(default_factory=dict)
    secondary_attachment_plan: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return {
            "primary": self.primary.to_record(),
            "secondary": self.secondary.to_record(),
            "primary_attachments": [item.to_record() for item in self.primary_attachments],
            "secondary_attachments": [item.to_record() for item in self.secondary_attachments],
            "score": self.score,
            "reason": self.reason,
            "score_components": self.score_components,
            "inventory_burden": self.inventory_burden,
            "primary_attachment_combination": (
                self.primary_attachment_combination.to_record()
                if self.primary_attachment_combination
                else None
            ),
            "secondary_attachment_combination": (
                self.secondary_attachment_combination.to_record()
                if self.secondary_attachment_combination
                else None
            ),
            "primary_attachment_plan": self.primary_attachment_plan,
            "secondary_attachment_plan": self.secondary_attachment_plan,
        }


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
    cluster_id: str | None = None
    grid_size: int = DROP_ZONE_GRID_SIZE
    centroid_x_cm: float | None = None
    centroid_y_cm: float | None = None
    region_status: str = "unmatched"
    region_id: str | None = None
    region_name: str | None = None
    region_name_ko: str | None = None
    region_display_name_ko: str | None = None
    region_geometry_type: str | None = None
    region_distance_to_center_m: float | None = None
    region_radius_m: float | None = None
    region_catalog_version: str | None = None
    region_source_commit: str | None = None
    assists: int = 0
    dbnos: int = 0

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["avg_kills"] = _safe_divide(self.kills, self.match_count)
        record["avg_assists"] = _safe_divide(self.assists, self.match_count)
        record["avg_dbnos"] = _safe_divide(self.dbnos, self.match_count)
        record["avg_deaths"] = _safe_divide(self.deaths, self.match_count)
        return record


@dataclass(frozen=True)
class DropRegionStats:
    map_name: str
    map_name_ko: str
    region_id: str
    region_name_ko: str
    x_pct: float
    y_pct: float
    centroid_x_cm: float | None
    centroid_y_cm: float | None
    match_count: int
    wins: int
    kills: int
    assists: int
    dbnos: int
    deaths: int
    damage_dealt: float
    avg_survival_seconds: float
    win_rate: float
    score: float
    zone_count: int

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["avg_kills"] = _safe_divide(self.kills, self.match_count)
        record["avg_assists"] = _safe_divide(self.assists, self.match_count)
        record["avg_dbnos"] = _safe_divide(self.dbnos, self.match_count)
        record["avg_deaths"] = _safe_divide(self.deaths, self.match_count)
        record["avg_damage_dealt"] = _safe_divide(self.damage_dealt, self.match_count)
        return record


@dataclass(frozen=True)
class PlayerDropZoneReport:
    player: RegisteredPlayer
    min_matches: int
    regions: list[DropRegionStats]
    zones: list[DropZoneRecommendation]

    def to_record(self) -> dict[str, Any]:
        return {
            "player": self.player.to_record(),
            "min_matches": self.min_matches,
            "regions": [item.to_record() for item in self.regions],
            "zones": [item.to_record() for item in self.zones],
        }


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
    attachment_combinations: list[WeaponAttachmentCombinationRecommendation] = field(default_factory=list)
    loadouts: list[WeaponLoadoutRecommendation] = field(default_factory=list)

    def to_record(self) -> dict[str, Any]:
        return {
            "player": self.player.to_record(),
            "min_matches": self.min_matches,
            "loadouts": [item.to_record() for item in self.loadouts],
            "weapons": [item.to_record() for item in self.weapons],
            "weapon_attachments": [item.to_record() for item in self.weapon_attachments],
            "attachment_combinations": [item.to_record() for item in self.attachment_combinations],
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
        weapon_candidates = self._weapon_recommendations(
            player,
            limit=max(limit * 4, 20),
            min_matches=min_matches,
            distance_by_weapon=_distance_by_weapon(weapon_ranges),
        )
        attachment_candidates, attachment_combinations = self._weapon_attachment_recommendations(
            player,
            limit=max(limit * 8, 40),
            min_matches=min_matches,
        )
        return PlayerRecommendationReport(
            player=player,
            min_matches=min_matches,
            weapons=weapon_candidates[:limit],
            weapon_attachments=attachment_candidates,
            weapon_ranges=weapon_ranges,
            attachments=self._attachment_recommendations(player, limit=limit, min_matches=min_matches),
            maps=self._map_recommendations(player, limit=limit, min_matches=min_matches),
            teammates=self._teammate_recommendations(player, limit=limit, min_matches=min_matches),
            drop_zones=self._drop_zone_recommendations(player, limit=limit, min_matches=min_matches),
            attachment_combinations=attachment_combinations,
            loadouts=_build_weapon_loadouts(
                weapon_candidates,
                attachment_candidates,
                attachment_combinations,
                limit=limit,
            ),
        )

    def get_drop_zone_analysis(
        self,
        *,
        shard: str,
        account_id: str | None = None,
        name: str | None = None,
        guild_id: str | None = None,
        global_scope: bool = False,
        limit: int = 100,
        min_matches: int = 1,
    ) -> PlayerDropZoneReport | None:
        player = self._get_player(
            shard=shard,
            account_id=account_id,
            name=name,
            guild_id=guild_id,
            global_scope=global_scope,
        )
        if player is None:
            return None
        normalized_limit = max(1, min(int(limit), 500))
        normalized_min_matches = max(1, int(min_matches))
        zones = self._drop_zone_recommendations(
            player,
            limit=500,
            min_matches=1,
        )
        regions = _aggregate_drop_regions(
            zones,
            min_matches=normalized_min_matches,
            limit=normalized_limit,
        )
        visible_zones = [
            zone for zone in zones if zone.match_count >= normalized_min_matches
        ][:normalized_limit]
        return PlayerDropZoneReport(
            player=player,
            min_matches=normalized_min_matches,
            regions=regions,
            zones=visible_zones,
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
                    outcomes.weapon_code,
                    COUNT(*) AS fight_count,
                    COALESCE(SUM(outcomes.outcome_type = 'win'), 0) AS fight_wins,
                    COALESCE(SUM(outcomes.outcome_type = 'loss'), 0) AS fight_losses
                FROM player_fight_outcomes outcomes
                INNER JOIN analysis_matches AS matches
                    ON matches.match_id = outcomes.match_id
                WHERE outcomes.account_id = %s
                  AND matches.shard = %s
                  AND outcomes.is_friendly_fire = 0
                  AND outcomes.weapon_code IS NOT NULL
                GROUP BY outcomes.weapon_code
                """,
                (player.account_id, player.shard),
            )
            fight_rows = cursor.fetchall()
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
                    COALESCE(SUM(weapon_stats.shots_hit), 0) AS shots_hit,
                    COALESCE(SUM(weapon_stats.character_hits), 0) AS character_hits,
                    COALESCE(SUM(weapon_stats.vehicle_hits), 0) AS vehicle_hits,
                    COALESCE(SUM(weapon_stats.vehicle_damage_dealt), 0) AS vehicle_damage_dealt,
                    COALESCE(SUM(weapon_stats.headshot_hits), 0) AS headshot_hits
                FROM player_weapon_match_stats weapon_stats
                INNER JOIN analysis_matches AS matches
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

        fight_by_weapon = {
            normalize_weapon_code(str(row["weapon_code"])): row
            for row in fight_rows
            if row.get("weapon_code")
        }
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
            character_hits = (
                shots_hit
                if row.get("character_hits") is None
                else _int(row.get("character_hits"))
            )
            headshot_hits = _int(row.get("headshot_hits"))
            weapon_code = str(row["weapon_code"])
            fight_row = fight_by_weapon.get(normalize_weapon_code(weapon_code), {})
            fight_count = _int(fight_row.get("fight_count"))
            fight_wins = _int(fight_row.get("fight_wins"))
            fight_losses = _int(fight_row.get("fight_losses"))
            fight_win_rate = _safe_divide(fight_wins, fight_wins + fight_losses)
            accuracy_metric = weapon_accuracy_metric(weapon_code, shots_fired, shots_hit)
            accuracy = accuracy_metric.estimated_hit_rate or 0.0
            top_distance_buckets = list(distance_by_weapon.get(weapon_code, []))[:3]
            range_evidence_events = sum(bucket.event_count for bucket in top_distance_buckets)
            # Distance rows contain successful kill/DBNO/finish events, not all
            # attempted fights. Treat them as bounded evidence confidence rather
            # than a win-rate-like performance score.
            range_score = min(12.0, sqrt(range_evidence_events) * 1.2)
            score_components = _performance_score_components(
                match_count=match_count,
                wins=wins,
                kills=kills,
                assists=assists,
                deaths=deaths,
                dbnos=dbnos,
                damage_dealt=damage_dealt,
                accuracy_score=recommendation_accuracy_score(accuracy_metric),
            )
            fight_confidence = min(1.0, sqrt(fight_count / 20)) if fight_count else 0.0
            fight_adjustment = (fight_win_rate - 0.5) * 40 * fight_confidence if fight_count else 0.0
            score = score_components["confidence_adjusted_score"] + range_score + fight_adjustment
            score_components["range_bonus"] = range_score
            score_components["range_evidence_events"] = float(range_evidence_events)
            score_components["range_bonus_cap"] = 12.0
            score_components["fight_win_rate"] = fight_win_rate
            score_components["fight_confidence"] = fight_confidence
            score_components["fight_adjustment"] = fight_adjustment
            score_components["total_score"] = score
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
                    character_hits=character_hits,
                    vehicle_hits=_int(row.get("vehicle_hits")),
                    vehicle_damage_dealt=_float(row.get("vehicle_damage_dealt")),
                    win_rate=_safe_divide(wins, match_count),
                    kills_per_match=_safe_divide(kills, match_count),
                    dbnos_per_match=_safe_divide(dbnos, match_count),
                    avg_damage_dealt=_safe_divide(damage_dealt, match_count),
                    accuracy=accuracy,
                    reason=(
                        f"{match_count}경기 · 승률 {_safe_divide(wins, match_count) * 100:.1f}% · "
                        f"평균 피해 {_safe_divide(damage_dealt, match_count):.1f} · "
                        f"경기당 킬 {_safe_divide(kills, match_count):.2f}"
                    ),
                    headshot_hits=headshot_hits,
                    headshot_hit_rate=_safe_divide(headshot_hits, character_hits),
                    fight_count=fight_count,
                    fight_wins=fight_wins,
                    fight_losses=fight_losses,
                    fight_win_rate=fight_win_rate,
                    accuracy_metric=accuracy_metric,
                    range_score=range_score,
                    top_distance_buckets=top_distance_buckets,
                    score_components=score_components,
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
                INNER JOIN analysis_matches AS matches
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
                    reason=f"{bucket.label} · {event_count}교전 · {kills}킬 · {dbnos}기절",
                )
            )
        return _top(recommendations, limit)

    def _weapon_attachment_recommendations(
        self,
        player: RegisteredPlayer,
        *,
        limit: int,
        min_matches: int,
    ) -> tuple[
        list[WeaponAttachmentRecommendation],
        list[WeaponAttachmentCombinationRecommendation],
    ]:
        snapshot_recommendations, combinations = self._loadout_snapshot_attachment_recommendations(
            player,
            limit=limit,
            min_matches=min_matches,
        )
        if snapshot_recommendations:
            return snapshot_recommendations, combinations
        return (
            self._attach_event_weapon_attachment_recommendations(
                player,
                limit=limit,
                min_matches=min_matches,
            ),
            combinations,
        )

    def _loadout_snapshot_attachment_recommendations(
        self,
        player: RegisteredPlayer,
        *,
        limit: int,
        min_matches: int,
    ) -> tuple[
        list[WeaponAttachmentRecommendation],
        list[WeaponAttachmentCombinationRecommendation],
    ]:
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
                INNER JOIN analysis_matches AS matches
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
                    weapon_name=translate_code(weapon_code, "damage_causer"),
                    attachment_code=attachment_code,
                    attachment_name=translate_code(attachment_code, "item"),
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
                        f"{translate_code(weapon_code, 'damage_causer')} + "
                        f"{translate_code(attachment_code, 'item')} · {event_count}교전 스냅샷"
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
        return (
            _top(recommendations, limit),
            _attachment_combinations_from_snapshot_rows(
                rows,
                limit=limit,
                min_matches=min_matches,
            ),
        )

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
                INNER JOIN analysis_matches AS matches
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
                    attachment_name=translate_code(attachment_code, "item"),
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
                        f"{translate_code(weapon_code, 'damage_causer')} + "
                        f"{translate_code(attachment_code, 'item')} · {match_count}경기"
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
                INNER JOIN analysis_matches AS matches
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
            attachment_names = tuple(translate_code(code, "item") for code in attachment_codes)
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
                    weapon_name=translate_code(str(row.get("weapon_code") or weapon_code), "damage_causer"),
                    attachment_code=attachment_code,
                    attachment_name=translate_code(attachment_code, "item"),
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
                INNER JOIN analysis_matches AS matches
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
            item_name = translate_code(item_code, "item")
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
                    reason=(
                        f"{attached_events}회 장착 · "
                        f"평균 피해 {_safe_divide(damage_dealt, match_count):.1f}"
                    ),
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
                INNER JOIN analysis_matches AS matches
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
                INNER JOIN analysis_matches AS matches
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
                    summaries.assists,
                    summaries.dbnos_caused,
                    summaries.deaths,
                    summaries.damage_dealt,
                    movement.landing_x,
                    movement.landing_y
                FROM player_movement_summaries movement
                INNER JOIN analysis_matches AS matches
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
            grid_x = min(DROP_ZONE_GRID_SIZE - 1, int(x_pct * DROP_ZONE_GRID_SIZE))
            grid_y = min(DROP_ZONE_GRID_SIZE - 1, int(y_pct * DROP_ZONE_GRID_SIZE))
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
                    "assists": 0,
                    "dbnos": 0,
                    "deaths": 0,
                    "damage_dealt": 0.0,
                    "survival_seconds": 0.0,
                    "x_cm_sum": 0.0,
                    "y_cm_sum": 0.0,
                    "x_pct_sum": 0.0,
                    "y_pct_sum": 0.0,
                },
            )
            bucket["match_count"] += 1
            bucket["wins"] += 1 if _optional_int(row.get("win_place")) == 1 else 0
            bucket["kills"] += _int(row.get("kills"))
            bucket["assists"] += _int(row.get("assists"))
            bucket["dbnos"] += _int(row.get("dbnos_caused"))
            bucket["deaths"] += _int(row.get("deaths"))
            bucket["damage_dealt"] += _float(row.get("damage_dealt"))
            bucket["survival_seconds"] += _survival_seconds_from_row(row)
            bucket["x_cm_sum"] += _float(row.get("landing_x"))
            bucket["y_cm_sum"] += _float(row.get("landing_y"))
            bucket["x_pct_sum"] += x_pct
            bucket["y_pct_sum"] += y_pct

        recommendations: list[DropZoneRecommendation] = []
        for bucket in clusters.values():
            match_count = _int(bucket["match_count"])
            if match_count < min_matches:
                continue
            wins = _int(bucket["wins"])
            kills = _int(bucket["kills"])
            assists = _int(bucket["assists"])
            dbnos = _int(bucket["dbnos"])
            deaths = _int(bucket["deaths"])
            damage_dealt = _float(bucket["damage_dealt"])
            score = _performance_score(
                match_count=match_count,
                wins=wins,
                kills=kills,
                assists=assists,
                deaths=deaths,
                dbnos=dbnos,
                damage_dealt=damage_dealt,
            ) + _safe_divide(bucket["survival_seconds"], match_count) / 20
            map_name = str(bucket["map_name"])
            centroid_x_cm = _safe_divide(bucket["x_cm_sum"], match_count)
            centroid_y_cm = _safe_divide(bucket["y_cm_sum"], match_count)
            region = resolve_map_region(map_name, centroid_x_cm, centroid_y_cm)
            cluster_id = (
                f"{map_name}:grid{DROP_ZONE_GRID_SIZE}:"
                f"{bucket['grid_x']}:{bucket['grid_y']}"
            )
            region_label = region.region_display_name_ko or f"grid {bucket['grid_x']},{bucket['grid_y']}"
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
                    reason=f"{region_label} · 승률 {_safe_divide(wins, match_count) * 100:.1f}%",
                    cluster_id=cluster_id,
                    grid_size=DROP_ZONE_GRID_SIZE,
                    centroid_x_cm=centroid_x_cm,
                    centroid_y_cm=centroid_y_cm,
                    region_status=region.status,
                    region_id=region.region_id,
                    region_name=region.region_name,
                    region_name_ko=region.region_name_ko,
                    region_display_name_ko=region.region_display_name_ko,
                    region_geometry_type=region.geometry_type,
                    region_distance_to_center_m=region.distance_to_center_m,
                    region_radius_m=region.radius_m,
                    region_catalog_version=region.catalog_version,
                    region_source_commit=region.source_commit,
                    assists=assists,
                    dbnos=dbnos,
                )
            )
        return _top(recommendations, limit)


def _aggregate_drop_regions(
    zones: list[DropZoneRecommendation],
    *,
    min_matches: int,
    limit: int,
) -> list[DropRegionStats]:
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for zone in zones:
        region_id = zone.region_id or zone.cluster_id or f"grid:{zone.grid_x}:{zone.grid_y}"
        key = (zone.map_name, region_id)
        group = groups.setdefault(
            key,
            {
                "map_name": zone.map_name,
                "map_name_ko": zone.map_name_ko,
                "region_id": region_id,
                "region_name_ko": zone.region_display_name_ko or f"격자 {zone.grid_x},{zone.grid_y}",
                "match_count": 0,
                "wins": 0,
                "kills": 0,
                "assists": 0,
                "dbnos": 0,
                "deaths": 0,
                "damage_dealt": 0.0,
                "survival_seconds": 0.0,
                "x_pct_sum": 0.0,
                "y_pct_sum": 0.0,
                "centroid_x_cm_sum": 0.0,
                "centroid_y_cm_sum": 0.0,
                "centroid_match_count": 0,
                "zone_count": 0,
            },
        )
        group["match_count"] += zone.match_count
        group["wins"] += zone.wins
        group["kills"] += zone.kills
        group["assists"] += zone.assists
        group["dbnos"] += zone.dbnos
        group["deaths"] += zone.deaths
        group["damage_dealt"] += zone.damage_dealt
        group["survival_seconds"] += zone.avg_survival_seconds * zone.match_count
        group["x_pct_sum"] += zone.x_pct * zone.match_count
        group["y_pct_sum"] += zone.y_pct * zone.match_count
        if zone.centroid_x_cm is not None and zone.centroid_y_cm is not None:
            group["centroid_x_cm_sum"] += zone.centroid_x_cm * zone.match_count
            group["centroid_y_cm_sum"] += zone.centroid_y_cm * zone.match_count
            group["centroid_match_count"] += zone.match_count
        group["zone_count"] += 1

    regions: list[DropRegionStats] = []
    for group in groups.values():
        match_count = _int(group["match_count"])
        if match_count < min_matches:
            continue
        wins = _int(group["wins"])
        kills = _int(group["kills"])
        assists = _int(group["assists"])
        dbnos = _int(group["dbnos"])
        deaths = _int(group["deaths"])
        damage_dealt = _float(group["damage_dealt"])
        avg_survival_seconds = _safe_divide(group["survival_seconds"], match_count)
        centroid_match_count = _int(group["centroid_match_count"])
        score = _performance_score(
            match_count=match_count,
            wins=wins,
            kills=kills,
            assists=assists,
            deaths=deaths,
            dbnos=dbnos,
            damage_dealt=damage_dealt,
        ) + avg_survival_seconds / 20
        regions.append(
            DropRegionStats(
                map_name=str(group["map_name"]),
                map_name_ko=str(group["map_name_ko"]),
                region_id=str(group["region_id"]),
                region_name_ko=str(group["region_name_ko"]),
                x_pct=_safe_divide(group["x_pct_sum"], match_count),
                y_pct=_safe_divide(group["y_pct_sum"], match_count),
                centroid_x_cm=(
                    _safe_divide(group["centroid_x_cm_sum"], centroid_match_count)
                    if centroid_match_count
                    else None
                ),
                centroid_y_cm=(
                    _safe_divide(group["centroid_y_cm_sum"], centroid_match_count)
                    if centroid_match_count
                    else None
                ),
                match_count=match_count,
                wins=wins,
                kills=kills,
                assists=assists,
                dbnos=dbnos,
                deaths=deaths,
                damage_dealt=damage_dealt,
                avg_survival_seconds=avg_survival_seconds,
                win_rate=_safe_divide(wins, match_count),
                score=score,
                zone_count=_int(group["zone_count"]),
            )
        )
    return _top(regions, limit)


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


def _attachment_combinations_from_snapshot_rows(
    rows: list[Mapping[str, Any]],
    *,
    limit: int,
    min_matches: int,
) -> list[WeaponAttachmentCombinationRecommendation]:
    grouped: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    for row in rows:
        weapon_code = str(row.get("weapon_code") or "")
        match_id = str(row.get("match_id") or "")
        attachment_codes = tuple(sorted(set(_json_string_list(row.get("attachment_codes")))))
        if not weapon_code.startswith("Weap") or not match_id or not attachment_codes:
            continue
        key = (weapon_code, attachment_codes)
        record = grouped.setdefault(
            key,
            {
                "weapon_code": weapon_code,
                "attachment_codes": attachment_codes,
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

    combinations: list[WeaponAttachmentCombinationRecommendation] = []
    for record in grouped.values():
        match_count = len(record["match_ids"])
        if match_count < min_matches:
            continue
        event_count = _int(record["event_count"])
        wins = len(record["win_match_ids"])
        kills = _int(record["kills"])
        dbnos = _int(record["dbnos"])
        finishes = _int(record["finishes"])
        headshots = _int(record["headshots"])
        damage_dealt = sum(_float(value) for value in record["damage_by_match"].values())
        score_components = {
            "kills": kills * 120.0,
            "dbnos": dbnos * 70.0,
            "finishes": finishes * 40.0,
            "headshots": headshots * 20.0,
            "events": event_count * 8.0,
            "wins": wins * 50.0,
            "average_damage": _safe_divide(damage_dealt, match_count) * 0.15,
        }
        score = sum(score_components.values())
        weapon_code = str(record["weapon_code"])
        attachment_codes = tuple(record["attachment_codes"])
        attachment_names = tuple(translate_code(code, "item") for code in attachment_codes)
        combinations.append(
            WeaponAttachmentCombinationRecommendation(
                weapon_code=weapon_code,
                weapon_name=translate_code(weapon_code, "damage_causer"),
                attachment_codes=attachment_codes,
                attachment_names=attachment_names,
                score=score,
                match_count=match_count,
                event_count=event_count,
                wins=wins,
                kills=kills,
                dbnos=dbnos,
                finishes=finishes,
                headshots=headshots,
                damage_dealt=damage_dealt,
                win_rate=_safe_divide(wins, match_count),
                avg_damage_dealt=_safe_divide(damage_dealt, match_count),
                avg_distance_m=(
                    _safe_divide(record["distance_sum"], record["distance_count"])
                    if record["distance_count"]
                    else None
                ),
                reason=(
                    f"{match_count}경기 · {event_count}교전 · {kills}킬 · "
                    f"{dbnos}기절 · 승률 {_safe_divide(wins, match_count) * 100:.1f}%"
                ),
                score_components=score_components,
            )
        )
    ranked = _top(combinations, max(1, min(int(limit), 100)))
    per_weapon: dict[str, int] = {}
    selected: list[WeaponAttachmentCombinationRecommendation] = []
    for item in ranked:
        if per_weapon.get(item.weapon_code, 0) >= 5:
            continue
        selected.append(item)
        per_weapon[item.weapon_code] = per_weapon.get(item.weapon_code, 0) + 1
    return selected


def _build_weapon_loadouts(
    weapons: list[WeaponRecommendation],
    attachments: list[WeaponAttachmentRecommendation],
    attachment_combinations: list[WeaponAttachmentCombinationRecommendation],
    *,
    limit: int,
) -> list[WeaponLoadoutRecommendation]:
    close_range = [
        weapon
        for weapon in weapons
        if weapon_family(weapon.weapon_code) in {"AR", "SMG", "LMG", "SHOTGUN"}
    ]
    long_range = [
        weapon
        for weapon in weapons
        if weapon_family(weapon.weapon_code) in {"DMR", "SR", "CROSSBOW"}
    ]
    attachments_by_weapon: dict[str, list[WeaponAttachmentRecommendation]] = {}
    for attachment in _top(attachments, len(attachments)):
        selected = attachments_by_weapon.setdefault(attachment.weapon_code, [])
        slot = _attachment_slot(attachment)
        if any(_attachment_slot(existing) == slot for existing in selected):
            continue
        selected.append(attachment)
    combinations_by_weapon: dict[str, list[WeaponAttachmentCombinationRecommendation]] = {}
    for combination in attachment_combinations:
        combinations_by_weapon.setdefault(combination.weapon_code, []).append(combination)
    best_combination_by_weapon = {
        weapon_code: max(
            candidates,
            key=lambda item: (
                len({_attachment_slot_from_code(code) for code in item.attachment_codes}),
                item.match_count,
                item.score,
                item.event_count,
            ),
        )
        for weapon_code, candidates in combinations_by_weapon.items()
    }
    attachment_by_weapon_code = {
        (attachment.weapon_code, attachment.attachment_code): attachment
        for attachment in attachments
    }

    def selected_attachments(
        weapon_code: str,
        combination: WeaponAttachmentCombinationRecommendation | None,
    ) -> tuple[list[WeaponAttachmentRecommendation], dict[str, Any]]:
        candidates = attachments_by_weapon.get(weapon_code, [])[:5]
        selected: list[WeaponAttachmentRecommendation] = []
        selected_slots: set[str] = set()
        observed_slots: set[str] = set()
        if combination:
            observed = [
                attachment_by_weapon_code[(weapon_code, code)]
                for code in combination.attachment_codes
                if (weapon_code, code) in attachment_by_weapon_code
            ]
            for attachment in observed:
                slot = _attachment_slot(attachment)
                observed_slots.add(slot)
                if slot in selected_slots:
                    continue
                selected.append(attachment)
                selected_slots.add(slot)

        supplemented = False
        for attachment in candidates:
            slot = _attachment_slot(attachment)
            if slot in selected_slots:
                continue
            selected.append(attachment)
            selected_slots.add(slot)
            supplemented = supplemented or bool(combination)

        selected.sort(key=lambda item: _attachment_slot_sort_key(_attachment_slot(item)))
        known_slots = {_attachment_slot(item) for item in candidates} | observed_slots
        if combination and supplemented:
            basis = "실전 관측 조합의 빈 슬롯을 무기별 성과 1위 파츠로 보완"
        elif combination:
            basis = "실전에서 함께 사용한 파츠 조합"
        elif selected:
            basis = "무기별 슬롯 성과 1위 파츠 조합"
        else:
            basis = "호환 파츠 실전 표본 부족"
        evidence_matches = combination.match_count if combination else max(
            (item.match_count for item in selected),
            default=0,
        )
        confidence = "높음" if evidence_matches >= 15 else "보통" if evidence_matches >= 5 else "낮음"
        plan = {
            "basis": basis,
            "confidence": confidence,
            "evidence_match_count": evidence_matches,
            "known_slot_count": len(known_slots),
            "selected_slot_count": len(selected_slots),
            "observed_combination_slot_count": len(observed_slots),
            "is_complete_for_observed_slots": bool(known_slots) and known_slots <= selected_slots,
            "supplemented": supplemented,
            "slot_labels": [
                _attachment_slot_label(slot)
                for slot in sorted(selected_slots, key=_attachment_slot_sort_key)
            ],
        }
        return selected, plan

    loadouts: list[WeaponLoadoutRecommendation] = []
    for primary in close_range:
        for secondary in long_range:
            primary_combination = best_combination_by_weapon.get(primary.weapon_code)
            secondary_combination = best_combination_by_weapon.get(secondary.weapon_code)
            primary_attachments, primary_attachment_plan = selected_attachments(
                primary.weapon_code,
                primary_combination,
            )
            secondary_attachments, secondary_attachment_plan = selected_attachments(
                secondary.weapon_code,
                secondary_combination,
            )
            inventory_burden = _inventory_burden(primary, secondary)
            primary_component = primary.score * 0.55
            secondary_component = secondary.score * 0.45
            inventory_adjustment = _float(inventory_burden["score_adjustment"])
            score = primary_component + secondary_component + inventory_adjustment
            loadouts.append(
                WeaponLoadoutRecommendation(
                    primary=primary,
                    secondary=secondary,
                    primary_attachments=primary_attachments,
                    secondary_attachments=secondary_attachments,
                    score=score,
                    reason=(
                        f"근·중거리 {primary.weapon_name} + 중·장거리 {secondary.weapon_name} · "
                        f"{inventory_burden['summary']}"
                    ),
                    score_components={
                        "primary_performance_55pct": primary_component,
                        "secondary_performance_45pct": secondary_component,
                        "inventory_adjustment": inventory_adjustment,
                        "total_score": score,
                    },
                    inventory_burden=inventory_burden,
                    primary_attachment_combination=primary_combination,
                    secondary_attachment_combination=secondary_combination,
                    primary_attachment_plan=primary_attachment_plan,
                    secondary_attachment_plan=secondary_attachment_plan,
                )
            )
    return _top(loadouts, max(1, min(int(limit), 20)))


def _inventory_burden(
    primary: WeaponRecommendation,
    secondary: WeaponRecommendation,
) -> dict[str, Any]:
    weapons = [primary, secondary]
    profiles: list[dict[str, Any]] = []
    for weapon in weapons:
        family = weapon_family(weapon.weapon_code)
        ammo_type = AMMO_TYPE_BY_WEAPON.get(weapon.weapon_code, "알 수 없음")
        reserve_rounds = RESERVE_ROUNDS_BY_WEAPON.get(
            weapon.weapon_code,
            RESERVE_ROUNDS_BY_FAMILY.get(family, 100),
        )
        per_round_weight = AMMO_INVENTORY_WEIGHT_PER_ROUND.get(ammo_type, 0.5)
        profiles.append(
            {
                "weapon_code": weapon.weapon_code,
                "weapon_name": weapon.weapon_name,
                "weapon_family": family,
                "ammo_type": ammo_type,
                "recommended_reserve_rounds": reserve_rounds,
                "observed_shots_per_match": _safe_divide(weapon.shots_fired, weapon.match_count),
                "inventory_weight_per_round": per_round_weight,
                "reserve_inventory_weight": reserve_rounds * per_round_weight,
            }
        )

    known_ammo_types = {
        profile["ammo_type"]
        for profile in profiles
        if profile["ammo_type"] != "알 수 없음"
    }
    mixed_ammo = len(known_ammo_types) > 1
    shared_ammo = len(known_ammo_types) == 1
    lmg_count = sum(profile["weapon_family"] == "LMG" for profile in profiles)
    carried_rounds_by_ammo: dict[str, int] = {}
    if shared_ammo and profiles:
        reserves = sorted(
            (_int(profile["recommended_reserve_rounds"]) for profile in profiles),
            reverse=True,
        )
        pooled_rounds = reserves[0] + _round_up_to_ten(reserves[1] * 0.35)
        carried_rounds_by_ammo[next(iter(known_ammo_types))] = pooled_rounds
    else:
        for profile in profiles:
            ammo_type = str(profile["ammo_type"])
            if ammo_type == "알 수 없음":
                continue
            carried_rounds_by_ammo[ammo_type] = (
                carried_rounds_by_ammo.get(ammo_type, 0)
                + _int(profile["recommended_reserve_rounds"])
            )
    weight_by_ammo = {
        ammo_type: rounds * AMMO_INVENTORY_WEIGHT_PER_ROUND.get(ammo_type, 0.5)
        for ammo_type, rounds in carried_rounds_by_ammo.items()
    }
    total_inventory_weight = sum(weight_by_ammo.values())
    mixed_ammo_penalty = 3.0 if mixed_ammo else 0.0
    shared_ammo_bonus = 3.0 if shared_ammo else 0.0
    reserve_pressure_penalty = max(0.0, total_inventory_weight - 85.0) * 0.12
    lmg_extra_reserve_inventory_weight = sum(
        max(
            0,
            _int(profile["recommended_reserve_rounds"]) - RESERVE_ROUNDS_BY_FAMILY["AR"],
        )
        * _float(profile["inventory_weight_per_round"])
        for profile in profiles
        if profile["weapon_family"] == "LMG"
    )
    # This is the LMG-attributable portion of reserve_pressure_penalty, not an extra deduction.
    lmg_reserve_penalty = min(
        reserve_pressure_penalty,
        lmg_extra_reserve_inventory_weight * 0.12,
    )
    adjustment = shared_ammo_bonus - mixed_ammo_penalty - reserve_pressure_penalty
    if total_inventory_weight >= 140:
        pressure_level = "높음"
    elif total_inventory_weight >= 100:
        pressure_level = "보통"
    else:
        pressure_level = "낮음"

    tradeoffs: list[str] = []
    if mixed_ammo:
        tradeoffs.append("탄종 2종을 각각 확보·소지하므로 회복·투척 아이템 또는 예비탄 여유가 줄 수 있음")
    elif shared_ammo:
        tradeoffs.append("같은 탄종을 공유해 두 무기의 예비탄을 하나의 탄약 풀로 운용함")
    if lmg_count:
        tradeoffs.append(
            "LMG 지속 사격용 초과 예비탄 "
            f"{lmg_extra_reserve_inventory_weight:.1f} 인벤토리 단위가 전체 탄약 부담에 포함됨"
        )
    if not tradeoffs:
        tradeoffs.append("탄약 운용 부담이 일반적인 수준")

    ammo_label = " + ".join(sorted(known_ammo_types)) if known_ammo_types else "탄종 확인 불가"
    return {
        "model_version": "inventory-weight-v3",
        "is_heuristic": True,
        "ammo_types": sorted(known_ammo_types),
        "ammo_label": ammo_label,
        "mixed_ammo": mixed_ammo,
        "shared_ammo": shared_ammo,
        "lmg_count": lmg_count,
        "weapon_profiles": profiles,
        "carried_rounds_by_ammo": carried_rounds_by_ammo,
        "inventory_weight_by_ammo": weight_by_ammo,
        "estimated_inventory_weight": total_inventory_weight,
        "relative_pressure_index": total_inventory_weight,
        "pressure_level": pressure_level,
        "mixed_ammo_penalty": mixed_ammo_penalty,
        "shared_ammo_bonus": shared_ammo_bonus,
        "reserve_pressure_penalty": reserve_pressure_penalty,
        "lmg_extra_reserve_inventory_weight": lmg_extra_reserve_inventory_weight,
        "lmg_reserve_penalty": lmg_reserve_penalty,
        "score_adjustment": adjustment,
        "tradeoffs": tradeoffs,
        "summary": (
            f"{ammo_label} · 예상 탄약 인벤토리 {total_inventory_weight:.1f}단위 · "
            f"부담 {pressure_level}"
        ),
        "basis": (
            "kg가 아닌 PUBG 인벤토리 단위로, 발당 무게 × 권장 예비탄을 계산합니다. "
            "동일 탄종은 공유 탄약 풀로 계산하고 LMG의 AR 기준 초과 예비탄도 전체 무게 부담에 "
            "포함합니다. 권장 예비탄 수와 점수 가중치는 휴리스틱입니다."
        ),
    }


def _attachment_slot(item: WeaponAttachmentRecommendation) -> str:
    return _attachment_slot_from_code(
        item.attachment_code,
        fallback=item.attachment_sub_category or item.attachment_category or item.attachment_code,
    )


def _attachment_slot_from_code(code: str, *, fallback: str | None = None) -> str:
    normalized = str(code or "").lower()
    for marker in ("upper", "lower", "muzzle", "magazine", "stock"):
        if f"_{marker}_" in normalized:
            return marker
    return str(fallback or code or "unknown").lower()


_ATTACHMENT_SLOT_ORDER = ("muzzle", "lower", "magazine", "stock", "upper")


def _attachment_slot_sort_key(slot: str) -> tuple[int, str]:
    try:
        return (_ATTACHMENT_SLOT_ORDER.index(slot), slot)
    except ValueError:
        return (len(_ATTACHMENT_SLOT_ORDER), slot)


def _attachment_slot_label(slot: str) -> str:
    return {
        "muzzle": "총구",
        "lower": "손잡이",
        "magazine": "탄창",
        "stock": "개머리판",
        "upper": "조준경",
    }.get(slot, slot)


def _round_up_to_ten(value: float) -> int:
    return int(ceil(max(0.0, value) / 10) * 10)


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
    return distance_weapon_family(weapon_code)


def _performance_score(
    *,
    match_count: int,
    wins: int,
    kills: int = 0,
    assists: int = 0,
    deaths: int = 0,
    dbnos: int = 0,
    damage_dealt: float = 0.0,
    accuracy_score: float = 0.0,
) -> float:
    return _performance_score_components(
        match_count=match_count,
        wins=wins,
        kills=kills,
        assists=assists,
        deaths=deaths,
        dbnos=dbnos,
        damage_dealt=damage_dealt,
        accuracy_score=accuracy_score,
    )["confidence_adjusted_score"]


def _performance_score_components(
    *,
    match_count: int,
    wins: int,
    kills: int = 0,
    assists: int = 0,
    deaths: int = 0,
    dbnos: int = 0,
    damage_dealt: float = 0.0,
    accuracy_score: float = 0.0,
) -> dict[str, float]:
    if match_count <= 0:
        return {
            "average_damage": 0.0,
            "kills": 0.0,
            "dbnos": 0.0,
            "assists": 0.0,
            "wins": 0.0,
            "accuracy": 0.0,
            "deaths_penalty": 0.0,
            "raw_score": 0.0,
            "confidence_factor": 0.65,
            "confidence_adjusted_score": 0.0,
        }
    avg_damage = _safe_divide(damage_dealt, match_count)
    kills_per_match = _safe_divide(kills, match_count)
    dbnos_per_match = _safe_divide(dbnos, match_count)
    assists_per_match = _safe_divide(assists, match_count)
    deaths_per_match = _safe_divide(deaths, match_count)
    win_rate = _safe_divide(wins, match_count)
    confidence = min(1.0, match_count / 5)
    components = {
        "average_damage": avg_damage,
        "kills": kills_per_match * 85,
        "dbnos": dbnos_per_match * 35,
        "assists": assists_per_match * 20,
        "wins": win_rate * 120,
        "accuracy": accuracy_score * 60,
        "deaths_penalty": -(deaths_per_match * 25),
    }
    raw_score = sum(components.values())
    confidence_factor = 0.65 + confidence * 0.35
    return {
        **components,
        "raw_score": raw_score,
        "confidence_factor": confidence_factor,
        "confidence_adjusted_score": max(0.0, raw_score) * confidence_factor,
    }


def _reason(match_count: int, wins: int, damage_dealt: float, kills: int) -> str:
    return (
        f"{match_count}경기 · 승률 {_safe_divide(wins, match_count) * 100:.1f}% · "
        f"평균 피해 {_safe_divide(damage_dealt, match_count):.1f} · "
        f"경기당 킬 {_safe_divide(kills, match_count):.2f}"
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


def _clamped(value: float) -> float:
    return max(0.0, min(1.0, value))
