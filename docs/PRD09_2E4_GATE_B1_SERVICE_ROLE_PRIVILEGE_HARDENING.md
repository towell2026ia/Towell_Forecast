# PRD 09.2E4 Gate B.1 — service_role privilege hardening

The linked project is `bskoyqhbgrycpwhydnnr`. Read-only catalog inspection
on 2026-10-09 found that all seven E4 tables are owned by `postgres`, while
their materialized `relacl` grants `service_role` SELECT, INSERT, UPDATE,
DELETE, TRUNCATE, REFERENCES and TRIGGER. `service_role` has no parent role;
none of the seven ACLs grants to PUBLIC. Broad `pg_default_acl` entries exist
for future `public` tables, but this gate intentionally does **not** alter
those defaults or any non-E4 table. The current effective source is DIRECT_ACL;
the catalog does not prove which historical statement materialized it.

Migration `202610080013_e4_service_role_privilege_hardening.sql` revokes that
table-specific ACL and grants exactly SELECT/INSERT/UPDATE to calculations
and capture sessions, SELECT/INSERT to the five append-only tables. Its
postcondition aborts atomically if effective rights, PUBLIC grants, browser
rights or RLS differ from the approved matrix. It neither changes the E4
write RPCs nor the existing immutability triggers.

The local PGlite rehearsal deliberately omits migration 011, recreates the
broad default ACL seen remotely before creating E4 tables, then applies 012
and 013. It checks all 147 role/table/privilege results, SQL permission
behavior, RLS, unchanged default ACL and all five RPC EXECUTE grants.
`test_e4_schema.mjs` separately exercises C1/C2, valid state transitions,
invalid mutation/deletion, capture and append-only LIVE behavior.

Remote status at preparation: 012 applied; 011 and 013 not applied; seven E4
tables empty; historical count 39,270 and certified business-lineage SHA
unchanged; Champion and vintages empty. **No remote migration or flag change
was made in Gate B.1.**

Do not use ordinary `supabase db push` for this gate: the read-only dry-run
stops because 011 is a pending migration before 012. `--include-all` would
also select 011 and is prohibited. Migration 013 requires a separately
approved, selective application and migration-history procedure. The four
E4 runtime flags and Champion publication remain off until that later gate.
