# PRD 09.2E1 — FT-SELECTION-2.0

The single source of numerical thresholds is `services/forecast_engine/selection_policy.py`.
`ForecastPolicy` reads the matching engine thresholds from it. Existing
`E3-GATES-1.0.0` evidence and its policy are deliberately unchanged.

For each product, the retrospective runner evaluates statistical, ML, and
ensemble alternatives only on the exact same product/origin/target/actual
pairs. Its `evaluation_signature` includes chain, category, product, objective,
issue period, mode, evaluation origins and targets, horizons, and actual
observation identifiers. When the provider lacks a physical observation ID,
the signature uses an explicit logical product/period key; this does not
invent `available_at` or imply point-in-time certification. A scope leader
and product recommendation are different objects. Scope-only metrics cannot
be used to recommend a product model.

`suggested_reference` and `suggested_references` are preview metadata, not
published forecasts. They never write to `champion_registry`. A recommendation
requires twelve valid nonnegative horizons, finite WAPE and Bias, and at least
six paired product observations. Ensemble additionally requires 24 observations
and three windows. The ranking is WAPE, absolute Bias, stability, recent
deterioration. A gain under 0.25 WAPE points is a practical tie; a statistical
baseline wins that tie. Bias deterioration over five points and critical
H1–H3 deterioration over two points fail the challenger. Confidence is based
on comparable observations, windows, and the number of valid candidates.

Bands are calibrated separately for each horizon. They require at least three
residual pairs at PRODUCT, otherwise CATEGORY, otherwise CHAIN, otherwise
`INSUFFICIENT_BAND_EVIDENCE` with NULL P10/P90/P95. P50 remains the central
*provisional* estimate. H1 residuals are never used for H8 or another horizon.
`band_basis` and `band_observations` are returned with each horizon.

`E3-GATES-2.0.0` is implemented as an opt-in quality-policy version. The
additive SQL in `supabase/migrations/202610080011_e1_policy_v2_guard.sql` is
**prepared only**; do not run `db push` as part of E1. Until separately
approved/applied, keep Railway on `QUALITY_POLICY_VERSION=E3-GATES-1.0.0`
with its original threshold values. After migration approval, switch to v2
using `MINIMUM_IMPROVEMENT_POINTS=0.25` and
`CRITICAL_HORIZON_DEGRADATION=2`, while retaining history 18,
continuity 0.85/0.95 and Bias deterioration five. The prepared guard permits
v2 vintage freeze with exactly three v2 gates, but deliberately forbids v2
Champion promotion until the E4 human decision/PIT certification contract is
implemented. Old v1 vintages are not recalculated or changed.

FENDI AZUL production acceptance must be performed with an authenticated
user after the same GitHub, Railway, and Netlify SHA is deployed. Verify
product WAPE and Walmart:BD WAPE in distinct sections; stat/ML/ensemble
classification; suggested reference versus published Champion; and all
twelve per-horizon bands and evidence bases. No authenticated production
forecast is launched by the automated suite.
