# PUBG AI Local Analytics Research

PUBG Open API, MySQL, Discord bot, and a local management app are planned as one local-first analytics system.
This repository currently contains the first research pass: official API behavior, telemetry collection strategy,
MySQL data model direction, 2D replay/live-view feasibility, and reference project survey.

## Current Documents

- [PUBG Open API Research](docs/PUBG_OPEN_API_RESEARCH.md)
- [Local Architecture and MySQL Model](docs/LOCAL_ARCHITECTURE_AND_MYSQL_MODEL.md)
- [Reference Project Survey](docs/REFERENCE_PROJECT_SURVEY.md)
- [Sources](docs/SOURCES.md)

## Key Decisions From Research

- Registered users are the only primary collection target.
- Nickname lookup is used once to resolve `accountId`; later polling and matching should use `accountId`.
- Match and telemetry data should be stored as immutable raw JSON first, then normalized into analysis tables.
- True in-match live data is not exposed by the public PUBG Open API. A 2D viewer should therefore start as
  telemetry replay and near-live post-match playback.
- The local stack should be MySQL + local API/worker + Discord bot + local web management UI.

## Proposed First Build Slice

1. Register player nickname and shard.
2. Resolve and store PUBG `accountId`.
3. Poll registered players for recent match IDs.
4. Fetch unseen matches and telemetry.
5. Persist raw JSON and normalized event rows.
6. Expose Discord commands for match summary, KDA, weapon usage, map usage, and recent chicken/non-chicken split.
7. Add a local 2D replay page that plays telemetry positions and fight events on a map canvas.

