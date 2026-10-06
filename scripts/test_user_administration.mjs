import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { PGlite } from '@electric-sql/pglite'

const db = new PGlite()
const migrations = join(process.cwd(), 'supabase', 'migrations')
const id = n => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`
const [admin, secondAdmin, missing, viewer, chainA, chainB] = [1, 2, 3, 4, 5, 6].map(id)
const denied = async sql => { try { await db.exec(sql) } catch { return } assert.fail('expected denial') }
const call = (actor, target, name, role, status, grants) =>
  `select public.portal_manage_user('${actor}','${target}','${name}','${role}','${status}','${JSON.stringify(grants)}'::jsonb)`
try {
  await db.exec(`
    create role anon; create role authenticated; create role service_role bypassrls;
    create schema auth; create table auth.users(id uuid primary key);
    create function auth.uid() returns uuid language sql stable as $$
      select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
    grant usage on schema auth to anon, authenticated;
    grant execute on function auth.uid() to anon, authenticated;
    create schema storage;
    create table storage.buckets(id text primary key,name text not null,public boolean not null);
    create table storage.objects(id uuid primary key default gen_random_uuid(),
      bucket_id text not null references storage.buckets(id),name text not null);
    alter table storage.objects enable row level security;
    grant usage on schema storage to anon, authenticated, service_role;
    grant select,insert,update,delete on storage.objects to anon, authenticated, service_role;
    grant select on storage.buckets to anon, authenticated, service_role;
  `)
  for (const file of readdirSync(migrations).filter(f => /^\d+_.*\.sql$/.test(f)).sort()) {
    await db.exec(readFileSync(join(migrations, file), 'utf8'))
    process.stdout.write(`USER SQL PASS ${file}\n`)
  }
  await db.exec(`
    grant usage on schema public to service_role;
    grant all on all tables in schema public to service_role;
    insert into auth.users(id) values ('${admin}'),('${secondAdmin}');
  `)
  await db.exec(`insert into auth.users(id) values ('${missing}'),('${viewer}');
    insert into public.profiles(id,full_name,global_role,status) values
      ('${admin}','Primary Admin','ADMIN','ACTIVE'),('${secondAdmin}','Second Admin','ADMIN','ACTIVE');
    insert into public.chains(id,code,name) values ('${chainA}','CHAIN_A','A'),('${chainB}','CHAIN_B','B');`)
  const viewerGrant = [{ chain_id: chainA, can_edit: false, can_import: false,
    can_run_forecast: false, can_approve: false }]
  await db.exec('set role service_role')
  await db.exec(call(admin, missing, 'Missing Profile', 'VIEWER', 'ACTIVE', viewerGrant))
  let row = (await db.query(`select p.global_role,p.status,count(a.chain_id)::int as grants from public.profiles p
    left join public.user_chain_access a on a.user_id=p.id where p.id='${missing}' group by p.id`)).rows[0]
  assert.deepEqual([row.global_role, row.status, row.grants], ['VIEWER','ACTIVE',1])
  const editorGrant = [{ ...viewerGrant[0], can_edit: true, can_run_forecast: true }]
  await db.exec(call(admin, missing, 'Managed Editor', 'EDITOR', 'ACTIVE', editorGrant))
  row = (await db.query(`select p.global_role,a.can_edit,a.can_run_forecast from public.profiles p
    join public.user_chain_access a on a.user_id=p.id where p.id='${missing}'`)).rows[0]
  assert.deepEqual([row.global_role,row.can_edit,row.can_run_forecast], ['EDITOR',true,true])
  await denied(call(admin, viewer, 'Bad Viewer', 'VIEWER', 'ACTIVE', editorGrant))
  await denied(call(admin, viewer, 'Wrong Chain', 'VIEWER', 'ACTIVE', [{ ...viewerGrant[0], chain_id: id(99) }]))
  await denied(call(admin, viewer, 'Duplicate Chain', 'VIEWER', 'ACTIVE', [viewerGrant[0],viewerGrant[0]]))
  await denied(call(admin, admin, 'Self Promotion', 'ADMIN', 'INACTIVE', []))
  await db.exec(call(admin, secondAdmin, 'Second Admin', 'ADMIN', 'INACTIVE', []))
  await denied(call(admin, admin, 'Last Admin', 'ADMIN', 'INACTIVE', []))
  await db.exec(call(admin, missing, 'Managed Editor', 'EDITOR', 'INACTIVE', []))
  assert.equal((await db.query(`select count(*)::int as n from public.user_chain_access where user_id='${missing}'`)).rows[0].n, 0)
  assert.equal((await db.query(`select count(*)::int as n from public.audit_log where entity_id='${missing}'`)).rows[0].n, 3)
  await db.exec('reset role')
  await db.exec('set role anon')
  await denied(call(admin, viewer, 'Browser Actor', 'ADMIN', 'ACTIVE', []))
  await db.exec('reset role; set role authenticated')
  await denied(call(admin, viewer, 'Forged Admin', 'ADMIN', 'ACTIVE', []))
  await db.exec('reset role')
  assert.equal((await db.query('select count(*)::int as n from public.monthly_observations')).rows[0].n, 0)
  process.stdout.write('USER SQL PASS atomic profile/grants, safe deactivation, audit, last-admin, chain validation, browser denial\n')
} finally { await db.close() }
