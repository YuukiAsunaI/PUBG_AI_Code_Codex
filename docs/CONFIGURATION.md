# Configuration

Research date: 2026-06-27

## Raw Match Data Storage

Match and telemetry JSON can become very large, so the storage path must be configurable from the beginning.
The app should not assume that raw files live inside the project folder or inside MySQL.

Use this environment variable as the main storage root:

```env
PUBG_RAW_DATA_DIR=E:\PUBG_AI_Data\raw
```

For local development without a separate drive, the default can be:

```env
PUBG_RAW_DATA_DIR=./data/raw
```

When the real storage drive is ready, the local management program should save the selected path to
`config/local_settings.json`; no code or database migration should be required.

## Local Program Storage Settings

The local management program must be able to change all four storage roots from a settings screen. The app should write
those choices to this JSON file:

```env
PUBG_LOCAL_SETTINGS_FILE=./config/local_settings.json
```

Recommended JSON shape:

```json
{
  "storage": {
    "raw_data_dir": "E:\\PUBG_AI_Data\\raw",
    "replay_data_dir": "E:\\PUBG_AI_Data\\replays",
    "backup_data_dir": "D:\\BackUP\\deletion-backups",
    "quarantine_data_dir": "E:\\PUBG_Quarantine",
    "raw_compression": "gzip",
    "updated_at": "2026-06-27T21:00:00+09:00"
  },
  "collector": {
    "poll_interval_seconds": 180,
    "cycle_player_limit": 100,
    "player_lookup_chunk_size": 10,
    "updated_at": "2026-06-27T21:00:00+09:00"
  },
  "discord_permissions": {
    "command_groups": {
      "register": ["유저등록", "pubg-register"],
      "profile_read": ["유저조회", "전적", "무기", "매치", "pubg-profile", "pubg-stats", "pubg-recent", "pubg-match", "pubg-weapon"],
      "ranking_read": ["랭킹", "pubg-ranking"],
      "replay_read": ["pubg-replay"],
      "settings_write": ["pubg-settings"],
      "admin": ["유저삭제", "pubg-permission", "pubg-ranking-scope", "pubg-guild-scope", "pubg-unregister", "pubg-delete-data", "pubg-delete-cancel"]
    },
    "user_grants": {
      "discord-user-id": ["profile_read", "ranking_read"]
    },
    "guild_user_grants": {
      "guild-id": {
        "discord-user-id": ["register", "profile_read"]
      }
    },
    "global_admin_user_ids": ["discord-admin-user-id"],
    "updated_at": "2026-06-27T21:00:00+09:00"
  },
  "discord_scopes": {
    "guild_ranking_scopes": {
      "guild-id": "guild"
    },
    "public_profile_default": true,
    "updated_at": "2026-06-27T21:00:00+09:00"
  },
  "alerts": {
    "minimum_free_bytes": 53687091200,
    "discord_channel_ids": ["discord-alert-channel-id"],
    "storage_alerts_enabled": true,
    "worker_error_alerts_enabled": true,
    "updated_at": "2026-06-27T21:00:00+09:00"
  }
}
```

Runtime priority:

The current built-in `admin` command group also includes `pubg-alerts`, `pubg-alert-ack`, `pubg-alert-snooze`,
`pubg-alert-note`, `pubg-alert-resolution`, `pubg-alert-notes`, `pubg-alert-history`, `pubg-worker-runs`, and
`pubg-worker-run`, plus `pubg-permission` and `pubg-ranking-scope`, for alert report lookup, suppression, annotation,
note review, filtered alert history review, worker-run inspection, permission grants, and guild ranking-scope changes
from Discord.

The `settings_write` group enables `pubg-settings`. Guild-scoped grants may read only a restricted summary. Global
admins and globally granted `settings_write` users may change collector limits and the default `public_profile` value.
Discord never returns API keys, bot tokens, database settings, or storage paths, and it never changes raw compression;
those remain `.env` or localhost-manager concerns.

