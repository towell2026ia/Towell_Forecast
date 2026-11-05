// Offline presentation version only. Frozen forecasts and previous files are not edited.
import fs from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {registerHooks} from 'node:module';
registerHooks({resolve(s,c,next){return next(s==='./planning-results'?'./planning-results.ts':s,c);}});
const {parsePiecesPlanning}=await import('../lib/planning-results.ts');
const {operationalPlanningSheets}=await import('../lib/planning-operations.ts');
const [input,output]=process.argv.slice(2);
if(!input||!output)throw Error('Usage (Node 24): node scripts/prepare-operational-plan.mjs V02.json NEW-V03.json');
for(const p of [output,output+'.tables.json'])await fs.access(p).then(()=>{throw Error('Refusing overwrite');},()=>{});
const text=await fs.readFile(input,'utf8'),raw=JSON.parse(text),parsed=parsePiecesPlanning(raw);
const result={...raw,operational_presentation:{prd:'3A.2',version:'V03',created_at:new Date().toISOString(),base_sha256:createHash('sha256').update(text).digest('hex'),inventory:'STRUCTURE_ONLY_NO_DATA',forecast_client:'NO_ACCREDITED_SOURCE',delivery:'NO_ACCREDITED_SOURCE'}};
const sheets=operationalPlanningSheets(parsed,parsed.versions.at(-1));
await fs.writeFile(output,JSON.stringify(result,null,2),{flag:'wx'});
await fs.writeFile(output+'.tables.json',JSON.stringify(sheets),{flag:'wx'});
console.log(JSON.stringify({products:parsed.versions.at(-1).products.length,sheets:sheets.length,forecast_recalculations:0,inventory_observations:0,frozen_versions_unchanged:JSON.stringify(result.versions)===JSON.stringify(raw.versions)}));
