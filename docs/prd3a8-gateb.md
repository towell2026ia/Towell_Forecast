# PRD 3A.8 — Gate B: laboratory implementation

This branch is a native PostgreSQL prototype, **not a production migration or an
application release**. It does not add HTTP routes, change charts, run forecasts,
load business releases or alter C1 / V01–V03 / WAPE / Champions.

## Isolation before every connection

`scripts/gateb.py` accepts no URL, service key, `.env` or remote host. Config files
must name `127.0.0.1`, a `gateb_*` database and a 32-hex server marker. Connections
explicitly set both host and hostaddr, then verify the actual server address,
port, listen_addresses, database, marker, system_identifier and data_directory.
Production project references and extra config fields fail with `STOP_B0`.
Writer methods reject connections not returned by the verified connector.

An operator must first provision a fresh, disposable PostgreSQL >=15 instance,
bound **only** to 127.0.0.1, with `gateb.lab_id=<random 32-hex token>`. No Windows
service or remote project is required. The local acceptance run uses portable
PostgreSQL 17.11 and verified Supabase CLI 3.0.0. CLI `migration new` created the
four new lab migrations. Existing migrations remain untouched.

Example local config (all values are laboratory metadata, never production
credentials):

```json
{
  "host": "127.0.0.1",
  "port": 65438,
  "database": "gateb_example",
  "lab_id": "32 lowercase hexadecimal digits from the server configuration",
  "system_identifier": "verified numeric server identifier",
  "data_directory": "verified local server data directory"
}
```

Use `probe` to produce a valid config from the independently known marker:

```text
python -B scripts/gateb.py probe --database gateb_example --lab-id <marker> --port 65438 --output <private>/config.json
python -B scripts/gateb.py prepare --config <private>/config.json --output <new-private>/preparation
python -B scripts/gateb.py test --config <private>/config.json --output <new-private>/tests
python -B scripts/gateb.py demonstrate --config <private>/config.json --output <new-private>/demo
python -B scripts/gateb.py recover --config <private>/config.json --postgres-bin <local-pg-bin> --output <new-private>/recovery
```

Install the existing API requirements plus `lab/prd3a8/requirements.txt` in an
isolated environment. All output directories must be new and outside Git.
The protocol is synthetic-only; `prepare` creates the test A/B/C fixtures, not a
release of the private workbook. There is no command to load or activate a real
operational candidate. Run `--help` before using a command.

## SQL and precision

`lab/prd3a8/auth_fixture.sql` emulates the minimal Auth schema/claims contract
with synthetic users so native PostgreSQL can execute the **real existing RLS**.
It is not an Auth server and does not certify hosted Auth/API integration. The
original core/forecast/governance/integrity/RLS migrations are applied to the
empty local database. A SQL guard rejects an unmarked or non-loopback server.

The additive lab migrations expand the CHECK to five independent metrics and
evaluate `numeric(31,16)` (15 integer digits, 16 fractional digits), while keeping
the old three-metric views compatible. A separate temporary `numeric` column
tests unconstrained decimal storage. Neither is a production precision decision.
Values pass Decimal range/scale validation **before** a typed SQL cast can round
them. Unknown metrics, negative/nonfinite values, floats and invalid source
batches fail. Explicit zero is stored; absence is a manifest absence, not zero.

The existing parser opts into the historical metrics only through the explicit
boolean `include_historical_forecasts`; its default remains the three existing
metrics. Source mappings are Towell -> FCST_TOWELL_HISTORY, Cliente ->
FCST_CLIENTE_HISTORY, Pedidos -> ORDER. SALES and DELIVERY have independent
sources. A narrow Decimal normalization fix prevents the default 28-digit
context from rounding a valid 31-digit quantity. This is parsing, not a change
to a statistical method.

The lab writer only appends observations for existing products and source
batches of the same chain/period. It never creates or updates descriptions,
categories, identifiers or products. A repeated normalized row reuses its ID;
corrections allocate the next physical version under a per-key advisory lock.
All historical fixture forecasts remain UNKNOWN/NULL for availability.

## Explicit release sets and RLS

