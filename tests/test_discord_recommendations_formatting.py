from __future__ import annotations

import unittest

from pubg_ai.discord_bot import format_player_recommendations
from pubg_ai.player_recommendations import (
    AttachmentRecommendation,
    DropZoneRecommendation,
    MapRecommendation,
    PlayerRecommendationReport,
    TeammateRecommendation,
    WeaponAttachmentRecommendation,
    WeaponDistanceBucketRecommendation,
    WeaponRecommendation,
)
from pubg_ai.player_registry import RegisteredPlayer


class DiscordRecommendationFormattingTests(unittest.TestCase):
    def test_formats_recommendation_summary(self) -> None:
        report = PlayerRecommendationReport(
            player=RegisteredPlayer(
                id=1,
                account_id="account.test",
                shard="steam",
                current_name="Yuuki_Asuna---",
                active=True,
                public_profile=True,
            ),
            min_matches=1,
            weapons=[
                WeaponRecommendation(
                    weapon_code="WeapHK416_C",
                    weapon_name="M416",
                    score=420.5,
                    match_count=5,
                    wins=2,
                    kills=10,
                    assists=3,
                    deaths=2,
                    dbnos=8,
                    damage_dealt=1800.0,
                    shots_fired=600,
                    shots_hit=180,
                    win_rate=0.4,
                    kills_per_match=2.0,
                    dbnos_per_match=1.6,
                    avg_damage_dealt=360.0,
                    accuracy=0.3,
                    reason="test",
                )
            ],
            weapon_attachments=[
                WeaponAttachmentRecommendation(
                    weapon_code="WeapHK416_C",
                    weapon_name="M416",
                    attachment_code="Item_Attach_Weapon_Lower_Foregrip_C",
                    attachment_name="Vertical Grip",
                    attachment_category="Attachment",
                    attachment_sub_category="Lower",
                    score=500.0,
                    match_count=2,
                    attached_events=3,
                    wins=1,
                    kills=4,
                    dbnos=3,
                    damage_dealt=720.0,
                    win_rate=0.5,
                    kills_per_match=2.0,
                    avg_damage_dealt=360.0,
                    reason="test",
                )
            ],
            weapon_ranges=[
                WeaponDistanceBucketRecommendation(
                    weapon_code="WeapHK416_C",
                    weapon_name="M416",
                    bucket_label="10-15m",
                    min_m=10,
                    max_m=15,
                    weapon_family="AR",
                    score=128.0,
                    event_count=1,
                    kills=1,
                    dbnos=0,
                    finishes=0,
                    avg_distance_m=12.0,
                    reason="test",
                )
            ],
            attachments=[
                AttachmentRecommendation(
                    item_code="Item_Attach_Weapon_Lower_Foregrip_C",
                    item_name="Vertical Grip",
                    item_category="Attachment",
                    item_sub_category="Lower",
                    score=360.0,
                    match_count=4,
                    attached_events=6,
                    wins=2,
                    win_rate=0.5,
                    avg_damage_dealt=350.0,
                    reason="test",
                )
            ],
            maps=[
                MapRecommendation(
                    map_name="Erangel_Main",
                    map_name_ko="Erangel",
                    score=390.0,
                    match_count=6,
                    wins=3,
                    kills=12,
                    assists=4,
                    deaths=3,
                    dbnos=9,
                    damage_dealt=2100.0,
                    win_rate=0.5,
                    kda=5.3,
                    avg_damage_dealt=350.0,
                    avg_survival_seconds=1500.0,
                    reason="test",
                )
            ],
            teammates=[
                TeammateRecommendation(
                    account_id="account.friend",
                    name="Friend",
                    registered=True,
                    score=370.0,
                    match_count=4,
                    wins=2,
                    kills=9,
                    assists=4,
                    deaths=2,
                    dbnos=7,
                    damage_dealt=1500.0,
                    win_rate=0.5,
                    kda=6.5,
                    avg_damage_dealt=375.0,
                    reason="test",
                )
            ],
            drop_zones=[
                DropZoneRecommendation(
                    map_name="Tiger_Main",
                    map_name_ko="Taego",
                    grid_x=5,
                    grid_y=2,
                    x_pct=0.52,
                    y_pct=0.25,
                    score=410.0,
                    match_count=2,
                    wins=1,
                    kills=4,
                    deaths=1,
                    damage_dealt=500.0,
                    win_rate=0.5,
                    avg_damage_dealt=250.0,
                    avg_survival_seconds=1690.0,
                    reason="test",
                )
            ],
        )

        body = format_player_recommendations(report)

        self.assertIn("Yuuki_Asuna--- recommendations", body)
        self.assertIn("M416", body)
        self.assertIn("Vertical Grip", body)
        self.assertIn("M416 + Vertical Grip", body)
        self.assertIn("M416 10-15m", body)
        self.assertIn("Erangel", body)
        self.assertIn("Friend registered", body)
        self.assertIn("Taego grid 5,2", body)


if __name__ == "__main__":
    unittest.main()
