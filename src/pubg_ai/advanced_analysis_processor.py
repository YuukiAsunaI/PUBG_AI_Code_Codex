from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
import gzip
import json

from pubg_ai.advanced_analysis import (
    PARSER_VERSION,
    AdvancedAnalysisBundle,
    FightEpisode,
    LootReadinessSummary,
    ParticipantContext,
    TeamCoordinationSummary,
    ZonePhaseSummary,
    build_advanced_analysis,
)
from pubg_ai.database import mysql_transaction
from pubg_ai.raw_storage import RawPayloadStore
from pubg_ai.telemetry_processing_state import (
    list_pending_telemetry_payloads,
    pending_tracked_account_ids,
    upsert_processing_states,
)
from pubg_ai.time_utils import now_kst


PROCESSOR_NAME = "advanced_analysis"


class AdvancedAnalysisProcessingError(RuntimeError):
    """Raised when advanced match analysis cannot be generated."""


@dataclass(frozen=True)
class AdvancedAnalysisProcessingResult:
    candidate_payloads: int
    parsed_payloads: int
    skipped_no_tracked_player: int
    failed_payloads: int
    events_read: int
    tracked_players: int
    fight_episodes: int
    zone_phases: int
    team_summaries: int
    loot_summaries: int
    failure_details: list[dict[str, str]] = field(default_factory=list)

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


