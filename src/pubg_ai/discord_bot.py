from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import shlex
from typing import Any, Callable
from urllib.parse import urlencode

from pubg_ai.alert_history import (
    ALERT_HISTORY_EXPORT_LIMIT,
    ALERT_HISTORY_SEVERITIES,
    ALERT_HISTORY_SORTS,
    ALERT_HISTORY_SOURCES,
    ALERT_HISTORY_STATES,
    AlertHistoryError,
    AlertHistoryNote,
    AlertHistoryPage,
    AlertHistoryRecord,
    acknowledge_alert,
    add_alert_note,
    get_alert_history_page,
    get_alert_history_record,
    list_alert_notes,
    mark_alert_notified,
    snooze_alert,
    sync_alert_history,
    visible_alert_records,
)
from pubg_ai.config import RuntimeConfig
from pubg_ai.data_deletion_requests import (
    DataDeletionRequest,
    DataDeletionRequestError,
    DataDeletionRequestService,
    normalize_deletion_scope,
)
from pubg_ai.database import connect_mysql
from pubg_ai.discord_guild_catalog import sync_discord_guild_catalog
from pubg_ai.code_translator import DAMAGE_CAUSER_KO, translate_code
from pubg_ai.discord_command_catalog import DISCORD_COMMAND_SPECS, command_group_label
from pubg_ai.discord_permission_manager import DiscordPermissionManager
from pubg_ai.discord_permissions import DiscordCommandIdentity, DiscordPermissionChecker
from pubg_ai.fight_outcome_stats import FightOutcomeStatsService, PlayerFightOutcomeReport
from pubg_ai.local_settings import CollectorSettings, LocalSettingsError, LocalSettingsStore
from pubg_ai.player_rankings import PlayerRanking, PlayerRankingService
from pubg_ai.player_recommendations import PlayerRecommendationReport, PlayerRecommendationService
from pubg_ai.player_registry import DiscordCommandContext, PlayerRegistry, RegisteredPlayer
from pubg_ai.player_stats import PlayerMatchDetail, PlayerProfileStats, PlayerStatsService, PlayerWeaponDetail
from pubg_ai.player_trends import (
    PlayerTrendFilters,
    PlayerTrendReport,
    PlayerTrendService,
    normalize_trend_granularity,
    parse_optional_bool,
    parse_trend_date,
)
from pubg_ai.pubg_client import PubgApiClient, PubgApiError
from pubg_ai.raw_storage import RawPayloadStore
from pubg_ai.replay_artifact_catalog import ReplayArtifactRecord, list_replay_artifacts
from pubg_ai.replay_storage import ReplayArtifactStore, ReplayStorageError
from pubg_ai.system_alerts import (
    collect_system_alerts,
    current_alerts_url,
    format_alert_report,
    format_discord_alert,
)
from pubg_ai.time_utils import now_kst, to_kst
from pubg_ai.watchlist import WatchlistService, mark_watchlist_alert_notified
from pubg_ai.worker_run_history import (
    WORKER_RUN_EXPORT_LIMIT,
    WORKER_RUN_STATUSES,
    WorkerRunHistoryError,
    WorkerRunPage,
    WorkerRunRecord,
    get_latest_worker_run_id,
    get_worker_run,
    get_worker_run_page,
)
from pubg_ai.weapon_accuracy import is_ballistic_weapon


DEFAULT_DISCORD_PREFIX = "!"
ALERT_HISTORY_PRESETS: dict[str, dict[str, str]] = {
    "current-errors": {
        "source": "all",
        "state": "current",
        "severity": "error",
        "sort": "severity",
        "search": "",
    },
    "worker-failures": {
        "source": "worker",
        "state": "all",
        "severity": "error",
        "sort": "newest",
        "search": "",
    },
    "storage-pressure": {
        "source": "storage",
        "state": "all",
        "severity": "all",
        "sort": "severity",
        "search": "",
    },
    "all-history": {
        "source": "all",
        "state": "all",
        "severity": "all",
        "sort": "newest",
        "search": "",
    },
}

DISCORD_COMMAND_USAGE_KO: dict[str, str] = {
    "배그도움말": "/배그도움말",
    "유저조회": "/유저조회 [닉네임] [플랫폼]",
    "전적": "/전적 닉네임 [플랫폼]",
    "교전": "/교전 닉네임 [플랫폼]",
    "추세": "/추세 닉네임 [옵션]",
    "무기": "/무기 닉네임 무기 [플랫폼]",
    "추천": "/추천 닉네임 [플랫폼]",
    "매치": "/매치 매치_ID [닉네임] [플랫폼]",
    "랭킹": "/랭킹 [지표] [플랫폼] [인원] [범위]",
    "유저등록": "/유저등록 플랫폼 닉네임",
    "유저삭제": "/유저삭제 플랫폼 닉네임",
    "최근스냅샷": "/최근스냅샷 [매치_ID]",
    "pubg-settings": "/pubg-settings [설정 종류] [값]",
    "pubg-delete-data": "/pubg-delete-data 플랫폼 대상 범위 [사유]",
    "pubg-delete-cancel": "/pubg-delete-cancel 요청_ID [사유]",
    "pubg-permission": "/pubg-permission",
    "pubg-ranking-scope": "/pubg-ranking-scope 범위 [서버_ID]",
    "pubg-alerts": "/pubg-alerts",
    "pubg-alert-ack": "/pubg-alert-ack 알림_ID",
    "pubg-alert-snooze": "/pubg-alert-snooze 알림_ID [분]",
    "pubg-alert-note": "/pubg-alert-note 알림_ID 메모",
    "pubg-alert-resolution": "/pubg-alert-resolution 알림_ID 해결 기록",
    "pubg-alert-notes": "/pubg-alert-notes 알림_ID [개수]",
    "pubg-alert-history": "/pubg-alert-history [필터]",
    "pubg-worker-runs": "/pubg-worker-runs [필터]",
    "pubg-worker-run": "/pubg-worker-run 실행_ID",
}


def format_player_list(
    players: list[RegisteredPlayer],
    *,
    detail_base_url: str | None = None,
) -> str:
    if not players:
        lines = ["등록된 유저가 없습니다."]
    else:
        lines = ["등록 유저"]
        for player in players:
            status = "수집중" if player.active else "중지"
            visibility = "공개" if player.public_profile else "비공개"
            lines.append(
                f"- {player.current_name} ({player.shard}) / {status} / {visibility} / {_short_account_id(player.account_id)}"
            )

    local_link = _local_section_url(
        detail_base_url,
        "registered-players",
        _registered_players_query_params(players),
    )
    if local_link:
        lines.append(f"- 로컬 앱에서 관리: [열기]({local_link})")
    return "\n".join(lines)


def _registered_players_query_params(players: list[RegisteredPlayer]) -> dict[str, Any]:
    if len(players) != 1:
        return {}
    player = players[0]
    return {
        "registered_shard": player.shard,
        "registered_account_id": player.account_id,
        "registered_name": player.current_name,
    }


def format_unregister_command_reply(message: str, *, detail_base_url: str | None = None) -> str:
    lines = [message]
    if detail_base_url:
        lines.append(f"- 로컬 앱에서 관리: [열기]({detail_base_url.rstrip('/')}/#registered-players)")
    return "\n".join(lines)


def format_local_section_command_reply(
    message: str,
    section_label: str,
    section_anchor: str,
    *,
    detail_base_url: str | None = None,
    query_params: dict[str, Any] | None = None,
) -> str:
    lines = [message]
    section_url = _local_section_url(detail_base_url, section_anchor, query_params)
    if section_url:
        lines.append(f"- 로컬 앱 상세: [열기]({section_url})")
    return "\n".join(lines)


def format_registered_player_command_reply(
    message: str,
    player: RegisteredPlayer,
    *,
    detail_base_url: str | None = None,
) -> str:
    return format_local_section_command_reply(
        message,
        "local_registered_players",
        "registered-players",
        detail_base_url=detail_base_url,
        query_params=_registered_players_query_params([player]),
    )


def format_discord_permission_command_reply(
    message: str,
    *,
    user_id: str | None = None,
    group: str | None = None,
    guild_id: str | None = None,
    detail_base_url: str | None = None,
) -> str:
    return format_local_section_command_reply(
        message,
        "local_discord_permissions",
        "discord-permissions",
        detail_base_url=detail_base_url,
        query_params={
            "discord_permission_user_id": user_id,
            "discord_permission_group": group,
            "discord_permission_guild_id": guild_id,
        },
    )


def format_discord_permission_change_result(
    *,
    action: str,
    user_id: str,
    group: str,
    guild_id: str | None,
    changed: bool,
    detail_base_url: str | None = None,
) -> str:
    action_label = "부여" if action == "grant" else "회수"
    scope_label = "모든 서버" if guild_id is None else f"서버 {guild_id}"
    result_label = "변경됨" if changed else "이미 적용됨"
    return format_discord_permission_command_reply(
        "\n".join(
            [
                f"Discord 권한 {action_label} 완료",
                f"- 사용자 ID: {user_id}",
                f"- 권한 그룹: {command_group_label(group)} (`{group}`)",
                f"- 적용 범위: {scope_label}",
                f"- 처리 결과: {result_label}",
            ]
        ),
        user_id=user_id,
        group=group,
        guild_id=guild_id,
        detail_base_url=detail_base_url,
    )


def format_discord_scope_command_reply(
    message: str,
    *,
    guild_id: str | None = None,
    ranking_scope: str | None = None,
    detail_base_url: str | None = None,
) -> str:
    return format_local_section_command_reply(
        message,
        "local_discord_scopes",
        "discord-scopes",
        detail_base_url=detail_base_url,
        query_params={
            "discord_scope_guild_id": guild_id,
            "discord_scope_value": ranking_scope,
        },
    )


def format_discord_scope_change_result(
    *,
    guild_id: str,
    ranking_scope: str,
    changed: bool,
    detail_base_url: str | None = None,
) -> str:
    result_label = "변경됨" if changed else "이미 적용됨"
    return format_discord_scope_command_reply(
        "\n".join(
            [
                "Discord 랭킹 범위 저장 완료",
                f"- Discord 서버 ID: {guild_id}",
                f"- 랭킹 범위: {'전체 서버' if ranking_scope == 'global' else '선택 서버'}",
                f"- 처리 결과: {result_label}",
            ]
        ),
        guild_id=guild_id,
        ranking_scope=ranking_scope,
        detail_base_url=detail_base_url,
    )


def format_discord_settings_command_reply(
    message: str,
    *,
    detail_base_url: str | None = None,
) -> str:
    lines = [message]
    for label, anchor in [
        ("수집 설정", "collector-settings"),
        ("저장 경로 설정", "storage-settings"),
        ("Discord 범위 설정", "discord-scopes"),
    ]:
        local_link = _local_section_url(detail_base_url, anchor)
        if local_link:
            lines.append(f"- {label}: [열기]({local_link})")
    return "\n".join(lines)


def format_discord_settings_summary(
    *,
    poll_interval_seconds: int,
    cycle_player_limit: int,
    player_lookup_chunk_size: int,
    raw_compression: str,
    public_profile_default: bool,
    guild_ranking_scope: str | None,
    detail_base_url: str | None = None,
) -> str:
    return format_discord_settings_command_reply(
        "\n".join(
            [
                "PUBG AI 안전 설정",
                f"- 수집 조회 주기: {poll_interval_seconds}초",
                f"- 한 주기 최대 플레이어: {cycle_player_limit}명",
                f"- 플레이어 조회 묶음: {player_lookup_chunk_size}명",
                f"- 원본 압축 방식: {raw_compression}",
                f"- 기본 프로필 공개: {'공개' if public_profile_default else '비공개'}",
                f"- 현재 서버 랭킹 범위: {'전체 서버' if guild_ranking_scope == 'global' else '선택 서버' if guild_ranking_scope else '-'}",
                "- 보안상 숨김: 비밀키, 데이터베이스 접속 정보, 저장 경로",
            ]
        ),
        detail_base_url=detail_base_url,
    )


def format_discord_collector_settings_result(
    *,
    poll_interval_seconds: int,
    cycle_player_limit: int,
    player_lookup_chunk_size: int,
    detail_base_url: str | None = None,
) -> str:
    return format_local_section_command_reply(
        "\n".join(
            [
                "Discord 수집 설정 저장 완료",
                f"- 조회 주기: {poll_interval_seconds}초",
                f"- 한 주기 최대 플레이어: {cycle_player_limit}명",
                f"- API 조회 묶음: {player_lookup_chunk_size}명",
            ]
        ),
        "local_collector_settings",
        "collector-settings",
        detail_base_url=detail_base_url,
        query_params={
            "collector_poll_interval_seconds": poll_interval_seconds,
            "collector_cycle_player_limit": cycle_player_limit,
            "collector_player_lookup_chunk_size": player_lookup_chunk_size,
        },
    )


def format_discord_public_profile_settings_result(
    *,
    public_profile_default: bool,
    detail_base_url: str | None = None,
) -> str:
    value = "공개" if public_profile_default else "비공개"
    return format_local_section_command_reply(
        f"Discord 공개 프로필 기본값 저장 완료\n- 기본 공개 상태: {value}",
        "local_discord_scopes",
        "discord-scopes",
        detail_base_url=detail_base_url,
        query_params={"discord_public_profile_default": str(public_profile_default).lower()},
    )


def format_data_deletion_command_reply(
    message: str,
    *,
    request_id: int | None = None,
    shard: str | None = None,
    target: str | None = None,
    detail_base_url: str | None = None,
) -> str:
    return format_local_section_command_reply(
        message,
        "local_data_deletions",
        "data-deletions",
        detail_base_url=detail_base_url,
        query_params={
            "deletion_request_id": request_id,
            "deletion_shard": shard,
            "deletion_target": target,
        },
    )


def format_data_deletion_request_result(
    request: DataDeletionRequest,
    *,
    action_label: str = "삭제 검토 요청 생성 완료",
    detail_base_url: str | None = None,
) -> str:
    expires_at = to_kst(request.expires_at_kst).isoformat(timespec="seconds")
    return format_data_deletion_command_reply(
        "\n".join(
            [
                action_label,
                f"- 요청 ID: {request.id}",
                f"- 대상: {request.player_name} ({request.shard})",
                f"- 계정 ID: {_short_account_id(request.account_id)}",
                f"- 삭제 범위: {request.deletion_scope}",
                f"- 상태: {_deletion_status_label(request.status)}",
                f"- 만료 시각 (KST): {expires_at}",
                "- 실행 상태: 실제 삭제 미실행",
            ]
        ),
        request_id=request.id,
        shard=request.shard,
        target=request.account_id,
        detail_base_url=detail_base_url,
    )


def _local_section_url(
    detail_base_url: str | None,
    section_anchor: str,
    query_params: dict[str, Any] | None = None,
) -> str:
    if not detail_base_url:
        return ""
    cleaned_params = {
        key: str(value)
        for key, value in (query_params or {}).items()
        if value is not None and str(value) != ""
    }
    query = f"?{urlencode(cleaned_params)}" if cleaned_params else ""
    return f"{detail_base_url.rstrip('/')}/{query}#{section_anchor}"


def _deletion_status_label(value: str) -> str:
    return {
        "pending": "검토 대기",
        "approved": "승인",
        "rejected": "거절",
        "cancelled": "취소",
        "completed": "완료",
        "expired": "만료",
    }.get(str(value or ""), str(value or "-") )


def _alert_source_label(value: str) -> str:
    return {
        "storage": "저장 공간",
        "worker": "자동 작업",
        "watchlist": "밴 플레이어",
        "all": "전체",
    }.get(str(value or ""), str(value or "-") )


def _alert_severity_label(value: str) -> str:
    return {
        "error": "오류",
        "warning": "경고",
        "info": "정보",
        "all": "전체",
    }.get(str(value or ""), str(value or "-") )


def _alert_state_label(value: str) -> str:
    return {
        "current": "현재 발생 중",
        "active": "활성",
        "acknowledged": "확인 완료",
        "snoozed": "일시 숨김",
        "resolved": "해결",
        "all": "전체",
    }.get(str(value or ""), str(value or "-") )


def _alert_sort_label(value: str) -> str:
    return {
        "newest": "최신순",
        "oldest": "오래된순",
        "severity": "심각도순",
    }.get(str(value or ""), str(value or "-") )


def _worker_status_label(value: str) -> str:
    return {
        "running": "실행 중",
        "succeeded": "성공",
        "failed": "실패",
        "cancelled": "취소",
        "all": "전체",
    }.get(str(value or ""), str(value or "-") )


def _worker_name_label(value: str) -> str:
    return {
        "collector": "매치 수집기",
        "post_processing": "후처리기",
        "all": "전체",
    }.get(str(value or ""), str(value or "-") )


def _note_type_label(value: str) -> str:
    return "해결 기록" if value == "resolution" else "메모"


def format_replay_artifact_summary(
    artifact: ReplayArtifactRecord,
    *,
    detail_base_url: str | None = None,
) -> str:
    player = artifact.player_name or _short_account_id(artifact.account_id or "")
    match_id = artifact.match_id
    map_name = translate_code(artifact.map_name, "map") if artifact.map_name else "알 수 없음"
    mode = translate_code(artifact.game_mode, "game_mode") if artifact.game_mode else "-"
    size_kb = artifact.size_bytes / 1024
    lines = [
        f"{player} 최근 2D 스냅샷",
        f"- 매치 ID: {match_id}",
        f"- 맵/모드: {map_name} / {mode}",
        f"- 파일 크기: {size_kb:.1f} KB",
    ]
    local_link = _local_section_url(
        detail_base_url,
        "replay-artifacts",
        {
            "shard": artifact.shard,
            "match_id": artifact.match_id,
            "account_id": artifact.account_id,
            "replay_artifact_id": artifact.id,
        },
    )
    if local_link:
        lines.append(f"- 로컬 앱에서 재생: [열기]({local_link})")
    return "\n".join(lines)


def format_alert_action_result(
    record: AlertHistoryRecord,
    action: str,
    *,
    detail_base_url: str | None = None,
) -> str:
    status = "확인 완료" if action == "acknowledged" else "일시 숨김"
    lines = [
        f"PUBG AI 알림 {status}",
        f"- 알림 ID: {record.id}",
        f"- 제목: {record.title}",
        f"- 출처/심각도: {_alert_source_label(record.source)}/{_alert_severity_label(record.severity)}",
    ]
    detail_link = _alert_history_detail_markdown(record.id, detail_base_url)
    if detail_link:
        lines.append(f"- 로컬 앱 상세: {detail_link}")
    if action == "snoozed" and record.snoozed_until_kst:
        lines.append(f"- 숨김 종료 시각 (KST): {record.snoozed_until_kst}")
    if action == "acknowledged" and record.acknowledged_at_kst:
        lines.append(f"- 확인 시각 (KST): {record.acknowledged_at_kst}")
    return "\n".join(lines)


def format_alert_note_result(note: AlertHistoryNote, *, detail_base_url: str | None = None) -> str:
    label = _note_type_label(note.note_type)
    lines = [
        f"PUBG AI 알림 {label} 저장 완료",
        f"- 알림 ID: {note.alert_history_id}",
        f"- 메모 ID: {note.id}",
        f"- 종류: {label}",
    ]
    detail_link = _alert_history_detail_markdown(note.alert_history_id, detail_base_url)
    if detail_link:
        lines.append(f"- 로컬 앱 상세: {detail_link}")
    if note.created_by:
        lines.append(f"- 작성자: {note.created_by}")
    if note.created_at_kst:
        lines.append(f"- 작성 시각 (KST): {note.created_at_kst}")
    lines.append(f"- 내용: {note.note_text}")
    return "\n".join(lines)


