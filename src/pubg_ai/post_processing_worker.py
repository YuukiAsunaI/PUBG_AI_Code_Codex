from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from threading import Event, Lock, Thread
from typing import Any, Callable

from pubg_ai.advanced_analysis_processor import AdvancedAnalysisProcessor
from pubg_ai.config import RuntimeConfig
from pubg_ai.database import connect_mysql
from pubg_ai.fight_outcome_processor import FightOutcomeProcessor
from pubg_ai.loadout_snapshot_processor import LoadoutSnapshotProcessor
from pubg_ai.map_snapshot_renderer import MapSnapshotProcessor
from pubg_ai.raw_storage import RawPayloadStore
from pubg_ai.replay_storage import ReplayArtifactStore
from pubg_ai.replay_timeline_builder import ReplayTimelineProcessor
from pubg_ai.telemetry_combat_processor import TelemetryCombatProcessor
from pubg_ai.telemetry_activity_processor import TelemetryActivityProcessor
from pubg_ai.telemetry_item_processor import TelemetryItemProcessor
from pubg_ai.telemetry_movement_processor import TelemetryMovementProcessor
from pubg_ai.time_utils import isoformat_kst, now_kst
from pubg_ai.worker_run_history import record_worker_cycle


class PostProcessingWorkerError(RuntimeError):
    """Raised when the automatic post-processing worker cannot run."""


_STAGE_LABELS = {
    "worker_run_history": "작업 실행 이력",
    "raw_store": "원본 저장소",
    "replay_store": "리플레이 저장소",
    "combat": "전투 처리",
    "activity": "행동 처리",
    "items": "아이템 처리",
    "movement": "이동·좌표 처리",
    "advanced_analysis": "교전·자기장·팀·파밍 분석",
    "loadout_snapshots": "장비 스냅샷 처리",
    "fight_outcomes": "교전 승패 처리",
    "map_snapshots": "2D 지도 생성",
    "replay_timelines": "2D 타임라인 생성",
    "worker": "후처리 작업",
}

_FAILURE_FIELD_LABELS = {
    "failed_payloads": "실패 텔레메트리",
    "failed_matches": "실패 경기",
    "failed_snapshots": "실패 지도",
    "failed_timelines": "실패 타임라인",
}


@dataclass(frozen=True)
class PostProcessingWorkerOptions:
    combat_limit: int = 10
    activity_limit: int = 10
    item_limit: int = 10
    movement_limit: int = 10
    advanced_analysis_limit: int = 10
    loadout_limit: int = 50
    fight_outcome_limit: int = 10
    map_snapshot_limit: int = 10
    timeline_limit: int = 10
    force: bool = False

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PostProcessingCycleResult:
    started_at_kst: str
    finished_at_kst: str
    duration_seconds: float
    poll_interval_seconds: int
    combat: dict[str, Any] | None
    activity: dict[str, Any] | None
    items: dict[str, Any] | None
    movement: dict[str, Any] | None
    advanced_analysis: dict[str, Any] | None
    loadout_snapshots: dict[str, Any] | None
    fight_outcomes: dict[str, Any] | None
    map_snapshots: dict[str, Any] | None
    replay_timelines: dict[str, Any] | None
    errors: list[str]
    options: dict[str, Any]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PostProcessingWorkerState:
    running: bool
    stop_requested: bool
    started_at_kst: str | None
    stopped_at_kst: str | None
    next_run_at_kst: str | None
    cycle_count: int
    options: dict[str, Any] | None
    last_cycle: dict[str, Any] | None
    last_error: str | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


ConfigLoader = Callable[[], RuntimeConfig]
ConnectionFactory = Callable[[Any], Any]
RawStoreFactory = Callable[..., RawPayloadStore]
ReplayStoreFactory = Callable[..., ReplayArtifactStore]
ProcessorFactory = Callable[..., Any]
HistoryRecorder = Callable[[Any, str, dict[str, Any]], Any]


