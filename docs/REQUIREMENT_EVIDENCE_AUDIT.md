# Requirement-to-Evidence Audit

Last updated: 2026-08-02 KST

## Purpose

This document maps the original product requirements to implementation and validation evidence. It is the source of
truth for remaining work. A feature is not marked `PROVEN` from source inspection alone.

Status meanings:

- `PROVEN`: implemented, covered by automated tests, and exercised against the applicable local or external system.
- `IMPLEMENTED`: implemented and covered by automated tests, but the final external-system path has not been exercised.
- `PARTIAL`: useful behavior exists, but part of the requested contract or evidence is missing.
- `PENDING`: no production implementation yet.

The weighted completion estimate for the requested core product is **88 to 90 percent**. Collection, storage,
telemetry parsing, durable fight-outcome analytics, post-match replay, recommendations, and local administration are
operational. The largest remaining product gaps are KST trend reports and named map regions. The largest remaining
validation gaps are a real Discord command exchange and controlled rate-limit, storage, and worker recovery drills.

## Live Evidence Snapshot

The following checks were run on 2026-08-02 KST without printing either secret:

- Repository baseline before this implementation slice: `f08b25e` (`Add read-only review packet comparison`).
- Automated suite: `372 passed`, with one existing Starlette deprecation warning.
- Python compile and `git diff --check`: passed.
- Secret scan and mutation-route scan: passed.
- MySQL: local `pubg_ai`, schema version 21, 46 tables.
- Registered target: Steam player `Yuuki_Asuna---`; live nickname lookup returned the stored PUBG `accountId`.
- Latest PUBG collection: 61 newly discovered matches, 61 match payloads, and 61 telemetry payloads, with zero failures.
- Current retained corpus: 207 matches and 18,447 participants.
- Current parsed corpus: 207 combat summaries, 679 weapon rows, 28,825 item events, 5,841 item-stat rows,
  13,060 position samples, 1,427 combat locations, 923 loadout snapshots, 815 durable fight outcomes,
  207 fight-processing states, 6,897 care-package events, 174 plane routes, and 29,991 phase events.
- Artifact corpus: 207 JPEG map snapshots and 207 timeline JSON files.
- Raw storage audit: 414 metadata rows, zero missing files, zero size mismatches, and zero paths outside the configured
  root.
- Replay storage audit: 414 metadata rows, zero missing files, zero size mismatches, and zero paths outside the
  configured root.
- Discord read-only authentication: bot account authenticated, `/users/@me` returned 200,
  `/users/@me/guilds` returned 200, and 16 guild memberships were visible. No message was sent.
- Collector one-cycle run: one active target, no errors, and no remaining fetch backlog.
- Post-processing one-cycle run: every processor, including parser-version-aware fight-outcome backfill, had zero
  remaining candidates and zero errors.
- Fight-outcome backfill: 207 match/player payloads produced 815 unique rows and 207 v2 processing states; state
  counts equal outcome rows, duplicate key groups are zero, and immutable raw/replay files were not changed.
- Live player report: after excluding one friendly-fire row by default, 814 fights produced 367 wins and 447 losses
  (45.09 percent), split into 229 kill wins, 138 DBNO wins, 230 death losses, and 217 DBNO losses. Fifty-six
  non-firearm contexts remain in the event ledger but are excluded from firearm/loadout rankings.
- Local API and UI: the real profile returned M416, AUG, and Beryl firearm rankings; desktop and 390 px mobile
  Playwright checks rendered the result with no horizontal overflow, console errors, or page errors.
- Browser replay check: a real `Savage_Main` timeline rendered a 960 by 960 nonblank canvas with 31 event buttons,
  an advancing playback clock, no document overflow, no console errors, and no page errors. Visual inspection confirmed
  the map, plane route, tracked player, three teammates, combat events, deaths, and care packages were legible.

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
| CFG-06 | Alert locally and in Discord when storage is low or unavailable | IMPLEMENTED | `storage_alerts.py`, `system_alerts.py`, and their tests; a real low-disk event has not been forced. |
| CFG-07 | Never expose the local manager to other computers by default | PROVEN | Runtime and tests require loopback binding; the active server is `http://127.0.0.1:8018`. |

## Player And Match Collection

