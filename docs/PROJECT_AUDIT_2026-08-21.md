# Full Project Audit - 2026-08-21 KST

## Outcome

The current source tree, MySQL data, configured local stores, post-match 2D replay pipeline, player analytics,
recommendation model, Discord permissions, local-only manager, and packaged Windows executable were reviewed again.
Reproduced defects were fixed and the current retained corpus was validated without modifying immutable raw payloads.

Audit snapshot:

- Python test suite: **550 passed**. The 33 warnings are dependency deprecations from Starlette TestClient and
  discord.py on Python 3.14, not failed application assertions.
- `python -m compileall -q src tests` and `git diff --check` pass.
- MySQL 8.0.41, database `pubg_ai`, 48 tables, connection successful.
- Current storage configuration: `F:\BackUP\raw` and `F:\BackUP\replay`. Both directories exist and are
  writable; the audit checkpoint had about 746 GiB free.
- Database checkpoint: 6 registered/active targets, 1,454 matches, 1,454 raw match payloads, 1,454 raw telemetry
  payloads, and no pending or failed API jobs.
- Latest replay corpus: 1,783 `player-timeline-v6` JSON files and 1,783 `map-snapshot-v5` JPEG files.
- The rebuilt `dist/PUBG_AI_Manager.exe` is 50,684,415 bytes. A packaged-process smoke test found the real GUI child
  window, confirmed `local_only=true`, closed it through the Windows close message, and verified that no child
  process or listening port remained.

## Replay Re-Audit

The replay audit read every latest artifact from the currently configured `F:\BackUP\replay` root. It did not rely
on the previous drive or on a database-only count.

- Artifacts checked: **3,566**
- Bytes hashed and parsed/decoded: **1,065,488,505**
- Timeline JSON: **1,783**, schema `player-timeline-v6`
- JPEG snapshots: **1,783**, renderer `map-snapshot-v5`, all **1280 x 1418**
- Path escapes, missing files, size mismatches, SHA-256 mismatches, and filename-hash mismatches: **0**
- JSON decode, schema, match/player identity, count-contract, event-time/index ordering, segment-order, and map-bound
  failures: **0**
- Initial transport-aircraft samples left in player paths: **0**
- Plane-route endpoint/time/index failures: **0**
- JPEG decode, format, dimension, and blank-preview failures: **0**

Replay behavior was also exercised in the real local UI:

- The plane route is reconstructed independently of the selected player's jump time and extended to both map
  boundaries, so it remains visible after an early jump.
- Lobby-to-plane movement and initial transport-aircraft positions are excluded from player movement.
- Respawn/redeploy and impossible movement gaps create separate path segments instead of drawing false straight lines.
- Player, teammate, phase, landing, combat, and care-package layers use the same piecewise telemetry clock.
- Player selection exposes 198 available timeline artifacts in the checked account, playback changes the canvas over
  time, and desktop/mobile layouts have no control or document overflow.

## Metric Contracts

The following definitions are enforced in profile, weapon, recommendation, match, and trend output:

- Accuracy uses the weapon-class-aware accuracy metric. Shotgun pellet hits are not presented as a conventional
  shot-level percentage.
- **Headshot hit rate = head hits / all hits.** Missed shots are never included in this denominator.
- Headshot kill rate remains a separate metric: headshot kills / kills.
- Received head-hit rate = head hits taken / all hits taken.
- Body-part hit and received-hit probabilities each normalize against their own observed hit totals.
- Fight wins are kills plus DBNOs caused. Fight losses are deaths plus DBNOs taken; DBNO and death remain separately
  inspectable so revival does not erase either event.
- Environmental, non-firearm, friendly-fire, human, and bot contexts are classified separately in fight evidence.

The live checked profile contained 19,532 shots fired, 2,687 hits, and 354 head hits. The reported headshot hit rate
was exactly `354 / 2,687 = 13.1745%`. Its hit-part and received-hit-part distributions each summed to 100%.

## Recommendation Review

Recommendation output now separates compact summaries, charts, evidence, score explanations, attachment combinations,
effective-distance evidence, and drop-zone analysis.

- A two-primary recommendation pairs complementary close/mid and long-range roles instead of returning AR + AR by
  default.
- Weapon score components expose evidence confidence, range bonus and cap, fight adjustment, and total score.
- Loadout score components expose the 55% primary contribution, 45% secondary contribution, inventory adjustment,
  and total score.
- Attachment output includes the best observed full combination for each weapon plus per-slot alternatives and
  evidence counts; raw API codes fall back safely but known codes use Korean game-facing labels.
- Inventory model `inventory-weight-v3` uses PUBG inventory units per round, recommended reserve rounds, shared-ammo
  pooling, a mixed-ammo penalty, and reserve-pressure cost. LMG reserves contribute their AR-baseline excess to the
  same total burden instead of applying a hidden duplicate penalty.
- MG3 and RPD use 220 recommended reserve rounds in the heuristic. The checked MG3 + M24 result therefore reports
  42.0 extra LMG inventory units rather than incorrectly showing zero.
- Drop-zone analysis is a separate view with landings, win rate, average placement, kills, and damage. Chart limit and
  sort order are independently selectable while the detailed table retains every region.
- Recommendation minimum matches accepts large historical thresholds rather than stopping at 50.

The ammo reserve and scoring model is deliberately labeled heuristic. It describes inventory trade-offs and does not
claim that a loadout score is an objective PUBG win probability.

## Discord And Manager UX

