from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping
import hashlib
import json

from pubg_ai.database import mysql_transaction
from pubg_ai.match_explorer import MatchExplorerError, MatchExplorerService
from pubg_ai.pubg_client import PubgApiClient, PubgPlayer
from pubg_ai.raw_storage import RawPayloadStore
from pubg_ai.time_utils import KST, now_kst, to_kst


ENGAGEMENT_GAP_SECONDS = 45


class WatchlistError(RuntimeError):
    """Raised when a watched player cannot be registered, updated, or analyzed."""


@dataclass(frozen=True)
class WatchedPlayer:
    id: int
    account_id: str
    shard: str
    current_name: str
    active: bool
    notify_name_change: bool
    notify_kill: bool
    notify_repeated_engagement: bool
    engagement_threshold: int
    notification_channel_ids: tuple[str, ...]
    last_identity_checked_at_kst: datetime | None
    created_at_kst: datetime
    updated_at_kst: datetime
    aliases: tuple[str, ...] = ()
    encounter_count: int = 0
    latest_encounter_at_kst: datetime | None = None

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        for key in (
            "last_identity_checked_at_kst",
            "created_at_kst",
            "updated_at_kst",
            "latest_encounter_at_kst",
        ):
            value = record[key]
            record[key] = value.isoformat() if value else None
        record["notification_channel_ids"] = list(self.notification_channel_ids)
        record["aliases"] = list(self.aliases)
        return record


@dataclass(frozen=True)
class WatchlistScanResult:
    candidate_encounters: int
    analyzed_encounters: int
    alerts_created: int
    failed_encounters: int

    def to_record(self) -> dict[str, int]:
        return asdict(self)


