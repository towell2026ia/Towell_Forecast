# PRD 09.2E.2.2A — Retrospective metrics closure

Base: `dcbd3b5c3894e114dc8f0aa7f51babf746117d33`, `origin/main`.

## Cause and correction

The old generic engine reserved disjoint training, selection and certification windows with a 12-month gap. For 19 calendar months its first selection origin fell after the issue period. Training samples existed but selection rows were empty. The retrospective wrapper also renamed `validation_*` fields while the statistical UI read their old names. Chain-level GETs omitted product horizons, hiding the selected model in the ranking.

`services/forecast_engine/retrospective.py` now provides `RETROSPECTIVE_EVALUATION` only within `RETROSPECTIVE_TRAINING`. The strict POINT_IN_TIME branch and ForecastPolicy defaults remain unchanged. Statistical functions and ML factories are reused directly; no legacy payload builder, spreadsheet or pilot CSV is used.

Statistical evaluation uses at most the last 12 eligible calendar origins, expanding observed history, H1/H3 with at least six contiguous observations and H6/H12 with at least twelve. Each model's actual history requirement and classification applicability still apply. Missing actuals are not zeros. Every product retains all 14 catalog entries with an evaluated result or reason. Selection compares a common origin/target set per product; scope metrics pool real/prediction pairs rather than averaging product percentages. Selected-statistical scope metrics include literal H1/H3/H6/H12 evidence.

Each ML fold creates fresh preprocessing and a fresh model fitted only to TRAIN targets at or before the origin. Vocabulary, lags and rolling features are origin-bound. The three existing NumPy factories remain the only models. Sample threshold uses the unchanged `min_train_observations`; at least two descriptive origins are required for VALIDATED. TRAINABLE, VALIDATED, SELECTED and operational refit readiness are separate. The selected model is refitted on all data known by the issue period, then produces twelve direct future months. This is descriptive model selection, not an independent certification holdout.

The operational preview version increments its cache identity. Existing SQLite results are not overwritten. An older preview remains readable with an explicit refresh notice; only **Calcular vista previa** starts a run. No model executes on tab, filter, focus or visibility changes.

## Private read-only validation

The requested real chain was read from the linked published historical view in READ ONLY transactions, and evaluated locally using the same normalizer, eligibility policy, forecast function, preview mapper and quality gate. Private commercial measurements and lineage evidence are kept only in ignored `outputs/prd09_2e22a/RESULT.md`; they are deliberately excluded from this public repository. This local computation does not replace Netlify → user JWT → Railway production acceptance. No remote write was performed.

Bias retains the existing backend convention: `100 * sum(actual - prediction) / sum(actual)`. A subsequent SELECT-only fingerprint check matched the previously certified historical baseline with availability still UNKNOWN/null. No dates were fabricated.

## Portal and contracts

Both model tabs are functional. Statistical coverage/catalog/ranking and product metrics are distinct; ML scope training information is distinguished from product validation metrics. Missing product metrics never borrow scope scores. The adapter supports old renamed retrospective fields, but a literal null in the new contract has priority over an old score. Chain summaries retain H1 model/classification without exposing every product horizon.

Backtesting metrics and future forecasts are separate. H1–H12 tables retain exact backend values. The executive view puts the future table first and collapses the historical block. The consolidated comparator includes WAPE, Bias and literal H1. Chart lines use only delivered forecasts: actual sales, selected statistical, selected ML and Towell; forecasts of unselected ML candidates are not invented. Aggregate Stat/ML values come from the backend. No global parent/child sum or aggregate quantile is fabricated.

## Executed local gates

- Backend: 487 tests, OK, seven pre-existing environment/private-fixture skips; coverage 88% (minimum 81%). The idempotent legacy-job test now uses a test-only rate allowance for its polling; production rate limits are untouched and separately tested.
- New 19-month closure regression: 11 PASS, including all models, fresh TRAIN preprocessing, future-feature invariance, pooled metrics, null H12 evidence, twelve future months, unknown availability and certification isolation. Synthetic six-product fixture: 900 samples, three ML candidates validated, thirteen statistical models evaluated, Holt-Winters not applicable. These synthetic results are not substituted for private production measurements.
- Existing suites: statistical 12, ML 11, ensemble 18, decision 30, closure 25, generic strict-PIT forecast 14 PASS; engine audit PASS.
- Portal: 215 tests PASS across 17 files, including 15 new closure cases and Auth/session/filter/assistant regressions.
- Local SQL: baseline PASS; bootstrap 13/13; security 36/36; RLS 28/28. No remote SQL migration or write was applied.
- Critical Python lint, dependency vulnerability audit, frontend lint (zero errors; four ignored `.venv` dependency warnings), regenerated Next TypeScript, native Netlify and rollback builds, and both browser bundle security scanners PASS.
- Docker is unavailable on this workstation. GitHub Actions must demonstrate Docker build, non-root startup and volume restart; a local Docker PASS is not claimed.

## Governance and final acceptance

Historical writes / available_at mutations / Supabase forecast writes / schema migrations / Champion changes / official vintages: **0**. No authentication, RLS, research, OpenAI or voice changes. SQLite preview persistence and `official_publication=false` remain.

The computer-use browser kernel failed twice (`helper_unknown_error`, sandbox setup refresh). No authenticated screenshot or production preview was obtained. A local calculation on real rows and healthy public endpoints do not replace the web acceptance gate.

After GitHub CI and deployment SHA alignment, open Netlify → Motores → Al Super / Todos and press **Calcular vista previa** once to generate the new cache version. Verify real coverage counts, all statistical catalog entries, three ML results, WAPE/Bias, August 2026 through July 2027, and selected-product/chain charts. Check module/window round-trips and no official publication. Do not share credentials. Until that fresh authenticated acceptance exists, the honest final state is `BLOCKED_MULTICHAIN_ENGINE_METRICS_CLOSURE` even if all code/deployment gates pass. Deployment SHA/CI results are recorded in the delivery rather than guessed here.
