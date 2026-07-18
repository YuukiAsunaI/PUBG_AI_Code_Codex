from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock
import hashlib
import json
import os
import unittest
import warnings
import zipfile

from pubg_ai.data_deletion_backup import (
    BACKUP_EVIDENCE_CONTRACT_VERSION,
    DataDeletionBackupError,
    DataDeletionBackupEvidence,
    fingerprint_backup_evidence,
    normalize_evidence_payload,
)
from pubg_ai.data_deletion_backup_builder import (
    DataDeletionBackupBuilderService,
    expected_backup_build_confirmation,
)
from pubg_ai.data_deletion_backup_verifier import (
    BACKUP_VERIFIER_CONTRACT_VERSION,
    DataDeletionBackupVerifierError,
    DataDeletionBackupVerifierService,
)
from pubg_ai.data_deletion_confirmation import fingerprint_preview_record
from pubg_ai.data_deletion_dry_run import (
    DRY_RUN_CONTRACT_VERSION,
    DataDeletionDryRunPlan,
    fingerprint_dry_run_plan,
)
from pubg_ai.data_deletion_requests import DataDeletionRequest


class DataDeletionBackupVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.backup_root = self.base / "backups"
        self.raw_root = self.base / "raw"
        self.replay_root = self.base / "replays"
        for path in (self.backup_root, self.raw_root, self.replay_root):
            path.mkdir()
        self.replay_relative = "timeline/steam/2026/07/12/match-1/timeline.json"
        self.replay_body = b'{"timeline":true}'
        replay_path = self.replay_root / self.replay_relative
        replay_path.parent.mkdir(parents=True)
        replay_path.write_bytes(self.replay_body)

        self.request = _request()
        self.preview = _preview()
        source_fingerprint, _ = fingerprint_preview_record(self.preview)
        self.plan = _plan(
            source_fingerprint,
            self.replay_relative,
            self.replay_body,
        )
        self.backup_service = _backup_service(self.plan, self.preview)
        self.build_result = DataDeletionBackupBuilderService(
            ReadOnlyConnection(),
            backup_service=self.backup_service,
            backup_root=self.backup_root,
            raw_data_dir=self.raw_root,
            replay_data_dir=self.replay_root,
            row_provider=lambda operation, _request: _rows()[str(operation["table"])],
        ).build(
            self.request,
            dry_run_plan_id=self.plan.id,
            confirmation_text=expected_backup_build_confirmation(
                self.request.id,
                self.plan.plan_fingerprint_sha256,
            ),
            actor_id="local-owner",
            reference_kst=datetime(2026, 7, 12, 12, 5, 0),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_verifies_both_artifacts_and_only_inserts_audit_run(self) -> None:
        connection = AuditConnection(self.plan)
        before = {
            path: (path.stat().st_mtime_ns, _sha256(path))
            for path in (
                self.build_result.manifest_path,
                *(artifact.path for artifact in self.build_result.artifacts),
            )
        }

        run = self._service(connection).verify(
            self.request,
            dry_run_plan_id=self.plan.id,
            manifest_path=str(self.build_result.manifest_path),
            expected_manifest_sha256=self.build_result.manifest_sha256,
            actor_id="local-owner",
            note="read-only verification",
            reference_kst=datetime(2026, 7, 12, 12, 10, 0),
        )

        self.assertEqual(run.contract_version, BACKUP_VERIFIER_CONTRACT_VERSION)
        self.assertEqual(run.result_status, "passed")
        self.assertEqual(run.verified_artifact_count, 2)
        self.assertEqual(run.blocker_count, 0)
        self.assertEqual(len(run.result_json["evidence_set"]["record_ids"]), 2)
        self.assertEqual(
            run.evidence_set_fingerprint_sha256,
            run.result_json["evidence_set"]["fingerprint_sha256"],
        )
        self.assertFalse(run.result_json["safety"]["restore_test_performed"])
        self.assertFalse(run.result_json["safety"]["backup_integrity_prerequisite_attested"])
        mutations = [
            statement
            for statement in connection.statements
            if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE"))
        ]
        self.assertEqual(len(mutations), 1)
        self.assertIn("data_deletion_backup_verification_runs", mutations[0])
        self.assertEqual(connection.begin_count, 1)
        self.assertEqual(connection.commit_count, 1)
        self.assertEqual(connection.rollback_count, 0)
        self.assertEqual(
            before,
            {
                path: (path.stat().st_mtime_ns, _sha256(path))
                for path in before
            },
        )

    def test_state_discovers_only_fingerprint_bound_build_candidate(self) -> None:
        state = self._service(AuditConnection(self.plan)).verification_state(self.request)

        self.assertTrue(state["verification_allowed"])
        self.assertEqual(state["selectable_candidate_count"], 1)
        self.assertEqual(
            state["candidates"][0]["manifest_sha256"],
            self.build_result.manifest_sha256,
        )
        self.assertFalse(state["candidates"][0]["artifact_contents_verified"])
        self.assertFalse(state["restore_test_performed"])

    def test_wrong_selected_manifest_hash_records_blocked_audit(self) -> None:
        connection = AuditConnection(self.plan)

        run = self._verify(connection, expected_manifest_sha256="0" * 64)

        self.assertEqual(run.result_status, "blocked")
        self.assertGreater(run.blocker_count, 0)
        self.assertIn("SHA-256", " ".join(run.result_json["verification_blockers"]))
        self.assertTrue(any("INSERT INTO data_deletion_backup_verification_runs" in item for item in connection.statements))

    def test_changed_whole_zip_is_blocked(self) -> None:
        mysql_path = self._artifact_path("mysql_target_backup")
        with mysql_path.open("ab") as target:
            target.write(b"tamper")

        run = self._verify(AuditConnection(self.plan))

        self.assertEqual(run.result_status, "blocked")
        self.assertIn("whole-file SHA-256", " ".join(run.result_json["verification_blockers"]))

    def test_recomputed_build_without_immutable_builder_evidence_is_blocked(self) -> None:
        replay_path = self._artifact_path("replay_artifact_backup")
        _rewrite_zip(replay_path, additions=[("files/extra.bin", b"extra")])
        expected_manifest_sha = _refresh_build_manifest(
            self.build_result.manifest_path,
            replay_path,
        )

        run = self._verify(
            AuditConnection(self.plan),
            expected_manifest_sha256=expected_manifest_sha,
        )

        self.assertEqual(run.result_status, "blocked")
        self.assertIn(
            "immutable builder evidence",
            " ".join(run.result_json["verification_blockers"]),
        )

    def test_missing_builder_evidence_rejects_verification_without_audit_insert(self) -> None:
        _EVIDENCE_BY_PLAN_ID[self.plan.id] = []
        connection = AuditConnection(self.plan)

        with self.assertRaises(DataDeletionBackupVerifierError):
            self._verify(connection)

        self.assertFalse(any("INSERT" in item.upper() for item in connection.statements))

    def test_internal_jsonl_hash_mismatch_is_blocked_even_when_outer_hashes_are_updated(self) -> None:
        mysql_path = self._artifact_path("mysql_target_backup")
        with zipfile.ZipFile(mysql_path, "r") as archive:
            table_entry = next(name for name in archive.namelist() if name.startswith("tables/"))
            changed = archive.read(table_entry).replace(b"account.test", b"account.fail")
        _rewrite_zip(mysql_path, replacements={table_entry: changed})
        expected_manifest_sha = self._refresh_and_reanchor(mysql_path)

        run = self._verify(
            AuditConnection(self.plan),
            expected_manifest_sha256=expected_manifest_sha,
        )

        self.assertEqual(run.result_status, "blocked")
        self.assertIn("JSONL content differs", " ".join(run.result_json["verification_blockers"]))

    def test_zip_slip_entry_is_blocked(self) -> None:
        replay_path = self._artifact_path("replay_artifact_backup")
        _rewrite_zip(replay_path, additions=[("../escape.txt", b"escape")])
        expected_manifest_sha = self._refresh_and_reanchor(replay_path)

        run = self._verify(
            AuditConnection(self.plan),
            expected_manifest_sha256=expected_manifest_sha,
        )

        self.assertEqual(run.result_status, "blocked")
        self.assertIn("path is unsafe", " ".join(run.result_json["verification_blockers"]))
        self.assertFalse((self.build_result.build_directory.parent / "escape.txt").exists())

    def test_duplicate_zip_entry_is_blocked(self) -> None:
        replay_path = self._artifact_path("replay_artifact_backup")
        with zipfile.ZipFile(replay_path, "r") as archive:
            duplicate_name = next(name for name in archive.namelist() if name.startswith("files/"))
            duplicate_body = archive.read(duplicate_name)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            _rewrite_zip(
                replay_path,
                additions=[(duplicate_name, duplicate_body)],
                allow_duplicate=True,
            )
        expected_manifest_sha = self._refresh_and_reanchor(replay_path)

        run = self._verify(
            AuditConnection(self.plan),
            expected_manifest_sha256=expected_manifest_sha,
        )

        self.assertEqual(run.result_status, "blocked")
        self.assertIn("duplicate ZIP entry", " ".join(run.result_json["verification_blockers"]))

    def test_undeclared_zip_entry_is_blocked(self) -> None:
        replay_path = self._artifact_path("replay_artifact_backup")
        _rewrite_zip(replay_path, additions=[("files/extra.bin", b"extra")])
        expected_manifest_sha = self._refresh_and_reanchor(replay_path)

        run = self._verify(
            AuditConnection(self.plan),
            expected_manifest_sha256=expected_manifest_sha,
        )

        self.assertEqual(run.result_status, "blocked")
        self.assertIn("undeclared entries", " ".join(run.result_json["verification_blockers"]))

    def test_malformed_jsonl_is_blocked(self) -> None:
        mysql_path = self._artifact_path("mysql_target_backup")
        with zipfile.ZipFile(mysql_path, "r") as archive:
            table_entry = next(name for name in archive.namelist() if name.startswith("tables/"))
            original = archive.read(table_entry)
        malformed = b"{" + (b"x" * (len(original) - 2)) + b"\n"
        _rewrite_zip(mysql_path, replacements={table_entry: malformed})
        expected_manifest_sha = self._refresh_and_reanchor(mysql_path)

        run = self._verify(
            AuditConnection(self.plan),
            expected_manifest_sha256=expected_manifest_sha,
        )

        self.assertEqual(run.result_status, "blocked")
        self.assertIn("invalid JSONL row", " ".join(run.result_json["verification_blockers"]))

    def test_manifest_outside_selected_plan_is_rejected_without_audit_insert(self) -> None:
        outside = self.backup_root / "build-manifest.json"
        outside.write_bytes(self.build_result.manifest_path.read_bytes())
        connection = AuditConnection(self.plan)

        with self.assertRaises(DataDeletionBackupVerifierError):
            self._service(connection).verify(
                self.request,
                dry_run_plan_id=self.plan.id,
                manifest_path=str(outside),
                expected_manifest_sha256=_sha256(outside),
                actor_id="local-owner",
            )

        self.assertFalse(any("INSERT" in item.upper() for item in connection.statements))

    def test_stale_plan_is_rejected_before_any_audit_write(self) -> None:
        connection = AuditConnection(self.plan)
        service = self._service(connection)
        self.backup_service.require_latest_plan.side_effect = DataDeletionBackupError(
            "dry-run plan is not latest"
        )

        with self.assertRaises(DataDeletionBackupError):
            service.verify(
                self.request,
                dry_run_plan_id=self.plan.id - 1,
                manifest_path=str(self.build_result.manifest_path),
                expected_manifest_sha256=self.build_result.manifest_sha256,
                actor_id="local-owner",
            )

        self.assertFalse(any("INSERT" in item.upper() for item in connection.statements))

    def _verify(
        self,
        connection: "AuditConnection",
        *,
        expected_manifest_sha256: str | None = None,
    ):
        return self._service(connection).verify(
            self.request,
            dry_run_plan_id=self.plan.id,
            manifest_path=str(self.build_result.manifest_path),
            expected_manifest_sha256=(
                expected_manifest_sha256 or _sha256(self.build_result.manifest_path)
            ),
            actor_id="local-owner",
            reference_kst=datetime(2026, 7, 12, 12, 10, 0),
        )

    def _service(self, connection: "AuditConnection") -> DataDeletionBackupVerifierService:
        return DataDeletionBackupVerifierService(
            connection,
            backup_service=self.backup_service,
            backup_root=self.backup_root,
            raw_data_dir=self.raw_root,
            replay_data_dir=self.replay_root,
        )

    def _refresh_and_reanchor(self, artifact_path: Path) -> str:
        manifest_sha = _refresh_build_manifest(
            self.build_result.manifest_path,
            artifact_path,
        )
        _install_evidence_from_manifest(
            self.backup_service,
            self.request,
            self.plan,
            self.build_result.manifest_path,
            manifest_sha,
        )
        return manifest_sha

    def _artifact_path(self, key: str) -> Path:
        return next(
            artifact.path
            for artifact in self.build_result.artifacts
            if artifact.prerequisite_key == key
        )


class ReadOnlyConnection:
    def begin(self) -> None:
        pass

    def rollback(self) -> None:
        pass


class AuditConnection:
    def __init__(self, plan: DataDeletionDryRunPlan) -> None:
        self.plan = plan
        self.statements: list[str] = []
        self.begin_count = 0
        self.commit_count = 0
        self.rollback_count = 0

    def cursor(self) -> "AuditCursor":
        return AuditCursor(self)

    def begin(self) -> None:
        self.begin_count += 1

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1


class AuditCursor:
    def __init__(self, connection: AuditConnection) -> None:
        self.connection = connection
        self.lastrowid = 0
        self._row = None
        self._rows: list[dict[str, object]] = []

    def __enter__(self) -> "AuditCursor":
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def execute(self, statement: str, parameters: tuple[object, ...]) -> None:
        normalized = " ".join(statement.split())
        self.connection.statements.append(normalized)
        if "SELECT status FROM data_deletion_requests" in normalized:
            self._row = {"status": "approved"}
        elif "SELECT id, plan_fingerprint_sha256" in normalized:
            self._row = {
                "id": self.connection.plan.id,
                "plan_fingerprint_sha256": self.connection.plan.plan_fingerprint_sha256,
            }
        elif "FROM data_deletion_backup_evidence" in normalized:
            self._rows = [
                {
                    "id": item.id,
                    "prerequisite_key": item.prerequisite_key,
                    "evidence_fingerprint_sha256": item.evidence_fingerprint_sha256,
                }
                for item in _EVIDENCE_BY_PLAN_ID.get(self.connection.plan.id, [])
                if item.id in {int(value) for value in parameters[1:]}
            ]
            self._row = None
        elif "INSERT INTO data_deletion_backup_verification_runs" in normalized:
            self.lastrowid = 1201
            self._row = None
        elif "FROM data_deletion_backup_verification_runs" in normalized:
            self._rows = []
        else:
            raise AssertionError(f"unexpected SQL: {normalized}")

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


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
        reason="backup verifier test",
        requested_by_discord_user_id="100",
        requested_guild_id="10",
        requested_channel_id="20",
        requested_at_kst=requested_at,
        expires_at_kst=requested_at + timedelta(hours=24),
        reviewed_by="local:local-owner",
        reviewed_at_kst=requested_at,
        review_note="approved",
        updated_at_kst=requested_at,
    )


