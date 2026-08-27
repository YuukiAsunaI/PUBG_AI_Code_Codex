from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pubg_ai.code_translator import CodeTranslator
from pubg_ai.database import SCHEMA_VERSION
from pubg_ai.fight_outcome_processor import FIGHT_OUTCOME_PARSER_VERSION
from pubg_ai.map_snapshot_renderer import RENDERER_VERSION as MAP_SNAPSHOT_RENDERER_VERSION
from pubg_ai.replay_timeline_builder import TIMELINE_RENDERER_VERSION
from pubg_ai.telemetry_combat_processor import PARSER_VERSION as COMBAT_PARSER_VERSION
from pubg_ai.telemetry_activity_processor import PARSER_VERSION as ACTIVITY_PARSER_VERSION
from pubg_ai.telemetry_event_catalog import get_telemetry_event_definition
from pubg_ai.telemetry_item_processor import PARSER_VERSION as ITEM_PARSER_VERSION
from pubg_ai.telemetry_movement_processor import PARSER_VERSION as MOVEMENT_PARSER_VERSION
from pubg_ai.time_utils import isoformat_kst


PROCESSING_GRACE_MINUTES = 15
PROCESSOR_SPECS = (
    ("combat", "전투", COMBAT_PARSER_VERSION),
    ("activity", "행동", ACTIVITY_PARSER_VERSION),
    ("items", "아이템", ITEM_PARSER_VERSION),
    ("movement", "이동·좌표", MOVEMENT_PARSER_VERSION),
)


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
    translation_coverage: dict[str, Any] = field(default_factory=dict)

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
            "translation_coverage": self.translation_coverage,
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
    analysis_raw_matches = _scalar(
        connection,
        """
        SELECT COUNT(DISTINCT raw_payloads.match_id) AS value
        FROM raw_telemetry_payloads raw_payloads
        INNER JOIN analysis_matches matches ON matches.match_id = raw_payloads.match_id
        """,
    )
    excluded_raw_matches = _scalar(
        connection,
        """
        SELECT COUNT(DISTINCT raw_payloads.match_id) AS value
        FROM raw_telemetry_payloads raw_payloads
        INNER JOIN excluded_matches excluded ON excluded.match_id = raw_payloads.match_id
        """,
    )
    eligible = _eligible_player_match_counts(connection, recent=False)
    grace = _eligible_player_match_counts(connection, recent=True)
    parser_versions = _rows(
        connection,
        """
        SELECT states.processor_name, states.parser_version,
               COUNT(*) AS player_matches, COUNT(DISTINCT states.match_id) AS matches
        FROM player_telemetry_processing_states states
        INNER JOIN analysis_matches matches ON matches.match_id = states.match_id
        GROUP BY states.processor_name, states.parser_version
        ORDER BY states.processor_name, states.parser_version
        """,
    )
    processor_coverage = {
        name: _processor_coverage(connection, processor_name=name, parser_version=version)
        for name, _label, version in PROCESSOR_SPECS
    }
    fight_coverage = _fight_outcome_coverage(connection)

    combat_summary_mismatches = _combat_summary_mismatches(connection)
    combat_integrity_mismatches = _combat_integrity_mismatches(connection)
    activity_summary_mismatches = _activity_summary_mismatches(connection)
    heal_mismatches = _heal_mismatches(connection)
    activity_match_count_mismatches = _activity_match_count_mismatches(connection)
    item_event_mismatches = _item_event_mismatches(connection)
    item_summary_mismatches = _item_summary_mismatches(connection)
    item_use_quantity_mismatches = _item_use_quantity_mismatches(connection)
    movement_output_mismatches = _movement_output_mismatches(connection)
    fight_outcome_mismatches = _fight_outcome_mismatches(connection)
    negative_activity_rows = _negative_activity_rows(connection)
    negative_item_rows = _negative_item_rows(connection)
    replay_coverage = _replay_artifact_coverage(connection)
    translation_coverage = _translation_coverage(connection)
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
        FROM player_item_match_stats stats
        INNER JOIN analysis_matches matches ON matches.match_id = stats.match_id
        INNER JOIN player_telemetry_processing_states states
            ON states.match_id = stats.match_id
           AND states.account_id = stats.account_id
           AND states.processor_name = 'items'
           AND states.parser_version = %s
        """,
        (ITEM_PARSER_VERSION,),
    )

    eligible_player_matches = int(eligible.get("player_matches") or 0)
    eligible_matches = int(eligible.get("matches") or 0)
    grace_player_matches = int(grace.get("player_matches") or 0)
    grace_matches = int(grace.get("matches") or 0)
    coverage_detail = (
        f"최근 {PROCESSING_GRACE_MINUTES}분 이내 저장된 {grace_matches}경기·"
        f"{grace_player_matches}개 경기-플레이어는 처리 유예 중이며, "
        f"정책 제외 원본 {excluded_raw_matches}경기는 분석 분모에서 제외합니다."
    )
    checks = [_equal_check("schema_version", "DB 스키마 버전", SCHEMA_VERSION, schema_version)]
    for processor_name, label, _version in PROCESSOR_SPECS:
        coverage = processor_coverage[processor_name]
        checks.extend(
            [
                _equal_check(
                    f"{processor_name}_player_coverage",
                    f"{label} 파서 경기·플레이어 커버리지",
                    eligible_player_matches,
                    int(coverage.get("player_matches") or 0),
                    detail=coverage_detail,
                ),
                _equal_check(
                    f"{processor_name}_match_coverage",
                    f"{label} 파서 경기 커버리지",
                    eligible_matches,
                    int(coverage.get("matches") or 0),
                    detail=coverage_detail,
                ),
            ]
        )
    checks.extend(
        [
            _equal_check(
                "fight_outcome_player_coverage",
                "교전 승패 경기·플레이어 커버리지",
                eligible_player_matches,
                int(fight_coverage.get("player_matches") or 0),
                detail=coverage_detail,
            ),
            _equal_check(
                "fight_outcome_match_coverage",
                "교전 승패 경기 커버리지",
                eligible_matches,
                int(fight_coverage.get("matches") or 0),
                detail=coverage_detail,
            ),
            _zero_check(
                "combat_summary_reconciliation",
                "전투 상태·요약 행 수 일치",
                combat_summary_mismatches,
            ),
            _zero_check(
                "combat_integrity",
                "전투 명중 분해·헤드샷·음수 값 정합성",
                combat_integrity_mismatches,
            ),
            _zero_check(
                "activity_summary_reconciliation",
                "행동 상태·요약·이벤트 행 수 일치",
                activity_summary_mismatches,
            ),
            _zero_check(
                "heal_reconciliation",
                "총 회복 = 아이템 + 부스트 지속 회복",
                heal_mismatches,
            ),
            _zero_check(
                "activity_match_event_reconciliation",
                "경기별 이벤트 카탈로그·플레이어 요약 수 일치",
                activity_match_count_mismatches,
            ),
            _zero_check(
                "item_event_reconciliation",
                "아이템 상태·이벤트 행 수 일치",
                item_event_mismatches,
            ),
            _zero_check(
                "item_summary_reconciliation",
                "아이템 이벤트·항목별 집계 전체 일치",
                item_summary_mismatches,
            ),
            _zero_check(
                "item_use_quantity_reconciliation",
                "아이템 사용 수량 = 사용 이벤트 수",
                item_use_quantity_mismatches,
            ),
            _zero_check(
                "movement_output_reconciliation",
                "이동 상태·좌표·낙하·요약·교전 위치 행 수 일치",
                movement_output_mismatches,
            ),
            _zero_check(
                "fight_outcome_reconciliation",
                "교전 승패 상태·결과 행 수 일치",
                fight_outcome_mismatches,
            ),
            _zero_check(
                "negative_activity_values",
                "행동 요약 음수 값 없음",
                negative_activity_rows,
            ),
            _zero_check(
                "negative_item_values",
                "아이템 요약 음수 값 없음",
                negative_item_rows,
            ),
            _equal_check(
                "map_snapshot_coverage",
                "현재 버전 2D 지도 커버리지",
                int(replay_coverage.get("renderable_player_matches") or 0),
                int(replay_coverage.get("map_snapshot_player_matches") or 0),
                detail=(
                    f"지상 이동 좌표가 없어 생성할 수 없는 "
                    f"{int(replay_coverage.get('unrenderable_player_matches') or 0)}건은 대상에서 제외합니다."
                ),
            ),
            _equal_check(
                "replay_timeline_coverage",
                "현재 버전 2D 타임라인 커버리지",
                int(replay_coverage.get("renderable_player_matches") or 0),
                int(replay_coverage.get("timeline_player_matches") or 0),
                detail=(
                    f"지상 이동 좌표가 없어 생성할 수 없는 "
                    f"{int(replay_coverage.get('unrenderable_player_matches') or 0)}건은 대상에서 제외합니다."
                ),
            ),
            _zero_check(
                "unknown_weapon_codes",
                "무기 코드 번역 누락",
                int(translation_coverage["weapons"]["unknown_distinct_code_count"]),
                detail=_unknown_translation_detail(translation_coverage["weapons"]),
            ),
            _zero_check(
                "unknown_item_codes",
                "아이템 코드 번역 누락",
                int(translation_coverage["items"]["unknown_distinct_code_count"]),
                detail=_unknown_translation_detail(translation_coverage["items"]),
            ),
        ]
    )
    counts = {
        "raw_matches": raw_matches,
        "analysis_raw_matches": analysis_raw_matches,
        "excluded_raw_matches": excluded_raw_matches,
        "eligible_matches": eligible_matches,
        "eligible_player_matches": eligible_player_matches,
        "grace_matches": grace_matches,
        "grace_player_matches": grace_player_matches,
        "latest_raw_fetched_at_kst": _row(
            connection,
            """
            SELECT MAX(raw_payloads.fetched_at_kst) AS value
            FROM raw_telemetry_payloads raw_payloads
            INNER JOIN analysis_matches matches ON matches.match_id = raw_payloads.match_id
            """,
        ).get("value"),
        "latest_match_started_at_kst": _row(
            connection,
            "SELECT MAX(created_at_kst) AS value FROM analysis_matches",
        ).get("value"),
        "fight_outcome_matches": int(fight_coverage.get("matches") or 0),
        "fight_outcome_player_matches": int(fight_coverage.get("player_matches") or 0),
        "activity_event_rows": _scalar(
            connection,
            """
            SELECT COUNT(*) AS value
            FROM player_activity_events events
            INNER JOIN analysis_matches matches ON matches.match_id = events.match_id
            """,
        ),
        "activity_summary_rows": _scalar(
            connection,
            """
            SELECT COUNT(*) AS value
            FROM player_match_activity_summaries summaries
            INNER JOIN analysis_matches matches ON matches.match_id = summaries.match_id
            """,
        ),
        "item_event_rows": _scalar(
            connection,
            """
            SELECT COUNT(*) AS value
            FROM player_item_events events
            INNER JOIN analysis_matches matches ON matches.match_id = events.match_id
            """,
        ),
        "item_summary_rows": _scalar(
            connection,
            """
            SELECT COUNT(*) AS value
            FROM player_item_match_stats stats
            INNER JOIN analysis_matches matches ON matches.match_id = stats.match_id
            """,
        ),
        **{
            f"{processor_name}_matches": int(coverage.get("matches") or 0)
            for processor_name, coverage in processor_coverage.items()
        },
        **{
            f"{processor_name}_player_matches": int(coverage.get("player_matches") or 0)
            for processor_name, coverage in processor_coverage.items()
        },
        **replay_coverage,
    }
    return PlayerIntelligenceAudit(
        generated_at_kst=isoformat_kst(),
        counts=counts,
        parser_versions=parser_versions,
        item_source_totals=item_source_totals,
        event_catalog=event_catalog,
        checks=checks,
        translation_coverage=translation_coverage,
    )


def _eligible_player_match_counts(connection: Any, *, recent: bool) -> dict[str, Any]:
    comparison = ">" if recent else "<="
    return _row(
        connection,
        f"""
        SELECT COUNT(*) AS player_matches, COUNT(DISTINCT match_id) AS matches
        FROM (
            SELECT DISTINCT raw_payloads.match_id, players.account_id
            FROM raw_telemetry_payloads raw_payloads
            INNER JOIN analysis_matches matches
                ON matches.match_id = raw_payloads.match_id
            INNER JOIN match_participants participants
                ON participants.match_id = raw_payloads.match_id
            INNER JOIN registered_players players
                ON players.account_id = participants.account_id
               AND players.shard = raw_payloads.shard
            WHERE raw_payloads.fetched_at_kst {comparison}
                  TIMESTAMPADD(MINUTE, -%s, NOW(6))
        ) eligible
        """,
        (PROCESSING_GRACE_MINUTES,),
    )


def _processor_coverage(
    connection: Any,
    *,
    processor_name: str,
    parser_version: str,
) -> dict[str, Any]:
    return _row(
        connection,
        """
        SELECT COUNT(states.match_id) AS player_matches,
               COUNT(DISTINCT states.match_id) AS matches
        FROM (
            SELECT DISTINCT raw_payloads.match_id, players.account_id
            FROM raw_telemetry_payloads raw_payloads
            INNER JOIN analysis_matches matches
                ON matches.match_id = raw_payloads.match_id
            INNER JOIN match_participants participants
                ON participants.match_id = raw_payloads.match_id
            INNER JOIN registered_players players
                ON players.account_id = participants.account_id
               AND players.shard = raw_payloads.shard
            WHERE raw_payloads.fetched_at_kst <= TIMESTAMPADD(MINUTE, -%s, NOW(6))
        ) eligible
        INNER JOIN player_telemetry_processing_states states
            ON states.match_id = eligible.match_id
           AND states.account_id = eligible.account_id
           AND states.processor_name = %s
           AND states.parser_version = %s
        """,
        (PROCESSING_GRACE_MINUTES, processor_name, parser_version),
    )


def _fight_outcome_coverage(connection: Any) -> dict[str, Any]:
    return _row(
        connection,
        """
        SELECT COUNT(states.match_id) AS player_matches,
               COUNT(DISTINCT states.match_id) AS matches
        FROM (
            SELECT DISTINCT raw_payloads.match_id, players.account_id
            FROM raw_telemetry_payloads raw_payloads
            INNER JOIN analysis_matches matches
                ON matches.match_id = raw_payloads.match_id
            INNER JOIN match_participants participants
                ON participants.match_id = raw_payloads.match_id
            INNER JOIN registered_players players
                ON players.account_id = participants.account_id
               AND players.shard = raw_payloads.shard
            WHERE raw_payloads.fetched_at_kst <= TIMESTAMPADD(MINUTE, -%s, NOW(6))
        ) eligible
        INNER JOIN player_fight_outcome_processing_states states
            ON states.match_id = eligible.match_id
           AND states.account_id = eligible.account_id
           AND states.parser_version = %s
        """,
        (PROCESSING_GRACE_MINUTES, FIGHT_OUTCOME_PARSER_VERSION),
    )


def _combat_summary_mismatches(connection: Any) -> int:
    return _scalar(
        connection,
        """
        SELECT COUNT(*) AS value
        FROM player_telemetry_processing_states states
        INNER JOIN analysis_matches matches ON matches.match_id = states.match_id
        LEFT JOIN player_match_combat_summaries summaries
            ON summaries.match_id = states.match_id
           AND summaries.account_id = states.account_id
        WHERE states.processor_name = 'combat'
          AND states.parser_version = %s
          AND states.output_count <> CASE WHEN summaries.match_id IS NULL THEN 0 ELSE 1 END
        """,
        (COMBAT_PARSER_VERSION,),
    )


def _combat_integrity_mismatches(connection: Any) -> int:
    return _scalar(
        connection,
        """
        SELECT COUNT(*) AS value
        FROM (
            SELECT summaries.id
            FROM player_match_combat_summaries summaries
            INNER JOIN player_telemetry_processing_states states
                ON states.match_id = summaries.match_id
               AND states.account_id = summaries.account_id
               AND states.processor_name = 'combat'
               AND states.parser_version = %s
            INNER JOIN analysis_matches matches ON matches.match_id = states.match_id
            WHERE summaries.shots_fired < 0 OR summaries.shots_hit < 0
               OR summaries.character_hits < 0 OR summaries.vehicle_hits < 0
               OR summaries.shots_hit <> summaries.character_hits + summaries.vehicle_hits
               OR summaries.headshot_hits < 0 OR summaries.headshot_hits > summaries.character_hits
               OR summaries.hits_taken < 0 OR summaries.damage_dealt < 0
               OR summaries.damage_taken < 0 OR summaries.vehicle_damage_dealt < 0
               OR summaries.kills < 0 OR summaries.assists < 0 OR summaries.deaths < 0
               OR summaries.dbnos_caused < 0 OR summaries.dbnos_taken < 0
            UNION ALL
            SELECT weapons.id
            FROM player_weapon_match_stats weapons
            INNER JOIN player_telemetry_processing_states states
                ON states.match_id = weapons.match_id
               AND states.account_id = weapons.account_id
               AND states.processor_name = 'combat'
               AND states.parser_version = %s
            INNER JOIN analysis_matches matches ON matches.match_id = states.match_id
            WHERE weapons.shots_fired < 0 OR weapons.shots_hit < 0
               OR weapons.character_hits < 0 OR weapons.vehicle_hits < 0
               OR weapons.shots_hit <> weapons.character_hits + weapons.vehicle_hits
               OR weapons.headshot_hits < 0 OR weapons.headshot_hits > weapons.character_hits
               OR weapons.hits_taken < 0 OR weapons.damage_dealt < 0
               OR weapons.damage_taken < 0 OR weapons.vehicle_damage_dealt < 0
               OR weapons.kills < 0 OR weapons.assists < 0 OR weapons.deaths < 0
               OR weapons.dbnos < 0 OR weapons.dbnos_taken < 0
        ) invalid_rows
        """,
        (COMBAT_PARSER_VERSION, COMBAT_PARSER_VERSION),
    )


def _movement_output_mismatches(connection: Any) -> int:
    return _scalar(
        connection,
        """
        SELECT COUNT(*) AS value
        FROM player_telemetry_processing_states states
        INNER JOIN analysis_matches matches ON matches.match_id = states.match_id
        LEFT JOIN (
            SELECT match_id, account_id, SUM(row_count) AS output_count
            FROM (
                SELECT match_id, account_id, COUNT(*) AS row_count
                FROM player_position_samples GROUP BY match_id, account_id
                UNION ALL
                SELECT match_id, account_id, COUNT(*) AS row_count
                FROM player_landing_events GROUP BY match_id, account_id
                UNION ALL
                SELECT match_id, account_id, COUNT(*) AS row_count
                FROM player_movement_summaries GROUP BY match_id, account_id
                UNION ALL
                SELECT match_id, account_id, COUNT(*) AS row_count
                FROM player_combat_location_events GROUP BY match_id, account_id
            ) output_rows
            GROUP BY match_id, account_id
        ) outputs
            ON outputs.match_id = states.match_id
           AND outputs.account_id = states.account_id
        WHERE states.processor_name = 'movement'
          AND states.parser_version = %s
          AND states.output_count <> COALESCE(outputs.output_count, 0)
        """,
        (MOVEMENT_PARSER_VERSION,),
    )


def _fight_outcome_mismatches(connection: Any) -> int:
    return _scalar(
        connection,
        """
        SELECT COUNT(*) AS value
        FROM player_fight_outcome_processing_states states
        INNER JOIN analysis_matches matches ON matches.match_id = states.match_id
        LEFT JOIN (
            SELECT match_id, account_id, COUNT(*) AS outcome_count
            FROM player_fight_outcomes
            GROUP BY match_id, account_id
        ) outcomes
            ON outcomes.match_id = states.match_id
           AND outcomes.account_id = states.account_id
        WHERE states.parser_version = %s
          AND states.outcome_count <> COALESCE(outcomes.outcome_count, 0)
        """,
        (FIGHT_OUTCOME_PARSER_VERSION,),
    )


def _replay_artifact_coverage(connection: Any) -> dict[str, Any]:
    coverage = _row(
        connection,
        """
        SELECT
            COUNT(*) AS renderable_player_matches,
            COALESCE(SUM(EXISTS (
                SELECT 1 FROM replay_artifacts artifacts
                WHERE artifacts.match_id = eligible.match_id
                  AND artifacts.account_id = eligible.account_id
                  AND artifacts.artifact_type = 'map_snapshot'
                  AND artifacts.artifact_name = 'player-route'
                  AND artifacts.renderer_version = %s
            )), 0) AS map_snapshot_player_matches,
            COALESCE(SUM(EXISTS (
                SELECT 1 FROM replay_artifacts artifacts
                WHERE artifacts.match_id = eligible.match_id
                  AND artifacts.account_id = eligible.account_id
                  AND artifacts.artifact_type = 'timeline'
                  AND artifacts.artifact_name = 'player-timeline'
                  AND artifacts.renderer_version = %s
            )), 0) AS timeline_player_matches
        FROM (
            SELECT DISTINCT summaries.match_id, summaries.account_id
            FROM player_movement_summaries summaries
            INNER JOIN analysis_matches matches ON matches.match_id = summaries.match_id
            INNER JOIN raw_telemetry_payloads raw_payloads ON raw_payloads.match_id = summaries.match_id
            INNER JOIN registered_players players
                ON players.account_id = summaries.account_id
               AND players.shard = matches.shard
            WHERE raw_payloads.fetched_at_kst <= TIMESTAMPADD(MINUTE, -%s, NOW(6))
              AND EXISTS (
                  SELECT 1
                  FROM player_position_samples positions
                  WHERE positions.match_id = summaries.match_id
                    AND positions.account_id = summaries.account_id
                    AND positions.common_is_game > 0
                    AND NOT (
                        COALESCE(positions.is_in_vehicle, 0) = 1
                        AND COALESCE(positions.z, 0) >= 100000
                    )
              )
        ) eligible
        """,
        (
            MAP_SNAPSHOT_RENDERER_VERSION,
            TIMELINE_RENDERER_VERSION,
            PROCESSING_GRACE_MINUTES,
        ),
    )
    coverage["unrenderable_player_matches"] = _scalar(
        connection,
        """
        SELECT COUNT(*) AS value
        FROM (
            SELECT DISTINCT summaries.match_id, summaries.account_id
            FROM player_movement_summaries summaries
            INNER JOIN analysis_matches matches ON matches.match_id = summaries.match_id
            INNER JOIN raw_telemetry_payloads raw_payloads ON raw_payloads.match_id = summaries.match_id
            INNER JOIN registered_players players
                ON players.account_id = summaries.account_id
               AND players.shard = matches.shard
            WHERE raw_payloads.fetched_at_kst <= TIMESTAMPADD(MINUTE, -%s, NOW(6))
              AND NOT EXISTS (
                  SELECT 1
                  FROM player_position_samples positions
                  WHERE positions.match_id = summaries.match_id
                    AND positions.account_id = summaries.account_id
                    AND positions.common_is_game > 0
                    AND NOT (
                        COALESCE(positions.is_in_vehicle, 0) = 1
                        AND COALESCE(positions.z, 0) >= 100000
                    )
              )
        ) unrenderable
        """,
        (PROCESSING_GRACE_MINUTES,),
    )
    return coverage


def _translation_coverage(connection: Any) -> dict[str, Any]:
    translator = CodeTranslator()
    weapon_rows = _rows(
        connection,
        """
        SELECT stats.weapon_code AS code, COUNT(*) AS row_count
        FROM player_weapon_match_stats stats
        INNER JOIN analysis_matches matches ON matches.match_id = stats.match_id
        INNER JOIN player_telemetry_processing_states states
            ON states.match_id = stats.match_id
           AND states.account_id = stats.account_id
           AND states.processor_name = 'combat'
           AND states.parser_version = %s
        GROUP BY stats.weapon_code
        ORDER BY stats.weapon_code
        """,
        (COMBAT_PARSER_VERSION,),
    )
    item_rows = _rows(
        connection,
        """
        SELECT stats.item_code AS code, COUNT(*) AS row_count
        FROM player_item_match_stats stats
        INNER JOIN analysis_matches matches ON matches.match_id = stats.match_id
        INNER JOIN player_telemetry_processing_states states
            ON states.match_id = stats.match_id
           AND states.account_id = stats.account_id
           AND states.processor_name = 'items'
           AND states.parser_version = %s
        GROUP BY stats.item_code
        ORDER BY stats.item_code
        """,
        (ITEM_PARSER_VERSION,),
    )
    return {
        "weapons": _translation_dimension_coverage(
            weapon_rows,
            translator=translator,
            category="damage_causer",
        ),
        "items": _translation_dimension_coverage(
            item_rows,
            translator=translator,
            category="item",
        ),
    }


def _translation_dimension_coverage(
    rows: list[dict[str, Any]],
    *,
    translator: CodeTranslator,
    category: str,
) -> dict[str, Any]:
    unknown = [
        {"code": str(row.get("code") or ""), "row_count": int(row.get("row_count") or 0)}
        for row in rows
        if not translator.translate(str(row.get("code") or ""), category).known
    ]
    return {
        "distinct_code_count": len(rows),
        "known_distinct_code_count": len(rows) - len(unknown),
        "unknown_distinct_code_count": len(unknown),
        "total_rows": sum(int(row.get("row_count") or 0) for row in rows),
        "unknown_row_count": sum(int(row["row_count"]) for row in unknown),
        "unknown_codes": unknown[:50],
    }


def _unknown_translation_detail(coverage: dict[str, Any]) -> str:
    unknown = list(coverage.get("unknown_codes") or [])
    if not unknown:
        return f"저장된 고유 코드 {int(coverage.get('distinct_code_count') or 0)}개를 모두 번역할 수 있습니다."
    labels = ", ".join(
        f"{row.get('code') or '-'} ({int(row.get('row_count') or 0)}행)"
        for row in unknown
    )
    return f"미번역 코드: {labels}"


def _equal_check(
    key: str,
    label: str,
    expected: int,
    actual: int,
    *,
    detail: str = "",
) -> DataQualityCheck:
    return DataQualityCheck(key, label, expected, actual, expected == actual, detail)


def _zero_check(
    key: str,
    label: str,
    actual: int,
    *,
    detail: str = "",
) -> DataQualityCheck:
    return DataQualityCheck(key, label, 0, actual, actual == 0, detail)


def _activity_summary_mismatches(connection: Any) -> int:
    return _scalar(
        connection,
        """
        SELECT COUNT(*) AS value
        FROM player_telemetry_processing_states states
        INNER JOIN analysis_matches matches ON matches.match_id = states.match_id
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
        INNER JOIN analysis_matches matches ON matches.match_id = states.match_id
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
                SELECT DISTINCT states.match_id
                FROM player_telemetry_processing_states states
                INNER JOIN analysis_matches matches ON matches.match_id = states.match_id
                WHERE states.processor_name = 'activity' AND states.parser_version = %s
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
        INNER JOIN analysis_matches matches ON matches.match_id = states.match_id
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


