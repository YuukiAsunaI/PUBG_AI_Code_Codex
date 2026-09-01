# PUBG AI Local Analytics Research

PUBG Open API, MySQL, Discord bot, and a localhost management app form one local-first analytics system.
This repository contains the working collector, retained raw storage, telemetry analytics, post-match 2D replay,
Discord command layer, operational controls, and the research and evidence behind those choices.

## Current Documents

- [Requirement-to-Evidence Audit](docs/REQUIREMENT_EVIDENCE_AUDIT.md)
- [Whole-Match Tactical Replay Verification](docs/WHOLE_MATCH_TACTICAL_REPLAY_2026-08-28.md)
- [Flight Path and Circle Analysis](docs/FLIGHT_PATH_AND_CIRCLE_ANALYSIS.md)
- [PUBG Open API Research](docs/PUBG_OPEN_API_RESEARCH.md)
- [PUBG Collection Flow](docs/PUBG_COLLECTION_FLOW.md)
- [Local Architecture and MySQL Model](docs/LOCAL_ARCHITECTURE_AND_MYSQL_MODEL.md)
- [Implementation Decisions](docs/IMPLEMENTATION_DECISIONS.md)
- [Data Lifecycle and Operations](docs/DATA_LIFECYCLE_AND_OPERATIONS.md)
- [Player Intelligence and Data Quality](docs/PLAYER_INTELLIGENCE_AND_DATA_QUALITY.md)
- [Operational Recovery and Drills](docs/OPERATIONAL_DRILLS.md)
- [Code Translation](docs/CODE_TRANSLATION.md)
- [Sample Match Analysis](docs/SAMPLE_MATCH_ANALYSIS.md)
- [Configuration](docs/CONFIGURATION.md)
- [Desktop Manager](docs/DESKTOP_MANAGER.md)
- [Map Region Catalog](docs/MAP_REGION_CATALOG.md)
- [Full Project Audit - 2026-08-28](docs/PROJECT_AUDIT_2026-08-28_FULL_SYSTEM.md)
- [Reference Project Survey](docs/REFERENCE_PROJECT_SURVEY.md)
- [Additional Reference Research](docs/ADDITIONAL_REFERENCE_RESEARCH.md)
- [Sources](docs/SOURCES.md)

## Key Decisions From Research

- Registered users are the only primary collection target.
- Registered PUBG players are treated as admin-managed tracking targets, not ownership claims by Discord users.
- Nickname registration requires a platform shard, then resolves `accountId`; later polling and matching use
  `accountId`.
- PUBG API key and Discord bot token stay only in the ignored `.env`. The local program provides write-only inputs
  that can replace those values but never reads a saved secret back into the browser or stores it in local settings.
- All discovered match types are collected and immediately classified by `game_mode`, `match_type`, map, shard,
  team mode, perspective, ranked/custom flags, and completion-only availability.
- Known PUBG item/weapon/map/vehicle codes are translated to Korean display labels; unknown codes are shown as-is.
- MySQL-facing timestamps are stored in KST because the expected audience is primarily Korean users.
- Match and telemetry data should be stored as immutable raw JSON first, then normalized into analysis tables.
- Raw match and telemetry files are retained indefinitely; low disk space raises local-program and Discord errors
  instead of deleting official raw data.
- Large raw match and telemetry files should be saved under a configurable external storage path such as
  `PUBG_RAW_DATA_DIR=E:\PUBG_AI_Data\raw`; MySQL stores metadata and relative paths.
- Generated 2D replay files should use a separate configurable path such as
  `PUBG_REPLAY_DATA_DIR=E:\PUBG_AI_Data\replays`.
- Opt-in deletion backup ZIP files use a third configurable root such as
  `PUBG_BACKUP_DATA_DIR=D:\BackUP\deletion-backups`; it must not contain or sit inside either source root.
- Read-only deletion quarantine planning uses a fourth configurable root such as
  `PUBG_QUARANTINE_DATA_DIR=E:\PUBG_Quarantine`. All four roots must be pairwise disjoint, and the read-only planner
  requires this directory to exist without creating it or moving files into it.
- The local management app should save user-changed storage paths to `config/local_settings.json`, so paths can be
  changed from the program without editing `.env`. It also saves collector polling limits there.
- Discord permissions and rankings are scoped by `guild_id`; global admins can view and manage all guilds.
- PUBG match detail and telemetry are available after the match finishes. A 2D viewer is therefore post-match replay,
  not in-match live tracking.
- The local stack should be MySQL + local API/worker + Discord bot + local web management UI.

## Proposed First Build Slice

1. Register player nickname and platform shard with an authorized Discord command.
2. Resolve and store PUBG `accountId`.
3. Poll registered players for recent match IDs.
4. Deduplicate match IDs and fetch unseen completed-match details.
5. Immediately classify match metadata, fetch telemetry, persist raw JSON, and create normalized event rows.
6. Expose Discord commands for match summary, KDA, weapon usage, map usage, and recent chicken/non-chicken split.
7. Add a local 2D replay page that plays telemetry positions and fight events on a map canvas.

## Local MVP Runtime

The first executable slice is now available:

- safe `.env` loader with masked secret status
- MySQL schema initializer for the `pubg_ai` database
- PUBG Players API lookup for nickname + shard to `accountId`
- player registration/list/unregister service layer
- registered-player refresh that queues unseen match IDs
- queued match detail downloader that stores raw match JSON files and queues telemetry jobs
- queued telemetry downloader that stores large telemetry JSON files under the configured raw storage path
- raw telemetry combat parser for registered-player match summaries and weapon-level stats
- raw telemetry item parser for pickups, drops, uses, equips, attachment changes, and item summary stats
- versioned activity parser for healing, revives, throwables, vehicles, mobility, environment interactions, and
  per-match telemetry event coverage
- progressive player-intelligence workspace with overview, KST trend graphs, categorical comparisons, raw evidence,
  metric definitions, parser coverage, and item-source provenance
- read-only data-quality audit in both CLI and local manager for analysis-policy scope, current parser coverage,
  combat/item/movement/fight reconciliation, 2D artifact coverage, translation coverage, and ingestion freshness
- combat loadout snapshot generator for weapon + attachment state at kill/DBNO/finish moments
- versioned official-asset-backed map-region catalog for Korean named drop-zone recommendations with
  raw-coordinate fallback
