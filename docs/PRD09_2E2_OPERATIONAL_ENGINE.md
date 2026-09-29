# PRD 09.2E.2 — Operational multi-chain preview

Base: `b33e34433eb2895800240475f52bd6522b1a7113`, `origin/main` in `towell2026ia/Towell_Forecast`.
This phase adds read-only operational previews, not productive publication. The owner accepted E1.3 on the base SHA.

## Runtime contract

Netlify sends the existing Supabase session's access token only to the configured HTTPS Railway origin. FastAPI verifies it via Supabase Auth `/auth/v1/user`, verifies issuer/audience/expiry/session/user identity, then reads ACTIVE profiles and chain permissions under the same token. Client roles, IDs and local headers are not authority. Supabase RLS remains the final read boundary. No service role, database password or new SQL migration is required. See [Supabase user verification](https://supabase.com/docs/reference/javascript/auth-getuser) and [JWT/RLS](https://supabase.com/docs/guides/auth/jwts).

`SupabaseDataProvider` issues only GET requests, bounded to 500 rows/page with deterministic ordering. It requests SALES, maps it to Venta, joins actual product/category catalogs, rejects duplicate/conflicting or foreign identities, and preserves zero, missing months and literal availability. Products are identified by chain UUID + product UUID, never description/UPC alone.

`OperationalMultiChainForecastRunner` reuses `forecast_dataset`, `ForecastPolicy` and the existing statistical/ML candidates. It processes each authorized scope separately, with at most two concurrent workers and a bounded queue. An all-scope parent processes children sequentially within its worker; it never sums overlapping parent/child scopes. Child cutoffs come from each scope's last SALES observation, not today's date or the global historical period filters. Divergent cuts are labelled `CUTS_NOT_ALIGNED`.

### Temporal boundaries

- `RETROSPECTIVE_TRAINING`: reads `portal_monthly_observations_current`. NULL `available_at` stays NULL. Uses the contiguous observed history ending at each origin; absent periods are not filled. Validation and holdout metrics are explicitly retrospective; certified WAPE/Bias remain NULL and temporal certification remains false. Status is `PROVISIONAL_TEMPORAL_UNKNOWN`.
- `POINT_IN_TIME`: reads the unchanged `monthly_observations_current` temporal view, requires non-null availability and retains the strict existing normalizer, origin-aware history and leakage checks. E2 previews still do not publish even when availability is known.
- A mixed-mode run is rejected. No artificial historical availability dates, new observations or import evidence are written.

Pre-launch/inactive/insufficient products have lifecycle metadata without fabricated zero forecasts. Eligible cold-start products can use statistical output without ML. The existing fourteen statistical and three ML candidates remain; XGBoost/LightGBM are not introduced. ML failure/nonfinite output falls back to valid statistical output. Retrospective ensemble failure retains the statistical preview; a failed statistical forecast fails the child rather than borrowing a demo. Empirical bands need at least three residuals, are labelled RETROSPECTIVE_PRODUCT/CATEGORY/CHAIN, and otherwise retain NULL tail quantiles.

### Selection and persistence

The existing `ChampionRegistry` is read-only here. A local incumbent is compatible only when chain UUID, Venta objective, chain scope, environment and dataset context match exactly. Legacy entries missing that context are not borrowed. Current official Supabase model tables are empty; this phase does not manufacture an official Champion. Candidate leader/challenger and no-degradation information are previews; automatic promotion is always false. Registry publication, vintage creation and Supabase output writes are absent from the E2 path.

SQLite stores `forecast_jobs` (mutable progress) and `forecast_previews` (immutable content-hashed results) on the existing Railway volume. Snapshot hashing includes ordered identity/period/objective/value/literal availability and source/version identity. Preview idempotency also includes mode, cutoff, environment, policy, product scope and engine version. Repeated completed inputs reuse the preview. Jobs keep user ID but never JWT. Interrupted jobs are marked FAILED on restart because credentials are deliberately not persisted; completed previews remain queryable with freshly verified authorization.

GET status/result/latest revalidate access to every stored scope. Summary responses omit all product horizon arrays; selecting a product returns only its twelve horizons. No historical corpus is transferred to the browser for calculation. The browser session repository and existing Lottie/auth/recovery components remain unchanged in lifecycle.

## API

All routes require `Authorization: Bearer <Supabase access token>`.

| Route | Result |
| --- | --- |
| POST `/api/forecast/preview-runs` | 202, async job; ADMIN/assigned EDITOR only |
| GET `/api/forecast/preview-runs/{job_id}` | Current safe progress, freshly authorized |
| GET `/api/forecast/preview-runs/{job_id}/result?product_id=...` | READY only, per-product horizons optional |
| GET `/api/forecast/preview-latest?chain_id=...&product_id=...` | Latest authorized completed/running preview; no chain filter means all-scope jobs only |

