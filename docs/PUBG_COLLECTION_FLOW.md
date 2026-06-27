# PUBG Collection Flow

Research date: 2026-06-28

## Confirmed API Flow

The collection flow starts from registered players and ends with match detail plus telemetry.

1. Register nickname and platform shard.
2. Resolve nickname with the players collection endpoint:
   - `GET /shards/{shard}/players?filter[playerNames]={nickname}`
3. Store the returned player ID as PUBG `accountId`.
4. Refresh player data by account ID:
   - `GET /shards/{shard}/players/{accountId}`
   - or chunked collection lookup:
     `GET /shards/{shard}/players?filter[playerIds]={accountId1},{accountId2},...`
5. Read match IDs from each player object's `relationships.matches.data`.
6. Deduplicate by `match_id`.
7. Fetch unseen match details:
   - `GET /shards/{shard}/matches/{match_id}`
8. Immediately classify the match:
   - shard
   - map
   - `game_mode`
   - `match_type`
   - solo/duo/squad
   - TPP/FPP
   - ranked/custom flags
9. Store one raw match JSON file for the `match_id`.
10. Read included objects:
    - `participant` records for player match stats
    - `roster` records for team/rank/win data
    - `asset` records for telemetry URL
11. Download telemetry JSON from the asset URL.
12. Store one raw telemetry JSON file for the `match_id`.
13. Parse telemetry with the current parser version.
14. Update player summaries, fight outcomes, aggregates, and optional replay artifacts.

## Completed-Match Availability

Player match IDs, match detail, and telemetry should be treated as completed-match data. The local system polls for
newly available match IDs, but it does not observe an active PUBG match in real time.

Operational impact:

- Do not promise in-match live tracking.
- 2D replay is generated after match detail and telemetry are available.
- If a match ID appears before telemetry is downloadable, keep the job pending and retry.

## Deduplication Rule

Multiple registered players can appear in the same match. Store raw match and telemetry payloads once per `match_id`.

Per-player rows are still generated separately:

- `match_participants`
- `player_match_summaries`
- `fight_outcomes`
- weapon/map/team aggregates

This prevents large raw files from being duplicated while preserving player-specific analysis.

## Player Lookup Limits

The official player lookup filter currently supports up to 10 player names or account IDs per request. A cycle that
targets up to 100 active registered players must therefore be chunked into 10-player requests, then rate-limited.

The local management program should expose:

- polling interval, 60 to 300 seconds
- player limit per collection cycle, 1 to 100
- player lookup chunk size, 1 to 10

## Match Job States

Use explicit job states so the local management UI can show where a match is stuck:

| State | Meaning |
| --- | --- |
| `player_seen` | Player refresh found a match ID |
| `match_queued` | Match detail fetch is queued |
| `match_fetched` | Match detail JSON is downloaded |
| `match_stored` | Raw match JSON file is stored and checksummed |
| `match_classified` | Match mode/type/map/shard/team/perspective flags are stored |
| `telemetry_queued` | Telemetry asset URL was found |
| `telemetry_fetched` | Telemetry JSON is downloaded |
| `telemetry_stored` | Raw telemetry JSON file is stored and checksummed |
| `parse_queued` | Parser run is queued |
| `parsed` | Normalized telemetry rows are created |
| `aggregated` | Summary and aggregate tables are updated |
| `replay_ready` | Optional 2D replay artifacts are generated |
| `failed_retryable` | Job failed but should retry |
| `failed_terminal` | Job failed and needs operator attention |

