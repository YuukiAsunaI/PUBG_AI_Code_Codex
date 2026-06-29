# Local Architecture and MySQL Model

Research date: 2026-06-27

## Target Shape

The system should run entirely on the local computer:

- MySQL for durable storage
- A local backend API for management UI and Discord command reads
- A worker/queue process for PUBG API polling, match download, telemetry parsing, and aggregation
- A Discord bot as the main user-facing interface
- A local web management app for player registration, job status, dashboards, and 2D replay playback
- A configurable raw-data storage directory for large match and telemetry files, preferably on a separate drive
- A configurable replay artifact directory for generated 2D timelines, map snapshots, thumbnails, GIFs, videos, and
  caches
- A local settings file managed by the UI so storage paths can be changed without editing `.env`

The local management app binds to `127.0.0.1` by default. Binding to `0.0.0.0` is intentionally rejected until a
separate authenticated remote-access mode exists.

## Recommended Runtime

Python is a strong first choice because the requested analytics work is event-heavy and Python has mature data
tooling. A good local stack would be:

- FastAPI for local API
- discord.py or py-cord for Discord
- SQLAlchemy + Alembic for MySQL schema
- APScheduler, Celery, RQ, or a simple MySQL-backed job queue for polling
- Pydantic for event parsing
- Leaflet, PixiJS, or Canvas in the local UI for 2D replay

TypeScript/NestJS is also viable, especially if Discord + local web UI are both written in Node. If choosing
TypeScript, `pubg-kit` is the most relevant current SDK reference because it includes rate limiting, caching, and
NestJS integration.

## Component Diagram

```mermaid
flowchart LR
    Discord["Discord bot"] --> API["Local API"]
    UI["Local management UI"] --> API
    API --> MySQL[("MySQL")]
    Worker["Collector worker"] --> PUBG["PUBG Open API"]
    Worker --> CDN["Telemetry CDN"]
    Worker --> RawFiles["Configurable raw file storage"]
    Worker --> MySQL
    API --> Replay["2D replay viewer"]
    Replay --> MySQL
    Replay --> RawFiles
    Replay --> ReplayFiles["Configurable replay artifact storage"]
    UI --> Settings["Local settings file"]
    Settings --> API
```

## Data Storage Principle

Use a two-layer storage model:

1. Raw immutable layer
   - Store large raw match and telemetry JSON files under `PUBG_RAW_DATA_DIR`.
   - Keep request metadata, relative file path, source URL, fetched timestamp, parse version, and checksum in MySQL.
   - This protects the project from API retention limits and parser mistakes.

2. Normalized analysis layer
   - Extract core entities and event facts for fast queries.
   - Keep derived aggregates separate and rebuildable.

## Core Tables

### Registration and Identity

| Table | Purpose |
| --- | --- |
| `registered_players` | Admin-managed tracking targets: `account_id`, `current_name`, required `shard`, `active`, `public_profile` |
| `player_aliases` | Nickname history and lookup evidence |
| `discord_users` | Discord user records; not assumed to own PUBG accounts |
| `discord_guilds` | Guild-specific settings, ranking scope, and visibility defaults |
| `discord_permission_grants` | Global and guild-scoped per-user command group permissions |
| `global_admins` | Discord users allowed to manage and view all guilds |
| `player_groups` | Optional friend/group labels for squad analysis |
| `code_translation_overrides` | Admin-maintained Korean labels for new or corrected PUBG codes |

### Raw API Storage

| Table | Purpose |
| --- | --- |
| `api_fetch_jobs` | Queue, retry state, and official rate-limit headers for player/match/telemetry fetches |
| `raw_player_snapshots` | Raw player endpoint responses; small enough for MySQL JSON storage |
| `raw_match_payloads` | Raw match JSON file metadata by `match_id` |
| `raw_telemetry_payloads` | Raw telemetry JSON file metadata by `match_id`, asset URL, and local file path |
| `replay_artifacts` | Generated 2D replay timeline, map snapshot, thumbnail, GIF, video, and cache metadata |
| `parse_runs` | Parser version, status, error, and row counts |

### Match Facts

| Table | Purpose |
| --- | --- |
| `matches` | `match_id`, shard, map, mode, match type, team mode, perspective, ranked/custom flags, created KST time, duration, telemetry URL, total players, human players, bot players |
| `match_rosters` | Teams/rosters, rank, win flag |
| `match_participants` | Player match stats from match object, including AI/bot detection flags when available |
| `player_match_summaries` | One row per tracked player per match with final placement, survival, phase facts, and derived flags |
| `match_teammates` | Teammate pairs/trios/squad membership from PUBG roster/team data |
| `player_collection_states` | Polling cursor/status by registered player |
| `collector_settings` | Program-editable polling interval, cycle player limit, and lookup chunk size |
| `discord_permission_settings` | Program-editable command groups, guild-scoped grants, and global admins |

