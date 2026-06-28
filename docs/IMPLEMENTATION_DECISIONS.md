# Implementation Decisions

Decision date: 2026-06-27

This file records product and data rules that should be treated as fixed unless an administrator intentionally
changes them.

## Player Registration

- Registering a player always requires both nickname and platform shard.
- Initial target shards are `steam` and `kakao`, but the data model must allow other PUBG shards.
- The same nickname on different shards is treated as a different player.
- Only Discord users with registration permission can register players.
- Registration resolves nickname to PUBG `accountId`; future collection uses `accountId`.
- Registered PUBG players are admin-managed tracking targets, not ownership claims by Discord users.
- Player records should include `public_profile` so profile/ranking visibility can be controlled.

## Match Collection Scope

- Collect every completed/discovered match type for registered players.
- Do not discard custom, casual, ranked, event, arcade, TPP, or FPP matches at ingestion time.
- Classify each match immediately when match details are fetched:
  - `game_mode`
  - `match_type`
  - `map_name`
  - `shard`
  - `is_custom_match`
  - `season_state` when available
  - team size and perspective derived from mode
- Match details and telemetry are post-match data. They are only available after the PUBG match has finished.
- 2D replay is post-match only because it is generated from finished-match logs and telemetry.
- Raw match and telemetry files are deduplicated by `match_id`.
- Telemetry asset JSON can be a top-level event array without an embedded `match_id`; always carry `match_id`, shard,
  asset URL, and raw file metadata from the Match endpoint fetch job into the telemetry parser.
- Every match record must store `total_players`, `human_players`, and `bot_players`.
- Player population counts should be computed from match participants when available and cross-checked with
  telemetry `LogMatchStart.characters`/`LogMatchEnd.characters`.
- Bot detection evidence must be stored per participant, with rules such as `ai.` player/account ID prefixes kept as
  detection sources.

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

## Weapon Accuracy And Hit Parts

- Store per-match, per-player combat totals separately from per-weapon details.
- Store per-match, per-player, per-weapon combat stats.
- Whole-match player combat totals should include damage dealt, damage taken, kills, assists, deaths,
  DBNOs caused, DBNOs taken, finishes, headshot counts, fired shots, hit shots, and received hit counts.
- Per-weapon combat stats should include the same weapon-attributable metrics where the source event identifies a
  weapon or a prior assist damage event can be linked to a weapon.
- DBNOs caused by the tracked player and DBNOs suffered by the tracked player must stay in separate fields.
- Damage dealt and damage taken must stay in separate fields at both total and per-weapon levels.
- Assists come from `LogPlayerKillV2.assists_AccountId` for the total player summary.
- Weapon-level assists should be attributed from the assistant's prior gun damage history against the victim when a
  weapon can be linked. Do not guess weapon-level assists from the final killer's weapon.
- Fired bullet count comes from `LogWeaponFireCount.fireCount`.
- Hit bullet count comes from `LogPlayerTakeDamage` where `damageTypeCategory = Damage_Gun`.
- Body-part hit counts come from `damageReason`, grouped at least as head, torso, pelvis, arm, leg, non-specific,
  none, and unknown/new raw reason.
- Store both directions:
  - hits caused by player by weapon and body part
  - hits received by player by enemy weapon and body part
- Record headshot-specific counts separately for hits, DBNOs, final kills/deaths, and finishes.
- Normalize weapon codes before aggregating because fire events can use `Item_Weapon_*` while damage/kill events use
  `Weap*` or projectile-style damage causer names.
- Lobby/pre-match events such as apple throws should be excluded from normal weapon accuracy unless an admin
  intentionally runs a raw-event/debug report.

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
- Permissions and ranking visibility are scoped by `guild_id`.
- Server-wide ranking commands should be supported within each guild scope.
- Global admins can view and manage all guilds.
- Personal data deletion/destructive commands require administrator permission.
- The local management program must be able to edit command groups, per-guild grants, global admins, and ranking
  scope.

Suggested command groups:

| Group | Examples |
| --- | --- |
| `register` | register player, update shard/name mapping |
| `profile_read` | profile, recent matches, weapon stats |
| `ranking_read` | server ranking, map ranking, weapon ranking |
| `replay_read` | replay link, replay summary |
| `settings_write` | storage paths, polling interval, API settings |
| `admin` | grant permissions, unregister players, delete data |

## Secret Handling

- `PUBG_API_KEY` stays only in `.env`.
- `DISCORD_BOT_TOKEN` stays only in `.env`.
- The local program can show configured/missing/masked status, but must not store or display raw secret values.
- `config/local_settings.json` must reject token/API-key fields.

## Team and Visibility

- Team membership comes from PUBG match roster/team data.
- Registered users on the same team should be highlighted separately from unregistered teammates.
- `public_profile` controls whether a player appears in public profile and ranking views.

## Location Analysis

- Phase 1: use coordinate clustering for drop/landing/hotspot analysis.
- Phase 2: map clusters to named regions such as city or landmark names.
- Map coordinate-to-region dictionaries should be versioned separately from raw telemetry.
- Record phase-aware location facts such as phase at landing, phase at DBNO, phase at kill, and phase at death when
  telemetry phase data is available.
- Record movement facts such as reconstructed plane route, distance from plane route to landing point, first vehicle
  ride time/location, and distance to safe-zone/blue-zone boundaries.
- Record AI/bot detection evidence separately from human player records when source IDs or telemetry names indicate
  AI players.

## Rendered Map Snapshots

- Generate static map snapshot images for important match/player summaries.
- Store generated JPEG or PNG files under the configurable replay artifact directory, not the raw-data directory.
- Snapshot layers should include plane route, parachute/drop route, player movement route, kill/DBNO positions,
  final death position, care package landing positions, and optional phase circles.
- Snapshots are rebuildable from raw telemetry and map assets, so they can be regenerated after renderer changes.
- Discord commands should prefer a compact image attachment for quick viewing, with a local 2D replay link for full
  playback.

## Code Translation

- Convert known PUBG internal codes to Korean labels before display.
- This applies to item IDs, damage causer names, map names, vehicle IDs, game modes, and local death types.
- If a code is not in the parser dictionary, show the original code unchanged.
- Updated or newly added PUBG codes should be added through dictionary overrides without breaking parsing.

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
- Polling interval, cycle player limit, and player lookup chunk size must be editable in the local management program.

## Raw Data Lifecycle

- Raw match and telemetry files are retained indefinitely.
- If capacity is insufficient, raise local-program and Discord error notifications.
- Do not automatically delete official raw match or telemetry files.
- Parser runs are versioned; parser-version changes can trigger reparse from retained raw files.
