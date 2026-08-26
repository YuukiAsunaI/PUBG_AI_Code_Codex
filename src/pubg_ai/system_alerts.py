from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, Mapping
from urllib.parse import urlencode

from pubg_ai.config import RuntimeConfig
from pubg_ai.local_settings import AlertSettings
from pubg_ai.storage_alerts import StorageCapacityAlert, assess_storage_capacity
from pubg_ai.time_utils import isoformat_kst
from pubg_ai.worker_run_history import WorkerRunRecord, get_latest_worker_run_id, list_failed_worker_runs
from pubg_ai.watchlist import pending_watchlist_alerts


AlertSource = Literal["storage", "worker", "watchlist"]

_STORAGE_ROLE_LABELS = {
    "raw_data_dir": "원본 매치 데이터",
    "replay_data_dir": "2D 리플레이",
    "backup_data_dir": "백업",
    "quarantine_data_dir": "격리",
}
_WORKER_LABELS = {
    "collector": "자동 수집기",
    "post_processing": "자동 후처리기",
}
_STORAGE_REASON_LABELS = {
    "storage path does not exist": "저장 경로가 존재하지 않습니다",
    "storage path is not a directory": "저장 경로가 폴더가 아닙니다",
    "storage free space is below the configured minimum; raw files must be preserved": (
        "저장소 여유 공간이 설정된 최소 기준보다 부족합니다. 원본 파일은 계속 보존됩니다"
    ),
    "storage capacity is available": "저장소 용량을 사용할 수 있습니다",
}


@dataclass(frozen=True)
class SystemAlert:
    key: str
    source: AlertSource
    severity: str
    title: str
    message: str
    created_at_kst: str
    source_id: int | None = None
    metadata: dict[str, Any] | None = None

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["metadata"] = self.metadata or {}
        return record


@dataclass(frozen=True)
class SystemAlertReport:
    alerts: list[SystemAlert]
    latest_worker_run_id: int

    def to_record(self) -> dict[str, Any]:
        return {
            "alerts": [alert.to_record() for alert in self.alerts],
            "latest_worker_run_id": self.latest_worker_run_id,
        }


def collect_system_alerts(
    *,
    config: RuntimeConfig,
    connection: Any,
    settings: AlertSettings,
    after_worker_run_id: int | None = None,
    worker_limit: int = 20,
) -> SystemAlertReport:
    alerts: list[SystemAlert] = []
    if settings.storage_alerts_enabled:
        alerts.extend(
            collect_storage_alerts(
                raw_data_dir=config.app.raw_data_dir,
                replay_data_dir=config.app.replay_data_dir,
                backup_data_dir=config.app.backup_data_dir,
                quarantine_data_dir=config.app.quarantine_data_dir,
                minimum_free_bytes=settings.minimum_free_bytes,
            )
        )

    if settings.worker_error_alerts_enabled:
        runs = list_failed_worker_runs(
            connection,
            after_id=after_worker_run_id,
            limit=worker_limit,
            ascending=after_worker_run_id is not None,
        )
        alerts.extend(worker_run_alert(run) for run in runs)

    alerts.extend(pending_watchlist_alerts(connection, limit=100))

    return SystemAlertReport(
        alerts=alerts,
        latest_worker_run_id=get_latest_worker_run_id(connection),
    )


def collect_storage_alerts(
    *,
    raw_data_dir: str | Path,
    replay_data_dir: str | Path,
    backup_data_dir: str | Path,
    quarantine_data_dir: str | Path,
    minimum_free_bytes: int,
) -> list[SystemAlert]:
    alerts: list[SystemAlert] = []
    for role, path in (
        ("raw_data_dir", raw_data_dir),
        ("replay_data_dir", replay_data_dir),
        ("backup_data_dir", backup_data_dir),
        ("quarantine_data_dir", quarantine_data_dir),
    ):
        capacity = assess_storage_capacity(path, minimum_free_bytes=minimum_free_bytes)
        if capacity.should_notify:
            alerts.append(storage_capacity_alert(role, capacity))
    return alerts


