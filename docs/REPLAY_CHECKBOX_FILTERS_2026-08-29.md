# 2D Replay Checkbox Filters - 2026-08-29

## Delivered Behavior

- Replaced the event-target dropdown with one checkbox per match participant.
- Ordered target checkboxes by six tactical priorities: focus player, enemies who DBNO'd
  or killed the focus player, teammates, enemies who DBNO'd or killed teammates, other
  humans, and bots.
- Added `(기절)`, `(죽임)`, or `(기절·죽임)` beside priority-two and priority-four
  threat labels so the reason for the priority is visible without opening an event.
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
- Kept the complete replay dataset in memory while limiting the visible event-list DOM to a
  240-row time window around the current or selected event.
- Indexed participants, tracks, events, focus/team relationships, and threat sets; position
  interpolation and current-event lookup now use binary search instead of full-array scans.
- Limited transient combat rendering to the active eight-second window while preserving
  persistent DBNO, revive, kill, and death markers.

## Verified Semantics

- Default target selection: the focus player only.
- Default event-type selection: all nine types.
- No targets or no event types selected: zero displayed events.
- Multiple target and event-type selections use OR within each set and AND between the two sets.
- Layer visibility remains independent and still suppresses hidden ally, enemy, or bot events.
- Bots remain in priority six even when a bot is the recorded attacker.
- Discord nickname selection is not a static slash-command choice list or autocomplete list.
  Leaving the nickname blank opens a private current-guild picker with 25 registrations per
  page, previous/next controls, and a partial nickname/account-ID search modal. Registration
  count is therefore not capped by Discord's 25-option response limit.

## Related Analysis And Discord UX

- Weapon detail now separates `전체 성과`, `노 파츠·개별 파츠`, and `관측 파츠 조합`.
- Fight win rate uses durable kill/DBNO-caused wins and death/DBNO-taken losses at the
  attachment snapshot. No-parts means the attachment list was empty at that fight outcome,
  not that the weapon was unmodified for the whole match.
- Individual parts and exact multi-part combinations show fights, wins/losses, fight win
  rate, percentage-point delta from the no-parts baseline, chicken rate, outcome counts,
  average distance, and sample reliability.
- Discord recommendation output uses one concise field per weapon, two-weapon loadout,
  attachment combination, individual attachment, range, map, teammate, or drop zone. Reports
  show five fields per page with previous/next controls instead of one dense text block.

## Verification Evidence

- Full Python suite: 661 passed.
- Source UI and packaged EXE UI both passed the full Playwright audit.
- Deterministic browser fixture returned the exact groups
  `focus, focus_threat, ally, teammate_threat, human, bot` at priorities 1 through 6,
  including separate `(기절)`, `(죽임)`, and `(기절·죽임)` labels.
- Packaged live-match check: 98 participants produced 98 target checkboxes.
- Selecting all retained 16,066 tactical events while rendering only 240 event-list rows;
  the complete all-target refresh took 167 ms on the verification machine.
- Searching one participant reduced the target list to one and clearing search restored all
  98 checked targets.
- Playback advanced, the canvas was nonblank with 1,732 sampled colors, and combat events
  remained visible.
- Weapon detail exposed three views and 19 individual attachment bars for the verified M416 sample.
- All 33 workspace sections were nonblank and reachable.
- Desktop and 390px mobile checks had no page overflow, overlay, console error, request failure, or HTTP error.
- Discord picker tests used 40 current-guild registrations and verified page two, partial
  search, guild isolation, and account-ID selection without a 25-player registration cap.
- The deployed-command verifier passed for all 26 commands in each of four managed guilds;
  nickname fields were optional with no static choices or autocomplete, and their descriptions
  direct users to the private current-guild page/search picker.

## Packaged Artifact

- Release: `2026.08.29.3`
- Path: `dist/PUBG_AI_Manager.exe`
- Size: 51,838,606 bytes
- SHA-256: `D3F88EC9984922B4A850FF3742BFA7B38F2FE3701478938C3D465E6D6F48FC98`
- Runtime health: `ok`, localhost-only
