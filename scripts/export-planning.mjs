// Private snapshots only. Resolve the authoring runtime supplied by the operator;
// the workbook and real data must never be put in the repository or CI logs.
import fs from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { parsePlanning, planningSheets } from '../lib/planning-results.ts';

const [input, output, runtime] = process.argv.slice(2);
if (!input || !output || !runtime) throw new Error('Usage: node --experimental-strip-types scripts/export-planning.mjs input.json output.xlsx bundled-node-root');
await fs.access(output).then(()=>{throw new Error('Refusing to overwrite an existing plan');},()=>{});
const require = createRequire(path.join(path.resolve(runtime),'package.json'));
const { Workbook, SpreadsheetFile } = await import(pathToFileURL(require.resolve('@oai/artifact-tool')).href);
const bundle = parsePlanning(JSON.parse(await fs.readFile(input,'utf8')));
const version = bundle.versions.at(-1);
const sheets = planningSheets(bundle,version);
const workbook = Workbook.create();
const col = n => { let s='';for(;n;n=Math.floor((n-1)/26))s=String.fromCharCode(65+(n-1)%26)+s;return s; };
const literal = v => typeof v === 'string' && /^[=+@]/.test(v) ? "'"+v : v;
for (const spec of sheets) {
  const sheet=workbook.worksheets.add(spec.name), width=spec.headers.length, height=spec.rows.length+5;
  sheet.showGridLines=false;
  sheet.getRange(`A1:${col(width)}${height}`).format.font.name='Aptos';
  sheet.getRange(`A1:${col(width)}${height}`).format.font.size=10;
  sheet.getRange(`A1:${col(width)}${height}`).format.rowHeight=22;
  sheet.getRange(`A1:${col(width)}${height}`).format.columnWidth=18;
  sheet.mergeCells(`A2:${col(Math.min(width,8))}2`);sheet.getRange('A2').values=[[spec.title]];
  sheet.getRange('A2').format.font.size=17;sheet.getRange('A2').format.font.bold=true;
  sheet.mergeCells(`A3:${col(Math.min(width,10))}3`);sheet.getRange('A3').values=[[spec.note]];
  sheet.getRange('A3').format.wrapText=true;sheet.getRange('A3').format.rowHeight=44;
  sheet.getRange(`A5:${col(width)}5`).values=[spec.headers];
  if(spec.rows.length)sheet.getRange(`A6:${col(width)}${height}`).values=spec.rows.map(r=>r.map(literal));
  // Transparent arithmetic from the frozen model outputs; no model is run in Excel.
  if(spec.name==='Metricas_y_cobertura')for(let row=6;row<=height;row++){
    const data=spec.rows[row-6];
    if(data[2]!==null&&data[11]!==null)sheet.getRange(`C${row}:D${row}`).formulas=[[`=IF(F${row}>0,100*L${row}/F${row},"")`,`=IF(F${row}>0,100*M${row}/F${row},"")`]];
  }
  if(spec.name==='Plan_anual_por_modelo')for(let row=6;row<=height;row++)sheet.getRange(`AD${row}`).formulas=[[`=IF(COUNT(F${row}:Q${row})=0,"",SUM(F${row}:Q${row}))`]];
  if(spec.name==='Revisiones_y_reales')for(let row=6;row<=height;row++){
    sheet.getRange(`L${row}:M${row}`).formulas=[[`=IF(COUNT(G${row}:H${row})=2,H${row}-G${row},"")`,`=IF(COUNT(H${row}:I${row})=2,I${row}-H${row},"")`]];
  }
  sheet.getRange(`A5:${col(width)}5`).format.fill='#19384D';
  sheet.getRange(`A5:${col(width)}5`).format.font.color='#FFFFFF';
  sheet.getRange(`A5:${col(width)}5`).format.font.bold=true;
  sheet.getRange(`A5:${col(width)}5`).format.wrapText=true;
  sheet.getRange(`A5:${col(width)}5`).format.rowHeight=42;
  sheet.freezePanes.freezeRows(5);
  if(spec.rows.length)sheet.tables.add(`A5:${col(width)}${height}`,true,'Pilot'+sheets.indexOf(spec));
  for(let j=0;j<width;j++){
    const h=spec.headers[j]; const range=sheet.getRange(`${col(j+1)}6:${col(j+1)}${height}`);
    if(/^(?:Real |Champion |Original |Revisión )?20\d\d-\d\d$|WAPE retrospectivo|Propuesta original|^Piezas$/.test(h))range.setNumberFormat('#,##0.00####');
    if(/Motivo|Interpretación|Evidencia|Descripción|Artículo|Color|Unidad|Referencia|Estado|Cobertura|Anomalías|Empaques/.test(h)) {range.format.columnWidth=38;range.format.wrapText=true;}
  }
  sheet.getRange(`A6:${col(width)}${height}`).format.rowHeight=spec.name==='Historico_por_color'?54:78;
  sheet.getRange(`A1:A${height}`).format.columnWidth=32;
  if(spec.name==='Guia_y_control'){sheet.getRange(`A1:A${height}`).format.columnWidth=38;sheet.getRange(`B1:B${height}`).format.columnWidth=110;sheet.getRange(`B6:B${height}`).format.wrapText=true;sheet.getRange(`A6:B${height}`).format.rowHeight=42;}
}
workbook.recalculate();
const inspected=await workbook.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!|#SPILL!',options:{useRegex:true,maxResults:20},summary:'Formula errors'});
await fs.writeFile(output+'.formula-check.json',JSON.stringify(inspected));
const outputBlob=await SpreadsheetFile.exportXlsx(workbook);
await outputBlob.save(output);
const qa=path.join(path.dirname(output),'revision_visual');await fs.mkdir(qa,{recursive:true});
for(const spec of sheets){
  const preview=await workbook.render({sheetName:spec.name,range:spec.name==='Guia_y_control'?'A1:B17':`A1:${col(Math.min(spec.headers.length,7))}10`,scale:1,format:'png'});
  await fs.writeFile(path.join(qa,spec.name+'.png'),new Uint8Array(await preview.arrayBuffer()));
}
await fs.writeFile(output+'.verification.json',JSON.stringify({version:version.id,hash:version.hash,sheets:sheets.map(s=>({name:s.name,rows:s.rows.length,columns:s.headers.length})),shared_projection:'lib/planning-results.ts'},null,2));
console.log('XLSX exported; private visual checks available.');