def _preview() -> dict[str, object]:
    return {
        "request_id": 17,
        "target": {
            "account_id": "account.test",
            "shard": "steam",
            "player_name": "Yuuki_Asuna---",
        },
        "deletion_scope": "all",
        "included_sections": {"registration": True, "normalized": True, "raw": True, "replay": True},
        "matched_match_count": 1,
        "candidate_row_count": 2,
        "preserved_reference_row_count": 0,
        "row_impacts": [],
        "preserved_references": [],
        "raw_files": None,
        "replay_files": None,
        "verification": {"catalog_complete": True, "filesystem_issue_count": 0},
    }


def _plan(
    source_fingerprint: str,
    replay_relative_path: str,
    replay_body: bytes,
) -> DataDeletionDryRunPlan:
    request = _request()
    database_operations = [
        {
            "sequence": 1,
            "action": "delete_rows_planned",
            "table": "registered_players",
            "selector": {
                "kind": "target_identity",
                "account_id": request.account_id,
                "shard": request.shard,
            },
            "estimated_rows": 1,
            "mutation_enabled": False,
        },
        {
            "sequence": 2,
            "action": "delete_rows_planned",
            "table": "player_collection_states",
            "selector": {
                "kind": "registered_player_join",
                "account_id": request.account_id,
                "shard": request.shard,
            },
            "estimated_rows": 1,
            "mutation_enabled": False,
        },
    ]
    file_operations = [
        {
            "sequence": 1,
            "action": "quarantine_file_planned",
            "source_table": "replay_artifacts",
            "record_id": 10,
            "artifact_type": "timeline",
            "match_id": "match-1",
            "storage_root": "PUBG_REPLAY_DATA_DIR",
            "relative_path": replay_relative_path,
            "declared_size_bytes": len(replay_body),
            "sha256": hashlib.sha256(replay_body).hexdigest(),
            "verification_status": "verified",
            "ownership": "player_artifact",
            "mutation_enabled": False,
        }
    ]
    plan_json = {
        "contract_version": DRY_RUN_CONTRACT_VERSION,
        "request_id": request.id,
        "source_fingerprint_sha256": source_fingerprint,
        "metrics": {
            "candidate_row_count": 2,
            "candidate_file_count": 1,
            "candidate_file_bytes": len(replay_body),
        },
        "backup_prerequisites": [
            {"key": "mysql_target_backup", "required": True},
            {"key": "replay_artifact_backup", "required": True},
            {"key": "quarantine_capacity_check", "required": True},
            {"key": "backup_integrity_verification", "required": True},
        ],
        "database_operations": database_operations,
        "file_operations": file_operations,
    }
    fingerprint = fingerprint_dry_run_plan(plan_json)
    return DataDeletionDryRunPlan(
        id=901,
        request_id=request.id,
        preview_snapshot_id=501,
        confirmation_id=701,
        contract_version=DRY_RUN_CONTRACT_VERSION,
        source_fingerprint_sha256=source_fingerprint,
        plan_fingerprint_sha256=fingerprint,
        plan_json=plan_json,
        operation_count=3,
        candidate_row_count=2,
        candidate_file_count=1,
        candidate_file_bytes=len(replay_body),
        excluded_row_count=0,
        excluded_file_count=0,
        generated_by="local-owner",
        generation_note=None,
        generated_at_kst=datetime(2026, 7, 12, 12, 0, 0),
    )


