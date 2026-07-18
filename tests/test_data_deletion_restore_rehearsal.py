from __future__ import annotations

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch
import hashlib
import json
import re
import unittest
import zipfile

from pubg_ai.data_deletion_backup import BACKUP_EVIDENCE_CONTRACT_VERSION
from pubg_ai.data_deletion_backup_builder import (
    BACKUP_BUILDER_CONTRACT_VERSION,
    MYSQL_BACKUP_FORMAT_VERSION,
    REPLAY_BACKUP_FORMAT_VERSION,
)
from pubg_ai.data_deletion_backup_verifier import (
    BACKUP_VERIFIER_CONTRACT_VERSION,
    DataDeletionBackupVerificationRun,
    RevalidatedBackupBuild,
)
from pubg_ai.data_deletion_dry_run import (
    DRY_RUN_CONTRACT_VERSION,
    DataDeletionDryRunPlan,
    fingerprint_dry_run_plan,
)
from pubg_ai.data_deletion_requests import DataDeletionRequest
from pubg_ai.data_deletion_restore_rehearsal import (
    RESTORE_REHEARSAL_CONTRACT_VERSION,
    DataDeletionBackupRestoreRehearsalService,
    DataDeletionRestoreRehearsalError,
    _IsolatedRestoreRunner,
    expected_restore_rehearsal_confirmation,
)


class IsolatedRestoreRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.backup_root = self.root / "backups"
        self.backup_root.mkdir()
        self.plan = _plan()
        self.verification = _verification(self.plan)
        self.mysql_path = self.backup_root / "mysql-target-backup.zip"
        self.replay_path = self.backup_root / "replay-artifact-backup.zip"
        _write_mysql_archive(self.mysql_path, self.plan)
        _write_replay_archive(self.replay_path, self.plan)
        self.revalidated = _revalidated(
            self.plan,
            self.verification,
            self.mysql_path,
            self.replay_path,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_round_trips_mysql_and_replay_and_removes_all_scratch_resources(self) -> None:
        audit = FakeMySqlConnection(connection_id=101)
        scratch = FakeMySqlConnection(connection_id=202)
        before = {
            self.mysql_path: _sha256(self.mysql_path),
            self.replay_path: _sha256(self.replay_path),
        }

        result = _IsolatedRestoreRunner(
            audit_connection=audit,
            scratch_connection_factory=lambda: scratch,
            backup_root=self.backup_root,
            expected_database_name="pubg_ai",
            revalidated=self.revalidated,
        ).run()

        blocked = [item for item in result["checks"] if item["status"] == "blocked"]
        self.assertEqual(blocked, [])
        self.assertEqual(result["metrics"]["mysql_table_count"], 1)
        self.assertEqual(result["metrics"]["mysql_row_count"], 1)
        self.assertEqual(result["metrics"]["replay_file_count"], 1)
        self.assertEqual(result["metrics"]["replay_source_bytes"], 17)
        self.assertTrue(scratch.closed)
        self.assertEqual(scratch.temporary_tables, {})
        self.assertEqual(
            list(self.backup_root.glob(".pubg-ai-restore-rehearsal-*")),
            [],
        )
        self.assertEqual(before[self.mysql_path], _sha256(self.mysql_path))
        self.assertEqual(before[self.replay_path], _sha256(self.replay_path))
        mutation_sql = [
            statement
            for statement, _ in scratch.executed
            if statement.startswith(("CREATE ", "INSERT ", "DROP "))
        ]
        self.assertTrue(any(statement.startswith("CREATE TEMPORARY TABLE") for statement in mutation_sql))
        self.assertTrue(any(statement.startswith("INSERT INTO `_pubg_ai_rr_") for statement in mutation_sql))
        self.assertTrue(any(statement.startswith("DROP TEMPORARY TABLE") for statement in mutation_sql))
        self.assertFalse(any("INSERT INTO `registered_players`" in statement for statement in mutation_sql))

    def test_blocks_when_scratch_connection_is_not_dedicated(self) -> None:
        scratch = FakeMySqlConnection(connection_id=101)

        result = _IsolatedRestoreRunner(
            audit_connection=FakeMySqlConnection(connection_id=101),
            scratch_connection_factory=lambda: scratch,
            backup_root=self.backup_root,
            expected_database_name="pubg_ai",
            revalidated=self.revalidated,
        ).run()

        self.assertEqual(
            next(item for item in result["checks"] if item["key"] == "scratch_mysql_connection_isolation")["status"],
            "blocked",
        )
        self.assertFalse(any(statement.startswith("CREATE TEMPORARY") for statement, _ in scratch.executed))
        self.assertTrue(scratch.closed)

    def test_blocks_mysql_readback_difference_and_still_cleans_up(self) -> None:
        scratch = FakeMySqlConnection(connection_id=202, mutate_readback=True)

        result = _IsolatedRestoreRunner(
            audit_connection=FakeMySqlConnection(connection_id=101),
            scratch_connection_factory=lambda: scratch,
            backup_root=self.backup_root,
            expected_database_name="pubg_ai",
            revalidated=self.revalidated,
        ).run()

        messages = " ".join(
            str(item["message"])
            for item in result["checks"]
            if item["status"] == "blocked"
        )
        self.assertIn("readback differs", messages)
        self.assertEqual(result["metrics"]["mysql_restored_row_count"], 0)
        self.assertTrue(scratch.closed)
        self.assertEqual(scratch.temporary_tables, {})
        self.assertEqual(
            next(item for item in result["checks"] if item["key"] == "scratch_cleanup")["status"],
            "passed",
        )

    def test_blocks_unsafe_replay_restore_path_before_writing_outside_scratch(self) -> None:
        _write_replay_archive(
            self.replay_path,
            self.plan,
            source_relative_path="../escape.bin",
        )
        revalidated = _revalidated(
            self.plan,
            self.verification,
            self.mysql_path,
            self.replay_path,
        )

        result = _IsolatedRestoreRunner(
            audit_connection=FakeMySqlConnection(connection_id=101),
            scratch_connection_factory=lambda: FakeMySqlConnection(connection_id=202),
            backup_root=self.backup_root,
            expected_database_name="pubg_ai",
            revalidated=revalidated,
        ).run()

        self.assertTrue(any(item["status"] == "blocked" for item in result["checks"]))
        self.assertFalse((self.root / "escape.bin").exists())
        self.assertEqual(
            list(self.backup_root.glob(".pubg-ai-restore-rehearsal-*")),
            [],
        )


class RestoreRehearsalServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.backup_root = Path(self.temporary.name)
        self.request = _request()
        self.plan = _plan()
        self.verification = _verification(self.plan)
        self.revalidated = RevalidatedBackupBuild(
            verification_run=self.verification,
            plan=self.plan,
            manifest_path=self.backup_root / "build-manifest.json",
            manifest={"artifacts": []},
            artifact_paths={},
            current_result_fingerprint_sha256="d" * 64,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_passed_restore_atomically_records_bound_integrity_evidence_and_audit(self) -> None:
        connection = ServiceAuditConnection(self.plan, self.verification)
        service = self._service(connection)
        runner_result = {
            "checks": [
                {
                    "key": "scratch_cleanup",
                    "status": "passed",
                    "expected": "clean",
                    "observed": {},
                    "message": "clean",
                }
            ],
            "metrics": {
                "mysql_table_count": 1,
                "mysql_restored_table_count": 1,
                "mysql_row_count": 2,
                "mysql_restored_row_count": 2,
                "replay_file_count": 1,
                "replay_restored_file_count": 1,
                "replay_source_bytes": 16,
                "replay_restored_bytes": 16,
            },
        }

        with patch(
            "pubg_ai.data_deletion_restore_rehearsal._IsolatedRestoreRunner"
        ) as runner_class:
            runner_class.return_value.run.return_value = runner_result
            run = service.run(
                self.request,
                backup_verification_run_id=self.verification.id,
                confirmation_text=expected_restore_rehearsal_confirmation(
                    self.request.id,
                    self.verification.id,
                    self.verification.result_fingerprint_sha256,
                ),
                actor_id="local-owner",
                note="isolated restore",
                reference_kst=datetime(2026, 7, 12, 12, 20, 0),
            )

        self.assertEqual(run.contract_version, RESTORE_REHEARSAL_CONTRACT_VERSION)
        self.assertEqual(run.result_status, "passed")
        self.assertEqual(run.backup_integrity_evidence_id, 1801)
        self.assertEqual(run.mysql_restored_row_count, 2)
        self.assertTrue(run.result_json["safety"]["backup_integrity_prerequisite_attested"])
        mutations = [
            (statement, parameters)
            for statement, parameters in connection.executed
            if statement.startswith(("INSERT ", "UPDATE ", "DELETE ", "REPLACE "))
        ]
        self.assertEqual(len(mutations), 2)
        self.assertIn("data_deletion_backup_evidence", mutations[0][0])
        self.assertIn("data_deletion_backup_restore_rehearsal_runs", mutations[1][0])
        evidence_payload = json.loads(str(mutations[0][1][6]))
        self.assertEqual(
            evidence_payload["artifact_evidence_set_fingerprint_sha256"],
            self.verification.evidence_set_fingerprint_sha256,
        )
        self.assertEqual(
            evidence_payload["backup_verification_run_id"],
            self.verification.id,
        )
        self.assertEqual(
            evidence_payload["build_id"],
            self.verification.build_id,
        )
        self.assertEqual(connection.begin_count, 1)
        self.assertEqual(connection.commit_count, 1)
        self.assertEqual(connection.rollback_count, 0)

    def test_blocked_restore_records_audit_without_integrity_evidence(self) -> None:
        connection = ServiceAuditConnection(self.plan, self.verification)
        service = self._service(connection)
        runner_result = {
            "checks": [
                {
                    "key": "mysql_restore_round_trip",
                    "status": "blocked",
                    "expected": "same",
                    "observed": None,
                    "message": "different",
                },
                {
                    "key": "scratch_cleanup",
                    "status": "passed",
                    "expected": "clean",
                    "observed": {},
                    "message": "clean",
                },
            ],
            "metrics": {
                "mysql_table_count": 1,
                "mysql_restored_table_count": 0,
                "mysql_row_count": 2,
                "mysql_restored_row_count": 0,
                "replay_file_count": 0,
                "replay_restored_file_count": 0,
                "replay_source_bytes": 0,
                "replay_restored_bytes": 0,
            },
        }

        with patch(
            "pubg_ai.data_deletion_restore_rehearsal._IsolatedRestoreRunner"
        ) as runner_class:
            runner_class.return_value.run.return_value = runner_result
            run = service.run(
                self.request,
                backup_verification_run_id=self.verification.id,
                confirmation_text=expected_restore_rehearsal_confirmation(
                    self.request.id,
                    self.verification.id,
                    self.verification.result_fingerprint_sha256,
                ),
                actor_id="local-owner",
                reference_kst=datetime(2026, 7, 12, 12, 20, 0),
            )

        self.assertEqual(run.result_status, "blocked")
        self.assertIsNone(run.backup_integrity_evidence_id)
        mutations = [
            statement
            for statement, _ in connection.executed
            if statement.startswith(("INSERT ", "UPDATE ", "DELETE ", "REPLACE "))
        ]
        self.assertEqual(len(mutations), 1)
        self.assertIn("data_deletion_backup_restore_rehearsal_runs", mutations[0])

    def test_rejects_wrong_confirmation_before_any_restore_or_audit_write(self) -> None:
        connection = ServiceAuditConnection(self.plan, self.verification)
        service = self._service(connection)

        with self.assertRaises(DataDeletionRestoreRehearsalError):
            service.run(
                self.request,
                backup_verification_run_id=self.verification.id,
                confirmation_text="wrong",
                actor_id="local-owner",
            )

        self.assertFalse(any(statement.startswith("INSERT ") for statement, _ in connection.executed))
        service.verifier_service.revalidate_passed_run.assert_not_called()

    def _service(
        self,
        connection: "ServiceAuditConnection",
    ) -> DataDeletionBackupRestoreRehearsalService:
        backup_service = MagicMock()
        backup_service.require_latest_plan.return_value = self.plan
        backup_service.dry_run_service.list_plans.return_value = [self.plan]
        verifier_service = MagicMock()
        verifier_service.get_run.return_value = self.verification
        verifier_service.revalidate_passed_run.return_value = self.revalidated
        verifier_service.list_runs.return_value = [self.verification]
        return DataDeletionBackupRestoreRehearsalService(
            connection,
            backup_service=backup_service,
            verifier_service=verifier_service,
            scratch_connection_factory=lambda: FakeMySqlConnection(connection_id=202),
            backup_root=self.backup_root,
            expected_database_name="pubg_ai",
        )


class FakeMySqlConnection:
    def __init__(
        self,
        *,
        connection_id: int,
        database_name: str = "pubg_ai",
        mutate_readback: bool = False,
    ) -> None:
        self.connection_id = connection_id
        self.database_name = database_name
        self.mutate_readback = mutate_readback
        self.closed = False
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.temporary_tables: dict[str, dict[str, object]] = {}
        self.schemas = {
            "registered_players": [
                ("id", "bigint unsigned", "auto_increment"),
                ("account_id", "varchar(128)", ""),
                ("shard", "varchar(32)", ""),
                ("current_name", "varchar(191)", ""),
                ("active", "tinyint(1)", ""),
                ("created_at_kst", "datetime(6)", ""),
            ]
        }

    def cursor(self) -> "FakeMySqlCursor":
        return FakeMySqlCursor(self)

    def close(self) -> None:
        self.closed = True
        self.temporary_tables.clear()


class FakeMySqlCursor:
    def __init__(self, connection: FakeMySqlConnection) -> None:
        self.connection = connection
        self._row = None
        self._rows: list[dict[str, object]] = []
        self._fetch_index = 0

    def __enter__(self) -> "FakeMySqlCursor":
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def execute(
        self,
        statement: str,
        parameters: tuple[object, ...] | None = None,
    ) -> None:
        normalized = " ".join(statement.split())
        values = tuple(parameters or ())
        self.connection.executed.append((normalized, values))
        self._row = None
        self._rows = []
        self._fetch_index = 0
        if normalized.startswith("SELECT DATABASE() AS database_name"):
            self._row = {
                "database_name": self.connection.database_name,
                "connection_id": self.connection.connection_id,
            }
            return
        show_columns = re.fullmatch(r"SHOW FULL COLUMNS FROM `([^`]+)`", normalized)
        if show_columns:
            table = show_columns.group(1)
            self._rows = [
                {"Field": name, "Type": column_type, "Extra": extra}
                for name, column_type, extra in self.connection.schemas[table]
            ]
            return
        show_create = re.fullmatch(r"SHOW CREATE TABLE `([^`]+)`", normalized)
        if show_create:
            table = show_create.group(1)
            source = self.connection.temporary_tables.get(table, {}).get("source", table)
            self._row = {
                "Table": table,
                "Create Table": f"CREATE TABLE `{table}` LIKE `{source}`",
            }
            return
        create = re.fullmatch(
            r"CREATE TEMPORARY TABLE `([^`]+)` LIKE `([^`]+)`",
            normalized,
        )
        if create:
            name, source = create.groups()
            self.connection.temporary_tables[name] = {
                "source": source,
                "rows": [],
            }
            return
        insert = re.fullmatch(
            r"INSERT INTO `([^`]+)` \((.+)\) VALUES \((.+)\)",
            normalized,
        )
        if insert:
            table = insert.group(1)
            columns = [item.strip().strip("`") for item in insert.group(2).split(",")]
            row = dict(zip(columns, values, strict=True))
            self.connection.temporary_tables[table]["rows"].append(row)
            return
        select = re.fullmatch(r"SELECT (.+) FROM `([^`]+)`", normalized)
        if select:
            columns = [item.strip().strip("`") for item in select.group(1).split(",")]
            table = select.group(2)
            rows = [
                {column: row[column] for column in columns}
                for row in self.connection.temporary_tables[table]["rows"]
            ]
            if self.connection.mutate_readback and rows:
                rows[0]["current_name"] = "changed"
            self._rows = rows
            return
        drop = re.fullmatch(
            r"DROP TEMPORARY TABLE IF EXISTS `([^`]+)`",
            normalized,
        )
        if drop:
            self.connection.temporary_tables.pop(drop.group(1), None)
            return
        raise AssertionError(f"unexpected SQL: {normalized}")

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)

    def fetchmany(self, size: int):
        rows = self._rows[self._fetch_index : self._fetch_index + size]
        self._fetch_index += len(rows)
        return rows


class ServiceAuditConnection:
    def __init__(
        self,
        plan: DataDeletionDryRunPlan,
        verification: DataDeletionBackupVerificationRun,
    ) -> None:
        self.plan = plan
        self.verification = verification
        self.begin_count = 0
        self.commit_count = 0
        self.rollback_count = 0
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    def cursor(self) -> "ServiceAuditCursor":
        return ServiceAuditCursor(self)

    def begin(self) -> None:
        self.begin_count += 1

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1


class ServiceAuditCursor:
    def __init__(self, connection: ServiceAuditConnection) -> None:
        self.connection = connection
        self.lastrowid = 0
        self._row = None
        self._rows: list[dict[str, object]] = []

    def __enter__(self) -> "ServiceAuditCursor":
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def execute(
        self,
        statement: str,
        parameters: tuple[object, ...] | None = None,
    ) -> None:
        normalized = " ".join(statement.split())
        values = tuple(parameters or ())
        self.connection.executed.append((normalized, values))
        self._row = None
        self._rows = []
        verification = self.connection.verification
        if normalized.startswith("SELECT status FROM data_deletion_requests"):
            self._row = {"status": "approved"}
        elif "FROM data_deletion_dry_run_plans" in normalized:
            self._row = {
                "id": self.connection.plan.id,
                "plan_fingerprint_sha256": self.connection.plan.plan_fingerprint_sha256,
            }
        elif "FROM data_deletion_backup_verification_runs" in normalized:
            self._row = {
                "id": verification.id,
                "request_id": verification.request_id,
                "dry_run_plan_id": verification.dry_run_plan_id,
                "contract_version": verification.contract_version,
                "plan_fingerprint_sha256": verification.plan_fingerprint_sha256,
                "evidence_set_fingerprint_sha256": verification.evidence_set_fingerprint_sha256,
                "evidence_record_ids_json": json.dumps(verification.evidence_record_ids),
                "build_id": verification.build_id,
                "expected_manifest_sha256": verification.expected_manifest_sha256,
                "result_fingerprint_sha256": verification.result_fingerprint_sha256,
                "result_status": verification.result_status,
            }
        elif "FROM data_deletion_backup_evidence" in normalized:
            fingerprints = _artifact_fingerprints()
            self._rows = [
                {
                    "id": record_id,
                    "prerequisite_key": key,
                    "evidence_fingerprint_sha256": fingerprints[key],
                }
                for key, record_id in verification.evidence_record_ids.items()
            ]
        elif "INSERT INTO data_deletion_backup_evidence" in normalized:
            self.lastrowid = 1801
        elif "INSERT INTO data_deletion_backup_restore_rehearsal_runs" in normalized:
            self.lastrowid = 1901
        else:
            raise AssertionError(f"unexpected SQL: {normalized}")

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)


