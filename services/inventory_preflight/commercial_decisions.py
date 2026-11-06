"""Read-only XLSX decision validator. Produces local draft mappings, never DB writes."""
from __future__ import annotations
from datetime import date,datetime
from pathlib import Path
from openpyxl import load_workbook

HEADERS=['Identidad privada','Cuenta','Cadena privada','Formato privado','UPC privado','Descripción original','Modelo','Presentación','Color / surtido','Código comercial','Grupo de revisión','Producto candidato UUID','Cadena candidata UUID','Scope candidato','Descripción candidata','ITEM candidato','Motivo correspondencia','Dictamen','Producto aprobado UUID','Cadena aprobada UUID','Responsable','Fecha dictamen','Evidencia verificable','Observaciones']
FIELDS=['identity','account','chain','format','upc','description','model','presentation','color','commercial_code','group','candidate_product_id','candidate_chain_id','candidate_scope','candidate_description','candidate_item','match_reason']

def validate(path:Path, baseline:list, products:list):
    wb=load_workbook(path,read_only=True,data_only=True)
    if wb.sheetnames!=['Revision_427','Guia_y_fuentes']:
        raise ValueError('HOJAS_INCOMPATIBLES')
    ws=wb['Revision_427']
    material=list(ws.iter_rows(min_row=5,max_row=432,max_col=24,values_only=True))
    if list(material[0])!=HEADERS or ws.max_row!=432 or len(baseline)!=427:
        raise ValueError('ESTRUCTURA_O_COBERTURA_INCOMPATIBLE')
    catalog={(p[0],p[1]):p for p in products}
    approved=[]; rejected=[]; pending=[]; errors=[]; targets=set()
    for idx,original in enumerate(baseline,6):
        values=list(material[idx-5])
        immutable={key:values[j] for j,key in enumerate(FIELDS)}
        blank=lambda v:None if v=='' else v
        if {k:blank(v) for k,v in immutable.items()}!={key:blank(original.get(key)) for key in FIELDS}:
            errors.append({'row':idx,'identity':original['identity'],'reason':'SOURCE_FIELDS_CHANGED'});continue
        status=values[17]
        if status not in ('Aprobado','Rechazado','Pendiente'):
            errors.append({'row':idx,'identity':original['identity'],'reason':'INVALID_DECISION'});continue
        target,chain,owner,when,evidence,note=values[18:24]
        record={'identity':original['identity'],'decision':status,'product_id':target,'chain_id':chain,'responsible':owner,'decision_date':when,'evidence':evidence,'notes':note,'group':original['group']}
        if status=='Pendiente':
            if owner or when or evidence:
                errors.append({'row':idx,'identity':original['identity'],'reason':'PENDING_WITH_DECISION_METADATA'})
            pending.append(record);continue
        if not all(isinstance(v,str) and v.strip() for v in (owner,evidence)) or not isinstance(when,(str,date)):
            errors.append({'row':idx,'identity':original['identity'],'reason':'MISSING_DECISION_EVIDENCE'});continue
        try:
            timestamp=when.date().isoformat() if isinstance(when,datetime) else when.isoformat() if isinstance(when,date) else when
            date.fromisoformat(timestamp)
        except ValueError:
            errors.append({'row':idx,'identity':original['identity'],'reason':'INVALID_DATE'});continue
        record['decision_date']=timestamp
        if status=='Rechazado':
            if not isinstance(note,str) or not note.strip():errors.append({'row':idx,'identity':original['identity'],'reason':'REJECTION_REASON_REQUIRED'})
            rejected.append(record);continue
        if not isinstance(target,str) or not isinstance(chain,str) or (target,chain) not in catalog or catalog[target,chain][3]!='ACTIVE':
            errors.append({'row':idx,'identity':original['identity'],'reason':'TARGET_NOT_ACTIVE_OR_ABSENT'});continue
        if (target,chain) in targets:
            errors.append({'row':idx,'identity':original['identity'],'reason':'DUPLICATE_TARGET'});continue
        targets.add((target,chain))
        if original['group']=='SIN_COINCIDENCIA_UPC' and 'UPC_ALTERNATIVO_VERIFICADO:' not in evidence:
            errors.append({'row':idx,'identity':original['identity'],'reason':'UPC_ALTERNATIVE_EVIDENCE_REQUIRED'});continue
        if original['group']=='REVISAR_CADENA_FORMATO' and 'SCOPE_VERIFICADO:' not in evidence:
            errors.append({'row':idx,'identity':original['identity'],'reason':'SCOPE_EVIDENCE_REQUIRED'});continue
        if (target!=original['candidate_product_id'] or chain!=original['candidate_chain_id']) and original['group']!='SIN_COINCIDENCIA_UPC' and 'CAMBIO_PRODUCTO_VERIFICADO:' not in evidence:
            errors.append({'row':idx,'identity':original['identity'],'reason':'CHANGED_TARGET_EVIDENCE_REQUIRED'});continue
        approved.append(record)
    wb.close()
    if errors:return {'status':'BLOCKED','errors':errors,'approved':[],'rejected':rejected,'pending':pending,'production_load':False}
    return {'status':'READY_FOR_COMMERCIAL_REVIEW','errors':[],'approved':approved,'rejected':rejected,'pending':pending,'production_load':False}
