from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO
import base64
import binascii
import hashlib
import hmac
import json
import math
import os
import re
import zipfile

from pubg_ai.data_deletion_backup import (
    BACKUP_EVIDENCE_CONTRACT_VERSION,
    DataDeletionBackupEvidence,
    DataDeletionBackupService,
    fingerprint_backup_evidence,
    fingerprint_evidence_set,
)
from pubg_ai.data_deletion_backup_builder import (
    BACKUP_BUILDER_CONTRACT_VERSION,
    MYSQL_BACKUP_FORMAT_VERSION,
    REPLAY_BACKUP_FORMAT_VERSION,
    DataDeletionBackupBuilderError,
    artifact_prerequisite_keys,
    database_backup_select,
    overlapping_source_root,
)
from pubg_ai.data_deletion_dry_run import (
    DataDeletionDryRunPlan,
    fingerprint_dry_run_plan,
)
from pubg_ai.data_deletion_requests import DataDeletionRequest
from pubg_ai.time_utils import now_kst, to_kst


BACKUP_VERIFIER_CONTRACT_VERSION = "deletion-backup-verifier-v1"
BUILD_MANIFEST_NAME = "build-manifest.json"

_ARTIFACT_FILENAMES = {
    "mysql_target_backup": "mysql-target-backup.zip",
    "replay_artifact_backup": "replay-artifact-backup.zip",
}
_BUILD_MANIFEST_KEYS = {
    "contract_version",
    "request_id",
    "dry_run_plan_id",
    "plan_fingerprint_sha256",
    "source_fingerprint_sha256",
    "build_id",
    "confirmation_text_sha256",
    "target",
    "artifacts",
    "built_by",
    "build_note",
    "built_at_kst",
    "safety",
    "manifest_fingerprint_sha256",
}
_ARTIFACT_RECORD_KEYS = {
    "prerequisite_key",
    "path",
    "sha256",
    "size_bytes",
    "covered_row_count",
    "covered_file_count",
    "covered_file_bytes",
}
_MYSQL_MANIFEST_KEYS = {
    "format_version",
    "builder_contract_version",
    "request_id",
    "dry_run_plan_id",
    "plan_fingerprint_sha256",
    "source_fingerprint_sha256",
    "built_at_kst",
    "row_count",
    "tables",
    "schema_creation_included",
    "restore_supported_by_current_application",
}
_MYSQL_TABLE_KEYS = {
    "sequence",
    "table",
    "entry",
    "row_count",
    "content_bytes",
    "content_sha256",
    "selector",
}
_REPLAY_MANIFEST_KEYS = {
    "format_version",
    "builder_contract_version",
    "dry_run_plan_id",
    "plan_fingerprint_sha256",
    "source_fingerprint_sha256",
    "built_at_kst",
    "file_count",
    "source_file_bytes",
    "files",
    "restore_supported_by_current_application",
}
_REPLAY_FILE_KEYS = {
    "sequence",
    "record_id",
    "artifact_type",
    "match_id",
    "source_relative_path",
    "entry",
    "size_bytes",
    "sha256",
}
_ALLOWED_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
_BUILD_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_BUILDER_EVIDENCE_NOTE_PATTERN = re.compile(
    rf"^builder={re.escape(BACKUP_BUILDER_CONTRACT_VERSION)}; "
    r"build_id=(?P<build_id>[0-9a-f]{32}); "
    r"opt_in_sha256=(?P<opt_in>[0-9a-f]{64}); "
    r"manifest_sha256=(?P<manifest>[0-9a-f]{64})"
    r"(?:; note=(?P<note>[\s\S]*))?$"
)
_COPY_CHUNK_BYTES = 1024 * 1024
_MAX_BUILD_MANIFEST_BYTES = 4 * 1024 * 1024
_MAX_INTERNAL_MANIFEST_BYTES = 8 * 1024 * 1024
_MAX_JSONL_LINE_BYTES = 64 * 1024 * 1024
_MAX_ZIP_ENTRY_BYTES = 128 * 1024 * 1024 * 1024
_MAX_ZIP_TOTAL_BYTES = 256 * 1024 * 1024 * 1024
_MAX_COMPRESSION_RATIO = 2000
_MAX_TYPED_VALUE_DEPTH = 100


class DataDeletionBackupVerifierError(RuntimeError):
    """Raised when a backup verification request is unsafe or invalid."""


class _VerificationBlocked(RuntimeError):
    pass


class _DuplicateJsonKey(ValueError):
    pass


@dataclass(frozen=True)
class _BackupBuildEvidenceAnchor:
    build_id: str
    manifest_sha256: str
    confirmation_text_sha256: str
    build_note: str | None
    recorded_by: str
    recorded_at_kst: datetime
    manifest_path: Path
    evidence_by_key: dict[str, DataDeletionBackupEvidence]
    evidence_set_fingerprint_sha256: str

    @property
    def record_ids(self) -> dict[str, int]:
        return {
            key: item.id for key, item in sorted(self.evidence_by_key.items())
        }


@dataclass(frozen=True)
class DataDeletionBackupVerificationRun:
    id: int
    request_id: int
    dry_run_plan_id: int
    contract_version: str
    plan_fingerprint_sha256: str
    evidence_set_fingerprint_sha256: str
    evidence_record_ids: dict[str, int]
    build_id: str | None
    manifest_path: str
    expected_manifest_sha256: str
    observed_manifest_sha256: str | None
    manifest_fingerprint_sha256: str | None
    result_fingerprint_sha256: str
    result_status: str
    result_json: dict[str, Any]
    artifact_count: int
    verified_artifact_count: int
    check_count: int
    passed_check_count: int
    blocker_count: int
    verified_by: str
    verification_note: str | None
    verified_at_kst: datetime

    def to_summary_record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "request_id": self.request_id,
            "dry_run_plan_id": self.dry_run_plan_id,
            "contract_version": self.contract_version,
            "plan_fingerprint_sha256": self.plan_fingerprint_sha256,
            "evidence_set_fingerprint_sha256": self.evidence_set_fingerprint_sha256,
            "evidence_record_ids": dict(self.evidence_record_ids),
            "build_id": self.build_id,
            "manifest_path": self.manifest_path,
            "expected_manifest_sha256": self.expected_manifest_sha256,
            "observed_manifest_sha256": self.observed_manifest_sha256,
            "manifest_fingerprint_sha256": self.manifest_fingerprint_sha256,
            "result_fingerprint_sha256": self.result_fingerprint_sha256,
            "result_status": self.result_status,
            "artifact_count": self.artifact_count,
            "verified_artifact_count": self.verified_artifact_count,
            "check_count": self.check_count,
            "passed_check_count": self.passed_check_count,
            "blocker_count": self.blocker_count,
            "verified_by": self.verified_by,
            "verification_note": self.verification_note,
            "verified_at_kst": to_kst(self.verified_at_kst).isoformat(),
            "immutable": True,
            "restore_test_performed": False,
            "quarantine_performed": False,
            "deletion_performed": False,
            "execution_enabled": False,
            "execution_ready": False,
        }

    def to_record(self) -> dict[str, Any]:
        return {**self.to_summary_record(), "result_json": self.result_json}


@dataclass(frozen=True)
class RevalidatedBackupBuild:
    verification_run: DataDeletionBackupVerificationRun
    plan: DataDeletionDryRunPlan
    manifest_path: Path
    manifest: dict[str, Any]
    artifact_paths: dict[str, Path]
    current_result_fingerprint_sha256: str


