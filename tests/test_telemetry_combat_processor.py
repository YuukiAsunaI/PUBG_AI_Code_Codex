from __future__ import annotations

import unittest
from unittest.mock import patch

from pubg_ai.telemetry_combat_processor import (
    TelemetryCombatProcessor,
    _ensure_summaries_for_tracked_accounts,
)
from pubg_ai.weapon_stats import PlayerMatchCombatSummary


class TelemetryCombatProcessorTests(unittest.TestCase):
    def test_reports_match_scoped_failure_details(self) -> None:
        processor = TelemetryCombatProcessor(connection=None, raw_store=None)  # type: ignore[arg-type]
        payload = {"match_id": "match-broken", "shard": "steam"}

        with (
            patch.object(processor, "_list_raw_telemetry_payloads", return_value=[payload]),
            patch.object(
                processor,
                "_tracked_account_ids_for_match",
                return_value={"account.tracked"},
            ),
            patch.object(
                processor,
                "_load_telemetry_events",
                side_effect=ValueError("invalid telemetry payload"),
            ),
        ):
            result = processor.process_raw_telemetry()

        self.assertEqual(result.failed_payloads, 1)
        self.assertEqual(result.parsed_payloads, 0)
        self.assertEqual(
            result.failure_details,
            [
                {
                    "match_id": "match-broken",
                    "error_type": "ValueError",
                    "message": "invalid telemetry payload",
                }
            ],
        )

    def test_ensures_zero_rows_for_tracked_players_without_combat_events(self) -> None:
        summaries = _ensure_summaries_for_tracked_accounts(
            match_id="match-1",
            tracked_account_ids={"account.tracked", "account.active"},
            summaries=[
                PlayerMatchCombatSummary(
                    match_id="match-1",
                    account_id="account.active",
                    shots_fired=10,
                )
            ],
        )

        by_account = {summary.account_id: summary for summary in summaries}

        self.assertEqual(set(by_account), {"account.tracked", "account.active"})
        self.assertEqual(by_account["account.active"].shots_fired, 10)
        self.assertEqual(by_account["account.tracked"].shots_fired, 0)
        self.assertEqual(by_account["account.tracked"].damage_dealt, 0.0)


if __name__ == "__main__":
    unittest.main()
