from __future__ import annotations

from pathlib import Path
import unittest

from pubg_ai.config import AppConfig, DatabaseConfig, RuntimeConfig, SecretConfig
from pubg_ai.post_processing_worker import (
    PostProcessingWorkerError,
    PostProcessingWorkerOptions,
    run_post_processing_cycle,
)


class PostProcessingWorkerTests(unittest.TestCase):
    def test_run_post_processing_cycle_uses_limits_and_force(self) -> None:
        calls: list[tuple[str, object]] = []
        history: list[tuple[FakeConnection, str, dict[str, object]]] = []
        connection = FakeConnection()

        def connection_factory(database: DatabaseConfig) -> FakeConnection:
            calls.append(("connect", database.database))
            return connection

        options = PostProcessingWorkerOptions(
            combat_limit=3,
            item_limit=4,
            movement_limit=5,
            loadout_limit=6,
            map_snapshot_limit=7,
            timeline_limit=8,
            force=True,
        )
        result = run_post_processing_cycle(
            _runtime_config(),
            options=options,
            connection_factory=connection_factory,
            raw_store_factory=lambda root, compression: FakeRawStore(root, compression, calls),
            replay_store_factory=lambda root: FakeReplayStore(root, calls),
            combat_processor_factory=lambda *args, **kwargs: FakeTelemetryProcessor("combat", *args, calls=calls),
            item_processor_factory=lambda *args, **kwargs: FakeTelemetryProcessor("items", *args, calls=calls),
            movement_processor_factory=lambda *args, **kwargs: FakeTelemetryProcessor("movement", *args, calls=calls),
            loadout_processor_factory=lambda *args, **kwargs: FakeLoadoutProcessor(*args, calls=calls),
            map_snapshot_processor_factory=lambda *args, **kwargs: FakeReplayProcessor("map_snapshots", *args, calls=calls),
            timeline_processor_factory=lambda *args, **kwargs: FakeReplayProcessor("replay_timelines", *args, calls=calls),
            history_recorder=lambda conn, worker_name, cycle: history.append((conn, worker_name, cycle)),
        )

        self.assertEqual(result.poll_interval_seconds, 120)
        self.assertEqual(result.options, options.to_record())
        self.assertEqual(result.combat, {"parsed_payloads": 3, "failed_payloads": 0})
        self.assertEqual(result.items, {"parsed_payloads": 4, "failed_payloads": 0})
        self.assertEqual(result.movement, {"parsed_payloads": 5, "failed_payloads": 0})
        self.assertEqual(result.loadout_snapshots, {"generated_snapshots": 6, "failed_matches": 0})
        self.assertEqual(result.map_snapshots, {"generated_snapshots": 7, "failed_snapshots": 0, "artifacts": []})
        self.assertEqual(result.replay_timelines, {"generated_timelines": 8, "failed_timelines": 0, "artifacts": []})
        self.assertEqual(result.errors, [])
        self.assertTrue(connection.closed)
        self.assertIn(("raw_store", (Path("raw"), "none")), calls)
        self.assertIn(("replay_store", Path("replays")), calls)
        self.assertIn(("combat", (3, True)), calls)
        self.assertIn(("items", (4, True)), calls)
        self.assertIn(("movement", (5, True)), calls)
        self.assertIn(("loadout", (6, True)), calls)
        self.assertIn(("map_snapshots", (7, True)), calls)
        self.assertIn(("replay_timelines", (8, True)), calls)
        self.assertEqual(len(history), 1)
        self.assertIs(history[0][0], connection)
        self.assertEqual(history[0][1], "post_processing")
        self.assertEqual(history[0][2]["map_snapshots"], {"generated_snapshots": 7, "failed_snapshots": 0, "artifacts": []})

    def test_run_post_processing_cycle_records_stage_errors_and_continues(self) -> None:
        result = run_post_processing_cycle(
            _runtime_config(),
            options=PostProcessingWorkerOptions(combat_limit=1),
            connection_factory=lambda database: FakeConnection(),
            raw_store_factory=lambda root, compression: FakeRawStore(root, compression, []),
            replay_store_factory=lambda root: FakeReplayStore(root, []),
            combat_processor_factory=lambda *args, **kwargs: FailingTelemetryProcessor(),
            item_processor_factory=lambda *args, **kwargs: FakeTelemetryProcessor("items", *args, calls=[]),
            movement_processor_factory=lambda *args, **kwargs: FakeTelemetryProcessor("movement", *args, calls=[]),
            loadout_processor_factory=lambda *args, **kwargs: FakeLoadoutProcessor(*args, calls=[]),
            map_snapshot_processor_factory=lambda *args, **kwargs: FakeReplayProcessor("map_snapshots", *args, calls=[]),
            timeline_processor_factory=lambda *args, **kwargs: FakeReplayProcessor("replay_timelines", *args, calls=[]),
            history_recorder=lambda conn, worker_name, cycle: None,
        )

        self.assertIsNone(result.combat)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("combat: RuntimeError: boom", result.errors[0])
        self.assertEqual(result.items, {"parsed_payloads": 10, "failed_payloads": 0})

    def test_run_post_processing_cycle_records_replay_store_errors(self) -> None:
        history: list[tuple[FakeConnection, str, dict[str, object]]] = []
        connection = FakeConnection()

        result = run_post_processing_cycle(
            _runtime_config(),
            connection_factory=lambda database: connection,
            raw_store_factory=lambda root, compression: FakeRawStore(root, compression, []),
            replay_store_factory=lambda root: (_ for _ in ()).throw(RuntimeError("drive missing")),
            history_recorder=lambda conn, worker_name, cycle: history.append((conn, worker_name, cycle)),
        )

        self.assertIsNone(result.combat)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("replay_store: RuntimeError: drive missing", result.errors[0])
        self.assertTrue(connection.closed)
        self.assertEqual(history[0][1], "post_processing")
        self.assertEqual(history[0][2]["errors"], result.errors)

    def test_run_post_processing_cycle_validates_limits(self) -> None:
        with self.assertRaises(PostProcessingWorkerError):
            run_post_processing_cycle(_runtime_config(), options=PostProcessingWorkerOptions(combat_limit=0))