class WatchlistService:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def register_by_name(
        self,
        *,
        pubg_client: PubgApiClient,
        shard: str,
        player_name: str,
        notify_name_change: bool = True,
        notify_kill: bool = True,
        notify_repeated_engagement: bool = True,
        engagement_threshold: int = 3,
        notification_channel_ids: Iterable[str] = (),
    ) -> WatchedPlayer:
        resolved = pubg_client.lookup_player_by_name(
            _required_text(shard, "플랫폼").lower(),
            _required_text(player_name, "닉네임"),
        )
        return self.register_resolved(
            player=resolved,
            notify_name_change=notify_name_change,
            notify_kill=notify_kill,
            notify_repeated_engagement=notify_repeated_engagement,
            engagement_threshold=engagement_threshold,
            notification_channel_ids=notification_channel_ids,
        )

    def register_resolved(
        self,
        *,
        player: PubgPlayer,
        notify_name_change: bool = True,
        notify_kill: bool = True,
        notify_repeated_engagement: bool = True,
        engagement_threshold: int = 3,
        notification_channel_ids: Iterable[str] = (),
    ) -> WatchedPlayer:
        threshold = _threshold(engagement_threshold)
        channels = _channel_ids(notification_channel_ids)
        timestamp = _mysql_now()
        with mysql_transaction(self.connection):
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO watched_players (
                        account_id, shard, current_name, active,
                        notify_name_change, notify_kill,
                        notify_repeated_engagement, engagement_threshold,
                        notification_channel_ids_json,
                        last_identity_checked_at_kst, created_at_kst, updated_at_kst
                    )
                    VALUES (%s, %s, %s, 1, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        current_name = VALUES(current_name),
                        active = 1,
                        notify_name_change = VALUES(notify_name_change),
                        notify_kill = VALUES(notify_kill),
                        notify_repeated_engagement = VALUES(notify_repeated_engagement),
                        engagement_threshold = VALUES(engagement_threshold),
                        notification_channel_ids_json = VALUES(notification_channel_ids_json),
                        last_identity_checked_at_kst = VALUES(last_identity_checked_at_kst),
                        updated_at_kst = VALUES(updated_at_kst)
                    """,
                    (
                        player.account_id,
                        player.shard.lower(),
                        player.name,
                        notify_name_change,
                        notify_kill,
                        notify_repeated_engagement,
                        threshold,
                        json.dumps(channels, separators=(",", ":")),
                        timestamp,
                        timestamp,
                        timestamp,
                    ),
                )
            saved = self.get(account_id=player.account_id, shard=player.shard)
            if saved is None:
                raise WatchlistError("감시 플레이어 저장 결과를 찾을 수 없습니다.")
            self._upsert_alias(
                watched_player_id=saved.id,
                account_id=saved.account_id,
                shard=saved.shard,
                name=player.name,
                source="registration",
                timestamp=timestamp,
            )
        refreshed = self.get(account_id=player.account_id, shard=player.shard)
        if refreshed is None:
            raise WatchlistError("감시 플레이어를 다시 불러올 수 없습니다.")
        return refreshed

    def get(self, *, account_id: str, shard: str) -> WatchedPlayer | None:
        records = self._list(
            where="WHERE watched.account_id = %s AND watched.shard = %s",
            params=[_required_text(account_id, "Account ID"), _required_text(shard, "플랫폼").lower()],
            limit=1,
        )
        return records[0] if records else None

    def list_players(
        self,
        *,
        include_inactive: bool = True,
        search: str | None = None,
        limit: int = 500,
    ) -> list[WatchedPlayer]:
        clauses: list[str] = []
        params: list[Any] = []
        if not include_inactive:
            clauses.append("watched.active = 1")
        if normalized := _optional_text(search):
            clauses.append(
                "(watched.current_name LIKE %s OR watched.account_id LIKE %s "
                "OR EXISTS (SELECT 1 FROM watched_player_aliases aliases "
                "WHERE aliases.watched_player_id = watched.id AND aliases.name LIKE %s))"
            )
            pattern = f"%{normalized}%"
            params.extend([pattern, pattern, pattern])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self._list(where=where, params=params, limit=max(1, min(int(limit), 1000)))

    def update(
        self,
        *,
        account_id: str,
        shard: str,
        active: bool,
        notify_name_change: bool,
        notify_kill: bool,
        notify_repeated_engagement: bool,
        engagement_threshold: int,
        notification_channel_ids: Iterable[str],
    ) -> WatchedPlayer:
        channels = _channel_ids(notification_channel_ids)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE watched_players
                SET active = %s,
                    notify_name_change = %s,
                    notify_kill = %s,
                    notify_repeated_engagement = %s,
                    engagement_threshold = %s,
                    notification_channel_ids_json = %s,
                    updated_at_kst = %s
                WHERE account_id = %s AND shard = %s
                """,
                (
                    active,
                    notify_name_change,
                    notify_kill,
                    notify_repeated_engagement,
                    _threshold(engagement_threshold),
                    json.dumps(channels, separators=(",", ":")),
                    _mysql_now(),
                    _required_text(account_id, "Account ID"),
                    _required_text(shard, "플랫폼").lower(),
                ),
            )
            if cursor.rowcount == 0:
                raise WatchlistError("감시 플레이어를 찾을 수 없습니다.")
        saved = self.get(account_id=account_id, shard=shard)
        if saved is None:
            raise WatchlistError("수정한 감시 플레이어를 불러올 수 없습니다.")
        return saved

    def refresh_identities(
        self,
        *,
        pubg_client: PubgApiClient,
        force: bool = False,
        max_age_minutes: int = 60,
    ) -> dict[str, int]:
        players = self.list_players(include_inactive=False, limit=1000)
        cutoff = now_kst() - timedelta(minutes=max(1, int(max_age_minutes)))
        due = [
            player
            for player in players
            if force
            or player.last_identity_checked_at_kst is None
            or to_kst(player.last_identity_checked_at_kst) <= cutoff
        ]
        checked = 0
        changed = 0
        alerts = 0
        by_shard: defaultdict[str, list[WatchedPlayer]] = defaultdict(list)
        for player in due:
            by_shard[player.shard].append(player)

        for shard, shard_players in by_shard.items():
            for chunk in _chunks(shard_players, 10):
                result = pubg_client.refresh_players_by_ids(
                    shard,
                    [player.account_id for player in chunk],
                )
                snapshots = {item.account_id: item for item in result.snapshots}
                timestamp = _mysql_now()
                for player in chunk:
                    checked += 1
                    snapshot = snapshots.get(player.account_id)
                    if snapshot is not None and snapshot.name != player.current_name:
                        changed += 1
                        alerts += self._record_observed_name(
                            player=player,
                            observed_name=snapshot.name,
                            source="api_refresh",
                            timestamp=timestamp,
                        )
                    else:
                        with self.connection.cursor() as cursor:
                            cursor.execute(
                                """
                                UPDATE watched_players
                                SET last_identity_checked_at_kst = %s,
                                    updated_at_kst = %s
                                WHERE id = %s
                                """,
                                (timestamp, timestamp, player.id),
                            )
        return {"checked": checked, "changed": changed, "alerts_created": alerts}

    def _record_observed_name(
        self,
        *,
        player: WatchedPlayer,
        observed_name: str,
        source: str,
        timestamp: datetime,
    ) -> int:
        normalized = _required_text(observed_name, "닉네임")
        if normalized == player.current_name:
            return 0
        old_name = player.current_name
        with mysql_transaction(self.connection):
            self._upsert_alias(
                watched_player_id=player.id,
                account_id=player.account_id,
                shard=player.shard,
                name=old_name,
                source=source,
                timestamp=timestamp,
            )
            self._upsert_alias(
                watched_player_id=player.id,
                account_id=player.account_id,
                shard=player.shard,
                name=normalized,
                source=source,
                timestamp=timestamp,
            )
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE watched_players
                    SET current_name = %s,
                        last_identity_checked_at_kst = %s,
                        updated_at_kst = %s
                    WHERE id = %s
                    """,
                    (normalized, timestamp, timestamp, player.id),
                )
            if player.notify_name_change:
                self._insert_alert(
                    player=player,
                    alert_type="name_change",
                    severity="warning",
                    title="감시 플레이어 닉네임 변경",
                    message=(
                        f"{old_name} 님의 닉네임이 {normalized}(으)로 변경되었습니다. "
                        f"Account ID {player.account_id} 기준으로 동일 계정임을 확인했습니다."
                    ),
                    match_id=None,
                    registered_account_id=None,
                    metadata={"old_name": old_name, "new_name": normalized},
                    dedupe_source=f"name:{player.id}:{old_name}:{normalized}",
                    timestamp=timestamp,
                )
                return 1
        return 0

    def scan_encounters(
        self,
        *,
        raw_store: RawPayloadStore,
        limit: int = 100,
        force: bool = False,
    ) -> WatchlistScanResult:
        candidates = self._encounter_candidates(
            limit=max(1, min(int(limit), 500)),
            force=force,
        )
        explorer = MatchExplorerService(self.connection, raw_store)
        source_cache: dict[str, dict[str, Any] | None] = {}
        analyzed = 0
        alerts_created = 0
        failed = 0
        for candidate in candidates:
            match_id = str(candidate["match_id"])
            try:
                if match_id not in source_cache:
                    source_cache[match_id] = explorer.get_replay_source(match_id)
                source = source_cache[match_id]
                if source is None:
                    raise WatchlistError("저장된 매치를 찾을 수 없습니다.")
                metrics = analyze_watchlist_pair(
                    source["events"],
                    watched_account_id=str(candidate["watched_account_id"]),
                    registered_account_id=str(candidate["registered_account_id"]),
                )
                self._save_encounter(candidate=candidate, metrics=metrics)
                alerts_created += self._create_encounter_alerts(
                    candidate=candidate,
                    metrics=metrics,
                )
                analyzed += 1
            except (MatchExplorerError, WatchlistError, OSError, ValueError):
                failed += 1
        return WatchlistScanResult(
            candidate_encounters=len(candidates),
            analyzed_encounters=analyzed,
            alerts_created=alerts_created,
            failed_encounters=failed,
        )

    def _encounter_candidates(
        self,
        *,
        limit: int,
        force: bool,
    ) -> list[dict[str, Any]]:
        existing_filter = "" if force else """
            AND NOT EXISTS (
                SELECT 1
                FROM watched_player_encounters analyzed
                WHERE analyzed.watched_player_id = watched.id
                  AND analyzed.match_id = matches.match_id
                  AND analyzed.registered_account_id = registered.account_id
            )
        """
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT
                    watched.id AS watched_player_id,
                    watched.account_id AS watched_account_id,
                    watched.shard,
                    watched.current_name AS watched_name,
                    watched.notify_kill,
                    watched.notify_repeated_engagement,
                    watched.engagement_threshold,
                    watched.notification_channel_ids_json,
                    matches.match_id,
                    matches.created_at_kst,
                    watched_participant.name AS watched_match_name,
                    registered.id AS registered_player_id,
                    registered.account_id AS registered_account_id,
                    registered.current_name AS registered_name
                FROM watched_players watched
                INNER JOIN match_participants watched_participant
                    ON watched_participant.account_id = watched.account_id
                INNER JOIN analysis_matches AS matches
                    ON matches.match_id = watched_participant.match_id
                   AND matches.shard = watched.shard
                INNER JOIN raw_telemetry_payloads raw_telemetry
                    ON raw_telemetry.match_id = matches.match_id
                INNER JOIN match_participants registered_participant
                    ON registered_participant.match_id = matches.match_id
                   AND registered_participant.account_id <> watched.account_id
                INNER JOIN registered_players registered
                    ON registered.account_id = registered_participant.account_id
                   AND registered.shard = matches.shard
                   AND registered.active = 1
                WHERE watched.active = 1
                {existing_filter}
                ORDER BY matches.created_at_kst ASC, watched.id ASC, registered.id ASC
                LIMIT %s
                """,
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def _save_encounter(
        self,
        *,
        candidate: Mapping[str, Any],
        metrics: Mapping[str, Any],
    ) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO watched_player_encounters (
                    watched_player_id, match_id, registered_account_id,
                    watched_name, registered_name,
                    engagement_count,
                    damage_events_by_watched, damage_events_by_registered,
                    dbnos_by_watched, dbnos_by_registered,
                    kills_by_watched, kills_by_registered,
                    first_interaction_at_kst, last_interaction_at_kst,
                    analyzed_at_kst
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    watched_name = VALUES(watched_name),
                    registered_name = VALUES(registered_name),
                    engagement_count = VALUES(engagement_count),
                    damage_events_by_watched = VALUES(damage_events_by_watched),
                    damage_events_by_registered = VALUES(damage_events_by_registered),
                    dbnos_by_watched = VALUES(dbnos_by_watched),
                    dbnos_by_registered = VALUES(dbnos_by_registered),
                    kills_by_watched = VALUES(kills_by_watched),
                    kills_by_registered = VALUES(kills_by_registered),
                    first_interaction_at_kst = VALUES(first_interaction_at_kst),
                    last_interaction_at_kst = VALUES(last_interaction_at_kst),
                    analyzed_at_kst = VALUES(analyzed_at_kst)
                """,
                (
                    candidate["watched_player_id"],
                    candidate["match_id"],
                    candidate["registered_account_id"],
                    candidate.get("watched_match_name") or candidate["watched_name"],
                    candidate["registered_name"],
                    metrics["engagement_count"],
                    metrics["damage_events_by_watched"],
                    metrics["damage_events_by_registered"],
                    metrics["dbnos_by_watched"],
                    metrics["dbnos_by_registered"],
                    metrics["kills_by_watched"],
                    metrics["kills_by_registered"],
                    metrics["first_interaction_at_kst"],
                    metrics["last_interaction_at_kst"],
                    _mysql_now(),
                ),
            )

    def _create_encounter_alerts(
        self,
        *,
        candidate: Mapping[str, Any],
        metrics: Mapping[str, Any],
    ) -> int:
        player = self.get(
            account_id=str(candidate["watched_account_id"]),
            shard=str(candidate["shard"]),
        )
        if player is None:
            return 0
        created = 0
        common_metadata = {
            "match_id": candidate["match_id"],
            "watched_account_id": player.account_id,
            "watched_name": candidate.get("watched_match_name") or player.current_name,
            "registered_account_id": candidate["registered_account_id"],
            "registered_name": candidate["registered_name"],
            "channel_ids": list(player.notification_channel_ids),
            "guild_ids": self._registered_guild_ids(
                account_id=str(candidate["registered_account_id"]),
                shard=player.shard,
            ),
            **dict(metrics),
        }
        if player.notify_kill and int(metrics["kills_by_watched"]) > 0:
            created += self._insert_alert(
                player=player,
                alert_type="registered_player_killed",
                severity="error",
                title="감시 플레이어 킬 감지",
                message=(
                    f"{common_metadata['watched_name']} 님이 등록 유저 "
                    f"{candidate['registered_name']} 님을 "
                    f"{metrics['kills_by_watched']}회 처치했습니다. "
                    f"매치={candidate['match_id']}"
                ),
                match_id=str(candidate["match_id"]),
                registered_account_id=str(candidate["registered_account_id"]),
                metadata=common_metadata,
                dedupe_source=(
                    f"kill:{player.id}:{candidate['match_id']}:"
                    f"{candidate['registered_account_id']}"
                ),
                timestamp=_mysql_now(),
            )
        engagement_count = int(metrics["engagement_count"])
        if (
            player.notify_repeated_engagement
            and engagement_count >= player.engagement_threshold
        ):
            created += self._insert_alert(
                player=player,
                alert_type="repeated_engagement",
                severity="warning",
                title="감시 플레이어 반복 교전 감지",
                message=(
                    f"{common_metadata['watched_name']} 님과 등록 유저 "
                    f"{candidate['registered_name']} 님 사이에 독립 교전이 "
                    f"{engagement_count}회 발생했습니다 "
                    f"(기준 {player.engagement_threshold}회). "
                    f"매치={candidate['match_id']}"
                ),
                match_id=str(candidate["match_id"]),
                registered_account_id=str(candidate["registered_account_id"]),
                metadata=common_metadata,
                dedupe_source=(
                    f"engagement:{player.id}:{candidate['match_id']}:"
                    f"{candidate['registered_account_id']}:{player.engagement_threshold}"
                ),
                timestamp=_mysql_now(),
            )
        return created

    def list_encounters(
        self,
        *,
        watched_player_id: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        where = ""
        params: list[Any] = []
        if watched_player_id is not None:
            where = "WHERE encounters.watched_player_id = %s"
            params.append(int(watched_player_id))
        params.append(max(1, min(int(limit), 500)))
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT
                    encounters.*,
                    matches.created_at_kst,
                    matches.map_name,
                    matches.game_mode,
                    matches.match_type
                FROM watched_player_encounters encounters
                LEFT JOIN analysis_matches AS matches ON matches.match_id = encounters.match_id
                {where}
                ORDER BY matches.created_at_kst DESC, encounters.id DESC
                LIMIT %s
                """,
                tuple(params),
            )
            return [_json_ready(dict(row)) for row in cursor.fetchall()]

    def _list(
        self,
        *,
        where: str,
        params: list[Any],
        limit: int,
    ) -> list[WatchedPlayer]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT
                    watched.*,
                    (
                        SELECT COUNT(*)
                        FROM watched_player_encounters encounters
                        WHERE encounters.watched_player_id = watched.id
                    ) AS encounter_count,
                    (
                        SELECT MAX(matches.created_at_kst)
                        FROM watched_player_encounters encounters
                        LEFT JOIN analysis_matches AS matches ON matches.match_id = encounters.match_id
                        WHERE encounters.watched_player_id = watched.id
                    ) AS latest_encounter_at_kst
                FROM watched_players watched
                {where}
                ORDER BY watched.active DESC, watched.current_name ASC
                LIMIT %s
                """,
                tuple([*params, limit]),
            )
            rows = [dict(row) for row in cursor.fetchall()]
        if not rows:
            return []
        ids = [int(row["id"]) for row in rows]
        placeholders = ", ".join(["%s"] * len(ids))
        aliases: defaultdict[int, list[str]] = defaultdict(list)
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT watched_player_id, name
                FROM watched_player_aliases
                WHERE watched_player_id IN ({placeholders})
                ORDER BY last_seen_at_kst DESC, id DESC
                """,
                tuple(ids),
            )
            for row in cursor.fetchall():
                aliases[int(row["watched_player_id"])].append(str(row["name"]))
        return [
            _watched_player_from_row(row, tuple(aliases[int(row["id"])]))
            for row in rows
        ]

    def _upsert_alias(
        self,
        *,
        watched_player_id: int,
        account_id: str,
        shard: str,
        name: str,
        source: str,
        timestamp: datetime,
    ) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO watched_player_aliases (
                    watched_player_id, account_id, shard, name, source,
                    first_seen_at_kst, last_seen_at_kst
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    source = VALUES(source),
                    last_seen_at_kst = VALUES(last_seen_at_kst)
                """,
                (
                    watched_player_id,
                    account_id,
                    shard,
                    name,
                    source,
                    timestamp,
                    timestamp,
                ),
            )

    def _insert_alert(
        self,
        *,
        player: WatchedPlayer,
        alert_type: str,
        severity: str,
        title: str,
        message: str,
        match_id: str | None,
        registered_account_id: str | None,
        metadata: Mapping[str, Any],
        dedupe_source: str,
        timestamp: datetime,
    ) -> int:
        merged_metadata = {
            "watched_player_id": player.id,
            "watched_account_id": player.account_id,
            "watched_name": player.current_name,
            "channel_ids": list(player.notification_channel_ids),
            **dict(metadata),
        }
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO watchlist_alert_events (
                    watched_player_id, match_id, registered_account_id,
                    alert_type, severity, title, message, metadata_json,
                    dedupe_key, created_at_kst
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE id = id
                """,
                (
                    player.id,
                    match_id,
                    registered_account_id,
                    alert_type,
                    severity,
                    title,
                    message,
                    json.dumps(
                        _json_ready(merged_metadata),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    hashlib.sha256(dedupe_source.encode("utf-8")).hexdigest(),
                    timestamp,
                ),
            )
            return 1 if cursor.rowcount == 1 else 0

    def _registered_guild_ids(
        self,
        *,
        account_id: str,
        shard: str,
    ) -> list[str]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT registrations.guild_id
                FROM registered_players players
                INNER JOIN player_discord_registrations registrations
                    ON registrations.registered_player_id = players.id
                   AND registrations.active = 1
                WHERE players.account_id = %s AND players.shard = %s
                ORDER BY registrations.guild_id
                """,
                (account_id, shard),
            )
            return [str(row["guild_id"]) for row in cursor.fetchall()]


def analyze_watchlist_pair(
    events: Iterable[Mapping[str, Any]],
    *,
    watched_account_id: str,
    registered_account_id: str,
) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "engagement_count": 0,
        "damage_events_by_watched": 0,
        "damage_events_by_registered": 0,
        "dbnos_by_watched": 0,
        "dbnos_by_registered": 0,
        "kills_by_watched": 0,
        "kills_by_registered": 0,
        "first_interaction_at_kst": None,
        "last_interaction_at_kst": None,
    }
    interaction_times: list[datetime] = []
    for event in events:
        event_type = str(event.get("_T") or "")
        if event_type == "LogPlayerTakeDamage":
            attacker_id = _character_account_id(event.get("attacker"))
            victim_id = _character_account_id(event.get("victim"))
            if not _pair_matches(
                attacker_id,
                victim_id,
                watched_account_id,
                registered_account_id,
            ):
                continue
            if not _is_combat_damage(event):
                continue
            key = (
                "damage_events_by_watched"
                if attacker_id == watched_account_id
                else "damage_events_by_registered"
            )
            metrics[key] += 1
            if event_at := _event_datetime(event):
                interaction_times.append(event_at)
        elif event_type == "LogPlayerMakeGroggy":
            attacker_id = _character_account_id(event.get("attacker"))
            victim_id = _character_account_id(event.get("victim"))
            if not _pair_matches(
                attacker_id,
                victim_id,
                watched_account_id,
                registered_account_id,
            ):
                continue
            key = (
                "dbnos_by_watched"
                if attacker_id == watched_account_id
                else "dbnos_by_registered"
            )
            metrics[key] += 1
            if event_at := _event_datetime(event):
                interaction_times.append(event_at)
        elif event_type == "LogPlayerKillV2":
            victim_id = _character_account_id(event.get("victim"))
            killer_id = _character_account_id(event.get("killer"))
            if killer_id is None:
                killer_id = _character_account_id(event.get("finisher"))
            if not _pair_matches(
                killer_id,
                victim_id,
                watched_account_id,
                registered_account_id,
            ):
                continue
            key = (
                "kills_by_watched"
                if killer_id == watched_account_id
                else "kills_by_registered"
            )
            metrics[key] += 1
            if event_at := _event_datetime(event):
                interaction_times.append(event_at)
    if interaction_times:
        ordered_times = sorted(interaction_times)
        engagement_count = 1
        for previous, current in zip(ordered_times, ordered_times[1:]):
            if (current - previous).total_seconds() > ENGAGEMENT_GAP_SECONDS:
                engagement_count += 1
        metrics["engagement_count"] = engagement_count
        metrics["first_interaction_at_kst"] = ordered_times[0].replace(tzinfo=None)
        metrics["last_interaction_at_kst"] = ordered_times[-1].replace(tzinfo=None)
    elif any(
        int(metrics[key]) > 0
        for key in (
            "damage_events_by_watched",
            "damage_events_by_registered",
            "dbnos_by_watched",
            "dbnos_by_registered",
            "kills_by_watched",
            "kills_by_registered",
        )
    ):
        metrics["engagement_count"] = 1
    return metrics


def pending_watchlist_alerts(
    connection: Any,
    *,
    limit: int = 100,
) -> list[Any]:
    from pubg_ai.system_alerts import SystemAlert

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                id, alert_type, severity, title, message,
                metadata_json, created_at_kst
            FROM watchlist_alert_events
            WHERE notified_at_kst IS NULL
            ORDER BY created_at_kst ASC, id ASC
            LIMIT %s
            """,
            (max(1, min(int(limit), 500)),),
        )
        rows = cursor.fetchall()
    alerts: list[Any] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if any(row.get(key) is None for key in ("id", "severity", "title", "message")):
            continue
        try:
            alert_event_id = int(row["id"])
        except (TypeError, ValueError):
            continue
        metadata = _json_mapping(row.get("metadata_json"))
        metadata["watchlist_alert_event_id"] = alert_event_id
        alerts.append(
            SystemAlert(
                key=f"watchlist:{alert_event_id}",
                source="watchlist",
                severity=str(row["severity"]),
                title=str(row["title"]),
                message=str(row["message"]),
                created_at_kst=_iso_datetime(row.get("created_at_kst")),
                source_id=alert_event_id,
                metadata=metadata,
            )
        )
    return alerts


