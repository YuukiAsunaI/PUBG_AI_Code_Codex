from __future__ import annotations

from datetime import datetime, timedelta
import json
import unittest
from unittest.mock import MagicMock

from pubg_ai.data_deletion_backup import (
    BACKUP_EVIDENCE_CONTRACT_VERSION,
    REHEARSAL_CONTRACT_VERSION,
    DataDeletionBackupError,
    DataDeletionBackupEvidence,
    DataDeletionBackupService,
    DataDeletionRehearsalRun,
    build_rehearsal_result,
    fingerprint_backup_evidence,
    fingerprint_evidence_set,
    fingerprint_rehearsal_result,
    normalize_evidence_payload,
)
from pubg_ai.data_deletion_dry_run import (
    DRY_RUN_CONTRACT_VERSION,
    DataDeletionDryRunPlan,
    fingerprint_dry_run_plan,
)
from pubg_ai.data_deletion_requests import DataDeletionRequest


class DataDeletionBackupTests(unittest.TestCase):
    def test_evidence_payload_and_fingerprint_are_canonical(self) -> None:
        plan = _plan()
        first = normalize_evidence_payload(
            "mysql_target_backup",
            {
                "artifact_path": "D:/BackUP/audit/mysql-plan-901.sql.gz",
                "artifact_sha256": "A" * 64,
                "artifact_size_bytes": "100",
                "covered_row_count": "10",
                "backup_created_at_kst": "2026-07-12T12:05:00+09:00",
                "ignored": "not persisted",
            },
        )
        second = normalize_evidence_payload(
            "mysql_target_backup",
            {
                "covered_row_count": 10,
                "artifact_size_bytes": 100,
                "artifact_sha256": "a" * 64,
                "backup_created_at_kst": datetime(2026, 7, 12, 12, 5, 0),
                "artifact_path": "D:/BackUP/audit/mysql-plan-901.sql.gz",
            },
        )

        self.assertEqual(first, second)
        self.assertNotIn("ignored", first)
        self.assertEqual(
            fingerprint_backup_evidence(17, plan, "mysql_target_backup", first),
            fingerprint_backup_evidence(17, plan, "mysql_target_backup", second),
        )

    def test_restore_integrity_evidence_requires_complete_artifact_bindings(self) -> None:
        plan = _plan()
        payload = normalize_evidence_payload(
            "backup_integrity_verification",
            {
                "checksums_verified": True,
                "restore_test_passed": True,
                "restore_tested_at_kst": "2026-07-12T12:07:00+09:00",
                "verified_at_kst": "2026-07-12T12:08:00+09:00",
                "artifact_evidence_set_fingerprint_sha256": "a" * 64,
                "backup_verification_run_id": 1201,
                "backup_verification_result_fingerprint_sha256": "b" * 64,
                "restore_rehearsal_result_fingerprint_sha256": "c" * 64,
                "build_id": "1" * 32,
                "manifest_sha256": "d" * 64,
            },
        )

        self.assertEqual(payload["backup_verification_run_id"], 1201)
        self.assertEqual(payload["build_id"], "1" * 32)
        with self.assertRaisesRegex(DataDeletionBackupError, "bindings must be supplied together"):
            normalize_evidence_payload(
                "backup_integrity_verification",
                {
                    "checksums_verified": True,
                    "restore_test_passed": True,
                    "restore_tested_at_kst": "2026-07-12T12:07:00+09:00",
                    "verified_at_kst": "2026-07-12T12:08:00+09:00",
                    "artifact_evidence_set_fingerprint_sha256": "a" * 64,
                },
            )

    def test_manual_restore_integrity_evidence_is_rejected_before_transaction(self) -> None:
        plan = _plan()
        connection = ScriptedConnection([])
        dry_run_service = MagicMock()
        dry_run_service.list_plans.return_value = [plan]
        service = DataDeletionBackupService(
            connection,
            dry_run_service=dry_run_service,
            preview_service=MagicMock(),
        )

        with self.assertRaisesRegex(DataDeletionBackupError, "only by a passed isolated restore rehearsal"):
            service.record_evidence(
                _request(),
                dry_run_plan_id=plan.id,
                prerequisite_key="backup_integrity_verification",
                evidence={
                    "checksums_verified": True,
                    "restore_test_passed": True,
                    "restore_tested_at_kst": "2026-07-12T12:07:00+09:00",
                    "verified_at_kst": "2026-07-12T12:08:00+09:00",
                },
                actor_id="local-owner",
            )

        self.assertEqual(connection.begin_count, 0)
        self.assertEqual(connection.executed, [])

    def test_restore_integrity_evidence_becomes_stale_after_artifact_set_changes(self) -> None:
        plan = _plan()
        evidence = _evidence_set(plan)
        mysql = evidence["mysql_target_backup"]
        evidence["mysql_target_backup"] = _evidence(
            plan,
            "mysql_target_backup",
            mysql.evidence_json,
            evidence_id=1801,
        )

        result = build_rehearsal_result(
            _request(),
            plan,
            evidence,
            live_fingerprint_sha256=plan.source_fingerprint_sha256,
            path_probe=_path_probe,
        )

        integrity = next(
            item
            for item in result["checks"]
            if item["key"] == "backup_integrity_verification"
        )
        self.assertEqual(integrity["status"], "blocked")
        self.assertIn("different artifact evidence set", integrity["message"])

    def test_rehearsal_result_passes_complete_consistent_evidence_without_mutation(self) -> None:
        plan = _plan()
        evidence = _evidence_set(plan)

        result = build_rehearsal_result(
            _request(),
            plan,
            evidence,
            live_fingerprint_sha256=plan.source_fingerprint_sha256,
            path_probe=_path_probe,
        )

        self.assertEqual(result["rehearsal_status"], "passed")
        self.assertEqual(result["rehearsal_blockers"], [])
        self.assertEqual(len(result["checks"]), 7)
        self.assertTrue(all(check["status"] == "passed" for check in result["checks"]))
        self.assertFalse(result["safety"]["target_mutation_performed"])
        self.assertFalse(result["safety"]["backup_creation_performed"])
        self.assertFalse(result["safety"]["checksum_recalculation_performed"])
        self.assertFalse(result["safety"]["restore_operation_performed"])
        self.assertFalse(result["safety"]["execution_enabled"])
        self.assertIn("executor_not_implemented", result["safety"]["execution_blockers"])
        self.assertNotIn("DELETE FROM", json.dumps(result).upper())

    def test_rehearsal_blocks_missing_and_mismatched_evidence(self) -> None:
        plan = _plan()
        evidence = _evidence_set(plan)
        evidence.pop("replay_artifact_backup")
        mysql = evidence["mysql_target_backup"]
        changed = dict(mysql.evidence_json)
        changed["covered_row_count"] = 9
        evidence["mysql_target_backup"] = _evidence(
            plan,
            "mysql_target_backup",
            changed,
            evidence_id=mysql.id,
        )

        result = build_rehearsal_result(
            _request(),
            plan,
            evidence,
            live_fingerprint_sha256="f" * 64,
            path_probe=_path_probe,
        )

        self.assertEqual(result["rehearsal_status"], "blocked")
        self.assertIn("live_source_fingerprint", result["rehearsal_blockers"])
        self.assertIn("mysql_target_backup", result["rehearsal_blockers"])
        self.assertIn("replay_artifact_backup", result["rehearsal_blockers"])

    def test_rehearsal_blocks_future_dated_evidence(self) -> None:
        plan = _plan()
        evidence = _evidence_set(plan)
        mysql = evidence["mysql_target_backup"]
        future_payload = dict(mysql.evidence_json)
        future_payload["backup_created_at_kst"] = "2026-07-12T12:30:00+09:00"
        evidence["mysql_target_backup"] = _evidence(
            plan,
            "mysql_target_backup",
            future_payload,
            evidence_id=mysql.id,
        )

        result = build_rehearsal_result(
            _request(),
            plan,
            evidence,
            live_fingerprint_sha256=plan.source_fingerprint_sha256,
            path_probe=_path_probe,
        )

        self.assertIn("mysql_target_backup", result["rehearsal_blockers"])
        mysql_check = next(
            check for check in result["checks"] if check["key"] == "mysql_target_backup"
        )
        self.assertIn("later than evidence recording time", mysql_check["message"])

    def test_record_evidence_only_inserts_immutable_audit_row(self) -> None:
        plan = _plan()
        payload = normalize_evidence_payload(
            "mysql_target_backup",
            _mysql_payload(),
        )
        evidence_fingerprint = fingerprint_backup_evidence(
            17,
            plan,
            "mysql_target_backup",
            payload,
        )
        row = _evidence_row(
            plan,
            "mysql_target_backup",
            payload,
            evidence_id=801,
            evidence_fingerprint=evidence_fingerprint,
        )
        connection = ScriptedConnection(
            [
                {"contains": "SELECT status FROM data_deletion_requests", "row": {"status": "approved"}},
                {
                    "contains": "SELECT id, plan_fingerprint_sha256 FROM data_deletion_dry_run_plans",
                    "row": {"id": plan.id, "plan_fingerprint_sha256": plan.plan_fingerprint_sha256},
                },
                {"contains": "INSERT INTO data_deletion_backup_evidence", "lastrowid": 801},
                {"contains": "WHERE id = %s", "row": row},
            ]
        )
        dry_run_service = MagicMock()
        dry_run_service.list_plans.return_value = [plan]
        service = DataDeletionBackupService(
            connection,
            dry_run_service=dry_run_service,
            preview_service=MagicMock(),
        )

        recorded = service.record_evidence(
            _request(),
            dry_run_plan_id=901,
            prerequisite_key="mysql_target_backup",
            evidence=_mysql_payload(),
            actor_id="local-owner",
            note="dump metadata",
            reference_kst=datetime(2026, 7, 12, 12, 6, 0),
        )

        self.assertEqual(recorded.id, 801)
        self.assertEqual(recorded.evidence_fingerprint_sha256, evidence_fingerprint)
        self.assertEqual(connection.begin_count, 1)
        self.assertEqual(connection.commit_count, 1)
        self.assertEqual(connection.rollback_count, 0)
        mutation_queries = [
            query
            for query, _ in connection.executed
            if query.lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE ", "REPLACE "))
        ]
        self.assertEqual(len(mutation_queries), 1)
        self.assertIn("INSERT INTO data_deletion_backup_evidence", mutation_queries[0])

    def test_record_evidence_batch_inserts_artifact_evidence_atomically(self) -> None:
        plan = _plan()
        mysql_payload = normalize_evidence_payload("mysql_target_backup", _mysql_payload())
        replay_payload = normalize_evidence_payload(
            "replay_artifact_backup",
            {
                "artifact_path": "D:/BackUP/audit/replay-plan-901.zip",
                "artifact_sha256": "b" * 64,
                "artifact_size_bytes": 80,
                "covered_file_count": 2,
                "covered_file_bytes": 30,
                "backup_created_at_kst": "2026-07-12T12:05:00+09:00",
            },
        )
        mysql_fingerprint = fingerprint_backup_evidence(
            17, plan, "mysql_target_backup", mysql_payload
        )
        replay_fingerprint = fingerprint_backup_evidence(
            17, plan, "replay_artifact_backup", replay_payload
        )
        connection = ScriptedConnection(
            [
                {"contains": "SELECT status FROM data_deletion_requests", "row": {"status": "approved"}},
                {
                    "contains": "SELECT id, plan_fingerprint_sha256 FROM data_deletion_dry_run_plans",
                    "row": {"id": plan.id, "plan_fingerprint_sha256": plan.plan_fingerprint_sha256},
                },
                {"contains": "INSERT INTO data_deletion_backup_evidence", "lastrowid": 801},
                {"contains": "INSERT INTO data_deletion_backup_evidence", "lastrowid": 802},
                {
                    "contains": "WHERE id = %s",
                    "row": _evidence_row(
                        plan,
                        "mysql_target_backup",
                        mysql_payload,
                        evidence_id=801,
                        evidence_fingerprint=mysql_fingerprint,
                    ),
                },
                {
                    "contains": "WHERE id = %s",
                    "row": _evidence_row(
                        plan,
                        "replay_artifact_backup",
                        replay_payload,
                        evidence_id=802,
                        evidence_fingerprint=replay_fingerprint,
                    ),
                },
            ]
        )
        dry_run_service = MagicMock()
        dry_run_service.list_plans.return_value = [plan]
        service = DataDeletionBackupService(
            connection,
            dry_run_service=dry_run_service,
            preview_service=MagicMock(),
        )

        recorded = service.record_evidence_batch(
            _request(),
            dry_run_plan_id=plan.id,
            evidence_by_key={
                "replay_artifact_backup": replay_payload,
                "mysql_target_backup": mysql_payload,
            },
            actor_id="local-owner",
            note="builder audit",
            reference_kst=datetime(2026, 7, 12, 12, 6, 0),
        )

        self.assertEqual(list(recorded), ["mysql_target_backup", "replay_artifact_backup"])
        self.assertEqual(connection.begin_count, 1)
        self.assertEqual(connection.commit_count, 1)
        self.assertEqual(connection.rollback_count, 0)
        mutation_queries = [
            query
            for query, _ in connection.executed
            if query.lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE ", "REPLACE "))
        ]
        self.assertEqual(len(mutation_queries), 2)
        self.assertTrue(
            all("INSERT INTO data_deletion_backup_evidence" in query for query in mutation_queries)
        )

    def test_record_evidence_rolls_back_when_request_changes(self) -> None:
        plan = _plan()
        connection = ScriptedConnection(
            [
                {"contains": "SELECT status FROM data_deletion_requests", "row": {"status": "cancelled"}},
            ]
        )
        dry_run_service = MagicMock()
        dry_run_service.list_plans.return_value = [plan]
        service = DataDeletionBackupService(
            connection,
            dry_run_service=dry_run_service,
            preview_service=MagicMock(),
        )

        with self.assertRaisesRegex(DataDeletionBackupError, "changed to cancelled"):
            service.record_evidence(
                _request(),
                dry_run_plan_id=901,
                prerequisite_key="mysql_target_backup",
                evidence=_mysql_payload(),
                actor_id="local-owner",
            )

        self.assertEqual(connection.rollback_count, 1)
        self.assertFalse(
            any("INSERT INTO data_deletion_backup_evidence" in query for query, _ in connection.executed)
        )

    def test_run_rehearsal_inserts_only_result_audit_row(self) -> None:
        plan = _plan()
        evidence = _evidence_set(plan)
        result = build_rehearsal_result(
            _request(),
            plan,
            evidence,
            live_fingerprint_sha256=plan.source_fingerprint_sha256,
            path_probe=_path_probe,
        )
        evidence_fingerprint = fingerprint_evidence_set(plan, evidence)
        result_fingerprint = fingerprint_rehearsal_result(result)
        connection = ScriptedConnection(
            [
                {"contains": "SELECT evidence.*", "rows": [_evidence_to_row(item) for item in evidence.values()]},
                {"contains": "SELECT status FROM data_deletion_requests", "row": {"status": "approved"}},
                {
                    "contains": "SELECT id, plan_fingerprint_sha256 FROM data_deletion_dry_run_plans",
                    "row": {"id": plan.id, "plan_fingerprint_sha256": plan.plan_fingerprint_sha256},
                },
                {
                    "contains": "SELECT id, prerequisite_key FROM data_deletion_backup_evidence",
                    "rows": [
                        {"id": item.id, "prerequisite_key": item.prerequisite_key}
                        for item in evidence.values()
                    ],
                },
                {"contains": "INSERT INTO data_deletion_rehearsal_runs", "lastrowid": 1001},
                {
                    "contains": "WHERE id = %s",
                    "row": _rehearsal_row(
                        plan,
                        result,
                        evidence_fingerprint,
                        result_fingerprint,
                    ),
                },
            ]
        )
        dry_run_service = MagicMock()
        dry_run_service.list_plans.return_value = [plan]
        preview = MagicMock()
        preview.to_record.return_value = {"request_id": 17}
        preview_service = MagicMock()
        preview_service.build_preview.return_value = preview
        service = DataDeletionBackupService(
            connection,
            dry_run_service=dry_run_service,
            preview_service=preview_service,
            path_probe=_path_probe,
        )
        from pubg_ai.data_deletion_backup import fingerprint_preview_record as live_fingerprint

        preview_fingerprint, _ = live_fingerprint({"request_id": 17})
        plan_with_live_fingerprint = _plan(source_fingerprint=preview_fingerprint)
        dry_run_service.list_plans.return_value = [plan_with_live_fingerprint]
        evidence = _evidence_set(plan_with_live_fingerprint)
        connection.steps[0]["rows"] = [_evidence_to_row(item) for item in evidence.values()]
        for step in connection.steps:
            row = step.get("row")
            if isinstance(row, dict) and "plan_fingerprint_sha256" in row:
                row["plan_fingerprint_sha256"] = plan_with_live_fingerprint.plan_fingerprint_sha256
        result = build_rehearsal_result(
            _request(),
            plan_with_live_fingerprint,
            evidence,
            live_fingerprint_sha256=preview_fingerprint,
            path_probe=_path_probe,
        )
        evidence_fingerprint = fingerprint_evidence_set(plan_with_live_fingerprint, evidence)
        result_fingerprint = fingerprint_rehearsal_result(result)
        connection.steps[-1]["row"] = _rehearsal_row(
            plan_with_live_fingerprint,
            result,
            evidence_fingerprint,
            result_fingerprint,
        )

        rehearsal = service.run_rehearsal(
            _request(),
            dry_run_plan_id=901,
            actor_id="local-owner",
            note="metadata-only rehearsal",
            reference_kst=datetime(2026, 7, 12, 12, 10, 0),
        )

        self.assertEqual(rehearsal.result_status, "passed")
        self.assertEqual(connection.commit_count, 1)
        preview_service.build_preview.assert_called_once_with(
            unittest.mock.ANY,
            file_limit=500,
        )
        mutation_queries = [
            query
            for query, _ in connection.executed
            if query.lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE ", "REPLACE "))
        ]
        self.assertEqual(len(mutation_queries), 1)
        self.assertIn("INSERT INTO data_deletion_rehearsal_runs", mutation_queries[0])

    def test_readiness_state_marks_passed_rehearsal_stale_after_new_evidence(self) -> None:
        plan = _plan()
        evidence = _evidence_set(plan)
        old_set_fingerprint = "c" * 64
        rehearsal = DataDeletionRehearsalRun(
            id=1001,
            request_id=17,
            dry_run_plan_id=901,
            contract_version=REHEARSAL_CONTRACT_VERSION,
            plan_fingerprint_sha256=plan.plan_fingerprint_sha256,
            evidence_set_fingerprint_sha256=old_set_fingerprint,
            result_fingerprint_sha256=fingerprint_rehearsal_result({"checks": []}),
            result_status="passed",
            result_json={"checks": []},
            check_count=7,
            passed_check_count=7,
            blocker_count=0,
            run_by="local-owner",
            rehearsal_note=None,
            run_at_kst=datetime(2026, 7, 12, 12, 10, 0),
        )
        dry_run_service = MagicMock()
        dry_run_service.list_plans.return_value = [plan]
        service = DataDeletionBackupService(
            ScriptedConnection([]),
            dry_run_service=dry_run_service,
            preview_service=MagicMock(),
        )
        service.latest_evidence = MagicMock(return_value=evidence)
        service.list_evidence = MagicMock(return_value=list(evidence.values()))
        service.list_rehearsals = MagicMock(return_value=[rehearsal])

        state = service.readiness_state(_request())

        self.assertIn("executor_not_implemented", state["execution_blockers"])
        self.assertIn("rehearsal_stale", state["execution_blockers"])
        self.assertNotIn("backup_evidence_not_recorded", state["execution_blockers"])
        self.assertFalse(state["execution_enabled"])
        self.assertFalse(state["execution_ready"])