def storage_capacity_alert(role: str, capacity: StorageCapacityAlert) -> SystemAlert:
    free = "알 수 없음" if capacity.free_bytes is None else format_bytes(capacity.free_bytes)
    minimum = format_bytes(capacity.minimum_free_bytes)
    title = f"{_STORAGE_ROLE_LABELS.get(role, role)} 저장소 경고"
    reason = _STORAGE_REASON_LABELS.get(capacity.message, capacity.message)
    message = (
        f"{reason}. 경로={capacity.path}, 여유 공간={free}, 최소 기준={minimum}. "
        "디스크 공간을 확보하거나 설정된 저장 경로를 변경하세요."
    )
    return SystemAlert(
        key=f"storage:{role}:{capacity.path}:{capacity.message}",
        source="storage",
        severity=capacity.severity,
        title=title,
        message=message,
        created_at_kst=isoformat_kst(),
        metadata={
            "role": role,
            "path": capacity.path,
            "free_bytes": capacity.free_bytes,
            "minimum_free_bytes": capacity.minimum_free_bytes,
        },
    )


def worker_run_alert(run: WorkerRunRecord) -> SystemAlert:
    title = f"{_WORKER_LABELS.get(run.worker_name, run.worker_name)} 작업 실패"
    error = run.last_error or "알 수 없는 작업 오류"
    message = (
        f"{error}. 종료 시각={run.finished_at_kst or '-'}, "
        f"소요 시간={_duration(run.duration_seconds)}, 작업 ID={run.id}."
    )
    return SystemAlert(
        key=f"worker:{run.id}",
        source="worker",
        severity="error",
        title=title,
        message=message,
        created_at_kst=run.finished_at_kst or run.created_at_kst or isoformat_kst(),
        source_id=run.id,
        metadata={
            "worker_name": run.worker_name,
            "run_id": run.id,
            "error_count": run.error_count,
            "summary": run.summary,
        },
    )


def format_discord_alert(alert: SystemAlert, *, detail_base_url: str | None = None) -> str:
    lines = [f"[PUBG AI 알림] {alert_display_title(alert)}"]
    alert_id = getattr(alert, "id", None)
    if alert_id is not None:
        lines.append(f"- 알림 ID: {alert_id}")
    lines.extend(
        [
            f"- 심각도: {_severity_label(alert.severity)}",
            f"- {alert_display_message(alert)}",
        ]
    )
    alert_detail_url = alert_history_detail_url(alert, detail_base_url)
    if alert_detail_url:
        lines.append(f"- 알림 상세: {alert_detail_url}")
    detail_url = worker_run_detail_url(alert, detail_base_url)
    if detail_url:
        lines.append(f"- 작업 상세: {detail_url}")
    return "\n".join(lines)


def format_alert_report(
    alerts: list[SystemAlert],
    *,
    limit: int = 5,
    detail_base_url: str | None = None,
) -> str:
    if not alerts:
        return "PUBG AI 알림: 현재 활성 경고가 없습니다."
    selected = alerts[: max(1, limit)]
    lines: list[str] = [f"PUBG AI 알림 ({len(alerts)}건)"]
    alerts_url = current_alerts_url(detail_base_url)
    if alerts_url:
        lines.append(f"- 현재 경고: [열기]({alerts_url})")
    for alert in selected:
        alert_id = getattr(alert, "id", None)
        prefix = f"#{alert_id} " if alert_id is not None else ""
        lines.append(
            f"- {prefix}{alert_display_title(alert)}: {alert_display_message(alert)}"
        )
    if len(alerts) > len(selected):
        lines.append(f"- 그 외 {len(alerts) - len(selected)}건")
    return "\n".join(lines)


def alert_display_title(alert: Any) -> str:
    source = str(_alert_value(alert, "source") or "")
    metadata = _alert_metadata(alert)
    title = str(_alert_value(alert, "title") or "")
    if source == "storage":
        role = str(metadata.get("role") or "")
        return f"{_STORAGE_ROLE_LABELS.get(role, role or '저장소')} 저장소 경고"
    if source == "worker":
        worker_name = str(metadata.get("worker_name") or "")
        if not worker_name and title.endswith(" worker failed"):
            worker_name = title.removesuffix(" worker failed")
        return f"{_WORKER_LABELS.get(worker_name, worker_name or '백그라운드')} 작업 실패"
    return title


