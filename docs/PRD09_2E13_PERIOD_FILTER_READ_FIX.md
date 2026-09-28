# PRD 09.2E.1.3 — Published period filter

Base: `52cdf6a88e1e67ab41949d90979c14c63cf94039`.
Production: https://towell-forecastia.netlify.app. Sites remains rollback only.

## Findings and correction

The old `getPeriods(null)` made two bounds requests and one HEAD/count per
month over `portal_monthly_observations_current`. This N+2 pattern is confirmed
in source. A read-only authenticated-role SQL probe of the first bounds query
succeeded; it did not reproduce a failing browser HTTP status. Do not claim a
specific upstream timeout, RLS denial, or error code without that diagnostic.

GlobalFilters also rendered the generic data error whenever any catalog read
failed. The user's confirmed real Dashboard/History rows are therefore not
evidence of a failing historical corpus.

Migration `202609280002_portal_history_periods.sql` creates ONLY a read-only
view with `(chain_id, period)`, distinct from the published display view.
Both views are `security_invoker=true`. Public/anon permissions are revoked;
authenticated receives SELECT only. All underlying RLS remains authoritative.
Migration 008 and the temporal eligibility view are unchanged.
The remote compact catalog has 352 scope/month pairs and 43 distinct months;
only this small period catalog is read, not the 39,270 observation quantities.
The actual-bearing Al Super scope returns 19 months (2025-01 through 2026-07).

`getPeriods` performs one ordered GET selecting only `period`, optionally with
`chain_id` equality. It unions/deduplicates actual visible months in memory,
sorts ascending and never fills gaps. Diagnostics use the existing five-field
allowlist with table `portal_history_periods` / operation `period_catalog`.
Legacy bounds/count operations remain recognizable for earlier diagnostics.

Each filter now has its own error message. A period failure disables only
Desde/Hasta, leaving Dashboard/History and chain/category/product controls
available. Other filters degrade independently. No redesign, auth changes,
focus listener, polling, fixture fallback or model execution was added.

## Evidence gates

- PF01–PF08: repository tests prove the exact single request, optional upstream
  chain equality, visible union, sorting, deduplication and no fabricated month.
- PF09–PF11: isolated PostgreSQL tests execute GRANT/RLS for anon, ADMIN,
  VIEWER, inactive and no-grant users. Draft versions remain absent.
- PF12–PF15: UI tests exercise period failure with usable Dashboard, Historical
  table/pagination and other controls; error messages remain control-specific.
- PF16: fixed SHA-256 of migration 008 plus unchanged existing view definitions.
- PF17/PF18: explicit remote read-only audit checks 39,270 facts, UNKNOWN/NULL,
  model counts zero, exact migration history, business/lineage checksum and
  fresh private readback using the original `verify_remote_facts` verifier.
  It recomputes the original dataset SHA, not just the count.

The original SHA is
`3a092905dc699a762be0ca3f459ff9af31c9045d5cc06f6407453b457ac969e4`.
Fresh postflight recomputation passed for all 39,270 facts, with 0 fabricated
available_at, 0 loaded scope/value blockers and 0 model/vintage/Champion rows.
Private audit artifacts remain ignored under `outputs/prd09_2e13/`; no rows,
XLSX, passwords, tokens or service credentials are published.

Run the rollback and native Next builds sequentially: both frameworks generate
route types under `.next`. If checking types again after the rollback build,
regenerate native Next types with `npx next typegen` before `npx tsc --noEmit`.
CI already uses sequential builds; do not mix their generated type artifacts.

The two historical backend tests that excluded only migration 008 now also
exclude the explicitly authorized read-only migration 009. They still require
exactly seven base migrations and no runtime imports of reconciliation code.
No statistical/ML engine or historical certification implementation changed.

## Read-only administrative audit

Requires the normal Supabase CLI login and exact linked project
`bskoyqhbgrycpwhydnnr`; never extracts a browser session or bypasses UI auth.
Run `scripts/audit_portal_periods.py` with a fresh private output directory.
Preflight requires exactly migrations 001–008. Postflight additionally requires
the approved private plan, preflight evidence directory, verified ADMIN ID,
and migration 009. It performs SELECT in read-only transactions only; it cannot
apply a migration, bootstrap data, update users or execute a model.
SQL role probes are administrative RLS tests, not proof of real browser login.

## Final authenticated acceptance

The user already confirmed Dashboard 18 scopes / 1,010 products / 39,270 facts /
2026-07, real History rows, selected-chain/product filters and floating Lottie
on the base release. Preserve that acceptance; only validate this correction:

1. All chains: no generic error; both period selectors enabled.
2. History: table and period selectors visible, no generic error.
3. Select the actual-bearing Al Super scope: its period options update.
4. Return to all chains: global visible months return without an error.
5. Switch windows/minimize: no reload, same module/filters/page; assistant
   remains animated and dynamic. Intelligent API and voice remain disabled.

Record the GitHub/Netlify matching SHA and GitHub Actions result. Browser
acceptance is PENDING until observed or explicitly confirmed by the owner;
fixture tests and administrative SQL probes do not substitute for it.
