# Custom Map Region Release Audit - 2026-09-05

## Scope

This audit verifies the local map-region editor requested for correcting or refining PUBG place names. The feature is
intended for building-level callouts such as Sanhok Bootcamp left warehouse, right warehouse, north side, two-story
house, and the nearby three-warehouse compound. It applies to every bundled map and does not replace raw telemetry or
the maintained official region catalog.

## Delivered Behavior

The **2D Replay > Region Editor** workspace supports:

- a point with a configurable meter radius;
- a rectangle dragged directly on the map;
- a free polygon created from map clicks;
- zoom centered on the pointer, pan mode, reset, and undo;
- optional maintained-region reference outlines;
- editable Korean names, notes, enabled state, and integer priority;
- edit, disable, re-enable, and delete actions for saved regions.

Coordinates are stored as normalized map fractions so definitions remain stable across image resolutions. Invalid
self-intersecting polygons, zero-area polygons, non-finite values, and out-of-map coordinates are rejected. When
custom regions overlap, higher priority wins; equal-priority matches use the smaller area and then a stable ID.

Definitions are written atomically to the ignored `config/map_regions.local.json` file. Enabled custom regions take
precedence over maintained labels in landing analysis, recommendation drop summaries, circle labels, replay location
status and overlays, and the app-owned Discord bot. Raw match and telemetry files are unchanged.

## Automated Verification

- Full Python suite: 697 passed.
- Custom geometry and persistence tests: point/radius boundaries, polygon boundaries, disabled/map filtering,
  deterministic overlap selection, crossing-polygon rejection, atomic CRUD persistence, official-label override, and
  catalog composition passed.
- Web API tests: list, create, update, disable, delete, resolve, and validation-error paths passed with isolated local
  settings.
- JavaScript browser verification drew a three-vertex polygon, four-vertex dragged rectangle, and point/radius draft;
  rendered 21 maintained Sanhok reference regions; verified zoom and pan changed the SVG viewport; and selected all
  12 editor maps, each of which loaded a non-empty bundled map asset.
- Git whitespace validation passed.

## Packaged Application Verification

- Release: `2026.09.04.1`
- Artifact: `dist/PUBG_AI_Manager.exe`
- Size: 52,173,424 bytes
- SHA-256: `7A7FD710BB05B5377E25DBAA5B36145C84075A9B1FDD9FF21901F46528D4B785`
- Health: HTTP 200, `local_only=true`, bind host `127.0.0.1`, matching release
- Restart: clean restart on port 8000 in 22.63 seconds

Playwright verified all 33 workspaces at 1536px desktop and 390px mobile widths. The packaged run had no blank view,
blocking overlay, document or button overflow, console error, failed request, or HTTP error. The player-data audit
passed all 30 checks. The whole-match replay rendered 99 participants and 10,088 events while keeping 240 visible
event rows; selecting all participants refreshed in 181 ms and canvas pixel sampling confirmed a nonblank replay.

## Live Integrations

The app-managed Discord bot synchronized the exact configured command surface for all four guilds: 31, 31, 17, and 31
commands. The lower count is an intentional per-guild visibility configuration, not a missing deployment. Command
metadata checks passed for Korean descriptions, scalable player/match selection, autocomplete, and configurable
sample thresholds.

The real MySQL-backed Discord service audit for `Yuuki_Asuna---` passed comprehensive, KST time, drop-zone, map
comparison, M416 attachment, recommendation, and whole-match reports. The current coverage was 402 matches and 21
time buckets; the selected whole match contained 89 participants and 44,719 telemetry events.

## Repeatable Checks

```powershell
python -m pytest -q -p no:cacheprovider
python scripts/verify_discord_command_deployment.py --base-url http://127.0.0.1:8000 --project-dir .
python scripts/verify_discord_player_services.py --nickname Yuuki_Asuna--- --shard steam
```

Browser verification uses `scripts/verify_local_ui.js` against either the source server or the packaged app's local
URL. It intentionally draws editor drafts without saving a test region into the user's local catalog.
