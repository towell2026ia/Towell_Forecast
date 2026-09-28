# PRD 09.2E.1.1 — Published Historical Portal Read Fix

## Two views, two non-interchangeable contracts

`monthly_observations_current` is the existing **temporal / point-in-time** view.
It requires non-NULL `available_at`. Migration 008 does not replace, alter, grant
additional access to, or remove its filter. UNKNOWN historical facts remain
ineligible for certified temporal replay. A known date alone is not a replacement
for a runner's own cutoff/evidence checks.

`portal_monthly_observations_current` is **published historical display** only.
It selects the latest IMPORTED version per chain/product/period/metric, joining
the batch by both ID and chain ID. Unbatched, non-imported and newer unpublished
revisions are excluded. UNKNOWN/NULL is retained literally, never repaired with
an ingestion date or `created_at`.

The original portal queried the temporal view: 39,270 legitimately UNKNOWN facts
were therefore invisible. This read fix is not a forecast or temporal-unblocking
operation.

## Migration and authorization

Authorized schema-only migration:
`supabase/migrations/202609280001_portal_published_history.sql` (008).
Migrations 001–007 are unchanged. The new view has `security_invoker=true`, flat
safe chain/product display metadata, SELECT for `authenticated`, and no grants
to PUBLIC or `anon`. All four joins obey existing caller JWT/RLS permissions.
No evidence description, account metadata, credentials or administrative payload
is included. The DISTINCT ON view is read-only and has no DML grant.

Only this migration is applied after exact base/repo/project and read-only remote
gates. No seeds, historic writes, batch transitions, bootstrap rerun, policy
changes, new facts or corpus expansion are part of deployment.

## Portal query contract

`SupabaseForecastReadRepository` uses the new view for has_history, periods,
historical pages, counts and latest period. It does not use PostgREST relationship
embedding from a view. Category filtering uses real `category_id`, never text
inference. Products remain from the authorized real master catalog; ITEM/UPC
aliases remain from the existing certified import mapping.

Tuple pagination is scope + product + period, ordered stably with metric tie
breaking. Page sizes 50/100/250 read at most 153/303/753 observations respectively.
Counts use HEAD requests; the 39,270-fact corpus is never preloaded into the browser.
Parent/child scopes are separate; they are not added together. Zero is preserved
and absent metrics remain missing.

The expected unfiltered ADMIN catalog contains 50 categories. **49 distinct
category IDs are actually assigned to products with facts**; the remaining real
catalog category is not fabricated into observations. PH09 counts the authorized
category catalog, as the selector does. Filtering an unused category legitimately
returns no facts. Historical category assignments are not rewritten.

## Executable checks and evidence boundaries

| Checks | Evidence |
| --- | --- |
| PH01–02 | Linked CLI read-only base count 39,270, temporal count 0 |
| PH03–09 | Real PostgreSQL authenticated ADMIN: 39,270 facts, 18 scopes, 1,010 products; authorized category catalog 50 |
| PH04–06 | SALES 13,153; ORDER 13,099; DELIVERY 13,018 |
| PH10 | Independent new-view readback + joined lineage + `verify_remote_facts`; exact frozen hash, not a copied manifest |
| PH11–14 | UNKNOWN/NULL 39,270; 1,538 scope blockers + 720 quantity conflicts intersect zero; only same-chain IMPORTED batches |
| PH15–17 | Real PGlite/PostgreSQL joins: draft/unbatched excluded, latest published selected, original view definition unchanged |
| PH18–21 | PostgreSQL GRANT/RLS behavior for anonymous, VIEWER, EDITOR, ADMIN; synthetic role identities are local only; actual ADMIN and remote ACL checked separately |
| PH22–29 | UI/repository tests: dashboard, history, next/previous, bounds, chain/product/category/period, catalog search |
| PH30–32 | Existing real-SDK recovery callback fixture and login/session/logout/recovery regression suites |
| PH33 | Public repo scan + compiled browser bundle privileged-config scan |
| PH34–35 | Remote model counts remain 0; pre/post business/lineage checksum identical; readonly view and unchanged historical table guards |

`scripts/audit_published_history.py` is an explicitly invoked administrative
read-only verifier, not imported by runtime/startup. It uses the normally linked
CLI account; never extracts or persists credentials. Its joined full-corpus
readback is a **private audit**, not a browser query or public fixture. Results
stay in ignored `outputs/prd09_2e11/`. The fingerprint reconstructs certified
business facts only after checking each view fact's real value, identity, metric,
period, version, scope, batch and evidence against frozen lineage. It also checks
blocked physical-source and business-key intersections.

Expected dataset SHA:
`3a092905dc699a762be0ca3f459ff9af31c9045d5cc06f6407453b457ac969e4`.

## Runtime and release boundaries

Python remains `PERSISTENCE_PROVIDER=sqlite`, `DATA_PROVIDER=normalized`,
`SUPABASE_ENABLED=false`. No Supabase Python provider, model run, new vintage,
Champion change, OpenAI, research, voice or write portal capability is enabled.
Login, persistent session, logout and `/update-password` remain unchanged;
public signup must remain disabled. No service role is usable in browser code.

GitHub `Towell_Forecast/main` is source of truth. Publish that exact commit to the
already authorized Sites project. Do not push to previous-origin or configure a
new Netlify deployment. Netlify auto-deploy is separately pending if not linked.

Public acceptance is a separate mandatory gate: owner signs in on the published
portal, checks dashboard 18/1,010/39,270, opens history, changes chain/product/
category/period/search, checks next/previous and logout. SQL, mocks or a successful
Sites deployment alone do **not** prove this gate. Record actual results privately;
report `BLOCKED_PUBLISHED_HISTORICAL_PORTAL` until that journey is verified.

Rollback: redeploy the previous Sites saved version to revert the frontend. The
additive view can remain unused; do not delete facts, repair dates, undo certified
bootstrap or rewrite migration history. Any later view change must be a new
reviewed migration, not an edit to applied migration 008.