def _record_worker_history(connection: Any, worker_name: str, cycle: dict[str, Any]) -> None:
    record_worker_cycle(connection, worker_name=worker_name, cycle=cycle)


def run_post_processing_cycle(
    config: RuntimeConfig,
    *,
    options: PostProcessingWorkerOptions | None = None,
    connection_factory: ConnectionFactory = connect_mysql,
    raw_store_factory: RawStoreFactory = RawPayloadStore,
    replay_store_factory: ReplayStoreFactory = ReplayArtifactStore,
    combat_processor_factory: ProcessorFactory = TelemetryCombatProcessor,
    activity_processor_factory: ProcessorFactory = TelemetryActivityProcessor,
    item_processor_factory: ProcessorFactory = TelemetryItemProcessor,
    movement_processor_factory: ProcessorFactory = TelemetryMovementProcessor,
    advanced_analysis_processor_factory: ProcessorFactory = AdvancedAnalysisProcessor,
    loadout_processor_factory: ProcessorFactory = LoadoutSnapshotProcessor,
    fight_outcome_processor_factory: ProcessorFactory = FightOutcomeProcessor,
    map_snapshot_processor_factory: ProcessorFactory = MapSnapshotProcessor,
    timeline_processor_factory: ProcessorFactory = ReplayTimelineProcessor,
    history_recorder: HistoryRecorder = _record_worker_history,
) -> PostProcessingCycleResult:
    worker_options = options or PostProcessingWorkerOptions()
    _validate_options(worker_options)
    started = now_kst()
    errors: list[str] = []
    combat: dict[str, Any] | None = None
    activity: dict[str, Any] | None = None
    items: dict[str, Any] | None = None
    movement: dict[str, Any] | None = None
    advanced_analysis: dict[str, Any] | None = None
    loadout_snapshots: dict[str, Any] | None = None
    fight_outcomes: dict[str, Any] | None = None
    map_snapshots: dict[str, Any] | None = None
    replay_timelines: dict[str, Any] | None = None

    def build_result(finished: datetime) -> PostProcessingCycleResult:
        return PostProcessingCycleResult(
            started_at_kst=started.isoformat(),
            finished_at_kst=finished.isoformat(),
            duration_seconds=(finished - started).total_seconds(),
            poll_interval_seconds=config.app.collector_poll_interval_seconds,
            combat=combat,
            activity=activity,
            items=items,
            movement=movement,
            advanced_analysis=advanced_analysis,
            loadout_snapshots=loadout_snapshots,
            fight_outcomes=fight_outcomes,
            map_snapshots=map_snapshots,
            replay_timelines=replay_timelines,
            errors=list(errors),
            options=worker_options.to_record(),
        )

    def finish_cycle() -> PostProcessingCycleResult:
        result = build_result(now_kst())
        try:
            history_recorder(connection, "post_processing", result.to_record())
        except Exception as exc:
            errors.append(_safe_error("worker_run_history", exc))
            result = build_result(now_kst())
        return result

    connection = connection_factory(config.database)
    try:
        try:
            raw_store = raw_store_factory(
                config.app.raw_data_dir,
                compression=config.app.raw_compression,  # type: ignore[arg-type]
            )
        except Exception as exc:
            errors.append(_safe_error("raw_store", exc))
            return finish_cycle()

        try:
            replay_store = replay_store_factory(config.app.replay_data_dir)
        except Exception as exc:
            errors.append(_safe_error("replay_store", exc))
            return finish_cycle()

        try:
            combat = combat_processor_factory(connection, raw_store).process_raw_telemetry(
                limit=worker_options.combat_limit,
                force=worker_options.force,
            ).to_record()
            _append_reported_failures(errors, "combat", combat, "failed_payloads")
        except Exception as exc:
            errors.append(_safe_error("combat", exc))

        try:
            activity = activity_processor_factory(connection, raw_store).process_raw_telemetry(
                limit=worker_options.activity_limit,
                force=worker_options.force,
            ).to_record()
            _append_reported_failures(errors, "activity", activity, "failed_payloads")
        except Exception as exc:
            errors.append(_safe_error("activity", exc))

        try:
            items = item_processor_factory(connection, raw_store).process_raw_telemetry(
                limit=worker_options.item_limit,
                force=worker_options.force,
            ).to_record()
            _append_reported_failures(errors, "items", items, "failed_payloads")
        except Exception as exc:
            errors.append(_safe_error("items", exc))

        try:
            movement = movement_processor_factory(connection, raw_store).process_raw_telemetry(
                limit=worker_options.movement_limit,
                force=worker_options.force,
            ).to_record()
            _append_reported_failures(errors, "movement", movement, "failed_payloads")
        except Exception as exc:
            errors.append(_safe_error("movement", exc))

        try:
            advanced_analysis = advanced_analysis_processor_factory(
                connection,
                raw_store,
            ).process_raw_telemetry(
                limit=worker_options.advanced_analysis_limit,
                force=worker_options.force,
            ).to_record()
            _append_reported_failures(
                errors,
                "advanced_analysis",
                advanced_analysis,
                "failed_payloads",
            )
        except Exception as exc:
            errors.append(_safe_error("advanced_analysis", exc))

        try:
            loadout_snapshots = loadout_processor_factory(connection).process_matches(
                limit=worker_options.loadout_limit,
                force=worker_options.force,
            ).to_record()
            _append_reported_failures(errors, "loadout_snapshots", loadout_snapshots, "failed_matches")
        except Exception as exc:
            errors.append(_safe_error("loadout_snapshots", exc))

        try:
            fight_outcomes = fight_outcome_processor_factory(connection, raw_store).process_raw_telemetry(
                limit=worker_options.fight_outcome_limit,
                force=worker_options.force,
            ).to_record()
            _append_reported_failures(errors, "fight_outcomes", fight_outcomes, "failed_payloads")
        except Exception as exc:
            errors.append(_safe_error("fight_outcomes", exc))

        try:
            map_snapshots = map_snapshot_processor_factory(connection, replay_store).generate_player_snapshots(
                limit=worker_options.map_snapshot_limit,
                force=worker_options.force,
            ).to_record()
            _append_reported_failures(errors, "map_snapshots", map_snapshots, "failed_snapshots")
        except Exception as exc:
            errors.append(_safe_error("map_snapshots", exc))

        try:
            replay_timelines = timeline_processor_factory(connection, replay_store).generate_player_timelines(
                limit=worker_options.timeline_limit,
                force=worker_options.force,
            ).to_record()
            _append_reported_failures(errors, "replay_timelines", replay_timelines, "failed_timelines")
        except Exception as exc:
            errors.append(_safe_error("replay_timelines", exc))

        return finish_cycle()
    finally:
        connection.close()


