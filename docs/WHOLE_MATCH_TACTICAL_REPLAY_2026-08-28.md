# Whole-Match Tactical Replay - 2026-08-28 KST

## Goal

The 2D replay must explain how an entire match developed around a selected registered player. The registered player
is the viewing focus, not the data boundary. Every match participant available in retained telemetry must therefore
remain visible as movement and tactical state, including unregistered enemies and bots.

## Implemented Contract

- The player form resolves a local registration to its stored PUBG account ID and uses that exact ID to list completed
  matches. Partial nickname text is never used as the match-membership predicate.
- Selecting a match creates or reuses one `match-timeline` artifact through `POST /matches/{match_id}/replay`.
- The artifact includes every participant account found in the match roster, all available position samples, landing
  points, combat markers, engagement intervals, plane route, phase circles, and care packages.
- Full raw telemetry objects are not embedded in the derived artifact. Immutable raw match and telemetry files remain
  the source of truth in Raw storage.
- The selected player is rebased as the focus without deleting the original root track. Team ID determines allies;
  other humans are enemies, PUBG bot identities are bots, and other registered users receive an additional emphasis.
- Ally, enemy, and bot layers are independently switchable. A partial-name participant search and actor filter make a
  100-player roster usable without requiring an account ID.
- Movement trails can show 15 seconds, 30 seconds, one minute, two minutes, or the full elapsed path. Airborne,
  vehicle, on-foot, and DBNO states retain distinct line and marker shapes.
- Hit direction is drawn only when both telemetry endpoints exist. DBNO, kill/finish, death, revive, environmental
  damage, and engagement state remain distinct. A dead or finished player's live marker disappears after the terminal
  event while the historical death marker remains.
- The event explorer defaults to the focus player. Operators may opt into all-participant events, but detailed raw
  event logs for every player are not required to understand the map flow.

## Live Verification

The source application was exercised through Playwright against retained local MySQL and Raw storage data:

| Check | Result |
| --- | ---: |
| Participants loaded in the selected match | 100 |
| Human / bot split | 99 / 1 |
| Participant roster buttons | 100 |
| Actor selector options | 101 |
| Tactical events available across all actors | 14,545 |
| Required replay layers enabled | 8 / 8 |
| Data-quality checks | 27 / 27 passed |
| Browser console, request, and HTTP errors | 0 |

The canvas contained 2,304 opaque samples and 1,938 sampled colors in the automated pixel check. Playback advanced
at 8x, the enemy and focus relationship labels were both present, and the desktop/mobile application had no blank
view, overlay, horizontal overflow, or overflowing buttons.

## Telemetry Limits

PUBG telemetry is discrete event data, not a video recording. Lines between position samples are interpolated and do
not prove the exact route between samples. A missed `LogPlayerAttack` has an observed firing origin but no general aim
ray endpoint, so the replay does not invent a shot direction. These limits are shown through marker semantics rather
than replaced with unverified estimates.
