from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest

from pubg_ai.database import (
    DatabaseError,
    _ensure_api_fetch_job_unique_index,
    _ensure_combat_vehicle_hit_columns,
    _ensure_replay_artifact_versioned_unique_index,
    mysql_transaction,
)
from pubg_ai.match_collection import RegisteredPlayerMatchCollector
from pubg_ai.match_job_processor import MatchJobProcessor
from pubg_ai.raw_storage import RawPayloadStore
from pubg_ai.telemetry_combat_processor import TelemetryCombatProcessor
from pubg_ai.telemetry_processing_state import (
    list_pending_telemetry_payloads,
    parser_version_rank,
    pending_tracked_account_ids,
    upsert_processing_states,
)
from pubg_ai.weapon_stats import PlayerMatchCombatSummary, WeaponCombatStats


class TransactionTests(unittest.TestCase):
    def test_commits_successful_transaction(self) -> None:
        connection = TransactionConnection()

        with mysql_transaction(connection):
            connection.actions.append("work")

        self.assertEqual(connection.actions, ["begin", "work", "commit"])

    def test_rolls_back_failed_transaction(self) -> None:
        connection = TransactionConnection()

        with self.assertRaisesRegex(RuntimeError, "insert failed"):
            with mysql_transaction(connection):
                raise RuntimeError("insert failed")

        self.assertEqual(connection.actions, ["begin", "rollback"])

    def test_combat_replacement_rolls_back_after_partial_insert_failure(self) -> None:
        connection = TransactionConnection(fail_on="INSERT INTO player_weapon_match_stats")
        processor = TelemetryCombatProcessor(connection, RawPayloadStore(Path("unused")))

        with self.assertRaisesRegex(RuntimeError, "forced SQL failure"):
            processor._replace_combat_rows(
                match_id="match-1",
                tracked_account_ids={"account-1"},
                summaries=[PlayerMatchCombatSummary(match_id="match-1", account_id="account-1")],
                weapon_stats=[
                    WeaponCombatStats(
                        match_id="match-1",
                        account_id="account-1",
                        weapon_code="WeapBerylM762_C",
                    )
                ],
            )

        self.assertIn("begin", connection.actions)
        self.assertIn("rollback", connection.actions)
        self.assertNotIn("commit", connection.actions)


class ProcessingStateTests(unittest.TestCase):
    def test_queries_pending_accounts_by_processor_and_version(self) -> None:
        connection = RoutingConnection(
            payload_rows=[
                {
                    "id": 1,
                    "match_id": "match-1",
                    "shard": "steam",
                    "relative_path": "telemetry.json",
                    "compression": "none",
                }
            ],
            account_rows=[{"account_id": "account-late"}],
        )

        payloads = list_pending_telemetry_payloads(
            connection,
            processor_name="items",
            parser_version="items-v1",
            limit=10,
            force=False,
        )
        accounts = pending_tracked_account_ids(
            connection,
            match_id="match-1",
            shard="steam",
            processor_name="items",
            parser_version="items-v1",
            force=False,
        )

        self.assertEqual(payloads[0]["match_id"], "match-1")
        self.assertEqual(accounts, {"account-late"})
        sql = "\n".join(query for query, _ in connection.cursor_obj.executed)
        self.assertIn("player_telemetry_processing_states", sql)
        self.assertIn("SUBSTRING_INDEX(states.parser_version, '-v', -1)", sql)
        self.assertIn("< %s", sql)
        self.assertEqual(connection.cursor_obj.executed[0][1], ("items", 1, 10))
        self.assertEqual(connection.cursor_obj.executed[1][1], ("match-1", "items", "steam", 1))

    def test_zero_output_is_still_marked_complete(self) -> None:
        connection = RoutingConnection()

        upsert_processing_states(
            connection,
            match_id="match-1",
            account_ids={"account-1"},
            processor_name="items",
            parser_version="items-v1",
            output_counts={},
        )

        query, rows = connection.cursor_obj.executed_many[-1]
        self.assertIn("player_telemetry_processing_states", query)
        self.assertIn("VALUES(parser_version)", query)
        self.assertIn(">= CAST(SUBSTRING_INDEX(parser_version", query)
        self.assertEqual(rows[0][4], 0)

    def test_parser_version_rank_is_monotonic_and_rejects_unknown_format(self) -> None:
        self.assertLess(parser_version_rank("items-v3"), parser_version_rank("items-v4"))
        self.assertEqual(parser_version_rank("items-v12"), 12)
        self.assertEqual(parser_version_rank("legacy"), 0)


