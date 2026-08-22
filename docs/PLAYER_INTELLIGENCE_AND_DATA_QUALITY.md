# Player Intelligence and Data Quality

## Purpose

This document defines the normalized telemetry contract behind the local manager's player-intelligence workspace.
The goal is to make detailed PUBG analysis reproducible: every displayed rate has an explicit numerator and
denominator, parser coverage is visible, immutable raw telemetry remains the source of truth, and a zero is not shown
when the required parser has not processed the match.

The implementation is local-only. It reads registered tracking targets from MySQL, reads retained telemetry from the
configured raw storage root, and exposes results through the localhost manager and CLI. API keys and Discord tokens
remain in `.env` and are never returned by these reports.

## Materialized Layers

| Layer | Current contract | Purpose |
| --- | --- | --- |
| `raw_telemetry_payloads` | immutable source metadata | Locates the retained official telemetry JSON. |
| `player_telemetry_processing_states` | monotonic parser version per match/player/stage | Records exactly which parser produced the materialized rows. |
| `match_telemetry_event_counts` | `activity-v2` | Counts every observed event type, tracked-player relevance, and normalized output per match. |
| `player_activity_events` | `activity-v2` | Stores normalized support, utility, mobility, vehicle, and environment events. |
| `player_match_activity_summaries` | `activity-v2` | Stores per-match counts and amounts for fast player aggregation. |
| `player_item_events` | `items-v4` | Stores item action, quantity, source, translated code context, and event order. |
| `player_item_match_stats` | `items-v4` | Stores per-match/item action totals and provenance totals. |

`items-v4` distinguishes normal pickup, loot box, care package, custom package, vehicle-trunk pickup, and
vehicle-trunk put events. Unknown item codes remain queryable as their original PUBG code; display translation never
destroys the raw identifier.

`activity-v2` normalizes healing, armor destruction, revive roles, character carry state, throwable and flare-gun
use, vehicle ride/leave/damage/destruction, wheel destruction, vaulting, swimming, object interaction/destruction,
environment destruction, emergency pickup, redeploy, and special-zone membership. Existing combat, item, movement,
landing, care-package, game-state, and match-start processors are included in the project event-support catalog.
Observed event types that do not yet have a stable analytical meaning are explicitly marked `raw_only`; unclassified
types are surfaced by the audit rather than silently ignored.

## Metric Contracts

The canonical metric catalog is implemented in `src/pubg_ai/metric_catalog.py` and is available from
`GET /analytics/metrics`. Important contracts are:

| Metric | Formula | Denominator rule |
| --- | --- | --- |
| Chicken rate | chicken matches / selected matches | Matches passing all selected filters. |
| TOP 10 rate | matches with `win_place <= 10` / ranked matches | Only matches with a recorded placement. |
| Accuracy | hits / estimated conventional shots fired | Shotgun pellets and special weapons follow weapon-aware rules. |
| Headshot hit rate | head hits / all hits | Missed shots are never included. |
| Fight win rate | resolved fight wins / resolved fights | Kills and DBNOs caused are wins; deaths and DBNOs taken are losses, with follow-up deduplication. |
| Average damage dealt | total damage dealt / selected matches | All selected matches, including zero-damage matches. |
| Total healing amount | sum of all `LogHeal.healAmount` | Only matches processed by the current activity parser. |
| Item healing amount | `LogHeal` amount with `item.itemId` | Item use count and actual healed health are separate concepts. |
| Passive healing amount | `LogHeal` amount without `item.itemId` | Boost healing ticks are not interpreted as item-use events. |
| Activity coverage | current-version processed matches / eligible raw matches | A rate below 100% marks incomplete analytical evidence. |

For activity metrics, averages use only matches with a current `activity-v2` summary. The API returns covered and
eligible match counts together. If no covered match exists, the activity average is `null`, not `0`. Temporal graph
buckets without current activity coverage are omitted for activity-dependent metrics.

## Query Behavior

The selected registered player is retained across overview, profile, trend, time-of-day, comparison, weapon,
recommendation, landing-zone, and match views. Local catalog queries provide weapon and match selectors, so the
operator does not need to remember PUBG codes or match IDs.

The intelligence endpoint accepts the same immediate match classifications used during collection: shard, map,
game mode, team mode, perspective, match type, season state, custom flag, KST date range, exact date, year, quarter,
month, and hour where applicable. Trends support time-based and categorical groupings. Large histories are queried in
bounded 500-match batches and merged after each SQL result, avoiding a single unbounded `IN` clause when a tracked
player reaches thousands of matches.

The local manager progressively exposes:

- `핵심 요약`: high-signal totals, combat probabilities, support, mobility, and coverage.
- `변화 그래프`: KST day/week/month trends with sample and denominator context.
- `조건 비교`: map, mode, perspective, match type, hour, and weekday comparisons.
- `원본·정의`: event support, raw-only evidence, item provenance, and metric definitions.

## Data-Quality Audit

Run the read-only audit after backfill, deployment, or a parser-version change:

```powershell
python -m pubg_ai.cli audit-player-intelligence
```

The local manager exposes the same operation under `운영·알림 > 플레이어 데이터 품질`. It is intentionally
manual because it scans materialized coverage and reconciliation tables.

The audit currently enforces eleven checks:

1. Applied MySQL schema version equals the application schema version.
2. Current activity parser covers every eligible match/player pair.
3. Current activity parser covers every eligible match.
4. Current item parser covers every eligible match/player pair.
5. Current item parser covers every eligible match.
6. Activity processing-state output counts match summary and event rows.
7. Total healing equals item healing plus passive boost healing.
8. Per-match event-catalog normalized totals match player activity summaries.
9. Item processing-state output counts match item event rows.
10. Activity summaries contain no invalid negative values.
11. Item summaries contain no invalid negative values.

An audit can correctly fail immediately after the collector stores a new telemetry payload. Process the new rows and
run the audit again:

```powershell
python -m pubg_ai.cli parse-telemetry-activity --limit 200
python -m pubg_ai.cli parse-telemetry-items --limit 200
python -m pubg_ai.cli audit-player-intelligence
```

Repeat bounded parser runs when more than the selected limit is pending. The audit exits with code `0` only when all
checks pass and code `1` otherwise.

Parser-state writes are version-monotonic. A stale process cannot replace `items-v4` with `items-v3`, or another
newer numeric version with an older one. Candidate selection also treats only a numerically lower stored version as
outdated. This protects current results when an old packaged manager remains open during an upgrade.

The normalized-data deletion review includes `player_activity_events` and
`player_match_activity_summaries` as player-owned rows. `match_telemetry_event_counts` remains protected shared match
context. The corresponding dry-run and backup-builder contracts are `deletion-dry-run-v3` and
`deletion-backup-builder-v2`; deletion execution itself remains disabled.

## Verified Checkpoint

The 2026-08-22 KST live checkpoint passed all eleven checks:

- 1,517 raw and eligible matches; 1,860 eligible match/player pairs.
- Activity coverage: 1,517 matches and 1,860 match/player pairs.
- Item coverage: 1,517 matches and 1,860 match/player pairs.
- 249,857 normalized activity events and 1,860 activity summaries.
- 305,939 item events and 56,244 per-match item summaries.
- 49 observed event types: 42 normalized, 7 explicitly raw-only, 0 unclassified.
- Item provenance: 15,680 loot-box pickups, 783 care-package pickups, 32 custom-package pickups,
  1,128 vehicle-trunk pickups, and 1,309 vehicle-trunk puts.

These counts are a checkpoint, not a fixed expected value. The collector can add completed matches at any time.

The final Python suite passed with 572 tests after the browser-discovered form regression was fixed. The rebuilt
Windows executable also passed its own Edge-based QA at desktop and 390 px mobile viewports. Coverage includes player
overview, trend SVG, definition evidence, all eleven audit rows, console errors, failed requests, HTTP errors,
blank/error overlays, document overflow, and button overflow. The reusable script is
`scripts/verify_local_ui.js`.

The final packaged smoke test opened the real GUI, returned localhost-only health and HTTP 200, queried the live
`Yuuki_Asuna---` intelligence report at 100% activity coverage, passed all eleven audit checks, accepted the Windows
close message, and left no process or listening port. `dist/PUBG_AI_Manager.exe` was 50,777,863 bytes with SHA-256
`7F5A000A5A6B3841D27904D53B1CBB53298A07D3CAAB10A2DF3BF25200962663` at this checkpoint.

## Official Evidence Boundary

The event names and object fields are interpreted against PUBG's official documentation:

- [Telemetry events](https://documentation.pubg.com/en/telemetry-events.html)
- [Telemetry objects](https://documentation.pubg.com/en/telemetry-objects.html)
- [Getting started and API request flow](https://documentation.pubg.com/en/getting-started.html)
- [Official API assets and data dictionaries](https://github.com/pubg/api-assets)

Derived fight outcomes, accuracy estimates, named-region clustering, recommendations, and inventory-cost heuristics
are project analytics. They must remain labeled as derived and must not be presented as official PUBG statistics.