def format_alert_notes_result(
    record: AlertHistoryRecord,
    notes: list[AlertHistoryNote],
    *,
    detail_base_url: str | None = None,
) -> str:
    lines = [
        "PUBG AI 알림 메모 이력",
        f"- 알림 ID: {record.id}",
        f"- 제목: {_discord_single_line(record.title, 120)}",
    ]
    detail_link = _alert_history_detail_markdown(record.id, detail_base_url)
    if detail_link:
        lines.append(f"- 로컬 앱 상세: {detail_link}")
    lines.append(f"- 표시/전체: {len(notes)}/{record.note_count}")
    if not notes:
        lines.append("- 저장된 메모나 해결 기록이 없습니다.")
        return "\n".join(lines)

    for note in notes:
        created_at = note.created_at_kst or "-"
        created_by = note.created_by or "-"
        text = _discord_single_line(note.note_text, 180)
        lines.append(f"- #{note.id} {_note_type_label(note.note_type)} · {created_at} · 작성자 {created_by}: {text}")
    return "\n".join(lines)


def format_alert_command_reply(
    message: str,
    alert_id: int | None = None,
    *,
    detail_base_url: str | None = None,
) -> str:
    lines = [message]
    if alert_id is not None:
        detail_link = _alert_history_detail_markdown(alert_id, detail_base_url)
        if detail_link:
            lines.append(f"- 로컬 앱 상세: {detail_link}")
    return "\n".join(lines)


def format_alerts_command_reply(message: str, *, detail_base_url: str | None = None) -> str:
    lines = [message]
    alerts_link = current_alerts_url(detail_base_url)
    if alerts_link:
        lines.append(f"- 로컬 앱의 현재 알림: [열기]({alerts_link})")
    return "\n".join(lines)


def format_alert_history_result(
    page: AlertHistoryPage,
    *,
    detail_base_url: str | None = None,
    command_prefix: str = DEFAULT_DISCORD_PREFIX,
) -> str:
    lines = [
        "PUBG AI 알림 이력",
        (
            f"- 필터: 출처={_alert_source_label(page.source)} 상태={_alert_state_label(page.state)} "
            f"심각도={_alert_severity_label(page.severity)} 정렬={_alert_sort_label(page.sort)} "
            f"검색={page.search or '-'}"
        ),
        f"- 표시/전체: {len(page.records)}/{page.total} · 시작 위치 {page.offset} · 한 페이지 {page.limit}개",
    ]
    filter_page_link = _alert_history_filter_page_link(page, detail_base_url)
    if filter_page_link:
        lines.append(f"- 같은 필터를 로컬 앱에서 열기: [열기]({filter_page_link})")
    export_link = _alert_history_export_link(page, detail_base_url)
    if export_link:
        lines.append(f"- CSV 내보내기: [다운로드]({export_link})")
    if not page.records:
        lines.append("- 조건에 맞는 알림 이력이 없습니다.")
        return "\n".join(lines)

    for record in page.records:
        state = _alert_history_record_state(record)
        title = _discord_single_line(record.title, 80)
        message = _discord_single_line(record.message, 100)
        last_seen = record.last_seen_at_kst or "-"
        lines.append(
            f"- #{record.id} [{_alert_source_label(record.source)}/{_alert_severity_label(record.severity)}/{_alert_state_label(state)}] {last_seen} "
            f"{title}: {message}{_alert_history_detail_link(record, detail_base_url)}"
        )
    lines.extend(_alert_history_navigation_hints(page, command_prefix=command_prefix))
    return "\n".join(lines)


def format_alert_history_command_reply(
    message: str,
    *,
    source: str = "all",
    state: str = "all",
    severity: str = "all",
    sort: str = "newest",
    search: str = "",
    limit: int = 5,
    offset: int = 0,
    detail_base_url: str | None = None,
) -> str:
    lines = [message]
    page = AlertHistoryPage(
        records=[],
        total=0,
        limit=limit,
        offset=offset,
        source=source,
        state=state,
        severity=severity,
        sort=sort,
        search=search,
    )
    filter_page_link = _alert_history_filter_page_link(page, detail_base_url)
    if filter_page_link:
        lines.append(f"- 같은 필터를 로컬 앱에서 열기: [열기]({filter_page_link})")
    export_link = _alert_history_export_link(page, detail_base_url)
    if export_link:
        lines.append(f"- CSV 내보내기: [다운로드]({export_link})")
    return "\n".join(lines)


def format_worker_run_history_result(
    page: WorkerRunPage,
    *,
    detail_base_url: str | None = None,
    command_prefix: str = DEFAULT_DISCORD_PREFIX,
) -> str:
    lines = [
        "PUBG AI 자동 작업 실행 이력",
        "- 필터: " + " ".join(_worker_run_history_filter_labels(page)),
        f"- 표시/전체: {len(page.records)}/{page.total} · 시작 위치 {page.offset} · 한 페이지 {page.limit}개",
    ]
    filter_page_link = _worker_run_filter_page_link(page, detail_base_url)
    if filter_page_link:
        lines.append(f"- 같은 필터를 로컬 앱에서 열기: [열기]({filter_page_link})")
    export_link = _worker_run_export_link(page, detail_base_url)
    if export_link:
        lines.append(f"- CSV 내보내기: [다운로드]({export_link})")
    if not page.records:
        lines.append("- 조건에 맞는 자동 작업 이력이 없습니다.")
        return "\n".join(lines)

    for run in page.records:
        created_at = run.created_at_kst or run.finished_at_kst or run.started_at_kst or "-"
        duration = _optional_duration_seconds(run.duration_seconds)
        last_error = _discord_single_line(run.last_error or "-", 120)
        lines.append(
            f"- #{run.id} [{_worker_name_label(run.worker_name)}/{_worker_status_label(run.status)}] {created_at} "
            f"소요={duration} 오류={run.error_count} 최근 오류={last_error} "
            f"상세: `{command_prefix}pubg-worker-run {run.id}`{_worker_run_detail_link(run, detail_base_url)}"
        )
    lines.extend(_worker_run_navigation_hints(page, command_prefix=command_prefix))
    return "\n".join(lines)


def format_worker_run_history_command_reply(
    message: str,
    *,
    worker_name: str | None = None,
    status: str = "all",
    limit: int = 5,
    offset: int = 0,
    created_from_kst: str | None = None,
    created_to_kst: str | None = None,
    detail_base_url: str | None = None,
) -> str:
    lines = [message]
    page = WorkerRunPage(
        records=[],
        total=0,
        limit=limit,
        offset=offset,
        worker_name=worker_name,
        status=status,
        created_from_kst=created_from_kst,
        created_to_kst=created_to_kst,
    )
    filter_page_link = _worker_run_filter_page_link(page, detail_base_url)
    if filter_page_link:
        lines.append(f"- 같은 필터를 로컬 앱에서 열기: [열기]({filter_page_link})")
    export_link = _worker_run_export_link(page, detail_base_url)
    if export_link:
        lines.append(f"- CSV 내보내기: [다운로드]({export_link})")
    return "\n".join(lines)


def format_worker_run_detail_result(run: WorkerRunRecord, *, detail_base_url: str | None = None) -> str:
    lines = [
        "PUBG AI 자동 작업 실행 상세",
        f"- 실행 ID: {run.id}",
        f"- 작업/상태: {_worker_name_label(run.worker_name)}/{_worker_status_label(run.status)}",
        f"- 시작 시각 (KST): {run.started_at_kst or '-'}",
        f"- 종료 시각 (KST): {run.finished_at_kst or '-'}",
        f"- 소요 시간/오류: {_optional_duration_seconds(run.duration_seconds)} / {run.error_count}",
        f"- 기록 시각 (KST): {run.created_at_kst or '-'}",
    ]
    detail_link = _worker_run_detail_link(run, detail_base_url).strip()
    if detail_link:
        lines.append(f"- 로컬 앱 상세: {detail_link}")

    metrics = _worker_run_summary_metrics(run.summary)
    if metrics:
        lines.append("- 요약 지표:")
        lines.extend(f"  - {metric}" for metric in metrics)
    else:
        lines.append("- 요약 지표: 없음")

    errors = _worker_run_summary_errors(run.summary)
    lines.append("- 오류:")
    if errors:
        lines.extend(f"  {index}. {_discord_single_line(error, 180)}" for index, error in enumerate(errors, start=1))
    else:
        lines.append("  없음")
    return "\n".join(lines)


def format_worker_run_command_reply(
    message: str,
    run_id: int | None = None,
    *,
    detail_base_url: str | None = None,
) -> str:
    lines = [message]
    if run_id is not None:
        detail_link = _worker_run_detail_markdown(run_id, detail_base_url)
        if detail_link:
            lines.append(f"- 로컬 앱 상세: {detail_link}")
    return "\n".join(lines)


def format_player_profile_stats(profile: PlayerProfileStats, *, detail_base_url: str | None = None) -> str:
    totals = profile.totals
    lines = [
        f"{profile.player.current_name} 전적 ({profile.player.shard})",
        f"- 경기/치킨: {totals.match_count}전 {totals.wins}치킨 ({_percent(totals.win_rate)})",
        f"- K/D/A: {totals.kills}/{totals.deaths}/{totals.assists} · KDA {_number(totals.kda, 2)}",
        f"- 평균 딜/받은 딜: {_number(totals.avg_damage_dealt, 1)} / {_number(totals.avg_damage_taken, 1)}",
        f"- 명중 지표/헤드샷 킬: {_accuracy_breakdown_text(totals.accuracy, totals.accuracy_breakdown)} / {totals.headshot_kills}",
        f"- 평균 생존/이동: {_minutes(totals.avg_survival_seconds)} / {_distance_km(totals.avg_movement_distance_m)}",
    ]

    if profile.top_weapons:
        weapons = [
            f"{weapon.weapon_name} {weapon.kills}킬 {_number(weapon.damage_dealt, 0)}딜"
            for weapon in profile.top_weapons[:3]
        ]
        lines.append(f"- 주무기: {', '.join(weapons)}")

    if profile.recent_matches:
        lines.append("최근 경기")
        for match in profile.recent_matches[:3]:
            rank = f"#{match.win_place}" if match.win_place is not None else "-"
            lines.append(
                f"- {_short_match_id(match.match_id)} {rank} "
                f"{match.kills}킬/{_number(match.damage_dealt, 0)}딜 "
                    f"{translate_code(match.map_name, 'map') if match.map_name else '-'} "
                    f"{translate_code(match.game_mode, 'game_mode') if match.game_mode else '-'}"
            )

    if totals.match_count == 0:
        lines.append("아직 파싱된 전투 요약 데이터가 없습니다.")

    local_link = _local_section_url(
        detail_base_url,
        "profile-lookup",
        {"shard": profile.player.shard, "account_id": profile.player.account_id},
    )
    if local_link:
        lines.append(f"- 로컬 앱 상세 분석: [열기]({local_link})")

    return "\n".join(lines)



def format_player_trends(
    report: PlayerTrendReport,
    *,
    detail_base_url: str | None = None,
) -> str:
    granularity_names = {"hour": "시간대별", "date": "일자별", "week": "주별", "month": "월별"}
    totals = report.totals
    lines = [
        f"{report.player.current_name} KST {granularity_names[report.granularity]} 추세 ({report.player.shard})",
        f"- 합계: {totals.match_count}전 {totals.wins}치킨/{totals.non_wins}비치킨 ({_percent(totals.win_rate)})",
        f"- K/D/A: {totals.kills}/{totals.deaths}/{totals.assists} · KDA {totals.kda:.2f}",
        f"- 평균 딜/받은 딜: {totals.avg_damage_dealt:.1f}/{totals.avg_damage_taken:.1f}",
        f"- 명중 지표: {_accuracy_breakdown_text(totals.accuracy, totals.accuracy_breakdown)}",
        f"- 필터: {_trend_filter_label(report.filters)}",
    ]
    if report.buckets:
        lines.append("최근 구간")
        for bucket in report.buckets[-6:]:
            metrics = bucket.metrics
            lines.append(
                f"- {bucket.period_label}: {metrics.match_count}전 {metrics.wins}치킨 "
                f"{_percent(metrics.win_rate)} · KDA {metrics.kda:.2f} · 평딜 {metrics.avg_damage_dealt:.1f} "
                f"· {_accuracy_breakdown_text(metrics.accuracy, metrics.accuracy_breakdown)}"
            )
    else:
        lines.append("조건에 맞는 완료 경기 데이터가 없습니다.")
    if report.truncated:
        lines.append(f"- 표시 구간: 최근 {len(report.buckets)}/{report.available_bucket_count}개")

    query_params: dict[str, Any] = {
        "shard": report.player.shard,
        "target": report.player.account_id,
        "granularity": report.granularity,
    }
    for key, value in report.filters.to_record().items():
        if value is not None:
            query_params[key] = str(value).lower() if isinstance(value, bool) else value
    local_link = _local_section_url(
        detail_base_url,
        "trend-lookup",
        query_params,
    )
    if local_link:
        lines.append(f"- 로컬 앱 추세 그래프: [열기]({local_link})")
    return "\n".join(lines)


def _trend_filter_label(filters: PlayerTrendFilters) -> str:
    values = filters.to_record()
    labels = {
        "game_mode": "게임 모드",
        "team_mode": "팀 모드",
        "perspective": "시점",
        "match_type": "매치 유형",
        "map_name": "맵",
        "is_custom_match": "커스텀",
        "from_date_kst": "시작일",
        "to_date_kst": "종료일",
    }
    categories = {
        "game_mode": "game_mode",
        "team_mode": "team_mode",
        "perspective": "perspective",
        "match_type": "match_type",
        "map_name": "map",
    }
    selected: list[str] = []
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, bool):
            display_value = "예" if value else "아니요"
        elif key in categories:
            display_value = translate_code(str(value), categories[key])
        else:
            display_value = str(value)
        selected.append(f"{labels[key]}={display_value}")
    return ", ".join(selected) if selected else "전체"


def parse_player_trend_command_options(
    raw_options: str,
) -> tuple[str, str, PlayerTrendFilters, int]:
    granularity = "month"
    shard = "steam"
    values: dict[str, str] = {}
    key_aliases = {
        "period": "granularity",
        "granularity": "granularity",
        "shard": "shard",
        "platform": "shard",
        "mode": "game_mode",
        "game_mode": "game_mode",
        "team": "team_mode",
        "team_mode": "team_mode",
        "view": "perspective",
        "perspective": "perspective",
        "type": "match_type",
        "match_type": "match_type",
        "map": "map_name",
        "map_name": "map_name",
        "custom": "custom",
        "from": "from_date",
        "from_date": "from_date",
        "to": "to_date",
        "to_date": "to_date",
        "limit": "limit",
    }
    for token in shlex.split(raw_options):
        if "=" in token:
            raw_key, value = token.split("=", 1)
            key = key_aliases.get(raw_key.strip().lower())
            if key is None:
                raise ValueError(f"unknown trend filter: {raw_key}")
            values[key] = value.strip()
            continue
        lowered = token.lower()
        if lowered in {"steam", "kakao"}:
            shard = lowered
            continue
        try:
            granularity = normalize_trend_granularity(token)
        except ValueError as exc:
            raise ValueError(f"unknown trend option: {token}") from exc

    if "granularity" in values:
        granularity = normalize_trend_granularity(values["granularity"])
    if "shard" in values:
        shard = values["shard"].lower()
    if shard not in {"steam", "kakao"}:
        raise ValueError("shard must be steam or kakao.")
    try:
        limit = int(values.get("limit", "12"))
    except ValueError as exc:
        raise ValueError("limit must be an integer.") from exc
    if not 1 <= limit <= 24:
        raise ValueError("limit must be between 1 and 24.")
    filters = PlayerTrendFilters(
        game_mode=values.get("game_mode"),
        team_mode=values.get("team_mode"),
        perspective=values.get("perspective"),
        match_type=values.get("match_type"),
        map_name=values.get("map_name"),
        is_custom_match=parse_optional_bool(values.get("custom"), "custom"),
        from_date_kst=parse_trend_date(values.get("from_date"), "from_date"),
        to_date_kst=parse_trend_date(values.get("to_date"), "to_date"),
    ).normalized()
    return granularity, shard, filters, limit


def format_player_fight_outcomes(
    report: PlayerFightOutcomeReport,
    *,
    detail_base_url: str | None = None,
) -> str:
    totals = report.totals
    lines = [
        f"{report.player.current_name} 교전 승패 ({report.player.shard})",
        f"- 전체: {totals.wins}승/{totals.losses}패 ({_percent(totals.fight_win_rate)})",
        f"- 승리: 킬 {totals.kill_wins} / 기절 {totals.dbno_wins}",
        f"- 패배: 사망 {totals.death_losses} / 기절 {totals.dbno_losses}",
        f"- 상대: 사람 {totals.human_opponent_fights} / 봇 {totals.bot_opponent_fights}",
    ]
    if totals.excluded_friendly_fire:
        lines.append(f"- 아군 피해 제외: {totals.excluded_friendly_fire}건")
    if totals.excluded_non_firearm_contexts:
        lines.append(
            f"- 총기 순위 제외: 비총기 장비 {totals.excluded_non_firearm_contexts}건"
        )

    if report.weapons:
        weapons = [
            f"{item.weapon_name} {item.wins}승/{item.losses}패 {_percent(item.fight_win_rate)}"
            for item in report.weapons[:3]
        ]
        lines.append(f"- 무기: {', '.join(weapons)}")

    if report.loadouts:
        lines.append("상위 무기 + 파츠")
        for item in report.loadouts[:3]:
            parts = " + ".join(item.attachment_names) if item.attachment_names else "파츠 없음"
            lines.append(
                f"- {item.weapon_name} + {parts}: "
                f"{item.wins}승/{item.losses}패 {_percent(item.fight_win_rate)}"
            )

    if totals.fight_count == 0:
        lines.append("아직 파싱된 교전 승패 데이터가 없습니다.")

    local_link = _local_section_url(
        detail_base_url,
        "profile-lookup",
        {"shard": report.player.shard, "account_id": report.player.account_id},
    )
    if local_link:
        lines.append(f"- 로컬 앱 교전 분석: [열기]({local_link})")
    return "\n".join(lines)


def format_player_weapon_detail(detail: PlayerWeaponDetail, *, detail_base_url: str | None = None) -> str:
    totals = detail.totals
    character_hits = (
        totals.character_hits
        if totals.character_hits or totals.vehicle_hits
        else totals.shots_hit
    )
    lines = [
        f"{detail.player.current_name} {detail.weapon_name} 무기 통계",
        f"- 사용 경기/치킨: {totals.match_count}전 {totals.wins}치킨 ({_percent(totals.win_rate)})",
        f"- 킬/어시/기절: {totals.kills}/{totals.assists}/{totals.dbnos}",
        f"- 딜/평균 딜: {_number(totals.damage_dealt, 0)} / {_number(totals.avg_damage_dealt, 1)}",
        f"- 명중 지표: {_accuracy_metric_text(totals.accuracy, totals.accuracy_metric)} "
        f"({totals.shots_hit}/{totals.shots_fired})",
        f"- 헤드샷 명중: {_percent(totals.to_record()['headshot_hit_rate'])} "
        f"({totals.headshot_hits}/{character_hits} 캐릭터 명중, 차량 제외)",
        f"- 차량 명중/피해: {totals.vehicle_hits}회 / {_number(totals.vehicle_damage_dealt, 1)}",
        f"- 헤드샷 킬/기절: {totals.headshot_kills}/{totals.headshot_dbnos}",
        f"- 교전 승률: {_percent(totals.fight_win_rate)} "
        f"({totals.fight_wins}승/{totals.fight_losses}패, 경기당 {_number(totals.avg_fights_per_match, 2)}회)",
    ]

    hit_parts = _top_parts(totals.hit_parts)
    if hit_parts:
        lines.append(f"- 맞춘 부위: {hit_parts}")

    if detail.effective_ranges:
        lines.append("- 효율 거리: " + ", ".join(
            f"{item.bucket_label} {_percent(item.observed_win_rate)} "
            f"({item.wins}승/{item.losses}패)"
            for item in detail.effective_ranges[:3]
        ))

    if detail.recent_matches:
        lines.append("최근 사용 경기")
        for match in detail.recent_matches[:3]:
            rank = f"#{match.win_place}" if match.win_place is not None else "-"
            lines.append(
                f"- {_short_match_id(match.match_id)} {rank} "
                f"{match.kills}킬/{match.dbnos}기절/{_number(match.damage_dealt, 0)}딜 "
                f"{_accuracy_metric_text(match.accuracy, match.accuracy_metric)}"
            )

    local_link = _local_section_url(
        detail_base_url,
        "weapon-lookup",
        {"shard": detail.player.shard, "account_id": detail.player.account_id, "weapon": detail.weapon_name},
    )
    if local_link:
        lines.append(f"- 로컬 앱 무기 분석: [열기]({local_link})")

    return "\n".join(lines)


