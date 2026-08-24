# Desktop Version And Display Audit - 2026-08-25 KST

## Reproduced Failure

The operator opened `dist/PUBG_AI_Manager_2026-08-24.exe` while an older
`dist/PUBG_AI_Manager.exe` process from 2026-08-23 still owned `127.0.0.1:8000`.

The former desktop launcher treated any healthy PUBG local manager on the preferred port as reusable. The new native
window therefore displayed the older server's HTML. Direct comparison proved the mismatch:

- port 8000: no dedicated Discord bot manager and no frequent-flight-path workspace;
- port 8770 from current source: both features present;
- the number-format control shown by the operator also matched the older partial implementation.

This was a launcher version-isolation defect, not a missing token or hidden permission setting.

## Corrections

1. Every desktop launch now owns its FastAPI server. An occupied preferred port is never reused.
2. The launcher scans up to 100 localhost ports and selects the first available endpoint.
3. Release `2026.08.25.1` is exposed by `/health`, the desktop JavaScript bridge, the HTML body, and the visible runtime
   badge.
4. Number format is persisted under `display.number_format` in `config/local_settings.json`, independent of browser
   origin or selected local port.
5. The left navigation label is explicitly `Discord 봇`; its first workspace contains write-only PUBG API key and
   Discord token inputs, start, stop, command sync, auto-start, prefix, and per-guild visibility controls.
6. Browser QA now queries real player intelligence while Korean-unit mode is active. It rejects any visible grouped
   number of 10,000 or greater that remains without a Korean large-number unit and restores the operator's original
   mode after the test.

## Evidence

Focused tests passed for endpoint selection, release-aware health checks, display persistence/API injection, desktop
shell, and settings. The final Python suite completed with **618 passed**, 69 dependency deprecation warnings, and no
failures. Python compilation, JavaScript syntax validation, and `git diff --check` also passed.

The real-data Edge workflow verified:

- desktop and mobile HTTP status 200;
- original, persisted, and restored mode: `korean_units`;
- Korean large-unit values present in player analysis;
- unconverted visible values of 10,000 or greater: 0;
- visible navigation label: `05 Discord 봇`;
- dedicated bot manager visible and write-only secret fields empty;
- console errors, request failures, and overflowing buttons: 0.

The packaged `dist/PUBG_AI_Manager_2026-08-25.exe` was then started while the old manager still owned port 8000. It
selected port 8001, returned release `2026.08.25.1`, loaded `korean_units` from the shared settings file, and exposed
the dedicated bot manager. Both package processes closed through the native window without forced termination.
