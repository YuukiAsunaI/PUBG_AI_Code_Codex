from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


EventSupport = Literal["normalized", "raw_only", "ignored"]


@dataclass(frozen=True)
class TelemetryEventDefinition:
    event_type: str
    label_ko: str
    domain: str
    support: EventSupport
    note: str

    def to_record(self) -> dict[str, str]:
        return asdict(self)


_NORMALIZED: tuple[TelemetryEventDefinition, ...] = (
    TelemetryEventDefinition("LogHeal", "회복", "support", "normalized", "회복 사용량과 횟수"),
    TelemetryEventDefinition(
        "LogArmorDestroy",
        "방어구 파괴",
        "combat",
        "normalized",
        "가한 파괴와 당한 파괴를 역할별 기록",
    ),
    TelemetryEventDefinition(
        "LogPlayerRevive",
        "부활",
        "support",
        "normalized",
        "부활 실행, 부활 받음, 트라우마 가방 사용",
    ),
    TelemetryEventDefinition(
        "LogCharacterCarry",
        "기절자 운반 상태",
        "support",
        "normalized",
        "공식 이벤트에 제공된 캐릭터별 상태만 기록",
    ),
    TelemetryEventDefinition(
        "LogPlayerUseThrowable",
        "투척물 사용",
        "utility",
        "normalized",
        "사용자와 투척물 코드",
    ),
    TelemetryEventDefinition(
        "LogPlayerUseFlareGun",
        "플레어건 사용",
        "utility",
        "normalized",
        "사용자와 무기 코드",
    ),
    TelemetryEventDefinition("LogVehicleRide", "차량 탑승", "mobility", "normalized", "차량과 좌석"),
    TelemetryEventDefinition(
        "LogVehicleLeave",
        "차량 하차",
        "mobility",
        "normalized",
        "이동 거리와 최고 속도",
    ),
    TelemetryEventDefinition(
        "LogVehicleDamage",
        "차량 피해",
        "vehicle",
        "normalized",
        "차량에 가한 피해량",
    ),
    TelemetryEventDefinition(
        "LogVehicleDestroy",
        "차량 파괴",
        "vehicle",
        "normalized",
        "차량 파괴 횟수",
    ),
    TelemetryEventDefinition(
        "LogWheelDestroy",
        "바퀴 파괴",
        "vehicle",
        "normalized",
        "차량 바퀴 파괴 횟수",
    ),
    TelemetryEventDefinition(
        "LogVaultStart",
        "파쿠르",
        "mobility",
        "normalized",
        "일반 넘기, 렛지 그랩, 차량 위 파쿠르",
    ),
    TelemetryEventDefinition("LogSwimStart", "수영 시작", "mobility", "normalized", "수영 세션 시작"),
    TelemetryEventDefinition(
        "LogSwimEnd",
        "수영 종료",
        "mobility",
        "normalized",
        "수영 거리와 최대 수심",
    ),
    TelemetryEventDefinition(
        "LogObjectInteraction",
        "오브젝트 상호작용",
        "environment",
        "normalized",
        "오브젝트 종류와 상태",
    ),
    TelemetryEventDefinition(
        "LogObjectDestroy",
        "오브젝트 파괴",
        "environment",
        "normalized",
        "오브젝트 종류",
    ),
    TelemetryEventDefinition(
        "LogPlayerDestroyProp",
        "환경물 파괴",
        "environment",
        "normalized",
        "플레이어가 파괴한 환경물",
    ),
    TelemetryEventDefinition(
        "LogPlayerDestroyBreachableWall",
        "파괴 가능 벽 파괴",
        "environment",
        "normalized",
        "플레이어가 파괴한 벽",
    ),
    TelemetryEventDefinition(
        "LogEmergencyPickup",
        "비상 호출",
        "mobility",
        "normalized",
        "호출자와 탑승자",
    ),
    TelemetryEventDefinition(
        "LogPlayerRedeploy",
        "재배치",
        "mobility",
        "normalized",
        "재배치 완료",
    ),
    TelemetryEventDefinition(
        "LogPlayerRedeployBRStart",
        "재배치 시작",
        "mobility",
        "normalized",
        "재배치 시작 이벤트",
    ),
    TelemetryEventDefinition(
        "LogSpecialZoneInCharacters",
        "특수 구역 진입자",
        "environment",
        "normalized",
        "특수 구역에 포함된 추적 플레이어",
    ),
)