`pubg-delete-data` creates an expiring review request only. The localhost manager records approve/reject/cancel
decisions and immutable audit events. Its detail view generates a read-only impact preview from MySQL plus the current
`PUBG_RAW_DATA_DIR` and `PUBG_REPLAY_DATA_DIR` settings. The per-storage file display is limited to 100 by default and
500 at most, while database totals and declared byte totals cover the complete candidate set. Paths outside the
configured roots, missing files, and size mismatches are reported without reading file contents.

Schema version 11 stores immutable maximum-500-file preview snapshots and SHA-256 manifests. Local confirmation
requires an `approved` request, the latest snapshot, a complete catalog, zero filesystem issues, at least one
player-owned candidate, a fresh matching fingerprint, and exact text in the form
`CONFIRM DELETE REQUEST <request_id> <full_fingerprint>`. Confirmation rows store only the text hash and audit
metadata. Schema version 12 adds `data_deletion_dry_run_plans`; generation requires the confirmed latest fingerprint
to match another maximum-500-file live preview. The immutable JSON plan contains non-executable row/file descriptors,
shared-data exclusions, backup prerequisites, postcondition checks, and a canonical plan SHA-256. It always reports
`execution_enabled=false`, `execution_ready=false`, `executor_not_implemented`, and
`backup_evidence_not_recorded`. Schema version 13 adds `data_deletion_backup_evidence` and
`data_deletion_rehearsal_runs`. Evidence paths must be absolute local paths; this first rehearsal checks artifact
metadata and current capacity without opening backup bytes or performing a restore. Corrected evidence appends a new
immutable row and makes an older passed rehearsal stale. Schema version 14 adds `data_deletion_backup_verification_runs`; each result is bound to the
builder-generated artifact-evidence IDs and evidence-set fingerprint. Schema version 15 adds
`data_deletion_backup_restore_rehearsal_runs`; passed rows reference the selected verification and the automatically
created integrity-evidence row. Schema version 16 adds `data_deletion_quarantine_planning_runs`. Its read-only planner
requires exact confirmation, revalidates source bytes and deterministic target absence, and checks the configured
quarantine root with a capacity reserve. A passed run creates planner-bound capacity evidence; manual capacity or
integrity evidence is rejected. Run `python -m pubg_ai.cli init-db` after updating so the ten deletion workflow tables
are created. No deletion executor or execution endpoint is enabled.

The opt-in builder uses `PUBG_BACKUP_DATA_DIR` (default `./data/backups`). The backup root must be writable and must not
equal, contain, or be contained by `PUBG_RAW_DATA_DIR` or `PUBG_REPLAY_DATA_DIR`. A build requires the exact latest-plan
confirmation text. For each prerequisite required by the latest plan, MySQL candidate rows are exported as typed JSONL
entries in `mysql-target-backup.zip`, and verified player-owned replay bytes are copied to
`replay-artifact-backup.zip`. A `build-manifest.json` binds every generated artifact to
the request, plan, source fingerprint, actor, KST build time, and confirmation-text hash. Whole-file and internal
checksums are calculated while building. The localhost review screen can run a read-only verifier through
`POST /data-deletions/{request_id}/backup-verifications`. It requires the selected manifest SHA-256 and an intact
builder-generated artifact-evidence set, rejects build-directory extras and unsafe/duplicate/encrypted/undeclared ZIP
entries, and streams all declared JSONL/replay entries to verify counts, byte sizes, typed wrappers, CRC, and SHA-256 values. Build manifests
are limited to 4 MiB, internal manifests to 8 MiB, and a JSONL row to 64 MiB; archive expansion limits also guard against
ZIP bombs. The format contains no schema creation SQL. The
`POST /data-deletions/{request_id}/backup-restore-rehearsals` endpoint requires a passed verification and exact
full-fingerprint confirmation text. It opens a second MySQL connection, creates random `TEMPORARY TABLE ... LIKE` copies, rejects copied
foreign keys or live-schema column drift, inserts typed rows only into those temporary names, and compares normalized
row-set fingerprints after readback. Replay bytes are restored and rehashed in a random temporary directory directly
under `PUBG_BACKUP_DATA_DIR`; free space is checked first and cleanup is mandatory. Only a passed run creates bound
`backup_integrity_verification` evidence.