class QueueUniquenessTests(unittest.TestCase):
    def test_combat_vehicle_hit_migration_backfills_existing_hits(self) -> None:
        cursor = CombatColumnMigrationCursor({})

        changed = _ensure_combat_vehicle_hit_columns(cursor, "pubg_ai")

        self.assertTrue(changed)
        alter_queries = [query for query, _ in cursor.executed if "ALTER TABLE" in query]
        update_queries = [query for query, _ in cursor.executed if "UPDATE player_" in query]
        self.assertEqual(len(alter_queries), 2)
        self.assertEqual(len(update_queries), 2)
        self.assertTrue(all("character_hits = shots_hit" in query for query in update_queries))

    def test_combat_vehicle_hit_migration_is_idempotent(self) -> None:
        columns = {"character_hits", "vehicle_hits", "vehicle_damage_dealt"}
        cursor = CombatColumnMigrationCursor(
            {
                "player_match_combat_summaries": columns,
                "player_weapon_match_stats": columns,
            }
        )

        changed = _ensure_combat_vehicle_hit_columns(cursor, "pubg_ai")

        self.assertFalse(changed)
        self.assertFalse(any("ALTER TABLE" in query for query, _ in cursor.executed))

    def test_schema_migration_adds_unique_index_when_missing(self) -> None:
        cursor = MigrationCursor(duplicate_groups=0, index_exists=False)

        changed = _ensure_api_fetch_job_unique_index(cursor, "pubg_ai")

        self.assertTrue(changed)
        self.assertTrue(any("ALTER TABLE api_fetch_jobs" in query for query, _ in cursor.executed))

    def test_schema_migration_refuses_existing_duplicates(self) -> None:
        cursor = MigrationCursor(duplicate_groups=2, index_exists=False)

        with self.assertRaises(DatabaseError):
            _ensure_api_fetch_job_unique_index(cursor, "pubg_ai")

    def test_replay_artifact_migration_replaces_legacy_unique_index(self) -> None:
        cursor = ReplayIndexMigrationCursor(
            ["match_id", "artifact_type", "artifact_name", "account_id"]
        )

        changed = _ensure_replay_artifact_versioned_unique_index(cursor, "pubg_ai")

        self.assertTrue(changed)
        alter_sql = next(query for query, _ in cursor.executed if "ALTER TABLE" in query)
        self.assertIn("DROP INDEX uq_replay_artifacts", alter_sql)
        self.assertIn("renderer_version", alter_sql)

    def test_replay_artifact_migration_is_idempotent(self) -> None:
        cursor = ReplayIndexMigrationCursor(
            ["match_id", "artifact_type", "artifact_name", "account_id", "renderer_version"]
        )

        changed = _ensure_replay_artifact_versioned_unique_index(cursor, "pubg_ai")

        self.assertFalse(changed)
        self.assertFalse(any("ALTER TABLE" in query for query, _ in cursor.executed))

    def test_match_enqueue_treats_duplicate_upsert_as_existing(self) -> None:
        connection = QueueConnection(insert_rowcount=0)
        collector = RegisteredPlayerMatchCollector(connection, object())  # type: ignore[arg-type]

        inserted = collector._enqueue_match_job("steam", "match-1")

        self.assertFalse(inserted)
        self.assertIn("ON DUPLICATE KEY UPDATE id = id", connection.cursor_obj.last_insert_sql)

    def test_telemetry_enqueue_treats_duplicate_upsert_as_existing(self) -> None:
        connection = QueueConnection(insert_rowcount=0)
        processor = MatchJobProcessor(connection, object(), object())  # type: ignore[arg-type]
        match = SimpleNamespace(telemetry_url="https://telemetry-cdn.pubg.com/a", shard="steam", match_id="match-1")

        status = processor._ensure_telemetry_job(match)  # type: ignore[arg-type]

        self.assertEqual(status, "existing")
        self.assertIn("ON DUPLICATE KEY UPDATE id = id", connection.cursor_obj.last_insert_sql)