- completed-match flight-path frequency analysis with per-map overlays, physical-route clustering, dominant travel
  direction, detailed KST/match filters, and registered-player scope
- localhost-only FastAPI management app
- browser UI for status, user registration, user lookup, collection stop/delete action, match job processing, and
  telemetry job/combat/item processing
- browser UI for raw/replay storage paths, raw compression, collector limits, Discord permission grants, guild ranking
  scopes, public profile defaults, and local evidence-link base URL settings
- automatic collector loop from CLI or the local manager for player refresh, match-detail storage, and telemetry
  download cycles
- automatic post-processing loop from CLI or the local manager for combat/item/movement parsing, loadout snapshots,
  durable fight outcomes, map JPEG snapshots, and replay timelines
- persistent worker run history in MySQL with a local manager table/detail panel for recent collector/post-processing
  cycles, summary metrics, and full stored errors
- bounded PUBG request retries, durable match/telemetry job requeue, and 15-minute stale-running recovery
- simulated and live operational drills with MySQL history and localhost controls for 429, storage, restart, recovery,
  and idempotent soak checks
- local and Discord alert settings for storage pressure and worker failures, including configurable Discord alert
  channel IDs, alert acknowledgement/snooze controls, and persisted alert history without storing bot tokens outside
  `.env`
- dedicated Discord bot manager with write-only `.env` secret entry, start/stop/sync controls, hybrid-command prefix,
  and per-guild visible-command selection

Install dependencies:

```powershell
python -m pip install -e .
```

Initialize the local MySQL schema:

```powershell
python -m pubg_ai.cli init-db
```

Check safe configuration status:

```powershell
python -m pubg_ai.cli config-status
```

Resolve a PUBG nickname:

```powershell
python -m pubg_ai.cli lookup-player Yuuki_Asuna--- --shard steam
```

Resolve and register a PUBG nickname:

```powershell
python -m pubg_ai.cli register-player Yuuki_Asuna--- --shard steam
```

Show registered player stats from parsed MySQL summary tables:

```powershell
python -m pubg_ai.cli player-stats Yuuki_Asuna--- --shard steam
```

Show one weapon's parsed combat stats:

```powershell
python -m pubg_ai.cli player-weapon-stats Yuuki_Asuna--- M416 --shard steam
```

Show durable kill/DBNO fight wins and death/DBNO losses by firearm and exact attachment loadout:

```powershell
python -m pubg_ai.cli player-fight-outcomes Yuuki_Asuna--- --shard steam
```

Friendly fire is excluded by default. Bot fights remain separately counted, and non-firearm contexts stay in the
event ledger and recent-event output while firearm/loadout rankings exclude them.

Show KST hour-of-day, date, ISO-week, or month trends. Every match dimension can be filtered independently:

```powershell
python -m pubg_ai.cli player-trends Yuuki_Asuna--- --shard steam --granularity month
python -m pubg_ai.cli player-trends Yuuki_Asuna--- --shard steam --granularity hour --team-mode squad --perspective tpp --match-type official --map-name Baltic_Main --custom false --from-date 2026-06-01 --to-date 2026-08-31
```

Trend reports calculate from immutable per-match facts at query time, use `Asia/Seoul` calendar boundaries and
Python ISO weeks, and require no schema or raw-file rewrite.

Show first-pass recommendations from parsed summary tables:

```powershell
python -m pubg_ai.cli player-recommendations Yuuki_Asuna--- --shard steam --min-matches 1
```

Recommendations include distance-weighted weapon ranges and weapon+attachment pairs. When generated, combat loadout
snapshots are used first so parts reflect the actual kill/DBNO/finish moment; attach-event co-occurrence remains the
fallback for older parsed data.

The local weapon-detail view also compares no-parts, every observed individual attachment, and exact multi-part
combinations from durable fight snapshots for one selected weapon. It rejects rows from any other weapon and counts
only parts mounted on the selected weapon at that outcome. It keeps fight win rate separate from chicken rate and
shows each part's percentage-point difference from the no-parts baseline, combat outcome counts, average distance,
and sample reliability under the same map/mode/season/KST filters. The no-parts/individual and observed-combination
tabs have independent minimum-match controls with no upper cap. Individual parts are grouped by the official item-code
slot family (grip, muzzle, magazine, stock/rear, sight, lower rail, or quiver), combinations are grouped by part count,
and every fight win rate includes its Wilson 95% confidence interval.

Player intelligence also exposes time-normalized combat tempo and efficiency: kills, caused DBNOs, fights, and damage
per 10 minutes, damage per resolved fight, and dealt/taken damage ratio. These metrics appear in KST day/week/month
graphs and condition comparisons. `timeSurvived` is capped at the completed match duration before normalization so an
old or malformed survival value cannot inflate the denominator.

The `판단 분석` view reconstructs bounded fight episodes, circle-phase rotations, teammate spacing/trades, and loot
readiness from retained telemetry. It shows first-hit and opening rates, fight accuracy and damage advantage, DBNO-to-
kill conversion, third-party involvement, blue-zone exposure, late entry, vehicle rotation share, isolation/support,
revive latency, regrouping, and readiness before the first fight. Recent defeated fights link directly to the complete
match view, and CUSUM-style change signals compare a recent window with the preceding player baseline. Coverage is
reported separately for each derived table; missing telemetry is displayed as unavailable rather than estimated.

Recommendation win rates use a beta-binomial prior derived from the player's eligible weapon sample, and fight rates
use the same shrinkage principle. The manager displays observed and adjusted rates, sample confidence, the final score
factor, and all score components. Map, mode, perspective, match type, season state, year/quarter/month/date/KST hour,
custom-match policy, and KST date-range filters are applied to every recommendation subquery so rows from different
conditions cannot leak into the result.

Drop-zone recommendations keep their stable cluster ID and raw centroid coordinates in centimeters. A versioned
official-asset-backed catalog adds a Korean place name only when the centroid is inside a maintained region; unmatched
points retain the map/grid label, and Paramo is explicitly treated as a dynamic map.

Resolve one coordinate or inspect the catalog:

```powershell
python -m pubg_ai.cli map-region Baltic_Main 575857 134391
python -m pubg_ai.cli map-region-catalog --map-name Baltic_Main
```

See `docs/MAP_REGION_CATALOG.md` for aliases, source commit and hashes, geometry policy, and update procedure.

