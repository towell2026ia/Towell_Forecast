# PRD 09.2E.2 — Validation evidence

Base SHA: `b33e34433eb2895800240475f52bd6522b1a7113`; base GitHub Actions success: `36481263333`. Initial main and origin/main matched and the tree was clean.

## Executed local gates

- Backend: 487 tests, PASS; seven Windows/local skips (six existing private-fixture gates and one POSIX-only symlink test). Backend coverage 88%; CI minimum remains 81%. The new provider/auth/preview suite includes 29 executable synthetic-fixture tests. These are not claimed as real authenticated production runs.
- Portal: 124 tests, PASS, including real-result UI contracts, all scopes, product H1–H12, insufficient/read-only/failure/loading states, stale read protection, Bearer-only Railway requests, plus existing Auth/recovery/logout/filter/session/Lottie regressions.
- Existing model suites: statistical 12, ML 11, ensemble 18, decision 30, closure 25, generic forecast engine 14; PASS. Strict PIT and deterministic results retained. Audit forecast engine PASS.
- SQL bootstrap 13/13, security 36/36, RLS 28/28, baseline constraints PASS; local emulation only, no new migration.
- Lint: zero errors; four warnings solely in ignored local Python dependency assets. Typecheck, native Next/Netlify and rollback Sites builds and both public browser bundle scans must pass before commit/deploy.
- Python compilation, critical lint and dependency vulnerability audit PASS.
- Docker build/non-root process/volume restart are verified by GitHub Actions, not by a fabricated local Docker claim (Docker is unavailable locally).

## Read-only remote data regression

`audit_portal_periods.py --after` completed against the linked Towell project with SELECT-only inspection and private output under ignored `outputs/prd09_2e2/read_only_regression`. It did not exercise or bypass the interactive JWT path for a forecast.

| Measurement | Result |
| --- | --- |
| Published observations | 39,270 |
| Certified historical scopes/products | 18 / 1,010 |
| Latest global period | 2026-07 |
| Dataset SHA | `3a092905dc699a762be0ca3f459ff9af31c9045d5cc06f6407453b457ac969e4` |
| Business lineage SHA | `b73bdddd15b7a6867e81c19a47856c5d3ff6b659020c7b343e246ecb3caf487f` |
| Historical writes / availability changes | 0 / 0 |
| Supabase forecast / Champion / vintage writes | 0 / 0 / 0 |

No migrations, RLS/Auth URLs, historical facts, legacy golden bytes or visual-baseline hashes were changed. The old pilot engine wrapper stays byte-frozen; the active module uses a new operational view, with no parallel forecasting algorithms.

## Deployment / real acceptance gate

Railway preflight confirmed the correct existing service, repo/main, `/api/live`, one replica and volume `/var/lib/forecast-towell`. It also exposed an already-failed base deployment caused by mount ownership (`PermissionError`); the non-root entrypoint fix is included and must pass Docker CI before cutover.

The owner confirmed both the volume and Netlify public API origin setting. Runtime activation must happen only after CI PASS, with the exact block in `PRD09_2E2_OPERATIONAL_ENGINE.md`. Compare GitHub, Railway `/api/version` and Netlify `/api/portal-version` SHA. A healthy process is not real ADMIN forecast acceptance.

Do not mark `PASS_MULTICHAIN_OPERATIONAL_ENGINE` until actual Netlify→Supabase Auth→Railway→RLS→engine→SQLite results are observed for a real sufficient scope, regular product, intermittent product if present, insufficient/cold-start product and the all-chain batch. Until those observations exist, operational run counts, real metrics and production UI acceptance are PENDING, not invented or copied from synthetic fixtures. No production writes are authorized by this phase.
