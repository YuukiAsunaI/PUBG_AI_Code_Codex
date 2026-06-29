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

This slice intentionally stores raw telemetry first. Normalized combat, item, movement, location, route, and future
2D replay artifact rows are created from the immutable raw telemetry files.

Live test completed with the registered Steam player `Yuuki_Asuna---`; 146 queued telemetry jobs were downloaded,
stored under `D:\BackUP\raw`, and recorded in `raw_telemetry_payloads` with 0 failed telemetry jobs. Compressed stored
telemetry size was 231,431,883 bytes.

## Implemented Combat Summary Parser Slice

The current local runtime can parse raw telemetry into registered-player combat summary tables:

- `TelemetryCombatProcessor.process_raw_telemetry(...)` reads stored telemetry files from `PUBG_RAW_DATA_DIR`.
- Only registered tracking targets that participated in the match are written to player summary tables.
- `player_match_combat_summaries` stores per-match totals for shots fired, shots hit, hits taken, damage dealt,
  damage taken, kills, assists, deaths, DBNOs caused/taken, finishes, headshot counts, and body-part hit maps.
- `player_weapon_match_stats` stores the same combat facts split by normalized weapon/damage-causer code.
- Weapon codes are canonicalized before storage so telemetry variants such as `WeapFamasG2_C` and
  `WeapFAMASG2_C` collapse into one weapon row.
- `python -m pubg_ai.cli parse-telemetry-combat --limit 10` runs one parse pass.
- `python -m pubg_ai.cli parse-telemetry-combat --limit 200 --force` reparses already summarized matches after
  parser changes.
- The local web UI has combat parse/reparse buttons.

Live test completed with the registered Steam player `Yuuki_Asuna---`; 146 telemetry files were parsed with 0 failed
payloads, reading 5,137,481 telemetry events and producing 146 match combat summaries plus 504 weapon-stat rows.

## Implemented Item Event Parser Slice

The current local runtime can parse raw telemetry into registered-player item event and item summary tables:

- `TelemetryItemProcessor.process_raw_telemetry(...)` reads stored telemetry files from `PUBG_RAW_DATA_DIR`.
- Only registered tracking targets that participated in the match are written to item tables.
- `player_item_events` stores pickup, loot-box pickup, care-package pickup, drop, use, equip, unequip, attach, and
  detach events with event index, KST event time, item code/name, parent weapon, child attachment, stack count,
  location, and raw event JSON.
- `player_item_match_stats` stores item-level per-match totals such as picked-up quantity, dropped quantity, uses,
  equips, unequips, attachment count, and detach count.
- Known item and weapon codes are translated to Korean labels. Unknown or newly added codes remain visible as their
  original PUBG code.
- `python -m pubg_ai.cli parse-telemetry-items --limit 10` runs one item parse pass.
- `python -m pubg_ai.cli parse-telemetry-items --limit 200 --force` reparses already summarized item rows after
  parser or translation changes.
- The local web UI has item parse/reparse buttons.

Live test completed with the registered Steam player `Yuuki_Asuna---`; 146 telemetry files produced 19,814 item event
rows and 4,133 item-stat rows with 0 failed payloads.

## Implemented Movement And Location Parser Slice

The current local runtime can parse raw telemetry into registered-player location tables and match-level route/event
tables:

- `TelemetryMovementProcessor.process_raw_telemetry(...)` reads stored telemetry files from `PUBG_RAW_DATA_DIR`.
- Only registered tracking targets that participated in the match are written to player-specific movement/location
  tables.
- `player_position_samples` stores registered-player `LogPlayerPosition` samples with KST event time, PUBG map
  coordinates, in-vehicle/zone/DBNO flags, elapsed time, and alive-player count.
- `player_landing_events` stores all parachute landing events. The per-match movement summary uses the earliest
  landing event as the first drop/landing point.
- `player_movement_summaries` stores first/last known coordinates, landing coordinates, sampled route distance,
  in-game sampled route distance, vehicle sample count, DBNO sample count, and altitude range.
- `player_combat_location_events` stores DBNO caused/taken, kill, death, finish, and finished-taken locations with
  related player coordinates, damage causer, damage reason, distance, and headshot flag.
