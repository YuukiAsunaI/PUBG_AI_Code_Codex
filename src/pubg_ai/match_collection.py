from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Iterable
import json

from pubg_ai.player_registry import PlayerRegistry, RegisteredPlayer
from pubg_ai.pubg_client import PubgApiClient, PubgPlayerSnapshot
from pubg_ai.time_utils import now_kst


class MatchCollectionError(RuntimeError):
    """Raised when registered player match discovery fails."""


@dataclass(frozen=True)
class MatchCollectionResult:
    registered_players: int
    refreshed_players: int
    discovered_matches: int
    queued_match_jobs: int
    existing_match_jobs: int

    def to_record(self) -> dict[str, int]:
        return asdict(self)


class RegisteredPlayerMatchCollector:
    def __init__(
        self,
        connection: Any,
        pubg_client: PubgApiClient | None = None,
        *,
        lookup_chunk_size: int = 10,
    ) -> None:
        if not 1 <= lookup_chunk_size <= 10:
            raise MatchCollectionError("lookup_chunk_size must be between 1 and 10.")
        self.connection = connection
        self.pubg_client = pubg_client
        self.lookup_chunk_size = lookup_chunk_size

    def collect_active_players(
        self,
        *,
        shard: str | None = None,
        limit: int = 100,
    ) -> MatchCollectionResult:
        players = PlayerRegistry(self.connection).list_players(
            shard=shard,
            active_only=True,
            limit=limit,
        )
        by_shard: dict[str, list[RegisteredPlayer]] = defaultdict(list)
        for player in players:
            by_shard[player.shard].append(player)

        refreshed_players = 0
        discovered_match_ids: set[str] = set()
        queued_jobs = 0
        existing_jobs = 0

        for player_shard, shard_players in by_shard.items():
            for chunk in _chunked(shard_players, self.lookup_chunk_size):
                account_ids = [player.account_id for player in chunk]
                if self.pubg_client is None:
                    raise MatchCollectionError("pubg_client is required to collect active players.")
                result = self.pubg_client.refresh_players_by_ids(player_shard, account_ids)
                snapshot_by_account = {snapshot.account_id: snapshot for snapshot in result.snapshots}

                for player in chunk:
                    snapshot = snapshot_by_account.get(player.account_id)
                    if snapshot is None:
                        self._record_collection_error(player, "player not returned by PUBG refresh")
                        continue

                    refreshed_players += 1
                    self._store_player_snapshot(snapshot)
                    self._refresh_registered_player_alias(player, snapshot)
                    self._update_collection_state(player, snapshot.match_ids)

                    for match_id in snapshot.match_ids:
                        discovered_match_ids.add(match_id)
                        queued = self._enqueue_match_job(player_shard, match_id)
                        if queued:
                            queued_jobs += 1
                        else:
                            existing_jobs += 1

        return MatchCollectionResult(
            registered_players=len(players),
            refreshed_players=refreshed_players,
            discovered_matches=len(discovered_match_ids),
            queued_match_jobs=queued_jobs,
            existing_match_jobs=existing_jobs,
        )

    def list_match_jobs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, job_type, shard, target_id, status, attempts, next_run_at_kst,
                       last_error, created_at_kst, updated_at_kst
                FROM api_fetch_jobs
                WHERE job_type = 'match'
                ORDER BY created_at_kst DESC, id DESC
                LIMIT %s
                """,
                (limit,),
            )
            return list(cursor.fetchall())

    def _store_player_snapshot(self, snapshot: PubgPlayerSnapshot) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO raw_player_snapshots (account_id, shard, fetched_at_kst, payload)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    snapshot.account_id,
                    snapshot.shard,
                    _mysql_kst_now(),
                    json.dumps(snapshot.raw_payload, ensure_ascii=False, separators=(",", ":")),
                ),
            )

    def _refresh_registered_player_alias(
        self,
        player: RegisteredPlayer,
        snapshot: PubgPlayerSnapshot,
    ) -> None:
        timestamp = _mysql_kst_now()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE registered_players
                SET current_name = %s, updated_at_kst = %s
                WHERE id = %s
                """,
                (snapshot.name, timestamp, player.id),
            )
            cursor.execute(
                """
                INSERT INTO player_aliases (
                    registered_player_id,
                    account_id,
                    shard,
                    name,
                    source,
                    first_seen_at_kst,
                    last_seen_at_kst
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    registered_player_id = VALUES(registered_player_id),
                    last_seen_at_kst = VALUES(last_seen_at_kst)
                """,
                (
                    player.id,
                    snapshot.account_id,
                    snapshot.shard,
                    snapshot.name,
                    "player_refresh",
                    timestamp,
                    timestamp,
                ),
            )

    def _update_collection_state(self, player: RegisteredPlayer, match_ids: list[str]) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO player_collection_states (
                    registered_player_id,
                    last_polled_at_kst,
                    last_seen_match_id,
                    last_error,
                    updated_at_kst
                )
                VALUES (%s, %s, %s, NULL, %s)
                ON DUPLICATE KEY UPDATE
                    last_polled_at_kst = VALUES(last_polled_at_kst),
                    last_seen_match_id = VALUES(last_seen_match_id),
                    last_error = NULL,
                    updated_at_kst = VALUES(updated_at_kst)
                """,
                (
                    player.id,
                    _mysql_kst_now(),
                    match_ids[0] if match_ids else None,
                    _mysql_kst_now(),
                ),
            )

    def _record_collection_error(self, player: RegisteredPlayer, error: str) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO player_collection_states (
                    registered_player_id,
                    last_polled_at_kst,
                    last_seen_match_id,
                    last_error,
                    updated_at_kst
                )
                VALUES (%s, %s, NULL, %s, %s)
                ON DUPLICATE KEY UPDATE
                    last_polled_at_kst = VALUES(last_polled_at_kst),
                    last_error = VALUES(last_error),
                    updated_at_kst = VALUES(updated_at_kst)
                """,
                (player.id, _mysql_kst_now(), error, _mysql_kst_now()),
            )

    def _enqueue_match_job(self, shard: str, match_id: str) -> bool:
        if self._match_exists(match_id) or self._match_job_exists(shard, match_id):
            return False

        timestamp = _mysql_kst_now()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO api_fetch_jobs (
                    job_type,
                    shard,
                    target_id,
                    status,
                    attempts,
                    next_run_at_kst,
                    created_at_kst,
                    updated_at_kst
                )
                VALUES ('match', %s, %s, 'queued', 0, %s, %s, %s)
                """,
                (shard, match_id, timestamp, timestamp, timestamp),
            )
        return True

    def _match_exists(self, match_id: str) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM matches WHERE match_id = %s LIMIT 1", (match_id,))
            return cursor.fetchone() is not None

    def _match_job_exists(self, shard: str, match_id: str) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM api_fetch_jobs
                WHERE job_type = 'match' AND shard = %s AND target_id = %s
                LIMIT 1
                """,
                (shard, match_id),
            )
            return cursor.fetchone() is not None


def _chunked(values: list[RegisteredPlayer], size: int) -> Iterable[list[RegisteredPlayer]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _mysql_kst_now() -> datetime:
    return now_kst().replace(tzinfo=None)
