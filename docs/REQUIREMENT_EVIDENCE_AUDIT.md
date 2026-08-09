# Requirement-to-Evidence Audit

Last updated: 2026-08-10 KST

## Purpose

This document maps the original product requirements to implementation and validation evidence. It is the source of
truth for remaining work. A feature is not marked `PROVEN` from source inspection alone.

Status meanings:

- `PROVEN`: implemented, covered by automated tests, and exercised against the applicable local or external system.
- `IMPLEMENTED`: implemented and covered by automated tests, but the final external-system path has not been exercised.
- `PARTIAL`: useful behavior exists, but part of the requested contract or evidence is missing.
- `PENDING`: no production implementation yet.

The weighted completion estimate for the requested core product is **100 percent**. Collection, storage,
telemetry parsing, class-aware weapon hit metrics, durable fight-outcome and KST trend analytics, named drop-region
resolution, post-match replay, recommendations, local administration, bounded operational recovery, and the selected
Discord-channel lifecycle are working. Destructive deletion execution remains a separately gated, non-core scope
until the administrator explicitly approves that irreversible capability.

## Live Evidence Snapshot

The following checks were run through 2026-08-10 KST without printing either secret:

- Repository baseline before the Discord live-acceptance slice: `14c82f2`.
- Automated suite: `447 passed`, with one existing Starlette deprecation warning.
- Python compile, `git diff --check`, and secret-pattern scan: passed. The actual `.env` is ignored and untracked;
  only `.env.example` is tracked, and no JWT-like or Discord-token-like value was found outside `.env`.
- MySQL: local `pubg_ai`, MySQL 8.0.41, schema version 22, 47 tables.
- Registered target: Steam player `Yuuki_Asuna---`; collection continues by the stored PUBG `accountId`.
- Operational drill evidence: simulated run 1 passed 4/4; live run 3 passed 5/5. The live transaction recovered one
  stale match and one stale telemetry job, rolled back, and left zero drill rows. Two bounded Steam cycles ended with
  zero queued/running/failed jobs and zero duplicate groups.
- Latest PUBG collection: 37 newly retained matches and telemetry payloads were completed with zero terminal failure.
- Current queue: 244 succeeded match jobs and 244 succeeded telemetry jobs; no other queue state remains.
- Current retained corpus: 244 matches, 21,911 participants, 9 player snapshots, 244 match payloads, and 244 telemetry
  payloads.
- Current parsed corpus: 244 combat summaries, 826 weapon rows, 36,167 item events, 7,202 item-stat rows, 16,841
  position samples, 1,644 combat locations, 1,059 loadout snapshots, 935 durable fight outcomes, 244 fight-processing
  states, 9,076 care-package events, 211 plane routes, and 36,045 phase events.
- Weapon hit-metric calibration: 244/244 telemetry payloads parsed with zero failures; 12,650 supported
  single-projectile attacks and 1,763 hit events yielded a 13.9368% estimate, while 68 shotgun shells and 213
  pellet hit events yielded 3.13235 pellet hits per shell. Twenty-three unclassified attacks remain visible and
  excluded from both metrics.
- Artifact corpus: 244 JPEG map snapshots and 244 timeline JSON files.
- Raw storage audit: 488 metadata rows, zero missing files, zero size mismatches, and zero paths outside the configured
  root.
- Discord stop/restore retention check: the target remained at 244 matches before stop, while inactive, and after
  restore. The post-stop and post-restore filesystem measurements were identical at 489 raw files / 484,437,073 bytes
  and 497 replay files / 136,635,256 bytes.
- Replay storage audit: 488 metadata rows, zero missing files, zero size mismatches, and zero paths outside the
  configured root.
- Final post-processing cycle: every parser and artifact generator reported zero remaining candidates and zero errors.
- Local operational UI: desktop and 390 px mobile Chrome checks executed a simulated drill and loaded persisted live
  detail with no document overflow, console errors, page errors, failed requests, or error overlay. The mobile history
  table uses a stable horizontal scroll layout.
- Earlier 2026-08-02 acceptance evidence remains valid for KST trend partitions, recommendation output, official-asset
  named regions, and nonblank 2D replay playback; all associated regression tests remain green.
- Discord live acceptance used guild `1077634255817027675` and channel `1077634256773320876`. The guarded production
  probe authenticated `SCA_Bot`, found 16 guild memberships, and verified view/send/history permissions. Controlled
  alert test `16f7b14b2591` created message `1536044033988628610` and read it back with `verified=true`.
