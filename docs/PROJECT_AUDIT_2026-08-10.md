# Full Project Audit - 2026-08-10 KST

## Outcome

The repository, local MySQL runtime, configured raw/replay stores, localhost manager, Discord process, and one live
official PUBG telemetry download were reviewed end to end. Reproduced defects were fixed with regression tests. The
production database was migrated and the complete retained corpus was reprocessed without modifying immutable raw
payloads.

Final evidence:

- Python 3.14.0; `485 passed`; one external Starlette TestClient deprecation warning.
- MySQL 8.0.41, database `pubg_ai`, schema version 23, 48 tables, session time zone `+09:00`.
- One active Steam tracking target and 244 retained matches.
- Combat, item, and movement state: 244 rows each; immediate reruns selected zero candidates.
- Combat reconciliation: 244/244 matches, 262/262 kills, damage 35,083.99/35,083.99, zero per-match mismatches.
- Assist reconciliation: 80/80 with zero per-match mismatches. Event deaths are 271 versus 237 final non-alive
  participant rows, preserving repeat deaths after revival instead of collapsing them to final match state.
- Raw catalog: 488/488 files passed confinement, existence, size, and SHA-256 checks.
- Replay catalog: 488/488 files passed confinement, existence, size, and SHA-256 checks.
- The one extra raw file is the manually supplied 59,632,311-byte source sample and was intentionally retained.
- Local manager: `127.0.0.1:8018` returned 200; bad Host returned 400; cross-site POST returned 403.
- Security headers: `no-store`, `nosniff`, `DENY`, and `no-referrer`.
- Discord restarted successfully and connected to the Gateway; no message was sent during this audit.
- One live official-CDN stream returned 713,225 JSON bytes from `telemetry-cdn.pubg.com`.

## Fixed Findings

### High

1. Match-level parser skipping lost late-registered players and repeated zero-output matches forever. Schema v23 now
   records completion per match, account, processor, and parser version, including output count zero.
2. Parser delete/insert sequences could leave partial normalized data. Combat, item, movement, loadout, and fight
   replacements now use MySQL transactions with rollback.
3. Overall combat facts omitted non-gun kills and damage, then included self/friendly damage when broadened. combat-v3
   records hostile dealt facts while retaining all taken damage/deaths. The complete corpus exactly reconciles to PUBG.
4. Local settings and Discord grants could lose concurrent updates. Adjacent cross-process locking and atomic
   read-modify-write mutations now serialize updates.
5. Telemetry automatic redirects could request an unvalidated destination. Redirects are manual, bounded, and each
   official HTTPS CDN target is validated before the next request.

### Medium

1. The localhost manager accepted arbitrary Host values and cross-site browser mutations. Trusted hosts, origin/fetch
   checks, no-store/security headers, and dynamic HTML escaping were added.
2. API job prechecks were race-prone. The target tuple is unique and inserts use duplicate-safe upserts.
3. Worker cycles could report success while sub-processors returned failed counts. Those counters now promote the cycle
   to failed so history and Discord alerts receive the failure.
4. Forced replay generation could overwrite a cataloged file before a failed DB upsert. Map JPEG and timeline JSON
   filenames now include the exact content SHA-256, preserving the old catalog target.
5. Invalid numeric environment values silently fell back to defaults. Invalid or out-of-range values now fail fast.
6. MySQL KST behavior depended on the host's `SYSTEM` time zone. Every project connection now sets `+09:00` explicitly.

### Storage And Transport

1. JSON and map-cache writes use atomic replacement with temporary-file cleanup.
2. Raw/replay verification hashes files in chunks instead of loading them fully into memory.
3. Telemetry accepts only the official HTTPS CDN host and default TLS port.
4. Live response bodies are streamed with a 256 MiB decoded-byte ceiling; gzip expansion is bounded to the same limit.
5. Local settings locks are ignored by Git; `.env` remains ignored and untracked.

## Remaining Risks

1. Rotate the PUBG API key that previously appeared in conversation history, then update only `.env`. The repository
   does not contain the value, but chat exposure is enough to require rotation.
2. Telemetry parsers still materialize one JSON file at a time with `json.load`. Current files completed reliably, but
   a future unusually large match can use several times its file size in RAM. An incremental-event parser is the next
   scalability improvement.
3. The machine-wide Python environment has an unrelated `opencv-python`/NumPy version conflict. This project declares
   neither package and passed fully; use an isolated virtual environment rather than changing the shared installation.
4. FastAPI's installed TestClient emits one Starlette deprecation warning. It does not affect runtime behavior, but the
   dependency set should be refreshed and retested when the replacement adapter is stable.
5. Destructive data-deletion execution remains intentionally unavailable. Existing review, backup, and rehearsal
   tooling does not authorize or perform deletion.

## Reproduction

```powershell
python -m pip install -e ".[dev]"
python -m pubg_ai.cli init-db
python -m pytest -p no:cacheprovider
python -m compileall -q src tests
git diff --check
python -m pubg_ai.cli db-status
```

The local manager is available at `http://127.0.0.1:8018` after:

```powershell
python -m pubg_ai.cli run-web --host 127.0.0.1 --port 8018
```
