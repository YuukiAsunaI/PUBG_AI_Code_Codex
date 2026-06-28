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

## Implemented Registration Slice

The current local runtime can already perform the first registration step:

- `PubgApiClient.lookup_player_by_name(shard, nickname)` calls the official players endpoint.
- `PlayerRegistry.register_player_by_name(...)` resolves the nickname, stores the PUBG `accountId`, and records an
  alias.
- `python -m pubg_ai.cli lookup-player <nickname> --shard <shard>` checks a nickname without registering it.
- `python -m pubg_ai.cli register-player <nickname> --shard <shard>` resolves and registers the player.
- The local web UI uses the same lookup path when Account ID is left blank.

Live test completed with the Steam nickname `Yuuki_Asuna---`; lookup and registration succeeded.

## Implemented Match Discovery Slice

The current local runtime can also refresh active registered players and queue unseen match IDs:

- `PubgApiClient.refresh_players_by_ids(shard, account_ids)` calls the official players endpoint with
  `filter[playerIds]`.
- `RegisteredPlayerMatchCollector.collect_active_players(...)` parses `relationships.matches.data`, stores a raw
  player snapshot, updates `player_collection_states`, and inserts unseen match IDs into `api_fetch_jobs` with
  `job_type = 'match'`.
- `python -m pubg_ai.cli collect-matches --shard steam --limit 10` runs one manual collection pass.
- `python -m pubg_ai.cli match-jobs --limit 20` lists queued match detail jobs.
- The local web UI has a `최근 매치 수집` button and a `Match 수집 큐` table.

Live test completed with the registered Steam player `Yuuki_Asuna---`; 146 match IDs were discovered and queued.

## Implemented Match Detail Storage Slice

The current local runtime can process queued match detail jobs:

- `PubgApiClient.fetch_match(shard, match_id)` calls `GET /shards/{shard}/matches/{match_id}`.
- `parse_match_payload(...)` extracts match metadata, participants, and the telemetry asset URL from the match
  payload.
- `MatchJobProcessor.process_queued_matches(...)` stores raw match JSON under `PUBG_RAW_DATA_DIR`, verifies the
  checksum, upserts `matches`, `raw_match_payloads`, and `match_participants`, then queues telemetry jobs with
  `job_type = 'telemetry'`.
- `python -m pubg_ai.cli process-match-jobs --limit 10` runs one manual match-detail processing pass.
- The local web UI has a `상세 저장` button for processing queued match jobs.

Live test completed with the registered Steam player `Yuuki_Asuna---`; 146 queued match jobs were fetched, stored
under `D:\BackUP\raw`, and converted into 146 queued telemetry jobs with 0 failed match jobs.

## Implemented Telemetry Raw Storage Slice

The current local runtime can process queued telemetry jobs:

- `TelemetryJobProcessor.process_queued_telemetry(...)` reads `job_type = 'telemetry'` jobs and looks up each match's
  `telemetry_url`.
- The telemetry JSON is downloaded from the PUBG telemetry CDN without requiring the PUBG API key.
- The downloaded JSON bytes are stored under `PUBG_RAW_DATA_DIR` using the `telemetry/{shard}/{yyyy}/{mm}/{dd}/`
  folder layout.
- `raw_telemetry_payloads` stores the asset URL, local relative path, compression mode, size, checksum, fetched KST
  timestamp, and current telemetry parser version.
- `python -m pubg_ai.cli process-telemetry-jobs --limit 5` runs one manual telemetry download pass.
- The local web UI has a `Telemetry 저장` button for processing queued telemetry jobs.

This slice intentionally stores raw telemetry first. Normalized combat, movement, item, route, and 2D replay rows are
created in later parser stages from the immutable raw telemetry files.

Live test completed with the registered Steam player `Yuuki_Asuna---`; 146 queued telemetry jobs were downloaded,
stored under `D:\BackUP\raw`, and recorded in `raw_telemetry_payloads` with 0 failed telemetry jobs. Compressed stored
telemetry size was 231,431,883 bytes.

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
