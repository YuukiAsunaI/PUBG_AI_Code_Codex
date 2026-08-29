# 2D Replay Checkbox Filters - 2026-08-29

## Delivered Behavior

- Replaced the event-target dropdown with one checkbox per match participant.
- Ordered target checkboxes by six tactical priorities: focus player, teammates, enemies
  who DBNO'd or killed the focus player, enemies who DBNO'd or killed teammates,
  other humans, and bots.
- Displayed the priority number and tactical category below every participant name.
- Derived threat groups from mirrored caused/taken DBNO, kill, finish, death, and
  finished-taken records while excluding self and environmental damage.
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
- Bots remain in priority six even when a bot is the recorded attacker.
- Discord nickname autocomplete is dynamic rather than a static choice list. In a guild it
  queries only that guild's active player registrations, applies partial-name/account-ID
  search before the 25-result Discord response limit, and therefore remains searchable
  when a guild has more than 25 registered players.

## Verification Evidence

- Full Python suite: 660 passed.
- Source UI and packaged EXE UI both passed the full Playwright audit.
- Deterministic browser fixture returned the exact groups
  `focus, ally, focus_threat, teammate_threat, human, bot` at priorities 1 through 6.
- Live stored-match check: 100 participants produced 100 target checkboxes.
- Searching one participant reduced the target list to one and clearing search restored all 100 checked targets.
- Combined DBNO plus kill/death filter: 533 displayed events with no unrelated event type.
- Full filter: 14,545 tactical events.
- All 33 workspace sections were nonblank and reachable.
- Desktop and 390px mobile checks had no page overflow, overlay, console error, request failure, or HTTP error.
- Discord autocomplete test used 40 registered players: the unfiltered response stopped at
  25 and typing `player37` returned `KiPlayer37`.
- The deployed-command verifier passed for all 26 commands in each of four managed guilds;
  nickname fields were deployed as autocomplete fields with no static choices and the
  current-guild/25-result/search explanation.

## Packaged Artifact

- Release: `2026.08.29.2`
- Path: `dist/PUBG_AI_Manager.exe`
- Size: 51,820,093 bytes
- SHA-256: `0DAC80D91795A46A9613F96B57B35C73382A413B6ABACF75C3D040B87576E2CB`
- Runtime health: `ok`, localhost-only
