"""Read editable XLSX, then run the same validator as the local browser. No forecasts/network."""
import argparse
import json
import subprocess
import tempfile
from datetime import date, datetime
from pathlib import Path
from openpyxl import load_workbook

HEADERS = ['Fuente','Cuenta','Cadena','Formato','Código de barras','Modelo','Tamaño','Color',
           'Unidad histórica de origen','Factor a piezas','Unidad de destino','Inicio de vigencia',
           'Fin de vigencia','Evidencia documental','Estado','Responsable','Fecha de validación',
           'Conflicto de empaque','Empaque de referencia (no evidencia)','Periodos observados','Identidad']
KEYS = ['source','account','chain','format','barcode','model','size','color','origin_unit','factor',
        'destination_unit','valid_from','valid_through','evidence','status','validated_by','validated_on']

def read_catalog(path, bundle):
    book=load_workbook(path,read_only=True,data_only=False,keep_links=False)
    try:
        sheet=book['Equivalencias']
        if [c.value for c in next(sheet.iter_rows(min_row=5,max_row=5,max_col=len(HEADERS)))]!=HEADERS:
            raise ValueError('Encabezados incompatibles. Usar el catálogo Fase 3A.')
        guide=book['Guia']
        if guide['B7'].value!=bundle['versions'][-1]['id'] or guide['B8'].value!=bundle['versions'][-1]['hash']:
            raise ValueError('El Excel pertenece a otra versión/base')
        rows=[]
        for n,cells in enumerate(sheet.iter_rows(min_row=6,max_col=len(HEADERS)),6):
            if all(c.value is None for c in cells):continue
            if any(c.data_type in ('f','e') for c in cells):raise ValueError(f'Fila {n}: fórmulas/errores no aceptados como evidencia')
            values=[c.value for c in cells]
            if not isinstance(values[4],str):raise ValueError(f'Fila {n}: conservar código de barras como texto')
            row=dict(zip(KEYS,values[:17]))
            row['product_id']=values[20]
            for k in ('valid_from','valid_through','validated_on'):
                if isinstance(row[k],(date,datetime)):row[k]=row[k].strftime('%Y-%m-%d')
            rows.append(row)
        return {'schema':'towell-unit-catalog-1','base_version':guide['B7'].value,'base_hash':guide['B8'].value,'rows':rows}
    finally:book.close()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--catalog',type=Path,required=True);p.add_argument('--base',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--node',default='node')
    a=p.parse_args()
    if a.output.exists():raise ValueError('No sobrescribir un catálogo existente; use otro nombre/version')
    bundle=json.loads(a.base.read_text(encoding='utf-8'))
    catalog=read_catalog(a.catalog,bundle)
    # Private intermediate never enters the checkout. Cleanup even if validation rejects it.
    with tempfile.TemporaryDirectory(prefix='towell-units-') as temp:
        input_path=Path(temp)/'catalog.json';input_path.write_text(json.dumps(catalog,ensure_ascii=False,allow_nan=False),encoding='utf-8')
        subprocess.run([a.node,'--experimental-strip-types',str(Path(__file__).with_name('validate-units.mjs')),
                        str(a.base.resolve()),str(input_path),str(a.output.resolve())],check=True)

if __name__=='__main__':main()
