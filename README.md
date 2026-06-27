# PUBG AI Local Analytics Research

PUBG Open API, MySQL, Discord bot, and a local management app are planned as one local-first analytics system.
This repository currently contains the first research pass: official API behavior, telemetry collection strategy,
MySQL data model direction, 2D replay/live-view feasibility, and reference project survey.

## Current Documents

- [PUBG Open API Research](docs/PUBG_OPEN_API_RESEARCH.md)
- [Local Architecture and MySQL Model](docs/LOCAL_ARCHITECTURE_AND_MYSQL_MODEL.md)
- [Implementation Decisions](docs/IMPLEMENTATION_DECISIONS.md)
- [Configuration](docs/CONFIGURATION.md)
- [Reference Project Survey](docs/REFERENCE_PROJECT_SURVEY.md)
- [Sources](docs/SOURCES.md)

## Key Decisions From Research

- Registered users are the only primary collection target.
- Nickname registration requires a platform shard, then resolves `accountId`; later polling and matching use
  `accountId`.
- All discovered match types are collected and immediately classified by `game_mode`, `match_type`, map, shard,
  team mode, perspective, ranked/custom flags, and completion-only availability.
- MySQL-facing timestamps are stored in KST because the expected audience is primarily Korean users.
- Match and telemetry data should be stored as immutable raw JSON first, then normalized into analysis tables.
- Large raw match and telemetry files should be saved under a configurable external storage path such as
  `PUBG_RAW_DATA_DIR=E:\PUBG_AI_Data\raw`; MySQL stores metadata and relative paths.
- Generated 2D replay files should use a separate configurable path such as
  `PUBG_REPLAY_DATA_DIR=E:\PUBG_AI_Data\replays`.
- The local management app should save user-changed storage paths to `config/local_settings.json`, so paths can be
  changed from the program without editing `.env`.
- PUBG match detail and telemetry are available after the match finishes. A 2D viewer is therefore post-match replay,
  not in-match live tracking.
- The local stack should be MySQL + local API/worker + Discord bot + local web management UI.

## Proposed First Build Slice

1. Register player nickname and platform shard with an authorized Discord command.
2. Resolve and store PUBG `accountId`.
3. Poll registered players for recent match IDs.
4. Fetch unseen matches and telemetry.
5. Persist raw JSON and normalized event rows.
6. Expose Discord commands for match summary, KDA, weapon usage, map usage, and recent chicken/non-chicken split.
7. Add a local 2D replay page that plays telemetry positions and fight events on a map canvas.