- `match_care_package_events` stores care-package spawn/land positions and package item-code lists for later 2D
  replay layers.
- `match_plane_routes` stores a match-level plane-route approximation reconstructed from early aircraft
  `LogPlayerPosition` samples.
- `python -m pubg_ai.cli parse-telemetry-movement --limit 10` runs one movement/location parse pass.
- `python -m pubg_ai.cli parse-telemetry-movement --limit 200 --force` reparses already summarized movement rows
  after parser changes.
- The local web UI has movement parse/reparse buttons.

Live test completed with the registered Steam player `Yuuki_Asuna---`; 146 telemetry files produced 8,610 player
position samples, 178 landing events, 146 movement summaries, 872 combat-location events, 4,734 care-package events,
and 119 plane-route approximations with 0 failed payloads.

## Implemented Combat Loadout Snapshot Slice

The current local runtime can reconstruct weapon + attachment state at fight-result moments:

- `LoadoutSnapshotProcessor.process_matches(...)` reads `player_item_events` attach/detach rows and
  `player_combat_location_events` kill, DBNO-caused, and finish rows.
- Item event order is reconstructed with telemetry `event_index`, so same-second events still apply in the correct
  sequence.
- `player_combat_loadout_snapshots` stores one row per fight-result event with weapon code/name, current attachment
  codes/names, distance, headshot flag, and KST combat event time.
- `python -m pubg_ai.cli generate-loadout-snapshots --limit 50` runs one snapshot pass.
- `python -m pubg_ai.cli generate-loadout-snapshots --limit 200 --force` rebuilds existing snapshots after parser
  changes.
- The local web UI has a Loadout Snapshot generate/regenerate section.
- Recommendation lookup uses these snapshots first for weapon+attachment pairs, then falls back to attach-event
  co-occurrence if snapshots have not been generated yet.
- `python -m pubg_ai.cli player-recommendation-evidence <nickname> <weapon_code> <attachment_code>` shows the
  supporting kill, DBNO-caused, and finish snapshots behind one recommendation.
- The local web recommendation panel has per-row evidence buttons that show match, map, mode, fight result, distance,
  full equipped attachment list, and match ID for the selected weapon+attachment pair.

## Implemented Map Snapshot Artifact Slice

The current local runtime can generate post-match 2D route summary JPEG files for registered players:

- `MapSnapshotProcessor.generate_player_snapshots(...)` reads normalized movement/location rows from MySQL.
- Map backgrounds are downloaded from official `pubg/api-assets` map PNG files and cached under
  `PUBG_REPLAY_DATA_DIR/cache/map_assets`.
- If a map asset is unavailable, the renderer falls back to a coordinate grid so the snapshot job still completes.
- `ReplayArtifactStore` writes JPEG files under the configured `PUBG_REPLAY_DATA_DIR`, separate from raw match and
  telemetry files.
- `replay_artifacts` stores artifact metadata only: match, shard, account, artifact name, relative path, content
  type, file size, checksum, renderer version, source tables, and generated KST timestamp.
- Player route snapshots include plane route, parachute/drop route, movement route, landing markers, DBNO/kill/death
  markers, care-package markers, match metadata, and a legend.
- `python -m pubg_ai.cli generate-map-snapshots --limit 10` generates only missing snapshots.
- `python -m pubg_ai.cli generate-map-snapshots --limit 200 --force` regenerates existing JPEG artifacts.
- `python -m pubg_ai.cli generate-replay-timelines --limit 10` generates compact JSON replay timelines from the same
  parsed movement/location tables. These files are used by the local 2D playback UI.
- Timeline artifacts store raw PUBG cm coordinates and normalized map percentage coordinates for player positions,
  landings, combat markers, care packages, and plane routes.
- Timeline artifacts also include same-roster team members from `match_participants`, registered-player emphasis
  flags, and related-player names/registration flags for combat events when those records are available.
- If same-roster teammates also have parsed `player_position_samples`, timeline artifacts include `team_tracks`
  route payloads for those teammates. This is usually available for teammates that are also registered tracking
  targets.
