from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any, Iterable, Mapping
import json

from pubg_ai.code_translator import translate_code
from pubg_ai.metric_catalog import metric_catalog_records
from pubg_ai.player_registry import RegisteredPlayer
from pubg_ai.player_stats import PlayerStatsService
from pubg_ai.player_trends import PlayerTrendFilters, PlayerTrendService
from pubg_ai.telemetry_activity_processor import PARSER_VERSION as ACTIVITY_PARSER_VERSION
from pubg_ai.telemetry_event_catalog import get_telemetry_event_definition
from pubg_ai.time_utils import isoformat_kst


MATCH_ID_QUERY_CHUNK_SIZE = 500


@dataclass(frozen=True)
class PlayerIntelligenceReport:
    player: RegisteredPlayer
    filters: PlayerTrendFilters
    generated_at_kst: str
    coverage: dict[str, Any]
    overview: dict[str, Any]
    combat: dict[str, Any]
    survival: dict[str, Any]
    support: dict[str, Any]
    loot: dict[str, Any]
    mobility: dict[str, Any]
    vehicle: dict[str, Any]
    environment: dict[str, Any]
    breakdowns: dict[str, Any]
    trends: dict[str, Any]
    activity_details: dict[str, Any]
    metric_definitions: list[dict[str, str]]

    def to_record(self) -> dict[str, Any]:
        return {
            "player": self.player.to_record(),
            "filters": self.filters.to_record(),
            "timezone": "Asia/Seoul",
            "generated_at_kst": self.generated_at_kst,
            "coverage": self.coverage,
            "overview": self.overview,
            "combat": self.combat,
            "survival": self.survival,
            "support": self.support,
            "loot": self.loot,
            "mobility": self.mobility,
            "vehicle": self.vehicle,
            "environment": self.environment,
            "breakdowns": self.breakdowns,
            "trends": self.trends,
            "activity_details": self.activity_details,
            "metric_definitions": self.metric_definitions,
        }


