# Flight Path and Circle Analysis

Last verified: 2026-09-03 KST

## Purpose

The local manager provides a completed-match map analysis that answers three different questions without mixing their
denominators:

- Which physical aircraft routes occur most often?
- Where does each numbered safe circle occur most often?
- For matches in one selected aircraft-route cluster, which circle patterns occurred?

The UI exposes these as `비행기 동선`, `자기장 서클`, and `동선 + 자기장`. The query form can limit the
server-side aggregation to one phase from 1 through 9. A separate map display selector changes only the visible phase
inside an already returned report, so the operator can compare phases without another database query.

## Official Evidence

The implementation follows PUBG's official telemetry contract:

- [Telemetry objects](https://github.com/pubg/api-documentation-content/blob/master/rst/telemetry-objects.rst)
  documents `common.isGame`, `poisonGasWarningPosition`, and `poisonGasWarningRadius`.
- PUBG telemetry coordinates use centimeters and the map origin is the top-left corner.
- Integer `common.isGame` values identify the newly announced target safe zone: `1.0` is the first circle,
  `2.0` is the second circle, and so on. Half-step values describe a shrinking phase and are not counted as a
  separate numbered target circle.
- [Current official Erangel page](https://www.pubg.com/en/game-info/maps/erangel) is the source of the bundled
  remastered Erangel image.
- [Official PUBG API map assets](https://github.com/pubg/api-assets/tree/master/Assets/Maps) remain the source for
  other supported map images.

## Extraction Contract

### Aircraft routes

1. Read completed-match plane samples from `match_plane_routes`.
2. Fit the observed route and extend it to the map boundaries.
3. Normalize opposite directions into the same physical line while retaining the dominant travel direction.
4. Cluster by angle and signed distance from map center.
5. Keep the exact match-ID membership for every displayed route cluster.

### Safe circles

1. Read position samples whose `common_is_game` value is an integer from 1 through 9.
2. Require positive `poison_gas_warning_x`, `poison_gas_warning_y`, and radius values.
3. For each match and numbered phase, use the earliest matching event to avoid counting the same announced circle
   repeatedly.
4. Cluster by center X/Y bins and radius bins. The default bins are 500 m for the center and 250 m for the radius.
5. Calculate frequency and share within the same map and phase. Phases never share a denominator.

In combined mode, selecting a route calls the circle analysis with that route cluster ID. The server reconstructs the
route cluster with the same parameters and limits circle rows to its exact match membership. It does not approximate
the relationship from nearby coordinates.

## Map Reading and Clutter Controls

- A circle report defaults to one visible phase. `전체 단계 겹쳐보기` is an explicit opt-in rather than the default.
- Every visible cluster has the same rank on the map and in the side list. A single-phase map uses `#1` through
  `#N`; an all-phase map uses `phase.rank` labels.
- Clicking a late-circle marker or rank row focuses a square viewport around that circle. The minimum viewport shows
  15% of the map width, preserving nearby roads and place labels while making small circles legible. `전체 보기` is
  available beside the frequency heading as well as on the map. It restores the full viewport, clears the selected
  rank and dimmed context markers, and keeps the existing query result in memory.
- Combined mode draws only the selected aircraft route by default. `다른 항로 겹쳐보기` adds the remaining ranked
  routes as low-emphasis context.
- Circle centers inside a maintained map-label radius use that Korean place name. Centers outside every radius are
  described as the nearest place name plus cardinal direction and exact distance, for example
  `파이난 북동쪽 387m`. This is deliberately labelled as the nearest-place basis and is not presented as an official
  administrative boundary.
- The map and analytical layers share one SVG viewport. Zooming therefore cannot separate the background image from
  its circles or routes.

## Filters and Limits

Both analyses support shard, registered player, map, game mode, team mode, perspective, match type, season state,
custom-match state, year, quarter, month, exact KST date, KST hour, and KST date range.

Every observed aircraft-route and circle cluster is eligible by default, including clusters seen exactly once.
Operators can set independent `비행기 동선 최소 빈도` and `자기장 최소 빈도` values, both defaulting to one.
Both inputs are in the always-visible primary query area; opening the advanced-filter disclosure is not required.
The optional per-map/per-phase display limits use zero for all eligible clusters; a positive value applies a final
ranked display cap without changing the frequency denominator. Source-row limits remain available for expensive
queries.

## Current Erangel Asset

- Repository path: `src/pubg_ai/assets/maps/Erangel_Remastered_2026_Official.webp`
- Dimensions: 1008 x 1008
- SHA-256: `e22200f9ab1d97bfd190a2ed6cdc01461fa77873f5e7a1c75da30efd24058702`
- Packaged with both the Python wheel and `PUBG_AI_Manager.exe`
- Materialized into the replay cache for offline local use

Changing a map image changes static replay output. The renderer version was therefore raised to
`map-snapshot-v6`; raw match and telemetry files remain untouched.

## Verification Record

On 2026-09-02 KST:

- all 2,711 eligible static replay snapshots were regenerated as `map-snapshot-v6`;
- snapshot generation reported 0 failures and 0 missing-position skips;
- the player-intelligence data-quality audit passed all 30 checks with 2,711 expected and 2,711 current snapshots;
- Playwright loaded the Erangel asset at 1008 x 1008 and verified combined, route-only, circle-only, and phase-1-only
  modes;
- combined-mode route conditioning produced both route and circle SVG layers;
- combined mode rendered one selected route by default and expanded to five only after the comparison toggle;
- the Sanhok phase-7 check rendered eight ranked circles, eight Korean location descriptions, and focused the selected
  circle from the full `0 0 1000 1000` viewport to a `150 x 150` viewport;
- a 390 x 844 mobile viewport kept the 338 x 338 map, toolbar, and zoom controls inside the viewport;
- desktop and mobile checks reported no console errors, request failures, HTTP errors, or horizontal overflow.
- all 685 Python tests passed;
- packaged release `2026.09.02.2` rendered all 33 workspaces and returned the same localhost-only health release;
- `dist/PUBG_AI_Manager.exe` was 52,103,250 bytes with SHA-256
  `4C99E4886735F69EEEA22A48A69161DAA21FBBAB562441D5FADD5BA28F508E06`;
- closing the packaged native window removed both PyInstaller processes and the local listener, while a real Discord
  start/stop cycle emitted no unclosed-session or pending-task warning.

The browser QA is implemented in `scripts/verify_local_ui.js`, and service-level coverage is in
`tests/test_circle_stats.py`, `tests/test_flight_path_stats.py`, and `tests/test_web_flight_path_stats.py`.

The 2026-09-03 browser regression no longer names one Sanhok fixture. It obtains every map that has stored phase-7
circle data and validates each map in turn. The current MySQL corpus covered Deston, Rondo, Miramar, Vikendi, Sanhok,
remastered Erangel, Karakin, Taego, and Paramo. Every map loaded its image, rendered the same number of ranked rows and
markers, produced a non-empty Korean location context, and focused the selected small circle into a 150 x 150 viewport.

The same regression was rerun against packaged release `2026.09.03.1`, not only the source server. All nine maps
passed again, while the combined view retained one selected flight route and eight phase-filtered circle rows. The
desktop and 390px mobile passes had no console errors, request failures, HTTP errors, or horizontal overflow. The
52,142,035-byte executable has SHA-256
`AEB8847FF452F1FEABDC15739A4F6DCC00243DB44A681CEA4EBA5440D1AD7A45`.

On 2026-09-05 KST, packaged release `2026.09.05.2` passed the full browser regression across all 33 workspaces on
desktop and mobile. Both minimum-frequency controls rendered in the primary query area while advanced filters were
closed, both defaulted to one, and both display caps rendered as zero for unlimited. The route-only and circle-only
tabs each retained the relevant minimum input and hid the unrelated one. All nine maps with phase-7 data passed
focus-to-overview restoration without losing any ranked rows. The current Sanhok corpus returned 63 route clusters
at minimum one and 56 at minimum two; it returned
40 phase-7 circle clusters at minimum one and 37 at minimum two. Neither filtered response contained a cluster below
the requested threshold. The packaged pass reported no console errors, failed requests, HTTP errors, or horizontal
overflow.
