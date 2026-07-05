from __future__ import annotations

from datetime import datetime
import unittest

from pubg_ai.discord_bot import (
    _parse_alert_history_filters,
    _parse_worker_run_filters,
    _player_visible_to_scope,
    format_alert_action_result,
    format_alert_command_reply,
    format_alerts_command_reply,
    format_alert_history_command_reply,
    format_alert_history_result,
    format_alert_note_result,
    format_alert_notes_result,
    format_player_list,
    format_player_match_detail,
    format_player_profile_stats,
    format_player_ranking,
    format_player_weapon_detail,
    format_replay_artifact_summary,
    format_worker_run_command_reply,
    format_worker_run_detail_result,
    format_worker_run_history_command_reply,
    format_worker_run_history_result,
)
from pubg_ai.alert_history import AlertHistoryNote, AlertHistoryPage, AlertHistoryRecord
from pubg_ai.player_rankings import PlayerRanking, PlayerRankingRow
from pubg_ai.player_registry import RegisteredPlayer
from pubg_ai.player_stats import (
    PlayerCombatTotals,
    PlayerMatchDetail,
    PlayerMatchWeaponStats,
    PlayerProfileStats,
    PlayerRecentMatch,
    PlayerWeaponDetail,
    PlayerWeaponDetailTotals,
    PlayerWeaponRecentMatch,
    PlayerWeaponStats,
)
from pubg_ai.replay_artifact_catalog import ReplayArtifactRecord
from pubg_ai.time_utils import KST
from pubg_ai.worker_run_history import WorkerRunPage, WorkerRunRecord