### Telemetry Event Facts

| Table | Purpose |
| --- | --- |
| `telemetry_events` | Generic event index: event type, timestamp, elapsed time, actor, raw payload JSON |
| `item_events` | Pick/drop/use/equip/unequip/attach/detach/trunk/carepackage/lootbox events |
| `player_combat_loadout_snapshots` | Reconstructed weapon + attachment state at kill, DBNO-caused, and finish moments |
| `weapon_fire_events` | Attack, throwable, flare, fire-count events |
| `damage_events` | Damage dealt/taken with causer, reason, distance, armor notes |
| `body_part_hit_events` | One row per gun hit with attacker, victim, weapon, damage reason/body part, damage, and headshot flag |
| `dbno_events` | Knockdown episodes keyed by `dBNOId`, including attacker, victim, weapon, distance, and revive/final state |
| `fight_outcomes` | Per-player fight outcomes such as `dbno_win`, `dbno_loss`, `final_kill`, and `final_death` |
| `kill_events` | Final kill/death/finish/assist/teamkill/suicide records |
| `revive_events` | Revive and redeploy events |
| `position_samples` | Player position samples for movement, drop, route, and replay |
| `vehicle_events` | Ride/leave/damage/destroy/wheel events |
| `zone_events` | Game state, phase, bluezone/redzone/blackzone signals |
| `plane_routes` | Reconstructed plane line/path and route metadata by match |
| `movement_summaries` | Per-player route facts such as landing point, drop distance from plane, first vehicle ride, and movement distance |
| `care_package_events` | Care package spawn and landing points |
| `landing_events` | Parachute landing and first-grounded position |

## Derived Aggregate Tables

| Table | Purpose |
| --- | --- |
| `agg_player_daily` | Daily KDA, damage, wins, maps, modes, play volume |
| `agg_player_monthly` | Monthly trend rollups |
| `player_match_combat_summaries` | Per-match, per-player whole-match combat totals: damage dealt/taken, kills, assists, deaths, DBNOs caused/taken, finishes, headshots, shots fired/hit, and received hits |
| `player_weapon_match_stats` | Per-match, per-player, per-weapon fired shots, hit shots, accuracy, body-part hits/taken, headshots, kills, assists, deaths, DBNOs, and finishes |
| `player_position_samples` | Registered-player `LogPlayerPosition` samples for route/replay layers |
| `player_landing_events` | Registered-player parachute landing events; first event is used as first drop/landing point |
| `player_movement_summaries` | Per-match first/last position, landing point, sampled movement distance, vehicle samples, DBNO samples, and altitude range |
| `player_combat_location_events` | DBNO, kill, death, finish, and finished-taken coordinates with related player coordinates and damage metadata |
| `match_care_package_events` | Care-package spawn/land coordinates and package item-code lists |
| `match_plane_routes` | Plane-route approximation from early aircraft `LogPlayerPosition` samples |
| `agg_player_weapon` | Weapon usage, kills, deaths, damage, assists, caused DBNOs, suffered DBNOs, fight wins/losses |
| `agg_player_weapon_body_part` | Weapon/body-part hit and hit-received rollups for accuracy and weakness analysis |
| `agg_weapon_distance_bucket` | Weapon outcomes by distance bucket |
| `agg_weapon_attachment` | Weapon + attachment combination outcomes |
| `agg_player_map` | Map-specific performance and drop preference |
| `agg_player_teammate` | Performance with each teammate or party set |
| `agg_player_drop_zone` | Common landing/drop coordinate clusters and outcomes |
| `agg_player_phase` | Performance by phase at landing, kill, DBNO, death, and survival endpoint |
| `agg_player_movement` | Movement style rollups such as first vehicle timing, distance from plane route, and zone-distance tendency |
| `map_region_labels` | Phase-2 mapping from coordinate clusters to named regions |
| `recommendation_scores` | Rebuildable player/global recommendation outputs |

### Code Translation

Known PUBG internal codes should be translated before UI/Discord display. Unknown codes are stored and displayed
unchanged so newly added PUBG content remains visible.

## MySQL Implementation Notes

- Use `utf8mb4` for all text.
- Store small player snapshots in MySQL `JSON` columns.
- Store large match and telemetry JSON payloads as compressed files under `PUBG_RAW_DATA_DIR`.
- Store generated 2D replay artifacts and static map images under `PUBG_REPLAY_DATA_DIR`.
- Store only metadata for large raw files in MySQL: root key, relative path, compression, file size, `sha256`,
  source URL, fetched timestamp, and parser version.
- Do not rely on telemetry JSON to contain `match_id`. The telemetry parser receives `match_id`, shard, and asset URL
  from the Match endpoint/raw fetch job because telemetry assets can be top-level event arrays.
