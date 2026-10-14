import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { PGlite } from '@electric-sql/pglite'

// Synthetic fixtures in isolated PostgreSQL only; never a production writer.
const db = new PGlite()
const id = n => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`
const q = value => `'${String(value).replaceAll("'", "''")}'`
let passed = 0
const check = async (label, fn) => { await fn(); passed++; process.stdout.write(`Commercial ${label} PASS\n`) }
const one = async sql => (await db.query(sql)).rows[0]
const reject = async (sql, code) => {
  try { await db.exec(sql) } catch (error) { assert.equal(error.code, code, error.message); return }
  assert.fail('Expected rejection')
}
const as = async (role, uid, fn) => {
  await db.exec(`reset role; select set_config('request.jwt.claim.sub', ${q(uid ?? '')}, false); set role ${role}`)
  try { await fn() } finally { await db.exec('reset role; reset request.jwt.claim.sub') }
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
  const dir = join(process.cwd(), 'supabase/migrations')
  for (const file of readdirSync(dir).filter(f => /^20.*\.sql$/.test(f)).sort()) await db.exec(readFileSync(join(dir, file), 'utf8'))
  await db.exec(`
    insert into auth.users values ('${id(10)}'),('${id(11)}'),('${id(12)}'),('${id(13)}');
    insert into public.profiles(id,global_role,status) values
      ('${id(10)}','ADMIN','ACTIVE'),('${id(11)}','VIEWER','ACTIVE'),
      ('${id(12)}','ADMIN','INACTIVE'),('${id(13)}','EDITOR','ACTIVE');
    insert into public.chains(id,code,name) values ('${id(1)}','A','A'),('${id(2)}','B','B');
    insert into public.categories(id,chain_id,code,name) values ('${id(21)}','${id(1)}','C','Toalla'),('${id(22)}','${id(2)}','C','Toalla');
    insert into public.user_chain_access(user_id,chain_id,can_view,can_edit,can_approve)
      values ('${id(11)}','${id(1)}',true,false,false),('${id(13)}','${id(1)}',true,true,true);
  `)
  for (const chain of [1, 2]) {
    await db.exec(`
      insert into public.import_profiles(id,chain_id,name) values ('${id(30 + chain)}','${id(chain)}','Fixture');
      insert into public.import_profile_versions(id,profile_id,chain_id,version,mapping_json,required_columns,created_by)
        values ('${id(40 + chain)}','${id(30 + chain)}','${id(chain)}',1,'{}','[]','${id(10)}');
      insert into public.import_batches(id,chain_id,profile_version_id,filename,sha256,period,uploaded_by)
        values ('${id(50 + chain)}','${id(chain)}','${id(40 + chain)}','fixture.csv','${'a'.repeat(64)}','2026-01-01','${id(10)}');
      insert into public.products(id,chain_id,category_id,product_code,description,first_seen_period,last_seen_period,created_from_batch_id)
        values ('${id(60 + chain)}','${id(chain)}','${id(20 + chain)}','0001','Toalla MB Nuevo Gris','2026-01-01','2026-01-01','${id(50 + chain)}');
    `)
  }
  const original = await one(`select
    (select jsonb_agg(to_jsonb(p) order by id) from public.products p) products,
    (select jsonb_agg(to_jsonb(b) order by id) from public.import_batches b) imports,
    (select jsonb_agg(to_jsonb(v) order by id) from public.import_profile_versions v) profiles,
    (select count(*) from public.monthly_observations) observations,
    (select count(*) from public.forecast_calculations) calculations,
    (select count(*) from public.forecast_selection_events) selections`)
  await check('schema adds no rows', async () => {
    assert.equal((await one('select count(*)::int n from public.product_commercial_classifications')).n, 0)
    const rows = (await db.query("select relname,relrowsecurity from pg_class where relname in ('commercial_models','commercial_colors','commercial_sizes','product_commercial_classifications')")).rows
    assert.equal(rows.length, 4)
    assert.ok(rows.every(r => r.relrowsecurity))
  })
  const tables = ['commercial_models', 'commercial_colors', 'commercial_sizes', 'product_commercial_classifications']
  for (const role of ['anon', 'authenticated', 'service_role']) {
    await check(`${role} cannot write even with admin identity`, async () => {
      for (const table of tables) for (const verb of ['INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) {
        assert.equal((await one(`select has_table_privilege('${role}','public.${table}','${verb}') allowed`)).allowed, false)
      }
      await as(role, id(10), async () => {
        await reject('delete from public.product_commercial_classifications', '42501')
        await reject("insert into public.commercial_models(name,evidence_json,created_by) values ('Forged','{}',null)", '42501')
      })
    })
  }
  await db.exec(`
    insert into public.commercial_models(id,name,evidence_json,created_by) values ('${id(70)}','Nuevo','{"source":"isolated fixture"}','${id(10)}'),('${id(71)}','Private B','{"source":"private scope B"}','${id(10)}');
    insert into public.commercial_colors(id,name,evidence_json,created_by) values ('${id(72)}','Gris','{"source":"isolated fixture"}','${id(10)}');
    insert into public.commercial_sizes(id,name,kind,evidence_json,created_by) values ('${id(73)}','MB','PRESENTATION','{"source":"literal MB"}','${id(10)}');
  `)
  const insert = (overrides = {}) => {
    const v = { product: 61, chain: 1, category: 21, model: 70, color: 72, size: 73,
      version: 1, predecessor: null, status: 'PENDING_CLASSIFICATION', modelState: 'KNOWN',
      colorState: 'KNOWN', sizeState: 'KNOWN', originalModel: 'Nuevo', originalColor: 'Gris', originalSize: 'MB',
      creator: 10, approver: null, at: null, reference: null, snapshot: null, evidence: { source: 'isolated fixture' }, ...overrides }
    const uuid = value => value === null ? 'null' : q(id(value))
    const text = value => value === null ? 'null' : q(value)
    return `insert into public.product_commercial_classifications(chain_id,category_id,product_id,model_id,color_id,size_id,
      model_state,color_state,size_state,original_model,original_color,original_size,classification_status,classification_source,
      classification_version,rules_version,evidence_json,explanation,product_snapshot,mapping_packet_hash,
      created_by,approved_by,approved_at,approval_reference,supersedes_id) values (
      ${uuid(v.chain)},${uuid(v.category)},${uuid(v.product)},${uuid(v.model)},${uuid(v.color)},${uuid(v.size)},
      ${q(v.modelState)},${q(v.colorState)},${q(v.sizeState)},${text(v.originalModel)},${text(v.originalColor)},${text(v.originalSize)},
      ${q(v.status)},'ISOLATED_TEST',${v.version},'test',${q(JSON.stringify(v.evidence))}::jsonb,'Review required',
      ${v.snapshot ? q(v.snapshot) + '::jsonb' : `(select to_jsonb(p) from public.products p where id='${id(v.product)}')`},
      '${'a'.repeat(64)}',${uuid(v.creator)},${uuid(v.approver)},${text(v.at)},${text(v.reference)},${text(v.predecessor)}) returning id`
  }
  await check('scope/category/snapshot cannot be forged', async () => {
    for (const patch of [{ chain: 2 }, { category: 22 }, { category: null }, { snapshot: '{}' }]) await reject(insert(patch), '23514')
  })
  await check('active administrator and explicit approval required', async () => {
    for (const patch of [{ creator: 11 }, { creator: 12 }, { creator: 13 }, { status: 'APPROVED' },
      { status: 'APPROVED', approver: 11, at: '2026-01-01', reference: 'test-only' },
      { status: 'APPROVED', approver: 12, at: '2026-01-01', reference: 'test-only' },
      { status: 'APPROVED', approver: 10, at: '2999-01-01', reference: 'test-only' }]) await reject(insert(patch), '23514')
  })
  await check('unknown is different from not applicable', async () => {
    await reject(insert({ size: null, sizeState: 'NOT_APPLICABLE', originalSize: null,
      status: 'APPROVED', approver: 10, at: '2026-01-01', reference: 'isolated-approval' }), '23514')
    await reject(insert({ size: null, sizeState: 'UNKNOWN', originalSize: null,
      status: 'APPROVED', approver: 10, at: '2026-01-01', reference: 'isolated-approval' }), '23514')
    await reject(insert({ model: 999 }), '23503')
  })
  let revision
  await check('pending revision append and duplicates rejected', async () => {
    revision = (await one(insert())).id
    await reject(insert(), '23514')
    await reject(insert({ version: 3, predecessor: revision }), '23514')
    await reject(insert({ version: 2 }), '23514')
    await reject(`update public.product_commercial_classifications set explanation='Rewrite' where id='${revision}'`, '23514')
    await reject(`delete from public.product_commercial_classifications where id='${revision}'`, '23514')
  })
  await check('reviewed approval and rejection preserve all previous events', async () => {
    const approved = await one(insert({ version: 2, predecessor: revision, status: 'APPROVED', approver: 10,
      at: '2026-01-01', reference: 'isolated-approval', size: null, sizeState: 'NOT_APPLICABLE', originalSize: null,
      evidence: { source: 'isolated fixture', size_not_applicable: 'Explicit fixture-only commercial decision' } }))
    await db.exec(insert({ version: 3, predecessor: approved.id, status: 'REJECTED' }))
    assert.equal((await one('select count(*)::int n from public.product_commercial_classifications')).n, 3)
    assert.equal((await one(`select classification_status from public.product_commercial_classifications_current where product_id='${id(61)}'`)).classification_status, 'REJECTED')
  })
  await db.exec(insert({ product: 62, chain: 2, category: 22, model: 71 }))
  await check('viewer chain isolation and shared color visibility', async () => {
    await as('authenticated', id(11), async () => {
      assert.equal((await one('select count(*)::int n from public.product_commercial_classifications')).n, 3)
      assert.equal((await one('select count(*)::int n from public.product_commercial_classifications_current')).n, 1)
      assert.deepEqual((await db.query('select name from public.commercial_models')).rows, [{ name: 'Nuevo' }])
      assert.deepEqual((await db.query('select name from public.commercial_colors')).rows, [{ name: 'Gris' }])
      await reject('select evidence_json from public.commercial_colors', '42501')
    })
  })
  await check('inactive identity and anon cannot read catalog', async () => {
    await as('authenticated', id(12), async () => {
      assert.equal((await one('select count(*)::int n from public.product_commercial_classifications_current')).n, 0)
      assert.equal((await one('select count(*)::int n from public.commercial_models')).n, 0)
    })
    await as('anon', null, async () => { await reject('select * from public.product_commercial_classifications_current', '42501') })
  })
  await check('admin sees all scopes; dictionary revisions are immutable', async () => {
    await as('authenticated', id(10), async () => { assert.equal((await one('select count(*)::int n from public.product_commercial_classifications_current')).n, 2) })
    await reject("update public.commercial_models set name='Replace'", '23514')
    await reject("insert into public.commercial_models(name,evidence_json,created_by) values ('NUEVO','{\"e\":1}','" + id(10) + "')", '23505')
  })
  await check('new product automatically pending with no fictitious mapping', async () => {
    await db.exec(`insert into public.products(id,chain_id,category_id,product_code,description,first_seen_period,last_seen_period,created_from_batch_id)
      values ('${id(63)}','${id(1)}','${id(21)}','NEW','New real product fixture','2026-01-01','2026-01-01','${id(51)}')`)
    const row = await one(`select * from public.product_commercial_classifications_current where product_id='${id(63)}'`)
    assert.equal(row.classification_status, 'PENDING_CLASSIFICATION')
    assert.equal(row.classification_id, null)
    await db.exec(`delete from public.products where id='${id(63)}'`)
  })
  await check('historical data and products unchanged', async () => {
    const after = await one(`select
      (select jsonb_agg(to_jsonb(p) order by id) from public.products p) products,
      (select jsonb_agg(to_jsonb(b) order by id) from public.import_batches b) imports,
      (select jsonb_agg(to_jsonb(v) order by id) from public.import_profile_versions v) profiles,
      (select count(*) from public.monthly_observations) observations,
      (select count(*) from public.forecast_calculations) calculations,
      (select count(*) from public.forecast_selection_events) selections`)
    assert.deepEqual(after, original)
  })
  await check('changed catalog is pending without rewriting stored revision', async () => {
    await db.exec(`update public.products set description='Changed local fixture' where id='${id(62)}'`)
    const row = await one(`select * from public.product_commercial_classifications_current where product_id='${id(62)}'`)
    assert.equal(row.stale_snapshot, true)
    assert.equal(row.classification_status, 'PENDING_CLASSIFICATION')
    assert.equal(row.stored_classification_status, 'PENDING_CLASSIFICATION')
    await db.exec(`update public.products set description='Toalla MB Nuevo Gris' where id='${id(62)}'`)
    assert.equal((await one(`select stale_snapshot from public.product_commercial_classifications_current where product_id='${id(62)}'`)).stale_snapshot, false)
  })
  await check('rollback refuses deleting commercial evidence', async () => {
    await reject(readFileSync('supabase/rollback/202610140015_commercial_catalog.sql', 'utf8'), 'P0001')
    await db.exec('rollback')
  })
  await check('empty schema rollback leaves original tables intact', async () => {
    // Dispose isolated commercial fixtures, never production data.
    await db.exec('truncate public.product_commercial_classifications,public.commercial_models,public.commercial_colors,public.commercial_sizes')
    await db.exec(readFileSync('supabase/rollback/202610140015_commercial_catalog.sql', 'utf8'))
    assert.equal((await one('select count(*)::int n from public.products')).n, 2)
    assert.equal((await one("select to_regclass('public.product_commercial_classifications') exists")).exists, null)
  })
  process.stdout.write(`Commercial schema ${passed} checks PASS\n`)
} finally { await db.close() }