def mark_watchlist_alert_notified(connection: Any, alert_event_id: int) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE watchlist_alert_events
            SET notified_at_kst = COALESCE(notified_at_kst, %s)
            WHERE id = %s
            """,
            (_mysql_now(), int(alert_event_id)),
        )


def _pair_matches(
    actor_id: str | None,
    target_id: str | None,
    watched_account_id: str,
    registered_account_id: str,
) -> bool:
    return (
        actor_id == watched_account_id
        and target_id == registered_account_id
    ) or (
        actor_id == registered_account_id
        and target_id == watched_account_id
    )


def _is_combat_damage(event: Mapping[str, Any]) -> bool:
    category = _optional_text(event.get("damageTypeCategory"))
    if category is None:
        return False
    return category.startswith("Damage_Gun") or category in {
        "Damage_Throwable",
        "Damage_Explosion_Grenade",
        "Damage_Melee",
        "Damage_Punch",
        "Damage_MeleeThrow",
        "Damage_Molotov",
        "Damage_Explosion_PanzerFaustWarhead",
        "Damage_Explosion_PanzerFaustBackBlast",
        "Damage_BlueZoneGrenade",
        "Damage_Explosion_JerryCan",
        "Damage_Explosion_Mortar",
        "Damage_Explosion_StickyBomb",
        "Damage_Explosion_C4",
    }


def _character_account_id(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    return _optional_text(value.get("accountId"))


def _event_datetime(event: Mapping[str, Any]) -> datetime | None:
    value = event.get("_D")
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return to_kst(parsed)


def _watched_player_from_row(
    row: Mapping[str, Any],
    aliases: tuple[str, ...],
) -> WatchedPlayer:
    return WatchedPlayer(
        id=int(row["id"]),
        account_id=str(row["account_id"]),
        shard=str(row["shard"]),
        current_name=str(row["current_name"]),
        active=bool(row["active"]),
        notify_name_change=bool(row["notify_name_change"]),
        notify_kill=bool(row["notify_kill"]),
        notify_repeated_engagement=bool(row["notify_repeated_engagement"]),
        engagement_threshold=int(row["engagement_threshold"]),
        notification_channel_ids=tuple(
            _channel_ids(_json_list(row.get("notification_channel_ids_json")))
        ),
        last_identity_checked_at_kst=_datetime_value(
            row.get("last_identity_checked_at_kst")
        ),
        created_at_kst=_datetime_value(row.get("created_at_kst")) or _mysql_now(),
        updated_at_kst=_datetime_value(row.get("updated_at_kst")) or _mysql_now(),
        aliases=aliases,
        encounter_count=int(row.get("encounter_count") or 0),
        latest_encounter_at_kst=_datetime_value(
            row.get("latest_encounter_at_kst")
        ),
    )


def _channel_ids(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if normalized and normalized.isdigit() and normalized not in result:
            result.append(normalized)
        if len(result) >= 50:
            break
    return result


def _threshold(value: Any) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise WatchlistError("반복 교전 기준은 숫자여야 합니다.") from exc
    if not 1 <= normalized <= 100:
        raise WatchlistError("반복 교전 기준은 1~100회여야 합니다.")
    return normalized


def _required_text(value: Any, field: str) -> str:
    normalized = _optional_text(value)
    if normalized is None:
        raise WatchlistError(f"{field} 값이 비어 있습니다.")
    return normalized


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            loaded = json.loads(value)
        except ValueError:
            return []
        return loaded if isinstance(loaded, list) else []
    return []


def _json_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            loaded = json.loads(value)
        except ValueError:
            return {}
        return dict(loaded) if isinstance(loaded, Mapping) else {}
    return {}


def _json_ready(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


def _datetime_value(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _iso_datetime(value: Any) -> str:
    parsed = _datetime_value(value)
    if parsed is None:
        return now_kst().isoformat()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return to_kst(parsed).isoformat()


def _mysql_now() -> datetime:
    return now_kst().replace(tzinfo=None)


def _chunks(values: list[Any], size: int) -> Iterable[list[Any]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]