def _request() -> DataDeletionRequest:
    requested_at = datetime(2026, 7, 12, 10, 0, 0)
    return DataDeletionRequest(
        id=17,
        registered_player_id=1,
        account_id="account.test",
        shard="steam",
        player_name="Yuuki_Asuna---",
        deletion_scope="all",
        status="approved",
        reason="restore rehearsal test",
        requested_by_discord_user_id="100",
        requested_guild_id="10",
        requested_channel_id="20",
        requested_at_kst=requested_at,
        expires_at_kst=requested_at,
        reviewed_by="local:owner",
        reviewed_at_kst=requested_at,
        review_note="approved",
        updated_at_kst=requested_at,
    )


def _plan() -> DataDeletionDryRunPlan:
    plan_json = {
        "contract_version": DRY_RUN_CONTRACT_VERSION,
        "request_id": 17,
        "source_fingerprint_sha256": "a" * 64,
        "metrics": {
            "candidate_row_count": 1,
            "candidate_file_count": 1,
            "candidate_file_bytes": 17,
        },
        "backup_prerequisites": [
            {"key": "mysql_target_backup", "required": True},
            {"key": "replay_artifact_backup", "required": True},
            {"key": "quarantine_capacity_check", "required": True},
            {"key": "backup_integrity_verification", "required": True},
        ],
        "database_operations": [
            {
                "sequence": 1,
                "table": "registered_players",
                "action": "delete_rows_planned",
                "mutation_enabled": False,
                "estimated_rows": 1,
                "selector": {
                    "kind": "target_identity",
                    "account_id": "account.test",
                    "shard": "steam",
                },
            }
        ],
        "file_operations": [
            {
                "sequence": 1,
                "record_id": 31,
                "artifact_type": "timeline_json",
                "match_id": "match-1",
                "relative_path": "timeline/match-1.json",
            }
        ],
    }
    fingerprint = fingerprint_dry_run_plan(plan_json)
    return DataDeletionDryRunPlan(
        id=901,
        request_id=17,
        preview_snapshot_id=501,
        confirmation_id=701,
        contract_version=DRY_RUN_CONTRACT_VERSION,
        source_fingerprint_sha256="a" * 64,
        plan_fingerprint_sha256=fingerprint,
        plan_json=plan_json,
        operation_count=2,
        candidate_row_count=1,
        candidate_file_count=1,
        candidate_file_bytes=17,
        excluded_row_count=0,
        excluded_file_count=0,
        generated_by="local-owner",
        generation_note=None,
        generated_at_kst=datetime(2026, 7, 12, 12, 0, 0),
    )


