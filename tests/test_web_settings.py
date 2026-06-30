from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import os
import unittest

from fastapi.testclient import TestClient

from pubg_ai.web.app import create_app


class WebSettingsTests(unittest.TestCase):
    def test_index_includes_storage_and_collector_settings_forms(self) -> None:
        client = TestClient(create_app())
        response = client.get("/")

        self.assertEqual(response.status_code, 200)
        body = response.text
        self.assertIn('id="storageSettingsForm"', body)
        self.assertIn('id="collectorSettingsForm"', body)
        self.assertIn("/settings/storage", body)
        self.assertIn("/settings/collector", body)
        self.assertIn('id="alertSettingsForm"', body)
        self.assertIn('id="alertsBody"', body)
        self.assertIn("/settings/alerts", body)
        self.assertIn("/alerts/status", body)
        self.assertIn("saveStorageSettings", body)
        self.assertIn("saveCollectorSettings", body)
        self.assertIn('id="collectorWorkerForm"', body)
        self.assertIn("/collector/worker/start", body)
        self.assertIn("/collector/worker/status", body)
        self.assertIn('id="postProcessingWorkerForm"', body)
        self.assertIn("/post-processing/worker/start", body)
        self.assertIn("/post-processing/worker/status", body)
        self.assertIn('id="workerRunsBody"', body)
        self.assertIn("/workers/runs", body)
        self.assertIn("loadWorkerRuns", body)
        self.assertIn('id="discordScopeForm"', body)
        self.assertIn('id="publicProfileDefaultForm"', body)
        self.assertIn('id="discordScopesBody"', body)
        self.assertIn("/discord/scopes", body)

    def test_web_settings_endpoint_updates_local_settings_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings_file = Path(temp_dir) / "config" / "local_settings.json"
            with patch.dict(os.environ, {"PUBG_LOCAL_SETTINGS_FILE": str(settings_file)}):
                client = TestClient(create_app())
                response = client.post(
                    "/settings/web",
                    json={"local_web_base_url": "http://127.0.0.1:8000/"},
                )
                status = client.get("/settings/status")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["web"]["local_web_base_url"], "http://127.0.0.1:8000")
            self.assertEqual(status.json()["local_web_base_url"], "http://127.0.0.1:8000")

            payload = json.loads(settings_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["web"]["local_web_base_url"], "http://127.0.0.1:8000")
            self.assertNotIn("PUBG_API_KEY", str(payload))
            self.assertNotIn("DISCORD_BOT_TOKEN", str(payload))

    def test_web_settings_endpoint_rejects_invalid_url(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings_file = Path(temp_dir) / "config" / "local_settings.json"
            with patch.dict(os.environ, {"PUBG_LOCAL_SETTINGS_FILE": str(settings_file)}):
                client = TestClient(create_app())
                response = client.post(
                    "/settings/web",
                    json={"local_web_base_url": "ftp://127.0.0.1:8000"},
                )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(settings_file.exists())

    def test_storage_settings_endpoint_updates_paths_and_status(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            settings_file = base_dir / "config" / "local_settings.json"
            raw_dir = base_dir / "raw-drive" / "raw"
            replay_dir = base_dir / "replay-drive" / "replay"
            with patch.dict(os.environ, {"PUBG_LOCAL_SETTINGS_FILE": str(settings_file)}):
                client = TestClient(create_app())
                response = client.post(
                    "/settings/storage",
                    json={
                        "raw_data_dir": str(raw_dir),
                        "replay_data_dir": str(replay_dir),
                        "raw_compression": "none",
                    },
                )
                status = client.get("/settings/status")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["storage"]["raw_data_dir"], str(raw_dir))
            self.assertEqual(payload["storage"]["replay_data_dir"], str(replay_dir))
            self.assertEqual(payload["storage"]["raw_compression"], "none")
            self.assertTrue(payload["storage_status"]["raw_data_dir"]["writable"])
            self.assertTrue(payload["storage_status"]["replay_data_dir"]["writable"])
            self.assertEqual(status.json()["raw_data_dir"], str(raw_dir))
            self.assertEqual(status.json()["raw_compression"], "none")
            self.assertTrue(raw_dir.is_dir())
            self.assertTrue(replay_dir.is_dir())

    def test_collector_settings_endpoint_updates_limits(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings_file = Path(temp_dir) / "config" / "local_settings.json"
            with patch.dict(os.environ, {"PUBG_LOCAL_SETTINGS_FILE": str(settings_file)}):
                client = TestClient(create_app())
                response = client.post(
                    "/settings/collector",
                    json={
                        "poll_interval_seconds": 90,
                        "cycle_player_limit": 50,
                        "player_lookup_chunk_size": 5,
                    },
                )
                status = client.get("/settings/status")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["collector"]["poll_interval_seconds"], 90)
            self.assertEqual(response.json()["collector"]["cycle_player_limit"], 50)
            self.assertEqual(response.json()["collector"]["player_lookup_chunk_size"], 5)
            self.assertEqual(status.json()["collector"]["poll_interval_seconds"], 90)
            self.assertEqual(status.json()["collector"]["cycle_player_limit"], 50)
            self.assertEqual(status.json()["collector"]["player_lookup_chunk_size"], 5)

    def test_collector_worker_status_defaults_to_stopped(self) -> None:
        client = TestClient(create_app())
        response = client.get("/collector/worker/status")

        self.assertEqual(response.status_code, 200)
        payload = response.json()["worker"]
        self.assertFalse(payload["running"])
        self.assertFalse(payload["stop_requested"])
        self.assertEqual(payload["cycle_count"], 0)

    def test_collector_worker_start_requires_pubg_key(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings_file = Path(temp_dir) / "config" / "local_settings.json"
            with patch.dict(os.environ, {"PUBG_LOCAL_SETTINGS_FILE": str(settings_file), "PUBG_API_KEY": ""}):
                client = TestClient(create_app())
                response = client.post(
                    "/collector/worker/start",
                    json={"match_job_limit": 10, "telemetry_job_limit": 5},
                )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], "PUBG_API_KEY is not configured.")

    def test_post_processing_worker_status_defaults_to_stopped(self) -> None:
        client = TestClient(create_app())
        response = client.get("/post-processing/worker/status")

        self.assertEqual(response.status_code, 200)
        payload = response.json()["worker"]
        self.assertFalse(payload["running"])
        self.assertFalse(payload["stop_requested"])
        self.assertEqual(payload["cycle_count"], 0)

    def test_worker_runs_endpoint_returns_recent_history(self) -> None:
        connection = FakeWorkerRunConnection()
        with patch("pubg_ai.web.app.connect_mysql", return_value=connection):
            client = TestClient(create_app())
            response = client.get("/workers/runs?worker_name=collector&limit=5")

        self.assertEqual(response.status_code, 200)
        runs = response.json()["runs"]
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["worker_name"], "collector")
        self.assertEqual(runs[0]["summary"]["collection"]["queued_match_jobs"], 2)
        self.assertTrue(connection.closed)

    def test_alert_settings_endpoint_updates_local_settings_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings_file = Path(temp_dir) / "config" / "local_settings.json"
            connection = FakeWorkerRunConnection(rows=[], latest_id=0)
            with patch.dict(os.environ, {"PUBG_LOCAL_SETTINGS_FILE": str(settings_file)}):
                with patch("pubg_ai.web.app.connect_mysql", return_value=connection):
                    client = TestClient(create_app())
                    response = client.post(
                        "/settings/alerts",
                        json={
                            "minimum_free_bytes": 123456,
                            "discord_channel_ids": ["111", "222"],
                            "storage_alerts_enabled": True,
                            "worker_error_alerts_enabled": False,
                        },
                    )
                    loaded = client.get("/alerts/status")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["alert_settings"]["minimum_free_bytes"], 123456)
            self.assertEqual(response.json()["alert_settings"]["discord_channel_ids"], ["111", "222"])
            self.assertFalse(response.json()["alert_settings"]["worker_error_alerts_enabled"])
            self.assertEqual(loaded.json()["alert_settings"]["minimum_free_bytes"], 123456)
            stored = json.loads(settings_file.read_text(encoding="utf-8"))
            self.assertEqual(stored["alerts"]["discord_channel_ids"], ["111", "222"])
            self.assertNotIn("DISCORD_BOT_TOKEN", str(stored))

    def test_discord_scope_settings_endpoint_updates_local_settings_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings_file = Path(temp_dir) / "config" / "local_settings.json"
            with patch.dict(os.environ, {"PUBG_LOCAL_SETTINGS_FILE": str(settings_file)}):
                client = TestClient(create_app())
                default_response = client.get("/discord/scopes")
                response = client.post(
                    "/discord/scopes",
                    json={
                        "guild_ranking_scopes": {
                            "guild-1": "guild",
                            "guild-2": "global",
                        },
                        "public_profile_default": False,
                    },
                )
                loaded_response = client.get("/discord/scopes")

            self.assertEqual(default_response.status_code, 200)
            self.assertEqual(default_response.json()["discord_scopes"]["guild_ranking_scopes"], {})
            self.assertTrue(default_response.json()["discord_scopes"]["public_profile_default"])

            self.assertEqual(response.status_code, 200)
            payload = response.json()["discord_scopes"]
            self.assertEqual(payload["guild_ranking_scopes"]["guild-1"], "guild")
            self.assertEqual(payload["guild_ranking_scopes"]["guild-2"], "global")
            self.assertFalse(payload["public_profile_default"])
            self.assertEqual(loaded_response.json()["discord_scopes"], payload)

            stored = json.loads(settings_file.read_text(encoding="utf-8"))
            self.assertEqual(stored["discord_scopes"]["guild_ranking_scopes"]["guild-2"], "global")
            self.assertFalse(stored["discord_scopes"]["public_profile_default"])
            self.assertNotIn("PUBG_API_KEY", str(stored))
            self.assertNotIn("DISCORD_BOT_TOKEN", str(stored))

    def test_discord_scope_settings_endpoint_rejects_invalid_scope(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings_file = Path(temp_dir) / "config" / "local_settings.json"
            with patch.dict(os.environ, {"PUBG_LOCAL_SETTINGS_FILE": str(settings_file)}):
                client = TestClient(create_app())
                response = client.post(
                    "/discord/scopes",
                    json={
                        "guild_ranking_scopes": {"guild-1": "public"},
                        "public_profile_default": True,
                    },
                )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(settings_file.exists())


class FakeWorkerRunConnection:
    def __init__(self, rows: list[dict[str, object]] | None = None, latest_id: int = 1) -> None:
        self.closed = False
        self.cursor_obj = FakeWorkerRunCursor(rows=rows, latest_id=latest_id)

    def cursor(self) -> "FakeWorkerRunCursor":
        return self.cursor_obj

    def close(self) -> None:
        self.closed = True


class FakeWorkerRunCursor:
    def __init__(self, rows: list[dict[str, object]] | None = None, latest_id: int = 1) -> None:
        self.rows = rows
        self.latest_id = latest_id

    def __enter__(self) -> "FakeWorkerRunCursor":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def execute(self, query: str, params: tuple[object, ...] = ()) -> None:
        self.query = query
        self.params = params

    def fetchall(self) -> list[dict[str, object]]:
        if self.rows is not None:
            return self.rows
        return [
            {
                "id": 1,
                "worker_name": "collector",
                "status": "succeeded",
                "started_at_kst": "2026-06-30T10:00:00+09:00",
                "finished_at_kst": "2026-06-30T10:00:01+09:00",
                "duration_seconds": 1.0,
                "error_count": 0,
                "last_error": None,
                "summary_json": '{"errors":[],"collection":{"queued_match_jobs":2}}',
                "created_at_kst": "2026-06-30T10:00:01+09:00",
            }
        ]

    def fetchone(self) -> dict[str, object]:
        return {"latest_id": self.latest_id}


if __name__ == "__main__":
    unittest.main()