def _item_summary_mismatches(connection: Any) -> int:
    return _scalar(
        connection,
        """
        SELECT COUNT(*) AS value
        FROM (
            SELECT item_keys.match_id, item_keys.account_id, item_keys.item_code
            FROM (
                SELECT match_id, account_id, item_code
                FROM player_item_match_stats
                UNION
                SELECT match_id, account_id, item_code
                FROM player_item_events
                WHERE item_code IS NOT NULL AND item_code <> ''
            ) item_keys
            INNER JOIN player_telemetry_processing_states states
                ON states.match_id = item_keys.match_id
               AND states.account_id = item_keys.account_id
               AND states.processor_name = 'items'
               AND states.parser_version = %s
            INNER JOIN analysis_matches matches ON matches.match_id = item_keys.match_id
            LEFT JOIN player_item_match_stats stats
                ON stats.match_id = item_keys.match_id
               AND stats.account_id = item_keys.account_id
               AND stats.item_code = item_keys.item_code
            LEFT JOIN (
                SELECT match_id, account_id, item_code,
                       SUM(action IN (
                           'pickup', 'pickup_lootbox', 'pickup_carepackage',
                           'pickup_custom_package', 'pickup_vehicle_trunk'
                       )) AS picked_up_events,
                       SUM(CASE WHEN action IN (
                           'pickup', 'pickup_lootbox', 'pickup_carepackage',
                           'pickup_custom_package', 'pickup_vehicle_trunk'
                       ) THEN CASE WHEN stack_count > 0 THEN stack_count ELSE 1 END ELSE 0 END)
                           AS picked_up_quantity,
                       SUM(action = 'pickup_lootbox') AS loot_box_pickup_events,
                       SUM(action = 'pickup_carepackage') AS carepackage_pickup_events,
                       SUM(action = 'pickup_custom_package') AS custom_package_pickup_events,
                       SUM(action = 'pickup_vehicle_trunk') AS vehicle_trunk_pickup_events,
                       SUM(action = 'put_vehicle_trunk') AS vehicle_trunk_put_events,
                       SUM(action = 'drop') AS dropped_events,
                       SUM(CASE WHEN action = 'drop'
                           THEN CASE WHEN stack_count > 0 THEN stack_count ELSE 1 END
                           ELSE 0 END) AS dropped_quantity,
                       SUM(action = 'use') AS used_events,
                       SUM(action = 'equip') AS equipped_events,
                       SUM(action = 'unequip') AS unequipped_events,
                       SUM(action = 'attach') AS attached_events,
                       SUM(action = 'detach') AS detached_events
                FROM player_item_events
                WHERE item_code IS NOT NULL AND item_code <> ''
                GROUP BY match_id, account_id, item_code
            ) events
                ON events.match_id = item_keys.match_id
               AND events.account_id = item_keys.account_id
               AND events.item_code = item_keys.item_code
            WHERE stats.item_code IS NULL OR events.item_code IS NULL
               OR stats.picked_up_events <> events.picked_up_events
               OR stats.picked_up_quantity <> events.picked_up_quantity
               OR stats.loot_box_pickup_events <> events.loot_box_pickup_events
               OR stats.carepackage_pickup_events <> events.carepackage_pickup_events
               OR stats.custom_package_pickup_events <> events.custom_package_pickup_events
               OR stats.vehicle_trunk_pickup_events <> events.vehicle_trunk_pickup_events
               OR stats.vehicle_trunk_put_events <> events.vehicle_trunk_put_events
               OR stats.dropped_events <> events.dropped_events
               OR stats.dropped_quantity <> events.dropped_quantity
               OR stats.used_events <> events.used_events
               OR stats.used_quantity <> events.used_events
               OR stats.equipped_events <> events.equipped_events
               OR stats.unequipped_events <> events.unequipped_events
               OR stats.attached_events <> events.attached_events
               OR stats.detached_events <> events.detached_events
        ) mismatches
        """,
        (ITEM_PARSER_VERSION,),
    )


