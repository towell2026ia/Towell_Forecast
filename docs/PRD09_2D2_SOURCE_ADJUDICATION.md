# PRD 09.2D.2 — Source adjudication

## Result and gates

**BLOCKED_BY_SOURCE_AUTHORITY.** This is a completed technical investigation,
not certification of a productive historical bootstrap. No remote data was loaded.

Base: `d66887cfda2d396e0ba77d846fb4fc332cfcd86c`, clean main, no divergence,
base GitHub Actions success. Fresh Towell-account authentication and read-only
Gate 1 verified project `bskoyqhbgrycpwhydnnr`: migrations 001–007, 28 public
tables, RLS 28/28, four private buckets, zero chains/products/observations/batches
and zero source-files objects. No migration, upload, bootstrap or runtime cutover.

The unchanged scanner reproduced 730 original conflicts, 18 auto-resolutions,
712 blockers and the frozen D.1 dataset fingerprint:
`7bcb9d459fd30b8e05e2f40e82a0eeb8bcf57128ec42a3342407df873c07fbc9`.
Original D/D.1 artifacts were not overwritten. Six files/five unique byte hashes
were verified; identical copies are parsed once without losing manifest lineage.

## Pareto (before adjudication)

Clusters include chain/year/metric, all competing Level A source hashes, sheets,
evidence level, reason, stable product identity pattern and observation grain.
Internal conflicts may have more than two source/locator representations; those
are retained, not artificially reduced to a misleading two-file comparison.

| Chain/year | Pattern | Metric | Blocked facts |
| --- | --- | --- | ---: |
| Walmart 2024 | Internal detail contradiction | SALES | 306 |
| Walmart 2025 | Cross-source contradiction | DELIVERY | 119 |
| Walmart 2025 | Cross-source contradiction | ORDER | 114 |
| Walmart 2024 | Internal detail contradiction | DELIVERY | 107 |
| Walmart 2024 | Internal detail contradiction | ORDER | 51 |
| Walmart 2025 | Cross-source contradiction | SALES | 12 |
| Al Super 2025 | Internal detail contradiction | DELIVERY | 1 |
| Al Super 2025 | Internal detail contradiction | ORDER | 1 |
| Walmart 2024 | Same-cutoff contradiction | SALES | 1 |

Nine clusters sum to exactly 712. Top four cover 80% and 90%; top five cover 95%;
all nine cover 100%. These percentages describe investigation coverage, not
permission to select a value. Four grouped human decisions remain after the
objective rule; no manual approval was invented.

## Evidence and source authority

The policy engine is generic, server-side, opt-in to administrative scanning;
it is not installed into the active forecast or monthly ingestion runtime.
No chain-specific branches, product codes or pilot value rules were added.

Authority records bind chain, period, metric, product scope, source hash, sheet,
grain, evidence, approval and reviewer. Approved PRIMARY/SECONDARY_CONTROL must
have scoped SOURCE_AUTHORITY evidence; every competing direct representation
must have an explicit reviewed role. PRIMARY still cannot hide contradictory
rows within its own scope. No min/max/first/last/sum/average selects a disagreement.
Derived/disjoint-grain exclusion requires its own scoped GRAIN_DISJOINT evidence.

Conservative structural rule: three January 2025 exported measure headers in the
February source's Base sheet explicitly start with `Suma de` and identify their
period and metric. Header cells are read from the immutable source bytes,
hash/locator/content fingerprints verified, then classified SUMMARY_ONLY (C).
This does **not** prove that the workbook currently contains an active pivot
definition, that any file is an official revision, or that BAASE is globally
authoritative. It only distinguishes the labelled aggregated measures from the
competing literal operational detail. Applying the existing A-over-C hierarchy
resolves 118 January differences: SALES 4, ORDER 55, DELIVERY 59.

| Authority matrix role | Approved rule scope | Outcome |
| --- | --- | --- |
| SUMMARY_ONLY | Three verified source-header/January/metric scopes | 118 differences resolved |
| PRIMARY / SECONDARY_CONTROL | None objectively established for competing A detail | No approval |
| REVISION_SOURCE | No documented comparable-snapshot sequence | No revisions |
| EXCLUDED_DERIVED | No proven disjoint extra grain in remaining conflicts | No grain-based resolution |
| UNRESOLVED | Competing source/sheet/period/metric scopes | 594 facts remain blocked |