Show the supporting combat snapshots behind one weapon+attachment recommendation:

```powershell
python -m pubg_ai.cli player-recommendation-evidence Yuuki_Asuna--- WeapHK416_C Item_Attach_Weapon_Lower_TiltedGrip_C --shard steam
```

Show one completed-match detail from parsed MySQL summary tables:

```powershell
python -m pubg_ai.cli player-match-stats 751d1def-d222-4d3e-8b9d-1fc3721bb5c1 Yuuki_Asuna--- --shard steam
```

Show registered player rankings. Add `--guild-id` to view one Discord server scope; omit it for the local global view:

```powershell
python -m pubg_ai.cli player-ranking --metric 평딜 --shard steam --limit 10
```

Refresh active registered players and queue unseen match IDs:

```powershell
python -m pubg_ai.cli collect-matches --shard steam --limit 10
```

List queued match fetch jobs:

```powershell
python -m pubg_ai.cli match-jobs --limit 20
```

Fetch queued match details, store raw match JSON under `PUBG_RAW_DATA_DIR`, and queue telemetry jobs:

```powershell
python -m pubg_ai.cli process-match-jobs --limit 10
```

List queued telemetry jobs:

```powershell
python -m pubg_ai.cli telemetry-jobs --limit 20
```

Download queued telemetry JSON files and store them under `PUBG_RAW_DATA_DIR`:

```powershell
python -m pubg_ai.cli process-telemetry-jobs --limit 5
```

Run the completed-match collector loop. This repeats player refresh, match-detail jobs, and telemetry-download jobs
using the saved polling interval, cycle player limit, and player lookup chunk size:

```powershell
python -m pubg_ai.cli run-collector --shard steam --match-job-limit 10 --telemetry-job-limit 5
```

Run the post-processing loop. This repeatedly parses stored telemetry and generates replay artifacts without pressing
the individual processing buttons:

```powershell
python -m pubg_ai.cli run-post-processing --combat-limit 10 --item-limit 10 --movement-limit 10 --loadout-limit 50 --fight-outcome-limit 10 --map-snapshot-limit 10 --timeline-limit 10
```

Run bounded operational recovery checks. `simulated` makes no PUBG requests and does not modify MySQL collection
tables; unless `--no-record` is supplied, it still stores its secret-free drill report. Stop the automatic collector
before `live`, which uses one active shard and at most one 10-player lookup batch per cycle:

```powershell
python -m pubg_ai.cli run-operational-drills --mode simulated --cycles 3
python -m pubg_ai.cli run-operational-drills --mode live --cycles 2
python -m pubg_ai.cli operational-drill-history --limit 20
```

See [Operational Recovery and Drills](docs/OPERATIONAL_DRILLS.md) for retry limits, safety boundaries, and verified
local evidence.

Parse raw telemetry into registered-player combat summaries and weapon stats:

```powershell
python -m pubg_ai.cli parse-telemetry-combat --limit 10
```

Reparse existing combat summaries after parser changes:

```powershell
python -m pubg_ai.cli parse-telemetry-combat --limit 200 --force
```

Parse raw telemetry into registered-player item events and item summary stats:

```powershell
python -m pubg_ai.cli parse-telemetry-items --limit 10
```

`LogItemUse.item.stackCount` is treated as the held stack snapshot, not the amount consumed. Item use quantity is
therefore one per use event; the local data-quality audit reports any current-parser row where those totals differ.

Reparse existing item events after parser or translation changes:

```powershell
python -m pubg_ai.cli parse-telemetry-items --limit 200 --force
```

Parse raw telemetry into registered-player movement, landing, combat-location, care-package, and plane-route tables:

```powershell
python -m pubg_ai.cli parse-telemetry-movement --limit 10
```

Reparse existing movement/location rows after parser changes:

```powershell
python -m pubg_ai.cli parse-telemetry-movement --limit 200 --force
```

Generate weapon + attachment loadout snapshots for kill/DBNO/finish events:

```powershell
python -m pubg_ai.cli generate-loadout-snapshots --limit 50
```

Regenerate existing combat loadout snapshots after parser changes:

```powershell
python -m pubg_ai.cli generate-loadout-snapshots --limit 200 --force
```

Parse versioned durable fight outcomes and tracked-player weapon/attachment context:

```powershell
python -m pubg_ai.cli parse-fight-outcomes --limit 10
```

The processor automatically reparses rows from an older parser version. Use `--force` only for an explicit full
rebuild. Raw match and telemetry files remain immutable.

Generate registered-player 2D route JPEG snapshots under `PUBG_REPLAY_DATA_DIR`:

```powershell
python -m pubg_ai.cli generate-map-snapshots --limit 10
```

Regenerate existing route snapshots after renderer changes:

```powershell
python -m pubg_ai.cli generate-map-snapshots --limit 200 --force
```

Generate registered-player 2D replay timeline JSON artifacts under `PUBG_REPLAY_DATA_DIR`:

```powershell
python -m pubg_ai.cli generate-replay-timelines --limit 10
```

The local web app also exposes generated replay artifacts:

```text
GET /replay/artifacts?artifact_type=&limit=50
GET /replay/artifacts/{artifact_id}/file
```

The local web app's primary 2D replay is a whole-match tactical replay. A registered player is selected only to find
that player's completed matches and establish the focus player's team; the generated `match-timeline` contains every
participant with an account ID, including unregistered enemies and bots. The UI uses cached official map images
when available and renders all available movement, plane route, phase rings, landing, combat, care-package, DBNO,
kill/death, and revive markers on one event clock. The raw telemetry event objects are not copied into the replay
artifact.

Focus, ally, enemy, other-registered-player, and bot relationships have separate labels and colors. Operators can
toggle allies, enemies, or bots, search the participant roster by partial nickname, choose an event actor, and limit
movement trails to 15, 30, 60, or 120 seconds or the full match. Dead or finished participant markers stop after the
terminal event instead of remaining at their final position. The current-event strip, time-sorted event list, KST
detail panel, actor/event filters, and major-location shortcuts make each marker inspectable without memorizing a
timestamp. Every located event has a map button; drop start, landing, first verified engagement, inferred attack
activity, hit, DBNO, kill, and death shortcuts seek and center the map directly. Movement is classified as airborne,
vehicle, on-foot, or DBNO for all participants when telemetry supplies those samples.

