from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from threading import Event, Lock, Thread
from time import sleep
from typing import Any, Callable

from pubg_ai.config import RuntimeConfig
from pubg_ai.database import connect_mysql
from pubg_ai.match_collection import RegisteredPlayerMatchCollector
from pubg_ai.match_job_processor import MatchJobProcessor
from pubg_ai.pubg_client import PubgApiClient
from pubg_ai.raw_storage import RawPayloadStore
from pubg_ai.telemetry_job_processor import TelemetryJobProcessor
from pubg_ai.time_utils import isoformat_kst, now_kst


class CollectorWorkerError(RuntimeError):
    """Raised when the automatic collector cannot run a cycle."""


@dataclass(frozen=True)
class CollectorWorkerOptions:
    shard: str | None = None
    match_job_limit: int = 10
    telemetry_job_limit: int = 5

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CollectorCycleResult:
    started_at_kst: str
    finished_at_kst: str
    duration_seconds: float
    poll_interval_seconds: int
    cycle_player_limit: int
    player_lookup_chunk_size: int
    shard: str | None
    match_job_limit: int
    telemetry_job_limit: int
    collection: dict[str, Any] | None
    match_jobs: dict[str, Any] | None
    telemetry_jobs: dict[str, Any] | None
    errors: list[str]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CollectorWorkerState:
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
PubgClientFactory = Callable[[str], PubgApiClient]
RawStoreFactory = Callable[..., RawPayloadStore]
CollectorFactory = Callable[..., Any]
MatchProcessorFactory = Callable[..., Any]
TelemetryProcessorFactory = Callable[..., Any]


def run_collector_cycle(
    config: RuntimeConfig,
    *,
    options: CollectorWorkerOptions | None = None,
    connection_factory: ConnectionFactory = connect_mysql,
    pubg_client_factory: PubgClientFactory = PubgApiClient,
    raw_store_factory: RawStoreFactory = RawPayloadStore,
    collector_factory: CollectorFactory = RegisteredPlayerMatchCollector,
    match_processor_factory: MatchProcessorFactory = MatchJobProcessor,
    telemetry_processor_factory: TelemetryProcessorFactory = TelemetryJobProcessor,
) -> CollectorCycleResult:
    worker_options = options or CollectorWorkerOptions()
    _validate_options(worker_options)
    started = now_kst()
    errors: list[str] = []
    collection: dict[str, Any] | None = None
    match_jobs: dict[str, Any] | None = None
    telemetry_jobs: dict[str, Any] | None = None

    if not config.secrets.pubg_api_key:
        raise CollectorWorkerError("PUBG_API_KEY is not configured.")

    pubg_client = pubg_client_factory(config.secrets.pubg_api_key)
    connection = connection_factory(config.database)
    try:
        raw_store = raw_store_factory(
            config.app.raw_data_dir,
            compression=config.app.raw_compression,  # type: ignore[arg-type]
        )

        try:
            collection = collector_factory(
                connection,
                pubg_client,
                lookup_chunk_size=config.app.player_lookup_chunk_size,
            ).collect_active_players(
                shard=worker_options.shard,
                limit=config.app.collector_cycle_player_limit,
            ).to_record()
        except Exception as exc:
            errors.append(_safe_error("collection", exc))

        try:
            match_jobs = match_processor_factory(
                connection,
                pubg_client,
                raw_store,
            ).process_queued_matches(limit=worker_options.match_job_limit).to_record()
        except Exception as exc:
            errors.append(_safe_error("match_jobs", exc))

        try:
            telemetry_jobs = telemetry_processor_factory(
                connection,
                raw_store,
            ).process_queued_telemetry(limit=worker_options.telemetry_job_limit).to_record()
        except Exception as exc:
            errors.append(_safe_error("telemetry_jobs", exc))
    finally:
        connection.close()

    finished = now_kst()
    return CollectorCycleResult(
        started_at_kst=started.isoformat(),
        finished_at_kst=finished.isoformat(),
        duration_seconds=(finished - started).total_seconds(),
        poll_interval_seconds=config.app.collector_poll_interval_seconds,
        cycle_player_limit=config.app.collector_cycle_player_limit,
        player_lookup_chunk_size=config.app.player_lookup_chunk_size,
        shard=worker_options.shard,
        match_job_limit=worker_options.match_job_limit,
        telemetry_job_limit=worker_options.telemetry_job_limit,
        collection=collection,
        match_jobs=match_jobs,
        telemetry_jobs=telemetry_jobs,
        errors=errors,
    )


