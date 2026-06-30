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

The local management program must be able to change both storage roots from a settings screen. The app should write
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
      "admin": ["유저삭제", "pubg-permission", "pubg-unregister", "pubg-delete-data"]
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
`pubg-alert-note`, `pubg-alert-resolution`, and `pubg-alert-notes` for alert report lookup, suppression, annotation,
and note review from Discord.

1. Built-in defaults: `./data/raw`, `./data/replays`
2. `.env` values: `PUBG_RAW_DATA_DIR`, `PUBG_REPLAY_DATA_DIR`
3. Local program values in `PUBG_LOCAL_SETTINGS_FILE`

This means `.env` is still useful for first boot, but the local program can override the paths after the user changes
them in the UI.

The current settings screen provides:

- match/telemetry raw-data path editor
- 2D replay artifact path editor
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
- persisted storage/worker alert history with source/status filters, title/message search,
  newest/oldest/severity-first sorting, previous/next pagination, CSV export, state/severity badges,
  note/resolution comment actions, and a per-alert detail panel with acknowledge/snooze controls and inline note entry
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

Discord recommendation responses can include local web evidence links for weapon+attachment recommendations. Configure
this only when Discord readers can reach the local management app URL:

```env
PUBG_LOCAL_WEB_BASE_URL=http://127.0.0.1:8000
```

The local management app can also save this value under `config/local_settings.json` from the `Local Web Link` section.
That local setting overrides `.env`, and saving an empty value disables evidence links without storing a secret.

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