The read-only quarantine planner uses `PUBG_QUARANTINE_DATA_DIR` (default `./data/quarantine`). The directory must
already exist, be absolute, not be a symlink or filesystem root, and be pairwise disjoint from raw, replay, and backup
roots. `POST /data-deletions/{request_id}/quarantine-plans` requires the exact fingerprint-bound confirmation beginning
with `RUN READ-ONLY QUARANTINE PLAN`. It checks source identity/size/SHA-256, deterministic target absence, capacity
with a `max(64 MiB, 5%)` reserve, and future rollback/crash-recovery contracts. It never creates the root or journal and
never copies, moves, deletes, restores, or changes source rows/files. Production restore, actual quarantine moves,
and deletion remain unavailable.

1. Built-in defaults: `./data/raw`, `./data/replays`, `./data/backups`, `./data/quarantine`
2. `.env` values: `PUBG_RAW_DATA_DIR`, `PUBG_REPLAY_DATA_DIR`, `PUBG_BACKUP_DATA_DIR`,
   `PUBG_QUARANTINE_DATA_DIR`
3. Local program values in `PUBG_LOCAL_SETTINGS_FILE`

This means `.env` is still useful for first boot, but the local program can override the paths after the user changes
them in the UI.

The current settings screen provides:

- match/telemetry raw-data path editor
- 2D replay artifact path editor
- opt-in deletion backup path editor
- read-only deletion quarantine planning path editor
- raw compression selector
- polling interval selector from 1 to 5 minutes
- collection cycle player limit selector up to 100
- player lookup chunk size selector up to the official player lookup limit
- in-process automatic collector start/stop/status controls
- in-process automatic post-processing start/stop/status controls
- Discord command group editor
- Discord per-user permission grant editor
- global admin editor
- Discord guild-specific ranking scope editor
- public profile default editor
- alert settings for minimum free storage and Discord alert channel IDs
- current storage/worker alert table with acknowledge and one-hour snooze actions
- persisted storage/worker alert history with source/status/severity filters, title/message search,
  newest/oldest/severity-first sorting, current-error/worker/storage quick presets, previous/next pagination,
  CSV export, state/severity badges, note/resolution comment actions, and a per-alert detail panel with
  acknowledge/snooze controls and inline note entry
- Discord admin alert note/resolution commands, controlled by the same `admin` permission group
- free disk space display for each path
- warning when a configured drive is disconnected

Discord permission settings can also be changed from the CLI:

```powershell
python -m pubg_ai.cli add-discord-global-admin <discord_user_id>
python -m pubg_ai.cli grant-discord-permission <discord_user_id> register --guild-id <guild_id>
python -m pubg_ai.cli revoke-discord-permission <discord_user_id> register --guild-id <guild_id>
python -m pubg_ai.cli discord-permissions
```

These commands update `config/local_settings.json` only. API keys and bot tokens still remain in `.env`.

## Local Web Link Sharing

Discord recommendation responses can include local web evidence links for weapon+attachment recommendations, and
Discord alert-history responses can include local alert-detail links. Configure this only when Discord readers can
reach the local management app URL:

```env
PUBG_LOCAL_WEB_BASE_URL=http://127.0.0.1:8000
```

The local management app can also save this value under `config/local_settings.json` from the `Local Web Link` section.
That local setting overrides `.env`, and saving an empty value disables evidence/detail links without storing a secret.

Leave `PUBG_LOCAL_WEB_BASE_URL` unset when the bot is running on a private machine and Discord readers cannot open the
local app. This setting is not a secret, but it should not be confused with remote access; the web app still refuses
non-localhost bind hosts by default.

## Recommended File Layout

Store files under deterministic relative paths:

```text
{PUBG_RAW_DATA_DIR}/
  matches/
    {shard}/
      {yyyy}/
        {mm}/
          {dd}/
            {match_id}.json.gz
  telemetry/
    {shard}/
      {yyyy}/
        {mm}/
          {dd}/
            {match_id}.telemetry.json.gz
```

Example:

```text
E:\PUBG_AI_Data\raw\matches\steam\2026\06\27\abc123.json.gz
E:\PUBG_AI_Data\raw\telemetry\steam\2026\06\27\abc123.telemetry.json.gz
```

