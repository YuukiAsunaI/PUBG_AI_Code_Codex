# PUBG Map Region Catalog

Last updated: 2026-09-04 KST

## Purpose

`src/pubg_ai/map_regions.py` maps a PUBG telemetry coordinate to a stable region ID and Korean display name. It is
used at query time for drop-zone recommendations. Locally drawn operator regions can refine names down to compounds
or buildings without modifying the official catalog. Raw landing coordinates and the stable coordinate-cluster ID
remain the source facts and are never replaced by a label.

Current catalog version: `2024-10-28.api-assets-32b13b5.v2`

## Authoritative Inputs

- PUBG telemetry coordinate contract: <https://documentation.pubg.com/en/telemetry-objects.html>
- Official PUBG API asset repository: <https://github.com/pubg/api-assets>
- Pinned asset commit: `32b13b51128b8d8909ae5e77f3b833e01230b24d`
- Official labeled map assets: <https://github.com/pubg/api-assets/tree/32b13b51128b8d8909ae5e77f3b833e01230b24d/Assets/Maps>

PUBG telemetry uses centimeters with `(0, 0)` at the top-left of the map. `MAP_WORLD_SIZE_CM` supplies the map extent
used to normalize `x_cm` and `y_cm` to `[0, 1]` coordinates.

The official assets provide map images and printed place labels, but not machine-readable region-boundary polygons.
This project therefore maintains circles around those label centers. The circle geometry is a documented project
interpretation, not an official PUBG boundary definition.

Each source image has a pinned SHA-256 in the catalog. A catalog update must deliberately change the source commit,
asset hash, geometry, and catalog version together.

## Currentness Review And Taego Correction

The pinned API Assets commit is PUBG's `31.2 Map image update` from 2024-10-28. Update 31.2 changed Taego's
`Studio` area to `Hospital` and published the corresponding world-map/minimap update. Later official Taego notes
reviewed through Updates 35.2, 41.1, and 42.2 describe terrain or gameplay changes but do not document a newer Taego
place-name layout. The conclusion that the pinned image is still the latest authoritative API asset is therefore a
cross-source inference, not a claim that PUBG guarantees frozen boundaries.

Reviewed sources:

- Official API Assets history: <https://github.com/pubg/api-assets/commits>
- Official Update 31.2 map revision: <https://pubg.com/en/news/7624?category=patch_notes>
- Official Update 35.2 Taego changes: <https://pubg.com/en/news/8616?category=patch_notes>
- Official Update 41.1 Taego changes: <https://pubg.com/en/news/9926>
- Official Update 42.2: <https://www.pubg.com/en/news/10459>

Taego `Market` and `Terminal` are now separate reviewed regions. Update 14.2 explicitly names `Market`, and the
current labeled map plus a Korean location cross-check place the traditional market west of the bus terminal. The
old broad Terminal circle incorrectly absorbed part of that market.

| Stable region | Korean label | Center `(x, y)` | Radius | Review |
| --- | --- | --- | --- | --- |
| `taego.market` | 시장 | `(53.5%, 44.5%)` | `3.2%` | high confidence, 2026-08-24 |
| `taego.terminal` | 터미널 | `(60.0%, 44.5%)` | `4.3%` | high confidence, 2026-08-24 |

Supporting location evidence:

- Official Update 14.2 Market reference: <https://pubg.com/en/news/1732?category=patch_notes>
- Terminal/market location cross-check: <https://www.inven.co.kr/webzine/news/?news=259109&site=battlegrounds>

The review status, note, confidence, and source links are embedded in the catalog records returned by
`GET /map-regions?map_name=Tiger_Main`. Other circles remain maintained approximations around printed label centers;
they are not silently promoted to reviewed status.

## Supported Map Names

| PUBG API name | Canonical asset map | Policy |
| --- | --- | --- |
| `Baltic_Main`, `Erangel_Main` | `Erangel_Main` | static regions |
| `Desert_Main` | `Miramar_Main` | static regions |
| `DihorOtok_Main` | `Vikendi_Main` | static regions |
| `Savage_Main` | `Sanhok_Main` | static regions |
| `Summerland_Main` | `Karakin_Main` | static regions |
| `Tiger_Main` | `Taego_Main` | static regions |
| `Chimera_Main` | `Paramo_Main` | dynamic map |
| `Neon_Main` | `Rondo_Main` | static regions |
| `Range_Main` | `Camp_Jackal_Main` | static regions |
| `Kiki_Main` | `Deston_Main` | static regions |
| `Heaven_Main` | `Haven_Main` | static regions |

Paramo rearranges named areas between sessions, so a fixed coordinate label would be misleading. It returns
`dynamic_map` and keeps the coordinate-cluster fallback.

## Resolution Contract

`resolve_map_region(map_name, x_cm, y_cm)` returns one of:

