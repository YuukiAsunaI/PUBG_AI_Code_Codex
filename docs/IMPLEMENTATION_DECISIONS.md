# Implementation Decisions

Decision date: 2026-06-27

This file records product and data rules that should be treated as fixed unless an administrator intentionally
changes them later.

## Player Registration

- Registering a player always requires both nickname and platform shard.
- Initial target shards are `steam` and `kakao`, but the data model must allow other PUBG shards.
- The same nickname on different shards is treated as a different player.
- Only Discord users with registration permission can register players.
- Registration resolves nickname to PUBG `accountId`; future collection uses `accountId`.

## Match Collection Scope

- Collect every discovered match type for registered players.
- Do not discard custom, casual, ranked, event, arcade, TPP, or FPP matches at ingestion time.
- Store enough metadata to filter later:
  - `game_mode`
  - `match_type`
  - `map_name`
  - `shard`
  - `is_custom_match`
  - `season_state` when available
  - team size and perspective derived from mode
- 2D replay is post-match only because match logs and telemetry are available after the match finishes.

## Time Zone

- Store DB-facing timestamps in KST.
- Display timestamps in KST.
- Daily, weekly, and monthly aggregates use KST calendar boundaries.
- External PUBG timestamps can be preserved as source values, but normalized tables should include KST columns.

## Combat Outcomes

- Solo fight results are primarily final kill/death outcomes.
- Duo and squad fight results include DBNO outcomes:
  - tracked player causes `LogPlayerMakeGroggy` -> `dbno_win`
  - tracked player receives `LogPlayerMakeGroggy` -> `dbno_loss`
- DBNO outcomes are separate from final kill/death to avoid double-counting.
- Revive or redeploy does not erase the DBNO fight outcome.
- Teamkill, suicide, fall damage, bluezone, vehicle accidents, and environment deaths must be classified separately
  from ordinary weapon fight outcomes.

## Weapon Distance Buckets

AR uses detailed close-range buckets and then 100m buckets to 1km:

```text
0-5m, 5-10m, 10-15m, 15-20m, 20-25m, 25-50m, 50-75m, 75-100m,
100-200m, 200-300m, 300-400m, 400-500m, 500-600m, 600-700m,
700-800m, 800-900m, 900-1000m, 1000m+
```

DMR and SR use 100m buckets to 1km:

```text
0-100m, 100-200m, 200-300m, 300-400m, 400-500m, 500-600m,
600-700m, 700-800m, 800-900m, 900-1000m, 1000m+
```

## Discord Permissions

- Registration is permission-gated.
- Commands are permission-gated by command group.
- Admins can grant or revoke per-user command permissions.
- Server-wide ranking commands should be supported.
- Personal data deletion/destructive commands require administrator permission.

Suggested command groups:

| Group | Examples |
| --- | --- |
| `register` | register player, update shard/name mapping |
| `profile_read` | profile, recent matches, weapon stats |
| `ranking_read` | server ranking, map ranking, weapon ranking |
| `replay_read` | replay link, replay summary |
| `settings_write` | storage paths, polling interval, API settings |
| `admin` | grant permissions, unregister players, delete data |

## Unregister Policy

- Unregistering a player stops future collection.
- Existing match, telemetry, replay, and aggregate data is retained by default.
- Only an administrator can choose destructive deletion.
- Deletion should be split into options:
  - delete registration only
  - delete normalized DB data
  - delete raw match/telemetry files
  - delete replay artifacts

## Polling Policy

- Default polling interval should be configurable between 1 and 5 minutes.
- One collection cycle may target up to 100 active registered players.
- Official player collection lookup currently supports up to 10 player names or account IDs per request, so a
  100-player cycle should be chunked into 10-player requests.
- Match and telemetry fetches are queued after match IDs are discovered.
- The exact high-volume scheduling policy remains open until live API behavior is tested.