def format_player_match_detail(detail: PlayerMatchDetail, *, detail_base_url: str | None = None) -> str:
    rank = f"#{detail.win_place}" if detail.win_place is not None else "-"
    total_players = _optional_number(detail.total_players)
    human_players = _optional_number(detail.human_players)
    bot_players = _optional_number(detail.bot_players)
    result = "치킨" if detail.is_chicken else "치킨 아님"
    lines = [
        f"{detail.player.current_name} 매치 상세 ({detail.shard})",
        f"- 매치 ID: {detail.match_id}",
        f"- 맵/모드: {translate_code(detail.map_name, 'map') if detail.map_name else '-'} / "
        f"{translate_code(detail.game_mode, 'game_mode') if detail.game_mode else '-'} / "
        f"{translate_code(detail.match_type, 'match_type') if detail.match_type else '-'}",
        f"- 결과/등수: {result} / {rank}",
        f"- 인원: 총 {total_players}명, 사람 {human_players}명, 봇 {bot_players}명",
        f"- K/D/A/기절: {detail.kills}/{detail.deaths}/{detail.assists}/{detail.dbnos_caused}"
        f" (당한 기절 {detail.dbnos_taken})",
        f"- 딜/받은 딜: {_number(detail.damage_dealt, 1)} / {_number(detail.damage_taken, 1)}",
        f"- 공격/피격/명중 지표: {detail.shots_fired}/{detail.shots_hit}/"
        f"{_accuracy_breakdown_text(detail.accuracy, detail.accuracy_breakdown)}",
        f"- 헤드샷 킬/기절: {detail.headshot_kills}/{detail.headshot_dbnos_caused}",
        f"- 생존/이동/낙하: {_optional_minutes(detail.survival_seconds)} / "
        f"{_optional_distance_km(detail.movement_distance_m)} / {_optional_distance_m(detail.landing_distance_m)}",
    ]

    if detail.weapons:
        weapon_lines = []
        for weapon in detail.weapons[:4]:
            weapon_lines.append(
                f"{weapon.weapon_name} {weapon.kills}킬/{weapon.dbnos}기절/"
                f"{_number(weapon.damage_dealt, 0)}딜/"
                f"{_accuracy_metric_text(weapon.accuracy, weapon.accuracy_metric)}"
            )
        lines.append(f"- 사용 무기: {', '.join(weapon_lines)}")

    hit_parts = _top_parts(detail.hit_parts)
    if hit_parts:
        lines.append(f"- 맞춘 부위: {hit_parts}")

    if detail.replay_artifact:
        lines.append(f"- 2D 스냅샷: 생성됨 (`!최근스냅샷 {detail.match_id}`)")

    local_link = _local_section_url(
        detail_base_url,
        "match-lookup",
        {"shard": detail.shard, "account_id": detail.player.account_id, "match_id": detail.match_id},
    )
    if local_link:
        lines.append(f"- 로컬 앱 매치 상세: [열기]({local_link})")

    return "\n".join(lines)


def format_player_ranking(
    ranking: PlayerRanking,
    *,
    detail_base_url: str | None = None,
    limit: int | None = None,
) -> str:
    scope = "전체" if ranking.global_scope else f"서버 {ranking.guild_id}"
    lines = [f"{ranking.metric_label} 랭킹 ({ranking.shard}, {scope})"]
    if not ranking.rows:
        lines.append("- 랭킹 데이터가 없습니다.")
    else:
        for row in ranking.rows:
            lines.append(
                f"- #{row.rank} {row.player.current_name}: {_ranking_score(ranking.metric, row.score)} "
                f"({row.match_count}전 {row.wins}치킨, {row.kills}K/{row.deaths}D/{row.assists}A, "
                f"평딜 {_number(row.avg_damage_dealt, 1)})"
            )

    local_link = _local_section_url(
        detail_base_url,
        "ranking-lookup",
        {
            "ranking_metric": ranking.metric,
            "ranking_shard": ranking.shard,
            "ranking_limit": limit,
            "ranking_guild_id": None if ranking.global_scope else ranking.guild_id,
        },
    )
    if local_link:
        lines.append(f"- 로컬 앱 랭킹: [열기]({local_link})")
    return "\n".join(lines)


def _drop_zone_location_label(item: Any) -> str:
    if item.region_display_name_ko:
        return str(item.region_display_name_ko)
    if item.region_status == "dynamic_map":
        return f"동적 맵 격자 {item.grid_x},{item.grid_y}"
    return f"격자 {item.grid_x},{item.grid_y}"


def format_player_recommendations(
    report: PlayerRecommendationReport,
    *,
    evidence_base_url: str | None = None,
    detail_base_url: str | None = None,
) -> str:
    lines = [
        f"{report.player.current_name} 추천 분석 ({report.player.shard})",
        f"- 최소 표본 경기: {report.min_matches}",
    ]
    if report.weapons:
        lines.append("- 추천 무기: " + ", ".join(
            f"{item.weapon_name} 점수 {_number(item.score, 1)} "
            f"(평균 딜 {_number(item.avg_damage_dealt, 1)}, 승률 {_percent(item.win_rate)}, "
            f"{_accuracy_metric_text(item.accuracy, item.accuracy_metric)}, "
            f"헤드샷 명중 {_percent(item.headshot_hit_rate)}, 교전 승률 {_percent(item.fight_win_rate)})"
            for item in report.weapons[:3]
        ))
    else:
        lines.append("- 추천 무기: 표본 없음")

    if report.loadouts:
        lines.append("- 추천 2주무기와 파츠 계획:")
        for index, item in enumerate(report.loadouts[:3], start=1):
            primary_parts = " / ".join(
                translate_code(part.attachment_code, "item")
                for part in item.primary_attachments
            ) or "호환 파츠 실전 표본 부족"
            secondary_parts = " / ".join(
                translate_code(part.attachment_code, "item")
                for part in item.secondary_attachments
            ) or "호환 파츠 실전 표본 부족"
            primary_plan = item.primary_attachment_plan or {}
            secondary_plan = item.secondary_attachment_plan or {}
            lines.extend(
                [
                    f"  {index}. {item.primary.weapon_name} + {item.secondary.weapon_name} "
                    f"(점수 {_number(item.score, 1)}, {item.inventory_burden.get('summary', '탄약 정보 없음')})",
                    f"     {item.primary.weapon_name}: {primary_parts} "
                    f"· {primary_plan.get('basis', '무기별 파츠 성과 조합')} "
                    f"· 신뢰도 {primary_plan.get('confidence', '낮음')}",
                    f"     {item.secondary.weapon_name}: {secondary_parts} "
                    f"· {secondary_plan.get('basis', '무기별 파츠 성과 조합')} "
                    f"· 신뢰도 {secondary_plan.get('confidence', '낮음')}",
                ]
            )

    if report.attachment_combinations:
        lines.append("- 실전 파츠 조합: " + ", ".join(
            f"{item.weapon_name} + {' + '.join(translate_code(code, 'item') for code in item.attachment_codes)} "
            f"({item.match_count}경기, 승률 {_percent(item.win_rate)})"
            for item in report.attachment_combinations[:3]
        ))

    if report.weapon_attachments:
        lines.append("- 파츠별 개별 성과: " + ", ".join(
            f"{item.weapon_name} + {translate_code(item.attachment_code, 'item')} "
            f"(평균 딜 {_number(item.avg_damage_dealt, 1)}, 승률 {_percent(item.win_rate)})"
            f"{_recommendation_evidence_link(report, item, evidence_base_url)}"
            for item in report.weapon_attachments[:3]
        ))
    else:
        lines.append("- 파츠별 개별 성과: 표본 없음")

    if report.weapon_ranges:
        lines.append("- 성과 발생 거리: " + ", ".join(
            f"{item.weapon_name} {item.bucket_label} "
            f"({item.kills}킬/{item.dbnos}기절)"
            for item in report.weapon_ranges[:3]
        ))
    else:
        lines.append("- 성과 발생 거리: 표본 없음")

    if report.attachments:
        lines.append("- 전체 파츠: " + ", ".join(
            f"{translate_code(item.item_code, 'item')} ({item.attached_events}회 장착)"
            for item in report.attachments[:3]
        ))
    else:
        lines.append("- 전체 파츠: 표본 없음")

    if report.maps:
        lines.append("- 맵: " + ", ".join(
            f"{item.map_name_ko} 승률 {_percent(item.win_rate)}"
            for item in report.maps[:3]
        ))
    else:
        lines.append("- 맵: 표본 없음")

    if report.teammates:
        lines.append("- 팀원: " + ", ".join(
            f"{item.name}{' (등록 유저)' if item.registered else ''} 승률 {_percent(item.win_rate)}"
            for item in report.teammates[:3]
        ))
    else:
        lines.append("- 팀원: 표본 없음")

    if report.drop_zones:
        lines.append("- 낙하 지역: " + ", ".join(
            f"{item.map_name_ko} {_drop_zone_location_label(item)} 승률 {_percent(item.win_rate)}"
            for item in report.drop_zones[:3]
        ))
    else:
        lines.append("- 낙하 지역: 표본 없음")

    local_link = _local_section_url(
        detail_base_url,
        "recommendation-lookup",
        {"shard": report.player.shard, "account_id": report.player.account_id, "min_matches": report.min_matches},
    )
    if local_link:
        lines.append(f"- 로컬 앱 추천 상세: [열기]({local_link})")

    return "\n".join(lines)


