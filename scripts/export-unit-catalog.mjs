// Generic authoring script; operator supplies private paths and bundled artifact runtime.
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
const [input,output,runtime]=process.argv.slice(2);
if(!input||!output||!runtime)throw Error('Usage: node --experimental-strip-types scripts/export-unit-catalog.mjs private-base.json output.xlsx bundled-node-root');
await fs.access(output).then(()=>{throw Error('Refusing overwrite');},()=>{});
const require=createRequire(path.join(path.resolve(runtime),'package.json'));
const {Workbook,SpreadsheetFile}=await import(pathToFileURL(require.resolve('@oai/artifact-tool')).href);
const base=JSON.parse(await fs.readFile(input,'utf8'));
if(base.schema!=='towell-planning-1'||!Array.isArray(base.versions)||!base.versions.length)throw Error('Invalid planning base');
const v=base.versions.at(-1);
if(!Array.isArray(v.products)||!v.products.length||new Set(v.products.map(p=>p.id)).size!==v.products.length)throw Error('Invalid or duplicate identities');
const sources=['Towell','Cliente','Pedidos'];
const headers=['Fuente','Cuenta','Cadena','Formato','Código de barras','Modelo','Tamaño','Color','Unidad histórica de origen','Factor a piezas','Unidad de destino','Inicio de vigencia','Fin de vigencia','Evidencia documental','Estado','Responsable','Fecha de validación','Conflicto de empaque','Empaque de referencia (no evidencia)','Periodos observados','Identidad'];
const packs=(p,s)=>[...new Set(Object.values(p.history[s]??{}).map(o=>o.pack_factor).filter(x=>x!==null))].join(' / ');
const rows=v.products.flatMap(p=>sources.map(s=>[s,p.account,p.chain,p.format,p.barcode,p.model,p.size,p.color,null,null,'piezas',null,null,null,'Pendiente',null,null,p.pack_conflict?'Sí':'No',packs(p,s),Object.keys(p.history[s]??{}).length?`${Object.keys(p.history[s]).sort()[0]} a ${Object.keys(p.history[s]).sort().at(-1)}`:'Sin histórico',p.id]));
const conflicts=v.products.filter(p=>p.pack_conflict).map(p=>[p.id,p.chain,p.format,p.barcode,p.model,p.size,p.color,...sources.map(s=>packs(p,s)),'Pendiente de evidencia por fuente']);
const guides=[['Uso','Completar Equivalencias. No cambiar identidad, cuenta, fuente ni atributos originales.'],
 ['Versión base',v.id],['Hash de la base',v.hash],['Fuente','Paquete privado de planeación. Referencias de empaque observadas, no acreditan unidades históricas.'],
 ['Campos amarillos','Unidad, factor positivo, vigencias, evidencia, estado, responsable y fecha. Ninguna equivalencia viene aprobada.'],
 ['Vigencias','Fechas reales de primer y último día de meses completos (yyyy-mm-dd). No se prorratean cantidades mensuales.'],
 ['Más vigencias','Duplicar la fila de la misma fuente/producto y completar intervalos sin superposición. Eliminar plantillas vacías duplicadas.'],
 ['Validado','Requiere documento verificable, unidad de origen, factor, destino piezas, responsable y fecha de validación no futura.'],
 ['Alcance de aprobación','El validador comprueba estructura y consistencia. Planeación debe verificar el documento y la autoridad del responsable.'],
 ['Embalaje','Empaques de Towell, Cliente o Pedidos y fórmulas de propuesta son referencias, nunca sustituyen evidencia de unidad.'],
 ['Pronóstico existente','Solo se convierte si la unidad/factor está acreditada en todo el entrenamiento, corte y mes objetivo. No se altera la cantidad nativa.'],
 ['Importar','Ejecutar scripts/import-unit-catalog.py con --catalog, --base y --output nuevo. Cargar el JSON resultante en Planeación privada.'],
 ['Reversión','Quitar equivalencias en la vista o volver a abrir V01. No hay escrituras productivas ni cambios del plan original.'],
 ['Sin histórico','Conservar la fila pendiente y registrar evidencia cuando exista; no deducir el factor de otras fuentes.']];
const specs=[{name:'Equivalencias',title:'Catálogo de equivalencias de resurtibles',headers,rows},
 {name:'Conflictos',title:'Diferencias de empaque entre fuentes',headers:['Identidad','Cadena','Formato','Código de barras','Modelo','Tamaño','Color','Referencia Towell','Referencia Cliente','Referencia Pedidos','Acción pendiente'],rows:conflicts},
 {name:'Guia',title:'Preparación y validación comercial',headers:['Concepto','Instrucción'],rows:guides}];
