# PRD 09.2E.1 — Supabase Auth and multi-chain read UI

## Scope and release gate

Approved base: `966e3fff16b9e268787cebb2f2e7ca69c9dca7f2`, clean main = origin/main.
Base GitHub Actions run 36302252040: PASS.
Project: `bskoyqhbgrycpwhydnnr`.
This phase activates frontend authenticated reads, not the Python engine provider.
Code, local tests and read-only administrative regression are implemented. A
release PASS also requires the real authenticated read acceptance and exact final
commit CI; neither a mock test nor an administrative CLI query substitutes for
the browser user's JWT/RLS acceptance. Private execution results record these
separate gates without credentials or row data in this document.

## Auth and identity

`/login` uses the official `@supabase/supabase-js` client and password sign-in.
No public signup or simulated user creation is offered. Supabase's public Auth
settings have independently confirmed `disable_signup=true` after the owner's
manual change, with email login enabled. Do not re-enable registration.

The SDK persists and refreshes the session in browser storage. The portal checks
the user with Supabase Auth `getUser()`, then reads that user's `profiles` row
under RLS. Only an ACTIVE profile with ADMIN/EDITOR/VIEWER is admitted. Missing,
inactive, expired or unverifiable identities fail closed. Local role fields,
environment allowlists and `oai-authenticated-user-*` are not portal authority.
The ChatGPT helper remains as unused embedded-integration compatibility code.

Auth events and window focus revalidate the profile. A session/identity change
immediately discards filters, repository caches, pending reads and visible data.
Logout calls `signOut`, clears local session storage and navigates to `/login`.
No shared server-global user data cache is used. SSR sends only the public shell
and public configuration; protected business rows are read after authentication.
No SSR cookie package is necessary for this client-session architecture.

## Public configuration

Set these outside Git, locally and in the frontend host:

```
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=
```

`NEXT_PUBLIC_SUPABASE_ANON_KEY` is the supported legacy alternative. Publishable
takes precedence. Configuration rejects secret-key prefixes and legacy JWT keys
whose role is not `anon`. Never supply a service key, database password, personal
access token or CLI token to either public variable. `.env.example` has names
with empty values only; actual runtime values are not committed.

## Read repository and RLS

`ForecastReadRepository` defines mockable profile, chain, category, product,
period, historical page, summary and profile-list reads. The implementation uses
the signed-in SDK's authenticated JWT; it never performs normal reads with
service role. Database policies remain the final authorization authority.
ADMIN sees all authorized masters and Users; EDITOR/VIEWER see only their RLS
scopes and have no global user module. All E1 historical operations are read-only,
including for ADMIN/EDITOR. No migration or RLS relaxation is required.

`/api/portal-validation` is a separate read-only diagnostic: it verifies the
bearer at the project's Auth service, verifies the ACTIVE database profile,
then queries through a per-request public-key client with that same JWT. It
returns/logs aggregate role/count attestation only, not bearer material, email,
row-level data or service credentials. It cannot mint identity or run Python.
No diagnostic result is persisted in Supabase. Error responses are controlled
and not shared-cacheable.

## Catalog, hierarchy and shared filters

Global filters are `chainId`, `productId`, `categoryId`, `periodRange` and search.
All modules use the same context. Chain changes reset dependent selections;
category/search changes reset product selection. Logout resets everything.

Chains come from `chains`. `has_history` is derived from RLS-protected HEAD/count
queries against `monthly_observations_current`, not an invented column or a fixed
list. The operative picker defaults to actual-bearing scopes; empty masters can
be explicitly shown and are labelled without claiming they have actuals.
Proven parent UUID/type metadata comes from the certified versioned import
mapping. Names are never parsed to invent hierarchy. Invisible parents are not
rendered from another user's scope metadata.

Products come from `products`, fetched in 250-row catalog pages. The UUID remains
identity; description is display only. Documented ITEM/UPC mappings come from
the certified import metadata. Search covers description, code, UPC and variant;
the picker displays up to 100 matches and asks users to refine larger results.
Catalog requests are coalesced within the current repository/session instance.
This small authorized catalog is not a preload of the 39,270 raw observations.

Categories come from `categories` and use stable UUIDs. Category filtering means
the evidenced current master `category_id`. A product with multiple historical
categories and NULL current category is not arbitrarily assigned to one; its
historical per-fact category evidence remains unchanged. E1 does not introduce a
new per-fact category query/schema or recertify historical category membership.
Periods come from visible observations: min/max bound HEAD checks, and only
months with actual visible rows become options. Empty gaps are not invented.

