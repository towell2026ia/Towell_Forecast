# PRD 09.2D.2.3.2 — Residual Fact-Scope Closure

Base commit: `f17fb4d3b8089f67984c4d95db1a67455fbfca8a`, origin/main.
Base CI: PASS, GitHub Actions run 36262702147.

## Result

**BLOCKED_BY_RESIDUAL_SCOPE_EVIDENCE**. The review implementation and regression
checks pass, but no independently supported quantity ownership was found for
the remaining literal cells. Zero blockers are forcibly cleared. This status
is not permission to bootstrap Supabase or replace the frozen global golden.

| Control | Before | Candidate |
| --- | ---: | ---: |
| Accepted observations | 39,270 | 39,270 |
| Actual-bearing scopes | 18 | 18 |
| Scope/product identities | 1,010 | 1,010 |
| Fact-scope blockers | 1,538 | 1,538 |
| Original quantity conflicts | 720 | 720 |
| Added observations | — | 0 |
| Lost / changed / duplicated old facts | — | 0 / 0 / 0 |
| UNKNOWN, available_at NULL | 39,270 | 39,270 |
| Supabase writes | 0 | 0 |

Certified parent, certified child, certified explicit-row, derived/non-fact and
duplicate-representation closures: **0 each**. Proven value-conflict scope splits:
**0**. No winner was selected for any quantity conflict.

Dataset SHA, V1 and V2 candidate:
`3a092905dc699a762be0ca3f459ff9af31c9045d5cc06f6407453b457ac969e4`.

Frozen certification SHA:
`f9037ef14ac523db85fe6625c36d5e0f2a574d53934643ef33686371190631bd`.

## Residual inventory and dynamic clustering

Every scope-blocking decision is inventoried with its complete physical source
locators, literal ITEM/UPC, period, metric, reason, possible parent/child scopes,
source/sheet roles, explicit fields, membership evidence and approved profile.
Clustering uses source hash, sheet, reason, roles, candidate scopes, metric layout
and identity pattern. No fixed retailer or product branch is used.

All residuals are literal ACTUAL quantities in CHAIN_CALC worksheets in two 2024
sources. There are six source/sheet pairs and eight clusters: CE has separate
ITEM-only and ITEM/UPC layouts. Parent context and commercial-unit names are
candidates, not quantity ownership authority.

| Worksheet pattern | Blockers per source | Total | Review decision |
| --- | ---: | ---: | --- |
| BATAS WM | 396 | 792 | S-5994587dfabf2d59 |
| CE | 307 | 614 | S-d337a29a451c6fd3 |
| Batas Bebés | 66 | 132 | S-2cfc3b7a5c118035 |
| TOTAL | 769 | 1,538 | 3 grouped decisions |

The grouped review covers January–November 2024. CE delivery literals span
January–October; its ITEM-only sales/order pattern spans January–November.

| Pareto target | Clusters | Covered blockers | Coverage |
| --- | ---: | ---: | ---: |
| 80% | 5 | 1,259 | 81.86% |
| 90% | 6 | 1,406 | 91.42% |
| 95% | 7 | 1,472 | 95.71% |
| 100% | 8 | 1,538 | 100.00% |

Classification: CALC_SHEET_WITHOUT_VALUE_SCOPE = 1,538. PARENT_SCOPE_MISSING,
CHILD_SCOPE_MISSING, MULTIPLE_POSSIBLE_PARENT, MULTIPLE_POSSIBLE_CHILD,
RAW_SOURCE_WITHOUT_SCOPE, IDENTITY_SCOPE_COUPLED and OTHER = 0 each. Ambiguous
candidate memberships remain recorded even when the primary cause is missing
calculation-sheet quantity authority.

## Evidence search and resolution safeguards

The opt-in administrative runner reads original XLSX sources without saving or
recalculating them, following the Spreadsheets read-only evidence workflow.
Source bytes, profiles, authorities and all frozen V1 artifacts are pinned.
The full 39,270-observation certification is replayed from literal sources before
residual review, not replaced with a sample or the FENDI regression.

The private evidence registry retains literal headers, monthly layouts, explicit
chain/format fields, approved sheet scopes, workbook parent contexts, memberships
and exact XLOOKUP dependency references. The reviewed residual profiles have no
explicit chain/format ownership columns. Raw worksheets have documented parent
scope, but that authority does not transfer to neighbouring calculation sheets.
Generic report headers and commercial membership do not certify their literals.

No scope is inferred from filename, modification time, file order, recency,
larger quantity, description, a name containing “final”, equal values or adjacent
formulas. A literal cannot become derived merely because another month uses a
lookup. Exact dependency recognition requires the blocked cell itself to be a
supported formula referencing one unique accepted literal ITEM/period/metric
target. It preserves lineage, never evaluates Excel and never creates a fact.

