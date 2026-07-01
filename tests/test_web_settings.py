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
        self.assertIn('id="alertHistoryFilterForm"', body)
        self.assertIn('id="alertHistoryBody"', body)
        self.assertIn('id="alertHistoryExport"', body)
        self.assertIn('id="alertHistoryPrev"', body)
        self.assertIn('id="alertHistoryNext"', body)
        self.assertIn('id="alertHistoryDetail"', body)
        self.assertIn('name="sort"', body)
        self.assertIn('name="severity"', body)
        self.assertIn('name="search"', body)
        self.assertIn('placeholder="title or message"', body)
        self.assertIn('value="severity"', body)
        self.assertIn('data-alert-history-preset="current-errors"', body)
        self.assertIn('data-alert-history-preset="worker-failures"', body)
        self.assertIn('data-alert-history-preset="storage-pressure"', body)
        self.assertIn('data-alert-history-preset="all-history"', body)
        self.assertIn("/settings/alerts", body)
        self.assertIn("/alerts/status", body)
        self.assertIn("/alerts/history/", body)
        self.assertIn("/alerts/history/export.csv", body)
        self.assertIn("data-alert-note-type", body)
        self.assertIn("data-alert-detail-id", body)
        self.assertIn("data-alert-detail-action", body)
        self.assertIn("data-worker-run-from-alert", body)
        self.assertIn("alertWorkerRunButton", body)
        self.assertIn("alertWorkerRunId", body)
        self.assertIn("positiveIntegerText", body)
        self.assertIn("dataset.workerRunFromAlert", body)
        self.assertIn("Worker run detail loaded from alert", body)
        self.assertIn("data-alert-note-form", body)
        self.assertIn("detail-note-form", body)
        self.assertIn("saveAlertHistoryNoteForm", body)
        self.assertIn("dataset.alertDetailAction", body)
        self.assertIn("alertHistoryState", body)
        self.assertIn("alert-state-badge", body)
        self.assertIn("alert-state-snoozed", body)
        self.assertIn("alertSeverityBadge", body)
        self.assertIn("alert-severity-badge", body)
        self.assertIn("alert-severity-error", body)
        self.assertIn("table-badge-stack", body)
        self.assertIn("sort ${alertHistoryPage.sort", body)
        self.assertIn("search ${alertHistoryPage.search", body)
        self.assertIn("severity ${alertHistoryPage.severity", body)
        self.assertIn("applyAlertHistoryPreset", body)
        self.assertIn("alertHistoryPresetButtons", body)
        self.assertIn('form.get("search") ?? alertHistoryPage.search', body)
        self.assertIn("Snoozed until", body)
        self.assertIn("loadAlertHistoryDetail", body)
        self.assertIn("loadAlertHistoryDetailById", body)
        self.assertIn("loadInitialAlertDetailFromUrl", body)
        self.assertIn('params.get("alert_id")', body)
        self.assertIn("/alerts/history/${encodeURIComponent(alertId)}", body)
        self.assertIn("formatAlertHistoryStatus", body)
        self.assertNotIn("function alertHistoryStatus", body)
        self.assertNotIn("window.prompt", body)
        self.assertIn("/notes", body)
        self.assertIn("saveStorageSettings", body)
        self.assertIn("saveCollectorSettings", body)
        self.assertIn('id="collectorWorkerForm"', body)
        self.assertIn("/collector/worker/start", body)
        self.assertIn("/collector/worker/status", body)
        self.assertIn('id="postProcessingWorkerForm"', body)
        self.assertIn("/post-processing/worker/start", body)
        self.assertIn("/post-processing/worker/status", body)
        self.assertIn('id="workerRunsBody"', body)
        self.assertIn('id="workerRunFilterForm"', body)
        self.assertIn('id="workerRunsStatus"', body)
        self.assertIn('id="workerRunsPrev"', body)
        self.assertIn('id="workerRunsNext"', body)
        self.assertIn('id="workerRunDetail"', body)
        self.assertIn("data-worker-run-detail-id", body)
        self.assertIn('name="status"', body)
        self.assertIn("/workers/runs", body)
        self.assertIn("/workers/runs/${encodeURIComponent(runId)}", body)
        self.assertIn("loadWorkerRuns", body)
        self.assertIn("loadWorkerRunDetail", body)
        self.assertIn("loadInitialWorkerRunDetailFromUrl", body)
        self.assertIn("renderWorkerRunDetail", body)
        self.assertIn("workerRunDetailUrl", body)
        self.assertIn("updateWorkerRunDetailUrl", body)
        self.assertIn("copyWorkerRunDetailLink", body)
        self.assertIn('params.get("worker_run_id")', body)
        self.assertIn('url.searchParams.set("worker_run_id", runId)', body)
        self.assertIn("data-copy-worker-run-link", body)
        self.assertIn("navigator.clipboard.writeText", body)
        self.assertIn("Summary metric", body)
        self.assertIn("Stored error", body)
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
            response = client.get("/workers/runs?worker_name=collector&status=succeeded&limit=5&offset=10")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        page = payload["worker_run_page"]
        runs = payload["runs"]
        self.assertEqual(page["worker_name"], "collector")
        self.assertEqual(page["status"], "succeeded")
        self.assertEqual(page["limit"], 5)
        self.assertEqual(page["offset"], 10)
        self.assertEqual(page["total"], 1)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["worker_name"], "collector")
        self.assertEqual(runs[0]["summary"]["collection"]["queued_match_jobs"], 2)
        executed_sql = "\n".join(query for query, _ in connection.cursor_obj.executed)
        self.assertIn("worker_name = %s", executed_sql)
        self.assertIn("status = %s", executed_sql)
        self.assertIn("LIMIT %s OFFSET %s", executed_sql)
        self.assertTrue(connection.closed)

    def test_worker_run_detail_endpoint_returns_stored_summary_and_errors(self) -> None:
        connection = FakeWorkerRunConnection(
            rows=[
                {
                    "id": 7,
                    "worker_name": "post_processing",
                    "status": "failed",
                    "started_at_kst": "2026-06-30T11:00:00+09:00",
                    "finished_at_kst": "2026-06-30T11:00:05+09:00",
                    "duration_seconds": 5.2,
                    "error_count": 2,
                    "last_error": "second parser error",
                    "summary_json": json.dumps(
                        {
                            "errors": ["first parser error", "second parser error"],
                            "combat": {"parsed_payloads": 3, "failed_payloads": 1},
                        }
                    ),
                    "created_at_kst": "2026-06-30T11:00:05+09:00",
                }
            ]
        )
        with patch("pubg_ai.web.app.connect_mysql", return_value=connection):
            client = TestClient(create_app())
            response = client.get("/workers/runs/7")

        self.assertEqual(response.status_code, 200)
        payload = response.json()["run"]
        self.assertEqual(payload["id"], 7)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["summary"]["combat"]["parsed_payloads"], 3)
        self.assertEqual(payload["summary"]["errors"][1], "second parser error")
        executed_sql = "\n".join(query for query, _ in connection.cursor_obj.executed)
        self.assertIn("FROM worker_run_history", executed_sql)
        self.assertIn("WHERE id = %s", executed_sql)
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

    def test_alert_history_endpoint_acknowledges_and_snoozes_alerts(self) -> None:
        connection = FakeWorkerRunConnection(rows=[], latest_id=0, alert_rows=[_alert_history_row()])
        with patch("pubg_ai.web.app.connect_mysql", return_value=connection):
            client = TestClient(create_app())
            acknowledge_response = client.post("/alerts/history/7/acknowledge", json={})
            snooze_response = client.post("/alerts/history/7/snooze", json={"minutes": 60})

        self.assertEqual(acknowledge_response.status_code, 200)
        self.assertEqual(acknowledge_response.json()["alert"]["id"], 7)
        self.assertEqual(snooze_response.status_code, 200)
        executed_sql = "\n".join(query for query, _ in connection.cursor_obj.executed)
        self.assertIn("acknowledged_at_kst", executed_sql)
        self.assertIn("snoozed_until_kst", executed_sql)

    def test_alert_history_endpoint_filters_and_pages_history(self) -> None:
        connection = FakeWorkerRunConnection(rows=[], latest_id=0, alert_rows=[_alert_history_row()])
        with patch("pubg_ai.web.app.connect_mysql", return_value=connection):
            client = TestClient(create_app())
            response = client.get(
                "/alerts/history?source=storage&state=resolved&severity=error&sort=severity&search=raw&limit=25&offset=50"
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["alert_history_page"]["source"], "storage")
        self.assertEqual(payload["alert_history_page"]["state"], "resolved")
        self.assertEqual(payload["alert_history_page"]["severity"], "error")
        self.assertEqual(payload["alert_history_page"]["sort"], "severity")
        self.assertEqual(payload["alert_history_page"]["search"], "raw")
        self.assertEqual(payload["alert_history_page"]["limit"], 25)
        self.assertEqual(payload["alert_history_page"]["offset"], 50)
        self.assertEqual(payload["alert_history_page"]["total"], 1)
        executed_sql = "\n".join(query for query, _ in connection.cursor_obj.executed)
        self.assertIn("source = %s", executed_sql)
        self.assertIn("severity = %s", executed_sql)
        self.assertIn("resolved_at_kst IS NOT NULL", executed_sql)
        self.assertIn("(title LIKE %s OR message LIKE %s)", executed_sql)
        self.assertIn("CASE severity", executed_sql)
        self.assertIn("LIMIT %s OFFSET %s", executed_sql)
        self.assertIn("%raw%", connection.cursor_obj.executed[0][1])

    def test_alert_history_export_endpoint_returns_csv(self) -> None:
        connection = FakeWorkerRunConnection(rows=[], latest_id=0, alert_rows=[_alert_history_row()])
        with patch("pubg_ai.web.app.connect_mysql", return_value=connection):
            client = TestClient(create_app())
            response = client.get(
                "/alerts/history/export.csv?source=storage&state=all&severity=warning&sort=oldest&search=drive&limit=5000&offset=0"
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response.headers["content-type"])
        self.assertIn("pubg-ai-alert-history.csv", response.headers["content-disposition"])
        self.assertIn("id,source,severity,state,title,message", response.text)
        self.assertIn("raw_data_dir storage alert", response.text)
        executed_sql = "\n".join(query for query, _ in connection.cursor_obj.executed)
        self.assertIn("source = %s", executed_sql)
        self.assertIn("severity = %s", executed_sql)
        self.assertIn("(title LIKE %s OR message LIKE %s)", executed_sql)
        self.assertIn("ORDER BY last_seen_at_kst ASC, id ASC", executed_sql)
        self.assertIn("LIMIT %s OFFSET %s", executed_sql)
        self.assertIn("%drive%", connection.cursor_obj.executed[0][1])

    def test_alert_history_note_endpoints_create_and_list_notes(self) -> None:
        connection = FakeWorkerRunConnection(
            rows=[],
            latest_id=0,
            alert_rows=[_alert_history_row()],
            note_rows=[_alert_note_row()],
        )
        with patch("pubg_ai.web.app.connect_mysql", return_value=connection):
            client = TestClient(create_app())
            create_response = client.post(
                "/alerts/history/7/notes",
                json={
                    "note_text": "Checked D drive and left the raw path unchanged.",
                    "note_type": "resolution",
                    "created_by": "local-admin",
                },
            )
            list_response = client.get("/alerts/history/7/notes")

        self.assertEqual(create_response.status_code, 200)
        self.assertEqual(create_response.json()["note"]["note_type"], "resolution")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["notes"][0]["created_by"], "local-admin")
        executed_sql = "\n".join(query for query, _ in connection.cursor_obj.executed)
        self.assertIn("INSERT INTO system_alert_notes", executed_sql)
        self.assertIn("FROM system_alert_notes", executed_sql)

    def test_alert_history_record_endpoint_returns_alert_and_notes(self) -> None:
        connection = FakeWorkerRunConnection(
            rows=[],
            latest_id=0,
            alert_rows=[_alert_history_row()],
            note_rows=[_alert_note_row()],
        )
        with patch("pubg_ai.web.app.connect_mysql", return_value=connection):
            client = TestClient(create_app())
            response = client.get("/alerts/history/7")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["alert"]["id"], 7)
        self.assertEqual(payload["notes"][0]["id"], 21)
        executed_sql = "\n".join(query for query, _ in connection.cursor_obj.executed)
        self.assertIn("WHERE id = %s", executed_sql)
        self.assertIn("FROM system_alert_notes", executed_sql)

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
    def __init__(
        self,
        rows: list[dict[str, object]] | None = None,
        latest_id: int = 1,
        alert_rows: list[dict[str, object]] | None = None,
        note_rows: list[dict[str, object]] | None = None,
    ) -> None:
        self.closed = False
        self.cursor_obj = FakeWorkerRunCursor(
            rows=rows,
            latest_id=latest_id,
            alert_rows=alert_rows,
            note_rows=note_rows,
        )

    def cursor(self) -> "FakeWorkerRunCursor":
        return self.cursor_obj

    def close(self) -> None:
        self.closed = True