- All 26 catalog commands are registered with `Bot.hybrid_command`, allowing slash and configured prefix invocation
  to share the same permission checks and implementation.
- Discord permission groups can be created, renamed, edited, assigned commands from the known catalog, and removed in
  the local manager.
- Optional prefix aliases are editable but may delegate only to catalog commands; they cannot execute arbitrary code.
- Guilds, alert channels, ranking scope, registered players, weapons, matches, and replay players use searchable
  selections instead of requiring remembered IDs.
- Search fields start empty, successful player lookup clears the query field, and detailed filter groups include
  reset actions.
- Queue summaries distinguish waiting, processing, retry, failed, and complete states. A stopped worker with a
  non-empty queue is shown as stopped/waiting rather than as an unexplained processing failure.
- The manager remains bound to `127.0.0.1`; external Host and cross-site write protections remain covered by tests.

## Player Analysis Follow-Up - 2026-08-22 KST

The player workspace was reviewed again as a player-centered analysis tool rather than as a collection of unrelated
forms.

- Selecting an exact registered player now establishes one in-memory analysis context shared by profile, trend,
  weapon, recommendation, drop-zone, and match views. Result/filter resets preserve that context; only the explicit
  release action clears it.
- A stale-response guard prevents a slower weapon/match catalog request for the previous player from overwriting a
  newer player selection.
- Date, week, month, quarter, and year trend views render chronological SVG line charts. Map, mode, and other
  categorical groupings retain comparison bars. Every chart reports its metric definition, displayed/available
  buckets, and match sample size.
- Weapon details expose daily and monthly time series for fight win rate, match win rate, class-aware accuracy,
  headshot hit rate, damage dealt/taken, kills, DBNOs, deaths, and usage count.
- Weapon catalog usage now has the same outgoing-use contract as weapon detail: shots fired, damage dealt, kills,
  assists, or DBNOs. In the live check, M416 showed **75 matches** in both the selector and detail result.
- A failed auxiliary fight-outcome request is shown as a partial-data warning and unavailable fight metrics instead
  of misleading zero-valued performance.
- Registration, collection-stop, status, alert, and worker requests now share checked response handling; periodic
  refresh failures are surfaced without creating unhandled browser promise errors.

Verification:

- Full Python suite: **550 passed**, 33 dependency deprecation warnings.
- Embedded JavaScript: parsed successfully with Node `vm.Script`.
- Live MySQL: 8.0.41, database `pubg_ai`, 48 tables, connection successful.
- Live Edge/Playwright check: registered-player context persisted through weapon changes and the trend view; the
  checked daily chart rendered 25 points and the monthly chart 3 points.
- Desktop and 390 x 844 mobile checks had no document/workspace overflow, console errors, or page errors.

The collection design remains aligned with the official player-to-match-to-telemetry flow described in
[PUBG telemetry](https://documentation.pubg.com/en/telemetry.html) and the event contracts in
[PUBG telemetry events](https://documentation.pubg.com/en/telemetry-events.html). Player lookups continue to cache
account IDs because player endpoints are rate limited while match and telemetry requests have different limits, as
documented in [PUBG rate limits](https://documentation.pubg.com/en/rate-limits.html). The API key remains server-side
in ignored local environment configuration in accordance with
[PUBG API key guidance](https://documentation.pubg.com/en/api-keys.html).

Comparable stat products such as [PUBG Statistics](https://pubgstatistics.com/) and
[PUBG Stats](https://www.pubgstats.app/) were reviewed for useful interaction patterns: persistent player context,
stats over time, weapon drill-down, and visible sample evidence. No third-party source code was copied.

## Official Evidence

- Telemetry IDs and map assets remain grounded in the authoritative
  [PUBG API assets repository](https://github.com/pubg/api-assets).
- The 7.62 mm inventory weight follows
  [PUBG Update 34.1](https://www.pubg.com/en/news/8170?category=patch_notes).
- RPD classification and magazine behavior were checked against
  [PUBG Update 42.3](https://pubg.com/en/news/10885).
- MG3's 7.62 mm LMG classification and 75-round magazine were checked against the official
  [MG3 introduction](https://pubg.com/zh-cn/news/4744).
- Hybrid command behavior follows the official
  [discord.py hybrid command guide](https://discordpy.readthedocs.io/en/stable/ext/commands/commands.html#hybrid-commands).

## Remaining Risks

1. Rotate the PUBG API key that appeared in earlier conversation history and update only `.env`. The repository does
   not contain that token and `.env` remains ignored, but conversation exposure still warrants rotation.
2. The optional AI CLI recommendation was not enabled. It would send derived player evidence outside the local
   machine, so provider, payload scope, retention, redaction, and explicit operator consent must be decided first.
3. The recommendation inventory model depends on balance constants that PUBG can change. Patch-note review and a
   model-version bump are required when ammo weight, magazine size, or weapon behavior changes.
4. The source development server intentionally has collector and post-processing workers stopped at this checkpoint.
   This is an operator state, not a queue failure; workers can be started from the local manager.
5. Starlette/discord.py deprecation warnings should be rechecked when dependencies add formal Python 3.14 support.
6. Telemetry processors decode one retained match at a time. A future exceptionally large telemetry file could justify
   incremental JSON parsing, although the current retained corpus passes.

## Reproduction

```powershell
python -m pytest -p no:cacheprovider
python -m compileall -q src tests
git diff --check
python -m pubg_ai.cli db-status
python -m PyInstaller --clean --noconfirm pubg_ai_desktop.spec
```

The audited development manager is available only on this machine at `http://127.0.0.1:8766`.
