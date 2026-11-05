# Private replenishment planning

This branch adds a local Excel adapter and a private Periodos viewer. It does not
write to a database, publish a Champion, change production horizons or run a model
when filters change. Commercial sources, snapshots, reports and XLSX files belong
outside this repository. No deployment is part of this change.

## Generate an immutable snapshot

Run `python -m services.planning_pilot.engine --help`. Supply an explicit source,
output JSON, cutoff, version ID, code SHA and actual closure confirmation date.
An existing output is never overwritten. Use the actual issue timestamp assigned
by the command; never backdate issuance to the closure month.

The adapter reads stored values from the three historical sources and joins the
definitive proposal by account and barcode. It inventories all workbook sheet XML,
formula counts and hashes without updating external links. Proposal-only and
history-only identities are retained. Original cells, decimals, zeros and blanks
remain available in the snapshot. Conflicting identities and individual model
errors become explicit exceptions. Assorted variants are never split.

Packaging factors are evidence about proposals, not historical units. The default
is an unknown native unit with no piece conversion. Optional conversion evidence
must identify source, account/barcode, period validity, unit, positive conversion
factor and its evidence reference. Model aggregates include only accredited pieces,
within account, format, article/size and commercial code. Missing codes are isolated.

## Method and horizon policy

The pilot reuses `services.statistical_engine.engine.run_series` on the uninterrupted
Towell series ending at the supplied closure. It does not impute missing months.
Existing lifecycle and method-selection rules apply; forecast rounding remains
the existing engine's rounding, while source quantities keep their full precision.
No ML/Ensemble/Cold Start is run without separately establishing eligible inputs.
No published Champion evidence is imported by this adapter, so the official view
explicitly says `SIN_CHAMPION`. Statistical outputs are provisional.

H13/H14 are an isolated extension. Each horizon requires:

- Exact preservation of the existing H1–H12 output and finite values.
- At least three rolling validation origins with a positive actual denominator.
- Retrospective WAPE no greater than 1.25 times the Naive benchmark.
- Sensitivity to removing the last training month, forecast to the same target,
  no greater than 50% of the recent twelve-month mean.
- A projection no greater than three times the recent twelve-month maximum.

These are conservative technical screening gates, not a business accuracy target
or temporal certification. Failure leaves a null with `Sin cobertura`. Policy and
per-horizon validation metrics are stored in the private JSON.

Retrospective H1 WAPE and signed Bias are ratios of sums. The selected method was
chosen using the full sample, so these metrics are not independent validation.
Cross-source comparisons require accredited common units; a similar value or
packaging factor is insufficient. LIVE additionally requires a closed actual and
an issue timestamp strictly before the target month's start. No fabricated LIVE
score or averaged per-product WAPE is produced.

## Revisions, viewer and export

`--previous` retains every prior snapshot byte-for-byte in logical content, checks
its hash chain and requires a cutoff at least two months later and a later issue
timestamp. New identities have a blank original, not their revised value copied
into the original column. Only genuinely generated versions are listed.

`lib/planning-results.ts` supplies the same filtered rows and numbers to Periodos
and the XLSX builder. Open `/planeacion-privada` locally, or choose private planning
inside Periodos, and select the JSON file. It is read into browser memory, never
uploaded. Multi-select scope, model/article-size, color, presentation and product
filters do not run forecasts. The original graph is reused. Its rolling window is
nine real months through the cut and three subsequent months, with two prior
twelve-month windows. Unknown native units cannot be aggregated in that graph.

Run the exporter using a compatible Node runtime with TypeScript stripping:

```text
node --experimental-strip-types scripts/export-planning.mjs PRIVATE_INPUT.json PRIVATE_OUTPUT.xlsx BUNDLED_NODE_ROOT
```

The last argument must resolve the separately supplied `@oai/artifact-tool` library;
the authoring runtime is not bundled into the portal or uploaded to CI. The XLSX
contains ten requested planning sheets, frozen model outputs and transparent
arithmetic formulas for metrics, differences and accredited model totals. Editing
the workbook does not retrain the model or change its immutable JSON snapshot.
The exporter refuses overwrite and writes private visual/check files beside the
workbook. Use a new version for another issuance.

## Verification and boundaries

Python tests cover source identity union, decimals, blank versus zero, conversion
scope, extension invariants/stability, WAPE timing and isolated SKU failures.
Portal tests cover code separation, assorted products, honest partial coverage,
multiselects, calendar rollover, original revisions and shared chart/export values.
Only synthetic fixtures run in CI. Validate the real-source controls and saved XLSX
privately, including formula caches, coverage, source hashes and no external links.

The private viewer is prepared for local use. Production loading, deployment,
administrative classification, publication and adoption require separate instructions.