Selecting every participant keeps the complete event dataset but windows the visible event-list DOM to 240 rows near
the active time. Cached replay indexes and binary-search time lookup keep movement and combat rendering responsive
without discarding events or hiding participants.

The player selector queries matches by the stored PUBG account ID, not by nickname text. Selecting a match calls
`POST /matches/{match_id}/replay`, which creates or reuses the match-wide content-addressed artifact. Older
registered-player timeline artifacts remain preserved and playable from storage, but they are no longer the default
entry path for tactical replay.

Attack normalization requires a non-empty PUBG weapon item ID. Firearm shots, throwable uses, melee attacks, and
other attacks are classified separately from item category/subcategory with item-code fallbacks for partial older
events. Empty weapon placeholder attacks remain in immutable raw telemetry but are not presented as measured shots.

Timeline JSON carries the full participant roster, per-participant position/combat tracks, registered-player emphasis,
engagement clusters, and vehicle identity when telemetry provides it. Opponent-backed engagements are separated from
attack-only activity; environmental/self damage and teammate damage cannot establish opponent evidence. Resolution
causes such as Blue Zone remain visible events but are not mislabeled as weapons. A shot always gets a location
marker. A direction line is only marked as verified when the telemetry event contains both attacker and target
coordinates; the PUBG telemetry schema does not expose a general aim vector for a missed shot, so the replay never
invents one. Initial transport-aircraft points are identified from the plane event window, vehicle state, and altitude
together because `common.isGame == 0.1` can remain present after the player has jumped.

The separate `동선·자기장` replay view analyzes retained completed matches rather than one selected timeline.
It extends observed aircraft samples to map boundaries and groups frequently repeated physical routes by angle and
distance from the map center while retaining the dominant travel direction. Integer PUBG `common.isGame` states
are used to extract the numbered target safe circles from `poisonGasWarningPosition` and
`poisonGasWarningRadius`. The operator can view routes only, circles only, or both; choose all circles or phases
1-9; and click a route cluster to recompute circle frequency from only the matches in that cluster.

Map, mode, season, custom-match, KST period, player, cluster precision, and source-limit filters are available at:

```text
GET /analytics/flight-paths
GET /analytics/circles
```

The current Erangel image is bundled from PUBG's current official Erangel page and is also used by newly generated
`map-snapshot-v6` JPEG artifacts. See
[Flight Path and Circle Analysis](docs/FLIGHT_PATH_AND_CIRCLE_ANALYSIS.md) for the phase mapping, clustering contract,
asset provenance, limits, and verification evidence.

Run the single project Discord bot from the local manager's `Discord 봇 > 앱 봇 제어` view. The app owns its
start/stop lifecycle; there is no separate CLI bot process that can accidentally create a second runtime. The Discord
bot token stays only in `.env` as `DISCORD_BOT_TOKEN`. All player, ranking, replay, permission, server-scope, alert,
and worker commands are registered on this same bot as hybrid commands, so prefix and slash invocation share handlers
and authorization.

Use the guarded live-acceptance workflow before enabling a production alert channel. The probe is read-only and its
selected channel row includes `last_message_id` for a pre-send audit:

```powershell
python -m pubg_ai.cli discord-acceptance-probe --guild-id <guild_id> --channel-id <channel_id>
python -m pubg_ai.cli discord-acceptance-send-alert --guild-id <guild_id> --channel-id <channel_id> --confirm SEND_DISCORD_ACCEPTANCE_TEST
```

Use the verified alert result's `message_id` as the baseline. After a human sends `!배그도움말` in that channel,
verify that Discord retained the newer command and the bot's referenced reply without returning either message body:

```powershell
python -m pubg_ai.cli discord-acceptance-observe --guild-id <guild_id> --channel-id <channel_id> --after-message-id <baseline_message_id>
```

The alert command refuses to send unless both numeric IDs and the exact confirmation phrase are supplied. It sends a
fixed message with mentions disabled, reads the created message back, and never prints the token. The observer
requires a numeric baseline, exits with code `2` until a newer matching human-command/bot-reply pair exists, then exits
with code `0`.

Initial commands are:

```text
!배그도움말
!유저등록 steam 닉네임
!유저조회 [닉네임] [shard]
!전적 닉네임 [shard]
!교전 닉네임 [shard]
!추세 닉네임 [집계단위] [shard] [팀모드] [시점] [맵] [게임모드] [매치유형] [시작일] [종료일] [표시구간]
!무기 닉네임 무기명 [shard]
!추천 닉네임 [shard] [최소표본경기] [결과수]
!매치 닉네임 [match_id] [shard]
!랭킹 [지표] [shard] [표시인원] [configured|guild|global] [최소경기]
!최근스냅샷 닉네임 [match_id] [shard]
!pubg-alerts
!pubg-alert-ack alert_id
!pubg-alert-snooze alert_id [minutes]
!pubg-alert-note alert_id note
!pubg-alert-resolution alert_id resolution
!pubg-alert-notes alert_id [limit]
!pubg-alert-history [current-errors|worker-failures|storage-pressure|all-history]
!pubg-alert-history source=storage state=current severity=error search="drive" limit=5
!pubg-worker-runs [collector|post_processing|all] [status=succeeded|failed|all] [limit] [offset=0] [range=last24h|today|yesterday|last7d] [from=KST] [to=KST]
!pubg-worker-run run_id
!pubg-settings
!pubg-settings collector 180 100 10
!pubg-settings public-profile public|private
!pubg-permission user_id group allow|deny [guild_id|global]
!pubg-ranking-scope guild|global [guild_id]
!pubg-delete-data steam target registration|normalized|raw|replay|all [reason]
!pubg-delete-cancel request_id [reason]
!유저삭제 steam 닉네임또는accountId
```

Command access is checked through local Discord permission settings in `config/local_settings.json`. Slash commands are
the recommended interactive surface: every option has a Korean name and description, finite values use choices, and
players are selected only from registrations in the current Discord guild. Leaving the nickname blank opens a private
picker with 25 registrations per page, previous/next controls, and a partial nickname/account-ID search modal. This
avoids Discord's 25-option component limit without capping the guild registration count. Direct nickname entry remains
available. Match and replay commands show recent matches as KST date, Korean map/mode, placement, kills, and damage
while retaining the match ID only as the hidden option value.