def create_discord_bot(
    *,
    config: RuntimeConfig,
    permission_checker: DiscordPermissionChecker,
    scope_settings_store: LocalSettingsStore | None = None,
    command_prefix: str = DEFAULT_DISCORD_PREFIX,
    status_callback: Callable[[str, dict[str, Any]], None] | None = None,
) -> Any:
    import discord
    from discord import app_commands
    from discord.ext import commands

    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix=command_prefix, intents=intents)
    permission_manager = DiscordPermissionManager(scope_settings_store) if scope_settings_store is not None else None
    alert_task_started = False
    alert_last_worker_run_id: int | None = None
    sent_storage_alert_keys: set[str] = set()
    custom_prefix_aliases: dict[str, str] = {}
    application_command_templates: tuple[Any, ...] | None = None
    application_command_sync_lock = asyncio.Lock()

    def notify_status(event: str, **details: Any) -> None:
        if status_callback is None:
            return
        try:
            status_callback(event, details)
        except Exception:
            pass

    def guild_id_for(ctx: Any) -> str | None:
        return str(ctx.guild.id) if ctx.guild else None

    def identity_for(ctx: Any) -> DiscordCommandIdentity:
        return DiscordCommandIdentity(user_id=str(ctx.author.id), guild_id=guild_id_for(ctx))

    def alert_note_creator_for(ctx: Any) -> str:
        guild_id = guild_id_for(ctx) or "dm"
        return f"discord:{guild_id}:{ctx.author.id}"

    def has_global_scope(ctx: Any) -> bool:
        return permission_checker.is_global_admin(identity_for(ctx))

    def sync_custom_prefix_aliases() -> None:
        for alias, target in list(custom_prefix_aliases.items()):
            target_command = bot.get_command(target)
            if target_command is not None and bot.all_commands.get(alias) is target_command:
                bot.all_commands.pop(alias, None)
        custom_prefix_aliases.clear()

        for alias, target in permission_checker.settings.command_aliases.items():
            target_command = bot.get_command(target)
            if target_command is None or bot.all_commands.get(alias) not in (None, target_command):
                continue
            bot.all_commands[alias] = target_command
            custom_prefix_aliases[alias] = target

    def refresh_permission_settings() -> bool:
        if scope_settings_store is None:
            sync_custom_prefix_aliases()
            return True
        try:
            permission_checker.settings = scope_settings_store.load_discord_permission_settings()
        except LocalSettingsError:
            return False
        sync_custom_prefix_aliases()
        return True

    def enabled_commands_for_guild(guild_id: str) -> set[str] | None:
        if scope_settings_store is None:
            return None
        try:
            settings = scope_settings_store.load_discord_bot_settings()
        except LocalSettingsError:
            return None
        configured = settings.guild_enabled_commands.get(guild_id)
        return set(configured) if configured is not None else None

    def prefix_command_is_visible(ctx: Any) -> bool:
        guild_id = guild_id_for(ctx)
        if guild_id is None:
            return True
        enabled = enabled_commands_for_guild(guild_id)
        if enabled is None:
            return True
        command_name = str(getattr(getattr(ctx, "command", None), "name", "") or "")
        return not command_name or command_name in enabled

    def guild_ranking_scope(ctx: Any) -> str:
        guild_id = guild_id_for(ctx)
        if guild_id is None or scope_settings_store is None:
            return "guild"
        try:
            settings = scope_settings_store.load_discord_scope_settings()
        except LocalSettingsError:
            return "guild"
        return settings.guild_ranking_scopes.get(guild_id, "guild")

    def guild_ranking_scope_ids(ctx: Any) -> list[str]:
        guild_id = guild_id_for(ctx)
        if guild_id is None:
            return []
        if scope_settings_store is None:
            return [guild_id]
        try:
            settings = scope_settings_store.load_discord_scope_settings()
        except LocalSettingsError:
            return [guild_id]
        return list(settings.guild_ranking_selected_guild_ids.get(guild_id) or [guild_id])

    def public_profile_default() -> bool:
        if scope_settings_store is None:
            return True
        try:
            settings = scope_settings_store.load_discord_scope_settings()
        except LocalSettingsError:
            return True
        return settings.public_profile_default

    async def require_permission(ctx: Any, command_group: str) -> bool:
        if not refresh_permission_settings():
            await ctx.reply("Discord 권한 설정을 불러오지 못했습니다.", mention_author=False)
            return False
        identity = identity_for(ctx)
        command_name = str(getattr(getattr(ctx, "command", None), "name", "") or "")
        if (
            permission_checker.is_allowed(identity, command_group)
            or permission_checker.is_command_allowed(identity, command_name)
        ):
            return True
        await ctx.reply("이 명령어를 사용할 권한이 없습니다.", mention_author=False)
        return False

    async def require_scoped_guild(ctx: Any) -> str | None:
        guild_id = guild_id_for(ctx)
        if has_global_scope(ctx) or guild_id:
            return guild_id
        await ctx.reply("이 명령어는 디스코드 서버 채널에서 사용해 주세요.", mention_author=False)
        return None

    async def send_alert_to_channel(
        channel_id: str,
        message: str,
        *,
        allowed_guild_ids: set[str] | None = None,
    ) -> bool:
        try:
            numeric_channel_id = int(channel_id)
        except ValueError:
            return False

        try:
            channel = bot.get_channel(numeric_channel_id)
            if channel is None:
                channel = await bot.fetch_channel(numeric_channel_id)
            channel_guild_id = str(getattr(getattr(channel, "guild", None), "id", "") or "")
            if allowed_guild_ids and channel_guild_id not in allowed_guild_ids:
                return False
            await channel.send(message)
            return True
        except Exception as exc:
            print(f"failed to send PUBG AI alert to Discord channel {channel_id}: {exc}")
            return False

    async def dispatch_alerts_once() -> None:
        nonlocal alert_last_worker_run_id
        if scope_settings_store is None:
            return

        try:
            alert_settings = scope_settings_store.load_alert_settings()
        except LocalSettingsError as exc:
            print(f"failed to load PUBG AI alert settings: {exc}")
            return

        connection = connect_mysql(config.database)
        try:
            watchlist = WatchlistService(connection)
            try:
                watchlist.scan_encounters(
                    raw_store=RawPayloadStore(
                        config.app.raw_data_dir,
                        compression=config.app.raw_compression,  # type: ignore[arg-type]
                    ),
                    limit=100,
                )
            except Exception as exc:
                print(f"failed to scan PUBG AI watchlist encounters: {exc}")
            if config.secrets.pubg_api_key:
                try:
                    watchlist.refresh_identities(
                        pubg_client=PubgApiClient(config.secrets.pubg_api_key),
                        force=False,
                        max_age_minutes=60,
                    )
                except Exception as exc:
                    print(f"failed to refresh PUBG AI watchlist identities: {exc}")
            if alert_last_worker_run_id is None:
                alert_last_worker_run_id = get_latest_worker_run_id(connection)
            report = collect_system_alerts(
                config=config,
                connection=connection,
                settings=alert_settings,
                after_worker_run_id=alert_last_worker_run_id,
            )
            active_records = sync_alert_history(connection, report.alerts)
            current_alert_keys = {alert.key for alert in report.alerts}
            visible_records = [
                record
                for record in visible_alert_records(active_records)
                if record.alert_key in current_alert_keys
            ]

            sent_worker_alert = False
            worker_alert_count = 0
            for alert in visible_records:
                if alert.source == "storage" and alert.alert_key in sent_storage_alert_keys:
                    continue
                if alert.source == "worker":
                    worker_alert_count += 1

                sent_alert = False
                metadata = alert.metadata or {}
                selected_channels = [
                    str(value)
                    for value in metadata.get("channel_ids", [])
                    if str(value).strip().isdigit()
                ]
                target_channels = selected_channels or list(
                    alert_settings.discord_channel_ids or []
                )
                allowed_guild_ids = None
                if alert.source == "watchlist" and not selected_channels:
                    scoped_guild_ids = {
                        str(value)
                        for value in metadata.get("guild_ids", [])
                        if str(value).strip()
                    }
                    allowed_guild_ids = scoped_guild_ids or None
                for channel_id in target_channels:
                    sent_alert = await send_alert_to_channel(
                        channel_id,
                        format_discord_alert(alert, detail_base_url=config.app.local_web_base_url),
                        allowed_guild_ids=allowed_guild_ids,
                    ) or sent_alert

                if sent_alert:
                    mark_alert_notified(connection, alert.id)
                    if alert.source == "watchlist":
                        event_id = metadata.get("watchlist_alert_event_id")
                        if event_id is not None:
                            mark_watchlist_alert_notified(connection, int(event_id))
                    if alert.source == "storage":
                        sent_storage_alert_keys.add(alert.alert_key)
                    if alert.source == "worker":
                        sent_worker_alert = True
        finally:
            connection.close()

        if worker_alert_count == 0 or sent_worker_alert:
            alert_last_worker_run_id = report.latest_worker_run_id

    async def alert_loop() -> None:
        await bot.wait_until_ready()
        while not bot.is_closed():
            try:
                await dispatch_alerts_once()
            except Exception as exc:
                print(f"PUBG AI alert loop failed: {exc}")
            await asyncio.sleep(60)

    def sync_known_guilds(guilds: Any) -> None:
        records = [
            {"guild_id": str(guild.id), "name": str(guild.name or "")}
            for guild in guilds
            if getattr(guild, "id", None) is not None
        ]
        managed_guild_ids = {
            str(guild.id)
            for guild in bot.guilds
            if getattr(guild, "id", None) is not None
        }
        managed_guild_ids.update(record["guild_id"] for record in records)
        bot_user_id = str(getattr(bot.user, "id", "") or "")
        if not bot_user_id:
            raise RuntimeError("Discord managed bot identity is unavailable.")
        if scope_settings_store is not None:
            reconciliation = scope_settings_store.reconcile_managed_discord_bot(
                bot_user_id=bot_user_id,
                bot_username=str(bot.user or ""),
                guild_ids=sorted(managed_guild_ids),
            )
            permission_checker.settings = reconciliation.permissions
        connection = connect_mysql(config.database)
        try:
            sync_discord_guild_catalog(connection, records)
        finally:
            connection.close()

    bot.pubg_sync_known_guilds = sync_known_guilds

    def restore_global_application_commands() -> tuple[Any, ...]:
        nonlocal application_command_templates
        if application_command_templates is None:
            application_command_templates = tuple(bot.tree.get_commands())
        bot.tree.clear_commands(guild=None)
        for command in application_command_templates:
            bot.tree.add_command(command, override=True)
        return application_command_templates

    async def sync_application_commands(guild_ids: list[str] | None = None) -> dict[str, int]:
        async with application_command_sync_lock:
            templates = restore_global_application_commands()
            available_names = {str(command.name) for command in templates}
            requested_ids = set(guild_ids or [])
            guilds = [
                guild
                for guild in bot.guilds
                if not requested_ids or str(getattr(guild, "id", "")) in requested_ids
            ]
            found_ids = {str(getattr(guild, "id", "")) for guild in guilds}
            missing_ids = sorted(requested_ids - found_ids)
            if missing_ids:
                raise RuntimeError(
                    "Discord bot is not connected to requested guild(s): " + ", ".join(missing_ids)
                )

            # The application is guild-managed. Purge legacy global commands first;
            # otherwise Discord combines them with the selected guild command list.
            bot.tree.clear_commands(guild=None)
            await bot.tree.sync()

            synced_by_guild: dict[str, int] = {}
            for guild in guilds:
                guild_id = str(guild.id)
                restore_global_application_commands()
                bot.tree.clear_commands(guild=guild)
                bot.tree.copy_global_to(guild=guild)
                enabled = enabled_commands_for_guild(guild_id)
                if enabled is not None:
                    for command_name in sorted(available_names - enabled):
                        bot.tree.remove_command(command_name, guild=guild)
                synced = await bot.tree.sync(guild=guild)
                synced_by_guild[guild_id] = len(synced)

            bot.tree.clear_commands(guild=None)
            notify_status(
                "commands_synced",
                guild_command_counts=synced_by_guild,
                available_command_count=len(DISCORD_COMMAND_SPECS),
            )
            return synced_by_guild

    bot.pubg_sync_application_commands = sync_application_commands

    async def fetch_application_commands(guild_ids: list[str] | None = None) -> dict[str, list[str]]:
        requested_ids = set(guild_ids or [])
        guilds = [
            guild
            for guild in bot.guilds
            if not requested_ids or str(getattr(guild, "id", "")) in requested_ids
        ]
        found_ids = {str(getattr(guild, "id", "")) for guild in guilds}
        missing_ids = sorted(requested_ids - found_ids)
        if missing_ids:
            raise RuntimeError(
                "Discord bot is not connected to requested guild(s): " + ", ".join(missing_ids)
            )
        return {
            str(guild.id): sorted(
                str(command.name)
                for command in await bot.tree.fetch_commands(guild=guild)
            )
            for guild in guilds
        }

    bot.pubg_fetch_application_commands = fetch_application_commands

    async def fetch_application_command_exposure(guild_id: str) -> dict[str, list[str]]:
        requested_id = str(guild_id or "").strip()
        guild = next(
            (
                item
                for item in bot.guilds
                if str(getattr(item, "id", "")) == requested_id
            ),
            None,
        )
        if guild is None:
            raise RuntimeError(f"Discord bot is not connected to requested guild: {requested_id}")
        global_names = sorted(
            str(command.name) for command in await bot.tree.fetch_commands()
        )
        guild_names = sorted(
            str(command.name) for command in await bot.tree.fetch_commands(guild=guild)
        )
        return {
            "global_commands": global_names,
            "guild_commands": guild_names,
            "visible_commands": sorted(set(global_names) | set(guild_names)),
        }

    bot.pubg_fetch_application_command_exposure = fetch_application_command_exposure

    @bot.event
    async def on_ready() -> None:
        nonlocal alert_task_started
        print(f"PUBG AI Discord bot logged in as {bot.user}")
        refresh_permission_settings()
        try:
            sync_known_guilds(bot.guilds)
        except Exception as exc:
            print(f"failed to sync Discord guild catalog: {exc}")
        try:
            synced = await sync_application_commands()
            print(f"synced Discord application commands for {len(synced)} guilds")
        except Exception as exc:
            print(f"failed to sync Discord application commands: {exc}")
        if scope_settings_store is not None and not alert_task_started:
            alert_task_started = True
            bot.loop.create_task(alert_loop())
        notify_status(
            "ready",
            bot_user=str(bot.user or ""),
            bot_user_id=str(getattr(bot.user, "id", "") or ""),
            guild_count=len(bot.guilds),
        )

    @bot.event
    async def on_guild_join(guild: Any) -> None:
        try:
            sync_known_guilds([guild])
            await sync_application_commands([str(guild.id)])
        except Exception as exc:
            print(f"failed to add Discord guild to catalog: {exc}")

    @bot.event
    async def on_guild_update(_before: Any, after: Any) -> None:
        try:
            sync_known_guilds([after])
        except Exception as exc:
            print(f"failed to update Discord guild catalog: {exc}")

    @bot.event
    async def on_guild_remove(_guild: Any) -> None:
        try:
            sync_known_guilds([])
        except Exception as exc:
            print(f"failed to update Discord guild membership: {exc}")

    @bot.event
    async def on_message(message: Any) -> None:
        refresh_permission_settings()
        ctx = await bot.get_context(message)
        if not prefix_command_is_visible(ctx):
            return
        await bot.process_commands(message)

    @bot.event
    async def on_disconnect() -> None:
        notify_status("disconnected")

    @bot.event
    async def on_resumed() -> None:
        try:
            await sync_application_commands()
        except Exception as exc:
            print(f"failed to resync Discord application commands after resume: {exc}")
        notify_status("resumed", guild_count=len(bot.guilds))

    class DiscordHelpView(discord.ui.View):
        CATEGORY_LABELS = {
            "all": "전체 명령",
            "guide": "안내",
            "profile_read": "플레이어 분석",
            "ranking_read": "랭킹",
            "management": "추적 대상 관리",
            "replay_read": "2D 리플레이",
            "settings_write": "수집 설정",
            "admin": "운영 관리",
        }

        def __init__(self, *, owner_id: int, guild_id: str | None) -> None:
            super().__init__(timeout=600.0)
            self.owner_id = owner_id
            enabled = enabled_commands_for_guild(guild_id) if guild_id else None
            self.specs = [
                spec
                for spec in DISCORD_COMMAND_SPECS
                if enabled is None or spec.name in enabled
            ]
            self.category = "all"
            self.selected_command: str | None = None
            self.rebuild()

        @staticmethod
        def category_for(spec: Any) -> str:
            if spec.permission_group is None:
                return "guide"
            if spec.permission_group in {"register", "player_manage"}:
                return "management"
            return spec.permission_group

        def filtered_specs(self) -> list[Any]:
            if self.category == "all":
                return list(self.specs)
            return [spec for spec in self.specs if self.category_for(spec) == self.category]

        def selected_spec(self) -> Any | None:
            return next(
                (spec for spec in self.specs if spec.name == self.selected_command),
                None,
            )

        def make_embed(self) -> Any:
            selected = self.selected_spec()
            if selected is not None:
                permission = (
                    command_group_label(selected.permission_group)
                    if selected.permission_group
                    else "권한 없이 사용 가능"
                )
                aliases = ", ".join(f"`{value}`" for value in selected.aliases) or "없음"
                description = (
                    f"{selected.description}\n\n"
                    f"**슬래시 사용법**\n`{DISCORD_COMMAND_USAGE_KO.get(selected.name, '/' + selected.name)}`\n\n"
                    f"**필요 권한**\n{permission}\n\n"
                    f"**접두사 별칭**\n{aliases}\n\n"
                    "닉네임 입력란이 있는 슬래시 명령은 현재 서버의 등록 유저를 자동완성으로 보여줍니다."
                )
                title = f"PUBG AI · {selected.label}"
            else:
                rows = self.filtered_specs()
                command_lines = [
                    f"`/{spec.name}` · **{spec.label}** · {spec.description}"
                    for spec in rows
                ]
                description = (
                    "위 메뉴에서 분류를 고르고, 아래 메뉴에서 명령을 선택하면 사용법과 권한을 확인할 수 있습니다.\n\n"
                    + ("\n".join(command_lines) if command_lines else "이 분류에 공개된 명령이 없습니다.")
                )
                title = f"PUBG AI 명령어 · {self.CATEGORY_LABELS[self.category]}"
            embed = discord.Embed(title=title, description=description[:4096], colour=0x42D3AA)
            embed.set_footer(text=f"현재 서버에 공개된 명령 {len(self.specs)}개 · 이 화면은 실행한 사용자만 조작할 수 있습니다.")
            return embed

        def rebuild(self) -> None:
            self.clear_items()
            categories = ["all"] + [
                key
                for key in self.CATEGORY_LABELS
                if key != "all" and any(self.category_for(spec) == key for spec in self.specs)
            ]
            category_select = discord.ui.Select(
                placeholder="명령 분류를 선택하세요",
                min_values=1,
                max_values=1,
                options=[
                    discord.SelectOption(
                        label=self.CATEGORY_LABELS[key],
                        value=key,
                        description=f"{sum(1 for spec in self.specs if key == 'all' or self.category_for(spec) == key)}개 명령",
                        default=self.category == key,
                    )
                    for key in categories
                ],
                row=0,
            )

            async def category_callback(interaction: Any) -> None:
                self.category = category_select.values[0]
                self.selected_command = None
                self.rebuild()
                await interaction.response.edit_message(embed=self.make_embed(), view=self)

            category_select.callback = category_callback
            self.add_item(category_select)

            rows = self.filtered_specs()
            if rows:
                command_select = discord.ui.Select(
                    placeholder="설명을 확인할 명령을 선택하세요",
                    min_values=1,
                    max_values=1,
                    options=[
                        discord.SelectOption(
                            label=f"{spec.label} · /{spec.name}"[:100],
                            value=spec.name,
                            description=spec.description[:100],
                            default=spec.name == self.selected_command,
                        )
                        for spec in rows[:25]
                    ],
                    row=1,
                )

                async def command_callback(interaction: Any) -> None:
                    self.selected_command = command_select.values[0]
                    self.rebuild()
                    await interaction.response.edit_message(embed=self.make_embed(), view=self)

                command_select.callback = command_callback
                self.add_item(command_select)

            close_button = discord.ui.Button(label="닫기", style=discord.ButtonStyle.danger, row=2)

            async def close_callback(interaction: Any) -> None:
                for item in self.children:
                    item.disabled = True
                await interaction.response.edit_message(embed=self.make_embed(), view=self)
                self.stop()

            close_button.callback = close_callback
            self.add_item(close_button)

        async def interaction_check(self, interaction: Any) -> bool:
            if interaction.user.id == self.owner_id:
                return True
            await interaction.response.send_message(
                "이 도움말 화면은 명령을 실행한 사용자만 조작할 수 있습니다.",
                ephemeral=True,
            )
            return False

    @bot.hybrid_command(name="배그도움말", aliases=["pubg-help", "pubg-ai"])
    async def help_command(ctx: Any) -> None:
        if getattr(ctx, "interaction", None) is not None:
            view = DiscordHelpView(owner_id=int(ctx.author.id), guild_id=guild_id_for(ctx))
            await ctx.reply(
                embed=view.make_embed(),
                view=view,
                ephemeral=True,
                mention_author=False,
            )
            return
        await ctx.reply(
            "\n".join(
                [
                    "PUBG AI 명령어",
                    f"- `{command_prefix}유저등록 steam 닉네임`",
                    f"- `{command_prefix}유저조회 [닉네임] [shard]`",
                    f"- `{command_prefix}전적 닉네임 [shard]`",
                    f"- `{command_prefix}교전 닉네임 [shard]`",
                    f"- `{command_prefix}추세 닉네임 [hour|date|week|month] [shard] [filters]`",
                    f"- `{command_prefix}무기 닉네임 무기명 [shard]`",
                    f"- `{command_prefix}추천 닉네임 [shard]`",
                    f"- `{command_prefix}매치 match_id [닉네임|accountId] [shard]`",
                    f"- `{command_prefix}랭킹 [지표] [shard] [limit] [전체]`",
                    f"- `{command_prefix}최근스냅샷 [match_id]`",
                    f"- `{command_prefix}pubg-alerts`",
                    f"- `{command_prefix}pubg-alert-ack alert_id`",
                    f"- `{command_prefix}pubg-alert-snooze alert_id [minutes]`",
                    f"- `{command_prefix}pubg-alert-note alert_id note`",
                    f"- `{command_prefix}pubg-alert-resolution alert_id resolution`",
                    f"- `{command_prefix}pubg-alert-notes alert_id [limit]`",
                    f"- `{command_prefix}pubg-alert-history [preset|filters]`",
                    f"- `{command_prefix}pubg-worker-runs [collector|post_processing|all] [status=succeeded|failed|all] [limit] [range=last24h|today|yesterday|last7d]`",
                    f"- `{command_prefix}pubg-worker-run run_id`",
                    f"- `{command_prefix}pubg-settings`",
                    f"- `{command_prefix}pubg-settings collector 180 100 10`",
                    f"- `{command_prefix}pubg-settings public-profile public|private`",
                    f"- `{command_prefix}pubg-permission user_id group allow|deny [guild_id|global]`",
                    f"- `{command_prefix}pubg-ranking-scope guild|global [guild_id]`",
                    f"- `{command_prefix}pubg-delete-data steam target registration|normalized|raw|replay|all [reason]`",
                    f"- `{command_prefix}pubg-delete-cancel request_id [reason]`",
                    f"- `{command_prefix}유저삭제 steam 닉네임또는accountId`",
                ]
            ),
            mention_author=False,
        )

    @bot.hybrid_command(name="유저조회", aliases=["pubg-profile"])
    async def list_players_command(ctx: Any, name: str | None = None, shard: str = "steam") -> None:
        if not await require_permission(ctx, "profile_read"):
            return
        guild_id = await require_scoped_guild(ctx)
        if guild_id is None and not has_global_scope(ctx):
            return
        global_scope = has_global_scope(ctx)

        connection = connect_mysql(config.database)
        try:
            registry = PlayerRegistry(connection)
            if name:
                player = registry.get_player(shard=shard, name=name, include_inactive=True)
                players = [player] if player and _player_visible_to_scope(player, guild_id, global_scope) else []
            else:
                players = registry.list_players(
                    active_only=False,
                    registered_guild_id=None if global_scope else guild_id,
                    limit=20,
                )
        finally:
            connection.close()

        await ctx.reply(
            format_player_list(players, detail_base_url=config.app.local_web_base_url),
            mention_author=False,
        )

    @bot.hybrid_command(name="전적", aliases=["pubg-stats"])
    async def player_stats_command(ctx: Any, name: str | None = None, shard: str = "steam") -> None:
        if not await require_permission(ctx, "profile_read"):
            return
        if not name:
            await ctx.reply(f"사용법: `{command_prefix}전적 닉네임 [shard]`", mention_author=False)
            return

        guild_id = await require_scoped_guild(ctx)
        if guild_id is None and not has_global_scope(ctx):
            return
        global_scope = has_global_scope(ctx)

        connection = connect_mysql(config.database)
        try:
            profile = PlayerStatsService(connection).get_profile(
                shard=shard,
                account_id=name if name.startswith("account.") else None,
                name=None if name.startswith("account.") else name,
                guild_id=None if global_scope else guild_id,
                global_scope=global_scope,
            )
        finally:
            connection.close()

        if profile is None:
            await ctx.reply(
                format_local_section_command_reply(
                    "조회 가능한 등록 유저를 찾지 못했습니다.",
                    "profile_lookup",
                    "profile-lookup",
                    detail_base_url=config.app.local_web_base_url,
                    query_params={"shard": shard, "target": name},
                ),
                mention_author=False,
            )
            return

        await ctx.reply(
            format_player_profile_stats(profile, detail_base_url=config.app.local_web_base_url),
            mention_author=False,
        )

    @bot.hybrid_command(name="교전", aliases=["pubg-fights", "pubg-fight"])
    async def player_fight_outcomes_command(
        ctx: Any,
        name: str | None = None,
        shard: str = "steam",
    ) -> None:
        if not await require_permission(ctx, "profile_read"):
            return
        if not name:
            await ctx.reply(f"사용법: `{command_prefix}교전 닉네임 [shard]`", mention_author=False)
            return

        guild_id = await require_scoped_guild(ctx)
        if guild_id is None and not has_global_scope(ctx):
            return
        global_scope = has_global_scope(ctx)

        connection = connect_mysql(config.database)
        try:
            report = FightOutcomeStatsService(connection).get_report(
                shard=shard,
                account_id=name if name.startswith("account.") else None,
                name=None if name.startswith("account.") else name,
                guild_id=None if global_scope else guild_id,
                global_scope=global_scope,
                weapon_limit=5,
                loadout_limit=3,
                recent_limit=5,
            )
        finally:
            connection.close()

        if report is None:
            await ctx.reply(
                format_local_section_command_reply(
                    "조회 가능한 등록 유저를 찾지 못했습니다.",
                    "profile_lookup",
                    "profile-lookup",
                    detail_base_url=config.app.local_web_base_url,
                    query_params={"shard": shard, "target": name},
                ),
                mention_author=False,
            )
            return

        await ctx.reply(
            format_player_fight_outcomes(report, detail_base_url=config.app.local_web_base_url),
            mention_author=False,
        )

    @bot.hybrid_command(name="추세", aliases=["pubg-trends", "pubg-trend"])
    async def player_trends_command(
        ctx: Any,
        name: str | None = None,
        *,
        options: str = "",
    ) -> None:
        if not await require_permission(ctx, "profile_read"):
            return
        if not name:
            await ctx.reply(
                f"사용법: `{command_prefix}추세 닉네임 [month|week|date|hour] [steam|kakao] "
                "[team=squad view=fpp mode=squad-fpp type=official map=Baltic_Main "
                "from=YYYY-MM-DD to=YYYY-MM-DD custom=false limit=12]`",
                mention_author=False,
            )
            return
        try:
            granularity, shard, filters, limit = parse_player_trend_command_options(options)
        except ValueError as exc:
            await ctx.reply(f"추세 옵션 오류: {exc}", mention_author=False)
            return

        guild_id = await require_scoped_guild(ctx)
        if guild_id is None and not has_global_scope(ctx):
            return
        global_scope = has_global_scope(ctx)
        connection = connect_mysql(config.database)
        try:
            report = PlayerTrendService(connection).get_report(
                shard=shard,
                account_id=name if name.startswith("account.") else None,
                name=None if name.startswith("account.") else name,
                guild_id=None if global_scope else guild_id,
                global_scope=global_scope,
                granularity=granularity,
                filters=filters,
                bucket_limit=limit,
            )
        finally:
            connection.close()

        if report is None:
            await ctx.reply("조회 가능한 추세 데이터를 찾지 못했습니다.", mention_author=False)
            return
        await ctx.reply(
            format_player_trends(report, detail_base_url=config.app.local_web_base_url),
            mention_author=False,
        )

    @bot.hybrid_command(name="무기", aliases=["pubg-weapon"])
    async def player_weapon_command(
        ctx: Any,
        name: str | None = None,
        weapon: str | None = None,
        shard: str = "steam",
    ) -> None:
        if not await require_permission(ctx, "profile_read"):
            return
        if not name or not weapon:
            await ctx.reply(f"사용법: `{command_prefix}무기 닉네임 무기명 [shard]`", mention_author=False)
            return

        guild_id = await require_scoped_guild(ctx)
        if guild_id is None and not has_global_scope(ctx):
            return
        global_scope = has_global_scope(ctx)

        connection = connect_mysql(config.database)
        try:
            detail = PlayerStatsService(connection).get_weapon_detail(
                shard=shard,
                account_id=name if name.startswith("account.") else None,
                name=None if name.startswith("account.") else name,
                weapon=weapon,
                guild_id=None if global_scope else guild_id,
                global_scope=global_scope,
            )
        finally:
            connection.close()

        if detail is None:
            await ctx.reply(
                format_local_section_command_reply(
                    "조회 가능한 무기 통계를 찾지 못했습니다.",
                    "weapon_lookup",
                    "weapon-lookup",
                    detail_base_url=config.app.local_web_base_url,
                    query_params={"shard": shard, "target": name, "weapon": weapon},
                ),
                mention_author=False,
            )
            return

        await ctx.reply(
            format_player_weapon_detail(detail, detail_base_url=config.app.local_web_base_url),
            mention_author=False,
        )

    @bot.hybrid_command(name="추천", aliases=["pubg-recommend"])
    async def player_recommendations_command(
        ctx: Any,
        name: str | None = None,
        shard: str = "steam",
    ) -> None:
        if not await require_permission(ctx, "profile_read"):
            return
        if not name:
            await ctx.reply(f"사용법: `{command_prefix}추천 닉네임 [shard]`", mention_author=False)
            return

        guild_id = await require_scoped_guild(ctx)
        if guild_id is None and not has_global_scope(ctx):
            return
        global_scope = has_global_scope(ctx)

        connection = connect_mysql(config.database)
        try:
            recommendations = PlayerRecommendationService(connection).get_recommendations(
                shard=shard,
                account_id=name if name.startswith("account.") else None,
                name=None if name.startswith("account.") else name,
                guild_id=None if global_scope else guild_id,
                global_scope=global_scope,
            )
        finally:
            connection.close()

        if recommendations is None:
            await ctx.reply(
                format_local_section_command_reply(
                    "조회 가능한 추천 데이터를 찾지 못했습니다.",
                    "recommendation_lookup",
                    "recommendation-lookup",
                    detail_base_url=config.app.local_web_base_url,
                    query_params={"shard": shard, "target": name},
                ),
                mention_author=False,
            )
            return

        await ctx.reply(
            format_player_recommendations(
                recommendations,
                evidence_base_url=config.app.local_web_base_url,
                detail_base_url=config.app.local_web_base_url,
            ),
            mention_author=False,
        )

    @bot.hybrid_command(name="매치", aliases=["pubg-match"])
    async def player_match_command(
        ctx: Any,
        match_id: str | None = None,
        name: str | None = None,
        shard: str = "steam",
    ) -> None:
        if not await require_permission(ctx, "profile_read"):
            return
        if not match_id:
            await ctx.reply(
                f"사용법: `{command_prefix}매치 match_id [닉네임|accountId] [shard]`",
                mention_author=False,
            )
            return

        if name and shard == "steam" and name.lower() in {"steam", "kakao", "psn", "xbox", "console"}:
            shard = name
            name = None

        guild_id = await require_scoped_guild(ctx)
        if guild_id is None and not has_global_scope(ctx):
            return
        global_scope = has_global_scope(ctx)

        connection = connect_mysql(config.database)
        try:
            detail = PlayerStatsService(connection).get_match_detail(
                shard=shard,
                match_id=match_id,
                account_id=name if name and name.startswith("account.") else None,
                name=None if not name or name.startswith("account.") else name,
                guild_id=None if global_scope else guild_id,
                global_scope=global_scope,
            )
        finally:
            connection.close()

        if detail is None:
            await ctx.reply(
                format_local_section_command_reply(
                    "조회 가능한 등록 유저의 매치 상세를 찾지 못했습니다.",
                    "match_lookup",
                    "match-lookup",
                    detail_base_url=config.app.local_web_base_url,
                    query_params={"shard": shard, "target": name, "match_id": match_id},
                ),
                mention_author=False,
            )
            return

        await ctx.reply(
            format_player_match_detail(detail, detail_base_url=config.app.local_web_base_url),
            mention_author=False,
        )

    @bot.hybrid_command(name="랭킹", aliases=["pubg-ranking"])
    async def ranking_command(
        ctx: Any,
        metric: str = "kda",
        shard_or_limit: str = "steam",
        limit_or_scope: str | None = None,
        scope: str | None = None,
    ) -> None:
        if not await require_permission(ctx, "ranking_read"):
            return

        parsed_metric, shard, limit, global_requested = _parse_ranking_args(
            metric,
            shard_or_limit,
            limit_or_scope,
            scope,
        )
        guild_id = await require_scoped_guild(ctx)
        if guild_id is None and not has_global_scope(ctx):
            return
        if global_requested and not has_global_scope(ctx):
            await ctx.reply("전체 랭킹은 글로벌 관리자만 조회할 수 있습니다.", mention_author=False)
            return

        global_scope = (
            global_requested
            or (guild_id is None and has_global_scope(ctx))
            or (guild_id is not None and guild_ranking_scope(ctx) == "global")
        )
        ranking_guild_id = None if global_scope else guild_id
        ranking_guild_ids = [] if global_scope else guild_ranking_scope_ids(ctx)

        connection = connect_mysql(config.database)
        try:
            ranking = PlayerRankingService(connection).get_player_ranking(
                shard=shard,
                metric=parsed_metric,
                guild_id=ranking_guild_id,
                guild_ids=ranking_guild_ids,
                global_scope=global_scope,
                limit=limit,
            )
        finally:
            connection.close()

        await ctx.reply(
            format_player_ranking(
                ranking,
                detail_base_url=config.app.local_web_base_url,
                limit=limit,
            ),
            mention_author=False,
        )

    @bot.hybrid_command(name="유저등록", aliases=["pubg-register"])
    async def register_player_command(ctx: Any, shard: str, nickname: str) -> None:
        if not await require_permission(ctx, "register"):
            return
        if not config.secrets.pubg_api_key:
            await ctx.reply("PUBG_API_KEY가 설정되어 있지 않습니다.", mention_author=False)
            return

        connection = connect_mysql(config.database)
        try:
            try:
                player = PlayerRegistry(connection).register_player_by_name(
                    pubg_client=PubgApiClient(config.secrets.pubg_api_key),
                    shard=shard,
                    player_name=nickname,
                    public_profile=public_profile_default(),
                    context=DiscordCommandContext(
                        user_id=str(ctx.author.id),
                        guild_id=str(ctx.guild.id) if ctx.guild else None,
                        channel_id=str(ctx.channel.id) if ctx.channel else None,
                    ),
                )
            except PubgApiError as exc:
                await ctx.reply(f"PUBG API 조회 실패: {exc}", mention_author=False)
                return
        finally:
            connection.close()

        await ctx.reply(
            format_registered_player_command_reply(
                f"등록 완료: {player.current_name} ({player.shard})",
                player,
                detail_base_url=config.app.local_web_base_url,
            ),
            mention_author=False,
        )

    @bot.hybrid_command(name="유저삭제", aliases=["pubg-unregister"])
    async def unregister_player_command(ctx: Any, shard: str, target: str) -> None:
        if not await require_permission(ctx, "player_manage"):
            return
        guild_id = await require_scoped_guild(ctx)
        if guild_id is None and not has_global_scope(ctx):
            return
        global_scope = has_global_scope(ctx)

        connection = connect_mysql(config.database)
        try:
            registry = PlayerRegistry(connection)
            existing = registry.get_player(
                shard=shard,
                account_id=target if target.startswith("account.") else None,
                name=None if target.startswith("account.") else target,
                include_inactive=True,
            )
            if not existing or not _player_visible_to_scope(existing, guild_id, global_scope):
                player = None
            else:
                player = (
                    registry.unregister_player(
                        shard=shard,
                        account_id=existing.account_id,
                    )
                    if global_scope
                    else registry.unregister_player_from_guild(
                        shard=shard,
                        account_id=existing.account_id,
                        guild_id=guild_id or "",
                    )
                )
        finally:
            connection.close()

        if player is None:
            await ctx.reply(
                format_unregister_command_reply(
                    "대상 유저를 찾지 못했습니다.",
                    detail_base_url=config.app.local_web_base_url,
                ),
                mention_author=False,
            )
        else:
            await ctx.reply(
                format_registered_player_command_reply(
                    (
                        f"수집 중지 완료: {player.current_name} ({player.shard})"
                        if global_scope
                        else f"현재 Discord 서버 등록 해제 완료: {player.current_name} ({player.shard})"
                    ),
                    player,
                    detail_base_url=config.app.local_web_base_url,
                ),
                mention_author=False,
            )

    @bot.hybrid_command(name="최근스냅샷", aliases=["pubg-replay"])
    async def latest_snapshot_command(ctx: Any, match_id: str | None = None) -> None:
        if not await require_permission(ctx, "replay_read"):
            return
        guild_id = await require_scoped_guild(ctx)
        if guild_id is None and not has_global_scope(ctx):
            return

        connection = connect_mysql(config.database)
        try:
            artifacts = list_replay_artifacts(
                connection,
                limit=1,
                artifact_type="map_snapshot",
                match_id=match_id,
                registered_guild_id=None if has_global_scope(ctx) else guild_id,
            )
        finally:
            connection.close()

        if not artifacts:
            await ctx.reply(
                format_local_section_command_reply(
                    "생성된 2D 스냅샷이 없습니다.",
                    "replay_artifacts",
                    "replay-artifacts",
                    detail_base_url=config.app.local_web_base_url,
                    query_params={"match_id": match_id},
                ),
                mention_author=False,
            )
            return

        artifact = artifacts[0]
        store = ReplayArtifactStore(config.app.replay_data_dir)
        try:
            path = store.resolve_path(artifact.relative_path)
        except ReplayStorageError:
            await ctx.reply(
                format_local_section_command_reply(
                    "스냅샷 파일 경로가 올바르지 않습니다.",
                    "replay_artifacts",
                    "replay-artifacts",
                    detail_base_url=config.app.local_web_base_url,
                    query_params={
                        "shard": artifact.shard,
                        "match_id": artifact.match_id,
                        "account_id": artifact.account_id,
                        "replay_artifact_id": artifact.id,
                    },
                ),
                mention_author=False,
            )
            return

        if not path.is_file():
            await ctx.reply(
                format_local_section_command_reply(
                    "스냅샷 파일을 찾지 못했습니다.",
                    "replay_artifacts",
                    "replay-artifacts",
                    detail_base_url=config.app.local_web_base_url,
                    query_params={
                        "shard": artifact.shard,
                        "match_id": artifact.match_id,
                        "account_id": artifact.account_id,
                        "replay_artifact_id": artifact.id,
                    },
                ),
                mention_author=False,
            )
            return

        await ctx.reply(
            format_replay_artifact_summary(artifact, detail_base_url=config.app.local_web_base_url),
            file=discord.File(Path(path), filename=path.name),
            mention_author=False,
        )

    @bot.hybrid_command(name="pubg-settings")
    async def discord_settings_command(
        ctx: Any,
        section: str | None = None,
        value1: str | None = None,
        value2: str | None = None,
        value3: str | None = None,
    ) -> None:
        if not await require_permission(ctx, "settings_write"):
            return
        if scope_settings_store is None:
            await ctx.reply(
                format_discord_settings_command_reply(
                    "로컬 설정 저장소를 사용할 수 없습니다.",
                    detail_base_url=config.app.local_web_base_url,
                ),
                mention_author=False,
            )
            return

        normalized_section = (section or "status").strip().lower()
        if normalized_section in {"status", "show", "조회"}:
            try:
                collector = scope_settings_store.load_collector_settings(
                    default=CollectorSettings(
                        poll_interval_seconds=config.app.collector_poll_interval_seconds,
                        cycle_player_limit=config.app.collector_cycle_player_limit,
                        player_lookup_chunk_size=config.app.player_lookup_chunk_size,
                    )
                )
                scopes = scope_settings_store.load_discord_scope_settings()
                storage = scope_settings_store.load_storage_settings()
            except LocalSettingsError:
                await ctx.reply(
                    format_discord_settings_command_reply(
                        "로컬 설정을 불러오지 못했습니다. 로컬 프로그램에서 확인해 주세요.",
                        detail_base_url=config.app.local_web_base_url,
                    ),
                    mention_author=False,
                )
                return

            guild_id = guild_id_for(ctx)
            ranking_scope = scopes.guild_ranking_scopes.get(guild_id, "guild") if guild_id else None
            await ctx.reply(
                format_discord_settings_summary(
                    poll_interval_seconds=collector.poll_interval_seconds,
                    cycle_player_limit=collector.cycle_player_limit,
                    player_lookup_chunk_size=collector.player_lookup_chunk_size,
                    raw_compression=storage.raw_compression if storage is not None else config.app.raw_compression,
                    public_profile_default=scopes.public_profile_default,
                    guild_ranking_scope=ranking_scope,
                    detail_base_url=config.app.local_web_base_url,
                ),
                mention_author=False,
            )
            return

        if normalized_section in {"secret", "secrets", "token", "api", "database", "db"}:
            await ctx.reply(
                "비밀정보와 데이터베이스 설정은 Discord에서 조회하거나 변경하지 않습니다.",
                mention_author=False,
            )
            return
        if normalized_section in {"storage", "path", "paths"}:
            await ctx.reply(
                format_local_section_command_reply(
                    "저장 경로와 압축 설정은 로컬 프로그램에서만 변경할 수 있습니다.",
                    "local_storage_settings",
                    "storage-settings",
                    detail_base_url=config.app.local_web_base_url,
                ),
                mention_author=False,
            )
            return

        if not permission_checker.is_globally_allowed(identity_for(ctx), "settings_write"):
            await ctx.reply(
                "전역 settings_write 권한 또는 글로벌 관리자 권한이 있어야 설정을 변경할 수 있습니다.",
                mention_author=False,
            )
            return

        if normalized_section in {"collector", "수집"}:
            poll_interval_seconds = _positive_int(value1)
            cycle_player_limit = _positive_int(value2)
            player_lookup_chunk_size = _positive_int(value3)
            if None in {poll_interval_seconds, cycle_player_limit, player_lookup_chunk_size}:
                await ctx.reply(
                    format_local_section_command_reply(
                        f"사용법: `{command_prefix}pubg-settings collector 조회주기 최대인원 묶음크기`",
                        "local_collector_settings",
                        "collector-settings",
                        detail_base_url=config.app.local_web_base_url,
                        query_params={
                            "collector_poll_interval_seconds": poll_interval_seconds,
                            "collector_cycle_player_limit": cycle_player_limit,
                            "collector_player_lookup_chunk_size": player_lookup_chunk_size,
                        },
                    ),
                    mention_author=False,
                )
                return
            if not (
                60 <= poll_interval_seconds <= 300
                and 1 <= cycle_player_limit <= 100
                and 1 <= player_lookup_chunk_size <= 10
            ):
                await ctx.reply(
                    format_local_section_command_reply(
                        "수집 설정 범위: poll_seconds 60~300, player_limit 1~100, chunk_size 1~10.",
                        "local_collector_settings",
                        "collector-settings",
                        detail_base_url=config.app.local_web_base_url,
                        query_params={
                            "collector_poll_interval_seconds": poll_interval_seconds,
                            "collector_cycle_player_limit": cycle_player_limit,
                            "collector_player_lookup_chunk_size": player_lookup_chunk_size,
                        },
                    ),
                    mention_author=False,
                )
                return
            try:
                saved = scope_settings_store.save_collector_settings(
                    poll_interval_seconds,
                    cycle_player_limit,
                    player_lookup_chunk_size,
                )
            except LocalSettingsError:
                await ctx.reply(
                    format_local_section_command_reply(
                        "수집 설정을 저장하지 못했습니다. 로컬 프로그램에서 확인해 주세요.",
                        "local_collector_settings",
                        "collector-settings",
                        detail_base_url=config.app.local_web_base_url,
                        query_params={
                            "collector_poll_interval_seconds": poll_interval_seconds,
                            "collector_cycle_player_limit": cycle_player_limit,
                            "collector_player_lookup_chunk_size": player_lookup_chunk_size,
                        },
                    ),
                    mention_author=False,
                )
                return

            await ctx.reply(
                format_discord_collector_settings_result(
                    poll_interval_seconds=saved.poll_interval_seconds,
                    cycle_player_limit=saved.cycle_player_limit,
                    player_lookup_chunk_size=saved.player_lookup_chunk_size,
                    detail_base_url=config.app.local_web_base_url,
                ),
                mention_author=False,
            )
            return

        if normalized_section in {"public-profile", "public_profile", "profile", "공개프로필"}:
            public_profile_default = _discord_public_profile_default(value1)
            if public_profile_default is None:
                await ctx.reply(
                    format_local_section_command_reply(
                        f"사용법: `{command_prefix}pubg-settings public-profile 공개|비공개`",
                        "local_discord_scopes",
                        "discord-scopes",
                        detail_base_url=config.app.local_web_base_url,
                    ),
                    mention_author=False,
                )
                return
            try:
                scopes = scope_settings_store.load_discord_scope_settings()
                scope_settings_store.save_discord_scope_settings(
                    guild_ranking_scopes=scopes.guild_ranking_scopes,
                    public_profile_default=public_profile_default,
                )
            except LocalSettingsError:
                await ctx.reply(
                    format_local_section_command_reply(
                        "공개 프로필 기본값을 저장하지 못했습니다. 로컬 프로그램에서 확인해 주세요.",
                        "local_discord_scopes",
                        "discord-scopes",
                        detail_base_url=config.app.local_web_base_url,
                        query_params={
                            "discord_public_profile_default": str(public_profile_default).lower(),
                        },
                    ),
                    mention_author=False,
                )
                return

            await ctx.reply(
                format_discord_public_profile_settings_result(
                    public_profile_default=public_profile_default,
                    detail_base_url=config.app.local_web_base_url,
                ),
                mention_author=False,
            )
            return

        await ctx.reply(
            format_discord_settings_command_reply(
                (
                    f"사용법: `{command_prefix}pubg-settings`, "
                    f"`{command_prefix}pubg-settings collector 180 100 10`, or "
                    f"`{command_prefix}pubg-settings public-profile public|private`"
                ),
                detail_base_url=config.app.local_web_base_url,
            ),
            mention_author=False,
        )

    @bot.hybrid_command(name="pubg-delete-data")
    async def data_deletion_request_command(
        ctx: Any,
        shard: str | None = None,
        target: str | None = None,
        deletion_scope: str | None = None,
        *,
        reason: str | None = None,
    ) -> None:
        if not await require_permission(ctx, "admin"):
            return
        guild_id = await require_scoped_guild(ctx)
        if guild_id is None and not has_global_scope(ctx):
            return

        try:
            normalized_scope = normalize_deletion_scope(deletion_scope or "")
        except DataDeletionRequestError:
            await ctx.reply(
                format_data_deletion_command_reply(
                    (
                        f"사용법: `{command_prefix}pubg-delete-data steam 대상 "
                        "registration|normalized|raw|replay|all [reason]`"
                    ),
                    shard=shard,
                    target=target,
                    detail_base_url=config.app.local_web_base_url,
                ),
                mention_author=False,
            )
            return
        if not shard or not target:
            await ctx.reply(
                format_data_deletion_command_reply(
                    (
                        f"사용법: `{command_prefix}pubg-delete-data steam 대상 "
                        "registration|normalized|raw|replay|all [reason]`"
                    ),
                    shard=shard,
                    target=target,
                    detail_base_url=config.app.local_web_base_url,
                ),
                mention_author=False,
            )
            return

        connection = connect_mysql(config.database)
        try:
            registry = PlayerRegistry(connection)
            player = registry.get_player(
                shard=shard,
                account_id=target if target.startswith("account.") else None,
                name=None if target.startswith("account.") else target,
                include_inactive=True,
            )
            if not player or not _player_visible_to_scope(player, guild_id, has_global_scope(ctx)):
                await ctx.reply(
                    format_data_deletion_command_reply(
                        "삭제 검토 요청 대상을 찾지 못했습니다.",
                        shard=shard,
                        target=target,
                        detail_base_url=config.app.local_web_base_url,
                    ),
                    mention_author=False,
                )
                return
            try:
                request = DataDeletionRequestService(connection).create_request(
                    player=player,
                    deletion_scope=normalized_scope,
                    requested_by_discord_user_id=str(ctx.author.id),
                    requested_guild_id=guild_id,
                    requested_channel_id=str(ctx.channel.id) if ctx.channel else None,
                    reason=reason,
                )
            except DataDeletionRequestError as exc:
                await ctx.reply(
                    format_data_deletion_command_reply(
                        f"삭제 검토 요청 실패: {exc}",
                        shard=player.shard,
                        target=player.account_id,
                        detail_base_url=config.app.local_web_base_url,
                    ),
                    mention_author=False,
                )
                return
        finally:
            connection.close()

        await ctx.reply(
            format_data_deletion_request_result(
                request,
                detail_base_url=config.app.local_web_base_url,
            ),
            mention_author=False,
        )

    @bot.hybrid_command(name="pubg-delete-cancel")
    async def data_deletion_cancel_command(
        ctx: Any,
        request_id: str | None = None,
        *,
        reason: str | None = None,
    ) -> None:
        if not await require_permission(ctx, "admin"):
            return
        parsed_request_id = _positive_int(request_id)
        if parsed_request_id is None:
            await ctx.reply(
                format_data_deletion_command_reply(
                    f"사용법: `{command_prefix}pubg-delete-cancel 요청_ID [사유]`",
                    detail_base_url=config.app.local_web_base_url,
                ),
                mention_author=False,
            )
            return
        guild_id = await require_scoped_guild(ctx)
        if guild_id is None and not has_global_scope(ctx):
            return

        connection = connect_mysql(config.database)
        try:
            service = DataDeletionRequestService(connection)
            try:
                request = service.get_request(parsed_request_id)
            except DataDeletionRequestError as exc:
                await ctx.reply(
                    format_data_deletion_command_reply(
                        f"삭제 검토 요청 조회 실패: {exc}",
                        request_id=parsed_request_id,
                        detail_base_url=config.app.local_web_base_url,
                    ),
                    mention_author=False,
                )
                return

            requester = request.requested_by_discord_user_id == str(ctx.author.id)
            same_guild = guild_id is not None and request.requested_guild_id == guild_id
            if not (requester or same_guild or has_global_scope(ctx)):
                await ctx.reply("이 삭제 검토 요청을 취소할 권한이 없습니다.", mention_author=False)
                return
            try:
                cancelled = service.cancel_request(
                    parsed_request_id,
                    actor_type="discord",
                    actor_id=str(ctx.author.id),
                    note=reason,
                )
            except DataDeletionRequestError as exc:
                await ctx.reply(
                    format_data_deletion_command_reply(
                        f"삭제 검토 요청 취소 실패: {exc}",
                        request_id=parsed_request_id,
                        detail_base_url=config.app.local_web_base_url,
                    ),
                    mention_author=False,
                )
                return
        finally:
            connection.close()

        await ctx.reply(
            format_data_deletion_request_result(
                cancelled,
                action_label="삭제 검토 요청 취소 완료",
                detail_base_url=config.app.local_web_base_url,
            ),
            mention_author=False,
        )

    class DiscordPermissionView(discord.ui.View):
        def __init__(
            self,
            *,
            owner_id: int,
            guild_id: str,
            global_admin: bool,
        ) -> None:
            super().__init__(timeout=600.0)
            self.owner_id = owner_id
            self.guild_id = guild_id
            self.global_admin = global_admin
            self.selected_user_id: str | None = None
            self.selected_user_label: str | None = None
            self.selected_group: str | None = None
            self.scope = "guild"
            self.group_page = 0
            self.last_result = "Discord 사용자와 권한 그룹을 선택하세요."
            self.rebuild()

        def groups(self) -> list[str]:
            if not refresh_permission_settings():
                return []
            return sorted(
                permission_checker.settings.command_groups,
                key=lambda group: (command_group_label(group), group),
            )

        def make_embed(self) -> Any:
            user = self.selected_user_label or "선택 안 함"
            group = (
                f"{command_group_label(self.selected_group)} (`{self.selected_group}`)"
                if self.selected_group
                else "선택 안 함"
            )
            scope = "모든 Discord 서버" if self.scope == "global" else "현재 Discord 서버"
            description = (
                "Discord ID를 직접 입력하지 않고 서버 멤버 목록에서 권한을 관리합니다.\n\n"
                f"**대상 사용자**\n{user}\n\n"
                f"**권한 그룹**\n{group}\n\n"
                f"**적용 범위**\n{scope}\n\n"
                f"**처리 결과**\n{self.last_result}"
            )
            embed = discord.Embed(
                title="PUBG AI · Discord 사용자 권한 관리",
                description=description,
                colour=0x42D3AA,
            )
            embed.set_footer(text="이 화면은 명령을 실행한 사용자만 조작할 수 있습니다.")
            return embed

        def rebuild(self) -> None:
            self.clear_items()
            user_select = discord.ui.UserSelect(
                placeholder="권한을 변경할 Discord 사용자를 선택하세요",
                min_values=1,
                max_values=1,
                row=0,
            )

            async def user_callback(interaction: Any) -> None:
                member = user_select.values[0]
                self.selected_user_id = str(member.id)
                display_name = str(getattr(member, "display_name", "") or getattr(member, "name", "") or member.id)
                username = str(getattr(member, "name", "") or "")
                self.selected_user_label = (
                    f"{display_name} (@{username})"
                    if username and display_name != username
                    else display_name
                )
                self.last_result = "대상 사용자를 선택했습니다."
                self.rebuild()
                await interaction.response.edit_message(embed=self.make_embed(), view=self)

            user_select.callback = user_callback
            self.add_item(user_select)

            groups = self.groups()
            max_page = max(0, (len(groups) - 1) // 25)
            self.group_page = min(max(0, self.group_page), max_page)
            page_groups = groups[self.group_page * 25 : (self.group_page + 1) * 25]
            if page_groups:
                group_select = discord.ui.Select(
                    placeholder="부여하거나 회수할 권한 그룹을 선택하세요",
                    min_values=1,
                    max_values=1,
                    options=[
                        discord.SelectOption(
                            label=command_group_label(group)[:100],
                            value=group,
                            description=(
                                f"키 {group} · "
                                f"{len(permission_checker.settings.command_groups.get(group, []))}개 명령"
                            )[:100],
                            default=group == self.selected_group,
                        )
                        for group in page_groups
                    ],
                    row=1,
                )

                async def group_callback(interaction: Any) -> None:
                    self.selected_group = group_select.values[0]
                    self.last_result = "권한 그룹을 선택했습니다."
                    self.rebuild()
                    await interaction.response.edit_message(embed=self.make_embed(), view=self)

                group_select.callback = group_callback
                self.add_item(group_select)

            if self.global_admin:
                scope_select = discord.ui.Select(
                    placeholder="권한 적용 범위를 선택하세요",
                    min_values=1,
                    max_values=1,
                    options=[
                        discord.SelectOption(
                            label="현재 Discord 서버",
                            value="guild",
                            description="이 서버에서만 권한을 적용합니다.",
                            default=self.scope == "guild",
                        ),
                        discord.SelectOption(
                            label="모든 Discord 서버",
                            value="global",
                            description="앱 봇이 관리하는 모든 서버에 권한을 적용합니다.",
                            default=self.scope == "global",
                        ),
                    ],
                    row=2,
                )

                async def scope_callback(interaction: Any) -> None:
                    self.scope = scope_select.values[0]
                    self.last_result = "적용 범위를 선택했습니다."
                    self.rebuild()
                    await interaction.response.edit_message(embed=self.make_embed(), view=self)

                scope_select.callback = scope_callback
                self.add_item(scope_select)

            grant_button = discord.ui.Button(label="권한 부여", style=discord.ButtonStyle.success, row=3)
            revoke_button = discord.ui.Button(label="권한 회수", style=discord.ButtonStyle.secondary, row=3)
            close_button = discord.ui.Button(label="닫기", style=discord.ButtonStyle.danger, row=3)

            async def apply_change(interaction: Any, action: str) -> None:
                if permission_manager is None:
                    await interaction.response.send_message("Discord 권한 저장소를 사용할 수 없습니다.", ephemeral=True)
                    return
                if not self.selected_user_id or not self.selected_group:
                    await interaction.response.send_message("Discord 사용자와 권한 그룹을 먼저 선택하세요.", ephemeral=True)
                    return
                target_guild_id = None if self.scope == "global" else self.guild_id
                try:
                    if action == "grant":
                        change = permission_manager.grant(
                            user_id=self.selected_user_id,
                            group=self.selected_group,
                            guild_id=target_guild_id,
                            member_label=self.selected_user_label,
                            member_guild_id=self.guild_id,
                        )
                    else:
                        change = permission_manager.revoke(
                            user_id=self.selected_user_id,
                            group=self.selected_group,
                            guild_id=target_guild_id,
                        )
                except LocalSettingsError as exc:
                    await interaction.response.send_message(f"권한 변경 실패: {exc}", ephemeral=True)
                    return
                permission_checker.settings = change.settings
                action_label = "부여" if action == "grant" else "회수"
                result_label = "변경 완료" if change.changed else "이미 같은 상태"
                self.last_result = f"{self.selected_user_label} · {command_group_label(self.selected_group)} · {action_label} {result_label}"
                self.rebuild()
                await interaction.response.edit_message(embed=self.make_embed(), view=self)

            async def grant_callback(interaction: Any) -> None:
                await apply_change(interaction, "grant")

            async def revoke_callback(interaction: Any) -> None:
                await apply_change(interaction, "revoke")

            async def close_callback(interaction: Any) -> None:
                for item in self.children:
                    item.disabled = True
                await interaction.response.edit_message(embed=self.make_embed(), view=self)
                self.stop()

            grant_button.callback = grant_callback
            revoke_button.callback = revoke_callback
            close_button.callback = close_callback
            self.add_item(grant_button)
            self.add_item(revoke_button)
            self.add_item(close_button)

            if max_page:
                previous_button = discord.ui.Button(
                    label="이전 그룹",
                    style=discord.ButtonStyle.secondary,
                    disabled=self.group_page == 0,
                    row=4,
                )
                next_button = discord.ui.Button(
                    label="다음 그룹",
                    style=discord.ButtonStyle.secondary,
                    disabled=self.group_page >= max_page,
                    row=4,
                )

                async def previous_callback(interaction: Any) -> None:
                    self.group_page -= 1
                    self.rebuild()
                    await interaction.response.edit_message(embed=self.make_embed(), view=self)

                async def next_callback(interaction: Any) -> None:
                    self.group_page += 1
                    self.rebuild()
                    await interaction.response.edit_message(embed=self.make_embed(), view=self)

                previous_button.callback = previous_callback
                next_button.callback = next_callback
                self.add_item(previous_button)
                self.add_item(next_button)

        async def interaction_check(self, interaction: Any) -> bool:
            if interaction.user.id == self.owner_id:
                return True
            await interaction.response.send_message(
                "이 권한 관리 화면은 명령을 실행한 사용자만 조작할 수 있습니다.",
                ephemeral=True,
            )
            return False

    @bot.hybrid_command(name="pubg-permission")
    async def discord_permission_command(
        ctx: Any,
        user_id: str | None = None,
        group: str | None = None,
        action: str | None = None,
        target_scope: str | None = None,
    ) -> None:
        if not await require_permission(ctx, "admin"):
            return

        if (
            getattr(ctx, "interaction", None) is not None
            and user_id is None
            and group is None
            and action is None
            and target_scope is None
        ):
            current_guild_id = guild_id_for(ctx)
            if current_guild_id is None:
                await ctx.reply("권한 관리 화면은 Discord 서버 채널에서 열어 주세요.", ephemeral=True, mention_author=False)
                return
            if permission_manager is None:
                await ctx.reply("Discord 권한 설정 저장소를 사용할 수 없습니다.", ephemeral=True, mention_author=False)
                return
            view = DiscordPermissionView(
                owner_id=int(ctx.author.id),
                guild_id=current_guild_id,
                global_admin=has_global_scope(ctx),
            )
            await ctx.reply(
                embed=view.make_embed(),
                view=view,
                ephemeral=True,
                mention_author=False,
            )
            return

        parsed_user_id = _discord_user_id(user_id)
        parsed_action = _discord_permission_action(action)
        requested_scope = (target_scope or "").strip()
        if parsed_user_id is None or not group or parsed_action is None:
            await ctx.reply(
                format_discord_permission_command_reply(
                    (
                        f"사용법: `{command_prefix}pubg-permission "
                        "사용자_ID 권한_그룹 허용|회수 [서버_ID|전체]`"
                    ),
                    user_id=parsed_user_id,
                    group=group,
                    guild_id=requested_scope if requested_scope.isdigit() else None,
                    detail_base_url=config.app.local_web_base_url,
                ),
                mention_author=False,
            )
            return

        global_admin = has_global_scope(ctx)
        current_guild_id = guild_id_for(ctx)
        if requested_scope and _is_scope_token(requested_scope):
            if not global_admin:
                await ctx.reply("전역 권한은 글로벌 관리자만 변경할 수 있습니다.", mention_author=False)
                return
            target_guild_id = None
        else:
            target_guild_id = requested_scope or current_guild_id
            if target_guild_id is None:
                await ctx.reply(
                    format_discord_permission_command_reply(
                        "Discord 서버 ID를 지정하거나 서버 채널에서 사용해 주세요.",
                        user_id=parsed_user_id,
                        group=group,
                        detail_base_url=config.app.local_web_base_url,
                    ),
                    mention_author=False,
                )
                return
            if not target_guild_id.isdigit():
                await ctx.reply(
                    format_discord_permission_command_reply(
                        "Discord 서버 ID는 숫자여야 합니다.",
                        user_id=parsed_user_id,
                        group=group,
                        detail_base_url=config.app.local_web_base_url,
                    ),
                    mention_author=False,
                )
                return
            if not global_admin and target_guild_id != current_guild_id:
                await ctx.reply(
                    "다른 Discord 서버의 권한은 글로벌 관리자만 변경할 수 있습니다.",
                    mention_author=False,
                )
                return

        if permission_manager is None:
            await ctx.reply(
                format_discord_permission_command_reply(
                    "Discord 권한 설정 저장소를 사용할 수 없습니다.",
                    user_id=parsed_user_id,
                    group=group,
                    guild_id=target_guild_id,
                    detail_base_url=config.app.local_web_base_url,
                ),
                mention_author=False,
            )
            return

        try:
            if parsed_action == "grant":
                get_member = getattr(getattr(ctx, "guild", None), "get_member", None)
                member = get_member(int(parsed_user_id)) if callable(get_member) else None
                member_label = None
                if member is not None:
                    display_name = str(getattr(member, "display_name", "") or getattr(member, "name", ""))
                    username = str(getattr(member, "name", "") or "")
                    member_label = (
                        f"{display_name} (@{username})"
                        if display_name and username and display_name != username
                        else display_name or username or None
                    )
                change = permission_manager.grant(
                    user_id=parsed_user_id,
                    group=group,
                    guild_id=target_guild_id,
                    member_label=member_label,
                    member_guild_id=current_guild_id,
                )
            else:
                change = permission_manager.revoke(
                    user_id=parsed_user_id,
                    group=group,
                    guild_id=target_guild_id,
                )
        except LocalSettingsError as exc:
            await ctx.reply(
                format_discord_permission_command_reply(
                    f"Discord 권한 변경 실패: {exc}",
                    user_id=parsed_user_id,
                    group=group,
                    guild_id=target_guild_id,
                    detail_base_url=config.app.local_web_base_url,
                ),
                mention_author=False,
            )
            return

        permission_checker.settings = change.settings
        await ctx.reply(
            format_discord_permission_change_result(
                action=parsed_action,
                user_id=parsed_user_id,
                group=group,
                guild_id=target_guild_id,
                changed=change.changed,
                detail_base_url=config.app.local_web_base_url,
            ),
            mention_author=False,
        )

    @bot.hybrid_command(name="pubg-ranking-scope", aliases=["pubg-guild-scope"])
    async def discord_ranking_scope_command(
        ctx: Any,
        ranking_scope: str | None = None,
        guild_id: str | None = None,
    ) -> None:
        if not await require_permission(ctx, "admin"):
            return
        if not has_global_scope(ctx):
            await ctx.reply("랭킹 범위는 글로벌 관리자만 변경할 수 있습니다.", mention_author=False)
            return

        parsed_scope = _discord_ranking_scope(ranking_scope)
        target_guild_id = (guild_id or guild_id_for(ctx) or "").strip()
        if parsed_scope is None or not target_guild_id or not target_guild_id.isdigit():
            await ctx.reply(
                format_discord_scope_command_reply(
                    f"사용법: `{command_prefix}pubg-ranking-scope 선택서버|전체서버 [서버_ID]`",
                    guild_id=target_guild_id if target_guild_id.isdigit() else None,
                    ranking_scope=parsed_scope,
                    detail_base_url=config.app.local_web_base_url,
                ),
                mention_author=False,
            )
            return
        if scope_settings_store is None:
            await ctx.reply(
                format_discord_scope_command_reply(
                    "Discord 범위 설정 저장소를 사용할 수 없습니다.",
                    guild_id=target_guild_id,
                    ranking_scope=parsed_scope,
                    detail_base_url=config.app.local_web_base_url,
                ),
                mention_author=False,
            )
            return

        try:
            settings = scope_settings_store.load_discord_scope_settings()
            changed = settings.guild_ranking_scopes.get(target_guild_id) != parsed_scope
            if changed:
                next_scopes = dict(settings.guild_ranking_scopes)
                next_scopes[target_guild_id] = parsed_scope
                scope_settings_store.save_discord_scope_settings(
                    guild_ranking_scopes=next_scopes,
                    public_profile_default=settings.public_profile_default,
                )
        except LocalSettingsError as exc:
            await ctx.reply(
                format_discord_scope_command_reply(
                    f"Discord 랭킹 범위 변경 실패: {exc}",
                    guild_id=target_guild_id,
                    ranking_scope=parsed_scope,
                    detail_base_url=config.app.local_web_base_url,
                ),
                mention_author=False,
            )
            return

        await ctx.reply(
            format_discord_scope_change_result(
                guild_id=target_guild_id,
                ranking_scope=parsed_scope,
                changed=changed,
                detail_base_url=config.app.local_web_base_url,
            ),
            mention_author=False,
        )

    @bot.hybrid_command(name="pubg-alerts")
    async def alerts_command(ctx: Any) -> None:
        if not await require_permission(ctx, "admin"):
            return
        if scope_settings_store is None:
            await ctx.reply(
                format_alerts_command_reply(
                    "PUBG AI 알림 설정을 사용할 수 없습니다.",
                    detail_base_url=config.app.local_web_base_url,
                ),
                mention_author=False,
            )
            return

        try:
            alert_settings = scope_settings_store.load_alert_settings()
        except LocalSettingsError as exc:
            await ctx.reply(
                format_alerts_command_reply(
                    f"PUBG AI 알림 설정 오류: {exc}",
                    detail_base_url=config.app.local_web_base_url,
                ),
                mention_author=False,
            )
            return

        connection = connect_mysql(config.database)
        try:
            report = collect_system_alerts(
                config=config,
                connection=connection,
                settings=alert_settings,
                after_worker_run_id=None,
            )
            active_records = sync_alert_history(connection, report.alerts)
            current_alert_keys = {alert.key for alert in report.alerts}
            visible_records = [
                record
                for record in visible_alert_records(active_records)
                if record.alert_key in current_alert_keys
            ]
        finally:
            connection.close()

        await ctx.reply(
            format_alert_report(visible_records, detail_base_url=config.app.local_web_base_url),
            mention_author=False,
        )

    @bot.hybrid_command(name="pubg-alert-ack", aliases=["pubg-alert-acknowledge"])
    async def alert_acknowledge_command(ctx: Any, alert_id: str | None = None) -> None:
        if not await require_permission(ctx, "admin"):
            return
        parsed_alert_id = _positive_int(alert_id)
        if parsed_alert_id is None:
            await ctx.reply(
                format_alert_command_reply(
                    f"사용법: `{command_prefix}pubg-alert-ack 알림_ID`",
                    parsed_alert_id,
                    detail_base_url=config.app.local_web_base_url,
                ),
                mention_author=False,
            )
            return

        connection = connect_mysql(config.database)
        try:
            try:
                record = acknowledge_alert(connection, parsed_alert_id)
            except AlertHistoryError as exc:
                await ctx.reply(
                    format_alert_command_reply(
                        f"PUBG AI 알림을 찾지 못했습니다: {exc}",
                        parsed_alert_id,
                        detail_base_url=config.app.local_web_base_url,
                    ),
                    mention_author=False,
                )
                return
        finally:
            connection.close()

        await ctx.reply(
            format_alert_action_result(
                record,
                "acknowledged",
                detail_base_url=config.app.local_web_base_url,
            ),
            mention_author=False,
        )

    @bot.hybrid_command(name="pubg-alert-snooze")
    async def alert_snooze_command(
        ctx: Any,
        alert_id: str | None = None,
        minutes: str = "60",
    ) -> None:
        if not await require_permission(ctx, "admin"):
            return
        parsed_alert_id = _positive_int(alert_id)
        parsed_minutes = _positive_int(minutes)
        if parsed_alert_id is None or parsed_minutes is None:
            await ctx.reply(
                format_alert_command_reply(
                    f"사용법: `{command_prefix}pubg-alert-snooze 알림_ID [숨길_분]`",
                    parsed_alert_id,
                    detail_base_url=config.app.local_web_base_url,
                ),
                mention_author=False,
            )
            return

        connection = connect_mysql(config.database)
        try:
            try:
                record = snooze_alert(connection, parsed_alert_id, parsed_minutes)
            except AlertHistoryError as exc:
                await ctx.reply(
                    format_alert_command_reply(
                        f"PUBG AI 알림을 찾지 못했습니다: {exc}",
                        parsed_alert_id,
                        detail_base_url=config.app.local_web_base_url,
                    ),
                    mention_author=False,
                )
                return
        finally:
            connection.close()

        await ctx.reply(
            format_alert_action_result(
                record,
                "snoozed",
                detail_base_url=config.app.local_web_base_url,
            ),
            mention_author=False,
        )

    @bot.hybrid_command(name="pubg-alert-note")
    async def alert_note_command(
        ctx: Any,
        alert_id: str | None = None,
        *,
        note_text: str | None = None,
    ) -> None:
        if not await require_permission(ctx, "admin"):
            return
        parsed_alert_id = _positive_int(alert_id)
        if parsed_alert_id is None or not note_text or not note_text.strip():
            await ctx.reply(
                format_alert_command_reply(
                    f"사용법: `{command_prefix}pubg-alert-note 알림_ID 메모`",
                    parsed_alert_id,
                    detail_base_url=config.app.local_web_base_url,
                ),
                mention_author=False,
            )
            return

        connection = connect_mysql(config.database)
        try:
            try:
                note = add_alert_note(
                    connection,
                    parsed_alert_id,
                    note_text,
                    note_type="note",
                    created_by=alert_note_creator_for(ctx),
                )
            except AlertHistoryError as exc:
                await ctx.reply(
                    format_alert_command_reply(
                        f"PUBG AI 알림 메모 저장 오류: {exc}",
                        parsed_alert_id,
                        detail_base_url=config.app.local_web_base_url,
                    ),
                    mention_author=False,
                )
                return
        finally:
            connection.close()

        await ctx.reply(
            format_alert_note_result(note, detail_base_url=config.app.local_web_base_url),
            mention_author=False,
        )

    @bot.hybrid_command(name="pubg-alert-resolution", aliases=["pubg-alert-resolve"])
    async def alert_resolution_command(
        ctx: Any,
        alert_id: str | None = None,
        *,
        note_text: str | None = None,
    ) -> None:
        if not await require_permission(ctx, "admin"):
            return
        parsed_alert_id = _positive_int(alert_id)
        if parsed_alert_id is None or not note_text or not note_text.strip():
            await ctx.reply(
                format_alert_command_reply(
                    f"사용법: `{command_prefix}pubg-alert-resolution 알림_ID 해결_기록`",
                    parsed_alert_id,
                    detail_base_url=config.app.local_web_base_url,
                ),
                mention_author=False,
            )
            return

        connection = connect_mysql(config.database)
        try:
            try:
                note = add_alert_note(
                    connection,
                    parsed_alert_id,
                    note_text,
                    note_type="resolution",
                    created_by=alert_note_creator_for(ctx),
                )
            except AlertHistoryError as exc:
                await ctx.reply(
                    format_alert_command_reply(
                        f"PUBG AI 알림 해결 기록 저장 오류: {exc}",
                        parsed_alert_id,
                        detail_base_url=config.app.local_web_base_url,
                    ),
                    mention_author=False,
                )
                return
        finally:
            connection.close()

        await ctx.reply(
            format_alert_note_result(note, detail_base_url=config.app.local_web_base_url),
            mention_author=False,
        )

    @bot.hybrid_command(name="pubg-alert-notes", aliases=["pubg-alert-note-list"])
    async def alert_notes_command(
        ctx: Any,
        alert_id: str | None = None,
        limit: str = "5",
    ) -> None:
        if not await require_permission(ctx, "admin"):
            return
        parsed_alert_id = _positive_int(alert_id)
        parsed_limit = _positive_int(limit)
        if parsed_alert_id is None or parsed_limit is None:
            await ctx.reply(
                format_alert_command_reply(
                    f"사용법: `{command_prefix}pubg-alert-notes 알림_ID [표시_개수]`",
                    parsed_alert_id,
                    detail_base_url=config.app.local_web_base_url,
                ),
                mention_author=False,
            )
            return

        connection = connect_mysql(config.database)
        try:
            try:
                record = get_alert_history_record(connection, parsed_alert_id)
                notes = list_alert_notes(connection, parsed_alert_id, limit=min(parsed_limit, 10))
            except AlertHistoryError as exc:
                await ctx.reply(
                    format_alert_command_reply(
                        f"PUBG AI 알림 메모 조회 오류: {exc}",
                        parsed_alert_id,
                        detail_base_url=config.app.local_web_base_url,
                    ),
                    mention_author=False,
                )
                return
        finally:
            connection.close()

        await ctx.reply(
            format_alert_notes_result(
                record,
                notes,
                detail_base_url=config.app.local_web_base_url,
            ),
            mention_author=False,
        )

    @bot.hybrid_command(name="pubg-alert-history", aliases=["pubg-alert-log"])
    async def alert_history_command(ctx: Any, *, filters: str | None = None) -> None:
        if not await require_permission(ctx, "admin"):
            return
        try:
            parsed = _parse_alert_history_filters(filters)
        except ValueError as exc:
            await ctx.reply(
                format_alert_history_command_reply(
                    (
                        f"사용법: `{command_prefix}pubg-alert-history "
                        "[current-errors|worker-failures|storage-pressure|all-history] "
                        "source=all|storage|worker state=all|current|active|acknowledged|snoozed|resolved "
                        "severity=all|error|warning|info|ok search=text limit=5`"
                        f"\nError: {exc}"
                    ),
                    detail_base_url=config.app.local_web_base_url,
                ),
                mention_author=False,
            )
            return

        connection = connect_mysql(config.database)
        try:
            try:
                page = get_alert_history_page(
                    connection,
                    source=str(parsed["source"]),
                    state=str(parsed["state"]),
                    severity=str(parsed["severity"]),
                    sort=str(parsed["sort"]),
                    search=str(parsed["search"]),
                    limit=int(parsed["limit"]),
                    offset=int(parsed["offset"]),
                )
            except AlertHistoryError as exc:
                await ctx.reply(
                    format_alert_history_command_reply(
                        f"PUBG AI 알림 이력 조회 오류: {exc}",
                        source=str(parsed["source"]),
                        state=str(parsed["state"]),
                        severity=str(parsed["severity"]),
                        sort=str(parsed["sort"]),
                        search=str(parsed["search"]),
                        limit=int(parsed["limit"]),
                        offset=int(parsed["offset"]),
                        detail_base_url=config.app.local_web_base_url,
                    ),
                    mention_author=False,
                )
                return
        finally:
            connection.close()

        await ctx.reply(
            format_alert_history_result(
                page,
                detail_base_url=config.app.local_web_base_url,
                command_prefix=command_prefix,
            ),
            mention_author=False,
        )

    @bot.hybrid_command(name="pubg-worker-runs", aliases=["pubg-worker-history", "pubg-worker-log"])
    async def worker_runs_command(ctx: Any, *, filters: str | None = None) -> None:
        if not await require_permission(ctx, "admin"):
            return
        try:
            parsed = _parse_worker_run_filters(filters)
        except ValueError as exc:
            await ctx.reply(
                format_worker_run_history_command_reply(
                    (
                        f"사용법: `{command_prefix}pubg-worker-runs [collector|post_processing|all] "
                        "status=succeeded|failed|all [limit] offset=0 range=last24h|today|yesterday|last7d "
                        "from=2026-07-01T00:00 to=2026-07-02T00:00`"
                        f"\nError: {exc}"
                    ),
                    detail_base_url=config.app.local_web_base_url,
                ),
                mention_author=False,
            )
            return
        worker_name = str(parsed["worker_name"]) if parsed["worker_name"] is not None else None
        status = str(parsed["status"])
        limit = int(parsed["limit"])
        offset = int(parsed["offset"])
        created_from_kst = (
            str(parsed["created_from_kst"]) if parsed["created_from_kst"] is not None else None
        )
        created_to_kst = str(parsed["created_to_kst"]) if parsed["created_to_kst"] is not None else None

        connection = connect_mysql(config.database)
        try:
            try:
                page = get_worker_run_page(
                    connection,
                    worker_name=worker_name,
                    status=status,
                    created_from_kst=created_from_kst,
                    created_to_kst=created_to_kst,
                    limit=limit,
                    offset=offset,
                )
            except WorkerRunHistoryError as exc:
                await ctx.reply(
                    format_worker_run_history_command_reply(
                        f"PUBG AI 자동 작업 이력 조회 오류: {exc}",
                        worker_name=worker_name,
                        status=status,
                        limit=limit,
                        offset=offset,
                        created_from_kst=created_from_kst,
                        created_to_kst=created_to_kst,
                        detail_base_url=config.app.local_web_base_url,
                    ),
                    mention_author=False,
                )
                return
        finally:
            connection.close()

        await ctx.reply(
            format_worker_run_history_result(
                page,
                detail_base_url=config.app.local_web_base_url,
                command_prefix=command_prefix,
            ),
            mention_author=False,
        )

    @bot.hybrid_command(name="pubg-worker-run", aliases=["pubg-worker-run-detail", "pubg-worker-detail"])
    async def worker_run_detail_command(ctx: Any, run_id: str | None = None) -> None:
        if not await require_permission(ctx, "admin"):
            return
        parsed_run_id = _positive_int(run_id)
        if parsed_run_id is None:
            await ctx.reply(
                format_worker_run_command_reply(
                    f"사용법: `{command_prefix}pubg-worker-run 실행_ID`",
                    parsed_run_id,
                    detail_base_url=config.app.local_web_base_url,
                ),
                mention_author=False,
            )
            return

        connection = connect_mysql(config.database)
        try:
            try:
                run = get_worker_run(connection, parsed_run_id)
            except WorkerRunHistoryError as exc:
                await ctx.reply(
                    format_worker_run_command_reply(
                        f"PUBG AI 자동 작업 상세 조회 오류: {exc}",
                        parsed_run_id,
                        detail_base_url=config.app.local_web_base_url,
                    ),
                    mention_author=False,
                )
                return
        finally:
            connection.close()

        await ctx.reply(
            format_worker_run_detail_result(run, detail_base_url=config.app.local_web_base_url),
            mention_author=False,
        )

    async def registered_player_autocomplete(interaction: Any, current: str) -> list[Any]:
        refresh_permission_settings()
        guild_id = str(interaction.guild_id) if interaction.guild_id else None
        identity = DiscordCommandIdentity(user_id=str(interaction.user.id), guild_id=guild_id)
        global_scope = permission_checker.is_global_admin(identity)
        if guild_id is None and not global_scope:
            return []
        namespace = getattr(interaction, "namespace", None)
        shard = str(getattr(namespace, "shard", "") or "").strip().lower()
        if shard not in {"steam", "kakao", "xbox", "psn", "console"}:
            shard = None
        connection = connect_mysql(config.database)
        try:
            players = PlayerRegistry(connection).list_players(
                shard=shard,
                registered_guild_id=None if global_scope else guild_id,
                active_only=False,
                limit=500,
            )
        except Exception:
            return []
        finally:
            connection.close()
        query = str(current or "").strip().casefold()
        matches = [
            player
            for player in players
            if not query
            or query in player.current_name.casefold()
            or query in player.account_id.casefold()
        ]
        matches.sort(
            key=lambda player: (
                not player.current_name.casefold().startswith(query) if query else False,
                not player.active,
                player.current_name.casefold(),
            )
        )
        return [
            app_commands.Choice(
                name=(
                    f"{player.current_name} · {translate_code(player.shard, 'shard')} · "
                    f"{'수집 중' if player.active else '수집 중지'} · ID …{player.account_id[-6:]}"
                )[:100],
                value=player.current_name[:100],
            )
            for player in matches[:25]
        ]

    async def weapon_autocomplete(_interaction: Any, current: str) -> list[Any]:
        query = str(current or "").strip().casefold()
        candidates = [
            (code, label)
            for code, label in DAMAGE_CAUSER_KO.items()
            if code.startswith("Weap") and is_ballistic_weapon(code)
        ]
        candidates = [
            (code, label)
            for code, label in candidates
            if not query or query in code.casefold() or query in label.casefold()
        ]
        candidates.sort(
            key=lambda item: (
                not item[1].casefold().startswith(query) if query else False,
                item[1].casefold(),
            )
        )
        return [
            app_commands.Choice(name=f"{label} · {code}"[:100], value=label[:100])
            for code, label in candidates[:25]
        ]

    async def permission_group_autocomplete(_interaction: Any, current: str) -> list[Any]:
        refresh_permission_settings()
        query = str(current or "").strip().casefold()
        groups = [
            group
            for group in permission_checker.settings.command_groups
            if not query
            or query in group.casefold()
            or query in command_group_label(group).casefold()
        ]
        groups.sort(key=lambda group: (command_group_label(group), group))
        return [
            app_commands.Choice(
                name=f"{command_group_label(group)} · 키 {group}"[:100],
                value=group[:100],
            )
            for group in groups[:25]
        ]

    for player_command, parameter_name in (
        (list_players_command, "name"),
        (player_stats_command, "name"),
        (player_fight_outcomes_command, "name"),
        (player_trends_command, "name"),
        (player_weapon_command, "name"),
        (player_recommendations_command, "name"),
        (player_match_command, "name"),
        (unregister_player_command, "target"),
    ):
        player_command.autocomplete(parameter_name)(registered_player_autocomplete)
    player_weapon_command.autocomplete("weapon")(weapon_autocomplete)
    discord_permission_command.autocomplete("group")(permission_group_autocomplete)

    for spec in DISCORD_COMMAND_SPECS:
        command = bot.get_command(spec.name)
        if command is not None and command.app_command is not None:
            command.app_command.description = spec.description[:100]

    application_command_templates = tuple(bot.tree.get_commands())
    expected_template_names = {spec.name for spec in DISCORD_COMMAND_SPECS}
    actual_template_names = {str(command.name) for command in application_command_templates}
    if actual_template_names != expected_template_names:
        missing = sorted(expected_template_names - actual_template_names)
        unexpected = sorted(actual_template_names - expected_template_names)
        raise RuntimeError(
            "Discord application command catalog mismatch "
            f"(missing={missing}, unexpected={unexpected})."
        )
    return bot


def _short_account_id(account_id: str) -> str:
    if not account_id:
        return "unknown"
    if account_id.startswith("account.") and len(account_id) > 20:
        return f"{account_id[:15]}...{account_id[-4:]}"
    return account_id


def _discord_user_id(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if text.startswith("<@") and text.endswith(">"):
        text = text[2:-1]
        if text.startswith("!"):
            text = text[1:]
    return text if text.isdigit() else None


def _discord_permission_action(value: str | None) -> str | None:
    if value is None:
        return None
    return {
        "allow": "grant",
        "grant": "grant",
        "add": "grant",
        "허용": "grant",
        "deny": "revoke",
        "revoke": "revoke",
        "remove": "revoke",
        "회수": "revoke",
        "해제": "revoke",
    }.get(value.strip().lower())


def _discord_ranking_scope(value: str | None) -> str | None:
    if value is None:
        return None
    return {
        "guild": "guild",
        "server": "guild",
        "서버": "guild",
        "global": "global",
        "all": "global",
        "전체": "global",
    }.get(value.strip().lower())


def _discord_public_profile_default(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"public", "true", "yes", "공개"}:
        return True
    if normalized in {"private", "false", "no", "비공개"}:
        return False
    return None


def _recommendation_evidence_link(
    report: PlayerRecommendationReport,
    item: Any,
    base_url: str | None,
) -> str:
    if not base_url:
        return ""
    query = urlencode(
        {
            "shard": report.player.shard,
            "account_id": report.player.account_id,
            "weapon_code": item.weapon_code,
            "attachment_code": item.attachment_code,
        }
    )
    return f" [근거]({base_url.rstrip('/')}/players/recommendations/weapon-attachment-evidence?{query})"


def _alert_history_detail_link(record: AlertHistoryRecord, base_url: str | None) -> str:
    detail = _alert_history_detail_markdown(record.id, base_url)
    return f" {detail}" if detail else ""


def _alert_history_detail_markdown(alert_id: int, base_url: str | None) -> str:
    if not base_url:
        return ""
    return f"[상세]({base_url.rstrip('/')}/?{urlencode({'alert_id': alert_id})}#alertHistoryDetail)"


def _alert_history_filter_page_link(page: AlertHistoryPage, base_url: str | None) -> str:
    if not base_url:
        return ""
    query = urlencode(
        {
            "alert_history_source": page.source,
            "alert_history_state": page.state,
            "alert_history_severity": page.severity,
            "alert_history_sort": page.sort,
            "alert_history_search": page.search or "",
            "alert_history_limit": page.limit,
            "alert_history_offset": page.offset,
        }
    )
    return f"{base_url.rstrip('/')}/?{query}#alerts"


def _alert_history_export_link(page: AlertHistoryPage, base_url: str | None) -> str:
    if not base_url:
        return ""
    query = urlencode(
        {
            "source": page.source,
            "state": page.state,
            "severity": page.severity,
            "sort": page.sort,
            "search": page.search or "",
            "limit": ALERT_HISTORY_EXPORT_LIMIT,
            "offset": 0,
        }
    )
    return f"{base_url.rstrip('/')}/alerts/history/export.csv?{query}"


def _worker_run_detail_link(run: WorkerRunRecord, base_url: str | None) -> str:
    detail = _worker_run_detail_markdown(run.id, base_url)
    return f" {detail}" if detail else ""


def _worker_run_detail_markdown(run_id: int, base_url: str | None) -> str:
    if not base_url:
        return ""
    return f"[상세]({base_url.rstrip('/')}/?{urlencode({'worker_run_id': run_id})}#workerRunDetail)"


def _worker_run_filter_page_link(page: WorkerRunPage, base_url: str | None) -> str:
    if not base_url:
        return ""
    query = urlencode(
        {
            "worker_run_worker": page.worker_name or "all",
            "worker_run_status": page.status,
            "worker_run_range": "custom",
            "worker_run_from": page.created_from_kst or "",
            "worker_run_to": page.created_to_kst or "",
            "worker_run_limit": page.limit,
            "worker_run_offset": page.offset,
        }
    )
    return f"{base_url.rstrip('/')}/?{query}#worker-runs"


def _worker_run_export_link(page: WorkerRunPage, base_url: str | None) -> str:
    if not base_url:
        return ""
    query = urlencode(
        {
            "worker_name": page.worker_name or "",
            "status": page.status,
            "created_from_kst": page.created_from_kst or "",
            "created_to_kst": page.created_to_kst or "",
            "limit": WORKER_RUN_EXPORT_LIMIT,
            "offset": 0,
        }
    )
    return f"{base_url.rstrip('/')}/workers/runs/export.csv?{query}"


def _alert_history_navigation_hints(page: AlertHistoryPage, *, command_prefix: str) -> list[str]:
    hints: list[str] = []
    if page.offset > 0:
        previous_offset = max(0, page.offset - page.limit)
        hints.append(
            "- 이전: `"
            + _alert_history_command_for_page(page, offset=previous_offset, command_prefix=command_prefix)
            + "`"
        )
    if page.offset + len(page.records) < page.total:
        next_offset = page.offset + page.limit
        hints.append(
            "- 다음: `"
            + _alert_history_command_for_page(page, offset=next_offset, command_prefix=command_prefix)
            + "`"
        )
    return hints


def _alert_history_command_for_page(page: AlertHistoryPage, *, offset: int, command_prefix: str) -> str:
    parts = [
        f"{command_prefix}pubg-alert-history",
        _alert_history_filter_arg("source", page.source),
        _alert_history_filter_arg("state", page.state),
        _alert_history_filter_arg("severity", page.severity),
        _alert_history_filter_arg("sort", page.sort),
        _alert_history_filter_arg("limit", str(page.limit)),
        _alert_history_filter_arg("offset", str(max(0, offset))),
    ]
    if page.search:
        parts.append(_alert_history_filter_arg("search", page.search))
    return " ".join(parts)


def _alert_history_filter_arg(key: str, value: str) -> str:
    return f"{key}={shlex.quote(str(value))}"


def _worker_run_navigation_hints(page: WorkerRunPage, *, command_prefix: str) -> list[str]:
    hints: list[str] = []
    if page.offset > 0:
        previous_offset = max(0, page.offset - page.limit)
        hints.append(
            "- 이전: `"
            + _worker_run_history_command_for_page(page, offset=previous_offset, command_prefix=command_prefix)
            + "`"
        )
    if page.offset + len(page.records) < page.total:
        next_offset = page.offset + page.limit
        hints.append(
            "- 다음: `"
            + _worker_run_history_command_for_page(page, offset=next_offset, command_prefix=command_prefix)
            + "`"
        )
    return hints


def _worker_run_history_filter_labels(page: WorkerRunPage) -> list[str]:
    labels = [
        f"작업={_worker_name_label(page.worker_name or 'all')}",
        f"상태={_worker_status_label(page.status)}",
    ]
    if page.created_from_kst or page.created_to_kst:
        labels.append(
            f"기간={_worker_run_date_filter_value(page.created_from_kst)}.."
            f"{_worker_run_date_filter_value(page.created_to_kst)}"
        )
    return labels


def _worker_run_history_command_for_page(page: WorkerRunPage, *, offset: int, command_prefix: str) -> str:
    parts = [
        f"{command_prefix}pubg-worker-runs",
        _worker_run_filter_arg("worker", page.worker_name or "all"),
        _worker_run_filter_arg("status", page.status),
        _worker_run_filter_arg("limit", str(page.limit)),
        _worker_run_filter_arg("offset", str(max(0, offset))),
    ]
    if page.created_from_kst:
        parts.append(_worker_run_filter_arg("from", page.created_from_kst))
    if page.created_to_kst:
        parts.append(_worker_run_filter_arg("to", page.created_to_kst))
    return " ".join(parts)


def _worker_run_filter_arg(key: str, value: str) -> str:
    return f"{key}={shlex.quote(str(value))}"


def _worker_run_date_filter_value(value: str | None) -> str:
    return value or "-"


def _optional_duration_seconds(value: float | None) -> str:
    return f"{value:.1f}s" if value is not None else "-"


def _worker_run_summary_metrics(summary: dict[str, Any], *, limit: int = 12) -> list[str]:
    metrics = _flatten_worker_run_summary_metrics(summary)
    if len(metrics) <= limit:
        return metrics
    remaining = len(metrics) - limit
    return metrics[:limit] + [f"... {remaining} more"]


def _flatten_worker_run_summary_metrics(
    value: Any,
    *,
    prefix: str = "",
) -> list[str]:
    if not isinstance(value, dict):
        return []

    metrics: list[str] = []
    skipped_keys = {"errors", "started_at_kst", "finished_at_kst", "duration_seconds"}
    for key, nested in value.items():
        if key in skipped_keys:
            continue
        metric_key = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(nested, dict):
            metrics.extend(_flatten_worker_run_summary_metrics(nested, prefix=metric_key))
        elif _is_worker_run_metric_value(nested):
            metrics.append(f"{metric_key}={_worker_run_metric_value(nested)}")
    return metrics


def _is_worker_run_metric_value(value: Any) -> bool:
    return isinstance(value, bool | int | float) or (isinstance(value, str) and len(value) <= 80)


def _worker_run_metric_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def _worker_run_summary_errors(summary: dict[str, Any]) -> list[str]:
    value = summary.get("errors")
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if value:
        return [str(value)]
    return []


def _short_match_id(match_id: str) -> str:
    return match_id[:8] if len(match_id) > 8 else match_id


def _discord_single_line(value: str, max_length: int) -> str:
    normalized = " ".join(str(value).split())
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[: max(0, max_length - 3)]}..."


def _accuracy_metric_text(value: float, metric: Any | None) -> str:
    if metric is None:
        return _percent(value)
    metric_value = getattr(metric, "metric_value", None)
    metric_kind = getattr(metric, "metric_kind", "unavailable")
    if metric_kind == "estimated_hit_rate" and metric_value is not None:
        return f"추정 {_percent(float(metric_value))}"
    if metric_kind == "pellet_hits_per_shell" and metric_value is not None:
        return f"셸당 펠릿 {_number(float(metric_value), 2)}회"
    if metric_kind == "hit_events_per_attack" and metric_value is not None:
        return f"공격당 피격 {_number(float(metric_value), 2)}회"
    return "측정 불가"


def _accuracy_breakdown_text(value: float, breakdown: Any | None) -> str:
    if breakdown is None:
        return _percent(value)
    parts: list[str] = []
    estimated = getattr(breakdown, "estimated_hit_rate", None)
    single_attacks = int(getattr(breakdown, "single_projectile_attacks", 0) or 0)
    if estimated is not None:
        parts.append(f"일반 탄환 추정 {_percent(float(estimated))}")
    elif single_attacks:
        parts.append("일반 탄환 측정 불가")
    pellet_shells = int(getattr(breakdown, "pellet_shells", 0) or 0)
    pellet_ratio = getattr(breakdown, "pellet_hits_per_shell", None)
    if pellet_shells and pellet_ratio is not None:
        parts.append(f"산탄 셸당 {_number(float(pellet_ratio), 2)}회")
    unclassified = int(getattr(breakdown, "unclassified_attacks", 0) or 0)
    if unclassified:
        parts.append(f"분류 제외 {unclassified}회")
    return " · ".join(parts) or "측정 불가"

def _percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def _safe_ratio(numerator: int | float, denominator: int | float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _number(value: float, digits: int) -> str:
    return f"{value:.{digits}f}"


def _minutes(seconds: float) -> str:
    return f"{seconds / 60:.1f}분"


def _optional_minutes(seconds: float | None) -> str:
    return _minutes(seconds) if seconds is not None else "-"


def _distance_km(meters: float) -> str:
    return f"{meters / 1000:.1f}km"


def _optional_distance_km(meters: float | None) -> str:
    return _distance_km(meters) if meters is not None else "-"


def _optional_distance_m(meters: float | None) -> str:
    return f"{meters:.0f}m" if meters is not None else "-"


def _optional_number(value: int | None) -> str:
    return str(value) if value is not None else "-"


def _ranking_score(metric: str, score: float) -> str:
    if metric in {"win_rate", "accuracy", "headshot_hit_rate", "headshot_rate"}:
        return _percent(score)
    if metric in {"kda", "avg_damage"}:
        return _number(score, 2)
    return _number(score, 0)


def _parse_ranking_args(
    metric: str,
    shard_or_limit: str,
    limit_or_scope: str | None,
    scope: str | None,
) -> tuple[str, str, int, bool]:
    metric_value = metric or "kda"
    shard = "steam"
    limit = 10
    global_requested = False

    if _is_scope_token(metric_value):
        metric_value = "kda"
        global_requested = True
    elif _is_shard_token(metric_value):
        shard = metric_value.lower()
        metric_value = "kda"
    elif _is_int_token(metric_value):
        limit = _ranking_limit(metric_value)
        metric_value = "kda"

    for token in [shard_or_limit, limit_or_scope, scope]:
        if not token:
            continue
        if _is_scope_token(token):
            global_requested = True
        elif _is_shard_token(token):
            shard = token.lower()
        elif _is_int_token(token):
            limit = _ranking_limit(token)

    return metric_value, shard, limit, global_requested


def _is_scope_token(value: str) -> bool:
    return value.lower() in {"전체", "global", "all"}


def _is_shard_token(value: str) -> bool:
    return value.lower() in {"steam", "kakao", "psn", "xbox", "console"}


def _is_int_token(value: str) -> bool:
    try:
        int(value)
    except ValueError:
        return False
    return True


def _ranking_limit(value: str) -> int:
    return max(1, min(int(value), 20))


def _positive_int(value: str | int | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _parse_alert_history_filters(raw: str | None) -> dict[str, str | int]:
    filters: dict[str, str | int] = {
        "source": "all",
        "state": "all",
        "severity": "all",
        "sort": "newest",
        "search": "",
        "limit": 5,
        "offset": 0,
    }
    if not raw or not raw.strip():
        return filters

    try:
        tokens = shlex.split(raw)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    search_terms: list[str] = []
    for token in tokens:
        normalized_token = token.strip()
        if not normalized_token:
            continue

        if "=" in normalized_token:
            key, value = normalized_token.split("=", 1)
            _apply_alert_history_filter(filters, key.strip().lower(), value.strip())
            continue

        lowered = normalized_token.lower()
        if lowered in ALERT_HISTORY_PRESETS:
            filters.update(ALERT_HISTORY_PRESETS[lowered])
        elif lowered in ALERT_HISTORY_SOURCES:
            filters["source"] = lowered
        elif lowered in ALERT_HISTORY_STATES:
            filters["state"] = lowered
        elif lowered in ALERT_HISTORY_SEVERITIES:
            filters["severity"] = lowered
        elif lowered in ALERT_HISTORY_SORTS:
            filters["sort"] = lowered
        elif _is_int_token(lowered):
            filters["limit"] = _alert_history_limit(lowered)
        else:
            search_terms.append(normalized_token)

    if search_terms:
        existing_search = str(filters["search"]).strip()
        terms = " ".join(search_terms).strip()
        filters["search"] = f"{existing_search} {terms}".strip() if existing_search else terms
    return filters


def _apply_alert_history_filter(filters: dict[str, str | int], key: str, value: str) -> None:
    lowered = value.strip().lower()
    if key in {"source", "src"}:
        if lowered not in ALERT_HISTORY_SOURCES:
            raise ValueError(f"invalid source: {value}")
        filters["source"] = lowered
    elif key in {"state", "status"}:
        if lowered not in ALERT_HISTORY_STATES:
            raise ValueError(f"invalid state: {value}")
        filters["state"] = lowered
    elif key in {"severity", "sev"}:
        if lowered not in ALERT_HISTORY_SEVERITIES:
            raise ValueError(f"invalid severity: {value}")
        filters["severity"] = lowered
    elif key == "sort":
        if lowered in {"severity-first", "severity_first"}:
            lowered = "severity"
        if lowered not in ALERT_HISTORY_SORTS:
            raise ValueError(f"invalid sort: {value}")
        filters["sort"] = lowered
    elif key in {"search", "q"}:
        filters["search"] = value.strip()
    elif key == "limit":
        filters["limit"] = _alert_history_limit(value)
    elif key == "offset":
        filters["offset"] = _alert_history_offset(value)
    elif key == "preset":
        if lowered not in ALERT_HISTORY_PRESETS:
            raise ValueError(f"invalid preset: {value}")
        filters.update(ALERT_HISTORY_PRESETS[lowered])
    else:
        raise ValueError(f"unknown filter: {key}")


def _alert_history_limit(value: str | int) -> int:
    parsed = _positive_int(value)
    if parsed is None:
        raise ValueError(f"invalid limit: {value}")
    return max(1, min(parsed, 10))


def _alert_history_offset(value: str | int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid offset: {value}") from exc
    return max(0, parsed)


def _alert_history_record_state(record: AlertHistoryRecord) -> str:
    if record.resolved_at_kst:
        return "resolved"
    if record.is_acknowledged():
        return "acknowledged"
    if record.is_snoozed():
        return "snoozed"
    return "current"


def _parse_worker_run_filters(
    raw: str | None,
    *,
    reference_kst: datetime | None = None,
) -> dict[str, str | int | None]:
    filters: dict[str, str | int | None] = {
        "worker_name": None,
        "status": "all",
        "limit": 5,
        "offset": 0,
        "created_from_kst": None,
        "created_to_kst": None,
    }
    if not raw or not raw.strip():
        return filters

    try:
        tokens = shlex.split(raw)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    for token in tokens:
        normalized_token = token.strip()
        if not normalized_token:
            continue

        if "=" in normalized_token:
            key, value = normalized_token.split("=", 1)
            _apply_worker_run_filter(
                filters,
                key.strip().lower(),
                value.strip(),
                reference_kst=reference_kst,
            )
            continue

        lowered = normalized_token.lower()
        if _is_int_token(lowered):
            filters["limit"] = _worker_run_limit(lowered)
        elif lowered in WORKER_RUN_STATUSES:
            filters["status"] = lowered
        else:
            filters["worker_name"] = _worker_run_name(lowered)

    return filters


def _apply_worker_run_filter(
    filters: dict[str, str | int | None],
    key: str,
    value: str,
    *,
    reference_kst: datetime | None = None,
) -> None:
    if key in {"worker", "worker_name", "name"}:
        filters["worker_name"] = _worker_run_name(value)
    elif key in {"status", "state"}:
        filters["status"] = _worker_run_status(value)
    elif key == "limit":
        filters["limit"] = _worker_run_limit(value)
    elif key == "offset":
        filters["offset"] = _worker_run_offset(value)
    elif key in {"from", "since", "start", "created_from", "created_from_kst", "date_from"}:
        filters["created_from_kst"] = _worker_run_datetime_filter(value)
    elif key in {"to", "until", "end", "created_to", "created_to_kst", "date_to"}:
        filters["created_to_kst"] = _worker_run_datetime_filter(value)
    elif key in {"range", "preset", "quick_range", "created_range"}:
        created_from, created_to = _worker_run_range_preset(value, reference_kst=reference_kst)
        filters["created_from_kst"] = created_from
        filters["created_to_kst"] = created_to
    else:
        raise ValueError(f"unknown filter: {key}")


def _worker_run_name(value: str) -> str | None:
    normalized = value.strip().lower().replace("-", "_")
    aliases = {
        "all": None,
        "any": None,
        "collector": "collector",
        "collect": "collector",
        "post": "post_processing",
        "processing": "post_processing",
        "postprocessing": "post_processing",
        "post_processing": "post_processing",
    }
    if normalized not in aliases:
        raise ValueError(f"invalid worker: {value}")
    return aliases[normalized]


def _worker_run_limit(value: str | int) -> int:
    parsed = _positive_int(value)
    if parsed is None:
        raise ValueError(f"invalid limit: {value}")
    return max(1, min(parsed, 10))


def _worker_run_status(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in WORKER_RUN_STATUSES:
        raise ValueError(f"invalid status: {value}")
    return normalized


def _worker_run_offset(value: str | int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid offset: {value}") from exc
    return max(0, parsed)


def _worker_run_datetime_filter(value: str) -> str | None:
    text = str(value).strip()
    return text or None


def _worker_run_range_preset(value: str, *, reference_kst: datetime | None = None) -> tuple[str | None, str | None]:
    normalized = str(value).strip().lower().replace("-", "_")
    aliases = {
        "none": "custom",
        "all": "custom",
        "custom": "custom",
        "1h": "last_1h",
        "last1h": "last_1h",
        "last_1h": "last_1h",
        "hour": "last_1h",
        "last_hour": "last_1h",
        "24h": "last_24h",
        "last24h": "last_24h",
        "last_24h": "last_24h",
        "day": "last_24h",
        "today": "today",
        "yesterday": "yesterday",
        "7d": "last_7d",
        "last7d": "last_7d",
        "last_7d": "last_7d",
        "week": "last_7d",
    }
    preset = aliases.get(normalized)
    if preset is None:
        raise ValueError(f"invalid range preset: {value}")
    if preset == "custom":
        return None, None

    now = to_kst(reference_kst) if reference_kst is not None else now_kst()
    now = now.replace(microsecond=0)
    today_start = now.replace(hour=0, minute=0, second=0)
    if preset == "last_1h":
        return _worker_run_datetime_preset_value(now - timedelta(hours=1)), _worker_run_datetime_preset_value(now)
    if preset == "last_24h":
        return _worker_run_datetime_preset_value(now - timedelta(hours=24)), _worker_run_datetime_preset_value(now)
    if preset == "today":
        return _worker_run_datetime_preset_value(today_start), _worker_run_datetime_preset_value(now)
    if preset == "yesterday":
        yesterday_start = today_start - timedelta(days=1)
        return _worker_run_datetime_preset_value(yesterday_start), _worker_run_datetime_preset_value(today_start)
    if preset == "last_7d":
        return _worker_run_datetime_preset_value(now - timedelta(days=7)), _worker_run_datetime_preset_value(now)
    raise ValueError(f"invalid range preset: {value}")


def _worker_run_datetime_preset_value(value: datetime) -> str:
    return to_kst(value).replace(microsecond=0).isoformat()


def _top_parts(parts: dict[str, int]) -> str:
    if not parts:
        return ""
    ordered = sorted(parts.items(), key=lambda item: (-item[1], item[0]))
    return ", ".join(f"{_part_label(key)} {value}" for key, value in ordered[:4])


def _part_label(value: str) -> str:
    return {
        "head": "머리",
        "torso": "몸통",
        "pelvis": "골반",
        "arm": "팔",
        "leg": "다리",
    }.get(value, value)


def _player_visible_to_scope(
    player: RegisteredPlayer,
    guild_id: str | None,
    global_scope: bool,
) -> bool:
    return global_scope or player.is_registered_in_guild(guild_id)