class ScriptedConnection:
    def __init__(self, steps: list[dict[str, object]]) -> None:
        self.steps = list(steps)
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.begin_count = 0
        self.commit_count = 0
        self.rollback_count = 0

    def cursor(self) -> "ScriptedCursor":
        return ScriptedCursor(self)

    def begin(self) -> None:
        self.begin_count += 1

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1


class ScriptedCursor:
    def __init__(self, connection: ScriptedConnection) -> None:
        self.connection = connection
        self.step: dict[str, object] = {}
        self.lastrowid = 0

    def __enter__(self) -> "ScriptedCursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, query: str, params: tuple[object, ...] = ()) -> None:
        if not self.connection.steps:
            raise AssertionError(f"unexpected query: {query}")
        self.step = self.connection.steps.pop(0)
        expected = str(self.step.get("contains") or "")
        normalized = " ".join(query.split())
        if expected not in normalized:
            raise AssertionError(f"expected {expected!r} in {normalized!r}")
        self.lastrowid = int(self.step.get("lastrowid") or 0)
        self.connection.executed.append((normalized, tuple(params)))

    def fetchone(self) -> dict[str, object] | None:
        row = self.step.get("row")
        return dict(row) if isinstance(row, dict) else None

    def fetchall(self) -> list[dict[str, object]]:
        rows = self.step.get("rows") or []
        return [dict(row) for row in rows if isinstance(row, dict)]


