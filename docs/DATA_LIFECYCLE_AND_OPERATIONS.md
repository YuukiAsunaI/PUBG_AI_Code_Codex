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
  `PUBG_LOCAL_WEB_BASE_URL` is set, the response includes a local current-alert list link. Settings unavailable/load
  error responses include the same local `#alerts` section link when the base URL is configured.
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
- Discord admin usage/error local-link audit: alert action commands, alert-history, worker-run history, and worker-run
  detail commands now attach a local detail/filter/export link whenever a parsed `alert_id`, parsed `worker_run_id`,
  parsed filters, or safe defaults give a stable target.
- Permission-denied and server-channel-required responses intentionally remain plain text because they are not tied to
  a local object and should not expose extra admin UI context.
- The local manager registered-player list has a stable `#registered-players` anchor. `유저삭제` not-found responses
  include that local registered-player list link when `PUBG_LOCAL_WEB_BASE_URL` is configured.
- The local manager profile, weapon, recommendation, match, replay player, and replay artifact sections have stable
  anchors: `#profile-lookup`, `#weapon-lookup`, `#recommendation-lookup`, `#match-lookup`, `#replay-player`, and
  `#replay-artifacts`. Discord `전적`, `무기`, `추천`, `매치`, and `최근스냅샷` not-found/file error responses
  include the matching section links when `PUBG_LOCAL_WEB_BASE_URL` is configured. These links carry shard, target,
  match, weapon, account, and replay artifact query parameters when available, and the local page pre-fills the
  matching lookup form or replay artifact filter from the URL. Successful `전적`, `무기`, `추천`, `매치`, and
  `최근스냅샷` responses also include contextual local manager links when the base URL is configured. `유저등록`,
  `유저조회`, and `유저삭제` success responses include contextual registered-player links, while `랭킹` success
  responses include ranking links, with row highlighting and ranking form pre-fill support on the local page.
- Current admin-link coverage leaves permission-denied and server-channel-required responses as the only intentional
  plain-text admin cases because they are not tied to a local object.
- The local Discord settings sections have stable `#discord-permissions` and `#discord-scopes` anchors. Authorized
  `pubg-permission` and global-admin-only `pubg-ranking-scope` success, usage, and settings-error responses include
  contextual links that pre-fill the affected user/group/guild or guild/ranking-scope form. Blocked privilege-boundary
  attempts remain plain text.
- `pubg-settings` exposes only collector limits, compression mode, public-profile default, and the current guild
  ranking scope. Global `settings_write` grants can change collector limits or the public-profile default; guild-only
  grants are read-only. Secrets, database details, and storage paths are never returned, and storage/compression
  changes remain local-program-only. Responses link to stable local settings anchors with safe form pre-fill values.
- `pubg-delete-data shard target scope [reason]` creates a 24-hour `pending` review request and a `requested` audit
  event. It performs no database or file deletion. Local review may transition it to `approved`, `rejected`, or
  `cancelled`; pending requests expire automatically, and every transition records actor, note, details, and KST time.
- Approval is authorization only. The API returns `execution_enabled=false`, and there is no execution route or UI
  button. Request rows retain player identity snapshots and audit events use `ON DELETE RESTRICT` so review history
  cannot be discarded accidentally.
- `GET /data-deletions/{request_id}/preview` is read-only. It counts registration and player-owned normalized rows,
  lists preserved cross-player/shared-match references separately, and never includes deletion-request audit tables.
- Raw player snapshots are player candidates, but raw match/telemetry payload metadata and files are protected as
  match-shared because the same payload feeds every participant. Replay candidates require an exact target
  `account_id` and shard match.
- File verification accepts only `local_file` records under the expected configured root. It checks path safety,
  existence, file type, and declared size by metadata only; payload bytes and SHA-256 contents are not read during
  preview. Limited catalogs keep complete record/byte totals and report `truncated=true`.
- A localhost reviewer may persist a maximum-500-file preview as an immutable versioned manifest plus SHA-256
  fingerprint. Snapshot and confirmation rows reference the deletion request with `ON DELETE RESTRICT` and are never
  updated by the application.
- Confirmation is allowed only for the latest snapshot of an `approved` request when the catalog is complete,
  filesystem issues are zero, at least one player-owned candidate exists, and a fresh preview produces the same
  fingerprint. The reviewer must type the complete fingerprint-bound confirmation text exactly.
- A confirmation stores the actor, KST time, snapshot/fingerprint, note, and confirmation-text hash. It does not change
  request status, enable execution, run deletion SQL, remove files, or weaken the requirement for future live
  revalidation.
- `GET /data-deletions/{request_id}/dry-run-state` returns immutable plan history and the latest full plan.
  `POST /data-deletions/{request_id}/dry-run-plans` is available only through the localhost manager and writes one
  audit-plan row after locking and rechecking the approved request, latest snapshot, matching confirmation, and fresh
  maximum-500-file fingerprint.
