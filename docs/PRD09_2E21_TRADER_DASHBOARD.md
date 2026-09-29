# PRD 09.2E.2.1 — Multi-chain Trader Dashboard

Base: `5f864917498f8f4366212c4bd89d565e1961fe09`, repository `towell2026ia/Towell_Forecast`, branch `main`.

## Scope and data boundaries

Frontend only. Zero backend/model/Docker/CI/migration changes and zero Railway, Netlify or Supabase configuration changes. The former executive dashboard's timeline, series palette, cutoff/future separation, tooltip and table presentation are extracted into data-driven components, without importing its pilot snapshots. Sidebar/navigation layout remains unchanged; Auth/recovery and Lottie files remain unchanged.

`SupabaseForecastReadRepository.getForecastHistory` uses the existing RLS-backed, cursor-paginated `portal_monthly_observations_current` reader. It aggregates observed SALES/ORDER/DELIVERY within **one exact chain UUID**, optionally scoped by product/category/search/visual dates. Missing metrics and gap months stay NULL; an observed zero remains zero. It refuses foreign identities, repeated cursors and silently truncated totals. It never downloads or aggregates all chains for a global chart.

Customer forecast is optional: only the latest RLS-visible VALIDATED/FROZEN version with `issue_period <= preview.issue_period`, and rows with the same chain/version/product identity. DRAFT/SUPERSEDED versions are not used. Absent data does not become zero; denied/unavailable reads are distinguished from an empty series. No new SQL or service credential is required.

Forecast comes exclusively from the existing Railway E2 preview client. The Bearer/session implementation is unchanged. Product horizons come from the selected product only. Category/chain horizons use the exact backend `aggregates` entries (`level`, UUID key) without reconstructing statistical/ML/quantile aggregates. Malformed/incomplete H1–H12 results are not repaired. A search subset without a selected product has no synthesized forecast aggregate.

No preview computation runs on a filter change, mount, focus or visibility event. The existing button alone creates a job. Polling uses the existing status/result routes and keeps a previous successful result while a new job runs. Cross-chain/product transitions do not display a foreign cached preview. The visited engine module remains mounted but hidden when navigating away, preserving job, chart and switches for the active session; existing Auth/logout disposes that session and repository.

## Presentation

- Compact summary: real latest period, literal Fcst Towell H1, statistical/ML coverage with denominator and percentage, Preview Leader.
- Recharts `ComposedChart`, null-gap `Line`, future `ReferenceArea`, backend cutoff `ReferenceLine`, P10–P90/P10–P95 range `Area` only when all four ordered quantiles exist.
- Forecast Towell is the main line. Aggregate scopes do not display unsupported Stat/ML/bands. All-chain view displays coverage by scope, never a consolidated chart/total; divergent cuts are an informational badge.
- Product/candidate distributions are labelled “Modelo más seleccionado,” never a global Champion. ML NOT_ELIGIBLE is a normal state. Published Champion absent is “Ninguno”; preview selection is not publication; promotion is OFF.
- Null WAPE/Bias use `—` with an evidence explanation, never `Sin evidencia%`.
- Executive comparison: Periodo / Fcst Cliente / Fcst Towell / Venta / Pedido / Entrega. Product horizon table: H1–H12 plus literal Stat/ML/Towell/quantiles. Aggregate horizon table: only backend Towell.
- Responsive chart heights: 300px mobile, 360px tablet, 440px desktop; horizontal table scrolling. Technical details are collapsible.

## Executed local gates

| Gate | Result |
| --- | --- |
| Portal/frontend tests | 162 PASS, zero failures |
| Backend regression | 487 tests OK; seven existing local/platform fixture skips |
| Statistical / ML / ensemble / decision / closure / generic engine | 12 / 11 / 18 / 30 / 25 / 14 tests PASS |
| Forecast engine audit | All executable controls PASS |
| SQL security / RLS regression (local PGlite) | 36/36 and 28/28 PASS |
| TypeScript | PASS |
| Lint | Zero errors; four existing warnings in ignored local `.venv` dependency assets |
| Native Next/Netlify build | PASS |
| Sites rollback build (not published) | PASS |
| Netlify / Sites bundle checks | PASS; zero privileged configuration references |
| Public repository scan / whitespace | PASS |
| Docker | No local Docker executable; existing CI performs Docker build, non-root process and volume restart gates |

New frontend tests cover V01–V16, A01–A06, C01–C09 and session/module/filter/switch/job regressions. Existing login/logout/history/Lottie tests remain in the executed suite. Fixtures are synthetic; the 45 evaluated / 20 statistical / 832 training samples / 0 ML coverage scenario tests **presentation semantics**, not a newly fetched Al Super production result.

## Remote/visual acceptance remains distinct

Read-only pre-change `/api/ready` confirmed production + Supabase + SQLite + SupabaseAuthProvider + operational preview true + official publication false. No remote model runs, historical changes, forecast writes, Champion publication or official vintages were performed by this task.

The previously accepted historical corpus (39,270 observations, 18 scopes, 1,010 products) and its lineage are not changed by this frontend patch. This task did not independently recount that corpus under an authenticated production session.

Browser validation could not start: the supported browser tool failed with `windows sandbox failed: helper_unknown_error: setup refresh had errors`; a reset/retry failed with `trusted Node process exited unexpectedly`. No credentials/session tokens were read. Therefore desktop/tablet/mobile appearance, live customer forecast presence, live product eligibility and the existing Al Super preview require owner visual acceptance. Automated DOM/adapter tests and successful builds do not substitute for those gates.

After GitHub Actions and Netlify auto-deploy, compare `/api/portal-version` to the final commit; inspect Railway `/api/ready` without altering its configuration. The owner should consume an **existing** preview first: inspect product, category, chain and all-scope coverage; toggle series, change visual dates and switch modules; verify historical gaps/zeros and H1/H12; check ML eligibility and literal bands. A new run is not needed for visual inspection. Do not close as `PASS_MULTICHAIN_TRADER_DASHBOARD` until real visual acceptance passes.
