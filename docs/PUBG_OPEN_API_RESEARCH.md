# PUBG Open API Research

Research date: 2026-06-27

## Goal Fit

The PUBG Open API is suitable for a local analytics system that tracks only registered players. The API supports
player lookup, recent match discovery, match details, season/lifetime stats, mastery stats, and telemetry files.
The most valuable source for this project is telemetry because it contains item, weapon, damage, kill, revive,
position, vehicle, zone, parachute, care package, and match start/end events.

The important limitation is that the public API does not provide true in-match live state. Telemetry is discovered
through a match object and downloaded from the telemetry CDN, so a 2D "live" feature should be designed first as
post-match replay or near-live playback after match data appears.

## Request Basics

- Base pattern: `https://api.pubg.com/shards/{platform}/...`
- Common platform shards: `steam`, `kakao`, `psn`, `xbox`, `console`, `tournament`
- Most requests need:
  - `Authorization: Bearer <PUBG_API_KEY>`
  - `Accept: application/vnd.api+json`
  - `Accept-Encoding: gzip` for larger responses
- Player search supports up to 10 names or account IDs per request.
- Data retention is short: match data older than 14 days is not available through the API.

## Rate Limit Strategy

The default development key limit is 10 requests per minute. Official guidance says `/matches` and telemetry
requests are not rate limited, so the local collector should spend rate-limited requests only on registered-player
lookup, player refresh, and optional season/lifetime stats.

Practical strategy:

- Cache nickname to `accountId` permanently, with alias tracking for nickname changes.
- Poll only active registered users.
- Batch player lookup where possible, up to the documented limit.
- Use `matches` and telemetry freely after match IDs are known, but still queue them to avoid local overload.
- Store raw API responses so parsing bugs can be fixed without re-requesting data within the 14-day window.

## Collection Flow

1. User registers nickname and shard, for example `steam` or `kakao`.
2. Collector calls `/players?filter[playerNames]={nickname}`.
3. Store returned `accountId`, canonical name, shard, registration state, and lookup timestamp.
4. Poll `/players/{accountId}` or batch IDs to collect recent match IDs.
5. For unseen match IDs, call `/matches/{matchId}`.
6. Read the match `relationships.assets` reference and find the included telemetry asset URL.
7. Download telemetry JSON from the CDN. The telemetry file does not require an API key.
8. Store raw match JSON and raw telemetry JSON.
9. Normalize events and update derived analysis tables.

## Endpoint Priority

| Priority | Endpoint family | Use |
| --- | --- | --- |
| P0 | Players | Nickname registration, `accountId` lookup, recent match ID discovery |
| P0 | Matches | Immutable match metadata, rosters, participants, ranks, match stats, telemetry asset URL |
| P0 | Telemetry CDN | Main event stream for item, weapon, damage, kill, movement, revive, vehicle, zone, and replay analysis |
| P1 | Seasons / player season | Season summaries and up to recent season match references |
| P1 | Lifetime stats | Baseline long-term player stats |
| P1 | Weapon mastery | Cross-check weapon preference and mastery signals |
| P2 | Leaderboards, clans, tournaments | Optional later features |

## Telemetry Events To Normalize First

| Feature | Telemetry events |
| --- | --- |
| Item pickup/drop/use | `LogItemPickup`, `LogItemPickupFromCarepackage`, `LogItemPickupFromLootbox`, `LogItemDrop`, `LogItemUse` |
| Equipment and attachments | `LogItemEquip`, `LogItemUnequip`, `LogItemAttach`, `LogItemDetach` |
| Weapon usage | `LogPlayerAttack`, `LogPlayerUseThrowable`, `LogPlayerUseFlareGun`, `LogWeaponFireCount` |
| Damage and armor | `LogPlayerTakeDamage`, `LogArmorDestroy`, `LogVehicleDamage`, `LogWheelDestroy` |
| DBNO, kill, assist, death | `LogPlayerMakeGroggy`, `LogPlayerKillV2`, legacy `LogPlayerKill` for tournament matches |
| Revival and redeploy | `LogPlayerRevive`, `LogPlayerRedeploy`, `LogPlayerRedeployBRStart`, `LogCharacterCarry` |
| Position and movement | `LogPlayerPosition`, `LogVehicleRide`, `LogVehicleLeave`, `LogSwimStart`, `LogSwimEnd`, `LogVaultStart` |
| Map and match state | `LogMatchStart`, `LogMatchEnd`, `LogGameStatePeriodic`, `LogPhaseChange` |
| Drop and landmarks | `LogParachuteLanding`, `LogCarePackageSpawn`, `LogCarePackageLand`, `LogObjectInteraction` |

## Death, DBNO, Revive, and Win/Loss Semantics

PUBG has knockdowns, revives, redeploy/recall flows, and final deaths. For analytics, do not treat every damage or
groggy event as a death.

Suggested model:

- `DBNO`: `LogPlayerMakeGroggy` creates a knockdown episode with `dBNOId`.
- `Revived`: `LogPlayerRevive` closes the knockdown episode as recovered.
- `Carried`: `LogCharacterCarry` records carry state, but does not imply death.
- `Redeployed`: `LogPlayerRedeploy` and `LogPlayerRedeployBRStart` mark return-to-play contexts.
- `Kill/death`: `LogPlayerKillV2` is the main final-out event; use `victimGameResult` and finisher/killer fields.
- `Win`: use match roster rank and/or `LogMatchEnd.gameResultOnFinished`, not only the player's final event.
- Weapon win/loss for a fight should be attached to the attack/DBNO/finish chain, not the whole match only.

## Metrics Requested By User

Per match:

- Picked items, dropped items, used items
- Equipped weapons, used weapons, attached/detached parts
- Survival time, kills, damage, assists, DBNOs, deaths, rank, chicken/non-chicken
- Total movement distance, vehicle distance, swim distance where available
- Map, mode, team size, perspective, party/teammates
- Main drop location and first parachute landing point
- Flight path approximation if available from aircraft/player position traces
- Care package spawn/landing points

Aggregates:

- KDA, win rate, top-N rate, chicken rate
- Weapon by kill/damage/assist/death/DBNO/win/loss
- Weapon by distance bucket
- Attachment combinations by weapon outcome
- Teammate combinations by win rate and performance
- Time of day, date, week, and month trends
- Solo/duo/squad and TPP/FPP splits
- Favorite maps, drop zones, weapons, parts, consumables
- Recommendation scores for weapons and weapon-part combinations

## Recommendation Direction

Recommendation should not use raw win rate alone because rare combinations can look falsely strong. Use minimum
sample thresholds and smoothed scores.

Initial score idea:

```text
weapon_score =
  0.35 * smoothed_kill_rate
  + 0.25 * smoothed_damage_per_min
  + 0.15 * smoothed_win_or_fight_win_rate
  + 0.15 * survival_after_equip_delta
  + 0.10 * user_comfort_score
```

For attachments:

```text
attachment_score =
  outcome_with_weapon_and_part - baseline_outcome_with_weapon
```

Store both global and per-player scores because "recommended for everyone" and "recommended for this player" will
often differ.

