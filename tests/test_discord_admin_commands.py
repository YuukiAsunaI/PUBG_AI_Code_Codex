from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pytest

from pubg_ai.config import AppConfig, DatabaseConfig, RuntimeConfig, SecretConfig
from pubg_ai.discord_bot import create_discord_bot
from pubg_ai.discord_permissions import DiscordCommandIdentity, DiscordPermissionChecker
from pubg_ai.local_settings import DEFAULT_COMMAND_GROUPS, LocalSettingsStore


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


class FakeContext:
    def __init__(self, *, user_id: int, guild_id: int | None) -> None:
        self.author = FakeDiscordObject(user_id)
        self.guild = FakeDiscordObject(guild_id) if guild_id is not None else None
        self.replies: list[str] = []

    async def reply(self, message: str, **_: object) -> None:
        self.replies.append(message)


class FakeDiscordObject:
    def __init__(self, object_id: int) -> None:
        self.id = object_id


def _bot_fixture(
    base_dir: Path,
    *,
    global_admin_user_ids: list[str] | None = None,
    guild_user_grants: dict[str, dict[str, list[str]]] | None = None,
) -> tuple[LocalSettingsStore, DiscordPermissionChecker, object]:
    store = LocalSettingsStore(base_dir / "config" / "local_settings.json", base_dir=base_dir)
    settings = store.save_discord_permission_settings(
        command_groups=DEFAULT_COMMAND_GROUPS,
        user_grants={},
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
        secrets=SecretConfig(),
    )
    bot = create_discord_bot(
        config=config,
        permission_checker=checker,
        scope_settings_store=store,
    )
    return store, checker, bot


if __name__ == "__main__":
    unittest.main()
