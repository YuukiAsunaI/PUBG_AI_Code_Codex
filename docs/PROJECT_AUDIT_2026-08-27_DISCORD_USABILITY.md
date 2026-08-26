# Discord Command Usability Audit - 2026-08-27 KST

## Reported Problems

The app-managed Discord bot still required knowledge that ordinary users should not need:

- an opaque `options` string in the trend command;
- raw PUBG match IDs in match and snapshot commands;
- static or unclear values for ranking and recommendation controls;
- nickname suggestions that could become incomplete after the first database page;
- long plain-text responses with weak visual grouping;
- application-command options with incomplete Korean explanations.

## Corrections

1. Every application-command option now has a concrete Korean name and description. Bounded values use choices or
   stated ranges, and free-form date values include their required format.
2. Trend filters are explicit options for aggregation, team mode, perspective, map, game mode, match type, KST date
   range, and displayed bucket count. The opaque options field no longer exists.
3. Match and recent-snapshot commands require a current-guild registered player and offer that player's recent
   completed matches as readable KST/map/mode/placement/combat labels. The raw match ID remains hidden as the selected
   value, and leaving the match blank uses the latest eligible record.
4. Recommendation minimum sample size is configurable from 1 to 100,000 matches, with a separate result-count option.
5. Ranking exposes searchable metrics, configured/current-guild/global scope, displayed player count, and minimum
   completed matches.
6. Registered-player autocomplete filters by current Discord guild and the typed nickname or account fragment in
   MySQL before applying Discord's 25-result response limit. A global administrator does not receive another guild's
   players in a guild autocomplete menu. PUBG nickname `_`, `%`, and escape-marker characters are matched literally.
7. Core query replies now use bounded embeds with named sections and caller-only previous/next page controls.
8. A reusable deployment verifier queries Discord's stored guild-command definitions without printing the bot token.
9. Platform, map, attachment, deletion-scope, ranking-row, and snapshot identifier output is Korean-first.
10. Recent-match catalogs are limited to the latest 500 records and held in a 60-second, 128-entry bounded cache.
    The replaced opaque trend-options parser and its obsolete tests were removed.

## Verification

The rebuilt local manager release is `2026.08.27.2`.

- `dist/PUBG_AI_Manager.exe`: 51,805,170 bytes
- SHA-256: `05EFB5E75B52C2A98C5110AED7278B31038C6CD89275F2ACDAD3A6BD513328DF`
- local health endpoint: release `2026.08.27.2`, local-only bind `127.0.0.1`
- app-managed bot: ready, four connected guilds, no last error
- command synchronization: 26 guild commands in each of four managed guilds

`scripts/verify_discord_command_deployment.py` then read the definitions back from Discord's API. All checks passed:

- all 26 commands exist in each managed guild;
- all options have non-placeholder descriptions;
- no option exceeds 25 static choices;
- no legacy English platform, scope, or Raw-data choice label remains;
- trend has no opaque options field;
- match selection is optional and autocomplete-driven;
- recommendation minimum sample size is configurable.

The complete Python test suite passed with **653 passed**. Python compilation and Git whitespace validation also
passed. PyInstaller emitted only known platform/optional-dependency warnings; the packaged application, MySQL-backed
manager, and Discord gateway connection started successfully.