- `GET /replay/artifacts?artifact_type=map_snapshot&limit=50` lists generated artifact metadata for local UI and
  future Discord command reuse.
- `GET /replay/artifacts/{artifact_id}/file` serves a generated artifact file after resolving the DB relative path
  through `ReplayArtifactStore`, so path escape attempts are rejected by the storage layer.
- The local web UI has JPEG and timeline JSON generate/regenerate buttons plus a recent artifact list with open-file
  links.
- The local web UI also has a canvas-based 2D replay player that loads `timeline` artifacts and renders player route,
  plane route, landing markers, combat markers, and care-package markers over cached official map PNG backgrounds
  when available. It falls back to the coordinate grid if the map asset is missing.
- The 2D replay player includes a time-sorted event list and event detail panel. Selecting a landing, combat, or
  care-package event pauses playback, seeks to that moment, highlights the map point, and shows event metadata such
  as weapon, damage reason, distance, related player, item count, and KST timestamp where available.
- The 2D replay player shows the tracked player's team list beside the event panel and visually emphasizes
  registered teammates.
- The 2D replay player has a teammate-route toggle. When `team_tracks` are present, it draws teammate paths as
  labeled overlays while keeping the tracked player's route visually primary.

Live test completed with the registered Steam player `Yuuki_Asuna---`; 146 route snapshot JPEG files were generated
under `D:\BackUP\replay`, recorded in `replay_artifacts`, and verified as readable JPEG images. Total generated
snapshot size was 54,922,370 bytes.
The local artifact list endpoint returned recent `map_snapshot` rows, and the file endpoint returned `image/jpeg`
with valid JPEG magic bytes.
Live test also regenerated one `timeline` artifact for match `751d1def-d222-4d3e-8b9d-1fc3721bb5c1`; the payload
included a four-member squad list and 13 combat-location events with related-player display names where available.

## Implemented Discord Bot MVP Slice

The current Discord bot slice is intentionally small and reuses the same local MySQL and file stores:

- `python -m pubg_ai.cli run-discord-bot --prefix !` starts the bot with the token from `.env`.
- The bot requires Discord message content intent because the first MVP uses text commands.
- `!유저등록 steam 닉네임` resolves the PUBG nickname, stores the tracking target, and records the Discord
  user/guild/channel context.
- `!유저조회 [닉네임] [shard]` lists registered targets or loads one registered target including inactive rows.
- `!전적 닉네임 [shard]` reads parsed MySQL summaries and returns matches, chickens, KDA, damage, accuracy,
  average survival/movement, top weapons, and recent match rows.
- `!무기 닉네임 무기명 [shard]` resolves common names such as `M416` to PUBG weapon codes and returns weapon-specific
  usage matches, chickens, kills, assists, DBNOs, damage, accuracy, body-part hits, and recent weapon rows.
- `!매치 match_id [닉네임|accountId] [shard]` reads one completed-match summary for a registered target, including
  map/mode/type, chicken/non-chicken, total/human/bot player counts, kills, deaths, assists, caused/taken DBNOs,
  damage, accuracy, survival/movement/landing distance, top weapons, hit parts, and generated 2D snapshot status.
- `!랭킹 [지표] [shard] [limit] [전체]` ranks registered targets from completed-match summary tables. Server channels
  default to that `guild_id` scope; global admins can request the full local ranking with `전체`.
- `!추천 닉네임 [shard]` reads parsed summary tables and returns recommendations for weapons, distance-weighted
  weapon ranges, weapon+attachment pairs from combat loadout snapshots when available, attachments, maps, teammates,
  and coordinate-clustered drop zones.
- `!유저삭제 steam 닉네임또는accountId` stops future collection by setting the registered target inactive.
- `!최근스냅샷 [match_id]` sends the latest generated `map_snapshot` JPEG artifact, or the latest snapshot for the
  requested match ID.
- Command access is checked through local Discord permission settings. Global admins can manage every guild, while
  guild-specific grants stay scoped by `guild_id`.
- The local web UI and CLI can now add/revoke user command-group grants and add/remove global Discord admins without
  storing any bot token or PUBG API key outside `.env`.
- The bot does not fetch live in-match data. It only reads completed-match data and already generated replay
  artifacts.

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