POST defaults: `chain_id=null` (all execution-authorized scopes), `product_id=null`, `objective=Venta`, `issue_period=null` (data-derived), `mode=RETROSPECTIVE_TRAINING`. Product execution requires an explicit authorized chain. A manual cutoff cannot exceed the scope's latest actual period. Per-user `FORECAST_RATE_PER_MINUTE` remains enforced. No `/publish`, `/promote` or `/freeze` endpoint is added; older assistant/official runner routes fail closed in E2.

## Deployment gate and exact configuration

Only apply the following after complete tests and CI PASS. Existing Dockerfile is `Dockerfile.api`; keep the existing service, repository/main and mount `/var/lib/forecast-towell` (owner confirmed mount). One service replica, no parallel Python process workers. Leave Railway-generated PORT alone and remove stale manual API_PORT/GIT_SHA overrides so service routing and version follow the actual deployment.

Remote preflight found the prior deployment failed with `PermissionError` creating the mounted historical directory. The container entrypoint therefore prepares ownership only on the verified mount/SQLite parent and existing SQLite/WAL/SHM files, without recursive changes or deletion, then permanently drops to the existing `forecast` user before running the API. CI checks the API process is non-root and verifies volume restart persistence. No `RAILWAY_RUN_UID=0` workaround is required.

Railway variables (publishable key must be supplied in Railway, not committed):

```dotenv
APP_ENV=production
API_HOST=0.0.0.0
FRONTEND_URL=https://towell-forecastia.netlify.app
RAILWAY_DOCKERFILE_PATH=Dockerfile.api
ASSISTANT_PROVIDER=local
DATA_PROVIDER=supabase
SUPABASE_ENABLED=true
SUPABASE_URL=https://bskoyqhbgrycpwhydnnr.supabase.co
SUPABASE_PUBLISHABLE_KEY=<existing project publishable key>
OPERATIONAL_PREVIEW_ENABLED=true
PERSISTENCE_PROVIDER=sqlite
PERSISTENCE_MODE=hosted-volume
STATE_DIR=/var/lib/forecast-towell
SQLITE_PATH=/var/lib/forecast-towell/historical/historical.sqlite3
RESEARCH_PROVIDER=local
MAX_FORECAST_RUNS=2
FORECAST_RATE_PER_MINUTE=10
AI_ASSISTANT_API_ENABLED=false
HISTORICAL_RUNNER_ENABLED=false
MONTHLY_RUNNER_ENABLED=false
LEGACY_PILOT_ENABLED=false
OPENAI_ENABLED=false
VOICE_ENABLED=false
DEEP_RESEARCH_ENABLED=false
```

`RESEARCH_PROVIDER=local` retains the pre-existing settings contract; previews pass `research=None` and never execute the legacy research provider. Local router flags can retain defaults; they are not invoked by E2. No HMAC secret or legacy actor allowlist is required for the verified Supabase boundary. No secret/service key is used for interactive reads.

Netlify retains existing Supabase public configuration, site URL and recovery URLs. Add:

```dotenv
NEXT_PUBLIC_FORECAST_API_URL=https://towellforecast-production.up.railway.app
```

The owner confirmed that public variable is prepared. Trigger/build the same GitHub commit on both providers. Do not change Supabase Auth URLs, disable_signup, RLS policies, migrations 008/009 or historical facts.

## Real acceptance and rollback

After CI, verify Railway `/api/live`, `/api/health`, `/api/ready`, `/api/version`; compare its commit with Netlify `/api/portal-version` and GitHub main. Anonymous POST must be 401; VIEWER POST and foreign EDITOR/result scopes must be denied. Technical readiness is not authenticated E2E proof.

The real ADMIN signs in at Netlify, opens Motores, runs a scope with sufficient history, checks the data-derived issue and H1/H12, one regular product, an intermittent product if present, one insufficient/cold-start product and the all-scope batch. Record scope counts/cuts, actual eligibility, selected model distribution, retrospective metrics, null certification and zero official writes. Do not close E2 as PASS based only on synthetic fixtures or HTTP health.

Restart the service and confirm a completed preview remains accessible under a new verified session. An interrupted job must safely show FAILED/PREVIEW_INTERRUPTED; it is not silently restarted with stored credentials. A volume is not a backup: use the existing private SQLite backup procedure.

For rollback, disable OPERATIONAL_PREVIEW_ENABLED and SUPABASE_ENABLED together, set DATA_PROVIDER=normalized, leave SQLite and the volume intact, and roll back to the last CI-green image. Keep assistant/OpenAI/voice/research/legacy flags OFF. The E1 read-only historical portal remains Supabase-backed independently of E2. Never delete or rewrite certified historical data to recover a preview deployment.
