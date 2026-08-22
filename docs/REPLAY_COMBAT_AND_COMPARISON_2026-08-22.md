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
  character and vehicle state. `common.isGame == 0.1` is not sufficient by itself to identify transport-aircraft
  samples because it can remain present after a player jumps; the path policy combines the plane event-index window,
  vehicle state, and altitude.
- PUBG world coordinates are centimeters. Stored replay points are normalized against the map world size and
  displayed in meters where distance is shown.

Sources:

- <https://documentation.pubg.com/en/telemetry-events.html>
- <https://documentation.pubg.com/en/telemetry-objects.html>

Accessibility references for the event legend:

- <https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html>
- <https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html>

## Replay v13

`player-timeline-v13` is the minimum playable timeline format. Older content-addressed artifacts remain preserved but
are not offered as playable timelines. The format contains:

- Tracked-player and teammate position tracks on the same event clock.
- Per-sample airborne, vehicle, on-foot, and DBNO movement modes, plus vehicle type/ID when available.
- Team combat events for shots, caused/taken hits, caused/taken DBNOs, kills, deaths, finishes, and revives.
- Actor identity, registered-teammate emphasis, combat counterpart identity, weapon label, hit reason, headshot flag,
  distance, damage when the source event actually contains damage, and attack ID.
- Engagement clusters with start/end time, shot/hit/DBNO/kill/death counts, outcome, evidence type, and distinct
  opponent count. Teammates, self damage, and unowned environment/status damage cannot establish opponent evidence.
- Combat-only weapon lists. A Blue Zone or fall resolution can remain a valid fight event without being presented as
  a weapon.
- Verified direction metadata only when both endpoints exist.
- Separate firearm-shot, throwable, melee, and generic attack actions. A `LogPlayerAttack` with an empty weapon item
  ID is retained only in raw telemetry and excluded from normalized shot counts.

The UI uses an actor-color underlay and a movement-mode inner line. Shot pulses, verified hit arrows, caused/taken
DBNO symbols, kill/death X markers, revive plus markers, and verified/inferred engagement rings use stable shapes,
line patterns, visible names, and independent layer toggles. The grouped legend never relies on color alone. A current
event strip, linked list highlight, actor and event-type filters, and map buttons make the event meaning and location
explicit. Major-location shortcuts cover drop start, landing, first verified engagement, first inferred attack
activity, first hit, caused/taken DBNO, kill, and death when those facts exist. All player and teammate event
collections are normalized to one timeline origin and sorted by time, event index, and stable sequence before
playback. The current-event selector never exposes a future event before its timestamp.

## Defects Found And Corrected

- Environment/status damage had previously been accepted as engagement evidence. Opponent consequences now require a
  non-self, non-teammate related account; attack-only clusters are labeled as inferred activity.
- A temporary v11 build shadowed the tracked `account_id` while constructing the team ID set. v13 preserves the
  catalog account, payload player, and exactly one self team member as the same identity, with a regression test.
- Blue Zone resolutions could appear in an engagement's `weapons` list. Resolution events remain visible, while the
  list now accepts only observed attack tools and combat weapon damage categories.
- The all-team major-location candidate path accidentally preferred only self events. Actor filtering and shortcuts
  now use the same predicate.
- The inferred-activity legend promised a dotted ring while the canvas drew the verified-engagement dash. Canvas and
  legend line patterns now match.

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
- Replay renderer version: `player-timeline-v13`.
- Rebuilt timelines: 1,846.
- Replay rebuild failures: 0.
- Remaining stale timeline candidates after rebuild: 0.
- Full retained v13 artifact audit: 1,846 unique match/player files and 1,637,882,371 bytes checked; 400,417 position
  samples, 1,142,464 combat events, and 2,514,196 normalized map points validated with zero issues.
- Engagement evidence audit: 23,119 opponent-verified engagements, 7,268 inferred attack activities, zero teammate
  opponent leaks, and zero non-weapon environment labels in weapon lists.
- Focused replay regression tests: 15 passed after the final identity and weapon-evidence changes.
- Full project regression tests: 555 passed; 33 dependency deprecation warnings and no application assertion
  failures.
- Real Chrome checks used the latest Yuuki replay. Drop/landing map navigation, actor and environment filters, strict
  current-event time boundaries, nonblank canvas rendering, desktop layout, and 430 px mobile layout passed with no
  overflow, console errors, page errors, or failed API responses.

The retained source data remains the authority. A UI label such as `direction verified` means both event endpoints
were observed; absence of that label means direction was not inferred.
