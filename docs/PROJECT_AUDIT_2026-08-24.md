# Full Project Audit - 2026-08-24 KST

## Outcome

The current source tree, actual local MySQL database, localhost UI, Windows package, Discord control boundary, combat
metric propagation, map-region catalog, and frequent flight-path analysis were reviewed as one release candidate.
Reproduced defects were fixed without rewriting immutable raw match or telemetry payloads.

Final checkpoint:

- Python suite: **611 passed**, 69 dependency deprecation warnings, 0 failures.
- `python -m compileall -q src tests` and `git diff --check` pass. The Git output contains only the repository's
  existing Windows LF-to-CRLF notices.
- MySQL 8.0.41, database `pubg_ai`, schema version **29**, 52 tables, connection successful.
- Re-running `init-db` completed 53 idempotent schema statements.
- Full Edge/Playwright workflow returned HTTP 200 on desktop and mobile with no console errors, failed requests, HTTP
  errors, blank screens, global overflow, overlays, or overflowing buttons.
- The packaged executable opened a native `PUBG AI Local Manager` window and closed through `WM_CLOSE`; both new
  package processes exited and no error-dialog process remained.

## Map Region Review

The catalog is now `2024-10-28.api-assets-32b13b5.v2`, pinned to official API Assets commit
`32b13b51128b8d8909ae5e77f3b833e01230b24d` with source-image SHA-256 values retained.

- Taego `Market` is a distinct high-confidence region west of `Terminal` instead of being absorbed into a broad
  Terminal circle.
- `taego.market` uses center `(53.5%, 44.5%)`, radius `3.2%`.
- `taego.terminal` uses center `(60.0%, 44.5%)`, radius `4.3%`.
- Each correction exposes review date, confidence, classification, parent district, Korean note, and evidence links
  through the map-region catalog API.
- The unmatched-coordinate fallback is 20 x 20. Open terrain is kept as a grid location instead of receiving the
  nearest convenient place name.
- Tests prove representative Market and Terminal coordinates resolve separately and retain the pinned source
  metadata.

The latest official API Assets map update is the 31.2 revision. Official Taego notes reviewed through Updates 35.2,
41.1, and 42.2 describe later gameplay or terrain changes but do not publish a newer named-area layout. Treating the
pinned map as current is a cross-source inference. PUBG does not provide machine-readable region polygons, so every
maintained circle remains a versioned project interpretation.

## Frequent Flight Paths

The Replay workspace now has a separate `비행기 동선` analysis backed by completed-match `match_plane_routes` rows.
It does not infer routes from the selected player's jump time.

- Observed aircraft samples are extended to both map boundaries.
- Opposite travel directions share one physical route, while the dominant actual direction remains visible as an
  arrow and confidence percentage.
- Routes are grouped by undirected angle and signed center offset. The offset uses the common angle-bin axis, so
  near-horizontal routes do not split at the 0/180-degree representation boundary.
- Filters cover registered player, platform, map, game/team mode, perspective, match type, season, custom match, KST
  year/quarter/month/date/hour/range, angle bin, offset bin, displayed routes, recent evidence, and source limit.
- Output includes a per-map overlay, ranked frequency/share, direction confidence, observation length/sample count,
  first/last KST occurrence, and recent source matches.

Actual MySQL check with 10-degree/500 m bins and a 1,000-route source limit:

- input/analyzed/rejected: **1,000 / 1,000 / 0**
- maps: **8**
- available clusters: **439**
- returned map-top clusters: **38**
- recent evidence rows: **5**

This is historical frequency evidence, not a claim that the next match's route can be predicted.

## Discord Bot Boundary

- `EnvSecretStore` writes PUBG API and Discord bot credentials only to the ignored project `.env` using an atomic
  replacement. Saved values have no read-back API; the browser receives configured/missing status only.
- The local manager can start, stop, and sync the dedicated bot, set auto-start and the hybrid-command prefix, and
  choose the application commands visible in each known guild.
- Slash registration and prefix invocation apply the same per-guild command gate. Global application commands are
  cleared when guild-specific commands are synchronized, avoiding accidental visibility in unrelated servers.
- Permission groups, aliases, ranking scopes, alert channels, and many-to-many player/guild registrations remain
  independently configurable.
- Application shutdown stops the controller before the manager-owned web server, preventing the former zero-value
  close dialog.

The audit did not start or synchronize the live external Discord bot because that would mutate real Discord command
state. Controller, command-tree, visibility, secret, API, and shutdown behavior are covered by automated tests and the
write-only inputs were confirmed empty in the browser.

## Combat And Display Contracts

Schema 29 adds weapon-level `character_hits`, `vehicle_hits`, and `vehicle_damage_dealt`.

- A ballistic vehicle impact counts as a hit in total hit evidence.
- Character accuracy and headshot-hit probability retain character-only denominators.
- Headshot-hit probability remains `head hits / character hits`; misses and vehicle hits are excluded.
- Vehicle evidence propagates through player totals, weapon details, trends, rankings, recommendations, local UI, and
  Discord summaries.

Number rendering now passes through the selected grouped, Korean-unit, or plain formatter across the reviewed UI.
The full UI test changed the example to `5만 9,452`, reloaded the page, confirmed the mode and preview persisted, then
restored grouped mode. Known result labels reviewed in this slice use Korean; unknown PUBG dictionary codes still use
the intentional raw-code fallback.

## Icon And Package

The operator-provided artwork was adapted into a text-free transparent helmet/data-shield icon so small sizes do not
contain broken lettering. The project now includes:

- `src/pubg_ai/assets/app_icon.png` for the manager header and favicon.
- `src/pubg_ai/assets/app_icon.ico` for pywebview and PyInstaller multi-resolution Windows icons.
- package-data and PyInstaller rules that include both assets.

The existing canonical `dist/PUBG_AI_Manager.exe` was locked by an already running manager, so it was not forcefully
replaced. The newly built and smoke-tested release is available as
`dist/PUBG_AI_Manager_2026-08-24.exe` (51,416,800 bytes).

## Remaining Risks

1. Close the older running manager before replacing the canonical executable name. The dated executable is complete
   and verified; the old processes were deliberately left untouched.
2. Rotate the PUBG API key that appeared in earlier conversation history and save the replacement only through the
   write-only local field or `.env`. The repository does not contain the disclosed value.
3. Recheck official map assets and patch notes whenever PUBG changes a map. Community guides may cross-check a
   location but must not become the sole source for a reviewed label.
4. Live Discord login/sync still needs an operator-controlled acceptance run in the intended guild after the token,
   visible command set, and permission groups are finalized.
5. The 69 warnings are current Starlette TestClient and discord.py deprecations under Python 3.14. They are not failed
   assertions, but should be rechecked when those dependencies add formal Python 3.14 support.

## Reproduction

```powershell
python -m pytest -p no:cacheprovider
python -m compileall -q src tests
python -m pubg_ai.cli init-db
python -m pubg_ai.cli db-status
git diff --check
python -m PyInstaller --noconfirm pubg_ai_desktop.spec
```

For the reusable browser workflow:

```powershell
node scripts/verify_local_ui.js http://127.0.0.1:8770
```
