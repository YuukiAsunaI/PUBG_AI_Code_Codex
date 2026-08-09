from __future__ import annotations

import pytest

from pubg_ai.weapon_stats import summarize_player_match_combat, summarize_weapon_combat_stats


ATTACKER = {"accountId": "account.attacker", "teamId": 1}
TEAMMATE = {"accountId": "account.teammate", "teamId": 1}
ENEMY = {"accountId": "account.enemy", "teamId": 2}
M416 = "WeapHK416_C"


def test_dealt_damage_and_hits_exclude_self_and_friendly_fire() -> None:
    events = [
        {
            "_T": "LogPlayerAttack",
            "common": {"isGame": 1},
            "attackType": "Weapon",
            "attacker": ATTACKER,
            "weapon": {"itemId": M416},
        },
        _damage_event(ATTACKER, TEAMMATE, 25.0, "Damage_Gun", M416),
        _damage_event(ATTACKER, ENEMY, 40.0, "Damage_Gun", M416),
        _damage_event(ATTACKER, ATTACKER, 10.0, "Damage_Explosion_Grenade", "ProjGrenade_C"),
    ]

    weapon = summarize_weapon_combat_stats(events, "match-1", {"account.attacker"})[0]
    summaries = {
        summary.account_id: summary
        for summary in summarize_player_match_combat(
            events,
            "match-1",
            {"account.attacker", "account.teammate"},
        )
    }

    assert weapon.shots_fired == 1
    assert weapon.shots_hit == 1
    assert weapon.damage_dealt == pytest.approx(40.0)
    assert summaries["account.attacker"].damage_dealt == pytest.approx(40.0)
    assert summaries["account.attacker"].damage_taken == pytest.approx(10.0)
    assert summaries["account.teammate"].damage_taken == pytest.approx(25.0)


def test_caused_dbnos_and_kills_exclude_teammates_but_taken_outcomes_remain() -> None:
    events = [
        _groggy_event(ATTACKER, TEAMMATE),
        _groggy_event(ATTACKER, ENEMY),
        _kill_event(ATTACKER, TEAMMATE),
        _kill_event(ATTACKER, ENEMY),
    ]
    summaries = {
        summary.account_id: summary
        for summary in summarize_player_match_combat(
            events,
            "match-1",
            {"account.attacker", "account.teammate"},
        )
    }

    assert summaries["account.attacker"].dbnos_caused == 1
    assert summaries["account.attacker"].kills == 1
    assert summaries["account.teammate"].dbnos_taken == 1
    assert summaries["account.teammate"].deaths == 1


def _damage_event(
    attacker: dict[str, object],
    victim: dict[str, object],
    damage: float,
    category: str,
    causer: str,
) -> dict[str, object]:
    return {
        "_T": "LogPlayerTakeDamage",
        "common": {"isGame": 1},
        "attacker": attacker,
        "victim": victim,
        "damage": damage,
        "damageTypeCategory": category,
        "damageCauserName": causer,
        "damageReason": "TorsoShot",
    }


def _groggy_event(
    attacker: dict[str, object], victim: dict[str, object]
) -> dict[str, object]:
    return {
        "_T": "LogPlayerMakeGroggy",
        "common": {"isGame": 1},
        "attacker": attacker,
        "victim": victim,
        "damageTypeCategory": "Damage_Gun",
        "damageCauserName": M416,
        "damageReason": "TorsoShot",
    }


def _kill_event(
    killer: dict[str, object], victim: dict[str, object]
) -> dict[str, object]:
    return {
        "_T": "LogPlayerKillV2",
        "common": {"isGame": 1},
        "killer": killer,
        "victim": victim,
        "isSuicide": False,
        "killerDamageInfo": {
            "damageTypeCategory": "Damage_Gun",
            "damageCauserName": M416,
            "damageReason": "TorsoShot",
        },
        "finishDamageInfo": {"damageTypeCategory": "Damage_None"},
    }
