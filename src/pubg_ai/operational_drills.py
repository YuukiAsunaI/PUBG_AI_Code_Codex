from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from shutil import disk_usage
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from typing import Any, Callable, Literal, Mapping
import json
from uuid import uuid4

from pubg_ai.collector_worker import (
    CollectorWorkerController,
    CollectorWorkerOptions,
    run_collector_cycle,
)
from pubg_ai.config import RuntimeConfig, SecretConfig
from pubg_ai.database import connect_mysql
from pubg_ai.match_job_processor import MatchJobProcessor
from pubg_ai.pubg_client import PubgApiClient, PubgRetryPolicy
from pubg_ai.storage_alerts import assess_storage_capacity
from pubg_ai.telemetry_job_processor import TelemetryJobProcessor
from pubg_ai.time_utils import now_kst


OPERATIONAL_DRILL_CONTRACT_VERSION = "operational-drill-v1"
OperationalDrillMode = Literal["simulated", "live"]


class OperationalDrillError(RuntimeError):
    """Raised when an operational drill request is invalid or cannot be recorded."""


@dataclass(frozen=True)
class OperationalDrillCheck:
    name: str
    passed: bool
    summary: str
    metrics: dict[str, Any]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OperationalDrillReport:
    contract_version: str
    mode: OperationalDrillMode
    started_at_kst: str
    finished_at_kst: str
    duration_seconds: float
    requested_cycles: int
    passed: bool
    checks: list[OperationalDrillCheck]

    def to_record(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "checks": [check.to_record() for check in self.checks],
            "check_count": len(self.checks),
            "passed_check_count": sum(1 for check in self.checks if check.passed),
        }


@dataclass(frozen=True)
class OperationalDrillRunRecord:
    id: int
    contract_version: str
    mode: str
    status: str
    started_at_kst: str | None
    finished_at_kst: str | None
    duration_seconds: float
    requested_cycles: int
    check_count: int
    passed_check_count: int
    report: dict[str, Any]
    created_at_kst: str | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


ConnectionFactory = Callable[[Any], Any]
CollectorCycleRunner = Callable[..., Any]


def run_operational_drills(
    config: RuntimeConfig,
    *,
    mode: OperationalDrillMode = "simulated",
    cycles: int = 3,
    connection_factory: ConnectionFactory = connect_mysql,
    collector_cycle_runner: CollectorCycleRunner = run_collector_cycle,
) -> OperationalDrillReport:
    if mode not in {"simulated", "live"}:
        raise OperationalDrillError("mode must be simulated or live.")
    if not 2 <= cycles <= 5:
        raise OperationalDrillError("cycles must be between 2 and 5.")

    started = now_kst()
    checks = [
        _run_rate_limit_drill(),
        _run_storage_pressure_drill(),
        _run_worker_restart_drill(config),
    ]
    if mode == "simulated":
        checks.append(_run_simulated_soak(config, cycles=cycles))
    else:
        checks.append(
            _run_live_stale_job_recovery_drill(
                config,
                connection_factory=connection_factory,
            )
        )
        checks.append(
            _run_live_soak(
                config,
                cycles=cycles,
                connection_factory=connection_factory,
                collector_cycle_runner=collector_cycle_runner,
            )
        )
    finished = now_kst()
    return OperationalDrillReport(
        contract_version=OPERATIONAL_DRILL_CONTRACT_VERSION,
        mode=mode,
        started_at_kst=started.isoformat(),
        finished_at_kst=finished.isoformat(),
        duration_seconds=max(0.0, (finished - started).total_seconds()),
        requested_cycles=cycles,
        passed=all(check.passed for check in checks),
        checks=checks,
    )