class PostProcessingWorkerController:
    def __init__(
        self,
        *,
        config_loader: ConfigLoader,
        connection_factory: ConnectionFactory = connect_mysql,
        raw_store_factory: RawStoreFactory = RawPayloadStore,
        replay_store_factory: ReplayStoreFactory = ReplayArtifactStore,
        combat_processor_factory: ProcessorFactory = TelemetryCombatProcessor,
        activity_processor_factory: ProcessorFactory = TelemetryActivityProcessor,
        item_processor_factory: ProcessorFactory = TelemetryItemProcessor,
        movement_processor_factory: ProcessorFactory = TelemetryMovementProcessor,
        advanced_analysis_processor_factory: ProcessorFactory = AdvancedAnalysisProcessor,
        loadout_processor_factory: ProcessorFactory = LoadoutSnapshotProcessor,
        fight_outcome_processor_factory: ProcessorFactory = FightOutcomeProcessor,
        map_snapshot_processor_factory: ProcessorFactory = MapSnapshotProcessor,
        timeline_processor_factory: ProcessorFactory = ReplayTimelineProcessor,
        history_recorder: HistoryRecorder = _record_worker_history,
    ) -> None:
        self._config_loader = config_loader
        self._connection_factory = connection_factory
        self._raw_store_factory = raw_store_factory
        self._replay_store_factory = replay_store_factory
        self._combat_processor_factory = combat_processor_factory
        self._activity_processor_factory = activity_processor_factory
        self._item_processor_factory = item_processor_factory
        self._movement_processor_factory = movement_processor_factory
        self._advanced_analysis_processor_factory = advanced_analysis_processor_factory
        self._loadout_processor_factory = loadout_processor_factory
        self._fight_outcome_processor_factory = fight_outcome_processor_factory
        self._map_snapshot_processor_factory = map_snapshot_processor_factory
        self._timeline_processor_factory = timeline_processor_factory
        self._history_recorder = history_recorder
        self._lock = Lock()
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._state = PostProcessingWorkerState(
            running=False,
            stop_requested=False,
            started_at_kst=None,
            stopped_at_kst=None,
            next_run_at_kst=None,
            cycle_count=0,
            options=None,
            last_cycle=None,
            last_error=None,
        )

    def start(self, options: PostProcessingWorkerOptions | None = None) -> PostProcessingWorkerState:
        worker_options = options or PostProcessingWorkerOptions()
        _validate_options(worker_options)
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self._state

            self._stop_event = Event()
            self._state = PostProcessingWorkerState(
                running=True,
                stop_requested=False,
                started_at_kst=isoformat_kst(),
                stopped_at_kst=None,
                next_run_at_kst=None,
                cycle_count=0,
                options=worker_options.to_record(),
                last_cycle=self._state.last_cycle,
                last_error=None,
            )
            self._thread = Thread(
                target=self._run_loop,
                args=(worker_options,),
                name="pubg-ai-post-processing-worker",
                daemon=True,
            )
            self._thread.start()
            return self._state

    def stop(self) -> PostProcessingWorkerState:
        with self._lock:
            self._stop_event.set()
            if self._state.running:
                self._state = PostProcessingWorkerState(
                    running=self._state.running,
                    stop_requested=True,
                    started_at_kst=self._state.started_at_kst,
                    stopped_at_kst=self._state.stopped_at_kst,
                    next_run_at_kst=self._state.next_run_at_kst,
                    cycle_count=self._state.cycle_count,
                    options=self._state.options,
                    last_cycle=self._state.last_cycle,
                    last_error=self._state.last_error,
                )
            return self._state

    def status(self) -> PostProcessingWorkerState:
        with self._lock:
            return self._state

    def _run_loop(self, options: PostProcessingWorkerOptions) -> None:
        try:
            while not self._stop_event.is_set():
                config = self._config_loader()
                try:
                    cycle = run_post_processing_cycle(
                        config,
                        options=options,
                        connection_factory=self._connection_factory,
                        raw_store_factory=self._raw_store_factory,
                        replay_store_factory=self._replay_store_factory,
                        combat_processor_factory=self._combat_processor_factory,
                        activity_processor_factory=self._activity_processor_factory,
                        item_processor_factory=self._item_processor_factory,
                        movement_processor_factory=self._movement_processor_factory,
                        advanced_analysis_processor_factory=self._advanced_analysis_processor_factory,
                        loadout_processor_factory=self._loadout_processor_factory,
                        fight_outcome_processor_factory=self._fight_outcome_processor_factory,
                        map_snapshot_processor_factory=self._map_snapshot_processor_factory,
                        timeline_processor_factory=self._timeline_processor_factory,
                        history_recorder=self._history_recorder,
                    )
                    self._record_cycle(cycle)
                except Exception as exc:
                    self._record_error(exc)

                if self._stop_event.is_set():
                    break

                interval = max(60, min(config.app.collector_poll_interval_seconds, 300))
                self._record_next_run(now_kst().timestamp() + interval)
                _interruptible_sleep(interval, self._stop_event)
        finally:
            self._mark_stopped()

    def _record_cycle(self, cycle: PostProcessingCycleResult) -> None:
        with self._lock:
            self._state = PostProcessingWorkerState(
                running=True,
                stop_requested=False,
                started_at_kst=self._state.started_at_kst,
                stopped_at_kst=None,
                next_run_at_kst=None,
                cycle_count=self._state.cycle_count + 1,
                options=self._state.options,
                last_cycle=cycle.to_record(),
                last_error="; ".join(cycle.errors) if cycle.errors else None,
            )

    def _record_error(self, exc: Exception) -> None:
        with self._lock:
            self._state = PostProcessingWorkerState(
                running=True,
                stop_requested=False,
                started_at_kst=self._state.started_at_kst,
                stopped_at_kst=None,
                next_run_at_kst=None,
                cycle_count=self._state.cycle_count,
                options=self._state.options,
                last_cycle=self._state.last_cycle,
                last_error=_safe_error("worker", exc),
            )

    def _record_next_run(self, next_run_epoch: float) -> None:
        next_run = datetime.fromtimestamp(next_run_epoch, tz=now_kst().tzinfo)
        with self._lock:
            self._state = PostProcessingWorkerState(
                running=True,
                stop_requested=False,
                started_at_kst=self._state.started_at_kst,
                stopped_at_kst=None,
                next_run_at_kst=next_run.isoformat(),
                cycle_count=self._state.cycle_count,
                options=self._state.options,
                last_cycle=self._state.last_cycle,
                last_error=self._state.last_error,
            )

    def _mark_stopped(self) -> None:
        with self._lock:
            self._state = PostProcessingWorkerState(
                running=False,
                stop_requested=False,
                started_at_kst=self._state.started_at_kst,
                stopped_at_kst=isoformat_kst(),
                next_run_at_kst=None,
                cycle_count=self._state.cycle_count,
                options=self._state.options,
                last_cycle=self._state.last_cycle,
                last_error=self._state.last_error,
            )


