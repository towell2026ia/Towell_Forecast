import fs from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {parsePlanning} from '../lib/planning-results.ts';
import {validateUnitCatalog,unitView} from '../lib/unit-equivalences.ts';
const [basePath,inputPath,outputPath]=process.argv.slice(2);
if(!basePath||!inputPath||!outputPath)throw Error('Usage: node --experimental-strip-types scripts/validate-units.mjs base.json catalog.json new-output.json');
const baseText=await fs.readFile(basePath,'utf8');
const base=parsePlanning(JSON.parse(baseText));
const catalog=validateUnitCatalog(JSON.parse(await fs.readFile(inputPath,'utf8')),base.versions.at(-1));
const view=unitView(base,catalog); // Verify multiplication ranges before accepting the catalog.
const report={rows:catalog.rows.length,identities:base.versions.at(-1).products.length,
 validated:catalog.rows.filter(r=>r.status==='Validado').length,pending:catalog.rows.filter(r=>r.status==='Pendiente').length,
 convertedForecasts:view.versions.at(-1).products.reduce((n,p)=>n+p.projection.filter(x=>x.pieces!==null).length,0),
 base_sha256:createHash('sha256').update(baseText).digest('hex'),no_forecasts_recalculated:true};
await fs.writeFile(outputPath,JSON.stringify(catalog,null,2),{flag:'wx'});
console.log(JSON.stringify(report));