def record_operational_drill(connection: Any, report: OperationalDrillReport) -> int:
    record = report.to_record()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO operational_drill_runs (
                contract_version,
                mode,
                status,
                started_at_kst,
                finished_at_kst,
                duration_seconds,
                requested_cycles,
                check_count,
                passed_check_count,
                report_json,
                created_at_kst
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                report.contract_version,
                report.mode,
                "passed" if report.passed else "failed",
                _mysql_datetime(report.started_at_kst),
                _mysql_datetime(report.finished_at_kst),
                report.duration_seconds,
                report.requested_cycles,
                record["check_count"],
                record["passed_check_count"],
                json.dumps(record, ensure_ascii=False, separators=(",", ":")),
                now_kst().replace(tzinfo=None),
            ),
        )
        return int(cursor.lastrowid)


def list_operational_drills(connection: Any, *, limit: int = 20) -> list[OperationalDrillRunRecord]:
    bounded_limit = max(1, min(int(limit), 100))
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, contract_version, mode, status, started_at_kst, finished_at_kst,
                   duration_seconds, requested_cycles, check_count, passed_check_count,
                   report_json, created_at_kst
            FROM operational_drill_runs
            ORDER BY id DESC
            LIMIT %s
            """,
            (bounded_limit,),
        )
        rows = list(cursor.fetchall())
    return [_drill_row(row) for row in rows]


def _run_rate_limit_drill() -> OperationalDrillCheck:
    clock = [1000.0]
    sleeps: list[float] = []
    responses = [
        _DrillResponse(
            429,
            headers={
                "X-RateLimit-Limit": "10",
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": "1005",
            },
        ),
        _DrillResponse(
            200,
            headers={
                "X-RateLimit-Limit": "10",
                "X-RateLimit-Remaining": "9",
                "X-RateLimit-Reset": "1060",
            },
        ),
    ]
    request_count = 0

    def request_get(*args: Any, **kwargs: Any) -> _DrillResponse:
        nonlocal request_count
        request_count += 1
        return responses.pop(0)

    def sleeper(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    try:
        result = PubgApiClient(
            "operational-drill-key",
            request_get=request_get,
            sleep_func=sleeper,
            time_func=lambda: clock[0],
            retry_policy=PubgRetryPolicy(max_attempts=3),
        ).lookup_players_by_names("steam", ["Operational_Drill"])
        passed = request_count == 2 and sleeps == [5.25] and result.rate_limit.remaining == 9
        summary = "429 reset header was honored and the retry recovered." if passed else "429 retry contract failed."
        return OperationalDrillCheck(
            name="rate_limit_429_backoff",
            passed=passed,
            summary=summary,
            metrics={
                "request_count": request_count,
                "sleep_seconds": sleeps,
                "remaining_after_recovery": result.rate_limit.remaining,
                "network_used": False,
            },
        )
    except Exception as exc:
        return _failed_check("rate_limit_429_backoff", exc)


def _run_storage_pressure_drill() -> OperationalDrillCheck:
    try:
        with TemporaryDirectory(prefix="pubg-ai-storage-drill-") as temp_dir:
            path = Path(temp_dir)
            observed_free = disk_usage(path).free
            alert = assess_storage_capacity(path, minimum_free_bytes=observed_free + 1)
        passed = (
            alert.severity == "error"
            and alert.should_notify
            and alert.targets == ("local_program", "discord")
        )
        return OperationalDrillCheck(
            name="storage_pressure_alert",
            passed=passed,
            summary=(
                "Synthetic low-space threshold produced local and Discord alert targets."
                if passed
                else "Storage pressure alert contract failed."
            ),
            metrics={
                "severity": alert.severity,
                "should_notify": alert.should_notify,
                "targets": list(alert.targets),
                "configured_storage_mutated": False,
            },
        )
    except Exception as exc:
        return _failed_check("storage_pressure_alert", exc)


def _run_worker_restart_drill(config: RuntimeConfig) -> OperationalDrillCheck:
    state = _DrillServiceState()
    drill_config = _simulation_config(config)
    controller = _drill_controller(drill_config, state)
    options = CollectorWorkerOptions(shard="steam", match_job_limit=2, telemetry_job_limit=2)
    try:
        controller.start(options)
        first_cycle = _wait_for(lambda: controller.status().cycle_count >= 1)
        controller.stop()
        first_stop = _wait_for(lambda: not controller.status().running)
        controller.start(options)
        second_cycle = _wait_for(lambda: controller.status().cycle_count >= 1)
        controller.stop()
        second_stop = _wait_for(lambda: not controller.status().running)
        passed = (
            first_cycle
            and first_stop
            and second_cycle
            and second_stop
            and len(state.history) == 2
            and state.connections_opened == state.connections_closed == 2
        )
        return OperationalDrillCheck(
            name="worker_stop_restart_recovery",
            passed=passed,
            summary=(
                "Collector stopped and restarted with two clean persisted cycle callbacks."
                if passed
                else "Collector stop/restart contract failed."
            ),
            metrics={
                "completed_cycle_callbacks": len(state.history),
                "connections_opened": state.connections_opened,
                "connections_closed": state.connections_closed,
                "first_stop_completed": first_stop,
                "second_stop_completed": second_stop,
                "external_services_used": False,
            },
        )
    except Exception as exc:
        controller.stop()
        _wait_for(lambda: not controller.status().running)
        return _failed_check("worker_stop_restart_recovery", exc)


def _run_simulated_soak(config: RuntimeConfig, *, cycles: int) -> OperationalDrillCheck:
    state = _DrillServiceState()
    drill_config = _simulation_config(config)
    reports = []
    try:
        for _ in range(cycles):
            reports.append(
                run_collector_cycle(
                    drill_config,
                    options=CollectorWorkerOptions(shard="steam", match_job_limit=2, telemetry_job_limit=2),
                    connection_factory=lambda database: _DrillConnection(state),
                    pubg_client_factory=lambda key: _DrillPubgClient(),
                    raw_store_factory=lambda root, compression: _DrillRawStore(),
                    collector_factory=lambda *args, **kwargs: _DrillCollector(state),
                    match_processor_factory=lambda *args, **kwargs: _DrillMatchProcessor(state),
                    telemetry_processor_factory=lambda *args, **kwargs: _DrillTelemetryProcessor(state),
                    history_recorder=lambda connection, worker_name, cycle: state.history.append(cycle),
                )
            )
        error_count = sum(len(report.errors) for report in reports)
        passed = (
            len(reports) == cycles
            and error_count == 0
            and state.match_discoveries == 1
            and state.match_processes == 1
            and state.telemetry_processes == 1
            and state.connections_opened == state.connections_closed == cycles
        )
        return OperationalDrillCheck(
            name="bounded_idempotent_soak",
            passed=passed,
            summary=(
                f"{cycles} bounded cycles completed without duplicate logical work."
                if passed
                else "Bounded simulated soak contract failed."
            ),
            metrics={
                "cycles": len(reports),
                "error_count": error_count,
                "logical_match_discoveries": state.match_discoveries,
                "logical_match_processes": state.match_processes,
                "logical_telemetry_processes": state.telemetry_processes,
                "connections_opened": state.connections_opened,
                "connections_closed": state.connections_closed,
                "external_services_used": False,
            },
        )
    except Exception as exc:
        return _failed_check("bounded_idempotent_soak", exc)


def _run_live_stale_job_recovery_drill(
    config: RuntimeConfig,
    *,
    connection_factory: ConnectionFactory,
) -> OperationalDrillCheck:
    connection: Any | None = None
    transaction_started = False
    try:
        connection = connection_factory(config.database)
        connection.begin()
        transaction_started = True
        stale_timestamp = (now_kst() - timedelta(minutes=30)).replace(tzinfo=None)
        token = uuid4().hex
        match_target = f"operational-drill-{token}-match"
        telemetry_target = f"operational-drill-{token}-telemetry"
        with connection.cursor() as cursor:
            for job_type, target_id in (
                ("match", match_target),
                ("telemetry", telemetry_target),
            ):
                cursor.execute(
                    """
                    INSERT INTO api_fetch_jobs (
                        job_type, shard, target_id, status, attempts,
                        next_run_at_kst, last_error, created_at_kst, updated_at_kst
                    )
                    VALUES (%s, %s, %s, 'running', 2, NULL, NULL, %s, %s)
                    """,
                    (job_type, "steam", target_id, stale_timestamp, stale_timestamp),
                )

        recovered_matches = MatchJobProcessor(
            connection,
            pubg_client=object(),
            raw_store=object(),
        )._recover_stale_running_jobs()
        recovered_telemetry = TelemetryJobProcessor(
            connection,
            raw_store=object(),
        )._recover_stale_running_jobs()

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT target_id, status, attempts, next_run_at_kst, last_error
                FROM api_fetch_jobs
                WHERE target_id IN (%s, %s)
                ORDER BY target_id
                """,
                (match_target, telemetry_target),
            )
            rows = list(cursor.fetchall())

        recovered_rows = len(rows) == 2 and all(
            row.get("status") == "queued"
            and int(row.get("attempts") or 0) == 1
            and row.get("next_run_at_kst") is not None
            and "recovered stale running" in str(row.get("last_error") or "")
            for row in rows
        )

        connection.rollback()
        transaction_started = False
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) AS remaining_rows
                FROM api_fetch_jobs
                WHERE target_id IN (%s, %s)
                """,
                (match_target, telemetry_target),
            )
            remaining_rows = int((cursor.fetchone() or {}).get("remaining_rows") or 0)

        passed = (
            recovered_matches >= 1
            and recovered_telemetry >= 1
            and recovered_rows
            and remaining_rows == 0
        )
        return OperationalDrillCheck(
            name="live_mysql_stale_job_recovery",
            passed=passed,
            summary=(
                "Match and telemetry stale jobs recovered and the drill transaction rolled back."
                if passed
                else "Live MySQL stale-job recovery contract failed."
            ),
            metrics={
                "match_jobs_recovered": recovered_matches,
                "telemetry_jobs_recovered": recovered_telemetry,
                "drill_rows_verified": len(rows),
                "transaction_rolled_back": True,
                "rows_remaining_after_rollback": remaining_rows,
            },
        )
    except Exception as exc:
        if transaction_started and connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass
        return _failed_check("live_mysql_stale_job_recovery", exc)
    finally:
        if connection is not None:
            connection.close()


def _run_live_soak(
    config: RuntimeConfig,
    *,
    cycles: int,
    connection_factory: ConnectionFactory,
    collector_cycle_runner: CollectorCycleRunner,
) -> OperationalDrillCheck:
    if not config.secrets.pubg_api_key:
        return OperationalDrillCheck(
            name="live_bounded_collection_soak",
            passed=False,
            summary="PUBG_API_KEY is not configured for the live drill.",
            metrics={"cycles": 0, "secret_exposed": False},
        )

    try:
        active_shard = _active_drill_shard(config, connection_factory)
        if active_shard is None:
            return OperationalDrillCheck(
                name="live_bounded_collection_soak",
                passed=False,
                summary="No active registered player is available for the live drill.",
                metrics={"cycles": 0, "secret_exposed": False},
            )

        limited_app = replace(
            config.app,
            collector_cycle_player_limit=max(1, min(config.app.player_lookup_chunk_size, 10)),
        )
        limited_config = replace(config, app=limited_app)
        before = _queue_snapshot(config, connection_factory)
        reports = []
        for index in range(cycles):
            reports.append(
                collector_cycle_runner(
                    limited_config,
                    options=CollectorWorkerOptions(
                        shard=active_shard,
                        match_job_limit=10,
                        telemetry_job_limit=5,
                    ),
                )
            )
            if index + 1 < cycles:
                sleep(1.0)
        after = _queue_snapshot(config, connection_factory)
        error_count = sum(len(report.errors) for report in reports)
        passed = (
            len(reports) == cycles
            and error_count == 0
            and after["duplicate_job_groups"] == 0
            and after["running_jobs"] == 0
        )
        return OperationalDrillCheck(
            name="live_bounded_collection_soak",
            passed=passed,
            summary=(
                f"{cycles} live bounded cycles completed without errors or duplicate jobs."
                if passed
                else "Live bounded collection soak found an operational issue."
            ),
            metrics={
                "cycles": len(reports),
                "error_count": error_count,
                "cycle_player_limit": limited_app.collector_cycle_player_limit,
                "selected_shard": active_shard,
                "before": before,
                "after": after,
                "secret_exposed": False,
            },
        )
    except Exception as exc:
        return _failed_check("live_bounded_collection_soak", exc)


def _active_drill_shard(
    config: RuntimeConfig,
    connection_factory: ConnectionFactory,
) -> str | None:
    connection = connection_factory(config.database)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT shard
                FROM registered_players
                WHERE active = 1
                GROUP BY shard
                ORDER BY COUNT(*) DESC, shard ASC
                LIMIT 1
                """
            )
            row = cursor.fetchone() or {}
        shard = str(row.get("shard") or "").strip().lower()
        return shard or None
    finally:
        connection.close()