def _validate_options(options: PostProcessingWorkerOptions) -> None:
    for label, value, maximum in (
        ("combat_limit", options.combat_limit, 200),
        ("activity_limit", options.activity_limit, 200),
        ("item_limit", options.item_limit, 200),
        ("movement_limit", options.movement_limit, 200),
        ("advanced_analysis_limit", options.advanced_analysis_limit, 200),
        ("loadout_limit", options.loadout_limit, 500),
        ("fight_outcome_limit", options.fight_outcome_limit, 200),
        ("map_snapshot_limit", options.map_snapshot_limit, 200),
        ("timeline_limit", options.timeline_limit, 200),
    ):
        if not 1 <= int(value) <= maximum:
            raise PostProcessingWorkerError(f"{label} must be between 1 and {maximum}.")


def _interruptible_sleep(seconds: int, stop_event: Event) -> None:
    stop_event.wait(timeout=max(0, seconds))


def _append_reported_failures(
    errors: list[str],
    stage: str,
    result: dict[str, Any],
    *fields: str,
) -> None:
    failures = [
        f"{_FAILURE_FIELD_LABELS.get(field, field)} {int(result.get(field) or 0)}건"
        for field in fields
        if int(result.get(field) or 0) > 0
    ]
    if failures:
        summary = f"{_STAGE_LABELS.get(stage, stage)}: " + ", ".join(failures)
        details = result.get("failure_details")
        if isinstance(details, list) and details and isinstance(details[0], dict):
            first = details[0]
            summary += (
                f"; 첫 원인={first.get('error_type') or 'Error'}: "
                f"{first.get('message') or '원인 정보 없음'}"
            )
        errors.append(summary[:1000])


def _safe_error(stage: str, exc: Exception) -> str:
    return f"{_STAGE_LABELS.get(stage, stage)}: {exc.__class__.__name__}: {exc}"[:1000]