class DiscordBotFormattingTests(unittest.TestCase):
    def test_alert_action_result_formats_discord_admin_response(self) -> None:
        record = AlertHistoryRecord(
            id=7,
            alert_key="worker:7",
            source="worker",
            severity="error",
            title="collector worker failed",
            message="drive missing",
            metadata={},
            first_seen_at_kst="2026-06-30T10:00:00+09:00",
            last_seen_at_kst="2026-06-30T10:01:00+09:00",
            last_notified_at_kst=None,
            acknowledged_at_kst="2026-06-30T10:02:00+09:00",
            snoozed_until_kst=None,
            resolved_at_kst=None,
            updated_at_kst="2026-06-30T10:02:00+09:00",
        )

        body = format_alert_action_result(record, "acknowledged")

        self.assertIn("PUBG AI alert acknowledged", body)
        self.assertIn("- id: 7", body)
        self.assertIn("collector worker failed", body)
        self.assertIn("acknowledged_at_kst", body)
        self.assertNotIn("local_detail", body)

        linked = format_alert_action_result(record, "acknowledged", detail_base_url="http://127.0.0.1:8000/")
        self.assertIn(
            "- local_detail: [detail](http://127.0.0.1:8000/?alert_id=7#alertHistoryDetail)",
            linked,
        )

    def test_alert_note_result_formats_discord_admin_response(self) -> None:
        note = AlertHistoryNote(
            id=12,
            alert_history_id=7,
            note_type="resolution",
            note_text="raw drive expanded and worker restarted",
            created_by="discord:987654321:123456789",
            created_at_kst="2026-06-30T10:05:00+09:00",
        )

        body = format_alert_note_result(note)

        self.assertIn("PUBG AI alert resolution saved", body)
        self.assertIn("- alert_id: 7", body)
        self.assertIn("- note_id: 12", body)
        self.assertIn("- type: resolution", body)
        self.assertIn("discord:987654321:123456789", body)
        self.assertIn("raw drive expanded and worker restarted", body)
        self.assertNotIn("local_detail", body)

        linked = format_alert_note_result(note, detail_base_url="http://127.0.0.1:8000/")
        self.assertIn(
            "- local_detail: [detail](http://127.0.0.1:8000/?alert_id=7#alertHistoryDetail)",
            linked,
        )

    def test_alert_notes_result_formats_recent_notes(self) -> None:
        record = AlertHistoryRecord(
            id=7,
            alert_key="worker:7",
            source="worker",
            severity="error",
            title="collector worker failed with a very long title that should still fit in one Discord line",
            message="drive missing",
            metadata={},
            first_seen_at_kst="2026-06-30T10:00:00+09:00",
            last_seen_at_kst="2026-06-30T10:01:00+09:00",
            last_notified_at_kst=None,
            acknowledged_at_kst=None,
            snoozed_until_kst=None,
            resolved_at_kst=None,
            updated_at_kst="2026-06-30T10:02:00+09:00",
            note_count=2,
        )
        notes = [
            AlertHistoryNote(
                id=12,
                alert_history_id=7,
                note_type="resolution",
                note_text="raw drive expanded\nworker restarted",
                created_by="discord:987654321:123456789",
                created_at_kst="2026-06-30T10:05:00+09:00",
            )
        ]

        body = format_alert_notes_result(record, notes)

        self.assertIn("PUBG AI alert notes", body)
        self.assertIn("- alert_id: 7", body)
        self.assertIn("- shown/total: 1/2", body)
        self.assertIn("#12 resolution 2026-06-30T10:05:00+09:00", body)
        self.assertIn("raw drive expanded worker restarted", body)
        self.assertNotIn("local_detail", body)

        linked = format_alert_notes_result(record, notes, detail_base_url="http://127.0.0.1:8000/")
        self.assertIn(
            "- local_detail: [detail](http://127.0.0.1:8000/?alert_id=7#alertHistoryDetail)",
            linked,
        )

    def test_alert_command_reply_adds_detail_only_when_alert_id_and_url_are_available(self) -> None:
        message = "Usage: `!pubg-alert-note alert_id note`"

        self.assertEqual(format_alert_command_reply(message), message)
        self.assertEqual(
            format_alert_command_reply(message, 7),
            message,
        )
        self.assertEqual(
            format_alert_command_reply(message, None, detail_base_url="http://127.0.0.1:8000/"),
            message,
        )

        linked = format_alert_command_reply(message, 7, detail_base_url="http://127.0.0.1:8000/")

        self.assertIn(message, linked)
        self.assertIn(
            "- local_detail: [detail](http://127.0.0.1:8000/?alert_id=7#alertHistoryDetail)",
            linked,
        )

    def test_alerts_command_reply_adds_current_alerts_link_when_url_is_available(self) -> None:
        message = "PUBG AI alert settings error: local settings file is invalid"

        self.assertEqual(format_alerts_command_reply(message), message)

        linked = format_alerts_command_reply(message, detail_base_url="http://127.0.0.1:8000/")

        self.assertIn(message, linked)
        self.assertIn(
            "- current_alerts: [open](http://127.0.0.1:8000/?"
            "alert_history_source=all&alert_history_state=current&alert_history_severity=all&"
            "alert_history_sort=severity&alert_history_search=&"
            "alert_history_limit=50&alert_history_offset=0#alerts)",
            linked,
        )

    def test_alert_history_result_formats_filters_and_rows(self) -> None:
        record = AlertHistoryRecord(
            id=7,
            alert_key="worker:7",
            source="worker",
            severity="error",
            title="collector worker failed",
            message="raw drive disconnected during match collection",
            metadata={},
            first_seen_at_kst="2026-06-30T10:00:00+09:00",
            last_seen_at_kst="2026-06-30T10:01:00+09:00",
            last_notified_at_kst=None,
            acknowledged_at_kst=None,
            snoozed_until_kst=None,
            resolved_at_kst=None,
            updated_at_kst="2026-06-30T10:02:00+09:00",
        )
        page = AlertHistoryPage(
            records=[record],
            total=3,
            limit=1,
            offset=0,
            source="worker",
            state="current",
            severity="error",
            sort="severity",
            search="raw drive",
        )

        body = format_alert_history_result(page)

        self.assertIn("PUBG AI alert history", body)
        self.assertIn("source=worker state=current severity=error sort=severity search=raw drive", body)
        self.assertIn("shown/total: 1/3", body)
        self.assertIn("#7 [worker/error/current]", body)
        self.assertIn("collector worker failed", body)
        self.assertIn("next: `!pubg-alert-history", body)
        self.assertIn("offset=1", body)
        self.assertNotIn("previous:", body)
        self.assertNotIn("[detail]", body)
        self.assertNotIn("filter_page", body)
        self.assertNotIn("export_csv", body)

        linked = format_alert_history_result(page, detail_base_url="http://127.0.0.1:8000/")
        self.assertIn(
            "filter_page: [open](http://127.0.0.1:8000/?"
            "alert_history_source=worker&alert_history_state=current&alert_history_severity=error&"
            "alert_history_sort=severity&alert_history_search=raw+drive&alert_history_limit=1&alert_history_offset=0#alerts)",
            linked,
        )
        self.assertIn(
            "export_csv: [download](http://127.0.0.1:8000/alerts/history/export.csv?"
            "source=worker&state=current&severity=error&sort=severity&search=raw+drive&limit=5000&offset=0)",
            linked,
        )
        self.assertIn("[detail](http://127.0.0.1:8000/?alert_id=7#alertHistoryDetail)", linked)

    def test_alert_history_command_reply_formats_filter_page_link(self) -> None:
        message = "PUBG AI alert history error: failed to read system_alert_history"

        self.assertEqual(format_alert_history_command_reply(message), message)

        default_linked = format_alert_history_command_reply(
            "Usage: `!pubg-alert-history ...`\nError: invalid state",
            detail_base_url="http://127.0.0.1:8000/",
        )
        self.assertIn(
            "filter_page: [open](http://127.0.0.1:8000/?"
            "alert_history_source=all&alert_history_state=all&alert_history_severity=all&"
            "alert_history_sort=newest&alert_history_search=&"
            "alert_history_limit=5&alert_history_offset=0#alerts)",
            default_linked,
        )
        self.assertIn(
            "export_csv: [download](http://127.0.0.1:8000/alerts/history/export.csv?"
            "source=all&state=all&severity=all&sort=newest&search=&limit=5000&offset=0)",
            default_linked,
        )

        linked = format_alert_history_command_reply(
            message,
            source="storage",
            state="resolved",
            severity="warning",
            sort="oldest",
            search="disk full",
            limit=4,
            offset=8,
            detail_base_url="http://127.0.0.1:8000/",
        )

        self.assertIn(message, linked)
        self.assertIn(
            "filter_page: [open](http://127.0.0.1:8000/?"
            "alert_history_source=storage&alert_history_state=resolved&alert_history_severity=warning&"
            "alert_history_sort=oldest&alert_history_search=disk+full&"
            "alert_history_limit=4&alert_history_offset=8#alerts)",
            linked,
        )
        self.assertIn(
            "export_csv: [download](http://127.0.0.1:8000/alerts/history/export.csv?"
            "source=storage&state=resolved&severity=warning&sort=oldest&search=disk+full&limit=5000&offset=0)",
            linked,
        )

    def test_alert_history_result_formats_previous_and_next_hints(self) -> None:
        page = AlertHistoryPage(
            records=[
                AlertHistoryRecord(
                    id=8,
                    alert_key="storage:raw",
                    source="storage",
                    severity="warning",
                    title="raw storage alert",
                    message="disk space below threshold",
                    metadata={},
                    first_seen_at_kst=None,
                    last_seen_at_kst="2026-06-30T10:01:00+09:00",
                    last_notified_at_kst=None,
                    acknowledged_at_kst=None,
                    snoozed_until_kst=None,
                    resolved_at_kst=None,
                    updated_at_kst=None,
                )
            ],
            total=3,
            limit=1,
            offset=1,
            source="storage",
            state="active",
            severity="warning",
            sort="oldest",
            search="disk full",
        )

        body = format_alert_history_result(page, command_prefix="?")

        self.assertIn("previous: `?pubg-alert-history", body)
        self.assertIn("next: `?pubg-alert-history", body)
        self.assertIn("source=storage", body)
        self.assertIn("state=active", body)
        self.assertIn("severity=warning", body)
        self.assertIn("sort=oldest", body)
        self.assertIn("limit=1", body)
        self.assertIn("offset=0", body)
        self.assertIn("offset=2", body)
        self.assertIn("search='disk full'", body)

    def test_alert_history_filter_parser_supports_presets_and_search_terms(self) -> None:
        filters = _parse_alert_history_filters("current-errors limit=7 raw drive")

        self.assertEqual(filters["source"], "all")
        self.assertEqual(filters["state"], "current")
        self.assertEqual(filters["severity"], "error")
        self.assertEqual(filters["sort"], "severity")
        self.assertEqual(filters["limit"], 7)
        self.assertEqual(filters["search"], "raw drive")

    def test_alert_history_filter_parser_supports_key_value_filters(self) -> None:
        filters = _parse_alert_history_filters(
            'source=storage status=resolved severity=warning sort=oldest q="disk full" offset=10 limit=99'
        )

        self.assertEqual(filters["source"], "storage")
        self.assertEqual(filters["state"], "resolved")
        self.assertEqual(filters["severity"], "warning")
        self.assertEqual(filters["sort"], "oldest")
        self.assertEqual(filters["search"], "disk full")
        self.assertEqual(filters["offset"], 10)
        self.assertEqual(filters["limit"], 10)

    def test_worker_run_history_result_formats_recent_runs(self) -> None:
        runs = [
            WorkerRunRecord(
                id=12,
                worker_name="collector",
                status="failed",
                started_at_kst="2026-07-01T10:00:00+09:00",
                finished_at_kst="2026-07-01T10:00:03+09:00",
                duration_seconds=3.2,
                error_count=1,
                last_error="match_jobs: RuntimeError: raw drive disconnected",
                summary={},
                created_at_kst="2026-07-01T10:00:03+09:00",
            ),
            WorkerRunRecord(
                id=11,
                worker_name="post_processing",
                status="succeeded",
                started_at_kst="2026-07-01T09:55:00+09:00",
                finished_at_kst="2026-07-01T09:55:05+09:00",
                duration_seconds=5,
                error_count=0,
                last_error=None,
                summary={},
                created_at_kst="2026-07-01T09:55:05+09:00",
            ),
        ]

        page = WorkerRunPage(records=runs, total=3, limit=2, offset=0, status="all")

        body = format_worker_run_history_result(page)

        self.assertIn("PUBG AI worker run history", body)
        self.assertIn("worker=all status=all", body)
        self.assertIn("shown/total: 2/3 offset=0 limit=2", body)
        self.assertIn("#12 [collector/failed]", body)
        self.assertIn("duration=3.2s errors=1", body)
        self.assertIn("raw drive disconnected", body)
        self.assertIn("detail: `!pubg-worker-run 12`", body)
        self.assertNotIn("[detail](http://127.0.0.1:8000/?worker_run_id=12#workerRunDetail)", body)
        self.assertIn("#11 [post_processing/succeeded]", body)
        self.assertIn("duration=5.0s errors=0 last_error=-", body)
        self.assertIn("detail: `!pubg-worker-run 11`", body)
        self.assertIn("next: `!pubg-worker-runs worker=all status=all limit=2 offset=2`", body)
        self.assertNotIn("previous:", body)
        self.assertNotIn("filter_page", body)
        self.assertNotIn("export_csv", body)

        linked_body = format_worker_run_history_result(page, detail_base_url="http://127.0.0.1:8000/")
        self.assertIn(
            "filter_page: [open](http://127.0.0.1:8000/?"
            "worker_run_worker=all&worker_run_status=all&worker_run_range=custom&"
            "worker_run_from=&worker_run_to=&worker_run_limit=2&worker_run_offset=0#worker-runs)",
            linked_body,
        )
        self.assertIn(
            "export_csv: [download](http://127.0.0.1:8000/workers/runs/export.csv?"
            "worker_name=&status=all&created_from_kst=&created_to_kst=&limit=5000&offset=0)",
            linked_body,
        )
        self.assertIn("[detail](http://127.0.0.1:8000/?worker_run_id=12#workerRunDetail)", linked_body)
        self.assertIn("[detail](http://127.0.0.1:8000/?worker_run_id=11#workerRunDetail)", linked_body)
        self.assertIn("detail: `!pubg-worker-run 12`", linked_body)

        custom_prefix_body = format_worker_run_history_result(page, command_prefix="?")
        self.assertIn("detail: `?pubg-worker-run 12`", custom_prefix_body)
        self.assertIn("next: `?pubg-worker-runs worker=all status=all limit=2 offset=2`", custom_prefix_body)

    def test_worker_run_history_result_formats_empty_state(self) -> None:
        page = WorkerRunPage(records=[], total=0, limit=3, offset=0, worker_name="collector", status="failed")
        body = format_worker_run_history_result(page)

        self.assertIn("worker=collector status=failed", body)
        self.assertIn("shown/total: 0/0 offset=0 limit=3", body)
        self.assertIn("no worker runs yet", body)

    def test_worker_run_history_command_reply_formats_filter_page_link(self) -> None:
        message = "PUBG AI worker run history error: failed to read worker_run_history"

        self.assertEqual(format_worker_run_history_command_reply(message), message)

        default_linked = format_worker_run_history_command_reply(
            "Usage: `!pubg-worker-runs ...`\nError: invalid worker",
            detail_base_url="http://127.0.0.1:8000/",
        )
        self.assertIn(
            "filter_page: [open](http://127.0.0.1:8000/?"
            "worker_run_worker=all&worker_run_status=all&worker_run_range=custom&"
            "worker_run_from=&worker_run_to=&worker_run_limit=5&worker_run_offset=0#worker-runs)",
            default_linked,
        )
        self.assertIn(
            "export_csv: [download](http://127.0.0.1:8000/workers/runs/export.csv?"
            "worker_name=&status=all&created_from_kst=&created_to_kst=&limit=5000&offset=0)",
            default_linked,
        )

        linked = format_worker_run_history_command_reply(
            message,
            worker_name="collector",
            status="failed",
            limit=4,
            offset=8,
            created_from_kst="2026-07-01T09:00:00+09:00",
            created_to_kst="2026-07-01T10:00:00+09:00",
            detail_base_url="http://127.0.0.1:8000/",
        )

        self.assertIn(message, linked)
        self.assertIn(
            "filter_page: [open](http://127.0.0.1:8000/?"
            "worker_run_worker=collector&worker_run_status=failed&worker_run_range=custom&"
            "worker_run_from=2026-07-01T09%3A00%3A00%2B09%3A00&"
            "worker_run_to=2026-07-01T10%3A00%3A00%2B09%3A00&"
            "worker_run_limit=4&worker_run_offset=8#worker-runs)",
            linked,
        )
        self.assertIn(
            "export_csv: [download](http://127.0.0.1:8000/workers/runs/export.csv?"
            "worker_name=collector&status=failed&created_from_kst=2026-07-01T09%3A00%3A00%2B09%3A00&"
            "created_to_kst=2026-07-01T10%3A00%3A00%2B09%3A00&limit=5000&offset=0)",
            linked,
        )

    def test_worker_run_history_result_formats_previous_and_next_hints(self) -> None:
        page = WorkerRunPage(
            records=[
                WorkerRunRecord(
                    id=10,
                    worker_name="collector",
                    status="failed",
                    started_at_kst="2026-07-01T09:50:00+09:00",
                    finished_at_kst="2026-07-01T09:50:02+09:00",
                    duration_seconds=2,
                    error_count=1,
                    last_error="boom",
                    summary={},
                    created_at_kst="2026-07-01T09:50:02+09:00",
                )
            ],
            total=3,
            limit=1,
            offset=1,
            worker_name="collector",
            status="failed",
            created_from_kst="2026-07-01T09:00:00+09:00",
            created_to_kst="2026-07-01T10:00:00+09:00",
        )

        body = format_worker_run_history_result(page, command_prefix="?")

        self.assertIn("created=2026-07-01T09:00:00+09:00..2026-07-01T10:00:00+09:00", body)
        self.assertIn(
            "previous: `?pubg-worker-runs worker=collector status=failed limit=1 offset=0 "
            "from=2026-07-01T09:00:00+09:00 to=2026-07-01T10:00:00+09:00`",
            body,
        )

        linked_body = format_worker_run_history_result(page, detail_base_url="http://127.0.0.1:8000/")
        self.assertIn("worker_run_worker=collector", linked_body)
        self.assertIn("worker_run_status=failed", linked_body)
        self.assertIn("worker_run_range=custom", linked_body)
        self.assertIn("worker_run_from=2026-07-01T09%3A00%3A00%2B09%3A00", linked_body)
        self.assertIn("worker_run_to=2026-07-01T10%3A00%3A00%2B09%3A00", linked_body)
        self.assertIn("worker_run_limit=1&worker_run_offset=1#worker-runs", linked_body)
        self.assertIn("worker_name=collector", linked_body)
        self.assertIn("status=failed", linked_body)
        self.assertIn("created_from_kst=2026-07-01T09%3A00%3A00%2B09%3A00", linked_body)
        self.assertIn("created_to_kst=2026-07-01T10%3A00%3A00%2B09%3A00", linked_body)
        self.assertIn("limit=5000&offset=0", linked_body)
        self.assertIn(
            "next: `?pubg-worker-runs worker=collector status=failed limit=1 offset=2 "
            "from=2026-07-01T09:00:00+09:00 to=2026-07-01T10:00:00+09:00`",
            body,
        )

    def test_worker_run_detail_result_formats_summary_metrics_and_errors(self) -> None:
        run = WorkerRunRecord(
            id=12,
            worker_name="collector",
            status="failed",
            started_at_kst="2026-07-01T10:00:00+09:00",
            finished_at_kst="2026-07-01T10:00:03+09:00",
            duration_seconds=3.25,
            error_count=2,
            last_error="telemetry_jobs: RuntimeError: telemetry missing",
            summary={
                "started_at_kst": "2026-07-01T10:00:00+09:00",
                "finished_at_kst": "2026-07-01T10:00:03+09:00",
                "duration_seconds": 3.25,
                "poll_interval_seconds": 180,
                "collection": {"queued_match_jobs": 2, "existing_match_jobs": 1},
                "match_jobs": {"stored_matches": 4, "queued_telemetry_jobs": 3},
                "errors": ["match_jobs: RuntimeError: boom", "telemetry_jobs: RuntimeError: telemetry missing"],
            },
            created_at_kst="2026-07-01T10:00:03+09:00",
        )

        body = format_worker_run_detail_result(run)

        self.assertIn("PUBG AI worker run detail", body)
        self.assertIn("- id: 12", body)
        self.assertIn("worker/status: collector/failed", body)
        self.assertIn("duration/errors: 3.2s / 2", body)
        self.assertNotIn("local_detail", body)
        self.assertIn("poll_interval_seconds=180", body)
        self.assertIn("collection.queued_match_jobs=2", body)
        self.assertIn("collection.existing_match_jobs=1", body)
        self.assertIn("match_jobs.stored_matches=4", body)
        self.assertIn("1. match_jobs: RuntimeError: boom", body)
        self.assertIn("2. telemetry_jobs: RuntimeError: telemetry missing", body)

        linked_body = format_worker_run_detail_result(run, detail_base_url="http://127.0.0.1:8000/")
        self.assertIn("- local_detail: [detail](http://127.0.0.1:8000/?worker_run_id=12#workerRunDetail)", linked_body)
        self.assertIn("collection.queued_match_jobs=2", linked_body)

    def test_worker_run_command_reply_adds_detail_only_when_run_id_and_url_are_available(self) -> None:
        message = "PUBG AI worker run detail error: worker run not found: 12"

        self.assertEqual(format_worker_run_command_reply(message), message)
        self.assertEqual(
            format_worker_run_command_reply(message, 12),
            message,
        )
        self.assertEqual(
            format_worker_run_command_reply(message, None, detail_base_url="http://127.0.0.1:8000/"),
            message,
        )

        linked = format_worker_run_command_reply(message, 12, detail_base_url="http://127.0.0.1:8000/")

        self.assertIn(message, linked)
        self.assertIn(
            "- local_detail: [detail](http://127.0.0.1:8000/?worker_run_id=12#workerRunDetail)",
            linked,
        )

    def test_worker_run_filter_parser_supports_worker_aliases_and_limit(self) -> None:
        filters = _parse_worker_run_filters("post-processing 99")

        self.assertEqual(filters["worker_name"], "post_processing")
        self.assertEqual(filters["limit"], 10)

        keyed = _parse_worker_run_filters(
            "worker=collector status=failed limit=4 offset=8 "
            "from=2026-07-01T09:00 to=2026-07-01T10:00"
        )
        self.assertEqual(keyed["worker_name"], "collector")
        self.assertEqual(keyed["status"], "failed")
        self.assertEqual(keyed["limit"], 4)
        self.assertEqual(keyed["offset"], 8)
        self.assertEqual(keyed["created_from_kst"], "2026-07-01T09:00")
        self.assertEqual(keyed["created_to_kst"], "2026-07-01T10:00")

        aliased_dates = _parse_worker_run_filters(
            'created_from_kst="2026-07-01 09:00" created_to=2026-07-01T11:00'
        )
        self.assertEqual(aliased_dates["created_from_kst"], "2026-07-01 09:00")
        self.assertEqual(aliased_dates["created_to_kst"], "2026-07-01T11:00")

        all_workers = _parse_worker_run_filters("all succeeded")
        self.assertIsNone(all_workers["worker_name"])
        self.assertEqual(all_workers["status"], "succeeded")

        reference = datetime(2026, 7, 3, 12, 30, 15, tzinfo=KST)
        today = _parse_worker_run_filters("range=today", reference_kst=reference)
        self.assertEqual(today["created_from_kst"], "2026-07-03T00:00:00+09:00")
        self.assertEqual(today["created_to_kst"], "2026-07-03T12:30:15+09:00")

        last_24h = _parse_worker_run_filters("preset=last24h", reference_kst=reference)
        self.assertEqual(last_24h["created_from_kst"], "2026-07-02T12:30:15+09:00")
        self.assertEqual(last_24h["created_to_kst"], "2026-07-03T12:30:15+09:00")

        yesterday = _parse_worker_run_filters("quick_range=yesterday", reference_kst=reference)
        self.assertEqual(yesterday["created_from_kst"], "2026-07-02T00:00:00+09:00")
        self.assertEqual(yesterday["created_to_kst"], "2026-07-03T00:00:00+09:00")

        with self.assertRaises(ValueError):
            _parse_worker_run_filters("range=tomorrow", reference_kst=reference)

    def test_player_list_formats_status_and_short_account_id(self) -> None:
        players = [
            RegisteredPlayer(
                id=1,
                account_id="account.1234567890abcdef",
                shard="steam",
                current_name="Yuuki_Asuna---",
                active=True,
                public_profile=True,
            ),
            RegisteredPlayer(
                id=2,
                account_id="account.abcdef1234567890",
                shard="kakao",
                current_name="StoppedPlayer",
                active=False,
                public_profile=False,
            ),
        ]

        body = format_player_list(players)

        self.assertIn("등록 유저", body)
        self.assertIn("Yuuki_Asuna--- (steam) / 수집중 / 공개", body)
        self.assertIn("StoppedPlayer (kakao) / 중지 / 비공개", body)
        self.assertIn("account.1234567...cdef", body)
        self.assertNotIn("account.1234567890abcdef", body)

    def test_empty_player_list_has_clear_message(self) -> None:
        self.assertEqual(format_player_list([]), "등록된 유저가 없습니다.")

    def test_profile_stats_summary_formats_core_metrics(self) -> None:
        profile = PlayerProfileStats(
            player=RegisteredPlayer(
                id=1,
                account_id="account.test",
                shard="steam",
                current_name="Yuuki_Asuna---",
                active=True,
                public_profile=True,
            ),
            totals=PlayerCombatTotals(
                match_count=10,
                wins=2,
                kills=25,
                assists=5,
                deaths=8,
                dbnos_caused=13,
                dbnos_taken=4,
                damage_dealt=2500.0,
                damage_taken=1600.0,
                shots_fired=1000,
                shots_hit=210,
                headshot_kills=6,
                avg_damage_dealt=250.0,
                avg_damage_taken=160.0,
                win_rate=0.2,
                kda=3.75,
                accuracy=0.21,
                headshot_kill_rate=0.24,
                avg_survival_seconds=1420.5,
                avg_movement_distance_m=3650.0,
            ),
            top_weapons=[
                PlayerWeaponStats(
                    weapon_code="WeapBerylM762_C",
                    weapon_name="베릴 M762",
                    match_count=6,
                    kills=12,
                    assists=2,
                    deaths=3,
                    dbnos=8,
                    damage_dealt=1200.0,
                    shots_fired=500,
                    shots_hit=95,
                    accuracy=0.19,
                    headshot_kills=2,
                )
            ],
            recent_matches=[
                PlayerRecentMatch(
                    match_id="match-123456789",
                    created_at_kst=datetime(2026, 6, 29, 1, 0, 0),
                    map_name="Erangel_Main",
                    game_mode="squad-fpp",
                    match_type="official",
                    win_place=1,
                    kills=5,
                    assists=1,
                    deaths=0,
                    dbnos_caused=3,
                    damage_dealt=550.0,
                    survival_seconds=1788.5,
                    movement_distance_m=4200.0,
                )
            ],
        )

        body = format_player_profile_stats(profile)

        self.assertIn("Yuuki_Asuna--- 전적 (steam)", body)
        self.assertIn("10전 2치킨 (20.0%)", body)
        self.assertIn("25/8/5", body)
        self.assertIn("KDA 3.75", body)
        self.assertIn("베릴 M762 12킬 1200딜", body)
        self.assertIn("match-12 #1 5킬/550딜", body)

    def test_weapon_detail_summary_formats_weapon_metrics(self) -> None:
        detail = PlayerWeaponDetail(
            player=RegisteredPlayer(
                id=1,
                account_id="account.test",
                shard="steam",
                current_name="Yuuki_Asuna---",
                active=True,
                public_profile=True,
            ),
            weapon_code="WeapHK416_C",
            weapon_name="M416",
            totals=PlayerWeaponDetailTotals(
                match_count=12,
                wins=2,
                kills=20,
                assists=4,
                deaths_taken=1,
                dbnos=16,
                dbnos_taken=0,
                finishes=8,
                finishes_taken=0,
                damage_dealt=2400.0,
                damage_taken=90.0,
                shots_fired=1000,
                shots_hit=230,
                hits_taken=1,
                headshot_hits=30,
                headshot_kills=5,
                headshot_dbnos=4,
                accuracy=0.23,
                avg_damage_dealt=200.0,
                win_rate=2 / 12,
                headshot_kill_rate=0.25,
                hit_parts={"head": 30, "torso": 140},
                taken_hit_parts={"arm": 1},
            ),
            recent_matches=[
                PlayerWeaponRecentMatch(
                    match_id="match-123456789",
                    created_at_kst=datetime(2026, 6, 29, 1, 0, 0),
                    map_name="Erangel_Main",
                    game_mode="squad-fpp",
                    win_place=1,
                    kills=4,
                    assists=1,
                    deaths_taken=0,
                    dbnos=3,
                    damage_dealt=520.0,
                    shots_fired=120,
                    shots_hit=40,
                    accuracy=1 / 3,
                )
            ],
        )

        body = format_player_weapon_detail(detail)

        self.assertIn("Yuuki_Asuna--- M416 무기 통계", body)
        self.assertIn("12전 2치킨", body)
        self.assertIn("20/4/16", body)
        self.assertIn("23.0%", body)
        self.assertIn("몸통 140", body)
        self.assertIn("match-12 #1 4킬/3기절/520딜", body)

    def test_match_detail_summary_formats_core_metrics(self) -> None:
        artifact = ReplayArtifactRecord(
            id=10,
            match_id="match-123456789",
            shard="steam",
            artifact_type="map_snapshot",
            artifact_name="player-route",
            account_id="account.test",
            player_name="Yuuki_Asuna---",
            map_name="Erangel_Main",
            game_mode="squad-fpp",
            match_type="official",
            match_created_at_kst=datetime(2026, 6, 29, 1, 0, 0),
            storage_backend="local_file",
            storage_root="PUBG_REPLAY_DATA_DIR",
            relative_path="map_snapshot/steam/2026/06/29/match-123456789/player-route.jpg",
            content_type="image/jpeg",
            size_bytes=2048,
            sha256="abc123",
            renderer_version="test",
            generated_at_kst=datetime(2026, 6, 29, 1, 3, 0),
        )
        detail = PlayerMatchDetail(
            player=RegisteredPlayer(
                id=1,
                account_id="account.test",
                shard="steam",
                current_name="Yuuki_Asuna---",
                active=True,
                public_profile=True,
            ),
            match_id="match-123456789",
            shard="steam",
            map_name="Erangel_Main",
            game_mode="squad-fpp",
            match_type="official",
            created_at_kst=datetime(2026, 6, 29, 1, 0, 0),
            duration_seconds=1800,
            total_players=100,
            human_players=96,
            bot_players=4,
            roster_id="roster-1",
            team_id=12,
            win_place=2,
            is_chicken=False,
            death_type="byplayer",
            kills=4,
            assists=1,
            deaths=1,
            dbnos_caused=5,
            dbnos_taken=1,
            finishes=3,
            finishes_taken=1,
            damage_dealt=620.0,
            damage_taken=310.0,
            shots_fired=200,
            shots_hit=50,
            hits_taken=8,
            accuracy=0.25,
            headshot_hits=10,
            headshot_hits_taken=2,
            headshot_kills=2,
            headshot_deaths=0,
            headshot_dbnos_caused=2,
            headshot_dbnos_taken=0,
            hit_parts={"head": 10, "torso": 34},
            taken_hit_parts={"arm": 3},
            survival_seconds=1750.5,
            landing_distance_m=760.0,
            movement_distance_m=3500.0,
            weapons=[
                PlayerMatchWeaponStats(
                    weapon_code="WeapHK416_C",
                    weapon_name="M416",
                    kills=3,
                    assists=1,
                    deaths=0,
                    dbnos=4,
                    dbnos_taken=1,
                    damage_dealt=420.0,
                    damage_taken=50.0,
                    shots_fired=120,
                    shots_hit=36,
                    accuracy=0.3,
                    headshot_kills=1,
                    hit_parts={"head": 6},
                    taken_hit_parts={"arm": 1},
                )
            ],
            replay_artifact=artifact,
        )

        body = format_player_match_detail(detail)

        self.assertIn("Yuuki_Asuna--- 매치 상세 (steam)", body)
        self.assertIn("match-123456789", body)
        self.assertIn("총 100명, 사람 96명, 봇 4명", body)
        self.assertIn("4/1/1/5", body)
        self.assertIn("200/50/25.0%", body)
        self.assertIn("M416 3킬/4기절/420딜/30.0%", body)
        self.assertIn("!최근스냅샷 match-123456789", body)

    def test_player_ranking_summary_formats_rows(self) -> None:
        ranking = PlayerRanking(
            metric="kda",
            metric_label="KDA",
            shard="steam",
            guild_id="guild-1",
            global_scope=False,
            active_only=True,
            min_matches=1,
            rows=[
                PlayerRankingRow(
                    rank=1,
                    player=RegisteredPlayer(
                        id=1,
                        account_id="account.test",
                        shard="steam",
                        current_name="Yuuki_Asuna---",
                        active=True,
                        public_profile=True,
                    ),
                    score=3.75,
                    match_count=10,
                    wins=2,
                    kills=25,
                    assists=5,
                    deaths=8,
                    dbnos_caused=13,
                    dbnos_taken=4,
                    damage_dealt=2500.0,
                    damage_taken=1600.0,
                    shots_fired=1000,
                    shots_hit=210,
                    headshot_kills=6,
                    avg_damage_dealt=250.0,
                    avg_damage_taken=160.0,
                    win_rate=0.2,
                    kda=3.75,
                    accuracy=0.21,
                    headshot_kill_rate=0.24,
                    avg_survival_seconds=1420.5,
                    avg_movement_distance_m=3650.0,
                    last_match_at_kst=datetime(2026, 6, 29, 1, 0, 0),
                )
            ],
        )

        body = format_player_ranking(ranking)

        self.assertIn("KDA 랭킹 (steam, 서버 guild-1)", body)
        self.assertIn("#1 Yuuki_Asuna---: 3.75", body)
        self.assertIn("10전 2치킨", body)
        self.assertIn("25K/8D/5A", body)

    def test_player_scope_visibility_requires_matching_guild_or_global_scope(self) -> None:
        player = RegisteredPlayer(
            id=1,
            account_id="account.1234567890abcdef",
            shard="steam",
            current_name="Yuuki_Asuna---",
            active=True,
            public_profile=True,
            registered_guild_id="guild-1",
        )

        self.assertTrue(_player_visible_to_scope(player, "guild-1", False))
        self.assertFalse(_player_visible_to_scope(player, "guild-2", False))
        self.assertFalse(_player_visible_to_scope(player, None, False))
        self.assertTrue(_player_visible_to_scope(player, None, True))

    def test_replay_artifact_summary_formats_match_and_size(self) -> None:
        artifact = ReplayArtifactRecord(
            id=10,
            match_id="match-123",
            shard="steam",
            artifact_type="map_snapshot",
            artifact_name="route-summary",
            account_id="account.1234567890abcdef",
            player_name="Yuuki_Asuna---",
            map_name="Erangel",
            game_mode="squad-fpp",
            match_type="official",
            match_created_at_kst=datetime(2026, 6, 29, 1, 2, 3),
            storage_backend="local_file",
            storage_root="PUBG_REPLAY_DATA_DIR",
            relative_path="map_snapshot/steam/2026/06/29/match-123/route-summary.jpg",
            content_type="image/jpeg",
            size_bytes=2048,
            sha256="abc123",
            renderer_version="test",
            generated_at_kst=datetime(2026, 6, 29, 1, 3, 3),
        )

        body = format_replay_artifact_summary(artifact)

        self.assertIn("Yuuki_Asuna--- 최근 2D 스냅샷", body)
        self.assertIn("- match: match-123", body)
        self.assertIn("- map/mode: Erangel / squad-fpp", body)
        self.assertIn("- size: 2.0 KB", body)


if __name__ == "__main__":
    unittest.main()
