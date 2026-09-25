import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { PGlite } from '@electric-sql/pglite'

// An isolated PostgreSQL engine: synthetic identities, real GRANT/RLS evaluation,
// and no credentials or data are sent to the Supabase project.
const db = new PGlite()
const sqlDir = join(process.cwd(), 'supabase', 'migrations')
const ids = {
  a: '00000000-0000-4000-8000-000000000001',
  b: '00000000-0000-4000-8000-000000000002',
  admin: '00000000-0000-4000-8000-000000000101',
  viewer: '00000000-0000-4000-8000-000000000102',
  editor: '00000000-0000-4000-8000-000000000103',
  approver: '00000000-0000-4000-8000-000000000104',
  inactive: '00000000-0000-4000-8000-000000000105',
  stranger: '00000000-0000-4000-8000-000000000106',
  catA: '00000000-0000-4000-8000-000000000201',
  catB: '00000000-0000-4000-8000-000000000202',
  profile: '00000000-0000-4000-8000-000000000301',
  version: '00000000-0000-4000-8000-000000000302',
  batch: '00000000-0000-4000-8000-000000000303',
  product: '00000000-0000-4000-8000-000000000304',
  run: '00000000-0000-4000-8000-000000000305',
  run2: '00000000-0000-4000-8000-000000000309',
  vintage: '00000000-0000-4000-8000-000000000306',
  decision: '00000000-0000-4000-8000-000000000308',
}
const hash = 'a'.repeat(64)
let passed = 0

const check = async (id, label, fn) => {
  await fn()
  passed++
  process.stdout.write(`${id} PASS ${label}\n`)
}
const denied = async (sql) => {
  try {
    await db.exec(sql)
  } catch (error) {
    assert.equal(error.code, '42501', `Expected permission/RLS denial, got ${error.code}: ${error.message}`)
    return
  }
  assert.fail(`Expected permission/RLS denial: ${sql}`)
}
const as = async (role, userId, fn) => {
  await db.exec(`reset role; select set_config('request.jwt.claim.sub', '${userId ?? ''}', false); set role ${role};`)
  try { return await fn() } finally { await db.exec('reset role; reset request.jwt.claim.sub;') }
}
const rows = async (sql) => (await db.query(sql)).rows
const path = (chain) => `${chain}/2026-09/${ids.batch}/sample.csv`

