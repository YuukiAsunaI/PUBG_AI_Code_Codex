from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from pubg_ai.time_utils import now_kst


@dataclass(frozen=True)
class DiscordGuildCatalogEntry:
    guild_id: str
    name: str | None
    ranking_scope: str
    registered_player_count: int
    known_to_bot: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def list_discord_guild_catalog(
    connection: Any,
    *,
    configured_guild_ids: Iterable[str] = (),
    ranking_scope_overrides: Mapping[str, str] | None = None,
    managed_guild_ids: Iterable[str] | None = None,
) -> list[DiscordGuildCatalogEntry]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT guild_id, name, ranking_scope
            FROM discord_guilds
            ORDER BY COALESCE(NULLIF(name, ''), guild_id)
            """,
            (),
        )
        stored_rows = list(cursor.fetchall())
        cursor.execute(
            """
            SELECT guild_id, COUNT(DISTINCT registered_player_id) AS registered_player_count
            FROM player_discord_registrations
            WHERE active = 1
            GROUP BY guild_id
            """,
            (),
        )
        player_rows = list(cursor.fetchall())
        cursor.execute(
            """
            SELECT DISTINCT guild_id
            FROM discord_permission_grants
            WHERE guild_id IS NOT NULL
              AND guild_id <> ''
            """,
            (),
        )
        permission_rows = list(cursor.fetchall())

    entries: dict[str, dict[str, Any]] = {}
    for row in stored_rows:
        guild_id = _optional_text(row.get("guild_id"))
        if not guild_id:
            continue
        entries[guild_id] = {
            "name": _optional_text(row.get("name")),
            "ranking_scope": _ranking_scope(row.get("ranking_scope")),
            "registered_player_count": 0,
            "known_to_bot": True,
        }

    for row in player_rows:
        guild_id = _optional_text(row.get("guild_id"))
        if not guild_id:
            continue
        entry = entries.setdefault(guild_id, _empty_entry())
        entry["registered_player_count"] = max(0, _int_value(row.get("registered_player_count")))

    for row in permission_rows:
        guild_id = _optional_text(row.get("guild_id"))
        if guild_id:
            entries.setdefault(guild_id, _empty_entry())

    for guild_id in configured_guild_ids:
        normalized = _optional_text(guild_id)
        if normalized:
            entries.setdefault(normalized, _empty_entry())

    for guild_id, scope in (ranking_scope_overrides or {}).items():
        normalized = _optional_text(guild_id)
        if normalized:
            entries.setdefault(normalized, _empty_entry())["ranking_scope"] = _ranking_scope(scope)

    if managed_guild_ids is not None:
        managed = {
            normalized
            for guild_id in managed_guild_ids
            if (normalized := _optional_text(guild_id)) is not None
        }
        entries = {
            guild_id: entry
            for guild_id, entry in entries.items()
            if guild_id in managed
        }
        for guild_id in managed:
            entries.setdefault(guild_id, _empty_entry())

    catalog = [
        DiscordGuildCatalogEntry(
            guild_id=guild_id,
            name=entry["name"],
            ranking_scope=entry["ranking_scope"],
            registered_player_count=entry["registered_player_count"],
            known_to_bot=entry["known_to_bot"],
        )
        for guild_id, entry in entries.items()
    ]
    catalog.sort(key=lambda item: ((item.name or "").casefold() or "\uffff", item.guild_id))
    return catalog


def list_stored_discord_guild_ids(connection: Any) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT guild_id FROM discord_guilds", ())
        rows = cursor.fetchall()
    return sorted(
        {
            guild_id
            for row in rows
            if (guild_id := _optional_text(row.get("guild_id"))) is not None
        }
    )


def sync_discord_guild_catalog(
    connection: Any,
    guilds: Iterable[Mapping[str, Any]],
    *,
    prune_missing: bool = False,
) -> int:
    normalized: dict[str, str | None] = {}
    for guild in guilds:
        guild_id = _optional_text(guild.get("guild_id") or guild.get("id"))
        if not guild_id:
            continue
        normalized[guild_id] = _optional_text(guild.get("guild_name") or guild.get("name"))

    synced_at = now_kst().replace(tzinfo=None)
    with connection.cursor() as cursor:
        for guild_id, name in normalized.items():
            cursor.execute(
                """
                INSERT INTO discord_guilds (
                    guild_id,
                    name,
                    ranking_scope,
                    public_profile_default,
                    created_at_kst,
                    updated_at_kst
                )
                VALUES (%s, %s, 'guild', 1, %s, %s)
                ON DUPLICATE KEY UPDATE
                    name = VALUES(name),
                    updated_at_kst = VALUES(updated_at_kst)
                """,
                (guild_id, name, synced_at, synced_at),
            )
        if prune_missing:
            managed_ids = sorted(normalized)
            if managed_ids:
                placeholders = ", ".join(["%s"] * len(managed_ids))
                cursor.execute(
                    f"DELETE FROM discord_guilds WHERE guild_id NOT IN ({placeholders})",
                    tuple(managed_ids),
                )
            else:
                cursor.execute("DELETE FROM discord_guilds", ())
    return len(normalized)


def _empty_entry() -> dict[str, Any]:
    return {
        "name": None,
        "ranking_scope": "guild",
        "registered_player_count": 0,
        "known_to_bot": False,
    }


def _ranking_scope(value: Any) -> str:
    return "global" if str(value or "").strip().lower() == "global" else "guild"


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