## Historical pagination and dashboard

History uses the security-invoker current-observation view and combines metrics
by scope + product + period. Each tuple keeps SALES/ORDER/DELIVERY separately;
missing is `Sin dato`, while zero remains zero. Duplicate current metrics fail
closed. Parent and child values are never added into an automatic total.

Pages default to 50 tuple rows, with 100/250 options. Upstream filters and a
validated stable scope/product/period cursor select at most `3 * size + 3` metric
rows (153/303/753). One lookahead tuple determines next-page availability. The
largest read remains below the usual 1,000-row API cap; missing metrics do not
break pagination or truncate a displayed tuple. Filter changes reset cursors.

Dashboard KPIs are live scope/product/observation counts and last available
period. Coverage is shown separately per scope. Observation count is a count of
records, not a mixed-grain quantity total. No fake WAPE, forecast, vintage or
Champion is presented. Loading, empty and controlled error states never fall
back to pilot/demo data. Motors show no published run and disabled execution.

## Disabled operations and retained evidence

Capture, forecast/ML/ensemble runs, closures, forecast decisions and Python
assistant proxy endpoints now return 403 before reading identity or making any
privileged request. No browser header can enable them. The old service-key
operational proxy was removed rather than repurposed to fake user identity.
The assistant's fixed pilot context is no longer active; its protected actions
remain off pending secure Supabase identity federation in a later PRD.

Users displays authorized `profiles` name/role/status only. It neither queries
`auth.users` for email nor offers simulated creation. Demo files and legacy
components remain available for fixtures/regression, but are not imported as
the production portal's data providers. Historical frozen artifacts, migrations
001–007, model implementations, vintages and Champion were not edited.

Python still uses `PERSISTENCE_PROVIDER=sqlite`, `DATA_PROVIDER=normalized`,
`SUPABASE_ENABLED=false`, `LEGACY_PILOT_ENABLED=false`. OpenAI, Deep Research and
voice remain off. There is no Netlify or Railway configuration change.

## Screens and verification

Changed screens: login, protected shell/header/footer, global filters, executive
dashboard, historical table/pagination, periods, read-only Users and unavailable
motor/capture/quality/audit states. Existing blue/white tokens, sidebar, cards and
responsive layouts are retained; the global stylesheet was not redesigned.

Local UI01–UI20 plus additional session/repository/security tests pass. SQL
baseline and SQL security 21/21, RLS 28/28 pass on local fixtures. Full backend:
452 collected, 446 executed PASS, six optional SKIP. Lint/typecheck/build pass;
only pre-existing coverage-library lint warnings remain. Built browser assets
are separately scanned for privileged environment references. CI repeats these
checks plus Docker build and persistent-volume/restart smoke.

The original PRD06/07 navigation hash remains unchanged in
`services/ensemble_engine/visual-baseline.json`. Its exact approved-base bytes
are retained as `fixtures/legacy-forecast-towell-app.tsx.txt` (SHA-256
`9E3810337AC3303F510B4751E43F7C8041F0ABC517E22714799BDE1181A96F09`).
Those two legacy navigation tests now verify this non-executable frozen artifact;
they cannot require a pilot hardcode in the deliberately replaced active shell.
UI01-UI20 and browser-graph tests cover the active portal instead. No baseline
hash is regenerated, and no decision/closure implementation is modified.

Remote read-only regression confirms 30 masters, 18 actual-bearing scopes, 1,010
products, 50 categories, 39,270 observations, 28/28 RLS and private buckets.
An actual anonymous public-key request returns 401 with no protected rows.
The prior remote 10/10 permission certificate is retained; this phase does not
repeat its profile-changing transaction because remote writes are forbidden.
The independent business/lineage checksum matches the certified bootstrap and
all frozen pins remain unchanged. No historical write, productive model run,
new vintage or Champion change was performed.

Dataset SHA:
`3a092905dc699a762be0ca3f459ff9af31c9045d5cc06f6407453b457ac969e4`.

The owner explicitly authorized synchronizing this same approved GitHub source
with the existing public Sites host's internal repository for publication.
`origin/main` remains the authoritative source; `previous-origin` is never used.
Publication and authenticated read acceptance are separate gates. The owner
reported being unable to log in because their password is unavailable. No
password reset, credential handling or user mutation is performed by this PRD.
Real ADMIN login/refresh/logout and browser-JWT counts remain BLOCKED pending
owner access recovery, even if publication and CI pass. Do not report
`PASS_MULTICHAIN_UI_AUTH` from mock or administrative evidence alone.
