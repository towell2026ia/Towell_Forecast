"""30 Gate B cases against real guarded PostgreSQL. Missing B0 is an error."""
import copy
import hashlib
import json
import os
import subprocess
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

import psycopg
from openpyxl import load_workbook

from .fixtures import ADMIN, VIEW_A, VIEW_B, EDITOR, A, B, C, CHAINS, PERIODS, PRODUCTS, actor, document, seed, uid
from .local import Context, LocalLab, METRICS, SOURCE_TO_METRIC, REPO, digest, export_exact, quantity, totals
from .private_audit import precision_audit


class GateBPostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path=os.environ.get('GATEB_CONFIG')
        if not path: raise ValueError('STOP_B0: GATEB_CONFIG required')
        cls.lab=LocalLab.from_file(path)
        with cls.lab.connect() as c:
            initialized=c.execute("select to_regclass('public.operational_data_versions') as name").fetchone()['name']
        if not initialized: cls.lab.initialize()
        with cls.lab.connect() as c:
            exists=c.execute('select count(*) as n from operational_data_versions where id=%s',(A,)).fetchone()['n']
        if not exists: seed(cls.lab)
        with cls.lab.connect() as c:
            cls.hashes={str(r['id']):r['manifest_hash'] for r in c.execute('select id,manifest_hash from operational_data_versions where id=any(%s::uuid[])',([A,B,C],))}
            cls.old_ids=sorted(str(r['observation_id']) for r in c.execute('select observation_id from operational_data_version_observations where version_id=%s',(A,)))
            cls.b_ids=sorted(str(r['observation_id']) for r in c.execute('select observation_id from operational_data_version_observations where version_id=%s',(B,)))

    def setUp(self):
        self.c=self.lab.connect(); actor(self.c)
        active=self.lab.context(self.c)
        if active.version_id!=A:
            self.lab.activate(self.c,A,self.hashes[A],active.version_id,active.generation,str(uuid.uuid4()))
        self.c.execute('begin')
        self.ctx=self.lab.context(self.c)

    def tearDown(self):
        self.c.execute('rollback'); self.c.close()

    def rows(self, version=A):
        return self.lab.read(self.c,Context(version,self.hashes[version],self.ctx.generation))

    def activate(self, version=B, fail=False):
        current=self.lab.context(self.c)
        return self.lab.activate(self.c,version,self.hashes[version],current.version_id,current.generation,str(uuid.uuid4()),fail)

    def try_sql(self, query, params=None, error=psycopg.Error):
        with self.assertRaises(error), self.c.transaction():
            self.c.execute(query,params)

    def test_T01_sales(self):
        rows=[r for r in self.rows() if r['metric_code']=='SALES']
        self.assertTrue(rows); self.assertIn(Decimal(0),[Decimal(r['value']) for r in rows])

    def test_T02_order(self):
        self.assertTrue(all(Decimal(r['value'])==10 for r in self.rows() if r['metric_code']=='ORDER'))

    def test_T03_delivery(self):
        self.assertTrue(all(Decimal(r['value'])==8 for r in self.rows() if r['metric_code']=='DELIVERY'))

    def test_T04_fcst_towell(self):
        rows=[r for r in self.rows(B) if r['metric_code']=='FCST_TOWELL_HISTORY']
        self.assertEqual(len(rows),8); self.assertTrue(all(Decimal(r['value'])==Decimal('11.1234567890123456') for r in rows))

    def test_T05_fcst_cliente(self):
        rows=[r for r in self.rows(B) if r['metric_code']=='FCST_CLIENTE_HISTORY']
        self.assertEqual(len(rows),8); self.assertTrue(all(Decimal(r['value'])==Decimal('12.00001') for r in rows))

    def test_T06_unknown_metric(self):
        row=self.rows()[0]
        with self.assertRaisesRegex(ValueError,'invalid_observation_contract'):
            self.lab.put(self.c,chain=row['chain_id'],product=row['product_id'],period=row['period'],metric='BOGUS',value='1',batch=uid('unused'))
        self.try_sql("insert into monthly_observations(id,chain_id,product_id,period,metric_code,value,version_no,availability_source) values(%s,%s,%s,'2026-10-01','BOGUS',1,99,'UNKNOWN')",(str(uuid.uuid4()),row['chain_id'],row['product_id']),psycopg.errors.CheckViolation)

    def test_T07_independent_aggregations(self):
        self.assertEqual(SOURCE_TO_METRIC,{'Towell':'FCST_TOWELL_HISTORY','Cliente':'FCST_CLIENTE_HISTORY','Pedidos':'ORDER'})
        rows=self.rows(B)
        a=totals(rows,PRODUCTS,'SALES'); b=totals(rows,PRODUCTS,'FCST_TOWELL_HISTORY')
        self.assertNotEqual(list(a.values()),list(b.values()))
        self.assertTrue(all(k[-1]=='SALES' for k in a))
        with self.assertRaisesRegex(ValueError,'double_counted'):
            totals(rows+rows,PRODUCTS,'ORDER')

    def test_T08_no_invented_dates(self):
        self.assertTrue(all(r['available_at'] is None and r['availability_source']=='UNKNOWN' for r in self.rows(B)))
        self.assertEqual(self.c.execute('select count(*) as n from monthly_observations_current').fetchone()['n'],0)

    def test_T09_exact_decimal(self):
        self.assertEqual(quantity('1.1234567890123456'),'1.1234567890123456')
        for bad in (True,1.1,'NaN','Infinity','-1','not-a-number'):
            with self.assertRaises(ValueError): quantity(bad)

    def test_T10_scale_and_rejection_before_cast(self):
        with self.assertRaisesRegex(ValueError,'STOP_PRECISION'): quantity('1.12345678901234567')
        self.assertEqual(self.c.execute('select %s::numeric as value',('1.12345678901234567',)).fetchone()['value'],Decimal('1.12345678901234567'))
        row=self.c.execute("select %s::numeric as dynamic,%s::numeric(31,16) as fixed",('1.1234567890123456','1.1234567890123456')).fetchone()
        self.assertEqual(row['dynamic'],row['fixed'])
        with self.assertRaisesRegex(ValueError,'STOP_PRECISION'): quantity('1.0001',scale=3)

    def test_T11_missing_is_not_zero(self):
        rows=self.rows()
        pid=list(PRODUCTS)[1]
        self.assertFalse(any(r['product_id']==pid and r['period']=='2026-10' and r['metric_code']=='ORDER' for r in rows))
        row=next(r for r in self.rows(B) if r['product_id']==pid and r['period']=='2026-10' and r['metric_code']=='ORDER')
        self.assertEqual(Decimal(row['value']),0)
        n=self.c.execute('select count(*) as n from monthly_observations').fetchone()['n']
        self.assertIsNone(self.lab.put(self.c,chain=CHAINS[0],product=pid,period='2026-10',metric='ORDER',value=None,batch=uid('unused')))
        self.assertEqual(self.c.execute('select count(*) as n from monthly_observations').fetchone()['n'],n)

    def test_T12_numeric_bounds(self):
        maximum='999999999999999.9999999999999999'
        self.assertEqual(quantity(maximum),maximum)
        self.assertEqual(self.c.execute('select %s::numeric(31,16) as v',(maximum,)).fetchone()['v'],Decimal(maximum))
        for bad in ('1000000000000000','-0.00001'):
            with self.assertRaisesRegex(ValueError,'STOP_PRECISION'): quantity(bad)

    def test_T13_exact_evidence_roundtrip(self):
        rows=self.rows(B)
        read=json.loads(json.dumps(rows,default=str))
        self.assertEqual([(r['observation_id'],r['value']) for r in rows],[(r['observation_id'],r['value']) for r in read])
        self.assertTrue(all(isinstance(r['value'],str) for r in read))

    def test_T14_private_72_cases(self):
        source=os.environ.get('GATEB_PRIVATE_CANDIDATE')
        output=os.environ.get('GATEB_REPORT_DIR')
        if not source: self.skipTest('Private commercial inputs intentionally absent from CI; not counted as PASS')
        self.assertTrue(output)
        result=precision_audit(self.c,source,Path(output)/'precision_private.csv')
        self.assertEqual(result['total'],72)
        self.assertEqual(result['by_metric'],{'ORDER':28,'FCST_TOWELL_HISTORY':28,'FCST_CLIENTE_HISTORY':16})
        self.assertEqual(result['max_scale'],16)
        (Path(output)/'precision_summary.json').write_text(json.dumps(result,indent=2),encoding='utf-8')

    def test_T15_baseline_A(self):
        self.assertEqual(self.lab.context(self.c).version_id,A)
        self.assertEqual(sorted(r['observation_id'] for r in self.rows()),self.old_ids)

    def test_T16_candidate_physically_present_not_active(self):
        original=self.rows()
        self.assertGreater(self.c.execute('select count(*) as n from monthly_observations').fetchone()['n'],len(original))
        self.assertEqual(self.lab.read(self.c,self.lab.context(self.c)),original)
        # Demonstrate why the old max-version path must not be used operationally.
        legacy={str(r['observation_id']) for r in self.c.execute('select observation_id from portal_monthly_observations_current')}
        self.assertNotEqual(legacy,set(self.old_ids))
        self.assertEqual(self.lab.context(self.c).version_id,A)

    def test_T17_explicit_carry_forward(self):
        a={r['observation_id'] for r in self.rows()}; b={r['observation_id'] for r in self.rows(B)}
        self.assertEqual(len(a-b),1)
        self.assertEqual(len(b),len(set((r['chain_id'],r['product_id'],r['period'],r['metric_code']) for r in self.rows(B))))
        draft=str(uuid.uuid4()); missing=next(iter(a & b)); ids=[oid for oid in self.b_ids if oid!=missing]
        self.lab.build(self.c,draft,document(self.c,ids,'invalid carry'),ids,A)
        with self.assertRaisesRegex(psycopg.Error,'missing_explicit_carry_forward'), self.c.transaction():self.lab.seal(self.c,draft)

    def test_T18_atomic_activation(self):
        self.activate(); ctx=self.lab.context(self.c)
        self.assertEqual(ctx.version_id,B)
        self.assertEqual(sorted(r['observation_id'] for r in self.lab.read(self.c,ctx)),self.b_ids)
        self.assertEqual(ctx.generation,self.ctx.generation+1)

    def test_T19_pinned_read(self):
        pinned=self.lab.context(self.c); before=self.lab.read(self.c,pinned)
        self.activate()
        self.assertEqual(self.lab.read(self.c,pinned),before)
        self.assertNotEqual(self.lab.context(self.c).version_id,pinned.version_id)

    def test_T20_reverse_exactly(self):
        original=self.rows(); initial=digest(original)
        self.activate(B); self.activate(A)
        restored=self.lab.read(self.c,self.lab.context(self.c))
        self.assertEqual(restored,original); self.assertEqual(digest(restored),initial)
        self.assertEqual(self.c.execute('select count(*) as n from operational_data_version_observations where version_id=%s',(B,)).fetchone()['n'],len(self.b_ids))

    def test_T21_B_only_keys_absent_in_A(self):
        def keys(rows):return {(r['chain_id'],r['product_id'],r['period'],r['metric_code']) for r in rows}
        extra=keys(self.rows(B))-keys(self.rows())
        self.assertTrue(extra)
        self.activate(B); self.activate(A)
        self.assertFalse(extra & keys(self.lab.read(self.c,self.lab.context(self.c))))

    def test_T22_transaction_failure(self):
        before=self.lab.context(self.c)
        events=self.c.execute('select count(*) as n from audit_log').fetchone()['n']
        with self.assertRaisesRegex(psycopg.Error,'injected_activation_failure'), self.c.transaction():self.activate(B,True)
        self.assertEqual(self.lab.context(self.c),before)
        self.assertEqual(self.c.execute('select count(*) as n from audit_log').fetchone()['n'],events)

    def test_T23_real_concurrent_connections(self):
        barrier=threading.Barrier(2)
        expected=self.ctx
        def work(target):
            with self.lab.connect() as c:
                actor(c,ADMIN,'authenticated'); barrier.wait(timeout=10)
                try:
                    self.lab.activate(c,target,self.hashes[target],expected.version_id,expected.generation,str(uuid.uuid4()))
                    return 'COMMITTED'
                except psycopg.errors.SerializationFailure:return 'STALE_REJECTED'
        with ThreadPoolExecutor(max_workers=2) as pool:
            result=list(pool.map(work,[B,C]))
        self.assertCountEqual(result,['COMMITTED','STALE_REJECTED'])
        self.assertEqual(self.lab.context(self.c).generation,expected.generation+1)

    def test_T24_idempotency_and_tamper(self):
        self.c.execute("set timezone='Asia/Tokyo'")
        self.assertEqual(self.c.execute('select private.gateb_manifest_hash(%s) as h',(B,)).fetchone()['h'],self.hashes[B])
        request=str(uuid.uuid4()); ctx=self.lab.context(self.c)
        first=self.lab.activate(self.c,B,self.hashes[B],ctx.version_id,ctx.generation,request)
        second=self.lab.activate(self.c,B,self.hashes[B],ctx.version_id,ctx.generation,request)
        self.assertEqual(first['generation'],second['generation']);self.assertTrue(second['already_applied'])
        self.activate(A)
        retry=self.lab.activate(self.c,B,self.hashes[B],ctx.version_id,ctx.generation,request)
        self.assertEqual(retry['active_now'],A)
        with self.assertRaisesRegex(psycopg.Error,'manifest_integrity_failure'),self.c.transaction():
            now=self.lab.context(self.c);self.lab.activate(self.c,B,'0'*64,now.version_id,now.generation,str(uuid.uuid4()))
        self.try_sql("update operational_data_versions set document='{}' where id=%s",(B,))
        self.try_sql('delete from operational_data_version_observations where version_id=%s',(B,))
        doc=self.c.execute('select document from operational_data_versions where id=%s',(B,)).fetchone()['document']
        n=self.c.execute('select count(*) as n from monthly_observations').fetchone()['n']
        self.lab.build(self.c,B,doc,self.b_ids,A)
        self.assertEqual(self.c.execute('select count(*) as n from monthly_observations').fetchone()['n'],n)
        sample=next(r for r in self.rows(B) if r['metric_code']=='FCST_TOWELL_HISTORY')
        batch=self.c.execute('select source_batch_id from monthly_observations where id=%s',(sample['observation_id'],)).fetchone()['source_batch_id']
        reused=self.lab.put_from_sheet(self.c,sheet='Towell',chain=sample['chain_id'],product=sample['product_id'],period=sample['period'],value='11.1234567890123456',batch=batch)
        self.assertEqual(reused,sample['observation_id'])
        self.assertEqual(self.c.execute('select count(*) as n from monthly_observations').fetchone()['n'],n)

    def test_T25_real_RLS(self):
        for user,allowed in ((VIEW_A,CHAINS[0]),(VIEW_B,CHAINS[1])):
            actor(self.c,user,'authenticated')
            rows=self.rows(B)
            self.assertTrue(rows);self.assertEqual({r['chain_id'] for r in rows},{allowed})
            self.try_sql('select document from operational_data_versions',error=psycopg.errors.InsufficientPrivilege)
        actor(self.c,None,'anon')
        self.try_sql('select * from operational_data_version_observations',error=psycopg.errors.InsufficientPrivilege)

    def test_T26_viewer_and_direct_write_denied(self):
        for user in (VIEW_A,VIEW_B,EDITOR):
            actor(self.c,user,'authenticated')
            with self.assertRaises(psycopg.errors.InsufficientPrivilege),self.c.transaction():
                self.lab.activate(self.c,B,self.hashes[B],A,self.ctx.generation,str(uuid.uuid4()))
        actor(self.c,ADMIN,'authenticated')
        self.try_sql('update operational_data_active set generation=0',error=psycopg.errors.InsufficientPrivilege)
        # Authorized procedure still works under the authenticated role, without read bypass.
        self.activate(B)

    def test_T27_scope_and_composite_FK(self):
        row=self.rows()[0]; draft=str(uuid.uuid4())
        self.lab.build(self.c,draft,document(self.c,[],'bad FK'),[])
        wrong=next(ch for ch in CHAINS if ch!=row['chain_id'])
        self.try_sql('insert into operational_data_version_observations values(%s,%s,%s,%s,%s,%s)',
                     (draft,wrong,row['product_id'],row['period']+'-01',row['metric_code'],row['observation_id']),psycopg.errors.ForeignKeyViolation)
        agg=totals(self.rows(B),PRODUCTS,'ORDER')
        self.assertEqual({k[0] for k in agg},set(CHAINS))
        self.assertEqual(len(agg),4)  # Same design/code across two chains stays separate.
        with self.assertRaisesRegex(psycopg.Error,'additional_root_not_allowed'),self.c.transaction():
            self.lab.seal(self.c,draft)
        incomplete=str(uuid.uuid4());doc=document(self.c,self.b_ids,'missing catalog product')
        extra=uid('catalog-product-without-forecast')
        original=self.c.execute('select * from products limit 1').fetchone()
        self.c.execute('''insert into products(id,chain_id,product_code,description,status,first_seen_period,last_seen_period,created_from_batch_id)
            values(%s,%s,'999999999999','Synthetic no observations','ACTIVE',%s,%s,%s)''',
            (extra,original['chain_id'],original['first_seen_period'],original['last_seen_period'],original['created_from_batch_id']))
        self.lab.build(self.c,incomplete,doc,self.b_ids,A)
        with self.assertRaisesRegex(psycopg.Error,'incomplete_global_catalog'),self.c.transaction():
            self.lab.seal(self.c,incomplete)

    def test_T28_protected_sources_and_code(self):
        bad=dict(self.lab.config)
        for field,value in (('host','bskoyqhbgrycpwhydnnr.supabase.co'),('database','postgres'),('lab_id','invalid')):
            with self.subTest(guard=field), self.assertRaisesRegex(ValueError,'STOP_B0'):
                LocalLab({**bad,field:value})
        with self.assertRaisesRegex(ValueError,'STOP_B0'): self.lab.put(object(),chain='',product='',period='',metric='',value='1',batch='')
        prefixes=['app','components','lib','services/statistical_engine','services/ml_engine',
                  'services/ensemble_engine','services/forecast_engine','services/planning_pilot','supabase/migrations']
        gateb='d6486fc03763d87afb6c23025e7c10afa7ae64a8'
        before_gateb=subprocess.check_output(['git','diff','--name-only','3faa061e418ce9bf4355aea227fc170e589b607d',gateb,'--',*prefixes],cwd=REPO,text=True)
        self.assertEqual(before_gateb.strip(),'')
        # Later PRD 3A.9 authorizes only this private pilot adapter and consultation.
        approved_prd3a9={
            'components/forecast/planning-periods.tsx','lib/planning-results.ts','lib/planning-source-audit.ts',
            'services/planning_pilot/engine.py','services/planning_pilot/test_engine.py',
        }
        # Subsequent authorized portal fixes and pinned RS readers. The exact
        # paths keep the other UI files, motors, and production migrations guarded.
        approved_portal_rs={
            'app/api/portal-validation/route.ts','app/commercial-history-view.tsx','app/database-view.tsx',
            'app/forecast-periods-view.tsx','app/forecast-towell-app.tsx','app/operational-engines-view.tsx',
            'app/report-controls.tsx','components/forecast/model-multiselect.tsx',
            'components/forecast/periods-comparison-chart.tsx','lib/commercial-filters.ts',
            'lib/forecast-chart-data.ts','lib/forecast-data.ts','lib/forecast-report-repository.ts',
            'lib/forecast-reporting.ts','lib/operational-periods-xlsx.ts','lib/periods-dashboard.ts',
            'lib/portal-read-diagnostic.ts','lib/supabase/types.ts',
        }
        approved_rs_assistant={
            'app/assistant/assistant-service.ts','app/forecast-assistant.tsx',
            'lib/forecast-preview.ts','lib/rs-assistant.ts',
        }
        # Authorized localhost adapter: development + explicit opt-in + exact
        # loopback URL. All other library files, motors and SQL stay protected.
        approved_localhost={'lib/supabase/client.ts'}
        # Explicitly authorized hybrid Auth boundary: hosted identity with
        # loopback-only data and recovery guards; no motor or SQL changes.
        approved_hybrid_auth={
            'app/portal-auth.tsx','app/update-password/update-password.tsx',
            'lib/supabase/public-config.ts',
        }
        # Authorized integral validation: expose existing quality reads and
        # distinguish stored commercial subsets in the two engine detail views.
        # No statistical implementation or production SQL is authorized here.
        approved_stored_scope_reads={
            'app/operational-quality-view.tsx',
            'components/forecast/ml-engine-detail.tsx',
            'components/forecast/statistical-engine-detail.tsx',
        }
        after_gateb=subprocess.check_output(['git','diff','--name-only',gateb,'--',*prefixes],cwd=REPO,text=True)
        self.assertEqual(set(after_gateb.splitlines()) - approved_prd3a9 - approved_portal_rs - approved_rs_assistant - approved_localhost - approved_hybrid_auth - approved_stored_scope_reads,set())
        private=os.environ.get('GATEB_PRIVATE_CANDIDATE')
        if private:
            identities=json.loads((Path(private)/'Identidades.json').read_text(encoding='utf-8'))
            self.assertEqual(len(identities),427)
            self.assertEqual(len({r['id'] for r in identities}),427)
            self.assertEqual(len({tuple(r['group']) for r in identities}),151)
            files=json.loads((Path(private)/'Protected_files.json').read_text(encoding='utf-8'))
            self.assertEqual(len(files),17)
            self.assertTrue(all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in files.items()))
        before=digest(self.rows()); self.activate(B);self.activate(A);self.assertEqual(digest(self.rows()),before)

    def test_T29_legacy_parser_and_views(self):
        from services.assistant_api.monthly_imports import ImportProfileVersion,parse_monthly
        csv=b'product_code,description,period,order,fcst_towell_history\n001,Synthetic,2026-10,0,3.00001\n'
        profile=ImportProfileVersion('p','c',1,{k:k for k in ('product_code','description','period','order','fcst_towell_history')})
        self.assertEqual(parse_monthly(csv,'synthetic.csv',profile)[0][0].metrics,{'ORDER':'0'})
        self.assertTrue(set(r['metric_code'] for r in self.c.execute('select metric_code from portal_monthly_observations_current'))<=set(METRICS[:3]))

    def test_T30_model_and_exact_XLSX(self):
        rows=self.rows(B); ctx=Context(B,self.hashes[B],self.ctx.generation)
        model=totals(rows,PRODUCTS,'FCST_TOWELL_HISTORY')
        self.assertEqual(sum(model.values()),sum(Decimal(r['value']) for r in rows if r['metric_code']=='FCST_TOWELL_HISTORY'))
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'synthetic.xlsx';export_exact(rows,ctx,path)
            book=load_workbook(path,read_only=True,data_only=True)
            saved=list(book['Cantidades exactas'].values)
            self.assertEqual([(r[0],r[5]) for r in saved[1:]],[(r['observation_id'],r['value']) for r in rows])
            self.assertEqual(dict(book['Trazabilidad'].values)['version_id'],B)
            self.assertTrue(all(isinstance(r[5],str) for r in saved[1:]));book.close()