| Status | Meaning | Display behavior |
| --- | --- | --- |
| `matched` | The point is inside a maintained region circle | Show the Korean region name |
| `unmatched` | Supported static map, but no circle contains the point | Show map plus grid cluster |
| `dynamic_map` | Fixed location names are unsafe for this map | Show map plus dynamic-map grid |
| `unsupported_map` | No catalog entry or world extent exists | Show the original map/grid values |
| `invalid_coordinate` | Coordinate is non-finite or outside the map | Keep finite values, return null for non-finite input, and do not label |

Overlapping circles are resolved by the smallest normalized distance to the center, then absolute distance, then
stable region ID. An unmatched point is never forced to the nearest place name.

Every result exposes the raw centimeter coordinates, normalized coordinates when valid, catalog version, source
commit, source asset URL and SHA-256, geometry type, region ID, and distance/radius metadata.

## Local Region Overrides

The localhost manager stores operator-defined regions in `config/map_regions.local.json`. This path matches the
repository's `config/*.local.json` ignore rule and is never embedded in the packaged executable. Writes use the same
process/thread lock and atomic replace behavior as other local settings.

Supported shapes:

| Editor shape | Stored geometry | Selection |
| --- | --- | --- |
| Point and radius | `point_radius` | center plus radius in normalized map coordinates |
| Rectangle | `polygon` | four corners generated by drag |
| Polygon | `polygon` | 3-64 ordered vertices |

Coordinates are saved as normalized `[0, 1]` values, while point radii are entered and displayed in meters using
the selected map's `MAP_WORLD_SIZE_CM`. This keeps shapes aligned when a bundled map image changes resolution.
Self-intersecting and zero-area polygons, non-finite coordinates, out-of-map points, and invalid radii are rejected.

Resolution order is deterministic:

1. Enabled custom regions containing the coordinate.
2. Higher custom `priority`.
3. Smaller custom area when priorities tie.
4. Stable custom region ID when both priority and area tie.
5. Official catalog circle resolution when no custom region matches.
6. Existing dynamic/unmatched/unsupported fallback.

Disabling a custom region preserves it but removes it from analysis. Deleting requires confirmation in the manager.
The official catalog and immutable raw coordinates are never edited. The custom file should be included in the
operator's normal configuration backup if those local names must survive a disk migration.

## Product Entry Points

```powershell
python -m pubg_ai.cli map-region Baltic_Main 575857 134391
python -m pubg_ai.cli map-region-catalog --map-name Baltic_Main
```

Local API:

```text
GET /map-regions
GET /map-regions?map_name=Baltic_Main
GET /map-regions/resolve?map_name=Baltic_Main&x_cm=575857&y_cm=134391
GET /map-regions/custom?map_name=Baltic_Main
POST /map-regions/custom
PUT /map-regions/custom/{region_id}
DELETE /map-regions/custom/{region_id}
```

The localhost manager has a zoomable region editor. Enabled custom names are used by landing rows, recommendation
rows, Discord recommendation/landing replies, flight-path circle labels, and the 2D replay region overlay and current
position label. Reports retain the cluster ID and centroid coordinates in their contracts.

## Live Validation Snapshot

The retained corpus was rechecked on 2026-08-24 through the local landing view. The current result rendered 134
named/fallback region rows and 20 map markers for the selected player without raw `Item_` labels, page overflow,
console errors, or failed requests. Desktop and 390 px mobile layouts both passed. The fallback grid is now 20 x 20,
so unmatched open terrain remains spatially useful without being assigned a guessed place name.

Unit coverage verifies the Taego market point resolves to `taego.market`, the terminal point resolves to
`taego.terminal`, and the two circles remain distinct. It also covers aliases, dynamic Paramo behavior, unsupported
maps, invalid coordinates, source hashes, custom point/polygon boundaries, crossing-polygon rejection, atomic CRUD,
custom-over-official precedence, and deterministic overlap resolution. Chromium QA draws all three editor shapes,
loads official references, zooms and pans the Sanhok map, and checks for console, request, and layout failures.

## Update Procedure

1. Pin a reviewed commit from `pubg/api-assets` and verify every source image SHA-256.
2. Compare labeled assets with current PUBG patch notes and telemetry map names.
3. Record the source URL, review date, confidence, classification, parent district, and Korean review note for every
   corrected region. A community guide may cross-check location, but cannot replace an official map or patch note.
4. Update aliases, world extents, label centers, radii, and Korean names in `map_regions.py`.
5. Increment `MAP_REGION_CATALOG_VERSION`; never silently alter an existing version.
6. Add tests for aliases, representative live coordinates, unmatched water/open terrain, dynamic maps, and invalid
   coordinates.
7. Re-run live coverage and desktop/mobile map checks. Review unmatched clusters manually; do not increase radii
   merely to maximize coverage.
8. Keep historical raw coordinates and cluster IDs unchanged. Region labels are reproducible query-time metadata.
