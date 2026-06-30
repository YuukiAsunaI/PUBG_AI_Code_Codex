from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from threading import Event, Lock, Thread
from time import sleep
from typing import Any, Callable

from pubg_ai.config import RuntimeConfig
from pubg_ai.database import connect_mysql
from pubg_ai.loadout_snapshot_processor import LoadoutSnapshotProcessor
from pubg_ai.map_snapshot_renderer import MapSnapshotProcessor
from pubg_ai.raw_storage import RawPayloadStore
from pubg_ai.replay_storage import ReplayArtifactStore
from pubg_ai.replay_timeline_builder import ReplayTimelineProcessor
from pubg_ai.telemetry_combat_processor import TelemetryCombatProcessor
from pubg_ai.telemetry_item_processor import TelemetryItemProcessor
from pubg_ai.telemetry_movement_processor import TelemetryMovementProcessor
from pubg_ai.time_utils import isoformat_kst, now_kst


class PostProcessingWorkerError(RuntimeError):
    """Raised when the automatic post-processing worker cannot run."""


@dataclass(frozen=True)
class PostProcessingWorkerOptions:
    combat_limit: int = 10
    item_limit: int = 10
    movement_limit: int = 10
    loadout_limit: int = 50
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
    items: dict[str, Any] | None
    movement: dict[str, Any] | None
    loadout_snapshots: dict[str, Any] | None
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


def run_post_processing_cycle(
    config: RuntimeConfig,
    *,
    options: PostProcessingWorkerOptions | None = None,
    connection_factory: ConnectionFactory = connect_mysql,
    raw_store_factory: RawStoreFactory = RawPayloadStore,
    replay_store_factory: ReplayStoreFactory = ReplayArtifactStore,
    combat_processor_factory: ProcessorFactory = TelemetryCombatProcessor,
    item_processor_factory: ProcessorFactory = TelemetryItemProcessor,
    movement_processor_factory: ProcessorFactory = TelemetryMovementProcessor,
    loadout_processor_factory: ProcessorFactory = LoadoutSnapshotProcessor,
    map_snapshot_processor_factory: ProcessorFactory = MapSnapshotProcessor,
    timeline_processor_factory: ProcessorFactory = ReplayTimelineProcessor,
) -> PostProcessingCycleResult:
    worker_options = options or PostProcessingWorkerOptions()
    _validate_options(worker_options)
    started = now_kst()
    errors: list[str] = []
    combat: dict[str, Any] | None = None
    items: dict[str, Any] | None = None
    movement: dict[str, Any] | None = None
    loadout_snapshots: dict[str, Any] | None = None
    map_snapshots: dict[str, Any] | None = None
    replay_timelines: dict[str, Any] | None = None

    connection = connection_factory(config.database)
    try:
        raw_store = raw_store_factory(
            config.app.raw_data_dir,
            compression=config.app.raw_compression,  # type: ignore[arg-type]
        )
        replay_store = replay_store_factory(config.app.replay_data_dir)

        try:
            combat = combat_processor_factory(connection, raw_store).process_raw_telemetry(
                limit=worker_options.combat_limit,
                force=worker_options.force,
            ).to_record()
        except Exception as exc:
            errors.append(_safe_error("combat", exc))

        try:
            items = item_processor_factory(connection, raw_store).process_raw_telemetry(
                limit=worker_options.item_limit,
                force=worker_options.force,
            ).to_record()
        except Exception as exc:
            errors.append(_safe_error("items", exc))

        try:
            movement = movement_processor_factory(connection, raw_store).process_raw_telemetry(
                limit=worker_options.movement_limit,
                force=worker_options.force,
            ).to_record()
        except Exception as exc:
            errors.append(_safe_error("movement", exc))

        try:
            loadout_snapshots = loadout_processor_factory(connection).process_matches(
                limit=worker_options.loadout_limit,
                force=worker_options.force,
            ).to_record()
        except Exception as exc:
            errors.append(_safe_error("loadout_snapshots", exc))

        try:
            map_snapshots = map_snapshot_processor_factory(connection, replay_store).generate_player_snapshots(
                limit=worker_options.map_snapshot_limit,
                force=worker_options.force,
            ).to_record()
        except Exception as exc:
            errors.append(_safe_error("map_snapshots", exc))

        try:
            replay_timelines = timeline_processor_factory(connection, replay_store).generate_player_timelines(
                limit=worker_options.timeline_limit,
                force=worker_options.force,
            ).to_record()
        except Exception as exc:
            errors.append(_safe_error("replay_timelines", exc))
    finally:
        connection.close()

    finished = now_kst()
    return PostProcessingCycleResult(
        started_at_kst=started.isoformat(),
        finished_at_kst=finished.isoformat(),
        duration_seconds=(finished - started).total_seconds(),
        poll_interval_seconds=config.app.collector_poll_interval_seconds,
        combat=combat,
        items=items,
        movement=movement,
        loadout_snapshots=loadout_snapshots,
        map_snapshots=map_snapshots,
        replay_timelines=replay_timelines,
        errors=errors,
        options=worker_options.to_record(),
    )


class PostProcessingWorkerController:
    def __init__(
        self,
        *,
        config_loader: ConfigLoader,
        connection_factory: ConnectionFactory = connect_mysql,
        raw_store_factory: RawStoreFactory = RawPayloadStore,
        replay_store_factory: ReplayStoreFactory = ReplayArtifactStore,
        combat_processor_factory: ProcessorFactory = TelemetryCombatProcessor,
        item_processor_factory: ProcessorFactory = TelemetryItemProcessor,
        movement_processor_factory: ProcessorFactory = TelemetryMovementProcessor,
        loadout_processor_factory: ProcessorFactory = LoadoutSnapshotProcessor,
        map_snapshot_processor_factory: ProcessorFactory = MapSnapshotProcessor,
        timeline_processor_factory: ProcessorFactory = ReplayTimelineProcessor,
    ) -> None:
        self._config_loader = config_loader
        self._connection_factory = connection_factory
        self._raw_store_factory = raw_store_factory
        self._replay_store_factory = replay_store_factory
        self._combat_processor_factory = combat_processor_factory
        self._item_processor_factory = item_processor_factory
        self._movement_processor_factory = movement_processor_factory
        self._loadout_processor_factory = loadout_processor_factory
        self._map_snapshot_processor_factory = map_snapshot_processor_factory
        self._timeline_processor_factory = timeline_processor_factory
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
                        item_processor_factory=self._item_processor_factory,
                        movement_processor_factory=self._movement_processor_factory,
                        loadout_processor_factory=self._loadout_processor_factory,
                        map_snapshot_processor_factory=self._map_snapshot_processor_factory,
                        timeline_processor_factory=self._timeline_processor_factory,
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
        ("item_limit", options.item_limit, 200),
        ("movement_limit", options.movement_limit, 200),
        ("loadout_limit", options.loadout_limit, 500),
        ("map_snapshot_limit", options.map_snapshot_limit, 200),
        ("timeline_limit", options.timeline_limit, 200),
    ):
        if not 1 <= int(value) <= maximum:
            raise PostProcessingWorkerError(f"{label} must be between 1 and {maximum}.")


def _interruptible_sleep(seconds: int, stop_event: Event) -> None:
    remaining = seconds
    while remaining > 0 and not stop_event.is_set():
        delay = min(1, remaining)
        sleep(delay)
        remaining -= delay


def _safe_error(stage: str, exc: Exception) -> str:
    return f"{stage}: {exc.__class__.__name__}: {exc}"[:1000]