- A human `!배그도움말` command (`1536046036299022356`) received referenced bot reply
  `1536046037658116118`. The same author then completed register (`1536046864082665514`), query
  (`1536047518293565450`), soft unregister (`1536054211408568500`), and restore registration
  (`1536055314011398335`); every referenced reply was observed and all response times were under two seconds.
- The first register bound the tracking target to the command author, guild, and channel. Soft unregister changed only
  `active` to false; 244 matches and all displayed aggregate values remained. Re-registration restored `active=true`
  with the same account, scope, 244 matches, and aggregate values.
- The live run discovered that soft unregister shared the broad `admin` group. It now uses dedicated
  `player_manage`; legacy `admin` grants imply it for compatibility, while delegates do not receive `admin`.
  Production settings confirmed no `admin` grant during the test. Temporary `register`, `profile_read`, and
  `player_manage` grants were all revoked afterward, leaving zero user grants and zero global admins.

Configured local roots:

- Raw API payloads: `D:\BackUP\raw`
- Replay artifacts: `D:\BackUP\replay`

## Configuration And Security

| ID | Requirement | Status | Evidence and remaining note |
| --- | --- | --- | --- |
| CFG-01 | Run MySQL and the application locally | PROVEN | Local MySQL was queried directly; the manager binds to `127.0.0.1`. |
| CFG-02 | Keep the PUBG key and Discord token only in `.env` | PROVEN | `.env` is ignored and untracked; configuration status reports only presence and length. |
| CFG-03 | Configure raw and replay roots from the local program | PROVEN | Settings UI/API and `tests/test_web_settings.py`; live roots are listed above. |
| CFG-04 | Configure polling interval, cycle target limit, and lookup chunk size | PROVEN | Live settings are 180 seconds, 100 targets per cycle, and 10 names per lookup chunk. |
| CFG-05 | Keep data indefinitely | PROVEN | Immutable raw and replay files are retained; no automatic expiry job exists. |
| CFG-06 | Alert locally and in Discord when storage is low or unavailable | PROVEN | The low-space drill produced both targets, and controlled alert `16f7b14b2591` was delivered to and read back from the selected production Discord channel. |
| CFG-07 | Never expose the local manager to other computers by default | PROVEN | Runtime and tests require loopback binding; the active server is `http://127.0.0.1:8018`. |

## Player And Match Collection

| ID | Requirement | Status | Evidence and remaining note |
| --- | --- | --- | --- |
| COL-01 | Register nickname together with platform | PROVEN | `player_registry.py`, `pubg_client.py`, tests, and the live Steam lookup. |
| COL-02 | Resolve nickname once and collect later by `accountId` | PROVEN | Registry persists the resolved identifier; the live collection used the stored account. |
| COL-03 | Use admin-managed tracking targets, not ownership claims | PROVEN | Registry and permission tests enforce the model; the live Discord registration updated the existing tracking target and command scope without claiming PUBG-account ownership. |
| COL-04 | Collect only registered targets as the primary scope | PROVEN | Collector queried the one active registered target; no unregistered polling path exists. |
| COL-05 | Stop collection on unregister but retain prior data | PROVEN | Live Discord soft unregister set `active=false` while preserving 244 matches and aggregates; restore returned `active=true`, with identical post-stop/post-restore raw and replay measurements. |
| COL-06 | Discover match IDs from player data, then fetch each match | PROVEN | This live slice discovered and processed 37 new match IDs through the stored player account flow. |
| COL-07 | Process completed matches and telemetry after match completion | PROVEN | All 37 new match and telemetry payloads completed; the final queue contains only 244+244 succeeded jobs. |
| COL-08 | Collect every discoverable match for tracked players | PROVEN | Continuous polling stores every new ID exposed by the player endpoint, with idempotent jobs. Historical reach is limited by PUBG's player response window. |
| COL-09 | Classify mode, perspective, match type, map, and shard immediately | PROVEN | `match_classification.py`, tests, and populated match rows. |
| COL-10 | Store timestamps in KST | PROVEN | `time_utils.py`, tests, and live KST match-window inspection. |
| COL-11 | Preserve raw player, match, and telemetry payloads | PROVEN | 9 player snapshots, 244 match payloads, and 244 telemetry payloads are retained. |
| COL-12 | Record total, human, and bot population | PROVEN | `match_population.py`, tests, and participant ingestion. |
| COL-13 | Handle API limits, retries, and idempotency | PROVEN | Production client retries are bounded and honor official reset headers; controlled 429 recovery, live bounded collection, zero duplicate groups, queue retries, and stale recovery passed. |

