from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping
import hashlib
import json
import re

from pubg_ai.data_deletion_review_packet_verifier import (
    ExportedReviewPacketVerification,
    ExportedReviewPacketVerifier,
    ExportedReviewPacketVerifierError,
    canonical_exported_review_packet_bytes,
    parse_exported_review_packet_text,
)
from pubg_ai.time_utils import now_kst, to_kst


EXPORTED_PACKET_COMPARER_CONTRACT_VERSION = (
    "deletion-review-packet-export-comparer-v1"
)
COMPARISON_STATUS_EQUIVALENT = "equivalent_packets"
COMPARISON_STATUS_DIFFERENT = "different_packets"
MAX_REPORTED_CANONICAL_FIELD_DIFFERENCES = 1000

_INPUT_ID_FIELDS = (
    "request_id",
    "dry_run_plan_id",
    "backup_verification_run_id",
    "quarantine_planning_run_id",
    "combined_rehearsal_run_id",
    "fault_matrix_run_id",
)
_FINGERPRINT_FIELDS = (
    ("packet_fingerprint_sha256", ("packet_fingerprint_sha256",)),
    (
        "input_contract_fingerprint_sha256",
        ("input_contract_fingerprint_sha256",),
    ),
    (
        "plan_fingerprint_sha256",
        ("input_contract", "plan_fingerprint_sha256"),
    ),
    (
        "backup_verification_result_fingerprint_sha256",
        (
            "input_contract",
            "backup_verification_result_fingerprint_sha256",
        ),
    ),
    (
        "quarantine_planning_result_fingerprint_sha256",
        (
            "input_contract",
            "quarantine_planning_result_fingerprint_sha256",
        ),
    ),
    (
        "destination_contract_fingerprint_sha256",
        ("input_contract", "destination_contract_fingerprint_sha256"),
    ),
    (
        "combined_rehearsal_result_fingerprint_sha256",
        (
            "input_contract",
            "combined_rehearsal_result_fingerprint_sha256",
        ),
    ),
    (
        "fault_matrix_result_fingerprint_sha256",
        ("input_contract", "fault_matrix_result_fingerprint_sha256"),
    ),
    (
        "fault_scenario_contract_fingerprint_sha256",
        (
            "input_contract",
            "fault_scenario_contract_fingerprint_sha256",
        ),
    ),
    (
        "confirmation_text_sha256",
        ("generation", "confirmation_text_sha256"),
    ),
)
_ASSESSMENT_FIELDS = (
    ("review_status", ("assessment", "review_status")),
    ("blocked_checks", ("assessment", "blocked_checks")),
    ("input_count", ("metrics", "input_count")),
    ("passed_input_count", ("metrics", "passed_input_count")),
    ("blocked_input_count", ("metrics", "blocked_input_count")),
    ("check_count", ("metrics", "check_count")),
    ("passed_check_count", ("metrics", "passed_check_count")),
    ("blocked_check_count", ("metrics", "blocked_check_count")),
    ("fault_scenario_count", ("metrics", "fault_scenario_count")),
    (
        "passed_fault_scenario_count",
        ("metrics", "passed_fault_scenario_count"),
    ),
    ("contained_fault_count", ("metrics", "contained_fault_count")),
    (
        "scratch_resources_removed",
        ("metrics", "scratch_resources_removed"),
    ),
    (
        "backup_verification_result_status",
        ("artifacts", "backup_verification", "result_status"),
    ),
    (
        "quarantine_planning_result_status",
        ("artifacts", "quarantine_planning", "result_status"),
    ),
    (
        "combined_rehearsal_result_status",
        ("artifacts", "combined_rehearsal", "result_status"),
    ),
    (
        "fault_matrix_result_status",
        ("artifacts", "fault_matrix", "result_status"),
    ),
)
_SIMPLE_JSON_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MISSING = object()


class ExportedReviewPacketComparerError(RuntimeError):
    """Raised when two exported review packets cannot be compared safely."""