const wb=Workbook.create(); const col=n=>{let s='';for(;n;n=Math.floor((n-1)/26))s=String.fromCharCode(65+(n-1)%26)+s;return s;};
const literal=x=>typeof x==='string'&&/^[=+@]/.test(x)?"'"+x:x;
for(const spec of specs){
 const s=wb.worksheets.add(spec.name),last=spec.rows.length+5,end=col(spec.headers.length);
 s.showGridLines=false;s.getRange(`A1:${end}${last}`).format.font.name='Aptos';s.getRange(`A1:${end}${last}`).format.font.size=10;
 s.getRange(`A1:${end}${last}`).format.columnWidth=22;s.getRange(`A1:${end}${last}`).format.rowHeight=34;s.getRange(`A1:${end}${last}`).format.verticalAlignment='center';
 s.getRange('A2').values=[[spec.title]];s.getRange('A2').format.font.size=16;s.getRange('A2').format.font.bold=true;
 s.getRange('A3').values=[[spec.name==='Equivalencias'?`${v.products.length} identidades. ${rows.length} filas iniciales. Unidades y factores por acreditar.`:spec.name==='Conflictos'?`${conflicts.length} identidades. No convertir con referencias de empaque.`:'Completar el catálogo sin cambiar el piloto V01.']];
 s.getRange(`A5:${end}5`).values=[spec.headers];s.getRange(`A6:${end}${last}`).values=spec.rows.map(r=>r.map(literal));
 s.getRange(`A5:${end}5`).format.fill='#19384D';s.getRange(`A5:${end}5`).format.font.color='#FFFFFF';s.getRange(`A5:${end}5`).format.font.bold=true;s.getRange(`A5:${end}5`).format.wrapText=true;s.getRange(`A5:${end}5`).format.rowHeight=46;
 s.getRange(`A6:${end}${last}`).format.wrapText=true;s.freezePanes.freezeRows(5);
 s.tables.add(`A5:${end}${last}`,true,'Units'+spec.name);
 if(spec.name==='Equivalencias'){
  s.getRange(`C6:C${last}`).format.columnWidth=42;s.getRange(`F6:F${last}`).format.columnWidth=32;s.getRange(`U6:U${last}`).format.columnWidth=32;
  s.getRange(`H1:H${last}`).format.columnWidth=38;
  spec.rows.forEach((r,i)=>{s.getRange(`A${i+6}:U${i+6}`).format.rowHeight=Math.max(34,Math.ceil(String(r[7]).length/34)*14+12);});
  s.getRange(`E6:E${last}`).setNumberFormat('@');s.getRange(`J6:J${last}`).setNumberFormat('0.########');s.getRange(`I6:Q${last}`).format.fill='#FFF3CD';
  s.getRange(`L6:M${last}`).setNumberFormat('yyyy-mm-dd');s.getRange(`Q6:Q${last}`).setNumberFormat('yyyy-mm-dd');s.getRange(`N6:N${last}`).format.columnWidth=45;
  s.getRange(`O6:O${last}`).dataValidation={rule:{type:'list',values:['Pendiente','Validado','Rechazado']}};
  s.getRange(`K6:K${last}`).dataValidation={rule:{type:'list',values:['piezas']}};
  s.getRange(`R6:R${last}`).conditionalFormats.add('containsText',{text:'Sí',format:{fill:'#FCE0D8',font:{color:'#9C3020'}}});
  s.getRange('I2').values=[['Validadas']];s.getRange('J2').formulas=[[`=COUNTIF(O6:O${last},"Validado")`]];
  s.getRange('L2').values=[['Pendientes']];s.getRange('M2').formulas=[[`=COUNTIF(O6:O${last},"Pendiente")`]];
 }else if(spec.name==='Guia'){
  s.getRange(`A1:A${last}`).format.columnWidth=29;s.getRange(`B1:B${last}`).format.columnWidth=110;s.getRange(`A6:B${last}`).format.rowHeight=48;
 }else{s.getRange(`A1:A${last}`).format.columnWidth=32;s.getRange(`B1:B${last}`).format.columnWidth=42;s.getRange(`E1:E${last}`).format.columnWidth=32;s.getRange(`D6:D${last}`).setNumberFormat('@');s.getRange(`K1:K${last}`).format.columnWidth=40;}
}
wb.recalculate();
await fs.mkdir(path.dirname(output),{recursive:true});
await (await SpreadsheetFile.exportXlsx(wb)).save(output);
const qa=path.join(path.dirname(output),'revision_visual');await fs.mkdir(qa,{recursive:true});
for(const spec of specs){const ranges=spec.name==='Equivalencias'?['A1:H9','I1:Q9','R5:U9']:spec.name==='Conflictos'?['A1:F9','G5:K9']:['A1:B19'];for(const [i,range]of ranges.entries()){
 const png=await wb.render({sheetName:spec.name,range,scale:1,format:'png'});await fs.writeFile(path.join(qa,`${spec.name}_${i}.png`),new Uint8Array(await png.arrayBuffer()));}}
await fs.writeFile(output+'.manifest.json',JSON.stringify({version:v.id,base_hash:v.hash,identities:v.products.length,rows:rows.length,conflicts:conflicts.length,sheets:specs},null,2),{flag:'wx'});
console.log(JSON.stringify({identities:v.products.length,rows:rows.length,conflicts:conflicts.length}));
