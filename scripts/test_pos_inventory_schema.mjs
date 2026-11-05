import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { PGlite } from '@electric-sql/pglite'

// Synthetic fixtures in isolated PostgreSQL only; never a production writer.
const db = new PGlite()
const id = n => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`
const q = value => `'${String(value).replaceAll("'", "''")}'`
let passed = 0
const check = async (label, fn) => { await fn(); passed++; process.stdout.write(`Inventory ${label} PASS\n`) }
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
  const before = await one(`select (select count(*) from public.monthly_observations) observations,
    (select count(*) from public.forecast_calculations) calculations,
    (select count(*) from public.forecast_selection_events) selections`)
  const snapshot = (stores="ARRAY['S1','S2']",version=1) => `insert into public.pos_inventory_snapshots
    (id,chain_id,account_code,cutoff_date,version_no,expected_store_codes,source_reference,created_by)
    values ('${id(90+version)}','${id(1)}','ACCOUNT','2026-10-31',${version},${stores},'ISOLATED TEST','${id(10)}')`
  const observation = (product=61,store='S1',quantity='0.125') => `insert into public.pos_inventory_observations
    (snapshot_id,chain_id,product_id,store_code,quantity,source_reference)
    values ('${id(91)}','${id(1)}','${id(product)}','${store}',${quantity},'ISOLATED TEST')`
  await check('inventory starts empty with RLS and no runtime writer',async()=>{
    for(const t of ['pos_inventory_snapshots','pos_inventory_observations']){
      assert.equal((await one(`select count(*)::int n from public.${t}`)).n,0)
      assert.equal((await one(`select relrowsecurity enabled from pg_class where relname='${t}'`)).enabled,true)
      for(const r of ['anon','authenticated','service_role'])for(const verb of ['INSERT','UPDATE','DELETE','TRUNCATE'])assert.equal((await one(`select has_table_privilege('${r}','public.${t}','${verb}') allowed`)).allowed,false)
    }
  })
  await check('reject duplicate or blank expected stores',async()=>{
    await reject(snapshot("ARRAY['S1','S1']"),'23514')
    await reject(snapshot("ARRAY['S1',NULL]"),'23514')
    await reject(snapshot("ARRAY['S1',' ']"),'23514')
  })
  await db.exec(snapshot())
  await check('exact scope and deduplication',async()=>{
    await reject(observation(62),'23503')
    await reject(observation(61,'OUTSIDE'),'23514')
    await reject(observation(61,'S1','-1'),'23514')
    await reject(observation(61,'S1',"'NaN'"),'23514')
    await reject(observation(61,'S1',"'Infinity'"),'23514')
    await db.exec(observation())
    await reject(observation(),'23505')
    await db.exec(observation(61,'S2','0'))
    assert.equal(Number((await one('select sum(quantity) total from public.pos_inventory_observations')).total),0.125)
    await reject(snapshot(),'23505')
  })
  await check('append-only versions preserve originals',async()=>{
    await reject('update public.pos_inventory_observations set quantity=9','23514')
    await reject('delete from public.pos_inventory_snapshots','23514')
    await db.exec(snapshot("ARRAY['S1','S2']",2))
    assert.equal((await one('select count(*)::int n from public.pos_inventory_snapshots')).n,2)
  })
  await check('RLS chain isolation and inactive users',async()=>{
    await as('authenticated',id(11),async()=>assert.equal((await one('select count(*)::int n from public.pos_inventory_observations')).n,2))
    await as('authenticated',id(12),async()=>assert.equal((await one('select count(*)::int n from public.pos_inventory_observations')).n,0))
    await db.exec(`update public.user_chain_access set chain_id='${id(2)}' where user_id='${id(11)}'`)
    await as('authenticated',id(11),async()=>assert.equal((await one('select count(*)::int n from public.pos_inventory_observations')).n,0))
    await as('anon',null,async()=>await reject('select * from public.pos_inventory_snapshots','42501'))
  })
  assert.deepEqual(await one(`select (select count(*) from public.monthly_observations) observations,
    (select count(*) from public.forecast_calculations) calculations,
    (select count(*) from public.forecast_selection_events) selections`),before)
  await check('rollback refuses nonempty evidence and permits empty local schema',async()=>{
    const sql=readFileSync('supabase/rollback/20261105215012_pos_inventory.sql','utf8')
    await reject(sql,'P0001');await db.exec('rollback')
    await db.exec('truncate public.pos_inventory_observations, public.pos_inventory_snapshots')
    await db.exec(sql)
    assert.equal((await one('select count(*)::int n from public.products')).n,2)
  })
  process.stdout.write(`Inventory schema ${passed} checks PASS\n`)
} finally { await db.close() }
