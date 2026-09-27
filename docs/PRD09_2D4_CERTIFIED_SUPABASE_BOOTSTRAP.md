# PRD 09.2D.4 — Controlled certified historical bootstrap

## Status

**IMPLEMENTATION READY — HISTORICAL BOOTSTRAP NOT YET EXECUTED.** The preparation
script cannot execute SQL. The separate administrative executor requires a clean
pushed implementation, the exact private source contract, a freshly verified
linked project and a real approved Auth ADMIN. Remote publication/readback,
idempotent rerun, security behavior and final CP01–36 remain pending.
Fresh Towell CLI authorization and project access have now been verified. The
read-only remote audit found migrations 001–007, 28/28 RLS-enabled baseline
tables, the expected view and four private buckets. BEFORE counts for chains,
categories, products, monthly_observations, import_batches and source_evidence
are all zero. No existing bootstrap jobs were found. A real Auth user has now
been verified and assigned ACTIVE/ADMIN following explicit user approval; the
administrative approval is audited. CLI authorization is not a Supabase Auth
identity. The shared CLI account changed again during the final read-only check;
execution stops without writes until fresh Towell authorization is verified.
No bootstrap business INSERT has occurred. Never infer access from a previous
CLI session or a failed/403 request.

Repository Gate 0 was checked against approved commit
`4002bded348a6570eaa1b4faeebf4855ec172942`: clean main, HEAD = origin/main,
base GitHub Actions run 36267065360 completed successfully.

## Authorized corpus

| Measure | Certified only |
| --- | ---: |
| Observations | 39,270 |
| Actual-bearing scopes | 18 |
| Scope/product identities | 1,010 |
| Named scope/category identities | 50 |
| SALES | 13,153 |
| ORDER | 13,099 |
| DELIVERY | 13,018 |
| Parent-grain facts | 8,558 |
| Sheet-owned child facts | 2,260 |
| Explicit-row child facts | 28,452 |

Dataset SHA: `3a092905dc699a762be0ca3f459ff9af31c9045d5cc06f6407453b457ac969e4`.
Certification SHA: `f9037ef14ac523db85fe6625c36d5e0f2a574d53934643ef33686371190631bd`.

The 1,538 scope blockers and 720 quantity conflicts are excluded, not a gate
requiring business resolution before loading the accepted corpus. The private
`HISTORICAL_BACKLOG_V1` retains all 2,258 keys, reasons, source hashes, literal
lineage fingerprints and their frozen dataset/certification references, with
status `PENDING_FUTURE_RECONCILIATION`.

## Preparation controls

`scripts/prepare_certified_bootstrap.py` is opt-in and read-only. It independently
rereads the XLSX sources without saving or recalculating them, checks every
accepted literal, reverses source/profile/authority order, verifies all frozen
input hashes, and checks global and FENDI goldens. A certificate copied from a
previous run alone is insufficient. Repeat this replay immediately before a
real insertion; preparation is not permission to bypass later gates.

The script requires a clean committed implementation before binding the
`bootstrap_id` to the execution commit and a timezone-qualified bootstrap
timestamp. Retries must reuse that persisted manifest, timestamp and plan; they
must not generate a different ID or assume a connection failure rolled back.
The planning module itself performs no database or network operation and is
not imported into the application runtime.

Current local validation: full 39,270-literal replay PASS, exact dataset and
certification hashes PASS, source-order determinism PASS, frozen global/FENDI
goldens unchanged, and 46/46 new offline planning/readback tests plus 13/13
synthetic PostgreSQL transaction/security tests PASS. Full backend: 452 collected,
446 executed PASS and six optional tests skipped; coverage 87.15%. The existing
110 engine tests, 21/21 SQL security tests, schema tests, motor audit, frontend
lint/typecheck/build, targeted Ruff and whitespace checks pass. A local format
roundtrip also reproduces the exact 39,270-fact SHA, all 18 scope totals and
grain counts. These tests are **not** a claim that remote CP01–36 passed.
The dynamic plan contains 30 scope
masters (18 actual-bearing scopes plus 12 proven parent ancestors), 1,010
products, 50 named categories and no backlog observations. No master has
yet been inserted remotely.

## Mapping to the existing contract — no migration proposed

The four original contract migrations and security migrations 005–007 remain
unchanged. The existing schema has one physical import batch per chain, not a
multi-chain batch. Physical batches must start `UPLOADED`, then transition
`VALIDATING → VALIDATED → CONFIRMED → IMPORTED`; `STAGING` is not a permitted
physical import_batches status. Do not bypass that guard.

The proposed logical `HISTORICAL_CERTIFIED_BOOTSTRAP_V1` starts `STAGING` in
the existing global administrative job/manifest and owns one physical batch
per dynamically derived actual-bearing scope. Aggregate metadata belongs to
that logical manifest. This distinction is tested against migrations 001–007
in local PostgreSQL. Staging chunks are append-only operational run logs; no
master/fact/batch publication occurs until all sealed chunks are validated.

