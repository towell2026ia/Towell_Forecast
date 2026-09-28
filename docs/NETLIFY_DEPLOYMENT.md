# Netlify canonical deployment

Production: https://towell-forecastia.netlify.app

Repository: `https://github.com/towell2026ia/Towell_Forecast`, branch `main`.
Use the existing connected site. Do not create another site or repository.
Sites is rollback only; no Sites production publication is part of this PRD.

## Build settings

`netlify.toml` selects `npm run build:netlify`, output `.next`, Node 22.
It runs native Next.js; the existing Vinext/Vite/Cloudflare build is retained
through `npm run build` / `npm run build:sites`.
Use Netlify's automatically managed Next/OpenNext integration; do not pin
another adapter. See [Netlify's Next documentation](https://docs.netlify.com/build/frameworks/framework-setup-guides/nextjs/overview/).
Set the site's base directory to the repository root (where `package.json` is).

Public variables in Netlify, available to build AND functions/runtime:

```dotenv
NEXT_PUBLIC_SUPABASE_URL=https://bskoyqhbgrycpwhydnnr.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=<project public anon or publishable key>
NEXT_PUBLIC_SITE_URL=https://towell-forecastia.netlify.app
```

The existing alternative `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` is supported;
a nonempty publishable value takes precedence over the anon variable. Do not
paste a service role, secret key, database password, or Supabase PAT into any
browser variable. `.env.example` contains empty placeholders only.

`NEXT_PUBLIC_SITE_URL` is also supplied in `netlify.toml`. HTTPS is required,
with no credentials, path, query, or fragment. A trailing slash is normalized.
Localhost HTTP is permitted only in development. Never set `NODE_ENV` to
development on the production site.

The build records Netlify's `COMMIT_REF` as `NEXT_PUBLIC_GIT_SHA` (local builds
use Git HEAD). Do not manually override it with an unrelated SHA.

## Supabase owner actions

In Authentication → URL Configuration, save:

- Site URL: `https://towell-forecastia.netlify.app`
- Allowed Redirect URL: `https://towell-forecastia.netlify.app/update-password`
- Optional development URL: `http://localhost:3000/update-password`
- If developing with the preserved Vinext preview, additionally allow
  `http://localhost:5173/update-password`.

A Sites recovery URL may remain temporarily allowlisted for rollback, but it
must not be the canonical Site URL. Keep public signup disabled. No new users
are created by this release. These settings require owner confirmation; the
code/build passing does not establish that they are saved remotely.

Recovery uses the configured canonical origin in production even if initiated
from a rollback host. Development localhost may use its current origin.
Responses do not reveal whether an email exists. Password updates still require
the verified recovery session, use the public SDK, and end at `/login`.

## Verify each release

After pushing `origin/main`, wait for GitHub Actions AND Netlify deployment.
Check `https://towell-forecastia.netlify.app/api/portal-version`:

```json
{"git_sha":"<same full SHA as origin/main>","site_url":"https://towell-forecastia.netlify.app"}
```

This no-store endpoint exposes only public release metadata, not credentials
or session data. A 200 login page alone is not proof of deploying the new SHA.
Verify `/login`, `/update-password`, and the Lottie asset (200), then follow the
authenticated acceptance in `PRD09_2E12_SESSION_STABILITY.md`.

If deployment fails, inspect Netlify's build/function logs using the correct
site/account; never export complete env dumps or log JWT/recovery/passwords.
Existing `/api/portal-validation` verifies the user's JWT and ACTIVE profile;
its read diagnostics allow only endpoint/table/status/code/operation. Do not
use service-role credentials to bypass the acceptance test.

## Rollback and boundaries

Prefer restoring the previous successful Netlify deploy, or reverting the
release commit and pushing main. Preserve the Sites build as temporary
rollback; do not maintain divergent application source between hosts.

This release does not change Railway, the SQL schema/migration 008, certified
historical data, temporal availability, vintages, Champion, or models. The
backend remains SQLite/normalized. Intelligent assistant API, OpenAI, Deep
Research and voice stay disabled. No productive forecast run is authorized.
