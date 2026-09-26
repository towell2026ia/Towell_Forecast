# PRD 09.2D.2.3.1 — Global Hierarchical Corpus Certification

Base: `42913c67d997809630cb01d46aa481f8a501936e` on `origin/main`.
Base GitHub Actions: PASS, run 36260212737.

## Scope and method

The administrative certifier is opt-in and isolated from application startup,
forecast engines and persistence. It certifies the entire accepted corpus, not
a sample or a pilot. It reads the original workbooks without saving, editing or
recalculating them, and pins their bytes and the approved profiles/authorities.
The Spreadsheets read-only workflow is used for source verification.

For every primary and equivalent source, approved profiles reconstruct product,
period and metric from literal cells. Approved SourceScope rules reconstruct
quantity ownership independently of report assignments. Description is never
product identity. Identity bridges do not confer quantity ownership. Parent facts
cannot be promoted to children using commercial membership or equal values.
One physical source fact cannot be assigned to multiple scopes.

The accepted observations, versions and scope catalogue must reproduce the
approved baseline exactly. Source, profile and authority orders are then reversed:
dataset, counts, scope/product coverage, blocker trace and complete certification
fingerprint must match. Original sources, legacy CSV and earlier frozen artifacts
are checked again for byte equality before publication of private results.

## Global result

| Control | Result |
| --- | ---: |
| Selected / certified / literal matches | 39,270 / 39,270 / 39,270 |
| Uncertified selected / duplicates / literal mismatches | 0 / 0 / 0 |
| Unsupported scope / unknown selected scope / membership fanout | 0 / 0 / 0 |
| Actual-bearing scopes PASS / BLOCKED | 18 / 0 |
| Accepted scope/product identities | 1,010 |
| Scoped categories, excluding UNCLASSIFIED | 50 |
| Period range | 2023-01 to 2026-07 |
| SALES / ORDER / DELIVERY | 13,153 / 13,099 / 13,018 |
| Parent / sheet-owned child / explicit-row child facts | 8,558 / 2,260 / 28,452 |
| UNKNOWN / E1 / E2 / E3 | 39,270 / 0 / 0 / 0 |
| Fabricated available_at / blocked facts selected | 0 / 0 |

All UNKNOWN observations retain NULL available_at. Certification timestamps,
membership approvals and filesystem dates are not historical availability.
These facts remain unavailable to an as-of runner until independently justified
temporal evidence exists. Certification does not unlock them.

## Dynamically discovered actual-bearing scopes

The two scopes named Walmart below are distinct parent/commercial-unit scopes.
They are not aggregated together. Scope names and counts are derived from the
certified corpus; they are not a fixed list in the certifier.

| Scope | Products | SALES | ORDER | DELIVERY | Facts | Period range | Status |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| Walmart — commercial unit | 11 | 67 | 67 | 67 | 201 | 2025-01–2025-12 | PASS |
| Soriana::Hiper | 44 | 704 | 704 | 704 | 2,112 | 2025-01–2026-07 | PASS |
| Walmart::BD | 56 | 731 | 756 | 738 | 2,225 | 2024-01–2026-07 | PASS |
| Merco | 8 | 120 | 120 | 120 | 360 | 2025-01–2026-07 | PASS |
| City Fresko | 51 | 378 | 378 | 378 | 1,134 | 2025-01–2026-07 | PASS |
| Suburbia | 53 | 878 | 879 | 879 | 2,636 | 2025-01–2026-07 | PASS |
| Casa Ley | 54 | 709 | 709 | 709 | 2,127 | 2025-01–2026-07 | PASS |
| HEB | 79 | 509 | 509 | 509 | 1,527 | 2025-01–2026-07 | PASS |
| Chedraui | 128 | 2,101 | 2,101 | 2,101 | 6,303 | 2025-01–2026-07 | PASS |
| Walmart::SC | 78 | 1,142 | 1,106 | 1,099 | 3,347 | 2024-01–2026-07 | PASS |
| TresB | 23 | 115 | 115 | 115 | 345 | 2025-04–2026-07 | PASS |
| DSW | 47 | 617 | 617 | 617 | 1,851 | 2025-01–2026-07 | PASS |
| Liverpool | 38 | 516 | 516 | 516 | 1,548 | 2025-01–2026-07 | PASS |
| GS Sears | 28 | 140 | 140 | 140 | 420 | 2026-03–2026-07 | PASS |
| Al Super | 45 | 581 | 580 | 580 | 1,741 | 2025-01–2026-07 | PASS |
| Soriana::Mercado | 41 | 682 | 682 | 682 | 2,046 | 2025-01–2026-07 | PASS |
| Walmart::Prichos | 13 | 263 | 263 | 263 | 789 | 2024-01–2026-07 | PASS |
| Walmart — parent | 213 | 2,900 | 2,857 | 2,801 | 8,558 | 2023-01–2025-02 | PASS |
| TOTAL | 1,010 | 13,153 | 13,099 | 13,018 | 39,270 | 2023-01–2026-07 | PASS |

