import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { PGlite } from '@electric-sql/pglite'

// Isolated Postgres-compatible rehearsal: 011 is deliberately absent remotely.
// Reproduce the observed Supabase default ACL before 012 creates the E4 tables.
const db = new PGlite()
const dir = join(process.cwd(), 'supabase', 'migrations')
const tables = ['forecast_calculations', 'forecast_calculation_horizons', 'forecast_selection_events',
  'forecast_capture_sessions', 'forecast_live_evaluations', 'forecast_learning_events', 'forecast_live_closures']
const privileges = ['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER']
const mutable = new Set(['forecast_calculations', 'forecast_capture_sessions'])
const one = async sql => (await db.query(sql)).rows[0]
const denied = async sql => {
  try { await db.exec(sql) } catch (error) { assert.equal(error.code, '42501', error.message); return }
  assert.fail(`Expected permission denial: ${sql}`)
}

try {
  await db.exec(`
    create role anon; create role authenticated; create role service_role bypassrls;
    create schema auth; create table auth.users(id uuid primary key);
    create function auth.uid() returns uuid language sql stable as $$
      select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid $$;
    grant usage on schema auth to authenticated,anon,service_role;
    grant execute on function auth.uid() to authenticated,anon,service_role;
    create schema storage;
    create table storage.buckets(id text primary key,name text not null,public boolean not null);
    create table storage.objects(id uuid primary key default gen_random_uuid(),bucket_id text not null references storage.buckets(id),name text not null);
    alter table storage.objects enable row level security;
    grant usage on schema storage to authenticated,anon,service_role;
    grant select,insert,update,delete on storage.objects to authenticated,anon,service_role;
    grant select on storage.buckets to authenticated,anon,service_role;
  `)
  const files = readdirSync(dir).filter(name => /^20.*\.sql$/.test(name) &&
    !name.startsWith('202610080011_')).sort()
  assert.ok(files.some(name => name.startsWith('202610080012_')))
  assert.ok(files.some(name => name.startsWith('202610080013_')))
  for (const file of files) {
    if (file.startsWith('202610080012_')) {
      await db.exec('alter default privileges in schema public grant all on tables to anon,authenticated,service_role')
    }
    if (file.startsWith('202610080013_')) {
      for (const table of tables) for (const privilege of privileges) {
        assert.equal((await one(`select has_table_privilege('service_role','public.${table}','${privilege}') as allowed`)).allowed,
          true, `pre-013 materialized ACL: ${table}.${privilege}`)
      }
      const before = await one("select defaclacl::text as acl from pg_default_acl d join pg_namespace n on n.oid=d.defaclnamespace where n.nspname='public' and defaclobjtype='r' and defaclrole=(select oid from pg_roles where rolname=current_user)")
      await db.exec(readFileSync(join(dir, file), 'utf8'))
      const after = await one("select defaclacl::text as acl from pg_default_acl d join pg_namespace n on n.oid=d.defaclnamespace where n.nspname='public' and defaclobjtype='r' and defaclrole=(select oid from pg_roles where rolname=current_user)")
      assert.deepEqual(after, before, '013 must not change global default privileges')
    } else {
      await db.exec(readFileSync(join(dir, file), 'utf8'))
    }
  }
  process.stdout.write('E4 011 excluded; broad materialized ACL reproduced; 013 applied locally PASS\n')

  let assertions = 0
  for (const table of tables) for (const privilege of privileges) {
    const expected = privilege === 'SELECT' || privilege === 'INSERT' ||
      (privilege === 'UPDATE' && mutable.has(table))
    for (const [role, allowed] of [['service_role', expected],
      ['authenticated', privilege === 'SELECT'], ['anon', false]]) {
      assert.equal((await one(`select has_table_privilege('${role}','public.${table}','${privilege}') as allowed`)).allowed,
        allowed, `${role}: ${table}.${privilege}`)
      assertions++
    }
    assert.equal((await one(`select relrowsecurity as enabled from pg_class where oid='public.${table}'::regclass`)).enabled, true)
  }
  process.stdout.write(`E4 effective privilege matrix and RLS ${assertions}/147 PASS\n`)

  await db.exec('set role service_role')
  try {
    for (const table of tables) {
      await db.exec(`select count(*) from public.${table}`)
      await denied(`delete from public.${table} where false`)
      await denied(`truncate public.${table}`)
      if (mutable.has(table)) await db.exec(`update public.${table} set status=status where false`)
      else await denied(`update public.${table} set id=id where false`)
    }
  } finally { await db.exec('reset role') }
  await db.exec('set role authenticated')
  try {
    for (const table of tables) {
      assert.equal((await one(`select count(*)::int as n from public.${table}`)).n, 0)
      await denied(`delete from public.${table} where false`)
      await denied(`update public.${table} set id=id where false`)
    }
  } finally { await db.exec('reset role') }
  await db.exec('set role anon')
  try { for (const table of tables) await denied(`select count(*) from public.${table}`) }
  finally { await db.exec('reset role') }
  process.stdout.write('E4 service/browser SQL permission behavior PASS\n')

  const rpc = (await db.query("select proname,has_function_privilege('service_role',p.oid,'EXECUTE') as service,has_function_privilege('authenticated',p.oid,'EXECUTE') as browser,has_function_privilege('anon',p.oid,'EXECUTE') as anon from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='public' and proname like 'e4_%'")).rows
  assert.equal(rpc.length, 5)
  assert.ok(rpc.every(row => row.service && !row.browser && !row.anon))
  process.stdout.write('E4 write RPC permissions 5/5 PASS\n')
} finally { await db.close() }
