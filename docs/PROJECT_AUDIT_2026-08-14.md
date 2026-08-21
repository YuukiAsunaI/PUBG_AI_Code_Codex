# Full Project Audit - 2026-08-14 KST

## Outcome

The source tree, current MySQL data, configured raw/replay stores, packaged desktop executable, localhost security
boundary, and automatic workers were reviewed again. Reproduced defects were fixed, regression-tested, and applied to
the retained data without modifying immutable raw payloads.

Audit-snapshot evidence:

- Python 3.14.0; `524 passed`; one external Starlette TestClient deprecation warning.
- MySQL 8.0.41, database `pubg_ai`, schema version 23, 48 tables, session time zone `+09:00`.
- The initial retained corpus contained 252 matches for the active tracking target at that checkpoint.
- Its raw catalog contained 252 match payloads and 252 telemetry payloads; the replay catalog contained 252 JPEG map
  snapshots and 252 JSON timelines.
- All 1,008 files cataloged at that checkpoint passed storage-root confinement, existence, size, SHA-256, and JSON/JPEG
  format checks.
- Combat-v3, items-v3, fight-outcomes-v3, and movement-v3 completion states cover every payload selected at the final
  parser-migration checkpoint.
- Distinct unknown item, damage-causer, and game-mode codes: zero.
- API jobs outside `succeeded`: zero at the audit checkpoint; active system alerts: zero.
- Local manager: `127.0.0.1:8000`, root 200, `local_only=true`, bad Host 400, cross-site POST 403.
- Desktop and mobile Playwright checks returned 200 with no console errors, page errors, error overlays, horizontal
  overflow, or overflowing controls.
- Recommendation minimum-sample input accepts values through `2,147,483,647`; a live request with `10,000` was
  accepted without truncation. Summary and graph views can be switched without re-querying, and graph metrics remain
  usable at desktop and mobile widths.
- The installed `dist/PUBG_AI_Manager.exe` passed a packaged-runtime smoke test; the collector and post-processing
  workers are running with successful cycles and no current error.
- The rebuilt executable also passed a real Windows close-message test: health became ready, the main window accepted
  `CloseMainWindow`, exit code was zero, and no process or listening port remained.
- After the final manager restart, the live registry contained two active Steam targets. The workers immediately began
  backfilling the additional target's retained match history, so match, telemetry, replay, and queued-job counts are
  expected to increase after the fixed audit snapshot above. Multiple subsequent collector and post-processing cycles
  completed with zero errors, and the pending queue decreased normally.
- Sixteen Discord server names were synchronized from the bot account. Ranking exposes the server with registered
  tracking targets by readable name and registered-player count rather than requiring a guild ID.

## Fixed Findings

### Data Correctness

1. `sdm-fpp` was stored with `team_mode=unknown`. It is now translated as Solo Deathmatch (FPP), classified as
   `solo`, regression-tested, and the existing row was backfilled. PUBG Update 42.2 identifies the mode as FPP Solo
   Deathmatch for 10-14 players.
2. Current telemetry contained untranslated official item and damage-causer codes. The dictionary now covers every
   distinct retained code, including current attachments and gear, parachute/ascender/Blue Chip variants, FAMAS case
   variants, environmental damage, vehicles, RPD, and Zima.
3. The FAMAS case variant initially broke canonical weapon normalization when added as a duplicate table key. It now
   uses an explicit translation alias, preserving `WeapFAMASG2_C` as the canonical stats code.
4. Translation output changed while the item parser still advertised `items-v1`. The parser is now `items-v2`; all
   252 matches were processed under that version. Stored item, parent-item, child-item, and summary labels exactly
   match the current translator.
5. `Item_Attach_Weapon_Lower_TiltedGrip_C` was labeled with the ambiguous Korean text `경사 손잡이`. PUBG Update 41.1
   distinguishes Angled Foregrip from the new Tilted Grip, so the application now displays the in-game name
   `틸티드 그립`. Item and fight processors were versioned to v3 so existing derived rows were refreshed rather than
   leaving stale labels behind.

### Runtime Correctness

1. Passing an explicit empty environment mapping to configuration loaders incorrectly fell back to the host process
   environment. `None` now means "use process environment" while `{}` remains intentionally empty.
2. `CodeTranslator({})` and an empty override file with `include_defaults=False` incorrectly enabled default tables.
   Explicit empty translation tables are now preserved and tested.
3. Movement summaries with zero renderable in-game positions were selected for map/timeline generation every worker
   cycle. Candidate SQL now requires the same renderable-position conditions used by the renderer. A repeated live
   cycle now reports zero map and timeline candidates instead of retrying forever.
4. Ranking, permission, and ranking-scope screens required operators to type Discord guild IDs. The manager now merges
   stored guild metadata, registered-player counts, permission grants, and configured scopes into a shared catalog.
   Discord-ready/join/update hooks and an explicit local refresh keep names current; ranking only offers guilds with
   registered targets and retains an all-server option.
5. Player-analysis results were presented as dense text and wide tables. Profile, trend, weapon, recommendation, match,
   and ranking results now use metric blocks and grouped sections. Rare trend/weapon filters are collapsed, mobile trend
   rows render as cards, and recommendations present complementary close/mid plus long-range weapon roles.
