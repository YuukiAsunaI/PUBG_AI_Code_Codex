from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock
from unittest.mock import patch
import time
import unittest

from pubg_ai.discord_permission_manager import DiscordPermissionManager
from pubg_ai.local_settings import LocalSettingsStore
from pubg_ai.raw_storage import RawPayloadStore, RawStorageError
from pubg_ai.replay_storage import ReplayArtifactStore, ReplayStorageError


class LocalSettingsConcurrencyTests(unittest.TestCase):
    def test_concurrent_section_updates_do_not_lose_data(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            settings_file = base_dir / "config" / "local_settings.json"
            collector_store = LocalSettingsStore(settings_file, base_dir=base_dir)
            web_store = LocalSettingsStore(settings_file, base_dir=base_dir)
            entered_write = Event()
            release_write = Event()
            call_lock = Lock()
            call_count = 0
            original_write = LocalSettingsStore._write_settings_unlocked

            def delayed_first_write(store: LocalSettingsStore, payload: dict[str, object]) -> None:
                nonlocal call_count
                with call_lock:
                    call_count += 1
                    first = call_count == 1
                if first:
                    entered_write.set()
                    self.assertTrue(release_write.wait(timeout=2))
                original_write(store, payload)

            with patch.object(LocalSettingsStore, "_write_settings_unlocked", delayed_first_write):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    first = executor.submit(collector_store.save_collector_settings, 60, 80, 10)
                    self.assertTrue(entered_write.wait(timeout=2))
                    second = executor.submit(web_store.save_web_settings, "http://127.0.0.1:8018")
                    time.sleep(0.05)
                    self.assertFalse(second.done())
                    release_write.set()
                    first.result(timeout=2)
                    second.result(timeout=2)

            self.assertEqual(web_store.load_collector_settings().cycle_player_limit, 80)
            self.assertEqual(web_store.load_web_settings().local_web_base_url, "http://127.0.0.1:8018")

    def test_concurrent_permission_grants_are_all_preserved(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            managers = [
                DiscordPermissionManager(
                    LocalSettingsStore(
                        base_dir / "config" / "local_settings.json",
                        base_dir=base_dir,
                    )
                )
                for _ in range(20)
            ]

            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = [
                    executor.submit(manager.grant, user_id=f"user-{index}", group="profile_read")
                    for index, manager in enumerate(managers)
                ]
                for future in futures:
                    self.assertTrue(future.result(timeout=5).changed)

            grants = managers[0].load().user_grants
            self.assertEqual(set(grants), {f"user-{index}" for index in range(20)})


class AtomicFileStorageTests(unittest.TestCase):
    def test_raw_write_failure_removes_temporary_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = RawPayloadStore(root, compression="none")

            with patch("pubg_ai.file_io.os.replace", side_effect=OSError("disk full")):
                with self.assertRaises(RawStorageError):
                    store.write_json("match", "steam", "match-1", {"data": {}})

            self.assertEqual(list(root.rglob("*.tmp")), [])

    def test_replay_write_failure_removes_temporary_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = ReplayArtifactStore(root)

            with patch("pubg_ai.file_io.os.replace", side_effect=OSError("disk full")):
                with self.assertRaises(ReplayStorageError):
                    store.write_bytes(
                        "timeline",
                        "steam",
                        "match-1",
                        b"{}",
                        "timeline.json",
                        "application/json",
                    )

            self.assertEqual(list(root.rglob("*.tmp")), [])

    def test_verification_streams_raw_and_replay_files(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw_store = RawPayloadStore(root / "raw", compression="none")
            replay_store = ReplayArtifactStore(root / "replay")
            raw = raw_store.write_json("match", "steam", "match-1", {"data": {"id": "match-1"}})
            replay = replay_store.write_bytes(
                "timeline",
                "steam",
                "match-1",
                b'{"events":[]}',
                "timeline.json",
                "application/json",
            )

            with patch.object(Path, "read_bytes", side_effect=AssertionError("read_bytes must not be used")):
                self.assertTrue(raw_store.verify(raw))
                self.assertTrue(replay_store.verify(replay))


if __name__ == "__main__":
    unittest.main()
