from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from pubg_ai.time_utils import now_kst


class PlayerRegistryError(RuntimeError):
    """Raised when player registration cannot be completed."""


@dataclass(frozen=True)
class DiscordCommandContext:
    user_id: str | None = None
    guild_id: str | None = None
    channel_id: str | None = None


@dataclass(frozen=True)
class RegisteredPlayer:
    id: int
    account_id: str
    shard: str
    current_name: str
    active: bool
    public_profile: bool
    registered_by_discord_user_id: str | None = None
    registered_guild_id: str | None = None
    registered_channel_id: str | None = None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


class PlayerRegistry:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def register_player(
        self,
        *,
        account_id: str,
        shard: str,
        current_name: str,
        public_profile: bool = True,
        context: DiscordCommandContext | None = None,
    ) -> RegisteredPlayer:
        account_id = _required_text(account_id, "account_id")
        shard = _required_text(shard, "shard").lower()
        current_name = _required_text(current_name, "current_name")
        context = context or DiscordCommandContext()
        timestamp = _mysql_kst_now()

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO registered_players (
                    account_id,
                    shard,
                    current_name,
                    active,
                    public_profile,
                    registered_by_discord_user_id,
                    registered_guild_id,
                    registered_channel_id,
                    created_at_kst,
                    updated_at_kst
                )
                VALUES (%s, %s, %s, 1, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    current_name = VALUES(current_name),
                    active = 1,
                    public_profile = VALUES(public_profile),
                    registered_by_discord_user_id = VALUES(registered_by_discord_user_id),
                    registered_guild_id = VALUES(registered_guild_id),
                    registered_channel_id = VALUES(registered_channel_id),
                    updated_at_kst = VALUES(updated_at_kst)
                """,
                (
                    account_id,
                    shard,
                    current_name,
                    public_profile,
                    context.user_id,
                    context.guild_id,
                    context.channel_id,
                    timestamp,
                    timestamp,
                ),
            )
            player = self.get_player(account_id=account_id, shard=shard, include_inactive=True)
            if player is None:
                raise PlayerRegistryError("registered player could not be loaded after save.")

            cursor.execute(
                """
                INSERT INTO player_aliases (
                    registered_player_id,
                    account_id,
                    shard,
                    name,
                    source,
                    first_seen_at_kst,
                    last_seen_at_kst
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    registered_player_id = VALUES(registered_player_id),
                    source = VALUES(source),
                    last_seen_at_kst = VALUES(last_seen_at_kst)
                """,
                (
                    player.id,
                    account_id,
                    shard,
                    current_name,
                    "registration",
                    timestamp,
                    timestamp,
                ),
            )

        return player

    def get_player(
        self,
        *,
        shard: str,
        account_id: str | None = None,
        name: str | None = None,
        include_inactive: bool = False,
    ) -> RegisteredPlayer | None:
        shard = _required_text(shard, "shard").lower()
        conditions = ["shard = %s"]
        params: list[Any] = [shard]
        if account_id:
            conditions.append("account_id = %s")
            params.append(account_id)
        elif name:
            conditions.append("current_name = %s")
            params.append(name)
        else:
            raise PlayerRegistryError("account_id or name is required.")

        if not include_inactive:
            conditions.append("active = 1")

        query = (
            "SELECT id, account_id, shard, current_name, active, public_profile, "
            "registered_by_discord_user_id, registered_guild_id, registered_channel_id "
            "FROM registered_players WHERE "
            + " AND ".join(conditions)
            + " ORDER BY updated_at_kst DESC LIMIT 1"
        )

        with self.connection.cursor() as cursor:
            cursor.execute(query, params)
            row = cursor.fetchone()
        return _player_from_row(row) if row else None

    def list_players(
        self,
        *,
        shard: str | None = None,
        active_only: bool = True,
        limit: int = 100,
    ) -> list[RegisteredPlayer]:
        limit = max(1, min(limit, 500))
        conditions: list[str] = []
        params: list[Any] = []
        if shard:
            conditions.append("shard = %s")
            params.append(shard.lower())
        if active_only:
            conditions.append("active = 1")

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = (
            "SELECT id, account_id, shard, current_name, active, public_profile, "
            "registered_by_discord_user_id, registered_guild_id, registered_channel_id "
            f"FROM registered_players {where} "
            "ORDER BY shard ASC, current_name ASC LIMIT %s"
        )
        params.append(limit)

        with self.connection.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()
        return [_player_from_row(row) for row in rows]

    def unregister_player(
        self,
        *,
        shard: str,
        account_id: str | None = None,
        name: str | None = None,
    ) -> RegisteredPlayer | None:
        player = self.get_player(
            shard=shard,
            account_id=account_id,
            name=name,
            include_inactive=True,
        )
        if player is None:
            return None

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE registered_players
                SET active = 0, updated_at_kst = %s
                WHERE id = %s
                """,
                (_mysql_kst_now(), player.id),
            )
        return self.get_player(shard=player.shard, account_id=player.account_id, include_inactive=True)


def _player_from_row(row: dict[str, Any]) -> RegisteredPlayer:
    return RegisteredPlayer(
        id=int(row["id"]),
        account_id=str(row["account_id"]),
        shard=str(row["shard"]),
        current_name=str(row["current_name"]),
        active=bool(row["active"]),
        public_profile=bool(row["public_profile"]),
        registered_by_discord_user_id=row.get("registered_by_discord_user_id"),
        registered_guild_id=row.get("registered_guild_id"),
        registered_channel_id=row.get("registered_channel_id"),
    )


def _mysql_kst_now() -> datetime:
    return now_kst().replace(tzinfo=None)


def _required_text(value: str, label: str) -> str:
    text = value.strip()
    if not text:
        raise PlayerRegistryError(f"{label} is required.")
    return text
