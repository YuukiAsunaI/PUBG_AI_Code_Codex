from __future__ import annotations

import unittest

from pubg_ai.pubg_client import (
    MAX_PLAYER_LOOKUP_IDS,
    MAX_PLAYER_LOOKUP_NAMES,
    PubgApiError,
    PubgPlayer,
    PubgPlayerSnapshot,
    PubgPlayerLookupResult,
    PubgRateLimit,
    parse_match_payload,
    parse_player_lookup_payload,
    parse_player_snapshot_payload,
)


class PubgClientParsingTests(unittest.TestCase):
    def test_parse_player_lookup_payload(self) -> None:
        players = parse_player_lookup_payload(
            {
                "data": [
                    {
                        "type": "player",
                        "id": "account.test",
                        "attributes": {"name": "Yuuki_Asuna---"},
                    }
                ]
            },
            shard="steam",
        )

        self.assertEqual(players, [PubgPlayer(account_id="account.test", name="Yuuki_Asuna---", shard="steam")])

    def test_parse_player_lookup_payload_rejects_missing_data_list(self) -> None:
        with self.assertRaises(PubgApiError):
            parse_player_lookup_payload({"data": {}}, shard="steam")

    def test_lookup_result_single_finds_exact_match_when_multiple_returned(self) -> None:
        result = PubgPlayerLookupResult(
            players=[
                PubgPlayer(account_id="account.one", name="Other", shard="steam"),
                PubgPlayer(account_id="account.two", name="Yuuki_Asuna---", shard="steam"),
            ],
            rate_limit=PubgRateLimit(),
        )

        self.assertEqual(result.single("yuuki_asuna---").account_id, "account.two")

    def test_lookup_result_single_raises_on_empty(self) -> None:
        result = PubgPlayerLookupResult(players=[], rate_limit=PubgRateLimit())

        with self.assertRaises(PubgApiError):
            result.single("missing")

    def test_max_player_lookup_names_matches_official_limit(self) -> None:
        self.assertEqual(MAX_PLAYER_LOOKUP_NAMES, 10)
        self.assertEqual(MAX_PLAYER_LOOKUP_IDS, 10)

    def test_parse_player_snapshot_payload_extracts_match_ids(self) -> None:
        snapshots = parse_player_snapshot_payload(
            {
                "data": [
                    {
                        "type": "player",
                        "id": "account.test",
                        "attributes": {"name": "Yuuki_Asuna---"},
                        "relationships": {
                            "matches": {
                                "data": [
                                    {"type": "match", "id": "match-1"},
                                    {"type": "match", "id": "match-2"},
                                    {"type": "match", "id": "match-1"},
                                ]
                            }
                        },
                    }
                ]
            },
            shard="steam",
        )

        self.assertEqual(
            snapshots,
            [
                PubgPlayerSnapshot(
                    account_id="account.test",
                    name="Yuuki_Asuna---",
                    shard="steam",
                    match_ids=["match-1", "match-2"],
                    raw_payload={
                        "type": "player",
                        "id": "account.test",
                        "attributes": {"name": "Yuuki_Asuna---"},
                        "relationships": {
                            "matches": {
                                "data": [
                                    {"type": "match", "id": "match-1"},
                                    {"type": "match", "id": "match-2"},
                                    {"type": "match", "id": "match-1"},
                                ]
                            }
                        },
                    },
                )
            ],
        )

    def test_parse_match_payload_extracts_metadata_and_telemetry(self) -> None:
        details = parse_match_payload(
            {
                "data": {
                    "type": "match",
                    "id": "match-1",
                    "attributes": {
                        "shardId": "steam",
                        "mapName": "Erangel_Main",
                        "gameMode": "squad-fpp",
                        "matchType": "official",
                        "createdAt": "2026-06-27T12:34:56Z",
                        "duration": 1888,
                        "seasonState": "progress",
                        "isCustomMatch": False,
                    },
                    "relationships": {
                        "assets": {
                            "data": [
                                {"type": "asset", "id": "asset-telemetry"},
                            ]
                        }
                    },
                },
                "included": [
                    {
                        "type": "participant",
                        "id": "participant-1",
                        "attributes": {
                            "stats": {
                                "playerId": "account.test",
                                "name": "Yuuki_Asuna---",
                            }
                        },
                    },
                    {
                        "type": "asset",
                        "id": "asset-telemetry",
                        "attributes": {
                            "URL": "https://telemetry-cdn.playbattlegrounds.com/example.json",
                        },
                    },
                ],
            },
            shard="steam",
            rate_limit=PubgRateLimit(limit=10, remaining=9, reset_epoch=123),
        )

        self.assertEqual(details.match_id, "match-1")
        self.assertEqual(details.shard, "steam")
        self.assertEqual(details.map_name, "Erangel_Main")
        self.assertEqual(details.game_mode, "squad-fpp")
        self.assertEqual(details.match_type, "official")
        self.assertEqual(details.created_at, "2026-06-27T12:34:56Z")
        self.assertEqual(details.duration_seconds, 1888)
        self.assertEqual(details.season_state, "progress")
        self.assertFalse(details.is_custom_match)
        self.assertEqual(details.telemetry_url, "https://telemetry-cdn.playbattlegrounds.com/example.json")
        self.assertEqual(len(details.participants), 1)
        self.assertEqual(details.rate_limit.remaining, 9)

    def test_parse_match_payload_rejects_missing_data_object(self) -> None:
        with self.assertRaises(PubgApiError):
            parse_match_payload({"data": []}, shard="steam")


if __name__ == "__main__":
    unittest.main()