def _request(*, status: str = "approved") -> DataDeletionRequest:
    requested_at = datetime(2026, 7, 12, 10, 0, 0)
    return DataDeletionRequest(
        id=17,
        registered_player_id=1,
        account_id="account.test",
        shard="steam",
        player_name="Yuuki_Asuna---",
        deletion_scope="all",
        status=status,
        reason="backup test",
        requested_by_discord_user_id="100",
        requested_guild_id="10",
        requested_channel_id="20",
        requested_at_kst=requested_at,
        expires_at_kst=requested_at + timedelta(hours=24),
        reviewed_by="local:local-owner" if status == "approved" else None,
        reviewed_at_kst=requested_at if status == "approved" else None,
        review_note="approved" if status == "approved" else None,
        updated_at_kst=requested_at,
    )


def _plan(*, source_fingerprint: str = "e" * 64) -> DataDeletionDryRunPlan:
    plan_json = {
        "contract_version": DRY_RUN_CONTRACT_VERSION,
        "request_id": 17,
        "source_fingerprint_sha256": source_fingerprint,
        "metrics": {
            "candidate_row_count": 10,
            "candidate_file_count": 2,
            "candidate_file_bytes": 30,
        },
        "backup_prerequisites": [
            {"key": "mysql_target_backup", "required": True, "description": "mysql"},
            {"key": "replay_artifact_backup", "required": True, "description": "replay"},
            {"key": "quarantine_capacity_check", "required": True, "description": "capacity"},
            {"key": "backup_integrity_verification", "required": True, "description": "integrity"},
        ],
        "database_operations": [],
        "file_operations": [],
    }
    fingerprint = fingerprint_dry_run_plan(plan_json)
    return DataDeletionDryRunPlan(
        id=901,
        request_id=17,
        preview_snapshot_id=501,
        confirmation_id=701,
        contract_version=DRY_RUN_CONTRACT_VERSION,
        source_fingerprint_sha256=source_fingerprint,
        plan_fingerprint_sha256=fingerprint,
        plan_json=plan_json,
        operation_count=0,
        candidate_row_count=10,
        candidate_file_count=2,
        candidate_file_bytes=30,
        excluded_row_count=20,
        excluded_file_count=4,
        generated_by="local-owner",
        generation_note=None,
        generated_at_kst=datetime(2026, 7, 12, 12, 0, 0),
    )