The private picker protects the current guild's registration catalog, but the completed analysis is sent as a new
public channel message so every channel member can read it. `/배그도움말` is public as well. Security-sensitive
permission-management inputs and per-user validation errors remain caller-only; their controls cannot be used by
other members.

Recommendation replies are split into one concise field per weapon, two-weapon loadout, attachment combination,
individual attachment, range, map, teammate, or drop zone. Five fields are shown per page with previous/next controls,
so large result sets remain readable instead of becoming one continuous paragraph.
Recommendation lookup is available through `!추천 닉네임 [shard] [최소표본경기] [결과수]` and the equivalent slash
command; minimum sample size is caller-selectable from 1 to 100,000.
Named drop-zone rows show the Korean region when matched and preserve a map/grid fallback otherwise.
Fight win/loss lookup uses `!교전 닉네임 [shard]` or `!pubg-fights nickname [shard]`. `/추세` exposes separate
controls for granularity, team mode, perspective, map, game mode, match type, KST start/end dates, and bucket count;
there is no opaque catch-all option. It shares the `profile_read` permission group with `!pubg-trends`. When
`PUBG_LOCAL_WEB_BASE_URL` is set, trend replies include a prefilled local manager link.

The local player-analysis view also provides KST time-of-day analysis and player, weapon, and map comparison. Time
rows separate match share, chicken/win rate, fights per match, accuracy, headshot share among hits, and average
damage with their sample denominators. Comparison supports two to five catalog-selected subjects, shared detailed
filters, selectable metrics, and chart/table views. Player selection is retained while moving between weapon,
trend, time, comparison, and match tools, while profile search itself starts and returns to an empty query field.
Weapon detail separates the overall view from no-attachment, individual-attachment, and exact multi-attachment views.
It shows chicken rate and fight win rate separately, where kill/DBNO-caused is a fight win and death/DBNO-taken is a
fight loss, and reports the percentage-point difference from the no-attachment combat-snapshot baseline.
If `PUBG_LOCAL_WEB_BASE_URL` is set, weapon+attachment recommendation rows include local web evidence links for
supporting combat snapshots, and `pubg-alert-history` rows include local alert-detail links, a filtered local manager
page link, and a filtered CSV export link. This can be set from the local manager's `Local Web Link` section or through
`.env`. Leave it unset when Discord readers cannot reach the local web app.

Manage Discord command permissions from the local program or CLI. For first boot, add yourself as a global admin or
grant a command group to a Discord user ID:

```powershell
python -m pubg_ai.cli add-discord-global-admin 123456789012345678
python -m pubg_ai.cli grant-discord-permission 123456789012345678 register --guild-id 987654321098765432
python -m pubg_ai.cli grant-discord-permission 123456789012345678 player_manage --guild-id 987654321098765432
python -m pubg_ai.cli discord-permissions
```

After bootstrap, an admin can grant or revoke a command group with
`!pubg-permission @user group allow|deny [guild_id|global]`. With no final argument, the command targets the current
Discord server. Guild admins can only change their current guild; explicit other-guild or `global` grants require a
global admin. Only global admins can run `!pubg-ranking-scope guild|global [guild_id]`. Global-admin membership itself
remains local-program/CLI managed. The running bot reloads permission settings before each gated command, so local
manager and CLI changes apply without restarting the bot.

