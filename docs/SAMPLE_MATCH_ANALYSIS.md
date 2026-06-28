# Sample Match Telemetry Analysis

Analysis date: 2026-06-28

This document records findings from one local PUBG telemetry file. The raw file is not committed to the repository
because telemetry files are large and contain player names/account IDs.

## Sample Identity

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

Using `LogWeaponFireCount`, gun-type `LogPlayerTakeDamage`, `LogPlayerMakeGroggy`, and `LogPlayerKillV2`, the parser
can derive the following match-wide weapon facts:

| Metric | Value |
| --- | ---: |
| Player/weapon stat rows | 277 |
| Fired count from `LogWeaponFireCount` | 14,630 |
| Gun hit events from `LogPlayerTakeDamage` | 905 |
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

| Weapon | Fired | Hit events | Kills | Headshot kills | DBNOs |
| --- | ---: | ---: | ---: | ---: | ---: |
| `WeapMP5K_C` | 7,520 | 172 | 17 | 2 | 15 |
| `WeapSaiga12_C` | 10 | 110 | 6 | 1 | 7 |
| `WeapWinchester_C` | 10 | 90 | 4 | 1 | 4 |
| `WeapVector_C` | 1,060 | 86 | 7 | 3 | 6 |
| `WeapBerylM762_C` | 2,300 | 76 | 12 | 6 | 5 |

Important caveat: shotgun/pellet weapons can produce more hit events than fired shell count. Store fired count and hit
event count separately, and interpret accuracy by weapon class instead of clamping it to 100%.

The same parser output should be stored in two query-friendly shapes:

- `player_match_combat_summaries`: one row per player/match with total damage dealt, damage taken, kills, assists,
  deaths, DBNOs caused, DBNOs taken, finishes, headshots, fired shots, hit shots, and received hits.
- `player_weapon_match_stats`: one row per player/match/weapon with weapon-attributable damage dealt/taken, kills,
  assists, deaths, DBNOs caused/taken, finishes, fired shots, hit shots, and body-part hit counts.

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
