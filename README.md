# PUBG AI Local Analytics Research

PUBG Open API, MySQL, Discord bot, and a local management app are planned as one local-first analytics system.
This repository currently contains the first research pass: official API behavior, telemetry collection strategy,
MySQL data model direction, 2D replay/live-view feasibility, and reference project survey.

## Current Documents

- [PUBG Open API Research](docs/PUBG_OPEN_API_RESEARCH.md)
- [PUBG Collection Flow](docs/PUBG_COLLECTION_FLOW.md)
- [Local Architecture and MySQL Model](docs/LOCAL_ARCHITECTURE_AND_MYSQL_MODEL.md)
- [Implementation Decisions](docs/IMPLEMENTATION_DECISIONS.md)
- [Data Lifecycle and Operations](docs/DATA_LIFECYCLE_AND_OPERATIONS.md)
- [Code Translation](docs/CODE_TRANSLATION.md)
- [Sample Match Analysis](docs/SAMPLE_MATCH_ANALYSIS.md)
- [Configuration](docs/CONFIGURATION.md)
- [Reference Project Survey](docs/REFERENCE_PROJECT_SURVEY.md)
- [Additional Reference Research](docs/ADDITIONAL_REFERENCE_RESEARCH.md)
- [Sources](docs/SOURCES.md)

## Key Decisions From Research

- Registered users are the only primary collection target.
- Registered PUBG players are treated as admin-managed tracking targets, not ownership claims by Discord users.
- Nickname registration requires a platform shard, then resolves `accountId`; later polling and matching use
  `accountId`.
- PUBG API key and Discord bot token stay only in `.env`; local program settings must not store or display them.
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
- combat loadout snapshot generator for weapon + attachment state at kill/DBNO/finish moments
- localhost-only FastAPI management app
- browser UI for status, user registration, user lookup, collection stop/delete action, match job processing, and
  telemetry job/combat/item processing
- browser UI for raw/replay storage paths, raw compression, collector limits, Discord permission grants, guild ranking
  scopes, public profile defaults, and local evidence-link base URL settings
- automatic collector loop from CLI or the local manager for player refresh, match-detail storage, and telemetry
  download cycles
- automatic post-processing loop from CLI or the local manager for combat/item/movement parsing, loadout snapshots,
  map JPEG snapshots, and replay timelines
- persistent worker run history in MySQL with a local manager table/detail panel for recent collector/post-processing
  cycles, summary metrics, and full stored errors
- local and Discord alert settings for storage pressure and worker failures, including configurable Discord alert
  channel IDs, alert acknowledgement/snooze controls, and persisted alert history without storing bot tokens outside
  `.env`

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

Show first-pass recommendations from parsed summary tables:

```powershell
python -m pubg_ai.cli player-recommendations Yuuki_Asuna--- --shard steam --min-matches 1
```

Recommendations include distance-weighted weapon ranges and weapon+attachment pairs. When generated, combat loadout
snapshots are used first so parts reflect the actual kill/DBNO/finish moment; attach-event co-occurrence remains the
fallback for older parsed data.

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
python -m pubg_ai.cli run-post-processing --combat-limit 10 --item-limit 10 --movement-limit 10 --loadout-limit 50 --map-snapshot-limit 10 --timeline-limit 10
```

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

The local web app includes a 2D replay player that loads generated `timeline` JSON artifacts, uses cached official
map PNG assets as the canvas background when available, and renders movement, plane route, phase rings, landing,
combat, care-package, and revive markers when telemetry has the related events. The player also includes a
time-sorted event list and a detail panel so fight, landing, and care-package events can be clicked to seek directly
to that moment. Timeline JSON also carries the tracked player's
team roster and marks registered teammates, so the local replay panel can show who was in the squad for that match.
When teammate position samples exist, the player can draw teammate route overlays with current-position labels.
The replay view also supports map zoom and tracked-player follow mode for inspecting dense fight moments.

Run the Discord bot MVP:

```powershell
python -m pubg_ai.cli run-discord-bot --prefix !
```

The Discord bot token stays only in `.env` as `DISCORD_BOT_TOKEN`. The bot currently uses text commands and requires
Discord's message content intent to be enabled for the bot application. Initial commands are:

```text
!배그도움말
!유저등록 steam 닉네임
!유저조회 [닉네임] [shard]
!전적 닉네임 [shard]
!무기 닉네임 무기명 [shard]
!매치 match_id [닉네임|accountId] [shard]
!랭킹 [지표] [shard] [limit] [전체]
!최근스냅샷 [match_id]
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
!유저삭제 steam 닉네임또는accountId
```

Command access is checked through local Discord permission settings in `config/local_settings.json`.
Recommendation lookup is available through `!추천 닉네임 [shard]` and `!pubg-recommend nickname [shard]`.
If `PUBG_LOCAL_WEB_BASE_URL` is set, weapon+attachment recommendation rows include local web evidence links for
supporting combat snapshots, and `pubg-alert-history` rows include local alert-detail links, a filtered local manager
page link, and a filtered CSV export link. This can be set from the local manager's `Local Web Link` section or through
`.env`. Leave it unset when Discord readers cannot reach the local web app.

Manage Discord command permissions from the local program or CLI. For first boot, add yourself as a global admin or
grant a command group to a Discord user ID:

```powershell
python -m pubg_ai.cli add-discord-global-admin 123456789012345678
python -m pubg_ai.cli grant-discord-permission 123456789012345678 register --guild-id 987654321098765432
python -m pubg_ai.cli discord-permissions
```

Permission groups currently include `register`, `profile_read`, `ranking_read`, `replay_read`, `settings_write`, and
`admin`.
The `admin` group includes `pubg-alerts`, which returns current storage and worker alerts. When
`PUBG_LOCAL_WEB_BASE_URL` is set, that response includes a local current-alert list link. When Discord alert channel
IDs are configured from the local manager, the running Discord bot also sends new worker failures and active storage
capacity alerts to those channels. When `PUBG_LOCAL_WEB_BASE_URL` is set, automatic storage and worker alert messages
include local `alert_id` detail links, and worker failure alerts also include a local `worker_run_id` detail link.
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
manager page link and a filtered CSV export link. When more history rows are available, the response includes copyable
previous/next `offset` commands. Use `pubg-worker-runs` to review recent collector and
post-processing cycle status, duration, error count, and last error directly from Discord; each row includes a
copyable `pubg-worker-run run_id` detail command for inspecting one run's summary metrics and full error list. When
`PUBG_LOCAL_WEB_BASE_URL` is set, the response includes local `worker_run_id` detail links, a filtered local manager
page link, and a filtered CSV export link. Filter the list with `status=succeeded|failed|all` and KST
created-time ranges such as
`from=2026-07-01T00:00` and
`to=2026-07-02T00:00`, or quick presets such as `range=last24h`, `range=today`, `range=yesterday`, and
`range=last7d`; when more worker rows are available, the response includes copyable previous/next `offset` commands
that keep the selected worker, status, and date filters. The `pubg-worker-run run_id` detail response also includes
the same local link when the base URL is configured, and matching usage/error responses include it whenever the
supplied run ID can be parsed.

Run the local management app:

```powershell
python -m pubg_ai.cli run-web --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

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
