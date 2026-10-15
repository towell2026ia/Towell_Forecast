# Commercial filters and Periodos dashboard

PRDs 09.2E5-01B-B, 09.2E5-01B-C and 09.2E5-01B-D are implemented in that order:
authorized catalog facets, stored-data comparison, then the Periodos dashboard.
No model execution, mapping application, migration, adoption or historical repair
is part of this release. Development of AI agents remains outside this scope.

## Commercial filters

The seven global selectors are Chain, Category, Product Type, Model, Color,
Size/Presentation and Product. Search also matches the existing opaque ITEM/UPC
metadata. All authorized products are reachable; there is no first-100 product
cap. Unclassified products remain available through the missing-attribute option.
Equal product descriptions on different chains have chain-qualified labels.

`GET /api/forecast/commercial-catalog` verifies the caller's Supabase JWT, active
profile and READ permission. Every source query uses that same JWT and RLS, not a
service credential. Sources are products, categories, chains, versioned import
metadata and the current commercial view. The existing configurable analyzer
discovers proposals from the authorized corpus; no closed model list or private
mapping packet is shipped to the browser. The result is request-scoped, no-store,
and rate-limited. It contains no writer or engine invocation.

Fresh APPROVED assignments use normalized dictionary names. Unmapped, stale or
unapproved assignments are PENDING_CLASSIFICATION and their transient proposals
are clearly identified as pending. UNKNOWN differs from explicitly documented
NOT_APPLICABLE. The endpoint does not insert these proposals into Supabase.
Existing commercial tables remain empty until separately authorized mapping.

The browser caches classification reads only within a repository/session instance.
Reloading the portal reads new products and proposals without a code change.
Discovery can depend on the caller's authorized corpus; a transient proposal is
not an approved cross-chain equivalence. Model, color, size and type stay separate.

Facet changes clear dependent attributes and product identity. Selecting a product
binds its authoritative chain/category. Shared repository filtering applies the
same product UUID set to historical reads, summaries, reports and database views.
Large fact reads use bounded product-ID batches, never a single oversized IN URL.
Failed/mismatched classification or historical reads do not display stale reports.

## Comparative chart

The selected comparison year has twelve calendar positions. The two previous
calendar years use grouped SALES columns, January through December. January to
September of the selected year uses actual-sales lines; October to December uses
only existing saved forecasts for those exact targets. Pedido, Enviado, validated
customer forecast, Statistical, ML, Ensemble, provisional Cold Start and adopted
Towell series remain independently available through checkboxes.

Nine actual positions do not imply nine available observations. The real catalog's
latest actual period is July 2026. August/September stay null; they are neither
zeros nor forecasts disguised as actuals. Likewise, FENDI's earlier incomplete
calendar year remains incomplete. Both twelve-month historical axes are displayed,
but a full year of actual data cannot be claimed when evidence is missing.
Coverage counts make these limitations explicit.

Saved C1 issued at July 2026 is not recalculated or shifted: the chart's October,
November and December values are its H3, H4 and H5. The matrix retains all H1-H12,
August 2026 through July 2027. Late issuance/elapsed-month warnings remain visible.
Only a valid stored decision supplies adopted Towell values. An Ensemble
suggestion never becomes adoption by changing a selector or toggling a series.

Global period bounds apply to exact absolute months in every view, including
historical comparison years. Narrowing the range can intentionally leave earlier
comparison bars empty. The chart's comparison-year control changes its calendar
comparison and January-September indicators, not the immutable calculation cut.

## Dashboard and arithmetic

Periodos retains the existing FORECAST Towell navigation and identity. A compact
filter summary can expand all selectors. Executive indicators precede the primary
combined chart; series controls, coverage, H1-H12 matrix, product detail and existing
chain/category/month totals follow. There are no calculation/adoption buttons in
this read-only workflow.

Indicators show observed January-September Venta, Pedido and Enviado, paired
Fill Rate, forecast availability and adopted coverage. Fill Rate is the ratio of
summed paired Enviado/Pedido, not an average of monthly percentages. Null remains
missing; genuine zero remains zero. The tooltip reports product coverage and
partial totals. Parent scopes with children in the authorized hierarchy are
excluded using parent IDs, never chain-name heuristics. Only parents present in
the filtered catalog contribute to the excluded-scope count.

Per-product detail retains the three candidate WAPEs as retrospective metrics;
they are not averaged into a fictitious consolidated WAPE or presented as LIVE.
Customer forecasts use only validated/frozen versions, selecting a compatible
version per product/target rather than summing revisions. Real-data lineage checks
and immutable C1 horizons remain in the report reader.

## Verification and deployment

Focused tests cover open/multiword models, FENDI/OXFORD, unknown attributes,
1,010-product reachability, documented not-applicable attributes, chain mismatch,
dependent resets, GET response contract, 9+3 boundaries, zero/null, partial coverage,
parent exclusion, exact date/decision filters and read-only interaction. Backend
route tests cover reader/editor/admin, invalid JWT, rate limiting and POST refusal.
Existing SQL checks exercise RLS, append-only history and referential integrity.

Local Playwright uses a private, read-only Supabase export, not invented commercial
observations. It exercises 1,010 product options, dynamic model options, C1's twelve
targets, toggles and detail on desktop, 768px and 390px screens. Private evidence
stays under `outputs/prd09_2e5_01b_bcd`, excluded from Git, TypeScript and lint.
The public application imports no such evidence. Authenticated production UI
validation is distinct from this local replay and must be reported separately.

Run the portal suite, backend suite, commercial schema checks, lint, TypeScript,
both frontend builds and bundle checks before release. GitHub Actions runs all
three CI jobs. Check Netlify's portal-version SHA and Railway's ready/version
response after deployment. Re-read Supabase row fingerprints/counts afterward:
products, imports, observations, C1, horizons and decision history must match.

## Safety and rollback

Selection stays ON with no new decision. Capture, LIVE/learning, Champion, Vintage,
Deep Research, OpenAI agents, inventory and official publication are not enabled.
There is no C2 creation, source-identifier mutation or commercial DML/DDL.

Rollback is a reviewed Git revert and redeployment of this release. It requires no
SQL migration reversal, deletion or data repair. Keep the private before/after
evidence and existing commercial schema/history. Any future productive mapping,
forecast adoption, engine features or AI agents require their own authorization.