The certified scope catalogue supplies canonical code, proven parent relation,
scope type and status. `chains.code` uses the stable certified scope ID in its
schema-compatible uppercase form; the original commercial code is retained
verbatim in the mapping metadata and name. Only actual scopes and proven parent
ancestors are planned. Thus the number of master scopes may exceed 18; extra
parents have no manufactured observations or products. No unknown hierarchy is
created, and child/parent observations must never be summed automatically.
Hierarchy can be retained in versioned profile mapping metadata without
inventing a new relational hierarchy or modifying old migrations.

Products use scope + certified source product code + variant. A missing variant
stays NULL; description is never identity. Documented ITEM/UPC mappings and
description aliases are preserved separately. Historical categories remain
attached to fact lineage. A product with multiple evidenced historical
categories has no arbitrarily chosen current category. `UNCLASSIFIED` is missing
category evidence, not an extra category. No fuzzy, case or whitespace merge is
performed without a certified mapping.

## Required remote execution gates — still pending

1. Fresh account access to project `bskoyqhbgrycpwhydnnr`, linked ref equality,
   migrations exactly 001–007, 28 expected RLS-enabled tables, expected view,
   and the four private buckets.
2. Read-only BEFORE counts and exact classification of existing data. A nonzero
   count is not a reason to delete anything. Identify a real approved Auth actor
   for the mandatory created_by/uploaded_by foreign keys; CLI account identity
   does not automatically create a Supabase Auth user.
3. Repeat literal replay and verify the persisted execution manifest, immutable
   sources, exact corpus/certification SHA and empty blocked-key intersection.
4. Execute the locally tested atomic master/batch/evidence/observation publication,
   collision checks, failure rollback and resume/idempotency. No arbitrary
   ON CONFLICT winner and no incomplete batch marked imported.
5. Independent remote joined readback of actual chain/product/period/metric/
   value/availability/batch/evidence, followed by canonical reconstruction and
   dataset hashing. A copied manifest SHA is not remote verification.
6. Verify all scope counts, metric/grain totals, 39,270 UNKNOWN/NULL facts,
   both key and physical-lineage disjointness from the 2,258 backlog keys,
   and a second execution producing no additional observations.
7. Test real RLS behavior for viewer/editor/admin/cross-chain access, buckets
   remaining private, scoped rollback/cleanup, audit actor/start/end/counts,
   and final CP01–36. Static RLS inspection is not a security-behavior PASS.

The append-only triggers also protect bootstrap observations/evidence/batches.
Do not disable them or RLS merely to load data. A safe transaction failure can
roll back every newly created master and fact before commit. Post-commit
withdrawal needs a separately validated, narrowly scoped administrative
procedure with an exact ownership manifest and no unrelated references/deletes;
it is implemented and locally tested, including refusal of unrelated references
and full rollback of failed withdrawal. Only four named DELETE guards can be
temporarily suspended under exclusive locks, with FK/RLS left enabled and the
guards restored in the same transaction. Successful publication never calls
cleanup. Staging and audit remain preserved. No withdrawal has run remotely.

`scripts/execute_certified_bootstrap.py` checks project access before every query,
rejects unrelated nonempty corpora, repeats literal replay immediately before
staging, publishes in one transaction, independently reads all joined facts,
checks every scope/metric/grain total and both blocked-key/physical-lineage
intersections, and reruns the identical persisted bootstrap to measure
idempotency. It then probes real authenticated PostgreSQL VIEWER/EDITOR/ADMIN
and cross-chain behavior in a rolled-back transaction; post-probe counts,
business checksum, actor role, grants and test edits are independently checked.
No test users, permissions or probe data remain persisted.

All operational and row-level artifacts live under ignored
`outputs/prd09_2d4/`. No XLSX upload, local paths, credentials, SQLite or
row-level business data belong in Git or this public document.

## Future bulk/V2 contract

New file → STAGING → reviewed identity/scope → compare accepted corpus and
backlog → NEW / SAME / REVISION / CONFLICT / RESOLVES_BACKLOG → review/publication.
Changed quantities without reviewed version authority are conflicts. A backlog
key is not resolved merely because it reappears in a file. No V1 overwrite.
V2 must record parent_dataset_sha, resolved_backlog_ids, additions/revisions,
new dataset_sha and audit trail. The offline classifier prepares this conceptual
contract only, without implementing frontend or runtime ingestion.

## Runtime remains unchanged

`SUPABASE_ENABLED=false`, `PERSISTENCE_PROVIDER=sqlite`, `DATA_PROVIDER=normalized`.
No Railway settings, frontend, models, Champion, vintages, OpenAI, Deep Research,
voice or Netlify changes. FENDI legacy values remain 96/96; evidence-supported
hierarchical scope remains 72 child + 24 parent, with both goldens frozen.
