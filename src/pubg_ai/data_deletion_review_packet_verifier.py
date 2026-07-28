from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping
import hashlib
import json
import math

from pubg_ai.data_deletion_backup_verifier import BACKUP_VERIFIER_CONTRACT_VERSION
from pubg_ai.data_deletion_combined_rehearsal import (
    COMBINED_REHEARSAL_CONTRACT_VERSION,
)
from pubg_ai.data_deletion_dry_run import DRY_RUN_CONTRACT_VERSION
from pubg_ai.data_deletion_fault_matrix import (
    FAULT_MATRIX_CONTRACT_VERSION,
    fault_scenario_contract,
)
from pubg_ai.data_deletion_quarantine_planner import (
    QUARANTINE_PLANNER_CONTRACT_VERSION,
)
from pubg_ai.data_deletion_review_packet import (
    REVIEW_PACKET_CONTRACT_VERSION,
    REVIEW_PACKET_KIND,
    REVIEW_STATUS_BLOCKED,
    REVIEW_STATUS_PASSED,
    DataDeletionReviewPacketError,
    review_packet_from_database_row,
    validate_exported_review_packet_payload,
)
from pubg_ai.time_utils import now_kst, to_kst


EXPORTED_PACKET_VERIFIER_CONTRACT_VERSION = (
    "deletion-review-packet-export-verifier-v1"
)
MAX_EXPORTED_REVIEW_PACKET_BYTES = 2 * 1024 * 1024
VERIFICATION_STATUS_OFFLINE = "valid_offline"
VERIFICATION_STATUS_CURRENT = "valid_and_database_current"
VERIFICATION_STATUS_DATABASE_MISMATCH = "valid_but_database_mismatch"

_CANONICALIZATION = "utf8-json-sort-keys-compact-sha256-body-v1"
_EXPECTED_TOP_LEVEL_KEYS = {
    "contract_version",
    "packet_kind",
    "canonicalization",
    "request_id",
    "dry_run_plan_id",
    "backup_verification_run_id",
    "quarantine_planning_run_id",
    "combined_rehearsal_run_id",
    "fault_matrix_run_id",
    "input_contract",
    "input_contract_fingerprint_sha256",
    "subject",
    "artifacts",
    "fault_scenarios",
    "assessment",
    "metrics",
    "generation",
    "safety",
    "packet_fingerprint_sha256",
}
_EXPECTED_INPUT_CONTRACT_KEYS = {
    "contract_version",
    "request_id",
    "request_status",
    "dry_run_plan_id",
    "plan_fingerprint_sha256",
    "backup_verification_run_id",
    "backup_verification_result_fingerprint_sha256",
    "quarantine_planning_run_id",
    "quarantine_planning_result_fingerprint_sha256",
    "destination_contract_fingerprint_sha256",
    "combined_rehearsal_run_id",
    "combined_rehearsal_result_fingerprint_sha256",
    "fault_matrix_run_id",
    "fault_matrix_result_fingerprint_sha256",
    "fault_scenario_contract_fingerprint_sha256",
}
_EXPECTED_ARTIFACT_KEYS = {
    "dry_run_plan",
    "backup_verification",
    "quarantine_planning",
    "combined_rehearsal",
    "fault_matrix",
}
_EXPECTED_REVIEW_CHECK_KEYS = (
    "request_approved_unexecuted",
    "dry_run_plan_current",
    "backup_verification_passed",
    "quarantine_planning_passed",
    "combined_rehearsal_passed",
    "fault_matrix_outcome",
    "all_declared_faults_contained",
    "advisory_non_authorization",
)


class ExportedReviewPacketVerifierError(RuntimeError):
    """Raised when an exported review packet cannot be verified safely."""


