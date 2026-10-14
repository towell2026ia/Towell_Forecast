# Commercial Catalog: PRD 09.2E5-01B-A

## Boundaries and Deliverables

This is a global, multicategory, multichain commercial classification foundation.
It adds an offline proposal analyzer, configurable business rules, four normalized
tables, an RLS-aware current view, and isolated regression tests. It does not mount
an API writer, add forecast features, change comparables, seed commercial rows,
or change Selection/Capture/LIVE/Champion/Vintage/publication flags.

The production export and complete approval packet are private local deliverables
under `outputs/prd09_2e5_01b_a/`; they are deliberately excluded from public Git.
The matrix includes every authorized product, even insufficient-evidence records.
Dictionary inventories are **candidates**, not approved master data.

## Real Identity and Evidence

The existing product key is a UUID. Its natural identity is
`(chain_id, product_code, coalesce(variant_code,''))`. Neither the key nor ITEM/UPC
is changed. The code is not assumed to be UPC. Original identifiers are strings,
including leading zeros; empty identifiers are missing evidence, not duplicates.

Versioned import-profile metadata contains `description_aliases`,
`historical_category_names` and `identifier_mappings` (ITEM, UPC pairs). All versions
are included; an alias conflict is retained, not silently replaced by the newest
name. The product category may be NULL; this differs from an invented category.
Two categories on past facts do not justify editing facts or current master data.

Identical identifiers on different chains do not imply identical product identity.
Same-chain nonempty identifier collisions are reported with every affected UUID.
The read is authenticated and RLS-filtered. A complete export is complete for the
specified identity, not a claim to bypass inaccessible scopes.

## Verified Rules and Assisted Proposals

Rules live in `config/commercial_catalog_rules.json`, with a version and digest.
No model-name allowlist exists. New designs can be proposed without code changes.

1. Leading whole-word TOALLA, TRAPO, TAPETE and BATA are generic product types;
   TOWELL is a brand, never a standalone model. Original descriptions are retained.
2. TOWEL is not automatically a model or an approved brand equivalence. A proposal
   can omit the leading descriptor but remains pending with its ambiguity recorded.
3. MB/Medio Bano, Facial, Bano and Pullman/Pull form four presentation groups,
   globally, including FIORE and GRANADA. Original literal denominations remain
   attached to their product. No conversion to three sizes or physical measures.
4. Apparel denominations are literal proposals, not undocumented equivalences.
   Physical dimensions are recognized only with an explicit unit in the text.
   Opaque SKU/variant codes never supply dimensions.
5. The six user-supplied color examples bootstrap suffix recognition. Other suffix
   labels are discovered contrastively from the actual corpus. Discovery is not
   proof of color semantics: every discovered label requires commercial review.
   Compound color labels and model names are preserved. OXFORD is a color.
6. Unresolved retail/season abbreviations, material/assortment descriptions, uncertain
   model boundaries, legacy names and conflicting aliases remain pending.
7. No classifier output is APPROVED. Every record has an explanation; 100% analysis
   coverage is independent of approval coverage. A NULL size/color with UNKNOWN
   does not mean NOT_APPLICABLE. The latter needs explicit commercial evidence.

`KNOWN` in a local proposal means a named candidate was extracted, **not** that it
is commercially approved. Final dictionary identity and equivalences are reviewed
separately. Case/accent-folded comparison keys group evidence for review only;
they do not authorize merging names such as Cashmere/Cashimere, Whitecap/WhiteCap
or distinct products. The SQL uniqueness key only normalizes case/whitespace.

Run against a previously captured export, without network access:

```powershell
.venv/Scripts/python.exe scripts/analyze_commercial_catalog.py `
  --catalog outputs/prd09_2e5_01b_a/catalog_readback.json `
  --output-dir outputs/prd09_2e5_01b_a
```

The packet contains original records, aliases, metadata version IDs, per-description
proposals, exact identifier pairs, explanations, inventories, per-chain totals,
input/product/rules/mapping digests and original integrity fingerprints. JSON is
authoritative; CSV quotes dangerous formula-like input for spreadsheet safety.
The separately generated review workbook preserves opaque identifiers as text.

## Schema and Referential Integrity