def _verification(plan: DataDeletionDryRunPlan) -> DataDeletionBackupVerificationRun:
    evidence_ids = {
        "mysql_target_backup": 1101,
        "replay_artifact_backup": 1102,
    }
    evidence_set_fingerprint = _evidence_set_fingerprint(plan, evidence_ids)
    return DataDeletionBackupVerificationRun(
        id=1201,
        request_id=17,
        dry_run_plan_id=plan.id,
        contract_version=BACKUP_VERIFIER_CONTRACT_VERSION,
        plan_fingerprint_sha256=plan.plan_fingerprint_sha256,
        evidence_set_fingerprint_sha256=evidence_set_fingerprint,
        evidence_record_ids=evidence_ids,
        build_id="1" * 32,
        manifest_path="C:/backup/build-manifest.json",
        expected_manifest_sha256="b" * 64,
        observed_manifest_sha256="b" * 64,
        manifest_fingerprint_sha256="c" * 64,
        result_fingerprint_sha256="d" * 64,
        result_status="passed",
        result_json={},
        artifact_count=2,
        verified_artifact_count=2,
        check_count=10,
        passed_check_count=10,
        blocker_count=0,
        verified_by="local-owner",
        verification_note=None,
        verified_at_kst=datetime(2026, 7, 12, 12, 10, 0),
    )


