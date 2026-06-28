from __future__ import annotations

from datetime import datetime
import unittest

from pubg_ai.discord_bot import (
    _player_visible_to_scope,
    format_player_list,
    format_replay_artifact_summary,
)
from pubg_ai.player_registry import RegisteredPlayer
from pubg_ai.replay_artifact_catalog import ReplayArtifactRecord


class DiscordBotFormattingTests(unittest.TestCase):
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
