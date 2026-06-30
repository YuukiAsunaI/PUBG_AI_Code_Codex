from __future__ import annotations

from datetime import datetime
import unittest

from pubg_ai.discord_bot import (
    _player_visible_to_scope,
    format_alert_action_result,
    format_alert_note_result,
    format_player_list,
    format_player_match_detail,
    format_player_profile_stats,
    format_player_ranking,
    format_player_weapon_detail,
    format_replay_artifact_summary,
)
from pubg_ai.alert_history import AlertHistoryNote, AlertHistoryRecord
from pubg_ai.player_rankings import PlayerRanking, PlayerRankingRow
from pubg_ai.player_registry import RegisteredPlayer
from pubg_ai.player_stats import (
    PlayerCombatTotals,
    PlayerMatchDetail,
    PlayerMatchWeaponStats,
    PlayerProfileStats,
    PlayerRecentMatch,
    PlayerWeaponDetail,
    PlayerWeaponDetailTotals,
    PlayerWeaponRecentMatch,
    PlayerWeaponStats,
)
from pubg_ai.replay_artifact_catalog import ReplayArtifactRecord


class DiscordBotFormattingTests(unittest.TestCase):
    def test_alert_action_result_formats_discord_admin_response(self) -> None:
        record = AlertHistoryRecord(
            id=7,
            alert_key="worker:7",
            source="worker",
            severity="error",
            title="collector worker failed",
            message="drive missing",
            metadata={},
            first_seen_at_kst="2026-06-30T10:00:00+09:00",
            last_seen_at_kst="2026-06-30T10:01:00+09:00",
            last_notified_at_kst=None,
            acknowledged_at_kst="2026-06-30T10:02:00+09:00",
            snoozed_until_kst=None,
            resolved_at_kst=None,
            updated_at_kst="2026-06-30T10:02:00+09:00",
        )

        body = format_alert_action_result(record, "acknowledged")

        self.assertIn("PUBG AI alert acknowledged", body)
        self.assertIn("- id: 7", body)
        self.assertIn("collector worker failed", body)
        self.assertIn("acknowledged_at_kst", body)

    def test_alert_note_result_formats_discord_admin_response(self) -> None:
        note = AlertHistoryNote(
            id=12,
            alert_history_id=7,
            note_type="resolution",
            note_text="raw drive expanded and worker restarted",
            created_by="discord:987654321:123456789",
            created_at_kst="2026-06-30T10:05:00+09:00",
        )

        body = format_alert_note_result(note)

        self.assertIn("PUBG AI alert resolution saved", body)
        self.assertIn("- alert_id: 7", body)
        self.assertIn("- note_id: 12", body)
        self.assertIn("- type: resolution", body)
        self.assertIn("discord:987654321:123456789", body)
        self.assertIn("raw drive expanded and worker restarted", body)

    def test_player_list_formats_status_and_short_account_id(self) -> None:
        players = [
            RegisteredPlayer(
                id=1,
                account_id="account.1234567890abcdef",
                shard="steam",
                current_name="Yuuki_Asuna---",
                active=True,
                public_profile=True,
            ),
            RegisteredPlayer(
                id=2,
                account_id="account.abcdef1234567890",
                shard="kakao",
                current_name="StoppedPlayer",
                active=False,
                public_profile=False,
            ),
        ]

        body = format_player_list(players)

        self.assertIn("등록 유저", body)
        self.assertIn("Yuuki_Asuna--- (steam) / 수집중 / 공개", body)
        self.assertIn("StoppedPlayer (kakao) / 중지 / 비공개", body)
        self.assertIn("account.1234567...cdef", body)
        self.assertNotIn("account.1234567890abcdef", body)

    def test_empty_player_list_has_clear_message(self) -> None:
        self.assertEqual(format_player_list([]), "등록된 유저가 없습니다.")

    def test_profile_stats_summary_formats_core_metrics(self) -> None:
        profile = PlayerProfileStats(
            player=RegisteredPlayer(
                id=1,
                account_id="account.test",
                shard="steam",
                current_name="Yuuki_Asuna---",
                active=True,
                public_profile=True,
            ),
            totals=PlayerCombatTotals(
                match_count=10,
                wins=2,
                kills=25,
                assists=5,
                deaths=8,
                dbnos_caused=13,
                dbnos_taken=4,
                damage_dealt=2500.0,
                damage_taken=1600.0,
                shots_fired=1000,
                shots_hit=210,
                headshot_kills=6,
                avg_damage_dealt=250.0,
                avg_damage_taken=160.0,
                win_rate=0.2,
                kda=3.75,
                accuracy=0.21,
                headshot_kill_rate=0.24,
                avg_survival_seconds=1420.5,
                avg_movement_distance_m=3650.0,
            ),
            top_weapons=[
                PlayerWeaponStats(
                    weapon_code="WeapBerylM762_C",
                    weapon_name="베릴 M762",
                    match_count=6,
                    kills=12,
                    assists=2,
                    deaths=3,
                    dbnos=8,
                    damage_dealt=1200.0,
                    shots_fired=500,
                    shots_hit=95,
                    accuracy=0.19,
                    headshot_kills=2,
                )
            ],
            recent_matches=[
                PlayerRecentMatch(
                    match_id="match-123456789",
                    created_at_kst=datetime(2026, 6, 29, 1, 0, 0),
                    map_name="Erangel_Main",
                    game_mode="squad-fpp",
                    match_type="official",
                    win_place=1,
                    kills=5,
                    assists=1,
                    deaths=0,
                    dbnos_caused=3,
                    damage_dealt=550.0,
                    survival_seconds=1788.5,
                    movement_distance_m=4200.0,
                )
            ],
        )

        body = format_player_profile_stats(profile)

        self.assertIn("Yuuki_Asuna--- 전적 (steam)", body)
        self.assertIn("10전 2치킨 (20.0%)", body)
        self.assertIn("25/8/5", body)
        self.assertIn("KDA 3.75", body)
        self.assertIn("베릴 M762 12킬 1200딜", body)
        self.assertIn("match-12 #1 5킬/550딜", body)

    def test_weapon_detail_summary_formats_weapon_metrics(self) -> None:
        detail = PlayerWeaponDetail(
            player=RegisteredPlayer(
                id=1,
                account_id="account.test",
                shard="steam",
                current_name="Yuuki_Asuna---",
                active=True,
                public_profile=True,
            ),
            weapon_code="WeapHK416_C",
            weapon_name="M416",
            totals=PlayerWeaponDetailTotals(
                match_count=12,
                wins=2,
                kills=20,
                assists=4,
                deaths_taken=1,
                dbnos=16,
                dbnos_taken=0,
                finishes=8,
                finishes_taken=0,
                damage_dealt=2400.0,
                damage_taken=90.0,
                shots_fired=1000,
                shots_hit=230,
                hits_taken=1,
                headshot_hits=30,
                headshot_kills=5,
                headshot_dbnos=4,
                accuracy=0.23,
                avg_damage_dealt=200.0,
                win_rate=2 / 12,
                headshot_kill_rate=0.25,
                hit_parts={"head": 30, "torso": 140},
                taken_hit_parts={"arm": 1},
            ),
            recent_matches=[
                PlayerWeaponRecentMatch(
                    match_id="match-123456789",
                    created_at_kst=datetime(2026, 6, 29, 1, 0, 0),
                    map_name="Erangel_Main",
                    game_mode="squad-fpp",
                    win_place=1,
                    kills=4,
                    assists=1,
                    deaths_taken=0,
                    dbnos=3,
                    damage_dealt=520.0,
                    shots_fired=120,
                    shots_hit=40,
                    accuracy=1 / 3,
                )
            ],
        )

        body = format_player_weapon_detail(detail)

        self.assertIn("Yuuki_Asuna--- M416 무기 통계", body)
        self.assertIn("12전 2치킨", body)
        self.assertIn("20/4/16", body)
        self.assertIn("23.0%", body)
        self.assertIn("몸통 140", body)
        self.assertIn("match-12 #1 4킬/3기절/520딜", body)

    def test_match_detail_summary_formats_core_metrics(self) -> None:
        artifact = ReplayArtifactRecord(
            id=10,
            match_id="match-123456789",
            shard="steam",
            artifact_type="map_snapshot",
            artifact_name="player-route",
            account_id="account.test",
            player_name="Yuuki_Asuna---",
            map_name="Erangel_Main",
            game_mode="squad-fpp",
            match_type="official",
            match_created_at_kst=datetime(2026, 6, 29, 1, 0, 0),
            storage_backend="local_file",
            storage_root="PUBG_REPLAY_DATA_DIR",
            relative_path="map_snapshot/steam/2026/06/29/match-123456789/player-route.jpg",
            content_type="image/jpeg",
            size_bytes=2048,
            sha256="abc123",
            renderer_version="test",
            generated_at_kst=datetime(2026, 6, 29, 1, 3, 0),
        )
        detail = PlayerMatchDetail(
            player=RegisteredPlayer(
                id=1,
                account_id="account.test",
                shard="steam",
                current_name="Yuuki_Asuna---",
                active=True,
                public_profile=True,
            ),
            match_id="match-123456789",
            shard="steam",
            map_name="Erangel_Main",
            game_mode="squad-fpp",
            match_type="official",
            created_at_kst=datetime(2026, 6, 29, 1, 0, 0),
            duration_seconds=1800,
            total_players=100,
            human_players=96,
            bot_players=4,
            roster_id="roster-1",
            team_id=12,
            win_place=2,
            is_chicken=False,
            death_type="byplayer",
            kills=4,
            assists=1,
            deaths=1,
            dbnos_caused=5,
            dbnos_taken=1,
            finishes=3,
            finishes_taken=1,
            damage_dealt=620.0,
            damage_taken=310.0,
            shots_fired=200,
            shots_hit=50,
            hits_taken=8,
            accuracy=0.25,
            headshot_hits=10,
            headshot_hits_taken=2,
            headshot_kills=2,
            headshot_deaths=0,
            headshot_dbnos_caused=2,
            headshot_dbnos_taken=0,
            hit_parts={"head": 10, "torso": 34},
            taken_hit_parts={"arm": 3},
            survival_seconds=1750.5,
            landing_distance_m=760.0,
            movement_distance_m=3500.0,
            weapons=[
                PlayerMatchWeaponStats(
                    weapon_code="WeapHK416_C",
                    weapon_name="M416",
                    kills=3,
                    assists=1,
                    deaths=0,
                    dbnos=4,
                    dbnos_taken=1,
                    damage_dealt=420.0,
                    damage_taken=50.0,
                    shots_fired=120,
                    shots_hit=36,
                    accuracy=0.3,
                    headshot_kills=1,
                    hit_parts={"head": 6},
                    taken_hit_parts={"arm": 1},
                )
            ],
            replay_artifact=artifact,
        )

        body = format_player_match_detail(detail)

        self.assertIn("Yuuki_Asuna--- 매치 상세 (steam)", body)
        self.assertIn("match-123456789", body)
        self.assertIn("총 100명, 사람 96명, 봇 4명", body)
        self.assertIn("4/1/1/5", body)
        self.assertIn("200/50/25.0%", body)
        self.assertIn("M416 3킬/4기절/420딜/30.0%", body)
        self.assertIn("!최근스냅샷 match-123456789", body)

    def test_player_ranking_summary_formats_rows(self) -> None:
        ranking = PlayerRanking(
            metric="kda",
            metric_label="KDA",
            shard="steam",
            guild_id="guild-1",
            global_scope=False,
            active_only=True,
            min_matches=1,
            rows=[
                PlayerRankingRow(
                    rank=1,
                    player=RegisteredPlayer(
                        id=1,
                        account_id="account.test",
                        shard="steam",
                        current_name="Yuuki_Asuna---",
                        active=True,
                        public_profile=True,
                    ),
                    score=3.75,
                    match_count=10,
                    wins=2,
                    kills=25,
                    assists=5,
                    deaths=8,
                    dbnos_caused=13,
                    dbnos_taken=4,
                    damage_dealt=2500.0,
                    damage_taken=1600.0,
                    shots_fired=1000,
                    shots_hit=210,
                    headshot_kills=6,
                    avg_damage_dealt=250.0,
                    avg_damage_taken=160.0,
                    win_rate=0.2,
                    kda=3.75,
                    accuracy=0.21,
                    headshot_kill_rate=0.24,
                    avg_survival_seconds=1420.5,
                    avg_movement_distance_m=3650.0,
                    last_match_at_kst=datetime(2026, 6, 29, 1, 0, 0),
                )
            ],
        )

        body = format_player_ranking(ranking)

        self.assertIn("KDA 랭킹 (steam, 서버 guild-1)", body)
        self.assertIn("#1 Yuuki_Asuna---: 3.75", body)
        self.assertIn("10전 2치킨", body)
        self.assertIn("25K/8D/5A", body)

    def test_player_scope_visibility_requires_matching_guild_or_global_scope(self) -> None:
        player = RegisteredPlayer(
            id=1,
            account_id="account.1234567890abcdef",
            shard="steam",
            current_name="Yuuki_Asuna---",
            active=True,
            public_profile=True,
            registered_guild_id="guild-1",
        )

        self.assertTrue(_player_visible_to_scope(player, "guild-1", False))
        self.assertFalse(_player_visible_to_scope(player, "guild-2", False))
        self.assertFalse(_player_visible_to_scope(player, None, False))
        self.assertTrue(_player_visible_to_scope(player, None, True))

    def test_replay_artifact_summary_formats_match_and_size(self) -> None:
        artifact = ReplayArtifactRecord(
            id=10,
            match_id="match-123",
            shard="steam",
            artifact_type="map_snapshot",
            artifact_name="route-summary",
            account_id="account.1234567890abcdef",
            player_name="Yuuki_Asuna---",
            map_name="Erangel",
            game_mode="squad-fpp",
            match_type="official",
            match_created_at_kst=datetime(2026, 6, 29, 1, 2, 3),
            storage_backend="local_file",
            storage_root="PUBG_REPLAY_DATA_DIR",
            relative_path="map_snapshot/steam/2026/06/29/match-123/route-summary.jpg",
            content_type="image/jpeg",
            size_bytes=2048,
            sha256="abc123",
            renderer_version="test",
            generated_at_kst=datetime(2026, 6, 29, 1, 3, 3),
        )

        body = format_replay_artifact_summary(artifact)

        self.assertIn("Yuuki_Asuna--- 최근 2D 스냅샷", body)
        self.assertIn("- match: match-123", body)
        self.assertIn("- map/mode: Erangel / squad-fpp", body)
        self.assertIn("- size: 2.0 KB", body)


if __name__ == "__main__":
    unittest.main()