Built-in permission groups include `register`, `player_manage`, `profile_read`, `ranking_read`, `replay_read`,
`settings_write`, and `admin`. The localhost manager can also create, edit, and remove custom groups by selecting
commands from the validated command catalog. It can create prefix aliases that delegate only to a known command;
arbitrary code or shell commands cannot be registered. `player_manage` grants only `유저삭제`/`pubg-unregister`, which stops future collection
while retaining existing data. It does not grant permission management, destructive deletion review, alert control,
or worker administration. Existing `admin` grants continue to imply `player_manage`, while a delegated
`player_manage` grant does not imply `admin`.
`!pubg-settings` returns a deliberately restricted summary: collector limits, raw compression mode, public-profile
default, and the current guild ranking scope. It never returns API keys, bot tokens, database details, or storage
paths. Global admins and users with a global `settings_write` grant can update collector limits or the public-profile
default. A guild-only `settings_write` grant is read-only. Storage-path and compression changes remain
local-program-only.
`!pubg-delete-data` does not delete anything. It creates a 24-hour review request for an in-scope registered player,
stores the requested deletion scope and Discord context, and links to the localhost `Data Deletion Review` section.
The local owner can approve, reject, or cancel the request with an audit note; approval records authorization but
`execution_enabled` remains false. `!pubg-delete-cancel request_id [reason]` lets the requester, the request guild's
admins, or a global admin cancel a pending/approved request. The detail screen calls the read-only
`GET /data-deletions/{request_id}/preview` endpoint to count scoped rows and catalog configured raw/replay files.
Raw match and telemetry payloads are marked as protected match-shared records; only replay artifacts with the target
`account_id` and shard are classified as player artifact candidates. File checks resolve paths inside the configured
root and compare existence and size without reading payload contents or recomputing checksums. Schema version 11 adds
immutable preview snapshot and confirmation tables on top of the version 10 request/event tables. A localhost reviewer
may capture a maximum-500-file snapshot and record confirmation only for an approved request whose latest complete,
issue-free fingerprint still matches a fresh preview. The required text includes the full SHA-256 fingerprint.
Schema version 12 adds immutable read-only dry-run plans for a confirmed latest snapshot. Each plan revalidates the
live fingerprint, records ordered database and player-owned replay-file operation descriptors, preserves shared raw
match/telemetry evidence, lists backup prerequisites and postconditions, and stores its own canonical SHA-256. Plan
generation writes only the audit plan row. Schema version 13 adds append-only backup-evidence and rehearsal tables.
The localhost manager records corrected evidence as new rows and can run a non-executing rehearsal against the latest
plan, latest evidence set, live source fingerprint, backup file existence/size, and current quarantine free space.
`PUBG_BACKUP_DATA_DIR` and the Storage Settings screen configure a source-disjoint backup root. Exact entry of
`BUILD BACKUP ARTIFACTS REQUEST <request_id> <full_plan_fingerprint>` lets the opt-in builder export required
whitelisted rows as typed JSONL and copy required player-owned replay files. It calculates archive and entry SHA-256
values, publishes the build directory atomically, and appends one evidence row per generated artifact in one
transaction. Schema version 14 adds append-only read-only verification runs that reopen every declared ZIP, reject
unsafe or undeclared content, and verify manifests, JSONL framing/types/counts, CRC, byte totals, and SHA-256 values.
Schema version 15 adds the opt-in isolated restore rehearsal. The exact confirmation starts with
`RUN ISOLATED RESTORE REHEARSAL REQUEST`, then includes the request ID, verification ID, and full verification-result
fingerprint. The rehearsal revalidates every backup byte, restores MySQL rows only into random connection-scoped temporary tables, restores replay files only into
a random temporary directory under the backup root, reads both back, and removes all scratch resources. A passed run
atomically appends its immutable audit row and a `backup_integrity_verification` evidence row bound to the build,
verification run, artifact-evidence set, manifest, and restore-result fingerprints. Manual integrity attestation is
rejected, and a later artifact build makes older integrity evidence stale. Schema version 16 adds immutable read-only
quarantine-planning runs. Exact confirmation starts with `RUN READ-ONLY QUARANTINE PLAN`, and the planner revalidates
the latest dry-run plan, source identity/size/SHA-256, deterministic target absence, a pre-existing source-disjoint
quarantine root, and free capacity with a `max(64 MiB, 5%)` reserve. Passed runs atomically append planner-generated
`quarantine_capacity_check` evidence and an audit row; blocked runs append only the audit row. The plan records future
postconditions, rollback steps, and crash-recovery journal requirements but creates no directory or journal and does
not copy, move, remove, restore, or mutate source data. Manual capacity attestation is rejected. Schema version 17
adds immutable isolated quarantine-rehearsal runs. Exact confirmation starts with
`RUN ISOLATED QUARANTINE REHEARSAL` and binds the latest passed planning-result and destination fingerprints. The
rehearsal creates only deterministic synthetic fixtures inside a random owned direct child of the configured
quarantine root, exercises copy/verify/source-removal postconditions, no-overwrite rollback, known interrupted states,
and ambiguous-state blocking, then requires complete scratch cleanup. Production replay files are not opened or
modified. Windows journal replacement uses write-through `MoveFileExW`; POSIX uses atomic replace plus parent `fsync`.
Cleanup failure records a blocked immutable audit. No outcome creates readiness evidence or enables execution. Schema
version 18 adds immutable isolated combined-deletion rehearsal runs. Exact confirmation starts with
`RUN ISOLATED COMBINED DELETION REHEARSAL` and binds the latest dry-run plan, passed backup-verification result, latest
passed quarantine-planning result, and destination contract fingerprints. The runner revalidates backup artifacts,
restores candidate rows into generated connection-scoped MySQL temporary tables, applies only the planned selectors
there in a DELETE-only transaction, verifies preservation, rolls back, and compares the original row counts and
canonical row-set hashes. The same run exercises the version 17 synthetic quarantine state machine and requires all
scratch resources to be removed. It appends one audit row and creates no evidence or readiness state. Production rows
and files remain unchanged. Schema version 19 adds immutable isolated combined fault-matrix runs. Exact confirmation
starts with `RUN ISOLATED COMBINED FAULT MATRIX` and binds the current plan, latest passed combined rehearsal, backup
verification, quarantine planning, destination contract, and fixed four-scenario contract fingerprints. One scenario
injects a failure after the first positive-row DELETE against a generated temporary table and verifies that ROLLBACK
restores both row counts and canonical row-set hashes. Three synthetic quarantine scenarios interrupt after verified
copy, after source removal, and on the first cleanup attempt; each must recover and remove its owned scratch resources.
Remaining scenarios still run after an individual block so the immutable result reports the complete matrix. The run
appends only its version 19 audit row and creates no combined-rehearsal row, evidence, readiness state, production
mutation, or execution capability. Schema version 20 adds immutable advisory deletion-readiness review packets. Exact
confirmation starts with `GENERATE ADVISORY DELETION REVIEW PACKET` and binds the current request, dry-run plan, backup
verification, quarantine planning, combined rehearsal, fault matrix, and their canonical fingerprints. A latest passed
or blocked fault matrix can be captured so an operator receives the complete assessment instead of a false readiness
signal. The localhost API emits deterministic UTF-8 JSON through:

- `POST /data-deletions/{request_id}/review-packets`
- `GET /data-deletions/{request_id}/review-packets/{packet_id}`
- `GET /data-deletions/{request_id}/review-packets/{packet_id}/export.json`

Application version 21 adds `POST /data-deletion-review-packets/verify` and an `Exported review packet verifier` in
the localhost manager. The browser reads one selected `.json` file into memory, enforces a 2 MiB UTF-8 limit, and
sends raw JSON text so duplicate keys and non-standard constants can be rejected before canonical hashes, fixed v1
shape, safety flags, metrics, review checks, and fault scenarios are recomputed. Offline mode opens no database and
establishes internal consistency only, not provenance. The default MySQL mode executes parameterized `SELECT` queries
to require an exact immutable packet row and the current unexecuted request plus latest bound input chain. Neither mode
persists the uploaded text, creates a record, changes a row or file, grants authorization, promotes readiness, or
enables execution. Version 21 adds no environment variable or table; at that feature slice, the schema remained
version 20 with 44 tables.

Application version 22 adds `POST /data-deletion-review-packets/compare` and a two-file comparison panel. Both inputs
must first pass the version 21 verifier. The comparison is directional from baseline to candidate and reports input-ID,
fingerprint, assessment, review-check, and canonical-field changes. The full canonical difference count is retained
while at most 1,000 field rows are returned. Each file keeps the 2 MiB UTF-8 limit. Offline mode opens no database;
the default MySQL mode cross-checks both packets through one connection using only parameterized `SELECT` queries.
Packet text and comparison results remain in memory and are never persisted. No record, authorization, readiness,
evidence, production mutation, restore, quarantine, deletion, or execution capability is created. Version 22 adds no
environment variable or table; at that feature slice, the schema remained version 20 with 44 tables.

