# Operational Recovery And Drills

Last verified: 2026-08-09 KST

## Purpose

The collector must recover from temporary PUBG API failures, process interruption, and storage pressure without
duplicating match work or losing retained raw data. The operational drill runner exercises those contracts in a
bounded form and stores a secret-free report in MySQL.

Official reference: [PUBG API rate limits](https://documentation.pubg.com/en/rate-limits.html). The default API key
limit is 10 requests per minute. A `429` response includes rate-limit headers, while match and telemetry endpoints are
documented as exempt from that limit.

## Retry Contract

The first-party PUBG client uses at most three attempts for transport errors and HTTP `408`, `425`, `429`, `500`,
`502`, `503`, and `504` responses. Delay selection is bounded and ordered as follows:

1. `X-RateLimit-Reset`, interpreted as a Unix timestamp, plus a 250 ms buffer.
2. `Retry-After` when present and valid.
3. Exponential delay from one second.

A single delay is capped at 65 seconds and the request's total sleep budget is capped at 70 seconds. Final failures
retain typed status, retryability, delay, and attempt metadata without including the API key.

Match and telemetry jobs use a second durable retry boundary in MySQL:

- Only typed transient failures return to `queued`; this includes short match/CDN `404` propagation windows.
- A job receives at most five processing attempts.
- Queue delays are 15, 30, 60, and 120 seconds, capped at 300 seconds.
- Permanent validation, checksum, storage-contract, or missing-reference failures become terminal `failed` jobs.
- A `running` match or telemetry job older than 15 minutes is treated as interrupted work, returned to `queued`, and
  has the abandoned attempt restored before processing resumes.

Under the supported single-collector deployment, discovery reuses existing jobs by `job_type`, shard, and target match
ID. The live drill also checks for duplicate groups after each bounded cycle. Worker stop waits are interruptible, so a
configured multi-minute poll interval does not delay an operator stop request.

## Drill Modes

`simulated` is the default, makes no PUBG requests, and does not modify MySQL collection tables. By default it stores a
secret-free drill report in `operational_drill_runs`; pass `--no-record` to avoid even that write. It checks:

- controlled `429` recovery using the production retry client and reset header;
- synthetic low-space classification with both `local_program` and `discord` notification targets;
- two real collector-controller start/stop cycles with injected in-memory services;
- two to five idempotent collector cycles with one logical match and telemetry job.

`live` adds two bounded checks:

- It inserts one stale match job and one stale telemetry job inside a MySQL transaction, invokes the production
  recovery methods, verifies both rows, rolls the transaction back, and confirms that zero drill rows remain.
- It selects one active shard, limits player lookup to one official 10-player batch per cycle, runs two to five real
  collector cycles, and verifies zero cycle errors, duplicate job groups, or stranded `running` jobs.

The live mode performs ordinary completed-match collection and can therefore add legitimate match and telemetry jobs.
It never deletes retained data. Stop the automatic collector before starting it so two collectors cannot compete for
the same queue.

## Commands And Local UI

```powershell
python -m pubg_ai.cli run-operational-drills --mode simulated --cycles 3
python -m pubg_ai.cli run-operational-drills --mode live --cycles 2
python -m pubg_ai.cli operational-drill-history --limit 20
```

Use `--no-record` for an unpersisted local check. The localhost manager exposes the same controls and history under
`#operational-drills`. Its API routes are:

- `POST /operations/drills`
- `GET /operations/drills?limit=20`

Schema version 22 adds `operational_drill_runs`. Reports use `operational-drill-v1` and store only mode, status, KST
times, bounded counts, summaries, and safe metrics. API keys, Discord tokens, nicknames, account IDs, and temporary
drill target IDs are not stored in the report.

## Verified Local Evidence

On 2026-08-09 KST:

- MySQL `pubg_ai` was migrated to schema version 22 with 47 tables.
- Simulated run 1 passed all four checks.
- Live run 3 passed all five checks.
- The live stale-recovery transaction recovered one match and one telemetry job, rolled back, and left zero rows.
- The final live soak ran two Steam cycles with a 10-player cap and ended with zero queued, running, or failed jobs,
  zero duplicate groups, and 244 succeeded match plus 244 succeeded telemetry jobs.
- The operational-drill validation suite passed 447 tests at that checkpoint; the later full-project audit is
  recorded in `PROJECT_AUDIT_2026-08-10.md`.
- Chrome checks at desktop and 390 px mobile widths found no console error, page error, failed request, or document
  overflow; the UI executed and persisted an additional simulated drill.
- Raw and replay audits each checked 488 metadata rows with zero missing files, size mismatches, or paths outside the
  configured roots.

The guarded `discord-acceptance-probe`, `discord-acceptance-send-alert`, and `discord-acceptance-observe` CLI commands
make that final run repeatable. Probe is read-only; send-alert requires exact explicit confirmation and verifies the
created message by reading it back; observe pairs a human command with the bot's referenced reply without exposing
message bodies. On 2026-08-10 KST, the production probe authenticated `SCA_Bot`, found 16 guild memberships, and
verified view/send/history permissions for the selected channel. Controlled test `16f7b14b2591` delivered message
`1536044033988628610` and read it back successfully.

A human author then completed referenced reply round trips for `배그도움말`, `유저등록`, `유저조회`, `유저삭제`,
and restored `유저등록`; all bot responses arrived within two seconds. Soft unregister changed the tracked player to
inactive while 244 matches and aggregates remained, and restore returned the same account/scope to active. The
post-stop and post-restore storage measurements were identical: 489 raw files / 484,437,073 bytes and 497 replay
files / 136,635,256 bytes. The live run also drove a least-privilege correction: soft unregister now uses the
dedicated `player_manage` group instead of broad `admin`. The author never received `admin`, and all temporary grants
were revoked after the lifecycle. This closes the external Discord operations acceptance gap without exposing message
bodies, API keys, or the bot token.
