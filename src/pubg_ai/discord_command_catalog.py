from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Iterable


COMMAND_NAME_PATTERN = re.compile(r"^[-_\w]{1,32}$", re.UNICODE)


@dataclass(frozen=True)
class DiscordCommandSpec:
    name: str
    aliases: tuple[str, ...]
    permission_group: str | None
    label: str
    description: str
    hybrid: bool = True

    def to_record(self) -> dict[str, object]:
        return asdict(self)


DISCORD_COMMAND_SPECS: tuple[DiscordCommandSpec, ...] = (
    DiscordCommandSpec("배그도움말", ("pubg-help", "pubg-ai"), None, "도움말", "사용 가능한 명령을 확인합니다."),
    DiscordCommandSpec("유저조회", ("pubg-profile",), "profile_read", "등록 유저", "등록된 추적 대상을 조회합니다."),
    DiscordCommandSpec("전적", ("pubg-stats",), "profile_read", "플레이어 전적", "플레이어 누적 전적을 조회합니다."),
    DiscordCommandSpec("종합분석", ("pubg-analysis", "pubg-intelligence"), "profile_read", "종합 분석", "전투·생존·지원·이동·판단 지표를 함께 분석합니다."),
    DiscordCommandSpec("교전", ("pubg-fights", "pubg-fight"), "profile_read", "교전 분석", "교전 승패와 거리 통계를 조회합니다."),
    DiscordCommandSpec("추세", ("pubg-trends", "pubg-trend"), "profile_read", "전적 추세", "기간별 지표 변화를 조회합니다."),
    DiscordCommandSpec("시간대", ("pubg-time",), "profile_read", "시간대 분석", "KST 시간대별 플레이와 성과를 분석합니다."),
    DiscordCommandSpec("무기", ("pubg-weapon",), "profile_read", "무기 분석", "무기별 성능과 파츠를 조회합니다."),
    DiscordCommandSpec("추천", ("pubg-recommend",), "profile_read", "장비 추천", "무기 조합과 파츠 추천을 조회합니다."),
    DiscordCommandSpec("비교", ("pubg-compare",), "profile_read", "상세 비교", "유저·맵·무기·모드별 성과를 같은 기준으로 비교합니다."),
    DiscordCommandSpec("낙하", ("pubg-drop", "pubg-landing"), "profile_read", "낙하 분석", "착지 지역별 표본과 성과를 조회합니다."),
    DiscordCommandSpec("매치", ("pubg-match",), "profile_read", "매치 상세", "최근 매치 상세 기록을 조회합니다."),
    DiscordCommandSpec("매치상세", ("pubg-match-detail",), "profile_read", "전체 매치 상세", "저장된 경기의 모든 참가자와 팀 요약을 조회합니다."),
    DiscordCommandSpec("랭킹", ("pubg-ranking",), "ranking_read", "랭킹", "서버 또는 전체 랭킹을 조회합니다."),
    DiscordCommandSpec("유저등록", ("pubg-register",), "register", "추적 대상 등록", "PUBG 플레이어를 추적 대상으로 등록합니다."),
    DiscordCommandSpec("유저삭제", ("pubg-unregister",), "player_manage", "추적 중지", "추적을 중지하고 기존 데이터는 유지합니다."),
    DiscordCommandSpec("최근스냅샷", ("pubg-replay",), "replay_read", "2D 리플레이", "최근 2D 스냅샷을 조회합니다."),
    DiscordCommandSpec("pubg-settings", (), "settings_write", "수집 설정", "안전한 로컬 수집 설정을 변경합니다."),
    DiscordCommandSpec("pubg-delete-data", (), "admin", "데이터 삭제 검토", "데이터 삭제 검토 요청을 생성합니다."),
    DiscordCommandSpec("pubg-delete-cancel", (), "admin", "삭제 요청 취소", "대기 중인 삭제 요청을 취소합니다."),
    DiscordCommandSpec("pubg-permission", (), "admin", "Discord 권한", "사용자의 권한 그룹을 변경합니다."),
    DiscordCommandSpec("pubg-ranking-scope", ("pubg-guild-scope",), "admin", "랭킹 범위", "길드 랭킹 범위를 변경합니다."),
    DiscordCommandSpec("pubg-alerts", (), "admin", "운영 알림", "현재 운영 알림을 조회합니다."),
    DiscordCommandSpec("pubg-alert-ack", ("pubg-alert-acknowledge",), "admin", "알림 확인", "운영 알림을 확인 처리합니다."),
    DiscordCommandSpec("pubg-alert-snooze", (), "admin", "알림 일시 중지", "운영 알림을 일정 시간 숨깁니다."),
    DiscordCommandSpec("pubg-alert-note", (), "admin", "알림 메모", "운영 알림에 메모를 추가합니다."),
    DiscordCommandSpec("pubg-alert-resolution", ("pubg-alert-resolve",), "admin", "알림 해결 기록", "운영 알림에 해결 기록을 남깁니다."),
    DiscordCommandSpec("pubg-alert-notes", ("pubg-alert-note-list",), "admin", "알림 메모 조회", "운영 알림의 메모 이력을 조회합니다."),
    DiscordCommandSpec("pubg-alert-history", ("pubg-alert-log",), "admin", "알림 이력", "운영 알림 이력을 조회합니다."),
    DiscordCommandSpec(
        "pubg-worker-runs",
        ("pubg-worker-history", "pubg-worker-log"),
        "admin",
        "워커 실행 이력",
        "자동 작업 실행 이력을 조회합니다.",
    ),
    DiscordCommandSpec(
        "pubg-worker-run",
        ("pubg-worker-run-detail", "pubg-worker-detail"),
        "admin",
        "워커 실행 상세",
        "자동 작업 한 건의 상세를 조회합니다.",
    ),
)

