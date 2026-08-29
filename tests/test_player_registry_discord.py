from __future__ import annotations

from datetime import datetime
import unittest
from unittest.mock import MagicMock

from pubg_ai.player_registry import PlayerRegistry


class PlayerRegistryDiscordTests(unittest.TestCase):
    def test_get_player_returns_all_active_discord_registrations(self) -> None:
        connection = FakeConnection()

        player = PlayerRegistry(connection).get_player(
            shard="steam",
            account_id="account.test",
            include_inactive=True,
        )

        self.assertIsNotNone(player)
        assert player is not None
        self.assertEqual(
            [item.guild_id for item in player.discord_registrations or ()],
            ["100", "200"],
        )
        self.assertTrue(player.is_registered_in_guild("100"))
        self.assertTrue(player.is_registered_in_guild("200"))
        self.assertFalse(player.is_registered_in_guild("300"))
        self.assertEqual(
            [item["guild_id"] for item in player.to_record()["discord_registrations"]],
            ["100", "200"],
        )

    def test_remove_one_guild_keeps_other_and_does_not_fall_back_to_legacy_field(self) -> None:
        connection = FakeConnection()
        registry = PlayerRegistry(connection)

        removed = registry.remove_discord_registration(
            registered_player_id=1,
            guild_id="100",
        )
        player = registry.get_player(
            shard="steam",
            account_id="account.test",
            include_inactive=True,
        )

        self.assertTrue(removed)
        self.assertIsNotNone(player)
        assert player is not None
        self.assertFalse(player.is_registered_in_guild("100"))
        self.assertTrue(player.is_registered_in_guild("200"))
        self.assertTrue(player.active)

    def test_add_reactivates_existing_guild_without_duplicate_link(self) -> None:
        connection = FakeConnection()
        connection.registrations["300"] = _registration(3, "300", active=False)
        registry = PlayerRegistry(connection)

        registration = registry.add_discord_registration(
            registered_player_id=1,
            guild_id="300",
            channel_id="333",
            registered_by_discord_user_id="999",
        )

        self.assertEqual(registration.guild_id, "300")
        self.assertEqual(registration.channel_id, "333")
        self.assertTrue(registration.active)
        self.assertEqual(len(connection.registrations), 3)

    def test_reregister_preserves_player_wide_public_profile_setting(self) -> None:
        connection = FakeConnection()
        connection.player["public_profile"] = 0

        player = PlayerRegistry(connection).register_player(
            account_id="account.test",
            shard="steam",
            current_name="Player Renamed",
            public_profile=True,
        )

        self.assertFalse(player.public_profile)
        registration_sql = next(
            query
            for query, _ in connection.executions
            if query.startswith("INSERT INTO registered_players")
        )
        self.assertNotIn("public_profile = VALUES(public_profile)", registration_sql)

    def test_list_players_filters_by_search_before_applying_discord_limit(self) -> None:
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.fetchall.return_value = []
        connection = MagicMock()
        connection.cursor.return_value = cursor

        players = PlayerRegistry(connection).list_players(
            shard="steam",
            registered_guild_id="100",
            search="ki",
            active_only=False,
            limit=25,
        )

        self.assertEqual(players, [])
        query, params = cursor.execute.call_args.args
        normalized_query = " ".join(query.split())
        self.assertIn(
            "current_name LIKE %s ESCAPE '=' OR account_id LIKE %s ESCAPE '='",
            normalized_query,
        )
        self.assertIn("registrations.guild_id = %s", normalized_query)
        self.assertIn(
            "CASE WHEN current_name LIKE %s ESCAPE '=' THEN 0 ELSE 1 END",
            normalized_query,
        )
        self.assertEqual(params, ["steam", "100", "%ki%", "%ki%", "ki%", 25])

    def test_list_players_page_counts_and_pages_after_guild_search(self) -> None:
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.fetchone.return_value = {"total": 40}
        cursor.fetchall.return_value = []
        connection = MagicMock()
        connection.cursor.return_value = cursor

        players, total = PlayerRegistry(connection).list_players_page(
            shard="steam",
            registered_guild_id="100",
            search="ki",
            active_only=False,
            limit=25,
            offset=25,
        )

        self.assertEqual(players, [])
        self.assertEqual(total, 40)
        count_call, page_call = cursor.execute.call_args_list
        count_query, count_params = count_call.args
        page_query, page_params = page_call.args
        self.assertIn("SELECT COUNT(*) AS total", " ".join(count_query.split()))
        self.assertIn("registrations.guild_id = %s", " ".join(count_query.split()))
        self.assertEqual(count_params, ["steam", "100", "%ki%", "%ki%"])
        self.assertIn("LIMIT %s OFFSET %s", " ".join(page_query.split()))
        self.assertEqual(
            page_params,
            ["steam", "100", "%ki%", "%ki%", "ki%", 25, 25],
        )

    def test_list_players_treats_pubg_nickname_wildcards_as_literal_text(self) -> None:
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.fetchall.return_value = []
        connection = MagicMock()
        connection.cursor.return_value = cursor

        PlayerRegistry(connection).list_players(
            shard="steam",
            registered_guild_id="100",
            search="Yuuki_%=",
            active_only=False,
            limit=25,
        )

        _query, params = cursor.execute.call_args.args
        self.assertEqual(
            params,
            [
                "steam",
                "100",
                "%Yuuki=_=%==%",
                "%Yuuki=_=%==%",
                "Yuuki=_=%==%",
                25,
            ],
        )


