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
  changed from the program without editing `.env`.
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
- localhost-only FastAPI management app
- browser UI for status, user registration, user lookup, collection stop/delete action, match job processing, and
  telemetry job/combat processing

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

Parse raw telemetry into registered-player combat summaries and weapon stats:

```powershell
python -m pubg_ai.cli parse-telemetry-combat --limit 10
```

Reparse existing combat summaries after parser changes:

```powershell
python -m pubg_ai.cli parse-telemetry-combat --limit 200 --force
```

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
