from __future__ import annotations

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock
import hashlib
import json
import unittest

from pubg_ai.data_deletion_dry_run import (
    DRY_RUN_CONTRACT_VERSION,
    DataDeletionDryRunPlan,
    fingerprint_dry_run_plan,
)
from pubg_ai.data_deletion_quarantine_planner import (
    MINIMUM_CAPACITY_RESERVE_BYTES,
    DataDeletionQuarantinePlannerError,
    DataDeletionQuarantinePlannerService,
    build_quarantine_destination_contract,
    build_quarantine_planning_result,
    expected_quarantine_planning_confirmation,
    fingerprint_quarantine_destination_contract,
)
from pubg_ai.data_deletion_requests import DataDeletionRequest
from pubg_ai.storage_contract import inspect_directory_read_only


class QuarantinePlanningResultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.raw_root = self.base / "raw"
        self.replay_root = self.base / "replay"
        self.backup_root = self.base / "backup"
        self.quarantine_root = self.base / "quarantine"
        for path in (
            self.raw_root,
            self.replay_root,
            self.backup_root,
            self.quarantine_root,
        ):
            path.mkdir()
        self.bodies = {
            "timeline/steam/match-1.json": b'{"timeline":true}',
            "map_snapshot/steam/match-1.jpeg": b"jpeg-fixture",
        }
        for relative_path, body in self.bodies.items():
            path = self.replay_root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        self.plan = _plan(self.bodies)
        self.destination = build_quarantine_destination_contract(
            self.plan,
            self.quarantine_root,
        )
        self.destination_fingerprint = fingerprint_quarantine_destination_contract(
            self.destination
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_passed_plan_hashes_sources_and_never_creates_destination_files(self) -> None:
        result = self._build_result(
            free_bytes=MINIMUM_CAPACITY_RESERVE_BYTES + 1000,
        )

        self.assertEqual(result["planning_status"], "passed")
        self.assertEqual(result["metrics"]["source_verified_file_count"], 2)
        self.assertEqual(
            result["metrics"]["source_verified_bytes"],
            sum(len(body) for body in self.bodies.values()),
        )
        self.assertEqual(result["metrics"]["target_conflict_count"], 0)
        self.assertEqual(list(self.quarantine_root.iterdir()), [])
        self.assertTrue(all(path.exists() for path in self._source_paths()))
        self.assertTrue(result["safety"]["read_only"])
        self.assertFalse(result["safety"]["directories_created"])
        self.assertFalse(result["safety"]["files_copied"])
        self.assertFalse(result["safety"]["source_files_removed"])
        self.assertFalse(result["safety"]["execution_enabled"])
        self.assertEqual(
            len(result["postcondition_contract"]["item_checks"]),
            2,
        )
        self.assertEqual(len(result["rollback_contract"]["item_actions"]), 2)
        self.assertFalse(
            result["crash_recovery_contract"]["independently_rehearsed"]
        )

    def test_blocks_insufficient_capacity_without_writing(self) -> None:
        result = self._build_result(free_bytes=1)

        self.assertEqual(result["planning_status"], "blocked")
        self.assertIn("quarantine_capacity", result["planning_blockers"])
        self.assertEqual(list(self.quarantine_root.iterdir()), [])
        self.assertTrue(all(path.exists() for path in self._source_paths()))

    def test_blocks_source_hash_drift(self) -> None:
        source = self.replay_root / "timeline/steam/match-1.json"
        source.write_bytes(b"x" * len(self.bodies["timeline/steam/match-1.json"]))

        result = self._build_result(
            free_bytes=MINIMUM_CAPACITY_RESERVE_BYTES + 1000,
        )

        self.assertEqual(result["planning_status"], "blocked")
        source_check = next(
            check for check in result["checks"] if check["key"] == "source_file_contract"
        )
        self.assertEqual(source_check["status"], "blocked")
        self.assertIn("SHA-256 differs", json.dumps(source_check))
        self.assertEqual(list(self.quarantine_root.iterdir()), [])

    def test_blocks_existing_deterministic_target(self) -> None:
        operation = self.plan.plan_json["file_operations"][0]
        target = (
            self.quarantine_root
            / "requests"
            / "17"
            / "plans"
            / "901"
            / "items"
            / f"{operation['sequence']:06d}-{operation['record_id']}"
            / operation["relative_path"]
        )
        target.parent.mkdir(parents=True)
        target.write_bytes(b"unexpected-existing-target")

        result = self._build_result(
            free_bytes=MINIMUM_CAPACITY_RESERVE_BYTES + 1000,
        )

        self.assertEqual(result["planning_status"], "blocked")
        self.assertIn("destination_target_conflicts", result["planning_blockers"])
        self.assertEqual(result["metrics"]["target_conflict_count"], 1)
        self.assertEqual(target.read_bytes(), b"unexpected-existing-target")

    def test_blocks_unsafe_relative_path_before_outside_access(self) -> None:
        self.plan.plan_json["file_operations"][0]["relative_path"] = "../escape.bin"
        self.plan = _refingerprint(self.plan)
        self.destination = build_quarantine_destination_contract(
            self.plan,
            self.quarantine_root,
        )
        self.destination_fingerprint = fingerprint_quarantine_destination_contract(
            self.destination
        )

        result = self._build_result(
            free_bytes=MINIMUM_CAPACITY_RESERVE_BYTES + 1000,
        )

        self.assertEqual(result["planning_status"], "blocked")
        self.assertFalse((self.base / "escape.bin").exists())
        self.assertEqual(list(self.quarantine_root.iterdir()), [])

    def _build_result(self, *, free_bytes: int) -> dict:
        return build_quarantine_planning_result(
            _request(),
            self.plan,
            quarantine_root=self.quarantine_root,
            raw_data_dir=self.raw_root,
            replay_data_dir=self.replay_root,
            backup_root=self.backup_root,
            destination_contract=self.destination,
            destination_contract_fingerprint_sha256=self.destination_fingerprint,
            root_status=inspect_directory_read_only(self.quarantine_root),
            free_bytes_probe=lambda _: free_bytes,
            planned_at_kst=datetime(2026, 7, 18, 20, 0, 0),
        )

    def _source_paths(self) -> list[Path]:
        return [self.replay_root / relative for relative in self.bodies]


class QuarantinePlannerServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.raw_root = self.base / "raw"
        self.replay_root = self.base / "replay"
        self.backup_root = self.base / "backup"
        self.quarantine_root = self.base / "quarantine"
        for path in (
            self.raw_root,
            self.replay_root,
            self.backup_root,
            self.quarantine_root,
        ):
            path.mkdir()
        self.body = b"quarantine-source"
        source = self.replay_root / "timeline/match-1.json"
        source.parent.mkdir(parents=True)
        source.write_bytes(self.body)
        self.plan = _plan({"timeline/match-1.json": self.body})
        self.connection = AuditConnection()
        self.backup_service = MagicMock()
        self.backup_service.require_latest_plan.return_value = self.plan
        self.backup_service.dry_run_service.list_plans.return_value = [self.plan]
        self.backup_service._assert_latest_plan_locked = MagicMock()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_passed_run_atomically_appends_bound_capacity_evidence_and_audit(self) -> None:
        service = self._service(
            free_bytes=MINIMUM_CAPACITY_RESERVE_BYTES + len(self.body) + 1000
        )
        destination = build_quarantine_destination_contract(
            self.plan,
            self.quarantine_root,
        )
        confirmation = expected_quarantine_planning_confirmation(
            17,
            self.plan.id,
            self.plan.plan_fingerprint_sha256,
            fingerprint_quarantine_destination_contract(destination),
        )

        run = service.run(
            _request(),
            dry_run_plan_id=self.plan.id,
            confirmation_text=confirmation,
            actor_id="local-owner",
            note="capacity and postconditions",
            reference_kst=datetime(2026, 7, 18, 20, 0, 0),
        )

        self.assertEqual(run.result_status, "passed")
        self.assertEqual(run.capacity_evidence_id, 1401)
        self.assertTrue(self.connection.committed)
        self.assertFalse(self.connection.rolled_back)
        self.assertEqual(
            self.connection.dml,
            [
                "INSERT INTO data_deletion_backup_evidence",
                "INSERT INTO data_deletion_quarantine_planning_runs",
            ],
        )
        evidence_payload = json.loads(self.connection.evidence_parameters[6])
        self.assertEqual(evidence_payload["candidate_file_count"], 1)
        self.assertEqual(evidence_payload["candidate_file_bytes"], len(self.body))
        self.assertGreaterEqual(
            evidence_payload["safety_reserve_bytes"],
            MINIMUM_CAPACITY_RESERVE_BYTES,
        )
        self.assertTrue(evidence_payload["source_disjoint_verified"])
        self.assertEqual(list(self.quarantine_root.iterdir()), [])

    def test_blocked_capacity_run_appends_audit_without_evidence(self) -> None:
        service = self._service(free_bytes=0)
        confirmation = self._confirmation()

        run = service.run(
            _request(),
            dry_run_plan_id=self.plan.id,
            confirmation_text=confirmation,
            actor_id="local-owner",
        )

        self.assertEqual(run.result_status, "blocked")
        self.assertIsNone(run.capacity_evidence_id)
        self.assertEqual(
            self.connection.dml,
            ["INSERT INTO data_deletion_quarantine_planning_runs"],
        )
        self.assertTrue(self.connection.committed)

    def test_wrong_confirmation_is_rejected_before_hash_or_audit(self) -> None:
        hasher = MagicMock(side_effect=AssertionError("hasher must not run"))
        service = self._service(
            free_bytes=MINIMUM_CAPACITY_RESERVE_BYTES + 1000,
            file_hasher=hasher,
        )

        with self.assertRaises(DataDeletionQuarantinePlannerError):
            service.run(
                _request(),
                dry_run_plan_id=self.plan.id,
                confirmation_text="RUN SOMETHING ELSE",
                actor_id="local-owner",
            )

        hasher.assert_not_called()
        self.assertEqual(self.connection.dml, [])
        self.assertFalse(self.connection.committed)

    def test_audit_failure_rolls_back_capacity_evidence(self) -> None:
        self.connection.fail_planning_insert = True
        service = self._service(
            free_bytes=MINIMUM_CAPACITY_RESERVE_BYTES + len(self.body) + 1000
        )

        with self.assertRaises(RuntimeError):
            service.run(
                _request(),
                dry_run_plan_id=self.plan.id,
                confirmation_text=self._confirmation(),
                actor_id="local-owner",
            )

        self.assertFalse(self.connection.committed)
        self.assertTrue(self.connection.rolled_back)

    def test_state_blocks_destination_nested_inside_replay_root(self) -> None:
        nested = self.replay_root / "quarantine"
        nested.mkdir()
        service = DataDeletionQuarantinePlannerService(
            self.connection,
            backup_service=self.backup_service,
            quarantine_root=nested,
            raw_data_dir=self.raw_root,
            replay_data_dir=self.replay_root,
            backup_root=self.backup_root,
        )

        state = service.planning_state(_request())

        self.assertFalse(state["planning_allowed"])
        self.assertTrue(
            any("PUBG_REPLAY_DATA_DIR" in blocker for blocker in state["planning_blockers"])
        )
        self.assertTrue(state["read_only_probe"])
        self.assertFalse(state["write_probe_performed"])
        self.assertEqual(self.connection.dml, [])

    def _service(
        self,
        *,
        free_bytes: int,
        file_hasher=None,
    ) -> DataDeletionQuarantinePlannerService:
        return DataDeletionQuarantinePlannerService(
            self.connection,
            backup_service=self.backup_service,
            quarantine_root=self.quarantine_root,
            raw_data_dir=self.raw_root,
            replay_data_dir=self.replay_root,
            backup_root=self.backup_root,
            free_bytes_probe=lambda _: free_bytes,
            file_hasher=file_hasher,
        )

    def _confirmation(self) -> str:
        destination = build_quarantine_destination_contract(
            self.plan,
            self.quarantine_root,
        )
        return expected_quarantine_planning_confirmation(
            17,
            self.plan.id,
            self.plan.plan_fingerprint_sha256,
            fingerprint_quarantine_destination_contract(destination),
        )


class AuditConnection:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False
        self.dml: list[str] = []
        self.evidence_parameters: tuple | None = None
        self.fail_planning_insert = False

    def cursor(self) -> "AuditCursor":
        return AuditCursor(self)

    def begin(self) -> None:
        return None

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


class AuditCursor:
    def __init__(self, connection: AuditConnection) -> None:
        self.connection = connection
        self.lastrowid = 0
        self._rows: list[dict] = []

    def __enter__(self) -> "AuditCursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, query: str, parameters: tuple | None = None) -> None:
        normalized = " ".join(query.split())
        if normalized.startswith("INSERT INTO data_deletion_backup_evidence"):
            self.connection.dml.append("INSERT INTO data_deletion_backup_evidence")
            self.connection.evidence_parameters = parameters
            self.lastrowid = 1401
        elif normalized.startswith(
            "INSERT INTO data_deletion_quarantine_planning_runs"
        ):
            self.connection.dml.append(
                "INSERT INTO data_deletion_quarantine_planning_runs"
            )
            if self.connection.fail_planning_insert:
                raise RuntimeError("planning audit insert failed")
            self.lastrowid = 1501
        elif normalized.startswith(
            "SELECT * FROM data_deletion_quarantine_planning_runs"
        ):
            self._rows = []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


def _request() -> DataDeletionRequest:
    timestamp = datetime(2026, 7, 18, 19, 0, 0)
    return DataDeletionRequest(
        id=17,
        registered_player_id=1,
        account_id="account.test",
        shard="steam",
        player_name="Yuuki_Asuna---",
        deletion_scope="all",
        status="approved",
        reason="quarantine planner test",
        requested_by_discord_user_id="100",
        requested_guild_id="10",
        requested_channel_id="20",
        requested_at_kst=timestamp,
        expires_at_kst=timestamp,
        reviewed_by="local-owner",
        reviewed_at_kst=timestamp,
        review_note="approved",
        updated_at_kst=timestamp,
    )


def _plan(files: dict[str, bytes]) -> DataDeletionDryRunPlan:
    operations = []
    for sequence, (relative_path, body) in enumerate(sorted(files.items()), start=1):
        operations.append(
            {
                "sequence": sequence,
                "phase": "quarantine_player_files",
                "action": "quarantine_file_planned",
                "source_table": "replay_artifacts",
                "record_id": 30 + sequence,
                "artifact_type": "timeline_json" if relative_path.endswith(".json") else "map_snapshot",
                "match_id": "match-1",
                "storage_root": "PUBG_REPLAY_DATA_DIR",
                "relative_path": relative_path,
                "declared_size_bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
                "verification_status": "present",
                "ownership": "player_artifact",
                "mutation_enabled": False,
            }
        )
    plan_json = {
        "contract_version": DRY_RUN_CONTRACT_VERSION,
        "request_id": 17,
        "source_fingerprint_sha256": "a" * 64,
        "metrics": {
            "candidate_row_count": 1,
            "candidate_file_count": len(operations),
            "candidate_file_bytes": sum(len(body) for body in files.values()),
            "excluded_row_count": 2,
            "excluded_file_count": 1,
        },
        "backup_prerequisites": [
            {"key": "mysql_target_backup", "required": True},
            {"key": "replay_artifact_backup", "required": True},
            {"key": "quarantine_capacity_check", "required": True},
            {"key": "backup_integrity_verification", "required": True},
        ],
        "database_operations": [],
        "file_operations": operations,
        "row_exclusions": [{"table": "matches", "row_count": 1}],
        "file_exclusions": [{"category": "raw", "file_count": 1}],
        "audit_table_exclusions": [
            {"table": "data_deletion_requests", "reason": "immutable"}
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
        operation_count=len(operations),
        candidate_row_count=1,
        candidate_file_count=len(operations),
        candidate_file_bytes=sum(len(body) for body in files.values()),
        excluded_row_count=2,
        excluded_file_count=1,
        generated_by="local-owner",
        generation_note=None,
        generated_at_kst=datetime(2026, 7, 18, 19, 30, 0),
    )


def _refingerprint(plan: DataDeletionDryRunPlan) -> DataDeletionDryRunPlan:
    fingerprint = fingerprint_dry_run_plan(plan.plan_json)
    return DataDeletionDryRunPlan(
        **{
            **plan.__dict__,
            "plan_fingerprint_sha256": fingerprint,
        }
    )


if __name__ == "__main__":
    unittest.main()
