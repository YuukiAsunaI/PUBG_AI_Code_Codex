from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


MetricEvidence = Literal["telemetry", "participant", "derived"]


@dataclass(frozen=True)
class MetricDefinition:
    key: str
    label_ko: str
    category: str
    unit: str
    formula_ko: str
    denominator_ko: str
    source: str
    evidence: MetricEvidence
    caveat_ko: str = ""

    def to_record(self) -> dict[str, str]:
        return asdict(self)


_METRICS: tuple[MetricDefinition, ...] = (
    MetricDefinition(
        "win_rate",
        "치킨율",
        "survival",
        "%",
        "치킨 경기 수 / 집계 경기 수 x 100",
        "선택 필터를 통과한 경기",
        "match_participants.win_place",
        "participant",
    ),
    MetricDefinition(
        "top10_rate",
        "TOP 10 비율",
        "survival",
        "%",
        "10위 이내 경기 수 / 순위가 있는 경기 수 x 100",
        "win_place가 기록된 경기",
        "match_participants.win_place",
        "participant",
    ),
    MetricDefinition(
        "accuracy",
        "명중률",
        "combat",
        "%",
        "명중 횟수 / 일반 탄환 발사 추정 횟수 x 100",
        "전투 파서가 일반 탄환으로 분류한 발사",
        "player_match_combat_summaries",
        "telemetry",
        "샷건 펠릿과 일부 특수 무기는 별도 추정 규칙이 적용됩니다.",
    ),
    MetricDefinition(
        "headshot_hit_rate",
        "헤드샷 명중 비율",
        "combat",
        "%",
        "머리 명중 횟수 / 전체 명중 횟수 x 100",
        "실제로 맞춘 타격만 포함",
        "player_match_combat_summaries",
        "telemetry",
        "빗나간 탄환은 분모에 포함하지 않습니다.",
    ),
    MetricDefinition(
        "fight_win_rate",
        "교전 승리율",
        "combat",
        "%",
        "승리 교전 / 승패 판정이 가능한 교전 x 100",
        "킬 또는 기절 성공, 사망 또는 기절당함으로 판정된 교전",
        "player_fight_outcomes",
        "derived",
        "동일 교전의 후속 마무리와 부활은 교전 판정 규칙에 따라 중복 제거됩니다.",
    ),
    MetricDefinition(
        "avg_damage_dealt",
        "평균 준 피해",
        "combat",
        "damage/match",
        "총 준 피해 / 집계 경기 수",
        "선택 필터를 통과한 경기",
        "player_match_combat_summaries",
        "telemetry",
    ),
    MetricDefinition(
        "damage_ratio",
        "피해 교환비",
        "combat",
        "ratio",
        "총 준 피해 / 총 받은 피해",
        "선택 필터를 통과한 경기의 받은 피해 합계",
        "player_match_combat_summaries",
        "derived",
        "받은 피해가 0이면 분모를 1로 처리합니다.",
    ),
    MetricDefinition(
        "kills_per_10_minutes",
        "10분당 킬",
        "combat",
        "count/10min",
        "총 킬 / 유효 생존시간 x 600초",
        "timeSurvived가 양수인 경기의 생존시간 합계",
        "match_participants.raw_stats.timeSurvived",
        "derived",
        "비정상적으로 큰 생존시간은 해당 매치 duration_seconds로 제한합니다.",
    ),
    MetricDefinition(
        "damage_per_10_minutes",
        "10분당 준 피해",
        "combat",
        "damage/10min",
        "총 준 피해 / 유효 생존시간 x 600초",
        "timeSurvived가 양수인 경기의 생존시간 합계",
        "player_match_combat_summaries + match_participants",
        "derived",
        "비정상적으로 큰 생존시간은 해당 매치 duration_seconds로 제한합니다.",
    ),
    MetricDefinition(
        "fights_per_10_minutes",
        "10분당 교전",
        "combat",
        "count/10min",
        "승패 판정 교전 수 / 유효 생존시간 x 600초",
        "timeSurvived가 양수인 경기의 생존시간 합계",
        "player_fight_outcomes + match_participants",
        "derived",
    ),
    MetricDefinition(
        "damage_per_fight",
        "교전당 준 피해",
        "combat",
        "damage/fight",
        "총 준 피해 / 승패 판정 교전 수",
        "킬·가한 기절·사망·당한 기절로 승패 판정된 교전",
        "player_match_combat_summaries + player_fight_outcomes",
        "derived",
    ),
    MetricDefinition(
        "heal_amount",
        "총 체력 회복량",
        "support",
        "health",
        "모든 LogHeal.healAmount 합계(공식 문서의 healamount도 호환)",
        "행동 파서가 처리된 경기",
        "player_match_activity_summaries",
        "telemetry",
        "아이템 회복과 아이템 코드가 없는 부스트 지속 회복을 모두 포함합니다.",
    ),
    MetricDefinition(
        "item_heal_amount",
        "아이템 체력 회복량",
        "support",
        "health",
        "item.itemId가 있는 LogHeal.healAmount 합계(healamount 호환)",
        "행동 파서가 처리된 경기",
        "player_match_activity_summaries",
        "telemetry",
        "participant 통계의 heals는 사용 횟수이며 실제 회복량과 다릅니다.",
    ),
    MetricDefinition(
        "passive_heal_amount",
        "부스트 지속 회복량",
        "support",
        "health",
        "item.itemId가 없는 LogHeal.healAmount 합계(healamount 호환)",
        "행동 파서가 처리된 경기",
        "player_match_activity_summaries",
        "telemetry",
        "작은 회복 틱이 반복 기록되므로 이벤트 수를 아이템 사용 횟수로 해석하면 안 됩니다.",
    ),
    MetricDefinition(
        "revives_caused",
        "팀원 부활",
        "support",
        "count",
        "추적 플레이어가 reviver인 LogPlayerRevive 수",
        "행동 파서가 처리된 경기",
        "player_match_activity_summaries",
        "telemetry",
    ),
    MetricDefinition(
        "vehicle_distance_m",
        "차량 이동 거리",
        "mobility",
        "m",
        "LogVehicleLeave.rideDistance 합계",
        "행동 파서가 처리된 경기",
        "player_match_activity_summaries",
        "telemetry",
    ),
    MetricDefinition(
        "activity_coverage",
        "행동 분석 커버리지",
        "quality",
        "%",
        "현재 활동 파서 버전 처리 경기 / 원본 텔레메트리가 있는 대상 경기 x 100",
        "원본 텔레메트리가 저장된 선택 경기",
        "player_telemetry_processing_states",
        "derived",
        "100% 미만이면 행동 지표의 0은 실제 0이 아니라 미처리일 수 있습니다.",
    ),
)


def metric_catalog_records(*, category: str | None = None) -> list[dict[str, str]]:
    normalized_category = str(category or "").strip().lower()
    definitions = _METRICS
    if normalized_category:
        definitions = tuple(row for row in definitions if row.category == normalized_category)
    return [definition.to_record() for definition in definitions]


def metric_definition(key: str) -> MetricDefinition | None:
    normalized = str(key or "").strip()
    return next((definition for definition in _METRICS if definition.key == normalized), None)
