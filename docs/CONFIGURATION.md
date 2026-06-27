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

When the real storage drive is ready, change only `.env`; no code or database migration should be required.

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

## Future UI Setting

The local management program should expose a storage settings page:

- current `PUBG_RAW_DATA_DIR`
- write permission status
- free and used disk space
- recent raw-data growth rate
- button to test write access
- warning if the path is on the OS/project drive

The UI can edit a local config file later, but the environment variable should remain the first supported method.