class DataDeletionBackupVerifierService:
    def __init__(
        self,
        connection: Any,
        *,
        backup_service: DataDeletionBackupService,
        backup_root: Path,
        raw_data_dir: Path,
        replay_data_dir: Path,
    ) -> None:
        self.connection = connection
        self.backup_service = backup_service
        self.backup_root = backup_root.expanduser().resolve(strict=False)
        self.raw_data_dir = raw_data_dir.expanduser().resolve(strict=False)
        self.replay_data_dir = replay_data_dir.expanduser().resolve(strict=False)

    def verification_state(self, request: DataDeletionRequest) -> dict[str, Any]:
        plans = self.backup_service.dry_run_service.list_plans(request.id, limit=1)
        plan = plans[0] if plans else None
        blockers: list[str] = []
        if request.status != "approved":
            blockers.append(f"request status must be approved, not {request.status}")
        if plan is None:
            blockers.append("latest confirmed dry-run plan is required")
        elif not hmac.compare_digest(
            plan.plan_fingerprint_sha256,
            fingerprint_dry_run_plan(plan.plan_json),
        ):
            blockers.append("latest dry-run plan fingerprint is invalid")
        if not self.backup_root.exists() or not self.backup_root.is_dir():
            blockers.append("configured backup root must exist and be a directory")
        overlap = overlapping_source_root(
            self.backup_root,
            self.raw_data_dir,
            self.replay_data_dir,
        )
        if overlap is not None:
            blockers.append(f"backup root overlaps source storage: {overlap}")

        anchors: list[_BackupBuildEvidenceAnchor] = []
        if plan is not None:
            evidence_records = self.backup_service.list_evidence(plan.id, limit=500)
            anchors = _evidence_anchors(request, plan, evidence_records)
        candidates = (
            self._discover_candidates(request, plan, anchors)
            if plan is not None
            else []
        )
        selectable_count = sum(bool(item.get("selectable")) for item in candidates)
        if plan is not None and not selectable_count:
            blockers.append("no fingerprint-bound backup build manifest is available")
        history = self.list_runs(plan.id, limit=50) if plan is not None else []
        return {
            "request_id": request.id,
            "request_status": request.status,
            "contract_version": BACKUP_VERIFIER_CONTRACT_VERSION,
            "backup_root": str(self.backup_root),
            "latest_plan_id": plan.id if plan is not None else None,
            "plan_fingerprint_sha256": plan.plan_fingerprint_sha256 if plan is not None else None,
            "candidates": candidates,
            "selectable_candidate_count": selectable_count,
            "trusted_evidence_set_count": len(anchors),
            "latest_verification": history[0].to_record() if history else None,
            "verification_history": [item.to_summary_record() for item in history],
            "verification_allowed": not blockers,
            "verification_blockers": blockers,
            "reads_backup_files": True,
            "writes_backup_files": False,
            "appends_verification_audit_row": True,
            "restore_test_performed": False,
            "quarantine_performed": False,
            "deletion_performed": False,
            "execution_enabled": False,
            "execution_ready": False,
        }

    def verify(
        self,
        request: DataDeletionRequest,
        *,
        dry_run_plan_id: int,
        manifest_path: str,
        expected_manifest_sha256: str,
        actor_id: str,
        note: str | None = None,
        reference_kst: datetime | None = None,
    ) -> DataDeletionBackupVerificationRun:
        actor_id = _required_text(actor_id, "actor_id", 191)
        note = _optional_text(note, "note", 1000)
        expected_sha256 = _fingerprint(expected_manifest_sha256, "expected_manifest_sha256")
        if request.status != "approved":
            raise DataDeletionBackupVerifierError("request must remain approved.")
        plan = self.backup_service.require_latest_plan(request, dry_run_plan_id)
        _assert_plan_integrity(plan, request)
        overlap = overlapping_source_root(
            self.backup_root,
            self.raw_data_dir,
            self.replay_data_dir,
        )
        if overlap is not None:
            raise DataDeletionBackupVerifierError(
                f"backup root overlaps source storage: {overlap}."
            )
        safe_manifest = _resolve_manifest_path(
            self.backup_root,
            request,
            plan,
            manifest_path,
        )
        evidence_records = self.backup_service.list_evidence(plan.id, limit=500)
        anchors = _evidence_anchors(request, plan, evidence_records)
        anchor = _select_evidence_anchor(safe_manifest, anchors)
        if anchor is None:
            raise DataDeletionBackupVerifierError(
                "backup build is not bound to an intact builder evidence set."
            )
        verified_at = to_kst(reference_kst or now_kst())
        verifier = _BuildArtifactVerifier(
            request=request,
            plan=plan,
            manifest_path=safe_manifest,
            expected_manifest_sha256=expected_sha256,
            evidence_anchor=anchor,
            verified_at_kst=verified_at,
        )
        result = verifier.run()
        result_fingerprint = _canonical_sha256(result)
        run = self._record_run(
            request,
            plan,
            result=result,
            result_fingerprint=result_fingerprint,
            evidence_anchor=anchor,
            actor_id=actor_id,
            note=note,
            verified_at_kst=verified_at,
        )
        return run

    def get_run(self, verification_id: int) -> DataDeletionBackupVerificationRun:
        verification_id = _positive_int(verification_id, "verification_id")
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM data_deletion_backup_verification_runs WHERE id = %s",
                (verification_id,),
            )
            row = cursor.fetchone()
        if not row:
            raise DataDeletionBackupVerifierError(
                f"backup verification {verification_id} was not found."
            )
        return _verification_from_row(row)

    def list_runs(
        self,
        dry_run_plan_id: int,
        *,
        limit: int = 50,
    ) -> list[DataDeletionBackupVerificationRun]:
        dry_run_plan_id = _positive_int(dry_run_plan_id, "dry_run_plan_id")
        if not 1 <= int(limit) <= 100:
            raise DataDeletionBackupVerifierError(
                "verification history limit must be between 1 and 100."
            )
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM data_deletion_backup_verification_runs
                WHERE dry_run_plan_id = %s
                ORDER BY verified_at_kst DESC, id DESC
                LIMIT %s
                """,
                (dry_run_plan_id, int(limit)),
            )
            rows = cursor.fetchall()
        return [_verification_from_row(row) for row in rows]

    def revalidate_passed_run(
        self,
        request: DataDeletionRequest,
        verification_id: int,
        *,
        reference_kst: datetime | None = None,
    ) -> RevalidatedBackupBuild:
        run = self.get_run(verification_id)
        if run.request_id != request.id:
            raise DataDeletionBackupVerifierError(
                "backup verification belongs to another deletion request."
            )
        if run.contract_version != BACKUP_VERIFIER_CONTRACT_VERSION:
            raise DataDeletionBackupVerifierError(
                "backup verification contract version is unsupported."
            )
        if run.result_status != "passed":
            raise DataDeletionBackupVerifierError(
                "backup verification must have passed before restore revalidation."
            )
        if request.status != "approved":
            raise DataDeletionBackupVerifierError("request must remain approved.")
        plan = self.backup_service.require_latest_plan(
            request,
            run.dry_run_plan_id,
        )
        _assert_plan_integrity(plan, request)
        if not hmac.compare_digest(
            run.plan_fingerprint_sha256,
            plan.plan_fingerprint_sha256,
        ):
            raise DataDeletionBackupVerifierError(
                "backup verification plan fingerprint is stale."
            )
        safe_manifest = _resolve_manifest_path(
            self.backup_root,
            request,
            plan,
            run.manifest_path,
        )
        evidence_records = self.backup_service.list_evidence(plan.id, limit=500)
        anchors = _evidence_anchors(request, plan, evidence_records)
        anchor = _select_evidence_anchor(
            safe_manifest,
            anchors,
            manifest_sha256=run.expected_manifest_sha256,
        )
        if anchor is None:
            raise DataDeletionBackupVerifierError(
                "backup verification no longer has an intact builder evidence set."
            )
        if (
            anchor.record_ids != run.evidence_record_ids
            or not hmac.compare_digest(
                anchor.evidence_set_fingerprint_sha256,
                run.evidence_set_fingerprint_sha256,
            )
        ):
            raise DataDeletionBackupVerifierError(
                "backup verification builder evidence binding is stale."
            )
        verifier = _BuildArtifactVerifier(
            request=request,
            plan=plan,
            manifest_path=safe_manifest,
            expected_manifest_sha256=run.expected_manifest_sha256,
            evidence_anchor=anchor,
            verified_at_kst=to_kst(reference_kst or now_kst()),
        )
        current_result = verifier.run()
        if current_result.get("verification_status") != "passed":
            blockers = current_result.get("verification_blockers") or []
            message = "; ".join(str(item) for item in blockers) or "unknown blocker"
            raise DataDeletionBackupVerifierError(
                f"backup artifacts no longer pass read-only verification: {message}"
            )
        if (
            current_result.get("build_id") != run.build_id
            or current_result.get("observed_manifest_sha256")
            != run.expected_manifest_sha256
            or current_result.get("manifest_fingerprint_sha256")
            != run.manifest_fingerprint_sha256
            or current_result.get("evidence_set", {}).get("record_ids")
            != run.evidence_record_ids
        ):
            raise DataDeletionBackupVerifierError(
                "current backup verification bindings differ from the immutable passed run."
            )
        manifest_body = _read_limited_file(safe_manifest, _MAX_BUILD_MANIFEST_BYTES)
        if not hmac.compare_digest(
            hashlib.sha256(manifest_body).hexdigest(),
            run.expected_manifest_sha256,
        ):
            raise DataDeletionBackupVerifierError(
                "build manifest changed after read-only revalidation."
            )
        manifest = _json_object_bytes(manifest_body, "build manifest")
        artifact_records = manifest.get("artifacts")
        if not isinstance(artifact_records, list):
            raise DataDeletionBackupVerifierError(
                "build manifest artifact records are unavailable."
            )
        artifact_paths: dict[str, Path] = {}
        for value in artifact_records:
            if not isinstance(value, dict):
                raise DataDeletionBackupVerifierError(
                    "build manifest contains an invalid artifact record."
                )
            key = str(value.get("prerequisite_key") or "")
            expected_name = _ARTIFACT_FILENAMES.get(key)
            if expected_name is None or value.get("path") != expected_name:
                raise DataDeletionBackupVerifierError(
                    "build manifest contains an unsupported artifact record."
                )
            artifact_path = (safe_manifest.parent / expected_name).resolve(strict=True)
            if (
                artifact_path.parent != safe_manifest.parent
                or not artifact_path.is_file()
                or artifact_path.is_symlink()
                or key in artifact_paths
            ):
                raise DataDeletionBackupVerifierError(
                    "revalidated artifact path is unsafe."
                )
            artifact_paths[key] = artifact_path
        if set(artifact_paths) != set(run.evidence_record_ids):
            raise DataDeletionBackupVerifierError(
                "revalidated artifact set differs from immutable verification evidence."
            )
        return RevalidatedBackupBuild(
            verification_run=run,
            plan=plan,
            manifest_path=safe_manifest,
            manifest=manifest,
            artifact_paths=artifact_paths,
            current_result_fingerprint_sha256=_canonical_sha256(current_result),
        )

    def _discover_candidates(
        self,
        request: DataDeletionRequest,
        plan: DataDeletionDryRunPlan,
        anchors: list[_BackupBuildEvidenceAnchor],
    ) -> list[dict[str, Any]]:
        plan_root = _expected_plan_root(self.backup_root, request, plan)
        if not plan_root.is_dir():
            return []
        candidates: list[dict[str, Any]] = []
        try:
            build_directories = sorted(
                (
                    item
                    for item in plan_root.iterdir()
                    if item.is_dir() and item.name.startswith("build-")
                ),
                key=lambda item: item.name,
                reverse=True,
            )[:100]
        except OSError as exc:
            return [{
                "manifest_path": None,
                "selectable": False,
                "inspection_error": f"failed to list plan backup directory: {exc}",
            }]
        for directory in build_directories:
            manifest_path = directory / BUILD_MANIFEST_NAME
            try:
                safe_path = _resolve_manifest_path(
                    self.backup_root,
                    request,
                    plan,
                    str(manifest_path),
                )
                body = _read_limited_file(safe_path, _MAX_BUILD_MANIFEST_BYTES)
                manifest = _json_object_bytes(body, "build manifest")
                fingerprint = _manifest_fingerprint(manifest)
                manifest_sha256 = hashlib.sha256(body).hexdigest()
                anchor = _select_evidence_anchor(
                    safe_path,
                    anchors,
                    manifest_sha256=manifest_sha256,
                )
                evidence_match = (
                    anchor is not None
                    and hmac.compare_digest(anchor.manifest_sha256, manifest_sha256)
                    and manifest.get("build_id") == anchor.build_id
                )
                bindings_match = (
                    manifest.get("contract_version") == BACKUP_BUILDER_CONTRACT_VERSION
                    and int(manifest.get("request_id") or 0) == request.id
                    and int(manifest.get("dry_run_plan_id") or 0) == plan.id
                    and hmac.compare_digest(
                        str(manifest.get("plan_fingerprint_sha256") or ""),
                        plan.plan_fingerprint_sha256,
                    )
                    and hmac.compare_digest(
                        str(manifest.get("source_fingerprint_sha256") or ""),
                        plan.source_fingerprint_sha256,
                    )
                    and hmac.compare_digest(
                        str(manifest.get("manifest_fingerprint_sha256") or ""),
                        fingerprint,
                    )
                    and evidence_match
                )
                candidates.append(
                    {
                        "manifest_path": str(safe_path),
                        "manifest_sha256": manifest_sha256,
                        "evidence_set_fingerprint_sha256": (
                            anchor.evidence_set_fingerprint_sha256 if anchor else None
                        ),
                        "evidence_record_ids": anchor.record_ids if anchor else {},
                        "manifest_fingerprint_sha256": manifest.get(
                            "manifest_fingerprint_sha256"
                        ),
                        "build_id": manifest.get("build_id"),
                        "built_at_kst": manifest.get("built_at_kst"),
                        "artifact_keys": [
                            str(item.get("prerequisite_key") or "")
                            for item in manifest.get("artifacts", [])
                            if isinstance(item, dict)
                        ],
                        "selectable": bool(bindings_match),
                        "artifact_contents_verified": False,
                        "inspection_error": None if bindings_match else "manifest binding mismatch",
                    }
                )
            except (
                DataDeletionBackupVerifierError,
                OSError,
                TypeError,
                ValueError,
                RuntimeError,
            ) as exc:
                candidates.append(
                    {
                        "manifest_path": str(manifest_path),
                        "selectable": False,
                        "artifact_contents_verified": False,
                        "inspection_error": str(exc),
                    }
                )
        return candidates

    def _record_run(
        self,
        request: DataDeletionRequest,
        plan: DataDeletionDryRunPlan,
        *,
        result: dict[str, Any],
        result_fingerprint: str,
        evidence_anchor: _BackupBuildEvidenceAnchor,
        actor_id: str,
        note: str | None,
        verified_at_kst: datetime,
    ) -> DataDeletionBackupVerificationRun:
        checks = list(result["checks"])
        passed_checks = sum(item.get("status") == "passed" for item in checks)
        blockers = list(result["verification_blockers"])
        artifact_results = list(result["artifacts"])
        verified_artifacts = sum(item.get("status") == "passed" for item in artifact_results)
        timestamp = to_kst(verified_at_kst).replace(tzinfo=None)
        _begin(self.connection)
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT status FROM data_deletion_requests WHERE id = %s FOR UPDATE",
                    (request.id,),
                )
                request_row = cursor.fetchone()
                if not request_row or str(request_row.get("status")) != "approved":
                    raise DataDeletionBackupVerifierError(
                        "request is no longer approved."
                    )
                cursor.execute(
                    """
                    SELECT id, plan_fingerprint_sha256
                    FROM data_deletion_dry_run_plans
                    WHERE request_id = %s
                    ORDER BY generated_at_kst DESC, id DESC
                    LIMIT 1
                    FOR UPDATE
                    """,
                    (request.id,),
                )
                plan_row = cursor.fetchone()
                if (
                    not plan_row
                    or int(plan_row["id"]) != plan.id
                    or not hmac.compare_digest(
                        str(plan_row["plan_fingerprint_sha256"]),
                        plan.plan_fingerprint_sha256,
                    )
                ):
                    raise DataDeletionBackupVerifierError(
                        "latest dry-run plan changed during backup verification."
                    )
                evidence_ids = sorted(evidence_anchor.record_ids.values())
                placeholders = ", ".join(["%s"] * len(evidence_ids))
                cursor.execute(
                    f"""
                    SELECT id, prerequisite_key, evidence_fingerprint_sha256
                    FROM data_deletion_backup_evidence
                    WHERE dry_run_plan_id = %s AND id IN ({placeholders})
                    FOR UPDATE
                    """,
                    (plan.id, *evidence_ids),
                )
                locked_evidence = {int(row["id"]): row for row in cursor.fetchall()}
                expected_evidence = {
                    item.id: item for item in evidence_anchor.evidence_by_key.values()
                }
                if set(locked_evidence) != set(expected_evidence):
                    raise DataDeletionBackupVerifierError(
                        "builder evidence set changed during backup verification."
                    )
                for evidence_id, item in expected_evidence.items():
                    row = locked_evidence[evidence_id]
                    if (
                        str(row["prerequisite_key"]) != item.prerequisite_key
                        or not hmac.compare_digest(
                            str(row["evidence_fingerprint_sha256"]),
                            item.evidence_fingerprint_sha256,
                        )
                    ):
                        raise DataDeletionBackupVerifierError(
                            "builder evidence set changed during backup verification."
                        )
                cursor.execute(
                    """
                    INSERT INTO data_deletion_backup_verification_runs (
                        request_id,
                        dry_run_plan_id,
                        contract_version,
                        plan_fingerprint_sha256,
                        evidence_set_fingerprint_sha256,
                        evidence_record_ids_json,
                        build_id,
                        manifest_path,
                        expected_manifest_sha256,
                        observed_manifest_sha256,
                        manifest_fingerprint_sha256,
                        result_fingerprint_sha256,
                        result_status,
                        result_json,
                        artifact_count,
                        verified_artifact_count,
                        check_count,
                        passed_check_count,
                        blocker_count,
                        verified_by,
                        verification_note,
                        verified_at_kst
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                              %s, %s)
                    """,
                    (
                        request.id,
                        plan.id,
                        BACKUP_VERIFIER_CONTRACT_VERSION,
                        plan.plan_fingerprint_sha256,
                        evidence_anchor.evidence_set_fingerprint_sha256,
                        _json_dump(evidence_anchor.record_ids),
                        result.get("build_id"),
                        str(result["manifest_path"]),
                        str(result["expected_manifest_sha256"]),
                        result.get("observed_manifest_sha256"),
                        result.get("manifest_fingerprint_sha256"),
                        result_fingerprint,
                        str(result["verification_status"]),
                        _json_dump(result),
                        len(artifact_results),
                        verified_artifacts,
                        len(checks),
                        passed_checks,
                        len(blockers),
                        actor_id,
                        note,
                        timestamp,
                    ),
                )
                verification_id = int(cursor.lastrowid)
            _commit(self.connection)
        except Exception:
            _rollback(self.connection)
            raise
        return DataDeletionBackupVerificationRun(
            id=verification_id,
            request_id=request.id,
            dry_run_plan_id=plan.id,
            contract_version=BACKUP_VERIFIER_CONTRACT_VERSION,
            plan_fingerprint_sha256=plan.plan_fingerprint_sha256,
            evidence_set_fingerprint_sha256=(
                evidence_anchor.evidence_set_fingerprint_sha256
            ),
            evidence_record_ids=evidence_anchor.record_ids,
            build_id=_optional_text(result.get("build_id"), "build_id", 64),
            manifest_path=str(result["manifest_path"]),
            expected_manifest_sha256=str(result["expected_manifest_sha256"]),
            observed_manifest_sha256=_optional_fingerprint(
                result.get("observed_manifest_sha256")
            ),
            manifest_fingerprint_sha256=_optional_fingerprint(
                result.get("manifest_fingerprint_sha256")
            ),
            result_fingerprint_sha256=result_fingerprint,
            result_status=str(result["verification_status"]),
            result_json=result,
            artifact_count=len(artifact_results),
            verified_artifact_count=verified_artifacts,
            check_count=len(checks),
            passed_check_count=passed_checks,
            blocker_count=len(blockers),
            verified_by=actor_id,
            verification_note=note,
            verified_at_kst=verified_at_kst,
        )


class _BuildArtifactVerifier:
    def __init__(
        self,
        *,
        request: DataDeletionRequest,
        plan: DataDeletionDryRunPlan,
        manifest_path: Path,
        expected_manifest_sha256: str,
        evidence_anchor: _BackupBuildEvidenceAnchor,
        verified_at_kst: datetime,
    ) -> None:
        self.request = request
        self.plan = plan
        self.manifest_path = manifest_path
        self.expected_manifest_sha256 = expected_manifest_sha256
        self.evidence_anchor = evidence_anchor
        self.verified_at_kst = verified_at_kst
        self.checks: list[dict[str, Any]] = []
        self.artifacts: list[dict[str, Any]] = []
        self.blockers: list[str] = []
        self.observed_manifest_sha256: str | None = None
        self.manifest_fingerprint_sha256: str | None = None
        self.build_id: str | None = None
        self.verified_file_identities: dict[Path, tuple[int, int, int, int]] = {}

    def run(self) -> dict[str, Any]:
        try:
            self._verify()
        except _VerificationBlocked as exc:
            self.blockers.append(str(exc))
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
            message = f"unexpected artifact read failure: {exc}"
            self.checks.append(_check("artifact_read", False, message))
            self.blockers.append(message)
        status = "passed" if not self.blockers else "blocked"
        return {
            "contract_version": BACKUP_VERIFIER_CONTRACT_VERSION,
            "request_id": self.request.id,
            "dry_run_plan_id": self.plan.id,
            "plan_fingerprint_sha256": self.plan.plan_fingerprint_sha256,
            "source_fingerprint_sha256": self.plan.source_fingerprint_sha256,
            "evidence_set": {
                "fingerprint_sha256": self.evidence_anchor.evidence_set_fingerprint_sha256,
                "record_ids": self.evidence_anchor.record_ids,
                "builder_manifest_sha256": self.evidence_anchor.manifest_sha256,
            },
            "build_id": self.build_id,
            "manifest_path": str(self.manifest_path),
            "expected_manifest_sha256": self.expected_manifest_sha256,
            "observed_manifest_sha256": self.observed_manifest_sha256,
            "manifest_fingerprint_sha256": self.manifest_fingerprint_sha256,
            "verified_at_kst": to_kst(self.verified_at_kst).isoformat(),
            "verification_status": status,
            "checks": self.checks,
            "artifacts": self.artifacts,
            "verification_blockers": self.blockers,
            "safety": {
                "backup_files_opened_read_only": True,
                "backup_files_modified": False,
                "source_rows_modified": False,
                "source_files_modified": False,
                "restore_test_performed": False,
                "quarantine_performed": False,
                "deletion_performed": False,
                "backup_integrity_prerequisite_attested": False,
                "execution_enabled": False,
                "execution_ready": False,
            },
        }

    def _verify(self) -> None:
        manifest_identity = _file_identity(self.manifest_path)
        body = _read_limited_file(self.manifest_path, _MAX_BUILD_MANIFEST_BYTES)
        self.observed_manifest_sha256 = hashlib.sha256(body).hexdigest()
        self._require(
            "build_manifest_sha256",
            hmac.compare_digest(
                self.observed_manifest_sha256,
                self.expected_manifest_sha256,
            ),
            "build manifest SHA-256 differs from the selected candidate",
        )
        self._require(
            "builder_evidence_manifest_sha256",
            hmac.compare_digest(
                self.observed_manifest_sha256,
                self.evidence_anchor.manifest_sha256,
            ),
            "build manifest SHA-256 differs from immutable builder evidence",
        )
        manifest = _json_object_bytes(body, "build manifest")
        self._require_exact_keys("build_manifest_fields", manifest, _BUILD_MANIFEST_KEYS)
        body_without_fingerprint = dict(manifest)
        claimed_fingerprint = _fingerprint(
            body_without_fingerprint.pop("manifest_fingerprint_sha256"),
            "manifest_fingerprint_sha256",
        )
        calculated_fingerprint = _canonical_sha256(body_without_fingerprint)
        self.manifest_fingerprint_sha256 = claimed_fingerprint
        self._require(
            "build_manifest_fingerprint",
            hmac.compare_digest(claimed_fingerprint, calculated_fingerprint),
            "build manifest canonical fingerprint is invalid",
        )
        self._require(
            "build_manifest_contract",
            manifest["contract_version"] == BACKUP_BUILDER_CONTRACT_VERSION,
            "unsupported backup builder contract",
        )
        self._require(
            "build_manifest_binding",
            int(manifest["request_id"]) == self.request.id
            and int(manifest["dry_run_plan_id"]) == self.plan.id
            and hmac.compare_digest(
                _fingerprint(manifest["plan_fingerprint_sha256"], "plan fingerprint"),
                self.plan.plan_fingerprint_sha256,
            )
            and hmac.compare_digest(
                _fingerprint(manifest["source_fingerprint_sha256"], "source fingerprint"),
                self.plan.source_fingerprint_sha256,
            ),
            "build manifest is not bound to the selected request and latest plan",
        )
        self.build_id = _required_text(manifest["build_id"], "build_id", 64)
        self._require(
            "build_id_format",
            bool(_BUILD_ID_PATTERN.fullmatch(self.build_id)),
            "build_id is not a 32-character lowercase hexadecimal identifier",
        )
        confirmation_sha256 = _fingerprint(
            manifest["confirmation_text_sha256"],
            "confirmation_text_sha256",
        )
        built_by = _required_text(manifest["built_by"], "built_by", 191)
        build_note = _optional_text(manifest.get("build_note"), "build_note", 500)
        self._require(
            "builder_evidence_identity",
            self.build_id == self.evidence_anchor.build_id
            and hmac.compare_digest(
                confirmation_sha256,
                self.evidence_anchor.confirmation_text_sha256,
            )
            and built_by == self.evidence_anchor.recorded_by
            and build_note == self.evidence_anchor.build_note,
            "build identity differs from immutable builder evidence",
        )
        target = _object(manifest["target"], "target")
        self._require(
            "build_target_binding",
            set(target) == {"account_id", "shard", "player_name"}
            and target["account_id"] == self.request.account_id
            and target["shard"] == self.request.shard
            and target["player_name"] == self.request.player_name,
            "build target identity differs from the deletion request",
        )
        built_at = _datetime_value(manifest["built_at_kst"], "built_at_kst")
        self._require(
            "build_time",
            to_kst(built_at) >= to_kst(self.plan.generated_at_kst)
            and to_kst(built_at) <= to_kst(self.verified_at_kst),
            "backup build time is outside the plan-to-verification interval",
        )
        self._require(
            "builder_evidence_time",
            to_kst(self.evidence_anchor.recorded_at_kst) >= to_kst(built_at),
            "builder evidence predates the backup build",
        )
        safety = _object(manifest["safety"], "safety")
        expected_safety = {
            "source_rows_modified": False,
            "source_files_modified": False,
            "restore_test_performed": False,
            "quarantine_performed": False,
            "deletion_performed": False,
            "execution_enabled": False,
            "execution_ready": False,
        }
        self._require(
            "build_safety_contract",
            safety == expected_safety,
            "build safety flags differ from the non-executing contract",
        )
        records = manifest["artifacts"]
        if not isinstance(records, list):
            self._block("artifact_manifest", "build artifacts must be a JSON array")
        required_keys = artifact_prerequisite_keys(self.plan)
        self._require(
            "artifact_count",
            len(records) == len(required_keys),
            "declared artifact count differs from the latest plan",
        )
        record_map: dict[str, dict[str, Any]] = {}
        for value in records:
            record = _object(value, "artifact record")
            self._require_exact_keys("artifact_record_fields", record, _ARTIFACT_RECORD_KEYS)
            key = str(record["prerequisite_key"])
            if key in record_map:
                self._block("artifact_keys", f"duplicate artifact key: {key}")
            record_map[key] = record
        self._require(
            "artifact_keys",
            tuple(key for key in _ARTIFACT_FILENAMES if key in record_map) == required_keys
            and set(record_map) == set(required_keys),
            "declared artifact keys differ from the latest plan",
        )
        declared_names = {BUILD_MANIFEST_NAME}
        for key in required_keys:
            expected_name = _ARTIFACT_FILENAMES[key]
            self._require(
                f"{key}_filename",
                record_map[key]["path"] == expected_name,
                f"{key} uses an unexpected artifact filename",
            )
            self._require(
                f"{key}_builder_evidence",
                _artifact_matches_evidence(
                    record_map[key],
                    key,
                    self.evidence_anchor,
                    artifact_path=self.manifest_path.parent / expected_name,
                    built_at_kst=built_at,
                    built_by=built_by,
                ),
                f"{key} differs from immutable builder evidence",
            )
            declared_names.add(expected_name)
        actual_names = _directory_entry_names(self.manifest_path.parent)
        self._require(
            "build_directory_entries",
            actual_names == declared_names,
            "build directory contains missing or undeclared entries",
        )
        self._require(
            "build_manifest_stability",
            manifest_identity == _file_identity(self.manifest_path),
            "build manifest changed while it was being verified",
        )

        for key in required_keys:
            record = record_map[key]
            artifact_path = self.manifest_path.parent / str(record["path"])
            try:
                if key == "mysql_target_backup":
                    result = self._verify_mysql_artifact(artifact_path, record, manifest)
                else:
                    result = self._verify_replay_artifact(artifact_path, record, manifest)
                self.artifacts.append(result)
                self.verified_file_identities[artifact_path] = _file_identity(artifact_path)
            except _VerificationBlocked as exc:
                self.artifacts.append(
                    {
                        "prerequisite_key": key,
                        "path": str(artifact_path),
                        "status": "blocked",
                        "message": str(exc),
                    }
                )
                self.checks.append(_check(f"{key}_contents", False, str(exc)))
                raise
            except (DataDeletionBackupVerifierError, OSError, ValueError, RuntimeError) as exc:
                message = str(exc)
                self.artifacts.append(
                    {
                        "prerequisite_key": key,
                        "path": str(artifact_path),
                        "status": "blocked",
                        "message": message,
                    }
                )
                self.checks.append(_check(f"{key}_contents", False, message))
                raise _VerificationBlocked(message) from exc
        self._require(
            "final_build_directory_entries",
            _directory_entry_names(self.manifest_path.parent) == declared_names,
            "build directory changed during artifact verification",
        )
        self._require(
            "final_build_manifest_stability",
            manifest_identity == _file_identity(self.manifest_path)
            and hmac.compare_digest(
                hashlib.sha256(
                    _read_limited_file(
                        self.manifest_path,
                        _MAX_BUILD_MANIFEST_BYTES,
                    )
                ).hexdigest(),
                self.observed_manifest_sha256,
            ),
            "build manifest changed during artifact verification",
        )
        self._require(
            "final_artifact_stability",
            all(
                identity == _file_identity(path)
                for path, identity in self.verified_file_identities.items()
            ),
            "one or more backup artifacts changed after content verification",
        )
        self.checks.append(
            _check(
                "all_artifact_contents",
                True,
                "all declared ZIP entries, payload hashes, and counts are valid",
            )
        )

    def _verify_mysql_artifact(
        self,
        path: Path,
        record: dict[str, Any],
        build_manifest: dict[str, Any],
    ) -> dict[str, Any]:
        _validate_mysql_top_record(record, self.plan)
        identity = _file_identity(path)
        with path.open("rb") as source:
            digest = _sha256_stream(source)
            self._require(
                "mysql_artifact_sha256",
                hmac.compare_digest(digest, _fingerprint(record["sha256"], "artifact sha256")),
                "MySQL artifact whole-file SHA-256 differs from the build manifest",
            )
            self._require(
                "mysql_artifact_size",
                identity[2] == _nonnegative_int(record["size_bytes"], "artifact size"),
                "MySQL artifact size differs from the build manifest",
            )
            source.seek(0)
            with zipfile.ZipFile(source, mode="r") as archive:
                infos = _zip_catalog(archive)
                inner = _read_zip_json(
                    archive,
                    infos,
                    "manifest.json",
                    _MAX_INTERNAL_MANIFEST_BYTES,
                    "MySQL manifest",
                )
                self._require_exact_keys("mysql_manifest_fields", inner, _MYSQL_MANIFEST_KEYS)
                self._require(
                    "mysql_manifest_contract",
                    inner["format_version"] == MYSQL_BACKUP_FORMAT_VERSION
                    and inner["builder_contract_version"] == BACKUP_BUILDER_CONTRACT_VERSION,
                    "unsupported MySQL backup format",
                )
                self._require(
                    "mysql_manifest_binding",
                    int(inner["request_id"]) == self.request.id
                    and int(inner["dry_run_plan_id"]) == self.plan.id
                    and inner["plan_fingerprint_sha256"] == self.plan.plan_fingerprint_sha256
                    and inner["source_fingerprint_sha256"] == self.plan.source_fingerprint_sha256
                    and inner["built_at_kst"] == build_manifest["built_at_kst"],
                    "MySQL manifest binding differs from the build manifest",
                )
                self._require(
                    "mysql_restore_flags",
                    inner["schema_creation_included"] is False
                    and inner["restore_supported_by_current_application"] is False,
                    "MySQL manifest claims unsupported schema creation or restore support",
                )
                table_records = inner["tables"]
                if not isinstance(table_records, list):
                    self._block("mysql_tables", "MySQL tables must be a JSON array")
                operations = _database_operations(self.plan, self.request)
                self._require(
                    "mysql_table_count",
                    len(table_records) == len(operations),
                    "MySQL table count differs from the dry-run plan",
                )
                expected_entries = {"manifest.json"}
                total_rows = 0
                total_content_bytes = 0
                for operation, value in zip(operations, table_records, strict=True):
                    table_record = _object(value, "MySQL table record")
                    self._require_exact_keys(
                        "mysql_table_record_fields",
                        table_record,
                        _MYSQL_TABLE_KEYS,
                    )
                    sequence = _positive_int(operation.get("sequence"), "sequence")
                    table = str(operation.get("table") or "")
                    expected_entry = f"tables/{sequence:03d}-{table}.jsonl"
                    expected_rows = _nonnegative_int(
                        operation.get("estimated_rows"),
                        "estimated_rows",
                    )
                    self._require(
                        f"mysql_table_{sequence}_binding",
                        int(table_record["sequence"]) == sequence
                        and table_record["table"] == table
                        and table_record["entry"] == expected_entry
                        and table_record["selector"] == operation["selector"]
                        and int(table_record["row_count"]) == expected_rows,
                        f"MySQL table manifest differs from plan operation {sequence}",
                    )
                    expected_entries.add(expected_entry)
                    content_bytes = _nonnegative_int(
                        table_record["content_bytes"],
                        "content_bytes",
                    )
                    content_sha = _fingerprint(
                        table_record["content_sha256"],
                        "content_sha256",
                    )
                    info = _zip_info(infos, expected_entry)
                    _validate_zip_entry_size(info, content_bytes)
                    row_count, observed_bytes, observed_sha = _verify_jsonl_entry(
                        archive,
                        info,
                    )
                    self._require(
                        f"mysql_table_{sequence}_content",
                        row_count == expected_rows
                        and observed_bytes == content_bytes
                        and hmac.compare_digest(observed_sha, content_sha),
                        f"MySQL JSONL content differs from table manifest for {table}",
                    )
                    total_rows += row_count
                    total_content_bytes += observed_bytes
                self._require(
                    "mysql_zip_entries",
                    set(infos) == expected_entries,
                    "MySQL ZIP contains missing or undeclared entries",
                )
                expected_total = _nonnegative_int(
                    self.plan.plan_json.get("metrics", {}).get("candidate_row_count"),
                    "candidate_row_count",
                )
                self._require(
                    "mysql_totals",
                    total_rows == expected_total
                    and int(inner["row_count"]) == expected_total
                    and int(record["covered_row_count"]) == expected_total,
                    "MySQL row totals differ from the plan or build manifest",
                )
        self._require(
            "mysql_artifact_stability",
            identity == _file_identity(path),
            "MySQL artifact changed while it was being verified",
        )
        return {
            "prerequisite_key": "mysql_target_backup",
            "path": str(path),
            "status": "passed",
            "sha256": digest,
            "size_bytes": identity[2],
            "row_count": total_rows,
            "jsonl_content_bytes": total_content_bytes,
            "zip_entry_count": len(infos),
        }

    def _verify_replay_artifact(
        self,
        path: Path,
        record: dict[str, Any],
        build_manifest: dict[str, Any],
    ) -> dict[str, Any]:
        _validate_replay_top_record(record, self.plan)
        identity = _file_identity(path)
        with path.open("rb") as source:
            digest = _sha256_stream(source)
            self._require(
                "replay_artifact_sha256",
                hmac.compare_digest(digest, _fingerprint(record["sha256"], "artifact sha256")),
                "replay artifact whole-file SHA-256 differs from the build manifest",
            )
            self._require(
                "replay_artifact_size",
                identity[2] == _nonnegative_int(record["size_bytes"], "artifact size"),
                "replay artifact size differs from the build manifest",
            )
            source.seek(0)
            with zipfile.ZipFile(source, mode="r") as archive:
                infos = _zip_catalog(archive)
                inner = _read_zip_json(
                    archive,
                    infos,
                    "manifest.json",
                    _MAX_INTERNAL_MANIFEST_BYTES,
                    "replay manifest",
                )
                self._require_exact_keys("replay_manifest_fields", inner, _REPLAY_MANIFEST_KEYS)
                self._require(
                    "replay_manifest_contract",
                    inner["format_version"] == REPLAY_BACKUP_FORMAT_VERSION
                    and inner["builder_contract_version"] == BACKUP_BUILDER_CONTRACT_VERSION,
                    "unsupported replay backup format",
                )
                self._require(
                    "replay_manifest_binding",
                    int(inner["dry_run_plan_id"]) == self.plan.id
                    and inner["plan_fingerprint_sha256"] == self.plan.plan_fingerprint_sha256
                    and inner["source_fingerprint_sha256"] == self.plan.source_fingerprint_sha256
                    and inner["built_at_kst"] == build_manifest["built_at_kst"],
                    "replay manifest binding differs from the build manifest",
                )
                self._require(
                    "replay_restore_flag",
                    inner["restore_supported_by_current_application"] is False,
                    "replay manifest claims unsupported restore support",
                )
                file_records = inner["files"]
                if not isinstance(file_records, list):
                    self._block("replay_files", "replay files must be a JSON array")
                operations = _replay_operations(self.plan)
                self._require(
                    "replay_file_count",
                    len(file_records) == len(operations),
                    "replay file count differs from the dry-run plan",
                )
                expected_entries = {"manifest.json"}
                total_bytes = 0
                for operation, value in zip(operations, file_records, strict=True):
                    file_record = _object(value, "replay file record")
                    self._require_exact_keys(
                        "replay_file_record_fields",
                        file_record,
                        _REPLAY_FILE_KEYS,
                    )
                    sequence = _positive_int(operation.get("sequence"), "sequence")
                    relative_path = _safe_relative_path(operation.get("relative_path"))
                    expected_entry = f"files/{relative_path.as_posix()}"
                    expected_size = _nonnegative_int(
                        operation.get("declared_size_bytes"),
                        "declared_size_bytes",
                    )
                    expected_sha = _fingerprint(operation.get("sha256"), "sha256")
                    self._require(
                        f"replay_file_{sequence}_binding",
                        int(file_record["sequence"]) == sequence
                        and int(file_record["record_id"]) == int(operation["record_id"])
                        and file_record["artifact_type"] == str(operation.get("artifact_type") or "")
                        and file_record["match_id"] == str(operation.get("match_id") or "")
                        and file_record["source_relative_path"] == relative_path.as_posix()
                        and file_record["entry"] == expected_entry
                        and int(file_record["size_bytes"]) == expected_size
                        and file_record["sha256"] == expected_sha,
                        f"replay file manifest differs from plan operation {sequence}",
                    )
                    expected_entries.add(expected_entry)
                    info = _zip_info(infos, expected_entry)
                    _validate_zip_entry_size(info, expected_size)
                    observed_size, observed_sha = _verify_binary_entry(
                        archive,
                        info,
                        expected_size,
                    )
                    self._require(
                        f"replay_file_{sequence}_content",
                        observed_size == expected_size
                        and hmac.compare_digest(observed_sha, expected_sha),
                        f"replay content differs from plan for {relative_path.as_posix()}",
                    )
                    total_bytes += observed_size
                self._require(
                    "replay_zip_entries",
                    set(infos) == expected_entries,
                    "replay ZIP contains missing or undeclared entries",
                )
                metrics = self.plan.plan_json.get("metrics", {})
                expected_count = _nonnegative_int(
                    metrics.get("candidate_file_count"),
                    "candidate_file_count",
                )
                expected_bytes = _nonnegative_int(
                    metrics.get("candidate_file_bytes"),
                    "candidate_file_bytes",
                )
                self._require(
                    "replay_totals",
                    len(operations) == expected_count
                    and int(inner["file_count"]) == expected_count
                    and int(record["covered_file_count"]) == expected_count
                    and total_bytes == expected_bytes
                    and int(inner["source_file_bytes"]) == expected_bytes
                    and int(record["covered_file_bytes"]) == expected_bytes,
                    "replay totals differ from the plan or build manifest",
                )
        self._require(
            "replay_artifact_stability",
            identity == _file_identity(path),
            "replay artifact changed while it was being verified",
        )
        return {
            "prerequisite_key": "replay_artifact_backup",
            "path": str(path),
            "status": "passed",
            "sha256": digest,
            "size_bytes": identity[2],
            "file_count": len(operations),
            "source_file_bytes": total_bytes,
            "zip_entry_count": len(infos),
        }

    def _require_exact_keys(
        self,
        key: str,
        value: dict[str, Any],
        expected_keys: set[str],
    ) -> None:
        self._require(
            key,
            set(value) == expected_keys,
            f"{key} contains missing or unsupported fields",
        )

    def _require(self, key: str, condition: bool, message: str) -> None:
        self.checks.append(_check(key, condition, message))
        if not condition:
            raise _VerificationBlocked(message)

    def _block(self, key: str, message: str) -> None:
        self._require(key, False, message)



def _evidence_anchors(
    request: DataDeletionRequest,
    plan: DataDeletionDryRunPlan,
    evidence_records: list[DataDeletionBackupEvidence],
) -> list[_BackupBuildEvidenceAnchor]:
    required_keys = artifact_prerequisite_keys(plan)
    grouped: dict[
        tuple[str, str, str, str | None, str, str, str],
        list[DataDeletionBackupEvidence],
    ] = {}
    for evidence in evidence_records:
        try:
            if (
                evidence.contract_version != BACKUP_EVIDENCE_CONTRACT_VERSION
                or evidence.request_id != request.id
                or evidence.dry_run_plan_id != plan.id
                or evidence.prerequisite_key not in required_keys
                or not hmac.compare_digest(
                    evidence.plan_fingerprint_sha256,
                    plan.plan_fingerprint_sha256,
                )
            ):
                continue
            calculated = fingerprint_backup_evidence(
                request.id,
                plan,
                evidence.prerequisite_key,
                evidence.evidence_json,
            )
            if not hmac.compare_digest(
                calculated,
                evidence.evidence_fingerprint_sha256,
            ):
                continue
            match = _BUILDER_EVIDENCE_NOTE_PATTERN.fullmatch(
                str(evidence.evidence_note or "")
            )
            if match is None:
                continue
            payload = evidence.evidence_json
            artifact_path = Path(str(payload.get("artifact_path") or ""))
            if (
                not artifact_path.is_absolute()
                or artifact_path.name != _ARTIFACT_FILENAMES[evidence.prerequisite_key]
            ):
                continue
            _fingerprint(payload.get("artifact_sha256"), "artifact_sha256")
            if _positive_int(payload.get("artifact_size_bytes"), "artifact_size_bytes") <= 0:
                continue
            _datetime_value(payload.get("backup_created_at_kst"), "backup_created_at_kst")
            group_key = (
                match.group("build_id"),
                match.group("manifest"),
                match.group("opt_in"),
                match.group("note"),
                evidence.recorded_by,
                to_kst(evidence.recorded_at_kst).isoformat(),
                str(evidence.evidence_note),
            )
            grouped.setdefault(group_key, []).append(evidence)
        except (DataDeletionBackupVerifierError, TypeError, ValueError, RuntimeError):
            continue

    anchors: list[_BackupBuildEvidenceAnchor] = []
    for group_key, records in grouped.items():
        if len(records) != len(required_keys):
            continue
        by_key = {item.prerequisite_key: item for item in records}
        if set(by_key) != set(required_keys) or len(by_key) != len(records):
            continue
        ids = sorted(item.id for item in records)
        if ids and ids[-1] - ids[0] + 1 != len(ids):
            continue
        artifact_paths = {
            key: Path(str(item.evidence_json["artifact_path"])).resolve(strict=False)
            for key, item in by_key.items()
        }
        parents = {
            os.path.normcase(str(artifact_path.parent))
            for artifact_path in artifact_paths.values()
        }
        if len(parents) != 1:
            continue
        backup_times = {
            to_kst(
                _datetime_value(
                    item.evidence_json["backup_created_at_kst"],
                    "backup_created_at_kst",
                )
            ).isoformat()
            for item in by_key.values()
        }
        if len(backup_times) != 1:
            continue
        build_id, manifest_sha, confirmation_sha, build_note, actor, recorded_iso, _ = group_key
        recorded_at = _datetime_value(recorded_iso, "recorded_at_kst")
        backup_time = _datetime_value(next(iter(backup_times)), "backup_created_at_kst")
        if to_kst(backup_time) > to_kst(recorded_at):
            continue
        manifest_path = next(iter(artifact_paths.values())).parent / BUILD_MANIFEST_NAME
        anchors.append(
            _BackupBuildEvidenceAnchor(
                build_id=build_id,
                manifest_sha256=manifest_sha,
                confirmation_text_sha256=confirmation_sha,
                build_note=build_note,
                recorded_by=actor,
                recorded_at_kst=recorded_at,
                manifest_path=manifest_path,
                evidence_by_key=by_key,
                evidence_set_fingerprint_sha256=fingerprint_evidence_set(plan, by_key),
            )
        )
    anchors.sort(
        key=lambda anchor: max(anchor.record_ids.values(), default=0),
        reverse=True,
    )
    return anchors


def _select_evidence_anchor(
    manifest_path: Path,
    anchors: list[_BackupBuildEvidenceAnchor],
    *,
    manifest_sha256: str | None = None,
) -> _BackupBuildEvidenceAnchor | None:
    normalized_path = os.path.normcase(str(manifest_path.resolve(strict=False)))
    matches = [
        anchor
        for anchor in anchors
        if os.path.normcase(str(anchor.manifest_path.resolve(strict=False)))
        == normalized_path
    ]
    if manifest_sha256 is not None:
        exact = [
            anchor
            for anchor in matches
            if hmac.compare_digest(anchor.manifest_sha256, manifest_sha256)
        ]
        if exact:
            return exact[0]
    return matches[0] if matches else None


def _artifact_matches_evidence(
    record: dict[str, Any],
    key: str,
    anchor: _BackupBuildEvidenceAnchor,
    *,
    artifact_path: Path,
    built_at_kst: datetime,
    built_by: str,
) -> bool:
    evidence = anchor.evidence_by_key.get(key)
    if evidence is None or evidence.recorded_by != built_by:
        return False
    payload = evidence.evidence_json
    try:
        evidence_path = Path(str(payload["artifact_path"])).resolve(strict=False)
        if os.path.normcase(str(evidence_path)) != os.path.normcase(
            str(artifact_path.resolve(strict=False))
        ):
            return False
        if not hmac.compare_digest(
            _fingerprint(payload["artifact_sha256"], "artifact_sha256"),
            _fingerprint(record["sha256"], "artifact sha256"),
        ):
            return False
        if _positive_int(payload["artifact_size_bytes"], "artifact_size_bytes") != int(
            record["size_bytes"]
        ):
            return False
        evidence_time = to_kst(
            _datetime_value(payload["backup_created_at_kst"], "backup_created_at_kst")
        )
        if evidence_time != to_kst(built_at_kst):
            return False
        if key == "mysql_target_backup":
            return int(payload["covered_row_count"]) == int(record["covered_row_count"])
        return (
            int(payload["covered_file_count"]) == int(record["covered_file_count"])
            and int(payload["covered_file_bytes"]) == int(record["covered_file_bytes"])
        )
    except (KeyError, TypeError, ValueError, DataDeletionBackupVerifierError):
        return False

def _resolve_manifest_path(
    backup_root: Path,
    request: DataDeletionRequest,
    plan: DataDeletionDryRunPlan,
    manifest_path: str,
) -> Path:
    text = _required_text(manifest_path, "manifest_path", 1000)
    supplied = Path(text).expanduser()
    if not supplied.is_absolute():
        raise DataDeletionBackupVerifierError("manifest_path must be absolute.")
    try:
        root = backup_root.resolve(strict=True)
        resolved = supplied.resolve(strict=True)
    except OSError as exc:
        raise DataDeletionBackupVerifierError(
            f"backup manifest path cannot be resolved: {exc}"
        ) from exc
    if not root.is_dir():
        raise DataDeletionBackupVerifierError("configured backup root is not a directory.")
    if resolved.name != BUILD_MANIFEST_NAME or not resolved.is_file():
        raise DataDeletionBackupVerifierError(
            f"manifest_path must name an existing {BUILD_MANIFEST_NAME} file."
        )
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise DataDeletionBackupVerifierError(
            "manifest_path escapes the configured backup root."
        ) from exc
    plan_root = _expected_plan_root(root, request, plan).resolve(strict=False)
    if resolved.parent.parent != plan_root or not resolved.parent.name.startswith("build-"):
        raise DataDeletionBackupVerifierError(
            "manifest_path is not a direct build of the selected request and plan."
        )
    return resolved


def _expected_plan_root(
    backup_root: Path,
    request: DataDeletionRequest,
    plan: DataDeletionDryRunPlan,
) -> Path:
    return (
        backup_root
        / "data-deletions"
        / f"request-{request.id}"
        / f"plan-{plan.id}-{plan.plan_fingerprint_sha256[:12]}"
    )


def _validate_mysql_top_record(record: dict[str, Any], plan: DataDeletionDryRunPlan) -> None:
    expected_rows = _nonnegative_int(
        plan.plan_json.get("metrics", {}).get("candidate_row_count"),
        "candidate_row_count",
    )
    if (
        record["covered_row_count"] != expected_rows
        or record["covered_file_count"] is not None
        or record["covered_file_bytes"] is not None
    ):
        raise _VerificationBlocked("MySQL artifact coverage fields are invalid")


def _validate_replay_top_record(record: dict[str, Any], plan: DataDeletionDryRunPlan) -> None:
    metrics = plan.plan_json.get("metrics", {})
    expected_count = _nonnegative_int(metrics.get("candidate_file_count"), "candidate_file_count")
    expected_bytes = _nonnegative_int(metrics.get("candidate_file_bytes"), "candidate_file_bytes")
    if (
        record["covered_row_count"] is not None
        or record["covered_file_count"] != expected_count
        or record["covered_file_bytes"] != expected_bytes
    ):
        raise _VerificationBlocked("replay artifact coverage fields are invalid")


def _database_operations(
    plan: DataDeletionDryRunPlan,
    request: DataDeletionRequest,
) -> list[dict[str, Any]]:
    records = plan.plan_json.get("database_operations")
    if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        raise _VerificationBlocked("dry-run database operations are invalid")
    operations = [dict(item) for item in records]
    operations.sort(key=lambda item: int(item.get("sequence") or 0))
    seen: set[str] = set()
    for operation in operations:
        table = str(operation.get("table") or "")
        if table in seen:
            raise _VerificationBlocked(f"duplicate dry-run database table: {table}")
        seen.add(table)
        try:
            statement, _ = database_backup_select(operation, request)
        except DataDeletionBackupBuilderError as exc:
            raise _VerificationBlocked(str(exc)) from exc
        if not statement.lstrip().upper().startswith("SELECT"):
            raise _VerificationBlocked(f"database selector for {table} is not read-only")
    return operations


def _replay_operations(plan: DataDeletionDryRunPlan) -> list[dict[str, Any]]:
    records = plan.plan_json.get("file_operations")
    if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        raise _VerificationBlocked("dry-run replay operations are invalid")
    operations = [dict(item) for item in records]
    operations.sort(key=lambda item: int(item.get("sequence") or 0))
    seen: set[str] = set()
    for operation in operations:
        path = _safe_relative_path(operation.get("relative_path")).as_posix()
        if path in seen:
            raise _VerificationBlocked(f"duplicate dry-run replay path: {path}")
        seen.add(path)
        if (
            operation.get("action") != "quarantine_file_planned"
            or bool(operation.get("mutation_enabled"))
            or operation.get("source_table") != "replay_artifacts"
            or operation.get("storage_root") != "PUBG_REPLAY_DATA_DIR"
            or operation.get("ownership") != "player_artifact"
            or operation.get("verification_status") != "verified"
        ):
            raise _VerificationBlocked(f"dry-run replay operation is invalid: {path}")
    return operations


def _zip_catalog(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    if archive.comment:
        raise _VerificationBlocked("ZIP archive comment is not allowed")
    infos: dict[str, zipfile.ZipInfo] = {}
    total_uncompressed = 0
    for info in archive.infolist():
        name = info.filename
        _safe_zip_entry_name(name)
        if name in infos:
            raise _VerificationBlocked(f"duplicate ZIP entry: {name}")
        if info.is_dir():
            raise _VerificationBlocked(f"directory ZIP entry is not declared: {name}")
        if info.flag_bits & 0x1:
            raise _VerificationBlocked(f"encrypted ZIP entry is not supported: {name}")
        if info.compress_type not in _ALLOWED_COMPRESSION:
            raise _VerificationBlocked(f"unsupported ZIP compression method: {name}")
        unix_mode = (info.external_attr >> 16) & 0o170000
        if unix_mode == 0o120000:
            raise _VerificationBlocked(f"symbolic-link ZIP entry is not allowed: {name}")
        if info.file_size < 0 or info.file_size > _MAX_ZIP_ENTRY_BYTES:
            raise _VerificationBlocked(f"ZIP entry exceeds the verifier size limit: {name}")
        total_uncompressed += info.file_size
        if total_uncompressed > _MAX_ZIP_TOTAL_BYTES:
            raise _VerificationBlocked("ZIP total uncompressed size exceeds the verifier limit")
        if (
            info.file_size > _MAX_INTERNAL_MANIFEST_BYTES
            and info.compress_size == 0
        ):
            raise _VerificationBlocked(f"ZIP entry has an unsafe compression ratio: {name}")
        if (
            info.compress_size > 0
            and info.file_size > max(
                _MAX_INTERNAL_MANIFEST_BYTES,
                info.compress_size * _MAX_COMPRESSION_RATIO,
            )
        ):
            raise _VerificationBlocked(f"ZIP entry has an unsafe compression ratio: {name}")
        infos[name] = info
    if "manifest.json" not in infos:
        raise _VerificationBlocked("ZIP manifest.json is missing")
    return infos


def _safe_zip_entry_name(value: str) -> PurePosixPath:
    if not value or "\\" in value or "\x00" in value or ":" in value:
        raise _VerificationBlocked("ZIP entry path is unsafe")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise _VerificationBlocked(f"ZIP entry path is unsafe: {value}")
    return path


def _safe_relative_path(value: Any) -> PurePosixPath:
    text = str(value or "").replace("\\", "/").strip()
    path = PurePosixPath(text)
    if (
        not text
        or ":" in text
        or path.is_absolute()
        or path.as_posix() != text
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise _VerificationBlocked("replay relative path is unsafe")
    return path


def _read_zip_json(
    archive: zipfile.ZipFile,
    infos: dict[str, zipfile.ZipInfo],
    name: str,
    limit: int,
    label: str,
) -> dict[str, Any]:
    info = _zip_info(infos, name)
    if info.file_size > limit:
        raise _VerificationBlocked(f"{label} exceeds the verifier size limit")
    try:
        with archive.open(info, mode="r") as source:
            body = source.read(limit + 1)
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise _VerificationBlocked(f"failed to read {label}: {exc}") from exc
    if len(body) > limit or len(body) != info.file_size:
        raise _VerificationBlocked(f"{label} size is invalid")
    return _json_object_bytes(body, label)


def _verify_jsonl_entry(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
) -> tuple[int, int, str]:
    digest = hashlib.sha256()
    row_count = 0
    content_bytes = 0
    try:
        with archive.open(info, mode="r") as source:
            while True:
                line = source.readline(_MAX_JSONL_LINE_BYTES + 1)
                if not line:
                    break
                if len(line) > _MAX_JSONL_LINE_BYTES:
                    raise _VerificationBlocked(
                        f"JSONL row exceeds the verifier limit in {info.filename}"
                    )
                if not line.endswith(b"\n") or line == b"\n":
                    raise _VerificationBlocked(
                        f"JSONL row framing is invalid in {info.filename}"
                    )
                digest.update(line)
                content_bytes += len(line)
                row = _json_value_bytes(line[:-1], f"JSONL row in {info.filename}")
                if not isinstance(row, dict):
                    raise _VerificationBlocked(
                        f"JSONL row is not an object in {info.filename}"
                    )
                _validate_typed_json_value(row)
                row_count += 1
    except _VerificationBlocked:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, RecursionError) as exc:
        raise _VerificationBlocked(
            f"failed to read JSONL entry {info.filename}: {exc}"
        ) from exc
    return row_count, content_bytes, digest.hexdigest()


def _verify_binary_entry(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    expected_size: int,
) -> tuple[int, str]:
    digest = hashlib.sha256()
    observed = 0
    try:
        with archive.open(info, mode="r") as source:
            while chunk := source.read(_COPY_CHUNK_BYTES):
                observed += len(chunk)
                if observed > expected_size:
                    raise _VerificationBlocked(
                        f"replay ZIP entry exceeds its declared size: {info.filename}"
                    )
                digest.update(chunk)
    except _VerificationBlocked:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise _VerificationBlocked(
            f"failed to read replay entry {info.filename}: {exc}"
        ) from exc
    return observed, digest.hexdigest()


def _validate_typed_json_value(value: Any, depth: int = 0) -> None:
    if depth > _MAX_TYPED_VALUE_DEPTH:
        raise _VerificationBlocked("typed JSON value exceeds the nesting limit")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _VerificationBlocked("JSONL contains a non-finite number")
        return
    if isinstance(value, list):
        for item in value:
            _validate_typed_json_value(item, depth + 1)
        return
    if not isinstance(value, dict):
        raise _VerificationBlocked("JSONL contains an unsupported value")
    if "$pubg_ai_type" not in value:
        for item in value.values():
            _validate_typed_json_value(item, depth + 1)
        return
    if set(value) != {"$pubg_ai_type", "value"} or not isinstance(value["value"], str):
        raise _VerificationBlocked("typed JSON wrapper fields are invalid")
    kind = value["$pubg_ai_type"]
    text = value["value"]
    try:
        if kind == "decimal":
            parsed = Decimal(text)
            if not parsed.is_finite():
                raise InvalidOperation
        elif kind == "datetime":
            datetime.fromisoformat(text)
        elif kind == "date":
            date.fromisoformat(text)
        elif kind == "time":
            time.fromisoformat(text)
        elif kind == "bytes_base64":
            base64.b64decode(text, validate=True)
        else:
            raise _VerificationBlocked(f"unsupported typed JSON wrapper: {kind}")
    except (ValueError, InvalidOperation, binascii.Error) as exc:
        raise _VerificationBlocked(f"invalid typed JSON wrapper: {kind}") from exc


def _validate_zip_entry_size(info: zipfile.ZipInfo, expected_size: int) -> None:
    if info.file_size != expected_size:
        raise _VerificationBlocked(
            f"ZIP entry size differs from its manifest: {info.filename}"
        )


def _zip_info(
    infos: dict[str, zipfile.ZipInfo],
    name: str,
) -> zipfile.ZipInfo:
    info = infos.get(name)
    if info is None:
        raise _VerificationBlocked(f"declared ZIP entry is missing: {name}")
    return info


def _directory_entry_names(directory: Path) -> set[str]:
    try:
        names: set[str] = set()
        for item in directory.iterdir():
            if item.is_symlink() or not item.is_file():
                raise _VerificationBlocked(
                    f"build directory contains a non-regular entry: {item.name}"
                )
            names.add(item.name)
        return names
    except OSError as exc:
        raise _VerificationBlocked(f"failed to inspect build directory: {exc}") from exc


def _file_identity(path: Path) -> tuple[int, int, int, int]:
    try:
        stat = path.stat()
    except OSError as exc:
        raise _VerificationBlocked(f"backup file is unavailable: {path}: {exc}") from exc
    if not path.is_file() or path.is_symlink():
        raise _VerificationBlocked(f"backup path is not a regular file: {path}")
    return (int(stat.st_dev), int(stat.st_ino), int(stat.st_size), int(stat.st_mtime_ns))


def _read_limited_file(path: Path, limit: int) -> bytes:
    try:
        with path.open("rb") as source:
            body = source.read(limit + 1)
    except OSError as exc:
        raise DataDeletionBackupVerifierError(f"failed to read {path}: {exc}") from exc
    if len(body) > limit:
        raise DataDeletionBackupVerifierError(f"file exceeds verifier limit: {path}")
    return body


def _sha256_stream(source: BinaryIO) -> str:
    digest = hashlib.sha256()
    while chunk := source.read(_COPY_CHUNK_BYTES):
        digest.update(chunk)
    return digest.hexdigest()


def _json_object_bytes(body: bytes, label: str) -> dict[str, Any]:
    value = _json_value_bytes(body, label)
    if not isinstance(value, dict):
        raise DataDeletionBackupVerifierError(f"{label} must be a JSON object.")
    return value


def _json_value_bytes(body: bytes, label: str) -> Any:
    try:
        text = body.decode("utf-8")
        return json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number: {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonKey, ValueError) as exc:
        raise _VerificationBlocked(f"invalid {label}: {exc}") from exc


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(f"duplicate key: {key}")
        result[key] = value
    return result


def _manifest_fingerprint(manifest: dict[str, Any]) -> str:
    body = dict(manifest)
    body.pop("manifest_fingerprint_sha256", None)
    return _canonical_sha256(body)


def _assert_plan_integrity(
    plan: DataDeletionDryRunPlan,
    request: DataDeletionRequest,
) -> None:
    if plan.request_id != request.id:
        raise DataDeletionBackupVerifierError("dry-run plan belongs to another request.")
    calculated = fingerprint_dry_run_plan(plan.plan_json)
    if not hmac.compare_digest(calculated, plan.plan_fingerprint_sha256):
        raise DataDeletionBackupVerifierError("dry-run plan fingerprint is invalid.")
    if not hmac.compare_digest(
        str(plan.plan_json.get("source_fingerprint_sha256") or ""),
        plan.source_fingerprint_sha256,
    ):
        raise DataDeletionBackupVerifierError("dry-run source fingerprint binding is invalid.")


def _verification_from_row(row: dict[str, Any]) -> DataDeletionBackupVerificationRun:
    status = str(row["result_status"])
    if status not in {"passed", "blocked"}:
        raise DataDeletionBackupVerifierError(
            f"unsupported backup verification status: {status}."
        )
    result_json = row.get("result_json")
    if isinstance(result_json, str):
        try:
            result_json = json.loads(result_json)
        except json.JSONDecodeError as exc:
            raise DataDeletionBackupVerifierError("invalid verification result JSON.") from exc
    if not isinstance(result_json, dict):
        raise DataDeletionBackupVerifierError("verification result JSON must be an object.")
    result_fingerprint = _fingerprint(
        row["result_fingerprint_sha256"],
        "result fingerprint",
    )
    if not hmac.compare_digest(result_fingerprint, _canonical_sha256(result_json)):
        raise DataDeletionBackupVerifierError(
            "backup verification result fingerprint is invalid."
        )
    evidence_fingerprint = _fingerprint(
        row["evidence_set_fingerprint_sha256"],
        "evidence set fingerprint",
    )
    evidence_record_ids = _json_id_map(
        row.get("evidence_record_ids_json"),
        "evidence_record_ids_json",
    )
    result_evidence = result_json.get("evidence_set")
    if (
        not isinstance(result_evidence, dict)
        or result_evidence.get("fingerprint_sha256") != evidence_fingerprint
        or result_evidence.get("record_ids") != evidence_record_ids
        or result_json.get("verification_status") != status
    ):
        raise DataDeletionBackupVerifierError(
            "backup verification audit bindings are invalid."
        )
    return DataDeletionBackupVerificationRun(
        id=int(row["id"]),
        request_id=int(row["request_id"]),
        dry_run_plan_id=int(row["dry_run_plan_id"]),
        contract_version=str(row["contract_version"]),
        plan_fingerprint_sha256=_fingerprint(
            row["plan_fingerprint_sha256"], "plan fingerprint"
        ),
        evidence_set_fingerprint_sha256=evidence_fingerprint,
        evidence_record_ids=evidence_record_ids,
        build_id=_optional_text(row.get("build_id"), "build_id", 64),
        manifest_path=str(row["manifest_path"]),
        expected_manifest_sha256=_fingerprint(
            row["expected_manifest_sha256"], "expected manifest sha256"
        ),
        observed_manifest_sha256=_optional_fingerprint(
            row.get("observed_manifest_sha256")
        ),
        manifest_fingerprint_sha256=_optional_fingerprint(
            row.get("manifest_fingerprint_sha256")
        ),
        result_fingerprint_sha256=result_fingerprint,
        result_status=status,
        result_json=result_json,
        artifact_count=_nonnegative_int(row["artifact_count"], "artifact_count"),
        verified_artifact_count=_nonnegative_int(
            row["verified_artifact_count"], "verified_artifact_count"
        ),
        check_count=_nonnegative_int(row["check_count"], "check_count"),
        passed_check_count=_nonnegative_int(
            row["passed_check_count"], "passed_check_count"
        ),
        blocker_count=_nonnegative_int(row["blocker_count"], "blocker_count"),
        verified_by=str(row["verified_by"]),
        verification_note=_optional_text(
            row.get("verification_note"), "verification_note", 1000
        ),
        verified_at_kst=_datetime_value(row["verified_at_kst"], "verified_at_kst"),
    )


def _json_id_map(value: Any, label: str) -> dict[str, int]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise DataDeletionBackupVerifierError(f"invalid {label}.") from exc
    if not isinstance(value, dict):
        raise DataDeletionBackupVerifierError(f"{label} must be a JSON object.")
    result: dict[str, int] = {}
    for key, record_id in value.items():
        if key not in _ARTIFACT_FILENAMES:
            raise DataDeletionBackupVerifierError(f"{label} contains an invalid key.")
        result[str(key)] = _positive_int(record_id, f"{label} record id")
    if not result:
        raise DataDeletionBackupVerifierError(f"{label} must not be empty.")
    return result


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _VerificationBlocked(f"{label} must be a JSON object")
    return value


def _fingerprint(value: Any, label: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise DataDeletionBackupVerifierError(f"{label} must be a 64-character SHA-256.")
    return text


def _optional_fingerprint(value: Any) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    return _fingerprint(value, "fingerprint")


def _positive_int(value: Any, label: str) -> int:
    number = _nonnegative_int(value, label)
    if number <= 0:
        raise DataDeletionBackupVerifierError(f"{label} must be positive.")
    return number


def _nonnegative_int(value: Any, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise DataDeletionBackupVerifierError(f"{label} must be an integer.") from exc
    if number < 0:
        raise DataDeletionBackupVerifierError(f"{label} must be nonnegative.")
    return number


def _required_text(value: Any, label: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise DataDeletionBackupVerifierError(f"{label} is required.")
    if len(text) > limit:
        raise DataDeletionBackupVerifierError(f"{label} must be at most {limit} characters.")
    return text


def _optional_text(value: Any, label: str, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > limit:
        raise DataDeletionBackupVerifierError(f"{label} must be at most {limit} characters.")
    return text


def _datetime_value(value: Any, label: str) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise DataDeletionBackupVerifierError(f"invalid {label}.") from exc
    raise DataDeletionBackupVerifierError(f"invalid {label}.")


def _canonical_sha256(value: Any) -> str:
    body = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _json_dump(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _check(key: str, passed: bool, message: str) -> dict[str, Any]:
    return {
        "key": key,
        "status": "passed" if passed else "blocked",
        "message": message,
    }


def _begin(connection: Any) -> None:
    method = getattr(connection, "begin", None)
    if callable(method):
        method()


def _commit(connection: Any) -> None:
    method = getattr(connection, "commit", None)
    if callable(method):
        method()


def _rollback(connection: Any) -> None:
    method = getattr(connection, "rollback", None)
    if callable(method):
        method()