def _rows() -> dict[str, list[dict[str, object]]]:
    return {
        "registered_players": [
            {
                "id": 1,
                "account_id": "account.test",
                "shard": "steam",
                "created_at_kst": datetime(2026, 7, 1, 12, 0, 0),
                "accuracy": {"source": "literal object"},
            }
        ],
        "player_collection_states": [
            {"registered_player_id": 1, "last_error": None}
        ],
    }


_EVIDENCE_BY_PLAN_ID: dict[int, list[DataDeletionBackupEvidence]] = {}


def _backup_service(plan: DataDeletionDryRunPlan, preview: dict[str, object]) -> MagicMock:
    service = MagicMock()
    service.require_latest_plan.return_value = plan
    service.dry_run_service.list_plans.return_value = [plan]
    preview_result = MagicMock()
    preview_result.to_record.return_value = preview
    service.preview_service.build_preview.return_value = preview_result
    _EVIDENCE_BY_PLAN_ID[plan.id] = []

    def record_batch(
        request: DataDeletionRequest,
        *,
        evidence_by_key: dict[str, dict[str, object]],
        actor_id: str,
        note: str | None,
        reference_kst: datetime,
        **_: object,
    ) -> dict[str, DataDeletionBackupEvidence]:
        records: dict[str, DataDeletionBackupEvidence] = {}
        for offset, (key, payload) in enumerate(evidence_by_key.items(), start=1):
            normalized = normalize_evidence_payload(key, payload)
            item = DataDeletionBackupEvidence(
                id=800 + offset,
                request_id=request.id,
                dry_run_plan_id=plan.id,
                contract_version=BACKUP_EVIDENCE_CONTRACT_VERSION,
                plan_fingerprint_sha256=plan.plan_fingerprint_sha256,
                prerequisite_key=key,
                evidence_fingerprint_sha256=fingerprint_backup_evidence(
                    request.id,
                    plan,
                    key,
                    normalized,
                ),
                evidence_json=normalized,
                recorded_by=actor_id,
                evidence_note=note,
                recorded_at_kst=reference_kst,
            )
            records[key] = item
        _EVIDENCE_BY_PLAN_ID[plan.id] = list(records.values())
        return records

    service.record_evidence_batch.side_effect = record_batch
    service.list_evidence.side_effect = lambda plan_id, limit=500: list(
        _EVIDENCE_BY_PLAN_ID.get(plan_id, [])[:limit]
    )
    return service


