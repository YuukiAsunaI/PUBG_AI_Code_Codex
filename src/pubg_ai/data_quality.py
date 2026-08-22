from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pubg_ai.database import SCHEMA_VERSION
from pubg_ai.telemetry_activity_processor import PARSER_VERSION as ACTIVITY_PARSER_VERSION
from pubg_ai.telemetry_event_catalog import get_telemetry_event_definition
from pubg_ai.telemetry_item_processor import PARSER_VERSION as ITEM_PARSER_VERSION
from pubg_ai.time_utils import isoformat_kst


@dataclass(frozen=True)
class DataQualityCheck:
    key: str
    label_ko: str
    expected: Any
    actual: Any
    passed: bool
    detail_ko: str = ""

    def to_record(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label_ko": self.label_ko,
            "expected": self.expected,
            "actual": self.actual,
            "passed": self.passed,
            "detail_ko": self.detail_ko,
        }


@dataclass(frozen=True)
class PlayerIntelligenceAudit:
    generated_at_kst: str
    counts: dict[str, Any]
    parser_versions: list[dict[str, Any]]
    item_source_totals: dict[str, Any]
    event_catalog: dict[str, Any]
    checks: list[DataQualityCheck]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def to_record(self) -> dict[str, Any]:
        return {
            "generated_at_kst": self.generated_at_kst,
            "passed": self.passed,
            "counts": self.counts,
            "parser_versions": self.parser_versions,
            "item_source_totals": self.item_source_totals,
            "event_catalog": self.event_catalog,
            "checks": [check.to_record() for check in self.checks],
        }


def audit_player_intelligence(connection: Any) -> PlayerIntelligenceAudit:
    schema_version = _scalar(
        connection,
        "SELECT COALESCE(MAX(version), 0) AS value FROM schema_migrations",
    )
    raw_matches = _scalar(
        connection,
        "SELECT COUNT(DISTINCT match_id) AS value FROM raw_telemetry_payloads",
    )
    eligible = _row(
        connection,
        """
        SELECT COUNT(*) AS player_matches, COUNT(DISTINCT match_id) AS matches
        FROM (
            SELECT DISTINCT raw_payloads.match_id, players.account_id
            FROM raw_telemetry_payloads raw_payloads
            INNER JOIN match_participants participants
                ON participants.match_id = raw_payloads.match_id
            INNER JOIN registered_players players
                ON players.account_id = participants.account_id
               AND players.shard = raw_payloads.shard
        ) eligible
        """,
    )
    parser_versions = _rows(
        connection,
        """
        SELECT processor_name, parser_version,
               COUNT(*) AS player_matches, COUNT(DISTINCT match_id) AS matches
        FROM player_telemetry_processing_states
        GROUP BY processor_name, parser_version
        ORDER BY processor_name, parser_version
        """,
    )
    current_states = {
        (str(row["processor_name"]), str(row["parser_version"])): row
        for row in parser_versions
    }
    activity_state = current_states.get(("activity", ACTIVITY_PARSER_VERSION), {})
    item_state = current_states.get(("items", ITEM_PARSER_VERSION), {})
    activity_player_matches = int(activity_state.get("player_matches") or 0)
    activity_matches = int(activity_state.get("matches") or 0)
    item_player_matches = int(item_state.get("player_matches") or 0)
    item_matches = int(item_state.get("matches") or 0)

    activity_summary_mismatches = _activity_summary_mismatches(connection)
    heal_mismatches = _heal_mismatches(connection)
    activity_match_count_mismatches = _activity_match_count_mismatches(connection)
    item_event_mismatches = _item_event_mismatches(connection)
    negative_activity_rows = _negative_activity_rows(connection)
    negative_item_rows = _negative_item_rows(connection)
    event_catalog = _event_catalog_coverage(connection)
    item_source_totals = _row(
        connection,
        """
        SELECT
            COALESCE(SUM(loot_box_pickup_events), 0) AS loot_box_pickups,
            COALESCE(SUM(carepackage_pickup_events), 0) AS carepackage_pickups,
            COALESCE(SUM(custom_package_pickup_events), 0) AS custom_package_pickups,
            COALESCE(SUM(vehicle_trunk_pickup_events), 0) AS vehicle_trunk_pickups,
            COALESCE(SUM(vehicle_trunk_put_events), 0) AS vehicle_trunk_puts
        FROM player_item_match_stats
        """,
    )

    eligible_player_matches = int(eligible.get("player_matches") or 0)
    eligible_matches = int(eligible.get("matches") or 0)
    checks = [
        _equal_check("schema_version", "DB 스키마 버전", SCHEMA_VERSION, schema_version),
        _equal_check(
            "activity_player_coverage",
            "행동 파서 경기·플레이어 커버리지",
            eligible_player_matches,
            activity_player_matches,
        ),
        _equal_check(
            "activity_match_coverage",
            "행동 파서 경기 커버리지",
            eligible_matches,
            activity_matches,
        ),
        _equal_check(
            "item_player_coverage",
            "아이템 파서 경기·플레이어 커버리지",
            eligible_player_matches,
            item_player_matches,
        ),
        _equal_check("item_match_coverage", "아이템 파서 경기 커버리지", eligible_matches, item_matches),
        _zero_check(
            "activity_summary_reconciliation",
            "행동 상태·요약·이벤트 행 수 일치",
            activity_summary_mismatches,
        ),
        _zero_check("heal_reconciliation", "총 회복 = 아이템 + 부스트 지속 회복", heal_mismatches),
        _zero_check(
            "activity_match_event_reconciliation",
            "경기별 이벤트 카탈로그·플레이어 요약 수 일치",
            activity_match_count_mismatches,
        ),
        _zero_check("item_event_reconciliation", "아이템 상태·이벤트 행 수 일치", item_event_mismatches),
        _zero_check("negative_activity_values", "행동 요약 음수 값 없음", negative_activity_rows),
        _zero_check("negative_item_values", "아이템 요약 음수 값 없음", negative_item_rows),
    ]
    counts = {
        "raw_matches": raw_matches,
        "eligible_matches": eligible_matches,
        "eligible_player_matches": eligible_player_matches,
        "activity_matches": activity_matches,
        "activity_player_matches": activity_player_matches,
        "item_matches": item_matches,
        "item_player_matches": item_player_matches,
        "activity_event_rows": _scalar(connection, "SELECT COUNT(*) AS value FROM player_activity_events"),
        "activity_summary_rows": _scalar(
            connection, "SELECT COUNT(*) AS value FROM player_match_activity_summaries"
        ),
        "item_event_rows": _scalar(connection, "SELECT COUNT(*) AS value FROM player_item_events"),
        "item_summary_rows": _scalar(connection, "SELECT COUNT(*) AS value FROM player_item_match_stats"),
    }
    return PlayerIntelligenceAudit(
        generated_at_kst=isoformat_kst(),
        counts=counts,
        parser_versions=parser_versions,
        item_source_totals=item_source_totals,
        event_catalog=event_catalog,
        checks=checks,
    )


