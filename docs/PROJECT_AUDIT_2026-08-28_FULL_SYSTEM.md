# Full Project Audit - 2026-08-28

## Scope

This audit treated the repository as an unfamiliar production application and reviewed the complete local workflow:

- PUBG player registration, match discovery, match/telemetry storage, and policy exclusions;
- combat, activity, item, movement, fight-outcome, loadout, map-snapshot, and replay-timeline post-processing;
- player analysis, recommendations, trends, comparisons, rankings, landing regions, match detail, and flight paths;
- app-managed Discord bot commands, autocomplete, descriptions, pagination, permissions, and four-guild deployment;
- localhost security boundary, MySQL materializations, desktop packaging, and desktop/mobile UI behavior.

The authoritative behavior references were the
[PUBG telemetry flow](https://documentation.pubg.com/en/telemetry.html),
[PUBG telemetry event reference](https://documentation.pubg.com/en/telemetry-events.html),
[official PUBG API Assets dictionaries](https://github.com/pubg/api-assets/tree/master/dictionaries/telemetry), and
[Discord application-command option rules](https://docs.discord.com/developers/interactions/application-commands).

## Findings And Fixes

### Data quality and policy scope

The earlier audit checked only part of the normalized model and mixed retained custom/training history into some
diagnostic totals. The audit now uses `analysis_matches` consistently while preserving excluded raw data. Telemetry
saved in the most recent 15 minutes is shown as processing-grace data instead of causing a false failure while the
post-processing worker catches up.

The audit now performs 27 checks covering:

- current combat, activity, item, movement, and fight-outcome parser coverage at match and match-player grain;
- combat hit decomposition, vehicle-hit separation, headshot bounds, nonnegative measures, and state reconciliation;
- activity events, healing decomposition, and event-catalog reconciliation;
- every item pickup/source/drop/use/equip/unequip/attach/detach aggregate against normalized item events;
- movement and fight-outcome state/output reconciliation;
- current 2D map/timeline coverage only where a real ground path is renderable;
- current analysis weapon/item translation coverage.

The local manager now shows mature scope, policy exclusions, grace rows, latest analysis match, latest telemetry save,
current parser versions, rendering exclusions, check evidence, and translation coverage in one readable panel.

### Parser and translation compatibility

- `LogHeal` accepts both the official reference spelling `healamount` and the camel-case `healAmount` found in current
  payloads.
- `WeapJuliesKar98k_C` resolves to `Kar98k` using the official damage-causer dictionary.
- `Item_Weapon_CamoNet_Desert_C` resolves to the current Korean label `차량 위장망`.
- Unknown codes still fall back to their raw code, while the audit now makes every newly observed unknown visible.

### Failure observability

Map snapshots and replay timelines previously reported only a failed count. The combat, activity, item, movement,
loadout, fight-outcome, map, and timeline stages all now return match-scoped diagnostic records containing the
exception type and message. Worker history and alerts include the first concrete cause instead of only a number.
User-visible stage and failure-count labels are Korean; technical exception types remain intact for debugging.

### Discord usability and completeness

Several Discord formatters silently re-sliced already bounded service results. Trends showed only six buckets and
recommendations/details hid later candidates. Those extra cuts and four unused parsing helpers were removed. The
existing embed paginator remains responsible for Discord message limits, so requested results are paged rather than
discarded.

Live Discord API verification confirmed all four managed guilds expose 26 commands each. Focus commands have complete
Korean descriptions, dynamic autocomplete instead of oversized static user lists, no opaque `옵션` field, optional
recent-match selection, and configurable recommendation sample size up to 100,000 matches.

### Desktop release consistency

The release module contained an unused label from a different build date. The dead label was removed and the single
release source is now `2026.08.28.1`. The current executable was rebuilt from this exact workspace.

## Live Data Evidence

The read-only audit ran against local MySQL 8 and schema version 33:

| Measure | Result |
| --- | ---: |
| Stored raw telemetry matches | 1,968 |
| Analysis matches | 1,952 |
| Preserved policy-excluded matches | 16 |
| Mature match-player pairs | 2,347 |
| Renderable 2D match-player pairs | 2,340 |
| Current map snapshots | 2,340 |
| Current replay timelines | 2,340 |
| Non-renderable ground paths | 7 |
| Known weapon codes | 55 / 55 |
| Known item codes | 174 / 174 |
| Failed audit checks | 0 / 27 |

The seven non-renderable rows contain no usable in-game ground path; they are reported separately instead of being
misclassified as renderer failures. The current event catalog contains 49 observed types, 42 normalized types, seven
known raw-only types, and zero unclassified types.

## Verification

- `python -m pytest -p no:cacheprovider`: **658 passed**.
- `python -m compileall -q src tests`: passed.
- `node --check scripts/verify_local_ui.js`: passed.
- `git diff --check`: passed with only the repository's existing Windows LF/CRLF notices.
- live MySQL player-intelligence audit: **27/27 passed**.
- Playwright source and packaged-app runs: all 33 workspace states rendered content; desktop and mobile returned 200;
  blank pages, overlays, console errors, request failures, HTTP errors, horizontal overflow, and overflowing buttons
  were all zero.
- live Discord deployment: four guilds, 26 commands per guild, all metadata quality checks passed.

The packaged artifact is `dist/PUBG_AI_Manager.exe`:

- size: 51,814,299 bytes;
- SHA-256: `4BBF85F499AF7AB2AB33A9D14E437CBAEAE7F4CA30938086170C8390398FA5FA`;
- health: `ok`, localhost-only `true`, release `2026.08.28.1`;
- app-managed Discord bot: running and ready for four guilds.

## Residual Risks

1. FastAPI's installed TestClient emits one Starlette/httpx adapter deprecation warning. Runtime tests pass; refresh the
   dependency set when the supported replacement is stable.
2. `discord.py` uses `asyncio.iscoroutinefunction`, deprecated for removal in Python 3.16. The current Python 3.14
   runtime passes; dependency compatibility must be checked before a Python 3.16 upgrade.
3. PyInstaller emits non-applicable Android collection and Pydantic-v1-on-Python-3.14 warnings. The Windows package
   builds and runs, but a pinned isolated build environment remains the best route to reproducible releases.
4. `web/app.py` and `discord_bot.py` remain large modules. No duplicate definitions were found and dead helpers touched
   by this audit were removed. Future extraction should be behavior-preserving and covered by the existing full UI and
   Discord deployment checks rather than performed as a broad cosmetic refactor.
5. The Codex image viewer cannot open screenshots under this Windows Korean-user ACL. Playwright still verified image
   loading, dimensions, layout, UI state, errors, and screenshots in both source and packaged runs.