def _install_evidence_from_manifest(
    service: MagicMock,
    request: DataDeletionRequest,
    plan: DataDeletionDryRunPlan,
    manifest_path: Path,
    manifest_sha256: str,
) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    note = (
        f"builder={manifest['contract_version']}; build_id={manifest['build_id']}; "
        f"opt_in_sha256={manifest['confirmation_text_sha256']}; "
        f"manifest_sha256={manifest_sha256}"
    )
    records: list[DataDeletionBackupEvidence] = []
    for offset, artifact in enumerate(manifest["artifacts"], start=1):
        payload = {
            "artifact_path": str(manifest_path.parent / artifact["path"]),
            "artifact_sha256": artifact["sha256"],
            "artifact_size_bytes": artifact["size_bytes"],
            "backup_created_at_kst": manifest["built_at_kst"],
            "covered_row_count": artifact["covered_row_count"],
            "covered_file_count": artifact["covered_file_count"],
            "covered_file_bytes": artifact["covered_file_bytes"],
        }
        key = artifact["prerequisite_key"]
        normalized = normalize_evidence_payload(key, payload)
        records.append(
            DataDeletionBackupEvidence(
                id=900 + offset,
                request_id=request.id,
                dry_run_plan_id=plan.id,
                contract_version=BACKUP_EVIDENCE_CONTRACT_VERSION,
                plan_fingerprint_sha256=plan.plan_fingerprint_sha256,
                prerequisite_key=key,
                evidence_fingerprint_sha256=fingerprint_backup_evidence(
                    request.id,
                    plan,
                    key,
                    normalized,
                ),
                evidence_json=normalized,
                recorded_by=manifest["built_by"],
                evidence_note=note,
                recorded_at_kst=datetime(2026, 7, 12, 12, 5, 0),
            )
        )
    _EVIDENCE_BY_PLAN_ID[plan.id] = records
    service.list_evidence.side_effect = lambda plan_id, limit=500: list(
        _EVIDENCE_BY_PLAN_ID.get(plan_id, [])[:limit]
    )


