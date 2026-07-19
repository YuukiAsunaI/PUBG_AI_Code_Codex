from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch
import hashlib
import json
import shutil
import unittest

from pubg_ai.data_deletion_dry_run import (
    DRY_RUN_CONTRACT_VERSION,
    DataDeletionDryRunPlan,
    fingerprint_dry_run_plan,
)
from pubg_ai.data_deletion_quarantine_planner import (
    MINIMUM_CAPACITY_RESERVE_BYTES,
    QUARANTINE_PLANNER_CONTRACT_VERSION,
    DataDeletionQuarantinePlanningRun,
    build_quarantine_destination_contract,
    build_quarantine_planning_result,
    fingerprint_quarantine_destination_contract,
)
from pubg_ai.data_deletion_quarantine_rehearsal import (
    DataDeletionQuarantineRehearsalError,
    DataDeletionQuarantineRehearsalService,
    _rehearsal_run_from_row,
    expected_quarantine_rehearsal_confirmation,
)
from pubg_ai.data_deletion_requests import DataDeletionRequest
from pubg_ai.storage_contract import inspect_directory_read_only


class QuarantineRehearsalServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.raw_root = self.base / "raw"
        self.replay_root = self.base / "replay"
        self.backup_root = self.base / "backup"
        self.quarantine_root = self.base / "quarantine"
        for root in (
            self.raw_root,
            self.replay_root,
            self.backup_root,
            self.quarantine_root,
        ):
            root.mkdir()
        self.bodies = {
            "timeline/steam/match-1.json": b'{"timeline":true}',
            "map_snapshot/steam/match-1.jpeg": b"jpeg-fixture",
        }
        for relative, body in self.bodies.items():
            source = self.replay_root / relative
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(body)
        self.plan = _plan(self.bodies)
        self.planning = _planning_run(
            self.plan,
            raw_root=self.raw_root,
            replay_root=self.replay_root,
            backup_root=self.backup_root,
            quarantine_root=self.quarantine_root,
        )
        self.connection = AuditConnection(self.planning)
        self.backup_service = MagicMock()
        self.backup_service.require_latest_plan.return_value = self.plan
        self.backup_service.dry_run_service.list_plans.return_value = [self.plan]
        self.backup_service._assert_latest_plan_locked = MagicMock()
        self.planner_service = MagicMock()
        self.planner_service.get_run.return_value = self.planning
        self.planner_service.list_runs.return_value = [self.planning]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_passed_rehearsal_uses_only_synthetic_fixtures_and_cleans_scratch(self) -> None:
        raw_sentinel = self.raw_root / "sentinel.raw"
        replay_sentinel = self.replay_root / "sentinel.replay"
        backup_sentinel = self.backup_root / "sentinel.backup"
        for path, body in (
            (raw_sentinel, b"raw"),
            (replay_sentinel, b"replay"),
            (backup_sentinel, b"backup"),
        ):
            path.write_bytes(body)
        for relative in self.bodies:
            (self.replay_root / relative).unlink()

        run = self._service().run(
            _request(),
            quarantine_planning_run_id=self.planning.id,
            confirmation_text=self._confirmation(),
            actor_id="local-owner",
            note="synthetic rollback and recovery",
            reference_kst=datetime(2026, 7, 19, 10, 0, 0),
        )

        self.assertEqual(run.result_status, "passed")
        self.assertTrue(run.scratch_directory_removed)
        self.assertEqual(list(self.quarantine_root.iterdir()), [])
        self.assertEqual(raw_sentinel.read_bytes(), b"raw")
        self.assertEqual(replay_sentinel.read_bytes(), b"replay")
        self.assertEqual(backup_sentinel.read_bytes(), b"backup")
        self.assertEqual(run.fixture_file_count, 2)
        self.assertEqual(run.normal_committed_count, 2)
        self.assertEqual(run.normal_rolled_back_count, 2)
        self.assertEqual(run.recovery_case_count, 5)
        self.assertEqual(run.recovered_case_count, 4)
        self.assertEqual(run.ambiguous_case_count, 1)
        self.assertEqual(run.ambiguous_case_blocked_count, 1)
        self.assertTrue(run.result_json["safety"]["synthetic_fixtures_only"])
        self.assertFalse(
            run.result_json["safety"]["production_source_files_opened"]
        )
        self.assertFalse(
            run.result_json["safety"]["production_source_files_modified"]
        )
        self.assertFalse(run.result_json["safety"]["execution_enabled"])
        recovery = next(
            item
            for item in run.result_json["checks"]
            if item["key"] == "crash_recovery_cases"
        )
        self.assertEqual(recovery["status"], "passed")
        self.assertEqual(
            recovery["details"]["cases"][-1]["observed"],
            "blocked_ambiguous",
        )
        self.assertEqual(
            self.connection.dml,
            ["INSERT INTO data_deletion_quarantine_rehearsal_runs"],
        )
        self.assertTrue(self.connection.committed)
        self.assertFalse(self.connection.rolled_back)

    def test_tampered_planning_item_binding_is_audited_blocked(self) -> None:
        tampered_result = deepcopy(self.planning.result_json)
        tampered_result["rollback_contract"]["item_actions"][0][
            "expected_sha256"
        ] = "d" * 64
        tampered = replace(
            self.planning,
            result_json=tampered_result,
            result_fingerprint_sha256=_canonical_fingerprint(
                tampered_result,
                ensure_ascii=False,
            ),
        )
        self.connection.planning = tampered
        self.planner_service.get_run.return_value = tampered
        self.planner_service.list_runs.return_value = [tampered]
        confirmation = expected_quarantine_rehearsal_confirmation(
            17,
            self.plan.id,
            tampered.id,
            tampered.result_fingerprint_sha256,
            tampered.destination_contract_fingerprint_sha256,
        )

        run = self._service().run(
            _request(),
            quarantine_planning_run_id=tampered.id,
            confirmation_text=confirmation,
            actor_id="local-owner",
        )

        self.assertEqual(run.result_status, "blocked")
        self.assertTrue(run.scratch_directory_removed)
        self.assertEqual(run.normal_committed_count, 0)
        binding = next(
            item
            for item in run.result_json["checks"]
            if item["key"] == "planning_contract_binding"
        )
        self.assertEqual(binding["status"], "blocked")
        self.assertEqual(list(self.quarantine_root.iterdir()), [])

    def test_audit_reader_rejects_rehashed_unsafe_production_flag(self) -> None:
        run = self._service().run(
            _request(),
            quarantine_planning_run_id=self.planning.id,
            confirmation_text=self._confirmation(),
            actor_id="local-owner",
        )
        row = _audit_row(run)
        unsafe_result = deepcopy(run.result_json)
        unsafe_result["safety"]["production_source_files_opened"] = True
        row["result_json"] = unsafe_result
        row["result_fingerprint_sha256"] = _canonical_fingerprint(
            unsafe_result,
            ensure_ascii=True,
        )

        with self.assertRaisesRegex(
            DataDeletionQuarantineRehearsalError,
            "safety contract",
        ):
            _rehearsal_run_from_row(row)

    def test_wrong_confirmation_is_rejected_before_scratch_or_audit(self) -> None:
        with self.assertRaises(DataDeletionQuarantineRehearsalError):
            self._service().run(
                _request(),
                quarantine_planning_run_id=self.planning.id,
                confirmation_text="RUN SOMETHING ELSE",
                actor_id="local-owner",
            )

        self.assertEqual(list(self.quarantine_root.iterdir()), [])
        self.assertEqual(self.connection.dml, [])
        self.assertFalse(self.connection.committed)

    def test_latest_blocked_planning_run_disables_state_and_run(self) -> None:
        blocked = replace(
            self.planning,
            id=1502,
            result_status="blocked",
            capacity_evidence_id=None,
            result_json={
                **self.planning.result_json,
                "planning_status": "blocked",
            },
        )
        self.planner_service.list_runs.return_value = [blocked, self.planning]
        self.planner_service.get_run.return_value = blocked

        state = self._service().rehearsal_state(_request())

        self.assertFalse(state["rehearsal_allowed"])
        self.assertIsNone(state["planning_candidate"])
        self.assertTrue(
            any("must have passed" in item for item in state["rehearsal_blockers"])
        )

    def test_stale_selected_planning_run_is_rejected_before_scratch(self) -> None:
        newer = replace(self.planning, id=1502)
        self.planner_service.list_runs.return_value = [newer]

        with self.assertRaisesRegex(
            DataDeletionQuarantineRehearsalError,
            "not the latest",
        ):
            self._service().run(
                _request(),
                quarantine_planning_run_id=self.planning.id,
                confirmation_text=self._confirmation(),
                actor_id="local-owner",
            )

        self.assertEqual(list(self.quarantine_root.iterdir()), [])
        self.assertEqual(self.connection.dml, [])

    def test_cleanup_failure_is_audited_as_blocked(self) -> None:
        service = self._service()
        with patch(
            "pubg_ai.data_deletion_quarantine_rehearsal.shutil.rmtree",
            side_effect=OSError("cleanup denied"),
        ):
            run = service.run(
                _request(),
                quarantine_planning_run_id=self.planning.id,
                confirmation_text=self._confirmation(),
                actor_id="local-owner",
            )

        self.assertEqual(run.result_status, "blocked")
        self.assertFalse(run.scratch_directory_removed)
        scratch = Path(run.scratch_directory)
        self.assertTrue(scratch.is_dir())
        cleanup = next(
            item
            for item in run.result_json["checks"]
            if item["key"] == "scratch_cleanup"
        )
        self.assertEqual(cleanup["status"], "blocked")
        shutil.rmtree(scratch)

    def test_audit_insert_failure_rolls_back_after_scratch_cleanup(self) -> None:
        self.connection.fail_insert = True

        with self.assertRaisesRegex(RuntimeError, "audit insert failed"):
            self._service().run(
                _request(),
                quarantine_planning_run_id=self.planning.id,
                confirmation_text=self._confirmation(),
                actor_id="local-owner",
            )

        self.assertEqual(list(self.quarantine_root.iterdir()), [])
        self.assertFalse(self.connection.committed)
        self.assertTrue(self.connection.rolled_back)

    def test_state_exposes_exact_confirmation_and_never_enables_execution(self) -> None:
        state = self._service().rehearsal_state(_request())

        self.assertTrue(state["rehearsal_allowed"])
        self.assertEqual(
            state["planning_candidate"]["confirmation_text"],
            self._confirmation(),
        )
        self.assertTrue(state["synthetic_fixtures_only"])
        self.assertFalse(state["production_source_files_opened"])
        self.assertFalse(state["production_quarantine_performed"])
        self.assertFalse(state["execution_enabled"])

    def _service(self) -> DataDeletionQuarantineRehearsalService:
        return DataDeletionQuarantineRehearsalService(
            self.connection,
            backup_service=self.backup_service,
            planner_service=self.planner_service,
            quarantine_root=self.quarantine_root,
            raw_data_dir=self.raw_root,
            replay_data_dir=self.replay_root,
            backup_root=self.backup_root,
        )

    def _confirmation(self) -> str:
        return expected_quarantine_rehearsal_confirmation(
            17,
            self.plan.id,
            self.planning.id,
            self.planning.result_fingerprint_sha256,
            self.planning.destination_contract_fingerprint_sha256,
        )


