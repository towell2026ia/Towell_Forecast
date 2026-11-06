import tempfile
import unittest
from pathlib import Path
from openpyxl import Workbook
from .commercial_decisions import HEADERS,FIELDS,validate

class CommercialDecisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'review.xlsx'
        self.base=[];self.catalog=[];self.wb=Workbook();self.ws=self.wb.active;self.ws.title='Revision_427';self.wb.create_sheet('Guia_y_fuentes')
        for c,h in enumerate(HEADERS,1):self.ws.cell(5,c,h)
        for i in range(427):
            group='CANDIDATO_UNICO' if i==0 else 'REVISAR_CADENA_FORMATO' if i==1 else 'SIN_COINCIDENCIA_UPC' if i==2 else 'CANDIDATO_UNICO'
            p={'identity':f'A|{i:03}','account':'A','chain':'Chain','format':'BD','upc':f'{i:03}','description':f'Item {i}','model':'Model','presentation':'MB','color':'Blue','commercial_code':'CODE','group':group,'candidate_product_id':f'P{i}' if i!=2 else None,'candidate_chain_id':'C' if i!=2 else None,'candidate_scope':'Walmart::BD','candidate_description':'Source','candidate_item':'','match_reason':'Test'}
            self.base.append(p);self.catalog.append([f'P{i}','C',f'{i:03}','ACTIVE'])
            for c,key in enumerate(FIELDS,1):self.ws.cell(i+6,c,p[key])
            self.ws.cell(i+6,18,'Pendiente')
            self.ws.cell(i+6,19,p['candidate_product_id']);self.ws.cell(i+6,20,p['candidate_chain_id'])
        self.wb.save(self.path)
    def tearDown(self):self.tmp.cleanup()
    def result(self):self.wb.save(self.path);return validate(self.path,self.base,self.catalog)
    def approve(self,row=6,evidence='Source document'):
        for col,value in [(18,'Aprobado'),(21,'Planner'),(22,'2026-11-06'),(23,evidence)]:self.ws.cell(row,col,value)
    def test_initial_all_pending(self):
        r=self.result();self.assertEqual(len(r['pending']),427);self.assertEqual(len(r['approved']),0);self.assertFalse(r['production_load'])
    def test_approved_requires_evidence_and_active_exact_target(self):
        self.approve();self.assertEqual(len(self.result()['approved']),1)
        self.ws.cell(6,23,'');self.assertEqual(self.result()['status'],'BLOCKED')
        self.ws.cell(6,23,'Source document');self.ws.cell(6,19,'OTHER');self.assertEqual(self.result()['status'],'BLOCKED')
        self.ws.cell(6,19,'P0');self.catalog[0][3]='INACTIVE';self.assertEqual(self.result()['status'],'BLOCKED')
    def test_conflict_and_upc_alternative_require_specific_evidence(self):
        self.approve(7);self.assertEqual(self.result()['status'],'BLOCKED')
        self.ws.cell(7,23,'SCOPE_VERIFICADO: signed account map');self.assertEqual(len(self.result()['approved']),1)
        self.approve(8);self.ws.cell(8,19,'P2');self.ws.cell(8,20,'C');self.assertEqual(self.result()['status'],'BLOCKED')
        self.ws.cell(8,23,'UPC_ALTERNATIVO_VERIFICADO: signed UPC alias');self.assertEqual(len(self.result()['approved']),2)
    def test_source_tamper_and_duplicate_target_block_all(self):
        self.ws.cell(6,5,'CHANGED');self.assertEqual(self.result()['status'],'BLOCKED')
        self.ws.cell(6,5,'000');self.approve();self.approve(7,'SCOPE_VERIFICADO: signed')
        self.ws.cell(7,19,'P0');self.ws.cell(7,23,'SCOPE_VERIFICADO: signed; CAMBIO_PRODUCTO_VERIFICADO: signed')
        r=self.result();self.assertEqual(r['approved'],[]);self.assertIn('DUPLICATE_TARGET',[e['reason'] for e in r['errors']])
    def test_rejection_needs_reason_and_date(self):
        self.ws.cell(6,18,'Rechazado');self.ws.cell(6,21,'Planner');self.ws.cell(6,22,'2026-11-06');self.ws.cell(6,23,'Document')
        self.assertEqual(self.result()['status'],'BLOCKED')
        self.ws.cell(6,24,'Different product');self.assertEqual(len(self.result()['rejected']),1)
        self.ws.cell(6,22,'yesterday');self.assertEqual(self.result()['status'],'BLOCKED')
