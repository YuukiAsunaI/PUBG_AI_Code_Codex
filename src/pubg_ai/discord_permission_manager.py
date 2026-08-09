from __future__ import annotations

from dataclasses import asdict, dataclass

from pubg_ai.local_settings import DiscordPermissionSettings, LocalSettingsError, LocalSettingsStore


@dataclass(frozen=True)
class DiscordPermissionChange:
    changed: bool
    settings: DiscordPermissionSettings

    def to_record(self) -> dict[str, object]:
        return {
            "changed": self.changed,
            "settings": self.settings.to_record(),
        }


class DiscordPermissionManager:
    def __init__(self, store: LocalSettingsStore) -> None:
        self.store = store

    def load(self) -> DiscordPermissionSettings:
        return self.store.load_discord_permission_settings()

    def grant(self, *, user_id: str, group: str, guild_id: str | None = None) -> DiscordPermissionChange:
        user_id = _required_text(user_id, "user_id")
        group = _required_text(group, "group")
        guild_id = _optional_text(guild_id)
        changed = False

        def update(settings: DiscordPermissionSettings) -> DiscordPermissionSettings:
            nonlocal changed
            _ensure_known_group(settings, group)
            user_grants = _copy_grants(settings.user_grants)
            guild_user_grants = _copy_guild_grants(settings.guild_user_grants)
            if guild_id:
                grants = guild_user_grants.setdefault(guild_id, {}).setdefault(user_id, [])
            else:
                grants = user_grants.setdefault(user_id, [])
            if group not in grants:
                grants.append(group)
                changed = True
            return _updated_settings(
                settings,
                user_grants=user_grants,
                guild_user_grants=guild_user_grants,
            )

        saved = self.store.update_discord_permission_settings(update)
        return DiscordPermissionChange(changed=changed, settings=saved)

    def revoke(self, *, user_id: str, group: str, guild_id: str | None = None) -> DiscordPermissionChange:
        user_id = _required_text(user_id, "user_id")
        group = _required_text(group, "group")
        guild_id = _optional_text(guild_id)
        changed = False

        def update(settings: DiscordPermissionSettings) -> DiscordPermissionSettings:
            nonlocal changed
            _ensure_known_group(settings, group)
            user_grants = _copy_grants(settings.user_grants)
            guild_user_grants = _copy_guild_grants(settings.guild_user_grants)
            if guild_id:
                guild_grants = guild_user_grants.get(guild_id, {})
                grants = guild_grants.get(user_id, [])
                if group in grants:
                    grants.remove(group)
                    changed = True
                if not grants:
                    guild_grants.pop(user_id, None)
                if not guild_grants:
                    guild_user_grants.pop(guild_id, None)
            else:
                grants = user_grants.get(user_id, [])
                if group in grants:
                    grants.remove(group)
                    changed = True
                if not grants:
                    user_grants.pop(user_id, None)
            return _updated_settings(
                settings,
                user_grants=user_grants,
                guild_user_grants=guild_user_grants,
            )

        saved = self.store.update_discord_permission_settings(update)
        return DiscordPermissionChange(changed=changed, settings=saved)

    def add_global_admin(self, user_id: str) -> DiscordPermissionChange:
        user_id = _required_text(user_id, "user_id")
        changed = False

        def update(settings: DiscordPermissionSettings) -> DiscordPermissionSettings:
            nonlocal changed
            admin_ids = list(settings.global_admin_user_ids)
            changed = user_id not in admin_ids
            if changed:
                admin_ids.append(user_id)
            return _updated_settings(settings, global_admin_user_ids=admin_ids)

        saved = self.store.update_discord_permission_settings(update)
        return DiscordPermissionChange(changed=changed, settings=saved)

    def remove_global_admin(self, user_id: str) -> DiscordPermissionChange:
        user_id = _required_text(user_id, "user_id")
        changed = False

        def update(settings: DiscordPermissionSettings) -> DiscordPermissionSettings:
            nonlocal changed
            admin_ids = [value for value in settings.global_admin_user_ids if value != user_id]
            changed = len(admin_ids) != len(settings.global_admin_user_ids)
            return _updated_settings(settings, global_admin_user_ids=admin_ids)

        saved = self.store.update_discord_permission_settings(update)
        return DiscordPermissionChange(changed=changed, settings=saved)


def _updated_settings(
    settings: DiscordPermissionSettings,
    *,
    user_grants: dict[str, list[str]] | None = None,
    guild_user_grants: dict[str, dict[str, list[str]]] | None = None,
    global_admin_user_ids: list[str] | None = None,
) -> DiscordPermissionSettings:
    return DiscordPermissionSettings(
        command_groups=settings.command_groups,
        user_grants=_normalize_grants(user_grants if user_grants is not None else settings.user_grants),
        guild_user_grants=_normalize_nested_grants(
            guild_user_grants if guild_user_grants is not None else settings.guild_user_grants
        ),
        global_admin_user_ids=sorted(
            set(global_admin_user_ids if global_admin_user_ids is not None else settings.global_admin_user_ids)
        ),
        updated_at=settings.updated_at,
    )


def settings_summary(settings: DiscordPermissionSettings) -> dict[str, object]:
    return asdict(settings)


def _ensure_known_group(settings: DiscordPermissionSettings, group: str) -> None:
    if group not in settings.command_groups:
        known = ", ".join(sorted(settings.command_groups))
        raise LocalSettingsError(f"unknown Discord permission group '{group}'. Known groups: {known}.")


def _copy_grants(value: dict[str, list[str]]) -> dict[str, list[str]]:
    return {key: list(groups) for key, groups in value.items()}


def _copy_guild_grants(value: dict[str, dict[str, list[str]]]) -> dict[str, dict[str, list[str]]]:
    return {
        guild_id: _copy_grants(grants)
        for guild_id, grants in value.items()
    }


def _normalize_grants(value: dict[str, list[str]]) -> dict[str, list[str]]:
    return {
        user_id: sorted(set(groups))
        for user_id, groups in value.items()
        if groups
    }


def _normalize_nested_grants(value: dict[str, dict[str, list[str]]]) -> dict[str, dict[str, list[str]]]:
    return {
        guild_id: normalized
        for guild_id, grants in value.items()
        if (normalized := _normalize_grants(grants))
    }


def _required_text(value: str, label: str) -> str:
    text = value.strip()
    if not text:
        raise LocalSettingsError(f"{label} is required.")
    return text


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None