class CollectorWorkerController:
    def __init__(
        self,
        *,
        config_loader: ConfigLoader,
        connection_factory: ConnectionFactory = connect_mysql,
        pubg_client_factory: PubgClientFactory = PubgApiClient,
        raw_store_factory: RawStoreFactory = RawPayloadStore,
        collector_factory: CollectorFactory = RegisteredPlayerMatchCollector,
        match_processor_factory: MatchProcessorFactory = MatchJobProcessor,
        telemetry_processor_factory: TelemetryProcessorFactory = TelemetryJobProcessor,
    ) -> None:
        self._config_loader = config_loader
        self._connection_factory = connection_factory
        self._pubg_client_factory = pubg_client_factory
        self._raw_store_factory = raw_store_factory
        self._collector_factory = collector_factory
        self._match_processor_factory = match_processor_factory
        self._telemetry_processor_factory = telemetry_processor_factory
        self._lock = Lock()
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._state = CollectorWorkerState(
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

    def start(self, options: CollectorWorkerOptions | None = None) -> CollectorWorkerState:
        worker_options = options or CollectorWorkerOptions()
        _validate_options(worker_options)
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self._state

            self._stop_event = Event()
            self._state = CollectorWorkerState(
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
                name="pubg-ai-collector-worker",
                daemon=True,
            )
            self._thread.start()
            return self._state

    def stop(self) -> CollectorWorkerState:
        with self._lock:
            self._stop_event.set()
            if self._state.running:
                self._state = CollectorWorkerState(
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

    def status(self) -> CollectorWorkerState:
        with self._lock:
            return self._state

    def _run_loop(self, options: CollectorWorkerOptions) -> None:
        try:
            while not self._stop_event.is_set():
                config = self._config_loader()
                try:
                    cycle = run_collector_cycle(
                        config,
                        options=options,
                        connection_factory=self._connection_factory,
                        pubg_client_factory=self._pubg_client_factory,
                        raw_store_factory=self._raw_store_factory,
                        collector_factory=self._collector_factory,
                        match_processor_factory=self._match_processor_factory,
                        telemetry_processor_factory=self._telemetry_processor_factory,
                    )
                    self._record_cycle(cycle)
                except Exception as exc:
                    self._record_error(exc)

                if self._stop_event.is_set():
                    break

                interval = max(60, min(config.app.collector_poll_interval_seconds, 300))
                next_run = now_kst().timestamp() + interval
                self._record_next_run(next_run)
                _interruptible_sleep(interval, self._stop_event)
        finally:
            self._mark_stopped()

    def _record_cycle(self, cycle: CollectorCycleResult) -> None:
        with self._lock:
            self._state = CollectorWorkerState(
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
            self._state = CollectorWorkerState(
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
            self._state = CollectorWorkerState(
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
            self._state = CollectorWorkerState(
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


def _validate_options(options: CollectorWorkerOptions) -> None:
    if not 1 <= options.match_job_limit <= 500:
        raise CollectorWorkerError("match_job_limit must be between 1 and 500.")
    if not 1 <= options.telemetry_job_limit <= 200:
        raise CollectorWorkerError("telemetry_job_limit must be between 1 and 200.")
    if options.shard is not None and not options.shard.strip():
        raise CollectorWorkerError("shard must be non-empty when provided.")


def _interruptible_sleep(seconds: int, stop_event: Event) -> None:
    remaining = seconds
    while remaining > 0 and not stop_event.is_set():
        delay = min(1, remaining)
        sleep(delay)
        remaining -= delay


def _safe_error(stage: str, exc: Exception) -> str:
    return f"{stage}: {exc.__class__.__name__}: {exc}"[:1000]
