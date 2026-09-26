# PRD 09.2D.2.3 — Hierarchical fact grain

Certification: **PASS_HIERARCHICAL_GRAIN** under the owner's authorized CP31A–H correction. This is an isolated administrative pre-ingestion layer, not a runtime activation or permission to publish historical data. The 1,538 unknown-scope keys and 720 quantity conflicts remain blocked.

Base: `43c02e5c7d58f2261c07afa8ca534b5299625f84`. The approved baseline and all frozen evidence remain unchanged.

## Ownership is not membership

Raw facts without a commercial-unit discriminator belong to the evidenced parent chain. A product may independently belong to several commercial units, or have no known child membership. Neither situation invalidates an evidenced parent fact or creates child observations.

The generic resolver accepts a private, hash-pinned `SourceScope` per actual-value source. It records source hash, sheet, parent, grain, explicit chain/format columns, evidence, approval, profile hash and owner-rule hash. Every actual-value sheet requires a scope; unproven scopes stay blocked. There are no retailer, pilot, product, or SKU branches in the resolver.

Supported grains are `PARENT_CHAIN`, `COMMERCIAL_UNIT`, `EXPLICIT_ROW_UNIT`, and `UNKNOWN_SCOPE`. Valid explicit row dimensions take precedence over source context. Missing or formula dimensions do not silently fall back to a parent. Independently certified literal child values retain their unit scope; business membership alone never certifies quantity ownership.

The canonical identity is `(fact_scope_id, stable_product, period, metric)`. Parent identities use stable ITEM identifiers where available, never descriptions. Parent and child identities are distinct even when their textual codes coincide. Explicit-row and sheet-owned facts for the same commercial unit share a unit identity.

## Raw and derived representations

The approved private configuration treats the 2024/2025 raw Base/BD WM sources as parent-level, and the 2026 master as explicit-row units. This corpus-specific configuration is not embedded in the engine.

Formula quantities are excluded. A restricted exact-ITEM XLOOKUP dependency parser can document a derived child representation's raw source; it does not evaluate Excel, read cached values, or treat an IFERROR fallback as a proven zero. Equal quantities alone never prove duplication. Unsupported lookup shapes remain unverified formulas.

One physical source fact maps to one fact scope only. Memberships are metadata. No fan-out, arbitrary child selection, allocation, or automatic parent/child rollup is permitted. The aggregation helper rejects mixed scopes, periods/metrics and duplicate canonical keys.

## Private reconstruction results

Counts describe observations/keys, not quantities to be summed across scopes.

| Measure | Before | After |
| --- | ---: | ---: |
| Selected observations | 32,624 | 39,270 |
| Former membership blockers | 9,016 | 0 still scope-blocking in that cohort |
| Current true scope blockers | — | 1,538 |
| Current value-blocking keys | 729 | 720 |

Accepted observations: 8,558 parent, 2,260 certified sheet-owned child, and 28,452 explicit-row child. The 9,016 former membership blockers are individually traced: 162 now valid parent/multi-child and 8,854 valid parent/unknown-child. All 12 previous review groups are accounted for. Resolving a scope does not authorize selecting a contradictory value.

Current quantity conflicts: 464 parent, 255 child, one same-cutoff, zero cross-snapshot. The original 729 decisions remain separately traced: 106 separate accepted fact scopes, 190 pending scope, 39 parent conflicts, 393 child conflicts, and one same-cutoff conflict. These are different cohorts/counting grains and must not be subtracted interchangeably.

There are 4,488 evidenced derived formula representations excluded. These formulas were already excluded from literal facts in the previous baseline; this is **not** a claim that 4,488 of the original 729 literal conflicts were resolved.

Coverage is reported separately. Among accepted parent products, 167 have one known child, three have multiple children, and 43 are parent-only. 119 parent products have at least one selected period with incomplete child membership; this does not block the parent fact. There are 114 catalog-only child series. Scope/product series labels distinguish parent eligibility, child eligibility, catalog-only, and blocked, but all temporal/as-of eligibility remains blocked because availability is unknown.

The reconstruction has 18 scopes with accepted actuals, 1,010 scope/product identities, 50 categories, and periods 2023-01 through 2026-07. Genuine source periods are preserved; a file's year or a membership approval does not manufacture an observation date. Every selected historical observation has `availability_source=UNKNOWN` and `available_at=NULL`.