Private scope summaries also contain parent/unit links, source/hash counts,
product eligibility and temporal/mismatch/unsupported/duplicate counts per scope.
Each scope's UNKNOWN count equals its fact count; its E1/E2/E3 counts are zero.
Its mismatch, unsupported and duplicate counts are zero.

## Product coverage and excluded data

| Structural classification | Accepted actual-bearing series | All classified series |
| --- | ---: | ---: |
| FORECAST_PARENT_ELIGIBLE | 124 | 124 |
| FORECAST_CHILD_ELIGIBLE | 734 | 734 |
| CATALOG_ONLY | 0 | 114 |
| BLOCKED | 152 | 246 |
| TOTAL | 1,010 | 1,218 |

Structural eligibility is not temporal permission to forecast. A series can
contain certified accepted facts and still be BLOCKED due to other unresolved
keys. Counts of series must not be confused with counts of observations.

The 28 catalogue scopes without accepted actuals remain separately reported as
BLOCKED for actual certification, rather than disappearing: seven commercial
scopes (PARISINA, CE, Batas, Batas Bebés, BATAS WM, CM, BATAS LV-WM), eleven parent
scopes (Al Super, GS Sears, HEB, TresB, Chedraui, DSW, Casa Ley, Merco, Soriana,
Liverpool, Suburbia), City Fresko parent, and nine UNKNOWN source scopes.
These are distinct from the 18 actual-bearing scopes above.

Exactly 1,538 fact-scope keys and 720 value-conflict keys remain excluded. Their
complete private trace includes canonical key, reason, source/sheet pairs, metric,
period, value fingerprints and decision group. Eleven grouped questions ask for
independent scope or source-version authority, never one question per fact.
No automatic winner is selected; no quantities are split or allocated.

There are 2,148 independently evidenced parent/child relationships marked
INDEPENDENT_SCOPE_REPRESENTATIONS. Both literal facts are preserved and automatic
aggregation is forbidden. Equal quantities or commercial membership alone cannot
create such a relationship.

## Private artifacts and frozen regression

All artifacts stay in ignored `outputs/prd09_2d231/` and are not shipped to GitHub:

- global_hierarchical_certification.json: all accepted facts, full required proof,
  primary/equivalent literal lineage and fail-closed certification status.
- global_scope_summary.json and global_product_coverage.json: all dynamic scopes
  and series, counts, metric/period ranges and structural eligibility.
- blocked_trace.json and grouped_review_packet.md: excluded keys and review groups.
- independent_scope_representations.json and non_actual_scopes.json.
- determinism.json, legacy_regression_reference.json and closure.json.
- global_corpus_golden.json: GLOBAL_HIERARCHICAL_CORPUS_GOLDEN, frozen only after
  full global checks pass. It records dataset, count, scope/product/metric/period
  metadata, source/scope/fact fingerprints, UTC certification time and version.

Inputs are supplied by an explicitly pinned private contract to
`scripts/certify_global_corpus.py`; output must be fresh and inside ignored outputs.
Existing output artifacts are never overwritten. Implementation-stage candidates
were preserved separately; original historical golden artifacts were untouched.

The additional unchanged FENDI regression remains 96/96 values, 72 BD and 24 parent
facts, with zero changed/lost/duplicate/unsupported facts. Legacy labels are not
scope authority and this regression does not substitute for global certification.

Golden comparison detects disappeared/new/duplicated keys, changed values,
identity, period, metric, scope and source lineage, plus changed certification
content. Any change requires explicit reviewed evidence. A timestamp is metadata,
not evidence for available_at.

## Isolation and validation

Supabase writes: 0. No bootstrap, remote SQL, schema/RLS, Railway, engine or visual
frontend changes. Runtime remains SQLite / normalized. SUPABASE_ENABLED,
OPENAI_ENABLED, VOICE_ENABLED and DEEP_RESEARCH_ENABLED remain false.

CP36–67 are implemented through public synthetic fail-closed tests and optional
full private-corpus regression tests. Public CI uses synthetic data only; absence
of private sources skips private gates rather than claiming real-source validation.
Local closure requires those private gates to execute successfully.

The full backend/engine suite, forecast audit, local SQL baseline/security tests,
Python checks, frontend lint/typecheck/build and public-repository scan are run.
Docker is unavailable locally; the existing GitHub Actions job verifies the image
build and container/volume restart smoke. No XLSX, row-level observations, private
source paths, credentials or identity mappings are committed.

Local backend: 360 collected, 354 PASS and six unrelated optional private gates
SKIP. All 40 new global tests executed locally, including six full-corpus gates.
Engine suites: 110/110 PASS. Total executable Python tests: 464/464 PASS.
Backend coverage: 86.68%; global certifier coverage: 96.24%.
SQL baseline/security, forecast audit, Python checks, lint/typecheck/build: PASS.
Lint has four pre-existing warnings in ignored dependency files and zero errors.

Dataset SHA: `3a092905dc699a762be0ca3f459ff9af31c9045d5cc06f6407453b457ac969e4`.
Certification SHA: `f9037ef14ac523db85fe6625c36d5e0f2a574d53934643ef33686371190631bd`.
Golden/determinism: PASS. Final commit and CI evidence are recorded in the closure
delivery after the push finishes.
