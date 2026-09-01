# Flight Path and Circle Analysis

Last verified: 2026-09-02 KST

## Purpose

The local manager provides a completed-match map analysis that answers three different questions without mixing their
denominators:

- Which physical aircraft routes occur most often?
- Where does each numbered safe circle occur most often?
- For matches in one selected aircraft-route cluster, which circle patterns occurred?

The UI exposes these as `비행기 동선`, `자기장 서클`, and `동선 + 자기장`. Circle phases can be viewed together
or individually from phase 1 through phase 9.

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

## Filters and Limits

Both analyses support shard, registered player, map, game mode, team mode, perspective, match type, season state,
custom-match state, year, quarter, month, exact KST date, KST hour, and KST date range.

The default UI renders at most eight circle clusters per phase. Operators can choose 1-25 clusters per phase and
100-200,000 source circle rows. Bounding the rendered SVG nodes keeps the combined map responsive even when the
retained telemetry corpus grows.

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
- desktop and mobile checks reported no console errors, request failures, HTTP errors, or horizontal overflow.
- all 685 Python tests passed;
- packaged release `2026.09.02.1` rendered all 33 workspaces and returned the same localhost-only health release;
- `dist/PUBG_AI_Manager.exe` was 52,099,316 bytes with SHA-256
  `65FE614900B626024C5F4A8CE0E2FA561A5050B7C6FA759F669477901A69C83B`;
- closing the packaged native window removed both PyInstaller processes and the local listener, while a real Discord
  start/stop cycle emitted no unclosed-session or pending-task warning.

The browser QA is implemented in `scripts/verify_local_ui.js`, and service-level coverage is in
`tests/test_circle_stats.py`, `tests/test_flight_path_stats.py`, and `tests/test_web_flight_path_stats.py`.