No filename, modification/creation date, directory order, “final” suffix, highest
value or maximum source period is evidence of authority or temporal availability.

## Aggregated Walmart source map and internal-conflict analysis

| Source/sheet | Observable structure and purpose | Known issue / authority |
| --- | --- | --- |
| 2024 sources / BD WM2024 | Wide historical monthly fields, explicit item, size/color, literal detail; “cierre de mes” context | 285 rows; 520 columns; closure label alone does not establish supremacy |
| 2024 sources / Base | Wide monthly fields, same explicit identity and descriptive grain, literal detail | 135 rows; 76 columns; conflicts with the other detail sheet remain unresolved |
| February 2025 source / Base | Explicit item; January aggregate-labelled measures, February ordinary monthly fields | 135 rows; 27 columns; January C rule only, February remains A |
| July 2026 multichain / BAASE | Explicit chain/ITEM/UPC/date, literal SALES/ORDER/DELIVERY, purchase type/format present | 11,512 rows; 33 columns; useful operational detail, not blanket authority over another A detail |

All 712 blocked facts and 2,352 Level A literal locators were checked against the
source row context; literal/formula status and normalized values matched the
private ledger. The inspected shared identity/dimension fields do not differ.
This does not establish absence of an undocumented business dimension.

The 464 Walmart internal contradictions span January–September 2024. Header and
row context do not demonstrate separate business entities or an embedded official
revision. Both sheets look like explicit-item monthly detail. No additional
dimension was invented; both representations and differing values remain in the
private ledger. The one December same-cutoff conflict has a separate private
dossier with hashes, cells, literals, identity, grain and workbook context.

Of the 245 cross-source conflicts, 118 January cases are explained by the
aggregated header. The 127 February cases still compare two A details. A later
file's coverage is not a validated revision sequence; none was approved.

## Independent Al Super investigation

Two January 2025 contradictions occur in adjacent BAASE detail rows. Explicit
chain, item, UPC, date, purchase type, format, color and size agree between the
rows. Thus the inspected dimensions do not establish disjoint entities; order
and delivery differ. SALES consistency does not prove which ORDER/DELIVERY value
is correct. Keep both blocked; do not sum, choose zero or import a Walmart rule.

## Private review/configuration and reproducibility

Ignored `outputs/prd09_2d2/` contains conflict_clusters, source_authority_matrix,
verified_evidence_registry, source_inspection, snapshot_proofs, manual_overrides,
same_snapshot_dossier, manual_review_pack and readable manual_review_summary.
Row-level identifiers/values, source workbooks and ledgers are deliberately not
committed. Public tests use invented chain/product identities only.

Grouped pending decisions: 464 Walmart internal facts; 127 Walmart February
cross-source facts; one Walmart December same-cutoff fact; two Al Super facts.
The review pack lists decision IDs, affected scopes, source pairs, alternatives,
risk and evidence needed. A recommendation is not an approval. Source owners
must provide actual authority/grain/correction evidence, not merely choose a
larger value or say “use the newer file.”

Administrative file loading supports authority matrix, scoped snapshot proofs
and manual overrides. Overrides require decision/cluster IDs, exact source hashes,
an exact key-scope fingerprint, rule ID, reason, approver/time and verified
evidence reference. Changed bytes invalidate decisions. Overlapping/duplicate
rules or proofs fail closed. This is not a browser-facing approval endpoint.
An external administrative evidence bundle must be explicitly reviewed and its
SHA pinned; merely putting a self-asserted registry in the policy folder is ignored.

`scripts/adjudicate_historical_sources.py` accepts explicit `--source` profiles,
`--baseline`, `--reference`, and a fresh ignored `--output-dir`. It verifies the
frozen baseline, inspects source contexts read-only, writes grouped review,
and scans the full corpus in forward/reverse order. Future reviewed policies can
be supplied with `--policy-dir`, and explicitly reviewed additional evidence with
`--reviewed-evidence` plus `--reviewed-evidence-sha`. Never reuse an existing output
directory's artifact names: preserve prior evidence and create a new run directory.

Full six-file reversed scan: counts, version payloads, normalized blocked
conflicts and dataset SHA identical. Canonical evidence does not depend on which
byte-identical filename was encountered first. FENDI remains 96/96, zero
differences, unchanged coverage; source-literal regression samples all 13 chains.