def _mysql_payload() -> dict[str, object]:
    return {
        "artifact_path": "D:/BackUP/audit/mysql-plan-901.sql.gz",
        "artifact_sha256": "a" * 64,
        "artifact_size_bytes": 100,
        "covered_row_count": 10,
        "backup_created_at_kst": "2026-07-12T12:05:00+09:00",
    }


def _evidence_set(plan: DataDeletionDryRunPlan) -> dict[str, DataDeletionBackupEvidence]:
    payloads = {
        "mysql_target_backup": _mysql_payload(),
        "replay_artifact_backup": {
            "artifact_path": "D:/BackUP/audit/replay-plan-901.zip",
            "artifact_sha256": "b" * 64,
            "artifact_size_bytes": 80,
            "covered_file_count": 2,
            "covered_file_bytes": 30,
            "backup_created_at_kst": "2026-07-12T12:05:00+09:00",
        },
        "quarantine_capacity_check": {
            "checked_path": "D:/BackUP/replay",
            "available_bytes": 1000,
            "verified_at_kst": "2026-07-12T12:06:00+09:00",
        },
    }
    records = {
        key: _evidence(
            plan,
            key,
            normalize_evidence_payload(key, payload),
            evidence_id=801 + index,
        )
        for index, (key, payload) in enumerate(payloads.items())
    }
    artifact_fingerprint = fingerprint_evidence_set(
        plan,
        {
            key: records[key]
            for key in ("mysql_target_backup", "replay_artifact_backup")
        },
    )
    integrity_payload = normalize_evidence_payload(
        "backup_integrity_verification",
        {
            "checksums_verified": True,
            "restore_test_passed": True,
            "restore_tested_at_kst": "2026-07-12T12:07:00+09:00",
            "verified_at_kst": "2026-07-12T12:08:00+09:00",
            "artifact_evidence_set_fingerprint_sha256": artifact_fingerprint,
            "backup_verification_run_id": 1201,
            "backup_verification_result_fingerprint_sha256": "c" * 64,
            "restore_rehearsal_result_fingerprint_sha256": "d" * 64,
            "build_id": "1" * 32,
            "manifest_sha256": "e" * 64,
        },
    )
    records["backup_integrity_verification"] = _evidence(
        plan,
        "backup_integrity_verification",
        integrity_payload,
        evidence_id=804,
    )
    return records


