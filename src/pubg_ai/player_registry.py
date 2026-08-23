from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from pubg_ai.pubg_client import PubgApiClient, PubgPlayer
from pubg_ai.time_utils import now_kst


class PlayerRegistryError(RuntimeError):
    """Raised when player registration cannot be completed."""


@dataclass(frozen=True)
class DiscordCommandContext:
    user_id: str | None = None
    guild_id: str | None = None
    channel_id: str | None = None


@dataclass(frozen=True)
class PlayerDiscordRegistration:
    id: int
    registered_player_id: int
    guild_id: str
    channel_id: str | None = None
    registered_by_discord_user_id: str | None = None
    active: bool = True
    created_at_kst: datetime | None = None
    updated_at_kst: datetime | None = None

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["created_at_kst"] = _datetime_record(self.created_at_kst)
        record["updated_at_kst"] = _datetime_record(self.updated_at_kst)
        return record


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
    discord_registrations: tuple[PlayerDiscordRegistration, ...] | None = None

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["discord_registrations"] = [
            registration.to_record()
            for registration in (self.discord_registrations or ())
        ]
        return record

    def is_registered_in_guild(self, guild_id: str | None) -> bool:
        if not guild_id:
            return False
        if self.discord_registrations is not None:
            return any(
                registration.active and registration.guild_id == guild_id
                for registration in self.discord_registrations
            )
        return self.registered_guild_id == guild_id


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
                    registered_by_discord_user_id = COALESCE(
                        registered_players.registered_by_discord_user_id,
                        VALUES(registered_by_discord_user_id)
                    ),
                    registered_guild_id = COALESCE(
                        registered_players.registered_guild_id,
                        VALUES(registered_guild_id)
                    ),
                    registered_channel_id = COALESCE(
                        registered_players.registered_channel_id,
                        VALUES(registered_channel_id)
                    ),
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

            if context.guild_id:
                self.add_discord_registration(
                    registered_player_id=player.id,
                    guild_id=context.guild_id,
                    channel_id=context.channel_id,
                    registered_by_discord_user_id=context.user_id,
                )

        refreshed = self.get_player(account_id=account_id, shard=shard, include_inactive=True)
        if refreshed is None:
            raise PlayerRegistryError("registered player could not be loaded after Discord save.")
        return refreshed

    def register_player_by_name(
        self,
        *,
        pubg_client: PubgApiClient,
        shard: str,
        player_name: str,
        public_profile: bool = True,
        context: DiscordCommandContext | None = None,
    ) -> RegisteredPlayer:
        pubg_player = pubg_client.lookup_player_by_name(shard, player_name)
        return self.register_resolved_player(
            pubg_player=pubg_player,
            public_profile=public_profile,
            context=context,
        )

    def register_resolved_player(
        self,
        *,
        pubg_player: PubgPlayer,
        public_profile: bool = True,
        context: DiscordCommandContext | None = None,
    ) -> RegisteredPlayer:
        return self.register_player(
            account_id=pubg_player.account_id,
            shard=pubg_player.shard,
            current_name=pubg_player.name,
            public_profile=public_profile,
            context=context,
        )

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
        if not row:
            return None
        player_id = int(row["id"])
        registrations = self._load_discord_registrations([player_id]).get(player_id, ())
        return _player_from_row(row, registrations)

    def list_players(
        self,
        *,
        shard: str | None = None,
        registered_guild_id: str | None = None,
        active_only: bool = True,
        limit: int = 100,
    ) -> list[RegisteredPlayer]:
        limit = max(1, min(limit, 500))
        conditions: list[str] = []
        params: list[Any] = []
        if shard:
            conditions.append("shard = %s")
            params.append(shard.lower())
        if registered_guild_id:
            conditions.append(
                "EXISTS ("
                "SELECT 1 FROM player_discord_registrations registrations "
                "WHERE registrations.registered_player_id = registered_players.id "
                "AND registrations.guild_id = %s AND registrations.active = 1"
                ")"
            )
            params.append(registered_guild_id)
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
        registrations_by_player = self._load_discord_registrations(
            [int(row["id"]) for row in rows]
        )
        return [
            _player_from_row(row, registrations_by_player.get(int(row["id"]), ()))
            for row in rows
        ]

    def set_player_management(
        self,
        *,
        shard: str,
        account_id: str,
        active: bool | None = None,
        public_profile: bool | None = None,
    ) -> RegisteredPlayer | None:
        player = self.get_player(
            shard=shard,
            account_id=account_id,
            include_inactive=True,
        )
        if player is None:
            return None
        assignments: list[str] = []
        params: list[Any] = []
        if active is not None:
            assignments.append("active = %s")
            params.append(1 if active else 0)
        if public_profile is not None:
            assignments.append("public_profile = %s")
            params.append(1 if public_profile else 0)
        if not assignments:
            return player
        assignments.append("updated_at_kst = %s")
        params.extend([_mysql_kst_now(), player.id])
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE registered_players SET {', '.join(assignments)} WHERE id = %s",
                params,
            )
        return self.get_player(shard=player.shard, account_id=player.account_id, include_inactive=True)

    def add_discord_registration(
        self,
        *,
        registered_player_id: int,
        guild_id: str,
        channel_id: str | None = None,
        registered_by_discord_user_id: str | None = None,
    ) -> PlayerDiscordRegistration:
        guild_id = _required_text(guild_id, "guild_id")
        timestamp = _mysql_kst_now()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO player_discord_registrations (
                    registered_player_id,
                    guild_id,
                    channel_id,
                    registered_by_discord_user_id,
                    active,
                    created_at_kst,
                    updated_at_kst
                )
                VALUES (%s, %s, %s, %s, 1, %s, %s)
                ON DUPLICATE KEY UPDATE
                    channel_id = VALUES(channel_id),
                    registered_by_discord_user_id = VALUES(registered_by_discord_user_id),
                    active = 1,
                    updated_at_kst = VALUES(updated_at_kst)
                """,
                (
                    int(registered_player_id),
                    guild_id,
                    _optional_text(channel_id),
                    _optional_text(registered_by_discord_user_id),
                    timestamp,
                    timestamp,
                ),
            )
        registrations = self._load_discord_registrations([int(registered_player_id)])
        for registration in registrations.get(int(registered_player_id), ()):
            if registration.guild_id == guild_id:
                return registration
        raise PlayerRegistryError("Discord registration could not be loaded after save.")

    def remove_discord_registration(
        self,
        *,
        registered_player_id: int,
        guild_id: str,
    ) -> bool:
        guild_id = _required_text(guild_id, "guild_id")
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE player_discord_registrations
                SET active = 0, updated_at_kst = %s
                WHERE registered_player_id = %s
                  AND guild_id = %s
                  AND active = 1
                """,
                (_mysql_kst_now(), int(registered_player_id), guild_id),
            )
            return bool(cursor.rowcount)

    def unregister_player_from_guild(
        self,
        *,
        shard: str,
        guild_id: str,
        account_id: str | None = None,
        name: str | None = None,
    ) -> RegisteredPlayer | None:
        player = self.get_player(
            shard=shard,
            account_id=account_id,
            name=name,
            include_inactive=True,
        )
        if player is None or not player.is_registered_in_guild(guild_id):
            return None
        self.remove_discord_registration(
            registered_player_id=player.id,
            guild_id=guild_id,
        )
        return self.get_player(shard=player.shard, account_id=player.account_id, include_inactive=True)

    def _load_discord_registrations(
        self,
        registered_player_ids: list[int],
    ) -> dict[int, tuple[PlayerDiscordRegistration, ...]]:
        if not registered_player_ids:
            return {}
        placeholders = ", ".join(["%s"] * len(registered_player_ids))
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    registered_player_id,
                    guild_id,
                    channel_id,
                    registered_by_discord_user_id,
                    active,
                    created_at_kst,
                    updated_at_kst
                FROM player_discord_registrations
                WHERE active = 1
                  AND registered_player_id IN (
                """
                + placeholders
                + ") ORDER BY guild_id ASC",
                registered_player_ids,
            )
            rows = cursor.fetchall()
        grouped: dict[int, list[PlayerDiscordRegistration]] = {}
        for row in rows:
            registration = _discord_registration_from_row(row)
            grouped.setdefault(registration.registered_player_id, []).append(registration)
        return {player_id: tuple(items) for player_id, items in grouped.items()}

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


def _player_from_row(
    row: dict[str, Any],
    discord_registrations: tuple[PlayerDiscordRegistration, ...] = (),
) -> RegisteredPlayer:
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
        discord_registrations=discord_registrations,
    )


def _discord_registration_from_row(row: dict[str, Any]) -> PlayerDiscordRegistration:
    return PlayerDiscordRegistration(
        id=int(row["id"]),
        registered_player_id=int(row["registered_player_id"]),
        guild_id=str(row["guild_id"]),
        channel_id=row.get("channel_id"),
        registered_by_discord_user_id=row.get("registered_by_discord_user_id"),
        active=bool(row.get("active", True)),
        created_at_kst=row.get("created_at_kst"),
        updated_at_kst=row.get("updated_at_kst"),
    )


def _mysql_kst_now() -> datetime:
    return now_kst().replace(tzinfo=None)


def _required_text(value: str, label: str) -> str:
    text = value.strip()
    if not text:
        raise PlayerRegistryError(f"{label} is required.")
    return text


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _datetime_record(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