_PROJECT_NORMALIZED: tuple[TelemetryEventDefinition, ...] = (
    TelemetryEventDefinition("LogWeaponFireCount", "발사 횟수", "combat", "normalized", "전투 파서의 무기별 발사 수"),
    TelemetryEventDefinition("LogPlayerAttack", "공격", "combat", "normalized", "발사·공격 위치와 무기 통계"),
    TelemetryEventDefinition("LogPlayerTakeDamage", "피해", "combat", "normalized", "준·받은 피해와 부위별 명중"),
    TelemetryEventDefinition("LogPlayerMakeGroggy", "기절", "combat", "normalized", "가한·당한 기절과 교전 결과"),
    TelemetryEventDefinition("LogPlayerKillV2", "킬·사망", "combat", "normalized", "킬·사망·어시스트와 교전 결과"),
    TelemetryEventDefinition("LogItemPickup", "아이템 획득", "loot", "normalized", "일반 획득 이벤트와 수량"),
    TelemetryEventDefinition("LogItemPickupFromLootBox", "루트박스 획득", "loot", "normalized", "루트박스 획득 출처"),
    TelemetryEventDefinition("LogItemPickupFromCarepackage", "보급 획득", "loot", "normalized", "보급 상자 획득 출처"),
    TelemetryEventDefinition("LogItemPickupFromCustomPackage", "커스텀 패키지 획득", "loot", "normalized", "커스텀 패키지 획득 출처"),
    TelemetryEventDefinition("LogItemPickupFromVehicleTrunk", "차량 트렁크 획득", "loot", "normalized", "차량 트렁크에서 꺼낸 아이템"),
    TelemetryEventDefinition("LogItemPutToVehicleTrunk", "차량 트렁크 보관", "loot", "normalized", "차량 트렁크에 넣은 아이템"),
    TelemetryEventDefinition("LogItemDrop", "아이템 버림", "loot", "normalized", "버린 아이템과 수량"),
    TelemetryEventDefinition("LogItemUse", "아이템 사용", "loot", "normalized", "사용한 아이템과 수량"),
    TelemetryEventDefinition("LogItemEquip", "장비 장착", "loadout", "normalized", "장착한 무기·장비"),
    TelemetryEventDefinition("LogItemUnequip", "장비 해제", "loadout", "normalized", "해제한 무기·장비"),
    TelemetryEventDefinition("LogItemAttach", "파츠 장착", "loadout", "normalized", "무기별 장착 파츠"),
    TelemetryEventDefinition("LogItemDetach", "파츠 해제", "loadout", "normalized", "무기별 해제 파츠"),
    TelemetryEventDefinition("LogPlayerPosition", "플레이어 위치", "movement", "normalized", "시간순 이동·차량 상태와 2D 리플레이"),
    TelemetryEventDefinition("LogParachuteLanding", "낙하산 착지", "movement", "normalized", "첫 착지 위치와 낙하 분석"),
    TelemetryEventDefinition("LogCarePackageSpawn", "보급 생성", "map", "normalized", "보급 생성 위치"),
    TelemetryEventDefinition("LogCarePackageLand", "보급 착지", "map", "normalized", "보급 착지 위치"),
    TelemetryEventDefinition("LogGameStatePeriodic", "게임 상태 주기", "map", "normalized", "비행기 동선과 페이즈·안전 구역"),
    TelemetryEventDefinition("LogMatchStart", "매치 시작", "match", "normalized", "교전 모드 판정과 타임라인 기준 시각"),
)


_RAW_ONLY: tuple[TelemetryEventDefinition, ...] = (
    TelemetryEventDefinition(
        "LogEmPickupLiftOff",
        "비상 호출 이륙",
        "mobility",
        "raw_only",
        "전체 비상 호출 이륙 이벤트 보존; 플레이어 역할은 LogEmergencyPickup으로 집계",
    ),
    TelemetryEventDefinition(
        "LogMatchDefinition",
        "매치 정의",
        "match",
        "raw_only",
        "원본 매치 정의 보존; 매치 속성은 match 응답을 기준으로 저장",
    ),
    TelemetryEventDefinition(
        "LogMatchEnd",
        "매치 종료",
        "match",
        "raw_only",
        "원본 종료 이벤트 보존; 결과와 등수는 participant 통계를 기준으로 저장",
    ),
    TelemetryEventDefinition(
        "LogPhaseChange",
        "페이즈 변경",
        "map",
        "raw_only",
        "원본 페이즈 경계 보존; 지도 상태는 LogGameStatePeriodic으로 재구성",
    ),
    TelemetryEventDefinition(
        "LogPlayerCreate",
        "플레이어 생성",
        "participant",
        "raw_only",
        "원본 참가자 생성 이벤트 보존; 참가자 명단은 match roster를 기준으로 저장",
    ),
    TelemetryEventDefinition(
        "LogPlayerLogin",
        "플레이어 접속",
        "session",
        "raw_only",
        "세션 수명주기 원본 보존; 전적 지표에는 사용하지 않음",
    ),
    TelemetryEventDefinition(
        "LogPlayerLogout",
        "플레이어 접속 종료",
        "session",
        "raw_only",
        "세션 수명주기 원본 보존; 전적 지표에는 사용하지 않음",
    ),
    TelemetryEventDefinition(
        "LogItemPackage",
        "아이템 패키지",
        "loot",
        "raw_only",
        "원본 보존, 의미 계약 확정 전 집계 제외",
    ),
)


_CATALOG = {
    definition.event_type: definition
    for definition in (*_NORMALIZED, *_PROJECT_NORMALIZED, *_RAW_ONLY)
}


def get_telemetry_event_definition(event_type: str) -> TelemetryEventDefinition:
    normalized = str(event_type or "").strip() or "(missing)"
    known = _CATALOG.get(normalized)
    if known is not None:
        return known
    return TelemetryEventDefinition(
        normalized,
        normalized,
        "unclassified",
        "raw_only",
        "새 이벤트 또는 아직 의미 계약을 확정하지 않은 이벤트",
    )


def telemetry_event_catalog_records() -> list[dict[str, str]]:
    return [definition.to_record() for definition in sorted(_CATALOG.values(), key=lambda row: row.event_type)]


def normalized_event_types() -> frozenset[str]:
    return frozenset(
        definition.event_type for definition in (*_NORMALIZED, *_PROJECT_NORMALIZED)
    )