RESERVED_COMMAND_GROUPS = frozenset(
    spec.permission_group
    for spec in DISCORD_COMMAND_SPECS
    if spec.permission_group is not None
)

COMMAND_GROUP_LABELS: dict[str, str] = {
    "profile_read": "플레이어 분석 조회",
    "ranking_read": "랭킹 조회",
    "register": "추적 대상 등록",
    "player_manage": "추적 대상 관리",
    "replay_read": "2D 리플레이 조회",
    "settings_write": "수집·공개 설정 관리",
    "admin": "Discord·운영 관리자",
}

_SPEC_BY_NAME = {
    value.casefold(): spec
    for spec in DISCORD_COMMAND_SPECS
    for value in (spec.name, *spec.aliases)
}


def default_command_groups() -> dict[str, list[str]]:
    groups: dict[str, set[str]] = {
        group: set()
        for group in RESERVED_COMMAND_GROUPS
    }
    for spec in DISCORD_COMMAND_SPECS:
        if spec.permission_group is None:
            continue
        groups[spec.permission_group].update((spec.name, *spec.aliases))
    return {
        group: sorted(names)
        for group, names in groups.items()
    }


def command_catalog_records() -> list[dict[str, object]]:
    return [spec.to_record() for spec in DISCORD_COMMAND_SPECS]


def command_group_label(group: str) -> str:
    return COMMAND_GROUP_LABELS.get(group, group)


def canonical_command_name(value: str) -> str | None:
    spec = _SPEC_BY_NAME.get(str(value or "").strip().casefold())
    return spec.name if spec else None


def normalize_command_selection(values: Iterable[str]) -> list[str]:
    normalized: set[str] = set()
    unknown: list[str] = []
    for value in values:
        text = str(value or "").strip()
        canonical = canonical_command_name(text)
        if canonical is None:
            unknown.append(text or "<empty>")
        else:
            normalized.add(canonical)
    if unknown:
        raise ValueError(f"unknown Discord commands: {', '.join(sorted(set(unknown)))}")
    return sorted(normalized)


def validate_custom_alias(alias: str) -> str:
    normalized = str(alias or "").strip()
    if not COMMAND_NAME_PATTERN.fullmatch(normalized):
        raise ValueError("alias must be 1-32 letters, numbers, underscores, or hyphens without spaces.")
    if canonical_command_name(normalized) is not None:
        raise ValueError("alias conflicts with an existing Discord command or built-in alias.")
    return normalized