class AuditConnection:
    def __init__(self, planning: DataDeletionQuarantinePlanningRun) -> None:
        self.planning = planning
        self.committed = False
        self.rolled_back = False
        self.fail_insert = False
        self.dml: list[str] = []

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
        if normalized.startswith(
            "SELECT * FROM data_deletion_quarantine_rehearsal_runs"
        ):
            self._rows = []
        elif normalized.startswith(
            "SELECT id, contract_version, plan_fingerprint_sha256"
        ):
            planning = self.connection.planning
            self._rows = [
                {
                    "id": planning.id,
                    "contract_version": planning.contract_version,
                    "plan_fingerprint_sha256": planning.plan_fingerprint_sha256,
                    "destination_contract_fingerprint_sha256": (
                        planning.destination_contract_fingerprint_sha256
                    ),
                    "result_fingerprint_sha256": (
                        planning.result_fingerprint_sha256
                    ),
                    "result_status": planning.result_status,
                    "capacity_evidence_id": planning.capacity_evidence_id,
                }
            ]
        elif normalized.startswith(
            "INSERT INTO data_deletion_quarantine_rehearsal_runs"
        ):
            self.connection.dml.append(
                "INSERT INTO data_deletion_quarantine_rehearsal_runs"
            )
            if self.connection.fail_insert:
                raise RuntimeError("audit insert failed")
            self.lastrowid = 1701

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