- Store PUBG rate-limit response headers such as `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and
  `X-RateLimit-Reset` on fetch jobs when present.
- Store replay artifact metadata in MySQL: artifact type, content type, relative path, size, `sha256`, generated
  timestamp, and renderer version.
- Keep file paths relative to `PUBG_RAW_DATA_DIR` or `PUBG_REPLAY_DATA_DIR` so drives can be moved without rewriting
  every row.
- Do not silently fall back to the project directory if a configured external drive is missing.
- Load storage paths from `config/local_settings.json` when the local program has saved user-selected paths.
- Keep `PUBG_API_KEY` and `DISCORD_BOT_TOKEN` only in `.env`; local settings must not store raw secrets.
- Scope Discord permissions/rankings by `guild_id`, with global admins allowed to view/manage all guilds.
- Treat registered PUBG players as tracking targets, not Discord ownership claims.
- Use `match_id` and `account_id` as natural keys where possible.
- Store `matches.total_players`, `matches.human_players`, and `matches.bot_players` for every match.
- Store participant-level `is_ai_or_bot` and `ai_detection_source` so match population counts are auditable.
- Prefer match API participant records for population counts, then cross-check telemetry `LogMatchStart.characters`
  and `LogMatchEnd.characters` during parsing.
- Store whole-match combat totals and weapon-specific combat totals separately so profile views can answer both
  "this match total" and "which weapon caused it" queries without re-aggregating raw events.
- Keep `damage_dealt` separate from `damage_taken`, and keep `dbnos_caused` separate from `dbnos_taken` in both
  summary and weapon aggregate tables.
- Count total assists from `LogPlayerKillV2.assists_AccountId`; attribute weapon-level assists only from the
  assistant's prior gun damage history against the victim.
- Treat `LogWeaponFireCount.fireCount` as the official reported fire-count aggregate. The official telemetry schema
  describes it as reported in increments of 10, so keep it separate from per-shot attack/throwable event counts.
- Normalize weapon codes for weapon aggregates so `Item_Weapon_BerylM762_C`, `WeapBerylM762_C`, and weapon instance
  strings such as `WeapBerylM762_C_1` group under one weapon code.
- Store raw `damageReason` alongside normalized body part so new PUBG hit locations do not disappear.
- Use bigint surrogate IDs for high-volume event tables.
- Add indexes on:
  - `registered_players(account_id, shard)`
  - `matches(created_at, map_name, game_mode)`
  - `match_participants(account_id, match_id)`
  - `telemetry_events(match_id, event_type, event_ts)`
  - `position_samples(match_id, account_id, elapsed_time)`
  - `damage_events(attacker_account_id, victim_account_id, match_id)`
  - `body_part_hit_events(match_id, attacker_account_id, weapon_code, body_part)`
  - `dbno_events(attacker_account_id, victim_account_id, match_id)`
  - `fight_outcomes(account_id, match_id, outcome_type)`
  - `kill_events(killer_account_id, victim_account_id, match_id)`
  - `player_match_combat_summaries(account_id, match_id)`
  - `player_weapon_match_stats(account_id, match_id, weapon_code)`
- Store normalized DB timestamps in KST and use KST calendar boundaries for daily/monthly aggregates.
- Preserve source API timestamps separately when useful for debugging.

## 2D Replay / Near-Live Viewer

Because the official API exposes telemetry after match discovery, implement this as a replay-first feature:

Match details and telemetry are only available after the PUBG match finishes, so 2D replay is not in-match live
tracking.

1. Parse `LogMatchStart` to identify map and team size.
2. Load the map image or coordinate metadata from official assets or project-maintained map assets.
3. Use `LogPlayerPosition` as the primary track source.
4. Interpolate positions between samples for smooth playback.
5. Overlay fight events:
   - damage lines
   - DBNO marker
   - DBNO fight win/loss marker for tracked players in duo/squad modes
   - kill marker
   - revive marker
   - care package marker
   - blue zone phase rings if coordinates are available
6. Add player/team filters for registered users and squad members.
7. Add playback controls: speed, seek, follow player, show weapons, show damage, show deaths.

## Static Map Snapshot Rendering

Generate JPEG or PNG map snapshots after telemetry parsing so Discord can show a fast visual summary without opening
the full replay UI. Use artifact type `map_snapshot` in `replay_artifacts`.

Recommended snapshot layers:

| Layer | Source |
| --- | --- |
| Plane route | Flight/aircraft path reconstructed from early position and phase events when available |
| Parachute route | `LogPlayerPosition` samples before first grounded/landing event |
| Movement route | `LogPlayerPosition` samples after landing, optionally simplified for readability |
| Landing point | `LogParachuteLanding` or first grounded player position |
| Kill and DBNO points | `LogPlayerKillV2` and `LogPlayerMakeGroggy` locations |
| Death point | victim location from `LogPlayerKillV2` or final participant location fallback |
| Care packages | `LogCarePackageSpawn` and `LogCarePackageLand` |
| Phase circles | `LogGameStatePeriodic` safe-zone/blue-zone data where coordinates are present |

Suggested outputs:

| File | Purpose |
| --- | --- |
| `match-route-summary.jpg` | Whole-match overview for Discord match summary |
| `player-{account_id}-route.jpg` | One tracked player's plane/drop/movement/fight/death view |
| `team-{team_id}-route.jpg` | Registered squad/team route and fight summary |

Renderer output should include a small legend and KST match timestamp, but raw secret values and Discord IDs must not
appear on the image.

For Discord, do not stream the whole replay. Send a summary image/GIF or a local UI link when the local app is open.

## Discord Command Ideas

Current MVP uses text commands with the configurable prefix, for example `!유저등록 steam nickname`. Slash commands
can be added later after the permission model and response payloads settle.

| Command | Result |
| --- | --- |
| `/pubg-register nickname shard` | Register nickname and required platform shard; permission-gated |
| `/pubg-profile nickname` | KDA, recent matches, favorite weapons/maps |
| `/pubg-recent nickname` | Recent match summaries |
| `/pubg-match match_id` | Match summary with chicken/non-chicken and team stats |
| `/pubg-weapon nickname weapon` | Weapon-specific kills, damage, death, distance, attachment stats |
| `/pubg-recommend nickname` | Recommended weapons and attachments |
| `/pubg-team nickname` | Best teammate combinations |
| `/pubg-map nickname` | Map performance and drop tendency |
| `/pubg-replay match_id` | Local 2D replay link or rendered summary |
| `/pubg-ranking scope` | Server-wide rankings |
| `/pubg-permission user group allow` | Grant or revoke command group permissions; admin-only |
| `/pubg-unregister nickname shard` | Stop future collection and retain existing data by default; admin/delegated-only |

The same permission groups and per-user grants must be editable in the local management program.

Team membership should come from PUBG roster/team data. Registered teammates should be visually emphasized in local
UI and Discord responses.

## First MVP Milestone

The first useful milestone should avoid heavy AI and focus on trustworthy data:

Completed slices:

1. Safe `.env` and local settings loading.
2. MySQL initializer for the core MVP schema.
3. Registration/list/unregister service layer using soft-delete collection stop.
4. Localhost-only FastAPI management app.
5. Browser UI for status, user registration, user lookup, and user unregister.
6. PUBG player API client for nickname + shard to `accountId`.
7. Registered-player match discovery and completed-match detail storage.
8. Raw telemetry download into configured storage.
9. Combat, item, movement, location, and 2D map snapshot parser/rendering slices.
10. Discord bot MVP runner with permission-gated `유저등록`, `유저조회`, `유저삭제`, and `최근스냅샷`.
11. Local web and CLI management for Discord command-group grants and global admins.
12. First player profile stats service and `전적` lookup command using parsed summary tables.
13. Weapon-specific lookup service and `무기` command for per-weapon damage, accuracy, DBNO, and hit-part stats.
14. Match detail lookup service and `매치` command for one completed match's map/mode/type, player/bot counts,
    combat totals, weapon rows, movement/landing summary, and generated map snapshot status.
15. Player ranking service and `랭킹` command for KDA, win rate, average damage, total damage, kills, matches,
    accuracy, headshot rate, and DBNO ranking with `guild_id` scope.
16. Replay timeline JSON artifact generation for future local 2D playback, using parsed movement, combat-location,
    care-package, landing, and plane-route tables.
17. Local canvas-based 2D replay player that loads generated timeline JSON artifacts with play/pause, seek, speed,
    route/combat/care-package/plane visibility controls, and artifact-list load buttons.
18. First-pass recommendation service, CLI, web endpoint, and Discord command for weapons, attachments, maps,
    teammates, and coordinate-clustered drop zones from parsed summary tables.
19. Distance-weighted weapon recommendations and first-pass weapon+attachment pair recommendations from
    combat-location distance buckets and attach events.
20. Combat loadout snapshot generator and recommendation upgrade so weapon+attachment pairs prefer the actual
    attachment state at kill, DBNO-caused, and finish moments.
21. Recommendation evidence detail lookup in CLI and local web UI, showing the supporting kill, DBNO-caused, and
    finish snapshots behind one weapon+attachment recommendation.

Next slice:

1. Richer 2D replay playback features such as team overlays, minimap assets, and event detail panels.
2. Add weapon+attachment evidence links from Discord recommendation responses where a local web URL can be shared
   safely.
