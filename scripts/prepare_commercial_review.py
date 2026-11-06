"""Prepare private PRD 3A.4 review data from immutable 3A.3 snapshots."""
import argparse
import hashlib
import json
import unicodedata
from collections import Counter
from pathlib import Path

def norm(s):
    return ''.join(c for c in unicodedata.normalize('NFKD', s.upper()) if c.isalnum())

def main():
    a = argparse.ArgumentParser(); a.add_argument('--matrix',type=Path,required=True); a.add_argument('--catalog',type=Path,required=True); a.add_argument('--current',type=Path,required=True); a.add_argument('--out',type=Path,required=True)
    x=a.parse_args(); load=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
    rows=load(x.matrix); snap=load(x.catalog); current=load(x.current)
    assert len(rows)==427 and len({r['id'] for r in rows})==427
    existing={(p[0],p[1]) for p in current['products']}
    by_scope={}
    for profile in snap['profiles']:
        scope=profile['mapping_json']['scope']['code']
        by_scope.setdefault(scope,[]).extend(q for q in profile['mapping_json']['products'] if (q['id'],q['chain_id']) in existing)
    # Only an investigation hint. Private account -> Supabase chain is NOT approved.
    scope_hints={'CL00038':'Suburbia','CL01648':'HEB','CL02262':'Merco'}
    missing=[]; conflicts=[]; review=[]
    for r in rows:
        c=r['candidates'][0] if r['candidate_count']==1 else None
        group='SIN_COINCIDENCIA_UPC' if c is None else 'CANDIDATO_UNICO' if c['format_hint'] else 'REVISAR_CADENA_FORMATO'
        reason='UPC exacto; formato textual coincide; cuenta y atributos requieren aprobación' if group=='CANDIDATO_UNICO' else 'UPC exacto; formato privado difiere del scope Supabase' if c else 'UPC privado ausente de mappings y product_code en el catálogo revisado'
        review.append({'identity':r['id'],'account':r['account'],'chain':r['chain'],'format':r['format'],'upc':r['barcode'],'description':r['description'],'model':r['model'],'presentation':r['size'],'color':r['color'],'commercial_code':r['commercial_code'],'group':group,'candidate_product_id':c['product_id'] if c else None,'candidate_chain_id':c['chain_id'] if c else None,'candidate_scope':c['scope'] if c else None,'candidate_description':c['description'] if c else None,'candidate_item':', '.join(c['items']) if c else None,'match_reason':reason,'decision':'Pendiente','approved_product_id':c['product_id'] if c else None,'approved_chain_id':c['chain_id'] if c else None,'responsible':None,'decision_date':None,'evidence':None,'notes':None})
        if group=='REVISAR_CADENA_FORMATO': conflicts.append({'identity':r['id'],'private_format':r['format'],'candidate_scope':c['scope'],'candidate_product_id':c['product_id'],'cause':'UPC exacto, scope/formato distinto; validar organización comercial'})
        if not c:
            scope=scope_hints.get(r['account']); possible=by_scope.get(scope,[])
            by_desc=[p for p in possible if norm(p['description'])==norm(r['description']) or any(norm(d)==norm(r['description']) for d in p.get('description_aliases',[]))]
            missing.append({'identity':r['id'],'account':r['account'],'format':r['format'],'upc':r['barcode'],'description':r['description'],'scope_heuristico_NO_APROBADO':scope,'same_description_count':len(by_desc),'same_description_product_ids':[p['id'] for p in by_desc],'explanation':'Misma descripción no prueba mismo producto: revisar UPC, cuenta, formato, presentación, color y vigencia.' if by_desc else 'Sin UPC ni descripción exacta dentro del scope heurístico; solicitar evidencia de catálogo y vigencia.'})
    counts=Counter(r['group'] for r in review)
    assert counts=={'CANDIDATO_UNICO':356,'REVISAR_CADENA_FORMATO':22,'SIN_COINCIDENCIA_UPC':49}
    summary={'total':427,'candidatos_upc_unicos':378,'unicos_sin_conflicto_formato':356,'revisar_formato':22,'sin_upc_exacto':49,'sin_upc_por_cuenta':dict(Counter(r['account'] for r in missing)),'sin_upc_con_descripcion_igual':sum(bool(r['same_description_count']) for r in missing),'approved':0,'source_matrix_sha256':hashlib.sha256(x.matrix.read_bytes()).hexdigest(),'catalog_sha256':hashlib.sha256(x.catalog.read_bytes()).hexdigest(),'current_sha256':hashlib.sha256(x.current.read_bytes()).hexdigest()}
    x.out.mkdir(parents=True,exist_ok=True)
    for name,obj in [('matriz_entrada.json',{'summary':summary,'rows':review}),('faltantes_49.json',missing),('conflictos_22.json',conflicts),('resumen.json',summary)]:
        with (x.out/name).open('x',encoding='utf-8') as f:json.dump(obj,f,ensure_ascii=False,indent=2)
    print(summary)
if __name__=='__main__':main()