class TransactionConnection:
    def __init__(self, fail_on: str | None = None) -> None:
        self.actions: list[str] = []
        self.cursor_obj = TransactionCursor(fail_on=fail_on)

    def begin(self) -> None:
        self.actions.append("begin")

    def commit(self) -> None:
        self.actions.append("commit")

    def rollback(self) -> None:
        self.actions.append("rollback")

    def cursor(self) -> "TransactionCursor":
        return self.cursor_obj


class TransactionCursor:
    def __init__(self, fail_on: str | None = None) -> None:
        self.fail_on = fail_on

    def __enter__(self) -> "TransactionCursor":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def execute(self, query: str, params: object = None) -> None:
        if self.fail_on and self.fail_on in query:
            raise RuntimeError("forced SQL failure")

    def executemany(self, query: str, params: object) -> None:
        if self.fail_on and self.fail_on in query:
            raise RuntimeError("forced SQL failure")


class RoutingConnection:
    def __init__(
        self,
        payload_rows: list[dict[str, object]] | None = None,
        account_rows: list[dict[str, object]] | None = None,
    ) -> None:
        self.cursor_obj = RoutingCursor(payload_rows or [], account_rows or [])

    def cursor(self) -> "RoutingCursor":
        return self.cursor_obj


class RoutingCursor:
    def __init__(self, payload_rows: list[dict[str, object]], account_rows: list[dict[str, object]]) -> None:
        self.payload_rows = payload_rows
        self.account_rows = account_rows
        self.query = ""
        self.executed: list[tuple[str, object]] = []
        self.executed_many: list[tuple[str, list[tuple[object, ...]]]] = []

    def __enter__(self) -> "RoutingCursor":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def execute(self, query: str, params: object = None) -> None:
        self.query = query
        self.executed.append((query, params))

    def executemany(self, query: str, rows: list[tuple[object, ...]]) -> None:
        self.executed_many.append((query, rows))

    def fetchall(self) -> list[dict[str, object]]:
        if "SELECT DISTINCT players.account_id" in self.query:
            return self.account_rows
        return self.payload_rows


class MigrationCursor:
    def __init__(self, *, duplicate_groups: int, index_exists: bool) -> None:
        self.duplicate_groups = duplicate_groups
        self.index_exists = index_exists
        self.query = ""
        self.executed: list[tuple[str, object]] = []

    def execute(self, query: str, params: object = None) -> None:
        self.query = query
        self.executed.append((query, params))

    def fetchone(self) -> dict[str, object] | None:
        if "duplicate_groups" in self.query:
            return {"duplicate_groups": self.duplicate_groups}
        return {"found": 1} if self.index_exists else None


class ReplayIndexMigrationCursor:
    def __init__(self, columns: list[str]) -> None:
        self.columns = columns
        self.query = ""
        self.executed: list[tuple[str, object]] = []

    def execute(self, query: str, params: object = None) -> None:
        self.query = query
        self.executed.append((query, params))

    def fetchall(self) -> list[dict[str, object]]:
        return [{"column_name": column} for column in self.columns]


class CombatColumnMigrationCursor:
    def __init__(self, columns_by_table: dict[str, set[str]]) -> None:
        self.columns_by_table = columns_by_table
        self.query = ""
        self.params: object = None
        self.executed: list[tuple[str, object]] = []

    def execute(self, query: str, params: object = None) -> None:
        self.query = query
        self.params = params
        self.executed.append((query, params))

    def fetchall(self) -> list[dict[str, object]]:
        table_name = ""
        if isinstance(self.params, tuple) and len(self.params) >= 2:
            table_name = str(self.params[1])
        return [
            {"column_name": column}
            for column in self.columns_by_table.get(table_name, set())
        ]


class QueueConnection:
    def __init__(self, *, insert_rowcount: int) -> None:
        self.cursor_obj = QueueCursor(insert_rowcount)

    def cursor(self) -> "QueueCursor":
        return self.cursor_obj


class QueueCursor:
    def __init__(self, insert_rowcount: int) -> None:
        self.insert_rowcount = insert_rowcount
        self.rowcount = 0
        self.query = ""
        self.last_insert_sql = ""

    def __enter__(self) -> "QueueCursor":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def execute(self, query: str, params: object = None) -> None:
        self.query = query
        if "INSERT INTO api_fetch_jobs" in query:
            self.last_insert_sql = query
            self.rowcount = self.insert_rowcount
        else:
            self.rowcount = 0

    def fetchone(self) -> None:
        return None
