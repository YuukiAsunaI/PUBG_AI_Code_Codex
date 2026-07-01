from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode

from pubg_ai.config import RuntimeConfig
from pubg_ai.local_settings import AlertSettings
from pubg_ai.storage_alerts import StorageCapacityAlert, assess_storage_capacity
from pubg_ai.time_utils import isoformat_kst
from pubg_ai.worker_run_history import WorkerRunRecord, get_latest_worker_run_id, list_failed_worker_runs


AlertSource = Literal["storage", "worker"]


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

    return SystemAlertReport(
        alerts=alerts,
        latest_worker_run_id=get_latest_worker_run_id(connection),
    )


def collect_storage_alerts(
    *,
    raw_data_dir: str | Path,
    replay_data_dir: str | Path,
    minimum_free_bytes: int,
) -> list[SystemAlert]:
    alerts: list[SystemAlert] = []
    for role, path in (
        ("raw_data_dir", raw_data_dir),
        ("replay_data_dir", replay_data_dir),
    ):
        capacity = assess_storage_capacity(path, minimum_free_bytes=minimum_free_bytes)
        if capacity.should_notify:
            alerts.append(storage_capacity_alert(role, capacity))
    return alerts


def storage_capacity_alert(role: str, capacity: StorageCapacityAlert) -> SystemAlert:
    free = "unknown" if capacity.free_bytes is None else format_bytes(capacity.free_bytes)
    minimum = format_bytes(capacity.minimum_free_bytes)
    title = f"{role} storage alert"
    message = (
        f"{capacity.message}. path={capacity.path}, free={free}, minimum={minimum}. "
        "Free disk space or change the configured storage path."
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
    title = f"{run.worker_name} worker failed"
    error = run.last_error or "unknown worker error"
    message = (
        f"{error}. finished_at={run.finished_at_kst or '-'}, "
        f"duration={_duration(run.duration_seconds)}, run_id={run.id}."
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
    lines = [f"[PUBG AI Alert] {alert.title}"]
    alert_id = getattr(alert, "id", None)
    if alert_id is not None:
        lines.append(f"- alert_id: {alert_id}")
    lines.extend(
        [
            f"- severity: {alert.severity}",
            f"- {alert.message}",
        ]
    )
    detail_url = worker_run_detail_url(alert, detail_base_url)
    if detail_url:
        lines.append(f"- worker_run_detail: {detail_url}")
    return "\n".join(lines)


def format_alert_report(alerts: list[SystemAlert], *, limit: int = 5) -> str:
    if not alerts:
        return "PUBG AI alerts: no active alerts."
    selected = alerts[: max(1, limit)]
    lines: list[str] = [f"PUBG AI alerts ({len(alerts)})"]
    for alert in selected:
        alert_id = getattr(alert, "id", None)
        prefix = f"#{alert_id} " if alert_id is not None else ""
        lines.append(f"- {prefix}{alert.title}: {alert.message}")
    if len(alerts) > len(selected):
        lines.append(f"- ...and {len(alerts) - len(selected)} more")
    return "\n".join(lines)


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
    return f"{base_url.rstrip('/')}/?{urlencode({'worker_run_id': run_id})}"


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


def _positive_integer_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text.isdigit():
        return ""
    return text if int(text) > 0 else ""


def _duration(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}s"
