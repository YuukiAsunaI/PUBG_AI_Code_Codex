# Sample Match Telemetry Analysis

Analysis date: 2026-06-28

This document records findings from local PUBG telemetry files. The raw files are not committed to the repository
because telemetry files are large and contain player names/account IDs.

## Sample 1 Identity

| Field | Value |
| --- | --- |
| File name | `f4ae05ae-e027-4123-9cbb-ed862da93e9c.txt` |
| SHA-256 | `925aee8516a42fc59a5997d2e7fc3d73bd2626bbf9d611042fa9174fe14b38d1` |
| Size | 36,284,092 bytes |
| JSON shape | Top-level array of telemetry events |
| Event count | 30,938 |
| Match ID | `match.bro.official.pc-2018-41.steam.squad.as.2026.06.15.10.f4ae05ae-e027-4123-9cbb-ed862da93e9c` |

## Match Summary

| Field | Value |
| --- | --- |
| Map | `Savage_Main` / 사녹 |
| Shard/mode hint | `steam.squad.as` from match ID |
| `teamSize` | 4 |
| Camera | `FpsAndTps` |
| Custom game | `false` |
| Event mode | `false` |
| Weather | `Overcast` |
| `LogMatchStart` | `2026-06-15T10:49:50.525Z` |
| `LogMatchEnd` | `2026-06-15T11:11:41.882Z` |
| Last event timestamp | `2026-06-15T11:12:47.345Z` |
| Start-to-end duration | 1,311 seconds |
| Start-to-last-event duration | 1,376 seconds |
| Players | 97 |
| Human players | 95 |
| Bot players | 2 |
| Teams | 31 |
| Team size distribution | 17 full squads, 7 three-person teams, 1 duo, 6 solo/partial teams |

## Event Counts

| Event type | Count |
| --- | ---: |
| `LogPlayerTakeDamage` | 6,033 |
| `LogPlayerPosition` | 4,910 |
| `LogPlayerAttack` | 4,176 |
| `LogItemPickup` | 3,158 |
| `LogHeal` | 2,863 |
| `LogItemEquip` | 1,569 |
| `LogItemUnequip` | 1,243 |
| `LogItemUse` | 940 |
| `LogItemAttach` | 784 |
| `LogItemDetach` | 740 |
| `LogItemDrop` | 681 |
| `LogItemPickupFromLootBox` | 423 |
| `LogWeaponFireCount` | 332 |
| `LogPlayerUseThrowable` | 211 |
| `LogVehicleRide` | 164 |
| `LogVehicleLeave` | 164 |
| `LogGameStatePeriodic` | 131 |
| `LogParachuteLanding` | 116 |
| `LogPlayerKillV2` | 95 |
| `LogPlayerMakeGroggy` | 88 |
| `LogPlayerRevive` | 23 |
| `LogPhaseChange` | 15 |
| `LogCarePackageSpawn` | 13 |
| `LogItemPickupFromCarepackage` | 11 |

## Weapon Accuracy / Hit-Part Sample

Using in-match `LogPlayerAttack` weapon events, gun-type `LogPlayerTakeDamage`, `LogPlayerMakeGroggy`, and
`LogPlayerKillV2`, the parser derives the following match-wide weapon facts. `LogWeaponFireCount` is inspected as
a cumulative audit/fallback checkpoint in the immutable raw telemetry.

| Metric | Value |
| --- | ---: |
| Player/weapon stat rows | 290 |
| Weapon attack events | 3,851 |
| Supported single-projectile attacks / hit events | 3,750 / 687 |
| Supported single-projectile estimated hit rate | 18.32% |
| Shotgun shells / pellet hit events | 82 / 218 |
| Shotgun pellet hit events per shell | 2.66 |
| Unclassified attacks / hit events | 19 / 0 |
| All gun hit events from `LogPlayerTakeDamage` | 905 |
| Head hit events | 122 |
| Final kills | 79 |
| Headshot final kills | 19 |
| DBNOs | 77 |
| Headshot DBNOs | 15 |
| Finishes | 55 |
| Headshot finishes | 16 |

