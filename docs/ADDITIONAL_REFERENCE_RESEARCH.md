# Additional Reference Research

Research date: 2026-06-28

This document records extra GitHub and blog references that were provided after the initial survey.

## smw0807/pubg_your.stat

Source: https://github.com/smw0807/pubg_your.stat

`pubg_your.stat` is a Korean Vue 3 + TypeScript stat search and team-finding site. It is useful as a UX and product
reference, but it is not a telemetry parser.

### Observed Architecture

- Frontend stack: Vite, Vue 3, TypeScript, Pinia, Element Plus, VueUse, vue-router, vue-timeago3, vue-zoomer.
- Backend/storage dependency: Firebase Auth and Firestore.
- PUBG calls are made from the app through an Axios wrapper.
- Search flow:
  - select platform and nickname
  - check Firestore cache first
  - if missing, call PUBG API
  - save result to Firestore
  - display cached result
  - allow manual refresh
- Firestore key style: `platform-nickname`.
- User convenience features:
  - search history in browser localStorage, limited to 10 entries
  - Steam/Kakao platform split
  - 404 player-not-found notification
  - 429 rate-limit notification
  - first-login platform nickname registration
  - team room creation, join, chat, member list, nickname copy
  - rank-team join message showing cached KDA, average damage, round count, and last update date

### Takeaways For This Project

- Keep the platform selector beside nickname registration. This matches our existing decision that nickname alone is
  not a stable identity.
- Add recent-search/history UX to the local management program and Discord autocomplete where practical.
- Show clear 404 and rate-limit messages. The local system should turn these into collector job states and Discord
  admin alerts instead of silent failures.
- Team/party features can benefit from simple UX details: nickname copy, mode filters, and cached stat previews for
  members.
- Firestore cache-first maps well to our local MySQL/raw-file design: read local DB first, then enqueue PUBG API
  fetch/update if stale or missing.
- Do not copy its client-side secret pattern. Vite exposes `VITE_*` values to the browser, while our PUBG key and
  Discord token must remain only in local `.env` and never be displayed by the UI.
- `pubg_your.stat` notes that season-normal stats are not enough for reliable deaths/KDA. Our telemetry and match
  parser should solve this by using `LogPlayerKillV2`, `LogPlayerMakeGroggy`, revive/redeploy events, final rank,
  and match/team context.

## songmin9813 PUBG Data Analysis Series

Sources:

- https://songmin9813.tistory.com/43
- https://songmin9813.tistory.com/44
- https://songmin9813.tistory.com/49
- https://songmin9813.tistory.com/50

This series documents a Python/Tableau PUBG data analysis project using PUBG Open API match data.

### Useful Findings

- The author highlights the 10 requests per minute API constraint and recommends spacing calls. This supports our
  existing need for a queue, chunking, retry, and backoff policy.
- The series used Samples API to collect match IDs and Match API to fetch match detail JSON. For our project, Samples
  API is useful for research/global baselines, but primary collection should still come from registered players.
- Raw JSON grows quickly. The series mentions roughly 5,700 match IDs producing hundreds of MB of raw JSON. This
  reinforces our decision to store raw match/telemetry files on a configurable external disk and not in Git.
- Match ID should be the durable join key between match metadata, rosters, participants, events, and aggregates.
- Participant extraction should avoid slow row-by-row concat patterns. Parse included objects into lists/records and
  bulk insert into MySQL.
- Useful high-level derived fields:
  - map
  - game mode
  - match duration
  - player stats
  - party/team play
  - item usage/pickup
  - death reason
  - phase of survival/death
  - AI/bot flag
- The series converts survival seconds into zone-phase buckets for easier visualization. We should prefer actual
  telemetry `LogGameStatePeriodic`/phase data where available, and use static phase schedules only as fallback.
- The author identified AI-like players by `playerId` patterns such as `ai.###`. We should record bot/AI detection as
  a parser field and keep the raw ID evidence.
- One-page reporting is useful. Our local dashboard should offer compact cards/sections for match summary, map, mode,
  kills/DBNO, damage, phase survival/death, weapons, and teammate context.

### Caveats

- Static blue-zone phase mapping can be wrong on maps or rulesets with dynamic blue zone behavior. Telemetry-derived
  phase/circle data is safer.
- The source project is broad public-match analysis. Our product is tracked-player analytics, so ingestion should not
  expand to all sample matches unless explicitly enabled for research.

## right1203 Pro Match Movement Analysis

Source: https://right1203.tistory.com/2

This article analyzes pro tournament movement patterns and is especially relevant to 2D replay/map snapshot work.

### Useful Findings

- Uses `chicken_dinner` to fetch tournament matches and telemetry.
- Separates match metadata, rosters, participants, and telemetry-derived movement facts.
- Uses telemetry helpers for:
  - `player_positions`
  - `circle_positions`
  - player names
  - attack logs
  - vehicle ride logs
- Important movement-derived metrics:
  - team attack counts by phase
  - first vehicle ride time/location
  - distance from player to safe-zone center by phase
  - distance from player to blue-zone border by phase
  - landing point
  - distance from plane route to landing point
  - season-wide landing distribution
- Map plotting details:
  - render map image with world coordinate extents
  - invert y-axis with `mapy - y` for image plotting
  - draw safe-zone circles
  - draw player movement paths after landing
  - draw plane line from early aircraft/player-position samples
  - compute perpendicular distance from plane line to landing point
  - use labels with outline strokes for readability
  - optionally use heat-density coloring for repeated vehicle/landing locations

### Takeaways For This Project

- Our `map_snapshot` artifact should include a route renderer that can draw the plane line, perpendicular drop
  distance, landing points, post-landing movement, kills/DBNOs/deaths, care packages, and phase circles.
- Store y-axis conversion rules per map asset so replay and static images agree.
- Split static map rendering into data extraction and rendering steps:
  - extraction creates normalized route/circle/fight/landing facts
  - renderer consumes those facts plus map asset metadata
- Do not assume pro movement patterns generalize to normal/ranked public games. Use this as a visualization and
  metric-design reference, not a recommendation model baseline.

## Changes To Existing Design

- Add `is_ai_or_bot` and `ai_detection_source` candidates to participant/player summary parsing.
- Add `phase_at_death`, `phase_at_dbno`, `phase_at_kill`, and `phase_at_landing` as derived fields when telemetry
  phase data exists.
- Add `plane_route`, `landing_distance_from_plane_m`, `first_vehicle_ride_at`, and `first_vehicle_ride_location` to
  future movement/location analysis.
- Keep dashboard/report design compact and comparison-friendly; one-page summaries are useful for Discord and local
  management views.