try {
  await db.exec(`
    create role anon;
    create role authenticated;
    create role service_role bypassrls;
    create schema auth;
    create table auth.users (id uuid primary key);
    create function auth.uid() returns uuid language sql stable as $$
      select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid
    $$;
    grant usage on schema auth to authenticated, anon;
    grant execute on function auth.uid() to authenticated, anon;
    create schema storage;
    create table storage.buckets (id text primary key, name text not null, public boolean not null);
    create table storage.objects (id uuid primary key default gen_random_uuid(), bucket_id text not null references storage.buckets(id), name text not null);
    alter table storage.objects enable row level security;
    grant usage on schema storage to authenticated, anon, service_role;
    grant select,insert,update,delete on storage.objects to authenticated, anon, service_role;
    grant select on storage.buckets to authenticated, anon, service_role;
  `)
  for (let i = 1; i <= 7; i++) {
    const file = `20260925000${i}_${['contract_core','contract_forecast','contract_governance','contract_integrity','auth_rls','private_storage','approval_probe_guard'][i - 1]}.sql`
    await db.exec(readFileSync(join(sqlDir, file), 'utf8'))
    process.stdout.write(`SQL PASS ${file}\n`)
  }
  await db.exec('grant usage on schema public, private to service_role; grant all on all tables in schema public to service_role;')

  await db.exec(`
    insert into auth.users(id) values ('${ids.admin}'),('${ids.viewer}'),('${ids.editor}'),('${ids.approver}'),('${ids.inactive}'),('${ids.stranger}');
    insert into public.chains(id,code,name) values ('${ids.a}','A','Chain A'),('${ids.b}','B','Chain B');
  `)
  await db.exec(`
    insert into public.profiles(id,status,global_role) values
      ('${ids.admin}','ACTIVE','ADMIN'),('${ids.viewer}','ACTIVE','VIEWER'),
      ('${ids.editor}','ACTIVE','EDITOR'),('${ids.approver}','ACTIVE','EDITOR'),
      ('${ids.inactive}','INACTIVE','EDITOR'),('${ids.stranger}','ACTIVE','VIEWER');
    insert into public.user_chain_access(user_id,chain_id,can_view,can_edit,can_import,can_run_forecast,can_approve) values
      ('${ids.viewer}','${ids.a}',true,false,false,false,false),
      ('${ids.editor}','${ids.a}',true,true,true,true,false),
      ('${ids.approver}','${ids.a}',true,false,false,false,true),
      ('${ids.inactive}','${ids.a}',true,true,true,true,true);
    insert into public.categories(id,chain_id,code,name) values
      ('${ids.catA}','${ids.a}','CAT-A','Category A'),('${ids.catB}','${ids.b}','CAT-B','Category B');
    insert into public.import_profiles(id,chain_id,name) values ('${ids.profile}','${ids.a}','fixture');
    insert into public.import_profile_versions(id,profile_id,chain_id,version,mapping_json,required_columns,created_by)
      values ('${ids.version}','${ids.profile}','${ids.a}',1,'{}','[]','${ids.admin}');
    insert into public.import_batches(id,chain_id,profile_version_id,filename,sha256,period,uploaded_by)
      values ('${ids.batch}','${ids.a}','${ids.version}','fixture.csv','${hash}','2026-09-01','${ids.admin}');
    insert into public.products(id,chain_id,product_code,description,first_seen_period,last_seen_period,created_from_batch_id)
      values ('${ids.product}','${ids.a}','SKU','Fixture','2026-09-01','2026-09-01','${ids.batch}');
    insert into public.forecast_runs(id,run_code,chain_id,objective,issue_period,cutoff_at,status,data_snapshot_hash,engine_version,git_sha)
      values ('${ids.run}','fixture-run','${ids.a}','Venta','2026-09-01',now(),'COMPLETED','${hash}','test','test');
    insert into public.forecast_runs(id,run_code,chain_id,objective,issue_period,cutoff_at,status,data_snapshot_hash,engine_version,git_sha)
      values ('${ids.run2}','service-run','${ids.a}','Venta','2026-09-01',now(),'COMPLETED','${hash}','test','test');
    insert into public.forecast_vintages(id,run_id,chain_id,objective,issue_period,cutoff_at,forecast_version,certification_status)
      values ('${ids.vintage}','${ids.run}','${ids.a}','Venta','2026-09-01',now(),'test','CERTIFIED');
    insert into public.forecast_horizons(vintage_id,chain_id,product_id,target_period,horizon,forecast_towell,model_strategy,forecast_status,certification_status)
      select '${ids.vintage}','${ids.a}','${ids.product}',('2026-09-01'::date + n * interval '1 month')::date,n,100,'test','CERTIFIED','CERTIFIED'
      from generate_series(1,12) n;
    insert into public.forecast_aggregates(vintage_id,chain_id,level,target_period,horizon,forecast_towell)
      select '${ids.vintage}','${ids.a}','CHAIN',('2026-09-01'::date + n * interval '1 month')::date,n,100
      from generate_series(1,12) n;
    update public.forecast_vintages set frozen_at=now() where id='${ids.vintage}';
    insert into public.forecast_decisions(id,vintage_id,chain_id,product_id,target_period,version_no,forecast_towell,adjusted_value,approved_value,reason_code,created_by)
      values ('${ids.decision}','${ids.vintage}','${ids.a}','${ids.product}','2026-10-01',1,100,110,110,'test','${ids.editor}');
  `)

  await check('CP01', 'anon cannot read operational tables', async () => as('anon', null, () => denied('select * from public.products')))
  await check('CP02', 'inactive user sees no chain rows', async () => as('authenticated', ids.inactive, async () => assert.equal((await rows('select * from public.categories')).length, 0)))
  await check('CP03', 'no chain grant means no rows', async () => as('authenticated', ids.stranger, async () => assert.equal((await rows('select * from public.categories')).length, 0)))
  await check('CP04', 'viewer sees only authorized chain', async () => as('authenticated', ids.viewer, async () => assert.deepEqual((await rows('select id from public.categories')).map((r) => r.id), [ids.catA])))
  await check('CP05', 'viewer cannot insert', async () => as('authenticated', ids.viewer, () => denied(`insert into public.categories(chain_id,code,name) values ('${ids.a}','X','X')`)))
  await check('CP06', 'viewer cannot update', async () => as('authenticated', ids.viewer, async () => assert.equal((await rows(`update public.categories set name='X' where id='${ids.catA}' returning id`)).length, 0)))
  await check('CP07', 'editor can edit own chain', async () => as('authenticated', ids.editor, async () => assert.equal((await rows(`update public.categories set name='Edited' where id='${ids.catA}' returning id`)).length, 1)))
  await check('CP08', 'editor cannot edit other chain', async () => as('authenticated', ids.editor, async () => { assert.equal((await rows(`update public.categories set name='Wrong' where id='${ids.catB}' returning id`)).length, 0); await denied(`insert into public.categories(chain_id,code,name) values ('${ids.b}','X','X')`) }))
  await check('CP09', 'can_import gates import profiles', async () => { await as('authenticated', ids.viewer, () => denied(`insert into public.import_profiles(chain_id,name) values ('${ids.a}','viewer')`)); await as('authenticated', ids.editor, async () => assert.equal((await rows(`insert into public.import_profiles(chain_id,name) values ('${ids.a}','editor') returning id`)).length, 1)) })
  await check('CP10', 'can_run_forecast gates queued jobs', async () => { await as('authenticated', ids.approver, () => denied(`insert into public.jobs(chain_id,job_type,status,requested_by) values ('${ids.a}','FORECAST_RUN','QUEUED','${ids.approver}')`)); await as('authenticated', ids.editor, async () => assert.equal((await rows(`insert into public.jobs(chain_id,job_type,status,requested_by) values ('${ids.a}','FORECAST_RUN','QUEUED','${ids.editor}') returning id`)).length, 1)) })
  await check('CP11', 'can_approve gates append-only decision approval', async () => {
    const approval = (actor) => `insert into public.forecast_decisions(vintage_id,chain_id,product_id,target_period,version_no,forecast_towell,adjusted_value,approved_value,reason_code,created_by,approved_by,approved_at) values ('${ids.vintage}','${ids.a}','${ids.product}','2026-10-01',2,100,110,110,'approved','${actor}','${actor}',now()) returning id`
    await as('authenticated', ids.editor, () => denied(approval(ids.editor)))
    await as('authenticated', ids.approver, async () => assert.equal((await rows(approval(ids.approver))).length, 1))
  })
  await check('CP12', 'active admin works across chains', async () => as('authenticated', ids.admin, async () => { assert.equal((await rows('select id from public.categories')).length, 2); assert.equal((await rows(`insert into public.categories(chain_id,code,name) values ('${ids.b}','ADMIN','Admin') returning id`)).length, 1) }))
  await check('CP13', 'cross-chain write injection is denied', async () => as('authenticated', ids.editor, () => denied(`insert into public.categories(chain_id,code,name) values ('${ids.b}','INJECT','Injection')`)))
  await check('CP14', 'Storage upload without permission denied', async () => as('authenticated', ids.viewer, () => denied(`insert into storage.objects(bucket_id,name) values ('source-files','${path(ids.a)}')`)))
  await check('CP15', 'authorized Storage upload allowed', async () => as('authenticated', ids.editor, async () => { assert.equal((await rows(`insert into storage.objects(bucket_id,name) values ('source-files','${path(ids.a)}') returning id`)).length, 1); await denied(`insert into storage.objects(bucket_id,name) values ('source-files','${path(ids.b)}')`) }))
  await check('CP16', 'all four buckets private', async () => assert.deepEqual((await rows('select id from storage.buckets where public=false order by id')).map((r) => r.id), ['exports','model-artifacts','research-evidence','source-files']))
  await check('CP17', 'self global_role update denied', async () => as('authenticated', ids.viewer, async () => assert.equal((await rows(`update public.profiles set global_role='ADMIN' where id='${ids.viewer}' returning id`)).length, 0)))
  await check('CP18', 'self chain access insert denied', async () => as('authenticated', ids.viewer, () => denied(`insert into public.user_chain_access(user_id,chain_id,can_view) values ('${ids.viewer}','${ids.b}',true)`)))
  await check('CP19', 'self can_approve escalation denied', async () => as('authenticated', ids.editor, async () => assert.equal((await rows(`update public.user_chain_access set can_approve=true where user_id='${ids.editor}' and chain_id='${ids.a}' returning user_id`)).length, 0)))
  await check('CP20', 'service writes engine output, user does not', async () => { await as('authenticated', ids.editor, () => denied(`insert into public.model_versions(run_id,chain_id,model_family,algorithm,model_version,certification_status) values ('${ids.run2}','${ids.a}','ML','X','1','CERTIFIED')`)); await as('service_role', null, async () => assert.equal((await rows(`insert into public.model_versions(run_id,chain_id,model_family,algorithm,model_version,certification_status) values ('${ids.run2}','${ids.a}','ML','X','1','CERTIFIED') returning id`)).length, 1)) })
  await check('CP21', 'approval helper does not reveal another chain', async () => {
    await as('authenticated', ids.viewer, async () => assert.equal((await rows(`select private.has_open_decision('${ids.vintage}','${ids.product}','2026-10-01',2,100) as found`))[0].found, false))
    await as('authenticated', ids.approver, async () => assert.equal((await rows(`select private.has_open_decision('${ids.vintage}','${ids.product}','2026-10-01',2,100) as found`))[0].found, true))
  })

  const rls = await rows("select relname, relrowsecurity from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relkind='r'")
  assert.equal(rls.length, 28)
  assert(rls.every((r) => r.relrowsecurity))
  process.stdout.write(`Security behavior: ${passed}/21 PASS; RLS: 28/28 PASS\n`)
} catch (error) {
  process.stderr.write(`Security test failure: ${error.code ?? 'ASSERT'} ${error.message}\n`)
  process.exitCode = 1
} finally {
  await db.close()
}