def _queue_snapshot(config: RuntimeConfig, connection_factory: ConnectionFactory) -> dict[str, int]:
    connection = connection_factory(config.database)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    SUM(status = 'queued') AS queued_jobs,
                    SUM(status = 'running') AS running_jobs,
                    SUM(status = 'failed') AS failed_jobs,
                    SUM(status = 'succeeded') AS succeeded_jobs
                FROM api_fetch_jobs
                """
            )
            status_row = cursor.fetchone() or {}
            cursor.execute(
                """
                SELECT COUNT(*) AS duplicate_job_groups
                FROM (
                    SELECT job_type, COALESCE(shard, ''), COALESCE(target_id, ''), COUNT(*) AS row_count
                    FROM api_fetch_jobs
                    GROUP BY job_type, COALESCE(shard, ''), COALESCE(target_id, '')
                    HAVING COUNT(*) > 1
                ) duplicates
                """
            )
            duplicate_row = cursor.fetchone() or {}
        return {
            "queued_jobs": _integer(status_row.get("queued_jobs")),
            "running_jobs": _integer(status_row.get("running_jobs")),
            "failed_jobs": _integer(status_row.get("failed_jobs")),
            "succeeded_jobs": _integer(status_row.get("succeeded_jobs")),
            "duplicate_job_groups": _integer(duplicate_row.get("duplicate_job_groups")),
        }
    finally:
        connection.close()


def _drill_controller(config: RuntimeConfig, state: "_DrillServiceState") -> CollectorWorkerController:
    return CollectorWorkerController(
        config_loader=lambda: config,
        connection_factory=lambda database: _DrillConnection(state),
        pubg_client_factory=lambda key: _DrillPubgClient(),
        raw_store_factory=lambda root, compression: _DrillRawStore(),
        collector_factory=lambda *args, **kwargs: _DrillCollector(state),
        match_processor_factory=lambda *args, **kwargs: _DrillMatchProcessor(state),
        telemetry_processor_factory=lambda *args, **kwargs: _DrillTelemetryProcessor(state),
        history_recorder=lambda connection, worker_name, cycle: state.history.append(cycle),
    )


def _simulation_config(config: RuntimeConfig) -> RuntimeConfig:
    secrets = config.secrets
    if not secrets.pubg_api_key:
        secrets = SecretConfig(
            pubg_api_key="operational-drill-placeholder",
            discord_bot_token=secrets.discord_bot_token,
        )
    return replace(
        config,
        app=replace(
            config.app,
            collector_poll_interval_seconds=60,
            collector_cycle_player_limit=1,
            player_lookup_chunk_size=1,
        ),
        secrets=secrets,
    )


def _wait_for(predicate: Callable[[], bool], *, timeout_seconds: float = 3.0) -> bool:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        if predicate():
            return True
        sleep(0.01)
    return bool(predicate())


def _failed_check(name: str, exc: Exception) -> OperationalDrillCheck:
    return OperationalDrillCheck(
        name=name,
        passed=False,
        summary=f"{exc.__class__.__name__}: {exc}"[:500],
        metrics={},
    )


def _mysql_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=None)


def _drill_row(row: Mapping[str, Any]) -> OperationalDrillRunRecord:
    report = row.get("report_json")
    if isinstance(report, str):
        try:
            parsed = json.loads(report)
        except json.JSONDecodeError:
            parsed = {}
    elif isinstance(report, Mapping):
        parsed = dict(report)
    else:
        parsed = {}
    return OperationalDrillRunRecord(
        id=_integer(row.get("id")),
        contract_version=str(row.get("contract_version") or ""),
        mode=str(row.get("mode") or ""),
        status=str(row.get("status") or ""),
        started_at_kst=_datetime_text(row.get("started_at_kst")),
        finished_at_kst=_datetime_text(row.get("finished_at_kst")),
        duration_seconds=float(row.get("duration_seconds") or 0.0),
        requested_cycles=_integer(row.get("requested_cycles")),
        check_count=_integer(row.get("check_count")),
        passed_check_count=_integer(row.get("passed_check_count")),
        report=parsed,
        created_at_kst=_datetime_text(row.get("created_at_kst")),
    )


def _datetime_text(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value or "").strip()
    return text or None


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class _DrillResponse:
    def __init__(self, status_code: int, *, headers: Mapping[str, str]) -> None:
        self.status_code = status_code
        self.headers = dict(headers)

    def json(self) -> dict[str, Any]:
        return {"data": []}


@dataclass
class _DrillServiceState:
    match_known: bool = False
    match_processed: bool = False
    telemetry_processed: bool = False
    match_discoveries: int = 0
    match_processes: int = 0
    telemetry_processes: int = 0
    connections_opened: int = 0
    connections_closed: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)


class _DrillConnection:
    def __init__(self, state: _DrillServiceState) -> None:
        self.state = state
        self.closed = False
        state.connections_opened += 1

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.state.connections_closed += 1


class _DrillPubgClient:
    pass


class _DrillRawStore:
    pass


class _DrillResult:
    def __init__(self, record: Mapping[str, Any]) -> None:
        self.record = dict(record)

    def to_record(self) -> dict[str, Any]:
        return dict(self.record)


class _DrillCollector:
    def __init__(self, state: _DrillServiceState) -> None:
        self.state = state

    def collect_active_players(self, *, shard: str | None, limit: int) -> _DrillResult:
        queued = 0
        existing = 1
        if not self.state.match_known:
            self.state.match_known = True
            self.state.match_discoveries += 1
            queued = 1
            existing = 0
        return _DrillResult(
            {
                "active_players": 1,
                "refreshed_players": 1,
                "queued_match_jobs": queued,
                "existing_match_jobs": existing,
                "errors": [],
            }
        )


class _DrillMatchProcessor:
    def __init__(self, state: _DrillServiceState) -> None:
        self.state = state

    def process_queued_matches(self, *, limit: int) -> _DrillResult:
        stored = 0
        if self.state.match_known and not self.state.match_processed:
            self.state.match_processed = True
            self.state.match_processes += 1
            stored = 1
        return _DrillResult(
            {
                "picked_jobs": stored,
                "stored_matches": stored,
                "failed_jobs": 0,
                "requeued_jobs": 0,
            }
        )


class _DrillTelemetryProcessor:
    def __init__(self, state: _DrillServiceState) -> None:
        self.state = state

    def process_queued_telemetry(self, *, limit: int) -> _DrillResult:
        stored = 0
        if self.state.match_processed and not self.state.telemetry_processed:
            self.state.telemetry_processed = True
            self.state.telemetry_processes += 1
            stored = 1
        return _DrillResult(
            {
                "picked_jobs": stored,
                "stored_telemetry": stored,
                "failed_jobs": 0,
                "requeued_jobs": 0,
            }
        )
