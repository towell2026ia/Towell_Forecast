// Preserve every frozen source field. Only add the explicit business declaration.
import fs from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {parsePiecesPlanning,piecesPlanningSheets} from '../lib/planning-results.ts';
const [basePath,confirmationPath,outputPath]=process.argv.slice(2);
if(!basePath||!confirmationPath||!outputPath)throw Error('Usage: node --experimental-strip-types scripts/prepare-pieces-plan.mjs base.json confirmation.json new-output.json');
for(const p of [outputPath,outputPath+'.tables.json'])await fs.access(p).then(()=>{throw Error('Refusing overwrite');},()=>{});
const text=await fs.readFile(basePath,'utf8'),raw=JSON.parse(text),provided=JSON.parse(await fs.readFile(confirmationPath,'utf8'));
const result={...raw,pieces_confirmation:{...provided,source_file_sha256:createHash('sha256').update(text).digest('hex'),base_versions:raw.versions.map(v=>({id:v.id,hash:v.hash}))}};
const parsed=parsePiecesPlanning(result),version=parsed.versions.at(-1);
if(JSON.stringify(result.versions)!==JSON.stringify(raw.versions))throw Error('Frozen versions changed');
const sheets=piecesPlanningSheets(parsed,version);
await fs.writeFile(outputPath,JSON.stringify(result,null,2),{flag:'wx'});
await fs.writeFile(outputPath+'.tables.json',JSON.stringify(sheets),{flag:'wx'});
console.log(JSON.stringify({products:version.products.length,sheets:sheets.length,version:result.pieces_confirmation.presentation_version,forecasts_recalculated:0,source_versions_unchanged:true}));