Three metadata tables hold versions, complete membership by logical key ->
observation ID, and a singleton active pointer plus generation. Composite FKs
validate the observation's chain/product/month/metric. Sealing verifies scope,
explicit carry-forward, catalog retention, absences and imported source batches.
The hash covers the complete document, members, quantities, versions, lineage,
catalog/physical guards and declared absences; timestamps are canonical in UTC.
Sealed metadata and members are immutable.
The global laboratory baseline must include the entire catalog, including
products without observations, and the complete initial imported selection.
Additional independent roots are rejected after initialization. Later releases
must explicitly retain all parent logical keys and catalog products.

The laboratory pointer covers one complete synthetic dataset across two chains.
It does not choose a production global/per-chain publication policy. The reader
captures an immutable Context once and uses its version ID for all reads/export.
There is no latest-version fallback. The legacy max-version view intentionally
demonstrates the staging leak in T16; the operational lab API never uses that
view. A production cutover must close those older paths first, under another
authorization. No deployed application or public API changes in Gate B.

Activation checks active ADMIN, expected ID/generation, sealed hash, catalog and
the first-use post-staging physical guard. It takes the pointer row lock plus a
short shared table lock and writes pointer/audit in one transaction. A failure
after pointer/audit rolls back both. Previous releases can be selected again
without deleting retained candidate facts. Repeated requests are idempotent;
generation prevents A->B->A stale requests from succeeding. Reversion restores
the exact prior member IDs, including disappearance of B-only logical keys.

Data readers are invoker/RLS-scoped. Users can read only authorized chain
members and non-sensitive header columns; global documents/counts are not
granted. Anonymous reads and direct active-pointer writes are denied. Private
sealing/activation procedures need narrowly elevated privileges, but verify the
existing active ADMIN helper before accessing a full manifest; they return no
business rows. No frontend service key or RLS read bypass is introduced.

## Tests and evidence

`services/operational_versions/test_postgres.py` implements the PRD's T01–T30
against actual PostgreSQL, including two real simultaneous connections and
`SET ROLE authenticated` with synthetic JWT subject claims. No in-memory mock
is used as evidence of transactions, CAS or RLS.

T14 optionally audits the private 3A.7 candidate outside CI:

```text
python -B scripts/gateb.py test --config <private>/config.json --output <new-private>/tests --private-candidate <private>/candidata_validada
```

The audit verifies the old package seal and round-trips all 72 linked precision
cases (28 ORDER, 28 Towell, 16 Cliente) through temporary SQL columns of both
types. Its private CSV includes identity, period, cell, original/exact value,
scale, sign, integer digits and both SQL readbacks. Original files and the 427
identities / 151 commercial groups are checked without recalculating forecasts.
Temporary precision rows disappear when the test transaction ends.

CI contains synthetic inputs only. T14 is explicitly **SKIP, not PASS**, there;
private input arguments are rejected in GitHub Actions. A green CI with that
skip does not certify the private case; the local 30/30 result does. Results are
reported per case in results.json. The exact-evidence XLSX contains literal text
quantities, immutable version/hash and a SYNTHETIC_LAB_NOT_OPERATIONAL label.
It does not replace or modify existing commercial exporters or workbooks.

`recover` dumps only the synthetic laboratory and restores into a new marked
local database, checking row counts and hashes for every public/auth/private
table. This is a Gate B fixture recovery test; it is not Gate C, a production
restore, a Storage backup, or an operational rehearsal of the real candidate.

## Reserved for Gate C/D

- Choose production numeric precision and exact browser/Excel transport policy.
- Approve final metadata/pointer scope and write privileges; no new user roles.
- Approve version-aware data-reader and legacy cutover contracts, without a
  graphical redesign. Hosted Auth/API and deployment integration remain untested.
- Resolve/exclude the 22/49 identity cases and reevaluate description exceptions
  in a new immutable operational candidate, not the frozen 3A.7 output.
- Authorize full data/Storage backup and restore rehearsal with the real scope.
- Authorize each production schema change, deployment, manifest and activation
  separately. Gate B authorizes none of them and runs no new forecast/Champion.