Body-part hit distribution:

| Body part | Hits |
| --- | ---: |
| Torso | 365 |
| Arm | 237 |
| Head | 122 |
| Pelvis | 97 |
| Leg | 84 |

Top weapons by gun hit events:

| Weapon | Attacks/shells | Hit events | Semantic hit metric | Kills | Headshot kills | DBNOs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `WeapMP5K_C` | 1,084 | 172 | 15.87% estimated | 17 | 2 | 15 |
| `WeapSaiga12_C` | 34 | 110 | 3.24 pellets/shell | 6 | 1 | 7 |
| `WeapWinchester_C` | 39 | 90 | 2.31 pellets/shell | 4 | 1 | 4 |
| `WeapVector_C` | 323 | 86 | 26.63% estimated | 7 | 3 | 6 |
| `WeapBerylM762_C` | 588 | 76 | 12.93% estimated | 12 | 6 | 5 |

Shotgun pellet hit events can exceed fired shell count, so they are never displayed as a percentage or clamped to
100%. Unknown or non-ballistic attack codes remain visible but are excluded from both calibrated metrics.

The same parser output should be stored in two query-friendly shapes:

- `player_match_combat_summaries`: one row per player/match with total damage dealt, damage taken, kills, assists,
  deaths, DBNOs caused, DBNOs taken, finishes, headshots, weapon attack events, hit events, and received hits.
- `player_weapon_match_stats`: one row per player/match/weapon with weapon-attributable damage dealt/taken, kills,
  assists, deaths, DBNOs caused/taken, finishes, attack events, hit events, and body-part hit counts.

Assists are available directly from `LogPlayerKillV2.assists_AccountId` for the total player summary. Weapon-level
assist attribution should use the assistant's prior gun damage history against the victim when a weapon can be linked;
it should not be guessed from the final killer's weapon.

## Parser Findings

- Event array order is not fully chronological. The first array item is `LogMatchDefinition` at
  `2026-06-15T10:49:50.5543029Z`, but the minimum timestamp is `LogPlayerLogin` at
  `2026-06-15T10:48:39.305Z`.
- There were 5 adjacent timestamp reversals in the array. Normalize and sort by `_D` when building timelines,
  while preserving source order for debugging.
- Pre-match/lobby events exist before `LogMatchStart`: position, item equip/unequip, attacks, throwable usage,
  apple drops, player create/login/logout, and fire-count events.
- `LogMatchStart.characters` entries wrap the actual player object under `character`. Team, name, account ID, and
  location should be read from `entry.character`.
- `LogMatchEnd.characters` also use the nested `character` shape and contain final `ranking`, final location,
  bluezone/redzone flags, and team membership.
- Team membership is available through `character.teamId`; this confirms that teammate grouping should come from
  telemetry/match roster data, not nickname matching.
- This sample's `LogMatchStart.characters` population has 97 total players. Two records have `ai.` account/player ID
  evidence, so it should be recorded as 95 human players and 2 bot players unless match API participant data later
  proves otherwise.
- DBNO linkage is available through `dBNOId` across `LogPlayerMakeGroggy`, `LogPlayerKillV2`, and
  `LogPlayerRevive`. Some final kills can have `dBNOId = -1`, so final kill/death logic must not assume every kill
  has a previous DBNO.
- `LogItemAttach` and `LogItemDetach` use `parentItem` and `childItem`, not `item`. The code translator and item
  parser must translate all item-like fields.
- `LogItemUse` includes ammo use/reload events, not only heal/boost items. Analytics should classify use events by
  `item.itemId` category before counting consumables.
- The sample's DBNO distance values look consistent with centimeter-style raw telemetry units. Keep one central
  conversion step before applying meter-based distance buckets.
- Bluezone damage appears as `damageTypeCategory = Damage_BlueZone` with `damageCauserName =
  TslGameModeBase_BattleRoyaleBP_C`; do not treat every unknown `damageCauserName` as a weapon.

## Translation Coverage From Current Seed