| ID | Requirement | Status | Evidence and remaining note |
| --- | --- | --- | --- |
| COL-01 | Register nickname together with platform | PROVEN | `player_registry.py`, `pubg_client.py`, tests, and the live Steam lookup. |
| COL-02 | Resolve nickname once and collect later by `accountId` | PROVEN | Registry persists the resolved identifier; the live collection used the stored account. |
| COL-03 | Use admin-managed tracking targets, not ownership claims | IMPLEMENTED | Registry and Discord permission tests enforce the tracking-target model. |
| COL-04 | Collect only registered targets as the primary scope | PROVEN | Collector queried the one active registered target; no unregistered polling path exists. |
| COL-05 | Stop collection on unregister but retain prior data | IMPLEMENTED | Registry and Discord command tests cover inactive retention. |
| COL-06 | Discover match IDs from player data, then fetch each match | PROVEN | Live run discovered and processed 61 match IDs exactly through this flow. |
| COL-07 | Process completed matches and telemetry after match completion | PROVEN | Match and telemetry jobs completed for all 61 discovered matches. PUBG provides this as post-match data. |
| COL-08 | Collect every discoverable match for tracked players | PROVEN | Continuous polling stores every new ID exposed by the player endpoint, with idempotent jobs. Historical reach is limited by PUBG's player response window. |
| COL-09 | Classify mode, perspective, match type, map, and shard immediately | PROVEN | `match_classification.py`, tests, and populated match rows. |
| COL-10 | Store timestamps in KST | PROVEN | `time_utils.py`, tests, and live KST match-window inspection. |
| COL-11 | Preserve raw player, match, and telemetry payloads | PROVEN | 3 player snapshots, 207 match payloads, and 207 telemetry payloads are retained. |
| COL-12 | Record total, human, and bot population | PROVEN | `match_population.py`, tests, and participant ingestion. |
| COL-13 | Handle API limits, retries, and idempotency | PARTIAL | Headers, retry states, chunking, and tests exist. A controlled real 429 and recovery drill remains. |

## Telemetry And Match Facts

| ID | Requirement | Status | Evidence and remaining note |
| --- | --- | --- | --- |
| TEL-01 | Record pickup, drop, use, equip, attach, and detach events | PROVEN | Item processor produced 28,825 retained item events. |
| TEL-02 | Translate item and weapon codes to Korean with raw-code fallback | PROVEN | `code_translator.py` and tests; unknown new codes remained visible verbatim in live output. |
| TEL-03 | Record survival time, kills, damage, assists, rank, movement, and chicken result | PROVEN | Match participants and player combat/movement summaries cover these values. |
| TEL-04 | Record total and per-weapon damage dealt and received | PROVEN | Combat summary and 679 per-weapon rows; live player statistics and recommendations loaded them. |
| TEL-05 | Record shots fired, hits, body-part hits, and headshots per weapon | PARTIAL | Events and per-weapon counters are retained. Shotgun pellet hits can exceed shell fire events, so accuracy semantics need an explicit weapon-family rule instead of relying on the current clamp. |
| TEL-06 | Record kills, assists, deaths, DBNO caused, and DBNO taken separately | PROVEN | Parser, summaries, per-weapon rows, and combat tests distinguish every requested direction. |
| TEL-07 | Distinguish DBNO, finish, death, revive, and repeated life-state events | PROVEN | Event-indexed telemetry facts preserve repeated DBNO/death/revive sequences. |
| TEL-08 | Count causing a DBNO as winning a duo or squad fight | PROVEN | Schema-backed outcomes persist duo/squad DBNO caused as wins and DBNO taken as losses; solo DBNO is ignored. The live report contains 138 DBNO wins and 217 eligible DBNO losses. |
| TEL-09 | Preserve winning and losing weapon plus attachment context | PROVEN | Parser v2 prioritizes actual `LogPlayerAttack.weapon.attachedItems`, then reconstructed item state/equip and damage-event fallback. Live backfill stores kill, DBNO-caused, DBNO-taken, and death contexts in the event ledger and shared snapshot table. |
| TEL-10 | Record movement path, landing, kill, DBNO, death, and revive locations | PROVEN | Movement processor populated positions, landing facts, and 1,427 combat-location events. |
| TEL-11 | Record plane route, care-package locations, and phase movement | PROVEN | 174 plane routes, 6,897 care-package events, and 29,991 phase events are retained. |
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
| ANA-08 | Map coordinate clusters to named regions | PENDING | Phase 2 needs a versioned per-map region dictionary and point-in-region mapper. |
| ANA-09 | Group reports by hour, day, week, and month in KST | PENDING | KST timestamps exist, but durable or query-time trend reports do not. |
| ANA-10 | Group trends by solo, duo, squad, perspective, match type, map, and shard | PARTIAL | Dimensions are stored immediately; complete grouped trend APIs/UI are not implemented. |
| ANA-11 | Compute durable fight win/loss rates by weapon and attachments | PROVEN | MySQL service, CLI, localhost API/UI, and Discord formatter expose total, reason, firearm, and exact loadout win/loss rates. Real API and desktop/mobile UI checks passed. |