## Authorized legacy values and hierarchical scope certification

The owner approved replacing the former requirement that all 96 FENDI 2024 facts retain a BD label. Of that frozen cohort, 72 remain supported by literal child sources. The remaining 24 (December: eight products × SALES/ORDER/DELIVERY) previously came solely from raw Base and were assigned to the child using membership. The raw source has no explicit unit discriminator.

The original CSV and prior certified report remain byte/content frozen as `LEGACY_VALUE_GOLDEN`. Their responsibility is product, period, metric and value, not granting unsupported commercial ownership. A new ignored `hierarchical_scope_golden.json` certifies one evidence-supported canonical reference per legacy fact: 96 matched, 96 equal values, 72 BD, 24 parent, zero losses, changes, duplicate keys or unsupported child assignments. Conservative child-to-parent correction is allowed; parent-to-child promotion without independent evidence is not.

All 96 original literal facts remain traceable with unchanged quantities at the parent scope. For 72, separately retained certified literal child sources also exist. These independent scope representations must never be added together automatically. The 24 December facts have only raw Base sources and no independent literal child source.

The generic certifier matches pinned hash/sheet/cell lineage, validates the source assignment's stable identity and ownership evidence, and selects supported specificity. It does not match by value alone, use the legacy scope label as evidence, or add facts. If more than one equally specific canonical match exists, certification fails rather than choosing arbitrarily. Independent parent representations referenced by the same legacy cohort are explicitly marked as not summed.

CP31A–H independently test 96 preserved identities/values, 72 supported BD scopes, 24 December parent scopes, zero lost facts, changed values, duplicates and unsupported child scopes, plus byte/content immutability of the entire legacy CSV and the previous report. Public synthetic negative controls reject missing facts, changed values, shifted periods/metrics, product mutations, duplicate keys, unsupported child evidence and legacy-byte changes.

Separately, 1,538 literal keys on calculation sheets lack independent quantity-scope proof. Approved commercial membership does not resolve these holds. Remaining contradictory values require source/version authority, not selection of the largest or latest value.

## Artifacts and verification

`scripts/reconstruct_hierarchical_grain.py` consumes explicit private source rules, reviewed profiles, frozen baseline, membership authority, source-scope authority, legacy CSV and a separate owner-approved certification contract with SHA pins. Certification counts/expected scopes in that contract are assertions, not ownership authority. It refuses an existing reconciliation output, verifies source bytes, baseline and legacy CSV before/after, and replays reversed source/profile/authority order. Canonical results, functional decisions and hierarchical certification must match; a duplicate-byte manifest may identify a different physical filename without changing facts.

The required ten artifacts, plus hierarchical golden, certification contract, literal certification, determinism, regression samples and comparison, are under ignored `outputs/prd09_2d23/`. Previous draft artifacts were preserved in its ignored `before_authorized_close/` subfolder. No source files, private configurations, rows or credentials belong in Git.

Synthetic contracts cover CP01–CP35 semantics; real-cohort accounting, literal regression and CP31A–H require ignored local evidence and are skipped in a clean public checkout. Public CI never substitutes for the local real-corpus certification or removes factual scope holds.

The closure replay reproduced the same canonical facts and dataset SHA `3a092905dc699a762be0ca3f459ff9af31c9045d5cc06f6407453b457ac969e4`, including 39,270 literal checks with zero differences and unchanged source bytes. Backend, CP31A–H, existing engine suites, SQL baseline, security, frontend and GitHub CI verification are recorded in the private closure result. Local frontend lint may report four unrelated warnings inside the ignored Python environment. Docker is unavailable locally; the existing GitHub workflow runs Docker build, container health and persistent-volume restart checks.

Local closure tests: backend 320 discovered, 314 executable passed, six unrelated private-evidence tests skipped; all local CP31A–H tests executed and passed. Existing engine suites: 110/110 passed. Overall coverage: 86.31%; new resolver and certifier approximately 93% each. Engine audit, schema/security tests, frontend lint/typecheck/build and syntax checks passed. No frozen regression artifact was rewritten to make these tests pass.

The change is not imported by `create_app()` or active runners. Monthly ingestion, forecasting models, migrations, RLS, frontend and Railway configuration are unchanged. Supabase writes: zero. Runtime remains SQLite/normalized with Supabase, OpenAI, Deep Research and voice disabled. No cutover or bootstrap is authorized.
