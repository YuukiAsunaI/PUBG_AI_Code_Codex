from __future__ import annotations

import unittest

from pubg_ai.telemetry_combat_processor import _ensure_summaries_for_tracked_accounts
from pubg_ai.weapon_stats import PlayerMatchCombatSummary


class TelemetryCombatProcessorTests(unittest.TestCase):
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