## Canonical candidate preview (NOT publication-ready)

| Measure | Result |
| --- | ---: |
| Chains / products / categories | 13 / 894 / 45 |
| SALES / ORDER / DELIVERY | 12,327 / 12,233 / 12,176 |
| Total observations / version rows | 36,736 / 36,736 |
| Historical revisions | 0 |
| Period range | 2023-01 through 2026-07 |
| UNKNOWN / E1 / E2 / E3 | 36,736 / 0 / 0 / 0 |
| Fabricated available_at | 0 |
| Pre-launch / unproven zero exclusions | 8,877 / 438 |
| Missing / formula / invalid exclusions | 9,412 / 492 / 13 |
| Unsupported/aggregate sheets | 57 |
| Non-direct-only fact groups | 258 |
| Multi-UPC ITEMs / ambiguous fact identities / aliases | 20 / 0 / 106 |
| Original conflicts / total resolved / final blocked | 730 / 136 / 594 |

Candidate fingerprint:
`7f5db050b05dcaa2de765faa81c3e4b16f48a4ab3672028f019bcd5d51b34fda`.

Counts intentionally do not equal old total plus 118: the conservative header
rule also prevents 186 previously C-only January groups from becoming facts.
118 resolved groups minus 186 newly non-direct-only groups gives net minus 68.
Those rows are retained in exclusion/lineage ledgers, not deleted. No underlying
source bytes or frozen results were edited.

## Temporal, publication and recovery policy

All historical available_at values remain NULL/UNKNOWN. There is no real E1/E2/E3
evidence in this corpus. Snapshot revision chronology, if later proven, creates
V1/V2 values while retaining NULL unless independently validated temporal
evidence exists. Administrative approval time and batch upload time never become
historical available_at. `monthly_observations_current` anti-leakage protections
and SQL migrations 001–007 are unchanged.

Publication gate BLOCKED: 594 value blockers, zero identity blockers, four
unapproved manual decisions. Adapter implementation and productive Storage /
PostgreSQL bootstrap are NOT EXECUTED, as required by the conditional PRD gate.
Remote fingerprint and post-load comparisons are N/A; remote corpus remains empty.

Before any eventual bootstrap, reauthenticate/check the exact project, certify
a real server-only adapter and transaction/idempotency/resume behavior, create
FK-compatible metadata before facts, commit batch facts with atomic checkpoints,
and verify remote canonical counts/fingerprint. Do not upload multichain files
until private Storage scope/policy handling is reviewed; do not open a bucket or
duplicate bytes to bypass authorization. On failure, recover by identified
batch/checkpoint and idempotent resume, not remote reset or broad deletes.

Runtime unchanged: SUPABASE_ENABLED=false, PERSISTENCE_PROVIDER=sqlite,
DATA_PROVIDER=normalized. No Railway variable changes, frontend visual changes,
model edits, OpenAI, Deep Research, voice or Netlify integration.

## Validation scope

CP01–26 and CP35–36 are executable source-policy tests plus real private CLI
baseline/Pareto/determinism/golden checks. CP31–32 use read-only remote Gate 1
and the existing actual PostgreSQL-compatible SQL/RLS test suites. Six remote
bootstrap cases (CP27–30, CP33–34) are explicitly skipped while publication is
blocked, never reported as adapter certification. Existing monthly CSV/XLSX,
SHA, preview, products, versioning, rollback and SYSTEM_INGESTION tests still run.
Full CI requires backend coverage >=81%, all engine suites, forecast audit,
Supabase baseline/security suites, frontend lint/typecheck/build, Docker build
and persisted-volume container recreation. No skipped remote case counts as PASS.

Local results: 180 backend cases discovered, 174 executed PASS, six conditional
remote cases NOT EXECUTED; 110 engine tests PASS; total executed Python 284/284.
Backend coverage 84.46% (threshold 81%). PostgreSQL-compatible baseline constraints
PASS, security behavior 21/21, RLS 28/28. Forecast audit, Python static checks,
frontend lint/typecheck/build PASS. Local Docker executable is unavailable;
Docker build/container-volume restart must be verified by the existing Linux CI.
Certified replay loaded the saved private policy files and reproduced the same
canonical fingerprint/counts/conflicts in both source orders; private golden
comparison again passed 96/96 with zero differences.