The current runtime schema is version 29 with 52 tables. Version 29 adds weapon-level `character_hits`,
`vehicle_hits`, and `vehicle_damage_dealt`, so vehicle impacts count toward total hit evidence without entering the
character-hit or headshot-hit denominator. Version 28 added authoritative many-to-many Discord guild registrations
for tracked players. Rerun `python -m pubg_ai.cli init-db` after updating; migrations are idempotent and do not rewrite
raw or replay artifacts. The local player manager can change collection/public-profile state independently and
connect one PUBG account to multiple known Discord guilds. See
[`docs/MULTI_DISCORD_AND_DIMENSION_ANALYTICS_2026-08-23.md`](docs/MULTI_DISCORD_AND_DIMENSION_ANALYTICS_2026-08-23.md)
for the data contract and live validation evidence. See also
[`docs/PROJECT_AUDIT_2026-08-28_FULL_SYSTEM.md`](docs/PROJECT_AUDIT_2026-08-28_FULL_SYSTEM.md) for the latest full
validation evidence. The previous 2026-08-24 baseline remains in
[`docs/PROJECT_AUDIT_2026-08-24.md`](docs/PROJECT_AUDIT_2026-08-24.md).

Each packet is guarded by six `ON DELETE RESTRICT` foreign keys. Generation appends only one version 20 audit row; it
grants no authorization,
promotes no readiness, creates no evidence, and performs no restore, quarantine, production mutation, or deletion. The
database archive still contains no schema DDL, and there is no production restore, quarantine mover, deletion endpoint,
executable deletion SQL, file remover, or execution button.
`executor_not_implemented` remains unconditional. Rerun
`python -m pubg_ai.cli init-db` after updating from an earlier schema.
The `admin` group includes `pubg-alerts`, which returns current storage and worker alerts. When
`PUBG_LOCAL_WEB_BASE_URL` is set, that response includes a local current-alert list link. When Discord alert channel
IDs are configured from the local manager, the running Discord bot also sends new worker failures and active capacity
alerts for all four configured storage roots to those channels. When `PUBG_LOCAL_WEB_BASE_URL` is set, automatic
storage and worker alert messages include local `alert_id` detail links, and worker failure alerts also include a local `worker_run_id` detail link.
Alerts are persisted in MySQL so the local manager can show alert history; using
the local manager's acknowledge or one-hour snooze action suppresses repeated local/Discord notifications for that
alert record. Admins can also run `pubg-alert-ack alert_id` or `pubg-alert-snooze alert_id [minutes]` directly in
Discord after reading the ID from `pubg-alerts` or an automatic alert message. Admins can attach incident notes with
`pubg-alert-note alert_id note` and resolution comments with `pubg-alert-resolution alert_id resolution`; those entries
are stored in the same MySQL `system_alert_notes` table shown by the local manager. Use
`pubg-alert-notes alert_id [limit]` to review the newest notes from Discord. When `PUBG_LOCAL_WEB_BASE_URL` is set,
those action, note, resolution, and note-list responses include a local alert-detail link, and matching usage/error
responses include the same link whenever the supplied alert ID can be parsed. Use `pubg-alert-history` with quick
presets or `source`/`state`/`severity`/`search` filters to review persisted alert history from Discord; when
`PUBG_LOCAL_WEB_BASE_URL` is set, each row includes a local detail link and the response includes both a filtered local
manager page link and a filtered CSV export link; parsed alert-history query errors include the same filtered page
and CSV export links, while parser errors include safe-default page and CSV links. When more history rows are
available, the response includes copyable previous/next `offset`
commands. Use
`pubg-worker-runs` to review recent collector and
post-processing cycle status, duration, error count, and last error directly from Discord; each row includes a
copyable `pubg-worker-run run_id` detail command for inspecting one run's summary metrics and full error list. When
`PUBG_LOCAL_WEB_BASE_URL` is set, the response includes local `worker_run_id` detail links, a filtered local manager
page link, and a filtered CSV export link; parsed worker-history query errors include the same filtered page and CSV
export links, while parser errors include safe-default page and CSV links. Filter the list with
`status=succeeded|failed|all` and KST
created-time ranges such as
`from=2026-07-01T00:00` and
`to=2026-07-02T00:00`, or quick presets such as `range=last24h`, `range=today`, `range=yesterday`, and
`range=last7d`; when more worker rows are available, the response includes copyable previous/next `offset` commands
that keep the selected worker, status, and date filters. The `pubg-worker-run run_id` detail response also includes
the same local link when the base URL is configured, and matching usage/error responses include it whenever the
supplied run ID can be parsed. `pubg-alerts` settings unavailable/load errors also include a local `#alerts` link
when the base URL is configured. `유저삭제` not-found responses include a local `#registered-players` link when the
base URL is configured. The local manager exposes stable anchors for profile, weapon, recommendation, match,
replay player, and replay artifact sections, and Discord `전적`, `무기`, `추천`, `매치`, and `최근스냅샷`
not-found/file error responses include those section links when the base URL is configured. Those lookup/replay links
carry shard, target, match, weapon, account, and replay artifact query parameters where the command has that context,
and the local page pre-fills the matching form or replay artifact filter from the URL. Successful `전적`, `무기`,
`추천`, `매치`, and `최근스냅샷` responses also include contextual `local_profile`, `local_weapon`,
`local_recommendations`, `local_match`, or `local_replay` links when the base URL is configured.
`유저등록`, `유저조회`, and `유저삭제` success responses include contextual `local_registered_players` links,
while `랭킹` success responses include `local_ranking` links. The local page can highlight a linked registered player
row or pre-fill the ranking form from those URLs. Ranking, Discord permission, and ranking-scope forms use synchronized
Discord server-name selectors; guild IDs remain secondary reference data, and ranking lists only servers that have
registered tracking targets. Authorized `pubg-permission` and `pubg-ranking-scope` success,
usage, and settings-error responses include contextual `#discord-permissions` or `#discord-scopes` links that pre-fill
the local forms. `pubg-settings` responses link to stable `#collector-settings`, `#storage-settings`, and
`#discord-scopes` sections; successful collector/public-profile updates pre-fill the affected local controls.
Deletion-request responses link to `#data-deletions` with the request ID highlighted; the local detail view shows the
complete audit event history, scoped row counts, protected references, raw/replay file status, immutable snapshot
history, fingerprint-bound confirmation history, and confirmed dry-run plan history. The latest plan exposes ordered
row/file descriptors, backup prerequisites, protected exclusions, candidate bytes, and its plan fingerprint. It also
shows prerequisite evidence history, latest metadata-only rehearsal checks, rehearsal history, and stale/invalid audit
blockers. Catalog totals remain complete when the displayed file list is limited, while confirmation, plan generation,
and rehearsal require current plan/fingerprint bindings. Every view and mutation response explicitly reports that
deletion execution is disabled and not ready.
Permission-denied and blocked privilege-boundary attempts remain plain text by design.

