from __future__ import annotations

import unittest

from pubg_ai.discord_bot import format_player_fight_outcomes
from pubg_ai.fight_outcome_stats import (
    FightLoadoutStats,
    FightOutcomeTotals,
    FightWeaponStats,
    PlayerFightOutcomeReport,
)
from pubg_ai.player_registry import RegisteredPlayer


class DiscordFightOutcomeFormattingTests(unittest.TestCase):
    def test_formats_fight_totals_weapons_loadouts_and_local_link(self) -> None:
        report = PlayerFightOutcomeReport(
            player=RegisteredPlayer(
                id=1,
                account_id="account.test",
                shard="steam",
                current_name="Yuuki_Asuna---",
                active=True,
                public_profile=True,
                registered_by_discord_user_id=None,
                registered_guild_id="guild-1",
                registered_channel_id=None,
            ),
            totals=FightOutcomeTotals(
                fight_count=20,
                wins=12,
                losses=8,
                fight_win_rate=0.6,
                kill_wins=7,
                dbno_wins=5,
                death_losses=3,
                dbno_losses=5,
                headshot_wins=2,
                human_opponent_fights=18,
                human_opponent_wins=10,
                bot_opponent_fights=2,
                bot_opponent_wins=2,
                environmental_or_unknown_opponent_losses=0,
                unknown_weapon_contexts=1,
                excluded_non_firearm_contexts=3,
                excluded_friendly_fire=2,
            ),
            weapons=[
                FightWeaponStats(
                    weapon_code="WeapHK416_C",
                    weapon_name="M416",
                    fight_count=10,
                    wins=7,
                    losses=3,
                    fight_win_rate=0.7,
                    kill_wins=4,
                    dbno_wins=3,
                    death_losses=1,
                    dbno_losses=2,
                )
            ],
            loadouts=[
                FightLoadoutStats(
                    weapon_code="WeapHK416_C",
                    weapon_name="M416",
                    attachment_codes=("Item_Attach_Weapon_Upper_DotSight_01_C",),
                    attachment_names=("레드도트",),
                    fight_count=6,
                    wins=5,
                    losses=1,
                    fight_win_rate=5 / 6,
                )
            ],
            recent_outcomes=[],
        )

        body = format_player_fight_outcomes(
            report,
            detail_base_url="http://127.0.0.1:8018/",
        )

        self.assertIn("Yuuki_Asuna--- 교전 승패 (steam)", body)
        self.assertIn("12승/8패 (60.0%)", body)
        self.assertIn("킬 7 / 기절 5", body)
        self.assertIn("M416 7승/3패 70.0%", body)
        self.assertIn("M416 + 레드도트: 5승/1패 83.3%", body)
        self.assertIn("아군 피해 제외: 2건", body)
        self.assertIn("총기 순위 제외: 비총기 장비 3건", body)
        self.assertIn("#profile-lookup", body)


if __name__ == "__main__":
    unittest.main()