def _item_use_quantity_mismatches(connection: Any) -> int:
    return _scalar(
        connection,
        """
        SELECT COUNT(*) AS value
        FROM player_item_match_stats stats
        INNER JOIN player_telemetry_processing_states states
            ON states.match_id = stats.match_id
           AND states.account_id = stats.account_id
           AND states.processor_name = 'items'
           AND states.parser_version = %s
        INNER JOIN analysis_matches matches ON matches.match_id = states.match_id
        WHERE stats.used_quantity <> stats.used_events
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
        INNER JOIN analysis_matches matches ON matches.match_id = states.match_id
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
        FROM player_item_match_stats stats
        INNER JOIN player_telemetry_processing_states states
            ON states.match_id = stats.match_id
           AND states.account_id = stats.account_id
           AND states.processor_name = 'items'
           AND states.parser_version = %s
        INNER JOIN analysis_matches matches ON matches.match_id = states.match_id
        WHERE stats.picked_up_events < 0 OR stats.picked_up_quantity < 0
           OR stats.dropped_events < 0 OR stats.dropped_quantity < 0
           OR stats.used_events < 0 OR stats.used_quantity < 0
           OR stats.equipped_events < 0 OR stats.attached_events < 0
           OR stats.custom_package_pickup_events < 0
           OR stats.vehicle_trunk_pickup_events < 0 OR stats.vehicle_trunk_put_events < 0
        """,
        (ITEM_PARSER_VERSION,),
    )


def _event_catalog_coverage(connection: Any) -> dict[str, Any]:
    rows = _rows(
        connection,
        """
        SELECT event_type, SUM(event_count) AS event_count,
               SUM(tracked_event_count) AS tracked_event_count,
               SUM(normalized_event_count) AS normalized_event_count
        FROM match_telemetry_event_counts counts
        INNER JOIN analysis_matches matches ON matches.match_id = counts.match_id
        WHERE counts.parser_version = %s
        GROUP BY counts.event_type
        ORDER BY counts.event_type
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
