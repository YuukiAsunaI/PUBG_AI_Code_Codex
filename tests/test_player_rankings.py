from __future__ import annotations

from datetime import datetime
import unittest

from pubg_ai.player_rankings import PlayerRankingService, resolve_ranking_metric


class PlayerRankingServiceTests(unittest.TestCase):
    def test_builds_scoped_player_ranking_by_average_damage(self) -> None:
        connection = FakeConnection(
            [
                [
                    _ranking_row(
                        player_id=1,
                        account_id="account.one",
                        name="Alpha",
                        match_count=5,
                        wins=1,
                        kills=10,
                        assists=2,
                        deaths=4,
                        damage_dealt=1500.0,
                    ),
                    _ranking_row(
                        player_id=2,
                        account_id="account.two",
                        name="Bravo",
                        match_count=2,
                        wins=0,
                        kills=5,
                        assists=1,
                        deaths=1,
                        damage_dealt=900.0,
                    ),
                ]
            ]
        )

        ranking = PlayerRankingService(connection).get_player_ranking(
            shard="steam",
            metric="평딜",
            guild_id="guild-1",
            global_scope=False,
            limit=10,
        )

        self.assertEqual(ranking.metric, "avg_damage")
        self.assertEqual(ranking.metric_label, "평균 딜")
        self.assertEqual(ranking.guild_id, "guild-1")
        self.assertFalse(ranking.global_scope)
        self.assertEqual([row.player.current_name for row in ranking.rows], ["Bravo", "Alpha"])
        self.assertEqual(ranking.rows[0].rank, 1)
        self.assertAlmostEqual(ranking.rows[0].score, 450.0)
        self.assertAlmostEqual(ranking.rows[1].score, 300.0)
        self.assertEqual(connection.executed[0][1], ["steam", "guild-1", 1])

    def test_global_ranking_does_not_require_guild_scope(self) -> None:
        connection = FakeConnection(
            [
                [
                    _ranking_row(
                        player_id=1,
                        account_id="account.one",
                        name="Alpha",
                        match_count=5,
                        wins=2,
                        kills=10,
                        assists=5,
                        deaths=3,
                        damage_dealt=1200.0,
                    )
                ]
            ]
        )

        ranking = PlayerRankingService(connection).get_player_ranking(
            shard="steam",
            metric="kda",
            global_scope=True,
        )

        self.assertTrue(ranking.global_scope)
        self.assertIsNone(ranking.guild_id)
        self.assertEqual(ranking.rows[0].player.current_name, "Alpha")
        self.assertEqual(connection.executed[0][1], ["steam", 1])

    def test_resolve_metric_aliases(self) -> None:
        self.assertEqual(resolve_ranking_metric("승률").key, "win_rate")
        self.assertEqual(resolve_ranking_metric("킬").key, "kills")
        self.assertEqual(resolve_ranking_metric("unknown").key, "kda")


def _ranking_row(
    *,
    player_id: int,
    account_id: str,
    name: str,
    match_count: int,
    wins: int,
    kills: int,
    assists: int,
    deaths: int,
    damage_dealt: float,
) -> dict[str, object]:
    return {
        "id": player_id,
        "account_id": account_id,
        "shard": "steam",
        "current_name": name,
        "active": 1,
        "public_profile": 1,
        "registered_by_discord_user_id": "user-1",
        "registered_guild_id": "guild-1",
        "registered_channel_id": "channel-1",
        "match_count": match_count,
        "wins": wins,
        "kills": kills,
        "assists": assists,
        "deaths": deaths,
        "dbnos_caused": kills + 1,
        "dbnos_taken": 1,
        "damage_dealt": damage_dealt,
        "damage_taken": 800.0,
        "shots_fired": 100,
        "shots_hit": 25,
        "headshot_kills": 2,
        "avg_survival_seconds": 1200.0,
        "avg_movement_distance_m": 3000.0,
        "last_match_at_kst": datetime(2026, 6, 29, 1, 0, 0),
    }


class FakeConnection:
    def __init__(self, results: list[object]) -> None:
        self.results = list(results)
        self.executed: list[tuple[str, list[object]]] = []

    def cursor(self) -> "FakeCursor":
        return FakeCursor(self)


class FakeCursor:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, query: str, params: tuple[object, ...] | list[object]) -> None:
        self.connection.executed.append((query, list(params)))

    def fetchall(self) -> object:
        return self.connection.results.pop(0)


if __name__ == "__main__":
    unittest.main()
