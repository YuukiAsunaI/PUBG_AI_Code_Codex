# Discord and All-Map Release Audit - 2026-09-03

## Scope

This audit verifies two user-facing changes end to end:

- the small-circle readability behavior is shared by every map rather than special-cased for Sanhok;
- the local player-analysis capabilities are available as readable hybrid Discord commands with scalable selection
  flows.

The checks used the packaged Windows application, the configured local MySQL database, and the command metadata stored
by Discord. Secrets were loaded through the existing ignored `.env` and were not printed or embedded in the artifact.

## Delivered Discord Surface

The app-managed bot now exposes `종합분석`, `시간대`, `비교`, `낙하`, and `매치상세` in addition to the existing
player commands. The same handlers support slash and prefix invocation and use the `profile_read` permission group.

- A blank player option opens a public, caller-controlled picker scoped to registrations in the current guild.
- The picker uses 25 rows per page, persistent selection, previous/next navigation, and partial nickname or account-ID
  search; it does not cap the guild registration count at 25.
- Weapon analysis opens a second paged picker containing only weapons actually used by the selected player.
- Whole-match detail opens a stored-match picker based on KST date, Korean map/mode, and participant search instead of
  requiring a remembered match ID.
- Comparison supports player, map, weapon, game-mode, team-mode, and perspective dimensions. Map and game-mode filters
  use Discord autocomplete.
- Long reports use compact embed fields, previous/next controls, and a page-jump menu.
- Weapon output includes no-parts, grouped individual attachments, observed attachment combinations, independent
  minimum-match thresholds, and daily/monthly trends.

The live Discord API check passed for four configured guilds. Each guild had exactly the expected 31 commands, and all
checks for Korean descriptions, command visibility, autocomplete, configurable sample limits, no oversized static
choice list, scalable registered-player selection, and match selection without a match ID passed.

## Packaged Browser Verification

The release was built and tested as `dist/PUBG_AI_Manager.exe`, not only through a source development server.

- Release: `2026.09.03.1`
- Size: 52,142,035 bytes
- SHA-256: `AEB8847FF452F1FEABDC15739A4F6DCC00243DB44A681CEA4EBA5440D1AD7A45`
- Health: HTTP 200, `local_only=true`, bind host `127.0.0.1`, matching release
- Discord runtime: ready, four guilds, 31 synchronized commands per guild, no reported startup error
- Shutdown: the native close request removed both PyInstaller processes and the port-8000 listener

Playwright visited all 33 application workspaces on desktop and 390px mobile viewports. There were no blank sections,
overlays blocking the interface, overflowing buttons, console errors, failed requests, or HTTP errors.

The all-participant replay test rendered 99 participants and 10,088 events, kept only 240 event rows in the DOM, and
updated after selecting all actors in 171 ms. Canvas pixel sampling confirmed a nonblank rendered replay.

## All-Map Circle Regression

The test requests the maps and phase data from the running application. It does not contain a hardcoded Sanhok-only
fixture. For every map with stored phase-7 circle data it verifies:

- the map exists in the filter;
- the map image loads with nonzero dimensions;
- ranked list rows and map markers have equal counts;
- every row has a non-empty location label and rank marker;
- selecting a row focuses the map to a viewport smaller than the full map;
- the focused viewport is 150 x 150 for the current small-circle samples;
- the focused location context is non-empty.

The current corpus covered nine maps and all passed: Deston, Rondo, Miramar, Vikendi, Sanhok, remastered Erangel,
Karakin, Taego, and Paramo. The renderer and focus functions are map-agnostic, so newly collected maps use the same
behavior; a map without stored phase-7 data is naturally absent from this data-driven runtime pass.

## Real MySQL Service Verification

`Yuuki_Asuna---` was used as the configured Steam fixture without modifying its records. The verifier passed:

- comprehensive analysis: 402 matches;
- KST time analysis: 21 buckets;
- map comparison: nine groups;
- weapon analysis: M416, 11 compact Discord pages;
- recommendations: five primary weapons and five two-weapon loadouts, 10 compact pages;
- whole-match detail: 97 participants and 27,593 telemetry events, six compact pages;
- drop-zone analysis and all Discord embed field/page limits.

## Automated Tests

- Full Python suite: 689 passed.
- Focused Discord command and formatting suite: 51 passed.
- Python compilation, JavaScript syntax, and Git whitespace checks passed.
- Remaining warnings are upstream `discord.py` compatibility deprecations under Python 3.14; no test failed because of
  them.

The repeatable checks are `scripts/verify_local_ui.js`,
`scripts/verify_discord_command_deployment.py`, and `scripts/verify_discord_player_services.py`.
