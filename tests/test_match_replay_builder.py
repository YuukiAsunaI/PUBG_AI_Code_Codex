from __future__ import annotations

from typing import Any
import unittest

from pubg_ai.match_replay_builder import MatchReplayProcessor


class MatchReplayBuilderTests(unittest.TestCase):
    def test_payload_contains_every_participant_and_core_tactical_events(self) -> None:
        participants = [
            _participant("account.focus", "Focus", 1, registered=True),
            _participant("account.ally", "Ally", 1),
            _participant("account.enemy", "Enemy", 2),
            _participant("ai.bot", "Bot", 3, bot=True),
            _participant("account.silent", "Silent", 4),
        ]
        events: list[dict[str, Any]] = [{"_T": "LogMatchStart", "_D": _at(0)}]
        for second, x_offset in ((10, 0), (20, 2000)):
            events.extend(
                [
                    _position("account.focus", "Focus", 1, second, 10000 + x_offset, 10000),
                    _position("account.ally", "Ally", 1, second, 12000 + x_offset, 10000),
                    _position("account.enemy", "Enemy", 2, second, 20000 + x_offset, 10000),
                    _position("ai.bot", "Bot", 3, second, 30000 + x_offset, 10000),
                ]
            )
        events.extend(
            [
                {
                    "_T": "LogPlayerAttack",
                    "_D": _at(11),
                    "common": {"isGame": 1.0},
                    "elapsedTime": 11.0,
                    "attackType": "Weapon",
                    "attacker": _character("account.enemy", "Enemy", 2, 20000, 10000),
                    "weapon": {
                        "itemId": "Item_Weapon_BerylM762_C",
                        "category": "Weapon",
                        "subCategory": "Main",
                    },
                },
                {
                    "_T": "LogPlayerTakeDamage",
                    "_D": _at(12),
                    "common": {"isGame": 1.0},
                    "elapsedTime": 12.0,
                    "attacker": _character("account.enemy", "Enemy", 2, 20000, 10000),
                    "victim": _character("account.focus", "Focus", 1, 10000, 10000),
                    "damage": 24.5,
                    "damageReason": "TorsoShot",
                    "damageCauserName": "WeapBerylM762_C",
                },
                {
                    "_T": "LogPlayerMakeGroggy",
                    "_D": _at(13),
                    "common": {"isGame": 1.0},
                    "elapsedTime": 13.0,
                    "attacker": _character("account.focus", "Focus", 1, 10000, 10000),
                    "victim": _character("account.enemy", "Enemy", 2, 20000, 10000),
                    "damageReason": "HeadShot",
                    "damageCauserName": "WeapHK416_C",
                },
                {
                    "_T": "LogPlayerKillV2",
                    "_D": _at(14),
                    "common": {"isGame": 1.0},
                    "elapsedTime": 14.0,
                    "killer": _character("account.enemy", "Enemy", 2, 20000, 10000),
                    "victim": _character("account.ally", "Ally", 1, 12000, 10000),
                    "killerDamageInfo": {
                        "damageReason": "HeadShot",
                        "damageCauserName": "WeapBerylM762_C",
                        "distance": 8000,
                    },
                },
            ]
        )
        source = {
            "match": {
                "match_id": "match-all",
                "shard": "steam",
                "map_name": "Baltic_Main",
                "game_mode": "squad",
                "match_type": "official",
                "created_at_kst": "2026-08-01T09:00:00+09:00",
                "duration_seconds": 1800,
            },
            "participants": participants,
            "events": events,
        }

        processor = object.__new__(MatchReplayProcessor)
        payload = processor._build_payload(source)

        self.assertEqual(payload["scope"], "match")
        self.assertEqual(payload["team"]["member_count"], 5)
        self.assertEqual(payload["team"]["human_member_count"], 4)
        self.assertEqual(payload["team"]["bot_member_count"], 1)
        self.assertEqual(payload["player"]["account_id"], "account.focus")
        self.assertEqual(
            {track["account_id"] for track in payload["team_tracks"]},
            {"account.ally", "account.enemy", "ai.bot", "account.silent"},
        )
        silent_track = next(track for track in payload["team_tracks"] if track["account_id"] == "account.silent")
        self.assertEqual(silent_track["positions"], [])
        self.assertEqual(silent_track["combat_events"], [])
        self.assertEqual(payload["team"]["position_sample_count"], 8)

        tracks = {
            payload["player"]["account_id"]: {"combat_events": payload["combat_events"]},
            **{track["account_id"]: track for track in payload["team_tracks"]},
        }
        self.assertIn("hit_caused", {event["action"] for event in tracks["account.enemy"]["combat_events"]})
        self.assertIn("dbno_taken", {event["action"] for event in tracks["account.enemy"]["combat_events"]})
        self.assertIn("death", {event["action"] for event in tracks["account.ally"]["combat_events"]})
        self.assertTrue(any(item["actor_account_id"] == "account.enemy" for item in payload["engagements"]))
        self.assertTrue(
            all(
                "raw_event" not in event
                for track in tracks.values()
                for event in track["combat_events"]
            )
        )


def _participant(
    account_id: str,
    name: str,
    team_id: int,
    *,
    registered: bool = False,
    bot: bool = False,
) -> dict[str, Any]:
    return {
        "account_id": account_id,
        "name": name,
        "roster_id": f"roster-{team_id}",
        "team_id": team_id,
        "win_place": team_id,
        "kills": 0,
        "assists": 0,
        "damage_dealt": 0.0,
        "death_type": "alive",
        "is_ai_or_bot": bot,
        "is_registered": registered,
    }


def _position(
    account_id: str,
    name: str,
    team_id: int,
    second: int,
    x: float,
    y: float,
) -> dict[str, Any]:
    return {
        "_T": "LogPlayerPosition",
        "_D": _at(second),
        "common": {"isGame": 1.0},
        "elapsedTime": float(second),
        "numAlivePlayers": 4,
        "character": _character(account_id, name, team_id, x, y),
    }


def _character(
    account_id: str,
    name: str,
    team_id: int,
    x: float,
    y: float,
) -> dict[str, Any]:
    return {
        "accountId": account_id,
        "name": name,
        "teamId": team_id,
        "location": {"x": x, "y": y, "z": 100.0},
        "isInVehicle": False,
        "isDBNO": False,
    }


def _at(second: int) -> str:
    return f"2026-08-01T00:00:{second:02d}Z"


if __name__ == "__main__":
    unittest.main()