New parent, child and explicit-row assignments require separately owner-reviewed
rules bound to source hash, sheet, approved profile fingerprint, period/metric
domain and unchanged literal evidence. Membership/context-only evidence is
rejected. Explicit-row grain is preserved. A non-fact exclusion requires its own
approved exclusion basis and literal proof; changing parser visibility cannot
clear a blocker. Duplicate representations require independently proven scope
and an existing identical canonical fact, with external lineage retained.

Every disappearing blocker must have an explicit outcome. Missing inventory,
altered lineage, rules for another source, ambiguous rules and source overlap with
already frozen facts fail closed. Accepted additions require literal product,
period, metric, quantity and grain proofs and remain UNKNOWN/NULL. New quantity
collisions are quarantined without selecting a winner. All 720 original conflict
decisions, values and source lineage are preserved verbatim.

V2 is a separate GLOBAL_CORPUS_V2_CANDIDATE. Its trace records parent/new dataset
SHA, added keys, preserved facts, losses, changed values, duplicates and forbidden
re-graining. It does not overwrite or freeze a replacement V1 golden.

## Unchanged dynamic scopes

Names/counts below are audit output, not routing rules. Parent/child representations
are never automatically added together.

| Actual-bearing scope | Facts |
| --- | ---: |
| Walmart — commercial unit | 201 |
| Soriana::Hiper | 2,112 |
| Walmart::BD | 2,225 |
| Merco | 360 |
| City Fresko | 1,134 |
| Suburbia | 2,636 |
| Casa Ley | 2,127 |
| HEB | 1,527 |
| Chedraui | 6,303 |
| Walmart::SC | 3,347 |
| TresB | 345 |
| DSW | 1,851 |
| Liverpool | 1,548 |
| GS Sears | 420 |
| Al Super | 1,741 |
| Soriana::Mercado | 2,046 |
| Walmart::Prichos | 789 |
| Walmart — parent | 8,558 |
| TOTAL | 39,270 |

Global golden/literal regression: PASS. FENDI legacy: 96/96 values, 72 BD and
24 parent, with zero changed/lost/duplicated/unsupported assignments. Original
legacy and global goldens, snapshots, vintage E3, evidence and hashes remain
byte/content frozen. Legacy labels confer no commercial-unit authority.

## Determinism, private artifacts and isolation

Source-file, profile, membership-authority, scope-authority and new-rule orders
are reversed. Accepted facts/versions/catalogue/source assignments, membership
sets, dependency lineage and complete functional ledger must reproduce V1.
Comparison uses canonical JSON fingerprints, treating in-memory tuples and
their persisted JSON arrays equivalently. Membership list order is not authority.
All physical-source provenance is inherited from the pinned V1 manifest; reading
two byte-identical files in reverse must not elect a new primary filename.
Inventory, registry, clusters, outcomes, V1-to-V2 trace, conflicts and full
candidate fingerprints then match in both directions. Source bytes and frozen
artifacts are checked again before any private output is written.

Ignored `outputs/prd09_2d232/` contains the requested inventory, clusters, evidence
registry, complete resolution ledger, global_v1_to_v2_trace, value_conflict_trace,
canonical_candidate, manual_fact_scope_review and RESULT, plus regression,
determinism, baseline gate, review decisions and new-fact certificates. No private
row-level data, mappings, source paths, XLSX, SQLite or secrets are committed.
Implementation-stage candidates remain separate; no frozen prior artifact is edited.

The three grouped decisions ask the owner whether each source/sheet's literal
quantities represent parent demand, independently owned commercial-unit demand
or copied/non-independent quantities, and require a source-bound declaration or
exact cell lineage. Approval for one source cannot automatically cover another.

Runtime remains SQLite/normalized. SUPABASE_ENABLED, OPENAI_ENABLED,
VOICE_ENABLED, DEEP_RESEARCH_ENABLED and LEGACY_PILOT_ENABLED remain false.
No Supabase bootstrap, SQL/RLS/schema, Railway, frontend, engine, Champion,
vintage or application-startup change is made. The new runner is explicit opt-in.

## Validation

CP01–36 use public synthetic fail-closed contracts and complete local-only
private gates. Public CI skips undistributed private inputs; it does not claim
to have independently read the real workbooks. Local certification requires the
complete real-source replay and all four new private gates to execute.

The full backend and six engine suites, forecast audit, Python checks, local SQL
baseline/security tests, frontend lint/typecheck/build and tracked-file privacy
check are required. Docker is unavailable locally; the existing CI builds the
official image and runs the container/volume restart smoke.

Local backend: 406 collected, 400 PASS, six unrelated optional private gates
SKIP. All 46 new residual tests execute locally, including the four complete
private gates. Six engine suites: 110/110 PASS. Total executable Python tests:
510/510 PASS. Backend coverage: 86.85%; residual module: 88.86%.
Forecast audit, Python checks, SQL baseline/security (21 behavioral permission
tests, 28 RLS tables), frontend lint/typecheck/build: PASS. Lint has four
pre-existing dependency warnings and zero errors. Final commit, push and CI
evidence are recorded in the private RESULT and closure delivery after GitHub
Actions completes; a failing CI prevents declaring the delivery complete.