class FakeWorkerRunCursor:
    def __init__(
        self,
        rows: list[dict[str, object]] | None = None,
        latest_id: int = 1,
        alert_rows: list[dict[str, object]] | None = None,
        note_rows: list[dict[str, object]] | None = None,
    ) -> None:
        self.rows = rows
        self.latest_id = latest_id
        self.alert_rows = alert_rows or []
        self.note_rows = note_rows or []
        self.query = ""
        self.params: tuple[object, ...] = ()
        self.lastrowid = 21
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    def __enter__(self) -> "FakeWorkerRunCursor":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def execute(self, query: str, params: tuple[object, ...] = ()) -> None:
        self.query = query
        self.params = params
        self.executed.append((query, params))

    def fetchall(self) -> list[dict[str, object]]:
        if "FROM system_alert_notes" in self.query and "FROM system_alert_history" not in self.query:
            return self.note_rows
        if "FROM system_alert_history" in self.query:
            return self.alert_rows
        if "FROM worker_run_history" not in self.query and self.rows is None:
            return []
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
        if "COUNT(*) AS total" in self.query:
            if "FROM worker_run_history" in self.query:
                return {"total": len(self.rows) if self.rows is not None else 1}
            return {"total": len(self.alert_rows)}
        if "FROM system_alert_notes" in self.query and "FROM system_alert_history" not in self.query:
            return self.note_rows[0] if self.note_rows else {}
        if "FROM system_alert_history" in self.query:
            return self.alert_rows[0] if self.alert_rows else {}
        if "FROM worker_run_history" in self.query and "WHERE id = %s" in self.query:
            rows = self.fetchall()
            try:
                target_id = int(self.params[0])
            except (TypeError, ValueError, IndexError):
                return {}
            for row in rows:
                if int(row.get("id") or 0) == target_id:
                    return row
            return {}
        return {"latest_id": self.latest_id}


def _alert_history_row() -> dict[str, object]:
    return {
        "id": 7,
        "alert_key": "storage:raw_data_dir:D:/BackUP/raw:missing",
        "source": "storage",
        "severity": "error",
        "title": "raw_data_dir storage alert",
        "message": "path missing",
        "metadata_json": '{"role":"raw_data_dir"}',
        "first_seen_at_kst": "2026-06-30T10:00:00+09:00",
        "last_seen_at_kst": "2026-06-30T10:01:00+09:00",
        "last_notified_at_kst": None,
        "acknowledged_at_kst": None,
        "snoozed_until_kst": None,
        "resolved_at_kst": None,
        "updated_at_kst": "2026-06-30T10:01:00+09:00",
    }


def _alert_note_row() -> dict[str, object]:
    return {
        "id": 21,
        "alert_history_id": 7,
        "note_type": "resolution",
        "note_text": "Checked D drive and left the raw path unchanged.",
        "created_by": "local-admin",
        "created_at_kst": "2026-06-30T10:05:00+09:00",
    }


if __name__ == "__main__":
    unittest.main()