def _rewrite_zip(
    path: Path,
    *,
    replacements: dict[str, bytes] | None = None,
    additions: list[tuple[str, bytes]] | None = None,
    allow_duplicate: bool = False,
) -> None:
    replacements = replacements or {}
    additions = additions or []
    with zipfile.ZipFile(path, "r") as source:
        records = [(info.filename, source.read(info)) for info in source.infolist()]
    temporary = path.with_suffix(".rewrite")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for name, body in records:
            target.writestr(name, replacements.get(name, body))
        for name, body in additions:
            if not allow_duplicate and any(existing == name for existing, _ in records):
                raise AssertionError(f"duplicate test entry: {name}")
            target.writestr(name, body)
    os.replace(temporary, path)


def _refresh_build_manifest(manifest_path: Path, artifact_path: Path) -> str:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for artifact in manifest["artifacts"]:
        if artifact["path"] == artifact_path.name:
            artifact["sha256"] = _sha256(artifact_path)
            artifact["size_bytes"] = artifact_path.stat().st_size
            break
    else:
        raise AssertionError(f"artifact not declared: {artifact_path.name}")
    body = dict(manifest)
    body.pop("manifest_fingerprint_sha256", None)
    manifest["manifest_fingerprint_sha256"] = hashlib.sha256(
        json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    manifest_path.write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    return _sha256(manifest_path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    unittest.main()
