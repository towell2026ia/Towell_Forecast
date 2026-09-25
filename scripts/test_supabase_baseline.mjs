import { PGlite } from '@electric-sql/pglite'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

const db = new PGlite()
const migrationsDir = join(process.cwd(), 'supabase', 'migrations')
// Preserve the 09.2B baseline test independently of later auth/storage migrations.
const files = readdirSync(migrationsDir).filter((name) => /^20260925000[1-4]_.*\.sql$/.test(name)).sort()
const expectReject = async (sql, label) => {
  try {
    await db.exec(sql)
  } catch (error) {
    if (!['23503', '23505', '23514'].includes(error.code)) {
      throw new Error(`Unexpected SQL error for ${label}: ${error.code ?? error.message}`)
    }
    return
  }
  throw new Error(`Expected constraint to reject: ${label}`)
}

try {
  await db.exec('create schema auth; create table auth.users(id uuid primary key);')
  for (const file of files) {
    await db.exec(readFileSync(join(migrationsDir, file), 'utf8'))
    process.stdout.write(`SQL PASS ${file}\n`)
  }

  const expected = [
    'chains', 'categories', 'products', 'profiles', 'user_chain_access',
    'import_profiles', 'import_profile_versions', 'import_batches', 'source_evidence',
    'monthly_observations', 'customer_forecast_versions', 'customer_forecast_rows',
    'forecast_runs', 'model_versions', 'research_snapshots', 'research_snapshot_sources', 'forecast_vintages',
    'forecast_horizons', 'forecast_aggregates', 'champion_registry',
    'actual_evaluations', 'performance_metrics', 'forecast_decisions',
    'jobs', 'run_logs', 'audit_log', 'forecast_run_inputs', 'legacy_identity_map',
  ]
  const { rows } = await db.query("select tablename from pg_tables where schemaname='public'")
  const found = new Set(rows.map((row) => row.tablename))
  for (const table of expected) if (!found.has(table)) throw new Error(`Missing table ${table}`)
  const rls = await db.query("select relname,relrowsecurity from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relkind='r'")
  for (const row of rls.rows) if (!row.relrowsecurity) throw new Error(`RLS not enabled on ${row.relname}`)

  const chainA = '00000000-0000-4000-8000-000000000001'
  const chainB = '00000000-0000-4000-8000-000000000002'
  const user = '00000000-0000-4000-8000-000000000003'
  const profile = '00000000-0000-4000-8000-000000000004'
  const version = '00000000-0000-4000-8000-000000000005'
  const batch = '00000000-0000-4000-8000-000000000006'
  const product = '00000000-0000-4000-8000-000000000007'
  const run = '00000000-0000-4000-8000-000000000008'
  const model = '00000000-0000-4000-8000-000000000009'
  const vintage = '00000000-0000-4000-8000-00000000000a'
  const evidence = '00000000-0000-4000-8000-00000000000b'
  const research = '00000000-0000-4000-8000-00000000000c'
  const hash = 'a'.repeat(64)
  await db.exec(`
    insert into auth.users(id) values ('${user}');
    insert into public.chains(id,code,name) values ('${chainA}','A','Chain A'),('${chainB}','B','Chain B');
    insert into public.import_profiles(id,chain_id,name) values ('${profile}','${chainA}','standard');
    insert into public.import_profile_versions(id,profile_id,chain_id,version,mapping_json,required_columns,created_by)
      values ('${version}','${profile}','${chainA}',1,'{}','[]','${user}');
    insert into public.import_batches(id,chain_id,profile_version_id,filename,sha256,period,uploaded_by,status,row_count,valid_rows)
      values ('${batch}','${chainA}','${version}','sample.csv','${hash}','2026-01-01','${user}','UPLOADED',1,1);
    update public.import_batches set status='VALIDATING' where id='${batch}';
    update public.import_batches set status='VALIDATED' where id='${batch}';
    update public.import_batches set status='CONFIRMED' where id='${batch}';
    insert into public.products(id,chain_id,product_code,description,first_seen_period,last_seen_period,created_from_batch_id)
      values ('${product}','${chainA}','SKU-1','First description','2026-01-01','2026-01-01','${batch}');
  `)
  await expectReject(`insert into public.products(chain_id,product_code,description,first_seen_period,last_seen_period,created_from_batch_id)
    values ('${chainA}','SKU-1','Another description','2026-01-01','2026-01-01','${batch}')`, 'product identity ignores description')
  await expectReject(`insert into public.products(chain_id,product_code,description,first_seen_period,last_seen_period,created_from_batch_id)
    values ('${chainB}','SKU-2','Cross-chain batch','2026-01-01','2026-01-01','${batch}')`, 'cross-chain batch')
  await expectReject(`insert into public.monthly_observations(chain_id,product_id,period,metric_code,value,version_no,available_at,availability_source)
    values ('${chainA}','${product}','2026-01-01','SALES',1,1,now(),'UNKNOWN')`, 'unknown history blocked')
  await expectReject(`insert into public.monthly_observations(chain_id,product_id,period,metric_code,value,version_no,available_at,availability_source,source_batch_id)
    values ('${chainA}','${product}','2026-01-01','SALES',1,1,now() + interval '1 day','SYSTEM_INGESTION','${batch}')`, 'system availability equals upload')
  await db.exec(`insert into public.monthly_observations(chain_id,product_id,period,metric_code,value,version_no,availability_source)
    values ('${chainA}','${product}','2026-01-01','SALES',1,1,'UNKNOWN')`)
  await db.exec(`insert into public.monthly_observations(chain_id,product_id,period,metric_code,value,version_no,available_at,availability_source,source_batch_id)
    select '${chainA}','${product}','2026-01-01','SALES',2,2,uploaded_at,'SYSTEM_INGESTION',id from public.import_batches where id='${batch}'`)
  await expectReject(`update public.monthly_observations set value=2 where product_id='${product}'`, 'observations are append-only')
  await db.exec(`insert into public.source_evidence(id,chain_id,evidence_level,evidence_type,evidence_date,source_name,source_hash,storage_path,validated_by,validated_at)
    values ('${evidence}','${chainA}','E1','MANIFEST','2025-12-15','signed-manifest','${hash}','private/manifest.json','${user}','2025-12-16')`)
  await expectReject(`insert into public.monthly_observations(chain_id,product_id,period,metric_code,value,version_no,available_at,availability_source,availability_evidence_id)
    values ('${chainA}','${product}','2026-01-01','SALES',1,3,'2025-12-14','E1','${evidence}')`, 'evidence-backed date cannot be fabricated')
  await db.exec(`insert into public.monthly_observations(chain_id,product_id,period,metric_code,value,version_no,available_at,availability_source,availability_evidence_id)
    values ('${chainA}','${product}','2026-01-01','SALES',1,3,'2025-12-15','E1','${evidence}')`)
  await db.exec(`insert into public.research_snapshots(id,chain_id,cutoff_at,provider,status,content_hash)
    values ('${research}','${chainA}','2026-08-01','local','DRAFT','${hash}')`)
  await expectReject(`insert into public.research_snapshot_sources(snapshot_id,chain_id,source_url,source_hash,published_at,captured_at)
    values ('${research}','${chainA}','https://example.com/postcutoff','${hash}','2026-08-02','2026-08-02')`, 'research postdates cutoff')

  await db.exec(`
    insert into public.forecast_runs(id,run_code,chain_id,objective,issue_period,cutoff_at,status,data_snapshot_hash,engine_version,git_sha)
      values ('${run}','test-run','${chainA}','Venta','2026-09-01',now()+interval '1 hour','COMPLETED','${hash}','test','test-sha');
    insert into public.model_versions(id,run_id,chain_id,model_family,algorithm,model_version,certification_status)
      values ('${model}','${run}','${chainA}','STATISTICAL','NAIVE','v1','CERTIFIED');
    insert into public.forecast_vintages(id,run_id,chain_id,objective,issue_period,cutoff_at,forecast_version,champion_model_version_id,certification_status)
      select '${vintage}',id,chain_id,objective,issue_period,cutoff_at,'v1','${model}','CERTIFIED' from public.forecast_runs where id='${run}';
  `)
  await expectReject(`insert into public.forecast_horizons(vintage_id,chain_id,product_id,target_period,horizon,forecast_towell,model_strategy,forecast_status,certification_status)
    values ('${vintage}','${chainA}','${product}','2026-11-01',1,10,'NAIVE','READY','CERTIFIED')`, 'target month must equal horizon')
  await expectReject(`insert into public.forecast_horizons(vintage_id,chain_id,product_id,target_period,horizon,forecast_towell,model_strategy,forecast_status,certification_status,p10,p50,p90,p95,band_basis,band_observations)
    values ('${vintage}','${chainA}','${product}','2026-10-01',1,10,'NAIVE','READY','CERTIFIED',10,8,12,15,'residual',10)`, 'P10 must not exceed P50')
  await db.exec(`insert into public.forecast_horizons(vintage_id,chain_id,product_id,target_period,horizon,forecast_towell,model_strategy,forecast_status,certification_status)
    select '${vintage}','${chainA}','${product}',(date '2026-09-01'+make_interval(months=>h))::date,h,10,'NAIVE','READY','CERTIFIED' from generate_series(1,12) h`)
  await expectReject(`insert into public.forecast_run_inputs(run_id,chain_id,monthly_observation_id,data_snapshot_hash)
    select '${run}','${chainA}',id,'${hash}' from public.monthly_observations where product_id='${product}' and version_no=1`, 'unknown history excluded from run')
  await db.exec(`insert into public.forecast_run_inputs(run_id,chain_id,monthly_observation_id,data_snapshot_hash)
    select '${run}','${chainA}',id,'${hash}' from public.monthly_observations where product_id='${product}' and version_no=2`)
  await expectReject(`update public.forecast_vintages set frozen_at=now() where id='${vintage}'`, 'freeze requires reconciled aggregates')
  await db.exec(`insert into public.forecast_aggregates(vintage_id,chain_id,level,target_period,horizon,forecast_towell)
    select '${vintage}','${chainA}','CHAIN',(date '2026-09-01'+make_interval(months=>h))::date,h,10 from generate_series(1,12) h`)
  await db.exec(`update public.forecast_vintages set frozen_at=now() where id='${vintage}'`)
  await expectReject(`update public.forecast_horizons set forecast_towell=11 where vintage_id='${vintage}'`, 'frozen forecast is immutable')
  await expectReject(`insert into public.forecast_run_inputs(run_id,chain_id,monthly_observation_id,data_snapshot_hash)
    select '${run}','${chainA}',id,'${hash}' from public.monthly_observations where product_id='${product}' and version_no=3`, 'frozen run cannot gain input')
  await expectReject(`insert into public.model_versions(run_id,chain_id,model_family,algorithm,model_version,certification_status)
    values ('${run}','${chainA}','ML','TREE','v2','CERTIFIED')`, 'frozen run cannot gain model')
  await expectReject(`insert into public.forecast_decisions(vintage_id,chain_id,product_id,target_period,version_no,forecast_towell,reason_code,created_by)
    values ('${vintage}','${chainA}','${product}','2026-10-01',1,12,'MANUAL','${user}')`, 'decision cannot change baseline')
  await db.exec(`insert into public.forecast_decisions(vintage_id,chain_id,product_id,target_period,version_no,forecast_towell,adjusted_value,reason_code,created_by)
    values ('${vintage}','${chainA}','${product}','2026-10-01',1,10,12,'MANUAL','${user}')`)
  await expectReject(`insert into public.champion_registry(chain_id,objective,scope_type,scope_id,model_version_id,strategy,published_at,published_by,valid_from,status)
    values ('${chainA}','Venta','CHAIN','${chainB}','${model}','NAIVE',now(),'${user}',now(),'ACTIVE')`, 'champion scope chain mismatch')
  await db.exec(`insert into public.champion_registry(chain_id,objective,scope_type,scope_id,model_version_id,strategy,published_at,published_by,valid_from,status)
    values ('${chainA}','Venta','CHAIN','${chainA}','${model}','NAIVE',now(),'${user}',now(),'ACTIVE')`)
  await expectReject(`insert into public.champion_registry(chain_id,objective,scope_type,scope_id,model_version_id,strategy,published_at,published_by,valid_from,status)
    values ('${chainA}','Venta','CHAIN','${chainA}','${model}','NAIVE',now(),'${user}',now(),'ACTIVE')`, 'one active champion per scope')
  process.stdout.write(`CONSTRAINT PASS ${expected.length} tables, RLS, identity, cross-chain, availability, evidence, research cutoff, H1-H12, bands, freeze, decisions, Champion and run lineage\n`)
} finally {
  await db.close()
}
