from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
import unittest

from pubg_ai.match_explorer import MatchExplorerService
from pubg_ai.raw_storage import RawPayloadStore


class MatchExplorerServiceTests(unittest.TestCase):
    def test_list_matches_can_filter_by_exact_participant_account_id(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store, match_row, _participants = _fixture(Path(temp_dir))
            listed_row = {
                **match_row,
                "participant_count": 3,
                "registered_participant_count": 1,
            }
            connection = FakeConnection([[{"total": 1}], [listed_row]])

            result = MatchExplorerService(connection, store).list_matches(
                shard="steam",
                account_id="account.alpha",
                telemetry_only=True,
            )

            self.assertEqual(result["total"], 1)
            self.assertEqual(result["matches"][0]["match_id"], "match-1")
            query_text = "\n".join(query for query, _params in connection.executed)
            self.assertIn("requested_participant.account_id = %s", query_text)
            self.assertEqual(connection.executed[0][1], ("steam", "account.alpha"))

    def test_detail_and_events_include_unregistered_participants(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store, match_row, participants = _fixture(Path(temp_dir))
            detail = MatchExplorerService(
                FakeConnection([[match_row], participants]),
                store,
            ).get_match_detail("match-1")

            self.assertIsNotNone(detail)
            assert detail is not None
            self.assertEqual(detail["summary"]["participants"], 3)
            self.assertEqual(detail["summary"]["registered_players"], 1)
            self.assertEqual(detail["summary"]["registered_player_names"], ["Alpha"])
            self.assertEqual(detail["telemetry"]["event_count"], 6)
            self.assertEqual(
                {row["name"] for row in detail["participants"]},
                {"Alpha", "Bravo", "Charlie"},
            )

            events = MatchExplorerService(
                FakeConnection([[match_row], participants]),
                store,
            ).list_events(
                match_id="match-1",
                domain="combat",
                account_id="account.bravo",
            )

            self.assertEqual(events["total"], 3)
            kill = next(row for row in events["events"] if row["event_type"] == "LogPlayerKillV2")
            self.assertEqual(kill["actor"]["name"], "Alpha")
            self.assertEqual(kill["target"]["name"], "Bravo")
            self.assertEqual(kill["weapon_name"], "M416")
            self.assertEqual(kill["distance_m"], 25.0)
            self.assertNotIn("LogPlayerPosition", [row["event_type"] for row in events["events"]])

    def test_event_search_uses_translated_weapon_name(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store, match_row, participants = _fixture(Path(temp_dir))
            events = MatchExplorerService(
                FakeConnection([[match_row], participants]),
                store,
            ).list_events(match_id="match-1", search="M416")

            self.assertGreaterEqual(events["total"], 2)
            self.assertTrue(all("M416" in row["summary"] for row in events["events"]))


class FakeConnection:
    def __init__(self, result_sets: list[list[dict[str, Any]]]) -> None:
        self.result_sets = list(result_sets)
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    def cursor(self) -> "FakeCursor":
        return FakeCursor(self)


class FakeCursor:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.current: list[dict[str, Any]] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self.connection.executed.append((query, params))
        self.current = self.connection.result_sets.pop(0)

    def fetchone(self) -> dict[str, Any] | None:
        return self.current[0] if self.current else None

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self.current)


def _fixture(root: Path) -> tuple[RawPayloadStore, dict[str, Any], list[dict[str, Any]]]:
    store = RawPayloadStore(root, compression="none")
    events = [
        {"_T": "LogMatchStart", "_D": "2026-08-01T00:00:00Z"},
        {
            "_T": "LogPlayerTakeDamage",
            "_D": "2026-08-01T00:00:10Z",
            "attacker": _character("account.alpha", "Alpha", 1, 1000, 1000),
            "victim": _character("account.bravo", "Bravo", 2, 3500, 1000),
            "damage": 26.5,
            "damageReason": "HeadShot",
            "damageCauserName": "WeapHK416_C",
        },
        {
            "_T": "LogPlayerMakeGroggy",
            "_D": "2026-08-01T00:00:12Z",
            "attacker": _character("account.alpha", "Alpha", 1, 1000, 1000),
            "victim": _character("account.bravo", "Bravo", 2, 3500, 1000),
            "damageCauserName": "WeapHK416_C",
        },
        {
            "_T": "LogPlayerKillV2",
            "_D": "2026-08-01T00:00:15Z",
            "killer": _character("account.alpha", "Alpha", 1, 1000, 1000),
            "victim": _character("account.bravo", "Bravo", 2, 3500, 1000),
            "killerDamageInfo": {
                "damageCauserName": "WeapHK416_C",
                "distance": 2500,
            },
        },
        {
            "_T": "LogItemPickup",
            "_D": "2026-08-01T00:00:20Z",
            "character": _character("account.bravo", "Bravo", 2, 3600, 1000),
            "item": {"itemId": "Item_Heal_FirstAid_C", "stackCount": 1},
        },
        {
            "_T": "LogPlayerPosition",
            "_D": "2026-08-01T00:00:21Z",
            "character": _character("account.charlie", "Charlie", 3, 8000, 9000),
        },
    ]
    stored = store.write_json(
        "telemetry",
        "steam",
        "match-1",
        events,
        datetime(2026, 8, 1, tzinfo=UTC),
    )
    match_row = {
        "match_id": "match-1",
        "shard": "steam",
        "map_name": "Baltic_Main",
        "game_mode": "squad",
        "match_type": "official",
        "team_mode": "squad",
        "perspective": "tpp",
        "season_state": "progress",
        "created_at_kst": datetime(2026, 8, 1, 9, 0),
        "duration_seconds": 1800,
        "is_custom_match": 0,
        "has_telemetry": 1,
        "telemetry_storage_root": "PUBG_RAW_DATA_DIR",
        "telemetry_relative_path": stored.relative_path,
        "telemetry_compression": stored.compression,
        "telemetry_size_bytes": stored.size_bytes,
        "telemetry_sha256": stored.sha256,
        "telemetry_fetched_at_kst": datetime(2026, 8, 1, 9, 40),
    }
    participants = [
        _participant("account.alpha", "Alpha", 1, 1, 3, 220.5, True),
        _participant("account.bravo", "Bravo", 2, 2, 1, 85.0, False),
        _participant("account.charlie", "Charlie", 3, 3, 0, 0.0, False),
    ]
    return store, match_row, participants


def _character(account_id: str, name: str, team_id: int, x: float, y: float) -> dict[str, Any]:
    return {
        "accountId": account_id,
        "name": name,
        "teamId": team_id,
        "location": {"x": x, "y": y, "z": 100.0},
    }


def _participant(
    account_id: str,
    name: str,
    team_id: int,
    win_place: int,
    kills: int,
    damage: float,
    registered: bool,
) -> dict[str, Any]:
    return {
        "match_id": "match-1",
        "account_id": account_id,
        "name": name,
        "roster_id": f"roster-{team_id}",
        "team_id": team_id,
        "win_place": win_place,
        "kills": kills,
        "assists": 0,
        "damage_dealt": damage,
        "death_type": "alive" if win_place == 1 else "byplayer",
        "is_ai_or_bot": 0,
        "is_registered": int(registered),
        "raw_stats": {},
    }


if __name__ == "__main__":
    unittest.main()
