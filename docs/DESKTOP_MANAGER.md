# Desktop Manager

## Architecture

The Windows desktop manager is a thin pywebview shell around the existing FastAPI UI. It does not duplicate player,
collection, analytics, permission, or replay logic. `pubg_ai.desktop` resolves a localhost endpoint, selects an
available port, starts a manager instance owned by that window, and opens that URL in a native window. It never
attaches a new executable to an older manager merely because `/health` returned a valid local response.

The browser-only `run-web` mode remains supported. Both modes use the same endpoints, local settings file, MySQL
database, Raw storage, Replay storage, workers, and tests.

The app uses `src/pubg_ai/assets/app_icon.png` for the manager header and favicon, and the multi-resolution
`src/pubg_ai/assets/app_icon.ico` for the pywebview window and packaged Windows executable. The icon is a text-free,
transparent helmet/data-shield adaptation of the operator-provided artwork so it remains legible at 16-32 px.

## Local Boundary

- Desktop endpoints accept only `127.0.0.1`, `localhost`, or `::1`.
- `0.0.0.0` and remote host names are rejected.
- An occupied preferred port is skipped. The launcher scans up to 100 local ports and starts its own current build on
  the first available one.
- The JavaScript bridge returns runtime metadata and folder-picker results only. It never returns PUBG or Discord
  secrets.
- Closing a window that started its own manager stops the Discord bot controller first, then the local web server.
  A normal zero exit is not surfaced as an error dialog.
- The top-right runtime badge exposes the active application release and the bridge tooltip exposes the selected
  localhost URL, making stale-window diagnosis explicit.

## Storage Selection

Desktop mode reveals native folder-picker buttons for Raw, Replay, deletion-backup, and quarantine storage. Selection
updates the form only; the existing Storage Settings API performs validation and persistence after Save. Browser mode
keeps the same text inputs and hides the native-only buttons.

## Player Analysis UX

- Profile, trend, weapon, recommendation, and match forms only accept locally registered tracking targets. A shared
  nickname datalist supports substring searching while the selected row resolves to its stored PUBG account ID.
- Selecting a player loads that player's weapon catalog and up to 5,000 completed matches. Weapon and match lookup use
  readable dropdown labels, and match search filters by date, map, mode, placement, kills, and match ID.
- Weapon and trend lookup support map, game/team mode, perspective, match type, season state, custom match, KST year,
  quarter, month, exact date, hour, and date-range filters. Trends can group by time, map, mode, perspective, match
  type, or season state.
- Recommendation output starts with a close/mid plus long-range two-weapon combination and keeps translated weapon and
  attachment names consistent. Raw PUBG codes remain visible only when the translation dictionary has no entry.
- Ranking uses Discord server names instead of requiring operators to remember guild IDs. The selector lists only
  servers with registered tracking targets, shows each registered-player count and configured ranking scope, and keeps
  an explicit all-server option. `서버 새로고침` synchronizes names from Discord without displaying or persisting the
  bot token.
- Discord permission and ranking-scope forms reuse the same server catalog. Server IDs remain visible as secondary
  reference text in the saved-scope table, while normal selection is name-based.
- Trend and weapon forms keep their common controls visible and place the less-frequent KST/date/season/custom-match
  controls under `상세 필터`. Results use metric blocks, translated chips, grouped sections, and mobile trend cards to
  avoid dense paragraph output and wide-table dependence.
- Default backup and quarantine directories are created during manager startup. Missing-path alerts resolve on the
  next status refresh while remaining available as resolved history records.

## Single Managed Discord Bot

The left navigation entry `Discord 봇` opens one bot workspace. `앱 봇 제어`, `명령·권한`, and `서버 관리`
are three views of the same manager-owned bot, not separate bots:

- PUBG API key and Discord bot token inputs are write-only. Saving writes only to the ignored project `.env`; status
  reports configured/missing and the browser never receives the saved value.
- Start, stop, command sync, auto-start, and hybrid-command prefix are available from the local manager.
- The bot user ID and its current guild memberships are bound to local settings. Every guild dropdown only exposes
  memberships of that managed identity; stale guild rows from a previous bot cannot re-enter through registrations.
- Each managed Discord server can inherit the full command catalog or expose an explicit command subset. Slash-command
  registration and prefix invocation use the same per-guild gate.
- Existing player, ranking, replay, permission, server-scope, alert, and worker features remain on this one bot.
- `서버 목록 정리` requires an operator confirmation before pruning stale guild catalog rows and guild-specific
  settings. Player analytics and global permissions are preserved.
- The standalone `run-discord-bot` CLI path is intentionally removed to prevent a duplicate runtime.
- The manager stays bound to localhost; bot control does not create a remote administration endpoint.

## Display Settings

The grouped, Korean-unit, and plain number modes are stored in the shared local settings file rather than only in
browser `localStorage`. A selected mode therefore survives reloads, packaged-app restarts, and automatic fallback from
an occupied preferred port to another localhost port. The browser workflow verifies the preview and real player
analysis values; Korean-unit mode fails verification if any visible grouped value of 10,000 or greater remains without
a Korean large-number unit.

## Frequent Flight Paths

The Replay workspace includes a separate `비행기 동선` view backed by `match_plane_routes` and completed match
metadata. It supports registered-player scope plus map, mode, perspective, match type, season, custom-match, KST
year/quarter/month/date/hour/range, angle-bin, offset-bin, result-limit, and source-route-limit filters.

Observed aircraft samples are extended to the map boundaries before rendering. Physical routes are clustered by
undirected angle and signed distance from the map center, while the dominant actual travel direction is retained for
the arrow. Opposite travel directions can therefore belong to one physical route. The angle-wrap calculation uses
the selected bin axis, preventing near-horizontal 0/180-degree routes from splitting only because the canonical
angle crossed its representation boundary.

The result provides a per-map frequency overlay, ranked routes, map share, dominant-direction confidence, observation
length/sample evidence, first/last KST occurrence, and recent source matches. It is post-match analysis and is not
presented as a prediction of the next plane route.

## Launch

```powershell
python -m pip install -e ".[desktop]"
python -m pubg_ai.cli run-desktop --maximized
```

The repository-root `run_desktop.cmd` provides a double-click launcher after installation. A repository checkout
always launches its current `src` tree first; the packaged executable is only a fallback when Python is unavailable
or the source tree is absent. A source startup error is reported instead of being hidden by an older executable. This
prevents a stale `dist` build from hiding newer manager changes. The configured
`local_web_base_url` supplies the preferred desktop port, with `8000` used when no local URL is configured. If that
port is occupied, the launcher selects the next available localhost port and displays it in the runtime badge tooltip.

## Windows Executable

```powershell
python -m pip install -e ".[desktop-build]"
python -m PyInstaller --clean --noconfirm pubg_ai_desktop.spec
```

This creates `dist/PUBG_AI_Manager.exe` without a console window and embeds `assets/app_icon.ico`. The packaged
entrypoint searches upward from the executable and current directory for the project `.env` plus `config` directory.
Set `PUBG_AI_BASE_DIR` when the executable is stored elsewhere. Secrets and local settings are not embedded into the
executable.