def _audit_row(run) -> dict:
    metrics = run.result_json["metrics"]
    return {
        "id": run.id,
        "request_id": run.request_id,
        "dry_run_plan_id": run.dry_run_plan_id,
        "quarantine_planning_run_id": run.quarantine_planning_run_id,
        "contract_version": run.contract_version,
        "plan_fingerprint_sha256": run.plan_fingerprint_sha256,
        "destination_contract_fingerprint_sha256": (
            run.destination_contract_fingerprint_sha256
        ),
        "planning_result_fingerprint_sha256": (
            run.planning_result_fingerprint_sha256
        ),
        "result_fingerprint_sha256": run.result_fingerprint_sha256,
        "result_status": run.result_status,
        "result_json": run.result_json,
        "scratch_directory": run.scratch_directory,
        "scratch_directory_removed": run.scratch_directory_removed,
        **metrics,
        "check_count": run.check_count,
        "passed_check_count": run.passed_check_count,
        "blocker_count": run.blocker_count,
        "run_by": run.run_by,
        "rehearsal_note": run.rehearsal_note,
        "run_at_kst": run.run_at_kst,
    }


def _canonical_fingerprint(value, *, ensure_ascii: bool) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=ensure_ascii,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _request() -> DataDeletionRequest:
    timestamp = datetime(2026, 7, 19, 9, 0, 0)
    return DataDeletionRequest(
        id=17,
        registered_player_id=1,
        account_id="account.test",
        shard="steam",
        player_name="Yuuki_Asuna---",
        deletion_scope="all",
        status="approved",
        reason="quarantine rehearsal test",
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
                "artifact_type": (
                    "timeline_json"
                    if relative_path.endswith(".json")
                    else "map_snapshot"
                ),
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
        "backup_prerequisites": [],
        "database_operations": [],
        "file_operations": operations,
        "row_exclusions": [{"table": "matches", "row_count": 1}],
        "file_exclusions": [{"category": "raw", "file_count": 1}],
        "audit_table_exclusions": [],
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
        generated_at_kst=datetime(2026, 7, 19, 9, 10, 0),
    )


