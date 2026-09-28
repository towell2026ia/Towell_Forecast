# PRD 09.2E.1.2 — Session stability acceptance

Base: `665d59a196d3e432a9f8ae6179a095e40a156a84`.
Canonical production: https://towell-forecastia.netlify.app.

## Implementation contract

Auth events are classified before clearing state. Auth SDK verification runs
outside its synchronous event callback. A verified user and ACTIVE database
profile remain the authority; event IDs, browser roles, and headers are not.

For the same identity, TOKEN_REFRESHED, repeated SIGNED_IN/INITIAL_SESSION and
USER_UPDATED preserve the repository and mounted shell. No focus/blur/visibility
listener or auth polling is added. Concurrent initial events are coalesced;
stale async completions cannot reopen logged-out or previous-user access.
The visual key is profile ID only; generation is solely cancellation control.

SIGNED_OUT clears data and disposes the repository once. A different identity
clears the old portal immediately and verifies the new user before mounting.
Final unmount disposes the remaining repository. PASSWORD_RECOVERY hides the
portal, retains the existing secure recovery flow, and redirects to
`/update-password`; redirect unmount owns repository disposal.

## Executable tests (not live browser evidence)

`tests/portal/session-stability.test.tsx` exercises SS01–SS18 using the real
portal shell and synthetic repository/Auth fixtures, including module, chain,
category, product, periods, search, history page 2 and assistant continuity.
It counts constructors, mount/unmount and dispose calls on focus/token refresh,
same-user events, identity change and logout, and covers recovery/security.

`tests/portal/canonical-site.test.ts` and `recovery.test.tsx` cover typed config,
URL validation, production vs development redirect contracts, reset requests,
verified recovery callbacks, and password updates. All emails/passwords/keys in
these tests are synthetic; no real recovery email or password change is needed.
CI preserves all existing backend, engine, security and Docker gates and adds
native Next/Netlify build and bundle-secret scanning beside the Sites build.

## Mandatory real acceptance — owner/browser

Until demonstrated on the deployed new SHA, each item below is PENDING, not
PASS. Automated fixture tests cannot substitute for real authenticated reads.

1. Verify `/api/portal-version` SHA matches `origin/main` and canonical URL.
2. Login ADMIN on Netlify. Dashboard: 18 scopes, 1,010 products, 39,270
   observations, latest period 2026-07.
3. Histórico: page 1, Siguiente, Anterior, and all four chain/product/category/
   period filters plus search. If a read fails, use the existing controlled
   diagnostics; do not redesign the query or change the corpus speculatively.
4. Choose Histórico with chain/product, go to page 2. Change windows for 30s,
   return, minimize and restore: same module/filters/page, no loading screen.
5. Open assistant: visible original animated Lottie, module continuity, dynamic
   context, no fixed FENDI/Walmart scope. Intelligent API and voice OFF.
6. Logout: return to login, no historical data or assistant remains.
7. Confirm owner saved Supabase canonical Site/Redirect URLs and signup remains
   disabled. Recovery redirect contract may be validated with the safe tests
   rather than changing a real password.

Record GitHub SHA, GitHub Actions result, Netlify SHA and owner/browser evidence
in the delivery report. Final status remains
`BLOCKED_MULTICHAIN_UI_AUTH_PUBLIC` if real authenticated acceptance or canonical
deployment/configuration cannot be verified.

## Preservation boundary

No changes to migration 008, certified corpus/SHA, historical available_at,
backlog/bootstrap, model implementation or frozen vintages. Historical writes,
productive model executions, new vintages and Champion changes in this PRD: 0.
The previous 18/1,010/39,270/2026-07 certification is preserved, not re-certified
by an unauthenticated login page.