class PlayerIntelligenceService:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def get_report(
        self,
        *,
        shard: str,
        account_id: str | None = None,
        name: str | None = None,
        guild_id: str | None = None,
        global_scope: bool = False,
        filters: PlayerTrendFilters | None = None,
        trend_limit: int = 365,
    ) -> PlayerIntelligenceReport | None:
        normalized_filters = (filters or PlayerTrendFilters()).normalized()
        profile = PlayerStatsService(self.connection).get_profile(
            shard=shard,
            account_id=account_id,
            name=name,
            guild_id=guild_id,
            global_scope=global_scope,
            weapon_limit=1,
            recent_limit=1,
        )
        if profile is None:
            return None

        player = profile.player
        rows = self._get_match_rows(player, normalized_filters)
        match_ids = [str(row["match_id"]) for row in rows]
        fight_rows = self._get_fight_rows(player, match_ids)
        fights_by_match = {str(row["match_id"]): row for row in fight_rows}
        item_rows = self._get_item_rows(player, match_ids)
        details = self._get_activity_details(player, match_ids)
        core_trends = PlayerTrendService(self.connection).get_report(
            shard=player.shard,
            account_id=player.account_id,
            guild_id=guild_id,
            global_scope=global_scope,
            filters=normalized_filters,
            granularity="month",
            bucket_limit=max(1, min(int(trend_limit), 1000)),
        )
        summary = summarize_player_intelligence(
            rows,
            fight_rows=fight_rows,
            item_rows=item_rows,
            activity_details=details,
            trend_limit=max(1, min(int(trend_limit), 1000)),
        )
        combat = summary["combat"]
        if core_trends is not None:
            combat.update(core_trends.totals.to_record())
        coverage = self._coverage(player, normalized_filters, rows)
        coverage["event_type_coverage"] = self._event_type_coverage(player, normalized_filters)

        return PlayerIntelligenceReport(
            player=player,
            filters=normalized_filters,
            generated_at_kst=isoformat_kst(),
            coverage=coverage,
            overview=summary["overview"],
            combat=combat,
            survival=summary["survival"],
            support=summary["support"],
            loot=summary["loot"],
            mobility=summary["mobility"],
            vehicle=summary["vehicle"],
            environment=summary["environment"],
            breakdowns=summary["breakdowns"],
            trends=summary["trends"],
            activity_details=details,
            metric_definitions=metric_catalog_records(),
        )

    def _get_match_rows(
        self,
        player: RegisteredPlayer,
        filters: PlayerTrendFilters,
    ) -> list[dict[str, Any]]:
        conditions, params = _match_filter_sql(
            account_id=player.account_id,
            shard=player.shard,
            filters=filters,
            participant_alias="participants",
        )
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    participants.match_id,
                    participants.win_place,
                    participants.kills AS participant_kills,
                    participants.assists AS participant_assists,
                    participants.damage_dealt AS participant_damage_dealt,
                    participants.death_type,
                    participants.raw_stats,
                    matches.created_at_kst,
                    matches.duration_seconds,
                    matches.map_name,
                    matches.game_mode,
                    matches.team_mode,
                    matches.perspective,
                    matches.match_type,
                    matches.season_state,
                    matches.is_custom_match,
                    raw_telemetry.match_id IS NOT NULL AS telemetry_available,
                    activity_state.parser_version AS activity_parser_version,
                    activity_state.processed_at_kst AS activity_processed_at_kst,
                    combat.shots_fired,
                    combat.shots_hit,
                    combat.character_hits,
                    combat.vehicle_hits,
                    combat.vehicle_damage_dealt,
                    combat.hits_taken,
                    combat.damage_dealt,
                    combat.damage_taken,
                    combat.kills,
                    combat.assists,
                    combat.deaths,
                    combat.dbnos_caused,
                    combat.dbnos_taken,
                    combat.finishes,
                    combat.finishes_taken,
                    combat.headshot_hits,
                    combat.headshot_hits_taken,
                    combat.headshot_kills,
                    combat.headshot_deaths,
                    combat.headshot_dbnos_caused,
                    combat.headshot_dbnos_taken,
                    activity.heal_events,
                    activity.heal_amount,
                    activity.item_heal_events,
                    activity.item_heal_amount,
                    activity.passive_heal_events,
                    activity.passive_heal_amount,
                    activity.throwable_uses,
                    activity.flare_uses,
                    activity.revives_caused,
                    activity.revives_received,
                    activity.trauma_bag_revives,
                    activity.carry_events,
                    activity.vehicle_rides,
                    activity.vehicle_leaves,
                    activity.vehicle_distance_m,
                    activity.vehicle_max_speed,
                    activity.vehicle_damage,
                    activity.vehicle_destroys,
                    activity.wheel_destroys,
                    activity.vaults,
                    activity.ledge_grabs,
                    activity.vehicle_vaults,
                    activity.swim_sessions,
                    activity.swim_distance_m AS activity_swim_distance_m,
                    activity.armor_destroys_caused,
                    activity.armor_destroys_taken,
                    activity.object_interactions,
                    activity.object_destroys,
                    activity.emergency_pickup_calls,
                    activity.emergency_pickup_rides,
                    activity.redeploys,
                    activity.normalized_event_count,
                    movement.in_game_sampled_distance_m,
                    movement.landing_distance_m
                FROM match_participants participants
                INNER JOIN analysis_matches AS matches ON matches.match_id = participants.match_id
                LEFT JOIN raw_telemetry_payloads raw_telemetry
                    ON raw_telemetry.match_id = participants.match_id
                LEFT JOIN player_telemetry_processing_states activity_state
                    ON activity_state.match_id = participants.match_id
                   AND activity_state.account_id = participants.account_id
                   AND activity_state.processor_name = 'activity'
                LEFT JOIN player_match_combat_summaries combat
                    ON combat.match_id = participants.match_id
                   AND combat.account_id = participants.account_id
                LEFT JOIN player_match_activity_summaries activity
                    ON activity.match_id = participants.match_id
                   AND activity.account_id = participants.account_id
                LEFT JOIN player_movement_summaries movement
                    ON movement.match_id = participants.match_id
                   AND movement.account_id = participants.account_id
                WHERE
                """
                + " AND ".join(conditions)
                + " ORDER BY matches.created_at_kst ASC, participants.match_id ASC",
                params,
            )
            return [dict(row) for row in cursor.fetchall()]

    def _get_fight_rows(
        self,
        player: RegisteredPlayer,
        match_ids: list[str],
    ) -> list[dict[str, Any]]:
        if not match_ids:
            return []
        rows: list[dict[str, Any]] = []
        with self.connection.cursor() as cursor:
            for match_id_chunk in _chunks(match_ids):
                placeholders = ", ".join(["%s"] * len(match_id_chunk))
                cursor.execute(
                    """
                    SELECT
                        match_id,
                        COUNT(*) AS fight_count,
                        COALESCE(SUM(outcome_type = 'win'), 0) AS fight_wins,
                        COALESCE(SUM(outcome_type = 'loss'), 0) AS fight_losses
                    FROM player_fight_outcomes
                    WHERE account_id = %s
                      AND is_friendly_fire = 0
                      AND match_id IN (
                    """
                    + placeholders
                    + ") GROUP BY match_id",
                    [player.account_id, *match_id_chunk],
                )
                rows.extend(dict(row) for row in cursor.fetchall())
        return rows

    def _get_item_rows(
        self,
        player: RegisteredPlayer,
        match_ids: list[str],
    ) -> list[dict[str, Any]]:
        if not match_ids:
            return []
        rows: list[dict[str, Any]] = []
        with self.connection.cursor() as cursor:
            for match_id_chunk in _chunks(match_ids):
                placeholders = ", ".join(["%s"] * len(match_id_chunk))
                cursor.execute(
                    """
                    SELECT
                        item_code,
                        MAX(item_name_ko) AS item_name_ko,
                        MAX(item_category) AS item_category,
                        MAX(item_sub_category) AS item_sub_category,
                        SUM(picked_up_events) AS picked_up_events,
                        SUM(picked_up_quantity) AS picked_up_quantity,
                        SUM(loot_box_pickup_events) AS loot_box_pickup_events,
                        SUM(carepackage_pickup_events) AS carepackage_pickup_events,
                        SUM(custom_package_pickup_events) AS custom_package_pickup_events,
                        SUM(vehicle_trunk_pickup_events) AS vehicle_trunk_pickup_events,
                        SUM(vehicle_trunk_put_events) AS vehicle_trunk_put_events,
                        SUM(dropped_events) AS dropped_events,
                        SUM(dropped_quantity) AS dropped_quantity,
                        SUM(used_events) AS used_events,
                        SUM(used_quantity) AS used_quantity,
                        SUM(equipped_events) AS equipped_events,
                        SUM(attached_events) AS attached_events
                    FROM player_item_match_stats
                    WHERE account_id = %s
                      AND match_id IN (
                    """
                    + placeholders
                    + ") GROUP BY item_code",
                    [player.account_id, *match_id_chunk],
                )
                rows.extend(dict(row) for row in cursor.fetchall())
        return _merge_item_rows(rows)

    def _get_activity_details(
        self,
        player: RegisteredPlayer,
        match_ids: list[str],
    ) -> dict[str, Any]:
        if not match_ids:
            return {"healing_items": [], "throwables": [], "vehicles": [], "objects": []}
        rows: list[dict[str, Any]] = []
        with self.connection.cursor() as cursor:
            for match_id_chunk in _chunks(match_ids):
                placeholders = ", ".join(["%s"] * len(match_id_chunk))
                cursor.execute(
                    """
                    SELECT
                        action,
                        item_code,
                        MAX(item_name_ko) AS item_name_ko,
                        vehicle_type,
                        object_type,
                        COUNT(*) AS event_count,
                        COALESCE(SUM(amount), 0) AS amount,
                        COALESCE(SUM(damage), 0) AS damage,
                        COALESCE(SUM(distance_m), 0) AS distance_m,
                        COALESCE(MAX(max_speed), 0) AS max_speed
                    FROM player_activity_events
                    WHERE account_id = %s
                      AND match_id IN (
                    """
                    + placeholders
                    + """
                      )
                    GROUP BY action, item_code, vehicle_type, object_type
                    """,
                    [player.account_id, *match_id_chunk],
                )
                rows.extend(dict(row) for row in cursor.fetchall())

        rows = _merge_activity_detail_rows(rows)

        def records_for(actions: set[str], limit: int = 20) -> list[dict[str, Any]]:
            return [row for row in rows if str(row.get("action")) in actions][:limit]

        return {
            "healing_items": records_for({"heal_item", "heal_passive"}),
            "throwables": records_for({"throwable_use", "flare_use"}),
            "vehicles": records_for(
                {
                    "vehicle_ride",
                    "vehicle_leave",
                    "vehicle_damage_caused",
                    "vehicle_destroy_caused",
                    "wheel_destroy_caused",
                }
            ),
            "objects": records_for(
                {"object_interaction", "object_destroy", "prop_destroy", "breachable_wall_destroy"}
            ),
        }

    def _coverage(
        self,
        player: RegisteredPlayer,
        filters: PlayerTrendFilters,
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del player, filters
        total_matches = len(rows)
        telemetry_matches = sum(_bool(row.get("telemetry_available")) for row in rows)
        processed_rows = [
            row
            for row in rows
            if row.get("activity_parser_version") == ACTIVITY_PARSER_VERSION
        ]
        processed_matches = len(processed_rows)
        eligible_processed = sum(
            1
            for row in processed_rows
            if _bool(row.get("telemetry_available"))
        )
        coverage_rate = _ratio(eligible_processed, telemetry_matches)
        if telemetry_matches == 0:
            status = "unavailable"
        elif eligible_processed >= telemetry_matches:
            status = "complete"
        elif eligible_processed == 0:
            status = "not_processed"
        else:
            status = "partial"
        missing_matches = [
            str(row["match_id"])
            for row in rows
            if _bool(row.get("telemetry_available"))
            and row.get("activity_parser_version") != ACTIVITY_PARSER_VERSION
        ]
        processed_times = [
            row.get("activity_processed_at_kst")
            for row in processed_rows
            if isinstance(row.get("activity_processed_at_kst"), datetime)
        ]
        return {
            "status": status,
            "parser_version": ACTIVITY_PARSER_VERSION,
            "total_matches": total_matches,
            "telemetry_matches": telemetry_matches,
            "processed_matches": processed_matches,
            "eligible_processed_matches": eligible_processed,
            "coverage_rate": coverage_rate,
            "missing_matches": missing_matches[:20],
            "missing_match_count": len(missing_matches),
            "last_processed_at_kst": max(processed_times).isoformat() if processed_times else None,
            "zero_value_reliable": status == "complete",
        }

    def _event_type_coverage(
        self,
        player: RegisteredPlayer,
        filters: PlayerTrendFilters,
    ) -> dict[str, Any]:
        conditions, params = _match_filter_sql(
            account_id=player.account_id,
            shard=player.shard,
            filters=filters,
            participant_alias="participants",
        )
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    counts.event_type,
                    SUM(counts.event_count) AS event_count,
                    SUM(counts.tracked_event_count) AS tracked_event_count,
                    SUM(counts.normalized_event_count) AS normalized_event_count,
                    MAX(counts.updated_at_kst) AS updated_at_kst
                FROM match_telemetry_event_counts counts
                INNER JOIN analysis_matches AS matches ON matches.match_id = counts.match_id
                INNER JOIN match_participants participants
                    ON participants.match_id = counts.match_id
                WHERE
                """
                + " AND ".join(conditions)
                + " GROUP BY counts.event_type ORDER BY event_count DESC",
                params,
            )
            rows = [dict(row) for row in cursor.fetchall()]

        records: list[dict[str, Any]] = []
        for row in rows:
            definition = get_telemetry_event_definition(str(row["event_type"]))
            records.append(
                {
                    **definition.to_record(),
                    "event_count": _int(row.get("event_count")),
                    "tracked_event_count": _int(row.get("tracked_event_count")),
                    "normalized_event_count": _int(row.get("normalized_event_count")),
                    "updated_at_kst": _iso(row.get("updated_at_kst")),
                }
            )
        raw_only = [row for row in records if row["support"] == "raw_only"]
        return {
            "event_types": records,
            "event_type_count": len(records),
            "raw_only_types": raw_only[:30],
            "raw_only_type_count": len(raw_only),
            "unclassified_type_count": sum(row["domain"] == "unclassified" for row in records),
        }


def _match_filter_sql(
    *,
    account_id: str,
    shard: str,
    filters: PlayerTrendFilters,
    participant_alias: str,
) -> tuple[list[str], list[Any]]:
    conditions = [
        f"{participant_alias}.account_id = %s",
        "matches.shard = %s",
        "matches.created_at_kst IS NOT NULL",
    ]
    params: list[Any] = [account_id, shard]
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
    return conditions, params


def summarize_player_intelligence(
    rows: Iterable[Mapping[str, Any]],
    *,
    fight_rows: Iterable[Mapping[str, Any]] = (),
    item_rows: Iterable[Mapping[str, Any]] = (),
    activity_details: Mapping[str, Any] | None = None,
    trend_limit: int = 365,
) -> dict[str, dict[str, Any]]:
    match_rows = [dict(row) for row in rows]
    fights_by_match = {str(row.get("match_id")): row for row in fight_rows}
    for row in match_rows:
        row["participant_stats"] = _json_mapping(row.get("raw_stats"))
        fight = fights_by_match.get(str(row.get("match_id")), {})
        row["fight_count"] = _int(fight.get("fight_count"))
        row["fight_wins"] = _int(fight.get("fight_wins"))
        row["fight_losses"] = _int(fight.get("fight_losses"))

    match_count = len(match_rows)
    activity_rows = [
        row
        for row in match_rows
        if str(row.get("activity_parser_version") or "") == ACTIVITY_PARSER_VERSION
    ]
    activity_match_count = len(activity_rows)
    wins = sum(_optional_int(row.get("win_place")) == 1 for row in match_rows)
    top10_eligible = [row for row in match_rows if _positive_int(row.get("win_place")) is not None]
    top10 = sum((_positive_int(row.get("win_place")) or 999) <= 10 for row in top10_eligible)
    placements = [_positive_int(row.get("win_place")) for row in match_rows]
    placements = [value for value in placements if value is not None]
    kill_places = [
        _positive_int(_stat(row, "killPlace"))
        for row in match_rows
    ]
    kill_places = [value for value in kill_places if value is not None]
    survival_values = [_float(_stat(row, "timeSurvived")) for row in match_rows]

    combat = _combat_summary(match_rows)
    support = {
        "participant_heal_uses": _sum_stats(match_rows, "heals"),
        "participant_boost_uses": _sum_stats(match_rows, "boosts"),
        "participant_revives": _sum_stats(match_rows, "revives"),
        "activity_covered_matches": activity_match_count,
        "activity_coverage_rate": _ratio(activity_match_count, match_count),
        "heal_events": _sum_rows(activity_rows, "heal_events"),
        "heal_amount": _sum_rows(activity_rows, "heal_amount"),
        "avg_heal_amount": _covered_ratio(
            _sum_rows(activity_rows, "heal_amount"), activity_match_count
        ),
        "item_heal_events": _sum_rows(activity_rows, "item_heal_events"),
        "item_heal_amount": _sum_rows(activity_rows, "item_heal_amount"),
        "avg_item_heal_amount": _covered_ratio(
            _sum_rows(activity_rows, "item_heal_amount"), activity_match_count
        ),
        "passive_heal_events": _sum_rows(activity_rows, "passive_heal_events"),
        "passive_heal_amount": _sum_rows(activity_rows, "passive_heal_amount"),
        "avg_passive_heal_amount": _covered_ratio(
            _sum_rows(activity_rows, "passive_heal_amount"), activity_match_count
        ),
        "throwable_uses": _sum_rows(activity_rows, "throwable_uses"),
        "avg_throwable_uses": _covered_ratio(
            _sum_rows(activity_rows, "throwable_uses"), activity_match_count
        ),
        "flare_uses": _sum_rows(activity_rows, "flare_uses"),
        "revives_caused": _sum_rows(activity_rows, "revives_caused"),
        "avg_revives_caused": _covered_ratio(
            _sum_rows(activity_rows, "revives_caused"), activity_match_count
        ),
        "revives_received": _sum_rows(activity_rows, "revives_received"),
        "trauma_bag_revives": _sum_rows(activity_rows, "trauma_bag_revives"),
        "carry_events": _sum_rows(activity_rows, "carry_events"),
    }
    walk_distance = _sum_stats(match_rows, "walkDistance")
    ride_distance = _sum_stats(match_rows, "rideDistance")
    swim_distance = _sum_stats(match_rows, "swimDistance")
    mobility = {
        "activity_covered_matches": activity_match_count,
        "walk_distance_m": walk_distance,
        "ride_distance_m": ride_distance,
        "swim_distance_m": swim_distance,
        "total_distance_m": walk_distance + ride_distance + swim_distance,
        "avg_walk_distance_m": _ratio(walk_distance, match_count),
        "avg_ride_distance_m": _ratio(ride_distance, match_count),
        "avg_swim_distance_m": _ratio(swim_distance, match_count),
        "sampled_distance_m": _sum_rows(match_rows, "in_game_sampled_distance_m"),
        "avg_landing_distance_m": _average(
            [_optional_float(row.get("landing_distance_m")) for row in match_rows]
        ),
        "vaults": _sum_rows(activity_rows, "vaults"),
        "ledge_grabs": _sum_rows(activity_rows, "ledge_grabs"),
        "vehicle_vaults": _sum_rows(activity_rows, "vehicle_vaults"),
        "swim_sessions": _sum_rows(activity_rows, "swim_sessions"),
        "telemetry_swim_distance_m": _sum_rows(activity_rows, "activity_swim_distance_m"),
        "emergency_pickup_calls": _sum_rows(activity_rows, "emergency_pickup_calls"),
        "emergency_pickup_rides": _sum_rows(activity_rows, "emergency_pickup_rides"),
        "redeploys": _sum_rows(activity_rows, "redeploys"),
    }
    vehicle = {
        "activity_covered_matches": activity_match_count,
        "rides": _sum_rows(activity_rows, "vehicle_rides"),
        "leaves": _sum_rows(activity_rows, "vehicle_leaves"),
        "distance_m": _sum_rows(activity_rows, "vehicle_distance_m"),
        "max_speed": max((_float(row.get("vehicle_max_speed")) for row in activity_rows), default=0.0),
        "damage": _sum_rows(activity_rows, "vehicle_damage"),
        "destroys": _sum_rows(activity_rows, "vehicle_destroys"),
        "participant_destroys": _sum_stats(match_rows, "vehicleDestroys"),
        "wheel_destroys": _sum_rows(activity_rows, "wheel_destroys"),
        "road_kills": _sum_stats(match_rows, "roadKills"),
    }
    environment = {
        "activity_covered_matches": activity_match_count,
        "armor_destroys_caused": _sum_rows(activity_rows, "armor_destroys_caused"),
        "armor_destroys_taken": _sum_rows(activity_rows, "armor_destroys_taken"),
        "object_interactions": _sum_rows(activity_rows, "object_interactions"),
        "object_destroys": _sum_rows(activity_rows, "object_destroys"),
    }
    survival = {
        "matches": match_count,
        "wins": wins,
        "win_rate": _ratio(wins, match_count),
        "top10": top10,
        "top10_eligible_matches": len(top10_eligible),
        "top10_rate": _ratio(top10, len(top10_eligible)),
        "avg_placement": _average(placements),
        "avg_kill_place": _average(kill_places),
        "avg_survival_seconds": _average(survival_values),
        "max_survival_seconds": max(survival_values, default=0.0),
        "longest_kill_m": max((_float(_stat(row, "longestKill")) for row in match_rows), default=0.0),
        "max_kill_streak": max((_int(_stat(row, "killStreaks")) for row in match_rows), default=0),
        "team_kills": _sum_stats(match_rows, "teamKills"),
        "suicides": _sum_stats(match_rows, "suicides"),
    }

    normalized_item_rows = [dict(row) for row in item_rows]
    loot = _loot_summary(normalized_item_rows, match_count)
    overview = {
        "matches": match_count,
        "wins": wins,
        "win_rate": _ratio(wins, match_count),
        "kda": combat["kda"],
        "avg_damage_dealt": combat["avg_damage_dealt"],
        "avg_survival_seconds": survival["avg_survival_seconds"],
        "avg_total_distance_m": _ratio(walk_distance + ride_distance + swim_distance, match_count),
        "avg_fights_per_match": combat["avg_fights_per_match"],
        "fight_win_rate": combat["fight_win_rate"],
    }
    bounded_trend_limit = max(1, min(int(trend_limit), 1000))
    trends = {
        "daily": _trend_rows(match_rows, "date")[-bounded_trend_limit:],
        "monthly": _trend_rows(match_rows, "month")[-bounded_trend_limit:],
    }
    breakdowns = {
        "maps": _dimension_rows(match_rows, "map_name"),
        "team_modes": _dimension_rows(match_rows, "team_mode"),
        "game_modes": _dimension_rows(match_rows, "game_mode"),
        "perspectives": _dimension_rows(match_rows, "perspective"),
        "match_types": _dimension_rows(match_rows, "match_type"),
        "hours": _time_dimension_rows(match_rows, "hour"),
        "weekdays": _time_dimension_rows(match_rows, "weekday"),
    }
    return {
        "overview": overview,
        "combat": combat,
        "survival": survival,
        "support": support,
        "loot": loot,
        "mobility": mobility,
        "vehicle": vehicle,
        "environment": environment,
        "breakdowns": breakdowns,
        "trends": trends,
        "activity_details": dict(activity_details or {}),
    }


def _combat_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    match_count = len(rows)
    kills = _sum_rows(rows, "kills")
    assists = _sum_rows(rows, "assists")
    deaths = _sum_rows(rows, "deaths")
    shots_fired = _sum_rows(rows, "shots_fired")
    shots_hit = _sum_rows(rows, "shots_hit")
    character_hits = sum(
        _int(row.get("shots_hit"))
        if row.get("character_hits") is None
        else _int(row.get("character_hits"))
        for row in rows
    )
    vehicle_hits = _sum_rows(rows, "vehicle_hits")
    headshot_hits = _sum_rows(rows, "headshot_hits")
    fights = _sum_rows(rows, "fight_count")
    fight_wins = _sum_rows(rows, "fight_wins")
    fight_losses = _sum_rows(rows, "fight_losses")
    return {
        "match_count": match_count,
        "kills": kills,
        "assists": assists,
        "deaths": deaths,
        "kda": _ratio(kills + assists, deaths if deaths else 1),
        "avg_kills": _ratio(kills, match_count),
        "avg_assists": _ratio(assists, match_count),
        "avg_deaths": _ratio(deaths, match_count),
        "dbnos_caused": _sum_rows(rows, "dbnos_caused"),
        "dbnos_taken": _sum_rows(rows, "dbnos_taken"),
        "finishes": _sum_rows(rows, "finishes"),
        "finishes_taken": _sum_rows(rows, "finishes_taken"),
        "damage_dealt": _sum_rows(rows, "damage_dealt"),
        "damage_taken": _sum_rows(rows, "damage_taken"),
        "avg_damage_dealt": _ratio(_sum_rows(rows, "damage_dealt"), match_count),
        "avg_damage_taken": _ratio(_sum_rows(rows, "damage_taken"), match_count),
        "shots_fired": shots_fired,
        "shots_hit": shots_hit,
        "character_hits": character_hits,
        "vehicle_hits": vehicle_hits,
        "vehicle_damage_dealt": _sum_rows(rows, "vehicle_damage_dealt"),
        "hits_taken": _sum_rows(rows, "hits_taken"),
        "accuracy": _ratio(shots_hit, shots_fired),
        "headshot_hits": headshot_hits,
        "headshot_hits_taken": _sum_rows(rows, "headshot_hits_taken"),
        "headshot_hit_rate": _ratio(headshot_hits, character_hits),
        "headshot_hit_taken_rate": _ratio(
            _sum_rows(rows, "headshot_hits_taken"),
            _sum_rows(rows, "hits_taken"),
        ),
        "headshot_kills": _sum_rows(rows, "headshot_kills"),
        "headshot_deaths": _sum_rows(rows, "headshot_deaths"),
        "headshot_dbnos_caused": _sum_rows(rows, "headshot_dbnos_caused"),
        "headshot_dbnos_taken": _sum_rows(rows, "headshot_dbnos_taken"),
        "fight_count": fights,
        "fight_wins": fight_wins,
        "fight_losses": fight_losses,
        "fight_win_rate": _ratio(fight_wins, fight_wins + fight_losses),
        "avg_fights_per_match": _ratio(fights, match_count),
    }


def _loot_summary(rows: list[dict[str, Any]], match_count: int) -> dict[str, Any]:
    total_fields = (
        "picked_up_events",
        "picked_up_quantity",
        "loot_box_pickup_events",
        "carepackage_pickup_events",
        "custom_package_pickup_events",
        "vehicle_trunk_pickup_events",
        "vehicle_trunk_put_events",
        "dropped_events",
        "dropped_quantity",
        "used_events",
        "used_quantity",
        "equipped_events",
        "attached_events",
    )
    totals = {field: sum(_float(row.get(field)) for row in rows) for field in total_fields}
    totals["avg_pickups_per_match"] = _ratio(totals["picked_up_events"], match_count)
    totals["avg_uses_per_match"] = _ratio(totals["used_events"], match_count)
    totals["top_picked_items"] = sorted(
        rows,
        key=lambda row: (_float(row.get("picked_up_events")), _float(row.get("picked_up_quantity"))),
        reverse=True,
    )[:15]
    totals["top_used_items"] = sorted(
        [row for row in rows if _float(row.get("used_events")) > 0],
        key=lambda row: _float(row.get("used_events")),
        reverse=True,
    )[:15]
    return totals


def _trend_rows(rows: list[dict[str, Any]], granularity: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    labels: dict[str, str] = {}
    for row in rows:
        created = row.get("created_at_kst")
        if not isinstance(created, datetime):
            continue
        if granularity == "date":
            key = created.strftime("%Y-%m-%d")
            label = created.strftime("%Y-%m-%d")
        else:
            key = created.strftime("%Y-%m")
            label = f"{created.year:04d}년 {created.month:02d}월"
        grouped[key].append(row)
        labels[key] = label
    return [
        _bucket_summary(key, labels[key], grouped[key])
        for key in sorted(grouped)
    ]


def _dimension_rows(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        value = str(row.get(field) or "unknown")
        grouped[value].append(row)
    result = [
        _bucket_summary(value, _dimension_label(field, value), bucket)
        for value, bucket in grouped.items()
    ]
    return sorted(result, key=lambda row: (-_int(row["matches"]), str(row["key"])))


def _dimension_label(field: str, value: str) -> str:
    if value == "unknown":
        return "알 수 없음"
    category_by_field = {
        "map_name": "map",
        "team_mode": "team_mode",
        "game_mode": "game_mode",
        "perspective": "perspective",
        "match_type": "match_type",
        "season_state": "season_state",
    }
    category = category_by_field.get(field)
    return translate_code(value, category) if category else value


def _time_dimension_rows(rows: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    labels: dict[str, str] = {}
    weekday_labels = ("월", "화", "수", "목", "금", "토", "일")
    for row in rows:
        created = row.get("created_at_kst")
        if not isinstance(created, datetime):
            continue
        if kind == "hour":
            key = f"{created.hour:02d}"
            label = f"{created.hour:02d}:00-{created.hour:02d}:59"
        else:
            key = str(created.weekday())
            label = weekday_labels[created.weekday()]
        grouped[key].append(row)
        labels[key] = label
    return [_bucket_summary(key, labels[key], grouped[key]) for key in sorted(grouped)]


def _bucket_summary(key: str, label: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    combat = _combat_summary(rows)
    matches = len(rows)
    activity_rows = [
        row
        for row in rows
        if str(row.get("activity_parser_version") or "") == ACTIVITY_PARSER_VERSION
    ]
    activity_matches = len(activity_rows)
    wins = sum(_optional_int(row.get("win_place")) == 1 for row in rows)
    survival = [_float(_stat(row, "timeSurvived")) for row in rows]
    walk = _sum_stats(rows, "walkDistance")
    ride = _sum_stats(rows, "rideDistance")
    swim = _sum_stats(rows, "swimDistance")
    return {
        "key": key,
        "label": label,
        "matches": matches,
        "wins": wins,
        "win_rate": _ratio(wins, matches),
        "kills": combat["kills"],
        "assists": combat["assists"],
        "deaths": combat["deaths"],
        "kda": combat["kda"],
        "avg_damage_dealt": combat["avg_damage_dealt"],
        "avg_damage_taken": combat["avg_damage_taken"],
        "accuracy": combat["accuracy"],
        "headshot_hit_rate": combat["headshot_hit_rate"],
        "dbnos_caused": combat["dbnos_caused"],
        "dbnos_taken": combat["dbnos_taken"],
        "fight_count": combat["fight_count"],
        "fight_win_rate": combat["fight_win_rate"],
        "avg_fights_per_match": combat["avg_fights_per_match"],
        "avg_survival_seconds": _average(survival),
        "activity_covered_matches": activity_matches,
        "activity_coverage_rate": _ratio(activity_matches, matches),
        "heal_amount": _sum_rows(activity_rows, "heal_amount"),
        "avg_heal_amount": _covered_ratio(
            _sum_rows(activity_rows, "heal_amount"), activity_matches
        ),
        "revives_caused": _sum_rows(activity_rows, "revives_caused"),
        "avg_revives_caused": _covered_ratio(
            _sum_rows(activity_rows, "revives_caused"), activity_matches
        ),
        "throwable_uses": _sum_rows(activity_rows, "throwable_uses"),
        "avg_throwable_uses": _covered_ratio(
            _sum_rows(activity_rows, "throwable_uses"), activity_matches
        ),
        "avg_walk_distance_m": _ratio(walk, matches),
        "avg_ride_distance_m": _ratio(ride, matches),
        "avg_swim_distance_m": _ratio(swim, matches),
    }


def _chunks(values: list[str]) -> Iterable[list[str]]:
    for start in range(0, len(values), MATCH_ID_QUERY_CHUNK_SIZE):
        yield values[start : start + MATCH_ID_QUERY_CHUNK_SIZE]


def _merge_item_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    text_fields = ("item_name_ko", "item_category", "item_sub_category")
    sum_fields = (
        "picked_up_events",
        "picked_up_quantity",
        "loot_box_pickup_events",
        "carepackage_pickup_events",
        "custom_package_pickup_events",
        "vehicle_trunk_pickup_events",
        "vehicle_trunk_put_events",
        "dropped_events",
        "dropped_quantity",
        "used_events",
        "used_quantity",
        "equipped_events",
        "attached_events",
    )
    merged: dict[str, dict[str, Any]] = {}
    for source in rows:
        item_code = str(source.get("item_code") or "")
        target = merged.setdefault(item_code, {"item_code": item_code})
        for field in text_fields:
            candidate = source.get(field)
            if candidate is not None and (
                target.get(field) is None or str(candidate) > str(target[field])
            ):
                target[field] = candidate
        for field in sum_fields:
            target[field] = _normalized_sum((target.get(field), source.get(field)))
    values = list(merged.values())
    for row in values:
        row["item_name_ko"] = translate_code(row["item_code"], "item")
        if row.get("item_category"):
            row["item_category_ko"] = translate_code(
                str(row["item_category"]),
                "item_category",
            )
        if row.get("item_sub_category"):
            row["item_sub_category_ko"] = translate_code(
                str(row["item_sub_category"]),
                "item_sub_category",
            )
    return sorted(
        values,
        key=lambda row: _float(row.get("picked_up_events")) + _float(row.get("used_events")),
        reverse=True,
    )


def _merge_activity_detail_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    key_fields = ("action", "item_code", "vehicle_type", "object_type")
    sum_fields = ("event_count", "amount", "damage", "distance_m")
    merged: dict[tuple[Any, ...], dict[str, Any]] = {}
    for source in rows:
        key = tuple(source.get(field) for field in key_fields)
        target = merged.setdefault(key, {field: source.get(field) for field in key_fields})
        item_name = source.get("item_name_ko")
        if item_name is not None and (
            target.get("item_name_ko") is None or str(item_name) > str(target["item_name_ko"])
        ):
            target["item_name_ko"] = item_name
        for field in sum_fields:
            target[field] = _normalized_sum((target.get(field), source.get(field)))
        target["max_speed"] = max(
            _float(target.get("max_speed")),
            _float(source.get("max_speed")),
        )
    values = list(merged.values())
    for row in values:
        if row.get("action"):
            row["action_ko"] = translate_code(str(row["action"]), "activity_action")
        if row.get("item_code"):
            row["item_name_ko"] = translate_code(str(row["item_code"]), "item")
        if row.get("vehicle_type"):
            row["vehicle_type_ko"] = translate_code(
                str(row["vehicle_type"]),
                "vehicle_type",
            )
    return sorted(
        values,
        key=lambda row: _float(row.get("event_count")),
        reverse=True,
    )


def _json_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return {}
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(loaded) if isinstance(loaded, Mapping) else {}
    return {}


def _stat(row: Mapping[str, Any], key: str) -> Any:
    stats = row.get("participant_stats")
    if not isinstance(stats, Mapping):
        stats = _json_mapping(row.get("raw_stats"))
    if key in stats:
        return stats[key]
    normalized_key = key.lower()
    for candidate, value in stats.items():
        if str(candidate).lower() == normalized_key:
            return value
    return None


def _sum_stats(rows: Iterable[Mapping[str, Any]], key: str) -> int | float:
    return _normalized_sum([_stat(row, key) for row in rows])


def _sum_rows(rows: Iterable[Mapping[str, Any]], key: str) -> int | float:
    return _normalized_sum([row.get(key) for row in rows])


def _normalized_sum(values: Iterable[Any]) -> int | float:
    parsed = [_float(value) for value in values]
    total = sum(parsed)
    if all(value.is_integer() for value in parsed):
        return int(total)
    return total


def _average(values: Iterable[int | float | None]) -> float:
    parsed = [float(value) for value in values if value is not None]
    return sum(parsed) / len(parsed) if parsed else 0.0


def _ratio(numerator: int | float, denominator: int | float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _covered_ratio(numerator: int | float, denominator: int) -> float | None:
    return float(numerator) / float(denominator) if denominator > 0 else None


def _int(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _positive_int(value: Any) -> int | None:
    parsed = _optional_int(value)
    return parsed if parsed is not None and parsed > 0 else None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None
