# PRD 09.2E5-01B-A.1: controlled commercial schema

The human PRD authorized correcting and applying only migration
`202610140015_commercial_catalog.sql` after preflight, while expressly reserving
product mapping for a separate authorization. The corrected migration was applied
to the existing linked project on 2026-10-14, with no commercial seeds or backfill.

Schema SHA-256 (canonical UTF-8/LF):
`dc86e6ae5670eec9c5ef3bbac83a87848685bc45dd0b05f2091ff3deaef80655`.
The migration ledger retains the exact applied SQL with that hash. No prior
migration was edited or replayed; the dry-run listed only migration 015.

## Five commercial tables and the current view

- `commercial_product_types`: an open product-type dictionary, not a closed enum.
- `commercial_models`: model/design candidates.
- `commercial_colors`: color candidates.
- `commercial_sizes`: literal or homologated presentation/explicit-size candidates.
- `product_commercial_classifications`: append-only, versioned associations by
  product UUID and chain, now including product type, state, literal and evidence.

Every table has RLS and zero rows after application. Anon, authenticated and
service_role have no INSERT, UPDATE, DELETE or TRUNCATE rights. Dictionary evidence
is withheld from browser readers; association evidence follows chain-scoped RLS.
`product_commercial_classifications_current` uses `security_invoker=true` and
returns all 1,010 authorized products as PENDING_CLASSIFICATION/UNKNOWN while no
mapping exists. APPROVED requires a known type as well as the existing approval
requirements. Schema approval activates no runtime writer or writer RPC.

MB -> MEDIO BAÑO, BANO/BAÑO -> BAÑO, PULL/PULLMAN -> PULLMAN and FACIAL remain
unchanged. BATA/BATAS maps to type BATA independently of model and garment size.
Original descriptions, literals, product identity and ITEM/UPC evidence remain
unchanged. No dimensions or unconfirmed apparel equivalences are invented.

## Preflight, recovery and application evidence

Preflight confirmed migration 015 absent, all 36 original public tables protected
by RLS, 1,010 products, 39,270 historical observations, exactly one C1 with twelve
horizons, no C2 and zero selection/adoption events. Full C1 content was verified
against the frozen preview and its stored hash; no forecast was recomputed.

Seven managed physical backups had COMPLETED status; PITR was disabled. A fresh
private portable snapshot of products, observations, C1, horizons, selection and
audit rows was restored into an isolated local PostgreSQL-compatible engine.
Every typed value and all six PostgreSQL row fingerprints matched the source.
This is a tested data recovery supplement, not a claim that a complete managed
physical restore, Auth or Storage restore was exercised. Empty-schema rollback
was tested separately and refuses removal of populated commercial tables.

Application used one transaction with an advisory lock, bounded table locks,
preflight freshness checks, exact prior migration ledger, schema-only DDL, empty
table/RLS/grant/FK/view assertions, and before/after fingerprints. Only migration
015 and its ledger entry were committed. Independent read-only postflight then
confirmed equality of all 36 existing table fingerprints, original schema
definitions, products, import metadata, history and C1.

Historical row SHA-256:
`db3a3549a2c89ca304248690d48d6a39baf364f3c4139821228f37e0a476dcde`.
Certified business-lineage SHA-256:
`b73bdddd15b7a6867e81c19a47856c5d3ff6b659020c7b343e246ecb3caf487f`.
C1 content hash:
`85bbf53219315aedc76f67ab54e7644794f05dee1aedb1265a994d79f7ecb478`.

Capture, LIVE/learning, Champion, Vintage and official publication remain OFF.
Selection remains ON with zero adoption events. No engine, baseline, ensemble,
Deep Research or Selection implementation/flag was changed.

## Separate pilot package, not loaded

`scripts/prepare_commercial_mapping.py` builds an offline exact payload, full CSV
matrix and guarded SQL. The private package covers all 1,010 source UUIDs, preserving
chain, original description, string ITEM/UPC arrays, source snapshots, ambiguity
reasons, evidence and mapping/rule versions. All rows remain PENDING_CLASSIFICATION;
APPROVED count is zero. Proposed dictionaries have 4 types, 90 models, 117 colors
and 20 presentation/size labels. These are candidates, not commercial approvals.
Accent-distinct labels are preserved rather than silently merged.

Sealed mapping package hash:
`cdbac53c3a7de8c63e156d71123cbafa7ee1e1b451441e1c647c1cfa73f00316`.
The exact package SQL was exercised only in an isolated local database: absent
authorization was rejected; local authorized replay produced exactly 1,010 unique
pending associations with matching snapshots and zero approvals. Supabase contains
zero mapping rows. A future invoker-view design separates approved and proposed
labels; that design was not applied.

Private production data, SQL with production snapshots and recovery files remain
outside public Git. Mapping requires an independent human authorization naming
this exact hash and pending-only scope, followed by fresh snapshot/permission checks.
Unresolved products must not be promoted to APPROVED by the loader.

## Validation

Local commercial Python: 37 tests PASS; commercial SQL/RLS: 18 checks PASS.
Existing baseline, 36 security checks, 147 E4 effective privilege checks and 21 E4
schema/regression checks PASS. Production read-only tests confirmed active ADMIN
scope, rejection of unknown identities and anon/service commercial read denial.
There are four active ADMIN profiles and no scoped memberships in production;
viewer/editor cross-chain isolation was tested with isolated SQL fixtures, without
creating production users. GitHub CI continues to run public-repo, backend and
frontend regression jobs on the final commit.

Operational result: SCHEMA_APPLIED_MAPPING_PENDING.