def _artifact_fingerprints() -> dict[str, str]:
    return {
        "mysql_target_backup": "1" * 64,
        "replay_artifact_backup": "2" * 64,
    }


def _evidence_set_fingerprint(
    plan: DataDeletionDryRunPlan,
    evidence_ids: dict[str, int],
) -> str:
    fingerprints = _artifact_fingerprints()
    body = {
        "contract_version": BACKUP_EVIDENCE_CONTRACT_VERSION,
        "dry_run_plan_id": plan.id,
        "plan_fingerprint_sha256": plan.plan_fingerprint_sha256,
        "records": [
            {
                "prerequisite_key": key,
                "evidence_id": evidence_ids[key],
                "evidence_fingerprint_sha256": fingerprints[key],
            }
            for key in sorted(evidence_ids)
        ],
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _write_mysql_archive(path: Path, plan: DataDeletionDryRunPlan) -> None:
    row = {
        "id": 1,
        "account_id": "account.test",
        "shard": "steam",
        "current_name": "Yuuki_Asuna---",
        "active": 1,
        "created_at_kst": {
            "$pubg_ai_type": "datetime",
            "value": "2026-07-12T11:00:00.123456",
        },
    }
    line = json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
    table_record = {
        "sequence": 1,
        "table": "registered_players",
        "entry": "tables/001-registered_players.jsonl",
        "row_count": 1,
        "content_bytes": len(line),
        "content_sha256": hashlib.sha256(line).hexdigest(),
        "selector": {
            "kind": "target_identity",
            "account_id": "account.test",
            "shard": "steam",
        },
    }
    manifest = {
        "format_version": MYSQL_BACKUP_FORMAT_VERSION,
        "builder_contract_version": BACKUP_BUILDER_CONTRACT_VERSION,
        "request_id": 17,
        "dry_run_plan_id": plan.id,
        "plan_fingerprint_sha256": plan.plan_fingerprint_sha256,
        "source_fingerprint_sha256": plan.source_fingerprint_sha256,
        "built_at_kst": "2026-07-12T12:05:00+09:00",
        "row_count": 1,
        "tables": [table_record],
        "schema_creation_included": False,
        "restore_supported_by_current_application": False,
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(table_record["entry"], line)
        archive.writestr("manifest.json", json.dumps(manifest, separators=(",", ":")))


def _write_replay_archive(
    path: Path,
    plan: DataDeletionDryRunPlan,
    *,
    source_relative_path: str = "timeline/match-1.json",
) -> None:
    body = b'{"timeline":true}'
    entry = "files/timeline/match-1.json"
    file_record = {
        "sequence": 1,
        "record_id": 31,
        "artifact_type": "timeline_json",
        "match_id": "match-1",
        "source_relative_path": source_relative_path,
        "entry": entry,
        "size_bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
    }
    manifest = {
        "format_version": REPLAY_BACKUP_FORMAT_VERSION,
        "builder_contract_version": BACKUP_BUILDER_CONTRACT_VERSION,
        "dry_run_plan_id": plan.id,
        "plan_fingerprint_sha256": plan.plan_fingerprint_sha256,
        "source_fingerprint_sha256": plan.source_fingerprint_sha256,
        "built_at_kst": "2026-07-12T12:05:00+09:00",
        "file_count": 1,
        "source_file_bytes": len(body),
        "files": [file_record],
        "restore_supported_by_current_application": False,
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(entry, body)
        archive.writestr("manifest.json", json.dumps(manifest, separators=(",", ":")))


def _revalidated(
    plan: DataDeletionDryRunPlan,
    verification: DataDeletionBackupVerificationRun,
    mysql_path: Path,
    replay_path: Path,
) -> RevalidatedBackupBuild:
    artifacts = [
        {
            "prerequisite_key": "mysql_target_backup",
            "path": mysql_path.name,
            "sha256": _sha256(mysql_path),
            "size_bytes": mysql_path.stat().st_size,
            "covered_row_count": 1,
            "covered_file_count": None,
            "covered_file_bytes": None,
        },
        {
            "prerequisite_key": "replay_artifact_backup",
            "path": replay_path.name,
            "sha256": _sha256(replay_path),
            "size_bytes": replay_path.stat().st_size,
            "covered_row_count": None,
            "covered_file_count": 1,
            "covered_file_bytes": 17,
        },
    ]
    return RevalidatedBackupBuild(
        verification_run=verification,
        plan=plan,
        manifest_path=mysql_path.parent / "build-manifest.json",
        manifest={"artifacts": artifacts},
        artifact_paths={
            "mysql_target_backup": mysql_path,
            "replay_artifact_backup": replay_path,
        },
        current_result_fingerprint_sha256="f" * 64,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
