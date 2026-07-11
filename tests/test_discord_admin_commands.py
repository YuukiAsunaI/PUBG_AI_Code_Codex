from __future__ import annotations

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

import pytest

from pubg_ai.config import AppConfig, DatabaseConfig, RuntimeConfig, SecretConfig
from pubg_ai.data_deletion_requests import DataDeletionRequest
from pubg_ai.discord_bot import create_discord_bot
from pubg_ai.discord_permissions import DiscordCommandIdentity, DiscordPermissionChecker
from pubg_ai.local_settings import DEFAULT_COMMAND_GROUPS, LocalSettingsStore
from pubg_ai.player_registry import RegisteredPlayer


pytestmark = pytest.mark.filterwarnings(
    "ignore:'asyncio.iscoroutinefunction' is deprecated.*:DeprecationWarning:discord\\..*"
)


class DiscordAdminCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_global_admin_grants_guild_permission_and_checker_updates(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            store, checker, bot = _bot_fixture(base_dir, global_admin_user_ids=["100"])
            ctx = FakeContext(user_id=100, guild_id=10)
            try:
                await bot.get_command("pubg-permission").callback(
                    ctx,
                    "<@!200>",
                    "register",
                    "allow",
                    None,
                )
            finally:
                await bot.close()

            settings = store.load_discord_permission_settings()
            self.assertEqual(settings.guild_user_grants["10"]["200"], ["register"])
            self.assertTrue(
                checker.is_allowed(
                    DiscordCommandIdentity(user_id="200", guild_id="10"),
                    "register",
                )
            )
            self.assertIn("Discord 권한 부여 완료", ctx.replies[-1])
            self.assertIn("#discord-permissions", ctx.replies[-1])

    async def test_guild_admin_cannot_change_another_guild(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            store, _, bot = _bot_fixture(
                base_dir,
                guild_user_grants={"10": {"100": ["admin"]}},
            )
            ctx = FakeContext(user_id=100, guild_id=10)
            try:
                await bot.get_command("pubg-permission").callback(
                    ctx,
                    "200",
                    "register",
                    "allow",
                    "20",
                )
            finally:
                await bot.close()

            settings = store.load_discord_permission_settings()
            self.assertNotIn("20", settings.guild_user_grants)
            self.assertEqual(
                ctx.replies[-1],
                "다른 Discord 서버의 권한은 글로벌 관리자만 변경할 수 있습니다.",
            )

    async def test_global_admin_updates_guild_ranking_scope(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            store, checker, bot = _bot_fixture(base_dir)
            store.save_discord_permission_settings(
                command_groups=DEFAULT_COMMAND_GROUPS,
                user_grants={},
                guild_user_grants={},
                global_admin_user_ids=["100"],
            )
            self.assertFalse(checker.is_global_admin(DiscordCommandIdentity(user_id="100")))
            ctx = FakeContext(user_id=100, guild_id=10)
            try:
                await bot.get_command("pubg-ranking-scope").callback(ctx, "global", None)
            finally:
                await bot.close()

            settings = store.load_discord_scope_settings()
            self.assertTrue(checker.is_global_admin(DiscordCommandIdentity(user_id="100")))
            self.assertEqual(settings.guild_ranking_scopes["10"], "global")
            self.assertIn("Discord 랭킹 범위 저장 완료", ctx.replies[-1])
            self.assertIn("#discord-scopes", ctx.replies[-1])

    async def test_global_settings_grant_updates_collector_settings(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            store, _, bot = _bot_fixture(
                base_dir,
                user_grants={"100": ["settings_write"]},
            )
            ctx = FakeContext(user_id=100, guild_id=10)
            try:
                await bot.get_command("pubg-settings").callback(
                    ctx,
                    "collector",
                    "120",
                    "50",
                    "5",
                )
                success_reply = ctx.replies[-1]
                await bot.get_command("pubg-settings").callback(
                    ctx,
                    "collector",
                    "30",
                    "101",
                    "11",
                )
            finally:
                await bot.close()

            settings = store.load_collector_settings()
            self.assertEqual(settings.poll_interval_seconds, 120)
            self.assertEqual(settings.cycle_player_limit, 50)
            self.assertEqual(settings.player_lookup_chunk_size, 5)
            self.assertIn("Discord 수집 설정 저장 완료", success_reply)
            self.assertIn("#collector-settings", success_reply)
            self.assertIn("poll_seconds 60~300", ctx.replies[-1])
            self.assertNotIn(str(base_dir), ctx.replies[-1])

    async def test_guild_settings_grant_can_read_safe_summary_but_cannot_mutate(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            _, _, bot = _bot_fixture(
                base_dir,
                guild_user_grants={"10": {"100": ["settings_write"]}},
            )
            ctx = FakeContext(user_id=100, guild_id=10)
            try:
                await bot.get_command("pubg-settings").callback(ctx)
                summary = ctx.replies[-1]
                await bot.get_command("pubg-settings").callback(
                    ctx,
                    "collector",
                    "120",
                    "50",
                    "5",
                )
            finally:
                await bot.close()

            self.assertIn("PUBG AI 안전 설정", summary)
            self.assertNotIn("dummy-pubg-secret", summary)
            self.assertNotIn("dummy-discord-secret", summary)
            self.assertNotIn(str(base_dir), summary)
            self.assertEqual(
                ctx.replies[-1],
                "전역 settings_write 권한 또는 글로벌 관리자 권한이 있어야 설정을 변경할 수 있습니다.",
            )

    async def test_global_admin_updates_public_profile_default(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            store, _, bot = _bot_fixture(base_dir, global_admin_user_ids=["100"])
            ctx = FakeContext(user_id=100, guild_id=10)
            try:
                await bot.get_command("pubg-settings").callback(
                    ctx,
                    "public-profile",
                    "private",
                    None,
                    None,
                )
            finally:
                await bot.close()

            settings = store.load_discord_scope_settings()
            self.assertFalse(settings.public_profile_default)
            self.assertIn("public_profile_default: private", ctx.replies[-1])
            self.assertIn("#discord-scopes", ctx.replies[-1])

    async def test_discord_settings_refuses_secret_and_storage_path_surfaces(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            _, _, bot = _bot_fixture(base_dir, global_admin_user_ids=["100"])
            ctx = FakeContext(user_id=100, guild_id=10)
            try:
                await bot.get_command("pubg-settings").callback(ctx, "secrets")
                secret_reply = ctx.replies[-1]
                await bot.get_command("pubg-settings").callback(ctx, "storage")
                storage_reply = ctx.replies[-1]
            finally:
                await bot.close()

            self.assertEqual(
                secret_reply,
                "비밀정보와 데이터베이스 설정은 Discord에서 조회하거나 변경하지 않습니다.",
            )
            self.assertNotIn("dummy-pubg-secret", secret_reply)
            self.assertNotIn("dummy-discord-secret", secret_reply)
            self.assertIn("로컬 프로그램에서만 변경", storage_reply)
            self.assertIn("#storage-settings", storage_reply)

    async def test_delete_data_command_creates_review_request_without_execution(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            _, _, bot = _bot_fixture(
                base_dir,
                guild_user_grants={"10": {"100": ["admin"]}},
            )
            player = RegisteredPlayer(
                id=1,
                account_id="account.test",
                shard="steam",
                current_name="Yuuki_Asuna---",
                active=False,
                public_profile=True,
                registered_guild_id="10",
            )
            request = DataDeletionRequest(
                id=17,
                registered_player_id=1,
                account_id=player.account_id,
                shard=player.shard,
                player_name=player.current_name,
                deletion_scope="raw",
                status="pending",
                reason="검토 요청",
                requested_by_discord_user_id="100",
                requested_guild_id="10",
                requested_channel_id="20",
                requested_at_kst=datetime(2026, 7, 11, 20, 0, 0),
                expires_at_kst=datetime(2026, 7, 12, 20, 0, 0),
            )
            connection = FakeDatabaseConnection()
            registry = MagicMock()
            registry.get_player.return_value = player
            service = MagicMock()
            service.create_request.return_value = request
            ctx = FakeContext(user_id=100, guild_id=10)
            try:
                with (
                    patch("pubg_ai.discord_bot.connect_mysql", return_value=connection),
                    patch("pubg_ai.discord_bot.PlayerRegistry", return_value=registry),
                    patch("pubg_ai.discord_bot.DataDeletionRequestService", return_value=service),
                ):
                    await bot.get_command("pubg-delete-data").callback(
                        ctx,
                        "steam",
                        "Yuuki_Asuna---",
                        "raw",
                        reason="검토 요청",
                    )
            finally:
                await bot.close()

            service.create_request.assert_called_once()
            self.assertTrue(connection.closed)
            self.assertIn("삭제 검토 요청 생성 완료", ctx.replies[-1])
            self.assertIn("실제 삭제 미실행", ctx.replies[-1])
            self.assertIn("#data-deletions", ctx.replies[-1])


class FakeContext:
    def __init__(self, *, user_id: int, guild_id: int | None) -> None:
        self.author = FakeDiscordObject(user_id)
        self.guild = FakeDiscordObject(guild_id) if guild_id is not None else None
        self.channel = FakeDiscordObject(20)
        self.replies: list[str] = []

    async def reply(self, message: str, **_: object) -> None:
        self.replies.append(message)


class FakeDiscordObject:
    def __init__(self, object_id: int) -> None:
        self.id = object_id


class FakeDatabaseConnection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _bot_fixture(
    base_dir: Path,
    *,
    global_admin_user_ids: list[str] | None = None,
    user_grants: dict[str, list[str]] | None = None,
    guild_user_grants: dict[str, dict[str, list[str]]] | None = None,
) -> tuple[LocalSettingsStore, DiscordPermissionChecker, object]:
    store = LocalSettingsStore(base_dir / "config" / "local_settings.json", base_dir=base_dir)
    settings = store.save_discord_permission_settings(
        command_groups=DEFAULT_COMMAND_GROUPS,
        user_grants=user_grants or {},
        guild_user_grants=guild_user_grants or {},
        global_admin_user_ids=global_admin_user_ids or [],
    )
    checker = DiscordPermissionChecker(settings)
    config = RuntimeConfig(
        app=AppConfig(
            raw_data_dir=base_dir / "raw",
            replay_data_dir=base_dir / "replay",
            local_web_base_url="http://127.0.0.1:8000",
        ),
        database=DatabaseConfig(),
        secrets=SecretConfig(
            pubg_api_key="dummy-pubg-secret",
            discord_bot_token="dummy-discord-secret",
        ),
    )
    bot = create_discord_bot(
        config=config,
        permission_checker=checker,
        scope_settings_store=store,
    )
    return store, checker, bot


if __name__ == "__main__":
    unittest.main()
