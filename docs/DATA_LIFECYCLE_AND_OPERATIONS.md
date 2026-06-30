# Data Lifecycle and Operations

Decision date: 2026-06-28

## Parser Version Policy

Telemetry parsing is versioned. Every parse run stores:

- `parser_version`
- `parse_status`
- `parsed_at`
- `source_match_id`
- raw match payload checksum
- raw telemetry payload checksum
- error message when parsing fails

When the parser version changes, existing parsed rows from older parser versions become candidates for reparse.
Raw match and telemetry files must remain available so the system can rebuild normalized rows without calling the
PUBG API again.

Initial parser version: `telemetry-parser-v1`.

## Raw File Retention

Raw match and telemetry files are retained indefinitely by default.

Rules:

- Do not automatically delete raw match files.
- Do not automatically delete raw telemetry files.
- Deduplicate raw files by `match_id`.
- If disk capacity is insufficient, stop the affected write job and raise an error notification.
- Capacity errors must be visible in the local management program.
- Capacity errors must also be sent to Discord for users/admins who have alert permission.

Replay artifacts are generated data. They can be rebuilt from raw telemetry, so they may receive separate retention
rules later. This includes timeline JSON, static map snapshot JPEG/PNG files, thumbnails, GIFs, videos, and renderer
caches. Raw official match and telemetry files are the priority.

## Storage Capacity Alerts

The storage monitor checks configured raw and replay roots. If free space is below the configured minimum, the system
creates an error notification for:

- `local_program`
- `discord`

The notification should include:

- path
- free bytes
- configured minimum free bytes
- affected storage role, such as raw data or replay artifacts
- next action: free disk space or change the configured storage path

The system should not silently switch to another path and should not delete raw files automatically.

Implemented behavior:

- The local manager stores alert settings in `config/local_settings.json` under `alerts`.
- `minimum_free_bytes` controls the raw/replay free-space threshold. The default is 50 GiB.
- `discord_channel_ids` controls where the Discord bot sends automatic alert messages.
- `GET /alerts/status` returns current unsuppressed alerts and recent alert history for the management UI.
- `POST /alerts/history/{alert_id}/acknowledge` marks a persisted alert as acknowledged.
- `POST /alerts/history/{alert_id}/snooze` hides a persisted alert until the requested KST expiry, capped at 30 days.
- The Discord bot sends active storage alerts once per bot process and sends newly persisted worker failures from
  `worker_run_history`; acknowledged and currently snoozed alert records are skipped.
- The admin-only `pubg-alerts` Discord command returns the current alert report on demand.

## Duplicate Match Handling

The same match can be discovered from multiple registered players. The raw layer stores one match JSON and one
telemetry JSON per `match_id`.

Player-specific analysis is still generated for every tracked player in the match. This means one raw match can feed
many player summaries and team/weapon aggregates.

## Collection Failure Handling

| Failure | Action |
| --- | --- |
| Rate limit | Retry after backoff |
| Match detail not yet available | Retry later |
| Telemetry URL missing | Retry until match data is stable, then mark for review |
| Storage path missing | Notify local program and Discord, retry after operator fixes path |
| Low disk capacity | Notify local program and Discord, pause writes for affected storage |
| Raw checksum mismatch | Mark terminal until operator review |
| Parser error | Preserve raw files, mark parse run failed, allow parser-version reparse |
| MySQL connection failure | Retry after backoff |

## Administrative Actions

Admins can:

- stop collection for a player while retaining existing data
- choose destructive deletion only when needed
- trigger reparse for a match, player, date range, or parser version
- regenerate replay artifacts, including map snapshot images
- change storage paths
- change polling settings
- change Discord permission grants
- change guild ranking scopes
- manage global admins

## Generated Map Snapshots

Static map snapshots are generated after telemetry parsing and stored under `PUBG_REPLAY_DATA_DIR`.

Rules:

- Do not store generated JPEG/PNG images in MySQL; store only artifact metadata and relative paths.
- Store `artifact_type = map_snapshot`, content type, file size, checksum, renderer version, and generated timestamp.
- Regenerate snapshots when the renderer version, map asset version, or route simplification policy changes.
- If replay storage is missing or full, notify the local program and Discord just like other replay artifact writes.
- Deleting or regenerating snapshots must not delete raw match or telemetry files.

## Secret Handling

Secrets are outside the local settings lifecycle.

- `PUBG_API_KEY` is stored only in `.env`.
- `DISCORD_BOT_TOKEN` is stored only in `.env`.
- Local program settings must reject secret fields.
- Logs and UI should show masked status only, such as configured or missing.
