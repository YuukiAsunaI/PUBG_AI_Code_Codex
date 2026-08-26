# Desktop Runtime Audit - 2026-08-26 KST

## Reported Failure

The current web source contained the latest player analytics, managed Discord bot, match explorer, watchlist, number
formatting, flight-path, and collection-policy changes, but restarting the Windows local manager did not show them.

After the launcher correction, the navigation itself remained visible but many player, replay, collection, and
operations tabs rendered no content when selected.

## Root Cause

The repository launcher unconditionally preferred the packaged executable. That executable was built at
2026-08-25 07:03 KST, before commit da62a3d on 2026-08-26, so every restart through the repository launcher could
continue to show the older embedded application.

The first source-first launcher correction also exposed a Windows argument edge case: the batch project-directory
token ends in a backslash. Passing it as the final character of a quoted base-directory argument could corrupt
argument parsing. Appending a dot avoids that trailing-backslash boundary.

The blank-tab regression was caused by one malformed hidden input in the watchlist edit dialog:
`type="hidden>` was missing its closing quote. The browser consumed following markup as part of the attribute and
moved every subsequent workspace section outside `<main>`. The menu buttons therefore existed, but their target
sections were not eligible for the workspace display rules.

## Corrections

1. A repository checkout now sets PYTHONPATH to its own src tree and launches that source before considering a
   packaged executable.
2. A source startup failure is reported and is never hidden by silently launching an older executable.
3. The packaged executable remains a fallback only when Python or the source tree is unavailable.
4. The malformed watchlist hidden input was corrected, preserving every workspace section under `<main>`.
5. Desktop navigation now keeps document, sidebar, and workspace scrolling isolated so menu changes cannot hide the
   application shell.
6. The desktop release identifier is 2026.08.26.2.
7. dist/PUBG_AI_Manager.exe was rebuilt locally from the same release.
8. Regression tests enforce source-before-EXE ordering, the explicit source path, normalized base directory,
   valid watchlist dialog markup, workspace section ownership, visible content for every menu tab, and
   no-fallback-on-source-error behavior.

## Runtime Evidence

The rebuilt packaged application was started directly:

- file size: 51,788,139 bytes;
- SHA-256: 6F48A999C24B717C30850899AB2993FC576B1A7D45F8837B644BFD29CD0AF761;
- native window: PUBG AI Local Manager;
- owned endpoint: 127.0.0.1:8000;
- health release: 2026.08.26.2.

The corrected launcher was then started independently. Its process tree contained the Python module desktop command,
not PUBG_AI_Manager.exe, and its own health endpoint also returned 2026.08.26.2.

The packaged server's rendered HTML contained the managed Discord bot, per-guild command visibility, Discord member
selection, match-detail explorer, watchlist, registered-player selectors, display settings, custom-match filters, and
frequent-flight-path workspace.

## End-To-End Verification

The real-data Playwright workflow ran against the packaged application server. It verified:

- 351/351 processed matches for the test player with no pending analytics;
- persisted Korean-unit number formatting and no visible unconverted grouped number at or above 10,000;
- player analysis, daily trend chart, match item summary, landing map, ranking, player comparison, and flight paths;
- the app-managed Discord bot running for four guilds with write-only secret inputs empty in the browser;
- desktop and mobile HTTP errors, console errors, request failures, overflowing buttons, blank pages, and overlays: 0;
- all 39 top-level/default/submenu selections rendered non-empty content, with every section owned by `<main>`;
- the maintained full UI workflow checked all 33 unique workspace states with zero failures;
- the 12-check player-intelligence data-quality audit: all passed.

The final Python suite completed with **649 passed**, 106 dependency deprecation warnings, and no failures.
Python compilation and Git whitespace validation also passed.

The executable and PyInstaller build directory remain local and ignored by Git. The launcher, release marker, tests,
and audit documentation are versioned so future source updates cannot repeat this launcher mismatch unnoticed.
