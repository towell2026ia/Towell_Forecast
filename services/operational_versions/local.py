"""Guarded PostgreSQL laboratory. Never reads .env or accepts a database URL."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
import weakref
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

METRICS = ('SALES', 'ORDER', 'DELIVERY', 'FCST_TOWELL_HISTORY', 'FCST_CLIENTE_HISTORY')
SOURCE_TO_METRIC = {'Towell': 'FCST_TOWELL_HISTORY', 'Cliente': 'FCST_CLIENTE_HISTORY', 'Pedidos': 'ORDER'}
REPO = Path(__file__).resolve().parents[2]


def quantity(value, scale=16):
    if value is None:
        return None
    if isinstance(value, (bool, float)) or not isinstance(value, (str, int, Decimal)):
        raise ValueError('invalid_exact_quantity')
    try:
        d = Decimal(value)
        with localcontext() as context:
            context.prec = 100
            if not d.is_finite() or d < 0 or d >= Decimal('1e15') or d != d.quantize(Decimal(1).scaleb(-scale)):
                raise ValueError('STOP_PRECISION')
    except InvalidOperation:
        raise ValueError('STOP_PRECISION') from None
    return format(d, 'f')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True)
class Context:
    version_id: str
    manifest_hash: str
    generation: int


class LocalLab:
    def __init__(self, config):
        config = dict(config)
        if ('bskoyqhbgrycpwhydnnr' in json.dumps(config).lower()
                or config.get('host') != '127.0.0.1'
                or not isinstance(config.get('port'), int) or not 1024 <= config['port'] <= 65535
                or not re.fullmatch(r'gateb_[a-z0-9_]{1,40}', config.get('database', ''))
                or not re.fullmatch(r'[0-9a-f]{32}', config.get('lab_id', ''))
                or not str(config.get('system_identifier', '')).isdigit()
                or not config.get('data_directory')
                or set(config) != {'host', 'port', 'database', 'lab_id', 'system_identifier', 'data_directory'}):
            raise ValueError('STOP_B0')
        self.config = config
        self._connections = weakref.WeakSet()

    def checked(self, c):
        if c not in self._connections or c.closed:
            raise ValueError('STOP_B0')

    @classmethod
    def from_file(cls, path):
        return cls(json.loads(Path(path).read_text(encoding='utf-8-sig')))

    def connect(self):
        c = psycopg.connect(host='127.0.0.1', hostaddr='127.0.0.1', port=self.config['port'],
                            dbname=self.config['database'], user='postgres', sslmode='disable',
                            connect_timeout=5, autocommit=True, row_factory=dict_row,
                            options='-c statement_timeout=30000 -c lock_timeout=10000')
        try:
            p = c.execute("""select host(inet_server_addr()) as address,inet_server_port() as port,
                current_database() as db,current_setting('listen_addresses') as listen,
                current_setting('gateb.lab_id',true) as marker,current_setting('data_directory') as directory,
                (select system_identifier::text from pg_control_system()) as system_id,
                current_setting('server_version_num')::int as server_version""").fetchone()
            norm = lambda s: s.replace('\\', '/').rstrip('/').casefold()
            if (p['address'] != '127.0.0.1' or p['port'] != self.config['port'] or p['listen'] != '127.0.0.1'
                    or p['db'] != self.config['database'] or p['marker'] != self.config['lab_id']
                    or p['system_id'] != str(self.config['system_identifier'])
                    or norm(p['directory']) != norm(self.config['data_directory']) or p['server_version'] < 150000):
                raise ValueError('STOP_B0')
            self._connections.add(c)
            return c
        except Exception:
            c.close()
            raise

    def initialize(self):
        """New disposable database only; base migrations remain byte-for-byte intact."""
        with self.connect() as c:
            if c.execute("select to_regclass('public.monthly_observations') as name").fetchone()['name']:
                raise ValueError('refusing_existing_schema')
            with c.transaction():
                c.execute((REPO / 'lab/prd3a8/auth_fixture.sql').read_text())
                for name in ('202609250001_contract_core.sql', '202609250002_contract_forecast.sql',
                             '202609250003_contract_governance.sql', '202609250004_contract_integrity.sql',
                             '202609250005_auth_rls.sql', '202609280001_portal_published_history.sql',
                             '202609280002_portal_history_periods.sql'):
                    c.execute((REPO / 'supabase/migrations' / name).read_text())
                for path in sorted((REPO / 'lab/prd3a8/supabase/migrations').glob('*.sql')):
                    c.execute(path.read_text())

    def put(self, c, *, chain, product, period, metric, value, batch):
        self.checked(c)
        if metric not in METRICS or not re.fullmatch(r'20\d\d-(0[1-9]|1[0-2])', period):
            raise ValueError('invalid_observation_contract')
        value = quantity(value)
        if value is None:
            return None  # Absence is in manifest metadata, not a fictitious zero.
        source = c.execute('select chain_id,period,status from import_batches where id=%s',(batch,)).fetchone()
        if (not source or str(source['chain_id']) != str(chain) or source['period'].strftime('%Y-%m') != period
                or source['status'] not in ('CONFIRMED','IMPORTED')):
            raise ValueError('invalid_source_batch')
        identity = [str(chain), str(product), period, metric]
        new_id = str(uuid.uuid5(uuid.NAMESPACE_URL, digest([identity, value, str(batch)])))
        with c.transaction():
            c.execute('select pg_advisory_xact_lock(hashtextextended(%s,0))', (json.dumps(identity),))
            repeated = c.execute('select id from monthly_observations where id=%s', (new_id,)).fetchone()
            if repeated:
                return str(repeated['id'])
            prior = c.execute('''select id,value,version_no from monthly_observations where chain_id=%s
                and product_id=%s and period=%s and metric_code=%s order by version_no desc limit 1''',
                (chain, product, period + '-01', metric)).fetchone()
            if prior and prior['value'] == Decimal(value):
                return str(prior['id'])
            c.execute('''insert into monthly_observations(id,chain_id,product_id,period,metric_code,value,
                version_no,available_at,availability_source,source_batch_id)
                values(%s,%s,%s,%s,%s,%s,%s,null,'UNKNOWN',%s)''',
                (new_id, chain, product, period+'-01', metric, value, prior['version_no']+1 if prior else 1, batch))
        return new_id

    def put_from_sheet(self, c, *, sheet, **record):
        if sheet not in SOURCE_TO_METRIC or 'metric' in record:
            raise ValueError('unsupported_master_source')
        return self.put(c,metric=SOURCE_TO_METRIC[sheet],**record)

    def build(self, c, version_id, document, observation_ids, base_id=None):
        """Caller supplies the whole explicit selection; no latest-version fallback."""
        self.checked(c)
        ids = sorted(set(str(x) for x in observation_ids if x))
        if len(ids) != len(observation_ids):
            raise ValueError('duplicate_or_missing_observation_id')
        with c.transaction():
            previous = c.execute('select document,base_version_id from operational_data_versions where id=%s', (version_id,)).fetchone()
            if previous:
                saved = [str(x['observation_id']) for x in c.execute('select observation_id from operational_data_version_observations where version_id=%s order by observation_id', (version_id,))]
                if saved != ids or previous['document'] != document or str(previous['base_version_id'] or '') != str(base_id or ''):
                    raise ValueError('version_id_reused_with_different_content')
                return
            c.execute('insert into operational_data_versions(id,base_version_id,document) values(%s,%s,%s::jsonb)',
                      (version_id, base_id, json.dumps(document)))
            for oid in ids:
                result = c.execute('''insert into operational_data_version_observations
                    select %s,chain_id,product_id,period,metric_code,id from monthly_observations where id=%s''', (version_id, oid))
                if result.rowcount != 1:
                    raise ValueError('observation_not_found')

    def seal(self, c, version_id):
        self.checked(c)
        return c.execute('select private.gateb_seal(%s) as hash', (version_id,)).fetchone()['hash']

    def activate(self, c, version_id, manifest_hash, expected_id, generation, request_id, fail=False):
        self.checked(c)
        return c.execute('select private.gateb_activate(%s,%s,%s,%s,%s,%s) as result',
                         (version_id, manifest_hash, expected_id, generation, request_id, fail)).fetchone()['result']

    def context(self, c):
        self.checked(c)
        row = c.execute('''select a.version_id,v.manifest_hash,a.generation from operational_data_active a
            join operational_data_versions v on v.id=a.version_id where a.singleton''').fetchone()
        if not row:
            raise ValueError('no_authorized_active_version')
        return Context(str(row['version_id']), row['manifest_hash'], row['generation'])

    def read(self, c, context):
        self.checked(c)
        return c.execute('''select m.chain_id::text,m.product_id::text,to_char(m.period,'YYYY-MM') as period,
            m.metric_code,o.id::text as observation_id,o.value::text as value,o.version_no,
            o.available_at,o.availability_source from operational_data_version_observations m
            join monthly_observations o on o.id=m.observation_id and o.chain_id=m.chain_id
            where m.version_id=%s order by m.chain_id,m.product_id,m.period,m.metric_code''', (context.version_id,)).fetchall()


def totals(rows, products, metric):
    if metric not in METRICS:
        raise ValueError('unknown_metric')
    output = {}
    seen = set()
    with localcontext() as context:
        context.prec = 100
        for r in rows:
            if r['metric_code'] != metric:
                continue
            identity = (r['chain_id'], r['product_id'], r['period'], metric)
            if identity in seen:
                raise ValueError('double_counted_identity')
            seen.add(identity)
            p = products[r['product_id']]
            key = (r['chain_id'], p['account'], p['format'], p['model'], p['presentation'], p['commercial_code'], r['period'], metric)
            output[key] = output.get(key, Decimal(0)) + Decimal(r['value'])
    return output


def export_exact(rows, context, path):
    """Lab evidence XLSX; original exporters and pilot workbooks are unchanged."""
    from openpyxl import Workbook
    path = Path(path)
    if path.resolve().is_relative_to(REPO) or path.exists():
        raise ValueError('private_output_must_be_new_and_outside_repository')
    book = Workbook()
    sheet = book.active
    sheet.title = 'Cantidades exactas'
    fields = ('observation_id', 'chain_id', 'product_id', 'period', 'metric_code', 'value')
    sheet.append(list(fields))
    for row in rows:
        sheet.append([str(row[k]) for k in fields])
        for cell in sheet[sheet.max_row]:
            cell.number_format = '@'
    meta = book.create_sheet('Trazabilidad')
    for key, value in vars(context).items():
        meta.append([key, str(value)])
    meta.append(['unit', 'PIEZAS'])
    meta.append(['status', 'SYNTHETIC_LAB_NOT_OPERATIONAL'])
    path.parent.mkdir(parents=True, exist_ok=True)
    book.save(path)
