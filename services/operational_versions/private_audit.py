"""Optional private precision audit. Never used with commercial inputs in CI."""
import csv
import hashlib
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

from .local import REPO, quantity
from services.operational_update.preparation import verify_package


def precision_audit(c, candidate, output):
    candidate, output = Path(candidate), Path(output)
    if output.resolve().is_relative_to(REPO) or output.exists():
        raise ValueError('private_report_must_be_new_outside_repository')
    manifest = json.loads((candidate/'Version_candidata.json').read_text(encoding='utf-8'))
    verify_package(candidate, manifest['manifest_hash'])
    source = candidate/'Conciliacion.json'
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    records = [r for r in json.loads(source.read_text(encoding='utf-8'))
               if r['identity_state']=='COINCIDENCIA_VERIFICADA' and r['value_state']=='VALUE'
               and Decimal(r['value']) != Decimal(r['value']).quantize(Decimal('.001'))]
    c.execute('''create temporary table gateb_private_precision(i int primary key,
        dynamic_value numeric check(dynamic_value>=0 and dynamic_value<1e15),
        fixed_value numeric(31,16) check(fixed_value>=0)) on commit drop''')
    report=[]
    for i, r in enumerate(records):
        exact=quantity(r['value'])  # Reject before PostgreSQL can round.
        c.execute('insert into gateb_private_precision values(%s,%s,%s)',(i,exact,exact))
        read=c.execute('select dynamic_value::text,fixed_value::text from gateb_private_precision where i=%s',(i,)).fetchone()
        if any(Decimal(value)!=Decimal(exact) for value in read.values()):
            raise ValueError('STOP_PRECISION')
        d=Decimal(exact)
        report.append({**{k:r[k] for k in ('identity','period','metric','cell','original','value')},
                       'scale':max(0,-d.as_tuple().exponent),'integer_digits':max(1,d.adjusted()+1),
                       'sign':d.as_tuple().sign,'read_numeric':read['dynamic_value'],
                       'read_numeric_31_16':read['fixed_value'],'numeric_status':'PASS','numeric_31_16_status':'PASS'})
    if hashlib.sha256(source.read_bytes()).hexdigest()!=before:
        raise ValueError('private_candidate_changed')
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=('identity','period','metric','cell','original','value','scale',
            'integer_digits','sign','read_numeric','read_numeric_31_16','numeric_status','numeric_31_16_status'))
        writer.writeheader(); writer.writerows(report)
    return dict(total=len(report),by_metric=dict(Counter(r['metric'] for r in records)),
                max_scale=max((-Decimal(r['value']).as_tuple().exponent for r in records),default=0),
                integer_digits=max((max(1,Decimal(r['value']).adjusted()+1) for r in records),default=0),
                alternatives={'numeric':'PASS','numeric(31,16)':'PASS'},source_unchanged=True)
