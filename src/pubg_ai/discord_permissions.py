from __future__ import annotations

from dataclasses import dataclass

from pubg_ai.local_settings import DiscordPermissionSettings


@dataclass(frozen=True)
class DiscordCommandIdentity:
    user_id: str
    guild_id: str | None = None


class DiscordPermissionChecker:
    def __init__(self, settings: DiscordPermissionSettings) -> None:
        self.settings = settings

    def is_global_admin(self, identity: DiscordCommandIdentity) -> bool:
        return identity.user_id in self.settings.global_admin_user_ids

    def is_allowed(self, identity: DiscordCommandIdentity, command_group: str) -> bool:
        if self.is_global_admin(identity):
            return True

        if command_group in self.settings.user_grants.get(identity.user_id, []):
            return True

        if identity.guild_id:
            guild_grants = self.settings.guild_user_grants.get(identity.guild_id, {})
            if command_group in guild_grants.get(identity.user_id, []):
                return True

        return False

    def command_names(self, command_group: str) -> list[str]:
        return list(self.settings.command_groups.get(command_group, []))