The local player-analysis forms use registered-player nickname search backed by stored account IDs. Weapon and match
selection come from that player's local catalog instead of requiring raw codes or memorized match IDs. The selected
registered player remains active across profile, trend, weapon, recommendation, drop-zone, and match views until the
operator explicitly releases it. Weapon and trend views expose map/mode/season-state and KST
year/quarter/month/date/hour filters. Chronological groupings render real line charts with denominator and match-sample
notes; categorical groupings retain comparison charts. Each weapon also provides daily/monthly graphs for combat,
accuracy, damage, kill, DBNO, death, and usage metrics. Recommendation results lead with a close/mid plus
DMR/SR/Crossbow two-weapon combination and weapon-specific translated attachments. Accuracy, headshot-hit probability,
fight-win probability, hit/taken body-part distributions, and per-match combat averages are available in current
totals and trends. Headshot-hit probability is head hits divided by hits, never by misses.
Loadout score details expose their performance components and the heuristic PUBG inventory-unit cost of mixed ammo,
shared ammo pools, and LMG reserve ammunition.

Run the Windows desktop manager (recommended):

```powershell
python -m pip install -e ".[desktop]"
python -m pubg_ai.cli run-desktop --maximized
```

After the desktop dependency is installed, `run_desktop.cmd` provides the same maximized launch by double-click. In a
repository checkout it always runs the current `src` tree first, so an older packaged executable cannot hide newer
local-manager changes. `dist/PUBG_AI_Manager.exe` is used only when Python or the source tree is unavailable. A source
startup error is reported instead of being hidden by a fallback to an older executable.
Desktop mode always starts its own FastAPI manager. If the configured localhost port is already occupied, it scans
forward for an available local port instead of attaching a new window to a stale or older manager build. The header
shows the active release identifier and the native window exposes Windows folder pickers for Raw, Replay,
deletion-backup, and quarantine roots; the selected value is not persisted until the Storage Settings Save button is
pressed. Closing the window stops only services owned by that launch, including its Discord bot controller. The bind
boundary remains localhost-only. The supplied artwork is packaged as the window, taskbar, executable, header, and
favicon icon.

The local `Display Settings` number format is persisted in `config/local_settings.json`, so grouped, Korean-unit, or
plain rendering remains consistent when the desktop launcher selects a different localhost port. The left navigation
entry `Discord 봇` opens the one app-managed bot workspace with write-only PUBG API key and Discord token fields plus
start, stop, sync, auto-start, prefix, permissions, server scopes, alerts, and per-guild command visibility controls.

Build the local Windows executable:

```powershell
python -m pip install -e ".[desktop-build]"
python -m PyInstaller --clean --noconfirm pubg_ai_desktop.spec
```

The result is `dist/PUBG_AI_Manager.exe`. The repository-root `run_desktop.cmd` prefers the current source and keeps
that executable as its fallback. The executable searches its parent directories for the project `.env` and `config`
directory; `PUBG_AI_BASE_DIR` can override that location explicitly.

With the local manager and app-managed bot running, verify the command definitions that Discord actually stores:

```powershell
python scripts\verify_discord_command_deployment.py
```

The verifier reads the token through the existing `.env` loader without printing it, checks every managed guild
through Discord's API, and fails when command counts, Korean option descriptions, autocomplete flags, or static-choice
limits do not match the expected deployment.

Run the browser-only local manager instead:

```powershell
python -m pubg_ai.cli run-web --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

Validate the current player-intelligence materialization after a collection or backfill cycle:

```powershell
python -m pubg_ai.cli audit-player-intelligence
```

The command returns exit code `0` only when all checks pass. Telemetry stored within the most recent 15 minutes is
shown as processing-grace data and excluded from the mature coverage denominator. Older missing outputs fail the
audit instead of being silently treated as zero activity. Custom and training matches remain preserved in raw storage
but are excluded from the analytical audit through `analysis_matches`.

The web app refuses non-localhost bind hosts by default. Do not run it with `0.0.0.0` unless a future authenticated
remote-access mode is intentionally added.

The local manager can start or stop the in-process automatic collector and post-processing workers. They stop when the
local web server stops; use the CLI `run-collector` and `run-post-processing` commands for separate long-running
worker processes. Both worker entry points store recent cycle summaries in `worker_run_history`; the local manager
shows those rows in `Worker Run History`, and admins can query them with `pubg-worker-runs`, so storage/API/parser
failures remain visible after the in-memory status changes. The local manager can filter those worker rows by worker
name, succeeded/failed status, and KST created-time range, page through older runs, and open one run's summary metrics
plus full stored errors from the table. The date controls include quick ranges for recent 1h, recent 24h, today,
yesterday, and recent 7d lookups, and the filtered rows can be exported as CSV for local incident review. Opening a
worker run also keeps a copyable `worker_run_id` detail link in the browser URL, and the filter bar can copy a
shareable local URL that restores the same worker/status/time window and page offset. Worker links use `#worker-runs`
or #workerRunDetail anchors so shared URLs land near the relevant panel. The same page stores storage/worker
alert records in
`system_alert_history`, shows current unsuppressed
alerts separately from recent history, lets the admin acknowledge or temporarily hide noisy alerts, and can filter or
page history by source/status/severity, search title/message text, and sort it by newest, oldest, or severity-first
when many old alerts have accumulated. Quick presets jump directly to current errors, worker failures, storage
pressure, or the full history, and worker failure rows can jump straight to the related worker-run detail panel. The
filtered and searched history can also be exported as CSV from the local manager, and the filter bar can copy a
shareable local URL that restores the same source/status/severity/search/sort window and page offset. Each alert history
row can also store persistent admin notes and resolution comments, and the local manager shows list-level
state/severity badges and can
open a detail panel with status badges, ack/snooze controls, full note history, and inline note/resolution entry for
one alert. Alert links use `#alerts` or `#alertHistoryDetail` anchors so shared URLs land near the relevant panel.
