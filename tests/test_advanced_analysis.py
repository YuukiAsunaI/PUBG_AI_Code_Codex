from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pubg_ai.advanced_analysis import (
    PARSER_VERSION,
    ParticipantContext,
    build_advanced_analysis,
)


BASE = datetime(2026, 8, 30, tzinfo=timezone.utc)


def event(kind: str, seconds: int, **values):
    return {
        "_T": kind,
        "_D": (BASE + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z"),
        "common": {"isGame": values.pop("is_game", 1.0)},
        **values,
    }


def character(account_id: str, team_id: int, x: float = 0, y: float = 0, **values):
    return {
        "accountId": account_id,
        "teamId": team_id,
        "location": {"x": x, "y": y, "z": 0},
        **values,
    }


def item(item_id: str, category: str, sub_category: str = ""):
    return {
        "itemId": item_id,
        "category": category,
        "subCategory": sub_category,
        "stackCount": 1,
    }


def sample_events():
    player = character("p1", 1)
    teammate = character("mate", 1)
    enemy = character("enemy", 2)
    return [
        event("LogParachuteLanding", 0, is_game=0.5, character=player),
        event(
            "LogItemPickup",
            10,
            is_game=0.5,
            character=player,
            item=item("Item_Weapon_HK416_C", "Weapon", "Main"),
        ),
        event(
            "LogItemPickup",
            15,
            is_game=0.5,
            character=player,
            item=item("Item_Armor_C_01_Lv2_C", "Equipment", "Vest"),
        ),
        event(
            "LogItemPickup",
            20,
            is_game=0.5,
            character=player,
            item=item("Item_Head_G_01_Lv2_C", "Equipment", "Helmet"),
        ),
        event(
            "LogItemPickup",
            25,
            is_game=0.5,
            character=player,
            item=item("Item_Heal_FirstAid_C", "Use", "Heal"),
        ),
        event(
            "LogItemPickupFromLootbox",
            30,
            is_game=0.5,
            character=player,
            item=item("Item_Weapon_M24_C", "Weapon", "Main"),
        ),
        event(
            "LogItemPickup",
            35,
            is_game=0.5,
            character=player,
            item=item("Item_Attach_Weapon_Upper_Scope4x_C", "Attachment", "Upper"),
        ),
        event(
            "LogItemPickup",
            40,
            is_game=0.5,
            character=player,
            item=item("Item_Weapon_Grenade_C", "Weapon", "Throwable"),
        ),
        event(
            "LogGameStatePeriodic",
            60,
            gameState={
                "elapsedTime": 60,
                "safetyZonePosition": {"x": 0, "y": 0, "z": 0},
                "safetyZoneRadius": 10000,
            },
        ),
        event(
            "LogPlayerPosition",
            60,
            elapsedTime=60,
            character=character("p1", 1, 15000, 0, isInBlueZone=True),
            vehicle={},
        ),
        event(
            "LogPlayerPosition",
            60,
            elapsedTime=60,
            character=character("mate", 1, 50000, 0),
            vehicle={},
        ),
        event(
            "LogGameStatePeriodic",
            70,
            gameState={
                "elapsedTime": 70,
                "safetyZonePosition": {"x": 0, "y": 0, "z": 0},
                "safetyZoneRadius": 10000,
            },
        ),
        event(
            "LogPlayerPosition",
            70,
            elapsedTime=70,
            character=character("p1", 1, 5000, 0, isInBlueZone=False),
            vehicle={},
        ),
        event(
            "LogPlayerPosition",
            70,
            elapsedTime=70,
            character=character("mate", 1, 6000, 0),
            vehicle={},
        ),
        event(
            "LogPlayerAttack",
            80,
            attacker=player,
            attackType="Weapon",
            weapon=item("Item_Weapon_HK416_C", "Weapon", "Main"),
        ),
        event(
            "LogPlayerTakeDamage",
            81,
            attacker=character("p1", 1, 0, 0),
            victim=character("enemy", 2, 10000, 0),
            damage=40,
            damageCauserName="WeapHK416_C",
        ),
        event(
            "LogPlayerMakeGroggy",
            82,
            attacker=enemy,
            victim=player,
            damageCauserName="WeapAK47_C",
            distance=7500,
        ),
        event(
            "LogPlayerMakeGroggy",
            88,
            attacker=teammate,
            victim=enemy,
            damageCauserName="WeapSCAR-L_C",
            distance=5500,
        ),
        event("LogPlayerRevive", 90, reviver=teammate, victim=player),
        event(
            "LogPlayerKillV2",
            91,
            killer=player,
            finisher={},
            victim=enemy,
            killerDamageInfo={
                "damageCauserName": "WeapHK416_C",
                "distance": 8000,
            },
            assists_AccountId=["mate"],
        ),
    ]


def participants():
    return {
        "p1": ParticipantContext("p1", 1),
        "mate": ParticipantContext("mate", 1),
        "enemy": ParticipantContext("enemy", 2),
    }


def test_builds_fight_zone_team_and_loot_analysis_from_one_match():
    bundle = build_advanced_analysis(
        sample_events(),
        match_id="match-1",
        tracked_account_ids={"p1"},
        participants=participants(),
    )

    assert len(bundle.fight_episodes) == 1
    fight = bundle.fight_episodes[0]
    assert fight.outcome == "win"
    assert fight.opening_actor == "self"
    assert fight.first_hit_actor == "self"
    assert fight.shots_fired == 1
    assert fight.shots_hit == 1
    assert fight.damage_dealt == pytest.approx(40)
    assert fight.dbnos_taken == 1
    assert fight.kills == 1
    assert fight.trade_opportunities == 1
    assert fight.trade_successes == 1
    assert fight.primary_opponent_account_id == "enemy"
    assert fight.weapon_codes == ("Item_Weapon_HK416_C", "WeapHK416_C")
    assert fight.min_distance_m == pytest.approx(75)
    assert fight.avg_distance_m == pytest.approx(85)
    assert fight.max_distance_m == pytest.approx(100)
    assert fight.to_record()["parser_version"] == PARSER_VERSION

    assert len(bundle.zone_phases) == 1
    zone = bundle.zone_phases[0]
    assert zone.phase_number == 1
    assert zone.outside_safe_zone_seconds == pytest.approx(10)
    assert zone.blue_zone_exposure_seconds == pytest.approx(10)
    assert zone.max_outside_distance_m == pytest.approx(50)
    assert zone.first_inside_elapsed_seconds == pytest.approx(70)
    assert zone.late_entry_seconds == pytest.approx(10)
    assert zone.rotation_distance_m == pytest.approx(100)
    assert zone.dbnos_taken == 1

    assert len(bundle.team_coordination) == 1
    team = bundle.team_coordination[0]
    assert team.avg_nearest_teammate_distance_m == pytest.approx(180)
    assert team.max_nearest_teammate_distance_m == pytest.approx(350)
    assert team.isolated_seconds == pytest.approx(10)
    assert team.regroup_count == 1
    assert team.trade_opportunities == 1
    assert team.trade_successes == 1
    assert team.revives_received == 1
    assert team.avg_revive_latency_seconds == pytest.approx(8)

    assert len(bundle.loot_readiness) == 1
    loot = bundle.loot_readiness[0]
    assert loot.first_primary_weapon_code == "Item_Weapon_HK416_C"
    assert loot.second_primary_weapon_code == "Item_Weapon_M24_C"
    assert loot.seconds_to_first_primary_weapon == pytest.approx(10)
    assert loot.seconds_to_second_primary_weapon == pytest.approx(30)
    assert loot.seconds_to_first_fight == pytest.approx(80)
    assert loot.ready_before_first_fight is True
    assert loot.readiness_score == pytest.approx(100)
    assert loot.ground_pickups == 6
    assert loot.loot_box_pickups == 1


def test_output_count_includes_zero_episode_match_summaries():
    events = [
        event("LogParachuteLanding", 0, is_game=0.5, character=character("p1", 1)),
    ]
    bundle = build_advanced_analysis(
        events,
        match_id="quiet",
        tracked_account_ids={"p1"},
        participants={"p1": ParticipantContext("p1", 1)},
    )

    assert bundle.fight_episodes == ()
    assert len(bundle.team_coordination) == 1
    assert len(bundle.loot_readiness) == 1
    assert bundle.output_count("p1") == 2


def test_team_distance_does_not_use_future_position_as_current_knowledge():
    events = [
        event(
            "LogPlayerPosition",
            60,
            elapsedTime=60,
            character=character("mate", 1, 10000, 0),
            vehicle={},
        ),
        event(
            "LogPlayerPosition",
            64,
            elapsedTime=64,
            character=character("p1", 1, 0, 0),
            vehicle={},
        ),
        event(
            "LogPlayerPosition",
            65,
            elapsedTime=65,
            character=character("mate", 1, 0, 0),
            vehicle={},
        ),
    ]

    bundle = build_advanced_analysis(
        events,
        match_id="causal-team-position",
        tracked_account_ids={"p1"},
        participants=participants(),
    )

    assert bundle.team_coordination[0].avg_nearest_teammate_distance_m == pytest.approx(100)
