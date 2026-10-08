import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { PGlite } from '@electric-sql/pglite'

const db = new PGlite()
const dir = join(process.cwd(), 'supabase', 'migrations')
const id = (n) => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`
const [chain, actor, product, observation, profile, version, batch] = [1, 2, 3, 4, 5, 6, 7].map(id)
const hash = 'a'.repeat(64)
const reject = async (sql) => {
  try { await db.exec(sql) } catch { return }
  assert.fail('Expected rejection')
}
try {
  await db.exec(`
    create role anon; create role authenticated; create role service_role bypassrls;
    create schema auth; create table auth.users(id uuid primary key);
    create function auth.uid() returns uuid language sql stable as $$
      select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
    grant usage on schema auth to authenticated, anon;
    grant execute on function auth.uid() to authenticated, anon;
    create schema storage;
    create table storage.buckets(id text primary key,name text not null,public boolean not null);
    create table storage.objects(id uuid primary key default gen_random_uuid(),
      bucket_id text not null references storage.buckets(id),name text not null);
    alter table storage.objects enable row level security;
    grant usage on schema storage to authenticated, anon, service_role;
    grant select,insert,update,delete on storage.objects to authenticated, anon, service_role;
    grant select on storage.buckets to authenticated, anon, service_role;
  `)
  for (const file of [
    '202609250001_contract_core.sql', '202609250002_contract_forecast.sql',
    '202609250003_contract_governance.sql', '202609250004_contract_integrity.sql',
    '202609250005_auth_rls.sql', '202609250006_private_storage.sql',
    '202609250007_approval_probe_guard.sql',
    '202609280001_portal_published_history.sql',
    '202609280002_portal_history_periods.sql',
    '202609290010_e3_vintage_transaction.sql',
    '202610080011_e1_policy_v2_guard.sql',
  ]) {
    await db.exec(readFileSync(join(dir, file), 'utf8'))
    process.stdout.write(`E3 SQL PASS ${file}\n`)
  }
  await db.exec(`
    insert into auth.users(id) values ('${actor}');
    insert into public.profiles(id,global_role,status) values ('${actor}','ADMIN','ACTIVE');
    insert into public.chains(id,code,name) values ('${chain}','SYN','Synthetic');
    insert into public.import_profiles(id,chain_id,name) values ('${profile}','${chain}','Synthetic');
    insert into public.import_profile_versions(id,profile_id,chain_id,version,mapping_json,required_columns,created_by)
      values ('${version}','${profile}','${chain}',1,'{}','[]','${actor}');
    insert into public.import_batches(id,chain_id,profile_version_id,filename,sha256,period,uploaded_by,status,row_count,valid_rows)
      values ('${batch}','${chain}','${version}','synthetic.csv','${hash}','2026-07-01','${actor}','UPLOADED',1,1);
    insert into public.products(id,chain_id,product_code,description,first_seen_period,last_seen_period,created_from_batch_id)
      values ('${product}','${chain}','SYN-1','Synthetic','2025-01-01','2026-07-01','${batch}');
    insert into public.monthly_observations(id,chain_id,product_id,period,metric_code,value,
      version_no,availability_source,source_batch_id)
      values ('${observation}','${chain}','${product}','2026-07-01','SALES',10,1,'UNKNOWN','${batch}');
  `)
  const horizons = Array.from({ length: 12 }, (_, index) => {
    const horizon = index + 1
    const date = new Date(Date.UTC(2026, 6 + horizon, 1)).toISOString().slice(0, 10)
    return { product_id: product, category_id: null, target_period: date, horizon,
      statistical_value: '10.000', ml_value: null, ensemble_value: null,
      forecast_towell: '10.000', model_strategy: 'statistical', confidence: 'Baja',
      p10: null, p50: null, p90: null, p95: null, band_basis: null,
      band_observations: null, forecast_status: 'ACTIVE' }
  })
  const payload = { actor_id: actor, chain_id: chain, objective: 'Venta', issue_period: '2026-07-01',
    cutoff_at: '2026-07-31T23:59:59Z', content_hash: hash, data_snapshot_hash: hash,
    certification_status: 'PROVISIONAL', engine_version: 'synthetic', git_sha: 'test',
    policy_version: 'E3-GATES-1.0.0', evidence_mode: 'RETROSPECTIVE_TRAINING', preview_id: 'synthetic',
    horizons, aggregates: horizons.map(({ target_period, horizon }) => ({ level: 'CHAIN',
      category_id: null, target_period, horizon, forecast_towell: '10.000' })),
    models: [{ family: 'STATISTICAL', algorithm: 'Naive', validation_wape: 10, parameters: {} }],
    inputs: [{ observation_id: observation, source_evidence_id: null }],
    metrics: [{ metric: 'WAPE', value: 10 }], gates: [
      { gate_type: 'DATA_QUALITY', status: 'DATA_QUALITY_WARNING', score: null, target: null, observed: null, details: {} },
      { gate_type: 'FORECAST_QUALITY', status: 'FORECAST_QUALITY_READY', score: null, target: null, observed: null, details: { no_degradation: true } },
      { gate_type: 'SERVICE_LEVEL', status: 'NOT_MEASURABLE', score: null, target: 95, observed: null, details: {} },
    ] }
  const encoded = JSON.stringify(payload).replaceAll("'", "''")
  await db.exec('set role service_role')
  const first = (await db.query(`select public.e3_create_vintage_candidate('${encoded}'::jsonb) as id`)).rows[0].id
  const second = (await db.query(`select public.e3_create_vintage_candidate('${encoded}'::jsonb) as id`)).rows[0].id
  assert.equal(first, second, 'idempotent candidate')
  const pitRun = id(30), pitVintage = id(31)
  await db.exec(`insert into public.forecast_runs(id,run_code,chain_id,objective,issue_period,cutoff_at,status,data_snapshot_hash,engine_version,git_sha)
    values('${pitRun}','PIT-test','${chain}','Venta','2026-07-01','2026-07-31T23:59:59Z','COMPLETED','${hash}','synthetic','test');
    insert into public.forecast_vintages(id,run_id,chain_id,objective,issue_period,cutoff_at,forecast_version,certification_status)
    values('${pitVintage}','${pitRun}','${chain}','Venta','2026-07-01','2026-07-31T23:59:59Z','PIT-test','PROVISIONAL');`)
  await reject(`insert into public.forecast_run_inputs(run_id,chain_id,monthly_observation_id,data_snapshot_hash,evidence_mode)
    values('${pitRun}','${chain}','${observation}','${hash}','POINT_IN_TIME')`)
  assert.equal((await db.query(`select count(*)::int as n from public.forecast_horizons where vintage_id='${first}'`)).rows[0].n, 12)
  assert.equal((await db.query(`select evidence_mode from public.forecast_run_inputs where run_id=(select run_id from public.forecast_vintages where id='${first}')`)).rows[0].evidence_mode, 'RETROSPECTIVE_TRAINING')
  await db.query(`select public.e3_freeze_vintage('${first}','${actor}')`)
  const v2Payload = { ...payload, content_hash: 'c'.repeat(64), policy_version: 'E3-GATES-2.0.0', preview_id: 'synthetic-v2' }
  const v2Encoded = JSON.stringify(v2Payload).replaceAll("'", "''")
  const v2 = (await db.query(`select public.e3_create_vintage_candidate('${v2Encoded}'::jsonb) as id`)).rows[0].id
  await db.query(`select public.e3_freeze_vintage('${v2}','${actor}')`)
  assert.equal((await db.query(`select frozen_at is not null as frozen from public.forecast_vintages where id='${v2}'`)).rows[0].frozen, true)
  await reject(`select public.e3_promote_champion('${v2}',(select id from public.model_versions where run_id=(select run_id from public.forecast_vintages where id='${v2}') limit 1),'${actor}','v2 remains blocked')`)
  await reject(`select public.e3_close_target_period('${first}','2026-08-01','${actor}')`)
  const actualBatch = id(40), actualObservation = id(41), orderObservation = id(42), deliveryObservation = id(43)
  await db.exec('reset role')
  await db.exec(`insert into public.import_batches(id,chain_id,profile_version_id,filename,sha256,period,uploaded_by,status,row_count,valid_rows)
    values('${actualBatch}','${chain}','${version}','actual.csv','${'b'.repeat(64)}','2026-08-01','${actor}','UPLOADED',3,3);
    update public.import_batches set status='VALIDATING' where id='${actualBatch}';
    update public.import_batches set status='VALIDATED' where id='${actualBatch}';
    update public.import_batches set status='CONFIRMED' where id='${actualBatch}';
    insert into public.monthly_observations(id,chain_id,product_id,period,metric_code,value,version_no,available_at,availability_source,source_batch_id)
    select '${actualObservation}','${chain}','${product}','2026-08-01','SALES',8,1,uploaded_at,'SYSTEM_INGESTION',id
      from public.import_batches where id='${actualBatch}';
    insert into public.monthly_observations(id,chain_id,product_id,period,metric_code,value,version_no,available_at,availability_source,source_batch_id)
    select '${orderObservation}','${chain}','${product}','2026-08-01','ORDER',20,1,uploaded_at,'SYSTEM_INGESTION',id
      from public.import_batches where id='${actualBatch}';
    insert into public.monthly_observations(id,chain_id,product_id,period,metric_code,value,version_no,available_at,availability_source,source_batch_id)
    select '${deliveryObservation}','${chain}','${product}','2026-08-01','DELIVERY',19,1,uploaded_at,'SYSTEM_INGESTION',id
      from public.import_batches where id='${actualBatch}';`)
  await db.exec('set role service_role')
  const close = (await db.query(`select public.e3_close_target_period('${first}','2026-08-01','${actor}') as result`)).rows[0].result
  assert.equal(close.status, 'CLOSED')
  assert.equal(Number(close.fill_rate), 95)
  assert.equal(Number(close.wape), 25)
  assert.equal((await db.query(`select public.e3_close_target_period('${first}','2026-08-01','${actor}') as result`)).rows[0].result.status, 'ALREADY_CLOSED')
  assert.equal((await db.query(`select count(*)::int as n from public.actual_evaluations where vintage_id='${first}'`)).rows[0].n, 1)
  assert.equal((await db.query(`select count(*)::int as n from public.performance_metrics where vintage_id='${first}' and phase='LIVE'`)).rows[0].n, 5)
  await reject(`update public.forecast_horizons set forecast_towell=11 where vintage_id='${first}'`)
  await reject(`select public.e3_publish_official('${first}','${actor}','No retrospective publication')`)
  await reject(`select public.e3_promote_champion('${first}',(select id from public.model_versions limit 1),'${actor}','No PIT certification')`)
  assert.equal((await db.query(`select count(*)::int as n from public.audit_log where action='PUBLISH_OFFICIAL'`)).rows[0].n, 0)
  await db.exec('reset role')
  await db.exec('set role authenticated')
  await reject(`select public.e3_create_vintage_candidate('${encoded}'::jsonb)`)
  await reject(`select public.e3_close_target_period('${first}','2026-08-01','${actor}')`)
  await db.exec('reset role')
  process.stdout.write('E3 SQL PASS atomic candidate, idempotency, UNKNOWN blocked for PIT, retrospective lineage, freeze, LIVE close/idempotency, immutable, publication/Champion denial, browser denial\n')
} finally { await db.close() }