| Area | Known event references | Unknown event references | Unique unknown codes |
| --- | ---: | ---: | ---: |
| Item IDs | 6,985 | 1,795 | 35 |
| Damage causers | 1,276 | 5,113 | 14 |
| Maps | all known | 0 | 0 |

Common missing item codes already present in the user's legacy `PUBG_Data.py`:

| Code | Suggested label from legacy file |
| --- | --- |
| `Item_Weapon_Apple_C` | 사과 |
| `Item_Back_B_01_StartParachutePack_C` | 낙하산 |
| `Item_Head_F_02_Lv2_C` | 헬멧 (Lv. 2) |
| `Item_Back_C_02_Lv3_C` | 배낭 (Lv. 3) |
| `Item_Special_BackupParachute_C` | 백업 낙하산 |
| `Item_Back_F_02_Lv2_C` | 배낭 (Lv. 2) |
| `Item_Head_E_02_Lv1_C` | 헬멧 (Lv. 1) |
| `Item_Back_E_02_Lv1_C` | 배낭 (Lv. 1) |
| `Item_Back_BlueBlocker` | 전파 방해 배낭 |
| `Item_Mountainbike_C` | 산악 자전거 |
| `Item_EmergencyPickup_C` | 비상호출 |
| `Item_Weapon_TacPack_C` | 전술가방 |
| `Item_BulletproofShield_C` | 접이식 방패 |

Loading the legacy Python dictionary through `CodeTranslator.from_python_file(...)` reduces sample item gaps from
35 unique codes to 12 unique codes. Damage-causer gaps remain unchanged because the missing values are mostly
environment, character, vehicle, or effect classes rather than ordinary weapon IDs.

Missing item codes not found in the legacy file should fall back to raw codes until labels are reviewed:

```text
Item_Attach_Weapon_Muzzle_AR_MuzzleBrake_C
Item_Special_Bluechip_C
Item_Attach_Weapon_Lower_TiltedGrip_C
Item_Attach_Weapon_Upper_DualOptic_4x1x_C
Item_Weapon_CoverStructDropHandFlare_C
Item_Weapon_Pickaxe_C
Item_Weapon_IntegratedRepair_C
Item_Back_BlueBlocker_Lv1
Item_Back_BlueBlocker_Lv3
Item_Weapon_PackageFlare_C
Item_Weapon_Ziplinegun_C
Item_Ammo_ZiplinegunHook_C
```

Common missing non-weapon damage causers:

```text
TslGameModeBase_BattleRoyaleBP_C
PlayerFemale_A_C
PlayerMale_A_C
JerrycanFire
BP_M_Rony_A_03_C
UltAIPawn_Base_Male_C
BP_Bicycle_C
ProjStickyGrenade_C
BP_Motorbike_04_Desert_C
BP_M_Rony_A_02_C
BP_FireEffectController_C
BP_MolotovFireDebuff_C
Jerrycan
WeapPanProjectile_C
```

## Implementation Notes To Carry Forward

- Store raw telemetry externally and keep only metadata/path/checksum in MySQL.
- Parse source timestamps into normalized KST columns after sorting/sequence indexing.
- Keep source event index so replay can reproduce PUBG's original event order when needed.
- Filter or separately tag lobby/pre-match events so apples, pre-match attacks, and spawn equipment do not pollute
  combat or loot analytics.
- Build item-event extraction around a reusable item-object parser for `item`, `parentItem`, `childItem`, `weapon`,
  and `victimWeapon`.
- Build DBNO episodes keyed by `match_id + dBNOId`, then join revive/final-kill outcomes onto the episode.
- Generate static map snapshot artifacts from the same parsed timeline: whole-match route summary, tracked-player
  route image, and team route image. These should mark plane route, parachute/drop route, movement route, kill/DBNO
  points, death position, and care package positions.

## Sample 2 Identity

This file was provided as the latest match data from a direct API fetch. Its JSON shape is a top-level telemetry event
array, not the Match endpoint `{data, included}` object. Treat it as a raw telemetry asset response and associate it
with the match through collection metadata.

