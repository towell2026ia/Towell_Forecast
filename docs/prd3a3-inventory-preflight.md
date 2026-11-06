# PRD 3A.3 — Source audit and inventory preparation

Operational integration is stopped until account/scope/product mappings, the store
universe and the inventory source have documented business approval. This change
does not create an ingestion API, write to Supabase, apply SQL or enable capture.

## Source contract

| Private source | Confirmed meaning | Private field | Consultation label |
| --- | --- | --- | --- |
| Towell, K:M | Historical internal forecast | `history.Towell` | Fcst Towell histórico |
| Cliente, K:M | Received forecast | `history.Cliente` | Fcst Cliente recibido |
| Pedidos, K:M | Actual orders | `history.Pedidos` | Pedido real |
| Venta | Independent actual source; absent from private bundle | No fallback | Sin dato |
| Entrega | Independent delivery source; absent from private bundle | No fallback | Sin dato |

The historical workbook's literal `Venta` headers do not override the user's
PRD 3A.3 source confirmation. The private statistical projections extrapolate
historical internal forecasts, not accredited actual demand. They remain frozen
provisionals. WAPE algorithms and stored values are untouched; consultation labels
flag inherited indicators as unsuitable for certifying actual-sales LIVE accuracy.
Future target months in received/internal forecasts are retained, regardless of
the last actual closure. Original issue timestamps still require accreditation.

Production actuals have a separate lineage: `monthly_observations.value`, with
`metric_code=SALES|ORDER|DELIVERY`, `source_batch_id` and
`availability_evidence_id`. `source_evidence.description.canonical_fact` contains
sheet/cell/source hash. The literal reconstruction is in
`services/assistant_api/historical_corpus.py` and
`scripts/reconstruct_chain_history.py`; `lib/forecast-data.ts` reads the published
history view. BAASE columns V/W/X represent Venta/Pedido/Entrega. Walmart monthly
headers use POS Qty / Total Eaches Str Ordered / Total Eaches Str Received.
Literal provenance and time-of-availability evidence are separate requirements.
No automatic join of those production actuals to private products is introduced.

`auditedPlanningSheets` supplies both private Períodos tables and its downloadable
consultation XLSX. It adds a source interpretation layer, never edits the V01–V03
packages, changes chart geometry, changes public production actuals, or fits a
model. Downloads are explicitly named `Consulta_3A3`, not a replacement V03.

## Identity audit

`scripts/audit_inventory_catalog.py` consumes local read-only exports of current
Supabase products and import profiles. UPC uses the UPC member of
`identifier_mappings`; ITEM is never coerced into UPC. It audits every private
identity and checks target IDs against the current catalog. Case/accent/spacing
normalization provides only descriptive hints. It does not resolve accounts,
parent/child chain ambiguity, presentation or color differences automatically.
All candidates remain blocked pending a signed crosswalk. Private reports,
snapshots and CSV files belong outside Git.

## Offline capture and import contract

One UTF-8 CSV line per product/store/date/version, with this exact header:

```csv
account,chain_id,barcode,product_id,store,cutoff,version,pieces
```

Keep UPC, account, store and IDs as text. `pieces` is a nonnegative decimal string
with a dot (zero is valid; blank, negative, exponent notation and separators are
rejected). Version is a positive integer written as text, matching the existing
SQL `version_no`. No pack conversion or rounding. At most 1,000 characters per quantity
are accepted by the offline importer. Dates use YYYY-MM-DD. Missing rows mean
unknown inventory, never zero.

The manifest declares `account`, `chain_id`, `cutoff`, `version`, `unit: PIEZAS`,
`source_sha256`, `stores` (the complete expected set), `identities` (account|UPC),
and `status: APPROVED`, `evidence`, `approved_by`, `approved_at` with timezone.
Evidence must cover the source and expected store/product universe. This is a
review artifact, not authentication or authorization to write a database.

Mappings are an array, exactly one per expected identity, containing `identity`,
`account`, `chain_id`, `barcode`, `product_id`, `format`, `model`, `presentation`,
`commercial_code`, `color`, and the same four approval fields. Missing/ambiguous
attributes remain unapproved. The fresh catalog array contains `id`, `chain_id`,
`barcodes` and `status: ACTIVE`. `existing` is the full prior snapshot manifest
array; matching account/chain/date/version blocks replacement. No empty registry
may be substituted for real history when this is operationalized.

```powershell
.venv/Scripts/python.exe -m services.inventory_preflight `
  --csv <private-capture.csv> --manifest <approved-source.json> `
  --mappings <approved-crosswalk.json> --catalog <current-catalog.json> `
  --existing <all-existing-snapshots.json> --output <new-preview.json>
```

The CLI verifies the CSV SHA256 and opens output exclusively (`x`); source files
are read-only. Every bad row blocks the whole batch, leaving staged rows empty.
`READY_FOR_OFFLINE_REVIEW` is not permission to publish. `production_load` is
always false. Test only synthetic inventory during this phase.

Consolidation key: account + chain + format + model + presentation + commercial
code. One common cutoff/version/store universe per manifest. Decimal sums are
exact; each model reports products, stores and product×store pairs covered over
expected, with COMPLETE/PARTIAL/NO_DATA. No sum across dates, versions or series.

## Future activation and rollback

Reuse the existing, unapplied `20261105215012_pos_inventory.sql` only after a
separate authorization and schema/RLS review. No new table is needed in 3A.3.
A future server must authenticate chain access, validate signed registry versions,
check current catalog and complete snapshot history, and use a transaction plus
database uniqueness to prevent concurrent duplicate imports. Local preview alone
does not provide those production guarantees. No integration switch is enabled.

Rollback is code-only: revert this branch's presentation/preflight changes.
No database rollback is needed because no SQL or data was applied. Never delete
or overwrite V01–V03. Regression tests protect raw projections, metrics, source
separation, controls, model sums, zeros, missing values and import blocking.