@dataclass(frozen=True)
class ExportedReviewPacketVerification:
    verification_status: str
    review_status: str
    request_id: int
    dry_run_plan_id: int
    fault_matrix_run_id: int
    packet_fingerprint_sha256: str
    input_contract_fingerprint_sha256: str
    canonical_export_sha256: str
    canonical_export_size_bytes: int
    database_cross_check_requested: bool
    database_cross_check_passed: bool | None
    matched_packet_id: int | None
    checks: tuple[dict[str, Any], ...]
    verified_at_kst: datetime

    def to_record(self) -> dict[str, Any]:
        passed_check_count = sum(
            item.get("status") == "passed" for item in self.checks
        )
        blocked_check_count = sum(
            item.get("status") == "blocked" for item in self.checks
        )
        return {
            "contract_version": EXPORTED_PACKET_VERIFIER_CONTRACT_VERSION,
            "verification_status": self.verification_status,
            "review_status": self.review_status,
            "request_id": self.request_id,
            "dry_run_plan_id": self.dry_run_plan_id,
            "fault_matrix_run_id": self.fault_matrix_run_id,
            "packet_fingerprint_sha256": self.packet_fingerprint_sha256,
            "input_contract_fingerprint_sha256": (
                self.input_contract_fingerprint_sha256
            ),
            "canonical_export_sha256": self.canonical_export_sha256,
            "canonical_export_size_bytes": self.canonical_export_size_bytes,
            "database_cross_check_requested": (
                self.database_cross_check_requested
            ),
            "database_cross_check_passed": self.database_cross_check_passed,
            "matched_packet_id": self.matched_packet_id,
            "checks": [deepcopy(item) for item in self.checks],
            "check_count": len(self.checks),
            "passed_check_count": passed_check_count,
            "blocked_check_count": blocked_check_count,
            "verified_at_kst": to_kst(self.verified_at_kst).isoformat(),
            "read_only": True,
            "uploaded_text_persisted": False,
            "records_created": False,
            "database_writes_performed": False,
            "authorization_granted": False,
            "readiness_promoted": False,
            "evidence_created": False,
            "production_database_rows_modified": False,
            "production_source_files_opened": False,
            "production_source_files_modified": False,
            "production_quarantine_performed": False,
            "production_restore_performed": False,
            "deletion_performed": False,
            "execution_enabled": False,
            "execution_ready": False,
        }