Migration `202610140015_commercial_catalog.sql` is **schema only**. It is not applied
remotely by this task. There are no data seeds or dependencies on forecast engines.

- Global `commercial_models` and `commercial_colors` allow reuse across chains.
- `commercial_sizes` supports presentations and documented explicit dimensions.
- `product_commercial_classifications` is append-only, with consecutive per-product
  versions, exact predecessor, administrative creator, optional approval identity,
  evidence, original attribute labels, rules version and mapping-packet digest.
- Composite FKs bind product/category to chain. An insert trigger checks category
  equality even when NULL and compares the complete product snapshot to the current
  product. A stale approval packet fails; product-row locking serializes revisions.
- APPROVED requires known model and known or documented not-applicable color/size,
  active administrator, approval reference and nonfuture approval timestamp.
- A reviewed reversal is a new REJECTED/PENDING revision, not an UPDATE/DELETE.
- The current view includes new unmapped products as PENDING with an explanation.
  Changed product snapshots are visibly stale/pending; prior events remain intact.

## RLS and Administrative Gate

All four tables have RLS. Classification reads use existing active-profile and
`private.can_chain(chain_id,'view')` checks. Dictionaries expose only names referenced
by a visible classification (or all names to an active administrator). Shared
dictionary evidence/creator columns are not granted to browser identities because
that evidence could mention another chain. Classification evidence remains scoped.

The view uses `security_invoker=true`, following the
[Supabase RLS view guidance](https://supabase.com/docs/guides/database/postgres/row-level-security#views).
Its base-table policies and grants apply to the caller. Role grants complement RLS;
RLS alone does not restrict a BYPASSRLS service role. See
[Supabase API security](https://supabase.com/docs/guides/api/securing-your-api).

**Anon, authenticated (including ADMIN), and service_role receive no DML or TRUNCATE
rights on commercial tables. No executable writer RPC is exposed.** Existing
application privileges are untouched. Initial approved mapping requires an audited
database-owner transaction after both authorizations; no blanket runtime grant.
User-friendly administrative CRUD is outside this review-only delivery and would
need its own reviewed authorization boundary before mounting write endpoints.

## Two Independent Production Authorizations

1. **Schema approval**: review this migration/digest, backup/restore readiness and
   local SQL tests; authorize one schema-only application. Verify empty new tables,
   grants/RLS/view behavior and unchanged business fingerprints afterward.
2. **Mapping approval**: review the sealed packet/hash and product UUID scope, resolve
   ambiguities or explicitly keep them PENDING, validate dictionaries/equivalences
   and NOT_APPLICABLE evidence, identify the active administrative reviewer and
   record an explicit approval reference. Regenerate if inventory/snapshot changed.

Before a mapping transaction, compare all source products/profile versions and
counts against the sealed export, not just the selected pilot. Require explicit
per-product outcomes and zero missing products. Insert dictionary/classification
rows only in the reviewed transaction, then reconcile UUID coverage and fingerprints.
No automatic authorization is implied by a workbook edit or a proposal status.
The existing C1 and zero human selections are independent and must stay unchanged.

## Rollback and Tests

`supabase/rollback/202610140015_commercial_catalog.sql` requires separate approval,
locks new tables and refuses to drop any populated commercial table. It contains no
CASCADE and never changes original tables. For a populated catalog, disable any
future writer, retain/export all evidence and append reviewed reversal revisions.
Do not destroy commercial audit history as a rollback mechanism.

Run:

```text
python -m unittest services.assistant_api.test_commercial_catalog -q
node scripts/test_commercial_catalog_schema.mjs
```

Tests cover unknown/new/multiword designs, simple/compound colors, FENDI+OXFORD,
FIORE/GRANADA/other-model four presentations, originals, no-size/unknown colors,
ambiguous tokens, aliases, identifier collisions, multichain reuse, Unicode safety,
incomplete exports, all RLS roles, composite integrity, approvals, append-only
versions, new unmapped products, no historical changes, and rollback refusal.
Synthetic fixtures are confined to isolated local test engines, never production.

Closure is READY_FOR_MAPPING_APPROVAL only when every real exported UUID is present
and either approved by the human gate or pending with verifiable evidence/reason.
Unresolved classifications do not justify forcing approval or inventing data.
