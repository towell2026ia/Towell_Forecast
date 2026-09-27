# PRD 09.2D.4 — Controlled certified historical bootstrap

## Status

**REMOTE CERTIFIED BOOTSTRAP GATES: PASS.** The certified corpus was published
and independently read back from project `bskoyqhbgrycpwhydnnr`. CP01–36 pass;
CP18/CP34 exercise failure rollback and scoped withdrawal against the baseline
in local synthetic PostgreSQL, not by deleting the successful remote corpus.
The final documentation commit must additionally pass GitHub Actions before
the release is declared `PASS_CERTIFIED_BOOTSTRAP`.

Execution commit: `6e41ba59522460682790f84ef7f1a29e10adc932`.
Bootstrap ID: `7dcd4781-f485-5479-83d5-1dd9edc29f06`.
The execution commit, original bootstrap timestamp and sealed plan are retained;
a later documentation commit does not rebind or republish this historical job.

Fresh normal Supabase CLI authorization used a dedicated profile selected for
every command. The read-only preflight confirmed migrations 001–007, all 28
RLS-enabled baseline tables, the expected view and four private buckets. A real
Auth ADMIN was verified after explicit user approval; that approval is audited.
CLI authorization is not itself a Supabase Auth identity. Never infer current
access from an old session or a failed/403 request.

| Remote measure | Before | After / identical rerun |
| --- | ---: | ---: |
| Chain/scope masters | 0 | 30 |
| Categories | 0 | 50 |
| Products | 0 | 1,010 |
| Physical import batches | 0 | 18 |
| Source evidence | 0 | 39,270 |
| Monthly observations | 0 | 39,270 |

All 18 actual-bearing scopes reconcile independently, including product counts,
metric counts, period bounds and fact grains. The remaining 12 scope masters
are proven parent ancestors, not additional manufactured actuals. Remote business
keys and actual joined values reconstruct the exact dataset SHA below. All
39,270 observations retain `UNKNOWN` and `available_at=NULL`; fabricated dates,
loaded scope blockers and loaded quantity conflicts are all zero.

The initial readback encountered a transport failure after 37,000 rows, after
publication had already committed. Recovery first confirmed the same completed
job, sealed plan and exact counts, then independently reread all 39,270 rows
using durable read-only pages. It did not create a new ID or republish facts.
The subsequent identical-bootstrap rerun left counts, business checksum and
completion audit unchanged: one completed job, 99 staging chunks and one
completion audit. The audit attests actor, execution commit, source certification,
start/completion timestamps and counts.

Real authenticated VIEWER/EDITOR/ADMIN and cross-chain RLS checks pass 10/10.
Their transaction was rolled back; independent post-probe checks confirm no
persisted grants, role changes, product probe edits or dataset changes. RLS
remains enabled 28/28, all four buckets remain private and no source XLSX was
uploaded. Existing migrations and frozen evidence/goldens are unchanged.

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

Completed validation: full 39,270-literal replay immediately before insertion
PASS, exact dataset and
certification hashes PASS, source-order determinism PASS, frozen global/FENDI
goldens unchanged, and 46/46 new offline planning/readback tests plus 13/13
synthetic PostgreSQL transaction/security tests PASS. Full backend: 452 collected,
446 executed PASS and six optional tests skipped; coverage 87.15%. The existing
110 engine tests, 21/21 SQL security tests, schema tests, motor audit, frontend
lint/typecheck/build, targeted Ruff and whitespace checks pass. A local format
roundtrip also reproduces the exact 39,270-fact SHA, all 18 scope totals and
grain counts. Remote readback, idempotency, scope/exclusion checks and security
also pass independently; local tests alone are not used as remote evidence.
The dynamic plan contains 30 scope
masters (18 actual-bearing scopes plus 12 proven parent ancestors), 1,010
products, 50 named categories and no backlog observations. These exact counts
were subsequently verified remotely. Implementation CI run 36297899723 passed
all three jobs, including Docker build and volume/restart smoke. Final closure
CI is tracked separately against the final documentation commit.

## Mapping to the existing contract — no migration proposed

The four original contract migrations and security migrations 005–007 remain
unchanged. The existing schema has one physical import batch per chain, not a
multi-chain batch. Physical batches must start `UPLOADED`, then transition
`VALIDATING → VALIDATED → CONFIRMED → IMPORTED`; `STAGING` is not a permitted
physical import_batches status. Do not bypass that guard.

The logical `HISTORICAL_CERTIFIED_BOOTSTRAP_V1` starts `STAGING` in
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

## Remote execution gates — completed

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