def _equal_check(key: str, label: str, expected: int, actual: int) -> DataQualityCheck:
    return DataQualityCheck(key, label, expected, actual, expected == actual)


def _zero_check(key: str, label: str, actual: int) -> DataQualityCheck:
    return DataQualityCheck(key, label, 0, actual, actual == 0)


def _activity_summary_mismatches(connection: Any) -> int:
    return _scalar(
        connection,
        """
        SELECT COUNT(*) AS value
        FROM player_telemetry_processing_states states
        LEFT JOIN player_match_activity_summaries summaries
            ON summaries.match_id = states.match_id
           AND summaries.account_id = states.account_id
        LEFT JOIN (
            SELECT match_id, account_id, COUNT(*) AS event_rows
            FROM player_activity_events
            GROUP BY match_id, account_id
        ) events
            ON events.match_id = states.match_id
           AND events.account_id = states.account_id
        WHERE states.processor_name = 'activity'
          AND states.parser_version = %s
          AND (
                summaries.match_id IS NULL
                OR summaries.normalized_event_count <> states.output_count
                OR COALESCE(events.event_rows, 0) <> states.output_count
          )
        """,
        (ACTIVITY_PARSER_VERSION,),
    )


def _heal_mismatches(connection: Any) -> int:
    return _scalar(
        connection,
        """
        SELECT COUNT(*) AS value
        FROM player_match_activity_summaries summaries
        INNER JOIN player_telemetry_processing_states states
            ON states.match_id = summaries.match_id
           AND states.account_id = summaries.account_id
           AND states.processor_name = 'activity'
           AND states.parser_version = %s
        WHERE summaries.heal_events <> summaries.item_heal_events + summaries.passive_heal_events
           OR ABS(
                summaries.heal_amount
                - summaries.item_heal_amount
                - summaries.passive_heal_amount
              ) > 0.001
        """,
        (ACTIVITY_PARSER_VERSION,),
    )


