"""Native PostgreSQL Gate B only. No URL, remote migration or deployment options."""
from __future__ import annotations
import argparse
import io
import json
import os
import re
import subprocess
import sys
import time
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import psycopg  # noqa: E402
from psycopg import sql  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402
from services.operational_versions.local import REPO, Context, LocalLab, digest, export_exact  # noqa: E402


def save(path, value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf-8')


def private_directory(path):
    path=Path(path).resolve()
    if path.is_relative_to(REPO) or path.exists():
        raise ValueError('output_must_be_new_and_outside_repository')
    path.mkdir(parents=True,exist_ok=False)
    return path


def probe(args):
    if not 1024 <= args.port <= 65535 or not re.fullmatch(r'gateb_[a-z0-9_]{1,40}',args.database) or not re.fullmatch(r'[0-9a-f]{32}',args.lab_id):
        raise ValueError('STOP_B0')
    with psycopg.connect(host='127.0.0.1',hostaddr='127.0.0.1',port=args.port,dbname=args.database,
                         user='postgres',sslmode='disable',connect_timeout=5,autocommit=True,row_factory=dict_row) as c:
        proof=c.execute("""select host(inet_server_addr()) as host,inet_server_port() as port,
            current_database() as database,current_setting('gateb.lab_id',true) as lab_id,
            current_setting('listen_addresses') as listen,current_setting('data_directory') as data_directory,
            (select system_identifier::text from pg_control_system()) as system_identifier""").fetchone()
        if proof['host']!='127.0.0.1' or proof['port']!=args.port or proof['lab_id']!=args.lab_id or proof['listen']!='127.0.0.1':
            raise ValueError('STOP_B0')
        proof.pop('listen')
    with LocalLab(proof).connect(): pass
    output=Path(args.output)
    if output.exists() or output.resolve().is_relative_to(REPO):raise ValueError('private_new_config_required')
    output.parent.mkdir(parents=True,exist_ok=True);save(output,proof)
    return dict(B0='PASS',database=args.database,host='127.0.0.1',port=args.port)


class Results(unittest.TextTestResult):
    def startTest(self,test):
        super().startTest(test);self.started=time.monotonic()
    def record(self,test,status,detail=None):
        self.cases.append(dict(id=test._testMethodName.split('_')[1],name=test._testMethodName,
            status=status,duration_ms=round(1000*(time.monotonic()-self.started),2),detail=detail))
    def addSuccess(self,test): super().addSuccess(test);self.record(test,'PASS')
    def addFailure(self,test,err): super().addFailure(test,err);self.record(test,'FAIL',self._exc_info_to_string(err,test))
    def addError(self,test,err):
        super().addError(test,err)
        if hasattr(test,'_testMethodName'):self.record(test,'ERROR',self._exc_info_to_string(err,test))
    def addSkip(self,test,reason):super().addSkip(test,reason);self.record(test,'SKIP',reason)
    def __init__(self,*args):super().__init__(*args);self.cases=[]


def prepare(args):
    from services.operational_versions.fixtures import A,B,C,seed
    output=private_directory(args.output);lab=LocalLab.from_file(args.config)
    with lab.connect() as c:
        initialized=c.execute("select to_regclass('public.operational_data_versions') as name").fetchone()['name']
    if not initialized:lab.initialize()
    with lab.connect() as c:
        exists=c.execute('select count(*) as n from operational_data_versions where id=%s',(A,)).fetchone()['n']
    if not exists:seed(lab)
    with lab.connect() as c:
        rows=c.execute('''select v.id::text,v.manifest_hash,count(m.observation_id) as observations
            from operational_data_versions v join operational_data_version_observations m on m.version_id=v.id
            where v.id=any(%s::uuid[]) group by v.id order by v.id''',([A,B,C],)).fetchall()
    result=dict(state='SYNTHETIC_CANDIDATES_ONLY',versions=rows,production_writes=0)
    save(output/'preparation.json',result);return result


def test(args):
    output=private_directory(args.output)
    lab=LocalLab.from_file(args.config)
    with lab.connect() as c:
        proof=c.execute("select version() as version,current_database() as db,host(inet_server_addr()) as address").fetchone()
    if args.private_candidate and os.environ.get('GITHUB_ACTIONS')=='true':
        raise ValueError('private_inputs_forbidden_in_ci')
    os.environ['GATEB_CONFIG']=str(Path(args.config).resolve())
    os.environ['GATEB_REPORT_DIR']=str(output)
    os.environ.pop('GATEB_PRIVATE_CANDIDATE',None)
    if args.private_candidate:os.environ['GATEB_PRIVATE_CANDIDATE']=str(Path(args.private_candidate).resolve())
    suite=unittest.defaultTestLoader.loadTestsFromName('services.operational_versions.test_postgres')
    with (output/'tests.log').open('w',encoding='utf-8') as stream:
        result=unittest.TextTestRunner(stream=stream,resultclass=Results,verbosity=2).run(suite)
    summary=dict(total=result.testsRun,passed=sum(r['status']=='PASS' for r in result.cases),
        skipped=len(result.skipped),failures=len(result.failures),errors=len(result.errors),cases=result.cases,
        database_proof=proof,private_inputs=bool(args.private_candidate),production_writes=0,
        acceptance=('LOCAL_SQL_PASS' if result.wasSuccessful() and not result.skipped and result.testsRun==30
                    else 'PRIVATE_CHECK_NOT_RUN' if result.wasSuccessful() else 'STOP'))
    save(output/'results.json',summary)
    if not result.wasSuccessful() or result.testsRun!=30:raise RuntimeError('GATE_B_TEST_FAILURE')
    return {k:v for k,v in summary.items() if k not in ('cases','database_proof')}


def demonstrate(args):
    from services.operational_versions.fixtures import A,B,actor
    output=private_directory(args.output);lab=LocalLab.from_file(args.config)
    with lab.connect() as c:
        actor(c)
        hashes={str(r['id']):r['manifest_hash'] for r in c.execute('select id,manifest_hash from operational_data_versions')}
        start=lab.context(c)
        if start.version_id!=A:lab.activate(c,A,hashes[A],start.version_id,start.generation,str(uuid.uuid4()))
        ca=lab.context(c);a=lab.read(c,ca)
        event_b=lab.activate(c,B,hashes[B],A,ca.generation,str(uuid.uuid4()));cb=lab.context(c);b=lab.read(c,cb)
        pinned=lab.read(c,ca)
        event_a=lab.activate(c,A,hashes[A],B,cb.generation,str(uuid.uuid4()));restored=lab.read(c,lab.context(c))
        if restored!=a or pinned!=a:raise ValueError('RESTORE_MISMATCH')
        save(output/'A_B_A.json',dict(status='PASS',data='SYNTHETIC_ONLY',A_context=vars(ca),B_context=vars(cb),
            A=a,B=b,restored_A=restored,A_hash=digest(a),restored_hash=digest(restored),
            events=[event_b,event_a],B_retained=True))
        export_exact(a,ca,output/'LAB_SINTETICO_A.xlsx');export_exact(b,cb,output/'LAB_SINTETICO_B.xlsx')
    return dict(A=len(a),B=len(b),restored_A=len(restored),ids_and_quantities_identical=True,production_writes=0)


def snapshot(c):
    result={}
    for t in c.execute("select schemaname,tablename from pg_tables where schemaname in ('public','auth','private') order by 1,2"):
        query=sql.SQL("select count(*) as n,encode(sha256(convert_to(coalesce(string_agg(to_jsonb(x)::text,E'\\n' order by to_jsonb(x)::text),''),'UTF8')),'hex') as hash from {}.{} x").format(sql.Identifier(t['schemaname']),sql.Identifier(t['tablename']))
        result[t['schemaname']+'.'+t['tablename']]=c.execute(query).fetchone()
    return result


def recover(args):
    output=private_directory(args.output);lab=LocalLab.from_file(args.config)
    binary=Path(args.postgres_bin).resolve()
    suffix='.exe' if os.name=='nt' else ''
    dump=binary/('pg_dump'+suffix);restore=binary/('pg_restore'+suffix)
    if not dump.is_file() or not restore.is_file():raise ValueError('postgres_tools_missing')
    def dsn(db):return f"host=127.0.0.1 hostaddr=127.0.0.1 port={lab.config['port']} user=postgres dbname={db} sslmode=disable"
    env={k:v for k,v in os.environ.items() if not k.upper().startswith('PG')}
    with lab.connect() as c:
        c.execute("set timezone='UTC'");before=snapshot(c)
        target=lab.config['database']+'_r_'+uuid.uuid4().hex[:6]
        # B0 verified on source; destination is a NEW local-only database in the same marked server.
        c.execute(sql.SQL('create database {}').format(sql.Identifier(target)))
    config={**lab.config,'database':target};destination=LocalLab(config)
    with destination.connect():pass  # Full B0 check before any restore.
    with (output/'backup.log').open('w',encoding='utf-8') as log:
        subprocess.run([str(dump),'--dbname',dsn(lab.config['database']),'-Fc','-f',str(output/'synthetic.dump')],env=env,stdout=log,stderr=log,check=True,timeout=60)
        subprocess.run([str(restore),'--dbname',dsn(target),'--no-owner','--clean','--if-exists','--exit-on-error','--single-transaction',str(output/'synthetic.dump')],env=env,stdout=log,stderr=log,check=True,timeout=60)
    with destination.connect() as c:
        c.execute("set timezone='UTC'");after=snapshot(c)
    if after!=before:raise ValueError('ISOLATED_RECOVERY_MISMATCH')
    save(output/'recovery.json',dict(status='PASS',source=lab.config['database'],destination=target,
        synthetic_only=True,tables=before,all_rows_hashes_identical=True,gate_c_executed=False,production_writes=0))
    return dict(recovery='PASS',tables=len(before),synthetic_only=True,gate_c_executed=False)


def main():
    p=argparse.ArgumentParser(description=__doc__);commands=p.add_subparsers(dest='command',required=True)
    q=commands.add_parser('probe');q.add_argument('--database',required=True);q.add_argument('--lab-id',required=True)
    q.add_argument('--port',type=int,default=65438);q.add_argument('--output',required=True)
    for name in ('prepare','test','demonstrate','recover'):
        q=commands.add_parser(name);q.add_argument('--config',required=True);q.add_argument('--output',required=True)
        if name=='test':q.add_argument('--private-candidate')
        if name=='recover':q.add_argument('--postgres-bin',required=True)
    args=p.parse_args()
    value=globals()[args.command](args)
    print(json.dumps(value,ensure_ascii=False,default=str))


if __name__=='__main__':
    try:main()
    except Exception as error:
        message=str(error)
        print('gateb_failed:'+(message if re.fullmatch(r'[A-Za-z0-9_: ]+',message) else type(error).__name__),file=sys.stderr)
        raise SystemExit(1) from None