| Field | Value |
| --- | --- |
| File name | `751d1def-d222-4d3e-8b9d-1fc3721bb5c1.txt` |
| Raw storage copy | `PUBG_RAW_DATA_DIR\matches\steam\751d1def-d222-4d3e-8b9d-1fc3721bb5c1.json` |
| SHA-256 | `3629b1ec02eac4b29c54c7c4f82c61d3e6092ae6a7516e9b8db21a208eb71c01` |
| Size | 59,632,311 bytes |
| JSON shape | Top-level array of telemetry events |
| Event count | 51,168 |
| Match ID in telemetry | Not present; carry this from the Match endpoint/raw fetch job |

## Sample 2 Match Summary

| Field | Value |
| --- | --- |
| Map | `Tiger_Main` |
| `teamSize` | 4 |
| Camera | `FpsAndTps` |
| Custom game | `false` |
| Event mode | `false` |
| Weather | `Clear` |
| `LogMatchStart` | `2026-06-28T00:13:17.928Z` |
| `LogMatchEnd` | `2026-06-28T00:41:53.927Z` |
| Last event timestamp | `2026-06-28T00:43:02.522Z` |
| Start-to-end duration | 1,715 seconds |
| Start-to-last-event duration | 1,784 seconds |
| Players | 98 |
| Human players | 98 |
| Bot players | 0 |
| Teams | 29 |
| Team size distribution | 21 full squads, 3 three-person teams, 5 solo/partial teams |

## Sample 2 Event Counts

| Event type | Count |
| --- | ---: |
| `LogPlayerTakeDamage` | 11,148 |
| `LogHeal` | 8,781 |
| `LogPlayerPosition` | 7,208 |
| `LogPlayerAttack` | 6,323 |
| `LogItemPickup` | 4,935 |
| `LogItemEquip` | 1,840 |
| `LogItemUnequip` | 1,523 |
| `LogItemUse` | 1,347 |
| `LogItemDrop` | 1,291 |
| `LogItemAttach` | 1,111 |
| `LogItemDetach` | 1,102 |
| `LogWeaponFireCount` | 539 |
| `LogVehicleDamage` | 462 |
| `LogItemPickupFromLootBox` | 438 |
| `LogObjectInteraction` | 402 |
| `LogVaultStart` | 366 |
| `LogVehicleRide` | 327 |
| `LogVehicleLeave` | 327 |
| `LogSpecialZoneInCharacters` | 231 |
| `LogObjectDestroy` | 228 |
| `LogGameStatePeriodic` | 171 |
| `LogPlayerUseThrowable` | 166 |
| `LogParachuteLanding` | 118 |
| `LogPlayerMakeGroggy` | 102 |
| `LogPlayerKillV2` | 102 |
| `LogArmorDestroy` | 57 |
| `LogCarePackageLand` | 54 |
| `LogCarePackageSpawn` | 43 |
| `LogPlayerRevive` | 29 |

Damage categories from `LogPlayerTakeDamage`:

| Category | Count |
| --- | ---: |
| `Damage_BlueZone` | 8,077 |
| `Damage_DBNO` | 1,934 |
| `Damage_Gun` | 1,041 |
| `Damage_Molotov` | 27 |
| `Damage_Explosion_Grenade` | 25 |
| `Damage_Instant_Fall` | 19 |
| `Damage_VehicleCrashHit` | 12 |
| `Damage_BlueZoneGrenade` | 10 |
| `Damage_VehicleHit` | 2 |
| `Damage_Explosion_PanzerFaustWarhead` | 1 |

## Sample 2 Weapon Accuracy / Hit-Part Sample

