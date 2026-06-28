# Reference Project Survey

Research date: 2026-06-27

This survey focuses on what can be reused or learned for a local MySQL + Discord + management UI project. Repository
metadata was checked with GitHub on 2026-06-27.

## High-Value References

| Project | Language | Last push | Notes |
| --- | --- | --- | --- |
| `pubg/api-documentation-content` | Docs | 2025-05-19 | Official documentation source. Use this as the authority for endpoints, telemetry events, and schemas. |
| `pubg/api-assets` | Assets/data | 2024-10-28 | Official dictionaries, enums, item IDs, map names, vehicle IDs, rank icons, item/weapon images. |
| `smw0807/pubg-kit` | TypeScript | 2026-04-26 | Current TypeScript SDK with Zod validation, 10 req/min rate limiter, LRU cache, telemetry helpers, NestJS support. Strong reference if backend is TypeScript. |
| `crflynn/chicken-dinner` | Python | 2022-12-08 | Older but useful because it includes telemetry playback visualization patterns and CLI replay generation. |
| `pubgsh/api` | JavaScript | 2023-03-02 | Useful architecture reference: GraphQL caching layer, normalized schema, local fixture caching. Uses PostgreSQL, but the design maps to MySQL. |

## SDK and Wrapper References

| Project | Language | Last push | Relevance |
| --- | --- | --- | --- |
| `ramonsaraiva/pubg-python` | Python | 2024-07-19 | Simple Python wrapper. Useful for endpoint ergonomics, but may need current telemetry/event coverage review before adopting. |
| `GavinPower747/pubg-dotnet` | C# | 2024-05-24 | Sync/async .NET client. Relevant only if a Windows desktop/C# implementation is chosen. |
| `ickerio/pubg.js` | JavaScript | 2023-03-04 | Lightweight JS wrapper. Older, but useful for API wrapper patterns in browser/Node contexts. |
| `bloody-green-tea/pubgapi` | Java | 2026-03-02 | Recent Java wrapper, GPL-3.0. License is not ideal for copying code into this project. |
| `Discord-ian/pubgy` | Python | 2024-03-09 | Python wrapper, GPL-3.0. Review ideas only unless license compatibility is explicitly accepted. |
| `theodorosidmar/pubgkt` | Kotlin | 2026-06-26 | Very recent multiplatform Kotlin library. Useful as a current API coverage reference, not a direct fit unless using Kotlin. |

## Recent Application References

| Project | Language | Last push | Relevance |
| --- | --- | --- | --- |
| `heversonbenatti/pubg-insight` | JavaScript | 2026-06-07 | Recent player-stat web app. Useful for UI flow and profile/stat presentation ideas. |
| `smw0807/pubg_your.stat` | Vue | 2026-05-03 | Korean PUBG stat search/team-finding site. Useful for Korean UX labels, cache-first stat lookup, platform/nickname UX, team-room flow, and 404/429 feedback patterns. Not a telemetry parser. |
| `smw0807/pubg-api` | TypeScript | 2026-05-17 | Recent API-facing TypeScript project. Useful to compare endpoint implementation and caching approach. |
| `amn057828-beep/pubg-ai-api` | Python | 2026-05-23 | Recent Python project with no GitHub description. Inspect before borrowing; may be directly related to AI/API experimentation. |

## Could Not Verify

| Project | Result |
| --- | --- |
| `SeatloN/pubg-api` | GitHub reported that the repository could not be resolved on 2026-06-27. It may be private, deleted, renamed, or misspelled. |

## Practical Takeaways

- Use official docs and assets as the ground truth, not community wrappers.
- For Python, build a thin first-party client around `requests/httpx` if wrappers are stale or incomplete. The API surface needed for the MVP is small.
- For TypeScript/NestJS, `smw0807/pubg-kit` is the strongest current reference because it already includes rate limiting, cache TTLs, telemetry fetch helpers, and NestJS module patterns.
- For 2D replay, study `chicken-dinner` first. It already demonstrates telemetry-to-playback conversion and common replay controls such as labels, winners, interpolation, damage display, and team colors.
- For normalized analytics API design, study `pubgsh/api`. It is old but directly relevant because it turns PUBG API data into a cached normalized schema.
- For Korean UX and team-finding flows, study `smw0807/pubg_your.stat`. Keep its cache-first/search-history ideas,
  but avoid client-side secret exposure and do not treat season stats as a substitute for telemetry-derived deaths.
- Avoid copying GPL code unless this repository is intentionally licensed compatibly. MIT/ISC references are safer to reuse directly.

## Suggested Adoption Path

1. Do not adopt a wrapper blindly for core collection. Implement a small internal `PubgClient`.
2. Borrow API ergonomics and rate-limit behavior from `pubg-kit`.
3. Borrow replay concepts from `chicken-dinner`.
4. Borrow normalized data/API concepts from `pubgsh/api`.
5. Keep official dictionaries synced from `pubg/api-assets`.