## Telemetry And Match Facts

| ID | Requirement | Status | Evidence and remaining note |
| --- | --- | --- | --- |
| TEL-01 | Record pickup, drop, use, equip, attach, and detach events | PROVEN | Item processor produced 36,167 retained item events. |
| TEL-02 | Translate item and weapon codes to Korean with raw-code fallback | PROVEN | `code_translator.py` and tests; unknown new codes remained visible verbatim in live output. |
| TEL-03 | Record survival time, kills, damage, assists, rank, movement, and chicken result | PROVEN | Match participants and player combat/movement summaries cover these values. |
| TEL-04 | Record total and per-weapon damage dealt and received | PROVEN | Combat summary and 826 per-weapon rows; live player statistics and recommendations loaded them. |
| TEL-05 | Record shots fired, hits, body-part hits, and headshots per weapon | PROVEN | All 244 telemetry payloads were reprocessed from `LogPlayerAttack`: supported single-projectile estimated hit rate, shotgun pellet hits per shell, unclassified raw counts, body parts, and headshots are retained and exposed without clamping. |
| TEL-06 | Record kills, assists, deaths, DBNO caused, and DBNO taken separately | PROVEN | Parser, summaries, per-weapon rows, and combat tests distinguish every requested direction. |
| TEL-07 | Distinguish DBNO, finish, death, revive, and repeated life-state events | PROVEN | Event-indexed telemetry facts preserve repeated DBNO/death/revive sequences. |
| TEL-08 | Count causing a DBNO as winning a duo or squad fight | PROVEN | Schema-backed outcomes persist duo/squad DBNO caused as wins and DBNO taken as losses; solo DBNO is ignored. The current durable fight ledger contains 935 outcomes. |
| TEL-09 | Preserve winning and losing weapon plus attachment context | PROVEN | Parser v2 prioritizes actual `LogPlayerAttack.weapon.attachedItems`, then reconstructed item state/equip and damage-event fallback. Live backfill stores kill, DBNO-caused, DBNO-taken, and death contexts in the event ledger and shared snapshot table. |
| TEL-10 | Record movement path, landing, kill, DBNO, death, and revive locations | PROVEN | Movement processor populated positions, landing facts, and 1,644 combat-location events. |
| TEL-11 | Record plane route, care-package locations, and phase movement | PROVEN | 211 plane routes, 9,076 care-package events, and 36,045 phase events are retained. |
| TEL-12 | Highlight other registered players in the same match | PROVEN | Timeline data/UI distinguishes tracked, registered, and ordinary teammates; live replay inspection passed. |

## Analytics And Recommendations

| ID | Requirement | Status | Evidence and remaining note |
| --- | --- | --- | --- |
| ANA-01 | Provide KDA and core player totals | PROVEN | `player_stats.py`, web/Discord formatters, tests, and live player query. |
| ANA-02 | Split chicken and non-chicken matches | PROVEN | Match result dimensions and recommendation outputs include match win rates. |
| ANA-03 | Analyze weapon use, kills, damage, deaths, and win rates | PROVEN | Weapon statistics and recommendations are available from real retained data. |
| ANA-04 | Analyze attachments and weapon-plus-attachment combinations | PROVEN | Loadout snapshots and attachment recommendations returned real results. |
| ANA-05 | Use fine close-range AR buckets and 100 m DMR/SR buckets through 1 km | PROVEN | `distance_buckets.py` and tests enforce the requested family-specific ranges. |
| ANA-06 | Recommend weapons, attachments, maps, and teammates | PROVEN | Recommendation service, web/Discord output, tests, and live queries passed. |
| ANA-07 | Analyze common drop locations from coordinates | PROVEN | Coordinate clustering and live drop-zone recommendations work. |
| ANA-08 | Map coordinate clusters to named regions | PROVEN | Versioned official-asset-backed region circles, stable IDs, raw-coordinate fallback, CLI/API/UI/Discord integration, tests, and live coverage validation passed. |
| ANA-09 | Group reports by hour, day, week, and month in KST | PROVEN | `player_trends.py` uses stored KST match times and Python ISO-week rules. CLI/API/UI and live all-unit invariants passed. |
| ANA-10 | Group trends by solo, duo, squad, perspective, match type, map, and shard | PROVEN | One filter contract covers every requested dimension. Earlier live team/perspective partitions summed exactly, and the current 244-match pipeline remains covered by regression tests. |
| ANA-11 | Compute durable fight win/loss rates by weapon and attachments | PROVEN | MySQL service, CLI, localhost API/UI, and Discord formatter expose total, reason, firearm, and exact loadout win/loss rates. Real API and desktop/mobile UI checks passed. |