@dataclass(frozen=True)
class ExportedReviewPacketComparison:
    comparison_status: str
    comparison_fingerprint_sha256: str
    baseline_verification: ExportedReviewPacketVerification
    candidate_verification: ExportedReviewPacketVerification
    canonical_field_differences: tuple[dict[str, Any], ...]
    canonical_field_difference_count: int
    input_id_differences: tuple[dict[str, Any], ...]
    fingerprint_differences: tuple[dict[str, Any], ...]
    assessment_differences: tuple[dict[str, Any], ...]
    review_check_differences: tuple[dict[str, Any], ...]
    compared_at_kst: datetime

    def to_record(self) -> dict[str, Any]:
        database_requested = (
            self.baseline_verification.database_cross_check_requested
            or self.candidate_verification.database_cross_check_requested
        )
        database_passed = (
            bool(
                self.baseline_verification.database_cross_check_passed
                and self.candidate_verification.database_cross_check_passed
            )
            if database_requested
            else None
        )
        reported_count = len(self.canonical_field_differences)
        return {
            "contract_version": EXPORTED_PACKET_COMPARER_CONTRACT_VERSION,
            "direction": "baseline_to_candidate",
            "comparison_status": self.comparison_status,
            "packets_equivalent": (
                self.comparison_status == COMPARISON_STATUS_EQUIVALENT
            ),
            "comparison_fingerprint_sha256": (
                self.comparison_fingerprint_sha256
            ),
            "baseline_verification": (
                self.baseline_verification.to_record()
            ),
            "candidate_verification": (
                self.candidate_verification.to_record()
            ),
            "differences": {
                "canonical_fields": [
                    deepcopy(item)
                    for item in self.canonical_field_differences
                ],
                "input_ids": [
                    deepcopy(item) for item in self.input_id_differences
                ],
                "fingerprints": [
                    deepcopy(item) for item in self.fingerprint_differences
                ],
                "assessment": [
                    deepcopy(item) for item in self.assessment_differences
                ],
                "review_checks": [
                    deepcopy(item) for item in self.review_check_differences
                ],
            },
            "metrics": {
                "canonical_field_difference_count": (
                    self.canonical_field_difference_count
                ),
                "reported_canonical_field_difference_count": reported_count,
                "max_reported_canonical_field_differences": (
                    MAX_REPORTED_CANONICAL_FIELD_DIFFERENCES
                ),
                "canonical_field_differences_truncated": (
                    reported_count < self.canonical_field_difference_count
                ),
                "input_id_difference_count": len(self.input_id_differences),
                "fingerprint_difference_count": len(
                    self.fingerprint_differences
                ),
                "assessment_difference_count": len(
                    self.assessment_differences
                ),
                "review_check_difference_count": len(
                    self.review_check_differences
                ),
            },
            "database_cross_check_requested": database_requested,
            "database_cross_check_passed": database_passed,
            "compared_at_kst": to_kst(self.compared_at_kst).isoformat(),
            "read_only": True,
            "uploaded_text_persisted": False,
            "comparison_persisted": False,
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


class ExportedReviewPacketComparer:
    def __init__(self, connection: Any | None = None) -> None:
        self.connection = connection

    def compare_texts(
        self,
        baseline_text: str,
        candidate_text: str,
        *,
        cross_check_database: bool,
        reference_kst: datetime | None = None,
    ) -> ExportedReviewPacketComparison:
        verifier = ExportedReviewPacketVerifier(self.connection)
        baseline_verification = self._verify_role(
            verifier,
            "baseline",
            baseline_text,
            cross_check_database=cross_check_database,
            reference_kst=reference_kst,
        )
        candidate_verification = self._verify_role(
            verifier,
            "candidate",
            candidate_text,
            cross_check_database=cross_check_database,
            reference_kst=reference_kst,
        )
        baseline_payload, _ = parse_exported_review_packet_text(baseline_text)
        candidate_payload, _ = parse_exported_review_packet_text(candidate_text)
        baseline_canonical = canonical_exported_review_packet_bytes(
            baseline_payload
        )
        candidate_canonical = canonical_exported_review_packet_bytes(
            candidate_payload
        )

        canonical_differences, canonical_difference_count = (
            _canonical_field_differences(
                baseline_payload,
                candidate_payload,
                limit=MAX_REPORTED_CANONICAL_FIELD_DIFFERENCES,
            )
        )
        input_id_differences = _named_field_differences(
            baseline_payload,
            candidate_payload,
            ((field, (field,)) for field in _INPUT_ID_FIELDS),
        )
        fingerprint_differences = _named_field_differences(
            baseline_payload,
            candidate_payload,
            _FINGERPRINT_FIELDS,
        )
        assessment_differences = _named_field_differences(
            baseline_payload,
            candidate_payload,
            _ASSESSMENT_FIELDS,
        )
        review_check_differences = _review_check_differences(
            baseline_payload,
            candidate_payload,
        )
        equivalent = baseline_canonical == candidate_canonical
        return ExportedReviewPacketComparison(
            comparison_status=(
                COMPARISON_STATUS_EQUIVALENT
                if equivalent
                else COMPARISON_STATUS_DIFFERENT
            ),
            comparison_fingerprint_sha256=_comparison_fingerprint(
                baseline_verification.canonical_export_sha256,
                candidate_verification.canonical_export_sha256,
            ),
            baseline_verification=baseline_verification,
            candidate_verification=candidate_verification,
            canonical_field_differences=tuple(canonical_differences),
            canonical_field_difference_count=canonical_difference_count,
            input_id_differences=tuple(input_id_differences),
            fingerprint_differences=tuple(fingerprint_differences),
            assessment_differences=tuple(assessment_differences),
            review_check_differences=tuple(review_check_differences),
            compared_at_kst=to_kst(reference_kst or now_kst()),
        )

    @staticmethod
    def _verify_role(
        verifier: ExportedReviewPacketVerifier,
        role: str,
        packet_text: str,
        *,
        cross_check_database: bool,
        reference_kst: datetime | None,
    ) -> ExportedReviewPacketVerification:
        try:
            return verifier.verify_text(
                packet_text,
                cross_check_database=cross_check_database,
                reference_kst=reference_kst,
            )
        except ExportedReviewPacketVerifierError as exc:
            raise ExportedReviewPacketComparerError(
                f"{role} review packet verification failed: {exc}"
            ) from exc


def _comparison_fingerprint(
    baseline_canonical_sha256: str,
    candidate_canonical_sha256: str,
) -> str:
    contract = {
        "contract_version": EXPORTED_PACKET_COMPARER_CONTRACT_VERSION,
        "direction": "baseline_to_candidate",
        "baseline_canonical_export_sha256": baseline_canonical_sha256,
        "candidate_canonical_export_sha256": candidate_canonical_sha256,
    }
    return hashlib.sha256(_canonical_json_bytes(contract)).hexdigest()


def _canonical_field_differences(
    baseline: Any,
    candidate: Any,
    *,
    limit: int,
) -> tuple[list[dict[str, Any]], int]:
    differences: list[dict[str, Any]] = []
    count = 0

    def record(
        path: str,
        change_type: str,
        baseline_value: Any,
        candidate_value: Any,
    ) -> None:
        nonlocal count
        count += 1
        if len(differences) >= limit:
            return
        differences.append(
            {
                "path": path,
                "change_type": change_type,
                "baseline_value": _summarize_value(baseline_value),
                "candidate_value": _summarize_value(candidate_value),
            }
        )

    def walk(baseline_value: Any, candidate_value: Any, path: str) -> None:
        if isinstance(baseline_value, dict) and isinstance(
            candidate_value,
            dict,
        ):
            keys = sorted(set(baseline_value) | set(candidate_value))
            for key in keys:
                child_path = _json_object_path(path, key)
                if key not in baseline_value:
                    record(
                        child_path,
                        "added",
                        _MISSING,
                        candidate_value[key],
                    )
                elif key not in candidate_value:
                    record(
                        child_path,
                        "removed",
                        baseline_value[key],
                        _MISSING,
                    )
                else:
                    walk(
                        baseline_value[key],
                        candidate_value[key],
                        child_path,
                    )
            return
        if isinstance(baseline_value, list) and isinstance(
            candidate_value,
            list,
        ):
            common_length = min(len(baseline_value), len(candidate_value))
            for index in range(common_length):
                walk(
                    baseline_value[index],
                    candidate_value[index],
                    f"{path}[{index}]",
                )
            for index in range(common_length, len(baseline_value)):
                record(
                    f"{path}[{index}]",
                    "removed",
                    baseline_value[index],
                    _MISSING,
                )
            for index in range(common_length, len(candidate_value)):
                record(
                    f"{path}[{index}]",
                    "added",
                    _MISSING,
                    candidate_value[index],
                )
            return
        if type(baseline_value) is not type(candidate_value) or (
            baseline_value != candidate_value
        ):
            record(path, "changed", baseline_value, candidate_value)

    walk(baseline, candidate, "$")
    return differences, count


def _named_field_differences(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    fields: Iterable[tuple[str, tuple[str, ...]]],
) -> list[dict[str, Any]]:
    differences: list[dict[str, Any]] = []
    for name, path in fields:
        baseline_value = _path_value(baseline, path)
        candidate_value = _path_value(candidate, path)
        if type(baseline_value) is type(candidate_value) and (
            baseline_value == candidate_value
        ):
            continue
        differences.append(
            {
                "field": name,
                "baseline_value": _summarize_value(baseline_value),
                "candidate_value": _summarize_value(candidate_value),
            }
        )
    return differences


def _review_check_differences(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> list[dict[str, Any]]:
    baseline_checks = baseline["assessment"]["checks"]
    candidate_checks = candidate["assessment"]["checks"]
    baseline_by_key = {item["key"]: item for item in baseline_checks}
    candidate_by_key = {item["key"]: item for item in candidate_checks}
    ordered_keys = list(baseline_by_key)
    ordered_keys.extend(
        key for key in candidate_by_key if key not in baseline_by_key
    )
    differences: list[dict[str, Any]] = []
    for key in ordered_keys:
        baseline_check = baseline_by_key.get(key, _MISSING)
        candidate_check = candidate_by_key.get(key, _MISSING)
        if baseline_check == candidate_check:
            continue
        changed_fields = []
        for field in ("status", "message", "details"):
            baseline_value = (
                baseline_check.get(field, _MISSING)
                if isinstance(baseline_check, dict)
                else _MISSING
            )
            candidate_value = (
                candidate_check.get(field, _MISSING)
                if isinstance(candidate_check, dict)
                else _MISSING
            )
            if type(baseline_value) is not type(candidate_value) or (
                baseline_value != candidate_value
            ):
                changed_fields.append(field)
        differences.append(
            {
                "key": key,
                "changed_fields": changed_fields,
                "baseline_status": (
                    baseline_check.get("status")
                    if isinstance(baseline_check, dict)
                    else None
                ),
                "candidate_status": (
                    candidate_check.get("status")
                    if isinstance(candidate_check, dict)
                    else None
                ),
                "baseline_message": (
                    baseline_check.get("message")
                    if isinstance(baseline_check, dict)
                    else None
                ),
                "candidate_message": (
                    candidate_check.get("message")
                    if isinstance(candidate_check, dict)
                    else None
                ),
                "baseline_details": _summarize_value(
                    baseline_check.get("details", _MISSING)
                    if isinstance(baseline_check, dict)
                    else _MISSING
                ),
                "candidate_details": _summarize_value(
                    candidate_check.get("details", _MISSING)
                    if isinstance(candidate_check, dict)
                    else _MISSING
                ),
            }
        )
    return differences


def _path_value(value: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = value
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return _MISSING
        current = current[key]
    return current


def _json_object_path(parent: str, key: str) -> str:
    if _SIMPLE_JSON_KEY.fullmatch(key):
        return f"{parent}.{key}"
    return f"{parent}[{json.dumps(key, ensure_ascii=False)}]"


def _summarize_value(value: Any) -> Any:
    if value is _MISSING:
        return {"present": False}
    if isinstance(value, str):
        if len(value) <= 256:
            return value
        encoded = value.encode("utf-8")
        return {
            "type": "string",
            "length": len(value),
            "utf8_size_bytes": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "preview": value[:120],
        }
    if value is None or isinstance(value, (bool, int, float)):
        return value
    canonical = _canonical_json_bytes(value)
    if len(canonical) <= 1024:
        return deepcopy(value)
    return {
        "type": "array" if isinstance(value, list) else "object",
        "item_count": len(value) if isinstance(value, (dict, list)) else None,
        "canonical_size_bytes": len(canonical),
        "canonical_sha256": hashlib.sha256(canonical).hexdigest(),
    }


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
