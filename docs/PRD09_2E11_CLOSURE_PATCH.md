# PRD 09.2E.1.1 — Closure patch

Base: `0cea4c25e2b9ffb4f231b587b0d032608b02fbda`, Towell_Forecast/main.
The owner confirmed the real Dashboard: 18 scopes, 1,010 products, 39,270
observations, latest period 2026-07 and real coverage per scope.

## Diagnosis before query correction

Do not edit applied migration 008, RLS, facts, available_at or bootstrap.
The initial SQL page is accessible; that alone does not prove that the browser
request, period picker, cursor and transformations work with the real session.
The old generic `read_failed` exception concealed the failing operation.

Each of the five reads is independently inspected by the existing same-origin
`GET /api/portal-validation` using the real SDK session bearer, verified through
Supabase Auth and an ACTIVE matching database profile. This is a SELECT-only
diagnostic, not a privileged provider or Python identity bridge. A failed read
does not skip the other probes. The aggregate attestation stays backwards
compatible when all reads succeed.

Controlled errors retain exactly: logical endpoint, table/view, HTTP status,
allowlisted machine code and operation. Status 0 means no upstream HTTP status
is available, not a fabricated success. Upstream message/details/hint, stack,
token, headers, identity, filters and fact values are never logged.

Browser failures can report those same five fields to
`POST /api/portal-validation`. The route independently verifies Auth/profile,
limits the body to 2 KiB, rejects extra fields and non-allowlisted content, and
logs a separate `TowellPortalBrowserRead` marker. It never writes to Supabase.
Browser reports cannot attest success or grant permission. They are deduplicated
per session/operation; expired/disposed session callbacks cannot report.

An exact query correction and a cause-specific regression must follow actual
controlled diagnostics; do not guess that `.or()` or RLS is broken. Successful
local fixtures are not real-history acceptance.

## Existing global assistant restored

The authenticated shell mounts the existing ForecastAssistantErrorBoundary and
ForecastAssistant once, outside individual module views. The launcher remains
bottom-right in all eight authorized modules and disappears on auth unmount.
Scope, role, active module and all global filters come from the current profile,
catalog and filter state; no pilot text is hardcoded.

The existing animation asset is unchanged. Its SVG renderer, loop and autoplay
remain enabled; load failure uses AssistantMark. API and voice remain explicitly
OFF regardless of environment overrides. Sending is disabled and guarded; no
microphone control or permission request is mounted. The panel states:

> Asistente disponible en modo visual. La consulta inteligente y voz se
> habilitarán en la siguiente fase.

Context is display-only, not a frontend authorization claim. No assistant
message is sent, no model runs, no vintage or Champion is changed.

## Release and real acceptance

Tests cover controlled diagnostics, authenticated/forged reports, five
independent probes, all-module launcher continuity, scope/role/filters, fallback,
disabled API/voice and logout unmount. CI also runs existing backend, schema,
security and engine suites. Publish the exact pushed GitHub commit to the
existing public Site; do not configure new Netlify or enable integrations.

After publication the real ADMIN must verify Login → Dashboard → History page 1
(50 tuples) → Next → Previous → chain/product/category/period filters → assistant
scope/module → Logout. Verify the public Lottie asset separately with HTTP 200.
Record browser outcomes independently from executable fixtures. Until that
journey is demonstrated, final status is BLOCKED_MULTICHAIN_UI_AUTH_PUBLIC.

Private execution evidence lives under ignored `outputs/prd09_2e11/`. No tokens,
private XLSX, SQLite state or row-level datasets belong in GitHub.
