from __future__ import annotations

from pathlib import Path
import unittest

from pubg_ai.collector_worker import CollectorWorkerError, CollectorWorkerOptions, run_collector_cycle
from pubg_ai.config import AppConfig, DatabaseConfig, RuntimeConfig, SecretConfig


class CollectorWorkerTests(unittest.TestCase):
    def test_run_collector_cycle_uses_runtime_settings_and_limits(self) -> None:
        calls: list[tuple[str, object]] = []
        history: list[tuple[FakeConnection, str, dict[str, object]]] = []
        connection = FakeConnection()

        def connection_factory(database: DatabaseConfig) -> FakeConnection:
            calls.append(("connect", database.database))
            return connection

        result = run_collector_cycle(
            _runtime_config(),
            options=CollectorWorkerOptions(shard="steam", match_job_limit=11, telemetry_job_limit=6),
            connection_factory=connection_factory,
            pubg_client_factory=lambda key: FakePubgClient(key, calls),
            raw_store_factory=lambda root, compression: FakeRawStore(root, compression, calls),
            collector_factory=lambda *args, **kwargs: FakeCollector(*args, calls=calls, **kwargs),
            match_processor_factory=lambda *args, **kwargs: FakeMatchProcessor(*args, calls=calls, **kwargs),
            telemetry_processor_factory=lambda *args, **kwargs: FakeTelemetryProcessor(*args, calls=calls, **kwargs),
            history_recorder=lambda conn, worker_name, cycle: history.append((conn, worker_name, cycle)),
        )

        self.assertEqual(result.cycle_player_limit, 75)
        self.assertEqual(result.player_lookup_chunk_size, 7)
        self.assertEqual(result.poll_interval_seconds, 90)
        self.assertEqual(result.shard, "steam")
        self.assertEqual(result.match_job_limit, 11)
        self.assertEqual(result.telemetry_job_limit, 6)
        self.assertEqual(result.collection, {"queued_match_jobs": 2, "existing_match_jobs": 1})
        self.assertEqual(result.match_jobs, {"stored_matches": 2, "failed_jobs": 0})
        self.assertEqual(result.telemetry_jobs, {"stored_telemetry": 2, "failed_jobs": 0, "stored_bytes": 123})
        self.assertEqual(result.errors, [])
        self.assertTrue(connection.closed)
        self.assertIn(("client", "pubg-key"), calls)
        self.assertIn(("raw_store", (Path("raw"), "none")), calls)
        self.assertIn(("collector_init", 7), calls)
        self.assertIn(("collect", ("steam", 75)), calls)
        self.assertIn(("match_jobs", 11), calls)
        self.assertIn(("telemetry_jobs", 6), calls)
        self.assertEqual(len(history), 1)
        self.assertIs(history[0][0], connection)
        self.assertEqual(history[0][1], "collector")
        self.assertEqual(history[0][2]["collection"], {"queued_match_jobs": 2, "existing_match_jobs": 1})

    def test_run_collector_cycle_requires_pubg_key(self) -> None:
        config = _runtime_config(pubg_api_key=None)

        with self.assertRaises(CollectorWorkerError):
            run_collector_cycle(config)

    def test_run_collector_cycle_records_raw_store_errors(self) -> None:
        history: list[tuple[FakeConnection, str, dict[str, object]]] = []
        connection = FakeConnection()

        result = run_collector_cycle(
            _runtime_config(),
            connection_factory=lambda database: connection,
            pubg_client_factory=lambda key: FakePubgClient(key, []),
            raw_store_factory=lambda root, compression: (_ for _ in ()).throw(RuntimeError("drive full")),
            history_recorder=lambda conn, worker_name, cycle: history.append((conn, worker_name, cycle)),
        )

        self.assertIsNone(result.collection)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("raw_store: RuntimeError: drive full", result.errors[0])
        self.assertTrue(connection.closed)
        self.assertEqual(history[0][1], "collector")
        self.assertEqual(history[0][2]["errors"], result.errors)


def _runtime_config(pubg_api_key: str | None = "pubg-key") -> RuntimeConfig:
    return RuntimeConfig(
        app=AppConfig(
            raw_data_dir=Path("raw"),
            replay_data_dir=Path("replays"),
            raw_compression="none",
            collector_poll_interval_seconds=90,
            collector_cycle_player_limit=75,
            player_lookup_chunk_size=7,
        ),
        database=DatabaseConfig(database="pubg_ai_test"),
        secrets=SecretConfig(pubg_api_key=pubg_api_key),
    )


class FakeConnection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakePubgClient:
    def __init__(self, api_key: str, calls: list[tuple[str, object]]) -> None:
        calls.append(("client", api_key))


class FakeRawStore:
    def __init__(self, root: Path, compression: str, calls: list[tuple[str, object]]) -> None:
        calls.append(("raw_store", (root, compression)))


class FakeResult:
    def __init__(self, record: dict[str, object]) -> None:
        self.record = record

    def to_record(self) -> dict[str, object]:
        return self.record


class FakeCollector:
    def __init__(
        self,
        connection: FakeConnection,
        client: FakePubgClient,
        *,
        lookup_chunk_size: int,
        calls: list[tuple[str, object]],
    ) -> None:
        self.calls = calls
        calls.append(("collector_init", lookup_chunk_size))

    def collect_active_players(self, *, shard: str | None, limit: int) -> FakeResult:
        self.calls.append(("collect", (shard, limit)))
        return FakeResult({"queued_match_jobs": 2, "existing_match_jobs": 1})


class FakeMatchProcessor:
    def __init__(
        self,
        connection: FakeConnection,
        client: FakePubgClient,
        raw_store: FakeRawStore,
        *,
        calls: list[tuple[str, object]],
    ) -> None:
        self.calls = calls

    def process_queued_matches(self, *, limit: int) -> FakeResult:
        self.calls.append(("match_jobs", limit))
        return FakeResult({"stored_matches": 2, "failed_jobs": 0})


class FakeTelemetryProcessor:
    def __init__(
        self,
        connection: FakeConnection,
        raw_store: FakeRawStore,
        *,
        calls: list[tuple[str, object]],
    ) -> None:
        self.calls = calls

    def process_queued_telemetry(self, *, limit: int) -> FakeResult:
        self.calls.append(("telemetry_jobs", limit))
        return FakeResult({"stored_telemetry": 2, "failed_jobs": 0, "stored_bytes": 123})


if __name__ == "__main__":
    unittest.main()
