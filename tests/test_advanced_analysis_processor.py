from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pubg_ai.advanced_analysis import PARSER_VERSION
from pubg_ai.advanced_analysis_processor import AdvancedAnalysisProcessor, PROCESSOR_NAME
from pubg_ai.raw_storage import RawPayloadStore


BASE = datetime(2026, 8, 30, tzinfo=timezone.utc)


def _event(kind: str, seconds: int, **values):
    return {
        "_T": kind,
        "_D": (BASE + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z"),
        "common": {"isGame": values.pop("is_game", 1.0)},
        **values,
    }


def _character(account_id: str, team_id: int, x: float = 0, y: float = 0):
    return {
        "accountId": account_id,
        "teamId": team_id,
        "location": {"x": x, "y": y, "z": 0},
    }


def test_processor_replaces_all_models_and_records_one_atomic_processing_state(tmp_path):
    store = RawPayloadStore(tmp_path, compression="none")
    player = _character("p1", 1)
    enemy = _character("enemy", 2, 5000, 0)
    stored = store.write_json(
        "telemetry",
        "steam",
        "match-1",
        [
            _event("LogParachuteLanding", 0, is_game=0.5, character=player),
            _event(
                "LogItemPickup",
                5,
                is_game=0.5,
                character=player,
                item={
                    "itemId": "Item_Weapon_HK416_C",
                    "category": "Weapon",
                    "subCategory": "Main",
                },
            ),
            _event(
                "LogPlayerAttack",
                30,
                attacker=player,
                attackType="Weapon",
                weapon={"itemId": "Item_Weapon_HK416_C"},
            ),
            _event(
                "LogPlayerTakeDamage",
                31,
                attacker=player,
                victim=enemy,
                damage=35,
                damageCauserName="WeapHK416_C",
                distance=5000,
            ),
            _event(
                "LogPlayerMakeGroggy",
                32,
                attacker=player,
                victim=enemy,
                damageCauserName="WeapHK416_C",
                distance=5000,
            ),
        ],
    )
    connection = FakeConnection(
        payload={
            "id": 1,
            "match_id": "match-1",
            "shard": "steam",
            "relative_path": stored.relative_path,
            "compression": stored.compression,
        },
        participants=[
            {"account_id": "p1", "team_id": 1, "is_ai_or_bot": 0},
            {"account_id": "enemy", "team_id": 2, "is_ai_or_bot": 0},
        ],
    )

    result = AdvancedAnalysisProcessor(connection, store).process_raw_telemetry(limit=5)

    assert result.candidate_payloads == 1
    assert result.parsed_payloads == 1
    assert result.failed_payloads == 0
    assert result.tracked_players == 1
    assert result.fight_episodes == 1
    assert result.team_summaries == 1
    assert result.loot_summaries == 1
    assert connection.begin_count == 1
    assert connection.commit_count == 1
    assert connection.rollback_count == 0
    delete_queries = [query for query, _ in connection.executed if "DELETE FROM player_" in query]
    assert len(delete_queries) == 4
    inserted_tables = "\n".join(query for query, _ in connection.executed_many)
    assert "INSERT INTO player_fight_episodes" in inserted_tables
    assert "INSERT INTO player_team_coordination_summaries" in inserted_tables
    assert "INSERT INTO player_loot_readiness_summaries" in inserted_tables
    assert "INSERT INTO player_telemetry_processing_states" in inserted_tables
    state_values = next(
        values
        for query, values in connection.executed_many
        if "player_telemetry_processing_states" in query
    )
    assert state_values[0][2] == PROCESSOR_NAME
    assert state_values[0][3] == PARSER_VERSION
    assert state_values[0][4] == 3


def test_processor_reports_invalid_telemetry_root_without_partial_transaction(tmp_path):
    store = RawPayloadStore(tmp_path, compression="none")
    stored = store.write_json("telemetry", "steam", "bad-match", {"not": "a list"})
    connection = FakeConnection(
        payload={
            "id": 2,
            "match_id": "bad-match",
            "shard": "steam",
            "relative_path": stored.relative_path,
            "compression": stored.compression,
        },
        participants=[{"account_id": "p1", "team_id": 1, "is_ai_or_bot": 0}],
    )

    result = AdvancedAnalysisProcessor(connection, store).process_raw_telemetry()

    assert result.parsed_payloads == 0
    assert result.failed_payloads == 1
    assert result.failure_details[0]["error_type"] == "AdvancedAnalysisProcessingError"
    assert connection.begin_count == 0
    assert connection.executed_many == []


class FakeConnection:
    def __init__(self, *, payload, participants):
        self.payload = payload
        self.participants = participants
        self.executed: list[tuple[str, list[object]]] = []
        self.executed_many: list[tuple[str, list[tuple[object, ...]]]] = []
        self.begin_count = 0
        self.commit_count = 0
        self.rollback_count = 0

    def cursor(self):
        return FakeCursor(self)

    def begin(self):
        self.begin_count += 1

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        self.rollback_count += 1


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, query, params):
        self.connection.executed.append((query, list(params)))
        if "FROM raw_telemetry_payloads raw_payloads" in query:
            self.rows = [self.connection.payload]
        elif "SELECT DISTINCT players.account_id" in query:
            self.rows = [{"account_id": "p1"}]
        elif "FROM match_participants" in query and "SELECT account_id" in query:
            self.rows = self.connection.participants
        else:
            self.rows = []

    def executemany(self, query, values):
        self.connection.executed_many.append((query, list(values)))

    def fetchall(self):
        return list(self.rows)
