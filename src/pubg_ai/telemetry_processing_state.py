from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Mapping

from pubg_ai.time_utils import now_kst


def list_pending_telemetry_payloads(
    connection: Any,
    *,
    processor_name: str,
    parser_version: str,
    limit: int,
    force: bool,
) -> list[dict[str, Any]]:
    state_filter = ""
    params: list[Any] = []
    if not force:
        parser_rank = parser_version_rank(parser_version)
        state_filter = """
            WHERE EXISTS (
                SELECT 1
                FROM match_participants participants
                INNER JOIN registered_players players
                    ON players.account_id = participants.account_id
                   AND players.shard = raw_payloads.shard
                LEFT JOIN player_telemetry_processing_states states
                    ON states.match_id = participants.match_id
                   AND states.account_id = participants.account_id
                   AND states.processor_name = %s
                WHERE participants.match_id = raw_payloads.match_id
                  AND (
                        states.match_id IS NULL
                        OR CAST(SUBSTRING_INDEX(states.parser_version, '-v', -1) AS UNSIGNED) < %s
                  )
            )
        """
        params.extend([processor_name, parser_rank])
    params.append(limit)

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT
                raw_payloads.id,
                raw_payloads.match_id,
                raw_payloads.shard,
                raw_payloads.relative_path,
                raw_payloads.compression
            FROM raw_telemetry_payloads raw_payloads
            {state_filter}
            ORDER BY raw_payloads.id ASC
            LIMIT %s
            """,
            tuple(params),
        )
        return list(cursor.fetchall())


def pending_tracked_account_ids(
    connection: Any,
    *,
    match_id: str,
    shard: str,
    processor_name: str,
    parser_version: str,
    force: bool,
) -> set[str]:
    state_filter = ""
    params: list[Any] = [processor_name, match_id, shard]
    if not force:
        state_filter = """
            AND (
                states.match_id IS NULL
                OR CAST(SUBSTRING_INDEX(states.parser_version, '-v', -1) AS UNSIGNED) < %s
            )
        """
        params.append(parser_version_rank(parser_version))

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT DISTINCT players.account_id
            FROM registered_players players
            INNER JOIN match_participants participants
                ON participants.account_id = players.account_id
               AND participants.match_id = %s
            LEFT JOIN player_telemetry_processing_states states
                ON states.match_id = participants.match_id
               AND states.account_id = participants.account_id
               AND states.processor_name = %s
            WHERE players.shard = %s
              {state_filter}
            """,
            _account_query_params(params),
        )
        return {str(row["account_id"]) for row in cursor.fetchall()}


def upsert_processing_states(
    connection: Any,
    *,
    match_id: str,
    account_ids: set[str],
    processor_name: str,
    parser_version: str,
    output_counts: Mapping[str, int] | None = None,
) -> None:
    if not account_ids:
        return

    timestamp = _mysql_datetime(now_kst())
    counts = output_counts or {}
    rows = [
        (
            match_id,
            account_id,
            processor_name,
            parser_version,
            max(0, int(counts.get(account_id, 0))),
            timestamp,
            timestamp,
        )
        for account_id in sorted(account_ids)
    ]
    with connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO player_telemetry_processing_states (
                match_id,
                account_id,
                processor_name,
                parser_version,
                output_count,
                processed_at_kst,
                updated_at_kst
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                output_count = IF(
                    CAST(SUBSTRING_INDEX(VALUES(parser_version), '-v', -1) AS UNSIGNED)
                        >= CAST(SUBSTRING_INDEX(parser_version, '-v', -1) AS UNSIGNED),
                    VALUES(output_count), output_count
                ),
                processed_at_kst = IF(
                    CAST(SUBSTRING_INDEX(VALUES(parser_version), '-v', -1) AS UNSIGNED)
                        >= CAST(SUBSTRING_INDEX(parser_version, '-v', -1) AS UNSIGNED),
                    VALUES(processed_at_kst), processed_at_kst
                ),
                updated_at_kst = IF(
                    CAST(SUBSTRING_INDEX(VALUES(parser_version), '-v', -1) AS UNSIGNED)
                        >= CAST(SUBSTRING_INDEX(parser_version, '-v', -1) AS UNSIGNED),
                    VALUES(updated_at_kst), updated_at_kst
                ),
                parser_version = IF(
                    CAST(SUBSTRING_INDEX(VALUES(parser_version), '-v', -1) AS UNSIGNED)
                        >= CAST(SUBSTRING_INDEX(parser_version, '-v', -1) AS UNSIGNED),
                    VALUES(parser_version), parser_version
                )
            """,
            rows,
        )


def count_outputs_by_account(rows: list[Any], *, account_attribute: str = "account_id") -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        account_id = getattr(row, account_attribute, None)
        if isinstance(account_id, str) and account_id:
            counts[account_id] = counts.get(account_id, 0) + 1
    return counts


def parser_version_rank(parser_version: str) -> int:
    match = re.search(r"-v(\d+)$", str(parser_version or "").strip())
    return int(match.group(1)) if match else 0


def _account_query_params(params: list[Any]) -> tuple[Any, ...]:
    processor_name, match_id, shard, *rest = params
    return (match_id, processor_name, shard, *rest)


def _mysql_datetime(value: datetime) -> datetime:
    return value.replace(tzinfo=None)
