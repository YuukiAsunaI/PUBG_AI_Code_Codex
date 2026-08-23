# Multi-Discord Registration and Dimension Analytics

Date: 2026-08-23 KST

## Player and Discord model

- `registered_players` remains the tracked PUBG identity and owns collection/public-profile state.
- `player_discord_registrations` is the authoritative active association between one tracked player and one Discord
  guild. Its unique key is `(registered_player_id, guild_id)`.
- One PUBG account can be active in multiple Discord guilds. Removing one association leaves every other association,
  collection state, and historical match data unchanged.
- Legacy `registered_guild_id` values are idempotently backfilled during `init-db` and retained only for compatibility.
- Guild-scoped Discord commands, rankings, stats, trends, recommendations, fight outcomes, and replay catalogs use the
  same active-association predicate. Global administrators retain cross-guild scope.
- Registration rows participate in deletion preview, dry-run ordering, and MySQL target backup scope.

The localhost player manager exposes collection and public-profile toggles separately. Discord guilds are selected
from the known-guild catalog; each row shows all active guild associations and can add or remove one association.
The mobile layout stacks these controls per player instead of hiding them behind a wide table.

## Detailed performance dimensions

The trend report supports `map`, `weapon`, and KST `hour` alongside the existing date/week/month/quarter/year and match
dimensions. Every bucket exposes match count, chickens and chicken rate, K/D/A and KDA, average kills/DBNOs/damage,
accuracy, headshot hit and kill rates, fight count and fight win rate, DBNO balance, movement, and survival where the
source facts support them. Existing map/mode/date/time/custom filters apply before grouping.

Weapon semantics are deliberately split:

- A weapon-use match requires shots, dealt damage, kills, assists, or caused DBNOs for that weapon.
- Chicken rate is the share of those weapon-use matches ending in first place.
- Kills, assists, caused DBNOs, dealt damage, shots, hits, and offensive body parts are attributed to the player's
  weapon.
- KDA deaths, taken DBNOs, and fight losses are attributed from fight outcomes to the weapon the player held at that
  event. They do not use the opponent-causer death column from `player_weapon_match_stats`.
- Received damage, hits, and received body parts describe the opponent weapon that caused the hit and are labeled as
  such in the UI.

## Validation evidence

- MySQL 8.0.41: schema version 28, 52 tables, KST sessions.
- Existing legacy association backfill: one retained row; FK to `registered_players` present.
- Live transaction test: one player was associated with two temporary guilds, visible through both guild-scoped
  registry queries, then rolled back; zero temporary rows remained.
- Live re-registration transaction requested the opposite public-profile value and confirmed that the existing
  player-wide setting was preserved; the transaction was rolled back.
- Player-intelligence audit: passed all 12 checks over 1,567 eligible matches and 1,913 player-match rows.
- Live `Yuuki_Asuna---` report: 331 matches; 9 map buckets, 42 weapon buckets, and 22 KST-hour buckets, each returning
  chicken rate, KDA, and fight win rate.
- Automated Python suite: 587 tests pass after the web association and re-registration tests were added.
- Playwright desktop/mobile: six player registry rows, twelve management controls, one retained Discord association,
  42 weapon chart rows, and zero console errors, request failures, HTTP errors, document overflow, blank screens, or
  overflowing buttons at 1536x900 and 390x844.