def _evidence(
    plan: DataDeletionDryRunPlan,
    key: str,
    payload: dict[str, object],
    *,
    evidence_id: int,
) -> DataDeletionBackupEvidence:
    fingerprint = fingerprint_backup_evidence(17, plan, key, payload)
    return DataDeletionBackupEvidence(
        id=evidence_id,
        request_id=17,
        dry_run_plan_id=plan.id,
        contract_version=BACKUP_EVIDENCE_CONTRACT_VERSION,
        plan_fingerprint_sha256=plan.plan_fingerprint_sha256,
        prerequisite_key=key,
        evidence_fingerprint_sha256=fingerprint,
        evidence_json=dict(payload),
        recorded_by="local-owner",
        evidence_note=None,
        recorded_at_kst=datetime(2026, 7, 12, 12, 9, 0),
    )


def _path_probe(path: str) -> dict[str, object]:
    if path.endswith("mysql-plan-901.sql.gz"):
        return {"path": path, "absolute": True, "exists": True, "is_file": True, "is_dir": False, "size_bytes": 100, "free_bytes": None, "error": None}
    if path.endswith("replay-plan-901.zip"):
        return {"path": path, "absolute": True, "exists": True, "is_file": True, "is_dir": False, "size_bytes": 80, "free_bytes": None, "error": None}
    return {"path": path, "absolute": True, "exists": True, "is_file": False, "is_dir": True, "size_bytes": None, "free_bytes": 1000, "error": None}