| Metric | Value |
| --- | ---: |
| Player/weapon stat rows | 359 |
| Weapon attack events | 6,039 |
| Supported single-projectile attacks / hit events | 5,756 / 811 |
| Supported single-projectile estimated hit rate | 14.09% |
| Shotgun shells / pellet hit events | 68 / 230 |
| Shotgun pellet hit events per shell | 3.38 |
| Unclassified attacks / hit events | 215 / 0 |
| All gun hit events from `LogPlayerTakeDamage` | 1,041 |
| Head hit events | 136 |
| Final kills | 90 |
| Headshot final kills | 22 |
| Assists attributed to prior gun damage | 24 |
| DBNOs | 91 |
| Headshot DBNOs | 28 |
| Finishes | 64 |
| Headshot finishes | 15 |

Body-part hit distribution:

| Body part | Hits |
| --- | ---: |
| Torso | 416 |
| Arm | 238 |
| Leg | 154 |
| Head | 136 |
| Pelvis | 97 |

Top weapons by gun hit events:

| Weapon | Attacks/shells | Hit events | Semantic hit metric | Kills | Headshot kills | DBNOs | Assists |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `WeapSaiga12_C` | 44 | 133 | 3.02 pellets/shell | 8 | 2 | 6 | 1 |
| `WeapACE32_C` | 940 | 106 | 11.28% estimated | 10 | 1 | 12 | 5 |
| `WeapMP5K_C` | 497 | 100 | 20.12% estimated | 11 | 1 | 7 | 0 |
| `WeapAUG_C` | 763 | 99 | 12.98% estimated | 9 | 2 | 11 | 1 |
| `WeapHK416_C` | 825 | 96 | 11.64% estimated | 5 | 1 | 4 | 3 |
| `WeapWinchester_C` | 16 | 57 | 3.56 pellets/shell | 3 | 2 | 4 | 0 |
| `WeapAK47_C` | 232 | 52 | 22.41% estimated | 6 | 3 | 6 | 0 |
| `WeapVector_C` | 121 | 49 | 40.50% estimated | 4 | 0 | 3 | 0 |
| `WeapSCAR-L_C` | 230 | 46 | 20.00% estimated | 5 | 2 | 5 | 2 |
| `WeapMini14_C` | 196 | 38 | 19.39% estimated | 1 | 0 | 2 | 4 |

## Corpus-Wide Accuracy Calibration

On 2026-08-10 KST, all 244 retained telemetry payloads (9,151,853 events) were reprocessed with zero failures. The
current tracked-player corpus contains 12,741 weapon attack events and 1,976 gun hit events. The calibrated breakdown
is 12,650 supported single-projectile attacks with 1,763 hit events (13.9368% estimated), 68 shotgun shells with 213
pellet hit events (3.13235 per shell), and 23 unclassified attacks with zero hit events. The unclassified attacks are
18 pickaxe, 1 stun-gun, and 4 zipline-gun events and are excluded from both displayed accuracy metrics.

The previous 60,283 fire count was inflated by summing cumulative `LogWeaponFireCount` checkpoints. Reprocessing
from `LogPlayerAttack` produced 244 combat summaries and 826 per-player/per-weapon rows. Player profile, weapon,
match, recommendation, ranking, trend, local web, and Discord outputs now use the same class-aware contract.

## Sample 2 Parser Findings

- The telemetry array does not expose `common.matchId`; match identity must come from the Match endpoint response or
  fetch job metadata.
- This sample has 8 adjacent timestamp reversals. Timeline builders should sort by parsed `_D` while preserving source
  order for debugging.
- `LogCarePackageLand` appears 54 times while `LogCarePackageSpawn` appears 43 times, so care-package parsing should
  not require a strict one-to-one spawn/land relationship.
- `Damage_BlueZone` dominates raw damage events. Combat analytics must continue filtering weapon stats to
  `Damage_Gun` and storing non-gun damage separately.
- `LogPlayerKillV2` has 23 events with assists and 24 assist slots. Weapon-level assist attribution from prior gun
  damage produced 24 attributed assists in this sample.
- New or less common event types observed here include `LogObjectInteraction`, `LogVaultStart`,
  `LogSpecialZoneInCharacters`, `LogObjectDestroy`, `LogArmorDestroy`, `LogCharacterCarry`,
  `LogItemPutToVehicleTrunk`, and vehicle trunk pickup events.
