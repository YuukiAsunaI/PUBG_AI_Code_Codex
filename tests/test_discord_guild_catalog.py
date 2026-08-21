from __future__ import annotations

import unittest

from pubg_ai.discord_guild_catalog import list_discord_guild_catalog, sync_discord_guild_catalog


class DiscordGuildCatalogTests(unittest.TestCase):
    def test_catalog_merges_named_guilds_registration_counts_and_settings(self) -> None:
        connection = FakeConnection(
            [
                [{"guild_id": "100", "name": "Alpha", "ranking_scope": "guild"}],
                [
                    {"guild_id": "100", "registered_player_count": 2},
                    {"guild_id": "200", "registered_player_count": 1},
                ],
                [{"guild_id": "300"}],
            ]
        )

        catalog = list_discord_guild_catalog(
            connection,
            configured_guild_ids=["400"],
            ranking_scope_overrides={"200": "global"},
        )

        by_id = {item.guild_id: item for item in catalog}
        self.assertEqual(set(by_id), {"100", "200", "300", "400"})
        self.assertEqual(by_id["100"].name, "Alpha")
        self.assertEqual(by_id["100"].registered_player_count, 2)
        self.assertTrue(by_id["100"].known_to_bot)
        self.assertEqual(by_id["200"].ranking_scope, "global")
        self.assertFalse(by_id["200"].known_to_bot)

    def test_sync_upserts_each_unique_guild_without_exposing_tokens(self) -> None:
        connection = FakeConnection([])

        count = sync_discord_guild_catalog(
            connection,
            [
                {"guild_id": "100", "guild_name": "Alpha"},
                {"id": "100", "name": "Alpha renamed"},
                {"id": "200", "name": "Beta"},
            ],
        )

        self.assertEqual(count, 2)
        inserted = [params for query, params in connection.executions if "INSERT INTO discord_guilds" in query]
        self.assertEqual({params[0] for params in inserted}, {"100", "200"})
        self.assertIn(("100", "Alpha renamed"), {(params[0], params[1]) for params in inserted})


class FakeConnection:
    def __init__(self, results: list[object]) -> None:
        self.results = list(results)
        self.executions: list[tuple[str, tuple[object, ...]]] = []

    def cursor(self) -> "FakeCursor":
        return FakeCursor(self)


class FakeCursor:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, query: str, params: tuple[object, ...]) -> None:
        self.connection.executions.append((query, params))

    def fetchall(self) -> object:
        return self.connection.results.pop(0)


if __name__ == "__main__":
    unittest.main()