def _activity_match_count_mismatches(connection: Any) -> int:
    return _scalar(
        connection,
        """
        SELECT COUNT(*) AS value
        FROM (
            SELECT current_matches.match_id,
                   COALESCE(event_totals.normalized_events, 0) AS normalized_events,
                   COALESCE(summary_totals.summary_events, 0) AS summary_events
            FROM (
                SELECT DISTINCT match_id
                FROM player_telemetry_processing_states
                WHERE processor_name = 'activity' AND parser_version = %s
            ) current_matches
            LEFT JOIN (
                SELECT match_id, SUM(normalized_event_count) AS normalized_events
                FROM match_telemetry_event_counts
                WHERE parser_version = %s
                GROUP BY match_id
            ) event_totals ON event_totals.match_id = current_matches.match_id
            LEFT JOIN (
                SELECT states.match_id, SUM(summaries.normalized_event_count) AS summary_events
                FROM player_telemetry_processing_states states
                INNER JOIN player_match_activity_summaries summaries
                    ON summaries.match_id = states.match_id
                   AND summaries.account_id = states.account_id
                WHERE states.processor_name = 'activity' AND states.parser_version = %s
                GROUP BY states.match_id
            ) summary_totals ON summary_totals.match_id = current_matches.match_id
        ) compared
        WHERE normalized_events <> summary_events
        """,
        (ACTIVITY_PARSER_VERSION, ACTIVITY_PARSER_VERSION, ACTIVITY_PARSER_VERSION),
    )


def _item_event_mismatches(connection: Any) -> int:
    return _scalar(
        connection,
        """
        SELECT COUNT(*) AS value
        FROM player_telemetry_processing_states states
        LEFT JOIN (
            SELECT match_id, account_id, COUNT(*) AS event_rows
            FROM player_item_events
            GROUP BY match_id, account_id
        ) events
            ON events.match_id = states.match_id
           AND events.account_id = states.account_id
        WHERE states.processor_name = 'items'
          AND states.parser_version = %s
          AND COALESCE(events.event_rows, 0) <> states.output_count
        """,
        (ITEM_PARSER_VERSION,),
    )


def _negative_activity_rows(connection: Any) -> int:
    return _scalar(
        connection,
        """
        SELECT COUNT(*) AS value
        FROM player_match_activity_summaries summaries
        INNER JOIN player_telemetry_processing_states states
            ON states.match_id = summaries.match_id
           AND states.account_id = summaries.account_id
           AND states.processor_name = 'activity'
           AND states.parser_version = %s
        WHERE summaries.heal_events < 0 OR summaries.heal_amount < 0
           OR summaries.item_heal_events < 0 OR summaries.item_heal_amount < 0
           OR summaries.passive_heal_events < 0 OR summaries.passive_heal_amount < 0
           OR summaries.throwable_uses < 0 OR summaries.revives_caused < 0
           OR summaries.vehicle_distance_m < 0 OR summaries.normalized_event_count < 0
        """,
        (ACTIVITY_PARSER_VERSION,),
    )


def _negative_item_rows(connection: Any) -> int:
    return _scalar(
        connection,
        """
        SELECT COUNT(*) AS value
        FROM player_item_match_stats
        WHERE picked_up_events < 0 OR picked_up_quantity < 0
           OR dropped_events < 0 OR dropped_quantity < 0
           OR used_events < 0 OR used_quantity < 0
           OR equipped_events < 0 OR attached_events < 0
           OR custom_package_pickup_events < 0
           OR vehicle_trunk_pickup_events < 0 OR vehicle_trunk_put_events < 0
        """,
    )


def _event_catalog_coverage(connection: Any) -> dict[str, Any]:
    rows = _rows(
        connection,
        """
        SELECT event_type, SUM(event_count) AS event_count,
               SUM(tracked_event_count) AS tracked_event_count,
               SUM(normalized_event_count) AS normalized_event_count
        FROM match_telemetry_event_counts
        WHERE parser_version = %s
        GROUP BY event_type
        ORDER BY event_type
        """,
        (ACTIVITY_PARSER_VERSION,),
    )
    raw_only: list[dict[str, Any]] = []
    unclassified: list[dict[str, Any]] = []
    normalized_types = 0
    for row in rows:
        event_type = str(row.get("event_type") or "")
        definition = get_telemetry_event_definition(event_type)
        record = {
            **row,
            "label_ko": definition.label_ko,
            "domain": definition.domain,
            "support": definition.support,
        }
        if definition.support == "normalized":
            normalized_types += 1
        else:
            raw_only.append(record)
        if definition.domain == "unclassified":
            unclassified.append(record)
    return {
        "event_type_count": len(rows),
        "normalized_type_count": normalized_types,
        "raw_only_type_count": len(raw_only),
        "unclassified_type_count": len(unclassified),
        "raw_only_types": raw_only,
        "unclassified_types": unclassified,
    }


def _scalar(
    connection: Any,
    query: str,
    params: tuple[Any, ...] = (),
) -> int:
    return int(_row(connection, query, params).get("value") or 0)


def _row(
    connection: Any,
    query: str,
    params: tuple[Any, ...] = (),
) -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        row = cursor.fetchone()
    return {key: _json_value(value) for key, value in dict(row or {}).items()}


def _rows(
    connection: Any,
    query: str,
    params: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        return [
            {key: _json_value(value) for key, value in dict(row).items()}
            for row in cursor.fetchall()
        ]


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value
