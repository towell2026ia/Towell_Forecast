// Artifact authoring uses only the bundled runtime; business tables are prepared separately.
import fs from 'node:fs/promises';
import {createRequire} from 'node:module';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const [input,output,runtime]=process.argv.slice(2);
if(!input||!output||!runtime)throw Error('Usage: node scripts/export-pieces-plan.mjs tables.json output.xlsx bundled-node-root');
await fs.access(output).then(()=>{throw Error('Refusing overwrite');},()=>{});
const require=createRequire(path.join(path.resolve(runtime),'package.json'));
const {Workbook,SpreadsheetFile}=await import(pathToFileURL(require.resolve('@oai/artifact-tool')).href);
const sheets=JSON.parse(await fs.readFile(input,'utf8')),wb=Workbook.create();
const col=n=>{let s='';for(;n;n=Math.floor((n-1)/26))s=String.fromCharCode(65+(n-1)%26)+s;return s;};
const literal=v=>typeof v==='string'&&/^[=+@]/.test(v)?"'"+v:v;
for(const [index,spec] of sheets.entries()){
 const s=wb.worksheets.add(spec.name),last=spec.rows.length+5,end=col(spec.headers.length);
 s.showGridLines=false;s.getRange(`A1:${end}${last}`).format.font.name='Aptos';s.getRange(`A1:${end}${last}`).format.font.size=10;
 s.getRange(`A1:${end}${last}`).format.columnWidth=20;s.getRange(`A1:${end}${last}`).format.rowHeight=52;s.getRange(`A1:${end}${last}`).format.verticalAlignment='center';
 s.mergeCells(`A2:${col(Math.min(7,spec.headers.length))}2`);s.getRange('A2').values=[[spec.title]];s.getRange('A2').format.font.bold=true;s.getRange('A2').format.font.size=17;
 s.mergeCells(`A3:${col(Math.min(7,spec.headers.length))}3`);s.getRange('A3').values=[[spec.note]];s.getRange('A3').format.wrapText=true;s.getRange('A3').format.rowHeight=46;
 s.getRange(`A5:${end}5`).values=[spec.headers];s.getRange(`A5:${end}5`).format.fill='#19384D';s.getRange(`A5:${end}5`).format.font.color='#FFFFFF';s.getRange(`A5:${end}5`).format.font.bold=true;s.getRange(`A5:${end}5`).format.wrapText=true;
 if(spec.rows.length){s.getRange(`A6:${end}${last}`).values=spec.rows.map(r=>r.map(literal));s.tables.add(`A5:${end}${last}`,true,'Pieces'+index);}
 s.freezePanes.freezeRows(5);
 spec.headers.forEach((h,j)=>{
   const r=s.getRange(`${col(j+1)}6:${col(j+1)}${last}`);
   if(/^(20\d\d-\d\d|WAPE|Plan original|Revisión$|Venta Towell|Pedido Towell|Cliente$|Cambio vs|Total parcial)/.test(h))r.setNumberFormat('#,##0.00####');
   if(/Identidad|Cuenta|Barras|Código|Hash/.test(h))r.setNumberFormat('@');
   if(h==='Barras')r.setNumberFormat('0;0;0;@');
   if(/Identidad|Cadena|Modelo|Descripción|Color|Motivo|Hash|Estado|Código|Celdas/.test(h)){r.format.columnWidth=/Celdas/.test(h)?70:34;r.format.wrapText=true;}
   if(/Emisión/.test(h)){r.setNumberFormat('yyyy-mm-dd hh:mm:ss.000 "UTC"');r.format.columnWidth=34;}
 });
 if(spec.name==='Guia_y_control'){
   s.getRange(`A1:A${last}`).format.columnWidth=36;s.getRange(`B1:B${last}`).format.columnWidth=115;s.getRange(`A6:B${last}`).format.wrapText=true;
   spec.rows.forEach((row,i)=>{if(/confirmación$|Emisión original/.test(String(row[0])))s.getRange(`B${i+6}`).setNumberFormat('yyyy-mm-dd hh:mm:ss.000 "UTC"');});
 }
 if(spec.name==='Plan_anual_por_modelo'){
   const total=spec.headers.indexOf('Total parcial anual')+1;
   for(let r=6;r<=last;r++)s.getRange(`${col(total)}${r}`).formulas=[[`=IF(COUNT(H${r}:S${r})=0,"",SUM(H${r}:S${r}))`]];
 }
 if(spec.name==='Trazabilidad')s.getRange(`A6:${end}${last}`).format.rowHeight=310;
}
wb.recalculate();await fs.mkdir(path.dirname(output),{recursive:true});
await(await SpreadsheetFile.exportXlsx(wb)).save(output);
const qa=path.join(path.dirname(output),'revision_visual');await fs.mkdir(qa,{recursive:true});
for(const spec of sheets){const last=spec.name==='Guia_y_control'?17:spec.name==='Trazabilidad'?6:9;
 const ranges=spec.name==='Guia_y_control'?['A1:B17']:[`A1:${col(Math.min(spec.headers.length,9))}${last}`];
 if(spec.name==='Pronostico_provisional')ranges.push('J5:P9');if(spec.name==='Plan_anual_por_modelo')ranges.push('H5:S9');
 for(const [i,range]of ranges.entries()){const img=await wb.render({sheetName:spec.name,range,format:'png',scale:1});await fs.writeFile(path.join(qa,`${spec.name}_${i}.png`),new Uint8Array(await img.arrayBuffer()));}}
await fs.writeFile(output+'.verification.json',JSON.stringify({sheets:sheets.map(s=>({name:s.name,rows:s.rows.length,columns:s.headers.length})),shared_tables:'lib/planning-results.ts:piecesPlanningSheets'},null,2),{flag:'wx'});
console.log(JSON.stringify({sheets:sheets.length,exported:true}));