class ExportedReviewPacketVerifier:
    def __init__(self, connection: Any | None = None) -> None:
        self.connection = connection

    def verify_text(
        self,
        packet_text: str,
        *,
        cross_check_database: bool,
        reference_kst: datetime | None = None,
    ) -> ExportedReviewPacketVerification:
        payload, source_size = parse_exported_review_packet_text(packet_text)
        try:
            packet = validate_exported_review_packet_payload(payload)
            _validate_v1_export_shape(payload)
            canonical_bytes = canonical_exported_review_packet_bytes(payload)
        except DataDeletionReviewPacketError as exc:
            raise ExportedReviewPacketVerifierError(str(exc)) from exc
        except (RecursionError, UnicodeError) as exc:
            raise ExportedReviewPacketVerifierError(
                "exported review packet cannot be canonicalized safely."
            ) from exc
        checks = [
            _check(
                "strict_json_and_size",
                True,
                "packet text is strict JSON within the 2 MiB limit",
                {"source_size_bytes": source_size},
            ),
            _check(
                "canonical_packet_fingerprint",
                True,
                "packet and input-contract SHA-256 values are internally valid",
                {
                    "packet_fingerprint_sha256": (
                        packet.packet_fingerprint_sha256
                    ),
                    "input_contract_fingerprint_sha256": (
                        packet.input_contract_fingerprint_sha256
                    ),
                },
            ),
            _check(
                "fixed_v1_shape",
                True,
                "packet keys, input bindings, checks, and fault scenarios match v1",
                {
                    "review_check_count": packet.check_count,
                    "fault_scenario_count": packet.fault_scenario_count,
                },
            ),
            _check(
                "non_authorization_safety",
                True,
                "packet remains advisory and cannot authorize or execute deletion",
                {
                    "authorization_granted": False,
                    "readiness_promoted": False,
                    "execution_enabled": False,
                },
            ),
            _check(
                "canonical_export_built",
                True,
                "canonical UTF-8 JSON was rebuilt in memory without persistence",
                {
                    "canonical_export_size_bytes": len(canonical_bytes),
                    "uploaded_text_persisted": False,
                },
            ),
        ]

        matched_packet_id: int | None = None
        database_passed: bool | None = None
        if cross_check_database:
            if self.connection is None:
                raise ExportedReviewPacketVerifierError(
                    "database connection is required for the requested cross-check."
                )
            database_checks, matched_packet_id = self._database_checks(payload)
            checks.extend(database_checks)
            database_passed = all(
                item.get("status") == "passed" for item in database_checks
            )
            status = (
                VERIFICATION_STATUS_CURRENT
                if database_passed
                else VERIFICATION_STATUS_DATABASE_MISMATCH
            )
        else:
            status = VERIFICATION_STATUS_OFFLINE

        return ExportedReviewPacketVerification(
            verification_status=status,
            review_status=packet.review_status,
            request_id=packet.request_id,
            dry_run_plan_id=packet.dry_run_plan_id,
            fault_matrix_run_id=packet.fault_matrix_run_id,
            packet_fingerprint_sha256=packet.packet_fingerprint_sha256,
            input_contract_fingerprint_sha256=(
                packet.input_contract_fingerprint_sha256
            ),
            canonical_export_sha256=hashlib.sha256(canonical_bytes).hexdigest(),
            canonical_export_size_bytes=len(canonical_bytes),
            database_cross_check_requested=cross_check_database,
            database_cross_check_passed=database_passed,
            matched_packet_id=matched_packet_id,
            checks=tuple(checks),
            verified_at_kst=to_kst(reference_kst or now_kst()),
        )

    def _database_checks(
        self,
        payload: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], int | None]:
        input_contract = payload["input_contract"]
        artifacts = payload["artifacts"]
        checks: list[dict[str, Any]] = []

        packet_row = self._fetchone(
            """
            SELECT *
            FROM data_deletion_readiness_review_packets
            WHERE packet_fingerprint_sha256 = %s
            ORDER BY generated_at_kst DESC, id DESC
            LIMIT 1
            """,
            (payload["packet_fingerprint_sha256"],),
        )
        matched_packet_id: int | None = None
        packet_match = False
        if packet_row is not None:
            try:
                persisted = review_packet_from_database_row(packet_row)
                matched_packet_id = persisted.id
                packet_match = persisted.packet_json == payload
            except DataDeletionReviewPacketError:
                packet_match = False
        checks.append(
            _check(
                "persisted_packet_matches",
                packet_match,
                "canonical packet exactly matches an immutable local audit row",
                {
                    "matched_packet_id": matched_packet_id,
                    "packet_fingerprint_sha256": payload[
                        "packet_fingerprint_sha256"
                    ],
                },
                "no valid immutable local packet row exactly matches this export",
            )
        )

        request_row = self._fetchone(
            """
            SELECT id, status, executed_at_kst, execution_summary_json
            FROM data_deletion_requests
            WHERE id = %s
            """,
            (payload["request_id"],),
        )
        request_match = bool(
            request_row
            and _int_value(request_row.get("id")) == payload["request_id"]
            and request_row.get("status") == "approved"
            and request_row.get("executed_at_kst") is None
            and request_row.get("execution_summary_json") is None
        )
        checks.append(
            _check(
                "request_current_and_unexecuted",
                request_match,
                "local request remains approved with no execution metadata",
                {"request_id": payload["request_id"]},
                "local request is missing, changed, or has execution metadata",
            )
        )

        plan_row = self._fetchone(
            """
            SELECT id, request_id, contract_version, plan_fingerprint_sha256
            FROM data_deletion_dry_run_plans
            WHERE request_id = %s
            ORDER BY generated_at_kst DESC, id DESC
            LIMIT 1
            """,
            (payload["request_id"],),
        )
        plan_match = _row_matches(
            plan_row,
            {
                "id": payload["dry_run_plan_id"],
                "request_id": payload["request_id"],
                "contract_version": DRY_RUN_CONTRACT_VERSION,
                "plan_fingerprint_sha256": input_contract[
                    "plan_fingerprint_sha256"
                ],
            },
        )
        checks.append(
            _check(
                "dry_run_plan_latest",
                plan_match,
                "local latest dry-run plan ID and fingerprint match",
                {"dry_run_plan_id": payload["dry_run_plan_id"]},
                "local latest dry-run plan is missing or differs",
            )
        )

        verification_row = self._fetchone(
            """
            SELECT id, dry_run_plan_id, contract_version,
                   result_fingerprint_sha256, result_status
            FROM data_deletion_backup_verification_runs
            WHERE dry_run_plan_id = %s
            ORDER BY verified_at_kst DESC, id DESC
            LIMIT 1
            """,
            (payload["dry_run_plan_id"],),
        )
        verification_match = _row_matches(
            verification_row,
            {
                "id": payload["backup_verification_run_id"],
                "dry_run_plan_id": payload["dry_run_plan_id"],
                "contract_version": BACKUP_VERIFIER_CONTRACT_VERSION,
                "result_fingerprint_sha256": input_contract[
                    "backup_verification_result_fingerprint_sha256"
                ],
                "result_status": "passed",
            },
        )
        checks.append(
            _check(
                "backup_verification_latest",
                verification_match,
                "local latest passed backup verification matches",
                {
                    "backup_verification_run_id": payload[
                        "backup_verification_run_id"
                    ]
                },
                "local latest backup verification is missing or differs",
            )
        )

        planning_row = self._fetchone(
            """
            SELECT id, dry_run_plan_id, contract_version,
                   result_fingerprint_sha256,
                   destination_contract_fingerprint_sha256,
                   result_status, capacity_evidence_id
            FROM data_deletion_quarantine_planning_runs
            WHERE dry_run_plan_id = %s
            ORDER BY planned_at_kst DESC, id DESC
            LIMIT 1
            """,
            (payload["dry_run_plan_id"],),
        )
        planning_match = bool(
            _row_matches(
                planning_row,
                {
                    "id": payload["quarantine_planning_run_id"],
                    "dry_run_plan_id": payload["dry_run_plan_id"],
                    "contract_version": QUARANTINE_PLANNER_CONTRACT_VERSION,
                    "result_fingerprint_sha256": input_contract[
                        "quarantine_planning_result_fingerprint_sha256"
                    ],
                    "destination_contract_fingerprint_sha256": input_contract[
                        "destination_contract_fingerprint_sha256"
                    ],
                    "result_status": "passed",
                },
            )
            and planning_row.get("capacity_evidence_id") is not None
        )
        checks.append(
            _check(
                "quarantine_planning_latest",
                planning_match,
                "local latest passed quarantine plan and capacity evidence match",
                {
                    "quarantine_planning_run_id": payload[
                        "quarantine_planning_run_id"
                    ]
                },
                "local latest quarantine plan is missing, stale, or unbound",
            )
        )

        combined_row = self._fetchone(
            """
            SELECT id, dry_run_plan_id, contract_version,
                   backup_verification_run_id, quarantine_planning_run_id,
                   result_fingerprint_sha256, result_status,
                   scratch_resources_removed
            FROM data_deletion_combined_rehearsal_runs
            WHERE dry_run_plan_id = %s
            ORDER BY run_at_kst DESC, id DESC
            LIMIT 1
            """,
            (payload["dry_run_plan_id"],),
        )
        combined_match = bool(
            _row_matches(
                combined_row,
                {
                    "id": payload["combined_rehearsal_run_id"],
                    "dry_run_plan_id": payload["dry_run_plan_id"],
                    "contract_version": COMBINED_REHEARSAL_CONTRACT_VERSION,
                    "backup_verification_run_id": payload[
                        "backup_verification_run_id"
                    ],
                    "quarantine_planning_run_id": payload[
                        "quarantine_planning_run_id"
                    ],
                    "result_fingerprint_sha256": input_contract[
                        "combined_rehearsal_result_fingerprint_sha256"
                    ],
                    "result_status": "passed",
                },
            )
            and _bool_value(combined_row.get("scratch_resources_removed"))
            is True
        )
        checks.append(
            _check(
                "combined_rehearsal_latest",
                combined_match,
                "local latest passed combined rehearsal matches and is clean",
                {
                    "combined_rehearsal_run_id": payload[
                        "combined_rehearsal_run_id"
                    ]
                },
                "local latest combined rehearsal is missing, stale, or unclean",
            )
        )

        matrix_row = self._fetchone(
            """
            SELECT id, dry_run_plan_id, contract_version,
                   backup_verification_run_id, quarantine_planning_run_id,
                   combined_rehearsal_run_id,
                   result_fingerprint_sha256,
                   scenario_contract_fingerprint_sha256,
                   result_status, scratch_resources_removed
            FROM data_deletion_combined_fault_matrix_runs
            WHERE dry_run_plan_id = %s
            ORDER BY run_at_kst DESC, id DESC
            LIMIT 1
            """,
            (payload["dry_run_plan_id"],),
        )
        matrix_match = _row_matches(
            matrix_row,
            {
                "id": payload["fault_matrix_run_id"],
                "dry_run_plan_id": payload["dry_run_plan_id"],
                "contract_version": FAULT_MATRIX_CONTRACT_VERSION,
                "backup_verification_run_id": payload[
                    "backup_verification_run_id"
                ],
                "quarantine_planning_run_id": payload[
                    "quarantine_planning_run_id"
                ],
                "combined_rehearsal_run_id": payload[
                    "combined_rehearsal_run_id"
                ],
                "result_fingerprint_sha256": input_contract[
                    "fault_matrix_result_fingerprint_sha256"
                ],
                "scenario_contract_fingerprint_sha256": input_contract[
                    "fault_scenario_contract_fingerprint_sha256"
                ],
                "result_status": artifacts["fault_matrix"]["result_status"],
                "scratch_resources_removed": payload["metrics"][
                    "scratch_resources_removed"
                ],
            },
        )
        checks.append(
            _check(
                "fault_matrix_latest",
                matrix_match,
                "local latest passed-or-blocked fault matrix exactly matches",
                {"fault_matrix_run_id": payload["fault_matrix_run_id"]},
                "local latest fault matrix is missing or differs",
            )
        )
        return checks, matched_packet_id

    def _fetchone(
        self,
        statement: str,
        parameters: tuple[Any, ...],
    ) -> dict[str, Any] | None:
        with self.connection.cursor() as cursor:
            cursor.execute(statement, parameters)
            row = cursor.fetchone()
        return _normalize_row(row) if row else None


