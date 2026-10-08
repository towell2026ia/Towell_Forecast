import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { PGlite } from '@electric-sql/pglite'

const db = new PGlite()
const dir = join(process.cwd(), 'supabase', 'migrations')
const files = readdirSync(dir).filter(name => /^20.*\.sql$/.test(name)).sort()
let passed = 0
const run = async (name, fn) => { await fn(); passed++; process.stdout.write(`E4 ${name} PASS\n`) }
const reject = async (sql, expected) => {
  try { await db.exec(sql) } catch (error) {
    assert.equal(error.code, expected, `${error.code}: ${error.message}`)
    return
  }
  assert.fail('Expected SQL rejection')
}
const one = async sql => (await db.query(sql)).rows[0]
const id = {
  chain: '00000000-0000-4000-8000-000000000001',
  other: '00000000-0000-4000-8000-000000000002',
  admin: '00000000-0000-4000-8000-000000000003',
  viewer: '00000000-0000-4000-8000-000000000004',
  product: '00000000-0000-4000-8000-000000000005',
  batch: '00000000-0000-4000-8000-000000000006',
  profile: '00000000-0000-4000-8000-000000000007',
  version: '00000000-0000-4000-8000-000000000008',
}
const q = value => `'${JSON.stringify(value).replaceAll("'", "''")}'::jsonb`
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
  for (const file of files) {
    await db.exec(readFileSync(join(dir, file), 'utf8'))
    process.stdout.write(`SQL PASS ${file}\n`)
  }
  await run('tables and RLS', async () => {
    const rows = (await db.query("select relname,relrowsecurity from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and relkind='r' and relname like 'forecast_%' order by relname")).rows
    for (const table of ['forecast_calculations','forecast_calculation_horizons','forecast_selection_events',
      'forecast_capture_sessions','forecast_live_evaluations','forecast_learning_events','forecast_live_closures']) {
      assert.equal(rows.find(row => row.relname === table)?.relrowsecurity, true, table)
    }
  })
  await db.exec(`
    grant usage on schema public,private to service_role;
    grant all on all tables in schema public to service_role;
    insert into auth.users(id) values ('${id.admin}'),('${id.viewer}');
    insert into public.chains(id,code,name) values ('${id.chain}','A','A'),('${id.other}','B','B');
    insert into public.profiles(id,status,global_role) values ('${id.admin}','ACTIVE','ADMIN'),('${id.viewer}','ACTIVE','VIEWER');
    insert into public.import_profiles(id,chain_id,name) values ('${id.profile}','${id.chain}','test');
    insert into public.import_profile_versions(id,profile_id,chain_id,version,mapping_json,required_columns,created_by)
      values ('${id.version}','${id.profile}','${id.chain}',1,'{}','[]','${id.admin}');
    insert into public.import_batches(id,chain_id,profile_version_id,filename,sha256,period,uploaded_by)
      values ('${id.batch}','${id.chain}','${id.version}','fixture.csv','${'a'.repeat(64)}','2026-01-01','${id.admin}');
    insert into public.products(id,chain_id,product_code,description,first_seen_period,last_seen_period,created_from_batch_id)
      values ('${id.product}','${id.chain}','SKU','Fixture','2026-01-01','2026-01-01','${id.batch}');
  `)
  const payload = (actor, key, issue='2026-07-01') => ({
    actor_id: actor, chain_id: id.chain, product_id: id.product, objective:'Venta',
    issue_period: issue, cutoff_at:'2026-07-31T23:59:59Z',preview_id:'preview-fixture',
    data_snapshot_hash:'a'.repeat(64),content_hash:'b'.repeat(64),
    input_observation_ids:[],product_snapshot:{description:'Fixture'},candidates:{},
    suggested_reference:{family:'STATISTICAL'},evidence_mode:'RETROSPECTIVE_TRAINING',
    engine_version:'test',git_sha:'test',idempotency_key:key,
    horizons:Array.from({length:12},(_,i)=>({horizon:i+1,
      target_period:new Date(Date.UTC(2026,7+i,1)).toISOString().slice(0,10),
      statistical_value:100+i,ml_value:90+i,ensemble_value:95+i,
      ensemble_ml_component:90+i,ensemble_ml_model:'Scope Forest',statistical_weight:.5,ml_weight:.5,
      p10:null,p50:95+i,p90:null,p95:null,band_basis:'INSUFFICIENT',band_observations:0,
      band_status:'INSUFFICIENT_BAND_EVIDENCE'}))
  })
  const first = payload(id.admin,'calc-1')
  await run('viewer cannot create calculation', async () => {
    await reject(`select public.e4_create_calculation(${q(payload(id.viewer,'viewer'))})`, '23514')
  })
  await run('Ensemble arithmetic and component lineage are enforced', async () => {
    const invalid = payload(id.admin,'bad-ensemble')
    invalid.horizons[0].ensemble_ml_component = 9000
    await reject(`select public.e4_create_calculation(${q(invalid)})`, '23514')
  })
  let calc1
  await run('C1 creates twelve immutable horizons', async () => {
    calc1 = (await one(`select public.e4_create_calculation(${q(first)}) as id`)).id
    assert.equal((await one(`select count(*)::int as n from public.forecast_calculation_horizons where calculation_id='${calc1}'`)).n,12)
    const readback = (await db.query(`select horizon,p50,p10,p90,p95 from public.forecast_calculation_horizons where calculation_id='${calc1}' order by horizon`)).rows
    assert.deepEqual(readback.map(row => Number(row.p50)),first.horizons.map(row => row.p50))
    assert.ok(readback.every(row => row.p10 === null && row.p90 === null && row.p95 === null))
    assert.equal((await one(`select public.e4_create_calculation(${q(first)}) as id`)).id,calc1)
    await reject(`update public.forecast_calculation_horizons set statistical_value=1 where calculation_id='${calc1}'`, '23514')
  })
  for (const basis of ['PRODUCT','CATEGORY','CHAIN']) {
    await run(`${basis} full bands preserve central P50`, async () => {
      const available = {...payload(id.admin,`band-${basis}`), recalculation_reason:'Nueva evidencia externa'}
      available.horizons = available.horizons.map(row => ({...row,band_basis:basis,band_status:'AVAILABLE',
        band_observations:3,p10:row.p50-10,p90:row.p50+10,p95:row.p50+20}))
      await db.exec('begin')
      try {
        const calc = (await one(`select public.e4_create_calculation(${q(available)}) as id`)).id
        const readback = (await db.query(`select p10,p50,p90,p95,band_basis from public.forecast_calculation_horizons where calculation_id='${calc}' order by horizon`)).rows
        assert.equal(readback.length,12)
        assert.deepEqual(readback.map(row => [Number(row.p10),Number(row.p50),Number(row.p90),Number(row.p95),row.band_basis]),
          available.horizons.map(row => [row.p10,row.p50,row.p90,row.p95,basis]))
      } finally { await db.exec('rollback') }
    })
  }
  await run('INSUFFICIENT rejects missing or negative P50', async () => {
    for (const p50 of [null,-1]) {
      const invalid = {...payload(id.admin,`bad-p50-${p50}`),recalculation_reason:'Nueva evidencia externa'}
      invalid.horizons[0].p50 = p50
      await reject(`select public.e4_create_calculation(${q(invalid)})`, '23514')
    }
  })
  await run('INSUFFICIENT rejects fabricated P10 P90 P95', async () => {
    for (const quantile of ['p10','p90','p95']) {
      const invalid = {...payload(id.admin,`bad-${quantile}`),recalculation_reason:'Nueva evidencia externa'}
      invalid.horizons[0][quantile] = 96
      await reject(`select public.e4_create_calculation(${q(invalid)})`, '23514')
    }
  })
  await run('AVAILABLE still requires sufficient complete ordered evidence', async () => {
    for (const overrides of [{p90:null},{band_observations:2},{p10:110},{p95:90}]) {
      const invalid = {...payload(id.admin,`bad-available-${JSON.stringify(overrides)}`),recalculation_reason:'Nueva evidencia externa'}
      invalid.horizons[0] = {...invalid.horizons[0],band_basis:'PRODUCT',band_status:'AVAILABLE',
        band_observations:3,p10:80,p90:100,p95:105,...overrides}
      await reject(`select public.e4_create_calculation(${q(invalid)})`, '23514')
    }
  })
  let calc2
  await run('C2 requires a reason and does not replace C1', async () => {
    await reject(`select public.e4_create_calculation(${q(payload(id.admin,'calc-2'))})`, '23514')
    const second = {...payload(id.admin,'calc-2'), recalculation_reason:'Nuevo mes disponible'}
    calc2 = (await one(`select public.e4_create_calculation(${q(second)}) as id`)).id
    assert.notEqual(calc2,calc1)
    assert.equal((await one(`select calculation_code from public.forecast_calculations where id='${calc2}'`)).calculation_code,'C2')
    assert.equal((await one(`select status from public.forecast_calculations where id='${calc1}'`)).status,'READY_FOR_DECISION')
  })
  await run('selection has complete curve and cannot mutate history', async () => {
    const selection = {actor_id:id.admin,calculation_id:calc1,selected_candidate:'STATISTICAL',idempotency_key:'sel-1'}
    const selected = (await one(`select public.e4_select_forecast(${q(selection)}) as id`)).id
    assert.equal((await one(`select jsonb_array_length(selected_curve) as n from public.forecast_selection_events where id='${selected}'`)).n,12)
    assert.equal((await one(`select public.e4_select_forecast(${q(selection)}) as id`)).id,selected)
    await reject(`update public.forecast_selection_events set selected_candidate='ML' where id='${selected}'`, '23514')
  })
  await run('browser roles cannot call write RPC', async () => {
    await db.exec('set role authenticated')
    try { await reject(`select public.e4_create_calculation(${q(first)})`, '42501') }
    finally { await db.exec('reset role') }
  })
  let captured
  await run('capture draft, confirm, and exact availability timestamp', async () => {
    const draft = {actor_id:id.admin,chain_id:id.chain,product_id:id.product,period:'2026-08-01',
      order_value:120,sale_value:100,delivery_value:90,idempotency_key:'capture-1'}
    captured = (await one(`select public.e4_save_capture(${q(draft)}) as id`)).id
    assert.equal((await one(`select public.e4_save_capture(${q(draft)}) as id`)).id,captured)
    const batch = (await one(`select public.e4_confirm_capture('${captured}','${id.admin}','confirm-1') as id`)).id
    assert.equal((await one(`select public.e4_confirm_capture('${captured}','${id.admin}','confirm-1') as id`)).id,batch)
    const observation = await one(`select o.value,o.available_at,b.uploaded_at,o.version_no from public.monthly_observations o
      join public.import_batches b on b.id=o.source_batch_id where o.product_id='${id.product}' and o.metric_code='SALES'`)
    assert.equal(Number(observation.value),100)
    assert.equal(observation.available_at.toISOString(),observation.uploaded_at.toISOString())
    assert.equal(observation.version_no,1)
    await reject(`update public.monthly_observations set value=999 where product_id='${id.product}'`, '23514')
  })
  await run('correction appends V2 and preserves V1', async () => {
    const draft = {actor_id:id.admin,chain_id:id.chain,product_id:id.product,period:'2026-08-01',
      order_value:130,sale_value:105,delivery_value:95,correction_reason:'Actualización validada',idempotency_key:'capture-2'}
    const second = (await one(`select public.e4_save_capture(${q(draft)}) as id`)).id
    await db.exec(`select public.e4_confirm_capture('${second}','${id.admin}','confirm-2')`)
    const versions = (await db.query(`select version_no,value from public.monthly_observations where product_id='${id.product}' and metric_code='SALES' order by version_no`)).rows
    assert.deepEqual(versions.map(row => Number(row.value)),[100,105])
  })
  await run('LIVE evaluates all mature calculations against one real version', async () => {
    const result = (await one(`select public.e4_close_live('${id.chain}','${id.product}','2026-08-01','${id.admin}','close-1') as result`)).result
    assert.equal(result.status,'CLOSED')
    assert.equal(result.evaluations,7) // C1 selected: 4; undecided C2: 3.
    assert.equal((await one(`select public.e4_close_live('${id.chain}','${id.product}','2026-08-01','${id.admin}','close-1') as result`)).result.evaluations,7)
    const evaluations = (await db.query(`select calculation_id,candidate,actual_sale,horizon from public.forecast_live_evaluations order by calculation_id,candidate`)).rows
    assert.equal(evaluations.length,7)
    assert.ok(evaluations.every(row => Number(row.actual_sale)===105 && row.horizon===1))
    assert.equal((await one('select count(*)::int as n from public.forecast_learning_events')).n,2)
    const context = await one(`select order_value,delivery_value from public.forecast_live_evaluations
      where calculation_id='${calc1}' and candidate='STATISTICAL'`)
    assert.equal(Number(context.order_value),130)
    assert.equal(Number(context.delivery_value),95)
    const learned = await one(`select metrics_json,signals_json from public.forecast_learning_events
      where calculation_id='${calc1}'`)
    assert.ok(Math.abs(Number(learned.metrics_json.fill_rate)-95/130*100)<1e-9)
    assert.ok(learned.signals_json.includes('SUPPLY_SHORTFALL'))
  })
  await run('post-actual decision is excluded from LIVE even after actual correction', async () => {
    const decision = {actor_id:id.admin,calculation_id:calc2,selected_candidate:'ML',
      decision_reason:'Cambio de precio',idempotency_key:'sel-after-real'}
    await db.exec(`select public.e4_select_forecast(${q(decision)})`)
    const third = {actor_id:id.admin,chain_id:id.chain,product_id:id.product,period:'2026-08-01',
      order_value:140,sale_value:110,delivery_value:100,correction_reason:'Corrección V3',idempotency_key:'capture-3'}
    const draft = (await one(`select public.e4_save_capture(${q(third)}) as id`)).id
    await db.exec(`select public.e4_confirm_capture('${draft}','${id.admin}','confirm-3')`)
    await db.exec(`select public.e4_close_live('${id.chain}','${id.product}','2026-08-01','${id.admin}','close-2')`)
    const late = await one(`select count(*)::int as n from public.forecast_live_evaluations
      where calculation_id='${calc2}' and candidate='TOWELL_SELECTED'`)
    assert.equal(late.n,0)
    assert.equal((await one(`select count(*)::int as n from public.forecast_live_evaluations
      where candidate='STATISTICAL' and calculation_id='${calc1}'`)).n,2)
  })
  await run('all required operational audit event types are recorded', async () => {
    const actions = new Set((await db.query(`select action from public.audit_log where chain_id='${id.chain}'`)).rows.map(row => row.action))
    for (const action of ['CREATE_CALCULATION','COMPLETE_CALCULATION','RECALCULATE','SELECT_FORECAST',
      'CHANGE_SELECTION','CAPTURE_OBSERVATION','CORRECT_OBSERVATION','CONFIRM_MONTH','LIVE_EVALUATION','LEARNING_EVENT'])
      assert.ok(actions.has(action), action)
  })
  process.stdout.write(`E4 schema checks: ${passed} PASS\n`)
} finally { await db.close() }