def alert_display_message(alert: Any) -> str:
    source = str(_alert_value(alert, "source") or "")
    metadata = _alert_metadata(alert)
    message = str(_alert_value(alert, "message") or "")
    if source == "storage":
        reason = next(
            (translated for raw, translated in _STORAGE_REASON_LABELS.items() if raw in message),
            "저장소 상태를 확인해야 합니다",
        )
        path = str(metadata.get("path") or "").strip()
        free_bytes = metadata.get("free_bytes")
        minimum_bytes = metadata.get("minimum_free_bytes")
        details = [f"경로={path}"] if path else []
        if free_bytes is not None:
            details.append(f"여유 공간={format_bytes(int(free_bytes))}")
        if minimum_bytes is not None:
            details.append(f"최소 기준={format_bytes(int(minimum_bytes))}")
        suffix = f". {', '.join(details)}" if details else ""
        return f"{reason}{suffix}. 디스크 공간 또는 저장 경로 설정을 확인하세요."
    if source == "worker":
        return (
            message.replace("unknown worker error", "알 수 없는 작업 오류")
            .replace("finished_at=", "종료 시각=")
            .replace("duration=", "소요 시간=")
            .replace("run_id=", "작업 ID=")
        )
    return message


def _alert_value(alert: Any, name: str) -> Any:
    if isinstance(alert, Mapping):
        return alert.get(name)
    return getattr(alert, name, None)


def _alert_metadata(alert: Any) -> dict[str, Any]:
    metadata = _alert_value(alert, "metadata")
    return dict(metadata) if isinstance(metadata, Mapping) else {}


def _severity_label(value: Any) -> str:
    return {
        "error": "오류",
        "warning": "경고",
        "info": "정보",
        "ok": "정상",
    }.get(str(value or "").lower(), str(value or "알 수 없음"))


def format_bytes(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    amount = float(max(0, value))
    index = 0
    while amount >= 1024 and index < len(units) - 1:
        amount /= 1024
        index += 1
    precision = 0 if index == 0 else 1
    return f"{amount:.{precision}f} {units[index]}"


def worker_run_detail_url(alert: SystemAlert, base_url: str | None) -> str:
    run_id = _worker_run_id(alert)
    if not base_url or not run_id:
        return ""
    return f"{base_url.rstrip('/')}/?{urlencode({'worker_run_id': run_id})}#workerRunDetail"


def current_alerts_url(base_url: str | None) -> str:
    if not base_url:
        return ""
    query = urlencode(
        {
            "alert_history_source": "all",
            "alert_history_state": "current",
            "alert_history_severity": "all",
            "alert_history_sort": "severity",
            "alert_history_search": "",
            "alert_history_limit": 50,
            "alert_history_offset": 0,
        }
    )
    return f"{base_url.rstrip('/')}/?{query}#alerts"


def _worker_run_id(alert: SystemAlert) -> str:
    if getattr(alert, "source", None) != "worker":
        return ""
    metadata = getattr(alert, "metadata", None) or {}
    for value in (
        metadata.get("run_id") if isinstance(metadata, dict) else None,
        metadata.get("worker_run_id") if isinstance(metadata, dict) else None,
        getattr(alert, "source_id", None),
    ):
        parsed = _positive_integer_text(value)
        if parsed:
            return parsed
    key = str(getattr(alert, "alert_key", "") or getattr(alert, "key", ""))
    if key.startswith("worker:"):
        return _positive_integer_text(key.split(":", 1)[1])
    return ""


def alert_history_detail_url(alert: SystemAlert, base_url: str | None) -> str:
    alert_id = _alert_history_id(alert)
    if not base_url or not alert_id:
        return ""
    return f"{base_url.rstrip('/')}/?{urlencode({'alert_id': alert_id})}#alertHistoryDetail"


def _alert_history_id(alert: SystemAlert) -> str:
    return _positive_integer_text(getattr(alert, "id", None))


def _positive_integer_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text.isdigit():
        return ""
    return text if int(text) > 0 else ""


def _duration(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}s"