- A dry-run plan stores ordered non-executable database selectors and player-owned replay quarantine descriptors.
  Player-specific derived rows precede participants, replay files precede replay metadata, and collection state,
  aliases, and registration are last. It never stores executable `DELETE FROM` statements.
- Match-shared raw metadata/files, shared match context, cross-player references, and every deletion audit table are
  explicit exclusions. Backup creation, capacity, checksum, and restore-rehearsal evidence are prerequisites, while
  `executor_not_implemented` and `backup_evidence_not_recorded` keep every plan non-ready and non-executable.
- Backup evidence is append-only and bound to the latest dry-run plan fingerprint. The four keys cover MySQL artifacts,
  replay artifacts, quarantine capacity, and checksum/restore integrity. Artifact evidence comes from the builder,
  capacity evidence only from a passed read-only quarantine plan, and integrity evidence only from a passed isolated
  restore rehearsal. Manual capacity and integrity attestation are rejected; corrections append new artifact rows.
- `PUBG_BACKUP_DATA_DIR` is a third source-disjoint local root. The localhost-only builder requires exact latest-plan
  confirmation text, exports whitelisted candidate rows as typed JSONL, copies only verified player-owned replay files,
  calculates archive/internal SHA-256 values, and atomically publishes a manifest-bound build directory. One evidence
  row per required generated artifact is appended in a single transaction; capacity and integrity are not claimed.
- The localhost artifact verifier accepts only a build whose path, ID, manifest SHA-256, metadata, actor, KST time, and
  confirmation hash match an intact builder-generated artifact-evidence set. It rejects unsafe, duplicate, encrypted,
  oversized, or undeclared entries and streams every declared JSONL/replay entry to verify CRC, hashes, counts, bytes,
  JSON structure, and typed wrappers. Its immutable result is not a restore attestation.
- The isolated restore endpoint requires a passed verification and exact confirmation text containing the full
  verification-result fingerprint. It revalidates the backup, uses a dedicated MySQL connection with random temporary
  table names, verifies that `CREATE TEMPORARY TABLE ... LIKE` copied no foreign keys, restores every typed row, and
  compares normalized source/readback row-set fingerprints. It writes no production table.
- Replay files are restored only below a random direct child of the backup root after a free-space check. Each file is
  hashed while copying and after readback. Temporary tables are explicitly dropped, the dedicated connection is closed,
  and the scratch directory is removed on both success and failure; cleanup failure blocks the run.
- A passed isolated restore atomically appends its audit row and `backup_integrity_verification` evidence bound to the
  build ID, manifest SHA-256, verification ID/result fingerprint, artifact-evidence-set fingerprint, and restore-result
  fingerprint. Manual integrity evidence is rejected. New artifact evidence makes an older restore attestation stale.
- `PUBG_QUARANTINE_DATA_DIR` is a fourth configurable root. It must already exist, be absolute, non-symlink,
  source-disjoint, and pairwise non-overlapping with raw, replay, and backup roots. The planner never creates it.
- The read-only quarantine planner requires exact confirmation bound to the latest plan. It verifies every source's
  identity, declared size, and SHA-256; rejects existing deterministic destinations; checks free space with a
  `max(64 MiB, 5%)` reserve; and records future postconditions, rollback steps, and crash-recovery journal contracts.
- A passed planner run appends `quarantine_capacity_check` evidence and its immutable audit row atomically. A blocked
  run appends only its audit row. Neither outcome creates directories or journals, copies or moves bytes, removes
  source files, restores data, or mutates deletion targets.
- Schema version 17 requires exact confirmation bound to the latest passed planning run. It derives small deterministic
  fixture bytes from planning metadata, not production replay content, and creates them only below a random owned
  direct child of the configured quarantine root.
- The isolated rehearsal executes the planned copy/verify/remove ordering against synthetic sources, checks committed
  postconditions, reverses every item without overwrite, and verifies the original fixture-tree fingerprint. Separate
  cases recover `copying`, `copied_and_verified`, `source_removal_committing`, and `committed` states; corrupt ambiguous
  bytes must be blocked without mutation.
- Journal updates use fsynced temporary files and durable replacement: write-through `MoveFileExW` on Windows and
  atomic replace plus parent-directory `fsync` on POSIX. Recursive cleanup is allowed only after the random name,
  exact parent, resolved path, and non-symlink ownership contract are rechecked. Cleanup failure makes the audit result
  blocked. The rehearsal adds no evidence and never enables execution.
- The earlier non-executing rehearsal still rechecks live deletion impact, evidence times, artifact metadata, bound
  planner-generated capacity evidence, and current quarantine free space without changing targets.
  `executor_not_implemented` remains even after every available rehearsal passes; production restore, actual
  quarantine moves, and deletion are absent.

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