## MySQL Responsibility

MySQL should store metadata and analysis rows, not the full raw text files by default.

Raw payload tables should include:

| Column | Purpose |
| --- | --- |
| `match_id` | PUBG match ID |
| `shard` | Platform shard, for example `steam` or `kakao` |
| `payload_type` | `match` or `telemetry` |
| `storage_backend` | `local_file` for the first implementation |
| `storage_root` | Logical root name, usually `PUBG_RAW_DATA_DIR` |
| `relative_path` | Path below `PUBG_RAW_DATA_DIR` |
| `compression` | `gzip` or `none` |
| `size_bytes` | Stored file size |
| `sha256` | Integrity checksum |
| `source_url` | PUBG API or telemetry CDN URL |
| `fetched_at` | Download timestamp |
| `parser_version` | Parser version used for normalized rows |

This lets the raw data drive move later. Only `PUBG_RAW_DATA_DIR` changes; DB rows remain valid because they store
relative paths.

## Safety Rules

- Require `PUBG_RAW_DATA_DIR` to be an existing writable directory in production mode.
- Do not silently fall back to the project folder when the external drive is disconnected.
- Write to a temporary file first, then atomically rename to the final file name.
- Calculate `sha256` after writing and store it in MySQL.
- Keep `gzip` enabled by default for telemetry.
- Show free disk space and write-test status in the local management UI.
- If the drive is missing, mark fetch jobs as `storage_unavailable` and retry later.
- Keep `.env` out of git; commit only `.env.example`.

## UI Setting Behavior

The local management program should call the settings service to validate and save paths. It should never write raw
files to a fallback folder without making that visible to the user.

Secrets are different from local settings:

- `PUBG_API_KEY` remains only in `.env`.
- `DISCORD_BOT_TOKEN` remains only in `.env`.
- The local management program should show only masked configured/missing status.
- `config/local_settings.json` must never contain raw tokens or API keys.

## 2D Replay Storage

2D replay files should also use a configurable root. They are generated artifacts, not official raw data, so keep
them separate from `PUBG_RAW_DATA_DIR`.

Use:

```env
PUBG_REPLAY_DATA_DIR=E:\PUBG_AI_Data\replays
```

Local development default:

```env
PUBG_REPLAY_DATA_DIR=./data/replays
```

Recommended layout:

```text
{PUBG_REPLAY_DATA_DIR}/
  timeline/
    {shard}/{yyyy}/{mm}/{dd}/{match_id}/timeline.json
  snapshot/
    {shard}/{yyyy}/{mm}/{dd}/{match_id}/frame-000120.json
  thumbnail/
    {shard}/{yyyy}/{mm}/{dd}/{match_id}/summary.png
  gif/
    {shard}/{yyyy}/{mm}/{dd}/{match_id}/highlight.gif
  video/
    {shard}/{yyyy}/{mm}/{dd}/{match_id}/replay.mp4
  cache/
    {shard}/{yyyy}/{mm}/{dd}/{match_id}/...
```

MySQL should store replay artifact metadata:

| Column | Purpose |
| --- | --- |
| `match_id` | PUBG match ID |
| `shard` | Platform shard |
| `artifact_type` | `timeline`, `snapshot`, `thumbnail`, `gif`, `video`, or `cache` |
| `storage_backend` | `local_file` for the first implementation |
| `storage_root` | `PUBG_REPLAY_DATA_DIR` |
| `relative_path` | Path below `PUBG_REPLAY_DATA_DIR` |
| `content_type` | MIME type such as `application/json`, `image/png`, or `video/mp4` |
| `size_bytes` | Stored file size |
| `sha256` | Integrity checksum |
| `generated_at` | Render timestamp |
| `renderer_version` | Version of the replay renderer |

Safety rules:

- Do not mix replay files into the raw telemetry directory.
- If the replay drive is disconnected, mark replay generation as `storage_unavailable`.
- Store relative paths so the replay drive can be moved later.
- Keep generated replay artifacts out of git.
- The management UI should show both raw-data storage and replay storage health.
