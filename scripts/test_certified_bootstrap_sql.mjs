// Synthetic PostgreSQL execution only. No Supabase credentials or network.
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { PGlite } from '@electric-sql/pglite'

const fixture = JSON.parse(execFileSync(process.env.BOOTSTRAP_TEST_PYTHON || 'python', ['-c', `
import json
from services.assistant_api.test_global_certification import fixture,certify
from services.assistant_api.test_certified_bootstrap import expected
from services.assistant_api.certified_bootstrap import build_plan
from services.assistant_api.bootstrap_sql import stage_setup_sql,stage_chunk_sql,publish_sql,readback_sql,cleanup_sql,security_probe_sql,measurement_sql
a=fixture(); c=certify(a); p=build_plan(a[0],c,expected(c),code_commit='1'*40,bootstrap_at='2026-09-26T00:00:00Z')
actor='00000000-0000-4000-8000-000000000101'
print(json.dumps({'actor':actor,'plan':p,'setup':stage_setup_sql(p,actor),'chunk':stage_chunk_sql(p,actor,0),
                 'failure':publish_sql(p,actor,simulate_failure=True),'publish':publish_sql(p,actor),'readback':readback_sql(p),
                 'cleanup':cleanup_sql(p,actor,certification_failed=True),'security':security_probe_sql(p,actor),
                 'measurement':measurement_sql(p)}))
`], { encoding: 'utf8', maxBuffer: 10 * 1024 * 1024 }))
const db = new PGlite()
let passed = 0
const check = async (name, fn) => { await fn(); passed++; console.log(`BOOTSTRAP SQL PASS ${name}`) }
const count = async (table) => Number((await db.query(`select count(*) as n from public.${table}`)).rows[0].n)
try {
  await db.exec(`create role anon; create role authenticated; create role service_role bypassrls;
    create schema auth; create table auth.users(id uuid primary key);
    create function auth.uid() returns uuid language sql stable as $$select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid$$;
    grant usage on schema auth to authenticated,anon;
    create schema storage; create table storage.buckets(id text primary key,name text not null,public boolean not null);
    create table storage.objects(id uuid primary key default gen_random_uuid(),bucket_id text not null references storage.buckets(id),name text not null);
    alter table storage.objects enable row level security;
    grant usage on schema storage to authenticated,anon,service_role;
    grant select,insert,update,delete on storage.objects to authenticated,anon,service_role;
    grant select on storage.buckets to authenticated,anon,service_role;`)
  for (const file of ['contract_core', 'contract_forecast', 'contract_governance', 'contract_integrity', 'auth_rls', 'private_storage', 'approval_probe_guard'].map((s, i) => `20260925000${i + 1}_${s}.sql`)) {
    await db.exec(readFileSync(join('supabase/migrations', file), 'utf8'))
  }
  await db.exec(`insert into auth.users(id) values ('${fixture.actor}'); insert into public.profiles(id,global_role) values ('${fixture.actor}','ADMIN');`)
  await check('logical STAGING setup', async () => {
    await db.exec(fixture.setup)
    assert.equal((await db.query('select status from public.jobs')).rows[0].status, 'STAGING')
    for (const t of ['chains', 'products', 'monthly_observations', 'import_batches', 'source_evidence']) assert.equal(await count(t), 0)
  })
  await check('incomplete staging rolls back', async () => {
    await assert.rejects(db.exec(fixture.publish), /staging_incomplete/)
    await db.exec('rollback')
    assert.equal(await count('chains'), 0)
  })
  await check('stage chunk idempotency', async () => {
    await db.exec(fixture.chunk)
    await db.exec(fixture.chunk)
    assert.equal(await count('run_logs'), 1)
  })
  await check('publication failure rolls back ALL masters and facts', async () => {
    await assert.rejects(db.exec(fixture.failure), /controlled_publication_failure/)
    await db.exec('rollback')
    for (const t of ['chains', 'categories', 'products', 'import_profiles', 'import_profile_versions', 'import_batches', 'source_evidence', 'monthly_observations', 'audit_log']) assert.equal(await count(t), 0)
    assert.equal((await db.query('select status from public.jobs')).rows[0].status, 'STAGING')
  })
  await check('atomic successful publication', async () => {
    await db.exec(fixture.publish)
    assert.equal(await count('monthly_observations'), fixture.plan.observations.length)
    assert.equal(await count('products'), fixture.plan.products.length)
    assert.equal(await count('chains'), fixture.plan.scopes.length)
    assert.equal((await db.query('select status from public.jobs')).rows[0].status, 'COMPLETED')
  })
  await check('physical batch state machine intact', async () => {
    assert.ok((await db.query('select status from public.import_batches')).rows.every(r => r.status === 'IMPORTED'))
    await assert.rejects(db.exec("update public.import_batches set status='STAGING'"))
  })
  await check('UNKNOWN NULL + real joined readback', async () => {
    const results = await db.exec(fixture.readback)
    const rows = results.flatMap(r => r.rows || [])
    assert.equal(rows.length, fixture.plan.observations.length)
    assert.ok(rows.every(r => r.available_at === null && r.availability_source === 'UNKNOWN' && r.lineage.canonical_fact.cell))
  })
  await check('second complete run creates no duplicates', async () => {
    const before = (await db.exec(fixture.measurement)).flatMap(r => r.rows || [])
    await db.exec(fixture.setup); await db.exec(fixture.chunk); await db.exec(fixture.publish)
    assert.equal(await count('monthly_observations'), fixture.plan.observations.length)
    assert.equal(await count('audit_log'), 1)
    const after = (await db.exec(fixture.measurement)).flatMap(r => r.rows || [])
    assert.deepEqual(after, before)
  })
  await check('viewer/editor/admin/cross-chain security probe is rolled back', async () => {
    const rows = (await db.exec(fixture.security)).flatMap(r => r.rows || [])
    assert.equal(rows.length, 10); assert.ok(rows.every(r => r.ok))
    assert.equal((await db.query('select global_role from public.profiles')).rows[0].global_role, 'ADMIN')
    assert.equal(await count('user_chain_access'), 0)
  })
  await check('RLS remains enabled 28/28', async () => {
    const rows = (await db.query("select relrowsecurity from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relkind='r'")).rows
    assert.equal(rows.length, 28); assert.ok(rows.every(r => r.relrowsecurity))
  })
  await check('append-only guards intact', async () => {
    await assert.rejects(db.exec('delete from public.monthly_observations'), /append-only/)
    assert.equal(await count('monthly_observations'), fixture.plan.observations.length)
  })
  await check('cleanup refuses unrelated references and fully rolls back', async () => {
    await db.exec(`insert into public.chains(id,code,name) values ('00000000-0000-4000-8000-000000009999','UNRELATED','Unrelated');
      insert into public.user_chain_access(user_id,chain_id,can_view) values ('${fixture.actor}','${fixture.plan.scopes[0].chain_uuid}',true);`)
    await assert.rejects(db.exec(fixture.cleanup))
    await db.exec('rollback')
    assert.equal(await count('monthly_observations'), fixture.plan.observations.length)
    await assert.rejects(db.exec('delete from public.monthly_observations'), /append-only/)
    await db.exec(`delete from public.user_chain_access where user_id='${fixture.actor}';`)
  })
  await check('scoped withdrawal preserves unrelated data/audit/lineage', async () => {
    await db.exec(fixture.cleanup)
    assert.equal(await count('monthly_observations'), 0)
    assert.equal(await count('chains'), 1)
    assert.equal((await db.query('select code from public.chains')).rows[0].code, 'UNRELATED')
    assert.equal(await count('audit_log'), 2)
    assert.equal(await count('run_logs'), 1)
    assert.equal((await db.query('select status from public.jobs')).rows[0].status, 'RETIRED_FAILED_CERTIFICATION')
    const guards = (await db.query("select tgenabled from pg_trigger where tgname in ('observations_insert_only','source_evidence_insert_only','import_batch_no_delete','import_profile_versions_insert_only')")).rows
    assert.equal(guards.length, 4); assert.ok(guards.every(r => r.tgenabled === 'O'))
  })
  console.log(`certified_bootstrap_sql: ${passed}/${passed} PASS`)
} finally { await db.close() }