## Replay And Local Management

| ID | Requirement | Status | Evidence and remaining note |
| --- | --- | --- | --- |
| REP-01 | Generate and retain JPEG match maps | PROVEN | 207 JPEG artifacts exist under the configured replay root. |
| REP-02 | Show plane, parachute/landing, movement, kill, and death locations | PROVEN | Renderer/timeline tests and a real browser replay inspection passed. |
| REP-03 | Generate and retain post-match 2D replay timelines | PROVEN | 207 timeline artifacts exist; playback advanced without browser errors. |
| REP-04 | Provide true in-match live tracking | NOT APPLICABLE | The PUBG Open API exposes usable telemetry after match completion. The implemented product is a post-match 2D replay, matching the confirmed collection constraint. |
| WEB-01 | Manage settings, targets, permissions, workers, artifacts, and analytics locally | PROVEN | Local web routes/UI and their test suites cover these workflows. |

## Discord And Operations

| ID | Requirement | Status | Evidence and remaining note |
| --- | --- | --- | --- |
| DSC-01 | Register, query, and unregister a player in the command channel | IMPLEMENTED | Bot command and formatting tests pass. A real command reply has not been sent. |
| DSC-02 | Assign command permissions through guild-scoped groups | IMPLEMENTED | Permission manager, Discord authorization, and web permission tests pass. |
| DSC-03 | Allow a global administrator to inspect all guilds | IMPLEMENTED | Scope and authorization tests pass. |
| DSC-04 | Provide guild rankings and optional public profiles | IMPLEMENTED | Ranking/public-profile tests pass. |
| DSC-05 | Authenticate the actual configured bot | PROVEN | Read-only Discord REST authentication and 16 guild memberships were verified. |
| DSC-06 | Exercise a real command in a selected guild and channel | PENDING | Requires an explicitly selected test guild/channel and a harmless command invocation. |
| OPS-01 | Run collector and post-processing workers | PROVEN | Both workers completed real one-cycle runs and left zero backlog. |
| OPS-02 | Recover cleanly across long-running operation and restart | PARTIAL | Idempotent one-cycle behavior and run history exist; a soak, forced stop, restart, and backlog recovery drill remains. |
| OPS-03 | Let an administrator decide whether retained player data is deleted | PARTIAL | Request, preview, backup, verification, quarantine, restore rehearsal, and review tooling exist. There is intentionally no destructive executor yet. |

## Prioritized Remaining Roadmap

1. **P0: KST trend reports**
   Add hour, date, ISO week, and month groupings, then combine them with mode, perspective, match type, map, and shard
   filters in service, local UI, and Discord-safe summaries.
2. **P1: real Discord command acceptance test**
   Run one read-only query and one disposable registration lifecycle in a user-selected test channel without exposing the
   token.
3. **P2: named region mapping**
   Introduce versioned map-region geometry and map coordinate clusters to Korean place labels while retaining the raw
   coordinates and cluster ID.
4. **P2: operational fault drills**
   Exercise controlled 429/backoff behavior, a low-space alert, worker stop/restart recovery, and a bounded soak run.
5. **P2: hit and accuracy calibration**
   Define shell, projectile, and pellet semantics per weapon family and report both raw hit events and a clearly named
   accuracy metric.
6. **P3: destructive deletion execution, only when explicitly approved**
   Keep the existing review and backup gates. Do not add or invoke irreversible deletion as an incidental step.

## Completion Gates

The original core scope can be called complete when:

- The KST trend gap has tests, local UI, and Discord-format coverage across the stored match dimensions.
- Existing retained matches can be queried or backfilled idempotently for the new trend facts.
- One real Discord channel acceptance run succeeds with no secret disclosure.
- The collector survives the bounded rate-limit and restart drills without duplicate or missing match jobs.
- Raw and replay storage audits remain at zero missing, mismatched, or escaped files after the changes.
- The full automated suite, compile check, diff check, and secret scan pass.
