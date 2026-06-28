from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
import gzip
import json

from pubg_ai.raw_storage import RawPayloadStore
from pubg_ai.time_utils import now_kst
from pubg_ai.weapon_stats import (
    PlayerMatchCombatSummary,
    WeaponCombatStats,
    summarize_player_match_combat,
    summarize_weapon_combat_stats,
)


class TelemetryCombatProcessingError(RuntimeError):
    """Raised when raw telemetry cannot be parsed into combat summaries."""


@dataclass(frozen=True)
class TelemetryCombatProcessingResult:
    candidate_payloads: int
    parsed_payloads: int
    skipped_existing: int
    skipped_no_tracked_player: int
    failed_payloads: int
    events_read: int
    combat_summaries: int
    weapon_stats: int

    def to_record(self) -> dict[str, int]:
        return asdict(self)


class TelemetryCombatProcessor:
    def __init__(self, connection: Any, raw_store: RawPayloadStore) -> None:
        self.connection = connection
        self.raw_store = raw_store

    def process_raw_telemetry(
        self,
        *,
        limit: int = 10,
        force: bool = False,
    ) -> TelemetryCombatProcessingResult:
        limit = max(1, min(limit, 200))
        payloads = self._list_raw_telemetry_payloads(limit=limit, force=force)

        parsed_payloads = 0
        skipped_existing = 0
        skipped_no_tracked_player = 0
        failed_payloads = 0
        events_read = 0
        combat_summary_count = 0
        weapon_stat_count = 0

        for payload in payloads:
            match_id = str(payload["match_id"])
            shard = str(payload["shard"])

            if not force and self._combat_summary_exists(match_id):
                skipped_existing += 1
                continue

            tracked_account_ids = self._tracked_account_ids_for_match(match_id=match_id, shard=shard)
            if not tracked_account_ids:
                skipped_no_tracked_player += 1
                continue

            try:
                events = self._load_telemetry_events(payload)
                summaries = summarize_player_match_combat(
                    events,
                    match_id=match_id,
                    tracked_account_ids=tracked_account_ids,
                )
                summaries = _ensure_summaries_for_tracked_accounts(
                    match_id=match_id,
                    tracked_account_ids=tracked_account_ids,
                    summaries=summaries,
                )
                weapon_stats = summarize_weapon_combat_stats(
                    events,
                    match_id=match_id,
                    tracked_account_ids=tracked_account_ids,
                )
                self._replace_combat_rows(
                    match_id=match_id,
                    tracked_account_ids=tracked_account_ids,
                    summaries=summaries,
                    weapon_stats=weapon_stats,
                )
            except Exception:
                failed_payloads += 1
                continue

            parsed_payloads += 1
            events_read += len(events)
            combat_summary_count += len(summaries)
            weapon_stat_count += len(weapon_stats)

        return TelemetryCombatProcessingResult(
            candidate_payloads=len(payloads),
            parsed_payloads=parsed_payloads,
            skipped_existing=skipped_existing,
            skipped_no_tracked_player=skipped_no_tracked_player,
            failed_payloads=failed_payloads,
            events_read=events_read,
            combat_summaries=combat_summary_count,
            weapon_stats=weapon_stat_count,
        )

    def _list_raw_telemetry_payloads(self, *, limit: int, force: bool) -> list[dict[str, Any]]:
        where = ""
        if not force:
            where = """
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM player_match_combat_summaries summaries
                    WHERE summaries.match_id = raw_telemetry_payloads.match_id
                )
            """

        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT id, match_id, shard, relative_path, compression
                FROM raw_telemetry_payloads
                {where}
                ORDER BY id ASC
                LIMIT %s
                """,
                (limit,),
            )
            return list(cursor.fetchall())

    def _combat_summary_exists(self, match_id: str) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM player_match_combat_summaries WHERE match_id = %s LIMIT 1",
                (match_id,),
            )
            return cursor.fetchone() is not None

    def _tracked_account_ids_for_match(self, *, match_id: str, shard: str) -> set[str]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT registered_players.account_id
                FROM registered_players
                INNER JOIN match_participants
                    ON match_participants.account_id = registered_players.account_id
                   AND match_participants.match_id = %s
                WHERE registered_players.shard = %s
                """,
                (match_id, shard),
            )
            return {str(row["account_id"]) for row in cursor.fetchall()}

    def _load_telemetry_events(self, payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        relative_path = _required_text(payload.get("relative_path"), "relative_path")
        compression = _required_text(payload.get("compression"), "compression")
        path = self.raw_store.resolve_path(relative_path)

        try:
            if compression == "gzip" or path.suffix == ".gz":
                with gzip.open(path, "rt", encoding="utf-8") as file:
                    loaded = json.load(file)
            else:
                with Path(path).open("r", encoding="utf-8") as file:
                    loaded = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            raise TelemetryCombatProcessingError(f"failed to read telemetry payload: {relative_path}") from exc

        if not isinstance(loaded, list):
            raise TelemetryCombatProcessingError("telemetry payload root must be a list.")

        return [event for event in loaded if isinstance(event, Mapping)]

    def _replace_combat_rows(
        self,
        *,
        match_id: str,
        tracked_account_ids: set[str],
        summaries: list[PlayerMatchCombatSummary],
        weapon_stats: list[WeaponCombatStats],
    ) -> None:
        self._delete_existing_rows(match_id=match_id, account_ids=tracked_account_ids)
        self._insert_combat_summaries(summaries)
        self._insert_weapon_stats(weapon_stats)

    def _delete_existing_rows(self, *, match_id: str, account_ids: set[str]) -> None:
        if not account_ids:
            return

        placeholders = ", ".join(["%s"] * len(account_ids))
        params = [match_id, *sorted(account_ids)]
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""
                DELETE FROM player_match_combat_summaries
                WHERE match_id = %s AND account_id IN ({placeholders})
                """,
                params,
            )
            cursor.execute(
                f"""
                DELETE FROM player_weapon_match_stats
                WHERE match_id = %s AND account_id IN ({placeholders})
                """,
                params,
            )

    def _insert_combat_summaries(self, summaries: list[PlayerMatchCombatSummary]) -> None:
        if not summaries:
            return

        timestamp = _mysql_kst_now()
        rows = [
            (
                summary.match_id,
                summary.account_id,
                summary.shots_fired,
                summary.shots_hit,
                summary.hits_taken,
                summary.damage_dealt,
                summary.damage_taken,
                summary.kills,
                summary.assists,
                summary.deaths,
                summary.dbnos_caused,
                summary.dbnos_taken,
                summary.finishes,
                summary.finishes_taken,
                summary.headshot_hits,
                summary.headshot_hits_taken,
                summary.headshot_kills,
                summary.headshot_deaths,
                summary.headshot_dbnos_caused,
                summary.headshot_dbnos_taken,
                json.dumps(summary.hit_parts, ensure_ascii=False, separators=(",", ":")),
                json.dumps(summary.taken_hit_parts, ensure_ascii=False, separators=(",", ":")),
                timestamp,
            )
            for summary in summaries
        ]

        with self.connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO player_match_combat_summaries (
                    match_id,
                    account_id,
                    shots_fired,
                    shots_hit,
                    hits_taken,
                    damage_dealt,
                    damage_taken,
                    kills,
                    assists,
                    deaths,
                    dbnos_caused,
                    dbnos_taken,
                    finishes,
                    finishes_taken,
                    headshot_hits,
                    headshot_hits_taken,
                    headshot_kills,
                    headshot_deaths,
                    headshot_dbnos_caused,
                    headshot_dbnos_taken,
                    hit_parts,
                    taken_hit_parts,
                    updated_at_kst
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                rows,
            )

    def _insert_weapon_stats(self, weapon_stats: list[WeaponCombatStats]) -> None:
        if not weapon_stats:
            return

        timestamp = _mysql_kst_now()
        rows = [
            (
                stats.match_id,
                stats.account_id,
                stats.weapon_code,
                stats.shots_fired,
                stats.shots_hit,
                stats.hits_taken,
                stats.damage_dealt,
                stats.damage_taken,
                stats.kills,
                stats.assists,
                stats.deaths,
                stats.dbnos,
                stats.dbnos_taken,
                stats.finishes,
                stats.finishes_taken,
                stats.headshot_hits,
                stats.headshot_hits_taken,
                stats.headshot_kills,
                stats.headshot_deaths,
                stats.headshot_dbnos,
                stats.headshot_dbnos_taken,
                json.dumps(stats.hit_parts, ensure_ascii=False, separators=(",", ":")),
                json.dumps(stats.taken_hit_parts, ensure_ascii=False, separators=(",", ":")),
                timestamp,
            )
            for stats in weapon_stats
        ]

        with self.connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO player_weapon_match_stats (
                    match_id,
                    account_id,
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
                    finishes,
                    finishes_taken,
                    headshot_hits,
                    headshot_hits_taken,
                    headshot_kills,
                    headshot_deaths,
                    headshot_dbnos,
                    headshot_dbnos_taken,
                    hit_parts,
                    taken_hit_parts,
                    updated_at_kst
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                rows,
            )


def _ensure_summaries_for_tracked_accounts(
    *,
    match_id: str,
    tracked_account_ids: set[str],
    summaries: list[PlayerMatchCombatSummary],
) -> list[PlayerMatchCombatSummary]:
    by_account = {summary.account_id: summary for summary in summaries}
    for account_id in tracked_account_ids:
        by_account.setdefault(
            account_id,
            PlayerMatchCombatSummary(match_id=match_id, account_id=account_id),
        )
    return sorted(by_account.values(), key=lambda summary: summary.account_id)


def _required_text(value: Any, label: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise TelemetryCombatProcessingError(f"{label} is required.")


def _mysql_kst_now() -> datetime:
    return now_kst().replace(tzinfo=None)
