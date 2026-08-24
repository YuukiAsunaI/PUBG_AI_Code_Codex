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
from pubg_ai.weapon_accuracy import weapon_accuracy_metric


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
                    headshot_hits=36,
                    headshot_hit_rate=0.2,
                    fight_count=20,
                    fight_wins=14,
                    fight_losses=6,
                    fight_win_rate=0.7,
                    accuracy_metric=weapon_accuracy_metric("WeapHK416_C", 600, 180),
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
                    grid_x=10,
                    grid_y=5,
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
                    cluster_id="Tiger_Main:grid20:10:5",
                    centroid_x_cm=428400.0,
                    centroid_y_cm=244800.0,
                    region_status="matched",
                    region_id="taego.yong_cheon",
                    region_name="Yong Cheon",
                    region_name_ko="용천",
                    region_display_name_ko="용천",
                    region_catalog_version="test-v1",
                    reason="test",
                )
            ],
        )

        body = format_player_recommendations(report)

        self.assertIn("Yuuki_Asuna--- 추천 분석", body)
        self.assertIn("M416", body)
        self.assertIn("추정 30.0%", body)
        self.assertIn("헤드샷 명중 20.0%", body)
        self.assertIn("교전 승률 70.0%", body)
        self.assertIn("Vertical Grip", body)
        self.assertIn("M416 + Vertical Grip", body)
        self.assertIn("M416 10-15m", body)
        self.assertIn("Erangel", body)
        self.assertIn("Friend (등록 유저)", body)
        self.assertIn("Taego 용천", body)
        self.assertNotIn("/players/recommendations/weapon-attachment-evidence", body)

        body_with_links = format_player_recommendations(
            report,
            evidence_base_url="http://127.0.0.1:8000/",
            detail_base_url="http://127.0.0.1:8000/",
        )

        self.assertIn(
            "http://127.0.0.1:8000/players/recommendations/weapon-attachment-evidence",
            body_with_links,
        )
        self.assertIn("account_id=account.test", body_with_links)
        self.assertIn("weapon_code=WeapHK416_C", body_with_links)
        self.assertIn("attachment_code=Item_Attach_Weapon_Lower_Foregrip_C", body_with_links)
        self.assertIn(
            "- 로컬 상세: [열기](http://127.0.0.1:8000/?shard=steam&account_id=account.test&min_matches=1#recommendation-lookup)",
            body_with_links,
        )


if __name__ == "__main__":
    unittest.main()