def _runtime_config() -> RuntimeConfig:
    return RuntimeConfig(
        app=AppConfig(
            raw_data_dir=Path("raw"),
            replay_data_dir=Path("replays"),
            raw_compression="none",
            collector_poll_interval_seconds=120,
        ),
        database=DatabaseConfig(database="pubg_ai_test"),
        secrets=SecretConfig(),
    )


class FakeConnection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeRawStore:
    def __init__(self, root: Path, compression: str, calls: list[tuple[str, object]]) -> None:
        calls.append(("raw_store", (root, compression)))


class FakeReplayStore:
    def __init__(self, root: Path, calls: list[tuple[str, object]]) -> None:
        calls.append(("replay_store", root))


class FakeResult:
    def __init__(self, record: dict[str, object]) -> None:
        self.record = record

    def to_record(self) -> dict[str, object]:
        return self.record


class FakeTelemetryProcessor:
    def __init__(self, name: str, connection: FakeConnection, raw_store: FakeRawStore, *, calls: list[tuple[str, object]]) -> None:
        self.name = name
        self.calls = calls

    def process_raw_telemetry(self, *, limit: int, force: bool) -> FakeResult:
        self.calls.append((self.name, (limit, force)))
        return FakeResult({"parsed_payloads": limit, "failed_payloads": 0})


class FailingTelemetryProcessor:
    def process_raw_telemetry(self, *, limit: int, force: bool) -> FakeResult:
        raise RuntimeError("boom")


class FakeLoadoutProcessor:
    def __init__(self, connection: FakeConnection, *, calls: list[tuple[str, object]]) -> None:
        self.calls = calls

    def process_matches(self, *, limit: int, force: bool) -> FakeResult:
        self.calls.append(("loadout", (limit, force)))
        return FakeResult({"generated_snapshots": limit, "failed_matches": 0})


class FakeReplayProcessor:
    def __init__(self, name: str, connection: FakeConnection, replay_store: FakeReplayStore, *, calls: list[tuple[str, object]]) -> None:
        self.name = name
        self.calls = calls

    def generate_player_snapshots(self, *, limit: int, force: bool) -> FakeResult:
        self.calls.append((self.name, (limit, force)))
        return FakeResult({"generated_snapshots": limit, "failed_snapshots": 0, "artifacts": []})

    def generate_player_timelines(self, *, limit: int, force: bool) -> FakeResult:
        self.calls.append((self.name, (limit, force)))
        return FakeResult({"generated_timelines": limit, "failed_timelines": 0, "artifacts": []})


if __name__ == "__main__":
    unittest.main()
