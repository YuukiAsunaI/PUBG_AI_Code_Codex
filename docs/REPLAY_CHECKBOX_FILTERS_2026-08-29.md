# 2D Replay Checkbox Filters - 2026-08-29

## Delivered Behavior

- Replaced the event-target dropdown with one checkbox per match participant.
- Added `focus only`, `select all`, and `clear all` target actions.
- Kept participant-name search and preserved checked targets while the search hides other rows.
- Replaced the event-type dropdown with nine independent checkboxes.
- Added event-type `select all`, `clear all`, and a full filter reset.
- Kept environment damage overlapping with DBNO or death where the same telemetry event has both meanings.
- Applied the same filters to the event list and major-location shortcuts.
- Restricted replay auto-follow scrolling to the event list instead of moving the whole management page.

## Verified Semantics

- Default target selection: the focus player only.
- Default event-type selection: all nine types.
- No targets or no event types selected: zero displayed events.
- Multiple target and event-type selections use OR within each set and AND between the two sets.
- Layer visibility remains independent and still suppresses hidden ally, enemy, or bot events.

## Verification Evidence

- Full Python suite: 660 passed.
- Source UI and packaged EXE UI both passed the full Playwright audit.
- Live stored-match check: 100 participants produced 100 target checkboxes.
- Searching one participant reduced the target list to one and clearing search restored all 100 checked targets.
- Combined DBNO plus kill/death filter: 533 displayed events with no unrelated event type.
- Full filter: 14,545 tactical events.
- All 33 workspace sections were nonblank and reachable.
- Desktop and 390px mobile checks had no page overflow, overlay, console error, request failure, or HTTP error.

## Packaged Artifact

- Release: `2026.08.29.1`
- Path: `dist/PUBG_AI_Manager.exe`
- Size: 51,819,459 bytes
- SHA-256: `5E036D470F9379AE0374571BDEF1EFEE332C426922EA76A22411290036BEE184`
- Runtime health: `ok`, localhost-only