def _planning_run(
    plan: DataDeletionDryRunPlan,
    *,
    raw_root: Path,
    replay_root: Path,
    backup_root: Path,
    quarantine_root: Path,
) -> DataDeletionQuarantinePlanningRun:
    destination = build_quarantine_destination_contract(plan, quarantine_root)
    destination_fingerprint = fingerprint_quarantine_destination_contract(
        destination
    )
    result = build_quarantine_planning_result(
        _request(),
        plan,
        quarantine_root=quarantine_root,
        raw_data_dir=raw_root,
        replay_data_dir=replay_root,
        backup_root=backup_root,
        destination_contract=destination,
        destination_contract_fingerprint_sha256=destination_fingerprint,
        root_status=inspect_directory_read_only(quarantine_root),
        free_bytes_probe=lambda _: (
            MINIMUM_CAPACITY_RESERVE_BYTES + plan.candidate_file_bytes + 1024
        ),
        planned_at_kst=datetime(2026, 7, 19, 9, 20, 0),
    )
    result_fingerprint = hashlib.sha256(
        json.dumps(
            result,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    metrics = result["metrics"]
    return DataDeletionQuarantinePlanningRun(
        id=1501,
        request_id=17,
        dry_run_plan_id=plan.id,
        contract_version=QUARANTINE_PLANNER_CONTRACT_VERSION,
        plan_fingerprint_sha256=plan.plan_fingerprint_sha256,
        destination_contract_fingerprint_sha256=destination_fingerprint,
        quarantine_root=str(quarantine_root.resolve()),
        result_fingerprint_sha256=result_fingerprint,
        result_status="passed",
        result_json=result,
        candidate_file_count=metrics["candidate_file_count"],
        candidate_file_bytes=metrics["candidate_file_bytes"],
        safety_reserve_bytes=metrics["safety_reserve_bytes"],
        required_free_bytes=metrics["required_free_bytes"],
        observed_free_bytes=metrics["observed_free_bytes"],
        source_verified_file_count=metrics["source_verified_file_count"],
        source_verified_bytes=metrics["source_verified_bytes"],
        target_conflict_count=metrics["target_conflict_count"],
        check_count=len(result["checks"]),
        passed_check_count=len(result["checks"]),
        blocker_count=0,
        capacity_evidence_id=1601,
        planned_by="local-owner",
        planning_note=None,
        planned_at_kst=datetime(2026, 7, 19, 9, 20, 0),
    )


if __name__ == "__main__":
    unittest.main()
