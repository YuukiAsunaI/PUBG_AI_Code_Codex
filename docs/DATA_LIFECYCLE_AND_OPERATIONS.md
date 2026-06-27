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
rules later. Raw official match and telemetry files are the priority.

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
- regenerate replay artifacts
- change storage paths
- change polling settings
- change Discord permission grants