def _registration(identifier: int, guild_id: str, *, active: bool = True) -> dict[str, object]:
    timestamp = datetime(2026, 8, 23, 12, 0)
    return {
        "id": identifier,
        "registered_player_id": 1,
        "guild_id": guild_id,
        "channel_id": f"channel-{guild_id}",
        "registered_by_discord_user_id": f"user-{guild_id}",
        "active": 1 if active else 0,
        "created_at_kst": timestamp,
        "updated_at_kst": timestamp,
    }


class FakeConnection:
    def __init__(self) -> None:
        self.player = {
            "id": 1,
            "account_id": "account.test",
            "shard": "steam",
            "current_name": "Player",
            "active": 1,
            "public_profile": 1,
            "registered_by_discord_user_id": "user-100",
            "registered_guild_id": "100",
            "registered_channel_id": "channel-100",
        }
        self.registrations = {
            "100": _registration(1, "100"),
            "200": _registration(2, "200"),
        }
        self.executions: list[tuple[str, list[object]]] = []

    def cursor(self) -> "FakeCursor":
        return FakeCursor(self)


class FakeCursor:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.result: object = None
        self.rowcount = 0

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, query: str, params: object = None) -> None:
        values = list(params or [])
        normalized = " ".join(query.split())
        self.connection.executions.append((normalized, values))
        self.rowcount = 0
        if normalized.startswith("SELECT id, account_id"):
            self.result = dict(self.connection.player)
            return
        if normalized.startswith("INSERT INTO registered_players"):
            self.connection.player["current_name"] = str(values[2])
            self.connection.player["active"] = 1
            self.rowcount = 1
            return
        if normalized.startswith("INSERT INTO player_aliases"):
            self.rowcount = 1
            return
        if "FROM player_discord_registrations" in normalized and normalized.startswith("SELECT"):
            self.result = [
                dict(item)
                for item in sorted(self.connection.registrations.values(), key=lambda row: str(row["guild_id"]))
                if bool(item["active"])
            ]
            return
        if normalized.startswith("INSERT INTO player_discord_registrations"):
            player_id, guild_id, channel_id, user_id, created_at, updated_at = values
            existing = self.connection.registrations.get(str(guild_id))
            identifier = int(existing["id"]) if existing else len(self.connection.registrations) + 1
            self.connection.registrations[str(guild_id)] = {
                "id": identifier,
                "registered_player_id": int(player_id),
                "guild_id": str(guild_id),
                "channel_id": channel_id,
                "registered_by_discord_user_id": user_id,
                "active": 1,
                "created_at_kst": existing["created_at_kst"] if existing else created_at,
                "updated_at_kst": updated_at,
            }
            self.rowcount = 1
            return
        if normalized.startswith("UPDATE player_discord_registrations"):
            _, player_id, guild_id = values
            registration = self.connection.registrations.get(str(guild_id))
            if registration and registration["registered_player_id"] == int(player_id) and registration["active"]:
                registration["active"] = 0
                self.rowcount = 1
            self.result = None
            return
        raise AssertionError(f"unexpected SQL: {normalized}")

    def fetchone(self) -> object:
        return self.result

    def fetchall(self) -> object:
        return self.result


if __name__ == "__main__":
    unittest.main()