6. Legacy fight-outcome rows could display raw attachment codes even after the translation dictionary was updated.
   Read-time compatibility translation now resolves those labels without mutating retained raw payloads.
7. Recommendation minimum matches had an arbitrary HTML/API maximum of 50. The UI no longer has that cap and the API
   accepts a signed 32-bit positive threshold. The number remains a minimum evidence threshold, not a limit on the
   number of historical matches analyzed.
8. Recommendation output was a long, always-expanded document. It now opens as a concise summary, keeps evidence
   sections collapsed, and provides an accessible summary/graph segmented control with selectable weapon metrics and
   responsive horizontal charts for weapons, two-primary loadouts, parts, and maps.
9. The desktop entry point caught `SystemExit(0)` as though it were a startup failure, then displayed its string value
   `0` in a Windows error dialog during a normal close. Clean zero/None returns now exit silently; only genuine startup
   exceptions open the error dialog.

### Live Data Repair

1. One existing Solo Deathmatch row was narrowly backfilled from `unknown` to `solo`.
2. The initial 249-match item corpus was migrated to items-v2 in deterministic 200+49 batches. Three later matches
   entered items-v2 through normal post-processing, bringing coverage to 252/252.
3. Two queued telemetry downloads left by a stopped manager were downloaded from the official CDN and fully
   post-processed. A subsequent live collector cycle found one additional match, which was also downloaded, parsed,
   rendered, and verified.
4. The follow-up item-v3 migration processed 343 retained telemetry payloads in deterministic 200+143 batches. It
   handled 13,329,603 telemetry events, produced 83,493 item events and 16,205 per-match stats, and reported zero
   failures. A third run selected zero candidates.
5. The fight-outcomes-v3 migration processed the same 343 payloads in 200+143 batches, produced 2,318 outcomes and
   2,159 loadout snapshots for tracked players, and reported zero failures. A third run selected zero candidates.
   Across loadout snapshots, fight outcomes, item events, and item stats, no stored `경사 손잡이` label remains.

## UX Reference Review

- The official `api-assets` dictionaries remain the source of truth for telemetry identifiers; game-facing labels are
  separately reconciled against current PUBG patch notes when an asset identifier is not a player-facing name.
- `chicken-dinner` informed the replay-view expectation that map layers and playback controls should be independently
  inspectable.
- `pubg_your.stat` reinforced autocomplete, explicit refresh/cache state, and drill-down filters instead of requiring
  operators to remember identifiers.
- `pubgsh/api` reinforced normalized retained data and cacheable derived views. No third-party implementation code was
  copied; only applicable interaction and data-model patterns were adopted.

## Official Evidence

- The project retains the official player-list batching limit of ten and recent-match flow described in
  [Making Requests](https://documentation.pubg.com/en/making-requests.html).
- Match and telemetry handling remains consistent with the official
  [Telemetry documentation](https://documentation.pubg.com/en/telemetry.html) and
  [Rate Limits](https://documentation.pubg.com/en/rate-limits.html).
- Item and damage-causer names were reconciled against the authoritative
  [PUBG API assets repository](https://github.com/pubg/api-assets).
- Tilted Grip naming was verified against
  [PUBG Update 41.1](https://pubg.com/ko/news/9926), which names Tilted Grip separately from Angled Foregrip.
- Solo Deathmatch classification was verified against
  [PUBG Update 42.2](https://pubg.com/en/news/10459).

## Remaining Risks

1. Rotate the PUBG API key that previously appeared in conversation history, then update only `.env`. The repository
   does not contain a JWT-like secret, and `.env` remains ignored, but conversation exposure still warrants rotation.
2. Telemetry processors materialize one decoded JSON match at a time with `json.load`. All retained files pass, but an
   unusually large future payload can consume several times its compressed size in memory. Incremental parsing remains
   the next scalability improvement.
3. The machine-wide Python environment has an unrelated `opencv-python 4.12.0.88` / NumPy 2.4.5 conflict. This project
   declares neither OpenCV nor that NumPy constraint and passes fully; use an isolated virtual environment for future
   dependency changes.
4. FastAPI's installed TestClient emits one Starlette deprecation warning. Runtime and packaged-app tests pass, but the
   dependency set should be refreshed and retested when the replacement adapter is stable.
5. PyInstaller reports non-applicable Android collection and Pydantic-v1 compatibility warnings under Python 3.14.
   The Windows executable builds and runs successfully; an isolated pinned build environment would make releases more
   reproducible.
6. Playwright created desktop/mobile screenshots and all automated visual checks passed. Codex's image viewer could not
   manually open the screenshots because of the Windows ACL on the Korean user path, so that one visual inspection step
   remains an environment limitation rather than a detected application failure.

## Reproduction

```powershell
python -m pytest -p no:cacheprovider
python -m compileall -q src tests
git diff --check
python -m pubg_ai.cli db-status
python -m PyInstaller --clean --noconfirm pubg_ai_desktop.spec
```

The installed manager is available only on the local machine at `http://127.0.0.1:8000`.
