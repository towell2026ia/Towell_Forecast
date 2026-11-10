"""Synthetic SQL fixtures only: no private workbook or production identities."""
import copy
import hashlib
import json
import uuid
from .local import METRICS


def uid(name):
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, 'gateb.synthetic.' + name))


ADMIN, VIEW_A, VIEW_B, EDITOR = (uid(x) for x in ('admin', 'viewer-a', 'viewer-b', 'editor'))
A, B, C = (uid(x) for x in ('release-a', 'release-b', 'release-c'))
CHAINS = [uid('chain-a'), uid('chain-b')]
PERIODS = ['2026-09', '2026-10']
PRODUCTS = {uid(f'product-{i}'): dict(chain=CHAINS[i//2], account=f'ACCOUNT-{i//2}',
    format=f'FORMAT-{i//2}', model='Shared Synthetic Design', presentation='MB',
    commercial_code='SYNTHETIC-GROUP', color=('Blue' if i%2 else 'Red')) for i in range(4)}


def actor(c, user=ADMIN, role='postgres'):
    if role not in ('postgres', 'authenticated', 'anon'):
        raise ValueError('invalid_fixture_role')
    c.execute('reset role')
    c.execute("select set_config('request.jwt.claim.sub',%s,false)", (user or '',))
    c.execute('set role '+role)


def _batch(c, chain, profile, period, label):
    bid=uid(f'batch-{chain}-{period}-{label}')
    c.execute('''insert into import_batches(id,chain_id,profile_version_id,filename,sha256,period,uploaded_by)
        values(%s,%s,%s,%s,%s,%s,%s)''',
        (bid,chain,profile,f'{label}-{period}.csv',hashlib.sha256((chain+period+label).encode()).hexdigest(),period+'-01',ADMIN))
    c.execute("update import_batches set status='VALIDATING',row_count=2,valid_rows=2 where id=%s",(bid,))
    for state in ('VALIDATED','CONFIRMED','IMPORTED'):
        c.execute('update import_batches set status=%s where id=%s',(state,bid))
    return bid


def document(c, ids, name):
    rows=c.execute('''select chain_id::text,product_id::text,to_char(period,'YYYY-MM') as period,metric_code
        from monthly_observations where id=any(%s::uuid[])''',(ids,)).fetchall()
    present={(r['chain_id'],r['product_id'],r['period'],r['metric_code']) for r in rows}
    absent=['/'.join((p['chain'],pid,month,m)) for pid,p in PRODUCTS.items() for month in PERIODS for m in METRICS
            if (p['chain'],pid,month,m) not in present]
    return dict(chains=CHAINS,products=list(PRODUCTS),absences=sorted(absent),exclusions=[{'synthetic':'unmapped SKU retained outside active facts'}],
                policy='gateb-local-global-1',name=name,unit='PIEZAS',grouping=PRODUCTS)


def seed(lab, *, stage_candidates=True, include_prior_year=False, temporal_history=False,
         activate_initial=True, commercial_profiles=False):
    with lab.connect() as c, c.transaction():
        for user,role in ((ADMIN,'ADMIN'),(VIEW_A,'VIEWER'),(VIEW_B,'VIEWER'),(EDITOR,'EDITOR')):
            c.execute('insert into auth.users(id) values(%s)',(user,))
            c.execute("insert into profiles(id,global_role) values(%s,%s)",(user,role))
        actor(c)
        profiles={}
        batches={}
        for i,chain in enumerate(CHAINS):
            c.execute("insert into chains(id,code,name) values(%s,%s,%s)",(chain,f'SYNTHETIC_{i}',f'Synthetic chain {i}'))
            profile=uid('profile-'+chain); pv=uid('profile-version-'+chain)
            c.execute("insert into import_profiles(id,chain_id,name) values(%s,%s,'Synthetic monthly')",(profile,chain))
            mapping = {}
            if commercial_profiles:
                mapping = {"scope": {"level": "COMMERCIAL_UNIT", "actual_bearing": True,
                            "code": f"SYNTHETIC_{i}::SYNTHETIC", "parent_code": f"SYNTHETIC_{i}"},
                           "products": [{"id": product, "identifier_mappings": [["", f"{j:07}"]]}
                                        for j, (product, detail) in enumerate(PRODUCTS.items()) if detail["chain"] == chain]}
            c.execute("insert into import_profile_versions(id,profile_id,chain_id,version,mapping_json,required_columns,created_by) values(%s,%s,%s,1,%s::jsonb,'[]',%s)",
                      (pv,profile,chain,json.dumps(mapping),ADMIN))
            profiles[chain]=pv
            for period in [*PERIODS, *(['2024-10'] if include_prior_year else [])]:
                batches[chain,period]=_batch(c,chain,pv,period,'base')
        for user,chain in ((VIEW_A,CHAINS[0]),(VIEW_B,CHAINS[1]),(EDITOR,CHAINS[0])):
            c.execute('insert into user_chain_access(user_id,chain_id,can_view) values(%s,%s,true)',(user,chain))
        for i,(pid,p) in enumerate(PRODUCTS.items()):
            c.execute('''insert into products(id,chain_id,product_code,description,status,first_seen_period,last_seen_period,created_from_batch_id)
                values(%s,%s,%s,%s,'ACTIVE','2026-09-01','2026-10-01',%s)''',
                (pid,p['chain'],f'00000000000{i}',f'Synthetic {p["color"]}',batches[p['chain'],PERIODS[0]]))
        old=[]
        for i,(pid,p) in enumerate(PRODUCTS.items()):
            for month in PERIODS:
                for metric,value in (('SALES','7.125'),('ORDER','10'),('DELIVERY','8')):
                    if (i==1 and month==PERIODS[1] and metric=='ORDER') or (i==3 and month==PERIODS[1] and metric=='SALES'):continue
                    if i==0 and month==PERIODS[0] and metric=='SALES':
                        value='0'
                        if temporal_history:
                            c.execute("""insert into monthly_observations
                                (id,chain_id,product_id,period,metric_code,value,version_no,
                                 availability_source,available_at,source_batch_id)
                                select %s,chain_id,%s,period,'SALES',6,1,'SYSTEM_INGESTION',uploaded_at,id
                                from import_batches where id=%s""",
                                (uid('known-earlier-sale'),pid,batches[p['chain'],month]))
                    old.append(lab.put(c,chain=p['chain'],product=pid,period=month,metric=metric,value=value,batch=batches[p['chain'],month]))
            if include_prior_year:
                old.append(lab.put(c,chain=p['chain'],product=pid,period='2024-10',metric='SALES',value='20',
                                   batch=batches[p['chain'],'2024-10']))
        if not activate_initial:
            return dict(old_ids=old,batches=batches)
        lab.build(c,A,document(c,old,'A'),old); hash_a=lab.seal(c,A)
        lab.activate(c,A,hash_a,None,0,'synthetic-first-activation')
    if not stage_candidates:
        return dict(hashes={A:hash_a},old_ids=old,batches=batches)
    # Commit A before B is physically present, then prepare B and C as inactive candidates.
    with lab.connect() as c, c.transaction():
        actor(c)
        candidate_batches={(ch,month):_batch(c,ch,profiles[ch],month,'candidate') for ch in CHAINS for month in PERIODS}
        selected={}
        for row in c.execute('''select o.* from monthly_observations o join operational_data_version_observations m
            on m.observation_id=o.id where m.version_id=%s''',(A,)):
            selected[str(row['product_id']),row['period'].strftime('%Y-%m'),row['metric_code']]=str(row['id'])
        for i,(pid,p) in enumerate(PRODUCTS.items()):
            for month in PERIODS:
                for metric,value in (('FCST_TOWELL_HISTORY','11.1234567890123456'),('FCST_CLIENTE_HISTORY','12.00001')):
                    selected[pid,month,metric]=lab.put(c,chain=p['chain'],product=pid,period=month,metric=metric,value=value,batch=candidate_batches[p['chain'],month])
                if month==PERIODS[1] and i in (0,1):
                    selected[pid,month,'ORDER']=lab.put(c,chain=p['chain'],product=pid,period=month,metric='ORDER',value=('20' if i==0 else '0'),batch=candidate_batches[p['chain'],month])
        ids=list(selected.values())
        lab.build(c,B,document(c,ids,'B'),ids,A); hash_b=lab.seal(c,B)
        lab.build(c,C,document(c,ids,'C'),ids,A); hash_c=lab.seal(c,C)
    return dict(hashes={A:hash_a,B:hash_b,C:hash_c},old_ids=old,new_ids=ids,batches=batches,candidate_batches=candidate_batches)
