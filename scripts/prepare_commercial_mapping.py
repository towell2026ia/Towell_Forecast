"""Seal a separate, pending-only commercial load package. Offline; never execute SQL."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.assistant_api.commercial_catalog import analyze, digest  # noqa: E402


def sql_literal(value):
    if value is None:
        return 'NULL'
    if isinstance(value, (dict, list)):
        return "'" + json.dumps(value, ensure_ascii=False, separators=(',', ':')).replace("'", "''") + "'::jsonb"
    return "'" + str(value).replace("'", "''") + "'"


def prepare(catalog, rules):
    packet = analyze(catalog, rules)
    actor = catalog['actor']
    if actor['global_role'] != 'ADMIN' or actor['status'] != 'ACTIVE':
        raise ValueError('Active administrative preparer required')
    products = {p['id']: p for p in catalog['products']}
    dictionaries = {}
    for field, table in [('product_type', 'commercial_product_types'), ('model', 'commercial_models'),
                         ('color', 'commercial_colors'), ('size', 'commercial_sizes')]:
        groups = {}
        for row in packet['rows']:
            name = row['proposed_' + field]
            if name:
                # Match SQL case/whitespace normalization, retaining accents and original labels.
                name_key = re.sub(r'\s+', ' ', name.strip()).upper()
                groups.setdefault(name_key, []).append(row)
        entries = []
        for name_key, rows in sorted(groups.items()):
            proposed_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'towell/commercial/{table}/{name_key}'))
            entry = {'id': proposed_id, 'name': rows[0]['proposed_' + field],
                     'evidence_json': {'classification_status': 'PENDING_CLASSIFICATION',
                                       'rules_version': rules['version'],
                                       'source_product_ids': sorted(r['product_id'] for r in rows),
                                       'original_labels': sorted({r['proposed_' + field] for r in rows}),
                                       'requires_independent_mapping_authorization': True},
                     'created_by': actor['id']}
            if field == 'size':
                entry['kind'] = 'EXPLICIT_DIMENSIONS' if re.fullmatch(
                    r'\d+(?:[.,]\d+)?\s*[X×]\s*\d+(?:[.,]\d+)?\s*(?:CM|MM|M)',
                    name_key) else 'PRESENTATION'
            entries.append(entry)
            for row in rows:
                row[field + '_id'] = proposed_id
        dictionaries[table] = entries
    classifications = []
    for row in packet['rows']:
        for field in ['model', 'color', 'size', 'product_type']:
            row.setdefault(field + '_id', None)
        classifications.append({
            'id': str(uuid.uuid5(uuid.NAMESPACE_URL, f"towell/commercial/classification/{row['product_id']}/1/{digest(row)}")),
            'chain_id': row['chain_id'], 'category_id': row['category_id'], 'product_id': row['product_id'],
            **{f + '_id': row.get(f + '_id') for f in ['model', 'color', 'size', 'product_type']},
            **{f + '_state': row[f + '_state'] for f in ['model', 'color', 'size', 'product_type']},
            'original_model': row['proposed_model'], 'original_color': row['proposed_color'],
            'original_size': row['original_size'], 'original_product_type': row['original_product_type'],
            'product_type_evidence_json': row['product_type_evidence'],
            'classification_status': 'PENDING_CLASSIFICATION', 'classification_source': row['classification_source'],
            'classification_version': 1, 'rules_version': rules['version'],
            'evidence_json': {**row['evidence'], 'product_type': row['product_type_evidence'],
                              'ambiguity_reasons': row['ambiguity_reasons'], 'upc': row['upc'], 'item': row['item']},
            'explanation': '; '.join(row['ambiguity_reasons']), 'product_snapshot': products[row['product_id']],
            'approval_reference': None, 'approved_by': None, 'approved_at': None,
            'created_by': actor['id'], 'supersedes_id': None})
    payload = {'package_version': '09.2E5-01B-A.1', 'authorization': 'MAPPING_NOT_AUTHORIZED',
               'source_catalog_hash': digest(catalog), 'source_integrity': catalog['integrity'],
               'rules_hash': digest(rules), 'dictionaries': dictionaries, 'classifications': classifications}
    # Hash excludes its own value to avoid a circular digest.
    packet_hash = digest(payload)
    for row in classifications:
        row['mapping_packet_hash'] = packet_hash
    payload['mapping_packet_hash'] = packet_hash
    packet['rows'] = [{**r, 'mapping_version': '09.2E5-01B-A.1', 'mapping_packet_hash': packet_hash}
                      for r in packet['rows']]
    packet['mapping_hash'] = digest(packet['rows'])
    return packet, payload


def render_sql(payload):
    q = sql_literal
    integrity = payload['source_integrity']
    tables = list(payload['dictionaries']) + ['product_commercial_classifications']
    # Deliberately defaults to a blocking marker until the package receives separate human authorization.
    lines = ['-- PENDING PACKAGE: NOT EXECUTED. Separate human authorization is required.',
             '-- Replace only the authorization marker after recording explicit approval of this exact package hash.',
             'BEGIN;', "SET LOCAL statement_timeout='90s';", "SET LOCAL lock_timeout='5s';",
             "SET LOCAL TIME ZONE 'UTC';",
             "DO $approval$ BEGIN IF current_setting('towell.mapping_authorization',true) IS DISTINCT FROM " +
             q(payload['mapping_packet_hash']) + " THEN RAISE EXCEPTION 'STOP: independent mapping authorization missing'; END IF; END $approval$;",
             'LOCK TABLE public.products,public.import_batches,public.import_profile_versions IN SHARE MODE;',
             'LOCK TABLE ' + ','.join('public.' + t for t in tables) + ' IN ACCESS EXCLUSIVE MODE;',
             'DO $pre$ BEGIN',
             "IF NOT EXISTS(select 1 from supabase_migrations.schema_migrations where version='202610140015') OR " +
             '(select count(*) from public.products) <> ' + str(len(payload['classifications'])) +
             " THEN RAISE EXCEPTION 'STOP: schema or product scope changed'; END IF;"]
    for table, hash_field in [('products', 'product_hash'), ('import_batches', 'import_batch_hash'),
                              ('import_profile_versions', 'import_profile_hash')]:
        lines.append(f"IF (select encode(sha256(convert_to(string_agg(to_jsonb(t)::text,E'\\n' order by id),'UTF8')),'hex') from public.{table} t) IS DISTINCT FROM {q(integrity[hash_field])} THEN RAISE EXCEPTION 'STOP: {table} snapshot changed'; END IF;")
    lines.append('IF ' + ' OR '.join('EXISTS(select 1 from public.' + t + ')' for t in tables) +
                 " THEN RAISE EXCEPTION 'STOP: mapping is not an empty first load'; END IF; END $pre$;")
    for table, entries in payload['dictionaries'].items():
        for entry in entries:
            lines.append(f"INSERT INTO public.{table} ({','.join(entry)}) VALUES ({','.join(q(v) for v in entry.values())});")
    for row in payload['classifications']:
        lines.append('INSERT INTO public.product_commercial_classifications (' + ','.join(row) +
                     ') VALUES (' + ','.join(q(v) for v in row.values()) + ');')
    lines += ["DO $post$ BEGIN IF (select count(*) from public.product_commercial_classifications) <> " +
              str(len(payload['classifications'])) +
              " OR EXISTS(select 1 from public.product_commercial_classifications where classification_status <> 'PENDING_CLASSIFICATION' or approved_by is not null or approved_at is not null or approval_reference is not null) THEN RAISE EXCEPTION 'STOP: pending-only mapping mismatch'; END IF; END $post$;",
              'COMMIT;']
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--rules', type=Path, default=Path('config/commercial_catalog_rules.json'))
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    raw = json.loads(args.catalog.read_text(encoding='utf-8-sig'))
    catalog = raw[0]['catalog'] if isinstance(raw, list) else raw
    packet, payload = prepare(catalog, json.loads(args.rules.read_text(encoding='utf-8')))
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    for name, data in [('mapping_proposals.json', packet), ('pending_load_payload.json', payload)]:
        (out/name).write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    (out/'pilot_mapping_NOT_AUTHORIZED.sql').write_text(render_sql(payload), encoding='utf-8')
    with (out/'classification_matrix.csv').open('w',encoding='utf-8-sig',newline='') as f:
        columns=list(packet['rows'][0])
        writer=csv.DictWriter(f,fieldnames=columns)
        writer.writeheader()
        for row in packet['rows']:
            values={k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in row.items()}
            writer.writerow({k:"'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v for k,v in values.items()})
    manifest={'mapping_packet_hash':payload['mapping_packet_hash'], 'products':len(payload['classifications']),
              'classification_status':'PENDING_CLASSIFICATION','approved_products':0,
              'hash_contract':'SHA256 canonical JSON of payload excluding top-level mapping_packet_hash and each classification mapping_packet_hash.',
              'dictionary_candidates':{k:len(v) for k,v in payload['dictionaries'].items()},
              'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.iterdir()) if p.is_file() and p.name!='manifest.json'}}
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(manifest,ensure_ascii=False))


if __name__ == '__main__':
    main()
