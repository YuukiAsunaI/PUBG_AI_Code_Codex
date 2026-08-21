from __future__ import annotations

from datetime import datetime
import unittest

from pubg_ai.fight_outcome_stats import summarize_fight_outcomes


class FightOutcomeStatsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = [
            self.row(
                event_index=50,
                outcome_type="win",
                outcome_reason="kill",
                weapon_code="WeapHK416_C",
                attachments=["Item_Attach_Weapon_Upper_DotSight_01_C"],
                opponent_is_bot=False,
                is_headshot=True,
            ),
            self.row(
                event_index=40,
                outcome_type="win",
                outcome_reason="dbno_caused",
                weapon_code="WeapHK416_C",
                attachments=["Item_Attach_Weapon_Upper_DotSight_01_C"],
                opponent_is_bot=True,
            ),
            self.row(
                event_index=30,
                outcome_type="loss",
                outcome_reason="death",
                weapon_code="WeapHK416_C",
                attachments=["Item_Attach_Weapon_Lower_Foregrip_C"],
                opponent_is_bot=False,
            ),
            self.row(
                event_index=20,
                outcome_type="loss",
                outcome_reason="dbno_taken",
                weapon_code="WeapBerylM762_C",
                attachments=[],
                opponent_is_bot=False,
                is_friendly_fire=True,
            ),
            self.row(
                event_index=10,
                outcome_type="loss",
                outcome_reason="death",
                weapon_code=None,
                attachments=[],
                opponent_is_bot=None,
                opponent_account_id=None,
            ),
        ]

    def test_summarizes_event_wins_losses_and_own_weapon_context(self) -> None:
        self.rows[0]["opponent_is_bot"] = 0
        self.rows[1]["opponent_is_bot"] = 1
        report = summarize_fight_outcomes(self.rows)

        self.assertEqual(report.totals.fight_count, 4)
        self.assertEqual(report.totals.wins, 2)
        self.assertEqual(report.totals.losses, 2)
        self.assertEqual(report.totals.fight_win_rate, 0.5)
        self.assertEqual(report.totals.kill_wins, 1)
        self.assertEqual(report.totals.dbno_wins, 1)
        self.assertEqual(report.totals.death_losses, 2)
        self.assertEqual(report.totals.dbno_losses, 0)
        self.assertEqual(report.totals.headshot_wins, 1)
        self.assertEqual(report.totals.bot_opponent_fights, 1)
        self.assertEqual(report.totals.human_opponent_fights, 2)
        self.assertEqual(report.totals.environmental_or_unknown_opponent_losses, 1)
        self.assertEqual(report.totals.unknown_weapon_contexts, 1)
        self.assertEqual(report.totals.excluded_non_firearm_contexts, 0)
        self.assertEqual(report.totals.excluded_friendly_fire, 1)

        m416 = report.weapons[0]
        self.assertEqual(m416.weapon_code, "WeapHK416_C")
        self.assertEqual(m416.weapon_name, "M416")
        self.assertEqual(m416.fight_count, 3)
        self.assertEqual(m416.wins, 2)
        self.assertEqual(m416.losses, 1)
        self.assertAlmostEqual(m416.fight_win_rate, 2 / 3)

        self.assertEqual(len(report.loadouts), 2)
        self.assertEqual(report.loadouts[0].fight_count, 2)
        self.assertEqual(report.loadouts[0].wins, 2)
        self.assertIn("레드 도트 사이트", report.loadouts[0].attachment_names)
        self.assertNotIn("Item_Attach_Weapon_Upper_DotSight_01_C", report.loadouts[0].attachment_names)
        self.assertEqual(report.recent_outcomes[0].weapon_name, "M416")
        self.assertTrue(all(not name.startswith("Item_") for name in report.recent_outcomes[0].attachment_names))
        self.assertEqual(report.recent_outcomes[0].event_index, 50)

    def test_keeps_non_firearm_events_but_excludes_them_from_weapon_rankings(self) -> None:
        rows = [
            self.row(
                event_index=60,
                outcome_type="loss",
                outcome_reason="dbno_taken",
                weapon_code="WeapTraumaBag_C",
                attachments=[],
                opponent_is_bot=False,
            ),
            *self.rows,
        ]

        report = summarize_fight_outcomes(rows)

        self.assertEqual(report.totals.fight_count, 5)
        self.assertEqual(report.totals.excluded_non_firearm_contexts, 1)
        self.assertEqual(report.recent_outcomes[0].weapon_code, "WeapTraumaBag_C")
        self.assertNotIn("WeapTraumaBag_C", [item.weapon_code for item in report.weapons])
        self.assertNotIn("WeapTraumaBag_C", [item.weapon_code for item in report.loadouts])

    def test_can_exclude_bot_fights_without_dropping_unknown_environment_losses(self) -> None:
        report = summarize_fight_outcomes(self.rows, include_bots=False)

        self.assertEqual(report.totals.fight_count, 3)
        self.assertEqual(report.totals.wins, 1)
        self.assertEqual(report.totals.losses, 2)
        self.assertEqual(report.totals.bot_opponent_fights, 0)

    def test_can_include_friendly_fire_for_audit_views(self) -> None:
        report = summarize_fight_outcomes(self.rows, include_friendly_fire=True)

        self.assertEqual(report.totals.fight_count, 5)
        self.assertEqual(report.totals.dbno_losses, 1)
        self.assertEqual(report.totals.excluded_friendly_fire, 0)

    @staticmethod
    def row(
        *,
        event_index: int,
        outcome_type: str,
        outcome_reason: str,
        weapon_code: str | None,
        attachments: list[str],
        opponent_is_bot: bool | None,
        is_headshot: bool = False,
        is_friendly_fire: bool = False,
        opponent_account_id: str | None = "account.enemy",
    ) -> dict[str, object]:
        return {
            "match_id": "match-1",
            "event_index": event_index,
            "event_at_kst": datetime(2026, 8, 2, 9, 0, event_index % 60),
            "map_name": "Baltic_Main",
            "game_mode": "squad-fpp",
            "outcome_type": outcome_type,
            "outcome_reason": outcome_reason,
            "opponent_account_id": opponent_account_id,
            "opponent_is_bot": opponent_is_bot,
            "is_friendly_fire": is_friendly_fire,
            "weapon_code": weapon_code,
            "weapon_name_ko": weapon_code,
            "attachment_codes": attachments,
            "attachment_names_ko": attachments,
            "weapon_context_source": "attack" if weapon_code else "unknown",
            "opponent_weapon_code": "WeapAK47_C",
            "opponent_weapon_name_ko": "AKM",
            "is_headshot": is_headshot,
            "distance_m": 30.0,
        }


if __name__ == "__main__":
    unittest.main()
