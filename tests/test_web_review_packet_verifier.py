from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from pubg_ai.data_deletion_review_packet_verifier import (
    ExportedReviewPacketVerifierError,
)
from pubg_ai.web.app import create_app


class WebReviewPacketVerifierTests(unittest.TestCase):
    def test_offline_verification_never_opens_database_or_persists_text(self) -> None:
        verifier = MagicMock()
        verifier.verify_text.return_value = _verification_result()

        with (
            patch(
                "pubg_ai.web.app.connect_mysql",
                side_effect=AssertionError("offline verification opened MySQL"),
            ),
            patch(
                "pubg_ai.web.app.ExportedReviewPacketVerifier",
                return_value=verifier,
            ) as verifier_type,
        ):
            response = TestClient(create_app()).post(
                "/data-deletion-review-packets/verify",
                json={"packet_text": "{}", "cross_check_database": False},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(
            response.json()["verification"]["verification_status"],
            "valid_offline",
        )
        self.assertFalse(response.json()["uploaded_text_persisted"])
        self.assertFalse(response.json()["records_created"])
        self.assertFalse(response.json()["database_writes_performed"])
        self.assertFalse(response.json()["authorization_granted"])
        self.assertFalse(response.json()["readiness_promoted"])
        self.assertFalse(response.json()["execution_enabled"])
        self.assertFalse(response.json()["execution_ready"])
        verifier_type.assert_called_once_with(None)
        verifier.verify_text.assert_called_once_with(
            "{}",
            cross_check_database=False,
        )

    def test_database_cross_check_uses_and_closes_one_connection(self) -> None:
        connection = _Connection()
        verifier = MagicMock()
        verifier.verify_text.return_value = _verification_result(
            status="valid_and_database_current",
            database_requested=True,
            database_passed=True,
        )

        with (
            patch("pubg_ai.web.app.connect_mysql", return_value=connection),
            patch(
                "pubg_ai.web.app.ExportedReviewPacketVerifier",
                return_value=verifier,
            ) as verifier_type,
        ):
            response = TestClient(create_app()).post(
                "/data-deletion-review-packets/verify",
                json={"packet_text": "{}", "cross_check_database": True},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(connection.closed)
        verifier_type.assert_called_once_with(connection)
        verifier.verify_text.assert_called_once_with(
            "{}",
            cross_check_database=True,
        )

    def test_invalid_packet_returns_conflict_without_echoing_packet_text(self) -> None:
        verifier = MagicMock()
        verifier.verify_text.side_effect = ExportedReviewPacketVerifierError(
            "exported review packet canonical fingerprint is invalid."
        )
        packet_text = '{"secret_like_local_note":"do-not-echo"}'

        with (
            patch(
                "pubg_ai.web.app.connect_mysql",
                side_effect=AssertionError("invalid offline check opened MySQL"),
            ),
            patch(
                "pubg_ai.web.app.ExportedReviewPacketVerifier",
                return_value=verifier,
            ),
        ):
            response = TestClient(create_app()).post(
                "/data-deletion-review-packets/verify",
                json={
                    "packet_text": packet_text,
                    "cross_check_database": False,
                },
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertIn("canonical fingerprint", response.json()["detail"])
        self.assertNotIn("do-not-echo", response.text)


class _Connection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _VerificationResult:
    def __init__(
        self,
        *,
        status: str,
        database_requested: bool,
        database_passed: bool | None,
    ) -> None:
        self.status = status
        self.database_requested = database_requested
        self.database_passed = database_passed

    def to_record(self) -> dict:
        return {
            "contract_version": "deletion-review-packet-export-verifier-v1",
            "verification_status": self.status,
            "review_status": "advisory_checks_passed",
            "request_id": 17,
            "dry_run_plan_id": 901,
            "fault_matrix_run_id": 1901,
            "packet_fingerprint_sha256": "a" * 64,
            "input_contract_fingerprint_sha256": "b" * 64,
            "canonical_export_sha256": "c" * 64,
            "canonical_export_size_bytes": 4096,
            "database_cross_check_requested": self.database_requested,
            "database_cross_check_passed": self.database_passed,
            "matched_packet_id": 2001 if self.database_passed else None,
            "checks": [],
            "check_count": 0,
            "passed_check_count": 0,
            "blocked_check_count": 0,
            "uploaded_text_persisted": False,
            "records_created": False,
            "database_writes_performed": False,
            "authorization_granted": False,
            "readiness_promoted": False,
            "execution_enabled": False,
            "execution_ready": False,
        }


def _verification_result(
    *,
    status: str = "valid_offline",
    database_requested: bool = False,
    database_passed: bool | None = None,
) -> _VerificationResult:
    return _VerificationResult(
        status=status,
        database_requested=database_requested,
        database_passed=database_passed,
    )


if __name__ == "__main__":
    unittest.main()