## Replay And Local Management

| ID | Requirement | Status | Evidence and remaining note |
| --- | --- | --- | --- |
| REP-01 | Generate and retain JPEG match maps | PROVEN | 244 JPEG artifacts exist under the configured replay root. |
| REP-02 | Show plane, parachute/landing, movement, kill, and death locations | PROVEN | Renderer/timeline tests and a real browser replay inspection passed. |
| REP-03 | Generate and retain post-match 2D replay timelines | PROVEN | 244 timeline artifacts exist; playback advanced without browser errors in the prior live replay check. |
| REP-04 | Provide true in-match live tracking | NOT APPLICABLE | The PUBG Open API exposes usable telemetry after match completion. The implemented product is a post-match 2D replay, matching the confirmed collection constraint. |
| WEB-01 | Manage settings, targets, permissions, workers, artifacts, and analytics locally | PROVEN | Local web routes/UI and their test suites cover these workflows. |

## Discord And Operations

| ID | Requirement | Status | Evidence and remaining note |
| --- | --- | --- | --- |
| DSC-01 | Register, query, and unregister a player in the command channel | PROVEN | One human author completed help, register, query, soft unregister, and restore registration in the selected channel; every bot reply was paired by message reference. |
| DSC-02 | Assign command permissions through guild-scoped groups | PROVEN | The live author received only guild-scoped `register`, `profile_read`, and dedicated `player_manage`; no `admin` grant appeared, and all temporary grants were revoked. |
| DSC-03 | Allow a global administrator to inspect all guilds | PROVEN | Multi-guild scope/authorization tests cover global inspection, the production bot authenticated across 16 guilds, and global admins remain local-only managed. |
| DSC-04 | Provide guild rankings and optional public profiles | PROVEN | Ranking/public-profile integration tests pass; the live registration/query exercised guild scope and the configured public profile through the production bot path. |
| DSC-05 | Authenticate the actual configured bot | PROVEN | Guarded REST probing verified the bot account, 16 guild memberships, and selected-channel permissions; the production bot also connected to the Gateway. |
| DSC-06 | Exercise a real command in a selected guild and channel | PROVEN | Controlled alert delivery/read-back and five human-command round trips succeeded in the selected guild/channel without exposing message bodies or secrets. |
| OPS-01 | Run collector and post-processing workers | PROVEN | Both workers completed real one-cycle runs and left zero backlog. |
| OPS-02 | Recover cleanly across long-running operation and restart | PROVEN | Controller stop/restart, bounded simulated and live soak, interruptible waits, and transactional MySQL match/telemetry stale recovery passed; live queue and duplicate counts ended at zero. |
| OPS-03 | Let an administrator decide whether retained player data is deleted | PARTIAL | Request, preview, backup, verification, quarantine, restore rehearsal, and review tooling exist. There is intentionally no destructive executor yet. |

## Separately Gated Roadmap

1. **Destructive deletion execution, only when explicitly approved**
   Keep the existing review and backup gates. Do not add or invoke irreversible deletion as an incidental step.

## Completion Gates

The original core scope can be called complete when:

- One real Discord channel acceptance run succeeds with no secret disclosure, including a controlled alert delivery.
- Raw and replay storage audits remain at zero missing, mismatched, or escaped files after later changes.
- The full automated suite, compile check, diff check, and secret scan continue to pass.

All three core gates passed on 2026-08-10 KST. The selected-channel alert and command lifecycle passed, storage audit
invariants remained clean, and the 447-test suite plus compile/diff/secret checks passed.

The bounded rate-limit, stop/restart, MySQL stale-recovery, and live idempotency gate is complete as of live drill run 3.
The weapon-family shell/projectile/pellet calibration gate is complete as of the 2026-08-10 full-corpus reprocess.