def parse_exported_review_packet_text(
    packet_text: str,
) -> tuple[dict[str, Any], int]:
    if not isinstance(packet_text, str):
        raise ExportedReviewPacketVerifierError(
            "exported review packet must be supplied as JSON text."
        )
    try:
        size = len(packet_text.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ExportedReviewPacketVerifierError(
            "exported review packet is not valid UTF-8 JSON text."
        ) from exc
    if size == 0:
        raise ExportedReviewPacketVerifierError(
            "exported review packet text is empty."
        )
    if size > MAX_EXPORTED_REVIEW_PACKET_BYTES:
        raise ExportedReviewPacketVerifierError(
            "exported review packet exceeds the 2 MiB verification limit."
        )
    try:
        payload = json.loads(
            packet_text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
            parse_float=_finite_json_float,
        )
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ExportedReviewPacketVerifierError(
            "exported review packet is not strict JSON."
        ) from exc
    if not isinstance(payload, dict):
        raise ExportedReviewPacketVerifierError(
            "exported review packet must be a JSON object."
        )
    return payload, size


def canonical_exported_review_packet_bytes(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _validate_v1_export_shape(payload: dict[str, Any]) -> None:
    if set(payload) != _EXPECTED_TOP_LEVEL_KEYS:
        raise ExportedReviewPacketVerifierError(
            "exported packet top-level keys do not match the v1 contract."
        )
    if (
        payload.get("contract_version") != REVIEW_PACKET_CONTRACT_VERSION
        or payload.get("packet_kind") != REVIEW_PACKET_KIND
        or payload.get("canonicalization") != _CANONICALIZATION
    ):
        raise ExportedReviewPacketVerifierError(
            "exported packet contract metadata does not match v1."
        )
    input_contract = payload.get("input_contract")
    if not isinstance(input_contract, dict) or set(input_contract) != (
        _EXPECTED_INPUT_CONTRACT_KEYS
    ):
        raise ExportedReviewPacketVerifierError(
            "exported packet input-contract keys do not match v1."
        )
    subject = payload.get("subject")
    if not isinstance(subject, dict) or (
        subject.get("id") != payload["request_id"]
        or subject.get("status") != "approved"
        or subject.get("executed_at_kst") is not None
        or subject.get("execution_summary_json") is not None
    ):
        raise ExportedReviewPacketVerifierError(
            "exported packet subject is not the approved unexecuted request."
        )
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != _EXPECTED_ARTIFACT_KEYS:
        raise ExportedReviewPacketVerifierError(
            "exported packet artifact keys do not match v1."
        )
    if any(not isinstance(value, dict) for value in artifacts.values()):
        raise ExportedReviewPacketVerifierError(
            "exported packet artifact summaries are invalid."
        )
    artifact_bindings = (
        (
            "dry_run_plan",
            "dry_run_plan_id",
            "plan_fingerprint_sha256",
            "plan_fingerprint_sha256",
        ),
        (
            "backup_verification",
            "backup_verification_run_id",
            "result_fingerprint_sha256",
            "backup_verification_result_fingerprint_sha256",
        ),
        (
            "quarantine_planning",
            "quarantine_planning_run_id",
            "result_fingerprint_sha256",
            "quarantine_planning_result_fingerprint_sha256",
        ),
        (
            "combined_rehearsal",
            "combined_rehearsal_run_id",
            "result_fingerprint_sha256",
            "combined_rehearsal_result_fingerprint_sha256",
        ),
        (
            "fault_matrix",
            "fault_matrix_run_id",
            "result_fingerprint_sha256",
            "fault_matrix_result_fingerprint_sha256",
        ),
    )
    for artifact_key, id_key, artifact_fingerprint_key, input_fingerprint_key in (
        artifact_bindings
    ):
        artifact = artifacts[artifact_key]
        if (
            artifact.get("id") != payload[id_key]
            or artifact.get(artifact_fingerprint_key)
            != input_contract[input_fingerprint_key]
        ):
            raise ExportedReviewPacketVerifierError(
                "exported packet artifact ID or fingerprint bindings are invalid."
            )
    if (
        artifacts["backup_verification"].get("result_status") != "passed"
        or artifacts["quarantine_planning"].get("result_status") != "passed"
        or artifacts["combined_rehearsal"].get("result_status") != "passed"
        or artifacts["fault_matrix"].get("result_status")
        not in {"passed", "blocked"}
    ):
        raise ExportedReviewPacketVerifierError(
            "exported packet artifact result statuses are invalid."
        )

    scenarios = payload.get("fault_scenarios")
    declared = fault_scenario_contract()
    if not isinstance(scenarios, list) or len(scenarios) != len(declared):
        raise ExportedReviewPacketVerifierError(
            "exported packet fault scenario count is invalid."
        )
    static_fields = ("sequence", "key", "category", "fault_point")
    for observed, expected in zip(scenarios, declared, strict=True):
        if not isinstance(observed, dict) or any(
            observed.get(key) != expected.get(key) for key in static_fields
        ):
            raise ExportedReviewPacketVerifierError(
                "exported packet fault scenario contract is invalid."
            )

    assessment = payload.get("assessment")
    metrics = payload.get("metrics")
    if not isinstance(assessment, dict) or not isinstance(metrics, dict):
        raise ExportedReviewPacketVerifierError(
            "exported packet assessment or metrics are invalid."
        )
    checks = assessment.get("checks")
    blocked_checks = assessment.get("blocked_checks")
    if not isinstance(checks, list) or not isinstance(blocked_checks, list):
        raise ExportedReviewPacketVerifierError(
            "exported packet review checks are invalid."
        )
    if [item.get("key") if isinstance(item, dict) else None for item in checks] != (
        list(_EXPECTED_REVIEW_CHECK_KEYS)
    ):
        raise ExportedReviewPacketVerifierError(
            "exported packet review-check order is invalid."
        )
    if any(
        item.get("status") not in {"passed", "blocked"}
        for item in checks
        if isinstance(item, dict)
    ):
        raise ExportedReviewPacketVerifierError(
            "exported packet review-check status is invalid."
        )
    expected_blocked_messages = [
        item.get("message")
        for item in checks
        if isinstance(item, dict) and item.get("status") == "blocked"
    ]
    if blocked_checks != expected_blocked_messages:
        raise ExportedReviewPacketVerifierError(
            "exported packet blocked-check messages are inconsistent."
        )

    passed_scenarios = sum(
        isinstance(item, dict) and item.get("status") == "passed"
        for item in scenarios
    )
    contained_scenarios = sum(
        isinstance(item, dict) and item.get("fault_contained") is True
        for item in scenarios
    )
    scratch_removed = all(
        isinstance(item, dict) and item.get("scratch_removed") is True
        for item in scenarios
    )
    passed_checks = sum(item.get("status") == "passed" for item in checks)
    blocked_check_count = len(checks) - passed_checks
    matrix_passed = artifacts["fault_matrix"]["result_status"] == "passed"
    expected_review_status = (
        REVIEW_STATUS_PASSED
        if blocked_check_count == 0
        else REVIEW_STATUS_BLOCKED
    )
    expected_passed_inputs = 6 if matrix_passed else 5
    if (
        assessment.get("review_status") != expected_review_status
        or metrics.get("input_count") != 6
        or metrics.get("passed_input_count") != expected_passed_inputs
        or metrics.get("blocked_input_count") != 6 - expected_passed_inputs
        or metrics.get("check_count") != len(checks)
        or metrics.get("passed_check_count") != passed_checks
        or metrics.get("blocked_check_count") != blocked_check_count
        or metrics.get("fault_scenario_count") != len(scenarios)
        or metrics.get("passed_fault_scenario_count") != passed_scenarios
        or metrics.get("contained_fault_count") != contained_scenarios
        or metrics.get("scratch_resources_removed") is not scratch_removed
    ):
        raise ExportedReviewPacketVerifierError(
            "exported packet assessment metrics are inconsistent."
        )
    matrix = artifacts["fault_matrix"]
    if (
        matrix.get("scenario_count") != len(scenarios)
        or matrix.get("passed_scenario_count") != passed_scenarios
        or matrix.get("contained_fault_count") != contained_scenarios
        or matrix.get("scratch_resources_removed") is not scratch_removed
        or matrix_passed
        != (
            passed_scenarios == len(scenarios)
            and contained_scenarios == len(scenarios)
            and scratch_removed
        )
    ):
        raise ExportedReviewPacketVerifierError(
            "exported packet fault-matrix summary is inconsistent."
        )


def _check(
    key: str,
    passed: bool,
    passed_message: str,
    details: dict[str, Any],
    blocked_message: str | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "status": "passed" if passed else "blocked",
        "message": passed_message if passed else (blocked_message or passed_message),
        "details": deepcopy(details),
    }


def _normalize_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key).lower(): value for key, value in row.items()}


def _row_matches(
    row: Mapping[str, Any] | None,
    expected: Mapping[str, Any],
) -> bool:
    if row is None:
        return False
    for key, value in expected.items():
        observed = row.get(key)
        if isinstance(value, bool):
            if _bool_value(observed) is not value:
                return False
        elif isinstance(value, int) and not isinstance(value, bool):
            if _int_value(observed) != value:
                return False
        elif observed != value:
            return False
    return True


def _int_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool_value(value: Any) -> bool | None:
    if value in (True, 1, b"\x01"):
        return True
    if value in (False, 0, b"\x00"):
        return False
    return None


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"non-finite JSON number: {value}")
    return parsed


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")
