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
- `GET /alerts/history?source=all|storage|worker&state=all|current|active|acknowledged|snoozed|resolved&severity=all|error|warning|info|ok&sort=newest|oldest|severity&search=drive&limit=50&offset=0`
  returns paged alert history with a total count for the management UI. `search` matches alert titles and messages.
- `GET /alerts/history/export.csv` exports the same filtered, searched, and sorted history to CSV, bounded to 5,000
  rows per request.
- `GET /alerts/history/{alert_id}/notes` returns persisted admin notes and resolution comments for one alert.
- `POST /alerts/history/{alert_id}/notes` stores a `note` or `resolution` comment for one alert.
- `POST /alerts/history/{alert_id}/acknowledge` marks a persisted alert as acknowledged.
- `POST /alerts/history/{alert_id}/snooze` hides a persisted alert until the requested KST expiry, capped at 30 days.
- `GET /workers/runs?worker_name=collector&status=failed&created_from_kst=2026-07-01T00:00&created_to_kst=2026-07-02T00:00&limit=50&offset=0`
  returns paged worker-run history for the local manager, including total count and previous/next state for
  worker/status/KST-created-time filters.
- `GET /workers/runs/export.csv` exports the same filtered worker-run history to CSV, bounded to 5,000 rows per
  request, including summary JSON and stored error lists for local incident review.
- The local manager worker-run filter form can fill those KST date fields with quick ranges for recent 1h, recent
  24h, today, yesterday, and recent 7d lookups.
- `GET /workers/runs/{run_id}` returns one worker cycle with the stored summary JSON and error list for the local
  manager detail panel.
- The local manager can load `/?worker_run_id={run_id}` directly, updates the browser URL when a worker-run detail is
  opened, and exposes a copy button for sharing that local detail link with another admin on the same machine.
- The local manager can also load and copy worker-run history filter URLs with `worker_run_worker`,
  `worker_run_status`, `worker_run_range`, `worker_run_from`, `worker_run_to`, `worker_run_limit`, and
  `worker_run_offset` query parameters for local incident-review handoff.
- The local manager can also load and copy alert-history filter URLs with `alert_history_source`,
  `alert_history_state`, `alert_history_severity`, `alert_history_sort`, `alert_history_search`,
  `alert_history_limit`, and `alert_history_offset` query parameters for local incident-review handoff.
- Local manager share links and Discord local links use `#alerts`, `#alertHistoryDetail`, `#worker-runs`, and
  `#workerRunDetail` anchors so incident handoff opens near the relevant table or detail panel.
- The current-alert and alert-history tables show severity/state badges so admins can scan alert lists before opening
  a detail row. Quick preset buttons set common alert-history filters for current errors, worker failures, storage
  pressure, and the full history.
- Worker failure alerts include a `Worker run` control in current alerts, alert-history rows, and alert-history detail
  panels whenever the alert metadata contains a worker run ID.
- The local manager alert history table can open a detail panel that shows the selected alert and its full recent note
  history without reading the raw JSON API response, shows a state badge for active/acknowledged/snoozed/resolved, and
  can acknowledge, snooze, or add `note` and `resolution` comments.
- The Discord bot sends active storage alerts once per bot process and sends newly persisted worker failures from
  `worker_run_history`; acknowledged and currently snoozed alert records are skipped. When `PUBG_LOCAL_WEB_BASE_URL`
  is set, automatic storage and worker alert messages include local `alert_id` detail links, and worker failure
  messages also include a local `worker_run_id` detail link.
- The admin-only `pubg-alerts` Discord command returns the current alert report on demand, including alert IDs. When
  `PUBG_LOCAL_WEB_BASE_URL` is set, the response includes a local current-alert list link.
- The admin-only `pubg-alert-ack alert_id` and `pubg-alert-snooze alert_id [minutes]` commands update the same
  persisted alert history from Discord. When `PUBG_LOCAL_WEB_BASE_URL` is set, their responses include a local
  alert-detail link.
- The admin-only `pubg-alert-note alert_id note` and `pubg-alert-resolution alert_id resolution` commands append
  `note` and `resolution` rows to the same persisted alert history from Discord. When `PUBG_LOCAL_WEB_BASE_URL` is
  set, their responses include a local alert-detail link.
- The admin-only `pubg-alert-notes alert_id [limit]` command lists recent notes and resolution comments for one alert
  without opening the local manager, and can include the same local alert-detail link.
- Alert action, note, resolution, and note-list usage/error responses also include that local detail link whenever an
  alert ID was supplied and parsed successfully.
- The admin-only `pubg-alert-history` command lists persisted alert history from Discord with quick presets or
  `source`/`state`/`severity`/`search`/`limit` filters. When `PUBG_LOCAL_WEB_BASE_URL` is set, those rows include
  local detail links that open the management UI with `alert_id` selected, and the response includes a filtered local
  manager page link plus a filtered CSV export link for the same source/state/severity/search window. If parsed filters
  later fail during history lookup, the error response includes the same filtered local manager page link and CSV export
  link. If filters cannot be parsed, the usage/error response still includes safe-default local manager and CSV links.
  When the query has more rows, the response includes copyable previous/next commands with the right `offset`.
- The admin-only
  `pubg-worker-runs [collector|post_processing|all] [status=succeeded|failed|all] [limit] [range=last24h|today|yesterday|last7d] [from=KST] [to=KST]`
  command lists recent worker cycles from `worker_run_history` with status, duration, error count, last error,
  copyable previous/next `offset` commands, and a copyable detail command for each row. `from`/`to` filter
  `created_at_kst` ranges in KST, while `range=last24h|today|yesterday|last7d` expands to common KST ranges. Date
  filters are preserved in pagination commands. When `PUBG_LOCAL_WEB_BASE_URL` is set, each row also includes a local
  `worker_run_id` detail link, and the response includes a filtered local manager page link plus a filtered CSV export
  link for the same worker/status/time window. If parsed filters later fail during history lookup, the error response
  includes the same filtered local manager page link and CSV export link. If filters cannot be parsed, the usage/error
  response still includes safe-default local manager and CSV links.
- The admin-only `pubg-worker-run run_id` command opens one worker cycle from Discord and shows summary metrics plus
  the full stored error list. When `PUBG_LOCAL_WEB_BASE_URL` is set, the response also includes a local
  `worker_run_id` detail link, and usage/error responses include that same link whenever the supplied run ID can be
  parsed.

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
