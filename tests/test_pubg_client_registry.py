from __future__ import annotations

import unittest

from pubg_ai.pubg_client import (
    MAX_PLAYER_LOOKUP_NAMES,
    PubgApiError,
    PubgPlayer,
    PubgPlayerLookupResult,
    PubgRateLimit,
    parse_player_lookup_payload,
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


if __name__ == "__main__":
    unittest.main()
