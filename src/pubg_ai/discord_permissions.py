from __future__ import annotations

from dataclasses import dataclass

from pubg_ai.local_settings import DiscordPermissionSettings


GROUP_IMPLICATIONS: dict[str, frozenset[str]] = {
    "admin": frozenset({"player_manage"}),
}


@dataclass(frozen=True)
class DiscordCommandIdentity:
    user_id: str
    guild_id: str | None = None


class DiscordPermissionChecker:
    def __init__(self, settings: DiscordPermissionSettings) -> None:
        self.settings = settings

    def is_global_admin(self, identity: DiscordCommandIdentity) -> bool:
        return identity.user_id in self.settings.global_admin_user_ids

    def is_globally_allowed(self, identity: DiscordCommandIdentity, command_group: str) -> bool:
        grants = self.settings.user_grants.get(identity.user_id, [])
        return self.is_global_admin(identity) or _grants_group(grants, command_group)

    def is_allowed(self, identity: DiscordCommandIdentity, command_group: str) -> bool:
        if self.is_globally_allowed(identity, command_group):
            return True

        if identity.guild_id:
            guild_grants = self.settings.guild_user_grants.get(identity.guild_id, {})
            if _grants_group(guild_grants.get(identity.user_id, []), command_group):
                return True

        return False

    def command_names(self, command_group: str) -> list[str]:
        return list(self.settings.command_groups.get(command_group, []))


def _grants_group(grants: list[str], command_group: str) -> bool:
    if command_group in grants:
        return True
    return any(command_group in GROUP_IMPLICATIONS.get(grant, ()) for grant in grants)
