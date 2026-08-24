# Desktop Version And Display Audit - 2026-08-25 KST

## Reproduced Failure

The operator opened `dist/PUBG_AI_Manager_2026-08-24.exe` while an older
`dist/PUBG_AI_Manager.exe` process from 2026-08-23 still owned `127.0.0.1:8000`.

The former desktop launcher treated any healthy PUBG local manager on the preferred port as reusable. The new native
window therefore displayed the older server's HTML. Direct comparison proved the mismatch:

- port 8000: no app-managed Discord bot workspace and no frequent-flight-path workspace;
- port 8770 from current source: both features present;
- the number-format control shown by the operator also matched the older partial implementation.

This was a launcher version-isolation defect, not a missing token or hidden permission setting.

## Corrections

1. Every desktop launch now owns its FastAPI server. An occupied preferred port is never reused.
2. The launcher scans up to 100 localhost ports and selects the first available endpoint.
3. Release `2026.08.25.1` is exposed by `/health`, the desktop JavaScript bridge, the HTML body, and the visible runtime
   badge.
4. Number format is persisted under `display.number_format` in `config/local_settings.json`, independent of browser
   origin or selected local port.
5. The left navigation label is explicitly `Discord 봇`; its first workspace contains write-only PUBG API key and
   Discord token inputs, start, stop, command sync, auto-start, prefix, and per-guild visibility controls.
6. Browser QA now queries real player intelligence while Korean-unit mode is active. It rejects any visible grouped
   number of 10,000 or greater that remains without a Korean large-number unit and restores the operator's original
   mode after the test.

## Evidence

Focused tests passed for endpoint selection, release-aware health checks, display persistence/API injection, desktop
shell, and settings. The final Python suite completed with **618 passed**, 69 dependency deprecation warnings, and no
failures. Python compilation, JavaScript syntax validation, and `git diff --check` also passed.

The real-data Edge workflow verified:

- desktop and mobile HTTP status 200;
- original, persisted, and restored mode: `korean_units`;
- Korean large-unit values present in player analysis;
- unconverted visible values of 10,000 or greater: 0;
- visible navigation label: `05 Discord 봇`;
- app-managed bot workspace visible and write-only secret fields empty;
- console errors, request failures, and overflowing buttons: 0.

The packaged `dist/PUBG_AI_Manager_2026-08-25.exe` was then started while the old manager still owned port 8000. It
selected port 8001, returned release `2026.08.25.1`, loaded `korean_units` from the shared settings file, and exposed
the app-managed bot workspace. Both package processes closed through the native window without forced termination.

## Single App-Managed Discord Bot - Build 2

The Discord architecture was reconciled around one bot identity. Existing player, ranking, replay, permission,
server-scope, alert, and worker commands were retained; only the separate `run-discord-bot` process entry point was
removed so a second runtime cannot compete with the manager-owned instance.

The manager now persists the bot user ID, display name, and authoritative guild membership list. Guild selectors show
only servers joined by that identity. Routine startup synchronization is non-destructive. The explicit
`서버 목록 정리` action requires confirmation before pruning stale guild catalog rows and guild-specific command,
grant, or ranking-scope settings; global administrators, global grants, player registrations, analytics, match data,
and raw/replay files are preserved.

Live reconciliation bound bot user `1541012324524101752` (`PUBG Metrics#9301`) to four current guilds. Twelve stale
catalog rows from the previous identity were removed, leaving the four current guilds. No active player registration
was outside those guilds. A controlled start synchronized all 26 hybrid commands to each of the four guilds, and a
controlled stop completed with `state=stopped` and `last_error=null`.

An additional shutdown defect was found during live verification: an intermediate Discord gateway close timeout could
remain in state after the bot thread had already stopped normally. The controller now uses one authoritative shutdown
deadline, ignores only intermediate close-future timeouts, and reports an error only if the thread actually exceeds
that deadline.

Guild lifecycle handling also uses the bot's complete cached membership set for ready, join, rename, and leave events.
A partial join/update event therefore cannot replace the authoritative list with one guild, while a leave immediately
hides the departed guild without deleting its retained player or analytics data.

Build 2 evidence:

- release endpoint: `2026.08.25.2`;
- Python tests: **627 passed**, 106 dependency deprecation warnings, 0 failures;
- desktop and mobile Playwright checks: HTTP errors 0, console errors 0, request failures 0, overflowing buttons 0;
- managed-bot UI: write-only secret inputs empty, bound account visible, four managed guilds, no page overflow;
- Python compilation, JavaScript syntax validation, and `git diff --check`: passed.

The final packaged artifact `dist/PUBG_AI_Manager_2026-08-25-2.exe` is 51,425,688 bytes with SHA-256
`496AF35D321C6A09B8D887757707A3903233CD07CF8FF54102DB35EA942C82CA`. Its own localhost server returned build 2,
the bound bot, and four managed guilds. Closing its native window terminated both PyInstaller parent and child
processes without forced termination.