class AdvancedAnalysisProcessor:
    def __init__(self, connection: Any, raw_store: RawPayloadStore) -> None:
        self.connection = connection
        self.raw_store = raw_store

    def process_raw_telemetry(
        self,
        *,
        limit: int = 10,
        force: bool = False,
    ) -> AdvancedAnalysisProcessingResult:
        limit = max(1, min(int(limit), 200))
        payloads = list_pending_telemetry_payloads(
            self.connection,
            processor_name=PROCESSOR_NAME,
            parser_version=PARSER_VERSION,
            limit=limit,
            force=force,
        )
        parsed_payloads = 0
        skipped_no_tracked_player = 0
        failed_payloads = 0
        events_read = 0
        tracked_players = 0
        fight_episode_count = 0
        zone_phase_count = 0
        team_summary_count = 0
        loot_summary_count = 0
        failure_details: list[dict[str, str]] = []

        for payload in payloads:
            match_id = str(payload["match_id"])
            shard = str(payload["shard"])
            account_ids = pending_tracked_account_ids(
                self.connection,
                match_id=match_id,
                shard=shard,
                processor_name=PROCESSOR_NAME,
                parser_version=PARSER_VERSION,
                force=force,
            )
            if not account_ids:
                skipped_no_tracked_player += 1
                continue
            try:
                events = self._load_telemetry_events(payload)
                participants = self._participant_contexts(match_id)
                bundle = build_advanced_analysis(
                    events,
                    match_id=match_id,
                    tracked_account_ids=account_ids,
                    participants=participants,
                )
                self._replace_rows(
                    match_id=match_id,
                    account_ids=account_ids,
                    bundle=bundle,
                )
            except Exception as exc:
                failed_payloads += 1
                failure_details.append(
                    {
                        "match_id": match_id,
                        "error_type": exc.__class__.__name__,
                        "message": str(exc)[:500],
                    }
                )
                continue

            parsed_payloads += 1
            events_read += len(events)
            tracked_players += len(account_ids)
            fight_episode_count += len(bundle.fight_episodes)
            zone_phase_count += len(bundle.zone_phases)
            team_summary_count += len(bundle.team_coordination)
            loot_summary_count += len(bundle.loot_readiness)

        return AdvancedAnalysisProcessingResult(
            candidate_payloads=len(payloads),
            parsed_payloads=parsed_payloads,
            skipped_no_tracked_player=skipped_no_tracked_player,
            failed_payloads=failed_payloads,
            events_read=events_read,
            tracked_players=tracked_players,
            fight_episodes=fight_episode_count,
            zone_phases=zone_phase_count,
            team_summaries=team_summary_count,
            loot_summaries=loot_summary_count,
            failure_details=failure_details,
        )

    def _participant_contexts(self, match_id: str) -> dict[str, ParticipantContext]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT account_id, team_id, is_ai_or_bot
                FROM match_participants
                WHERE match_id = %s
                  AND account_id IS NOT NULL
                  AND account_id <> ''
                """,
                (match_id,),
            )
            rows = cursor.fetchall()
        return {
            str(row["account_id"]): ParticipantContext(
                account_id=str(row["account_id"]),
                team_id=_optional_int(row.get("team_id")),
                is_bot=bool(row.get("is_ai_or_bot")),
            )
            for row in rows
        }

    def _load_telemetry_events(
        self,
        payload: Mapping[str, Any],
    ) -> list[Mapping[str, Any]]:
        relative_path = str(payload.get("relative_path") or "").strip()
        compression = str(payload.get("compression") or "").strip()
        if not relative_path:
            raise AdvancedAnalysisProcessingError("relative_path is required.")
        path = self.raw_store.resolve_path(relative_path)
        try:
            if compression == "gzip" or path.suffix == ".gz":
                with gzip.open(path, "rt", encoding="utf-8") as file:
                    loaded = json.load(file)
            else:
                with Path(path).open("r", encoding="utf-8") as file:
                    loaded = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            raise AdvancedAnalysisProcessingError(
                f"failed to read telemetry payload: {relative_path}"
            ) from exc
        if not isinstance(loaded, list):
            raise AdvancedAnalysisProcessingError("telemetry payload root must be a list.")
        return [event for event in loaded if isinstance(event, Mapping)]

    def _replace_rows(
        self,
        *,
        match_id: str,
        account_ids: set[str],
        bundle: AdvancedAnalysisBundle,
    ) -> None:
        ordered_accounts = sorted(account_ids)
        placeholders = ", ".join(["%s"] * len(ordered_accounts))
        params = (match_id, *ordered_accounts)
        with mysql_transaction(self.connection):
            with self.connection.cursor() as cursor:
                for table_name in (
                    "player_fight_episodes",
                    "player_zone_phase_summaries",
                    "player_team_coordination_summaries",
                    "player_loot_readiness_summaries",
                ):
                    cursor.execute(
                        f"""
                        DELETE FROM {table_name}
                        WHERE match_id = %s AND account_id IN ({placeholders})
                        """,
                        params,
                    )
            self._insert_fight_episodes(bundle.fight_episodes)
            self._insert_zone_phases(bundle.zone_phases)
            self._insert_team_summaries(bundle.team_coordination)
            self._insert_loot_summaries(bundle.loot_readiness)
            upsert_processing_states(
                self.connection,
                match_id=match_id,
                account_ids=account_ids,
                processor_name=PROCESSOR_NAME,
                parser_version=PARSER_VERSION,
                output_counts={
                    account_id: bundle.output_count(account_id)
                    for account_id in account_ids
                },
            )

    def _insert_fight_episodes(self, rows: tuple[FightEpisode, ...]) -> None:
        if not rows:
            return
        timestamp = _mysql_datetime(now_kst())
        values = [
            (
                row.match_id,
                row.account_id,
                row.episode_index,
                row.start_event_index,
                row.end_event_index,
                _mysql_datetime(row.started_at_kst),
                _mysql_datetime(row.ended_at_kst),
                row.duration_seconds,
                row.phase_number,
                row.outcome,
                row.opening_actor,
                row.first_hit_actor,
                row.primary_opponent_account_id,
                row.primary_opponent_team_id,
                row.opponent_count,
                row.opponent_team_count,
                row.shots_fired,
                row.shots_hit,
                row.damage_dealt,
                row.damage_taken,
                row.dbnos_caused,
                row.dbnos_taken,
                row.kills,
                row.deaths,
                row.assists,
                row.revives_given,
                row.revives_received,
                row.trade_opportunities,
                row.trade_successes,
                row.is_third_party,
                json.dumps(row.weapon_codes, ensure_ascii=False),
                json.dumps(row.opponent_weapon_codes, ensure_ascii=False),
                row.min_distance_m,
                row.avg_distance_m,
                row.max_distance_m,
                row.summary_reason,
                PARSER_VERSION,
                timestamp,
            )
            for row in rows
        ]
        with self.connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO player_fight_episodes (
                    match_id, account_id, episode_index, start_event_index, end_event_index,
                    started_at_kst, ended_at_kst, duration_seconds, phase_number, outcome,
                    opening_actor, first_hit_actor, primary_opponent_account_id,
                    primary_opponent_team_id, opponent_count, opponent_team_count,
                    shots_fired, shots_hit, damage_dealt, damage_taken, dbnos_caused,
                    dbnos_taken, kills, deaths, assists, revives_given, revives_received,
                    trade_opportunities, trade_successes, is_third_party, weapon_codes,
                    opponent_weapon_codes, min_distance_m, avg_distance_m, max_distance_m,
                    summary_reason, parser_version, updated_at_kst
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                values,
            )

    def _insert_zone_phases(self, rows: tuple[ZonePhaseSummary, ...]) -> None:
        if not rows:
            return
        timestamp = _mysql_datetime(now_kst())
        values = [
            (
                row.match_id,
                row.account_id,
                row.phase_number,
                row.sample_count,
                row.phase_started_elapsed_seconds,
                row.phase_ended_elapsed_seconds,
                row.first_inside_elapsed_seconds,
                row.late_entry_seconds,
                row.outside_safe_zone_seconds,
                row.blue_zone_exposure_seconds,
                row.max_outside_distance_m,
                row.avg_center_distance_ratio,
                row.edge_position_seconds,
                row.center_position_seconds,
                row.rotation_distance_m,
                row.foot_distance_m,
                row.vehicle_distance_m,
                row.vehicle_seconds,
                row.dbnos_taken,
                row.deaths,
                PARSER_VERSION,
                timestamp,
            )
            for row in rows
        ]
        with self.connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO player_zone_phase_summaries (
                    match_id, account_id, phase_number, sample_count,
                    phase_started_elapsed_seconds, phase_ended_elapsed_seconds,
                    first_inside_elapsed_seconds, late_entry_seconds,
                    outside_safe_zone_seconds, blue_zone_exposure_seconds,
                    max_outside_distance_m, avg_center_distance_ratio,
                    edge_position_seconds, center_position_seconds, rotation_distance_m,
                    foot_distance_m, vehicle_distance_m, vehicle_seconds,
                    dbnos_taken, deaths, parser_version, updated_at_kst
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                values,
            )

    def _insert_team_summaries(
        self,
        rows: tuple[TeamCoordinationSummary, ...],
    ) -> None:
        if not rows:
            return
        timestamp = _mysql_datetime(now_kst())
        values = [
            (
                row.match_id,
                row.account_id,
                row.sample_count,
                row.avg_nearest_teammate_distance_m,
                row.max_nearest_teammate_distance_m,
                row.avg_visible_teammates,
                row.isolated_seconds,
                row.close_support_seconds,
                row.regroup_count,
                row.trade_opportunities,
                row.trade_successes,
                row.revives_given,
                row.revives_received,
                row.avg_revive_latency_seconds,
                row.team_dbnos_taken,
                row.team_deaths,
                PARSER_VERSION,
                timestamp,
            )
            for row in rows
        ]
        with self.connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO player_team_coordination_summaries (
                    match_id, account_id, sample_count,
                    avg_nearest_teammate_distance_m, max_nearest_teammate_distance_m,
                    avg_visible_teammates, isolated_seconds, close_support_seconds,
                    regroup_count, trade_opportunities, trade_successes,
                    revives_given, revives_received, avg_revive_latency_seconds,
                    team_dbnos_taken, team_deaths, parser_version, updated_at_kst
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                values,
            )

    def _insert_loot_summaries(
        self,
        rows: tuple[LootReadinessSummary, ...],
    ) -> None:
        if not rows:
            return
        timestamp = _mysql_datetime(now_kst())
        values = [
            (
                row.match_id,
                row.account_id,
                _mysql_datetime(row.landed_at_kst),
                _mysql_datetime(row.first_fight_at_kst),
                row.first_primary_weapon_code,
                row.second_primary_weapon_code,
                row.seconds_to_first_primary_weapon,
                row.seconds_to_second_primary_weapon,
                row.seconds_to_vest,
                row.seconds_to_helmet,
                row.seconds_to_heal,
                row.seconds_to_throwable,
                row.seconds_to_scope,
                row.seconds_to_first_fight,
                row.ready_before_first_fight,
                row.pickup_events,
                row.ground_pickups,
                row.loot_box_pickups,
                row.care_package_pickups,
                row.vehicle_trunk_pickups,
                row.readiness_score,
                json.dumps(row.early_inventory, ensure_ascii=False),
                PARSER_VERSION,
                timestamp,
            )
            for row in rows
        ]
        with self.connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO player_loot_readiness_summaries (
                    match_id, account_id, landed_at_kst, first_fight_at_kst,
                    first_primary_weapon_code, second_primary_weapon_code,
                    seconds_to_first_primary_weapon, seconds_to_second_primary_weapon,
                    seconds_to_vest, seconds_to_helmet, seconds_to_heal,
                    seconds_to_throwable, seconds_to_scope, seconds_to_first_fight,
                    ready_before_first_fight, pickup_events, ground_pickups,
                    loot_box_pickups, care_package_pickups, vehicle_trunk_pickups,
                    readiness_score, early_inventory, parser_version, updated_at_kst
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                values,
            )


def _mysql_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=None)


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