def _evidence_row(
    plan: DataDeletionDryRunPlan,
    key: str,
    payload: dict[str, object],
    *,
    evidence_id: int,
    evidence_fingerprint: str,
) -> dict[str, object]:
    return {
        "id": evidence_id,
        "request_id": 17,
        "dry_run_plan_id": plan.id,
        "contract_version": BACKUP_EVIDENCE_CONTRACT_VERSION,
        "plan_fingerprint_sha256": plan.plan_fingerprint_sha256,
        "prerequisite_key": key,
        "evidence_fingerprint_sha256": evidence_fingerprint,
        "evidence_json": json.dumps(payload),
        "recorded_by": "local-owner",
        "evidence_note": "dump metadata",
        "recorded_at_kst": datetime(2026, 7, 12, 12, 6, 0),
    }


def _evidence_to_row(item: DataDeletionBackupEvidence) -> dict[str, object]:
    return {
        "id": item.id,
        "request_id": item.request_id,
        "dry_run_plan_id": item.dry_run_plan_id,
        "contract_version": item.contract_version,
        "plan_fingerprint_sha256": item.plan_fingerprint_sha256,
        "prerequisite_key": item.prerequisite_key,
        "evidence_fingerprint_sha256": item.evidence_fingerprint_sha256,
        "evidence_json": json.dumps(item.evidence_json),
        "recorded_by": item.recorded_by,
        "evidence_note": item.evidence_note,
        "recorded_at_kst": item.recorded_at_kst,
    }


def _rehearsal_row(
    plan: DataDeletionDryRunPlan,
    result: dict[str, object],
    evidence_fingerprint: str,
    result_fingerprint: str,
) -> dict[str, object]:
    checks = result["checks"]
    assert isinstance(checks, list)
    return {
        "id": 1001,
        "request_id": 17,
        "dry_run_plan_id": plan.id,
        "contract_version": REHEARSAL_CONTRACT_VERSION,
        "plan_fingerprint_sha256": plan.plan_fingerprint_sha256,
        "evidence_set_fingerprint_sha256": evidence_fingerprint,
        "result_fingerprint_sha256": result_fingerprint,
        "result_status": "passed",
        "result_json": json.dumps(result),
        "check_count": len(checks),
        "passed_check_count": len(checks),
        "blocker_count": 0,
        "run_by": "local-owner",
        "rehearsal_note": "metadata-only rehearsal",
        "run_at_kst": datetime(2026, 7, 12, 12, 10, 0),
    }


if __name__ == "__main__":
    unittest.main()
