# Replay Combat and Analysis Verification - 2026-08-22 KST

## Scope

This checkpoint covers the local-only 2D replay, tracked-team telemetry, KST time analysis, and player/weapon/map
comparison experience. Immutable raw match and telemetry payloads remain unchanged. Normalized movement rows and
derived replay timelines were rebuilt from the retained raw telemetry.

## Telemetry Contracts

The implementation follows the official PUBG telemetry event and object documentation:

- `LogPlayerAttack` provides attacker, attack type, weapon, attack ID, and weapon stack count. It does not provide a
  general aim orientation or ray endpoint. Every shot can therefore have an origin marker, but a missed-shot line
  must not be presented as measured direction.
- `LogPlayerTakeDamage` provides attacker and victim character objects. When both locations are present, the replay
  can draw a verified attacker-to-victim direction line.
- `LogPlayerPosition` provides character, optional vehicle, and elapsed match time. Movement mode is derived from
  the character state plus vehicle object, while `common.isGame == 0.1` is treated as transport-aircraft state and
  excluded from personal movement paths.
- PUBG world coordinates are centimeters. Stored replay points are normalized against the map world size and
  displayed in meters where distance is shown.

Sources:

- <https://documentation.pubg.com/en/telemetry-events.html>
- <https://documentation.pubg.com/en/telemetry-objects.html>

## Replay v9

`player-timeline-v9` is the minimum playable timeline format. It contains:

- Tracked-player and teammate position tracks on the same event clock.
- Per-sample airborne, vehicle, on-foot, and DBNO movement modes, plus vehicle type/ID when available.
- Team combat events for shots, caused/taken hits, caused/taken DBNOs, kills, deaths, finishes, and revives.
- Actor identity, registered-teammate emphasis, combat counterpart identity, weapon label, hit reason, headshot flag,
  distance, damage when the source event actually contains damage, and attack ID.
- Engagement clusters with start/end time, shot/hit/DBNO/kill/death counts, and outcome.
- Verified direction metadata only when both endpoints exist.
- Separate firearm-shot, throwable, melee, and generic attack actions. A `LogPlayerAttack` with an empty weapon item
  ID is retained only in raw telemetry and excluded from normalized shot counts.

The UI uses an actor-color underlay and a movement-mode inner line. Shot pulses, verified hit arrows, DBNO diamonds,
kill/death X markers, revive plus markers, and engagement rings use stable symbols and independent layer toggles.
All player and teammate event collections are normalized to one timeline origin and sorted by time, event index, and
stable sequence before playback.

## Analysis Views

KST time-of-day rows can be grouped by hour or broader day periods and filtered with the existing season, map,
mode, match type, custom, date, and time filters. They expose matches, chickens, wins, fight events, accuracy,
headshot share among confirmed hits, average damage, and explicit sample counts.

Comparison supports:

- Registered player versus registered player.
- Weapon versus weapon for one selected registered player.
- Map versus map for one selected registered player.
- Two to five selected subjects.
- Win rate, KDA, average kills, average DBNOs, average damage, accuracy, headshot share among hits, fight-win rate,
  and chicken rate.
- Chart and table views with the underlying match/hit/fight sample sizes visible.

## Live Data Verification

- MySQL schema version: 25.
- Movement parser version: 6.
- Replay renderer version: `player-timeline-v9`.
- Rebuilt timelines: 1,845.
- Replay rebuild failures: 0.
- Remaining stale timeline candidates after rebuild: 0.
- Focused replay/analysis regression tests: 21 passed after the final attack-classification change.
- Full project regression tests: 554 passed; 33 dependency deprecation warnings and no application assertion
  failures.
- Desktop and 430 px mobile browser checks found no document overflow, console errors, page errors, or failed API
  responses. The replay canvas was nonblank and event selection could reach late kill and DBNO events.

The retained source data remains the authority. A UI label such as `direction verified` means both event endpoints
were observed; absence of that label means direction was not inferred.
