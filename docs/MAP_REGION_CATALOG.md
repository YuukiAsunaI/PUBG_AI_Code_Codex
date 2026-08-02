# PUBG Map Region Catalog

Last updated: 2026-08-02 KST

## Purpose

`src/pubg_ai/map_regions.py` maps a PUBG telemetry coordinate to a stable region ID and Korean display name. It is
used at query time for drop-zone recommendations. Raw landing coordinates and the stable coordinate-cluster ID remain
the source facts and are never replaced by a label.

Current catalog version: `2024-10-28.api-assets-32b13b5.v1`

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
```

The localhost manager has a coordinate resolver. Recommendation rows and Discord recommendation replies show a
Korean region name when `matched`, while retaining the cluster ID and centroid coordinates in the report contract.

## Live Validation Snapshot

The retained `Yuuki_Asuna---` corpus produced 20 current drop clusters covering 35 landing matches. The catalog
matched 15 clusters and 30 landing matches, or 85.7 percent of the weighted sample. Examples included Stalber, Water
Town, Mylta, Primorsk, El Pozo, Stadium, Fong Tun, Race Track, Camp Alpha, Yong Cheon, Airport, Terminal, Ha Po, Ho
San, and Oh Hyang. The remaining points correctly retained grid labels instead of receiving a guessed name.

Desktop and 390 px mobile checks resolved Stalber and rendered named live recommendations without horizontal
overflow, console errors, or page errors.

## Update Procedure

1. Pin a reviewed commit from `pubg/api-assets` and verify every source image SHA-256.
2. Compare labeled assets with current PUBG patch notes and telemetry map names.
3. Update aliases, world extents, label centers, radii, and Korean names in `map_regions.py`.
4. Increment `MAP_REGION_CATALOG_VERSION`; never silently alter an existing version.
5. Add tests for aliases, representative live coordinates, unmatched water/open terrain, dynamic maps, and invalid
   coordinates.
6. Re-run live coverage. Review unmatched clusters manually; do not increase radii merely to maximize coverage.
7. Keep historical raw coordinates and cluster IDs unchanged. Region labels are reproducible query-time metadata.
